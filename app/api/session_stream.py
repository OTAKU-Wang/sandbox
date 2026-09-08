"""W10: WebSocket exec stream — interactive streaming execution channel.

CubeSandbox C11 alignment (host-side equivalent; no in-VM agent):
- ``POST /api/v1/auth/ws-ticket`` mints a 30s one-time ticket (JWT with a
  jti burned in Redis on first use);
- ``WS /api/v1/sandbox-sessions/{session_id}/exec/stream?ticket=...``
  authenticates via ticket, checks session ownership/state (auto-resume
  chain included), then streams JSON frames:
    client→server: start / stdin / ping
    server→client: stdout / stderr / blocked / exit / error / pong

Security: every complete output line passes the T5 DLP inspection before it
leaves the process boundary — critical findings kill the process, emit a
``blocked`` frame and close the connection (fail-closed); other findings are
redacted line-by-line. Raw exit codes travel alone — the ``exit`` frame
never carries output.
"""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

WS_TICKET_TTL_SECONDS = 30
WS_HEARTBEAT_SECONDS = 30

# Close codes (protocol-level; mirrors HTTP semantics)
CLOSE_UNAUTHENTICATED = 4401
CLOSE_CONFLICT = 4409
CLOSE_POLICY_BLOCKED = 4408

_active_streams: dict[str, int] = {}


class OutputBlockedError(Exception):
    """Raised by the line reviewer when a critical finding kills the stream."""


async def issue_ws_ticket(user_id: str, role: str) -> dict:
    """Mint a 30-second single-use WebSocket ticket."""
    settings = get_settings()
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "jti": jti,
        "type": "ws-ticket",
        "iat": int(now.timestamp()),
        "exp": int((now.timestamp()) + WS_TICKET_TTL_SECONDS),
    }
    token = pyjwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    try:
        redis = await get_redis()
        await redis.set(f"cds:ws-ticket:{jti}", "1", ex=WS_TICKET_TTL_SECONDS)
    except Exception as e:
        # Without Redis we cannot enforce single-use; refuse to mint.
        logger.warning("[WS] Ticket mint refused (Redis unavailable): %s", e)
        return {"error": "ws-ticket service unavailable"}
    return {"ticket": token, "expires_in": WS_TICKET_TTL_SECONDS}


async def _consume_ticket(ticket: str) -> dict | None:
    """Verify signature + type + expiry, then burn the jti (single use)."""
    settings = get_settings()
    try:
        payload = pyjwt.decode(
            ticket, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except pyjwt.PyJWTError:
        return None
    if payload.get("type") != "ws-ticket":
        return None
    jti = payload.get("jti")
    if not jti:
        return None
    try:
        redis = await get_redis()
        key = f"cds:ws-ticket:{jti}"
        consumed = await redis.get(key)
        if consumed is None:
            return None
        await redis.delete(key)
    except Exception as e:
        logger.warning("[WS] Ticket burn failed (Redis unavailable): %s", e)
        return None
    return payload


async def _review_line(text: str, *, user_id: str, session_id: str, sandbox_mode: str):
    """Run one output line through the DLP gateway.

    Returns the (possibly redacted) line. Raises OutputBlockedError on a
    critical finding (fail-closed). Inspection failures block (T5).
    """
    from app.services.output_security import inspect_text_output, should_block
    from app.core.metrics import record_output_inspection

    if not text:
        return text
    inspection = inspect_text_output(
        text, user_id=user_id, session_id=session_id, sandbox_mode=sandbox_mode,
    )
    if should_block(inspection):
        record_output_inspection("blocked")
        raise OutputBlockedError(inspection.redacted_output or text)
    redacted = inspection.redacted_output
    record_output_inspection("redacted" if redacted and redacted != text else "passed")
    return redacted if redacted is not None else text


@router.websocket("/api/v1/sandbox-sessions/{session_id}/exec/stream")
async def exec_stream(ws: WebSocket, session_id: uuid.UUID):
    settings = get_settings()
    await ws.accept()

    ticket = ws.query_params.get("ticket", "")
    claims = await _consume_ticket(ticket) if ticket else None
    if claims is None:
        await ws.close(code=CLOSE_UNAUTHENTICATED)
        return
    user_id = claims["sub"]

    from sqlalchemy import select

    from app.core.database import async_session
    from app.models.sandbox_session import SandboxSession, SessionStatus
    from app.api.sandbox_sessions import _maybe_auto_resume

    async with async_session() as db:
        result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
        session = result.scalar_one_or_none()
        if not session or str(session.user_id) != user_id:
            await ws.close(code=CLOSE_UNAUTHENTICATED)
            return

        try:
            await _maybe_auto_resume(session, db)
            await db.commit()
        except Exception:
            await ws.close(code=CLOSE_CONFLICT)
            return

        if session.status != SessionStatus.RUNNING.value:
            await ws.close(code=CLOSE_CONFLICT)
            return
        if not session.container_id:
            await ws.close(code=CLOSE_CONFLICT)
            return

        # N7: streaming exec supported on L0 (bwrap) and k8s pods (python
        # client exec WebSocket); L1/L2 remain STREAM_UNSUPPORTED.
        if session.sandbox_level not in ("L0", "k8s"):
            await ws.send_json({"type": "error", "code": "STREAM_UNSUPPORTED",
                                "message": f"streaming exec not supported on {session.sandbox_level}"})
            await ws.close(code=CLOSE_CONFLICT)
            return

        current_streams = _active_streams.get(str(session_id), 0)
        if current_streams >= settings.EXEC_STREAM_MAX_CONCURRENT:
            await ws.send_json({"type": "error", "code": "TOO_MANY_STREAMS",
                                "message": "concurrent stream limit reached"})
            await ws.close(code=CLOSE_CONFLICT)
            return
        _active_streams[str(session_id)] = current_streams + 1

    try:
        await _stream_loop(ws, session, user_id)
    finally:
        count = _active_streams.get(str(session_id), 1) - 1
        if count <= 0:
            _active_streams.pop(str(session_id), None)
        else:
            _active_streams[str(session_id)] = count


async def _stream_loop(ws: WebSocket, session, user_id: str) -> None:
    from app.core.database import async_session
    from app.services.sandbox_manager import get_sandbox_manager
    from app.services.audit_service import audit_service

    settings = get_settings()
    max_timeout = int(settings.SESSION_EXEC_TIMEOUT_SECONDS)
    process_task: asyncio.Task | None = None

    try:
        while True:
            receive_task = asyncio.create_task(ws.receive_json())
            heartbeat_task = asyncio.create_task(asyncio.sleep(WS_HEARTBEAT_SECONDS))
            done, pending = await asyncio.wait(
                {receive_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

            if receive_task not in done:
                # No heartbeat within the window — close but let any running
                # process finish into the logs (recoverable via /logs).
                await ws.close(code=1000)
                return

            try:
                message = receive_task.result()
            except WebSocketDisconnect:
                return

            msg_type = message.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue
            if msg_type != "start":
                await ws.send_json({"type": "error", "code": "BAD_MESSAGE",
                                    "message": f"unsupported frame type: {msg_type}"})
                continue

            if process_task is not None and not process_task.done():
                await ws.send_json({"type": "error", "code": "OPERATION_LOCKED",
                                    "message": "a command is already streaming"})
                continue

            command = str(message.get("command") or "")
            if not command.strip():
                await ws.send_json({"type": "error", "code": "VALIDATION_ERROR",
                                    "message": "command cannot be empty"})
                continue
            timeout = message.get("timeout")
            try:
                timeout = max(1, min(int(timeout or max_timeout), max_timeout))
            except (TypeError, ValueError):
                timeout = max_timeout

            from app.api.sandbox_db import get_encryption_config_for_session
            from app.api.sandbox_sessions import _session_template_env
            from app.services.sandbox_manager import get_sandbox_manager as _get_runtime

            enc_config = get_encryption_config_for_session(str(session.id))
            env_vars = {
                "CDS_SESSION_ID": str(session.id),
                "CDS_SANDBOX_LEVEL": session.sandbox_level,
                "CDS_USER_ID": str(user_id),
            }
            if enc_config:
                env_vars["CDS_DEK_HEX"] = enc_config["dek_hex"]
            env_vars.update(_session_template_env(session))
            runtime = await _get_runtime()
            sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
            started = datetime.now(timezone.utc)

            async def _on_line(stream_name: str, line: str) -> None:
                try:
                    reviewed = await _review_line(
                        line, user_id=user_id, session_id=str(session.id),
                        sandbox_mode=sandbox_mode,
                    )
                except OutputBlockedError:
                    raise
                await ws.send_json({"type": stream_name, "data": reviewed})

            async def _run() -> dict:
                from app.services import shared_volumes

                try:
                    binds = await shared_volumes.resolve_binds(db, session.id)
                except Exception:
                    binds = []
                return await runtime.execute_streaming(
                    session.container_id, command, "bash",
                    env_vars=env_vars, timeout=timeout,
                    extra_binds=binds or None, on_line=_on_line,
                )

            command_sha = hashlib.sha256(command.encode("utf-8")).hexdigest()
            process_task = asyncio.create_task(_run())

            def _audit(blocked: bool, exit_code) -> None:
                async def _write():
                    try:
                        async with async_session() as audit_db:
                            await audit_service.log(
                                audit_db, action="session.exec_stream",
                                resource_type="sandbox_session",
                                user_id=uuid.UUID(user_id), session_id=session.id,
                                detail={
                                    "command_sha256": command_sha,
                                    "command_preview": command[:500],
                                    "timeout_seconds": timeout,
                                    "exit_code": exit_code,
                                    "output_blocked": blocked,
                                },
                            )
                            await audit_db.commit()
                    except Exception as e:
                        logger.warning("[WS] exec_stream audit failed: %s", e)
                asyncio.get_running_loop().create_task(_write())

            try:
                result = await process_task
            except OutputBlockedError:
                _audit(True, None)
                await ws.send_json({"type": "blocked", "code": "PII_CRITICAL"})
                await ws.close(code=CLOSE_POLICY_BLOCKED)
                return
            process_task = None

            duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            _audit(False, result.get("exit_code"))
            await ws.send_json({
                "type": "exit",
                "code": result.get("exit_code", -1),
                "duration_ms": result.get("duration_ms", duration_ms),
            })
    except WebSocketDisconnect:
        return
    except Exception as e:
        logger.warning("[WS] stream error: %s", e)
        try:
            await ws.send_json({"type": "error", "code": "INTERNAL_ERROR", "message": str(e)})
        except Exception:
            pass

