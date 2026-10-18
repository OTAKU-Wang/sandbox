"""PZ-16 ③: CORS expose_headers 必须放行 X-CDS-Output-Review。

跨域部署下浏览器只把 CORS-safelisted 响应头交给 JavaScript；若 CORSMiddleware 未
expose 该头，前端就读不到"下载内容已被输出审查 REDACTED"，成为静默泄露失败
（app/api/sandbox_sessions.py 下载路径设置该头，cds-frontend/src/services/sandboxApi.ts
downloadFile 解析该头）。本测试锁定中间件配置本身。
"""

from fastapi.middleware.cors import CORSMiddleware

from app.main import app

REVIEW_HEADER = "X-CDS-Output-Review"


def _cors_middleware_kwargs():
    """从 app.user_middleware 取出 CORSMiddleware 的初始化 kwargs。"""
    entries = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(entries) == 1, f"expected exactly one CORSMiddleware, got {len(entries)}"
    return entries[0].kwargs


def test_cors_exposes_output_review_header():
    expose = [h.lower() for h in _cors_middleware_kwargs()["expose_headers"]]
    assert REVIEW_HEADER.lower() in expose, (
        f"{REVIEW_HEADER} must be in CORS expose_headers, got {expose}"
    )


def test_cors_exposes_download_headers():
    """审计导出/交付包/会话文件下载都靠 Content-Disposition 传服务端文件名。"""
    expose = [h.lower() for h in _cors_middleware_kwargs()["expose_headers"]]
    for header in ("content-disposition", "content-length", "content-type"):
        assert header in expose, f"{header} must be in CORS expose_headers, got {expose}"
