"""The fee rules. The routers check WHO is asking; this module decides WHAT happens.

`routers/admin_fees.py` (the Main Admin) and `routers/student_fees.py` (the
student, and Razorpay) both come through here, so the office's screen, the
student's screen and the PDFs read one set of numbers.

Four rules worth knowing before editing:

1. A "current student" is `students.status = ACTIVE`, role STUDENT, and an
   account that has not been removed. A graduate keeps their row and their
   past fees, but is never charged a new one.
2. One live fee per (student, fee head, academic year). A second Tuition for
   2026-27 is a 409, because the usual cause is a double press of Raise fee.
3. A charge becomes PAID only through `mark_charge_paid`, which is idempotent:
   the browser's verify and Razorpay's webhook can both arrive, in either
   order, and the second changes nothing.
4. OVERDUE is derived (`display_status_for`), never stored.

Nothing here commits. The caller commits, so the audit row and the change land
together or not at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .clock import local_today
from .models.cohort import Cohort
from .models.fee import (
    CHARGE_CANCELLED,
    CHARGE_PAID,
    CHARGE_PENDING,
    DISPLAY_OVERDUE,
    FEE_HEAD_LABELS,
    OFFICE_PAYMENT_METHODS,
    QUOTA_LABELS,
    FeeCharge,
    FeeDemand,
    FeeStructure,
)
from .models.institution import AcademicCourse, Department
from .models.user import STUDENT_STATUS_ACTIVE, Role, Student, User
from .usn import usn_is

ROSTER_NOT_RAISED: Final = "NOT_RAISED"
ROSTER_PENDING: Final = "PENDING"
ROSTER_OVERDUE: Final = "OVERDUE"
ROSTER_PAID: Final = "PAID"

#: Payment methods whose paper carries a number the office must write down.
METHODS_NEEDING_REFERENCE: Final[tuple[str, ...]] = ("CHEQUE", "DEMAND_DRAFT", "BANK_TRANSFER")

_ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{2})$")
#: The academic year starts in July (batches run July to the end of June).
ACADEMIC_YEAR_START_MONTH: Final = 7


class FeeRuleError(Exception):
    """A refusal with the HTTP status and the sentence the screen shows."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# -- small pure helpers ----------------------------------------------------------

def academic_year_for(day: date) -> str:
    """2026-10-29 -> "2026-27"; 2027-03-01 -> "2026-27"."""
    start = day.year if day.month >= ACADEMIC_YEAR_START_MONTH else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def academic_year_is_valid(value: str | None) -> bool:
    if not value:
        return False
    match = _ACADEMIC_YEAR.match(value)
    if match is None:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    return 2000 <= start <= 2100 and end == (start + 1) % 100


def display_status_for(charge: FeeCharge, due_on: date | None, today: date) -> str:
    """The chip the screens draw: PENDING turns OVERDUE the day after it is due."""
    if charge.status == CHARGE_PENDING and due_on is not None and due_on < today:
        return DISPLAY_OVERDUE
    return charge.status


def receipt_number_for(charge: FeeCharge, paid_at: datetime) -> str:
    """Stable and unique: the payment day plus the start of the charge's own id."""
    return f"RCPT-{paid_at:%Y%m%d}-{charge.id[:8].upper()}"


def mark_charge_paid(
    charge: FeeCharge,
    *,
    method: str,
    reference: str | None = None,
    razorpay_payment_id: str | None = None,
    recorded_by_user_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Settle a charge. Returns True if it changed, False if it was already
    paid (the second of verify and webhook). The caller must hold the row
    FOR UPDATE."""
    if charge.status == CHARGE_PAID:
        return False
    if charge.status == CHARGE_CANCELLED:
        raise FeeRuleError(409, "This fee was cancelled and cannot be paid.")
    paid_at = now or datetime.now(timezone.utc)
    charge.status = CHARGE_PAID
    charge.paid_at = paid_at
    charge.payment_method = method
    charge.payment_reference = reference
    charge.razorpay_payment_id = razorpay_payment_id
    charge.recorded_by_user_id = recorded_by_user_id
    charge.receipt_number = receipt_number_for(charge, paid_at)
    return True


# -- value objects the routers and the PDFs read ------------------------------------

@dataclass(frozen=True)
class StudentFeeProfile:
    student_id: str
    usn: str | None
    name: str
    email: str
    course_name: str | None
    department_name: str | None
    batch_label: str | None
    quota: str | None
    current_semester: int | None


@dataclass(frozen=True)
class StatementLine:
    charge_id: str
    title: str
    note: str | None
    amount_paise: int
    due_on: date | None
    status: str
    display_status: str
    paid_at: datetime | None
    fee_head: str | None
    academic_year: str | None
    quota: str | None
    raised_on: date | None
    payment_method: str | None
    payment_reference: str | None
    receipt_number: str | None
    razorpay_payment_id: str | None


@dataclass(frozen=True)
class StatementTotals:
    total_raised_paise: int
    paid_paise: int
    balance_paise: int
    fee_count: int
    paid_count: int
    pending_count: int
    overdue_count: int


@dataclass(frozen=True)
class FeeStatement:
    student: StudentFeeProfile
    academic_year: str
    generated_on: date
    lines: list[StatementLine]
    totals: StatementTotals


@dataclass
class RosterRow:
    student_id: str
    usn: str | None
    name: str
    course_name: str | None
    quota: str | None
    academic_year: str
    amount_paise: int = 0
    fee_count: int = 0
    unpaid_count: int = 0
    raised_on: date | None = None
    next_due_on: date | None = None
    status: str = ROSTER_NOT_RAISED
    _overdue: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class RosterTotals:
    students: int
    students_with_fee: int
    pending_paise: int
    paid_paise: int
    payments_recorded: int
    overdue_students: int


@dataclass(frozen=True)
class RaisedItem:
    student_id: str
    name: str
    charge_id: str
    demand_id: str
    amount_paise: int


@dataclass(frozen=True)
class SkippedItem:
    student_id: str
    name: str
    reason: str


# -- queries shared by the group raise and the service ------------------------------

def _current_student_clauses() -> list:
    return [
        Student.status == STUDENT_STATUS_ACTIVE,
        User.role == Role.STUDENT,
        User.deleted_at.is_(None),
    ]


def eligible_student_ids(db: Session, *, quota: str | None, semester: int | None) -> list[str]:
    """Current students a group fee would reach. The quota match is EXACT: a
    student with no recorded quota is reached only when `quota` is None."""
    stmt = select(Student.id).join(User, User.id == Student.user_id).where(*_current_student_clauses())
    if quota is not None:
        stmt = stmt.where(Student.admission_quota == quota)
    if semester is not None:
        stmt = stmt.where(Student.current_semester == semester)
    return list(db.scalars(stmt.order_by(Student.id)))


def _live_charge_exists(db: Session, student_id: str, fee_head: str, academic_year: str) -> bool:
    return db.scalar(
        select(func.count())
        .select_from(FeeCharge)
        .join(FeeDemand, FeeDemand.id == FeeCharge.demand_id)
        .where(
            FeeCharge.student_id == student_id,
            FeeCharge.status != CHARGE_CANCELLED,
            FeeDemand.fee_head == fee_head,
            FeeDemand.academic_year == academic_year,
        )
    ) > 0


def raise_fee(
    db: Session,
    *,
    title: str,
    description: str | None,
    amount_paise: int,
    due_on: date | None,
    quota: str | None,
    semester: int | None,
    created_by_user_id: str | None,
    academic_year: str | None = None,
    fee_head: str | None = None,
) -> tuple[FeeDemand, int]:
    """The group raise: one demand, one charge per eligible student. A student
    who already has a live fee for the same head and year is skipped, so
    raising twice never bills anybody twice."""
    year = academic_year or academic_year_for(local_today())
    demand = FeeDemand(
        title=title,
        description=description or None,
        amount_paise=amount_paise,
        due_on=due_on,
        quota=quota,
        semester=semester,
        academic_year=year,
        fee_head=fee_head,
        created_by_user_id=created_by_user_id,
    )
    db.add(demand)
    db.flush()
    charged = 0
    for student_id in eligible_student_ids(db, quota=quota, semester=semester):
        if fee_head is not None and _live_charge_exists(db, student_id, fee_head, year):
            continue
        db.add(FeeCharge(demand_id=demand.id, student_id=student_id, amount_paise=amount_paise))
        charged += 1
    db.flush()
    return demand, charged


# -- the service -------------------------------------------------------------------

def _profile_select():
    department_id = func.coalesce(Cohort.department_id, Student.department_id)
    return (
        select(
            Student.id,
            Student.usn,
            User.name,
            User.email,
            AcademicCourse.name.label("course_name"),
            Department.name.label("department_name"),
            Cohort.batch_label,
            Student.admission_quota,
            Student.current_semester,
        )
        .join(User, User.id == Student.user_id)
        .outerjoin(Cohort, Cohort.id == Student.cohort_id)
        .outerjoin(AcademicCourse, AcademicCourse.id == Cohort.course_id)
        .outerjoin(Department, Department.id == department_id)
    )


def _profile_from(row) -> StudentFeeProfile:
    return StudentFeeProfile(
        student_id=row.id,
        usn=row.usn,
        name=row.name,
        email=row.email,
        course_name=row.course_name,
        department_name=row.department_name,
        batch_label=row.batch_label,
        quota=row.admission_quota,
        current_semester=row.current_semester,
    )


class FeeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -- students ---------------------------------------------------------------

    def profile(self, student_id: str, *, current_only: bool) -> StudentFeeProfile:
        stmt = _profile_select().where(Student.id == student_id)
        if current_only:
            stmt = stmt.where(*_current_student_clauses())
        row = self.db.execute(stmt).first()
        if row is None:
            raise FeeRuleError(404, "No current student with that ID.")
        return _profile_from(row)

    def find_student_by_usn(self, usn: str) -> StudentFeeProfile:
        usn = usn.strip()
        if not usn:
            raise FeeRuleError(422, "Type the student's ID (USN).")
        row = self.db.execute(
            _profile_select().where(usn_is(Student.usn, usn), *_current_student_clauses())
        ).first()
        if row is None:
            raise FeeRuleError(404, f"No current student has the ID {usn.upper()}.")
        return _profile_from(row)

    def search_students(
        self,
        *,
        text: str | None,
        department_id: str | None,
        course_id: str | None,
        limit: int,
    ) -> tuple[list[StudentFeeProfile], int]:
        """The Student ID dropdown: matches on USN or name, newest USN order."""
        stmt = _profile_select().where(*_current_student_clauses())
        if department_id:
            stmt = stmt.where(func.coalesce(Cohort.department_id, Student.department_id) == department_id)
        if course_id:
            stmt = stmt.where(Cohort.course_id == course_id)
        if text and text.strip():
            needle = f"%{text.strip().upper()}%"
            stmt = stmt.where(
                func.upper(func.coalesce(Student.usn, "")).like(needle)
                | func.upper(User.name).like(needle)
            )
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.execute(stmt.order_by(Student.usn.asc().nulls_last(), User.name).limit(limit)).all()
        return [_profile_from(r) for r in rows], int(total)

    # -- the fee structure ---------------------------------------------------------

    def standard_amount_paise(self, academic_year: str, quota: str, fee_head: str) -> int | None:
        return self.db.scalar(
            select(FeeStructure.amount_paise).where(
                FeeStructure.academic_year == academic_year,
                FeeStructure.quota == quota,
                FeeStructure.fee_head == fee_head,
            )
        )

    def list_standard_amounts(self, academic_year: str | None) -> list[FeeStructure]:
        stmt = select(FeeStructure)
        if academic_year:
            stmt = stmt.where(FeeStructure.academic_year == academic_year)
        return list(
            self.db.scalars(
                stmt.order_by(FeeStructure.academic_year.desc(), FeeStructure.quota, FeeStructure.fee_head)
            )
        )

    def save_standard_amount(
        self, academic_year: str, quota: str, fee_head: str, amount_paise: int
    ) -> tuple[FeeStructure, int | None]:
        """Set one standard amount. Returns the row and the amount it had
        before (None if new). Fees already raised are not touched."""
        if not academic_year_is_valid(academic_year):
            raise FeeRuleError(422, "The academic year must look like 2026-27.")
        row = self.db.scalar(
            select(FeeStructure)
            .where(
                FeeStructure.academic_year == academic_year,
                FeeStructure.quota == quota,
                FeeStructure.fee_head == fee_head,
            )
            .with_for_update()
        )
        if row is None:
            row = FeeStructure(
                academic_year=academic_year, quota=quota, fee_head=fee_head, amount_paise=amount_paise
            )
            self.db.add(row)
            self.db.flush()
            return row, None
        previous = row.amount_paise
        row.amount_paise = amount_paise
        self.db.flush()
        return row, previous

    # -- raising one student's fee --------------------------------------------------

    def raise_fee_for_student(
        self,
        *,
        student_id: str,
        academic_year: str,
        quota: str,
        fee_head: str,
        amount_paise: int,
        due_on: date,
        note: str | None,
        raised_by_user_id: str | None,
    ) -> tuple[FeeDemand, FeeCharge, bool]:
        """Returns (demand, charge, quota_recorded). The student's row is held
        FOR UPDATE so two presses of Raise fee cannot both pass the duplicate
        check."""
        if not academic_year_is_valid(academic_year):
            raise FeeRuleError(422, "The academic year must look like 2026-27.")
        if fee_head not in FEE_HEAD_LABELS:
            raise FeeRuleError(422, "Pick a fee head.")
        student = self.db.scalar(
            select(Student)
            .join(User, User.id == Student.user_id)
            .where(Student.id == student_id, *_current_student_clauses())
            .with_for_update(of=Student)
        )
        if student is None:
            raise FeeRuleError(404, "No current student with that ID.")

        quota_recorded = False
        if student.admission_quota is None:
            student.admission_quota = quota
            quota_recorded = True
        elif student.admission_quota != quota:
            recorded = QUOTA_LABELS.get(student.admission_quota, student.admission_quota)
            raise FeeRuleError(
                409,
                f"This student is recorded under the {recorded} quota. "
                "Change the quota on their record first if that is wrong.",
            )

        if _live_charge_exists(self.db, student.id, fee_head, academic_year):
            raise FeeRuleError(
                409,
                f"A {FEE_HEAD_LABELS[fee_head]} fee for {academic_year} is already raised "
                "for this student.",
            )

        demand = FeeDemand(
            title=FEE_HEAD_LABELS[fee_head],
            description=note,
            amount_paise=amount_paise,
            due_on=due_on,
            quota=quota,
            semester=student.current_semester,
            academic_year=academic_year,
            fee_head=fee_head,
            created_by_user_id=raised_by_user_id,
        )
        self.db.add(demand)
        self.db.flush()
        charge = FeeCharge(
            demand_id=demand.id,
            student_id=student.id,
            amount_paise=amount_paise,
            status=CHARGE_PENDING,
        )
        self.db.add(charge)
        self.db.flush()
        return demand, charge, quota_recorded

    def raise_fee_for_students(
        self,
        *,
        student_ids: list[str],
        academic_year: str,
        fee_head: str,
        amount_paise: int | None,
        due_on: date,
        note: str | None,
        raised_by_user_id: str | None,
    ) -> tuple[list[RaisedItem], list[SkippedItem]]:
        """"Raise fee for N selected". Each student is charged on their OWN
        recorded quota. A student who cannot be charged is skipped with the
        reason, inside a savepoint, so one refusal never undoes the others."""
        if not academic_year_is_valid(academic_year):
            raise FeeRuleError(422, "The academic year must look like 2026-27.")
        if fee_head not in FEE_HEAD_LABELS:
            raise FeeRuleError(422, "Pick a fee head.")
        raised: list[RaisedItem] = []
        skipped: list[SkippedItem] = []
        seen: set[str] = set()
        for student_id in student_ids:
            if student_id in seen:
                continue
            seen.add(student_id)
            row = self.db.execute(
                select(Student.admission_quota, User.name)
                .join(User, User.id == Student.user_id)
                .where(Student.id == student_id)
            ).first()
            if row is None:
                skipped.append(SkippedItem(student_id, "", "No such student."))
                continue
            quota, name = row
            if quota is None:
                skipped.append(SkippedItem(student_id, name, "Quota not recorded."))
                continue
            amount = amount_paise
            if amount is None:
                amount = self.standard_amount_paise(academic_year, quota, fee_head)
                if amount is None:
                    skipped.append(SkippedItem(
                        student_id, name,
                        f"No standard {FEE_HEAD_LABELS[fee_head]} amount for "
                        f"{QUOTA_LABELS[quota]} quota in {academic_year}. Enter an amount.",
                    ))
                    continue
            savepoint = self.db.begin_nested()
            try:
                demand, charge, _ = self.raise_fee_for_student(
                    student_id=student_id,
                    academic_year=academic_year,
                    quota=quota,
                    fee_head=fee_head,
                    amount_paise=amount,
                    due_on=due_on,
                    note=note,
                    raised_by_user_id=raised_by_user_id,
                )
            except FeeRuleError as error:
                savepoint.rollback()
                skipped.append(SkippedItem(student_id, name, error.message))
                continue
            savepoint.commit()
            raised.append(RaisedItem(student_id, name, charge.id, demand.id, amount))
        return raised, skipped

    # -- payments ------------------------------------------------------------------

    def record_office_payment(
        self,
        *,
        charge_id: str,
        method: str,
        reference: str | None,
        recorded_by_user_id: str | None,
    ) -> FeeCharge:
        if method not in OFFICE_PAYMENT_METHODS:
            raise FeeRuleError(422, "Pick how the payment was made.")
        if method in METHODS_NEEDING_REFERENCE and not reference:
            raise FeeRuleError(422, "Enter the cheque, draft or bank reference number.")
        charge = self.db.scalar(
            select(FeeCharge).where(FeeCharge.id == charge_id).with_for_update()
        )
        if charge is None:
            raise FeeRuleError(404, "Fee not found.")
        if charge.status == CHARGE_PAID:
            raise FeeRuleError(409, "This fee is already paid.")
        mark_charge_paid(
            charge, method=method, reference=reference, recorded_by_user_id=recorded_by_user_id
        )
        self.db.flush()
        return charge

    # -- the student's statement ---------------------------------------------------

    def statement_for(self, student_id: str) -> FeeStatement:
        """Every live fee the student has, newest academic year first, and the
        three totals. The My fees screen, the statement PDF and each receipt
        are all built from this."""
        profile = self.profile(student_id, current_only=False)
        today = local_today()
        rows = self.db.execute(
            select(FeeCharge, FeeDemand)
            .join(FeeDemand, FeeDemand.id == FeeCharge.demand_id)
            .where(FeeCharge.student_id == student_id, FeeCharge.status != CHARGE_CANCELLED)
            .order_by(
                FeeDemand.academic_year.desc(),
                FeeDemand.due_on.asc().nulls_last(),
                FeeCharge.created_at.asc(),
            )
        ).all()
        lines = [
            StatementLine(
                charge_id=charge.id,
                title=demand.title,
                note=demand.description,
                amount_paise=charge.amount_paise,
                due_on=demand.due_on,
                status=charge.status,
                display_status=display_status_for(charge, demand.due_on, today),
                paid_at=charge.paid_at,
                fee_head=demand.fee_head,
                academic_year=demand.academic_year,
                quota=demand.quota or profile.quota,
                raised_on=charge.created_at.date() if charge.created_at else None,
                payment_method=charge.payment_method,
                payment_reference=charge.payment_reference,
                receipt_number=charge.receipt_number,
                razorpay_payment_id=charge.razorpay_payment_id,
            )
            for charge, demand in rows
        ]
        paid = [line for line in lines if line.status == CHARGE_PAID]
        total = sum(line.amount_paise for line in lines)
        paid_total = sum(line.amount_paise for line in paid)
        totals = StatementTotals(
            total_raised_paise=total,
            paid_paise=paid_total,
            balance_paise=total - paid_total,
            fee_count=len(lines),
            paid_count=len(paid),
            pending_count=sum(1 for line in lines if line.display_status == CHARGE_PENDING),
            overdue_count=sum(1 for line in lines if line.display_status == DISPLAY_OVERDUE),
        )
        return FeeStatement(
            student=profile,
            academic_year=academic_year_for(today),
            generated_on=today,
            lines=lines,
            totals=totals,
        )

    def receipt_for(self, student_id: str, charge_id: str) -> tuple[FeeStatement, StatementLine]:
        statement = self.statement_for(student_id)
        for line in statement.lines:
            if line.charge_id == charge_id:
                if line.status != CHARGE_PAID or line.receipt_number is None:
                    raise FeeRuleError(409, "A receipt is issued once the fee is paid.")
                return statement, line
        raise FeeRuleError(404, "Fee not found.")

    # -- the Student fees screen ---------------------------------------------------

    def roster(
        self,
        *,
        academic_year: str,
        department_id: str | None,
        course_id: str | None,
        quota: str | None,
    ) -> tuple[list[RosterRow], RosterTotals]:
        department = func.coalesce(Cohort.department_id, Student.department_id)
        stmt = (
            select(Student.id, Student.usn, User.name, AcademicCourse.name, Student.admission_quota)
            .join(User, User.id == Student.user_id)
            .outerjoin(Cohort, Cohort.id == Student.cohort_id)
            .outerjoin(AcademicCourse, AcademicCourse.id == Cohort.course_id)
            .where(*_current_student_clauses())
            .order_by(Student.usn.asc().nulls_last(), User.name)
        )
        if department_id:
            stmt = stmt.where(department == department_id)
        if course_id:
            stmt = stmt.where(Cohort.course_id == course_id)
        if quota:
            stmt = stmt.where(Student.admission_quota == quota)

        rows: dict[str, RosterRow] = {}
        for sid, usn, name, course_name, student_quota in self.db.execute(stmt).all():
            rows[sid] = RosterRow(
                student_id=sid, usn=usn, name=name, course_name=course_name,
                quota=student_quota, academic_year=academic_year,
            )

        today = local_today()
        pending_paise = paid_paise = payments = 0
        if rows:
            charges = self.db.execute(
                select(FeeCharge, FeeDemand.due_on)
                .join(FeeDemand, FeeDemand.id == FeeCharge.demand_id)
                .where(
                    FeeCharge.student_id.in_(list(rows)),
                    FeeCharge.status != CHARGE_CANCELLED,
                    FeeDemand.academic_year == academic_year,
                )
            ).all()
            for charge, due_on in charges:
                row = rows[charge.student_id]
                row.amount_paise += charge.amount_paise
                row.fee_count += 1
                raised = charge.created_at.date() if charge.created_at else None
                if raised and (row.raised_on is None or raised > row.raised_on):
                    row.raised_on = raised
                if charge.status == CHARGE_PAID:
                    paid_paise += charge.amount_paise
                    payments += 1
                    continue
                row.unpaid_count += 1
                pending_paise += charge.amount_paise
                if due_on is not None and (row.next_due_on is None or due_on < row.next_due_on):
                    row.next_due_on = due_on
                if display_status_for(charge, due_on, today) == DISPLAY_OVERDUE:
                    row._overdue = True

        for row in rows.values():
            if row.fee_count == 0:
                row.status = ROSTER_NOT_RAISED
            elif row._overdue:
                row.status = ROSTER_OVERDUE
            elif row.unpaid_count:
                row.status = ROSTER_PENDING
            else:
                row.status = ROSTER_PAID

        result = list(rows.values())
        totals = RosterTotals(
            students=len(result),
            students_with_fee=sum(1 for r in result if r.fee_count),
            pending_paise=pending_paise,
            paid_paise=paid_paise,
            payments_recorded=payments,
            overdue_students=sum(1 for r in result if r.status == ROSTER_OVERDUE),
        )
        return result, totals
