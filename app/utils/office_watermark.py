"""OOXML 容器水印（2026-10-30 拍板：CSV/Excel/Word 走「背景水印」，像 web 页面背景水印那样）。

诚实边界（项目铁律 honest degradation，与 docs/mock-remediation-plan.md 同口径）：

- ``.docx``：OOXML 有真背景水印能力——往分节引用的页眉部件注入 VML ``v:shape``
  （WordArt 文本形状 ``#_x0000_t136``，斜向 −45°、半透明、页面居中、文字下层）。
  没有页眉关系就新建部件 / 关系 / 内容类型 / ``w:sectPr`` 引用，四者齐备才算注入。
  水印口径 ``embedded_watermark``。
- ``.xlsx``：OOXML 电子表格**没有**页面背景水印概念——诚实降级为「打印页眉文本水印
  （``headerFooter/oddHeader`` 居中段）+ ``docProps/custom.xml`` 自定义属性」，
  清单口径永远是 ``header_text``，绝不标 ``embedded_watermark``。
- ``.csv`` / ``.tsv`` 等纯文本数据表：文件没有「背景」概念，改一个字节都可能破坏下游
  解析——文件字节不动，水印只存在于平台在线预览层（前端叠层），口径 ``preview_only``。

只用 stdlib（zipfile + 字符串级 XML 手术）：不引入 python-docx/openpyxl，也不做整树
DOM 重序列化——OOXML 命名空间前缀与 ``standalone="yes"`` XML 声明必须原样保留，
ElementTree 往返重写会丢声明并重排前缀，反而让 Word/Excel 判定文档损坏。
"""
import io
import posixpath
import re
import zipfile
from xml.sax.saxutils import escape

__all__ = [
    "WATERMARK_METHOD_DOCX",
    "WATERMARK_METHOD_XLSX_HEADER",
    "WATERMARK_METHOD_PREVIEW_ONLY",
    "WATERMARK_METHOD_TEXT_STEGO",
    "embed_docx_background_watermark",
    "embed_xlsx_header_watermark",
]

# 水印口径常量：MANIFEST.files[].watermark_method 与打包响应的唯一词表。
# header_text / preview_only 是「非真背景水印」的两个诚实档位，绝不允许互相冒用，
# 更不允许冒充 embedded_watermark。
WATERMARK_METHOD_DOCX = "embedded_watermark"
WATERMARK_METHOD_XLSX_HEADER = "header_text"
WATERMARK_METHOD_PREVIEW_ONLY = "preview_only"
WATERMARK_METHOD_TEXT_STEGO = "zero_width_text"

_HEADER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
_CUSTOM_PROPS_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL_HEADER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
_REL_CUSTOM_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_CDS_HEADER_PART = "word/headerCdsWm.xml"

# Word 自家文本水印用的 VML 文本艺术字 shapetype（#_x0000_t136）boilerplate：缺了它，
# v:shape 的 type="#_x0000_t136" 引用在部件内不可解析。
_VML_SHAPETYPE_T136 = (
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" path="m@7,l@8,m@5,21600l@6,21600e">'
    "<v:formulas>"
    '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
    '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
    '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
    '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/>'
    '<v:f eqn="sum @6 0 @5"/>'
    "</v:formulas>"
    '<v:path textpathok="t" o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
    '<v:textpath on="t" fitshape="t"/>'
    '<v:handles><v:h position="#0,bottomRight" xrange="6629,10800"/></v:handles>'
    '<o:lock v:ext="edit" text="t" shapetype="t"/>'
    "</v:shapetype>"
)

# 形状样式：页面正中（mso-position-*-relative:page）+ 斜向 −45°（VML rotation 单位
# 1/65536 度，−45×65536=−2949120）+ 文字下层（负 z-index）。半透明走 v:fill
# opacity=".45" + 银灰 fillcolor，与 Word 自家水印观感一致。
_DOCX_WM_STYLE = (
    "position:absolute;left:0;text-align:left;margin-left:0;margin-top:0;"
    "width:460pt;height:130pt;z-index:-251658240;mso-wrap-style:square;"
    "mso-position-horizontal:center;mso-position-horizontal-relative:page;"
    "mso-position-vertical:center;mso-position-vertical-relative:page;"
    "rotation:-2949120f"
)


def _attr_text(text: str) -> str:
    """XML 属性/文本值转义（水印文本会进 v:textpath string="…" 与 vt:lpwstr）。"""
    return escape(str(text), {'"': "&quot;"})


def _wm_para(text: str) -> str:
    """承载背景水印的页眉段落（VML WordArt：斜向、半透明、页面居中、文字下层）。"""
    return (
        "<w:p><w:r><w:rPr><w:noProof/></w:rPr><w:pict>"
        + _VML_SHAPETYPE_T136
        + '<v:shape id="CdsDeliveryWatermark" o:spid="_x0000_s2026" type="#_x0000_t136" '
        + f'style="{_DOCX_WM_STYLE}" fillcolor="#c0c0c0" stroked="f" alt="CDS 成果包背景水印">'
        + '<v:fill on="t" opacity=".45"/>'
        + f'<v:textpath style="font-family:&quot;SimHei&quot;;font-size:1pt" trim="t" fitshape="t" string="{_attr_text(text)}"/>'
        + "</v:shape>"
        + "</w:pict></w:r></w:p>"
    )


def _read_zip(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if zf.testzip() is not None:
                raise ValueError("zip 条目 CRC 校验失败")
            return {info.filename: zf.read(info.filename) for info in zf.infolist() if not info.is_dir()}
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是合法 zip/OOXML 容器: {exc}") from exc


def _write_zip(parts: dict[str, bytes], order: list[str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            if name in parts:
                zf.writestr(name, parts[name])
        for name in parts:
            if name not in order:
                zf.writestr(name, parts[name])
    return out.getvalue()


def _ensure_content_type(parts: dict[str, bytes], part_name: str, content_type: str) -> None:
    """[Content_Types].xml 缺该部件的 Override 时补一条（已有同 PartName 不动）。"""
    raw = parts.get("[Content_Types].xml")
    if raw is None:
        raise ValueError("OOXML 容器缺少 [Content_Types].xml")
    ct = raw.decode("utf-8")
    needle = f'PartName="/{part_name}"'
    if needle in ct:
        return
    override = f'<Override PartName="/{part_name}" ContentType="{content_type}"/>'
    if "</Types>" not in ct:
        raise ValueError("[Content_Types].xml 缺少 </Types> 结束标签")
    parts["[Content_Types].xml"] = ct.replace("</Types>", override + "</Types>").encode("utf-8")


def _rels_map(rels_xml: str) -> dict[str, dict[str, str]]:
    """Relationships XML → {Id: {Type, Target}}（属性顺序不定的字符串级解析）。"""
    out: dict[str, dict[str, str]] = {}
    for tag in re.findall(r"<Relationship\b[^>]*/?>", rels_xml):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', tag))
        if attrs.get("Id"):
            out[attrs["Id"]] = attrs
    return out


def _next_rel_id(existing_ids: set[str]) -> str:
    used = {int(m.group(1)) for rid in existing_ids if (m := re.fullmatch(r"rId(\d+)", rid))}
    n = 1
    while n in used:
        n += 1
    return f"rId{n}"


def _load_or_init_rels(parts: dict[str, bytes], rels_path: str) -> str:
    raw = parts.get(rels_path)
    if raw is None:
        # 显式闭合而非自闭合：后续统一用 </Relationships> 锚点追加条目。
        return f'{_XML_DECL}<Relationships xmlns="{_REL_NS}"></Relationships>'
    xml = raw.decode("utf-8")
    if "</Relationships>" not in xml:
        raise ValueError(f"{rels_path} 缺少 </Relationships> 结束标签")
    return xml


def _add_relationship(parts: dict[str, bytes], rels_path: str, *, rel_type: str, target: str) -> str:
    """向 .rels 追加一条关系并返回新 Id；文件不存在则新建 Relationships 根。"""
    xml = _load_or_init_rels(parts, rels_path)
    new_id = _next_rel_id(set(_rels_map(xml)))
    _append_rel(parts, rels_path, xml, rel_id=new_id, rel_type=rel_type, target=target)
    return new_id


def _upsert_relationship(parts: dict[str, bytes], rels_path: str, *, rel_id: str, rel_type: str, target: str) -> None:
    """按指定 Id 挂/改关系（既有页眉引用悬空时把引用重挂到新建水印部件上）。"""
    xml = _load_or_init_rels(parts, rels_path)
    xml = re.sub(rf'<Relationship\b[^>]*\bId="{re.escape(rel_id)}"[^>]*/?>', "", xml)
    _append_rel(parts, rels_path, xml, rel_id=rel_id, rel_type=rel_type, target=target)


def _append_rel(parts: dict[str, bytes], rels_path: str, xml: str, *, rel_id: str, rel_type: str, target: str) -> None:
    entry = f'<Relationship Id="{rel_id}" Type="{rel_type}" Target="{_attr_text(target)}"/>'
    parts[rels_path] = xml.replace("</Relationships>", entry + "</Relationships>").encode("utf-8")


# w:headerReference 在 sectPr 子元素序列里排最前，紧跟开始标签插入即合法位。
_SECT_PR_RE = re.compile(r"<w:sectPr\b[^>]*?(?:/>|>)")
_DEFAULT_HEADER_REF_RE = re.compile(
    r'<w:headerReference\b(?:(?![^>]*w:type=)|[^>]*w:type="default")[^>]*/?>'
)


def _inject_into_header(parts: dict[str, bytes], header_part: str, wm_para: str) -> None:
    """水印段落注入既有页眉部件；根标签缺 v/o 命名空间时补齐（VML 前缀未声明整段不可解析）。"""
    hdr = parts[header_part].decode("utf-8")
    m_root = re.search(r"<w:hdr\b[^>]*?/?>", hdr)
    if m_root is None:
        raise ValueError(f"{header_part} 缺少 <w:hdr> 根元素")
    root_tag = m_root.group(0)
    new_root = root_tag[:-2] + ">" if root_tag.endswith("/>") else root_tag  # 自闭合空页眉先展开
    for prefix, uri in (("v", "urn:schemas-microsoft-com:vml"), ("o", "urn:schemas-microsoft-com:office:office")):
        if f"xmlns:{prefix}=" not in new_root:
            new_root = new_root[:-1] + f' xmlns:{prefix}="{uri}">'
    hdr = hdr.replace(root_tag, new_root, 1)
    inject_at = hdr.index(new_root) + len(new_root)
    parts[header_part] = (hdr[:inject_at] + wm_para + hdr[inject_at:]).encode("utf-8")


def embed_docx_background_watermark(data: bytes, text: str) -> bytes:
    """往 docx 注入真·背景水印（页眉 VML WordArt），返回重写后的完整 docx 字节。

    - 已有 default 页眉引用且部件在包内 → 水印段落注入**既有页眉部件**（一个节只允许
      一个 default 页眉，绝不写第二个 default 引用）；
    - 页眉引用悬空（关系缺失/部件丢失）→ 新建部件并把原引用重挂过去；
    - 没有页眉关系 → 新建 ``word/headerCdsWm.xml`` + document.xml.rels 关系 +
      [Content_Types].xml Override + ``w:sectPr`` 的 ``w:headerReference``，四者齐备；
    - 连 ``w:sectPr`` 都没有 → 在 ``</w:body>`` 前补一个带引用的 sectPr。

    任何不满足前提的输入（非 zip / 缺 word/document.xml / XML 结构不认识）都抛
    ``ValueError``——调用方必须按原文交付并把失败原因如实写进清单，不得静默吞掉。
    """
    parts = _read_zip(data)
    order = list(parts)
    doc_raw = parts.get("word/document.xml")
    if doc_raw is None:
        raise ValueError("不是合法 docx：缺少 word/document.xml")
    doc = doc_raw.decode("utf-8")
    doc_rels_path = "word/_rels/document.xml.rels"
    wm_para = _wm_para(text)

    existing_default = _DEFAULT_HEADER_REF_RE.search(doc)
    if existing_default:
        rid_m = re.search(r'\b\w+:id="([^"]+)"', existing_default.group(0))
        rid = rid_m.group(1) if rid_m else None
        rels_raw = parts.get(doc_rels_path)
        rel = _rels_map(rels_raw.decode("utf-8")).get(rid or "") if rels_raw is not None else None
        target = (rel or {}).get("Target") or ""
        if target and "header" in (rel or {}).get("Type", "").lower():
            candidate = posixpath.normpath(posixpath.join("word", target))
            if candidate in parts:
                _inject_into_header(parts, candidate, wm_para)
                return _write_zip(parts, order)
        if rid:
            # 引用存在但目标部件不可用：把这条引用重挂到新建的水印部件上，
            # 保持「一个节一个 default 页眉」的 schema 约束。
            _new_cds_header_part(parts, wm_para)
            _upsert_relationship(parts, doc_rels_path, rel_id=rid, rel_type=_REL_HEADER, target="headerCdsWm.xml")
            return _write_zip(parts, order)
        raise ValueError("docx 已有 w:headerReference 但缺 r:id，注入位置不可判定")

    _new_cds_header_part(parts, wm_para)
    rid = _add_relationship(parts, doc_rels_path, rel_type=_REL_HEADER, target="headerCdsWm.xml")

    # sectPr：取**最后一个**——正文级节属性在 body 末尾，段落级 sectPr 只会出现在它之前。
    ref = f'<w:headerReference w:type="default" r:id="{rid}"/>'
    matches = list(_SECT_PR_RE.finditer(doc))
    if matches:
        m = matches[-1]
        head = m.group(0)
        if head.endswith("/>"):
            doc = doc[: m.start()] + head[:-2] + ">" + ref + "</w:sectPr>" + doc[m.end():]
        else:
            doc = doc[: m.end()] + ref + doc[m.end():]
    else:
        if "</w:body>" not in doc:
            raise ValueError("word/document.xml 缺少 </w:body>")
        doc = doc.replace("</w:body>", f"<w:sectPr>{ref}</w:sectPr></w:body>", 1)

    # r:id 的命名空间依赖 document 根上的 xmlns:r 声明，缺失则补。
    m_root = re.search(r"<w:document\b[^>]*>", doc)
    if m_root is None:
        raise ValueError("word/document.xml 缺少 <w:document> 根元素")
    root_tag = m_root.group(0)
    if "xmlns:r=" not in root_tag:
        patched = root_tag[:-1] + ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        doc = doc.replace(root_tag, patched, 1)
    parts["word/document.xml"] = doc.encode("utf-8")
    return _write_zip(parts, order)


def _new_cds_header_part(parts: dict[str, bytes], wm_para: str) -> None:
    header_ns = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word"'
    )
    parts[_CDS_HEADER_PART] = f"{_XML_DECL}<w:hdr {header_ns}>{wm_para}</w:hdr>".encode("utf-8")
    _ensure_content_type(parts, _CDS_HEADER_PART, _HEADER_CONTENT_TYPE)


# CT_Worksheet 元素序列里排在 headerFooter **之后**的成员：页眉要插在这些之前才符合
# schema 顺序；一个都没有时贴 </worksheet> 前。
_AFTER_HEADERFOOTER = (
    "<rowBreaks", "<colBreaks", "<customProperties", "<cellWatches", "<ignoredErrors",
    "<smartTags", "<drawing", "<legacyDrawing", "<legacyDrawingHF", "<picture",
    "<oleObjects", "<controls", "<webPublishItems", "<tableParts", "<extLst",
)


def _sheet_with_header(xml: str, header_text: str) -> str:
    """工作表 XML 写 oddHeader 居中段（``&C`` 是 Excel 页眉语法，XML 里转义为 &amp;C）。"""
    value = "&amp;C" + _attr_text(header_text) + "&amp;C"
    if "<headerFooter" in xml:
        m_self = re.search(r"<headerFooter\b[^>]*?/>", xml)
        if m_self:  # 自闭合 → 展开后写入
            return xml[: m_self.start()] + m_self.group(0)[:-2] + f"><oddHeader>{value}</oddHeader></headerFooter>" + xml[m_self.end():]
        m_odd = re.search(r"<oddHeader\b[^>]*?(?:/>|>.*?</oddHeader>)", xml, re.DOTALL)
        if m_odd:  # 已有 oddHeader → 整段替换；覆盖旧页眉文本是打包时点行为，清单如实披露
            return xml[: m_odd.start()] + f"<oddHeader>{value}</oddHeader>" + xml[m_odd.end():]
        if "</headerFooter>" not in xml:
            raise ValueError("工作表 headerFooter 缺少结束标签")
        return xml.replace("</headerFooter>", f"<oddHeader>{value}</oddHeader></headerFooter>", 1)
    element = f"<headerFooter><oddHeader>{value}</oddHeader></headerFooter>"
    cuts = [pos for token in _AFTER_HEADERFOOTER if (pos := xml.find(token)) != -1]
    insert_at = min(cuts) if cuts else xml.rfind("</worksheet>")
    if insert_at == -1:
        raise ValueError("工作表 XML 缺少 </worksheet>")
    return xml[:insert_at] + element + xml[insert_at:]


def embed_xlsx_header_watermark(data: bytes, text: str) -> bytes:
    """xlsx 诚实降级水印：打印页眉文本（oddHeader）+ docProps 自定义属性。

    OOXML 电子表格没有 Word 那种页面背景水印部件，所以本函数的产出口径永远是
    ``header_text``：页眉只在打印/分页预览可见，custom 属性只在文件属性里可查——
    **绝不宣称 embedded_watermark**。前提不满足（非 zip / 缺 xl/workbook.xml /
    没有工作表部件）抛 ValueError，由调用方按原文交付并如实记录原因。
    """
    parts = _read_zip(data)
    order = list(parts)
    if "xl/workbook.xml" not in parts:
        raise ValueError("不是合法 xlsx：缺少 xl/workbook.xml")
    sheets = sorted(n for n in parts if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not sheets:
        raise ValueError("xlsx 里没有找到 xl/worksheets/sheetN.xml，页眉水印无处可写")
    for name in sheets:
        parts[name] = _sheet_with_header(parts[name].decode("utf-8"), text).encode("utf-8")

    # docProps/custom.xml：自定义属性 CDS-Watermark（文件属性里可查的归属痕迹）。
    custom = "docProps/custom.xml"

    def _prop(pid: int) -> str:
        return (
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" name="CDS-Watermark">'
            f"<vt:lpwstr>{_attr_text(text)}</vt:lpwstr></property>"
        )

    if custom in parts:
        xml = parts[custom].decode("utf-8")
        pids = [int(m.group(1)) for m in re.finditer(r'\bpid="(\d+)"', xml)]
        pid = max(pids or [1]) + 1
        xml = re.sub(r'<property\b[^>]*name="CDS-Watermark".*?</property>', "", xml, flags=re.DOTALL)
        if "</Properties>" not in xml:
            raise ValueError("docProps/custom.xml 缺少 </Properties>")
        parts[custom] = xml.replace("</Properties>", _prop(pid) + "</Properties>", 1).encode("utf-8")
    else:
        parts[custom] = (
            _XML_DECL
            + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
            + 'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            + _prop(2)
            + "</Properties>"
        ).encode("utf-8")
    _ensure_content_type(parts, custom, _CUSTOM_PROPS_CONTENT_TYPE)

    raw = parts.get("_rels/.rels")
    if raw is None or _REL_CUSTOM_PROPS not in raw.decode("utf-8"):
        _add_relationship(parts, "_rels/.rels", rel_type=_REL_CUSTOM_PROPS, target="docProps/custom.xml")
    return _write_zip(parts, order)
