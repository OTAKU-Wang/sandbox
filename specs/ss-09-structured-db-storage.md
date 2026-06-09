# SS-09：结构化数据安全存储 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-09  
> 核心场景：数据库形态的结构化数据产品，从数商源库→沙箱内加密库的全链路安全存储  
> 上游：SS-01（密钥）、SS-02（合约/策略）  
> 下游：SS-04（沙箱运行时读取）、SS-05（输出审查）、SS-06（审计）

---

## 1. 场景定位与问题空间

### 1.1 与文件形态结构化数据的区别

| 维度 | 文件形态（CSV/Parquet） | 数据库形态（本分系统） |
|------|------------------------|----------------------|
| 数据来源 | 数商离线导出的文件 | 数商生产数据库（实时/定期同步） |
| 更新方式 | 全量替换 | CDC 增量同步（支持实时数据产品） |
| 查询能力 | DuckDB 有限 SQL | 完整关系型 DB 能力（事务/索引/存储过程）|
| 数据规模 | 通常 < 100GB | 可达 TB 级（分库分表场景）|
| 应用集成 | 脚本/Notebook | 应用程序直连 DB（JDBC/libpq）|
| 加密粒度 | 文件级 | 列级 + 行级 + 表空间级 |
| 持久化 | 无（任务结束销毁）| 有（沙箱内 DB 跨会话持久，DEK 保护）|

### 1.2 支持的数商源数据库类型

| 源库类型 | 采集协议 | 国产替代 | 备注 |
|----------|----------|----------|------|
| PostgreSQL 12+ | pgoutput / pglogical | 瀚高 DB / PolarDB | WAL 级 CDC |
| MySQL 8.0+ | binlog（ROW 格式） | OceanBase / TiDB | binlog 解析 |
| Oracle 11g+ | LogMiner / OGG | 达梦 DM8 | 需授权 |
| SQL Server 2016+ | CDC / CT | — | Windows 环境 |
| 达梦 DM8 | DMHS 同步 | 国产首选 | 政务场景常见 |
| OceanBase 3.x+ | OMS 同步 | — | 分布式 |
| 人大金仓 KingbaseES | KSYNC | 国产替代 PG | 政务数据库 |

---

## 2. 整体数据流架构

```
数商源数据库（生产环境，数商域）
    │
    │ ① 安全采集层（数商侧部署，最小权限）
    │   ・仅读权限账号（SELECT + REPLICATION）
    │   ・CDC Agent（Debezium 国产化版本）
    │   ・数据在离开数商域前 SM4 加密
    │
    ▼
    ② 安全传输（TLCP 专线 / 加密队列）
    Kafka（SM4 加密消息）/ 安全文件传输
    │
    ▼
    ③ 沙箱数据接入网关（数据空间侧）
    ・消息解密（用中间密钥，不含最终 DEK）
    ・Schema 注册与版本管理
    ・数据质量检测
    ・敏感字段预标注
    │
    ▼
    ④ 沙箱内加密数据库（TEE 内）
    ・PostgreSQL-in-TEE / DuckDB-persistent
    ・列级 SM4 加密（高敏感字段）
    ・行级安全策略（RLS）
    ・表空间级 pg_tde 透明加密
    │
    ▼
    ⑤ 数据访问层（SS-04 沙箱运行时）
    ・SQL 查询引擎（受策略控制）
    ・应用程序 JDBC/libpq 连接（代理模式）
    ・建模/训练 DataLoader
    │
    ▼
    ⑥ 输出审查网关（SS-05）
```

---

## 3. 安全采集层（数商侧 CDC Agent）

### 3.1 Debezium 国产化 CDC 配置

```java
// Debezium Source Connector 配置（数商侧部署）
// 对应数商 PostgreSQL 源库
{
  "name": "cds-pg-source-connector",
  "config": {
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "database.hostname": "prod-db.provider.internal",
    "database.port": "5432",
    "database.user": "cds_replication_ro",        // 只读复制账号
    "database.password": "${env:DB_REPL_PASSWORD}",
    "database.dbname": "business_db",
    "plugin.name": "pgoutput",
    "slot.name": "cds_replication_slot_001",
    "publication.name": "cds_publication",
    "table.include.list": "public.customers,public.transactions",
    "column.exclude.list": "public.customers.password_hash", // 排除绝对不允许的字段

    // 敏感字段脱敏（发送前，在数商侧执行）
    "transforms": "FieldMasking",
    "transforms.FieldMasking.type":
        "io.debezium.transforms.MaskField$Value",
    "transforms.FieldMasking.fields": "customers.id_card,customers.phone",
    "transforms.FieldMasking.replacement": "MASKED",  // 粗脱敏，细脱敏在沙箱内

    // 输出：Kafka（消息加密）
    "database.history.kafka.bootstrap.servers": "kafka.cds.internal:9093",
    "database.history.kafka.topic": "dbhistory.business_db",

    // SM4 加密（自定义 Kafka Serializer）
    "value.converter": "io.cds.kafka.SM4EncryptedJsonConverter",
    "value.converter.sm4.key.id": "intermediate_key_001",  // 中间密钥（非 DEK）
    "value.converter.sm4.kms.endpoint": "https://kms.cds.internal",
  }
}
```

### 3.2 数据采集权限最小化

```sql
-- 在数商源数据库上创建只读复制账号（数商 DBA 执行）

-- PostgreSQL
CREATE ROLE cds_replication_ro REPLICATION LOGIN PASSWORD '${strong_password}';
GRANT CONNECT ON DATABASE business_db TO cds_replication_ro;
GRANT USAGE ON SCHEMA public TO cds_replication_ro;
-- 仅授权允许纳入数据产品的表
GRANT SELECT ON TABLE public.customers TO cds_replication_ro;
GRANT SELECT ON TABLE public.transactions TO cds_replication_ro;
-- 禁止访问其他表
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cds_replication_ro;
GRANT SELECT ON TABLE public.customers, public.transactions TO cds_replication_ro;

-- 创建 Publication（仅包含授权表，列级过滤）
CREATE PUBLICATION cds_publication
FOR TABLE
  public.customers (id, name, age_group, region, credit_tier),  -- 仅允许列
  public.transactions (id, amount_range, category, ts_day)       -- 日期只到天
WITH (publish = 'insert, update, delete');

-- 达梦 DM8 等效配置
-- CREATE USER cds_sync IDENTIFIED BY '${password}';
-- GRANT SELECT ON TABLE SYSDBA.CUSTOMERS TO cds_sync;
```

### 3.3 CDC 事件 Schema（Kafka 消息格式）

```json
{
  "metadata": {
    "connector": "debezium-pg-v2.5",
    "source_db": "business_db",
    "source_table": "customers",
    "op": "u",
    "ts_ms": 1717200000000,
    "lsn": "0/1A2B3C4D",
    "encryption": {
      "algorithm": "SM4-GCM",
      "key_id": "intermediate_key_001",
      "iv": "hex_encoded_iv"
    }
  },
  "before": null,
  "after_encrypted": "base64_encoded_sm4_gcm_ciphertext",
  "schema_version": "v3",
  "checksum_sm3": "hex_encoded_sm3_of_plaintext_after"
}
```

---

## 4. 沙箱内加密数据库详细设计

### 4.1 三层加密体系

```
┌──────────────────────────────────────────────────────────────────┐
│           沙箱内 PostgreSQL（PG-in-TEE）三层加密                   │
│                                                                  │
│  Layer 3：TEE 硬件内存加密（L1 自动，L2 软件模拟）                 │
│  ──────────────────────────────────────────────────────────────  │
│  Layer 2：表空间级透明加密（pg_tde 扩展）                          │
│    ・整个表空间的所有数据文件、索引文件、WAL 使用 SM4-XTS 加密      │
│    ・加密密钥（TDE Key）由 SS-01 KMS 管理，沙箱启动时注入          │
│    ・对 SQL 执行引擎完全透明，无需修改查询                          │
│  ──────────────────────────────────────────────────────────────  │
│  Layer 1：列级加密（pgcrypto + 自定义 SM4 函数）                  │
│    ・高敏感字段（身份证、手机、银行卡）额外使用列加密               │
│    ・即使 TDE 密钥泄露，列级密文仍然安全                           │
│    ・支持等值查询（确定性加密）和范围查询（OPE 顺序保留加密，限场景）│
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 表空间级透明加密（pg_tde）

```sql
-- PG-in-TEE 初始化脚本（沙箱启动时自动执行）
-- DEK 以 hex 形式从 TEE 内存注入，不从配置文件读取

-- 1. 安装 pg_tde 扩展
CREATE EXTENSION IF NOT EXISTS pg_tde;

-- 2. 注册 KMS 密钥提供者（对接 SS-01 KMS）
SELECT pg_tde_add_key_provider_http(
    'cds_kms_provider',
    'https://kms.cds.internal/v1/tde',    -- KMS 端点
    current_setting('sandbox.session_token')  -- 会话令牌（TEE 内存注入）
);

-- 3. 设置默认加密密钥（TDE Master Key）
SELECT pg_tde_set_default_key('sandbox_tde_key', 'cds_kms_provider');

-- 4. 创建加密表空间
CREATE TABLESPACE encrypted_ts
    LOCATION '/mnt/sandbox_db/tablespace'
    WITH (pg_tde.key_name = 'sandbox_tde_key');

-- 5. 所有数据产品表建在加密表空间
SET default_tablespace = 'encrypted_ts';
```

### 4.3 列级加密（高敏感字段）

```sql
-- 创建 SM4 加密/解密函数（基于 pgcrypto + 自定义 C 扩展）
CREATE OR REPLACE FUNCTION sm4_encrypt(plaintext TEXT, key_id TEXT)
RETURNS BYTEA
LANGUAGE C STRICT SECURITY DEFINER AS 'cds_sm4', 'sm4_encrypt_col';

CREATE OR REPLACE FUNCTION sm4_decrypt(ciphertext BYTEA, key_id TEXT)
RETURNS TEXT
LANGUAGE C STRICT SECURITY DEFINER AS 'cds_sm4', 'sm4_decrypt_col';

-- 等值查询用确定性加密（AES-SIV 模式，同明文→同密文）
CREATE OR REPLACE FUNCTION sm4_det_encrypt(plaintext TEXT, key_id TEXT)
RETURNS BYTEA
LANGUAGE C STRICT SECURITY DEFINER AS 'cds_sm4', 'sm4_det_encrypt_col';

-- 示例：包含敏感列的客户表
CREATE TABLE data_products.customers (
    id              BIGINT NOT NULL,
    -- 普通字段（TDE 保护，无列加密）
    age_group       TEXT,
    region_code     TEXT,
    credit_tier     SMALLINT,
    -- 高敏感字段（列加密 + TDE 双重保护）
    id_card_enc     BYTEA,        -- sm4_det_encrypt(id_card, 'col_key_001')
    phone_enc       BYTEA,        -- sm4_det_encrypt(phone, 'col_key_001')
    real_name_enc   BYTEA,        -- sm4_encrypt(real_name, 'col_key_001')（非确定性）
    -- 衍生字段（供分析使用，已脱敏）
    id_card_prefix  CHAR(6),      -- 身份证前6位（地区码，非敏感）
    phone_suffix    CHAR(4),      -- 手机后4位
    created_date    DATE,
    PRIMARY KEY (id)
) TABLESPACE encrypted_ts;

-- 买方视图：自动解密展示（根据合约字段 ACL 决定是否解密）
CREATE OR REPLACE VIEW data_products.customers_masked AS
SELECT
    id,
    age_group,
    region_code,
    credit_tier,
    id_card_prefix,
    phone_suffix,
    -- 高敏感字段：仅在合约允许且买方有列密钥权限时解密
    CASE
        WHEN current_setting('sandbox.col_decrypt_allowed', true) = 'true'
        THEN sm4_decrypt(id_card_enc, 'col_key_001')
        ELSE '***MASKED***'
    END AS id_card_masked,
    created_date
FROM data_products.customers;
```

### 4.4 行级安全策略（RLS）

```sql
-- 基于合约约束的行级过滤策略

-- 策略 1：地域过滤（合约限定买方只能访问特定地区数据）
ALTER TABLE data_products.customers ENABLE ROW LEVEL SECURITY;
CREATE POLICY region_filter ON data_products.customers
    USING (
        region_code = ANY(
            string_to_array(
                current_setting('sandbox.allowed_regions', true),
                ','
            )
        )
    );

-- 策略 2：时间窗口过滤（合约限定只能访问某时间范围数据）
CREATE POLICY time_window_filter ON data_products.transactions
    USING (
        ts_day BETWEEN
            current_setting('sandbox.data_start_date')::DATE AND
            current_setting('sandbox.data_end_date')::DATE
    );

-- 策略 3：数据分级过滤（高敏感等级数据需特殊合约权限）
CREATE POLICY sensitivity_filter ON data_products.customers
    USING (
        data_sensitivity_level <=
        current_setting('sandbox.max_sensitivity_level')::INT
    );

-- 在会话初始化时设置策略参数（来自合约策略包）
-- 沙箱启动代码执行：
-- SET sandbox.allowed_regions = 'CN-SH,CN-BJ,CN-GD';
-- SET sandbox.data_start_date = '2024-01-01';
-- SET sandbox.data_end_date = '2024-12-31';
-- SET sandbox.max_sensitivity_level = '2';
```

---

## 5. CDC 增量同步流水线

### 5.1 流水线状态机

```
源库变更事件（INSERT/UPDATE/DELETE）
         │
         ▼
[Debezium CDC Agent（数商侧）]
  ・捕获 WAL/binlog 变更
  ・应用数商侧粗脱敏（排除绝对禁止字段）
  ・SM4 加密（中间密钥）
  ・发送至 Kafka
         │
         ▼
[接入网关（数据空间侧）]
  ├─ Schema Registry 检查（字段变更检测）
  ├─ 中间密钥解密
  ├─ 数据质量检测（NULL 率、格式校验）
  ├─ 敏感字段精细脱敏（按数据产品策略）
  └─ DEK 重加密（最终数据 DEK）
         │
         ▼
[沙箱内 DB Writer（TEE 内运行）]
  ├─ DEK 解密获取明文
  ├─ 列级 SM4 加密（高敏感字段）
  ├─ UPSERT 至 PG-in-TEE
  └─ 更新 LSN/位点（用于断点续传）
         │
         ▼
[数据就绪通知]
  ・更新产品目录（数据版本、时间戳、行数统计）
  ・触发等待数据的沙箱会话（若有）
```

### 5.2 Schema 变更处理

```python
class SchemaEvolutionHandler:
    """
    处理源库 Schema 变更（加字段、改类型、删字段）
    核心原则：Schema 变更不能破坏正在运行的沙箱会话
    """

    def handle_schema_change(self, change: SchemaChangeEvent):
        current = schema_registry.get_current(change.table_name)
        proposed = change.new_schema

        diff = compute_schema_diff(current, proposed)

        for change_item in diff:
            if change_item.type == "ADD_COLUMN":
                # 向前兼容：新列对旧会话不可见（需合约更新）
                self._add_column_shadow(change_item)

            elif change_item.type == "DROP_COLUMN":
                # 危险操作：需通知所有活跃会话并等待结束
                active_sessions = session_service.get_active_for_product(
                    change.product_id)
                if active_sessions:
                    raise SchemaChangePending(
                        f"Cannot drop column while {len(active_sessions)} sessions active. "
                        f"Scheduled for: {change_item.column}"
                    )
                self._drop_column_with_audit(change_item)

            elif change_item.type == "CHANGE_TYPE":
                # 类型兼容性检查
                if not is_compatible_type_change(change_item.old_type,
                                                  change_item.new_type):
                    raise IncompatibleSchemaChange(
                        f"Incompatible type change: {change_item.old_type} → "
                        f"{change_item.new_type} for column {change_item.column}"
                    )
                self._apply_type_change(change_item)

        # 注册新 Schema 版本
        schema_registry.register(change.table_name, proposed,
                                   version=current.version + 1)
        audit_service.record("schema.changed", change)
```

### 5.3 断点续传与位点管理

```python
class CDCSyncCheckpointer:
    """
    CDC 同步位点管理：支持断点续传（网络中断/节点重启场景）
    存储介质：PostgreSQL（元数据库，TEE 外）+ 沙箱内备份（TEE 内）
    """

    def save_checkpoint(self, product_id: str, lsn: str, event_count: int):
        """保存当前同步位点"""
        with db.transaction():
            db.upsert("sync_checkpoints", {
                "product_id": product_id,
                "last_lsn": lsn,
                "last_event_count": event_count,
                "checkpoint_at": datetime.utcnow(),
                "checksum": sm3_hash(f"{product_id}:{lsn}:{event_count}".encode()).hex()
            }, conflict_target=["product_id"])

    def get_resume_lsn(self, product_id: str) -> str | None:
        """获取上次中断的位点（用于重启后续传）"""
        row = db.get("sync_checkpoints", product_id=product_id)
        if not row:
            return None

        # 完整性校验（防止位点被篡改导致数据重复/跳过）
        expected = sm3_hash(
            f"{row.product_id}:{row.last_lsn}:{row.last_event_count}".encode()
        ).hex()
        if row.checksum != expected:
            raise CheckpointTampered(product_id)

        return row.last_lsn
```

---

## 6. 数据库形态产品的跨会话持久化

### 6.1 持久化策略

与文件形态数据（每次会话重新加载）不同，数据库形态产品在沙箱内持久存储，支持多个会话共享同一个数据库实例：

```
沙箱内持久化 DB 生命周期：

  数据产品发布 → 创建沙箱 DB 实例（初始全量同步）
       │
       ├── 会话 A 创建（连接已有 DB）
       │       │ 任务执行
       │       └── 会话 A 结束（DB 不销毁，只清理临时表）
       │
       ├── 会话 B 创建（连接同一 DB，看到最新数据）
       │       │ CDC 增量更新持续进行
       │       └── 会话 B 结束
       │
       ├── CDC 实时更新（持续）
       │
       └── 数据产品下架 / DEK 吊销 → DB 加密销毁
```

```python
class PersistentSandboxDB:
    """
    持久化沙箱数据库管理
    多会话共享，但每个会话有独立的 schema（防会话间干扰）
    """

    def get_or_create_db(self, product_id: str, dek: bytes) -> DBConnection:
        db_path = f"/mnt/sandbox_persistent/{product_id}/db"

        if not os.path.exists(db_path):
            # 首次创建：初始全量同步
            self._create_encrypted_db(db_path, dek)
            self._initial_full_sync(product_id, db_path, dek)
        else:
            # 已存在：验证 DEK 匹配（防止使用错误密钥）
            self._verify_db_key(db_path, dek)

        conn = self._connect_pg(db_path, dek)
        return conn

    def create_session_schema(self, conn: DBConnection, session_id: str):
        """
        为每个会话创建独立 schema（隔离会话间的临时表/视图）
        只读数据共享（data_products schema），读写隔离（session schema）
        """
        schema_name = f"session_{session_id.replace('-', '_')}"
        conn.execute(f"CREATE SCHEMA {schema_name}")
        conn.execute(f"SET search_path = {schema_name}, data_products")

        # 给会话 schema 设置 DROP 触发器（会话结束自动清理）
        conn.execute(f"""
            CREATE OR REPLACE FUNCTION cleanup_{schema_name}()
            RETURNS VOID AS $$
            BEGIN
                DROP SCHEMA IF EXISTS {schema_name} CASCADE;
            END;
            $$ LANGUAGE plpgsql;
        """)

    def cleanup_session_schema(self, conn: DBConnection, session_id: str):
        """会话结束时清理临时 schema（不影响持久数据）"""
        schema_name = f"session_{session_id.replace('-', '_')}"
        conn.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")

    def _create_encrypted_db(self, db_path: str, dek: bytes):
        """
        创建 pg_tde 透明加密数据库
        DEK 通过 TEE 内存安全注入，不落磁盘
        """
        os.makedirs(db_path, exist_ok=True)
        # 初始化 PG 数据目录
        subprocess.run([
            "initdb", "-D", db_path,
            "--auth=scram-sha-256",
            "--no-instructions",
        ], check=True)

        # 配置 pg_tde（在 postgresql.conf 中）
        pg_conf = Path(db_path) / "postgresql.conf"
        pg_conf.write_text(pg_conf.read_text() + f"""
# CDS TDE Configuration
shared_preload_libraries = 'pg_tde'
pg_tde.auto_encrypt = on
pg_tde.default_key_name = 'sandbox_tde_key'
""")
```

---

## 7. 数据库安全访问代理（应用场景）

当买方应用程序需要通过 JDBC/libpq 连接沙箱内 DB 时，通过数据访问代理拦截所有查询，进行策略执行和审计：

```python
class DBAccessProxy:
    """
    数据库访问代理：位于应用进程和 PG-in-TEE 之间
    拦截所有 SQL，执行策略检查，记录审计日志
    协议：PostgreSQL Wire Protocol (PGWP) 代理
    """

    def __init__(self, listen_port: int, pg_conn: str, policy_engine: PolicyDecisionPoint):
        self.server = PGWireProxyServer(listen_port)
        self.pg = pg_conn
        self.policy = policy_engine

    async def handle_query(self, query_msg: PGQueryMessage) -> PGResponse:
        sql = query_msg.query

        # 1. SQL 注入检测
        if sql_injection_detector.is_suspicious(sql):
            audit_service.record("security.sql_injection_attempt", sql=sql)
            return PGResponse.error("Query rejected by security policy")

        # 2. 策略评估
        decision = self.policy.evaluate(PolicyRequest(
            action_type="SQL_EXECUTE",
            sql=sql,
            accessed_tables=extract_tables(sql),
            accessed_columns=extract_columns(sql),
        ))
        if not decision.allowed:
            return PGResponse.error(f"Policy denied: {decision.reasons}")

        # 3. SQL 改写（强制注入 RLS 配合设置）
        rewritten = self._inject_rls_settings(sql)

        # 4. 执行并计时
        start = time.monotonic()
        result = await self.pg.execute(rewritten)
        elapsed_ms = (time.monotonic() - start) * 1000

        # 5. 结果路由至输出审查（应用场景允许行级数据，但须 PII 脱敏）
        inspected = await output_gateway.inspect_app_result(
            result, session_id=self.current_session_id
        )

        # 6. 审计
        audit_service.record("data.sql.executed",
                              sql_truncated=sql[:200],
                              rows_returned=len(result),
                              elapsed_ms=elapsed_ms)

        return PGResponse.ok(inspected)

    def _inject_rls_settings(self, sql: str) -> str:
        """
        在每个查询前注入 SET 语句，确保 RLS 策略使用最新合约参数
        防止应用绕过 RLS（通过缓存旧参数）
        """
        prefix = f"""
SET sandbox.allowed_regions = '{self.contract.allowed_regions}';
SET sandbox.data_start_date = '{self.contract.data_start_date}';
SET sandbox.data_end_date = '{self.contract.data_end_date}';
"""
        return prefix + sql
```

---

## 8. 数据库安全销毁

```python
class SecureDBDestructor:
    """
    数据产品下架或 DEK 吊销时，安全销毁沙箱内 DB
    确保：密钥擦除 → 数据覆写 → 文件删除 → 审计
    """

    def destroy(self, product_id: str, reason: str):
        db_path = f"/mnt/sandbox_persistent/{product_id}/db"

        # 1. 通知所有活跃会话立即断开（宽限期 30s）
        active_sessions = session_service.get_by_product(product_id)
        for sess in active_sessions:
            session_service.graceful_shutdown(sess.session_id, timeout_s=30)

        # 2. 停止 CDC 同步
        cdc_manager.stop_sync(product_id)

        # 3. 停止 PG 实例
        subprocess.run(["pg_ctl", "stop", "-D", db_path, "-m", "immediate"])

        # 4. 使用 shred 覆写数据文件（3 遍：0x00/0xFF/随机）
        for dirpath, _, filenames in os.walk(db_path):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                subprocess.run(["shred", "-u", "-z", "-n", "3", fpath])

        # 5. 删除目录
        shutil.rmtree(db_path, ignore_errors=True)

        # 6. 通知 KMS 吊销 TDE 密钥（即使文件未完全覆写，密钥失效即不可读）
        kms_service.revoke_tde_key(product_id)

        audit_service.record("data.db.destroyed", product_id=product_id, reason=reason)
```

---

## 9. 数据库形态产品元数据

```yaml
DataProduct:
  productId: "dp-db-001"
  dataCategory: "structured"
  dataFormat: "database"
  databaseConfig:
    sourceDbType: "postgresql"              # postgresql|mysql|oracle|dm8|kingbase
    sourceVersion: "15.3"
    syncMode: "cdc_realtime"               # cdc_realtime|cdc_batch|snapshot_only
    syncIntervalMinutes: null              # cdc_realtime 时为 null
    persistentSandboxDb: true             # 沙箱内持久化 DB
    sandboxDbEngine: "postgresql15"        # 沙箱内使用的 DB 引擎
    tdeEnabled: true
    schemaVersion: "v5"

  tables:
    - tableName: "customers"
      rowCount: 5000000
      sizeGB: 8.5
      columns:
        - name: "id"
          type: "bigint"
          sensitivity: "low"
          columnEncrypted: false
        - name: "id_card"
          type: "text"
          sensitivity: "critical"
          columnEncrypted: true
          encryptionMode: "deterministic"   # 支持等值查询
          buyerVisible: false               # 任何合约均不可见原文
        - name: "phone"
          type: "text"
          sensitivity: "high"
          columnEncrypted: true
          encryptionMode: "deterministic"
          buyerVisible: false
        - name: "age_group"
          type: "text"
          sensitivity: "low"
          columnEncrypted: false
          buyerVisible: true

  rlsPolicies:
    - policyName: "region_filter"
      column: "region_code"
      contractParam: "allowed_regions"
    - policyName: "time_window"
      column: "created_date"
      contractParam: "data_date_range"

  allowedSandboxModes:
    - "structured_query"
    - "structured_modeling"
    - "structured_app"
    - "product_dev"                        # 数商可在开发沙箱开发此产品

  minimumSandboxLevel: "L1"
```

---

## 10. 配置参数

```yaml
structured_db_storage:
  cdc:
    debezium_version: "2.5.3"
    kafka_bootstrap: "kafka.cds.internal:9093"
    kafka_sasl_mechanism: "SCRAM-SHA-256"
    kafka_ssl_enabled: true
    schema_registry_url: "https://schema-registry.cds.internal"
    batch_size: 500
    batch_timeout_ms: 5000

  sync_checkpoint:
    save_every_events: 1000
    save_every_seconds: 30
    storage: "postgresql"

  sandbox_db:
    pg_version: "15"
    pg_tde_version: "1.1"
    max_connections: 100                   # 单实例最大连接数
    shared_buffers: "2GB"
    work_mem: "256MB"
    persistent_volume_path: "/mnt/sandbox_persistent"
    volume_encryption: "dm_crypt"          # L2 用 dm-crypt；L1 用 EPC

  column_encryption:
    sm4_extension: "cds_sm4.so"
    deterministic_mode: "SM4-SIV"         # 确定性加密：AES-SIV 国密版
    non_deterministic_mode: "SM4-GCM"

  destruction:
    shred_passes: 3
    graceful_shutdown_timeout_s: 30
    audit_retention_days: 365

  performance:
    cdc_lag_alert_ms: 5000                # CDC 延迟告警阈值
    max_db_size_gb: 500                   # 单数据产品最大 DB 大小
    vacuum_schedule: "0 2 * * *"          # 每天凌晨 2 点 VACUUM
```

---

*文档：SS-09 | 版本：v1.0 | 行数：~450*
