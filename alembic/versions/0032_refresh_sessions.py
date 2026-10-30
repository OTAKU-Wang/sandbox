"""CR-2026-10-15 §4.10/§4.11（M3/M4，用户 2026-10-30 拍板）— refresh 会话表 + MFA 确认时刻。

生产门 auth 安全两条，全加性：

* **M3** 新表 ``refresh_sessions``：``/auth/refresh`` 过去只验签名 + ``is_active``，
  已签发的 refresh token 在有效期内无法作废。现在每次登录（密码+MFA 通过、注册、
  证书登录）与每次轮换都落一行，``jti`` 唯一；logout/轮换置 ``revoked``，
  过期在读取侧按 ``expires_at`` 判定，不依赖清扫任务。行永不物理删除。
* **MFA 重验**：``user_mfa`` 加 ``confirmed_at``（可空）。刷新门禁：
  ``mfa.confirmed`` 且会话 ``issued_at`` 早于 ``confirmed_at`` ⇒ 拒绝，要求重新
  走 MFA 登录。**存量行不回填**：回填任何猜测的时刻都是在伪造取证事实；
  NULL 时应用侧回落到 ``created_at``（行创建必然早于确认，是确认时刻的下界，
  只会放行「确认之后、真实确认之前」这一有界窗口内的旧会话，属诚实披露的
  残留缺口，新一轮 enroll/verify 后即消除）。

downgrade 只还原模式：已吊销/已轮换的会话行无法复原。

Revision ID: 0032_refresh_sessions
Revises: 0031_ocr_seed_bigint
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0032_refresh_sessions"
down_revision = "0031_ocr_seed_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_refresh_sessions_jti"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"], unique=False)
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"], unique=False)

    op.add_column(
        "user_mfa",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_mfa", "confirmed_at")
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
