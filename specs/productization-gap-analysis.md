# CDS 密态沙箱产品化 Gap 分析与修复计划

> 更新时间：2026-09-07  
> 范围：产品化可用性、运营运维、安全合规、部署交付、前端体验、可观测性与验收测试  
> 当前原则：先修复不依赖真实 TEE/GPU/K8s/联盟链/SIEM 的软件闭环；真实硬件、真实外部系统、性能长稳和 e2e 作为产品化验收项持续跟踪。  
> 产品化基线：自 Round P10 起，产品化能力对齐升级为 **CDS × CubeSandbox 能力对齐方案**（`docs/cubesandbox-parity-plan.md`），分 M1（W1/W2/W4/W6）与 M2（W3/W5/W7）落地。**M1+M2 已完成**，P0 产品化基线清零（详见 §5「产品化基线执行记录」）。

---

## 1. 当前产品化结论

系统核心逻辑已经具备可信数据空间密态沙箱的主链路：数据产品、合约策略、沙箱运行时、密钥/证书、输出审查、审计存证、联邦互联、告警后端、非结构化处理和训练管线均已有可编译、可单测的软件实现。

距离“可试点交付”的主要差距不再是单点函数缺失，而是产品化完整性：

- 运营台体验：告警处置、异常状态、筛选、空状态、移动端和多角色工作台仍需逐项收敛。
- 部署交付：真实 K3s/K8s、Postgres/Redis/MinIO、备份恢复、迁移、密钥轮换和 CI/e2e 需要固化。
- 安全合规：威胁模型、SBOM、依赖扫描、镜像扫描、K8s CIS baseline、渗透/逃逸测试需要形成证据包。
- 外部系统：TEE/GPU-TEE、HSM/Vault/国密、联盟链、SIEM/邮件/短信、Kafka Connect 等需要真实环境验收。
- 质量基线：性能、容量、故障恢复、长稳和并发压测尚未形成产品级基线。

当前复扫结论：软件可闭环产品化 gap 已清零；剩余项均依赖真实硬件、真实外部系统、真实 K3s/K8s 集群、性能长稳或 CI/e2e 环境验收，不在普通代码编译阶段伪造完成。

---

## 2. 产品化 Gap 表

| ID | Gap | 优先级 | 类型 | 当前状态 | 验收标准 | 修复状态 |
|---|---|---:|---|---|---|---|
| P-GAP-001 | 前端监控中心仍按旧 audit-log 告警结构展示，缺少持久化告警筛选、ack/resolve 处置和通知状态可见性 | P1 | 软件可闭环 | 后端 `AlertCenter` 已完成，前端未对齐 | 运营/管理员可查看告警类型、等级、状态、occurrence、通知状态；可确认/解决告警；列表可按状态/等级过滤 | Fixed |
| P-GAP-002 | 产品化部署 runbook、环境变量、安全默认值和验收命令未集中成一份交付文档 | P1 | 软件可闭环 | 配置分散在 README/specs/scripts | 有单独部署检查清单，覆盖本地/243/K3s/生产模式、安全必填项和回滚方式 | Fixed |
| P-GAP-003 | 前端关键页面缺少统一错误态/加载态/空状态审计 | P2 | 软件可闭环 | 已有基础页面，未形成系统性 QA 清单 | 角色工作台、目录、合约、沙箱、监控、输出审查均有清晰空/错/加载态 | Fixed |
| P-GAP-004 | 真实 TEE/GPU-TEE/HSM/联盟链/SIEM 集成验收未完成 | P0 | 环境验收 | 软件 hook/fallback 已实现 | 真实环境通过 e2e，证书链/密钥释放/链回执/SIEM 事件可验证 | Deferred |
| P-GAP-005 | K3s/K8s 真实集群 e2e 与 NetworkPolicy/FQDN/ResourceQuota 生效验证未固化 | P0 | 环境验收 | manifest 和单测已完成，243 可部署；**W5 已补 Alembic 基线迁移（31 表），生产空库可一键建表** | 243 或专用节点一键部署并跑沙箱生命周期 e2e | Deferred |
| P-GAP-006 | 性能容量和长稳基线缺失 | P1 | 环境验收 | 聚焦测试通过，未压测 | 形成并发沙箱数、启动延迟、查询延迟、审查吞吐、故障恢复和 24h 长稳报告 | Deferred |
| P-GAP-007 | 安全认证/供应链证据包缺失 | P0 | 环境验收 | 代码级安全修复充分，缺扫描证据 | SBOM、SAST、依赖漏洞、镜像扫描、K8s CIS、渗透测试报告可归档 | Deferred |
| P-GAP-008 | 全量 CI/e2e 回归矩阵未固定 | P1 | 软件可闭环 | **W7 已上线 `.github/workflows/ci.yml`**（backend 编译+迁移+分层测试门禁 / frontend build / gitleaks）；e2e 矩阵仍需 243 环境 | CI 或 243 自动跑最小生命周期、联邦、链存证、训练、输出审查、K3s e2e | Fixed* |
| P-GAP-009 | 前端服务层与后端路由/字段契约存在脱节，部分交互会触发隐藏 404 或展示误导性状态 | P1 | 软件可闭环 | KMS/证书、连接器、联邦、训练页面和服务层存在路由、请求参数、返回字段不一致 | 关键页面操作对齐真实后端接口；状态类接口返回完整对象；敏感操作有确认；loading 精确到当前行；隐藏服务方法不再指向不存在路由 | Fixed |
| P-GAP-010 | 产品化 API 和运行时配置仍暴露 `mock/stub` 主命名，容易被集成方误解为未真实实现 | P2 | 软件可闭环 | 测试数据生成和 GPU-TEE factory 主路径仍使用 mock/stub 名称 | 正式 API 使用 synthetic/local/software 命名；旧 mock/stub 仅作为 deprecated/compatibility alias；返回体明确 synthetic 语义 | Fixed |
| P-GAP-011 | 发版前缺少可签署的安全产品设计复核、残余风险说明和发布门禁清单 | P0 | 软件可闭环 | runbook 有部署步骤，但缺 Go/No-Go、安全口径、P0/P1 门禁和发布后观察项 | 有独立安全复核文档和发版门禁清单，明确试点/生产边界、残余风险、证据要求和签署项 | Fixed |
| P-GAP-012 | 前端缺少面向运营/监管的安全态势披露，用户容易把软件 fallback 误解为硬件 TEE/HSM/链/SIEM 已真实启用 | P0 | 软件可闭环 | 安全能力分散在配置和文档中，页面没有统一展示 | 运营/监管/管理员可在工作台看到 TEE、GPU-TEE、HSM/Vault、链存证、SIEM、K8s policy、输出审查和 Debug 的状态、证据、建议动作和发版建议 | Fixed |
| P-GAP-013 | 沙箱会话证据分散在会话、策略、KMS、输出审查和审计表中，缺少可下载的客户/监管验收证明包 | P0 | 软件可闭环 | 底层有 attestation、session key、审计签名和输出签名，但没有统一接口和前端入口 | 会话详情可生成/下载 JSON 证明包，包含 policy hash、attestation quote hash、key id、输出签名、审计摘要、稳定 evidence hash 和本次 bundle hash，且不泄露密钥明文、原始输出或 raw quote | Fixed |
| P-GAP-014 | 管理员高危操作只做二次确认，缺少后端强制理由、工单号和可检索审计字段 | P0 | 软件可闭环 | KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换可直接调用 API | 高危 API 必须提交至少 8 字符 reason，可选 ticket_id；前端统一理由弹窗；审计日志记录 reason/ticket_id | Fixed |

---

## 3. 修复策略

1. 先修 `Active` 中的软件闭环 gap；当前阶段按要求只做编译/构建验证，单元测试和 e2e 在全部开发完成后统一执行。
2. 每修复一轮，更新本文件的 Gap 表和修复记录。
3. 对需要真实硬件或真实外部系统的 gap，不伪造成功；保留 hook/fallback，并列入环境验收。
4. 前端产品化修复以“实际运营可用”为准，避免只做静态展示。

---

## 4. 修复记录

### Round P1

已完成：修复 `P-GAP-001` 前端告警运营台。

- `cds-frontend/src/services/monitoringApi.ts` 对齐持久化告警字段，补充 `status`、`severity`、`alert_type`、`include_legacy` 筛选参数，以及 `acknowledgeAlert`、`resolveAlert` 处置接口。
- `cds-frontend/src/pages/Monitoring/index.tsx` 将旧审计告警列表升级为安全告警运营台：支持状态/等级/类型筛选，展示告警类型、等级、状态、发生次数、通知状态和详情抽屉，支持确认/解决告警并保留旧审计告警只读兼容。
- 编译验证：`cds-frontend npm run build` 通过。

### Round P2

已完成：修复 `P-GAP-003` 前端关键页面统一错误态/加载态/空状态。

- 新增 `cds-frontend/src/components/Feedback/QueryFeedback.tsx`，统一错误提示、重试入口、居中加载态、空态和表格空态。
- 角色工作台、数据目录、数据产品、合约列表/详情、沙箱会话、监控中心、输出审查管线均补充接口失败提示、重试动作和业务空态。
- 沙箱状态筛选切换时回到第一页，避免筛选后误显示空结果。
- 编译验证：`cds-frontend npm run build` 通过。

### Round P3

已完成：修复 `P-GAP-002` 产品化部署 runbook、环境变量、安全默认值和验收命令集中化。

- 新增 `specs/productization-deployment-runbook.md`，集中本地 Compose、243/K3s、生产 Helm、安全变量、回滚、编译级验证和统一测试验证步骤。
- 修复 `Dockerfile.api` 中不存在的 `ip netns` 包名，并补充 `curl`，确保镜像构建和 compose 健康检查可用。
- 修复 `.env.example`、`docker-compose.yml`、`helm/cds/templates/deployment-api.yaml`、`helm/cds/values.yaml` 的环境变量命名，统一使用后端实际读取的 `CDS_` 前缀。
- `docker-compose.yml` 补齐 MinIO 服务，避免对象存储默认端口误连 ClickHouse。
- `k8s/cds-app.yaml` 改为从 Secret 读取完整连接串和敏感配置，修正 CDC Kafka 变量名，并补齐前端 Deployment/Service。
- `k8s/deploy.sh` 和 `scripts/install-k3s.sh` 补齐 API + 前端部署；`install-k3s.sh` 支持将已有本地 `cds-api:latest`、`cds-frontend:latest` 镜像导入 K3s/K3d。
- 编译/配置验证：
  - `.venv/bin/python -m compileall -q app alembic` 通过。
  - `bash -n scripts/install-k3s.sh k8s/deploy.sh` 通过。
  - `docker compose config` 通过。
  - K8s、Compose、Helm values YAML 解析通过。
  - `helm template cds helm/cds` 未执行：当前机器未安装 `helm`。

### Round P4

已完成：复扫产品化剩余 gap，区分可代码闭环项与必须进入真实环境验收的项。

- 修复 `k8s/opa.yaml`，OPA 镜像从 `latest` 固定为与 Compose 一致的 `openpolicyagent/opa:0.68.0`。
- 修复 `helm/cds/values.yaml`，API/前端默认 tag 从 `latest` 改为 chart 当前版本 `0.1.0`，生产可通过 values 覆盖成实际发布版本。
- 新增 `policies/system.rego` 和 `policies/README.md`，避免 Compose 挂载空 OPA 策略目录；默认策略失败关闭，运行时合约策略仍由应用推送到 OPA。
- 配置验证：
  - `docker compose config` 通过。
  - K8s、Compose、Helm values YAML 解析通过。
  - `bash -n scripts/install-k3s.sh k8s/deploy.sh` 通过。

### Round P5

已完成：修复 `P-GAP-009` 前后端契约与页面交互脱节。

- KMS 和证书前端服务对齐后端真实路由：KMS 创建/撤销/审计使用 query 参数、DELETE 和 `/kms/audit`；证书列表、删除和验证使用 `/certificates/list`、DELETE、`/certificates/verify`。
- 连接器后端补齐详情与心跳接口，并统一注册、列表、详情、暂停、恢复、轮换 Key 的返回体；前端注册流程不再要求手输 API Key，状态按钮 loading 精确到当前行。
- 联邦后端补齐 trust 详情、目录同步状态/触发/条目接口，并新增真实信任评分接口；前端建立信任表单对齐后端字段，暂停/撤销和同步增加确认与行级 loading。
- 训练后端列表和取消接口返回完整任务对象，并补齐 `/training/jobs/{job_id}/audit`、`/training/jobs/{job_id}/checkpoints`；训练页移除会触发隐藏 404 的审计/检查点按钮，改为任务详情抽屉和可取消状态操作。
- 本轮按当前要求仅进行编译/构建验证，不跑单元测试和 e2e；`.venv/bin/python -m compileall -q app alembic` 通过，`cds-frontend npm run build` 通过。

### Round P6

已完成：修复 `P-GAP-010` 产品化命名与兼容层。

- 数据产品测试数据新增正式接口 `POST /data-products/{product_id}/test-data/synthetic`，返回体明确 `type=synthetic` 和 `synthetic=true`；旧 `/test-data/mock` 保留为 deprecated 兼容入口。
- 数据资源 schema 级生成新增正式接口 `POST /data-resources/generate-synthetic`；旧 `/generate-mock` 保留为 deprecated 兼容入口。
- GPU-TEE runtime factory 默认实现从 `stub` 改为 `local`，新增 `software` alias；`stub` 仅作为旧集成兼容别名，默认 singleton 使用本地软件加密运行时。
- 本轮按当前要求仅进行编译/构建验证，不跑单元测试和 e2e；`.venv/bin/python -m compileall -q app alembic` 通过，`cds-frontend npm run build` 通过。

### Round P7

已完成：修复 `P-GAP-011` 发版安全设计与门禁文档缺口。

- 新增 `specs/product-security-release-review.md`，从产品安全设计角度明确当前可发布能力、硬件/外部系统声明边界、核心安全边界、发版前证据、产品设计改进建议、残余风险和对外口径。
- 新增 `specs/release-gate-checklist.md`，提供 v0.1.0 发版签署模板、P0 阻塞门禁、P1 条件门禁、建议发版命令、发布说明必含项、Go/No-Go 规则和发布后 24 小时观察项。
- 更新本文件，将“安全产品设计复核和发布门禁清单”纳入产品化 gap 表并标记 Fixed。

### Round P8

已完成：修复 `P-GAP-012` 安全态势披露缺口。

- `app/api/monitoring.py` 新增 `GET /api/v1/monitoring/security-posture`，按配置和 TEE 探测结果返回 TEE、GPU-TEE、HSM/Vault、链存证、SIEM、K8s policy、输出审查和 Debug 状态，不返回密钥或 Secret 值。
- `cds-frontend/src/services/monitoringApi.ts` 补齐安全态势类型与接口。
- `cds-frontend/src/pages/Dashboard/index.tsx` 为监控读者增加“安全态势”卡片，显示 Go/Conditional Go/No-Go 建议和各能力证据/动作提示。
- 本轮按当前要求仅进行编译/构建验证，不跑单元测试和 e2e；`.venv/bin/python -m compileall -q app alembic` 通过，`cds-frontend npm run build` 通过。

### Round P9

已完成：修复 `P-GAP-013` 沙箱会话证明包缺口。

- `app/api/sandbox_sessions.py` 新增 `GET /api/v1/sandbox-sessions/{session_id}/proof-bundle`，聚合会话、合约策略、网络策略、attestation、KMS 元数据、输出审查摘要和审计事件摘要，并计算稳定 `evidence_hash` 与本次 `bundle_hash`。
- 执行路径新增 `sandbox.output_inspected` 审计摘要，只保存 report hash、released output hash、签名、水印、阻断状态和发现数量，不保存原始输出。
- `cds-frontend/src/services/sandboxApi.ts` 增加 `SessionProofBundle` 和 `getProofBundle()`。
- `cds-frontend/src/pages/Sandbox/SessionDetail.tsx` 增加证明包抽屉和 JSON 下载入口，展示证明级别、policy hash、quote hash、输出签名和审计事件数量。
- 本轮按当前要求仅进行编译/构建验证，不跑单元测试和 e2e；`.venv/bin/python -m compileall -q app alembic` 通过，`cds-frontend npm run build` 通过。

### Round P10

已完成：修复 `P-GAP-014` 管理员高危操作理由必填与审计缺口。

- 新增 `app/schemas/high_risk_operation.py`，统一 `reason` 和 `ticket_id` 请求体，其中 `reason` 至少 8 个字符。
- `app/api/kms.py`、`app/api/certificates.py`、`app/api/contracts.py`、`app/api/connectors.py` 对 KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换强制要求高危操作理由，并写入审计 detail。
- `cds-frontend/src/utils/highRiskOperation.tsx` 增加统一高危操作确认弹窗；KMS、证书、合约、连接器页面均改为输入理由/工单后提交。
- 更新已发现的相关测试请求，后续统一测试不会因接口契约变化缺少 reason。
- 本轮按当前要求仅进行编译/构建验证，不跑单元测试和 e2e；`.venv/bin/python -m compileall -q app alembic` 通过，`cds-frontend npm run build` 通过。

### 当前剩余项

无软件可闭环 Active gap。以下项目保留为真实环境验收，不通过代码编译阶段伪造完成：

- `P-GAP-004` 真实 TEE/GPU-TEE/HSM/联盟链/SIEM 集成验收。
- `P-GAP-005` K3s/K8s 真实集群 e2e 与 NetworkPolicy/FQDN/ResourceQuota 生效验证（W5 已补迁移基线，集群 e2e 仍待环境）。
- `P-GAP-006` 性能容量和长稳基线。
- `P-GAP-007` 安全认证/供应链证据包。
- `P-GAP-008` CI 流水线已上线（W7），真实集群 e2e 矩阵仍待 243 环境验收。

---

## 5. 产品化基线执行记录（CDS × CubeSandbox 对齐，P0 清零）

自 Round P10 之后，产品化能力对齐按 `docs/cubesandbox-parity-plan.md` 执行。P0 产品化基线（M1+M2）已全部落地，**软件可闭环的 P0 项清零**：

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M1**（Round 42） | W4 admin 认证治理 · W6 Git 卫生与密钥止血 · W1 可观测性（/metrics + Request-ID + 结构化日志） · W2 统一错误契约（错误码目录 + 408/409/410/429/503+Retry-After） | ✅ |
| **M2**（Round 43） | W3 Redis 分布式限流 + 租户配额持久化 · W5 Alembic 基线迁移（31 表 + 对拍脚本） · W7 CI 流水线（backend/frontend/secrets） | ✅ |

**本轮（Round 43 = M2）关键产出（详见 parity 方案 §八 Round 43）：**
- W3：`RateLimitMiddleware` Redis 固定窗口（auth/user/ip 三作用域，fail-open 降级）；租户配额 Redis hash 持久化 + `rebuild_tenant_quotas` 恢复路径；新增 `test_rate_limit_distributed.py`(14) / `test_tenant_quota_persistence.py`(9)。
- W5：`alembic/versions/0000_baseline_all_tables.py`（链首基线）→ 空库 `upgrade head` 建 31 表；`scripts/verify_schema_parity.py` 双库对拍；`docs/migrations.md`；新增 `test_migrations.py`(4)。
- W7：`.github/workflows/ci.yml` 分层门禁；`ci/known-failures.md` 基线（2319 passed/7 failed/17 errors）；`pyproject.toml` timeout=600。
- 验证：新测试 + 核心回归 **184 passed**；`compileall` 通过；`alembic upgrade head`/`check`/parity 全绿。

**P1（W8 分页 / W9 空闲自动暂停 / W10 WS 流 / W11 异步操作 / W12 保留 GC / W13 出站审计 / W14 节点运维）与 P2 路线图仍待后续轮次。**

> **2026-09-07 更新**：新一轮全量复扫（Round 44 之后，CubeSandbox 对齐 W1–W19 完成）发现若干此前未登记的代码级缺陷（生产 compose YAML 解析失败、Helm 拓扑失真、五处半接线/死代码、MPC 无持久化、后端-only 路由无前端/SDK 面）及能力缺口（推理服务沙箱、RAG 二期、智能体框架、HE/MPC、K8s client 化）。Round 45+ 的新一轮产品化方案见 **`specs/sandbox-productization-round3-spec.md`**（含扫描结论、差距矩阵、P0/P1/P2 任务卡与环境验收轨道）。
>
> **mock/模拟维度**（2026-09-07 补充）：全部"宣称实现实为模拟/回退/死代码"路径的逐项问题分析与真实化实现计划见 **`docs/mock-remediation-plan.md`**（M-01..M-28 清单 + MR-A1..A15/MR-B1 任务，含 SM4-GCM 真后端、SFT 真实训练、DP 管线接线、证明白名单强制、链存证持久化、mTLS 强制等）。P-GAP-004/005 的软件前置项已在本文件登记。
