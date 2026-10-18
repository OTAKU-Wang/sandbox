
import sys, httpx, json
sys.path.insert(0, "D:/workspace/sandbox/sdk")
from cds_sdk.client import _parse_error, _retry_after_seconds
for body, ct in [('[{"detail":"x"}]', "application/json"), ('"just a string"', "application/json"), ("null", "application/json"), ("not json at all", "text/plain")]:
    req = httpx.Request("GET", "http://x/api/v1/y")
    resp = httpx.Response(500, headers={"content-type": ct}, request=req)
    resp._content = body.encode()
    try:
        e = _parse_error(resp, "/api/v1/y")
        print("OK   ", body[:22], "-> code=", e.code, "detail=", str(e.detail)[:40])
    except Exception as exc:
        print("RAISE", body[:22], "->", type(exc).__name__, str(exc)[:60])
r = httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, request=req)
print("Retry-After HTTP-date ->", _retry_after_seconds(r), "seconds (spec value would be minutes-hours)")
