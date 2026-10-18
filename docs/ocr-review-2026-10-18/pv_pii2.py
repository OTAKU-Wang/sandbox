
import sys, json
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.pii_ner import PIINERService, PIIMatch, PIIType, Severity
J = lambda o: print(json.dumps(o, ensure_ascii=True))
s = PIINERService(ner_engine="rule")

def uncov(ms, kept):
    out = []
    for m in ms:
        covered = [(k.start, k.end) for k in kept if k.start <= m.start and m.end <= k.end]
        if covered: continue
        cur = m.start
        for a, b in sorted((k.start, k.end) for k in kept if k.start < m.end and k.end > m.start):
            if a > cur: out.append((cur, a))
            cur = max(cur, b)
        if cur < m.end: out.append((cur, m.end))
    return out

mk = lambda t, a, b, sev: PIIMatch(PIIType[t], "x"*(b-a), a, b, sev, "ner")
cases = {
 "A_low_sev_tail": [mk("ADDRESS",0,15,Severity.HIGH), mk("EMAIL",10,40,Severity.MEDIUM)],
 "B_replace_later_start": [mk("PHONE",0,20,Severity.HIGH), mk("ID_CARD",10,18,Severity.CRITICAL)],
 "C_same_sev_longer": [mk("ADDRESS",0,10,Severity.HIGH), mk("ADDRESS",5,40,Severity.HIGH)],
}
for name, ms in cases.items():
    ms = sorted(ms, key=lambda m: (m.start, m.end))
    kept = s._resolve_overlaps(ms)
    red = s._redact("0123456789"*6, kept)
    J({"case": name, "kept": [(k.pii_type.value, k.start, k.end) for k in kept],
       "unredacted_spans_left_by_dropped": uncov(ms, kept),
       "redacted_head": red[:26], "redacted_len": len(red)})

def det(tag, text):
    r = s.detect(text)
    J({"case": tag, "engine": r.ner_engine,
       "matches": [(m.pii_type.value, m.value, m.start, m.end, m.severity.value, m.confidence, m.layer) for m in r.matches],
       "redacted": r.redacted_text})
det("C39_contract_dates", "本合同自2024年1月1日起实施，有效期至2026年12月31日")
det("C41_org_greedy", "华为技术有限公司是一家总部位于深圳市的全球领先通信设备制造商主要从事通信设备制造与销售业务")
det("C41_company_then_text", "公司成立于二零一零年并专注于智能数据处理技术的研发与推广服务已有十年历史")
det("C42_addr_greedy", "住址不详请联系客服中心尽快处理谢谢合作")
det("C54_order_no", "订单号 6222021381100001234 已发货")

class FakeLAC:
    def __init__(self, out): self.out = out
    def run(self, text): return self.out
# pos becomes -1 when an UNTAGGED token is absent -> all later lookups start at the last char
svc = PIINERService(ner_engine="rule"); svc._active_engine = "lac"; svc._lac = FakeLAC((["XYZ", "北京"], ["O", "GPE"]))
t = "北京"
r = svc.detect(t)
J({"case": "C36_pos_minus1_miss", "text": t, "lac_output": [["XYZ","北京"],["O","GPE"]],
   "ner_lac_matches": [(m.value, m.start, m.end) for m in r.matches if m.layer == "ner_lac"],
   "note": "北京 is in text but produced no match"})
svc2 = PIINERService(ner_engine="rule"); svc2._active_engine = "lac"; svc2._lac = FakeLAC((["XYZ", "京"], ["O", "GPE"]))
t2 = "北京南站 京"
r2 = svc2.detect(t2)
J({"case": "C36_pos_minus1_misbind", "text": t2, "ner_lac_matches": [(m.value, m.start, m.end) for m in r2.matches if m.layer == "ner_lac"],
   "expected_first_occurrence": 1, "redacted": r2.redacted_text})
# tagged-token miss leaves pos stale (claim's described mechanism)
svc3 = PIINERService(ner_engine="rule"); svc3._active_engine = "lac"; svc3._lac = FakeLAC((["甲", "ZZZ", "京"], ["PER", "PER", "GPE"]))
t3 = "北京南站 京"
r3 = svc3.detect(t3)
J({"case": "C36_stale_pos", "text": t3, "ner_lac_matches": [(m.value, m.start, m.end) for m in r3.matches if m.layer == "ner_lac"]})

