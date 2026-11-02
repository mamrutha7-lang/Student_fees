"""The Main Admin's fees: raise a fee, see who has paid, record a student's quota.

    GET  /api/admin/fees/preview                 how many students a fee would reach
    POST /api/admin/fees/demands                 raise a fee against a group of students
    GET  /api/admin/fees/demands                 every fee raised, with what is collected
    GET  /api/admin/fees/charges                 one row per student, filterable
    PUT  /api/admin/fees/students/{id}/quota     record CET or Management for a student

The Raise a fee dialog (one student at a time):

    GET  /api/admin/fees/options                 the dialog's dropdowns
    GET  /api/admin/fees/students/search?q=      the Student ID dropdown
    GET  /api/admin/fees/students/lookup?usn=    fill Student, Course, Department, Batch, Quota
    GET  /api/admin/fees/structures/amount       fill Fee amount from the fee structure
    POST /api/admin/fees/charges                 the Raise fee button
    GET  /api/admin/fees/structures              the fee structure, listed
    PUT  /api/admin/fees/structures              set one standard amount
    POST /api/admin/fees/charges/{id}/payments   record a cash, cheque, draft or bank payment
    GET  /api/admin/fees/charges/{id}/receipt    a paid fee's receipt PDF

The Student fees screen:

    GET  /api/admin/fees/roster                  every current student, their fees, the four cards
    GET  /api/admin/fees/roster.csv              the Export button
    POST /api/admin/fees/charges/bulk            "Raise fee for N selected"
    POST /api/admin/fees/reminders               Send reminders / Send reminder

The rules behind the dialog live in `fees.FeeService`; this router checks that
the caller is the Main Admin, writes the audit row and commits.

Main Admin only (`require_admin`, never a capability): raising a fee writes a
money claim onto every student it names, and a capability is something the
office hands to somebody else. The students pay on their own screen
(`routers/student_fees.py`); nothing here talks to Razorpay.

THE QUOTA FILTER IS EXACT. A demand aimed at CET reaches students recorded as
CET and nobody else; a student whose quota is not recorded is reached only by a
demand that names no quota. The `quota_not_recorded` count on the preview is how
the office sees that gap before it raises a fee, instead of discovering it as a
student who was never asked.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..clock import local_today
from ..db import get_db
from ..exports import csv_cell
from ..fee_documents import PAYMENT_METHOD_LABELS, render_fee_receipt
from ..fee_mail import send_fee_reminders
from ..fees import (
    ROSTER_NOT_RAISED,
    FeeRuleError,
    FeeService,
    RosterRow,
    StudentFeeProfile,
    academic_year_for,
    academic_year_is_valid,
    display_status_for,
    eligible_student_ids,
    raise_fee,
)
from ..identity import get_current_session
from ..models.fee import (
    CHARGE_PAID,
    CHARGE_STATUSES,
    FEE_HEAD_LABELS,
    OFFICE_PAYMENT_METHODS,
    FeeCharge,
    FeeDemand,
)
from ..models.institution import AcademicCourse, Department
from ..models.user import Student, User
from .mentor import require_admin

router = APIRouter(prefix="/admin/fees", tags=["admin"])

PAISE_PER_RUPEE: Final[int] = 100
#: A fee above this is a typo, not a fee. Rs 10 lakh is far past any semester
#: fee here, and `fee_charges.amount_paise` is a 32-bit column.
MAX_AMOUNT_RUPEES: Final[int] = 1_000_000
MAX_CHARGE_ROWS: Final[int] = 500
MAX_SEARCH_ROWS: Final[int] = 50

Quota = Literal["CET", "MANAGEMENT"]
FeeHead = Literal["TUITION", "EXAMINATION", "LIBRARY_LAB", "OTHER"]
OfficePaymentMethod = Literal["CASH", "CHEQUE", "DEMAND_DRAFT", "BANK_TRANSFER"]
RosterStatus = Literal["NOT_RAISED", "PENDING", "OVERDUE", "PAID"]


def check_academic_year(value: str | None) -> str | None:
    """Shared by every body that carries an academic year."""
    if value is None:
        return None
    value = value.strip()
    if not academic_year_is_valid(value):
        raise ValueError("The academic year must look like 2026-27.")
    return value


def require_academic_year(value: str) -> str:
    """The same check for a query parameter, as a 422 rather than a ValueError."""
    if not academic_year_is_valid(value.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The academic year must look like 2026-27."
        )
    return value.strip()


def clean_optional_text(value: str | None) -> str | None:
    """Collapse spaces; a blank box is None, not an empty string."""
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def refusal_for(error: FeeRuleError) -> HTTPException:
    """A fee rule's refusal, as the HTTP answer the screen reads."""
    return HTTPException(status_code=error.status_code, detail=error.message)


# -- bodies and answers ----------------------------------------------------------

class RaiseFeeIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    amount_rupees: int = Field(ge=1, le=MAX_AMOUNT_RUPEES)
    due_on: date | None = None
    #: Omitted = every student. See the module docstring.
    quota: Quota | None = None
    semester: int | None = Field(default=None, ge=1, le=12)
    #: Optional on the group raise, so older clients keep working.
    academic_year: str | None = None
    fee_head: FeeHead | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("A fee needs a title.")
        return value

    @field_validator("description")
    @classmethod
    def _description_clean(cls, value: str | None) -> str | None:
        return clean_optional_text(value)

    @field_validator("academic_year")
    @classmethod
    def academic_year_shape(cls, value: str | None) -> str | None:
        return check_academic_year(value)


class RaiseStudentFeeIn(BaseModel):
    """The Raise a fee dialog's fields, one student at a time."""

    student_id: str = Field(min_length=1, max_length=64)
    academic_year: str
    quota: Quota
    fee_head: FeeHead
    amount_rupees: int = Field(ge=1, le=MAX_AMOUNT_RUPEES)
    due_on: date
    #: "Note to student (optional)". Shown beside the fee on My fees.
    note: str | None = Field(default=None, max_length=500)

    @field_validator("academic_year")
    @classmethod
    def academic_year_shape(cls, value: str) -> str:
        return check_academic_year(value)

    @field_validator("note")
    @classmethod
    def note_clean(cls, value: str | None) -> str | None:
        return clean_optional_text(value)


class StudentLookupOut(BaseModel):
    student_id: str
    usn: str | None
    name: str
    course_name: str | None
    department_name: str | None = None
    batch_label: str | None = None
    #: None = the office has not recorded it. The dialog then asks for one,
    #: and raising the fee records it.
    quota: str | None
    current_semester: int | None

    @classmethod
    def from_profile(cls, profile: StudentFeeProfile) -> "StudentLookupOut":
        return cls(
            student_id=profile.student_id,
            usn=profile.usn,
            name=profile.name,
            course_name=profile.course_name,
            department_name=profile.department_name,
            batch_label=profile.batch_label,
            quota=profile.quota,
            current_semester=profile.current_semester,
        )


class StudentSearchOut(BaseModel):
    #: "Showing 6 of 58 students".
    total: int
    rows: list[StudentLookupOut]


class OptionOut(BaseModel):
    value: str
    label: str


class CourseOptionOut(OptionOut):
    department_id: str


class FeeOptionsOut(BaseModel):
    current_academic_year: str
    academic_years: list[str]
    fee_heads: list[OptionOut]
    quotas: list[OptionOut]
    payment_methods: list[OptionOut]
    departments: list[OptionOut] = []
    courses: list[CourseOptionOut] = []


class FeeStructureIn(BaseModel):
    academic_year: str
    quota: Quota
    fee_head: FeeHead
    amount_rupees: int = Field(ge=1, le=MAX_AMOUNT_RUPEES)

    @field_validator("academic_year")
    @classmethod
    def academic_year_shape(cls, value: str) -> str:
        return check_academic_year(value)


class FeeStructureOut(BaseModel):
    id: str
    academic_year: str
    quota: str
    fee_head: str
    fee_head_label: str
    amount_rupees: int
    updated_at: datetime


class StandardAmountOut(BaseModel):
    academic_year: str
    quota: str
    fee_head: str
    #: None when the office has not set one: the dialog leaves the box empty.
    amount_rupees: int | None


class RaiseStudentFeeOut(BaseModel):
    charge_id: str
    demand_id: str
    student_id: str
    title: str
    academic_year: str
    quota: str
    fee_head: str
    amount_rupees: int
    due_on: date
    status: str
    #: True when this fee also recorded the student's quota for the first time.
    quota_recorded: bool


class OfficePaymentIn(BaseModel):
    method: OfficePaymentMethod
    #: The cheque number, draft number or bank reference. Optional for cash.
    reference: str | None = Field(default=None, max_length=120)

    @field_validator("reference")
    @classmethod
    def reference_clean(cls, value: str | None) -> str | None:
        return clean_optional_text(value)


class OfficePaymentOut(BaseModel):
    charge_id: str
    status: str
    paid_at: datetime | None
    payment_method: str | None
    payment_reference: str | None
    receipt_number: str | None


class FeeReachOut(BaseModel):
    reaches: int
    #: Current students whose quota the office has not recorded. They are not
    #: reached by a CET or Management fee, and the screen says how many.
    quota_not_recorded: int


class FeeDemandOut(BaseModel):
    id: str
    title: str
    description: str | None
    amount_rupees: int
    due_on: date | None
    quota: str | None
    semester: int | None
    academic_year: str | None = None
    fee_head: str | None = None
    created_at: datetime
    charges: int
    paid: int
    collected_rupees: int


class RaiseFeeOut(BaseModel):
    demand: FeeDemandOut
    charged: int


class FeeChargeOut(BaseModel):
    id: str
    demand_id: str
    demand_title: str
    student_id: str
    student_name: str
    usn: str | None
    quota: str | None
    amount_rupees: int
    status: str
    paid_at: datetime | None
    razorpay_payment_id: str | None
    fee_head: str | None = None
    academic_year: str | None = None
    due_on: date | None = None
    #: `status`, except a pending fee past its due date reads OVERDUE.
    display_status: str | None = None
    payment_method: str | None = None
    payment_reference: str | None = None
    receipt_number: str | None = None


class QuotaIn(BaseModel):
    #: None clears it back to "not recorded".
    quota: Quota | None = None


class QuotaOut(BaseModel):
    student_id: str
    quota: str | None


def _demand_out(demand: FeeDemand, charges: int, paid: int, collected_paise: int) -> FeeDemandOut:
    return FeeDemandOut(
        id=demand.id,
        title=demand.title,
        description=demand.description,
        amount_rupees=demand.amount_paise // PAISE_PER_RUPEE,
        due_on=demand.due_on,
        quota=demand.quota,
        semester=demand.semester,
        academic_year=demand.academic_year,
        fee_head=demand.fee_head,
        created_at=demand.created_at,
        charges=charges,
        paid=paid,
        collected_rupees=collected_paise // PAISE_PER_RUPEE,
    )


# -- the group raise -------------------------------------------------------------

@router.get("/preview", response_model=FeeReachOut)
def preview_fee_reach(
    quota: Quota | None = None,
    semester: int | None = Query(default=None, ge=1, le=12),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FeeReachOut:
    """What the Raise button would do, asked before pressing it."""
    require_admin(session)
    reached = eligible_student_ids(db, quota=quota, semester=semester)
    everyone = eligible_student_ids(db, quota=None, semester=semester)
    unrecorded = 0
    if everyone:
        unrecorded = db.scalar(
            select(func.count())
            .select_from(Student)
            .where(Student.id.in_(everyone), Student.admission_quota.is_(None))
        ) or 0
    return FeeReachOut(reaches=len(reached), quota_not_recorded=int(unrecorded))


@router.post("/demands", response_model=RaiseFeeOut, status_code=status.HTTP_201_CREATED)
def raise_fee_demand(
    body: RaiseFeeIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RaiseFeeOut:
    """The group fee-raise form's submit. Writes the demand and one charge per student."""
    require_admin(session)
    try:
        demand, charged = raise_fee(
            db,
            title=body.title,
            description=body.description,
            amount_paise=body.amount_rupees * PAISE_PER_RUPEE,
            due_on=body.due_on,
            quota=body.quota,
            semester=body.semester,
            created_by_user_id=session.get("userId"),
            academic_year=body.academic_year,
            fee_head=body.fee_head,
        )
    except FeeRuleError as error:
        db.rollback()
        raise refusal_for(error) from None
    if charged == 0:
        # Raising a fee that reaches nobody is almost always a filter the office
        # did not mean (a quota nobody has been recorded under yet). Refuse it
        # rather than leave an empty demand on the list.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No current student matches those filters, so nobody would be charged. "
            "Record each student's quota first, or widen the filters.",
        )
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_demand", entity_id=demand.id, action="CREATED",
        before=None,
        after={
            "title": demand.title, "amount_paise": demand.amount_paise,
            "quota": demand.quota, "semester": demand.semester,
            "academic_year": demand.academic_year, "fee_head": demand.fee_head,
            "charged": charged,
        },
        event_type="fee.raised",
        payload={"charged": charged, "quota": demand.quota},
    )
    db.commit()
    db.refresh(demand)
    return RaiseFeeOut(demand=_demand_out(demand, charged, 0, 0), charged=charged)


@router.get("/demands", response_model=list[FeeDemandOut])
def list_fee_demands(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[FeeDemandOut]:
    require_admin(session)
    paid_flag = case((FeeCharge.status == CHARGE_PAID, 1), else_=0)
    rows = db.execute(
        select(
            FeeDemand,
            func.count(FeeCharge.id),
            func.coalesce(func.sum(paid_flag), 0),
            func.coalesce(func.sum(paid_flag * FeeCharge.amount_paise), 0),
        )
        .outerjoin(FeeCharge, FeeCharge.demand_id == FeeDemand.id)
        .group_by(FeeDemand.id)
        .order_by(FeeDemand.created_at.desc())
    ).all()
    return [_demand_out(d, int(n), int(p), int(c)) for d, n, p, c in rows]


@router.get("/charges", response_model=list[FeeChargeOut])
def list_fee_charges(
    response: Response,
    demand_id: str | None = None,
    quota: Quota | None = None,
    charge_status: str | None = Query(default=None, alias="status"),
    student_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[FeeChargeOut]:
    """One row per student. `quota` filters on the STUDENT's recorded quota, so
    it answers "who among CET students has paid" for a fee aimed at everyone.

    At most `MAX_CHARGE_ROWS` rows. `X-Total-Count` carries how many matched,
    so a screen can say "500 of 812" rather than present a cut list as whole."""
    require_admin(session)
    if charge_status is not None and charge_status not in CHARGE_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Status must be one of {', '.join(CHARGE_STATUSES)}.",
        )
    today = local_today()
    stmt = (
        select(FeeCharge, FeeDemand, Student, User.name)
        .join(FeeDemand, FeeDemand.id == FeeCharge.demand_id)
        .join(Student, Student.id == FeeCharge.student_id)
        .join(User, User.id == Student.user_id)
        .where(User.deleted_at.is_(None))
    )
    if demand_id:
        stmt = stmt.where(FeeCharge.demand_id == demand_id)
    if student_id:
        stmt = stmt.where(FeeCharge.student_id == student_id)
    if quota:
        stmt = stmt.where(Student.admission_quota == quota)
    if charge_status:
        stmt = stmt.where(FeeCharge.status == charge_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    rows = db.execute(
        stmt.order_by(FeeCharge.created_at.desc(), User.name).limit(MAX_CHARGE_ROWS)
    ).all()
    return [
        FeeChargeOut(
            id=c.id,
            demand_id=c.demand_id,
            demand_title=d.title,
            student_id=s.id,
            student_name=name,
            usn=s.usn,
            quota=s.admission_quota,
            amount_rupees=c.amount_paise // PAISE_PER_RUPEE,
            status=c.status,
            paid_at=c.paid_at,
            razorpay_payment_id=c.razorpay_payment_id,
            fee_head=d.fee_head,
            academic_year=d.academic_year,
            due_on=d.due_on,
            display_status=display_status_for(c, d.due_on, today),
            payment_method=c.payment_method,
            payment_reference=c.payment_reference,
            receipt_number=c.receipt_number,
        )
        for c, d, s, name in rows
    ]


@router.put("/students/{student_id}/quota", response_model=QuotaOut)
def set_student_quota(
    student_id: str,
    body: QuotaIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> QuotaOut:
    """Record how a student took their seat. Changing it does not touch fees
    already raised: a charge is a promise made at raise time."""
    require_admin(session)
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found.")
    before = student.admission_quota
    if before != body.quota:
        student.admission_quota = body.quota
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="student", entity_id=student.id, action="UPDATED",
            before={"admission_quota": before}, after={"admission_quota": body.quota},
            event_type="student.quota_set",
            payload={"admission_quota": body.quota},
        )
        db.commit()
    return QuotaOut(student_id=student.id, quota=student.admission_quota)


# -- the Raise a fee dialog ------------------------------------------------------

@router.get("/options", response_model=FeeOptionsOut)
def get_fee_options(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FeeOptionsOut:
    """Every dropdown on the fee screens: last, this and next academic year,
    the fee heads, the quotas, the ways the office can record a payment, and
    the departments and courses the Student fees screen filters by."""
    require_admin(session)
    current_year = academic_year_for(local_today())
    start = int(current_year[:4])
    years = [f"{year}-{(year + 1) % 100:02d}" for year in (start - 1, start, start + 1)]
    departments = db.execute(select(Department.id, Department.name).order_by(Department.name)).all()
    courses = db.execute(
        select(AcademicCourse.id, AcademicCourse.name, AcademicCourse.department_id)
        .order_by(AcademicCourse.name)
    ).all()
    return FeeOptionsOut(
        current_academic_year=current_year,
        academic_years=years,
        departments=[OptionOut(value=row.id, label=row.name) for row in departments],
        courses=[
            CourseOptionOut(value=row.id, label=row.name, department_id=row.department_id)
            for row in courses
        ],
        fee_heads=[OptionOut(value=key, label=label) for key, label in FEE_HEAD_LABELS.items()],
        quotas=[
            OptionOut(value="MANAGEMENT", label="Management"),
            OptionOut(value="CET", label="CET"),
        ],
        payment_methods=[
            OptionOut(value=method, label=PAYMENT_METHOD_LABELS[method])
            for method in OFFICE_PAYMENT_METHODS
        ],
    )


@router.get("/students/search", response_model=StudentSearchOut)
def search_students_for_fee(
    q: str | None = Query(default=None, max_length=60),
    department_id: str | None = None,
    course_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_SEARCH_ROWS),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StudentSearchOut:
    """The Student ID dropdown: current students whose USN or name contains `q`."""
    require_admin(session)
    profiles, total = FeeService(db).search_students(
        text=q, department_id=department_id, course_id=course_id, limit=limit
    )
    return StudentSearchOut(total=total, rows=[StudentLookupOut.from_profile(p) for p in profiles])


@router.get("/students/lookup", response_model=StudentLookupOut)
def lookup_student_for_fee(
    usn: str = Query(min_length=1, max_length=40),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StudentLookupOut:
    """The "Student details" card. Fills Name, Course, Department, Batch and Quota."""
    require_admin(session)
    try:
        profile = FeeService(db).find_student_by_usn(usn)
    except FeeRuleError as error:
        raise refusal_for(error) from None
    return StudentLookupOut.from_profile(profile)


@router.get("/structures/amount", response_model=StandardAmountOut)
def get_standard_amount(
    academic_year: str,
    quota: Quota,
    fee_head: FeeHead,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StandardAmountOut:
    """What fills the Fee amount box when the office picks a quota and a fee
    head. The office can still type a different amount."""
    require_admin(session)
    academic_year = require_academic_year(academic_year)
    paise = FeeService(db).standard_amount_paise(academic_year, quota, fee_head)
    return StandardAmountOut(
        academic_year=academic_year,
        quota=quota,
        fee_head=fee_head,
        amount_rupees=None if paise is None else paise // PAISE_PER_RUPEE,
    )


@router.get("/structures", response_model=list[FeeStructureOut])
def list_fee_structures(
    academic_year: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[FeeStructureOut]:
    require_admin(session)
    if academic_year:
        academic_year = require_academic_year(academic_year)
    return [
        FeeStructureOut(
            id=row.id,
            academic_year=row.academic_year,
            quota=row.quota,
            fee_head=row.fee_head,
            fee_head_label=FEE_HEAD_LABELS.get(row.fee_head, row.fee_head),
            amount_rupees=row.amount_paise // PAISE_PER_RUPEE,
            updated_at=row.updated_at,
        )
        for row in FeeService(db).list_standard_amounts(academic_year)
    ]


@router.put("/structures", response_model=FeeStructureOut)
def save_fee_structure(
    body: FeeStructureIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FeeStructureOut:
    """Set the standard amount for one fee head, quota and academic year."""
    require_admin(session)
    try:
        structure, previous_paise = FeeService(db).save_standard_amount(
            body.academic_year, body.quota, body.fee_head, body.amount_rupees * PAISE_PER_RUPEE
        )
    except FeeRuleError as error:
        db.rollback()
        raise refusal_for(error) from None
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_structure", entity_id=structure.id,
        action="CREATED" if previous_paise is None else "UPDATED",
        before=None if previous_paise is None else {"amount_paise": previous_paise},
        after={
            "academic_year": structure.academic_year, "quota": structure.quota,
            "fee_head": structure.fee_head, "amount_paise": structure.amount_paise,
        },
        event_type="fee.structure_set",
        payload={"fee_head": structure.fee_head, "quota": structure.quota},
    )
    db.commit()
    db.refresh(structure)
    return FeeStructureOut(
        id=structure.id,
        academic_year=structure.academic_year,
        quota=structure.quota,
        fee_head=structure.fee_head,
        fee_head_label=FEE_HEAD_LABELS[structure.fee_head],
        amount_rupees=structure.amount_paise // PAISE_PER_RUPEE,
        updated_at=structure.updated_at,
    )


@router.post("/charges", response_model=RaiseStudentFeeOut, status_code=status.HTTP_201_CREATED)
def raise_student_fee(
    body: RaiseStudentFeeIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RaiseStudentFeeOut:
    """THE RAISE FEE BUTTON. Raises one fee for one student; it shows on their
    My fees as Pending straight away.

    Answers 404 for a student who is not current, 409 for a quota that
    contradicts the student's recorded one or a fee already raised for that
    head and year, and 422 for a malformed field.
    """
    require_admin(session)
    try:
        demand, charge, quota_recorded = FeeService(db).raise_fee_for_student(
            student_id=body.student_id,
            academic_year=body.academic_year,
            quota=body.quota,
            fee_head=body.fee_head,
            amount_paise=body.amount_rupees * PAISE_PER_RUPEE,
            due_on=body.due_on,
            note=body.note,
            raised_by_user_id=session.get("userId"),
        )
    except FeeRuleError as error:
        db.rollback()
        raise refusal_for(error) from None

    if quota_recorded:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="student", entity_id=charge.student_id, action="UPDATED",
            before={"admission_quota": None}, after={"admission_quota": body.quota},
            event_type="student.quota_set",
            payload={"admission_quota": body.quota, "via": "fee_raised"},
        )
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_demand", entity_id=demand.id, action="CREATED",
        before=None,
        after={
            "title": demand.title, "fee_head": demand.fee_head,
            "academic_year": demand.academic_year, "amount_paise": demand.amount_paise,
            "quota": demand.quota, "student_id": charge.student_id, "charged": 1,
        },
        event_type="fee.raised_for_student",
        payload={"charged": 1, "quota": demand.quota, "fee_head": demand.fee_head},
    )
    db.commit()
    return RaiseStudentFeeOut(
        charge_id=charge.id,
        demand_id=demand.id,
        student_id=charge.student_id,
        title=demand.title,
        academic_year=body.academic_year,
        quota=body.quota,
        fee_head=body.fee_head,
        amount_rupees=body.amount_rupees,
        due_on=body.due_on,
        status=charge.status,
        quota_recorded=quota_recorded,
    )


@router.post("/charges/{charge_id}/payments", response_model=OfficePaymentOut)
def record_office_payment(
    charge_id: str,
    body: OfficePaymentIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> OfficePaymentOut:
    """The accounts office records a payment taken at the counter or by bank.
    The fee turns Paid on the student's screen and gets a receipt. A cheque,
    draft or bank transfer needs its reference number; cash does not."""
    require_admin(session)
    try:
        charge = FeeService(db).record_office_payment(
            charge_id=charge_id,
            method=body.method,
            reference=body.reference,
            recorded_by_user_id=session.get("userId"),
        )
    except FeeRuleError as error:
        db.rollback()
        raise refusal_for(error) from None
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_charge", entity_id=charge.id, action="UPDATED",
        before={"status": "PENDING"},
        after={
            "status": charge.status, "payment_method": charge.payment_method,
            "payment_reference": charge.payment_reference,
            "receipt_number": charge.receipt_number,
        },
        event_type="fee.payment_recorded",
        payload={"payment_method": charge.payment_method},
    )
    db.commit()
    return OfficePaymentOut(
        charge_id=charge.id,
        status=charge.status,
        paid_at=charge.paid_at,
        payment_method=charge.payment_method,
        payment_reference=charge.payment_reference,
        receipt_number=charge.receipt_number,
    )


@router.get("/charges/{charge_id}/receipt")
def download_charge_receipt(
    charge_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """A paid fee's receipt, for the office to print or resend. The download
    leaves an audit row against the student: the receipt names them, their USN
    and their payment."""
    require_admin(session)
    student_id = db.scalar(select(FeeCharge.student_id).where(FeeCharge.id == charge_id))
    if student_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found.")
    try:
        statement, line = FeeService(db).receipt_for(student_id, charge_id)
    except FeeRuleError as error:
        raise refusal_for(error) from None
    pdf = render_fee_receipt(statement, line)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="student", entity_id=student_id, action="DOWNLOADED",
        before=None,
        after={"document": "fee_receipt", "charge_id": charge_id,
               "receipt_number": line.receipt_number},
        event_type="fee.receipt_downloaded",
        payload={"charge_id": charge_id},
    )
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{line.receipt_number}.pdf"'},
    )


# -- the Student fees screen -------------------------------------------------------

class RosterRowOut(BaseModel):
    student_id: str
    usn: str | None
    name: str
    course_name: str | None
    quota: str | None
    academic_year: str
    #: Every live fee of the year, added together. 0 when none is raised.
    amount_rupees: int
    fee_count: int
    unpaid_count: int
    raised_on: date | None
    next_due_on: date | None
    #: NOT_RAISED, PENDING, OVERDUE (any unpaid fee past its due date) or PAID.
    status: str

    @classmethod
    def from_row(cls, row: RosterRow) -> "RosterRowOut":
        return cls(
            student_id=row.student_id,
            usn=row.usn,
            name=row.name,
            course_name=row.course_name,
            quota=row.quota,
            academic_year=row.academic_year,
            amount_rupees=row.amount_paise // PAISE_PER_RUPEE,
            fee_count=row.fee_count,
            unpaid_count=row.unpaid_count,
            raised_on=row.raised_on,
            next_due_on=row.next_due_on,
            status=row.status,
        )


class RosterTotalsOut(BaseModel):
    students: int
    students_with_fee: int
    pending_rupees: int
    paid_rupees: int
    payments_recorded: int
    overdue_students: int


class RosterOut(BaseModel):
    academic_year: str
    totals: RosterTotalsOut
    rows: list[RosterRowOut]


class BulkRaiseIn(BaseModel):
    """"Raise fee for N selected". Each student is charged on their own
    recorded quota; leave the amount out to use the fee structure's."""

    student_ids: list[str] = Field(min_length=1, max_length=MAX_CHARGE_ROWS)
    academic_year: str
    fee_head: FeeHead
    amount_rupees: int | None = Field(default=None, ge=1, le=MAX_AMOUNT_RUPEES)
    due_on: date
    note: str | None = Field(default=None, max_length=500)

    @field_validator("academic_year")
    @classmethod
    def academic_year_shape(cls, value: str) -> str:
        return check_academic_year(value)

    @field_validator("note")
    @classmethod
    def note_clean(cls, value: str | None) -> str | None:
        return clean_optional_text(value)


class BulkRaisedOut(BaseModel):
    student_id: str
    name: str
    charge_id: str
    amount_rupees: int


class BulkSkippedOut(BaseModel):
    student_id: str
    name: str
    reason: str


class BulkRaiseOut(BaseModel):
    raised: list[BulkRaisedOut]
    skipped: list[BulkSkippedOut]


class RemindersIn(BaseModel):
    academic_year: str
    #: Omitted = every student with an unpaid fee in the year.
    student_ids: list[str] | None = Field(default=None, max_length=MAX_CHARGE_ROWS)

    @field_validator("academic_year")
    @classmethod
    def academic_year_shape(cls, value: str) -> str:
        return check_academic_year(value)


class RemindersOut(BaseModel):
    sent: int
    already_reminded_today: int
    failed: int
    nothing_due: int
    no_address: int
    #: "email" when a mail service is set up, "console" when the message was
    #: only written to this server's log (a development machine).
    delivered_by: str


def roster_for(
    db: Session,
    academic_year: str,
    department_id: str | None,
    course_id: str | None,
    quota: Quota | None,
):
    academic_year = require_academic_year(academic_year)
    return FeeService(db).roster(
        academic_year=academic_year,
        department_id=department_id or None,
        course_id=course_id or None,
        quota=quota or None,
    )


@router.get("/roster", response_model=RosterOut)
def get_fee_roster(
    academic_year: str,
    department_id: str | None = None,
    course_id: str | None = None,
    quota: Quota | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RosterOut:
    """The Student fees screen: every current student, their fees for the year
    added up, and the four cards. Searching and the Status filter are done on
    the screen, over these rows."""
    require_admin(session)
    rows, totals = roster_for(db, academic_year, department_id, course_id, quota)
    return RosterOut(
        academic_year=academic_year,
        totals=RosterTotalsOut(
            students=totals.students,
            students_with_fee=totals.students_with_fee,
            pending_rupees=totals.pending_paise // PAISE_PER_RUPEE,
            paid_rupees=totals.paid_paise // PAISE_PER_RUPEE,
            payments_recorded=totals.payments_recorded,
            overdue_students=totals.overdue_students,
        ),
        rows=[RosterRowOut.from_row(row) for row in rows],
    )


ROSTER_STATUS_LABELS = {
    ROSTER_NOT_RAISED: "Not raised",
    "PENDING": "Pending",
    "OVERDUE": "Overdue",
    "PAID": "Paid",
}


@router.get("/roster.csv")
def export_fee_roster(
    request: Request,
    academic_year: str,
    department_id: str | None = None,
    course_id: str | None = None,
    quota: Quota | None = None,
    roster_status: RosterStatus | None = Query(default=None, alias="status"),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The Export button: the roster as filtered, one line per student. Every
    cell goes through `csv_cell`, and the download leaves an audit row, the
    rule every export here follows."""
    require_admin(session)
    rows, _ = roster_for(db, academic_year, department_id, course_id, quota)
    if roster_status:
        rows = [row for row in rows if row.status == roster_status]
    header = [
        "Student ID", "Student", "Course name", "Academic year", "Quota type",
        "Fees raised", "Fee amount (Rs)", "Unpaid fees", "Raised on", "Next due date", "Status",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([csv_cell(cell) for cell in header])
    for row in rows:
        writer.writerow([csv_cell(cell) for cell in (
            row.usn or "", row.name, row.course_name or "", row.academic_year,
            {"CET": "CET", "MANAGEMENT": "Management"}.get(row.quota or "", "Not recorded"),
            row.fee_count, row.amount_paise // PAISE_PER_RUPEE, row.unpaid_count,
            row.raised_on.isoformat() if row.raised_on else "",
            row.next_due_on.isoformat() if row.next_due_on else "",
            ROSTER_STATUS_LABELS.get(row.status, row.status),
        )])
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_roster", entity_id=academic_year, action="EXPORTED",
        before=None,
        after={
            "academic_year": academic_year, "department_id": department_id,
            "course_id": course_id, "quota": quota, "status": roster_status, "rows": len(rows),
        },
        event_type="fee.roster_exported",
        payload={"rows": len(rows)},
    )
    db.commit()
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="student-fees-{academic_year}.csv"'},
    )


@router.post("/charges/bulk", response_model=BulkRaiseOut, status_code=status.HTTP_201_CREATED)
def raise_fee_for_selected(
    body: BulkRaiseIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> BulkRaiseOut:
    """"Raise fee for N selected". Students who cannot be charged are listed
    with the reason and the rest are raised, in one transaction."""
    require_admin(session)
    try:
        raised, skipped = FeeService(db).raise_fee_for_students(
            student_ids=body.student_ids,
            academic_year=body.academic_year,
            fee_head=body.fee_head,
            amount_paise=None if body.amount_rupees is None else body.amount_rupees * PAISE_PER_RUPEE,
            due_on=body.due_on,
            note=body.note,
            raised_by_user_id=session.get("userId"),
        )
    except FeeRuleError as error:
        db.rollback()
        raise refusal_for(error) from None
    for item in raised:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="fee_demand", entity_id=item.demand_id, action="CREATED",
            before=None,
            after={
                "fee_head": body.fee_head, "academic_year": body.academic_year,
                "amount_paise": item.amount_paise, "student_id": item.student_id, "charged": 1,
            },
            event_type="fee.raised_for_student",
            payload={"charged": 1, "fee_head": body.fee_head, "bulk": True},
        )
    db.commit()
    return BulkRaiseOut(
        raised=[
            BulkRaisedOut(
                student_id=item.student_id, name=item.name, charge_id=item.charge_id,
                amount_rupees=item.amount_paise // PAISE_PER_RUPEE,
            )
            for item in raised
        ],
        skipped=[
            BulkSkippedOut(student_id=item.student_id, name=item.name, reason=item.reason)
            for item in skipped
        ],
    )


@router.post("/reminders", response_model=RemindersOut)
def send_reminders(
    body: RemindersIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RemindersOut:
    """"Send reminders" (every student with an unpaid fee in the year) and
    "Send reminder" (only the ticked students). One mail per student per day."""
    require_admin(session)
    result = send_fee_reminders(db, academic_year=body.academic_year, student_ids=body.student_ids)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="fee_reminder", entity_id=body.academic_year, action="CREATED",
        before=None,
        after={
            "academic_year": body.academic_year, "sent": result.sent,
            "already_reminded_today": result.already_reminded_today,
            "failed": result.failed, "delivered_by": result.delivered_by,
        },
        event_type="fee.reminders_sent",
        payload={"sent": result.sent, "selected": body.student_ids is not None},
    )
    db.commit()
    return RemindersOut(
        sent=result.sent,
        already_reminded_today=result.already_reminded_today,
        failed=result.failed,
        nothing_due=result.nothing_due,
        no_address=result.no_address,
        delivered_by=result.delivered_by,
    )
