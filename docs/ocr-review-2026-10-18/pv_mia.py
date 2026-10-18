
import sys, traceback
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.mia_probe import MIAProbe
p = MIAProbe()

# C1/C2: all suspected, mean_loss slightly > 1
s1 = [{"sample_id": f"s{i}", "loss": 1.001, "confidence": 0.75} for i in range(10)]
r = p.analyze_batch(s1)
print("C1 all-suspected ls=-1.001 ->", r.risk_level.value, round(r.risk_score, 3), "suspected", r.suspected_members, "/", r.total_samples)

# C1b: exact -1.0 -> ZeroDivisionError?
s1b = [{"sample_id": f"s{i}", "loss": 1.0, "confidence": 0.75} for i in range(10)]
try:
    p.analyze_batch(s1b)
    print("C1b loss=1.0 -> no exception")
except ZeroDivisionError as e:
    print("C1b loss=1.0 -> ZeroDivisionError:", e)

# C2: zero suspected members, small losses
s2 = [{"sample_id": f"s{i}", "loss": 0.05, "confidence": 0.0} for i in range(10)]
r2 = p.analyze_batch(s2)
print("C2 zero-suspected ->", r2.risk_level.value, round(r2.risk_score, 4), "member_ratio", r2.member_ratio, "ls", round(r2.loss_separation, 4))

# C19: discontinuity at ratio==1.0
a = p.analyze_sample("a", 0.5, 0.0, loss_threshold=0.5)
b = p.analyze_sample("b", 0.5005, 0.0, loss_threshold=0.5)
c = p.analyze_sample("c", -1.0, 0.0, loss_threshold=0.5)
print("C19 loss_score@ratio=1.0 risk", round(a.risk_score,4), "| ratio=1.001 risk", round(b.risk_score,4), "| loss<0 risk", round(c.risk_score,4))

# C3: duplicated n-grams across reference texts
gen = "the quick brown fox jumps over the lazy dog again today"
refs = ["the quick brown fox jumps over the lazy dog again today"] * 5
ratio, snips = p.check_text_memorization(gen, refs, 4)
print("C3 memorization_ratio (5 identical refs) =", ratio, "snippets", len(snips), snips[:4])

# C18: ngram_size=0 and negative
for n in (0, -2):
    try:
        print("C18 n =", n, "->", p.check_text_memorization(gen, ["hello world", "again other"], n))
    except Exception as e:
        print("C18 n =", n, "-> EXC", type(e).__name__, e)

# C4: perplexity_threshold = 0
p0 = MIAProbe(perplexity_threshold=0.0)
try:
    print("C4 ppl=0 ->", p0.analyze_sample("x", 0.1, 0.5, perplexity=3.0).risk_score)
except ZeroDivisionError as e:
    print("C4 ppl_threshold=0 -> ZeroDivisionError")
print("C4 perplexity=0 score ->", p.analyze_sample("y", 0.1, 0.0, perplexity=0.0).risk_score)

