"""Student self-service endpoints. First slice: read your own profile."""

import threading
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, func, select
from sqlalchemy.orm import Session

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .. import batch_labels
from ..ai.llm import complete_chat, llm_config, student_data_egress_allowed
from ..clock import local_today
from ..db import get_db
from ..governance import require_feature
from ..identity import get_current_session
from ..document_manifest import DocumentFacts, release, save_and_record
from ..models.archived_document import DocumentOwnerKind
from ..document_store import (
    MAX_BYTES,
    MAX_UPLOAD_BYTES_PER_STUDENT,
    MAX_UPLOADS_PER_STUDENT,
    UploadRejected,
    content_disposition,
    delete as document_store_delete,
    read_bytes,
    VolumeQuota,
    QuotaRejected,
)
from ..resume_pdf import EvidenceProof, append_evidence, render_resume_pdf
from ..models.academic_history import AcademicGap, AcademicQualification
from ..models.academics import SemesterResult
from ..models.attendance import AttendanceRecord
from ..models.badge import StudentBadge, StudentBadgeStatus
from ..models.certification import CertificationProgress
from ..models.cohort import Cohort
from ..models.institution import AcademicCourse, AcademicSpecialization, College, Department
from ..models.course import Course, Enrollment, ProgressStatus
from ..models.job import Job, JobApplication
from ..models.lab import ActivityType, CheckInSource, LabSession, LearningMode
from ..models.mock_test import MockAttempt
# B6.3. The mock interviewer's four numbers reach the home screen through the
# SUMMARY table, never through `interview_evaluations`: the summaries outlive
# the 180-day purge, so a trend, a readiness factor and the Home chart do not
# quietly lose their oldest points every night. See app/interview_summary.py.
from ..models.interview import (
    InterviewEvaluation,
    InterviewScoreSummary,
    InterviewSession,
)
from ..models.offer import (
    OfferChannel,
    OfferRoleType,
    OfferStatus,
    OfferWorkMode,
    PlacementOffer,
)
from ..criteria import (
    ResolvedCriteria,
    resolve as resolve_criteria,
    resolve_for_student as resolve_criteria_for_student,
    resolve_for_students as resolve_criteria_for_students,
)
# B12.1/B12.2. Which postings reach this viewer, decided in one module shared
# with the alumni board — see `app/jobs_visibility.py` for why the two feeds
# must not each own a copy of that answer.
from ..jobs_visibility import audience_for_student, postings_for
from ..job_scout import job_source
from ..models.student_profile import StudentProfile
from ..models.resume import Resume, ResumeStatus
from ..models.schedule import ScheduleItem
from ..models.skill import Skill, SkillClaim, StudentSkill
from ..models.swoc import SwocEntry, SwocKind, SwocSource
from ..models.resume_profile import ResumeProfile
from ..models.time_ledger import TimeLedgerCell, TimeLedgerDay
from ..models.timesheet import DayActivity, TimeSheetEntry
from ..models.upload import Upload, UploadKind, UploadStatus
from ..models.user import LoginDay, Mentor, Role, Student, User
from ..ratelimit import llm_rate_limited

router = APIRouter(prefix="/student", tags=["student"])


class InstitutionLevelOut(BaseModel):
    """One row of the locked card, with its STATE — because a blank means two
    different things and the card must not confuse them.

    `set`         the level has a value; render label + value.
    `pending`     the level is expected and this student does not have it yet.
                  Render a dash AND the words "Not yet recorded" — text and
                  colour together, never colour alone. Something to chase.
    `not_in_use`  the level is optional and this institution does not use it.
                  Nothing is pending and nobody should chase it. The card HIDES
                  the row and then SAYS what was hidden, once, in
                  `not_in_use_note` — because a silently omitted row is the
                  confident blank the English-baseline rule forbids, while a
                  row omitted and counted in a sentence is stated, not blank.

    Rendering a not-in-use level as a dash would tell the student their record
    is incomplete when it is complete: a false pending, the mirror image of a
    confident zero. Both lie about state. Only the server can tell them apart,
    because only it knows the switch (HIERARCHY_LEVELS) and the chain.
    """

    key: str
    label: str
    value: str | None
    state: str  # "set" | "pending" | "not_in_use"


class InstitutionOut(BaseModel):
    """The locked "Institutional assignment — verified by Main Admin" card.

    EVERY FLAT FIELD IS NULLABLE, and that is the contract rather than laziness.
    The card is read through `students.cohort_id -> cohorts -> ...`, and any
    hop in that chain may legitimately be unset: a student is provisioned
    before an admin seats them, `cohorts` predates `departments` so older
    batches have no department, and the two optional levels are optional. A
    missing value is never defaulted and never invented — the same rule the
    English baseline follows for a pending score, and for the same reason: an
    invented institution on a screen headed "verified by Main Admin" is worse
    than an honest blank.

    `levels` is what the card actually renders, in the server's order with the
    server's labels (typed once, in HIERARCHY_LEVELS) — so the client never
    names a level, and the next level insertion costs it nothing. The flat
    fields stay for anything that reads them by name.

    STUDENTS CANNOT WRITE THESE. `update_profile` refuses them; the card says
    "Locked — not editable by students" and the API is what makes that true.
    """

    college_name: str | None
    college_code: str | None
    department_name: str | None
    course_name: str | None
    specialization_name: str | None
    batch_label: str | None
    entry_date: date | None
    expected_completion: date | None
    levels: list[InstitutionLevelOut]
    not_in_use_note: str | None


#: ONE round trip, not a chain of db.get() calls. The first version walked
#: Cohort -> Department -> College as three sequential queries on
#: GET /api/student/profile — the app's hottest endpoint — and extending that
#: pattern to five levels would have made it five. Every join here is LEFT
#: OUTER on a primary key: a level the admin never filled yields NULL for its
#: columns and drops nothing, which is `_institution_for`'s promise ("walks as
#: far as the data allows") written as SQL instead of as four early returns.
#:
#: The joins are FLAT, each hanging off `cohorts`, not chained through one
#: another. That is what the ancestor pointers on `cohorts` buy: a batch
#: attached at department level with no specialization still resolves its
#: department and college, where a chain spec -> course -> department would
#: have yielded NULL for all three.
_INSTITUTION_Q = (
    select(
        College.name.label("college_name"),
        College.code.label("college_code"),
        Department.name.label("department_name"),
        AcademicCourse.name.label("course_name"),
        AcademicSpecialization.name.label("specialization_name"),
        Cohort.batch_label.label("batch_label"),
        Cohort.start_date.label("start_date"),
        Cohort.end_date.label("end_date"),
    )
    .select_from(Cohort)
    .join(Department, Department.id == Cohort.department_id, isouter=True)
    .join(College, College.id == Department.college_id, isouter=True)
    .join(AcademicCourse, AcademicCourse.id == Cohort.course_id, isouter=True)
    .join(
        AcademicSpecialization,
        AcademicSpecialization.id == Cohort.specialization_id,
        isouter=True,
    )
    .where(Cohort.id == bindparam("cohort_id"))
)

#: The same two top levels reached WITHOUT a batch (31f7a4c60b12).
#:
#: College and Department are required at registration while Course,
#: Specialization and Batch are not, so "named a department, seated in nothing"
#: is the normal state of a college that has not built its batches yet. Before
#: `students.department_id` existed there was no query that could answer it and
#: the card came back blank.
#:
#: INNER on college, because `departments.college_id` is NOT NULL — a department
#: without a college cannot exist, so an outer join here would only invent a
#: half-resolved row that no caller knows how to render.
_DEPARTMENT_Q = (
    select(
        College.name.label("college_name"),
        College.code.label("college_code"),
        Department.name.label("department_name"),
    )
    .select_from(Department)
    .join(College, College.id == Department.college_id)
    .where(Department.id == bindparam("department_id"))
)

#: The fixed levels either side of the optional ones, in card order. These are
#: not in HIERARCHY_LEVELS because they are not switchable: a batch always
#: belongs to a department in a college. Their labels are the design's.
_FIXED_UPPER = (("college", "College"), ("department", "Department"))
_FIXED_LOWER = (("batch", "Batch"),)


def _join_names(names: list[str]) -> str:
    """"A", "A and B", "A, B and C". Grammar assembled here, once, rather than
    in a template that ends up reading "Course, and ."."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _compose_levels(values: dict[str, str | None], seated: bool) -> tuple[list[InstitutionLevelOut], str | None]:
    """Turn a flat name-per-level dict into the card's rows and its footnote.

    `seated` distinguishes "no cohort at all" (every level pending — the admin
    has not seated this student) from "seated in a batch that does not use a
    level" (that level is not in use). The switch decides the second case: a
    level flipped to required is `pending` when blank, because the admin now
    owes it; a level still optional is `not_in_use`.
    """
    from ..models import institution as institution_model

    rows: list[InstitutionLevelOut] = []
    not_in_use: list[str] = []

    def add(key: str, label: str, value: str | None, optional: bool) -> None:
        if value is not None:
            state = "set"
        elif not seated or not optional:
            state = "pending"
        else:
            state = "not_in_use"
            not_in_use.append(label)
        rows.append(InstitutionLevelOut(key=key, label=label, value=value, state=state))

    for key, label in _FIXED_UPPER:
        add(key, label, values.get(key), optional=False)
    for lv in institution_model.HIERARCHY_LEVELS:
        add(lv.key, lv.label, values.get(lv.key), optional=not lv.required)
    for key, label in _FIXED_LOWER:
        add(key, label, values.get(key), optional=False)

    note = None
    if not_in_use:
        where = values.get("college_code") or "this college"
        verb = "is" if len(not_in_use) == 1 else "are"
        note = f"{_join_names(not_in_use)} {verb} not used at {where}."
    return rows, note


def _empty_institution() -> InstitutionOut:
    levels, _ = _compose_levels({}, seated=False)
    return InstitutionOut(
        college_name=None,
        college_code=None,
        department_name=None,
        course_name=None,
        specialization_name=None,
        batch_label=None,
        entry_date=None,
        expected_completion=None,
        levels=levels,
        not_in_use_note=None,
    )


def _department_only(db: Session, department_id: str | None) -> InstitutionOut | None:
    """The card for a student placed by department alone, or None if unplaced.

    Two levels resolve (College, Department) and the rest read `pending`, which
    is the honest word: the admin owes this student a batch. `seated=False` is
    what produces that — a level left blank on a student with no batch is work
    outstanding, never "not in use at this college".
    """
    if not department_id:
        return None
    row = db.execute(_DEPARTMENT_Q, {"department_id": department_id}).mappings().first()
    if row is None:  # department_id names a department that is gone
        return None
    values = {
        "college": row["college_name"],
        "college_code": row["college_code"],
        "department": row["department_name"],
    }
    levels, note = _compose_levels(values, seated=False)
    return InstitutionOut(
        college_name=row["college_name"],
        college_code=row["college_code"],
        department_name=row["department_name"],
        course_name=None,
        specialization_name=None,
        batch_label=None,
        # The two dates belong to the BATCH, and there is no batch.
        entry_date=None,
        expected_completion=None,
        levels=levels,
        not_in_use_note=note,
    )


def _institution_for(db: Session, stu: Student | None) -> InstitutionOut:
    """Resolve the student's institutional assignment.

    TWO POINTERS, tried deepest first. `cohort_id` resolves all five levels; if
    there is no batch — or the batch is gone, or the batch was never filed under
    a department — `students.department_id` still places the student in a
    college and a department (31f7a4c60b12). Before that column existed this
    returned an empty card for every unseated student, which is every student at
    a college that has not built its batches, even though the registration form
    had REQUIRED them to name a department.

    Walks as far as the data allows and stops without complaint: nulls rather
    than a 404. The card is one block on a screen that has plenty else to show.
    """
    if stu is None:
        return _empty_institution()
    row = (
        db.execute(_INSTITUTION_Q, {"cohort_id": stu.cohort_id}).mappings().first()
        if stu.cohort_id
        else None
    )
    if row is None:  # no batch, or cohort_id names a cohort that is gone
        return _department_only(db, stu.department_id) or _empty_institution()
    if row["department_name"] is None:
        # A batch nobody filed under a department. The student's own pointer is
        # allowed to stand alone in exactly this case (see
        # `student_placement.resolve_student_department`), so prefer the fuller
        # card over printing a blank Department beside a real batch.
        by_department = _department_only(db, stu.department_id)
        if by_department is not None:
            row = dict(row) | {
                "college_name": by_department.college_name,
                "college_code": by_department.college_code,
                "department_name": by_department.department_name,
            }
    values = {
        "college": row["college_name"],
        "college_code": row["college_code"],
        "department": row["department_name"],
        "course": row["course_name"],
        "specialization": row["specialization_name"],
        "batch": row["batch_label"],
    }
    levels, note = _compose_levels(values, seated=True)
    return InstitutionOut(
        college_name=row["college_name"],
        college_code=row["college_code"],
        department_name=row["department_name"],
        course_name=row["course_name"],
        specialization_name=row["specialization_name"],
        batch_label=row["batch_label"],
        # The two dates belong to the BATCH, not the student — which is what the
        # design shows, and why they are not columns on `students`.
        entry_date=row["start_date"].date() if row["start_date"] else None,
        expected_completion=row["end_date"].date() if row["end_date"] else None,
        levels=levels,
        not_in_use_note=note,
    )


class ProfileOut(BaseModel):
    student_id: str
    # Identity, synced from the programme's student record and read-only on the
    # profile screen. Additive fields on an existing response: the v2 UI's
    # sidebar profile card and the profile screen's locked "Identity" block both
    # need them, and the SESSION cannot carry them — its claims are fixed by
    # contract (AGENTS.md: the require_* dependencies read the same payload the
    # Node app minted and were not changed), so widening the cookie to please a
    # sidebar is exactly the change that must not happen.
    usn: str | None
    full_name: str | None
    current_semester: int
    current_stage: str
    phone: str | None
    email: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None
    city: str | None
    career_summary: str | None
    placement_eligible: bool
    interested_in_jobs: bool
    interested_in_internships: bool
    education: list
    experience: list
    projects: list
    skills: list
    achievements: list
    leaderboard_opt_out: bool
    # Read-only, resolved through the cohort join. See InstitutionOut.
    institution: InstitutionOut
    # The assigned faculty mentor's name, or None when nobody is assigned yet.
    #
    # The Resume Builder's References step offers "add your mentor as a referee"
    # in one click, and it used to offer a HARD-CODED name — a person who does
    # not work here — which a student could put on a document a recruiter then
    # calls. A referee has to be the real one or there must be no button, and
    # the client cannot know which without being told. None here is what turns
    # that card off.
    mentor_name: str | None = None


@router.get("/profile", response_model=ProfileOut)
def my_profile(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> ProfileOut:
    # Through the one helper rather than a fourth copy of the same two lines:
    # this was an inline duplicate and therefore the one student endpoint the
    # B4.4 role check would have missed.
    student_id = _require_student(session)
    prof = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    if prof is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No profile yet.")
    return _profile_out(db, prof)


class SubjectMarkOut(BaseModel):
    subject_code: str
    subject_name: str
    credits: int
    internal: int
    external: int
    total: int
    passed: bool


class SemesterResultOut(BaseModel):
    semester: int
    sgpa: float | None
    cgpa: float | None
    closed_backlogs: int
    live_backlogs: int
    result_class: str | None
    subjects: list[SubjectMarkOut]


def _require_student(session: dict) -> str:
    """The student's own id, or a 403 — and the ROLE is checked, not just the claim.

    THE MOST DANGEROUS LINE IN B4.4. This used to read `session["studentId"]`
    and nothing else, and `_payload_for` (routers/auth.py) mints that claim for
    ANY account with a `students` row. Graduation flips `users.role` to ALUMNI
    and deliberately KEEPS the `students` row — it is the record of their marks,
    badges and interviews — so on the role check alone a graduate kept a valid
    `studentId` and with it roughly forty endpoints across this module,
    student_programme.py and badges.py: results, ledger, uploads, resume
    generation, SWOC acknowledgements. The Angular `roleGuard('STUDENT')` bounced
    them off the SCREENS, so the symptom would have been an API wide open behind
    a client that looked closed.

    It is not only graduation. `registrations`' GUARD 2 refuses to attach a
    Student row to a staff account precisely because `studentId` would then ride
    in a MENTOR's session; that guard is one write path, and this is the read
    side of the same fact. The role is what decides scope (rule 2). The claim
    only says which row.

    BOTH FACTS ARE REQUIRED AND THE ROLE IS ASKED FIRST, so a STUDENT-role
    account with no Student row and an ALUMNI account with one get different
    sentences — they are different problems and the office fixes them
    differently.
    """
    if session.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The student screens are open to current students only.",
        )
    student_id = session.get("studentId")
    if not student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a student account.")
    return student_id


@router.get("/results", response_model=list[SemesterResultOut])
def my_results(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[SemesterResultOut]:
    student_id = _require_student(session)
    rows = db.scalars(
        select(SemesterResult)
        .where(SemesterResult.student_id == student_id)
        .order_by(SemesterResult.semester)
    ).all()
    return [
        SemesterResultOut(
            semester=r.semester,
            sgpa=r.sgpa,
            cgpa=r.cgpa,
            closed_backlogs=r.closed_backlogs,
            live_backlogs=r.live_backlogs,
            result_class=r.result_class,
            subjects=[
                SubjectMarkOut(
                    subject_code=s.subject_code,
                    subject_name=s.subject_name,
                    credits=s.credits,
                    internal=s.internal,
                    external=s.external,
                    total=s.total,
                    passed=s.passed,
                )
                for s in r.subjects
            ],
        )
        for r in rows
    ]


class CourseAttendanceOut(BaseModel):
    course_code: str
    present: int
    total: int
    percent: float


class AttendanceSummaryOut(BaseModel):
    overall_percent: float
    present: int
    total: int
    by_course: list[CourseAttendanceOut]


@router.get("/attendance", response_model=AttendanceSummaryOut)
def my_attendance(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> AttendanceSummaryOut:
    student_id = _require_student(session)
    rows = db.execute(
        select(AttendanceRecord.course_code, AttendanceRecord.present).where(
            AttendanceRecord.student_id == student_id
        )
    ).all()

    per_course: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # code -> [present, total]
    present_total = grand_total = 0
    for course_code, present in rows:
        per_course[course_code][1] += 1
        grand_total += 1
        if present:
            per_course[course_code][0] += 1
            present_total += 1

    def pct(p: int, t: int) -> float:
        return round(100 * p / t, 1) if t else 0.0

    by_course = [
        CourseAttendanceOut(course_code=code, present=p, total=t, percent=pct(p, t))
        for code, (p, t) in sorted(per_course.items())
    ]
    return AttendanceSummaryOut(
        overall_percent=pct(present_total, grand_total),
        present=present_total,
        total=grand_total,
        by_course=by_course,
    )


class DashboardOut(BaseModel):
    name: str
    usn: str | None
    current_stage: str
    current_semester: int
    latest_cgpa: float | None
    attendance_percent: float


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> DashboardOut:
    """One call for the landing page: REEP stage, latest CGPA, and attendance %."""
    student_id = _require_student(session)
    stu = db.get(Student, student_id)
    if stu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")

    latest = db.scalar(
        select(SemesterResult)
        .where(SemesterResult.student_id == student_id)
        .order_by(SemesterResult.semester.desc())
        .limit(1)
    )
    att = db.execute(
        select(AttendanceRecord.present).where(AttendanceRecord.student_id == student_id)
    ).all()
    total = len(att)
    present = sum(1 for (p,) in att if p)

    return DashboardOut(
        name=session.get("name", ""),
        usn=stu.usn,
        current_stage=stu.current_stage.value,
        current_semester=stu.current_semester,
        latest_cgpa=latest.cgpa if latest else None,
        attendance_percent=round(100 * present / total, 1) if total else 0.0,
    )


@router.get("/overview")
def overview(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> dict:
    """Everything the landing page shows, in ONE request and ONE DB session.

    The Angular overview screen used to fire its ten GETs in a Promise.all —
    an 11-request volley (plus /auth/me) per view, each request taking its own
    threadpool slot and pooled connection, so TWO students landing at once
    already wanted more connections than the old pool had (2026-08 audit). The
    ten handlers are unchanged and still individually routed — sibling screens
    and older clients keep working — this endpoint simply composes them
    sequentially on this request's session: one connection, ten queries'
    worth of work, one JSON document.

    Keys mirror the client's destructuring, not the route paths, so the
    component's `load()` is a one-line change. Any handler's HTTPException
    (e.g. the 404 for a session that outlived its Student row) propagates for
    the whole document: a landing page for a student who no longer exists has
    no partial answer worth assembling.
    """
    return {
        "dashboard": dashboard(session, db),
        "attendance": my_attendance(session, db),
        "results": my_results(session, db),
        "streak": my_streak(session, db),
        "swoc": my_swoc(session, db),
        "mocks": my_mocks(session, db),
        "skills": my_skills(session, db),
        "next_actions": next_actions(session, db),
        "placement_readiness": placement_readiness(session, db),
        "recommendations": recommendations(session, db),
        # The landing's Academic History block (10th / 12th / UG cards and the
        # declared education gaps). Same reasoning as the ten above: the landing
        # already needs this document, and a separate GET per view is the
        # volley this endpoint exists to avoid.
        "academics": my_academics(session, db),
        # The career-goal card: the role preferences the office loaded from the
        # college's form, first choice first.
        "career_goal": my_career_goal(session, db),
    }


class CareerGoalOut(BaseModel):
    #: Role names in priority order, first choice first. Empty when the office
    #: has not loaded this student's preferences, never a guessed role.
    preferences: list[str]


@router.get("/career-goal", response_model=CareerGoalOut)
def my_career_goal(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> CareerGoalOut:
    """The student's role preferences as their career goal. Read-only on purpose:
    the goal is the preference the placement office collected, so there is no
    student-side write and no "change my goal"."""
    stu = db.get(Student, _require_student(session))
    if stu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    prefs = [p for p in (stu.role_preferences or []) if isinstance(p, str) and p.strip()]
    return CareerGoalOut(preferences=prefs)


class SwocItemOut(BaseModel):
    """One line of the student's own board — B7.5.

    THE FOUR NEW FIELDS ARE ADDITIVE AND THE TILE IS NOT REDRAWN. 04 asks for
    author, date and an acknowledgement here, and the board in this repository
    (`docs/redesign-2026-09/design/student/MentorLogRedesign.html`) draws none of
    them: the SWOC tiles are eyebrow + text, one tile per quadrant, and
    `03-student-portal-spec.md` says "API unchanged" for that screen. The
    current client concatenates every entry in a quadrant into ONE STRING
    (`mentor-log.component.ts`'s `joinSwoc`), so per-entry author, date or an
    acknowledge button is structurally impossible without a new tile — and
    inventing one is not this task's to invent. The API carries the facts so the
    screen can be built when the board says how; nothing that exists changes
    shape, because every one of these is a new key.

    `id` is here because `POST /student/swoc/{entry_id}/acknowledge` needs one
    and the student's payload has never exposed it.
    """

    id: str
    source: str
    text: str
    weight: int
    #: The author's NAME, with `author_recorded` beside it for the same reason
    #: the admin board carries the pair — and see that model for why the flag is
    #: NOT "the account is gone": `author_user_id` is `ON DELETE SET NULL`, so
    #: `author: null` means nobody was recorded and nothing else. On the
    #: student's own screen that must render as "Author not recorded"; "written
    #: by somebody who left" is a sentence about a person who never existed.
    author: str | None
    author_recorded: bool
    recorded_at: datetime
    semester: int | None
    acknowledged_at: datetime | None


class SwocBoardOut(BaseModel):
    strengths: list[SwocItemOut]
    weaknesses: list[SwocItemOut]
    opportunities: list[SwocItemOut]
    challenges: list[SwocItemOut]


@router.get("/swoc", response_model=SwocBoardOut)
def my_swoc(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> SwocBoardOut:
    student_id = _require_student(session)
    rows = db.execute(
        select(SwocEntry, User.name)
        .outerjoin(User, User.id == SwocEntry.author_user_id)
        .where(SwocEntry.student_id == student_id)
        .order_by(SwocEntry.weight.desc())
    ).all()
    buckets: dict[str, list[SwocItemOut]] = {
        "STRENGTH": [],
        "WEAKNESS": [],
        "OPPORTUNITY": [],
        "CHALLENGE": [],
    }
    for r, author in rows:
        buckets[r.kind.value].append(
            SwocItemOut(
                id=r.id, source=r.source.value, text=r.text, weight=r.weight,
                author=author, author_recorded=r.author_user_id is not None,
                recorded_at=r.recorded_at, semester=r.semester,
                acknowledged_at=r.acknowledged_at,
            )
        )
    return SwocBoardOut(
        strengths=buckets["STRENGTH"],
        weaknesses=buckets["WEAKNESS"],
        opportunities=buckets["OPPORTUNITY"],
        challenges=buckets["CHALLENGE"],
    )


@router.post("/swoc/{entry_id}/acknowledge", response_model=SwocItemOut)
def acknowledge_swoc(
    entry_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SwocItemOut:
    """"I have read this." — B7.5. The student's own entry, and nobody else's.

    FIRST-PERSON, WITH THE ID FROM THE SESSION AND NEVER FROM THE REQUEST.
    `_require_student` resolves who is asking, and the entry must carry that
    student_id or this is a 404 — an entry id is a bare uuid, so without that
    check the endpoint would acknowledge any line about any student on the
    deployment, which is a write against somebody else's record.

    IDEMPOTENT, AND IT KEEPS THE FIRST TIMESTAMP. Pressing it twice is a
    double-tap, not a second reading, and re-stamping would move the date the
    mentor sees. A line cannot be UN-acknowledged: "I read it" is not a thing a
    later click makes untrue, and a toggle would let a student quietly undo the
    one signal this field carries.

    NOT FEATURE-GATED. `governance.require_feature`'s map gates
    `student.mentor_log` on `GET /student/mentor-meetings` only, and `GET
    /student/swoc` — which also feeds Home — has never been gated. Acknowledging
    a line about yourself is closest to that docstring's "artefact the student
    already produced" exemption; gating it would hide the button on a board the
    same screen still draws.
    """
    student_id = _require_student(session)
    row = db.execute(
        select(SwocEntry, User.name)
        .outerjoin(User, User.id == SwocEntry.author_user_id)
        .where(SwocEntry.id == entry_id, SwocEntry.student_id == student_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such entry.")
    entry, author = row
    if entry.acknowledged_at is None:
        entry.acknowledged_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(entry)
    return SwocItemOut(
        id=entry.id, source=entry.source.value, text=entry.text, weight=entry.weight,
        author=author, author_recorded=entry.author_user_id is not None,
        recorded_at=entry.recorded_at, semester=entry.semester,
        acknowledged_at=entry.acknowledged_at,
    )


#: B6.3 — where one row of the Home mocks chart came from.
#:
#: `mock_attempt` is the staff-logged rehearsal the placement cell has always
#: recorded by hand; `interview` is a completed mock interview with the AI
#: interviewer. ONE SERIES, TWO SOURCES, because to a student they are the same
#: thing — "how have my practice interviews gone" — and two charts side by side
#: would make them ask which one counts.
#:
#: The field DEFAULTS to `mock_attempt`, which is what makes this additive: a
#: client built before this release reads the same six keys it always did and
#: simply gains rows.
MOCK_SOURCE_ATTEMPT = "mock_attempt"
MOCK_SOURCE_INTERVIEW = "interview"


class MockAttemptOut(BaseModel):
    type: str
    taken_on: datetime
    score: float | None
    max_score: float | None
    percent: float | None
    notes: str | None
    source: str = MOCK_SOURCE_ATTEMPT


@router.get("/mocks", response_model=list[MockAttemptOut])
def my_mocks(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[MockAttemptOut]:
    """Every practice attempt, newest first — staff-logged and AI both (B6.3).

    THE INTERVIEWS COME FROM `interview_score_summaries`, not from
    `interview_sessions` joined to their evaluations, and the difference shows
    up six months after a deployment goes live rather than in any test: the
    summary is the row that survives `retention.purge_expired`, so the oldest
    points on this chart stay where they are instead of disappearing overnight.

    ONLY COMPLETED INTERVIEWS COUNT HERE, which is deliberately NARROWER than
    `GET /api/interview/progress`. This is the Home chart — "how have my
    practice attempts scored" — and an abandoned interview has no score to plot.
    The progress screen carries every attempt including the abandoned ones,
    because "three attempts abandoned in the first minute" is the fact a MENTOR
    needs, and it is a fact about practice habits rather than about performance.

    A scorecard the model never produced leaves `score` NULL. It is not turned
    into a 0 anywhere on the way here: a missing score and a zero mean opposite
    things, and this list is charted.
    """
    student_id = _require_student(session)
    rows = db.scalars(
        select(MockAttempt)
        .where(MockAttempt.student_id == student_id)
        .order_by(MockAttempt.taken_on.desc())
    ).all()
    out = [
        MockAttemptOut(
            type=r.type.value,
            taken_on=r.taken_on,
            score=r.score,
            max_score=r.max_score,
            percent=(
                round(100 * r.score / r.max_score, 1)
                if (r.score is not None and r.max_score)
                else None
            ),
            notes=r.notes,
            source=MOCK_SOURCE_ATTEMPT,
        )
        for r in rows
    ]
    interviews = db.scalars(
        select(InterviewScoreSummary)
        .where(
            InterviewScoreSummary.student_id == student_id,
            InterviewScoreSummary.status == "completed",
        )
        .order_by(InterviewScoreSummary.started_at.desc())
    ).all()
    out.extend(
        MockAttemptOut(
            # The same word `MockType.INTERVIEW` carries, so the chart's series
            # key does not fork. `source` is what tells the two apart, and it is
            # the field a client should branch on — never the type.
            type="INTERVIEW",
            taken_on=iv.started_at,
            score=None if iv.overall_score is None else float(iv.overall_score),
            # The scorecard is already out of 100, so the percent and the score
            # are the same number. Stated rather than left for the client to
            # infer from a null max_score, which would chart as a bare count.
            max_score=None if iv.overall_score is None else 100.0,
            percent=None if iv.overall_score is None else float(iv.overall_score),
            # No notes. The interview's own words are its transcript and its
            # scorecard, both of which have their own screen with the
            # calibration copy beside them; a summary sentence pulled onto a
            # chart tooltip would be the model's judgement with none of that.
            notes=None,
            source=MOCK_SOURCE_INTERVIEW,
        )
        for iv in interviews
    )
    out.sort(key=lambda m: m.taken_on, reverse=True)
    return out


class StudentSkillOut(BaseModel):
    slug: str
    name: str
    category: str
    level: int
    verified: bool
    # The upload the mentor verified this against, when there is one. The resume
    # builder's "View proof" needs it: a verified skill the student cannot open
    # the evidence for is an assertion, not a citation.
    evidence_upload_id: str | None = None


@router.get("/skills", response_model=list[StudentSkillOut])
def my_skills(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[StudentSkillOut]:
    student_id = _require_student(session)
    rows = db.scalars(
        select(StudentSkill).where(StudentSkill.student_id == student_id)
    ).all()
    out = [
        StudentSkillOut(
            slug=r.skill.slug,
            name=r.skill.name,
            category=r.skill.category,
            level=r.level,
            verified=r.verified,
            evidence_upload_id=r.evidence_upload_id,
        )
        for r in rows
    ]
    # Grouped by category, strongest first within each.
    return sorted(out, key=lambda s: (s.category, -s.level))


class SkillCatalogueOut(BaseModel):
    id: str
    slug: str
    name: str
    category: str


@router.get("/skills/catalogue", response_model=list[SkillCatalogueOut])
def skills_catalogue(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[SkillCatalogueOut]:
    """The full skill catalogue — what a student picks from when filing a claim."""
    _require_student(session)
    rows = db.scalars(select(Skill).order_by(Skill.category, Skill.name)).all()
    return [
        SkillCatalogueOut(id=s.id, slug=s.slug, name=s.name, category=s.category) for s in rows
    ]


class StreakOut(BaseModel):
    current: int
    longest: int
    days_active: int
    last_active: date | None


@router.get("/streak", response_model=StreakOut)
def my_streak(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> StreakOut:
    """Login streak from LoginDay (one row per active day). Current counts back
    from today (or yesterday, so an as-yet-unopened today doesn't break it)."""
    days = sorted(
        set(db.scalars(select(LoginDay.day).where(LoginDay.user_id == session["userId"])).all())
    )
    if not days:
        return StreakOut(current=0, longest=0, days_active=0, last_active=None)

    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)

    today = date.today()
    current = 0
    if days[-1] in (today, today - timedelta(days=1)):
        current = 1
        i = len(days) - 1
        while i > 0 and days[i] - days[i - 1] == timedelta(days=1):
            current += 1
            i -= 1

    return StreakOut(
        current=current, longest=longest, days_active=len(days), last_active=days[-1]
    )


class TimeSheetEntryOut(BaseModel):
    day: date
    activity: str
    minutes: int


class TimeSheetSummaryOut(BaseModel):
    window_days: int
    by_activity_minutes: dict[str, int]
    skilling_hours: float
    weekly_hour_target: float
    entries: list[TimeSheetEntryOut]


@router.get("/timesheet", response_model=TimeSheetSummaryOut)
def my_timesheet(
    days: int = 7,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> TimeSheetSummaryOut:
    """The time log over the last `days`, with per-activity totals and the
    SKILLING-hours-vs-target the ledger's weekly strip draws.

    THE LEDGER IS WHAT THE STUDENT FILLS IN, SO THE LEDGER IS WHAT THIS SUMS
    (2026-09-22). `time_sheet_entries` was the old free-form time log's table,
    and when the Time Allocation Ledger replaced that screen the only thing
    left writing it was `POST /student/timesheet`, which no client posts to.
    This endpoint went on reading it alone, so the "Skilling this week" strip
    on the ledger read "0 h of a 12 h target" under a fortnight of ledger days
    carrying three hours of skilling each — the student's own screen telling
    them their filled-in record did not exist. Per calendar day, the ledger's
    cells win where a ledger row exists (that is the record the student made
    and can see), and a legacy row is counted only for a day that has no
    ledger row, so the seed's demo data and any history from the old screen
    still count once and never twice.
    """
    student_id = _require_student(session)
    window = max(1, min(days, 90))
    today = local_today()
    since = today - timedelta(days=window - 1)

    ledger_rows = db.execute(
        select(TimeLedgerDay.day, TimeLedgerCell.activity, func.sum(TimeLedgerCell.half_hours))
        .select_from(TimeLedgerDay)
        .join(TimeLedgerCell, TimeLedgerCell.ledger_day_id == TimeLedgerDay.id)
        .where(
            TimeLedgerDay.student_id == student_id,
            TimeLedgerDay.day >= since,
            TimeLedgerDay.day <= today,
        )
        .group_by(TimeLedgerDay.day, TimeLedgerCell.activity)
    ).all()
    # minutes per (day, activity): a half hour is thirty minutes.
    minutes: dict[tuple[date, str], int] = {}
    for day, activity, halves in ledger_rows:
        minutes[(day, activity.value)] = minutes.get((day, activity.value), 0) + int(halves) * 30
    ledger_days = {day for day, _ in minutes}

    legacy = db.scalars(
        select(TimeSheetEntry)
        .where(
            TimeSheetEntry.student_id == student_id,
            TimeSheetEntry.day >= since,
            TimeSheetEntry.day <= today,
        )
        .order_by(TimeSheetEntry.day)
    ).all()
    for r in legacy:
        if r.day in ledger_days:
            continue
        minutes[(r.day, r.activity.value)] = minutes.get((r.day, r.activity.value), 0) + r.minutes

    by_activity: dict[str, int] = {}
    for (_day, activity), mins in minutes.items():
        by_activity[activity] = by_activity.get(activity, 0) + mins

    stu = db.get(Student, student_id)
    return TimeSheetSummaryOut(
        window_days=window,
        by_activity_minutes=by_activity,
        skilling_hours=round(by_activity.get("SKILLING", 0) / 60, 1),
        weekly_hour_target=stu.weekly_hour_target if stu else 12.0,
        entries=[
            TimeSheetEntryOut(day=day, activity=activity, minutes=mins)
            for (day, activity), mins in sorted(minutes.items())
        ],
    )


class QualificationOut(BaseModel):
    level: str
    institution: str
    board: str | None
    year: int
    marks: float
    max_marks: float
    percent: float
    medium: str | None
    location: str | None
    subjects: str | None


class AcademicGapOut(BaseModel):
    twelfth_to_grad_mo: int
    diploma_to_grad_mo: int
    grad_to_pg_mo: int
    other_mo: int
    total_mo: int


class AcademicsOut(BaseModel):
    qualifications: list[QualificationOut]
    gap: AcademicGapOut


@router.get("/academics", response_model=AcademicsOut)
def my_academics(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> AcademicsOut:
    student_id = _require_student(session)
    quals = db.scalars(
        select(AcademicQualification)
        .where(AcademicQualification.student_id == student_id)
        .order_by(AcademicQualification.year)
    ).all()
    gap = db.get(AcademicGap, student_id)
    gap_out = AcademicGapOut(
        twelfth_to_grad_mo=gap.twelfth_to_grad_mo if gap else 0,
        diploma_to_grad_mo=gap.diploma_to_grad_mo if gap else 0,
        grad_to_pg_mo=gap.grad_to_pg_mo if gap else 0,
        other_mo=gap.other_mo if gap else 0,
        total_mo=(
            gap.twelfth_to_grad_mo + gap.diploma_to_grad_mo + gap.grad_to_pg_mo + gap.other_mo
            if gap
            else 0
        ),
    )
    return AcademicsOut(
        qualifications=[
            QualificationOut(
                level=q.level.value,
                institution=q.institution,
                board=q.board,
                year=q.year,
                marks=q.marks,
                max_marks=q.max_marks,
                percent=round(100 * q.marks / q.max_marks, 1) if q.max_marks else 0.0,
                medium=q.medium,
                location=q.location,
                subjects=q.subjects,
            )
            for q in quals
        ],
        gap=gap_out,
    )


class JobRowOut(BaseModel):
    id: str
    title: str
    company: str
    degree_level: str
    location: str | None
    apply_url: str | None
    required_skills: list[str]
    match_percent: float
    eligible: bool
    reasons: list[str]
    applied: bool
    closes_on: str | None
    posted_on: str | None
    #: `office`, or the board the daily job agent found it on (`adzuna`,
    #: `jooble`); the screen prints Adzuna's attribution from it, which that
    #: board's terms require on every advert shown.
    source: str = "office"


@router.get("/jobs", response_model=list[JobRowOut])
def my_jobs(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[JobRowOut]:
    """The opportunities feed with a per-row skill match % and the eligibility
    verdict (per-posting CGPA / live-backlog gates).

    NARROWED TO THIS STUDENT'S COLLEGE, COURSE AND TRACK (B12.1), and to the
    postings that are still open (B12.2). Both rules live in
    `app/jobs_visibility.py` because the alumni board applies exactly the same
    ones; read that module before changing what a NULL college means, because
    the answer is load-bearing for every posting that existed before B12.1.
    """
    student_id = _require_student(session)
    require_feature(db, student_id, "student.jobs")

    skill_slugs = set(
        db.scalars(
            select(Skill.slug)
            .join(StudentSkill, StudentSkill.skill_id == Skill.id)
            .where(StudentSkill.student_id == student_id)
        ).all()
    )
    latest = db.scalar(
        select(SemesterResult)
        .where(SemesterResult.student_id == student_id)
        .order_by(SemesterResult.semester.desc())
        .limit(1)
    )
    latest_cgpa = latest.cgpa if latest else None
    live_backlogs = (
        db.scalar(
            select(func.coalesce(func.sum(SemesterResult.live_backlogs), 0)).where(
                SemesterResult.student_id == student_id
            )
        )
        or 0
    )
    applied_ids = set(
        db.scalars(
            select(JobApplication.job_id).where(JobApplication.student_id == student_id)
        ).all()
    )
    # B8.2: ONE RESOLVER, and this call site is why it exists. It used to fall
    # through to `None` when no criteria row was set, which is NO GATE AT ALL —
    # while `readiness_criteria` two screens away was applying 6.0 CGPA to the
    # same student. `criteria.resolve_for_student` walks the student's course,
    # then their college, then the programme row, then the hard-coded defaults;
    # `app/criteria.py` records that this now gates a deployment which had no
    # row, and why that is the point rather than a regression.
    crit = resolve_criteria_for_student(db, student_id)
    gap = db.get(AcademicGap, student_id)
    gap_months = (
        gap.twelfth_to_grad_mo + gap.diploma_to_grad_mo + gap.grad_to_pg_mo + gap.other_mo
        if gap
        else 0
    )

    rows: list[JobRowOut] = []
    audience = audience_for_student(db, student_id)
    for j in db.scalars(postings_for(audience)).all():
        required = set(j.required_skills or [])
        match = round(100 * len(skill_slugs & required) / len(required), 1) if required else 100.0
        # Per-posting override wins; else fall back to the active criteria.
        # The per-posting override still wins; what changed is what it falls
        # back TO. A posting that names no gate is governed by the criteria that
        # resolved for this student, which is the same row their readiness card
        # is scored against.
        min_cgpa = j.min_cgpa if j.min_cgpa is not None else crit.min_cgpa
        max_backlogs = (
            j.max_live_backlogs if j.max_live_backlogs is not None else crit.max_live_backlogs
        )
        max_gap = crit.max_gap_months
        reasons: list[str] = []
        # A null CGPA is unassessed (not blocking); only an actual below-cutoff blocks.
        if min_cgpa is not None and latest_cgpa is not None and latest_cgpa < min_cgpa:
            reasons.append(f"CGPA {latest_cgpa} is below the required {min_cgpa}")
        if max_backlogs is not None and live_backlogs > max_backlogs:
            reasons.append(f"{live_backlogs} live backlog(s) exceeds the limit of {max_backlogs}")
        if max_gap is not None and gap_months > max_gap:
            reasons.append(f"education gap {gap_months}mo exceeds the limit of {max_gap}mo")
        rows.append(
            JobRowOut(
                id=j.id,
                title=j.title,
                company=j.company,
                degree_level=j.degree_level.value,
                location=j.location,
                apply_url=j.apply_url,
                required_skills=j.required_skills or [],
                match_percent=match,
                eligible=not reasons,
                reasons=reasons,
                applied=j.id in applied_ids,
                closes_on=j.closes_on.isoformat() if j.closes_on else None,
                posted_on=j.posted_on.isoformat() if j.posted_on else None,
                source=job_source(j),
            )
        )
    return rows


class ApplyIn(BaseModel):
    notes: str | None = None


@router.post("/jobs/{job_id}/apply")
def apply_to_job(
    job_id: str,
    body: ApplyIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    student_id = _require_student(session)
    # BEFORE the job lookup, so a student whose jobs feed is switched off reads
    # the office's sentence rather than "Job not found" for a posting that is
    # sitting right there.
    require_feature(db, student_id, "student.jobs")
    # THE BUTTON OBEYS THE LIST (B12.1/B12.2). The feed above narrows to this
    # student's college, course and track and to the postings still open;
    # without the same predicate here that narrowing is decoration, because the
    # id of a posting for another college is guessable and a closed posting's id
    # is sitting in the student's own browser from before it closed. Same 404 as
    # a posting that does not exist, and deliberately the same sentence: "you
    # may not apply to this" and "this is not for you" are the same fact to the
    # applicant, and two different answers would say which colleges exist.
    job = db.scalar(
        postings_for(audience_for_student(db, student_id)).where(Job.id == job_id)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    existing = db.scalar(
        select(JobApplication).where(
            JobApplication.student_id == student_id, JobApplication.job_id == job_id
        )
    )
    if existing:
        return {"applied": True, "already": True}
    db.add(JobApplication(student_id=student_id, job_id=job_id, notes=body.notes))
    db.commit()
    return {"applied": True, "already": False}


class OfferIn(BaseModel):
    role_type: str
    job_title: str
    organisation: str
    channel: str = "ON_CAMPUS"
    work_mode: str = "ONSITE"
    location: str | None = None
    ctc_inr: int = 0
    fixed_gross_inr: int = 0
    joining_date: datetime | None = None
    job_id: str | None = None


class OfferOut(BaseModel):
    id: str
    role_type: str
    job_title: str
    organisation: str
    channel: str
    work_mode: str
    location: str | None
    ctc_inr: int
    fixed_gross_inr: int
    status: str


def _offer_out(o: PlacementOffer) -> OfferOut:
    return OfferOut(
        id=o.id,
        role_type=o.role_type.value,
        job_title=o.job_title,
        organisation=o.organisation,
        channel=o.channel.value,
        work_mode=o.work_mode.value,
        location=o.location,
        ctc_inr=o.ctc_inr,
        fixed_gross_inr=o.fixed_gross_inr,
        status=o.status.value,
    )


@router.post("/offers", response_model=OfferOut, status_code=status.HTTP_201_CREATED)
def create_offer(
    body: OfferIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> OfferOut:
    student_id = _require_student(session)
    try:
        role = OfferRoleType(body.role_type)
        channel = OfferChannel(body.channel)
        mode = OfferWorkMode(body.work_mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid role_type / channel / work_mode.",
        )
    # job_id is an optional FK the client fills in when the offer came from a
    # posting on the board. Unchecked, a typo reached Postgres and came back as a
    # foreign-key IntegrityError — a 500 that reads to the student as "the site is
    # broken" when the honest answer is "that job does not exist". Checked here,
    # in the same place the enum values are, so every bad field in this body gets
    # the same treatment. POST /jobs/{job_id}/apply already answers 404 this way.
    if body.job_id and db.get(Job, body.job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    offer = PlacementOffer(
        student_id=student_id,
        role_type=role,
        job_title=body.job_title,
        organisation=body.organisation,
        channel=channel,
        work_mode=mode,
        location=body.location,
        ctc_inr=body.ctc_inr,
        fixed_gross_inr=body.fixed_gross_inr,
        joining_date=body.joining_date,
        job_id=body.job_id,
        status=OfferStatus.DRAFT,
    )
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return _offer_out(offer)


@router.get("/offers", response_model=list[OfferOut])
def list_offers(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[OfferOut]:
    student_id = _require_student(session)
    rows = db.scalars(
        select(PlacementOffer)
        .where(PlacementOffer.student_id == student_id)
        .order_by(PlacementOffer.created_at.desc())
    ).all()
    return [_offer_out(o) for o in rows]


@router.post("/offers/{offer_id}/submit", response_model=OfferOut)
def submit_offer(
    offer_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> OfferOut:
    student_id = _require_student(session)
    offer = db.get(PlacementOffer, offer_id)
    if offer is None or offer.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found.")
    if offer.status != OfferStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a draft offer can be submitted."
        )
    offer.status = OfferStatus.PENDING_APPROVAL
    db.commit()
    db.refresh(offer)
    return _offer_out(offer)


def _profile_out(db: Session, prof: StudentProfile) -> ProfileOut:
    """The ONE place a ProfileOut is built.

    It used to be two: `my_profile` composed the full response inline while this
    helper composed a shorter one for `update_profile`. When usn / full_name /
    current_semester / current_stage were added to ProfileOut only the inline
    copy was updated, so PUT /api/student/profile raised a 4-field
    ValidationError on every call — a break nothing caught, because no test
    exercises the update path's response body.

    Two constructions of one response is how that happens, so there is now one.
    """
    stu = db.get(Student, prof.student_id)
    owner = db.get(User, stu.user_id) if stu else None
    # A faculty account is not a mentor by existing: `students.mentor_id` is null
    # until the Main Admin assigns one, and a student with no mentor gets None
    # rather than a placeholder.
    mentor_name = None
    if stu is not None and stu.mentor_id:
        mentor = db.get(Mentor, stu.mentor_id)
        mentor_owner = db.get(User, mentor.user_id) if mentor is not None else None
        mentor_name = mentor_owner.name if mentor_owner is not None else None
    return ProfileOut(
        student_id=prof.student_id,
        usn=stu.usn if stu else None,
        full_name=owner.name if owner else None,
        current_semester=stu.current_semester if stu else 1,
        current_stage=stu.current_stage.value if stu else "EXCEL",
        institution=_institution_for(db, stu),
        mentor_name=mentor_name,
        phone=prof.phone,
        email=prof.email,
        linkedin_url=prof.linkedin_url,
        github_url=prof.github_url,
        portfolio_url=prof.portfolio_url,
        city=prof.city,
        career_summary=prof.career_summary,
        placement_eligible=prof.placement_eligible,
        interested_in_jobs=prof.interested_in_jobs,
        interested_in_internships=prof.interested_in_internships,
        education=prof.education or [],
        experience=prof.experience or [],
        projects=prof.projects or [],
        skills=prof.skills or [],
        achievements=prof.achievements or [],
        leaderboard_opt_out=prof.leaderboard_opt_out,
    )


class ProfileUpdateIn(BaseModel):
    phone: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    city: str | None = None
    career_summary: str | None = None
    interested_in_jobs: bool | None = None
    interested_in_internships: bool | None = None
    leaderboard_opt_out: bool | None = None
    education: list | None = None
    experience: list | None = None
    projects: list | None = None
    achievements: list | None = None


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileUpdateIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ProfileOut:
    """Edit your own profile. Only the fields sent are changed; placement_eligible
    is admin-set and intentionally absent from the editable set."""
    student_id = _require_student(session)
    prof = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    if prof is None:
        prof = StudentProfile(student_id=student_id)
        db.add(prof)
    changes = body.model_dump(exclude_unset=True)
    # An explicit `null` on a column that cannot hold one is a bad request, not
    # a database error: it used to reach the INSERT/UPDATE and come back as an
    # IntegrityError 500 (DEF-002, testing/docs/05-incident-reports.md). Asked
    # of the table rather than listed here, so a column made NOT NULL later is
    # covered without anybody remembering this handler.
    refused = [
        field
        for field, value in changes.items()
        if value is None
        and (column := StudentProfile.__table__.c.get(field)) is not None
        and not column.nullable
    ]
    if refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{', '.join(sorted(refused))} cannot be empty; leave the field out to keep its value.",
        )
    for field, value in changes.items():
        setattr(prof, field, value)
    db.commit()
    db.refresh(prof)
    return _profile_out(db, prof)


class ScheduleItemOut(BaseModel):
    id: str
    type: str
    title: str
    starts_at: datetime
    location: str | None
    course_code: str | None


@router.get("/schedule", response_model=list[ScheduleItemOut])
def my_schedule(
    upcoming: bool = True,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[ScheduleItemOut]:
    student_id = _require_student(session)
    query = select(ScheduleItem).where(ScheduleItem.student_id == student_id)
    if upcoming:
        query = query.where(ScheduleItem.starts_at >= datetime.now(timezone.utc))
    rows = db.scalars(query.order_by(ScheduleItem.starts_at)).all()
    return [
        ScheduleItemOut(
            id=i.id,
            type=i.type.value,
            title=i.title,
            starts_at=i.starts_at,
            location=i.location,
            course_code=i.course_code,
        )
        for i in rows
    ]


class TimeSheetLogIn(BaseModel):
    day: date
    activity: str  # DayActivity
    minutes: int = Field(ge=0, le=1440)


@router.post("/timesheet")
def log_timesheet(
    body: TimeSheetLogIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    """Upsert the minutes for one (day, activity) — one row per bucket per day."""
    student_id = _require_student(session)
    try:
        activity = DayActivity(body.activity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid activity."
        )
    entry = db.scalar(
        select(TimeSheetEntry).where(
            TimeSheetEntry.student_id == student_id,
            TimeSheetEntry.day == body.day,
            TimeSheetEntry.activity == activity,
        )
    )
    if entry is None:
        entry = TimeSheetEntry(
            student_id=student_id, day=body.day, activity=activity, minutes=body.minutes
        )
        db.add(entry)
    else:
        entry.minutes = body.minutes
    db.commit()
    return {"day": str(body.day), "activity": activity.value, "minutes": body.minutes}


# --------------------------------------------------------------------------- #
# The resume document
# --------------------------------------------------------------------------- #
#
# THE BUILDER'S CONTENT IS WHAT THE DOCUMENT IS MADE OF, and until this was
# written it was not. The composer read the `student_profiles` row, the verified
# skills and the academic record — and nothing else. So a student could fill all
# fifteen sections of the builder, watch the sidebar climb to 100%, press
# Generate, and receive a five-line page carrying none of their experience,
# internships, projects, publications, certifications, positions, objective,
# achievements or referees, printed under a card that told them the resume was
# "drawing on your full record". The builder wrote `resume_profiles.data`; no
# reader existed. The screen said the work had landed and the document said it
# had not, which is the worse of the two ways to be wrong.

#: What a builder section contributes is decided HERE, by name, and that is the
#: point: the resume builder is also the placement office's intake form, so
#: `resume_profiles.data` holds material an employer must never see and that the
#: screens have already promised will not travel — `family` (next-of-kin,
#: "only used where the placement office requires next-of-kin details"),
#: `basic.dob` ("Used for placement eligibility only — not shown on your
#: exported resume"), `basic.medical_history` ("visible to your mentor and the
#: placement office, never to recruiters"), gender / blood group / marital
#: status, `basic.dream_company` (an aspiration addressed to the office),
#: `policy` (the student's acceptance of the placement terms) and `goal` (which
#: posting this copy is aimed at). The street lines and postal code of an
#: address are the office's delivery detail; a resume carries a city.
#:
#: A denylist would publish the NEXT section someone adds to the builder by
#: default, and the first person to find out would be the student, in front of
#: an employer. So nothing reaches the page unless a function below puts it
#: there, and `tests/test_resume_document.py` fills every section with a marker
#: and asserts the private ones are absent.
_PRIVATE_BUILDER_SECTIONS = ("family", "policy", "goal")


def _rt(value) -> str:
    """A trimmed string from an untrusted JSON leaf; "" for anything else.

    `resume_profiles.data` is an opaque map the client owns, so every leaf here
    is whatever the browser last sent — a number, a null, a nested object. The
    composer must never raise on one.
    """
    return value.strip() if isinstance(value, str) else ""


def _rrows(value) -> list[dict]:
    """The dict rows of a builder list, ignoring anything else it holds."""
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _rstrs(value) -> list[str]:
    """The non-blank strings of a builder list."""
    return [s for s in (_rt(v) for v in value) if s] if isinstance(value, list) else []


def _rmap(value) -> dict:
    """A builder sub-object, or an empty one."""
    return value if isinstance(value, dict) else {}


def _entry(title: str, org: str, meta: str, body: str) -> list[str]:
    """One entry, in the only shapes `app/resume_pdf.py` can actually draw.

    The renderer understands `# `, `## `, `- ` and paragraphs. It has NO nested
    bullets, so an entry with a description is a bold headline paragraph
    followed by a plain one rather than a bullet with a child — which would
    render as two sibling bullets and read as two separate claims.
    """
    head = f"**{title}**"
    if org:
        head += f", {org}"
    if meta:
        head += f" ({meta})"
    return [head] + ([body] if body else []) + [""]


def _section(heading: str, body: list[str]) -> list[str]:
    """A heading and its body, or nothing at all when the body is empty.

    An empty section on a resume is a question the reader answers unkindly
    ("Publications — none"), so a section the student has not filled does not
    appear rather than appearing bare.
    """
    return [f"## {heading}", ""] + body if body else []


def _roles_block(rows: list[dict]) -> list[str]:
    """Experience and internships share one shape: title, org · sector, dates,
    location, description."""
    out: list[str] = []
    for e in rows:
        title = _rt(e.get("title"))
        if not title:
            # A row the student opened and left unnamed is not a claim. The
            # builder itself refuses to save one (its Save button is disabled
            # without a title); a row that predates that rule stays off the page.
            continue
        org = " · ".join(x for x in (_rt(e.get("org")), _rt(e.get("sector"))) if x)
        span = " — ".join(x for x in (_rt(e.get("start")), _rt(e.get("end"))) if x)
        meta = " · ".join(x for x in (span, _rt(e.get("location"))) if x)
        out += _entry(title, org, meta, _rt(e.get("description")))
    return out


def _projects_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for p in rows:
        title = _rt(p.get("title"))
        if not title:
            continue
        meta = " · ".join(
            x for x in (" · ".join(_rstrs(p.get("tech"))), _rt(p.get("link"))) if x
        )
        out += _entry(title, "", meta, _rt(p.get("description")))
    return out


def _publications_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for p in rows:
        title = _rt(p.get("title"))
        if not title:
            continue
        meta = " · ".join(x for x in (_rt(p.get("date")), _rt(p.get("coauthors"))) if x)
        out += _entry(title, _rt(p.get("publisher")), meta, _rt(p.get("doi")))
    return out


def _seminars_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for s in rows:
        title = _rt(s.get("title"))
        if not title:
            continue
        out += _entry(title, _rt(s.get("provider")), _rt(s.get("date")), "")
    return out


def _positions_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for p in rows:
        title = _rt(p.get("title"))
        if not title:
            continue
        out += _entry(title, _rt(p.get("org")), _rt(p.get("duration")), _rt(p.get("description")))
    return out


def _external_certs_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for c in rows:
        title = _rt(c.get("name"))
        if not title:
            continue
        meta = " · ".join(x for x in (_rt(c.get("year")), _rt(c.get("link"))) if x)
        out += _entry(title, _rt(c.get("provider")), meta, "")
    return out


def _references_block(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for r in rows:
        name = _rt(r.get("name"))
        if not name:
            continue
        org = ", ".join(x for x in (_rt(r.get("designation")), _rt(r.get("org"))) if x)
        reach = " · ".join(x for x in (_rt(r.get("email")), _rt(r.get("phone"))) if x)
        out += _entry(name, org, _rt(r.get("relationship")), reach)
    return out


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items] + ([""] if items else [])


def _contact_line(profile, contact: dict) -> str:
    """One line of ways to reach the student, deduplicated, office detail dropped.

    The profile row and the builder's contact section overlap (both hold an
    email, a phone and links), and a resume that prints the same address twice
    reads as carelessness. `add` is case-insensitive because "Test@x.ac.in" and
    "test@x.ac.in" are one address to a reader and two to a set.
    """
    parts: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        v = _rt(value)
        if v and v.lower() not in seen:
            seen.add(v.lower())
            parts.append(v)

    if profile is not None:
        add(profile.email)
        add(profile.phone)
    for email in _rstrs(contact.get("personal_emails")):
        add(email)
    for row in _rrows(contact.get("other_phones")):
        number = _rt(row.get("number"))
        if number:
            add(f"{_rt(row.get('code'))} {number}".strip())
    if profile is not None:
        add(profile.linkedin_url)
        add(profile.github_url)
        add(profile.portfolio_url)
    for row in _rrows(contact.get("web_links")):
        add(row.get("url"))

    # A city, never the street. See _PRIVATE_BUILDER_SECTIONS.
    address = _rmap(contact.get("current_address"))
    where = ", ".join(x for x in (_rt(address.get("city")), _rt(address.get("state"))) if x)
    add(where or (profile.city if profile is not None else ""))
    return " · ".join(parts)


#: THE ONE TEMPLATE EVERY GENERATED RESUME FOLLOWS (2026-10-13). A student's
#: resume reaches a recruiter beside thirty others from the same batch, and a
#: placement office reads them side by side; the conventional campus-placement
#: layout is what both expect, so every version the builder creates or
#: regenerates is laid out in this order and nothing else decides it. The ids
#: are stamped into `resumes.content["template"]` so a version made under a
#: later revision of this list is distinguishable from one made under this.
#:
#:   header (name, then one contact line) · Career Objective · Education (a
#:   table, current programme first) · Professional Experience · Internships ·
#:   Projects · Verified Skills · Key Expertise (self-reported) · Certifications
#:   (self-reported) · Positions of Responsibility · Achievements · Awards &
#:   Scholarships · Publications & Research · Seminars & Trainings · Activities
#:   · Languages · References · Declaration
#:
#: Education leads because a fresher's resume is read for the programme first,
#: and the AI polish is told to keep every heading in this order and is
#: discarded when it does not (`_polish_keeps_template`).
RESUME_TEMPLATE_ID = "reep-standard-v1"
RESUME_TEMPLATE_SECTIONS = (
    "Career Objective",
    "Education",
    "Professional Experience",
    "Internships",
    "Projects",
    "Verified Skills",
    "Key Expertise (self-reported)",
    "Certifications (self-reported)",
    "Positions of Responsibility",
    "Achievements",
    "Awards & Scholarships",
    "Publications & Research",
    "Seminars & Trainings",
    "Activities",
    "Languages",
    "References",
    "Declaration",
)

_QUAL_LABELS = {
    "TENTH": "Class X",
    "TWELFTH": "Class XII",
    "DIPLOMA": "Diploma",
    "UNDERGRAD": "Graduation",
    "POSTGRAD": "Post-graduation",
}


def _cell(value) -> str:
    """A table cell: a `|` in the data would split the row, so it is replaced."""
    return str(value).replace("|", "/").strip() if value is not None else ""


def _education_table(cgpa, quals, programme: dict | None) -> list[str]:
    """The Education section as one markdown table, most recent first.

    The current programme is the first row, read from the institutional spine
    (course, college, batch) and scored by the latest CGPA; the earlier
    qualifications follow newest first. A row with no programme and no CGPA is
    still printed, because "not yet assessed" is a fact a reader needs where an
    empty section is a question.
    """
    rows: list[list[str]] = []
    p = programme or {}
    course = _rt(p.get("course"))
    if course or cgpa is not None:
        rows.append(
            [
                course or "Current programme",
                _rt(p.get("college")),
                _rt(p.get("batch")) or "Pursuing",
                f"CGPA {cgpa}" if cgpa is not None else "Not yet assessed",
            ]
        )
    for q in sorted(quals, key=lambda q: q.year, reverse=True):
        pct = round(100 * q.marks / q.max_marks) if q.max_marks else 0
        level = q.level.value if hasattr(q.level, "value") else str(q.level)
        institution = ", ".join(x for x in (_rt(q.institution), _rt(q.board)) if x)
        rows.append([_QUAL_LABELS.get(level, level.title()), institution, str(q.year), f"{pct}%"])
    if not rows:
        rows.append(["Current programme", "", "Pursuing", "Not yet assessed"])
    out = ["| Qualification | Institution / Board | Year | Score |", "| --- | --- | --- | --- |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out + [""]


def _compose_resume_markdown(
    name, profile, skill_names, cgpa, quals, builder=None, programme: dict | None = None
) -> str:
    """The deterministic resume, laid out in `RESUME_TEMPLATE_SECTIONS` order.

    `builder` is `resume_profiles.data` — the fifteen-section map the Resume
    Builder writes. It is optional so the signature stays callable with the
    older five arguments, but a caller that omits it publishes a document
    missing everything the student typed, which is the bug this parameter fixed.
    `programme` is the current course / college / batch for the Education
    table's first row; absent, that row says "Current programme".
    """
    b = builder if isinstance(builder, dict) else {}
    other = _rmap(b.get("other"))

    lines = [f"# {name or 'REEP Student'}", ""]

    # The header block: everything between the name and the first `##` is
    # drawn centred under the name by `app/resume_pdf.py`, so it holds the
    # contact line and nothing else.
    contact = _contact_line(profile, _rmap(b.get("contact")))
    if contact:
        lines += [contact.replace(" · ", " | "), ""]

    # The objective the student wrote for THIS document outranks the one-line
    # career summary on their profile row: it is the more recent statement of
    # intent, and it is the paragraph the builder's own copy promises "the
    # generated resume opens with".
    objective = _rt(other.get("career_objective")) or _rt(
        profile.career_summary if profile is not None else ""
    )
    lines += _section("Career Objective", [objective, ""] if objective else [])
    lines += _section("Education", _education_table(cgpa, quals, programme))

    lines += _section("Professional Experience", _roles_block(_rrows(b.get("experience"))))
    lines += _section("Internships", _roles_block(_rrows(b.get("internship"))))
    lines += _section("Projects", _projects_block(_rrows(b.get("projects"))))

    # VERIFIED means a mentor checked the evidence. `skill_names` is already
    # filtered to those; `key_expertise` is the student's own word for what they
    # can do, so it travels under its own heading saying exactly that. One
    # undifferentiated "Skills" line would present work in review as work
    # confirmed, which is the thing the whole verification flow exists to stop.
    expertise = _rstrs(other.get("key_expertise"))
    lines += _section("Verified Skills", [", ".join(skill_names), ""] if skill_names else [])
    lines += _section(
        "Key Expertise (self-reported)", [", ".join(expertise), ""] if expertise else []
    )
    lines += _section(
        "Certifications (self-reported)", _external_certs_block(_rrows(b.get("external_certs")))
    )

    lines += _section("Positions of Responsibility", _positions_block(_rrows(b.get("por"))))
    lines += _section("Achievements", _bullets(_rstrs(other.get("achievements"))))
    lines += _section("Awards & Scholarships", _bullets(_rstrs(other.get("awards"))))
    lines += _section("Publications & Research", _publications_block(_rrows(b.get("publications"))))
    lines += _section("Seminars & Trainings", _seminars_block(_rrows(b.get("seminars"))))
    lines += _section(
        "Activities",
        _bullets(_rstrs(other.get("co_curricular")) + _rstrs(other.get("extra_curricular"))),
    )

    languages = _rstrs(_rmap(b.get("basic")).get("languages"))
    lines += _section("Languages", [", ".join(languages), ""] if languages else [])
    lines += _section("References", _references_block(_rrows(b.get("references"))))
    lines += _section(
        "Declaration",
        [
            "I hereby declare that the information furnished above is true to the best "
            "of my knowledge and belief.",
            "",
            f"**{name or 'REEP Student'}**",
            "",
        ],
    )

    # Trailing blanks are free in markdown and cost a page break in the PDF.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _headings(markdown: str) -> list[str]:
    return [ln[3:].strip() for ln in (markdown or "").splitlines() if ln.startswith("## ")]


def _table_rows(markdown: str) -> list[tuple[str, ...]]:
    """The content rows of every `| … |` table, cells trimmed, separators skipped.

    Normalised so a model that only re-pads the cells or rewrites `| --- |` as
    `|:---|` is not counted as having changed anything.
    """
    rows = []
    for ln in (markdown or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = tuple(c.strip() for c in ln.strip("|").split("|"))
        if all(c and set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def _polish_keeps_template(draft: str, polished: str) -> bool:
    """Did the model keep the standard template's headings and its Education table?

    The polish is told to, and a model that drops Education or reorders the
    sections has produced a resume that no longer follows the one template every
    other student's does — so it is discarded and the deterministic draft kept.
    The table rows are compared too: they are the student's record (programme,
    institution, year, CGPA or percentage), and a polish that rewrote a score or
    an institution would put a figure on the document that REEP does not hold.
    """
    return (
        bool(polished.strip())
        and _headings(polished) == _headings(draft)
        and _table_rows(polished) == _table_rows(draft)
    )


def _programme_for(db: Session, student_id: str) -> dict:
    """Course, college and batch for the Education table's first row."""
    inst = _institution_for(db, db.get(Student, student_id))
    return {
        "course": inst.course_name or "",
        "college": inst.college_name or "",
        "batch": inst.batch_label or "",
    }


def _compose_for_student(db: Session, student_id: str, name: str) -> str:
    """Everything one student's resume is made of, laid out in the template."""
    profile = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    # ONLY VERIFIED SKILLS REACH THE DOCUMENT. A resume is a claim made to an
    # employer, and an unverified StudentSkill is a claim still with the mentor
    # — printing the two in one undifferentiated "Skills" line presented work in
    # review as work confirmed, which is the one thing the verification flow
    # exists to prevent.
    verified_rows = db.execute(
        select(Skill.slug, Skill.name)
        .join(StudentSkill, StudentSkill.skill_id == Skill.id)
        .where(StudentSkill.student_id == student_id, StudentSkill.verified.is_(True))
    ).all()
    # The builder lets the student switch individual verified skills off when
    # tailoring to a posting. An empty/absent list means they have not curated,
    # so everything verified goes in; an explicit list is respected as written.
    resume_state = db.scalar(
        select(ResumeProfile.data).where(ResumeProfile.student_id == student_id)
    )
    included = ((resume_state or {}).get("evidence_skills") or {}).get("included") or []
    if included:
        chosen = set(included)
        skill_names = [n for slug, n in verified_rows if slug in chosen]
    else:
        skill_names = [n for _slug, n in verified_rows]
    latest = db.scalar(
        select(SemesterResult)
        .where(SemesterResult.student_id == student_id)
        .order_by(SemesterResult.semester.desc())
        .limit(1)
    )
    cgpa = latest.cgpa if latest else None
    quals = db.scalars(
        select(AcademicQualification)
        .where(AcademicQualification.student_id == student_id)
        .order_by(AcademicQualification.year)
    ).all()
    return _compose_resume_markdown(
        name, profile, skill_names, cgpa, quals, resume_state, _programme_for(db, student_id)
    )


def _polish(db: Session, markdown: str) -> tuple[str, str, str | None, bool, str | None]:
    """The optional AI polish, behind rule 1's gate, keeping the template.

    Returns (markdown, generated_by, model, used_ai, note). Commits first: the
    SELECTs that composed the draft opened a transaction that pins one of the
    pool's connections, and complete_chat can legally take LLM_TIMEOUT_MS — the
    2026-08 audit's arithmetic: ~15 concurrent generations against a slow
    provider held the ENTIRE pool and 500'd every other request in the app.
    """
    db.commit()
    cfg = llm_config()
    if cfg is not None and student_data_egress_allowed(cfg.base_url):
        prompt = (
            "Polish the wording of this one-page markdown resume for an MBA student. "
            "Keep every fact; invent nothing. Keep the '# ' name line, every '## ' "
            "heading with its exact text and order, and the Education table rows "
            "unchanged; rewrite only the sentences and bullets under them.\n\n" + markdown
        )
        try:
            polished = complete_chat(
                [
                    {"role": "system", "content": "You are a concise resume writer."},
                    {"role": "user", "content": prompt},
                ],
                carries_student_data=True,
                # Was 1500, which was a one-page budget for a five-line draft.
                # Now that the document carries every builder section, a full
                # profile can exceed that — and a truncated polish silently
                # returns LESS than the deterministic draft the student would
                # have got for free.
                max_tokens=3000,
            )
        except Exception as exc:  # keep the deterministic draft on any failure
            return markdown, "fallback", None, False, f"AI polish failed ({exc}); kept the deterministic draft."
        if not _polish_keeps_template(markdown, polished):
            return (
                markdown,
                "fallback",
                None,
                False,
                "AI polish changed the standard resume layout or the Education table, "
                "so the deterministic draft was kept.",
            )
        return polished, cfg.provider, cfg.model, True, None
    if cfg is None:
        # Two different facts used to share one sentence: "no model configured"
        # was reported as "the configured model runs off this machine", which
        # sends the reader looking for a setting that is not the reason.
        return (
            markdown,
            "fallback",
            None,
            False,
            "No language model is configured, so the resume was composed on this "
            "machine from your saved REEP records.",
        )
    return (
        markdown,
        "fallback",
        None,
        False,
        "AI generation skipped: the resume carries student data and the configured "
        "model runs off this machine. Composed deterministically. Set "
        "LLM_ALLOW_REMOTE_STUDENT_DATA=true or use a local model to enable AI.",
    )


class ResumeGenerateIn(BaseModel):
    title: str | None = None
    target_role: str | None = None


@router.post("/resume/generate", dependencies=[Depends(llm_rate_limited)])
def generate_resume(
    body: ResumeGenerateIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    """CREATE: compose a new resume version from the student's REEP data, in the
    standard template. The prompt carries student PII, so a model is used ONLY
    when it is local or explicitly allowed; otherwise it composes
    deterministically and says so (the AGENTS.md egress rule)."""
    student_id = _require_student(session)
    # Gated here and not on GET /student/resume or the PDF: a switched-off
    # Resume Builder stops the student MAKING a new document, and does not take
    # away the ones they already made. See require_feature's docstring for the
    # rule; it is the same one that keeps DELETE /uploads/{id} open.
    require_feature(db, student_id, "student.resume")
    markdown = _compose_for_student(db, student_id, session.get("name", ""))
    markdown, generated_by, model, used_ai, note = _polish(db, markdown)

    version = (
        db.scalar(select(func.max(Resume.version)).where(Resume.student_id == student_id)) or 0
    ) + 1
    resume = Resume(
        student_id=student_id,
        version=version,
        title=_clean_title(body.title) or "REEP Resume",
        target_role=_rt(body.target_role) or None,
        status=ResumeStatus.GENERATED,
        content={"template": RESUME_TEMPLATE_ID},
        markdown=markdown,
        generated_by=generated_by,
        model=model,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return {
        "id": resume.id,
        "version": version,
        "template": RESUME_TEMPLATE_ID,
        "generated_by": generated_by,
        "model": model,
        "used_ai": used_ai,
        "note": note,
        "markdown": markdown,
    }


class ResumeOut(BaseModel):
    id: str
    version: int
    title: str
    status: str
    generated_by: str
    model: str | None
    # When this version was composed — the "Updated …" line on the Export &
    # share step's version list. Additive; nothing else reads it.
    created_at: datetime | None = None
    # Additive (2026-10-13): what the update and delete controls need.
    updated_at: datetime | None = None
    target_role: str | None = None
    #: The layout the version was composed in; None for a version made before
    #: the standard template existed, which "Regenerate" brings onto it.
    template: str | None = None


class ResumeDetailOut(ResumeOut):
    markdown: str


def _resume_out(r: Resume) -> ResumeOut:
    return ResumeOut(
        id=r.id,
        version=r.version,
        title=r.title,
        status=r.status.value,
        generated_by=r.generated_by,
        model=r.model,
        created_at=r.created_at,
        updated_at=r.updated_at,
        target_role=r.target_role,
        template=(r.content or {}).get("template") if isinstance(r.content, dict) else None,
    )


@router.get("/resume", response_model=list[ResumeOut])
def list_resumes(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[ResumeOut]:
    """READ: the student's own versions, newest first."""
    student_id = _require_student(session)
    rows = db.scalars(
        select(Resume).where(Resume.student_id == student_id).order_by(Resume.created_at.desc())
    ).all()
    return [_resume_out(r) for r in rows]


#: A title is a label on the student's own list, not a document field; a cap
#: keeps a pasted paragraph from becoming one.
RESUME_TITLE_MAX = 120


def _clean_title(value) -> str:
    return _rt(value)[:RESUME_TITLE_MAX]


def _own_resume(db: Session, student_id: str, resume_id: str) -> Resume:
    """The caller's own resume, or the same 404 whether it is absent or another's."""
    resume = db.get(Resume, resume_id)
    if resume is None or resume.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found.")
    return resume


def _refuse_if_finalised(resume: Resume, act: str) -> None:
    """A FINALISED version is the copy the student has decided to send; it is
    not rewritten or deleted underneath them. Un-finalising is one PATCH."""
    if resume.status == ResumeStatus.FINALISED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This resume is finalised. Mark it as a draft again before you {act} it.",
        )


@router.get("/resume/{resume_id}", response_model=ResumeDetailOut)
def get_resume(
    resume_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ResumeDetailOut:
    """READ one version, with its document."""
    student_id = _require_student(session)
    r = _own_resume(db, student_id, resume_id)
    return ResumeDetailOut(**_resume_out(r).model_dump(), markdown=r.markdown or "")


class ResumeUpdateIn(BaseModel):
    """What a student may change on a saved version without recomposing it.

    The DOCUMENT is not here on purpose: its "Verified Skills" heading is a
    claim a mentor checked, and a free-text edit of the markdown would let any
    line be printed under it. The way to change what a version says is to edit
    the builder and press Regenerate, which composes it again from the record.
    """

    title: str | None = None
    target_role: str | None = None
    #: GENERATED or FINALISED. DRAFT is the model's default for a row nothing
    #: has composed yet, which a saved version never is.
    status: str | None = None


@router.patch("/resume/{resume_id}", response_model=ResumeOut)
def update_resume(
    resume_id: str,
    body: ResumeUpdateIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ResumeOut:
    """UPDATE a version's label, target role or finalised state."""
    student_id = _require_student(session)
    r = _own_resume(db, student_id, resume_id)
    if body.status is not None:
        if body.status not in (ResumeStatus.GENERATED.value, ResumeStatus.FINALISED.value):
            raise HTTPException(
                status_code=422, detail="Status must be GENERATED or FINALISED."
            )
        r.status = ResumeStatus(body.status)
    if body.title is not None:
        title = _clean_title(body.title)
        if not title:
            raise HTTPException(status_code=422, detail="A resume needs a name.")
        r.title = title
    if body.target_role is not None:
        r.target_role = _rt(body.target_role)[:RESUME_TITLE_MAX] or None
    db.commit()
    db.refresh(r)
    return _resume_out(r)


@router.post(
    "/resume/{resume_id}/regenerate",
    response_model=ResumeDetailOut,
    dependencies=[Depends(llm_rate_limited)],
)
def regenerate_resume(
    resume_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ResumeDetailOut:
    """UPDATE a version's document: compose it again, in the standard template,
    from what the builder and the record hold NOW. Same id, same version number,
    so the student's list keeps its shape; a version made before the template
    existed is brought onto it this way."""
    student_id = _require_student(session)
    require_feature(db, student_id, "student.resume")
    r = _own_resume(db, student_id, resume_id)
    _refuse_if_finalised(r, "regenerate")
    resume_pk = r.id
    markdown = _compose_for_student(db, student_id, session.get("name", ""))
    markdown, generated_by, model, _used_ai, _note = _polish(db, markdown)
    # Re-read and re-check: the model call can take LLM_TIMEOUT_MS, and the
    # student may have finalised this version from another tab meanwhile.
    r = _own_resume(db, student_id, resume_pk)
    _refuse_if_finalised(r, "regenerate")
    r.markdown = markdown
    r.generated_by = generated_by
    r.model = model
    r.status = ResumeStatus.GENERATED
    r.content = {**(r.content if isinstance(r.content, dict) else {}), "template": RESUME_TEMPLATE_ID}
    db.commit()
    db.refresh(r)
    return ResumeDetailOut(**_resume_out(r).model_dump(), markdown=r.markdown or "")


@router.delete("/resume/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """DELETE one of the student's own versions. The builder's saved sections
    are untouched — a version is a composed copy, and the record it was composed
    from stays — so a deleted version can always be generated again. Not
    feature-gated, for the same reason DELETE /uploads/{id} is not."""
    student_id = _require_student(session)
    r = _own_resume(db, student_id, resume_id)
    _refuse_if_finalised(r, "delete")
    db.delete(r)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _evidence_proofs(db: Session, student_id: str) -> list[EvidenceProof]:
    """The files behind the verified skills this resume actually claims.

    Only VERIFIED and INCLUDED skills, in that order of authority:

      * verified, because an appendix is the evidence half of the document and a
        claim still with a mentor is not evidence of anything yet;
      * included, because the student curates which verified skills this copy
        presents, and an appendix that carries proofs the resume never mentions
        hands an employer answers to questions nobody asked.

    A skill whose upload row is gone, or whose bytes are missing from the store,
    is skipped rather than raising: the export of a resume must not fail because
    one certificate was deleted.
    """
    state = db.scalar(select(ResumeProfile.data).where(ResumeProfile.student_id == student_id))
    included = {s for s in (((state or {}).get("evidence_skills") or {}).get("included") or []) if s}
    if not included:
        return []

    rows = db.execute(
        select(Skill.slug, Skill.name, StudentSkill.evidence_upload_id, Skill.id)
        .join(StudentSkill, StudentSkill.skill_id == Skill.id)
        .where(StudentSkill.student_id == student_id, StudentSkill.verified.is_(True))
        .order_by(Skill.name)
    ).all()

    proofs: list[EvidenceProof] = []
    for slug, name, upload_id, skill_id in rows:
        if slug not in included:
            continue
        # The skill row points at its evidence; where it does not, the claim the
        # mentor reviewed does. Same fallback the student's own screen uses.
        if not upload_id:
            upload_id = db.scalar(
                select(SkillClaim.upload_id)
                .where(SkillClaim.student_id == student_id, SkillClaim.skill_id == skill_id)
                .order_by(SkillClaim.created_at.desc())
                .limit(1)
            )
        if not upload_id:
            continue
        upload = db.get(Upload, upload_id)
        if upload is None or upload.student_id != student_id:
            continue
        try:
            content = read_bytes(upload.stored_name)
        except FileNotFoundError:
            continue
        proofs.append(
            EvidenceProof(
                skill=name,
                title=upload.title or "",
                original_name=upload.original_name or "",
                mime_type=upload.mime_type or "",
                content=content,
                verified_on=upload.reviewed_at.strftime("%d %b %Y") if upload.reviewed_at else None,
            )
        )
    return proofs


@router.get("/resume/{resume_id}/pdf")
def resume_pdf(
    resume_id: str,
    appendix: bool = False,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Render one of the student's own resumes to a PDF. Local render (no model,
    no network), so the egress gate does not apply — but ownership does: a
    student can only export their own resume.

    `appendix=true` binds the proof files of the included verified skills onto
    the end. The Export step's "Include an evidence appendix with proof links"
    checkbox used to change nothing at all — see app/resume_pdf.py — and a
    student who ticked it sent a document without the certificates they believed
    were attached. Links could never have served: an upload URL needs the
    student's own cookie, so a recruiter following one reaches a login page.
    """
    student_id = _require_student(session)
    resume = _own_resume(db, student_id, resume_id)
    pdf = render_resume_pdf(resume.markdown or "", fallback_title=resume.title or "REEP Resume")
    if appendix:
        pdf = append_evidence(pdf, _evidence_proofs(db, student_id))
    filename = f"resume-v{resume.version}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


class CourseOut(BaseModel):
    code: str
    name: str
    stage: str
    dimension: str
    semester: int
    status: str
    teaching_hours_attended: float
    self_learning_hours_logged: float
    lectures_attended: int
    lectures_total: int
    lecture_percent: float
    # --- Progress-plan fields (rule-based; no LLM) ---
    progress_pct: float
    next_task: str
    unlocks: str


def _prettify_stage(stage_value: str) -> str:
    """EXCEL_ADVANCED -> 'Excel Advanced'."""
    return " ".join(word.capitalize() for word in stage_value.split("_"))


def _course_next_task(status: str, lectures_attended: int, lectures_total: int) -> str:
    """The single next action for a course, derived from lecture progress."""
    if status == "COMPLETED":
        return "Completed"
    if lectures_attended < lectures_total:
        return f"Attend lecture {lectures_attended + 1} of {lectures_total}"
    return "Finish the coursework / assessment"


@router.get("/courses", response_model=list[CourseOut])
def my_courses(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[CourseOut]:
    student_id = _require_student(session)
    rows = db.execute(
        select(Enrollment, Course)
        .join(Course, Enrollment.course_code == Course.code)
        .where(Enrollment.student_id == student_id)
        .order_by(Course.semester, Course.code)
    ).all()
    out: list[CourseOut] = []
    for enr, course in rows:
        lecture_percent = (
            round(100 * enr.lectures_attended / enr.lectures_total, 1)
            if enr.lectures_total
            else 0.0
        )
        out.append(
            CourseOut(
                code=course.code,
                name=course.name,
                stage=course.stage.value,
                dimension=course.dimension.value,
                semester=course.semester,
                status=enr.status.value,
                teaching_hours_attended=enr.teaching_hours_attended,
                self_learning_hours_logged=enr.self_learning_hours_logged,
                lectures_attended=enr.lectures_attended,
                lectures_total=enr.lectures_total,
                lecture_percent=lecture_percent,
                progress_pct=lecture_percent,
                next_task=_course_next_task(
                    enr.status.value, enr.lectures_attended, enr.lectures_total
                ),
                unlocks=f"Progresses your {_prettify_stage(course.stage.value)} stage",
            )
        )
    return out


class CheckInIn(BaseModel):
    course_code: str
    module: str
    activity: str = "ONLINE_COURSE"
    mode: str = "SUPERVISED_LAB"


@router.post("/checkin")
def check_in(
    body: CheckInIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    """Open a focus session (self-reported). Close it with /student/checkout/{id}."""
    student_id = _require_student(session)
    try:
        activity = ActivityType(body.activity)
        mode = LearningMode(body.mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid activity or mode."
        )
    # `course_code` is a foreign key to courses.code; an unknown one used to
    # reach the INSERT and come back as a 500 (DEF-003).
    if db.get(Course, body.course_code) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"There is no course with the code {body.course_code!r}.",
        )
    ls = LabSession(
        student_id=student_id,
        course_code=body.course_code,
        module=body.module,
        activity=activity,
        mode=mode,
        source=CheckInSource.SELF_REPORTED,
        check_in_at=datetime.now(timezone.utc),
    )
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return {"id": ls.id, "check_in_at": ls.check_in_at.isoformat(), "open": True}


@router.post("/checkout/{session_id}")
def check_out(
    session_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    student_id = _require_student(session)
    ls = db.get(LabSession, session_id)
    if ls is None or ls.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if ls.check_out_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session already closed.")
    now = datetime.now(timezone.utc)
    ls.check_out_at = now
    ls.duration_min = max(0, int((now - ls.check_in_at).total_seconds() // 60))
    db.commit()
    return {"id": ls.id, "duration_min": ls.duration_min, "open": False}


class FocusSessionOut(BaseModel):
    id: str
    course_code: str
    module: str
    activity: str
    mode: str
    check_in_at: datetime
    check_out_at: datetime | None
    duration_min: int | None
    mentor_confirmed: bool


@router.get("/focus", response_model=list[FocusSessionOut])
def my_focus(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[FocusSessionOut]:
    student_id = _require_student(session)
    rows = db.scalars(
        select(LabSession)
        .where(LabSession.student_id == student_id)
        .order_by(LabSession.check_in_at.desc())
    ).all()
    return [
        FocusSessionOut(
            id=ls.id,
            course_code=ls.course_code,
            module=ls.module,
            activity=ls.activity.value,
            mode=ls.mode.value,
            check_in_at=ls.check_in_at,
            check_out_at=ls.check_out_at,
            duration_min=ls.duration_min,
            mentor_confirmed=ls.mentor_confirmed,
        )
        for ls in rows
    ]


class UploadRowOut(BaseModel):
    id: str
    kind: str
    cert_code: str | None
    title: str
    original_name: str
    mime_type: str
    size_bytes: int
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    uploaded_at: datetime


@router.get("/uploads", response_model=list[UploadRowOut])
def my_uploads(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[UploadRowOut]:
    """A student's own submitted documents and their review state."""
    student_id = _require_student(session)
    require_feature(db, student_id, "student.uploads")
    rows = db.scalars(
        select(Upload)
        .where(Upload.student_id == student_id)
        .order_by(Upload.uploaded_at.desc())
    ).all()
    return [_upload_row(u) for u in rows]


def _upload_row(u: Upload) -> "UploadRowOut":
    return UploadRowOut(
        id=u.id,
        kind=u.kind.value,
        cert_code=u.cert_code,
        title=u.title,
        original_name=u.original_name,
        mime_type=u.mime_type,
        size_bytes=u.size_bytes,
        status=u.status.value,
        review_note=u.review_note,
        reviewed_at=u.reviewed_at,
        uploaded_at=u.uploaded_at,
    )


@router.post("/uploads", response_model=UploadRowOut, status_code=status.HTTP_201_CREATED)
def create_upload(
    file: UploadFile = File(...),
    kind: str = Form("DOCUMENT"),
    title: str = Form(""),
    cert_code: str | None = Form(None),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> "UploadRowOut":
    """Upload a document (multipart). The type is decided by magic bytes, not the
    client — PDF/PNG/JPEG only, 10 MB max — and the row starts PENDING_REVIEW.

    Sync `def` ON PURPOSE (2026-08 audit): this used to be the one async
    endpoint in the file, which put its sync DB queries and a 10 MB
    `write_bytes` directly on the event loop — the loop every live interview's
    audio shares. A deadline-week upload burst produced 100-300 ms loop stalls
    (audible glitches in every running interview), and under pool contention
    the sync checkout parked the loop for the full pool timeout. In the
    threadpool, the only thing this endpoint can stall is itself. The one
    await it needed, `file.read()`, is `file.file.read()` here — same bytes,
    same SpooledTemporaryFile, no coroutine.
    """
    student_id = _require_student(session)
    require_feature(db, student_id, "student.uploads")
    try:
        upload_kind = UploadKind(kind)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown upload kind."
        )

    # Quota before the body is buffered. document_store.MAX_BYTES caps ONE file at
    # 10 MB and nothing capped how many, so any authenticated student could fill
    # the uploads volume 10 MB at a time — after which every other student's
    # upload fails on a machine whose health checks all pass. The count is
    # checked first precisely so an over-quota student never gets their megabytes
    # read into this process's memory at all.
    used_count, used_bytes = db.execute(
        select(func.count(Upload.id), func.coalesce(func.sum(Upload.size_bytes), 0)).where(
            Upload.student_id == student_id
        )
    ).one()
    # 409 for a full shelf, 413 for the byte allowance: the file is fine either
    # way, so neither is 422. Deleting an upload (DELETE /student/uploads/{id})
    # removes the bytes and the row, so the student can act on this themselves —
    # the messages say so, or the limit reads as a dead end.
    quota = VolumeQuota(
        max_files=MAX_UPLOADS_PER_STUDENT,
        max_bytes=MAX_UPLOAD_BYTES_PER_STUDENT,
        used_files=used_count,
        used_bytes=used_bytes,
        noun="file",
    )
    try:
        quota.check_slot()
    except QuotaRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # read(MAX+1), never read(): the per-file cap is enforced by save_bytes on
    # len(content), so reading one byte past it is enough to trip the refusal —
    # while an unbounded read loads a body only nginx's client_max_body_size
    # bounds into RAM, and that bound does not exist when uvicorn is exposed
    # directly (the documented dev setup, or a different ingress).
    content = file.file.read(MAX_BYTES + 1)
    try:
        stored_name, mime, size = save_and_record(
            db,
            content,
            quota=quota,
            kind=DocumentOwnerKind.STUDENT_UPLOAD,
            owner_id=student_id,
            original_name=file.filename or "upload",
            title=title.strip() or (file.filename or "Upload"),
        )
    except QuotaRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except UploadRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    upload = Upload(
        student_id=student_id,
        kind=upload_kind,
        cert_code=cert_code or None,
        title=title.strip() or (file.filename or "Upload"),
        original_name=file.filename or stored_name,
        stored_name=stored_name,
        mime_type=mime,
        size_bytes=size,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return _upload_row(upload)


@router.get("/uploads/{upload_id}/file")
def download_upload(
    upload_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Stream one of the student's own uploaded files back."""
    student_id = _require_student(session)
    upload = db.get(Upload, upload_id)
    if upload is None or upload.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    try:
        content = read_bytes(upload.stored_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing."
        )
    return Response(
        content=content,
        media_type=upload.mime_type,
        # RFC 6266 via document_store.content_disposition -- interpolating the student's own
        # filename here raises UnicodeEncodeError inside Response.__init__ for any name
        # outside latin-1 (Kannada, Hindi, an emoji), 500-ing the download of a file that
        # uploaded perfectly. See document_store.content_disposition for the full reasoning.
        headers={"Content-Disposition": content_disposition(upload.original_name)},
    )


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(
    upload_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Delete one of the student's own uploads — the stored bytes then the row.
    A student can only delete their own upload."""
    student_id = _require_student(session)
    upload = db.get(Upload, upload_id)
    if upload is None or upload.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found.")
    # RELEASED BEFORE THE BYTES GO, NOT AFTER, AND THE ORDER IS THE WHOLE POINT.
    # `release` runs a SELECT against `archived_documents`. Between an
    # irreversible `unlink` and the commit, ANY failure of that query -- a lock
    # timeout, a connection blip, or the table not existing yet because
    # deploy.yml's `run_migrations` was left off -- rolls the transaction back
    # with the file already destroyed, leaving a row whose download 404s. Ahead
    # of the unlink, the same failure destroys nothing: the request 500s and
    # the person retries.
    #
    # Deliberately NOT a `try/except OperationalError` around the call, which
    # was the suggested fix: swallowing it would drop the manifest row silently
    # and that row is the only thing that can ever name the archived bytes.
    # The failure must stay loud. Moving it earlier makes it harmless as well.
    #
    # The bytes still leave the volume and the row still goes -- the student's
    # screen, their quota and the store are all correct a second later. The
    # file itself is already in the permanent archive and stays there; this
    # marks the manifest row so a restore can tell "the student removed this"
    # from "this is still on their shelf".
    release(
        db,
        upload.stored_name,
        reason="student deleted upload",
        # The live row is still here, so a file stored before the manifest
        # existed is recorded from exact data rather than losing its name.
        facts=DocumentFacts(
            kind=DocumentOwnerKind.STUDENT_UPLOAD,
            owner_id=upload.student_id,
            original_name=upload.original_name,
            title=upload.title,
            mime_type=upload.mime_type,
            size_bytes=upload.size_bytes,
            recorded_at=upload.uploaded_at,
        ),
    )
    document_store_delete(upload.stored_name)
    db.delete(upload)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SkillClaimOut(BaseModel):
    id: str
    skill_id: str
    skill_name: str
    # NULL once the student has deleted the certificate this was filed against.
    # The claim, its level and the mentor's verdict survive; only the pointer to
    # the file goes. See models/skill.py's comment on the FK.
    upload_id: str | None
    claimed_level: int
    status: str
    student_note: str | None
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime


class SkillClaimIn(BaseModel):
    skill_id: str
    upload_id: str
    # The claim form no longer asks the student to grade themselves — it asks for
    # the certificate and the badge it proves, and the mentor sets the level when
    # they grant it. The default stands in for the claim's "working" level.
    claimed_level: int = Field(default=3, ge=1, le=5)
    student_note: str | None = None


@router.get("/skill-claims", response_model=list[SkillClaimOut])
def my_skill_claims(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[SkillClaimOut]:
    """A student's own skill claims and their review state."""
    student_id = _require_student(session)
    rows = db.execute(
        select(SkillClaim, Skill.name)
        .join(Skill, SkillClaim.skill_id == Skill.id)
        .where(SkillClaim.student_id == student_id)
        .order_by(SkillClaim.created_at.desc())
    ).all()
    return [
        SkillClaimOut(
            id=sc.id,
            skill_id=sc.skill_id,
            skill_name=name,
            upload_id=sc.upload_id,
            claimed_level=sc.claimed_level,
            status=sc.status.value,
            student_note=sc.student_note,
            review_note=sc.review_note,
            reviewed_at=sc.reviewed_at,
            created_at=sc.created_at,
        )
        for sc, name in rows
    ]


@router.post("/skill-claims", response_model=SkillClaimOut, status_code=status.HTTP_201_CREATED)
def create_skill_claim(
    body: SkillClaimIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> SkillClaimOut:
    """Claim a skill level, backed by one of your own uploads, for mentor review."""
    student_id = _require_student(session)
    skill = db.get(Skill, body.skill_id)
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")
    upload = db.get(Upload, body.upload_id)
    if upload is None or upload.student_id != student_id:
        # Never let a claim point at someone else's upload.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evidence upload not found."
        )
    claim = SkillClaim(
        student_id=student_id,
        skill_id=body.skill_id,
        upload_id=body.upload_id,
        claimed_level=body.claimed_level,
        student_note=body.student_note,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return SkillClaimOut(
        id=claim.id,
        skill_id=claim.skill_id,
        skill_name=skill.name,
        upload_id=claim.upload_id,
        claimed_level=claim.claimed_level,
        status=claim.status.value,
        student_note=claim.student_note,
        review_note=claim.review_note,
        reviewed_at=claim.reviewed_at,
        created_at=claim.created_at,
    )


# The resume-builder sections that ResumeProfile owns (the ones with no other
# home). Education / certifications / attachments are shown by the builder too
# but come from their own endpoints, so they don't count here.
_RESUME_SECTIONS = [
    "basic", "contact", "family", "experience", "internship", "projects",
    "publications", "seminars", "por", "other", "references", "policy",
]


#: Leaf keys whose value is FURNITURE THE FORM SUPPLIES, not content a student
#: entered. The contact section seeds `country: "India"` and a `+91` dial `code`
#: into every address and phone row the moment one is added, so pressing "Add
#: another number" and typing nothing used to mark the whole section filled.
#:
#: Observed: a fresh profile went from 8% to 17% — a twelfth of the bar — for one
#: empty phone row and one unticked checkbox, with nothing typed. The sidebar
#: advertises 70% as the point where the document gets materially stronger, and
#: a percentage reachable by clicking Add is not a measure of anything.
_STRUCTURAL_LEAF_KEYS = {"country", "code"}


def _section_filled(value, *, key: str | None = None) -> bool:
    """Does this section hold content the student actually put there?

    Only a non-blank string counts. Booleans and numbers are STRUCTURE — a
    checkbox has a value whether or not anyone has looked at it, and
    `permanent_same: false` says nothing about whether an address was written.
    The old rule returned True for any list with a row in it and for any
    non-empty leaf of any type, so an empty row counted the same as a filled one.
    """
    if isinstance(value, dict):
        return any(_section_filled(v, key=k) for k, v in value.items())
    if isinstance(value, list):
        return any(_section_filled(v) for v in value)
    if isinstance(value, str):
        return bool(value.strip()) and key not in _STRUCTURAL_LEAF_KEYS
    return False


def _resume_completeness(data: dict) -> int:
    if not data:
        return 0
    filled = sum(1 for k in _RESUME_SECTIONS if _section_filled(data.get(k)))
    return round(100 * filled / len(_RESUME_SECTIONS))


class ResumeProfileIn(BaseModel):
    data: dict = Field(default_factory=dict)


class ResumeProfileOut(BaseModel):
    data: dict
    completeness: int
    updated_at: datetime | None


@router.get("/resume-profile", response_model=ResumeProfileOut)
def get_resume_profile(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> ResumeProfileOut:
    """The student's saved resume-builder state (the free-form sections)."""
    student_id = _require_student(session)
    row = db.scalar(select(ResumeProfile).where(ResumeProfile.student_id == student_id))
    data = row.data if row else {}
    return ResumeProfileOut(
        data=data,
        completeness=_resume_completeness(data),
        updated_at=row.updated_at if row else None,
    )


@router.put("/resume-profile", response_model=ResumeProfileOut)
def put_resume_profile(
    body: ResumeProfileIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ResumeProfileOut:
    """Upsert the whole builder state for the caller (one row per student)."""
    student_id = _require_student(session)
    row = db.scalar(select(ResumeProfile).where(ResumeProfile.student_id == student_id))
    if row is None:
        row = ResumeProfile(student_id=student_id, data=body.data)
        db.add(row)
    else:
        row.data = body.data
    db.commit()
    db.refresh(row)
    return ResumeProfileOut(
        data=row.data,
        completeness=_resume_completeness(row.data),
        updated_at=row.updated_at,
    )


# --- Leaderboards --------------------------------------------------------------
#
# THE BOARD IS THE BATCH (2026-09-22). A student who registered naming a batch,
# was approved and seated in it, opened Leaderboards and saw none of their
# batch mates. Two things did that, and both are fixed here rather than in the
# client.
#
# ONE: a board listed only the students who already HELD something on it (the
# 2026-09-17 rule, kept — a "Rank 2 of 30" over a zero is a lie), so a fresh
# batch with no verified skills and no results yet drew "No ranking yet" for
# everybody, and nothing on the screen said the classmates were there at all.
# The board now carries the batch mates who are NOT yet ranked as a second,
# unnumbered list, so the student sees their batch and sees who is ranked.
#
# TWO: a student with `students.cohort_id` NULL — provisioned before the form
# copied the requested batch onto the row, or granted with `--department-id`
# alone — was "ranked" among every other NULL-cohort student on the
# deployment, which is not a batch and never was. The scope is resolved by
# `_leaderboard_scope`: the batch where there is one, else the department
# (`students.department_id`, the same second pointer `ancestry_of_student`
# reads), else nobody — and the response SAYS which, in words the screen
# prints, because "you are not seated in a batch yet" and "your batch has no
# results yet" are different sentences with different fixes.
#
# And the boards rank ONE component each. `overall` is the rank across all of
# them: skills, VTU results, streak and mocks, each worth up to
# OVERALL_POINTS_PER_COMPONENT scaled against the best in the batch, summed to
# a score out of 100. Scaled rather than summed raw because a CGPA is out of
# 10 and a streak is out of however many days the term has run — added
# unscaled, the streak would BE the leaderboard.

_BOARDS = ("overall", "certificates", "skills", "vtu", "streak", "mocks")

#: The single-component boards `overall` is built from. `certificates` is not
#: one of them for the reason the client dropped its tab: a certificate is
#: evidence for a skill, and the skills board already ranks what those
#: certificates were verified into — counting both would count it twice.
OVERALL_COMPONENTS: tuple[str, ...] = ("skills", "vtu", "streak", "mocks")
OVERALL_POINTS_PER_COMPONENT = 25


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def overall_points(components: dict[str, dict[str, float]]) -> dict[str, int]:
    """student_id -> points out of 100, from `{board: {student_id: value}}`.

    Each component is worth OVERALL_POINTS_PER_COMPONENT, scaled by the
    student's value against the BEST value in the batch on that component —
    the batch's top CGPA earns the full 25, a CGPA of 8 against a best of 9
    earns 22.2 — and the components are added. Rounded half up to a whole
    point, and RANKED ON THE ROUNDED NUMBER, so two students the label calls
    "72 pts" are level rather than separated by a decimal the screen hides.
    A student on no component at all is absent, like on every other board.

    Pure, so the arithmetic is pinned without a database.
    """
    totals: dict[str, float] = defaultdict(float)
    for values in components.values():
        best = max(values.values(), default=0.0)
        if best <= 0:
            continue
        for sid, value in values.items():
            if value > 0:
                totals[sid] += OVERALL_POINTS_PER_COMPONENT * value / best
    return {sid: int(total + 0.5) for sid, total in totals.items() if total > 0}


def _board_values(db: Session, board: str, roster: list[tuple[str, str]]) -> dict[str, tuple[float, str]]:
    """student_id -> (value, label) for the chosen board, over the cohort roster
    (list of (student_id, user_id)).

    A STUDENT WITH NOTHING RECORDED ON A BOARD IS ABSENT FROM THE RESULT, and
    `_ranked_board` reads that absence as "not ranked" (2026-09-17). Every
    roster student used to come back scoring 0 and was then ranked with the
    rest, so a batch of thirty with one verified skill between them drew
    "Rank 2 of 30 -- top 7%" under a student holding nothing, in whatever order
    Postgres returned the roster that minute. On the VTU board the same rule
    printed "CGPA 0.00" against a student whose results were never recorded --
    the confident zero the English-baseline and attendance screens already
    refuse to draw. The client has carried a "You're not ranked here yet" state
    for exactly this since the UX audit, and nothing could reach it.
    """
    sids = [sid for sid, _ in roster]
    uid_by_sid = {sid: uid for sid, uid in roster}

    if board == "overall":
        components = {
            component: {sid: value for sid, (value, _label) in _board_values(db, component, roster).items()}
            for component in OVERALL_COMPONENTS
        }
        return {sid: (float(pts), f"{pts} pts") for sid, pts in overall_points(components).items()}

    if board == "certificates":
        rows = db.execute(
            select(CertificationProgress.student_id, func.count())
            .where(
                CertificationProgress.student_id.in_(sids),
                CertificationProgress.status == ProgressStatus.COMPLETED,
            )
            .group_by(CertificationProgress.student_id)
        ).all()
        return {sid: (float(n), _plural(n, "cert")) for sid, n in rows if n}

    if board == "skills":
        # WHAT THE SKILLING SCREEN VERIFIES, NOT `student_skills` (2026-09-17).
        # `/student/skilling` files a certificate as badge evidence, the mentor
        # APPROVES it on `/mentor/verifications`, and `_award` mints the EARNED
        # `student_badges` row that screen draws as "Verified and acquired" and
        # counts as "N skills currently illuminated". This board counted rows of
        # `student_skills` instead -- a table written only by the legacy
        # skill-claim review (`/mentor/skill-claims/{id}/review`, which no
        # client has posted to since the Skilling screen replaced the per-skill
        # claim form) and by the dev seed -- and counted them verified or not.
        # So on a live deployment a student whose skills the mentor had just
        # verified saw "0 skills" beside classmates showing the same, and the
        # office reported the ranking as never done. One record, one count: the
        # board agrees with the screen the student verifies on, which is B6.3's
        # rule for the mocks board below.
        rows = db.execute(
            select(StudentBadge.student_id, func.count())
            .where(
                StudentBadge.student_id.in_(sids),
                StudentBadge.status == StudentBadgeStatus.EARNED,
            )
            .group_by(StudentBadge.student_id)
        ).all()
        return {sid: (float(n), _plural(n, "skill")) for sid, n in rows if n}

    if board == "mocks":
        # B6.3 — BOTH SOURCES, and in the same commit as `my_mocks`. This board
        # and the Home chart answer the same question in the same word
        # ("mocks"), so a board counting only the staff-logged rehearsals would
        # tell a student they had sat 2 while their own home screen showed 9,
        # with nothing on either screen to explain the gap. Completed interviews
        # only, which is exactly what `my_mocks` charts: an interview closed
        # before its verdict is `abandoned`, has no score, and counts nowhere.
        counts: dict[str, int] = {}
        for sid, n in db.execute(
            select(MockAttempt.student_id, func.count())
            .where(MockAttempt.student_id.in_(sids))
            .group_by(MockAttempt.student_id)
        ).all():
            counts[sid] = counts.get(sid, 0) + n
        for sid, n in db.execute(
            select(InterviewScoreSummary.student_id, func.count())
            .where(
                InterviewScoreSummary.student_id.in_(sids),
                InterviewScoreSummary.status == "completed",
            )
            .group_by(InterviewScoreSummary.student_id)
        ).all():
            counts[sid] = counts.get(sid, 0) + n
        return {sid: (float(n), _plural(n, "mock")) for sid, n in counts.items() if n}

    if board == "vtu":
        # Latest recorded CGPA per student, decided by Postgres. This used to
        # fetch EVERY SemesterResult row for the cohort and keep the first per
        # student in Python — ~30,000 hydrated rows per call at a 5,000-student
        # cohort (2026-08 audit). DISTINCT ON with the matching ORDER BY is the
        # same "first row per student is the highest semester" idea, executed
        # where the rows live, returning one row per student. A semester filed
        # without a CGPA is skipped in the WHERE, so the latest semester that
        # HAS one is the number, and a student with none is absent rather than
        # "CGPA 0.00" (or, before this, a TypeError on float(None)).
        rows = db.execute(
            select(SemesterResult.student_id, SemesterResult.cgpa)
            .where(SemesterResult.student_id.in_(sids), SemesterResult.cgpa.is_not(None))
            .distinct(SemesterResult.student_id)
            .order_by(SemesterResult.student_id, SemesterResult.semester.desc())
        ).all()
        return {sid: (float(cgpa), f"CGPA {float(cgpa):.2f}") for sid, cgpa in rows}

    if board == "streak":
        # Active-day count (LoginDay is keyed by user_id, and every sign-in door
        # writes one through auth._record_login).
        uids = list(uid_by_sid.values())
        rows = db.execute(
            select(LoginDay.user_id, func.count())
            .where(LoginDay.user_id.in_(uids))
            .group_by(LoginDay.user_id)
        ).all()
        by_uid = {uid: n for uid, n in rows}
        return {
            sid: (float(by_uid[uid]), _plural(by_uid[uid], "active day"))
            for sid, uid in uid_by_sid.items()
            if by_uid.get(uid)
        }

    return {}


class LeaderRow(BaseModel):
    rank: int
    student_id: str
    name: str
    initials: str
    value: float
    value_label: str
    is_me: bool


class LeaderPeer(BaseModel):
    """A batch mate who is on the roster but not yet ranked on this board:
    a name and nothing else, because there is no number to print."""

    student_id: str
    name: str
    initials: str
    is_me: bool


class LeaderboardOut(BaseModel):
    board: str
    opted_out: bool
    #: "batch" — ranked within the caller's batch; "department" — the caller is
    #: seated in no batch and is ranked within the department they named;
    #: "none" — neither is recorded, and the screen says so instead of drawing
    #: an empty board as if nobody had done anything.
    scope: str
    #: The batch ("General MBA - Finance · 2026-28") or department the board
    #: covers, for the sentence under the title. None when `scope` is "none".
    scope_label: str | None
    #: Everybody in the scope who has not opted out — ranked or not.
    classmates: int
    cohort_size: int  # number of ranked students on the board (for "Rank 8 of N")
    rows: list[LeaderRow]
    #: Classmates with nothing on this board yet, by name. Capped; see
    #: `_LEADERBOARD_UNRANKED_N`.
    unranked: list[LeaderPeer]
    unranked_total: int


# How much of a board one response carries: the top of the table plus the
# caller's own row (appended with its true rank when it falls outside). At a
# 5,000-student cohort the full board was a ~600 KB JSON document per request,
# and nobody scrolls to rank 4,000 — they look at the top and at themselves.
_LEADERBOARD_TOP_N = 50
# The unranked list is names only, and a batch is dozens of people; the cap
# exists for the department fallback, which can be every batch of a department.
_LEADERBOARD_UNRANKED_N = 200
# One ranked board per (scope, board) is recomputed at most this often, per
# worker. Leaderboards tolerate staleness by definition — a rank that is 60 s
# old is still today's rank — and this turns a deadline-week thundering herd
# into at most one aggregate pass a minute per scope per board.
_LEADERBOARD_CACHE_TTL_S = 60.0
_leaderboard_cache: dict[tuple[str, str | None, str], tuple[float, "_Board"]] = {}
_leaderboard_cache_lock = threading.Lock()


def clear_leaderboard_cache() -> None:
    """Drop every cached board on this worker.

    For a write that changes WHO is on a board rather than what they hold: a
    student's own visibility setting, and the office removing or restoring an
    account (`routers/admin_deletion.py`). The WHOLE cache and never one
    scope, because a student sits in a batch and a department at once and a
    change like this is rare enough that recomputing every board once is
    cheaper than getting the key set wrong. Per worker, like the cache itself:
    another worker keeps its board for the rest of the TTL. And a board read
    that began before the write committed can finish after this clear and put
    the old board back for one TTL — the staleness the cache already declares.
    """
    with _leaderboard_cache_lock:
        _leaderboard_cache.clear()


@dataclass(frozen=True)
class _Scope:
    """Who the caller is ranked against — see the module note above."""

    kind: str  # "batch" | "department" | "none"
    key: str | None
    label: str | None


@dataclass(frozen=True)
class _Board:
    """One computed board: the ranked entries — (rank, student_id, name, value,
    label) — the unranked classmates as (student_id, name), and the roster
    size the two were drawn from."""

    entries: list[tuple[int, str, str, float, str]]
    unranked: list[tuple[str, str]]
    classmates: int


def _leaderboard_scope(db: Session, me: Student) -> _Scope:
    """The batch, else the department, else nobody — and the words for it.

    The label is `batch_labels.compose` over the same institution query the
    profile card reads (`_INSTITUTION_Q`), never `cohorts.name` alone, which
    is a year and not a batch ("A BATCH IS A YEAR", AGENTS.md).
    """
    if me.cohort_id:
        cohort = db.get(Cohort, me.cohort_id)
        if cohort is not None:
            row = db.execute(_INSTITUTION_Q, {"cohort_id": cohort.id}).mappings().first()
            label = batch_labels.compose(
                row["course_name"] if row else None,
                row["specialization_name"] if row else None,
                cohort.name,
                cohort.batch_label,
            )
            return _Scope("batch", cohort.id, label)
    if me.department_id:
        department = db.get(Department, me.department_id)
        if department is not None:
            return _Scope("department", department.id, department.name)
    return _Scope("none", None, None)


def _ranked_board(db: Session, scope: _Scope, board: str) -> _Board:
    """The whole board for one scope, cached per worker for
    _LEADERBOARD_CACHE_TTL_S.

    Cached WITHOUT the caller folded in: `is_me` is per-request decoration, so
    one cached board serves every student in the scope. The opt-out set is part
    of the cached computation and therefore up to 60 s stale FOR OTHERS — the
    student who opts out vanishes from their own screen immediately, because
    `leaderboards` checks their own profile fresh on every request. A stampede
    on an expired entry recomputes it a couple of times concurrently rather
    than serialising every reader behind one computation; the work is idempotent
    and the last writer wins.
    """
    if scope.kind == "none":
        return _Board(entries=[], unranked=[], classmates=0)
    key = (scope.kind, scope.key, board)
    now = time.monotonic()
    with _leaderboard_cache_lock:
        hit = _leaderboard_cache.get(key)
        if hit is not None and now - hit[0] < _LEADERBOARD_CACHE_TTL_S:
            return hit[1]

    opted_out = set(
        db.scalars(
            select(StudentProfile.student_id).where(
                StudentProfile.leaderboard_opt_out.is_(True)
            )
        ).all()
    )
    # A REMOVED account (`users.deleted_at`) is off every screen, classmates'
    # boards included: the office is told "… is off every screen", and a name
    # left ranked, or listed as not ranked yet, on a batch mate's board made
    # that untrue. Every board, `overall`'s scaling and `classmates` read this
    # roster, so the one filter reaches them all.
    roster_q = (
        select(Student.id, Student.user_id, User.name)
        .select_from(Student)
        .join(User, Student.user_id == User.id)
        .where(User.deleted_at.is_(None))
    )
    if scope.kind == "batch":
        roster_q = roster_q.where(Student.cohort_id == scope.key)
    else:
        # The department reaches BOTH pointers, as `ancestry_of_student` does:
        # the students seated in one of its batches and the students who named
        # it on the form and are seated nowhere yet.
        roster_q = roster_q.outerjoin(Cohort, Student.cohort_id == Cohort.id).where(
            (Student.department_id == scope.key) | (Cohort.department_id == scope.key)
        )
    roster_rows = db.execute(roster_q).all()
    roster = [(sid, uid, name) for sid, uid, name in roster_rows if sid not in opted_out]

    values = _board_values(db, board, [(sid, uid) for sid, uid, _ in roster])
    # Only a student with a record on this board is on it (see _board_values).
    # Equal totals share a rank and the next rank skips -- 1, 2, 2, 4 -- because
    # two students holding the same three verified skills are level, and
    # `enumerate` over a sort made one of them second by whichever row Postgres
    # happened to return first, a different one after every cache refresh. The
    # name and then the id order the DISPLAY of a tie only, so the board is the
    # same list every time it is drawn.
    ordered = sorted(
        ((sid, name) for sid, _uid, name in roster if sid in values),
        key=lambda r: (-values[r[0]][0], r[1].casefold(), r[0]),
    )
    entries: list[tuple[int, str, str, float, str]] = []
    rank = 0
    previous: float | None = None
    for position, (sid, name) in enumerate(ordered, start=1):
        value, label = values[sid]
        if value != previous:
            rank, previous = position, value
        entries.append((rank, sid, name, value, label))
    unranked = sorted(
        ((sid, name) for sid, _uid, name in roster if sid not in values),
        key=lambda r: (r[1].casefold(), r[0]),
    )
    computed = _Board(entries=entries, unranked=unranked, classmates=len(roster))
    with _leaderboard_cache_lock:
        _leaderboard_cache[key] = (now, computed)
    return computed


@router.get("/leaderboards", response_model=LeaderboardOut)
def leaderboards(
    board: str = "overall",
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> LeaderboardOut:
    """Rank the caller's batch on one board. A student who opted out is excluded
    from every board and — in both directions — sees no ranks themselves."""
    student_id = _require_student(session)
    require_feature(db, student_id, "student.leaderboards")
    if board not in _BOARDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown board. One of: {', '.join(_BOARDS)}.",
        )
    me = db.get(Student, student_id)
    if me is None:
        # A session outlives the row it describes: a 12h cookie stays valid after
        # a roster rekey or a deleted account, and this endpoint then read
        # me.cohort_id straight through and 500ed. Every sibling that
        # dereferences the caller's own Student answers 404 here (see
        # /dashboard); match them rather than inventing a third behaviour.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    scope = _leaderboard_scope(db, me)
    my_profile = db.scalar(
        select(StudentProfile).where(StudentProfile.student_id == student_id)
    )
    if my_profile is not None and my_profile.leaderboard_opt_out:
        return LeaderboardOut(
            board=board,
            opted_out=True,
            scope=scope.kind,
            scope_label=scope.label,
            classmates=0,
            cohort_size=0,
            rows=[],
            unranked=[],
            unranked_total=0,
        )

    # The whole board, ranked, from the per-worker 60 s cache — see
    # _ranked_board for what is cached and what stays per-request.
    computed = _ranked_board(db, scope, board)

    def _row(entry: tuple[int, str, str, float, str]) -> LeaderRow:
        rank, sid, name, value, label = entry
        return LeaderRow(
            rank=rank,
            student_id=sid,
            name=name,
            initials=_initials(name),
            value=value,
            value_label=label,
            is_me=(sid == student_id),
        )

    # Top of the table, plus the caller's own row with its TRUE rank when they
    # fall outside it — "Rank 1,247 of 4,890" is the sentence a student wants,
    # and shipping ranks 51-4,889 to render it was the 600 KB the audit flagged.
    rows = [_row(e) for e in computed.entries[: _LEADERBOARD_TOP_N]]
    if not any(r.is_me for r in rows):
        mine = next((e for e in computed.entries if e[1] == student_id), None)
        if mine is not None:
            rows.append(_row(mine))
    unranked = [
        LeaderPeer(student_id=sid, name=name, initials=_initials(name), is_me=(sid == student_id))
        for sid, name in computed.unranked[:_LEADERBOARD_UNRANKED_N]
    ]
    return LeaderboardOut(
        board=board,
        opted_out=False,
        scope=scope.kind,
        scope_label=scope.label,
        classmates=computed.classmates,
        cohort_size=len(computed.entries),
        rows=rows,
        unranked=unranked,
        unranked_total=len(computed.unranked),
    )


class LeaderboardVisibilityIn(BaseModel):
    hidden: bool


@router.put("/leaderboard-visibility")
def set_leaderboard_visibility(
    body: LeaderboardVisibilityIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    """Hide or show yourself on the Leaderboards screen — sets the caller's
    StudentProfile.leaderboard_opt_out (creating the profile row if missing)."""
    student_id = _require_student(session)
    prof = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    if prof is None:
        prof = StudentProfile(student_id=student_id)
        db.add(prof)
    prof.leaderboard_opt_out = body.hidden
    db.commit()
    # Drop the cached boards so the toggle is visible on the very next read.
    # Without this, opting BACK IN inside a still-fresh cache window answered
    # "You're now visible on the leaderboards" and then rendered a board
    # without them — the review of the 2026-08 fix wave caught the
    # contradiction. (Opting OUT never had the problem only because the
    # caller's own opt-out is checked fresh, before the cache, in
    # `leaderboards` above.) Classmates on another worker may see the old
    # board for the remaining TTL, which is the staleness the cache already
    # declares acceptable — lying to the student about their own setting was
    # not.
    clear_leaderboard_cache()
    return {"hidden": body.hidden}


# --- Rule-based guidance (no LLM): next actions, readiness, recommendations ----
#
# Every value below is computed from the caller's own rows. These endpoints are
# STUDENT-only and never touch a model, so they carry no student-data egress.


def _attendance_pct(db: Session, student_id: str) -> float | None:
    """Overall attendance %, or None when NOTHING HAS BEEN RECORDED.

    IT RETURNED 0.0 FOR AN EMPTY TABLE UNTIL 2026-09-13, and that was a live
    violation of the cross-cutting guardrail (07 §5) the rest of this codebase
    already keeps in four other places. `attendance_records` has exactly one
    writer — B8.1's spreadsheet import — so on every deployment where that
    import has not yet been run, EVERY student was shown "Attendance 0.0% vs
    required 75.0%" with a red Not-met chip beside it, on their own home screen,
    for a bar nobody had measured them against. A student reading that has been
    told they are failing; the true statement is that no attendance has been
    imported.

    None and 0.0 are opposite facts here and must never render the same:
    `console.py::MenteeMetricsOut.attendance_percent` and
    `student_weekly.attendance_percent` already say exactly this, per student
    and per week. This is the third reader and the one that was wrong.
    """
    rows = db.execute(
        select(AttendanceRecord.present).where(AttendanceRecord.student_id == student_id)
    ).all()
    total = len(rows)
    if not total:
        return None
    present = sum(1 for (p,) in rows if p)
    return round(100 * present / total, 1)


def _latest_cgpa(db: Session, student_id: str) -> float | None:
    latest = db.scalar(
        select(SemesterResult)
        .where(SemesterResult.student_id == student_id)
        .order_by(SemesterResult.semester.desc())
        .limit(1)
    )
    return latest.cgpa if latest else None


def _live_backlogs(db: Session, student_id: str) -> int | None:
    """Total live backlogs across every semester result, or None when no result
    has been recorded.

    THIS ONE FAILED IN THE OTHER DIRECTION and is the more insidious of the
    pair. `sum()` over no rows is 0, and 0 backlogs PASSES the readiness check —
    so a student with nothing imported was shown a green Met chip on a fact
    nobody had established, beside a red one on attendance. Absence read as a
    pass is still absence read as a measurement, and it is the version nobody
    reports as a bug.

    Backlogs and CGPA are the same evidence (`semester_results`), so they
    resolve together: either the marks are in and both are answerable, or
    neither is.
    """
    rows = db.execute(
        select(SemesterResult.live_backlogs).where(
            SemesterResult.student_id == student_id
        )
    ).all()
    if not rows:
        return None
    return int(sum(int(n or 0) for (n,) in rows))


def _cert_completion_pct(db: Session, student_id: str) -> float | None:
    """Share of the student's certifications that are COMPLETED, or None when
    none is enrolled.

    `_attendance_pct`'s fix, applied to its neighbour for the same reason: "0%
    of certifications completed vs required 50%" is a failing grade in a course
    nobody has put them on. A student with an empty shelf has not fallen behind;
    there is nothing to be behind on, and the readiness score must not spend a
    weight on it either way.
    """
    rows = db.execute(
        select(CertificationProgress.status).where(
            CertificationProgress.student_id == student_id
        )
    ).all()
    total = len(rows)
    if not total:
        return None
    done = sum(1 for (s,) in rows if s == ProgressStatus.COMPLETED)
    return round(100 * done / total, 1)


def _resume_pct(db: Session, student_id: str) -> int:
    row = db.scalar(select(ResumeProfile).where(ResumeProfile.student_id == student_id))
    return _resume_completeness(row.data if row else {})


#: B6.3 — how far back a mock-interview score counts towards readiness.
#:
#: Ninety days is the spec's number and it is a judgement about SHELF LIFE, not
#: about the window a student practises in: an interview sat last October says
#: very little about how somebody presents in February, and a readiness score
#: standing on it would be quietly out of date on the screen a placement
#: decision gets argued from. Past the window the factor goes back to
#: UNMEASURED — which is not the same as failing it, and the machinery below
#: keeps those apart.
INTERVIEW_READINESS_WINDOW_DAYS = 90

#: The bar a mock-interview score has to clear to count as met.
#:
#: A JUDGEMENT AND NOT A DERIVATION, and it deliberately does not live in
#: `placement_criteria` with the other four cut-offs. Those four are the
#: college's (B8.2 made them a table, per course, with history); this one is a
#: property of a scorecard written by a model REEP chose and is not a rule any
#: college has set. Putting it in that table would invite an office to tune it
#: against a scale they have no way to calibrate.
#:
#: 60 is the same number `_readiness_band` calls the bottom of "On track", so a
#: student whose interview score is at their overall band passes this check
#: rather than dragging it. Move it and a cohort changes band on a deploy.
MIN_MOCK_INTERVIEW_SCORE = 60


def _best_interview_score(db: Session, student_id: str) -> int | None:
    """The best overall mock-interview score inside the window, or None.

    BEST, NOT LATEST, and the spec says so for a reason worth keeping: this is a
    practice instrument a student is meant to use repeatedly, and a rule that
    reads the last attempt punishes exactly the behaviour the product is asking
    for — sitting one more interview to try something different can only lower
    your readiness. Best answers "what has this student shown they can do".

    None means NOT ASSESSED and is the whole point of the column being nullable
    all the way down. A student who has never practised has no interview score;
    they have not failed one. `build_readiness` carries that through as
    `measured=False`, which keeps the factor out of the denominator entirely —
    so not having practised yet cannot lower a readiness score.

    Reads `interview_score_summaries`, never `interview_evaluations`: the
    summary survives retention and the evaluation does not, so this is the only
    read that gives the same answer in March about a January interview.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=INTERVIEW_READINESS_WINDOW_DAYS
    )
    return db.scalar(
        select(func.max(InterviewScoreSummary.overall_score)).where(
            InterviewScoreSummary.student_id == student_id,
            InterviewScoreSummary.started_at >= cutoff,
        )
    )


#: B7.6. The weight at which a SWOC weakness becomes something to do about it.
#: The author's own number off the composer's weight picker — see `next_actions`.
SWOC_ACTION_MIN_WEIGHT = 4

#: How many of them may reach the card at once, so one board cannot take over a
#: list it is one input to.
SWOC_MAX_ACTIONS = 2

#: WHOSE OBSERVATION IT WAS, in the student's words rather than the enum's. The
#: board is deliberately un-averaged — disagreement between viewpoints IS the
#: finding — so the viewpoint travels with the line onto this card too, and a
#: student can tell "my mentor said this" from "the placement cell said this".
#: PM is unreachable from the writer and still legal in storage, so it is
#: answered here rather than left to a KeyError on a row the seed writes.
SWOC_ACTION_REASON: dict[SwocSource, str] = {
    SwocSource.MENTOR: "Flagged by your mentor on your Faculty / TPO Log",
    SwocSource.PLACEMENT: "Flagged by the placement cell on your Faculty / TPO Log",
    SwocSource.PM: "Flagged on your Faculty / TPO Log",
}


def _swoc_action_route(entry: SwocEntry) -> str:
    """Where a flagged weakness sends the student — B7.6's two halves meeting.

    The LINK decides, and the fallback is the board itself. Order matters only
    where an author named more than one: the skill is the most specific thing a
    weakness can be about and the posting is the least, so the route follows
    that. Every one of these is a real route in `app.routes.ts`; a `cta_route`
    that 404s is a button that reports the product as broken.
    """
    if entry.linked_skill_id:
        return "/student/skilling"
    if entry.linked_session_id:
        return "/student/interviews"
    if entry.linked_job_id:
        return "/student/jobs"
    return "/student/mentor-log"


class NextActionOut(BaseModel):
    id: str
    title: str
    reason: str
    cta_label: str
    cta_route: str
    status: str
    deadline: datetime | None
    priority: int


class NextActionsOut(BaseModel):
    actions: list[NextActionOut]


@router.get("/next-actions", response_model=NextActionsOut)
def next_actions(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> NextActionsOut:
    """The student's 'what to do next' list — the top 5 candidate actions drawn
    from their real course, profile, resume and skilling state, sorted by
    urgency (lower priority = more urgent). Rule-based; no model.

    Certification rows used to lead this list ("Finish X", routed to the
    Certification Tracker). That screen and its endpoint were removed on
    2026-09-17, so an action pointing at it would be a button to nowhere.
    """
    student_id = _require_student(session)
    actions: list[NextActionOut] = []

    # In-progress courses.
    course_rows = db.execute(
        select(Enrollment, Course)
        .join(Course, Enrollment.course_code == Course.code)
        .where(
            Enrollment.student_id == student_id,
            Enrollment.status == ProgressStatus.IN_PROGRESS,
        )
        .order_by(Course.semester, Course.code)
    ).all()
    for enr, course in course_rows:
        if enr.lectures_total:
            reason = f"{enr.lectures_attended}/{enr.lectures_total} lectures attended"
        else:
            reason = "In progress — keep logging your self-learning hours"
        actions.append(
            NextActionOut(
                id=f"course-{course.code}",
                title=f"Finish {course.name}",
                reason=reason,
                cta_label="Continue",
                cta_route="/student/courses",
                status="In progress",
                deadline=None,
                priority=3,
            )
        )

    # Missing placement-profile fields.
    prof = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    if not (prof and prof.phone):
        actions.append(
            NextActionOut(
                id="profile-phone",
                title="Add your phone number to your placement profile",
                reason="Recruiters need a way to reach you",
                cta_label="Add",
                cta_route="/student/profile",
                status="Missing",
                deadline=None,
                priority=2,
            )
        )
    if not (prof and prof.linkedin_url):
        actions.append(
            NextActionOut(
                id="profile-linkedin",
                title="Add your LinkedIn URL to your placement profile",
                reason="A LinkedIn profile strengthens your placement record",
                cta_label="Add",
                cta_route="/student/profile",
                status="Missing",
                deadline=None,
                priority=2,
            )
        )

    # Low resume completeness.
    resume_pct = _resume_pct(db, student_id)
    if resume_pct < 70:
        actions.append(
            NextActionOut(
                id="resume-completeness",
                title=f"Complete your resume profile ({resume_pct}%)",
                reason="A fuller resume profile means a stronger auto-generated CV",
                cta_label="Complete",
                cta_route="/student/resume",
                status="Incomplete",
                deadline=None,
                priority=4,
            )
        )

    # A pending skill claim under review, or a held-but-unverified skill.
    pending_claim = db.execute(
        select(SkillClaim, Skill.name)
        .join(Skill, SkillClaim.skill_id == Skill.id)
        .where(
            SkillClaim.student_id == student_id,
            SkillClaim.status == UploadStatus.PENDING_REVIEW,
        )
        .order_by(SkillClaim.created_at.desc())
        .limit(1)
    ).first()
    if pending_claim is not None:
        _claim, skill_name = pending_claim
        actions.append(
            NextActionOut(
                id="skill-claim-pending",
                title=f"Your {skill_name} skill claim is under review",
                reason="A mentor is reviewing your evidence",
                cta_label="View",
                cta_route="/student/skilling",
                status="Pending review",
                deadline=None,
                priority=4,
            )
        )
    else:
        unverified = db.execute(
            select(StudentSkill, Skill.name)
            .join(Skill, StudentSkill.skill_id == Skill.id)
            .where(
                StudentSkill.student_id == student_id,
                StudentSkill.verified.is_(False),
            )
            .order_by(StudentSkill.updated_at.desc())
            .limit(1)
        ).first()
        if unverified is not None:
            _ss, skill_name = unverified
            actions.append(
                NextActionOut(
                    id="skill-unverified",
                    title=f"Get your {skill_name} skill verified",
                    reason="Upload evidence so a mentor can verify it",
                    cta_label="Verify",
                    cta_route="/student/skilling",
                    status="Unverified",
                    deadline=None,
                    priority=4,
                )
            )

    # B6.3 — the drill the last mock interview asked for.
    #
    # "WHILE IT IS UNADDRESSED" IS DEFINED AS "UNTIL YOU SIT ANOTHER ONE", and
    # that is the whole of the definition. Nothing records that a student did a
    # drill — there is no acknowledgement flag, and inventing one would be a
    # column written by a button nobody presses, which then makes the action
    # stick forever for everybody who ignores it. Reading the MOST RECENT
    # completed interview instead means the action is replaced when the student
    # practises again (with the new drill, or with nothing if that interview
    # produced none), which is the behaviour a student would describe as "it
    # went away when I did it".
    #
    # The drill is the model's own sentence about this student, so it is shown
    # to the student and nowhere else — this endpoint is `_require_student` and
    # first-person throughout.
    drill_row = db.execute(
        select(InterviewEvaluation.drill, InterviewSession.started_at)
        .join(
            InterviewSession,
            InterviewEvaluation.interview_session_id == InterviewSession.id,
        )
        .where(
            InterviewSession.student_id == student_id,
            InterviewSession.status == "completed",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.started_at.desc())
        .limit(1)
    ).first()
    if drill_row is not None and (drill_row[0] or "").strip():
        drill_text = drill_row[0].strip()
        actions.append(
            NextActionOut(
                id="interview-drill",
                # The model's words, not a paraphrase. A drill rewritten into a
                # house sentence stops being the thing the student was told at
                # the end of their interview, which is the only reason it
                # carries any weight.
                title=drill_text,
                reason="From your last mock interview",
                cta_label="Practise",
                cta_route="/student/assistant",
                status="Suggested",
                deadline=None,
                priority=3,
            )
        )

    # B7.6 — the heavy weaknesses somebody wrote on this student's SWOC board.
    #
    # WEIGHT >= 4 IS THE WHOLE FILTER, and it is the author's own judgement
    # rather than a threshold this function invented: the composer's weight
    # picker is the one control a mentor has for "this matters". A board with
    # nine 3s produces no actions, which is correct — the screen exists to carry
    # observations, not to nag about every one of them.
    #
    # THE TWO HEAVIEST ONLY. `actions[:5]` below is a cap on the list, not on any
    # one source, so six weight-5 weaknesses would push the overdue certification
    # and the profile fields off a student's next-actions card entirely — the
    # board would have quietly taken over a list it is one input to.
    #
    # "WHILE IT IS UNADDRESSED" IS THE AUTHOR'S TO ANSWER, and unlike B6.3's
    # drill this one already has a mechanism: the mentor lowers the weight or
    # deletes the line. That is a real human lifecycle on an existing screen, so
    # nothing here needs a flag nobody presses. The student's own acknowledgement
    # deliberately does NOT clear it: "I have read this" is not "I have done
    # something about it", and letting a tap remove the action would make the one
    # honest signal the student sends into a way of hiding the line.
    #
    # THE ROUTE COMES FROM THE LINK (B7.6's other half), and that is the reason
    # these two halves are one task. `NextActionOut` requires a `cta_route` and a
    # sentence about a person has no natural destination; a linked skill,
    # interview or posting does. With no link the route is the board itself,
    # where the line is drawn in full beside the meeting it came out of.
    #
    # READINESS IS DELIBERATELY NOT TOUCHED, and this is the one place a reader
    # will look for it. 04 asks for "readiness and next-actions"; adding an
    # eighth `ReadinessFactorOut` would (a) move `MIN_SCORED_WEIGHT_SHARE`'s bar
    # under everybody — the comment on that constant spells out what happened the
    # last time a factor was added and the fraction was not re-derived — and
    # (b) score a student DOWN for a colleague's written opinion, which has no
    # cut-off, no import behind it and no way for the student to answer it. Every
    # other factor is a measurement against a stated threshold. A SWOC line is a
    # judgement, and turning it into two points of a headline number is a
    # different product decision from showing it as something to work on.
    heavy = db.execute(
        select(SwocEntry)
        .where(
            SwocEntry.student_id == student_id,
            SwocEntry.kind == SwocKind.WEAKNESS,
            SwocEntry.weight >= SWOC_ACTION_MIN_WEIGHT,
        )
        .order_by(SwocEntry.weight.desc(), SwocEntry.recorded_at.desc())
        .limit(SWOC_MAX_ACTIONS)
    ).scalars().all()
    for entry in heavy:
        actions.append(
            NextActionOut(
                id=f"swoc-{entry.id}",
                # THE AUTHOR'S OWN SENTENCE, for B6.3's reason restated: a line
                # rewritten into a house phrasing stops being the thing the
                # student's mentor actually wrote about them, which is the only
                # reason it carries any weight here.
                title=entry.text,
                reason=SWOC_ACTION_REASON[entry.source],
                cta_label="Open",
                cta_route=_swoc_action_route(entry),
                status="Flagged",
                # NO DEADLINE, and not one derived from `recorded_at` either. A
                # mentor writing "needs more practice" set no date, and a card
                # that invents one would age a line into an overdue-looking chip
                # nobody promised.
                deadline=None,
                # A 5 sits with the overdue certification and a 4 with the
                # in-progress work: the author said which of the two this was.
                priority=2 if entry.weight >= 5 else 3,
            )
        )

    actions.sort(key=lambda a: a.priority)
    return NextActionsOut(actions=actions[:5])


class ReadinessFactorOut(BaseModel):
    label: str
    met: bool
    detail: str
    weight: int
    #: FALSE means nothing has been imported or recorded for this check, so
    #: `met` is not an answer — it is the absence of one (07 §5's guardrail).
    #:
    #: `met` IS STILL PRESENT AND STILL FALSE on an unmeasured factor, and that
    #: is a compatibility decision rather than an oversight: three callers read
    #: this shape (the student home card, the staff 360 panel and the
    #: assistant's readiness answer), and a client that has not learned the new
    #: field must not start rendering a green "Met" chip over a check nobody has
    #: run. Reading `met` alone is now WRONG in one direction and it was always
    #: wrong; reading it alone is no longer DANGEROUS. Every caller in this
    #: repository branches on `measured` first.
    measured: bool = True


class PlacementReadinessOut(BaseModel):
    #: NULL when not one check could be measured — never 0. A student on a fresh
    #: deployment has no marks, no attendance and no certifications imported, and
    #: a "0/100 — Not ready" in a 27px numeral on their own home screen is the
    #: product telling them they have failed an assessment that has not happened.
    #: The score is computed over the MEASURED factors only, so it answers
    #: "against what is known about you" and rises as the imports land rather
    #: than being dragged down by their absence.
    score: int | None
    band: str
    summary: str
    factors: list[ReadinessFactorOut]


#: The band when there is not enough on record to score at all. A WORD AND NOT
#: AN EMPTY STRING, because every consumer renders the band as a chip beside the
#: score and a blank chip reads as a rendering bug rather than as a statement.
BAND_NOT_ASSESSED = "Not assessed yet"

#: How much of the readiness weight must be MEASURABLE before a score is
#: reported at all.
#:
#: WITHOUT THIS THE FIX MAKES THE HEADLINE NUMBER WORSE, which is worth spelling
#: out because it is counter-intuitive. Two of the seven checks read what the
#: STUDENT has typed (contact details, resume completeness) and are therefore
#: always measurable; the other five read tables only an import, a staff action
#: or a mock interview fills. So on a fresh deployment, scoring "over the
#: measured weight" means scoring over those two alone — and a student who has
#: not yet typed their phone number lands on **0/100 — Not ready**, in a 27px
#: numeral, where the old broken arithmetic at least said 17. Both are verdicts
#: on evidence that does not exist; the new one is simply a harsher way of being
#: wrong.
#:
#: A score over a sixth of the evidence is not a score. Below this share the
#: honest answer is that there is not enough on record, said in words, with the
#: per-check chips underneath still showing exactly what is and is not known —
#: which is more use to a student than any number would be.
#:
#: IT IS A JUDGEMENT AND NOT A DERIVATION, and it is expressed as a FRACTION so
#: that adding a check does not silently move the bar. The judgement is: the two
#: checks that cannot be missing are nowhere near sufficient on their own, and
#: EITHER of the imported pairs — marks (6), or two of {attendance 2,
#: certifications 2, mock interview 2} — crosses it in company with them.
#:
#: It read `0.5` while the rule carried six checks weighing 12, and 6/12 was
#: exactly the attendance-plus-certifications case. B6.3 added a seventh check
#: worth 2, and leaving the number at 0.5 would have quietly RAISED the bar:
#: 6/14 is 0.43, so a first-semester student with attendance and certifications
#: imported but no results yet would have stopped being scored on a deploy that
#: changed nothing about them. `3/7` is the same judgement over the new total —
#: every combination that crossed before crosses now, and the only new ones are
#: the two that pair the mock interview with attendance or certifications, which
#: is the same shape as the pair that already crossed. Move it and a cohort's
#: worth of students change band on a deploy, so move it deliberately.
MIN_SCORED_WEIGHT_SHARE = 3 / 7


def _readiness_band(score: int | None) -> str:
    if score is None:
        return BAND_NOT_ASSESSED
    if score < 40:
        return "Not ready"
    if score < 60:
        return "Developing"
    if score < 80:
        return "On track"
    return "Ready"


@dataclass(frozen=True)
class _ReadinessInputs:
    """Everything the readiness rule reads about ONE student, already fetched.

    THE RULE AND THE READS ARE SPLIT SO THE ROLL-UP CANNOT BECOME A SECOND RULE.
    B8.5's "placement-ready %" is this score over every student in a reach, and
    the obvious implementation — a SQL expression that joins marks, attendance
    and certifications and counts the ones over the bar — would be a SECOND
    definition of placement readiness, in a different language, which would
    disagree with the student's own screen the first time either was edited.
    `routers/mentee_records.py` states the rule and `compose_placement_readiness`
    was already built to it; this only carries it one step further, so the
    per-student path and the cohort path share the DECISION and differ only in
    how many rows they fetched to make it.

    Every field is Optional where absence is a real state — see
    `_attendance_pct` and its neighbours for what None means and why it is not 0.
    """

    cgpa: float | None
    backlogs: int | None
    attendance_pct: float | None
    cert_pct: float | None
    has_contacts: bool
    resume_pct: int
    #: B6.3 — the best overall mock-interview score inside the 90-day window.
    #: None is NOT ASSESSED, never a zero, and it is what keeps a student who
    #: has simply not practised out of the denominator instead of ~14 points
    #: below where they were yesterday.
    best_interview_score: int | None = None


@dataclass(frozen=True)
class _ReadinessCriteria:
    """The four cut-offs, resolved once per request rather than per student."""

    min_cgpa: float
    max_backlogs: int
    min_attendance_pct: float
    min_cert_completion_pct: float


def _readiness_criteria_from(resolved: ResolvedCriteria) -> _ReadinessCriteria:
    """The four cut-offs readiness scores against, out of a resolved set."""
    return _ReadinessCriteria(
        min_cgpa=resolved.min_cgpa,
        max_backlogs=resolved.max_live_backlogs,
        min_attendance_pct=resolved.min_attendance_pct,
        min_cert_completion_pct=resolved.min_cert_completion_pct,
    )


def readiness_criteria(db: Session, student_id: str | None = None) -> _ReadinessCriteria:
    """The cut-offs one student is scored against — B8.2's resolver (see below).

    THIS WAS THE ONE CALL SITE B8.2 WAS ASKED TO CHANGE, and the sentence that
    used to be here said so. The four literals it fell back to — 6.0 · 0 · 75 % ·
    50 % — moved to `criteria.DEFAULTS` unchanged to the digit; what is new is
    the three rungs above them: the student's COURSE row, then their COLLEGE
    row, then the programme-wide row.

    `student_id=None` ANSWERS THE PROGRAMME QUESTION, for a caller that is not
    scoring a particular person. It is not the same as "the defaults": a
    programme-wide row still answers it.
    """
    resolved = (
        resolve_criteria_for_student(db, student_id)
        if student_id is not None
        else resolve_criteria(db)
    )
    return _readiness_criteria_from(resolved)


def _readiness_inputs(db: Session, student_id: str) -> _ReadinessInputs:
    """One student's inputs, through the five helpers above — six queries."""
    prof = db.scalar(select(StudentProfile).where(StudentProfile.student_id == student_id))
    return _ReadinessInputs(
        cgpa=_latest_cgpa(db, student_id),
        backlogs=_live_backlogs(db, student_id),
        attendance_pct=_attendance_pct(db, student_id),
        cert_pct=_cert_completion_pct(db, student_id),
        has_contacts=bool(prof and prof.phone and prof.linkedin_url),
        resume_pct=_resume_pct(db, student_id),
        best_interview_score=_best_interview_score(db, student_id),
    )


def readiness_inputs_many(db: Session, student_ids: list[str]) -> dict[str, _ReadinessInputs]:
    """The same inputs for MANY students: six queries in total, not six each.

    Written because the naive roll-up is six queries per student — 12 000 of
    them for a two-thousand-student deployment, on a screen the office opens
    every morning. The ARITHMETIC is not repeated here; only the fetching is.

    A student with no row in a given table gets None (or 0 for the two
    self-reported measures), which is the same answer the single-student
    helpers give for the same absence. That equivalence is what
    `tests/test_readiness_unassessed.py` pins, because the moment the batch
    reader disagrees with the single reader, a mentor and a student are looking
    at different numbers again.
    """
    if not student_ids:
        return {}

    latest_sem = (
        select(
            SemesterResult.student_id.label("sid"),
            func.max(SemesterResult.semester).label("sem"),
        )
        .where(SemesterResult.student_id.in_(student_ids))
        .group_by(SemesterResult.student_id)
        .subquery()
    )
    cgpa = {
        sid: value
        for sid, value in db.execute(
            select(SemesterResult.student_id, SemesterResult.cgpa).join(
                latest_sem,
                (SemesterResult.student_id == latest_sem.c.sid)
                & (SemesterResult.semester == latest_sem.c.sem),
            )
        ).all()
    }
    backlogs = {
        sid: int(total or 0)
        for sid, total in db.execute(
            select(SemesterResult.student_id, func.sum(SemesterResult.live_backlogs))
            .where(SemesterResult.student_id.in_(student_ids))
            .group_by(SemesterResult.student_id)
        ).all()
    }
    attendance = {
        sid: round(100 * int(present or 0) / int(total), 1)
        for sid, present, total in db.execute(
            select(
                AttendanceRecord.student_id,
                func.count().filter(AttendanceRecord.present.is_(True)),
                func.count(),
            )
            .where(AttendanceRecord.student_id.in_(student_ids))
            .group_by(AttendanceRecord.student_id)
        ).all()
        if int(total or 0) > 0
    }
    certs = {
        sid: round(100 * int(done or 0) / int(total), 1)
        for sid, done, total in db.execute(
            select(
                CertificationProgress.student_id,
                func.count().filter(CertificationProgress.status == ProgressStatus.COMPLETED),
                func.count(),
            )
            .where(CertificationProgress.student_id.in_(student_ids))
            .group_by(CertificationProgress.student_id)
        ).all()
        if int(total or 0) > 0
    }
    contacts = {
        sid: bool(phone and linkedin)
        for sid, phone, linkedin in db.execute(
            select(
                StudentProfile.student_id, StudentProfile.phone, StudentProfile.linkedin_url
            ).where(StudentProfile.student_id.in_(student_ids))
        ).all()
    }
    resumes = {
        sid: _resume_completeness(data or {})
        for sid, data in db.execute(
            select(ResumeProfile.student_id, ResumeProfile.data).where(
                ResumeProfile.student_id.in_(student_ids)
            )
        ).all()
    }
    # B6.3, and it is the SAME rule `_best_interview_score` states one query at
    # a time: best-in-window, from the summaries, absent means not assessed. The
    # `.get(sid)` below is what makes "no row" read as None rather than as 0 —
    # the equivalence `tests/test_readiness_unassessed.py` pins, because the
    # moment this reader disagrees with the single-student one a mentor and a
    # student are looking at different numbers again.
    interview_cutoff = datetime.now(timezone.utc) - timedelta(
        days=INTERVIEW_READINESS_WINDOW_DAYS
    )
    interviews = {
        sid: best
        for sid, best in db.execute(
            select(
                InterviewScoreSummary.student_id,
                func.max(InterviewScoreSummary.overall_score),
            )
            .where(
                InterviewScoreSummary.student_id.in_(student_ids),
                InterviewScoreSummary.started_at >= interview_cutoff,
            )
            .group_by(InterviewScoreSummary.student_id)
        ).all()
        if best is not None
    }

    return {
        sid: _ReadinessInputs(
            cgpa=cgpa.get(sid),
            backlogs=backlogs.get(sid),
            attendance_pct=attendance.get(sid),
            cert_pct=certs.get(sid),
            has_contacts=contacts.get(sid, False),
            resume_pct=resumes.get(sid, _resume_completeness({})),
            best_interview_score=interviews.get(sid),
        )
        for sid in student_ids
    }


def build_readiness(
    inputs: _ReadinessInputs, criteria: _ReadinessCriteria
) -> PlacementReadinessOut:
    """THE READINESS RULE. One function, no database, two callers.

    Everything above fetches; this decides. Keeping the decision in a pure
    function is what makes "the mentor sees exactly the student's own number"
    a property of the call graph rather than a sentence in a docstring.
    """
    min_cgpa = criteria.min_cgpa
    max_backlogs = criteria.max_backlogs
    min_att = criteria.min_attendance_pct
    min_cert = criteria.min_cert_completion_pct
    cgpa = inputs.cgpa
    backlogs = inputs.backlogs
    att = inputs.attendance_pct
    cert_pct = inputs.cert_pct
    has_contacts = inputs.has_contacts
    resume_pct = inputs.resume_pct
    interview = inputs.best_interview_score

    # FIVE OF THE SEVEN CHECKS CAN BE UNMEASURED, AND TWO NEVER CAN. CGPA,
    # backlogs, attendance and certification completion are read out of tables
    # that only an IMPORT or a staff action fills, so "no rows" means "nobody
    # has looked", not "zero". Placement profile and resume completeness are
    # read out of what the STUDENT has typed, where an empty form genuinely is
    # the answer — "you have not added your phone number" is measured, and
    # telling somebody it is unknown would be an excuse for a field they can
    # fill in right now.
    factors = [
        ReadinessFactorOut(
            label="CGPA",
            met=(cgpa is not None and cgpa >= min_cgpa),
            measured=cgpa is not None,
            detail=(
                f"CGPA {cgpa} meets the {min_cgpa} cut-off"
                if (cgpa is not None and cgpa >= min_cgpa)
                else (
                    f"CGPA {cgpa} is below the {min_cgpa} cut-off"
                    if cgpa is not None
                    else f"No semester results on record yet — the {min_cgpa} cut-off has nothing to read"
                )
            ),
            weight=3,
        ),
        ReadinessFactorOut(
            label="Live backlogs",
            met=(backlogs is not None and backlogs <= max_backlogs),
            measured=backlogs is not None,
            detail=(
                f"{backlogs} live backlog(s); limit is {max_backlogs}"
                if backlogs is not None
                else f"No semester results on record yet; the limit is {max_backlogs}"
            ),
            weight=3,
        ),
        ReadinessFactorOut(
            label="Attendance",
            met=(att is not None and att >= min_att),
            measured=att is not None,
            detail=(
                f"Attendance {att}% vs required {min_att}%"
                if att is not None
                else f"No attendance recorded yet; {min_att}% is required once it is"
            ),
            weight=2,
        ),
        ReadinessFactorOut(
            label="Certification completion",
            met=(cert_pct is not None and cert_pct >= min_cert),
            measured=cert_pct is not None,
            detail=(
                f"{cert_pct}% of certifications completed vs required {min_cert}%"
                if cert_pct is not None
                else f"No certifications enrolled yet; {min_cert}% completion is required"
            ),
            weight=2,
        ),
        ReadinessFactorOut(
            label="Placement profile",
            met=has_contacts,
            detail=(
                "Phone and LinkedIn are on file"
                if has_contacts
                else "Add your phone number and LinkedIn URL"
            ),
            weight=1,
        ),
        ReadinessFactorOut(
            label="Resume profile",
            met=(resume_pct >= 70),
            detail=f"Resume profile {resume_pct}% complete (target 70%)",
            weight=1,
        ),
        # B6.3. WEIGHT 2, AND `measured=False` WHEN THERE IS NO SCORE — which
        # is the whole design of this factor and not a detail of it. An
        # unmeasured factor is out of BOTH the numerator and the denominator
        # (see below), so a student who has never sat a mock interview is not
        # told they are less placement-ready for it. They are told, on the chip,
        # that nobody has assessed this yet — which is a thing they can act on
        # in one click, unlike "your CGPA has not been imported".
        #
        # BEST OF THE LAST 90 DAYS, from `interview_score_summaries`: best so
        # that practising again can never lower a score, the window so that a
        # reading does not stand on an interview from last October, and the
        # summaries because they are the rows that survive retention.
        ReadinessFactorOut(
            label="Mock interview",
            met=(interview is not None and interview >= MIN_MOCK_INTERVIEW_SCORE),
            measured=interview is not None,
            detail=(
                f"Best mock-interview score {interview} in the last "
                f"{INTERVIEW_READINESS_WINDOW_DAYS} days "
                f"(target {MIN_MOCK_INTERVIEW_SCORE})"
                if interview is not None
                else (
                    "No scored mock interview in the last "
                    f"{INTERVIEW_READINESS_WINDOW_DAYS} days — practise one and "
                    f"{MIN_MOCK_INTERVIEW_SCORE} is the target"
                )
            ),
            weight=2,
        ),
    ]

    # THE DENOMINATOR IS THE MEASURED WEIGHT, not the total weight. Dividing by
    # the total would keep the old behaviour under a new label: a student with
    # nothing imported would score 2/12 and be told "17/100 — Not ready", which
    # is the same false verdict the `measured` flag was added to stop, just
    # reached through arithmetic instead of through a chip.
    measured = [f for f in factors if f.measured]
    full_weight = sum(f.weight for f in factors)
    measured_weight = sum(f.weight for f in measured)
    met_weight = sum(f.weight for f in measured if f.met)
    enough = full_weight > 0 and measured_weight / full_weight >= MIN_SCORED_WEIGHT_SHARE
    score = round(100 * met_weight / measured_weight) if (enough and measured_weight) else None
    band = _readiness_band(score)
    unmeasured = [f.label for f in factors if not f.measured]
    if score is None:
        # Names the missing checks rather than saying "insufficient data": the
        # student cannot act on either sentence, but their mentor can act on the
        # first one, and it is the same card.
        # The labels VERBATIM, not lower-cased: "cgpa" reads as a typo where
        # "CGPA" reads as a heading, and these are the same words the chips
        # underneath carry.
        summary = (
            f"{band}. Not enough is on record to score this yet — nothing is "
            f"recorded for {', '.join(unmeasured)}."
        )
    else:
        met_count = sum(1 for f in measured if f.met)
        summary = f"{score}/100 — {band}. {met_count} of {len(measured)} placement checks met."
        if unmeasured:
            summary += (
                f" {len(unmeasured)} check(s) not measured yet, and are not "
                "counted either way."
            )

    return PlacementReadinessOut(score=score, band=band, summary=summary, factors=factors)



def compose_placement_readiness(db: Session, student_id: str) -> PlacementReadinessOut:
    """The readiness score for one student, computed once and shared.

    THE BUILDER IS SHARED BECAUSE THE VIEW MUST BE. `routers/mentee_records.py`
    already states the rule this follows: a staff screen reads the STUDENT'S OWN
    numbers, computed by the same expression, or a mentor ends up looking at a
    confident 0 where the student sees a dash and acting on it. B4.5's student
    360 needs this score for a student named in a path, so the endpoint below
    keeps the session check and the arithmetic lives in `build_readiness`.

    It takes a student id rather than a session on purpose: `_require_student`
    is the first-person gate and has no business in a builder a staff route
    calls. Rule 2's gate runs at the staff call site, before this is reached.

    Rule-based; no model, so rule 1's egress gate does not apply to it.
    """
    return build_readiness(
        _readiness_inputs(db, student_id), readiness_criteria(db, student_id)
    )


def compose_readiness_many(
    db: Session, student_ids: list[str]
) -> dict[str, PlacementReadinessOut]:
    """The same score for many students, for B8.5's cohort roll-up.

    Six queries plus the criteria, then `build_readiness` per student — the same
    function `compose_placement_readiness` calls, so a cohort percentage and the
    student's own card can never disagree.

    B8.2 MADE THE CRITERIA PER STUDENT, so this can no longer resolve one set
    for the whole call: two batches in the reach may sit on two courses with two
    different CGPA cut-offs, and scoring both against one of them would put a
    number on the office's screen that neither cohort's students see.
    `criteria.resolve_for_students` batches it by BATCH — one resolve per
    cohort, not per student — so the cost is unchanged in practice.
    """
    inputs = readiness_inputs_many(db, student_ids)
    gates = resolve_criteria_for_students(db, list(inputs))
    return {
        sid: build_readiness(row, _readiness_criteria_from(gates[sid]))
        for sid, row in inputs.items()
    }


@router.get("/placement-readiness", response_model=PlacementReadinessOut)
def placement_readiness(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> PlacementReadinessOut:
    """A weighted placement-readiness score against the criteria that resolve
    for this student — their course's, their college's, the programme's, or the
    hard-coded defaults (`app/criteria.py`). Rule-based; no model."""
    return compose_placement_readiness(db, _require_student(session))


class RecommendationOut(BaseModel):
    title: str
    why: str
    cta_label: str
    cta_route: str


class RecommendationsOut(BaseModel):
    items: list[RecommendationOut]


@router.get("/recommendations", response_model=RecommendationsOut)
def recommendations(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> RecommendationsOut:
    """Up to three rule-based next-skill (or fallback) recommendations, drawn from
    the catalogue skills the student does not yet hold. Never empty when any
    sensible recommendation exists. No model."""
    student_id = _require_student(session)

    held_ids = set(
        db.scalars(
            select(StudentSkill.skill_id).where(StudentSkill.student_id == student_id)
        ).all()
    )
    catalogue = db.scalars(select(Skill).order_by(Skill.category, Skill.name)).all()
    missing = [s for s in catalogue if s.id not in held_ids]

    items: list[RecommendationOut] = []
    for s in missing[:3]:
        items.append(
            RecommendationOut(
                title=f"Learn {s.name}",
                why=f"Unlocks your {s.category} badge",
                cta_label="Start",
                cta_route="/student/skilling",
            )
        )

    # Holds every catalogue skill (or the catalogue is empty): fall back to
    # completing the resume profile. This used to suggest finishing an
    # in-progress certification first, routed to the Certification Tracker —
    # removed with that screen on 2026-09-17.
    if not items:
        if _resume_pct(db, student_id) < 100:
            items.append(
                RecommendationOut(
                    title="Complete your resume profile",
                    why="A fuller resume profile means a stronger auto-generated CV",
                    cta_label="Complete",
                    cta_route="/student/resume",
                )
            )

    return RecommendationsOut(items=items[:3])
