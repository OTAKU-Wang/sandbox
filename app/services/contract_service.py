"""Contract Engine — manages contract lifecycle and policy compilation."""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.models.user import User
from app.services.policy_compiler import policy_compiler
from app.services.crypto_service import crypto_service
from app.services.dp_budget import dp_budget_ledger
from app.services.blockchain_service import blockchain_service

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
            datetime.now(timezone.utc).isoformat()
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
            contract.status = ContractStatus.ACTIVE.value
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
        contract = await self.get_by_id(db, contract_id)
        if not contract:
            raise ValueError("Contract not found")
        if user_id not in (contract.provider_id, contract.buyer_id):
            raise ValueError("User is not a party to this contract")

        contract.status = ContractStatus.TERMINATED.value
        await db.flush()
        await db.refresh(contract)
        return contract

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
