"""`/ws/media-bridge` — the WebSocket that ingests the candidate's audio and
yields the AI response audio, on the Nova engine, for one degree level's
catalogue.

    wss://<host>/ws/media-bridge?degree=UG&specialization=bsc-ai
    wss://<host>/api/platform/media-bridge?degree=PG&specialization=mtech-data-science

Both paths are the same handler. The first is the architecture's name; the
second is reachable through the existing CDN/ALB routing, which forwards
`/api/*` to the API and everything else to the SPA bucket.

THE WIRE CONTRACT IS `/api/interview`'S, UNCHANGED. Same cookie session, same
Origin check, same 24 kHz PCM16 frames in and out, same downstream events
(`reep.ready`, transcripts, the scorecard) and the same close codes
(app/interview_core.py). The Angular interview client can point at this socket
with only the URL changed. What differs is where the interviewer's material
comes from — the per-degree catalogue rows compiled by
app/voice_platform/engine — and what happens at close: the dual-channel buffer
is rendered and uploaded and the session is projected to DynamoDB.

REUSE, NOT A FORK. The handshake below deliberately calls the interview
router's own helpers — `_open_records` (consent gate + fleet caps + the
`interview_sessions` row), the turn/report/finalizer/heartbeat writers, the
per-worker limiter and the shutdown drain set — so a platform call is a real
interview record with a platform row beside it, and every guard those helpers
carry (rule 1's containment, consent fail-closed, the 4012/4015 caps) applies
here without a second copy that could drift.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketException

from ...config import settings
from ...db import SessionLocal
from ...identity import get_ws_session
from ...interview_audio import audio_consent_granted, recorder_for
from ...interview_core import (
    _CLOSE_FORBIDDEN_ORIGIN,
    _CLOSE_GOING_AWAY,
    _CLOSE_INTERNAL,
    _CLOSE_NOT_CONFIGURED,
    _CLOSE_OVERLOADED,
    _CLOSE_UNKNOWN_SPECIALIZATION,
    _CLOSE_USER_SESSION_CAP,
    _CLOSE_CONSENT_REQUIRED,
    _CLOSE_DAILY_CAP,
    _CLOSE_CONSENT_REVOKED,
    _REFUSED_BY_USER,
)
from ...models.user import Role
from ...routers import interview as interview_router
from ...routers.interview import (
    _CLOSE_NOT_A_STUDENT,
    _ConsentRequired,
    _DailyCapReached,
    _FleetFull,
    _LIMITER,
    _LIVE_SESSIONS,
    _UserSessionCapReached,
    _close_downstream,
    _finalize_if_running,
    _make_finalizer,
    _make_heartbeat,
    _make_report_writer,
    _make_turn_writer,
    _open_records,
)
from ..engine import nova as engine
from ..monitoring import sentry
from ..monitoring.cloudwatch import get_logger, put_metric
from ..queue.validation import CandidateValidationError, normalize_degree
from ..storage import aurora
from ..storage.dynamodb import session_store_for
from ..streaming import buffer as wav_buffer
from ..streaming.buffer import DualChannelBuffer
from ..streaming.tee import TeeRecorder
from . import call_close

log = get_logger("api.media_bridge")

router = APIRouter(tags=["voice-platform-bridge"])


async def _refuse(websocket: WebSocket, conn_id: str, code: int, reason: str, why: str) -> None:
    log.warning("[conn=%s] WS media-bridge -> %d: %s", conn_id, code, why)
    put_metric("BridgeRefused", 1, code=str(code))
    await _close_downstream(websocket, code, reason)


def _open_platform_row(
    *,
    call_id: str,
    degree: str,
    user_id: str,
    interview_session_id: str,
    config: engine.EngineConfig,
) -> tuple[str | None, bool, bool]:
    """Worker thread: the platform row, the recording-policy answer and the
    DynamoDB projection. Returns (candidate_id, policy_enabled, keep_mono)."""
    db = SessionLocal()
    try:
        candidate = aurora.candidate_for_user(db, user_id)
        aurora.create_call_session(
            db,
            session_id=call_id,
            degree_level=degree,
            user_id=user_id,
            interview_session_id=interview_session_id,
            specialization_key=config.specialization.key,
            time_limit_seconds=config.max_seconds,
            candidate_id=candidate.id if candidate else None,
        )
        policy = aurora.get_recording_policy(db, degree)
        db.commit()
        store = session_store_for(degree)
        ok = store.put(
            {
                "session_id": call_id,
                "degree_level": degree,
                "user_id": user_id,
                "candidate_id": candidate.id if candidate else None,
                "interview_session_id": interview_session_id,
                "specialization": config.specialization.key,
                "status": "running",
                "time_limit_seconds": config.max_seconds,
                "question_count": config.question_count,
            }
        )
        aurora.mark_synced(db, call_id, dynamo=ok and store.name != "memory")
        db.commit()
        return (
            candidate.id if candidate else None,
            bool(policy and policy.enabled),
            bool(policy and policy.keep_channels in ("mixed", "both")),
        )
    finally:
        db.close()


async def media_bridge(websocket: WebSocket) -> None:
    conn_id = uuid.uuid4().hex[:12]
    await websocket.accept()

    origin = websocket.headers.get("origin")
    if origin is not None and origin != settings.web_origin:
        await _refuse(websocket, conn_id, _CLOSE_FORBIDDEN_ORIGIN, "Origin not allowed", f"origin {origin!r}")
        return

    try:
        session = await asyncio.to_thread(get_ws_session, websocket)
    except WebSocketException as exc:
        await _refuse(websocket, conn_id, exc.code, exc.reason or "Sign in required.", "no valid session")
        return

    if session.get("role") != Role.STUDENT.value or not session.get("studentId"):
        await _refuse(
            websocket, conn_id, _CLOSE_NOT_A_STUDENT,
            "Mock interviews are a student feature.", f"role {session.get('role')} / no studentId",
        )
        return
    user_id = session["userId"]
    student_id = session["studentId"]

    if not engine.engine_ready():
        await _refuse(
            websocket, conn_id, _CLOSE_NOT_CONFIGURED,
            "The interviewer is not configured on this server yet.",
            f"engine {settings.interview_engine!r} / region {settings.nova_region!r}",
        )
        return

    # The two selectors. Both REQUIRED here: the platform has no generic
    # interview — a call is always against one degree level's catalogue.
    try:
        degree = normalize_degree(websocket.query_params.get("degree"))
    except CandidateValidationError as exc:
        await _refuse(websocket, conn_id, _CLOSE_UNKNOWN_SPECIALIZATION, f"Unknown degree level: {exc.message}", str(exc))
        return
    spec_key = (websocket.query_params.get("specialization") or "").strip()
    if not spec_key:
        await _refuse(websocket, conn_id, _CLOSE_UNKNOWN_SPECIALIZATION, "specialization is required", "no specialization")
        return

    refused_by = _LIMITER.try_acquire(user_id)
    if refused_by == _REFUSED_BY_USER:
        await _refuse(
            websocket, conn_id, _CLOSE_USER_SESSION_CAP,
            "You already have a mock interview open. Close it and try again.", "per-user cap",
        )
        return
    if refused_by:
        await _refuse(websocket, conn_id, _CLOSE_OVERLOADED, "Too many interviews in progress", "worker cap")
        return

    # From here the slot is HELD; every exit releases it.
    try:
        def _load() -> engine.EngineConfig | None:
            db = SessionLocal()
            try:
                return engine.load_engine_config(db, degree, spec_key)
            finally:
                db.close()

        config = await asyncio.to_thread(_load)
    except Exception:
        _LIMITER.release(user_id)
        log.exception("[conn=%s] media-bridge: cannot load the %s/%s catalogue", conn_id, degree, spec_key)
        await _close_downstream(websocket, _CLOSE_INTERNAL, "Internal error")
        return
    if config is None:
        _LIMITER.release(user_id)
        await _refuse(
            websocket, conn_id, _CLOSE_UNKNOWN_SPECIALIZATION,
            f"Unknown specialization: {spec_key}", f"{degree}/{spec_key} not in the catalogue",
        )
        return

    try:
        # `_open_records` returns a RECORD since B6.1 (it now also carries the
        # college's resolved policy and the pinned acknowledgement's scopes).
        # Unpacked here rather than passed around: this path applies the
        # PLATFORM's own per-degree recording policy and time limit below, and
        # the two must not silently start disagreeing about which one won.
        opened = await asyncio.to_thread(
            _open_records, user_id, Role(session["role"]), student_id, conn_id, config.specialization
        )
        conversation_id = opened.conversation_id
        interview_session_id = opened.interview_session_id
        consent_id = opened.consent_id
        interview_policy = opened.policy
    except _ConsentRequired:
        _LIMITER.release(user_id)
        await _refuse(websocket, conn_id, _CLOSE_CONSENT_REQUIRED, "Interview consent required", "no live consent")
        return
    except _UserSessionCapReached:
        _LIMITER.release(user_id)
        await _refuse(
            websocket, conn_id, _CLOSE_USER_SESSION_CAP,
            "You already have a mock interview open. Close it and try again.", "fleet-wide per-user cap",
        )
        return
    except _FleetFull:
        _LIMITER.release(user_id)
        await _refuse(
            websocket, conn_id, _CLOSE_OVERLOADED,
            "Too many interviews in progress", "fleet-wide ceiling",
        )
        return
    except _DailyCapReached as exc:
        # B6.4's two ceilings: the sentence comes off the exception, so this
        # path says the same thing the student portal's socket says and a change
        # to the wording cannot reach one and miss the other.
        _LIMITER.release(user_id)
        await _refuse(
            websocket, conn_id, _CLOSE_DAILY_CAP, exc.message, f"{exc.which} cap",
        )
        return
    except Exception:
        _LIMITER.release(user_id)
        log.exception("[conn=%s] media-bridge: cannot open the interview records", conn_id)
        await _close_downstream(websocket, _CLOSE_INTERNAL, "Internal error")
        return

    call_id = uuid.uuid4().hex
    sentry.tag_connection(conn_id=conn_id, degree=degree, specialization=config.specialization.key, call_id=call_id)
    buffer: DualChannelBuffer | None = None
    try:
        candidate_id, policy_enabled, keep_mono = await asyncio.to_thread(
            _open_platform_row,
            call_id=call_id,
            degree=degree,
            user_id=user_id,
            interview_session_id=interview_session_id,
            config=config,
        )
    except Exception:
        _LIMITER.release(user_id)
        log.exception("[conn=%s] media-bridge: cannot open the platform call row", conn_id)
        await _close_downstream(websocket, _CLOSE_INTERNAL, "Internal error")
        await asyncio.to_thread(_finalize_if_running, interview_session_id, conn_id, _CLOSE_INTERNAL, "Internal error")
        return

    # RECORDING: three switches, all required — the degree's policy, the
    # process flag, and the candidate's own live store-audio grant. The
    # per-speaker recorder keeps its own gates inside recorder_for() — the
    # operator's flag, the college's policy and the candidate's grant.
    primary = await asyncio.to_thread(
        recorder_for,
        interview_session_id,
        user_id,
        # FOUR switches on this path, and the college's is the new one: the
        # degree's platform policy (`policy_enabled`, applied below), the
        # operator's flag, the college's `store_audio` and the candidate's own
        # grant. The narrowest wins, which is why this is an AND of all of them
        # rather than the platform policy standing in for the college's.
        policy_allows_audio=interview_policy.store_audio,
    )
    if policy_enabled and settings.interview_recording_enabled and interview_policy.store_audio:
        consented = await asyncio.to_thread(audio_consent_granted, user_id)
        if consented:
            buffer = DualChannelBuffer(
                call_id,
                max_bytes=settings.platform_buffer_max_bytes,
                local_dir=call_close.platform_audio_dir(),
                keep_mono=keep_mono,
            )
            wav_buffer.register(buffer)
        else:
            log.info("[conn=%s] media-bridge: policy records %s calls but this candidate has no store-audio grant", conn_id, degree)
    recorder: Any | None = TeeRecorder(buffer, primary) if buffer is not None else primary

    loop = asyncio.get_running_loop()
    relay_box: list[Any] = []

    def _consent_withdrawn() -> None:
        try:
            loop.call_soon_threadsafe(relay_box[0].request_stop, _CLOSE_CONSENT_REVOKED, "Consent withdrawn")
        except RuntimeError:
            pass

    base_finalize = _make_finalizer(interview_session_id)
    base_heartbeat = _make_heartbeat(
        interview_session_id,
        consent_id=consent_id,
        on_consent_revoked=_consent_withdrawn,
        consent_scopes=opened.consent_scopes,
    )

    def on_heartbeat() -> None:
        base_heartbeat()
        try:
            db = SessionLocal()
            try:
                aurora.touch_call_session(db, call_id)
                db.commit()
            finally:
                db.close()
            session_store_for(degree).update(call_id, {"heartbeat_at": loop.time()})
        except Exception as exc:  # noqa: BLE001 - a heartbeat never ends a call
            log.warning("[conn=%s] media-bridge heartbeat bookkeeping failed: %s", conn_id, exc)

    turns_seen = {"n": 0}
    base_turn = _make_turn_writer(
        conversation_id,
        interview_session_id,
        store_transcript=interview_policy.store_transcript,
    )

    def on_turn(*args: Any, **kwargs: Any) -> None:
        turns_seen["n"] += 1
        base_turn(*args, **kwargs)

    relay = engine.build_session(
        websocket,
        conn_id,
        config,
        recorder=recorder,
        on_turn=on_turn,
        on_report=_make_report_writer(interview_session_id),
        on_finalize=base_finalize,
        on_heartbeat=on_heartbeat,
    )
    relay_box.append(relay)
    _LIVE_SESSIONS.add(relay)
    put_metric("BridgeOpened", 1, degree=degree)
    log.info(
        "[conn=%s] media-bridge open: call=%s degree=%s spec=%s limit=%ds questions=%d recording=%s",
        conn_id, call_id, degree, config.specialization.key, config.max_seconds, config.question_count, buffer is not None,
    )
    code, reason = _CLOSE_INTERNAL, "Internal error"
    try:
        with sentry.transaction(f"platform.media_bridge.{degree}", degree=degree, call_id=call_id):
            code, reason = await relay.run()
    except asyncio.CancelledError:
        code, reason = _CLOSE_GOING_AWAY, "Server shutting down"
        raise
    except Exception:
        log.exception("[conn=%s] media-bridge: unhandled error in the relay", conn_id)
        code, reason = _CLOSE_INTERNAL, "Internal error"
    finally:
        _LIVE_SESSIONS.discard(relay)
        _LIMITER.release(user_id)
        await _close_downstream(websocket, code, reason)
        try:
            await asyncio.to_thread(_finalize_if_running, interview_session_id, conn_id, code, reason)
        except Exception:
            log.exception("[conn=%s] media-bridge: interview backstop finalization failed", conn_id)
        try:
            await call_close.finish_call(
                call_id, degree_level=degree, code=code, reason=reason, buffer=buffer, turns=turns_seen["n"]
            )
        except Exception:
            log.exception("[conn=%s] media-bridge: call close failed for %s", conn_id, call_id)


# Registered twice on purpose — see the module docstring.
router.add_api_websocket_route("/ws/media-bridge", media_bridge)
router.add_api_websocket_route("/api/platform/media-bridge", media_bridge)

# Keep a reference so the interview router's shutdown drain sees these
# sessions: they live in the same _LIVE_SESSIONS set it iterates.
__all__ = ["router", "media_bridge", "interview_router"]
