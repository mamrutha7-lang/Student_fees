"""The daily job agent (app/job_scout.py, 2026-10-23), against the real schema
with two fake boards and no network.

What each test holds down, and what comes back if it is deleted:

1. EVERY STREAM IS SEARCHED. A course with no specialization (BGSCET's Digital
   Marketing MBA) is a stream too; drop it and those students get nothing.
2. TARGETING IS EXACT. A Digital Marketing advert on an HR student's board, or
   the reverse, is the leak `jobs_visibility`'s skipped track clause would
   cause if a row ever lost its course.
3. FRESHER MEANS FRESHER, and an internship says so in its title.
4. NO DUPLICATES: not on the second morning, not across the two boards, not
   over a posting the office already made, and never a closed posting back.
5. THE BOARD DOES NOT DROWN: ten new postings per stream per morning.
6. THE AGENT CLEARS UP AFTER ITSELF, and only after itself.
7. AN AGENT POSTING IS CLOSED, NOT REMOVED, or the next run reposts it.
8. A RUN LEAVES A RECEIPT the Jobs sheet reads, and a dry run leaves nothing.
9. ONE BOARD FAILING DOES NOT STOP THE OTHER, and both failing is a failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, or_, select, update

from conftest import requires_db

from app import job_scout, job_scout_job
from app.config import settings
from app.db import SessionLocal
from app.job_sources import ADZUNA, JOOBLE, FoundJob, SourceError
from app.jobs_visibility import STATUS_CLOSED, STATUS_OPEN
from app.models.cohort import Cohort
from app.models.institution import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from app.models.job import DegreeLevel, Job, JobApplication
from app.models.redesign import AuditEvent
from app.models.skill import Skill
from app.models.user import Role, Student


# --------------------------------------------------------------------------- #
# Without a database
# --------------------------------------------------------------------------- #


def _advert(title: str, description: str = "", **kw) -> FoundJob:
    return FoundJob(
        provider=kw.pop("provider", ADZUNA),
        external_id=kw.pop("external_id", uuid.uuid4().hex),
        title=title,
        company=kw.pop("company", "Acme"),
        location=kw.pop("location", "Bengaluru"),
        apply_url=kw.pop("apply_url", "https://example.com/ad"),
        description=description,
        posted_on=kw.pop("posted_on", None),
    )


@pytest.mark.parametrize(
    ("title", "description", "fresher"),
    [
        ("HR Executive", "Freshers welcome", True),
        ("Assistant Manager - HR", "", True),
        ("Management Trainee", "0-1 years of experience", True),
        ("Financial Analyst", "1-3 yrs experience", True),
        ("Analyst", "A company with 25 years of history", True),
        ("Senior HR Executive", "", False),
        ("Sr. Business Analyst", "", False),
        ("HR Manager", "", False),
        ("Team Lead - Sales", "", False),
        ("Head of Marketing", "", False),
        ("Financial Analyst", "2-5 years of experience required", False),
        ("Analyst", "Experience: minimum 3 years", False),
        ("Analyst", "3+ yrs exp in FP&A", False),
    ],
)
def test_a_fresher_role_is_one_a_fresher_can_apply_to(title: str, description: str, fresher: bool) -> None:
    assert job_scout.is_fresher_role(_advert(title, description)) is fresher


def test_an_internship_says_so_in_its_title() -> None:
    described = _advert("HR Trainee", "A 3-month internship with a stipend")
    assert job_scout.posting_title(described, job_scout.KIND_INTERNSHIP) == "Internship · HR Trainee"
    assert job_scout.posting_title(_advert("HR Intern"), job_scout.KIND_INTERNSHIP) == "HR Intern"
    assert job_scout.posting_title(_advert("Summer Internship - HR"), job_scout.KIND_INTERNSHIP).startswith("Summer")
    assert job_scout.posting_title(_advert("HR Trainee"), job_scout.KIND_JOB) == "HR Trainee"


def test_a_posting_reads_its_source_back_from_its_ref() -> None:
    assert job_scout.source_of_ref(None) == "office"
    assert job_scout.source_of_ref("") == "office"
    assert job_scout.source_of_ref("adzuna:1:c") == "adzuna"
    assert job_scout.source_of_ref("jooble:1:c") == "jooble"
    # A ref this module did not write is not the agent's.
    assert job_scout.source_of_ref("campus-drive-2026") == "office"


def test_every_known_code_has_two_phrases_and_an_unknown_one_falls_back_to_its_name() -> None:
    for code, phrases in job_scout.SEARCH_TERMS.items():
        assert code == code.upper() and len(phrases) == 2, code
    leaf = job_scout.Leaf("c", "k", "OPS", "OPS", "Operations Management", DegreeLevel.PG)
    assert job_scout.phrases_for(leaf) == ("Operations Management",)


# --------------------------------------------------------------------------- #
# Fakes and fixtures
# --------------------------------------------------------------------------- #


@dataclass
class FakeBoard:
    """A board answering every phrase from a fixed list, keyed by kind."""

    name: str
    jobs: list[FoundJob] = field(default_factory=list)
    internships: list[FoundJob] = field(default_factory=list)
    by_phrase: dict[str, list[FoundJob]] = field(default_factory=dict)
    fail: SourceError | None = None
    calls: list[tuple[str, bool]] = field(default_factory=list)

    def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
        self.calls.append((phrase, internship))
        if self.fail is not None:
            raise self.fail
        if phrase in self.by_phrase:
            return [] if internship else list(self.by_phrase[phrase])
        return list(self.internships if internship else self.jobs)


@pytest.fixture
def campus():
    """One college: a General MBA with HR and Marketing, a Digital Marketing
    MBA with no specialization, and an archived course and specialization the
    agent must not search. One batch per stream so a student can be seated."""
    tag = uuid.uuid4().hex[:6]
    made: dict[str, object] = {"tag": tag}
    with SessionLocal() as db:
        college = College(code=f"JS{tag.upper()}", name="Scout College", status=STATUS_ACTIVE)
        db.add(college)
        db.flush()
        dept = Department(college_id=college.id, name="MBA", code=f"MBA{tag}")
        db.add(dept)
        db.flush()
        gen = AcademicCourse(department_id=dept.id, code=f"gen{tag}", name="General MBA", degree_level=DegreeLevel.PG)
        dm = AcademicCourse(department_id=dept.id, code="dm", name="Digital Marketing", degree_level=DegreeLevel.PG)
        old = AcademicCourse(
            department_id=dept.id, code=f"old{tag}", name="Retired MBA", status=STATUS_ARCHIVED
        )
        db.add_all([gen, dm, old])
        db.flush()
        hr = AcademicSpecialization(course_id=gen.id, code="hr", name="Human Resources")
        mkt = AcademicSpecialization(course_id=gen.id, code="marketing", name="Marketing")
        gone = AcademicSpecialization(course_id=gen.id, code="ops", name="Operations", status=STATUS_ARCHIVED)
        db.add_all([hr, mkt, gone])
        db.flush()

        def batch(code, course, spec) -> Cohort:
            return Cohort(
                code=f"{code}-{tag}", name="2026-28", batch_label="2026-28",
                degree_level=DegreeLevel.PG, department_id=dept.id, course_id=course.id,
                specialization_id=spec.id if spec else None,
                start_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end_date=datetime(2028, 6, 30, tzinfo=timezone.utc),
            )

        b_hr, b_mkt, b_dm = batch("HR", gen, hr), batch("MKT", gen, mkt), batch("DM", dm, None)
        db.add_all([b_hr, b_mkt, b_dm])
        db.commit()
        made |= {
            "college": college.id, "dept": dept.id, "gen": gen.id, "dm": dm.id, "old": old.id,
            "specs": [hr.id, mkt.id, gone.id], "batch_hr": b_hr.id, "batch_mkt": b_mkt.id,
            "batch_dm": b_dm.id,
        }

    yield made

    with SessionLocal() as db:
        courses = [made["gen"], made["dm"], made["old"]]
        # By college as well as by course, so a run that wrote a row without
        # its course (the defect test 2 exists for) still cannot strand it.
        mine = or_(Job.course_id.in_(courses), Job.college_id == made["college"])
        db.execute(delete(JobApplication).where(JobApplication.job_id.in_(select(Job.id).where(mine))))
        db.execute(delete(Job).where(mine))
        batches = [made["batch_hr"], made["batch_mkt"], made["batch_dm"]]
        db.execute(update(Student).where(Student.cohort_id.in_(batches)).values(cohort_id=None))
        db.execute(delete(Cohort).where(Cohort.id.in_(batches)))
        db.execute(delete(AcademicSpecialization).where(AcademicSpecialization.id.in_(made["specs"])))
        db.execute(delete(AcademicCourse).where(AcademicCourse.id.in_(courses)))
        db.execute(delete(Department).where(Department.id == made["dept"]))
        db.execute(delete(College).where(College.id == made["college"]))
        db.commit()


@pytest.fixture
def runs():
    """Run the agent over the campus's streams only, at a clock of the test's
    choosing, and remove every receipt and every agent row it wrote."""
    clocks: list[datetime] = []

    def _run(campus, boards, *, now: datetime | None = None, dry_run: bool = False):
        now = now or datetime.now(timezone.utc)
        clocks.append(now)
        with SessionLocal() as db:
            streams = [leaf for leaf in job_scout.leaves(db) if leaf.college_id == campus["college"]]
            return job_scout.run(db, now=now, sources=boards, dry_run=dry_run, streams=streams)

    yield _run

    with SessionLocal() as db:
        db.execute(
            delete(AuditEvent).where(
                AuditEvent.entity_type == job_scout.AUDIT_ENTITY, AuditEvent.occurred_at.in_(clocks)
            )
        )
        db.commit()


def _rows(campus) -> list[Job]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(Job).where(Job.course_id.in_([campus["gen"], campus["dm"]])).order_by(Job.posted_on.desc())
            )
        )


def _seat(user, cohort_id) -> None:
    with SessionLocal() as db:
        student = db.scalar(select(Student).where(Student.user_id == user.user_id))
        student.cohort_id = cohort_id
        db.commit()


def _feed(client, user) -> dict[str, dict]:
    r = client.get("/api/student/jobs", headers=user.headers)
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()}


def _phrase_board(name: str, **adverts: list[FoundJob]) -> FakeBoard:
    """A board answering each of the given streams' FIRST phrase."""
    first = {key: job_scout.SEARCH_TERMS[key][0] for key in ("HR", "MARKETING", "DM")}
    return FakeBoard(name, by_phrase={first[key]: jobs for key, jobs in adverts.items()})


# --------------------------------------------------------------------------- #
# The catalogue and the targeting
# --------------------------------------------------------------------------- #


@requires_db
def test_every_active_stream_is_found_including_a_course_with_no_specialization(campus) -> None:
    with SessionLocal() as db:
        streams = [leaf for leaf in job_scout.leaves(db) if leaf.college_id == campus["college"]]
    assert [(leaf.course_id, leaf.track, leaf.code, leaf.name) for leaf in streams] == [
        (campus["dm"], None, "DM", "Digital Marketing"),
        (campus["gen"], "HR", "HR", "Human Resources"),
        (campus["gen"], "MARKETING", "MARKETING", "Marketing"),
    ]


@requires_db
def test_targeting_is_exact_to_the_course_and_the_specialization(client, make_user, campus, runs) -> None:
    hr_student = make_user("scout-hr", Role.STUDENT)
    mkt_student = make_user("scout-mkt", Role.STUDENT)
    dm_student = make_user("scout-dm", Role.STUDENT)
    _seat(hr_student, campus["batch_hr"])
    _seat(mkt_student, campus["batch_mkt"])
    _seat(dm_student, campus["batch_dm"])

    board = _phrase_board(
        ADZUNA,
        HR=[_advert("HR Executive", company="People Co")],
        DM=[_advert("SEO Executive", company="Clicks Co")],
    )
    summary = runs(campus, [board])
    assert summary.created == 2

    rows = {row.title: row for row in _rows(campus)}
    hr_row, dm_row = rows["HR Executive"], rows["SEO Executive"]
    assert (hr_row.college_id, hr_row.course_id, hr_row.tracks) == (campus["college"], campus["gen"], ["HR"])
    assert (dm_row.college_id, dm_row.course_id, dm_row.tracks) == (campus["college"], campus["dm"], [])

    hr_feed, mkt_feed, dm_feed = _feed(client, hr_student), _feed(client, mkt_student), _feed(client, dm_student)
    assert hr_row.id in hr_feed and hr_row.id not in mkt_feed and hr_row.id not in dm_feed
    assert dm_row.id in dm_feed and dm_row.id not in hr_feed and dm_row.id not in mkt_feed
    # Adzuna's terms ask that every advert shown say where it came from.
    assert hr_feed[hr_row.id]["source"] == "adzuna"


@requires_db
def test_only_fresher_roles_are_posted_and_internships_say_so(campus, runs) -> None:
    hr_phrase = job_scout.SEARCH_TERMS["HR"][0]
    jobs = [
        _advert("Senior HR Executive"),
        _advert("HR Executive", "3+ years of experience"),
        _advert("HR Executive", "Freshers welcome", company="Fresh Co"),
    ]

    class Board(FakeBoard):
        def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
            if phrase != hr_phrase:
                return []
            trainee = _advert("HR Trainee", "A 3-month internship with a stipend", company="Learn Co")
            return [trainee] if internship else jobs

    summary = runs(campus, [Board(ADZUNA)])
    titles = sorted(row.title for row in _rows(campus))
    assert titles == ["HR Executive", "Internship · HR Trainee"]
    assert summary.kept == 2 and summary.created == 2


# --------------------------------------------------------------------------- #
# No duplicates
# --------------------------------------------------------------------------- #


@requires_db
def test_a_second_morning_adds_nothing_and_a_second_specialization_widens_the_row(campus, runs) -> None:
    shared = _advert("Sales and HR Trainee", company="Both Co", external_id=f"shared-{uuid.uuid4().hex}")
    board = _phrase_board(ADZUNA, HR=[shared], MARKETING=[shared])

    first = runs(campus, [board])
    assert (first.created, first.merged) == (1, 1)
    (row,) = _rows(campus)
    assert row.tracks == ["HR", "MARKETING"]

    again = runs(campus, [board])
    assert (again.created, again.merged) == (0, 0)
    assert [r.id for r in _rows(campus)] == [row.id]


@requires_db
def test_an_advert_on_both_boards_is_posted_once(campus, runs) -> None:
    adzuna = _phrase_board(ADZUNA, HR=[_advert("HR Executive", company="Twin Co", provider=ADZUNA)])
    jooble = _phrase_board(JOOBLE, HR=[_advert("HR  executive", company="TWIN CO", provider=JOOBLE)])
    summary = runs(campus, [adzuna, jooble])
    assert summary.created == 1 and summary.duplicates == 1
    assert len(_rows(campus)) == 1


@requires_db
def test_an_office_posting_is_not_posted_again_and_a_closed_one_never_comes_back(campus, runs) -> None:
    with SessionLocal() as db:
        db.add(
            Job(
                title="HR Executive", company="Office Co", degree_level=DegreeLevel.PG,
                posted_on=datetime.now(timezone.utc), course_id=campus["gen"], tracks=["HR"],
            )
        )
        db.commit()
    board = _phrase_board(
        ADZUNA,
        HR=[_advert("HR Executive", company="Office Co"), _advert("HR Recruiter", company="Agent Co", external_id="ag-1")],
    )
    assert runs(campus, [board]).created == 1

    with SessionLocal() as db:
        agent_row = db.scalar(select(Job).where(Job.course_id == campus["gen"], Job.company == "Agent Co"))
        agent_row.status = STATUS_CLOSED
        db.commit()

    # Same advert from the same board, and the same advert from the OTHER board.
    jooble = _phrase_board(JOOBLE, HR=[_advert("HR Recruiter", company="Agent Co", provider=JOOBLE)])
    summary = runs(campus, [board, jooble])
    assert summary.created == 0
    statuses = sorted((row.company, row.status) for row in _rows(campus))
    assert statuses == [("Agent Co", STATUS_CLOSED), ("Office Co", STATUS_OPEN)]


@requires_db
def test_each_specialization_gets_at_most_the_daily_limit_newest_first(campus, runs) -> None:
    now = datetime.now(timezone.utc)
    limit = settings.job_scout_new_per_specialization
    adverts = [
        _advert(f"HR Executive {n}", company=f"Co {n}", posted_on=now - timedelta(hours=n))
        for n in range(limit + 5)
    ]
    summary = runs(campus, [_phrase_board(ADZUNA, HR=adverts)], now=now)
    assert summary.created == limit and summary.capped == 5
    assert summary.per_leaf["Human Resources"] == limit
    kept = {row.company for row in _rows(campus)}
    assert kept == {f"Co {n}" for n in range(limit)}, "the newest adverts are the ones posted"


# --------------------------------------------------------------------------- #
# Clearing up, and only after itself
# --------------------------------------------------------------------------- #


@requires_db
def test_expiry_closes_old_agent_postings_and_never_the_offices(campus, runs) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=settings.job_scout_max_age_days + 1)
    with SessionLocal() as db:
        agent = Job(
            source_ref=f"adzuna:old-{uuid.uuid4().hex}:{campus['gen']}", title="Old agent posting",
            company="A", degree_level=DegreeLevel.PG, posted_on=old, course_id=campus["gen"],
        )
        fresh = Job(
            source_ref=f"jooble:new-{uuid.uuid4().hex}:{campus['gen']}", title="Fresh agent posting",
            company="B", degree_level=DegreeLevel.PG, posted_on=now - timedelta(days=2), course_id=campus["gen"],
        )
        office = Job(
            title="Old office posting", company="C", degree_level=DegreeLevel.PG,
            posted_on=old, course_id=campus["gen"],
        )
        db.add_all([agent, fresh, office])
        db.commit()

    summary = runs(campus, [FakeBoard(ADZUNA)], now=now)
    assert summary.closed >= 1
    statuses = {row.title: row.status for row in _rows(campus)}
    assert statuses == {
        "Old agent posting": STATUS_CLOSED,
        "Fresh agent posting": STATUS_OPEN,
        "Old office posting": STATUS_OPEN,
    }


@requires_db
def test_an_agent_posting_is_closed_not_removed_and_an_office_posting_still_can_be(
    client, make_user, campus, runs
) -> None:
    admin = make_user("scout-admin", Role.ADMIN)
    runs(campus, [_phrase_board(ADZUNA, HR=[_advert("HR Executive", company="Keep Co")])])
    (agent_row,) = _rows(campus)
    with SessionLocal() as db:
        office = Job(title="Office", company="O", degree_level=DegreeLevel.PG,
                     posted_on=datetime.now(timezone.utc), course_id=campus["gen"])
        db.add(office)
        db.commit()
        office_id = office.id

    r = client.delete(f"/api/admin/jobs/{agent_row.id}", headers=admin.headers)
    assert r.status_code == 409, r.text
    assert "Close it instead" in r.json()["detail"]
    assert client.post(f"/api/admin/jobs/{agent_row.id}/close", headers=admin.headers).status_code == 200
    assert client.delete(f"/api/admin/jobs/{office_id}", headers=admin.headers).status_code == 204

    sheet = {row["id"]: row for row in client.get("/api/admin/jobs", headers=admin.headers).json()}
    assert sheet[agent_row.id]["source"] == "adzuna" and sheet[agent_row.id]["status"] == STATUS_CLOSED


@requires_db
def test_skills_named_in_an_advert_become_catalogue_slugs(campus, runs) -> None:
    tag = uuid.uuid4().hex[:6]
    slug = f"scout-tally-{tag}"
    with SessionLocal() as db:
        db.add(Skill(slug=slug, name=f"Tally Prime {tag}", category="Business", aliases=[f"tally erp {tag}"]))
        db.commit()
    try:
        board = _phrase_board(
            ADZUNA, HR=[_advert("HR Executive", f"Payroll on TALLY ERP {tag} and attendance", company="Ledger Co")]
        )
        runs(campus, [board])
        (row,) = _rows(campus)
        assert slug in row.required_skills
    finally:
        with SessionLocal() as db:
            db.execute(delete(Skill).where(Skill.slug == slug))
            db.commit()


# --------------------------------------------------------------------------- #
# The receipt, the dry run and the switch
# --------------------------------------------------------------------------- #


@requires_db
def test_a_run_leaves_one_system_receipt_the_sheet_reads(client, make_user, campus, runs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "adzuna_app_id", "id")
    monkeypatch.setattr(settings, "adzuna_app_key", "key")
    monkeypatch.setattr(settings, "jooble_api_key", "")
    monkeypatch.setattr(settings, "job_scout_enabled", "")
    admin = make_user("scout-status", Role.ADMIN)
    now = datetime.now(timezone.utc)
    runs(campus, [_phrase_board(ADZUNA, HR=[_advert("HR Executive", company="Receipt Co")])], now=now)

    with SessionLocal() as db:
        receipts = db.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == job_scout.AUDIT_ENTITY, AuditEvent.occurred_at == now)
        ).all()
    assert len(receipts) == 1
    (receipt,) = receipts
    assert receipt.actor_type == "SYSTEM" and receipt.actor_user_id is None
    assert receipt.after_json["created"] == 1

    status = client.get("/api/admin/jobs/scout-status", headers=admin.headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["active"] is True
    assert body["created"] == 1 and body["closed"] is not None
    assert datetime.fromisoformat(body["last_run_at"]) == now
    assert [(s["key"], s["configured"]) for s in body["sources"]] == [("adzuna", True), ("jooble", False)]

    student = make_user("scout-status-student", Role.STUDENT)
    assert client.get("/api/admin/jobs/scout-status", headers=student.headers).status_code == 403


@requires_db
def test_a_dry_run_writes_nothing(campus, runs) -> None:
    now = datetime.now(timezone.utc)
    summary = runs(campus, [_phrase_board(ADZUNA, HR=[_advert("HR Executive", company="Dry Co")])], now=now, dry_run=True)
    assert summary.created == 1 and summary.dry_run
    assert _rows(campus) == []
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.occurred_at == now)) is None


@requires_db
def test_one_board_failing_does_not_stop_the_other(campus, runs) -> None:
    down = FakeBoard(ADZUNA, fail=SourceError(ADZUNA, "Adzuna refused the key (403).", stop=True))
    up = _phrase_board(JOOBLE, HR=[_advert("HR Executive", company="Still Co", provider=JOOBLE)])
    summary = runs(campus, [down, up])
    assert summary.created == 1
    assert summary.errors == {ADZUNA: "Adzuna refused the key (403)."}
    assert not summary.every_source_failed
    # A refused key is not asked again eleven more times this morning.
    assert len(down.calls) == 1


def test_the_job_exits_zero_with_no_keys_and_one_when_every_board_failed(monkeypatch) -> None:
    # main() would register this pytest process as reep-scheduled-jobs, and
    # every later app startup in the session would then be refused Sentry.
    monkeypatch.setattr(job_scout_job, "init_sentry", lambda *a, **kw: False)
    monkeypatch.setattr(job_scout_job, "sources_from_settings", lambda: [])
    assert job_scout_job.main([]) == 0

    class Summary:
        busy = False
        dry_run = False
        leaves = calls = found = stale = suspicious = kept = created = merged = duplicates = capped = closed = 0
        per_leaf: dict = {}
        errors = {ADZUNA: "Adzuna could not be reached (ConnectError)."}
        every_source_failed = True

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(job_scout_job, "sources_from_settings", lambda: [FakeBoard(ADZUNA)])
    monkeypatch.setattr(job_scout_job, "SessionLocal", Session)
    monkeypatch.setattr(job_scout_job.job_scout, "run", lambda db, **kw: Summary())
    assert job_scout_job.main([]) == 1
    Summary.every_source_failed = False
    assert job_scout_job.main(["--dry-run"]) == 0
