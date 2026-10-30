"""OCR-A105/A106 修复回归针 — 输出代理两条真值 + 证书链校验与撤销族.

A105（app_output_proxy / streaming_inspector，「接线即 high」）：
  1. 4KB 字节切块 + 逐块 ``errors="ignore"`` 解码 → 跳块边界 PII 永不命中、
     多字节字符被静默丢弃、全有全无式写回把受损 body 发还给客户端；
  2. Content-Type 前缀白名单，头缺失/未列举**直接免检** → 一个头绕过全部泄露控制。
  修复后：增量 UTF-8（零丢失）+ 边界保留带（跨块 PII 必中且只脱一次）+ ``flush()``
  定局流尾；所有内容类型都过审——文本型命中脱敏、非文本/未声明命中阻断、
  不可解码 fail-closed 阻断，绝不写回受损/未审 body。

A106（certificate_service / api/certificates.py）：
  ``verify_chain`` 从不验父签名、``verify_certificate`` 用叶子自己的公钥验自己的
  签名（自签仹即信）、信任锚=「库里任意 is_ca 行」、CRL 每次现生成一把一次性密钥
  签名、``revoke_certificate`` 吞掉会话终止失败仍记 ``cascade.complete``。
  修复后：逐环父公钥验签 + 根必须自签验签过 + 链必须终止于根；CRL 只认本地库签发
  私钥否则诚实 unsigned；级联失败如实记 ``cascade.incomplete``。
"""
import datetime as dt
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.services.app_output_proxy import CDSOutputInspector
from app.services.streaming_inspector import StreamingInspector, StreamingInspectorPool

needs_gmssl = pytest.mark.skipif(
    not __import__("app.services.crypto_service", fromlist=["_HAS_GMSSL"])._HAS_GMSSL,
    reason="SM2 验签/验链需要 gmssl（fail-closed 已由 test_ocr_sm2_fixed_width 的结构针钉）",
)

# ────────────────────────────── A105：输出代理 ──────────────────────────────

IDN = b"110101199001011234"     # 18B 中国身份证（ASCII，正则 CHINESE_ID）
PHONE = b"13812345678"


def _proxy(chunk_size: int = 8, **pool_kw) -> CDSOutputInspector:
    return CDSOutputInspector(pool=StreamingInspectorPool(**pool_kw), chunk_size=chunk_size)


class TestA105OutputProxyTruths:
    def test_pii_spanning_chunk_boundary_is_caught_and_redacted(self):
        """真值 1 的逃逸形态：PII 恰好被 4KB（此处 8B）字节边界劈开。"""
        body = b"id=" + IDN + b";end"
        for cut in range(4, len(body)):  # 逐一切点：任何边界都不许逃逸
            d = _proxy(chunk_size=cut).inspect_body(body, f"s-{cut}", "text/plain")
            assert d.action == "redact", f"chunk={cut} 边界 PII 逃逸：{d}"
            assert IDN not in d.body, f"chunk={cut} 未脱敏：{d.body}"

    def test_multibyte_across_chunks_is_lossless(self):
        """真值 1 的丢字节形态：多字节字符被切块 + errors=ignore 静默吞掉。"""
        clean = "中文测试漢字αβγ→ok，".encode("utf-8") * 4
        d = _proxy(chunk_size=5).inspect_body(clean, "s-mb", "text/plain")
        assert d.action == "pass" and d.body == clean, "干净多字节 body 必须字节级原样"

    def test_undecodable_text_body_blocks_instead_of_damaging(self):
        """不可解码的文本型 body：fail-closed 阻断，绝不写回被 ignore 咬残的 body。"""
        d = _proxy(chunk_size=4).inspect_body(b"ok\xff\xfe\x00bad", "s-dec", "application/json")
        assert d.action == "block" and d.reason == "output_undecodable", d

    def test_missing_content_type_no_longer_exempts(self):
        """真值 2：头缺失 = 未声明类型，PII 命中照阻断；干净则字节原样。"""
        d = _proxy().inspect_body(b"call me " + PHONE, "s-noct", "")
        assert d.action == "block" and d.reason == "pii_in_non_text_body", d
        d2 = _proxy().inspect_body(b"clean body", "s-noct2", "")
        assert d2.action == "pass" and d2.body == b"clean body"

    def test_binary_content_type_cannot_smuggle_pii(self):
        """真值 2 的绕过形态：谎报 image/png 藏 ASCII PII → 阻断；真二进制干净则原样。"""
        smuggle = b"\x89PNG\r\n\x1a\n" + b" hidden " + PHONE + b"\x00"
        d = _proxy().inspect_body(smuggle, "s-png", "image/png")
        assert d.action == "block" and d.reason == "pii_in_non_text_body", d
        ok = _proxy().inspect_body(b"\x89PNG\r\n\x1a\n\x00\x01\x02", "s-png2", "image/png")
        assert ok.action == "pass" and ok.body == b"\x89PNG\r\n\x1a\n\x00\x01\x02"

    def test_tail_pii_in_boundary_carry_is_resolved_by_flush(self):
        """流尾保留带里的最后一枚 PII 必须由 flush 定局（不漂不丢）。"""
        body = b"x" * 100 + b" " + PHONE
        d = _proxy(chunk_size=16).inspect_body(body, "s-tail", "text/plain")
        assert d.action == "redact" and PHONE not in d.body
        assert len(d.body) == len(body), "脱敏是等长替换，总宽必须守恒"

    def test_circuit_breaker_still_blocks(self):
        add = CDSOutputInspector(pool=StreamingInspectorPool(), chunk_size=64)
        body = (b"tel " + PHONE + b" id " + IDN + b" ") * 20
        d = add.inspect_body(body, "s-cb", "text/plain")
        assert d.action == "block" and d.reason == "pii_circuit_breaker", d

    def test_redaction_is_exactly_once_no_double_replace(self):
        """跨块定局的 PII 只被脱一次（carry 重扫不得重复改写）。"""
        insp = StreamingInspector(session_id="s-once", chunk_size=7)
        body = b"pre " + IDN + b" post"
        out = b""
        for i in range(0, len(body), 7):
            chunk_out, res = insp.inspect_chunk(body[i:i + 7])
            out += chunk_out
            assert sum(len(h.value) for h in res.pii_hits) <= len(IDN)
        tail_out, tail_res = insp.flush()
        out += tail_out
        assert IDN not in out and b"pre " in out and b" post" in out
        assert out.count(b"*") == len(IDN) - 2, f"重复脱敏/过度脱敏：{out}"
        # 18 位身份证同时落入 CHINESE_ID 与 BANK_CARD 两个模式——同 span 各计一次命中，
        # 但替换只落一份（span 相同）：钉「改写不重复」而不是钉命中条数。
        assert insp.stats["total_pii_hits"] >= 1
        assert out.count(b"1") + out.count(b"4") >= 2  # 首尾保留位未被二次改写


# ────────────────────────── A106：证书链与撤销族 ──────────────────────────

ROOT_DN = "CN=CDS Root CA,O=CDS,C=CN"


async def _issue(db, svc, user_id, cert_type, subject, **kw):
    return await svc.issue_certificate(db, user_id, cert_type, subject=subject, **kw)


@pytest.fixture
def svc():
    from app.services.certificate_service import CertificateService
    return CertificateService()


@needs_gmssl
class TestA106ChainVerification:
    async def test_real_chain_is_cryptographically_verified(self, db_session, svc, make_user):
        from app.models.certificate import CertType
        _, uid_str = await make_user(prefix="a106u")
        uid = uuid.UUID(uid_str)
        root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
        child = await _issue(db_session, svc, uid, CertType.IDENTITY.value,
                             "CN=alice,O=CDS,C=CN", parent_cert_id=root.id)

        r = await svc.verify_chain(db_session, child.id)
        assert r["valid"] is True and r["signatures_verified"] is True, r
        assert r["trust_anchor"]["type"] == "self-signed-root"
        assert all(entry["signature_verified"] for entry in r["chain"]), r["chain"]

    async def test_orphan_self_signed_no_longer_trusted(self, db_session, svc, make_user):
        """旧缺陷正中：无 parent 的自签单行链过去直接 valid true。"""
        from app.models.certificate import CertType
        _, uid_str = await make_user(prefix="a106o")
        uid = uuid.UUID(uid_str)
        orphan = await _issue(db_session, svc, uid, CertType.IDENTITY.value, "CN=mallory,O=CDS,C=CN")
        r = await svc.verify_chain(db_session, orphan.id)
        assert r["valid"] is False and "no trust anchor" in r["error"], r

    async def test_grafted_forged_link_rejected_by_parent_signature(self, db_session, svc, make_user):
        """DB 行嫁接（issuer 字符串照抄 + parent_cert_id 指到真 CA）——字符串比对会被骗过，
        父公钥验签不会。这是「自签仹即信」的判决性证明。"""
        from app.models.certificate import CertType
        _, uid_str = await make_user(prefix="a106g")
        uid = uuid.UUID(uid_str)
        root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
        forged = await _issue(db_session, svc, uid, CertType.IDENTITY.value, "CN=mallory,O=CDS,C=CN")
        forged.issuer = ROOT_DN
        forged.parent_cert_id = root.id
        await db_session.flush()

        r = await svc.verify_chain(db_session, forged.id)
        assert r["valid"] is False and "NOT signed by its parent key" in r["error"], r

    async def test_self_signed_root_without_ca_bit_rejected(self, db_session, svc, make_user):
        """自签 + issuer==subject 但 PEM basicConstraints 非 CA → 不能当锚。"""
        from app.models.certificate import Certificate, CertType, CertStatus
        from app.services.crypto_service import crypto_service
        _, uid_str = await make_user(prefix="a106c")
        uid = uuid.UUID(uid_str)
        kp = crypto_service.generate_keypair()
        pem = svc._build_cert_pem(
            serial=f"CDS-FAKE-{uuid.uuid4().hex[:8]}", subject=ROOT_DN, issuer=ROOT_DN,
            public_key=kp.public_key, not_before=dt.datetime.now(dt.timezone.utc),
            not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10),
            is_ca=False, private_key=kp.private_key,
        )
        row = Certificate(user_id=uid, cert_type=CertType.IDENTITY.value,
                          status=CertStatus.ACTIVE.value, subject=ROOT_DN, issuer=ROOT_DN,
                          serial_number="CDS-FAKE-ANCHOR", public_key=kp.public_key,
                          certificate_pem=pem, is_ca=False,
                          not_before=dt.datetime.now(dt.timezone.utc),
                          not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10))
        db_session.add(row)
        await db_session.flush()
        r = await svc.verify_chain(db_session, row.id)
        assert r["valid"] is False and "not a CA" in r["error"], r

    async def test_root_with_broken_self_signature_rejected(self, db_session, svc, make_user):
        """信任锚必须自签验签通过：拿 A 的私钥签、装 B 的公钥的『根』必须被拒。"""
        from app.models.certificate import Certificate, CertType, CertStatus
        from app.services.crypto_service import crypto_service
        _, uid_str = await make_user(prefix="a106b")
        uid = uuid.UUID(uid_str)
        kpa = crypto_service.generate_keypair()
        kpb = crypto_service.generate_keypair()
        pem = svc._build_cert_pem(
            serial="CDS-BROKEN-ROOT", subject=ROOT_DN, issuer=ROOT_DN,
            public_key=kpb.public_key,  # SPKI 装 B
            not_before=dt.datetime.now(dt.timezone.utc),
            not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10),
            is_ca=True, private_key=kpa.private_key,  # 用 A 签 → 自洽验签必炸
        )
        row = Certificate(user_id=uid, cert_type=CertType.PLATFORM.value,
                          status=CertStatus.ACTIVE.value, subject=ROOT_DN, issuer=ROOT_DN,
                          serial_number="CDS-BROKEN-ROOT", public_key=kpb.public_key,
                          certificate_pem=pem, is_ca=True,
                          not_before=dt.datetime.now(dt.timezone.utc),
                          not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10))
        db_session.add(row)
        await db_session.flush()
        r = await svc.verify_chain(db_session, row.id)
        assert r["valid"] is False and "self-signature" in r["error"], r

    async def test_naive_datetimes_no_longer_500_the_chain(self, db_session, svc, make_user):
        """A320 同族：SQLite naive DateTime 列与 aware now 直比会 TypeError→500。"""
        from app.models.certificate import CertType
        _, uid_str = await make_user(prefix="a106t")
        uid = uuid.UUID(uid_str)
        root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
        child = await _issue(db_session, svc, uid, CertType.IDENTITY.value,
                             "CN=tz,O=CDS,C=CN", parent_cert_id=root.id)
        child.not_after = dt.datetime.utcnow() - dt.timedelta(days=1)  # naive（SQLite 回读形态）
        await db_session.flush()
        r = await svc.verify_chain(db_session, child.id)  # 不许 raise
        assert r["valid"] is False and "expired" in r["error"], r


@needs_gmssl
class TestA106CrlAndRevocation:
    async def test_crl_signed_by_issuer_key_and_verifiable(self, db_session, svc, make_user):
        from app.models.certificate import CertType
        from app.services.crypto_service import crypto_service
        _, uid_str = await make_user(prefix="a106s")
        uid = uuid.UUID(uid_str)
        root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
        leaf = await _issue(db_session, svc, uid, CertType.IDENTITY.value, "CN=leaf,O=CDS,C=CN",
                            parent_cert_id=root.id)
        await svc.revoke_certificate(db_session, leaf.id, reason="superseded")

        crl = await svc.generate_crl(db_session)
        assert crl["signed"] is True and crl["signed_by_serial"] == root.serial_number, crl
        signable = {k: v for k, v in crl.items()
                    if k not in ("signed", "signature", "signed_by_serial", "signature_reason")}
        assert crypto_service.verify(
            json.dumps(signable, sort_keys=True).encode(), crl["signature"], root.public_key
        ) is True

    async def test_crl_without_issuer_key_is_honestly_unsigned(self, db_session, svc, make_user):
        """一次性密钥签名被禁：密钥库里没有签发私钥 → 显式 unsigned + reason，绝不伪造。"""
        from app.services.certificate_service import CertificateService
        crl = await CertificateService().generate_crl(db_session)
        assert crl["signed"] is False
        assert crl["signature"] is None
        assert "unsigned" in crl["signature_reason"]

    async def test_cascade_failure_audited_as_incomplete_never_complete(
        self, db_session, svc, make_user, monkeypatch
    ):
        """撤销级联：会话终止失败过去被吞仍记 complete——审计谎报完成。"""
        from app.models.audit_log import AuditLog
        from app.models.certificate import CertType
        from app.models.kms import DataEncryptionKey, DEKStatus
        import app.services.sandbox_runtime as sr_mod

        _, uid_str = await make_user(prefix="a106f")
        uid = uuid.UUID(uid_str)
        ca = await _issue(db_session, svc, uid, CertType.PLATFORM.value, "CN=CDS Fail CA,O=CDS,C=CN")
        dek = DataEncryptionKey(key_id=f"k-{uuid.uuid4().hex[:8]}", encrypted_key="x",
                                encryption_cert_id=ca.id, status=DEKStatus.ACTIVE.value)
        db_session.add(dek)
        await db_session.flush()

        monkeypatch.setattr(sr_mod.sandbox_runtime, "terminate_sessions_by_key",
                            AsyncMock(side_effect=RuntimeError("runtime down")))
        await svc.revoke_certificate(db_session, ca.id, reason="key_compromise")

        rows = (await db_session.execute(
            select(AuditLog).where(AuditLog.action.like("certificate.cascade%")))).scalars().all()
        actions = {r.action for r in rows}
        assert "certificate.cascade.incomplete" in actions, actions
        assert "certificate.cascade.complete" not in actions, actions
        done = [r for r in rows if r.action == "certificate.cascade.incomplete"][0]
        detail = done.detail if isinstance(done.detail, dict) else json.loads(done.detail)
        assert "runtime down" in json.dumps(detail) and detail["cascade_complete"] is False

    async def test_suspend_sessions_by_cert_returns_real_count(self, db_session, svc, make_user, monkeypatch):
        """恒返 1 的旧语义（「证书存在」冒充「会话受影响数」）改为真实处置计数。"""
        from app.models.certificate import CertType
        from app.models.kms import DataEncryptionKey, DEKStatus
        import app.services.sandbox_runtime as sr_mod

        _, uid_str = await make_user(prefix="a106n")
        uid = uuid.UUID(uid_str)
        ca = await _issue(db_session, svc, uid, CertType.PLATFORM.value, "CN=CDS Count CA,O=CDS,C=CN")
        db_session.add(DataEncryptionKey(key_id=f"k-{uuid.uuid4().hex[:8]}", encrypted_key="x",
                                         encryption_cert_id=ca.id, status=DEKStatus.ACTIVE.value))
        await db_session.flush()

        monkeypatch.setattr(sr_mod.sandbox_runtime, "terminate_sessions_by_key",
                            AsyncMock(return_value=7))
        assert await svc.suspend_sessions_by_cert(db_session, ca.id) == 7
        assert await svc.suspend_sessions_by_cert(db_session, uuid.uuid4()) == 0


@needs_gmssl
class TestA106SingleCertDisclosures:
    async def test_self_consistency_is_not_trust(self, db_session, svc, make_user):
        """verify_certificate 只证明自洽：trusted 恒 False；「签名自洽却谎称他人签发」
        （默认无父签发的形态）必须点名 selfsigned_despite_issuer_claim。"""
        from app.models.certificate import CertType
        _, uid_str = await make_user(prefix="a106d")
        uid = uuid.UUID(uid_str)
        root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
        child = await _issue(db_session, svc, uid, CertType.IDENTITY.value,
                             "CN=alice,O=CDS,C=CN", parent_cert_id=root.id)
        mis = await _issue(db_session, svc, uid, CertType.IDENTITY.value, "CN=mis,O=CDS,C=CN")

        pr = svc.verify_certificate(root.certificate_pem)
        assert pr["self_signed"] is True and pr["signature_valid"] is True and pr["trusted"] is False
        assert "selfsigned_despite_issuer_claim" not in pr

        pc = svc.verify_certificate(child.certificate_pem)  # 父签 → 自洽验签 False（本就是父的签）
        assert pc["signature_valid"] is False and pc["self_signed"] is False and pc["trusted"] is False

        pm = svc.verify_certificate(mis.certificate_pem)  # 自签却声称 root 签发
        assert pm["signature_valid"] is True and pm["trusted"] is False
        assert pm["selfsigned_despite_issuer_claim"] is True


@needs_gmssl
async def test_verify_chain_route_disclosure_tracks_reality(client, db_session, admin_headers, svc, make_user):
    """路由披露从结论推导：真验了签才许报 signature_verified=true（只强不弱；
    A101 那批「未知 id 必须报 none/false」的针原样成立，这里钉正路径）。"""
    from app.models.certificate import CertType
    _, uid_str = await make_user(prefix="a106r")
    uid = uuid.UUID(uid_str)
    root = await _issue(db_session, svc, uid, CertType.PLATFORM.value, ROOT_DN)
    child = await _issue(db_session, svc, uid, CertType.IDENTITY.value,
                         "CN=route,O=CDS,C=CN", parent_cert_id=root.id)

    resp = await client.get(f"/api/v1/certificates/{child.id}/verify-chain", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["assurance"]["signature_verified"] is True
    assert body["assurance"]["level"] == "cryptographic"
    assert "self-signed-root" in body["assurance"]["trust_anchor"]
