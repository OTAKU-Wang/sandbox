"""Sync HTTP client for the CDS sandbox API (httpx-based, stdlib-free beyond httpx).

Error model: any non-2xx response raises :class:`CDSError` with the parsed
``detail`` from the FastAPI error body when available.
"""
from __future__ import annotations

import time
from typing import Any, BinaryIO, Iterable, Iterator

import httpx

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
    """

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


def _retry_after_seconds(resp: httpx.Response) -> float:
    ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if ra is not None:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass
    return 1.0


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
    """Build a CDSApiError from a non-2xx response (W2 shape or legacy)."""
    try:
        payload = resp.json()
        detail = payload.get("detail", payload)
    except ValueError:
        payload = None
        detail = resp.text
    return CDSApiError(
        resp.status_code,
        detail,
        path,
        code=str(payload.get("code")) if isinstance(payload, dict) and payload.get("code") else None,
        message=str(payload.get("message")) if isinstance(payload, dict) and payload.get("message") else None,
        request_id=str(payload.get("request_id")) if isinstance(payload, dict) and payload.get("request_id") else None,
        headers=dict(resp.headers),
    )


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
        # PZ-11③ / docs/error-codes.md: 410 SESSION_TERMINATED is terminal —
        # clients must stop retrying, so it is excluded from the retry set.
        if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
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
        if resp.status_code in (429, 503) and retries > 0:
            time.sleep(min(_retry_after_seconds(resp), 5.0))
            return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
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
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}")

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
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/terminate")

    def execute(self, session_id: str, code: str, language: str = "python") -> dict:
        return self._request(
            "POST", f"/api/v1/sandbox-sessions/{session_id}/execute",
            json={"code": code, "language": language},
        )

    def exec_command(self, session_id: str, command: str, timeout_seconds: int | None = None) -> dict:
        body: dict[str, Any] = {"command": command}
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/exec", json=body)

    def pause(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/pause")

    def resume(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/resume")

    def refresh(self, session_id: str, extend_seconds: int | None = None) -> dict:
        body = {"extend_seconds": extend_seconds} if extend_seconds is not None else {}
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/refreshes", json=body)

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
            f"session {session_id} did not reach {sorted(targets)} within {timeout}s "
            f"(last status: {last.get('status')!r})"
        )

    # ── files ────────────────────────────────────────────────────────────
    def list_files(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}/files")["files"]

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
            "POST", f"/api/v1/sandbox-sessions/{session_id}/files",
            files={"file": (filename, payload)},
        )

    def download_file(self, session_id: str, filename: str) -> bytes:
        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")
        if resp.status_code >= 400:
            raise _parse_error(resp, f"/files/{filename}")
        return resp.content

    def delete_file(self, session_id: str, filename: str) -> dict:
        return self._request("DELETE", f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")

    # ── snapshots ────────────────────────────────────────────────────────
    def list_snapshots(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}/snapshots")["snapshots"]

    def create_snapshot(self, session_id: str, *, async_mode: bool = False) -> dict:
        params = {"async": "true"} if async_mode else None
        return self._request(
            "POST", f"/api/v1/sandbox-sessions/{session_id}/snapshots", params=params
        )

    def get_operation(self, operation_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/operations/{operation_id}")

    def wait_for_operation(self, operation_id: str, *, timeout: float = 120.0,
                           interval: float = 0.5) -> dict:
        """Poll an operation until it reaches a terminal state (W11)."""
        deadline = time.monotonic() + timeout
        while True:
            op = self.get_operation(operation_id)
            if op.get("status") in ("succeeded", "failed"):
                return op
            if time.monotonic() >= deadline:
                raise TimeoutError(f"operation {operation_id} did not finish within {timeout}s")
            time.sleep(interval)

    def rollback_snapshot(self, session_id: str, snapshot_id: str) -> dict:
        return self._request(
            "POST",
            f"/api/v1/sandbox-sessions/{session_id}/snapshots/{snapshot_id}/rollback",
        )

    def delete_snapshot(self, session_id: str, snapshot_id: str) -> dict:
        return self._request(
            "DELETE", f"/api/v1/sandbox-sessions/{session_id}/snapshots/{snapshot_id}"
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
            "GET", f"/api/v1/sandbox-sessions/{session_id}/logs", params=params
        )

    def usage(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}/usage")

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
        return self._request("GET", f"/api/v1/contracts/{contract_id}")

    @staticmethod
    def sign_data(contract_id: str, contract_no: str, party_role: str, timestamp: str,
                  purpose: str | None = None, purpose_scope: list | None = None) -> str:
        """Build the canonical contract sign payload (mirrors backend
        crypto_service.contract_sign_data). Sign it with your SM2 private key
        and pass the hex signature to sign_contract."""
        base = f"CDS-SIGN|{contract_id}|{contract_no}|{party_role}|{timestamp}"
        if purpose:
            base += f"|purpose:{purpose}"
        if purpose_scope:
            base += f"|purpose_scope:{','.join(str(s) for s in purpose_scope)}"
        return base

    def sign_contract(self, contract_id: str, signature: str, timestamp: str) -> dict:
        return self._request(
            "POST", f"/api/v1/contracts/{contract_id}/sign",
            json={"signature": signature, "timestamp": timestamp},
        )

    def activate_contract(self, contract_id: str) -> dict:
        return self._request("POST", f"/api/v1/contracts/{contract_id}/activate")

    def terminate_contract(self, contract_id: str, reason: str, ticket_id: str | None = None) -> dict:
        body: dict[str, Any] = {"reason": reason}
        if ticket_id:
            body["ticket_id"] = ticket_id
        return self._request("POST", f"/api/v1/contracts/{contract_id}/terminate", json=body)

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
        instead of ``code`` (system-generated RAG runner)."""
        params: dict[str, Any] = {"session_id": session_id, "task_type": task_type}
        if code is not None:
            params["code"] = code
        if language:
            params["language"] = language
        if timeout_seconds:
            params["timeout_seconds"] = timeout_seconds
        if purpose:
            params["purpose"] = purpose
        if rag_query:
            params["rag_query"] = rag_query
        return self._request("POST", "/api/v1/sandbox-tasks", params=params)

    def submit_task(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "POST", f"/api/v1/sandbox-tasks/{task_id}/submit", params={"session_id": session_id}
        )

    def get_task(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-tasks/{task_id}", params={"session_id": session_id}
        )

    def get_task_result(self, session_id: str, task_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-tasks/{task_id}/result", params={"session_id": session_id}
        )

    # ── proof bundle (N9) ────────────────────────────────────────────────
    def get_proof_bundle(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}/proof-bundle")

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
        return self._request("POST", f"/api/v1/inference/{model_id}/invoke", json=body)

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
        """
        from . import stream as _stream

        session = self.get_session(session_id)
        level = session.get("sandbox_level") if isinstance(session, dict) else None
        if level not in _stream.SUPPORTED_STREAM_LEVELS:
            raise CDSApiError(
                400,
                "streaming exec not supported on this sandbox level",
                f"/api/v1/sandbox-sessions/{session_id}/exec/stream",
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
        return self._request("GET", f"/api/v1/data-products/{product_id}")

    def list_data_resources(self) -> list[dict]:
        """List data resources owned by the calling user."""
        return self._request("GET", "/api/v1/data-resources/")

    def get_data_resource(self, resource_id: str) -> dict:
        return self._request("GET", f"/api/v1/data-resources/{resource_id}")

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
        return self._request("GET", f"/api/v1/training/jobs/{job_id}")

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
            "GET", f"/api/v1/sandbox-sessions/{session_id}/audit", params=params
        )

    def cleanup_expired_sessions(self) -> dict:
        """Trigger a cleanup pass over expired sessions (operator)."""
        return self._request("POST", "/api/v1/sandbox-sessions/cleanup-expired")

    def get_session_network_policy(self, session_id: str) -> dict:
        return self._request(
            "GET", f"/api/v1/sandbox-sessions/{session_id}/network-policy"
        )

    def update_session_network_policy(self, session_id: str,
                                      policy: dict) -> dict:
        """Update the session network policy (mode / allowlists / active)."""
        return self._request(
            "PUT", f"/api/v1/sandbox-sessions/{session_id}/network-policy", json=policy
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
        return self._request("POST", f"/api/v1/gateway/{contract_id}/query", json=body)
