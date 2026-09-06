"""Contract Engine — manages contract lifecycle and policy compilation."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct, DataProductStatus
from app.models.user import User
from app.services.policy_compiler import policy_compiler
from app.services.crypto_service import crypto_service
from app.services.dp_budget import dp_budget_ledger
from app.services.blockchain_service import blockchain_service
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)


class ContractService:
    """Contract lifecycle management."""

    def _generate_contract_no(self) -> str:
        import time
        return f"CDS-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"

    # Valid state transitions
    _TRANSITIONS = {
        ContractStatus.DRAFT.value: {ContractStatus.NEGOTIATING.value, ContractStatus.TERMINATED.value},
        ContractStatus.NEGOTIATING.value: {ContractStatus.SIGNED.value, ContractStatus.TERMINATED.value},
        ContractStatus.SIGNED.value: {ContractStatus.ACTIVE.value, ContractStatus.TERMINATED.value},
        ContractStatus.ACTIVE.value: {ContractStatus.SUSPENDED.value, ContractStatus.COMPLETED.value, ContractStatus.BREACHED.value, ContractStatus.VIOLATED.value, ContractStatus.TERMINATED.value},
        ContractStatus.SUSPENDED.value: {ContractStatus.ACTIVE.value, ContractStatus.TERMINATED.value},
        ContractStatus.BREACHED.value: {ContractStatus.ACTIVE.value, ContractStatus.TERMINATED.value, ContractStatus.ARCHIVED.value},
        ContractStatus.VIOLATED.value: {ContractStatus.TERMINATED.value, ContractStatus.ARCHIVED.value},
        ContractStatus.COMPLETED.value: {ContractStatus.ARCHIVED.value},
        ContractStatus.TERMINATED.value: {ContractStatus.ARCHIVED.value},
    }

    def _validate_transition(self, current: str, target: str) -> None:
        allowed = self._TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(f"Invalid transition: {current} → {target}")

    async def create(self, db: AsyncSession, provider_id: uuid.UUID, **kwargs) -> Contract:
        """Create a new contract in draft status."""
        contract = Contract(
            contract_no=self._generate_contract_no(),
            provider_id=provider_id,
            status=ContractStatus.DRAFT.value,
            **kwargs,
        )
        db.add(contract)
        await db.flush()
        await db.refresh(contract)
        return contract

    async def get_by_id(self, db: AsyncSession, contract_id: uuid.UUID) -> Contract | None:
        result = await db.execute(select(Contract).where(Contract.id == contract_id))
        return result.scalar_one_or_none()

    async def validate_activation_ready(self, db: AsyncSession, contract: Contract) -> None:
        """Validate that contract products can be used before the contract becomes active."""
        if not contract.product_ids:
            raise ValueError("Contract has no data products")
        for raw_product_id in contract.product_ids:
            try:
                product_id = uuid.UUID(str(raw_product_id))
            except (TypeError, ValueError):
                raise ValueError(f"Invalid product id in contract: {raw_product_id}")
            product = await db.get(DataProduct, product_id)
            if not product:
                raise ValueError(f"Product {raw_product_id} not found")
            if product.provider_id != contract.provider_id:
                raise ValueError(f"Product {raw_product_id} is not owned by the contract provider")
            if product.status != DataProductStatus.PUBLISHED.value:
                raise ValueError(f"Product {raw_product_id} is not published")

    async def list_by_user(self, db: AsyncSession, user_id: uuid.UUID, skip: int = 0, limit: int = 20) -> list[Contract]:
        query = (
            select(Contract)
            .where((Contract.provider_id == user_id) | (Contract.buyer_id == user_id))
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def sign(self, db: AsyncSession, contract_id: uuid.UUID, user_id: uuid.UUID, signature: str) -> Contract:
        """Sign a contract with SM2 signature verification.

        Both parties must sign for the contract to become active.
        Signature is verified against the user's SM2 public key from their certificate.
        """
        contract = await self.get_by_id(db, contract_id)
        if not contract:
            raise ValueError("Contract not found")
        if contract.status not in (ContractStatus.DRAFT.value, ContractStatus.NEGOTIATING.value):
            raise ValueError(f"Cannot sign contract in {contract.status} status")

        # Verify SM2 signature — reject demo-signature, require real SM2
        user = await db.execute(select(User).where(User.id == user_id))
        user_obj = user.scalar_one_or_none()
        if not user_obj:
            raise ValueError("User not found")

        party_role = "provider" if user_id == contract.provider_id else "buyer"
        sign_data = crypto_service.contract_sign_data(
            str(contract.id), contract.contract_no, party_role,
            datetime.now(timezone.utc).isoformat(),
            purpose=contract.purpose,
            purpose_scope=contract.purpose_scope,
        )

        if not signature or signature == "demo-signature":
            raise ValueError("Real SM2 signature required — demo signatures not accepted")

        # Verify SM2 signature — certificate required
        if not user_obj.sm2_certificate:
            raise ValueError(f"User {user_id} has no SM2 certificate — cannot verify signature")
        try:
            public_key = user_obj.sm2_certificate
            valid = crypto_service.verify(sign_data, signature, public_key)
            if not valid:
                raise ValueError("SM2 signature verification failed")
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"SM2 verification error for user {user_id}: {e}")
            raise ValueError(f"SM2 signature verification error: {e}")

        now = datetime.now(timezone.utc)
        if user_id == contract.provider_id:
            contract.provider_signature = signature
            contract.provider_signed_at = now
        elif user_id == contract.buyer_id:
            contract.buyer_signature = signature
            contract.buyer_signed_at = now
        else:
            raise ValueError("User is not a party to this contract")

        # Check if both have signed
        if contract.provider_signature and contract.buyer_signature:
            await self.validate_activation_ready(db, contract)
            contract.status = ContractStatus.ACTIVE.value
            # Gap A1: pick up the validity deadline from contract terms so the
            # lifecycle sweep can auto-terminate the contract when it lapses.
            self._apply_valid_until_from_terms(contract)
            # Platform witness signature
            contract.platform_signature = self._platform_witness_sign(contract)
            contract.platform_signed_at = datetime.now(timezone.utc)

            # Allocate DP budget from contract terms
            if contract.dp_epsilon_budget and float(contract.dp_epsilon_budget) > 0:
                await dp_budget_ledger.allocate(db, str(contract.id), float(contract.dp_epsilon_budget))
                logger.info(f"DP budget allocated: ε={contract.dp_epsilon_budget} for contract {contract.contract_no}")

            # Anchor contract hash to blockchain (P1-7)
            contract_hash = crypto_service.sm3_hash(
                f"{contract.id}:{contract.contract_no}:{contract.provider_id}:{contract.buyer_id}".encode()
            )
            attestation = blockchain_service.anchor_to_blockchain(
                contract_hash,
                metadata={"contract_id": str(contract.id), "contract_no": contract.contract_no},
            )
            if attestation.success:
                contract.blockchain_tx_hash = attestation.tx_hash
                logger.info(f"Contract {contract.contract_no} anchored to blockchain: {attestation.tx_hash}")
            else:
                logger.warning(f"Blockchain anchoring failed for contract {contract.contract_no}: {attestation.error}")
        else:
            contract.status = ContractStatus.NEGOTIATING.value

        await db.flush()
        await db.refresh(contract)
        return contract

    async def terminate(self, db: AsyncSession, contract_id: uuid.UUID, user_id: uuid.UUID) -> Contract:
        """Terminate a contract and cascade-reclaim everything it granted (gap A1).

        Cascade: active sandbox sessions (container/key/network-policy/quota
        via the lifecycle terminator), policy bundles (DB revocation + OPA
        removal), plus an audit entry. Previously this only flipped the status
        flag — running buyer sessions kept operating after termination.
        """
        contract = await self.get_by_id(db, contract_id)
        if not contract:
            raise ValueError("Contract not found")
        if user_id not in (contract.provider_id, contract.buyer_id):
            raise ValueError("User is not a party to this contract")

        contract.status = ContractStatus.TERMINATED.value

        # Cascade 1: terminate active sessions under this contract
        terminated_sessions = 0
        try:
            from app.services.contract_fulfillment import contract_fulfillment
            terminated_sessions = await contract_fulfillment.terminate_contract_sessions(db, contract_id)
        except Exception as e:
            logger.error(f"[Contract] Session cascade failed for {contract_id}: {e}")

        # Cascade 2: revoke policy bundles (DB + OPA)
        revoked_bundles = await self._revoke_policy_bundles(db, contract)

        await audit_service.log(
            db, action="contract.terminate_cascade", resource_type="contract",
            user_id=user_id, resource_id=str(contract_id),
            detail={
                "sessions_terminated": terminated_sessions,
                "policy_bundles_revoked": revoked_bundles,
            },
        )

        await db.flush()
        await db.refresh(contract)
        return contract

    async def _revoke_policy_bundles(self, db: AsyncSession, contract: Contract) -> int:
        """Revoke compiled policy bundles for a contract (DB + OPA)."""
        from app.models.policy_bundle import PolicyBundle
        from app.services.opa_client import opa_client

        result = await db.execute(select(PolicyBundle).where(PolicyBundle.contract_id == contract.id))
        bundles = result.scalars().all()
        revoked = 0
        for bundle in bundles:
            bundle.revoked_at = datetime.now(timezone.utc)
            try:
                await opa_client.delete_policy(bundle.policy_id)
            except Exception as e:
                logger.warning(f"[Contract] OPA policy deletion failed for {bundle.policy_id}: {e}")
            revoked += 1
        return revoked

    async def terminate_expired_contracts(self, db: AsyncSession) -> int:
        """Auto-terminate ACTIVE contracts past their valid_until (gap A1).

        Called by the session lifecycle sweep. Returns the number of
        contracts terminated.
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Contract).where(
                Contract.status == ContractStatus.ACTIVE.value,
                Contract.valid_until.isnot(None),
                Contract.valid_until < now,
            )
        )
        count = 0
        for contract in result.scalars().all():
            try:
                await self.terminate(db, contract.id, contract.provider_id)
                count += 1
                logger.info(
                    "[Contract] Contract %s auto-terminated (valid_until=%s)",
                    contract.contract_no, contract.valid_until.isoformat(),
                )
            except Exception as e:
                logger.error(f"[Contract] Auto-termination failed for {contract.id}: {e}")
        return count

    def _apply_valid_until_from_terms(self, contract: Contract) -> None:
        """Populate contract.valid_until from terms['valid_until'] when present.

        Accepts an ISO-8601 datetime string. Malformed values are logged and
        ignored (contract stays open-ended until updated).
        """
        if contract.valid_until:
            return
        terms = contract.terms or {}
        raw = terms.get("valid_until") if isinstance(terms, dict) else None
        if not raw:
            return
        try:
            contract.valid_until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            logger.warning(
                "[Contract] Ignoring malformed terms.valid_until for %s: %r",
                contract.contract_no, raw,
            )

    def _platform_witness_sign(self, contract: Contract) -> str:
        """Generate platform witness SM2 signature for a contract (P0-2: HSM-first)."""
        sign_data = f"witness:{contract.contract_no}:{contract.provider_signature}:{contract.buyer_signature}"
        try:
            if ContractService._hsm_signing_key_id is None:
                try:
                    _, key_id = crypto_service.generate_hsm_signing_keypair("contract-witness")
                    ContractService._hsm_signing_key_id = key_id
                except Exception:
                    if ContractService._platform_keypair is None:
                        ContractService._platform_keypair = crypto_service.generate_keypair()
                    ContractService._hsm_signing_key_id = "__software__"

            if ContractService._hsm_signing_key_id == "__software__":
                kp = ContractService._platform_keypair
                sig = crypto_service.sign(sign_data.encode(), kp.private_key, kp.public_key)
                return sig.signature
            else:
                sig = crypto_service.sign_with_hsm(sign_data.encode(), ContractService._hsm_signing_key_id)
                return sig.signature
        except Exception as e:
            logger.warning(f"Platform witness signing failed: {e}")
            return ""

    _platform_keypair = None
    _hsm_signing_key_id = None

    def compile_policy(self, contract: Contract) -> dict:
        """Compile contract terms into an executable policy bundle (OPA Rego format)."""
        contract_dict = {
            "contract_id": str(contract.id),
            "allowed_sandbox_levels": (contract.allowed_sandbox_levels or "L3").split(","),
            "allowed_operations": (contract.allowed_operations or "read").split(","),
            "max_duration_seconds": contract.max_duration_hours * 3600,
            "dp_epsilon_budget": float(contract.dp_epsilon_budget) if contract.dp_epsilon_budget else None,
            "max_output_rows": contract.max_output_rows or 10000,
            "allowed_output_formats": (contract.allowed_output_formats or "csv,json").split(","),
            "inspection_rule_set": contract.inspection_rule_set,
            "status": contract.status,
        }
        bundle = policy_compiler.compile(contract_dict)
        return {
            **contract_dict,
            "rego_source": bundle.rego_source,
            "sm3_hash": bundle.sm3_hash,
        }


contract_service = ContractService()
