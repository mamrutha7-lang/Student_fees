"""Student fees: the fee structure, fee demands, fee charges, and the quota.

Revision ID: c4f81a2e9d07
Revises: b9e3c71a4f20
Create Date: 2026-10-29

Three new tables (app/models/fee.py) and one new column,
`students.admission_quota`. NO BACKFILL: NULL on every existing student is the
truth ("the office has not recorded it") until the office records it, and the
fee preview counts those students so the gap is visible before a fee is raised.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f81a2e9d07"
down_revision: Union[str, None] = "b9e3c71a4f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUOTAS = "('CET', 'MANAGEMENT')"
HEADS = "('TUITION', 'EXAMINATION', 'LIBRARY_LAB', 'OTHER')"
STATUSES = "('PENDING', 'PAID', 'CANCELLED')"
METHODS = "('RAZORPAY', 'CASH', 'CHEQUE', 'DEMAND_DRAFT', 'BANK_TRANSFER')"


def upgrade() -> None:
    op.add_column("students", sa.Column("admission_quota", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_student_admission_quota", "students",
        f"admission_quota IS NULL OR admission_quota IN {QUOTAS}",
    )

    op.create_table(
        "fee_structures",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("academic_year", sa.String(length=7), nullable=False),
        sa.Column("quota", sa.String(length=20), nullable=False),
        sa.Column("fee_head", sa.String(length=30), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"quota IN {QUOTAS}", name="ck_fee_structure_quota"),
        sa.CheckConstraint(f"fee_head IN {HEADS}", name="ck_fee_structure_head"),
        sa.CheckConstraint("amount_paise > 0", name="ck_fee_structure_amount"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("academic_year", "quota", "fee_head", name="uq_fee_structure_slot"),
    )

    op.create_table(
        "fee_demands",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("quota", sa.String(length=20), nullable=True),
        sa.Column("semester", sa.Integer(), nullable=True),
        sa.Column("academic_year", sa.String(length=7), nullable=False),
        sa.Column("fee_head", sa.String(length=30), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_fee_demand_amount"),
        sa.CheckConstraint(f"quota IS NULL OR quota IN {QUOTAS}", name="ck_fee_demand_quota"),
        sa.CheckConstraint(f"fee_head IS NULL OR fee_head IN {HEADS}", name="ck_fee_demand_head"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fee_demands_academic_year", "fee_demands", ["academic_year"])
    op.create_index("ix_fee_demands_created_at", "fee_demands", ["created_at"])
    op.create_index("ix_fee_demands_created_by_user_id", "fee_demands", ["created_by_user_id"])

    op.create_table(
        "fee_charges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("demand_id", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_method", sa.String(length=30), nullable=True),
        sa.Column("payment_reference", sa.String(length=120), nullable=True),
        sa.Column("recorded_by_user_id", sa.String(), nullable=True),
        sa.Column("receipt_number", sa.String(length=40), nullable=True),
        sa.Column("razorpay_order_id", sa.String(length=100), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_fee_charge_amount"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_fee_charge_status"),
        sa.CheckConstraint(
            f"payment_method IS NULL OR payment_method IN {METHODS}", name="ck_fee_charge_method"
        ),
        sa.CheckConstraint(
            "status <> 'PAID' OR (paid_at IS NOT NULL AND payment_method IS NOT NULL "
            "AND receipt_number IS NOT NULL)",
            name="ck_fee_charge_paid_complete",
        ),
        sa.ForeignKeyConstraint(["demand_id"], ["fee_demands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("demand_id", "student_id", name="uq_fee_charge_demand_student"),
        sa.UniqueConstraint("receipt_number"),
        sa.UniqueConstraint("razorpay_order_id"),
        sa.UniqueConstraint("razorpay_payment_id"),
    )
    op.create_index("ix_fee_charges_student_id", "fee_charges", ["student_id"])
    op.create_index("ix_fee_charges_recorded_by_user_id", "fee_charges", ["recorded_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_fee_charges_recorded_by_user_id", table_name="fee_charges")
    op.drop_index("ix_fee_charges_student_id", table_name="fee_charges")
    op.drop_table("fee_charges")
    op.drop_index("ix_fee_demands_created_by_user_id", table_name="fee_demands")
    op.drop_index("ix_fee_demands_created_at", table_name="fee_demands")
    op.drop_index("ix_fee_demands_academic_year", table_name="fee_demands")
    op.drop_table("fee_demands")
    op.drop_table("fee_structures")
    op.drop_constraint("ck_student_admission_quota", "students", type_="check")
    op.drop_column("students", "admission_quota")
