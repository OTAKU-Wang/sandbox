"""Sync HTTP client for the CDS sandbox API (httpx-based, stdlib-free beyond httpx).

Error model: any non-2xx response raises :class:`CDSError` with the parsed
``detail`` from the FastAPI error body when available.
"""
from __future__ import annotations

import time
from typing import Any, BinaryIO, Iterable, Iterator

import httpx

_TERMINAL_STATUSES = ("completed", "terminated", "failed", "expired")


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
    """Small typed wrapper over the ``/sandbox-sessions`` surface."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 120.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", **(headers or {})},
            transport=transport,
        )

    # ── low level ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _request_with_headers(self, method: str, path: str, **kwargs: Any) -> tuple[Any, dict]:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise _parse_error(resp, path)
        body = None if resp.status_code == 204 or not resp.content else resp.json()
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
