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
        self._closed = False

    async def _get_client(self) -> httpx.AsyncClient:
        # A160: after close() the client must STAY gone. Rebuilding on is_closed
        # handed every post-shutdown request a fresh AsyncClient that nothing
        # would ever close (app/main.py closes us in lifespan shutdown).
        if self._closed:
            logger.warning("OPA client requested after shutdown — refusing to rebuild an unclosable client")
            raise RuntimeError("OPAClient is shut down; create a new instance")
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=_OPA_TIMEOUT,
            )
        return self._client

    @property
    def is_shutdown(self) -> bool:
        """True once close() ran — the terminal state, readable without triggering it.

        Without this accessor a post-shutdown caller sees the same False/None as a
        transiently unreachable OPA sidecar, i.e. a permanently absent PDP reads as
        a momentary outage (A160b).
        """
        return self._closed

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
            logger.error(f"OPA push error: {e}", exc_info=True)
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
            logger.error(f"OPA evaluate error: {e}", exc_info=True)
            return None

    async def delete_policy(self, package_path: str) -> bool:
        """Delete a policy from OPA."""
        try:
            client = await self._get_client()
            path = package_path.replace(".", "/")
            resp = await client.delete(f"/v1/policies/{path}")
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"OPA delete error: {e}", exc_info=True)
            return False

    async def health(self) -> bool:
        """Check if OPA is healthy."""
        try:
            client = await self._get_client()
            resp = await client.get("/health")
            return resp.status_code == 200
        except Exception:
            logger.debug("OPA health check failed", exc_info=True)
            return False

    async def close(self):
        """Shut the client down for good: idempotent, terminal, never reopened.

        Reopening would hand a post-shutdown request an AsyncClient that nothing
        will ever close again; refusing is the honest half. `is_shutdown` plus
        `PolicyEvaluator`'s decision disclosure are the visible half (A160b).
        """
        client, self._client = self._client, None
        self._closed = True
        if client and not client.is_closed:
            await client.aclose()


# Singleton
opa_client = OPAClient()
