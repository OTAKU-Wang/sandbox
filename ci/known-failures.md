# CI known failures baseline（W7 软门禁）

> 规则：`Full suite (informational)` job 的失败数**不得增长**。每次合入后人工比对下方基线；
> 新失败如果是本次改动引入，必须修复后合入。环境性失败清单见
> `docs/ai-sandbox-gap-remediation-plan.md` 各轮回归结论。

## 门禁层（不适用）

Gate tests job 中的 24 个测试文件必须全绿，任何失败直接阻断合入。

## 全量层基线

| 日期 | 基线 | 说明 |
|---|---|---|
| 2026-09-07 | **2420 passed / 0 failed / 3 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv） | P2（W15-W19）合入后全量；零失败 |
| 2026-09-07 | 2399 passed / 0 failed / 3 skipped | P1（W8–W14）+ W10 前端合入后全量；零失败 |
| 2026-09-07 | 2359 passed / 0 failed / 3 skipped | W3/W5/W7 合入后全量；20 个既有失败已清零（12 个 `llm_sft_runtime.mia_status` UnboundLocalError 真实缺陷修复 + 8 个 `test_admin_frontend` 过时测试对齐 W4 门禁设计） |
| 2026-09-06 | 2319 passed / 7 failed（Round 41，全 cgroup 门禁） | 历史基线 |

## 既有环境性失败分类（历史轮次记录，Linux 基线）

| 分类 | 原因 | 处置 |
|---|---|---|
| L0 bwrap/cgroup | CI 容器 cgroup 只读 / 无 bwrap 命名空间权限 | 环境门禁，不修复 |
| TEE 设备检测 | 无 SGX/SEV 设备 | 硬件验收项（FG-004） |
| tmpfs/磁盘加密 | CI runner 无 tmpfs/加密盘 | 环境门禁 |
| e2e 集群 | `tests/test_e2e_full_lifecycle.py` 打真实集群（已 ignore） | 实时环境单独跑 |
| pytest-timeout 新增 | 若出现超时失败，先查挂死根因，不得调大掩盖 | 必须修复 |
