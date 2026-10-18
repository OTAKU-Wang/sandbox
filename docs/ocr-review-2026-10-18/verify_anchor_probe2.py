"""Probe #2 — merkle + field_mask head, and cross_space_policy with a valid FederationTrust."""
import json, asyncio, uuid as _uuid

out = {}
import app.services.merkle_service as msm
from app.services.merkle_service import merkle_service as ms, MerkleProof, _sm3_fallback_allowed

out["gmssl_available"] = msm._GMSSL_AVAILABLE
out["hash_name"] = msm._HASH_NAME
out["build_tree_empty"] = ms.build_tree([])
out["get_root_empty_eq_emptystr"] = ms.get_root([]) == ""
out["verify_batch_root_empty_passes"] = ms.verify_batch_root([], "")
out["fallback_gate_value"] = _sm3_fallback_allowed()
try:
    ms.verify_proof(MerkleProof(leaf_index=0, leaf_hash="zz",
                                proof_path=[{"hash": "0x00", "position": "left"}], root_hash="00"))
    out["verify_proof_badhex"] = "no raise"
except Exception as e:
    out["verify_proof_badhex"] = type(e).__name__ + ": " + str(e)[:90]
try:
    ms.verify_proof(MerkleProof(leaf_index=0, leaf_hash="ab",
                                proof_path=[{"hash": None, "position": "left"}], root_hash="00"))
    out["verify_proof_none"] = "no raise"
except Exception as e:
    out["verify_proof_none"] = type(e).__name__ + ": " + str(e)[:90]
# self-attesting: attacker picks leaf + path then sets root to whatever it computes
forged_root = ms.hash_internal("bb" * 32, "aa" * 32)
out["forged_proof_verifies"] = ms.verify_proof(MerkleProof(
    leaf_index=0, leaf_hash="aa" * 32,
    proof_path=[{"hash": "bb" * 32, "position": "left"}], root_hash=forged_root))
out["forged_proof_different_leaf"] = ms.verify_proof(MerkleProof(
    leaf_index=0, leaf_hash="cc" * 32,
    proof_path=[{"hash": "bb" * 32, "position": "left"}], root_hash=forged_root))
out["proof_carries_no_leaf_count"] = [f for f in MerkleProof.__dataclass_fields__]
r = ms.hash_leaf(b"x")
out["root_case_sensitive"] = (ms.get_root([r]) != ms.get_root([r.upper()]))

from app.services.field_mask import FieldMasker, MaskRule, MaskMode
fm = FieldMasker()
mk = lambda **kw: MaskRule(**kw)
out["bucket_default"] = fm._generalize_numeric_bucket("250", {})
for name, p in [("bucket_0", {"bucket_size": 0}), ("bucket_none", {"bucket_size": None}),
                ("bucket_str", {"bucket_size": "100"}), ("bucket_float", {"bucket_size": 50.0})]:
    try:
        out[name] = fm._generalize_numeric_bucket("250", p)
    except Exception as e:
        out[name] = "RAISED " + type(e).__name__ + ": " + str(e)[:80]
for name, p in [("trunc_default", {}), ("trunc_neg", {"keep_chars": -1}),
                ("trunc_str", {"keep_chars": "3"}), ("trunc_none", {"keep_chars": None}),
                ("trunc_zero", {"keep_chars": 0})]:
    try:
        out[name] = fm._generalize_truncate("13800138000", p)
    except Exception as e:
        out[name] = "RAISED " + type(e).__name__ + ": " + str(e)[:80]
out["hash_low_entropy"] = fm._hash("13800138000")
out["hash_backend"] = fm.mask_row({"p": "13800138000"}, [mk(field_name="p", mode=MaskMode.HASH)]).masked_row
out["hash_int_vs_float"] = [
    fm.mask_row({"v": 25}, [mk(field_name="v", mode=MaskMode.HASH)]).masked_row["v"],
    fm.mask_row({"v": 25.0}, [mk(field_name="v", mode=MaskMode.HASH)]).masked_row["v"]]
out["date_slice_lens"] = {fmt: len(fmt.replace('%', ' ').strip()) + 5 for fmt in
                          ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")}
out["date_month_T_ok"] = fm._generalize_date_month("2020-01-02T03:04:05")
out["date_month_extra_precision"] = fm._generalize_date_month("2020-01-02T03:04:05.123456")
out["date_month_ms_fmt"] = fm._generalize_date_month("2020/01/02 03:04:05")
out["mask_row_datetime_str"] = fm.mask_row({"d": __import__("datetime").datetime(2020, 1, 2, 3, 4, 5)},
                                           [mk(field_name="d", mode=MaskMode.GENERALIZE, generalize_type="date_month")]).masked_row
out["mask_row_dateobj_generalize"] = fm.mask_row({"d": __import__("datetime").date(2020, 1, 2)},
                                                 [mk(field_name="d", mode=MaskMode.GENERALIZE, generalize_type="date_month")]).masked_row
out["region_miss_returns_original"] = fm._generalize_region("Atlantis")
out["gen_unknown_type"] = fm.mask_row({"d": "1990-01-02"}, [mk(field_name="d", mode=MaskMode.GENERALIZE, generalize_type="date_mnth")]).masked_row
out["rules_applied_on_parse_fail"] = vars(fm.mask_row(
    {"d": "not-a-date"}, [mk(field_name="d", mode=MaskMode.GENERALIZE, generalize_type="date_month")]))

from app.services.cross_space_policy import cross_space_policy as csp, CrossSpacePolicyRule, OPERATION_TRUST_MAP
from app.services.federation_connector import (FederationTrust, SpaceIdentity, TrustLevel, FederationStatus)

def T(level=TrustLevel.BASIC, status=FederationStatus.ACTIVE, ops=None, expires=None):
    return FederationTrust(
        trust_id="t1",
        local_space=SpaceIdentity(space_id="local", space_name="L", endpoint="https://l"),
        remote_space=SpaceIdentity(space_id="s1", space_name="n", endpoint="https://r"),
        trust_level=level, status=status,
        allowed_operations=ops if ops is not None else ["read_catalog", "search", "verify_audit", "export_result"],
        expires_at=expires)

def ev(**kw):
    try:
        return vars(csp.evaluate(**kw))
    except Exception as e:
        return "RAISED " + type(e).__name__ + ": " + str(e)[:120]

out["export_result_in_map"] = "export_result" in OPERATION_TRUST_MAP
out["export_at_BASIC_L2"] = ev(trust=T(TrustLevel.BASIC, ops=["export_result"]), operation="export_result", sandbox_level="L2")
out["export_at_BASIC_L1"] = ev(trust=T(TrustLevel.BASIC, ops=["export_result"]), operation="export_result", sandbox_level="L1")
out["unknown_op_at_BASIC"] = ev(trust=T(TrustLevel.BASIC, ops=["brand_new_op"]), operation="brand_new_op", sandbox_level="L3")
out["read_data_at_BASIC_denied"] = ev(trust=T(TrustLevel.BASIC, ops=["read_data"]), operation="read_data", sandbox_level="L3")
out["train_dp_none"] = ev(trust=T(TrustLevel.FULL, ops=["train_model"]), operation="train_model", sandbox_level="L3")
out["train_dp_zero"] = ev(trust=T(TrustLevel.FULL, ops=["train_model"]), operation="train_model", sandbox_level="L3", dp_budget_remaining=0)
from datetime import datetime, timedelta, timezone
out["expired_trust"] = ev(trust=T(TrustLevel.FULL, ops=["train_model"], expires=datetime.now(timezone.utc) - timedelta(days=1)),
                          operation="train_model", sandbox_level="L3")
out["pending_reason"] = ev(trust=T(TrustLevel.BASIC, status=FederationStatus.PENDING), operation="search", sandbox_level="L1")
out["revoked_reason"] = ev(trust=T(TrustLevel.BASIC, status=FederationStatus.REVOKED), operation="search", sandbox_level="L1")
before = csp._get_min_sandbox_level("read_data")
csp.add_rule(CrossSpacePolicyRule(rule_id="custom-strict", name="cs", min_sandbox_level="L3",
                                  allowed_operations=["read_data"], priority=1))
out["priority_before_after"] = (before, csp._get_min_sandbox_level("read_data"), [r.priority for r in csp.list_rules()])
csp.add_rule(CrossSpacePolicyRule(rule_id="typo", name="t", min_sandbox_level="l4",
                                  allowed_operations=["read_data"], priority=999))
out["typo_level"] = (csp._get_min_sandbox_level("read_data"),
                     ev(trust=T(TrustLevel.FULL, ops=["read_data"]), operation="read_data", sandbox_level="L0"))
out["dup_rule_id"] = (csp.add_rule(CrossSpacePolicyRule(rule_id="default-read", name="dup",
                                                        min_sandbox_level="L0", allowed_operations=["read_data"], priority=998)),
                      [r.name for r in csp.list_rules() if r.rule_id == "default-read"])
out["trust_score_vocab"] = csp.evaluate_trust_score()
print(json.dumps(out, indent=1, default=str, ensure_ascii=False))
