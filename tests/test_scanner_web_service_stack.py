"""product_dev 服务开发族白名单（2026-11-05 Jupyter FastAPI 演示轮）：
fastapi/pydantic/uvicorn 与 loopback 标准件放行；urllib 以外部源能力 WARNING 披露（A98 口径）。"""
from app.services.code_scanner import ScanSeverity, code_scanner

WEB_STACK = """
import json
import threading
import time
import socket
import urllib.parse
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Order(BaseModel):
    city: str
"""


def test_web_service_stack_allowed_in_product_dev():
    result = code_scanner.scan(WEB_STACK, "python", "product_dev")
    fatal = [i for i in result.issues if i.severity == ScanSeverity.FATAL]
    assert not fatal, [i.message for i in fatal]
    assert result.passed


def test_urllib_still_disclosed_as_external_source():
    result = code_scanner.scan("import urllib.request\n", "python", "product_dev")
    disclosed = [i for i in result.issues if "urllib" in (i.message or "")]
    assert disclosed and all(i.severity == ScanSeverity.WARNING for i in disclosed)


def test_non_whitelisted_net_module_still_fatal():
    result = code_scanner.scan("import requests\nrequests.get('http://127.0.0.1')\n", "python", "product_dev")
    assert any(i.code == "NOT_WHITELISTED" and "requests" in i.message for i in result.issues)
    assert not result.passed
