import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.high_risk_operation import HighRiskOperationRequest
from app.models.data_product import DataProduct
from app.schemas.contract import ContractCreate, ContractSign, ContractResponse
from app.services.contract_service import contract_service
from app.services.audit_service import audit_service
from app.services.contract_fulfillment import contract_fulfillment
from app.services.dp_budget import dp_budget_ledger
from app.models.contract import Contract
from sqlalchemy import select, func

router = APIRouter()


def _can_view_all_contracts(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


async def _validate_contract_create(
    db: AsyncSession,
    body: ContractCreate,
    current_user: User,
) -> None:
    if current_user.role != UserRole.DATA_PROVIDER:
        raise HTTPException(status_code=403, detail="Only data providers can create contracts")
    if body.buyer_id == current_user.id:
        raise HTTPException(status_code=400, detail="Buyer and provider must be different users")

    buyer = await db.get(User, body.buyer_id)
    if not buyer:
        raise HTTPException(status_code=404, detail="Buyer not found")
    if buyer.role != UserRole.BUYER:
        raise HTTPException(status_code=400, detail="Contract buyer must have buyer role")

    result = await db.execute(select(DataProduct).where(DataProduct.id.in_(body.product_ids)))
    products = result.scalars().all()
    products_by_id = {str(product.id): product for product in products}
    missing = [str(product_id) for product_id in body.product_ids if str(product_id) not in products_by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Data product not found: {missing[0]}")

    not_owned = [
        str(product.id)
        for product in products
        if product.provider_id != current_user.id
    ]
    if not_owned:
        raise HTTPException(status_code=403, detail="Can only create contracts for your own data products")


@router.post("", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
async def create_contract(
    body: ContractCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _validate_contract_create(db, body, current_user)

    contract = await contract_service.create(
        db,
        provider_id=current_user.id,
        contract_type=body.contract_type,
        buyer_id=body.buyer_id,
        title=body.title,
        terms=body.terms,
        product_ids=[str(pid) for pid in body.product_ids],
        allowed_sandbox_levels=body.allowed_sandbox_levels,
        allowed_sandbox_modes=body.allowed_sandbox_modes,
        allowed_operations=body.allowed_operations,
        max_duration_hours=body.max_duration_hours,
        dp_epsilon_budget=body.dp_epsilon_budget,
        max_output_rows=body.max_output_rows,
        allowed_output_formats=body.allowed_output_formats,
        inspection_rule_set=body.inspection_rule_set,
        purpose=body.purpose,
        purpose_scope=body.purpose_scope,
    )

    await audit_service.log(
        db, action="contract.create", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract.id),
        detail={
            "contract_type": body.contract_type,
            "product_ids": [str(p) for p in body.product_ids],
            "purpose": body.purpose,
            "purpose_scope": body.purpose_scope,
        },
    )

    return ContractResponse.model_validate(contract)


@router.get("")
async def list_contracts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    contract_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Contract)
    count_query = select(func.count()).select_from(Contract)
    if not _can_view_all_contracts(current_user):
        party_filter = (Contract.provider_id == current_user.id) | (Contract.buyer_id == current_user.id)
        query = query.where(party_filter)
        count_query = count_query.where(party_filter)
    if status:
        query = query.where(Contract.status == status)
        count_query = count_query.where(Contract.status == status)
    if contract_type:
        query = query.where(Contract.contract_type == contract_type)
        count_query = count_query.where(Contract.contract_type == contract_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    items = [ContractResponse.model_validate(c) for c in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = await contract_service.get_by_id(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if current_user.id not in (contract.provider_id, contract.buyer_id) and not _can_view_all_contracts(current_user):
        raise HTTPException(status_code=403, detail="Not a party to this contract")
    return ContractResponse.model_validate(contract)


@router.post("/{contract_id}/sign", response_model=ContractResponse)
async def sign_contract(
    contract_id: uuid.UUID,
    body: ContractSign,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        contract = await contract_service.sign(db, contract_id, current_user.id, body.signature, body.timestamp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="contract.sign", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract_id),
    )

    return ContractResponse.model_validate(contract)


@router.post("/{contract_id}/activate", response_model=ContractResponse)
async def activate_contract(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = await contract_service.get_by_id(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if current_user.id not in (contract.provider_id, contract.buyer_id):
        raise HTTPException(status_code=403, detail="Not a party to this contract")
    if contract.status == "active":
        return ContractResponse.model_validate(contract)
    if contract.status != "signed":
        raise HTTPException(status_code=400, detail=f"Cannot activate contract in {contract.status} status")

    try:
        await contract_service.validate_activation_ready(db, contract)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    contract.status = "active"
    await db.flush()

    # Allocate DP budget on first activation
    if contract.dp_epsilon_budget and float(contract.dp_epsilon_budget) > 0:
        await dp_budget_ledger.allocate(db, str(contract.id), float(contract.dp_epsilon_budget))

    await db.refresh(contract)

    # Auto-fulfill: provision sandbox sessions for all contracted products
    try:
        fulfillment_result = await contract_fulfillment.fulfill_contract(db, contract.id)
        await audit_service.log(
            db, action="contract.auto_fulfill", resource_type="contract",
            user_id=current_user.id, resource_id=str(contract.id),
            detail=fulfillment_result,
        )
    except Exception as e:
        # Fulfillment failure is non-fatal — contract is still active
        await audit_service.log(
            db, action="contract.fulfill_failed", resource_type="contract",
            user_id=current_user.id, resource_id=str(contract.id),
            detail={"error": str(e)},
        )

    return ContractResponse.model_validate(contract)


@router.post("/{contract_id}/terminate", response_model=ContractResponse)
async def terminate_contract(
    contract_id: uuid.UUID,
    body: HighRiskOperationRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        contract = await contract_service.terminate(db, contract_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Terminate all active sandbox sessions for this contract
    terminated_count = await contract_fulfillment.terminate_contract_sessions(db, contract_id)
    await audit_service.log(
        db, action="contract.terminate_with_sessions", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract_id),
        detail={
            "sessions_terminated": terminated_count,
            "reason": body.reason,
            "ticket_id": body.ticket_id,
        },
    )

    return ContractResponse.model_validate(contract)


@router.get("/{contract_id}/policy")
async def get_contract_policy(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = await contract_service.get_by_id(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if current_user.id not in (contract.provider_id, contract.buyer_id):
        raise HTTPException(status_code=403, detail="Not a party to this contract")
    return contract_service.compile_policy(contract)


@router.get("/{contract_id}/dp-budget")
async def get_dp_budget_status(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get DP budget status for a contract."""
    contract = await contract_service.get_by_id(db, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if current_user.id not in (contract.provider_id, contract.buyer_id):
        raise HTTPException(status_code=403, detail="Not a party to this contract")

    status = await dp_budget_ledger.get_status(db, str(contract_id))
    ledger = await dp_budget_ledger.get_ledger(db, str(contract_id))

    return {
        "contract_id": str(contract_id),
        "total_epsilon": status.total_epsilon,
        "remaining_epsilon": status.remaining_epsilon,
        "consumed_epsilon": status.consumed_epsilon,
        "last_consumed_at": status.last_consumed_at.isoformat() if status.last_consumed_at else None,
        "entries": [
            {
                "session_id": e.session_id,
                "epsilon_consumed": e.epsilon_consumed,
                "operation": e.operation,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in ledger[:20]
        ],
    }
