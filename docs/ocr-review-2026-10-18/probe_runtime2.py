import os, sys, time, tempfile, stat, inspect, ast, re
sys.path.insert(0, r"D:/workspace/sandbox")
from decimal import Decimal
from app.services import vision_multimodal_runtime as vmr
from app.services.trust_evaluator import TrustEvaluator, TrustLevel


def hr(t):
    print("\n" + "=" * 70 + "\nPROBE: " + t)


hr("V4 plate regex: is bare (?i) global in Python?")
for probe in (b"license_plate=ABC123", b"license_plate=abc123", b"plate=abc-123",
              b"plate=" + "京".encode() + b"A12345",
              b"license_plate=ABCDEFGHIJKLMN"):
    print("  %-34r -> %r" % (probe, vmr.LicensePlateStep().apply(probe)))
print("  global (?i) at start  :", re.sub(rb"(?i)(plate|license_plate)\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=abc123"))
print("  scoped (?i:...) form  :", re.sub(rb"(?i:(plate|license_plate))\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=abc123"))
print("  14-char value        :", re.sub(rb"(?i)(plate|license_plate)\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=ABCDEFGHIJKLMN"))

hr("V5 marker append corrupts stream + dedup bypass")
png = b"\x89PNG\r\n\x1a\nIHDR....\x00\x00\x00\x00IEND\xaeB`\x82"
o = vmr.LicensePlateStep().apply(png)
print("  bytes after IEND:", o[len(png):])
already = b"payload|cds_step=face_blur:gaussian_k51 tail PatientName=ZHANG"
print("  pre-existing marker anywhere -> step skipped:", vmr.FaceBlurStep().apply(already) == already)
d = b"x|cds_step=dicom_tags_stripped|y;PatientName=ZHANG^SAN"
print("  DICOM step skipped when marker pre-exists:", vmr.DICOMStripStep().apply(d) == d, "->", vmr.DICOMStripStep().apply(d))

hr("V6 default redaction rules / medical task")
dflt = vmr.RedactionRules()
print("  defaults:", dflt)
print("  pipeline:", vmr.build_image_pipeline(dflt).description)
print("  MEDICAL default rules:", vmr.VisionTrainingConfig(task=vmr.VisionTask.MEDICAL_IMAGING).redaction_rules)
pipe = vmr.build_image_pipeline(dflt)
ldr = vmr.SecureImageDataLoader(["dp"], pipe, 32)
ldr.load()
sample = ldr.get_batches()[0][0][0]
print("  sample reaching training:", sample)
print("  PatientName survives:", b"PatientName=" in sample, "| license_plate survives:", b"license_plate=" in sample)

hr("V7 session_id path traversal")
print("  vision    :", vmr.vision_training_runtime.run_training("../../Windows/win.ini", vmr.VisionTrainingConfig(num_epochs=1), ["dp"]).model_path)
print("  multimodal:", vmr.multimodal_training_runtime.run_training("C:/Windows/notepad", vmr.MultimodalTrainingConfig(num_epochs=1), ["i"], ["t"]).model_path)

hr("V8 batch_size <= 0 and num_epochs=0")
for bs in (0, -4):
    for label, fn in (("vision", lambda: vmr.SecureImageDataLoader(["dp"], vmr.ImagePipeline(), bs).get_batches()),
                      ("multimodal", lambda: vmr.SecureMultimodalDataLoader(["i"], ["t"], bs).get_batches())):
        try:
            fn()
            print(f"  {label} batch_size={bs}: no error")
        except Exception as e:
            print(f"  {label} batch_size={bs}: {type(e).__name__}: {e}")
r0 = vmr.vision_training_runtime.run_training("s", vmr.VisionTrainingConfig(num_epochs=0), ["dp"])
print("  num_epochs=0 -> error=%r total_epochs=%s final_loss=%s final_accuracy=%s watermark=%s" % (
    r0.error, r0.total_epochs, r0.final_loss, r0.final_accuracy, r0.watermark_injected))
print("  VisionTrainingConfig batch_size annotation/validation:",
      [f for f in vmr.VisionTrainingConfig.__dataclass_fields__], "-> no constraint on batch_size (plain int)")

hr("V9 _max_text_len never read")
src = inspect.getsource(vmr.SecureMultimodalDataLoader)
print("  '_max_text_len' occurrences:", src.count("_max_text_len"), "(assignment only)")
print("  load()/get_batches() bodies:")
print(inspect.getsource(vmr.SecureMultimodalDataLoader.load))
print(inspect.getsource(vmr.SecureMultimodalDataLoader.get_batches))

hr("V10 DICOM_STRIP ignores step.params['tags']")
p = vmr.build_image_pipeline(vmr.RedactionRules(dicom_strip=True))
step = [s for s in p.steps if s.step_type == vmr.PipelineStepType.DICOM_STRIP][0]
print("  configured params:", step.params)
print("  hardcoded tags   :", vmr.DICOMStripStep.SENSITIVE_TAGS)
print("  branch:", [l.strip() for l in inspect.getsource(vmr.SecureImageDataLoader._apply_pipeline_step).splitlines() if "DICOM" in l])
one = b"x;PatientName=A;InstitutionName=Hospital X"
print("  in :", one)
print("  out:", vmr.SecureImageDataLoader(["dp"], p, 32)._apply_pipeline_step(one, step))

hr("V11 load() ignores product_ids")
for ids in ([], ["bogus"], ["dp-1"]):
    l2 = vmr.SecureImageDataLoader(ids, vmr.ImagePipeline(), 10)
    l2.load()
    print("  product_ids=%-10r -> num_samples=%d" % (ids, l2.num_samples))
l3 = vmr.SecureMultimodalDataLoader([], [], 4)
l3.load()
print("  multimodal, empty ids -> num_samples =", l3.num_samples)
print("  load() mentions _product_ids:", "_product_ids" in inspect.getsource(vmr.SecureImageDataLoader.load))
print("  result for empty ids:", vars(vmr.vision_training_runtime.run_training("s", vmr.VisionTrainingConfig(num_epochs=1), []))["error"],
      "| epochs:", vmr.vision_training_runtime.run_training("s", vmr.VisionTrainingConfig(num_epochs=1), []).total_epochs)

hr("V12 get_batches per epoch + sm3 cost")
src = inspect.getsource(vmr.VisionModelTrainingRuntime.run_training)
print("  get_batches call sits after 'for epoch in range':",
      src.index("get_batches") > src.index("for epoch in range"))
from app.services.crypto_service import crypto_service
N = 3000
t0 = time.perf_counter()
for i in range(N):
    vmr._stable_unit_interval("vision", "s", "classification", ["dp-1"], 0, i)
dt = time.perf_counter() - t0
print("  _stable_unit_interval x%d: %.1f us/call" % (N, dt / N * 1e6))
import gmssl.sm3 as g3
print("  gmssl.sm3 module file:", g3.__file__)
print("  gmssl sm3 is pure python:", not hasattr(g3, "__compiled__"))
t0 = time.perf_counter()
import hashlib
for i in range(N):
    hashlib.sha256(b"x" * 32).hexdigest()
print("  hashlib.sha256 x%d: %.3f us/call" % (N, (time.perf_counter() - t0) / N * 1e6))
# 100 images / batch 32 = 4 batches per epoch
print("  per-epoch sm3 calls for default 100-image dataset: ~%d" % (4 + 1))

hr("V13 unused names in vision module")
tree = ast.parse(open(r"D:/workspace/sandbox/app/services/vision_multimodal_runtime.py", encoding="utf-8").read())
used = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Name):
        used.add(n.id)
    if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.value, ast.Name):
        used.add(n.value.id)
for c in ("uuid", "datetime", "timezone", "Any", "field", "crypto_service", "logger", "re", "time", "ABC", "abstractmethod", "dataclass", "Enum"):
    print("  %-16s used: %s" % (c, c in used))

hr("E6 does truncation ever change the LEVEL (real threshold crossings)?")
ev = TrustEvaluator()
mis = []
for dq in range(0, 101):
    for rep in range(0, 101):
        for sec in range(0, 101):
            exact = (Decimal(dq) * Decimal("0.30") + Decimal(dq) * Decimal("0.30")
                     + Decimal(rep) * Decimal("0.20") + Decimal(sec) * Decimal("0.20"))
            fl = dq * 0.30 + dq * 0.30 + rep * 0.20 + sec * 0.20
            t_exact = int(exact)
            t_float = int(min(100, max(0, fl)))
            if t_exact != t_float:
                mis.append((dq, rep, sec, t_exact, t_float, repr(fl)))
print("  truncation-vs-exact mismatches:", len(mis))
thr = {90: 0, 70: 0, 50: 0}
samples = []
for dq, rep, sec, te, tf, raw in mis:
    for t in thr:
        if te >= t > tf:
            thr[t] += 1
            if len(samples) < 8:
                samples.append((dq, rep, sec, raw, "exact=%d float=%d crosses %d" % (te, tf, t)))
print("  mismatches that cross a level threshold:", thr)
for s in samples:
    print("   ", s)

print("\nDONE")
