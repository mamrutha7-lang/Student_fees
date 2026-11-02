"""FastAPI application entrypoint.

Run (dev):  uvicorn app.main:app --port 3300
Docs:       http://localhost:3300/docs

(No --reload: on Windows it has wedged a stale worker here. See AGENTS.md.)
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from fastapi.middleware.cors import CORSMiddleware

from . import retention
from . import document_archive
from .config import settings
from .db import SessionLocal
from .observability import SERVICE_API, init_sentry
from .traceability import RequestTraceMiddleware
from .routers import (
    onboarding,
    passwords,
    admin,
    audit,
    governance,
    agent,
    alumni,
    auth,
    admin_catalogue,
    badge_verification,
    badges,
    console,
    health,
    interview,
    admin_deletion,
    admin_faculty,
    admin_fees,
    admin_mail,
    student_fees,
    admin_imports,
    admin_interview_tracks,
    admin_mentoring,
    admin_promotion,
    admin_role_preferences,
    admin_student_360,
    admin_student_record,
    admin_students,
    interview_bank,
    interview_policy,
    leave_alternate,
    leave_attachments,
    leave_paper,
    leave_policy,
    signature,
    swoc,
    leave,
    mentee_records,
    mentor,
    registration,
    redesign,
    staff_upskilling,
    student,
    student_programme,
)
from .voice_platform.api import admin as platform_admin
from .voice_platform.api import calls as platform_calls
from .voice_platform.api import media_bridge as platform_bridge
from .voice_platform import ssm_config as platform_ssm_config
from .voice_platform.monitoring import cloudwatch as platform_cloudwatch

log = logging.getLogger("reep.startup")

# Sentry is the ONE observability + traceability tool (user decision, 2026-08):
# errors and performance traces from api and web land in one place, joined to
# raw CloudWatch log lines by the X-Request-ID tag app/traceability.py sets.
# Initialised at import time — before the app object — so even lifespan/boot
# failures are captured. Blank SENTRY_DSN = not initialised = every downstream
# sentry_sdk call is a no-op; a laptop and CI pay nothing.
#
# The init itself — its three rule-1 flags (send_default_pii=False,
# include_local_variables=False, max_request_body_size="never"), the sampler
# and the scrubbing hooks — lives in app/observability.py: ONE function for the
# three processes that report (this one, the retention job, the drain worker),
# so the flags cannot drift between them. tests/test_codebase_guards.py reads
# that file as text for the flags and this file as text for the call. The
# service name is what separates the projects; the DSN is this project's own.
init_sentry(SERVICE_API, settings.sentry_dsn)


def _sweep_orphaned_interviews() -> int:
    """Layer 3 of interview finalization, run once per boot. See the call site
    in `lifespan` below, which is where the reasoning lives.

    Its own function so that call stays a single readable line, and so the
    session is opened AND closed on the worker thread — a `Session` is not safe
    to hand across threads, and the one thing worse than an unswept orphan is a
    connection shared by two of them.
    """
    with SessionLocal() as db:
        return retention.finalize_orphaned_interviews(db)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Clamp what may be logged, then two configuration gates before we serve.

    Boot is where the gates belong. `Settings` can decide what is wrong, but only
    startup can REFUSE, and startup is the moment an operator is actually
    watching; anything checked later is checked at request time, by which point
    the process is already answering students.
    """
    # A HARD FLOOR, not a preference. At DEBUG the `websockets` library prints
    # the outbound handshake header by header (ClientProtocol.send_request,
    # guarded by Protocol.debug = logger.isEnabledFor(DEBUG)) and redacts
    # nothing -- and one of those headers is
    # `Authorization: Bearer <OPENAI_API_KEY>` on the interview relay's upstream
    # socket. Running uvicorn with --log-level debug is
    # a documented troubleshooting step, so without this the operator following
    # the manual is the one who prints the credential into the API log, beside
    # student traffic, in whatever aggregator this deployment ships to. That
    # would defeat the containment the relay exists to provide.
    # Protocol.debug is evaluated per connection at connect time, i.e. after
    # this runs, and websockets.client/.server are NOTSET so they inherit it.
    logging.getLogger("websockets").setLevel(logging.INFO)

    # THE APP'S OWN LOG LINES HAD NOWHERE TO GO (2026-09-10). Nothing in this
    # process ever called basicConfig, so the root logger carried no handler and
    # sat at WARNING: every `log.info` in `app/` was discarded, in development
    # AND in production. What that cost is concrete — the console mail
    # transport writes the activation and confirmation LINKS at INFO and calls
    # itself "not a stub" for exactly that reason, so a developer following
    # AGENTS.md to read a link out of the uvicorn log found nothing; and the
    # 30-day production log contained not one mail line while real applicants
    # were being silently dropped. `logging.lastResort` masks it, because
    # WARNING and above still reach stderr, so the log looks alive.
    #
    # uvicorn configures its OWN loggers (uvicorn.access, uvicorn.error) and
    # deliberately leaves the root alone, so this is ours to set.
    #
    # NOT `force=True`, which was the first attempt and is wrong: it REMOVES
    # every handler already on the root logger, which in a container is
    # whatever the operator configured and under pytest is caplog's capturing
    # handler — test_boot_guard's "raised AND logged at CRITICAL" assertion
    # went from passing to failing while the message was still plainly on
    # stderr. Configure a bare process; adjust the level on one that is already
    # configured; never take somebody else's handler away.
    _root = logging.getLogger()
    _level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if _root.handlers:
        _root.setLevel(_level)
    else:
        logging.basicConfig(
            level=_level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # The voice platform's direct CloudWatch Logs handler, only when
    # PLATFORM_CLOUDWATCH_LOG_GROUP is set (stdout -> awslogs is the default).
    # The voice platform's PLATFORM_* settings, from the SSM parameters its CDK
    # stack publishes (app/voice_platform/ssm_config.py). Environment wins; a
    # blank is filled; any failure leaves the setting blank and is logged.
    platform_ssm_config.load()
    platform_cloudwatch.configure()

    # 1) REFUSE to serve real people with the credentials published in this repo.
    #
    # Settings.production_boot_failures() decides (it owns the defaults, so it
    # cannot end up comparing against a string nobody uses); this raises. It is
    # fatal rather than a warning, and there is no override flag, for the reason
    # app/seed.py's ENV=prod refusal gives: a warning about a leaked signing key
    # is a line in a log nobody reads, and the deployment it describes is one
    # forged cookie away from every student's marks, attendance and USN. The
    # check returns [] on every non-production ENV, so a laptop, CI, and the test
    # suite — which boots this same app through TestClient — never meet it.
    #
    # Logged AND raised on purpose: uvicorn reports a lifespan exception as a
    # traceback under "Application startup failed. Exiting.", which is complete
    # but easy to lose in a container log; the CRITICAL line is the one that
    # survives an aggregator and is worth grepping for. Neither carries the
    # secret or the URL itself — see production_boot_failures().
    # Said out loud at boot, not refused: mail is optional (the on-screen
    # activation link and the console transport both work without it), but an
    # operator wondering why nobody gets their email should find the answer in
    # the first screen of the log rather than in a MailLog row later.
    if settings.mail_configured:
        log.info("outbound mail: Amazon SES from %s", settings.ses_from_address.strip())
    else:
        log.info(
            "outbound mail: NO TRANSPORT (SES_FROM_ADDRESS blank) — activation, reset and "
            "confirmation emails are logged here and kept in app.mail_transport.outbox; "
            "activation links can still be issued on screen"
        )
    # The permanent document archive, said at boot for the mail transport's
    # reason and a sharper one. A blank bucket is a SUPPORTED configuration and
    # the API works perfectly without it -- uploads store, download and delete
    # exactly as before. What it silently costs is the only copy of an uploaded
    # file that outlives `backupRetentionDays`, and the failure is invisible
    # from every screen in the product: nothing 500s, nothing looks wrong, and
    # the loss is discovered the first time somebody asks for a document that
    # was deleted more than 35 days ago -- by which point it is unrecoverable
    # everywhere.
    #
    # `archive_bytes` returns False and logs nothing on this path, deliberately,
    # because a line per upload would be noise. So this is the one place the
    # absence is stated, and `python -m app.archive_documents` repeats it nightly.
    if document_archive.archive_enabled():
        log.info(
            "document archive: %s — every stored file is copied there as it is accepted",
            settings.document_archive_bucket.strip(),
        )
    else:
        log.warning(
            "document archive: NOT CONFIGURED (DOCUMENT_ARCHIVE_BUCKET blank) — uploaded "
            "marksheets, certificates, resumes, signatures and recordings have NO copy "
            "outliving the volume's %d-day backup lifecycle, and a file deleted from the "
            "website is unrecoverable after it",
            35,
        )
    boot_failures = settings.production_boot_failures()
    if boot_failures:
        detail = "\n".join(f"  * {problem}" for problem in boot_failures)
        refusal = (
            f"REFUSED to start: ENV={settings.env.strip() or '(blank)'} is a production "
            f"environment and this process is holding development credentials.\n{detail}"
        )
        log.critical(refusal)
        raise RuntimeError(refusal)

    # 2) Close the interview rows the PREVIOUS process died holding.
    #
    # Interview finalization has three layers (docs/interview-engine-v3.md §6.7)
    # and the first two — the relay's own finalizer and the router's `finally:`
    # backstop — both need this process to still be alive to run. Neither
    # survives a `kill -9`, an OOM kill, or a container cut off mid-interview,
    # and each of those leaves `interview_sessions.status = 'running'` forever:
    # a row that lies, sitting on a student's history screen next to their real
    # interviews.
    #
    # Startup is where this belongs, with no cron at all, because a worker that
    # has just booted is precisely the process that knows the previous one died.
    # A deploy and a crash-restart — which is essentially every way this happens
    # — therefore sweep themselves. The stale-heartbeat predicate is what makes
    # it safe to run here with other workers live: a healthy interview's
    # heartbeat is a minute old, and the grace window is 1200 s.
    #
    # NOT purge_expired. That one DELETES student data on a clock, and running it
    # from every boot would make the amount destroyed a function of how often
    # someone restarts the API. Cron wiring for the reapers stays a deployment
    # concern, as app/retention.py has always said of its own jobs.
    #
    # Wrapped whole, and on a thread: this is a synchronous psycopg call, so it
    # would otherwise block the event loop, and a database that is slow or down
    # at boot must degrade to "orphans get swept next restart" rather than to "no
    # dashboard". The whole app does not fall over for a bookkeeping sweep.
    try:
        swept = await asyncio.to_thread(_sweep_orphaned_interviews)
        if swept:
            log.info("Startup sweep finalized %d orphaned interview session(s).", swept)
    except Exception:
        log.exception(
            "Startup sweep of orphaned interview sessions failed; any "
            "interview_sessions row left 'running' by a previous process stays "
            "that way until the next successful sweep. Serving anyway."
        )

    try:
        yield
    finally:
        # Ask live interviews to close with a real code and reason rather than
        # being torn down as a bare 1006.
        #
        # BACKSTOP ONLY, and honestly so: uvicorn's Server.shutdown() closes
        # every live WebSocket (queueing websocket.disconnect(1012)) BEFORE it
        # sends the lifespan shutdown event, so under uvicorn this set is
        # already empty here. The standalone relay hijacked SIGINT/SIGTERM to
        # get ahead of that; this process serves the whole dashboard and taking
        # over its signals to improve one feature's close code is a bad trade --
        # especially as the client already has a sentence for 1012 ("the
        # interview server is restarting"). This covers an ASGI server whose
        # shutdown ordering differs, and a shutdown with no signal at all.
        interview.shutdown_interviews()


# /docs, /redoc and /openapi.json follow settings.docs_exposed: served in dev —
# AGENTS.md sends a newcomer to http://127.0.0.1:3300/docs on their first run —
# and OFF in production unless the operator sets DOCS_ENABLED on purpose.
#
# The endpoints stay authenticated either way, so this is not a security control;
# it is the difference between an attacker guessing at routes and reading the
# list. The schema names every path, every field, every enum value and every
# staff-only surface, and it is served before any login.
#
# All three are named even though `openapi_url=None` alone would disable the two
# UIs as a side effect: leaving that as an implicit consequence is how one of
# them comes back the next time someone adds a docs option.
_DOCS_URL = "/docs" if settings.docs_exposed else None
_REDOC_URL = "/redoc" if settings.docs_exposed else None
_OPENAPI_URL = "/openapi.json" if settings.docs_exposed else None

app = FastAPI(
    title="REEP API (Python / FastAPI)",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=_DOCS_URL,
    redoc_url=_REDOC_URL,
    openapi_url=_OPENAPI_URL,
)

# Credentials are sent (the session cookie), so the origin must be explicit, not "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Traceability: every request carries an X-Request-ID edge to log line — see
# app/traceability.py. Added AFTER CORSMiddleware so it runs INSIDE it and the
# echoed header rides on responses CORS has already stamped.
app.add_middleware(RequestTraceMiddleware)


_generate_openapi = app.openapi


def _openapi_with_honest_422() -> dict:
    """The published contract, with 422's `detail` as it is actually sent.

    FastAPI documents every 422 as `HTTPValidationError`, whose `detail` is an
    ARRAY of validation errors. That is what a schema failure sends, but about
    150 handlers also raise `HTTPException(422, "a sentence")`, whose `detail`
    is a STRING, so the document described half of what the API returns
    (DEF-008; Schemathesis `response_schema_conformance`). The SPA's `detailOf`
    already reads both. Changing 150 responses would have broken nobody's
    reading and fixed nothing; saying so in the contract fixes the contract.
    """
    schema = _generate_openapi()
    error = schema.get("components", {}).get("schemas", {}).get("HTTPValidationError")
    detail = (error or {}).get("properties", {}).get("detail")
    if detail is not None and "anyOf" not in detail:
        error["properties"]["detail"] = {
            "title": "Detail",
            "anyOf": [
                {"type": "array", "items": {"$ref": "#/components/schemas/ValidationError"}},
                {"type": "string"},
            ],
        }
    return schema


app.openapi = _openapi_with_honest_422


@app.exception_handler(DataError)
async def _unstorable_value(request: Request, exc: DataError) -> JSONResponse:
    """A value the database refused to STORE is the caller's input, never a
    server fault, so it is a 422 rather than a 500.

    Postgres raises DataError only for the value itself: a NUL byte or an
    invalid escape in text, a number past its column's range, a string longer
    than its column, text that is not a valid date or number. If a value the
    SERVER computed ever trips one of these, this handler would blame the
    caller for it; the warning below is logged so that case is still visible. Before this, a
    student profile or a catalogue import carrying one came back as an Internal
    Server Error, and the office read that as REEP being down (DEF-007,
    testing/docs/05-incident-reports.md; found by Schemathesis). Handlers that
    can validate earlier still should: this is the floor, not the fix.

    The log line names the error class and the path and NOTHING ELSE.
    `str(exc)` carries the statement's bound parameters, which here can be a
    student's marks or address, so it must never reach a log (rule 1 applies
    to telemetry too). The session is rolled back by `get_db`'s close.
    """
    logging.getLogger("reep.data_error").warning(
        "422 for an unstorable value: %s on %s %s",
        type(getattr(exc, "orig", exc)).__name__,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "A value in this request cannot be stored: it may be too long, "
            "outside the allowed range, in the wrong format, or contain a NUL character. "
            "Check the values you sent."
        },
    )


# Response hardening on every HTTP response this process serves. These belong
# on the application, not only on whatever proxy happens to sit in front —
# apps/web/nginx.conf carries the same headers for the SPA's files, but the
# documented dev setup and any future ingress talk to uvicorn directly.
#   nosniff       — the download endpoints serve student-supplied bytes with a
#                   stored mime type; without this a browser is free to sniff
#                   its own, which is how "it's just a PNG" becomes markup.
#   DENY          — no screen in this app is ever legitimately framed, and a
#                   framed login is a clickjacking kit's first ingredient.
#   HSTS          — only outside the dev allowlist (the same
#                   insecure_cookies_allowed gate the Secure cookie flag keys
#                   on): a laptop on http://localhost must not have this host
#                   pinned to HTTPS by a stray header.
# setdefault, never overwrite: a handler that deliberately sets one of these
# knows more than this blanket does.
@app.middleware("http")
async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if not settings.insecure_cookies_allowed:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

# WHY THE PUBLIC FORM REFUSED SOMEBODY, IN THE LOG (2026-10-01). About seventy
# POST /api/register calls on one morning were refused with 422 in two
# milliseconds, and nothing anywhere said which box: the access line carries
# the status and the reason went back to the phone alone. Field NAMES and
# pydantic's error TYPE only — never `input`, which is the value the applicant
# typed (an address, a phone number, a USN). Every other route keeps FastAPI's
# own handler untouched; the response body is unchanged for this one too.
@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
    if request.method == "POST" and request.url.path == "/api/register":
        registration.log_form_refusal(exc.errors())
    return await request_validation_exception_handler(request, exc)


# Health is infra liveness — unprefixed at /health.
app.include_router(health.router)
# agent + interview already carry /api in their own prefix
# (/api/agent, /api/interview).
app.include_router(agent.router)
app.include_router(interview.router)
# The voice-assistant platform (app/voice_platform): Admin CRUD under
# /api/platform/admin, call sessions under /api/platform/calls, and the media
# bridge at both /ws/media-bridge and /api/platform/media-bridge.
app.include_router(platform_admin.router)
app.include_router(platform_calls.router)
app.include_router(platform_bridge.router)
# Domain routers mount under a single /api prefix, so the whole surface the
# Angular client calls lives under /api — matching environment.apiBase and the
# dev proxy (apps/web/proxy.conf.json), with no path rewriting.
app.include_router(auth.router, prefix="/api")
# Credentials — activation, forgot, reset, change. Separate from sign-in.
app.include_router(passwords.router, prefix="/api")
app.include_router(onboarding.router, prefix="/api")
# Canonical v1 surface. The legacy /api surface remains during the expand/contract window.
app.include_router(auth.router, prefix="/api/v1")
app.include_router(redesign.router, prefix="/api/v1")
app.include_router(student.router, prefix="/api")
# The v2 UI's three new screens (ledger, English baseline, mentor meeting log)
# and the landing programme cards. Its own module so routers/student.py — the
# file every other student change touches — did not grow another 600 lines; it
# shares the /student prefix, so the client sees one flat surface.
app.include_router(student_programme.router, prefix="/api")
app.include_router(mentor.router, prefix="/api")
# The staff side of the v2 screens — a mentor or the Main Admin reading a student's
# ledger and English baseline. Separate from mentor.py so that file is untouched,
# and every endpoint in it goes through _assert_can_access_student (rule 2).
app.include_router(mentee_records.router, prefix="/api")
app.include_router(console.router, prefix="/api")
# B9 - mentor mapping: the load board, the unassigned pool, the assignment
# itself and the history it leaves. Moved OUT of console.py, which is a file of
# programme-wide aggregates; this is the write that decides rule 2's scope key,
# and it needs to be findable by somebody asking where mentor assignment is
# decided. Same /admin prefix and the same paths - see the module docstring.
app.include_router(admin_mentoring.router, prefix="/api")
# Main Admin: the institutional write layer (colleges, departments, batches,
# seating a student, and the users.designation/department columns that had no
# writer at all). require_admin inside, same as every other admin surface.
app.include_router(admin.router, prefix="/api")
app.include_router(admin_students.router, prefix="/api")
# B4.3/B4.4 - promote and graduate a whole batch. Its own module beside the
# roster edits, not inside them: a promotion is not "the single edit,
# repeated" - it writes a history row per student, reads the course's length
# and carries an effective date - and `admin_students.py` is already the file
# every roster change touches.
app.include_router(admin_promotion.router, prefix="/api")
# B4.5 — the whole of one student in one read, so the detail screen stops
# fetching the entire roster to pick a row out of it. Its own module: the
# roster edits are writes with a domain fence and an audit row on every one,
# and this is a read that touches ten tables and writes nothing.
app.include_router(admin_student_360.router, prefix="/api")
app.include_router(admin_student_record.router, prefix="/api")
app.include_router(admin_faculty.router, prefix="/api")
# 2026-09-16 - remove / restore / delete-for-good, for students, faculty and
# colleges, plus the emailed code the permanent ones demand. Its own module
# for the reason the purge modules are their own: a destructive act should
# be found by looking for it, not by reading an edit router to the end.
app.include_router(admin_deletion.router, prefix="/api")
app.include_router(admin_mail.router, prefix="/api")
# B8.1 - spreadsheet imports. Its own module for admin_promotion.py's reason:
# `console.py` and `admin.py` are ~1700 lines each and this is a new surface,
# not a variation on an existing one. Today it answers the history; preview,
# apply, the error report and the templates land with the parser.
app.include_router(admin_imports.router, prefix="/api")
# Governance: capability grants for staff (deny past the role baseline) and
# student feature overrides (allow until switched off, at any rung of the
# hierarchy). Two instruments with OPPOSITE defaults, which is why they are one
# module and not merged into admin.py's institutional writes.
app.include_router(governance.router, prefix="/api")
app.include_router(leave.router, prefix="/api")
# Faculty upskilling (own certificate uploads) and the alumni area (first-login
# profile + jobs sheet). Both scope every row to the signed-in user; the staff
# one gates on mentor.require_mentor, the alumni one on the ALUMNI role.
app.include_router(staff_upskilling.router, prefix="/api")
app.include_router(signature.router, prefix="/api")
app.include_router(leave_paper.router, prefix="/api")
# B10's three additions, each in its OWN module for leave_paper's reason: the
# form's submit and decide paths live in routers/leave.py, the owner asked for
# them to be left exactly as they are, and that file grows nothing. All three
# import the scope rule from it rather than restating one.
#   leave_attachments  the papers that came with a request (B10.3)
#   leave_alternate    who is covering, and their acceptance (B10.6)
#   leave_policy       allowances and the college calendar (B10.2) — two
#                      routers, because the office's CRUD is /api/admin/... and
#                      the person's own two reads are /api/leaves/...
app.include_router(leave_attachments.router, prefix="/api")
app.include_router(leave_alternate.router, prefix="/api")
app.include_router(leave_policy.leave_router, prefix="/api")
app.include_router(leave_policy.admin_router, prefix="/api")
app.include_router(alumni.router, prefix="/api")
# The Skills & Badge dashboard: the student half shares the /student prefix
# (badges, growth, leaderboards); badge_verification carries the staff review queue,
# manual awards, assessment entry and cohort views, under /mentor and /admin as
# rule 2 dictates.
app.include_router(badges.router, prefix="/api")
app.include_router(badge_verification.router, prefix="/api")
# B13 - the catalogue, per college and per course: the approved certifications
# (MOVED HERE FROM badge_verification, same URLs), which of the 48 code-defined
# badges apply to a programme, the stage rules a promotion reads, the copy
# between two programmes and the subject CSV import. Its own module because it
# is the one admin surface that names no student at all: every fence in it is
# `admin.catalogue` plus B1.2's reach, and none of it is rule 2's.
app.include_router(admin_catalogue.router, prefix="/api")
app.include_router(admin_role_preferences.router, prefix="/api")
app.include_router(registration.router, prefix="/api")
# B5.1 — the Specialization Matrix as rows. MOUNTED BEFORE THE BANK ROUTER, and
# the order is load-bearing rather than tidy: both live under
# /api/admin/interview-questions, and FastAPI matches the first route whose path
# and method fit. `tracks` is a single path segment, so a request to
# /admin/interview-questions/tracks would otherwise be matched by the bank's
# `/{question_id}` routes and answer "Question not found." for a track list.
app.include_router(admin_interview_tracks.router, prefix="/api")
app.include_router(interview_bank.router, prefix="/api")
app.include_router(swoc.router, prefix="/api")
# B6.1/B6.4 — the college's interview policy, the cap reset and the student's
# policy card. Its two routers are declared ALREADY PREFIXED (/api/admin and
# /api/interview), the same shape `interview_records` uses and for the same
# reason: the student card belongs under /api/interview beside the socket it
# describes, and mounting it under the `prefix="/api"` the domain routers use
# would serve it at /api/api/interview/policy — every request 404s, nothing
# raises, and the only symptom is a Start button that never learns the caps.
app.include_router(interview_policy.admin_router)
app.include_router(interview_policy.student_router)
# B2.7: the audit trail, read back. A READER ONLY — `record_change` writes
# redesign_audit_events from twenty-seven endpoints and nothing in this module
# writes a domain row. Mounted last among the admin surfaces because it is about
# all of them.
app.include_router(audit.router, prefix="/api")
# Student fees (2026-10-29): the Main Admin raises fees and records payments,
# the student pays through Razorpay, and Razorpay confirms on the webhook.
app.include_router(admin_fees.router, prefix="/api")
app.include_router(student_fees.router, prefix="/api")
app.include_router(student_fees.webhook_router, prefix="/api")


# --- The interview record endpoints (Interview Engine v3 §7) -----------------
#
# `app/routers/interview_records.py` declares its routers ALREADY PREFIXED — one
# at /api/interview and one at /api/mentor — so that `app/routers/mentor.py` is
# not touched at all by this work. That is the whole reason the module exists as
# a separate file, and it is why these are included bare rather than under the
# `prefix="/api"` the domain routers use.
#
# Two things make this include look unlike its neighbours, and both are
# deliberate:
#
# 1) THE IMPORT IS GUARDED, and on `Exception` rather than `ImportError`. The
#    module lands in the same wave as this wiring and from a different hand. A
#    missing or half-written module must degrade to "the interview history
#    screen 404s" — one feature — and never to "the dashboard does not boot",
#    which is every student's marks, attendance and jobs board gone because a
#    read-only history endpoint was not ready.
#    `except ImportError` would have covered only the ONE way this fails that
#    nobody actually hits once the file exists. A module that is present and
#    broken raises SyntaxError, NameError or AttributeError at import — none of
#    them an ImportError — and each would have taken the whole process down at
#    boot, which is precisely the outcome this guard was written to prevent.
#    Logged with the traceback (ERROR, not WARNING) because from here on a
#    failure here means a real defect in a module that is supposed to exist, and
#    "which line" is the first thing whoever reads this will need. The message
#    names exactly what is unavailable so this is diagnosed from the log rather
#    than from a student's screenshot.
#
# 2) THE ROUTERS ARE DISCOVERED, not named. §7 specifies two of them and does not
#    fix their variable names; guessing `router` and finding `student_router`
#    would mount NOTHING while raising nothing — a silently missing API surface,
#    which is the exact failure this codebase keeps writing comments about. Every
#    public APIRouter the module exposes is mounted, and the names and prefixes
#    are logged, so what got served is a fact in the log rather than an
#    assumption. The `/api/` requirement is a real contract check, not a filter
#    of convenience: the Angular client's `environment.apiBase` and the dev proxy
#    (apps/web/proxy.conf.json) forward `/api` and nothing else, so a router
#    mounted anywhere else is unreachable from the app that needs it.
try:
    from .routers import interview_records
except Exception:
    log.exception(
        "app/routers/interview_records.py is not importable: the interview "
        "record endpoints (GET /api/interview/sessions, the consent endpoints, "
        "and GET /api/mentor/students/{id}/interviews) are NOT served. "
        "Interviews themselves still run and still record. Nothing else is "
        "affected."
    )
else:
    _mounted: list[str] = []
    for _name, _candidate in vars(interview_records).items():
        if _name.startswith("_") or not isinstance(_candidate, APIRouter):
            continue
        if not _candidate.prefix.startswith("/api/"):
            log.error(
                "interview_records.%s is an APIRouter with prefix %r, which is "
                "outside /api — NOT mounting it, because the Angular client and "
                "the dev proxy only forward /api and it would be unreachable "
                "there anyway. Give it its own /api prefix.",
                _name,
                _candidate.prefix,
            )
            continue
        app.include_router(_candidate)
        _mounted.append(f"{_name} -> {_candidate.prefix}")
    if _mounted:
        log.info("Interview record routers mounted: %s", ", ".join(_mounted))
    else:
        log.error(
            "app/routers/interview_records.py imported but exposes no public "
            "APIRouter with an /api prefix — the interview record endpoints are "
            "NOT served."
        )


# --- The development-only MCP surface ----------------------------------------
#
# LAST, and deliberately so: fastapi-mcp reads the app's OpenAPI schema to build
# its tool list, so every router must already be included or the tools it
# publishes are a subset of the API by accident of ordering.
#
# Two gates, both in settings.mcp_enabled: the ENV must be on the development
# ALLOWLIST (never `not is_prod` — an unrecognised ENV has to lose the
# affordance, not gain it) and MCP_DEV_SURFACE must spell "true". The mount
# forwards the caller's session cookie into the real handlers, so on a host
# serving real students it would be a second front door with none of the rate
# limiting, revocation or audit the first one has.
#
# app/dev_mcp.py imports fastapi_mcp INSIDE its function: the package is in
# requirements-dev.txt only, the Dockerfile installs requirements.txt, and CI's
# dependency-completeness job imports every module under app/ against that same
# manifest. With the flag off this line is the only thing that runs.
if settings.mcp_enabled:
    from .dev_mcp import mount_dev_mcp

    mount_dev_mcp(app)
