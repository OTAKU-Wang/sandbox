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

from ._encoding import path_segment, query_value
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
    # A86: connect(timeout=...) is what bounds the TCP + handshake phase
    # (websocket-client copies it into sock_opt.timeout); settimeout() alone
    # only covers recv(), so an unreachable host could block forever here.
    ws.connect(url, timeout=timeout)
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


def _validate_timeout_seconds(value: Any) -> None:
    """Fail fast on a bad ``timeout_seconds`` BEFORE any network I/O (SDK MINOR).

    The old code reached ``int(timeout_seconds)`` only after the ws-ticket
    round trip, the websocket connect and the start frame — a garbage value
    (``"30"``, ``0``, ``-1``) burned a full session round trip and then blew
    up with a bare ``ValueError`` / ``TypeError`` from inside the generator,
    after frames may already have been yielded. Raises :class:`ValueError`
    with an actionable message instead.
    """
    if value is None:
        return
    # bool is an int subclass; True/False as a timeout is a caller bug.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"timeout_seconds must be a positive number of seconds "
            f"(int or float), got {value!r} ({type(value).__name__})"
        )
    # `not > 0` also rejects NaN.
    if not value > 0:
        raise ValueError(
            f"timeout_seconds must be a positive number of seconds, got {value!r}"
        )


def _validate_stream_args(session_id: Any, language: Any, recv_timeout: Any) -> None:
    """A218: validate the remaining ``stream_exec`` arguments BEFORE any I/O.

    Companion of :func:`_validate_timeout_seconds` — same contract: a bad
    argument is a caller bug, so it raises :class:`ValueError` with an
    actionable message *before* burning a ws-ticket round trip, instead of
    surfacing later as a raw ``AttributeError``/``TypeError`` (or, for
    ``session_id``, as a percent-mangled route 404 from the server).
    ``code`` keeps its existing contract: empty code raises
    :class:`CDSError` (400) at frame-build time — that one is data, not
    argument shape.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError(
            f"session_id must be a non-empty string, got {session_id!r}"
            f" ({type(session_id).__name__})"
        )
    if not isinstance(language, str) or not language.strip():
        raise ValueError(
            f"language must be a non-empty string, got {language!r}"
            f" ({type(language).__name__})"
        )
    if isinstance(recv_timeout, bool) or not isinstance(recv_timeout, (int, float)):
        raise ValueError(
            f"recv_timeout must be a positive number of seconds "
            f"(int or float), got {recv_timeout!r} ({type(recv_timeout).__name__})"
        )
    if not recv_timeout > 0:
        raise ValueError(
            f"recv_timeout must be a positive number of seconds, got {recv_timeout!r}"
        )


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
        Must be a non-empty string — validated before any request (A218);
        percent-encoded as a single URL path segment (A215).
    code:
        Code to run. ``language="python"`` wraps it as
        ``python3 -c <code>``; any other language runs ``code`` as a shell
        command.
    timeout_seconds:
        Server-side command timeout (clamped server-side to the session
        exec timeout). Validated up-front — anything that is not a positive
        int/float raises :class:`ValueError` before any request is sent.
    recv_timeout:
        Seconds to wait for the next frame before giving up. Also validated
        up-front: a non-positive or non-numeric value raises
        :class:`ValueError` before any request is sent (A218).
    _ws_open:
        Test hook: ``fn(url, timeout) -> ws-like`` with ``send``/``recv``/
        ``close``. Defaults to websocket-client.

    Yields one parsed frame (dict) per server message. Iteration stops
    after a terminal frame (``exit`` / ``blocked`` / ``error``) or when the
    server closes the connection. Silence longer than ``recv_timeout`` is
    reported as a degradation, not as a clean end: the generator raises
    :class:`CDSError` (504) carrying the structured marker ``truncated =
    True`` (SDK MINOR, cr-2026-10-15 §4.7) — consumers branch on the flag,
    not on message text, so a timeout can never masquerade as complete
    output (a sandboxed compile or data pull can be silent for minutes).
    A clean end (terminal frame / server close) never sets the flag.
    A ``blocked`` frame means the T5 DLP inspection killed the stream
    (fail-closed) — treat the output as untrusted and check
    :meth:`CDSClient.get_session_audit`.
    """
    # SDK MINOR / A218: validate every argument shape before burning a
    # ws-ticket (timeout first — the pre-existing pin keeps that order).
    _validate_timeout_seconds(timeout_seconds)
    _validate_stream_args(session_id, language, recv_timeout)
    ticket_resp = client.request_ws_ticket()
    ticket = ticket_resp.get("ticket") if isinstance(ticket_resp, dict) else None
    if not ticket:
        raise CDSError(500, f"ws-ticket response has no ticket: {ticket_resp!r}")

    command = _build_command(code, language)
    # A215: session id is one route segment and the ticket one query value —
    # encode both, or a reserved character silently re-routes/breaks the URL.
    path = (
        f"/api/v1/sandbox-sessions/{path_segment(session_id)}/exec/stream"
        f"?ticket={query_value(ticket)}"
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
        try:
            ws.send(json.dumps(frame))
        except Exception as exc:
            # A86: the SDK contract is "only CDSError escapes" (A76 family) —
            # a raw websocket-client send error must not reach the caller.
            raise CDSError(502, f"websocket send failed: {exc}") from exc
        while True:
            try:
                raw = ws.recv()
            except Exception as exc:  # closed / stalled / transport error
                # A74: no frame within recv_timeout is a degradation, not a
                # clean end — it must be disclosed, never reported as "done".
                if _is_timeout_error(exc):
                    # SDK MINOR (cr-2026-10-15 §4.7): the truncation marker is
                    # STRUCTURED (`truncated=True` on the error), not just
                    # message prose — a consumer can branch on the flag and
                    # never mistakes a stalled stream for complete output.
                    err = CDSError(
                        504,
                        f"websocket recv timed out after {recv_timeout}s without "
                        f"receiving a frame: output is TRUNCATED (truncated=true) "
                        f"and the command may still be running in the sandbox — "
                        f"raise recv_timeout or fall back to execute()/logs()",
                    )
                    err.truncated = True
                    raise err from exc
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


def _is_timeout_error(exc: Exception) -> bool:
    """A stalled stream (silence past recv_timeout) is not a closed one (A74)."""
    if isinstance(exc, TimeoutError):
        return True
    return type(exc).__name__ == "WebSocketTimeoutException"


def _is_closed_error(exc: Exception) -> bool:
    """Only a genuinely closed connection ends the iteration cleanly (A74)."""
    name = type(exc).__name__
    if name == "WebSocketConnectionClosedException":
        return True
    return isinstance(exc, ConnectionError)
