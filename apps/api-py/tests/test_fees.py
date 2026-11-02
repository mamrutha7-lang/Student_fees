"""Student fees: the Raise a fee dialog, the Student fees board, My fees, and
Razorpay's two doors (checkout verify and the webhook)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.clock import local_today
from app.config import settings
from app.db import SessionLocal
from app.fees import academic_year_for, academic_year_is_valid
from app.models.fee import FeeCharge, FeeDemand
from app.models.user import Role, Student

YEAR = academic_year_for(local_today())


@pytest.fixture
def office(make_user):
    return make_user("fee-office", Role.ADMIN)


@pytest.fixture
def student(make_user):
    s = make_user("fee-student")
    with SessionLocal() as db:
        row = db.scalar(select(Student).where(Student.user_id == s.user_id))
        row.usn = f"1BGTEST{s.user_id[:6].upper()}"
        db.commit()
        s.student_id, s.usn = row.id, row.usn
    yield s
    with SessionLocal() as db:
        demand_ids = list(db.scalars(
            select(FeeCharge.demand_id).where(FeeCharge.student_id == s.student_id)
        ))
        db.execute(delete(FeeCharge).where(FeeCharge.student_id == s.student_id))
        if demand_ids:
            db.execute(delete(FeeDemand).where(FeeDemand.id.in_(demand_ids)))
        db.commit()


def _raise(client, office, student, **over):
    body = {
        "student_id": student.student_id, "academic_year": YEAR, "quota": "CET",
        "fee_head": "TUITION", "amount_rupees": 60000,
        "due_on": (local_today() + timedelta(days=30)).isoformat(),
    }
    body.update(over)
    return client.post("/api/admin/fees/charges", json=body, headers=office.headers)


def test_academic_year_helpers():
    assert academic_year_is_valid("2026-27")
    assert not academic_year_is_valid("2026-28")
    assert not academic_year_is_valid("26-27")


def test_a_student_cannot_reach_the_office_screens(client, student):
    r = client.get(f"/api/admin/fees/roster?academic_year={YEAR}", headers=student.headers)
    assert r.status_code == 403


def test_lookup_and_raise_records_the_quota_and_shows_on_my_fees(client, office, student):
    r = client.get(f"/api/admin/fees/students/lookup?usn={student.usn.lower()}", headers=office.headers)
    assert r.status_code == 200, r.text
    assert r.json()["quota"] is None

    r = _raise(client, office, student, note="  First   instalment ")
    assert r.status_code == 201, r.text
    assert r.json()["quota_recorded"] is True
    assert r.json()["status"] == "PENDING"

    mine = client.get("/api/student/fees", headers=student.headers).json()
    assert [f["title"] for f in mine["fees"]] == ["Tuition"]
    assert mine["fees"][0]["description"] == "First instalment"
    assert mine["summary"]["balance_rupees"] == 60000


def test_a_second_fee_for_the_same_head_and_year_is_refused(client, office, student):
    assert _raise(client, office, student).status_code == 201
    r = _raise(client, office, student)
    assert r.status_code == 409


def test_a_quota_that_contradicts_the_record_is_refused(client, office, student):
    assert _raise(client, office, student).status_code == 201
    r = _raise(client, office, student, quota="MANAGEMENT", fee_head="EXAMINATION")
    assert r.status_code == 409
    assert "CET" in r.json()["detail"]


def test_a_malformed_year_on_the_amount_lookup_is_a_422(client, office):
    r = client.get(
        "/api/admin/fees/structures/amount?academic_year=abc&quota=CET&fee_head=TUITION",
        headers=office.headers,
    )
    assert r.status_code == 422


def test_overdue_on_the_board_and_office_payment_issues_a_receipt(client, office, student):
    charge_id = _raise(
        client, office, student, due_on=(local_today() - timedelta(days=1)).isoformat()
    ).json()["charge_id"]

    board = client.get(f"/api/admin/fees/roster?academic_year={YEAR}", headers=office.headers).json()
    row = next(r for r in board["rows"] if r["student_id"] == student.student_id)
    assert row["status"] == "OVERDUE"

    r = client.post(f"/api/admin/fees/charges/{charge_id}/payments",
                    json={"method": "CHEQUE"}, headers=office.headers)
    assert r.status_code == 422  # a cheque needs its number
    r = client.post(f"/api/admin/fees/charges/{charge_id}/payments",
                    json={"method": "CHEQUE", "reference": "000123"}, headers=office.headers)
    assert r.status_code == 200, r.text
    assert r.json()["receipt_number"].startswith("RCPT-")

    pdf = client.get(f"/api/student/fees/{charge_id}/receipt", headers=student.headers)
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"
    assert client.get("/api/student/fees/statement", headers=student.headers).content[:4] == b"%PDF"


def test_another_students_fee_answers_404(client, office, student, make_user):
    charge_id = _raise(client, office, student).json()["charge_id"]
    other = make_user("fee-other")
    assert client.get(f"/api/student/fees/{charge_id}/receipt", headers=other.headers).status_code == 404


def test_bulk_raise_skips_with_a_reason(client, office, student):
    r = client.post("/api/admin/fees/charges/bulk", headers=office.headers, json={
        "student_ids": [student.student_id], "academic_year": YEAR, "fee_head": "TUITION",
        "due_on": (local_today() + timedelta(days=30)).isoformat(),
    })
    assert r.status_code == 201, r.text
    assert r.json()["raised"] == []
    assert r.json()["skipped"][0]["reason"] == "Quota not recorded."


def test_checkout_verify_and_webhook_settle_once(client, office, student, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(settings, "razorpay_key_secret", "test_secret")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "hook_secret")
    monkeypatch.setattr("app.razorpay_client.create_order", lambda **_: "order_TEST123")

    charge_id = _raise(client, office, student).json()["charge_id"]
    order = client.post(f"/api/student/fees/{charge_id}/order", headers=student.headers)
    assert order.status_code == 200, order.text
    assert order.json()["order_id"] == "order_TEST123"

    bad = client.post(f"/api/student/fees/{charge_id}/verify", headers=student.headers, json={
        "razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_1",
        "razorpay_signature": "0" * 64,
    })
    assert bad.status_code == 400

    # The webhook with a wrong amount settles nothing.
    def hook(amount):
        body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_1", "order_id": "order_TEST123", "amount": amount, "currency": "INR",
        }}}}).encode()
        sig = hmac.new(b"hook_secret", body, hashlib.sha256).hexdigest()
        return client.post("/api/payments/razorpay/webhook", content=body,
                           headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})

    assert hook(1).status_code == 200
    with SessionLocal() as db:
        assert db.get(FeeCharge, charge_id).status == "PENDING"

    good_sig = hmac.new(b"test_secret", b"order_TEST123|pay_1", hashlib.sha256).hexdigest()
    ok = client.post(f"/api/student/fees/{charge_id}/verify", headers=student.headers, json={
        "razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_1",
        "razorpay_signature": good_sig,
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "PAID"

    # Razorpay's confirmation arriving second changes nothing.
    assert hook(6_000_000).status_code == 200
    with SessionLocal() as db:
        charge = db.get(FeeCharge, charge_id)
        assert charge.status == "PAID" and charge.payment_method == "RAZORPAY"


def test_a_webhook_with_a_bad_signature_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "hook_secret")
    r = client.post("/api/payments/razorpay/webhook", content=b"{}",
                    headers={"X-Razorpay-Signature": "nope"})
    assert r.status_code == 400
