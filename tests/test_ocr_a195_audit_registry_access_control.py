"""A195 — `contracts/AuditRegistry.sol` anchoring must be access-controlled and attributable.

No `solc`/`web3` toolchain exists in this lane and the chain is not wired
(`app/services/blockchain_adapter.py` self-reports "no real/wired chain anchoring exists"),
so this is a source-level gate in the `tests/test_p0_security_gaps.py` style: it pins the
contract's authorization and provenance structure rather than executing bytecode.
"""

from __future__ import annotations

import re
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "AuditRegistry.sol"
SRC = CONTRACT.read_text(encoding="utf-8")


def _member(kind: str, name: str) -> str:
    pattern = rf"\b{kind}\s+{name}\s*\((?:[^;{{]*?)\)"
    match = re.search(pattern, SRC, re.S)
    assert match, f"{kind} {name} not found in {CONTRACT.name}"
    start = match.end()
    brace = SRC.find("{", start)
    semicolon = SRC.find(";", start)
    if semicolon != -1 and (brace == -1 or semicolon < brace):
        return SRC[match.start() : semicolon + 1]
    depth = 0
    for idx in range(brace, len(SRC)):
        if SRC[idx] == "{":
            depth += 1
        elif SRC[idx] == "}":
            depth -= 1
            if depth == 0:
                return SRC[match.start() : idx + 1]
    raise AssertionError(f"unbalanced body for {kind} {name}")


def _event_params(name: str) -> str:
    match = re.search(rf"\bevent\s+{name}\s*\((.*?)\)\s*;", SRC, re.S)
    assert match, f"event {name} not found"
    return re.sub(r"\s+", " ", match.group(1))


def test_anchor_audit_is_access_controlled():
    anchor = _member("function", "anchorAudit")
    header = anchor.split("{", 1)[0]
    modifiers = re.sub(
        r"function\s+anchorAudit\s*\([^)]*\)", "", header, flags=re.S
    ).strip()
    assert modifiers, f"anchorAudit has no access-control modifier: {header!r}"

    guard = ""
    for mod in re.findall(r"\b(\w+)", modifiers):
        try:
            guard += _member("modifier", mod)
        except AssertionError:
            continue
    assert guard, f"anchorAudit modifier(s) {modifiers!r} are not defined in the contract"
    assert "authorizedAnchors" in guard and "owner" in guard, (
        f"anchor guard does not check an anchorer allowlist: {guard!r}"
    )
    assert "msg.sender" in guard


def test_unauthorized_accounts_cannot_write_any_anchor():
    write_guards = []
    for match in re.finditer(r"\bfunction\s+(\w+)\s*\((?:[^)]*)\)\s*([^;{]*)", SRC, re.S):
        name, header_tail = match.group(1), match.group(2)
        if "external" not in header_tail or "view" in header_tail:
            continue
        if name in {"transferOwnership", "setAuthorizedAnchor"}:
            assert "onlyOwner" in header_tail, f"{name} lost its onlyOwner gate"
            continue
        write_guards.append((name, header_tail))
    assert write_guards, "no external write function found — contract shape changed"
    for name, header_tail in write_guards:
        assert "onlyAnchorer" in header_tail or "onlyOwner" in header_tail, (
            f"external write {name} is unauthenticated: {header_tail!r}"
        )


def test_anchor_event_and_state_carry_the_anchorer():
    params = _event_params("AuditAnchored")
    assert re.search(r"address\s+indexed\s+anchorer", params), (
        f"AuditAnchored does not disclose who anchored it: {params!r}"
    )
    anchor = _member("function", "anchorAudit")
    assert "msg.sender" in anchor.split("emit AuditAnchored", 1)[-1] or "msg.sender" in anchor

    attributed = _member("function", "anchoredBy")
    assert "external view returns (address)" in attributed, attributed
    assert "audits[merkleRoot].anchorer" in attributed


def test_owner_and_anchorer_allowlist_are_administered():
    assert re.search(r"\baddress\s+public\s+owner\b", SRC), "no owner state slot"
    assert re.search(r"\bmapping\s*\(\s*address\s*=>\s*bool\s*\)\s+public\s+authorizedAnchors\b", SRC)
    assert re.search(r"\bconstructor\s*\(\s*\)", SRC), "no constructor to seed the owner"

    setter = _member("function", "setAuthorizedAnchor")
    assert "onlyOwner" in setter.split("{", 1)[0], setter


def test_immutability_and_input_gates_survive_the_fix():
    anchor = _member("function", "anchorAudit")
    assert re.search(r"require\(\s*audits\[merkleRoot\]\.timestamp\s*==\s*0", anchor), (
        "first-writer-wins guard was removed — anchors became overwritable"
    )
    assert re.search(r"require\(\s*merkleRoot\s*!=\s*bytes32\(0\)", anchor)
    assert "block.timestamp" in anchor
    assert "function verifyAudit(bytes32 merkleRoot) external view returns (bool)" in SRC
