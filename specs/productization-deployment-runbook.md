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
| stamp 落在旧 revision 的库（历史引导只到 00xx） | 先 `alembic current` 对账，再 `alembic stamp head`，最后 `upgrade head`。直接 `upgrade` 会重放已存在的 `create_table`，PG 报 42701 `relation already exists`（后续加列链会连带报 42703）。已于 2026-10-15 远程部署现场实测。 |
| 模型改动后 | `alembic revision --autogenerate` → **人工核对** → `upgrade head` → parity 脚本 |
| 漂移检测 | `alembic check`（CI 中执行；模型与迁移不一致时失败） |
| 跨过 0021 的存量库（数据集交付包） | 升级本身加性无锁表；但 0021 之前生成的交付包在库里**没有归属记录**，`GET /deliveries/dataset/{bundle_id}/download` 会诚实返回 404（无记录即拒绝，不按"猜到 UUID 就放行"）。告知使用方重新点一次「生成交付包」即可；旧包字节仍留在对象存储里，按留存策略不删。 |

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
| `CDS_COLUMN_ENCRYPTION_KEY` | 生产必填 | 16 字节列加密 DEK（hex/base64）；生产空值/默认值启动即拒（SS-11 PZ-04）。 |
| `CDS_SM3_FALLBACK_ALLOWED` | 生产必须 `false` | `gmssl` 缺失时 SHA-256 回退开关；生产 `true` 启动即拒（SS-11 PZ-04）。 |
| `CDS_ATTESTATION_ALLOWED_MEASUREMENTS` | 生产必填（TEE 证明门禁开启时） | measurement 白名单（csv）；生产空值启动即拒，本地静态信任根披露 `trust_root=local-simulated`（SS-11 PZ-05/MR-B1）。 |
| `CDS_FEDERATION_ENABLED` / `CDS_FEDERATION_CA_BUNDLE` / `CDS_FEDERATION_CERT` / `CDS_FEDERATION_KEY` | 联邦启用时必填 | 生产启用联邦且缺 mTLS 材料 → 拒绝联邦通道（fail-closed）；跨空间互操作归 FG-008（SS-11 PZ-05）。 |
| `CDS_SECCOMP_FALLBACK_ALLOWED` | 建议生产 `false` | seccomp 不兼容时无 seccomp 重试开关（firecracker + sandbox_runtime 双点门禁，SS-11 PZ-04/MR-A13 收口 S4）。 |
| `CDS_MIA_SHADOW_MODEL` | 可选 | 影子模型 MIA 评估开关（默认关；开启后响应携 `mia_status=shadow_model`）。 |

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

### 5.1 K8s manifests 与 Helm chart 的密钥边界（spec N4）

两套部署路径的密钥来源不同，切勿混用：

- **`k8s/secrets.yaml`（manifests 路径，dev/243 专用）**：内含硬编码明文开发凭据，已提交到 git，**仅限 dev/243 环境**。文件顶部有 `DEV ONLY` 标注。任何真实环境都必须轮换其中的所有凭据，并使用外部密钥管理注入。
- **Helm chart（生产路径）**：不内置任何明文密钥，一律经 `secrets.existingSecret`（默认 `cds-secrets`）以 `CDS_*` 前缀注入（见 §5 创建命令）。生产环境推荐将 `existingSecret` 指向由 Vault / External Secrets Operator / 云 KMS 托管的 Secret，而非 manifests 路径的静态文件。
- **外部组件接线**：Helm 部署下 ClickHouse / OPA 不随 chart 渲染（见 `helm/cds/values.yaml` `externalComponents` 白名单），通过 `CDS_CLICKHOUSE_URL` / `CDS_OPA_URL` 指向外部实例；其连接凭据同样经 existingSecret 注入。
- **流式输出代理**：`STREAMING_PROXY_ENABLED` 配置键已移除（spec N2）——流式输出代理是部署栈外部的 mitmproxy 组件，需独立部署并前置在会话出口；CDS 应用本身不再声明该能力。

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

---

## 9. Cube 节点部署与升级（SS-10 CB-02）

> 适用范围：CubeSandbox 计算节点（`DEPLOY_ROLE=compute`，**不在** CDS k3s 内，§5.1 红线 2）。上游版本钉住见 `scripts/cube-bundle-manifest.json`（§5.2：钉住单位 = 上游 git sha）。真实节点 bring-up + 全链路 e2e 为环境验收项 **FG-017**。
>
> **硬件前提（交付口径，2026-10-16 实测订正）**：计算节点必须是**裸金属**，或**宿主已开启嵌套虚拟化并向 guest 透传 `vmx`/`svm` 的 VM**。普通云 VM（未透传虚拟化指令集）**不能**作为计算节点：guest 内无任何手段可生成 `/dev/kvm`，`preflight.sh` 第一项即硬 FAIL；SS-13 单路径下**不降级到进程隔离/bwrap**，节点仅能以"无数据面"档运行（控制面/目录/契约/审批/审计/交付中心可用，执行类入口返回 `RUNTIME_UNAVAILABLE` + `reason=dataplane_disabled`，不假装可用）。TEE 仍是可选属性（无 TEE = `tee=none` 裸 MicroVM，合法）。
>
> 下发/采购前先跑三行判定（只读，售前可用）：
>
> ```bash
> grep -c -m1 hypervisor /proc/cpuinfo        # >0 且下一行为 0 ⇒ 虚机且未透传，不能做计算节点
> grep -o -m1 -E "vmx|svm" /proc/cpuinfo      # 空 ⇒ 无虚拟化指令集；非空 ⇒ 可继续
> ls /dev/kvm 2>/dev/null                     # 缺失 ⇒ 先跑 sudo bash deploy/cube-node/preflight.sh 看完整清单
> ```

### 9.0 组件所有权边界（谁交付什么，升级谁负责）

| 组件 | 形态 | 交付任务 | 升级责任方 | 备注 |
|---|---|---|---|---|
| cubelet / shim / cube-runtime / CubeCoW / CubeVS / CubeEgress / guest 资产 / containerd | stock 单元（`cube-sandbox-compute.target`） | 上游 install-compute.sh | **上游**（零 diff 红线 1：CDS 不 patch、不自补安全补丁，铁律 6） | 升级=换钉住 bundle 重跑官方安装器 |
| ① cds-volume-plugin | 独立进程 + socket `/run/cds-volume-plugin/cds.sock` | CB-03 | CDS（插件镜像） | stock 配置仅增量注册（diff 留档 `stock-config-diff.log`） |
| ② TLS 前置代理 | `cds-tls-proxy.service`（stunnel mTLS） | CB-02 | CDS（单元+证书独立升级，不碰 stock） | sidecar 不可达 = 节点 down，无明文回退（§2.10） |
| ③ 审计 tailer | `cds-audit-tailer.service` | 单元 CB-02 / 二进制 CB-06 | CDS | 红线 5：审计不可丢弃 |

### 9.0.1 ① cds-volume-plugin 的构建前置：Tongsuo 源码取用

插件镜像的 `tongsuo` 阶段要 `COPY Tongsuo/` 编 `libcrypto.a`（SM4-GCM 静态助手），而 `Tongsuo/` 是**只钉 sha 的 gitlink**（`.gitmodules` + `160000 <sha>`，上游 8.5 树 406MB / 23,474 文件不入库），因此干净 clone 后该目录为空——构建前必须先取用：

```bash
scripts/prepare_tongsuo.sh                       # 默认：git submodule update --init --depth 1（上游 https）
TONGSUO_TARBALL=/root/thirdparty/tongsuo-<sha>.tar.gz scripts/prepare_tongsuo.sh   # 内网/离线源包
TONGSUO_GIT_URL=<内网 git 镜像> scripts/prepare_tongsuo.sh                            # 内网镜像
```

三条路径都以 `git ls-files -s Tongsuo` 的钉住 sha 为准；源包路径还要求 sha256 等于脚本内常量 `TONGSUO_TARBALL_SHA256`（**不可被环境变量覆盖**），不匹配即 fail-closed 且不落地任何源码树——避免用"手边随便一个版本"编出静默换实现的密码库。源包 = 钉住 sha 的 `git archive`（即上游 `util/mktar.sh` 的发布语义：不含 `fuzz/corpora/**` 与 dotfile，构建不需要），内网示例：`100.112.5.253:/root/thirdparty/tongsuo-baa1bb2b95f3.tar.gz`（53,609,980 B，sha256 `5b8483b1…d821005f`）。契约由 `tests/test_prepare_tongsuo.py` 钉住。

### 9.1 首次 bring-up

```bash
# 节点前置：/dev/kvm 可读写、XFS(reflink) 数据盘挂 /data/cubelet、eBPF 内核支持（§2.1）
sudo bash deploy/cube-node/preflight.sh     # 硬门禁：逐项 PASS/FAIL，任一 FAIL → exit 1
sudo CDS_CUBE_TLS_CERT=<cert_service 签发的 crt> \
     CDS_CUBE_TLS_KEY=<key> \
     CDS_CUBE_TLS_CA=<ca> \
     bash deploy/cube-node/install.sh       # preflight → 官方 install-compute.sh → 三 CDS 组件 → 状态落盘
bash deploy/cube-node/verify.sh --json      # 机读验收：cubelet gRPC / sidecar 证书 / 卷 socket / tailer
```

安装状态：`/var/lib/cds/cube-node/INSTALL_STATE.json`；stock 配置增量 diff 归档：同目录 `stock-config-diff.log`（红线 1 留档）。bring-up 后由 CB-01 客户端把 `runtime_version`（如 `v0.7.3-rc2+9b75d33907ed`）写入 `sandbox_nodes`；CDS 拒绝调度到版本不匹配节点（§5.2）。

### 9.2 preflight 失败分诊

| FAIL 行 | 含义 | 处置 |
|---|---|---|
| `FAIL: kvm` | `/dev/kvm` 缺失或权限不足 | 先分两种情形：**裸金属**→ 宿主机 BIOS 开虚拟化（VT-x/SVM），并确认 `lsmod` 有 `kvm_intel`/`kvm-amd`；**虚拟机**→ 不是 BIOS 问题，缺的是宿主侧嵌套虚拟化透传（AMD/海光需宿主 `kvm_amd.nested=1` 或平台开关，客户侧无法自助），须找云厂换裸金属/支持嵌套虚拟化的实例。判别：`grep -c hypervisor /proc/cpuinfo` > 0 且 `grep -oE "vmx\|svm" /proc/cpuinfo` 为空 ⇒ 就是这种情形（实测：海光 Hygon VM 32 核全带 `hypervisor` 标志、`svm` 为 0，dmesg `kvm: no hardware support`）。仅权限问题才处置：`usermod -aG kvm <user>` 或 root 运行（本工具链不代跑 sudo） |
| `FAIL: data_fs` | 数据盘非 XFS / 无 reflink / FICLONE 实测失败 | 专用 XFS 分区挂 `/data/cubelet`（`mkfs.xfs` + `mount`）；确认 `xfs_info` 输出 `reflink=1`；上游同类问题：CubeSandbox#311 |
| `FAIL: ebpf` | bpftool 缺失 / bpf syscall 被拒 / bpffs 未挂载 | 安装 bpf 工具集；检查 seccomp/AppArmor；`mount -t bpf bpf /sys/fs/bpf`（上游 troubleshooting: bpffs-is-not-mounted） |
| `FAIL: cubelet_bin` | 二进制版本与钉住 sha 不符 | 重跑 install.sh（bundle 换版）或恢复旧 bundle（§9.4 回滚）；**禁止手工替换二进制**（零 diff） |
| `FAIL: volume_sock` | CB-03 插件 socket 不在 | 部署/启动 cds-volume-plugin；未上 CB-03 前该节点不可承接加密卷会话（如实，不降级） |
| `FAIL: tls_sidecar` | 9443 不可达 | `systemctl status cds-tls-proxy`；证书缺失时 install.sh 会硬失败——向 certificate_service 重新签发后重跑（无明文回退路径） |
| `FAIL: containerd` / `FAIL: resources` | 依赖缺失 / 内存<8GB 或盘余量<10% | 安装 containerd；扩容（内存阈值与上游一致，可调 `CDS_CUBE_MIN_MEM_KB`，不得低于默认） |

### 9.3 滚动升级 SOP（版本钉住 + 冒烟 + 滚动，批次=1 起步）

1. **isolate**：CDS 侧 W14 节点运维 isolate 目标节点（node_selector 不再选它）
2. **排空**：存量会话 terminate 或 pause——CB-07（跨节点 resume）就绪前排空=**终止**（诚实：无迁移能力时不做假迁移）
3. **换 bundle**：更新钉住 sha（`scripts/cube-bundle-manifest.json` + `app/_stubs/cube/_VERSION.json` 经 CB-00 跟进流）→ 节点重跑 `install.sh`（`--mode` 走配置保留升级）
4. **verify**：`verify.sh --json` 全绿（cubelet gRPC 应答 + sidecar 证书 + 卷 socket + tailer）
5. **unisolate**：写新 `runtime_version` → W14 unisolate 恢复调度

**安全补丁强制流**：上游 SECURITY release → **48h 内**完成滚动，豁免 isolate 等待窗口（§5.3 豁免等待；M1 收口前演练一次）。

### 9.4 回滚

- 回滚 = **旧 bundle 重钉**：恢复旧钉住 sha 的 manifest → 重跑 `install.sh`（上游安装器幂等）→ `verify.sh` → 恢复 `runtime_version` → unisolate
- stock 配置增量（volume_plugins 段）由 `install.sh` 幂等重放恢复（§5.4）；回滚不产生手工痕迹
- 最坏情况 = 钉版本等待（运维债，非代码债；§6.6 风险表）

### 9.5 验收与 CI

- 工具链冒烟：`.github/workflows/cube-node-smoke.yml`（`workflow_dispatch` 手动，self-hosted `kvm` 标签 runner；普通 CI 不运行）
- 单元级：`pytest tests/test_cube_node_tooling.py`（mock fixture，PASS/FAIL 矩阵）
- 真实节点全链路：FG-017（环境验收，不伪造完成）
