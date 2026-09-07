# CDS 密态沙箱产品化部署 Runbook

> 更新时间：2026-09-07  
> 目标：把本地开发、243 验证、K3s/K8s 预生产和生产 Helm 交付的环境变量、安全默认值、部署步骤、回滚方式和验证命令集中到一份可执行清单。

---

## 0. 数据库迁移（W5 基线）

Alembic 链（`alembic/versions/`）自 `0000_baseline_all_tables` 起，可从空库建出全部表：

```bash
alembic upgrade head                      # 空库：0000 建全部表 → 0001/0002 加列
python scripts/verify_schema_parity.py    # Alembic 链 vs create_all 结构比对（CI 门禁）
```

| 场景 | 操作 |
|---|---|
| 全新生产库 | `alembic upgrade head`（生产禁止 create_all，见 `app/main.py` 分支条件） |
| 既有 dev/test 库（create_all 建的） | `alembic stamp head`（列已在，不重放） |
| 模型改动后 | `alembic revision --autogenerate` → **人工核对** → `upgrade head` → parity 脚本 |
| 漂移检测 | `alembic check`（CI 中执行；模型与迁移不一致时失败） |

---

## 1. 部署模式

| 模式 | 用途 | 入口 | 说明 |
|---|---|---|---|
| 本地 Compose | 开发联调、产品演示 | `docker-compose.yml` | 包含 API、前端、Postgres、Redis、ClickHouse、MinIO、OPA、Vault。 |
| 243 K3s | 资源受限机器上的集成验证 | `scripts/install-k3s.sh`、`k8s/*.yaml` | 默认使用中国网络友好的 K3s 安装参数和 registry mirror；API 暴露 `30080`，前端暴露 `30081`。 |
| 生产 Helm | 预生产/生产部署 | `helm/cds` | Secret 由平台注入，Ingress/TLS/持久化由 values 控制。 |

---

## 2. 安全配置基线

后端配置类使用 `CDS_` 环境变量前缀。生产环境必须设置下列变量，不能依赖代码默认值：

| 变量 | 必填 | 要求 |
|---|---|---|
| `CDS_DATABASE_URL` | 是 | 指向生产 Postgres，必须使用独立账号和强密码。 |
| `CDS_REDIS_URL` | 是 | Redis 必须启用认证。 |
| `CDS_JWT_SECRET_KEY` | 是 | HS256 至少 32 字节；禁止使用默认值。 |
| `CDS_MINIO_ENDPOINT` / `CDS_MINIO_ACCESS_KEY` / `CDS_MINIO_SECRET_KEY` | 是 | 对象存储凭证必须来自 Secret/Vault。 |
| `CDS_VAULT_ADDR` / `CDS_VAULT_TOKEN` | 生产必填 | 接 HSM/Vault 时必须使用最小权限 token。 |
| `CDS_OPA_URL` | 是 | 策略引擎地址。 |
| `CDS_TEE_MODE` | 是 | 无真实 TEE 时可为 `auto` 或 `software`；有 TEE 时配置对应硬件 hook。 |
| `CDS_TEE_ALLOW_SOFTWARE_FALLBACK` | 是 | 普通密态沙箱允许 `true`；要求真实 TEE 保证时必须置为 `false`。 |
| `CDS_ALERT_WEBHOOK_URLS` | 可选 | 配置真实 Webhook/SIEM 后，告警中心会记录投递状态。 |
| `CDS_CDC_KAFKA_BROKERS` / `CDS_CDC_KAFKA_CONNECT_URL` | 可选 | 配置真实 Kafka Connect 后启用 CDC 控制面。 |

安全默认值：

- 无 TEE 环境时，L1 作为普通软件密态沙箱运行，证明类型为 `software_confidential/software_hash`，不伪造硬件 quote。
- 有 TEE 环境且配置 `CDS_TEE_HARDWARE_PROVISION_CMD` / `CDS_TEE_HARDWARE_EXEC_CMD` / `CDS_TEE_HARDWARE_ATTEST_CMD` 时，运行时进入硬件对接路径。
- `k8s/secrets.yaml` 是样例文件，交付部署前必须用平台 Secret 替换其中所有明文占位值。

---

## 3. 本地 Compose 部署

1. 准备环境：

```bash
cp .env.example .env
```

2. 修改 `.env` 中所有 `change_me_*` 值，确保 `CDS_JWT_SECRET_KEY` 至少 32 字节。

3. 启动：

```bash
docker compose up --build -d
```

4. 初始化/迁移数据库：

```bash
docker compose exec cds-api alembic upgrade head
```

5. 访问：

```text
API:      http://localhost:8000
Frontend: http://localhost:3000
MinIO:    http://localhost:9002
Vault:    http://localhost:8200
```

6. 回滚：

```bash
docker compose down
docker compose up -d postgres redis clickhouse minio opa vault
docker compose up --build -d cds-api cds-frontend
```

---

## 4. 243 / K3s 部署

1. 同步代码：

```bash
rsync -a app alembic specs tests k8s scripts helm Dockerfile.api docker-compose.yml pyproject.toml requirements.txt root@172.22.4.243:/root/cds-sandbox-codex/
rsync -a cds-frontend root@172.22.4.243:/root/cds-sandbox-codex/
```

2. 在 243 构建本地镜像：

```bash
ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && docker build -f Dockerfile.api -t cds-api:latest .'
ssh root@172.22.4.243 'cd /root/cds-sandbox-codex/cds-frontend && docker build -t cds-frontend:latest .'
```

3. 安装 K3s 并部署：

```bash
ssh root@172.22.4.243 'cd /root/cds-sandbox-codex && INSTALL_K3S_MIRROR=cn CDS_K3S_MODE=k3s bash scripts/install-k3s.sh'
```

4. 查看状态：

```bash
ssh root@172.22.4.243 'k3s kubectl get pods,svc -n cds-system'
```

5. 访问：

```text
API:      http://172.22.4.243:30080
Frontend: http://172.22.4.243:30081
```

6. 回滚：

```bash
ssh root@172.22.4.243 'k3s kubectl rollout undo deploy/cds-api -n cds-system || true'
ssh root@172.22.4.243 'k3s kubectl rollout undo deploy/cds-frontend -n cds-system || true'
```

---

## 5. 生产 Helm 部署

1. 创建 Secret。Secret key 必须使用 `CDS_` 前缀：

```bash
kubectl -n cds-system create secret generic cds-secrets \
  --from-literal=CDS_DATABASE_URL='postgresql+asyncpg://...' \
  --from-literal=CDS_REDIS_URL='redis://:...@redis:6379/0' \
  --from-literal=CDS_CLICKHOUSE_URL='http://clickhouse:8123/cds_audit' \
  --from-literal=CDS_JWT_SECRET_KEY='replace-with-32-bytes-minimum' \
  --from-literal=CDS_VAULT_ADDR='https://vault.example.com' \
  --from-literal=CDS_VAULT_TOKEN='replace-with-token' \
  --from-literal=CDS_MINIO_ENDPOINT='minio.example.com' \
  --from-literal=CDS_MINIO_ACCESS_KEY='replace-with-access-key' \
  --from-literal=CDS_MINIO_SECRET_KEY='replace-with-secret-key'
```

2. 部署：

```bash
helm upgrade --install cds helm/cds -n cds-system --create-namespace -f helm/cds/values.yaml
```

3. 回滚：

```bash
helm history cds -n cds-system
helm rollback cds <REVISION> -n cds-system
```

---

## 6. 编译级验证

开发阶段按当前要求只做编译/构建验证，不跑单元测试和 e2e：

```bash
.venv/bin/python -m compileall -q app alembic
cd cds-frontend && npm run build
bash -n scripts/install-k3s.sh k8s/deploy.sh
docker compose config >/tmp/cds-compose-config.yaml
helm template cds helm/cds >/tmp/cds-helm-rendered.yaml
```

如果当前机器未安装 Docker 或 Helm，可在 243 或 CI 中执行对应命令。

CI（W7，`.github/workflows/ci.yml`）在 push/PR 时自动执行上述门禁，本地复现：

```bash
pip install -r requirements.txt
python -m compileall -q app tests alembic
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" ALEMBIC_COMPARE_TYPES=0 alembic check
python scripts/verify_schema_parity.py
python -m pytest tests/test_observability.py tests/test_error_contract.py \
  tests/test_admin_auth.py tests/test_session_lifecycle.py \
  tests/test_contract_terminate_cascade.py tests/test_dev_sandbox_contract.py \
  tests/test_kms_attestation_required.py tests/test_output_gateway_enforcement.py \
  tests/test_contract_purpose.py tests/test_security_config_matrix.py \
  tests/test_training_fail_closed.py tests/test_field_classification_policy.py \
  tests/test_dp_guardrails.py tests/test_kms_dek_wrapped.py \
  tests/test_pii_ner_engine.py tests/test_blockchain_backend_disclosure.py \
  tests/test_rag_embedding.py tests/test_rag_service.py tests/test_rag_runner.py \
  tests/test_rag_task.py tests/test_rag_ingest.py \
  tests/test_rate_limit_distributed.py tests/test_tenant_quota_persistence.py \
  tests/test_migrations.py -q
```

---

## 7. 统一测试验证

所有产品化代码开发完成后统一执行：

```bash
.venv/bin/python -m pytest -q
cd cds-frontend && npm test
```

真实环境验收不伪造结果，应在具备对应基础设施后执行：

- 243/K3s 沙箱生命周期 e2e。
- 真实 TEE/GPU-TEE/HSM/Vault/联盟链/SIEM 集成测试。
- 性能容量、备份恢复、密钥轮换、故障恢复和长稳测试。

---

## 8. 发版安全签署

发版前按以下顺序执行：

1. 阅读 `specs/product-security-release-review.md`，确认本次发布类型、可声明能力、不得声明能力和残余风险。
2. 填写 `specs/release-gate-checklist.md` 中的发布结论模板。
3. 完成所有 P0 门禁；任一 P0 失败则 No-Go。
4. 对未完成的 P1 门禁写入发布说明，作为 Conditional Go 的限制条件。
5. 发布后按“发布后 24 小时观察项”监控 API、沙箱、KMS、输出审查、审计和外部依赖。

发版文档入口：

- `specs/product-security-release-review.md`
- `specs/release-gate-checklist.md`
- `specs/productization-gap-analysis.md`
