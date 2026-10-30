"""PZ-08 (SS-11 spec) — data closed-loop e2e, CI gate level (not `slow`).

One repeatable loop per the spec §3.1 PZ-08:
  synthetic data upload → L0 session → execute → T5-gated download
  → audit / usage / proof-bundle all present.

Determinism: the T5 output gateway is REAL and asserted end-to-end
(`X-CDS-Output-Review` header, audit records). Only the sandbox
provisioning itself is environment-dependent (L0 needs bwrap); when the
test environment cannot provision, the provisioning-dependent tail is
skipped with an explicit reason (env whitelist in ci/known-failures.md),
never silently.
"""
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

# sandbox_security imports POSIX-only 'resource'; runs on Linux/CI.
_win_skip = pytest.mark.skipif(sys.platform == "win32",
                               reason="sandbox_security imports POSIX-only 'resource'")

# P1a-8（2026-10-30 lead 代收）failed-即-skip 纪律：环境性失败只认下面这组
# **签名白名单**（逐条对应 ci/known-failures.md「宿主 AppArmor 禁非特权 userns」行），
# 命中才允许 skip 且 reason 带签名名；产品性失败（回归、断言、错误档位）绝不落进
# 白名单，必须红。白名单外的一切失败照抛——门禁不许被 skip 静默绕过。
_ENV_FAILURE_SIGNATURES = (
    "No permissions to create a new namespace",  # Ubuntu apparmor userns 拒（本机现态）
    "unprivileged user namespaces",              # 同类文案变体
    "Read-only file system: '/sys/fs/cgroup",    # cgroup 只读挂载（本机另一签名）
)


def _env_failure_reason(text: str) -> str | None:
    """命中白名单签名 → 返回签名名（进 skip reason）；否则 None（=必须红）。"""
    for sig in _ENV_FAILURE_SIGNATURES:
        if sig in text:
            return sig
    return None

PII_FREE_PAYLOAD = b"name,Alice\nname,Bob\nname,Carol\n"


@_win_skip
@pytest.mark.asyncio
async def test_e2e_data_loop_upload_execute_download_audit(
    client: AsyncClient, auth_headers: dict, operator_headers: dict, publish_product,
):
    prod_resp = await client.post("/api/v1/data-products",
                                   json={"name": "PZ08DataLoop"}, headers=auth_headers)
    assert prod_resp.status_code in (200, 201), prod_resp.text
    product_id = prod_resp.json()["id"]
    await publish_product(product_id)

    sess_resp = await client.post("/api/v1/sandbox-sessions",
                                  json={"data_product_id": product_id,
                                        "sandbox_level": "L0"},
                                  headers=auth_headers)
    assert sess_resp.status_code == 201, sess_resp.text
    session = sess_resp.json()
    sid = session["id"]
    status = session["status"]

    up = await client.post(f"/api/v1/sandbox-sessions/{sid}/files",
                           files={"file": ("loop.csv", PII_FREE_PAYLOAD, "text/csv")},
                           headers=auth_headers)
    assert up.status_code in (200, 201), up.text

    files = await client.get(f"/api/v1/sandbox-sessions/{sid}/files", headers=auth_headers)
    assert files.status_code == 200
    names = [e["filename"] for e in files.json()["files"]]
    assert "loop.csv" in names

    aud = await client.get("/api/v1/audit/records",
                           params={"action": "sandbox.file_upload", "limit": 100},
                           headers=operator_headers)
    assert aud.status_code == 200, aud.text
    assert sid in aud.text, "file_upload audit record for this session missing"

    if status not in ("ready", "running"):
        reason = _env_failure_reason(status + " " + str(session))
        if reason:
            pytest.skip(f"L0 provisioning status={status} hit env whitelist signature "
                        f"[{reason}] (ci/known-failures.md 宿主 AppArmor/userns 行); "
                        f"upload/audit half of the loop verified")
        pytest.fail(f"L0 provisioning status={status} is NOT an env-whitelisted failure "
                    f"(no signature in {_ENV_FAILURE_SIGNATURES}) — product regression? "
                    f"failed-即-skip 纪律：白名单外必须红。session={session}")

    ex = await client.post(f"/api/v1/sandbox-sessions/{sid}/execute",
                           json={"language": "python",
                                 "code": "print('pz08-hello')"},
                           headers=auth_headers)
    assert ex.status_code == 200, ex.text
    body = ex.json()
    out = str(body.get("output") or body.get("stdout") or body)
    # failed-即-skip 纪律（本机实证落点）：会话能到 ready，但 exec 真跑时 bwrap
    # 被宿主拒——错误以「exec 200 + 输出即错误」形态返回。输出命中白名单签名才
    # skip（前半程 upload/audit 已验）；其余输出=产品面，照常红。
    reason = _env_failure_reason(out)
    if reason:
        pytest.skip(f"exec output hit env whitelist signature [{reason}] "
                    f"(ci/known-failures.md 宿主 AppArmor/userns 行); "
                    f"upload/audit half verified; download/T5 half unreachable without exec")
    assert "pz08-hello" in out

    dl = await client.get(f"/api/v1/sandbox-sessions/{sid}/files/loop.csv",
                          headers=auth_headers)
    assert dl.status_code == 200, dl.text
    review = dl.headers.get("X-CDS-Output-Review", "")
    assert review.startswith(("passed", "redacted")), f"T5 review header missing: {review!r}"
    assert dl.content == PII_FREE_PAYLOAD

    usage = await client.get(f"/api/v1/sandbox-sessions/{sid}/usage", headers=auth_headers)
    assert usage.status_code == 200
    assert usage.json()["uploaded_files"]["count"] >= 1

    pb = await client.get(f"/api/v1/sandbox-sessions/{sid}/proof-bundle", headers=auth_headers)
    assert pb.status_code == 200, pb.text
    bundle = pb.json()
    assert bundle.get("integrity", {}).get("evidence_hash"), "evidence_hash missing from proof bundle"
    # PZ-06 (Track C): disclosure meta is additive; evidence_hash input set must stay unchanged
    assert "bundle_meta" in bundle, "bundle_meta disclosure block missing (PZ-06)"
    meta = bundle["bundle_meta"]
    for field in ("signing_backend", "aead_algorithm", "trust_root", "chain_anchoring"):
        assert field in meta, f"bundle_meta.{field} missing (PZ-06)"

    term = await client.post(f"/api/v1/sandbox-sessions/{sid}/terminate",
                             headers=auth_headers)
    assert term.status_code in (200, 202)
