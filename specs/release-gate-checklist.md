# CDS v0.1.0 发版门禁清单

> 日期：2026-06-09  
> 适用范围：v0.1.0 release candidate 的试点/预生产发版  
> 原则：所有硬件、外部系统和生产级安全证据必须真实验证；未验证项只能标注为未启用或待验收，不允许在发布说明中伪造成已完成。

---

## 1. 发布结论模板

发布负责人在发版前填写：

| 项 | 结论 |
|---|---|
| 版本号 | `0.1.0` |
| 发布类型 | 内部 / POC / 试点 / 预生产 / 生产 |
| Git commit | 待填写 |
| API 镜像 digest | 待填写 |
| 前端镜像 digest | 待填写 |
| 数据库迁移版本 | 待填写 |
| 发布结论 | Go / Conditional Go / No-Go |
| 条件说明 | 待填写 |
| 发布负责人 | 待填写 |
| 安全负责人 | 待填写 |
| 运维负责人 | 待填写 |

推荐默认结论：

- 内部/POC/试点：`Conditional Go`
- 生产 GA：`No-Go`，直到真实环境验收、供应链证据包和安全测试报告完成

---

## 2. P0 阻塞门禁

任一 P0 未通过，不允许发版。

| ID | 门禁项 | 命令/证据 | 结果 |
|---|---|---|---|
| RG-P0-001 | 后端编译通过 | `.venv/bin/python -m compileall -q app alembic` | 待填写 |
| RG-P0-002 | 前端构建通过 | `cd cds-frontend && npm run build` | 待填写 |
| RG-P0-003 | 数据库迁移可执行 | `alembic upgrade head` 在目标 Postgres 执行记录 | 待填写 |
| RG-P0-004 | JWT secret 非默认且不少于 32 字节 | Secret 审计截图或命令输出 | 待填写 |
| RG-P0-005 | `.env`、`k8s/secrets.yaml`、Helm Secret 无样例明文密码用于生产 | Secret 审核记录 | 待填写 |
| RG-P0-006 | API/前端镜像固定 digest，不使用 `latest` 作为生产 tag | 镜像清单 | 待填写 |
| RG-P0-007 | 依赖漏洞扫描完成并无未接受的高危/严重漏洞 | pip-audit/npm audit/平台扫描报告 | 待填写 |
| RG-P0-008 | 镜像漏洞扫描完成并无未接受的高危/严重漏洞 | Trivy/Grype/平台扫描报告 | 待填写 |
| RG-P0-009 | SBOM 生成完成 | Python + frontend SBOM 文件 | 待填写 |
| RG-P0-010 | 生产 CORS 域名不包含任意来源 | 环境变量与启动日志 | 待填写 |
| RG-P0-011 | 生产关闭 debug | `CDS_DEBUG=false` | 待填写 |
| RG-P0-012 | 输出审查路径未被绕过 | 最小 e2e 证据或代码审查记录 | 待填写 |
| RG-P0-013 | 管理员高危操作理由与审计可查 | KMS 密钥撤销、证书撤销、合约终止、连接器暂停/恢复/API Key 轮换样例；审计 detail 必须包含 reason，可选 ticket_id | 待填写 |
| RG-P0-014 | K8s 生产部署具备 TLS Ingress | Ingress/TLS Secret 证据 | 待填写 |
| RG-P0-015 | 备份恢复方案已确认 | Postgres/Redis/MinIO/ClickHouse 备份策略 | 待填写 |
| RG-P0-016 | 安全态势未给出 No-Go | `GET /api/v1/monitoring/security-posture` 输出与首页“安全态势”截图；无 `release_gate=block` 或已签署例外 | 待填写 |

---

## 3. P1 条件门禁

P1 未完成时可发试点版，但必须在发布说明中写明限制。

| ID | 门禁项 | 条件发布要求 | 结果 |
|---|---|---|---|
| RG-P1-001 | 真实 TEE 硬件验证 | 未完成则发布说明标注“当前以软件密态沙箱运行” | 待填写 |
| RG-P1-002 | GPU-TEE 硬件训练验证 | 未完成则不得宣称 GPU-TEE 生产可用 | 待填写 |
| RG-P1-003 | HSM/Vault 生产接入 | 未完成则不得宣称硬件密钥托管完成 | 待填写 |
| RG-P1-004 | 联盟链真实上链 | 未完成则只宣称本地 hash-chain/append-only 审计 | 待填写 |
| RG-P1-005 | SIEM/邮件/短信/Webhook 真实投递 | 未完成则只宣称本地告警中心 | 待填写 |
| RG-P1-006 | K3s/K8s 生命周期 e2e | 未完成则不得宣称目标集群已验收 | 待填写 |
| RG-P1-007 | 性能压测与长稳 | 未完成则不得给出生产容量承诺 | 待填写 |
| RG-P1-008 | 沙箱逃逸/渗透测试 | 未完成则生产 GA No-Go | 待填写 |
| RG-P1-009 | 备份恢复演练 | 未完成则生产 GA No-Go | 待填写 |
| RG-P1-010 | 回滚演练 | 未完成则发布必须准备人工回滚窗口 | 待填写 |
| RG-P1-011 | 沙箱会话证明包归档 | 至少提供一个 `GET /api/v1/sandbox-sessions/{id}/proof-bundle` JSON 样例，包含 evidence hash、bundle hash、policy hash、quote hash、输出签名和审计摘要 | 待填写 |

---

## 4. 建议发版命令

开发机编译级验证：

```bash
.venv/bin/python -m compileall -q app alembic
cd cds-frontend && npm run build
```

配置渲染验证：

```bash
bash -n scripts/install-k3s.sh k8s/deploy.sh
docker compose config >/tmp/cds-compose-config.yaml
helm template cds helm/cds >/tmp/cds-helm-rendered.yaml
```

目标环境迁移验证：

```bash
alembic current
alembic upgrade head
alembic current
```

镜像构建建议：

```bash
docker build -f Dockerfile.api -t cds-api:0.1.0 .
docker build -t cds-frontend:0.1.0 cds-frontend
docker image inspect cds-api:0.1.0 --format '{{json .RepoDigests}}'
docker image inspect cds-frontend:0.1.0 --format '{{json .RepoDigests}}'
```

---

## 5. 发布说明必须包含

发布说明必须明确：

- 本版本是试点/预生产版本还是生产版本。
- 当前部署使用 `software_confidential/software_hash` 还是真实 TEE quote。
- HSM/Vault、链存证、SIEM、Kafka Connect 是否真实启用。
- 安全态势接口的 `release_recommendation` 和任何 `release_gate=block/conditional` 的解释。
- 沙箱会话证明包样例的 `evidence_hash`、`bundle_hash`、证明级别和残余限制。
- 哪些外部环境能力尚未完成验收。
- 已知残余风险和规避方式。
- 回滚步骤和数据库迁移注意事项。
- 兼容性说明：
  - `POST /data-products/{product_id}/test-data/mock` 已 deprecated，推荐使用 `/test-data/synthetic`。
  - `POST /data-resources/generate-mock` 已 deprecated，推荐使用 `/generate-synthetic`。
  - GPU-TEE factory 默认实现为 `local`，`stub` 仅保留为兼容 alias。

---

## 6. Go / No-Go 规则

| 场景 | 规则 |
|---|---|
| P0 任一失败 | No-Go |
| P0 全过，P1 有缺口 | Conditional Go，仅限内部/POC/试点/预生产 |
| P0 全过，P1 全过，但缺性能长稳 | Conditional Go，不允许生产容量承诺 |
| P0/P1 全过，完成安全负责人签署 | Go |

---

## 7. 发布后 24 小时观察项

发布后必须观察：

- API 5xx、认证失败率、CORS 错误。
- 沙箱创建、执行、终止成功率。
- KMS key 创建/轮换/撤销错误。
- OutputInspector 阻断率、误报反馈。
- DP budget 扣减失败和告警。
- 审计写入延迟、ClickHouse/PG 审计一致性。
- Redis/Postgres/MinIO/OPA/Vault 连接错误。
- 前端静态资源 404、登录失败、页面接口 401/403/500。

回滚触发条件：

- 核心 API 连续 5 分钟 5xx 大于 5%。
- 沙箱生命周期关键路径不可用。
- KMS/证书/认证出现系统性失败。
- 输出审查被绕过或审计写入中断。
- 数据库迁移造成不可恢复的数据写入错误。
