-- CDS ClickHouse Audit Log Schema (Expanded)
-- 30+ columns for comprehensive audit trail
-- Runs automatically on first ClickHouse start
--
-- 漂移登记（2026-10 运维评审轮）：k8s/configmaps.yaml 内嵌的仍是 8 列的
-- audit events 副本，与本文件（规范 schema）已漂移；修复归 k8s 工作包，
-- 在此之前任何 8 列副本都按过时处理，本文件是唯一权威。

CREATE DATABASE IF NOT EXISTS cds_audit;

-- Main audit events table — 30+ columns
CREATE TABLE IF NOT EXISTS cds_audit.audit_events
(
    -- Core identification
    event_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    event_date Date DEFAULT toDate(timestamp),

    -- User/session context
    user_id UUID,
    session_id UUID,
    user_role LowCardinality(String) DEFAULT '',
    user_region LowCardinality(String) DEFAULT '',

    -- Action details
    action LowCardinality(String),
    action_category LowCardinality(String) DEFAULT '',  -- auth, data, compute, admin
    resource_type LowCardinality(String),
    resource_id String DEFAULT '',
    resource_name String DEFAULT '',

    -- Request context
    ip_address IPv4 DEFAULT toIPv4('0.0.0.0'),
    user_agent String DEFAULT '',
    request_method LowCardinality(String) DEFAULT '',  -- GET, POST, PUT, DELETE
    request_path String DEFAULT '',
    request_id UUID DEFAULT generateUUIDv4(),

    -- Cryptographic attestation
    sm2_signature String DEFAULT '',
    integrity_hash String DEFAULT '',
    merkle_root String DEFAULT '',
    blockchain_tx_hash String DEFAULT '',

    -- Data product context
    data_product_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    data_product_version UInt32 DEFAULT 0,
    data_product_type LowCardinality(String) DEFAULT '',

    -- Sandbox/compute context
    sandbox_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    sandbox_type LowCardinality(String) DEFAULT '',  -- docker, bwrap, firecracker
    compute_duration_ms UInt64 DEFAULT 0,

    -- Data metrics
    rows_affected UInt64 DEFAULT 0,
    bytes_read UInt64 DEFAULT 0,
    bytes_written UInt64 DEFAULT 0,

    -- Policy/contract context
    contract_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    policy_decision LowCardinality(String) DEFAULT '',  -- allow, deny, partial
    policy_reason String DEFAULT '',

    -- Security context
    security_level LowCardinality(String) DEFAULT '',  -- public, internal, confidential, secret
    encryption_key_id String DEFAULT '',
    column_encrypted_fields Array(String) DEFAULT [],

    -- Training/AI context
    training_job_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    model_id String DEFAULT '',
    dp_epsilon Float64 DEFAULT 0.0,
    dp_delta Float64 DEFAULT 0.0,

    -- Error tracking
    error_code LowCardinality(String) DEFAULT '',
    error_message String DEFAULT '',

    -- Flexible detail payload
    detail String DEFAULT '{}',  -- JSON
    tags Array(String) DEFAULT [],

    -- 高基数回溯列跳数索引：追溯查询按这些列过滤，无索引则全表扫。
    -- 月分区表配合下方 ttl_only_drop_parts，过期按分区丢弃，不产生行级 TTL mutation。
    INDEX idx_session_id session_id TYPE minmax GRANULARITY 4,
    INDEX idx_resource_id resource_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_request_id request_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_contract_id contract_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_sandbox_id sandbox_id TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, user_id, action, resource_type)
TTL toDateTime(timestamp) + INTERVAL 7 YEAR
SETTINGS ttl_only_drop_parts = 1;   -- 审计事件保留 7 年（分区级过期，不清审计日志/Merkle 叶）

-- Policy decisions table for OPA/RLS audit
CREATE TABLE IF NOT EXISTS cds_audit.policy_decisions
(
    decision_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    user_id UUID,
    contract_id UUID,
    operation String,
    sandbox_mode String,
    decision LowCardinality(String),  -- allow / deny
    reason String,
    fields_requested Array(String),
    fields_allowed Array(String),
    fields_denied Array(String),
    -- Extended fields
    rls_policies_applied Array(String) DEFAULT [],
    column_encryption_applied Array(String) DEFAULT [],
    sensitivity_level LowCardinality(String) DEFAULT '',
    user_region LowCardinality(String) DEFAULT '',
    user_clearance LowCardinality(String) DEFAULT ''
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, user_id, contract_id)
-- 数据最小化：决策遥测是高容量辅助流，非 7 年审计记录本体，有界窗口 1 年；
-- 不影响主审计表的 7 年保留
TTL toDateTime(timestamp) + INTERVAL 1 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- CDC change events table
CREATE TABLE IF NOT EXISTS cds_audit.cdc_events
(
    event_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    source_table String,
    operation LowCardinality(String),  -- c, u, d, r
    lsn UInt64 DEFAULT 0,
    before_data String DEFAULT '{}',  -- JSON
    after_data String DEFAULT '{}',   -- JSON
    connector_name String DEFAULT '',
    kafka_topic String DEFAULT '',
    kafka_offset UInt64 DEFAULT 0,
    processed Boolean DEFAULT false
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, source_table, operation)
-- CDC 是传输中继遥测，非审计记录本体：有界窗口 90 天
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS ttl_only_drop_parts = 1;

-- Data product version audit
CREATE TABLE IF NOT EXISTS cds_audit.data_product_versions
(
    version_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    data_product_id UUID,
    version UInt32,
    parent_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    change_summary String DEFAULT '',
    user_id UUID,
    action LowCardinality(String),  -- create, update, archive
    schema_snapshot String DEFAULT '{}'  -- JSON schema at this version
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, data_product_id, version)
-- 版本变更审计属审计链本体：与主表同 7 年保留，分区级过期
TTL toDateTime(timestamp) + INTERVAL 7 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Certificate lifecycle audit
CREATE TABLE IF NOT EXISTS cds_audit.certificate_events
(
    event_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    cert_id UUID,
    serial_number String,
    action LowCardinality(String),  -- issue, revoke, renew, verify
    subject String DEFAULT '',
    issuer String DEFAULT '',
    user_id UUID,
    reason String DEFAULT '',
    -- 可空表达「尚未链上校验」：DEFAULT true 是 fail-open（未验证即报有效）
    chain_valid Nullable(Boolean),
    chain_length UInt32 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, cert_id, action)
-- 证书生命周期属审计链本体：与主表同 7 年保留，分区级过期
TTL toDateTime(timestamp) + INTERVAL 7 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- 合约网关计量（A164）：gateway_service._write_metering 写、/api/v1/gateway 计量接口读。
-- 列定义必须与 app/services/clickhouse_schema.py 的注册表逐字一致（tests/test_ocr_a164_activity_metering_ddl.py 钉），
-- 冷启动时 ClickHouse 只执行本文件，缺表就是写入失败被吞、计量静默归零。
CREATE TABLE IF NOT EXISTS cds_audit.gateway_metering
(
    request_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    event_date Date DEFAULT toDate(timestamp),
    contract_id UUID,
    app_id String,
    consumer_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    product_id UUID DEFAULT toUUID('00000000-0000-0000-0000-000000000000'),
    operation LowCardinality(String),  -- query, access, export
    rows_returned UInt64 DEFAULT 0,
    bytes_returned UInt64 DEFAULT 0,
    duration_ms UInt64 DEFAULT 0,
    status_code UInt16 DEFAULT 200
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, contract_id, app_id, product_id, operation)
-- 计量是计费/限额依据：保留 3 年（与运行面注册表一致），分区级过期
TTL toDateTime(timestamp) + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- 沙箱内活动审计（A164）：sandbox_audit 采集器写、会话详情接口读。
-- 同上：列定义与 app/services/clickhouse_schema.py 的注册表同源，由测试钉住不得漂移。
CREATE TABLE IF NOT EXISTS cds_audit.sandbox_activity_events
(
    event_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3, 'UTC'),
    event_date Date DEFAULT toDate(timestamp),
    session_id String DEFAULT '',
    user_id String DEFAULT '',
    sandbox_level LowCardinality(String) DEFAULT '',
    category LowCardinality(String),  -- data_access, network, application
    event_type LowCardinality(String),  -- query, connection, process_start, ...
    detail String DEFAULT '{}',  -- JSON
    duration_ms UInt64 DEFAULT 0,
    bytes_read UInt64 DEFAULT 0,
    bytes_written UInt64 DEFAULT 0,
    rows_affected UInt64 DEFAULT 0,
    risk_level LowCardinality(String) DEFAULT 'low',
    blocked Boolean DEFAULT false,
    INDEX idx_session_id session_id TYPE bloom_filter GRANULARITY 3,
    INDEX idx_category category TYPE bloom_filter GRANULARITY 3,
    INDEX idx_event_type event_type TYPE bloom_filter GRANULARITY 3
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, session_id, category, event_type)
-- 沙箱活动流：保留 3 年（与运行面注册表一致），分区级过期
TTL toDateTime(timestamp) + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;
