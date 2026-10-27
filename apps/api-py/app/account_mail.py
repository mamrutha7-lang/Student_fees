"""The email a student gets when the Main Admin disables their account
(2026-10-27).

Until this date a disabled student found out at the login screen: every door
answers `DISABLED_SIGN_IN_MESSAGE` and an open session ends with
`X-Reep-Session: disabled`, but nothing told the student before they next
tried, and the office had no way to tell them either. The owner asked for a
mail at the moment of the block, and for the students blocked before this
existed to be sent one too.

  `notify_student_disabled(db, user)`  runs after `POST /admin/users/{id}/
                                       disable` commits, for a STUDENT only.
  `send_disabled_notice(db, user)`     is the console's "Email the disabled
                                       notice" button, `POST /admin/users/{id}/
                                       disabled-notice`. Its first press for a
                                       block that was never mailed is how the
                                       students disabled before 2026-10-27 hear.

STUDENTS ONLY. A faculty account is disabled to offboard it (a resignation, a
secondment), and the sentence this mail carries is about a security issue,
which would be untrue for most of them. The owner asked for students.

WHAT TRAVELS: the account's own address, the student's name, and the same
words the login screen prints (`DISABLED_SIGN_IN_MESSAGE`), so the mail and the
screen cannot disagree. NEVER the reason the office typed: the disable dialog
says "the student is not shown it", and it can read "under investigation".
Never who disabled the account, for the leave mail's reason.

THE SWITCH IS DERIVED, `badge_mail_active`'s idiom (`settings.account_mail_active`):
"true" forces it on (the console outbox, and the test suite), "false" forces it
off, and blank is on exactly when SES is configured. So a development machine
writes no SENT row about a message nobody received, and production, which
carries `SES_FROM_ADDRESS`, sends with no second variable to remember.

ONE MESSAGE PER BLOCK, THROUGH `deliver_once`. The key is
`account-disabled:{user_id}:{disabled_at}`, so a retried disable mails once and
a student disabled again after being enabled is mailed again. The button reuses
that key on its first press, which makes it the backfill: a block from before
this module has no row under its key, so the press sends. A later press is the
office asking again ("they say it never came"), and gets a numbered key
(`…:resend:1`, `…:resend:2`), so it sends, and a double-tap on the same press
still sends once (both requests count the same rows and race for one key).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .mail_transport import send as transport_send
from .mailer import deliver_once
from .models.mail import MailLog, MailStatus
from .models.user import Role, User
from .routers.auth import DISABLED_SIGN_IN_MESSAGE

log = logging.getLogger(__name__)

#: `kind` on the MailLog rows, so the office can count these apart from the
#: activation, reset, leave and badge mail on the Email delivery screen.
DISABLED_MAIL_KIND = "account-disabled"

DISABLED_SUBJECT = "Your REEP account has been disabled"


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def disabled_dedupe_key(user: User) -> str:
    """`account-disabled:{user_id}:{when}` for the block the account is under
    now, to the microsecond the column stores, so disabling, enabling and
    disabling again within one second is still two blocks and two messages.
    Exported for the tests."""
    if user.disabled_at is None:
        raise ValueError("the account is not disabled")
    return f"{DISABLED_MAIL_KIND}:{user.id}:{_utc(user.disabled_at):%Y%m%dT%H%M%S.%fZ}"


def mailable(user: User) -> bool:
    """A STUDENT account under a block, with an address, and still on the
    roster. A removed account is not told about a security issue (the login
    screen tells it something else, `REMOVED_SIGN_IN_MESSAGE`)."""
    return (
        user.role is Role.STUDENT
        and user.disabled_at is not None
        and user.deleted_at is None
        and bool((user.email or "").strip())
    )


def _body(user: User) -> str:
    """`DISABLED_SIGN_IN_MESSAGE` verbatim, so the mail says exactly what the
    login screen says."""
    return (
        f"Hello {user.name},\n\n"
        f"{DISABLED_SIGN_IN_MESSAGE}\n\n"
        f"This is about your REEP account, {user.email}. You have been signed out "
        "on every device, and you cannot sign in until the account is enabled "
        "again. Nothing in your record has been removed.\n\n"
        "Replies to this email are not read. Please contact the placement office "
        "directly.\n"
    )


def _deliver(db: Session, user: User, dedupe_key: str) -> MailLog:
    text = _body(user)
    return deliver_once(
        db,
        kind=DISABLED_MAIL_KIND,
        recipient=user.email,
        dedupe_key=dedupe_key,
        subject=DISABLED_SUBJECT,
        send=lambda to, subject: transport_send(to, subject or "", text),
    )


def notify_student_disabled(db: Session, user: User) -> MailLog | None:
    """Tell a student their account was just disabled. Returns the
    `mail_logs` row, or None when nothing was attempted: the switch is off, or
    the account is not a student's. A failed send is a FAILED row, never an
    exception (`deliver_once`). Call it AFTER the disable has committed: a
    mail about a block that did not land is worse than no mail."""
    if not settings.account_mail_active or not mailable(user):
        return None
    return _deliver(db, user, disabled_dedupe_key(user))


def notices_for(db: Session, user: User) -> list[MailLog]:
    """Every row written for the block the account is under now, oldest
    first: the one sent at the block, if any, and every press of the button."""
    if user.disabled_at is None:
        return []
    base = disabled_dedupe_key(user)
    return list(
        db.scalars(
            select(MailLog)
            .where(or_(MailLog.dedupe_key == base, MailLog.dedupe_key.like(f"{base}:resend:%")))
            .order_by(MailLog.sent_at, MailLog.id)
        )
    )


def last_sent_at(db: Session, user: User) -> datetime | None:
    """When the student was last emailed about the block they are under now,
    or None if they never were (every student disabled before 2026-10-27)."""
    if user.disabled_at is None:
        return None
    base = disabled_dedupe_key(user)
    return db.scalar(
        select(func.max(MailLog.sent_at)).where(
            or_(MailLog.dedupe_key == base, MailLog.dedupe_key.like(f"{base}:resend:%")),
            MailLog.status == MailStatus.SENT,
        )
    )


def send_disabled_notice(db: Session, user: User) -> MailLog:
    """The button. The caller has already checked `mailable` and the switch.

    The first row for this block takes the block's own key, so a student
    disabled before this module existed is sent the same message a new block
    sends. Every later press takes the next numbered key and sends again."""
    base = disabled_dedupe_key(user)
    written = len(notices_for(db, user))
    key = base if written == 0 else f"{base}:resend:{written}"
    row = _deliver(db, user, key)
    log.info("Disabled notice %s for account %s (%s)", row.status.value, user.id, key)
    return row
