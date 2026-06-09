-- Chain Attestations — append-only hash chain for tamper-evident audit trail
-- Each record contains previous_hash for chain integrity verification

CREATE TABLE IF NOT EXISTS chain_attestations (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(128) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(255) NOT NULL,
    actor_id VARCHAR(255),
    detail JSONB DEFAULT '{}',
    attestation_hash VARCHAR(64) NOT NULL,
    previous_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS ix_chain_attestations_resource ON chain_attestations(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_chain_attestations_event_type ON chain_attestations(event_type);
CREATE INDEX IF NOT EXISTS ix_chain_attestations_created ON chain_attestations(created_at);
CREATE INDEX IF NOT EXISTS ix_chain_attestations_hash ON chain_attestations(attestation_hash);

-- Prevent updates/deletes (enforce append-only at DB level)
-- This is enforced by application code; for strict enforcement, use a trigger:
CREATE OR REPLACE FUNCTION prevent_chain_attestation_modify()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'chain_attestations is append-only; updates and deletes are prohibited';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_chain_modify ON chain_attestations;
CREATE TRIGGER trg_prevent_chain_modify
    BEFORE UPDATE OR DELETE ON chain_attestations
    FOR EACH ROW
    EXECUTE FUNCTION prevent_chain_attestation_modify();
