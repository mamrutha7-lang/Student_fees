"""The daily job agent against the real world (2026-10-23): a job board that
sends something odd, an advert written to cheat students, an office that
deletes a college, an operator who presses "run" at six in the morning.

Every test here was written BEFORE its fix and seen failing on the code the
agent shipped with (PR #217's first commit); the module docstring of each
fix says what it was. What each one holds down:

1. ONE BAD ADVERT NEVER COSTS THE MORNING. A field of the wrong shape, an
   invisible NUL, an absurd id or a board that raises something unexpected
   is skipped or recorded, and every good advert is still posted.
2. A SCAM ADVERT IS NEVER POSTED. Anything asking a student for money, and
   the "data entry / work from home / earn per day" family, stays off the
   college's own portal.
3. WORDS MEAN WHAT THEY SAY. "Internship" only on an internship; "minimum 3
   years" is not a fresher job; "Relationship Manager" is.
4. NOTHING LEAKS OR PILES UP. A posting whose course was deleted is closed,
   an old advert takes no place in the day, one college's office posting
   does not hide another college's job, and a second run at the same time
   waits its turn instead of failing.
5. A STUDENT IS NEVER TOLD A FALSE "100% MATCH". A posting that lists no
   skills says so, office postings included (the owner's decision).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select, text

from conftest import requires_db

from app import job_scout, job_scout_job
from app.config import Settings, settings
from app.db import SessionLocal
from app.job_sources import (
    ADZUNA,
    JOOBLE,
    AdzunaSource,
    FoundJob,
    JoobleSource,
    plain_text,
)
from app.jobs_visibility import STATUS_CLOSED, STATUS_OPEN
from app.models.institution import STATUS_ACTIVE, College
from app.models.job import DegreeLevel, Job
from app.models.user import Role

from test_job_scout import (  # noqa: F401 - the fixtures are used by name
    FakeBoard,
    _advert,
    _feed,
    _phrase_board,
    _rows,
    _seat,
    campus,
    runs,
)


def _client(page: dict) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page)))


def _unpaced(source):
    """The boards space their calls to stay inside the free tier; a test makes
    a dozen calls and has no allowance to protect."""
    source.min_interval = 0.0
    return source


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 1. One bad advert never costs the morning
# --------------------------------------------------------------------------- #


def test_a_field_of_the_wrong_shape_skips_that_advert_and_keeps_the_page() -> None:
    page = {
        "results": [
            {"id": "1", "title": "HR Executive", "company": "Acme as text", "redirect_url": "https://a/1"},
            {"id": "2", "title": "HR Executive", "company": {"display_name": "Acme"}, "location": "Pune",
             "redirect_url": "https://a/2"},
            {"id": "3", "title": "HR Trainee", "company": {"display_name": "Good Co"},
             "location": {"display_name": "Bengaluru"}, "redirect_url": "https://a/3"},
            "not even an object",
        ]
    }
    jobs = _unpaced(AdzunaSource("id", "key", client=_client(page))).search("HR", internship=False)
    # A name sent as plain text instead of {"display_name": ...} is still a
    # name; a row that is not an object at all is skipped, and nothing raises.
    assert [(j.external_id, j.company, j.location) for j in jobs] == [
        ("1", "Acme as text", None),
        ("2", "Acme", "Pune"),
        ("3", "Good Co", "Bengaluru"),
    ]


def test_invisible_characters_never_reach_the_database() -> None:
    assert plain_text("HR\x00 Exec\x07utive\x1b") == "HR Executive"
    assert plain_text("Line one\nline two\ttab") == "Line one line two tab"


def test_an_absurd_advert_id_is_skipped() -> None:
    page = {"jobs": [
        {"id": "9" * 5000, "title": "HR Executive", "company": "A", "link": "https://j/1"},
        {"id": "77", "title": "HR Executive", "company": "B", "link": "https://j/2"},
    ]}
    jobs = _unpaced(JoobleSource("key", client=_client(page))).search("HR", internship=False)
    assert [j.external_id for j in jobs] == ["77"]


def test_a_control_character_in_an_id_or_a_link_skips_that_advert() -> None:
    """The id and the link are written to the row as they arrive, not through
    `plain_text`, so a NUL in either would fail the morning's commit too."""
    page = {"jobs": [
        {"id": "a\x00b", "title": "HR Executive", "company": "A", "link": "https://j/1"},
        {"id": "88", "title": "HR Executive", "company": "B", "link": "https://j/2\x00"},
        {"id": "99", "title": "HR Executive", "company": "C", "link": "https://j/3"},
    ]}
    jobs = _unpaced(JoobleSource("key", client=_client(page))).search("HR", internship=False)
    assert [j.external_id for j in jobs] == ["99"]


@requires_db
def test_one_poisoned_advert_does_not_lose_the_other_adverts(campus, runs) -> None:
    """The whole morning is one transaction. A NUL in a title, or an id too
    long for the unique index, used to fail the COMMIT, so every good advert
    found that morning was lost with it."""
    tag = uuid.uuid4().hex[:6]
    page = {"jobs": [
        {"id": f"nul-{tag}", "title": "HR\x00 Executive", "company": f"Nul Co {tag}", "link": "https://j/1"},
        {"id": "9" * 5000, "title": "HR Recruiter", "company": f"Long Co {tag}", "link": "https://j/2"},
        {"id": f"ok-{tag}", "title": "HR Trainee", "company": f"Good Co {tag}", "link": "https://j/3"},
    ]}
    summary = runs(campus, [_unpaced(JoobleSource("key", client=_client(page)))])
    companies = {row.company: row for row in _rows(campus)}
    assert f"Good Co {tag}" in companies, summary
    assert companies[f"Nul Co {tag}"].title == "HR Executive"
    assert f"Long Co {tag}" not in companies


@requires_db
def test_a_board_that_breaks_unexpectedly_does_not_stop_the_other(campus, runs) -> None:
    class Exploding(FakeBoard):
        def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
            self.calls.append((phrase, internship))
            raise KeyError("results")

    down = Exploding(ADZUNA)
    up = _phrase_board(JOOBLE, HR=[_advert("HR Executive", company="Still Here Co", provider=JOOBLE)])
    summary = runs(campus, [down, up])
    assert [row.company for row in _rows(campus)] == ["Still Here Co"]
    assert ADZUNA in summary.errors and "KeyError" in summary.errors[ADZUNA]
    assert len(down.calls) == 1, "a board that broke is not asked again this morning"
    assert not summary.every_source_failed


# --------------------------------------------------------------------------- #
# 2. A scam advert is never posted
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("Data Entry - Work From Home", "Registration fee of Rs 500, refundable"),
        ("HR Executive", "Candidates must pay a security deposit before joining"),
        ("Back Office Executive", "Processing fee applicable. Earn 2000 per day"),
        ("Marketing Executive", "Pay Rs.999 to apply, guaranteed placement"),
        ("Home Based Data Entry Job", "Freshers welcome"),
        ("Part Time Work From Home - Earn Daily", ""),
        # A negation in the sentence BEFORE does not cancel the ask.
        ("HR Executive", "We do not charge. Registration fee Rs 500 at joining"),
    ],
)
def test_an_advert_asking_for_money_or_promising_easy_money_is_suspicious(title: str, description: str) -> None:
    assert job_scout.is_suspicious(_advert(title, description))


@pytest.mark.parametrize(
    ("title", "description"),
    [
        ("HR Executive", "Freshers welcome. No fee is charged at any stage."),
        ("Financial Analyst", "Work from office in Bengaluru. Excel and financial modelling."),
        ("Marketing Executive", "Hybrid role, travel allowance paid"),
        ("Business Analyst", "There is no registration fee. Work from home two days a week."),
        ("Accounts Executive", "Duties include data entry in Tally and GST filing"),
    ],
)
def test_an_ordinary_advert_is_not_suspicious(title: str, description: str) -> None:
    assert not job_scout.is_suspicious(_advert(title, description))


@requires_db
def test_a_scam_advert_is_not_posted(campus, runs) -> None:
    tag = uuid.uuid4().hex[:6]
    board = _phrase_board(
        ADZUNA,
        HR=[
            _advert("Data Entry - Work From Home", "Registration fee of Rs 500", company=f"Scam Co {tag}"),
            _advert("HR Executive", "Freshers welcome", company=f"Real Co {tag}"),
        ],
    )
    runs(campus, [board])
    assert [row.company for row in _rows(campus)] == [f"Real Co {tag}"]


# --------------------------------------------------------------------------- #
# 3. Words mean what they say
# --------------------------------------------------------------------------- #


def test_a_full_time_job_found_by_the_internship_search_is_not_called_an_internship() -> None:
    full_time = _advert("HR Executive", "Full time role, internship experience preferred")
    assert job_scout.posting_title(full_time, job_scout.KIND_INTERNSHIP) == "HR Executive"
    six_months = _advert("HR Trainee", "This is a 6-month internship with a stipend")
    assert job_scout.posting_title(six_months, job_scout.KIND_INTERNSHIP) == "Internship · HR Trainee"


@pytest.mark.parametrize(
    ("title", "description", "fresher"),
    [
        ("Business Analyst", "Minimum 3 years in analytics", False),
        ("Analyst", "Candidates with 3 to 5 years in banking", False),
        ("Sales Executive", "2-4 yrs in FMCG sales", False),
        ("Analyst", "At least 2 years in a similar role", False),
        ("Relationship Manager", "Freshers welcome", True),
        ("Analyst", "A 2 years MBA is required", True),
        ("Analyst", "0 to 1 years", True),
        ("HR Executive", "Age 21 to 28 years. Freshers welcome", True),
    ],
)
def test_experience_is_read_without_the_word_experience(title: str, description: str, fresher: bool) -> None:
    assert job_scout.is_fresher_role(_advert(title, description)) is fresher


# --------------------------------------------------------------------------- #
# 4. Nothing leaks or piles up
# --------------------------------------------------------------------------- #


@requires_db
def test_an_old_advert_is_skipped_and_takes_no_place_in_the_day(campus, runs) -> None:
    now = _now()
    tag = uuid.uuid4().hex[:6]
    stale = _advert("HR Executive", company=f"Stale Co {tag}",
                    posted_on=now - timedelta(days=settings.job_scout_max_age_days + 5))
    fresh = _advert("HR Recruiter", company=f"Fresh Co {tag}", posted_on=now - timedelta(hours=2))
    summary = runs(campus, [_phrase_board(ADZUNA, HR=[stale, fresh])], now=now)
    assert [row.company for row in _rows(campus)] == [f"Fresh Co {tag}"]
    assert summary.created == 1


@requires_db
def test_a_posting_whose_course_was_deleted_is_closed_and_not_shown_to_everyone(campus, runs) -> None:
    """Deleting a college clears `jobs.college_id` and `course_id`
    (`college_deletion.COLLEGE_POLICY`), and a cleared row reads as "every
    college, every course" to `visible_clauses`. An agent posting only ever
    exists for one course, so one with no course is an orphan."""
    with SessionLocal() as db:
        orphan = Job(
            source_ref=f"adzuna:orphan-{uuid.uuid4().hex}:gone", title="HR Executive", company="Orphan Co",
            degree_level=DegreeLevel.PG, posted_on=_now() - timedelta(days=1), tracks=["HR"],
        )
        db.add(orphan)
        db.commit()
        orphan_id = orphan.id
    try:
        runs(campus, [FakeBoard(ADZUNA)])
        with SessionLocal() as db:
            assert db.get(Job, orphan_id).status == STATUS_CLOSED
    finally:
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.id == orphan_id))
            db.commit()


@requires_db
def test_another_colleges_office_posting_does_not_hide_this_colleges_job(campus, runs) -> None:
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        other = College(code=f"JO{tag.upper()}", name="Other College", status=STATUS_ACTIVE)
        db.add(other)
        db.flush()
        office = Job(title="HR Executive", company=f"Shared Co {tag}", degree_level=DegreeLevel.PG,
                     posted_on=_now(), college_id=other.id)
        db.add(office)
        db.commit()
        other_id, office_id = other.id, office.id
    try:
        runs(campus, [_phrase_board(ADZUNA, HR=[_advert("HR Executive", company=f"Shared Co {tag}")])])
        assert [row.company for row in _rows(campus)] == [f"Shared Co {tag}"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.id == office_id))
            db.execute(delete(College).where(College.id == other_id))
            db.commit()


@requires_db
def test_a_second_run_at_the_same_time_waits_its_turn(campus, runs) -> None:
    """Somebody presses the Ops task's job-scout at 06:00. The second run used
    to collide on `source_ref`'s unique index and fail with an alert."""
    with SessionLocal() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": job_scout.RUN_LOCK_KEY})
        try:
            summary = runs(campus, [_phrase_board(ADZUNA, HR=[_advert("HR Executive", company="Busy Co")])])
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": job_scout.RUN_LOCK_KEY})
    assert summary.busy
    assert _rows(campus) == []


def test_a_busy_run_exits_quietly(monkeypatch) -> None:
    class Summary:
        busy = True
        dry_run = False
        errors: dict = {}
        every_source_failed = False

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(job_scout_job, "init_sentry", lambda *a, **kw: False)
    monkeypatch.setattr(job_scout_job, "sources_from_settings", lambda: [FakeBoard(ADZUNA)])
    monkeypatch.setattr(job_scout_job, "SessionLocal", Session)
    monkeypatch.setattr(job_scout_job.job_scout, "run", lambda db, **kw: Summary())
    assert job_scout_job.main([]) == 0


def test_the_daily_limit_is_five_per_specialization() -> None:
    assert Settings.model_fields["job_scout_new_per_specialization"].default == 5


# --------------------------------------------------------------------------- #
# 5. A student is never told a false "100% match"
# --------------------------------------------------------------------------- #


@requires_db
def test_a_posting_that_lists_no_skills_has_no_match_percentage(client, make_user, campus) -> None:
    student = make_user("hostile-match", Role.STUDENT)
    _seat(student, campus["batch_hr"])
    with SessionLocal() as db:
        bare = Job(title="No Skills Listed", company="Bare Co", degree_level=DegreeLevel.PG,
                   posted_on=_now(), course_id=campus["gen"], required_skills=[])
        listed = Job(title="Skills Listed", company="Listed Co", degree_level=DegreeLevel.PG,
                     posted_on=_now(), course_id=campus["gen"], required_skills=["no-such-skill"])
        db.add_all([bare, listed])
        db.commit()
        bare_id, listed_id = bare.id, listed.id
    feed = _feed(client, student)
    assert feed[bare_id]["match_percent"] is None
    assert feed[listed_id]["match_percent"] == 0.0
