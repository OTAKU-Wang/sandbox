"""Admin UI routes — serves frontend management pages (FE-1~FE-5).

Uses Jinja2 directly to avoid Starlette TemplateResponse compatibility
issues with Python 3.14+.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

_template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_template_dir)))
router = APIRouter(prefix="/admin", tags=["admin"])


def _render(template_name: str) -> HTMLResponse:
    html = _env.get_template(template_name).render()
    return HTMLResponse(content=html)


@router.get("/identities", response_class=HTMLResponse)
async def identities_page():
    return _render("identities.html")


@router.get("/certificates", response_class=HTMLResponse)
async def certificates_page():
    return _render("certificates.html")


@router.get("/connectors", response_class=HTMLResponse)
async def connectors_page():
    return _render("connectors.html")


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page():
    return _render("alerts.html")


@router.get("/blockchain", response_class=HTMLResponse)
async def blockchain_page():
    return _render("blockchain.html")
