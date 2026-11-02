"""Development seed for the fee screens: `python -m app.seed_fees`.

Run AFTER `python -m app.seed`. Idempotent: running it twice changes nothing.

It writes:
  * the fee structure for the current academic year (both quotas, every fee head);
  * six demo MBA students beside the seeded student (the names on the Student
    fees board), with no password: they exist to fill the roster, not to sign in;
  * a recorded quota on every one of them, and on `student@bgscet.ac.in`;
  * three fees for `student@bgscet.ac.in`, so My fees shows each state:
    Tuition pending, Examination paid at the counter, Library and lab overdue.

REFUSES TO RUN WHEN ENV=prod, for `app.seed`'s reason: it creates accounts.
"""
from __future__ import annotations

import sys
from datetime import timedelta

from sqlalchemy import select

from .clock import local_today
from .config import settings
from .db import SessionLocal
from .fees import FeeRuleError, FeeService, academic_year_for
from .models.fee import FEE_HEADS, QUOTAS
from .models.user import Role, Student, User

SEEDED_STUDENT_EMAIL = "student@bgscet.ac.in"
SSO_ONLY_PASSWORD_HASH = "google-only"  # app.grant_access's sentinel: no password works

#: Rupees, per (quota, fee head). Placeholders the office replaces on the
#: Fee structure screen.
STANDARD_RUPEES: dict[str, dict[str, int]] = {
    "MANAGEMENT": {"TUITION": 150000, "EXAMINATION": 5000, "LIBRARY_LAB": 8000, "OTHER": 2000},
    "CET": {"TUITION": 60000, "EXAMINATION": 5000, "LIBRARY_LAB": 8000, "OTHER": 2000},
}

DEMO_STUDENTS: list[tuple[str, str, str]] = [
    ("1BG25MBA001", "Priya S", "MANAGEMENT"),
    ("1BG25MBA004", "Aditya N", "CET"),
    ("1BG25MBA009", "Rakshita K", "MANAGEMENT"),
    ("1BG25MBA012", "Vikram G", "MANAGEMENT"),
    ("1BG25MBA015", "Nandini R", "CET"),
    ("1BG25MBA018", "Sameer B", "CET"),
]


def main() -> None:
    if settings.is_prod:
        print("REFUSED: app.seed_fees creates demo accounts and ENV=prod.", file=sys.stderr)
        raise SystemExit(1)

    with SessionLocal() as db:
        service = FeeService(db)
        year = academic_year_for(local_today())

        for quota in QUOTAS:
            for head in FEE_HEADS:
                if service.standard_amount_paise(year, quota, head) is None:
                    service.save_standard_amount(year, quota, head, STANDARD_RUPEES[quota][head] * 100)

        seeded = db.execute(
            select(Student).join(User, User.id == Student.user_id).where(User.email == SEEDED_STUDENT_EMAIL)
        ).scalar_one_or_none()
        if seeded is None:
            print("No seeded student found. Run `python -m app.seed` first.", file=sys.stderr)
            raise SystemExit(1)
        if seeded.admission_quota is None:
            seeded.admission_quota = "MANAGEMENT"

        for usn, name, quota in DEMO_STUDENTS:
            if db.scalar(select(Student.id).where(Student.usn == usn)) is not None:
                continue
            user = User(
                email=f"{usn.lower()}@bgscet.ac.in",
                name=name,
                role=Role.STUDENT,
                password_hash=SSO_ONLY_PASSWORD_HASH,
            )
            db.add(user)
            db.flush()
            db.add(Student(
                user_id=user.id,
                usn=usn,
                cohort_id=seeded.cohort_id,
                department_id=seeded.department_id,
                admission_quota=quota,
            ))
        db.flush()

        today = local_today()
        plan = [
            ("TUITION", today + timedelta(days=30), None),
            ("EXAMINATION", today + timedelta(days=15), "CASH"),
            ("LIBRARY_LAB", today - timedelta(days=5), None),
        ]
        for head, due_on, paid_by in plan:
            try:
                _, charge, _ = service.raise_fee_for_student(
                    student_id=seeded.id,
                    academic_year=year,
                    quota=seeded.admission_quota,
                    fee_head=head,
                    amount_paise=STANDARD_RUPEES[seeded.admission_quota][head] * 100,
                    due_on=due_on,
                    note=None,
                    raised_by_user_id=None,
                )
            except FeeRuleError:
                continue  # already raised on an earlier run
            if paid_by:
                service.record_office_payment(
                    charge_id=charge.id, method=paid_by, reference=None, recorded_by_user_id=None
                )
        db.commit()
        print(f"Fee seed done for {year}: structure, {len(DEMO_STUDENTS)} demo students, 3 fees.")


if __name__ == "__main__":
    main()
