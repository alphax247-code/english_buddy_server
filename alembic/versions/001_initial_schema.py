"""Initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2026-04-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("mobile", sa.String, unique=True, nullable=False),
        sa.Column("role", sa.String, nullable=False, server_default="student"),
        sa.Column("is_paid", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_banned", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("affiliate_code", sa.String, nullable=True),
        sa.Column("xp", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_sessions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("device_id", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("mobile", sa.String, nullable=False),
        sa.Column("reference", sa.String, unique=True, nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("method", sa.String, nullable=False, server_default="mpesa"),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("provider_payment_id", sa.String, nullable=True),
        sa.Column("provider_reference", sa.String, nullable=True),
        sa.Column("checkout_url", sa.Text, nullable=True),
        sa.Column("affiliate_code", sa.String, nullable=True),
        sa.Column("commission_amount", sa.Integer, nullable=False, server_default="0"),
        sa.Column("commission_paid", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "affiliates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String, unique=True, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("mobile", sa.String, nullable=True),
        sa.Column("commission_rate", sa.Integer, nullable=False, server_default="20"),
        sa.Column("total_referrals", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_earnings", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("password", sa.String, nullable=True),
        sa.Column("password_reset_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "payouts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("affiliate_id", sa.Integer, nullable=False),
        sa.Column("affiliate_code", sa.String, nullable=False),
        sa.Column("affiliate_name", sa.String, nullable=False),
        sa.Column("affiliate_mobile", sa.String, nullable=True),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("method", sa.String, nullable=False, server_default="bank_transfer"),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("provider_payout_id", sa.String, nullable=True),
        sa.Column("provider_reference", sa.String, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("paid_by", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )

    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("conversation_id", sa.Integer, nullable=False),
        sa.Column("scenario", sa.String, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("corrected", sa.Text, nullable=True),
        sa.Column("grammar", sa.Text, nullable=True),
        sa.Column("pronunciation", sa.Text, nullable=True),
        sa.Column("examples", sa.JSON, nullable=True),
        sa.Column("grammar_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pronunciation_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("xp_earned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("double_xp", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String, nullable=False),
    )

    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("extra_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("start_date", sa.String, nullable=True),
        sa.Column("end_date", sa.String, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("promotions")
    op.drop_table("practice_sessions")
    op.drop_table("payouts")
    op.drop_table("affiliates")
    op.drop_table("payments")
    op.drop_table("users")
