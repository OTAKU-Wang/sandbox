"""Code Scanner — AST-based static analysis for sandbox-submitted code.

Implements SS-03 §5 design: whitelist mode for Python, SELECT-only for SQL.
Python: AST walk checking imports, builtins, and save paths.
SQL: sqlparse statement type validation per sandbox mode.
"""
import ast
import posixpath
import re
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ScanSeverity(str, Enum):
    FATAL = "FATAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ScanIssue:
    severity: ScanSeverity
    code: str
    message: str
    line: int | None = None


@dataclass
class ScanResult:
    passed: bool
    issues: list[ScanIssue] = field(default_factory=list)

    @classmethod
    def PASS(cls):
        return cls(passed=True)

    @classmethod
    def FAIL(cls, message: str = "", issues: list[ScanIssue] | None = None):
        if message and not issues:
            issues = [ScanIssue(ScanSeverity.FATAL, "SCAN_FAILED", message)]
        return cls(passed=False, issues=issues or [])


class SandboxMode(str, Enum):
    STRUCTURED_QUERY = "structured_query"
    STRUCTURED_MODELING = "structured_modeling"
    STRUCTURED_APP = "structured_app"
    PRODUCT_DEV = "product_dev"
    PRODUCT_DEVELOPMENT = "product_development"
    LLM_TRAINING = "llm_training"
    LLM_SFT = "llm_sft"
    LLM_PT = "llm_pretrain"
    VISION_TRAIN = "vision_train"
    MULTIMODAL_TRAIN = "multimodal_train"
    SEMI_ETL = "semi_structured_etl"
    JOINT_FEDERATED = "joint_federated"


# ── Python Security Rules (Whitelist Mode, defence-in-depth) ───────
# NIST SP 800-53: whitelist-only. Anything not in ALLOWED_IMPORTS is FATAL.
#
# A98 修正的自述：白名单**不是**安全边界，只是一层纵深防御。白名单自身就收录了
# 带文件读写与外部数据源能力的库（pathlib / pyarrow / PIL / cv2 落盘读写，duckdb /
# sqlalchemy 连任意路径与外部 DB；io 由 A97 在 `io.open`/`from io import open` 上
# FATAL，本身因 BytesIO/StringIO 太通用而保留），所以「不调 open()」并不等于读不到
# 文件。真正的边界在 hypervisor 层：bwrap/Cube 的挂载集 + exec 通道注入 + 只回收
# cube_runtime_adapter.HARVEST_DIRS（SS-18 目录契约）。这里把白名单自带的能力作为
# WARNING 披露进扫描结果与日志（不判死，见条目「不得强判」），使自述与实际一致。

ALLOWED_IMPORTS = {
    # Data science
    "numpy", "pandas", "scipy", "sklearn", "xgboost", "lightgbm",
    # ML/AI training
    "torch", "torchvision", "transformers", "datasets", "peft",
    "trl", "accelerate", "deepspeed",
    # Visualization
    "matplotlib", "seaborn", "plotly",
    # Standard library (safe subset)
    "json", "re", "math", "datetime", "collections",
    "typing", "dataclasses", "functools", "itertools",
    "io", "copy", "abc", "enum", "pathlib",
    "struct", "hashlib", "hmac", "secrets",
    "decimal", "fractions", "statistics",
    "string", "textwrap", "unicodedata",
    "uuid", "base64", "binascii",
    # Database (sandbox-internal)
    "sqlalchemy",
    # Differential privacy
    "diffprivlib", "opacus",
    # Multimedia
    "PIL", "cv2", "librosa", "torchaudio",
    # Arrow/Parquet
    "pyarrow", "pyarrow.parquet",
    # DuckDB
    "duckdb",
}

# Dangerous builtins (call → FATAL)
DANGEROUS_BUILTINS = {"exec", "eval", "compile", "__import__", "open", "globals", "locals", "breakpoint"}

# A97: 白名单库自己就提供与内建同名的正常 API（`re.compile` / `torch.compile` /
# `pd.eval`）。只有能静态解析到「已导入模块 + 危险名」且不在本清单时才按危险内建处理；
# 变量根（`df.eval`）无法静态定类型，不参与判定（残余面见台账 NOTE）。
SAFE_MODULE_ATTR_CALLS = {
    ("re", "compile"),
    ("torch", "compile"),
    ("pandas", "eval"),
}

# 属性逃逸面：拿类/函数对象摸到可执行对象的 dunder。
DANGEROUS_ATTRS = {
    "__subclasses__", "__bases__", "__mro__",
    "__globals__", "__code__", "__reduce__", "__reduce_ex__",
}

_INDIRECT_ACCESSORS = {"getattr", "__getattribute__"}
_BUILTINS_NAMES = {"builtins", "__builtins__"}

# A98: 白名单里自带文件读写能力的模块（import 成功即等价于拿到文件 I/O）。
FILE_CAPABILITY_MODULES = {
    "pathlib": "Path.open/read_text/read_bytes/write_text/write_bytes",
    "pyarrow": "read_table/read_csv/write_table/dataset（本地与对象存储路径）",
    "PIL": "Image.open/save（文件系统路径）",
    "cv2": "imread/imwrite/VideoCapture（文件系统路径）",
}

# A98: 白名单里可访问外部数据源的模块。
EXTERNAL_SOURCE_MODULES = {
    "duckdb": "connect/ATTACH/read_csv（任意路径与远端对象）",
    "sqlalchemy": "create_engine（沙箱网络可达的任意 DB URL）",
}

# 可静态解析到模块根的调用点（比 import 披露更精确的一条）。
FILE_CAPABILITY_CALLS = {
    ("pyarrow", "read_table"), ("pyarrow", "write_table"),
    ("pyarrow", "read_csv"), ("pyarrow", "write_csv"),
    ("pyarrow", "read_dataset"), ("pyarrow", "write_dataset"),
    ("PIL", "open"),
    ("cv2", "imread"), ("cv2", "imwrite"), ("cv2", "VideoCapture"),
}
EXTERNAL_SOURCE_CALLS = {
    ("sqlalchemy", "create_engine"),
    ("duckdb", "connect"), ("duckdb", "read_csv"),
    ("duckdb", "read_json"), ("duckdb", "read_parquet"),
}

# Training modes where save paths must be sandbox-restricted
GPU_MODES = {
    SandboxMode.LLM_TRAINING, SandboxMode.LLM_SFT, SandboxMode.LLM_PT,
    SandboxMode.VISION_TRAIN, SandboxMode.MULTIMODAL_TRAIN,
}

# 允许保存的目录必须等于「真的会被成果交付采集」的目录，口径与 cube_runtime_adapter.HARVEST_DIRS
# 同源（SS-18 目录契约）：/sandbox/output 是正规产物目录，/scratch 是兼容期仍参与采集的工作区。
# 之前的 /sandbox/checkpoints 不在采集面内，通过检查的代码会把产物静默丢在 VM 里。
SANDBOX_OUTPUT_PATHS = {"/sandbox/output/", "/scratch/"}


# ── SQL statement rules ───────────────────────────────────────────
# A100: 「一段语句的类型」不等于「分号后的第一个词」——
# `WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t` 的首词是 WITH，在只允许
# SELECT 的档位会被直接放行（当前会话内 DuckDB 不支持 CTE 内写操作所以不可利用，但扫描器
# 同时被 SQL→DB 网关复用）。掩掉字符串/注释后收集一段里出现的**全部**写语句类型，
# 任一类型不在该档白名单即 FATAL；各档自己的白名单集合不变。
SQL_WRITE_STMT_TYPES = ("INSERT", "UPDATE", "DELETE", "MERGE",
                        "TRUNCATE", "CREATE", "DROP", "ALTER")

# 语句类型白名单 + 拒因文案。表里没有的档位本来就不做语句类型白名单
# （只查文件 I/O 与系统目录），本条不顺手收紧。
_SQL_MODE_STMT_RULES: dict[SandboxMode, tuple[tuple[str, ...], str]] = {
    SandboxMode.STRUCTURED_QUERY: (
        ("SELECT", "WITH", "EXPLAIN"),
        "Only SELECT allowed in query mode, got {}",
    ),
    SandboxMode.STRUCTURED_MODELING: (
        ("SELECT", "WITH", "CREATE", "INSERT", "EXPLAIN", "DROP", "ALTER"),
        "Statement type {} not allowed in modeling mode",
    ),
}

# `ON CONFLICT DO UPDATE` / `ON DUPLICATE KEY UPDATE` 是 INSERT 的一部分，不是第二条语句，
# 所以用 `(?:DO\s+)?` 把它整段吐掉；括号用于区分 CTE 体与顶级尾段。
_SQL_TOKEN_RE = re.compile(
    r"[()]|\b(?:DO\s+)?(" + "|".join(SQL_WRITE_STMT_TYPES) + r")\b", re.IGNORECASE
)


@dataclass
class SqlSegment:
    """One `;`-delimited statement: its leading type plus any other type it hides."""

    lead: str
    nested: list[tuple[str, str]]  # (statement type, "cte" = inside parens / "tail" = top level)
    text: str                      # raw segment, upper-cased (file-I/O / catalog checks)


def _mask_sql_literals(sql: str) -> str:
    """Blank string literals and comments to equal-length spaces (offset-preserving).

    Keyword scanning and `;` splitting must not see literal/comment bytes: otherwise
    `WHERE s = 'a;b'` splits into two statements and `-- delete from x` reads as one.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        char = sql[i]
        if char in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == char:
                    if j + 1 < n and sql[j + 1] == char:
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
        else:
            out.append(char)
            i += 1
    return "".join(out)


class CodeScanner:
    """AST-based static code analyzer for sandbox submissions."""

    def scan(self, code: str, language: str, sandbox_mode: str) -> ScanResult:
        try:
            mode = SandboxMode(self._normalize_mode(sandbox_mode))
        except ValueError:
            return ScanResult.FAIL(f"Unknown sandbox mode: {sandbox_mode}")

        if language == "python":
            return self._scan_python(code, mode)
        elif language == "sql":
            return self._scan_sql(code, mode)
        else:
            return ScanResult.FAIL(f"Unsupported language: {language}")

    @staticmethod
    def _normalize_mode(sandbox_mode: str) -> str:
        """Normalize legacy scene names to the canonical product mode names."""
        aliases = {
            "query": SandboxMode.STRUCTURED_QUERY.value,
            "read": SandboxMode.STRUCTURED_QUERY.value,
            "modeling": SandboxMode.STRUCTURED_MODELING.value,
            "analyze": SandboxMode.STRUCTURED_MODELING.value,
            "app": SandboxMode.STRUCTURED_APP.value,
            "develop": SandboxMode.PRODUCT_DEV.value,
            "development": SandboxMode.PRODUCT_DEV.value,
            "product_development": SandboxMode.PRODUCT_DEV.value,
            "train": SandboxMode.LLM_TRAINING.value,
            "training": SandboxMode.LLM_TRAINING.value,
            "llm_sft": SandboxMode.LLM_TRAINING.value,
            "llm_pretrain": SandboxMode.LLM_TRAINING.value,
            "federated": SandboxMode.JOINT_FEDERATED.value,
            "compute": SandboxMode.JOINT_FEDERATED.value,
        }
        return aliases.get(str(sandbox_mode), str(sandbox_mode))

    def _scan_python(self, code: str, mode: SandboxMode) -> ScanResult:
        issues: list[ScanIssue] = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return ScanResult.FAIL(f"Syntax error at line {e.lineno}: {e.msg}")

        module_bindings = self._collect_module_bindings(tree)
        path_bindings = self._literal_str_bindings(tree)
        seen_capabilities: set[tuple[str, str, str]] = set()

        for node in ast.walk(tree):
            # ── Import checks (pure whitelist mode) ──
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = self._extract_modules(node)
                for mod in modules:
                    top = mod.split(".")[0]
                    if not top:
                        continue
                    if top not in ALLOWED_IMPORTS:
                        issues.append(ScanIssue(
                            ScanSeverity.FATAL, "NOT_WHITELISTED",
                            f"Module '{top}' is not in the sandbox whitelist", node.lineno,
                        ))

                if isinstance(node, ast.ImportFrom) and node.module:
                    for alias in node.names:
                        if alias.name in DANGEROUS_BUILTINS:
                            issues.append(ScanIssue(
                                ScanSeverity.FATAL, "DANGEROUS_BUILTIN",
                                f"Importing '{alias.name}' from '{node.module}' binds a "
                                f"forbidden builtin as '{alias.asname or alias.name}'",
                                node.lineno,
                            ))

            # ── Dangerous builtin checks ──
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                builtin_hit = self._dangerous_builtin(node, func_name, module_bindings)
                if builtin_hit:
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "DANGEROUS_BUILTIN", builtin_hit, node.lineno,
                    ))

            # ── Training mode: save path restrictions ──
            if mode in GPU_MODES:
                if isinstance(node, ast.Call):
                    func_name = self._get_call_name(node)
                    if func_name in ("save", "save_pretrained", "save_checkpoint"):
                        if not self._has_valid_save_path(node, path_bindings):
                            issues.append(ScanIssue(
                                ScanSeverity.FATAL, "INVALID_SAVE_PATH",
                                f"'{func_name}' must target a sandbox output path "
                                f"({', '.join(sorted(SANDBOX_OUTPUT_PATHS))})", node.lineno,
                            ))

            # ── Attribute-based dangerous access ──
            if isinstance(node, ast.Attribute):
                if node.attr in DANGEROUS_ATTRS:
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "DANGEROUS_ATTR",
                        f"Access to '{node.attr}' is not allowed", node.lineno,
                    ))

            # ── A98: whitelist-carried capability disclosure (WARNING) ──
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for module in self._extract_modules(node):
                    self._capability_issue(
                        issues, seen_capabilities, module, None, node.lineno,
                    )

            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                root = self._root_name(node.func) if func_name else None
                module = ""
                if isinstance(root, ast.Name):
                    module = module_bindings.get(root.id) or (
                        root.id if root.id in ALLOWED_IMPORTS else ""
                    )
                if module and func_name:
                    self._capability_issue(
                        issues, seen_capabilities, module, func_name, node.lineno,
                    )

        fatal = [i for i in issues if i.severity == ScanSeverity.FATAL]
        capability = [
            i for i in issues
            if i.code in ("FILE_IO_CAPABILITY", "EXTERNAL_DATA_SOURCE")
        ]
        if capability:
            # 披露出口之一：sandbox_sessions / connectors 只把 FATAL 透给调用方，
            # 没这条日志 WARNING 的话，白名单能力在那两条路径上无人可见。
            logger.warning(
                "A98 whitelist capability disclosure (the scanner is defence-in-depth, "
                "NOT the security boundary — the boundary is the hypervisor mount set, "
                "the exec channel and HARVEST_DIRS): codes=%s",
                sorted({i.code for i in capability}),
            )
        if fatal:
            return ScanResult(passed=False, issues=issues)
        return ScanResult(passed=True, issues=[i for i in issues if i.severity != ScanSeverity.FATAL])

    def _scan_sql(self, sql: str, mode: SandboxMode) -> ScanResult:
        """SQL validation using regex-based statement detection (no sqlparse dependency)."""
        issues: list[ScanIssue] = []

        for segment in self._extract_sql_statements(sql):
            rule = _SQL_MODE_STMT_RULES.get(mode)
            if rule is not None:
                allowed, reason = rule
                if segment.lead not in allowed:
                    issues.append(ScanIssue(
                        ScanSeverity.FATAL, "SQL_FORBIDDEN_STMT", reason.format(segment.lead),
                    ))
                for stmt_type, where in segment.nested:
                    if stmt_type in allowed:
                        continue
                    if where == "cte":
                        issues.append(ScanIssue(
                            ScanSeverity.FATAL, "SQL_WRITE_IN_CTE",
                            f"Write statement {stmt_type} inside a CTE body is forbidden "
                            f"({reason.format(stmt_type)})",
                        ))
                    else:
                        issues.append(ScanIssue(
                            ScanSeverity.FATAL, "SQL_FORBIDDEN_STMT",
                            f"Second statement in this segment: {reason.format(stmt_type)}",
                        ))

            # All modes: forbid file I/O
            if "INTO OUTFILE" in segment.text or "LOAD DATA" in segment.text:
                issues.append(ScanIssue(
                    ScanSeverity.FATAL, "SQL_FILE_IO",
                    "File I/O in SQL is forbidden",
                ))

            # Forbid system catalog access
            if "PG_" in segment.text or "INFORMATION_SCHEMA" in segment.text:
                issues.append(ScanIssue(
                    ScanSeverity.FATAL, "SQL_CATALOG_ACCESS",
                    "System catalog access is not allowed",
                ))

        fatal = [i for i in issues if i.severity == ScanSeverity.FATAL]
        if fatal:
            return ScanResult(passed=False, issues=issues)
        return ScanResult.PASS()

    # ── Helpers ─────────────────────────────────────────────────────

    def _extract_modules(self, node: ast.AST) -> list[str]:
        """Extract module names from Import/ImportFrom nodes."""
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            return [node.module] if node.module else []
        return []

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract the function name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    @staticmethod
    def _collect_module_bindings(tree: ast.AST) -> dict[str, str]:
        """Map bound names to their top-level module (`import io as _io` -> _io: io)."""
        bindings: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    bindings[alias.asname or top] = top
        return bindings

    @staticmethod
    def _capability_issue(
        issues: list[ScanIssue],
        seen: set[tuple[str, str, str]],
        module: str,
        attr: str | None,
        lineno: int | None,
    ) -> None:
        """Record (once) the file/external-source capability a whitelisted module grants."""
        top = module.split(".")[0]
        for code, mods, calls in (
            ("FILE_IO_CAPABILITY", FILE_CAPABILITY_MODULES, FILE_CAPABILITY_CALLS),
            ("EXTERNAL_DATA_SOURCE", EXTERNAL_SOURCE_MODULES, EXTERNAL_SOURCE_CALLS),
        ):
            if attr is None and top in mods:
                detail = mods[top]
            elif (top, attr) in calls:
                detail = f"{top}.{attr}()"
            else:
                continue
            key = (code, top, detail)
            if key in seen:
                continue
            seen.add(key)
            issues.append(ScanIssue(
                ScanSeverity.WARNING, code,
                f"Whitelisted module '{top}' grants {detail}; the import whitelist is "
                f"defence-in-depth, not the security boundary "
                f"(mount set + exec channel + harvest exclusion)",
                lineno,
            ))

    @staticmethod
    def _root_name(node: ast.AST) -> ast.AST:
        while isinstance(node, ast.Attribute):
            node = node.value
        return node

    @staticmethod
    def _folded_str(node: ast.AST) -> str | None:
        """Fold a constant string, incl. `'ev' + 'al'` concatenation."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = CodeScanner._folded_str(node.left)
            right = CodeScanner._folded_str(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    def _dangerous_builtin(
        self, node: ast.Call, func_name: str, bindings: dict[str, str]
    ) -> str | None:
        """Return the rejection reason when this call resolves to a forbidden builtin."""
        if isinstance(node.func, ast.Name):
            if func_name in DANGEROUS_BUILTINS:
                return f"Builtin '{func_name}()' is not allowed"
        elif isinstance(node.func, ast.Attribute):
            root = self._root_name(node.func)
            module = ""
            if isinstance(root, ast.Name):
                module = bindings.get(root.id, root.id if root.id in ALLOWED_IMPORTS else "")
            if module and func_name in DANGEROUS_BUILTINS \
                    and (module, func_name) not in SAFE_MODULE_ATTR_CALLS:
                return f"Module call '{module}.{func_name}()' is not allowed"

        if func_name in _INDIRECT_ACCESSORS and node.args:
            root = self._root_name(node.args[0])
            if isinstance(root, ast.Name) and root.id in _BUILTINS_NAMES:
                return "Indirect access to the 'builtins' module is not allowed"
            if len(node.args) >= 2:
                name = self._folded_str(node.args[1])
                if name in DANGEROUS_BUILTINS:
                    return f"Indirect resolution of builtin '{name}' is not allowed"
        return None

    def _has_valid_save_path(self, node: ast.Call, bindings: dict[str, str]) -> bool:
        """True when a save() destination is provably inside a harvested output dir.

        A99 has two halves. Literals are normalised first, so `/sandbox/output/../../etc`
        can no longer pass a bare startswith(); and constant-derived paths (f-strings,
        concatenation, a name assigned exactly once to a literal) are accepted instead of
        a false FATAL. Anything not statically provable stays FATAL (fail-closed).
        """
        candidates = list(node.args)
        for kw in node.keywords:
            if kw.arg in ("path", "save_directory", "output_dir"):
                candidates.append(kw.value)
        for expr in candidates:
            raw = self._fold_path(expr, bindings)
            if raw is None:
                continue
            norm = posixpath.normpath(raw)
            if any(norm == p.rstrip("/") or norm.startswith(p) for p in SANDBOX_OUTPUT_PATHS):
                return True
        return False

    def _fold_path(self, node: ast.expr, bindings: dict[str, str], depth: int = 0) -> str | None:
        """Fold a path expression to a literal string; None when not statically provable."""
        if depth > 8:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._fold_path(node.left, bindings, depth + 1)
            right = self._fold_path(node.right, bindings, depth + 1)
            if left is not None and right is not None:
                return left + right
            return None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    if value.format_spec is not None:
                        return None
                    inner = self._fold_path(value.value, bindings, depth + 1)
                    if inner is None:
                        return None
                    parts.append(inner)
                elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                else:
                    return None
            return "".join(parts)
        return None

    def _literal_str_bindings(self, tree: ast.AST) -> dict[str, str]:
        """Module-level names written exactly once with a foldable literal string.

        Sound by construction: a name with any other write anywhere in the tree (loop
        target, parameter, rebind, import alias, global/nonlocal) is excluded, so a folded
        value cannot be stale at the save() call site. Unprovable ⇒ no binding ⇒ FATAL.
        """
        writes: dict[str, int] = {}
        unsafe: set[str] = set()

        def bump(name: str) -> None:
            writes[name] = writes.get(name, 0) + 1

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bump(node.id)
            elif isinstance(node, ast.arg):
                bump(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bump(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bump(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bump(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                unsafe.update(node.names)

        sources: list[tuple[str, ast.expr]] = []
        for stmt in getattr(tree, "body", []):
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target, value = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.expr):
                target, value = stmt.target, stmt.value
            if target is None or value is None:
                continue
            if isinstance(target, ast.Name) and writes.get(target.id, 0) == 1:
                sources.append((target.id, value))

        bindings: dict[str, str] = {}
        for _ in range(3):
            for name, value in sources:
                if name in unsafe:
                    continue
                folded = self._fold_path(value, bindings)
                if folded is not None:
                    bindings[name] = folded
        return {name: val for name, val in bindings.items() if name not in unsafe}

    def _extract_sql_statements(self, sql: str) -> list[SqlSegment]:
        """Split on `;` outside literals; each segment carries **every** statement type it holds.

        A100: the old code returned only the first word of each `;` segment, so a write
        statement hidden in a CTE body (`WITH t AS (DELETE … RETURNING *) SELECT …`) or in
        the trailing clause of a WITH statement was never typed, hence never refused.
        """
        masked = _mask_sql_literals(sql)
        boundaries = [index for index, char in enumerate(masked) if char == ";"]
        boundaries.append(len(masked))

        segments: list[SqlSegment] = []
        start = 0
        for end in boundaries:
            piece = masked[start:end]
            if piece.strip():
                segments.append(self._classify_sql_segment(sql[start:end], piece))
            start = end + 1
        return segments

    @staticmethod
    def _classify_sql_segment(raw: str, masked: str) -> SqlSegment:
        """Type the segment: leading keyword plus write keywords at top level or in parens."""
        words = masked.split()
        lead = words[0].upper() if words else ""
        nested: list[tuple[str, str]] = []
        depth = 0
        for match in _SQL_TOKEN_RE.finditer(masked):
            token = match.group(0)
            if token == "(":
                depth += 1
                continue
            if token == ")":
                depth = max(0, depth - 1)
                continue
            if token[:2].upper() == "DO":  # ON CONFLICT DO UPDATE — not a second statement
                continue
            stmt_type = match.group(1).upper()
            if stmt_type == lead:
                continue
            where = "cte" if depth else "tail"
            if (stmt_type, where) not in nested:
                nested.append((stmt_type, where))
        return SqlSegment(lead=lead, nested=nested, text=raw.upper())


code_scanner = CodeScanner()
