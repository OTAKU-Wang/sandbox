"""Sync HTTP client for the CDS sandbox API (httpx-based, stdlib-free beyond httpx).

Error model: any non-2xx response raises :class:`CDSError` with the parsed
``detail`` from the FastAPI error body when available.
"""
from __future__ import annotations

import time
from typing import Any, BinaryIO, Iterable

import httpx

_TERMINAL_STATUSES = ("completed", "terminated", "failed", "expired")


class CDSError(RuntimeError):
    """API error with the HTTP status and the server-provided detail."""

    def __init__(self, status_code: int, detail: Any, url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(f"CDSError {status_code} on {url}: {detail}")


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
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CDSError(resp.status_code, detail, path)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

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
        return self._request("POST", "/api/v1/sandbox-sessions", json=body)

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}")

    def list_sessions(
        self, *, status: str | None = None, page: int = 1, page_size: int = 20
    ) -> dict:
        return self._request(
            "GET",
            "/api/v1/sandbox-sessions",
            params={
                "skip": (page - 1) * page_size,
                "limit": page_size,
                **({"status": status} if status else {}),
            },
        )

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
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CDSError(resp.status_code, detail, f"/files/{filename}")
        return resp.content

    def delete_file(self, session_id: str, filename: str) -> dict:
        return self._request("DELETE", f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")

    # ── snapshots ────────────────────────────────────────────────────────
    def list_snapshots(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/api/v1/sandbox-sessions/{session_id}/snapshots")["snapshots"]

    def create_snapshot(self, session_id: str) -> dict:
        return self._request("POST", f"/api/v1/sandbox-sessions/{session_id}/snapshots")

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
