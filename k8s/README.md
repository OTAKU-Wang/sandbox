# k8s/ — k3s 原生清单部署路径

`bash k8s/deploy.sh`（自动优先 `k3s kubectl`）按序 apply **10** 个清单：
namespace → secrets → configmaps → postgres → redis → minio → vault →
clickhouse → opa → cds-app（含 cds-migrate Job），随后 exec
`scripts/vault-init.sh` 进 vault Pod。脚本尾部每个 `kubectl wait` 都带
`|| true`——"=== CDS Infrastructure Deployed ===" 不等于成功，部署后逐项检查。

## 部署前必读：secrets.yaml 是模板

`secrets.yaml`（P6 起）全部凭据值为 `<REPLACE_ME_*>` 占位符。DEV 冒烟可以
原样 apply（同 Secret 内部自洽）；**任何真实环境必须全部替换为随机值**
（`openssl rand -hex 32`），或整体改走 existingSecret / Vault / 外部注入。
键集（16 键）与 helm `values.yaml` 的 `secrets.existingSecret` 注释同步；
`CDS_COLUMN_ENCRYPTION_KEY` / `CDS_MFA_SECRET_ENCRYPTION_KEY` 为 boot 门位
强制密钥（prod 姿态空值拒绝启动），已以 `secretKeyRef` 接入 cds-api env。

## 配置单一真值源（2026-10-30 P6 收敛，测试钉死）

| 键 | 真值源 | 说明 |
|----|--------|------|
| Postgres 应用用户 | `cds` | 与 compose×3 / helm bitnami auth / `scripts/init-db.sql` GRANT 同值。存量 dev 卷（历史 initdb 出 `cds_admin`）apply 前先 `ALTER USER cds_admin RENAME TO cds;`，否则 `pg_isready` 探针对旧卷必红。 |
| `CDS_LOG_JSON` | `"true"` | 生产姿态四副本（prod compose / helm values / k8s cds-app-env / remote_deploy.sh）由 `test_deploy_manifest_gates` PROD_POSTURE 门钉同值；dev compose / local_deploy 是记录在案的宽松孪生，不在此门。 |
| MinIO 镜像 | `minio/minio:RELEASE.2025-01-20T14-49-07Z` | 七副本（compose×3 / k8s / helm values / deploy 脚本×2）同 tag；`:latest` 禁止入库。升级归属 = 发布流程人工评审，一把同升，不做单副本漂移升级。 |

## CDC：有意手动（不是遗漏）

`kafka.yaml` / `debezium.yaml`（zookeeper + kafka + debezium-connect）**不在
deploy.sh 的 apply 集**——CDC 是 opt-in：默认拉起会白付三组 JVM 内存，且
connector 配置属部署方决策。需要时按序手动：

```bash
kubectl apply -f k8s/kafka.yaml       # 先等 zookeeper/kafka Ready
kubectl apply -f k8s/debezium.yaml    # connect + connector 注册
# 把 cds-secrets 的 CDS_CDC_KAFKA_BROKERS 设为 "kafka:9092"（模板默认已是该值）
kubectl rollout restart deploy/cds-api -n cds-system
```

反向关闭：删 connect/connector，把 `CDS_CDC_KAFKA_BROKERS` 置空并重启 cds-api。

## 其它

- 沙箱 Pod 在 ns `cds-sandbox`（`scripts/e2e-k8s.sh`），基础设施在 `cds-system`；
  Cube 计算节点**永不**进 `cds-system`（runbook §5.1 红线 2，走 `deploy/cube-node/`）。
- `registries.yaml` 是 CN 镜像加速配置，装到 `/etc/rancher/k3s/registries.yaml`。
- ClickHouse 权威 schema 是 `scripts/clickhouse-init.sql`（~40 列）；
  `configmaps.yaml` 内嵌的是 k3s 精简副本，改动时两份都要看。
