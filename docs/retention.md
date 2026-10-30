# 数据保留与 GC（W12 · retention janitor）

`app/services/retention_janitor.py` 随会话清理循环（默认 300s）对增长表执行有界、
可配置、可审计的保留清理。

## 配置（CDS_ 前缀）

| 配置 | 默认 | 含义 |
|---|---|---|
| `RETENTION_JANITOR_ENABLED` | `true` | 总开关 |
| `AUDIT_LOG_RETENTION_DAYS` | `0`（永留） | 审计日志是合规证据，默认永不清理 |
| `MERKLE_LEAF_RETENTION_DAYS` | `0`（永留） | 存证 Merkle 叶子支撑证明包验证，默认永不清理 |
| `ALERT_RETENTION_DAYS` | `180` | 告警记录保留天数 |
| `RETENTION_JANITOR_DRY_RUN` | `true` | **演练模式**：只统计不删除 |
| `RETENTION_JANITOR_BATCH` | `1000` | 单批删除行数（有界事务） |

## 从演练切切实删（合规审批建议流程）

1. 保持 dry-run 至少一个观察周期，从 API 日志/审计确认候选行数符合预期；
2. 变更管理审批（注明表、保留天数、预计删除量）；
3. 设置 `CDS_RETENTION_JANITOR_DRY_RUN=false` 滚动重启；
4. 首个实删周期后核对审计事件 `retention.purge` 的 `detail.results` 与行数一致；
5. `0` 值的表（audit/merkle）任何情况下都不会被清理——不要为了省空间改为有限保留，
   如确有合规要求，走导出归档而非删除。

## 多副本

PostgreSQL 环境使用 `pg_try_advisory_lock('cds_retention_janitor')` 保证同一时刻只有
一个副本执行；SQLite（dev/test）单进程，跳过锁。

---

## 2026-10-30 拍板：白名单真删（用户决定，追加不重写上文）

范围拍板：**janitor 先只对「确定安全的类别」真删**。类别白名单写死在代码常量
`app/services/retention_janitor.py::REAL_DELETE_WHITELIST`，配置只能从中选子集，
扩张不了任何东西。

### 白名单类别（唯一可被真删的集合）

| 类别 | 内容 | 保留天数配置 |
|---|---|---|
| `alert_records` | 告警记录（DB，既有行为） | `CDS_ALERT_RETENTION_DAYS`（默认 180） |
| `session_workspace_residue` | 会话临时文件/工作区残留：`/tmp/cds-sandbox`、`/tmp/cds-sandbox-l0`、`/tmp/cds-firecracker` 固定根下、**整棵子树** mtime 都过期的孤儿目录（根路径写死在代码，不来自配置；名字含 audit 的目录、符号链接、散文件一律不碰） | `CDS_WORKSPACE_RESIDUE_RETENTION_DAYS`（默认 30） |
| `dev_debug_artifacts` | 过期的开发调试产物：供数明文暂存 `/tmp/cds-sandbox/data-load`、流水线工作区 `/tmp/cds-pipeline` | `CDS_DEV_ARTIFACT_RETENTION_DAYS`（默认 14） |

### 永久保留重申（不可被任何配置翻转的铁律）

`audit_logs`（审计日志）、`merkle_leaves`（Merkle 存证叶子）、`delivery_bundles`
（交付物 bundle）、`data_resources`（原始数据资源）属于
`retention_janitor.NEVER_DELETE_CATEGORIES`：**执行器硬守卫
`_require_deletable()` 在发出任何 SELECT/DELETE 之前拒删**，即便把
`CDS_AUDIT_LOG_RETENTION_DAYS` / `CDS_MERKLE_LEAF_RETENTION_DAYS` 改成正数也一样
——结果恒为 -1（拒删）并写入审计记录 `retention.purge` 的
`protected_refusals`。retention 0 = 永远；需要腾空间走 **export-and-archive**，
不是删除。这四类同样不允许出现在 `CDS_JANITOR_CATEGORIES` 里（见下）。

### 开关（默认姿态全关）

| 配置 | 默认 | 含义 |
|---|---|---|
| `CDS_JANITOR_ENABLED` | `false` | **真删主开关**，生产默认关闭；会话清理循环要求它与兼容副闸 `CDS_RETENTION_JANITOR_ENABLED` 同时为 true 才调度 janitor |
| `CDS_JANITOR_CATEGORIES` | `""`（=全部白名单类别） | 白名单子集。**选集外（含上面四类受保护类别）= 启动报错 fail-closed**，任何环境都拒（不是生产专属） |
| `CDS_RETENTION_JANITOR_DRY_RUN` | `true` | dry-run 仍是默认行为：只统计不删除 |

四处部署副本（prod compose / dev compose / k8s `cds-app-env` ConfigMap / helm
values，外加 `deploy/local`、`deploy/e2e-remote` 两个镜像脚本）都钉
`CDS_JANITOR_ENABLED="false"`，由 `tests/test_deploy_manifests.py` 门禁看住；
白名单/硬拒/默认值由 `tests/test_janitor_real_delete.py` 钉住（含「无论怎么配置
都不删」四类各一条）。

### 切实删流程（更新版）

1. 保持默认姿态（`CDS_JANITOR_ENABLED=false` + dry-run）至少一个观察周期；
2. 变更管理审批（注明**白名单内**类别、保留天数、预计删除量）；
3. 设 `CDS_JANITOR_ENABLED=true`（必要时 `CDS_JANITOR_CATEGORIES` 收窄类别子集、
   `CDS_RETENTION_JANITOR_DRY_RUN=false`）滚动重启；
4. 首个实删周期核对 `retention.purge` 审计事件的 `detail.results` 与
   `detail.protected_refusals`；受保护类别出现任何非 -1 的值都视为守卫失效，
   立即回滚并上报。
