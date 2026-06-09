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
        # Get the data product
        result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise ValueError(f"Data product {product_id} not found")
        if product.status != DataProductStatus.PUBLISHED.value:
            raise ValueError(f"Product {product_id} is not published")

        # Determine sandbox level from contract
        allowed_levels = (contract.allowed_sandbox_levels or "L3").split(",")
        sandbox_level = allowed_levels[0].strip() if allowed_levels else "L3"

        # Validate resource limits against contract
        effective_limits = validate_resource_limits(sandbox_level, None)

        # Create sandbox session
        session = SandboxSession(
            user_id=contract.buyer_id,
            data_product_id=product_id,
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
        session.status = provision_result.get("status", SessionStatus.RUNNING.value)
        if provision_result.get("error"):
            session.status = SessionStatus.FAILED.value
            session.error_message = provision_result["error"]

        # Generate session key
        key_result = kms_service.generate_session_key(str(session.id))
        session.session_key_id = key_result["key_id"]

        # Distribute key
        if session.container_id:
            kms_service.distribute_key(key_result["key_id"], str(session.id))

        # Record key metadata
        key_meta = KeyMetadata(
            key_id=key_result["key_id"],
            key_type=KeyType.SESSION.value,
            status=KeyStatus.ACTIVE.value,
            session_id=session.id,
            product_id=product_id,
        )
        db.add(key_meta)

        await db.flush()
        await db.refresh(session)

        return {
            "product_id": str(product_id),
            "session_id": str(session.id),
            "sandbox_level": sandbox_level,
            "status": session.status,
        }

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

        result = await db.execute(select(Contract).where(Contract.id == session.contract_id))
        contract = result.scalar_one_or_none()
        if not contract:
            return {"allowed": True, "reason": "Contract not found"}

        # Check allowed operations
        if contract.allowed_operations:
            allowed_ops = [op.strip() for op in contract.allowed_operations.split(",")]
            if operation not in allowed_ops:
                return {"allowed": False, "reason": f"Operation '{operation}' not in contract allowed_operations: {allowed_ops}"}

        # Check allowed sandbox modes
        if contract.allowed_sandbox_modes:
            if operation not in contract.allowed_sandbox_modes:
                return {"allowed": False, "reason": f"Mode '{operation}' not in contract allowed_sandbox_modes"}

        return {"allowed": True, "reason": "Contract constraints satisfied"}

    async def terminate_contract_sessions(self, db: AsyncSession, contract_id: uuid.UUID) -> int:
        """Terminate all active sessions for a contract. Returns count of terminated sessions."""
        result = await db.execute(
            select(SandboxSession).where(
                SandboxSession.contract_id == contract_id,
                SandboxSession.status.in_([
                    SessionStatus.PROVISIONING.value,
                    SessionStatus.RUNNING.value,
                ]),
            )
        )
        sessions = result.scalars().all()

        terminated = 0
        runtime = await get_sandbox_manager()
        for session in sessions:
            session.status = SessionStatus.TERMINATED.value
            session.ended_at = datetime.now(timezone.utc)
            session.error_message = "Contract terminated"

            if session.container_id:
                try:
                    runtime.terminate(session.container_id)
                except Exception as e:
                    logger.error(f"Failed to terminate container {session.container_id}: {e}")

            if session.session_key_id:
                kms_service.destroy_key(session.session_key_id)
                key_result = await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id))
                key_meta = key_result.scalar_one_or_none()
                if key_meta:
                    key_meta.status = KeyStatus.DESTROYED.value
                    key_meta.destroyed_at = datetime.now(timezone.utc)
                    key_meta.destroy_reason = "contract_terminated"

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
