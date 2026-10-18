"""Behavioural probes for the runtime claims. Read-only on the repo."""
import os, sys, time, tempfile, subprocess, stat
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./probe_tmp.db")

SEP = "\n" + "=" * 70 + "\n"


def hr(t):
    print(SEP + "PROBE: " + t)


# ---------------- trust_evaluator: str-Enum hashing ----------------
from app.services.trust_evaluator import TrustEvaluator, TrustLevel, trust_evaluator

hr("E1 hash(TrustLevel.GOLD) vs hash('gold') vs hash('GOLD')")
print("mro:", [c.__name__ for c in TrustLevel.__mro__])
print("hash(member)      =", hash(TrustLevel.GOLD))
print("hash('gold')      =", hash("gold"))
print("hash('GOLD')      =", hash("GOLD"))
print("member == 'gold'  =", TrustLevel.GOLD == "gold")
print("dict lookup {'gold'->?}:", {TrustLevel.GOLD: 2}.get("gold", "MISS"))

hr("E2 can_access with plain strings / None / garbage")
ev = TrustEvaluator()
order = {TrustLevel.NEW: 0, TrustLevel.SILVER: 1, TrustLevel.GOLD: 2, TrustLevel.PLATINUM: 3}
print("level_order.get('new',0)  =", order.get("new", 0), " get('platinum',0) =", order.get("platinum", 0))
print("can_access('new','platinum')      =", ev.can_access("new", "platinum"))
print("can_access(NEW, 'platinum')       =", ev.can_access(TrustLevel.NEW, "platinum"))
print("can_access(TrustLevel.NEW, TrustLevel.PLATINUM) =", ev.can_access(TrustLevel.NEW, TrustLevel.PLATINUM))
print("can_access(NEW, None)             =", ev.can_access(TrustLevel.NEW, None))
print("can_access(TrustLevel.NEW, 'BOGUS') =", ev.can_access(TrustLevel.NEW, "BOGUS"))

hr("E3 double compliance penalty in evaluate_provider")
for v in (1, 2, 3, 4, 5, 10):
    s = ev.evaluate_provider("p", total_products=10, avg_quality=80.0, violations=v, certifications=["ISO27001"])
    print(f"violations={v:>2} -> compliance={s.compliance:>3} score={s.score:>3} level={s.level.value}")

hr("E4 no input clamping")
s = ev.evaluate(data_quality_score=500, compliance_score=500, reputation_score=500, security_score=500)
print("inputs 500 ->", s.score, s.data_quality, s.compliance, s.reputation, s.security, s.level.value)
s = ev.evaluate(data_quality_score=-40, compliance_score=-40, reputation_score=-40, security_score=-40)
print("inputs -40 ->", s.score, s.data_quality, s.level.value)
s = ev.evaluate(data_quality_score=90, data_age_days=-9999)
print("data_age_days=-9999 -> quality", s.data_quality, "factors", s.factors)

hr("E5 float weight truncation off-by-one")
bad = 0
examples = []
for dq in range(0, 101):
    for rep in range(0, 101):
        for sec in range(0, 101):
            scores = {"data_quality": dq, "compliance": dq, "reputation": rep, "security": sec}
            tot = sum(scores[k] * ev.WEIGHTS[k] for k in ev.WEIGHTS)
            trunc = int(min(100, max(0, tot)))
            rnd = int(round(tot, 6))
            if trunc != rnd:
                bad += 1
                if len(examples) < 6:
                    examples.append((dq, rep, sec, repr(tot), trunc, rnd))
print("trunc != round for", bad, "of 10**3 combos; sample (dq,rep,sec,raw,trunc,round):")
for e in examples:
    print("   ", e)
s = ev.evaluate(data_quality_score=90, compliance_score=90, reputation_score=90, security_score=90)
print("all-90 -> raw repr:", repr(sum([90*0.30, 90*0.30, 90*0.20, 90*0.20])), "score:", s.score, "level:", s.level.value)

# ---------------- circuit breaker ----------------
from app.services.task_circuit_breaker import TaskCircuitBreaker, CircuitConfig, CircuitState

hr("C1 failure window decay is dead code")
b = TaskCircuitBreaker(CircuitConfig(failure_threshold=3, failure_window_seconds=1, recovery_timeout_seconds=0))
for i in range(3):
    b.record_failure("s", "e")
    st = b.get_stats("s")
    print(f"  failure #{i+1}: failure_count={st.failure_count} state={st.state.value}")
print("=> 3 failures recorded with window=1s still opened (no decay)")
b2 = TaskCircuitBreaker(CircuitConfig(failure_threshold=3, failure_window_seconds=0))
for i in range(3):
    b2.record_failure("s", "e")
print("window=0 (i.e. decay must always fire) -> state:", b2.get_stats("s").state.value,
      "failure_count:", b2.get_stats("s").failure_count)

hr("C2 record_success in CLOSED does not clear failure_count")
b3 = TaskCircuitBreaker(CircuitConfig(failure_threshold=3))
b3.record_failure("s"); b3.record_failure("s")
b3.record_success("s"); b3.record_success("s"); b3.record_success("s")
print("2 failures then 3 successes -> failure_count:", b3.get_stats("s").failure_count,
      "state:", b3.get_stats("s").state.value)
b3.record_failure("s")
print("one more failure -> state:", b3.get_stats("s").state.value, "(total_failures=%d)" % b3.get_stats("s").total_failures)

hr("C3 HALF_OPEN lets every caller through; first success closes")
b4 = TaskCircuitBreaker(CircuitConfig(failure_threshold=2, recovery_timeout_seconds=0))
b4.record_failure("s"); b4.record_failure("s")
print("state after threshold:", b4.get_stats("s").state.value)
time.sleep(0.01)
allowed = [b4.can_execute("s")[0] for _ in range(10)]
print("can_execute x10 in HALF_OPEN:", allowed, "state:", b4.get_stats("s").state.value)
b4.record_success("s")
print("after 1 success ->", b4.get_stats("s").state.value, "failure_count:", b4.get_stats("s").failure_count)
for _ in range(9):
    b4.record_failure("s")
print("then 9 failed probes -> state:", b4.get_stats("s").state.value,
      "circuit_opened_count:", b4.get_stats("s").circuit_opened_count)

hr("C4 read-only methods create entries (unbounded growth)")
b5 = TaskCircuitBreaker()
for i in range(1000):
    b5.can_execute(f"session-{i}")
print("after 1000 can_execute() calls, len(_sessions) =", len(b5._sessions))
b5.get_stats("readonly-probe")
print("after get_stats('readonly-probe'), present:", "readonly-probe" in b5._sessions)

hr("C5 no lock / no threading import in module")
import threading, inspect
import app.services.task_circuit_breaker as tcb
src = inspect.getsource(tcb)
print("imports threading:", "import threading" in src, "| uses Lock:", "Lock(" in src)
print("get_all_open_circuits iterates dict directly:",
      "for sid, stats in self._sessions.items()" in src)

# ---------------- tee_capability ----------------
from app.services.tee_capability import TEECapabilityDetector
import shutil

hr("T1 unknown explicit provider -> fake 'not supported'")
d = TEECapabilityDetector()
for m in ("tdx_guest", "sev-snp", "sgx ", "TDx"):
    c = d.detect(m)
    print(f"  detect({m!r}) -> provider={c.provider!r} hw={c.hardware_available} evidence={c.evidence} reason={c.reason!r}")

hr("T2 _detect_provider accepts a REGULAR FILE as device evidence")
with tempfile.TemporaryDirectory() as td:
    fake = os.path.join(td, "sev-guest")
    with open(fake, "wb") as fh:
        fh.write(b"not a device")
    evd = TEECapabilityDetector._detect_provider({"devices": (fake,), "commands": ()})
    print("  regular file (mode=%s, ischar=%s) -> evidence %r" % (
        oct(os.stat(fake).st_mode), stat.S_ISCHR(os.stat(fake).st_mode), evd))

hr("T3 shutil.which trusts attacker-writable PATH")
with tempfile.TemporaryDirectory() as td:
    exe = os.path.join(td, "sgx-detect.bat")
    with open(exe, "w") as fh:
        fh.write("@echo off\n")
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = td + os.pathsep + old
    try:
        c = TEECapabilityDetector().detect("sgx")
        print("  after planting sgx-detect in a writable dir on PATH:")
        print("  provider=%r hardware_available=%s evidence=%r reason=%r" % (
            c.provider, c.hardware_available, c.evidence, c.reason))
    finally:
        os.environ["PATH"] = old
print("  which() with no path= honours cwd when PATH has an empty entry:")
old = os.environ.get("PATH", "")
os.environ["PATH"] = "" + os.pathsep + old
try:
    print("    which('sgx-detect') with empty PATH entry:", shutil.which("sgx-detect"))
finally:
    os.environ["PATH"] = old

hr("T4 detect() output shape when mode=software")
print(" ", TEECapabilityDetector().detect("software"))

# ---------------- vision runtime ----------------
from app.services import vision_multimodal_runtime as vmr

hr("V1 FaceBlurStep does not touch pixels")
jpeg = bytes.fromhex("ffd8ffe000104a46494600") + b"FACEPIXELSDATA" + bytes.fromhex("ffd9")
out = vmr.FaceBlurStep().apply(jpeg)
print("  input  =", jpeg[:24], "... len", len(jpeg))
print("  output =", out[:24], "... len", len(out))
print("  original pixel bytes preserved verbatim:", out[: len(jpeg)] == jpeg)
print("  appended tail:", out[len(jpeg):])

hr("V2 DICOM regex vs a real DICOM part-10 preamble/LE tag encoding")
raw_tag = b"\x10\x00\x10\x01" + b"PN" + b"\x0e\x00" + b"ZHANG^SAN".ljust(10)
print("  real DICOM element (0010,0010) PN 'ZHANG^SAN' bytes:", raw_tag)
r = vmr.DICOMStripStep().apply(raw_tag)
print("  after DICOMStripStep, patient name still present:", b"ZHANG^SAN" in r)
texty = b"Study;PatientName=ZHANG^SAN;PatientID=12345"
r2 = vmr.DICOMStripStep().apply(texty)
print("  text form before:", texty)
print("  text form after :", r2)

hr("V3 watermark inject returns True without writing anything")
import inspect as _i
print(vmr.VisionModelTrainingRuntime._inject_watermark.__doc__ or "(no docstring)")
print(_i.getsource(vmr.VisionModelTrainingRuntime._inject_watermark))
res = vmr.vision_training_runtime.run_training("sess-demo", vmr.VisionTrainingConfig(num_epochs=1), ["dp-1"])
print("  watermark_injected =", res.watermark_injected, " model_path =", res.model_path)
print("  model_path exists on disk:", os.path.exists(res.model_path))

hr("V4 plate regex: is (?i) global in Python? lowercase values")
for probe in (b"license_plate=ABC123", b"license_plate=abc123", b"plate=abc-123", b"plate=\u4eacA12345".encode(),
              b"license_plate=ABCDEFGHIJKLMN"):  # 14 chars > 12
    out = vmr.LicensePlateStep().apply(probe)
    print(f"  {probe!r:40s} -> {out!r}")
import re as _re
print("  bare (?i) at pattern start is a GLOBAL flag:",
      _re.sub(rb"(?i)(plate|license_plate)\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=abc123"))
print("  scoped form (?i:...) would NOT affect the class:",
      _re.sub(rb"(?i:(plate|license_plate))\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=abc123"))

hr("V5 marker append corrupts the stream + silent dedup bypass")
png = b"\x89PNG\r\n\x1a\n" + b"IHDR....IEND\xaeB`\x82"
o = vmr.LicensePlateStep().apply(png)
print("  bytes after IEND:", o[len(png):])
already = b"payload|cds_step=face_blur:gaussian_k51 tail"
print("  pre-existing marker anywhere -> unchanged:", vmr.FaceBlurStep().apply(already) == already)

hr("V6 default redaction rules in a medical task")
dflt = vmr.RedactionRules()
print("  defaults:", dflt)
pipe = vmr.build_image_pipeline(dflt)
print("  pipeline steps:", pipe.description)
print("  MEDICAL_IMAGING uses the same defaults:",
      vmr.VisionTrainingConfig(task=vmr.VisionTask.MEDICAL_IMAGING).redaction_rules)
ldr = vmr.SecureImageDataLoader(["dp"], pipe, 32)
ldr.load()
sample = ldr.get_batches()[0][0][0]
print("  sample that reaches training:", sample)
print("  PatientName survives:", b"PatientName=" in sample, " license_plate survives:", b"license_plate=" in sample)

hr("V7 session_id path traversal")
res = vmr.vision_training_runtime.run_training("../../etc/passwd", vmr.VisionTrainingConfig(num_epochs=1), ["dp"])
print("  vision model_path   =", res.model_path)
res2 = vmr.multimodal_training_runtime.run_training("/absolute/evil", vmr.MultimodalTrainingConfig(num_epochs=1), ["i"], ["t"])
print("  multimodal model_path =", res2.model_path)

hr("V8 batch_size <= 0")
for bs in (0, -4):
    try:
        vmr.SecureImageDataLoader(["dp"], vmr.ImagePipeline(), bs).get_batches()
        print(f"  vision batch_size={bs}: no error")
    except Exception as e:
        print(f"  vision batch_size={bs}: {type(e).__name__}: {e}")
    try:
        vmr.SecureMultimodalDataLoader(["i"], ["t"], bs).get_batches()
        print(f"  multimodal batch_size={bs}: no error")
    except Exception as e:
        print(f"  multimodal batch_size={bs}: {type(e).__name__}: {e}")
r0 = vmr.vision_training_runtime.run_training("s", vmr.VisionTrainingConfig(num_epochs=0), ["dp"])
print("  num_epochs=0 -> error=%r total_epochs=%s final_loss=%s final_accuracy=%s watermark=%s" % (
    r0.error, r0.total_epochs, r0.final_loss, r0.final_accuracy, r0.watermark_injected))

hr("V9 max_text_len is stored but never read")
src = _i.getsource(vmr.SecureMultimodalDataLoader)
print("  '_max_text_len' occurrences in loader source:", src.count("_max_text_len"))
print("  body:"); print(src)

hr("V10 DICOM_STRIP step ignores step.params['tags']")
p = vmr.build_image_pipeline(vmr.RedactionRules(dicom_strip=True))
step = [s for s in p.steps if s.step_type == vmr.PipelineStepType.DICOM_STRIP][0]
print("  configured params:", step.params)
print("  hardcoded SENSITIVE_TAGS:", vmr.DICOMStripStep.SENSITIVE_TAGS)
print("  _apply_pipeline_step DICOM branch:",
      [l.strip() for l in _i.getsource(vmr.SecureImageDataLoader._apply_pipeline_step).splitlines()
       if "DICOM" in l])
one = b"x;PatientName=A;InstitutionName=Hospital X"
print("  payload:", one)
print("  result :", vmr.SecureImageDataLoader(["dp"], p, 32)._apply_pipeline_step(one, step))

hr("V11 load() ignores product_ids / authorisation")
for ids in ([], ["bogus"], ["dp-1"]):
    l2 = vmr.SecureImageDataLoader(ids, vmr.ImagePipeline(), 10)
    l2.load()
    print("  product_ids=%-12r -> num_samples=%d" % (ids, l2.num_samples))
l3 = vmr.SecureMultimodalDataLoader([], [], 4)
l3.load()
print("  multimodal with empty product ids -> num_samples =", l3.num_samples)
src = _i.getsource(vmr.SecureImageDataLoader.load)
print("  load() references _product_ids:", "_product_ids" in src)

hr("V12 get_batches re-run per epoch + sm3 per batch")
src = _i.getsource(vmr.VisionModelTrainingRuntime.run_training)
print("  get_batches inside epoch loop:", "for epoch in range" in src and src.index("get_batches") > src.index("for epoch in range"))
import time as _t
from app.services.crypto_service import crypto_service
N = 2000
t0 = _t.perf_counter()
for i in range(N):
    vmr._stable_unit_interval("vision", "s", "classification", ["dp-1"], 0, i)
dt = _t.perf_counter() - t0
print("  _stable_unit_interval x%d: %.1f ms total, %.1f us/call" % (N, dt * 1000, dt / N * 1e6))

hr("V13 unused imports in vision module")
import ast
tree = ast.parse(open(r"D:/workspace/sandbox/app/services/vision_multimodal_runtime.py", encoding="utf-8").read())
names = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Name):
        names.add(n.id)
    if isinstance(n, ast.Attribute):
        pass
used = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Name):
        used.add(n.id)
    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
        used.add(n.value.id)
for cand in ("uuid", "datetime", "timezone", "Any", "field", "crypto_service", "logger", "re", "time", "ABC", "abstractmethod", "dataclass", "Enum"):
    print(f"  {cand:16s} referenced as a name: {cand in used}")

# ---------------- clickhouse_schema ----------------
from app.services.clickhouse_schema import ClickHouseSchema, ColumnDef, IndexDef, clickhouse_schema as chs

hr("K1 gateway_metering definition")
t = chs._tables["gateway_metering"]
print("  engine   :", t.engine)
print("  order_by :", t.order_by)
print("  cols     :", [c.name for c in t.columns])
print("  generated DDL:\n" + chs.generate_create_sql("gateway_metering"))

hr("K2 validate_schema_completeness is a no-op for registered tables")
for name in chs.list_tables():
    v = chs.validate_schema_completeness(name)
    print("  %-28s valid=%s missing=%s" % (name, v["valid"], v["missing_columns"]))
print("  unknown table:", chs.validate_schema_completeness("nope"))
t.columns.append(ColumnDef("timestamp", "DateTime64(3, 'UTC')"))   # duplicate column
t.columns.append(ColumnDef("ghost_col", "UInt8"))
t.order_by = "(timestamp, does_not_exist)"
t.ttl = "missing_col + INTERVAL 3 YEAR"
t.indexes.append(IndexDef("idx_ghost", "ghost_index_expr", "bloom_filter"))
print("  after injecting duplicate col + dangling order_by/ttl/index refs:")
print("  validate:", chs.validate_schema_completeness("gateway_metering"))
t.columns.pop(); t.columns.pop()

hr("K3 unknown table name is indistinguishable")
print("  get_columns('nope')         =", chs.get_columns("nope"))
print("  get_table_info('nope')      =", chs.get_table_info("nope"))
print("  generate_create_sql('nope') =", repr(chs.generate_create_sql("nope")))

hr("K4 ColumnDef.comment never set / never emitted")
src_all = open(r"D:/workspace/sandbox/app/services/clickhouse_schema.py", encoding="utf-8").read()
print("  'comment=' occurrences:", src_all.count("comment="))
print("  'col.comment' occurrences:", src_all.count("col.comment"))
print("  COMMENT in generated SQL:", "COMMENT" in chs.generate_create_sql("audit_events"))
with_comment = ColumnDef("x", "UInt8", "0", "hello")
print("  ColumnDef(name,dtype,default,comment) with comment='hello' -> DDL:",
      "COMMENT" in ClickHouseSchema().generate_create_sql("audit_events") )

hr("K5 logger unused")
import re as _r
print("  'logger.' occurrences:", src_all.count("logger."))
print("  'logging' occurrences:", src_all.count("logging"))

hr("K6 unused imports in clickhouse_schema / task_circuit_breaker")
for path, cands in ((r"D:/workspace/sandbox/app/services/clickhouse_schema.py", ("Any", "field", "logging")),
                    (r"D:/workspace/sandbox/app/services/task_circuit_breaker.py", ("field", "dataclass", "time"))):
    tree = ast.parse(open(path, encoding="utf-8").read())
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            used.add(n.value.id)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
            used.add(n.value.id)
    print(" ", os.path.basename(path), {c: (c in used) for c in cands})
print("\nDONE")
