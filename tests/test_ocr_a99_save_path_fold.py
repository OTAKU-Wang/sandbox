"""A99 — 保存路径判定既不能只认字面量，也不能被 `..` 字面量绕过

`_has_valid_save_path` 旧实现只看 `ast.Constant` 且用裸 `startswith`：
- 可用性半边：`OUT = "/sandbox/output"` 之后 `save(f"{OUT}/m.bin")` / `save(OUT + "m.bin")`
  这类可静态证明落在采集目录内的写法被误判 INVALID_SAVE_PATH（FATAL 在 API 层就是 400）；
- 安全半边：`"/sandbox/output/../../etc/x"` 这种字面量反而通过 startswith 检查。
修法口径：先归一（posixpath.normpath）再判包含；能静态折叠的路径接受，
任何不可静态证明的（变量多写、运行期路径）维持 FATAL——默认拒绝不放宽。
"""
from app.services.code_scanner import CodeScanner, SandboxMode

scanner = CodeScanner()
MODE = SandboxMode.LLM_SFT.value


def _codes(code: str) -> list[str]:
    result = scanner.scan(code, "python", MODE)
    return [] if result.passed else [i.code for i in result.issues]


# ── 安全半边：归一后再判包含 ────────────────────────────────────────

def test_traversal_literal_is_rejected():
    """A99: startswith 无 realpath ⇒ `/sandbox/output/../../etc` 字面量必须 INVALID_SAVE_PATH。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'model.save("/sandbox/output/../../etc/x.bin")'
    )


def test_traversal_via_keyword_is_rejected():
    """A99: 关键字参数同一半区（path/save_directory/output_dir）。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'model.save_pretrained(path="/scratch/../../etc/x.bin")'
    )


def test_traversal_collapses_to_root_is_rejected():
    """归一到 `/` 也是逃逸：不能因为前缀是输出目录就放行。"""
    assert "INVALID_SAVE_PATH" in _codes('model.save("/sandbox/output/../..")')
    assert "INVALID_SAVE_PATH" in _codes(
        'model.save(output_dir="/sandbox/output/sub/../..")'
    )


def test_normalize_then_contain():
    """A99: 归一后仍在输出目录内 → 放行；归一后逃出 → 拒。"""
    assert _codes('model.save("/sandbox/output/../output/ok.bin")') == []
    assert "INVALID_SAVE_PATH" in _codes(
        'model.save("/sandbox/output/../output/../etc/x.bin")'
    )


def test_existing_contracts_still_hold():
    """回归护栏：SS-18 采集面口径不能被 A99 修法顺手放宽。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'model.save_pretrained("/sandbox/checkpoints/epoch_1/")'
    )
    assert _codes('model.save_pretrained("/sandbox/output/model/")') == []
    assert _codes('model.save_pretrained("/scratch/run1/")') == []
    assert "INVALID_SAVE_PATH" in _codes('model.save_pretrained("/tmp/evil/")')


# ── 可用性半边：可静态证明的派生路径不再误杀 ──────────────────────

def test_variable_derived_literal_path_is_accepted():
    """A99: `OUT = "/sandbox/output"` 之后 f-string/拼接可静态证明，不得误杀。"""
    assert _codes('OUT = "/sandbox/output"\nmodel.save(f"{OUT}/model.bin")') == []
    assert _codes('OUT = "/sandbox/output/"\nmodel.save(OUT + "model.bin")') == []
    assert _codes('model.save("/scratch/" + "run1" + "/model.bin")') == []


def test_keyword_derived_path_is_accepted():
    """关键字参数走同一折叠逻辑（save_directory= 是 HF 常见写法）。"""
    assert _codes(
        'OUT = "/sandbox/output"\nmodel.save_pretrained(save_directory=f"{OUT}/ckpt")'
    ) == []


def test_output_dir_itself_is_accepted():
    """归一后等于采集目录本身（无前导子目录）也是合法产物目录。"""
    assert _codes('model.save("/sandbox/output")') == []


# ── 边界：不可静态证明的一律维持 FATAL（fail-closed 不回退）────────

def test_reassigned_variable_stays_fail_closed():
    """A99 边界：变量多处赋值不可静态证明 ⇒ 维持 FATAL（不放宽默认拒绝）。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'out = "/sandbox/output"\nout = "/etc"\nmodel.save(out)'
    )


def test_unresolvable_variable_stays_fail_closed():
    """A99 边界：运行期才知道的路径仍然 FATAL。"""
    assert "INVALID_SAVE_PATH" in _codes("model.save(user_path)")


def test_variable_bound_outside_is_fail_closed():
    """A99 边界：来源含运行期表达式（函数调用 / 非模块级唯一赋值）不折叠。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'import os\nout = os.environ.get("OUT")\nmodel.save(f"{out}/m.bin")'
    )
    assert "INVALID_SAVE_PATH" in _codes(
        'def run(out):\n    model.save(f"{out}/m.bin")\n'
    )


def test_folded_traversal_via_variable_is_rejected():
    """变量折叠出来的路径同样要先归一：拼接出来的 `..` 也拦。"""
    assert "INVALID_SAVE_PATH" in _codes(
        'OUT = "/sandbox/output"\nmodel.save(OUT + "/../../etc/x.bin")'
    )
