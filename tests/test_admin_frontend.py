"""Admin Frontend Tests — FE-1~FE-5 management page routes.

Verifies that all admin pages render correctly and return 200.
"""
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def admin_client(db_session):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class TestAdminRoutes:
    """FE-1~FE-5: All admin pages should return 200."""

    @pytest.mark.asyncio
    async def test_admin_identities_page(self, admin_client):
        resp = await admin_client.get("/admin/identities")
        assert resp.status_code == 200
        assert "identities" in resp.text.lower() or "身份" in resp.text

    @pytest.mark.asyncio
    async def test_admin_certificates_page(self, admin_client):
        resp = await admin_client.get("/admin/certificates")
        assert resp.status_code == 200
        assert "certificate" in resp.text.lower() or "证书" in resp.text

    @pytest.mark.asyncio
    async def test_admin_connectors_page(self, admin_client):
        resp = await admin_client.get("/admin/connectors")
        assert resp.status_code == 200
        assert "connector" in resp.text.lower() or "连接器" in resp.text

    @pytest.mark.asyncio
    async def test_admin_alerts_page(self, admin_client):
        resp = await admin_client.get("/admin/alerts")
        assert resp.status_code == 200
        assert "alert" in resp.text.lower() or "告警" in resp.text

    @pytest.mark.asyncio
    async def test_admin_blockchain_page(self, admin_client):
        resp = await admin_client.get("/admin/blockchain")
        assert resp.status_code == 200
        assert "blockchain" in resp.text.lower() or "区块链" in resp.text

    @pytest.mark.asyncio
    async def test_admin_base_template_includes_nav(self, admin_client):
        """Base template should include navigation sidebar."""
        resp = await admin_client.get("/admin/identities")
        assert resp.status_code == 200
        # Check for navigation links in the rendered HTML
        text = resp.text
        has_nav = any(link in text for link in [
            "/admin/identities", "/admin/certificates",
            "/admin/connectors", "/admin/alerts", "/admin/blockchain"
        ])
        assert has_nav, "Admin pages should include navigation links"

    @pytest.mark.asyncio
    async def test_admin_static_css_loaded(self, admin_client):
        """Admin pages should reference the CSS stylesheet."""
        resp = await admin_client.get("/admin/identities")
        assert resp.status_code == 200
        assert "admin.css" in resp.text or "/static/" in resp.text

    @pytest.mark.asyncio
    async def test_admin_static_js_loaded(self, admin_client):
        """Admin pages should reference the JS client."""
        resp = await admin_client.get("/admin/identities")
        assert resp.status_code == 200
        assert "admin.js" in resp.text or "/static/" in resp.text

    @pytest.mark.asyncio
    async def test_admin_nonexistent_returns_404(self, admin_client):
        """Non-existent admin page should return 404."""
        resp = await admin_client.get("/admin/nonexistent")
        assert resp.status_code == 404
