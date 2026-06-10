# CDS 密态沙箱安全产品设计复核

> 版本：v0.1.0 release candidate  
> 日期：2026-06-09  
> 目标：从产品设计和安全交付角度明确当前可发布能力、必须加注的条件、残余风险和下一阶段增强项。

---

## 1. 发版结论

当前版本可以作为“可信数据空间密态沙箱试点版 / 预生产版”发布，用于软件密态沙箱、合约授权、输出审查、审计留痕、KMS/证书、联邦互联和训练管线的软件闭环验证。

当前版本不应在未完成真实环境验收前宣称以下能力已经生产级闭环：

- 真实 TEE 硬件隔离已完成。
- GPU-TEE/NVIDIA CC 训练已在硬件上完成验收。
- 生产 HSM/Vault/国密硬件密码模块已完成合规接入。
- 生产联盟链/SIEM/短信/邮件通道已完成外部系统验收。
- K3s/K8s 真实集群 NetworkPolicy、FQDN allowlist、ResourceQuota 已在目标环境完成 e2e。
- 已完成渗透测试、沙箱逃逸测试、SBOM、镜像扫描和供应链签名。

推荐发版标签：

| 发布类型 | 建议 |
|---|---|
| 内部开发版 | 可以发布 |
| 客户试点 / POC | 可以发布，但合同和页面文案必须标注真实硬件能力以部署环境为准 |
| 预生产验证版 | 可以发布，必须执行发布门禁清单 |
| 生产 GA | 不建议，需完成真实环境验收、供应链证据包和安全测试报告 |

---

## 2. 核心安全设计边界

### 2.1 运行时隔离

已实现的软件边界：

- L3 使用 hardened bwrap 隔离，支持 seccomp 兼容重试。
- L2 Firecracker/QEMU 不可用时有 hardened bwrap fallback，不返回未实现占位。
- L1 无硬件时明确降级为普通软件密态沙箱，证明类型为 `software_confidential/software_hash`，不伪造 SGX/TDX/SEV-SNP quote。
- K8s 运行时具备 manifest、readiness gate、ResourceQuota、NetworkPolicy 和 Cilium FQDN allowlist 语义。

产品声明边界：

- 无真实 TEE 时，只能宣称“软件密态沙箱 + 隔离执行 + 审计证明”，不能宣称“硬件 TEE 内运行”。
- 如果 `CDS_TEE_ALLOW_SOFTWARE_FALLBACK=false`，硬件 TEE 不可用必须失败关闭。

### 2.2 密钥与证书

已实现的软件边界：

- KMS key lifecycle、轮换、撤销、审计已接入后端与前端。
- 证书签发、删除、验证链路已接入本地 CA/证书服务。
- 会话密钥和数据密钥通过运行时上下文分发，并在会话终止/证书吊销流程中纳入审计。

发布要求：

- 生产必须替换 `.env`、`k8s/secrets.yaml` 和 Helm Secret 中所有样例密钥。
- 生产必须使用 HSM/Vault 最小权限 token；Vault dev mode 只能用于开发。
- JWT secret 至少 32 字节，且禁止使用默认值。

### 2.3 输出安全

已实现的软件边界：

- 沙箱任务、开发沙箱、连接器代理和契约网关均接入统一输出审查。
- PII/DLP、k 匿名、重建检测、DP budget、水印、签名构成输出释放控制面。
- DP 预算查询和扣减失败时，涉及 epsilon 的路径失败关闭。

发布要求：

- 发布演示和客户试点必须使用输出审查页面展示“阻断/脱敏/签名/水印”结果。
- 不允许绕过 OutputInspector 直接暴露沙箱原始 stdout 或文件输出。

### 2.4 审计与不可抵赖

已实现的软件边界：

- 主审计写入 PostgreSQL，并支持 SM2 签名、Merkle/append-only 证明和链式 fallback。
- ClickHouse/SIEM/链节点不可用时，不伪造外部回执；保留本地可验证哈希链。

发布要求：

- 对外声明“链上存证”前，必须提供真实链 tx hash、区块高度、确认状态和验签过程。
- SIEM/Webhook 未配置时，只能声明“本地告警中心和投递记录能力已具备”。

### 2.5 联邦互联

已实现的软件边界：

- Trust 建立、暂停、撤销、目录同步、信任评分和连接器 API key lifecycle 已对齐。
- 连接器代理会话执行前校验 contract 与 connector/space 绑定关系，并接入代码扫描和输出审查。

发布要求：

- 真实跨空间互操作需要第二可信数据空间或测试桩完成 e2e。
- 信任评分当前是本地运行指标和配置计算结果，不代表第三方信用评级。

---

## 3. 发版前必须补齐的证据

| 类别 | 必需证据 | 阻塞级别 |
|---|---|---:|
| 编译构建 | 后端 `compileall`、前端 `npm run build` | P0 |
| 数据库迁移 | Alembic 在目标 Postgres 执行记录和回滚策略 | P0 |
| Secret | 所有默认/样例 Secret 已替换，JWT/Vault/MinIO/Redis/Postgres 强凭证检查通过 | P0 |
| 镜像 | API/前端镜像 digest、漏洞扫描结果、基础镜像版本 | P0 |
| SBOM | Python 与前端依赖 SBOM | P0 |
| K8s | Pod 安全上下文、NetworkPolicy、ResourceQuota、readiness/liveness、Ingress TLS | P0 |
| 安全测试 | SAST、依赖漏洞扫描、镜像扫描、沙箱逃逸测试、权限绕过测试 | P0 |
| e2e | 最小生命周期、合约、沙箱执行、输出审查、审计、KMS、联邦最小流 | P1 |
| 外部系统 | TEE/HSM/链/SIEM/Kafka Connect 的真实回执或明确标注未启用 | P1 |
| 运维 | 备份恢复、回滚、日志留存、告警投递、事故响应联系人 | P1 |

---

## 4. 产品设计改进建议

这些改进不阻塞 v0.1.0 试点发版，但建议进入下一轮产品化路线图。

| ID | 改进点 | 安全价值 | 建议优先级 |
|---|---|---|---:|
| SD-001 | 在前端增加“安全态势条”，展示 TEE 模式、HSM/Vault、链存证、SIEM、K8s policy 是否真实启用 | 防止用户误解软件 fallback 为硬件能力 | P0 |
| SD-002 | 每个沙箱会话详情增加“证明包”下载：policy hash、image hash、software/hardware quote、key id、output signature、audit ids | 便于客户验收和监管留痕 | P0 |
| SD-003 | 数据产品增加“安全画像”：字段敏感度、可见性策略、DP 默认预算、输出约束、可用沙箱等级 | 降低合约配置错误 | P1 |
| SD-004 | 连接器/联邦页面增加 trust score 明细和异常空间处置建议 | 降低跨空间误信任 | P1 |
| SD-005 | KMS/证书页面增加轮换演练、吊销影响面和会话终止模拟 | 减少运维误操作 | P1 |
| SD-006 | 输出审查增加可解释策略链：命中规则、阻断原因、脱敏前后统计、审批建议 | 提升买方/监管可理解性 | P1 |
| SD-007 | 发布控制台增加“发版证据包”聚合：构建 digest、SBOM、扫描结果、迁移版本、测试摘要 | 支持企业发布审计 | P1 |
| SD-008 | 增加 break-glass 紧急授权流程，要求双人审批、短 TTL、强审计和自动吊销 | 处理生产事故且保留可追责性 | P1 |
| SD-009 | 对管理员高危操作加入二次确认和理由必填：密钥撤销、证书删除、合约终止、连接器撤销、策略放宽 | 降低误操作风险 | P1 |
| SD-010 | 引入租户级安全基线模板：金融、医疗、政务、科研四类策略预设 | 提高落地效率并减少策略漂移 | P2 |

当前软件闭环进展：

- `SD-001` 已完成。运营/监管/管理员可通过 `GET /api/v1/monitoring/security-posture` 和首页“安全态势”卡片查看 TEE、GPU-TEE、HSM/Vault、链存证、SIEM、K8s policy、输出审查和 Debug 状态，发版前应以该接口作为环境能力披露证据。
- `SD-002` 已完成。沙箱会话详情可通过 `GET /api/v1/sandbox-sessions/{session_id}/proof-bundle` 生成 JSON 证明包，包含 policy hash、attestation quote hash、key id、输出签名、审计摘要、稳定 evidence hash 和本次 bundle hash；证明包不包含密钥明文、原始输出、raw quote 或 Secret 配置值。
- `SD-009` 已完成。KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换均要求提交高危操作理由，可选工单号，并将 reason/ticket_id 写入审计 detail。

---

## 5. 残余风险

| 风险 | 当前控制 | 发版处理 |
|---|---|---|
| 无硬件 TEE 环境时无法提供硬件内存保护 | 明确软件 fallback，生成 `software_hash`，不伪造 quote | 发版说明必须标注 |
| Vault/HSM 未真实接入时密钥仍由软件路径托管 | HSM/Vault adapter 和软件 fallback 已实现 | 生产发布必须要求真实 Vault/HSM 或标注试点限制 |
| 真实 K8s 网络策略效果依赖集群 CNI | 非 Cilium 时 FQDN allowlist 失败关闭 | 目标集群必须 e2e 验证 |
| 供应链扫描证据缺失 | 代码和配置已可构建 | 生产发版前必须补 SBOM/扫描/签名 |
| 外部链/SIEM/邮件/短信未配置时无法形成外部回执 | 本地审计和告警记录可用 | 对外声明必须区分本地记录与外部投递 |
| 性能和长稳数据缺失 | 单元/聚焦测试覆盖逻辑 | 生产 GA 前必须压测和长稳 |

---

## 6. 发版口径

推荐对外口径：

> CDS v0.1.0 提供可信数据空间密态沙箱的软件闭环能力，包括合约授权、隔离执行、密钥/证书、输出审查、审计留痕、联邦连接器和训练管线。系统支持在无 TEE 环境下以软件密态沙箱运行；如部署方提供 TEE/HSM/联盟链/SIEM/K8s 等基础设施，可通过已预留的适配器和 hook 接入并完成环境验收。

禁止对外口径：

- “已完成所有真实 TEE/GPU-TEE 硬件验证。”
- “默认部署即满足等保/国密/行业认证。”
- “所有输出绝对不可泄露。”
- “链存证/SIEM 已生产可用。”除非目标环境已配置并提供回执证据。
