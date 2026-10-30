"""Sync HTTP client for the CDS sandbox API (httpx-based, stdlib-free beyond httpx).

Error model: any non-2xx response raises :class:`CDSError` with the parsed
``detail`` from the FastAPI error body when available.
"""
from __future__ import annotations

import time
from typing import Any, BinaryIO, Iterable, Iterator

import httpx

from ._encoding import path_segment as _enc

_TERMINAL_STATUSES = ("completed", "terminated", "failed", "expired")

# PZ-11④: honest-degradation disclosure fields — extracted from any response
# that carries them (additive; plain dict responses are unchanged).
DISCLOSURE_KEYS = (
    "is_simulation",
    "tee_mode",
    "backend",
    "engine",
    "trust_root",
    "signing_backend",
    "aead_algorithm",
    "dp_applied",
)


class CDSError(RuntimeError):
    """Legacy error type (kept for backward compatibility).

    New code should catch :class:`CDSApiError`, which exposes the W2 error
    contract fields (code / request_id / retry_after).

    ``truncated`` is a structured degradation marker: it is ``True`` only on
    errors whose payload is known to be cut short (today: a WS stream
    stalled past ``recv_timeout`` — see :mod:`cds_sdk.stream`). Consumers
    branch on this flag instead of message text, so truncated output can
    never masquerade as complete (SDK MINOR, cr-2026-10-15 §4.7).
    """

    #: structured truncation marker — False unless a producer set it (stream.py).
    truncated: bool = False

    def __init__(self, status_code: int, detail: Any, url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(f"CDSError {status_code} on {url}: {detail}")


class CDSApiError(CDSError):
    """Parsed W2 error-contract response.

    Attributes mirror the unified body ``{code, message, detail, request_id}``;
    ``retry_after`` is populated from the ``Retry-After`` response header when
    present (429/503). Responses without a ``code`` field (legacy) fall back
    to ``HTTP_{status}``.
    """

    def __init__(
        self,
        status_code: int,
        detail: Any,
        url: str = "",
        *,
        code: str | None = None,
        message: str | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
        headers: dict | None = None,
    ):
        super().__init__(status_code, detail, url)
        self.code = code or (f"HTTP_{status_code}" if not isinstance(detail, dict) or "code" not in detail else str(detail["code"]))
        self.message = message or (
            str(detail.get("message", detail)) if isinstance(detail, dict) else str(detail)
        )
        self.request_id = request_id or (
            str(detail.get("request_id")) if isinstance(detail, dict) and detail.get("request_id") else None
        )
        if retry_after is None and headers:
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra is not None:
                try:
                    retry_after = int(ra)
                except ValueError:
                    retry_after = None
        self.retry_after = retry_after
        self.headers = headers or {}


_RETRY_AFTER_CAP_SECONDS = 60.0


def _retry_after_seconds(resp: httpx.Response) -> float:
    """Parse the ``Retry-After`` header, clamped into [0, 60] seconds.

    MAJOR-7 (cr-2026-10-15 §4.7): the backoff honours Retry-After as the docs
    promise (README: 尊重 ``Retry-After``) — the old 5s clip silently ignored
    any server-requested wait beyond 5s. The one remaining bound is a
    defensive 60s cap: this is a *synchronous* client and a longer sleep
    would block the caller past the point of usefulness of a retry loop.
    A missing/unparseable header falls back to 1s.
    """
    ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if ra is not None:
        try:
            return min(max(0.0, float(ra)), _RETRY_AFTER_CAP_SECONDS)
        except ValueError:
            pass
    return 1.0


# A213 / A88: 429/503 only say "this hop did not accept the request" — a gateway
# can answer them AFTER the backend already committed the write, so replaying a
# write duplicates it (double session / duplicate snapshot / duplicate key).
# Only methods whose replay has no server-side effect are resent automatically;
# writes raise, and the caller decides (``CDSApiError.retry_after`` is populated).
# DELETE/PUT stay out of the set: a replay of a committed delete surfaces as a
# 404, and this API's PUT handlers are not specified as idempotent.
# PZ-11③ / docs/error-codes.md: 410 SESSION_TERMINATED remains terminal — it is
# not in the retry status set at all.
_RETRY_STATUSES = (429, 503)
_IDEMPOTENT_METHODS = ("GET", "HEAD", "OPTIONS")


def _is_retryable(method: str, status_code: int, retries: int) -> bool:
    return (
        status_code in _RETRY_STATUSES
        and retries > 0
        and method.upper() in _IDEMPOTENT_METHODS
    )


# ── 合约签名原文的字节锁（OCR-A110 / OCR-A255）─────────────────────────────
# 必须与后端 `app/services/crypto_service.py::contract_sign_data` 逐字同语义：原文用
# `|` 分隔字段、`,` 分隔 purpose_scope 元素，字段里带分隔符就能拼出与另一组字段
# 完全相同的原文，而这份原文的 SM2 签名是跨方的不可否认证据。修法与后端一致：
# 拼接前**拒绝**而不是转义（改编码得后端/SDK/前端三边同改，各自转义会验签静默失败，
# 见 crypto_service.py:208-216）。上限取 DB 列宽，与后端同名类常量一一对应；SDK 是
# 独立发行包不能 import app.*，所以这里是镜像副本，三边常量+向量表由
# tests/test_ocr_a255_xlang_parity.py 与 cds-frontend 的平价 spec 锁死。
PAYLOAD_MAX_LEN = 64  # == CryptoService._PAYLOAD_MAX_LEN
PAYLOAD_ROLE_MAX_LEN = 16  # == CryptoService._PAYLOAD_ROLE_MAX_LEN
PAYLOAD_SCOPE_MAX_ITEMS = 64  # == CryptoService._PAYLOAD_SCOPE_MAX_ITEMS


def _payload_field_issues(
    field: str, value: Any, *, max_len: int, forbidden: str = "|"
) -> list[dict]:
    """一个字段能否无歧义地进原文：不含分隔符、不超列宽（后端同名函数的镜像）。"""
    text = "" if value is None else str(value)
    issues = [{"field": field, "reason": f"contains forbidden separator {ch!r}"}
              for ch in forbidden if ch in text]
    if len(text) > max_len:
        issues.append({"field": field, "reason": f"exceeds {max_len} characters"})
    return issues


def _contract_payload_errors(
    contract_id: str, contract_no: str, party_role: str, timestamp: str,
    purpose: str | None = None, purpose_scope: list | None = None,
) -> list[dict]:
    """逐字段汇总歧义/越界明细，字段顺序与 reason 文案跟后端逐字一致。"""
    check = _payload_field_issues
    errors: list[dict] = []
    errors += check("contract_id", contract_id, max_len=PAYLOAD_MAX_LEN)
    errors += check("contract_no", contract_no, max_len=PAYLOAD_MAX_LEN)
    errors += check("party_role", party_role, max_len=PAYLOAD_ROLE_MAX_LEN)
    errors += check("timestamp", timestamp, max_len=PAYLOAD_MAX_LEN)
    errors += check("purpose", purpose, max_len=PAYLOAD_MAX_LEN)
    if purpose_scope is not None:
        if len(purpose_scope) > PAYLOAD_SCOPE_MAX_ITEMS:
            errors.append({"field": "purpose_scope",
                           "reason": f"more than {PAYLOAD_SCOPE_MAX_ITEMS} elements"})
        for idx, item in enumerate(purpose_scope):
            for issue in check("purpose_scope", item,
                               max_len=PAYLOAD_MAX_LEN, forbidden="|,"):
                errors.append({**issue, "index": idx})
    return errors


class CDSResponse(dict):
    """Dict-shaped API response with structured disclosure access (PZ-11④).

    A plain :class:`dict` subclass — existing code that treats responses as
    dicts (indexing, ``json.dumps``, ``dict(...)``) is unaffected. New users
    can read :attr:`disclosure`, the subset of the PZ-06 disclosure fields
    (:data:`DISCLOSURE_KEYS`) present at the top level or under a nested
    ``disclosure`` key; empty when the response discloses nothing.
    """

    @property
    def disclosure(self) -> dict:
        out: dict[str, Any] = {}
        nested = self.get("disclosure")
        if isinstance(nested, dict):
            out.update(nested)
        for key in DISCLOSURE_KEYS:
            if key in self:
                out[key] = self[key]
        return out


def _parse_error(resp: httpx.Response, path: str) -> CDSApiError:
    """Build a CDSApiError from a non-2xx response (W2 shape or legacy).

    A173: the contract is "every non-2xx raises CDSApiError" — parsing the
    body must never raise a *different* exception on hostile input. The old
    code caught only ``ValueError`` (JSON decode failures) and then touched
    the payload with ``.get``; A76 covered non-dict JSON, but a response
    declaring ``Content-Encoding: gzip`` with a corrupt payload makes httpx
    raise ``httpx.DecodingError`` (not a ValueError) out of ``.json()`` AND
    ``.text``, which escaped the parser as a raw transport error. Every
    extraction is guarded now; the fallback detail is the raw bytes.
    """
    try:
        payload = resp.json()
    except (ValueError, httpx.DecodingError):
        payload = None
        try:
            detail: Any = resp.text
        except (ValueError, httpx.DecodingError):
            detail = repr(resp.content)
    else:
        # A76: valid JSON is not necessarily an object — an upstream gateway can
        # answer ``[{...}]`` / ``"error"`` / ``null``, and ``payload.get`` on those
        # raised AttributeError, breaking the "non-2xx raises CDSApiError" contract.
        if isinstance(payload, dict):
            detail = payload.get("detail", payload)
        elif payload is None:
            try:
                detail = resp.text
            except (ValueError, httpx.DecodingError):
                detail = repr(resp.content)
        else:
            detail = payload
    return CDSApiError(
        resp.status_code,
        detail,
        path,
        code=str(payload.get("code")) if isinstance(payload, dict) and payload.get("code") else None,
        message=str(payload.get("message")) if isinstance(payload, dict) and payload.get("message") else None,
        request_id=str(payload.get("request_id")) if isinstance(payload, dict) and payload.get("request_id") else None,
        headers=dict(resp.headers),
    )


def _compact(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop unset (``None``) keys so PATCH/PUT bodies only carry what the caller passed."""
    return {key: value for key, value in fields.items() if value is not None}


class CDSClient:
    """Small typed wrapper over the ``/sandbox-sessions`` surface.

    Auth (SS-12 §4.6) is one of three mutually exclusive paths, chosen at
    construction:

    - ``token``: a human JWT (positional, backward compatible).
    - ``app_id`` + ``app_secret``: gateway app credentials sent via the
      ``X-App-Id`` / ``X-App-Secret`` headers (PZ-11⑥), no Bearer token.
    - ``api_key``: a platform API key, a ``cds_ak_``-prefixed machine
      credential (SS-12), sent as ``Authorization: Bearer <api_key>`` on
      every request — the same header a JWT uses; the server decides the
      auth path from the credential shape.

    The bare ``api_key`` value is shown **once** by the issuing admin at
    creation/rotation time and can never be retrieved again — store it
    immediately. A 401 response is **not** retried (only 429/503 are); the
    parsed :class:`CDSApiError` carries ``.code`` of ``API_KEY_INVALID``
    (bad/expired key) or ``API_KEY_INACTIVE`` (disabled/revoked/in failure
    cooldown). On those codes the client must stop and the admin should be
    told to check the key's status.
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: float = 120.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        task_payload_in_body: bool = True,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_key: str | None = None,
    ):
        """Build a client for ``base_url``.

        Auth is exactly one of three mutually exclusive paths (SS-12 §4.6):
        a JWT ``token`` (positional, backward compatible), the gateway
        app-credential pair ``app_id`` / ``app_secret`` (PZ-11⑥, sent via
        the ``X-App-Id`` / ``X-App-Secret`` headers with no Bearer token),
        or a platform API key ``api_key`` (a ``cds_ak_``-prefixed machine
        credential, sent as ``Authorization: Bearer <api_key>`` on every
        request).

        Providing more than one path, a half-formed app-credential pair, or
        an ``api_key`` that does not start with ``cds_ak_`` all raise
        :class:`CDSError` (400) at construction — a malformed key fails
        fast and no request is ever sent.

        ``task_payload_in_body`` (A87/A216) sends ``create_task``'s script /
        RAG-query payload as a JSON request body instead of a query string, so
        tenant code never reaches an access log or the 64 KB URL cap. It is the
        default — ``POST /api/v1/sandbox-tasks`` reads the body form since
        A87/A216. Pass ``task_payload_in_body=False`` to talk to a pre-body
        server with the legacy query-string shape.

        Retries are limited to idempotent reads (GET/HEAD/OPTIONS) on 429/503;
        a write that gets 429/503 raises :class:`CDSApiError` undelivered-by-
        retry, with ``retry_after`` from the response header. The wait between
        attempts honours the server's ``Retry-After`` header — the documented
        contract (MAJOR-7 fixed the old 5s clip that contradicted it), capped
        at 60s, falling back to 1s when the header is absent. 410
        SESSION_TERMINATED stays terminal and is never retried (W2).
        """
        has_token = bool(token)
        has_app = bool(app_id or app_secret)
        has_key = bool(api_key)
        if (has_token + has_app + has_key) > 1:
            raise CDSError(
                400,
                "pass exactly one auth path: token, app_id/app_secret, "
                "or api_key — not more than one",
            )
        if has_app and not (app_id and app_secret):
            raise CDSError(400, "app_id and app_secret must be provided together")
        if has_key and not api_key.startswith("cds_ak_"):
            raise CDSError(400, "api_key must start with 'cds_ak_'")
        if not (has_token or has_app or has_key):
            raise CDSError(
                400,
                "CDSClient requires a token, the app_id/app_secret gateway "
                "credential pair, or a cds_ak_ api_key",
            )
        self._access_token = token
        self._refresh_token: str | None = None
        self._max_retries = max_retries
        self._task_payload_in_body = task_payload_in_body
        auth_headers: dict[str, str] = {}
        if token:
            auth_headers["Authorization"] = f"Bearer {token}"
        elif api_key:
            auth_headers["Authorization"] = f"Bearer {api_key}"
        if app_id and app_secret:
            auth_headers["X-App-Id"] = app_id
            auth_headers["X-App-Secret"] = app_secret
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={**auth_headers, **(headers or {})},
            transport=transport,
        )

    # ── low level ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        retries = kwargs.pop("_retries", self._max_retries)
        resp = self._client.request(method, path, **kwargs)
        if _is_retryable(method, resp.status_code, retries):
            time.sleep(_retry_after_seconds(resp))
            return self._request(method, path, _retries=retries - 1, **kwargs)
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        if resp.status_code == 204 or not resp.content:
            return None
        data = resp.json()
        if isinstance(data, dict):
            data = CDSResponse(data)
        return data

    def _request_with_headers(self, method: str, path: str, **kwargs: Any) -> tuple[Any, dict]:
        retries = kwargs.pop("_retries", self._max_retries)
        resp = self._client.request(method, path, **kwargs)
        if _is_retryable(method, resp.status_code, retries):
            time.sleep(_retry_after_seconds(resp))
            return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
        # A212: this channel used to return the error body as success-shaped data.
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        body = None if resp.status_code == 204 or not resp.content else resp.json()
        if isinstance(body, dict):
            body = CDSResponse(body)
        return body, dict(resp.headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CDSClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── sessions ─────────────────────────────────────────────────────────
    def health(self) -> Any:
        return self._request("GET", "/health")

    def list_templates(self) -> list[dict]:
        return self._request("GET", "/api/v1/sandbox-sessions/session-templates")["templates"]

    def create_session(
        self,
        data_product_id: str,
        *,
        contract_id: str | None = None,
        sandbox_level: str = "L3",
        sandbox_mode: str = "structured_query",
        timeout_seconds: int = 3600,
        template: str | None = None,
        resource_limits: dict | None = None,
        idle_policy: str | None = None,
        auto_resume: bool = False,
    ) -> dict:
        body: dict[str, Any] = {
            "data_product_id": data_product_id,
            "sandbox_level": sandbox_level,
            "sandbox_mode": sandbox_mode,
            "timeout_seconds": timeout_seconds,
        }
        if contract_id:
            body["contract_id"] = contract_id
        if template:
            body["template"] = template
        if resource_limits:
            body["resource_limits"] = resource_limits
        if idle_policy:
            body["idle_policy"] = idle_policy
        if auto_resume:
            body["auto_resume"] = True
        return self._request("POST", "/api/v1/sandbox-sessions", json=body)

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}")

    def list_sessions(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict:
        """List sessions.

        Offset mode (legacy): pass page/page_size — returns the full
        envelope with total. Keyset mode (W8): pass cursor (and optionally
        limit) — the response carries ``next_cursor`` when a further page
        exists; iterate with :meth:`iter_sessions` instead for auto-paging.
        """
        if cursor is not None or (limit is not None and page == 1 and page_size == 20):
            params: dict[str, Any] = {"limit": limit if limit is not None else page_size}
            if status:
                params["status"] = status
            if cursor:
                params["cursor"] = cursor
            body, headers = self._request_with_headers(
                "GET", "/api/v1/sandbox-sessions", params=params
            )
            body["next_cursor"] = headers.get("x-next-cursor")
            return body
        return self._request(
            "GET",
            "/api/v1/sandbox-sessions",
            params={
                "skip": (page - 1) * page_size,
                "limit": page_size,
                **({"status": status} if status else {}),
            },
        )

    def iter_sessions(
        self, *, status: str | None = None, page_size: int = 100
    ) -> Iterator[dict]:
        """Yield every visible session via keyset pagination (W8)."""
        cursor: str | None = ""  # empty string = keyset page 1
        while True:
            body = self.list_sessions(status=status, cursor=cursor, limit=page_size)
            yield from body.get("items", [])
            cursor = body.get("next_cursor")
            if not cursor:
                return

    def terminate_session(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/terminate")

    def execute(self, session_id: str, code: str, language: str = "python") -> dict:
        return self._request(
            "POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/execute",
            json={"code": code, "language": language},
        )

    def exec_command(self, session_id: str, command: str, timeout_seconds: int | None = None) -> dict:
        body: dict[str, Any] = {"command": command}
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return self._request("POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/exec", json=body)

    def pause(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/pause")

    def resume(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/resume")

    def refresh(self, session_id: str, extend_seconds: int | None = None) -> dict:
        body = {"extend_seconds": extend_seconds} if extend_seconds is not None else {}
        return self._request("POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/refreshes", json=body)

    def wait_for_status(
        self,
        session_id: str,
        *,
        target: Iterable[str] = ("running", "ready"),
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> dict:
        """Poll until the session status is one of ``target`` (or terminal)."""
        targets = set(target)
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.get_session(session_id)
            status = last.get("status", "")
            if status in targets or status in _TERMINAL_STATUSES:
                return last
            time.sleep(poll_interval)
        raise TimeoutError(
            f"session {_enc(session_id)} did not reach {sorted(targets)} within {timeout}s "
            f"(last status: {last.get('status')!r})"
        )

    # ── files ────────────────────────────────────────────────────────────
    def list_files(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/files")["files"]

    def upload_file(
        self,
        session_id: str,
        filename: str,
        data: bytes | bytearray | BinaryIO | str,
    ) -> dict:
        """Upload one file into the session workspace.

        ``data`` may be raw bytes, an open binary file object, or a local path.
        """
        if isinstance(data, str):
            with open(data, "rb") as fh:
                payload = fh.read()
        elif isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = data.read()
        return self._request(
            "POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/files",
            files={"file": (filename, payload)},
        )

    def download_file(self, session_id: str, filename: str) -> bytes:
        resp = self._client.get(f"/api/v1/sandbox-sessions/{_enc(session_id)}/files/{_enc(filename)}")
        if resp.status_code >= 400:
            raise _parse_error(resp, f"/files/{_enc(filename)}")
        return resp.content

    def delete_file(self, session_id: str, filename: str) -> dict:
        return self._request("DELETE", f"/api/v1/sandbox-sessions/{_enc(session_id)}/files/{_enc(filename)}")

    # ── snapshots ────────────────────────────────────────────────────────
    def list_snapshots(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/snapshots")["snapshots"]

    def create_snapshot(self, session_id: str, *, async_mode: bool = False) -> dict:
        params = {"async": "true"} if async_mode else None
        return self._request(
            "POST", f"/api/v1/sandbox-sessions/{_enc(session_id)}/snapshots", params=params
        )

    def get_operation(self, operation_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/operations/{_enc(operation_id)}")

    def wait_for_operation(self, operation_id: str, *, timeout: float = 120.0,
                           interval: float = 0.5) -> dict:
        """Poll an operation until it reaches a terminal state (W11)."""
        deadline = time.monotonic() + timeout
        while True:
            op = self.get_operation(operation_id)
            if op.get("status") in ("succeeded", "failed"):
                return op
            if time.monotonic() >= deadline:
                raise TimeoutError(f"operation {_enc(operation_id)} did not finish within {timeout}s")
            time.sleep(interval)

    def rollback_snapshot(self, session_id: str, snapshot_id: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/sandbox-sessions/{_enc(session_id)}/snapshots/{_enc(snapshot_id)}/rollback",
        )

    def delete_snapshot(self, session_id: str, snapshot_id: str) -> dict:
        return self._request(
            "DELETE", f"/api/v1/sandbox-sessions/{_enc(session_id)}/snapshots/{_enc(snapshot_id)}"
        )

    # ── logs / usage ─────────────────────────────────────────────────────
    def logs(
        self,
        session_id: str,
        *,
        limit: int = 100,
        since: str | None = None,
        action: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        if action:
            params["action"] = action
        return self._request(
            "GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/logs", params=params
        )

    def usage(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/usage")

    # ── auth (N9) ────────────────────────────────────────────────────────
    def refresh_token(self) -> dict:
        """Exchange the refresh token for a fresh access+refresh pair (N9)."""
        if not self._refresh_token:
            raise CDSApiError(401, {"message": "no refresh token available"}, "")
        resp = self._client.request(
            "POST", "/api/v1/auth/refresh", json={"refresh_token": self._refresh_token}
        )
        if resp.status_code >= 400:
            raise _parse_error(resp, "/api/v1/auth/refresh")
        body = resp.json()
        self._set_tokens(body.get("access_token"), body.get("refresh_token"))
        return body

    def _set_tokens(self, access_token: str | None, refresh_token: str | None) -> None:
        if access_token:
            self._access_token = access_token
            self._client.headers["Authorization"] = f"Bearer {access_token}"
        if refresh_token:
            self._refresh_token = refresh_token

    @classmethod
    def login(cls, base_url: str, username: str, password: str, **kwargs: Any) -> "CDSClient":
        """Authenticate and return a ready CDSClient (N9)."""
        probe = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=kwargs.get("timeout", 120.0),
            transport=kwargs.get("transport"),
        )
        try:
            resp = probe.post("/api/v1/auth/login", json={"username": username, "password": password})
            if resp.status_code >= 400:
                raise _parse_error(resp, "/api/v1/auth/login")
            body = resp.json()
        finally:
            probe.close()
        client = cls(base_url, body["access_token"], **kwargs)
        client._refresh_token = body.get("refresh_token")
        return client

    @classmethod
    def register(cls, base_url: str, username: str, email: str, password: str,
                 role: str = "buyer", **kwargs: Any) -> "CDSClient":
        """Register a user and return a ready CDSClient (N9)."""
        probe = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=kwargs.get("timeout", 120.0),
            transport=kwargs.get("transport"),
        )
        try:
            resp = probe.post("/api/v1/auth/register", json={
                "username": username, "email": email, "password": password, "role": role,
            })
            if resp.status_code >= 400:
                raise _parse_error(resp, "/api/v1/auth/register")
            body = resp.json()
        finally:
            probe.close()
        client = cls(base_url, body["access_token"], **kwargs)
        client._refresh_token = body.get("refresh_token")
        return client

    # ── contracts (N9) ───────────────────────────────────────────────────
    def list_contracts(self, *, status: str | None = None, limit: int = 20) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/contracts", params=params)

    def get_contract(self, contract_id: str) -> dict:
        return self._request("GET", f"/api/v1/contracts/{_enc(contract_id)}")

    @staticmethod
    def sign_data(contract_id: str, contract_no: str, party_role: str, timestamp: str,
                  purpose: str | None = None, purpose_scope: list | None = None) -> str:
        """Build the canonical contract sign payload (mirrors backend
        crypto_service.contract_sign_data). Sign it with your SM2 private key
        and pass the hex signature to sign_contract.

        Raises:
            CDSError: 422 / ``VALIDATION_ERROR`` when a field would make the
                payload ambiguous (an embedded ``|``, or ``,` inside a
                ``purpose_scope`` element) or longer than the column width it
                would be stored in. Such a payload is refused locally: the
                request is never sent and nothing is signed, because a
                signature over it is counterparty-reinterpretable evidence
                (OCR-A110 / OCR-A255).
        """
        errors = _contract_payload_errors(
            contract_id, contract_no, party_role, timestamp, purpose, purpose_scope,
        )
        if errors:
            raise CDSError(422, {
                "code": "VALIDATION_ERROR",
                "message": (
                    "合约签名原文不得含 `|` / `,` 分隔符，且不得超过落库列宽："
                    "有歧义的原文可被对方重新解释，SDK 侧本地拒签，未发出请求"
                ),
                "errors": errors,
            })
        base = f"CDS-SIGN|{_enc(contract_id)}|{contract_no}|{party_role}|{timestamp}"
        if purpose:
            base += f"|purpose:{purpose}"
        if purpose_scope:
            base += f"|purpose_scope:{','.join(str(s) for s in purpose_scope)}"
        return base

    def sign_contract(self, contract_id: str, signature: str, timestamp: str) -> dict:
        return self._request(
            "POST", f"/api/v1/contracts/{_enc(contract_id)}/sign",
            json={"signature": signature, "timestamp": timestamp},
        )

    def activate_contract(self, contract_id: str) -> dict:
        return self._request("POST", f"/api/v1/contracts/{_enc(contract_id)}/activate")

    def terminate_contract(self, contract_id: str, reason: str, ticket_id: str | None = None) -> dict:
        body: dict[str, Any] = {"reason": reason}
        if ticket_id:
            body["ticket_id"] = ticket_id
        return self._request("POST", f"/api/v1/contracts/{_enc(contract_id)}/terminate", json=body)

    # ── tasks (N9) ───────────────────────────────────────────────────────
    def create_task(
        self,
        session_id: str,
        task_type: str = "query",
        *,
        code: str | None = None,
        language: str = "python",
        timeout_seconds: int = 3600,
        purpose: str | None = None,
        rag_query: str | None = None,
    ) -> dict:
        """Create a sandbox task. ``task_type="rag_query"`` takes ``rag_query``
        instead of ``code`` (system-generated RAG runner).

        A87/A216: ``code``/``rag_query`` are tenant data. By default they travel
        in the JSON body and the request line keeps only
        ``session_id``/``task_type``; ``CDSClient(task_payload_in_body=False)``
        restores the legacy query-string shape for pre-body servers.
        """
        payload: dict[str, Any] = {}
        if code is not None:
            payload["code"] = code
        if language:
            payload["language"] = language
        if timeout_seconds:
            payload["timeout_seconds"] = timeout_seconds
        if purpose:
            payload["purpose"] = purpose
        if rag_query:
            payload["rag_query"] = rag_query
        if self._task_payload_in_body:
            return self._request(
                "POST",
                "/api/v1/sandbox-tasks",
                params={"session_id": session_id, "task_type": task_type},
                json=payload,
            )
        params: dict[str, Any] = {"session_id": session_id, "task_type": task_type, **payload}
        return self._request("POST", "/api/v1/sandbox-tasks", params=params)

    def submit_task(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "POST", f"/api/v1/sandbox-tasks/{_enc(task_id)}/submit", params={"session_id": session_id}
        )

    def get_task(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-tasks/{_enc(task_id)}", params={"session_id": session_id}
        )

    def get_task_result(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-tasks/{_enc(task_id)}/result", params={"session_id": session_id}
        )

    # ── proof bundle (N9) ────────────────────────────────────────────────
    def get_proof_bundle(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/proof-bundle")

    # ── inference (N5/N9) ────────────────────────────────────────────────
    def list_models(self) -> list[dict]:
        return self._request("GET", "/api/v1/inference/models")

    def invoke_model(self, model_id: str, inputs: dict, purpose: str | None = None) -> dict:
        """Invoke a registered model inside the buyer's sandbox session.
        Returns the inference task descriptor; poll ``get_task_result`` with
        the returned session_id/task_id for the output."""
        body: dict[str, Any] = {"inputs": inputs}
        if purpose:
            body["purpose"] = purpose
        return self._request("POST", f"/api/v1/inference/{_enc(model_id)}/invoke", json=body)

    def get_inference_metering(self) -> dict:
        return self._request("GET", "/api/v1/inference/metering")

    # ── ws streaming (PZ-11②) ───────────────────────────────────────────
    def request_ws_ticket(self) -> dict:
        """Mint a 30s single-use WebSocket ticket (W10 / PZ-11②).

        Returns ``{"ticket", "expires_in"}``; pair it with
        :meth:`stream_exec`, which performs the exchange itself.
        """
        return self._request("POST", "/api/v1/auth/ws-ticket")

    def stream_exec(
        self,
        session_id: str,
        code: str,
        language: str = "python",
        *,
        timeout_seconds: int | None = None,
        recv_timeout: float = 30.0,
        _ws_open: Any = None,
    ) -> Iterator[dict]:
        """Stream exec output frames for a session (W10, PZ-11②).

        Exchanges a ws-ticket, opens
        ``/api/v1/sandbox-sessions/{id}/exec/stream`` and yields server
        frames (``stdout`` / ``stderr`` / ``blocked`` / ``exit`` / ``error``).
        ``language="python"`` wraps ``code`` as ``python3 -c <code>``;
        otherwise ``code`` is run as a shell command.

        Capability matrix: only ``L0`` (bwrap) and ``k8s`` pods support
        streaming exec — other levels raise :class:`CDSApiError` with
        guidance to fall back to :meth:`execute` or :meth:`logs` polling.
        Requires the optional ``websocket-client`` dependency.
        ``timeout_seconds``/``session_id``/``language``/``recv_timeout`` are
        validated before any request (A218): a bad argument raises
        :class:`ValueError` up-front, burning zero round trips. Path
        identifiers are percent-encoded (A215). A stream that stalls past
        ``recv_timeout`` raises :class:`CDSError` (504) with
        ``truncated = True`` — never a silent "complete" end.
        """
        from . import stream as _stream

        # Validate before the first network call (get_session below):
        # timeout_seconds (A218 wave-1) plus session_id/language/recv_timeout
        # (A218 remainder) — a bad argument must never burn an HTTP round trip.
        _stream._validate_timeout_seconds(timeout_seconds)
        _stream._validate_stream_args(session_id, language, recv_timeout)
        session = self.get_session(session_id)
        level = session.get("sandbox_level") if isinstance(session, dict) else None
        if level not in _stream.SUPPORTED_STREAM_LEVELS:
            raise CDSApiError(
                400,
                "streaming exec not supported on this sandbox level",
                f"/api/v1/sandbox-sessions/{_enc(session_id)}/exec/stream",
                code="STREAM_UNSUPPORTED",
                message=(
                    f"WS streaming exec is only available on L0/k8s sessions "
                    f"(this session is {level!r}); use execute() or poll logs() "
                    f"instead, or check capability_matrix() for supported levels"
                ),
            )
        return _stream.stream_exec(
            self, session_id, code, language=language,
            timeout_seconds=timeout_seconds, recv_timeout=recv_timeout,
            _ws_open=_ws_open,
        )

    # ── data marketplace (PZ-11①, buyer journey) ───────────────────────
    def list_data_products(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        product_type: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        if product_type:
            params["product_type"] = product_type
        return self._request("GET", "/api/v1/data-products", params=params)

    def get_data_product(self, product_id: str) -> dict:
        return self._request("GET", f"/api/v1/data-products/{_enc(product_id)}")

    def list_data_resources(self) -> list[dict]:
        """List data resources owned by the calling user."""
        return self._request("GET", "/api/v1/data-resources/")

    def get_data_resource(self, resource_id: str) -> dict:
        return self._request("GET", f"/api/v1/data-resources/{_enc(resource_id)}")

    def search_catalog(
        self,
        q: str | None = None,
        *,
        product_type: str | None = None,
        industry: str | None = None,
        security_level: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """Search published data products in the catalog."""
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if q:
            params["q"] = q
        if product_type:
            params["product_type"] = product_type
        if industry:
            params["industry"] = industry
        if security_level:
            params["security_level"] = security_level
        return self._request("GET", "/api/v1/catalog", params=params)

    # ── training (PZ-11①) ───────────────────────────────────────────────
    def list_training_jobs(self, *, status: str | None = None, skip: int = 0,
                           limit: int = 20) -> dict:
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/training/jobs", params=params)

    def get_training_job(self, job_id: str) -> dict:
        return self._request("GET", f"/api/v1/training/jobs/{_enc(job_id)}")

    def training_job_status(self, job_id: str) -> str:
        """Return the ``status`` of a training job (e.g. ``running``)."""
        job = self.get_training_job(job_id)
        return job.get("status", "") if isinstance(job, dict) else ""

    # ── federation (PZ-11①) ────────────────────────────────────────────
    def list_federation_spaces(self, *, status: str | None = None) -> list[dict]:
        """List federation trust spaces / relationships (admin/operator)."""
        params = {"status": status} if status else None
        return self._request("GET", "/api/v1/federation/trusts", params=params)

    # ── monitoring / compliance (PZ-11①) ───────────────────────────────
    def get_monitoring_summary(self) -> dict:
        """Get the system monitoring metrics summary (30s server cache)."""
        return self._request("GET", "/api/v1/monitoring/stats")

    def get_compliance_report(self) -> dict:
        """Get the latest compliance report (anchored audit statistics)."""
        return self._request("GET", "/api/v1/audit/compliance-report")

    # ── session audit / network policy / cleanup (PZ-11①) ──────────────
    def get_session_audit(self, session_id: str, *, category: str | None = None,
                          limit: int = 200) -> dict:
        """Get in-sandbox activity audit events for a session.

        ``category``: data_access / network / application.
        """
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category
        return self._request(
            "GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/audit", params=params
        )

    def cleanup_expired_sessions(self) -> dict:
        """Trigger a cleanup pass over expired sessions (operator)."""
        return self._request("POST", "/api/v1/sandbox-sessions/cleanup-expired")

    def get_session_network_policy(self, session_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-sessions/{_enc(session_id)}/network-policy"
        )

    def update_session_network_policy(self, session_id: str,
                                      policy: dict) -> dict:
        """Update the session network policy (mode / allowlists / active)."""
        return self._request(
            "PUT", f"/api/v1/sandbox-sessions/{_enc(session_id)}/network-policy", json=policy
        )

    # ── capability matrix (PZ-13 / PZ-11) ──────────────────────────────
    def capability_matrix(self) -> dict:
        """Get the authoritative sandbox-level capability matrix
        (``{level x capability} -> real | simulated | unsupported | planned``)."""
        return self._request("GET", "/api/v1/meta/capability-matrix")

    # ── contract gateway (PZ-11⑥ app credentials) ──────────────────────
    def gateway_query(self, contract_id: str, sql: str, product_id: str,
                      format: str = "json") -> dict:
        """Run a governed SQL query through the contract gateway.

        Authenticated by the client's gateway credential (``X-App-Id`` /
        ``X-App-Secret`` headers when built with ``app_id``/``app_secret``,
        or the Bearer token otherwise); contract binding is enforced server-side.
        """
        body: dict[str, Any] = {"sql": sql, "product_id": product_id, "format": format}
        return self._request("POST", f"/api/v1/gateway/{_enc(contract_id)}/query", json=body)

    # ── flow orchestration: notebook (SS-15 stage 2) ─────────────────
    def list_notebooks(self, *, contract_id: str | None = None, status: str | None = None,
                       page: int = 1, page_size: int = 20) -> dict:
        """List notebooks visible to the caller (``{items, total, page, page_size}``)."""
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if contract_id:
            params["contract_id"] = contract_id
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/notebooks", params=params)

    def create_notebook(self, title: str, path: str, *, contract_id: str | None = None,
                        session_id: str | None = None, content: Any = None,
                        language: str = "python") -> dict:
        """Create a notebook. ``content`` is a nbformat v4 document or omitted for an empty one."""
        body: dict[str, Any] = {
            "title": title,
            "path": path,
            "language": language,
            **_compact({"contract_id": contract_id, "session_id": session_id, "content": content}),
        }
        return self._request("POST", "/api/v1/notebooks", json=body)

    def get_notebook(self, notebook_id: str) -> dict:
        """Read one notebook, including its persisted ``content`` document and ``version``."""
        return self._request("GET", f"/api/v1/notebooks/{_enc(notebook_id)}")

    def update_notebook(self, notebook_id: str, *, title: str | None = None,
                        content: Any = None, session_id: str | None = None) -> dict:
        """Save the notebook; every successful save bumps ``version`` server-side."""
        return self._request(
            "PUT", f"/api/v1/notebooks/{_enc(notebook_id)}",
            json=_compact({"title": title, "content": content, "session_id": session_id}),
        )

    def archive_notebook(self, notebook_id: str) -> None:
        """Archive (soft-delete) a notebook. Returns ``None`` on 204."""
        return self._request("DELETE", f"/api/v1/notebooks/{_enc(notebook_id)}")

    def start_notebook_dev_session(self, notebook_id: str) -> dict:
        """Open/reuse the test sandbox session backing this notebook.

        Fails with 400 ``RUNTIME_UNAVAILABLE`` (``detail.reason`` carries the cause, e.g.
        ``dataplane_disabled``) when no sandbox runtime is reachable — never simulated.
        """
        return self._request("POST", f"/api/v1/notebooks/{_enc(notebook_id)}/dev-session")

    def execute_notebook_cell(self, notebook_id: str, code: str, *, cell_index: int | None = None,
                             persist: bool = False) -> dict:
        """Execute one cell in the notebook's sandbox session and return the output envelope."""
        body: dict[str, Any] = {"code": code, "persist": persist}
        if cell_index is not None:
            body["cell_index"] = cell_index
        return self._request("POST", f"/api/v1/notebooks/{_enc(notebook_id)}/cells/execute", json=body)

    def get_notebook_jupyter(self, notebook_id: str) -> dict:
        """Report whether the notebook's session exposes a native Jupyter UI."""
        return self._request("GET", f"/api/v1/notebooks/{_enc(notebook_id)}/jupyter")

    def run_notebook(self, notebook_id: str) -> dict:
        """Execute every code cell in order (per-cell results, stop-on-failure semantics)."""
        return self._request("POST", f"/api/v1/notebooks/{_enc(notebook_id)}/run")

    # ── flow orchestration: workload (test → publish gate) ──────────────
    def list_workloads(self, *, contract_id: str | None = None, env: str | None = None,
                       status: str | None = None, page: int = 1, page_size: int = 20) -> dict:
        """List workloads (``env``: ``test`` | ``production``)."""
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if contract_id:
            params["contract_id"] = contract_id
        if env:
            params["env"] = env
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/workloads", params=params)

    def create_workload(self, name: str, *, contract_id: str | None = None,
                        notebook_id: str | None = None, entrypoint: str | None = None,
                        language: str | None = None, resource_spec: dict | None = None,
                        data_product_ids: list[str] | None = None) -> dict:
        """Create a workload. With ``notebook_id`` and no ``entrypoint``, the backend derives
        the entrypoint (and its hash) from the notebook's code cells."""
        body: dict[str, Any] = {
            "name": name,
            **_compact({
                "contract_id": contract_id,
                "notebook_id": notebook_id,
                "entrypoint": entrypoint,
                "language": language,
                "resource_spec": resource_spec,
                "data_product_ids": data_product_ids,
            }),
        }
        return self._request("POST", "/api/v1/workloads", json=body)

    def get_workload(self, workload_id: str) -> dict:
        return self._request("GET", f"/api/v1/workloads/{_enc(workload_id)}")

    def test_workload(self, workload_id: str) -> dict:
        """Run the workload in a test sandbox. ``passed=False`` is a normal 200 outcome."""
        return self._request("POST", f"/api/v1/workloads/{_enc(workload_id)}/test")

    def list_workload_test_runs(self, workload_id: str, *, page: int = 1,
                                page_size: int = 20) -> dict:
        """History of test runs, including check items and the failure reason."""
        return self._request(
            "GET", f"/api/v1/workloads/{_enc(workload_id)}/test-runs",
            params={"page": page, "page_size": page_size},
        )

    def get_workload_test_run(self, workload_id: str, run_id: str) -> dict:
        return self._request("GET", f"/api/v1/workloads/{_enc(workload_id)}/test-runs/{_enc(run_id)}")

    def publish_workload(self, workload_id: str) -> dict:
        """Publish to production. Rejected with 412 ``WORKLOAD_NOT_PUBLISHED`` family codes
        until the latest entrypoint hash passed a test run."""
        return self._request("POST", f"/api/v1/workloads/{_enc(workload_id)}/publish")

    def rollback_workload(self, workload_id: str) -> dict:
        """Roll the production slot back to the previously published revision."""
        return self._request("POST", f"/api/v1/workloads/{_enc(workload_id)}/rollback")

    def retire_workload(self, workload_id: str) -> dict:
        """Retire the workload (no further runs or OpenAPI exposure)."""
        return self._request("POST", f"/api/v1/workloads/{_enc(workload_id)}/retire")

    # ── flow orchestration: delivery (stage 3, three channels) ─────────
    def create_dataset_bundle(self, session_id: str, contract_id: str,
                              watermark: str | None = None) -> dict:
        """Package the session outputs into a downloadable dataset bundle (watermarked)."""
        body: dict[str, Any] = {"session_id": session_id, "contract_id": contract_id}
        if watermark:
            body["watermark"] = watermark
        return self._request("POST", "/api/v1/deliveries/dataset/bundle", json=body)

    def download_dataset_bundle(self, bundle_id: str) -> bytes:
        """Download a bundle zip. Only contract parties may fetch it (404 otherwise)."""
        path = f"/api/v1/deliveries/dataset/{_enc(bundle_id)}/download"
        resp = self._client.get(path)
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        return resp.content

    def get_contract_openapi(self, contract_id: str) -> dict:
        """OpenAPI 3.1 document for the contract's published workloads (``paths`` is empty
        when nothing is published — unpublished workloads are never advertised)."""
        return self._request("GET", f"/api/v1/deliveries/openapi/contract/{_enc(contract_id)}")

    def create_openapi_endpoint(self, contract_id: str, name: str, *,
                                workload_id: str | None = None, slug: str | None = None,
                                request_schema: dict | None = None,
                                rate_limit_per_minute: int | None = None,
                                quota_total: int | None = None) -> dict:
        """Open an external ``POST /open/v1/{slug}`` entry point (requires ``open_api``
        among the contract's delivery methods, else 403 ``DELIVERY_METHOD_NOT_ALLOWED``)."""
        body: dict[str, Any] = {
            "contract_id": contract_id,
            "name": name,
            **_compact({
                "workload_id": workload_id,
                "slug": slug,
                "request_schema": request_schema,
                "rate_limit_per_minute": rate_limit_per_minute,
                "quota_total": quota_total,
            }),
        }
        return self._request("POST", "/api/v1/deliveries/openapi/endpoints", json=body)

    def list_openapi_endpoints(self, *, contract_id: str | None = None) -> dict:
        params: dict[str, Any] = {}
        if contract_id:
            params["contract_id"] = contract_id
        return self._request("GET", "/api/v1/deliveries/openapi/endpoints", params=params)

    def update_openapi_endpoint(self, endpoint_id: str, *, status: str | None = None,
                                rate_limit_per_minute: int | None = None,
                                quota_total: int | None = None) -> dict:
        return self._request(
            "PATCH", f"/api/v1/deliveries/openapi/endpoints/{_enc(endpoint_id)}",
            json=_compact({
                "status": status,
                "rate_limit_per_minute": rate_limit_per_minute,
                "quota_total": quota_total,
            }),
        )

    def delete_openapi_endpoint(self, endpoint_id: str) -> None:
        return self._request("DELETE", f"/api/v1/deliveries/openapi/endpoints/{_enc(endpoint_id)}")

    def invoke_open_endpoint(self, slug: str, payload: dict) -> dict:
        """Call the external delivery entry point (agent-side surface; authenticated by a
        platform API key, not a user token)."""
        return self._request("POST", f"/open/v1/{_enc(slug)}", json=payload)

    def create_webhook(self, name: str, url: str, events: list[str], *,
                       contract_id: str | None = None) -> dict:
        """Subscribe to result-push events. The plaintext secret is returned once."""
        body: dict[str, Any] = {"name": name, "url": url, "events": events}
        if contract_id:
            body["contract_id"] = contract_id
        return self._request("POST", "/api/v1/deliveries/webhooks", json=body)

    def list_webhooks(self, *, contract_id: str | None = None) -> dict:
        params: dict[str, Any] = {}
        if contract_id:
            params["contract_id"] = contract_id
        return self._request("GET", "/api/v1/deliveries/webhooks", params=params)

    def update_webhook(self, subscription_id: str, *, name: str | None = None,
                       url: str | None = None, events: list[str] | None = None,
                       status: str | None = None) -> dict:
        return self._request(
            "PATCH", f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}",
            json=_compact({"name": name, "url": url, "events": events, "status": status}),
        )

    def delete_webhook(self, subscription_id: str) -> None:
        """Delete a subscription; delivery history is retained for audit (0 = forever)."""
        return self._request("DELETE", f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}")

    def list_webhook_deliveries(self, subscription_id: str, *, status: str | None = None,
                                page: int = 1, page_size: int = 20) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        return self._request(
            "GET", f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}/deliveries", params=params
        )

    def redeliver_webhook_delivery(self, subscription_id: str, delivery_id: str) -> dict:
        """Force a redelivery attempt (backoff schedule restarts)."""
        return self._request(
            "POST",
            f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}/deliveries/{_enc(delivery_id)}/redeliver",
        )

    def rotate_webhook_secret(self, subscription_id: str) -> dict:
        """Rotate the HMAC signing secret; the new plaintext is returned once."""
        return self._request(
            "POST", f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}/rotate-secret"
        )

    def test_webhook(self, subscription_id: str) -> dict:
        """Send a probe event so the receiver can be verified before real results flow."""
        return self._request("POST", f"/api/v1/deliveries/webhooks/{_enc(subscription_id)}/test")

    # ── flow orchestration: scheduled compute + contract flow config ────
    def trigger_compute_run(self, contract_id: str) -> dict:
        """Trigger a compute run now (``trigger=manual``)."""
        return self._request("POST", "/api/v1/compute/runs", json={"contract_id": contract_id})

    def list_compute_runs(self, *, contract_id: str | None = None, status: str | None = None,
                          page: int = 1, page_size: int = 20) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if contract_id:
            params["contract_id"] = contract_id
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/compute/runs", params=params)

    def get_compute_run(self, run_id: str) -> dict:
        return self._request("GET", f"/api/v1/compute/runs/{_enc(run_id)}")

    def cancel_compute_run(self, run_id: str) -> dict:
        return self._request("POST", f"/api/v1/compute/runs/{_enc(run_id)}/cancel")

    def get_compute_schedule(self, *, contract_id: str | None = None) -> dict:
        """Scheduled-compute plan per contract (``next_compute_at`` is server-owned)."""
        params: dict[str, Any] = {}
        if contract_id:
            params["contract_id"] = contract_id
        return self._request("GET", "/api/v1/compute/schedule", params=params)

    def update_contract_flow_config(self, contract_id: str, *,
                                    delivery_methods: list[str] | None = None,
                                    auto_compute: bool | None = None,
                                    compute_frequency: str | None = None,
                                    compute_minute: int | None = None,
                                    compute_hour: int | None = None,
                                    compute_tz: str | None = None,
                                    sandbox_resource_spec: dict | None = None) -> dict:
        """Set the contract's delivery methods, scheduled-compute plan and sandbox sizing."""
        body = _compact({
            "delivery_methods": delivery_methods,
            "auto_compute": auto_compute,
            "compute_frequency": compute_frequency,
            "compute_minute": compute_minute,
            "compute_hour": compute_hour,
            "compute_tz": compute_tz,
            "sandbox_resource_spec": sandbox_resource_spec,
        })
        return self._request("POST", f"/api/v1/contracts/{_enc(contract_id)}/flow-config", json=body)
