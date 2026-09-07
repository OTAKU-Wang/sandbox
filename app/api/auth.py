import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse
from app.services.auth_service import register_user, authenticate_user, create_access_token, create_refresh_token, decode_refresh_token, get_user_by_id
from app.services.audit_service import audit_service
from app.services.crypto_service import crypto_service

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        user = await register_user(db, body.username, body.email, body.password, body.role, body.organization)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    await audit_service.log(
        db, action="user.register", resource_type="user", user_id=user.id,
        detail={"username": body.username, "role": body.role},
        ip_address=request.client.host if request.client else None,
    )
    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.username, body.password)
    if not user:
        await audit_service.log(
            db, action="user.login_failed", resource_type="user",
            detail={"username": body.username, "reason": "invalid_credentials"},
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    await audit_service.log(
        db, action="user.login", resource_type="user", user_id=user.id,
        detail={"username": body.username},
        ip_address=request.client.host if request.client else None,
    )
    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: dict, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access+refresh token pair."""
    token = body.get("refresh_token")
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    try:
        payload = decode_refresh_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.post("/ws-ticket")
async def create_ws_ticket(current_user: User = Depends(get_current_user)):
    """W10: mint a 30s single-use WebSocket ticket for the exec stream."""
    from app.api.session_stream import issue_ws_ticket

    result = await issue_ws_ticket(str(current_user.id), current_user.role)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.patch("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: uuid.UUID,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: update a user's role."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    new_role = body.get("role")
    if not new_role:
        raise HTTPException(status_code=400, detail="role required")
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = new_role
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/generate-sm2-keys")
async def generate_sm2_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate SM2 key pair for the current user and store the public key.

    Returns the private key (hex) — user must save it securely, it won't be stored.
    """
    try:
        keypair = crypto_service.generate_keypair()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Store public key as the user's SM2 certificate
    current_user.sm2_certificate = keypair.public_key
    await db.flush()

    return {
        "public_key": keypair.public_key,
        "private_key": keypair.private_key,
        "message": "SM2密钥对已生成。请妥善保管私钥，系统不会存储。",
    }


@router.post("/sign-data")
async def sign_data(
    data: dict,
    current_user: User = Depends(get_current_user),
):
    """Sign arbitrary data with user's SM2 private key.

    Request body: { "data": "text to sign", "private_key": "hex..." }
    Returns: { "signature": "hex..." }
    """
    text = data.get("data")
    private_key = data.get("private_key")
    if not text or not private_key:
        raise HTTPException(status_code=400, detail="data and private_key required")

    if not current_user.sm2_certificate:
        raise HTTPException(status_code=400, detail="用户未生成SM2密钥对，请先调用 /generate-sm2-keys")

    try:
        sig = crypto_service.sign(text.encode("utf-8"), private_key, current_user.sm2_certificate)
        return {"signature": sig.signature}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"签名失败: {e}")
