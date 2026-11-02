"""Auth-critical models — the first slice of the fresh Python schema.

This is deliberately small: enough to authenticate and mint the same session
payload the Next.js app does (userId, email, name, role, studentId?, mentorId?).
The remaining ~35 models are ported in later phases (see
docs/python-fastapi-migration.md).
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Role(str, enum.Enum):
    STUDENT = "STUDENT"
    MENTOR = "MENTOR"
    DIRECTOR = "DIRECTOR"
    ADMIN = "ADMIN"
    # A graduate. No Student/Mentor row, no staff scope: they see their own
    # profile and the jobs sheet, nothing of the live cohort's records.
    ALUMNI = "ALUMNI"


class Stage(str, enum.Enum):
    """The REEP developmental stages, in order."""

    REBOOT = "REBOOT"
    EXCEL = "EXCEL"
    EXCEL_ADVANCED = "EXCEL_ADVANCED"
    ELEVATE = "ELEVATE"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # EVERY case-insensitive lookup of an account, and there are nine:
        # the Google SSO callback, registration provisioning, `grant_access`,
        # and the admin faculty/student create-and-edit paths all ask
        # `WHERE lower(email) = :x`.
        #
        # `ix_users_email` is a plain unique btree on `email`, and Postgres
        # CANNOT use it for that predicate — `lower(email)` is an expression,
        # not the indexed value — so each of those was a sequential scan of
        # `users`. With six rows that is invisible; with a seeded roster it is
        # every single sign-in.
        #
        # A functional index is the fix rather than lower-casing on write: the
        # address is stored as the person typed it, which is what gets printed
        # on a leave form and read back to them on the phone.
        Index("ix_users_email_lower", text("lower(email)")),
        # PARTIAL, and the predicate is part of the declaration: the only
        # question ever asked of this column is "is this account disabled", and
        # on a healthy deployment almost every row is NULL. Declared here as
        # well as in e5f2c86d40b1 — an index that exists only in a migration is
        # drift, and `alembic check` asking to drop three of them every run is
        # how a real drop goes unnoticed.
        Index(
            "ix_users_disabled_at",
            "disabled_at",
            postgresql_where=text("disabled_at IS NOT NULL"),
        ),
        # The same shape for removal (2026-09-16): every roster read asks
        # `deleted_at IS NULL` and almost every row answers yes, so the index
        # is over the handful that do not.
        Index(
            "ix_users_deleted_at",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )


    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), default=Role.STUDENT)
    # Format: "scrypt:<salt_hex>:<digest_hex>" — identical to the Next.js app,
    # so migrated hashes verify without a reset.
    password_hash: Mapped[str] = mapped_column(String)
    # THE GOOGLE PRINCIPAL THIS ROW IS PINNED TO. `email` is how a sign-in FINDS
    # a row; `sub` is what proves it is the same person as last time. An
    # institutional address is a lease, not an identity — the college re-issues
    # 1mp25mdm01@ to a new intake — and with sign-in keyed on the email string
    # alone the new holder inherited the previous student's marks, uploads and
    # mentor notes through a completely valid Google login, silently. Pinned on
    # the first Google sign-in (NULL until then, so every already-seeded roster
    # row keeps working) and compared on every one after; a mismatch is refused
    # in app/routers/auth.py rather than reconciled, because the only safe way
    # to hand a row to a new person is for a human to clear this column.
    #
    # UNIQUE so the same Google account cannot end up pinned to two roster rows.
    # Nullable + unique is the right pair in Postgres: NULLs are not compared,
    # so any number of un-pinned rows coexist.
    google_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)

    #: OFFBOARDING (B3.3). Set and the account cannot sign in by any door, and
    #: every live session is retired with it.
    #:
    #: A TIMESTAMP RATHER THAN A BOOLEAN, because "disabled" without "when" is
    #: unanswerable six months later, and the 90-day window in which enabling
    #: restores the login is measured from it. Nothing is deleted: the mentor
    #: notes they wrote, the leave they sanctioned and the evidence they verified
    #: are part of other people's records and were true when they were written.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    disable_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    #: REMOVED FROM THE ROSTER BUT STILL IN THE DATABASE (2026-09-16).
    #:
    #: The Main Admin's "delete, but not permanently": the account leaves every
    #: list the console draws, cannot sign in by any door, and every row it
    #: owns — marks, uploads, notes, interviews — stays exactly where it is,
    #: because the row is still here. `POST /api/admin/users/{id}/restore`
    #: brings it back with nothing lost. The permanent delete
    #: (`app/account_deletion.py`) is the OTHER answer and needs a code emailed
    #: to the Main Admin; this one needs a reason in words, like disabling.
    #:
    #: SEPARATE FROM `disabled_at`, and it must stay so. Disabling is an
    #: offboarding the office still wants to SEE — a disabled faculty member is
    #: listed greyed with the date, because "she vanished from the list" reads
    #: as a bug. Removal is the opposite promise: the person is gone from the
    #: screens. One column cannot carry both, and a removed account that was
    #: disabled beforehand comes back disabled when it is restored, which is
    #: only expressible with two columns.
    #:
    #: EVERY SIGN-IN DOOR READS `barred_at`, never this column or `disabled_at`
    #: alone. A door that forgot removal would admit a person the office
    #: believes is gone, on a screen that no longer lists them.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delete_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    @property
    def barred_at(self) -> datetime | None:
        """When this account stopped being admitted: disabled OR removed,
        whichever happened. `None` means it may sign in. The one question every
        door asks, answered in one place, so a third way of shutting an account
        is a one-line change here and not a hunt through nine files."""
        return self.deleted_at or self.disabled_at

    #: What REEP may email this account, as a small JSON object (B15).
    #:
    #: JSON rather than a column per switch: these are preferences, they will
    #: grow, and a migration per digest is a migration nobody will write -- so
    #: the alternative is a preference that exists on the screen and nowhere
    #: else. Empty object means "every default", which is what every account has
    #: today.
    notification_prefs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Bumped on logout; carried in the session JWT and compared on the way back
    # in. See app/security.py — this column is the whole of the revocation
    # story, including its honest limits.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Institutional identity, printed on the official leave form as synced
    # fields the applicant cannot type over. Nullable because the roster does not
    # carry them for every row yet; the form says "not on record" rather than
    # inventing a department.
    designation: Mapped[str | None] = mapped_column(String, nullable=True)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    # WHERE A STAFF MEMBER SITS IN THE INSTITUTION — the faculty counterpart of
    # `Student.cohort_id`, and for the same reason.
    #
    # A student's college, department, course and batch are reached THROUGH one
    # pointer and stored on nothing (see Student.cohort_id and
    # routers/student.py::_institution_for). Faculty had no such pointer at all:
    # the only institutional thing on a staff row was `department` above, a FREE
    # TEXT string typed on a form, so "Dept of Management Studies", "DMS" and
    # "Management" were three departments to anybody trying to group by one, and
    # no staff row could name its college at all. With more than one college in
    # `colleges` that is not a tidiness problem — it is the difference between
    # being able to say which institution a person belongs to and not.
    #
    # ON `users`, NOT ON `mentors`, and that placement is forced: a faculty
    # account deliberately has no `Mentor` row until the Main Admin assigns it a
    # student (see routers/admin_mentoring.py and AGENTS.md). Hanging the department off
    # `mentors` would mean a newly created faculty member — the exact row the
    # admin is trying to file — had nowhere to record where they work.
    #
    # Nullable, like `cohort_id`: staff exist before anyone files them, and every
    # account that predates this column keeps working. The free-text `department`
    # is kept beside it because the leave form prints that line and backfilling
    # is a separate act; this column is the source of truth wherever it is set.
    # `use_alter` + an explicit name because this column CLOSES A CYCLE in the
    # foreign-key graph: users -> departments -> colleges -> users
    # (`colleges.created_by_user_id`). `Base.metadata.create_all` — which the dev
    # seed uses — cannot topologically sort a cycle and drops the constraints it
    # cannot order, silently building a dev database without this FK. `use_alter`
    # makes it a separate ALTER after both tables exist, which is orderable. The
    # name is given because an unnamed constraint is called one thing by
    # create_all and another by Alembic, and the migration's own downgrade cannot
    # drop a constraint whose name it does not know.
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", name="fk_users_department", use_alter=True),
        nullable=True,
        index=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    student: Mapped["Student | None"] = relationship(back_populates="user", uselist=False)
    mentor: Mapped["Mentor | None"] = relationship(back_populates="user", uselist=False)
    login_days: Mapped[list["LoginDay"]] = relationship(back_populates="user")


#: `students.status` — see the column. Two values today; a plain String so a
#: third is a data change rather than a CREATE TYPE migration.
STUDENT_STATUS_ACTIVE = "ACTIVE"
STUDENT_STATUS_GRADUATED = "GRADUATED"


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint(
            "current_semester >= 1 AND weekly_hour_target >= 0",
            name="ck_student_semester_target",
        ),
        CheckConstraint(
            "admission_quota IS NULL OR admission_quota IN ('CET', 'MANAGEMENT')",
            name="ck_student_admission_quota",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    # Nullable / server-defaulted so the column adds cleanly onto existing rows.
    usn: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    # Both indexed (b41c9e2d7f05): cohort_id is what leaderboards rank a cohort
    # by, mentor_id is what rule 2's staff-scope gate filters by — the two
    # hottest scope columns in the app, seq-scanned until the 2026-08 audit.
    # A REAL foreign key since d5a1c8b30f47. It was a bare String carrying the
    # comment "FK to Cohort later" for its whole life, and "later" arrived when
    # the student's locked profile card needed College / Batch / Entry date /
    # Expected completion — all four of which are reached THROUGH this hop and
    # none of which are stored on the student. Nullable because a student
    # legitimately exists before an admin seats them (seed_roster.py creates
    # exactly that), and `ondelete` is deliberately omitted so the database
    # REFUSES to delete a cohort that still has students: the design archives a
    # batch, it never deletes one, and a cascade here would take student rows
    # with it. The index is kept — a foreign key does not create one on the
    # referencing side, so dropping it would silently undo b41c9e2d7f05.
    cohort_id: Mapped[str | None] = mapped_column(
        ForeignKey("cohorts.id"), nullable=True, index=True
    )
    # WHERE THIS STUDENT SITS WHEN NO BATCH SAYS SO (31f7a4c60b12). `cohort_id`
    # above was the only institutional pointer a student had, so a student with
    # no batch resolved to no department and no college — and Course /
    # Specialization / Batch are all OPTIONAL on the registration form while
    # College and Department are REQUIRED, so "named a department, seated in
    # nothing" is the NORMAL state of every college that has not built its
    # batches yet, not an edge case. Provisioning was dropping the one fact the
    # applicant was forced to give.
    #
    # It is a pointer and nothing is copied off it, exactly like `cohort_id` —
    # the department NAME is read through the join, never stored here. The two
    # pointers can both name a department, which is a chance to disagree, so
    # `student_placement.resolve_student_department` is their single writer: the
    # batch wins whenever it has a department, a contradicting value is a 422,
    # and only an unfiled batch lets this stand alone. No `ondelete`, matching
    # `users.department_id`: the database REFUSES to delete a department that
    # still has students filed under it, because the design archives, and a
    # cascade here would delete student rows to tidy a catalogue.
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True
    )
    # THE OTHER HALF OF A DUAL SPECIALIZATION (2026-09-23, migration
    # e2b7c4d9f1a6). A student who opted for two specializations -- Finance
    # and Marketing, say -- sits in ONE batch, and a batch hangs on one
    # specialization at most, so the first of the two is the batch's own and is
    # read through `cohort_id` exactly as before; this column is the SECOND and
    # nothing else. It is copied from `registrations.second_specialization_id`
    # when an application is approved and set by the Main Admin on the roster
    # editor, whose PATCH refuses a stream from another course or the batch's
    # own. SET NULL, the application column's discipline: a student survives
    # the office archiving a specialization, and only the pointer goes.
    second_specialization_id: Mapped[str | None] = mapped_column(
        ForeignKey("academic_specializations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mentor_id: Mapped[str | None] = mapped_column(ForeignKey("mentors.id"), nullable=True, index=True)
    # THE STUDENT'S ROLE PREFERENCES, IN PRIORITY ORDER (2026-10-22, migration
    # b9e3c71a4f20). A list of role names, first choice first, as the student
    # gave them on the college's role-preference form; the Main Admin loads that
    # form's spreadsheet at `POST /api/admin/role-preferences/import`. The
    # student's landing shows the first as their career goal and the rest as
    # backups, and there is deliberately no student-side write: the goal is the
    # preference the office collected. NULL means none was collected.
    role_preferences: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    current_stage: Mapped[Stage] = mapped_column(
        Enum(Stage, name="stage"), default=Stage.EXCEL, server_default="EXCEL"
    )
    current_semester: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # ------------------------------------------------------------------ #
    # IS THIS STUDENT STILL A STUDENT (B4.4). ACTIVE | GRADUATED.
    #
    # A PLAIN STRING for institution.py's stated reason, and NOT a deletion:
    # the row is the record of who was in the batch, what they scored and what
    # they were awarded, and a graduate's placement still counts in the
    # college's own numbers for the year they graduated.
    #
    # WHAT THIS COLUMN DOES **NOT** DO, and the next person to add a broad
    # `select(Student)` needs to know it: it does not filter anything by
    # itself. Graduating flips `users.role` to ALUMNI as well, and the two
    # facts answer different questions — the role decides what the ACCOUNT may
    # reach, this column decides who is counted as a current student on a
    # roster, a leaderboard and an analytics tile. A query that means "current
    # students" must say so; nothing derives it.
    # ------------------------------------------------------------------ #
    status: Mapped[str] = mapped_column(
        String, default=STUDENT_STATUS_ACTIVE, server_default=STUDENT_STATUS_ACTIVE
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    weekly_hour_target: Mapped[float] = mapped_column(Float, default=12, server_default="12")
    # HOW THE STUDENT TOOK THEIR SEAT: "CET" or "MANAGEMENT" (the fee screens,
    # 2026-10-29). NULL means the office has not recorded it, and a fee aimed
    # at one quota does not reach such a student: the preview counts them so
    # the office sees the gap before raising. No backfill, for that reason.
    admission_quota: Mapped[str | None] = mapped_column(String(20), nullable=True)

    user: Mapped[User] = relationship(back_populates="student")


class Mentor(Base):
    __tablename__ = "mentors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)

    user: Mapped[User] = relationship(back_populates="mentor")


class LoginDay(Base):
    """One row per user per calendar day they signed in — feeds the streak.

    The day is the local calendar date (matching the Next.js app), so an evening
    sign-in is not bucketed onto the next UTC day.
    """

    __tablename__ = "login_days"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_login_day"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    day: Mapped[date] = mapped_column(Date)

    user: Mapped[User] = relationship(back_populates="login_days")
