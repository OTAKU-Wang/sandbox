
import sys, traceback
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.model_watermark import ModelWatermarkService, WatermarkMetadata
S = ModelWatermarkService()

# C5: numpy write silently lost
try:
    import numpy as np
    bits = S.generate_watermark_bits("o", "m", 16)
    before_copy = None
    sd = {"w": np.array([0.1234567 + 0.0001*i for i in range(64)], dtype=np.float64)}
    before = sd["w"].copy()
    res = S.embed_watermark(sd, bits, ["w"], 12345)
    changed = int((sd["w"] != before).sum())
    print("C5 numpy: success=%s changed_weights=%d/64" % (res.success, changed))
    v = S.verify_watermark(sd, bits, res.metadata)
    print("C5 numpy verify: match_ratio=%.3f passed=%s" % (v.match_ratio, v.passed))
except ImportError as e:
    print("C5 numpy unavailable:", e)

# C5b: 2-D non-contiguous torch? (torch likely absent) 
try:
    import torch
    sd2 = {"w": torch.arange(16, dtype=torch.float32).reshape(4,4).t()}
    b2 = S.generate_watermark_bits("o","m",4)
    before2 = sd2["w"].clone()
    r2 = S.embed_watermark(sd2, b2, ["w"], 7)
    print("C5b torch non-contig changed:", int((sd2["w"] != before2).sum()))
except ImportError:
    print("C5b torch not installed -> skipped (numpy path already proven)")

# C7: fallback order permutation (ordered_positions empty), 2 layers
sd3 = {"a": [0.1]*40, "b": [0.2]*40}
bits3 = S.generate_watermark_bits("o", "m", 20)
r3 = S.embed_watermark(sd3, bits3, ["a", "b"], 99)
md_ok = r3.metadata
v_ok = S.verify_watermark(sd3, bits3, md_ok)
md_bad = WatermarkMetadata(layer_positions=md_ok.layer_positions, key_seed=md_ok.key_seed, num_bits=md_ok.num_bits, ordered_positions=[])
v_bad = S.verify_watermark(sd3, bits3, md_bad)
print("C7 ordered_positions present: match=%.3f passed=%s | fallback (empty ordered): match=%.3f passed=%s"
      % (v_ok.match_ratio, v_ok.passed, v_bad.match_ratio, v_bad.passed))
print("C7 layer counts:", {k: len(v) for k, v in md_ok.layer_positions.items()})

# C24: empty watermark -> passed True
sd4 = {"a": [0.5]*20}
r4 = S.embed_watermark(sd4, [], ["a"], 5)
v4 = S.verify_watermark(sd4, [], r4.metadata)
print("C24 empty bits: embed.success=%s verify.passed=%s match=%.3f" % (r4.success, v4.passed, v4.match_ratio))
print("C24 generate_watermark_bits(num_bits=0) ->", S.generate_watermark_bits("o","m",0))

# C21: nested list with tuple inner
sd5 = {"a": [(0.1, 0.2), (0.3, 0.4)] * 10}
try:
    S.embed_watermark(sd5, [1,0,1,0], ["a"], 3)
    print("C21 nested tuple: no error")
except Exception as e:
    print("C21 nested tuple ->", type(e).__name__, e)

# C25: float32/low-precision round-trip (numpy float16)
try:
    import numpy as np
    sd6 = {"w": np.array([1.5 + 0.001*i for i in range(64)], dtype=np.float16)}
    bits6 = S.generate_watermark_bits("o","m",16)
    r6 = S.embed_watermark(sd6, bits6, ["w"], 77)
    v6 = S.verify_watermark(sd6, bits6, r6.metadata)
    print("C25 float16: embed.success=%s verify.match=%.3f passed=%s" % (r6.success, v6.match_ratio, v6.passed))
except ImportError:
    print("C25 skipped (no numpy)")

# C23: duplicate layer names
sd7 = {"a": [0.1]*30}
r7 = S.embed_watermark(sd7, S.generate_watermark_bits("o","m",10), ["a", "a"], 11)
v7 = S.verify_watermark(sd7, S.generate_watermark_bits("o","m",10), r7.metadata)
print("C23 duplicate layer_names accepted: embed.success=%s verify.match=%.3f passed=%s" % (r7.success, v7.match_ratio, v7.passed))

