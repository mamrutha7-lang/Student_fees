"""Student fees: what the office charges, and whether each student has paid.

Three tables:

    fee_structures   the standard amount for (academic year, quota, fee head).
                     It only FILLS the Fee amount box; the office can type
                     another amount, and changing it never touches fees
                     already raised.
    fee_demands      one act of raising a fee: the title, the amount, the due
                     date and who it was aimed at. The single-student dialog
                     writes one demand per student; the group raise writes one
                     demand for many students.
    fee_charges      one row per (demand, student): the money claim on that
                     student, and how it was settled.

AMOUNTS ARE INTEGER PAISE. Razorpay speaks paise, and a float column makes
"is this fee fully paid" a comparison of epsilons. The API speaks whole rupees
and converts at the edge.

STATUS IS STORED AS PENDING / PAID / CANCELLED, AND OVERDUE IS DERIVED. A fee
becomes overdue by the calendar, not by anybody's act, so storing it would
need a nightly job to keep it true and would be wrong for the hours that job
had not run yet. `fees.display_status_for` derives it from the due date.

Plain strings with CHECK constraints rather than Postgres enums, so adding a
fee head is a data change and not a CREATE TYPE migration.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


# -- vocabularies --------------------------------------------------------------

QUOTA_CET: Final = "CET"
QUOTA_MANAGEMENT: Final = "MANAGEMENT"
QUOTAS: Final[tuple[str, ...]] = (QUOTA_CET, QUOTA_MANAGEMENT)
QUOTA_LABELS: Final[dict[str, str]] = {QUOTA_CET: "CET", QUOTA_MANAGEMENT: "Management"}

FEE_HEAD_LABELS: Final[dict[str, str]] = {
    "TUITION": "Tuition",
    "EXAMINATION": "Examination",
    "LIBRARY_LAB": "Library and lab",
    "OTHER": "Other",
}
FEE_HEADS: Final[tuple[str, ...]] = tuple(FEE_HEAD_LABELS)

CHARGE_PENDING: Final = "PENDING"
CHARGE_PAID: Final = "PAID"
CHARGE_CANCELLED: Final = "CANCELLED"
CHARGE_STATUSES: Final[tuple[str, ...]] = (CHARGE_PENDING, CHARGE_PAID, CHARGE_CANCELLED)
#: Derived, never stored: a PENDING charge whose due date has passed.
DISPLAY_OVERDUE: Final = "OVERDUE"

PAYMENT_RAZORPAY: Final = "RAZORPAY"
#: The ways the accounts office records a payment it took itself.
OFFICE_PAYMENT_METHODS: Final[tuple[str, ...]] = (
    "CASH", "CHEQUE", "DEMAND_DRAFT", "BANK_TRANSFER",
)
PAYMENT_METHODS: Final[tuple[str, ...]] = (PAYMENT_RAZORPAY, *OFFICE_PAYMENT_METHODS)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class FeeStructure(Base):
    __tablename__ = "fee_structures"
    __table_args__ = (
        UniqueConstraint("academic_year", "quota", "fee_head", name="uq_fee_structure_slot"),
        CheckConstraint(_in("quota", QUOTAS), name="ck_fee_structure_quota"),
        CheckConstraint(_in("fee_head", FEE_HEADS), name="ck_fee_structure_head"),
        CheckConstraint("amount_paise > 0", name="ck_fee_structure_amount"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    academic_year: Mapped[str] = mapped_column(String(7))
    quota: Mapped[str] = mapped_column(String(20))
    fee_head: Mapped[str] = mapped_column(String(30))
    amount_paise: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FeeDemand(Base):
    __tablename__ = "fee_demands"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_fee_demand_amount"),
        CheckConstraint(f"quota IS NULL OR {_in('quota', QUOTAS)}", name="ck_fee_demand_quota"),
        CheckConstraint(
            f"fee_head IS NULL OR {_in('fee_head', FEE_HEADS)}", name="ck_fee_demand_head"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(120))
    #: The office's "Note to student", shown beside the fee on My fees.
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Who the demand was aimed at. NULL quota = every quota.
    quota: Mapped[str | None] = mapped_column(String(20), nullable=True)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    academic_year: Mapped[str] = mapped_column(String(7), index=True)
    fee_head: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class FeeCharge(Base):
    __tablename__ = "fee_charges"
    __table_args__ = (
        UniqueConstraint("demand_id", "student_id", name="uq_fee_charge_demand_student"),
        CheckConstraint("amount_paise > 0", name="ck_fee_charge_amount"),
        CheckConstraint(_in("status", CHARGE_STATUSES), name="ck_fee_charge_status"),
        CheckConstraint(
            f"payment_method IS NULL OR {_in('payment_method', PAYMENT_METHODS)}",
            name="ck_fee_charge_method",
        ),
        # A PAID charge always says when and how. Without this a half-written
        # payment reads as Paid with no receipt behind it.
        CheckConstraint(
            "status <> 'PAID' OR (paid_at IS NOT NULL AND payment_method IS NOT NULL "
            "AND receipt_number IS NOT NULL)",
            name="ck_fee_charge_paid_complete",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    demand_id: Mapped[str] = mapped_column(
        # No separate index: `uq_fee_charge_demand_student` leads with this column.
        ForeignKey("fee_demands.id", ondelete="CASCADE")
    )
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    #: Copied from the demand at raise time: what the student was asked for.
    amount_paise: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default=CHARGE_PENDING, server_default=CHARGE_PENDING
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Cheque number, draft number or bank reference. Optional for cash.
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    receipt_number: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
