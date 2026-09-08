# CDS 沙箱产品化第三轮（Round 45+）· 功能扫描结论与实现方案

> 日期：2026-09-07
> 基线：Round 32–44 全部完成——PPT 安全闭环（T1–T12）、141 个软件 gap 清零、CubeSandbox 平台对齐（W1–W19）。
> 测试基线：**2420 passed / 0 failed / 3 skipped**（2026-09-07，排除 e2e 集群文件）。
> 扫描方法：①文档层——`specs/SS-gap-analysis.md`（FG 表）、`specs/productization-gap-analysis.md`（P-GAP 表）、`docs/ai-sandbox-gap-remediation-plan.md`、`docs/cubesandbox-parity-plan.md` 执行记录全量比对；②代码层——32 个 API router、~110 个 service、alembic 迁移链（0000→0007）、前端 19 个 service×22 页、SDK、compose/helm/k8s/CI 全量审计（双 explore agent 并行）；③关键断言人工复核——`docker-compose.prod.yml` 实际 YAML 解析、alembic 全迁移表集 diff、死代码 grep（调用方/导入方/测试引用三向验证）。
> 性质：可执行落地方案。实施时若与现状冲突，以代码为准并回写本文件（沿用既有纪律）。
> 前序：本方案**不重复** Round 32–44 已完成范围；聚焦"从沙箱角度尚未正式产化"的功能。
> 配套：**`docs/mock-remediation-plan.md`**（2026-09-07）——全部 mock/模拟路径的逐项问题分析与真实化实现计划（MR-A1..A15 / MR-B1，另含 SM4-GCM 真后端、SFT 真实训练、DP 管线接线等 round3 spec 未覆盖的 S4 类伪实现修复）。本 spec 的 N-XX 与 MR-XX 交叉引用处不重述。

---

## 一、扫描结论：未产化功能全景

### 1.1 已产化基线（执行 agent 禁止重复实施）

- **安全闭环**：合约终止级联回收、会话生命周期调度、dev 沙箱合约门禁、KMS 证明强制 + wrapped keys 持久化、输出网关强制路径（409 门禁）、合约用途限定、字段级分类分级、DP 护栏、模拟/降级显式化开关矩阵（`validate_security_config` 生产 fail-closed）。
- **平台运营**：/metrics + Request-ID + 结构化日志、统一错误契约（`docs/error-codes.md`）、Redis 分布式限流 + 配额持久化、Alembic 基线链 + parity 门禁、GitHub Actions CI（三 job）、游标分页、空闲自动暂停（pause+auto_resume 重验合约）、WebSocket 流式执行（L0）、异步操作模型（202+operation）、保留 GC janitor、egress JSONL 审计（SM4-GCM 加密可选）、节点 isolate/stale/offline 运维、共享卷（L0 bind）、多副本任务队列、Helm 完备化（HPA/PDB/securityContext/topologySpread）。
- **沙箱可用性**：会话文件 API、快照/回滚、暂停/恢复/续期、exec/logs/usage、会话模板、脱敏下载、快照 GC、Python SDK（会话面）、前端会话工作台（xterm 终端）。
- **RAG 一期**：语料不出域的抽取式检索问答（TF 嵌入 + 加密语料 + runner 字节级校验）。

### 1.2 未产化功能清单（本轮范围）

#### A 类 · 沙箱执行通道（runtime 层）

| # | 功能 | 现状（证据） | 产化差距 |
|---|------|------|------|
| A1 | **L1 TEE 硬件路径** | `remote_attestation.py` 全部软件模拟（本地静态根 `cds-local-attestation-root-v1`）；硬件仅外部 CLI 命令钩子（`CDS_TEE_HARDWARE_*_CMD`）；`tee_simulator.py` 占位进程 | 无 Gramine/CoCo/Occlum 进程内适配；无 Intel PCS/AMD KDS 外部信任根；`AttestationPolicy.allowed_measurements` 默认空（=FG-001，硬件门禁项） |
| A2 | **GPU-TEE 训练运行时** | `gpu_tee_runtime.py` `LocalSoftwareGPUTeeRuntime = GPUTeeRuntimeStub`（CPU 模拟器） | 无 NVIDIA CC 真实路径（=FG-002）；无 `AccelerationRuntime` 插件扩展点（P2-6） |
| A3 | **L2 Firecracker 真实通道** | `firecracker_runtime.py` 探测 firecracker→qemu-tcg→不可用时**回退 simulation（直接子进程，无隔离）** | 无 guest-agent/virtio-fs 执行通道（=FG-003）；回退语义已诚实标注但非产化形态 |
| A4 | **K8s 运行时产化** | `k8s_sandbox.py` 经 kubectl 子进程（`k8s_sandbox.py:147-149`） | 未用官方 Python client（D3）；真实集群 e2e 未固化（=FG-004） |
| A5 | **非 L0 流式交互** | WS exec stream 仅 L0，其余 `STREAM_UNSUPPORTED`（`session_stream.py:161-165`） | K8s Pod exec 天然可流式（kubectl exec -i），未接线 |
| A6 | **共享卷多级支持** | `resolve_binds` 仅 L0 bwrap 路径；L1/L2/L3 诚实 unsupported；`size_limit_mb` 登记性元数据 | K8s PVC 卷未接（W15 边界）；无 fs quota 执行 |
| A7 | **快照性能优化** | tar.gz 拷贝式快照（copy-snapshot） | overlayfs copy-up 增量快照留 P2（前序方案已登记） |

#### B 类 · 沙箱上层的 AI 计算能力（产品场景缺口）

| # | 功能 | 现状（证据） | 产化差距 |
|---|------|------|------|
| B1 | **推理服务沙箱 / 隐私推理**（设计场景五："API 服务沙箱（在线推理）"，`product-design.md:242-256`、Phase 3 路线） | **ABSENT**：全部 32 个 router 无 `/inference` 端点；`gateway.py` metering 仅覆盖数据查询，无按 token/次数的推理计量 | 设计七类沙箱中"API 服务沙箱"完全缺失：无受控推理出口、无推理计量计费、无推理输出保护闭环（=gap review C5 + T11 二期衔接） |
| B2 | **RAG 二期（生成式）** | 一期抽取式已上线（`rag_service.py` `answer_mode="extractive_retrieval"`）；transformers 仅宿主侧实验后端且 `build_corpus` 对其 fail-closed | 沙箱内生成式 LLM、transformers 沙箱侧检索、模型水印/MIA 门禁、推理计量（T11 二期清单） |
| B3 | **智能体执行框架** | `cdc_agent.py` 是 CDC 数据同步（Debezium/Kafka），非 AI 智能体（gap review C4） | 供方/需方智能体运行时（沙箱内受限执行 + 工具白名单 + 全程审计）ABSENT；命名易误导 |
| B4 | **同态加密 / MPC 计算** | `mpc_service.py` 仅 Shamir 秘密分享（split/reconstruct），**且纯内存态**（无 DB 模型/表，重启即丢） | 无 HE/两方 MPC 计算协议（SecretFlow HEU/SPU 评估，P2-1）；密钥托管态无持久化 |
| B5 | **训练编排产化** | CPU torch 真实训练 + `TRAINING_REQUIRE_TORCH` fail-closed；模拟路径诚实标注 | 真实分布式训练平台/GPU 调度未接（=FG-011，环境验收） |
| B6 | **非结构化生产引擎镜像** | 脚本级真实（OCR/ASR/ffmpeg/DICOM 脚本 + 失败重试 + partial_success） | 生产镜像内引擎可用性未验证（=FG-010，环境验收） |

#### C 类 · 本轮扫描新发现的代码级缺陷（未登记于任何 tracker）

| # | 缺陷 | 证据 | 影响 |
|---|------|------|------|
| C1 | **`docker-compose.prod.yml` YAML 解析失败** | 第 113 行 `CDS_LOG_JSON: "true"` 缩进错误（2 空格，应为 6）；`yaml.safe_load` 实测报错 `line 115, column 7` | **生产 compose 部署路径整体不可用**（Round 36 引入的回归）；CI 无部署文件语法门禁故未拦截 |
| C2 | **Helm chart 拓扑与 values 不一致** | `values.clickhouse.enabled=true` 但无任何 ClickHouse 模板；OPA Service（selector `cds-opa`）无对应 Deployment（悬空）；Bitnami postgresql/redis 为声明依赖未 vendor（需 `helm dependency build`） | 按价值观装即缺组件；values 高估拓扑 |
| C3 | **半接线/死代码五处** | ①`chain_attestation.py` 服务无调用方、`chain_attestations` 表仅 `scripts/chain-attestation-ddl.sql` 手工建（不在 alembic/模型）；②`merkle_pipeline.py` 单例从未 `.start()`（审计走 `merkle_service` 同步路径）；③`STREAMING_PROXY_ENABLED`（`config.py:234`）无消费方；④`DockerAdapter`（`sandbox_runtime.py`）定义未注册；⑤`app/models/key_metadata.py` 无导入方（已被 `kms.py` 别名取代） | 维护负担 + 审计/集成方误解；违反项目"诚实标注"纪律 |
| C4 | **MPC 纯内存态** | `mpc_service.py:85-88` dict 存储，无模型/迁移 | 重启丢密钥份额；API 契约暗示持久但无持久化 |
| C5 | **前端/SDK 产品面缺口** | 后端-only 路由无 UI：compliance、data_pipeline、mpc、sandbox_db、sandbox_nodes、shared_volumes、network_policies、gateway、rag（`ragApi.ts` 存在但无页面消费）；SDK 仅覆盖 sandbox-sessions（~30 个 router 之 1，仅 Python）；前端 vitest 3 文件未入 CI（CI 只跑 build） | 已实现且测试过的能力对用户不可见 |
| C6 | **认证产品化缺口** | 登录=用户名/密码 + HS256 JWT；无 MFA、无 SM2 证书登录（`spec-implementation-analysis.md` 登记为 ❌） | 面向监管/等保的认证强度不足 |
| C7 | **k8s/ 明文开发密钥 + Vault 弱初始化** | `k8s/secrets.yaml` 硬编码；`vault-init.sh` 单 share/单 threshold，root token 回写 k8s Secret | dev 可用，不可产线（需在 runbook 显式声明边界） |

#### D 类 · 环境/流程验收（不在本轮软件范围，列验收轨道）

FG-005 真链 e2e、FG-007 生产 HSM/Vault、FG-008 联邦 e2e、FG-009 SIEM/邮件/短信、FG-012 性能长稳、FG-013 备份恢复演练、FG-014 前端 QA/a11y、FG-015 渗透/供应链、FG-016 CI/e2e 矩阵（见 §六）。

### 1.3 明确不做项及理由（执行 agent 禁止实施）

| 不做项 | 理由 |
|---|---|
| E2B SDK 兼容 / envd in-VM agent / OCI 模板构建 / Template Store | 沿用 `cubesandbox-parity-plan.md:53-62` 裁决（CDS 是合约治理密态沙箱，非通用算力沙箱） |
| 跨节点 pause/resume（S3 快照外置） | 依赖对象存储快照外置，远期（本 spec N13 仅登记） |
| 伪造 TEE/GPU/真链/HSM 硬件实现 | 硬件门禁项必须环境验收（项目铁律）；软件侧只做接线与诚实披露 |
| filter-repo 历史清洗 / 密钥轮换执行 | W6 遗留**用户决策项**，不属 agent 自主范围 |

---

## 二、差距矩阵（本轮任务映射）

| # | 能力 | 差距 | 任务 | 优先级 | 类型 |
|---|---|---|---|---|---|
| C1 | 生产部署阻断 | compose prod 不可解析 | N1 | **P0** | 软件闭环 |
| — | 部署文件回归防护 | CI 无 YAML/模板语法门禁 | N1 | **P0** | 软件闭环 |
| C3 | 遗留半接线/死代码 | 五处处置 | N2 | **P0** | 软件闭环 |
| C4 | MPC 无持久化 | DB 模型 + 迁移 + 服务持久化 | N3 | **P0** | 软件闭环 |
| C2/C7 | helm/k8s 拓扑失真 | chart 模板补齐或显式 external + dev 边界文档 | N4 | **P0** | 软件闭环 |
| B1 | 推理服务沙箱 | 端点 + 计量 + 输出保护 | N5 | **P1** | 软件闭环 |
| B2 | RAG 二期 | 生成式 + 沙箱侧嵌入 + 水印/MIA | N6 | **P1** | 软件闭环 |
| A4/A5 | K8s 运行时产化 | Python client + 流式 + PVC 卷 | N7 | **P1** | 软件闭环（e2e 部分环境验收） |
| C5 | 前端运营面 | nodes/compliance/rag 等页面 | N8 | **P1** | 软件闭环 |
| C5 | SDK 扩面 | auth/contracts/tasks/proof-bundle | N9 | **P1** | 软件闭环 |
| B3 | 智能体执行框架 | 沙箱内 agent 运行时 | N10 | P2 | 软件闭环（立项另估） |
| B4 | HE/MPC 计算 | SecretFlow 评估集成 | N11 | P2 | 依赖许可/部署评估 |
| A2/A7 | GPU 插件 / overlayfs 快照 | 扩展点 + 优化 | N12/N13 | P2 | 路线图 |
| C6 | MFA / SM2 证书登录 | 认证增强 | N14 | P2 | 软件闭环 |
| A1/A3/B5/B6/D 类 | 硬件/外部系统/长稳 | 验收轨道 | §六 | 持续 | 环境验收 |

---

## 三、P0 · 产化阻断修复（4 项 · 约 1 周 · 5 人日）

### N1 · 生产部署文件修复 + 语法门禁（修 C1）

**目标**：`docker-compose.prod.yml` 可解析；同类回归被 CI 拦截。

**代码现状**：第 113 行 `CDS_LOG_JSON: "true"` 缩进 2 空格（应为 6）；`python -c "import yaml; yaml.safe_load(...)"` 实测失败于 line 115 col 7。Round 36 加 `CDS_LOG_JSON` 时引入。

**实现步骤**：

1. 修复缩进：`CDS_LOG_JSON: "true"` 归位 `cds-api.environment` 块（6 空格）。
2. 新建 `tests/test_deploy_manifests.py`（进 CI 门禁集）：
   - `yaml.safe_load` 解析 `docker-compose.yml` / `docker-compose.prod.yml` / `docker-compose.middleware.yml` / `docker-compose.blockchain.yml` / `helm/cds/values.yaml` / `k8s/*.yaml`（全部 0 exit）；
   - 断言 prod compose 关键 fail-closed 键在位（`CDS_DEBUG="false"`、`CDS_KMS_REQUIRE_ATTESTATION="true"`、`CDS_SECCOMP_FALLBACK_ALLOWED="false"`、`CDS_HSM_SOFTWARE_FALLBACK_ALLOWED="false"`），防回归；
   - 断言 prod compose 的 service 键集合与 dev compose 一致（拓扑不漂移）。
3. `.github/workflows/ci.yml` backend job 把该文件加入门禁测试列表。

**测试**：`pytest tests/test_deploy_manifests.py -v`（修前红/修后绿）。
**工时**：0.5 人日

### N2 · 遗留半接线/死代码处置（修 C3）

**目标**：五处遗留物各有显式归宿（接线或移除），零"沉默存在"。

**处置表**（默认方案，实施时可经用户确认改选）：

| 遗留物 | 默认处置 | 理由 |
|---|---|---|
| `chain_attestation.py` + `scripts/chain-attestation-ddl.sql` | **移除**（git 可回溯），`test_p0_security_gaps.py:291` 的 glob 断言同步更新 | 无调用方、表不在 alembic；TLCP 链上存证应随 FG-005 真链一并设计，而非半成品共存 |
| `merkle_pipeline.py`（含 `tests/test_merkle_pipeline.py`） | **保留 + 显式接线**：`MERKLE_PIPELINE_ENABLED: bool = False`（默认关），lifespan 按开关 start；开启时文档标注"实验性异步批量模式" | 审计批量异步化是合理演进方向；测试已存在；默认关=零行为变化 |
| `STREAMING_PROXY_ENABLED`（config.py:234） | **移除配置键**，流式代理属外部 mitmproxy 组件，在 runbook 部署节说明 | 无消费方的死键违反配置四同步纪律 |
| `DockerAdapter`（sandbox_runtime.py） | **移除类定义**（grep 确认无引用；`test_p0_security_gaps.py` 若引用同步更新） | L3 由 BwrapAdapter 承担，未注册的适配器徒增误解 |
| `app/models/key_metadata.py` | **移除文件**（无导入方；`kms.py:140` 的 `KeyMetadata = DataEncryptionKey` 别名保留） | 双模型同名是 Round 32 已确认的遗留（P1-10 去重），死文件留存误导审计 |

**测试**：`pytest tests/test_p0_security_gaps.py tests/test_merkle_pipeline.py tests/test_audit*.py -v`；`python -c "from app.core.config import settings"` 冒烟（无死键后配置加载正常）。
**工时**：1.5 人日

### N3 · MPC 秘密分享持久化（修 C4）

**目标**：Shamir 密钥托管跨重启可用；诚实边界文档化（现状=秘密分享托管，非 MPC 计算协议）。

**代码现状**：`mpc_service.py:85-88` 三个内存 dict；`mpc.py` 7 端点直接读写；无 DB 模型。

**实现步骤**：

1. 新建 `app/models/mpc_key.py`：`mpc_keys` 表（`key_id UUID PK`、`threshold int`、`share_count int`、`status String(16)`（active/rotated/destroyed）、`created_at`、`rotated_at`）+ `mpc_key_shares` 表（`share_id UUID PK`、`key_id FK`、`share_index int`、`share_value LargeBinary`（SM4-GCM 加密后）、`holder_id String(255)`）；进 `app/models/__init__.py`。
2. Alembic 迁移 `0008_mpc_persistence`（additive 新表）。
3. `mpc_service.py` 改造：split 结果落库（share 用现有 `SM4Cipher` 封装）；reconstruct/verify/rotate 从 DB 读；destroy 标记状态并 crypto-erase `share_value`。
4. API 层（`mpc.py`）行为不变（纯增量，响应体加 `persisted: true`）。
5. **诚实边界**：`sdk/README.md` 或 `docs/error-codes.md` 旁注：`mpc_service` 为 Shamir 秘密托管；HE/MPC 计算见 N11（P2）。前端无页面（C5 属 N8 范围，本轮不动）。

**测试**（新建 `tests/test_mpc_persistence.py`）：split→新服务实例（模拟重启）→reconstruct 一致；destroy 后 reconstruct 失败；threshold 不足失败（回归既有语义）。
**工时**：2 人日

### N4 · Helm/K8s 拓扑对齐（修 C2/C7 软件面）

**目标**：chart 渲染产物与 values 声明一致；dev 密钥路径有产线替代说明。

**实现步骤**：

1. **ClickHouse 二选一（默认外部化）**：`values.clickhouse.enabled` 改注释说明"外部 ClickHouse，经 `api.env.CDS_CLICKHOUSE_URL` 指向"；若保留 enabled 键则必须在 `templates/` 增加 deployment+service（推荐外部化，与 OPA 同法）。
2. **OPA 悬空 Service 处理**：`service.yaml` 中 `-opa` Service 保留但 values 注释明确" expects externally-managed OPA deployment matching selector"，或提供 `opa.external.url` 直连模式（默认后者：`CDS_OPA_URL` 直接可配外部地址，删除悬空 Service）。
3. **依赖 vendoring**：`Chart.yaml` dependencies（bitnami postgresql/redis）加注释说明 `helm dependency build` 前置步骤，写入 runbook 部署节；或 vendor 进 `charts/`（若许可允许，默认注释+文档）。
4. **k8s/ dev 密钥边界**：`k8s/secrets.yaml` 顶部加显著注释"DEV ONLY — 生产必须 existingSecret/Vault"；`specs/productization-deployment-runbook.md` 增加"K8s manifests 与 Helm chart 的密钥边界"一节（manifests 路径仅 dev/243）。
5. `tests/test_helm_chart.py` 扩展：断言 values 中每个 `*.enabled=true` 的组件要么有模板要么在"外部组件白名单"注释中（防再漂移）。

**测试**：`pytest tests/test_helm_chart.py tests/test_deploy_manifests.py -v`；有 helm 的环境 `helm template cds helm/cds` + `helm lint`（环境验收项）。
**工时**：1 人日

---

## 四、P1 · 沙箱核心能力产化（5 项 · 约 4-6 周 · 30-38 人日）

### N5 · 推理服务沙箱一期（修 B1 · 设计场景五落地）

**目标**：训练产物有受控推理出口——"API 服务沙箱"最小闭环：模型不出沙箱、输入经审查、输出过网关、调用可计量。

**设计决策（对齐既有架构，避免新造）**：

- **复用 RAG runner 模式**：模型以加密产物随会话分发（同 `prepare_corpus_for_task` 的 DEK 重加密物化模式），推理全部在 sandbox runtime 内执行——不新增暴露面。
- **复用 session/task 生命周期**：推理任务走 `TaskType.INFERENCE` + task_pipeline，输出强制过 `build_output_policy`（T5 既有门禁），零新审查路径。
- **计量挂 gateway 模式**：`gateway.py` 已有 `/metering` 先例，推理计量新增 token 维度。

**实现步骤**：

1. 模型注册：`app/models/trained_model.py`（`model_id`、`task_id`（来源训练任务）、`name`、`format`（onnx/pickle/safetensors 白名单）、`artifact_ref`（加密存储 key）、`size_bytes`、`status`（registered/revoked））+ 迁移 `0009_trained_models`。
2. 模型上传/注册端点（`app/api/inference.py` 前身或并入 training.py）：训练任务完成后可注册产物（checkpoint → 模型产物，加密落 storage_service）；或供方上传（过 code_scanner 同款扫描）。
3. 推理端点（新 `app/api/inference.py`，prefix `/api/v1/inference`）：
   - `POST /models`（注册，供方）、`GET /models`（买方可合约内可见）、`POST /{model_id}/invoke`（买方，body：input payload + purpose 声明）。
   - invoke 链：合约校验（覆盖该模型对应产品 + purpose 门禁，复用 T6）→ 创建 session（或复用常驻 warm session，`idle_policy=pause` 天然适配 W9）→ task_pipeline 提交 `INFERENCE` 任务 → runner 在沙箱内加载模型执行 → 输出过网关（脱敏/行数/水印）→ 返回。
4. 推理 runner：`app/services/inference_runner.py`——一期限定 ONNX（`onnxruntime` CPU 可跑，无 torch 依赖）+ 结构化输入输出（JSON in/out）；模型加载与执行均在 workspace 内；诚实降级：无 onnxruntime 时任务失败（fail-closed，不模拟）。
5. **计量计费**：`inference_usage` 表（model_id、contract_id、user_id、input_tokens/requests、output_rows、ts）+ `GET /inference/metering`（运营/供方）；DP 输出叠加时记录 epsilon 消耗（复用 dp_budget）。
6. 模型水印衔接：注册时若来源训练任务含 watermark_records，invoke 输出可选携带水印标记字段。
7. 前端：TrainingDashboard 增加"注册为推理模型"动作 + 简单 invoke 测试面板（visual-engineering，独立验收）。
8. SDK：`invoke_model()` + `wait_for_result()`。

**测试**（新建 `tests/test_inference_service.py` 等 3 文件）：注册→invoke→输出过网关（PII 阻断 409）；无合约 invoke 拒绝；metering 计数正确； revoked 模型拒绝；模型损坏 fail-closed。
**工时**：8 人日（后端 6 + 前端 2）
**诚实边界**：一期 CPU ONNX、单会话串行推理；GPU 推理与弹性副本属 N12/环境验收。

### N6 · RAG 二期（修 B2）

**目标**：检索质量升级（transformers 嵌入进沙箱）+ 生成式答案（沙箱内 LLM 钩子）+ 水印/MIA 门禁。

**实现步骤**：

1. **嵌入后端二选一进沙箱**：`rag_embedding.py` 的 transformers 后端从"宿主侧实验"转为"沙箱内可复现"——模型文件随语料同法加密分发（runner 解密加载）；`build_corpus` 解除对 transformers 的 fail-closed（改为校验模型哈希一致即可复现）；`auto` 仍解析为 tf（保守默认），`PII_NER_ENGINE` 同款配置风格：`RAG_EMBEDDING_ENGINE: auto|tf|transformers`。
2. **生成式答案模式**：`answer_mode` 增加 `generative`——runner 内 LLM 推理钩子（一期限定 ONNX 格式小模型，与 N5 runner 复用加载器）；无模型时端点 400（诚实拒绝，不静默回退抽取式）。
3. **水印/MIA 门禁**：generative 输出走 `output_policy` 时叠加水印（复用 LSB/文本水印服务）；训练侧 MIA 结论（`mia_status`）在模型注册（N5）时携带，`not_evaluable` 时推理侧披露该事实。
4. **检索计量**：`rag_query` 任务落 `resource_usage` 增加检索条数/耗时维度（喂监控）。

**测试**：嵌入一致性（host/runner 哈希校验）；generative 无模型 400；水印存在性验证；tf→transformers 语料重建迁移路径。
**工时**：6 人日
**依赖**：N5 的模型分发/加载器复用（先 N5 后 N6）。

### N7 · K8s 运行时产化（修 A4/A5，A6 部分）

**目标**：K8s 适配器进程内化（去 kubectl 子进程）+ K8s 路径获得流式与卷能力。

**实现步骤**：

1. **Python client 化**：`requirements.txt` 加 `kubernetes>=29`；`k8s_sandbox.py` 的 kubectl 子进程调用全部替换为官方 client（`CoreV1Api.create_namespaced_pod` / `connect_get_namespaced_pod_exec` 等）；kubeconfig 加载沿用现有逻辑；无集群时保留现有"不可用"诚实降级。
2. **流式 exec（K8s）**：`K8sRuntimeAdapter.execute_streaming`——`pod_exec` 的 WebSocket 流逐行回调，接 W10 既有帧协议（复用 `session_stream.py` 的 DLP 行审查链，仅 L0 限制放开为 L0+K8s）；`STREAM_UNSUPPORTED` 语义收敛到 L1/L2。
3. **PVC 共享卷（K8s）**：`shared_volumes` attach 解析支持 K8s 适配器——会话 Pod spec 增 `volumeMounts`（PVC `cds-shared-<name>`，RO 粒度按 attach 配置）；卷创建时 `ensure_pvc`（`ReadWriteMany` 需集群存储类，values 注明依赖，缺存储类时诚实报错）；L1/L2 仍 unsupported。
4. 集群 e2e 脚本固化：`scripts/e2e-k8s.sh`（install-k3s → deploy → 跑沙箱生命周期 + 流式 + 卷用例）——真实执行留环境验收（FG-004），脚本先行。

**测试**：单测层用 client mock（现有 test_k8s*.py 模式扩展）；流式协议复用 `test_exec_stream.py` 参数化 runtime。
**工时**：6 人日
**诚实边界**：无集群环境时全部走既有降级与 mock 单测；真实渲染/e2e 归 FG-004。

### N8 · 前端运营面补齐（修 C5）

**目标**：后端已实现且已测试、但无 UI 的能力获得运营入口。

**范围（按运营价值排序，一期 4 页）**：

1. **节点管理页**（ADMIN/OPERATOR）：`/nodes`——`sandboxApi` 扩展 nodes 端点（list/isolate/unisolate + health_state/scheduling_disabled 展示 + 高危操作理由弹窗复用 `highRiskOperation.tsx`）。
2. **合规报告页**（COMPLIANCE/ADMIN）：`/compliance`——complianceApi（`/compliance/reports`、`/reports/quick`）+ 报告下载。
3. **RAG 语料管理页**（PROVIDER）：`/rag`——消费已有 `ragApi.ts`（corpus 摄入 + 任务状态）；零后端改动。
4. **共享卷管理页**（PROVIDER/OPERATOR）：`/volumes`——CRUD + attach/detach。

**实现**：全部走既有 visual-engineering 纪律（QueryFeedback 空错态、行级 loading、角色路由 ROLE_GROUPS 扩展、Sidebar 菜单）；mpc/sandbox_db/gateway/data_pipeline 页面留 P2（对应能力本就 PARTIAL/场景窄）。

**测试**：`npm run build` + vitest（api 层 mock）+ 手工验收；CI 前端 job 增加 `npm test`（vitest 入 CI，3 文件已有）。
**工时**：5 人日（visual-engineering 委托）

### N9 · SDK 扩面（修 C5）

**目标**：SDK 从"会话专用"扩到主链路闭环：登录→合约→任务→证明包。

**实现**：

1. `sdk/cds_sdk/client.py` 增加：`login()/auth`（token 管理 + 自动 refresh）、contracts（list/get/sign_data 提交/terminate——复用 W2 `CDSApiError`）、tasks（create/get/result——含 RAG/INFERENCE 类型）、`get_proof_bundle()`、inference（`invoke_model`，N5 同步）。
2. README 能力表更新 + 4 个新示例（买方全流程：登录→查目录→签合约→建会话→提交任务→取脱敏结果→下载证明包）。
3. `tests/test_sdk_client.py` 扩展（MockTransport 覆盖新方法 + 429/410 重试语义）。

**工时**：3 人日

---

## 五、P2 · 路线图（立项另估）

| # | 方向 | 路线 | 前置 |
|---|---|---|---|
| N10 | **智能体执行框架**（B3） | 沙箱内受限 agent 运行时：工具白名单（复用 code_scanner + 网络策略）+ 逐步审计 + 会话级 token/步数预算；`cdc_agent` 加模块 docstring 澄清 CDC 语义 | N5 任务类型扩展机制 |
| N11 | **HE/MPC 计算**（B4） | SecretFlow（HEU 统计 + SPU 两方）评估→集成；`mpc_service` 保留托管语义（N3 后已持久化） | SecretFlow 许可与部署评估 |
| N12 | **GPU 加速 runtime 插件** | `AccelerationRuntime` 扩展点（runtime 可插拔已有）；NVIDIA CC 接入对齐 FG-002 | FG-002 硬件 |
| N13 | **overlayfs 快照 + 跨节点恢复** | L0 overlayfs copy-up 增量快照；S3 外置快照支撑跨节点 pause/resume | 对象存储 |
| N14 | **认证增强**（C6） | MFA（TOTP）+ SM2 证书登录（证书体系已真实，双轨认证） | — |
| N15 | **前端二期页面** | mpc/sandbox_db/gateway/data_pipeline 页 + 角色设计文档剩余页（用户管理/买方工作台） | N8 交付后评估价值 |
| N16 | **JS SDK** | TypeScript SDK（对齐 cds_sdk 面） | N9 |

---

## 六、环境验收轨道（硬件/外部系统门禁 · 持续跟踪不伪造）

本轮软件任务与既有 FG 项的衔接关系（完整 FG 清单与验收标准见 `specs/SS-gap-analysis.md` FG 表，此处只列增量）：

| FG | 内容 | 本轮贡献 | 就绪后动作 |
|---|---|---|---|
| FG-001 TEE 硬件 | Gramine/CoCo + 外部信任根 | 无（禁伪造）；`ALLOW_SIMULATION` 披露已就绪 | `_provision_hardware` 适配 + measurement 白名单强制 |
| FG-002 GPU-TEE | NVIDIA CC | N12 扩展点预研 | 真实 attestation 接线 |
| FG-003 Firecracker | guest-agent 通道 | 无 | L2 通道实现（本 spec 未排期，硬件就绪后立项） |
| FG-004 K8s 集群 | 真实 e2e | **N7 交付 e2e 脚本 + client 化** | 集群执行 `scripts/e2e-k8s.sh` 固化进 CI |
| FG-005 真链 | FISCO e2e | 无（backend 诚实披露已就绪） | bcos3sdk 适配 |
| FG-008/009/012/013/015/016 | 外部系统/长稳/渗透/CI 矩阵 | N1 部署门禁是 FG-016 前置 | 环境到位后执行 |
| W6 遗留 | 密钥轮换执行 + filter-repo | **用户决策项**（本 spec 重申，不代决） | 用户批准后执行 |

---

## 七、横切面

### 7.1 配置开关矩阵（新增）

| 字段 | 默认 | dev/test | 生产 | 任务 |
|---|---|---|---|---|
| `MERKLE_PIPELINE_ENABLED` | False | 同 | False（实验性，按需） | N2 |
| `RAG_EMBEDDING_ENGINE` | auto（→tf） | 同 | 同 | N6 |

原则沿用：**安全默认 fail-closed，宽松只在显式配置**；四处同步（`.env.example` / `docker-compose.yml` / `docker-compose.prod.yml` / `helm/cds/values.yaml`）。N5/N6 若引入模型目录、推理超时等键，随任务同步登记。

### 7.2 Alembic 迁移清单（依赖顺序）

| 迁移 | 内容 | 任务 |
|---|---|---|
| 0008 | 新表 `mpc_keys` + `mpc_key_shares` | N3 |
| 0009 | 新表 `trained_models` | N5 |
| (0010) | `inference_usage`（若不并入 0009） | N5 |

全部 additive 新表，存量零迁移风险；每迁移后 `python scripts/verify_schema_parity.py`。

### 7.3 每任务 DoD（沿用既有模板，逐项自检）

1. 新测试全绿（方案列出的每条用例）；2. 受影响回归不低于基线；3. `compileall` 全绿；4. 配置四同步；5. 降级/模拟路径 `logger.warning` + `validate_security_config` 覆盖；6. API 行为变更回写本文件执行记录；7. 禁项自检（无裸 `except: pass` 新增、不删测试、不放宽 W2/T5 门禁）。
8. **新增（本轮）**：部署文件改动必须过 `tests/test_deploy_manifests.py`（N1 交付后）。

### 7.4 发布顺序与依赖

```
N1 (compose 修复+门禁) ─┐
N2 (死代码处置)          ├─→ P0 收口：全量回归 → 发版
N3 (MPC 持久化)          │
N4 (helm 拓扑)           ┘
── P1 ──
N5 (推理服务) → N6 (RAG 二期，复用 N5 加载器)
N7 (K8s client+流式+卷) ∥ N8 (前端 4 页) ∥ N9 (SDK 扩面)
```

### 7.5 里程碑与工时

| 里程碑 | 内容 | 时点 |
|---|---|---|
| M1 | P0 四项（N1–N4）合入，全量回归 | 第 1 周末 |
| M2 | N5 推理服务 + N9 SDK | 第 3 周末 |
| M3 | N6 + N7 | 第 5 周末 |
| M4 | N8 前端 + 统一回归 | 第 6 周末 |

**工时汇总**：P0 ≈ 5 人日；P1 ≈ 28 人日（N5 8 + N6 6 + N7 6 + N8 5 + N9 3）；P2 另估。

### 7.6 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| N5/N6 模型分发体积与加载耗时 | 推理延迟超标 | 模型大小上限配置 + warm session（W9 pause 复用）+ 指标观测（W1） |
| N7 kubernetes client 与现网 kubectl 行为差异 | K8s 路径回归 | 双轨保留：`K8S_USE_PYTHON_CLIENT: bool` 开关，默认 true，异常可回退子进程路径一轮 |
| N2 移除 chain_attestation 触及 e2e 断言 | 测试红 | 实施前 grep 三向验证（本 spec 已验：仅 glob 存在性断言） |
| 无集群/无 onnxruntime 环境 | N5/N7 部分用例只能 mock | fail-closed + 环境验收项诚实登记；mock 单测覆盖协议层 |
| MPC 持久化引入加密 share 的密钥依赖 | KEK 依赖与 wrapped keys 同（B3 边界） | 复用 `SM4Cipher` KEK 派生链，标注单副本限制（同 T4 边界） |

---

## 八、执行边界声明

- 本方案只覆盖**软件可闭环**项。FG-001..016、W6 决策项、LAC 安装、helm 真渲染按既有纪律环境验收，不伪造。
- 扫描结论中 C 类缺陷系本轮新发现（此前未登记于任何 tracker），实施时若与代码冲突以代码为准并回写。
- 每轮实施完成后在"执行记录"追加：任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项。

---

## 九、执行记录

### Round 45 执行记录（2026-09-08）—— M1：P0 四项（N1–N4）

| 任务 | 状态 | 关键产出 | 新增测试与结果 | 回归结论 | 未实施项 |
|---|---|---|---|---|---|
| N1 生产部署文件修复 + 语法门禁 | ✅ 已实现 | `docker-compose.prod.yml` 第 114 行 `CDS_LOG_JSON` 缩进归位（2→6 空格），`yaml.safe_load` 恢复可解析；新建 `tests/test_deploy_manifests.py`（22 用例：全部 compose/values/Chart/k8s 多文档 manifest 可解析、prod fail-closed 键在位、dev/prod service 拓扑一致、placeholder 声明核对）；`.github/workflows/ci.yml` backend gate 加入该文件 | tests/test_deploy_manifests.py 22 passed | — | 无 |
| N2 遗留半接线/死代码处置 | ✅ 已实现 | ①移除 `app/services/chain_attestation.py` + `scripts/chain-attestation-ddl.sql`（无调用方、表不在 alembic），`test_p0_security_gaps.py` SM3 glob 断言同步；②`merkle_pipeline.py` 保留并显式接线：新增 `MERKLE_PIPELINE_ENABLED=False`（config + 四同步）+ `app/main.py` lifespan 按开关 start/stop（开启时 warning 标注实验性异步批量模式）；③移除死配置键 `STREAMING_PROXY_ENABLED`/`STREAMING_PROXY_PORT`（config.py，四文件均无引用），runbook §5.1 说明流式代理为外部 mitmproxy 组件；④移除 `DockerAdapter` 类（sandbox_runtime.py，无引用，L3 由 BwrapAdapter 承担）；⑤移除 `app/models/key_metadata.py`（无导入方，`kms.py:140` `KeyMetadata = DataEncryptionKey` 别名保留，`key_metadata` 表从未被 create_all/alembic 创建） | tests/test_p0_security_gaps.py + test_merkle_pipeline.py + test_audit_service.py + test_audit_api.py 59 passed；`from app.core.config import settings` 冒烟通过（无死键） | 待全量回归确认 | 无 |
| N3 MPC 秘密分享持久化 | ✅ 已实现 | 新建 `app/models/mpc_key.py`（`mpc_keys` + `mpc_key_shares`，share_value 为 SM4-GCM 密文，KEK 复用 `AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY` → sha256 派生链，同 egress_audit）；Alembic `0008_mpc_persistence`（additive）；`mpc_service.py` 全量改造为 async + DB 落库（split/reconstruct/get/list/rotate/verify/destroy，destroy 标记 destroyed + crypto-erase share，rotate 标记 rotated + 旧 share 清除）；`mpc.py` API 全端点接 db + `status`/`persisted:true` 字段 + 新增 `POST /keys/{id}/destroy`；`app/main.py` 注册模型导入 | tests/test_mpc_service.py 重写（async）+ tests/test_mpc_persistence.py 新增，合计 21 passed（含"新服务实例重启重构一致/destroy 后重构失败/threshold 不足回归"）；`alembic upgrade head` + `verify_schema_parity.py` → SCHEMA PARITY OK 36 tables | 待全量回归确认 | 无 |
| N4 Helm/K8s 拓扑对齐 | ✅ 已实现 | `helm/cds/values.yaml`：ClickHouse/OPA 标注 EXTERNALLY MANAGED + 新增 `externalComponents` 白名单（clickhouse/opa，注释契约）；`templates/service.yaml` 删除悬空 `-opa` Service（selector 无匹配 Deployment，改 `CDS_OPA_URL` 直连外部）；`Chart.yaml` 注明 `helm dependency build` 前置；`k8s/secrets.yaml` 顶部加 `DEV ONLY` 显著标注；`specs/productization-deployment-runbook.md` §5.1 新增"K8s manifests 与 Helm chart 的密钥边界"章节 | tests/test_helm_chart.py 扩展 `test_enabled_components_have_templates_or_whitelist`（enabled=true 组件必须有模板/依赖/白名单，防再漂移）；helm+deploy 合计 28 passed | 待全量回归确认 | helm 真渲染/lint 属环境验收（本机无 helm） |

**配置四同步**：`MERKLE_PIPELINE_ENABLED=false` 已同步 .env.example / docker-compose.yml / docker-compose.prod.yml / helm/cds/values.yaml。
**禁项自检**：无裸 `except: pass` 新增、未删既有测试、未放宽 W2/T5 门禁；N2/N3 移除均为无引用死代码（grep 三向验证）。
**偏差说明**：N3 表主键按既有 API 契约用 String(64)/String(128)（服务生成的短 hex key_id/share_id 原样返回），未按 spec 字面用 UUID 列——API 行为不变优先。
