from fastapi import APIRouter, Depends

from app.core.deps import get_business_user
from app.models.user import User
from app.services.capability_matrix import get_capability_matrix
from app.services.compliance_report import disclosure_fields

router = APIRouter()


# PZ-13 (SS-11): level capability matrix — single authority source
# (spec ss-11-cds-productization.md §4.2). Read-only, JWT like the other
# /api/v1 endpoints.
@router.get("/capability-matrix")
async def capability_matrix_endpoint(user: User = Depends(get_business_user)):
    return {
        **get_capability_matrix(),
        **disclosure_fields(),
    }
