"""The Amazon Nova 2 Sonic engine behind the REEP mock interviewer.

    browser  <--WS /api/interview-->  THIS PROCESS  <--HTTP/2-->  Bedrock

THE hosted engine, and the default one (`INTERVIEW_ENGINE=nova`);
app/interview_local.py is the alternative that keeps everything on the machine.
The contracts both hold to — the persona, the records, the close codes, the
caps — live in app/interview_core.py.

The DOWNSTREAM event vocabulary (`response.created`, `response.done`,
`conversation.item.input_audio_transcription.completed`, the five `reep.*`
controls) is inherited from the OpenAI relay this replaced, and is kept
deliberately: it is the Angular client's contract, the client is good, and
renaming events to match whichever model is speaking would be churn paid for in
a screen students depend on. The event names are the contract; which model
produced the audio is an implementation detail the browser must never know.

NO API KEY, AND THAT IS THE POINT. The relay's OPENAI_API_KEY was a spendable
credential that had to be pasted into the environment of every host that runs an
interview. This engine has none: the bidirectional stream is signed with SigV4
from whatever the standard AWS chain resolves — the ECS task role, the instance
profile, an SSO profile, or the environment — so an AWS-hosted REEP grants
`bedrock:InvokeModelWithBidirectionalStream` to the role it already has and
stores no secret anywhere.

RULE 1 (AGENTS.md) reads here EXACTLY as it read in the relay, and for the
same reason: Bedrock is a remote provider. No student record enters this
session — no marks, attendance, CGPA, USN or resume text. What this module
authors upstream is the fixed `_INTERVIEWER_PERSONA`, the fixed per-phase and
per-turn directives composed in app/interview_matrix.py, and nothing else; the
rest of the uplink is the student's own microphone. This module imports no ORM
model and no database code, which is what makes that a property of the call
graph rather than a promise. If the interview is ever personalised, that path
goes through complete_chat(..., carries_student_data=True) in app/ai/llm.py and
degrades when the gate refuses — the same shape as /student/resume/generate
falling back to used_ai=false.

WHO OWNS THE TURN, STATED PLAINLY: **Nova owns the turn, this engine owns the
phase.** The retired v3 relay set `turn_detection.create_response: false` and
issued exactly one `response.create` from one call site, which made "one open
question at a time" a property of the call graph — read
docs/interview-engine-v3.md for why that mattered, because the reasoning
survives its engine. Nova 2 Sonic has no equivalent switch: it detects the end
of the student's speech and answers on its own, and that IS the product (its
turn-taking and its barge-in are the reason to use it). So the arc is steered
rather than driven:

  * the phase machine still ticks on ACCEPTED answers only, judged by the same
    deterministic `classify_answer` word gate as the local engine, so a
    student's scorecard is comparable whichever engine ran the interview;
  * the QUESTION phases (probing, deep dive) are BRIEFED ONCE at the handshake
    — `build_arc_briefing`, appended to the system prompt, stating the same
    counting rule the word gate applies — and the model moves through them on
    its own. They used to arrive as a CONTROL NOTE (a cross-modal text input,
    the documented way to put text into a live Nova session) sent the moment
    the student's transcript landed, and that was the bug reported as "it
    cuts itself off mid-sentence and changes the question": Nova's ASR
    transcript is the FIRST block of the completion in which Nova is already
    composing its reply, so a note sent on it is a user turn that barges in
    on that reply, and one held until the reply has finished provokes a
    second question on top of the one the student was just asked. A note can
    steer the NEXT turn or interrupt THIS one; it cannot shape the reply in
    flight. A briefing can, because it is there before the answer is;
  * the two beats that must happen at a fixed point regardless of what the
    model would do next — "any questions for us?" and the closing verdict —
    and the clock's forced verdict are control notes, HELD while a reply is
    in flight (`_steer`) and sent in the gap between turns, with the directive
    telling the model to set aside any question it has just asked. Those
    three are engine-driven on purpose: a model that decided for itself when
    the questioning was over could decide it one answer before the engine
    does and then wait for a verdict note that never comes;
  * a clarification is NOT injected. An engine that holds the turn can ask a
    too-short answer for more detail — the relay did, and the local engine still
    does; here the model has already started replying, and a second directive
    would produce a second question. The briefing tells the model to ask for
    more on its own instead. The turn is still RECORDED as `too_short`/
    `filler`, which is the fact a mentor reads, and it still does not advance
    the arc;
  * AN INTERRUPTION THAT WAS NOT AN ANSWER GETS THE QUESTION BACK (2026-09-17).
    Nova stops speaking the moment it hears the student — that is barge-in,
    and it is right for a student who has started answering. It is wrong for
    a cough, a chair, a "hmm", or the interviewer's own voice coming back
    through the student's speakers, and Nova cannot tell those apart: it
    abandons the question, emits its interruption marker (the browser's queue
    is flushed, so the voice does not tail off, it vanishes), and then answers
    whatever it heard — which for a cough is "Sorry?" and for an echo is the
    question it was asking, put to itself. Reported from real interviews as
    "the voice drops and it goes to the next question". So when the transcript
    that provoked an interruption turns out to be `empty`, `filler` or
    `too_short`, a `resume` directive is steered — HELD, like every note,
    until the reply in flight has finished, so the recovery lands in the gap
    rather than as a second barge-in — and the model finishes the question it
    was asking. A transcript that is the interviewer's OWN last sentence
    (`_looks_like_echo`) is recorded as `echo`, counts for nothing, and gets
    the same recovery whether or not a marker was seen. A real answer given
    over the interviewer is still a real answer and moves the arc as before.
    And a student who says "next question" is recorded as `skipped` and gets
    nothing steered at all: the model heard them, and the briefing already
    tells it to move on without pressing.

THE SCORECARD IS A TOOL CALL. Nova speaks everything it generates, so the
relay's trick — one extra text-only response after the verdict — would have read
a JSON blob aloud to the student. Instead the session declares one tool,
`submit_scorecard`, and the closing control note asks the model to call it: the
arguments are the scorecard, they are never spoken, never written into the chat
history, and they are persisted whatever happens to them. The grading
instructions are `REPORT_DIRECTIVE` verbatim, shared with both other engines,
so the bar does not move with the transport.

THE 8-MINUTE WALL. A Nova bidirectional stream is closed by the service after 8
minutes, which is less than INTERVIEW_MAX_SECONDS (900). An interview that runs
into it ends mid-sentence with no verdict and no scorecard — the exact failure
the phase machine exists to prevent — so this engine treats
NOVA_SONIC_CONNECTION_SECONDS as the real cap and forces the wrap-up early
enough for the verdict and the scorecard to finish inside it.

Wire format downstream, unchanged from the engine this replaced:

    browser -> relay : BINARY = raw PCM16 LE mono 24 kHz
                       TEXT   = JSON control ({"type": "reep.end"}, ...)
    relay -> browser : BINARY = raw PCM16 LE mono 24 kHz (decoded)
                       TEXT   = JSON control

Upstream, Nova is fed 16 kHz by default (NOVA_SONIC_INPUT_RATE_HZ) because that
is the rate every AWS sample streams and the model is documented against; it
speaks 24 kHz, which is the browser's rate, so the downlink is never resampled.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Final

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from .config import settings
from .interview_audio import TRACK_INTERVIEWER, TRACK_STUDENT
from .tracing import span
from .interview_matrix import (
    REPORT_DIRECTIVE,
    SKIP_RULE,
    InterviewPhase,
    InterviewStateMachine,
    Specialization,
    build_arc_briefing,
    build_instructions,
    classify_answer,
    nova_voice_for,
    phase_directive,
    turn_directive,
    words_of,
)

# The payload records, the persona and the close codes are IMPORTED, never
# redefined. They are the contract between an engine and the router's writers,
# and a parallel definition would drift the moment either side gained a field --
# silently, because both would still construct and only one would carry the new
# value into the database. The persona is imported for a stronger reason still:
# it is verbatim product spec, and a student sitting the Nova interview must be
# assessed against the same words as one sitting the OpenAI interview.
from .interview_core import (
    _CLOSE_GOING_AWAY,
    _CLOSE_IDLE,
    _CLOSE_NOT_CONFIGURED,
    _CLOSE_OK,
    _CLOSE_SESSION_CAP,
    _CLOSE_UPSTREAM_UNAVAILABLE,
    _INTERVIEWER_PERSONA,
    _ConnLog,
    _ReportRecord,
    _SessionEnded,
    _SessionOutcome,
    _TurnRecord,
    _TurnWriteRefused,
    _first_leaf,
    _parse_report,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

# The browser link, unchanged from the retired relay in both directions: the
# client's capture and playback are built for this rate, and nothing here is
# worth a client change.
_CLIENT_RATE_HZ: Final[int] = 24_000
# What Nova speaks. Requested explicitly in promptStart so the downlink needs no
# resampling at all -- the bytes Bedrock sends are the bytes the browser plays.
_OUTPUT_RATE_HZ: Final[int] = 24_000
# One client frame. Only used to size the downlink chunks we forward: the
# browser's jitter buffer schedules per buffer, and one three-second frame would
# arrive as a single scheduling event with nothing to smooth a late one.
_CHUNK_MS: Final[int] = 40
_DOWNLINK_CHUNK_BYTES: Final[int] = int(_OUTPUT_RATE_HZ * _CHUNK_MS / 1000) * 2

# Anything larger than ~0.5 s of 24 kHz PCM in ONE frame is not a browser
# capturing a microphone. Dropped and counted rather than forwarded: the same
# bound the relay put on its uplink, for the same reason -- one client must not
# be able to push arbitrary bytes into a billed upstream session.
_MAX_CLIENT_FRAME_BYTES: Final[int] = 24_000


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------

# How often the guardrail loop looks at the clock. Coarse on purpose: it decides
# caps, not turns.
_WATCHDOG_INTERVAL_S: Final[float] = 1.0
# How often heartbeat_at is stamped on the interview_sessions row. Matched to
# the relay's was, because retention's orphan sweeper and the consent-revocation
# poll are both written against that interval and neither knows which engine
# produced the row.
_HEARTBEAT_WRITE_INTERVAL_S: Final[float] = 60.0
# How long teardown waits for in-flight turn writes before cancelling them. A
# wedged database must not hold the browser's socket open.
_TURN_WRITE_DRAIN_S: Final[float] = 2.0
# Bound on ONE awaited record write (the evaluation, the session outcome).
_RECORD_WRITE_TIMEOUT_S: Final[float] = 3.0
# Bound on flushing and closing the audio recording.
_AUDIO_CLOSE_TIMEOUT_S: Final[float] = 10.0
# Bound on the polite upstream close (contentEnd / promptEnd / sessionEnd). A
# stream that will not take them is already gone, and the 8-minute service
# timeout will reap it regardless.
_UPSTREAM_CLOSE_TIMEOUT_S: Final[float] = 5.0
# Taken off NOVA_SONIC_CONNECTION_SECONDS before anything is scheduled against
# it. The service's 8 minutes are measured from ITS view of the connection, not
# from the moment this coroutine noticed; a session that plans to speak right up
# to the wall gets cut off by it.
_CONNECTION_MARGIN_S: Final[float] = 20.0
# Reserved at the end of the effective cap for the wrap-up: the "any questions"
# beat, the spoken verdict and the scorecard tool call. Measured against the
# relay's own wrap-up, which needs ~25 s for the report alone on top of a verdict
# that runs 30-45 s spoken.
_WRAP_UP_RESERVE_S: Final[float] = 90.0


def _boto3_credentials() -> Any | None:
    """Frozen AWS credentials from boto3's default chain, or None. Never raises.

    BLOCKING -- call it via asyncio.to_thread. boto3's chain is the one every
    other AWS call in this process (S3, ECS metadata, Secrets Manager via the
    task) already succeeds with, which on Fargate means the container
    credentials endpoint. The preview Bedrock SDK carries its own chain; handing
    it boto3's answer as a static identity takes that chain -- and whatever it
    does when a provider stalls -- out of the interview's connect path.
    None means "let the SDK's own chain try", so a machine with no AWS
    configuration at all still gets the SDK's error rather than ours.
    """
    try:
        import boto3  # noqa: PLC0415

        credentials = boto3.session.Session().get_credentials()
        if credentials is None:
            return None
        return credentials.get_frozen_credentials()
    except Exception:  # noqa: BLE001 - a credential lookup failure is the SDK chain's to report
        return None


# ---------------------------------------------------------------------------
# The upstream event vocabulary
# ---------------------------------------------------------------------------

# Nova brackets every block of content in contentStart / <content> / contentEnd,
# and the ROLE plus the generationStage on the contentStart is what says which
# of four different things a `textOutput` is. Named here rather than compared
# inline, because "USER" on an output event means "the model's transcript of the
# student", which is the single most confusable string in this file.
_ROLE_USER: Final[str] = "USER"
_ROLE_ASSISTANT: Final[str] = "ASSISTANT"
_STAGE_SPECULATIVE: Final[str] = "SPECULATIVE"
_STAGE_FINAL: Final[str] = "FINAL"

# Barge-in, as it actually arrives: Nova emits a textOutput whose CONTENT is
# this marker rather than a dedicated event. Matched loosely (the spacing inside
# the braces has changed between releases) and never shown to the student.
_INTERRUPT_MARKERS: Final[tuple[str, ...]] = ('"interrupted"', "'interrupted'")

# The sender strings the ROUTER'S writer understands, named rather than spelled
# at each call site. It maps `speaker = "student" if sender == "user" else
# "interviewer"`, so "student" -- the obvious guess -- lands in the else branch
# and files the student's own answer under the interviewer's name. A silent
# corruption: the row is written, the counters match, and only a human reading
# the transcript back sees the interviewer saying the student's words.
_SENDER_STUDENT: Final[str] = "user"
_SENDER_INTERVIEWER: Final[str] = "assistant"

# Bedrock's error members, which can arrive as ordinary events on the stream
# rather than as a transport failure. Ended as 4002 rather than ignored: a
# stream that has answered with a validation or throttling error will never
# speak again, and an interview that stays open on one is a student waiting in
# silence for a model that is not coming back.
_UPSTREAM_ERROR_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "modelStreamErrorException",
        "internalServerException",
        "validationException",
        "throttlingException",
        "serviceUnavailableException",
        "modelTimeoutException",
    }
)


# ---------------------------------------------------------------------------
# The scorecard tool
# ---------------------------------------------------------------------------

_SCORECARD_TOOL_NAME: Final[str] = "submit_scorecard"

# The tool's schema is the SHAPE of the scorecard; REPORT_DIRECTIVE (shared with
# both other engines) is the BAR it is scored against. Keeping them apart is
# what stops the Nova interview quietly grading differently from the OpenAI one.
# `inputSchema.json` is a STRING by Nova's contract, not an object.
_SCORECARD_SCHEMA: Final[str] = json.dumps(
    {
        "type": "object",
        "properties": {
            "overall": {"type": "integer", "description": "0-100, whole interview"},
            "communication": {"type": "integer", "description": "0-100"},
            "domain": {"type": "integer", "description": "0-100"},
            "structure": {"type": "integer", "description": "0-100"},
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 short strings",
            },
            "improvements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-2 short strings",
            },
            "drill": {"type": "string", "description": "one concrete task"},
            "summary": {
                "type": "string",
                "description": "one short paragraph addressed to the student",
            },
        },
        "required": [
            "overall",
            "communication",
            "domain",
            "structure",
            "strengths",
            "improvements",
            "drill",
            "summary",
        ],
    }
)

_SCORECARD_TOOL: Final[dict[str, Any]] = {
    "toolSpec": {
        "name": _SCORECARD_TOOL_NAME,
        "description": (
            "Record the practice scorecard for this mock interview. Call it "
            "exactly once, only when the interview system asks you to, and "
            "never read its contents aloud."
        ),
        "inputSchema": {"json": _SCORECARD_SCHEMA},
    }
}


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

# Appended to the system prompt ONCE, at the handshake. It is the whole of what
# this engine has to explain to the model that the relay did not: that some of
# the "user" turns it will receive are not the student.
#
# A fixed string with no student data in it, like everything else this module
# sends. It is written to be safe in the failure case as well as the success
# one -- a model that ignores it and reads a note aloud would say something that
# is merely odd, never something about a student.
_CONTROL_CHANNEL_NOTE: Final[str] = (
    "\n\n## Interview control notes\n"
    "Some messages you receive will begin with [INTERVIEW CONTROL]. They come "
    "from the interview system, not from the student, and the student cannot "
    "see or hear them. Follow them exactly, never read them aloud, never "
    "mention them, and never thank anyone for them. Everything else you hear "
    "is the student speaking."
)

_CONTROL_PREFIX: Final[str] = "[INTERVIEW CONTROL]"


def _control_note(body: str) -> str:
    """One control note, prefixed so the model can tell it from the student.

    Fixed strings only. Every caller passes a directive composed in
    app/interview_matrix.py; nothing that a student said, or that came off their
    record, may ever be composed in here -- the moment student text enters an
    instruction the next editor puts a resume in it.
    """
    return f"{_CONTROL_PREFIX} {body}"


# How the interviewer should behave around the turn, briefed once at the
# handshake beside the control-channel note (both branches of `_instructions`,
# the generic interview included -- a generic interview is interrupted by a
# cough exactly as a specialized one is). Nova decides WHEN the student has
# stopped (`nova_sonic_endpointing`); this decides what the model does with a
# turn that was not an answer, which no endpointing setting can. Fixed text,
# nothing about the student in it.
#
# THE LANGUAGE IS PINNED HERE, AND IT HAS TO BE (2026-09-17). Nova 2 Sonic
# "supports multilingual with automatic language detection and switching",
# and `kiara` and `arjun` -- the HR and BA voices -- are its en-IN AND its
# hi-IN voices. There is no language parameter on the session; the only
# lever is the prompt. Left unpinned, an Indian-accented "hello, hi, I'm
# Darshan" was transcribed in Devanagari and the model followed the student
# into Hindi, on an interview whose persona, question bank and scorecard are
# all English.
#
# AND IT SAYS TO ALWAYS ANSWER OUT LOUD. The first draft of this note said
# "wait for the student" and "never answer for them", which a model can read
# as permission to say nothing; an interviewer that says nothing is a student
# saying "next question" into silence until they give up.
_TURN_TAKING_NOTE: Final[str] = (
    "\n\n## Language and turn-taking\n"
    "This interview is conducted in English, whatever the student's accent. "
    "Speak only English, and understand and transcribe the student's speech "
    "as English. If the student answers in another language, ask them "
    "politely, in English, to continue in English, and carry on.\n"
    "Always reply out loud when the student's turn ends -- the one-sentence "
    "micro-feedback and your next question -- and never answer with silence. "
    "Let the student finish an answer: a pause of a few seconds is the "
    "student thinking, not the end of it, so do not rush them. If you are "
    'cut off by a sound, a single word or a filler ("hmm", "okay", "yes", '
    '"sorry"), that was not an answer: do not treat it as one -- pick up the '
    "question you were asking, briefly, and let the student answer it. If "
    "what you hear is your own question repeated back, that is an echo from "
    "the student's speakers, not the student: ask the question again, "
    "briefly. "
    f"{SKIP_RULE}"
)

# A transcript that arrives this soon after Nova's interruption marker is the
# utterance that caused the interruption: Nova takes the turn ~2 s after the
# speaker stops (the LOW endpointing pause) and transcribes within a couple of
# seconds more. Generous rather than tight, because a late transcript wrongly
# read as "not the interruption" costs the recovery, while an early one wrongly
# read as "the interruption" costs at most one restated question.
_INTERRUPTION_ANSWER_WINDOW_S: Final[float] = 20.0

# THE TURN BOUNDARIES NOVA 2 SONIC ACTUALLY SENDS (measured 2026-10-29 against
# amazon.nova-2-sonic-v1:0 from inside this account). ONE completionStart for
# the whole session and NO completionEnd between replies, so nothing in the
# stream says "this answer is over" or "this reply is over" in so many words.
# Each reply is: ASSISTANT TEXT SPECULATIVE (contentEnd PARTIAL_TURN), ASSISTANT
# AUDIO (contentEnd END_TURN), and then -- about as long after as the speech
# takes to PLAY -- ASSISTANT TEXT FINAL (contentEnd END_TURN). A student's words
# arrive as USER TEXT blocks ending PARTIAL_TURN, and one answer can be several.
# So the student's answer is complete when the interviewer's reply to it
# STARTS, and the reply is over when its FINAL text closes.
#
# How long the student's words may wait for that reply to start before a held
# note may go upstream anyway. Nova delivers the transcript as it takes the
# turn and starts its reply within about a second, so this is never reached in
# an ordinary exchange; it exists for words Nova does not answer at all (a
# "thanks" after the verdict), which must not hold the verdict back for ever.
_STUDENT_REPLY_WAIT_S: Final[float] = 5.0
# How long past the moment the interviewer's audio should have finished
# PLAYING the engine waits for that reply's FINAL text before closing the reply
# without it. The FINAL text is what releases a held note, so a reply that
# never sends one would otherwise hold the invitation and the verdict until the
# session cap.
_FINAL_TEXT_GRACE_S: Final[float] = 4.0

# Echo: the interviewer's own sentence coming back through the student's
# speakers and being transcribed as the student's answer. Judged on the
# LONGEST RUN of consecutive words the transcript shares with what the
# interviewer last said -- a run, not a bag of words, because a real answer
# reuses the question's words ("the role of the manager is to lead the team")
# without repeating its sentence. Both bounds are needed: a run of at least
# this many words, covering at least this share of the transcript.
_ECHO_MIN_RUN_WORDS: Final[int] = 6
_ECHO_MIN_COVERAGE: Final[float] = 0.8


def _longest_common_run(a: list[str], b: list[str]) -> int:
    """Length of the longest run of consecutive words `a` and `b` share.

    Plain dynamic programming over two short lists; a transcript and one
    interviewer turn are a few dozen words each, so this is microseconds on
    the hot path and needs no cleverness.
    """
    if not a or not b:
        return 0
    best = 0
    previous = [0] * (len(b) + 1)
    for word in a:
        current = [0] * (len(b) + 1)
        for j, other in enumerate(b, start=1):
            if word == other:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def _looks_like_echo(transcript: str, interviewer_said: str) -> bool:
    """Is this "student answer" the interviewer's own last sentence?

    True when a run of at least _ECHO_MIN_RUN_WORDS consecutive words of the
    transcript appears, in order, in what the interviewer last said, and that
    run covers at least _ECHO_MIN_COVERAGE of the transcript. A student who
    parrots the question and then answers it is not an echo -- the answer
    drags the coverage under the bar -- and a five-word fragment is judged by
    the word gate, not here.
    """
    # `words_of`, the gate's own tokeniser, so a transcript in any script is
    # compared word for word rather than read as having no words at all.
    heard = words_of(transcript)
    said = words_of(interviewer_said)
    if len(heard) < _ECHO_MIN_RUN_WORDS or not said:
        return False
    run = _longest_common_run(heard, said)
    return run >= _ECHO_MIN_RUN_WORDS and run >= _ECHO_MIN_COVERAGE * len(heard)


# `reep.client.stats`: the fields the browser sends, and the only ones kept.
# Numbers are clamped to a sane non-negative range and the one word is checked
# against its two values, so a frame from anybody's devtools can put nothing
# but a bounded number or a known word into the log line.
_CLIENT_STAT_FIELDS: Final[tuple[str, ...]] = (
    "judged",
    "suppressed",
    "keepalive",
    "local_barge_ins",
    "confirmed_barge_ins",
    "held_frames",
    "dropped",
    "underruns",
    "lead_ms",
    "coupling",
    "output_latency_ms",
)
_CLIENT_STAT_MAX: Final[float] = 1_000_000.0
_CLIENT_ROUTES: Final[frozenset[str]] = frozenset({"speaker", "earphones"})


def _client_stats(payload: dict[str, Any]) -> dict[str, float | int | str]:
    """Whitelist and clamp one `reep.client.stats` frame. Never raises."""
    out: dict[str, float | int | str] = {}
    route = payload.get("route")
    if isinstance(route, str) and route in _CLIENT_ROUTES:
        out["route"] = route
    for field in _CLIENT_STAT_FIELDS:
        value = payload.get(field)
        # bool is an int in Python; a `true` here is not a count.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value != value:  # NaN
            continue
        clamped = min(max(float(value), 0.0), _CLIENT_STAT_MAX)
        out[field] = round(clamped, 3) if field == "coupling" else int(clamped)
    return out


def _format_client_stats(stats: dict[str, float | int | str]) -> str:
    """`-` when the browser sent none; otherwise `key=value` pairs, in order."""
    if not stats:
        return "-"
    keys = ("route", *_CLIENT_STAT_FIELDS)
    return ",".join(f"{key}={stats[key]}" for key in keys if key in stats)


def _resample(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Linear resample of PCM16 LE mono. Dependency-free beyond numpy.

    Adequate and deliberately simple: speech at 16-24 kHz is band-limited well
    below Nyquist on both sides, and the alternative is a scipy dependency in
    the API image for a filter nobody would hear. Same function the local engine
    uses to feed Whisper, for the same reason.
    """
    if src_hz == dst_hz or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if x.size == 0:
        return b""
    n = int(round(x.size * dst_hz / src_hz))
    if n <= 0:
        return b""
    y = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def _is_interruption(text: str) -> bool:
    """Whether a textOutput is Nova's barge-in marker rather than speech.

    It arrives as a small JSON object inside a text event -- `{ "interrupted" :
    true }` -- and the exact spacing has moved between releases, so this matches
    the key and the value rather than the literal string.
    """
    lowered = text.strip().lower()
    if not lowered.startswith("{") or "true" not in lowered:
        return False
    return any(marker in lowered for marker in _INTERRUPT_MARKERS)


# ---------------------------------------------------------------------------
# The upstream stream
# ---------------------------------------------------------------------------


class _NovaUpstream:
    """One Bedrock bidirectional stream, and the only place the SDK is touched.

    THE IMPORT IS LAZY, and that is not an excuse to leave it undeclared:
    `aws-sdk-bedrock-runtime` is in requirements.txt and CI's `api-imports` job
    proves it (AGENTS.md — a lazy import only moves the crash from boot to the
    first student who reaches the path). It is lazy because it pulls aiohttp and
    the whole smithy stack in, and a deployment running INTERVIEW_ENGINE=openai
    or =local should not pay that at import time.

    Sends are serialised behind a lock. The uplink has two writers — the audio
    pump and the control-note injections — and a Bedrock event stream frames one
    JSON document per chunk: two coroutines interleaving mid-send would produce
    a document that is not one.
    """

    __slots__ = (
        "_model",
        "_region",
        "_log",
        "_client",
        "_stream",
        "_output",
        "_lock",
        "_attach_lock",
        "_closed",
        "phase",
    )

    def __init__(self, model: str, region: str, conn_log: Any) -> None:
        self._model = model
        self._region = region
        self._log = conn_log
        self._client: Any = None
        self._stream: Any = None
        self._output: Any = None
        # attach_output() may be entered twice — once from run()'s opening
        # TaskGroup and once lazily from the first receive() — and
        # await_output() must be awaited exactly once per stream.
        self._attach_lock = asyncio.Lock()
        # Which sub-step of open() is in flight, for the timeout reason. Production
        # said "(open timed out)" and that was not enough to act on: resolving
        # credentials, building the client and opening the HTTP/2 stream are
        # three different faults with three different owners.
        self.phase = "not started"
        self._lock = asyncio.Lock()
        self._closed = False

    async def open(self) -> None:
        """Resolve credentials, open the stream, and wait for it to be writable."""
        try:
            from aws_sdk_bedrock_runtime.client import (  # noqa: PLC0415
                AsyncBedrockRuntimeClient,
            )
            from aws_sdk_bedrock_runtime.config import (  # noqa: PLC0415
                AsyncBedrockRuntimeConfig,
            )
            from aws_sdk_bedrock_runtime.models import (  # noqa: PLC0415
                InvokeModelWithBidirectionalStreamOperationInput,
            )
            from smithy_aws_core.identity import (  # noqa: PLC0415
                AWSCredentialsIdentity,
                StaticCredentialsResolver,
            )
            from smithy_http.aio.crt import AWSCRTHTTPClient  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - requirements.txt declares it
            raise RuntimeError(
                "aws-sdk-bedrock-runtime and awscrt are not both installed; "
                "INTERVIEW_ENGINE=nova needs both (see apps/api-py/requirements.txt)"
            ) from exc

        # `resolve` is the SDK's own credential + endpoint resolution: the
        # environment, the shared config files, the container/IMDS role, in the
        # standard order. Nothing about the CREDENTIALS is resolved by hand —
        # anything this code worked out itself would be a second, worse copy of
        # a chain the rest of the AWS toolchain on the box already agrees on.
        #
        # THE TRANSPORT IS THE ONE THING WE MUST NAME, and leaving it to the
        # default is not a slower path or a fallback — it is a hard refusal.
        # `resolve()` defaults to the aiohttp transport, which declares
        # SUPPORTS_DUPLEX_STREAMING = False, so the very next line raises
        # UnsupportedTransportError before a packet leaves the process: every
        # region, every account, credentials or none. The bidirectional API is
        # HTTP/2 with both halves open at once, and awscrt is the only transport
        # in this stack that does that. The student sees close 4002.
        # CREDENTIALS FROM BOTO3, HANDED TO THE SDK AS A STATIC IDENTITY.
        # boto3 is already a runtime dependency and its chain is the one every
        # other AWS call in this process succeeds with -- on Fargate that is the
        # ECS container credentials endpoint. The preview SDK has its own chain
        # (smithy_aws_core.identity.IdentityChain) which we leave to its own
        # devices only when boto3 finds nothing, so a laptop with no AWS profile
        # still gets the SDK's own error rather than ours. Run in a thread: boto3
        # is blocking, and this is on the interview's connect path.
        self.phase = "resolving credentials"
        resolver = None
        frozen = await asyncio.to_thread(_boto3_credentials)
        if frozen is not None:
            resolver = StaticCredentialsResolver(
                AWSCredentialsIdentity(
                    access_key_id=frozen.access_key,
                    secret_access_key=frozen.secret_key,
                    session_token=frozen.token,
                )
            )
        self.phase = "resolving client config"
        if resolver is not None:
            config = await AsyncBedrockRuntimeConfig.resolve(
                region=self._region,
                transport=AWSCRTHTTPClient(),
                aws_credentials_identity_resolver=resolver,
            )
        else:
            config = await AsyncBedrockRuntimeConfig.resolve(
                region=self._region, transport=AWSCRTHTTPClient()
            )
        self._client = AsyncBedrockRuntimeClient(config=config)
        self.phase = "opening the stream"
        self._stream = await self._client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=self._model)
        )
        self.phase = "open"
        # DELIBERATELY NOT `await self._stream.await_output()` HERE. That call
        # waits for Bedrock's response headers, and Bedrock does not send them
        # until it has received the first input event (sessionStart) — the AWS
        # Nova Sonic samples read output only after the session-start events
        # have gone out. Awaiting it before the handshake is a deadlock: the
        # engine waits for headers, the service waits for an event, and the
        # student sits on an open socket that never speaks while the
        # interview_sessions row stays `running` with no close code. That was
        # production. The output half is attached lazily by the first
        # receive(), which the upstream pump makes only after _handshake().

    async def attach_output(self) -> None:
        """Wait for the response half of the stream — AFTER sessionStart went out.

        Public and duck-typed from run(): the test fakes reduce Bedrock to
        send() and have no response half to attach, so run() calls this only
        when the upstream offers it. Idempotent, and receive() calls it too, so
        an upstream whose run() skipped it still attaches on first read.
        """
        async with self._attach_lock:
            if self._output is not None or self._stream is None:
                return
            _initial, self._output = await self._stream.await_output()

    async def send(self, event: dict[str, Any]) -> None:
        """Send one event document. Silently a no-op once closed.

        A send after close is not an error worth propagating: it happens when a
        control note is injected in the same tick the watchdog decided the
        session is over, and the interview is ending either way.
        """
        if self._closed or self._stream is None:
            return
        from aws_sdk_bedrock_runtime.models import (  # noqa: PLC0415
            BidirectionalInputPayloadPart,
            InvokeModelWithBidirectionalStreamInputChunk,
        )

        chunk = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(
                bytes_=json.dumps(event).encode("utf-8")
            )
        )
        async with self._lock:
            if self._closed:
                return
            await self._stream.input_stream.send(chunk)

    async def receive(self) -> dict[str, Any] | None:
        """The next parsed event, or None when the stream is finished.

        Bedrock's output stream carries typed exception members alongside the
        ordinary chunks (validation, throttling, model timeout). They arrive as
        events with no payload bytes, so anything without `value.bytes_` is
        raised as a RuntimeError naming the member — a stream that is answering
        with a ValidationException must not present as a quiet hang.
        """
        if self._stream is None:
            return None
        # First call attaches the response half. By now the handshake has sent
        # sessionStart (run() orders it so), which is what Bedrock waits for
        # before it answers — see the note in open().
        await self.attach_output()
        if self._output is None:
            return None
        event = await self._output.receive()
        if event is None:
            return None
        value = getattr(event, "value", None)
        raw = getattr(value, "bytes_", None)
        if raw is None:
            detail = getattr(value, "message", "") or type(event).__name__
            raise RuntimeError(f"Bedrock stream error: {detail}")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            # A frame this process cannot read is not a reason to end an
            # interview that is otherwise going fine.
            self._log.warning("Unparseable frame from Bedrock; ignoring it")
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def aclose(self, *, prompt_name: str | None, audio_content: str | None) -> None:
        """The polite close: contentEnd, promptEnd, sessionEnd, then the socket.

        Bounded and total. Skipping the closing events leaks the prompt on
        Bedrock's side until the 8-minute timeout reaps it, which is worth five
        seconds of teardown — but not the browser's close frame, which is why
        every failure here is logged and swallowed.
        """
        if self._closed:
            return
        try:
            async with asyncio.timeout(_UPSTREAM_CLOSE_TIMEOUT_S):
                if prompt_name and audio_content:
                    await self.send(
                        {
                            "event": {
                                "contentEnd": {
                                    "promptName": prompt_name,
                                    "contentName": audio_content,
                                }
                            }
                        }
                    )
                if prompt_name:
                    await self.send(
                        {"event": {"promptEnd": {"promptName": prompt_name}}}
                    )
                await self.send({"event": {"sessionEnd": {}}})
        except TimeoutError:
            self._log.warning("Bedrock did not accept the closing events in time")
        except Exception as exc:  # noqa: BLE001 - teardown never raises
            self._log.warning("Could not close the Bedrock session cleanly: %s", exc)
        self._closed = True
        for closer in (
            getattr(getattr(self._stream, "input_stream", None), "close", None),
            getattr(self._stream, "close", None),
            getattr(self._client, "close", None),
        ):
            if closer is None:
                continue
            try:
                async with asyncio.timeout(_UPSTREAM_CLOSE_TIMEOUT_S):
                    await closer()
            except Exception as exc:  # noqa: BLE001 - teardown never raises
                self._log.debug("Bedrock teardown step failed: %s", exc)


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


class NovaSonicSession:
    """One interview, relayed to Amazon Nova 2 Sonic.

    Mirrors `_RelaySession`'s constructor and `run()` contract exactly, so the
    router picks between the three engines on one setting and every writer
    around them — the limiter, the recorder, the turn writer, the finalizer and
    all three layers of session close — keeps working without ever learning
    which one spoke.
    """

    def __init__(
        self,
        websocket: WebSocket,
        conn_id: str,
        *,
        on_turn: Callable[[str, str, str, _TurnRecord], None] | None = None,
        specialization: Specialization | None = None,
        on_report: Callable[[_ReportRecord], None] | None = None,
        on_finalize: Callable[[_SessionOutcome], None] | None = None,
        on_heartbeat: Callable[[], None] | None = None,
        recorder: Any | None = None,
        max_seconds: int | None = None,
    ) -> None:
        self._ws = websocket
        self._conn_id = conn_id
        # A per-SESSION cap, set by the voice platform from its per-degree
        # time-limit rows (app/voice_platform/engine). None means the global
        # INTERVIEW_MAX_SECONDS, which is what /api/interview always passes.
        # Either way Bedrock's 8-minute wall still wins in _effective_cap.
        self._max_seconds = int(max_seconds) if max_seconds else None
        self._on_turn = on_turn
        self._on_report = on_report
        self._on_finalize = on_finalize
        self._on_heartbeat = on_heartbeat
        self._recorder = recorder
        self._machine = InterviewStateMachine(specialization)
        self._log = _ConnLog(log, {"conn_id": conn_id, "session_id": None})

        self._upstream: _NovaUpstream | None = None
        # Nova's identifiers. `promptName` ties every event of this interview
        # together and `audio_content` is the ONE audio block the student's
        # microphone streams into for the whole session — Nova wants a single
        # container, not one per utterance.
        self._prompt_name = f"reep-{conn_id}"
        self._audio_content = f"{self._prompt_name}-mic"
        self._note_seq = 0
        # Bedrock's own session id, off the first output event that carries one.
        # Stored for the interview record: it is what an AWS support case is
        # opened with, and there is no other way back from a row to a stream.
        self._session_id: str | None = None

        # What each open content block IS. Nova says role and generation stage
        # on contentStart and then sends bare textOutput/audioOutput events
        # keyed by contentId, so without this map a transcript of the STUDENT
        # and the model's own planned speech are indistinguishable.
        self._content: dict[str, dict[str, str]] = {}
        # The student's transcript, accumulated per content block: one block
        # can arrive as several `textOutput` events.
        self._user_text: dict[str, list[str]] = {}
        # ...and the ANSWER, accumulated across blocks. Nova ends every USER
        # block PARTIAL_TURN and one answer can be several blocks, so a closed
        # block is NOT a finished answer: judging each one counted one answer
        # as several and raced the arc to the wrap-up in under a minute (29
        # "answers" in one interview on 2026-10-29). The answer is judged when
        # the interviewer's reply to it starts (`_finish_student_turn`).
        # `_student_last_at` is None when nothing is waiting.
        self._student_parts: list[str] = []
        self._student_turn_id = ""
        self._student_last_at: float | None = None
        self._judging_answer = False
        # Whether an interviewer reply is in flight: raised by its first
        # SPECULATIVE text or audio block, lowered when its FINAL text closes
        # (see _STUDENT_REPLY_WAIT_S for why those, and not completionStart /
        # completionEnd, which Nova 2 Sonic does not send per reply).
        self._response_open = False
        # Steering notes held back until no reply is in flight and none is
        # about to start. `_inject` is a USER text input, and Nova treats that
        # as a barge-in: it stops generating and answers the new input. So a
        # directive sent during a reply cuts the interviewer off mid-sentence
        # and replaces the question the student was listening to -- and the
        # interruption marker that follows makes `_on_text_output` flush the
        # browser's PCM queue, so the voice does not tail off, it vanishes.
        # Every beat is decided on a transcript, and a transcript is judged as
        # the reply to it starts, so a beat is always held; it goes upstream
        # when that reply's FINAL text closes (`_take_next_beat`).
        self._pending_notes: list[str] = []
        # Per-reply bookkeeping: whether any of the reply's content has
        # arrived (completionStart can open the first reply before it does),
        # and, for a FINAL text that never comes, when the audio started, how
        # much of it there was (which says how long it takes to play), whether
        # it has ended, whether the FINAL text closed before it did, and the
        # deadline after which the reply is closed without its FINAL text.
        self._reply_has_content = False
        self._reply_audio_started_at: float | None = None
        self._reply_audio_last_at: float | None = None
        self._reply_audio_bytes = 0
        self._reply_audio_ended = False
        self._reply_final_closed = False
        self._final_deadline: float | None = None
        # The verdict note has gone upstream; the reply that follows it is the
        # verdict, and only once THAT reply is over is the scorecard asked for.
        self._verdict_sent = False
        self._reply_follows_verdict = False
        self._verdict_spoken = False
        # WRAP_UP is announced to the browser when the verdict note goes up,
        # not when the machine ticks: the client reads every `response.done`
        # in wrap_up as "the interview is over, the report is being written",
        # and the invitation and its answer still come between the tick and
        # the verdict.
        self._wrap_up_announced = False
        # The model's FINAL sentence-level transcript of what it actually said,
        # which is what gets recorded. The SPECULATIVE text is streamed to the
        # browser live (it is what makes the caption appear while the
        # interviewer is still speaking) but it is what the model PLANNED to
        # say, and on a barge-in the two differ.
        self._assistant_final: list[str] = []
        # The SPECULATIVE text of the same turn, kept only as a fallback for
        # `_last_interviewer_text`. A turn cut off by a barge-in may end with
        # no FINAL text at all, and an echo of THAT question must be compared
        # with it -- not with the question before it, which it does not match,
        # so the echo would be counted as an answer and the arc advanced on
        # it. Never recorded and never persisted: FINAL stays the record.
        self._assistant_speculative: list[str] = []
        self._assistant_spoke = False
        # What the interviewer last said -- `_end_response`'s joined FINAL
        # text, or the speculative text where a barge-in left no FINAL --
        # which is what an echo through the student's speakers comes back as.
        # Only ever compared against, never sent anywhere.
        self._last_interviewer_text = ""
        # When Nova last reported a barge-in, so the transcript that follows
        # can be judged as "the thing that interrupted" rather than as an
        # answer. None until the first marker; consumed by the next transcript.
        self._interrupted_at: float | None = None
        # Whether the turn now closing has already been handled as
        # interrupted, so the text marker and an INTERRUPTED stop reason for
        # the same barge-in are one interruption. Cleared as the turn ends.
        self._interrupted_this_turn = False

        # The two-beat close, mirroring the relay: the tick into WRAP_UP asks
        # "any questions for us?" and the student's reply — any reply, never
        # word-gated, because "no, I'm good" is filler to the answer gate —
        # earns the verdict.
        self._awaiting_candidate_questions = False
        self._verdict_requested = False
        self._report_requested = False
        self._report_settled = False
        self._report_deadline: float | None = None
        self._report_status: str | None = None
        self._report_raw = ""
        self._pending_tool: dict[str, Any] | None = None

        # Odd-byte carry. A client frame that splits a 16-bit sample must not be
        # resampled on its own: half a sample interpreted as a whole one is
        # white noise, and every later frame in the session is misaligned.
        self._pcm_carry = b""

        now = time.monotonic()
        self._started_at = now
        # Seeded at start, so a student who never speaks at all is caught by the
        # idle cap rather than only by the session cap.
        self._last_audio_at = now
        self._heartbeat_at = now

        self._stop_requested = asyncio.Event()
        self._stop_outcome: tuple[int, str] = (_CLOSE_OK, "Interview complete")

        self._client_frames = 0
        self._client_bytes = 0
        self._oversized_frames = 0
        self._gate_closes = 0
        self._interruptions = 0
        # The browser's own audio counters (`reep.client.stats`): the latest
        # copy, already reduced to whitelisted numbers, for the end line.
        self._client_stats: dict[str, float | int | str] = {}

        # Counted where a turn is EMITTED, so the pair (turns_emitted,
        # turns_persisted) on the interview_sessions row answers the AGENTS.md
        # runbook's "the call sounded fine and saved nothing" with no join.
        self._turns_user = 0
        self._turns_assistant = 0
        self._turns_persisted = 0
        self._turn_seq = 0
        # Accepted answers, counted here rather than read off the machine: the
        # machine's counter only moves for a specialized interview, and a
        # generic one would otherwise record zero answers however well it went.
        self._answers_accepted = 0

        self._writes: set[asyncio.Task[None]] = set()
        self._finalized = False

    # -- lifecycle ---------------------------------------------------------

    def request_stop(self, code: int, reason: str) -> None:
        """Ask this session to end. Safe from another task; never blocks.

        The consent heartbeat (4014) and the app's shutdown drain (1001) both
        call this from outside the session's own tasks, which is why it only
        sets an event and lets the watchdog turn it into a close.
        """
        if not self._stop_requested.is_set():
            self._stop_outcome = (code, reason[:120])
            self._stop_requested.set()

    async def run(self) -> tuple[int, str]:
        """The whole interview. Returns the (code, reason) both sockets close with."""
        if not settings.nova_sonic_ready:
            # Caught at the router too, so this is the belt to that braces —
            # but an engine that opens a stream with no region composes an
            # endpoint URL of "https://bedrock-runtime..amazonaws.com" and the
            # student meets a DNS failure instead of a sentence.
            self._log.error(
                "INTERVIEW_ENGINE=nova but no AWS region is configured "
                "(NOVA_SONIC_REGION / BEDROCK_REGION / AWS_REGION)"
            )
            return _CLOSE_NOT_CONFIGURED, "Voice service not configured"

        upstream = _NovaUpstream(
            settings.nova_sonic_model.strip(), settings.nova_region, self._log
        )
        open_timeout = float(settings.nova_sonic_open_timeout_seconds)
        try:
            async with asyncio.timeout(open_timeout):
                await upstream.open()
        except TimeoutError:
            # The one failure that used to be silent. Named as such, because the
            # operator reading this log has a socket that never spoke and a
            # `running` row with no close code, and "timed out" is the sentence
            # that connects the two.
            self._log.error(
                "Nova Sonic stream did not open within %.0fs (region=%s model=%s)",
                open_timeout,
                settings.nova_region,
                settings.nova_sonic_model,
            )
            # The PHASE rides in the close reason, in parentheses. The student's
            # screen maps 4002 to its own sentence and never shows this text;
            # the reason lands in interview_sessions.terminal_reason and in the
            # browser's close frame, which is how an operator with no log access
            # — a diagnostic probe, a staff screen — can tell "the connection
            # never opened" from "it opened and the service never answered".
            return (
                _CLOSE_UPSTREAM_UNAVAILABLE,
                "Interviewer service unavailable "
                f"(open timed out while {getattr(upstream, 'phase', 'opening')})",
            )
        except Exception as exc:  # noqa: BLE001 - every failure is the same to the student
            # Credentials, IAM, an unsupported region, a throttle at the door.
            # All of them are 4002 to the browser and all of them are named in
            # the log, which is the only place the difference is actionable.
            self._log.error("Could not open the Nova Sonic stream: %s", exc)
            return (
                _CLOSE_UPSTREAM_UNAVAILABLE,
                f"Interviewer service unavailable ({type(exc).__name__})",
            )
        self._upstream = upstream

        code, reason = _CLOSE_OK, "Interview complete"
        # `settled` fixes the PRECEDENCE between clauses. A deliberate end
        # cancels the sibling pumps, and their disconnects land in the same
        # exception group -- so without this a clean 1000 could be overwritten
        # by the failure it caused. First cause wins, later ones are logged.
        settled = False
        try:
            # Handshake, THEN wait for Bedrock's response half, both under the
            # same bound as open(). Ordering is the fix: sessionStart must be on
            # the wire before anything waits for headers. The bound is the
            # guarantee: a service that never answers becomes a TimeoutError,
            # which the `except* Exception` below turns into 4002 — a sentence
            # on the student's screen inside twenty seconds, and a finalized
            # row, instead of a socket that is open and silent forever.
            # `attach_output` is duck-typed because the test fakes reduce
            # Bedrock to send(); only the real upstream has a response half.
            # CONCURRENTLY, not in either order — this is the AWS sample's own
            # shape (a receive task is started, then the session-start events
            # are sent). Both sequential orders deadlock against a real
            # Bedrock: attach-then-handshake waits for headers the service
            # sends only after sessionStart; handshake-then-attach waits for a
            # send() that can block until the connection the output side
            # establishes exists. Production showed each in turn. Running both
            # under one bound works whichever side the service is waiting on.
            attach_output = getattr(upstream, "attach_output", None)
            try:
                # The span that earns its keep: this is where interviews fail.
                # "did not open within 20s" is the most common failure on this
                # path and the one a student experiences as the assistant simply
                # not answering. Timing it separately from the rest of the
                # session is what distinguishes a slow Bedrock handshake from a
                # slow interview. Region and model, never a student's words.
                with span(
                    "bedrock.invoke_bidirectional_stream",
                    "open + handshake",
                    region=settings.nova_region,
                    model=settings.nova_sonic_model,
                    timeout_s=open_timeout,
                ):
                    async with asyncio.timeout(open_timeout):
                        async with asyncio.TaskGroup() as opening:
                            if attach_output is not None:
                                opening.create_task(
                                    attach_output(), name=f"nova-attach-{self._conn_id}"
                                )
                            opening.create_task(
                                self._handshake(), name=f"nova-handshake-{self._conn_id}"
                            )
            except TimeoutError:
                self._log.error(
                    "Nova Sonic handshake did not complete within %.0fs "
                    "(region=%s model=%s): the stream opened but the service "
                    "never answered sessionStart",
                    open_timeout,
                    settings.nova_region,
                    settings.nova_sonic_model,
                )
                raise _SessionEnded(
                    _CLOSE_UPSTREAM_UNAVAILABLE,
                    "Interviewer service unavailable (handshake timed out)",
                )
            await self._send_ready()
            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump_upstream(), name=f"nova-up-{self._conn_id}")
                group.create_task(self._pump_client(), name=f"nova-down-{self._conn_id}")
                group.create_task(self._watchdog(), name=f"nova-watch-{self._conn_id}")
        except* _SessionEnded as group_exc:
            ended = _first_leaf(group_exc, _SessionEnded)
            if ended is not None:
                code, reason, settled = ended.code, ended.reason, True
        except* WebSocketDisconnect as group_exc:
            disconnect = _first_leaf(group_exc, WebSocketDisconnect)
            self._log.info(
                "Browser disconnected (code=%s)", getattr(disconnect, "code", None)
            )
            if not settled:
                code, reason, settled = _CLOSE_OK, "Client disconnected", True
        except* Exception as group_exc:
            # Anything not matched above is a bug HERE rather than peer
            # behaviour, so the traceback is the point. Logged even when the
            # outcome is already settled: a failure that lost the race is still
            # the only record of what went wrong.
            self._log.exception("Nova interview failed: %s", group_exc)
            if not settled:
                code, reason = (
                    _CLOSE_UPSTREAM_UNAVAILABLE,
                    "Interviewer service unavailable",
                )
        finally:
            await upstream.aclose(
                prompt_name=self._prompt_name, audio_content=self._audio_content
            )
            self._record_unanswered_words()
            await self._drain_writes()
            await self._finalize_session(code, reason)
            self._log.info(
                "Interview ended %d %s: phase=%s answers=%d turns=%d/%d "
                "frames=%d bytes=%d interruptions=%d gate_closes=%d report=%s "
                "client=%s",
                code,
                reason,
                self._machine.phase.value,
                self._answers_accepted,
                self._turns_persisted,
                self._turns_user + self._turns_assistant,
                self._client_frames,
                self._client_bytes,
                self._interruptions,
                self._gate_closes,
                self._report_status or "-",
                _format_client_stats(self._client_stats),
            )
        return code, reason

    # -- the handshake -----------------------------------------------------

    def _instructions(self) -> str:
        """The system prompt, composed ONCE for the whole session.

        Nova has no `session.update`, so unlike the relay's this cannot be
        replaced when the phase changes — which is exactly why the whole arc
        is briefed here, up front (`build_arc_briefing`), rather than steered
        with a note per phase: a note is a user turn, and the module docstring
        says what one does to the reply in flight. The persona arrives first
        and verbatim, carrying the conduct rules and the rule-1 disclosure,
        and nothing a student said or a record holds is composed in.
        """
        spec = self._machine.specialization
        if spec is None:
            return _INTERVIEWER_PERSONA + _TURN_TAKING_NOTE + _CONTROL_CHANNEL_NOTE
        return (
            build_instructions(spec, _INTERVIEWER_PERSONA, InterviewPhase.OPENING)
            + "\n\n"
            + build_arc_briefing(spec)
            + _TURN_TAKING_NOTE
            + _CONTROL_CHANNEL_NOTE
        )

    async def _handshake(self) -> None:
        """sessionStart, promptStart, the system prompt, the open microphone.

        Order is fixed by Nova's contract and every step must land before the
        next: the prompt cannot be configured before the session exists, and no
        content may be sent before the prompt names it.
        """
        voice, requested = nova_voice_for(self._machine.specialization)
        if voice != requested:
            # A ValidationException at the handshake is an interview that never
            # starts and says nothing about why, so an unknown voice falls back
            # — loudly, because the operator meant something by it.
            self._log.warning(
                "NOVA voice %r is not a Nova 2 Sonic voice; using %r", requested, voice
            )

        inference: dict[str, Any] = {"maxTokens": 1024, "topP": 0.9}
        if settings.interview_temperature is not None:
            # Sent only when an operator has explicitly tuned it, in the same
            # spirit as the relay: an unverified parameter that is rejected
            # costs the whole session, and the model's own default is the
            # behaviour until somebody has a reason to move it.
            inference["temperature"] = float(settings.interview_temperature)

        await self._upstream_send(
            {
                "event": {
                    "sessionStart": {
                        "inferenceConfiguration": inference,
                        "turnDetectionConfiguration": {
                            "endpointingSensitivity": self._endpointing()
                        },
                    }
                }
            }
        )
        await self._upstream_send(
            {
                "event": {
                    "promptStart": {
                        "promptName": self._prompt_name,
                        "textOutputConfiguration": {"mediaType": "text/plain"},
                        "audioOutputConfiguration": {
                            "mediaType": "audio/lpcm",
                            "sampleRateHertz": _OUTPUT_RATE_HZ,
                            "sampleSizeBits": 16,
                            "channelCount": 1,
                            "voiceId": voice,
                            "encoding": "base64",
                            "audioType": "SPEECH",
                        },
                        "toolUseOutputConfiguration": {"mediaType": "application/json"},
                        "toolConfiguration": {"tools": [_SCORECARD_TOOL]},
                    }
                }
            }
        )

        system_content = f"{self._prompt_name}-system"
        await self._upstream_send(
            {
                "event": {
                    "contentStart": {
                        "promptName": self._prompt_name,
                        "contentName": system_content,
                        "type": "TEXT",
                        "interactive": False,
                        "role": "SYSTEM",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    }
                }
            }
        )
        await self._upstream_send(
            {
                "event": {
                    "textInput": {
                        "promptName": self._prompt_name,
                        "contentName": system_content,
                        "content": self._instructions(),
                    }
                }
            }
        )
        await self._upstream_send(
            {
                "event": {
                    "contentEnd": {
                        "promptName": self._prompt_name,
                        "contentName": system_content,
                    }
                }
            }
        )

        # The microphone container, opened BEFORE the kick-off note so that a
        # student who is already talking is heard from the first frame rather
        # than into a stream that is not listening yet.
        await self._upstream_send(
            {
                "event": {
                    "contentStart": {
                        "promptName": self._prompt_name,
                        "contentName": self._audio_content,
                        "type": "AUDIO",
                        "interactive": True,
                        "role": "USER",
                        "audioInputConfiguration": {
                            "mediaType": "audio/lpcm",
                            "sampleRateHertz": self._input_rate(),
                            "sampleSizeBits": 16,
                            "channelCount": 1,
                            "audioType": "SPEECH",
                            "encoding": "base64",
                        },
                    }
                }
            }
        )

        # WHO SPEAKS FIRST. Nova waits for the student, and a mock interview
        # that opens with silence teaches the student to open with silence. The
        # kick-off note is what makes the interviewer greet them — and for a
        # specialized interview it is also the OPENING phase directive, so the
        # arc starts where the state machine says it does.
        spec = self._machine.specialization
        opening = (
            phase_directive(spec, InterviewPhase.OPENING)
            if spec is not None
            else (
                "Begin the interview now: greet the student briefly, introduce "
                "yourself in one sentence, and ask them to introduce "
                "themselves. Ask nothing else yet."
            )
        )
        await self._inject(opening)

    def _endpointing(self) -> str:
        """HIGH | MEDIUM | LOW, defaulting rather than failing on a typo."""
        value = settings.nova_sonic_endpointing.strip().upper()
        if value in {"HIGH", "MEDIUM", "LOW"}:
            return value
        self._log.warning(
            "NOVA_SONIC_ENDPOINTING=%r is not HIGH/MEDIUM/LOW; using MEDIUM",
            settings.nova_sonic_endpointing,
        )
        return "MEDIUM"

    def _input_rate(self) -> int:
        """The uplink rate, restricted to the three Nova actually accepts.

        A rate outside the set is a ValidationException at the first audio
        frame — i.e. an interview that connects, greets the student and then
        never hears a word — so an unrecognised value falls back to 16 kHz.
        """
        rate = settings.nova_sonic_input_rate_hz
        if rate in (8000, 16000, 24000):
            return rate
        self._log.warning(
            "NOVA_SONIC_INPUT_RATE_HZ=%r is not 8000/16000/24000; using 16000", rate
        )
        return 16000

    # -- the pumps ---------------------------------------------------------

    async def _pump_client(self) -> None:
        """Browser -> Bedrock. Audio frames up, control frames handled here."""
        while True:
            message = await self._ws.receive()
            if message.get("type") == "websocket.disconnect":
                raise _SessionEnded(_CLOSE_OK, "Client disconnected")
            data = message.get("bytes")
            if data is not None:
                await self._forward_client_audio(data)
                continue
            text = message.get("text")
            if text is not None:
                self._handle_client_control(text)

    async def _forward_client_audio(self, frame: bytes) -> None:
        """One PCM frame: recorded as captured, resampled, sent as base64.

        The recorder is fed the ORIGINAL 24 kHz bytes, not the resampled ones:
        app/interview_audio.py writes 24 kHz WAVs and a 16 kHz payload in a
        24 kHz container plays back as a chipmunk — the one artefact that would
        make a recording useless as evidence of what was said.
        """
        self._client_frames += 1
        self._client_bytes += len(frame)
        self._last_audio_at = time.monotonic()
        if len(frame) > _MAX_CLIENT_FRAME_BYTES:
            self._oversized_frames += 1
            return
        self._capture(TRACK_STUDENT, frame)

        pcm = self._pcm_carry + frame
        if len(pcm) % 2:
            # Hold the orphaned byte for the next frame rather than dropping it:
            # dropping one byte shifts every later sample by half a word and the
            # whole session becomes noise upstream while sounding fine locally.
            self._pcm_carry = pcm[-1:]
            pcm = pcm[:-1]
        else:
            self._pcm_carry = b""
        if not pcm:
            return

        payload = _resample(pcm, _CLIENT_RATE_HZ, self._input_rate())
        await self._upstream_send(
            {
                "event": {
                    "audioInput": {
                        "promptName": self._prompt_name,
                        "contentName": self._audio_content,
                        "content": base64.b64encode(payload).decode("ascii"),
                    }
                }
            }
        )

    def _handle_client_control(self, text: str) -> None:
        """The `reep.*` control frames. None of them reach Bedrock.

        `reep.mic.gate` is counted and otherwise ignored: this engine runs no
        server-side VAD of its own that the interviewer's voice could fool —
        Nova does its own turn detection — so there is nothing to suppress. It
        is accepted rather than rejected so the client needs no branch for which
        engine it got. It deliberately does NOT advance the idle clock: a text
        frame that did would let a client hold a billed session open with no
        audio at all. The browser sends `{"state": "suppressed"}`; the
        `{"open": false}` spelling is kept for older clients -- counting only
        that one meant this counter read zero on every interview.

        `reep.client.stats` is the browser's own audio counters (the echo
        gate's barge-ins, playback underruns, the jitter buffer). The latest
        copy is kept, reduced to whitelisted numbers, and printed in the
        end-of-interview line -- the only place a "the voice kept breaking"
        report can be read without the student's browser console.
        """
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")
        if kind == "reep.end":
            self.request_stop(_CLOSE_OK, "Student ended the interview")
        elif kind == "reep.mic.gate" and (
            payload.get("state") == "suppressed" or payload.get("open") is False
        ):
            self._gate_closes += 1
        elif kind == "reep.client.stats":
            self._client_stats = _client_stats(payload)

    async def _pump_upstream(self) -> None:
        """Bedrock -> browser. One task, and the only one that reads the stream."""
        assert self._upstream is not None
        while True:
            event = await self._upstream.receive()
            if event is None:
                break
            await self._on_upstream_event(event)
        # The stream ended on its own. After a settled scorecard that is simply
        # the end of the interview; before one it is the 8-minute service wall
        # or a dropped connection, and the student must not be told "complete".
        if self._report_settled:
            raise _SessionEnded(_CLOSE_OK, "Interview complete")
        raise _SessionEnded(_CLOSE_UPSTREAM_UNAVAILABLE, "Interviewer stream ended")

    # -- upstream events ---------------------------------------------------

    async def _on_upstream_event(self, event: dict[str, Any]) -> None:
        body = event.get("event")
        if not isinstance(body, dict) or not body:
            return
        name = next(iter(body))
        payload = body.get(name) or {}
        if not isinstance(payload, dict):
            return

        session_id = payload.get("sessionId")
        if isinstance(session_id, str) and session_id and self._session_id is None:
            self._session_id = session_id
            self._log.extra["session_id"] = session_id  # type: ignore[union-attr]

        if name in _UPSTREAM_ERROR_EVENTS:
            # `message` here describes OUR event documents, never the student's
            # audio, so it is safe to log and useless to hide. It never reaches
            # the browser: the student gets the close code and a sentence.
            self._log.error(
                "Bedrock refused the stream (%s): %s", name, payload.get("message") or ""
            )
            raise _SessionEnded(
                _CLOSE_UPSTREAM_UNAVAILABLE, "Interviewer service unavailable"
            )

        if name == "completionStart":
            # Nova 2 Sonic sends ONE of these, ahead of its first reply, and
            # none per turn after that. It opens that first reply; every later
            # one is opened by its own content (`_on_reply_content`).
            await self._begin_response()
        elif name == "contentStart":
            await self._on_content_start(payload)
        elif name == "textOutput":
            await self._on_text_output(payload)
        elif name == "audioOutput":
            await self._on_audio_output(payload)
        elif name == "toolUse":
            self._on_tool_use(payload)
        elif name == "contentEnd":
            await self._on_content_end(payload)
        elif name == "completionEnd":
            await self._on_completion_end()
        # usageEvent carries nothing this engine acts on. Ignored deliberately
        # rather than logged: at ~one per turn it would bury everything else
        # in the log.

    async def _on_content_start(self, payload: dict[str, Any]) -> None:
        """Remember what an incoming content block IS before its content lands.

        `additionalModelFields` is a JSON STRING inside the JSON, and it is the
        only thing distinguishing the model's planned speech (SPECULATIVE) from
        its transcript of what it actually said (FINAL). Parsed defensively:
        an unreadable field costs the stage, never the turn.
        """
        content_id = str(payload.get("contentId") or "")
        if not content_id:
            return
        stage = ""
        extra = payload.get("additionalModelFields")
        if isinstance(extra, str) and extra:
            try:
                parsed = json.loads(extra)
                if isinstance(parsed, dict):
                    stage = str(parsed.get("generationStage") or "")
            except ValueError:
                stage = ""
        meta = {
            "role": str(payload.get("role") or ""),
            "type": str(payload.get("type") or ""),
            "stage": stage,
        }
        self._content[content_id] = meta
        # Bound: one interview is a few dozen content blocks, but a stream that
        # never closes one must not grow this map without limit.
        if len(self._content) > 256:
            self._content.pop(next(iter(self._content)))
        if meta["type"] == "AUDIO" and meta["role"] == _ROLE_ASSISTANT:
            # The interviewer's reply has begun (or goes on). Its SPECULATIVE
            # text counts too, but only once it carries words
            # (`_on_text_output`): Nova's interruption marker also arrives as
            # assistant text, and it starts nothing.
            await self._on_reply_content()

    async def _on_text_output(self, payload: dict[str, Any]) -> None:
        content_id = str(payload.get("contentId") or "")
        text = str(payload.get("content") or "")
        meta = self._content.get(content_id, {})
        role = str(payload.get("role") or meta.get("role") or "")
        stage = meta.get("stage", "")

        if _is_interruption(text):
            await self._on_interruption()
            return

        if role == _ROLE_USER:
            if text:
                self._user_text.setdefault(content_id, []).append(text)
            return
        if role != _ROLE_ASSISTANT or not text:
            return
        if stage != _STAGE_FINAL:
            await self._on_reply_content()
        if stage == _STAGE_FINAL:
            # What the interviewer ACTUALLY said, sentence by sentence. Recorded
            # rather than the speculative text: on a barge-in the two differ,
            # and the transcript a mentor reads must match the audio.
            if self._response_open:
                self._assistant_final.append(text)
            else:
                # A FINAL text for a reply already closed (it was closed
                # without one, or this is a second block). Too late to record,
                # and still what an echo of that reply would match.
                self._last_interviewer_text = (
                    f"{self._last_interviewer_text} {text.strip()}".strip()
                )
            return
        self._assistant_speculative.append(text)
        # SPECULATIVE: the live caption, aliased onto the event name the client
        # already renders. Never persisted — the FINAL text above is the record.
        await self._send_control(
            {
                "type": "response.audio_transcript.delta",
                "item_id": content_id,
                "response_id": content_id,
                "delta": text,
            }
        )

    async def _on_interruption(self) -> None:
        """BARGE-IN. Nova has already stopped generating; what matters here is
        the browser's queue, which may hold a second of speech the student is
        talking over. `reep.audio.flush` is the client's existing handler for
        exactly this. The stamp makes the transcript that follows readable as
        "the thing that interrupted" (`_took_interruption`)."""
        self._interruptions += 1
        self._interrupted_at = time.monotonic()
        self._interrupted_this_turn = True
        await self._send_control({"type": "reep.audio.flush"})

    async def _on_audio_output(self, payload: dict[str, Any]) -> None:
        """The interviewer's voice: base64 in, raw PCM out, in client-sized chunks."""
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            return
        try:
            pcm = base64.b64decode(content, validate=True)
        except (ValueError, TypeError):
            self._log.warning("Undecodable audio frame from Bedrock; dropped")
            return
        if not pcm:
            return
        self._assistant_spoke = True
        now = time.monotonic()
        if self._reply_audio_started_at is None:
            self._reply_audio_started_at = now
        self._reply_audio_last_at = now
        self._reply_audio_bytes += len(pcm)
        self._capture(TRACK_INTERVIEWER, pcm)
        for start in range(0, len(pcm), _DOWNLINK_CHUNK_BYTES):
            if self._ws.client_state is not WebSocketState.CONNECTED:
                return
            await self._ws.send_bytes(pcm[start : start + _DOWNLINK_CHUNK_BYTES])

    def _on_tool_use(self, payload: dict[str, Any]) -> None:
        """The scorecard, arriving as arguments rather than as speech.

        Held, not settled: the tool block is not finished until its contentEnd,
        and answering the model mid-block is how a stream ends in a
        ValidationException on the last event of an interview that otherwise
        went perfectly.
        """
        if str(payload.get("toolName") or "") != _SCORECARD_TOOL_NAME:
            self._log.warning("Ignoring unexpected tool call %r", payload.get("toolName"))
            return
        self._pending_tool = payload

    async def _on_content_end(self, payload: dict[str, Any]) -> None:
        content_id = str(payload.get("contentId") or "")
        meta = self._content.pop(content_id, {})
        kind = str(payload.get("type") or meta.get("type") or "")
        stop_reason = str(payload.get("stopReason") or "")

        if kind == "TEXT" and meta.get("role") == _ROLE_USER:
            # One block of the student's words. NOT a finished answer: Nova
            # ends every USER block PARTIAL_TURN, and one answer can be
            # several blocks. Held until the interviewer's reply starts.
            parts = self._user_text.pop(content_id, [])
            # Joined the same way the interviewer's own text is: each piece
            # stripped, one space between. Concatenating raw leaves a double
            # space where a piece arrived with a leading one, and a transcript
            # a mentor reads should not carry the transport's seams.
            joined = " ".join(part.strip() for part in parts if part.strip())
            if joined:
                self._student_parts.append(joined)
            if not self._student_turn_id:
                self._student_turn_id = content_id
            self._student_last_at = time.monotonic()
            return

        if kind == "AUDIO" and stop_reason in ("END_TURN", "INTERRUPTED"):
            if stop_reason == "INTERRUPTED" and not self._interrupted_this_turn:
                # Nova ended the audio block as interrupted without the
                # `{"interrupted": true}` text marker. It is the same barge-in,
                # and without the stamp the transcript that follows is judged
                # as an ordinary answer: no `resume`, and the question it cut
                # off is simply gone.
                await self._on_interruption()
            # Lets the browser's player drop its scheduling cursor and play the
            # tail out. The client accepts two spellings of this event (the
            # relay had two API generations to serve); this engine sends the one
            # that matches what it actually is.
            await self._send_control({"type": "response.audio.done"})
            if self._response_open:
                self._reply_audio_ended = True
                if self._reply_final_closed:
                    await self._on_reply_finished()
                else:
                    self._final_deadline = self._final_text_deadline(stop_reason)
        elif (
            kind == "TEXT"
            and meta.get("role") == _ROLE_ASSISTANT
            and meta.get("stage") == _STAGE_FINAL
            and stop_reason in ("END_TURN", "INTERRUPTED", "")
            and self._response_open
        ):
            # THE END OF THE INTERVIEWER'S REPLY: its transcript of what it
            # actually said, which Nova sends once the speech has played.
            if self._reply_audio_ended or not self._assistant_spoke:
                await self._on_reply_finished()
            else:
                # The words closed before the audio did. The reply is over
                # when the audio ends too, or once it has had time to play.
                self._reply_final_closed = True
                self._final_deadline = self._final_text_deadline("")
        elif kind == "TOOL" and self._pending_tool is not None:
            await self._settle_tool_use()

    def _final_text_deadline(self, stop_reason: str) -> float:
        """When to stop waiting for this reply's FINAL text.

        Nova sends it once the speech has PLAYED, and the audio arrives faster
        than real time, so the deadline is measured from when the audio started
        plus how long it takes to play. An interrupted reply stops playing at
        once, so its FINAL text is due straight away.
        """
        if (
            stop_reason == "INTERRUPTED"
            or self._reply_audio_started_at is None
            or self._reply_audio_last_at is None
        ):
            return time.monotonic() + _FINAL_TEXT_GRACE_S
        playback_s = self._reply_audio_bytes / (_OUTPUT_RATE_HZ * 2)
        played_at = max(self._reply_audio_last_at, self._reply_audio_started_at + playback_s)
        return played_at + _FINAL_TEXT_GRACE_S

    async def _on_completion_end(self) -> None:
        """A completion closed. Nova 2 Sonic sends this at the end of a stream,
        not per reply; it is still honoured as a reply's end, so a stream that
        does send it per reply loses nothing."""
        if self._response_open:
            await self._on_reply_finished()
        else:
            await self._take_next_beat()

    async def _on_reply_content(self) -> None:
        """A block of the interviewer's reply is starting.

        The first one opens the reply, and that is also the moment the
        student's answer is complete: Nova takes the turn when the student
        stops, so every word of that answer has arrived by now. Judged before
        the reply opens so the browser shows the student's line before the
        interviewer's, with `_holding` keeping any beat it decides held.
        """
        if self._response_open and self._reply_audio_ended:
            # A new reply while the last one is still open: its FINAL text
            # never came. Record it as it is and start the new one. Held notes
            # stay held -- this new reply is in flight.
            self._log.warning(
                "An interviewer reply sent no final transcript before the next "
                "one began; recorded without it"
            )
            await self._end_response()
        if self._response_open and self._reply_has_content:
            return
        await self._finish_student_turn()
        # A no-op when completionStart has already opened this reply.
        await self._begin_response()
        self._reply_has_content = True

    async def _on_reply_finished(self) -> None:
        """One interviewer reply is over: record it, then take the next beat."""
        verdict_reply = self._reply_follows_verdict
        await self._end_response()
        if verdict_reply:
            self._verdict_spoken = True
        await self._take_next_beat()

    def _holding(self) -> bool:
        """Must a control note wait? Yes while a reply is in flight, and while
        the student's words are waiting for the reply that is about to start."""
        if self._response_open or self._judging_answer:
            return True
        return (
            self._student_last_at is not None
            and time.monotonic() - self._student_last_at < _STUDENT_REPLY_WAIT_S
        )

    async def _take_next_beat(self) -> None:
        """Between replies: send what was held, or ask for the scorecard.

        THE ONLY PLACE the scorecard is requested, and only once the verdict
        has been SPOKEN: a request that arrives while the interviewer is still
        speaking is a second turn on top of the verdict, and the student would
        hear the interviewer talk over its own closing words. Called when a
        reply ends and on every watchdog tick, so a note held behind words Nova
        never answered still goes.
        """
        if self._holding():
            return
        if await self._drain_pending_notes():
            # A held directive just went upstream, so the next interviewer turn
            # is the one it asks for -- the verdict, in the case that matters.
            return
        if self._verdict_spoken and not self._report_requested:
            await self._request_report()

    async def _finish_student_turn(self) -> None:
        """Judge the words the student said since the interviewer last spoke."""
        if self._student_last_at is None:
            return
        parts, self._student_parts = self._student_parts, []
        turn_id, self._student_turn_id = self._student_turn_id, ""
        # A beat decided on this answer is held (`_holding`) rather than sent
        # onto the reply that is starting, however late that reply came.
        self._judging_answer = True
        try:
            await self._on_student_transcript(" ".join(parts), turn_id)
        finally:
            self._judging_answer = False
            self._student_last_at = None

    def _record_unanswered_words(self) -> None:
        """The session is closing with student words no reply ever followed:
        the "thank you" after the verdict, or an answer the socket cut. Saved as
        what they were; nothing is steered or advanced on them now."""
        if self._student_last_at is None:
            return
        transcript = " ".join(self._student_parts).strip()
        turn_id = self._student_turn_id
        self._student_parts, self._student_turn_id = [], ""
        self._student_last_at = None
        self._emit_turn(
            _SENDER_STUDENT,
            transcript,
            f"nova-{turn_id or self._turn_seq + 1}",
            status="completed" if transcript else "empty",
            quality=None if self._awaiting_candidate_questions else classify_answer(transcript),
        )

    # -- the student's turn ------------------------------------------------

    async def _on_student_transcript(self, text: str, content_id: str) -> None:
        """One student answer: recorded, judged, and (maybe) a beat of the arc.

        Called as the interviewer's reply to it starts (`_finish_student_turn`):
        Nova owns the turn and is already speaking, so nothing here waits for
        anything, and nothing here reaches the model directly. The phase tick
        is recorded and announced to the browser; the model was briefed on the
        question phases at the handshake and moves on the same rule. The two
        beats that must land at a fixed point — the invitation to ask
        questions and the verdict — go through `_steer`, which holds them
        until this reply has finished.
        """
        transcript = text.strip()
        await self._send_control({"type": "input_audio_buffer.speech_stopped"})
        await self._send_control(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": content_id,
                "transcript": transcript,
            }
        )
        turn_id = f"nova-{content_id or self._turn_seq + 1}"
        status = "completed" if transcript else "empty"

        if self._awaiting_candidate_questions:
            # The reply to "any questions for us?" — deliberately NOT put
            # through classify_answer: "no, I'm good, thanks" is filler to the
            # gate and must not earn a clarification on the final turn. The
            # interruption stamp is spent here too, so a "no thanks" said over
            # the invitation cannot make the NEXT short transcript (a thank-you
            # during the verdict) look like a barge-in that needs resuming.
            self._awaiting_candidate_questions = False
            self._took_interruption()
            self._emit_turn(
                _SENDER_STUDENT, transcript, turn_id, status=status, quality=None
            )
            self._verdict_requested = True
            await self._steer(turn_directive("verdict"))
            return

        interrupted = self._took_interruption()
        quality = classify_answer(transcript)
        if quality == "accepted" and _looks_like_echo(
            transcript, self._last_interviewer_text
        ):
            # Long enough to pass the word gate, because it is the
            # interviewer's own sentence: the student's speakers played the
            # question into their microphone. Counting it would advance the
            # arc on an answer nobody gave.
            quality = "echo"
        counted = quality == "accepted"
        self._emit_turn(
            _SENDER_STUDENT,
            transcript,
            turn_id,
            status=status,
            quality=quality,
            counted=counted,
        )
        if not counted:
            # No clarification directive, and that is the documented difference
            # from the relay: the model is already answering, so a second
            # directive would produce a second question. The turn is recorded
            # as what it was and the arc does not move on it.
            #
            # ONE EXCEPTION, held like every note: the question that this
            # non-answer INTERRUPTED. Nova abandoned it and the browser
            # flushed it, so unless somebody asks, it is gone -- the student
            # is left facing whatever the model made of a cough. `resume`
            # tells the model to finish the question it was asking, and it
            # goes upstream in the gap after the reply in flight. A skip is
            # deliberately not resumed: the student asked for the next
            # question and the briefing has the model giving them one.
            if self._report_requested:
                # The interview is over and the student can no longer hear:
                # the only turn left is the scorecard tool call, and a resume
                # note here would put a spoken turn in front of it.
                return
            if quality == "echo" or (interrupted and quality != "skipped"):
                self._log.info(
                    "Interruption was not an answer (%s); asking the model to "
                    "resume the question",
                    quality,
                )
                await self._steer(turn_directive("resume"))
            return

        self._answers_accepted += 1
        spec = self._machine.specialization
        if spec is None:
            # The generic interview that predates the matrix has no arc to
            # advance and no verdict to reach, exactly as on the hosted relay.
            return
        if not self._machine.student_answered():
            return
        if self._machine.phase is InterviewPhase.WRAP_UP:
            # Announced when the verdict goes up, not now -- see
            # `_wrap_up_announced`.
            self._awaiting_candidate_questions = True
            await self._steer(turn_directive("invite_questions"))
            return
        await self._announce_phase()
        # PROBING and DEEP_DIVE send nothing. The model was briefed on both at
        # the handshake (`build_arc_briefing`) and moves on the rule this
        # machine just applied; a note here would either barge in on the reply
        # in flight or, held, ask a second question after it.

    def _took_interruption(self) -> bool:
        """Was the transcript being handled the one that interrupted the model?

        Consumes the stamp: a marker is answered by exactly one transcript, and
        a stale stamp must not make a later, unrelated short answer look like
        a barge-in. Bounded by _INTERRUPTION_ANSWER_WINDOW_S so a marker whose
        transcript never arrived (Nova heard noise and transcribed nothing at
        all) cannot outlive the turn it belonged to.
        """
        stamped = self._interrupted_at
        self._interrupted_at = None
        if stamped is None:
            return False
        return time.monotonic() - stamped <= _INTERRUPTION_ANSWER_WINDOW_S

    # -- the interviewer's turn --------------------------------------------

    async def _begin_response(self) -> None:
        """One model turn opens. The browser starts a fresh PCM stream.

        Reached from the reply's first SPECULATIVE text or audio block
        (`_on_reply_content`), and from the one completionStart Nova sends
        ahead of its first reply; the guard makes a second call a no-op.
        Between here and `_end_response` a control note must not go upstream
        -- see `_steer`.

        `response.created` is what makes the client drop any odd-byte carry left
        from the previous response: prepending a stranded byte to a new stream
        byte-misaligns the whole of it into white noise.
        """
        if self._response_open:
            return
        self._response_open = True
        self._assistant_final = []
        self._assistant_spoke = False
        self._reply_has_content = False
        self._reply_audio_started_at = None
        self._reply_audio_last_at = None
        self._reply_audio_bytes = 0
        self._reply_audio_ended = False
        self._reply_final_closed = False
        self._final_deadline = None
        self._reply_follows_verdict = self._verdict_sent
        await self._send_control({"type": "response.created"})

    async def _end_response(self) -> None:
        """One interviewer turn closes: record what was said, tell the browser.

        `response.done` is also what moves the client into "Writing your
        report…" once the phase is wrap_up, so it must be sent for every turn
        including one that produced no audio at all.
        """
        spoken = " ".join(part.strip() for part in self._assistant_final if part.strip())
        planned = " ".join(
            part.strip() for part in self._assistant_speculative if part.strip()
        )
        was_open = self._response_open
        self._response_open = False
        self._final_deadline = None
        self._assistant_final = []
        self._assistant_speculative = []
        self._interrupted_this_turn = False
        if spoken or planned:
            # Both, when both exist: a turn cut mid-way has FINAL text for the
            # sentences that finished and speculative text for the one the
            # student's speakers were playing when it was cut. Echo is judged
            # on the longest shared RUN of words, so the duplication of the
            # finished sentences costs nothing.
            # On an ordinary reply the two are the same sentence, kept once.
            if spoken and planned and spoken != planned:
                self._last_interviewer_text = f"{spoken} {planned}"
            else:
                self._last_interviewer_text = spoken or planned
        if was_open and (spoken or self._assistant_spoke):
            self._emit_turn(
                _SENDER_INTERVIEWER,
                spoken,
                f"nova-{self._session_id or self._conn_id}-{self._turn_seq + 1}",
                status="not_applicable",
                quality=None,
            )
        await self._send_control({"type": "response.done"})
        if self._report_requested and not self._report_settled and spoken:
            # The model was asked for the scorecard and TALKED instead of
            # calling the tool. Rather than lose the report, the spoken text is
            # parsed exactly as the relay parses its text-only response: a model
            # that read the JSON aloud has still produced it, and the student is
            # owed the scorecard either way.
            salvaged = _parse_report(spoken)
            if salvaged is not None:
                self._report_raw = spoken
                await self._finish_report(salvaged, "ok")

    # -- steering ----------------------------------------------------------

    async def _steer(self, body: str) -> None:
        """One control note, held back while a model turn is in flight.

        "In flight" is `_holding`: from the moment the student's words arrive,
        through the reply they provoke, until that reply's FINAL text closes.
        A beat decided on a transcript therefore always waits. The directive
        still governs what happens next; it simply arrives between turns
        rather than on top of one. See `_pending_notes` for what injecting
        mid-turn does to the audio, and note every directive sent this way
        says what to do with a question the model has just asked and not had
        answered.
        """
        if self._holding():
            self._pending_notes.append(body)
            return
        await self._send_note(body)

    async def _drain_pending_notes(self) -> bool:
        """Send what was held back. True when anything went, which means a new
        interviewer turn is about to open because of it."""
        if not self._pending_notes:
            return False
        held, self._pending_notes = self._pending_notes, []
        for note in held:
            await self._send_note(note)
        return True

    async def _send_note(self, body: str) -> None:
        """A held or immediate beat, on its way upstream.

        The verdict is the one with consequences beyond itself: the reply that
        follows it is the verdict, after which the scorecard is asked for, and
        it is the moment the browser is told the interview is in its wrap-up.
        """
        await self._inject(body)
        if body != turn_directive("verdict"):
            return
        self._verdict_sent = True
        if self._machine.phase is InterviewPhase.WRAP_UP and not self._wrap_up_announced:
            self._wrap_up_announced = True
            await self._announce_phase()

    async def _inject(self, body: str) -> None:
        """One control note into the live session, as cross-modal text input.

        THE ONLY WAY TO STEER A NOVA SESSION MID-INTERVIEW. Its system prompt is
        set once at the handshake and there is no update event, so what changes
        — the phase directive, the invitation, the verdict — arrives as text on
        the same prompt. `interactive: true` with role USER is the documented
        shape for text during an active voice session.

        Every caller passes a FIXED string composed in app/interview_matrix.py.
        Nothing the student said and nothing from their record is ever composed
        in here; that is the same line the relay draws, and it is drawn as a
        shape rather than a check because the risk is what the next editor puts
        in an instruction, not what this one did.
        """
        self._note_seq += 1
        name = f"{self._prompt_name}-note-{self._note_seq}"
        await self._upstream_send(
            {
                "event": {
                    "contentStart": {
                        "promptName": self._prompt_name,
                        "contentName": name,
                        "type": "TEXT",
                        "interactive": True,
                        "role": "USER",
                        "textInputConfiguration": {"mediaType": "text/plain"},
                    }
                }
            }
        )
        await self._upstream_send(
            {
                "event": {
                    "textInput": {
                        "promptName": self._prompt_name,
                        "contentName": name,
                        "content": _control_note(body),
                    }
                }
            }
        )
        await self._upstream_send(
            {
                "event": {
                    "contentEnd": {"promptName": self._prompt_name, "contentName": name}
                }
            }
        )

    async def _announce_phase(self) -> None:
        spec = self._machine.specialization
        self._log.info("Phase -> %s", self._machine.phase.value)
        await self._send_control(
            {
                "type": "reep.phase",
                "phase": self._machine.phase.value,
                "specialization": spec.label if spec is not None else None,
            }
        )

    async def _force_wrap_up(self) -> None:
        """The clock, not the arc, ended the questioning.

        Called once, early enough that the verdict and the scorecard both fit
        inside the connection's remaining life. It goes STRAIGHT to the verdict
        and skips the "any questions for us?" beat: a real interviewer who is
        out of time closes, and asking a question there is only ever answered
        by the socket shutting.

        Held, like every other note, while a reply is in flight: an interviewer
        who is out of time finishes the sentence and then closes, rather than
        cutting itself off mid-word and having the browser's queue flushed
        under it. The wait is bounded by one reply, and _WRAP_UP_RESERVE_S is
        sized for the verdict and the scorecard on top of that.
        """
        spec = self._machine.specialization
        if spec is None or self._verdict_requested:
            return
        self._awaiting_candidate_questions = False
        # The browser hears of WRAP_UP when the verdict goes (`_send_note`).
        self._machine.force_wrap_up()
        self._verdict_requested = True
        self._log.info("Forcing the wrap-up: the session cap is close")
        await self._steer(turn_directive("verdict"))

    # -- the scorecard -----------------------------------------------------

    async def _request_report(self) -> None:
        """Ask for the scorecard as a TOOL CALL, once, after the verdict is spoken.

        Never as speech: Nova speaks everything it generates, so a scorecard
        asked for as text would be read aloud to a student who has just been
        told the interview is over. REPORT_DIRECTIVE is included VERBATIM — it
        is the same bar the other two engines score against, and a paraphrase
        here would quietly make the Nova interview a different assessment.
        """
        if self._report_requested or self._report_settled:
            return
        self._report_requested = True
        self._report_deadline = (
            time.monotonic() + settings.interview_report_timeout_ms / 1000
        )
        await self._inject(
            "The interview is over and the student can no longer hear you. Do "
            "not speak, and do not say goodbye again. Call the "
            f"{_SCORECARD_TOOL_NAME} tool exactly once with your assessment.\n\n"
            f"{REPORT_DIRECTIVE}"
        )

    async def _settle_tool_use(self) -> None:
        """The tool block closed: parse the arguments, answer Bedrock, settle.

        Parsed with the relay's own `_parse_report`, so a Nova scorecard and an
        OpenAI one are validated, clamped and bounded identically — including
        the rule that a missing score stays None rather than becoming a zero a
        mentor would read as a judgement.
        """
        call = self._pending_tool
        self._pending_tool = None
        if call is None:
            return
        content = call.get("content")
        raw = content if isinstance(content, str) else json.dumps(content or {})
        self._report_raw = raw[:16_000]
        report = _parse_report(raw)

        # Answer the model before ending, so the last thing on the stream is a
        # completed tool exchange rather than a half-open block. Best-effort:
        # the interview is over and a failure here changes nothing the student
        # sees.
        tool_use_id = str(call.get("toolUseId") or "")
        if tool_use_id:
            name = f"{self._prompt_name}-toolresult-{self._note_seq}"
            try:
                await self._upstream_send(
                    {
                        "event": {
                            "contentStart": {
                                "promptName": self._prompt_name,
                                "contentName": name,
                                "interactive": False,
                                "type": "TOOL",
                                "role": "TOOL",
                                "toolResultInputConfiguration": {
                                    "toolUseId": tool_use_id,
                                    "type": "TEXT",
                                    "textInputConfiguration": {
                                        "mediaType": "text/plain"
                                    },
                                },
                            }
                        }
                    }
                )
                await self._upstream_send(
                    {
                        "event": {
                            "toolResult": {
                                "promptName": self._prompt_name,
                                "contentName": name,
                                "content": json.dumps({"status": "recorded"}),
                            }
                        }
                    }
                )
                await self._upstream_send(
                    {
                        "event": {
                            "contentEnd": {
                                "promptName": self._prompt_name,
                                "contentName": name,
                            }
                        }
                    }
                )
            except Exception as exc:  # noqa: BLE001 - the scorecard is already here
                self._log.warning("Could not acknowledge the scorecard tool: %s", exc)

        await self._finish_report(report, "ok" if report is not None else "unparseable")

    async def _finish_report(self, report: dict[str, Any] | None, status: str) -> None:
        """Send reep.report and end the session — always with close 1000.

        Every failure here is a payload, never a close code: the interview
        COMPLETED and only the scorecard did not, and a dedicated error code
        would make a successful interview read as a failure in the client, in
        the logs and in the record. Settled once — the deadline and a late tool
        call can both arrive, and the second finds this already set.
        """
        if self._report_settled:
            return
        self._report_settled = True
        self._report_deadline = None
        self._report_status = status
        if status == "ok":
            self._log.info(
                "Scorecard ready (overall=%s)", report.get("overall") if report else None
            )
        else:
            self._log.warning("No scorecard for this interview: %s", status)

        # AWAITED, unlike a turn write: this row happens once, after the
        # interview is over, so waiting costs the student nothing and losing it
        # costs the record everything. Left to the fire-and-forget path it would
        # be the last write scheduled and so the first one the drain cancels.
        await self._write_record(
            self._on_report,
            _ReportRecord(
                status=status,
                report=report,
                raw_response=self._report_raw,
                model=settings.nova_sonic_model.strip(),
            ),
            "the interview evaluation",
        )
        await self._send_control(
            {
                "type": "reep.report",
                "available": status == "ok",
                "reason": None if status == "ok" else status,
                "report": report if status == "ok" else None,
            }
        )
        raise _SessionEnded(_CLOSE_OK, "Interview complete")

    # -- the guardrails ----------------------------------------------------

    def _effective_cap(self) -> float:
        """The real session length: the shorter of our cap and Bedrock's.

        A Nova stream is closed by the service after 8 minutes. Running the
        relay's 15-minute cap against it produces the exact failure the phase
        machine exists to prevent — an interview cut off mid-answer with no
        verdict and no scorecard — so the wall wins whenever it is nearer.
        """
        ours = float(self._max_seconds or settings.interview_max_seconds)
        return min(
            ours,
            max(60.0, float(settings.nova_sonic_connection_seconds) - _CONNECTION_MARGIN_S),
        )

    async def _watchdog(self) -> None:
        """Caps, heartbeat and the two deadlines, on one coarse timer.

        Raising is the mechanism: the TaskGroup cancels both pumps in response,
        which is what guarantees no task outlives the session.
        """
        cap = self._effective_cap()
        idle_cap = float(settings.interview_idle_seconds)
        wrap_at = max(0.0, cap - _WRAP_UP_RESERVE_S)

        while True:
            try:
                async with asyncio.timeout(_WATCHDOG_INTERVAL_S):
                    await self._stop_requested.wait()
            except TimeoutError:
                pass
            else:
                raise _SessionEnded(*self._stop_outcome)

            now = time.monotonic()
            elapsed = now - self._started_at

            if now - self._heartbeat_at >= _HEARTBEAT_WRITE_INTERVAL_S:
                # Stamped BEFORE the write is scheduled: a database answering
                # slowly must not pile a second and third heartbeat behind the
                # first.
                self._heartbeat_at = now
                self._beat()

            if self._report_deadline is not None and now >= self._report_deadline:
                # The model never called the tool. The transcript is already
                # saved; the evaluation row says why there is no scorecard.
                await self._finish_report(None, "timeout")

            await self._check_turn_deadlines(now)

            if elapsed >= cap:
                raise _SessionEnded(_CLOSE_SESSION_CAP, "Session cap reached")
            if now - self._last_audio_at >= idle_cap:
                raise _SessionEnded(
                    _CLOSE_IDLE, f"No audio received for {int(idle_cap)}s"
                )
            if elapsed >= wrap_at:
                await self._force_wrap_up()

    async def _check_turn_deadlines(self, now: float) -> None:
        """The two waits Nova's stream can leave open, checked once a tick."""
        deadline = self._final_deadline
        if deadline is None and self._reply_audio_started_at is not None:
            # Audio arrived and no block of it has ended with a stop reason.
            # Once that speech has played, and a second grace has passed, the
            # reply is over whatever it failed to say.
            deadline = self._final_text_deadline("") + _FINAL_TEXT_GRACE_S
        if self._response_open and deadline is not None and now >= deadline:
            # The reply's audio has played and its FINAL text never came.
            # Closing it is what lets a held beat go.
            self._log.warning(
                "An interviewer reply sent no final transcript; closing it "
                "without one"
            )
            await self._on_reply_finished()
            return
        # A note held behind words Nova never answered goes once those words
        # are stale (`_holding`). A no-op otherwise.
        await self._take_next_beat()

    # -- downstream --------------------------------------------------------

    async def _send_control(self, payload: dict[str, Any]) -> None:
        if self._ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self._ws.send_text(json.dumps(payload))
        except (RuntimeError, WebSocketDisconnect):
            # The browser went away between the state check and the send. The
            # disconnect is already on its way to the client pump, which ends
            # the session with the right code.
            return

    async def _send_ready(self) -> None:
        spec = self._machine.specialization
        await self._send_control(
            {
                "type": "reep.ready",
                "session_id": self._session_id or self._conn_id,
                "conn_id": self._conn_id,
                "audio": {
                    "format": "pcm16",
                    "sample_rate": _CLIENT_RATE_HZ,
                    "chunk_ms": _CHUNK_MS,
                },
                "limits": {
                    # The EFFECTIVE cap, not INTERVIEW_MAX_SECONDS: the client
                    # draws its countdown and its two-minute warning from this
                    # number, and a clock that promises fifteen minutes on an
                    # eight-minute stream is worse than no clock.
                    "session_max_seconds": int(self._effective_cap()),
                    "idle_max_seconds": settings.interview_idle_seconds,
                },
                "specialization": spec.label if spec is not None else None,
                "phase": self._machine.phase.value,
                # The client does not branch on this; it is here so a support
                # conversation can start from the transcript instead of a guess.
                "engine": "nova",
            }
        )

    async def _upstream_send(self, event: dict[str, Any]) -> None:
        if self._upstream is None:
            return
        await self._upstream.send(event)

    # -- persistence -------------------------------------------------------

    def _emit_turn(
        self,
        sender: str,
        text: str,
        provider_turn_id: str,
        *,
        status: str,
        quality: str | None,
        counted: bool = False,
    ) -> None:
        """Persist one FINAL turn. FIRE-AND-FORGET, by contract.

        A failed write must never end an interview that is otherwise going fine:
        the student is mid-sentence and cannot be helped by an exception. The
        price is the silent failure the AGENTS.md runbook exists to catch — a
        call that sounds perfect and saves nothing — so a failed write is logged
        WITH ITS CAUSE and the (emitted, persisted) pair on the session row
        makes the gap visible.

        A BLANK transcript is still a turn. "The transcriber heard nothing" is a
        fact a mentor may need, and dropping it is how turns_emitted and
        turns_persisted quietly stop meaning anything.
        """
        if self._on_turn is None:
            return
        self._turn_seq += 1
        record = _TurnRecord(
            seq=self._turn_seq,
            # The phase the turn HAPPENED in, read at emit time: it is the one
            # thing a shared `messages` row can never carry, and it is only true
            # before the next tick.
            phase=self._machine.phase.value,
            transcription_status=status,
            answer_quality=quality,
            counted_as_answer=counted,
            # Nova reports what it ACTUALLY said, so an interrupted turn is
            # recorded complete-as-spoken rather than flagged partial; a student
            # utterance arrives as one committed segment either way.
            is_partial=False,
        )
        if sender == _SENDER_STUDENT:
            self._turns_user += 1
        else:
            self._turns_assistant += 1
        task = asyncio.create_task(
            self._run_turn_write(sender, text, provider_turn_id, record),
            name=f"nova-write-{self._conn_id}",
        )
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def _run_turn_write(
        self, sender: str, text: str, provider_turn_id: str, record: _TurnRecord
    ) -> None:
        on_turn = self._on_turn
        if on_turn is None:  # pragma: no cover - guarded by _emit_turn
            return
        try:
            # to_thread, never a direct call: the writer is synchronous
            # SQLAlchemy and this loop carries every other live interview's
            # audio.
            await asyncio.to_thread(on_turn, sender, text, provider_turn_id, record)
        except _TurnWriteRefused as exc:
            # The chat thread this interview writes into was cleared or purged
            # mid-call, so every remaining turn would be refused the same way.
            # 1000 rather than an error code: nothing failed — the student
            # deliberately cleared the thread.
            self._log.error(
                "Interview turn refused and ENDING the interview: sender=%s turn=%s: %s",
                sender,
                provider_turn_id,
                exc,
            )
            self.request_stop(_CLOSE_OK, "Conversation cleared")
        except asyncio.CancelledError:
            self._log.warning(
                "Interview turn not persisted (cancelled at teardown): sender=%s turn=%s",
                sender,
                provider_turn_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a dropped turn never ends a call
            self._log.error(
                "Dropped interview turn: sender=%s turn=%s: %s",
                sender,
                provider_turn_id,
                exc,
            )
        else:
            self._turns_persisted += 1

    async def _drain_writes(self) -> None:
        """Let in-flight turn writes finish, bounded, before the session goes away."""
        if not self._writes:
            return
        pending = tuple(self._writes)
        try:
            async with asyncio.timeout(_TURN_WRITE_DRAIN_S):
                await asyncio.gather(*pending, return_exceptions=True)
        except TimeoutError:
            self._log.warning(
                "%d interview turn write(s) did not finish within %.0fs",
                sum(1 for task in pending if not task.done()),
                _TURN_WRITE_DRAIN_S,
            )
            for task in pending:
                task.cancel()

    async def _write_record(
        self, hook: Callable[[Any], None] | None, payload: Any, what: str
    ) -> None:
        """One AWAITED record write, bounded, whose failure never changes the outcome."""
        if hook is None:
            return
        try:
            async with asyncio.timeout(_RECORD_WRITE_TIMEOUT_S):
                await asyncio.to_thread(hook, payload)
        except TimeoutError:
            # Before Exception, deliberately: since 3.11 TimeoutError IS an
            # OSError and the clause below would name the wrong fault.
            self._log.error("Timed out after %.0fs writing %s", _RECORD_WRITE_TIMEOUT_S, what)
        except Exception as exc:  # noqa: BLE001 - the interview's outcome is fixed
            self._log.error("Could not write %s: %s", what, exc)

    def _beat(self) -> None:
        """Stamp heartbeat_at, on a thread, fire-and-forget.

        Also the channel a consent revocation comes back through (4014): the
        router's heartbeat re-reads the grant this interview opened under and
        calls request_stop when it has gone away.
        """
        hook = self._on_heartbeat
        if hook is None:
            return

        async def run() -> None:
            try:
                await asyncio.to_thread(hook)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a missed beat is not fatal
                self._log.warning("Interview heartbeat not written: %s", exc)

        task = asyncio.create_task(run(), name=f"nova-heartbeat-{self._conn_id}")
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    # -- the recording -----------------------------------------------------

    def _capture(self, track: str, pcm: bytes) -> None:
        """Hand one frame to the recorder, if there is one. Cannot fail.

        None in every default deployment: recording needs
        INTERVIEW_RECORDING_ENABLED **and** a live `scope_store_audio` grant,
        and app/interview_audio.py checks both before a recorder exists at all.
        """
        recorder = self._recorder
        if recorder is None:
            return
        try:
            recorder.feed(track, pcm)
        except Exception as exc:  # noqa: BLE001 - audio is never worth the call
            self._log.warning("Interview audio frame dropped: %s", exc)

    async def _close_recorder(self) -> Any:
        """Flush and close the recording; return what was kept, or None.

        A timeout does NOT mean nothing was recorded — it means the disk is
        slow, and the bytes are already there. Reporting nothing would leave a
        student's voice on disk with no row pointing at it, which is the one
        state retention cannot clean up.
        """
        recorder = self._recorder
        if recorder is None:
            return None
        try:
            async with asyncio.timeout(_AUDIO_CLOSE_TIMEOUT_S):
                result = await recorder.aclose()
            if result.recorded:
                self._log.info(
                    "Interview audio stored: path=%s bytes=%d duration=%.0fs truncated=%s",
                    result.path,
                    result.total_bytes,
                    result.duration_ms / 1000,
                    result.truncated,
                )
            return result
        except TimeoutError:
            self._log.error(
                "Interview audio did not finish closing within %.0fs; recording "
                "what is believed to be on disk so it stays deletable",
                _AUDIO_CLOSE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - teardown never raises
            self._log.error("Could not close the interview audio: %s", exc)
        try:
            return recorder.snapshot()
        except Exception as exc:  # noqa: BLE001 - snapshot() only reads counters
            self._log.error("Interview audio state is unreadable: %s", exc)
            return None

    # -- the interview record ----------------------------------------------

    async def _finalize_session(self, code: int, reason: str) -> None:
        """LAYER 1 of three: this engine closing its own record.

        A `running` row that is never closed is worse than no row — it is a
        record that lies. The router's `finally` backstop and retention's
        orphan sweeper are the other two layers, and all three are idempotent
        against each other through one `AND status = 'running'` predicate.

        `status` is not a lookup on the close code alone: 1000 covers both "the
        scorecard was delivered" and "the student pressed End at minute three",
        and calling the second one `completed` would put a finished-looking
        interview with no verdict in front of a mentor.
        """
        if self._finalized:
            return
        self._finalized = True

        # Before the early return on a missing hook: the files must be closed
        # even when nothing is listening for the result, or a session ends with
        # two open handles and an unpatched RIFF header.
        audio = await self._close_recorder()
        if self._on_finalize is None:
            return

        if self._report_settled:
            status = "completed"
        elif code in (_CLOSE_OK, _CLOSE_GOING_AWAY, _CLOSE_IDLE, _CLOSE_SESSION_CAP):
            status = "abandoned"
        else:
            status = "failed"

        await self._write_record(
            self._on_finalize,
            _SessionOutcome(
                status=status,
                close_code=code,
                terminal_reason=f"{code} {reason}",
                # The phase the interview STOPPED in: machine.end() is
                # deliberately not called first, because 'ended' on every row
                # would erase the one fact this column exists for.
                final_phase=self._machine.phase.value,
                answers_accepted=self._answers_accepted,
                turns_emitted=self._turns_user + self._turns_assistant,
                turns_persisted=self._turns_persisted,
                # Bedrock's own session id. It is what an AWS support case is
                # opened with, and there is no other way back from this row to
                # the stream that produced it.
                upstream_session_id=self._session_id,
                # None ⇒ the scorecard was never attempted (the interview ended
                # before the wrap-up), which the writer records as an evaluation
                # row saying `unavailable`. A missing row says nothing at all.
                report_status=self._report_status,
                audio_recorded=bool(audio and audio.recorded),
                audio_path=audio.path if audio and audio.recorded else None,
                audio_bytes=audio.total_bytes if audio and audio.recorded else None,
                audio_duration_ms=(audio.duration_ms if audio and audio.recorded else None),
                audio_truncated=bool(audio and audio.truncated),
            ),
            "the interview record",
        )
