"""OCR A135 — `_process_xml` 用 `ET.parse` 直解用户可控文件，无实体膨胀防护。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A135）：`path` 来自
待处理文件（上传/沙箱产出），`process` 只校存在性、不校内容与大小。expat 默认不取
外部实体，所以这不是 XXE 外带面，但 billion-laughs / quadratic blowup 这类内部实体
膨胀 DoS 成立：几 KB 的文件就能把 CPU/内存打满。修法是不引新依赖，在解析前用 expat
的 DTD/ENTITY 钩子拒绝，并对输入字节数设上限。
"""
import pytest

import app.services.data_processing as dp
from app.services.data_processing import SemiStructuredDataProcessor

BOMB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
 <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
]>
<lolz>&lol6;</lolz>
"""

BENIGN = """<?xml version="1.0" encoding="UTF-8"?>
<records>
  <record><id>1</id><name>alpha</name></record>
  <record><id>2</id><name>beta</name></record>
</records>
"""


def _run(tmp_path, text: str, name: str = "in.xml"):
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    return SemiStructuredDataProcessor().process(str(src), str(tmp_path / "out"))


def test_a135_entity_expansion_bomb_is_rejected(tmp_path):
    """内部实体声明必须在解析前被拒，而不是展开后才失败。"""
    res = _run(tmp_path, BOMB)
    assert res.success is False
    assert res.records_processed == 0
    assert "entity" in (res.error or "").lower() or "dtd" in (res.error or "").lower()


def test_a135_quadratic_blowup_is_rejected(tmp_path):
    quadratic = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "AAAAAAAAAA">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><r>&b;&b;&b;&b;</r>'
    )
    assert _run(tmp_path, quadratic).success is False


def test_a135_plain_doctype_without_entity_is_rejected(tmp_path):
    external = '<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://127.0.0.1:1/x.dtd"><r/>'
    assert _run(tmp_path, external).success is False


def test_a135_oversized_input_is_rejected(tmp_path, monkeypatch):
    """`process` 不再只看存在性：超过上限的输入直接拒处理，不喂给解析器。"""
    monkeypatch.setattr(dp, "MAX_XML_INPUT_BYTES", 2048, raising=False)
    payload = "<records>" + ("<record><v>x</v></record>" * 500) + "</records>"
    res = _run(tmp_path, payload)
    assert res.success is False
    assert "too large" in (res.error or "").lower()


def test_a135_benign_xml_still_processes(tmp_path):
    """防护不得把正常 XML 一起拒掉（否则是产品回归）。"""
    res = _run(tmp_path, BENIGN)
    assert res.success is True, res.error
    assert res.records_processed == 2
    assert (res.metadata or {})["root_tag"] == "records"


def test_a135_malformed_xml_reports_parse_error(tmp_path):
    res = _run(tmp_path, "<records><unclosed>")
    assert res.success is False
    assert "parse error" in (res.error or "").lower()
