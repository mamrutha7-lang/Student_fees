"""A disabled student is emailed to say so (2026-10-27).

The owner asked for two things, and this module pins both:

1. WHEN THE MAIN ADMIN DISABLES A STUDENT, THE STUDENT IS EMAILED. The message
   carries the login screen's own sentence (`DISABLED_SIGN_IN_MESSAGE`) and
   never the reason the office typed. A faculty account is not mailed.
2. THE STUDENTS DISABLED BEFORE THE MAIL EXISTED CAN BE SENT IT.
   `POST /admin/users/{id}/disabled-notice` sends it for the block the account
   is under now. Its first press uses the block's own key, so it is exactly
   the message a new block sends; a later press is a numbered resend.

The switch is derived from the transport like the badge mail's, so this suite,
which has none, sends nothing unless a test forces it on.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import requires_db
from sqlalchemy import delete, select

from app import account_mail, mail_transport
from app.config import settings
from app.db import SessionLocal
from app.models.mail import MailLog, MailStatus
from app.models.user import Role, Student, User
from app.routers.auth import DISABLED_SIGN_IN_MESSAGE

USERS = "/api/admin/users"
REASON = "Password shared with another student"


@pytest.fixture
def office(make_user):
    """The Main Admin, a student, and a clean outbox. Every mail row the test
    writes for those accounts is removed afterwards."""
    admin = make_user("notice-admin", Role.ADMIN)
    student = make_user("notice-student", Role.STUDENT)
    mail_transport.outbox.clear()
    yield admin, student
    mail_transport.outbox.clear()
    with SessionLocal() as db:
        db.execute(
            delete(MailLog).where(
                MailLog.dedupe_key.like(f"{account_mail.DISABLED_MAIL_KIND}:{student.user_id}:%")
            )
        )
        db.commit()


def _disable(client, admin, user_id: str):
    return client.post(f"{USERS}/{user_id}/disable", headers=admin.headers, json={"reason": REASON})


def _notice(client, headers, user_id: str):
    return client.post(f"{USERS}/{user_id}/disabled-notice", headers=headers)


def _student_id(user_id: str) -> str:
    with SessionLocal() as db:
        return db.scalar(select(Student.id).where(Student.user_id == user_id))


def _rows(user_id: str) -> list[MailLog]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(MailLog)
                .where(MailLog.dedupe_key.like(f"{account_mail.DISABLED_MAIL_KIND}:{user_id}:%"))
                .order_by(MailLog.sent_at)
            )
        )


def test_the_switch_reads_only_the_exact_words(monkeypatch):
    for text, forced in (("true", True), ("TRUE ", True), ("false", False), ("flase", None), ("", None)):
        monkeypatch.setattr(settings, "account_mail_enabled", text)
        expected = forced if forced is not None else settings.mail_configured
        assert settings.account_mail_active is expected, text


@requires_db
def test_with_no_transport_nothing_is_sent_and_nothing_is_claimed(client, office):
    """No SES sender and nothing forced: no message, no SENT row about a
    message nobody received, and the screen says no email went."""
    assert settings.account_mail_enabled.strip() == "", "the suite must not force the switch"
    assert settings.account_mail_active is False
    admin, student = office

    out = _disable(client, admin, student.user_id)
    assert out.status_code == 200, out.text
    assert out.json()["notice_mail"] is None
    assert "No email was sent" in out.json()["detail"]
    assert list(mail_transport.outbox) == []
    assert _rows(student.user_id) == []

    # The button says so too, rather than pretending.
    refused = _notice(client, admin.headers, student.user_id)
    assert refused.status_code == 503, refused.text
    assert _rows(student.user_id) == []


@requires_db
def test_disabling_a_student_emails_them_without_the_reason(client, office, monkeypatch):
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    admin, student = office

    out = _disable(client, admin, student.user_id)
    assert out.status_code == 200, out.text
    assert out.json()["notice_mail"] == "SENT"
    assert f"We emailed {student.email}" in out.json()["detail"]

    assert len(mail_transport.outbox) == 1
    message = mail_transport.outbox[0]
    assert message.to == student.email
    assert message.subject == account_mail.DISABLED_SUBJECT
    assert DISABLED_SIGN_IN_MESSAGE in message.text, "the mail must say what the login screen says"
    assert REASON not in message.text, "the office's reason was emailed to the student"
    assert "Password shared" not in message.text

    rows = _rows(student.user_id)
    assert len(rows) == 1 and rows[0].status is MailStatus.SENT
    with SessionLocal() as db:
        assert rows[0].dedupe_key == account_mail.disabled_dedupe_key(db.get(User, student.user_id))

    # The record shows when they were told.
    login = client.get(f"/api/admin/students/{_student_id(student.user_id)}/360", headers=admin.headers)
    assert login.status_code == 200, login.text
    assert login.json()["login"]["disabled_notice_sent_at"] is not None


@requires_db
def test_a_faculty_account_is_not_emailed(client, office, make_user, monkeypatch):
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    admin, _student = office
    faculty = make_user("notice-faculty", Role.MENTOR)

    out = _disable(client, admin, faculty.user_id)
    assert out.status_code == 200, out.text
    assert out.json()["notice_mail"] is None
    assert "email" not in out.json()["detail"].lower()
    assert list(mail_transport.outbox) == []
    assert _notice(client, admin.headers, faculty.user_id).status_code == 422


@requires_db
def test_a_student_disabled_before_the_mail_existed_is_sent_it_by_the_button(
    client, office, monkeypatch
):
    """The two students the office had already blocked. With the switch off
    at the block (the mail did not exist yet), nothing was written; the first
    press sends the block's own message, and a later press sends again."""
    admin, student = office
    assert _disable(client, admin, student.user_id).status_code == 200
    assert _rows(student.user_id) == []
    with SessionLocal() as db:
        never = account_mail.last_sent_at(db, db.get(User, student.user_id))
    assert never is None

    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    first = _notice(client, admin.headers, student.user_id)
    assert first.status_code == 200, first.text
    assert first.json()["notice_mail"] == "SENT"
    assert [m.to for m in mail_transport.outbox] == [student.email]
    with SessionLocal() as db:
        base = account_mail.disabled_dedupe_key(db.get(User, student.user_id))
    assert [r.dedupe_key for r in _rows(student.user_id)] == [base]

    again = _notice(client, admin.headers, student.user_id)
    assert again.status_code == 200, again.text
    assert len(mail_transport.outbox) == 2
    assert sorted(r.dedupe_key for r in _rows(student.user_id)) == [base, f"{base}:resend:1"]


@requires_db
def test_only_the_main_admin_sends_it_and_only_for_a_disabled_student(
    client, office, make_user, monkeypatch
):
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    admin, student = office
    faculty = make_user("notice-other", Role.MENTOR)

    # Not disabled: nothing to tell them.
    assert _notice(client, admin.headers, student.user_id).status_code == 409
    assert _notice(client, admin.headers, "no-such-account").status_code == 404

    assert _disable(client, admin, student.user_id).status_code == 200
    mail_transport.outbox.clear()
    refused = _notice(client, faculty.headers, student.user_id)
    assert refused.status_code == 403, refused.text
    assert list(mail_transport.outbox) == []

    # A removed account hears the roster sentence at the door, not this.
    with SessionLocal() as db:
        db.get(User, student.user_id).deleted_at = datetime.now(timezone.utc)
        db.commit()
    assert _notice(client, admin.headers, student.user_id).status_code == 409
    assert list(mail_transport.outbox) == []


@requires_db
def test_each_block_is_its_own_message(client, office, monkeypatch):
    """Enabled and disabled again is a new block, so a new mail; the old
    block's row does not swallow it."""
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    admin, student = office
    assert _disable(client, admin, student.user_id).status_code == 200
    # Straight back on and off again, well inside one second.
    assert client.post(f"{USERS}/{student.user_id}/enable", headers=admin.headers).status_code == 200
    assert _disable(client, admin, student.user_id).status_code == 200
    assert len(mail_transport.outbox) == 2
    assert len({r.dedupe_key for r in _rows(student.user_id)}) == 2


@requires_db
def test_a_failed_send_never_undoes_the_block(client, office, monkeypatch):
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    admin, student = office

    def refuse(to, subject, text):
        raise RuntimeError("SES said no")

    monkeypatch.setattr(account_mail, "transport_send", refuse)
    out = _disable(client, admin, student.user_id)
    assert out.status_code == 200, out.text
    assert out.json()["disabled"] is True
    assert out.json()["notice_mail"] == "FAILED"
    assert "could not be sent" in out.json()["detail"]
    with SessionLocal() as db:
        assert db.get(User, student.user_id).disabled_at is not None
    assert [r.status for r in _rows(student.user_id)] == [MailStatus.FAILED]

    # Pressing the button tries again under a new key, and this time it goes.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "account_mail_enabled", "true")
    retry = _notice(client, admin.headers, student.user_id)
    assert retry.status_code == 200, retry.text
    assert retry.json()["notice_mail"] == "SENT"
    assert [m.to for m in mail_transport.outbox] == [student.email]
