"""A student's fees, and paying them through Razorpay.

    GET  /api/student/fees                       what I owe and what I have paid
    GET  /api/student/fees/statement             Download statement (PDF)
    GET  /api/student/fees/{id}/receipt          a paid fee's receipt (PDF)
    POST /api/student/fees/{id}/order            open (or reuse) the Razorpay order
    POST /api/student/fees/{id}/verify           the checkout's signed reply
    POST /api/payments/razorpay/webhook          Razorpay telling us, signed

EVERY STUDENT ENDPOINT IS FIRST-PERSON. `_require_student` reads `studentId`
from the session and every charge is looked up by `(charge id, that student)`,
so a crafted charge id belonging to somebody else answers 404 exactly as a
missing one does. There is no staff surface here: the office reads the same rows
through `routers/admin_fees.py`.

A CHARGE BECOMES PAID IN EXACTLY TWO PLACES, BOTH ON A VERIFIED SIGNATURE:
`verify_fee_payment` (the browser's reply, signed with the key secret) and
`razorpay_webhook` (Razorpay's own call, signed with the webhook secret). The
browser's callback is not enough on its own -- a student who closes the tab
after paying never sends it -- and the webhook is not enough on its own, because
it needs a public URL and a secret that a laptop does not have. Both go through
`fees.mark_charge_paid`, which is idempotent, so whichever arrives second
changes nothing. The row is read FOR UPDATE in both so they cannot interleave.

A CLIENT'S WORD IS NEVER "PAID". The verify body carries Razorpay's ids and
signature and nothing that says paid; the amount is the charge's, copied at
raise time, never one the browser sends. The webhook additionally checks that
the amount and currency Razorpay reports are the charge's own before it settles
anything.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import razorpay_client
from ..architecture_events import record_change, request_context
from ..config import settings
from ..db import get_db
from ..fee_documents import render_fee_receipt, render_fee_statement
from ..fees import FeeRuleError, FeeService, mark_charge_paid
from ..identity import get_current_session
from ..models.fee import (
    CHARGE_PAID,
    CHARGE_PENDING,
    DISPLAY_OVERDUE,
    PAYMENT_RAZORPAY,
    FeeCharge,
    FeeDemand,
)
from ..models.redesign import AuditEvent
from ..models.user import Role

log = logging.getLogger(__name__)

router = APIRouter(prefix="/student/fees", tags=["student-screens"])
webhook_router = APIRouter(prefix="/payments/razorpay", tags=["payments"])

PAISE_PER_RUPEE = 100
#: The Razorpay events that mean money has arrived for an order.
SETTLING_EVENTS = frozenset({"payment.captured", "order.paid"})


def _require_student(session: dict) -> str:
    """The student's own id, or a 403. The same gate `student_programme.py`
    uses, deliberately repeated rather than imported (its docstring says why):
    the ROLE is checked as well as the claim, because a graduate keeps a
    `studentId` and must not reach a student's money screen. EDIT BOTH OR
    NEITHER."""
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The student screens are open to current students only.",
        )
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a student account.")
    return student_id


class MyFeeOut(BaseModel):
    id: str
    title: str
    #: The office's "Note to student".
    description: str | None
    amount_rupees: int
    due_on: date | None
    #: As stored: PENDING or PAID. The Pay now button reads this one.
    status: str
    paid_at: datetime | None
    # -- added for the My fees design; older clients ignore them --------------
    fee_head: str | None = None
    academic_year: str | None = None
    quota: str | None = None
    #: "Raised on [Date]".
    raised_on: date | None = None
    #: The status chip: PENDING, PAID or OVERDUE (pending past its due date).
    display_status: str | None = None
    payment_method: str | None = None
    #: Set on a paid fee. Its presence is what draws the Receipt button.
    receipt_number: str | None = None


class MyFeesSummaryOut(BaseModel):
    """The three cards: Total raised, Paid, Balance to pay."""

    total_raised_rupees: int
    paid_rupees: int
    balance_rupees: int
    fee_count: int
    paid_count: int
    pending_count: int
    overdue_count: int
    #: "Management quota" under Total raised. None when not recorded.
    quota: str | None


class MyFeesOut(BaseModel):
    #: False when this server has no Razorpay key. The list still shows what is
    #: owed; the screen says paying is not switched on instead of offering a
    #: button that can only fail.
    payments_enabled: bool
    fees: list[MyFeeOut]
    #: "Academic year 2026-27" in the page header.
    academic_year: str | None = None
    summary: MyFeesSummaryOut | None = None
    #: Titles of the overdue fees, for the red banner above the table.
    overdue_titles: list[str] = []


class OrderOut(BaseModel):
    #: The PUBLIC half of the key pair, which the checkout window needs.
    key_id: str
    order_id: str
    amount_paise: int
    currency: str
    title: str


class FeePaymentIn(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=100)
    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)


class FeePaymentOut(BaseModel):
    status: str
    paid_at: datetime | None
    receipt_number: str | None = None


def _own_charge_for_update(db: Session, student_id: str, charge_id: str) -> tuple[FeeCharge, str]:
    row = db.execute(
        select(FeeCharge, FeeDemand.title)
        .join(FeeDemand, FeeDemand.id == FeeCharge.demand_id)
        .where(FeeCharge.id == charge_id, FeeCharge.student_id == student_id)
        .with_for_update(of=FeeCharge)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fee not found.")
    return row[0], row[1]


@router.get("", response_model=MyFeesOut)
def list_my_fees(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> MyFeesOut:
    """The My fees screen: the cards, the overdue banner and the table, all
    from one `FeeService.statement_for`, the same object the statement PDF
    prints, so the screen and the PDF agree on every number."""
    student_id = _require_student(session)
    try:
        statement = FeeService(db).statement_for(student_id)
    except FeeRuleError as error:
        raise HTTPException(error.status_code, error.message) from None
    totals = statement.totals
    return MyFeesOut(
        payments_enabled=settings.razorpay_configured,
        fees=[
            MyFeeOut(
                id=line.charge_id,
                title=line.title,
                description=line.note,
                amount_rupees=line.amount_paise // PAISE_PER_RUPEE,
                due_on=line.due_on,
                status=line.status,
                paid_at=line.paid_at,
                fee_head=line.fee_head,
                academic_year=line.academic_year,
                quota=line.quota,
                raised_on=line.raised_on,
                display_status=line.display_status,
                payment_method=line.payment_method,
                receipt_number=line.receipt_number,
            )
            for line in statement.lines
        ],
        academic_year=statement.academic_year,
        summary=MyFeesSummaryOut(
            total_raised_rupees=totals.total_raised_paise // PAISE_PER_RUPEE,
            paid_rupees=totals.paid_paise // PAISE_PER_RUPEE,
            balance_rupees=totals.balance_paise // PAISE_PER_RUPEE,
            fee_count=totals.fee_count,
            paid_count=totals.paid_count,
            pending_count=totals.pending_count,
            overdue_count=totals.overdue_count,
            quota=statement.student.quota,
        ),
        overdue_titles=[
            line.title for line in statement.lines if line.display_status == DISPLAY_OVERDUE
        ],
    )


@router.get("/statement")
def download_fee_statement(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The Download statement button: every fee and the three totals, as a PDF
    rendered on this machine."""
    student_id = _require_student(session)
    try:
        statement = FeeService(db).statement_for(student_id)
    except FeeRuleError as error:
        raise HTTPException(error.status_code, error.message) from None
    filename = f"fee-statement-{statement.academic_year}.pdf"
    return Response(
        content=render_fee_statement(statement),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{charge_id}/receipt")
def download_fee_receipt(
    charge_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The Receipt button on a paid fee. Another student's fee answers 404, as
    if it did not exist; an unpaid fee answers 409."""
    student_id = _require_student(session)
    try:
        statement, line = FeeService(db).receipt_for(student_id, charge_id)
    except FeeRuleError as error:
        raise HTTPException(error.status_code, error.message) from None
    return Response(
        content=render_fee_receipt(statement, line),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{line.receipt_number}.pdf"'},
    )


@router.post("/{charge_id}/order", response_model=OrderOut)
def open_fee_order(
    charge_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> OrderOut:
    """The Pay now button. Reuses the charge's open order so a double tap, or a
    student who closed the window and came back, never opens a second one.

    The row stays locked while Razorpay is asked for an order, which is what
    makes the reuse safe under a double tap; `razorpay_client` bounds that call
    to ten seconds so a slow provider cannot hold the lock for long."""
    student_id = _require_student(session)
    if not settings.razorpay_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Online payment is not switched on for this college yet. Please pay at the office.",
        )
    charge, title = _own_charge_for_update(db, student_id, charge_id)
    if charge.status == CHARGE_PAID:
        raise HTTPException(status.HTTP_409_CONFLICT, "This fee is already paid.")
    if charge.status != CHARGE_PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This fee can no longer be paid online.")
    if charge.razorpay_order_id is None:
        try:
            charge.razorpay_order_id = razorpay_client.create_order(
                amount_paise=charge.amount_paise, receipt=charge.id
            )
        except razorpay_client.RazorpayError as exc:
            db.rollback()
            log.warning("Razorpay order failed for a fee charge: %s", exc)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "The payment provider could not be reached. Nothing was charged; please try again.",
            ) from None
        db.commit()
    else:
        db.rollback()  # release the lock; nothing changed
    return OrderOut(
        key_id=settings.razorpay_key_id.strip(),
        order_id=charge.razorpay_order_id,
        amount_paise=charge.amount_paise,
        currency=razorpay_client.CURRENCY,
        title=title,
    )


@router.post("/{charge_id}/verify", response_model=FeePaymentOut)
def verify_fee_payment(
    charge_id: str,
    body: FeePaymentIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> FeePaymentOut:
    """The checkout's reply. Paid only if Razorpay's signature checks out over
    THIS charge's own order id."""
    student_id = _require_student(session)
    charge, _ = _own_charge_for_update(db, student_id, charge_id)
    if charge.razorpay_order_id is None or body.razorpay_order_id != charge.razorpay_order_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That payment does not belong to this fee.")
    if not razorpay_client.checkout_signature_is_valid(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The payment could not be verified. If money left your account, it will be "
            "confirmed automatically; otherwise please try again.",
        )
    try:
        changed = mark_charge_paid(
            charge, method=PAYMENT_RAZORPAY, razorpay_payment_id=body.razorpay_payment_id
        )
    except FeeRuleError as error:
        db.rollback()
        raise HTTPException(error.status_code, error.message) from None
    if changed:
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="fee_charge", entity_id=charge.id, action="UPDATED",
            before={"status": CHARGE_PENDING},
            after={"status": charge.status, "payment_method": PAYMENT_RAZORPAY,
                   "receipt_number": charge.receipt_number},
            event_type="fee.paid_online",
            payload={"via": "checkout"},
        )
    db.commit()
    return FeePaymentOut(status=charge.status, paid_at=charge.paid_at,
                         receipt_number=charge.receipt_number)


# -- Razorpay's webhook ------------------------------------------------------------

def _settle_from_webhook(db: Session, event: dict[str, Any], request: Request) -> str:
    """Apply one verified webhook event. Returns a word for the log. Never
    raises for a payment that is not ours: Razorpay retries anything but a 2xx,
    and retrying an event about somebody else's order helps nobody."""
    if event.get("event") not in SETTLING_EVENTS:
        return "ignored"
    payment = (((event.get("payload") or {}).get("payment") or {}).get("entity")) or {}
    order_id = payment.get("order_id")
    payment_id = payment.get("id")
    if not isinstance(order_id, str) or not isinstance(payment_id, str):
        return "no_ids"
    charge = db.scalar(
        select(FeeCharge).where(FeeCharge.razorpay_order_id == order_id).with_for_update()
    )
    if charge is None:
        return "unknown_order"
    if payment.get("amount") != charge.amount_paise or payment.get("currency") != razorpay_client.CURRENCY:
        log.error("Razorpay webhook amount mismatch for fee charge %s; not settled", charge.id)
        return "amount_mismatch"
    try:
        changed = mark_charge_paid(charge, method=PAYMENT_RAZORPAY, razorpay_payment_id=payment_id)
    except FeeRuleError:
        db.rollback()
        log.error("Razorpay webhook paid a cancelled fee charge %s; refund by hand", charge.id)
        return "cancelled"
    if changed:
        request_id, correlation_id = request_context(request)
        db.add(AuditEvent(
            actor_user_id=None, actor_type="SYSTEM",
            request_id=request_id, correlation_id=correlation_id,
            entity_type="fee_charge", entity_id=charge.id, action="UPDATED",
            before_json={"status": CHARGE_PENDING},
            after_json={"status": charge.status, "payment_method": PAYMENT_RAZORPAY,
                        "receipt_number": charge.receipt_number},
            metadata_json={"route": str(request.url.path), "via": "webhook"},
        ))
    db.commit()
    return "settled" if changed else "already_paid"


@webhook_router.post("/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Razorpay's own call. No session: the signature over the raw body, made
    with the webhook secret, is the only thing that authenticates it.

    `async` only to read the raw body; the database work runs on the threadpool
    so it never blocks the event loop."""
    if not settings.razorpay_webhook_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook not configured.")
    body = await request.body()
    if not razorpay_client.webhook_signature_is_valid(
        body, request.headers.get("X-Razorpay-Signature")
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad signature.")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON.") from None
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not an event.")
    outcome = await run_in_threadpool(_settle_from_webhook, db, event, request)
    log.info("Razorpay webhook %s: %s", event.get("event"), outcome)
    return {"ok": True}
