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
