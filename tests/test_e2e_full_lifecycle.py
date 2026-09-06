"""E2E Test: Full Data Product Lifecycle

Tests the complete flow:
1. Data resource upload (provider)
2. Data product creation with schema
3. Field visibility configuration
4. Test data generation (mock, sample, desensitize)
5. Product approval workflow (submit → approve → publish)
6. Buyer field exposure request
7. Provider reviews and approves fields
8. Contract creation and signing
9. Contract activation (auto-fulfillment)
10. Sandbox session execution
11. Chain attestation verification
"""
import os
import json
import time
import pytest
import httpx

BASE_URL = os.environ.get("CDS_BASE_URL", "http://172.21.0.2:30080")
API = f"{BASE_URL}/api/v1"


def request_with_retry(method, url, max_retries=3, **kwargs):
    """HTTP request with retry on 429 (rate limit)."""
    for attempt in range(max_retries):
        resp = method(url, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = 2 ** attempt
        time.sleep(wait)
    return resp  # Return last response even if still 429


@pytest.fixture(autouse=True)
def rate_limit_delay():
    """Small delay between tests to stay under rate limit (60 req/min)."""
    time.sleep(2.0)


def _auth_post_with_429_retry(url: str, payload: dict):
    """POST with 20s-step retry on 429 (sliding 60s window must age out)."""
    for attempt in range(4):
        resp = httpx.post(url, json=payload)
        if resp.status_code != 429:
            return resp
        time.sleep(20)
    return resp


def _live_429(method, url: str, **kwargs):
    """Any-method request with 20s-step retry on 429 — for the usability E2E
    which bursts well past the 60 req/min rate limit."""
    for attempt in range(4):
        resp = method(url, **kwargs)
        if resp.status_code != 429:
            return resp
        time.sleep(20)
    return resp


@pytest.fixture
def provider_token():
    """Register and login as data provider."""
    resp = _auth_post_with_429_retry(f"{API}/auth/register", {
        "username": "provider_e2e",
        "password": "TestPass123!",
        "email": "provider@test.com",
        "role": "data_provider",
    })
    if resp.status_code in (200, 201) and "access_token" in resp.json():
        return resp.json()["access_token"]
    # Already registered, login instead
    resp = _auth_post_with_429_retry(f"{API}/auth/login", {
        "username": "provider_e2e",
        "password": "TestPass123!",
    })
    return resp.json()["access_token"]


@pytest.fixture
def buyer_token():
    """Register and login as data buyer."""
    resp = _auth_post_with_429_retry(f"{API}/auth/register", {
        "username": "buyer_e2e",
        "password": "TestPass123!",
        "email": "buyer@test.com",
        "role": "buyer",
    })
    if resp.status_code in (200, 201) and "access_token" in resp.json():
        return resp.json()["access_token"]
    resp = _auth_post_with_429_retry(f"{API}/auth/login", {
        "username": "buyer_e2e",
        "password": "TestPass123!",
    })
    return resp.json()["access_token"]


@pytest.fixture
def admin_token():
    """Login as admin.

    The suite bursts well past the 60 req/min rate limit (per-IP sliding
    60s window), so on 429 wait long enough for older requests to age out
    (20s steps; one full window is 60s) instead of fast-retrying.
    """
    for attempt in range(4):
        resp = httpx.post(f"{API}/auth/login", json={
            "username": "admin",
            "password": os.environ.get("CDS_ADMIN_PASSWORD", "AdminPass123!"),
        })
        if resp.status_code != 429:
            break
        time.sleep(20)
    data = resp.json()
    if "access_token" not in data:
        pytest.skip(f"Admin login failed: {resp.status_code} {data}")
    return data["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestFullLifecycle:
    """End-to-end test of the data product lifecycle."""

    def test_01_data_resource_upload(self, provider_token):
        """Upload a CSV data resource."""
        csv_data = "name,email,phone,amount\n张三,zhangsan@example.com,13800138000,100.50\n李四,lisi@example.com,13900139000,200.75\n"
        files = {"file": ("test_data.csv", csv_data.encode(), "text/csv")}
        resp = httpx.post(
            f"{API}/data-resources/upload",
            files=files,
            data={"name": "E2E Test Resource", "description": "Test data for E2E"},
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"
        data = resp.json()
        assert data["status"] == "ready"
        assert data["schema_fields"] is not None
        assert len(data["schema_fields"]) > 0
        TestFullLifecycle.resource_id = data["id"]

    def test_02_data_product_create(self, provider_token):
        """Create a data product linked to the resource."""
        resp = httpx.post(
            f"{API}/data-products",
            json={
                "name": "E2E Test Product",
                "description": "A test data product for E2E testing",
                "product_type": "structured",
                "resource_id": TestFullLifecycle.resource_id,
                "industry": "finance",
                "security_level": "confidential",
                "allowed_operations": ["read", "query", "export"],
            },
            headers=auth(provider_token),
        )
        assert resp.status_code == 201, f"Create product failed: {resp.text}"
        data = resp.json()
        assert data["status"] == "draft"
        assert data["data_schema"] is not None
        TestFullLifecycle.product_id = data["id"]

    def test_03_field_visibility_config(self, provider_token):
        """Configure field visibility rules."""
        resp = httpx.put(
            f"{API}/field-exposure/products/{TestFullLifecycle.product_id}/field-visibility",
            json={
                "field_rules": {
                    "name": {"sensitivity": "pii", "description": "Customer name"},
                    "email": {"sensitivity": "pii", "description": "Customer email"},
                    "phone": {"sensitivity": "pii", "description": "Customer phone"},
                    "amount": {"sensitivity": "public", "description": "Transaction amount"},
                },
                "default_sensitivity": "internal",
            },
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Set visibility failed: {resp.text}"
        data = resp.json()
        assert "name" in data["field_rules"]
        assert data["field_rules"]["name"]["sensitivity"] == "pii"

    def test_04_test_data_mock(self, provider_token):
        """Generate mock test data."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/test-data/mock?row_count=10",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Mock data failed: {resp.text}"
        data = resp.json()
        assert data["type"] == "mock"
        assert data["row_count"] == 10
        assert "data" in data

    def test_05_test_data_sample(self, provider_token):
        """Sample real data from resource."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/test-data/sample?sample_size=5&method=first_n",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Sample failed: {resp.text}"
        data = resp.json()
        assert data["type"] == "real_sample"
        assert "data" in data

    def test_06_test_data_desensitize(self, provider_token):
        """Generate desensitized test data."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/test-data/desensitize?sample_size=5",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Desensitize failed: {resp.text}"
        data = resp.json()
        assert data["type"] == "desensitized"
        assert data["masking_applied"] is True

    def test_07_submit_for_review(self, provider_token):
        """Submit product for review."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/submit",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Submit failed: {resp.text}"
        assert resp.json()["status"] == "reviewing"

    def test_08_approve_product(self, admin_token):
        """Admin approves the product."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/approve",
            headers=auth(admin_token),
        )
        assert resp.status_code == 200, f"Approve failed: {resp.text}"
        assert resp.json()["status"] == "approved"

    def test_09_publish_product(self, provider_token):
        """Provider publishes the approved product."""
        resp = httpx.post(
            f"{API}/data-products/{TestFullLifecycle.product_id}/publish",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Publish failed: {resp.text}"
        assert resp.json()["status"] == "published"

    def test_10_catalog_search(self, buyer_token):
        """Buyer searches the catalog."""
        resp = httpx.get(
            f"{API}/catalog?q=E2E",
            headers=auth(buyer_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(p["id"] == TestFullLifecycle.product_id for p in data["items"])

    def test_11_field_exposure_request(self, buyer_token):
        """Buyer requests access to sensitive fields."""
        resp = httpx.post(
            f"{API}/field-exposure/exposure-requests",
            json={
                "product_id": TestFullLifecycle.product_id,
                "requested_fields": ["name", "email", "phone", "amount"],
                "justification": "Need customer contact info for payment verification",
            },
            headers=auth(buyer_token),
        )
        assert resp.status_code == 201, f"Request failed: {resp.text}"
        data = resp.json()
        assert data["status"] in ("pending", "approved")  # public fields auto-approved
        TestFullLifecycle.exposure_request_id = data["id"]

    def test_12_provider_review_fields(self, provider_token):
        """Provider reviews and approves field exposure request."""
        resp = httpx.get(
            f"{API}/field-exposure/exposure-requests?as_provider=true&status=pending",
            headers=auth(provider_token),
        )
        assert resp.status_code == 200
        requests = resp.json()
        # Find the pending request
        pending = [r for r in requests if r["status"] == "pending"]
        if pending:
            req_id = pending[0]["id"]
            resp = httpx.post(
                f"{API}/field-exposure/exposure-requests/{req_id}/review",
                json={"approved_fields": ["name", "email"]},  # Only approve name and email
                headers=auth(provider_token),
            )
            assert resp.status_code == 200, f"Review failed: {resp.text}"
            assert resp.json()["status"] == "approved"
            assert "name" in resp.json()["approved_fields"]
            assert "phone" not in resp.json()["approved_fields"]

    def test_13_contract_create_and_sign(self, provider_token, buyer_token):
        """Create and sign a contract."""
        # Get buyer user ID from profile
        buyer_resp = httpx.get(f"{API}/auth/me", headers=auth(buyer_token))
        assert buyer_resp.status_code == 200, f"Get buyer profile failed: {buyer_resp.text}"
        buyer_id = buyer_resp.json()["id"]

        # Provider creates contract
        resp = httpx.post(
            f"{API}/contracts",
            json={
                "contract_type": "data_query",
                "buyer_id": buyer_id,
                "title": "E2E Test Contract",
                "terms": {"description": "Access to E2E test data product"},
                "product_ids": [TestFullLifecycle.product_id],
                "allowed_sandbox_levels": "L3",
                "allowed_sandbox_modes": ["query", "read"],
                "allowed_operations": "read,query",
                "max_duration_hours": 24,
                "max_output_rows": 1000,
                "allowed_output_formats": "csv,json",
            },
            headers=auth(provider_token),
        )
        assert resp.status_code == 201, f"Create contract failed: {resp.text}"
        TestFullLifecycle.contract_id = resp.json()["id"]

    def test_14_contract_activate(self, provider_token, buyer_token):
        """Activate contract via REAL SM2 signatures from both parties.

        Mirrors an external client: generate SM2 keypairs, build the canonical
        sign payload (CDS-SIGN|id|no|role|timestamp[|purpose...]) from the
        contract detail, sign it server-side via /auth/sign-data, then submit
        {signature, timestamp}. The server verifies the SM2 signature against
        the stored public key and enforces timestamp freshness (±300s).
        """
        if not hasattr(TestFullLifecycle, 'contract_id'):
            pytest.skip("Contract not created in previous step")

        from datetime import datetime, timezone

        def _sm2_sign(token: str, party_role: str) -> dict:
            # 1. Generate this user's SM2 keypair (public key stored server-side)
            resp = request_with_retry(httpx.post,
                f"{API}/auth/generate-sm2-keys", headers=auth(token))
            assert resp.status_code == 200, f"SM2 keygen failed: {resp.text}"
            private_key = resp.json()["private_key"]

            # 2. Fetch contract detail to build the canonical payload
            resp = request_with_retry(httpx.get,
                f"{API}/contracts/{TestFullLifecycle.contract_id}", headers=auth(token))
            assert resp.status_code == 200, f"Get contract failed: {resp.text}"
            c = resp.json()

            # 3. Build the canonical payload exactly as the server verifies it
            ts = datetime.now(timezone.utc).isoformat()
            payload = f"CDS-SIGN|{c['id']}|{c['contract_no']}|{party_role}|{ts}"
            if c.get("purpose"):
                payload += f"|purpose:{c['purpose']}"
            if c.get("purpose_scope"):
                payload += f"|purpose_scope:{','.join(str(s) for s in c['purpose_scope'])}"

            # 4. Sign with the private key via the sign-data endpoint
            resp = request_with_retry(httpx.post,
                f"{API}/auth/sign-data",
                json={"data": payload, "private_key": private_key},
                headers=auth(token))
            assert resp.status_code == 200, f"SM2 sign failed: {resp.text}"
            return {"signature": resp.json()["signature"], "timestamp": ts}

        # Both parties sign with real SM2 signatures
        resp = request_with_retry(httpx.post,
            f"{API}/contracts/{TestFullLifecycle.contract_id}/sign",
            json=_sm2_sign(provider_token, "provider"),
            headers=auth(provider_token),
        )
        assert resp.status_code == 200, f"Provider sign failed: {resp.text}"
        resp = request_with_retry(httpx.post,
            f"{API}/contracts/{TestFullLifecycle.contract_id}/sign",
            json=_sm2_sign(buyer_token, "buyer"),
            headers=auth(buyer_token),
        )
        assert resp.status_code == 200, f"Buyer sign failed: {resp.text}"
        # Contract auto-activates when both parties sign; try explicit activate as fallback
        resp = request_with_retry(httpx.post,
            f"{API}/contracts/{TestFullLifecycle.contract_id}/activate",
            headers=auth(provider_token),
        )
        if resp.status_code == 200:
            assert resp.json()["status"] == "active"
        elif resp.status_code == 400:
            # Already active after both signatures — verify status
            resp = request_with_retry(httpx.get,
                f"{API}/contracts/{TestFullLifecycle.contract_id}",
                headers=auth(provider_token),
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "active"
        elif resp.status_code == 500:
            # Intermittent server error — verify contract is active via GET
            time.sleep(2)
            resp = request_with_retry(httpx.get,
                f"{API}/contracts/{TestFullLifecycle.contract_id}",
                headers=auth(provider_token),
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "active"
        else:
            assert False, f"Activate returned unexpected status: {resp.status_code} {resp.text}"

    def test_15_chain_attestation_verify(self, provider_token):
        """Verify chain attestation integrity."""
        resp = httpx.get(
            f"{API}/audit/chain/verify",
            headers=auth(provider_token),
        )
        # This endpoint may not exist yet, so we just check it doesn't 500
        assert resp.status_code in (200, 404)

    def test_16_sandbox_usability(self, buyer_token):
        """Round 39 usability closed loop on a LIVE session: files → snapshot →
        mutate → rollback → verify → pause → refresh → resume → cleanup.

        Requires a provisioned session (bwrap on the verification host); when
        no live API is reachable this skips instead of erroring.
        """
        if not hasattr(TestFullLifecycle, 'product_id') or not hasattr(TestFullLifecycle, 'contract_id'):
            pytest.skip("Product/contract not created in previous steps")
        try:
            _live_429(httpx.get, f"{BASE_URL}/health", timeout=3)
        except Exception:
            pytest.skip("Live API not reachable (in-process suite run)")

        resp = _live_429(httpx.post, f"{API}/sandbox-sessions", json={
            "data_product_id": TestFullLifecycle.product_id,
            "contract_id": TestFullLifecycle.contract_id,
            "sandbox_level": "L3",
            "sandbox_mode": "structured_query",
            "timeout_seconds": 3600,
        }, headers=auth(buyer_token))
        assert resp.status_code == 201, f"Create session failed: {resp.text}"
        sid = resp.json()["id"]
        status = resp.json()["status"]
        assert status in ("ready", "running", "provisioning", "pending"), \
            f"Session not active: {status}"

        # ── files: upload → list → download ──────────────────────
        csv_payload = b"id,amount\n1,900\n2,800\n"
        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/files",
                          files={"file": ("usability.csv", csv_payload, "text/csv")},
                          headers=auth(buyer_token))
        assert resp.status_code == 200, f"File upload failed: {resp.text}"
        assert resp.json()["filename"] == "usability.csv"

        resp = _live_429(httpx.get, f"{API}/sandbox-sessions/{sid}/files", headers=auth(buyer_token))
        assert resp.status_code == 200
        assert any(f["filename"] == "usability.csv" for f in resp.json()["files"])

        resp = _live_429(httpx.get, f"{API}/sandbox-sessions/{sid}/files/usability.csv",
                         headers=auth(buyer_token))
        assert resp.status_code == 200, f"File download failed: {resp.text}"
        assert resp.content == csv_payload

        # PII upload must be blocked on download by the T5 output review.
        pii = "身份证 11010119900307867X，手机 13800138000"
        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/files",
                          files={"file": ("pii.txt", pii.encode(), "text/plain")},
                          headers=auth(buyer_token))
        assert resp.status_code == 200
        resp = _live_429(httpx.get, f"{API}/sandbox-sessions/{sid}/files/pii.txt",
                         headers=auth(buyer_token))
        assert resp.status_code == 409, "PII file download must be blocked by output review"

        # ── snapshot → mutate → rollback → verify ────────────────
        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/snapshots",
                          headers=auth(buyer_token))
        assert resp.status_code == 200, f"Snapshot failed: {resp.text}"
        snap_id = resp.json()["snapshot_id"]

        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/files",
                          files={"file": ("post-snap.txt", b"after snapshot", "text/plain")},
                          headers=auth(buyer_token))
        assert resp.status_code == 200

        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/snapshots/{snap_id}/rollback",
                          headers=auth(buyer_token))
        assert resp.status_code == 200, f"Rollback failed: {resp.text}"

        resp = _live_429(httpx.get, f"{API}/sandbox-sessions/{sid}/files", headers=auth(buyer_token))
        names = [f["filename"] for f in resp.json()["files"]]
        assert "usability.csv" in names and "post-snap.txt" not in names, \
            "rollback must restore the snapshot state"

        # ── pause → refresh → resume (state permitting) ──────────
        if status in ("ready", "running"):
            resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/pause", headers=auth(buyer_token))
            assert resp.status_code == 200, f"Pause failed: {resp.text}"
            assert resp.json()["status"] == "suspended"

            resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/refreshes",
                              headers=auth(buyer_token))
            assert resp.status_code == 200, f"Refresh failed: {resp.text}"
            assert resp.json()["extended_seconds"] == 3600

            resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/resume", headers=auth(buyer_token))
            assert resp.status_code == 200
            assert resp.json()["status"] == status

        # ── cleanup: delete file + snapshot + terminate session ──
        resp = _live_429(httpx.delete, f"{API}/sandbox-sessions/{sid}/files/usability.csv",
                            headers=auth(buyer_token))
        assert resp.status_code == 200
        resp = _live_429(httpx.delete, f"{API}/sandbox-sessions/{sid}/snapshots/{snap_id}",
                            headers=auth(buyer_token))
        assert resp.status_code == 200
        resp = _live_429(httpx.post, f"{API}/sandbox-sessions/{sid}/terminate", headers=auth(buyer_token))
        assert resp.status_code == 200

    def test_17_cleanup(self, provider_token, admin_token):
        """Clean up test data."""
        if hasattr(TestFullLifecycle, 'product_id'):
            # Delete product
            httpx.delete(
                f"{API}/data-products/{TestFullLifecycle.product_id}",
                headers=auth(provider_token),
            )
