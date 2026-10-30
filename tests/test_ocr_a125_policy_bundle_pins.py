"""A125 — policy_bundle.py 模型层潜伏缺陷钉（读侧锚点 policy_evaluator 零生产导入）。

修复组（b 方案：保持无密钥 SM3，但把真实保证强度写死三处 + 机器钉）：
  B1  三处披露缺一即红：①模型字段注释 ②列注释(comment= DDL) ③boot 披露
      （import 时经 main.py:41 的模型导入路径触发，disclose_integrity_limitations）。
  B2  列注释必须活在 SQLAlchemy 表对象上（不只源码文本）。
  B3  boot 披露可测（force 重放），文案含 A125 + 无密钥/unkeyed 语义。
  B4  ①号缺陷 LATENT 坐标：模型层无查询 helper（models 约定 declarative-only），
      docstring 必须记 revoked_at 防线落点（读侧过滤=app/services/policy_evaluator.py
      的 A73 过滤；撤销写入=app/services/contract_service.py）+ fallback PDP
      接线要求（读 field_acl/allowed_ops/sandbox_modes 者必须把完整性校验纳入校验面）。
可达性复核（lead 明令：死人有复活迹象就先红报 lead）：
  R1  app/services/policy_evaluator.py 仍零生产导入（secure_duckdb 引的
      policy_evaluator 是 policy_compiler 的同名单例，不同人）。
  R2  模型行写入方在 app/ 不存在（白名单：contract_service 撤销标记读 +
      policy_evaluator 死读侧）；新 importer = 复活迹象 → 红。
  R3  防线落点仍活：policy_evaluator 源码仍含 revoked_at 过滤（A73）。
"""
import logging
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"
MODEL_PATH = APP_DIR / "models" / "policy_bundle.py"
EVALUATOR_PATH = APP_DIR / "services" / "policy_evaluator.py"


def _app_sources():
    for p in sorted(APP_DIR.rglob("*.py")):
        yield p, p.read_text(encoding="utf-8")


class TestBThreeDisclosureSites:
    def test_b1a_model_field_comment_present(self):
        src = MODEL_PATH.read_text(encoding="utf-8")
        assert "只防无意不一致" in src, "模型字段注释缺失：无密钥 SM3 的保证强度必须写死"

    def test_b1b_column_comment_present_in_source(self):
        src = MODEL_PATH.read_text(encoding="utf-8")
        # 列注释以 comment= kwarg 落 DDL
        assert 'comment=' in src and "unkeyed" in src.lower(), \
            "列注释缺失：integrity_hash 列必须带 comment= 披露"

    def test_b1c_boot_disclosure_wired(self):
        src = MODEL_PATH.read_text(encoding="utf-8")
        assert "def disclose_integrity_limitations" in src
        assert "logger.warning" in src
        # import 时真的会调用（boot 路径：main.py:41 导入本模型模块）
        lines = [ln.strip() for ln in src.splitlines()]
        call_lines = [ln for ln in lines
                      if ln.startswith("disclose_integrity_limitations(")]
        assert call_lines, "boot 披露缺失：模块级必须实际调用 disclose_integrity_limitations()"

    def test_b2_column_comment_live_on_table(self):
        from app.models.policy_bundle import PolicyBundle
        comment = PolicyBundle.__table__.c.integrity_hash.comment
        assert comment, "integrity_hash 列注释必须是 SQLAlchemy 层活注释（进 DDL）"
        low = comment.lower()
        assert "unkeyed" in low or "无密钥" in comment
        assert "A125" in comment

    def test_b3_boot_disclosure_emits(self, caplog):
        from app.models import policy_bundle as mod
        with caplog.at_level(logging.WARNING, logger=mod.__name__):
            mod.disclose_integrity_limitations(force=True)
        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("A125" in m and ("无密钥" in m or "unkeyed" in m.lower()) for m in msgs)
        assert getattr(mod, "_BOOT_DISCLOSURED", False) is True, "import 时已完成首次披露"

    def test_b4_docstring_latent_coordinates_and_wiring_requirement(self):
        src = MODEL_PATH.read_text(encoding="utf-8")
        for needle in (
            "app/services/policy_evaluator.py",   # 读侧锚点坐标
            "app/services/contract_service.py",   # 撤销写入坐标
            "revoked_at",                          # ①防线主题
            "fallback PDP",                        # 接线要求主语
            "integrity",                           # 校验面要求
        ):
            assert needle in src, f"LATENT 坐标/接线要求缺失: {needle!r}"


class TestRReachability:
    """死人有复活迹象就先红报 lead（A125 可达性复核，机器化）。"""

    def test_r1_policy_evaluator_still_zero_production_importers(self):
        import re
        pat = re.compile(
            r"app\.services\.policy_evaluator\b"
            r"|from\s+app\.services\s+import\s+[^\n#]*\bpolicy_evaluator\b"
        )
        offenders = []
        for p, src in _app_sources():
            if p == EVALUATOR_PATH:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if pat.search(line):
                    offenders.append(f"{p.relative_to(APP_DIR.parent)}:{i}: {line.strip()}")
        assert offenders == [], (
            "policy_evaluator 出现生产导入迹象——A125/A73 LATENT 前提失效，先报 lead: "
            + "; ".join(offenders)
        )

    def test_r2_no_new_model_writers_readers(self):
        allowed = {
            "app/models/policy_bundle.py",
            "app/services/contract_service.py",  # 撤销标记（revoked_at 写入方）
            "app/services/policy_evaluator.py",  # 死读侧（A73 锚点）
        }
        import re
        pat = re.compile(r"from\s+app\.models\.policy_bundle\s+import|models\.policy_bundle\b")
        offenders = []
        for p, src in _app_sources():
            rel = str(p.relative_to(APP_DIR.parent))
            if rel in allowed:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if pat.search(line) and not line.strip().startswith("#"):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        assert offenders == [], (
            "PolicyBundle 模型出现白名单外触点——LATENT 前提变化，先报 lead 再谈接线: "
            + "; ".join(offenders)
        )

    def test_r3_revoked_filter_defense_still_at_anchor(self):
        src = EVALUATOR_PATH.read_text(encoding="utf-8")
        assert "revoked_at.is_(None)" in src, "A73 撤销过滤已从锚点消失——防线落点失效"
        # 过期侧锚点 = 合约 valid_until（合约生命周期，非 bundle 列）
        assert "valid_until" in src, "A73 合约过期检查已从锚点消失——防线落点失效"


def test_a125_tag_registered_for_grep_ledger():
    # 钉账 grep 入口：TODO(A125) 或 A125 标注必须至少存在于模型模块
    assert "A125" in MODEL_PATH.read_text(encoding="utf-8")
