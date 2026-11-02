"""Fee reminders: "Send reminders" and "Send reminder" on the Student fees screen.

One mail per student per day, whatever the button is pressed, through
`mailer.deliver_once` and the key `fee-reminder:{student}:{day}` (its unique
index is what makes the promise hold under two presses at once). The mail goes
to the student's OWN account address and lists their unpaid fees for the year:
title, amount and due date. Nothing else from the record travels.

A removed or disabled account is skipped and counted as `no_address`: a
reminder to an account that cannot sign in to pay is a message about a door
that is shut.

On a machine with no mail service (`SES_FROM_ADDRESS` blank) the transport
writes the message to this server's log and its outbox instead of sending it,
and the result says `delivered_by="console"`, so the screen never claims an
email went out when it did not.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import local_today
from .config import settings
from .fee_documents import rupees
from .mail_transport import send as transport_send
from .mailer import deliver_once
from .models.fee import CHARGE_PENDING, FeeCharge, FeeDemand
from .models.mail import MailLog, MailStatus
from .models.user import STUDENT_STATUS_ACTIVE, Student, User

REMINDER_KIND = "fee_reminder"


@dataclass(frozen=True)
class ReminderResult:
    sent: int
    already_reminded_today: int
    failed: int
    nothing_due: int
    no_address: int
    delivered_by: str


def reminder_dedupe_key(student_id: str, day) -> str:
    return f"fee-reminder:{student_id}:{day.isoformat()}"


def _compose(name: str, academic_year: str, lines: list[tuple[str, int, object]]) -> tuple[str, str]:
    subject = f"Fee reminder: {len(lines)} unpaid fee(s) for {academic_year}"
    body = [f"Hello {name},", "", f"These fees for {academic_year} are not paid yet:", ""]
    for title, amount_paise, due_on in lines:
        due = f", due {due_on:%d %b %Y}" if due_on else ""
        body.append(f"  - {title}: {rupees(amount_paise)}{due}")
    body += ["", "Open My fees in REEP to pay online or download your statement.", "", "REEP"]
    return subject, "\n".join(body)


def send_fee_reminders(
    db: Session, *, academic_year: str, student_ids: list[str] | None
) -> ReminderResult:
    today = local_today()
    stmt = (
        select(Student.id, User.name, User.email, User.deleted_at, User.disabled_at,
               FeeDemand.title, FeeCharge.amount_paise, FeeDemand.due_on)
        .join(User, User.id == Student.user_id)
        .outerjoin(FeeCharge, (FeeCharge.student_id == Student.id) & (FeeCharge.status == CHARGE_PENDING))
        .outerjoin(
            FeeDemand,
            (FeeDemand.id == FeeCharge.demand_id) & (FeeDemand.academic_year == academic_year),
        )
        .where(Student.status == STUDENT_STATUS_ACTIVE)
        .order_by(Student.id, FeeDemand.due_on.asc().nulls_last())
    )
    if student_ids is not None:
        stmt = stmt.where(Student.id.in_(list(dict.fromkeys(student_ids))))
    else:
        # "Send reminders" with nothing ticked: only students with something unpaid.
        stmt = stmt.where(FeeDemand.id.is_not(None))

    owed: dict[str, list[tuple[str, int, object]]] = defaultdict(list)
    people: dict[str, tuple[str, str, bool]] = {}
    for sid, name, email, deleted_at, disabled_at, title, amount, due_on in db.execute(stmt).all():
        people[sid] = (name, email, deleted_at is None and disabled_at is None)
        if title is not None:
            owed[sid].append((title, amount, due_on))

    sent = already = failed = nothing_due = no_address = 0
    for sid, (name, email, reachable) in people.items():
        lines = owed.get(sid)
        if not lines:
            nothing_due += 1
            continue
        if not reachable or not email:
            no_address += 1
            continue
        key = reminder_dedupe_key(sid, today)
        if db.scalar(select(MailLog.id).where(MailLog.dedupe_key == key)) is not None:
            already += 1
            continue
        subject, text = _compose(name, academic_year, lines)
        row = deliver_once(
            db,
            kind=REMINDER_KIND,
            recipient=email,
            dedupe_key=key,
            subject=subject,
            send=lambda to, subj, _text=text: transport_send(to, subj or "", _text),
        )
        if row.status == MailStatus.SENT:
            sent += 1
        else:
            failed += 1
    return ReminderResult(
        sent=sent,
        already_reminded_today=already,
        failed=failed,
        nothing_due=nothing_due,
        no_address=no_address,
        delivered_by="email" if settings.mail_configured else "console",
    )
