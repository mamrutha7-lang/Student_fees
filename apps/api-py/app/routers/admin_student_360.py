"""B4.5 — one student, every panel the office opens their record for.

    GET /api/admin/students/{student_id}/360

WHAT IT REPLACES. `features/admin/student-detail` says so in its own docstring:
there is no `GET /api/admin/students/{id}`, so the screen fetches the WHOLE
roster and picks one row out of it, discarding the list in the same statement —
and then makes five more round trips for the panels beside it. This is that one
read. The identity block is deliberately the roster's own `AdminStudentOut`,
built by `admin_students._one`, so the card at the top of the detail screen and
the row in the grid behind it cannot disagree about a student's batch, faculty
or stage.

IT IS A READ AND IT WRITES NOTHING — no audit row of its own, for
`routers/audit.py`'s stated reason: opening a record is the most frequent action
the console performs, and a trail that logs every read buries the writes it
exists to show. Nothing here mutates a row, and nothing here calls a model, so
rule 1's egress gate is not on this path.

ITS KEY IS `admin.student_records` (2026-09-17), NOT THE ROSTER'S. The roster
key, `admin.students`, is the WRITE -- it edits and moves rows -- and this read
used to hang on it, so the office could not hand "look at this candidate's
complete details" to a faculty member without also handing them the editor.
The split is the reason the View button exists on the roster and the "Full
record" link on the Mentee Log: the Main Admin holds both keys by baseline and
sees no difference; a faculty member holds whichever the office granted.

WHAT "COMPLETE" MEANS HERE, and the two panels that were missing from it. The
read carried identity, login, every semester, readiness, open items, the mentor
and the trail, and it named the profile fields the office still needed WITHOUT
showing the ones already filled in: the phone number, the contact email, the
LinkedIn profile, the city, the career summary. And the student's DOCUMENTS
had no by-id read anywhere -- the Uploads tab on the screen was disabled with
that reason on it. Both are on the payload now (`profile`, `documents`), and
the file behind a document is streamed by `GET /mentor/uploads/{id}/file`,
which is rule 2's gate applied to a file: the Main Admin reaches every one, a
granted MENTOR their own mentees'.

BOTH FENCES, SEPARATELY (AGENTS.md, "Phase 3 added a THIRD fence beside that
one"). This endpoint names a student in the path, which is the exact shape rule
2 governs, so:

  * `require_capability(db, session, CAPABILITY, target=ancestry_of_student(...))`
    asks may-you-at-all AND may-you-HERE — B1.2's question, the same pair
    `admin_students.update_student` asks before an edit; and
  * `_assert_can_access_student` asks rule 2's question, which is stricter on a
    MENTOR's own students and is never relaxed by a capability.

Neither replaces the other. A change that reaches one and not the other opens
the other — that is not hypothetical here, it is what the DIRECTOR removal did.

EVERY MISSING NUMBER IS NULL, NEVER 0. The rule has bitten REEP on three
screens (English baseline sections, capability growth, `_attendance_pct`): a
pending section rendered as a confident `0` tells a mentor a student failed
something nobody has looked at yet. So `results` is `null` for a semester with
no marks imported, `best_interview_score` is `null` when no interview was ever
scored, and — see `_semester_windows` — the per-semester ACTIVITY counts are
`null` rather than `0` whenever the dates that would attribute them to a
semester are not on record.

THE MENTOR-HISTORY PANEL IS FILLED NOW, and it kept its `available` flag. B9.1
landed `mentor_assignments`, so the panel reads the real spells — who, from
when, to when, who moved them and why. `available` stays on the shape rather
than being dropped because it still carries a fact the entries cannot: an EMPTY
list is "nothing was recorded", never "this student has never had a mentor", and
the two are different on a deployment whose oldest pairings predate the table.
The note says which of those it is in words, and the CURRENT assignment is still
reported separately as a present-tense fact, because a panel that shows only
today's faculty member with no dates reads as "this student has always had this
mentor", which is a claim this data cannot make.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .. import account_mail
from ..db import get_db
from ..governance import ancestry_of_student, require_capability
from ..identity import get_current_session
from ..models.academics import SemesterResult
from ..models.account_events import LoginEvent
from ..models.badge import (
    BadgeEvidence,
    EvidenceStatus,
    StudentBadge,
    StudentBadgeStatus,
)
from ..models.interview import InterviewEvaluation, InterviewSession
from ..models.redesign import AuditEvent
from ..models.semester_history import StudentSemesterHistory
from ..models.student_profile import StudentProfile
from ..models.time_ledger import LedgerDayStatus, TimeLedgerDay
from ..models.upload import Upload, UploadKind, UploadStatus
from ..models.user import Mentor, Student, User
from ..semester_bounds import course_for_cohort
# B9.1's history, composed ONCE and read here through this module's own two
# fences. See `compose_mentor_history` for why it is not a second query.
from .admin_mentoring import MentorAssignmentOut, compose_mentor_history
from .admin_students import AdminStudentOut, _one, _student_or_404
from .mentor import _assert_can_access_student
from .registration import APPLICATION_CV_TITLE
from .student import compose_placement_readiness, PlacementReadinessOut

router = APIRouter(prefix="/admin", tags=["admin-students"])

#: The read side of the roster, split off `admin.students` on 2026-09-17 -- see
#: the module docstring and the catalogue entry in models/governance.py. A
#: plain module constant so `tools/ci/check_capability_enforcement.py` resolves
#: the name at the two call sites below.
CAPABILITY = "admin.student_records"

#: How many sign-ins and audit rows the panel carries. Both are "the recent
#: ones" on a detail screen, not a log viewer: the whole trail for a student is
#: `GET /api/admin/audit?entity=` and the whole sign-in list is the account
#: holder's own My-account screen. A cap here is what keeps one student's
#: response a response rather than a page of history.
RECENT_SIGN_INS: Final[int] = 5
RECENT_AUDIT_EVENTS: Final[int] = 20

#: The profile fields the placement office needs filled before a student is put
#: in front of a recruiter, with the label the console shows. `usn` is on the
#: STUDENT row and the rest are on the profile; the split is invisible here on
#: purpose, because it is invisible to the person reading the screen.
PROFILE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("usn", "USN"),
    ("phone", "Phone number"),
    ("email", "Contact email"),
    ("linkedin_url", "LinkedIn URL"),
    ("city", "City"),
    ("career_summary", "Career summary"),
    ("skills", "Skills"),
    ("education", "Education"),
)


# ------------------------------------------------------------- schemas --


class SignIn360Out(BaseModel):
    """One successful sign-in. `login_events` records successes only — a failed
    attempt is the brute-force limiter's business and putting it on a screen
    turns a mistyped password into an alarm."""

    at: datetime
    door: str
    ip: str | None
    user_agent: str | None


class Login360Out(BaseModel):
    """How this account gets in, and whether it still can.

    `google_linked` and `recent_sign_ins` are REAL here, which is the opposite
    of `GET /api/auth/me`'s contract: there they are `None`/empty everywhere
    except the one screen that asked, because `None` means "not asked" and a
    client reading absent as `false` would tell somebody their Google sign-in is
    unlinked on the screen immediately after they used it. This endpoint IS the
    asking, so the values are answers.
    """

    google_linked: bool
    #: True when the account holds a real `scrypt:` hash. `app.grant_access` and
    #: `app.seed_roster` mint the SSO-only sentinel instead, so False means
    #: "Google or the onboarding walk, no password issued" — which is the fact
    #: the office needs when a student says they cannot sign in.
    password_set: bool
    #: AGENTS.md's one-device rule: every sign-in advances this and retires the
    #: previous device. It is on the panel because "they keep getting signed
    #: out" is answered by it plus the sign-in list beside it.
    token_version: int
    disabled: bool
    disabled_at: datetime | None
    disable_reason: str | None
    disabled_by_name: str | None
    #: When the student was last emailed that this block is in place
    #: (app/account_mail.py); None if they never were, which is every student
    #: disabled before 2026-10-27. The Main Admin's, like the reason.
    disabled_notice_sent_at: datetime | None = None
    #: REMOVED from the roster (2026-09-16): the record is reachable by id
    #: while the account is off every list. Null on a listed student.
    deleted_at: datetime | None = None
    delete_reason: str | None = None
    last_login_at: datetime | None
    recent_sign_ins: list[SignIn360Out]


class SemesterResults360Out(BaseModel):
    """Marks for one semester. Every score is nullable because
    `semester_results` stores them nullable — a semester whose results are
    published but whose SGPA has not been entered is a real state."""

    sgpa: float | None
    cgpa: float | None
    live_backlogs: int
    closed_backlogs: int
    subjects_recorded: int
    result_class: str | None
    published_on: datetime | None


class Semester360Out(BaseModel):
    semester: int
    is_current: bool
    #: The dates this semester covers, derived from `student_semester_history`.
    #: Null when the moves that would bound it were never recorded.
    started_on: date | None
    ended_on: date | None
    #: False when this semester has no date bounds, in which case every count
    #: below is null. See `_semester_windows` — guessing a window would file a
    #: student's whole ledger under whichever semester they happen to be in now.
    activity_known: bool
    #: Null when no results row exists for this semester: nothing was imported,
    #: which is not the same fact as a row of zeros.
    results: SemesterResults360Out | None
    ledger_days_reconciled: int | None
    interviews: int | None
    #: The best `overall_score` of the interviews in the window. Null both when
    #: there were no interviews and when none of them produced a score — a
    #: failed or abandoned interview has a null score, and 0 would read as a
    #: verdict that was never given.
    best_interview_score: int | None
    badges_earned: int | None


class SemesterMove360Out(BaseModel):
    """One row of `student_semester_history` — the record of an academic act.

    NOT `admin_promotion.SemesterHistoryOut`, which is the same table read for a
    BATCH: every row there carries `student_name` and `usn` because the list
    mixes students, and on a one-student panel those would repeat the name the
    identity block above already gives, on every line. Same table, two subjects,
    two shapes; the fields below are read straight off the model, so the only
    thing that can make them drift is the model changing under both.
    """

    id: str
    kind: str
    from_semester: int
    to_semester: int
    effective_on: date
    reason: str | None
    by_user_id: str | None
    #: Null when the account that made the move has since been removed. The FK
    #: is SET NULL deliberately: the fact that a batch was promoted outlives the
    #: account of whoever promoted it.
    by_name: str | None
    created_at: datetime


class OpenItems360Out(BaseModel):
    """What this student is waiting on somebody for, or somebody is waiting on
    them for. Counts, not lists: each one already has a screen that owns it."""

    #: Uploads sitting at PENDING_REVIEW — waiting on STAFF.
    pending_uploads: int
    #: Badge evidence at PENDING_VERIFICATION — waiting on STAFF.
    pending_badge_claims: int
    #: Evidence sent back for more information — waiting on the STUDENT. Kept
    #: apart from the line above because the two need opposite conversations,
    #: which is the same reason `UploadStatus` keeps NEEDS_CHANGES and REJECTED
    #: apart.
    badge_claims_needing_info: int
    #: Ledger days with something entered that were never submitted. A day with
    #: no cells at all is not an open item, it is a day the student did not use
    #: the screen.
    unsubmitted_ledger_days: int
    missing_profile_fields: list[str]
    #: The number the screen puts on the tab. Deliberately computed here rather
    #: than by the client, so two clients cannot total it differently.
    total: int


class MentorAssignment360Out(BaseModel):
    """Who mentors this student NOW — a present-tense fact, read from
    `students.mentor_id`. Not a history and never rendered as one."""

    mentor_id: str | None
    mentor_user_id: str | None
    mentor_name: str | None


class MentorHistory360Out(BaseModel):
    """Every mentor this student has had, newest first (B9.1).

    `available` is NOT "does this deployment have the feature" — it always does
    now. It is "is there anything recorded for THIS student", which is the
    question the panel has to answer before it draws a timeline: a student
    seated before `mentor_assignments` existed carries one seeded row with a
    NULL `from_at`, a student who has never been assigned carries none at all,
    and an empty timeline must not be able to say the second when it means the
    first. `note` says which in words.
    """

    available: bool
    note: str
    #: `admin_mentoring.MentorAssignmentOut`, not a second shape — the history
    #: card on Mentors & students and this panel draw the same row, and one of
    #: them growing a field the other lacks is the "one name, two shapes" the
    #: codebase guard exists to stop.
    entries: list[MentorAssignmentOut]


class AuditRow360Out(BaseModel):
    id: str
    occurred_at: datetime
    action: str
    entity_type: str
    entity_id: str
    actor_user_id: str | None
    actor_name: str | None
    route: str | None


class Profile360Out(BaseModel):
    """The contact and profile details the student filled in, as they stand.

    `on_record` is False when the student has never saved a profile at all --
    every field is then null, and the screen says "no profile yet" rather than
    printing eight dashes that read as eight blanks the student left. `skills`
    is whatever the student typed on their profile, verbatim; the VERIFIED
    skills are a different list (`GET /mentor/students/{id}/badges`).
    """

    on_record: bool
    phone: str | None
    contact_email: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None
    city: str | None
    career_summary: str | None
    placement_eligible: bool | None
    interested_in_jobs: bool | None
    interested_in_internships: bool | None
    skills: list[str]
    education_entries: int
    experience_entries: int
    project_entries: int
    achievement_entries: int
    updated_at: datetime | None


class Document360Out(BaseModel):
    """One row of `uploads`, with its verdict. The bytes are behind
    `GET /mentor/uploads/{id}/file`, rule 2's gate applied to a file."""

    id: str
    kind: str
    title: str
    original_name: str
    mime_type: str
    size_bytes: int
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    uploaded_at: datetime
    #: True for the CV the student sent with their application, which approval
    #: moved into their uploads. It is the resume the office reads first.
    from_application: bool = False


class Student360Out(BaseModel):
    identity: AdminStudentOut
    login: Login360Out
    profile: Profile360Out
    documents: list[Document360Out]
    #: The course's semester count when the course names one (B4.1), so the
    #: screen can say "semester 3 of 8". Null on a batch whose course has not
    #: been given a shape, and the client must not fall back to 8.
    total_semesters: int | None
    semesters: list[Semester360Out]
    semester_history: list[SemesterMove360Out]
    readiness: PlacementReadinessOut
    open_items: OpenItems360Out
    current_mentor: MentorAssignment360Out
    mentor_history: MentorHistory360Out
    recent_audit: list[AuditRow360Out]


# ------------------------------------------------------------- windows --


def _semester_windows(
    student: Student, history: list[StudentSemesterHistory]
) -> dict[int, tuple[date | None, date | None]]:
    """When each semester began and ended, or nothing.

    A ledger day, an interview and a badge carry a DATE, not a semester. The
    only record of which semester a given date fell in is
    `student_semester_history`, whose rows say "left N, entered M, on this day".
    So a semester's window is bounded by the move INTO it and the next move OUT
    of it, and a semester neither move names has NO window.

    THE ONE FALLBACK IS THE ONE THAT IS PROVABLE. A student sitting in semester
    1 with no history has only ever been in semester 1, so that window runs from
    their enrolment to now. Any other semester without a recorded move is left
    unbounded, and the caller reports its activity as null.

    The alternative — attributing everything since enrolment to whatever
    semester the student is in today — is how a screen ends up showing three
    semesters of ledger days stacked under semester 4, and it is wrong in the
    direction nobody checks, because the number looks plausible.

    Dates are UTC dates. `time_ledger_days.day` is already a plain date; the
    timestamps are compared by their UTC date, which is the same convention the
    ledger writes on.
    """
    ordered = sorted(history, key=lambda r: (r.effective_on, r.created_at))
    entered: dict[int, date] = {}
    left: dict[int, date] = {}
    for row in ordered:
        # Latest move in wins, so a hold-back that returns a student to a
        # semester they have already sat re-opens that window rather than
        # merging it with the first one.
        entered[row.to_semester] = row.effective_on
        left.setdefault(row.from_semester, row.effective_on)

    first_position = ordered[0].from_semester if ordered else student.current_semester
    if first_position not in entered and first_position == 1:
        entered[1] = student.enrolled_at.date()

    windows: dict[int, tuple[date | None, date | None]] = {}
    for semester in set(entered) | set(left):
        start = entered.get(semester)
        if start is None:
            # An exit with no matching entry: we know when they left it, never
            # when they arrived. Half a window attributes half a semester's
            # activity, which is worse than none.
            continue
        end = left.get(semester)
        if end is not None and end < start:
            # They came back. The window is open again from the later entry.
            end = None
        windows[semester] = (start, end)
    return windows


def _in_window(when: date | None, window: tuple[date | None, date | None]) -> bool:
    if when is None:
        return False
    start, end = window
    if start is None or when < start:
        return False
    return end is None or when < end


# --------------------------------------------------------------- panel --


def _login_panel(db: Session, user: User) -> Login360Out:
    events = db.scalars(
        select(LoginEvent)
        .where(LoginEvent.user_id == user.id)
        .order_by(LoginEvent.at.desc())
        .limit(RECENT_SIGN_INS)
    ).all()
    disabled_by = (
        db.scalar(select(User.name).where(User.id == user.disabled_by_user_id))
        if user.disabled_by_user_id
        else None
    )
    return Login360Out(
        google_linked=user.google_sub is not None,
        # `scrypt:salt:digest` is the only shape `verify_password` accepts; the
        # SSO-only sentinel is deliberately not one, so this is exactly the
        # question "has anybody ever issued this account a password".
        password_set=str(user.password_hash or "").startswith("scrypt:"),
        token_version=user.token_version,
        disabled=user.disabled_at is not None,
        disabled_at=user.disabled_at,
        disable_reason=user.disable_reason,
        disabled_by_name=disabled_by,
        disabled_notice_sent_at=account_mail.last_sent_at(db, user),
        deleted_at=user.deleted_at,
        delete_reason=user.delete_reason,
        last_login_at=user.last_login_at,
        recent_sign_ins=[
            SignIn360Out(at=e.at, door=e.door, ip=e.ip, user_agent=e.user_agent)
            for e in events
        ],
    )


def _names(values: list | None) -> list[str]:
    """A profile's `skills` JSON as a list of words. The column is untyped --
    a student's profile stores whatever the profile form sent -- so a string is
    kept, a dict is read for the name-like key it carries, and anything else is
    dropped rather than rendered as `[object Object]`."""
    out: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, dict):
            text = str(value.get("name") or value.get("skill") or value.get("title") or "").strip()
        else:
            text = ""
        if text:
            out.append(text)
    return out


def _profile_panel(db: Session, student: Student) -> Profile360Out:
    profile = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student.id))
    if profile is None:
        return Profile360Out(
            on_record=False,
            phone=None, contact_email=None, linkedin_url=None, github_url=None,
            portfolio_url=None, city=None, career_summary=None,
            placement_eligible=None, interested_in_jobs=None, interested_in_internships=None,
            skills=[], education_entries=0, experience_entries=0, project_entries=0,
            achievement_entries=0, updated_at=None,
        )
    return Profile360Out(
        on_record=True,
        phone=profile.phone or None,
        contact_email=profile.email or None,
        linkedin_url=profile.linkedin_url or None,
        github_url=profile.github_url or None,
        portfolio_url=profile.portfolio_url or None,
        city=profile.city or None,
        career_summary=profile.career_summary or None,
        placement_eligible=profile.placement_eligible,
        interested_in_jobs=profile.interested_in_jobs,
        interested_in_internships=profile.interested_in_internships,
        skills=_names(profile.skills),
        education_entries=len(profile.education or []),
        experience_entries=len(profile.experience or []),
        project_entries=len(profile.projects or []),
        achievement_entries=len(profile.achievements or []),
        updated_at=profile.updated_at,
    )


def _documents_panel(db: Session, student: Student) -> list[Document360Out]:
    rows = db.scalars(
        select(Upload).where(Upload.student_id == student.id).order_by(Upload.uploaded_at.desc())
    ).all()
    return [
        Document360Out(
            id=u.id,
            kind=u.kind.value,
            title=u.title,
            original_name=u.original_name,
            mime_type=u.mime_type,
            size_bytes=u.size_bytes,
            status=u.status.value,
            review_note=u.review_note,
            reviewed_at=u.reviewed_at,
            uploaded_at=u.uploaded_at,
            from_application=(u.kind == UploadKind.RESUME and u.title == APPLICATION_CV_TITLE),
        )
        for u in rows
    ]


def _open_items(db: Session, student: Student) -> OpenItems360Out:
    pending_uploads = len(
        db.scalars(
            select(Upload.id).where(
                Upload.student_id == student.id,
                Upload.status == UploadStatus.PENDING_REVIEW,
            )
        ).all()
    )
    evidence = db.execute(
        select(BadgeEvidence.status).where(BadgeEvidence.student_id == student.id)
    ).all()
    pending_claims = sum(1 for (s,) in evidence if s == EvidenceStatus.PENDING_VERIFICATION)
    needs_info = sum(1 for (s,) in evidence if s == EvidenceStatus.MORE_INFO_REQUIRED)

    unsubmitted = 0
    for day in db.scalars(
        select(TimeLedgerDay)
        .options(selectinload(TimeLedgerDay.cells))
        .where(
            TimeLedgerDay.student_id == student.id,
            TimeLedgerDay.status != LedgerDayStatus.SUBMITTED,
        )
    ).all():
        if day.cells:
            unsubmitted += 1

    profile = db.scalar(
        select(StudentProfile).where(StudentProfile.student_id == student.id)
    )
    missing: list[str] = []
    for field, label in PROFILE_FIELDS:
        value = student.usn if field == "usn" else (getattr(profile, field, None) if profile else None)
        if not value:
            missing.append(label)

    return OpenItems360Out(
        pending_uploads=pending_uploads,
        pending_badge_claims=pending_claims,
        badge_claims_needing_info=needs_info,
        unsubmitted_ledger_days=unsubmitted,
        missing_profile_fields=missing,
        total=pending_uploads + pending_claims + needs_info + unsubmitted + len(missing),
    )


def _timeline(
    db: Session, student: Student, history: list[StudentSemesterHistory]
) -> tuple[list[Semester360Out], int | None]:
    results = {
        row.semester: row
        for row in db.scalars(
            select(SemesterResult)
            .options(selectinload(SemesterResult.subjects))
            .where(SemesterResult.student_id == student.id)
        ).all()
    }
    windows = _semester_windows(student, history)

    # THE COURSE'S DECLARED LENGTH, NOT `semester_bounds.ceiling_for_student`.
    # The ceiling falls back to DEFAULT_MAX_SEMESTER so an edit is never refused
    # on a deployment that has not filled its courses in; that fallback is right
    # for a bound and wrong for a LABEL — rendering "semester 3 of 8" on a batch
    # whose course names no length states a fact nobody entered. Null here, and
    # the client says nothing rather than 8. The walk itself is not rewritten:
    # `course_for_cohort` is the one place `cohort -> course` is written.
    course = course_for_cohort(db, student.cohort_id)
    total_semesters = course.total_semesters if course is not None else None

    highest = max(
        [student.current_semester, *results.keys(), *windows.keys()]
        + [r.to_semester for r in history]
        + [r.from_semester for r in history]
    )

    # Read once, bucketed in Python. One query per semester would be six queries
    # for a number nobody paginates, and the rows per student are in the dozens.
    ledger_days = db.execute(
        select(TimeLedgerDay.day).where(
            TimeLedgerDay.student_id == student.id,
            TimeLedgerDay.status == LedgerDayStatus.SUBMITTED,
        )
    ).all()
    interviews = db.execute(
        select(InterviewSession.started_at, InterviewEvaluation.overall_score)
        .outerjoin(
            InterviewEvaluation,
            InterviewEvaluation.interview_session_id == InterviewSession.id,
        )
        .where(
            InterviewSession.student_id == student.id,
            # Retention stamps `deleted_at` and redacts the turns in the same
            # pass; a deleted interview must not be counted as one that happened
            # and can no longer be opened.
            InterviewSession.deleted_at.is_(None),
        )
    ).all()
    badges = db.execute(
        select(StudentBadge.earned_at).where(
            StudentBadge.student_id == student.id,
            StudentBadge.status == StudentBadgeStatus.EARNED,
        )
    ).all()

    out: list[Semester360Out] = []
    for semester in range(1, highest + 1):
        window = windows.get(semester)
        result = results.get(semester)
        row = Semester360Out(
            semester=semester,
            is_current=(semester == student.current_semester),
            started_on=window[0] if window else None,
            ended_on=window[1] if window else None,
            activity_known=window is not None,
            results=(
                SemesterResults360Out(
                    sgpa=result.sgpa,
                    cgpa=result.cgpa,
                    live_backlogs=result.live_backlogs,
                    closed_backlogs=result.closed_backlogs,
                    subjects_recorded=len(result.subjects),
                    result_class=result.result_class,
                    published_on=result.published_on,
                )
                if result is not None
                else None
            ),
            ledger_days_reconciled=None,
            interviews=None,
            best_interview_score=None,
            badges_earned=None,
        )
        if window is not None:
            row.ledger_days_reconciled = sum(
                1 for (day,) in ledger_days if _in_window(day, window)
            )
            scores = [
                score
                for started_at, score in interviews
                if _in_window(started_at.date() if started_at else None, window)
            ]
            row.interviews = len(scores)
            real = [s for s in scores if s is not None]
            row.best_interview_score = max(real) if real else None
            row.badges_earned = sum(
                1
                for (earned_at,) in badges
                if _in_window(earned_at.date() if earned_at else None, window)
            )
        out.append(row)
    return out, total_semesters


def _recent_audit(db: Session, student: Student, user: User) -> list[AuditRow360Out]:
    """The last few trail rows ABOUT this student.

    WHY THIS IS NOT `routers/audit.py`'s GATE. That module is Main-Admin-only
    and says why: an audit row carries no college, so there is no honest way to
    narrow the WHOLE trail for a scoped holder. This is not the whole trail —
    it is the rows whose `entity_id` is this student's record or this student's
    account, for a caller who has already been proven to reach that student by
    both fences above. The narrowing is by entity, which the row does carry, so
    the objection that made audit.py role-gated does not apply.

    A registration's approval — the row that minted this account — is filed
    under `entity_type='registration'` against the APPLICATION's id, which this
    student row does not point at. It is deliberately not chased through the
    email address: matching a person by the string they typed on a public form
    is how the wrong person's application ends up on somebody's record.
    """
    actor = db.execute(
        select(AuditEvent, User.name)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(
            or_(
                (AuditEvent.entity_type == "student") & (AuditEvent.entity_id == student.id),
                (AuditEvent.entity_type == "user") & (AuditEvent.entity_id == user.id),
            )
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(RECENT_AUDIT_EVENTS)
    ).all()
    return [
        AuditRow360Out(
            id=event.id,
            occurred_at=event.occurred_at,
            action=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            actor_user_id=event.actor_user_id,
            actor_name=name,
            route=(event.metadata_json or {}).get("route"),
        )
        for event, name in actor
    ]


def _mentor_history_panel(db: Session, student: Student) -> MentorHistory360Out:
    """B9.1's spells, with the sentence that says what an empty one means.

    THREE STATES, NOT TWO, and the middle one is the reason this is a function
    rather than a ternary. "Nothing recorded and nobody mentors them" is a
    student waiting to be seated — the office's cue to assign somebody. "Nothing
    recorded and somebody DOES mentor them" is a row the history missed, which
    on this deployment can only mean the seeded spell was removed, and saying
    "no mentor" there would contradict the panel directly above it. The third is
    the ordinary one: rows, drawn as a timeline.
    """
    entries = compose_mentor_history(db, student.id)
    if entries:
        note = ""
    elif student.mentor_id:
        note = (
            "This student has a faculty member assigned, but no assignment "
            "history was recorded for them. Nothing is missing from the current "
            "assignment above; only the record of how it came about."
        )
    else:
        note = (
            "No mentor has ever been assigned to this student. This is not a "
            "gap in the record — it is a student waiting to be seated with a "
            "faculty member."
        )
    return MentorHistory360Out(available=bool(entries), note=note, entries=entries)


# ------------------------------------------------------------ endpoint --


@router.get("/students/{student_id}/360", response_model=Student360Out)
def student_360(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Student360Out:
    """Everything the office opens a student's record to see, in one read."""
    require_capability(db, session, CAPABILITY)
    student, user = _student_or_404(db, student_id)
    # B1.2: may you do it HERE. Asked after the row is loaded because the
    # ancestry is a property of the student, and asked BEFORE anything is read
    # off them. A holder of `admin.student_records` scoped to another
    # department gets the same 403 they would get on an edit — the list being
    # narrowed is decoration if the detail behind it is not.
    require_capability(db, session, CAPABILITY, target=ancestry_of_student(db, student.id))
    # Rule 2, separately and always. A MENTOR holding this capability sees
    # their own group, and a MENTOR with no group sees nobody — never the whole
    # programme. The one widening is the office's own: a SCOPED grant of this
    # capability opens the students it names, read only (2026-10-15, the
    # owner's decision; `policies.assert_student_scope` says how it is fenced).
    _assert_can_access_student(session, student.id, db, allow_records_grant=True)

    history = db.scalars(
        select(StudentSemesterHistory)
        .where(StudentSemesterHistory.student_id == student.id)
        .order_by(
            StudentSemesterHistory.effective_on.desc(),
            StudentSemesterHistory.created_at.desc(),
        )
    ).all()
    names = {
        uid: name
        for uid, name in db.execute(
            select(User.id, User.name).where(
                User.id.in_([r.by_user_id for r in history if r.by_user_id] or [""])
            )
        ).all()
    }
    semesters, total_semesters = _timeline(db, student, list(history))

    group = (
        db.scalar(select(Mentor).where(Mentor.id == student.mentor_id))
        if student.mentor_id
        else None
    )
    faculty_name = (
        db.scalar(select(User.name).where(User.id == group.user_id)) if group else None
    )

    # THE ACCESS TAB'S HALF OF THIS PAYLOAD IS THE MAIN ADMIN'S (2026-10-17).
    # Recent sign-ins (with their addresses and browsers), the record's own
    # history and the reasons the office wrote when it disabled or removed the
    # account are drawn on that tab, which nobody else is shown. A faculty
    # member reading on a grant still learns THAT the account is disabled,
    # from the flag and the date; the why was written for the office.
    office = session.get("role") == "ADMIN"
    login = _login_panel(db, user)
    if not office:
        login = login.model_copy(
            update={
                "recent_sign_ins": [],
                "disable_reason": None,
                "disabled_by_name": None,
                "disabled_notice_sent_at": None,
                "delete_reason": None,
            }
        )

    return Student360Out(
        identity=_one(db, student.id),
        login=login,
        profile=_profile_panel(db, student),
        documents=_documents_panel(db, student),
        total_semesters=total_semesters,
        semesters=semesters,
        semester_history=[
            SemesterMove360Out(
                id=r.id,
                kind=r.kind,
                from_semester=r.from_semester,
                to_semester=r.to_semester,
                effective_on=r.effective_on,
                reason=r.reason,
                by_user_id=r.by_user_id,
                by_name=names.get(r.by_user_id or ""),
                created_at=r.created_at,
            )
            for r in history
        ],
        readiness=compose_placement_readiness(db, student.id),
        open_items=_open_items(db, student),
        current_mentor=MentorAssignment360Out(
            mentor_id=student.mentor_id,
            mentor_user_id=group.user_id if group else None,
            mentor_name=faculty_name,
        ),
        mentor_history=_mentor_history_panel(db, student),
        recent_audit=_recent_audit(db, student, user) if office else [],
    )

