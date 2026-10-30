"""A97 — 危险内建判定必须区分属性名与内建名，并封住 io.open / getattr 绕过

可用性半边：`re.compile()` / `torch.compile()` / `df.eval()` 是白名单库的正常 API，
不该因为名字撞内建就被 FATAL（FATAL 在 API 层直接 400）。
安全半边：同名绕过 `io.open(...)`、`from io import open`、`getattr(builtins, 'ev'+'al')`
必须在同一函数里被拦住。
"""
from app.services.code_scanner import CodeScanner

scanner = CodeScanner()


def test_module_re_compile_is_not_a_dangerous_builtin():
    result = scanner.scan(
        "import re\npat = re.compile('a+')\nprint(pat.findall('aa'))",
        "python",
        "structured_query",
    )
    assert result.passed is True, result.issues


def test_torch_compile_is_not_a_dangerous_builtin():
    result = scanner.scan(
        "import torch\nmodel = torch.compile(torch.nn.Linear(2, 2))\nprint(model)",
        "python",
        "structured_query",
    )
    assert result.passed is True, result.issues


def test_dataframe_eval_is_not_a_dangerous_builtin():
    result = scanner.scan(
        "import pandas as pd\n"
        "df = pd.DataFrame({'a': [1, 2]})\n"
        "print(df.eval('a + 1'))\n"
        "print(pd.eval('df.a + 1'))",
        "python",
        "structured_query",
    )
    assert result.passed is True, result.issues


def test_bare_builtin_open_still_fatal():
    result = scanner.scan("f = open('/etc/passwd')", "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" and "open" in i.message for i in result.issues)


def test_module_qualified_io_open_is_fatal():
    result = scanner.scan(
        "import io\nf = io.open('/etc/passwd')\nprint(f.read())",
        "python",
        "structured_query",
    )
    assert result.passed is False
    assert any(
        i.code == "DANGEROUS_BUILTIN" and "io.open" in i.message for i in result.issues
    )


def test_aliased_import_of_builtin_is_fatal():
    result = scanner.scan(
        "from io import open as op\nprint(op('/etc/passwd').read())",
        "python",
        "structured_query",
    )
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" for i in result.issues)


def test_getattr_folded_builtin_name_is_fatal():
    result = scanner.scan(
        "runner = getattr(object, 'ev' + 'al')\nprint(runner('1+1'))",
        "python",
        "structured_query",
    )
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" for i in result.issues)


def test_getattr_on_builtins_is_fatal():
    result = scanner.scan(
        "print(getattr(builtins, 'open')('/etc/passwd'))",
        "python",
        "structured_query",
    )
    assert result.passed is False
    assert any(i.code == "DANGEROUS_BUILTIN" for i in result.issues)


def test_getattr_with_ordinary_name_is_allowed():
    result = scanner.scan(
        "import dataclasses\nprint(getattr(dataclasses, 'is_dataclass'))",
        "python",
        "structured_query",
    )
    assert result.passed is True, result.issues


def test_dunder_globals_escape_is_fatal():
    result = scanner.scan("print((lambda: 0).__globals__)", "python", "structured_query")
    assert result.passed is False
    assert any(i.code == "DANGEROUS_ATTR" for i in result.issues)
