"""The Nova 2 Sonic engine: the arc, the record, and what goes on the wire.

app/interview_nova.py runs the same interview as the relay on Amazon Nova 2
Sonic. The model owns the TURN there (Nova detects the end of the student's
speech and answers on its own; there is no `response.create` to withhold), so
what this engine owns is the PHASE — and that is precisely what these tests
pin, because it is the half a reader cannot check by listening to a call.

NO DATABASE, NO SOCKET AND NO AWS: a fake browser socket that records what was
forwarded, a fake upstream that records the event documents that would have
gone to Bedrock, and `on_turn=None` so the persistence path is never entered.
What is asserted is what went ON THE WIRE and what the state machine did.

Each test is named for the failure it prevents, because that is the only useful
thing to know when one of them goes red in two years.
"""

import asyncio
import base64
import inspect
import json

import pytest
from fastapi import WebSocketDisconnect
from fastapi.websockets import WebSocketState

from app import interview_nova as nova
from app.config import Settings, settings
from app.interview_matrix import (
    DEFAULT_NOVA_VOICE,
    KNOWN_NOVA_VOICES,
    SPECIALIZATIONS,
    InterviewPhase,
    build_instructions,
    nova_voice_for,
    phase_directive,
)
from app.interview_core import _CLOSE_OK, _INTERVIEWER_PERSONA, _SessionEnded
from app.interview_local import LocalSession
from app.interview_nova import NovaSonicSession


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBrowser:
    """Starlette's WebSocket, reduced to what this engine calls."""

    def __init__(self) -> None:
        self.control: list[dict] = []
        self.audio: list[bytes] = []
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, text: str) -> None:
        self.control.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)

    def of_type(self, kind: str) -> list[dict]:
        return [frame for frame in self.control if frame.get("type") == kind]


class _FakeUpstream:
    """Bedrock, reduced to send(). Every event document is kept, parsed."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, event: dict) -> None:
        self.sent.append(event)

    @property
    def notes(self) -> list[str]:
        """Every control note injected into the session, in order."""
        return [
            (event["event"]["textInput"]["content"])
            for event in self.sent
            if "textInput" in event.get("event", {})
        ]


class _Nova(NovaSonicSession):
    """The real engine, with the persistence call captured instead of scheduled."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.emitted: list[tuple[str, str, str, str | None, bool]] = []

    def _emit_turn(self, sender, text, provider_turn_id, *, status, quality, counted=False):
        self.emitted.append((sender, text, provider_turn_id, quality, counted))
        super()._emit_turn(
            sender, text, provider_turn_id, status=status, quality=quality, counted=counted
        )


def make_session(spec_key: str | None = "hr") -> tuple[_Nova, _FakeUpstream, _FakeBrowser]:
    browser = _FakeBrowser()
    session = _Nova(
        browser,
        "c0ffee123456",
        on_turn=None,
        specialization=SPECIALIZATIONS[spec_key] if spec_key else None,
    )
    upstream = _FakeUpstream()
    session._upstream = upstream
    return session, upstream, browser


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Sequence helpers — the event shapes Bedrock actually sends
# ---------------------------------------------------------------------------

_FINAL = json.dumps({"generationStage": "FINAL"})
_SPECULATIVE = json.dumps({"generationStage": "SPECULATIVE"})

_GOOD_ANSWER = "I led the campus fintech club and grew it to eighty members"


async def interviewer_turn(session, cid: str = "a1", said: str = "Tell me about yourself.") -> None:
    """One complete interviewer reply, as Nova 2 Sonic sends it mid-session.

    Kept beside `spoken_reply` for the tests that read better with it; the two
    are one sequence now, because Nova sends no completionStart per reply."""
    await spoken_reply(session, cid, said)


async def exchange(session, index: int, transcript: str = _GOOD_ANSWER) -> None:
    """One answered turn IN THE ORDER NOVA 2 SONIC SENDS IT (measured
    2026-10-29): the student's words as a USER block ending PARTIAL_TURN, then
    the interviewer's reply. No completionStart and no completionEnd -- Nova
    sends one completionStart for the whole session and none per turn."""
    await student_says(session, f"u{index}", transcript)
    await spoken_reply(session, f"a{index}")


async def reply_starts(session, cid: str, said: str = "Tell me about yourself.") -> None:
    """The first block of a reply: the SPECULATIVE text, which is what Nova
    sends first. It is the moment the student's answer is judged."""
    await session._on_upstream_event(
        {
            "event": {
                "contentStart": {
                    "contentId": f"{cid}-plan",
                    "type": "TEXT",
                    "role": "ASSISTANT",
                    "additionalModelFields": _SPECULATIVE,
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"textOutput": {"contentId": f"{cid}-plan", "content": said}}}
    )
    await session._on_upstream_event(
        {
            "event": {
                "contentEnd": {
                    "contentId": f"{cid}-plan",
                    "type": "TEXT",
                    "stopReason": "PARTIAL_TURN",
                }
            }
        }
    )


async def reply_speaks(session, cid: str) -> None:
    """The reply's audio block, ending END_TURN."""
    await session._on_upstream_event(
        {"event": {"contentStart": {"contentId": cid, "type": "AUDIO", "role": "ASSISTANT"}}}
    )
    await session._on_upstream_event(
        {
            "event": {
                "audioOutput": {
                    "contentId": cid,
                    "content": base64.b64encode(b"\x01\x00" * 32).decode("ascii"),
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"contentEnd": {"contentId": cid, "type": "AUDIO", "stopReason": "END_TURN"}}}
    )


async def reply_ends(session, cid: str, said: str = "Tell me about yourself.") -> None:
    """The reply's FINAL text, which Nova sends once the speech has played.
    Its contentEnd is the end of the reply."""
    await session._on_upstream_event(
        {
            "event": {
                "contentStart": {
                    "contentId": f"{cid}-text",
                    "type": "TEXT",
                    "role": "ASSISTANT",
                    "additionalModelFields": _FINAL,
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"textOutput": {"contentId": f"{cid}-text", "content": said}}}
    )
    await session._on_upstream_event(
        {
            "event": {
                "contentEnd": {
                    "contentId": f"{cid}-text",
                    "type": "TEXT",
                    "stopReason": "END_TURN",
                }
            }
        }
    )


async def spoken_reply(session, cid: str, said: str = "Tell me about yourself.") -> None:
    """One whole interviewer reply: SPECULATIVE text (PARTIAL_TURN), audio
    (END_TURN), FINAL text (END_TURN) -- the three blocks, in that order, that
    Nova 2 Sonic sent for every reply when it was measured."""
    await reply_starts(session, cid, said)
    await reply_speaks(session, cid)
    await reply_ends(session, cid, said)


async def student_says(session, cid: str, transcript: str) -> None:
    """One USER transcript block. It ends PARTIAL_TURN, as Nova's do: a block
    is not an answer, and the answer is judged when the reply starts."""
    await session._on_upstream_event(
        {
            "event": {
                "contentStart": {
                    "contentId": cid,
                    "type": "TEXT",
                    "role": "USER",
                    "additionalModelFields": _FINAL,
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"textOutput": {"contentId": cid, "content": transcript}}}
    )
    await session._on_upstream_event(
        {"event": {"contentEnd": {"contentId": cid, "type": "TEXT", "stopReason": "PARTIAL_TURN"}}}
    )


# ---------------------------------------------------------------------------
# It is a drop-in
# ---------------------------------------------------------------------------


class TestItIsADropIn:
    """The router swaps three engines on one setting; they must be interchangeable."""

    def test_the_constructor_matches_the_other_engine(self):
        """Every keyword the router passes must exist on every engine.

        A missing one is a TypeError at the moment a student presses Start,
        which is the worst possible time to find out. Checked against the LOCAL
        engine because it is the other implementation of
        interview_core.InterviewEngine — a Protocol cannot pin constructor
        keywords, so this is what does.
        """
        other = set(inspect.signature(LocalSession.__init__).parameters)
        assert other == set(inspect.signature(NovaSonicSession.__init__).parameters)

    def test_run_and_request_stop_exist_with_the_same_shape(self):
        assert inspect.iscoroutinefunction(NovaSonicSession.run)
        stop = inspect.signature(NovaSonicSession.request_stop)
        assert list(stop.parameters) == ["self", "code", "reason"]

    def test_the_payload_records_are_the_shared_ones(self):
        """Imported from app/interview_core.py, not redefined — a parallel
        definition drifts silently."""
        from app import interview_core

        assert nova._TurnRecord is interview_core._TurnRecord
        assert nova._ReportRecord is interview_core._ReportRecord
        assert nova._SessionOutcome is interview_core._SessionOutcome

    def test_the_persona_is_the_shared_one(self):
        """Byte-identical, or two students' scorecards stop being comparable.

        The persona is verbatim product spec. A copy here would let the Nova
        interview drift into assessing something else while every test still
        passed.
        """
        assert nova._INTERVIEWER_PERSONA is _INTERVIEWER_PERSONA


# ---------------------------------------------------------------------------
# Rule 1
# ---------------------------------------------------------------------------


class TestRuleOne:
    def test_the_engine_touches_no_database_and_no_orm(self):
        """The containment is structural, exactly as it is in the relay.

        Bedrock is a remote provider. Nothing that can reach a student's record
        may be importable from the module that holds the uplink.
        """
        source = inspect.getsource(nova)
        for banned in ("from .models", "app.models", "SessionLocal", "app.conversations"):
            assert banned not in source

    def test_student_text_never_enters_the_instructions(self):
        spec = SPECIALIZATIONS["dm"]
        composed = build_instructions(spec, _INTERVIEWER_PERSONA, InterviewPhase.PROBING)
        secret = "my USN is 1MP25MDM01 and my CGPA is 8.4"
        assert secret not in composed

    def test_the_system_prompt_is_the_persona_plus_fixed_blocks(self):
        """The whole uplink this app authors, in one assertion.

        If a future edit composes anything about the student in here, the
        persona is no longer the only thing sent — which is the exact moment
        rule 1's gate starts being required.
        """
        session, _upstream, _browser = make_session("hr")
        composed = session._instructions()
        assert composed.startswith(_INTERVIEWER_PERSONA)
        assert composed.endswith(nova._CONTROL_CHANNEL_NOTE)

    def test_a_control_note_is_marked_as_not_the_student(self):
        """The model must be able to tell a directive from a person.

        Without the prefix a phase directive reads as the student issuing
        instructions, and the interviewer thanks them for it out loud.
        """
        assert nova._control_note("Do the thing").startswith(nova._CONTROL_PREFIX)


# ---------------------------------------------------------------------------
# The arc
# ---------------------------------------------------------------------------


class TestTheArc:
    def test_an_accepted_answer_advances_the_phase_and_the_briefing_steers_the_model(self):
        """The tick AND the steering. Either alone is a broken interview.

        The tick alone records a phase the model was never told about; steering
        alone drives a model whose record says it is still in the opening. On
        this engine the steering for the question phases is the BRIEFING sent
        at the handshake, not a note sent on the answer -- see the next test
        for why a note cannot be made harmless.
        """
        session, upstream, browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1")
            await exchange(session, 1)

        run(scenario())
        assert session._machine.phase is InterviewPhase.PROBING
        assert browser.of_type("reep.phase")[0]["phase"] == "probing"
        assert not any("Probe" in note for note in upstream.notes), (
            "a probing note went upstream: the briefing already carries it, and "
            "a note is a user turn the model answers with a second question"
        )
        assert phase_directive(SPECIALIZATIONS["hr"], InterviewPhase.PROBING) in session._instructions()

    def test_the_question_phases_are_briefed_once_at_the_handshake(self):
        """What the model is told up front, and what it is deliberately not.

        Probing and deep dive are in the system prompt with the word gate's own
        counting rule, so the model's reply to the answer that ticks the phase
        is already the right phase's question. The two stop beats are NOT
        there: a model that decided for itself when the questioning was over
        could decide it one answer before the engine does and then wait for a
        verdict note the engine would never send.
        """
        session, _upstream, _browser = make_session("hr")
        spec = SPECIALIZATIONS["hr"]
        composed = session._instructions()

        assert composed.startswith(_INTERVIEWER_PERSONA)
        assert phase_directive(spec, InterviewPhase.PROBING) in composed
        assert phase_directive(spec, InterviewPhase.DEEP_DIVE) in composed
        assert f"at least {settings.interview_min_answer_words} words" in composed
        assert "any questions for you" not in composed
        assert "closing verdict" not in composed
        assert composed.endswith(nova._CONTROL_CHANNEL_NOTE)

        generic, _upstream, _browser = make_session(None)
        assert "How this interview unfolds" not in generic._instructions()

    def test_a_beat_decided_on_the_transcript_never_goes_onto_the_reply_in_flight(self):
        """THE ORDER NOVA 2 SONIC ACTUALLY SENDS, and the two bugs it explains.

        Measured 2026-10-29: one completionStart for the whole session, no
        completionEnd between replies, and each reply is SPECULATIVE text,
        audio, then -- once the speech has played -- FINAL text. The student's
        words arrive just before the reply, as USER blocks ending PARTIAL_TURN.

        The engine used to treat every USER block as a finished answer and
        wait for a completionEnd that never came, so a beat decided on an
        answer was held for the rest of the interview. Pinned here: a block is
        not judged until the reply starts, the reply is open until its FINAL
        text closes, and a phase tick sends NOTHING, before or after the reply.
        """
        session, upstream, browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1")
            before = len(upstream.notes)

            await student_says(session, "u1", _GOOD_ANSWER)
            # A block is not an answer: nothing is judged yet.
            assert session._machine.phase is InterviewPhase.OPENING
            assert session._holding() is True, "the reply to these words is about to start"

            await reply_starts(session, "a2")
            assert session._response_open is True, "the reply's first block opens it"
            # The arc moved, and the student's screen was told.
            assert session._machine.phase is InterviewPhase.PROBING
            assert browser.of_type("reep.phase")[-1]["phase"] == "probing"
            # Nothing was said to the model while it was composing its reply.
            assert len(upstream.notes) == before, (
                "a directive went upstream mid-turn: that is the barge-in that "
                "cuts the interviewer off and drops the audio"
            )
            assert session._pending_notes == []

            await reply_speaks(session, "a2")
            assert session._response_open is True, (
                "the audio has been SENT, not played: the reply is open until "
                "its FINAL text closes"
            )
            await reply_ends(session, "a2")
            assert session._response_open is False
            assert len(upstream.notes) == before, (
                "a phase note after the reply is a second question on top of "
                "the one the student was just asked"
            )

        run(scenario())

    def test_one_answer_in_several_blocks_is_one_answer(self):
        """THE MISCOUNT OF 2026-10-29: 29 "accepted answers" in one interview.

        Nova ends every USER block PARTIAL_TURN and splits one spoken answer
        into several blocks. Judging each block as an answer moved the arc on
        every one, so interviews went from the opening to the wrap-up in about
        fifty seconds. The answer is everything the student said before the
        interviewer's reply started.
        """
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a0", "Tell me about yourself.")
            await student_says(session, "u1", "I am a final-year commerce student")
            await student_says(session, "u2", "and I led the campus fintech club")
            await student_says(session, "u3", "for two years, growing it to eighty members")
            await spoken_reply(session, "a1")

        run(scenario())
        students = [t for t in session.emitted if t[0] == nova._SENDER_STUDENT]
        assert len(students) == 1
        assert students[0][1] == (
            "I am a final-year commerce student and I led the campus fintech "
            "club for two years, growing it to eighty members"
        )
        assert session._answers_accepted == 1
        assert session._machine.answers == 1
        assert session._machine.phase is InterviewPhase.PROBING
        transcripts = browser.of_type("conversation.item.input_audio_transcription.completed")
        assert len(transcripts) == 1
        assert transcripts[0]["item_id"] == "u1"

    def test_every_reply_is_recorded_without_a_completion_end(self):
        """The other half of the same incident: `answers=29 turns=31/31` was 29
        student turns and TWO interviewer turns, because the interviewer's turn
        was recorded on completionEnd, which Nova 2 Sonic does not send per
        reply. Each reply's FINAL text is what closes and records it now."""
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {"event": {"completionStart": {"completionId": "only-one"}}}
            )
            await spoken_reply(session, "a0", "Welcome. Tell me about yourself.")
            for index in range(1, 4):
                await exchange(session, index)

        run(scenario())
        interviewer = [t for t in session.emitted if t[0] == nova._SENDER_INTERVIEWER]
        students = [t for t in session.emitted if t[0] == nova._SENDER_STUDENT]
        assert len(interviewer) == 4
        assert len(students) == 3
        assert interviewer[0][1] == "Welcome. Tell me about yourself."
        assert len(browser.of_type("response.created")) == 4
        assert len(browser.of_type("response.done")) == 4
        # What echo detection compares against is set on every reply too.
        assert session._last_interviewer_text == "Tell me about yourself."

    def test_the_browser_hears_of_the_wrap_up_with_the_verdict(self):
        """The client reads every `response.done` in wrap_up as "the interview
        is over and the report is being written" and stops inviting speech.
        Announced at the fifth answer, that would fire on the invitation itself
        and tell the student to say nothing at the one moment they are asked
        to speak. So WRAP_UP reaches the browser as the verdict note goes."""
        session, _upstream, browser = make_session("hr")

        def phases() -> list[str]:
            return [c["phase"] for c in browser.of_type("reep.phase")]

        async def scenario():
            for index in range(5):
                await exchange(session, index)
            assert "wrap_up" not in phases()
            # The invitation's own reply: still not the wrap-up for the browser.
            await interviewer_turn(session, "invite", "Do you have any questions for us?")
            assert "wrap_up" not in phases()
            await exchange(session, 99, "No, thank you.")
            assert phases()[-1] == "wrap_up"
            assert phases().count("wrap_up") == 1

        run(scenario())

    def test_a_note_held_behind_words_nova_never_answered_still_goes(self, monkeypatch):
        """Words with no reply (a "thanks" Nova lets pass) must not hold the
        verdict for the rest of the session: once they are stale, the watchdog's
        next beat sends what was held."""
        session, upstream, _browser = make_session("hr")
        clock = [1000.0]
        monkeypatch.setattr(nova.time, "monotonic", lambda: clock[0])

        async def scenario():
            await interviewer_turn(session, "a1")
            await student_says(session, "u1", "thanks")
            session._pending_notes.append(nova.turn_directive("verdict"))
            await session._take_next_beat()
            assert session._pending_notes, "the reply to these words may still start"
            clock[0] += nova._STUDENT_REPLY_WAIT_S + 0.1
            await session._take_next_beat()
            assert session._pending_notes == []
            assert any("closing verdict" in note for note in upstream.notes)

        run(scenario())

    def test_a_reply_whose_final_text_never_comes_is_closed_after_it_has_played(
        self, monkeypatch
    ):
        """The FINAL text is what releases a held beat. A reply that never sends
        one is closed once its audio has had time to play, plus a grace."""
        session, upstream, _browser = make_session("hr")
        clock = [1000.0]
        monkeypatch.setattr(nova.time, "monotonic", lambda: clock[0])

        async def scenario():
            await student_says(session, "u1", "hmm")
            await reply_starts(session, "a1")
            await reply_speaks(session, "a1")  # 64 bytes of 24 kHz audio: ~1 ms
            session._pending_notes.append(nova.turn_directive("resume"))
            deadline = session._final_deadline
            assert deadline == pytest.approx(1000.0 + nova._FINAL_TEXT_GRACE_S, abs=0.01)

            clock[0] = deadline - 0.5
            await session._check_turn_deadlines(clock[0])
            assert session._response_open is True, "still inside the grace"
            assert session._pending_notes

            clock[0] = deadline + 0.1
            await session._check_turn_deadlines(clock[0])
            assert session._response_open is False
            assert session._pending_notes == []
            assert any("finish the question you were asking" in note for note in upstream.notes)

        run(scenario())
        interviewer = [t for t in session.emitted if t[0] == nova._SENDER_INTERVIEWER]
        assert len(interviewer) == 1, "recorded, without the FINAL text it never got"

    def test_a_new_reply_closes_one_that_never_sent_its_final_text(self):
        """If the next reply starts first, the open one is recorded as it is and
        the held notes stay held: the new reply is in flight."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await reply_starts(session, "a1", "First question?")
            await reply_speaks(session, "a1")
            session._pending_notes.append(nova.turn_directive("resume"))
            await student_says(session, "u1", _GOOD_ANSWER)
            await reply_starts(session, "a2", "Second question?")
            assert session._response_open is True
            assert len(session._pending_notes) == 1
            await reply_speaks(session, "a2")
            await reply_ends(session, "a2", "Second question?")
            assert session._pending_notes == []

        run(scenario())
        interviewer = [t for t in session.emitted if t[0] == nova._SENDER_INTERVIEWER]
        assert len(interviewer) == 2
        assert last_student_turn(session)[3] == "accepted"

    def test_a_reply_that_starts_late_still_holds_the_beat_it_provokes(self, monkeypatch):
        """The beat decided on an answer waits for the reply to that answer
        even when Nova took longer than `_STUDENT_REPLY_WAIT_S` to start it:
        sent then, it would land on the reply that is starting."""
        session, upstream, _browser = make_session("hr")
        clock = [1000.0]
        monkeypatch.setattr(nova.time, "monotonic", lambda: clock[0])
        session._awaiting_candidate_questions = True

        async def scenario():
            await student_says(session, "u1", "No questions, thank you.")
            clock[0] += nova._STUDENT_REPLY_WAIT_S + 3
            before = len(upstream.notes)
            await reply_starts(session, "a1")
            assert session._verdict_requested is True
            assert len(upstream.notes) == before
            assert len(session._pending_notes) == 1

        run(scenario())

    def test_the_interruption_marker_does_not_start_a_reply(self):
        """The marker arrives as assistant text. Taken for the start of a new
        reply, it would judge the student's barge-in before Nova had heard it
        all, and leave the real reply judged one exchange late."""
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await reply_starts(session, "a1")
            await reply_speaks(session, "a1")
            await reply_ends(session, "a1")
            await session._on_upstream_event(
                {
                    "event": {
                        "contentStart": {
                            "contentId": "m1",
                            "type": "TEXT",
                            "role": "ASSISTANT",
                            "additionalModelFields": _SPECULATIVE,
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "m1", "content": '{ "interrupted" : true }'}}}
            )
            assert session._response_open is False
            assert len(browser.of_type("response.created")) == 1

        run(scenario())

    def test_a_reply_whose_audio_never_ends_is_still_closed(self, monkeypatch):
        """No stop reason on the audio and no FINAL text: once the speech has
        had time to play, and two graces have passed, the reply is over."""
        session, _upstream, _browser = make_session("hr")
        clock = [1000.0]
        monkeypatch.setattr(nova.time, "monotonic", lambda: clock[0])

        async def scenario():
            await reply_starts(session, "a1")
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await session._on_upstream_event(
                {
                    "event": {
                        "audioOutput": {
                            "contentId": "a1",
                            # one second of 24 kHz, 16-bit mono
                            "content": base64.b64encode(b"\x01\x00" * 24_000).decode("ascii"),
                        }
                    }
                }
            )
            clock[0] = 1000.0 + 1 + 2 * nova._FINAL_TEXT_GRACE_S - 0.5
            await session._check_turn_deadlines(clock[0])
            assert session._response_open is True
            clock[0] = 1000.0 + 1 + 2 * nova._FINAL_TEXT_GRACE_S + 0.1
            await session._check_turn_deadlines(clock[0])
            assert session._response_open is False

        run(scenario())

    def test_the_invitation_is_held_until_the_reply_in_flight_has_finished(self):
        """The stop beats still travel as notes, and a note waits for the gap.

        The fifth accepted answer is judged as the model starts its own next
        reply. The invitation must not land on that reply (barge-in), not when
        its audio has merely been SENT (it is still playing), and must land as
        its FINAL text closes, telling the model to set aside any question it
        just asked. The arc ticks on the answer; the browser is told WRAP_UP
        with the verdict, so the invitation is not read as "writing your
        report".
        """
        session, upstream, browser = make_session("hr")

        async def scenario():
            for index in range(4):
                await exchange(session, index)
            assert session._machine.phase is InterviewPhase.DEEP_DIVE
            before = len(upstream.notes)

            await student_says(session, "u4", _GOOD_ANSWER)
            await reply_starts(session, "a5")
            assert session._machine.phase is InterviewPhase.WRAP_UP
            assert browser.of_type("reep.phase")[-1]["phase"] == "deep_dive"
            assert session._awaiting_candidate_questions is True
            assert len(upstream.notes) == before
            assert len(session._pending_notes) == 1
            assert "any questions for you" in session._pending_notes[0]

            await reply_speaks(session, "a5")
            assert len(upstream.notes) == before, "the audio is still playing"

            await reply_ends(session, "a5")
            assert session._pending_notes == []
            sent = upstream.notes[before:]
            assert any("any questions for you" in note for note in sent)
            assert any("set it aside" in note for note in sent), (
                "the note lands after the model has asked a question of its "
                "own; it must say what to do with it"
            )
            # Releasing a held note is not the moment to ask for the scorecard.
            assert session._report_requested is False

        run(scenario())

    def test_a_held_verdict_does_not_let_the_scorecard_be_asked_for_early(self):
        """The drain runs BEFORE the report check, and suppresses it for that beat.

        The verdict note goes when the reply it was decided under finishes, so
        the NEXT reply is the verdict. Asking for the scorecard when the note
        goes would ask for it before the student has heard a word of the
        verdict. It is asked for when the verdict's own reply has finished.
        """
        session, upstream, browser = make_session("hr")
        session._awaiting_candidate_questions = True

        async def scenario():
            await student_says(session, "u9", "No questions, thank you.")
            await reply_starts(session, "a9")
            assert session._verdict_requested is True
            assert len(session._pending_notes) == 1

            await reply_speaks(session, "a9")
            await reply_ends(session, "a9")
            # The verdict went; the scorecard was NOT asked for on the same beat.
            assert any("closing verdict" in note for note in upstream.notes)
            assert session._report_requested is False

            # The verdict is spoken, and its end is what asks for the scorecard.
            await interviewer_turn(session, "a10", said="Overall, a solid round.")
            assert session._report_requested is True

        run(scenario())

    def test_a_transcript_split_across_events_is_still_one_answer(self):
        """The arc must not race to the wrap-up because ASR arrived in pieces.

        One block can also arrive as several `textOutput` events. Joined, and
        judged once, with the blocks around it.
        """
        session, _upstream, _browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {
                    "event": {
                        "contentStart": {
                            "contentId": "u1",
                            "type": "TEXT",
                            "role": "USER",
                            "additionalModelFields": _FINAL,
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "u1", "content": "I led the campus"}}}
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "u1", "content": " fintech club for two years"}}}
            )
            await session._on_upstream_event(
                {"event": {"contentEnd": {"contentId": "u1", "type": "TEXT", "stopReason": "PARTIAL_TURN"}}}
            )
            await reply_starts(session, "a1")

        run(scenario())
        assert session._answers_accepted == 1
        assert session.emitted[-1][1] == "I led the campus fintech club for two years"

    def test_a_filler_answer_moves_nothing(self):
        """A cough must not advance the interview.

        `classify_answer` is shared with both other engines for this reason: a
        student's arc has to be the same interview whichever one ran it.
        """
        session, upstream, browser = make_session("hr")
        before = len(upstream.notes)

        run(exchange(session, 1, "um, yeah"))

        assert session._machine.phase is InterviewPhase.OPENING
        assert len(upstream.notes) == before
        assert last_student_turn(session)[3] == "filler"
        assert last_student_turn(session)[4] is False

    def test_a_short_answer_is_recorded_but_not_clarified(self):
        """The documented difference from the relay, pinned so it stays deliberate.

        The relay holds the turn and can ask for more detail. Nova has already
        started replying, so a clarification directive here would produce a
        SECOND question on top of the one the student is about to hear.
        """
        session, upstream, _browser = make_session("hr")

        run(exchange(session, 1, "yes it was"))

        assert last_student_turn(session)[3] == "too_short"
        assert not any("too brief" in note for note in upstream.notes)

    def test_the_fifth_answer_invites_questions_before_the_verdict(self):
        """A real interview ends by handing the floor over, then closing.

        Going straight to the verdict is the failure this beat exists to stop:
        the student never gets the one part of an interview they can prepare.
        """
        session, upstream, browser = make_session("hr")

        async def scenario():
            for index in range(5):
                await exchange(session, index)

        run(scenario())
        assert session._machine.phase is InterviewPhase.WRAP_UP
        assert session._awaiting_candidate_questions is True
        assert any("any questions for you" in note for note in upstream.notes)
        assert not any("closing verdict" in note for note in upstream.notes)

    def test_the_reply_to_that_invitation_is_never_word_gated(self):
        """"No, I'm good" is filler to the answer gate and a real answer here.

        Word-gating the final turn would trap the student in a clarification
        loop at the one moment the interview is supposed to end.
        """
        session, upstream, _browser = make_session("hr")

        async def scenario():
            for index in range(5):
                await exchange(session, index)
            await exchange(session, 99, "no thanks")

        run(scenario())
        assert session._verdict_requested is True
        assert any("closing verdict" in note for note in upstream.notes)

    def test_the_scorecard_is_requested_after_the_verdict_is_spoken(self):
        """Not when it is asked for. The student would hear the model talk over itself.

        The request rides on the end of the verdict's reply -- the model has
        stopped speaking -- which is also what makes the client's "Writing
        your report…" honest.
        """
        session, upstream, _browser = make_session("hr")

        async def scenario():
            for index in range(5):
                await exchange(session, index)
            await exchange(session, 99, "no thanks")
            assert session._report_requested is False
            await interviewer_turn(session, "verdict", "You did well. Good luck.")

        run(scenario())
        assert session._report_requested is True
        assert any(nova._SCORECARD_TOOL_NAME in note for note in upstream.notes)

    def test_the_generic_interview_has_no_arc_and_no_verdict(self):
        """No ?specialization= is the pre-matrix interview, on every engine.

        There is no syllabus to steer against and no bar to score to, so the
        phase must not move and no scorecard may be invented.
        """
        session, upstream, _browser = make_session(None)

        async def scenario():
            for index in range(6):
                await exchange(session, index)

        run(scenario())
        assert session._machine.phase is InterviewPhase.OPENING
        assert session._report_requested is False

# ---------------------------------------------------------------------------
# The scorecard
# ---------------------------------------------------------------------------


class TestTheScorecard:
    def test_a_tool_call_becomes_the_report_and_closes_1000(self):
        """It arrives as arguments, never as speech.

        Nova speaks everything it generates, so a scorecard asked for as text
        would be read aloud to the student. The tool call is what keeps it
        silent, and the interview still closes 1000: the interview completed.
        """
        session, _upstream, browser = make_session("hr")
        payload = {
            "overall": 72,
            "communication": 70,
            "domain": 65,
            "structure": 80,
            "strengths": ["clear structure"],
            "improvements": ["quantify results"],
            "drill": "Rehearse two STAR answers out loud",
            "summary": "A solid first attempt.",
        }

        async def scenario():
            await session._on_upstream_event(
                {
                    "event": {
                        "toolUse": {
                            "toolName": nova._SCORECARD_TOOL_NAME,
                            "toolUseId": "tu-1",
                            "content": json.dumps(payload),
                            "contentId": "t1",
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"contentEnd": {"contentId": "t1", "type": "TOOL", "stopReason": "TOOL_USE"}}}
            )

        with pytest.raises(_SessionEnded) as raised:
            run(scenario())
        assert raised.value.code == _CLOSE_OK
        report = browser.of_type("reep.report")[0]
        assert report["available"] is True
        assert report["report"]["overall"] == 72
        assert session._report_status == "ok"

    def test_a_spoken_scorecard_is_salvaged_rather_than_lost(self):
        """A model that reads the JSON aloud has still produced it.

        Degrade, never assert: the student is owed the scorecard even when the
        model ignored the tool it was given.
        """
        session, _upstream, browser = make_session("hr")
        session._report_requested = True
        spoken = json.dumps(
            {
                "overall": 55,
                "communication": 50,
                "domain": 60,
                "structure": 55,
                "strengths": ["engaged"],
                "improvements": ["more detail"],
                "drill": "Practise one metric walkthrough",
                "summary": "Keep going.",
            }
        )

        with pytest.raises(_SessionEnded):
            run(interviewer_turn(session, "verdict", spoken))
        assert browser.of_type("reep.report")[0]["available"] is True

    def test_an_unparseable_scorecard_is_a_payload_not_a_close_code(self):
        """The interview COMPLETED; only the scorecard did not.

        A dedicated error code would make a successful interview read as a
        failure in the client, in the logs and in the record.
        """
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {
                    "event": {
                        "toolUse": {
                            "toolName": nova._SCORECARD_TOOL_NAME,
                            "toolUseId": "tu-1",
                            "content": "sorry, I could not score that",
                            "contentId": "t1",
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"contentEnd": {"contentId": "t1", "type": "TOOL"}}}
            )

        with pytest.raises(_SessionEnded) as raised:
            run(scenario())
        assert raised.value.code == _CLOSE_OK
        assert browser.of_type("reep.report")[0]["available"] is False
        assert session._report_status == "unparseable"

    def test_the_grading_bar_is_the_shared_one(self):
        """REPORT_DIRECTIVE verbatim, or the Nova interview grades differently.

        Two engines that score the same student against different words produce
        two scorecards a mentor cannot compare.
        """
        session, upstream, _browser = make_session("hr")
        run(session._request_report())
        from app.interview_matrix import REPORT_DIRECTIVE

        assert any(REPORT_DIRECTIVE in note for note in upstream.notes)


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


class TestTheDownstreamContract:
    """The browser is not changed for this engine, so the names must match."""

    def test_the_model_s_audio_reaches_the_browser_as_binary(self):
        session, _upstream, browser = make_session("hr")
        run(interviewer_turn(session, "a1"))
        assert browser.audio
        assert b"".join(browser.audio) == b"\x01\x00" * 32

    def test_a_turn_opens_with_response_created_and_ends_with_response_done(self):
        """The client keys its whole state machine off this pair.

        Without `response.created` the player never drops the previous stream's
        odd-byte carry; without `response.done` the wrap-up never becomes
        "Writing your report…".
        """
        session, _upstream, browser = make_session("hr")
        run(interviewer_turn(session, "a1"))
        assert browser.of_type("response.created")
        assert browser.of_type("response.audio.done")
        assert browser.of_type("response.done")

    def test_the_live_caption_is_the_speculative_text(self):
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {
                    "event": {
                        "contentStart": {
                            "contentId": "s1",
                            "type": "TEXT",
                            "role": "ASSISTANT",
                            "additionalModelFields": _SPECULATIVE,
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "s1", "content": "Tell me"}}}
            )

        run(scenario())
        delta = browser.of_type("response.audio_transcript.delta")[0]
        assert delta["delta"] == "Tell me"

    def test_the_recorded_turn_is_what_was_actually_said(self):
        """FINAL, not SPECULATIVE. On a barge-in the two differ.

        A transcript that does not match the audio is worse than no transcript:
        a mentor reads a question the student never heard.
        """
        session, _upstream, _browser = make_session("hr")
        run(interviewer_turn(session, "a1", "So, walk me through your CV."))
        assert session.emitted[-1][0] == nova._SENDER_INTERVIEWER
        assert session.emitted[-1][1] == "So, walk me through your CV."

    def test_the_student_s_transcript_lands_on_the_name_the_client_reads(self):
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await student_says(session, "u1", _GOOD_ANSWER)
            assert not browser.of_type(
                "conversation.item.input_audio_transcription.completed"
            ), "a block is not an answer; the line is drawn when it is judged"
            await reply_starts(session, "a1")

        run(scenario())
        completed = browser.of_type(
            "conversation.item.input_audio_transcription.completed"
        )[0]
        assert completed["transcript"] == _GOOD_ANSWER
        assert completed["item_id"] == "u1"
        assert browser.of_type("input_audio_buffer.speech_stopped")
        # The student's line reaches the browser before the reply it provoked.
        kinds = [c["type"] for c in browser.control]
        assert kinds.index("conversation.item.input_audio_transcription.completed") < kinds.index(
            "response.created"
        )

    def test_a_barge_in_flushes_the_player_and_is_not_a_transcript(self):
        """Nova's interruption marker is a textOutput, not an event of its own.

        Treated as speech it would appear in the chat as the interviewer saying
        `{ "interrupted" : true }`, and the queue the student is talking over
        would keep playing.
        """
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {
                    "event": {
                        "contentStart": {
                            "contentId": "i1",
                            "type": "TEXT",
                            "role": "ASSISTANT",
                            "additionalModelFields": _FINAL,
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "i1", "content": '{ "interrupted" : true }'}}}
            )

        run(scenario())
        assert browser.of_type("reep.audio.flush")
        assert not browser.of_type("response.audio_transcript.delta")
        assert session._interruptions == 1

    def test_a_stream_error_event_ends_the_session_rather_than_waiting(self):
        """Bedrock reports some failures as EVENTS, not as a dropped connection.

        Ignoring them leaves the student sitting in silence in front of a model
        that is never going to speak again, with the idle cap two minutes away
        and nothing in the log naming the cause.
        """
        session, _upstream, _browser = make_session("hr")
        with pytest.raises(_SessionEnded) as raised:
            run(
                session._on_upstream_event(
                    {"event": {"validationException": {"message": "bad voiceId"}}}
                )
            )
        assert raised.value.code == nova._CLOSE_UPSTREAM_UNAVAILABLE

    def test_ready_names_the_engine_and_the_real_cap(self):
        """The clock the student sees must be the clock they get.

        A countdown promising fifteen minutes on an eight-minute stream is
        worse than no countdown: the two-minute warning never arrives.
        """
        session, _upstream, browser = make_session("hr")
        run(session._send_ready())
        ready = browser.of_type("reep.ready")[0]
        assert ready["engine"] == "nova"
        assert ready["limits"]["session_max_seconds"] == int(session._effective_cap())


# ---------------------------------------------------------------------------
# The uplink
# ---------------------------------------------------------------------------


class TestTheUplink:
    def test_the_student_is_recorded_at_the_rate_the_wav_claims(self):
        """The recorder gets the ORIGINAL 24 kHz bytes, never the resampled ones.

        app/interview_audio.py writes 24 kHz WAVs. A 16 kHz payload in a 24 kHz
        container plays back as a chipmunk — the one artefact that would make a
        recording useless as evidence of what a student said.
        """
        captured: list[tuple[str, bytes]] = []

        class _Recorder:
            def feed(self, track, pcm):
                captured.append((track, pcm))

        session, upstream, _browser = make_session("hr")
        session._recorder = _Recorder()
        frame = b"\x10\x00" * 480  # 40 ms at 24 kHz

        run(session._forward_client_audio(frame))

        assert captured == [(nova.TRACK_STUDENT, frame)]
        sent = upstream.sent[-1]["event"]["audioInput"]["content"]
        assert len(base64.b64decode(sent)) < len(frame)

    def test_a_split_sample_is_carried_not_dropped(self):
        """Half a sample interpreted as a whole one misaligns the whole session.

        It sounds fine locally (the browser never sees the uplink) and is noise
        upstream, which is the worst possible way for this to fail.
        """
        session, _upstream, _browser = make_session("hr")
        run(session._forward_client_audio(b"\x01\x00\x02"))
        assert session._pcm_carry == b"\x02"

    def test_an_oversized_frame_is_refused(self):
        """One client must not push arbitrary bytes into a billed session."""
        session, upstream, _browser = make_session("hr")
        run(session._forward_client_audio(b"\x00" * (nova._MAX_CLIENT_FRAME_BYTES + 2)))
        assert session._oversized_frames == 1
        assert not upstream.sent

    def test_resampling_lands_on_the_expected_number_of_samples(self):
        out = nova._resample(b"\x01\x00" * 480, 24_000, 16_000)
        assert len(out) == 320 * 2

    def test_a_mic_gate_frame_does_not_hold_the_session_open(self):
        """A text frame that advanced the idle clock would keep a billed stream
        alive with no audio at all."""
        session, _upstream, _browser = make_session("hr")
        before = session._last_audio_at
        session._handle_client_control(json.dumps({"type": "reep.mic.gate", "open": False}))
        assert session._gate_closes == 1
        assert session._last_audio_at == before

    def test_the_gate_frame_the_browser_actually_sends_is_counted(self):
        """The browser says `{"state": "suppressed"}`. Counting only the
        `{"open": false}` spelling left this counter at zero on every
        interview, and an opening of the gate is not a close."""
        session, _upstream, _browser = make_session("hr")
        session._handle_client_control(json.dumps({"type": "reep.mic.gate", "state": "suppressed"}))
        session._handle_client_control(json.dumps({"type": "reep.mic.gate", "state": "open"}))
        assert session._gate_closes == 1

    def test_the_browsers_audio_counters_reach_the_end_line_and_nothing_else_does(self):
        """`reep.client.stats` is the only way a "the voice kept breaking"
        report can be read without the student's own console. Whitelisted and
        clamped: a frame from anybody's devtools can put a bounded number or a
        known word into the log line, and nothing else."""
        session, _upstream, _browser = make_session("hr")
        session._handle_client_control(
            json.dumps(
                {
                    "type": "reep.client.stats",
                    "route": "speaker",
                    "local_barge_ins": 3,
                    "confirmed_barge_ins": 3,
                    "underruns": 2,
                    "lead_ms": 180,
                    "coupling": 0.31234,
                    "transcript": "a student's words must never be logged",
                    "dropped": -5,
                    "judged": 10**12,
                    "suppressed": True,
                    "keepalive": "7",
                }
            )
        )
        stats = session._client_stats
        assert stats["route"] == "speaker"
        assert stats["local_barge_ins"] == 3 and stats["underruns"] == 2
        assert stats["coupling"] == 0.312
        assert stats["dropped"] == 0
        assert stats["judged"] == 1_000_000
        assert "transcript" not in stats
        assert "suppressed" not in stats, "a boolean is not a count"
        assert "keepalive" not in stats, "a string is not a count"
        line = nova._format_client_stats(stats)
        assert line.startswith("route=speaker,")
        assert "transcript" not in line
        assert nova._format_client_stats({}) == "-"

    def test_a_malformed_stats_frame_changes_nothing_and_raises_nothing(self):
        session, _upstream, _browser = make_session("hr")
        session._handle_client_control(json.dumps({"type": "reep.client.stats", "route": "moon"}))
        assert session._client_stats == {}
        session._handle_client_control("not json")
        session._handle_client_control(json.dumps(["reep.client.stats"]))
        assert session._client_stats == {}

    def test_reep_end_stops_the_session_with_1000(self):
        session, _upstream, _browser = make_session("hr")
        session._handle_client_control(json.dumps({"type": "reep.end"}))
        assert session._stop_requested.is_set()
        assert session._stop_outcome[0] == _CLOSE_OK


# ---------------------------------------------------------------------------
# The transport under the stream
# ---------------------------------------------------------------------------


class TestTheTransport:
    """The default transport REFUSES the only call this engine exists to make.

    aws-sdk-bedrock-runtime does not depend on awscrt, and
    AsyncBedrockRuntimeConfig.resolve() with no transport picks aiohttp, which
    sets SUPPORTS_DUPLEX_STREAMING = False. invoke_model_with_bidirectional_stream
    then raises UnsupportedTransportError before a packet leaves the process —
    on every region, in every account, with credentials or none.

    Nothing else in this suite can see that: every other test fakes the upstream,
    and api-imports only proves the MODULE imports, while the transport is chosen
    inside a lazy call the CI never makes. Two tests, because there are two ways
    to lose it — the package going undeclared, and the argument going unpassed.
    """

    def test_the_engine_can_import_a_duplex_capable_transport(self):
        """Pins the manifest: awscrt is a runtime dependency, not an extra."""
        from smithy_http.aio.crt import AWSCRTHTTPClient

        assert AWSCRTHTTPClient.SUPPORTS_DUPLEX_STREAMING is True

    def test_open_hands_that_transport_to_resolve(self):
        """Pins the call: the transport is named, never left to the default."""
        import aws_sdk_bedrock_runtime.client as sdk_client
        import aws_sdk_bedrock_runtime.config as sdk_config

        captured: dict[str, object] = {}

        async def _fake_resolve(**kwargs):
            captured.update(kwargs)
            return object()

        class _FakeStream:
            async def await_output(self):
                return (None, None)

        class _FakeClient:
            def __init__(self, config):
                pass

            async def invoke_model_with_bidirectional_stream(self, _input):
                return _FakeStream()

        original_resolve = sdk_config.AsyncBedrockRuntimeConfig.resolve
        original_client = sdk_client.AsyncBedrockRuntimeClient
        sdk_config.AsyncBedrockRuntimeConfig.resolve = _fake_resolve
        sdk_client.AsyncBedrockRuntimeClient = _FakeClient
        try:
            upstream = nova._NovaUpstream("amazon.nova-2-sonic-v1:0", "ap-northeast-1", None)
            run(upstream.open())
        finally:
            sdk_config.AsyncBedrockRuntimeConfig.resolve = original_resolve
            sdk_client.AsyncBedrockRuntimeClient = original_client

        assert captured["region"] == "ap-northeast-1"
        transport = captured.get("transport")
        assert transport is not None, "resolve() was left to pick the transport"
        assert type(transport).SUPPORTS_DUPLEX_STREAMING is True

    def test_open_hands_boto3_credentials_to_the_sdk_as_a_static_identity(self, monkeypatch):
        """Production timed out inside open() with the SDK's own credential chain.

        boto3's chain is the one every other AWS call in the process succeeds
        with (on Fargate, the container credentials endpoint), so its answer is
        handed to the SDK as a static identity and the SDK's chain never runs.
        When boto3 finds nothing, the SDK chain is left to report its own error.
        Both branches pinned; and the sub-step names are what the timeout reason
        carries, so they are pinned too.
        """
        from types import SimpleNamespace

        from aws_sdk_bedrock_runtime import client as sdk_client
        from aws_sdk_bedrock_runtime import config as sdk_config
        from smithy_aws_core.identity import StaticCredentialsResolver

        captured: dict[str, object] = {}
        phases: list[str] = []

        async def _fake_resolve(**kwargs):
            captured.update(kwargs)
            return object()

        class _FakeStream:
            async def await_output(self):
                raise AssertionError("open() must not await the response half")

        class _FakeClient:
            def __init__(self, config):
                pass

            async def invoke_model_with_bidirectional_stream(self, _input):
                return _FakeStream()

        frozen = SimpleNamespace(access_key="AKIA-test", secret_key="s3cr3t", token="tok")
        monkeypatch.setattr(nova, "_boto3_credentials", lambda: frozen)
        monkeypatch.setattr(sdk_config.AsyncBedrockRuntimeConfig, "resolve", _fake_resolve)
        monkeypatch.setattr(sdk_client, "AsyncBedrockRuntimeClient", _FakeClient)

        upstream = nova._NovaUpstream("amazon.nova-2-sonic-v1:0", "ap-northeast-1", None)
        run(upstream.open())

        resolver = captured.get("aws_credentials_identity_resolver")
        assert isinstance(resolver, StaticCredentialsResolver)
        identity = run(resolver.get_identity(properties={}))
        assert (identity.access_key_id, identity.secret_access_key, identity.session_token) == (
            "AKIA-test", "s3cr3t", "tok"
        )
        assert upstream.phase == "open"
        # The response half is attached later, never inside open().
        assert upstream._output is None

        # No boto3 credentials: the SDK's own chain, i.e. no resolver passed.
        captured.clear()
        monkeypatch.setattr(nova, "_boto3_credentials", lambda: None)
        upstream = nova._NovaUpstream("amazon.nova-2-sonic-v1:0", "ap-northeast-1", None)
        run(upstream.open())
        assert "aws_credentials_identity_resolver" not in captured


# ---------------------------------------------------------------------------
# The 8-minute wall
# ---------------------------------------------------------------------------


class TestTheConnectionWall:
    def test_bedrock_s_limit_wins_over_the_relay_s_cap(self):
        """A Nova stream dies at 8 minutes; INTERVIEW_MAX_SECONDS is 900.

        Running the longer cap against the shorter stream produces exactly the
        failure the phase machine exists to prevent: an interview cut off with
        no verdict and no scorecard.
        """
        session, _upstream, _browser = make_session("hr")
        assert session._effective_cap() < float(settings.interview_max_seconds)
        assert session._effective_cap() <= float(settings.nova_sonic_connection_seconds)

    def test_the_wrap_up_is_forced_with_room_to_finish_speaking(self):
        """The reserve is the verdict plus the scorecard, not a rounding margin."""
        session, _upstream, _browser = make_session("hr")
        assert nova._WRAP_UP_RESERVE_S >= 60
        assert session._effective_cap() - nova._WRAP_UP_RESERVE_S > 0

    def test_forcing_the_wrap_up_skips_the_invitation(self):
        """Out of time is not the moment to ask "any questions for us?".

        The only possible answer is the socket closing, and the student loses
        the verdict they sat the interview for.
        """
        session, upstream, _browser = make_session("hr")
        run(session._force_wrap_up())
        assert session._machine.phase is InterviewPhase.WRAP_UP
        assert session._verdict_requested is True
        assert any("closing verdict" in note for note in upstream.notes)
        assert not any("any questions for you" in note for note in upstream.notes)

    def test_forcing_the_wrap_up_twice_asks_once(self):
        """The watchdog re-enters every second past the threshold."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await session._force_wrap_up()
            await session._force_wrap_up()

        run(scenario())
        assert sum("closing verdict" in note for note in upstream.notes) == 1

    def test_forcing_the_wrap_up_lets_the_sentence_in_flight_finish(self):
        """Out of time is not a reason to cut the interviewer off mid-word.

        The clock fires once a second and does not know whether the model is
        speaking. Injecting the verdict onto a reply in flight is the same
        barge-in as a phase note: the reply is abandoned, the interruption
        marker flushes the browser's queue, and the verdict opens on a
        half-word. Held, the verdict follows the sentence; the reserve is
        sized for that. The watchdog's re-entry must not queue it twice.
        """
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event(
                {"event": {"completionStart": {"completionId": "c1"}}}
            )
            before = len(upstream.notes)
            await session._force_wrap_up()
            await session._force_wrap_up()
            assert session._verdict_requested is True
            assert session._machine.phase is InterviewPhase.WRAP_UP
            assert len(upstream.notes) == before
            assert sum("closing verdict" in note for note in session._pending_notes) == 1

            await spoken_reply(session, "a1")
            assert sum("closing verdict" in note for note in upstream.notes[before:]) == 1
            # The verdict has only just been asked for; the scorecard waits for
            # it to be SPOKEN, which is the next completion.
            assert session._report_requested is False
            await interviewer_turn(session, "verdict", "Two strengths, one drill.")
            assert session._report_requested is True

        run(scenario())


# ---------------------------------------------------------------------------
# Casting and configuration
# ---------------------------------------------------------------------------


class TestVoices:
    def test_every_matrix_row_casts_a_real_nova_voice(self):
        """An unknown voiceId is a ValidationException at the handshake.

        That is an interview that never starts, with nothing on the student's
        screen naming the cause — so the four rows are pinned here.
        """
        for spec in SPECIALIZATIONS.values():
            assert spec.nova_voice in KNOWN_NOVA_VOICES

    def test_the_four_roles_do_not_all_sound_the_same(self):
        voices = {spec.nova_voice for spec in SPECIALIZATIONS.values()}
        assert len(voices) == len(SPECIALIZATIONS)

    def test_a_mistyped_voice_falls_back_instead_of_failing_the_session(self):
        session, _upstream, _browser = make_session(None)
        try:
            settings.nova_sonic_voice = "coral"  # an OpenAI voice, not a Nova one
            voice, requested = nova_voice_for(None)
        finally:
            settings.nova_sonic_voice = Settings.model_fields["nova_sonic_voice"].default
        assert voice == DEFAULT_NOVA_VOICE
        assert requested == "coral"
        assert session is not None

class TestEngineSelection:
    def test_nova_is_the_default_engine(self):
        assert Settings().interview_engine == "nova"
        assert Settings(interview_engine="nova").interview_engine == "nova"

    def test_a_typo_falls_back_to_the_documented_default(self):
        """An allowlist, so a typo lands on something that exists."""
        assert Settings(interview_engine="nove").interview_engine == "nova"

    def test_the_retired_engine_is_not_a_recognised_value(self):
        """"openai" ran this interview until 2026-09 and its module is gone.

        Left in the allowlist it would resolve to an engine the router can no
        longer construct — a TypeError at the moment a student presses Start.
        It falls back like any other unknown string instead.
        """
        assert Settings(interview_engine="openai").interview_engine == "nova"

    def test_readiness_asks_the_engine_that_is_actually_running(self):
        """Each engine answers for itself, and neither needs a pasted key.

        This used to be one question — "is OPENAI_API_KEY set?" — asked of every
        engine, which told a Nova or local deployment its interviews were
        unconfigured until somebody pasted a key it would never spend.
        """
        aws = Settings(interview_engine="nova", nova_sonic_region="us-east-1")
        assert aws.interview_ready is True
        # The local engine needs nothing configured: its failures (missing
        # weights, no GPU) surface at start with their own close code and their
        # own sentence, which is more useful than a blanket "unavailable".
        assert Settings(interview_engine="local").interview_ready is True

    def test_a_nova_deployment_with_no_region_is_reported_unavailable(self):
        """The endpoint is composed from the region by this process.

        Blank is not "let the SDK decide" here — it is a DNS failure the
        student meets as a dead socket.
        """
        stranded = Settings(
            interview_engine="nova", nova_sonic_region="", bedrock_region=""
        )
        if stranded.nova_region:
            pytest.skip("AWS_REGION is set in this environment")
        assert stranded.interview_ready is False
        assert "region" in stranded.interview_unready_reason

    def test_the_endpointing_default_is_the_most_patient_one(self):
        """An interview answer contains thinking pauses.

        AWS documents the three levels as the pause Nova waits for before
        taking the turn: HIGH 1.5 s, MEDIUM 1.75 s, LOW ~2 s. This shipped at
        MEDIUM and a student gathering an example was answered over the second
        half of their answer -- "it records the answer very rigidly". Being cut
        off mid-answer is the most damaging thing a mock interviewer can do to
        a nervous student, so the default is the slowest one, and a typo still
        falls back rather than failing the handshake.
        """
        session, _upstream, _browser = make_session("hr")
        assert session._endpointing() == "LOW"


# ---------------------------------------------------------------------------
# An interruption that was not an answer
# ---------------------------------------------------------------------------


async def interrupted(session, cid: str = "i1") -> None:
    """Nova's barge-in marker inside the reply being interrupted, its audio
    block stopping INTERRUPTED, and the FINAL text of what was actually
    spoken closing the reply. No completionEnd: Nova 2 Sonic sends none per
    reply."""
    await session._on_upstream_event(
        {
            "event": {
                "contentStart": {
                    "contentId": f"{cid}-marker",
                    "type": "TEXT",
                    "role": "ASSISTANT",
                    "additionalModelFields": _FINAL,
                }
            }
        }
    )
    await session._on_upstream_event(
        {"event": {"textOutput": {"contentId": f"{cid}-marker", "content": '{ "interrupted" : true }'}}}
    )
    await session._on_upstream_event(
        {"event": {"contentEnd": {"contentId": cid, "type": "AUDIO", "stopReason": "INTERRUPTED"}}}
    )
    await session._on_upstream_event(
        {
            "event": {
                "contentEnd": {
                    "contentId": f"{cid}-marker",
                    "type": "TEXT",
                    "stopReason": "INTERRUPTED",
                }
            }
        }
    )


def last_student_turn(session) -> tuple[str, str, str, str | None, bool]:
    """The most recent STUDENT turn the engine emitted -- after `spoken_reply`
    the last emitted turn is the interviewer's, whose quality is always None."""
    return [turn for turn in session.emitted if turn[0] == nova._SENDER_STUDENT][-1]


class TestAnInterruptionThatWasNotAnAnswer:
    """"The voice drops and it goes to the next question."

    Nova stops speaking the moment it hears the student, and cannot tell a
    cough from the first word of an answer. It abandons the question, the
    browser flushes it, and the model then answers whatever it heard. These
    pin the recovery: when the thing that interrupted turns out not to have
    been an answer, the model is told to finish the question it was asking --
    in the gap after its reply, never on top of it.
    """

    def test_a_cough_that_interrupted_gets_the_question_resumed_after_the_reply(self):
        session, upstream, browser = make_session("hr")

        async def scenario():
            # The interviewer is mid-question; the student's chair scrapes.
            await reply_starts(session, "a1")
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a1")
            assert browser.of_type("reep.audio.flush"), "the browser's queue is flushed on the marker"
            assert session._response_open is False, "the interrupted reply is over"
            before = len(upstream.notes)

            # Nova replies to the noise; the noise is judged as that reply starts.
            await student_says(session, "u1", "hmm")
            await reply_starts(session, "a2", "Sorry, I didn't catch that.")
            assert last_student_turn(session)[3] == "filler"
            assert last_student_turn(session)[4] is False
            assert session._machine.phase is InterviewPhase.OPENING
            # Held, not sent: a note now would barge in on the reply in flight.
            assert len(upstream.notes) == before
            assert len(session._pending_notes) == 1
            assert "finish the question you were asking" in session._pending_notes[0]

            await reply_speaks(session, "a2")
            await reply_ends(session, "a2", "Sorry, I didn't catch that.")
            sent = upstream.notes[before:]
            assert any("finish the question you were asking" in note for note in sent), (
                "the resume directive must go upstream once the reply has finished"
            )
            assert session._pending_notes == []

        run(scenario())

    def test_a_real_answer_given_over_the_interviewer_is_still_an_answer(self):
        """Barge-in with content is what barge-in is for; nothing is resumed."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await reply_starts(session, "a1")
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a1")
            before = len(upstream.notes)
            await exchange(session, 1)
            assert last_student_turn(session)[3] == "accepted"
            assert session._machine.phase is InterviewPhase.PROBING
            assert len(upstream.notes) == before
            assert session._pending_notes == []

        run(scenario())

    def test_a_short_answer_that_did_not_interrupt_is_not_resumed(self):
        """The documented no-clarification rule still holds when nothing was
        cut off: a short answer in the ordinary gap steers nothing."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1")
            before = len(upstream.notes)
            await exchange(session, 1, "yes it was")
            assert last_student_turn(session)[3] == "too_short"
            assert len(upstream.notes) == before
            assert session._pending_notes == []

        run(scenario())

    def test_the_interruption_stamp_is_consumed_by_one_transcript_and_expires(self, monkeypatch):
        """A marker is answered by exactly one transcript. A stale stamp must
        not make a later short answer look like a barge-in, and a marker whose
        transcript never came must not outlive its turn."""
        session, _upstream, _browser = make_session("hr")
        session._interrupted_at = 1000.0
        monkeypatch.setattr(nova.time, "monotonic", lambda: 1000.0 + nova._INTERRUPTION_ANSWER_WINDOW_S + 1)
        assert session._took_interruption() is False
        session._interrupted_at = 1000.0
        monkeypatch.setattr(nova.time, "monotonic", lambda: 1005.0)
        assert session._took_interruption() is True
        assert session._took_interruption() is False, "consumed on first read"

    def test_the_interviewers_own_words_coming_back_are_echo_not_an_answer(self):
        """Loud speakers play the question into the microphone; Nova transcribes
        it as the student and, long enough for the word gate, it would advance
        the arc on an answer nobody gave -- and the model would answer its own
        question. It is recorded as `echo`, counts for nothing, and the model is
        asked to put the question again.

        Until 2026-10-29 this could not work in production: the sentence it is
        compared against was set when a reply closed, and replies only closed
        on a completionEnd Nova 2 Sonic never sends mid-session."""
        session, upstream, _browser = make_session("hr")
        question = "Tell me about a time you led a team through a difficult change at work."

        async def scenario():
            await interviewer_turn(session, "a1", question)
            assert session._last_interviewer_text == question
            before = len(upstream.notes)
            await student_says(session, "u1", "about a time you led a team through a difficult change at work")
            await reply_starts(session, "a2")
            assert last_student_turn(session)[3] == "echo"
            assert last_student_turn(session)[4] is False
            assert session._machine.phase is InterviewPhase.OPENING
            assert len(session._pending_notes) == 1
            await reply_speaks(session, "a2")
            await reply_ends(session, "a2")
            assert any("finish the question you were asking" in note for note in upstream.notes[before:])

        run(scenario())

    def test_an_interruption_reported_only_as_a_stop_reason_is_still_resumed(self):
        """Nova can end the audio block INTERRUPTED without the text marker.
        It is the same barge-in: the browser is told to flush and the
        transcript that follows is judged as the thing that interrupted, so a
        cough gets the question back rather than losing it. With no FINAL text
        either, the cut reply is closed when the next one starts."""
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await reply_starts(session, "a1")
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await session._on_upstream_event(
                {"event": {"contentEnd": {"contentId": "a1", "type": "AUDIO", "stopReason": "INTERRUPTED"}}}
            )
            assert len(browser.of_type("reep.audio.flush")) == 1
            assert session._interruptions == 1

            await student_says(session, "u1", "hmm")
            await reply_starts(session, "a2")
            assert len(session._pending_notes) == 1
            assert "finish the question you were asking" in session._pending_notes[0]

        run(scenario())
        assert len(browser.of_type("response.done")) == 1, (
            "the cut reply was closed when the next one began"
        )
        assert session._response_open is True, "and the next one is in flight"

    def test_the_marker_and_the_stop_reason_are_one_interruption(self):
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c1"}}})
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a1")

        run(scenario())
        assert session._interruptions == 1
        assert len(browser.of_type("reep.audio.flush")) == 1

    def test_a_stale_stamp_does_not_swallow_the_next_interruption(self):
        """A marker whose transcript never arrived leaves its stamp behind.
        The NEXT turn cut off by a stop reason alone must still flush the
        browser: holding the flush on the old stamp would leave the student
        talking over the rest of an abandoned question."""
        session, _upstream, browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c1"}}})
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a1")  # no transcript follows
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c2"}}})
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a2", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await session._on_upstream_event(
                {"event": {"contentEnd": {"contentId": "a2", "type": "AUDIO", "stopReason": "INTERRUPTED"}}}
            )

        run(scenario())
        assert session._interruptions == 2
        assert len(browser.of_type("reep.audio.flush")) == 2

    def test_an_echo_of_a_question_cut_before_its_final_text_is_still_echo(self):
        """A barge-in can end a turn before Nova sends any FINAL text for it.
        The echo of THAT question must be compared with it -- compared with the
        question before, it matches nothing, counts as an answer, and the arc
        moves on: "the voice drops and it goes to the next question"."""
        session, _upstream, _browser = make_session("hr")
        cut = "Walk me through how you would evaluate a new market before a product launch."

        async def scenario():
            await interviewer_turn(session, "a0", "Tell me about yourself.")
            await exchange(session, 1)  # an accepted answer: the arc moves to PROBING
            phase = session._machine.phase

            await reply_starts(session, "a2", cut)
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a2", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a2")
            assert session._last_interviewer_text == cut

            await student_says(session, "u2", "how you would evaluate a new market before a product launch")
            await reply_starts(session, "a3")
            assert last_student_turn(session)[3] == "echo"
            assert last_student_turn(session)[4] is False
            assert session._machine.phase is phase, "an echo must not advance the arc"

        run(scenario())

    def test_the_speculative_text_is_never_the_record(self):
        """It is only a fallback for the echo comparison. The interviewer's
        recorded turn is still what it actually said."""
        session, _upstream, _browser = make_session("hr")

        async def scenario():
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c1"}}})
            await session._on_upstream_event(
                {
                    "event": {
                        "contentStart": {
                            "contentId": "s1",
                            "type": "TEXT",
                            "role": "ASSISTANT",
                            "additionalModelFields": _SPECULATIVE,
                        }
                    }
                }
            )
            await session._on_upstream_event(
                {"event": {"textOutput": {"contentId": "s1", "content": "What I planned to say."}}}
            )
            await spoken_reply(session, "a1", "What I said.")

        run(scenario())
        interviewer = [t for t in session.emitted if t[0] == nova._SENDER_INTERVIEWER]
        assert interviewer[-1][1] == "What I said."

    def test_an_answer_that_reuses_the_questions_words_is_not_echo(self):
        session, _upstream, _browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1", "What is the role of a manager in a difficult change?")
            await exchange(session, 1, "The role of a manager in a difficult change is to keep the team focused on the outcome and to absorb the uncertainty")

        run(scenario())
        assert last_student_turn(session)[3] == "accepted"
        assert session._machine.phase is InterviewPhase.PROBING

    def test_next_question_is_a_skip_and_nothing_is_steered(self):
        """The model heard "next question" and the briefing tells it to move on
        without pressing; a resume note here would fight the skip, and a
        clarification would refuse to take it for an answer."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1")
            before = len(upstream.notes)
            # Even said over the interviewer, a skip is a skip.
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c2"}}})
            await session._on_upstream_event(
                {"event": {"contentStart": {"contentId": "a2", "type": "AUDIO", "role": "ASSISTANT"}}}
            )
            await interrupted(session, "a2")
            await session._on_upstream_event({"event": {"completionStart": {"completionId": "c3"}}})
            await student_says(session, "u1", "next question please")
            await spoken_reply(session, "a3")
            assert last_student_turn(session)[3] == "skipped"
            assert last_student_turn(session)[4] is False
            assert session._machine.phase is InterviewPhase.OPENING
            assert len(upstream.notes) == before
            assert session._pending_notes == []

        run(scenario())

    def test_nothing_is_resumed_once_the_scorecard_has_been_asked_for(self):
        """After the verdict the student cannot hear; the only turn left is the
        tool call, and a resume note would put a spoken turn in front of it."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            for index in range(5):
                await exchange(session, index)
            await exchange(session, 99, "no thanks")
            await interviewer_turn(session, "v1", "Two strengths, one improvement, one drill. Thank you.")
            assert session._report_requested is True
            before = len(upstream.notes)
            session._interrupted_at = nova.time.monotonic()
            await student_says(session, "u-late", "thank you")
            await reply_starts(session, "late")
            assert session._pending_notes == []
            assert len(upstream.notes) == before

        run(scenario())

    def test_words_no_reply_followed_are_saved_when_the_session_closes(self):
        """The "thank you" after the verdict, or an answer the socket cut,
        never meets a reply to be judged at. It is still the student's turn,
        and a record that drops it is short by one turn with no reason."""
        session, upstream, _browser = make_session("hr")

        async def scenario():
            await interviewer_turn(session, "a1")
            await student_says(session, "u1", "thank you so much")

        run(scenario())
        before = len(session.emitted)
        notes = len(upstream.notes)
        session._record_unanswered_words()
        assert len(session.emitted) == before + 1
        assert session.emitted[-1][0] == nova._SENDER_STUDENT
        assert session.emitted[-1][1] == "thank you so much"
        assert session.emitted[-1][4] is False, "nothing advances on words at the close"
        assert len(upstream.notes) == notes
        session._record_unanswered_words()
        assert len(session.emitted) == before + 1, "saved once"

    def test_the_model_is_briefed_on_interruptions_and_skips_for_every_interview(self):
        """Nova decides WHEN the student has stopped; what it does with a turn
        that was not an answer is the briefing's job, generic interview
        included -- a cough interrupts a generic interview exactly as it does a
        specialized one."""
        for spec in ("hr", None):
            session, _upstream, _browser = make_session(spec)
            composed = session._instructions()
            assert "## Language and turn-taking" in composed
            assert "pick up the question you were asking" in composed
            assert "next question" in composed and "skip" in composed
            # Nova auto-detects the language and kiara/arjun are also its Hindi
            # voices: an Indian-accented greeting was transcribed in Devanagari
            # and the interview followed the student into Hindi. The prompt is
            # the only lever, so it pins English.
            assert "conducted in English" in composed
            assert "Speak only English" in composed
            # And it can never be read as permission to say nothing.
            assert "never answer with silence" in composed
            assert composed.endswith(nova._CONTROL_CHANNEL_NOTE)
            assert composed.startswith(_INTERVIEWER_PERSONA)


# ---------------------------------------------------------------------------
# The whole loop
# ---------------------------------------------------------------------------


class _ScriptedUpstream(_FakeUpstream):
    """Bedrock, scripted: the events one interview would receive, in order.

    Running out of script means the stream has gone quiet, which is the only
    state in which a deadline can be the thing that ends the session.
    """

    def __init__(self, script: list[dict]) -> None:
        super().__init__()
        self.script = list(script)
        self.closed = False

    async def open(self) -> None:
        return None

    async def receive(self) -> dict | None:
        if not self.script:
            await asyncio.sleep(3600)
        return self.script.pop(0)

    async def aclose(self, *, prompt_name=None, audio_content=None) -> None:
        self.closed = True


class _SilentBrowser(_FakeBrowser):
    """A browser that is connected and says nothing — the student is listening."""

    async def receive(self) -> dict:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class TestTheWholeLoop:
    def test_run_opens_greets_scores_and_closes_1000(self, monkeypatch):
        """One interview end to end through the real task group.

        The unit tests above drive `_on_upstream_event` directly, which cannot
        catch an ordering bug in the handshake or a pump that never starts —
        the two failures that would make every one of them pass while no
        student could hold an interview.
        """
        report = {
            "overall": 68,
            "communication": 70,
            "domain": 62,
            "structure": 70,
            "strengths": ["clear opening"],
            "improvements": ["quantify impact"],
            "drill": "Record a two-minute self-introduction",
            "summary": "A promising first run.",
        }
        script = [
            {"event": {"completionStart": {"sessionId": "sess-42"}}},
            {"event": {"contentStart": {"contentId": "a1", "type": "AUDIO", "role": "ASSISTANT"}}},
            {
                "event": {
                    "audioOutput": {
                        "contentId": "a1",
                        "content": base64.b64encode(b"\x02\x00" * 16).decode("ascii"),
                    }
                }
            },
            {"event": {"contentEnd": {"contentId": "a1", "type": "AUDIO", "stopReason": "END_TURN"}}},
            {"event": {"completionEnd": {"stopReason": "END_TURN"}}},
            {
                "event": {
                    "toolUse": {
                        "toolName": nova._SCORECARD_TOOL_NAME,
                        "toolUseId": "tu-9",
                        "contentId": "t1",
                        "content": json.dumps(report),
                    }
                }
            },
            {"event": {"contentEnd": {"contentId": "t1", "type": "TOOL", "stopReason": "TOOL_USE"}}},
        ]
        upstream = _ScriptedUpstream(script)
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")

        browser = _SilentBrowser()
        session = NovaSonicSession(
            browser, "c0ffee123456", specialization=SPECIALIZATIONS["ba"]
        )

        code, reason = run(session.run())

        assert (code, reason) == (_CLOSE_OK, "Interview complete")
        # The handshake, in the order Nova's contract requires.
        names = [next(iter(event["event"])) for event in upstream.sent]
        assert names[:2] == ["sessionStart", "promptStart"]
        # The system prompt, then the microphone, then the kick-off note: an
        # order Nova's contract fixes and a reordering would break at the
        # handshake rather than in any assertion above.
        assert names[2:5] == ["contentStart", "textInput", "contentEnd"]
        assert upstream.closed is True
        # The student was greeted before they said anything, and scored after.
        assert browser.of_type("reep.ready")
        assert browser.of_type("reep.report")[0]["report"]["overall"] == 68
        assert session._session_id == "sess-42"

    def test_a_stream_that_dies_before_the_scorecard_is_not_reported_complete(
        self, monkeypatch
    ):
        """The 8-minute wall, or a dropped connection, mid-interview.

        Telling the student "Interview complete" when the stream was cut is how
        a missing scorecard becomes a support ticket instead of a retry.
        """

        class _DeadStream(_ScriptedUpstream):
            async def receive(self):
                return None

        upstream = _DeadStream([])
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")

        session = NovaSonicSession(
            _SilentBrowser(), "c0ffee123456", specialization=SPECIALIZATIONS["hr"]
        )
        code, _reason = run(session.run())
        assert code == nova._CLOSE_UPSTREAM_UNAVAILABLE

    def test_an_unreachable_bedrock_is_4002_and_never_a_traceback(self, monkeypatch):
        """Credentials, IAM, an unsupported region, a throttle at the door.

        All of them are one close code to the student and a named cause in the
        log; none of them is a 1011 that reads as a bug in REEP.
        """

        class _Refusing(_FakeUpstream):
            async def open(self):
                raise RuntimeError("no credentials found")

            async def aclose(self, **kwargs):
                return None

        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: _Refusing())
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")

        session = NovaSonicSession(_SilentBrowser(), "c0ffee123456")
        code, _reason = run(session.run())
        assert code == nova._CLOSE_UPSTREAM_UNAVAILABLE


class TestTheOpenSequence:
    """The production hang, pinned.

    Bedrock does not send the response half of a bidirectional stream until it
    has received sessionStart. The engine used to await that response half
    inside open(), BEFORE the handshake had sent anything, and nothing bounded
    the wait — so a real interview was an open socket that never spoke and an
    interview_sessions row left `running` with no close code. Two properties
    close that, and each has a test that fails without it:

      ORDER  — the response half is awaited only after sessionStart is sent;
      BOUND  — open + handshake + attach cannot take longer than the setting,
               after which the student gets 4002 and a sentence.
    """

    def _ready_script(self) -> list[dict]:
        # Just enough upstream for run() to greet and then end on its own.
        return [
            {"event": {"completionStart": {"promptName": "p", "sessionId": "sess-1"}}},
        ]

    def test_bedrock_that_answers_only_after_session_start_is_greeted(self, monkeypatch):
        """THE DEADLOCK. Bedrock, modelled honestly: headers after sessionStart.

        With the old ordering run() awaits headers first and sessionStart is
        never sent; this test would hang, so the open bound is set short and
        the failure mode of a regression is a fast 4002, not a stuck suite.
        """
        session_started = asyncio.Event()

        class _HonestBedrock(_ScriptedUpstream):
            attached = False

            async def send(self, event: dict) -> None:
                await super().send(event)
                if "sessionStart" in event.get("event", {}):
                    session_started.set()

            async def attach_output(self) -> None:
                # Exactly what the service does: nothing until it has an event.
                await session_started.wait()
                self.attached = True

        class _LeavesAfterReady(_FakeBrowser):
            """A student who closes the tab the moment the interviewer is ready.

            The scripted upstream has no scorecard to end the session with, and
            waiting for the idle cap would make this test as slow as the cap.
            A disconnect right after `reep.ready` ends run() with a clean
            "Client disconnected", which is enough: the point is that ready was
            reached at all.
            """

            async def receive(self) -> dict:
                while not self.of_type("reep.ready"):
                    await asyncio.sleep(0.01)
                raise WebSocketDisconnect(code=1001)

        upstream = _HonestBedrock(self._ready_script())
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")
        monkeypatch.setattr(settings, "nova_sonic_open_timeout_seconds", 2)

        browser = _LeavesAfterReady()
        session = NovaSonicSession(browser, "c0ffee123456", specialization=SPECIALIZATIONS["hr"])
        code, reason = run(session.run())

        assert upstream.attached is True, "the response half was never attached"
        assert browser.of_type("reep.ready"), "the student was never told to start"
        # sessionStart went out BEFORE the response half was awaited.
        assert next(iter(upstream.sent[0]["event"])) == "sessionStart"
        assert (code, reason) == (_CLOSE_OK, "Client disconnected")

    def test_a_response_half_that_never_arrives_is_4002_within_the_bound(self, monkeypatch):
        """THE BOUND, on the attach. A silent Bedrock becomes a sentence."""

        class _Mute(_ScriptedUpstream):
            async def attach_output(self) -> None:
                await asyncio.sleep(3600)

        upstream = _Mute([])
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")
        monkeypatch.setattr(settings, "nova_sonic_open_timeout_seconds", 0.3)

        browser = _SilentBrowser()
        session = NovaSonicSession(browser, "c0ffee123456", specialization=SPECIALIZATIONS["hr"])
        code, reason = run(session.run())

        assert code == nova._CLOSE_UPSTREAM_UNAVAILABLE
        assert reason == "Interviewer service unavailable (handshake timed out)"
        # Never told to start talking to a service that never answered.
        assert not browser.of_type("reep.ready")
        # And the row was finalized: aclose ran, so the finally block did.
        assert upstream.closed is True

    def test_an_open_that_never_returns_is_4002_within_the_bound(self, monkeypatch):
        """THE BOUND, on open() itself — a black-holed TCP connect."""

        class _Hanging(_FakeUpstream):
            closed = False

            async def open(self) -> None:
                await asyncio.sleep(3600)

            async def aclose(self, **kwargs) -> None:
                self.closed = True

        upstream = _Hanging()
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")
        monkeypatch.setattr(settings, "nova_sonic_open_timeout_seconds", 0.3)

        session = NovaSonicSession(_SilentBrowser(), "c0ffee123456")
        code, reason = run(session.run())
        # The fake has no `phase`, so the reason falls back to "opening"; the
        # real upstream names the sub-step (credentials / client / stream).
        assert (code, reason) == (
            nova._CLOSE_UPSTREAM_UNAVAILABLE,
            "Interviewer service unavailable (open timed out while opening)",
        )

    def test_a_send_that_blocks_until_the_connection_exists_is_still_greeted(
        self, monkeypatch
    ):
        """THE MIRROR-IMAGE DEADLOCK, pinned after production showed it.

        The first fix ordered handshake-then-attach and production still timed
        out at exactly the bound. In the SDK, input send() can block until the
        connection that the OUTPUT side establishes exists — so strictly
        sequential in EITHER order deadlocks. Attach and handshake now run
        concurrently, which is the AWS sample's shape; this fake makes send()
        wait for attach and would hang the sequential version.
        """
        attach_started = asyncio.Event()

        class _ConnectsOnAttach(_ScriptedUpstream):
            async def send(self, event: dict) -> None:
                await attach_started.wait()  # the connection exists only once attach ran
                await super().send(event)

            async def attach_output(self) -> None:
                attach_started.set()

        class _LeavesAfterReady(_FakeBrowser):
            async def receive(self) -> dict:
                while not self.of_type("reep.ready"):
                    await asyncio.sleep(0.01)
                raise WebSocketDisconnect(code=1001)

        upstream = _ConnectsOnAttach(self._ready_script())
        monkeypatch.setattr(nova, "_NovaUpstream", lambda *args, **kwargs: upstream)
        monkeypatch.setattr(settings, "nova_sonic_region", "us-east-1")
        monkeypatch.setattr(settings, "nova_sonic_open_timeout_seconds", 2)

        browser = _LeavesAfterReady()
        session = NovaSonicSession(browser, "c0ffee123456", specialization=SPECIALIZATIONS["hr"])
        code, reason = run(session.run())

        assert browser.of_type("reep.ready"), "sequential ordering would have deadlocked here"
        assert next(iter(upstream.sent[0]["event"])) == "sessionStart"
        assert (code, reason) == (_CLOSE_OK, "Client disconnected")


class _RecordingUpstream(nova._NovaUpstream):
    """The real teardown, with the wire replaced. `__slots__` is why this is a
    subclass and not a monkeypatched attribute."""

    def __init__(self) -> None:
        super().__init__("amazon.nova-2-sonic-v1:0", "us-east-1", nova.log)
        self.sent: list[dict] = []

    async def send(self, event: dict) -> None:  # type: ignore[override]
        if self._closed:
            return
        self.sent.append(event)


class TestTheClosingSequence:
    def test_the_stream_is_closed_in_the_order_bedrock_requires(self):
        """contentEnd, promptEnd, sessionEnd — then the socket.

        Skipping them leaks the prompt on Bedrock's side until the 8-minute
        timeout reaps it, and a half-closed prompt is what a "the model would
        not answer my next session" report looks like.
        """
        upstream = _RecordingUpstream()
        run(upstream.aclose(prompt_name="p", audio_content="mic"))
        assert [next(iter(event["event"])) for event in upstream.sent] == [
            "contentEnd",
            "promptEnd",
            "sessionEnd",
        ]

    def test_closing_twice_is_a_no_op(self):
        """Teardown runs from run()'s finally and from the router's backstop."""
        upstream = _RecordingUpstream()

        async def scenario():
            await upstream.aclose(prompt_name="p", audio_content="mic")
            await upstream.aclose(prompt_name="p", audio_content="mic")

        run(scenario())
        assert len(upstream.sent) == 3
