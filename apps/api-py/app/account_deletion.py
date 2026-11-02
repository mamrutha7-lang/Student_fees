"""Delete ONE account for good — a student or a faculty member — from the
console, with a code the Main Admin reads out of their own mailbox.

Until 2026-09-16 the answer to "delete this student" was `python -m
app.purge_students`, which deletes every student, and the answer to "delete
this faculty account" was `python -m app.purge_people`, which deletes everyone
but the Main Admin. Both are the right tool for what they are named after and
neither can express one person. The owner asked for a Delete button on the
Students and Faculty screens with two answers behind it: REMOVE, which takes
the person off every screen and keeps every row (`users.deleted_at`, in
`routers/admin_deletion.py`), and DELETE, which is this module — the rows, the
files, the recordings, gone — guarded by a six-digit code mailed to the office
account and spent by exactly one act.

WHAT GOES is decided by the schema and by `ACCOUNT_POLICY`, through
`deletion_walk.Walk` (read its docstring first). The roots are the account's
own rows — `users`, `students`, `mentors`, the `registrations` that became or
named it, its `mail_logs` and its idempotency keys — and every `ON DELETE
CASCADE` child follows. The policy classifies the columns the schema left
undecided, and one edge it decided the other way:

  * `students.user_id`, `mentors.user_id`, `login_days.user_id` carry no
    clause because the purge modules wanted the database to REFUSE a stray
    account delete; here the row is the person's and goes with them.
  * `mentor_assignments.student_id` / `.mentor_id` and
    `student_semester_history.student_id`: history ABOUT the person, NOT NULL,
    and `purge_students` says why a row with nobody to name is not a record.
    On a FACULTY delete the assignment rows naming their group go too — the
    group row cannot survive its account — and the trail keeps the acts.
  * `redesign_mentor_notebook_*` author/owner columns are RESTRICT: the
    notebook is the faculty member's own instrument and goes with them.
  * `students.mentor_id` on a faculty delete: the mentees are RELEASED (the
    pointer cleared, counted as `mentees_released`), never deleted.
  * the four `created_by_user_id` columns on the institutional spine: cleared,
    `purge_people.CREATED_BY_COLUMNS`' reason.
  * `import_rows.student_id` is SET NULL in the schema and DELETE_ROW here: the
    line holds the student's USN, name and marks, and a delete that leaves it
    behind under a staff member's receipt has not deleted the student.
    `platform_candidates.user_id` likewise (a name and an address).

WHAT STAYS, WITHOUT THE NAME. Every `SET NULL` column is a record that outlives
the person — the audit trail, the SWOC lines they wrote (authorless is the
office's, `swoc.py`'s rule), the leave they signed, the badges they awarded —
and the plan counts them so the dialog can say so before the office presses
the button. `archived_documents` is untouched (`purge_people`'s reason: the
manifest is the only thing that can ever say whose file an archived object
was) and every stored name this delete destroys is stamped released in it.

WHAT IT REFUSES. The Main Admin account (the console's only key; a handover
is `grant_access`'s documented demotion), and — on a FACULTY delete — nothing
else: their mentees are released, their notes about students are DELETED
(`mentor_notes.mentor_id` is CASCADE and NOT NULL, so they cannot survive the
group row), and the plan says how many. Removing rather than deleting is the
answer for a faculty member whose notes the office wants to keep, and the
dialog says that too.

FILES GO BEFORE ROWS, `purge_people`'s rule applied to the subset: the stored
names are read out of the doomed rows, the bytes destroyed through the four
destroyers that module owns, and only then the transaction opens.

`tests/test_account_deletion.py` runs the real delete against the real
schema inside a transaction it rolls back, and pins the policy against every
foreign key in the metadata: a new column with no clause fails the build.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import column, delete, func, inspect as sa_inspect, or_, select, table as sa_table
from sqlalchemy.orm import Session

from .db import Base
from .deletion_walk import CLEAR_POINTER, DELETE_ROW, DeletionRefused, Walk, probe_policy
from .models.user import Mentor, Role, Student, User
from .purge_people import (
    FILE_COLUMNS,
    destroy_document_files,
    destroy_interview_audio,
    destroy_platform_call_audio,
    destroy_s3_recordings,
)

log = logging.getLogger("reep.account_deletion")

#: The tables the walk starts from. `registrations`, `mail_logs` and the
#: idempotency keys carry no foreign key to the account and are roots with
#: their own predicates rather than edges the walk could find.
ACCOUNT_ROOTS: tuple[str, ...] = (
    "users",
    "students",
    "mentors",
    "registrations",
    "mail_logs",
    "redesign_api_idempotency_keys",
)

#: Every foreign key the walk reaches whose clause the schema left undecided,
#: plus the two it decided the other way. Read the module docstring for each.
ACCOUNT_POLICY: dict[tuple[str, str], str] = {
    ("students", "user_id"): DELETE_ROW,
    ("mentors", "user_id"): DELETE_ROW,
    ("login_days", "user_id"): DELETE_ROW,
    ("mentor_assignments", "student_id"): DELETE_ROW,
    ("mentor_assignments", "mentor_id"): DELETE_ROW,
    ("student_semester_history", "student_id"): DELETE_ROW,
    ("redesign_mentor_notebook_entries", "author_user_id"): DELETE_ROW,
    ("redesign_mentor_notebook_entry_revisions", "author_user_id"): DELETE_ROW,
    ("redesign_mentor_notebook_actions", "owner_user_id"): DELETE_ROW,
    ("redesign_mentor_notebook_attachments", "uploaded_by_user_id"): DELETE_ROW,
    ("students", "mentor_id"): CLEAR_POINTER,
    ("colleges", "created_by_user_id"): CLEAR_POINTER,
    ("departments", "created_by_user_id"): CLEAR_POINTER,
    ("academic_courses", "created_by_user_id"): CLEAR_POINTER,
    ("academic_specializations", "created_by_user_id"): CLEAR_POINTER,
    # Overrides of a SET NULL the schema carries. See the module docstring.
    ("import_rows", "student_id"): DELETE_ROW,
    ("platform_candidates", "user_id"): DELETE_ROW,
}

#: Tables `purge_students` empties that a ONE-account delete leaves standing,
#: with the reason. `tests/test_account_deletion.py` checks every non-KEEP
#: verdict there is either reached by this walk or named here, so the next
#: student-owned table is decided by both destructors and not by whichever one
#: its author happened to read.
NOT_PER_ACCOUNT: dict[str, str] = {
    "fee_demands": (
        "a demand is the office's act of raising a fee, and a group demand names "
        "many students; this student's own claims go with them (fee_charges is "
        "ON DELETE CASCADE from students), and the demand keeps its title and "
        "amount, which name nobody"
    ),
    "import_runs": (
        "a spreadsheet run is the office's receipt for one upload, not one "
        "student's row; the lines naming this student go (import_rows), the "
        "receipt keeps its counters and stays"
    ),
    "analytics_snapshots": (
        "a nightly aggregate over the whole roster, rewritten by the next run; "
        "there is no row in it that is one person's"
    ),
    "students_orphaned_cohort_ids": (
        "a migration's rescue table with no model; handled by hand in "
        "`_delete_orphan_receipt` because the walk cannot see a model-less table"
    ),
}


class PermanentDeleteRefused(DeletionRefused):
    """Raised before anything is destroyed."""


@dataclass(frozen=True)
class Doomed:
    """Who is going, read once and held as literal ids — `purge_students.Doomed`'s
    reason: the account row is deleted last, and a predicate that re-read
    `users` after that would match nothing and leave rows behind."""

    user_id: str
    email: str
    name: str
    role: str
    student_id: str | None
    mentor_ids: tuple[str, ...]


@dataclass
class AccountPlan:
    """What a delete would do. The dialog shows this; the real run acts on
    exactly these numbers, so the two can be compared afterwards."""

    doomed: Doomed
    rows: dict[str, int] = field(default_factory=dict)
    cleared: dict[str, int] = field(default_factory=dict)
    files: int = 0
    audio_sessions: int = 0
    platform_calls: int = 0
    s3_objects: int = 0
    mentees_released: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    def as_dict(self) -> dict:
        return {
            "user_id": self.doomed.user_id,
            "email": self.doomed.email,
            "name": self.doomed.name,
            "role": self.doomed.role,
            "student_id": self.doomed.student_id,
            "rows": dict(sorted(self.rows.items())),
            "cleared": dict(sorted(self.cleared.items())),
            "total_rows": self.total_rows,
            "files": self.files,
            "audio_sessions": self.audio_sessions,
            "platform_calls": self.platform_calls,
            "s3_objects": self.s3_objects,
            "mentees_released": self.mentees_released,
        }


def _doomed_for(db: Session, user: User) -> Doomed:
    student_id = db.scalar(select(Student.id).where(Student.user_id == user.id))
    mentor_ids = tuple(db.scalars(select(Mentor.id).where(Mentor.user_id == user.id)).all())
    return Doomed(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        student_id=student_id,
        mentor_ids=mentor_ids,
    )


def _roots(doomed: Doomed) -> dict[str, object]:
    t = Base.metadata.tables
    regs = t["registrations"]
    return {
        "users": t["users"].c.id == doomed.user_id,
        "students": t["students"].c.user_id == doomed.user_id,
        "mentors": t["mentors"].c.user_id == doomed.user_id,
        # The application that became this student, or that carries their
        # address: `purge_students`' property four. Left behind, the duplicate
        # guard on POST /api/register refuses the address for ever.
        "registrations": or_(
            regs.c.approved_student_id == (doomed.student_id or "-"),
            func.lower(regs.c.email) == doomed.email.lower(),
        ),
        "mail_logs": func.lower(t["mail_logs"].c.recipient) == doomed.email.lower(),
        "redesign_api_idempotency_keys": (
            t["redesign_api_idempotency_keys"].c.principal_id == doomed.user_id
        ),
    }


def walk_for(db: Session, user: User) -> tuple[Doomed, Walk]:
    doomed = _doomed_for(db, user)
    return doomed, Walk(roots=_roots(doomed), policy=ACCOUNT_POLICY)


def refuse_unless_deletable(user: User, *, acting_user_id: str | None) -> None:
    """The Main Admin is the console's only key; a handover is a demotion
    first (`grant_access`'s documented path), never a delete."""
    if user.role is Role.ADMIN:
        raise PermanentDeleteRefused(
            "The Main Admin account cannot be deleted - it is the only way into "
            "the console. Hand the office over first (demote it to MENTOR with "
            "`python -m app.grant_access`), then delete it."
        )
    if acting_user_id is not None and user.id == acting_user_id:
        raise PermanentDeleteRefused("You cannot delete the account you are signed in with.")


def build_plan(db: Session, user: User, *, acting_user_id: str | None = None) -> AccountPlan:
    refuse_unless_deletable(user, acting_user_id=acting_user_id)
    doomed, walk = walk_for(db, user)
    plan = AccountPlan(doomed=doomed)
    plan.rows = walk.count_rows(db)
    plan.cleared = walk.count_cleared(db)
    plan.mentees_released = plan.cleared.get("students.mentor_id", 0)
    for name, col in FILE_COLUMNS.items():
        plan.files += len(walk.select_column(db, name, col))
    plan.audio_sessions = len(walk.select_rows(db, "interview_sessions", "id"))
    calls = walk.select_rows(db, "platform_call_sessions", "id", "recording_s3_key")
    plan.platform_calls = len(calls)
    plan.s3_objects = sum(1 for _, key in calls if key)
    orphan = _orphan_receipt_count(db, doomed)
    if orphan:
        plan.rows["students_orphaned_cohort_ids"] = orphan
    return plan


# ------------------------------------------------------------------ files --


def _destroy_files(db: Session, walk: Walk) -> tuple[list[str], list[tuple[str, str]]]:
    """Bytes first. Reads every stored name out of the doomed rows while those
    rows still exist, then hands them to `purge_people`'s destroyers."""
    documents: list[tuple[str, str]] = []
    for name, col in FILE_COLUMNS.items():
        documents += [(name, stored) for stored in walk.select_column(db, name, col)]
    audio = walk.select_rows(db, "interview_sessions", "id", "audio_path")
    call_rows = walk.select_rows(db, "platform_call_sessions", "id", "recording_s3_key")
    call_ids = [call_id for call_id, _ in call_rows]
    keys = [key for _, key in call_rows if key is not None]
    failures = (
        destroy_document_files(documents)
        + destroy_interview_audio(audio)
        + destroy_platform_call_audio(call_ids)
        + destroy_s3_recordings(keys)
    )
    return failures, documents


def _release_manifest(db: Session, documents: list[tuple[str, str]]) -> None:
    """The archive's index survives (KEEP in both purge modules); stamping the
    release is what keeps its rows honest. Best-effort, never fatal."""
    from .document_manifest import release

    for _label, stored in documents:
        try:
            release(db, stored, reason="account deleted")
        except Exception:  # noqa: BLE001 - reported, never fatal to a delete
            log.exception("Could not mark %s released in the manifest", stored)


# ------------------------------------------------- the model-less receipt --

ORPHAN_RECEIPT = "students_orphaned_cohort_ids"


def _orphan_receipt(db: Session):
    if ORPHAN_RECEIPT not in sa_inspect(db.get_bind()).get_table_names():
        return None
    return sa_table(ORPHAN_RECEIPT, column("student_id"))


def _orphan_receipt_count(db: Session, doomed: Doomed) -> int:
    receipt = _orphan_receipt(db)
    if receipt is None or doomed.student_id is None:
        return 0
    return int(
        db.scalar(
            select(func.count()).select_from(receipt).where(receipt.c.student_id == doomed.student_id)
        )
        or 0
    )


def _delete_orphan_receipt(db: Session, doomed: Doomed) -> None:
    """Migration `d5a1c8b30f47`'s rescue table names students by id and has no
    model, so the walk cannot reach it. Every row is a `students.id`."""
    receipt = _orphan_receipt(db)
    if receipt is None or doomed.student_id is None:
        return
    db.execute(delete(receipt).where(receipt.c.student_id == doomed.student_id))


# ---------------------------------------------------------------- execute --


def delete_rows(db: Session, doomed: Doomed, walk: Walk) -> dict[str, int]:
    """Pointers cleared, then every doomed row children-first, accounts last.
    NO COMMIT, so a test can run it against the real schema and roll back."""
    _delete_orphan_receipt(db, doomed)
    walk.clear_pointers(db)
    return walk.delete_rows(db)


def execute(db: Session, plan: AccountPlan) -> list[str]:
    """Destroy. Files first (a filesystem has no transaction to join), then
    one transaction for the rows, checked before it commits: the account row
    must be gone and no OTHER account may have gone with it."""
    user = db.get(User, plan.doomed.user_id)
    if user is None:
        raise PermanentDeleteRefused("That account is already gone.")
    refuse_unless_deletable(user, acting_user_id=None)
    doomed, walk = walk_for(db, user)
    users = Base.metadata.tables["users"]
    others_before = db.scalar(select(func.count()).select_from(users).where(users.c.id != doomed.user_id))

    failures, documents = _destroy_files(db, walk)
    delete_rows(db, doomed, walk)

    still_there = db.scalar(select(func.count()).select_from(users).where(users.c.id == doomed.user_id))
    others_after = db.scalar(select(func.count()).select_from(users))
    if still_there or others_after != others_before:
        db.rollback()
        raise PermanentDeleteRefused(
            f"Refusing to commit: {others_before} other account(s) existed and "
            f"{others_after} remain, and the deleted account "
            f"{'is still present' if still_there else 'is gone'}. The row deletes "
            "were rolled back. Stored files for this account were already "
            "destroyed - that step cannot join the transaction."
        )
    db.commit()
    _release_manifest(db, documents)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return failures


def policy_problems() -> list[str]:
    """What the account walk would refuse on, for the guard test and the boot
    self-check. Empty is the only acceptable answer."""
    return probe_policy(list(ACCOUNT_ROOTS), ACCOUNT_POLICY)
