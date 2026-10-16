"""SS-15 — 数据开发到成果交付的完整业务闭环（合约交付配置 + 调度 + Notebook + 工作负载提升 + 三条交付通道）。

全部加性变更：
- contracts 新增交付方式 / 自动计算 / 频率 / 沙箱资源规格 / 下次计算时间等列（历史合约保持人工触发语义）
- sandbox_sessions 新增 env（test|production，历史行留 NULL 表示"未标注"）与 workload_id 松引用
- 新表：notebooks, workloads, workload_test_runs, compute_runs, api_endpoints,
        webhook_subscriptions, webhook_deliveries

workloads.test_run_id 指向后建的 workload_test_runs，故先建表再 add_column，
保持线性迁移且保留真实外键。

Revision ID: 0019_flow_orchestration
Revises: 0018_owner_fks
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0019_flow_orchestration"
down_revision = "0018_owner_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contracts", sa.Column("delivery_methods", sa.JSON(), nullable=True))
    op.add_column("contracts", sa.Column("auto_compute", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("contracts", sa.Column("compute_frequency", sa.String(length=16), nullable=False, server_default="manual"))
    op.add_column("contracts", sa.Column("compute_minute", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("contracts", sa.Column("compute_hour", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("contracts", sa.Column("compute_tz", sa.String(length=64), nullable=False, server_default="Asia/Shanghai"))
    op.add_column("contracts", sa.Column("sandbox_resource_spec", sa.JSON(), nullable=True))
    op.add_column("contracts", sa.Column("next_compute_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("contracts", sa.Column("last_computed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_contracts_next_compute_at", "contracts", ["next_compute_at"], unique=False)

    op.add_column("sandbox_sessions", sa.Column("env", sa.String(length=16), nullable=True))
    op.add_column("sandbox_sessions", sa.Column("workload_id", sa.UUID(), nullable=True))
    op.create_index("ix_sandbox_sessions_workload_id", "sandbox_sessions", ["workload_id"], unique=False)

    op.create_table(
        "notebooks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("kernel", sa.String(length=64), nullable=False, server_default="python3"),
        sa.Column("language", sa.String(length=32), nullable=False, server_default="python"),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("execution_backend", sa.String(length=32), nullable=True),
        sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["notebooks.id"]),
    )
    op.create_index("ix_notebooks_user_id", "notebooks", ["user_id"], unique=False)
    op.create_index("ix_notebooks_user_status", "notebooks", ["user_id", "status"], unique=False)
    op.create_index("ix_notebooks_contract", "notebooks", ["contract_id"], unique=False)

    op.create_table(
        "workloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=True),
        sa.Column("source_notebook_id", sa.UUID(), nullable=True),
        sa.Column("source_task_id", sa.UUID(), nullable=True),
        sa.Column("entrypoint", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=False, server_default="python"),
        sa.Column("entrypoint_hash", sa.String(length=64), nullable=True),
        sa.Column("resource_spec", sa.JSON(), nullable=True),
        sa.Column("data_product_ids", sa.JSON(), nullable=True),
        sa.Column("env", sa.String(length=16), nullable=False, server_default="test"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["source_notebook_id"], ["notebooks.id"]),
        sa.ForeignKeyConstraint(["source_task_id"], ["sandbox_tasks.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["workloads.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"]),
    )
    op.create_index("ix_workloads_user_id", "workloads", ["user_id"], unique=False)
    op.create_index("ix_workloads_contract_env_status", "workloads", ["contract_id", "env", "status"], unique=False)
    op.create_index("ix_workloads_user_status", "workloads", ["user_id", "status"], unique=False)

    op.create_table(
        "workload_test_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workload_id", sa.UUID(), nullable=False),
        sa.Column("triggered_by", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
    )
    op.create_index("ix_workload_test_runs_workload_id", "workload_test_runs", ["workload_id"], unique=False)

    op.add_column("workloads", sa.Column("test_run_id", sa.UUID(), nullable=True))

    op.create_table(
        "compute_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=False),
        sa.Column("workload_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("output_files", sa.JSON(), nullable=True),
        sa.Column("output_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource_usage", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_compute_runs_idempotency_key"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["sandbox_tasks.id"]),
    )
    op.create_index("ix_compute_runs_contract_id", "compute_runs", ["contract_id"], unique=False)
    op.create_index("ix_compute_runs_contract_status", "compute_runs", ["contract_id", "status"], unique=False)
    op.create_index("ix_compute_runs_status_created", "compute_runs", ["status", "created_at"], unique=False)

    op.create_table(
        "api_endpoints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=False),
        sa.Column("workload_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False, server_default="POST"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="enabled"),
        sa.Column("request_schema", sa.JSON(), nullable=True),
        sa.Column("response_schema", sa.JSON(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("quota_total", sa.Integer(), nullable=True),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_api_endpoints_slug"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
    )
    op.create_index("ix_api_endpoints_owner_id", "api_endpoints", ["owner_id"], unique=False)
    op.create_index("ix_api_endpoints_contract_id", "api_endpoints", ["contract_id"], unique=False)
    op.create_index("ix_api_endpoints_contract_status", "api_endpoints", ["contract_id", "status"], unique=False)

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
    )
    op.create_index("ix_webhook_subscriptions_user_id", "webhook_subscriptions", ["user_id"], unique=False)
    op.create_index("ix_webhook_subs_user_status", "webhook_subscriptions", ["user_id", "status"], unique=False)
    op.create_index("ix_webhook_subs_contract", "webhook_subscriptions", ["contract_id"], unique=False)

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signature", sa.String(length=128), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_snippet", sa.Text(), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_webhook_deliveries_event_id"),
        sa.ForeignKeyConstraint(["subscription_id"], ["webhook_subscriptions.id"]),
    )
    op.create_index("ix_webhook_deliveries_subscription_id", "webhook_deliveries", ["subscription_id"], unique=False)
    op.create_index("ix_webhook_deliveries_sub_status", "webhook_deliveries", ["subscription_id", "status"], unique=False)
    op.create_index("ix_webhook_deliveries_status_retry", "webhook_deliveries", ["status", "next_retry_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_status_retry", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_sub_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_subscription_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_subs_contract", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_subs_user_status", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_subscriptions_user_id", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
    op.drop_index("ix_api_endpoints_contract_status", table_name="api_endpoints")
    op.drop_index("ix_api_endpoints_contract_id", table_name="api_endpoints")
    op.drop_index("ix_api_endpoints_owner_id", table_name="api_endpoints")
    op.drop_table("api_endpoints")
    op.drop_index("ix_compute_runs_status_created", table_name="compute_runs")
    op.drop_index("ix_compute_runs_contract_status", table_name="compute_runs")
    op.drop_index("ix_compute_runs_contract_id", table_name="compute_runs")
    op.drop_table("compute_runs")
    op.drop_column("workloads", "test_run_id")
    op.drop_index("ix_workload_test_runs_workload_id", table_name="workload_test_runs")
    op.drop_table("workload_test_runs")
    op.drop_index("ix_workloads_user_status", table_name="workloads")
    op.drop_index("ix_workloads_contract_env_status", table_name="workloads")
    op.drop_index("ix_workloads_user_id", table_name="workloads")
    op.drop_table("workloads")
    op.drop_index("ix_notebooks_contract", table_name="notebooks")
    op.drop_index("ix_notebooks_user_status", table_name="notebooks")
    op.drop_index("ix_notebooks_user_id", table_name="notebooks")
    op.drop_table("notebooks")
    op.drop_index("ix_sandbox_sessions_workload_id", table_name="sandbox_sessions")
    op.drop_column("sandbox_sessions", "workload_id")
    op.drop_column("sandbox_sessions", "env")
    op.drop_index("ix_contracts_next_compute_at", table_name="contracts")
    op.drop_column("contracts", "last_computed_at")
    op.drop_column("contracts", "next_compute_at")
    op.drop_column("contracts", "sandbox_resource_spec")
    op.drop_column("contracts", "compute_tz")
    op.drop_column("contracts", "compute_hour")
    op.drop_column("contracts", "compute_minute")
    op.drop_column("contracts", "compute_frequency")
    op.drop_column("contracts", "auto_compute")
    op.drop_column("contracts", "delivery_methods")
