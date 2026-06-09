-- CDS ClickHouse Audit Log Schema (Expanded)
-- 30+ columns for comprehensive audit trail
-- Runs automatically on first ClickHouse start

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
    tags Array(String) DEFAULT []
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, user_id, action, resource_type)
TTL timestamp + INTERVAL 7 YEAR;

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
ORDER BY (timestamp, user_id, contract_id);

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
ORDER BY (timestamp, source_table, operation);

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
ORDER BY (timestamp, data_product_id, version);

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
    chain_valid Boolean DEFAULT true,
    chain_length UInt32 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, cert_id, action);
