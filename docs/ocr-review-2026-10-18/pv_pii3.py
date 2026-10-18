
import sys, json
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.pii_ner import NERPatterns
J = lambda o: print(json.dumps(o, ensure_ascii=True))
p2 = NERPatterns.ORGANIZATION_PATTERNS[1]
for t in ["公司成立于2010年并专注于智能数据处理技术的研发与推广服务", "单位：智能数据处理技术的研发与推广服务已有十年历史并且持续投入大量资源用于市场拓展"]:
    J({"org_pattern2": [m.group(1) for m in p2.finditer(t)], "lens": [len(m.group(1)) for m in p2.finditer(t)]})
p2a = NERPatterns.ADDRESS_PATTERNS[1]
J({"addr_pattern2_max_len": p2a.pattern})

