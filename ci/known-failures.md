# CI known failures baseline（W7 软门禁）

> 规则：`Full suite (informational)` job 的失败数**不得增长**。每次合入后人工比对下方基线；
> 新失败如果是本次改动引入，必须修复后合入。环境性失败清单见
> `docs/ai-sandbox-gap-remediation-plan.md` 各轮回归结论。

## 门禁层（不适用）

Gate tests job 中的 24 个测试文件必须全绿，任何失败直接阻断合入。

## 全量层基线

| 日期 | 基线 | 说明 |
|---|---|---|
| 2026-10-15 | **2864 passed / 24 failed / 5 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，**远程主机 py3.12 生产派生镜像** + 真实 PostgreSQL 16 / Redis / ClickHouse / OPA / Vault，1683.98s；同树门禁层 **231 passed / 2 skipped / 0 failed**（183.89s，两次一致）） | CR 2026-10-15 轮（B1/B2/M1/M2 + SM3 门旁路 + 卷算法实标 + 远程发现的 4 项）最终树；分支 `feat/ss-11-productization`。**口径变更：本轮按用户要求改在远程 py3.12 生产镜像跑，不再与上方 py3.14 venv 行直接比数——镜像缺国密 libcrypto、缺 `git`、peft 版本不同，24 项失败逐条定性且全部单跑复验，无一由本轮改动引入**（详见 `docs/cr-2026-10-15-review.md` §5.1）。分解：13× `test_cds_volume_plugin`（镜像 `libcrypto.so.3` 无 `EVP_sm4_gcm`，C 助手 127 退出，插件 fail-closed 拒 attach＝预期诚实降级）+ 3× `test_cube_upstream_check`（镜像内 `NO_GIT`）+ 4× `test_pz01_sft_real`（peft 版本耦合）+ 1× `test_async_operations::test_failed_operation_records_error`（**基线提交 `4ccc43b` 同镜像单跑同样失败＝基线既有**，非本轮引入）+ 1× `test_e2e_flows::test_e2e_monitoring_stats_api`（跨事件循环连接池复用，两轮全量同位置复现、单跑 42/42 绿）+ 2× SM2 稀有单发（`test_result_verifier::test_valid_signature`、`test_p0_8_9_10::TestDEKSM2Encryption::test_generate_data_key_sm2_encrypted`，单跑 17/17、4/4、合跑 29/29×2 全绿；根因见分类表 SM2 行）。过程：首轮 84 failed → 修 `requirements.txt` 缺 `cryptography` + `tee_probe.py` 的 `Path.exists` 非绑定默认参数（py3.12 必崩、3.14 容忍）+ scopes 计数裁剪次序 + `deps.py` 审计写入改 SAVEPOINT → 次轮 23 failed → 修自己新测试的 gmssl 桩（只翻 `_HAS_GMSSL` 不够，需 `sys.modules` 摘除）→ 终轮 24 failed（−1 自身测试修复，+2 SM2 稀有单发）。被测树与本地提交树 505 个源文件清单哈希一致（`ca3ab67f0bc92c6e0f74cf097e574ad5`）。无测试被删除、跳过或放宽断言/超时 |
| 2026-10-13 | **2784 passed / 0 failed / 3 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv，732.94s） | SS-13 MicroVM 单路径 + TEE 可选（RT-01..05, 08）分支 `feat/ss-11-productization` 最终树全量（裁决跑）；零失败——不劣于 SS-10 行（2758/0/3）。pass 增量 +26 = SS-13 新测试（default_and_legacy 4 + tee_policy 5 + e2e 3 + node_tee_probe 14）。过程：首轮 9 failed（conftest 种子 fixture 打挂精确节点集/生产门顺序测试 ×5 + RT-04 披露面加性键打挂 SS-11 键集断言 ×4，全数定性修复，36/36 复验绿）；次轮 1 failed（`test_pagination` 单发 AttributeError，按 diagnose-full-suite-test-pollution 流程排除：单跑 30/30 + 预导入全模块 + 字母序前缀 113 文件复现均未复现，判定罕见单发事件，探针已移除、测试恢复原样）；本轮零失败。skip 3 与基线同集 |
| 2026-10-13 | **2758 passed / 0 failed / 3 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv，724.03s） | SS-10 M1+M2 软件侧（CB-00..06 + KMS 卷密钥控制面）分支 `feat/ss-11-productization` 最终树全量；零失败——不劣于 2026-10-13 SS-11 行（2654/0/3）。pass 增量 +104 = SS-10 新测试（cube 节点客户端 11 + L2' 适配器 7 + pz13 能力矩阵 + egress 编译 17 + 模板管线 14 + 审计尾随 14 + KMS 卷密钥 + 上游契约监视等）。过程：CB-04 初版 `_session_is_l2c` 自开第二 DB 会话回滚共享连接（e2e 3×401 wipe）→ 父任务探针定位 + 修复（create_policy/remove_policy 增 db 参数，11 调用点透传）；次轮回归 2756/2/3（2× 测试 fake 未接 db 参数）→ 测试侧修复后本轮零失败。3 skip 与基线同集 |
| 2026-10-13 | **2654 passed / 0 failed / 3 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv，771.23s） | SS-11 缺口收口轮（G-1..G-4：role PATCH 服务端强制 reason/ticket_id + `AUTH_MFA_REQUIRED` 入目录 + 三源一致性测试 3 项 + ci 门禁提级 3 文件）后最终树；零失败——不劣于 2026-10-12 P1 行（2649/0/4）。pass 增量 +5 = 新增契约测试 4 项（pz10 reason 契约 1 + capability_matrix_consistency 3）+ 1 项条件性 skip 本轮转为 pass。新入 gate 清单：test_pz10_mfa / test_capability_matrix_consistency / test_e2e_data_loop |
| 2026-10-12 | **2649 passed / 0 failed / 4 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv，832.6s） | SS-11 P1（Round 46 P1 批次，PZ-09..16）分支 `feat/ss-11-productization` 最终树全量（第三轮定讫）；零失败——不劣于同日 P0 终点行（2604/0/3），pass 增量 +45 = P1 新测试。过程：首轮 26 failed（25× `test_e2e_flows` 适配 PZ-10 注册角色白名单缺失——`_register` 改 buyer 省略角色 + 非 buyer 走 make_user；1× `test_pz10_mfa::test_cert_login_disabled_via_setting` 打错 settings 实例——auth.py 模块级绑定 + test_k8s_n7 cache_clear 污染，改 patch `app.api.auth.settings`）；次轮 1 failed（`test_certificate_service::test_issue_ca_certificate` signature_valid=False——30 隔离 + 10×22 前置循环 + 全模块预导入 40+ 次复现全绿，cert/tlcp 服务 P1 未触碰，判定罕见单发事件，候选 SM2 签名字节边界专项排查）；第三轮零失败。两处测试侧适配均保持测试本意，无断言弱化。skip 3→4 为条件性 skip 波动（非失败） |
| 2026-10-12 | **2604 passed / 0 failed / 3 skipped**（排除 `tests/test_e2e_full_lifecycle.py`，Linux，Python 3.14 venv） | SS-11 P0（Round 46，PZ-01..08）分支 `feat/ss-11-productization` 最终树全量；零失败；pass 增量为 Round 45/46 新测试（+184 vs 09-07 基线） |

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
| 国密 libcrypto（Tongsuo） | 远程 py3.12 生产镜像只有社区版 OpenSSL 3.5.7，`nm -D libcrypto.so.3` 查无 `EVP_sm4_gcm`；C 助手 `undefined symbol` 以 127 退出 | 环境门禁；插件 self-test **fail-closed 拒 attach** 是预期行为（禁伪造），真机 Tongsuo 为验收项 |
| 镜像内无 `git` | `command -v git` → `NO_GIT`；`test_cube_upstream_check` 需 `git init/commit` 造本地 upstream 仓 | 环境门禁（CI 镜像不装 git），不修复 |
| peft 版本耦合 | 镜像内 peft 对 dummy 基底模型报 `Target modules {'q_proj','v_proj'} not found` | 依赖版本漂移，单独跟踪，不改断言 |
| 跨事件循环连接池复用 | 套件级共享 engine 的连接对象绑在已销毁的 loop 上 → `StatementError: (builtins.RuntimeError) <asyncio.locks.Lock ...> is bound to a different event loop` | 2026-10-15 远程两轮全量在**同一位置**复现（`test_e2e_flows::test_e2e_monitoring_stats_api`），而单跑该文件 42/42、与他文件合跑 47/47 均不可复现 → 套件顺序相关；按 diagnose-full-suite-test-pollution 流程定性，不改试验 |
| gmssl SM2 稀有单发 | gmssl 纯 Python SM2 的坐标/密文按裸十六进制拼接，**不做定宽零填充**：300 次探针中同一 8 字节明文的密文长度为 103/104/105 字节（105 占 200、104 占 90、103 占 10），而 `decrypt()` 按固定偏移切片；签名路径同族 | 单发概率随坐标前导零出现（每坐标约 1/256），全量一轮出现 1–2 次；历史同源见 2026-10-12 行 `test_issue_ca_certificate` 罕见单发。定性为环境/依赖相关稀有单发，修复走 §4.12 定宽化方案（基线既有、无调用者的死路径，不改断言掩盖） |
| pytest-timeout 新增 | 若出现超时失败，先查挂死根因，不得调大掩盖 | 必须修复 |
