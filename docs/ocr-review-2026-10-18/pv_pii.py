
import sys
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.pii_ner import (PIINERService, pii_ner_service, PIIMatch, PIIType, Severity,
                                  NERPatterns)
import app.services.pii_ner as M

print("settings PII_NER_ENGINE default:", repr(M._settings.PII_NER_ENGINE), "USE_ML:", M._settings.PII_NER_USE_ML)
print("singleton engine =", pii_ner_service._active_engine, "| lac loaded =", pii_ner_service._lac is not None)

# C14 engine=off but rule NER still runs
s = PIINERService(enable_ner=True, ner_engine="off")
r = s.detect("联系人张三 邮箱 zhang@example.com 住址不详请联系客服中心尽快处理")
print("C14 ner_engine =", r.ner_engine, "| layers =", sorted({m.layer for m in r.matches}),
      "| types =", [m.pii_type.value for m in r.matches])

# C15 overlap leaks, using the real _resolve_overlaps + _redact
s2 = PIINERService(ner_engine="rule")
mk = lambda t, a, b, sev: PIIMatch(PIIType[t], "X"*(b-a), a, b, sev, "ner")
cases = {
 "A drop-lower-sev tail": [mk("ADDRESS",0,15,Severity.HIGH), mk("EMAIL",10,40,Severity.MEDIUM)],
 "B replace-keeps-later-start": [mk("PHONE",0,20,Severity.HIGH), mk("ID_CARD",10,18,Severity.CRITICAL)],
 "C same-severity-longer-dropped": [mk("ADDRESS",0,10,Severity.HIGH), mk("ADDRESS",5,40,Severity.HIGH)],
}
text = "0123456789" * 6
for name, ms in cases.items():
    kept = s2._resolve_overlaps(sorted(ms, key=lambda m: (m.start, m.end)))
    red = s2._redact(text, kept)
    leaked = "".join(text[a:b] for a, b in [(m.start, m.end) for m in ms] if not any(k.start <= a and b <= k.end for k in kept))
    print("C15", name, "| kept", [(k.pii_type.value, k.start, k.end) for k in kept], "| leaked_chars", repr(leaked))

# C39 DOB false positive
r = s2.detect("本合同自2024年1月1日起实施，有效期至2026年12月31日")
print("C39", [(m.pii_type.value, m.value, m.severity.value, m.confidence) for m in r.matches])
print("C39 redacted:", r.redacted_text)

# C41 org over-capture
r = s2.detect("华为技术有限公司是一家总部位于深圳市的全球领先通信设备制造商主要从事通信设备制造与销售")
print("C41", [(m.pii_type.value, m.value) for m in r.matches])
# C42 address over-capture
r = s2.detect("住址不详请联系客服中心尽快处理谢谢合作")
print("C42", [(m.pii_type.value, m.value, m.severity.value) for m in r.matches], "| redacted:", r.redacted_text)
# C54 bank card without Luhn
r = s2.detect("订单号 6222021381100001234 已发货")
print("C54", [(m.pii_type.value, m.value, m.severity.value) for m in r.matches], "| redacted:", r.redacted_text)

# C16/C36 fake LAC
class FakeLAC:
    def __init__(self, out=None, boom=False):
        self.out, self.boom = out, boom
    def run(self, text):
        if self.boom: raise RuntimeError("model crashed")
        return self.out
svc = PIINERService(ner_engine="rule")
svc._lac = FakeLAC(boom=True); svc._active_engine = "lac"
r = svc.detect("联系人张三 电话 13800138000")
print("C16 LAC inference crashed -> ner_engine =", r.ner_engine, "layers =", sorted({m.layer for m in r.matches}),
      "types =", [m.pii_type.value for m in r.matches])
# C36: token absent from text (LAC normalisation) -> pos corruption for later tokens
svc2 = PIINERService(ner_engine="rule")
svc2._lac = FakeLAC((["张", "三", "公", "司", "北", "京"], ["PER", "PER", "O", "O", "GPE", "GPE"]))
svc2._active_engine = "lac"
txt = "张三公司驻京办 北京市"
r = svc2.detect(txt)
print("C36 fake-LAC tokens", ["张三","公司","北京市"], "-> matches",
      [(m.value, m.start, m.end, txt[m.start:m.end]) for m in r.matches if m.layer == "ner_lac"])
svc3 = PIINERService(ner_engine="rule"); svc3._active_engine = "lac"
svc3._lac = FakeLAC((["联系人", "张三", "位于", "北京市"], ["O", "PER", "O", "GPE"]))
t3 = "联系人：張三 位于 北京市"   # 张三 written with a variant char -> find() fails for it
r3 = svc3.detect(t3)
print("C36 find-miss -> matches", [(m.value, m.start, m.end, t3[m.start:m.end]) for m in r3.matches if m.layer == "ner_lac"])
print("threading in module:", hasattr(M, "threading"), "| lock:", any("lock" in d.lower() for d in dir(M.PIINERService)))

