# Chapter 15 — Conventions, Rules and Running It

> **HISTORICAL — this book was written against the codebase as it stood in 2026-08
> and has not been maintained since. `AGENTS.md` is the current truth; where this
> chapter and that file disagree, this chapter is wrong.** It is kept for the
> *reasoning*, which is still this codebase's reasoning. `README.md` in this
> directory lists everything the book describes that no longer exists — the
> LiveKit voice stack, the DIRECTOR role, the `openai` interview engine,
> `ThemeService` / `IconComponent` / four of the five `shared/kit/` components.

*The closing volume. This is the page you keep open.*

Chapters 1–14 explained the code. This one is the compendium: every naming rule the book
surfaced, the standing rules that must not be broken, a review checklist organised by what
you changed, and the whole operational half — processes, environment, Docker, the two
requirements files, the two seeds, the suite, CI, and the runbooks.

Every claim below was re-measured against the working tree on branch
`harden/voice-stack-architecture-review`. Where a house document and the code disagree, both
sides are shown and the disagreement is labelled **DRIFT**, **STALE** or **DEFECT** rather
than reconciled silently. Where a rule has exceptions, the exceptions are enumerated — a
rulebook that hides its exceptions is a rulebook you stop trusting the first time you grep.

---

## 1. How to use this chapter

**This is a reference, not a narrative.** Nothing here builds on what came before it. Jump to
the section that matches what you are doing:

| You are… | Go to |
|---|---|
| naming a new Python module, class, function, constant, setting, schema, route, **table, column, foreign key, constraint, relationship, timestamp**, index, enum, migration, test or logger | [§2](#2-the-backend-naming-rulebook) — and the sub-index below |
| naming a new component, signal, DTO, CSS class or route | [§3](#3-the-frontend-naming-rulebook) |
| crossing the client/server boundary — casing, enums, status codes | [§4](#4-the-cross-cutting-conventions) |
| about to send a student's record anywhere, or to widen a staff query | [§5](#5-the-rules-that-must-not-be-broken) — read it in full before you write the line |
| reviewing a diff | [§6](#6-the-review-checklist) |
| standing the stack up from cold | [§7](#7-running-it) |
| wondering what a variable does or why a value is blank | [§8](#8-the-complete-environment-variable-reference) |
| deploying, or reading `docker-compose.prod.yml` | [§9](#9-docker-and-deployment) |
| setting up a fresh database | [§10](#10-two-requirements-files-two-seeds-and-the-fresh-database-order) |
| adding a test, or wondering why CI is green when it should not be | [§11](#11-the-test-suite-ci-and-where-coverage-is-thin) |
| holding a broken thing at 2 a.m. | [§12](#12-the-runbooks) |
| new to the repo and wondering what to read first | [§13](#13-closing-the-book) |

**The sub-index for the two rulebooks**, because §2 and §3 are long and the artefact you are
naming is what you actually know:

| §2 — backend | §3 — frontend |
|---|---|
| 2.1 modules · 2.2 classes · 2.3 functions · 2.4 constants · 2.5 settings and env vars · 2.6 Pydantic schemas · 2.7 routers and route paths | 3.1 the component triplet · 3.2 class names · 3.3 selector prefixes · 3.4 signals and computeds · 3.5 the `asReadonly()` idiom · 3.6 DTO interfaces |
| **2.8 tables · columns · foreign keys · relationships · timestamps** (2.8.1–2.8.5) · 2.9 enums and PG type names · 2.10 indexes and constraints | 3.7 module constants · 3.8 CSS classes and the global/local split · 3.9 the two token generations · 3.10 `localStorage` keys |
| 2.11 migrations · 2.12 tests · 2.13 loggers · 2.14 errors and status codes · 2.15 module ordering | 3.11 spec files · 3.12 injection, guards, doc comments, templates, routes |

**Three conventions used throughout.**

- Every non-obvious claim carries a link to the exact line, e.g.
  [apps/api-py/app/config.py:287](../../apps/api-py/app/config.py#L287). If a link and this
  prose disagree, the file wins — open it.
- A **> Why it is like this** callout quotes the repo's own comment explaining the past
  failure a rule exists to prevent. Those comments are the single most valuable habit in this
  codebase, and they are load-bearing: see §4.5.
- Counts (`43 fetch call sites`, `89 route decorators`) were measured, not remembered. They
  will drift. Re-measure before you cite one in a review.

> **Why it is like this.** There is **no linter, no type checker and no formatter** on either
> half of this repo — `apps/web` has no ESLint config, only a `.prettierrc`, and
> `apps/web/tsconfig.json` does not set `"strict": true`. The conventions in this chapter are
> not enforced by a tool. They are enforced by being written down and by the comments in the
> code. **Deleting one of those comments is a real change.**

---

## 2. The backend naming rulebook

Organised by artefact. Every rule has a cited example; every known exception is listed.

**Every Exceptions cell carries a one-word verdict**, because "there is an exception" and "you may
copy the exception" are different facts and a reader in a hurry cannot tell them apart:
**SANCTIONED** (deliberate, reasoned, copy it where the same reason applies) · **DRIFT** (a break
nobody defends — do not copy) · **UNADJUDICATED** (real, undocumented, and this book could not
establish which it is).

### 2.1 Modules and packages

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.1.1 | Service modules sit **flat at `app/` top level**, `lower_snake`, named for the **artefact** not the layer. There is no `services/` and no `utils/`. | `app/document_store.py`, `app/mailer.py`, `app/resume_pdf.py`, `app/retention.py`, `app/redaction.py`, `app/conversations.py`, `app/knowledge.py`, `app/interview_relay.py` | none |
| 2.1.2 | Router modules are **singular, named for the audience or the domain noun**; never `*_router`, never `*_api`. | `app/routers/student.py`, `mentor.py`, `director.py`, `leave.py`, `voice.py`, `interview.py`, `health.py` | none |
| 2.1.3 | Model modules are `snake_case`, one domain slice per file. | [apps/api-py/app/models/user.py](../../apps/api-py/app/models/user.py) holds four classes; `academic_history.py`, `job_import_run.py`, `voice_worker.py` | **SANCTIONED** — **`placement_criteria.py`**: `criteria` is the Latin plural and the class is `PlacementCriteria`, so the module tracks the class. **UNADJUDICATED** — **`academics.py`** is plural for no stated reason; it holds `SemesterResult`, `SubjectMark` and friends, i.e. a *subject area* rather than one entity. Do not generalise from it |
| 2.1.4 | A package `__init__.py` is **either a side-effect registry or empty** — never a re-export façade. | `app/models/__init__.py` is 31 `from . import x  # noqa: F401` lines; `app/routers/__init__.py`, `app/schemas/__init__.py` and `app/ai/__init__.py` are **0 bytes** | **SANCTIONED** — `app/eval/__init__.py` carries a docstring and nothing else; a docstring is not a façade |
| 2.1.5 | Models **must** be imported for the metadata side effect. Routers **must not** rely on import — they are live only via an explicit `include_router` in [apps/api-py/app/main.py:100-114](../../apps/api-py/app/main.py#L100-L114). | — | — |

**The asymmetry that bites.** Forgetting a router in `main.py` produces a 404 you notice in
five seconds. Forgetting a model in `app/models/__init__.py` is **silent**: Alembic
autogenerate emits no DDL for the new table, and — because the class is absent from
`Base.metadata` — may emit a `DROP` for it later. Nothing checks this.

### 2.2 Classes

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.2.1 | ORM classes are **PascalCase singular**. | `User`, `Student`, `SemesterResult`, `AcademicQualification`, `LabSession`, `PlacementOffer`, `VoiceWorkerHeartbeat`, `KnowledgeChunk` | **SANCTIONED** — `PlacementCriteria`: plural stem, singular class. English has no singular "criterion" the product uses, and the table is `placement_criteria`. Copy the *shape* only when the domain word is genuinely a Latin plural |
| 2.2.2 | Knowledge-Base classes take a `Knowledge` prefix. | `KnowledgeDocument` → `knowledge_documents`; `KnowledgeChunk` → `knowledge_chunks` ([apps/api-py/app/models/knowledge.py](../../apps/api-py/app/models/knowledge.py)) | — |
| 2.2.3 | Domain exceptions are **noun + past participle**, subclass a stdlib exception, carry an **end-user-readable** message, and their docstring states the *conditions*. | `class UploadRejected(ValueError)` — `"""The bytes are not an accepted file (bad type, empty, or too large)."""` ([apps/api-py/app/document_store.py:33-34](../../apps/api-py/app/document_store.py#L33-L34)) | **SANCTIONED** — AI-layer exceptions name a **state**, subclass `RuntimeError`, and drop the `Error` suffix: `LLMNotConfigured` ([app/ai/llm.py:34](../../apps/api-py/app/ai/llm.py#L34)), `StudentDataEgressRefused` ([:38](../../apps/api-py/app/ai/llm.py#L38)). `_SessionEnded(Exception)` ([app/interview_relay.py:352](../../apps/api-py/app/interview_relay.py#L352)) is private |
| 2.2.4 | Private classes take a leading underscore. | `_Embedder(NamedTuple)` ([app/ai/embeddings.py:39](../../apps/api-py/app/ai/embeddings.py#L39)); `_ConnLog`, `_ConnectionLimiter`, `_RelaySession` ([app/interview_relay.py:367](../../apps/api-py/app/interview_relay.py#L367), `:387`, `:463`) | — |
| 2.2.5 | Persistence singletons: engine lowercase, session factory PascalCase (it is used as a class), declarative base `Base`. | `engine`, `SessionLocal`, `Base` — [apps/api-py/app/db.py:16-21](../../apps/api-py/app/db.py#L16-L21) | — |

### 2.3 Functions

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.3.1 | Public functions are unprefixed; module-private functions take **one leading underscore**. | public: `save_bytes`, `read_bytes`, `delete`, `deliver_once`, `render_resume_pdf`, `get_db`, `purge_expired`, `redact_pii`, `search`, `embed` · private: `_sniff`, `_store_dir`, `_inline`, `_styles`, `_uuid`, `_utcnow`, `_now`, `_cosine` | **UNADJUDICATED** — [app/ai/adk.py:14](../../apps/api-py/app/ai/adk.py#L14) imports the private `_PROVIDERS` from [app/ai/llm.py:61](../../apps/api-py/app/ai/llm.py#L61): **the only cross-module import of a private name in `app/`**, and no comment defends it. The alternative — publishing `_PROVIDERS` — has never been proposed in the tree |
| 2.3.2 | **Dependency providers take `get_` and are public.** | `get_db` ([app/db.py:24](../../apps/api-py/app/db.py#L24)), `get_current_session` ([app/identity.py:8](../../apps/api-py/app/identity.py#L8)), `get_ws_session` ([app/identity.py:16](../../apps/api-py/app/identity.py#L16)), `convo.get_or_create` | **SANCTIONED** — `get_ws_session` is a dependency provider that is **not** used through `Depends(...)`: [interview.py:226](../../apps/api-py/app/routers/interview.py#L226) calls it inside a `try:` (`:225`) *after* `websocket.accept()` (`:203`), because a close sent before accept reaches the browser as a bare 1006 |
| 2.3.3 | Three guard prefixes, three return contracts. | `require_mentor(session) -> dict` returns the session ([mentor.py:31](../../apps/api-py/app/routers/mentor.py#L31)) · `_require_student(session) -> str` returns the student id ([student.py:118](../../apps/api-py/app/routers/student.py#L118)) · `_assert_can_access_student(session, student_id, db) -> None` raises only ([mentor.py:72](../../apps/api-py/app/routers/mentor.py#L72)) | — |
| 2.3.4 | ORM→schema mappers are `_<noun>_out(row, *extras) -> <Noun>Out`. | `_leave_out` ([leave.py:38](../../apps/api-py/app/routers/leave.py#L38)), `_alert_rule_out` ([director.py:205](../../apps/api-py/app/routers/director.py#L205)), `_note_out` ([mentor.py:101](../../apps/api-py/app/routers/mentor.py#L101)), `_alert_out` (`mentor.py:172`), `_upload_out` (`mentor.py:392`), `_claim_out` (`mentor.py:492`), `_offer_out` ([student.py:687](../../apps/api-py/app/routers/student.py#L687)), `_profile_out` (`student.py:771`) | **DRIFT** — 4 of the 12 break it; the full census is the table below 2.3.15 |
| 2.3.5 | **Write `_<noun>_out` even when the schema is named `…RowOut`** — the mapper's name follows the *noun*, not the schema's spelling. | `_upload_row` should have been `_upload_out`; `UploadRowOut` is still the return type | **DRIFT, do not copy — 4 of the 12 mappers**, enumerated below the table |
| 2.3.6 | A "now" helper is a private one-liner so a caller can pin the clock. | `_utcnow()` ([retention.py:37](../../apps/api-py/app/retention.py#L37)), `_now()` ([conversations.py:23](../../apps/api-py/app/conversations.py#L23), `voice.py:61`) | — |
| 2.3.7 | An injectable clock is a defaulted parameter resolved on the first line. | `def purge_expired(db: Session, now: datetime \| None = None)` then `now = now or _utcnow()` ([retention.py:41](../../apps/api-py/app/retention.py#L41)) | — |
| 2.3.8 | In a **router handler** the session parameter is `db: Session = Depends(get_db)`, injected **last**. | every handler, e.g. [mentor.py:46-47](../../apps/api-py/app/routers/mentor.py#L46-L47) | — |
| 2.3.9 | In a **non-router** function `db: Session` is the **first positional** parameter, and nothing opens its own session. | `deliver_once(db, *, kind, …)` ([mailer.py:28](../../apps/api-py/app/mailer.py#L28)), `get_or_create(db, user_id, role)` ([conversations.py:40](../../apps/api-py/app/conversations.py#L40)), `purge_expired(db, now=None)`, `redact_expired_runs(db, …)` ([retention.py:107](../../apps/api-py/app/retention.py#L107)) | **SANCTIONED** — `_open_conversation` ([interview.py:322](../../apps/api-py/app/routers/interview.py#L322)) **does** open its own `SessionLocal()`: it runs off-request on a worker thread via `asyncio.to_thread` and closes in a `finally:` |
| 2.3.10 | Keyword-only arguments after a bare `*`; only the payload is positional. | `complete_chat(messages, *, carries_student_data=…, temperature=…, json_mode=…, max_tokens=…)` ([llm.py:113-119](../../apps/api-py/app/ai/llm.py#L113-L119)); `deliver_once(db, *, kind, recipient, dedupe_key, …)` | — |
| 2.3.11 | Predicates naming a **condition** read as adjectival phrases; predicates naming a **type test** keep `is_`. | `student_data_egress_allowed()` ([llm.py:105](../../apps/api-py/app/ai/llm.py#L105)), `embedder_configured()` ([embeddings.py:62](../../apps/api-py/app/ai/embeddings.py#L62)) — versus `is_loopback()` ([llm.py:101](../../apps/api-py/app/ai/llm.py#L101)), `settings.is_prod` ([config.py:276](../../apps/api-py/app/config.py#L276)) | — |
| 2.3.12 | Factories are `build_*`, take no arguments, and read `settings` themselves. | `build_model`, `build_general_agent` (`app/ai/adk.py`, `app/ai/agents.py`) | — |
| 2.3.13 | Import aliasing only to dodge a collision, and the alias names the source module. | `from ..document_store import UploadRejected, delete as document_store_delete, read_bytes, save_bytes` ([student.py:14](../../apps/api-py/app/routers/student.py#L14)) — avoids SQLAlchemy's `delete()` | — |
| 2.3.14 | Handlers are `def`, not `async def`. | 87 of 89 routes | **SANCTIONED** — exactly two: [interview.py:193 `async def interview`](../../apps/api-py/app/routers/interview.py#L193) (a websocket) and [student.py:1352 `async def create_upload`](../../apps/api-py/app/routers/student.py#L1352) (awaits `UploadFile.read()`) |
| 2.3.15 | Student-router handler names: `my_<noun>` for the caller's own rows; a plain verb phrase for a global read, aggregate or mutation. | `my_profile`, `my_results`, `my_uploads` · `dashboard`, `apply_to_job`, `create_offer` | **DRIFT, do not copy** — `list_offers`, `list_resumes`, `resume_pdf`, `get_resume_profile` all return the caller's own rows and should be `my_*` |

**The mapper census, in full — 12 mappers, 8 conforming, 4 not.** Named here because
`grep -rn 'def _.*_out(' app/routers/` finds only eight of them, and a reviewer who trusts that
grep will believe four endpoints have no mapper. (Widening it to `_out(` does not rescue you: it
picks up `registration.py`'s bare `_out` and every call site, and still misses all three `_row`
mappers.)

| Mapper | Returns | Verdict |
|---|---|---|
| `_leave_out` ([leave.py:38](../../apps/api-py/app/routers/leave.py#L38)), `_alert_rule_out` ([director.py:205](../../apps/api-py/app/routers/director.py#L205)), `_note_out` ([mentor.py:101](../../apps/api-py/app/routers/mentor.py#L101)), `_alert_out` (`mentor.py:172`), `_upload_out` (`mentor.py:392`), `_claim_out` (`mentor.py:492`), `_offer_out` ([student.py:687](../../apps/api-py/app/routers/student.py#L687)), `_profile_out` (`student.py:771`) | `<Noun>Out` | conforming |
| `_focus_row` → `FocusRowOut` ([mentor.py:331](../../apps/api-py/app/routers/mentor.py#L331)) · `_upload_row` → `UploadRowOut` ([student.py:1335](../../apps/api-py/app/routers/student.py#L1335)) | a schema whose name contains `Row` | **DRIFT** — at least the name *mirrors* the schema. Do not copy |
| `_offer_row` → `PendingOfferOut` ([mentor.py:252](../../apps/api-py/app/routers/mentor.py#L252)) | no `Row` anywhere | **DRIFT** — sits beside four conforming `_out` names in the same file, and maps the same `PlacementOffer` table that `student.py:687` maps with `_offer_out`. Do not copy |
| `_out` → `RegistrationOut` ([registration.py:91](../../apps/api-py/app/routers/registration.py#L91)) | no noun at all | **DRIFT** — the worst of the four; a second mapper in that file would have nowhere to go. Do not copy |

### 2.4 Constants

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.4.1 | Module constants are `UPPER_SNAKE`: **public** when a test or another module must import them, `_`-prefixed when purely internal. | the roster below | **SANCTIONED ×2.** `_PRISMA_ONLY_PARAMS` ([config.py:292](../../apps/api-py/app/config.py#L292)) is a **class-level** private constant inside `Settings` — pydantic ignores underscore names as fields, which is why it works. `app/ai/orchestrator.py:61-72` declares its intent constants **public and un-prefixed** (`READINESS`, `GAPS`, `JOBS`, `SKILLS`, `PROFILE`, `DEADLINES`, `POLICY`, `GENERAL`, `STUDENT_DATA_INTENTS`) because `classify()`'s return value is the module's contract |
| 2.4.2 | Every constant carries a comment giving the **reason for the number**, not a restatement of it. | `MAX_BYTES = 10 * 1024 * 1024  # 10 MB`, with the module docstring's "Max 10 MB, matching the UI copy"; `_MAX_VEC_DISTANCE = 0.32` ([knowledge.py:56](../../apps/api-py/app/knowledge.py#L56)) under a **ten-line** calibration rationale ([knowledge.py:46-55](../../apps/api-py/app/knowledge.py#L46-L55)); every `interview_*` field in [config.py:118-149](../../apps/api-py/app/config.py#L118-L149) | — |
| 2.4.3 | A cap that is also a policy is **referenced by name** in the schema, never re-typed. | `text: str = Field(max_length=MAX_TRANSCRIPT_CHARS)` ([voice.py:391](../../apps/api-py/app/routers/voice.py#L391)) | **DRIFT, do not copy** — `ChatIn`/`AskIn` inline `max_length=4000` as a literal ([agent.py:124](../../apps/api-py/app/routers/agent.py#L124), `:139`), the same number as `MAX_TRANSCRIPT_CHARS`, not shared |
| 2.4.4 | Constants sit **above** the helpers that use them (§2.15). | `document_store.py:24-30`, `voice.py:43-58` | **UNADJUDICATED** — [conversations.py:148 `GREETING`](../../apps/api-py/app/conversations.py#L148) sits in the **middle of the module**, after six functions (`_now` `:23`, `current_conversation` `:27`, `get_or_create` `:40`, `assert_owner` `:76`, `append_message` `:92`, `history` `:133`) and immediately above the **one** function that uses it, `open_with_greeting` ([:182](../../apps/api-py/app/conversations.py#L182), which reads `GREETING` at `:186` and `:188`); `awaiting_first_reply` (`:151`) and `mark_greeted` (`:172`) never touch it, and the only other reader is out of module ([agent.py:288](../../apps/api-py/app/routers/agent.py#L288) `convo.GREETING`). No comment explains the placement. **SANCTIONED** — [voice.py:383-385](../../apps/api-py/app/routers/voice.py#L383-L385) likewise places its three caps 340 lines down, but that is the boxed-banner idiom of 2.7.7: the constants open a titled feature block |

**The constants roster for 2.4.1**, split by visibility:

| Visibility | Constants |
|---|---|
| **public** — a test or another module imports them | `MAX_BYTES` ([document_store.py:30](../../apps/api-py/app/document_store.py#L30)) · `REDACTED` ([redaction.py:17](../../apps/api-py/app/redaction.py#L17)) · `SOFT_DELETE_GRACE_DAYS` ([retention.py:34](../../apps/api-py/app/retention.py#L34)) · `RETENTION_DAYS` ([conversations.py:20](../../apps/api-py/app/conversations.py#L20)) · `GREETING` ([conversations.py:148](../../apps/api-py/app/conversations.py#L148)) · `HISTORY_LIMIT` ([agent.py:116](../../apps/api-py/app/routers/agent.py#L116)) · `SYSTEM_PROMPT` (`agent.py:107`) · `FRIENDLY_ERROR` (`agent.py:120`) · `MAX_TRANSCRIPT_CHARS` / `MAX_CONVERSATION_ID_CHARS` / `MAX_PROVIDER_TURN_ID_CHARS` ([voice.py:383-385](../../apps/api-py/app/routers/voice.py#L383-L385)) · `HEARTBEAT_FRESH_SECONDS` ([voice.py:43](../../apps/api-py/app/routers/voice.py#L43)) · `VOICE_AGENT_NAME` ([voice.py:58](../../apps/api-py/app/routers/voice.py#L58)) · `SESSION_COOKIE` / `SESSION_TTL_SECONDS` ([security.py:20-21](../../apps/api-py/app/security.py#L20-L21)) |
| **private** — `_`-prefixed, module-internal | `_MAGIC` ([document_store.py:24](../../apps/api-py/app/document_store.py#L24)) · `_SCRYPT` ([security.py:25](../../apps/api-py/app/security.py#L25)) · `_STAFF` ([mentor.py:28](../../apps/api-py/app/routers/mentor.py#L28)) · `_DIRECTORS` (`mentor.py:230`) · `_EMAIL` / `_PHONE` / `_USN` ([redaction.py:21-31](../../apps/api-py/app/redaction.py#L21-L31)) · `_LOOPBACK_HOSTS` / `_PROVIDERS` ([llm.py:31](../../apps/api-py/app/ai/llm.py#L31), `:61`) · `_CANDIDATE_POOL` / `_COSINE_WEIGHT` / `_MAX_VEC_DISTANCE` ([knowledge.py:43](../../apps/api-py/app/knowledge.py#L43), `:45`, `:56`) · `_CHANNEL` / `_CLOSE_NOT_A_STUDENT` / `_LIMITER` / `_LIVE_SESSIONS` ([interview.py:69-90](../../apps/api-py/app/routers/interview.py#L69-L90)) |

**Unit suffixes on constants are not uniform.** Three shapes coexist: `SESSION_TTL_SECONDS: int`
([security.py:21](../../apps/api-py/app/security.py#L21)), `HEARTBEAT_FRESH_SECONDS: int`
([voice.py:43](../../apps/api-py/app/routers/voice.py#L43)), and `HEARTBEAT_REAP_AFTER` /
`TOKEN_TTL` as bare `timedelta` objects with **no** unit suffix
([voice.py:48](../../apps/api-py/app/routers/voice.py#L48), `:51`) — there the type carries the unit.

### 2.5 Settings versus environment variables

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.5.1 | `Settings` fields are `snake_case`, mapping case-insensitively to `SCREAMING_SNAKE` env vars. | `database_url` ← `DATABASE_URL`; `voice_worker_secret` ← `VOICE_WORKER_SECRET` ([config.py:32](../../apps/api-py/app/config.py#L32), docstring at `:3`) | — |
| 2.5.2 | A **derived** value is never a second field — it is a read-only `@property` with no env var of its own. | `is_prod` ([:276](../../apps/api-py/app/config.py#L276)), `uploads_path` (`:280`), `allow_remote_student_data` (`:287`), `sqlalchemy_url` (`:295`), `livekit_ready` (`:268`), `gemini_key_present` (`:244`), `voice_model_key_present` (`:255`), `realtime_ready` (`:215`), `realtime_url` (`:227`), `realtime_beta_header` (`:239`) | — |
| 2.5.3 | **Boolean-ish settings are stored as `str`** so a blank line in a shared `.env` is legal; the coercion lives in the property. | `llm_allow_remote_student_data: str = ""` ([:46](../../apps/api-py/app/config.py#L46)) → `allow_remote_student_data` ([:287-288](../../apps/api-py/app/config.py#L287-L288), `.strip().lower() == "true"`); `openai_realtime_beta_header: str = ""` ([:116](../../apps/api-py/app/config.py#L116)) with the comment at `:113` naming the same reason | — |
| 2.5.4 | **Numeric settings get a `mode="before"` validator turning a blank string into the field default.** | `_blank_is_default` ([config.py:162](../../apps/api-py/app/config.py#L162)) over `llm_timeout_ms` plus the six `interview_*` numerics ([:152-158](../../apps/api-py/app/config.py#L152-L158)) | — |
| 2.5.5 | **Range and sign validation happens at startup, not mid-request.** | `_must_be_positive` ([config.py:193](../../apps/api-py/app/config.py#L193)), `_threshold_in_range` ([config.py:206](../../apps/api-py/app/config.py#L206)) | These three validators are the only `_`-prefixed **methods** on `Settings`; everything else private there is a constant |
| 2.5.6 | `_ms` on the env-facing value, `_s` on the internal one. | `llm_timeout_ms: int = 300000` ([config.py:43](../../apps/api-py/app/config.py#L43)) → `LLMConfig.timeout_s: float` ([llm.py:47](../../apps/api-py/app/ai/llm.py#L47)) | **SANCTIONED** — not universal. `interview_max_seconds` / `interview_idle_seconds` use `_seconds` on the env-facing value ([:121](../../apps/api-py/app/config.py#L121), `:125`) while `interview_vad_prefix_padding_ms` / `interview_vad_silence_duration_ms` use `_ms` ([:148-149](../../apps/api-py/app/config.py#L148-L149)) — **the suffix tracks the unit the upstream API wants, not the direction of travel** |
| 2.5.7 | A new provider key is documented in `.env.example`. | `SAKANA_API_KEY`…`COHERE_API_KEY` at `.env.example:36-41` | **SANCTIONED** — `VOICE_TTS` is documented at `.env.example:75` while having **no `Settings` field at all**, because it is worker-only: [voice_agent.py:127](../../apps/api-py/voice_agent.py#L127) reads it with `os.getenv`. Documenting a worker-only variable is correct; see §6.7 |
| 2.5.8 | `settings` is a **single import-time singleton** ([config.py:327](../../apps/api-py/app/config.py#L327)). A key pasted into `.env` does nothing until a genuine restart. | — | — |

**A documentation gap, not an exception to 2.5.7.** The rule is unenforced in **both** directions
and nothing greps for either half:

- `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_API_KEY`
  ([config.py:64-66](../../apps/api-py/app/config.py#L64-L66)) and `UPLOAD_DIR`
  ([config.py:273](../../apps/api-py/app/config.py#L273)) are real `Settings` fields that appear
  **nowhere** in `.env.example` — an operator cannot discover them.
- `VOICE_TTS`'s documented default disagrees with the one production compose file
  (`.env.example:75` and `voice_agent.py:127` say `edge`; `docker-compose.prod.yml:121` says
  `groq`).

Both are §8's problem to answer and §6.7's to prevent.

> **Why it is like this.** `config.py:151-183` explains `_blank_is_default`: `Settings()` runs
> at *import*, so a bare `LLM_TIMEOUT_MS=` used to raise a `ValidationError` **before uvicorn
> bound a socket** — the whole dashboard died at boot on a blank line in a file four processes
> share. The docstring also records that returning `PydanticUndefined` does *not* re-trigger
> default substitution in pydantic 2.13, which is why the validator returns the default itself.

### 2.6 Pydantic schemas

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.6.1 | `<Thing>In` for a request body, `<Thing>Out` for anything named in `response_model=`. Declared **immediately above** the handler, in the router that uses it. | `LeaveIn`/`LeaveOut`/`LeaveDecisionIn`, `NoteIn`/`NoteOut`, `UploadReviewIn`/`UploadOut`, `ChatIn`/`ChatOut`, `TranscriptIn`/`TranscriptOut`, `HeartbeatIn`, `ConsentIn`/`ConsentOut`, `SkillClaimReviewIn`/`SkillClaimReviewOut` | four, below |
| 2.6.2 | A shared **base envelope** is `<X>Response`, because it is a base class rather than any endpoint's return type. | `class AssistantResponse(BaseModel)` ([agent.py:153](../../apps/api-py/app/routers/agent.py#L153)), subclassed by `class AskOut(AssistantResponse)` ([agent.py:160](../../apps/api-py/app/routers/agent.py#L160)) which *is* the `response_model` | — |
| 2.6.3 | A nested element type **keeps the `Out` suffix**. Both spellings are in the tree, so this rule is stated to end the coin-flip: **write `Out`.** | keeps it (the majority and the rule): `ActionOut` and `SourceOut` nested in `AssistantResponse` (`agent.py:142`, `:148`), `SubjectMarkOut` nested in `SemesterResultOut` (`student.py:98`, `:114`) | **DRIFT, do not copy** — `LeaderRow` inside `LeaderboardOut.rows` ([student.py:1674](../../apps/api-py/app/routers/student.py#L1674)) and `KnowledgeHit` inside `KnowledgeSearchOut.results` ([agent.py:458](../../apps/api-py/app/routers/agent.py#L458)) drop it. Nothing in the code prefers them; they are simply older |
| 2.6.4 | `app/schemas/auth.py` follows neither convention, pinned by the external session contract. | `LoginRequest` ([schemas/auth.py:7](../../apps/api-py/app/schemas/auth.py#L7)), `SessionUser` (`:12`) with camelCase fields `userId`/`studentId`/`mentorId`; the reason is in the docstring at `:1-2` | — |
| 2.6.5 | The endpoint parameter carrying a body is always called `body`. | **26 occurrences** across the routers — agent 4, auth 1, director 1, leave 2, mentor 4, registration 2, student 9, voice 3; no other spelling exists | — |
| 2.6.6 | Always set `response_model=`. | **72 of the 89 route decorators do.** | **17 do not** — the full breakdown is the table below |
| 2.6.7 | Schema fields are **snake_case**; a column-backed field takes its column name verbatim, a derived field is named for its unit. | `ProfileOut` ([student.py:45-62](../../apps/api-py/app/routers/student.py#L45-L62)) mirrors `StudentProfile` exactly; `AttendanceSummaryOut.overall_percent` ([student.py:168](../../apps/api-py/app/routers/student.py#L168)) | **SANCTIONED** — the **three camelCase islands**: the JWT payload minted at [auth.py:29-40](../../apps/api-py/app/routers/auth.py#L29-L40), `SessionUser` mirroring it (§4.1), and the keys *inside* `alert_rule_configs.params` JSONB ([models/alert.py:73](../../apps/api-py/app/models/alert.py#L73)) |
| 2.6.8 | Enums are serialised with an explicit `.value` at the mapper boundary; never type a response field as a Python enum. | `role_type=offer.role_type.value` ([mentor.py:259](../../apps/api-py/app/routers/mentor.py#L259)), `kind=u.kind.value` (`student.py:1337`) | **DRIFT, do not copy** — on the way *in*, the codebase uses a hand-rolled `try: Enum(body.x) except ValueError` (offers, timesheet, checkin, uploads) rather than typing the field as the enum, which yields one flat 422 that cannot say which field was wrong |
| 2.6.9 | **Schema names are not globally unique** — so qualify a new one. | the collisions: `StatusOut` — [voice.py:166](../../apps/api-py/app/routers/voice.py#L166) (available/reason/worker_healthy/provider_ready/maintenance_message) and [interview.py:93](../../apps/api-py/app/routers/interview.py#L93) (available/reason/active_sessions/max_sessions). `DecisionIn` — `mentor.py:280` and `registration.py:172` | **DRIFT, do not extend** — four classes, two names, one OpenAPI document |

**The 17 routes with no `response_model=`**, because 2.6.6 is the rule most often broken by
accident and the legitimate reasons are finite:

| Why | Count | Where |
|---|---|---|
| health probes | 2 | `app/routers/health.py` — `/health`, `/ready` |
| `204 NO_CONTENT` deletes (no body to model) | 2 | — |
| SSE `StreamingResponse` | 1 | [agent.py:249](../../apps/api-py/app/routers/agent.py#L249) |
| binary file reads | 2 | [student.py:1041](../../apps/api-py/app/routers/student.py#L1041), `:1391` |
| websocket | 1 | [interview.py:192](../../apps/api-py/app/routers/interview.py#L192) |
| **bare `-> dict` handlers — the anti-pattern** | **9** | [agent.py:554 `/metrics`](../../apps/api-py/app/routers/agent.py#L554) · [auth.py:85 `/logout`](../../apps/api-py/app/routers/auth.py#L85) · [voice.py:110 `/heartbeat`](../../apps/api-py/app/routers/voice.py#L110) · six in `student.py`: `:638 /jobs/{job_id}/apply`, `:869 /timesheet`, `:923 /resume/generate`, `:1220 /checkin`, `:1250 /checkout/{session_id}`, `:1748 /leaderboard-visibility` |

The first five rows are **SANCTIONED**; the last is **DRIFT** — nine endpoints whose response
shape exists only in the handler body and in no schema, so nothing types them and nothing
documents them.

> **Chapter 7's advice, restated because it has already aged.** *"Give your request schema a
> qualified name — `LeaveDecisionIn`, not a third `DecisionIn`."* `DecisionIn` is two deep and
> `StatusOut` is now two deep as well. The OpenAPI document silently keeps whichever class FastAPI
> registered last under the shared name.

### 2.7 Routers and route paths

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.7.1 | **One** module-level `router = APIRouter(prefix=…, tags=[…])` per file, a bare domain prefix, a single-element `tags`. `/api` is added at include time. | `router = APIRouter(prefix="/student", tags=["student"])` ([student.py:42](../../apps/api-py/app/routers/student.py#L42)) + `app.include_router(student.router, prefix="/api")` ([main.py:110](../../apps/api-py/app/main.py#L110)) | **SANCTIONED ×2** — [agent.py:105](../../apps/api-py/app/routers/agent.py#L105), [voice.py:39](../../apps/api-py/app/routers/voice.py#L39) and [interview.py:62](../../apps/api-py/app/routers/interview.py#L62) carry `/api` themselves and are included without a prefix ([main.py:103-105](../../apps/api-py/app/main.py#L103-L105)). [health.py:24](../../apps/api-py/app/routers/health.py#L24) is a bare `APIRouter()` — no prefix, **no tags** — because probes are infra, not a domain area |
| 2.7.2 | Paths are lowercase and **hyphenated** for multi-word segments. | `/skill-claims`, `/resume-profile`, `/next-actions`, `/placement-readiness`, `/leaderboard-visibility`, `/alert-rules`, `/job-imports` | none found |
| 2.7.3 | Path parameters are `{snake_case}` named `<resource>_id`. | `/students/{student_id}/notes`, `/alerts/{alert_id}/resolve`, `/uploads/{upload_id}/review`, `/skill-claims/{claim_id}/review` | **SANCTIONED** — `/checkout/{session_id}` and `/focus/{session_id}/confirm` name the *role* (`session`) rather than the entity (`LabSession`), which is what a student calls the thing and what the surrounding path already says. Copy it only when the role word is unambiguous in that path |
| 2.7.4 | **Never write `/api` in a route path.** | — | — |
| 2.7.5 | The collection root of a prefixed router is the **empty string**, not `"/"`. | `@router.post("")` (`leave.py`, `registration.py`), `@router.websocket("")` ([interview.py:192](../../apps/api-py/app/routers/interview.py#L192)) | — |
| 2.7.6 | Custom wire headers are `X-<Product>-<Thing>`, declared as a snake_case `Header(...)` parameter; FastAPI derives the hyphenation. | `X-Voice-Worker-Secret` → `require_voice_worker` ([voice.py:65](../../apps/api-py/app/routers/voice.py#L65)) | — |
| 2.7.7 | Long routers separate feature blocks with a comment banner. **Three shapes exist — copy the right one.** | Titled rule `# --- Title ` + hyphens to 79 columns: `orchestrator.py:59`, `agent.py:494`, `assistant_tools.py:57`. Boxed (rule / padded title / rule, 79 columns): **`voice.py` only**, e.g. `:96-98`. Untitled 77-column rule (`# ` + 75 hyphens) sandwiching a title: the test files, e.g. `test_voice.py:30-33` | **DRIFT, do not copy** — the 77-column test-file shape now appears in **production** code — [app/interview_relay.py:74-76](../../apps/api-py/app/interview_relay.py#L74-L76) and six more (`:112`, `:173`, `:210`, `:256`, `:297`, `:458`). Chapter 2's "that shape describes the test files, not the routers" no longer holds. `student.py:1593` overruns its titled rule to 81 columns |

**The full mounted surface**, re-counted per file with
`grep -cE '^@router\.(get|post|put|patch|delete|websocket)' app/routers/*.py`:

| Prefix | Decorators |
|---|---|
| `/health` + `/ready` (unprefixed) | 2 |
| `/api/agent/*` | 9 |
| `/api/voice/*` | 5 |
| `/api/interview/status` + `/api/interview` (WS) | 2 |
| `/api/auth/*` | 3 |
| `/api/student/*` | **40** |
| `/api/mentor/*` | **13** |
| `/api/director/*` | 7 |
| `/api/leaves/*` | 4 |
| `/api/register/*` | 4 |
| **Total** | **89** |

### 2.8 Tables, columns, foreign keys, relationships, timestamps

The highest-traffic section in the chapter, so it is numbered like every other: **2.8.1** tables ·
**2.8.2** columns · **2.8.3** foreign keys · **2.8.4** relationships · **2.8.5** timestamps.

#### 2.8.1 Tables

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.8.1a | The table name is the snake_case **plural** of the class name. | `users`, `login_days`, `student_profiles`, `semester_results`, `time_sheet_entries`, `lab_sessions`, `placement_offers`, `swoc_entries`, `knowledge_chunks`, `voice_worker_heartbeats` | **SANCTIONED ×3** — `placement_criteria` (already a Latin plural), `certification_progress` (mass noun) and `assistant_feedback` (mass noun, [models/feedback.py:45](../../apps/api-py/app/models/feedback.py#L45)) |
| 2.8.1b | The table keeps the **class's** word boundaries, not the module's. | class `TimeSheetEntry` → table `time_sheet_entries`, while the *module* `timesheet.py` drops the boundary | — |

#### 2.8.2 Columns and primary keys

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.8.2a | Columns are snake_case, always. | verified: **every** SQL column in `app/models/` is snake_case | **none** |
| 2.8.2b | Primary keys are `id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)` — a `VARCHAR` holding `uuid4().hex`, not Postgres' native `uuid`. | [models/user.py](../../apps/api-py/app/models/user.py) and every sibling | **SANCTIONED** — a migration artefact inherited from the retired Prisma schema. Uniform, so keep it; do not introduce a native `uuid` column beside 31 `VARCHAR` ones |
| 2.8.2c | The suffix carries the **unit or the semantics** — the full suffix roster is the table immediately below. | `size_bytes`, `duration_min`, `ctc_inr`, `progress_pct`, `token_hash`, `apply_url` | four suffixes carry caveats: `_code`, `_date`, `_text`, `_json` — see the Caveat column |
| 2.8.2d | **Booleans are a bare adjective or participle with no `is_` prefix.** | `placement_eligible`, `interested_in_jobs`, `leaderboard_opt_out`, `auto_approve`, `enabled`, `verified`, `present`, `passed`, `mentor_confirmed`, `self_reported`, `active` | **DRIFT ×3** of the 17 boolean columns — see below the suffix table |

**Unit and semantic suffixes**, re-verified column by column:

| Suffix | Meaning | Verified examples | Caveat |
|---|---|---|---|
| `_id` | FK or soft reference | `student_id`, `semester_result_id`, `matched_rule_id`, `evidence_upload_id` | — |
| `_code` | FK onto a natural key | `course_code`, `cert_code`, `subject_code` | **SANCTIONED** — `schedule_items.course_code` is a plain `String` with the comment `# FK to Course later` ([models/schedule.py:33](../../apps/api-py/app/models/schedule.py#L33)) |
| `_at` | an instant, `TIMESTAMPTZ` | `created_at`, `check_in_at`, `reviewed_at`, `greeted_at`, `last_activity_at`, `meeting_at`, `triggered_at`, `resolved_at`, `uploaded_at` | **reliable** |
| `_on` | a day-grained event, still stored `TIMESTAMPTZ` | `posted_on`, `closes_on`, `taken_on`, `published_on` — all four `DateTime(timezone=True)` | — |
| `_date` | **type varies — check the column** | genuinely `Date`: `leave_requests.from_date`/`to_date` ([models/leave.py:49-50](../../apps/api-py/app/models/leave.py#L49-L50)). `TIMESTAMPTZ` despite the name: `certification_progress.due_date`, `cohorts.start_date`/`end_date`, `attendance_records.session_date`, `placement_offers.joining_date` | only 2 of 7 are `Date` |
| `day` (bare) | a real `Date`, the row's time dimension | `login_days.day` ([models/user.py:99](../../apps/api-py/app/models/user.py#L99)), `time_sheet_entries.day` | — |
| `_mo` | months | `twelfth_to_grad_mo`, `diploma_to_grad_mo`, `grad_to_pg_mo`, `other_mo` ([models/academic_history.py:54-57](../../apps/api-py/app/models/academic_history.py#L54-L57)) | — |
| `_min` | minutes | `duration_min` (`models/lab.py:80`) | — |
| `_ms` | milliseconds | `agent_runs.duration_ms` ([models/agent_run.py:53](../../apps/api-py/app/models/agent_run.py#L53)) — so `_ms` is a *column* suffix too, not only env-facing | — |
| `_pct` / `_percent` | percentages | columns always take `_pct`: `progress_pct`, `progress_delta_pct`, `min_attendance_pct`, `min_cert_completion_pct`, `min_reep_completion_pct` | **schemas spell it `_percent`** — `AttendanceSummaryOut.overall_percent` ([student.py:168](../../apps/api-py/app/routers/student.py#L168)), `JobRowOut.match_percent`. A bare `percent` also occurs where the qualifier is the schema itself (`CourseAttendanceOut.percent`, [student.py:164](../../apps/api-py/app/routers/student.py#L164)). **The column half of a percentage never crosses the wire under the same name** |
| `_hours` | hours | `teaching_hours` (`models/course.py:60`), `required_hours` (`models/certification.py:38`) | `students.weekly_hour_target` ([models/user.py:73](../../apps/api-py/app/models/user.py#L73)) is also hours and ends `_target` |
| `_inr` / `_bytes` | currency / size | `ctc_inr`, `fixed_gross_inr` (`models/offer.py:69-70`), `size_bytes` (`models/upload.py:60`) | — |
| `_no` | an ordinal | `session_no` (`models/attendance.py:30`) | — |
| `_url` | an absolute URL, always a nullable `String` | `source_url`, `apply_url`, `linkedin_url`, `github_url`, `portfolio_url` | — |
| `_hash` | a digest, never the secret | `password_hash` ([models/user.py:48](../../apps/api-py/app/models/user.py#L48)), `token_hash` (`models/registration.py:134`) | — |
| `_key` | an application-generated lookup key | `dedupe_key` (`models/mail.py:47`), `rule_key` (`models/alert.py:69`) | — |
| `_name` | a human or filesystem name | `file_name`, `original_name`, `stored_name`, `subject_name` | — |
| `_text` | free-form body copy | `note_text` (`models/mentor_note.py:37`), `chunk_text` ([models/knowledge.py:112](../../apps/api-py/app/models/knowledge.py#L112)) | **`chunk_text` is the ONLY `Text` column in the schema** — verified, one hit for `mapped_column(Text`. `note_text` is a plain unbounded `String` |
| `_json` | **not a convention — one collision workaround** | `knowledge_chunks.metadata_json` ([models/knowledge.py:118](../../apps/api-py/app/models/knowledge.py#L118)), the single instance, and only because SQLAlchemy reserves `metadata` on a declarative class | the other 15 JSONB columns carry plain domain names — `trace`, `citations`, `params`, `errors`, `context`, `data`, `content`, `evidence`, `scoring`, `bonuses`, `education`, `experience`, `projects`, `skills`, `achievements`. **Do not copy `_json`.** |
| `min_` / `max_` prefix | a threshold | `min_cgpa`, `max_live_backlogs`, `max_gap_months` | — |

**The boolean census (2.8.2d), all 17 columns.** Fourteen follow the rule. The three that do not
are **DRIFT — do not copy**, and they are three *different* shapes, which is why a new boolean
needs the rule and not a nearby example:

| Column | Shape | Verdict |
|---|---|---|
| `certifications.is_optional` ([models/certification.py:40](../../apps/api-py/app/models/certification.py#L40)) | `is_` prefix | DRIFT |
| `messages.is_final` ([models/conversation.py:120](../../apps/api-py/app/models/conversation.py#L120)) | `is_` prefix | DRIFT |
| `placement_criteria.require_core_certs` ([models/placement_criteria.py:26](../../apps/api-py/app/models/placement_criteria.py#L26)) | **verb phrase** — neither adjective, participle, nor `is_` | DRIFT |

#### 2.8.3 Foreign keys

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.8.3a | An FK column is `<singular target>_id`. | `student_id`, `semester_result_id`, `conversation_id`, `document_id`, `matched_rule_id` | — |
| 2.8.3b | `<target>_code` when the target's PK is a **natural code**. | `course_code`, `cert_code`, `subject_code` | **SANCTIONED** — `schedule_items.course_code` is a plain `String` with the comment `# FK to Course later` ([models/schedule.py:33](../../apps/api-py/app/models/schedule.py#L33)): a soft reference, deliberately not yet an FK, and the comment says so |
| 2.8.3c | **Where the role matters more than the target, the role wins.** | `requester_user_id`, `first_approver_user_id`, `owner_user_id`, `actor_id`, `evaluator_user_id`, `author_user_id`, `approved_by_id`, `reviewed_by_id`, `uploaded_by_id` | — |
| 2.8.3d | The `_id` is never dropped, even on a role name. | the five sibling review columns spell it in full: `registrations.reviewed_by_id`, `skill_claims.reviewed_by_id`, `uploads.reviewed_by_id`, `placement_offers.approved_by_id`, `job_import_runs.uploaded_by_id` | **DRIFT, do not copy** — `alerts.resolved_by`, below |
| 2.8.3e | FKs are almost never *named*; the name is only required by `op.create_foreign_key` in a later migration, where it is `fk_<table>_<target>`. | `fk_students_mentor` (`9ac9f4696b0d:45`), `fk_jobs_import_run` (`496d83735a1d:34`) — the complete list, two | — |

**The one confirmed FK-naming exception.** **`alerts.resolved_by`**
([models/alert.py:56](../../apps/api-py/app/models/alert.py#L56)) is a plain nullable `String`
with **no `_id`**, sitting directly beside `resolved_at` (`:55`). The module docstring at
[models/alert.py:3](../../apps/api-py/app/models/alert.py#L3) writes the pair as
"resolved_at/resolved_by", so the omission was deliberate at authoring time — but no comment gives
a reason, and it is inconsistent with all five siblings. **DRIFT. Do not copy it.**

**Structural note.** 56 `ForeignKey(...)` columns exist across 26 model modules, but only
**13 `relationship()` declarations across 5 modules** (`user.py`, `academics.py`,
`conversation.py`, `knowledge.py`, `skill.py`). FK columns are the norm; ORM relationships are
the exception.

#### 2.8.4 Relationships

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.8.4a | A relationship names **the thing on the other end** — singular for many-to-one, plural for one-to-many. | singular: `user`, `document`, `semester_result`, `conversation` · plural: `login_days`, `subjects`, `chunks`, `messages` | — |
| 2.8.4b | **Both sides name each other with `back_populates`.** `backref` appears nowhere. | verified: **zero** `backref` hits in `app/models/` | **DRIFT, do not copy** — `StudentSkill.skill: Mapped[Skill] = relationship()` ([models/skill.py:67](../../apps/api-py/app/models/skill.py#L67)) is a bare relationship with no reverse side and, being lazy by default, one extra SELECT per row in `GET /student/skills` |
| 2.8.4c | A forward reference is a **string literal** inside `Mapped["..."]` when the class is defined later in the module. | `Mapped["Student"]` | — |

#### 2.8.5 Timestamps

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.8.5a | **Every** `DateTime` is `DateTime(timezone=True)`. There are no naive columns. | verified across `app/models/` | **none** |
| 2.8.5b | The standard creation stamp is `created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())`. | [models/user.py:50](../../apps/api-py/app/models/user.py#L50) | present on **16 of the 31** model modules — **not** universal, so do not assume a table has one |
| 2.8.5c | `_at` for a recorded event; `_on` / `_date` / `_until` for a deadline or boundary; a verb participle for a liveness marker. | **30 of the 44** distinct `Mapped[datetime]` / `Mapped[date]` column names in `app/models/` end `_at`; `conversations.retention_until` ([models/conversation.py:81](../../apps/api-py/app/models/conversation.py#L81)); `voice_worker_heartbeats.last_seen` ([models/voice_worker.py:29](../../apps/api-py/app/models/voice_worker.py#L29)) | `_date`'s **type varies** — see the suffix table above; only 2 of 7 are a real `Date` |

### 2.9 Enums and their Postgres type names

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.9.1 | `class X(str, enum.Enum)`, PascalCase, members SCREAMING_SNAKE with the value **repeated verbatim** — so `VALUE == NAME` and `Role.MENTOR.value` is interchangeable with `"MENTOR"`. | `class Role(str, enum.Enum): STUDENT = "STUDENT"` … ([models/user.py:23-27](../../apps/api-py/app/models/user.py#L23-L27)) | none among the **32** enum classes |
| 2.9.2 | The PG type name is passed **explicitly** as `name=` and is the **snake_case singular** of the class. | `Role → "role"`, `RegistrationStatus → "registration_status"`, `CheckInSource → "check_in_source"`, `AgentRunStatus → "agent_run_status"`, `AlertRuleKey → "alert_rule_key"`, `CourseModel → "course_model"` | **DRIFT.** The single break: **`FeedbackRating → "feedbackrating"`** ([models/feedback.py:60](../../apps/api-py/app/models/feedback.py#L60)) — no underscore. It is exactly what SQLAlchemy would auto-derive from the class name, written out and never normalised. **Do not copy it.** |
| 2.9.3 | An enum is declared in the module that **first** needs it; a reusing module imports it and passes `create_type=False`. | `from .job import DegreeLevel` (`cohort.py`, `registration.py`), `from .user import Stage` (`course.py:57`), `from .course import ProgressStatus` (`certification.py:55`), `from .upload import UploadStatus` (`skill.py:91`), `from .user import Role` (`agent_run.py:39`, `conversation.py:68`) | **SANCTIONED, with a trap** — `create_type=False` on a **generic `sa.Enum`** is discarded by SQLAlchemy — only `postgresql.ENUM` honours it. In `app/models/` it is **documentary**; in migrations it is load-bearing |
| 2.9.4 | Two columns sharing one enum reuse a **single module-private `Enum` instance**, so the type is created once. | `_LEAVE_DECISION = Enum(LeaveDecision, name="leave_decision")` (`models/leave.py:37`), `_DEGREE_LEVEL = Enum(DegreeLevel, name="degree_level", create_type=False)` (`models/registration.py:42`) — SCREAMING_SNAKE **with** a leading underscore, unlike migrations' lowercase handles | — |
| 2.9.5 | **Values deliberately *not* enums.** Where the vocabulary is expected to grow, the column is a lowercase free `String` with a trailing comment listing the current values. This is a stated pattern. | `messages.sender  # 'user' \| 'assistant'` and `messages.channel` ([models/conversation.py:118-121](../../apps/api-py/app/models/conversation.py#L118-L121)), `conversations.channel`, `conversations.consent_state`, `knowledge_documents.source_type` / `audience`, `agent_runs.scope`, `agent_runs.intent`, `mail_logs.kind` | **`messages.channel` now carries a third value in production**: [interview.py:78 `_CHANNEL = "interview"`](../../apps/api-py/app/routers/interview.py#L78). The comment at [models/conversation.py:121](../../apps/api-py/app/models/conversation.py#L121) still reads `# 'text' \| 'voice'` and is **stale** |
| 2.9.6 | Enum values are UPPER_SNAKE in Python **and on the wire**, but lower-cased for metrics keys and in the Angular client — map explicitly at the boundary. | `FeedbackRating.NOT_HELPFUL` → wire `"NOT_HELPFUL"` → metrics key `"not_helpful"` (`agent.py`) | — |

### 2.10 Indexes and constraints

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.10.1 | Index names are `ix_<shortened table>_<shortened intent>`, spelled out explicitly in `__table_args__`. The name is the **first positional argument** to `Index(...)`; **no `Index(...)` in `app/models/` passes `name=`**. | `Index("ix_conversation_owner_activity", "owner_user_id", "last_activity_at")` ([models/conversation.py:47](../../apps/api-py/app/models/conversation.py#L47)) | — |
| 2.10.2 | **Both halves of the name are hand-shortened.** The table half is abbreviated and/or singularised. | de-underscored: `academic_qualifications → acadqual`, `lab_sessions → labsession`, `time_sheet_entries → timesheet`, `certification_progress → certprog`, `job_applications → jobapp`, `job_import_runs → jobimport`, `mentor_notes → mentornote`, `registration_rules → regrule`, `email_verifications → emailverif`, `mail_logs → maillog`, `agent_runs → agentrun`, `skill_claims → skillclaim` · singularised only: `alerts → alert`, `certifications → cert`, `conversations → conversation`, `messages → message`, `enrollments → enrollment`, `leave_requests → leave`, `mock_attempts → mock`, `placement_offers → offer`, `registrations → registration`, `resumes → resume`, `schedule_items → schedule`, `swoc_entries → swoc`, `uploads → upload`, `attendance_records → attendance`, `assistant_feedback → feedback` | **SANCTIONED** — shortened but keeping an internal underscore: `knowledge_documents → knowledge_doc`, `knowledge_chunks → knowledge_chunk`. **DRIFT, do not copy — copied whole, plural and all, exactly three cases:** `ix_courses_stage` (`models/course.py:53`), `ix_skills_category` (`models/skill.py:38`), `ix_student_skills_skill` (`models/skill.py:52`) |
| 2.10.3 | The column half is shortened to the reader's word for the thing, not the column name. | `ix_conversation_owner_activity` on `(owner_user_id, last_activity_at)`; `ix_alert_student_resolved` on `(student_id, resolved_at)` | **SANCTIONED** — one index names a **purpose** because it has no columns to name: `ix_knowledge_chunk_fts`, over `to_tsvector('english', chunk_text)` ([models/knowledge.py:100-104](../../apps/api-py/app/models/knowledge.py#L100-L104)) |
| 2.10.4 | **Single-column indexes are still named explicitly.** | `ix_upload_status`, `ix_upload_cert`, `ix_skills_category`, `ix_agentrun_status`, `ix_alert_rule`, `ix_courses_stage`, `ix_enrollment_course`, `ix_labsession_course`, `ix_jobapp_job`, `ix_cert_course`, `ix_feedback_run`, `ix_jobimport_started`, `ix_emailverif_registration`, `ix_knowledge_doc_source_type`, `ix_knowledge_chunk_document`, `ix_skillclaim_status`, `ix_skillclaim_skill`, `ix_student_skills_skill` | **DRIFT, do not copy.** **Exactly two columns in the whole schema take the inline `index=True` shortcut** and inherit an Alembic-generated name: `users.email` ([models/user.py:43](../../apps/api-py/app/models/user.py#L43) → `ix_users_email`) and `semester_results.student_id` ([models/academics.py:33](../../apps/api-py/app/models/academics.py#L33) → `ix_semester_results_student_id`). **A new single-column index declared inline is the break to avoid.** |
| 2.10.5 | Unique constraints are `uq_<singular thing being uniquified>` — singular even though the table is plural — and use `name=` (the constraint idiom, not the index one). | `UniqueConstraint("user_id", "day", name="uq_login_day")` ([models/user.py:95](../../apps/api-py/app/models/user.py#L95)); also `uq_semester_result`, `uq_subject_mark`, `uq_attendance`, `uq_enrollment`, `uq_timesheet`, `uq_job_application`, `uq_student_skill`, `uq_cert_progress`, `uq_alertrule_cohort_key`, `uq_feedback_run_owner`, `uq_message_provider_turn` — 12 in total | **14 columns take the inline `unique=True` shortcut instead**, with no name in the model — enumerated below the table. `uq_students_usn`, for example, exists only in the migration (`9ac9f4696b0d:44`) |
| 2.10.6 | A **partial unique index** still takes the `uq_` prefix, because the name should announce the invariant. | `Index("uq_conversation_one_active_per_owner", "owner_user_id", unique=True, postgresql_where=text("deleted_at IS NULL"))` ([models/conversation.py:56-61](../../apps/api-py/app/models/conversation.py#L56-L61)), under a nine-line comment explaining the race it closes | — |
| 2.10.7 | Foreign keys are almost never named — they are inline on the column (§2.8.3e). Only FKs added by a later migration with `op.create_foreign_key` are named `fk_<table>_<target>`, because that call **requires** a name. | `fk_students_mentor` (`9ac9f4696b0d:45`), `fk_jobs_import_run` (`496d83735a1d:34`) — the complete list, two | — |

**The 14 inline `unique=True` columns for 2.10.5**, in full — because "every known exception is
listed" is the promise this section makes, and a three-of-fourteen sample reads as exhaustive:

| Module | Column | Table |
|---|---|---|
| [models/user.py:43](../../apps/api-py/app/models/user.py#L43) | `email` (also `index=True`) | `users` |
| [models/user.py:61](../../apps/api-py/app/models/user.py#L61) | `user_id` | `students` |
| [models/user.py:63](../../apps/api-py/app/models/user.py#L63) | `usn` | `students` |
| [models/user.py:82](../../apps/api-py/app/models/user.py#L82) | `user_id` | `mentors` |
| [models/cohort.py:24](../../apps/api-py/app/models/cohort.py#L24) | `code` | `cohorts` |
| [models/job.py:42](../../apps/api-py/app/models/job.py#L42) | `source_ref` | `jobs` |
| [models/mail.py:47](../../apps/api-py/app/models/mail.py#L47) | `dedupe_key` | `mail_logs` |
| [models/student_profile.py:27](../../apps/api-py/app/models/student_profile.py#L27) | `student_id` | `student_profiles` |
| [models/registration.py:81](../../apps/api-py/app/models/registration.py#L81) | `email` | `registrations` |
| [models/registration.py:134](../../apps/api-py/app/models/registration.py#L134) | `token_hash` | `email_verifications` |
| [models/resume_profile.py:34](../../apps/api-py/app/models/resume_profile.py#L34) | `student_id` | `resume_profiles` |
| [models/skill.py:41](../../apps/api-py/app/models/skill.py#L41) | `slug` | `skills` |
| [models/upload.py:58](../../apps/api-py/app/models/upload.py#L58) | `stored_name` | `uploads` |
| [models/voice_worker.py:28](../../apps/api-py/app/models/voice_worker.py#L28) | `worker_id` | `voice_worker_heartbeats` |

**DRIFT, and the reason it matters:** every one of these carries an Alembic-generated constraint
name that exists only in a migration file. You cannot find the constraint from the model, and
`alembic revision --autogenerate` will not rename it for you. New unique constraints go in
`__table_args__` with an explicit `uq_` name.

### 2.11 Migrations

**Verified: 38 revision files, 38 `downgrade()` functions, zero bare-`pass` downgrades.**

- **Revision id** — 12 lowercase hex. Generated ids (`f65867efe738`, `6afb55d18ed8`) are the
  default, and accepting one is the default choice. There are **five** hand-written (banner-less)
  revisions, and **four of them** carry readable hex alongside a round `.000000` Create Date:
  `b7e2f4a19c33`, `d2f7a1c9e4b0`, `e1a7c9d34f20`, `f2b8d05e6a11`. The fifth, **`9ac9f4696b0d`**,
  carries an ordinary generated-looking id and an ordinary `03:29:02.166884` timestamp — so
  **readable hex is a habit of four files, not a rule the tree follows**, and you cannot identify
  a hand-written revision by its id. Chapter 4 (`04-migrations.md:348`, `:1911-1915`) records the
  same uncertainty.
- **Filename** — `<revision>_<slug>.py`. Over 40 characters Alembic trims back to the last
  complete word and re-appends `_`, which is where
  `f65867efe738_auth_slice_users_students_mentors_login_.py` comes from. **Do not rename the file.**
- **Message (`-m`)** — lowercase, space-separated. Creating tables → name them; altering →
  name the target or the behaviour.
- **Module-level enum handle** — lowercase, named after the Postgres type, declared between
  the four Alembic variables and `upgrade()`: `stage_enum`, `feedbackrating`. **No leading
  underscore** — migrations are scripts. Contrast 2.9.4, where shared enum instances in models
  take `_LEAVE_DECISION`.
- **Downgrades are never stubs.** If state cannot be restored — as `b7e2f4a19c33`'s dropped
  embeddings cannot — write the schema-level inverse anyway and say in a comment what is lost.
  Leave alone anything the revision does not solely own: an extension, or a type another
  revision created (`b7e2f4a19c33:52-54`).

**The enum checklist — the gotchas, each against a real revision.**

1. Adding an enum **column** to an existing table does **not** auto-`CREATE TYPE`. Create it
   explicitly first, then `create_type=False` on the column.
2. A **new table** reusing an **existing** enum must use
   `postgresql.ENUM(..., name='x', create_type=False)`. Autogenerate emits a bare `sa.Enum`
   that errors "type already exists" — hand-fix it, and import
   `from sqlalchemy.dialects import postgresql` exactly once.
3. Two columns sharing one enum reuse a single instance.
4. A **brand-new** enum gets `.create(op.get_bind(), checkfirst=True)` as the first statement
   of `upgrade()` and `.drop(op.get_bind(), checkfirst=True)` as the **last** of `downgrade()`.
   `feedbackrating` is the in-tree example.
5. **Adding a member** to an existing enum is hand-written, because `compare_type` does not
   diff members: `op.execute("ALTER TYPE <name> ADD VALUE IF NOT EXISTS 'X'")`. Postgres has
   **no `DROP VALUE`**, so `downgrade()` cannot mirror it — say so in a comment. *No revision
   in the repo does this yet; this item is a specification, not a copyable example.*
6. Drop only the types **this revision created**. Never drop a type you borrowed with
   `create_type=False`.
7. Expression, functional and partial indexes are hand-written with `op.execute` **outside**
   the autogenerate banner **in both halves**, mirrored in the model's `__table_args__`, and
   dropped with `DROP INDEX IF EXISTS`. `1aa19fa788e9` gets the upgrade right and the
   downgrade wrong — copy the upgrade.
8. Every new column on a populated table is nullable or carries a `server_default`.
9. `down_revision` is the previous head; `alembic heads` prints exactly one row.

### 2.12 Tests

| # | Rule | Example | Exceptions |
|---|---|---|---|
| 2.12.1 | `tests/test_<subject>.py`. | the **18 test modules** under `apps/api-py/tests/`, alongside `conftest.py` — 19 `.py` files in all | — |
| 2.12.2 | Test functions are **full sentences describing the pinned behaviour**. | `test_stray_conversation_id_cannot_reach_another_users_thread`, `test_greeting_survives_a_failed_first_turn`, `test_remote_egress_blocked_by_default`, `test_vector_threshold_preserves_the_honest_fallback`, `test_only_one_active_conversation_per_owner` | **DRIFT, do not copy** — a few short ones survive: `test_login_wrong_password_401`, `test_me_reflects_session` |
| 2.12.3 | Anything touching Postgres carries `@requires_db`, imported from `conftest`. | `requires_db = pytest.mark.skipif(not DB_UP, …)` ([tests/conftest.py:49](../../apps/api-py/tests/conftest.py#L49)) — a lowercase mark, alongside SCREAMING_SNAKE `DB_UP` (`:37`) and `REQUIRE_DB` (`:38`) | — |
| 2.12.4 | The assertion idiom is `assert r.status_code == 200, r.text`. | used in 8 test modules | — |
| 2.12.5 | Integer status literals are for **test assertions only**; application code uses `status.HTTP_*` constants. | **verified: zero `status_code=<int>` literals anywhere in `app/`** | — |
| 2.12.6 | Throwaway users carry a per-suite email prefix plus a uuid slice. | `f"convtest-{label}-{uuid.uuid4().hex[:8]}@bgscet.ac.in"` (`tests/test_conversations.py:63`), `voicetest-…`, `retention-…` | — |
| 2.12.7 | A test intending to be unauthenticated **must** call `client.cookies.clear()` first — the session-scoped `client` fixture retains cookies. | `test_unauthenticated_is_rejected` | — |
| 2.12.8 | A test stubbing the adapter must patch the **importing module's namespace** — `orchestrator.complete_chat`, `app.routers.agent.complete_chat` — never `app.ai.llm.complete_chat`. All three consumers use `from … import <name>` and bind at import time. **Patching the wrong target is a silent no-op that turns an offline test into a live network call.** | — | — |

### 2.13 Loggers

- `log = logging.getLogger(__name__)`, bound to the one-letter name `log`, **never `logger`**.
  Verified: 6 bindings, all named `log`, zero named `logger`. The five bound to `__name__`:
  [ai/embeddings.py:31](../../apps/api-py/app/ai/embeddings.py#L31),
  [ai/orchestrator.py:57](../../apps/api-py/app/ai/orchestrator.py#L57),
  [interview_relay.py:71](../../apps/api-py/app/interview_relay.py#L71),
  [routers/agent.py:103](../../apps/api-py/app/routers/agent.py#L103),
  [routers/interview.py:60](../../apps/api-py/app/routers/interview.py#L60).
- **Exception:** the single app-level **named** logger is dotted —
  `log = logging.getLogger("reep.startup")` ([main.py:29](../../apps/api-py/app/main.py#L29)).
  [main.py:61](../../apps/api-py/app/main.py#L61) also raises the third-party `websockets`
  logger to INFO.
- **Levels:** `log.exception` for a diagnosable failure (never `log.error` in the AI layer);
  `log.warning` for a recoverable anomaly; `log.error` where the message *is* the compensating
  evidence for a silent write path.
- **Messages:** a lowercase sentence naming the stage, `%s` lazy formatting with context
  arguments — `log.exception("orchestrator failed (intent=%s)", intent)`.
- **Correlation prefix:** the interview surface prefixes every line with `[conn=%s]` carrying a
  12-hex connection id ([interview.py:202](../../apps/api-py/app/routers/interview.py#L202), and
  throughout `interview_relay.py` via the `_ConnLog(logging.LoggerAdapter)` at `:367`). This is
  the only correlation-id convention in the backend.
- The API configures **no** logging itself — no `basicConfig`, no `dictConfig`, no config file.
  It relies entirely on uvicorn.

### 2.14 Errors and status codes

- Always the `status.HTTP_*` constants. Verified: **no integer literals in `app/`**, against
  **80 named-constant uses**.
- Detail strings are **complete sentences ending in a full stop, written for the end user**:
  `"Sign in required."`, `"Staff access required."`, `"Not a student account."`,
  `"Student not in your mentor group."`, `"Student not found."`.
- **Guard rejections never name the mechanism that refused.** The standing exception is
  *validation*: those messages do name the request field, and three are lowercase fragments —
  `"decision must be APPROVE or REJECT."` ([mentor.py:308](../../apps/api-py/app/routers/mentor.py#L308),
  `leave.py:126`), `"decision must be VERIFY or REJECT."` (`mentor.py:464`),
  `"decision must be GRANT or REJECT."` (`mentor.py:586`), `"Invalid linked_action."` (`mentor.py:146`).
- **Code vocabulary.** **404** for "not yours or not there" (`if row is None or row.student_id != student_id`)
  *and* for every cross-scope refusal · **409** for a wrong-state transition · **422** for a
  value the schema could not reject · **403** only from a role guard · **502** for a provider
  failure, with `FRIENDLY_ERROR` as the detail ([agent.py:236](../../apps/api-py/app/routers/agent.py#L236)) ·
  **503** for unconfigured credentials or maintenance · **204** with no body for soft deletes.
- Guards read `session.get("role")`; post-guard code may read `session["role"]`.
- **The WebSocket vocabulary is separate**: `WebSocketException`, not `HTTPException` — an HTTP
  exception on a WS scope emits an illegal `http.response.start`. Close code **1008** covers
  both "not signed in" and "not a student"
  ([interview.py:64-69](../../apps/api-py/app/routers/interview.py#L64-L69) documents why they
  deliberately share it), plus `_CLOSE_FORBIDDEN_ORIGIN`, `_CLOSE_NOT_CONFIGURED`,
  `_CLOSE_OVERLOADED`, `_CLOSE_INTERNAL` and `_CLOSE_GOING_AWAY` from `interview_relay.py`.

### 2.15 Module ordering

Uniform across `app/`, and **not** "all models then all routes":

1. **Module docstring** — a design brief, not a summary. It states the rule the module enforces
   and the failure it defends against, often with an indented endpoint block. (`document_store.py`'s
   two bullet rules, `mailer.py`'s at-most-once guarantee, `health.py`'s
   liveness-versus-readiness table.)
2. **Imports** — stdlib, blank, third-party (`fastapi` → `pydantic` → `sqlalchemy`), blank,
   relative `..`, then relative `.`. *(`student.py` and `director.py` each break this.)*
3. **Module-level singletons** — `router = APIRouter(...)` on one line, or `engine`/`SessionLocal`,
   or a `Driver` type alias.
4. **Constants**, `UPPER_SNAKE`, each with its justifying comment.
5. **Private helpers**, `_`-prefixed, declared before first use.
6. **Public API**, last.

In a router this repeats per feature: schema, mapper, handler; schema, mapper, handler.

**Verified exceptions to step 5** — helpers declared *after* first use:

- [student.py:118 `_require_student`](../../apps/api-py/app/routers/student.py#L118) is defined
  after `my_profile` (`:66`), which is exactly why `my_profile:69-73` **inlines the guard's
  body** instead of calling it — the most-cited duplication in the backend.
- [student.py:1335 `_upload_row`](../../apps/api-py/app/routers/student.py#L1335) is defined
  after its only caller `my_uploads` (`:1321-1331`), and carries a needlessly quoted forward-ref
  annotation `-> "UploadRowOut"` even though `UploadRowOut` is declared above it at `:1307`.

> **Why it is like this — the unwritten meta-convention.** *When a rule is unenforceable, write
> down the failure it prevents.* `config.py`'s `sslmode` paragraph, `mailer.py`'s
> recipient-fatigue argument, `memory.py`'s whole tombstone,
> [interview.py:64-90](../../apps/api-py/app/routers/interview.py#L64-L90)'s three constant
> rationales, [knowledge.py:46-55](../../apps/api-py/app/knowledge.py#L46-L55)'s distance-floor
> calibration. In a codebase with no linter, no type checker and no formatter, **the comments
> are the enforcement mechanism.**

---

## 3. The frontend naming rulebook

Organised by artefact, like §2. **Every rule has a cited example; every known exception is
listed, and each carries the same one-word verdict** — SANCTIONED / DRIFT / UNADJUDICATED. Where
a table has no Exceptions column the exceptions follow it in prose under a bolded
**Exceptions.** heading, so a `grep -n 'Exception' ` over this section finds all of them.

That symmetry matters more here than in §2: the front end has **no ESLint config, no `"strict":
true` and no formatter beyond `.prettierrc`**, so nothing but this list distinguishes a
deliberate deviation from rot.

Verified against `apps/web/src/` on this branch, which is ahead of what Chapters 12–14 describe;
nine rules changed and are corrected here.

### 3.1 The component file triplet

Every routed screen is a directory named for the *screen*, holding `<screen>.component.ts`
plus `.html` plus `.scss`, wired with `templateUrl: './x.component.html'` and the **singular**
`styleUrl: './x.component.scss'`. Student screens nest one level deeper under
`features/student/`. The house example is
[apps/web/src/app/features/student/jobs/](../../apps/web/src/app/features/student/jobs/).

**Verified: `grep -r styleUrls apps/web/src` returns 0 hits.** The array form does not appear
anywhere; the folder convention gives each screen exactly one sheet. If you reach for a second
sheet, the thing you actually want is a global class (§3.9).

**Exceptions, all real:**

- Small shared components inline `template:` and `styles: []` —
  [shared/kit/kit.components.ts](../../apps/web/src/app/shared/kit/kit.components.ts),
  `shared/icon.component.ts`, `shared/charts/bar-chart.component.ts`.
- `features/placeholder/placeholder.component.ts` inlines both — about 40 lines, one input.
- Twelve of the fifteen resume sections inline `template:`
  ([features/student/resume/sections/](../../apps/web/src/app/features/student/resume/sections/));
  only `basic`, `contact` and `family` keep a sibling `.html`, and **none** of the sections has
  a `.scss`.
- The root application files are the modern-scaffold style and the exception to everything:
  `app.ts`, `app.html`, `app.scss`, `app.spec.ts`, `app.config.ts`, `app.routes.ts`.
- [apps/web/src/app/app.scss](../../apps/web/src/app/app.scss) is **empty** — the only `.scss`
  under `app/` with no `:host` rule (20 sheets total, 19 with `:host`).

### 3.2 Class names

PascalCase ending in `Component`, matching the file stem: `jobs.component.ts` →
`JobsComponent`; `app-shell.component.ts` → `AppShellComponent`. Services are
`<name>.service.ts`; guards `<name>.guard.ts`; pure type or constant modules in `core/` drop
the suffix entirely (`core/session.ts`).

**Exceptions.** The root class `App` ([apps/web/src/app/app.ts](../../apps/web/src/app/app.ts))
is the **only** component with no `Component` suffix and the only one omitting
`standalone: true`. `kit.components.ts` is the only plural filename, holding five classes.
One screen has three spellings: directory `features/register/`, file
`registration.component.ts`, class `RegistrationComponent`, route path `register`. And
`features/student/overview/student-overview.component.ts` disagrees with its directory.

### 3.3 Selector prefixes — `app-`, `kit-`, `rb-`, and a fourth that is not Angular

`"prefix": "app"` is configured at
[apps/web/angular.json:18](../../apps/web/angular.json#L18). The area is folded into the
selector: `app-student-jobs`, `app-student-overview`, `app-bar-chart`, `app-shell`, `app-root`.

**43 distinct selectors, no duplicates** (`grep -rhoE "selector: *'[^']+'" app | sort -u`):

| Prefix | Count | Where it applies |
|---|---|---|
| `app-` | 21 | every routed screen, the shell, the root, and `shared/` components outside the kit — `app-root`, `app-shell`, `app-login`, `app-registration`, `app-assistant`, `app-placeholder`, `app-resume-builder`, `app-icon`, `app-bar-chart`, plus the twelve `app-student-*` screens |
| `kit-` | 5 | the shared kit only — `kit-page-intro`, `kit-section`, `kit-stat`, `kit-empty`, `kit-banner` |
| `rb-` | 17 | resume-builder sub-components only — the 15 sections plus `rb-preview` and `rb-all-resumes` |

**Exceptions.** `kit-` and `rb-` **SANCTIONED** — they deliberately break the configured `app`
prefix to mark two internal component families, and both are consistent within themselves.
Nothing catches the divergence: there is no ESLint config in `apps/web` at all, only a
`.prettierrc`.

**A fourth prefix exists and is not an Angular selector.**
[shared/voice-visualizer.ts:789-800](../../apps/web/src/app/shared/voice-visualizer.ts#L789-L800)
is `OVERLAY_CSS`, a template literal injected once per document as a **global** style element
(`OVERLAY_STYLE_ID = 'rvz-overlay-style'`, `:788`). It defines five rules — `.rvz-overlay`,
`.rvz-overlay.is-open`, `.rvz-overlay.is-settled`, `.rvz-overlay canvas`, and a
`@media (prefers-reduced-motion: reduce)` override — a runtime-injected CSS namespace outside
both the Angular selector system and the three stylesheets in §3.9.
`assistant.component.scss` overrides it at
`.orb.rvz-overlay`, and the specificity `(0,3,0)` against `(0,2,0)` is documented in that file's
header comment and is load-bearing.

**Adoption caveat on the kit.** Only **three** components import `shared/kit/kit.components` —
`assistant.component.ts`, `academics.component.ts`, `offers.component.ts` — and those three
templates are the only ones containing a `kit-*` element. `kit-stat` and `kit-banner` have **no
caller anywhere**. Two of the three importers are the broken orphan screens (§3.6). Treat the
kit as direction of travel, not established practice.

### 3.4 Signal and computed naming

State is `readonly` plus `signal<T>(init)`, camelCase, with no `$` suffix and no underscore on
the public name. `readonly` applies to the *signal*, not its value — you mutate via `.set()` and
`.update()`. Derived values are `computed()` on a `readonly` field with a **noun** name, never
`get*`.

| Suffix / name | Means | Verified example |
|---|---|---|
| `loading` / `loaded` / `state` | pending flag | `records.component.ts` carries `resultsState`, `attendanceState`, `academicsState` |
| `error` | the primary failure surface | universal |
| `<noun>Error` | a *per-panel* failure, so one dead endpoint degrades one card | `offersError`, `uploadError`, `claimError`, `claimsError`, `skillsError`, `actionError`, `formError`, `saveError`, `photoError`, `historyError`, `voiceError` |
| `saving` / `savedAt` / `dirty` | mutation lifecycle | [features/student/resume/resume-builder.service.ts:32](../../apps/web/src/app/features/student/resume/resume-builder.service.ts#L32) `saving`, `:34` `savedAt`, `:42` `dirty` — there is no `saved` signal |
| `<x>Id` | an armed or selected target | `removingId`, `replaceTargetId`, `photoId`, `claimSkillId`, `defaultId` |
| `*Count` | derived count | `oppCount`, `eligibleCount`, `appliedCount`, `offerCount` |
| `*Label` / `*Text` / `*Summary` / `*Breakdown` | derived text | `stageLabel`, `mockSummary`, `offerBreakdown`, `appliedBreakdown`, `durationLabel` |
| `*Rows` | filtered list | `levelRows`, `opportunityRows`, `appliedRows` |
| predicate names | derived boolean | `isSignedIn`, `anyFilter`, `hasGap`, `hasMocks`, `active` |
| `*Seq` / `*Gen` | monotonic guard | `transcriptSeq`, `sessionGen` (`core/chat-voice.service.ts`) |

**Verb prefixes on methods** — `load*` (private async, invoked from the constructor with
`void`), `on*` (a DOM handler bound from the template), `set*` (a mutator that may do more than
assign), `toggle*` (a binary flip). See
[features/student/jobs/jobs.component.ts:187](../../apps/web/src/app/features/student/jobs/jobs.component.ts#L187)
`loadJobs` and `:204` `loadOffers`.

**Private helpers carry no underscore** on the TypeScript side — `private clampH`,
`private skillIcon`, `private getJson`, `private joinSwoc`. This is the exact inverse of the
Python half (`_offer_out`, `_readiness_band`), so translating a helper across the boundary
means renaming it.

**Exception.** Mutable **form models** are plain objects, not signals, because `[(ngModel)]`
two-way-binds into them: `jobs.component.ts:124` (`form = this.blankForm()`),
`login.component.ts:53-54`, `registration.component.ts:53-59`, the resume sections' `m` /
`draft`. Where a select element must be signal-backed, the codebase uses the explicit pair
`[ngModel]="filterElig()" (ngModelChange)="filterElig.set($event)"` rather than mixing the two.
This is harmless until something `computed()`s over it —
`features/student/academics/academics.component.ts:60-66` crossed that line and its "Total gap"
is frozen at first read.

### 3.5 The private-underscore + `asReadonly()` idiom

A private writable signal takes a leading underscore; its public read-only projection drops it
and is produced by `.asReadonly()`, which returns a `Signal<T>` with no `.set()` on its type,
so the write path stays inside the owning class.

**Correction to Chapter 12 §§9–10**, which state this is "an `AuthService` convention with
exactly one instance in the whole app." **Verified: 11 `asReadonly()` hits across 2 files.**

- [core/auth.service.ts:25-26](../../apps/web/src/app/core/auth.service.ts#L25-L26) — the original pair:
  ```ts
  private readonly _session = signal<SessionPayload | null>(null);
  readonly session = this._session.asReadonly();
  ```
- [core/interview.service.ts:856-904](../../apps/web/src/app/core/interview.service.ts#L856-L904) —
  **ten** pairs, every one of them: `_state`/`state`, `_detail`/`detail`, `_notice`/`notice`,
  `_lines`/`lines`, `_userRms`/`userRms`, `_aiRms`/`aiRms`, `_micLevel`/`micLevel`,
  `_elapsedSeconds`/`elapsedSeconds`, `_sessionMaxSeconds`/`sessionMaxSeconds`,
  `_completedSessions`/`completedSessions`.

The newest service in the codebase adopts the idiom wholesale. **Write it as the rule for new
services**, not as an `AuthService` quirk.

**The two older services still do not follow it**, and you will read them:
`core/chat-voice.service.ts:161-183` exposes nine writable signals as bare `readonly` fields; and
[features/student/resume/resume-builder.service.ts:26-42](../../apps/web/src/app/features/student/resume/resume-builder.service.ts#L26-L42)
exposes seven (`data`, `completeness`, `loaded`, `saving`, `savedAt`, `error`, `dirty`). Sixteen
writable public signals against eleven encapsulated ones.

**A path worth noting while you are there.** `ResumeBuilderService` is the **only service in the
app that does not live in `core/`** — `core/` holds exactly four (`auth.service.ts`,
`chat-voice.service.ts`, `interview.service.ts`, `theme.service.ts`) plus `auth.guard.ts` and
`session.ts`. It sits beside its feature at
`apps/web/src/app/features/student/resume/`, which is defensible — nothing outside the resume
builder injects it — but it means a grep of `core/` does not find every `@Injectable`.

**One more underscore-signal that is not this idiom.**
`shared/charts/bar-chart.component.ts:59` — `private readonly _data = signal<BarDatum[]>([])`
backs an `@Input() set data` / `get data` pair. A setter-backed input, deliberately *not*
projected with `asReadonly()`.

**The feature-layer equivalent is simpler and is universal:** a plain `private readonly` signal
with no public projection — `private readonly rows = signal<CertRow[] | null>(null)`
(`features/student/certifications/certifications.component.ts:66`). Encapsulation in
`features/` is by TypeScript visibility, not by the readonly wrapper.

### 3.6 DTO interfaces mirror the server's snake_case verbatim

There is **no camelCase conversion layer** — no interceptor, no mapper, no helper. The
interface *is* the wire contract. Name it after the Pydantic model with `Out` dropped, copy the
fields character for character, and say so in a doc comment naming the endpoint and the source
schema.

```ts
/** Row shape of GET /student/jobs (snake_case, verbatim from JobRowOut). */
interface JobRow {
  id: string;
  degree_level: Level;
  apply_url: string | null;
  required_skills: string[];
  match_percent: number | null;
  ...
}
```
— [features/student/jobs/jobs.component.ts:35](../../apps/web/src/app/features/student/jobs/jobs.component.ts#L35)

**Verified: 19 DTO doc comments follow the house form**, including `certifications.component.ts:18`,
`jobs.component.ts:35` and `:52`, `leaderboards.component.ts:34`,
`student-overview.component.ts:86/98/112`, `records.component.ts:24/44/58`,
`resume/sections/attachments.component.ts:20`, `education.component.ts:24/52`,
`resume/views/all-resumes.component.ts:28`, `preview.component.ts:29`.
`records.component.ts:11` states the rule at file level: *"Interfaces are snake_case, verbatim
from student.py's \*Out models — no client…"*.

**Purely local view models keep camelCase precisely because they never cross the wire** —
`interface DeadlineInfo { tone; icon; label; closed }`. The casing tells you which side of the
boundary a type sits on.

> **Why it is like this.** `apps/web/tsconfig.json` does not set `"strict": true`, so
> `strictNullChecks` is off, and `angularCompilerOptions` does not set `strictTemplates`. Every
> `| null` in a DTO is decorative and every `(await res.json()) as T` is an assertion the
> compiler will never test. **The convention *is* the type check**, and it checks each field
> independently.

**The sanctioned exception** is `SessionPayload` — see §4.1.

**The unsanctioned breaks** (both compile green, both are orphan routes nobody can click to):

- `features/student/offers/offers.component.ts:23-41` — `interface Offer`, 17 fields, 11
  camelCase, cast at `:110` against a snake_case `OfferOut`. Only five fields can ever
  populate. **`jobs.component.ts:52-64` declares the same contract correctly**, ten snake_case
  fields matching `OfferOut`.
- `features/student/academics/academics.component.ts:19-33` — `maxMarks`, `twelfthToGradMo`,
  `diplomaToGradMo`, `gradToPgMo`, `otherMo`, plus a `Semester` shape `AcademicsOut` does not
  return at all; cast twice, at `:80` and `:117`. **`records.component.ts:58-81` and
  `resume/sections/education.component.ts:25-50` both declare it correctly.**

**The allowed escape.** If you want camelCase in your component, keep it out of the wire shape
and write the translation by hand. `features/student/profile/profile.component.ts` is the one
screen that camelCases and still works: `ProfileOut` (`:29-47`) stays snake_case verbatim, and a
**separate** local `Snapshot` (`:56-67`) holds the camelCase form model, translated explicitly
in `apply()` and in the PUT body (`:361-372`). An explicit boundary you can grep is allowed;
renaming the wire shape by declaration and trusting a cast to translate is not.

### 3.7 Module constants

Module-scope constants are `SCREAMING_SNAKE_CASE`. Millisecond values carry numeric separators.
Role- or enum-keyed lookup tables are typed `Record<K, V>` — so a missing key is a compile
error — and named `<THING>_FOR_ROLE` or `<DIMENSION>_<THING>`. **Their readers always apply a
fallback.** Verified across 60+ sites; representative:

- [core/session.ts:20](../../apps/web/src/app/core/session.ts#L20) — `HOME_FOR_ROLE: Record<Role, string>`.
  The `Record<Role, …>` annotation *is* the enforcement: add a member to `Role` without a key
  here and the build fails, which is what stops `navigateByUrl(undefined)` after login. Read in
  exactly one place, `login.component.ts:94`.
- `core/chat-voice.service.ts:90/102/115` — `LIVE_STATES`, `CONNECT_TIMEOUT_MS = 30_000`, `CLEAN_DISCONNECTS`.
- `core/interview.service.ts:54-272` — twenty-odd tunables, each with its derivation in a
  comment above it: `SAMPLE_RATE`, `CHUNK_MS`, `PLAYBACK_LEAD_S`, `FLUSH_RAMP_S`,
  `MAX_UPLINK_BUFFERED_BYTES`, `METER_*`, `AI_ANALYSER_FFT`, `DEFAULT_SESSION_MAX_S`,
  `CONNECT_TIMEOUT_MS = 30_000`, `AUDIO_DONE_TYPES`, `CLOSE_MESSAGES`.
- [core/theme.service.ts:13](../../apps/web/src/app/core/theme.service.ts#L13) — `STORAGE_KEY = 'reep-theme'`.
- [features/assistant/assistant.component.ts:77-112](../../apps/web/src/app/features/assistant/assistant.component.ts#L77-L112) —
  `CONSENT_KEY_PREFIX`, `STATE_LABELS`, `STATE_CAPTIONS`, `ORB_STATE`, all `Record<InterviewState, …>`.
- `features/login/login.component.ts:29` — `DEMO_PASSWORD`, `:31` `DEMO_ACCOUNTS`.
- `features/student/overview/student-overview.component.ts:155` — `STATUS_CHIPS`.
- `shared/charts/bar-chart.component.ts:27-28` — `ACCENT_LIGHT = '#8a5a1e'` /
  `ACCENT_DARK = '#d9a85f'`, **the sanctioned colour literals**: they feed ApexCharts' JS
  config, which cannot read a CSS custom property.
- `shared/kit/tone.ts:10` — `TONE_INK: Record<Tone, string>`.

**Exceptions.** Six resume sections declare a lowercase-value slice key as
`const KEY = 'experience'` and so on (`experience`, `internship`, `por`, `projects`,
`publications`, `seminars`) — SCREAMING_SNAKE name, string payload. Three arrays escape the
`Record` form because they are ordered, not keyed: `LEVEL_ORDER` (`academics.component.ts:42`),
`ROMAN` (`education.component.ts:68`), `LEVEL_NAMES` (`skilling.component.ts:59`).

### 3.8 CSS class conventions and the global/local split

**The split is the rule, and it is a shape, not a list.**

| | Global (Chapter 14 owns) | Component-local |
|---|---|---|
| Shape | **flat, lowercase, hyphenated, area-prefixed** | **BEM: `block__element--modifier`** |
| Modifiers | separate co-classes — `.chip.good`, `.dt-btn.sm`, `.card.err` — **never `--`** | `--` — `.field--invalid`, `.up-card--busy` |
| Examples | `.card`, `.chip`, `.badge`, `.ctrl`, `.field`, `.btn`, `.stepper`, `.dt-table`, `.dt-btn`, `.dt-header`, `.dense-grid`, `.dense-stat`, `.desktop-shell`, `.reg-frame`, `.ts-cell`, `.lb-row`, `.swoc-box`, `.res-*` | `.intro__title`, `.section__head`, `.stat__value`, `.chat__log`, `.msg__col`, `.msg--user`, `.vpanel__pulse--live`, `.brand-strip__name`, `.up-card--skeleton` |
| Lives in | `apps/web/src/styles/reep-v2.scss`, `reep-v2-resume.scss`, `reep-theme.scss` | the component's own `.scss` |

**BEM applies only inside a component stylesheet. Global classes are never BEM.** That
difference is itself the namespace — the BEM shape is the signal that a class is
component-scoped, and it is the closest thing this codebase has to a module boundary. A flat
English word in a component sheet is a name that can collide with a global, and *every* recorded
collision is exactly that: `.stepper` and `.dt-btn` (`uploads.component.scss:11`, `:381-398`),
`.badge` (`offers.component.scss:35-41`), `.fld` (duplicated in `academics.component.scss:21-28`
and `offers.component.scss:6-14`, already drifted on `.fld__input`'s `background`), `.reqp`
(three copies). Prefix the block and the collision cannot arise.

**Verified counts in the working tree: 99 distinct `block__element` names and 21
`block--modifier` names** under `features/`. (Chapter 13 §1 says 113 and 28; the assistant
rewrite accounts for the difference.)

**`:host` — correction.** Every component stylesheet opens with a `:host` rule and, verified,
**19 of 19 use `display: block`** — Angular components are inline by default and the v2 grid
needs a block. Chapter 12 §10 names `assistant.component.scss:1-6` as a
`display: flex; height: 100%` exception; **that exception no longer exists**.
[features/assistant/assistant.component.scss:31-33](../../apps/web/src/app/features/assistant/assistant.component.scss#L31-L33)
is now a plain `:host { display: block; }`. The only file without a `:host` rule is the empty
`app/app.scss`.

**Global reuse is the dominant practice** (template occurrences): `.card` 81, `.ctrl` 54,
`.field` 78, `.btn` 55, `.chip` 49, `.dt-btn` 20, `.dt-header` 12, `.dense-stat` 5,
`.dt-table` 3.

**Live defect to know about.** `.reep-h6` is applied at
[features/assistant/assistant.component.html:67](../../apps/web/src/app/features/assistant/assistant.component.html#L67)
and `:152` and **is defined nowhere** —
[styles/reep-theme.scss:147-150](../../apps/web/src/styles/reep-theme.scss#L147-L150) stops at
`.reep-h4`. The consent-dialog and transcript titles fall back to the browser's default heading
size.

**Icon glyphs** are Material Symbols ligature names written as element text content, snake_case:
`check_circle`, `radio_button_unchecked`, `hourglass_top`, `workspace_premium`.

### 3.9 The two CSS custom-property generations — generation 2 is current

[apps/web/src/styles.scss](../../apps/web/src/styles.scss) loads three sheets, and **the order
is load-bearing**:

Quoted verbatim from [apps/web/src/styles.scss:1-17](../../apps/web/src/styles.scss#L1-L17) —
the three `@use` lines and the comments that actually sit above them:

```scss
/* Global styles for the Angular REEP app.
 *
 * The design system lives in reep-theme.scss — the exact tokens ported from the
 * Next.js app's src/theme.ts, so a component here reads the identical CSS
 * variable an MUI component read there. Everything visual is built on those
 * tokens; nothing hard-codes a colour. */

@use './styles/reep-theme';

/* REEP v2 design system — the exact tokens and component classes from
 * docs/design-v2/student-app.html, global and unscoped. Loaded after
 * reep-theme so the v2 body/typography rules win where the two overlap. */
@use './styles/reep-v2';

/* Resume Builder component classes (docs/design-v2/resume-builder.html) that
 * reep-v2 does not already define. Loaded LAST so it wins on equal specificity. */
@use './styles/reep-v2-resume';
```

Note what the file itself does **not** say: it never calls `reep-theme` legacy or frozen. That
judgement is this book's, drawn from the adoption counts below — the file still describes
generation 1 as "the design system".

| | Generation 1 | **Generation 2 — use this** |
|---|---|---|
| File | `apps/web/src/styles/reep-theme.scss` | `apps/web/src/styles/reep-v2.scss:20-47` |
| Prefix | `--reep-*` — **42 distinct tokens in 80 declarations.** 38 are declared twice, light at [`:root`, reep-theme.scss:25](../../apps/web/src/styles/reep-theme.scss#L25) and dark at [`:root[data-theme='dark']`, `:85`](../../apps/web/src/styles/reep-theme.scss#L85); the four theme-invariant ones (`--reep-font-stack`, `--reep-radius`, `--reep-radius-chip`, `--reep-radius-control`) are declared once. **The doubling *is* the dark-mode support** — which is exactly what generation 2 does not have | unprefixed semantic tokens |
| Tokens | `--reep-success-main`, `--reep-error-main`, `--reep-secondary-main`, `--reep-text-primary`, `--reep-bg-default`, `--reep-bg-paper`, … | `--ink-900/800/700/500/400`, `--paper-0/1/2`, `--amber-700/600/500/400/300`, `--line`, `--good` `#5c7a3a`, `--warn` `#a8752f`, `--risk` `#8b3a2e`, `--radius-lg/md/sm`, `--shadow-lift`, `--shadow-soft`, `--edge-hi`, `--press`, `--ring`, `--font` |
| Provenance | ported from the Next.js app's `src/theme.ts` | a verbatim port of the style block in `docs/design-v2/student-app.html` |

**Rule.** Use generation 2 in all new code. Do **not** introduce a new `--reep-*` token, do not
use one in a v2 screen, and do not write a dark-mode block — the v2 tokens have no dark values.

**Generation 1 cannot be deleted.** Verified: **four** component sheets still read
`var(--reep-*)` — `assistant.component.scss`, `login.component.scss`, `academics.component.scss`,
`offers.component.scss` — plus `shared/kit/tone.ts:10-17`, whose entire `TONE_INK` map is
generation-1 tokens. Fifteen component sheets read generation-2 tokens.

**Corollary.** Never reorder `styles.scss`, and keep `angular.json`'s `styles` array at **one**
entry ([apps/web/angular.json:32-33](../../apps/web/angular.json#L32-L33)). Reorder it and
`reep-theme`'s `body` font-family and background beat v2's, and `reep-v2-resume`'s `.btn`,
`.ctrl`, `.chip.neutral`, `.card > h3` and `.card > .desc` lose — flipping every button in 23
files.

**The tint trap.** Every tone tint is a **hand-expanded `rgba()` of a token's hex, not a
reference**: `.chip.good`'s `rgba(92,122,58,0.12)` is `--good` `#5c7a3a` written out by hand.
Same for `.chip.warn`, `.chip.risk`, `.swoc-s/w/o`, `.reg-approval.ok/.flag`, `.autofill-note`,
`.notice.info/.evi`, `.tag.evi`, `.evi-tag`, `.iconbtn:hover`. **Change a hex in `:root` and
every tint keeps the old hue, silently, with no build error.** Retuning a status colour means
grepping for its rgb triple in the same commit.

**Where the tones live.** `styles/reep-v2.scss:184-205` defines only `.chip`, `.chip.good`,
`.chip.warn`, `.chip.risk` and `.chip .icon`. **`.chip.neutral` is in
`styles/reep-v2-resume.scss:519`** — which is why the local `.chip.neutral` in
`uploads.component.scss:401-404` is *not* a redefinition: there is no `neutral` tone in
`reep-v2.scss` to collide with.

**Where a new global class goes:** `styles/reep-v2.scss`, in its family's section, and only when
**three or more unrelated screens** need the identical *primitive*. Not `reep-v2-resume.scss`
(its charter is "what reep-v2 does not already define"; already the source of the `.btn`
collision), and never `reep-theme.scss` (frozen).

### 3.10 `localStorage` key naming

Keys are kebab-case with a `reep-` prefix. **Per-user keys append the user id after a colon** —
never a bare shared key. The two keys in the app:

- [core/theme.service.ts:13](../../apps/web/src/app/core/theme.service.ts#L13) —
  `const STORAGE_KEY = 'reep-theme';` (global, correctly so: a theme is per-machine).
- [features/assistant/assistant.component.ts:77](../../apps/web/src/app/features/assistant/assistant.component.ts#L77) —
  `const CONSENT_KEY_PREFIX = 'reep-interview-consent:';`. **Correction:** Chapters 11 §9 and
  12 §10 both cite `'reep-voice-consent:'` at `assistant.component.ts:51`; the interview rewrite
  renamed both the value and the line. The key is **built** by `consentKey()` at
  [`:394-396`](../../apps/web/src/app/features/assistant/assistant.component.ts#L394-L396),
  **written** at [`:308`](../../apps/web/src/app/features/assistant/assistant.component.ts#L308)
  (`localStorage.setItem(key, 'true')`) and **read** at
  [`:405`](../../apps/web/src/app/features/assistant/assistant.component.ts#L405)
  (`localStorage.getItem(key) === 'true'`).

> **Why it is like this.** The header comment at
> [features/assistant/assistant.component.ts:66-72](../../apps/web/src/app/features/assistant/assistant.component.ts#L66-L72)
> records the incident: the key was once shared, and *"REEP runs on shared lab PCs: once any one
> student accepted, every student who signed in on that machine afterwards had the disclosure
> silently suppressed and went straight into a live microphone session having never been shown
> what it does with their audio."* Both readers also guard the no-user case — `if (!key) return
> false` shows the disclosure rather than assuming a previous student's acceptance covers this
> one — and both wrap the call in `try/catch` so blocked storage merely re-shows the disclosure.

### 3.11 Spec files

Specs are `<name>.spec.ts` beside the file they test, run with
`cd apps/web && npx ng test`. **There are exactly two, and this is the honest state of frontend
test coverage:**

- [apps/web/src/app/app.spec.ts](../../apps/web/src/app/app.spec.ts) — two tests; asserts the
  root component mounts and provides a router outlet. Its header records why it exists: the
  `ng new` scaffold asserted a heading containing "Hello, web" against a template that never had
  one, so `ng test` failed on a fresh checkout — *"a suite that is always red is a suite nobody
  reads."*
- [apps/web/src/app/core/chat-voice.service.spec.ts](../../apps/web/src/app/core/chat-voice.service.spec.ts) —
  two `describe` blocks, five tests: transcript merging (`:41`, `:73`, `:82`, `:99`) and `ask()`
  failure marking (`:130`).

**No feature component has a spec. `core/interview.service.ts` — 1,585 lines, the newest and
most intricate file in the front end — has no spec.**

### 3.12 Injection, guards, doc comments, templates, routes

**Injection.** Always a field initialiser, never a constructor parameter:
`private readonly auth = inject(AuthService);`. Verified: **30 non-spec `inject()` calls, 0
Angular DI constructor parameters.** Twenty-eight are field initialisers; the other two are
**locals inside a functional guard** —
[core/auth.guard.ts:16-17](../../apps/web/src/app/core/auth.guard.ts#L16-L17),
`const auth = inject(AuthService); const router = inject(Router);` — which is the correct shape
there, because a `CanActivateFn` is a function and has no fields. This works only because both
field initialisers and a guard invocation run inside an
injection context; the same call in a method compiles and throws `NG0203` at runtime. Every
service is `@Injectable({ providedIn: 'root' })` — five of them.

*The single `constructor(private readonly ...)` in the tree is not a DI site:*
`core/interview.service.ts:680` is a plain non-Angular helper class (`PlaybackScheduler`) taking
a shared `AudioContext`. It does not break the rule.

**Guards.** A lowerCamelCase exported `const` with a `Guard` suffix, typed as the functional-guard
type, with unused parameters underscore-prefixed —
[core/auth.guard.ts](../../apps/web/src/app/core/auth.guard.ts):

```ts
export const authGuard: CanActivateFn = async (_route, state) => { … };
```

**Doc comments.** A file header on every file explaining **why** — usually naming the React
original it was ported from and what bit them — and Rust/Dart-style `///` triple-slash for
member-level notes.

**Templates.** Built-in control-flow blocks **exclusively**. Verified: a grep for the structural
directives and `CommonModule` across `apps/web/src` returns **0 hits**. Use `@if` / `@else if` /
`@else`, `@for … track` (every `@for` carries a `track`), `@empty`, the `; as` narrowing alias,
and `@let` to call a per-row helper once (`@let s = statusChip(a.status);`).

**Interfaces and unions.** Domain unions are string-literal unions — SCREAMING_SNAKE where they
mirror a backend enum (`type Role`, `type Level = 'UG' | 'PG'`, `type RoleType`), lowercase where
purely client-side (`type Mode = 'light' | 'dark'`, `type LoadState`, `type VoiceState`,
`type InterviewState`, `type NoticeTone`).

**Routes.** Lowercase kebab-case segments namespaced by role (`student/time-log`,
`director/registrations`); **no path parameters anywhere** — the whole table is static strings.
Nav labels are allowed to diverge from path segments (`student/time-log` → "Time Sheet",
`student` → "Landing"). Route metadata is `data: { title }` consumed by an identically-named
`@Input() title`; Angular's own `title:` route property is **never** used, which is why the
browser tab reads `REEP Dashboard` on every screen.

---

## 4. The cross-cutting conventions

Five things happen at the seam between Python and TypeScript. Get them wrong and nothing fails
loudly.

### 4.1 The camelCase session island

**One camelCase wire shape is sanctioned, and only one.**

Client — [apps/web/src/app/core/session.ts:8-17](../../apps/web/src/app/core/session.ts#L8-L17):

```ts
export type Role = 'STUDENT' | 'MENTOR' | 'DIRECTOR' | 'ADMIN';

export interface SessionPayload {
  userId: string;
  email: string;
  name: string;
  role: Role;
  /// Present for STUDENT, absent otherwise.
  studentId?: string;
  mentorId?: string;
}
```

Server — [apps/api-py/app/schemas/auth.py:1-2](../../apps/api-py/app/schemas/auth.py#L1-L2),
verbatim:

```python
"""Request/response models for auth. Field names mirror the Next.js session
payload (camelCase) so the Angular client is unchanged across the cutover."""
```

…and `:12-18` declares `SessionUser` with `userId`, `email`, `name`, `role`, `studentId`,
`mentorId`.

> **Why it is like this.** It is a **migration artefact, deliberately frozen**. The Angular
> client was written against a Next.js/NestJS session payload; making FastAPI mirror the casing
> meant the auth half of the client needed no changes across the cutover. It is the *server*
> that bends to the client here, and the server says so in a docstring — that mutual, documented
> agreement is exactly what "sanctioned" means.

Every other camelCase wire shape in the client (§3.6) is a break, and the diagnostic is simple:
**is there a matching server-side declaration that says why? If not, it is a bug.**

**Two known mismatches inside the island itself.** The server types `role` as a bare `str`, so a
new backend role falls silently outside the client's union. And `studentId`/`mentorId` arrive as
JSON `null`, never as absent keys, so the `/// Present for STUDENT, absent otherwise.` comment is
wrong about the wire. A third: `session.ts:1-6` still describes the backend as "the NestJS
backend" — **STALE**, three stacks ago.

`HOME_FOR_ROLE: Record<Role, string>`
([core/session.ts:20-25](../../apps/web/src/app/core/session.ts#L20-L25)) is the island's
enforcement mechanism — the `Record` annotation makes a missing role a compile error. `ADMIN`
deliberately aliases the director home because ADMIN has no screens.

### 4.2 The snake_case wire format, with no conversion layer

**Everything except the session island crosses as snake_case, and nothing converts it.** No
interceptor, no mapper, no `camelize` helper, no serializer alias generator. The client's
`interface` *is* the contract, consumed through an unchecked `as T` — and with `strict` off in
`apps/web/tsconfig.json`, the compiler will never test the assertion.

This is why §3.6's doc-comment convention is load-bearing rather than decorative: **it is the
only type check that exists**, it is applied by a human reading two files, and it checks each
field independently. The offers screen is the proof — seventeen declared fields against ten real
ones, five passing, twelve unreachable, green build, silent `undefined`s.

**The corollary for anyone adding an endpoint: do not "fix" this by adding a conversion layer on
one side.** A partial conversion is strictly worse than none, because it makes the casing of a
field stop predicting which side of the boundary it lives on — which is the property §3.6 relies
on.

### 4.3 Enums cross as `.value`

A Python `Enum` column is never serialised as the enum object. The router reads `.value` at the
boundary, the `*Out` schema types the field as a plain `str`, and the client declares it as a
**string-literal union**, SCREAMING_SNAKE to match.

**Verified — 61 `.value` reads across `apps/api-py/app/routers/`** (`grep -oE "\.value\b"`, which
excludes the two `.values(` calls): `student.py` 30, `mentor.py` 10, `agent.py` 5,
`director.py` 5, `registration.py` 3, `voice.py` 3, `interview.py` 2, `leave.py` 2,
`auth.py` 1. Representative: `user.role.value`,
`offer.status.value`, `offer.role_type.value`, `o.work_mode.value`, `r.degree_level.value`,
`q.level.value`, `student.current_stage.value`, `enr.status.value`, `course.stage.value`.

**A cited round trip.** Server: `channel: str`
([student.py:679](../../apps/api-py/app/routers/student.py#L679)) filled from `o.channel.value`
(`:693`). Client:
`type Channel = 'ON_CAMPUS' | 'OFF_CAMPUS' | 'POOL' | 'REFERRAL'`
(`features/student/offers/offers.component.ts:19`). The two vocabularies match by hand.

**The consequence to remember:** because the schema types it `str`, **adding a member to a Python
enum is invisible to the TypeScript union**. That is precisely why the status lookup tables in
§5.7 must be total — an unmapped new enum value must degrade to a neutral chip printing the raw
string, not to a blank one.

**The client's own enums invert the casing rule.** Purely client-side unions are lowercase kebab
strings, never a TypeScript `enum` — `type Mode = 'light' | 'dark'`,
`type LoadState = 'loading' | 'ready' | 'error'`, `type VoiceState` (`'permission-check'`,
`'reconnecting'`), `type InterviewState`, `type NoticeTone = 'info' | 'warn' | 'error'`.
**Casing tells you the origin: SCREAMING_SNAKE came from Python; lowercase-kebab was born in the
browser.**

One documented conversion point, and it is at the HTTP boundary only: assistant **ratings are
lowercase in the client and uppercase on the wire**, converted with `rating.toUpperCase()` at
the call site and nowhere else.

### 4.4 Status codes as constants, and the shape of an error body

Every HTTP status on the server is a named `fastapi.status` constant. **Verified:
`grep -rhoE "status_code=[0-9]+" apps/api-py/app/` returns zero matches**, against **80
named-constant uses**: `HTTP_404_NOT_FOUND` ×25, `HTTP_422_UNPROCESSABLE_ENTITY` ×15,
`HTTP_409_CONFLICT` ×10, `HTTP_403_FORBIDDEN` ×9, `HTTP_201_CREATED` ×6,
`HTTP_503_SERVICE_UNAVAILABLE` ×4, `HTTP_204_NO_CONTENT` ×4, `HTTP_401_UNAUTHORIZED` ×3,
`HTTP_400_BAD_REQUEST` ×2, `HTTP_502_BAD_GATEWAY` ×1, `HTTP_500_INTERNAL_SERVER_ERROR` ×1.

**The client's half of the same contract** — read the *shape* of the error body, not just the code:

- FastAPI writes **`.detail`**. The house extractor tolerates a non-JSON body,
  [features/student/jobs/jobs.component.ts:348](../../apps/web/src/app/features/student/jobs/jobs.component.ts#L348):
  ```ts
  const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
  this.formError.set(detail ?? 'Could not save the offer.');
  ```
  The inner `.catch(() => ({}))` matters: an error body is not guaranteed to be JSON — a 502 from
  a proxy is HTML — and an unguarded `res.json()` would throw *inside the error handler*,
  replacing a useful message with an unhandled rejection.
- For a 422, `detail` is an **array**, not a string. Guard the type as
  `features/register/registration.component.ts:109` does.
- **`features/student/offers/offers.component.ts:157` reads `.message`** — a field FastAPI never
  writes — so that screen always shows the generic fallback.

**And the taxonomy this sits inside.** `!res.ok` and `catch` are **different failures with
different messages** and are never merged: `'Could not load the jobs board.'` for the former,
`'Could not reach the server.'` for the latter. *A response arrived* and *no response arrived* are
different diagnoses, and collapsing them sends the user hunting for the wrong fault.
`student-overview.component.ts:456-466` (`getJson` returns `null` for both) and
`records.component.ts:148-188` (both set `'error'`) are the two places this is deliberately
collapsed, and the cost is that the dashboard cannot tell a 401 from a 500 from an offline
browser.

### 4.5 The comment style, and the past-failure narrative

Three comment shapes exist, and they are not interchangeable.

| Shape | Purpose | Example |
|---|---|---|
| **Module docstring / file header** | a *design brief*: the rule this module enforces and the failure it defends against. Often an indented endpoint block. | `app/document_store.py`, `app/mailer.py`, `app/health.py`; every Angular file header |
| **Constant justification** | the *reason for the number*, never a restatement of it | [knowledge.py:46-55](../../apps/api-py/app/knowledge.py#L46-L55) — ten lines calibrating `_MAX_VEC_DISTANCE = 0.32` (`:56`) |
| **Past-failure narrative** | what broke, how it presented, and why the code now reads the way it does | see below |

**The past-failure narrative is this codebase's most valuable habit.** It is what turns a
surprising line into a decision you can evaluate. A representative set, all quotable:

- **`docker-compose.yml:26-28`** — the healthcheck probes `reep_py`, "not `reep_dev`: `reep_dev`
  existing proves nothing about the database the application connects to."
- **[app/config.py:151-183](../../apps/api-py/app/config.py#L151-L183)** — a blank
  `LLM_TIMEOUT_MS=` raised at *import*, before uvicorn bound a socket, so the whole dashboard died
  at boot on a blank line in a file four processes share.
- **[app/config.py:294-324](../../apps/api-py/app/config.py#L294-L324)** — `sqlalchemy_url` used
  to `split("?", 1)[0]` and silently discard `sslmode`, so every managed Postgres fell back to
  libpq's `prefer`: TLS opportunistic, certificate never verified, nothing logged.
- **[app/memory.py](../../apps/api-py/app/memory.py)** — kept as a tombstone that raises
  `NotImplementedError` rather than being deleted, with a docstring listing the five policies the
  old path bypassed.
- **[app/routers/interview.py:194-201](../../apps/api-py/app/routers/interview.py#L194-L201)** —
  accept-then-refuse, "a close sent BEFORE accept fails the HTTP upgrade, and the browser
  WebSocket API surfaces neither code nor reason for that."
- **[features/assistant/assistant.component.ts:66-72](../../apps/web/src/app/features/assistant/assistant.component.ts#L66-L72)** —
  the shared-lab-PC consent incident (§3.10).
- **`apps/web/src/app/app.routes.ts:13-18`** — every route was once statically imported, producing
  one 1.23 MB `main` chunk a student on a phone downloaded before the login form could paint.
- **`apps/api-py/requirements-voice.txt:14-17`** — the file once declared only
  `livekit-agents[google]` while `voice_agent.py` imported `groq`, `silero`, `noise_cancellation`
  and `edge_tts`, so a clean checkout installed a worker that raised `ImportError` at startup.
  (`:18-21` is the separate `==`-versus-`~=` rationale.)

**The rule, stated as a rule:** *when a rule is unenforceable, write down the failure it
prevents.* And its consequence: **deleting one of these comments is a real change**, because in a
repo with no linter, no type checker and no formatter they are the enforcement mechanism. If you
change the behaviour a narrative describes, rewrite the narrative in the same commit — a comment
that describes a failure the code no longer has is worse than none, because the next reader will
preserve a constraint that has stopped existing.

---

## 5. THE RULES THAT MUST NOT BE BROKEN

Two of these are named in `AGENTS.md` as inviolable. The rest are standing rules the book
surfaced chapter by chapter.

**The form, stated honestly.** Every rule below carries **the code that enforces it** and an
**Enforced?** verdict. §5.1 and §5.2 — the two inviolable rules — additionally carry **the failure
they prevent** as a paragraph, and §5.1 alone carries a **How to check** column, because it is the
only section where every rule has a one-line grep that settles it. §5.3–§5.7 do not: their checks
are "read the handler", and printing that in a column eleven times would be theatre. Where a
genuine check exists it is named in the *Enforced by* cell.

**The Enforced? verdict** is honest: ✅ means code refuses the mistake; ⚠️ means the rule is real
but kept by convention and review only.

### 5.1 Rule 1 — student data must not leave the machine unbidden

**The failure it prevents.** `LLM_BASE_URL` is a URL, not a promise — it may point at a free
model that trains on submissions. A resume brief carries a student's name, USN, marks and
attendance. Sending it to such a provider is not recoverable: you cannot un-send it, and nobody
would ever see an error.

| Rule | Enforced by | How to check | Enforced? |
|---|---|---|---|
| Any path sending a student's private records to a model must go through `complete_chat(..., carries_student_data=True)` or `stream_chat(...)`. | [app/ai/llm.py:130](../../apps/api-py/app/ai/llm.py#L130) and [:173](../../apps/api-py/app/ai/llm.py#L173) — `if carries_student_data and not student_data_egress_allowed(cfg.base_url): raise StudentDataEgressRefused`. **Code, not review.** | `grep -rn 'complete_chat\|stream_chat' apps/api-py/app/` and check each call site | ✅ |
| The gate itself: **loopback is always allowed; anything else requires the flag.** | `student_data_egress_allowed()` ([llm.py:105-109](../../apps/api-py/app/ai/llm.py#L105-L109)) → `is_loopback()` ([:101](../../apps/api-py/app/ai/llm.py#L101)) against `_LOOPBACK_HOSTS = {"127.0.0.1","localhost","::1","0.0.0.0"}` ([:31](../../apps/api-py/app/ai/llm.py#L31)), else `settings.allow_remote_student_data` ([config.py:287-288](../../apps/api-py/app/config.py#L287-L288)) | `tests/test_egress_gate.py` | ✅ |
| **Pre-check; do not rely on the raise.** Both `True` call sites do `cfg = llm_config()` then `if cfg is not None and student_data_egress_allowed(cfg.base_url)` *before building the prompt*. | [student.py:958-959](../../apps/api-py/app/routers/student.py#L958-L959), [orchestrator.py:570-571](../../apps/api-py/app/ai/orchestrator.py#L570-L571) (`:569` is the `if polish_ctx:` that wraps it) | `StudentDataEgressRefused` is caught nowhere by name; without the pre-check a *policy* decision would surface as a generic note | ✅ |
| **A refusal must degrade to a deterministic answer, never to an error.** Compose the deterministic result first; initialise the result variables to the refused outcome; overwrite only inside the guarded `try`. The client learns the truth from a field, not a 500. | [student.py:955-981](../../apps/api-py/app/routers/student.py#L955-L981) — `generated_by, model, used_ai, note = "fallback", None, False, None` on line 956, *before* the gate; the refusal branch writes an explanatory `note` naming `LLM_ALLOW_REMOTE_STUDENT_DATA` | `test_auth_rbac.py:73 test_resume_generate_respects_egress_gate` | ✅ |
| Public data — a job posting, KB policy text — does **not** need the gate. | [orchestrator.py:478](../../apps/api-py/app/ai/orchestrator.py#L478) carries the one-line justification `# PUBLIC content only — carries_student_data=False` | — | ✅ |
| **Exactly two `carries_student_data=True` call sites exist.** Any third is a review event. | [orchestrator.py:578](../../apps/api-py/app/ai/orchestrator.py#L578) (optional polish) and [student.py:970](../../apps/api-py/app/routers/student.py#L970) (resume generation) | `grep -rn 'carries_student_data=True' apps/api-py/app/` — expect exactly 2 hits in executable code | ✅ |
| **Redaction is NOT a backstop for Rule 1.** `redact_pii` sits on **no egress path**. | `redact_pii` has exactly **one reachable** caller: [agent.py:528](../../apps/api-py/app/routers/agent.py#L528) `note = redact_pii(body.note)` — the feedback free-text field, after the ownership check. The only other call site is [retention.py:77](../../apps/api-py/app/retention.py#L77), inside `purge_expired`, **which nothing in the repository invokes** (§5.4) | `grep -rn 'redact_pii' apps/api-py/app/` — two call sites, one of them dead. Either way, the gate in `app/ai/llm.py` is the only thing between student records and a provider | ✅ |
| **The voice worker holds no database session and its `instructions` are a verbatim constant.** Adding grounding to voice means adding a path to Groq that never consults the gate. | `apps/api-py/voice_agent.py` — `instructions = BASE_INSTRUCTIONS`; the worker calls exactly two endpoints | `tests/test_voice_worker_source.py` asserts this **against the source text**, not by import | ✅ |
| **The interview relay is under the same obligation.** | [app/interview_relay.py:21](../../apps/api-py/app/interview_relay.py#L21) states it in the module docstring: anything personalising the prompt goes through `complete_chat(..., carries_student_data=True)` or is left out. [config.py:96](../../apps/api-py/app/config.py#L96) repeats it beside `openai_api_key` | `grep -n 'import' apps/api-py/app/interview_relay.py` — no ORM model, no `assistant_tools`, no `knowledge` | ✅ |
| **The "always pass it explicitly, even when False" rule is not universal.** | [orchestrator.py:480](../../apps/api-py/app/ai/orchestrator.py#L480) and `:528` pass `carries_student_data=False` explicitly. But [agent.py:231](../../apps/api-py/app/routers/agent.py#L231) `complete_chat(messages, max_tokens=1024)` and [agent.py:292](../../apps/api-py/app/routers/agent.py#L292) `stream_chat(messages, max_tokens=1024)` **omit the keyword entirely**, relying on the signature default; the module docstring at `agent.py:75-77` states the reasoning in prose instead | Chapter 8 scoped its claim to `app/ai/` — inside that scope it holds; at the router boundary it does not | ⚠️ |
| **DRIFT.** `AGENTS.md` documents the unlock as the exact string `true`; the code accepts any case after stripping ([config.py:288](../../apps/api-py/app/config.py#L288)). | — | Documentation gap, not a policy gap — still defaults closed, still needs an affirmative word | ⚠️ |

**Checkable in one line:** any new `complete_chat` / `stream_chat` reachable from a path that
loads a `Student`, `StudentProfile`, `SemesterResult`, `SubjectMark`, `AttendanceRecord`,
`AcademicQualification` or `Upload` **must** carry a `student_data_egress_allowed` pre-check in
the same function.

### 5.2 Rule 2 — staff scope is decided by role, not by a missing field

**The failure it prevents.** A MENTOR with no `Mentor` group has an empty scope key. Read that as
"no filter needed" and the query returns **the whole programme** — every student's records, to a
member of staff who was granted access to none. It is one `if` away at all times.

| Rule | Enforced by | Enforced? |
|---|---|---|
| `require_mentor` admits MENTOR/DIRECTOR/ADMIN; `require_director` admits DIRECTOR/ADMIN. | `_STAFF = {"MENTOR","DIRECTOR","ADMIN"}` ([mentor.py:28](../../apps/api-py/app/routers/mentor.py#L28)) + `require_mentor` ([:31](../../apps/api-py/app/routers/mentor.py#L31)); `_DIRECTORS = {"DIRECTOR","ADMIN"}` (`:230`) + `require_director` (`:233`). Role sets are module-private SCREAMING_SNAKE plurals declared **immediately above** the guard that reads them | ✅ |
| **A MENTOR with no `Mentor` group sees NOBODY — never the whole programme.** The highest-consequence line in the backend. | `_assert_can_access_student` ([mentor.py:72-84](../../apps/api-py/app/routers/mentor.py#L72-L84)): `if not mentor_id or student is None or student.mentor_id != mentor_id: raise 404`. For lists, the idiom at [mentor.py:50-56](../../apps/api-py/app/routers/mentor.py#L50-L56) — **the `return []` comes *before* the `.where()`**. **Never write `if mentor_id: query = query.where(...)`.** | ✅ |
| **Cross-scope denial is 404, never 403.** A 403 would confirm a student id exists outside the caller's group and let a mentor enumerate the roster. | [mentor.py:79-84](../../apps/api-py/app/routers/mentor.py#L79-L84); `agent.py`'s `/feedback` applies the same rule to run ids; [conversations.py:76](../../apps/api-py/app/conversations.py#L76) `assert_owner` encodes it for conversation ids | ✅ |
| Every staff endpoint touching **one named student** calls `_assert_can_access_student(session, student_id, db)` — **including when the id is derived from a fetched row** (`alert.student_id`, `ls.student_id`, `up.student_id`, `sc.student_id`). Those are the ones people forget, because the handler "looks like" it is about an alert. | convention and review only. **No router-level `dependencies=[...]` exists anywhere** (verified: zero hits), and there is no test enumerating routes | ⚠️ |
| **Derive the student id from the fetched row, never from the client.** Accepting both an object id and a student id lets a caller pair their own mentee's id with another group's row. | — | ⚠️ |
| **On row-addressed endpoints call `require_mentor(session)` explicitly before the `db.get(...)`**, even though `_assert_can_access_student` calls it too — the redundancy stops a non-staff caller using the object-not-found 404 as an existence oracle. | — | ⚠️ |
| **Order: role → fetch → scope → workflow state → mutate.** Checking state before scope leaks workflow information about another group's records. | — | ⚠️ |
| **`director.py`'s unfiltered query idiom must never be copied into a `require_mentor`-guarded handler.** It is safe there *because* only DIRECTOR/ADMIN reach it, and catastrophic one router over. | — | ⚠️ |
| **Never re-derive the scope key from a body, header or query parameter.** `mentorId` and `studentId` are signed claims. | — | ⚠️ |

**Residual shape, recorded honestly.** Four handlers look the entity up *between* the role check
and the scope check, so a MENTOR guessing an id gets "Alert not found." when it is fake and
"Student not in your mentor group." when it is real. **Both are 404**, so the status leaks
nothing, but the `detail` is an existence oracle. Ids are 32-hex `uuid4`, so it is not practically
enumerable. Avoid the shape by putting `_assert_can_access_student` first, as `list_notes`,
`add_note` and `student_focus` do.

> **DRIFT worth knowing.** `AGENTS.md` says a DIRECTOR "reads every student's marks, attendance
> and USN". USN is exposed by `GET /mentor/mentees`; **marks and attendance are not** —
> `routers/mentor.py` and `routers/director.py` import none of `SemesterResult`, `SubjectMark`,
> `AttendanceRecord`, `AcademicQualification` or `Enrollment`, and no endpoint returns another
> student's marks. The only academic row crossing the student/staff boundary is `LabSession`, via
> `GET /mentor/students/{id}/focus`.

### 5.3 Auth and session

| Rule | Enforced by | Enforced? |
|---|---|---|
| **`Depends(get_current_session)` in the signature is the ONLY structural authentication in the app.** No middleware — verified: `CORSMiddleware` at [main.py:91](../../apps/api-py/app/main.py#L91) is the only `add_middleware` call — and no router-level `dependencies=[...]`. **Omitting it makes the route fully public and nothing fails.** | [app/identity.py:8](../../apps/api-py/app/identity.py#L8) | ⚠️ structural |
| **Never split the login error message.** Unknown email and wrong password stay one 401 with one detail string. | `app/routers/auth.py`; `tests/test_auth_rbac.py` | ⚠️ |
| **Never widen `algorithms=[...]` in `verify_session_token`.** A widened list lets a token signed with a weaker algorithm — or `none` — verify. | `verify_session_token` ([security.py:51](../../apps/api-py/app/security.py#L51)); the list itself is `algorithms=["HS256"]` at [security.py:53](../../apps/api-py/app/security.py#L53) | ⚠️ |
| **Never compare a credential with `==` where `hmac.compare_digest` belongs.** | `verify_password` ([security.py:42](../../apps/api-py/app/security.py#L42)) — `return hmac.compare_digest(derived, digest)`. **That is the only `compare_digest` in the backend.** The standing exception is [`require_voice_worker`, voice.py:89](../../apps/api-py/app/routers/voice.py#L89): `if x_voice_worker_secret != settings.voice_worker_secret:` — a plain, timing-variable comparison of a shared secret. Recorded as a finding, **not as evidence for the rule**; the exposure is small (the attacker must already reach the API and the secret is not user-derived) but there is no reason for it | ⚠️ |
| **Never change `_SCRYPT` or the `salt.encode()` call** without a migration plan. `_SCRYPT = dict(n=16384, r=8, p=1, dklen=64, maxmem=64*1024*1024)` ([security.py:25](../../apps/api-py/app/security.py#L25)) is Node-`scryptSync`-compatible, and there is **no per-hash parameter record** — so a change invalidates every password in the database **and the suite stays green**. | [security.py:25-43](../../apps/api-py/app/security.py#L25-L43) | ⚠️ |
| **Never put anything in the JWT you would not put on a postcard.** Claims are base64url text readable by anyone holding the cookie; the signature protects integrity, not confidentiality. | [security.py:45](../../apps/api-py/app/security.py#L45) | ⚠️ |
| Session claim keys are **camelCase** (`userId`, `studentId`, `mentorId`); every other request/response field is snake_case. | [auth.py:29-40](../../apps/api-py/app/routers/auth.py#L29-L40), [schemas/auth.py:12-18](../../apps/api-py/app/schemas/auth.py#L12-L18) | ✅ by contract |
| Do **not** add `extra='forbid'` to `SessionUser` (it 500s every `/auth/me` — the session dict still carries `iat`/`exp`) or to `LoginRequest` (it 422s every login arriving through the guard's redirect, which carries an extra `next` key; a *direct* login still passes, which is what makes it easy to miss by hand-test). | `app/schemas/auth.py` | ⚠️ |
| No code path may read the session cookie in JavaScript, or introduce a JS-readable token as a second source of truth. | the httpOnly `reep_session` cookie | ⚠️ |
| `async def login` is forbidden — see §2.3.14; the handler is synchronous because scrypt is CPU-bound and would block the event loop. | `app/routers/auth.py` | ⚠️ |

### 5.4 Conversation state and governance

| Rule | Enforced by | Enforced? |
|---|---|---|
| **Never trust a client-supplied thread id or client-supplied history.** No endpoint may accept a conversation id to decide whose data is read or written. | Structural: `ChatIn`/`AskIn` declare **only `message`** ([agent.py:123-124](../../apps/api-py/app/routers/agent.py#L123-L124), `:138-139`); `GET /history` and `DELETE /conversation` declare **zero** parameters; every write path calls `convo.get_or_create(db, session["userId"], Role(session["role"]))` — verified at `agent.py:221/272/350`, `voice.py:270/367`, [interview.py:326](../../apps/api-py/app/routers/interview.py#L326). If you must take an id in a URL, route it through `assert_owner` ([conversations.py:76](../../apps/api-py/app/conversations.py#L76)) — which is what it is pre-positioned for, and which **still has no production caller**, only tests | ✅ |
| **One live conversation per owner is enforced by the DATABASE**, not by application logic. `get_or_create`'s read-then-insert is *expected* to lose races; recovery is rollback-then-re-read, and the rollback is mandatory or the next statement raises `PendingRollbackError`. | the partial unique index `uq_conversation_one_active_per_owner` ([models/conversation.py:56-61](../../apps/api-py/app/models/conversation.py#L56-L61)). The predicate uses `postgresql_where`, so on another dialect the index is created **with the predicate stripped** — stricter, and it breaks "clear and start again" | ✅ |
| **Turns are appended through exactly two doors.** Inside a request: `conversations.append_message(db, ...)` ([:92](../../apps/api-py/app/conversations.py#L92)). Out of process: `POST /api/voice/transcript` — policy lives on the server. | [app/memory.py](../../apps/api-py/app/memory.py) **raises `NotImplementedError` rather than being deleted**, precisely so this cannot be forgotten; its docstring lists the five policies the old path bypassed | ✅ |
| `append_message` is **intentionally dumb**. Greeting, the 4000-char cap, final-only, dedup, worker auth and soft-delete refusal all live in the router above it. | [conversations.py:92](../../apps/api-py/app/conversations.py#L92); [voice.py:383-412](../../apps/api-py/app/routers/voice.py#L383-L412) | ✅ |
| **The compulsory greeting is stamped only after the greeted reply is durably persisted**, and read from an explicit `greeted_at` stamp — **never by counting assistant rows**, because voice writes land through a best-effort cross-process POST whose failures are swallowed. `open_with_greeting` must never stack. | [conversations.py:148](../../apps/api-py/app/conversations.py#L148) `GREETING`, `:151` `awaiting_first_reply`, `:172` `mark_greeted`, `:182` `open_with_greeting` (the prefix is applied at `:186-188`) | ✅ |
| **The redaction pass order `EMAIL → USN → PHONE` is load-bearing and must not be reordered** — emails match first so their local part is not chewed up by the phone and USN passes. | the three substitutions at [redaction.py:46-48](../../apps/api-py/app/redaction.py#L46-L48) (`_EMAIL.sub` `:46`, `_USN.sub` `:47`, `_PHONE.sub` `:48`); the regexes at [`:21-31`](../../apps/api-py/app/redaction.py#L21-L31) each carry their reason, and `_EMAIL`'s says so in as many words | ⚠️ |
| **What retention guarantees today: nothing.** `purge_expired` and `redact_expired_runs` are correct, idempotent and clock-pinnable — and **nothing in this repository calls them.** Verified: the only callers of either are `tests/test_retention.py`. `retention_until` is written and never read; "Clear conversation" only hides; `agent_runs` accumulates every question every student has ever asked, verbatim and indefinitely. | [retention.py:41](../../apps/api-py/app/retention.py#L41), [:107](../../apps/api-py/app/retention.py#L107) | ⚠️ **absent** |

> **The standing rule:** *a governance function that nothing schedules is a promise the product
> is not keeping.* Add the thing that runs it in the same change, or record the absence where an
> operator will read it. **Anyone extending this is writing the scheduler, not configuring one** —
> there is no `__main__` block, so even `python -m app.retention` does nothing.

### 5.5 Voice and interview

| Rule | Enforced by | Enforced? |
|---|---|---|
| **A transcript write must never break a call.** `_post_sync` must never raise; every POST stays fire-and-forget from the event handler's view; every task is held in a strong reference until it completes. Because the write is silent by design, **the ERROR log line carrying the status code is the only compensating evidence — do not downgrade it, do not drop the status code.** | `apps/api-py/voice_agent.py`; the runbook grep is `grep -- '-> HTTP' worker.log` | ⚠️ |
| **Keep the client and server state machines in agreement.** The agent name is compile-time on both sides and **must never become an env var** — `VOICE_AGENT_NAME = "reep-voice"` ([voice.py:58](../../apps/api-py/app/routers/voice.py#L58)); verified, no `voice_agent_name` field exists in `Settings`. Conversation ids must stay **dash-free** so the room-name split works. `HEARTBEAT_INTERVAL_SECONDS` plus a worst-case POST must stay strictly inside `HEARTBEAT_FRESH_SECONDS = 30` ([voice.py:43](../../apps/api-py/app/routers/voice.py#L43)). A 404 from `/transcript` must end the call. The drain wait must stay strictly under `shutdown_process_timeout`, and the in-flight drain must be a shutdown callback, never a `finally:` after `session.start()`. | `voice.py`, `voice_agent.py`, `core/chat-voice.service.ts`; `tests/test_voice_worker_source.py:99` pins the agent name | partly ✅ |
| **Do not "simplify" the endpointing or interruption settings.** `min_delay: 1.5`, `max_delay: 6.0`, `mode: "vad"`, the omission of `min_words`, and `discard_audio_if_uninterruptible: False` each encode a specific observed failure and **none is protected by a test.** | `apps/api-py/voice_agent.py` | ⚠️ |
| **Re-run the SDK introspection on any `livekit-agents` bump.** The pin is `==` not `~=` because `~=1.6` silently admits the whole 1.x series — that is how the earlier `~=1.5` let 1.6.10 in unnoticed. | `apps/api-py/requirements-voice.txt` VERIFIED block | ⚠️ |
| **Accept the WebSocket first, then refuse.** A close sent *before* accept fails the HTTP upgrade and reaches the browser as a bare 1006 with neither code nor reason — "not signed in" becomes indistinguishable from "the wifi dropped". | [interview.py:194-201](../../apps/api-py/app/routers/interview.py#L194-L201) (docstring), then [`:203` `await websocket.accept()`](../../apps/api-py/app/routers/interview.py#L203) — `:202` is `conn_id = uuid.uuid4().hex[:12]`, the correlation id §2.13 describes — and then origin (`:211-223`) → session (`:225-234`) → role → config → capacity, each closing on an already-accepted socket. [identity.py:16-46](../../apps/api-py/app/identity.py#L16-L46) states the two mechanical reasons a WS session dependency cannot reuse the HTTP one | ✅ |
| **Role scoping is the router's job, not the UI's.** Hiding the Start button in an Angular component is not a gate: a MENTOR or DIRECTOR with a valid cookie can open the socket from devtools in one line, and each open costs a billed upstream Realtime session. | [interview.py:236-252](../../apps/api-py/app/routers/interview.py#L236-L252) | ✅ |
| **Concurrency caps are PER-WORKER and module-level, never `app.state`** — the cap is a property of the process, and a second FastAPI app in one process (tests) must not silently double it. | `_LIMITER = _ConnectionLimiter(settings.interview_max_sessions)` ([interview.py:85](../../apps/api-py/app/routers/interview.py#L85)), with `try_acquire()` (`:267`) / `release()` and the comment "From here on the slot is HELD, so every exit path must release it" ([:280](../../apps/api-py/app/routers/interview.py#L280)) | ✅ |

### 5.6 Schema and migrations

| Rule | Enforced by | Enforced? |
|---|---|---|
| **Alembic owns the schema.** No application code creates tables. Verified: **`create_all` appears nowhere in `app/` or `tests/` except two comments** ([db.py:4](../../apps/api-py/app/db.py#L4), [models/__init__.py:1](../../apps/api-py/app/models/__init__.py#L1)) — and `db.py`'s docstring claim that "`create_all` is used only by the dev seed" is **STALE and hazardous**: a `create_all`-built database has no `alembic_version` row, so the next `upgrade head` starts from the base revision and dies on the first `CREATE TABLE`. | `app/seed.py:18` — "Data only — Alembic owns the schema" | ✅ |
| **Migrate before you seed.** `alembic upgrade head`, then `python -m app.seed` (dev) or `python -m app.seed_kb` (production-safe). Nothing automates it, but nothing needs to — seeding an unmigrated DB dies loudly on a missing relation. | `apps/api-py/README.md:46-47`, `app/seed.py:1-4` | ⚠️ |
| **`python -m app.seed` refuses to run when `ENV=prod`. There is no override flag.** It creates a DIRECTOR behind a password published in `AGENTS.md`. | [app/seed.py:57-70](../../apps/api-py/app/seed.py#L57-L70) — `if settings.is_prod: … raise SystemExit(1)`, with the error naming `app.seed_kb` as the production alternative | ✅ |
| **Migrations run exactly once per deploy**, as their own job or init container — never from the API entrypoint, because every replica would race on the `alembic_version` row. | stated in three places that must stay in agreement: `docs/deployment-env.md:96-105`, `apps/api-py/Dockerfile:51-53`, `docker-compose.prod.yml`'s `migrate` service | ✅ |
| **Keep the revision graph linear.** One base, one head, no branch labels, no `depends_on`. Ten `create_type=False` column references across eight revisions depend on an earlier revision having run, and only the linear chain guarantees that ordering. `script.py.mako` types `down_revision` as `Union[str, None]` rather than upstream's `Union[str, Sequence[str], None]` to back this up. | `alembic heads` prints exactly one row | ⚠️ |
| **Never write the database URL into `alembic.ini`.** It comes from `settings.sqlalchemy_url`, which forces the psycopg-3 driver **and preserves `sslmode`**. | [config.py:295](../../apps/api-py/app/config.py#L295), injected in `migrations/env.py` | ✅ |
| **`pgvector` stays a runtime dependency** — a revision imports it at module scope, so Alembic cannot build its script directory without it. And **wherever `alembic upgrade head` runs, the Postgres image must carry pgvector.** True of `docker-compose.yml` and `docker-compose.prod.yml`; **false of `.github/workflows/ci.yml`** — a live one-line defect (§11). | — | ⚠️ **broken in CI** |
| **Ship the model, the migration, the tests and any requirements pin in one commit** — and check `.github/workflows/ci.yml` too if the revision needs an image, extension or privilege. The pgvector work (`87d3981`) did all four and still broke CI for exactly that reason. | — | ⚠️ |

### 5.7 Retrieval and the honest fallback

| Rule | Enforced by | Enforced? |
|---|---|---|
| **A retrieval miss must produce an honest refusal, never a low-confidence answer.** `search()` returns `[]` and the caller says "no approved answer" — it never returns the least-bad chunk with a caveat, and nothing downstream applies a score threshold, so **emptiness is the *only* signal the honest fallback has.** | [app/knowledge.py:74](../../apps/api-py/app/knowledge.py#L74) `search`, `orchestrator.py:456` `_policy`, `NO_POLICY_ANSWER` (`orchestrator.py:97`) | ✅ |
| **Never add a retrieval branch without splatting `base_where` into it** — approval and audience are enforced in exactly one place and nowhere else. | `app/knowledge.py` | ⚠️ |
| **Anything that makes the candidate pool easier to populate makes the honest fallback harder to reach.** The ILIKE fallback is the live demonstration: written for "a single rare token", in practice it admits any question containing a two-letter substring of the corpus and launders arbitrarily distant chunks past a distance floor that correctly rejected every one of them. | [knowledge.py:139](../../apps/api-py/app/knowledge.py#L139) — `fallback`, the one branch statement that breaks the `_stmt` suffix rule; **do not copy it** | ⚠️ |
| **The floor gates one door only.** `_cosine` in the blend applies no floor, so any candidate that entered by another route is scored on raw similarity however far away it is. **If you widen retrieval, widen the refusal test in the same commit.** | `_MAX_VEC_DISTANCE = 0.32` ([knowledge.py:56](../../apps/api-py/app/knowledge.py#L56)), `_cosine` ([:63](../../apps/api-py/app/knowledge.py#L63)) | ⚠️ |
| **Re-measure `_MAX_VEC_DISTANCE` whenever the provider, model or corpus changes.** It is provider-specific, there is no calibration script anywhere in the repo, and no test will tell you it has drifted. | — | ⚠️ |
| **No embedder configured ⇒ full-text only, and that is a supported configuration, not a broken one.** The KB always works. | `embedder_configured()` ([embeddings.py:62](../../apps/api-py/app/ai/embeddings.py#L62)); auto-select is Mistral only | ✅ |
| **The KB is APPROVED public policy text, so embedding it is outside the Rule 1 egress gate.** | — | ✅ |
| Editing a KB document by re-running the seed does not work — the title guard skips the row. **Delete the `KnowledgeDocument` first (the cascade removes its chunks), then re-run.** Renaming is the same operation and worse if you get it wrong: re-running after a rename inserts a *second* document while the original stays APPROVED, embedded and retrievable, because nothing constrains `title` to be unique. | `app/seed_kb.py` | ⚠️ |

### 5.8 The frontend standing rules

**They are lettered `F1`–`F5`, not numbered.** §5.1 and §5.2 are "Rule 1" and "Rule 2" — the two
`AGENTS.md` names the whole product depends on — and a review comment reading "this breaks rule 1"
must never be ambiguous between an egress-gate violation and an eager route. `F` is for frontend.
(Chapter 12 §9 uses `R5` for something else again; that is Chapter 12's handle, not this one's.)

**F1 — routes stay lazy, or the production build fails the bundle budget.**

Every screen is registered in
[apps/web/src/app/app.routes.ts](../../apps/web/src/app/app.routes.ts) with
`loadComponent: () => import('./…').then((m) => m.XComponent)` — **never** a static `component:`
reference. Verified today: **19 `loadComponent:` occurrences** — 18 explicit route entries plus
the one inside the shared `placeholder()` helper at `:28` — **and exactly one `component:`**, at
[app.routes.ts:46](../../apps/web/src/app/app.routes.ts#L46), `component: AppShellComponent` on
the `''` parent with `canActivate: [authGuard]`. That one is **correct, not a violation**: the
shell frames every authenticated route and the guard must run first, so lazy-loading either only
adds a round trip to the critical path. Those are the two sanctioned eager imports (`:3-4`).

The gate, from [apps/web/angular.json:38-49](../../apps/web/angular.json#L38-L49):

```json
"budgets": [
  { "type": "initial",            "maximumWarning": "250kB", "maximumError": "400kB" },
  { "type": "anyComponentStyle",  "maximumWarning": "16kb",  "maximumError": "32kb"  }
]
```

(The casing is inconsistent as written — `kB` then `kb`. Angular parses both.) The budget is a
gate **only** because [angular.json:58](../../apps/web/angular.json#L58) sets
`"defaultConfiguration": "production"` on the build target, so a bare `ng build` — what CI runs —
*is* the production build. Flip that default to `development` and the budgets, which live only
under `configurations.production`, never evaluate.

**The mechanism, stated correctly** (it is easy to get backwards): it is the **static `import`
statement at the top of the file** that defeats code-splitting, not the `component:` property.
`app.routes.ts` is reachable from `main.ts` via `app.config.ts`, so anything it names in a
top-level import is in the initial graph whether or not the route is visited.

**Two tricks worth preserving.** `placeholder()` gives all fifteen placeholder routes **one
identical import specifier**, so the bundler emits a single 804-byte chunk for all of them. And
`student/assistant`, `mentor/assistant` and `director/assistant` write
`import('./features/assistant/assistant.component')` **character for character**
([app.routes.ts:120-122](../../apps/web/src/app/app.routes.ts#L120-L122) explains why), so all
three share one chunk — the largest artefact in the app at 554.35 kB raw / 120.46 kB transfer,
dominated by `livekit-client`.

**Honest caveat on the number.** The builder's "Initial total" of 141.80 kB excludes two chunks
that `main` **statically imports** and the browser must therefore fetch before a line of `main`
runs — the Angular runtime (156.59 kB) and `@angular/common` (20.25 kB). True first-frame
payload: **318.63 kB raw / 87.71 kB transfer**, 2.50× the reported wire number. That does not
weaken the rule; it strengthens it, because the fixed framework floor means the real headroom
under the 250 kB warning is smaller than the reported figure suggests.

**And the converse is not enforced.** The route file's comment enforces one direction — "every nav
destination needs a route" — and nothing enforces the other. `app-shell.component.html:16-57`
lists **twelve** static student destinations with no `@if`/`@for`; the table registers **fourteen**
student paths. The orphans `student/academics` and `student/offers` have no nav link, and **both
are broken against the current API** (§3.6). That is not a coincidence: an unlinked screen is a
screen nobody notices rotting. The entire `mentor/*` and `director/*` placeholder surface is
unlinked too — a mentor signing in gets the student nav.

**F2 — status is text plus colour, never colour alone.**

A status element must contain a human-readable **label**. A meter must carry a number *and*
`role="progressbar"` with `aria-valuenow`/`aria-valuemin`/`aria-valuemax`/`aria-label`. A tab or
step must carry `role="tab"` plus `aria-selected`, or `aria-current`. **A hover-only `title` is
not a substitute for visible text.** Stated in the code at
[features/student/overview/student-overview.component.ts:153-155](../../apps/web/src/app/features/student/overview/student-overview.component.ts#L153-L155):

```ts
/** Status-label → chip tone + icon. TEXT is always the label itself, so colour
 *  is never the only signal. Unknown labels fall back to a neutral chip. */
const STATUS_CHIPS: Record<string, StatusChip> = {
```

**Enforced structurally by `.chip`**: each `.chip.{tone}` rule (`styles/reep-v2.scss:193-203`)
sets **both** `background` and `color` from one token, so a tone can never be a background-only
wash. Nothing else is automated.

**Every lookup must be total.** Verified pattern, five sites:
`certifications.component.ts:74` — `CHIP[r.status] ?? { cls: 'warn' as const, icon: 'help', label: r.status }`;
`jobs.component.ts:236`; `resume/sections/attachments.component.ts:130`;
`resume/views/all-resumes.component.ts:100`; and `student-overview.component.ts:212` —
`STATUS_CHIPS[status] ?? { cls: 'neutral', icon: 'info' }`, **a legitimate variant** whose
fallback carries no `label` because the template prints the raw server string alongside it
(`student-overview.component.html:34-35`). The chip object supplies tone and glyph; the text comes
from the server value directly. The rule holds; only the plumbing differs. An unmapped server enum
therefore degrades to **grey-with-the-right-words**, never a blank chip.

**Copy these two:** `records.component.html:135-148` and `jobs.component.html:9-13`.
**Corollary — do not use a tone where there is no status.** A skill name in a `chip good` teaches
the reader that green means nothing.

**Where the rule is broken today** (known debt; do not extend it): the resume-builder step dots,
the login-streak cells, the time-sheet stacked bar, the sidebar's current-page marker, and the
assistant's live-audio dot. Give a colour-only affordance at minimum an `[attr.title]`, as the
stepper dots do — and know that is the weak form.

**F3 — reuse a global class; never redefine one.**

Reuse the global. If you must diverge, add a **modifier co-class on the global base**
(`.dt-btn.sm`, `.card.err`, `.chip.unlocks`) or screen-specific layout — that is the intended
pattern and the dominant practice. A deliberate override carries a comment saying why
(`uploads.component.scss:1-4` is the model statement).

**Why the failure is silent rather than loud.** Angular's emulated encapsulation rewrites your
`.badge` to `.badge[_ngcontent-…]` — specificity `(0,2,0)` against the global's `(0,1,0)`. **The
local copy always wins, and it wins silently.** Worse, a *partial* redefinition wins only the
properties it declares and lets the rest of the global rule cascade in invisibly:
`uploads.component.scss:11` redefines `.stepper` without `width`/`background`/`border-right`, so
those three cascade in from `styles/reep-v2-resume.scss:64` — a stylesheet named for the Resume
Builder — onto the Uploads screen.

Enforced by convention plus header comments in six component sheets. **No linter, no test, no
CSS-module boundary.** Already broken seven times. **The prevention is §3.8:** give the class a
BEM block prefix and the collision cannot arise.

**F4 — never recompute a rule the API already decided.**

Display `eligible`, `reasons`, `band`, `score`, `total_mo`, `percent`, `value_label`. Do not
re-derive them. The server's copy is configurable and yours is a constant, and the two will
diverge **without a single test failing**.

The reference implementation is `jobs.component.html:105-152`:
`[class.ineligible]="!row.eligible"`, `@if (row.eligible)`, `@for (r of row.reasons; track r)`,
`{{ row.match_percent }}% skill match`, and a disabled button carrying
`[attr.title]="row.reasons.join('; ')"`. Every decision is the server's; the screen only renders
it.

**What is allowed, explicitly:** mapping a server-decided *string* to a tone (`bandChip`,
`matchTone`); counting server-decided *booleans* (`eligibleCount`, `appliedCount`, `offerCount` —
a count is not a recomputation); and pure presentation over server values with no server
counterpart (`stagePct = Math.round(((idx + 1) / STAGES.length) * 100)`; `matchTone`'s 70/40
thresholds at `jobs.component.ts:272-276` tint a bar whose *value and label* both come straight
from `row.match_percent`, so there is nothing for the server to disagree with).

**What is not** — verified violations, each shipping a number the eligibility engine disagrees
with:

- `records.component.ts:194-206` hard-codes **85/75** attendance thresholds against the server's
  configurable `crit.min_attendance_pct` (`student.py:2051`). Set it to 80 and Records calls 78%
  "Watch" (amber) while placement-readiness marks the factor unmet.
- `records.component.ts:127-134` defines "latest CGPA" as *first non-null scanning back*;
  `student.py:1782-1789` defines it as *highest semester row, null included*. The headline tile
  reads 8.1 while the eligibility gate treats the student as unassessed.
- `records.component.ts:136-138` and `academics.component.ts:67` each sum live backlogs
  client-side — a second and third implementation of an eligibility input.
- `academics.component.ts:64-66` recomputes `total_mo`, which the API already ships;
  `records.component.html:228` reads `gap.total_mo` correctly.
- `jobs.component.html:191` renders a hard-coded "Under review" chip. `JobApplication` has no
  status column — nothing will ever move it. **Do not render a status the API does not have.**

**And its companion (F4b): if a rule must genuinely be enforced, enforce it on the server too.**
The jobs-eligibility and deadline gates exist **only in the browser**. A client-side guard is a
usability affordance, never a security control.

**F5 — every fetch carries `credentials: 'include'`.**

Every request opts in by hand. There is no interceptor, no HTTP service layer, no repository —
and `environment.apiBase` being `'/api'` (a **relative** path,
`apps/web/src/environments/environment.ts:11`) is *not* sufficient: `fetch` and `HttpClient` both
omit cookies unless told otherwise.

**Verified in the working tree, and adherence is total: 43 `fetch(` call sites under
`apps/web/src`, 43 occurrences of `credentials: 'include'`** — 100%, hand-maintained. Plus
**12 `withCredentials: true` `HttpClient` call sites**:

| File | Calls | Lines |
|---|---|---|
| [core/auth.service.ts](../../apps/web/src/app/core/auth.service.ts) | 3 | 37, 50, 63 |
| [core/chat-voice.service.ts](../../apps/web/src/app/core/chat-voice.service.ts) | 8 | 221, 231, 327, 335, 355, 417, 460, 551 |
| [core/interview.service.ts](../../apps/web/src/app/core/interview.service.ts) | 1 | 1040 |

Chapter 12 §4 counts **eleven** — auth 3 and chat-voice 8, exactly the same lines, still exactly
what is there. **The whole delta from 11 to 12 is `interview.service.ts`, which did not exist
when Chapter 12 was written.** `auth.service.ts` gained nothing.

A raw `grep -rn withCredentials apps/web/src` returns **14**, because **two** of the hits are
comments, not calls: `app.config.ts:14` and `auth.service.ts:7`. The `app.config.ts` one is also
**STALE** — it still says "the NestJS backend".

**The trap.** Omit it once and the request arrives with no `reep_session` cookie,
`get_current_session` raises 401, and — because no data-loading component distinguishes 401 from
any other failure — the screen shows a generic "Could not load…". **The bug presents as a data
problem and is an auth problem.**

**The known gap, stated honestly.** Angular runs `canActivate` only on *newly activated* routes,
and `authGuard` sits on the parent `''` route. Navigating `/student` → `/student/jobs` →
`/student/resume` never re-runs it. There is no interceptor to catch a 401 globally. So after the
cookie expires: the shell keeps rendering with a stale `session()` showing the user's name and
role, every panel independently 401s into its own error state, and **there is no route back to
`/login`** — recovery requires a hard reload. A single `HttpInterceptorFn` mapping 401 →
`auth.logout()` plus `router.navigate(['/login'])` would close it; nothing in this repo does.

**The one deviation, and why it matters.** `core/chat-voice.service.ts` is the only application
file that hard-codes bare `'/api/...'` literals instead of `` `${environment.apiBase}/...` `` — in
all nine of its calls. Its spec mirrors the same literals
(`chat-voice.service.spec.ts:36`, `http.expectOne('/api/agent/history')`), so
`grep -rl "'/api/" src/app` returns **two** files. They are equivalent today because `apiBase` is
`'/api'`; change it and every agent and voice call breaks while the rest of the app keeps working.
**`core/interview.service.ts` deliberately does not repeat the mistake** — `:1203` builds the
WebSocket URL as ``new URL(`${environment.apiBase}/interview`, location.href)``, with the reason in
a comment at `:1200`: a path outside `/api` loses both the dev proxy and the cookie.

### 5.9 Adding things — the complete checklists

| Adding… | The checklist |
|---|---|
| **a provider** | Append a `Provider(name, base_url, default_model, key_attr)` row to `_PROVIDERS` ([llm.py:61](../../apps/api-py/app/ai/llm.py#L61)) in precedence order; add `<provider>_api_key: str = ""` to `Settings` spelled as the **exact lowercase** of the env var (`ai/adk.py:27` reconstructs the env var by uppercasing the field name, so they must stay identical); document it in `.env.example`. **A typo in `key_attr` makes `getattr` return `""` and the provider is skipped forever with no error.** `Provider.name` serves three roles at once — auto-select identity, `LLMConfig.provider`, and the LiteLLM prefix in `adk.py` — so renaming one silently fractures the `by_model` aggregation in `/api/agent/metrics` and drops the provider out of `_LITELLM_NATIVE`. |
| **a tool** | Put it in `app/assistant_tools.py` with signature `(db: Session, student_id: str)`, name it as a bare domain noun-phrase (**no `get_`/`fetch_` prefix** — `completion_gaps`, `skill_status`, `eligible_jobs`, `deadlines`, `profile_completion`, `placement_readiness`, `policy_search`), call the real `/student/...` endpoint function with `_session(student_id)` (`assistant_tools.py:47`) rather than reimplementing its logic, and project the Pydantic result to a plain JSON-ready `dict`/`list`. **Import nothing from `app.ai` and no network client** — the module's import list (`:37-44`) is the layer a reviewer can check in a diff. Then add a builder `_<intent>(db, student_id)` in the orchestrator, a `_student_source(...)` citation, an entry in `classify()`, and a golden case. |
| **an endpoint the assistant might reuse** | It may read only `session.get("studentId")` — **never `session["userId"]`, `name` or `role`** — or `assistant_tools._session()` breaks. |
| **a `cta_route`** | It is an **Angular** router path (`/student/skilling`), not an API path, and **nothing checks that it still exists**. Keep it in step with `apps/web/src/app/app.routes.ts` by hand. |
| **a decision endpoint** | Name the field `decision`, `.upper()` it, compare against a tuple, 422 from the `else` **before any assignment**. Canonical pairs: APPROVE/REJECT (offers, leave, registration), VERIFY/REJECT (uploads), GRANT/REJECT (skill claims). Give the schema a **qualified** name (`LeaveDecisionIn`) — `DecisionIn` is already taken twice. Two improvements worth making rather than copying the drift: **`.strip()` before `.upper()`** (nothing does today) and **name every accepted synonym in the 422** (no message does today, so the synonyms are undiscoverable). |
| **a reviewed entity** | Stamp the reviewer from `session["userId"]` and `datetime.now(timezone.utc)` **in the handler, never from the body** — no request schema on the staff surface carries a reviewer field, and none should. Use the majority trio `reviewed_by_id` / `reviewed_at` / `review_note`. If a response declares a non-optional `str` populated by a separate lookup, give it an `or ""` fallback — a `None` there is a **500 on an already-committed write**. |
| **a low-attention free-text column** | Route it through `redact_pii` before it is assigned to any ORM object. |
| **a derived number** | Guard every denominator; `round(x, 1)` for percentages; name the field for its unit. If a helper exists (`_attendance_pct`, `_latest_cgpa`, `_live_backlogs`, `_cert_completion_pct`, `_resume_pct`), **call it** rather than inlining a fourth copy. Thresholds a director owns come from the active `PlacementCriteria`; hard-coded fallbacks **must match the column defaults**. |

---

## 6. THE REVIEW CHECKLIST

One actionable list, organised by **what changed**. Read only the block that applies.

### 6.1 Reject on sight — any diff, any file

- A handler under `/api/mentor/*` or `/api/director/*` whose first body statement (after an
  optional docstring) is not a guard call.
- `if mentor_id:` wrapping a `where` clause.
- A **403** where the existing code returns **404** for out-of-scope access.
- A new `require_*` defined in a second place rather than imported from `routers/mentor.py`.
- A student-router query not filtered by the caller's own `student_id`, or a student id accepted
  from a path, query string or body.
- A `complete_chat`/`stream_chat` call reachable from a student-record path without a
  `student_data_egress_allowed` pre-check.
- A new single-column index declared inline as `index=True`.
- A new PG enum type name without an underscore (do not follow `feedbackrating`).
- A model module not added to `app/models/__init__.py`.
- `Base.metadata.create_all` in any code path.
- `extra='forbid'` on `SessionUser` or `LoginRequest`.
- `async def login`.
- A migration whose `downgrade()` is `pass`.
- A dict-returning endpoint with no `response_model=`.
- An integer HTTP status literal in `app/`.
- A `fetch(` with no `credentials: 'include'`.
- A static `component:` route, or a top-level `import` of a feature component in `app.routes.ts`.
  **The one sanctioned `component:` is `AppShellComponent` on the `''` parent**
  ([app.routes.ts:46](../../apps/web/src/app/app.routes.ts#L46)) — it frames every authenticated
  route behind `canActivate: [authGuard]`, so lazy-loading it only adds a round trip. A second one
  is a violation (§5.8 F1).
- A component stylesheet declaring a flat, un-prefixed English class name. **Except a `.chip`
  tone that has no global definition to collide with** — `.chip.neutral` lives in
  `styles/reep-v2-resume.scss:519`, not `reep-v2.scss`, which is why
  `uploads.component.scss:401-404` is not a redefinition (§3.9). Verify the collision exists
  before you call it one.

### 6.2 A new endpoint

1. Router prefix is the bare domain noun; `/api` is added in `main.py` (except `agent`, `voice`,
   `interview`, which carry it themselves).
2. Path is lowercase-hyphenated; path parameters are `{resource_id}`; the collection root is `""`.
3. `Depends(get_current_session)` is present — **it is the only authentication in the app**.
4. `db: Session = Depends(get_db)` is **last**.
5. `response_model=` is set. If the return is a bare `dict`, justify it or add a schema.
6. Schemas are `<Thing>In` / `<Thing>Out`, declared immediately above the handler; the body
   parameter is called `body`.
7. Enums are read out with `.value`; enum-typed schema fields are `str`.
8. Statuses are `status.HTTP_*` constants; details are complete sentences for the end user;
   cross-scope refusal is 404.
9. Staff endpoint touching one student → `_assert_can_access_student(session, student_id, db)`,
   with `student_id` **derived from the fetched row**, in the order role → fetch → scope → state →
   mutate.
10. Student endpoint → `_require_student(session)` and every query filtered by that id.
11. Handler is `def`, not `async def`, unless it awaits something real.
12. If the assistant might reuse it, it reads only `session.get("studentId")`.
13. Add a test. If it touches Postgres, mark it `@requires_db`.

### 6.3 A new model

1. Module is `snake_case`, one domain slice, and **added to `app/models/__init__.py`** — the
   silent failure.
2. Class is PascalCase singular (§2.2.1); table is the snake_case plural (§2.8.1a).
3. Columns are snake_case (§2.8.2a) with the right unit suffix; booleans are bare adjectives with
   no `is_` prefix (§2.8.2d).
4. `DateTime(timezone=True)` on every temporal column (§2.8.5a); `created_at` uses
   `server_default=func.now()` (§2.8.5b).
5. FKs are `<target>_id`, or the role where the role matters (§2.8.3a, §2.8.3c) — **not** a bare
   `resolved_by` (§2.8.3d).
6. Indexes are named explicitly in `__table_args__`, `ix_<short table>_<short intent>`, first
   positional argument (§2.10.1), **never inline `index=True`** (§2.10.4).
7. Unique constraints are `uq_<singular thing>` with `name=` (§2.10.5) — not inline `unique=True`,
   which 14 columns already do and none of them can be found from the model.
8. New enum: `class X(str, enum.Enum)` with values repeating the names, an explicit snake_case
   `name=`, declared in the module that first needs it.
9. Relationships name the other end and use `back_populates` on both sides; never `backref`
   (§2.8.4).
10. Ship the migration in the same commit.

### 6.4 A new migration

1. `down_revision` is the current head; `alembic heads` prints exactly one row afterwards.
2. `downgrade()` is real — never `pass`. If state cannot be restored, write the schema-level
   inverse and say in a comment what is lost.
3. Enum column on an existing table → `CREATE TYPE` first, then `create_type=False`.
4. New table reusing an existing enum → `postgresql.ENUM(..., name='x', create_type=False)`,
   hand-fixed from what autogenerate emitted.
5. Brand-new enum → `.create(op.get_bind(), checkfirst=True)` first in `upgrade()`,
   `.drop(..., checkfirst=True)` **last** in `downgrade()`.
6. Adding an enum member → hand-written `ALTER TYPE … ADD VALUE IF NOT EXISTS`, with a comment
   saying `downgrade()` cannot mirror it.
7. Drop only the types this revision created.
8. Expression/partial/functional indexes are `op.execute` in **both** halves, mirrored in the
   model's `__table_args__`, dropped with `DROP INDEX IF EXISTS`.
9. Every new column on a populated table is nullable or has a `server_default`.
10. Do not rename the generated filename.
11. **If the revision needs an image, extension or privilege, check `.github/workflows/ci.yml`
    too** — that is exactly how the pgvector work broke CI.

### 6.5 A new screen

1. Directory named for the screen, holding `.ts` + `.html` + `.scss`; singular `styleUrl`.
2. Class is `<Name>Component`; selector is `app-<area>-<name>`.
3. Registered in `app.routes.ts` with `loadComponent`, **no top-level import**.
4. Added to `app-shell.component.html`'s nav, or you have deliberately built an orphan.
5. `:host { display: block; }` opens the stylesheet.
6. Every local class is BEM-prefixed; global classes are reused, never redefined.
7. Generation-2 tokens only; no new `--reep-*`, no dark-mode block.
8. DTO interfaces are snake_case verbatim from the `*Out` schema, with a doc comment naming the
   endpoint and the schema.
9. Every `fetch` carries `credentials: 'include'` and uses `` `${environment.apiBase}/...` ``.
10. `!res.ok` and `catch` produce **different** messages; the error body is read as `.detail`,
    with `.catch(() => ({}))` on `res.json()`.
11. Status is text **plus** colour; every lookup table has a `??` fallback.
12. Nothing the API already decided is recomputed.
13. Templates use `@if`/`@for … track`/`@empty`; no `*ngIf`, no `CommonModule`.
14. Services are injected as field initialisers with `inject()`.
15. New signals in a service use the `_private` + `.asReadonly()` pair.

### 6.6 A new model call (anything that talks to an LLM)

1. Does the prompt contain a student's record? If **yes**:
   - `cfg = llm_config()`, then `if cfg is not None and student_data_egress_allowed(cfg.base_url)`
     **before building the prompt**;
   - `complete_chat(..., carries_student_data=True)` inside that branch;
   - the deterministic result is composed **first** and the result variables initialised to the
     refused outcome, so a refusal degrades to a field, not a 500;
   - the refusal branch's `note` names `LLM_ALLOW_REMOTE_STUDENT_DATA`.
2. If **no** — public content only — say so in a one-line comment beside the call, and pass
   `carries_student_data=False` explicitly.
3. A third `carries_student_data=True` call site is a **review event**: flag it, do not wave it
   through.
4. Adding a provider? Follow §5.9 exactly — the `key_attr` typo is silent.
5. Retrieval change? Widen the refusal test in the **same** commit, and re-measure
   `_MAX_VEC_DISTANCE` if the provider, model or corpus moved.
6. Patching the adapter in a test? Patch the **importing module's** namespace, never
   `app.ai.llm.complete_chat`.

### 6.7 A new setting or environment variable

The silent-failure capital of the repo, and until now the one change type with no block here.
`extra="ignore"` ([config.py:30](../../apps/api-py/app/config.py#L30)) means a **typo is
discarded with no error at all**, and `docs/deployment-env.md:16-19` records that every field has
a default, so "a missing variable does not crash, it silently selects a development default."
Nothing in the tree greps `.env.example` against `Settings`, in either direction.

1. **Is it read by the API or by the worker?** The worker (`voice_agent.py`) has its own
   `os.getenv` calls and **no `Settings` field will exist** — `VOICE_TTS` is the live example
   (`.env.example:75` ↔ [voice_agent.py:127](../../apps/api-py/voice_agent.py#L127)). If it is
   worker-only, say so in the `.env.example` comment, because otherwise the next reader greps
   `config.py`, finds nothing, and concludes the variable is dead.
2. **Field name is the exact lowercase of the env var** — `voice_worker_secret` ←
   `VOICE_WORKER_SECRET` (§2.5.1). For a provider key this is load-bearing beyond style:
   `ai/adk.py:27` reconstructs the env var by *uppercasing the field name* (§5.9).
3. **Numeric?** Add it to `_blank_is_default` ([config.py:162](../../apps/api-py/app/config.py#L162)),
   a `mode="before"` validator, or a bare `MY_VAR=` line in a shared `.env` raises a
   `ValidationError` **at import, before uvicorn binds a socket** — §2.5.4 and the runbook at
   §12.6 Symptom F.
4. **Range- or sign-constrained?** Add an AFTER validator beside `_must_be_positive`
   ([:193](../../apps/api-py/app/config.py#L193)) or `_threshold_in_range`
   ([:206](../../apps/api-py/app/config.py#L206)). Validate at **startup**, not mid-request: an
   out-of-range value that the upstream API rejects with a non-fatal `error` event runs the whole
   feature on a default while the config claims otherwise.
5. **Boolean-ish?** Declare it `str = ""` and coerce in a `@property` (§2.5.3), so a blank line
   in a file four processes share stays legal.
6. **Derived from other settings?** A read-only `@property`, **never a second field** (§2.5.2) —
   ten of them already exist and none has an env var of its own.
7. **Document it in `.env.example`** with the reason for the default, not a restatement of it —
   and check the default you write there against `config.py` **and** against
   `docker-compose.prod.yml`, which is where `VOICE_TTS` already disagrees with itself.
8. **Add a row to §8** of this chapter: variable, read by, default, and *what breaks if it is
   wrong or unset*. The last column is the one an operator at 2 a.m. actually reads.
9. **If it must reach production, add it to `docker-compose.prod.yml`.** That file already omits
   `OPENAI_API_KEY`, `LLM_*`, `EMBEDDING_*` and `VOICE_MAINTENANCE_MESSAGE` (§8.6) — an omission
   nothing detects, because every one of them has a default.
10. Remember `settings` is an **import-time singleton** ([config.py:327](../../apps/api-py/app/config.py#L327)):
    pasting a key into `.env` does nothing until a genuine restart, and on Windows that means
    killing port 3300 (§12.7), not trusting `--reload`.

---

## 7. Running it

### 7.1 The process list (as of 2026-08)

| # | Process | Where from | Port | Required? |
|---|---|---|---|---|
| 1 | **Postgres 17 + pgvector** | `docker compose up -d` (repo root) | host **5433** → container 5432 | yes |
| 2 | **FastAPI API** (`uvicorn app.main:app`) | `apps/api-py`, venv `.venv`, **Python 3.14** | **3300** | yes |
| 3 | **Angular SPA** (`ng serve`) | `apps/web` | **4200** | yes (dev) |
| 4 | **LiveKit voice worker** (`voice_agent.py`) | `apps/api-py`, venv `.venv-voice`, **Python 3.12** | none (outbound only) | **optional, and currently has no UI caller** |

**There is no fifth process.** The student-facing mock interviewer (`/student/assistant`) is a
WebSocket relay that runs *inside* process 2 —
[app/routers/interview.py](../../apps/api-py/app/routers/interview.py) mounted by
[app/main.py:105](../../apps/api-py/app/main.py#L105), engine in
[app/interview_relay.py](../../apps/api-py/app/interview_relay.py). It needs no extra venv and no
extra port (`docs/interview-assistant.md:10-14`).

**Process 4 is now the rollback path, not the live feature.**
[app/routers/agent.py:1-16](../../apps/api-py/app/routers/agent.py#L1-L16) states it plainly: the
LiveKit stack and `POST /api/agent/ask` are "SUPERSEDED AS THE STUDENT-FACING ASSISTANT — 2026-08.
RETAINED FOR ROLLBACK." Starting the voice worker and hunting for a button in the UI is a wasted
afternoon; nothing calls it.

`apps/interview-realtime/` is a **superseded standalone prototype** with no auth and no database —
**do not run it** (`AGENTS.md:73`, `docs/interview-assistant.md:11-14`).

### 7.2 Windows (PowerShell) — the documented path

```powershell
# 1. Database
docker compose up -d                      # from repo root

# 2. Back end (once)
cd apps\api-py
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt     # runtime + pytest
copy .env.example .env                                # then fill it in

# 2b. Back end (every time)
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m app.seed                      # idempotent dev seed
.venv\Scripts\python -m uvicorn app.main:app --port 3300
#    -> http://127.0.0.1:3300/docs

# 3. Front end
cd ..\web
npm ci
npx ng serve                                          # -> http://localhost:4200

# 4. Voice worker — OPTIONAL, no UI caller (rollback path only)
cd ..\api-py
py -3.12 -m venv .venv-voice                          # once
.venv-voice\Scripts\pip install -r requirements-voice.txt
.venv-voice\Scripts\python voice_agent.py dev         # `start` in production
```

> **Windows note, from `AGENTS.md:22`.** `uvicorn --reload` has wedged a stale worker on this
> host. After editing backend files, kill port 3300 and restart rather than relying on
> `--reload`. See runbook §12.7.

### 7.3 POSIX (macOS / Linux)

The repo only ever documents the Windows `Scripts/` paths (`AGENTS.md:16-29`;
`apps/api-py/README.md:40-53` for the API venv and `:73-76` for the voice venv). The POSIX
equivalents are `bin/` — **inferred from the standard
venv layout, not from a file in this repo:**

```bash
docker compose up -d

cd apps/api-py
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env

.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app.seed
.venv/bin/python -m uvicorn app.main:app --port 3300

cd ../web && npm ci && npx ng serve

# optional worker
cd ../api-py
python3.12 -m venv .venv-voice
.venv-voice/bin/pip install -r requirements-voice.txt
.venv-voice/bin/python voice_agent.py dev
```

### 7.4 The venv layout, and why 3.12 for the second

```
apps/api-py/
  .venv/          Python 3.14  — requirements-dev.txt (runtime + pytest). API, Alembic, seeds, tests.
  .venv-voice/    Python 3.12  — requirements-voice.txt. The LiveKit worker, and nothing else.
```

The worker is a **separate environment on a different interpreter**:

- `apps/api-py/requirements-voice.txt:1-2` — "install in a SEPARATE environment from the FastAPI
  server. The audio/ML stack is heavy and wants Python 3.12". (`:7-9` is the separate CASCADE
  architecture note; `:14-21` is the incomplete-manifest narrative.)
- `apps/api-py/Dockerfile.voice:3-11` gives three reasons: `livekit-agents` declares
  `Requires-Python: <3.15,>=3.10` (`:3-4`); **3.12 is the version the worker's SDK contract was
  actually verified against** (`:4-6`); and `livekit-plugins-silero` depends on `onnxruntime`,
  which **publishes no musl wheels at all** (`:8-11`), so glibc ≥ 2.28 (bookworm) is required and
  Alpine is out. Line `:13` is the `FROM`.
- The dependency sets barely overlap. `apps/api-py/Dockerfile:3-7`: the API image is about 370 MB
  (litellm + google-adk), the worker about 505 MB (livekit + onnxruntime); merging them ships
  roughly 800 MB of site-packages to both.

> **DRIFT.** `AGENTS.md:30` justifies 3.12 with "`livekit-agents` declares
> `Requires-Python: <3.15`" alone. That bound *admits* 3.14, so on its own it does not exclude the
> API's interpreter. `Dockerfile.voice:3-11` carries the real reasons. **Trust the Dockerfile.**

The worker reads **the same `apps/api-py/.env`** — it `setdefault`s the file into `os.environ` at
import ([voice_agent.py:114](../../apps/api-py/voice_agent.py#L114)) — so credentials are entered
once. A real environment variable always wins over the file, which is what lets `REEP_API_URL` and
`VOICE_WORKER_ID` be overridden per process.

**Seeded logins** — the repo publishes them in exactly one place, `AGENTS.md:34`:
`student@bgscet.ac.in` / `student123`, `mentor@bgscet.ac.in` / `mentor123`,
`director@bgscet.ac.in` / `director123`. (`apps/api-py/README.md` carries no credentials at all;
`grep -n bgscet apps/api-py/README.md` returns nothing.) **That single location is load-bearing:**
`app.seed`'s production guard and §10.2 both argue from "the password is published in
`AGENTS.md`", and a DIRECTOR account behind a published password is why the guard has no override
flag.

---

## 8. The complete environment-variable reference

[app/config.py:29-30](../../apps/api-py/app/config.py#L29-L30) — `Settings(BaseSettings)` with
`env_file` pinned to `apps/api-py/.env` (`_ENV_FILE`, `config.py:13-17`) and **`extra="ignore"`**.
Field names map to env vars case-insensitively (`database_url` ← `DATABASE_URL`).

> **Two consequences an operator must internalise.**
> 1. `extra="ignore"` means a **misspelled variable is silently discarded** — no warning, no error.
> 2. `docs/deployment-env.md:16-19`: *every* field has a default, so "a missing variable does not
>    crash, it silently selects a development default."

**Legend for "Read by"** — **API** = the uvicorn process (`app/config.py`); **Worker** =
`voice_agent.py` via its own `os.getenv`; **libpq** = the Postgres client library directly;
**compose** = a compose file only; **tests** = `tests/conftest.py`.

### 8.1 Core

| Variable | Read by | Default | What breaks if wrong or unset |
|---|---|---|---|
| `DATABASE_URL` | API ([config.py:32](../../apps/api-py/app/config.py#L32)), prod compose (`migrate` `:46`, `api` `:60` — required; the `db` service takes only `POSTGRES_*`) | `postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_py` | In a container the default is the container's **own** loopback — nothing listening. [config.py:294-324](../../apps/api-py/app/config.py#L294-L324) forces `+psycopg`, strips only the Prisma-only params `schema` / `connection_limit` / `pgbouncer`, and **preserves `sslmode`** (an earlier version discarded the whole query string, silently downgrading a managed DB to unverified TLS). |
| `AUTH_SECRET` | API ([config.py:34](../../apps/api-py/app/config.py#L34)), prod compose (`migrate`, `api` — required) | a literal dev string in `config.py` | Sessions are HS256 JWTs. Shipping the default lets **anyone who has read this repo mint a valid cookie for any user, including a DIRECTOR**. Use ≥32 random bytes. |
| `WEB_ORIGIN` | API ([config.py:35](../../apps/api-py/app/config.py#L35)), prod compose (required) | `http://localhost:4200` | The CORS allow-list ([main.py:91-97](../../apps/api-py/app/main.py#L91-L97), `allow_credentials=True` so it cannot be `*`) **and** the WebSocket Origin check at [interview.py:211-223](../../apps/api-py/app/routers/interview.py#L211-L223) (close **4003**). Wrong ⇒ login appears to work, then the app behaves as logged-out. |
| `ENV` | API ([config.py:36](../../apps/api-py/app/config.py#L36)), prod compose (`prod`) | `dev` | `prod` marks the cookie `Secure` (**requires TLS in front** — otherwise the browser drops it), fails `require_voice_worker` closed on a blank secret, and makes `python -m app.seed` refuse. Leaving it `dev` disables all three at once. |
| `UPLOAD_DIR` | API ([config.py:273](../../apps/api-py/app/config.py#L273), resolved at `:280-284`) | `""` → `apps/api-py/var/uploads`; the API image sets `/var/reep/uploads` | Unmounted in a container ⇒ uploads land in the image layer and **every redeploy destroys student files**. **Absent from `.env.example`.** |
| `PGSSLMODE` | libpq | prod compose default `prefer` | Read by libpq directly, so TLS verification works regardless of the `DATABASE_URL` query string. Set `require` or stricter for a managed DB. |
| `PGSSLROOTCERT` | libpq | `""` | CA bundle for `verify-full`. |

### 8.2 LLM adapter (`app/ai/llm.py`) — the text agent and resume polish

| Variable | Read by | Default | What breaks if wrong or unset |
|---|---|---|---|
| `LLM_BASE_URL` | API ([config.py:40](../../apps/api-py/app/config.py#L40)) | `""` | With `LLM_MODEL` and `LLM_API_KEY` this is the explicit provider and wins over auto-select. **Open question (FINDINGS.md):** a remote base + model with a **blank key** silently falls through to auto-select ([llm.py:88](../../apps/api-py/app/ai/llm.py#L88) — `if base and model and (key or is_loopback(base))`). The docstring at [llm.py:80-81](../../apps/api-py/app/ai/llm.py#L80-L81) documents the *condition* — *"Explicit override — LLM_BASE_URL + LLM_MODEL set (key optional for a local model)"* — but nothing documents the **consequence**, that a remote trio with a blank key is silently discarded rather than refused. No test covers it. |
| `LLM_MODEL` | API (`config.py:41`) | `""` | as above |
| `LLM_API_KEY` | API (`config.py:42`) | `""` | as above |
| `LLM_TIMEOUT_MS` | API ([config.py:43](../../apps/api-py/app/config.py#L43)) | `300000` | A **blank line is legal** and means "default" — [config.py:151-183](../../apps/api-py/app/config.py#L151-L183) coerces `""` back to the field default. Before that validator existed, `LLM_TIMEOUT_MS=` raised at *import*, before uvicorn bound a socket, and the whole dashboard died at boot. |
| `LLM_ALLOW_REMOTE_STUDENT_DATA` | API ([config.py:46](../../apps/api-py/app/config.py#L46)), gate at [:287-288](../../apps/api-py/app/config.py#L287-L288) | `""` (closed) | **Rule 1.** Closed ⇒ student PII never leaves the process for a non-loopback model; `/student/resume/generate` composes deterministically and returns `used_ai=false`. **Confirmed:** the gate is `.strip().lower() == "true"`, so `TRUE`, `True` and `" true "` all open it — *more* permissive than the "exact string" the comments claim. |
| `SAKANA_API_KEY` | API (`config.py:57`) | `""` | Auto-select order is **Sakana → Groq → Mistral → OpenRouter → Gemini → Cohere**; Sakana is checked first. |
| `GROQ_API_KEY` | API ([config.py:51](../../apps/api-py/app/config.py#L51), also raw `os.getenv` at `:265`), Worker (via `livekit-plugins-groq`), prod compose (worker: required) | `""` | Triple duty: an LLM provider for the API, **and** the worker's STT + LLM + optional TTS. `voice_model_key_present` ([config.py:254-265](../../apps/api-py/app/config.py#L254-L265)) gates voice on this key and deliberately **not** on the Gemini key. |
| `MISTRAL_API_KEY` | API (`config.py:52`) | `""` | Also auto-selects the KB embedder (`mistral-embed`). |
| `OPENROUTER_API_KEY` / `COHERE_API_KEY` | API (`config.py:53-54`) | `""` | auto-select chain members |
| `GEMINI_API_KEY` | API (`config.py:55`, `:250`) | `""` | Auto-select member. **Not used by voice** (`.env.example:62`). |
| `GOOGLE_API_KEY` | API — **raw `os.getenv` only**, [config.py:251](../../apps/api-py/app/config.py#L251) | `""` | Not a `Settings` field. It counts toward `gemini_key_present` but you will not find it in a settings dump. |

### 8.3 Knowledge-Base embedder (`app/ai/embeddings.py`)

| Variable | Read by | Default | What breaks if wrong or unset |
|---|---|---|---|
| `EMBEDDING_BASE_URL` | API ([config.py:64](../../apps/api-py/app/config.py#L64)) | `""` | Unset ⇒ `embedder_configured()` is false ⇒ retrieval degrades to **Postgres full-text only**. Nothing breaks; answer quality drops. **Absent from `.env.example`.** |
| `EMBEDDING_MODEL` | API (`config.py:65`) | `""` | as above |
| `EMBEDDING_API_KEY` | API (`config.py:66`) | `""` | as above. Falls back to auto-selecting Mistral `mistral-embed` from `MISTRAL_API_KEY`. The KB is approved public policy text, so this is **outside** the Rule-1 gate. |

### 8.4 Voice — the LiveKit cascade (retained, no UI caller)

| Variable | Read by | Default | What breaks if wrong or unset |
|---|---|---|---|
| `LIVEKIT_URL` | API ([config.py:70](../../apps/api-py/app/config.py#L70)), Worker (SDK reads env), prod compose (api optional, worker required) | `""` | Any of the three missing ⇒ `livekit_ready` false ([config.py:267-269](../../apps/api-py/app/config.py#L267-L269)) ⇒ `POST /api/voice/token` **503**. |
| `LIVEKIT_API_KEY` | same | `""` | same |
| `LIVEKIT_API_SECRET` | same | `""` | same. `.env.example:56-57`: copy with the console's copy button — selecting the masked value copies bullet characters. |
| `VOICE_WORKER_SECRET` | API ([config.py:75](../../apps/api-py/app/config.py#L75)), **Worker ([voice_agent.py:145](../../apps/api-py/voice_agent.py#L145))**, prod compose (**required in both**) | `""` | **The single most confusing failure in the stack.** Values disagreeing ⇒ every heartbeat and transcript POST 401s; `/api/voice/status` reports the worker offline forever while the worker's own log looks healthy. Blank + `ENV=prod` ⇒ `require_voice_worker` returns **500** (fail-closed). Blank + `ENV=dev` ⇒ the endpoints are **open**, and a forged heartbeat makes voice claim availability with no worker behind it. The API says so at startup: [main.py:63-69](../../apps/api-py/app/main.py#L63-L69) is the `log.warning` call, under the `lifespan` docstring at [`:34-48`](../../apps/api-py/app/main.py#L34-L48) that explains why it is a warning and not a hard failure. |
| `VOICE_MAINTENANCE_MESSAGE` | API ([config.py:78](../../apps/api-py/app/config.py#L78); used [voice.py:189-194](../../apps/api-py/app/routers/voice.py#L189-L194)) | `""` | Non-empty is the **kill switch**: it overrides a perfectly healthy provider *and* worker and forces `/token` to **503**. |
| `VOICE_TTS` | **Worker only** ([voice_agent.py:127](../../apps/api-py/voice_agent.py#L127)) | `edge` | `edge-tts` is an **unofficial, no-SLA endpoint with no privacy terms** (`.env.example:68-70`). `groq` uses the same `GROQ_API_KEY`, but the free tier is 10 req/min and 100/day org-wide, shared with the API's LLM calls. **DRIFT:** `docker-compose.prod.yml:121` defaults this to `groq`; `voice_agent.py:127` and `.env.example:75` default to `edge`. **No `Settings` field exists for it.** |
| `GROQ_TTS_MODEL` | Worker (`voice_agent.py:128`) | `canopylabs/orpheus-v1-english` | Requires a one-off terms acceptance at console.groq.com. |
| `GROQ_TTS_VOICE` | Worker (`voice_agent.py:129`) | `autumn` | — |
| `EDGE_TTS_VOICE` | Worker (`voice_agent.py:136`) | `en-IN-PrabhatNeural` | — |
| `REEP_API_URL` | **Worker only** ([voice_agent.py:142](../../apps/api-py/voice_agent.py#L142)) | `http://localhost:3300` | **Must be `http://api:3300` in containers.** The default was correct when the worker sat beside the API on a laptop; in a container the worker's own loopback has nothing listening, and heartbeats and transcripts silently vanish. |
| `VOICE_WORKER_ID` | Worker ([voice_agent.py:147](../../apps/api-py/voice_agent.py#L147)) | random `voice-agent-<8 hex>` per process | Identifies the heartbeat row; the table is reaped, so the default is fine. |
| `VOICE_HEARTBEAT_INTERVAL_SECONDS` | Worker ([voice_agent.py:151](../../apps/api-py/voice_agent.py#L151)) | `10` | The freshness window is `HEARTBEAT_FRESH_SECONDS = 30` ([voice.py:43](../../apps/api-py/app/routers/voice.py#L43)). **DRIFT:** comments at `voice_agent.py:38` and `routers/voice.py:150` both say "~15s"; the default is 10. Comment drift only. |
| *(do not set)* `LIVEKIT_AGENT_NAME` | — | — | `.env.example:144-148`: the dispatch name `reep-voice` is compile-time on **both** sides (`VOICE_AGENT_NAME`, [voice.py:58](../../apps/api-py/app/routers/voice.py#L58)). Making it an env var invites disagreement, and that failure is silent: the token mints, the room opens, the mic publishes, **no agent ever joins**. |

### 8.5 Realtime mock interview — the live student-facing assistant

| Variable | Read by | Default | What breaks if wrong or unset |
|---|---|---|---|
| `OPENAI_API_KEY` | API ([config.py:101](../../apps/api-py/app/config.py#L101); `realtime_ready` at [:214-224](../../apps/api-py/app/config.py#L214-L224)) | `""` | **Blank is off, and only for this feature.** `GET /api/interview/status` reports unavailable with a reason and the socket closes **4001** ([interview.py:256-265](../../apps/api-py/app/routers/interview.py#L256-L265)). Nothing else is affected. `realtime_ready` `.strip()`s because a pasted key routinely carries a trailing newline — whitespace is a 401 the *student* meets. Deliberately **not** in the LLM auto-select chain ([config.py:87-96](../../apps/api-py/app/config.py#L87-L96)). |
| `OPENAI_REALTIME_MODEL` | API ([config.py:102](../../apps/api-py/app/config.py#L102); used `:227-236`) | `gpt-realtime` | Blank falls back to the default; a blank model in the query string would be a **404 at handshake**, which reads to a student as "the interview is down". |
| `OPENAI_REALTIME_BASE_URL` | API ([config.py:103](../../apps/api-py/app/config.py#L103)) | `wss://api.openai.com/v1/realtime` | Composed with the percent-encoded model in exactly one place — `realtime_url`, [config.py:227-236](../../apps/api-py/app/config.py#L227-L236). |
| `OPENAI_REALTIME_VOICE` | API ([config.py:109](../../apps/api-py/app/config.py#L109)) | `alloy` | Frozen once the model emits audio. `alloy` exists on both API generations; `marin` and `cedar` are GA-only, and an unknown name yields an `error` event and a **silent** fall back — it fails without failing. |
| `OPENAI_REALTIME_BETA_HEADER` | API ([config.py:116](../../apps/api-py/app/config.py#L116); `realtime_beta_header` at `:239-241`) | `""` (GA) | Non-empty (`"realtime=v1"`) pins the **beta** event surface (`response.audio.delta`, flat session object); blank selects **GA** (`response.output_audio.delta`, nested `session.audio.*`). This is the lever for when a generation is retired, not a knob to turn casually. |
| `INTERVIEW_MAX_SECONDS` | API ([config.py:121](../../apps/api-py/app/config.py#L121)) | `900` (15 min) | A cost ceiling: audio bills per second of a session a forgotten tab holds open. Hitting it closes **4009**. `0` or negative is **rejected at startup** ([config.py:185-202](../../apps/api-py/app/config.py#L185-L202)) — otherwise it would mean "close every session the instant it opens", indistinguishable from an upstream outage. |
| `INTERVIEW_IDLE_SECONDS` | API ([config.py:125](../../apps/api-py/app/config.py#L125)) | `120` | No inbound audio for this long ⇒ close **4008**. Two minutes survives a long thinking pause plus a reconnect. |
| `INTERVIEW_MAX_SESSIONS` | API ([config.py:131](../../apps/api-py/app/config.py#L131); `_LIMITER`, [interview.py:85](../../apps/api-py/app/routers/interview.py#L85)) | `100` | **Per-worker.** N uvicorn workers give N× this. Over the cap the socket closes **1013** immediately rather than queueing behind a clock that has not started. |
| `INTERVIEW_VAD_THRESHOLD` | API ([config.py:147](../../apps/api-py/app/config.py#L147)) | `0.5` | Validated to `0.0–1.0` at startup ([config.py:204-212](../../apps/api-py/app/config.py#L204-L212)) because an out-of-range value is rejected upstream with an `error` event that does **not** close the socket — the interview would quietly run on the default while the config claimed otherwise. |
| `INTERVIEW_VAD_PREFIX_PADDING_MS` | API ([config.py:148](../../apps/api-py/app/config.py#L148)) | `300` | Below about 200 ms, candidates lose the leading consonant of "Actually…". |
| `INTERVIEW_VAD_SILENCE_DURATION_MS` | API ([config.py:149](../../apps/api-py/app/config.py#L149)) | `700` | Above the API's 500 ms default on purpose: a real answer contains 400–600 ms mid-sentence thinking pauses, and the persona promises not to interrupt. |

### 8.6 Compose-only and test-only

| Variable | Read by | Default | Notes |
|---|---|---|---|
| `POSTGRES_USER` | dev compose (literal `reep`), prod compose (**required**) | — | Also the superuser `docker/initdb/01-create-reep-py.sh` runs as. |
| `POSTGRES_PASSWORD` | dev compose (literal `reep_dev_password`), prod compose (**required**) | — | — |
| `POSTGRES_DB` | dev compose (`reep_dev`), prod compose (`reep_py`) | — | **Dev creates `reep_dev`, which the app never uses.** The initdb script creates `reep_py` separately; the healthcheck probes `reep_py`, "not `reep_dev`: `reep_dev` existing proves nothing about the database the application connects to" (`docker-compose.yml:26-28`). |
| `REEP_REQUIRE_DB` | **tests only** ([tests/conftest.py:38](../../apps/api-py/tests/conftest.py#L38)) | unset | `1` / `true` / `yes` turns an unreachable Postgres into a hard `pytest.UsageError` instead of a silent skip. CI sets it. |

> **DEFECT — new, verified here.** `docker-compose.prod.yml`'s `api` service environment block
> passes `DATABASE_URL`, `AUTH_SECRET`, `WEB_ORIGIN`, `ENV`, `LIVEKIT_*`, `GROQ_API_KEY`,
> `VOICE_WORKER_SECRET`, `PGSSLMODE` and `PGSSLROOTCERT` — and **no `OPENAI_API_KEY`**, no
> `LLM_*`, no `EMBEDDING_*`, no `VOICE_MAINTENANCE_MESSAGE`. Under that compose file as written,
> the **current student-facing assistant is permanently unavailable** (`/api/interview/status` →
> `available:false`, socket closes 4001), the LLM adapter has only Groq, KB retrieval is
> full-text-only, and the voice kill switch cannot be thrown without editing the file. The compose
> file predates the interview relay and has not caught up.

---

## 9. Docker and deployment

### 9.1 `docker-compose.yml` — dev, **database only**

One service, `db`, image **`pgvector/pgvector:pg17`**, container `reep-postgres`, host port
**5433**, volume `reep_pgdata`.

- **Why 5433:** "so this never collides with the PostgreSQL 17 service already listening on 5432
  on this machine" (`docker-compose.yml:14-15`).
- **Why the pgvector image and not stock PG17:** stock `postgres:17` has no `vector.control`.
  `migrations/versions/b7e2f4a19c33_kb_embedding_pgvector.py:37` runs
  `CREATE EXTENSION IF NOT EXISTS vector`; `IF NOT EXISTS` suppresses "already exists" but **does
  not** help when the extension is not installed on the server at all. The image is stock PG17 plus
  the extension *available to create* (`docker-compose.yml:3-7`).
- **The alpine→debian switch was safe:** same PG major, so `reep_pgdata` mounts unchanged; the
  libc/collation change was "remediated with REINDEX + REFRESH COLLATION VERSION, data preserved".
- **The healthcheck probes `reep_py`, not `reep_dev`** (`docker-compose.yml:26-28`).

### 9.2 `docker/initdb/01-create-reep-py.sh`

Mounted read-only at `/docker-entrypoint-initdb.d` by **both** compose files. It does two things:

1. `CREATE DATABASE reep_py`, guarded by a `pg_database` existence check plus `\gexec`.
   `POSTGRES_DB` creates `reep_dev`, but [config.py:32](../../apps/api-py/app/config.py#L32)
   defaults to `reep_py`, so a fresh `up -d` used to produce a server **without the app's
   database** and the API failed with `FATAL: database "reep_py" does not exist`.
2. `CREATE EXTENSION IF NOT EXISTS vector` in `reep_py` (the statement itself is at
   `01-create-reep-py.sh:26-28`). **This is why the extension exists at all:** `vector.control` is
   not marked `trusted = true`, so `CREATE EXTENSION` needs superuser — which this script has and
   the app's role does not. The reasoning is the comment at
   **`01-create-reep-py.sh:10-13`**: *"`vector.control` is not marked `trusted = true`, so CREATE
   EXTENSION needs superuser — which is exactly what this script runs as, and is why the KB
   migration cannot create it itself when the app connects as an unprivileged role."*

> **THE CAVEAT THAT BITES.** `/docker-entrypoint-initdb.d` runs **only when the data directory is
> empty**. An existing `reep_pgdata` volume will not re-run it — that is Postgres behaviour, not a
> bug. The script says so itself at **`01-create-reep-py.sh:15-18`**, ending *"For an existing
> volume, run the same two statements by hand once."* Those two statements are the heredocs at
> `:21-28`; §12.6 Symptom A gives them as copyable `docker exec` lines.

> **STALE.** `apps/api-py/.env.example:5-6` still instructs
> `docker exec reep-postgres createdb -U reep reep_py`. The initdb script replaced that step on a
> fresh volume — but the instruction is still exactly what you need on a pre-existing one.

### 9.3 `docker-compose.prod.yml` — production-shaped

Deliberately **not** the default file: `docker compose -f docker-compose.prod.yml up -d`. Secrets
come from the environment as `${VAR:?message}` so a missing one **fails the deploy loudly** rather
than starting a half-configured stack.

Four services, and the ordering contract:

1. **`db`** — `pgvector/pgvector:pg17`, `POSTGRES_DB: reep_py`, same initdb mount. **No published
   port** — "Publishing 5432 would expose student records to the host." Limits 2 CPU / 2 GB.
2. **`migrate`** — one-shot `python -m alembic upgrade head`, `restart: "no"`,
   `depends_on: db healthy`. Runs **once, as its own unit**: "Every API replica running
   `alembic upgrade head` on boot races on the version table, and the loser can fail in ways that
   leave the schema half-applied."
3. **`api`** — `depends_on: db healthy` plus `migrate: service_completed_successfully`. Volume
   `reep_uploads:/var/reep/uploads`. Healthcheck hits **`/health` (liveness only)** — "Pointing
   this at `/ready` would restart every API container during a brief Postgres wobble and turn a
   recoverable blip into an outage." `stop_grace_period: 120s`, above uvicorn's
   `--timeout-graceful-shutdown 110`. Limits 2 CPU / 1 GB, reservation 512 MB.
4. **`voice-worker`** — `Dockerfile.voice`, `depends_on: api started`,
   `REEP_API_URL: http://api:3300`. **No healthcheck** — the worker has no inbound HTTP surface;
   liveness is observed through the heartbeat row `GET /api/voice/status` reads.
   `stop_grace_period: **960s**`, derived: `AgentServer(drain_timeout=900)` plus 2 ×
   `shutdown_process_timeout` (20s) plus slack. Limits 2 CPU / 3 GB, reservation 1 GB — Silero and
   livekit-agents are resident per process, plus per-call audio buffers.

Volumes: `reep_pgdata`, `reep_uploads`. **Uploads must outlive the container** — without the
volume they land in the image layer and every redeploy destroys them.

> **DRIFT.** `docs/deployment-env.md:120` says the worker's `stop_grace_period` is **300s**;
> `docker-compose.prod.yml:131` sets **960s** with a written derivation. **The compose file is what
> runs.**

### 9.4 The two Dockerfiles

| | `apps/api-py/Dockerfile` | `apps/api-py/Dockerfile.voice` |
|---|---|---|
| Base | `python:3.14-slim-bookworm` | `python:3.12-slim-bookworm` |
| Installs | `requirements.txt` **only** (no pytest) | `requirements-voice.txt` with `--only-binary=:all:` so a missing wheel fails the *build* loudly instead of starting a 20-minute source compile |
| Copies | `app/`, `migrations/`, `alembic.ini` | `voice_agent.py` **only** — the worker imports nothing from `app/` |
| User | non-root uid 10001 | non-root uid 10001 |
| Port | `EXPOSE 3300` (`Dockerfile:45`) | none |
| CMD | `uvicorn … --host 0.0.0.0 --port 3300 --proxy-headers --forwarded-allow-ips * --timeout-graceful-shutdown 110` | `python voice_agent.py **start**` |

Three comments worth memorising:

- **`--host 0.0.0.0` is required in a container.** Locally uvicorn is started with only `--port`,
  so it binds 127.0.0.1 — in a container that is the container's own loopback, and it "would look
  healthy and refuse every request."
- **`start`, not `dev`.** The SDK only drains on SIGTERM when not in devmode (`cli.py`:
  `if not devmode: loop.run_until_complete(server.drain())`), so `dev` in production kills live
  calls mid-sentence on every rolling deploy.
- **`--reload` is forbidden.** [apps/api-py/Dockerfile:65](../../apps/api-py/Dockerfile#L65) —
  "NEVER `--reload` (AGENTS.md: it wedges a stale worker on this platform)." (`:66-69` is the
  `CMD` array the comment sits above.)

**Neither Dockerfile runs migrations.** That is `migrate`'s job.

### 9.5 The open deployment questions the book could not resolve

Recorded in [FINDINGS.md](FINDINGS.md), unanswered, and load-bearing:

1. **How is the SPA served in production?** There is **no Dockerfile for `apps/web`** and no web or
   reverse-proxy service in `docker-compose.prod.yml` — yet `ENV=prod` marks the cookie `Secure`
   and the API's CMD passes `--proxy-headers`. Nothing in the repo describes what builds the
   Angular bundle, what serves it, or what terminates TLS.
2. **How does traffic reach the API?** The image sets `EXPOSE 3300` but the `api` service declares
   **no `ports:`**.
3. `apps/api-py/KMS/` contains only an empty `logs/`, is referenced by nothing, and is not
   gitignored.
4. `app/ai/adk.py` and `app/ai/agents.py` are imported by nothing outside the `ai` package, yet
   `google-adk` and `litellm` are pinned **runtime** dependencies carried in the production image.
5. **Nothing schedules `app/retention.py`** — soft-delete, message scrubbing and `AgentRun`
   redaction are implemented, tested (233 lines) and **never invoked outside
   `tests/test_retention.py`**. Every conversation is stamped `retention_until` at creation
   ([conversations.py:60](../../apps/api-py/app/conversations.py#L60)) — a deletion date nothing
   enforces. There is no `__main__` block, so even `python -m app.retention` does nothing.
6. **Nothing evaluates alerts.** The only place an `Alert` row is ever constructed is
   `app/seed.py:160`. Since `app.seed` refuses under `ENV=prod`, on a production host the mentor
   `/alerts` queue is permanently empty.
7. **`docker-compose.prod.yml` passes no `OPENAI_API_KEY`** — §8.6. The live student-facing
   feature cannot run under the only production compose file in the repo.

---

## 10. Two requirements files, two seeds, and the fresh-database order

### 10.1 The requirements split

| File | Contents | Who installs it |
|---|---|---|
| `apps/api-py/requirements.txt` | **Runtime only**, pinned `==` | `Dockerfile` (the production image) |
| `apps/api-py/requirements-dev.txt` | `-r requirements.txt` plus `pytest==9.1.1` | developers, CI |
| `apps/api-py/requirements-voice.txt` | the worker's whole stack, pinned `==` | `.venv-voice`, `Dockerfile.voice` |

> **Why pinned `==` and not `>=`.** `requirements.txt:3-7`: "`>=` bounds meant a rebuild three
> months from now resolved a different dependency set than the one these tests passed against: the
> image would drift from the venv silently, and the first sign of it would be a production-only
> failure nobody could reproduce locally." **Bumping: change the pin, run the suite, commit both
> together.**

> **Why the split at all.** `requirements.txt:9-11`: test-only packages "must NOT be here — this
> file is what `Dockerfile` installs, and pytest in a production image is extra attack surface plus
> a bigger download for nothing."

Two pins carry non-obvious reasoning:

- **`websockets==15.0.1`** (`requirements.txt:27`) is "not a NEW dependency — it is the missing
  PIN." `uvicorn[standard]` already pulls it for the *server* side;
  [app/interview_relay.py](../../apps/api-py/app/interview_relay.py) imports
  `websockets.asyncio.client` for the *upstream* side. A free resolver breaks it two ways **at
  connect time rather than import time** — surviving CI and failing on a student's first call:
  `websockets >= 14` renamed `connect()`'s `extra_headers=` to `additional_headers=` (which the
  relay uses), and the modern asyncio API it imports does not exist on the older line.
- **`livekit-agents==1.6.10`** (`requirements-voice.txt`) is pinned exactly rather than `~=`
  because "the SDK contract below was verified against these versions by introspecting the
  installed package, and `~=1.6` silently admits the whole 1.x series (that is how the earlier
  `~=1.5` pin let 1.6.10 in unnoticed)." The file then lists the exact SDK surface
  `voice_agent.py` relies on, ending with: **"On any version bump, re-run that introspection
  BEFORE the first live call."** It also records that adaptive interruption needs
  `stt.capabilities.streaming` **and** aligned transcript, and Groq Whisper has neither — so
  interruption mode must be `"vad"` or the SDK silently disables the detector.

`requirements-voice.txt:14-17` also documents the failure it exists to prevent: it once declared
only `livekit-agents[google]` while `voice_agent.py` imported `groq`, `silero`,
`noise_cancellation` and `edge_tts`, so a clean install raised `ImportError` at startup. It only
worked locally because those had been hand-installed. **CI's `worker-imports` job exists to catch
exactly this.** And note: **no HTTP client is listed** — the worker talks to the API with stdlib
`urllib` only.

### 10.2 The two seeds

| | `python -m app.seed` | `python -m app.seed_kb` |
|---|---|---|
| Creates | the three demo logins, the full demo dataset, **and the KB** (`seed.py:54`, `:557`) | the Knowledge Base **only** |
| Production | **REFUSES** — `if settings.is_prod: … raise SystemExit(1)` ([seed.py:57-70](../../apps/api-py/app/seed.py#L57-L70)) | **safe and required** |
| Override flag | **none, on purpose** — "an escape hatch here would be found and used, and every path through it ends with director123 live on the internet" ([seed.py:58-60](../../apps/api-py/app/seed.py#L58-L60)) | n/a |

The refusal message names the alternative explicitly: *"For the Knowledge Base — the only seed data
production needs — run: python -m app.seed_kb"*.

`seed_knowledge` also backfills pgvector embeddings when a provider is configured
(`seed_kb.py:284-290`) — idempotent, and a no-op without one.

The DIRECTOR account is the reason the guard exists: by Rule 2 that account reads across the whole
programme, behind a password published in `AGENTS.md`.

### 10.3 The exact order on a fresh database

```
1. docker compose up -d
     └─ initdb runs ONLY on an empty volume: CREATE DATABASE reep_py; CREATE EXTENSION vector
        (existing volume? run those two statements by hand once — §12.6 Symptom A)
2. wait for the healthcheck (pg_isready -U reep -d reep_py)
3. .venv/Scripts/python -m alembic upgrade head
     └─ b7e2f4a19c33 runs CREATE EXTENSION IF NOT EXISTS vector; it CANNOT create the
        extension on a server that lacks vector.control, and the app's role is not superuser
4a. dev:  .venv/Scripts/python -m app.seed        (accounts + demo data + KB, idempotent)
4b. prod: python -m app.seed_kb                   (KB only; app.seed refuses under ENV=prod)
5. .venv/Scripts/python -m uvicorn app.main:app --port 3300
6. cd apps/web && npx ng serve
```

Migrations are **never** run from the API entrypoint
([`Dockerfile:51-53`](../../apps/api-py/Dockerfile#L51-L53), the `migrate` service in
`docker-compose.prod.yml`). Seeding is separate and manual in production
(`docs/deployment-env.md:107-111`). Nothing automates step 3 before step 4, and nothing needs to —
seeding an unmigrated DB dies loudly on a missing relation.

---

## 11. The test suite, CI, and where coverage is thin

### 11.1 Running it

```
cd apps/api-py && .venv/Scripts/python -m pytest        # backend
cd apps/web    && npx ng build                          # frontend compile + bundle budget
cd apps/web    && npx ng test --watch=false             # frontend unit tests (two spec files)
```

`apps/api-py/pytest.ini`:

```ini
[pytest]
testpaths = tests
addopts = -q
filterwarnings =
    # The Starlette TestClient httpx-deprecation notice is environment noise.
    ignore::DeprecationWarning
```

Tests import `from conftest import requires_db` directly. **The mechanism is worth getting right,
because the obvious guess is wrong.** `testpaths` only supplies the default collection argument
when none is given on the command line; it does not touch `sys.path`, and rootdir is never
inserted. What makes the import work is that **`apps/api-py/tests/` contains no `__init__.py`**:
under pytest's default `prepend` import mode, a collected file's *basedir* — the first ancestor
directory without an `__init__.py` — is inserted at the front of `sys.path`, and here that
directory is `tests/` itself.

**The practical consequence:** adding `tests/__init__.py` turns `tests` into a package, moves the
basedir up to `apps/api-py/`, and breaks `from conftest import requires_db` in every module at
once. Do not add it.

### 11.2 `@requires_db` and `REEP_REQUIRE_DB`

[tests/conftest.py](../../apps/api-py/tests/conftest.py) is the crux:

```python
DB_UP = _db_reachable()                                                              # :37
REQUIRE_DB = os.getenv("REEP_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes"} # :38
if REQUIRE_DB and not DB_UP:
    raise pytest.UsageError(...)                                                     # :40
requires_db = pytest.mark.skipif(not DB_UP, reason="Postgres reep_py not reachable")  # :49
```

> **Why it is like this.** The `UsageError` text states the hazard: the DB-backed tests
> "(conversations, voice, retention, RBAC) would have been SKIPPED and the suite would have
> reported success without exercising them." A pipeline without Postgres prints a green
> "N passed" having verified essentially nothing about the product. `REEP_REQUIRE_DB=1` converts
> the silent skip into a **hard collection error**.

Three shared fixtures: `client` (a session-scoped `TestClient`), `login(email, password)`
returning a cookie header dict, and `make_user(label, role)` — a factory that creates a throwaway
`User` (plus a `Student` row for STUDENT), logs it in, and **tears every row down** in the
fixture's finaliser.

### 11.3 What each of the 19 files in `tests/` pins

**18 test modules plus `conftest.py`** — the table below names every `.py` file in
`apps/api-py/tests/`, with nothing omitted.

| File | DB? | What it pins |
|---|---|---|
| `conftest.py` | — | the `DB_UP` probe, the `REEP_REQUIRE_DB` hard-fail, and the `client` / `login` / `make_user` fixtures with full teardown |
| `test_egress_gate.py` | no | **Rule 1**, pure logic: `is_loopback` true for `127.0.0.1` / `localhost` / `[::1]`, false for Groq and Gemini; loopback always allowed even with the flag off; remote blocked by default; remote needs "true" |
| `test_registration_rules.py` | no | `_email_domain` extraction (case-folding), and `_rule_matches`: an empty rule is a wildcard, per-condition matching for email domain / USN pattern / degree level, all conditions ANDed |
| `test_resume_pdf.py` | no | `render_resume_pdf` emits real PDF bytes (`%PDF-` … `%%EOF`, >800 B), blank markdown still yields a valid PDF, and angle brackets in data cannot inject markup |
| `test_seed_guard.py` | no (deliberately) | the production seed guard, **run out-of-process** via `subprocess` because `settings` is read at import. Three cases: `app.seed` refuses under `ENV=prod`; the guard **fires before any DB connection** (pointed at an impossible DB, with `connect_timeout` load-bearing because libpq on Windows hangs rather than refusing on `127.0.0.1:1`); `app.seed_kb` is *not* blocked in prod |
| `test_mailer.py` | yes | `deliver_once` over the `MailLog` dedupe store: first send → SENT, same key → the same row and no second delivery, `suppress=True` records without sending, a driver failure is recorded not raised |
| `test_auth_rbac.py` | yes | the security spine end to end: login sets `reep_session` and returns the role; wrong password 401; `/auth/me` reflects the session; a student reads their own dashboard; **a student is blocked from mentor and director areas; a mentor is blocked from director-only**; a director reads the overview; unauthenticated is rejected; `/resume/generate` respects the egress gate |
| `test_conversations.py` | yes | **Assistant V2 Phase A — conversation ownership.** The chat body carries *only* a message; history and delete declare **no id parameter**; two users get different conversation ids; a stray conversation id cannot reach another user's thread; the chat→history round trip; delete starts a fresh thread; `assert_owner` rejects a non-owner and an unknown id, and 404s after soft delete; the greeting survives a failed first turn, is not duplicated, and is per-conversation not per-message; **only one active conversation per owner**. `llm_config` and `complete_chat` are monkeypatched so the flow is offline and deterministic |
| `test_feedback.py` | yes | `POST /api/agent/feedback`: auth required; own-run feedback stored; another user's run, or a nonexistent one, is **404 either way — no existence leak**; a re-vote **upserts, never duplicates**; the note is PII-redacted before storage. Plus pure `redact_pii` cases |
| `test_knowledge.py` | yes | the KB retrieval contract: a natural-language question lands on the right approved chunk; an off-topic query returns nothing or a strictly weaker match; **only APPROVED documents surface — a DRAFT is invisible** (`:77`); an empty query returns empty; a semantic query with **no shared tokens** still lands correctly (the pgvector branch); the vector distance floor preserves the honest fallback (`:141`); `GET /api/agent/knowledge/search` is STUDENT-only (mentor 403, unauth rejected) |
| `test_orchestrator.py` | mixed | intent routing is **pure** (parametrised, no DB). DB-backed: readiness is deterministic with a score and a weakest factor; gaps and actions come from the completion-gaps tool; policy answers are grounded and cite a `policy` source; an unanswerable policy question yields the honest fallback, **never a guess**; a student-data intent is refused for a non-student; `/api/agent/ask` returns structured readiness, cites `policy` with no student record, and requires auth |
| `test_assistant_eval.py` | yes | the **golden-set regression gate**. Every case in `app.eval.golden.GOLDEN` runs through the real orchestrator over the seeded DB, asserting routed intent, grounding signal and cited source type. `llm_config` is stubbed to `None` so the deterministic tool result must be the source of truth. Plus a non-DB test that the golden set is reasonably sized |
| `test_metrics.py` | yes | `GET /api/agent/metrics`: auth required, STUDENT 403, DIRECTOR gets a stable payload shape, and a resolved-versus-refused run actually moves the resolution and refusal rates — matched against a direct count over the column |
| `test_retention.py` | yes | the three-stage lifecycle: past-`retention_until` → soft delete; long-soft-deleted → hard delete with messages; an aged `AgentRun` has its free text replaced while **every metrics field survives**; both jobs idempotent. *(Nothing in production calls these — §5.4.)* |
| `test_voice.py` | mostly | `POST /api/voice/consent` (STUDENT-only, flips `consent_state`, auth required) and `POST /api/voice/transcript` (final-only, dedup, unknown conversation 404, worker-secret enforcement, **fails closed under `ENV=prod`**, oversized text rejected, refuses a cleared conversation). Also — importantly — `/ready` reports dependencies separately, `/health` touches none, `DATABASE_URL` **preserves `sslmode`**, and a `draining: true` heartbeat withdraws readiness immediately |
| `test_voice_gates.py` | yes | `/api/voice/status` and `/api/voice/token`, which "had ZERO tests" and are the security spine of voice. Status names the missing LiveKit config or missing Groq key, reports a stale worker offline, accepts a beat just inside `HEARTBEAT_FRESH_SECONDS`, and the maintenance message overrides a perfectly healthy stack. Token grants **only the caller's own room**, dispatches the named agent `reep-voice`, **ignores a client-supplied conversation id**, mints a fresh room per call, is STUDENT-only, requires auth. And the agreement test: **status available ⟺ token succeeds** (`:323`). Heartbeat upserts rather than accumulating, flips readiness on, rejects a wrong secret and a blank worker id |
| `test_voice_transcript.py` | yes | ingest semantics: voice turns join the same conversation the text chat reads (**one memory**); a voice turn counts as activity; dedup is scoped per conversation; a NULL `provider_turn_id` never dedups; a re-emitted turn **keeps the first text**; an interim turn for an unknown conversation is a quiet no-op; an unknown speaker and a blank conversation id are rejected |
| `test_voice_worker_core.py` | no | unit tests for `_extract_turn` and `_resolve_conversation_id` — "everything that ever gets written as a student's spoken turn passes through them." Heavy imports are `MagicMock`-stubbed and **the environment is snapshotted and restored around the import**, because importing runs `_load_env_file()` which `setdefault`s the real `.env` into `os.environ` and would leak credentials into the whole pytest session. Also pins the `AgentServer` drain and prefork bounds and the exact greeting string |
| `test_voice_worker_source.py` | no | **Rule 1 asserted against the SOURCE TEXT**, not by import: the worker has no database access, calls only the two worker endpoints, builds its system prompt from the constant verbatim, and its agent name matches the server's `VOICE_AGENT_NAME` (`:99`). The rationale is the point — "a worker that had quietly grown a `SELECT` would pass every behavioural test while streaming a student's record to a third party" |

### 11.4 CI, step by step (`.github/workflows/ci.yml`)

Triggers: push to `main`, every pull request, manual dispatch. Concurrency group
`ci-${{ github.ref }}` with `cancel-in-progress: true`.

**Job 1 — `api` (FastAPI + Postgres), ubuntu-latest**

1. A service container `postgres` — **`image: postgres:17`** (`ci.yml:26`), `POSTGRES_USER=reep`,
   `POSTGRES_PASSWORD=reep_dev_password`, `POSTGRES_DB=reep_py`, published `5433:5432`, with a
   `pg_isready -U reep -d reep_py` healthcheck ("Without the healthcheck the job races the
   container and fails on a connection error that looks like a test bug").
2. Job env: `DATABASE_URL=postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_py`,
   `AUTH_SECRET=ci-secret-…`, `ENV=dev`, **`REEP_REQUIRE_DB: "1"`** (`ci.yml:46`).
3. `actions/checkout@v4`; `actions/setup-python@v5` at **3.14** (`ci.yml:53`) with a pip cache
   keyed on `requirements.txt` plus `requirements-dev.txt`.
4. `pip install -r requirements-dev.txt` — runtime plus pytest. (The image installs
   `requirements.txt` alone; that split is the point.)
5. `python -m alembic upgrade head` (`ci.yml:70`)
6. `python -m app.seed` (`ci.yml:74`)
7. `python -m pytest -q` (`ci.yml:78`)

**Job 2 — `worker-imports` (dependency completeness), Python 3.12** (`ci.yml:89`)

`pip install -r requirements-voice.txt` into a **fresh** environment, then load `voice_agent.py`
via `importlib.util.spec_from_file_location` and execute it, printing `module.VOICE_TTS`. No
LiveKit or Groq credentials needed. This exists to catch the exact incomplete-manifest
`ImportError` described in §10.1.

**Job 3 — `web` (Angular), Node 22** (`ci.yml:124`)

`npm ci` → `npx tsc --noEmit -p tsconfig.app.json` → `npx ng test --watch=false` →
`npx ng build`. Two comments explain why the last two are gates at all: the `ng new` scaffold spec
asserted a heading reading "Hello, web" against a template that is just a router outlet, so
`ng test` failed on a fresh checkout and **"a permanently-red suite is worse than none"**; and
`ng build` used to blow the bundle budget (1.23 MB against a 1 MB cap) because every route was
statically imported — routes are lazy now (~142 kB initial) and the budget sits close enough that
**one `component:` slipped back into `app.routes.ts` fails CI**.

### 11.5 The confirmed CI defect, and its one-line fix

**`ci.yml:26` provisions `postgres:17` — stock.** Step 5 runs `python -m alembic upgrade head`, and
`migrations/versions/b7e2f4a19c33_kb_embedding_pgvector.py:37` executes:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

`IF NOT EXISTS` suppresses *"extension already exists"*. It does **not** help when the extension is
not installed on the server at all — stock `postgres:17` has no `vector.control`, so the statement
raises. The migration's own docstring says it "Requires the `pgvector/pgvector:pg17` docker image",
which is what `docker-compose.yml:7` uses and what `docker/initdb/01-create-reep-py.sh:27` relies
on. **CI is the one place that was not updated when pgvector landed** (commit `87d3981`).

Consequence: the *Apply migrations* step fails, so *Seed*, *pytest*, and **every DB-backed test
behind them never run** — with `REEP_REQUIRE_DB: "1"` set specifically so that skipped DB tests are
a hard failure.

**Fix — one line:**

```diff
       postgres:
-        image: postgres:17
+        image: pgvector/pgvector:pg17
```

The service's `POSTGRES_USER: reep` is the initdb-created superuser, so `CREATE EXTENSION`
succeeds once the image carries `vector.control`. CI needs no initdb mount because it sets
`POSTGRES_DB: reep_py` directly.

### 11.6 Where coverage is genuinely thin

1. **The live student-facing feature has ZERO backend tests.**
   `grep -rl interview apps/api-py/tests/` returns **nothing**.
   [app/routers/interview.py](../../apps/api-py/app/routers/interview.py) (333 lines) and
   [app/interview_relay.py](../../apps/api-py/app/interview_relay.py) are untested: not the
   accept-then-close ordering, not the Origin check (4003), not the non-STUDENT refusal (1008),
   not `_LIMITER` (1013), not the `IntegrityError`-is-a-no-op dedup path in `_make_turn_writer`,
   not `realtime_url` composition, not one close code. Meanwhile **5 of the 18 test modules cover
   the LiveKit path that no UI calls** — `test_voice.py`, `test_voice_gates.py`,
   `test_voice_transcript.py`, `test_voice_worker_core.py`, `test_voice_worker_source.py`. This is
   the single largest gap in the suite: **28% of the suite guards the rollback path and 0% guards
   the live one.**
2. **The upload download defect is untested** — §12.5; nothing exercises
   `routers/student.py:1391-1412`.
3. **No test covers the lifespan warning branch** — there is no `caplog` anywhere in `tests/`, and
   nothing sets `ENV=prod` at app construction.
4. **`llm_config()`'s tier-1 skip** — a remote base plus model with a blank key falling through to
   auto-select ([llm.py:88](../../apps/api-py/app/ai/llm.py#L88)) — has **no test**, and while the
   docstring at [`:80-81`](../../apps/api-py/app/ai/llm.py#L80-L81) states the condition ("key
   optional for a local model"), **nothing anywhere states the consequence**: the explicit config
   an operator set is discarded rather than refused.
5. **`app/retention.py` is tested but never scheduled**; `Alert` evaluation has no producer at all.
   Green tests over code nothing runs.
6. **The frontend gate is a compile-plus-budget gate primarily.** `ng test` covers only the two
   spec files in §3.11.

---

## 12. The runbooks

Symptom → diagnosis → fix. Each one is a real failure this stack has produced.

### 12.1 The voice call sounded fine but saved nothing

**Symptom.** The conversation was perfect in the room; the database is empty.

This is the worst failure mode in the stack because it is **silent by design**: transcript POSTs
are fire-and-forget so a bad write can never kill a live call (`AGENTS.md:52`).

**Diagnose.** After a test call:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

Read it by channel: `voice` is the LiveKit worker; `interview` is the realtime relay; other
channels are the text agent. No row, or a stale `max(created_at)`, means turns are being dropped.

**Fix — for `voice`, in order of likelihood** (`AGENTS.md:58-63`):

1. **`VOICE_WORKER_SECRET` differs between the API and the worker** → every POST 401s. The worker
   still connects to LiveKit and answers normally, so nothing looks wrong from outside. Compare
   the value in `apps/api-py/.env` against the worker's environment.
2. **`REEP_API_URL` is wrong** — usually `localhost` from inside a container — so the POSTs never
   arrive. In compose it must be `http://api:3300`.

Both now surface in the worker log as `ERROR POST /api/voice/transcript -> HTTP 401: …`, **with
the status code**. They used to be one WARNING folding every cause together. The grep is
`grep -- '-> HTTP' worker.log`.

**Fix — for `interview`.** The relay is fire-and-forget for the same reason, so the same silence
applies. The cause is in the **API** log with its exception — grep for `Dropped interview turn`
(`AGENTS.md:69`, `docs/interview-assistant.md:93-95`). Note that `_make_turn_writer`
([interview.py:146-189](../../apps/api-py/app/routers/interview.py#L146-L189)) deliberately
swallows **only** `IntegrityError` ([`:178-185`](../../apps/api-py/app/routers/interview.py#L178-L185)),
which is the idempotent dedup no-op, not a failure. Its docstring says why it catches nothing
else: the relay logs the cause against the connection id, "so catching here would only lose the
one identifier that makes the line diagnosable."

### 12.2 The assistant cites nothing, or falls back to "no approved answer"

**Symptom.** Grounded answers come back as the honest refusal, or citations are absent.

**Diagnose, in this order:**

1. **Is the KB seeded at all?** `select count(*) from knowledge_chunks;` and
   `select title, status from knowledge_documents;`. Retrieval surfaces **only `APPROVED`**
   documents whose audience admits the caller
   ([app/knowledge.py:6-8](../../apps/api-py/app/knowledge.py#L6-L8), `:91`). A DRAFT is
   invisible — pinned by `tests/test_knowledge.py:77`.
2. **On a production host, was `seed_kb` ever run?** `python -m app.seed` refuses under `ENV=prod`,
   and it is the dev seed that carries the KB (`seed.py:557`). The production-safe seed is
   `python -m app.seed_kb`.
3. **Is an embedder configured?** No `EMBEDDING_*` and no `MISTRAL_API_KEY` ⇒
   `embedder_configured()` is false ⇒ retrieval runs on **Postgres full-text alone**. The KB still
   works; semantic questions with no shared tokens stop landing.
4. **Are the vectors backfilled?**
   `select count(*) from knowledge_chunks where embedding is not null;`. The vector branch filters
   on `embedding is not null` ([knowledge.py:184](../../apps/api-py/app/knowledge.py#L184)).
   Re-run `python -m app.seed_kb`, which calls `reembed_all` when a provider is configured.
5. **The distance floor.** A genuinely off-topic query is *supposed* to return nothing — that is
   the calibrated relevance floor preserving the honest fallback
   ([knowledge.py:46-55](../../apps/api-py/app/knowledge.py#L46-L55) explains it,
   `_MAX_VEC_DISTANCE` is `:56`; pinned by
   `tests/test_knowledge.py:141`). Getting the fallback on an off-topic question is correct
   behaviour, not a fault.

**Context that saves an hour.** The *text* assistant this runbook describes
(`POST /api/agent/ask`, `/chat`) **has no UI caller** as of 2026-08
([agent.py:17-25](../../apps/api-py/app/routers/agent.py#L17-L25)). If a student reports "the
assistant isn't citing anything", they are describing the **interview relay**, which is not
grounded at all by design — `interview_relay.py` imports no ORM model, no `assistant_tools`, no
`knowledge` (`docs/interview-assistant.md:63-66`). Go to §12.8 instead.

### 12.3 "Start voice" does nothing — 409 versus 503

**Symptom.** The student presses the button and gets an error, or the button never enables.

**Diagnose.** `GET /api/voice/status` (STUDENT-authenticated) is the single source of truth;
`/token` re-computes the same thing
([voice.py:188-210](../../apps/api-py/app/routers/voice.py#L188-L210), `:261-268`), and
`tests/test_voice_gates.py:323` pins that the two agree. The status code from `/token` tells you
which half failed:

| Code | Meaning | Fix |
|---|---|---|
| **409 Conflict** | The provider **is** configured and there is no maintenance message — but **no worker is heartbeating**. | Start the voice worker: `.venv-voice/Scripts/python voice_agent.py dev`. `worker_healthy` needs a heartbeat inside `HEARTBEAT_FRESH_SECONDS = 30` ([voice.py:43](../../apps/api-py/app/routers/voice.py#L43), `:174-175`); the worker beats every `VOICE_HEARTBEAT_INTERVAL_SECONDS`, default 10. |
| **503 Service Unavailable** | The provider is **not** configured, **or** a maintenance message is set. | Set all three `LIVEKIT_*` (`livekit_ready`, [config.py:267-269](../../apps/api-py/app/config.py#L267-L269)) **and** `GROQ_API_KEY` (`voice_model_key_present`, [config.py:254-265](../../apps/api-py/app/config.py#L254-L265)). Then check `VOICE_MAINTENANCE_MESSAGE` is blank — non-empty overrides a perfectly healthy provider *and* worker. |
| **403** | The caller is not a STUDENT ([voice.py:252-256](../../apps/api-py/app/routers/voice.py#L252-L256)). | — |

**The 409 that is really a secret mismatch.** A worker with the wrong `VOICE_WORKER_SECRET`
connects to LiveKit and looks completely healthy, but its heartbeat POSTs 401 — so
`worker_healthy` stays false forever and `/token` 409s indefinitely.
`docs/deployment-env.md:78-81` calls this "the single most confusing failure mode in the stack;
check it first." A blank secret with `ENV=prod` is a **500** from `require_voice_worker` instead,
with the same downstream effect.

**And the trap that is not an error at all.** `VOICE_AGENT_NAME = "reep-voice"`
([voice.py:58](../../apps/api-py/app/routers/voice.py#L58)) is a *named* agent, which opts out of
automatic dispatch. The API attaches an explicit
`RoomConfiguration(agents=[RoomAgentDispatch(agent_name="reep-voice")])` to every token
([voice.py:305-309](../../apps/api-py/app/routers/voice.py#L305-L309)). Rename it on one side only
and **the token mints, the room opens, the microphone publishes, and no agent ever joins** — with
no error anywhere. Pinned by `tests/test_voice_worker_source.py:99`.

**Before spending any time here:** the LiveKit stack has **no UI caller** (`AGENTS.md:71`). If a
student says "Start voice does nothing", confirm which screen they are on — `/student/assistant`
is the interview relay, and its failures are in §12.8.

### 12.4 The resume says `used_ai=false`

**Symptom.** `/student/resume/generate` returns a resume with `used_ai: false`.

**Diagnosis: this is almost always correct behaviour, not a fault.**
[routers/student.py:956-973](../../apps/api-py/app/routers/student.py#L956-L973):

```python
generated_by, model, used_ai, note = "fallback", None, False, None

cfg = llm_config()
if cfg is not None and student_data_egress_allowed(cfg.base_url):
    ...
    complete_chat(..., carries_student_data=True, max_tokens=1500)
    generated_by, model, used_ai = cfg.provider, cfg.model, True
```

Two independent conditions, and either one alone produces `used_ai=false`:

1. **`cfg is None`** — no LLM provider resolved at all. Check `LLM_BASE_URL` + `LLM_MODEL` +
   `LLM_API_KEY`, or any one of the per-provider keys (`SAKANA` → `GROQ` → `MISTRAL` →
   `OPENROUTER` → `GEMINI` → `COHERE`). **Watch out:** a remote `LLM_BASE_URL` + `LLM_MODEL` with
   a **blank** `LLM_API_KEY` silently skips tier 1 and falls through to auto-select
   ([llm.py:88](../../apps/api-py/app/ai/llm.py#L88)) — the explicit config you set is ignored with
   no message.
2. **The egress gate refused** — `student_data_egress_allowed(cfg.base_url)` is false. A resume
   brief carries the student's name, USN, marks and attendance, so **loopback is always allowed;
   anything else requires `LLM_ALLOW_REMOTE_STUDENT_DATA=true`**
   ([config.py:287-288](../../apps/api-py/app/config.py#L287-L288), `AGENTS.md:81`). When it
   refuses, the resume is composed **deterministically** and the `note` field says so, naming the
   variable.

**Fix, choosing deliberately:**

- **Correct fix** — point `LLM_BASE_URL` at a loopback model, e.g. `http://127.0.0.1:11434/v1` for
  Ollama (`.env.example:25`). The gate then allows it unconditionally.
- **Deliberate fix** — set `LLM_ALLOW_REMOTE_STUDENT_DATA=true`. Understand what you are doing:
  `docs/deployment-env.md:47` — "setting it means a student's name, USN, marks and attendance go to
  that provider" — and `.env.example:43-46` warns that free tiers train on submissions. Use a
  **paid** key if you do this.
- The gate is case-insensitive in practice (`TRUE`, `True`, `" true "` all work) despite comments
  claiming an exact match.

`tests/test_auth_rbac.py:73` (`test_resume_generate_respects_egress_gate`) pins this behaviour, so
a "fix" that removes it fails CI.

### 12.5 An upload succeeds but 500s on download

**Symptom.** A student uploads a file fine; fetching it back returns an unhandled 500.
**CONFIRMED DEFECT.**

**Diagnosis.** `POST /api/student/uploads` stores the client's filename verbatim:

```python
original_name=file.filename or stored_name,          # routers/student.py:1380
```

and the download handler interpolates it straight into a response header:

```python
headers={"Content-Disposition": f'inline; filename="{upload.original_name}"'},   # routers/student.py:1411
```

**Starlette encodes header values as latin-1.** A filename outside that range — Kannada, Hindi, or
an emoji — raises `UnicodeEncodeError` inside `Response.__init__`, **before any handler code can
catch it**, so the request dies as an unhandled 500. The upload itself succeeded; only the
read-back fails. At a Bengaluru college a student naming a file `ಪ್ರಮಾಣಪತ್ರ.pdf` is not
hypothetical.

**Confirm.** Look for `UnicodeEncodeError` in the API log for
`GET /api/student/uploads/{id}/file`, and check
`select original_name from uploads where id = …` for non-ASCII characters.

**Fix.** RFC 6266 encoding: an ASCII-sanitised `filename=` **plus**
`filename*=UTF-8''<percent-encoded>`.

**Worth knowing while you are in there.** The same line was first suspected of being a
header-injection hole — a CRLF in the filename splitting the response. **It is not**, but the
reason matters: Starlette encodes the CRLF into `raw_headers` without complaint, and uvicorn's h11
layer then rejects the response with `LocalProtocolError('Illegal header value')`. **Response
splitting is blocked by a dependency, not by this code** — the defence is borrowed and would
evaporate under a different server. The sibling at `routers/student.py:1059` builds its filename
server-side and is not exposed the same way. **Nothing in `tests/` exercises either path.**

### 12.6 Migrations fail on a fresh clone

**Symptom A — `FATAL: database "reep_py" does not exist`.**

*Diagnosis.* `docker-compose.yml` sets `POSTGRES_DB: reep_dev`, but
[config.py:32](../../apps/api-py/app/config.py#L32) connects to **`reep_py`**.
`docker/initdb/01-create-reep-py.sh` bridges the gap — but `/docker-entrypoint-initdb.d` runs
**only on an empty data directory**. If you already had a `reep_pgdata` volume from before the
initdb script landed, it never ran.

*Fix.* Either `docker compose down -v` (destroys data) or run the two statements by hand once:

```bash
docker exec reep-postgres psql -U reep -d reep_dev -c "CREATE DATABASE reep_py"
docker exec reep-postgres psql -U reep -d reep_py  -c "CREATE EXTENSION IF NOT EXISTS vector"
```

**Symptom B — `b7e2f4a19c33` fails: `could not open extension control file ".../vector.control"`.**

*Diagnosis.* The server is stock `postgres:17`, not `pgvector/pgvector:pg17`.
`CREATE EXTENSION IF NOT EXISTS vector` (migration line 37) cannot install what the image does not
ship. **This is exactly the CI defect in §11.5** — if you see it locally, you are running the wrong
image.

*Fix.* `image: pgvector/pgvector:pg17` in `docker-compose.yml` (already correct there) — and in
`.github/workflows/ci.yml:26` (not yet).

**Symptom C — `permission denied to create extension "vector"`.**

*Diagnosis.* `vector.control` is not marked `trusted = true`, so `CREATE EXTENSION` needs
**superuser** — which is why the initdb script, running as `POSTGRES_USER`, creates it, and why
"the KB migration cannot create it itself when the app connects as an unprivileged role"
(`01-create-reep-py.sh:10-13`). (Lines `:15-18` are the *different* empty-data-directory caveat —
Symptom A.)

*Fix.* Create the extension out-of-band as a superuser once; the migration's `IF NOT EXISTS` then
passes.

**Symptom D — `type "x" already exists` during autogenerate.**

*Diagnosis.* The three Alembic enum gotchas (`AGENTS.md:90`, §2.11): (a) adding an enum **column**
to an existing table does not auto-`CREATE TYPE` — create it first; (b) a **new table** reusing an
**existing** enum must use `postgresql.ENUM(..., name='x', create_type=False)` — autogenerate emits
a bare `sa.Enum` that errors, and it must be **hand-fixed**; (c) two columns sharing one enum reuse
a single `Enum` instance.

**Symptom E — the wrong `.env` is being read.**
[config.py:13-17](../../apps/api-py/app/config.py#L13-L17) pins `_ENV_FILE` to `apps/api-py/.env`
precisely because a bare `".env"` resolves against the process CWD, which from the repo root was
the old Next.js/Prisma `.env` whose `postgresql://…?schema=public` URL selects psycopg2 (not
installed). Alembic reads the same settings (`migrations/env.py` → `settings.sqlalchemy_url`), so
run migrations **from `apps/api-py`**.

**Symptom F — the app dies at import with a `ValidationError`.**

A blank line like `LLM_TIMEOUT_MS=` used to raise inside `Settings()`, which runs **at import,
before uvicorn binds a socket** — the whole dashboard died at boot on a blank line in a file four
processes share. [config.py:151-183](../../apps/api-py/app/config.py#L151-L183) fixed that for the
numeric fields. But `INTERVIEW_MAX_SECONDS=0`, or a threshold outside `0.0–1.0`, is still a
deliberate hard failure at startup ([config.py:185-212](../../apps/api-py/app/config.py#L185-L212)),
and that is the correct behaviour. And remember `extra="ignore"`
([config.py:30](../../apps/api-py/app/config.py#L30)): a **typo'd variable name produces no error
at all** — it is simply discarded.

### 12.7 The Windows `uvicorn --reload` stale-worker trap

**Symptom.** You edit a backend file, the reloader announces a restart, and the API keeps serving
the **old** code: your new endpoint 404s, your changed logic does not run, log lines you just added
never appear. Restarting the terminal does not help, because a detached worker still holds port
3300.

**Diagnosis.** `AGENTS.md:22`: *"`uvicorn --reload` has wedged a stale worker here."*
[apps/api-py/Dockerfile:65](../../apps/api-py/Dockerfile#L65) repeats it as a rule for the image:
*"NEVER `--reload` (AGENTS.md: it wedges a stale worker on this platform)."* The reloader's child
process survives the parent on
Windows and keeps the listening socket.

**Fix — kill the port, then restart without `--reload`.**

PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 3300 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }

cd apps\api-py
.venv\Scripts\python -m uvicorn app.main:app --port 3300
```

POSIX equivalent:

```bash
lsof -ti:3300 | xargs -r kill -9
.venv/bin/python -m uvicorn app.main:app --port 3300
```

**Confirm you are on the new process:** `curl http://127.0.0.1:3300/health` →
`{"status":"ok","service":"reep-api-py"}`
([health.py:27-30](../../apps/api-py/app/routers/health.py#L27-L30) — dependency-free liveness),
then `curl http://127.0.0.1:3300/ready` for the per-dependency breakdown
([health.py:33-66](../../apps/api-py/app/routers/health.py#L33-L66): `database` is a hard
dependency and 503s; `voice` is reported but never fails the probe).

**Related trap while debugging.** `--log-level debug` is a documented troubleshooting step, and
[app/main.py:49-61](../../apps/api-py/app/main.py#L49-L61) pins
`logging.getLogger("websockets").setLevel(logging.INFO)` as a **hard floor**, because at DEBUG that
library prints the outbound handshake header by header, unredacted — and one of those headers is
`Authorization: Bearer <OPENAI_API_KEY>` on the interview relay's upstream socket. **Do not undo
that floor to chase a WebSocket bug.**

### 12.8 The mock interview will not start, or drops — reading the close codes

From [app/interview_relay.py:283-291](../../apps/api-py/app/interview_relay.py#L283-L291),
[routers/interview.py:69](../../apps/api-py/app/routers/interview.py#L69), and the client map
`CLOSE_MESSAGES` in `apps/web/src/app/core/interview.service.ts`. **Adding a close code on the
server means adding it to that map** — an unmapped code degrades to "closed unexpectedly"
(`docs/interview-assistant.md:120-124`).

| Code | Cause | Operator action |
|---|---|---|
| 1000 | interview complete | — |
| 1001 / 1012 | server restarting (1012 is uvicorn's own) | — |
| **1006** | handshake refused **before** accept, or the network dropped — no code, no reason available | Check the API is on 3300 and that `/api/interview/status` answers |
| 1008 | not signed in, **or** not a STUDENT ([interview.py:64-69](../../apps/api-py/app/routers/interview.py#L64-L69), `:242-254`) | Deliberately shared: both are "you are not allowed here" |
| 1011 | internal error | The traceback is in the API log |
| 1013 | the per-worker concurrency cap (`_LIMITER`, [interview.py:267-278](../../apps/api-py/app/routers/interview.py#L267-L278)) | Raise `INTERVIEW_MAX_SESSIONS` or add workers |
| **4001** | `OPENAI_API_KEY` unset, or an upstream 401 | Usually a **trailing newline** on a pasted key; `realtime_ready` `.strip()`s ([config.py:214-224](../../apps/api-py/app/config.py#L214-L224)) |
| 4002 | upstream 403/429/5xx or handshake failure — **also** what the relay closes with when `session.updated` shows server VAD is off | The log line names the echoed `turn_detection` |
| **4003** | Origin refused — `WEB_ORIGIN` does not match the browser's origin ([interview.py:211-223](../../apps/api-py/app/routers/interview.py#L211-L223)) | A deployment mistake, not a student one |
| 4008 | the idle cap: no inbound audio for `INTERVIEW_IDLE_SECONDS` | — |
| 4009 | the hard cap: `INTERVIEW_MAX_SECONDS` reached | — |

**"Connects, transcribes, plays no sound" is not a close code.** The two API generations name the
audio events differently (`response.audio.delta` versus `response.output_audio.delta`). The relay
matches a *set* of names for exactly this reason; if it recurs, check
`OPENAI_REALTIME_BETA_HEADER` against the model in use (`docs/interview-assistant.md:149-151`).

### 12.9 Login succeeds, then the app behaves as logged-out

**Symptom.** The login form accepts the password and navigates. Then every panel shows a generic
"Could not load…", the sidebar still shows the user's name and role, and there is no way back to
`/login` short of a hard reload. Or: the mock interview closes immediately with **4003**.

This is the most likely **first-deploy** failure in the whole stack, and unlike every other
runbook here it has no error message pointing at its cause — because the failure is a *cookie
that is never sent*, and a request with no cookie is indistinguishable from a request whose data
is missing (§5.8 F5).

**Diagnose in this order.** All three causes are configuration, not code.

| Check | What you are looking for | Cause |
|---|---|---|
| Browser devtools → Network → any `/api/...` request → **Request Headers** | is `Cookie: reep_session=…` present? | absent ⇒ one of the two below |
| Browser devtools → Application → Cookies | is `reep_session` **stored at all** after login? | stored but not sent ⇒ origin/`SameSite`; not stored ⇒ `Secure` without TLS |
| API log at login | a CORS rejection, or nothing at all | `WEB_ORIGIN` mismatch |

**Cause 1 — `WEB_ORIGIN` does not match the browser's origin.** The value feeds two independent
gates: the CORS allow-list at [main.py:91-97](../../apps/api-py/app/main.py#L91-L97) (with
`allow_credentials=True`, so it can never be `*`) **and** the WebSocket Origin check at
[interview.py:211-223](../../apps/api-py/app/routers/interview.py#L211-L223), which closes
**4003**. A scheme or port mismatch counts — `http://localhost:4200` and `http://127.0.0.1:4200`
are different origins, and so are `http://` and `https://` on the same host.
*Fix:* set `WEB_ORIGIN` to the **exact** origin the browser shows in the address bar, then
restart the API — `settings` is an import-time singleton ([config.py:327](../../apps/api-py/app/config.py#L327)),
so editing `.env` alone changes nothing.

**Cause 2 — `ENV=prod` with no TLS in front.** `ENV=prod` marks the session cookie `Secure`, and
a browser silently drops a `Secure` cookie arriving over plain `http://`. Nothing logs it; the
`Set-Cookie` header is present in the response and the cookie simply does not exist afterwards.
This is easy to hit because §9.5's first open question is unanswered — **there is no Dockerfile
for `apps/web` and no reverse-proxy service in `docker-compose.prod.yml`**, so nothing in the repo
terminates TLS for you.
*Fix:* terminate TLS in front of the API (the image's CMD already passes `--proxy-headers` for
exactly this), or run with `ENV=dev` while you are on plain HTTP — accepting that `ENV=dev` also
opens `require_voice_worker` and un-blocks `python -m app.seed` (§8.1).

**Cause 3 — the cookie simply expired, and there is no route back.** Not a misconfiguration; a
known gap, described in full at §5.8 F5. `authGuard` sits on the `''` parent route and Angular
runs `canActivate` only on *newly activated* routes, so navigating within `/student/*` never
re-runs it. There is no `HttpInterceptorFn`. The shell keeps rendering from a stale `session()`
while every panel independently 401s into its own error state.
*Fix for the user:* hard reload — the guard runs, `auth.refresh()` fails, and it redirects.
*Fix for the repo:* one interceptor mapping 401 → `auth.logout()` + `router.navigate(['/login'])`.
Nothing in this repo does it today.

> **Why this reads as a data problem.** No data-loading component distinguishes 401 from any other
> failure (§4.4), so an authentication fault is reported to the student in the vocabulary of a
> broken endpoint. If several unrelated panels fail at once, suspect the cookie before you suspect
> the API.

---

## 13. Closing the book

### 13.1 Reading order for someone new

The book is written to be read in order, but if you have a week and a job to do, this is the path:

| Day | Read | Why |
|---|---|---|
| 1 | [Chapter 1 — The Stack, End to End](01-stack-architecture.md), then **§7 and §12 of this chapter** | Get it running, and know what to do when it breaks. Nothing else matters until the four processes are on your machine. |
| 2 | [Chapter 5 — Auth & RBAC](05-auth-rbac.md) and **§5 of this chapter** | The two inviolable rules are the shape of the whole product. Read them before you read any handler, or you will read handlers as CRUD. |
| 3 | [Chapter 2 — Backend Core](02-backend-core.md) and [Chapter 3 — The Data Model](03-data-model.md) | The vocabulary. §2 of this chapter is the index to both. |
| 4 | [Chapter 6 — The Student API](06-api-student.md) | The largest surface, and the one a student actually touches. |
| 5 | [Chapter 12 — Frontend Architecture](12-frontend-architecture.md), then **§3 of this chapter** | The client's conventions are unenforced, so they must be learned rather than discovered. |
| 6 | Whichever of [8 — AI Layer](08-ai-layer.md), [9 — Conversations & Memory](09-conversations-memory.md), [10 — Vector DB & the KB](10-vector-kb.md), [11 — Voice](11-voice.md) touches your task | These are deep and independent. Read the one you need. |
| any | [Chapter 4 — Migrations](04-migrations.md) before your first schema change; [Chapter 7 — Staff & Machine API](07-api-staff.md) before your first mentor or director endpoint; [Chapter 13](13-frontend-features.md) and [14](14-design-system.md) before your first screen | Just-in-time. |

**Before your first commit**, read **§6** of this chapter — the review checklist — as an author,
not a reviewer.

### 13.2 What this book deliberately does not cover

- **The product's roadmap.** `docs/reep-pod-roadmap.md`, `docs/spec-implementation-plan.json` and
  the UX plans are planning documents. This book describes the code that exists.
- **The design mockups as design.** `docs/design-v2/*.html` are the reference the v2 system was
  ported from; Chapter 14 covers the *system*, not the visual rationale behind it.
- **`apps/interview-realtime/`** beyond one sentence. It is a superseded standalone prototype with
  no auth and no database, kept for reference and marked "do not deploy" in its own `server.py`
  banner.
- **The retired Next.js/NestJS/Prisma stack.** It is gone. Where a decision only makes sense as a
  migration artefact — the camelCase session island, the `VARCHAR` primary keys, the scrypt
  parameters — the book says so and moves on.
- **Third-party APIs.** LiveKit, Groq, OpenAI Realtime, Mistral and pgvector are described only at
  the boundary this code touches. Their own documentation is authoritative for everything else.
- **Anything the council could not verify against a file.** Where a claim could not be grounded, it
  was dropped or flagged — never smoothed over.

### 13.3 The open questions

[FINDINGS.md](FINDINGS.md) tracks everything the council turned up that it could not resolve, in
four groups:

- **Confirmed defects** — the CI pgvector image (§11.5), the non-latin-1 upload filename (§12.5),
  the case-insensitive egress gate (§5.1).
- **Features whose halves do not meet** — alerting has no producer, the mentor and director UIs do
  not exist, `LeaderboardOut` ships peers' internal ids, retention has no scheduler.
- **Design risks that are correct today but structurally fragile** — the voice channel sitting
  outside Rule 1's enforcement mechanism; the role guards not being dependencies.
- **Drift and stale comments**, and **unresolved questions** — how the SPA is served, how traffic
  reaches the API, `KMS/`, the unused `adk.py`/`agents.py` runtime dependencies.

This chapter adds three of its own:

1. **`docker-compose.prod.yml` passes no `OPENAI_API_KEY`** (§8.6), so the current student-facing
   assistant cannot run under the only production compose file in the repo.
2. **`app/identity.py` now contains two functions, not one** (`get_current_session` and
   `get_ws_session`), which FINDINGS is stale about — though its substantive point, that the role
   guards are not FastAPI dependencies, still holds.
3. **`require_voice_worker` compares a shared secret with `!=`**
   ([voice.py:89](../../apps/api-py/app/routers/voice.py#L89)), not `hmac.compare_digest` — which
   the backend uses in exactly one place, `verify_password`
   ([security.py:42](../../apps/api-py/app/security.py#L42)). The practical exposure is small
   (the secret is machine-generated and the attacker must already reach the API), but it is the
   only credential comparison in the tree that is timing-variable, and §5.3 previously cited it as
   *evidence for* the rule it breaks.

### 13.4 Corrections this chapter records against existing documents

| Where | Says | Reality |
|---|---|---|
| `.github/workflows/ci.yml:26` | `postgres:17` | Must be `pgvector/pgvector:pg17` — **CI's migration step is failing** (§11.5) |
| `docker-compose.prod.yml` (`api` env) | — | **Passes no `OPENAI_API_KEY`**, so the current student-facing assistant cannot run under it. Also no `LLM_*`, `EMBEDDING_*` or `VOICE_MAINTENANCE_MESSAGE`. *(New finding.)* |
| `docs/deployment-env.md:120` | worker `stop_grace_period` 300s | `docker-compose.prod.yml:131` sets **960s** with a written derivation. Compose wins. |
| `AGENTS.md:30` | 3.12 because `Requires-Python: <3.15` | That bound admits 3.14. `Dockerfile.voice:3-11` gives the real reasons: a verified SDK contract and `onnxruntime` wheel availability. |
| `AGENTS.md:48` | "`require_*` dependencies in `apps/api-py/app/identity.py`" | The role guards are **not** in `identity.py` and are **not** FastAPI dependencies. They live in `routers/mentor.py`. |
| `AGENTS.md` (Rule 2 preamble) | a DIRECTOR "reads every student's marks, attendance and USN" | USN yes, via `GET /mentor/mentees`. **Marks and attendance are not exposed on the staff surface at all** — no staff router imports `SemesterResult`, `SubjectMark`, `AttendanceRecord`, `AcademicQualification` or `Enrollment`. |
| `AGENTS.md:81` / `tests/test_egress_gate.py:32` | the unlock is the exact string `true` | `.strip().lower() == "true"` — `TRUE`, `True` and `" true "` all open the gate. More permissive than documented, still closed by default. |
| `apps/api-py/app/db.py:3-5` | `Base.metadata.create_all` "is used only by the dev seed" | Nothing calls it. Acting on the sentence bricks the migration chain — a `create_all`-built database has no `alembic_version` row. |
| `app/models/conversation.py:121` | `messages.channel` is `'text' \| 'voice'` | `interview.py:78` writes a third value, `"interview"`. The column is an unconstrained `varchar`, so nothing rejected it. |
| `.env.example:5-6` | run `docker exec … createdb -U reep reep_py` | Superseded by `docker/initdb/` on a **fresh** volume; still exactly right on an existing one. |
| `.env.example` | — | Declares `VOICE_TTS` (which has no `Settings` field) and **omits `EMBEDDING_*` and `UPLOAD_DIR` entirely**. |
| `.env.example:75` / `voice_agent.py:127` versus prod compose | `VOICE_TTS` default `edge` | `docker-compose.prod.yml:121` defaults it to `groq`. |
| `voice_agent.py:38`, `routers/voice.py:150` | heartbeat "every ~15s" | The default is **10** (`voice_agent.py:151`). Comment drift; the 30s freshness window holds either way. |
| `docs/codebase-mahabharath/README.md:31` (repeated at `:55`) | "All 40 revisions" | The tree has **38** — 38 `.py` files under `migrations/versions/`, 38 `downgrade()` functions, zero bare-`pass` downgrades. |
| Chapter 2's banner taxonomy | "the 75-hyphen shape describes the test files" | `app/interview_relay.py` uses that shape seven times in production code. |
| Chapter 8 | "pass `carries_student_data` explicitly at every call site" | True inside `app/ai/`; `agent.py:231` and `:292` omit it at the router boundary. |
| Chapter 12 §9 R5 / §10 | `asReadonly()` has "exactly one hit in the whole app" | **11 hits, 2 files** — `auth.service.ts:26` plus ten in `core/interview.service.ts:856-904`. |
| Chapters 11 §9 / 12 §10 | `CONSENT_KEY_PREFIX = 'reep-voice-consent:'` at `assistant.component.ts:51` | **`'reep-interview-consent:'` at `:77`.** The rationale comment survives at `:66-72`. |
| Chapter 12 §10 | `assistant.component.scss:1-6` is the one `:host` that is not `display: block` | **The exception is gone.** 19 of 19 `:host` rules are `display: block`. |
| Chapter 12 §4 (`:598`) | 11 `withCredentials` call sites | **12** — auth 3 (unchanged: `:37`, `:50`, `:63`), chat-voice 8 (unchanged), plus **one new one**, `interview.service.ts:1040`. Chapter 12's eleven were right when written; only the interview service is new. Two of the 14 raw grep matches are **comments** — `app.config.ts:14` (stale, "the NestJS backend") and `auth.service.ts:7`. |
| Chapter 13 §1 | 113 `block__element` and 28 `block--modifier` names | **99 and 21** — the assistant rewrite accounts for the difference. |
| Chapter 13 §10 | `reep-h6` used once, at `assistant.component.html:55` | **Still undefined, now used twice** — `:67` and `:152`. |

### 13.5 The last word

Four things are worth carrying out of this book.

**The rules are asymmetric.** Rule 1 is enforced by code — `complete_chat` refuses. Rule 2 is
enforced by a function you have to remember to call. That asymmetry is why §5.2 has so many ⚠️
rows and §5.1 has so few, and it is the single most useful thing to know when reviewing a diff:
**on the AI path, trust the code; on the staff path, trust nothing but your own reading.**

**The silent failures are the expensive ones.** A forgotten model import, a `VOICE_WORKER_SECRET`
mismatch, a misspelled env var under `extra="ignore"`, a local `.badge` beating a global one, a
`Provider` `key_attr` typo, a route that stopped being lazy — none of these produce an error. Every
one of them is documented above with the grep that finds it. Learn the greps.

**The comments are the enforcement mechanism.** No linter, no type checker, no formatter, no
`strict`. The past-failure narratives in this codebase are not decoration; they are the only thing
standing between a surprising line and someone "simplifying" it. Read them, and rewrite them when
you change the behaviour they describe.

**Write down what you could not verify.** That is how `FINDINGS.md` exists, how the CI defect was
found, and how this chapter knows that the production compose file cannot run the product's live
feature. A flag is cheap; an invented reassurance is not.

*— End of the REEP Codebase Mahabharath.*
