"""2026-11-03 沙箱系统对齐计划 Task 3：场景/建模项目/团队/对账单/通知 5 张新表。

全部加性，与 app/models/{team,scenario,modeling_project,billing_invoice,
user_notification}.py 成对（verify_schema_parity 门）。5 张表都是上层聚合
实体，id 引用（created_by / team_id / contract_id 等）如实存 UUID 不建
外键——跨表生命周期各自独立，级联语义由 API 层把关，不伪装 DB 级约束。

- ``teams``：团队 + 团队级 CPU/内存/存储配额；成员与角色权限为 JSON 快照。
- ``scenarios``：业务场景（发布/承接/交付方式预埋），承接后转建模项目。
- ``modeling_projects``：建模项目协同空间（DAG 定义、里程碑、多语言偏好）。
- ``billing_invoices``：按账期的计量对账单；invoice_no 唯一（uq_ 约束名，
  与 refresh_sessions.jti 同形状）；单价与明细在生成时刻固化快照。
- ``user_notifications``：通知中心的持久化通知，实时待办读取侧现算不落表；
  is_read 的 server_default 用 ``text("false")``（D-e2e-08：text("0") 在
  真实 PG 被拒）。

JSON 列生产类型是 PG 原生 JSONB；SQLite（CI/parity 道）没有 JSONB 编译器，
with_variant 如实降级为 JSON，两侧在 PG 上收敛为 JSONB。

downgrade 逆序 drop 索引与表。

Revision ID: 0035_sandbox_realignment_entities
Revises: 0034_integrity_patrol_columns
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035_sandbox_realignment_entities"
down_revision = "0034_integrity_patrol_columns"
branch_labels = None
depends_on = None

# JSONB 生产语义 + SQLite 如实降级（与 app/models 侧 _JSONB 同形状）。
_JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("org_id", sa.String(length=128), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cpu_quota_cores", sa.Integer(), nullable=False),
        sa.Column("memory_quota_gb", sa.Integer(), nullable=False),
        sa.Column("storage_quota_gb", sa.Integer(), nullable=False),
        sa.Column("members", _JSONB, nullable=False),
        sa.Column("role_permissions", _JSONB, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_org_id", "teams", ["org_id"], unique=False)
    op.create_index("ix_teams_status", "teams", ["status"], unique=False)

    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=True),
        sa.Column("business_background", sa.Text(), nullable=True),
        sa.Column("target_description", sa.Text(), nullable=True),
        sa.Column("delivery_scene", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("suggested_resource_ids", _JSONB, nullable=False),
        sa.Column("compliance_constraints", _JSONB, nullable=False),
        sa.Column("delivery_tech_config", _JSONB, nullable=False),
        sa.Column("accepted_team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenarios_status", "scenarios", ["status"], unique=False)
    op.create_index("ix_scenarios_delivery_scene", "scenarios", ["delivery_scene"], unique=False)

    op.create_table(
        "modeling_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("environment_type", sa.String(length=32), nullable=False),
        sa.Column("preferred_language", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("participants", _JSONB, nullable=False),
        sa.Column("milestones", _JSONB, nullable=False),
        sa.Column("dag_definition", _JSONB, nullable=False),
        sa.Column("linked_notebook_ids", _JSONB, nullable=False),
        sa.Column("linked_workload_ids", _JSONB, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modeling_projects_team_id", "modeling_projects", ["team_id"], unique=False)
    op.create_index("ix_modeling_projects_scenario_id", "modeling_projects", ["scenario_id"], unique=False)
    op.create_index("ix_modeling_projects_status", "modeling_projects", ["status"], unique=False)

    op.create_table(
        "billing_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_no", sa.String(length=64), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("billing_period", sa.String(length=32), nullable=False),
        sa.Column("api_calls_count", sa.Integer(), nullable=False),
        sa.Column("compute_cpu_hours", sa.Float(), nullable=False),
        sa.Column("storage_gb_days", sa.Float(), nullable=False),
        sa.Column("egress_bytes", sa.Integer(), nullable=False),
        sa.Column("unit_price_config", _JSONB, nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("line_items", _JSONB, nullable=False),
        sa.Column("blockchain_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_no", name="uq_billing_invoices_invoice_no"),
    )
    op.create_index("ix_billing_invoices_contract_id", "billing_invoices", ["contract_id"], unique=False)
    op.create_index("ix_billing_invoices_billing_period", "billing_invoices", ["billing_period"], unique=False)
    op.create_index("ix_billing_invoices_status", "billing_invoices", ["status"], unique=False)

    op.create_table(
        "user_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("target_path", sa.String(length=256), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("extra_meta", _JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_notifications_user_read", "user_notifications", ["user_id", "is_read"], unique=False)
    op.create_index("ix_user_notifications_category", "user_notifications", ["category"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_notifications_category", table_name="user_notifications")
    op.drop_index("ix_user_notifications_user_read", table_name="user_notifications")
    op.drop_table("user_notifications")
    op.drop_index("ix_billing_invoices_status", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_billing_period", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_contract_id", table_name="billing_invoices")
    op.drop_table("billing_invoices")
    op.drop_index("ix_modeling_projects_status", table_name="modeling_projects")
    op.drop_index("ix_modeling_projects_scenario_id", table_name="modeling_projects")
    op.drop_index("ix_modeling_projects_team_id", table_name="modeling_projects")
    op.drop_table("modeling_projects")
    op.drop_index("ix_scenarios_delivery_scene", table_name="scenarios")
    op.drop_index("ix_scenarios_status", table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index("ix_teams_status", table_name="teams")
    op.drop_index("ix_teams_org_id", table_name="teams")
    op.drop_table("teams")
