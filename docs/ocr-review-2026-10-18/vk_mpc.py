"""Runtime probe: mpc_service claims M1..M8 (in-memory sqlite, no network)."""
import os
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_AUDIT_ENCRYPTION_KEY", "unit-test-audit-key")
os.environ.setdefault("CDS_SM3_FALLBACK_ALLOWED", "true")

import asyncio
import hashlib

import app.services.mpc_service as m
from app.services.mpc_service import MPCService, _PRIME, _mod_inverse, _lagrange_interpolate

OUT = []
def p(*a): OUT.append(" ".join(str(x) for x in a))

p("M5 _mod_inverse(0,p) =", _mod_inverse(0, _PRIME), "(silent 0, no error)")
try:
    _lagrange_interpolate([(1, 11), (1, 11)], _PRIME)
    p("M5b duplicate x-coord interpolation returned:",
      hex(_lagrange_interpolate([(1, 11), (1, 11)], _PRIME)))
except Exception as e:
    p("M5b duplicate x RAISED:", type(e).__name__, e)


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import select, delete, func
    from app.core.database import Base
    from app.models.mpc_key import MPCKeyRecord, MPCKeyShareRecord

    eng = create_async_engine("sqlite+aiosqlite:///:memory:",
                              poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with eng.begin() as c:
        await c.run_sync(lambda sc: Base.metadata.create_all(
            sc, tables=[MPCKeyRecord.__table__, MPCKeyShareRecord.__table__]))
    SM = async_sessionmaker(eng, expire_on_commit=False)
    svc = MPCService()

    # ---- M1: >32-byte secret is silently truncated -------------------------
    async with SM() as db:
        secret = bytes(range(1, 65))                      # 64 bytes, allowed by API (max_length=128 hex)
        k = await svc.split_key(db, secret=secret, threshold=2, total_shares=3)
        got = await svc.reconstruct_key(db, k.key_id, [s.share_id for s in k.shares[:2]])
        p("M1 64B secret: split ok, reconstructed len =", len(got),
          "equal-to-original =", got == secret, "| prefix", got[:8].hex())
        secret2 = b"\xff" * 32                            # 32B but value >= _PRIME
        k2 = await svc.split_key(db, secret=secret2, threshold=2, total_shares=3)
        got2 = await svc.reconstruct_key(db, k2.key_id, [s.share_id for s in k2.shares[:2]])
        p("M1b 32B all-0xFF (>= _PRIME): reconstructed == original =", got2 == secret2,
          "| returned", got2.hex()[:24], "... int value", int.from_bytes(got2, "big") == int.from_bytes(secret2, "big"))
        p("M1c _PRIME bit length =", _PRIME.bit_length(), "so any secret >= 2^254-ish is reduced")

        # ---- M2: duplicated share id -> wrong secret, NO error -------------
        sids = [s.share_id for s in k2.shares]
        good = await svc.reconstruct_key(db, k2.key_id, sids[:2])
        dup = await svc.reconstruct_key(db, k2.key_id, [sids[0], sids[0], sids[1]])
        p("M2 duplicate-share-id list accepted? secret changed =", good != dup,
          "| good", good.hex()[:16], "dup", dup.hex()[:16], "| raised: nothing")

    # ---- M4: _load_holders ordering / None filtering -----------------------
    async with SM() as db:
        k3 = await svc.split_key(db, secret=os.urandom(32), threshold=2, total_shares=3,
                                 holders=["ALICE", None, "CAROL"])
        holders = await svc._load_holders(db, k3.key_id)
        p("M4 _load_holders ->", holders, "(len", len(holders), "< total_shares 3; None dropped, no ORDER BY)")
        k4 = await svc.rotate_key(db, k3.key_id)
        binding = [(s.holder, s.index) for s in k4.shares]
        p("M4b after rotate holder->index:", binding, "(CAROL moved from index 3 to 2)")

    # ---- M3: rotate_key is not atomic --------------------------------------
    async with SM() as db:
        k5 = await svc.split_key(db, secret=os.urandom(32), threshold=2, total_shares=3,
                                 holders=["A", "B"])
        kid5 = k5.key_id
        orig = svc.split_key
        async def boom(*a, **kw):
            raise RuntimeError("simulated failure inside split_key")
        svc.split_key = boom
        try:
            await svc.rotate_key(db, kid5)
        except RuntimeError as e:
            p("M3 rotate_key raised:", e)
        svc.split_key = orig
        rec = (await db.execute(select(MPCKeyRecord).where(MPCKeyRecord.key_id == kid5))).scalar_one()
        nsh = (await db.execute(select(func.count()).select_from(MPCKeyShareRecord)
                                .where(MPCKeyShareRecord.key_id == kid5))).scalar()
        p("M3b state after failure: status =", rec.status, "| shares left =", nsh,
          "-> unrecoverable (old shares crypto-erased, new never written)")
        try:
            await svc.reconstruct_key(db, kid5, [])
        except ValueError as e:
            p("M3c reconstruct after failed rotation:", e)

    # ---- M6: orphan share row -> AttributeError (no FK enforcement) ---------
    async with SM() as db:
        k6 = await svc.split_key(db, secret=os.urandom(32), threshold=2, total_shares=2)
        sid = k6.shares[0].share_id
        await db.execute(delete(MPCKeyRecord).where(MPCKeyRecord.key_id == k6.key_id))
        await db.commit()
        orphan = (await db.execute(select(MPCKeyShareRecord)
                                   .where(MPCKeyShareRecord.share_id == sid))).scalar_one_or_none()
        p("M6 orphan share row survived key deletion (SQLite FK off by default):", orphan is not None)
        try:
            await svc.get_share(db, sid)
            p("M6b get_share returned normally")
        except Exception as e:
            p("M6b get_share RAISED:", type(e).__name__, "-> API has no handler for this (500)")

    # ---- M7: shares and egress audit lines share one key -------------------
    from app.core.config import get_settings
    st = get_settings()
    kms_key = hashlib.sha256((st.AUDIT_ENCRYPTION_KEY or st.JWT_SECRET_KEY).encode()).digest()
    p("M7 _share_cipher_key() == egress_audit key:", m._share_cipher_key() == kms_key,
      "| HKDF/domain-separation/key-version present:", False)

    # ---- M8: list_keys decrypts every share --------------------------------
    async with SM() as db:
        for _ in range(3):
            await svc.split_key(db, secret=os.urandom(32), threshold=2, total_shares=4)
        calls = {"n": 0}
        real = m._decrypt_share_value
        def counting(b):
            calls["n"] += 1
            return real(b)
        m._decrypt_share_value = counting
        keys = await svc.list_keys(db)
        m._decrypt_share_value = real
        p("M8 list_keys: keys =", len(keys), "SM4-GCM decryptions performed =", calls["n"],
          "| API uses only share_count (len(k.shares))")

    # ---- M9: 32-bit key_id space -------------------------------------------
    import uuid as U
    ids = {str(U.uuid4())[:8] for _ in range(20000)}
    p("M9 key_id length =", len(str(U.uuid4())[:8]), "hex chars (32-bit); 20k samples distinct:", len(ids))

    await eng.dispose()

asyncio.run(main())
print("\n".join(OUT))
