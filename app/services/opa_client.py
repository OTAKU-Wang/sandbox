"""OPA (Open Policy Agent) HTTP client.

Pushes Rego policies to OPA and evaluates access requests via the OPA REST API.
"""
import logging
import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_OPA_TIMEOUT = 5.0  # seconds


class OPAClient:
    """HTTP client for OPA sidecar."""

    def __init__(self, base_url: str | None = None):
        self._base_url = (base_url or get_settings().OPA_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=_OPA_TIMEOUT,
            )
        return self._client

    async def push_policy(self, package_path: str, rego_source: str) -> bool:
        """Push a Rego policy to OPA.

        Args:
            package_path: OPA policy path, e.g. "cds/policies/contract_123"
            rego_source: The Rego source code

        Returns:
            True if policy was pushed successfully.
        """
        try:
            client = await self._get_client()
            # OPA PUT /v1/policies/{path} — upload a policy module
            path = package_path.replace(".", "/")
            resp = await client.put(
                f"/v1/policies/{path}",
                content=rego_source,
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code in (200, 204):
                logger.info(f"Policy pushed to OPA: {path}")
                return True
            else:
                logger.error(f"OPA push failed ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"OPA push error: {e}")
            return False

    async def evaluate(self, package_path: str, input_data: dict) -> dict | None:
        """Evaluate a policy in OPA.

        Args:
            package_path: OPA policy path, e.g. "cds/policies/contract_123"
            input_data: The input document for policy evaluation

        Returns:
            OPA result dict, or None on error.
        """
        try:
            client = await self._get_client()
            # OPA POST /v1/data/{path} — evaluate policy
            path = package_path.replace(".", "/")
            resp = await client.post(
                f"/v1/data/{path}",
                json={"input": input_data},
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"OPA evaluate failed ({resp.status_code}): {resp.text}")
                return None
        except Exception as e:
            logger.error(f"OPA evaluate error: {e}")
            return None

    async def delete_policy(self, package_path: str) -> bool:
        """Delete a policy from OPA."""
        try:
            client = await self._get_client()
            path = package_path.replace(".", "/")
            resp = await client.delete(f"/v1/policies/{path}")
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"OPA delete error: {e}")
            return False

    async def health(self) -> bool:
        """Check if OPA is healthy."""
        try:
            client = await self._get_client()
            resp = await client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Singleton
opa_client = OPAClient()
