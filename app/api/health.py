from fastapi import APIRouter

from app.services.capability_matrix import health_capability_section

router = APIRouter()


@router.get("/health")
async def health_check():
    # PZ-13: additive capability section — level list + l2c planned flag
    # (authority: app/services/capability_matrix.py).
    return {
        "status": "ok",
        "service": "cds",
        "version": "0.1.0",
        "capability": health_capability_section(),
    }
