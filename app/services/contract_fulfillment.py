"""Contract Auto-Fulfillment — end-to-end data product delivery.

When a buyer signs a contract:
1. Verify all contract terms are met
2. Provision sandbox sessions for each contracted product
3. Generate session keys via KMS
4. Link sandbox to data resource
5. Notify buyer that products are ready

When a session is used:
6. Enforce contract constraints (operations, output limits, DP budget)
7. Log all operations to audit trail
8. Track DP epsilon consumption

When contract ends:
9. Terminate all active sessions
10. Destroy all session keys
11. Generate compliance report
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct, DataProductStatus
from app.models.sandbox_session import SandboxSession, SessionStatus, SandboxLevel
from app.models.kms import KeyMetadata, KeyType, KeyStatus
from app.core.errors import CDSError
from app.services import capability_matrix
from app.services.audit_service import audit_service
from app.services.kms_service import kms_service
from app.services.sandbox_manager import get_sandbox_manager, validate_resource_limits
from app.services.quota_manager import quota_manager

logger = logging.getLogger(__name__)


class ContractFulfillmentService:
    """Handles automatic contract fulfillment and lifecycle management."""

    async def fulfill_contract(self, db: AsyncSession, contract_id: uuid.UUID) -> dict:
        """Auto-fulfill a signed contract by provisioning sandbox sessions.

        Called when a contract transitions to 'active' state.
        Returns a summary of provisioned sessions.
        """
        result = await db.execute(select(Contract).where(Contract.id == contract_id))
        contract = result.scalar_one_or_none()
        if not contract:
            raise ValueError(f"Contract {contract_id} not found")
        if contract.status != ContractStatus.ACTIVE.value:
            raise ValueError(f"Contract must be active to fulfill, current: {contract.status}")

        provisioned = []
        errors = []

        for product_id in contract.product_ids:
            try:
                session_info = await self._provision_product_session(db, contract, product_id)
                provisioned.append(session_info)
            except Exception as e:
                logger.error(f"Failed to provision session for product {product_id}: {e}")
                errors.append({"product_id": str(product_id), "error": str(e)})

        # Audit the fulfillment
        await audit_service.log(
            db, action="contract.fulfill", resource_type="contract",
            user_id=contract.buyer_id, resource_id=str(contract.id),
            detail={
                "provisioned": len(provisioned),
                "errors": len(errors),
                "product_ids": [str(pid) for pid in contract.product_ids],
            },
        )

        return {
            "contract_id": str(contract_id),
            "status": "fulfilled" if not errors else "partial",
            "provisioned_sessions": provisioned,
            "errors": errors,
        }

    async def _provision_product_session(
        self, db: AsyncSession, contract: Contract, product_id: uuid.UUID
    ) -> dict:
        """Provision a sandbox session for a single contracted product."""
        try:
            product_uuid = product_id if isinstance(product_id, uuid.UUID) else uuid.UUID(str(product_id))
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid product id in contract: {product_id}") from e

        # Get the data product
        result = await db.execute(select(DataProduct).where(DataProduct.id == product_uuid))
        product = result.scalar_one_or_none()
        if not product:
            raise ValueError(f"Data product {product_uuid} not found")
        if product.status != DataProductStatus.PUBLISHED.value:
            raise ValueError(f"Product {product_uuid} is not published")

        # Determine sandbox level from contract
        allowed_levels = (contract.allowed_sandbox_levels or "L3").split(",")
        sandbox_level = allowed_levels[0].strip() if allowed_levels else "L3"

        # A55c（A55/A55b 的自动履约入口）：这里建出来的会话装载的就是合约供数，选档
        # 必须过同一条平台隔离下限。判据只在调用时读权威
        # ``capability_matrix.can_carry_supply_data``（本文件不另抄一份档位阶梯或做字面量
        # 比较；权威增删一档，本处跟着动）。违规复用已登记码 LEGACY_LEVEL_RETIRED：
        # 不静默升档，也不因「合约只放行弱档」就放行；拒绝排在会话落库、配额初始化、
        # runtime.provision 与密钥下发之前。
        contract_levels = [
            lv.strip() for lv in (contract.allowed_sandbox_levels or "").split(",") if lv.strip()
        ]
        if not capability_matrix.can_carry_supply_data(sandbox_level):
            reason = (
                "level_deprecated"
                if capability_matrix.LEVEL_METADATA.get(sandbox_level, {}).get("deprecated")
                else "below_supply_data_floor"
            )
            raise CDSError(
                f"Contract fulfillment may not provision a supply-data session at "
                f"'{sandbox_level}': the supply-data isolation floor is a platform invariant "
                "and is never relaxed by contract data "
                "(SS-13: refuse, never silently run at another level)",
                code="LEGACY_LEVEL_RETIRED",
                http_status=400,
                detail={
                    "sandbox_level": sandbox_level,
                    "reason": reason,
                    "supply_data_levels": list(capability_matrix.SUPPLY_DATA_LEVELS),
                    "contract_allowed_sandbox_levels": contract_levels,
                },
            )

        # Validate resource limits against contract
        effective_limits = validate_resource_limits(sandbox_level, None)

        # Create sandbox session
        session = SandboxSession(
            user_id=contract.buyer_id,
            data_product_id=product_uuid,
            sandbox_level=sandbox_level,
            contract_id=str(contract.id),
            timeout_seconds=contract.max_duration_hours * 3600,
            resource_limits=effective_limits,
            status=SessionStatus.PROVISIONING.value,
        )
        db.add(session)
        await db.flush()

        # Initialize session quota counters (P1-4)
        await quota_manager.reset(session.id)

        # Provision sandbox container
        runtime = await get_sandbox_manager()
        provision_result = runtime.provision(
            session_id=session.id,
            level=sandbox_level,
            data_path="",
            timeout=session.timeout_seconds,
        )
        session.container_id = provision_result.get("container_id")
        session.status = provision_result.get("status", SessionStatus.PROVISIONING.value)
        if provision_result.get("error") or not session.container_id or session.status == SessionStatus.FAILED.value:
            session.status = SessionStatus.FAILED.value
            session.error_message = provision_result.get("error") or "Sandbox provision failed"
            await db.flush()
            raise RuntimeError(session.error_message)

        # Everything from here on runs with a live runtime side effect (the
        # container) that is NOT part of this DB transaction: any exception has
        # to undo that side effect, otherwise the outer rollback deletes the only
        # row pointing at the container while it keeps running with its data
        # channel injected (A147).
        key_id: str | None = None
        try:
            # Generate session key
            key_result = kms_service.generate_session_key(str(session.id))
            key_id = key_result["key_id"]
            session.session_key_id = key_id

            # Distribute key (gap B1: pass the provision attestation when the
            # runtime produced one — KMS rejects unattested distribution when
            # CDS_KMS_REQUIRE_ATTESTATION is enabled)
            from app.services.sandbox_manager import attestation_from_provision
            distributed_key = kms_service.distribute_key(
                key_id,
                str(session.id),
                attestation=attestation_from_provision(provision_result),
            )
            if not distributed_key:
                raise RuntimeError("Session key distribution failed")

            # Record key metadata (gap B3: persist the wrapped blob for restart
            # recovery — crypto-erased on destroy by the lifecycle terminator)
            key_meta = KeyMetadata(
                key_id=key_id,
                key_type=KeyType.SESSION.value,
                status=KeyStatus.ACTIVE.value,
                session_id=session.id,
                product_id=product_uuid,
                wrapped_payload=kms_service.export_wrapped(key_id),
                sm2_encrypted_payload=kms_service.export_sm2_ciphertext(key_id),
            )
            db.add(key_meta)

            await db.flush()
            await db.refresh(session)
        except Exception as exc:
            raise RuntimeError(
                await self._rollback_provision_side_effects(db, runtime, session, key_id, exc)
            ) from exc

        return {
            "product_id": str(product_uuid),
            "session_id": str(session.id),
            "sandbox_level": sandbox_level,
            "status": session.status,
        }

    async def _rollback_provision_side_effects(
        self, db: AsyncSession, runtime, session: SandboxSession, key_id: str | None, cause: Exception
    ) -> str:
        """Undo the runtime side effects of a half-provisioned session (A147).

        Returns the message to surface: the cause first, then every cleanup
        problem — a container this path could NOT stop must be named, not
        swallowed, because the FAILED row may not survive the caller's rollback.
        """
        problems: list[str] = []

        if key_id:
            try:
                kms_service.destroy_key(key_id)
            except Exception as e:
                problems.append(f"session key {key_id} not destroyed: {e}")
            session.session_key_id = None

        if session.container_id:
            try:
                if not runtime.terminate(session.container_id):
                    problems.append(
                        f"runtime terminate returned false for container {session.container_id}"
                    )
            except Exception as e:
                problems.append(
                    f"runtime terminate failed for container {session.container_id}: {e}"
                )

        message = str(cause) or type(cause).__name__
        if problems:
            message = f"{message}; cleanup: {'; '.join(problems)}"
            logger.error(
                "Provision rollback incomplete for session %s (container %s): %s",
                session.id,
                session.container_id,
                "; ".join(problems),
            )

        session.status = SessionStatus.FAILED.value
        session.error_message = message
        try:
            await db.flush()
        except Exception as e:
            logger.error("Could not persist FAILED for session %s: %s", session.id, e)
        return message

    async def check_contract_constraints(
        self, db: AsyncSession, session_id: uuid.UUID, operation: str
    ) -> dict:
        """Check if an operation is allowed by the contract.

        Returns {allowed: bool, reason: str}.
        """
        result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return {"allowed": False, "reason": "Session not found"}
        if not session.contract_id:
            return {"allowed": True, "reason": "No contract constraints"}

        try:
            contract_uuid = uuid.UUID(str(session.contract_id))
        except (TypeError, ValueError):
            return {"allowed": False, "reason": "Invalid bound contract id"}

        result = await db.execute(select(Contract).where(Contract.id == contract_uuid))
        contract = result.scalar_one_or_none()
        if not contract:
            return {"allowed": False, "reason": "Bound contract not found"}
        if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value):
            return {"allowed": False, "reason": f"Contract is {contract.status}"}
        if str(session.data_product_id) not in [str(pid) for pid in contract.product_ids]:
            return {"allowed": False, "reason": "Session product is not covered by bound contract"}

        # Check allowed operations
        if contract.allowed_operations:
            allowed_ops = [op.strip() for op in contract.allowed_operations.split(",")]
            if operation not in allowed_ops:
                return {"allowed": False, "reason": f"Operation '{operation}' not in contract allowed_operations: {allowed_ops}"}

        # Check allowed sandbox modes
        if contract.allowed_sandbox_modes:
            if session.sandbox_mode not in contract.allowed_sandbox_modes:
                return {"allowed": False, "reason": f"Mode '{session.sandbox_mode}' not in contract allowed_sandbox_modes"}

        return {"allowed": True, "reason": "Contract constraints satisfied"}

    async def terminate_contract_sessions(self, db: AsyncSession, contract_id: uuid.UUID) -> int:
        """Terminate all active sessions for a contract. Returns count of terminated sessions.

        Gap A1: delegates to the single termination primitive in
        session_lifecycle so container/key/network-policy/quota/tasks are all
        released — not just the status flag.
        """
        from app.services.session_lifecycle import terminate_session, ACTIVE_SESSION_STATUSES

        result = await db.execute(
            select(SandboxSession).where(
                SandboxSession.contract_id == str(contract_id),
                SandboxSession.status.in_(ACTIVE_SESSION_STATUSES),
            )
        )
        sessions = result.scalars().all()

        terminated = 0
        for session in sessions:
            if await terminate_session(session, db, reason=f"contract_terminated:{contract_id}"):
                terminated += 1

        await db.flush()

        await audit_service.log(
            db, action="contract.terminate_sessions", resource_type="contract",
            user_id=None, resource_id=str(contract_id),
            detail={"sessions_terminated": terminated},
        )

        return terminated


# Singleton
contract_fulfillment = ContractFulfillmentService()
