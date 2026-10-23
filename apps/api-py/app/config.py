"""Application settings, read from the environment / apps/api-py/.env.

Field names map to env vars case-insensitively (database_url <- DATABASE_URL).
"""

from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Pin the env file to THIS app's directory. A bare ".env" resolves against the
# process CWD, which — run from the repo root — is the Next.js/Prisma .env, whose
# `postgresql://…?schema=public` URL selects psycopg2 (not installed) and carries
# a Prisma-only query param. This app reads its own file or nothing.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# The engines that may run a mock interview, and the whole of the allowlist
# INTERVIEW_ENGINE is validated against. Named here rather than spelled inside
# the validator so that adding one is a single edit and so the warning that
# rejects a typo can print the real list instead of a hand-maintained sentence.
_INTERVIEW_ENGINES = frozenset({"nova", "local"})

# Where an unrecognised INTERVIEW_ENGINE lands. Named rather than repeated, so
# the fallback in the validator and the field's own default cannot drift apart.
# They did drift once, when the third engine ("openai") was removed and the
# validator kept naming it: every deployment that had not set the variable would
# have been "corrected" to an engine that no longer existed.
_DEFAULT_INTERVIEW_ENGINE = "nova"

# Amazon Nova 2 Sonic, the speech-to-speech model behind INTERVIEW_ENGINE=nova.
# The id is the model itself and not an inference profile: the bidirectional
# streaming API is invoked with the bare model id in every AWS example, and a
# regional profile prefix ("apac.") is rejected there — which is why this is a
# separate setting from BEDROCK_MODEL rather than reusing it.
_DEFAULT_NOVA_SONIC_MODEL = "amazon.nova-2-sonic-v1:0"

# The institution's mail domain. One constant behind two settings
# (GOOGLE_ALLOWED_DOMAIN and ROSTER_EMAIL_DOMAIN) because they describe the same
# real-world fact: which addresses belong to this college. It is NOT a security
# boundary — the roster is (see app/google_auth.py).
_DEFAULT_COLLEGE_DOMAIN = "bgscet.ac.in"

# Every spelling of "this is production" an operator actually types. `is_prod`
# used to be `env == "prod"` exactly, which FAILS OPEN in the two places it now
# matters most: ENV=production — the spelling most deploy templates and PaaS
# dashboards use — would leave the session cookie without `Secure` AND leave
# password sign-in reachable on the internet. Recognising the synonyms is the
# cheap half of the fix; `password_login_allowed` below is the other half.
_PROD_ENV_NAMES = frozenset({"prod", "production", "prd", "live"})

# The environments allowed to keep their DEVELOPMENT AFFORDANCES — an
# ALLOWLIST, not the complement of _PROD_ENV_NAMES, because the two guards must
# fail in opposite directions. A typo'd ENV must mean "the cookie is Secure and
# the password door is shut", never "neither". ENV is "dev" by default and CI
# sets nothing, so the suite and every laptop keep the doors they already have.
#
# Two guards read it, both through _is_dev_env below: password sign-in
# (password_login_allowed) and a session cookie without `Secure`
# (insecure_cookies_allowed). The 2026-08 audit found the second keyed on
# `is_prod` instead, which is a NAME TEST: a `staging`/`uat`/`demo` box — real
# roster rows, real HTTPS, real students — is not one of the four prod
# spellings, so it was handed a sniffable session cookie because nobody had
# typed the magic word. (A third, worker_auth_optional, guarded the LiveKit
# voice worker's endpoints; that stack was removed from the repo.)
_DEV_ENV_NAMES = frozenset({"dev", "development", "test", "testing", "ci", "local"})

# The committed development credentials. They are FIELD DEFAULTS AND GUARD
# INPUTS in one place on purpose: the boot check in
# Settings.production_boot_failures compares against these constants, so a
# future edit to a default cannot leave the guard testing a string nobody uses
# any more. Both are also printed in .env.example, i.e. in the repository, i.e.
# known to everyone who has ever cloned it.
_DEV_DATABASE_URL = "postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_py"
_DEV_DB_PASSWORD = "reep_dev_password"
_DEV_AUTH_SECRET = "reep-dev-secret-change-me-in-production-0123456789abcdef"

# Substrings that mean "nobody has replaced this yet". A secret edited from the
# committed default by a couple of characters is not a new secret, and the
# equality test above would pass it. Kept deliberately short and specific:
# a real random secret can contain "secret", so that word is NOT on the list.
_PLACEHOLDER_SECRET_MARKERS = ("change-me", "changeme", "change_me", "your-secret")

# Minimum AUTH_SECRET length in production. HS256 signs with the raw bytes of
# this string and its digest is 32 bytes; a key shorter than the digest adds no
# strength beyond its own length, and a short one is guessable offline from a
# single captured cookie. 32 is also the floor .env.example has always claimed
# (">= 32 chars") — this only makes the file's own promise enforceable.
AUTH_SECRET_MIN_CHARS = 32

# How to replace it, quoted verbatim in every refusal below. An operator meeting
# a boot failure at 2am needs the command, not a policy.
_NEW_SECRET_HINT = (
    'Generate one and set AUTH_SECRET in apps/api-py/.env: '
    'python -c "import secrets; print(secrets.token_hex(32))". '
    "Changing it signs out every live session, which is the correct trade."
)


def _is_dev_env(env: str) -> bool:
    """Whether `env` names an environment that may keep its dev affordances.

    One allowlist behind three fail-closed guards (see _DEV_ENV_NAMES). Anything
    unrecognised — "staging", "uat", a typo, an empty string from a half-written
    deploy template — is treated as production-like: it loses the password door,
    it gets `Secure` cookies, and it must authenticate its voice worker.
    """
    return env.strip().lower() in _DEV_ENV_NAMES


def _env_true(value: str) -> bool:
    """A string flag read as a boolean: "1", "true", "yes", "on" (any case)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # BOTH of the next two defaults are development credentials published in this
    # repository, and BOTH are refused at boot when ENV is production — see
    # production_boot_failures() at the bottom of this class for what each one
    # costs if it reaches a real deployment.
    database_url: str = _DEV_DATABASE_URL
    # Signs the HS256 `reep_session` cookie (app/security.py) AND derives the
    # OAuth flow-cookie key (app/google_auth.py). Whoever knows it IS every user:
    # a forged {"role":"ADMIN"} claim reads every student's marks, attendance
    # and USN, and no login, no Google round-trip and no DB row is involved.
    auth_secret: str = _DEV_AUTH_SECRET
    web_origin: str = "http://localhost:4200"

    # --- outbound mail: activation, password reset, registration confirmation ---
    # Blank = no transport: messages are logged in full and kept in
    # app/mail_transport.outbox (dev, CI, and a fresh deployment before IT has
    # verified the domain). Set to a verified SES identity to send for real;
    # SES authenticates through the task role, so there is no key to paste.
    ses_from_address: str = ""
    ses_region: str = ""  # falls back to AWS_REGION, then ap-south-1
    # The configuration set every message is sent UNDER, and the reason it is a
    # setting rather than a fact about the identity.
    #
    # SES applies a configuration set two ways: the one named on the send call,
    # or the identity's DEFAULT. Production has `reep-transactional` set as the
    # default on the `sast-skills.com` identity, which is why bounce, complaint
    # and delivery events reach the `reep-ses-notifications` topic today with
    # this field blank. But a default is a property of the IDENTITY, edited in a
    # different console screen from the one anyone looks at when they think
    # about mail — and the failure when it goes is the quiet kind: every send
    # still succeeds, the event destination stops firing, and both reputation
    # alarms sit at INSUFFICIENT_DATA looking exactly like a quiet week. Nothing
    # errors and nothing is logged. Naming the set on the CALL makes that
    # unsurvivable by accident, because SES REFUSES a send naming a
    # configuration set that does not exist: the tracking cannot be lost
    # silently, only loudly.
    #
    # Blank = name nothing and let the identity's default decide, which is what
    # dev and CI want and what every deployment did before this existed.
    ses_configuration_set: str = ""
    # B10.5's leave notifications. They still ship OFF, and the default is still
    # the design — but the REASON changed on 2026-09-15, so read this and not
    # the sentence it replaced.
    #
    # B3.7 has shipped. The api task carries a real `SES_FROM_ADDRESS` and mail
    # leaves through a verified identity holding production access, so the old
    # note — "this stays false until an operator has a transport" — is spent.
    # Production turns it on from the task definition (infra/cdk's
    # `leaveMailEnabled`), and the CDK app REFUSES to synthesise that flag
    # without a sender, so the switch and the transport cannot come apart.
    #
    # The DEFAULT stays false, and that is not leftover caution: it is the right
    # value for every machine WITHOUT a transport. With `ses_from_address` blank
    # — every development box, every CI run — `mail_transport.send` logs the
    # message and appends it to a bounded in-memory `outbox` that reaches
    # NOBODY. A leave notification on by default there writes a `mail_logs` row
    # saying SENT for a message the applicant never received, and that row is
    # the only thing anyone looks at afterwards: the exact shape of the failure
    # that killed PENDING_VERIFICATION. No screen may say "the applicant has
    # been emailed" while this is false.
    leave_mail_enabled: bool = False
    # Skill-claim notifications (2026-09-17): the mentor is mailed when one of
    # their mentees files a badge claim, and the student is mailed when it is
    # decided (app/badge_mail.py). THREE STATES, `password_login`'s idiom, and
    # NOT `leave_mail_enabled`'s plain boolean, on purpose:
    #
    #   "true"          on, whatever the transport -- a developer reading the
    #                   outbox, and the test suite;
    #   "false"         off, whatever the transport -- the incident switch;
    #   anything else   DERIVED: on exactly when a real transport exists
    #                   (`mail_configured`, i.e. SES_FROM_ADDRESS is set).
    #
    # `leave_mail_enabled`'s own comment says what its false default protects:
    # a machine with NO transport, where a notification switched on writes a
    # `mail_logs` row reading SENT about a message that reached nobody. On such
    # a machine the derived answer here is off, so the same machine is
    # protected -- and on the production task, which already carries
    # SES_FROM_ADDRESS, the notifications are on without a second variable
    # that has to be remembered in a task definition. That is the password
    # door's argument ("two sources of truth for one door is how a login form
    # ends up 403ing over keys in circulation") applied to mail: the act that
    # gives this deployment a transport is the decision to use it.
    badge_mail_enabled: str = ""
    # Link lifetimes, from the agreed plan: activation 7 days (a new staff
    # member may not check mail today); reset 1 hour (the account exists and
    # may already be under attack); an application's confirmation 24 hours.
    activation_link_hours: int = 168
    password_reset_minutes: int = 60
    # A sign-in one-time code (the second step on password sign-in, see
    # otp_login_required) lives this long. Short on purpose: the code is six
    # digits, so its safety is the pairing of a small window with the per-account
    # attempt budget in app/routers/auth.py.
    otp_code_minutes: int = 10
    # What the ROOT logger emits, so `app/` log lines are visible at all. See
    # app/main.py's basicConfig for what their absence cost. DEBUG is refused
    # a hard floor there for the `websockets` library specifically; this
    # setting does not lift it.
    log_level: str = "INFO"
    email_verification_hours: int = 24
    # "Forgot password" gets its own caps, per address and overall, or it is a
    # way to send someone a hundred emails.
    forgot_password_per_address_per_hour: int = 3
    forgot_password_global_per_hour: int = 100
    env: str = "dev"
    # Whether /docs, /redoc and /openapi.json are mounted. True here, and OFF in
    # production unless the operator sets DOCS_ENABLED on purpose — `docs_exposed`
    # below is what app/main.py actually reads, and it explains why the default
    # cannot be the whole answer. The endpoints stay authenticated either way;
    # what an open schema hands out is the MAP — every route, every field name,
    # every enum value, staff-only ones included — which is what turns "find an
    # endpoint" into "read the list".
    docs_enabled: bool = True
    # Whether POST /api/auth/login answers on an environment that is NOT dev/CI
    # — i.e. whether this deployment offers email + password alongside Google.
    #
    # A string, not a bool, and blank by default: the same idiom as
    # llm_allow_remote_student_data, so a blank line in .env, an unset variable
    # or a typo all mean OFF. Turning it on is one deliberate edit that has to
    # spell "true"; nothing about upgrading gets a deployment this door.
    #
    # WHAT THIS DOES NOT DO, and must never be changed to do: it does not give
    # anybody a password. Accounts minted by app.grant_access and
    # app.seed_roster carry the unusable SSO_ONLY_PASSWORD_HASH sentinel, and
    # they keep it until an operator runs `python -m app.set_password` for one
    # named account. And app.seed still refuses to run when ENV=prod, so the
    # published admin123 / mentor123 / student123 logins cannot exist on a
    # production host for this flag to let in. Those two facts are what make
    # this a door rather than a hole: opening it admits exactly the accounts an
    # operator has deliberately issued a password to, and no others.
    password_login: str = ""
    # Whether this process mounts the dev-only MCP surface at /mcp, which lets a
    # developer's tools call the API's READ endpoints as a signed-in role while
    # they build a screen against it (docs/redesign-2026-09/08-tooling-mcp.md).
    #
    # A string that has to spell "true", the same idiom as password_login and
    # otp_required, so a blank line, an absent variable or a typo all mean OFF.
    # Read only through `mcp_enabled`, which ALSO requires a dev environment:
    # this mount forwards the caller's session cookie into the real handlers, so
    # on a production host it would be an unauthenticated-by-default door into
    # every read the cookie's owner can perform.
    mcp_dev_surface: str = ""
    # Whether a correct password is followed by a SECOND STEP: a six-digit code
    # emailed to the account, which must be posted to /api/auth/login/code before
    # a session is issued. Same idiom as password_login — a string that has to
    # spell "true"; blank, absent or a typo all mean OFF. Read only through
    # `otp_login_required` below, which also refuses to apply it on dev/CI.
    otp_required: str = ""
    # Sessions are stateless 12-hour HS256 JWTs, so `POST /api/auth/logout`
    # deleting the cookie does nothing to a token that was already copied (audit
    # M8). A revocation deny-list closes that; this is how long one decision may
    # be cached in-process before the DB is asked again — i.e. the window in
    # which a logged-out token still works. 60 s keeps "log out on the shared lab
    # machine" honest within a minute at a cost of one small query per user per
    # minute. 0 is legal and means "ask the database every request", which is the
    # value to use the day a per-worker cache looks like the wrong trade.
    auth_revocation_cache_seconds: int = 60

    # --- SQLAlchemy connection pool (app/db.py) --------------------------------
    # These used to be SQLAlchemy's silent defaults (pool_size=5, max_overflow=10,
    # pool_timeout=30), which made 15 connections the whole app's ceiling with no
    # env var to raise it — the 2026-08 scalability audit's first critical. Every
    # sync endpoint holds one connection for its whole handler, so two students
    # loading the 10-fetch dashboard at once already wanted 20. The new defaults
    # fit a single dev process; a production deployment sizes them per worker so
    # that workers x (pool_size + max_overflow) stays under Postgres
    # max_connections (docker-compose.prod.yml does this arithmetic in comments).
    # pool_timeout drops from 30 to 5: a request that cannot get a connection in
    # 5 s is already part of an incident, and making it queue for 30 more only
    # converts one slow endpoint into a wall of hung requests.
    db_pool_size: int = 20
    db_max_overflow: int = 20
    db_pool_timeout_s: int = 5

    # --- Google sign-in (OIDC authorization-code flow) -------------------------
    # Sign-in is Google-only for every role. These credentials decide only WHO
    # GOOGLE SAYS YOU ARE; they decide nothing about access. The roster in the
    # `users` table is the allowlist — a verified Google account with no user row
    # is refused — so a leaked client id buys an attacker an identity we then
    # look up and reject, not an account.
    #
    # From console.cloud.google.com -> Credentials -> "OAuth client ID", type
    # "Web application". The SECRET is a real credential: it is used only on the
    # server-to-server token exchange (POST oauth2.googleapis.com/token) and must
    # never be serialised into a page, a redirect or a log line.
    #
    # BLANK IS OFF, the same contract OPENAI_API_KEY has for the interview: the
    # sign-in capability probe reports it unconfigured and the login screen keeps
    # the Google button disabled, instead of sending a student out of the app to
    # a Google error page nobody here can explain.
    google_client_id: str = ""
    google_client_secret: str = ""
    # OPTIONAL OVERRIDE, blank on nearly every deployment. Blank means
    # app/google_auth.py:redirect_uri() derives it as WEB_ORIGIN + CALLBACK_PATH,
    # which is the right answer whenever the browser reaches the API through the
    # web origin. Set it only when the public URL is not WEB_ORIGIN (a proxy in
    # front, a separate API host).
    #
    # Whatever the value, it must be BYTE-IDENTICAL to an "Authorised redirect
    # URI" on the OAuth client AND be the origin the BROWSER is on — not the one
    # FastAPI sees. apps/web/proxy.conf.json sets changeOrigin:true, so this
    # process is told `Host: localhost:3300` while the student is on :4200; a
    # redirect_uri built from the inbound request (request.url_for) would come
    # out :3300 and Google would answer redirect_uri_mismatch on every sign-in.
    # Composition lives in ONE function in app/google_auth.py, not here, because
    # the authorize request and the token exchange must send the same string and
    # Google rejects the exchange when they differ.
    google_redirect_uri: str = ""
    # The college's mail domain(s), comma-separated. READ THE NAME AS A LABEL,
    # NOT A FENCE: nothing in the sign-in path refuses on it, and nothing should
    # start to. The roster is the allowlist (app/google_auth.py explains why the
    # `hd` claim is deliberately not used — a domain test on top of the roster
    # can only add a second way to lock out someone who IS enrolled, and
    # app/grant_access.py exists precisely to admit staff whose address is not
    # on the student domain at all). Its two real jobs: it is the default
    # `roster_domain` derives addresses from, and its first entry is the string
    # GET /api/auth/sso/status hands the login screen so the copy can say
    # "the one ending @bgscet.ac.in" instead of "@your college".
    google_allowed_domain: str = _DEFAULT_COLLEGE_DOMAIN
    # The USN -> email convention: 1MP25MDM01 -> 1mp25mdm01@bgscet.ac.in (local
    # part = the USN, lowercased — the shape app/seed.py already seeds as
    # 1bg24mba045@bgscet.ac.in). This is the ONE piece of the design that is a
    # guess about the outside world: if the college's real convention differs by
    # a single character then every student on the roster is locked out on day
    # one, and that fix must be a .env edit, never a code change plus a rebuild.
    # COLLEGE_EMAIL_DOMAIN is accepted as an alias because that is the name
    # app/seed_roster.py documents in its --help. Blank -> the first
    # GOOGLE_ALLOWED_DOMAIN entry, so a single-domain college sets one variable.
    roster_email_domain: str = Field(
        "", validation_alias=AliasChoices("ROSTER_EMAIL_DOMAIN", "COLLEGE_EMAIL_DOMAIN")
    )

    # How many mentees one faculty mentor is expected to carry. The Mentor row
    # has no capacity column and adding one is a migration for a number that is
    # a programme POLICY, not a fact about any one mentor: every mentor shares
    # it, and the admissions office changes it between intakes. The assignment
    # screen derives "N free" and "at capacity" from this; it is advisory
    # (nothing refuses an assignment past it), because an admin who chooses
    # to overload one mentor in a thin year should not have to edit .env first.
    mentor_capacity: int = 20

    # --- the programme's clock, and the Time Allocation Ledger's lock ---
    #
    # THE ZONE THE STUDENTS' DAY RUNS IN. The container runs UTC and India is
    # five and a half hours ahead, so `date.today()` in a handler is yesterday
    # for the whole of 00:00–05:30 IST — which is when a student who fills the
    # ledger in last thing at night presses Save. app/clock.py reads this; see
    # its docstring for the two rules that depend on it. An unknown name falls
    # back to UTC with a warning rather than refusing to boot.
    programme_timezone: str = "Asia/Kolkata"
    # HOW LONG A LEDGER DAY STAYS OPEN AFTER IT ENDS, in calendar days of the
    # zone above. 2 means Monday can be written until the end of Wednesday;
    # 0 means the day itself and nothing later. After that the day is LOCKED:
    # its figures cannot be saved, submitted or overwritten by "copy
    # yesterday", and the screen says so. The ledger is a daily record and the
    # faculty read the weekly roll-up off it; with no lock at all a student
    # could type the whole semester in on the last night and the roll-up
    # would be fiction. It is a setting and not a policy row because it is
    # one number for the programme, like `mentor_capacity`.
    ledger_edit_window_days: int = 2

    # Universal LLM adapter (see app/ai/llm.py). Same names as the Next.js app,
    # so one set of keys drives both stacks. Any OpenAI-compatible provider.
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    # Was 300000 (5 minutes). Every LLM call runs synchronously in the shared
    # 40-thread pool, and generate_resume used to hold a pooled DB connection
    # across it too — so one hung provider could pin threads for 5 minutes each
    # and turn a slow model into a dashboard outage (audit critical #2). 60 s is
    # generous for a resume brief; a provider that has not answered in a minute
    # is not going to, and the deterministic fallback path exists precisely for
    # that day. Raise it only for a deliberately slow local model, and know that
    # the price is how long one request can occupy a worker thread.
    llm_timeout_ms: int = 60000
    # Per-user ceiling on LLM-backed HTTP requests (app/ratelimit.py): resume
    # generation and the retained /api/agent chat routes. The audit found these
    # had no cap at all, so one student's retry loop — or a 60-person class told
    # to "regenerate until it looks good" — was a self-inflicted DDoS on the
    # thread pool and an unbudgeted token bill. 5/minute is invisible to a human
    # clicking a button and a wall to a loop. The bucket is per worker process,
    # so N workers relax it to at most N x this — still a ceiling, and an
    # in-process dict costs nothing; a shared store is the day-two upgrade.
    llm_requests_per_minute: int = 5
    # A string (not bool) so a blank value is valid and safely means "off",
    # matching the Next.js gate where only the exact string "true" enables it.
    llm_allow_remote_student_data: str = ""

    # Per-provider keys for universal auto-select (app/ai/llm.py). Paste any one;
    # the adapter picks the first present. The explicit LLM_* trio above wins
    # over these when fully set.
    groq_api_key: str = ""
    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    cohere_api_key: str = ""
    gemini_api_key: str = ""
    # Sakana Fugu — OpenAI-compatible meta-router (https://api.sakana.ai/v1).
    sakana_api_key: str = ""

    # Amazon Bedrock (Nova) — a transport of its own in app/ai/llm.py, driven by
    # IAM credentials (task role / instance profile / env), never an API key.
    # Setting BEDROCK_MODEL is the whole opt-in; use the inference-profile id
    # for your region (e.g. "apac.amazon.nova-pro-v1:0"). The explicit LLM_*
    # trio still wins; the free-tier key auto-select is checked AFTER this, so
    # an AWS deployment cannot be silently routed to a free provider by a stray
    # key in the environment. Rule 1 applies unchanged: Bedrock is off-machine,
    # so student-data paths still require LLM_ALLOW_REMOTE_STUDENT_DATA=true —
    # a deliberate operator decision, made reasonable here because Bedrock does
    # not train on your account's traffic.
    bedrock_model: str = ""
    # Blank = boto3's own default chain (AWS_REGION / profile / IMDS).
    bedrock_region: str = ""

    # Sentry — THE observability + traceability tool for this deployment (errors
    # AND performance traces, api and web in one project each). Blank DSN =
    # fully off: the SDK is not initialised and every sentry_sdk call downstream
    # is a documented no-op, so a laptop pays nothing. send_default_pii stays
    # FALSE in app/main.py — Sentry receives stack traces, timings and the
    # request-id tag, never cookies, bodies or student text (rule 1's spirit,
    # applied to telemetry).
    sentry_dsn: str = ""
    # A string like llm_allow_remote_student_data, so a blank line in .env is
    # "use the default" rather than a pydantic float error — parsed and clamped
    # by the property below.
    sentry_traces_sample_rate: str = "0.2"

    @property
    def sentry_traces_rate(self) -> float:
        try:
            rate = float(self.sentry_traces_sample_rate.strip() or "0.2")
        except ValueError:
            return 0.2
        return min(1.0, max(0.0, rate))

    # --- the rest of the telemetry settings, read by app/observability.py ----
    # Blank = the Sentry environment IS `ENV` (prod -> "prod", dev -> "dev"):
    # one fact, one source. Set only to relabel telemetry without touching the
    # five other things ENV decides — and read docs/sentry-playbook.md's
    # environment table first, because an alert rule written for `production`
    # matches nothing this deployment sends.
    sentry_environment: str = ""
    # The commit this process was built from. The Dockerfile bakes it in as
    # SENTRY_RELEASE from a --build-arg; a blank stays blank (no release)
    # rather than falling back to a git revision nobody chose.
    sentry_release: str = ""
    # Continuous-profiling session sample rate, 0.0-1.0. Nothing is profiled at
    # 0.0. Named for the env var (SENTRY_PROFILES_SAMPLE_RATE); passed to the
    # SDK as profile_session_sample_rate, the current API — the legacy
    # transaction profiler stops at 30 s and cannot see an interview.
    sentry_profiles_sample_rate: str = "0.0"
    # RECOGNISED AND REFUSED. Rule 1: cookies, IPs and user context never leave
    # this process. A truthy value here is logged as an error at init and
    # changes nothing — it exists so the refusal is visible rather than silent.
    sentry_send_default_pii: str = "false"
    # Sentry structured logs (a separate pipeline from breadcrumbs and events).
    # Off: stdout already reaches CloudWatch. When on, app/telemetry_scrub.py's
    # before_send_log is the scrubber, because logs bypass before_breadcrumb.
    sentry_logs_enabled: str = "false"
    # One DSN per process, one project per DSN. The retention job runs on the
    # api's task definition and could read SENTRY_DSN — it must not (see
    # app/observability.py), so it has its own name, and blank means the job
    # reports nowhere and says so at the top of its log.
    sentry_jobs_dsn: str = ""
    sentry_interview_worker_dsn: str = ""
    # The daily job agent (app/job_scout.py, 2026-10-23): fresher jobs and
    # internships for every specialization, found on two official job-board
    # APIs and posted to the Jobs board each morning. BLANK KEYS ARE OFF, one
    # provider at a time: Adzuna needs both halves of its pair, Jooble one key.
    # They are server-to-server credentials and never reach a page or a log.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""
    # The companies' own public job feeds (Greenhouse, Lever, SmartRecruiters;
    # `job_sources.COMPANY_BOARDS`). They need NO key and no sign-up, so the
    # switch is a flag rather than a key's presence. Off unless set, so a
    # laptop running the job never reaches the internet by surprise.
    job_scout_company_boards: bool = False
    # THREE STATES, `badge_mail_enabled`'s idiom: "true" or blank runs the agent
    # whenever a provider has a key, "false" stops it with the keys still in
    # place (the incident switch: one bad batch of postings must not need a
    # secret edited and a task redeployed to stop the next one).
    job_scout_enabled: str = ""
    # An agent posting is closed this many days after the employer posted it.
    # A past `closes_on` hides nothing (app/jobs_visibility.py), and the
    # providers rarely give one, so without this the board would only grow.
    job_scout_max_age_days: int = 30
    # At most this many NEW postings per specialization per run, newest first,
    # so one busy morning cannot bury the office's own postings. Five is the
    # owner's number (2026-10-23): kept for 30 days, that is at most about 150
    # agent postings on one student's board, a table with no pagination.
    job_scout_new_per_specialization: int = 5

    @property
    def sentry_environment_name(self) -> str:
        return self.sentry_environment.strip() or self.env.strip() or "development"

    @property
    def sentry_profile_rate(self) -> float:
        try:
            rate = float(self.sentry_profiles_sample_rate.strip() or "0.0")
        except ValueError:
            return 0.0
        return min(1.0, max(0.0, rate))

    @property
    def sentry_pii_requested(self) -> bool:
        return _env_true(self.sentry_send_default_pii)

    @property
    def sentry_logs_on(self) -> bool:
        return _env_true(self.sentry_logs_enabled)

    # Knowledge-Base embedder (app/ai/embeddings.py). OPTIONAL: leave the base URL
    # blank and retrieval falls back to Postgres full-text — no embeddings needed.
    # The KB is public policy text (no student PII), so it may be sent to any
    # OpenAI-compatible /embeddings endpoint. When set, POSTs to
    # {embedding_base_url}/embeddings with embedding_model.
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: str = ""

    # Sampling temperature for the interviewer's responses, 0.0-2.0. UNSET by
    # default and sent only when explicitly configured: this codebase
    # deliberately omits unverified parameters (a rejected inference
    # configuration kills the session at the handshake), so the model's own
    # default is the behaviour unless an operator has a reason to tune.
    interview_temperature: float | None = None

    # Hard cap on one interview - a cost ceiling as much as a product decision:
    # audio tokens bill per second of a session that a forgotten browser tab
    # would otherwise hold open indefinitely. 15 minutes is a full mock round.
    interview_max_seconds: int = 900
    # No inbound audio at all for this long means the student left, the tab was
    # backgrounded (which suspends mic capture), or the mic died. Two minutes
    # survives a long thinking pause plus a reconnecting network.
    interview_idle_seconds: int = 120
    # PER-WORKER concurrency cap. N uvicorn workers give N times this number, so
    # it is deliberately NOT the 1000-student target: one CPython process cannot
    # carry 1000 sessions (~96 MB/s of PCM). Over the cap the socket is closed
    # immediately rather than queueing a student behind a clock that has not
    # started ticking.
    interview_max_sessions: int = 100
    # The other half of that cap, and the one the audit (H1) found missing: the
    # limiter above counts sessions and never asks WHOSE they are. One student
    # looping `new WebSocket('/api/interview')` from devtools takes every slot —
    # each socket authenticates, opens an upstream session, and BILLS from the
    # handshake's response.create with no microphone input at all — and everyone
    # else is answered 1013. 2 (not 1) so a student whose laptop slept mid-answer
    # can start again without waiting for the dead socket's watchdog to notice.
    interview_max_sessions_per_user: int = 2
    # The VOLUME half of the per-student cap, which concurrency alone never was:
    # 2 concurrent slots x back-to-back 15-minute sessions is ~96 interviews a
    # day from one account — each one billing a hosted speech-to-speech model
    # from the handshake — and nothing counted them (audit H: unbounded spend).
    # Enforced
    # in _open_records with one indexed COUNT over the student's sessions in the
    # last 24 h, refused with close 4015 BEFORE any upstream socket opens or row
    # is written. 8 is a full afternoon of honest practice; a student who hits
    # it is looping, sharing a cookie, or being scripted.
    #
    # B6.4 SPLIT IT IN TWO AND THIS IS NOW THE PRACTICE ALLOWANCE, counted on
    # COMPLETED interviews only: an interview that dropped out at minute two no
    # longer costs a student a turn. Abandoned and failed sessions used to count
    # here, and the reason they did has not gone away — "a cap that only counts
    # clean finishes is a cap a crash loop never hits" — so it moved to the
    # setting below, which is the one that still counts everything.
    #
    # Both are the DEPLOYMENT's answer and both are overridable per college
    # (`interview_policies`, B6.1). A college that has decided nothing gets
    # exactly these numbers, which is what makes the absence of a policy row the
    # default rather than a row somebody has to create.
    interview_max_per_student_per_day: int = 8
    # The SPEND ceiling: every `interview_sessions` row in the same rolling 24 h,
    # finished or not. Higher than the allowance above by construction — the
    # policy table's CHECK refuses the other order, because a spend ceiling below
    # the practice cap makes the practice cap unreachable and the student's
    # allowance silently becomes the ceiling. 20 leaves room for a bad network
    # morning (a dozen dropped handshakes and eight real interviews) and still
    # bounds a reconnect loop at a number somebody would notice on an invoice.
    interview_max_attempts_per_student_per_day: int = 20

    # The deterministic answer gate — a word count, not a model call, because
    # this runs on the hot path between the student finishing and the
    # interviewer replying, and a round-trip here is latency every single turn.
    # Below this many words the transcript is not treated as an answer: it is
    # recorded as `too_short` and does NOT advance the phase machine (the OpenAI
    # relay also re-asked the question; app/interview_nova.py deliberately does
    # not — see its header). 4 clears "yes",
    # "I don't know" and a cough transcribed as "uh", while leaving a real short
    # answer ("I led the campus fintech club") intact. 0 disables the gate — the
    # pre-v3 behaviour, where anything at all counted as an answer.
    interview_min_answer_words: int = 4
    # Budget for the final scorecard: one extra, text-only response.create issued
    # after the spoken wrap-up verdict, in the same session that already holds
    # the transcript. It is the LAST thing in a session, so the student is
    # sitting on a finished interview waiting for it. On expiry the evaluation is
    # persisted as unavailable and the socket still closes 1000 — a missing
    # report must never cost the transcript.
    # ---- Which engine runs the interview -------------------------------
    #
    # "nova"    app/interview_nova.py -> Amazon Nova 2 Sonic on Bedrock, over
    #           the native InvokeModelWithBidirectionalStream API. One
    #           speech-to-speech model hears, reasons and speaks, authenticated
    #           by the IAM role this deployment already runs under rather than
    #           by a pasted key.
    # "local"   app/interview_local.py -> nothing leaves the machine.
    #           faster-whisper hears, the Ollama model in LLM_MODEL reasons,
    #           Piper speaks. No key, no cost, and rule 1 holds by construction
    #           rather than by a gate.
    #
    # DEFAULT IS "nova", the hosted engine, because the local one is opt-IN:
    # it needs a fourth venv, model weights on disk and ideally a GPU, and a
    # deployment that has set none of that up must not find its interviews
    # silently running on an engine that cannot start.
    #
    # A THIRD ENGINE, "openai", ran this interview until 2026-09 and is gone;
    # `openai` is therefore no longer a recognised value and falls back to the
    # default with a warning, like any other unknown string.
    interview_engine: str = _DEFAULT_INTERVIEW_ENGINE

    @field_validator("interview_engine", mode="before")
    @classmethod
    def _known_engine(cls, value: object) -> str:
        """An unrecognised engine name falls back to the hosted relay.

        Deliberately an ALLOWLIST rather than `!= "local"`: a typo like
        INTERVIEW_ENGINE=loca must not quietly run the hosted engine while the
        operator believes nothing is leaving the machine. It falls back to the
        documented default and says so, which is the same shape as
        `password_login_allowed` -- an unknown value closes the door rather than
        guessing which one was meant.
        """
        text = str(value or "").strip().lower()
        if text in _INTERVIEW_ENGINES:
            return text
        if text:
            import logging

            logging.getLogger(__name__).warning(
                "INTERVIEW_ENGINE=%r is not one of %s; using the default",
                value,
                ", ".join(sorted(_INTERVIEW_ENGINES)),
            )
        return _DEFAULT_INTERVIEW_ENGINE

    # ---- The Nova 2 Sonic engine (INTERVIEW_ENGINE=nova) ----------------
    #
    # Bedrock's speech-to-speech model, reached over
    # InvokeModelWithBidirectionalStream. There is NO api key here and that is
    # the point: the stream is signed with SigV4 from whatever the standard AWS
    # chain resolves (task role, instance profile, SSO profile, or the
    # AWS_ACCESS_KEY_ID pair in the environment), so an AWS-hosted REEP grants
    # bedrock:InvokeModelWithBidirectionalStream to the task role and pastes
    # nothing anywhere.
    #
    # Rule 1 (AGENTS.md) applies in full, as it did to the relay before it:
    # Bedrock
    # is off-machine, so no student record enters the session. What the engine
    # authors upstream is the fixed persona plus the fixed per-phase directives
    # from app/interview_matrix.py, and everything else on the uplink is the
    # student's own microphone.
    nova_sonic_model: str = _DEFAULT_NOVA_SONIC_MODEL
    # Blank falls back to BEDROCK_REGION and then to the ordinary AWS
    # environment (AWS_REGION / AWS_DEFAULT_REGION) — see `nova_region`'s
    # resolver property below. It must resolve to SOMETHING, because the
    # bidirectional endpoint is regional and Nova 2 Sonic is not in every
    # region; `interview_ready` reports the interview unavailable rather than
    # letting a student meet the failure as a dead socket.
    nova_sonic_region: str = ""
    # The voice for the GENERIC interview (no ?specialization=). Each matrix row
    # carries its own — see nova_voice_for() in app/interview_matrix.py — and an
    # unknown name here falls back to the model's own default with a line in the
    # log rather than failing the session.
    nova_sonic_voice: str = "matthew"
    # HIGH | MEDIUM | LOW: how fast Nova decides the student has stopped
    # speaking. AWS documents the three as the pause Nova waits for before it
    # takes the turn — HIGH 1.5 s, MEDIUM 1.75 s, LOW ~2 s — and describes LOW
    # as "maximum patience ... minimizing interruptions of users who pause
    # while thinking ... best for thoughtful conversations". An interview
    # answer IS that conversation: a student gathering a STAR example stops for
    # a breath, and every setting faster than LOW read that breath as the end
    # of the answer, so the next question landed on top of the second half
    # (reported as the interviewer "recording the answer very rigidly", and it
    # was). Being cut off mid-answer is the single most damaging thing a mock
    # interviewer can do to a nervous student; a quarter of a second of extra
    # latency on every turn is what it costs to stop doing it. This shipped as
    # MEDIUM ("AWS's recommended default") until 2026-09-17.
    nova_sonic_endpointing: str = "LOW"
    # What the uplink is resampled to. The browser captures at 24 kHz (the
    # client link is unchanged from the OpenAI relay) and Nova accepts 8/16/24
    # kHz; 16 kHz is what every AWS sample streams and what the model is
    # documented against, so it is the default and the resample happens here.
    nova_sonic_input_rate_hz: int = 16000
    # THE 8-MINUTE WALL. A Nova bidirectional stream is closed by the service
    # after 8 minutes, which is less than half of interview_max_seconds (900).
    # An interview that runs into it ends mid-sentence with no verdict and no
    # scorecard, so the engine treats this as the real cap and forces the
    # wrap-up early enough to finish speaking. 480 is the documented limit; the
    # setting exists so a deployment that sees the stream cut sooner can say so
    # rather than discovering it one interview at a time.
    nova_sonic_connection_seconds: int = 480
    # How long opening the stream AND completing the handshake may take before
    # the student is told the interviewer is unavailable (close 4002) instead of
    # left on an open socket that never speaks. Production hung exactly there:
    # the engine awaited Bedrock's response headers before it had sent
    # sessionStart, Bedrock does not answer until it has, and nothing bounded
    # the wait — the interview_sessions row stayed `running` with no close code
    # and the student saw a spinner. 20 s is several times a healthy handshake
    # (~1-2 s from ap-south-1 to Tokyo) and short enough that the sentence
    # arrives while the student is still looking at the screen.
    nova_sonic_open_timeout_seconds: int = 20

    # ---- The voice-assistant platform (app/voice_platform) -----------------
    #
    # The dual-path (Undergraduate / Postgraduate) interview platform: per-degree
    # catalogue + Admin CRUD, the /ws/media-bridge socket on the Nova engine,
    # S3 -> Lambda -> SQS candidate ingest, dual-channel call recordings, and
    # and the DynamoDB projection. EVERY value here is optional and
    # blank means "that projection is off, honestly": no bucket -> recordings
    # stay on the local audio volume, no queue -> bulk uploads store straight
    # into Postgres, no table -> in-memory session state, no endpoint -> no
    # search index. GET /api/platform/admin/status says which are on.
    # ---------------------------------------------------------------- #
    # THE IDENTITY LEDGER (app/export_identity.py). Blank bucket means the
    # ledger is not being kept, and `python -m app.export_identity` says so
    # rather than pretending -- the same honesty rule as the platform
    # projections below.
    #
    # THIS IS NOT A BACKUP SETTING AND MUST NOT BORROW A BACKUP'S LIFECYCLE.
    # Every database artefact this deployment has is bounded by
    # `backupRetentionDays`, which RDS caps at 35; this bucket is the one place
    # a login survives past that, so its retention is "never" and its bucket is
    # its own. A prefix under an existing bucket would inherit that bucket's
    # lifecycle rules, which is how "permanent" quietly becomes 35 days again.
    identity_ledger_bucket: str = ""
    identity_ledger_prefix: str = "identity"
    # Blank falls back to the ordinary AWS environment, like every other client
    # here. Named separately because the ledger may deliberately live in a
    # different region from the database it describes -- a bucket in the same
    # region as the thing it is protecting against losing is half a plan.
    identity_ledger_region: str = ""

    # ---------------------------------------------------------------- #
    # THE LOGICAL BACKUP (app/backup_database.py). The counterpart to the
    # ledger above and to the RDS snapshots both: the snapshots are PHYSICAL
    # and restore only into PostgreSQL 17 on RDS, the ledger carries identity
    # alone, and this carries every other row in a format PostgreSQL itself
    # can read anywhere.
    #
    # TWO BUCKETS, AND THEY CANNOT BE ONE. S3 Object Lock's default retention
    # is a property of the BUCKET and not of a prefix, so a single bucket
    # cannot both promise "the daily copy goes at 90 days" and "the monthly
    # copy is kept for years". Worse, getting it wrong fails silently in the
    # direction nobody checks: a lifecycle expiration aimed at an object whose
    # lock has not expired is not an error, it is a rule that re-evaluates
    # every day, deletes nothing and reports success -- so the bucket grows
    # without bound behind a console showing a rule that looks like it works.
    db_dump_bucket: str = ""
    # Blank means the deployment keeps 90 days and no more. It is a supported
    # configuration and `app.backup_database` SAYS SO on every run rather than
    # letting a reader assume the monthly copy exists.
    db_dump_archive_bucket: str = ""
    db_dump_prefix: str = ""
    # Blank falls back to the ordinary AWS environment. Named separately for
    # the ledger's reason: a copy of the database in the same region as the
    # database is half a plan.
    db_dump_region: str = ""

    # ---------------------------------------------------------------- #
    # THE PERMANENT DOCUMENT ARCHIVE (app/document_archive.py). The file half
    # of the two tiers above, and the only one of the three that carries
    # BYTES.
    #
    # THIS IS NOT A BACKUP SETTING AND MUST NOT BORROW A BACKUP'S LIFECYCLE —
    # the identity ledger's rule, and for the identical reason. Until this
    # bucket existed, every uploaded file's only copy was the EFS volume,
    # whose one backup plan is bounded by `backupRetentionDays`; the archive
    # backup rule that reaches past 35 days selects the DATABASE ALONE, and
    # the two `pg_dump` tiers carry rows and never file bytes. So a marksheet
    # deleted from the website was unrecoverable on day 36, in both regions at
    # once. Its own bucket, versioned, Object-Locked and with NO LIFECYCLE
    # RULE AT ALL — the absence is the feature, exactly as it is on the ledger
    # and the monthly dump archive.
    #
    # Blank is supported and means the deployment has no permanent copy of its
    # files. `python -m app.archive_documents` says so on every run rather
    # than letting a reader assume one exists.
    document_archive_bucket: str = ""
    document_archive_prefix: str = ""
    # Blank falls back to the ordinary AWS environment. Named separately for
    # the ledger's reason: a copy of the college's documents in the same
    # region as the volume holding them is half a plan.
    document_archive_region: str = ""

    platform_aws_region: str = ""
    platform_ug_queue_url: str = ""
    platform_pg_queue_url: str = ""
    platform_bulk_upload_bucket: str = ""
    platform_recordings_bucket: str = ""
    platform_recordings_prefix: str = "recordings"
    platform_presign_ttl_seconds: int = 3600
    platform_dynamo_ug_table: str = ""
    platform_dynamo_pg_table: str = ""
    platform_dynamo_ttl_days: int = 180
    platform_cloudwatch_log_group: str = ""
    platform_cloudwatch_namespace: str = "REEP/VoicePlatform"
    # The WAV buffer's hard cap per call: 24 kHz PCM16 on two channels is
    # 96 kB/s, so 256 MB is ~44 minutes -- far past any time limit, and still a
    # bound, because an unbounded buffer on 100 concurrent sockets is how a
    # worker dies at the busiest hour.
    platform_buffer_max_bytes: int = 256_000_000
    # What a call may run when no time-limit row exists for its degree/track.
    # 480 = Bedrock's own 8-minute stream wall (see nova_sonic_connection_seconds).
    platform_default_time_limit_seconds: int = 480

    # The interviewer's model, SEPARATE from llm_model on purpose.
    #
    # llm_model serves the resume builder and the grounded assistant, where a
    # bigger model is simply better and a few seconds is nobody's problem. The
    # interviewer has two constraints that one does not: it must leave room on
    # the card for faster-whisper, and it must answer inside a conversation.
    #
    # Measured on this hardware, the cost of ignoring that: reep-gemma3 (7.8 GB)
    # resident alongside Whisper left 1 GB free on a 12 GB card and the first
    # clause took 9528 ms. llama3.2:3b (2 GB) leaves ~9 GB and returns in tens
    # of milliseconds. A smaller interviewer that speaks is worth more than a
    # better one that pauses for ten seconds between questions.
    #
    # Blank falls back to llm_model, so a deployment with plenty of VRAM can
    # deliberately run one model for everything.
    interview_local_llm_model: str = "llama3.2:3b"

    # faster-whisper size. base.en is the measured balance on this hardware:
    # ~28 ms for a 0.6 s tail on CUDA. tiny.en is faster and noticeably worse at
    # Indian-accented English; small.en is better and roughly 2.5x the latency.
    interview_local_stt_model: str = "base.en"
    # Decode width. beam_size=1 is greedy and was the obvious default; measured
    # on this cohort's own recordings it is both LESS accurate and SLOWER than 5
    # (157 ms vs 116 ms on a 6 s clip), because a wrong greedy token costs more
    # decoding downstream than the extra beams cost up front.
    interview_local_stt_beam: int = 5
    # Strip silence before decoding. The single biggest accuracy win measured
    # here, and it takes the average DOWN to 87 ms: less audio to decode, and no
    # silence for the model to hallucinate words into. It recovered a whole
    # clause the greedy config dropped.
    interview_local_stt_vad_filter: bool = True
    # Domain vocabulary. Whisper leans on an initial prompt for terms it has not
    # heard in context, and this cohort's interview is full of them -- a real
    # session transcribed "CAC" as "DCAC", which then cleared the answer-word
    # floor as a substantive answer and advanced the interview on nothing.
    interview_local_stt_prompt: str = (
        "This is a digital marketing interview. Terms used: CAC, LTV, ROAS, "
        "CTR, CPC, CPM, SEO, SEM, P-O-E-M, impressions, clicks, conversions, "
        "funnel, remarketing, programmatic, backlinks, crawling, indexing."
    )

    # "cuda" or "cpu". A CUDA failure is caught at load and falls back to CPU
    # rather than refusing to start the interview -- slow beats unavailable.
    interview_local_stt_device: str = "cuda"
    # Piper voice, absolute or relative to apps/api-py.
    interview_local_tts_voice: str = "var/piper-voices/en_US-lessac-medium.onnx"
    # How long Ollama keeps the interviewer model resident. An interview that
    # pays a cold load mid-conversation has a multi-second silence in it.
    interview_local_keep_alive: str = "30m"
    # The interviewer's instructions are ~2.6 kB composed; 4096 leaves room for
    # the phase directive and the syllabus block without a per-turn reload.
    interview_local_num_ctx: int = 4096
    # One question is tens of tokens. A ceiling against a model that decides to
    # monologue, not a target -- and deliberately not the primary control for
    # length, because a token cap truncates mid-sentence. The instruction does
    # that job; this catches the case where it is ignored entirely.
    interview_local_max_tokens: int = 120

    interview_report_timeout_ms: int = 20000
    # Audio capture, OFF and deliberately awkward to turn on. Recording a
    # student's voice requires their recorded consent (interview_consent_version
    # below) AND this flag AND a retention deadline, and the audio is retrievable
    # by the Main Admin only. Flipping this true without the consent row in place
    # is the failure that matters here: it is the one that cannot be undone after
    # the fact.
    interview_recording_enabled: bool = False
    # Hard per-session ceiling on stored audio — a disk-exhaustion stop, not a
    # quality knob. At the cap the recording stops and the interview continues:
    # a call is never dropped to protect a file.
    #
    # SIZE IT AGAINST 96,000 BYTES A SECOND, NOT 48,000. This comment used to
    # read "64 MB is roughly 45 minutes ... comfortably past
    # interview_max_seconds", which was true of the speech-only capture it was
    # written for. app/interview_audio.py now pads BOTH tracks to the session's
    # wall clock, so an interview burns 2 x 24 kHz x 16-bit whether anyone is
    # talking or not: 64,000,000 B was 666 s = 11.1 min against an
    # interview_max_seconds of 900, and every maximum-length interview lost its
    # last 3.8 minutes to a cap nobody had recomputed. A truncation is at least
    # never silent (audio_truncated), which is the only reason that was a defect
    # and not a scandal.
    #
    # The floor is therefore 2 x interview_max_seconds x 48,000 = 86.4 MB, and
    # 128 MB is that plus ~48% — headroom for a model that emits an answer
    # faster than real time (the interviewer's track legitimately runs ahead of
    # the clock) and for the seconds a session spends closing. Disk, which is a
    # different number: the mix is written from what survived, so a full session
    # occupies up to ~256 MB. If you move interview_max_seconds, move this.
    interview_recording_max_bytes: int = 128000000
    # Where the WAVs live. Blank keeps the historical fallback — a SIBLING of
    # uploads_path named interview-audio (app/interview_audio.py:_store_root,
    # which already reads this field by name and documents why a sibling). Set
    # it explicitly in production to a MOUNTED path: the audit found the
    # fallback landed in the container's writable layer under the image's
    # UPLOAD_DIR, i.e. consented recordings were destroyed on every redeploy.
    # docker-compose.prod.yml now mounts a dedicated volume and sets this.
    interview_audio_dir: str = ""
    # Free-disk floor below which recorder_for hands out NO new recorders: the
    # interview proceeds, existing recordings keep flushing, only NEW capture is
    # declined (and says so in the log). Recording is wall-clock-padded at
    # 96,000 B/s, so a deadline-week of interviews can genuinely fill a volume —
    # and a full disk stops upload writes and (co-located) Postgres too, which
    # is a far worse trade than one interview going unrecorded. 2 GB clears the
    # worst case of every in-flight session flushing its cap at once. This is a
    # guard on NEW recordings only, never a reason to end a call.
    interview_audio_min_free_bytes: int = 2000000000
    # How long an interview record — transcript, evaluation, any audio — is kept
    # before the reaper deletes it. 180 days covers a placement season and the
    # review that follows it. There is no "keep forever" value and 0 is refused,
    # because the two honest readings of 0 ("delete immediately" and "never
    # delete") differ by the entire record.
    interview_retention_days: int = 180
    # How long an interview_sessions row may sit in `running` with no heartbeat
    # before the sweeper marks it `abandoned`. A killed worker cannot finalize
    # its own sessions, so without this every crash leaves rows that claim to be
    # live forever. interview_max_seconds (900) + 300 s of slack: comfortably
    # past the longest legal interview, so a HEALTHY session is never swept, and
    # short enough that a deploy's restart sweep tidies the previous process's
    # wreckage rather than yesterday's. The relay's heartbeat write is what
    # feeds it; if that write fails all session long the row is swept while
    # alive, which is the right direction to fail -- visible and arguable,
    # rather than invisible and stuck at `running`.
    interview_orphan_grace_seconds: int = 1200
    # Stamped on every consent row so a change of terms is visible in the data
    # instead of assumed. Consent is NOT retroactive: bump this when what the
    # student agrees to changes, and rows carrying the old string stop counting
    # as consent for the new terms — which is exactly what should happen. A date
    # is enough; nothing parses it, and it sorts.
    interview_consent_version: str = "2026-08"

    @field_validator(
        "llm_timeout_ms",
        "llm_requests_per_minute",
        "db_pool_size",
        "db_max_overflow",
        "db_pool_timeout_s",
        "mentor_capacity",
        "ledger_edit_window_days",
        "interview_max_seconds",
        "interview_idle_seconds",
        "interview_local_num_ctx",
        "interview_local_stt_beam",
        "interview_local_max_tokens",
        "interview_max_sessions",
        "interview_max_sessions_per_user",
        "interview_max_per_student_per_day",
        "interview_min_answer_words",
        "interview_report_timeout_ms",
        "nova_sonic_input_rate_hz",
        "nova_sonic_connection_seconds",
        "platform_presign_ttl_seconds",
        "platform_dynamo_ttl_days",
        "platform_buffer_max_bytes",
        "platform_default_time_limit_seconds",
        "interview_recording_enabled",
        "interview_recording_max_bytes",
        "interview_retention_days",
        "interview_orphan_grace_seconds",
        "interview_audio_min_free_bytes",
        "interview_temperature",
        "auth_revocation_cache_seconds",
        "leave_mail_enabled",
        "job_scout_company_boards",
        "job_scout_max_age_days",
        "job_scout_new_per_specialization",
        mode="before",
    )
    @classmethod
    def _blank_is_default(cls, value: Any, info: ValidationInfo) -> Any:
        """A blank line in .env means "unset", not "crash".

        Every str field here already reads "" as "feature off", and .env.example
        documents `INTERVIEW_MAX_SECONDS=` as falling back to the default. That
        promise did not hold for the int fields: pydantic cannot coerce "" to an
        int, so a bare `LLM_TIMEOUT_MS=` raised ValidationError inside
        `Settings()` - which runs at IMPORT, i.e. before uvicorn binds a socket.
        The whole dashboard died at boot on a blank line, and apps/api-py/.env is
        shared by four processes any of which may write one.

        `_must_be_positive` below cannot catch this: it is an AFTER validator and
        never runs, because coercion has already failed.

        The default is read off the model field rather than returned as
        PydanticUndefined - returning PydanticUndefined does NOT re-trigger
        default substitution in pydantic 2.13; it is validated as a value and
        fails with "Input should be a valid integer".

        EVERY NEW NUMERIC OR BOOLEAN FIELD BELONGS ON THE LIST ABOVE. bool is not
        exempt: `INTERVIEW_RECORDING_ENABLED=` raises "Input should be a valid
        boolean" at import in exactly the same way, and str fields need it only
        because "" already means "off" for them. `docs_enabled` is the single
        deliberate absence - its PRESENCE in the environment, not just its value,
        decides something (see docs_exposed), so a blank line for it is dropped
        earlier by _blank_docs_flag_is_not_an_opt_in rather than defaulted here.
        """
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator(
        "llm_requests_per_minute",
        "db_pool_size",
        "db_max_overflow",
        "db_pool_timeout_s",
        "interview_max_seconds",
        "interview_idle_seconds",
        "interview_local_num_ctx",
        "interview_local_stt_beam",
        "interview_local_max_tokens",
        "interview_max_sessions",
        "interview_max_sessions_per_user",
        "interview_max_per_student_per_day",
        "interview_report_timeout_ms",
        "interview_recording_max_bytes",
        "interview_retention_days",
        "interview_orphan_grace_seconds",
        "interview_audio_min_free_bytes",
        "job_scout_max_age_days",
        "job_scout_new_per_specialization",
    )
    @classmethod
    def _must_be_positive(cls, value: int, info: ValidationInfo) -> int:
        """Reject zero/negative at startup rather than mid-interview.

        An `INTERVIEW_MAX_SECONDS=0` typo otherwise means "close every session the
        instant it opens", which presents as a relay that connects and hangs up -
        indistinguishable from an upstream outage. The same shape repeats across
        this list: 0 sessions per user is "nobody may interview", a 0 ms
        transcription deadline is "every answer is unknown", 0 retention days is
        a record that may already be gone. None of them is a thing an operator
        meant to type, and all of them look like an outage rather than a config
        error from the outside.
        """
        if value <= 0:
            raise ValueError(f"{info.field_name} must be a positive integer, got {value}")
        return value

    @field_validator(
        "interview_min_answer_words",
        "auth_revocation_cache_seconds",
        "ledger_edit_window_days",
    )
    @classmethod
    def _must_not_be_negative(cls, value: int, info: ValidationInfo) -> int:
        """The three settings where 0 is a MEANING, not a typo.

        Deliberately not on the positive-only list above, and do not "fix" the
        omission by moving them: for these three, zero is the switch that turns a
        v3 behaviour off and returns the older one. 0 answer words accepts every
        transcript (the pre-v3 gate); 0 clarifications never re-asks a question;
        0 cache seconds asks the database about revocation on every request,
        which is the SAFEST value here rather than the broken one. Negative is
        still nonsense, and a negative deadline would read as "already expired"
        at every call site that compares against it.
        """
        if value < 0:
            raise ValueError(f"{info.field_name} must be zero or a positive integer, got {value}")
        return value

    @field_validator("interview_temperature")
    @classmethod
    def _temperature_in_range(cls, value: float | None) -> float | None:
        """0.0-2.0 when set; None means "never sent" and is always legal."""
        if value is not None and not 0.0 <= value <= 2.0:
            raise ValueError(
                f"interview_temperature must be between 0.0 and 2.0, got {value}"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _blank_docs_flag_is_not_an_opt_in(cls, values: Any) -> Any:
        """A blank `DOCS_ENABLED=` line means UNSET, not "the operator chose".

        `docs_exposed` asks pydantic which fields a source actually supplied
        (`model_fields_set`), because in production the field default True is not
        consent — somebody has to say so. But a source that supplies the key with
        an EMPTY value lands in model_fields_set exactly like a real "true"
        would, so one stray blank line in a shared .env would republish the whole
        API surface on a production host. Dropping the key restores "blank means
        unset" for the one field whose presence, not just value, changes
        behaviour — and it is why docs_enabled is absent from _blank_is_default:
        by the time that validator would run, this has already made the field
        look untouched.
        """
        if isinstance(values, dict):
            return {
                key: raw
                for key, raw in values.items()
                if not (
                    key.lower() == "docs_enabled"
                    and isinstance(raw, str)
                    and not raw.strip()
                )
            }
        return values

    @property
    def google_ready(self) -> bool:
        """Whether Google sign-in — the only way a human signs in — is configured.

        Exactly the two credentials, matching app/google_auth.py:sso_ready(),
        which is the function the router actually gates on: GOOGLE_REDIRECT_URI
        is an optional override that defaults correctly, so requiring it here
        would report the feature "off" on a deployment where it works. Without
        either credential the failure lands on a Google page we do not control
        and cannot translate, so it is caught here and the login screen keeps its
        Google button disabled with our own words on it.

        `.strip()` for the same reason `realtime_ready` does it: a value pasted
        into a shared .env routinely arrives with a trailing space, and
        whitespace is not a credential — it is a failure the student meets
        instead of the operator.
        """
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def roster_domain(self) -> str:
        """Domain appended when deriving a roster email from a USN.

        ROSTER_EMAIL_DOMAIN (or its COLLEGE_EMAIL_DOMAIN alias) wins; otherwise
        the FIRST GOOGLE_ALLOWED_DOMAIN entry — the student domain by
        convention, staff domains listed after it — so a single-domain college
        configures one variable and not two. NEVER empty: an empty domain would
        derive "1mp25mdm01@", which is not a failed lookup but an INSERT of a
        malformed address into a UNIQUE column.
        """
        explicit = self.roster_email_domain.strip().lstrip("@").lower()
        if explicit:
            return explicit
        first = self.google_allowed_domain.split(",")[0].strip().lstrip("@").lower()
        return first or _DEFAULT_COLLEGE_DOMAIN

    @property
    def mail_configured(self) -> bool:
        """Is there a real outbound transport? Blank means log-and-keep."""
        return bool(self.ses_from_address.strip())

    @property
    def badge_mail_active(self) -> bool:
        """Whether skill-claim notifications go out (app/badge_mail.py).

        Only the exact words count, as with `password_login_forced`: "true"
        forces them on over whatever transport exists (the console outbox on a
        development machine), "false" forces them off even with SES configured,
        and anything else -- blank, absent, a typo -- derives the answer from
        `mail_configured`. A typo therefore degrades to the derived default,
        which on a machine with no transport is OFF: never to a SENT row about
        a message nobody received.
        """
        text = self.badge_mail_enabled.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
        return self.mail_configured

    @property
    def adzuna_configured(self) -> bool:
        """Both halves of Adzuna's key pair are set. One half is no key."""
        return bool(self.adzuna_app_id.strip() and self.adzuna_app_key.strip())

    @property
    def jooble_configured(self) -> bool:
        return bool(self.jooble_api_key.strip())

    @property
    def job_scout_active(self) -> bool:
        """Whether the daily job agent searches anything (app/job_scout.py).

        Only the exact word "false" stops it; anything else derives the answer
        from the keys, so the act of putting a key in the secret is the
        decision to run it, the password door's argument again. The company
        feeds have no key, so `JOB_SCOUT_COMPANY_BOARDS=true` is that act.
        """
        if self.job_scout_enabled.strip().lower() == "false":
            return False
        return self.adzuna_configured or self.jooble_configured or self.job_scout_company_boards

    @property
    def provisionable_email_domains(self) -> frozenset[str]:
        """Domains an application may be APPROVED INTO. A fence, deliberately.

        Read this against `google_allowed_domain`'s comment, which says the
        domain list is a label and "nothing in the sign-in path refuses on it,
        and nothing should start to". That is still true and this does not
        change it. The two run in opposite directions:

          sign-in       a verified Google account -> is it on the roster?
          provisioning  an UNAUTHENTICATED form   -> may it join the roster?

        A domain test on sign-in can only lock out someone who is already
        enrolled, which is why there isn't one. A domain test on provisioning
        runs BEFORE enrolment, so it cannot lock anybody out of anything — it
        can only stop a stranger's address from becoming a roster row. That is
        the whole difference, and it is why the fence belongs here and nowhere
        else.

        Staff are unaffected: `app/grant_access.py` exists precisely to admit an
        address that is not on the student domain, and it does not come through
        this path.
        """
        raw = f"{self.google_allowed_domain},{self.roster_email_domain}"
        return frozenset(
            part.strip().lstrip("@").lower() for part in raw.split(",") if part.strip()
        )

    @property
    def nova_region(self) -> str:
        """The region the Nova Sonic stream is opened in, or "" if unknown.

        THREE sources, most specific first: NOVA_SONIC_REGION, then
        BEDROCK_REGION (a deployment already calling Bedrock for the resume
        builder has said where its Bedrock lives, and making it say so twice is
        how the two drift), then the ordinary AWS environment every other tool
        on the box reads. Unlike boto3's client, the bidirectional endpoint is
        composed from this string BY US, so "" cannot be handed on as "let the
        SDK decide" — it is an unconfigured deployment, and `interview_ready`
        says so in words instead of leaving a student at a dead socket.
        """
        import os

        return (
            self.nova_sonic_region.strip()
            or self.bedrock_region.strip()
            or os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        )

    @property
    def platform_region(self) -> str:
        """The region the platform's boto3 clients are built for, or "".

        Unlike `nova_region`, "" is a legitimate answer here: it is handed to
        boto3, which then reads its own configuration (AWS_REGION, a profile,
        the task metadata). It is resolved in one place so the S3, SQS,
        DynamoDB and CloudWatch clients cannot disagree.
        """
        import os

        return (
            self.platform_aws_region.strip()
            or os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        )

    def platform_queue_url(self, degree_level: str) -> str:
        """The SQS queue for one degree level, or "" when that stream is off."""
        level = degree_level.strip().upper()
        if level == "UG":
            return self.platform_ug_queue_url.strip()
        if level == "PG":
            return self.platform_pg_queue_url.strip()
        return ""

    def platform_dynamo_table(self, degree_level: str) -> str:
        """The DynamoDB sessions table for one degree level, or ""."""
        level = degree_level.strip().upper()
        if level == "UG":
            return self.platform_dynamo_ug_table.strip()
        if level == "PG":
            return self.platform_dynamo_pg_table.strip()
        return ""

    @property
    def nova_sonic_ready(self) -> bool:
        """Whether the Nova engine has enough to open a stream.

        Deliberately NOT a credential check. Credentials come from the standard
        AWS chain (task role, instance profile, profile, environment) which is
        resolved asynchronously at connect time and cannot be probed from a
        synchronous property without a network call on a request path. What CAN
        be checked here is the pair this process composes itself — the model id
        and the region of the endpoint — and those are also the two an operator
        actually gets wrong. A missing ROLE fails at handshake and closes 4002
        with the reason in the log, which is the right place for a fault only
        AWS can explain.
        """
        return bool(self.nova_sonic_model.strip() and self.nova_region)

    @property
    def interview_ready(self) -> bool:
        """Whether the mock interview can run ON THE ENGINE THIS DEPLOYMENT USES.

        `realtime_ready` answers the OpenAI question alone, and both the status
        probe and the socket used to ask it whatever INTERVIEW_ENGINE said —
        which meant a deployment running the local engine (no key by design, and
        nothing leaving the machine) was told its interviews were "not
        configured on this server yet" until it pasted an OpenAI key it would
        never spend. Each engine answers for itself here.
        """
        engine = self.interview_engine.strip().lower()
        if engine == "nova":
            return self.nova_sonic_ready
        if engine == "local":
            # Nothing to configure: the models are on disk and the failure mode
            # (a missing weights file, no GPU) surfaces at start with its own
            # close code and its own sentence, which is more useful than a
            # blanket "unavailable" here.
            return True
        return self.realtime_ready

    @property
    def interview_unready_reason(self) -> str:
        """The one sentence a student reads when the interview is off.

        Engine-specific because the fixes are: an operator who reads "set
        OPENAI_API_KEY" on an AWS deployment goes and buys the wrong thing.
        Written for the student (who can only be told it is not their fault)
        while naming the variable for the operator standing behind them.
        """
        engine = self.interview_engine.strip().lower()
        if engine == "nova" and not self.nova_region:
            return (
                "Mock interviews are not configured on this server yet "
                "(no AWS region for Nova Sonic)."
            )
        return "Mock interviews are not configured on this server yet."

    @property
    def interview_provider_label(self) -> str:
        """WHO receives the student's voice, in words a student can read.

        This is consent copy, not decoration. The assistant screen tells the
        student where their microphone audio goes before they agree to it, and
        that sentence used to name OpenAI in the HTML — which became a FALSE
        disclosure the day INTERVIEW_ENGINE grew a second hosted engine. A
        consent record whose wording says the wrong company is worse than no
        record: `interview_consents` exists to answer "what did they agree to",
        and it would answer wrongly with a straight face.

        Operator note that belongs next to this string: changing engines
        changes who receives the audio, so it warrants bumping
        INTERVIEW_CONSENT_VERSION and asking again.
        """
        engine = self.interview_engine.strip().lower()
        if engine == "local":
            # Deliberately still phrased as a disclosure. Nothing leaves the
            # machine on this engine, so the surrounding copy overstates in the
            # SAFE direction: a student told more leaves than does is not
            # harmed, and one told less is.
            return "a speech model running on the college's own server"
        return "Amazon's Nova Sonic model, running on AWS Bedrock"

    @property
    def gemini_key_present(self) -> bool:
        """A Gemini/Google key from either the config field or the raw env."""
        import os

        return bool(
            self.gemini_api_key.strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )

    # Where uploaded files are stored on disk (only metadata lives in the DB).
    # Empty -> apps/api-py/var/uploads (gitignored). Object storage in production.
    upload_dir: str = ""

    @property
    def is_prod(self) -> bool:
        """Whether this process is serving real people. See _PROD_ENV_NAMES."""
        return self.env.strip().lower() in _PROD_ENV_NAMES

    @property
    def env_is_dev(self) -> bool:
        """Whether this environment keeps its development affordances.

        The public reading of the `_DEV_ENV_NAMES` ALLOWLIST, which is the only
        test a dev-only door may key on. Never `not is_prod`: the two name sets are
        deliberately not complements, so an ENV nobody anticipated — "staging",
        "uat", a typo, a blank from a half-written deploy template — is neither,
        and must lose the affordance rather than gain it.
        """
        return _is_dev_env(self.env)

    @property
    def mcp_enabled(self) -> bool:
        """Whether app/main.py mounts the dev MCP surface at /mcp.

        BOTH halves are required, and the environment half is not negotiable.
        The mount forwards the caller's Cookie header into the real request
        handlers, so every tool call runs as whoever holds that session: on a
        host serving real students it would be a second front door with none of
        the rate limiting, revocation or audit the first one has. Keying it on
        the dev allowlist means an unrecognised ENV shuts it, like every other
        door in this file.

        tests/test_codebase_guards.py pins both halves, and pins that
        `fastapi_mcp` stays out of requirements.txt — it is a development tool
        and has no business in the image the Dockerfile builds.
        """
        return self.env_is_dev and _env_true(self.mcp_dev_surface)

    @property
    def password_login_allowed(self) -> bool:
        """Whether POST /api/auth/login may answer at all.

        Two ways to be true, and they are different in kind.

        DEV AND CI get it unconditionally, as they always have: tests/conftest.py's
        `login` fixture and the test modules that use it cannot drive an OAuth
        round-trip from a TestClient. That half is keyed on the environments it
        SERVES rather than on `not is_prod`, so an ENV nobody anticipated
        ("staging", a typo, an empty string from a broken deploy) still refuses
        rather than admits — a misconfiguration must fail towards "nobody can use
        a password", never "anyone can".

        ANY OTHER ENVIRONMENT, production included, gets it from THIS property
        only when an operator sets PASSWORD_LOGIN=true. This is the ENV-LEVEL
        answer; it is not the whole answer. When PASSWORD_LOGIN is blank the
        router derives the door from the database instead — open exactly when at
        least one account holds a real scrypt hash (see
        app/routers/auth.py:password_door_open) — because on Fargate the
        environment is a Terraform apply away and a key is one ops-task away, and
        the person issuing the first key has already made the decision this flag
        would ask them to make twice. PASSWORD_LOGIN=false is the hard off-switch
        that overrides even an issued key (incident response: shut the door, keep
        the hashes). See `password_login_forced` for the three states.

        Running two doors is not free: a password is guessable where a Google
        account behind the college's own 2FA is not, which is why `login`
        rate-limits per account and why `app.set_password` refuses a weak one.
        What it is NOT is a way back to the seeded demo logins — `app.seed` still
        refuses when ENV=prod, so the accounts whose passwords are published in
        AGENTS.md cannot be there for any of this to admit.
        """
        return _is_dev_env(self.env) or self.password_login_forced is True

    @property
    def password_login_forced(self) -> bool | None:
        """PASSWORD_LOGIN as a three-state switch: True, False, or None (derive).

        Only the exact words count. "true" forces the door open on any ENV,
        "false" forces it shut on any non-dev ENV even if keys have been issued,
        and anything else — blank, absent, a typo — is None, meaning "decide from
        whether a key exists". A typo therefore degrades to the derived default,
        never to open: an operator who meant "false" and typed "flase" gets a
        door that is shut unless someone has deliberately issued a key, which is
        the same state they were in before they touched the variable.
        """
        text = self.password_login.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
        return None

    @property
    def otp_login_required(self) -> bool:
        """Whether POST /api/auth/login answers a correct password with an emailed
        one-time code instead of a session.

        True ONLY when OTP_REQUIRED spells "true" AND the environment is not one
        of the dev/CI names — the same allowlist `password_login_allowed` reads.
        On dev and CI this is ALWAYS False, whatever the variable says:
        tests/conftest.py's `login` fixture POSTs /api/auth/login and reads the
        set-cookie header straight off the response, and the whole DB-backed
        suite signs in through it. A second step there would take the suite
        with it, and a guard that trips on a laptop gets deleted by whoever is
        trying to ship that afternoon. Students are unaffected either way: they
        sign in with Google, which never reaches the password endpoint.
        """
        if _is_dev_env(self.env):
            return False
        return self.otp_required.strip().lower() == "true"

    @property
    def insecure_cookies_allowed(self) -> bool:
        """Whether the session and OAuth-state cookies may be issued WITHOUT
        `Secure` — i.e. whether this box is allowed to be plain HTTP.

        Read it as `secure=not settings.insecure_cookies_allowed` at the
        set_cookie sites in app/routers/auth.py. It replaces `secure=is_prod`,
        which was a NAME TEST and the audit's M2: a `staging`, `uat` or `demo`
        box is not one of the four spellings is_prod knows, so it served REAL
        roster rows over HTTPS while marking the session cookie non-Secure —
        meaning one downgraded or plain-HTTP request (a bookmarked http:// link,
        a mixed-content asset, an attacker who can force one) puts a
        12-hour-valid session token on the wire in the clear.

        Keyed on the dev allowlist instead, so only the environments that
        genuinely run on http://localhost keep the affordance and everything
        unrecognised gets the safe behaviour. The dev cost of getting this
        backwards is visible immediately (no cookie is stored on http://), which
        is the right direction for a mistake to fail in.
        """
        return _is_dev_env(self.env)

    @property
    def docs_exposed(self) -> bool:
        """Whether app/main.py mounts /docs, /redoc and /openapi.json.

        Dev keeps them: they are how this API is explored, and AGENTS.md sends a
        newcomer to http://127.0.0.1:3300/docs on their first run.

        Production drops them unless DOCS_ENABLED says otherwise IN THE
        ENVIRONMENT. That is why this is not simply `self.docs_enabled`: the
        field default is True and a default is not a decision, so a deploy that
        sets nothing must not publish the full surface. `model_fields_set` is
        pydantic's record of which fields a source actually supplied, which makes
        "DOCS_ENABLED appears in the environment" the opt-in signal;
        _blank_docs_flag_is_not_an_opt_in above keeps a blank line from counting
        as one. `DOCS_ENABLED=false` still turns them off everywhere, dev
        included, because an explicit no is an answer in both directions.
        """
        if not self.docs_enabled:
            return False
        return not self.is_prod or "docs_enabled" in self.model_fields_set

    def production_boot_failures(self) -> list[str]:
        """Configuration this process must not serve real people with.

        Returns one sentence per problem, each naming the variable and the fix;
        empty means "nothing here is fatal". app/main.py's lifespan calls this
        and refuses to start when it comes back non-empty — the check lives here
        because these constants live here, and the REFUSAL lives there because
        boot is where an operator meets it.

        EMPTY ON EVERY NON-PRODUCTION ENV, and that is load-bearing: the test
        suite boots the real app through TestClient with ENV=dev and would
        otherwise need to know a secret. It is also the reason this is a hard
        failure rather than a warning — it can only fire on a host that has
        declared itself production, where "the log said something" is not a
        control. app/seed.py's ENV=prod refusal has the same shape and the same
        deliberate absence of an override flag: an escape hatch here would be
        found, used, and end with the committed secret live on the internet.

        Nothing in the returned text ever contains the secret or the URL itself.
        These messages land in deploy logs and aggregators, which is precisely
        where a credential must not be copied to.
        """
        if not self.is_prod:
            return []

        problems: list[str] = []

        secret = self.auth_secret.strip()
        placeholder = any(marker in secret.lower() for marker in _PLACEHOLDER_SECRET_MARKERS)
        if not secret:
            problems.append(f"AUTH_SECRET is blank or whitespace. {_NEW_SECRET_HINT}")
        elif secret == _DEV_AUTH_SECRET or placeholder:
            problems.append(
                "AUTH_SECRET is still the development value published in this "
                "repository (.env.example). It signs the reep_session cookie and "
                "derives the OAuth flow-cookie key, so anyone who has read the repo "
                'can sign {"role":"ADMIN"} for themselves and read every '
                "student's marks, attendance and USN — no login, no Google, no DB "
                f"row involved. {_NEW_SECRET_HINT}"
            )
        elif len(secret) < AUTH_SECRET_MIN_CHARS:
            problems.append(
                f"AUTH_SECRET is {len(secret)} characters; the floor is "
                f"{AUTH_SECRET_MIN_CHARS}, because HS256 signs with these bytes and a "
                "key shorter than its 32-byte digest is brute-forceable offline from "
                f"one captured cookie. {_NEW_SECRET_HINT}"
            )

        # A REFUSAL, not a warning, and the call was close enough to write down.
        # The password is published in this repo and in .env.example, so a
        # production database reachable with it is a production database that
        # anyone who cloned us can read every student's records from. The two
        # cases it catches are both bad and neither is subtle: the whole default
        # URL untouched means production is about to serve a developer's laptop
        # database (empty dashboards that read as a working deploy, or a
        # connection refused on every request), and a real host carrying this
        # password means the credential needs one ALTER ROLE. The argument for a
        # warning is that an operator on a private network may have chosen it —
        # but that operator can change one env var, while nobody can un-leak a
        # student roster, and a warning in a deploy log is a thing nobody reads.
        if _DEV_DB_PASSWORD in self.database_url:
            problems.append(
                "DATABASE_URL still carries the development password published in "
                "this repository (.env.example). Point it at the production database "
                "with a credential that is not in the repo; if that database really "
                "does use this password, change the password first (ALTER ROLE reep "
                "WITH PASSWORD ...) — anyone who has cloned REEP knows it."
            )

        return problems

    @property
    def uploads_path(self) -> Path:
        """Resolved directory for the file store (created on first use)."""
        if self.upload_dir.strip():
            return Path(self.upload_dir)
        return Path(__file__).resolve().parent.parent / "var" / "uploads"

    @property
    def allow_remote_student_data(self) -> bool:
        return self.llm_allow_remote_student_data.strip().lower() == "true"

    # Query params that belong to Prisma and mean nothing to libpq. Only these
    # are stripped — see sqlalchemy_url.
    _PRISMA_ONLY_PARAMS = frozenset({"schema", "connection_limit", "pgbouncer"})

    @property
    def sqlalchemy_url(self) -> str:
        """Normalise the DB URL for SQLAlchemy + psycopg 3.

        Forces the `+psycopg` driver (so a plain `postgresql://` does not fall
        back to psycopg2) and drops the Prisma-only query params left over from
        the old stack.

        It drops ONLY those. This used to end `return url.split("?", 1)[0]`,
        discarding the entire query string — which silently threw away
        `sslmode`. Every managed Postgres (Neon, RDS, Supabase, Cloud SQL) hands
        you `...?sslmode=require`, so the connection fell back to libpq's default
        `prefer`: TLS opportunistic, server certificate never verified, nothing
        logged and nothing failed. An operator who set sslmode=require in the
        secret had every reason to believe it applied while student records
        crossed the network on an unauthenticated channel.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]

        base, sep, query = url.partition("?")
        if not sep:
            return base

        kept = [
            pair
            for pair in query.split("&")
            if pair and pair.split("=", 1)[0] not in self._PRISMA_ONLY_PARAMS
        ]
        return f"{base}?{'&'.join(kept)}" if kept else base


settings = Settings()
