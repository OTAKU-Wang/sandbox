"""回归针：`.env.example` 照抄成 `.env` 不能炸掉 Settings（2026-11-02 全栈 e2e 实锤）。

根因两层：
1. pydantic-settings 对 dotenv 源不套 `env_prefix`（只对环境变量套）——compose 专用键
   （POSTGRES_PASSWORD / vite 注入键…）原样流入校验。
2. `model_config` 不写 `extra` 时，pydantic-settings v2 默认 **forbid**：
   1 的多余键直接 ValidationError，71 个错从第一个字段炸出来。
   后果：`validate_security_config` 的 fail-closed 报错**永远到不了**，
   而 `.env.example` 的文档用法（"Copy to .env for local development"）自断活路。

口径（钉死）：
- dotenv 侧宽松：非模型键忽略（compose/前端注入键与后端模型本就异域）；
- 环境变量侧严格保留：`CDS_*` 打错键名必须仍然响亮拒绝——安全旗标的
  typo-fail-loud 面（PZ/SS 系列）不许被这次修复顺手放松。
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(tmp_path: Path, code: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("CDS_")}
    env["CDS_JWT_SECRET_KEY"] = "0123456789abcdef0123456789abcdef"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(REPO)!r}); " + code],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )


_BOOT = (
    "from app.core.config import get_settings\n"
    "s = get_settings()\n"
    "print('OK', s.JWT_SECRET_KEY == '0123456789abcdef0123456789abcdef')\n"
)


def test_example_env_boot_survives_settings(tmp_path):
    import shutil

    shutil.copy2(REPO / ".env.example", tmp_path / ".env")
    # app 必须可导入：把仓根加进 PYTHONPATH（cwd 故意换成 tmp_path，模拟 .env 生效面）
    r = _run(tmp_path, _BOOT)
    assert r.returncode == 0, (
        f"照抄 .env.example 后 Settings 直接崩——compose 键漏进模型校验：\n{r.stderr[-800:]}"
    )
    assert "OK True" in r.stdout


def test_prefixed_typo_env_is_ignored_upstream_semantics_pinned(tmp_path):
    """如实记录并钉死上游语义：`CDS_*` 环境变量拼错键名 = 静默忽略（不是崩溃）。

    这是 pyd-settings 的既有行为（env 源只按字段匹配，从不带未知键；forbid 只在
    dotenv 源有意义）。本测试不是为了表扬该行为，而是把「typo-fail-loud 在环境变量侧
    从来不存在」这一实证钉在册——若未来给安全旗标加 typo 拒绝，改这条针就是显式改口。
    """
    r = _run(tmp_path, _BOOT, {"CDS_THISSHOULDEXIST_NOT": "42"})
    assert r.returncode == 0, f"环境变量侧未知 CDS_* 键不得崩启动（上游语义）：\n{r.stderr[-400:]}"
    assert "OK True" in r.stdout
