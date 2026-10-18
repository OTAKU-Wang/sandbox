"""A111 — 单元格真实输出必须过输出 DLP（Notebook 不得成为输出策略的旁路）。

会话级 DLP 在 execute_in_sandbox 里跑，那时单元格输出还封在 ``CDS_CELL_B64::``
base64 信封里：DLP 检的是密文，明文随后被 run_cell 解出来直落 notebook 文档并
回给前端。这里钉住修复后的契约：

1. DLP 必须看到解码后的明文（stdout / stderr / repr）；
2. DLP 命中阻断级策略 ⇒ 整个单元格输出被撤下并如实标记 output_blocked，
   且端到端地落不到 notebook 文档里；
3. 未阻断的输出按平台既有口径放行（DLP 脱敏 + 零宽水印），可见文本不变。
"""
import base64
import json

import pytest

from app.services import notebook_service as nb

KERNEL_MARKER = "CDS_CELL_B64::"

ID_CARD = "110105199001011234"
PHONE = "13800138000"
# 零宽水印字符（app/utils/watermark.py 的 _ZWC + _SENTINEL）
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u2060"


def _kernel_output(stdout="", stderr="", repr_="", **extra) -> str:
    payload = {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": stderr, "repr": repr_,
               "cell_id": "c1", "backend": "cds_kernel", "stateful": True, "resident": True}
    payload.update(extra)
    return KERNEL_MARKER + base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode()


def _executor(output: str, **raw_extra):
    async def _run(script: str) -> dict:
        return {"output": output, "exit_code": 0, "stderr": "", **raw_extra}
    return _run


def _visible(text: str) -> str:
    for ch in ZERO_WIDTH:
        text = text.replace(ch, "")
    return text


def _doc_text(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False)


async def test_id_card_in_cell_stdout_is_blocked_end_to_end():
    """端到端：单元格里打印身份证号 ⇒ 输出被拦，且一个字都不进 notebook 文档。"""
    executor = _executor(_kernel_output(stdout=f"客户身份证 {ID_CARD}\n"))

    result = await nb.run_cell(executor, "print(customer_id)", cell_id="c1")

    assert result["output_blocked"] is True
    assert result["blocked_reason"] == "output_inspection_blocked"
    assert result["stdout"] == ""
    assert ID_CARD not in json.dumps(result, ensure_ascii=False)

    doc = nb.apply_cell_result(nb.make_notebook("泄漏测试"), 0, result, source="print(customer_id)")
    assert ID_CARD not in _doc_text(doc)
    assert doc["cells"][0]["outputs"] == []


async def test_secret_in_repr_is_blocked():
    """execute_result（repr）也是输出：命中同样整格撤下。"""
    executor = _executor(_kernel_output(repr_=f"'{ID_CARD}'"))

    result = await nb.run_cell(executor, "customer_id_card", cell_id="c1")

    assert result["output_blocked"] is True
    assert result["repr"] == "" and result["stdout"] == ""


async def test_secret_in_stderr_is_blocked():
    executor = _executor(_kernel_output(stderr=f"ValueError: bad id {ID_CARD}"))

    result = await nb.run_cell(executor, "boom()", cell_id="c1")

    assert result["output_blocked"] is True
    assert result["stderr"] == ""


async def test_dlp_sees_the_decoded_plaintext_not_the_base64_envelope(monkeypatch):
    """修复前 DLP 只看得到信封密文：钉住「检的是明文」这件事本身。"""
    import app.services.output_security as output_security

    seen: list[str] = []
    real = output_security.inspect_text_output

    def spy(output, **kwargs):
        seen.append(output)
        return real(output, **kwargs)

    monkeypatch.setattr(output_security, "inspect_text_output", spy)
    executor = _executor(_kernel_output(stdout=f"联系 {PHONE}\n"))

    result = await nb.run_cell(executor, "print(contact)", cell_id="c1")

    assert f"联系 {PHONE}\n" in seen, "DLP never saw the decoded cell output"
    assert PHONE not in result["stdout"], "high-severity pattern must be redacted"
    assert "[REDACTED:" in result["stdout"]
    assert not result.get("output_blocked"), "high severity is redacted, not blocked"


async def test_benign_output_is_released_verbatim():
    """未命中：可见文本一字不改（只允许零宽水印/签名这类不可见附着）。"""
    executor = _executor(_kernel_output(stdout="hello 世界\n", repr_="'ok'"))

    result = await nb.run_cell(executor, "print('hello 世界')", cell_id="c1")

    assert not result.get("output_blocked")
    assert _visible(result["stdout"]) == "hello 世界\n"
    assert _visible(result["repr"]) == "'ok'"

    doc = nb.apply_cell_result(nb.make_notebook("正常"), 0, result, source="print()")
    assert _visible("".join(doc["cells"][0]["outputs"][0]["text"])) == "hello 世界\n"


async def test_outer_dlp_block_clears_the_decoded_output_too():
    """会话级已判拦截时，解码出的明文也必须一起撤下（旧代码只打个标记）。"""
    executor = _executor(
        _kernel_output(stdout=f"身份证 {ID_CARD}"),
        output_blocked=True,
        blocked_reason="output_inspection_blocked",
    )

    result = await nb.run_cell(executor, "print(customer_id)", cell_id="c1")

    assert result["output_blocked"] is True
    assert result["stdout"] == ""
    assert ID_CARD not in json.dumps(result, ensure_ascii=False)


async def test_kernel_error_is_still_reported_when_output_is_blocked():
    """拦截只撤下输出，不得顺手把后端的降级/错误披露也抹掉。"""
    executor = _executor(_kernel_output(stdout=f"{ID_CARD}\n", kernel_error="kernel exec failed: x"))

    result = await nb.run_cell(executor, "print(x)", cell_id="c1")

    assert result["output_blocked"] is True
    assert result["kernel_error"] == "kernel exec failed: x"
    assert result["backend"] == "cds_kernel"


async def test_stateless_path_passes_through_unchanged(monkeypatch):
    """无信封的降级路径：输出在会话级已经检过，run_stateless 原样如实回传。"""
    calls: list[str] = []
    import app.services.output_security as output_security

    real = output_security.inspect_text_output

    def spy(output, **kwargs):
        calls.append(output)
        return real(output, **kwargs)

    monkeypatch.setattr(output_security, "inspect_text_output", spy)
    executor = _executor("plain output without envelope\n")

    result = await nb.run_stateless(executor, "print(1)")

    assert calls == []
    assert result["stdout"] == "plain output without envelope\n"
    assert not result.get("output_blocked")
