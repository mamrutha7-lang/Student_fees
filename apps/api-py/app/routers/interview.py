"""Realtime AI mock interviewer — the student-facing assistant (Assistant V2).

  GET /api/interview/status   -> is an interview usable right now? (STUDENT)
  WS  /api/interview          -> one interview, relayed (STUDENT)

The engines themselves are app/interview_nova.py (Amazon Nova 2 Sonic, the
default) and app/interview_local.py (nothing leaves the machine); the contracts
they share are app/interview_core.py. This module is the boundary:
authentication, the STUDENT check, the concurrency cap, the server-owned
conversation, and the turn writer. It replaces POST /api/agent/ask as the
assistant screen's entry point — see the header on app/routers/agent.py, which
stays mounted and working as the rollback path.

AUTH IS REEP'S. The socket authenticates with the same httpOnly `reep_session`
cookie and the same verify_session_token as every HTTP route; no second token
scheme exists here. A browser WebSocket cannot set headers, but it DOES send
cookies on a SAME-ORIGIN handshake, and /api is same-origin by construction —
apps/web/proxy.conf.json forwards /api to this process with `"ws": true` in dev,
and there is one origin in production. The cookie is SameSite=Lax
(app/routers/auth.py), which is also why a cross-site page cannot carry it onto
this handshake at all.

RULE 1 (AGENTS.md). The hosted engine speaks to a REMOTE provider, so no
student record enters it. This module reads the session ONLY to answer "who owns
the conversation these turns are written to" — that id never leaves the process.
The sole thing sent upstream is the fixed persona in app/interview_core.py, the
fixed directives app/interview_matrix.py composes, and the student's microphone. Nothing here imports app.assistant_tools, app.knowledge or
app.ai.llm, and no student field is ever placed on the uplink.

PERSISTENCE, IN TWO PLACES THAT MUST NOT DISAGREE. Every turn still lands in the
SAME conversations/messages tables the text agent uses, through
app/conversations.py, so GET /api/agent/history returns them
unchanged and the AGENTS.md runbook query
    select channel, count(*), max(created_at) from messages group by channel;
grows an `interview` row. That contract does not bend.

ALONGSIDE it -- never instead of it -- each turn also writes an `interview_turns`
row carrying what a shared `messages` row cannot: the phase it happened in,
whether the transcriber actually heard it, and whether it advanced the arc. The
two inserts share ONE transaction (interview-engine-v3 §6.5). A second
fire-and-forget writer was considered and rejected: it doubles the pool pressure
at interview_max_sessions, and it can PARTIALLY succeed -- `messages` has the
turn and `interview_turns` does not, or the reverse -- which puts the runbook
query in the position of reporting "saved fine" while the reviewable record is
missing turns. That is the exact silent failure the runbook exists to catch,
reintroduced somewhere new.

An `interview_sessions` row is opened beside the conversation and closed by
whichever of three layers gets there first: the engine's own finalizer (Layer 1),
this module's `finally` backstop (Layer 2), and retention's orphan sweeper
(Layer 3, for the process that was killed). A `running` row that is never closed
is worse than no row -- it is a record that lies.

CONSENT IS ENFORCED HERE, and it is the last gate the socket passes. No live
`interview_consents` row for the CURRENT version -> the interview never opens and
the socket closes 4013; the grant revoked while the interview is running -> the
heartbeat notices within a minute and the socket closes 4014. Both gates read a
row, never a client claim, and the exact grant is pinned on
`interview_sessions.consent_id` so "was this student consented, to what wording,
at the time of interview X" survives every later revocation. The gate could only
be turned on AFTER the browser started posting grants (interview-engine-v3 §8.3),
which is why it arrived a step later than the table it reads.

AUDIO IS OFF, AND "OFF" IS TWO INDEPENDENT SWITCHES. This module is where the
recorder is constructed (app.interview_audio.recorder_for), and it hands back
None unless INTERVIEW_RECORDING_ENABLED is true AND the student holds a live
`scope_store_audio` grant. Neither is true in a default deployment, so nothing is
written. When both are true, the five `audio_*` columns written by the Layer 1
finalizer are the interview record's ONLY account of what was kept -- retention
deletes recordings by reading them -- which is why they are set in the same
UPDATE as the terminal status and not in a second write that could fail on its
own.

AND ONE ROW PER WAV IN `archived_documents`, WHICH ANSWERS A DIFFERENT QUESTION.
Those five columns say what THIS interview kept, and they are deleted with the
interview after INTERVIEW_RETENTION_DAYS (and outright by
`python -m app.purge_people`). The nightly sweep has by then copied the files
into the Object-Locked archive under their own names and nothing else, so the
moment the row goes the bucket holds a named student's recorded voice that
nobody can name. The Layer 1 finalizer writes the manifest row that answers it
-- see `_record_audio_in_the_manifest`. It is an INDEX and never a second set of
audio columns: retention still sweeps the store by session id and never by what
a row believes.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any, Final
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import conversations as convo
from ..config import settings
from ..db import SessionLocal, engine, get_db
from ..governance import FEATURE_DISABLED_DEFAULT_MESSAGE, FeatureState, feature_state
from ..identity import get_current_session, get_ws_session
from ..interview_audio import (
    SKIP_NOTHING_CAPTURED,
    available_tracks,
    download_name,
    recorder_for,
    recorder_or_reason,
    track_path,
)
from .. import tracing
from ..interview_matrix import Specialization
# B6.1/B6.4 — the college's policy and the two cap ceilings. A SERVICE MODULE,
# not a router: `app/routers/interview_policy.py` serves the same answer over
# HTTP and both read `app/interview_policy.py`, so the socket and the card can
# never disagree about what a college decided. Rule 1 is untouched — a policy is
# staff-authored numbers and nothing here reaches a model.
from ..interview_policy import (
    EffectivePolicy,
    cap_message,
    evaluate_caps,
    policy_for_student,
    default_policy,
)
from ..interview_tracks import resolve_specialization
# B6.2. The four numbers are copied out of the record at finalization, because
# `retention.purge_expired` deletes the record and keeping the trend is the
# other promise. One builder, used by all three finalization layers and by the
# backfill — see app/interview_summary.py for why it is not four copies.
from ..interview_summary import ensure_summary
from ..interview_core import (
    _CLOSE_CONSENT_REQUIRED,
    _CLOSE_CONSENT_REVOKED,
    _CLOSE_DAILY_CAP,
    _CLOSE_FORBIDDEN_ORIGIN,
    _CLOSE_GOING_AWAY,
    _CLOSE_IDLE,
    _CLOSE_INTERNAL,
    _CLOSE_NOT_CONFIGURED,
    _CLOSE_OK,
    _CLOSE_OVERLOADED,
    _CLOSE_SESSION_CAP,
    _CLOSE_UNKNOWN_SPECIALIZATION,
    _CLOSE_USER_SESSION_CAP,
    _REFUSED_BY_USER,
    _ConnectionLimiter,
    _ReportRecord,
    _SessionOutcome,
    _TurnRecord,
    _TurnWriteRefused,
    _close_downstream,
    ask_all_sessions_to_stop,
)
from ..interview_core import InterviewEngine  # the contract both engines satisfy
from ..document_manifest import record_existing
from ..models.archived_document import DocumentOwnerKind
from ..models.interview import (
    InterviewConsent,
    InterviewEvaluation,
    InterviewSession,
    InterviewTurn,
)
from ..models.user import Role

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interview", tags=["interview"])

# 1008 is the code FastAPI's own WebSocket validation handler uses for a policy
# refusal, and get_ws_session already raises with it. "Not signed in" and "not a
# student" deliberately SHARE it: both are "you are not allowed here", the client
# has one sentence covering both (CLOSE_MESSAGES in interview.service.ts), and a
# private-use code the client did not map would degrade to "closed unexpectedly".
_CLOSE_NOT_A_STUDENT = 1008

# The `student.assistant` switch is off for this student (B2.2). A PRIVATE-USE
# code of its own rather than sharing 1008, because this refusal carries the
# office's own sentence in `reason` and the client should print THAT rather than
# its generic "you are not allowed here" — the whole point of `student_message`
# is that the student reads the words somebody chose for them. It sits with the
# other 40xx refusals in interview_core.py's numbering and is declared HERE, next
# to _CLOSE_NOT_A_STUDENT, for the reason that one is: role and feature scoping
# are the ROUTER's job in this repo, and neither engine ever learns about them.
_CLOSE_FEATURE_DISABLED = 4016

# The channel this surface writes under. "interview", NOT "voice": both are
# spoken, but they are different products with different retention questions, and
# folding them together would leave the runbook unable to answer "did the
# interviewer save anything" independently of the text agent. Message.channel is a plain
# String column (app/models/conversation.py), so this needs no migration, and
# conversations.history() filters on is_final only — never on channel — so
# GET /api/agent/history returns these turns like any other.
_CHANNEL = "interview"

# PER-WORKER, for the reason app/config.py gives on interview_max_sessions: one
# CPython process cannot carry the full student body's audio, so N uvicorn
# workers give N times this. Module-level rather than app.state because the cap
# is a property of the process, and a second FastAPI app in one process (tests)
# must not silently double it.
#
# The second number is the per-USER cap (audit H1): the worker cap counts
# sessions and never asks whose they are, so one student looping
# `new WebSocket('/api/interview')` from devtools took every slot and everyone
# else was answered 1013 -- each of those sockets billing an upstream Realtime
# session from the handshake's response.create with no microphone input at all.
_LIMITER = _ConnectionLimiter(
    settings.interview_max_sessions, settings.interview_max_sessions_per_user
)

# The model's raw scorecard text kept on the evaluation row for the Main Admin to
# read when a parse goes wrong. Truncated because it is unbounded model output
# on a row nobody paginates; 8000 characters is an order of magnitude past the
# 800-token response the relay asks for, so a healthy report is never clipped and
# a runaway one cannot become the largest column in the database.
_MAX_RAW_RESPONSE_CHARS = 8000

# Interviews the sweeper will never see, because they never ran: a `running` row
# is only honest while a socket is actually holding it.
_TERMINAL_ABANDONED = "abandoned"
_TERMINAL_FAILED = "failed"

# Live sessions, so shutdown can ask each to close with a real code and reason
# instead of being torn down as a bare 1006. Not keyed by student, room or id —
# which is what lets this process be replicated with no shared registry.
_LIVE_SESSIONS: set[InterviewEngine] = set()


class _ConsentRequired(Exception):
    """This student holds no live grant for the current terms, so 4013.

    A type rather than a sentinel return, because the alternative is a
    `(conversation_id, interview_session_id, ok)` triple whose third element one
    caller eventually forgets to read — and the failure mode of forgetting is an
    interview that runs without consent, which is the one thing this gate exists
    to make impossible. An exception cannot be ignored by accident.

    Raised from `_open_records` BEFORE anything is written, so a refusal leaves
    no conversation, no `interview_sessions` row for the sweeper to trip over,
    and nothing for a mentor to read as an interview that happened.
    """


class _DailyCapReached(Exception):
    """This student has hit ONE OF TWO ceilings, so 4015.

    The VOLUME half of the per-user cap (the _LIMITER's 4012 is the concurrency
    half): 2 concurrent slots with no volume ceiling was ~96 billable sessions a
    day from one looping account, invisible until the invoice. A type rather
    than a sentinel return for the same reason as _ConsentRequired above — an
    exception cannot be ignored by accident — and raised from `_open_records`
    BEFORE anything is written or any upstream socket opens, so a refused
    attempt costs nothing and records nothing.

    B6.4 MADE IT TWO CEILINGS AND THE EXCEPTION CARRIES WHICH. 04 asks for the
    cap to count `status='completed'` only, which on its own deletes the abuse
    control this class was written for — a reconnect loop never reaches
    `completed`. So `daily_cap` is the student's practice allowance, counted on
    completions, and `attempt_cap` is the spend ceiling, counted on every row;
    `which` says which one refused and `message` is the sentence the student
    reads, because "you have used your 8 practice interviews" and "too many
    attempts in the last 24 hours" are different things to be told and only one
    of them is the student's fault.
    """

    def __init__(self, count: int, *, which: str = "daily", message: str = ""):
        super().__init__(count)
        self.count = count
        self.which = which
        self.message = message or (
            "You've reached today's mock interview limit. Try again tomorrow."
        )


class _UserSessionCapReached(Exception):
    """This student already holds their concurrency quota of LIVE interviews
    somewhere in the fleet, so 4012 — the cross-worker half of the check the
    per-process _LIMITER can only make for its own worker.

    With one worker the _LIMITER's answer WAS the fleet's answer. WEB_CONCURRENCY
    workers each carry their own limiter, so without this a student could hold
    interview_max_sessions_per_user live interviews PER WORKER — the review of
    the 2026-08 fix wave caught exactly that. The database is the only party
    that sees every worker, so `_open_records` counts `running` rows there,
    inside the same advisory-locked transaction as the daily cap.

    Only rows with a FRESH heartbeat count (see _LIVE_ELSEWHERE_GRACE_S): a
    worker that was kill -9'd leaves rows stamped `running` until the next boot
    sweep, and a hard count of those would lock its students out of interviews
    through no act of their own. A stale-heartbeat row is a dead session,
    whatever its status column still claims.
    """


class _FleetFull(Exception):
    """Every interview the deployment may run at once is already running, so
    1013 — `settings.interview_max_concurrent`, counted over the whole fleet.

    The per-process _LIMITER cannot answer this: it sees one task, and
    autoscaling decides how many tasks there are. The number it guards is a
    budget — Bedrock's concurrent-stream quota for the voice model, or a lower
    ceiling the college chose — and going past it does not queue the student,
    it fails them upstream AFTER their session row and attempt were written.
    Raised from `_open_records` before any write, like its two siblings, so a
    refused student loses nothing and can press Start again in a minute.
    """


# The fleet count below is count-then-insert ACROSS students, so it is
# serialized by one lock for the whole deployment rather than the per-student
# hashtext() key: two different students racing for the last slot must queue
# behind each other. Outside the int4 range on purpose, so it can never equal a
# hashtext() value; "fleetcap" in ASCII, as job_scout.RUN_LOCK_KEY is "jobscout".
_FLEET_LOCK_KEY: Final[int] = 0x666C656574636170


# A `running` row whose heartbeat is older than this is treated as dead for the
# cross-worker concurrency count above. 3x the relay's 60 s heartbeat interval:
# one missed beat is a busy loop, two is suspicious, three is a process that is
# not coming back — and deliberately far below interview_orphan_grace_seconds
# (1200), which answers the different question of when to REWRITE the row.
_LIVE_ELSEWHERE_GRACE_S = 180


class StatusOut(BaseModel):
    available: bool
    reason: str | None = None
    active_sessions: int
    max_sessions: int
    #: True for the Main Admin: the interview runs, and NOTHING is stored.
    #: See `_is_rehearsal`.
    rehearsal: bool = False


def _is_rehearsal(session: dict) -> bool:
    """The Main Admin's rehearsal — the interview with no record (2026-09-16).

    The office asked to be able to TRY the interviewer: hear the persona, walk
    the four phases, see the scorecard arrive, on the deployment the students
    use. The Main Admin is not a student, so it has no `students` row, no
    consent to record, no cap to count and no mentor to read a transcript —
    every table the interview writes is keyed on a student, and every reader of
    those tables is rule 2's. So the rehearsal is the interview with ALL of its
    writers unplugged: no conversation, no `interview_sessions` row, no turns,
    no report row, no recording, no consent row. The engine speaks, the socket
    relays, the scorecard is shown once on screen and is gone with the tab.

    What still applies: the session cookie, the Origin check, the engine's
    readiness, and BOTH halves of the concurrency limiter — a rehearsal bills
    an upstream session exactly as a real interview does, and the per-user cap
    is what keeps "test it" from becoming "open six of them".

    Only ADMIN. A MENTOR is still refused (1008): faculty read interview
    records through rule 2 and do not sit them, and a staff rehearsal path with
    no record would be a way to spend the college's Bedrock budget that nothing
    accounts for. The one office account is a bounded audience.
    """
    return session.get("role") == Role.ADMIN.value


@router.get("/status", response_model=StatusOut)
def interview_status(
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> StatusOut:
    """Why the socket would refuse, in words — the ONLY place a student learns it.

    A rejected WebSocket handshake reaches the browser as a bare 1006 with no
    code and no reason, so this probe is the client's one chance to say "not
    configured" or "not a student" instead of "check your network".

    Deliberately 200-with-a-reason for a non-student, where GET /api/voice/status
    raises 403: the interview client treats ANY non-2xx as "probe unavailable"
    and falls through to the socket (interview.service.ts), so a 403 here would
    throw away the very explanation this endpoint exists to give. Nothing about
    a student's data is disclosed either way.
    """
    rehearsal = _is_rehearsal(session)
    if session.get("role") != Role.STUDENT.value and not rehearsal:
        return StatusOut(
            available=False,
            reason="Mock interviews are a student feature.",
            active_sessions=_LIMITER.active,
            max_sessions=_LIMITER.limit,
            rehearsal=rehearsal,
        )
    # ASKED BEFORE "is the engine configured", and the order is the message.
    # A student whose office switched mock interviews off must read the sentence
    # the office wrote, not "Voice service not configured" — which is an
    # operator's problem, sends them to support, and is not even true for them.
    # The switch is per STUDENT and the rehearsal has none, so it is not asked.
    switch = (
        feature_state(db, session["studentId"], "student.assistant")
        if session.get("studentId") and not rehearsal
        else None
    )
    if switch is not None and not switch.enabled:
        return StatusOut(
            available=False,
            reason=switch.message or FEATURE_DISABLED_DEFAULT_MESSAGE,
            active_sessions=_LIMITER.active,
            max_sessions=_LIMITER.limit,
            rehearsal=rehearsal,
        )
    if not settings.interview_ready:
        # ENGINE-AWARE, and it has to be: `realtime_ready` asks the OpenAI
        # question alone, so a deployment running INTERVIEW_ENGINE=nova (signed
        # by an IAM role) or =local (nothing leaves the machine) was told its
        # interviews were unconfigured until somebody pasted an OpenAI key it
        # would never spend.
        log.warning(
            "GET /api/interview/status -> unavailable: engine %r is not configured",
            settings.interview_engine,
        )
        return StatusOut(
            available=False,
            reason=settings.interview_unready_reason,
            active_sessions=_LIMITER.active,
            max_sessions=_LIMITER.limit,
            rehearsal=rehearsal,
        )
    if _LIMITER.active >= _LIMITER.limit:
        return StatusOut(
            available=False,
            reason="Too many interviews are running right now. Try again shortly.",
            active_sessions=_LIMITER.active,
            max_sessions=_LIMITER.limit,
            rehearsal=rehearsal,
        )
    if _LIMITER.active_for(session["userId"]) >= _LIMITER.per_user_limit:
        # Asked here as well as at the socket so the student reads a sentence
        # instead of watching a Start button fail. The two caps get DIFFERENT
        # words on purpose: "the server is busy" and "your own last interview is
        # still open" lead to opposite actions, and the second one is fixed by
        # closing the other tab rather than by waiting.
        return StatusOut(
            available=False,
            reason=(
                "You already have a mock interview open in another tab. "
                "Close it, or wait a moment for it to time out."
            ),
            active_sessions=_LIMITER.active,
            max_sessions=_LIMITER.limit,
            rehearsal=rehearsal,
        )
    return StatusOut(
        available=True,
        reason=None,
        active_sessions=_LIMITER.active,
        max_sessions=_LIMITER.limit,
        rehearsal=rehearsal,
    )


def _make_turn_writer(
    conversation_id: str,
    interview_session_id: str,
    *,
    store_transcript: bool = True,
):
    """A SYNCHRONOUS writer for one interview's turns, bound to BOTH its records.

    `store_transcript=False` IS THE COLLEGE SAYING DO NOT KEEP THE WORDS (B6.1),
    and it is the FIRST enforcement of that scope — the boolean has been
    recorded on every consent row since 2026-08 and read by nothing, so the copy
    on the assistant screen promised something no code decided. It suppresses
    BOTH rows, not one: keeping `interview_turns` and dropping `messages` would
    leave the student's own words in the reviewable record while the chat
    history claimed they were gone, which is the more dishonest half of the two.
    The REPORT is still written — a scorecard is what the student and their
    mentor read, and it quotes nobody.

    Suppression is a decision taken ONCE, at open, and passed in: re-reading the
    policy per turn would let an edit that lands mid-interview keep half of one
    transcript. `interview_sessions.transcript_suppressed` records that the same
    decision was taken, because `turns_emitted` > `turns_persisted` is
    AGENTS.md's runbook signal for dropped writes and this would otherwise fire
    it on every interview at such a college.

    The keyword defaults to True so that every existing caller — the platform
    media bridge and the engine tests — keeps exactly the behaviour it had.

    Synchronous on purpose: app/conversations.py is synchronous SQLAlchemy, and
    the relay runs this on a worker thread (asyncio.to_thread) so a round trip to
    Postgres never stalls the event loop carrying every other student's audio.

    It takes its OWN short-lived connection and Session per turn rather than
    holding one for the call. An interview runs up to 15 minutes; a Session held
    that long pins a pooled connection and keeps an idle transaction open, which
    blocks autovacuum and — at interview_max_sessions — would starve every HTTP
    request on this worker. This is also why the WebSocket route has no
    Depends(get_db).

    It deliberately does NOT catch the general case. The engine calls this
    fire-and-forget and logs a failure with its cause against the connection id
    (each engine's own `_run_turn_write`), so catching here would only lose the
    one identifier that makes the line diagnosable. The two exceptions below are
    handled because neither is that: one is not a failure at all, and the other
    is a failure only the engine can act on.

    TWO INSERTS, ONE TRANSACTION (§6.5), AND THAT COSTS ONE UNUSUAL LINE. The
    Session is bound to a Connection that already has a transaction open, with
    `join_transaction_mode="create_savepoint"`, because `append_message` ends
    with its own `db.commit()` — and inside a joined transaction that commit
    releases a SAVEPOINT instead of ending the real one. So both rows land in a
    single COMMIT to Postgres, and `interview_turns.message_id` can be filled in
    from a Message that has an id only after that flush.

    Two traps in that arrangement, both silent if got wrong:

      * `db.close()` ROLLS BACK the open savepoint. The explicit `db.commit()`
        before the `finally` is what releases it; without that line the FK
        back-link is written and then quietly discarded, and the only symptom is
        a `message_id` column that is always NULL.
      * an IntegrityError on EITHER table rolls back BOTH. That is deliberate,
        not a cost we failed to avoid: `interview_turns` carries the same
        `(session, provider_turn_id)` unique shape as `messages`, so a retry of
        one turn is a no-op either way — the turn is already stored by the
        winner. The alternative, two independent writers, can leave the two
        records disagreeing about which turns happened, and a record that can
        silently disagree with `messages` has no value at all.
    """

    def write(
        sender: str, text: str, provider_turn_id: str, record: _TurnRecord
    ) -> None:
        if not store_transcript:
            # Nothing is written and nothing is raised: the engine's contract is
            # fire-and-forget, and a refusal here would look to it exactly like
            # a failed write. The turn still HAPPENED — `turns_emitted` counts
            # it, and `transcript_suppressed` on the session is what tells the
            # runbook why `turns_persisted` stayed at zero.
            return
        try:
            with engine.connect() as conn, conn.begin():
                db = SessionLocal(
                    bind=conn, join_transaction_mode="create_savepoint"
                )
                try:
                    turn = InterviewTurn(
                        interview_session_id=interview_session_id,
                        seq=record.seq,
                        # The DB vocabulary is student/interviewer where the
                        # messages vocabulary is user/assistant. Translated HERE,
                        # at the boundary that owns the schema, so the relay
                        # never has to know a second set of words for the two
                        # parties in the room.
                        speaker="student" if sender == "user" else "interviewer",
                        phase=record.phase,
                        content=text,
                        transcription_status=record.transcription_status,
                        answer_quality=record.answer_quality,
                        counted_as_answer=record.counted_as_answer,
                        is_partial=record.is_partial,
                        provider_turn_id=provider_turn_id,
                    )
                    db.add(turn)
                    if text.strip():
                        # THE BLANK-TEXT CARVE-OUT (L4), and it is this `if`.
                        # A transcription that timed out or failed arrives as ""
                        # — and those are exactly the turns
                        # `transcription_status` exists to record, so the
                        # interview_turns row above is written REGARDLESS. What
                        # is skipped is only the `messages` row: an empty chat
                        # bubble in GET /api/agent/history says "the student said
                        # nothing", which is the opposite of what happened.
                        message = convo.append_message(
                            db,
                            conversation_id,
                            sender,
                            text,
                            channel=_CHANNEL,
                            is_final=True,
                            provider_turn_id=provider_turn_id,
                        )
                        turn.message_id = message.id
                    db.commit()
                finally:
                    db.close()
        except convo.ConversationGone as exc:
            # L5, and the contract append_message's docstring sets out. The
            # student cleared this chat thread from another tab (or retention
            # purged it) mid-call, so every remaining turn of a 15-minute session
            # would be refused identically. Translated into the relay's own
            # vocabulary rather than raised across the module boundary, because
            # the engines import no ORM model and no database code at
            # all and that containment is the point — see _TurnWriteRefused. It
            # is NOT swallowed and NOT folded into the IntegrityError branch
            # below, which is precisely what that contract forbids: the relay
            # logs it against the connection id and ends the session.
            raise _TurnWriteRefused(str(exc)) from exc
        except IntegrityError:
            # append_message's read-then-insert dedup is a CHECK, not a
            # guarantee: two writes for one turn can both pass it before either
            # commits, and the unique index on
            # (conversation_id, provider_turn_id) then fires. The turn IS
            # stored — by the other writer — so this is the idempotent no-op it
            # looks like, not a failure. Same handling as /api/voice/transcript,
            # and `uq_interview_turn_provider` gives the second table the same
            # property for the same reason. Nothing to roll back by hand: the
            # transaction above already unwound on its way out.
            #
            # ONE OTHER THING CAN LAND HERE, and it is worth knowing about: a
            # foreign-key violation, if the Student row was deleted mid-call and
            # took interview_sessions with it by CASCADE. Swallowed too, and
            # acceptably so — that student's whole record is gone by design, so
            # there is nothing left for this turn to belong to. Any NEW cause of
            # IntegrityError on this path would be silent, so add its own branch
            # rather than widening this one.
            pass

    return write


def _make_report_writer(interview_session_id: str):
    """The one evaluation row for this interview. AWAITED by the relay, not fired.

    Called exactly once, after the interview is over, which is why the relay
    waits for it (L3): left on the fire-and-forget path it would be the last
    write scheduled and therefore the first one the teardown drain cancels — the
    scorecard would be the piece most likely to be missing.

    A row is written for EVERY outcome, including the failures. `report_status`
    of 'unparseable', 'timeout' or 'rejected' is a record that a report was
    attempted and did not arrive; a missing row says nothing at all, and a mentor
    cannot tell it from an interview that predates the feature.
    """

    def write(record: _ReportRecord) -> None:
        db = SessionLocal()
        try:
            # Present only on success — and on success every key exists, because
            # the relay's parser coerces the whole shape before it gets here.
            report = record.report or {}
            raw = record.raw_response[:_MAX_RAW_RESPONSE_CHARS]
            db.add(
                InterviewEvaluation(
                    interview_session_id=interview_session_id,
                    report_status=record.status,
                    # NULLABLE EVEN WHEN report_status='ok', and never
                    # substituted with 0: a missing score and a zero mean
                    # opposite things to the person reading them. The parser
                    # returns None for a score the model did not give, and that
                    # None travels all the way to the screen.
                    overall_score=report.get("overall"),
                    communication_score=report.get("communication"),
                    domain_score=report.get("domain"),
                    structure_score=report.get("structure"),
                    # None rather than [] when there is no report at all: an
                    # empty list would claim the model listed no strengths.
                    strengths=report.get("strengths") if record.report else None,
                    improvements=(
                        report.get("improvements") if record.report else None
                    ),
                    drill=report.get("drill"),
                    summary=report.get("summary"),
                    raw_response=raw or None,
                    model=record.model or None,
                )
            )
            db.commit()
        except IntegrityError:
            # DF6. The report deadline and a late response.done can both settle
            # once each in a pathological ordering, and the UNIQUE constraint on
            # interview_session_id makes the second one a no-op. FIRST WRITE
            # WINS, deliberately: the earlier row is the one whose status matches
            # what the student was actually told.
            db.rollback()
        finally:
            db.close()

    return write


def _record_audio_in_the_manifest(
    db: Session, interview_session_id: str, stem: str | None
) -> None:
    """One `archived_documents` row per WAV this interview left on disk.

    WHY THE ROW HAS TO EXIST AT ALL. `python -m app.archive_documents` copies
    every recording into `reep-documents-archive-<account>` -- versioned,
    Object-Locked, no lifecycle rule -- under the file's own name and nothing
    else. The only thing that has ever known whose voice that is is the
    `interview_sessions` row pointing at it, and that row is deleted after
    INTERVIEW_RETENTION_DAYS and emptied outright by `python -m
    app.purge_people`. So without this, a pass whose whole purpose is removing
    every trace of a person leaves their recorded voice in a bucket that cannot
    delete it, indexed by nothing -- which is precisely the failure
    `archived_documents` was built to prevent, arriving through the one store
    that does not go through `document_manifest.save_and_record`.
    `DocumentOwnerKind.INTERVIEW_AUDIO` has been in the manifest since its first
    migration with no writer behind it; this is the writer.

    WHY IT IS WRITTEN HERE. Three places could have: the recorder, the sweep,
    and this finalizer. The recorder runs on the audio hot path, holds no
    Session and does not know which student it is recording. The sweep "needs no
    database and deliberately does not open one" -- read its docstring, the
    argument is that the filesystem is the authority and a sweep driven by rows
    silently skips every file whose row never landed -- and making it open one
    would trade that property for this bookkeeping. This function is the only
    point that holds all three facts at once: a Session, a set of files the
    relay has already closed and flushed, and the record saying whose interview
    it was.

    THE ROW ITSELF IS WRITTEN BY `document_manifest.record_existing`, NOT HERE.
    None of that module's original three entry points could express "record a
    file that already exists" -- `save_and_record` WRITES BYTES through
    `document_store.save_bytes`, which accepts PDF/PNG/JPEG by magic number and
    holds the whole file in memory under a 10 MB ceiling, and a WAV of tens of
    megabytes written incrementally over eight minutes in a different store is
    none of that; `release` stamps `released_at`, which would tell every future
    reader the recording was gone on the day it was made; `reattribute` only
    edits a row that is already there. So a fourth verb was added beside them
    rather than this file constructing the model itself. §36 of
    tests/test_codebase_guards.py pins ONE writer of `archived_documents` by
    scanning every module for the constructor outside the manifest, and it was
    right to: the manifest is the only thing that can ever say whose file an
    object in a bucket with no delete permission was, and a second writer is
    how one of them quietly stops setting `owner_id`. This function's job is to
    work out the FACTS -- which files exist, whose interview it was, how big
    each one is -- and hand them over.

    IT CANNOT FAIL THE INTERVIEW CLOSE. Everything is inside one `except`, the
    way every other write on this path is: the student's close frame has
    already gone out, the terminal status has already committed, and an index
    that could take the record down with it would be worth less than no index.
    """
    try:
        # The stem is the files' name, and `delete_session_audio` takes the
        # same two candidates in the same order for the same reason: the store
        # names every file after the `interview_sessions.id` precisely so a row
        # that never learned its own path can still find them. `available_tracks`
        # then asks the DISK which of the three exist, so a mixdown that failed
        # to land is simply not indexed rather than indexed as missing.
        stem = stem or interview_session_id
        tracks = available_tracks(stem)
        if not tracks:
            # `audio_recorded` is true and there is no file: the interview
            # believed it kept something that is not there. Nothing to name, and
            # worth a line, because the two ought never to disagree.
            log.error(
                "Interview %s reports audio but the store holds no file under "
                "%r; nothing was added to the archive manifest",
                interview_session_id,
                stem,
            )
            return

        # THE OWNER IS `interview_sessions.student_id`, WHICH IS A `students.id`.
        # The manifest's `owner_id` is a plain String holding "a users.id or a
        # students.id" and deliberately not a foreign key (the model says why:
        # an FK would carry the cascade of the column it shadows and be
        # destroyed at the exact moment it becomes the only record left), so
        # which id goes in it is a choice that has to be argued. It is the
        # student's: it is the id the interview record itself carries, the id
        # rule 2 filters every staff read on, and the id `app.purge_students`
        # works from -- so "everything this owner ever had", the one question
        # this table is asked, gives the same answer here as it does for the
        # student's uploads next door. A `users.id` would be a second
        # vocabulary for one person inside one column.
        #
        # READ OFF THE ROW rather than carried down from the socket, so this
        # names the owner the RECORD names. A closure holding a student id from
        # the handshake would keep being right only while nothing between the
        # two ever disagreed.
        owner_id = db.scalar(
            select(InterviewSession.student_id).where(
                InterviewSession.id == interview_session_id
            )
        )
        if not owner_id:
            # The interview record is gone (retention, or a purge, between the
            # UPDATE above and here). A manifest row with no owner names a file
            # and nobody, which is the state this table exists to avoid.
            log.error(
                "Interview %s has no record to take an owner from; its audio "
                "was NOT added to the archive manifest",
                interview_session_id,
            )
            return

        # THE MANIFEST'S KEY IS THE FILE'S OWN NAME, because the archive's key
        # is: `archive_documents._key_parts` uploads each file under
        # `path.name`. So it is taken from the store's own `track_path` rather
        # than from a format string restated here -- the two spellings have to
        # produce the same string or this row indexes an object that does not
        # exist, and one of them is in a bucket where a key, once written,
        # cannot be moved.
        paths = {track: track_path(stem, track) for track in tracks}

        # ALREADY-NAMED FILES ARE SKIPPED, NEVER RE-INSERTED -- and that check
        # lives inside `record_existing` rather than here, because idempotence
        # belongs with the writer. `stored_name` is UNIQUE, deliberately, so two
        # rows can never claim one file; three layers finalize one interview,
        # only Layer 1 reaches this code today, and "only one layer writes this"
        # is exactly the sentence that stops being true quietly.
        written = 0
        for track, path in paths.items():
            try:
                # THE REAL SIZE, OFF THE FILE. `outcome.audio_bytes` is the
                # whole interview's disk footprint -- both source tracks, the
                # mixdown, and 44 bytes of RIFF header each -- so apportioning
                # it per file would be an invention. The relay closes and
                # flushes the recorder BEFORE it composes the outcome, which is
                # what makes `st_size` here the finished length rather than a
                # snapshot.
                #
                # The one path where it is not: a close that timed out, on
                # which the engine falls back to `snapshot()` and the writer
                # thread it could not wait for may still be finishing the
                # mixdown. The row is written anyway -- every other field on it
                # is exact, and a size short by a tail is a far smaller loss
                # than an object in a bucket that nothing can name, which is
                # the trade `document_manifest.release` makes in the same
                # words.
                size_bytes = path.stat().st_size
            except OSError:
                # Gone between the listing and here -- retention sweeping this
                # session, or a volume that has stopped answering. Counted and
                # carried past rather than fatal, `archive_documents.run`'s
                # rule: one unreadable file must not cost the other two tracks
                # the rows that name them.
                log.exception(
                    "Could not size %s for the archive manifest; the other "
                    "track(s) of interview %s are still being named",
                    path.name,
                    interview_session_id,
                )
                continue
            if record_existing(
                db,
                path.name,
                kind=DocumentOwnerKind.INTERVIEW_AUDIO,
                owner_id=owner_id,
                # What a human sees when this comes back out of the bucket.
                # `download_name` is the store's own answer to that question
                # and it "contains no student name" on purpose -- the
                # association lives in this row, where access control can
                # reach it, rather than in a filename that syncs, backs up
                # and gets searched.
                original_name=download_name(stem, track),
                # No title: the store has no heading for a recording, and
                # the manifest's rule is NULL where there is none rather
                # than a guessed value. The track is already in the name.
                title=None,
                mime_type="audio/wav",
                size_bytes=size_bytes,
            ):
                written += 1

        if not written:
            return
        # COMMITTED HERE, where `document_manifest` would flush. That rule is
        # about a router, which has a request transaction to land in; this
        # function owns its Session and nothing after it will commit. It is its
        # OWN transaction and it is the last thing this finalizer does, so a
        # failure of the index can never roll back the terminal status the
        # whole mechanism exists to write.
        db.commit()
        log.info(
            "Named %d interview-audio file(s) for session %s in the archive "
            "manifest",
            written,
            interview_session_id,
        )
    except Exception:
        # Never fatal, and never allowed to leave a half-built transaction
        # behind for the `db.close()` in the caller's `finally`.
        try:
            db.rollback()
        except Exception:  # pragma: no cover - a session that is already gone
            pass
        log.exception(
            "Could not name interview %s's audio in the archive manifest. The "
            "files and the bucket copy are unaffected; what is missing is the "
            "row that says whose they are.",
            interview_session_id,
        )


def _make_finalizer(interview_session_id: str, *, audio_skip_reason: str | None = None):
    """LAYER 1's database half: close the record, and record that no report came.

    `audio_skip_reason` is `recorder_or_reason`'s word for the gate that
    refused a recorder before the interview began, or None when one was built.
    It lands on the row ONLY when nothing was recorded: a recorder that was
    built and still closed with nothing is `nothing_captured`, and a recorded
    interview has nothing to explain.

    The UPDATE carries `AND status = 'running'`, which is what makes all three
    finalization layers idempotent against each other with no coordination: the
    loser updates zero rows and says nothing.

    Two commits, in this order, and the order is the decision. The terminal
    status MUST land — a row stuck at `running` is a record that lies, and it is
    the failure this whole mechanism exists to prevent. The 'unavailable'
    evaluation row is a courtesy to whoever reads the screen afterwards. Sharing
    one transaction would let the courtesy's failure roll back the necessity.

    The summary and the archive manifest commit after those two, each in its own
    transaction and each for the same reason: an index is worth less than the
    record it points into, so neither may be in a position to undo it.
    """

    def finalize(outcome: _SessionOutcome) -> None:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            result = db.execute(
                update(InterviewSession)
                .where(
                    InterviewSession.id == interview_session_id,
                    InterviewSession.status == "running",
                )
                .values(
                    status=outcome.status,
                    terminal_reason=outcome.terminal_reason,
                    final_phase=outcome.final_phase,
                    answers_accepted=outcome.answers_accepted,
                    turns_emitted=outcome.turns_emitted,
                    turns_persisted=outcome.turns_persisted,
                    close_code=outcome.close_code,
                    upstream_session_id=outcome.upstream_session_id,
                    # WHAT WAS KEPT OF THE STUDENT'S VOICE — written here and
                    # NOWHERE else. These five columns are the only record that
                    # a file exists, and `retention.purge_expired` destroys
                    # recordings by reading them, so a recording whose row never
                    # received them is a recording nothing will ever delete: a
                    # named student's voice on disk, past its retention window,
                    # invisible to every query. The relay has already closed and
                    # flushed the files by the time this runs (_close_recorder is
                    # awaited before the outcome is composed), which is what
                    # makes audio_bytes a final number rather than a snapshot.
                    #
                    # In every deployment today all five are the defaults —
                    # recording needs INTERVIEW_RECORDING_ENABLED *and* a live
                    # scope_store_audio grant (app/interview_audio.py) — and the
                    # defaults say "we know nothing was kept" rather than
                    # leaving the row ambiguous. Branch on `audio_recorded`,
                    # never on `audio_path IS NOT NULL`.
                    audio_recorded=outcome.audio_recorded,
                    audio_path=outcome.audio_path,
                    audio_bytes=outcome.audio_bytes,
                    audio_duration_ms=outcome.audio_duration_ms,
                    audio_truncated=outcome.audio_truncated,
                    audio_skipped_reason=(
                        None
                        if outcome.audio_recorded
                        else (audio_skip_reason or SKIP_NOTHING_CAPTURED)
                    ),
                    heartbeat_at=now,
                    ended_at=now,
                )
            )
            db.commit()
            if not result.rowcount:
                # Somebody finalized first — normally impossible, since Layer 2
                # only runs after this returns. Worth a line rather than silence:
                # if it starts happening, two layers are racing and the row's
                # terminal status is whichever of them is less informed.
                log.info(
                    "Interview session %s was already finalized; "
                    "left as it stands",
                    interview_session_id,
                )

            if outcome.report_status is None:
                # WRAP_UP was never reached, so no scorecard was ever requested.
                # The row says `unavailable` so a mentor reads "no report — this
                # interview hit the cap" instead of a blank panel they cannot
                # distinguish from a bug.
                db.add(
                    InterviewEvaluation(
                        interview_session_id=interview_session_id,
                        report_status="unavailable",
                    )
                )
                try:
                    db.commit()
                except IntegrityError:
                    # An evaluation already exists, so the scorecard did settle
                    # and this session simply did not know. The real row wins.
                    db.rollback()

            # B6.2, AND IT IS THIRD FOR A REASON. The summary copies the
            # terminal STATUS (written by the UPDATE above) and the four SCORES
            # (written by `_make_report_writer`, which the relay awaits before
            # this runs, or by the courtesy row just above). Taken any earlier
            # it would record a status that is about to change, or NULL scores
            # for an interview that was in fact marked — and a NULL here is not
            # a gap to be filled in later, it is a permanent claim that the
            # model never scored this interview.
            #
            # It cannot raise: `ensure_summary` swallows and logs. Losing this
            # copy costs a trend point that the backfill can recover for 180
            # days; losing the UPDATE above costs a record that says `running`
            # forever, which is what all three finalization layers exist to
            # prevent.
            ensure_summary(db, interview_session_id)

            # THE ARCHIVE'S INDEX, LAST, AND ONLY WHEN SOMETHING WAS KEPT.
            # Branching on `audio_recorded` and never on `audio_path IS NOT
            # NULL` is the rule the UPDATE above is written to and it is the
            # rule here: a NULL path collapses four different facts into one,
            # and the fact this needs is the one `audio_recorded` states --
            # bytes reached the disk. LAST because it is the only write in this
            # function nobody reads today: the terminal status, the courtesy
            # evaluation and the summary all answer somebody looking at this
            # interview this week, and this one answers whoever is holding a key
            # out of an Object-Locked bucket in five years, after the row above
            # has been deleted by retention or by a purge. It cannot raise.
            if outcome.audio_recorded:
                _record_audio_in_the_manifest(
                    db, interview_session_id, outcome.audio_path
                )
        finally:
            db.close()

    return finalize


def _successor_covers(
    db: Session, consent_id: str, scopes: tuple[bool, bool, bool]
) -> bool:
    """Does this user still hold a live grant covering everything the revoked
    one covered?

    Only ever called when the pinned grant has gone away, so the cost is paid
    once per interview at most. The comparison is per-scope and one-directional:
    a successor may grant MORE (that is a widening, and no reason to end a call)
    and may not grant LESS. Any version, deliberately — the acknowledgement the
    student just posted carries the CURRENT version string, which is not the one
    a long-running interview may have opened under, and refusing a newer version
    here would end a call for having agreed to newer terms.

    THE FAIL-OPEN PROPERTY LIVES IN THE CALLER, NOT HERE. This function never
    swallows an error: anything that raises propagates through the heartbeat to
    the relay's "heartbeat not written" warning and the interview continues,
    which is the asymmetry `_open_records` is deliberately on the other side of.
    What it must never do is INVENT a successor, so a query that comes back
    empty means there is none — and that ends the call, because by then the
    query did come back and it said the scope is gone.
    """
    owner = db.scalar(
        select(InterviewConsent.user_id).where(InterviewConsent.id == consent_id)
    )
    if owner is None:
        # The row was deleted outright rather than revoked. Nothing can cover
        # what nobody can read, and the interview must not continue on it.
        return False
    live_ai, transcript, audio = scopes
    query = select(InterviewConsent.id).where(
        InterviewConsent.user_id == owner,
        InterviewConsent.revoked_at.is_(None),
    )
    if live_ai:
        query = query.where(InterviewConsent.scope_live_ai.is_(True))
    if transcript:
        query = query.where(InterviewConsent.scope_store_transcript.is_(True))
    if audio:
        query = query.where(InterviewConsent.scope_store_audio.is_(True))
    return db.scalar(query.limit(1)) is not None


def _make_heartbeat(
    interview_session_id: str,
    *,
    consent_id: str | None = None,
    on_consent_revoked: Callable[[], None] | None = None,
    consent_scopes: tuple[bool, bool, bool] | None = None,
):
    """Stamp heartbeat_at — and notice a consent grant that has gone away (4014).

    Scoped to `status = 'running'` so a heartbeat still in flight when the
    session finalizes cannot touch a row that is already closed — the sweeper
    keys on status anyway, but a closed row with a moving timestamp is the kind
    of thing that costs somebody an afternoon.

    THE SECOND STATEMENT IS 4014, AND IT IS A POLL, NOT THE PUSH §7.1 SKETCHES.
    Having `DELETE /api/interview/consent` call a `stop_sessions_for_user()`
    would be immediate, and it would also be wrong most of the time: any
    registry it could consult — `_LIVE_SESSIONS` here included — is per-PROCESS,
    so with N uvicorn workers the revoking request lands on a worker that is not
    holding the socket in (N-1)/N of cases, and the push then does nothing at
    all while looking like it worked. Re-reading the grant on a heartbeat the
    session is already issuing catches a revocation from any worker, any tab and
    any future code path that stamps `revoked_at`, for one indexed SELECT a
    minute per live interview. The cost is up to _HEARTBEAT_WRITE_INTERVAL_S of
    delay, and that is a backstop's delay rather than a student watching a
    button: the assistant screen refuses to offer "Withdraw" while an interview
    is running (assistant.component.ts).

    IT WATCHES THE EXACT GRANT THE INTERVIEW OPENED UNDER, by id, never "is
    there a live grant for the current version". An operator bumping
    INTERVIEW_CONSENT_VERSION mid-session would otherwise end every interview in
    progress with "you withdrew consent", which is a sentence the student did
    not earn and an event nobody could explain afterwards.

    IT FAILS OPEN, and that asymmetry against the gate in `_open_records` is
    deliberate. Opening an interview fails CLOSED because "we could not check
    whether they agreed" must never start one. Ending an interview must not:
    a transient database error here would kill a live call that a real grant
    authorised, so the stop runs only on a query that came back and said the row
    is gone. Anything that raises propagates to the relay's own "heartbeat not
    written" warning, and the interview continues.

    Both new arguments are keyword-only and default to None, which means "stamp
    the heartbeat and watch nothing". That is the shape the relay's own tests
    want, and it is honest for them: they have no consent row to watch. It is
    never the shape a real interview gets — `_open_records` refuses without a
    grant, so a live session always has an id to pass here.

    B6.1 ADDED A THIRD ARGUMENT AND IT IS THE DIFFERENCE BETWEEN "SUPERSEDED"
    AND "WITHDRAWN". The student now posts an ACKNOWLEDGEMENT of the college's
    policy at every Start, and `POST /api/interview/consent` supersedes the live
    grant when its scopes differ — which stamps `revoked_at` on exactly the row
    a running interview is pinned to. Watching only "is the pinned row revoked"
    would then let a second tab's Start end the interview in the first with
    "Consent withdrawn", a sentence the student did not earn.

    So when the pinned grant is gone, this asks a second question: does this
    user still hold a LIVE grant covering every scope this interview is running
    under? If they do, the acknowledgement was replaced by an equal-or-wider one
    and the call continues. If they do not, a scope this interview depends on is
    gone and the session ends 4014 — which is the compatibility board's rule
    ("a policy change stops a running session with 4014 only when it removes a
    scope") expressed as a property of the grant rather than as a second read of
    the policy table on every heartbeat of every live interview.

    Passing no `consent_scopes` keeps the OLD behaviour exactly — any revocation
    of the pinned row stops the session — which is what the platform media
    bridge and the existing tests ask for.

    THE SECOND QUERY RUNS ONLY WHEN THE FIRST SAID THE ROW IS GONE, so the steady
    state is still one indexed SELECT a minute, and the fail-OPEN property is
    unchanged: anything that raises propagates to the relay's "heartbeat not
    written" warning and the interview continues.
    """

    def beat() -> None:
        db = SessionLocal()
        try:
            db.execute(
                update(InterviewSession)
                .where(
                    InterviewSession.id == interview_session_id,
                    InterviewSession.status == "running",
                )
                .values(heartbeat_at=datetime.now(timezone.utc))
            )
            db.commit()
            if consent_id is None or on_consent_revoked is None:
                return
            still_live = db.scalar(
                select(InterviewConsent.id)
                .where(
                    InterviewConsent.id == consent_id,
                    InterviewConsent.revoked_at.is_(None),
                )
                .limit(1)
            )
            if still_live is not None:
                return
            if consent_scopes is None:
                on_consent_revoked()
                return
            if _successor_covers(db, consent_id, consent_scopes):
                # Superseded by an acknowledgement that grants at least as much.
                # INFO because it is the ordinary shape of a student pressing
                # Start in another tab, and because "the pinned grant is gone
                # and the interview continued" is otherwise a mystery.
                log.info(
                    "Interview %s: its consent grant was superseded by a live "
                    "grant covering the same scopes; the session continues.",
                    interview_session_id,
                )
                return
            on_consent_revoked()
        finally:
            db.close()

    return beat


def _assistant_switch(student_id: str) -> FeatureState:
    """`student.assistant` for one student, read on a worker thread.

    Its OWN short-lived Session, for the reason every other database read on
    this route takes one: the WebSocket handler has no `Depends(get_db)`, because
    a Session held for the length of an interview pins a pooled connection and
    keeps an idle transaction open for up to fifteen minutes.

    Failure is NOT caught here. An unreadable feature switch is a database that
    is not answering, and the very next thing this route does is write an
    `interview_sessions` row: failing open would start an interview that cannot
    be recorded, and failing closed with a made-up sentence would tell a student
    the office switched them off when nobody did. The generic handler already
    turns this into 1011 "Internal error", which is the truth.
    """
    with SessionLocal() as db:
        return feature_state(db, student_id, "student.assistant")


@router.websocket("")
async def interview(websocket: WebSocket) -> None:
    """One interview, relayed.

    ACCEPT FIRST, then check. A close sent BEFORE accept fails the HTTP upgrade,
    and the browser WebSocket API surfaces neither code nor reason for that — the
    student would see an opaque 1006 and "not signed in" would be
    indistinguishable from "the wifi dropped". Every refusal below is therefore a
    close on an accepted socket, which is the only way the cause reaches them.
    """
    conn_id = uuid.uuid4().hex[:12]
    await websocket.accept()

    # Defence in depth, not the gate. The cookie is SameSite=Lax, so a cross-site
    # page cannot carry reep_session onto this handshake in the first place; this
    # refuses the mismatched browser before it costs an upstream connection. Only
    # a PRESENT-and-wrong Origin is refused: a non-browser client omitting the
    # header is stopped by the session check below, and refusing on absence would
    # break nothing an attacker relies on while risking a same-origin deployment.
    origin = websocket.headers.get("origin")
    if origin is not None and origin != settings.web_origin:
        log.warning(
            "[conn=%s] WS /api/interview -> %d: origin %r is not %s",
            conn_id,
            _CLOSE_FORBIDDEN_ORIGIN,
            origin,
            settings.web_origin,
        )
        await _close_downstream(
            websocket, _CLOSE_FORBIDDEN_ORIGIN, "Origin not allowed"
        )
        return

    try:
        # to_thread because verify_session_token's revocation check runs a
        # SELECT on a cache miss (security.py says so itself), and this
        # coroutine is on the loop shared with every live interview's audio —
        # the audit found a pool-starved handshake could park the loop for the
        # whole pool timeout, silencing every interview at once.
        session = await asyncio.to_thread(get_ws_session, websocket)
    except WebSocketException as exc:
        log.warning(
            "[conn=%s] WS /api/interview -> %d: no valid reep_session cookie",
            conn_id,
            exc.code,
        )
        await _close_downstream(websocket, exc.code, exc.reason or "Sign in required.")
        return

    # Role scoping is the ROUTER's job in this repo (require_mentor +
    # _assert_can_access_student, and voice.py's own STUDENT check), so
    # get_ws_session authenticates and this authorises. Hiding the Start button
    # in the Angular component is not a gate: a MENTOR or the Main Admin holding a
    # valid cookie can open this socket from devtools in one line, and each open
    # costs a billed upstream Realtime session.
    rehearsal = _is_rehearsal(session)
    if session.get("role") != Role.STUDENT.value and not rehearsal:
        log.warning(
            "[conn=%s] WS /api/interview -> %d: role %s is not STUDENT",
            conn_id,
            _CLOSE_NOT_A_STUDENT,
            session.get("role"),
        )
        await _close_downstream(
            websocket,
            _CLOSE_NOT_A_STUDENT,
            "Mock interviews are a student feature.",
        )
        return

    # A STUDENT with no `studentId` claim — a User row with no Student row, which
    # the roster seed does not produce but a hand-made account can — cannot have
    # an interview record, because interview_sessions.student_id is NOT NULL and
    # rule 2's gate is written against that column. Refused HERE, sharing 1008
    # with the role check, so the student reads "you are not allowed here"
    # instead of meeting the NOT NULL violation as an opaque 1011 thirty seconds
    # later with an upstream session already billed.
    student_id = session.get("studentId")
    if rehearsal:
        # The Main Admin has no Student row and needs none: nothing below
        # writes. `student_id` stays None, and every branch that would have
        # written keys off `rehearsal` rather than off its absence.
        log.info(
            "[conn=%s] WS /api/interview: REHEARSAL by the Main Admin (%s) — "
            "nothing from this interview will be stored",
            conn_id,
            session.get("userId"),
        )
    elif not student_id:
        log.error(
            "[conn=%s] WS /api/interview -> %d: STUDENT session has no studentId",
            conn_id,
            _CLOSE_NOT_A_STUDENT,
        )
        await _close_downstream(
            websocket,
            _CLOSE_NOT_A_STUDENT,
            "Your student profile is incomplete; ask the placement cell.",
        )
        return

    # The `student.assistant` switch, BEFORE the engine check and before the
    # limiter — same order as the status probe, and for the same reason: a
    # student the office switched off must read the office's sentence rather
    # than an operator's, and a refusal that happens before `try_acquire` can
    # never leak the slot it did not take. to_thread because this is a SELECT
    # and this coroutine shares its loop with every live interview's audio.
    switch = (
        await asyncio.to_thread(_assistant_switch, student_id)
        if not rehearsal
        else None
    )
    if switch is not None and not switch.enabled:
        log.info(
            "[conn=%s] WS /api/interview -> %d: student.assistant is switched off for %s",
            conn_id,
            _CLOSE_FEATURE_DISABLED,
            student_id,
        )
        await _close_downstream(
            websocket,
            _CLOSE_FEATURE_DISABLED,
            switch.message or FEATURE_DISABLED_DEFAULT_MESSAGE,
        )
        return

    if not settings.interview_ready:
        # Asked of the ENGINE this deployment actually runs — see the same
        # check in the status probe above. The close reason stays generic
        # because it is read by a student; the log line names the engine,
        # because it is read by the operator who can fix it.
        log.error(
            "[conn=%s] WS /api/interview -> %d: engine %r is not configured",
            conn_id,
            _CLOSE_NOT_CONFIGURED,
            settings.interview_engine,
        )
        await _close_downstream(
            websocket, _CLOSE_NOT_CONFIGURED, "Voice service not configured"
        )
        return

    # ONE acquire, TWO caps, and which one refused decides the close code. A
    # two-step acquire is a slot leak waiting to happen: the first step succeeds,
    # the second refuses, and the slot the first one took is released only on the
    # paths somebody remembered.
    user_id = session["userId"]
    refused_by = _LIMITER.try_acquire(user_id)
    if refused_by == _REFUSED_BY_USER:
        # Audit H1. The worker cap never asked WHOSE sessions it was counting, so
        # one student could open them all — each socket authenticating, opening
        # an upstream Realtime session and billing from the handshake's
        # response.create with no microphone input at all, while every other
        # student was answered 1013 and the graph read as a capacity incident.
        # 4012 rather than 1013 so the student is told the true cause: their own
        # other interview is still open, and closing that tab fixes it.
        log.warning(
            "[conn=%s] WS /api/interview -> %d: user already holds %d/%d interviews",
            conn_id,
            _CLOSE_USER_SESSION_CAP,
            _LIMITER.active_for(user_id),
            _LIMITER.per_user_limit,
        )
        await _close_downstream(
            websocket,
            _CLOSE_USER_SESSION_CAP,
            "You already have a mock interview open. Close it and try again.",
        )
        return
    if refused_by:
        log.warning(
            "[conn=%s] WS /api/interview -> %d: %d/%d interviews on this worker",
            conn_id,
            _CLOSE_OVERLOADED,
            _LIMITER.active,
            _LIMITER.limit,
        )
        await _close_downstream(
            websocket, _CLOSE_OVERLOADED, "Too many interviews in progress"
        )
        return

    # The Specialization Matrix row, chosen by the student in the UI and carried
    # as a query param because a browser WebSocket cannot set headers. ABSENT is
    # the generic interview that predates the matrix; PRESENT-but-unknown is a
    # client bug or a hand-rolled socket, and is refused outright rather than
    # silently downgraded -- a student who asked for an HR interview and got a
    # generic one was assessed against the wrong bar with no sign of it.
    # Checked AFTER the limiter so a bad param never holds a slot.
    spec_key = websocket.query_params.get("specialization")
    # ONE CALL, ON A WORKER THREAD, and the shape of this line is the point.
    # It used to be a synchronous in-memory dict lookup followed by
    # `asyncio.to_thread(with_question_bank, ...)` for the question bank,
    # because the first cost nothing and the second was a SELECT. B5.1 makes the
    # track itself a row, so the lookup is a SELECT too — and leaving it here on
    # the loop would put a database round trip back on the coroutine every live
    # interview's audio shares, which shows up as OTHER people's interviews
    # stuttering rather than as anything wrong with this one. So both reads move
    # across together: `resolve_specialization` opens one session, reads the
    # track and its bank, and hands back the same frozen Specialization the
    # engine has always taken (app/interview_tracks.py). Resolved LIVE, never
    # cached, so a question — or a persona — an admin changes this morning is
    # used this afternoon. The engine, the caps, the recorder and the writers
    # never learn whether the row came from the table or from the constant.
    specialization = await asyncio.to_thread(
        resolve_specialization, spec_key, student_id=student_id
    )
    if spec_key and specialization is None:
        log.warning(
            "[conn=%s] WS /api/interview -> %d: unknown specialization %r",
            conn_id,
            _CLOSE_UNKNOWN_SPECIALIZATION,
            spec_key,
        )
        _LIMITER.release(user_id)
        await _close_downstream(
            websocket,
            _CLOSE_UNKNOWN_SPECIALIZATION,
            f"Unknown specialization: {spec_key}",
        )
        return

    # From here on the slot is HELD, so every exit path must release it.
    if rehearsal:
        # THE REHEARSAL OPENS NO RECORDS. `_open_records` is the one function
        # that writes the conversation, the `interview_sessions` row and the
        # consent check, and it is keyed on a student; not calling it is the
        # whole mechanism, and it is why nothing downstream needs a "skip"
        # flag — the writers below are constructed only when it ran. The
        # policy is the deployment default with BOTH storage scopes off, so an
        # engine that reads `store_transcript` sees the truth, and the time
        # limit is the default one, which the engine floors at Bedrock's wall.
        rehearsal_policy = replace(
            default_policy(), store_transcript=False, store_audio=False
        )
        await _run_relay(
            websocket,
            conn_id,
            user_id,
            specialization,
            on_turn=None,
            on_report=None,
            on_finalize=None,
            on_heartbeat=None,
            recorder=None,
            max_seconds=rehearsal_policy.time_limit_seconds,
            interview_session_id=None,
        )
        return

    try:
        # The conversation is derived from the SESSION, never from the client —
        # the same rule POST /api/agent/ask and POST /api/voice/token follow.
        # to_thread because get_or_create is synchronous SQLAlchemy and this
        # coroutine is on the loop shared with every other live interview.
        opened = await asyncio.to_thread(
            _open_records,
            user_id,
            Role(session["role"]),
            student_id,
            conn_id,
            specialization,
        )
        conversation_id = opened.conversation_id
        interview_session_id = opened.interview_session_id
        consent_id = opened.consent_id
        policy = opened.policy
    except _ConsentRequired as exc:
        # 4013, and BEFORE the generic handler below on purpose: this is a
        # refusal, not a fault, and reporting it as 1011 "Internal error" would
        # tell a student to contact support about a panel they can tick
        # themselves. WARNING rather than exception() for the same reason —
        # there is no traceback worth printing.
        #
        # Note this is NOT mirrored into GET /api/interview/status. The client
        # treats `available: false` there as "do not start", which would hide
        # the very consent panel that fixes it; the panel is driven by
        # GET /api/interview/consent instead, and 4013 is what catches the cases
        # the panel cannot see (a cached bundle, a hand-rolled socket, a tab
        # left open across a revocation).
        _LIMITER.release(user_id)
        log.warning(
            "[conn=%s] WS /api/interview -> %d: no live consent for version %s",
            conn_id,
            _CLOSE_CONSENT_REQUIRED,
            exc,
        )
        await _close_downstream(
            websocket,
            _CLOSE_CONSENT_REQUIRED,
            "Interview consent required",
        )
        return
    except _UserSessionCapReached as exc:
        # 4012 from the DATABASE's view of the fleet: the student's other live
        # interview is on a different worker, where this process's _LIMITER
        # cannot see it. Same close code and same sentence as the in-process
        # refusal above — which worker spotted the duplicate is an
        # implementation detail the student must never need to care about.
        _LIMITER.release(user_id)
        log.warning(
            "[conn=%s] WS /api/interview -> %d: student %s already holds %s "
            "live interview(s) fleet-wide (per-user cap %d)",
            conn_id,
            _CLOSE_USER_SESSION_CAP,
            student_id,
            exc,
            settings.interview_max_sessions_per_user,
        )
        await _close_downstream(
            websocket,
            _CLOSE_USER_SESSION_CAP,
            "You already have a mock interview open. Close it and try again.",
        )
        return
    except _FleetFull as exc:
        # 1013, the code and sentence the per-process cap already uses: to the
        # student "this task is full" and "the deployment is full" are the same
        # fact, and the client already reads 1013 as "try again in a few
        # minutes". WARNING, because hitting it is the signal to raise the
        # quota or the ceiling, and the operator should see how often.
        _LIMITER.release(user_id)
        log.warning(
            "[conn=%s] WS /api/interview -> %d: %s interviews live fleet-wide "
            "(fleet ceiling %d)",
            conn_id,
            _CLOSE_OVERLOADED,
            exc,
            settings.interview_max_concurrent,
        )
        await _close_downstream(
            websocket, _CLOSE_OVERLOADED, "Too many interviews in progress"
        )
        return
    except _DailyCapReached as exc:
        # 4015: a refusal with a schedule, not a fault. WARNING because a
        # student meeting this cap honestly is rare — the expected causes are a
        # retry loop, a shared cookie, or a script, and the operator should see
        # which student id it is.
        #
        # WHICH CEILING TRIPPED IS LOGGED AND IS SAID OUT LOUD (B6.4). The
        # sentence comes off the exception rather than being written here,
        # because it is the same sentence the platform media bridge has to send
        # and a second copy of it is a second copy to forget to update.
        _LIMITER.release(user_id)
        log.warning(
            "[conn=%s] WS /api/interview -> %d: student %s tripped the %s "
            "ceiling at %s in 24 h",
            conn_id,
            _CLOSE_DAILY_CAP,
            student_id,
            exc.which,
            exc.count,
        )
        await _close_downstream(websocket, _CLOSE_DAILY_CAP, exc.message)
        return
    except Exception:
        _LIMITER.release(user_id)
        log.exception(
            "[conn=%s] WS /api/interview -> %d: cannot open the interview records",
            conn_id,
            _CLOSE_INTERNAL,
        )
        await _close_downstream(websocket, _CLOSE_INTERNAL, "Internal error")
        return

    # THE AUDIO RECORDER, or None — and None is the answer in every deployment
    # today. Both gates live inside recorder_for(): INTERVIEW_RECORDING_ENABLED
    # (false by default) AND a live `scope_store_audio` grant of the current
    # version. It is called HERE rather than inside the relay because
    # the engines import no ORM model and no database code at all,
    # and a recording feature is not what that containment gets spent on. It is
    # total by its own contract — a bad id, an unwritable root or an unreachable
    # database all return None and the interview runs without a recording — so
    # there is deliberately no try around it. to_thread because the consent
    # check it performs is a query, and this coroutine shares a loop with every
    # other live interview's audio.
    # B6.1 adds the COLLEGE's switch to the two that were already there, and it
    # is passed rather than read inside `recorder_for` so that the decision is
    # the one taken under the advisory lock a moment ago. All three must be
    # true: the operator's INTERVIEW_RECORDING_ENABLED, the college's
    # `store_audio`, and the student's own live `scope_store_audio` grant. A
    # policy that turned recording on over a student who was never told would be
    # the one failure this whole area exists to prevent.
    recorder, audio_skip_reason = await asyncio.to_thread(
        recorder_or_reason,
        interview_session_id,
        user_id,
        policy_allows_audio=policy.store_audio,
    )

    # Captured on the loop, because the only thread that may hand work back to
    # an event loop is one holding a reference to it — asyncio.get_running_loop()
    # from the heartbeat's worker thread raises.
    loop = asyncio.get_running_loop()

    # Breaking one construction cycle, in the smallest place it can be broken:
    # the heartbeat hook has to be able to stop the relay (4014), and the relay
    # is constructed with its hooks. The closure reads this list at call time,
    # which is always after the append two lines below — the first heartbeat is
    # a minute away, and the relay is not even running yet.
    relay_box: list[InterviewEngine] = []

    def _consent_withdrawn() -> None:
        """4014, from the heartbeat's worker thread back onto the event loop.

        `call_soon_threadsafe` rather than calling `request_stop` directly: that
        method sets an asyncio.Event, and setting one from another thread pokes
        loop internals without waking the selector. The session would still end
        (the watchdog re-enters `wait()` every _WATCHDOG_INTERVAL_S and Event
        short-circuits on an already-set flag) but it would end by accident, and
        under asyncio debug mode it would raise instead. One hop makes it
        correct rather than lucky.
        """
        try:
            loop.call_soon_threadsafe(
                relay_box[0].request_stop,
                _CLOSE_CONSENT_REVOKED,
                "Consent withdrawn",
            )
        except RuntimeError:
            # The loop is already closing, which means the session is ending
            # anyway and with a better-informed code than this one.
            log.info(
                "[conn=%s] Consent was withdrawn while the loop was closing; "
                "the session is already ending",
                conn_id,
            )

    await _run_relay(
        websocket,
        conn_id,
        user_id,
        specialization,
        on_turn=_make_turn_writer(
            conversation_id,
            interview_session_id,
            store_transcript=policy.store_transcript,
        ),
        on_report=_make_report_writer(interview_session_id),
        on_finalize=_make_finalizer(
            interview_session_id, audio_skip_reason=audio_skip_reason
        ),
        on_heartbeat=_make_heartbeat(
            interview_session_id,
            consent_id=consent_id,
            on_consent_revoked=_consent_withdrawn,
            consent_scopes=opened.consent_scopes,
        ),
        recorder=recorder,
        # B6.1: the college's session length. The engine floors it at Bedrock's
        # own 8-minute stream wall (`_effective_cap`), so this can only ever
        # SHORTEN an interview — which is the only direction a policy is allowed
        # to move it, because a number here that outlived the provider's wall
        # would end the interview mid-verdict.
        max_seconds=policy.time_limit_seconds,
        interview_session_id=interview_session_id,
        relay_box=relay_box,
    )


async def _run_relay(
    websocket: WebSocket,
    conn_id: str,
    user_id: str,
    specialization: Specialization | None,
    *,
    on_turn: Callable[..., None] | None,
    on_report: Callable[..., None] | None,
    on_finalize: Callable[..., None] | None,
    on_heartbeat: Callable[[], None] | None,
    recorder: Any | None,
    max_seconds: int,
    interview_session_id: str | None,
    relay_box: list[InterviewEngine] | None = None,
) -> None:
    """Construct the engine, run it, release the slot, close the record.

    ONE BODY FOR TWO CALLERS: the real interview, which arrives with every
    writer wired, and the Main Admin's rehearsal, which arrives with every
    hook `None` and no `interview_session_id` — so Layer 2's backstop has no
    row to close and is skipped. The engines already treat a `None` hook as
    "do not write" (their per-hook guards predate this), which is what makes
    the rehearsal a call with fewer arguments rather than a second relay.
    The slot was acquired by the caller and is released HERE, on every exit.
    """
    # WHICH ENGINE. Chosen here and nowhere else: both classes satisfy
    # interview_core.InterviewEngine — the same constructor, the same
    # (code, reason) from run() — so every writer, the limiter, the recorder and
    # all three finalization layers below are identical whichever one runs and
    # never learn which one spoke.
    #
    # BOTH IMPORTS ARE LAZY. The Nova engine pulls aiohttp and the whole smithy
    # stack in and the local one pulls faster-whisper and Piper; a deployment
    # pays for the engine it runs and not for the other.
    engine_cls: type[InterviewEngine]
    if settings.interview_engine.strip().lower() == "local":
        from ..interview_local import LocalSession

        engine_cls = LocalSession
    else:
        from ..interview_nova import NovaSonicSession

        engine_cls = NovaSonicSession

    relay = engine_cls(
        websocket,
        conn_id,
        on_turn=on_turn,
        specialization=specialization,
        on_report=on_report,
        on_finalize=on_finalize,
        on_heartbeat=on_heartbeat,
        recorder=recorder,
        max_seconds=max_seconds,
    )
    if relay_box is not None:
        relay_box.append(relay)
    _LIVE_SESSIONS.add(relay)
    code, reason = _CLOSE_INTERNAL, "Internal error"
    try:
        # ONE TRACE PER INTERVIEW. To the FastAPI integration this handler is a
        # single upgrade request that never returns, so the eight minutes of
        # audio, Bedrock turns and database writes inside it were invisible —
        # the least observable path in the system and also the most complex.
        # Starting it here rather than at the top of the handler is deliberate:
        # everything above is the refusal path (auth, consent, the caps), which
        # is HTTP-shaped and already traced, and a transaction spanning it would
        # report a five-millisecond 4013 as an interview.
        #
        # The engine's spans attach to this without being passed anything: the
        # SDK carries the active transaction in a context variable, and the
        # TaskGroup children created inside relay.run() inherit that context.
        #
        # Tags only. Nothing here is a student's words.
        with tracing.transaction(
            f"interview {getattr(specialization, 'key', None) or 'generic'}",
            op="websocket.server",
            conn_id=conn_id,
            interview_session_id=interview_session_id or "rehearsal",
            engine=engine_cls.__name__,
        ):
            code, reason = await relay.run()
    except asyncio.CancelledError:
        # App shutdown that outran the graceful drain. Reported honestly and
        # re-raised: swallowing CancelledError breaks the shutdown it belongs to.
        code, reason = _CLOSE_GOING_AWAY, "Server shutting down"
        raise
    except Exception:
        # Nothing in relay.run() matched, so this is a bug here rather than peer
        # behaviour. The traceback is the point; the student gets a generic 1011.
        log.exception("[conn=%s] Unhandled error in the interview relay", conn_id)
        code, reason = _CLOSE_INTERNAL, "Internal error"
    finally:
        _LIVE_SESSIONS.discard(relay)
        _LIMITER.release(user_id)
        await _close_downstream(websocket, code, reason)
        # LAYER 2 of finalization, and it exists for the exits Layer 1 cannot
        # reach: the relay raising before its own finalizer ran, and shutdown
        # cancelling this coroutine (CancelledError re-raises AFTER this block,
        # so this still runs). Idempotent by predicate, not by flag — one UPDATE
        # with `AND status = 'running'`, so when the relay already finalized this
        # touches zero rows and says nothing. A rehearsal has no row to close.
        #
        # Bounded honesty about cancellation: under CancelledError the await
        # below may itself be cancelled, in which case the thread's UPDATE
        # usually still lands and Layer 3's sweeper covers the case where it does
        # not. That is the correct shape — a deploy must not be held open by a
        # bookkeeping write, and there is a third layer for exactly this.
        if interview_session_id is None:
            return
        try:
            await asyncio.to_thread(
                _finalize_if_running, interview_session_id, conn_id, code, reason
            )
        except Exception:
            # Never allowed to replace the outcome with a teardown detail: the
            # student's close frame has already gone out with the real code, and
            # _close_downstream documents the same discipline one line above.
            log.exception(
                "[conn=%s] Interview backstop finalization failed", conn_id
            )


@dataclass(frozen=True, slots=True)
class _OpenedInterview:
    """What one opened interview is running under.

    A RECORD RATHER THAN A LONGER TUPLE, and the reason is the call site: this
    used to be `(conversation_id, interview_session_id, consent_id)` unpacked in
    two places — here and `voice_platform/api/media_bridge.py` — and B6.1 adds
    four more facts that the socket has to carry from the advisory-locked
    transaction all the way to the engine. A seven-tuple is a positional
    argument list nobody can read, and the first person to reorder it breaks the
    platform path silently.

    `policy` is resolved ONCE, inside the lock, and is then the ONLY answer this
    interview uses. Re-reading it after the lock would let an edit that lands
    mid-handshake apply to half of one interview — the transcript suppressed but
    the recorder already built, or a retention window that disagrees with the
    stamp on the row.
    """

    conversation_id: str
    interview_session_id: str
    consent_id: str
    policy: EffectivePolicy
    #: The three booleans the pinned acknowledgement actually carries. The
    #: heartbeat compares a SUCCESSOR against these, so that superseding an
    #: acknowledgement with an identical one cannot end a live call — see
    #: `_make_heartbeat`.
    consent_scopes: tuple[bool, bool, bool]


def _open_records(
    user_id: str,
    role: Role,
    student_id: str,
    conn_id: str,
    specialization: Specialization | None,
) -> _OpenedInterview:
    """The conversation AND the interview_sessions row, in one session, one hop.

    Opened together because a turn write needs both ids and because
    `interview_turns.interview_session_id` is NOT NULL — the session row must
    exist BEFORE the first turn, which is one extra insert per interview and not
    one per turn. A failure of either fails the socket the same way (1011), which
    is the honest answer: an interview whose record cannot be opened would be an
    interview nobody can review afterwards.

    THE CONSENT GATE IS NOW ENFORCED, and it is the FIRST statement — §8.3's
    ordering constraint is satisfied: `apps/web` posts to
    POST /api/interview/consent before it starts an interview and maps 4013 and
    4014, so turning this on no longer locks every existing student out of the
    feature on the deploy that ships it. It reads and it refuses; there is no
    "record whatever happened to be live" path left, because a grant that is not
    required is not a grant.

    IT REFUSES BEFORE IT WRITES. Nothing is created on this path — no
    conversation, no `interview_sessions` row — so a student who has not agreed
    leaves behind no record of an interview that never happened, and the orphan
    sweeper never meets a `running` row that had no socket.

    IT FAILS CLOSED by construction: an unreachable database raises out of this
    function and the socket closes 1011, which refuses the interview. "We could
    not check whether they agreed" and "they agreed" are not the same sentence,
    and only one of them may put a student's voice on a remote provider's wire.

    `consent_id` still pins the exact grant, and that remains the whole point of
    the column: "was this student consented, to what wording, at the time of
    interview X" stays answerable years later, after the grant has been revoked
    and re-given twice.
    """
    db = SessionLocal()
    try:
        # Serialize concurrent opens FOR THIS STUDENT across every worker for
        # the few milliseconds this transaction lives. The two caps below are
        # count-then-insert, and without a lock a burst of simultaneous
        # handshakes (2 per worker x WEB_CONCURRENCY workers) all read the
        # pre-insert count and all pass — the review of the 2026-08 fix wave
        # demonstrated the overshoot. pg_advisory_xact_lock releases itself at
        # COMMIT/ROLLBACK, so there is no unlock path to forget, and keying on
        # hashtext(student_id) scopes the queueing to one student — two
        # different students never wait on each other.
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(student_id))))
        # THE COLLEGE'S POLICY, READ INSIDE THE LOCK (B6.1). Two indexed SELECTs
        # (the student's ancestry, then the row) and it inherits this function's
        # fail-closed property for free: an unreachable database raises here,
        # the socket closes 1011, and no interview runs under a policy nobody
        # could read. Resolved ONCE and carried on the return value — see
        # `_OpenedInterview` for why it is never re-read.
        policy = policy_for_student(db, student_id)
        consent = db.scalar(
            select(InterviewConsent)
            .where(
                InterviewConsent.user_id == user_id,
                InterviewConsent.version == settings.interview_consent_version,
                InterviewConsent.revoked_at.is_(None),
            )
            .order_by(InterviewConsent.granted_at.desc())
            .limit(1)
        )
        if consent is None:
            # Scoped to the CURRENT version, so terms that changed since the
            # student last agreed put them back in front of the new copy rather
            # than carrying them on an old yes. `GET /api/interview/consent`
            # answers the same question the same way, which is why the client
            # shows the panel instead of ever meeting this close code — 4013 is
            # for the stale bundle, the hand-rolled socket and the tab that sat
            # open across a revocation.
            raise _ConsentRequired(settings.interview_consent_version)
        now = datetime.now(timezone.utc)
        # The VOLUME cap (audit H: unbounded Realtime spend), checked AFTER
        # consent — a student who has not agreed should meet the consent panel,
        # not a quota sentence — and BEFORE any write, so a refused attempt
        # leaves no row. Two COUNTs on the exact composite index
        # ix_interview_session_student_started; a rolling 24 h window rather
        # than a calendar day, so midnight is not a reset button.
        #
        # B6.4 MADE IT TWO CEILINGS, AND BOTH HALVES OF THIS COMMENT STILL
        # HOLD. The one this code was written with counted EVERY row —
        # abandoned and failed included — "because each one billed an upstream
        # handshake, and a cap that only counts clean finishes is a cap a crash
        # loop never hits". 04-backend-changes.md asks for completions only,
        # which would delete exactly that. So there are two: `daily_cap`
        # completions (the practice allowance — an interview that dropped out
        # at minute two no longer costs the student a turn, which is the point
        # of B6.4) and `attempt_cap` rows of any status (the spend ceiling,
        # higher by construction). The window's lower bound is reset-aware
        # (B6.4's `interview_cap_resets`), and it is an EXTRA bound on the
        # rolling window rather than a replacement for it, so a reset can only
        # ever move it forward.
        verdict = evaluate_caps(db, student_id, policy, now)
        if not verdict.allowed:
            raise _DailyCapReached(
                verdict.completed if verdict.which == "daily" else verdict.attempts,
                which=verdict.which or "daily",
                message=cap_message(verdict, policy),
            )
        # The CROSS-WORKER concurrency check (see _UserSessionCapReached): the
        # per-process _LIMITER already refused same-worker duplicates before we
        # got here, so this catches the tab that landed on ANOTHER worker.
        # Checked after the daily cap on purpose — a student at both walls
        # should hear the daily one, because it is the one no closed tab fixes.
        live_now = db.scalar(
            select(func.count())
            .select_from(InterviewSession)
            .where(
                InterviewSession.student_id == student_id,
                InterviewSession.status == "running",
                InterviewSession.heartbeat_at
                >= now - timedelta(seconds=_LIVE_ELSEWHERE_GRACE_S),
            )
        )
        if (live_now or 0) >= settings.interview_max_sessions_per_user:
            raise _UserSessionCapReached(live_now)
        # THE FLEET CEILING (see _FleetFull), last of the refusals: a student
        # at their own limits should hear about those, which only they can fix.
        # The lock is taken AFTER the per-student one on every path, so the two
        # can never be acquired in opposite orders, and it is held only for the
        # rest of this short transaction. Same freshness rule as the per-user
        # count, so a killed task's rows stop occupying slots after three
        # missed heartbeats rather than until the nightly sweep.
        if settings.interview_max_concurrent > 0:
            db.execute(select(func.pg_advisory_xact_lock(_FLEET_LOCK_KEY)))
            live_fleet = db.scalar(
                select(func.count())
                .select_from(InterviewSession)
                .where(
                    InterviewSession.status == "running",
                    InterviewSession.heartbeat_at
                    >= now - timedelta(seconds=_LIVE_ELSEWHERE_GRACE_S),
                )
            )
            if (live_fleet or 0) >= settings.interview_max_concurrent:
                raise _FleetFull(live_fleet)
        conversation_id = convo.get_or_create(db, user_id, role).id
        row = InterviewSession(
            student_id=student_id,
            conversation_id=conversation_id,
            specialization=specialization.key if specialization else None,
            conn_id=conn_id,
            consent_id=consent.id,
            started_at=now,
            heartbeat_at=now,
            # STORED rather than computed at read time, so changing the setting
            # — or, since B6.1, the college's `retention_days` — never
            # retroactively re-dates an interview a student was already promised
            # 180 days for. That promise is the reason this column exists, and
            # it is why `PUT /api/admin/interview-policies/...` says in so many
            # words that lowering the number applies to interviews held AFTER
            # the save rather than sweeping the ones already taken.
            retention_until=now + timedelta(days=policy.retention_days),
            # B6.1: the college has turned the transcript off, so the turn
            # writer will skip both rows. RECORDED ON THE SESSION because
            # `turns_emitted` > `turns_persisted` is AGENTS.md's runbook signal
            # for dropped writes, and without this flag every interview at such
            # a college would fire it — the runbook would then be noise and stop
            # being read at all. "We chose not to keep this" and "we lost this"
            # must not look the same.
            transcript_suppressed=not policy.store_transcript,
        )
        db.add(row)
        db.commit()
        return _OpenedInterview(
            conversation_id=conversation_id,
            interview_session_id=row.id,
            consent_id=consent.id,
            policy=policy,
            consent_scopes=(
                bool(consent.scope_live_ai),
                bool(consent.scope_store_transcript),
                bool(consent.scope_store_audio),
            ),
        )
    finally:
        db.close()


def _finalize_if_running(
    interview_session_id: str, conn_id: str, code: int, reason: str
) -> None:
    """LAYER 2: close a record whose relay never got to close it itself.

    Knows strictly less than Layer 1 — it has the close code and nothing else, so
    it cannot tell a completed interview from an abandoned one and does not
    pretend to. It only ever runs when Layer 1 did not, and a row it wrote is
    already a sign that something went wrong on the way out.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        result = db.execute(
            update(InterviewSession)
            .where(
                InterviewSession.id == interview_session_id,
                InterviewSession.status == "running",
            )
            .values(
                status=_backstop_status(code),
                terminal_reason=f"{code} {reason}",
                close_code=code,
                ended_at=now,
            )
        )
        db.commit()
        if result.rowcount:
            log.warning(
                "[conn=%s] Interview record %s was closed by the router backstop; "
                "the relay's own finalizer did not run",
                conn_id,
                interview_session_id,
            )
        # B6.2 again, and unconditionally rather than under `if result.rowcount`.
        # This layer runs on EVERY exit, including the ordinary one where Layer 1
        # already closed the row and updated nothing here — and Layer 1's own
        # summary write can have failed (it swallows, by design). A second
        # attempt costs one indexed SELECT on a socket that has already closed
        # and is the difference between "the trend point is missing until
        # somebody runs the backfill" and "it is there".
        ensure_summary(db, interview_session_id)
    finally:
        db.close()


def _backstop_status(code: int) -> str:
    """Terminal status from a close code ALONE — Layer 2's whole vocabulary.

    Deliberately not shared with the relay's mapping: that one also knows whether
    the scorecard settled, which is what separates `completed` from `abandoned`,
    and this one cannot. Rather than guess `completed` from a 1000 (which also
    covers a student pressing End at minute three), it takes the conservative
    reading. A record that under-claims is arguable; one that claims an interview
    completed when nobody heard a verdict is not.
    """
    if code in (_CLOSE_OK, _CLOSE_GOING_AWAY, _CLOSE_IDLE, _CLOSE_SESSION_CAP):
        return _TERMINAL_ABANDONED
    return _TERMINAL_FAILED


def shutdown_interviews() -> None:
    """Ask every live interview to close itself. Called from app/main.py lifespan."""
    ask_all_sessions_to_stop(_LIVE_SESSIONS)
