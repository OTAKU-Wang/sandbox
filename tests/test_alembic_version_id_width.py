"""回归针：alembic revision ID 宽度 vs 版本表 VARCHAR(32)（2026-11-02 远端 e2e 实锤）。

真实 PG16 上 `alembic upgrade head` 在 0029→0030 的 stamp UPDATE 炸
`value too long for type character varying(32)`：alembic 自管的版本表写死
sa.String(32)，而 0030/0033 的 ID 有 34 字符。SQLite 不执法度所以本机/CI 从未暴露。
0030 的 upgrade() 以 PG 守卫把列加宽到 128（stamp 在 upgrade() 之后，加宽必须发生在
第一个超长 ID 落库之前——见 0030 docstring 的位置论证）。

这里钉两条：
1. 新 revision 的 ID 若超 32 字符，编号必须排在加宽点（0030）之后——链上第一个
   超长 ID 之前不存在超长的可能；
2. 加宽语句必须还住在 0030 里（被人「清理」掉即红，链在 PG 上会重新起爆）。
"""
import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_WIDEN_AT = 30  # 0030 = 加宽点（链上第一个超 32 字符 ID 的编号）


def _revision_ids() -> list[tuple[int, str]]:
    out = []
    for path in sorted(VERSIONS.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        m = re.search(r'^revision\s*(?::[^=]+)?=\s*["\']([^"\']+)["\']', src, re.M)
        assert m, f"{path.name}: 找不到 revision 声明"
        out.append((int(m.group(1)[:4]), m.group(1)))
    return out


def test_no_overlong_ids_before_widen_point():
    overlong = [(num, rid) for num, rid in _revision_ids() if len(rid) > 32]
    early = [(num, rid) for num, rid in overlong if num < _WIDEN_AT]
    assert not early, (
        f"这些 revision 的 ID 超过 32 字符但排在加宽点 0030 之前，PG 上 stamp 必炸："
        f"{early}——要么改短 ID，要么把加宽逻辑前移到更早的 revision"
    )


def test_widen_statement_lives_in_widen_point():
    src = (VERSIONS / next(
        p.name for p in VERSIONS.glob(f"00{_WIDEN_AT}_*.py")
    )).read_text(encoding="utf-8")
    assert "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)" in src, (
        "0030 里的版本表加宽语句被删了——真实 PG 上 0029→0030 的 stamp 会重新起爆"
        "（SQLite 测不出来，这是 2026-11-02 远端 e2e 的实锤教训）"
    )


def test_widen_is_pg_guarded_noop_elsewhere():
    """加宽必须被 dialect 守卫短路——SQLite/其他方言上零副作用（不改已应用库语义）。"""
    path = next(VERSIONS.glob(f"00{_WIDEN_AT}_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_widen_alembic_version_column"
    )
    guard = "postgresql" in ast.unparse(fn)
    assert guard, "_widen_alembic_version_column 缺 PG dialect 守卫"
