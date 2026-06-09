"""Compliance report API — TC609-6-2025-01 compliance report generation."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.user import User, UserRole
from app.schemas.compliance import ComplianceReportRequest, ComplianceReportResponse
from app.services.compliance_report import compliance_report_service

router = APIRouter()


@router.post("/reports", response_model=ComplianceReportResponse)
async def generate_compliance_report(
    body: ComplianceReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.REGULATOR, UserRole.OPERATOR)),
):
    """Generate a TC609 compliance report with auto-filled audit data."""
    return await compliance_report_service.generate_report(
        db=db,
        start_date=body.start_date,
        end_date=body.end_date,
        report_type=body.report_type,
        include_sections=body.include_sections,
    )


@router.get("/reports/quick")
async def quick_compliance_report(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.REGULATOR, UserRole.OPERATOR)),
):
    """Quick compliance report for the last N days."""
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=days)
    return await compliance_report_service.generate_report(
        db=db, start_date=start, end_date=end,
    )
