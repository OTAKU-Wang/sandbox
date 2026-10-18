from app.services.crypto_service import crypto_service as cs
f = cs.contract_sign_data
a = f("C1", "NO-1", "buyer", "T1", purpose="研究|purpose_scope:营销", purpose_scope=None)
b = f("C1", "NO-1", "buyer", "T1", purpose="研究", purpose_scope=["营销"])
print("collision1:", a == b, a.decode())
c = f("C1", "NO-1", "buyer", "T1", purpose="p", purpose_scope=["a|b"])
e = f("C1", "NO-1", "buyer", "T1", purpose="p", purpose_scope=["a", "b"])
print("collision2:", c == e, c.decode())
g = f("C1", "NO-1", "buyer", "T1", purpose="p", purpose_scope=[])
h = f("C1", "NO-1", "buyer", "T1", purpose="p", purpose_scope=None)
print("empty-vs-none:", g == h)
