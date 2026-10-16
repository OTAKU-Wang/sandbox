"""WebSocket exec streaming (W10 / PZ-11②).

Flow: mint a 30s single-use ws-ticket (``POST /api/v1/auth/ws-ticket``),
open ``/api/v1/sandbox-sessions/{id}/exec/stream?ticket=...`` and exchange
JSON frames. Server frames: ``stdout`` / ``stderr`` / ``blocked`` /
``exit`` / ``error`` / ``pong``; client frames: ``start`` / ``stdin`` /
``ping``.

Capability matrix: only ``L0`` (bwrap) and ``k8s`` sessions support
streaming exec — the server answers ``STREAM_UNSUPPORTED`` for the rest,
and :meth:`CDSClient.stream_exec` fails fast before any socket is opened.

The ``websocket-client`` package is an OPTIONAL dependency: importing this
module never requires it, but opening a stream raises :class:`CDSError`
with install guidance when it is missing (honest degradation, no fake
streaming).
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Callable, Iterator

from .client import CDSError

# Capability matrix (PZ-13): levels where streaming exec is real.
SUPPORTED_STREAM_LEVELS = ("L0", "k8s")

#: Frames that end a stream_exec iteration after being yielded.
_TERMINAL_FRAME_TYPES = ("exit", "blocked", "error")


def _import_websocket():
    """Lazy import so the SDK core stays usable without websocket-client."""
    try:
        import websocket  # websocket-client
    except ImportError as exc:
        raise CDSError(
            501,
            "websocket-client is required for streaming exec — "
            "install it with: pip install 'cds-sdk[stream]' "
            "(or: pip install websocket-client)",
        ) from exc
    return websocket


def _default_ws_open(url: str, timeout: float):
    ws = _import_websocket().WebSocket()
    ws.connect(url)
    ws.settimeout(timeout)
    return ws


def _build_command(code: str, language: str) -> str:
    if not code or not code.strip():
        raise CDSError(400, "code cannot be empty")
    if language in ("python", "python3"):
        return f"python3 -c {shlex.quote(code)}"
    return code


def _to_ws_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        scheme = "wss"
        rest = base[len("https://"):]
    elif base.startswith("http://"):
        scheme = "ws"
        rest = base[len("http://"):]
    else:
        scheme, rest = "ws", base
    return f"{scheme}://{rest}{path}"


def stream_exec(
    client,
    session_id: str,
    code: str,
    language: str = "python",
    *,
    timeout_seconds: int | None = None,
    recv_timeout: float = 30.0,
    _ws_open: Callable[[str, float], Any] | None = None,
) -> Iterator[dict]:
    """Yield exec output frames for one streaming run.

    Parameters
    ----------
    client:
        A :class:`CDSClient` (used for the ws-ticket exchange; its base URL
        determines the WebSocket endpoint).
    session_id:
        Target session (must be an L0 or k8s session, status running/ready).
    code:
        Code to run. ``language="python"`` wraps it as
        ``python3 -c <code>``; any other language runs ``code`` as a shell
        command.
    timeout_seconds:
        Server-side command timeout (clamped server-side to the session
        exec timeout).
    recv_timeout:
        Seconds to wait for the next frame before giving up.
    _ws_open:
        Test hook: ``fn(url, timeout) -> ws-like`` with ``send``/``recv``/
        ``close``. Defaults to websocket-client.

    Yields one parsed frame (dict) per server message. Iteration stops
    after a terminal frame (``exit`` / ``blocked`` / ``error``) or when the
    server closes the connection. A ``blocked`` frame means the T5 DLP
    inspection killed the stream (fail-closed) — treat the output as
    untrusted and check :meth:`CDSClient.get_session_audit`.
    """
    ticket_resp = client.request_ws_ticket()
    ticket = ticket_resp.get("ticket") if isinstance(ticket_resp, dict) else None
    if not ticket:
        raise CDSError(500, f"ws-ticket response has no ticket: {ticket_resp!r}")

    command = _build_command(code, language)
    path = (
        f"/api/v1/sandbox-sessions/{session_id}/exec/stream"
        f"?ticket={ticket}"
    )
    url = _to_ws_url(str(client._client.base_url), path)

    open_ws = _ws_open or _default_ws_open
    try:
        ws = open_ws(url, recv_timeout)
    except CDSError:
        raise
    except Exception as exc:
        raise CDSError(502, f"websocket connect failed: {exc}") from exc

    try:
        frame: dict[str, Any] = {"type": "start", "command": command}
        if timeout_seconds is not None:
            frame["timeout"] = int(timeout_seconds)
        ws.send(json.dumps(frame))
        while True:
            try:
                raw = ws.recv()
            except Exception as exc:  # server closed / timeout
                if _is_closed_error(exc):
                    return
                raise CDSError(502, f"websocket recv failed: {exc}") from exc
            if not raw:
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                # Non-JSON frames (should not happen per W10 contract) are
                # surfaced verbatim rather than swallowed.
                yield {"type": "data", "data": raw}
                continue
            yield msg
            if isinstance(msg, dict) and msg.get("type") in _TERMINAL_FRAME_TYPES:
                return
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _is_closed_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in ("WebSocketConnectionClosedException", "WebSocketTimeoutException"):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))
