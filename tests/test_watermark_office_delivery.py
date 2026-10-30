"""SS-19 P2-4（2026-10-30 用户拍板）：成果包「背景水印」三档语义——docx 真内嵌 / xlsx 页眉降级 / csv 仅预览层。

判据（honest degradation 铁律）：
1. ``.docx``：走打包端点后进包的字节必须内嵌 OOXML 背景水印——页眉部件里有 VML
   ``v:shape``+``v:textpath``（斜向 rotation、半透明 opacity），文字含成果包编号；
   部件 / 关系 / [Content_Types].xml / sectPr 引用四者齐备；包内 docx 仍是合法 zip、
   XML 可再解析。清单口径 ``embedded_watermark``。
2. ``.xlsx``：OOXML 表格无背景水印能力——**绝不标 embedded_watermark**；清单如实标
   ``header_text``，包内 xlsx 的 oddHeader 与 docProps/custom.xml 都带水印文本。
3. ``.csv``/``.tsv``：文件字节一个不动（在线预览层才有水印，前端叠层）；清单标
   ``preview_only``，包级 watermark_status 也是 ``preview_only``，交付中心列表读新口径。
4. 向后兼容：历史清单字典没有 ``watermark_method`` 键时 ``_watermark_status`` 聚合与
   SS-18 口径逐位一致；历史包行 NULL 仍按「未记录」下发（ss18 已钉，这里补聚合函数侧）。
"""
import io
import json
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET

import pytest

from app.api import deliveries as deliveries_api
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.sandbox_session import SandboxSession
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.utils.office_watermark import embed_docx_background_watermark, embed_xlsx_header_watermark

BUNDLES_URL = "/api/v1/deliveries/dataset/bundles"

CSV_BODY = "user_id,amount\n1,10\n2,20\n"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_DOCX_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{_CT_NS}">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{_REL_NS}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _mk_docx(*, sect_pr: bool = True, with_header: bool = False) -> bytes:
    """手工构造最小合法 docx（不新增依赖）：[Content_Types]+根关系+document.xml（+可选既有页眉）。"""
    sect = ""
    if sect_pr:
        inner = '<w:headerReference w:type="default" r:id="rId5"/>' if with_header else ""
        sect = f"<w:sectPr>{inner}<w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr>"
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}" xmlns:r="{_R_NS}">'
        f"<w:body><w:p><w:r><w:t>密态成果正文</w:t></w:r></w:p>{sect}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", _DOCX_CT)
        zf.writestr("_rels/.rels", _DOCX_ROOT_RELS)
        zf.writestr("word/document.xml", doc)
        if with_header:
            zf.writestr(
                "word/_rels/document.xml.rels",
                f'<?xml version="1.0"?><Relationships xmlns="{_REL_NS}">'
                f'<Relationship Id="rId5" Type="{_R_NS}/header" Target="header1.xml"/></Relationships>',
            )
            zf.writestr("word/header1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:hdr xmlns:w="{_W_NS}"><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>')
    return buf.getvalue()


_XLSX_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{_CT_NS}">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    "</Types>"
)


def _mk_xlsx() -> bytes:
    """手工构造最小合法 xlsx：workbook + 一张含 tableParts 的工作表（页眉必须插在它之前）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", _XLSX_CT)
        zf.writestr("_rels/.rels", _DOCX_ROOT_RELS.replace("word/document.xml", "xl/workbook.xml"))
        zf.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="S" sheetId="1"/></sheets></workbook>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData><row r=\"1\"><c r=\"A1\"><v>1</v></c></row></sheetData>"
            "<pageMargins left=\"0.7\"/><tableParts count=\"1\"/></worksheet>",
        )
    return buf.getvalue()


class TestDocxEmbedding:
    def test_creates_header_part_relation_and_sectpr_reference(self):
        out = embed_docx_background_watermark(_mk_docx(), "B1 | t | user:u")
        zf = zipfile.ZipFile(io.BytesIO(out))
        assert zf.testzip() is None
        assert "word/headerCdsWm.xml" in zf.namelist()
        for name in zf.namelist():
            ET.fromstring(zf.read(name))  # 每个部件仍必须是 well-formed XML
        doc = zf.read("word/document.xml").decode()
        rid = re.search(r'headerReference w:type="default" r:id="([^"]+)"', doc).group(1)
        rels = zf.read("word/_rels/document.xml.rels").decode()
        assert f'Id="{rid}"' in rels and "relationships/header" in rels
        assert 'PartName="/word/headerCdsWm.xml"' in zf.read("[Content_Types].xml").decode()

    def test_shape_is_diagonal_translucent_and_carries_text(self):
        out = embed_docx_background_watermark(_mk_docx(), "BUNDLE-42 | 2026-10-30T13:00:00+08:00 | user:9")
        hdr = zipfile.ZipFile(io.BytesIO(out)).read("word/headerCdsWm.xml").decode()
        assert "<v:shape" in hdr and "<v:textpath" in hdr and "#_x0000_t136" in hdr
        assert "rotation:-2949120f" in hdr  # -45°：斜向（单位 1/65536 度）
        assert 'opacity=".45"' in hdr  # 半透明
        textpath = re.search(r'<v:textpath[^>]*string="([^"]*)"', hdr).group(1)
        assert "BUNDLE-42" in textpath and "user:9" in textpath  # 包号+时间戳+主体

    def test_without_sectpr_one_is_created(self):
        out = embed_docx_background_watermark(_mk_docx(sect_pr=False), "W")
        doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()
        assert "<w:sectPr><w:headerReference" in doc

    def test_existing_default_header_is_augmented_not_duplicated(self):
        out = embed_docx_background_watermark(_mk_docx(with_header=True), "W-9")
        zf = zipfile.ZipFile(io.BytesIO(out))
        assert "word/headerCdsWm.xml" not in zf.namelist()  # 没有页眉关系才新建——有就不造第二份
        hdr = zf.read("word/header1.xml").decode()
        assert "<v:shape" in hdr and "W-9" in hdr and "xmlns:v=" in hdr  # 命名空间补齐
        doc = zf.read("word/document.xml").decode()
        assert doc.count("<w:headerReference") == 1  # 一个节只允许一个 default 页眉

    def test_original_content_survives(self):
        out = embed_docx_background_watermark(_mk_docx(), "W")
        doc = zipfile.ZipFile(io.BytesIO(out)).read("word/document.xml").decode()
        assert "密态成果正文" in doc and "<w:pgSz" in doc

    @pytest.mark.parametrize("bad", [b"not a zip", b"PK\x03\x04truncated"])
    def test_invalid_input_raises_instead_of_silent_pass(self, bad):
        with pytest.raises(ValueError):
            embed_docx_background_watermark(bad, "W")


class TestXlsxHonestDowngrade:
    def test_header_and_custom_prop_written(self):
        out = embed_xlsx_header_watermark(_mk_xlsx(), "B7 | t | user:u")
        zf = zipfile.ZipFile(io.BytesIO(out))
        assert zf.testzip() is None
        for name in zf.namelist():
            ET.fromstring(zf.read(name))
        sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "<oddHeader>&amp;C" in sheet and "B7" in sheet
        assert sheet.index("headerFooter") < sheet.index("tableParts")  # CT_Worksheet 元素序
        custom = zf.read("docProps/custom.xml").decode()
        assert 'name="CDS-Watermark"' in custom and "B7" in custom
        assert "custom-properties+xml" in zf.read("[Content_Types].xml").decode()
        assert "relationships/custom-properties" in zf.read("_rels/.rels").decode()

    def test_reembed_is_idempotent_not_duplicated(self):
        out = embed_xlsx_header_watermark(embed_xlsx_header_watermark(_mk_xlsx(), "A"), "B")
        zf = zipfile.ZipFile(io.BytesIO(out))
        assert zf.read("docProps/custom.xml").decode().count("CDS-Watermark") == 1
        assert zf.read("xl/worksheets/sheet1.xml").decode().count("<oddHeader") == 1

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            embed_xlsx_header_watermark(b"nope", "W")


# ---------- 打包端点全链路（真端点：采集面注入假文件，其余全真） ----------


async def _mk_user(db, role: str, prefix: str):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"{prefix}_{unique}",
        email=f"{prefix}_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _mk_contract(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="背景水印测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["dataset"],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "wmprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "wmbuy")
    return provider, provider_headers, buyer, buyer_headers


async def _make(client, db, monkeypatch, buyer, headers, files, *, watermark=True):
    async def _fake_files(_db, _session):
        return [
            {"name": name, "data": body.encode("utf-8") if isinstance(body, str) else body, "size_bytes": len(body), "encrypted": False}
            for name, body in files
        ]

    monkeypatch.setattr(deliveries_api, "_collect_session_files", _fake_files)
    provider, _ = await _mk_user(db, UserRole.DATA_PROVIDER, "wmprovx")
    contract = await _mk_contract(db, provider, buyer)
    session = SandboxSession(
        user_id=buyer.id,
        data_product_id=uuid.uuid4(),
        contract_id=contract.id,
        sandbox_level="l3",
        sandbox_mode="structured_query",
        status="completed",
        timeout_seconds=3600,
    )
    db.add(session)
    await db.flush()
    resp = await client.post(
        "/api/v1/deliveries/dataset/bundle",
        json={"session_id": str(session.id), "contract_id": str(contract.id), "watermark": watermark},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return contract, resp.json()


def _download_archive_bytes(response, member: str) -> bytes:
    """从下载响应字节里取成果包（zip）内某个成员——解包动作本身即断言 zip 完整。"""
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.testzip() is None  # 成果包本身也必须完整可再打开
        return zf.read(member)


class TestBundleEndpointWatermarkMethods:
    pytestmark = pytest.mark.asyncio

    async def test_docx_gets_real_embedded_background_watermark(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        _contract, body = await _make(client, db_session, monkeypatch, buyer, headers, [("report.docx", _mk_docx())])
        assert body["watermark_status"] == "applied"
        assert body["files"][0]["watermark_method"] == "embedded_watermark"

        dl = await client.get(body["download_url"], headers=headers)
        assert dl.status_code == 200, dl.text
        with zipfile.ZipFile(io.BytesIO(dl.content)) as bundle:
            manifest = json.loads(bundle.read("MANIFEST.json"))
            assert manifest["files"][0]["watermark_method"] == "embedded_watermark"
            assert manifest["watermark_legend"]["embedded_watermark"]
            docx_bytes = bundle.read("files/report.docx")
        inner = zipfile.ZipFile(io.BytesIO(docx_bytes))
        assert inner.testzip() is None  # 解包后可再打开
        for name in inner.namelist():
            ET.fromstring(inner.read(name))
        hdr = inner.read("word/headerCdsWm.xml").decode()
        assert "<v:shape" in hdr and "rotation:-2949120f" in hdr and body["bundle_id"] in hdr

    async def test_xlsx_labeled_header_text_never_embedded(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        _contract, body = await _make(client, db_session, monkeypatch, buyer, headers, [("report.xlsx", _mk_xlsx())])
        assert body["files"][0]["watermark_method"] == "header_text"
        assert body["files"][0]["watermark_method"] != "embedded_watermark"  # 绝不标真内嵌
        assert body["watermark_status"] == "applied"

        dl = await client.get(body["download_url"], headers=headers)
        inner = zipfile.ZipFile(io.BytesIO(_download_archive_bytes(dl, "files/report.xlsx")))
        assert inner.testzip() is None
        sheet = inner.read("xl/worksheets/sheet1.xml").decode()
        assert "<oddHeader>&amp;C" in sheet and body["bundle_id"] in sheet
        assert 'name="CDS-Watermark"' in inner.read("docProps/custom.xml").decode()

    async def test_csv_bytes_untouched_and_preview_only(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        _contract, body = await _make(client, db_session, monkeypatch, buyer, headers, [("result.csv", CSV_BODY)])
        assert body["files"][0]["watermark_method"] == "preview_only"
        assert body["files"][0]["watermark_applied"] is False
        assert body["watermark_status"] == "preview_only"

        dl = await client.get(body["download_url"], headers=headers)
        csv_bytes = _download_archive_bytes(dl, "files/result.csv")
        assert csv_bytes == CSV_BODY.encode("utf-8")  # 文件本体未修改，一字节的零宽都不许加

        listed = await client.get(BUNDLES_URL, headers=headers)
        item = next(i for i in listed.json()["items"] if i["bundle_id"] == body["bundle_id"])
        assert item["watermark_status"] == "preview_only"  # 交付中心列表读新口径

    async def test_mixed_docx_csv_is_partially_applied_not_applied(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        _contract, body = await _make(
            client, db_session, monkeypatch, buyer, headers,
            [("report.docx", _mk_docx()), ("result.csv", CSV_BODY)],
        )
        assert body["watermark_status"] == "partially_applied"  # csv 无字节改写，不许冒称全嵌入
        methods = {f["name"]: f["watermark_method"] for f in body["files"]}
        assert methods == {"report.docx": "embedded_watermark", "result.csv": "preview_only"}

    async def test_corrupt_docx_delivers_original_with_honest_note(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        fake = b"%PDF-1.4 not a docx at all"
        _contract, body = await _make(client, db_session, monkeypatch, buyer, headers, [("report.docx", fake)])
        assert body["files"][0]["watermark_applied"] is False
        assert body["files"][0]["watermark_method"] is None
        assert "注入失败" in body["files"][0]["watermark_note"]
        assert body["watermark_status"] == "not_applied"

        dl = await client.get(body["download_url"], headers=headers)
        assert _download_archive_bytes(dl, "files/report.docx") == fake

    async def test_watermark_not_requested_keeps_not_requested(self, client, db_session, monkeypatch):
        _p, _ph, buyer, headers = await _parties(db_session)
        _contract, body = await _make(client, db_session, monkeypatch, buyer, headers, [("result.csv", CSV_BODY)], watermark=False)
        assert body["watermark_status"] == "not_requested"
        assert body["files"][0]["watermark_method"] is None

    async def test_legacy_row_still_reads_null(self, client, db_session):
        """历史包（0024 之前的行）三列 NULL 仍按「未记录」下发——新口径不得回填推断。"""
        provider, _ph, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        legacy = DeliveryBundle(
            id=uuid.uuid4(), contract_id=contract.id, session_id=None, created_by=buyer.id,
            object_name=f"delivery/{uuid.uuid4()}.zip", size_bytes=10, file_count=1,
        )
        db_session.add(legacy)
        await db_session.flush()
        listed = await client.get(BUNDLES_URL, headers=headers)
        item = next(i for i in listed.json()["items"] if i["bundle_id"] == str(legacy.id))
        assert item["watermark_status"] is None


class TestAggregateBackCompat:
    """_watermark_status：无 watermark_method 键的历史字典与 SS-18 口径逐位一致。"""

    def test_legacy_dicts_match_ss18_exactly(self):
        assert deliveries_api._watermark_status(True, [{"watermark_applied": True}, {"watermark_applied": False}]) == "partially_applied"
        assert deliveries_api._watermark_status(True, [{"watermark_applied": False}]) == "not_applied"
        assert deliveries_api._watermark_status(True, [{"watermark_applied": True}]) == "applied"
        assert deliveries_api._watermark_status(False, [{"watermark_applied": False}]) == "not_requested"

    def test_preview_only_only_when_everything_is_preview_layer(self):
        files = [{"watermark_applied": False, "watermark_method": "preview_only"}] * 2
        assert deliveries_api._watermark_status(True, files) == "preview_only"
        mixed = [{"watermark_applied": False, "watermark_method": "preview_only"}, {"watermark_applied": False}]
        assert deliveries_api._watermark_status(True, mixed) == "not_applied"
        some = [{"watermark_applied": True, "watermark_method": "embedded_watermark"}, {"watermark_applied": False, "watermark_method": "preview_only"}]
        assert deliveries_api._watermark_status(True, some) == "partially_applied"
