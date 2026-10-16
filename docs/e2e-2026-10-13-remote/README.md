# SS-14 — 远程 e2e 真实环境测试过程档案（2026-10-13）

目标: 将 CDS 部署到远程真实环境（中国境内服务器，ssh 免密，依赖走国内源，镜像走渡渡鸟 ddn 加速），完成 环境部署 → API key 业务闭环 → 沙箱安全 → 可信数据空间场景 四级验证，修复全部测试发现问题，全程留痕。

远程: 100.112.5.x:22022（root，免密 ssh；密码/密钥不入本目录任何文档）。

## 文档索引

| 文件 | 内容 | 状态 |
|---|---|---|
| `01-deployment.md` | 环境部署：侦察、镜像源策略、部署脚本迭代（deploy#1..#N 败点与修复）、S1 验收证据 | 在库（S1 PASS 证据已回填） |
| `02-business-loop.md` | S2 平台 API key 业务闭环（资源→产品→契约→会话→执行→审计）：步骤契约、逐轮执行记录、缺陷关联 | 已定稿（远程 16/16 + 节点轨 22/22 两形态全绿） |
| `03-sandbox-security.md` | S3 沙箱安全：fail-closed（无节点 l2c 400 / 退役级别 400）、TEE/引擎披露一致性、节点注册表面、节点轨活会话隔离（microVM 边界实证） | 已定稿（远程 10/10 + 节点轨隔离全绿） |
| `04-defects-and-fixes.md` | 缺陷台账 D-e2e-01..21：发现/根因/修复/复验证据（唯一缺陷权威源） | 已定稿（21 项全部 fixed，无未决） |
| `05-tds-scenarios.md` | S4 可信数据空间场景（SS-01..09 契约蒸馏 T1..T6）：执行记录（远程 7P/3S + 节点轨 12P 活会话腿） | 已定稿（两形态全绿，无 SKIP 遗留） |
| `README.md` | 本索引 + 总结论 + 证据文件清单 | 收口时定稿 |

## 证据文件（远程 /root/ + 本机 evidence-node/，关键摘录已回填对应 md）

- `e2e_business_loop_evidence.json` — S2 逐步证据（远程 /root/）
- `e2e_sandbox_security_evidence.json` — S3 逐步证据（远程 /root/）
- `e2e_tds_scenarios_evidence.json` — S4 逐步证据（远程 /root/）
- `s1_check` 输出（/health、/metrics、前端）— 见 01 §6
- `evidence-node/live_session_v38.json` — 节点轨 N1-N10 活会话 12/12
- `evidence-node/business_loop_node.json` — 节点轨 S2 业务闭环 22/22
- `evidence-node/tds_scenarios_node.json` — 节点轨 S4 场景 12/12
- `evidence-node/isolation_live.json` — 节点轨特权/设备面探针

## 复现入口

- 部署: `deploy/e2e-remote/remote_deploy.sh`（远程 /root/cds-deploy/）
- 测试: `deploy/e2e-remote/e2e_business_loop.py | e2e_sandbox_security.py | e2e_tds_scenarios.py`
- 可复用 skill: `~/.agents/skills/cds-remote-e2e/SKILL.md`

## 总结论（两形态总定稿：远程形态 + 节点轨形态）

**S1 环境部署: PASS（两形态）** — 远程 8/8 容器 Up（deploy 脚本化可重跑，含 DDL 清理 + Vault transit 前置）；节点轨本机 KVM 栈全活（cube 10 单元 + cds-volume-plugin + CDS 4 容器，`node_bringup.sh` 幂等可重跑）。

**S2 业务闭环: PASS（两形态）** — 无节点 16/16（诚实 fail-closed 400 分支）+ **节点轨 22/22**（l2c 活会话真实执行腿：上传/执行/门禁下载/用量/proof-bundle/终止）。

**S3 沙箱安全: PASS（两形态）** — 无节点 10/10（fail-closed/退役拒绝/诚实披露）+ **节点轨活会话隔离全绿**：宿主进程/挂载不可见、egress 拒绝域、跨租户 403/403、mount 系统调用 DENIED；**microVM 边界真实**（guest 内核 6.6.1199 ≠ 宿主 7.0.0-30，guest 1GB ≠ 宿主 15GB，/dev/mem 仅 guest 自身 RAM）；guest 内部设备特性已如实披露（非宿主逃逸）。

**S4 TDS 场景: PASS（两形态，无 SKIP 遗留）** — 无节点 7P/3S + **节点轨 12P/0S**（T2 跨方隔离 / T3 输出治理 / T5 proof-bundle 活会话腿全部真实执行）。

**S5 缺陷: 0 未决** — D-e2e-01..21 全部 fixed + 复验（台账 04）：远程轨 14 项 + 节点轨 7 族（镜像 uids_file、asyncpg 跨 loop 族、卷契约族、冷启动卷面缺失+数据面 v2、exec 通道契约族、l2c 会话契约面、e2e 脚本设计族）。

**S6 过程留存: PASS** — 本目录 6 文档 + 远程 3 证据 JSON + 节点轨 4 证据 JSON（evidence-node/）+ 部署日志链。全部密码仅存部署机 /root/cds.env 与本机 /tmp/cds-local.env，**未落仓库 markdown**。

**S7 可复用 skill: PASS** — `~/.agents/skills/cds-remote-e2e/SKILL.md`（部署链 + 测试流程 + 踩坑录；节点轨教训补入）。

**可用状态判定（总定稿）**: 平台在两条形态下均可正常使用——无节点环境诚实 fail-closed（不伪造、不回退）；有节点环境全业务闭环真实可达（业务/安全/TDS 三轨全绿，节点轨活会话全链路无 SKIP 遗留）。

**本地回归**: 远程轨轮全量回归 **2871 passed / 3 skipped / 0 failed**（与基线精确持平）；节点轨改动面（SyncDbBridge/prepare_session_volume/ReadSandboxFile/exec 中转/l2c 会话契约面）收口：**2876 passed / 3 skipped / 0 failed**（2026-10-14，排除 `tests/test_e2e_full_lifecycle.py`，Python 3.14 venv，800.59s）—— 不劣于 2871 基线，pass 增量 +5 = 节点轨新测试（deadline 档契约 / 卷预建 fail-closed / storage_media OCI 默认 / container_id 32-hex 右切 / hot-swap 全状态推送等）。过程：首轮全量 21 failed 全部定性为 RT-07 新契约（l2c 产品数据 fail-closed / 卷预建硬件依赖 / 探针 List+GetStorageMetrics / 32-hex container_id / capabilities dict / CUBE_RESULTS_DIR 预建）与 SS-13 时代测试 fixture 的漂移 —— 按新契约更新 fixture（conftest 增 prepare_session_volume 测试替身 + get_storage_metrics/list 桩 + 32-hex sandboxID + publish_product 自动补已上传资源 + CDS_CUBE_RESULTS_DIR 钉 /tmp；node 测试 capabilities list→dict；registration 探针 monkeypatch 改新入口），零产品行为削弱；另修 1 个真产品 bug（sandbox_sessions.py create 路径 `session_data` 分支内赋值/分支外引用 → 非 Cube 适配器 UnboundLocalError，无条件初始化修复）。
