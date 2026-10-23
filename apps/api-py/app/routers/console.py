"""The Main Admin console — programme-wide aggregates. Main Admin only; reuses
an admin.* capability per screen (Governance). Compute-only over existing data.
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy import true as sa_true
from sqlalchemy.orm import Session

from .. import batch_labels, job_scout
from ..config import settings
from ..db import get_db
from ..identity import get_current_session

from ..models.alert import AlertRuleConfig, AlertRuleKey, AlertSeverity
from ..models.attendance import AttendanceRecord
from ..models.badge import (
    BADGE_BY_CODE,
    BADGES,
    CATEGORY_LABEL,
    BadgeEvidence,
    EvidenceStatus,
    StudentBadge,
    StudentBadgeStatus,
)
from ..models.skill import Skill, StudentSkill
from ..models.timesheet import DayActivity, TimeSheetEntry
from ..models.time_ledger import (
    PRODUCTIVE,
    LedgerDayStatus,
    TimeLedgerCell,
    TimeLedgerDay,
)
from ..models.certification import Certification
from ..models.cohort import Cohort
from ..models.course import Course, Enrollment
from ..models.job import DegreeLevel, Job, JobApplication
from ..models.offer import OfferStatus, PlacementOffer
from ..models.placement_criteria import PlacementCriteria
from ..models.institution import (
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from ..criteria import (
    SOURCE_COLLEGE as CRITERIA_SOURCE_COLLEGE,
    SOURCE_COURSE as CRITERIA_SOURCE_COURSE,
    SOURCE_PROGRAMME as CRITERIA_SOURCE_PROGRAMME,
    ResolvedCriteria,
    from_row as criteria_from_row,
    as_payload as criteria_payload,
    resolve as resolve_criteria,
)
from ..models.registration import PENDING_QUEUE_STATUSES, Registration
from ..models.resume import Resume
from ..models.user import Mentor, Role, Student, User
# Rule 2's gate, imported rather than reimplemented — see its docstring for why
# there is exactly one of it.
from .mentor import _assert_can_access_student
# B8.5's cohort roll-up is the STUDENT'S OWN readiness rule applied to many
# students, imported rather than reimplemented in SQL — see
# `routers/student.py::_ReadinessInputs` for why the fetch and the decision
# were split rather than a second expression written here.
from .student import compose_readiness_many
from ..resume_pdf import render_resume_pdf
from ..architecture_events import record_change
from ..governance import require_capability
from ..policies import scope_filter
# B1.4. The projection of ONE reach onto `registrations`, and the response
# header that states the caller's scope. See app/scope_views.py for why
# neither belongs in policies.py.
from ..scope_views import job_scope_clause, registration_scope_clause, scope_header
# B12.1/B12.2. `jobs.status`' vocabulary and the track normalisation live beside
# the feeds that read them, not here: the console is the one WRITER of both and
# a second spelling of "open" would be invisible until a student's board emptied.
from ..jobs_visibility import STATUS_CLOSED, STATUS_OPEN, normalise_track
# B14. What an extract may carry and the receipt it leaves are decided in ONE
# module, shared with the badge export in routers/badge_verification.py — an
# export rule written twice is an export rule applied once.
from ..exports import (
    carries_personal_columns,
    csv_response,
    drop_personal,
    record_export,
    scope_note,
)
from ..models.account_events import ExportEvent

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# B8.4 REMOVED `GET /overview`, `GET /mail` and `GET /job-imports` from this
# module (2026-09-13), and the reason is worth leaving behind.
#
# All three were reachable, capability-gated and correct, and NOTHING CALLED
# ANY OF THEM: no Angular caller, and no client of any kind. `/overview` in
# particular computed five programme-wide counts with NO `scope_filter` on it
# at all, so it was the one admin aggregate a scoped grant could not narrow —
# a B1.4 hole kept open by an endpoint nobody was using. Its replacement is
# `GET /analytics-summary` below, which answers the same question through the
# reach and stamps the scope header.
#
# `MailLog` and `app/mailer.py` are UNTOUCHED: the mail is still recorded and
# still deduplicated: only the read endpoint is gone. `job_import_runs` went
# with its endpoint, and took `jobs.import_run_id` with it (migration
# f1a7c93d5e26) — B8.1's `import_runs` is the import provenance now, for the
# datasets an office actually imports.
# ---------------------------------------------------------------------------


class CohortOut(BaseModel):
    id: str
    code: str
    name: str
    batch_label: str
    #: The spine and the year put together by the one rule
    #: (`batch_labels.compose`): "General MBA - Finance · 2026-28". A batch
    #: is a YEAR (`name`); the course and specialization it belongs to are its
    #: links, so the three pickers this endpoint feeds cannot tell four batches
    #: apart without it — `name · batch_label`, which they used to build
    #: themselves, reads "2026-28 · 2026-28" now that the spine has left
    #: the name (a4e7c92d1f38).
    display_label: str
    degree_level: str
    student_count: int


@router.get("/cohorts", response_model=list[CohortOut])
def cohorts(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[CohortOut]:
    require_capability(db, session, "admin.analytics")
    counts = dict(
        db.execute(select(Student.cohort_id, func.count()).group_by(Student.cohort_id)).all()
    )
    rows = db.execute(
        select(Cohort, AcademicCourse.name, AcademicSpecialization.name)
        .outerjoin(AcademicCourse, AcademicCourse.id == Cohort.course_id)
        .outerjoin(AcademicSpecialization, AcademicSpecialization.id == Cohort.specialization_id)
        .order_by(Cohort.code)
    ).all()
    return [
        CohortOut(
            id=c.id,
            code=c.code,
            name=c.name,
            batch_label=c.batch_label,
            display_label=batch_labels.compose(
                course_name, spec_name, c.name, c.batch_label
            ),
            degree_level=c.degree_level.value,
            student_count=counts.get(c.id, 0),
        )
        for c, course_name, spec_name in rows
    ]


class CriteriaOut(BaseModel):
    """The gates that apply, and — since B8.2 — where they came from.

    THE FIRST NINE FIELDS ARE UNCHANGED, names and types, because
    `features/admin/imports/import-dataset.ts::PlacementCriteriaOut` and the
    Jobs sheet's posting form both read this shape today. Everything B8.2 adds
    is a new OPTIONAL field, so a client that has not been rebuilt reads exactly
    what it read before.
    """

    name: str
    active: bool
    min_cgpa: float
    max_live_backlogs: int
    max_gap_months: int
    min_attendance_pct: float
    min_reep_completion_pct: float
    min_cert_completion_pct: float
    require_core_certs: bool
    # ------------------------------------------------------------- B8.2 ----
    id: str | None = None
    #: `course`, `college` or `programme` — which rung of the chain answered.
    #: Never `defaults` on this endpoint: see the handler's docstring.
    source: str | None = None
    college_id: str | None = None
    course_id: str | None = None
    effective_from: date | None = None


def _criteria_out(resolved: ResolvedCriteria) -> CriteriaOut:
    return CriteriaOut(
        name=resolved.name or "Default",
        active=resolved.active,
        min_cgpa=resolved.min_cgpa,
        max_live_backlogs=resolved.max_live_backlogs,
        max_gap_months=resolved.max_gap_months,
        min_attendance_pct=resolved.min_attendance_pct,
        min_reep_completion_pct=resolved.min_reep_completion_pct,
        min_cert_completion_pct=resolved.min_cert_completion_pct,
        require_core_certs=resolved.require_core_certs,
        id=resolved.criteria_id,
        source=resolved.source,
        college_id=resolved.college_id,
        course_id=resolved.course_id,
        effective_from=resolved.effective_from,
    )


@router.get("/criteria", response_model=CriteriaOut)
# THE ANALYTICS SURFACE CHECKS A CAPABILITY, NOT A ROLE. The Main Admin holds
# every capability through ROLE_BASELINE, so for them this is identical to
# require_admin; the difference is a MENTOR an admin has granted
# admin.analytics to. It is PROGRAMME scope — no mentor group narrows it — which
# is why the console paints that grant red and demands a reason.
def criteria(
    course_id: str | None = None,
    college_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CriteriaOut:
    """The criteria the office has SET for this course, or 404 (B8.2).

    `?course_id=` / `?college_id=` walk `app/criteria.py`'s chain — course row,
    then college row, then the programme-wide row — so the screen can ask "what
    applies to the MBA" and get the row that actually governs it. Neither
    parameter given is the old question and the old answer: the programme-wide
    row.

    THE 404 STAYS, AND IT IS NOT THE SAME AS THE RESOLVER'S FALLBACK. This
    endpoint answers "what has somebody SET", and when the answer is nothing the
    honest reply is 404 — which the client already renders as "no criteria set
    yet" rather than as a row (`imports.component.ts` treats 404 as
    `criteriaState='none'`). The hard-coded defaults in `criteria.DEFAULTS` are
    what the ENGINE falls back to when it has to give a student a verdict; they
    are not something the office chose, and serving them here would make this
    screen show a configuration nobody typed. `tests/test_phase3_compatibility.py`
    pins the other half of the same sentence: whatever is served must never be a
    row of zeros.
    """
    require_capability(db, session, "admin.analytics")
    resolved = resolve_criteria(db, college_id=college_id, course_id=course_id)
    if resolved.is_default:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active placement criteria set."
        )
    return _criteria_out(resolved)


def _blank_is_none(value: str | None) -> str | None:
    """An empty or whitespace id is "not chosen", which is what a form's blank
    <select> option posts. Left as "", it skipped the existence checks below
    (`if body.course_id and ...` is falsy) and reached the INSERT as a foreign
    key to nothing: a 500 (DEF-004, testing/docs/05-incident-reports.md)."""
    if value is None:
        return None
    value = value.strip()
    return value or None


class CriteriaIn(BaseModel):
    """A new set of gates. Every threshold is optional and falls back to the
    RESOLVED set it replaces, so the screen can save one changed number without
    re-sending the other six and without a PATCH/PUT split."""

    name: str = "Default"
    college_id: str | None = None
    course_id: str | None = None
    effective_from: date | None = None
    min_cgpa: float | None = Field(default=None, ge=0, le=10)
    max_live_backlogs: int | None = Field(default=None, ge=0)
    max_gap_months: int | None = Field(default=None, ge=0)
    min_attendance_pct: float | None = Field(default=None, ge=0, le=100)
    min_reep_completion_pct: float | None = Field(default=None, ge=0, le=100)
    min_cert_completion_pct: float | None = Field(default=None, ge=0, le=100)
    require_core_certs: bool | None = None

    _ids = field_validator("college_id", "course_id")(_blank_is_none)


@router.post("/criteria", response_model=CriteriaOut, status_code=status.HTTP_201_CREATED)
def set_criteria(
    body: CriteriaIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> CriteriaOut:
    """Write a set of placement gates for a course, a college, or the programme.

    A NEW ROW EVERY TIME, NEVER AN EDIT IN PLACE. `placement_criteria` is the
    rule a student's eligibility verdict was decided under, and a verdict
    somebody was given in March must stay explicable in September. Superseding
    by INSERT plus `effective_from` is what makes the History list a history
    rather than a list with one row in it; editing in place would leave
    `updated_at` as the only trace and nothing to say what the numbers were.

    THE PREVIOUS ROW AT THE SAME RUNG IS DEACTIVATED, not deleted. Two live rows
    at one rung would be resolved by `updated_at` and the loser would sit in the
    list looking live. `active=False` is what the history renders as
    "superseded".

    THE SCOPE FENCE IS THE REACH. A college admin holding a college-scoped
    `admin.analytics` may write their own college's gates and not another's, and
    not the PROGRAMME-WIDE row — which governs every college on the deployment
    and is therefore the Main Admin's alone. That refusal is a 403 and says so.
    """
    require_capability(db, session, "admin.analytics")
    reach = scope_filter(db, session, "admin.analytics")
    if not reach.everything:
        if body.college_id is None and body.course_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "The programme-wide criteria apply to every college on this "
                    "deployment, so only the Main Admin may set them. Name a "
                    "college or a course instead."
                ),
            )
        if body.college_id and body.college_id not in reach.colleges:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your Analytics capability does not reach that college.",
            )
        if body.course_id:
            # THE COURSE IS CHECKED THROUGH ITS OWN COLLEGE, not by taking the
            # caller's word that it is theirs. A college-scoped holder naming
            # another college's course would otherwise write gates for students
            # they cannot see — the scope check reduced to "did you also send a
            # college_id", which is a fence with a gate in it.
            owning_college = db.scalar(
                select(Department.college_id)
                .select_from(AcademicCourse)
                .join(Department, AcademicCourse.department_id == Department.id)
                .where(AcademicCourse.id == body.course_id)
            )
            if body.course_id not in reach.courses and owning_college not in reach.colleges:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your Analytics capability does not reach that course.",
                )
    if body.college_id and db.get(College, body.college_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="College not found.")
    if body.course_id and db.get(AcademicCourse, body.course_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found.")

    # What this set replaces — so an omitted threshold keeps the number that was
    # already governing these students rather than snapping to the fallback.
    current = resolve_criteria(db, college_id=body.college_id, course_id=body.course_id)
    values = {
        field: (
            getattr(body, field)
            if getattr(body, field) is not None
            else getattr(current, field)
        )
        for field in (
            "min_cgpa",
            "max_live_backlogs",
            "max_gap_months",
            "min_attendance_pct",
            "min_reep_completion_pct",
            "min_cert_completion_pct",
            "require_core_certs",
        )
    }

    superseded = db.scalars(
        select(PlacementCriteria).where(
            PlacementCriteria.active.is_(True),
            PlacementCriteria.college_id.is_(body.college_id)
            if body.college_id is None
            else PlacementCriteria.college_id == body.college_id,
            PlacementCriteria.course_id.is_(body.course_id)
            if body.course_id is None
            else PlacementCriteria.course_id == body.course_id,
        )
    ).all()
    for row in superseded:
        row.active = False

    written = PlacementCriteria(
        name=body.name,
        active=True,
        college_id=body.college_id,
        course_id=body.course_id,
        effective_from=body.effective_from,
        created_by=session.get("userId"),
        **values,
    )
    db.add(written)
    db.flush()
    record_change(
        db,
        session=session,
        request=request,
        tenant_id=None,
        entity_type="placement_criteria",
        entity_id=written.id,
        action="CRITERIA_SET",
        before={"superseded": [row.id for row in superseded], **criteria_payload(current)},
        after={
            "name": body.name,
            "college_id": body.college_id,
            "course_id": body.course_id,
            "effective_from": body.effective_from.isoformat() if body.effective_from else None,
            **values,
        },
        event_type="criteria.set",
        payload={"criteria_id": written.id, "college_id": body.college_id,
                 "course_id": body.course_id},
    )
    db.commit()
    db.refresh(written)
    return _criteria_out(_criteria_resolved_row(written))


#: How many sets of gates the History list answers with. A rung acquires one row
#: per policy change, so a decade of them fits.
MAX_CRITERIA_HISTORY = 100


class CriteriaHistoryRow(CriteriaOut):
    updated_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None


@router.get("/criteria/history", response_model=list[CriteriaHistoryRow])
def criteria_history(
    course_id: str | None = None,
    college_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[CriteriaHistoryRow]:
    """Every set of gates ever written for this rung, newest first (B8.2).

    UNFILTERED IT IS THE WHOLE TABLE, which is the answer the office's History
    button wants: "what have we ever set, and when". `?course_id=` / `?college_id=`
    narrow to one rung. Superseded rows are IN the list and marked `active:
    false` — a history that hid them would be a list of one row.
    """
    require_capability(db, session, "admin.analytics")
    query = select(PlacementCriteria, User.name).outerjoin(
        User, PlacementCriteria.created_by == User.id
    )
    if course_id:
        query = query.where(PlacementCriteria.course_id == course_id)
    if college_id:
        query = query.where(PlacementCriteria.college_id == college_id)
    rows = db.execute(
        query.order_by(
            PlacementCriteria.effective_from.desc().nullslast(),
            PlacementCriteria.updated_at.desc(),
        ).limit(MAX_CRITERIA_HISTORY)
    ).all()
    return [
        CriteriaHistoryRow(
            **_criteria_out(_criteria_resolved_row(row)).model_dump(),
            updated_at=row.updated_at,
            created_by=row.created_by,
            created_by_name=name,
        )
        for row, name in rows
    ]


def _criteria_resolved_row(row: PlacementCriteria) -> ResolvedCriteria:
    """One stored row, in the resolver's shape, labelled by the rung it hangs on.

    Goes through `criteria.from_row` rather than building the dataclass here,
    so the write endpoint and the resolver cannot disagree about what a row
    means — the same discipline as one fallback chain.
    """
    if row.course_id:
        source = CRITERIA_SOURCE_COURSE
    elif row.college_id:
        source = CRITERIA_SOURCE_COLLEGE
    else:
        source = CRITERIA_SOURCE_PROGRAMME
    return criteria_from_row(row, source)


class AlertRuleOut(BaseModel):
    id: str
    cohort_id: str
    rule_key: str
    enabled: bool
    params: dict
    severity: str


def _alert_rule_out(r: AlertRuleConfig) -> AlertRuleOut:
    return AlertRuleOut(
        id=r.id,
        cohort_id=r.cohort_id,
        rule_key=r.rule_key.value,
        enabled=r.enabled,
        params=r.params,
        severity=r.severity.value,
    )


@router.get("/alert-rules", response_model=list[AlertRuleOut])
def alert_rules(
    cohort_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AlertRuleOut]:
    """The admin-configurable alert thresholds, optionally scoped to a cohort."""
    require_capability(db, session, "admin.analytics")
    query = select(AlertRuleConfig)
    if cohort_id:
        query = query.where(AlertRuleConfig.cohort_id == cohort_id)
    rows = db.scalars(query.order_by(AlertRuleConfig.cohort_id, AlertRuleConfig.rule_key)).all()
    return [_alert_rule_out(r) for r in rows]


class AlertRuleIn(BaseModel):
    cohort_id: str
    rule_key: str
    params: dict
    enabled: bool = True
    severity: str = "WARNING"


@router.put("/alert-rules", response_model=AlertRuleOut)
def upsert_alert_rule(
    body: AlertRuleIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AlertRuleOut:
    """Create or update the threshold for one (cohort, rule) — the config lives
    in data, so tuning it never needs a deploy."""
    require_capability(db, session, "admin.analytics")
    if db.get(Cohort, body.cohort_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cohort not found.")
    try:
        rule_key = AlertRuleKey(body.rule_key)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown rule_key."
        )
    try:
        severity = AlertSeverity(body.severity)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown severity."
        )

    row = db.scalar(
        select(AlertRuleConfig).where(
            AlertRuleConfig.cohort_id == body.cohort_id,
            AlertRuleConfig.rule_key == rule_key,
        )
    )
    if row is None:
        row = AlertRuleConfig(cohort_id=body.cohort_id, rule_key=rule_key)
        db.add(row)
    row.params = body.params
    row.enabled = body.enabled
    row.severity = severity
    db.commit()
    db.refresh(row)
    return _alert_rule_out(row)


# --- catalogue ------------------------------------------------------------


class CatalogueCertOut(BaseModel):
    code: str
    name: str
    provider: str
    required_hours: float
    is_optional: bool
    link: str | None


class CatalogueCourseOut(BaseModel):
    code: str
    name: str
    stage: str
    dimension: str
    semester: int
    teaching_hours: float
    self_learning_hours_required: float
    model_type: str
    duration_weeks: int
    # Students with an Enrollment row on this course, any status. The catalogue
    # table's "Enrolled" column; a course nobody is on is a real finding.
    enrolled: int
    certifications: list[CatalogueCertOut]


@router.get("/catalogue", response_model=list[CatalogueCourseOut])
def catalogue(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[CatalogueCourseOut]:
    """The programme as designed: every course with the certifications mapped to
    it. Nested rather than two flat lists, because a certification only means
    anything against the course it certifies — the screen's whole question is
    which courses have evidence attached and which do not."""
    require_capability(db, session, "admin.catalogue")
    courses = db.scalars(select(Course).order_by(Course.semester, Course.code)).all()
    enrolled = {
        code: n
        for code, n in db.execute(
            select(Enrollment.course_code, func.count()).group_by(Enrollment.course_code)
        ).all()
    }
    certs: dict[str, list[CatalogueCertOut]] = {}
    for c in db.scalars(select(Certification).order_by(Certification.name)).all():
        certs.setdefault(c.course_code, []).append(
            CatalogueCertOut(
                code=c.code,
                name=c.name,
                provider=c.provider,
                required_hours=c.required_hours,
                is_optional=c.is_optional,
                link=c.link,
            )
        )
    return [
        CatalogueCourseOut(
            code=c.code,
            name=c.name,
            stage=c.stage.value,
            dimension=c.dimension.value,
            semester=c.semester,
            teaching_hours=c.teaching_hours,
            self_learning_hours_required=c.self_learning_hours_required,
            model_type=c.model_type.value,
            duration_weeks=c.duration_weeks,
            enrolled=enrolled.get(c.code, 0),
            certifications=certs.get(c.code, []),
        )
        for c in courses
    ]


# --- jobs sheet -----------------------------------------------------------


class JobSheetOut(BaseModel):
    id: str
    title: str
    company: str
    degree_level: str
    location: str | None
    apply_url: str | None
    required_skills: list[str]
    posted_on: datetime
    closes_on: datetime | None
    min_cgpa: float | None
    max_live_backlogs: int | None
    # How many students have applied. The sheet's real question is which
    # postings are working, and a posting nobody applied to looks identical to a
    # healthy one without this.
    applicants: int
    # B12.1. Where the posting is offered. NULL on either means EVERY college /
    # every course — see `app/jobs_visibility.py` for why that is the reading
    # rather than "nobody", and the labels so the grid can print a name without
    # a second round trip per row.
    college_id: str | None
    college_name: str | None
    course_id: str | None
    course_name: str | None
    tracks: list[str]
    # B12.2. `open` or `closed`, as the office asserts it. NOT derived from
    # `closes_on`, which the grid already reads and which answers the other
    # question — see `close_job` below.
    status: str
    # `office` for a posting typed on this sheet, else the board the daily job
    # agent found it on (`adzuna`, `jooble`) — app/job_scout.py. An agent
    # posting is closed, never removed: see `delete_job`.
    source: str = "office"


@router.get("/jobs", response_model=list[JobSheetOut])
def jobs_sheet(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[JobSheetOut]:
    """Every posting this caller may see, newest first, with its application count.

    SCOPED (B1.4) FOR THE FIRST TIME, because until B12.1 there was nothing to
    scope by: `console.placement`'s docstring said in as many words that jobs
    carry no institution and therefore could not be one of B1.4's targets. They
    carry one now, and the projection is `scope_views.job_scope_clause` — read
    its docstring before changing what a NULL college means here, because it is
    the deliberate opposite of the import-run rule directly above it.
    """
    require_capability(db, session, "admin.jobs")
    reach = scope_filter(db, session, "admin.jobs")
    scope_header(response, reach)
    if reach.nothing:
        return []
    counts = {
        jid: n
        for jid, n in db.execute(
            select(JobApplication.job_id, func.count()).group_by(JobApplication.job_id)
        ).all()
    }
    rows = db.scalars(
        select(Job).where(job_scope_clause(reach)).order_by(Job.posted_on.desc())
    ).all()
    labels = _spine_labels(db, rows)
    return [_job_sheet_row(j, counts.get(j.id, 0), labels) for j in rows]


class ScoutSourceOut(BaseModel):
    key: str
    name: str
    configured: bool


class ScoutStatusOut(BaseModel):
    """What the daily job agent last did, for the line at the top of the sheet.

    `last_run_at` NULL means it has never written a run on this deployment,
    which is a different sentence from a run that found nothing; and
    `active` false with a run on record is an agent somebody switched off."""

    active: bool
    sources: list[ScoutSourceOut]
    last_run_at: datetime | None
    created: int | None
    closed: int | None
    errors: dict[str, str]


@router.get("/jobs/scout-status", response_model=ScoutStatusOut)
def jobs_scout_status(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ScoutStatusOut:
    """The latest run's receipt (one SYSTEM audit row per run) and which boards
    have a key here. The counts are programme-wide by construction, a run being
    one act over every college, and they are numbers, never a posting."""
    require_capability(db, session, "admin.jobs")
    row = job_scout.latest_run(db)
    body = (row.after_json or {}) if row else {}
    return ScoutStatusOut(
        active=settings.job_scout_active,
        sources=[ScoutSourceOut(**source) for source in job_scout.status_sources()],
        last_run_at=row.occurred_at if row else None,
        created=body.get("created") if row else None,
        closed=body.get("closed") if row else None,
        errors={str(k): str(v) for k, v in (body.get("errors") or {}).items()},
    )


def _spine_labels(db: Session, rows) -> dict[str, str]:
    """`{id: name}` for every college and course the given postings name.

    Two queries for the page rather than two per row, and `id -> name` in one
    dict because the two id spaces are both uuid hex and cannot collide. A
    posting naming a college that has since been archived still resolves, which
    is why this reads the rows rather than trusting a join that a narrowed
    query would have dropped.
    """
    college_ids = {j.college_id for j in rows if j.college_id}
    course_ids = {j.course_id for j in rows if j.course_id}
    labels: dict[str, str] = {}
    if college_ids:
        labels |= {
            cid: name
            for cid, name in db.execute(
                select(College.id, College.name).where(College.id.in_(college_ids))
            ).all()
        }
    if course_ids:
        labels |= {
            cid: name
            for cid, name in db.execute(
                select(AcademicCourse.id, AcademicCourse.name).where(
                    AcademicCourse.id.in_(course_ids)
                )
            ).all()
        }
    return labels


def _job_sheet_row(j: Job, applicants: int, labels: dict[str, str] | None = None) -> JobSheetOut:
    labels = labels or {}
    return JobSheetOut(
        id=j.id,
        title=j.title,
        company=j.company,
        degree_level=j.degree_level.value,
        location=j.location,
        apply_url=j.apply_url,
        required_skills=list(j.required_skills or []),
        posted_on=j.posted_on,
        closes_on=j.closes_on,
        min_cgpa=j.min_cgpa,
        max_live_backlogs=j.max_live_backlogs,
        applicants=applicants,
        college_id=j.college_id,
        college_name=labels.get(j.college_id) if j.college_id else None,
        course_id=j.course_id,
        course_name=labels.get(j.course_id) if j.course_id else None,
        tracks=list(j.tracks or []),
        status=j.status,
        source=job_scout.job_source(j),
    )


class JobIn(BaseModel):
    """A new opening, as the Jobs sheet's form asks for it: role, company,
    level, location and a closing date. The eligibility gates (min CGPA, live
    backlogs) are deliberately NOT here — they default to the programme's
    placement criteria, and a form that invites a per-posting override is how a
    cut-off gets set a notch too high by accident (see the sheet's own note)."""

    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    degree_level: str = "PG"
    location: str | None = Field(default=None, max_length=200)
    closes_on: date | None = None
    apply_url: str | None = Field(default=None, max_length=1000)
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    # B12.1. All three OPTIONAL, and omitting them is a real choice rather than
    # an unfinished form: a posting with no college, no course and no track is
    # published to everybody, which is what every posting on every deployment is
    # today. The form's help text says so; `app/jobs_visibility.py` is why.
    college_id: str | None = None
    course_id: str | None = None
    tracks: list[str] = Field(default_factory=list, max_length=20)

    _ids = field_validator("college_id", "course_id")(_blank_is_none)


@router.post("/jobs", response_model=JobSheetOut, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobIn,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> JobSheetOut:
    """Publish an opening to the sheet. Visible to students and alumni at once —
    both boards read the same `jobs` table, which is what "publish" means here."""
    require_capability(db, session, "admin.jobs")
    try:
        level = DegreeLevel(body.degree_level.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Level must be PG or UG."
        )
    apply_url = (body.apply_url or "").strip() or None
    if apply_url and not apply_url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The apply link must start with http:// or https://.",
        )
    # B12.1. A NAMED RUNG MUST EXIST. An id that resolves to nothing is stored
    # as a posting nobody can see — every feed matches a college the viewer
    # cannot have — which reads on the sheet as a posting that published fine
    # and on every student's screen as nothing at all. The same reasoning as
    # `_target_label` on a governance grant: two existence checks against the
    # same table is how one of them ends up accepting a rung the other refuses,
    # so the message names the rung rather than the column.
    if body.college_id and db.get(College, body.college_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That college is not on the roll.",
        )
    if body.course_id and db.get(AcademicCourse, body.course_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That course is not in the catalogue.",
        )
    # Normalised here, at the ONE writer, so the feeds compare a plain equality
    # against a stored value rather than lowering both sides per row on a column
    # no index could then serve. Duplicates collapse and order is kept.
    tracks: list[str] = []
    for raw in body.tracks:
        code = normalise_track(raw)
        if code and code not in tracks:
            tracks.append(code)
    now = datetime.now(timezone.utc)
    closes = (
        datetime(body.closes_on.year, body.closes_on.month, body.closes_on.day, 23, 59, tzinfo=timezone.utc)
        if body.closes_on
        else None
    )
    job = Job(
        title=body.title.strip(),
        company=body.company.strip(),
        degree_level=level,
        location=(body.location or "").strip() or None,
        apply_url=apply_url,
        required_skills=[skill.strip() for skill in body.required_skills if skill.strip()],
        posted_on=now,
        closes_on=closes,
        college_id=body.college_id,
        course_id=body.course_id,
        tracks=tracks,
        status=STATUS_OPEN,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_sheet_row(job, 0, _spine_labels(db, [job]))


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    """Remove a posting. REFUSED once anyone has applied: job_applications
    cascade on delete, and a student's application is part of their record —
    the row a mentor reads when the student says "I applied to TCS". A posting
    that has done its job stays on the sheet as history."""
    require_capability(db, session, "admin.jobs")
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posting not found.")
    # An agent posting is CLOSED, never removed. Its row is the only memory
    # that the advert was seen; deleting it would let the next morning's run
    # post the same advert again, which is the opposite of what the office
    # meant by pressing Remove.
    if job_scout.is_agent_posting(job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The job agent found this posting, so it cannot be removed: the agent would "
                "post it again tomorrow. Close it instead and it stays off both boards."
            ),
        )
    applicants = (
        db.scalar(
            select(func.count()).select_from(JobApplication).where(JobApplication.job_id == job_id)
        )
        or 0
    )
    if applicants:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{applicants} student{'s have' if applicants != 1 else ' has'} applied to this "
                "posting, so it cannot be removed — the applications are part of their records."
            ),
        )
    db.delete(job)
    db.commit()


@router.post("/jobs/{job_id}/close", response_model=JobSheetOut)
def close_job(
    job_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> JobSheetOut:
    """Withdraw a posting from the two candidate boards (B12.2).

    THE COLUMN AND THE DATE ANSWER DIFFERENT QUESTIONS, and this is the decision
    the jobs grid's own comment asked for. `closes_on` is the deadline printed
    on the posting, which the grid already derives a state from; `status` is the
    office saying the recruiter has withdrawn it, which a date cannot express
    and cannot be back-dated into without lying about when applications closed.
    So the column does NOT override the date and is not derived from it: the
    feeds filter on `status = 'open'` alone (`app/jobs_visibility.py` says why
    filtering on both would remove postings students can see today), and the
    grid goes on printing the date beside it.

    NOT A DELETE, and the difference is the whole point. `DELETE /admin/jobs/
    {id}` still refuses with 409 once anybody has applied, because the
    applications are part of those students' records; closing is the action that
    was missing for exactly that posting — the one that has done its work and
    must come off the board without taking a student's history with it.

    IDEMPOTENT. Closing a closed posting returns it unchanged and writes no
    second audit event: the control is a toolbar button over a grid selection
    and a double-tap is a double-tap, not a second decision.

    ONE-WAY ON PURPOSE, FOR NOW — there is no reopen, because the board draws no
    control for one and inventing an endpoint with no caller is what B8.4 spent
    this phase deleting. A posting closed in error is republished, which leaves
    the applications against the original where they belong.
    """
    require_capability(db, session, "admin.jobs")
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posting not found.")
    # The same fence the list applies: a narrowed holder who cannot see a
    # posting must not be able to close it by typing its id. `reaches_target`
    # has no shape for a posting (it is not a rung of the spine), so the reach
    # is asked the same question the sheet asks, through the same clause.
    reach = scope_filter(db, session, "admin.jobs")
    if reach.nothing or db.scalar(
        select(Job.id).where(Job.id == job_id, job_scope_clause(reach))
    ) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Posting not found.")

    applicants = (
        db.scalar(
            select(func.count()).select_from(JobApplication).where(JobApplication.job_id == job_id)
        )
        or 0
    )
    if job.status != STATUS_CLOSED:
        job.status = STATUS_CLOSED
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="job", entity_id=job.id, action="JOB_CLOSED",
            before={"status": STATUS_OPEN}, after={"status": STATUS_CLOSED},
            event_type="job.closed",
            payload={"job_id": job.id, "company": job.company, "applicants": applicants},
        )
        db.commit()
        db.refresh(job)
    return _job_sheet_row(job, applicants, _spine_labels(db, [job]))


# --- the analytics header and stat tiles ------------------------------------


def _modal_semester(db: Session, reach=None) -> int | None:
    """The semester most students are in, or None with no students. There is no
    programme-wide "current semester" row; the header says the one that holds
    for most of the cohort rather than inventing a setting for it.

    NARROWED BY THE CALLER'S REACH when one is given (B1.4): the modal semester
    of a department is a different number from the modal semester of the
    programme, and a scoped screen showing the programme's would be quietly
    describing students the reader cannot see. `reach=None` keeps the
    programme-wide answer for the callers that have not resolved one.
    """
    stmt = select(Student.current_semester, func.count())
    if reach is not None and not reach.everything:
        stmt = stmt.where(Student.id.in_(reach.student_ids()))
    row = db.execute(
        stmt.group_by(Student.current_semester)
        .order_by(func.count().desc(), Student.current_semester.desc())
        .limit(1)
    ).first()
    return int(row[0]) if row else None


class AnalyticsSummaryOut(BaseModel):
    students_total: int
    #: Applications still waiting on a human: PENDING_REVIEW **or** HOLD
    #: (B11.2). A hold is a bookmark with a note, not an outcome, so the work is
    #: still owed; counting only PENDING_REVIEW would make this tile fall the
    #: moment somebody pressed Hold and report progress that nobody made.
    pending_registrations: int
    mentors_total: int
    # Assigned students per mentor. None with no mentors — an average over
    # nobody is not zero.
    mentees_per_mentor: float | None
    badges_awarded: int
    evidence_awaiting_verification: int
    placed_students: int
    placement_percent: float
    approved_offers: int
    semester: int | None
    generated_at: datetime


@router.get("/analytics-summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnalyticsSummaryOut:
    """The four tiles across the top of Programme analytics, in one call.

    SCOPED (B1.4). A count is a smaller leak than a list and it is still a leak:
    "your college has 41 students" told to somebody granted Analytics for one
    department of it is a number they can subtract. Every tile is narrowed
    through the SAME reach, so the percentages stay consistent with each other —
    a scoped numerator over a programme-wide denominator would draw a placement
    rate nobody can reproduce from any screen.

    THE MENTOR COUNT IS FACULTY, NOT GROUPS. It used to count `mentors` rows,
    which are groups; scoping it needs the account behind the group anyway, and
    counting accounts is what `mentees_per_mentor` has always divided by on
    every deployment where the two agree. They disagree only for a `Mentor` row
    whose user has been purged, which is a row `purge_people` removes.

    `B8.5`'s analytics SERIES endpoints do not exist yet. This is the only
    analytics route there is, so it is the only one there was to scope.
    """
    require_capability(db, session, "admin.analytics")
    reach = scope_filter(db, session, "admin.analytics")
    scope_header(response, reach)

    def count(stmt) -> int:
        return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    if reach.nothing:
        return AnalyticsSummaryOut(
            students_total=0, pending_registrations=0, mentors_total=0,
            mentees_per_mentor=None, badges_awarded=0,
            evidence_awaiting_verification=0, placed_students=0,
            placement_percent=0.0, approved_offers=0, semester=None,
            generated_at=datetime.now(timezone.utc),
        )

    in_reach = Student.id.in_(reach.student_ids())
    total = count(select(Student.id).where(in_reach))
    # PENDING_REVIEW **OR** HOLD, through the one `PENDING_QUEUE_STATUSES` the
    # model declares (B11.2). A held application is work the office still owes
    # somebody — parked with a note, not finished — and counting only
    # PENDING_REVIEW would drop this tile the moment a reviewer pressed Hold,
    # reporting a queue getting shorter when nothing had been decided. The
    # Registrations screen splits the two into tabs because it has room to; this
    # tile is one number and must mean "waiting on us".
    pending_regs = count(
        select(Registration.id).where(
            Registration.status.in_(PENDING_QUEUE_STATUSES),
            registration_scope_clause(reach),
        )
    )
    mentors = count(
        select(User.id).where(User.role == Role.MENTOR, User.id.in_(reach.user_ids()))
    )
    assigned = count(select(Student.id).where(in_reach, Student.mentor_id.is_not(None)))
    badges = count(
        select(StudentBadge.id).where(
            StudentBadge.status == StudentBadgeStatus.EARNED,
            StudentBadge.student_id.in_(reach.student_ids()),
        )
    )
    awaiting = count(
        select(BadgeEvidence.id).where(
            BadgeEvidence.status == EvidenceStatus.PENDING_VERIFICATION,
            BadgeEvidence.student_id.in_(reach.student_ids()),
        )
    )
    approved_offers = count(
        select(PlacementOffer.id).where(
            PlacementOffer.status == OfferStatus.APPROVED,
            PlacementOffer.student_id.in_(reach.student_ids()),
        )
    )
    placed = (
        db.scalar(
            select(func.count(func.distinct(PlacementOffer.student_id))).where(
                PlacementOffer.status == OfferStatus.APPROVED,
                PlacementOffer.student_id.in_(reach.student_ids()),
            )
        )
        or 0
    )
    return AnalyticsSummaryOut(
        students_total=total,
        pending_registrations=pending_regs,
        mentors_total=mentors,
        mentees_per_mentor=round(assigned / mentors, 1) if mentors else None,
        badges_awarded=badges,
        evidence_awaiting_verification=awaiting,
        placed_students=placed,
        placement_percent=round(100 * placed / total, 1) if total else 0.0,
        approved_offers=approved_offers,
        semester=_modal_semester(db, reach),
        generated_at=datetime.now(timezone.utc),
    )


# --- one student, read from the mentorship map ------------------------------

#: Weeks of history the analytics detail draws, this week included.
WEEKLY_WINDOW = 6


class WeekOut(BaseModel):
    label: str
    start: date
    end: date


class SkillCategoryOut(BaseModel):
    category: str
    count: int


class StudentWeeklyOut(BaseModel):
    student_id: str
    name: str
    usn: str | None
    weekly_hour_target: float
    # Whether "Download CV" has anything to download — checked here so the
    # button is never drawn for a student with no resume on record.
    has_resume: bool
    weeks: list[WeekOut]
    # Per week, present / total sessions. None for a week with no sessions at
    # all — that is "no classes", not 0 % attendance.
    attendance_percent: list[float | None]
    # Per week, hours entered in the Time Allocation Ledger.
    logged_hours: list[float]
    # Verified skills, grouped by the catalogue's category — the one honest
    # breakdown of "skill badges" for one student. Verification carries no
    # timestamp, so there is no weekly series for it.
    skills_by_category: list[SkillCategoryOut]


@router.get("/students/{student_id}/weekly", response_model=StudentWeeklyOut)
def student_weekly(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StudentWeeklyOut:
    """The last six ISO weeks of one student's attendance and ledger hours —
    what the analytics bar chart draws when a student arc is clicked.

    BOTH GATES, and it used to be one. `admin.analytics` is GRANTABLE: the Main
    Admin can hand the Analytics screen to a faculty member in Governance. This
    docstring said "Main Admin only (rule 2: they see all)" while the code
    checked the capability alone — so a MENTOR granted that screen could read
    ANY student's six-week attendance and ledger series by putting their id in
    the path, including students in another mentor's group.

    app/governance.py states the rule this broke: the capability decides WHICH
    SCREENS, `_assert_can_access_student` decides WHICH STUDENTS, "and a
    capability can never relax the student filter". The Main Admin still sees
    every student, because that is what the scope gate says for ADMIN; a granted
    mentor now sees their own.
    """
    require_capability(db, session, "admin.analytics")
    _assert_can_access_student(session, student_id, db)
    return compose_student_weekly(db, student_id)


def compose_student_weekly(db: Session, student_id: str) -> StudentWeeklyOut:
    """Six ISO weeks of one student's attendance and ledger hours. Shared with
    the office's Student 360 read (`routers/admin_student_record.py`); the
    caller has already run both fences."""
    row = db.execute(
        select(Student, User.name)
        .join(User, Student.user_id == User.id)
        .where(Student.id == student_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    student, name = row

    today = datetime.now(timezone.utc).date()
    this_monday = today - timedelta(days=today.weekday())
    starts = [this_monday - timedelta(weeks=WEEKLY_WINDOW - 1 - i) for i in range(WEEKLY_WINDOW)]
    window_start = starts[0]

    def bucket(d: date) -> int | None:
        idx = (d - window_start).days // 7
        return idx if 0 <= idx < WEEKLY_WINDOW else None

    present = [0] * WEEKLY_WINDOW
    total = [0] * WEEKLY_WINDOW
    for session_date, was_present in db.execute(
        select(AttendanceRecord.session_date, AttendanceRecord.present).where(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.session_date >= datetime(
                window_start.year, window_start.month, window_start.day, tzinfo=timezone.utc
            ),
        )
    ).all():
        idx = bucket(session_date.date())
        if idx is None:
            continue
        total[idx] += 1
        if was_present:
            present[idx] += 1

    halves = [0] * WEEKLY_WINDOW
    for day, half in db.execute(
        select(TimeLedgerDay.day, func.sum(TimeLedgerCell.half_hours))
        .join(TimeLedgerCell, TimeLedgerCell.ledger_day_id == TimeLedgerDay.id)
        .where(TimeLedgerDay.student_id == student_id, TimeLedgerDay.day >= window_start)
        .group_by(TimeLedgerDay.day)
    ).all():
        idx = bucket(day)
        if idx is not None:
            halves[idx] += int(half or 0)

    skills = db.execute(
        select(Skill.category, func.count())
        .join(StudentSkill, StudentSkill.skill_id == Skill.id)
        .where(StudentSkill.student_id == student_id, StudentSkill.verified.is_(True))
        .group_by(Skill.category)
        .order_by(func.count().desc(), Skill.category)
    ).all()

    has_resume = (
        db.scalar(select(func.count()).select_from(Resume).where(Resume.student_id == student_id))
        or 0
    ) > 0

    return StudentWeeklyOut(
        student_id=student.id,
        name=name,
        usn=student.usn,
        weekly_hour_target=student.weekly_hour_target,
        has_resume=has_resume,
        weeks=[
            # f"{d.day}" rather than strftime("%-d"): the %-d directive is a
            # glibc extension. It renders "6 Sep" on Linux and raises
            # ValueError("Invalid format string") on Windows, so this endpoint
            # passed in CI and 500-ed on every developer machine. Guarded now by
            # tests/test_codebase_guards.py::test_no_platform_specific_strftime.
            WeekOut(
                label=f"{start.day} {start:%b}",
                start=start,
                end=start + timedelta(days=6),
            )
            for start in starts
        ],
        attendance_percent=[
            round(100 * present[i] / total[i], 1) if total[i] else None
            for i in range(WEEKLY_WINDOW)
        ],
        logged_hours=[h / 2 for h in halves],
        skills_by_category=[SkillCategoryOut(category=c, count=n) for c, n in skills],
    )


# --------------------------------------------------------------------------- #
# B8.5 — the weekly series and the KPI strip
#
# WHY EVERY NUMBER HERE IS NULLABLE AND EVERY SERIES CARRIES ITS OWN SOURCE.
# The Analytics board asks for four weekly series and six KPIs. Three of the
# series and three of the KPIs are answerable from the tables that exist; the
# rest are not, and each is not-answerable for a DIFFERENT reason. The shape
# below makes the reason travel with the number, because the alternative — a
# zero, or a series of zeros — is indistinguishable on a chart from a real
# collapse, and the office would act on it.
#
# READINESS HAS NO HISTORY, and that is the sharp one. `placement_readiness` is
# computed from the state of a student's records AS THEY ARE NOW: there is no
# record of what their CGPA or attendance was in week 7, so "readiness % in week
# 7" cannot be recovered from any table in this database. It is answerable for
# the CURRENT week and for no other, until `analytics_snapshots` (B8.6) has been
# written for a few weeks by a nightly job — which is an EventBridge schedule,
# i.e. an infrastructure change nobody has approved. So this endpoint computes
# every point it can and marks the readiness series `partial`, with the last
# point filled and a sentence saying why the rest are empty. It does NOT read
# `analytics_snapshots`: nothing writes that table yet, and a read path against
# an empty table is a chart that silently shows nothing.
#
# SCOPED (B1.4), through the same reach as the tiles beside them. The ratios on
# this screen must be reproducible from each other; a series narrowed
# differently from `analytics-summary` would draw an attendance line that does
# not match the attendance the same reader sees on the roster.
# --------------------------------------------------------------------------- #

#: The longest window the series will answer for. Twelve weeks is the board's
#: default; the cap is here because `?weeks=` is a query parameter and an
#: unbounded one is a full table scan of `attendance_records` an anonymous
#: typo can ask for.
MAX_SERIES_WEEKS = 52

#: A readiness score at or above this is "placement ready" — the floor of
#: `routers/student.py::_readiness_band`'s top band, named once here so the
#: cohort percentage and the word on the student's own card cannot drift.
PLACEMENT_READY_SCORE = 80

#: Above this many students in reach, the readiness roll-up is not attempted.
#: `compose_readiness_many` is six queries however large the set, so the bound
#: is on the Python pass and the memory it holds, not on the database. Reported
#: as unavailable WITH the count rather than silently truncated: a percentage
#: over the first two thousand students of a larger set is a number nobody can
#: reproduce.
MAX_READINESS_ROLLUP = 5000


#: The four lines, in the order the board draws them. Named once so the
#: refused/empty branch below hands back the SAME four series, dashed — a chart
#: that loses its legend when a reader has no access reads as a screen that
#: failed to load rather than as an answer.
_SERIES_SHAPE = (
    ("attendance_pct", "Attendance %", "percent"),
    ("skilling_hours", "Skilling hours", "hours"),
    ("offers", "Offers approved", "count"),
    ("readiness_pct", "Placement ready %", "percent"),
)


class SeriesOut(BaseModel):
    key: str
    label: str
    #: `percent`, `hours` or `count` — the client picks an axis from this rather
    #: than from the key, so a fifth series does not need a client change to
    #: render with the right suffix.
    unit: str
    #: One point per week, oldest first, aligned to `weeks`. NULL means NOT
    #: MEASURED for that week — no sessions recorded, no readiness history —
    #: never zero. A zero here is a real zero.
    points: list[float | None]
    #: `live` (recomputed from rows now), `partial` (only some weeks can be
    #: answered) or `unavailable` (nothing can answer it). Three words, for
    #: `scope_views`' reason: "measured and zero" and "not measurable" are
    #: opposite facts and must not render the same.
    source: str
    #: Present whenever `source` is not `live`, and shown on the chart.
    note: str | None = None


class AnalyticsSeriesOut(BaseModel):
    weeks: list[WeekOut]
    series: list[SeriesOut]
    #: How many students the reach covers — the denominator every percentage
    #: above was taken over, so the reader can tell a flat line from an empty one.
    students_in_reach: int
    generated_at: datetime


def _week_starts(weeks: int, today: date) -> list[date]:
    """The ISO Monday of each week in the window, oldest first, this week last."""
    this_monday = today - timedelta(days=today.weekday())
    return [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]


@router.get("/analytics/series", response_model=AnalyticsSeriesOut)
def analytics_series(
    response: Response,
    weeks: int = 12,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnalyticsSeriesOut:
    """The Analytics board's weekly lines: attendance %, skilling hours, offers
    and (for this week only) placement readiness.

    Same gate and same reach as `analytics-summary`, deliberately: these are the
    same students counted a different way, and two capabilities over one screen
    is how a reader ends up with a chart they can see and a tile they cannot.
    """
    require_capability(db, session, "admin.analytics")
    reach = scope_filter(db, session, "admin.analytics")
    scope_header(response, reach)

    weeks = max(1, min(int(weeks), MAX_SERIES_WEEKS))
    today = datetime.now(timezone.utc).date()
    starts = _week_starts(weeks, today)
    window_start = starts[0]
    # `WeekOut` is the shape `student_weekly` already returns, reused rather
    # than restated: the drill-down chart and this cohort chart label their
    # weeks identically or a reader clicking from one to the other cannot line
    # them up. The `%-d` directive is a glibc extension and raises on Windows —
    # `test_no_platform_specific_strftime` pins that.
    labels = [
        WeekOut(label=f"{d.day} {d:%b}", start=d, end=d + timedelta(days=6)) for d in starts
    ]

    student_ids = (
        []
        if reach.nothing
        else list(db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all())
    )

    if not student_ids:
        # NO LINE IS DRAWN OVER NOBODY, and the two ways of getting here are
        # given DIFFERENT sentences on purpose. `reach.nothing` is "you may see
        # nothing" — the header beside it says `none` — and an empty reach that
        # resolves to no students is "there is nothing here yet". Those are
        # opposite facts (app/scope_views.py), and four flat zero lines would
        # render them identically, and identically to a real collapse.
        refused = (
            "Your access does not reach any students, so there is nothing to plot."
            if reach.nothing
            else "No students are in scope yet, so there is nothing to plot."
        )
        return AnalyticsSeriesOut(
            weeks=labels,
            series=[
                SeriesOut(key=key, label=label, unit=unit, points=[None] * weeks,
                          source="unavailable", note=refused)
                for key, label, unit in _SERIES_SHAPE
            ],
            students_in_reach=0,
            generated_at=datetime.now(timezone.utc),
        )

    def bucket(d: date) -> int | None:
        idx = (d - window_start).days // 7
        return idx if 0 <= idx < weeks else None

    # --- attendance: present and total per week, over the reach --------------
    present = [0] * weeks
    total = [0] * weeks
    for session_date, was_present, n in db.execute(
        select(
            AttendanceRecord.session_date,
            AttendanceRecord.present,
            func.count(),
        )
        .where(
            AttendanceRecord.student_id.in_(reach.student_ids()),
            AttendanceRecord.session_date
            >= datetime(window_start.year, window_start.month, window_start.day, tzinfo=timezone.utc),
        )
        .group_by(AttendanceRecord.session_date, AttendanceRecord.present)
    ).all():
        idx = bucket(session_date.date())
        if idx is None:
            continue
        total[idx] += int(n)
        if was_present:
            present[idx] += int(n)
    attendance_points: list[float | None] = [
        round(100 * present[i] / total[i], 1) if total[i] else None for i in range(weeks)
    ]

    # --- skilling hours: time_sheet_entries, the SKILLING head ---------------
    # AGENTS.md names this table for exactly this question ("how many minutes of
    # SKILLING has this student logged this week", which the dashboard chart and
    # the weekly target ask). The Time Allocation Ledger answers a different one
    # — what the 24 hours of Thursday looked like — and summing it here would
    # put sleep and leisure into a line labelled Skilling.
    minutes = [0] * weeks
    for day, mins in db.execute(
        select(TimeSheetEntry.day, func.sum(TimeSheetEntry.minutes))
        .where(
            TimeSheetEntry.student_id.in_(reach.student_ids()),
            TimeSheetEntry.activity == DayActivity.SKILLING,
            TimeSheetEntry.day >= window_start,
        )
        .group_by(TimeSheetEntry.day)
    ).all():
        idx = bucket(day)
        if idx is not None:
            minutes[idx] += int(mins or 0)
    # A week with no entries is 0 hours and NOT null, and that is the deliberate
    # opposite of the attendance line above. The time sheet is a SELF-REPORT: a
    # week in which nobody logged anything is a measurement of the cohort, and a
    # true one. Attendance is a RECORD somebody else keeps, and a week with no
    # sessions in it is a week nobody wrote down, which is not 0%.
    hours_points: list[float | None] = [round(m / 60, 1) for m in minutes]

    # --- offers approved per week -------------------------------------------
    offers = [0] * weeks
    for decided_at, n in db.execute(
        select(PlacementOffer.decided_at, func.count())
        .where(
            PlacementOffer.student_id.in_(reach.student_ids()),
            PlacementOffer.status == OfferStatus.APPROVED,
            PlacementOffer.decided_at.is_not(None),
            PlacementOffer.decided_at
            >= datetime(window_start.year, window_start.month, window_start.day, tzinfo=timezone.utc),
        )
        .group_by(PlacementOffer.decided_at)
    ).all():
        idx = bucket(decided_at.date())
        if idx is not None:
            offers[idx] += int(n)
    offer_points: list[float | None] = [float(n) for n in offers]

    # --- readiness: this week only ------------------------------------------
    readiness_points: list[float | None] = [None] * weeks
    readiness_note = (
        "Placement readiness is computed from a student's records as they stand "
        "today, so earlier weeks cannot be recovered. This line fills in once "
        "the nightly analytics snapshot (B8.6) has been running."
    )
    readiness_source = "partial"
    if len(student_ids) > MAX_READINESS_ROLLUP:
        readiness_source = "unavailable"
        readiness_note = (
            f"{len(student_ids)} students are in reach, above the "
            f"{MAX_READINESS_ROLLUP} this roll-up will compute live. Narrow the "
            "scope, or wait for the nightly snapshot (B8.6)."
        )
    elif student_ids:
        scores = [
            r.score for r in compose_readiness_many(db, student_ids).values()
            if r.score is not None
        ]
        # NOT `len(student_ids)` as the denominator: a student whose score is
        # None has nothing imported, and counting them as "not ready" is the
        # same false verdict `build_readiness` exists to avoid, moved up one
        # level to a cohort. The tile reports the share of the students who
        # CAN be scored, and `students_in_reach` beside it is what makes the
        # difference visible.
        if scores:
            ready = sum(1 for s in scores if s >= PLACEMENT_READY_SCORE)
            readiness_points[-1] = round(100 * ready / len(scores), 1)
            readiness_note = (
                f"{len(scores)} of {len(student_ids)} students have enough on "
                "record to be scored. Earlier weeks need the nightly snapshot "
                "(B8.6)."
            )
        else:
            readiness_note = (
                "No student in reach has marks, attendance or certifications on "
                "record yet, so nobody can be scored. Run an import (B8.1)."
            )

    return AnalyticsSeriesOut(
        weeks=labels,
        series=[
            SeriesOut(key="attendance_pct", label="Attendance %", unit="percent",
                      points=attendance_points, source="live"),
            SeriesOut(key="skilling_hours", label="Skilling hours", unit="hours",
                      points=hours_points, source="live"),
            SeriesOut(key="offers", label="Offers approved", unit="count",
                      points=offer_points, source="live"),
            SeriesOut(key="readiness_pct", label="Placement ready %", unit="percent",
                      points=readiness_points, source=readiness_source, note=readiness_note),
        ],
        students_in_reach=len(student_ids),
        generated_at=datetime.now(timezone.utc),
    )


#: The KPI strip's shape, for the refused-reach branch below — the SAME keys in
#: the SAME order, so a reader whose access reaches nothing sees the strip they
#: would otherwise see, every tile dashed, rather than an empty row that reads
#: as a screen that failed to load.
_KPI_SHAPE = (
    ("placement_rate", "Placement rate", "percent"),
    ("median_ctc", "Median CTC", "inr"),
    ("highest_ctc", "Highest CTC", "inr"),
    ("placement_ready_pct", "Placement ready %", "percent"),
    ("attendance_avg", "Attendance average", "percent"),
    ("mock_interviews", "Mock interviews", "count"),
    ("pending_approvals", "Pending approvals", "count"),
)


class KpiOut(BaseModel):
    key: str
    label: str
    unit: str
    #: NULL means NOT MEASURED. Every consumer renders it as a dash — the
    #: Analytics screen already does this for the tiles it cannot compute, and
    #: its header comment says so ("A TILE NOBODY COMPUTES SHOWS A DASH").
    value: float | None
    #: The same measure over the PREVIOUS window of the same length, and their
    #: difference. `delta` is NULL whenever either end is, which is not the same
    #: as a delta of zero: "unchanged" and "we cannot compare" are different
    #: sentences and an arrow drawn for the second one is a lie in a glyph.
    previous: float | None = None
    delta: float | None = None
    #: Why this is null, when it is. Rendered under the dash.
    note: str | None = None


class AnalyticsKpisOut(BaseModel):
    #: The window both halves of every delta were taken over.
    weeks: int
    period_start: date
    previous_period_start: date
    kpis: list[KpiOut]
    generated_at: datetime


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


@router.get("/analytics/kpis", response_model=AnalyticsKpisOut)
def analytics_kpis(
    response: Response,
    weeks: int = 12,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AnalyticsKpisOut:
    """The KPI strip above the charts, each with its delta against the previous
    window of the same length.

    THREE OF THE SIX CANNOT HAVE A DELTA AND SAY SO. Placement-ready %, pending
    approvals and the mock-interview count are all "how many right now" —
    nothing records what the queue depth or the readiness share WAS twelve weeks
    ago, so their `previous` and `delta` are null rather than compared against a
    number computed a different way. A delta is a promise that two numbers were
    measured the same way; a screen that draws a green arrow from an absence is
    worse than one that draws nothing.
    """
    require_capability(db, session, "admin.analytics")
    reach = scope_filter(db, session, "admin.analytics")
    scope_header(response, reach)

    weeks = max(1, min(int(weeks), MAX_SERIES_WEEKS))
    now = datetime.now(timezone.utc)
    today = now.date()
    period_start = today - timedelta(weeks=weeks)
    previous_start = today - timedelta(weeks=2 * weeks)
    period_start_at = datetime(period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc)
    previous_start_at = datetime(previous_start.year, previous_start.month, previous_start.day, tzinfo=timezone.utc)

    def envelope(kpis: list[KpiOut]) -> AnalyticsKpisOut:
        return AnalyticsKpisOut(
            weeks=weeks,
            period_start=period_start,
            previous_period_start=previous_start,
            kpis=kpis,
            generated_at=now,
        )

    if reach.nothing:
        refused = "Your access does not reach any students."
        return envelope(
            [
                KpiOut(key=key, label=label, unit=unit, value=None, note=refused)
                for key, label, unit in _KPI_SHAPE
            ]
        )

    student_ids = list(db.scalars(select(Student.id).where(Student.id.in_(reach.student_ids()))).all())
    eligible = len(student_ids)
    if not eligible:
        # The strip keeps its shape rather than emptying — see `_KPI_SHAPE`.
        return envelope(
            [
                KpiOut(
                    key=key, label=label, unit=unit, value=None,
                    note="No students are in scope yet.",
                )
                for key, label, unit in _KPI_SHAPE
            ]
        )

    def delta(value: float | None, previous: float | None) -> float | None:
        if value is None or previous is None:
            return None
        return round(value - previous, 1)

    # --- placement rate: distinct placed students, as at each window's END ---
    def placed_by(at: datetime | None) -> int:
        stmt = select(func.count(func.distinct(PlacementOffer.student_id))).where(
            PlacementOffer.status == OfferStatus.APPROVED,
            PlacementOffer.student_id.in_(reach.student_ids()),
        )
        if at is not None:
            stmt = stmt.where(
                PlacementOffer.decided_at.is_not(None), PlacementOffer.decided_at < at
            )
        return int(db.scalar(stmt) or 0)

    rate_now = round(100 * placed_by(None) / eligible, 1) if eligible else None
    # The denominator is TODAY's roster at both ends, because the roster as it
    # stood twelve weeks ago is not recorded either. So this delta answers "how
    # many more of the students I have now had been placed by then", which is
    # the honest reading and is stated in the note rather than left to be
    # guessed from an arrow.
    rate_then = round(100 * placed_by(period_start_at) / eligible, 1) if eligible else None

    # --- CTC over offers approved IN each window ----------------------------
    def ctcs(start: datetime, end: datetime | None) -> list[int]:
        stmt = select(PlacementOffer.ctc_inr).where(
            PlacementOffer.status == OfferStatus.APPROVED,
            PlacementOffer.student_id.in_(reach.student_ids()),
            PlacementOffer.decided_at.is_not(None),
            PlacementOffer.decided_at >= start,
            PlacementOffer.ctc_inr > 0,
        )
        if end is not None:
            stmt = stmt.where(PlacementOffer.decided_at < end)
        return [int(v) for v in db.scalars(stmt).all() if v]

    this_window = ctcs(period_start_at, None)
    last_window = ctcs(previous_start_at, period_start_at)

    # --- attendance average over each window --------------------------------
    def attendance_avg(start: datetime, end: datetime | None) -> float | None:
        stmt = select(
            func.count().filter(AttendanceRecord.present.is_(True)), func.count()
        ).where(
            AttendanceRecord.student_id.in_(reach.student_ids()),
            AttendanceRecord.session_date >= start,
        )
        if end is not None:
            stmt = stmt.where(AttendanceRecord.session_date < end)
        row = db.execute(stmt).first()
        if row is None or not row[1]:
            return None
        return round(100 * int(row[0] or 0) / int(row[1]), 1)

    # --- placement-ready % (now only) ---------------------------------------
    ready_pct: float | None = None
    ready_note: str | None = None
    if not student_ids:
        ready_note = "No students in reach."
    elif len(student_ids) > MAX_READINESS_ROLLUP:
        ready_note = (
            f"{len(student_ids)} students are in reach, above the "
            f"{MAX_READINESS_ROLLUP} this roll-up computes live."
        )
    else:
        scores = [
            r.score for r in compose_readiness_many(db, student_ids).values()
            if r.score is not None
        ]
        if scores:
            ready_pct = round(
                100 * sum(1 for s in scores if s >= PLACEMENT_READY_SCORE) / len(scores), 1
            )
            ready_note = (
                f"Of the {len(scores)} students with enough on record to be scored, "
                f"out of {len(student_ids)} in reach. No comparison period: "
                "readiness has no history until the nightly snapshot (B8.6) runs."
            )
        else:
            ready_note = (
                "Nobody in reach has marks, attendance or certifications on record, "
                "so no readiness score can be computed. Run an import (B8.1)."
            )

    # The same `PENDING_QUEUE_STATUSES` the summary tile counts, for the same
    # reason: a held application is still an approval this office owes somebody.
    # Two KPIs answering "pending" from two different status sets is how the
    # Analytics screen ends up contradicting itself by four.
    pending = int(
        db.scalar(
            select(func.count()).select_from(
                select(Registration.id)
                .where(
                    Registration.status.in_(PENDING_QUEUE_STATUSES),
                    registration_scope_clause(reach),
                )
                .subquery()
            )
        )
        or 0
    ) + int(
        db.scalar(
            select(func.count())
            .select_from(BadgeEvidence)
            .where(
                BadgeEvidence.status == EvidenceStatus.PENDING_VERIFICATION,
                BadgeEvidence.student_id.in_(reach.student_ids()),
            )
        )
        or 0
    )

    median_now = _median(this_window)
    median_then = _median(last_window)
    highest_now = float(max(this_window)) if this_window else None
    attendance_now = attendance_avg(period_start_at, None)
    attendance_then = attendance_avg(previous_start_at, period_start_at)

    return envelope(
        [
            KpiOut(
                key="placement_rate", label="Placement rate", unit="percent",
                value=rate_now, previous=rate_then, delta=delta(rate_now, rate_then),
                note=(
                    "Both ends use today's roster as the denominator; the roster "
                    "as it stood earlier is not recorded."
                ),
            ),
            KpiOut(
                key="median_ctc", label="Median CTC", unit="inr",
                value=median_now, previous=median_then,
                delta=delta(median_now, median_then),
                note=None if median_now is not None else (
                    "No offer was approved with a CTC in this window."
                ),
            ),
            KpiOut(
                key="highest_ctc", label="Highest CTC", unit="inr",
                value=highest_now,
                note=None if highest_now is not None else (
                    "No offer was approved with a CTC in this window."
                ),
            ),
            KpiOut(
                key="placement_ready_pct", label="Placement ready %", unit="percent",
                value=ready_pct, note=ready_note,
            ),
            KpiOut(
                key="attendance_avg", label="Attendance average", unit="percent",
                value=attendance_now, previous=attendance_then,
                delta=delta(attendance_now, attendance_then),
                note=None if attendance_now is not None else (
                    "No attendance has been recorded in this window. Run an import (B8.1)."
                ),
            ),
            KpiOut(
                key="mock_interviews", label="Mock interviews", unit="count",
                value=None,
                note=(
                    "Not reported here on purpose. The one definition of a mock "
                    "interview — `interview_sessions` plus the legacy "
                    "`mock_attempts`, distinguished by source — is B6.3's, and a "
                    "second one written here would disagree with the interview "
                    "records screen the first time either moved."
                ),
            ),
            KpiOut(
                key="pending_approvals", label="Pending approvals", unit="count",
                value=float(pending),
                note=(
                    "Applications awaiting review — held ones included, because "
                    "a hold is a note, not a decision — plus evidence awaiting "
                    "verification. No comparison period: queue depth is not "
                    "recorded over time."
                ),
            ),
        ]
    )




@router.get("/students/{student_id}/resume.pdf")
def student_resume_pdf(
    student_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """The student's latest REEP resume, rendered to PDF for the reader of the
    analytics map. LOCAL render (ReportLab, no model, no network), so rule 1's
    egress gate does not apply.

    RULE 2 DOES, and it is now actually applied. This docstring already said "a
    mentor reading a mentee's resume would need the scope gate" — and the code
    checked only `admin.analytics`, which is grantable to a mentor. Any faculty
    member holding the Analytics screen could download any student's resume,
    with their contact details and academic record, by id.
    """
    require_capability(db, session, "admin.analytics")
    _assert_can_access_student(session, student_id, db)
    row = db.execute(
        select(Resume, Student.usn)
        .join(Student, Resume.student_id == Student.id)
        .where(Resume.student_id == student_id)
        .order_by(Resume.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No resume on record for this student."
        )
    resume, usn = row
    pdf = render_resume_pdf(resume.markdown or "", fallback_title=resume.title or "REEP Resume")
    stem = (usn or student_id).replace('"', "")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="resume-{stem}-v{resume.version}.pdf"'},
    )


# --- placement ------------------------------------------------------------


class PlacementOfferRowOut(BaseModel):
    id: str
    student_id: str
    student_name: str
    usn: str | None
    organisation: str
    job_title: str
    role_type: str
    ctc_inr: int
    status: str
    created_at: datetime
    decided_at: datetime | None


class RecruiterOut(BaseModel):
    organisation: str
    count: int


class TrackSplitOut(BaseModel):
    """One row of the by-track split: how many, and how many placed.

    `code` is NULL for the students whose batch names no specialization, and
    that row is CARRIED rather than dropped — the columns have to sum to the
    funnel or the screen is quietly reporting a smaller programme than it has.
    """

    code: str | None
    name: str
    eligible: int
    placed: int


class FunnelGapOut(BaseModel):
    """A stage the funnel names and cannot count, with the reason in words.

    A funnel that silently draws four stages where the board asks for five
    reads as a programme where nobody was interviewed. A stage that is `null`
    with a sentence attached reads as what it is: not recorded.
    """

    stage: str
    reason: str


class PlacementOut(BaseModel):
    semester: int | None
    # ------------------------------------------------------------ the funnel
    # FIVE STAGES NAMED, FOUR COUNTED, ALL FOUR DISTINCT STUDENTS (B12.3).
    # `offers` and `approved` below are still counts of OFFERS and are kept
    # because the status donut is arithmetic over them; the funnel reads the
    # `_students` figures beside them, because a funnel captioned "distinct
    # students" that draws a count of offers is a wrong number, not a missing
    # one.
    eligible: int  # students in reach
    applied: int  # distinct students with at least one job application
    interviewed: int | None  # see `unavailable` — nothing records a recruiter round
    offered_students: int  # distinct students holding a submitted offer
    approved_students: int  # ...and holding an approved one: PLACED
    offers: int  # offers submitted for approval (pending, approved or refused)
    approved: int  # offers approved — the ones that count towards placement
    #: Every stage above whose value is `null`, and why. Empty when the funnel
    #: is complete, so a client can branch on the list rather than on which
    #: field happens to be None this release.
    unavailable: list[FunnelGapOut]
    # -------------------------------------------------------------- the KPIs
    #: placed / eligible, as a percentage. NULL — never 0.0 — when nobody is
    #: eligible: "0% placed" and "there is nobody to place" are opposite facts
    #: and a screen must not print the first for the second. Same rule as every
    #: nullable score on the English baseline.
    placement_rate_pct: float | None
    median_ctc_inr: int | None
    highest_ctc_inr: int | None
    #: How many offers the two CTC figures were taken over. A median is a
    #: statement about a set, and a screen that prints one without saying over
    #: what invites the reader to assume it is over all of them.
    ctc_offers_counted: int
    #: Distinct students holding more than one offer that has not been refused.
    multiple_offer_students: int
    # ------------------------------------------------------------- the split
    by_track: list[TrackSplitOut]
    #: The offer years this reach has records in, newest first — what the
    #: screen's Period filter is built from, so it cannot offer a year with
    #: nothing behind it.
    years: list[int]
    #: The year `?year=` narrowed the offer figures to, echoed back; NULL is
    #: the whole record.
    year: int | None
    recent: list[PlacementOfferRowOut]
    top_recruiters: list[RecruiterOut]


#: Rows the Recent offers table shows. Newest first; the export carries all.
RECENT_OFFERS = 25


#: The one funnel stage this product cannot count, and the sentence that says
#: why. A constant rather than a literal at two call sites, because the empty
#: reach returns it too and the two must not drift into two different
#: explanations of the same dash.
_INTERVIEWED_GAP = FunnelGapOut(
    stage="interviewed",
    reason=(
        "No recruiter interview round is recorded anywhere in REEP. The mock "
        "interviewer's sessions are rehearsals, not hiring rounds, and counting "
        "them here would put a practice figure in a placement funnel."
    ),
)


def _placement_by_track(db: Session, students, in_year) -> list[TrackSplitOut]:
    """How many students each track holds, and how many of them are placed.

    A TRACK IS THE STUDENT'S ACADEMIC SPECIALIZATION, not the posting's. The
    board asks "which specialization placed a student", and the only way to
    answer that for EVERY offer is through the student's own batch: an offer
    carries a nullable `job_id`, so an off-campus offer has no posting and
    therefore no `jobs.tracks` to read, and a posting for two tracks would count
    one placement twice. The student's batch names exactly one specialization
    and names it for the eligible side of the ratio as well, which is what makes
    "placed / eligible" a ratio rather than two numbers about different sets.

    THE UNFILED ROW IS CARRIED. A student whose batch names no specialization —
    or who has no batch — lands under `code = null`, because the split is drawn
    beside a funnel and columns that do not sum to it read as missing students.

    GROUPED BY (code, name), NOT BY ID. `academic_specializations.code` is
    unique only WITHIN a course, so a deployment teaching Finance under both an
    MBA and an MCA has two rows spelling "FIN". Grouping by id would draw them
    as two rows the screen cannot tell apart; grouping by the pair merges the
    ones a reader would call the same track and keeps apart the ones that only
    share a code. The split is normally read under a batch or a course filter,
    where the question does not arise at all.
    """
    def grouped(*extra, join_offers: bool):
        query = (
            select(
                AcademicSpecialization.code,
                AcademicSpecialization.name,
                func.count(func.distinct(Student.id)),
            )
            .select_from(Student)
            .outerjoin(Cohort, Student.cohort_id == Cohort.id)
            .outerjoin(
                AcademicSpecialization,
                Cohort.specialization_id == AcademicSpecialization.id,
            )
        )
        if join_offers:
            query = query.join(PlacementOffer, PlacementOffer.student_id == Student.id)
        return db.execute(
            query.where(Student.id.in_(students), *extra).group_by(
                AcademicSpecialization.code, AcademicSpecialization.name
            )
        ).all()

    rolls = grouped(join_offers=False)
    placed = {
        (code, name): n
        for code, name, n in grouped(
            PlacementOffer.status == OfferStatus.APPROVED, in_year, join_offers=True
        )
    }
    rows = [
        TrackSplitOut(
            code=code,
            # The label the screen prints. "Not filed" rather than an empty
            # string, so the row reads as a statement about those students
            # rather than as a rendering fault.
            name=name or "Not filed",
            eligible=n,
            placed=placed.get((code, name), 0),
        )
        for code, name, n in rolls
    ]
    # Biggest track first, the unfiled row last whatever its size: it is the
    # one row that is not a track.
    rows.sort(key=lambda r: (r.code is None, -r.eligible, r.code or ""))
    return rows


@router.get("/placement", response_model=PlacementOut)
def placement(
    response: Response,
    cohort_id: str | None = None,
    # Bounded (DEF-006): `?year=-5466912982786` overflowed the comparison with
    # EXTRACT(year ...) and was a 500. A calendar year, or a 422.
    year: int | None = Query(default=None, ge=1900, le=9999),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> PlacementOut:
    """The placement funnel, the yearly KPIs, the by-track split and the recent offers.

    SCOPED (B1.4) through the student, which is still the only route there is:
    an offer hangs on a `students` row, and although B12.1 has now given `jobs`
    a college and a course, an offer's `job_id` is NULLABLE — an off-campus
    offer a student typed in themselves has no posting behind it — so narrowing
    through the posting would silently drop exactly the offers nobody else
    records. The student is the join that always lands.

    `?cohort_id=` NARROWS WITHIN THE REACH AND CANNOT WIDEN IT, as
    `mentor_load`'s two filters do: it is an extra predicate over the reach's
    own, so asking for a batch the caller does not hold returns zeros rather
    than somebody else's figures. Re-validation is the shape of the query rather
    than a second check that could one day disagree with the first.

    `?year=` NARROWS THE OFFERS, NOT THE ROLL. The KPIs are "yearly" in the
    sense the board's Period filter means: offers submitted in that calendar
    year, over the students on the roll TODAY. Ageing the denominator as well
    would need a roll-as-of-date that nothing records, and inventing one would
    make last year's rate move every time somebody is added to a batch.

    FOUR OF THE FIVE FUNNEL STAGES ARE COUNTED AND THE FIFTH IS NAMED. Nothing
    in this schema records a recruiter's interview round: `interview_sessions`
    is the REEP MOCK interviewer, a rehearsal a student can sit four times in an
    afternoon with no recruiter involved, and drawing it between "applied" and
    "offered" would put a rehearsal count in a hiring funnel — a wrong number
    rather than a missing one, which is the distinction the placement screen's
    own header comment refuses to blur. `interviewed` is therefore `null` with
    its reason in `unavailable`, and it stays that way until something records a
    round. "Shortlisted", the board's sixth stage, has the same problem and is
    not named at all: a stage that will never be counted is not a gap, it is a
    feature nobody has asked to build.

    The funnel's stages are all narrowed by one reach, so the ratios between
    them stay readable. `top_recruiters` counts only offers inside it, which
    means a scoped reader sees the recruiters OF THEIR OWN students — the
    question that screen is asked.
    """
    require_capability(db, session, "admin.placement")
    reach = scope_filter(db, session, "admin.placement")
    scope_header(response, reach)
    if reach.nothing:
        return PlacementOut(
            semester=None, eligible=0, applied=0, interviewed=None,
            offered_students=0, approved_students=0, offers=0, approved=0,
            unavailable=[_INTERVIEWED_GAP], placement_rate_pct=None,
            median_ctc_inr=None, highest_ctc_inr=None, ctc_offers_counted=0,
            multiple_offer_students=0, by_track=[], years=[], year=year,
            recent=[], top_recruiters=[],
        )

    # The roll this whole payload is about, written ONCE: every count below
    # narrows to these students, so a `?cohort_id=` that reached the funnel but
    # not the split would produce a screen whose columns do not add up.
    students = select(Student.id).where(Student.id.in_(reach.student_ids()))
    if cohort_id:
        students = students.where(Student.cohort_id == cohort_id)
    mine = PlacementOffer.student_id.in_(students)
    submitted = PlacementOffer.status != OfferStatus.DRAFT
    # Offers the office has not refused — the set "how many students hold an
    # offer" is asked about. A REJECTED row is one the office declined to
    # record, not one a recruiter withdrew, and counting it would tell a student
    # they hold an offer the same screen refused.
    standing = PlacementOffer.status.in_((OfferStatus.PENDING_APPROVAL, OfferStatus.APPROVED))
    in_year = (
        func.extract("year", PlacementOffer.created_at) == year if year else sa_true()
    )

    eligible = db.scalar(select(func.count()).select_from(students.subquery())) or 0
    applied = (
        db.scalar(
            select(func.count(func.distinct(JobApplication.student_id))).where(
                JobApplication.student_id.in_(students)
            )
        )
        or 0
    )
    offers = (
        db.scalar(
            select(func.count()).select_from(PlacementOffer).where(submitted, mine, in_year)
        )
        or 0
    )
    approved = (
        db.scalar(
            select(func.count())
            .select_from(PlacementOffer)
            .where(PlacementOffer.status == OfferStatus.APPROVED, mine, in_year)
        )
        or 0
    )
    offered_students = (
        db.scalar(
            select(func.count(func.distinct(PlacementOffer.student_id))).where(
                submitted, mine, in_year
            )
        )
        or 0
    )
    approved_students = (
        db.scalar(
            select(func.count(func.distinct(PlacementOffer.student_id))).where(
                PlacementOffer.status == OfferStatus.APPROVED, mine, in_year
            )
        )
        or 0
    )

    # THE CTC FIGURES ARE TAKEN OVER APPROVED OFFERS WITH A CTC ON THEM.
    # `ctc_inr` defaults to 0 and the student's own offer form does not demand
    # it, so a zero means "not stated" far more often than it means an unpaid
    # role — and a median dragged to the floor by blanks is worse than a dash.
    # `ctc_offers_counted` travels beside them so the screen can say over how
    # many, which is the sentence that makes a median honest.
    priced = (PlacementOffer.status == OfferStatus.APPROVED, mine, in_year, PlacementOffer.ctc_inr > 0)
    ctc_offers_counted = (
        db.scalar(select(func.count()).select_from(PlacementOffer).where(*priced)) or 0
    )
    median_ctc = highest_ctc = None
    if ctc_offers_counted:
        median_ctc = db.scalar(
            select(
                func.percentile_cont(0.5).within_group(PlacementOffer.ctc_inr.asc())
            ).where(*priced)
        )
        highest_ctc = db.scalar(select(func.max(PlacementOffer.ctc_inr)).where(*priced))

    held = (
        select(PlacementOffer.student_id)
        .where(standing, mine, in_year)
        .group_by(PlacementOffer.student_id)
        .having(func.count() > 1)
        .subquery()
    )
    multiple_offer_students = db.scalar(select(func.count()).select_from(held)) or 0

    by_track = _placement_by_track(db, students, in_year)

    # NOT narrowed by `in_year`, deliberately: this is the list the Period
    # filter is built from, and a filter that offers only the year already
    # selected cannot be used to leave it.
    offer_year = func.extract("year", PlacementOffer.created_at)
    years = [
        int(y)
        for y in db.scalars(
            select(offer_year).where(submitted, mine).distinct().order_by(offer_year.desc())
        ).all()
        if y is not None
    ]

    recent = db.execute(
        select(PlacementOffer, User.name, Student.usn)
        .join(Student, PlacementOffer.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .where(submitted, mine, in_year)
        .order_by(PlacementOffer.created_at.desc())
        .limit(RECENT_OFFERS)
    ).all()
    recruiters = db.execute(
        select(PlacementOffer.organisation, func.count())
        .where(PlacementOffer.status == OfferStatus.APPROVED, mine, in_year)
        .group_by(PlacementOffer.organisation)
        .order_by(func.count().desc(), PlacementOffer.organisation)
        .limit(10)
    ).all()
    return PlacementOut(
        semester=_modal_semester(db, reach),
        eligible=eligible,
        applied=applied,
        interviewed=None,
        offered_students=offered_students,
        approved_students=approved_students,
        offers=offers,
        approved=approved,
        unavailable=[_INTERVIEWED_GAP],
        # A rate over nobody is not 0% — see the field's own note.
        placement_rate_pct=round(100 * approved_students / eligible, 1) if eligible else None,
        median_ctc_inr=int(round(median_ctc)) if median_ctc is not None else None,
        highest_ctc_inr=int(highest_ctc) if highest_ctc is not None else None,
        ctc_offers_counted=ctc_offers_counted,
        multiple_offer_students=multiple_offer_students,
        by_track=by_track,
        years=years,
        year=year,
        recent=[
            PlacementOfferRowOut(
                id=o.id,
                student_id=o.student_id,
                student_name=name,
                usn=usn,
                organisation=o.organisation,
                job_title=o.job_title,
                role_type=o.role_type.value,
                ctc_inr=o.ctc_inr,
                status=o.status.value,
                created_at=o.created_at,
                decided_at=o.decided_at,
            )
            for o, name, usn in recent
        ],
        top_recruiters=[RecruiterOut(organisation=org, count=n) for org, n in recruiters],
    )

# --- the badge catalogue, for the certification form ------------------------


class BadgeCatalogueOut(BaseModel):
    code: str
    name: str
    category: str
    category_label: str
    stage: str
    points: int


@router.get("/badge-catalogue", response_model=list[BadgeCatalogueOut])
def badge_catalogue(session: dict = Depends(get_current_session), db: Session = Depends(get_db)) -> list[BadgeCatalogueOut]:
    """The 48-badge catalogue (code, not rows — see models/badge.py), so the
    Approved Certification form can offer the badge a certification maps to.
    No database read; the gate is here because the catalogue's points are what
    the Certifications table shows and that table is a Main Admin screen."""
    require_capability(db, session, "admin.catalogue")
    return [
        BadgeCatalogueOut(
            code=b.code,
            name=b.name,
            category=b.category.value,
            category_label=CATEGORY_LABEL[b.category],
            stage=b.stage.value,
            points=b.points,
        )
        for b in BADGES
    ]


# --- exports (B14) ----------------------------------------------------------
#
# Three extracts, one set of rules, and none of the rules live here: see
# app/exports.py for scope, the personal-column test and the receipt. What this
# section owns is which columns each file has and which of them name a person.


@router.get("/exports/students.csv")
def export_students_csv(
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Admitted students with their stage, semester, cohort and mentor — the
    "registrations & mentor map" a placement office forwards.

    SCOPED (B1.4/B14). It used to select every `students` row on the
    deployment, so an `admin.exports` grant scoped to one department downloaded
    the other department's roster — the widest possible failure of the
    narrowest possible grant, on the one endpoint whose output cannot be
    recalled.
    """
    require_capability(db, session, "admin.exports")
    reach = scope_filter(db, session, "admin.exports")
    carried_pii = carries_personal_columns(db, session)

    mentor_name = {
        mid: name
        for mid, name in db.execute(
            select(Mentor.id, User.name).join(User, Mentor.user_id == User.id)
        ).all()
    }
    cohort_name = dict(db.execute(select(Cohort.id, Cohort.name)).all())
    rows = (
        []
        if reach.nothing
        else db.execute(
            select(Student, User.name)
            .join(User, Student.user_id == User.id)
            .where(Student.id.in_(reach.student_ids()))
            .order_by(User.name)
        ).all()
    )
    header, body = drop_personal(
        ["Name", "USN", "REEP stage", "Semester", "Cohort", "Mentor"],
        [
            [
                name,
                s.usn or "",
                s.current_stage.value,
                s.current_semester,
                cohort_name.get(s.cohort_id, "") if s.cohort_id else "",
                mentor_name.get(s.mentor_id, "") if s.mentor_id else "Unassigned",
            ]
            for s, name in rows
        ],
        # The mentor's name stays. It is a member of staff acting in their
        # professional role on a map of who mentors whom — which is the whole
        # subject of this file — and dropping it would leave a spreadsheet of
        # anonymous students assigned to anonymous mentors, useful to nobody.
        ["Name", "USN"],
        carry=carried_pii,
    )
    record_export(
        db, session=session, request=request, kind="students",
        filters=scope_note(reach), rows=len(body), carried_pii=carried_pii,
    )
    return csv_response(
        header, body, "reep-students-mentor-map.csv",
        reach=reach, carried_pii=carried_pii,
    )


@router.get("/exports/placement.csv")
def export_placement_csv(
    request: Request,
    cohort_id: str | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Every submitted offer: student, company, role, CTC and the decision.

    `?cohort_id=` (B12.3) NARROWS WITHIN THE REACH, exactly as it does on
    `GET /admin/placement`, and it is a PARAMETER HERE RATHER THAN A SECOND
    ENDPOINT UNDER `/admin/placement/`. 04-backend-changes.md asks B12.3 for a
    batch-scoped `offers.csv`; this file already is that file. It applies B14's
    three rules — `scope_filter`, `carries_personal_columns`/`drop_personal`,
    and a receipt in `export_events` — and a second route would be a second
    implementation of all three, which `tests/test_exports.py` enforces from a
    list that the new one would have to be added to and kept on. Two exports of
    the same rows under two capabilities is how one of them stops dropping the
    USN column.

    The batch is recorded ON THE RECEIPT beside the scope, because "who
    downloaded the placement file" and "which batch did they download" are the
    same question to whoever reads that table afterwards.
    """
    require_capability(db, session, "admin.exports")
    reach = scope_filter(db, session, "admin.exports")
    carried_pii = carries_personal_columns(db, session)

    query = (
        select(PlacementOffer, User.name, Student.usn)
        .join(Student, PlacementOffer.student_id == Student.id)
        .join(User, Student.user_id == User.id)
        .where(PlacementOffer.status != OfferStatus.DRAFT)
        .order_by(PlacementOffer.created_at.desc())
    )
    if not reach.everything:
        query = query.where(Student.id.in_(reach.student_ids()))
    if cohort_id:
        query = query.where(Student.cohort_id == cohort_id)
    rows = [] if reach.nothing else db.execute(query).all()

    header, body = drop_personal(
        ["Student", "USN", "Company", "Role", "Role type", "CTC (INR)", "Status", "Submitted", "Decided"],
        [
            [
                name,
                usn or "",
                o.organisation,
                o.job_title,
                o.role_type.value,
                o.ctc_inr,
                o.status.value,
                o.created_at.date().isoformat() if o.created_at else "",
                o.decided_at.date().isoformat() if o.decided_at else "",
            ]
            for o, name, usn in rows
        ],
        ["Student", "USN"],
        carry=carried_pii,
    )
    record_export(
        db, session=session, request=request, kind="placement",
        filters=scope_note(reach) | ({"cohort_id": cohort_id} if cohort_id else {}),
        rows=len(body), carried_pii=carried_pii,
    )
    return csv_response(
        header, body, "reep-placement-summary.csv",
        reach=reach, carried_pii=carried_pii,
    )


@router.get("/exports/ledger.csv")
def export_ledger_csv(
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Time Allocation Ledger compliance per student: days logged, days
    submitted, hours entered and the productive share (lectures, coursework,
    skilling — the same three heads the student's own metrics strip counts).

    THE ONE EXTRACT THAT IS STILL WORTH READING WITHOUT NAMES, which is the
    argument for the whole personal-column rule: "42 students logged 6 days and
    submitted 2" is a compliance question, and it is answerable from the
    redacted file.
    """
    require_capability(db, session, "admin.exports")
    reach = scope_filter(db, session, "admin.exports")
    carried_pii = carries_personal_columns(db, session)

    days: dict[str, tuple[int, int]] = {
        sid: (int(logged or 0), int(submitted or 0))
        for sid, logged, submitted in db.execute(
            select(
                TimeLedgerDay.student_id,
                func.count(),
                func.count().filter(TimeLedgerDay.status == LedgerDayStatus.SUBMITTED),
            ).group_by(TimeLedgerDay.student_id)
        ).all()
    }
    hours: dict[str, tuple[int, int]] = {
        sid: (int(total or 0), int(productive or 0))
        for sid, total, productive in db.execute(
            select(
                TimeLedgerDay.student_id,
                func.sum(TimeLedgerCell.half_hours),
                func.sum(TimeLedgerCell.half_hours).filter(
                    TimeLedgerCell.activity.in_(list(PRODUCTIVE))
                ),
            )
            .join(TimeLedgerCell, TimeLedgerCell.ledger_day_id == TimeLedgerDay.id)
            .group_by(TimeLedgerDay.student_id)
        ).all()
    }
    rows = (
        []
        if reach.nothing
        else db.execute(
            select(Student, User.name)
            .join(User, Student.user_id == User.id)
            .where(Student.id.in_(reach.student_ids()))
            .order_by(User.name)
        ).all()
    )
    out: list[list[object]] = []
    for s, name in rows:
        logged, submitted = days.get(s.id, (0, 0))
        total_h, productive_h = hours.get(s.id, (0, 0))
        out.append([name, s.usn or "", logged, submitted, total_h / 2, productive_h / 2])
    header, body = drop_personal(
        ["Name", "USN", "Days logged", "Days submitted", "Hours logged", "Productive hours"],
        out,
        ["Name", "USN"],
        carry=carried_pii,
    )
    record_export(
        db, session=session, request=request, kind="ledger",
        filters=scope_note(reach), rows=len(body), carried_pii=carried_pii,
    )
    return csv_response(
        header, body, "reep-ledger-compliance.csv",
        reach=reach, carried_pii=carried_pii,
    )


class ExportEventOut(BaseModel):
    id: str
    kind: str
    at: datetime
    rows: int
    carried_pii: bool
    filters: dict
    #: The account that downloaded it. Never null on a fresh row; null once that
    #: account has been deleted, because the FK is ON DELETE SET NULL — the
    #: export still happened and the history must not lose the fact of it just
    #: because the person left.
    by_user_id: str | None
    by_name: str | None


class ExportHistoryOut(BaseModel):
    """The receipts, and the reach of the person reading them.

    `scope` IS IN THIS BODY and in no other export response, and that is not an
    inconsistency: this is the one B14 endpoint that returns JSON. The three
    CSVs state the same fact in response headers, because a JSON envelope around
    a CSV is not a CSV (app/exports.py says so at more length).
    """

    scope: dict
    events: list[ExportEventOut]


@router.get("/exports/history", response_model=ExportHistoryOut)
def export_history(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
    limit: int = 100,
) -> ExportHistoryOut:
    """Who has taken what out of the building (B14).

    WHO SEES WHOSE. A holder whose Exports grant covers the programme sees every
    receipt — that is the office, and the whole point of a receipt is that
    somebody else reads it. A NARROWED holder sees only their own downloads.
    That is not a scope filter in the usual sense, because an `export_events`
    row hangs under an account rather than under a student and has no ancestry
    to test; the honest narrowing is "you may audit what you can reach, and a
    department-scoped grant does not reach the office's downloads".
    """
    require_capability(db, session, "admin.exports")
    reach = scope_filter(db, session, "admin.exports")
    query = (
        select(ExportEvent, User.name)
        .outerjoin(User, ExportEvent.user_id == User.id)
        .order_by(ExportEvent.at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if not reach.everything:
        query = query.where(ExportEvent.user_id == session.get("userId"))
    return ExportHistoryOut(
        scope=scope_note(reach),
        events=[
            ExportEventOut(
                id=row.id,
                kind=row.kind,
                at=row.at,
                rows=row.rows,
                carried_pii=bool(row.carried_pii),
                filters=row.filters or {},
                by_user_id=row.user_id,
                by_name=name,
            )
            for row, name in db.execute(query).all()
        ],
    )
