
import sys, os, traceback
sys.path.insert(0, r'D:/workspace/sandbox')
os.environ.setdefault("CDS_DEBUG", "false")
try:
    from app.services.output_inspection import output_inspector as O
except Exception as e:
    print("IMPORT FAIL", type(e).__name__, e); sys.exit(0)

# C28: output=None
try:
    r = O.inspect(None, "u1", "s1", sandbox_mode="develop")
    print("C28 inspect(None) ->", r.passed, r.stage_results)
except Exception as e:
    print("C28 inspect(None) -> EXC", type(e).__name__ + ":", e)

# C27: harmless version string blocks the whole output (ip_address severity low)
try:
    r = O.inspect("release v1.2.3.4 fixed a bug", "u1", "s2", sandbox_mode="develop")
    print("C27 text 'v1.2.3.4' -> passed=", r.passed, "DLP=", r.stage_results.get("dlp_scan"),
          "finding_sev=", [(f.severity, f.type) for f in r.findings if f.stage=="dlp_scan"],
          "redacted=", repr(r.redacted_output))
except Exception as e:
    print("C27 EXC", type(e).__name__, e)

# C10: raw PII in findings.samples vs redacted_output
try:
    r = O.inspect("身份证 110101199003077758 手机 13800138000 邮箱 a@b.com", "u1", "s3", sandbox_mode="develop")
    d = [f for f in r.findings if f.stage=="dlp_scan"]
    print("C10 passed=", r.passed, "samples=", [f.samples for f in d])
    print("C10 redacted_output=", r.redacted_output)
except Exception as e:
    print("C10 EXC", type(e).__name__, e)

# C11: DP stage enabled, epsilon None -> vacuum not registered
try:
    r = O.inspect("hello", "u1", "s4", sandbox_mode="query", output_rows=None, dp_epsilon=None)
    print("C11 DP stage_result=", r.stage_results.get("differential_privacy"), "not_evaluated=", r.not_evaluated, "dp_applied=", r.dp_applied)
except Exception as e:
    print("C11 EXC", type(e).__name__, e, traceback.format_exc().splitlines()[-2:])

# C32: MIA fail-open / TypeError
print("C32 check_mia_risk({}) ->", O.check_mia_risk({}, 100))
try:
    print("C32 string fields ->", O.check_mia_risk({"confidence":"0.99","loss":"1.0"}, 100))
except Exception as e:
    print("C32 string fields -> EXC", type(e).__name__ + ":", e)

# C26: k-anonymity samples carry raw QI values
try:
    rows=[{"name":"张三","age":"30","addr":"北京市海淀区中关村大街1号"}]
    r = O.inspect("agg", "u1", "s5", sandbox_mode="query", output_rows=rows)
    k=[f for f in r.findings if f.stage=="k_anonymity"]
    print("C26 k-anon samples=", [f.samples for f in k])
except Exception as e:
    print("C26 EXC", type(e).__name__, e)

# C31/C13: signature degraded + signature field is a bare digest
try:
    r = O.inspect("hello world", "u1", "s6", sandbox_mode="develop")
    print("C31 signature=", (r.signature[:24] if r.signature else None), "degraded=", r.degraded, "sig_stage=", r.stage_results.get("signature"))
except Exception as e:
    print("C31 EXC", type(e).__name__, e)

# C9: custom/unknown scene falls back to query (which includes DLP)
print("C9 SCENE_RULES all contain dlp?", all("dlp_scan" in v for v in O.SCENE_RULES.values()), list(O.SCENE_RULES))
r = O.inspect("手机 13800138000", "u1", "s7", sandbox_mode="totally_custom_scene")
print("C9 unknown scene -> dlp stage:", r.stage_results.get("dlp_scan"), "passed:", r.passed)

# C29: dp_applied with no structured rows
print("C29 _apply_dp_noise(None, 0.5) ->", O._apply_dp_noise(None, 0.5))
# C50: add_laplace_noise epsilon=0
try:
    from app.services.output_inspection import dp_engine
    dp_engine.add_laplace_noise(1.0, 1.0, 0.0)
    print("C50 epsilon=0 -> no error")
except Exception as e:
    print("C50 epsilon=0 -> EXC", type(e).__name__ + ":", e)
# C33: reconstruction bypass by column order
src=[{"a":1,"b":2.0,"c":"007"}]*10
out1=[{"a":1,"b":2.0,"c":"007"}]
out2=[{"c":"007","b":2.0,"a":1}]
out3=[{"b":2,"a":1,"c":7}]
for tag,o in (("same",out1),("reordered",out2),("repr-diff",out3)):
    print("C33", tag, O.check_data_reconstruction(o, src))

