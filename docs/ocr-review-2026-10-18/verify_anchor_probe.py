"""Read-only runtime probes for the anchor/audit/masking/quota claims. No repo writes."""
import sys, json, traceback
out = {}

def rec(k, fn):
    try:
        out[k] = fn()
    except Exception as e:
        out[k] = {"RAISED": type(e).__name__, "msg": str(e)[:160]}

# ---------- merkle_service ----------
from app.services.merkle_service import merkle_service as ms, MerkleProof, _sm3_fallback_allowed
import app.services.merkle_service as msm

rec("gmssl_available", lambda: msm._GMSSL_AVAILABLE)
rec("hash_name", lambda: msm._HASH_NAME)
rec("build_tree_empty", lambda: ms.build_tree([]))
rec("get_root_empty_is_empty_string", lambda: ms.get_root([]) == "")
rec("verify_batch_root_empty_passes", lambda: ms.verify_batch_root([], ""))
rec("fallback_gate_true_on_bad_settings", lambda: _sm3_fallback_allowed())
rec("verify_proof_badhex", lambda: ms.verify_proof(
    MerkleProof(leaf_index=0, leaf_hash="zz", proof_path=[{"hash": "0x00", "position": "left"}], root_hash="00")))
rec("verify_proof_none_hash", lambda: ms.verify_proof(
    MerkleProof(leaf_index=0, leaf_hash="ab", proof_path=[{"hash": None, "position": "left"}], root_hash="00")))
rec("verify_proof_self_attesting_forgery", lambda: (lambda: (
    ms.verify_proof(MerkleProof(leaf_index=0, leaf_hash="aa" * 32,
                                proof_path=[{"hash": "bb" * 32, "position": "left"}],
                                root_hash=ms.hash_internal("bb" * 32, "aa" * 32))),
    ms.verify_proof(MerkleProof(leaf_index=0, leaf_hash="cc" * 32,
                                proof_path=[{"hash": "bb" * 32, "position": "left"}],
                                root_hash=ms.hash_internal("bb" * 32, "cc" * 32))),
))())
rec("verify_proof_no_leaf_count_binding", lambda: ms.verify_proof(
    MerkleProof(leaf_index=0, leaf_hash="aa" * 32, proof_path=[], root_hash="aa" * 32)))
rec("root_case_sensitive", lambda: (lambda r: (r, r.upper(), ms.get_root([r]) == ms.get_root([r.upper()])))(
    ms.hash_leaf(b"x")))
rec("dead_branch_get_root_single_leaf", lambda: ms.get_root([ms.hash_leaf(b"1")])[:8])

# ---------- field_mask ----------
from app.services.field_mask import FieldMasker, MaskRule, MaskMode
fm = FieldMasker()
mk = lambda **kw: MaskRule(**kw)
rec("mask_missing_field_silent", lambda: vars(fm.mask_row({"a": 1}, [mk(field_name="ssn", mode=MaskMode.REDACT)])))
rec("mask_type_lost_int_to_str", lambda: fm.mask_row({"age": 25}, [mk(field_name="age", mode=MaskMode.GENERALIZE, generalize_type="age_range")]).masked_row)
rec("mask_bool_to_str", lambda: fm.mask_row({"b": True}, [mk(field_name="b", mode=MaskMode.REDACT)]).masked_row)
rec("hash_determinism_int_vs_float", lambda: (
    fm.mask_row({"v": 25}, [mk(field_name="v", mode=MaskMode.HASH)]).masked_row["v"],
    fm.mask_row({"v": 25.0}, [mk(field_name="v", mode=MaskMode.HASH)]).masked_row["v"]))
rec("hash_is_unsalted_and_backend", lambda: (
    fm._hash("13800138000"), fm.mask_row({"p": "13800138000"}, [mk(field_name="p", mode=MaskMode.HASH)]).masked_row["p"][:16]))
rec("unknown_gen_type_returns_original", lambda: fm.mask_row(
    {"d": "1990-01-02"}, [mk(field_name="d", mode=MaskMode.GENERALIZE, generalize_type="date_mnth")]).masked_row)
rec("gen_age_parse_fail_returns_original", lambda: fm._generalize_age("not-an-age"))
rec("gen_age_120_unknown", lambda: fm._generalize_age("120"))
rec("gen_age_130_unknown", lambda: fm._generalize_age("130"))
rec("gen_age_negative", lambda: fm._generalize_age("-5"))
rec("gen_age_inf", lambda: fm._generalize_age("inf"))
rec("gen_age_nan_returns_original", lambda: fm._generalize_age("nan"))
rec("bucket_default_100", lambda: fm._generalize_numeric_bucket("250", {}))
rec("bucket_zero", lambda: fm._generalize_numeric_bucket("250", {"bucket_size": 0}))
rec("bucket_none", lambda: fm._generalize_numeric_bucket("250", {"bucket_size": None}))
rec("bucket_str", lambda: fm._generalize_numeric_bucket("250", {"bucket_size": "100"}))
rec("truncate_default", lambda: fm._generalize_truncate("13800138000", {}))
rec("truncate_negative_keep", lambda: fm._generalize_truncate("13800138000", {"keep_chars": -1}))
rec("truncate_str_keep", lambda: fm._generalize_truncate("13800138000", {"keep_chars": "3"}))
rec("date_month_slice_len", lambda: [(fmt, len(fmt.replace('%', ' ').strip()) + 5) for fmt in
                                     ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")])
rec("date_month_iso", lambda: fm._generalize_date_month("2020-01-02T03:04:05"))
rec("date_month_ymd_compact", lambda: fm._generalize_date_month("20200102"))
rec("date_month_unparseable_returns_original", lambda: fm._generalize_date_month("1990年1月2日"))
rec("nested_container_untouched", lambda: (lambda src: (
    fm.mask_row(src, [mk(field_name="user.ssn", mode=MaskMode.REDACT),
                      mk(field_name="profile", mode=MaskMode.REDACT)]).masked_row,
    src))(
    {"profile": {"ssn": "110101199001011234"}}))
rec("nested_alias_source_mutated", lambda: (lambda src: (
    (lambda r: (r["profile"].update({"ssn": "MUT"}), src["profile"])[1])(
        fm.mask_row(src, [mk(field_name="x", mode=MaskMode.REDACT)]).masked_row),
    src))({"profile": {"ssn": "orig"}}))
rec("redact_default_dup", lambda: (mk(field_name="f", mode=MaskMode.REDACT).placeholder,
                                   fm._redact.__defaults__))

# ---------- cross_space_policy ----------
from app.services.cross_space_policy import (cross_space_policy as csp, CrossSpacePolicyRule,
                                             OPERATION_TRUST_MAP)
from app.services.federation_connector import (FederationTrust, TrustLevel, FederationStatus)
import inspect
sig = str(inspect.signature(FederationTrust))
out["FederationTrust_signature"] = sig[:600]

def trust(level=TrustLevel.BASIC, status=FederationStatus.ACTIVE, ops=None, expires=None):
    kw = dict(trust_id="t1", remote_space_id="s1", remote_space_name="n",
              trust_level=level, status=status,
              allowed_operations=ops if ops is not None else ["read_catalog", "search", "verify_audit", "export_result"])
    try:
        if expires is not None:
            kw["expires_at"] = expires
        return FederationTrust(**kw)
    except TypeError as e:
        return {"CTOR_ERROR": str(e)[:200]}

rec("export_result_in_trust_map", lambda: "export_result" in OPERATION_TRUST_MAP)
rec("export_result_allowed_at_BASIC_L2", lambda: vars(csp.evaluate(
    trust(TrustLevel.BASIC, ops=["export_result"]), "export_result", sandbox_level="L2")))
rec("read_data_denied_at_BASIC", lambda: vars(csp.evaluate(
    trust(TrustLevel.BASIC, ops=["read_data"]), "read_data", sandbox_level="L3")))
rec("min_sandbox_for_export_result", lambda: csp._get_min_sandbox_level("export_result"))
rec("unknown_operation_defaults_BASIC", lambda: vars(csp.evaluate(
    trust(TrustLevel.BASIC, ops=["totally_new_op"]), "totally_new_op", sandbox_level="L3")))
rec("require_dp_budget_skipped_when_none", lambda: vars(csp.evaluate(
    trust(TrustLevel.FULL, ops=["train_model"]), "train_model", sandbox_level="L3")))
rec("require_dp_budget_denied_when_zero", lambda: vars(csp.evaluate(
    trust(TrustLevel.FULL, ops=["train_model"]), "train_model", sandbox_level="L3", dp_budget_remaining=0)))
rec("expired_still_active_denied_or_not", lambda: vars(csp.evaluate(
    trust(TrustLevel.FULL, ops=["train_model"], expires="2000-01-01T00:00:00+00:00"), "train_model",
    sandbox_level="L3")))
rec("priority_semantics", lambda: (
    (lambda before: (csp.add_rule(CrossSpacePolicyRule(
        rule_id="strict", name="strict", min_sandbox_level="L3",
        allowed_operations=["read_data"], priority=1)),
        csp._get_min_sandbox_level("read_data"), [r.priority for r in csp.list_rules()], before))(
    csp._get_min_sandbox_level("read_data"))))
rec("typo_sandbox_level_fails_open", lambda: (
    csp.add_rule(CrossSpacePolicyRule(rule_id="typo", name="t", min_sandbox_level="l3",
                                      allowed_operations=["read_data"], priority=999)),
    csp._get_min_sandbox_level("read_data"),
    vars(csp.evaluate(trust(TrustLevel.FULL, ops=["read_data"]), "read_data", sandbox_level="L0"))))
rec("pending_trust_reason_label", lambda: vars(csp.evaluate(
    trust(TrustLevel.BASIC, status=FederationStatus.PENDING), "search", sandbox_level="L1")))
rec("evaluate_trust_score_vocab", lambda: csp.evaluate_trust_score())

# ---------- alert_engine ----------
from app.services.alert_engine import AlertRuleEngine, AlertType, RuleConfig
e = AlertRuleEngine()
e.record_dp_budget("s1", remaining_epsilon=0.01, total_epsilon=1.0, user_id="u1")
a1 = [a.alert_type.value for a in e.evaluate()]
a2 = [a.alert_type.value for a in e.evaluate()]
out["alert_evaluate_repeat"] = {"first": a1, "second": a2, "fired_len": len(e.get_fired_alerts())}
e2 = AlertRuleEngine()
e2.configure(AlertType.ANOMALY_BURST, RuleConfig(enabled=True, threshold=2, window_minutes=0))
for _ in range(5):
    e2.record_query("u1")
out["burst_window_zero_hits_all_history"] = [a.message for a in e2._check_anomaly_burst(
    e2._configs[AlertType.ANOMALY_BURST])]
e3 = AlertRuleEngine()
out["offhours_uses_wall_clock"] = {"hour_used_is_now": True, "src": "see code quote",
                                   "msgs": [a.message for a in e3._check_off_hours(
                                       e3._configs[AlertType.ANOMALY_OFF_HOURS])]}
e4 = AlertRuleEngine()
out["events_never_pruned"] = {"clear_events_only": hasattr(e4, "clear_events")}

# ---------- quota_manager local fallback ----------
from app.services.quota_manager import QuotaManager, QuotaType
import asyncio, uuid as _uuid

async def quotas():
    q = QuotaManager()  # no redis -> local fallback
    sid = _uuid.uuid4()
    await q.set_limits(sid, {QuotaType.ROWS: 100})
    denied = []
    for _ in range(3):
        r = await q.check_and_increment(sid, QuotaType.ROWS, 80, limit=0)  # limit auto-read
        denied.append((r.allowed, r.current, r.limit))
    usage_after_denials = await q.get_usage(sid, QuotaType.ROWS)
    # limit=0 semantics
    sid2 = _uuid.uuid4()
    await q.set_limits(sid2, {QuotaType.ROWS: 50})
    batch = await q.check_batch(sid2, {QuotaType.ROWS: (10, 0)})  # limit 0 passed explicitly
    batch_state = {k.value: (v.allowed, v.limit) for k, v in batch.items()}
    usage2_before = await q.get_usage(sid2, QuotaType.ROWS)
    # rejected increment not rolled back
    sid3 = _uuid.uuid4()
    r1 = await q.check_and_increment(sid3, QuotaType.ROWS, 10, limit=5)
    usage3 = await q.get_usage(sid3, QuotaType.ROWS)
    # gateway local: rejected still counted
    ok1 = q._check_gateway_quota_local("app1", rows_increment=5, rows_limit=3)
    usage_gw = await q.get_gateway_usage("app1")
    # reset() with malformed contract limits after keys deleted
    sid4 = _uuid.uuid4()
    await q.set_limits(sid4, {QuotaType.ROWS: 10})
    try:
        await q.reset(sid4, contract_limits={"dp_epsilon_budget": "abc"})
        reset_raised = None
    except Exception as ex:
        reset_raised = type(ex).__name__ + ": " + str(ex)[:80]
    limit_after_failed_reset = await q.get_limit(sid4, QuotaType.ROWS)
    return {"check_and_increment_x3": denied, "usage_after_denials": usage_after_denials,
            "batch_limit0": batch_state, "batch_usage_before": usage2_before,
            "rejected_still_counted": (r1.allowed, usage3),
            "gateway_rejected": ok1, "gateway_usage_after_reject": usage_gw,
            "reset_malformed": reset_raised, "limit_after_failed_reset": limit_after_failed_reset}

rec("quota_local", lambda: asyncio.get_event_loop().run_until_complete(quotas()))

# ---------- dp_budget static facts ----------
from app.services.dp_budget import dp_budget_ledger as dbl
rec("dp_mock_detection", lambda: (dbl._is_mock_session(object()), dbl._is_mock_session.__doc__))
import unittest.mock as um
rec("dp_mock_detection_mock_obj", lambda: dbl._is_mock_session(um.AsyncMock()))
rec("dp_loaded_never_invalidated", lambda: sorted({"loaded": len(dbl._loaded), "cache": len(dbl._cache)}.__items())[:2])

print(json.dumps(out, indent=1, default=str, ensure_ascii=False))
