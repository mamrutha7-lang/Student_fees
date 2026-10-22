"""The 2026-08 scalability-fix guards, pinned.

Three guards landed together and each is the kind that gets deleted the first
time it inconveniences someone, so each is pinned the way the boot guard is:

* the per-user LLM rate limit (app/ratelimit.py) — 429 with Retry-After, unit
  and end-to-end through POST /api/agent/chat;
* the per-student DAILY interview cap (_open_records raising _DailyCapReached
  -> close 4015) — the volume half of the per-user cap, refused before any row
  is written or any upstream socket billed;
* GET /api/student/overview — the one-request composition of the landing
  page's ten reads, whose keys the Angular component destructures by name.

The autouse conftest fixture resets the limiter and the leaderboard cache
before every test, so these tests build their own warm state and assert on it.
"""

import types
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from conftest import requires_db

from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models.agent_run import AgentRun
from app.models.interview import InterviewConsent, InterviewSession
from app.models.user import Role, Student

STUB_REPLY = "STUB REPLY from the offline assistant."


@pytest.fixture
def stub_llm(monkeypatch):
    """Same shape as test_conversations.stub_llm: /chat deterministic + offline."""
    import app.routers.agent as agent

    monkeypatch.setattr(
        agent, "llm_config",
        lambda: types.SimpleNamespace(provider="stub", model="stub-model"),
    )
    monkeypatch.setattr(agent, "complete_chat", lambda messages, **kwargs: STUB_REPLY)
    return STUB_REPLY


# ---------------------------------------------------------------------------
# The rate limiter, as a unit
# ---------------------------------------------------------------------------
def test_rate_limiter_allows_up_to_limit_then_refuses(monkeypatch):
    monkeypatch.setattr(settings, "llm_requests_per_minute", 3)
    uid = f"unit-{uuid.uuid4().hex[:8]}"
    assert ratelimit._check(uid) is None
    assert ratelimit._check(uid) is None
    assert ratelimit._check(uid) is None
    wait = ratelimit._check(uid)
    assert wait is not None and 0 < wait <= 60.0


def test_rate_limiter_is_per_user(monkeypatch):
    """One student's loop must not spend another student's budget."""
    monkeypatch.setattr(settings, "llm_requests_per_minute", 1)
    a, b = f"unit-a-{uuid.uuid4().hex[:6]}", f"unit-b-{uuid.uuid4().hex[:6]}"
    assert ratelimit._check(a) is None
    assert ratelimit._check(a) is not None  # a is spent...
    assert ratelimit._check(b) is None  # ...and b is untouched


# ---------------------------------------------------------------------------
# The rate limiter, end to end: 429 + Retry-After on the chat surface
# ---------------------------------------------------------------------------
@requires_db
def test_chat_answers_429_with_retry_after_over_the_limit(
    client, stub_llm, make_user, monkeypatch
):
    monkeypatch.setattr(settings, "llm_requests_per_minute", 2)
    s = make_user("ratelimit")
    try:
        for i in range(2):
            r = client.post(
                "/api/agent/chat", headers=s.headers, json={"message": f"hi {i}"}
            )
            assert r.status_code == 200, r.text
        r = client.post("/api/agent/chat", headers=s.headers, json={"message": "hi 3"})
        assert r.status_code == 429, r.text
        # Retry-After is the header that makes 429 mean "wait seconds", not
        # "the assistant is down" — a client (or a student reading devtools)
        # must be able to tell the difference.
        assert int(r.headers["Retry-After"]) >= 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(AgentRun).where(AgentRun.actor_id == s.user_id))
            db.commit()


# ---------------------------------------------------------------------------
# The daily interview cap: refused BEFORE anything is written
# ---------------------------------------------------------------------------
@requires_db
def test_daily_interview_cap_refuses_before_writing(make_user, monkeypatch):
    """B6.4 made this TWO ceilings, and the test covers both.

    It used to open two sessions of any status and expect the third to be
    refused. The practice allowance now counts COMPLETED interviews only — an
    interview that dropped out at minute two no longer costs a student a turn —
    so the two have to be finished before the third is refused. The property the
    test was written for is unchanged and is still asserted: the refusal happens
    BEFORE anything is written.

    The second half is the control 04-backend-changes.md would have deleted.
    Counting completions alone hands a reconnect loop unlimited billable
    handshakes, so `attempt_cap` counts every row whatever its status — and this
    proves it refuses on rows that never reached `completed`.
    """
    from app.routers.interview import _DailyCapReached, _open_records

    monkeypatch.setattr(settings, "interview_max_per_student_per_day", 2)
    monkeypatch.setattr(settings, "interview_max_attempts_per_student_per_day", 3)
    s = make_user("dailycap")
    try:
        with SessionLocal() as db:
            student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
            db.add(
                InterviewConsent(
                    user_id=s.user_id,
                    version=settings.interview_consent_version,
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=False,
                )
            )
            db.commit()

        # Two interviews open normally, and are FINISHED — a `running` row is
        # the concurrency cap's business (4012), not this one's.
        for i in range(2):
            opened = _open_records(s.user_id, Role.STUDENT, student_id, f"cap{i}", None)
            with SessionLocal() as db:
                db.get(InterviewSession, opened.interview_session_id).status = "completed"
                db.commit()

        # ...and the third is refused with the cap exception, leaving the
        # session count where it was: the refusal writes nothing.
        with pytest.raises(_DailyCapReached) as caught:
            _open_records(s.user_id, Role.STUDENT, student_id, "cap2", None)
        assert caught.value.which == "daily"
        with SessionLocal() as db:
            rows = db.scalars(
                select(InterviewSession.id).where(
                    InterviewSession.student_id == student_id
                )
            ).all()
            assert len(rows) == 2

        # THE SPEND CEILING, on rows that never completed. Raise the practice
        # allowance out of the way and abandon three attempts: the student has
        # completed nothing and is still stopped.
        monkeypatch.setattr(settings, "interview_max_per_student_per_day", 50)
        with SessionLocal() as db:
            for i in range(3):
                db.add(
                    InterviewSession(
                        student_id=student_id,
                        status="abandoned",
                        started_at=datetime.now(timezone.utc),
                        heartbeat_at=datetime.now(timezone.utc),
                        conn_id=f"attempt{i}",
                    )
                )
            db.commit()
        with pytest.raises(_DailyCapReached) as caught:
            _open_records(s.user_id, Role.STUDENT, student_id, "cap3", None)
        assert caught.value.which == "attempts"
    finally:
        # interview_sessions reference the conversation make_user's teardown
        # deletes, so these rows must go first or that teardown FK-fails.
        with SessionLocal() as db:
            db.execute(
                delete(InterviewSession).where(
                    InterviewSession.student_id == student_id
                )
            )
            db.execute(
                delete(InterviewConsent).where(InterviewConsent.user_id == s.user_id)
            )
            db.commit()


# ---------------------------------------------------------------------------
# The cross-worker concurrency cap: the DB sees what the per-process limiter
# cannot — a live interview held by ANOTHER worker
# ---------------------------------------------------------------------------
@requires_db
def test_open_records_refuses_when_live_sessions_exist_fleet_wide(
    make_user, monkeypatch
):
    from app.routers.interview import _UserSessionCapReached, _open_records

    monkeypatch.setattr(settings, "interview_max_sessions_per_user", 2)
    s = make_user("fleetcap")
    try:
        with SessionLocal() as db:
            student_id = db.scalar(select(Student.id).where(Student.user_id == s.user_id))
            db.add(
                InterviewConsent(
                    user_id=s.user_id,
                    version=settings.interview_consent_version,
                    scope_live_ai=True,
                    scope_store_transcript=True,
                    scope_store_audio=False,
                )
            )
            db.commit()

        # Two live interviews (rows stay `running` with a fresh heartbeat, as
        # if held open on two other workers)...
        _open_records(s.user_id, Role.STUDENT, student_id, "fleet0", None)
        _open_records(s.user_id, Role.STUDENT, student_id, "fleet1", None)

        # ...and a third handshake is refused by the DATABASE's count, which is
        # the only party that sees every worker.
        with pytest.raises(_UserSessionCapReached):
            _open_records(s.user_id, Role.STUDENT, student_id, "fleet2", None)

        # A dead worker's row must NOT lock the student out: age the heartbeats
        # past the liveness grace and the same open succeeds.
        with SessionLocal() as db:
            from datetime import datetime, timedelta, timezone

            db.execute(
                InterviewSession.__table__.update()
                .where(InterviewSession.student_id == student_id)
                .values(
                    heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=600)
                )
            )
            db.commit()
        _open_records(s.user_id, Role.STUDENT, student_id, "fleet3", None)
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(InterviewSession).where(
                    InterviewSession.student_id == student_id
                )
            )
            db.execute(
                delete(InterviewConsent).where(InterviewConsent.user_id == s.user_id)
            )
            db.commit()


# ---------------------------------------------------------------------------
# The aggregate overview: one request, the eleven keys the client destructures
# ---------------------------------------------------------------------------
@requires_db
def test_overview_composes_all_ten_reads(client, make_user):
    s = make_user("overview")
    r = client.get("/api/student/overview", headers=s.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "dashboard",
        "attendance",
        "results",
        "streak",
        "swoc",
        "mocks",
        "skills",
        "next_actions",
        "placement_readiness",
        "recommendations",
        "academics",
        "career_goal",
    }
    # A student the office has loaded no role preferences for gets an empty
    # list, never a guessed goal, and the landing draws no card.
    assert body["career_goal"] == {"preferences": []}
    # A brand-new student still gets a whole document — the aggregate must not
    # turn "no data yet" into a missing key or a 500.
    assert body["dashboard"] is not None
    assert isinstance(body["results"], list)
    # The Academic History block rides along: an empty chain and a zero gap,
    # never a missing key, so the landing can render its "no gaps" state.
    assert body["academics"]["qualifications"] == []
    assert body["academics"]["gap"]["total_mo"] == 0
