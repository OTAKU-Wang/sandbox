"""PZ-13 / SS-11 §7.3 DoD-9: capability-matrix multi-source consistency.

Locks the four sources of sandbox-level metadata so a level added, removed or
renamed in ONE of them breaks the build until ALL are updated together
(spec ss-11 §4.2 更新纪律):

1. API  — ``GET /api/v1/meta/capability-matrix`` (app/services/capability_matrix.py)
2. FE   — ``SandboxLevel`` const in cds-frontend/src/types/enums.ts
3. SDK  — ``CDSClient.capability_matrix()`` (path + response shape, replaying
          the live API body through the SDK transport)
4. Docs — the 级别能力 table in docs/error-codes.md (levels + 弃用 flags)
"""

import re
import sys
from pathlib import Path

import httpx
import pytest

# Make the sdk package importable without installation (same pattern as
# tests/test_pz11_sdk.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FE_ENUMS = REPO_ROOT / "cds-frontend" / "src" / "types" / "enums.ts"
ERROR_CODES = REPO_ROOT / "docs" / "error-codes.md"


def _fe_levels() -> list[str]:
    """Level values declared in the FE SandboxLevel const (as const object)."""
    text = FE_ENUMS.read_text(encoding="utf-8")
    block = re.search(r"export const SandboxLevel = \{(.*?)\} as const", text, re.S)
    assert block is not None, "SandboxLevel const not found in enums.ts"
    values = re.findall(r":\s*'([^']+)'", block.group(1))
    assert values, "no level values parsed from SandboxLevel"
    return values


def _doc_levels() -> dict[str, bool]:
    """Levels + 弃用 flags from the 级别能力 table in docs/error-codes.md."""
    text = ERROR_CODES.read_text(encoding="utf-8")
    section = re.search(r"## 级别能力.*?(?=\n## |\Z)", text, re.S)
    assert section is not None, "级别能力 section not found in error-codes.md"
    out: dict[str, bool] = {}
    for row in re.findall(r"^\|\s*`([A-Za-z0-9]+)`\s*\|[^|]*\|[^|]*\|\s*(是|否)\s*\|",
                          section.group(0), re.M):
        out[row[0]] = row[1] == "是"
    assert out, "no level rows parsed from the 级别能力 table"
    return out


@pytest.mark.asyncio
async def test_matrix_level_set_consistent_across_sources(client, auth_headers):
    """API ↔ FE ↔ SDK ↔ docs agree on the exact level set."""
    resp = await client.get("/api/v1/meta/capability-matrix", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    api_levels = set(data["matrix"].keys())

    fe_levels = set(_fe_levels())
    doc_levels = set(_doc_levels())

    assert api_levels == fe_levels, (
        f"FE enums.ts SandboxLevel drift: api={sorted(api_levels)} "
        f"fe={sorted(fe_levels)}"
    )
    assert api_levels == doc_levels, (
        f"docs/error-codes.md 级别能力 table drift: api={sorted(api_levels)} "
        f"docs={sorted(doc_levels)}"
    )


@pytest.mark.asyncio
async def test_matrix_deprecated_flags_match_docs(client, auth_headers):
    """Deprecated flags (L2/k8s 弃用观察) are identical in API and docs."""
    resp = await client.get("/api/v1/meta/capability-matrix", headers=auth_headers)
    data = resp.json()
    doc_levels = _doc_levels()
    for level, meta in data["levels"].items():
        assert meta["deprecated"] == doc_levels.get(level), (
            f"deprecated flag drift for {level!r}: "
            f"api={meta['deprecated']} docs={doc_levels.get(level)}"
        )


@pytest.mark.asyncio
async def test_sdk_capability_matrix_matches_api(client, auth_headers):
    """SDK capability_matrix() hits the same path and preserves the shape:
    replay the live API body through the SDK transport and compare."""
    resp = await client.get("/api/v1/meta/capability-matrix", headers=auth_headers)
    assert resp.status_code == 200
    api_data = resp.json()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/meta/capability-matrix", (
            f"SDK called unexpected path {request.url.path}"
        )
        return httpx.Response(200, json=api_data)

    c = CDSClient("http://test", token="tok", transport=httpx.MockTransport(handler))
    sdk_data = c.capability_matrix()

    assert set(sdk_data["matrix"].keys()) == set(api_data["matrix"].keys())
    assert set(sdk_data["levels"].keys()) == set(api_data["levels"].keys())
    # every cell state survives the SDK round-trip
    for level in api_data["matrix"]:
        assert set(sdk_data["matrix"][level].keys()) == set(api_data["matrix"][level].keys())
        for cap, cell in api_data["matrix"][level].items():
            assert sdk_data["matrix"][level][cap]["state"] == cell["state"]
