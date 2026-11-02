"""Deleting every STUDENT while the faculty stay exactly where they are.

`app.purge_people` is proven by one question — does the delete order satisfy 188
real foreign keys. This module has to answer that AND a second one that
`purge_people` never faces: for every table a student and a staff member can
BOTH hold rows in, did the right half go?

That is why `_seed_one_of_each` builds a faculty member and a student with the
SAME SHAPES of row — a leave request each, a login day each, a conversation and
a message each, an assistant run each — and the test asserts on both halves of
every pair. A test that only checked "the student is gone" would pass just as
happily against a module that emptied the tables outright, which is precisely
the bug this module exists to not have.

The delete runs against the REAL schema and is rolled back, for
`purge_people`'s reason: a synthetic fixture graph proves the order against the
constraints somebody remembered to reproduce.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, func, insert, select

from conftest import requires_db

from app import purge_people, purge_students
from app.db import Base, SessionLocal
from app.models.user import Role

T = Base.metadata.tables
STUDENT_ROLES = (Role.STUDENT, "STUDENT")


def test_every_table_has_a_verdict():
    """No database needed, so this runs everywhere and is the guard that
    actually catches the next person adding a model."""
    purge_students.check_verdicts()  # raises PurgeRefused naming the offenders


def test_the_two_destructors_classify_the_same_tables():
    """The pairing is the point: a model classified in one and forgotten in the
    other is silent in every other way, and `check_verdicts` refuses on it."""
    assert set(purge_students.STUDENT_VERDICTS) == set(purge_people.VERDICTS)


def test_this_module_keeps_only_what_it_says_it_keeps():
    """The whole difference between the two destructors, written down as a set.

    Anything KEPT by the deployment-wide purge is kept here too — the
    institution does not become deletable by narrowing the scope. Everything
    else this module protects is listed explicitly, so ADDING a KEEP is an edit
    to this assertion and somebody has to justify it in the diff.
    """
    keeps_here = {
        n for n, v in purge_students.STUDENT_VERDICTS.items() if v == purge_students.KEEP
    }
    keeps_there = {n for n, v in purge_people.VERDICTS.items() if v == purge_people.KEEP}
    assert keeps_there - keeps_here == set(), "the institution must be kept by both"
    assert keeps_here - keeps_there == {
        # a faculty member's own shelf, which is the reason this module exists
        "staff_signatures",
        "staff_upskilling_certs",
        # queues with no person in them: no foreign key to users, and an opaque
        # aggregate id that cannot be attributed to a student without guessing
        "redesign_outbox_events",
        "redesign_domain_jobs",
    }


def test_the_student_file_stores_go_and_the_staff_ones_stay():
    """FILE_COLUMNS is shared with purge_people, which empties all five. Here
    two of them belong to people who are staying, and a file destroyed for a
    row that survives is unrecoverable."""
    assert purge_students.STUDENT_VERDICTS["uploads"] == purge_students.ALL
    assert purge_students.STUDENT_VERDICTS["registration_documents"][0] == "via"
    assert purge_students.STUDENT_VERDICTS["staff_signatures"] == purge_students.KEEP
    assert purge_students.STUDENT_VERDICTS["staff_upskilling_certs"] == purge_students.KEEP
    for name in purge_people.FILE_COLUMNS:
        assert name in purge_students.STUDENT_VERDICTS, name


def test_the_doomed_set_is_carried_as_ids_and_not_as_a_live_subquery():
    """The bug this pins is invisible on a fresh database.

    Written as a subquery over `users`, every scope would keep re-asking "who is
    a student" as the pass runs — and the accounts are deleted near the END of
    it, because `users` is a parent of almost everything and the order runs
    children first. Any table ordered AFTER the accounts would then match
    nothing and leave its rows behind, silently, and no test on a database
    where the order happens to be favourable would ever notice. So the ids are
    read once and compiled in as parameters, and this is the assertion that
    says so.
    """
    doomed = purge_students.Doomed(
        user_ids=("doomed-user-1", "doomed-user-2"),
        emails=("doomed@bgscet.ac.in",),
        registration_ids=(),
    )
    for table in ("login_days", "leave_requests", "conversations", "users"):
        sql = str(
            purge_students.predicate(table, doomed).compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "FROM users" not in sql, f"{table} re-selects the students at delete time"
        assert "doomed-user-1" in sql and "doomed-user-2" in sql, table


def test_apply_alone_is_refused():
    """The second flag is the whole confirmation. --apply on its own must not
    delete anything, and must say why it did not."""
    assert purge_students.main(["--apply"]) == 2


# --------------------------------------------------------------------------
# The database half.
# --------------------------------------------------------------------------


def _seed_one_of_each(db) -> dict[str, str]:
    """One faculty member and one student, with matching shapes of row.

    Everything here is written inside the caller's transaction and dies with
    the same rollback the delete does, so nothing escapes into the suite's
    ambient database.
    """
    tag = uuid.uuid4().hex[:8]
    today = dt.date.today()
    ids = {
        "faculty_user": uuid.uuid4().hex,
        "student_user": uuid.uuid4().hex,
        "mentor": uuid.uuid4().hex,
        "student": uuid.uuid4().hex,
        "faculty_leave": uuid.uuid4().hex,
        "student_leave": uuid.uuid4().hex,
        "faculty_conversation": uuid.uuid4().hex,
        "student_conversation": uuid.uuid4().hex,
        "faculty_message": uuid.uuid4().hex,
        "student_message": uuid.uuid4().hex,
        "faculty_run": uuid.uuid4().hex,
        "student_run": uuid.uuid4().hex,
        "signature": uuid.uuid4().hex,
        "upskilling": uuid.uuid4().hex,
        "note": uuid.uuid4().hex,
        "approved_registration": uuid.uuid4().hex,
        "pending_registration": uuid.uuid4().hex,
    }
    ids["student_email"] = f"purge-stu-{tag}@bgscet.ac.in"

    db.execute(
        insert(T["users"]),
        [
            {
                "id": ids["faculty_user"],
                "email": f"purge-fac-{tag}@bgscet.ac.in",
                "name": "Purge Test Faculty",
                "password_hash": "sso-only",
                "role": "MENTOR",
            },
            {
                "id": ids["student_user"],
                "email": ids["student_email"],
                "name": "Purge Test Student",
                "password_hash": "sso-only",
                "role": "STUDENT",
            },
        ],
    )
    db.execute(insert(T["mentors"]).values(id=ids["mentor"], user_id=ids["faculty_user"]))
    db.execute(insert(T["students"]).values(id=ids["student"], user_id=ids["student_user"]))

    # The faculty member's own shelf. Neither has a student counterpart: these
    # tables are KEPT outright, and that is what makes them worth asserting on.
    db.execute(
        insert(T["staff_signatures"]).values(
            id=ids["signature"],
            user_id=ids["faculty_user"],
            stored_name=f"sig-{tag}.png",
            mime_type="image/png",
            size_bytes=101,
        )
    )
    db.execute(
        insert(T["staff_upskilling_certs"]).values(
            id=ids["upskilling"],
            user_id=ids["faculty_user"],
            title="Purge Test Course",
            original_name=f"cert-{tag}.pdf",
            stored_name=f"cert-{tag}.pdf",
            mime_type="application/pdf",
            size_bytes=202,
        )
    )

    # Shared tables: one row each, same shape, opposite fates.
    db.execute(
        insert(T["leave_requests"]),
        [
            {
                "id": ids["faculty_leave"],
                "requester_user_id": ids["faculty_user"],
                "from_date": today,
                "to_date": today,
                "reason": "purge test - faculty",
            },
            {
                "id": ids["student_leave"],
                "requester_user_id": ids["student_user"],
                "from_date": today,
                "to_date": today,
                "reason": "purge test - student",
            },
        ],
    )
    db.execute(
        insert(T["login_days"]),
        [
            {"id": uuid.uuid4().hex, "user_id": ids["faculty_user"], "day": today},
            {"id": uuid.uuid4().hex, "user_id": ids["student_user"], "day": today},
        ],
    )
    db.execute(
        insert(T["conversations"]),
        [
            {
                "id": ids["faculty_conversation"],
                "owner_user_id": ids["faculty_user"],
                "role": "MENTOR",
            },
            {
                "id": ids["student_conversation"],
                "owner_user_id": ids["student_user"],
                "role": "STUDENT",
            },
        ],
    )
    # `messages` is reached only through its conversation, which is the one
    # scope shape that cannot be checked by looking at the row itself.
    db.execute(
        insert(T["messages"]),
        [
            {
                "id": ids["faculty_message"],
                "conversation_id": ids["faculty_conversation"],
                "sender": "user",
                "content": "purge test - faculty",
            },
            {
                "id": ids["student_message"],
                "conversation_id": ids["student_conversation"],
                "sender": "user",
                "content": "purge test - student",
            },
        ],
    )
    db.execute(
        insert(T["agent_runs"]),
        [
            {
                "id": ids["faculty_run"],
                "actor_id": ids["faculty_user"],
                "role": "MENTOR",
                "scope": "mentor",
                "question": "purge test",
                "status": "ANSWERED",
            },
            {
                "id": ids["student_run"],
                "actor_id": ids["student_user"],
                "role": "STUDENT",
                "scope": "student",
                "question": "purge test",
                "status": "ANSWERED",
            },
        ],
    )

    # What a staff member wrote ABOUT the student. It goes with the student:
    # the row is keyed on a student_id that will not exist in a moment.
    db.execute(
        insert(T["mentor_notes"]).values(
            id=ids["note"],
            mentor_id=ids["mentor"],
            student_id=ids["student"],
            note_text="purge test note",
        )
    )

    # The two registration cases. The approved one became this student; the
    # pending one is a stranger the office has not decided about yet.
    db.execute(
        insert(T["registrations"]),
        [
            {
                "id": ids["approved_registration"],
                "name": "Purge Test Student",
                "email": ids["student_email"],
                "status": "APPROVED",
                "approved_student_id": ids["student"],
            },
            {
                "id": ids["pending_registration"],
                "name": "Purge Test Stranger",
                "email": f"purge-stranger-{tag}@bgscet.ac.in",
                "status": "PENDING_REVIEW",
                # Spelled out because an executemany needs the same keys in
                # every group — and because NULL here IS the distinction the
                # test is making: this application became nobody.
                "approved_student_id": None,
            },
        ],
    )
    return ids


def _exists(db, table: str, row_id: str) -> bool:
    t = T[table]
    return db.scalar(select(func.count()).select_from(t).where(t.c.id == row_id)) == 1


@requires_db
def test_the_students_go_and_the_faculty_survive_every_foreign_key():
    """THE REAL DELETE, ROLLED BACK — both halves of every shared table."""
    users = T["users"]
    with SessionLocal() as db:
        ids = _seed_one_of_each(db)
        kept_before = {
            name: db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
            for name, verdict in purge_students.STUDENT_VERDICTS.items()
            if verdict == purge_students.KEEP
        }
        staff_before = db.scalar(
            select(func.count()).select_from(users).where(users.c.role.not_in(STUDENT_ROLES))
        )
        plan = purge_students.build_plan(db)
        assert plan.accounts >= 1, "the seeded student should be in the plan"

        try:
            purge_students._null_created_by(db, plan)
            purge_students._delete_rows(db, plan)

            # Not one student account, and not one student row, anywhere.
            assert (
                db.scalar(
                    select(func.count()).select_from(users).where(users.c.role.in_(STUDENT_ROLES))
                )
                == 0
            )
            assert db.scalar(select(func.count()).select_from(T["students"])) == 0
            assert not _exists(db, "users", ids["student_user"])

            # Every non-student account is still here, by count and by name.
            staff_after = db.scalar(select(func.count()).select_from(users))
            assert staff_after == staff_before, f"{staff_before} staff -> {staff_after}"
            assert _exists(db, "users", ids["faculty_user"])
            assert _exists(db, "mentors", ids["mentor"])

            # The faculty member's own shelf, untouched.
            assert _exists(db, "staff_signatures", ids["signature"])
            assert _exists(db, "staff_upskilling_certs", ids["upskilling"])

            # Every shared table: the faculty row stays, the student row goes.
            for table, kept, gone in (
                ("leave_requests", ids["faculty_leave"], ids["student_leave"]),
                ("conversations", ids["faculty_conversation"], ids["student_conversation"]),
                ("messages", ids["faculty_message"], ids["student_message"]),
                ("agent_runs", ids["faculty_run"], ids["student_run"]),
            ):
                assert _exists(db, table, kept), f"{table}: the faculty row was deleted"
                assert not _exists(db, table, gone), f"{table}: the student row survived"

            # What staff wrote about the student goes with the student.
            assert not _exists(db, "mentor_notes", ids["note"])

            # The application that became a student goes; the pending one that
            # belongs to nobody yet is not this module's to take.
            assert not _exists(db, "registrations", ids["approved_registration"])
            assert _exists(db, "registrations", ids["pending_registration"])

            # Every table classified ALL is empty, and every KEPT table still
            # holds exactly what it held.
            for name, verdict in purge_students.STUDENT_VERDICTS.items():
                if verdict == purge_students.ALL:
                    # `table_for` and not `T[name]`: a preserved rescue table is
                    # classified here but has no model to look up.
                    n = db.scalar(
                        select(func.count()).select_from(purge_people.table_for(name))
                    )
                    assert n == 0, f"{name} still holds {n} row(s)"
            for name, before in kept_before.items():
                after = db.scalar(
                    select(func.count()).select_from(purge_people.table_for(name))
                )
                assert after == before, f"{name} lost rows: {before} -> {after}"
        finally:
            db.rollback()


@requires_db
def test_the_plan_counts_what_the_delete_deletes():
    """A dry run an operator reads and then acts on is only useful if the two
    passes agree. Same session, same transaction: plan, delete, compare."""
    with SessionLocal() as db:
        _seed_one_of_each(db)
        plan = purge_students.build_plan(db)
        before = {
            # `table_for`: once a preserved rescue table holds rows it appears
            # in `plan.rows` like any other, and it has no model to look up.
            name: db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
            for name in plan.rows
        }
        try:
            purge_students._null_created_by(db, plan)
            purge_students._delete_rows(db, plan)
            for name, planned in plan.rows.items():
                after = db.scalar(
                    select(func.count()).select_from(purge_people.table_for(name))
                )
                assert before[name] - after == planned, name
        finally:
            db.rollback()


@requires_db
def test_it_refuses_when_the_doomed_set_reaches_a_non_student(monkeypatch):
    """The guard that stands between a mis-edited scope and a deployment's
    faculty. `find_doomed` is replaced with one that sweeps in a MENTOR, and
    `build_plan` must refuse by NAME before anything is destroyed."""
    with SessionLocal() as db:
        ids = _seed_one_of_each(db)
        real = purge_students.find_doomed

        # **kwargs because `find_doomed` grew `include_former_students` with
        # the graduation guard (c3a9f1e7d2b4). The stub has no opinion about it:
        # what is being mis-edited here is WHO is in the set, not which mode
        # asked for them, and the refusal must be the same in both.
        def _too_greedy(session, **kwargs):
            honest = real(session, **kwargs)
            return purge_students.Doomed(
                user_ids=honest.user_ids + (ids["faculty_user"],),
                emails=honest.emails,
                registration_ids=honest.registration_ids,
                former_student_user_ids=honest.former_student_user_ids,
            )

        monkeypatch.setattr(purge_students, "find_doomed", _too_greedy)
        try:
            try:
                purge_students.build_plan(db)
            except purge_people.PurgeRefused as exc:
                assert "not students" in str(exc)
                assert "purge-fac-" in str(exc)
            else:
                raise AssertionError("build_plan accepted a MENTOR into the doomed set")
        finally:
            db.rollback()


@requires_db
def test_a_deployment_with_no_students_plans_nothing():
    """The state a second run lands in. It must be a quiet, successful no-op
    and never a refusal — an operator who runs it twice has done nothing
    wrong."""
    with SessionLocal() as db:
        doomed = purge_students.find_doomed(db)
        try:
            if doomed.user_ids:
                # Remove them for the life of this transaction by planning and
                # deleting for real, then plan again on the empty deployment.
                plan = purge_students.build_plan(db)
                purge_students._null_created_by(db, plan)
                purge_students._delete_rows(db, plan)
            again = purge_students.build_plan(db)
            assert again.accounts == 0
            assert again.rows == {}
        finally:
            db.rollback()


@requires_db
def test_a_dry_run_changes_nothing():
    """`main()` with no flags opens its own session, so it cannot be staged
    inside a transaction. It must plan against whatever is really there and
    leave every count exactly as it found it."""
    users = T["users"]
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(users))
        students_before = db.scalar(select(func.count()).select_from(T["students"]))

    assert purge_students.main([]) == 0

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(users)) == before
        assert db.scalar(select(func.count()).select_from(T["students"])) == students_before


@requires_db
def test_the_receipt_actually_writes():
    """`_stamp` swallows its own failure so a purge that worked is not reported
    as failed by its receipt — which makes a broken receipt SILENT, and the
    receipt is the only record left that a cohort was removed at all. So it is
    written for real here, read back, and removed."""
    audit = T["redesign_audit_events"]
    with SessionLocal() as db:
        plan = purge_students.Plan(doomed=purge_students.find_doomed(db))
        plan.rows = {"students": 7}
        purge_students._stamp(db, plan)  # commits its own row

    written_id = None
    try:
        with SessionLocal() as db:
            row = db.execute(
                select(audit.c.id, audit.c.after_json)
                .where(audit.c.action == "PURGE_STUDENTS")
                .order_by(audit.c.occurred_at.desc())
            ).first()
            assert row is not None, "the purge left no record of itself"
            assert row.after_json["rows_deleted"] == 7
            written_id = row.id
    finally:
        if written_id is not None:
            with SessionLocal() as db:
                db.execute(delete(audit).where(audit.c.id == written_id))
                db.commit()
