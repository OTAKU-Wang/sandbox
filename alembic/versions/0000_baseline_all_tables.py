"""W5 baseline — creates all live tables from an empty database.

Revision ID: 0000_baseline_all_tables
Revises: None (chain root)
Create Date: 2026-09-07 (autogenerate, hand-reviewed)

This is the FIRST migration in the chain: on a fresh (production) database
`alembic upgrade head` builds every table, then 0001/0002 re-add their
additive columns. Columns introduced by 0001_p0_security_hardening and
0002_session_usability are deliberately ABSENT here so the chain applies
cleanly on an empty database:

  contracts.valid_until / purpose / purpose_scope
  data_resources.field_classifications
  policy_bundles.revoked_at
  sandbox_sessions.extended_seconds / pre_pause_status
  data_encryption_keys.wrapped_payload / sm2_encrypted_payload

For databases bootstrapped with Base.metadata.create_all (dev/test), run
`alembic stamp head` instead — see docs in this repository.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '0000_baseline_all_tables'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('alert_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('dedup_key', sa.String(length=128), nullable=False),
    sa.Column('alert_type', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=True),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('resource_type', sa.String(length=64), nullable=True),
    sa.Column('resource_id', sa.String(length=255), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=False),
    sa.Column('occurrence_count', sa.Integer(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('acknowledged_by', sa.String(length=64), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.String(length=64), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('notification_status', sa.String(length=32), nullable=False),
    sa.Column('notification_results', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_records_alert_type'), 'alert_records', ['alert_type'], unique=False)
    op.create_index(op.f('ix_alert_records_dedup_key'), 'alert_records', ['dedup_key'], unique=True)
    op.create_index('ix_alert_records_last_seen', 'alert_records', ['last_seen_at'], unique=False)
    op.create_index(op.f('ix_alert_records_resource_id'), 'alert_records', ['resource_id'], unique=False)
    op.create_index(op.f('ix_alert_records_resource_type'), 'alert_records', ['resource_type'], unique=False)
    op.create_index(op.f('ix_alert_records_session_id'), 'alert_records', ['session_id'], unique=False)
    op.create_index(op.f('ix_alert_records_severity'), 'alert_records', ['severity'], unique=False)
    op.create_index(op.f('ix_alert_records_status'), 'alert_records', ['status'], unique=False)
    op.create_index('ix_alert_records_status_severity', 'alert_records', ['status', 'severity'], unique=False)
    op.create_index('ix_alert_records_type_status', 'alert_records', ['alert_type', 'status'], unique=False)
    op.create_index(op.f('ix_alert_records_user_id'), 'alert_records', ['user_id'], unique=False)
    op.create_table('blockchain_anchors',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('data_hash', sa.String(length=64), nullable=False),
    sa.Column('backend', sa.String(length=32), nullable=False),
    sa.Column('tx_hash', sa.String(length=128), nullable=True),
    sa.Column('block_number', sa.Integer(), nullable=True),
    sa.Column('confirmed', sa.Boolean(), nullable=False),
    sa.Column('metadata_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_blockchain_anchors_backend', 'blockchain_anchors', ['backend'], unique=False)
    op.create_index('ix_blockchain_anchors_created', 'blockchain_anchors', ['created_at'], unique=False)
    op.create_index('ix_blockchain_anchors_tx_hash', 'blockchain_anchors', ['tx_hash'], unique=False)
    op.create_table('dp_budget_allocations',
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('total_epsilon', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('contract_id')
    )
    op.create_table('dp_budget_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=False),
    sa.Column('epsilon_consumed', sa.Float(), nullable=False),
    sa.Column('operation', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_dp_budget_contract', 'dp_budget_entries', ['contract_id'], unique=False)
    op.create_index('ix_dp_budget_session', 'dp_budget_entries', ['session_id'], unique=False)
    op.create_table('dp_budget_status',
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('total_epsilon', sa.Float(), nullable=False),
    sa.Column('consumed_epsilon', sa.Float(), nullable=False),
    sa.Column('remaining_epsilon', sa.Float(), nullable=False),
    sa.Column('last_consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('contract_id')
    )
    op.create_table('federation_audit_log',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('entry_id', sa.String(length=64), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('source_space', sa.String(length=128), nullable=False),
    sa.Column('target_space', sa.String(length=128), nullable=False),
    sa.Column('operation', sa.String(length=128), nullable=False),
    sa.Column('resource', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=True),
    sa.Column('details_json', sa.Text(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entry_id')
    )
    op.create_index('ix_federation_audit_source', 'federation_audit_log', ['source_space'], unique=False)
    op.create_index('ix_federation_audit_target', 'federation_audit_log', ['target_space'], unique=False)
    op.create_index('ix_federation_audit_time', 'federation_audit_log', ['timestamp'], unique=False)
    op.create_table('federation_trusts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('local_space_id', sa.String(length=128), nullable=False),
    sa.Column('local_space_name', sa.String(length=256), nullable=False),
    sa.Column('local_endpoint', sa.String(length=512), nullable=False),
    sa.Column('remote_space_id', sa.String(length=128), nullable=False),
    sa.Column('remote_space_name', sa.String(length=256), nullable=False),
    sa.Column('remote_endpoint', sa.String(length=512), nullable=False),
    sa.Column('trust_level', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('allowed_operations_json', sa.Text(), nullable=True),
    sa.Column('policy_sync_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_sync', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_federation_trusts_local', 'federation_trusts', ['local_space_id'], unique=False)
    op.create_index('ix_federation_trusts_remote', 'federation_trusts', ['remote_space_id'], unique=False)
    op.create_index('ix_federation_trusts_status', 'federation_trusts', ['status'], unique=False)
    op.create_table('key_distributions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key_id', sa.String(length=255), nullable=False),
    sa.Column('session_id', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('tee_quote_hash', sa.String(length=128), nullable=True),
    sa.Column('tee_mrenclave', sa.String(length=128), nullable=True),
    sa.Column('tee_type', sa.String(length=32), nullable=True),
    sa.Column('attestation_status', sa.String(length=32), nullable=True),
    sa.Column('session_key_enc', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_key_dist_key_id', 'key_distributions', ['key_id'], unique=False)
    op.create_index('ix_key_dist_session', 'key_distributions', ['session_id'], unique=False)
    op.create_index('ix_key_dist_status', 'key_distributions', ['status'], unique=False)
    op.create_table('network_policies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.String(length=255), nullable=False),
    sa.Column('user_id', sa.String(length=255), nullable=False),
    sa.Column('mode', sa.String(length=32), nullable=False),
    sa.Column('allowed_ips', sa.JSON(), nullable=True),
    sa.Column('allowed_domains', sa.JSON(), nullable=True),
    sa.Column('allowed_ports', sa.JSON(), nullable=True),
    sa.Column('dns_proxy_enabled', sa.Boolean(), nullable=False),
    sa.Column('max_connections_per_second', sa.Integer(), nullable=False),
    sa.Column('max_bandwidth_bytes_per_second', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_network_policies_session_id'), 'network_policies', ['session_id'], unique=True)
    op.create_index(op.f('ix_network_policies_user_id'), 'network_policies', ['user_id'], unique=False)
    op.create_table('sandbox_nodes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('node_id', sa.String(length=64), nullable=False),
    sa.Column('hostname', sa.String(length=255), nullable=False),
    sa.Column('ip_address', sa.String(length=45), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('capabilities', sa.JSON(), nullable=True),
    sa.Column('region', sa.String(length=64), nullable=False),
    sa.Column('cpu_cores', sa.Integer(), nullable=False),
    sa.Column('memory_mb', sa.Integer(), nullable=False),
    sa.Column('gpu_count', sa.Integer(), nullable=False),
    sa.Column('gpu_memory_mb', sa.Integer(), nullable=False),
    sa.Column('cpu_usage', sa.Float(), nullable=False),
    sa.Column('memory_usage', sa.Float(), nullable=False),
    sa.Column('gpu_usage', sa.Float(), nullable=False),
    sa.Column('active_tasks', sa.Integer(), nullable=False),
    sa.Column('max_tasks', sa.Integer(), nullable=False),
    sa.Column('total_completed', sa.Integer(), nullable=False),
    sa.Column('total_failed', sa.Integer(), nullable=False),
    sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_rate', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sandbox_nodes_node_id'), 'sandbox_nodes', ['node_id'], unique=True)
    op.create_index('ix_sandbox_nodes_region', 'sandbox_nodes', ['region'], unique=False)
    op.create_index('ix_sandbox_nodes_status', 'sandbox_nodes', ['status'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('hashed_password', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('organization', sa.String(length=255), nullable=True),
    sa.Column('sm2_certificate', sa.String(length=2048), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('watermark_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('owner_id', sa.String(length=128), nullable=False),
    sa.Column('model_id', sa.String(length=128), nullable=False),
    sa.Column('layer_positions', sa.JSON(), nullable=False),
    sa.Column('ordered_positions', sa.JSON(), nullable=False),
    sa.Column('key_seed', sa.Integer(), nullable=False),
    sa.Column('num_bits', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_watermark_records_job_id', 'watermark_records', ['job_id'], unique=False)
    op.create_index('ix_watermark_records_owner', 'watermark_records', ['owner_id'], unique=False)
    op.create_table('certificates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('cert_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('serial_number', sa.String(length=64), nullable=False),
    sa.Column('subject', sa.String(length=512), nullable=False),
    sa.Column('issuer', sa.String(length=512), nullable=False),
    sa.Column('public_key', sa.Text(), nullable=False),
    sa.Column('certificate_pem', sa.Text(), nullable=False),
    sa.Column('parent_cert_id', sa.UUID(), nullable=True),
    sa.Column('is_ca', sa.Boolean(), nullable=False),
    sa.Column('not_before', sa.DateTime(timezone=True), nullable=False),
    sa.Column('not_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['parent_cert_id'], ['certificates.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('serial_number')
    )
    op.create_index('ix_certificates_serial', 'certificates', ['serial_number'], unique=True)
    op.create_index('ix_certificates_user_status', 'certificates', ['user_id', 'status'], unique=False)
    op.create_table('connectors',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('space_id', sa.String(length=255), nullable=False),
    sa.Column('space_name', sa.String(length=255), nullable=False),
    sa.Column('space_url', sa.String(length=512), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('api_key_hash', sa.String(length=64), nullable=False),
    sa.Column('api_key_prefix', sa.String(length=8), nullable=False),
    sa.Column('supported_protocols', sa.JSON(), nullable=False),
    sa.Column('max_concurrent_sessions', sa.Integer(), nullable=False),
    sa.Column('allowed_product_types', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('is_healthy', sa.Boolean(), nullable=False),
    sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=True),
    sa.Column('registered_by', sa.UUID(), nullable=False),
    sa.Column('metadata', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['registered_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('space_id')
    )
    op.create_index('ix_connectors_status', 'connectors', ['status'], unique=False)
    op.create_table('contracts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('contract_no', sa.String(length=64), nullable=False),
    sa.Column('contract_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('buyer_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('terms', sa.JSON(), nullable=True),
    sa.Column('product_ids', sa.JSON(), nullable=False),
    sa.Column('allowed_sandbox_levels', sa.String(length=128), nullable=True),
    sa.Column('allowed_sandbox_modes', sa.JSON(), nullable=True),
    sa.Column('allowed_operations', sa.Text(), nullable=True),
    sa.Column('max_duration_hours', sa.Integer(), nullable=False),
    sa.Column('dp_epsilon_budget', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('max_output_rows', sa.Integer(), nullable=False),
    sa.Column('allowed_output_formats', sa.String(length=256), nullable=True),
    sa.Column('inspection_rule_set', sa.JSON(), nullable=True),
    sa.Column('provider_signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('buyer_signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_signature', sa.Text(), nullable=True),
    sa.Column('buyer_signature', sa.Text(), nullable=True),
    sa.Column('platform_signature', sa.Text(), nullable=True),
    sa.Column('platform_signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('blockchain_tx_hash', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['buyer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['provider_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contracts_buyer_id'), 'contracts', ['buyer_id'], unique=False)
    op.create_index('ix_contracts_buyer_status', 'contracts', ['buyer_id', 'status'], unique=False)
    op.create_index(op.f('ix_contracts_contract_no'), 'contracts', ['contract_no'], unique=True)
    op.create_index(op.f('ix_contracts_provider_id'), 'contracts', ['provider_id'], unique=False)
    op.create_index('ix_contracts_provider_status', 'contracts', ['provider_id', 'status'], unique=False)
    op.create_table('data_resources',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('resource_type', sa.String(length=32), nullable=False),
    sa.Column('format', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('storage_path', sa.String(length=512), nullable=True),
    sa.Column('encryption_key_id', sa.String(length=255), nullable=True),
    sa.Column('chunk_refs', sa.JSON(), nullable=True),
    sa.Column('file_size_bytes', sa.Integer(), nullable=True),
    sa.Column('sm3_checksum', sa.String(length=64), nullable=True),
    sa.Column('schema_fields', sa.JSON(), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('error_message', sa.String(length=1000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['provider_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_data_resources_provider_id'), 'data_resources', ['provider_id'], unique=False)
    op.create_index('ix_data_resources_provider_status', 'data_resources', ['provider_id', 'status'], unique=False)
    op.create_table('key_audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key_id', sa.String(length=255), nullable=False),
    sa.Column('operation', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.String(length=255), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_key_audit_key_id', 'key_audit_logs', ['key_id'], unique=False)
    op.create_index('ix_key_audit_operation', 'key_audit_logs', ['operation'], unique=False)
    op.create_table('app_credentials',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('app_id', sa.String(length=64), nullable=False),
    sa.Column('app_secret_hash', sa.String(length=128), nullable=False),
    sa.Column('contract_id', sa.UUID(), nullable=False),
    sa.Column('consumer_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('rate_limit', sa.Integer(), nullable=False),
    sa.Column('quota_rows', sa.BigInteger(), nullable=False),
    sa.Column('quota_bytes', sa.BigInteger(), nullable=False),
    sa.Column('allowed_ips', sa.JSON(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('revoke_reason', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['consumer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_app_credentials_app_id'), 'app_credentials', ['app_id'], unique=True)
    op.create_index('ix_app_credentials_consumer', 'app_credentials', ['consumer_id', 'status'], unique=False)
    op.create_index('ix_app_credentials_contract', 'app_credentials', ['contract_id', 'status'], unique=False)
    op.create_table('data_products',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider_id', sa.UUID(), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('product_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('industry', sa.String(length=128), nullable=True),
    sa.Column('data_schema', sa.JSON(), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('encrypted_storage_path', sa.String(length=512), nullable=True),
    sa.Column('encryption_key_id', sa.String(length=255), nullable=True),
    sa.Column('sm4_checksum', sa.String(length=64), nullable=True),
    sa.Column('security_level', sa.String(length=32), nullable=True),
    sa.Column('allowed_operations', sa.JSON(), nullable=True),
    sa.Column('output_constraints', sa.JSON(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('change_summary', sa.Text(), nullable=True),
    sa.Column('is_latest', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['data_products.id'], ),
    sa.ForeignKeyConstraint(['provider_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['resource_id'], ['data_resources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_data_products_industry_status', 'data_products', ['industry', 'status'], unique=False)
    op.create_index(op.f('ix_data_products_provider_id'), 'data_products', ['provider_id'], unique=False)
    op.create_index('ix_data_products_provider_status', 'data_products', ['provider_id', 'status'], unique=False)
    op.create_index(op.f('ix_data_products_resource_id'), 'data_products', ['resource_id'], unique=False)
    op.create_table('policy_bundles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('policy_id', sa.String(length=255), nullable=False),
    sa.Column('contract_id', sa.UUID(), nullable=False),
    sa.Column('rego_source', sa.Text(), nullable=False),
    sa.Column('integrity_hash', sa.String(length=64), nullable=False),
    sa.Column('field_acl', sa.JSON(), nullable=True),
    sa.Column('allowed_ops', sa.JSON(), nullable=True),
    sa.Column('sandbox_modes', sa.JSON(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_policy_bundles_contract', 'policy_bundles', ['contract_id'], unique=False)
    op.create_index(op.f('ix_policy_bundles_policy_id'), 'policy_bundles', ['policy_id'], unique=True)
    op.create_table('field_exposure_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('buyer_id', sa.UUID(), nullable=False),
    sa.Column('contract_id', sa.UUID(), nullable=True),
    sa.Column('requested_fields', sa.JSON(), nullable=False),
    sa.Column('justification', sa.Text(), nullable=True),
    sa.Column('approved_fields', sa.JSON(), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['buyer_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['data_products.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_field_exposure_product_buyer', 'field_exposure_requests', ['product_id', 'buyer_id'], unique=False)
    op.create_index(op.f('ix_field_exposure_requests_buyer_id'), 'field_exposure_requests', ['buyer_id'], unique=False)
    op.create_index(op.f('ix_field_exposure_requests_product_id'), 'field_exposure_requests', ['product_id'], unique=False)
    op.create_index('ix_field_exposure_status', 'field_exposure_requests', ['status'], unique=False)
    op.create_table('field_visibility_configs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('field_rules', sa.JSON(), nullable=False),
    sa.Column('default_sensitivity', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['data_products.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('product_id')
    )
    op.create_index('ix_field_visibility_product', 'field_visibility_configs', ['product_id'], unique=True)
    op.create_table('sandbox_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('data_product_id', sa.UUID(), nullable=False),
    sa.Column('sandbox_level', sa.String(length=16), nullable=False),
    sa.Column('sandbox_mode', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('contract_id', sa.String(length=255), nullable=True),
    sa.Column('container_id', sa.String(length=255), nullable=True),
    sa.Column('session_key_id', sa.String(length=255), nullable=True),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('resource_limits', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['data_product_id'], ['data_products.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sandbox_sessions_created', 'sandbox_sessions', ['created_at'], unique=False)
    op.create_index(op.f('ix_sandbox_sessions_data_product_id'), 'sandbox_sessions', ['data_product_id'], unique=False)
    op.create_index('ix_sandbox_sessions_product_status', 'sandbox_sessions', ['data_product_id', 'status'], unique=False)
    op.create_index(op.f('ix_sandbox_sessions_user_id'), 'sandbox_sessions', ['user_id'], unique=False)
    op.create_index('ix_sandbox_sessions_user_status', 'sandbox_sessions', ['user_id', 'status'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=128), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('resource_id', sa.String(length=255), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('blockchain_tx_hash', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sandbox_sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_created_action', 'audit_logs', ['created_at', 'action'], unique=False)
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource_type', 'resource_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_session_id'), 'audit_logs', ['session_id'], unique=False)
    op.create_index('ix_audit_logs_user_created', 'audit_logs', ['user_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    op.create_table('connector_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('connector_id', sa.UUID(), nullable=False),
    sa.Column('contract_id', sa.UUID(), nullable=False),
    sa.Column('sandbox_session_id', sa.UUID(), nullable=True),
    sa.Column('remote_user_id', sa.String(length=255), nullable=False),
    sa.Column('remote_session_id', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['data_products.id'], ),
    sa.ForeignKeyConstraint(['sandbox_session_id'], ['sandbox_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_connector_sessions_connector', 'connector_sessions', ['connector_id'], unique=False)
    op.create_index(op.f('ix_connector_sessions_connector_id'), 'connector_sessions', ['connector_id'], unique=False)
    op.create_index('ix_connector_sessions_status', 'connector_sessions', ['status'], unique=False)
    op.create_table('data_encryption_keys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key_id', sa.String(length=255), nullable=False),
    sa.Column('key_type', sa.String(length=32), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=False),
    sa.Column('algorithm', sa.String(length=32), nullable=False),
    sa.Column('encrypted_key', sa.Text(), nullable=True),
    sa.Column('encryption_cert_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('max_usage', sa.Integer(), nullable=False),
    sa.Column('usage_count', sa.Integer(), nullable=False),
    sa.Column('rotated_to', sa.String(length=255), nullable=True),
    sa.Column('destroy_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('destroyed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['encryption_cert_id'], ['certificates.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['data_products.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['sandbox_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_data_encryption_keys_encryption_cert_id'), 'data_encryption_keys', ['encryption_cert_id'], unique=False)
    op.create_index(op.f('ix_data_encryption_keys_key_id'), 'data_encryption_keys', ['key_id'], unique=True)
    op.create_index(op.f('ix_data_encryption_keys_product_id'), 'data_encryption_keys', ['product_id'], unique=False)
    op.create_index('ix_dek_product_status', 'data_encryption_keys', ['product_id', 'status'], unique=False)
    op.create_index('ix_dek_status', 'data_encryption_keys', ['status'], unique=False)
    op.create_table('pipeline_tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.String(length=64), nullable=False),
    sa.Column('task_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('input_path', sa.Text(), nullable=False),
    sa.Column('output_path', sa.Text(), nullable=True),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['sandbox_sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pipeline_tasks_created', 'pipeline_tasks', ['created_at'], unique=False)
    op.create_index('ix_pipeline_tasks_status', 'pipeline_tasks', ['status'], unique=False)
    op.create_index(op.f('ix_pipeline_tasks_task_id'), 'pipeline_tasks', ['task_id'], unique=True)
    op.create_index('ix_pipeline_tasks_user_status', 'pipeline_tasks', ['user_id', 'status'], unique=False)
    op.create_table('sandbox_tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.String(length=255), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('task_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=True),
    sa.Column('code_content', sa.Text(), nullable=True),
    sa.Column('language', sa.String(length=32), nullable=True),
    sa.Column('resource_usage', sa.JSON(), nullable=True),
    sa.Column('dp_epsilon_used', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('output_rows', sa.Integer(), nullable=False),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sandbox_sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sandbox_tasks_session_id'), 'sandbox_tasks', ['session_id'], unique=False)
    op.create_index('ix_sandbox_tasks_session_status', 'sandbox_tasks', ['session_id', 'status'], unique=False)
    op.create_index(op.f('ix_sandbox_tasks_task_id'), 'sandbox_tasks', ['task_id'], unique=True)
    op.create_index('ix_sandbox_tasks_user', 'sandbox_tasks', ['user_id'], unique=False)
    op.create_table('training_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('job_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('config', sa.JSON(), nullable=True),
    sa.Column('base_model', sa.String(length=128), nullable=True),
    sa.Column('dataset_path', sa.Text(), nullable=True),
    sa.Column('metrics', sa.JSON(), nullable=True),
    sa.Column('output_path', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['sandbox_sessions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_training_jobs_job_id'), 'training_jobs', ['job_id'], unique=True)
    op.create_index('ix_training_jobs_status', 'training_jobs', ['status'], unique=False)
    op.create_index('ix_training_jobs_user_status', 'training_jobs', ['user_id', 'status'], unique=False)
    op.create_table('merkle_leaves',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.String(length=64), nullable=False),
    sa.Column('leaf_index', sa.Integer(), nullable=False),
    sa.Column('leaf_hash', sa.String(length=64), nullable=False),
    sa.Column('audit_event_id', sa.UUID(), nullable=True),
    sa.Column('batch_root', sa.String(length=64), nullable=True),
    sa.Column('anchored_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['audit_event_id'], ['audit_logs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_merkle_leaves_audit', 'merkle_leaves', ['audit_event_id'], unique=False)
    op.create_index('ix_merkle_leaves_batch', 'merkle_leaves', ['batch_id'], unique=False)
    op.create_index(op.f('ix_merkle_leaves_batch_id'), 'merkle_leaves', ['batch_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(op.f('ix_merkle_leaves_batch_id'), table_name='merkle_leaves')
    op.drop_index('ix_merkle_leaves_batch', table_name='merkle_leaves')
    op.drop_index('ix_merkle_leaves_audit', table_name='merkle_leaves')
    op.drop_table('merkle_leaves')
    op.drop_index('ix_training_jobs_user_status', table_name='training_jobs')
    op.drop_index('ix_training_jobs_status', table_name='training_jobs')
    op.drop_index(op.f('ix_training_jobs_job_id'), table_name='training_jobs')
    op.drop_table('training_jobs')
    op.drop_index('ix_sandbox_tasks_user', table_name='sandbox_tasks')
    op.drop_index(op.f('ix_sandbox_tasks_task_id'), table_name='sandbox_tasks')
    op.drop_index('ix_sandbox_tasks_session_status', table_name='sandbox_tasks')
    op.drop_index(op.f('ix_sandbox_tasks_session_id'), table_name='sandbox_tasks')
    op.drop_table('sandbox_tasks')
    op.drop_index('ix_pipeline_tasks_user_status', table_name='pipeline_tasks')
    op.drop_index(op.f('ix_pipeline_tasks_task_id'), table_name='pipeline_tasks')
    op.drop_index('ix_pipeline_tasks_status', table_name='pipeline_tasks')
    op.drop_index('ix_pipeline_tasks_created', table_name='pipeline_tasks')
    op.drop_table('pipeline_tasks')
    op.drop_index('ix_dek_status', table_name='data_encryption_keys')
    op.drop_index('ix_dek_product_status', table_name='data_encryption_keys')
    op.drop_index(op.f('ix_data_encryption_keys_product_id'), table_name='data_encryption_keys')
    op.drop_index(op.f('ix_data_encryption_keys_key_id'), table_name='data_encryption_keys')
    op.drop_index(op.f('ix_data_encryption_keys_encryption_cert_id'), table_name='data_encryption_keys')
    op.drop_table('data_encryption_keys')
    op.drop_index('ix_connector_sessions_status', table_name='connector_sessions')
    op.drop_index(op.f('ix_connector_sessions_connector_id'), table_name='connector_sessions')
    op.drop_index('ix_connector_sessions_connector', table_name='connector_sessions')
    op.drop_table('connector_sessions')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index('ix_audit_logs_user_created', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_session_id'), table_name='audit_logs')
    op.drop_index('ix_audit_logs_resource', table_name='audit_logs')
    op.drop_index('ix_audit_logs_created_action', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_sandbox_sessions_user_status', table_name='sandbox_sessions')
    op.drop_index(op.f('ix_sandbox_sessions_user_id'), table_name='sandbox_sessions')
    op.drop_index('ix_sandbox_sessions_product_status', table_name='sandbox_sessions')
    op.drop_index(op.f('ix_sandbox_sessions_data_product_id'), table_name='sandbox_sessions')
    op.drop_index('ix_sandbox_sessions_created', table_name='sandbox_sessions')
    op.drop_table('sandbox_sessions')
    op.drop_index('ix_field_visibility_product', table_name='field_visibility_configs')
    op.drop_table('field_visibility_configs')
    op.drop_index('ix_field_exposure_status', table_name='field_exposure_requests')
    op.drop_index(op.f('ix_field_exposure_requests_product_id'), table_name='field_exposure_requests')
    op.drop_index(op.f('ix_field_exposure_requests_buyer_id'), table_name='field_exposure_requests')
    op.drop_index('ix_field_exposure_product_buyer', table_name='field_exposure_requests')
    op.drop_table('field_exposure_requests')
    op.drop_index(op.f('ix_policy_bundles_policy_id'), table_name='policy_bundles')
    op.drop_index('ix_policy_bundles_contract', table_name='policy_bundles')
    op.drop_table('policy_bundles')
    op.drop_index(op.f('ix_data_products_resource_id'), table_name='data_products')
    op.drop_index('ix_data_products_provider_status', table_name='data_products')
    op.drop_index(op.f('ix_data_products_provider_id'), table_name='data_products')
    op.drop_index('ix_data_products_industry_status', table_name='data_products')
    op.drop_table('data_products')
    op.drop_index('ix_app_credentials_contract', table_name='app_credentials')
    op.drop_index('ix_app_credentials_consumer', table_name='app_credentials')
    op.drop_index(op.f('ix_app_credentials_app_id'), table_name='app_credentials')
    op.drop_table('app_credentials')
    op.drop_index('ix_key_audit_operation', table_name='key_audit_logs')
    op.drop_index('ix_key_audit_key_id', table_name='key_audit_logs')
    op.drop_table('key_audit_logs')
    op.drop_index('ix_data_resources_provider_status', table_name='data_resources')
    op.drop_index(op.f('ix_data_resources_provider_id'), table_name='data_resources')
    op.drop_table('data_resources')
    op.drop_index('ix_contracts_provider_status', table_name='contracts')
    op.drop_index(op.f('ix_contracts_provider_id'), table_name='contracts')
    op.drop_index(op.f('ix_contracts_contract_no'), table_name='contracts')
    op.drop_index('ix_contracts_buyer_status', table_name='contracts')
    op.drop_index(op.f('ix_contracts_buyer_id'), table_name='contracts')
    op.drop_table('contracts')
    op.drop_index('ix_connectors_status', table_name='connectors')
    op.drop_table('connectors')
    op.drop_index('ix_certificates_user_status', table_name='certificates')
    op.drop_index('ix_certificates_serial', table_name='certificates')
    op.drop_table('certificates')
    op.drop_index('ix_watermark_records_owner', table_name='watermark_records')
    op.drop_index('ix_watermark_records_job_id', table_name='watermark_records')
    op.drop_table('watermark_records')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
    op.drop_index('ix_sandbox_nodes_status', table_name='sandbox_nodes')
    op.drop_index('ix_sandbox_nodes_region', table_name='sandbox_nodes')
    op.drop_index(op.f('ix_sandbox_nodes_node_id'), table_name='sandbox_nodes')
    op.drop_table('sandbox_nodes')
    op.drop_index(op.f('ix_network_policies_user_id'), table_name='network_policies')
    op.drop_index(op.f('ix_network_policies_session_id'), table_name='network_policies')
    op.drop_table('network_policies')
    op.drop_index('ix_key_dist_status', table_name='key_distributions')
    op.drop_index('ix_key_dist_session', table_name='key_distributions')
    op.drop_index('ix_key_dist_key_id', table_name='key_distributions')
    op.drop_table('key_distributions')
    op.drop_index('ix_federation_trusts_status', table_name='federation_trusts')
    op.drop_index('ix_federation_trusts_remote', table_name='federation_trusts')
    op.drop_index('ix_federation_trusts_local', table_name='federation_trusts')
    op.drop_table('federation_trusts')
    op.drop_index('ix_federation_audit_time', table_name='federation_audit_log')
    op.drop_index('ix_federation_audit_target', table_name='federation_audit_log')
    op.drop_index('ix_federation_audit_source', table_name='federation_audit_log')
    op.drop_table('federation_audit_log')
    op.drop_table('dp_budget_status')
    op.drop_index('ix_dp_budget_session', table_name='dp_budget_entries')
    op.drop_index('ix_dp_budget_contract', table_name='dp_budget_entries')
    op.drop_table('dp_budget_entries')
    op.drop_table('dp_budget_allocations')
    op.drop_index('ix_blockchain_anchors_tx_hash', table_name='blockchain_anchors')
    op.drop_index('ix_blockchain_anchors_created', table_name='blockchain_anchors')
    op.drop_index('ix_blockchain_anchors_backend', table_name='blockchain_anchors')
    op.drop_table('blockchain_anchors')
    op.drop_index(op.f('ix_alert_records_user_id'), table_name='alert_records')
    op.drop_index('ix_alert_records_type_status', table_name='alert_records')
    op.drop_index('ix_alert_records_status_severity', table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_status'), table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_severity'), table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_session_id'), table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_resource_type'), table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_resource_id'), table_name='alert_records')
    op.drop_index('ix_alert_records_last_seen', table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_dedup_key'), table_name='alert_records')
    op.drop_index(op.f('ix_alert_records_alert_type'), table_name='alert_records')
    op.drop_table('alert_records')
    # ### end Alembic commands ###
