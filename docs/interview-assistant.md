# The realtime mock interviewer

The student-facing assistant on `/student/assistant`. The browser's microphone is
relayed, inside the API process, to a speech-to-speech model and back.

```
browser  <--WS /api/interview-->  apps/api-py (uvicorn)  <--HTTP/2-->  Bedrock (Nova 2 Sonic)
                                                         --->  nothing (the local engine)
```

WHICH of those two runs is one setting, `INTERVIEW_ENGINE` — see *Engines*
below. Everything else on this page is the same either way: the same socket, the
same event names, the same records, the same consent gate and the same client.

There is **no fifth process**. The relay runs inside the FastAPI app that already
serves the dashboard, so it shares that app's session cookie, its database and
its deployment. (`apps/interview-realtime/`, the superseded standalone prototype
with no authentication and no database, was deleted in 2026-09.)

| file | role |
|---|---|
| `apps/api-py/app/routers/interview.py` | the boundary: auth, STUDENT check, both concurrency caps, **the consent gate**, specialization validation, the conversation and `interview_sessions` row, the turn/report/finalize/heartbeat writers |
| `apps/api-py/app/interview_core.py` | what every engine shares and none owns: the persona, the payload records, the close codes, both concurrency caps, the scorecard parse, the `InterviewEngine` protocol |
| `apps/api-py/app/interview_nova.py` | the default engine: Amazon Nova 2 Sonic over Bedrock's bidirectional stream, signed by an IAM role, no API key anywhere |
| `apps/api-py/app/interview_local.py` | the on-machine engine (`INTERVIEW_ENGINE=local`): faster-whisper hears, Ollama reasons, Piper speaks |
| `apps/api-py/app/interview_matrix.py` | the Specialization Matrix (HR/DM/BA/FA personas, frameworks, sample questions, per-role voices), the phase state machine, `classify_answer` and the per-turn instruction overrides |
| `apps/api-py/app/routers/interview_records.py` | the READ side and consent: the student's own history, the staff views behind rule 2, `GET/POST/DELETE /api/interview/consent` |
| `apps/api-py/app/models/interview.py` | the four tables |
| `apps/api-py/app/interview_audio.py` | the optional on-disk recording store (off by default — see *Audio*) |
| `apps/api-py/app/retention.py` | the orphan sweeper and the 180-day reaper |
| `apps/web/src/app/core/interview.service.ts` | the client: audio graph, uplink, close-code messages, `reep.report` |
| `apps/web/src/app/features/student/interviews/` | the student's own history, transcript and report screens |

## Engines

Three, chosen by `INTERVIEW_ENGINE` and by nothing else. The router constructs
one class, and all three take the same constructor and return the same
`(code, reason)` from `run()`, so the concurrency caps, the recorder, the turn
writer, the finalizer and all three layers of session close never learn which
one spoke. **They also emit the same downstream events** — the OpenAI Realtime
names plus the five `reep.*` controls — which is why the Angular client needs no
branch for the engine.

| `INTERVIEW_ENGINE` | module | upstream | credential | notes |
|---|---|---|---|---|
| `nova` *(default)* | `interview_nova.py` | Bedrock `InvokeModelWithBidirectionalStream`, `amazon.nova-2-sonic-v1:0` | **none** — SigV4 from the task role / instance profile / profile / environment | Nova owns the turn, the engine owns the phase |
| `local` | `interview_local.py` | nothing leaves the machine | none | faster-whisper + Ollama + Piper; rule 1 holds by construction |

The default is the hosted one because the local engine is opt-IN: it needs a
fourth venv, model weights on disk and ideally a GPU, and a deployment that has
set none of that up must not find its interviews silently running on an engine
that cannot start. An unrecognised value falls back to the default **with a
warning** rather than being guessed at — `INTERVIEW_ENGINE=loca` must not
quietly leave the machine.

**A third engine, `openai`, ran this interview until 2026-09.** It relayed the
microphone to `wss://api.openai.com/v1/realtime` on an `OPENAI_API_KEY`, and
`app/interview_relay.py` was deleted when Nova replaced it. Two things survive
it and are worth knowing about:

* **the contracts it happened to hold** moved to `app/interview_core.py`
  unchanged — they were never about OpenAI;
* **the reasoning** is `docs/interview-engine-v3.md`, still the design record
  for the phase machine, the deterministic word gate and "one open question at a
  time". The mechanism died; the argument did not. Read it before changing the
  arc.

### The bar, and where the interview is opened from (2026-09-15)

**The persona is a Tier-1 multinational's campus round.** `_INTERVIEWER_PERSONA`
in `app/interview_core.py` now describes a senior interviewer on the
campus-hiring panel of a top-tier employer running a structured,
competency-based interview: specific, evidence-backed, structured answers
(STAR for behavioural, hypothesis → analysis → recommendation for case and
technical), quantified where a number exists, one brief probe of a generic
answer, calm and courteous throughout. The mechanics every engine and test
relies on are unchanged — one question at a time, no interrupting, one
sentence of micro-feedback, the blindness paragraph verbatim — and it is still
one string imported by both engines, never copied. The OPENING directive in
`app/interview_matrix.py` names the panel in the same breath as the persona.

**The room is a shared component, and the orb opens it.**
`apps/web/src/app/shared/interview-room/` is the stage (the design system's
dark stage: the interviewer's briefing card, the orb, the status pill, the
live caption, the four-stage stepper, the clock, the mic meter), the round
picker, the consent dialog, the transcript and the report. `/student/assistant`
renders it in `page` mode under its heading and the saved conversation; the
floating dock in the shell (`layout/agent-dock.component.ts`) renders it in
`dock` mode as the "Mock interview" tab beside the "Ask REEP" tab (the typed
agent, itself now `shared/agent-chat/`), so an interview can be started from
any screen. Both tabs are `@defer`red: the shell is in the initial bundle and
the room pulls the audio pipeline and the visualizer behind it. The room
mirrors a three-word state and its clock into `AgentDockService` so the orb
can pulse and carry "Live · 04:12" without importing `InterviewService`; the
room is the only writer and clears it in `ngOnDestroy`, which is also where the
microphone is released — so closing the dock over a live interview asks first.

### The Main Admin's rehearsal (2026-09-16)

The office can sit the interview from the dock's "Mock interview" tab to hear
the interviewer it deploys, and **the server stores nothing for it**. The gate
is `_is_rehearsal` in `app/routers/interview.py` (role ADMIN only); the
mechanism is that `_open_records` — the one function that writes the
conversation, the `interview_sessions` row and the consent check — is never
called, and the engine is constructed through `_run_relay` with every writer
`None`, no recorder, the default policy with both storage scopes off, and no
`interview_session_id` for the backstop to close. The scorecard still arrives
on the socket and is shown once; the room says on screen that nothing is
saved and asks for no consent. The cookie, the Origin check, engine readiness
and both concurrency caps apply unchanged. `GET /api/interview/status` carries
`rehearsal: true` for that account so the client can tell the two apart; a
MENTOR is still refused with 1008.

### Nova 2 Sonic — what is different, and what is not

**Not different:** the persona (imported verbatim from `interview_core.py`, so
two students are assessed against the same words), the Specialization Matrix,
`classify_answer`'s word gate, the phase thresholds, `REPORT_DIRECTIVE`, the
four tables, consent, the recorder, and every close code.

**Different, and deliberately:**

* **Nova owns the turn.** The v3 relay set `turn_detection.create_response:
  false` and issued exactly one `response.create` from one call site. Nova 2
  Sonic has no equivalent switch — its own endpointing and barge-in *are* the
  product — so the engine steers the arc instead of driving it. The phase
  machine still ticks on accepted answers only, for the record and the
  browser's stepper, and the model is steered in two ways. The **question
  phases are briefed once at the handshake** (`build_arc_briefing` in
  `interview_matrix.py`, appended to the system prompt with `classify_answer`'s
  counting rule in words), because the system prompt is set once — Nova has
  no `session.update` — and because a note cannot shape the reply in flight.
  The beats that must land at a fixed point (the invitation to ask questions,
  the verdict, the clock's forced verdict, the scorecard request) travel as a
  **control note**, a cross-modal text input prefixed `[INTERVIEW CONTROL]`
  carrying a fixed directive from `interview_matrix.py`, and are **held while
  a model turn is in flight** (from the student's words to the reply's FINAL
  text) and sent in the gap between turns.
* **The turn boundaries Nova 2 Sonic actually sends** (measured 2026-10-29
  from inside the account). One `completionStart` for the whole session and no
  `completionEnd` between replies. Each reply is SPECULATIVE text (ends
  `PARTIAL_TURN`), audio (ends `END_TURN`), then FINAL text (ends `END_TURN`),
  which arrives about as long after the audio as the speech takes to play. The
  student's words come just before the reply, as USER blocks ending
  `PARTIAL_TURN`, and one answer can be several blocks. So the engine judges an
  answer when the interviewer's reply to it **starts**, and treats the reply's
  FINAL text as its end. Until that date it judged every USER block as an
  answer and waited for a per-reply `completionEnd`: interviews reached the
  wrap-up in under a minute, interviewer turns were not recorded, echo
  detection compared against nothing, and the invitation, the verdict and the
  scorecard were never sent. A reply whose FINAL text never comes is closed
  once its audio has had time to play plus `_FINAL_TEXT_GRACE_S`, or when the
  next reply starts; words no reply follows stop holding notes after
  `_STUDENT_REPLY_WAIT_S` and are saved when the session closes.
* **The browser hears of WRAP_UP with the verdict**, not at the fifth answer.
  The client reads every `response.done` in wrap_up as "the report is being
  written" and stops inviting speech, which would fire on the invitation to ask
  questions itself.
* **A note is a user turn — the reason for all of the above.** Nova delivers
  the student's ASR transcript just before it starts its reply. A note sent
  when that transcript lands barges in on the reply: Nova abandons it, emits its
  interruption marker, the engine forwards `reep.audio.flush`, and the browser
  throws away the queued audio — the voice vanishes mid-word and a different
  question follows. A note held until the reply has finished provokes a second
  question on top of the one just asked. So the phase directives are briefed
  instead of sent, and the stop beats are held and worded to set aside a
  dangling question. The invariant is pinned end to end in
  `tests/test_interview_simulation.py`: nothing goes upstream between the
  student's words and the end of the reply to them. Nova has no client-owned-turn mode and
  documents non-interactive text for the system prompt and pre-audio history
  only, so there is no AWS-side setting that changes this.
* **No clarification turn.** An engine that holds the turn can ask a too-short
  answer for more detail — the relay did, and the local engine still does; here
  the model has already begun replying, and a second directive would produce a
  second question. The briefing tells the model to ask for more on its own;
  the turn is still recorded as `too_short` / `filler`, and it still does not
  advance the arc.
* **The scorecard is a tool call.** Nova speaks everything it generates, so the
  relay's text-only second response would have read the JSON aloud.
  The session declares one tool, `submit_scorecard`; the closing control note
  asks for it; the arguments are parsed by the relay's own `_parse_report` and
  are never spoken and never written into the chat history. A model that reads
  the JSON aloud anyway is salvaged rather than lost.
* **The 8-minute wall.** A Nova bidirectional stream is closed by the service
  after 8 minutes, which is shorter than `INTERVIEW_MAX_SECONDS` (900). The
  engine therefore treats `NOVA_SONIC_CONNECTION_SECONDS` (minus a margin) as
  the real cap, reports THAT as `session_max_seconds` in `reep.ready` so the
  client's countdown and two-minute warning are honest, and forces the wrap-up
  90 s before it so the verdict and the scorecard both fit.
* **Voices.** Each matrix row carries `nova_voice`: HR speaks as `kiara` and BA
  as `arjun` (English, India), DM as `tiffany` and FA as `matthew` (English,
  US). A casting decision, not a translation — the row used to carry an OpenAI
  `voice` as well, and the two vocabularies shared no names at all. An unknown
  voice falls back with a log line, because Nova answers a bad `voiceId` with a
  ValidationException *at the handshake* — an interview that never starts and
  says nothing about why.

Rule 1 is unchanged and unrelaxed: Bedrock is a remote provider, so no student
record enters the session. `app/interview_nova.py` imports no ORM model and no
database code at all, exactly like the relay.

## Endpoints

```
GET    /api/interview/status                          {available, reason, active_sessions, max_sessions}
WS     /api/interview                                 one interview; ?specialization=hr|dm|ba|fa optional

GET    /api/interview/policy                          the college's policy, today's spend, the tracks (B6.1/B5.3/B6.4)
GET    /api/interview/consent                         the caller's own live acknowledgement, or null
POST   /api/interview/consent                         acknowledge; body is {version} ONLY; 422 on a version this server does not know
                                                      (DELETE is GONE — 405 for everyone; see "Consent" below)

GET    /api/interview/sessions                        the caller's own interviews
GET    /api/interview/sessions/{id}                   404 — never 403 — for somebody else's
GET    /api/interview/sessions/{id}/transcript
GET    /api/interview/sessions/{id}/report            without `raw_response`

GET    /api/mentor/students/{sid}/interviews          rule 2, then the row's own subject re-checked
GET    /api/mentor/students/{sid}/interviews/{id}
GET    /api/mentor/students/{sid}/interviews/{id}/transcript
GET    /api/mentor/students/{sid}/interviews/{id}/report     `raw_response` only for the Main Admin
GET    /api/mentor/students/{sid}/interviews/{id}/audio      ADMIN only; ?track=mixed(default)|student|interviewer
```

`GET /status` exists because a **rejected WebSocket handshake reaches the browser
as a bare 1006 with no code and no reason**. It is the only place a student can be
told *why* — not configured, not signed in, not a student. It answers `200` with
`available:false` even for a non-student,
because the client treats any non-2xx as "probe unavailable" and would throw the
explanation away. (It was written against `/api/voice/status`, which raised 403
for a non-student; that endpoint went with the LiveKit stack in 2026-09 and the
convention is this socket's own now.)

It deliberately does **not** report missing consent. The client treats
`available:false` as "do not start", which would hide the very consent panel that
fixes it; the panel is driven by `GET /api/interview/consent` instead.

Every staff endpoint opens with `_assert_can_access_student` imported from
`app/routers/mentor.py` — never a second copy — and then re-checks that the row's
own `student_id` matches the path. A MENTOR with no `Mentor` group sees **nobody**,
by that function, with nothing added here.

## The Specialization Matrix

The student picks a track on the assistant screen; the client sends it as
`?specialization=` on the socket (a query param because a browser WebSocket
cannot set headers — safe precisely because it is a UI choice, not a student
record). `app/interview_matrix.py` owns the four rows — AI persona, core
frameworks, the sample question worked in during probing, and **the voice the
role speaks with** — **verbatim from the product spec**, and
`InterviewStateMachine`, which advances the interview through explicit phases
on each *completed student answer*:

```
opening  --1 answer-->  probing  --3-->  deep_dive  --5-->  wrap_up
```

(Any phase can also go straight to `ended`; `wrap_up` is sticky.) *Completed* is
load-bearing and is decided below, not by the model: a barge-in over an
unfinished question, a cough and a two-word answer all fail it. The relay
composes instructions as **base persona verbatim + delivery-style block +
specialization block + phase directive**, sends the opening composition in the
startup `session.update`, and pushes an **instructions-only** `session.update`
on every phase change (the voice is frozen once the model has spoken, so
nothing else may ride that update). The browser learns the phase from
`reep.ready` and `reep.phase` events. **No `?specialization=` runs the generic
interview with the untouched base persona** — and an unknown key is refused
with close 4010 rather than silently downgraded to it.

The arc is shaped like a real interview, not a quiz. **Opening** greets the
student, introduces the interviewer in one sentence, sets expectations (a
focused ~15-minute conversation with honest feedback at the end) and asks the
student to introduce themselves and say what drew them to the track — no domain
questions yet. The matrix's hard scenario question moved to **probing**, worked
in early and rephrased naturally rather than recited cold. And `wrap_up` is a
**two-beat close**: the tick into it first asks *"any questions for us about
the role or the company?"* (the `invite_questions` turn directive), the
student's reply — any reply, never `classify_answer`'d, because "no, I'm good"
is filler words to the word gate — earns the spoken verdict, and only the
verdict's `response.done` requests the scorecard. The forced/clock path skips
the beat and goes straight to the verdict.

Each track also speaks with its own Realtime voice, frozen onto the session in
the single startup `session.update`:

| track | voice | role it voices |
|---|---|---|
| HR | `coral` | the warm CHRO |
| DM | `marin` | the energetic CMO |
| BA | `cedar` | the measured Director of Analytics |
| FA | `ash` | the authoritative MD/CFO |
| *(generic)* | `OPENAI_REALTIME_VOICE` (default `alloy`) | the base interviewer |

An `OPENAI_REALTIME_VOICE` value outside the known set (`alloy, ash, ballad,
coral, echo, sage, shimmer, verse, marin, cedar`) is logged and falls back to
`alloy` — upstream answers an unknown voice with a *silent* fallback to its own
default, which is an interview that runs fine while sounding nothing like what
was configured.

One asymmetry to know about: the phase tick is gated on a specialization being
present, so **the generic interview never reaches `wrap_up`** and therefore never
produces a scorecard. It runs, it records, and it ends at the cap. That is the
pre-matrix behaviour preserved deliberately — the *create* is unconditional,
only the tick is gated — but it is the first thing to revisit if the generic
track is ever offered to students as a real option.

## The turn protocol — the relay owns the turn (v3)

The session is opened with **`turn_detection.create_response: false`**. Upstream
still runs VAD, still commits the input buffer and still transcribes; it no
longer creates responses. **The relay does**, from exactly one call site
(`_advance_turn`) — and that is what makes *"one open question at a time"* a
property of the call graph rather than a sentence in the persona.

The old behaviour asked the next question at end-of-speech, before the
transcript of the answer had arrived. Two independent upstream subsystems with
no happens-before edge between them meant the phase directive was structurally
one response late, a cough created a question, and after the fifth answer the
model asked a sixth one instead of delivering the verdict. None of that was
fixable in a prompt: instructions are the *content* of a response, and the flag
decides how many responses exist and when.

One turn, after:

```
speech_stopped        -> arm the answer deadline (INTERVIEW_TRANSCRIPTION_TIMEOUT_MS)
committed{item_id}    -> re-key that pending entry, re-arm
exactly one of:
    transcription.completed -> transcript,  status ok
    transcription.failed    -> "",          status failed
    the deadline expires    -> "",          status timeout
drain the queue head  -> ONE response.create for the whole batch, never one per item
```

While the student's answer is transcribing, the relay also forwards
`conversation.item.input_audio_transcription.delta` events to the browser as
`reep.transcript.delta` (`item_id` + `delta`), so the "You" line appears and
revises live instead of popping in whole seconds later. They are **never
persisted and never fed to the arc** — `.completed` remains the single point
where a student turn is recorded or judged — and the beta generation may not
emit them at all, in which case the client simply keeps its completed-only
behaviour.

Between the drain and the create, the relay decides what kind of response to ask
for. The order of the two checks is not interchangeable:

| condition | kind | advances the arc? |
|---|---|---|
| the last question was never finished (barge-in) | `resume` | no |
| the transcript failed or timed out | `unheard` | not until the clarification budget is spent |
| `classify_answer` returns `empty` / `filler` / `too_short` | `clarify` | no |
| otherwise | `next` | **yes** — `student_answered()` ticks the phase machine |

`classify_answer` (`interview_matrix.py`) is a word count against
`INTERVIEW_MIN_ANSWER_WORDS`, deliberately **not** a model call: it runs between
the student finishing and the interviewer replying, so a round trip there would
be latency on every turn of every interview. Clarifications are capped by
`INTERVIEW_MAX_CLARIFICATIONS_PER_QUESTION`; at the cap the relay accepts
whatever it got and moves on, because **a student who genuinely cannot answer
must not be trapped**.

Everything the relay waits for now has a deadline, and that is not decoration:
under `create_response: false` a transcription that never lands is an interview
that never continues, and the idle watchdog does **not** cover it (the browser's
echo-gate keepalive keeps sending frames, so 4008 never fires). A create that is
never acknowledged is retried once and then closes **4011**.

At `wrap_up` the model first invites the student's questions (the beat above),
then speaks its verdict on the reply, and that verdict is persisted as an
interviewer turn — it is part of the interview the student heard. Then the relay
issues **one further, text-only `response.create`** in the same session, which
already holds the whole transcript: a strict-JSON scorecard, parsed defensively,
sent to the browser as `reep.report` and written to `interview_evaluations`.
The scorecard is never spoken (`modalities: ["text"]`, plus a guard that drops
any audio carrying the report's response id) and never appears in the chat
history (`_on_response_done` returns before `_emit_turn` for that id). A student
speaking during it is **ignored** — the one place in this design where that is
the correct action.

Every failure of the report has a status (`unparseable`, `timeout`, `rejected`,
`unavailable`) and **none of them fails the interview**: a row is written in
every case, `reep.report` says `available:false` with the reason, and the socket
closes **1000**. There is deliberately no `4015`: the interview completed; only
the scorecard did not.

## Authentication

The socket uses **REEP's own httpOnly `reep_session` cookie** and the same
`verify_session_token` as every HTTP route — there is no second token scheme.

A browser WebSocket cannot set headers, but it *does* send cookies on a
**same-origin** handshake. `/api` is same-origin by construction:
`apps/web/proxy.conf.json` forwards `/api` to the API with `"ws": true` in dev,
and production serves one origin. The cookie is `SameSite=Lax`, which is what
stops a cross-site page from carrying it onto this handshake at all; the Origin
check in the router is defence in depth on top of that.

**Non-STUDENT callers are refused** (close 1008). Hiding the Start button in the
Angular component is not a gate — a mentor or director holding a valid cookie can
open the socket from devtools, and each open costs a billed upstream session.

The socket is **accepted before any check that can fail**, and every refusal is a
close on the accepted socket. Closing before accept fails the HTTP upgrade, and
the browser then reports 1006 with no code and no reason, so "not signed in"
would be indistinguishable from "the wifi dropped".

## Rule 1 (AGENTS.md): no student record reaches the model

Bedrock is a remote provider, so **nothing personal enters the session**.
This is structural, not a matter of asking the model nicely:

- the only thing the server authors upstream is `_INTERVIEWER_PERSONA`, a fixed
  string with no student data in it;
- the only other thing on the uplink is the student's own microphone;
- the engines import no ORM model, no `assistant_tools`, no `knowledge`, and
  neither does `interview_core.py`;
- the session id, conversation id and user id never leave the process.

The persona **also tells the model it is blind** — the same disclosure
`voice_agent.py`'s `BASE_INSTRUCTIONS` made for the LiveKit worker, which was
removed in 2026-09 along with that file. That sentence
is not redundant with the architecture: a model that is not told it cannot see the
dashboard will invent a CGPA and say it out loud, and the student has no way to
know it was fiction.

**If the interview is ever personalised** (branch, target company, resume text),
that path must go through `complete_chat(..., carries_student_data=True)` in
`app/ai/llm.py` and fall back to the generic persona when the gate refuses — the
same shape as `/student/resume/generate` degrading to `used_ai=false`.

## Persistence — and how to check it

Turns are written through `app/conversations.py` into the **same**
`conversations` / `messages` tables the text agent uses (and the removed LiveKit
worker used — its rows read back unchanged, which is why `Message.channel` is
still a plain String), so
`GET /api/agent/history` returns them unchanged. That contract does not bend —
the four tables below are **in addition**, never instead.

Writes are **fire-and-forget**: a failed write must never end an interview that is
otherwise going fine (the same rule the LiveKit transcript POSTs followed). That buys
the silent failure mode the voice runbook exists to catch, so after a test call:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

An `interview` row that does not grow, or a stale `max(created_at)`, means turns
are being dropped. The cause is in the API log, with its exception — grep for
`Dropped interview turn`. The channel is `interview`, **not** `voice`: both are
spoken, but they are different products with different retention questions, and
folding them together would make this query unable to answer "did the interviewer
save anything" independently of the `voice` rows the removed LiveKit stack left
behind.

Dedup is on `(conversation_id, provider_turn_id)`, with `u:`/`a:` prefixes because
upstream item ids and response ids are separate sequences.

## The interview record — four tables

`app/models/interview.py`. Every vocabulary column is a plain `String`, not a PG
enum, because these vocabularies will move — a fifth specialization is a data
change today, and turning it into a migration would be a regression. The
vocabulary lives in a comment next to the column.

| table | one row per | what it carries that `messages` cannot |
|---|---|---|
| `interview_sessions` | interview | the subject, the track, the terminal status and close code, `answers_accepted`, `turns_emitted`/`turns_persisted`, the relay's `conn_id`, the upstream session id, the consent grant, the retention deadline |
| `interview_turns` | turn | the **phase** it happened in, whether the transcriber actually heard it (`transcription_status`), how the answer gate judged it (`answer_quality`), whether it ticked the arc (`counted_as_answer`), whether an interviewer turn was cut off (`is_partial`) |
| `interview_evaluations` | interview (UNIQUE) | the scorecard: four nullable 0–100 scores, strengths, improvements, a drill, a summary, and `raw_response` for the Main Admin debugging a bad parse |
| `interview_consents` | grant | who agreed, to which version, to which of three scopes, when, from what user agent and a **salted hash** of the address — never the address |

Two things about this record are worth knowing before reading it:

- **A blank `interview_turns.content` is legal and meaningful.** A transcription
  that timed out or failed arrives as `""`, and those are exactly the turns
  `transcription_status` exists to record, so the row is written regardless. Only
  the `messages` row is skipped — an empty chat bubble in
  `GET /api/agent/history` would claim the student said nothing, which is the
  opposite of what happened.
- **The scores are nullable even when `report_status = 'ok'`.** A missing score
  and a zero mean opposite things to whoever reads them, and nothing in this
  stack substitutes one for the other — not the parser, not the writer, not the
  screen.

The two inserts per turn share **one transaction**. A second, independent writer
was considered and rejected: it can partially succeed, which puts the runbook
query above in the position of reporting "saved fine" while the reviewable record
is missing turns.

### A `running` row that is never closed is a record that lies

Three layers close it, in order of how much of the process has to survive:

1. **The relay's own finalizer** — every ordinary exit, including the
   guardrails, a disconnect and a 1011. It is the only layer that knows whether
   the scorecard settled, which is what separates `completed` from `abandoned`.
2. **The router's `finally` backstop** — the relay raising before its own
   finalizer ran, and shutdown cancelling the coroutine. It knows only the close
   code, so it never claims `completed`.
3. **`retention.finalize_orphaned_interviews`** — the process was killed and
   nothing in it ran. Predicate: `status='running' AND heartbeat_at < now -
   INTERVIEW_ORPHAN_GRACE_SECONDS`. Called once at startup from `lifespan`,
   because a worker that just restarted is exactly the process that knows the
   previous one died.

All three are idempotent against each other by one predicate — `AND status =
'running'` — so the loser updates zero rows and says nothing. The heartbeat the
sweeper reads is written by the relay's watchdog once a minute.

### Retention

`INTERVIEW_RETENTION_DAYS` (180) is stamped on the row at open as
`retention_until`, so changing the setting never re-dates an interview a student
was already promised a window for. `retention.purge_expired` then runs the same
two stages conversations already have: past the deadline → soft-delete plus
`redact_pii` over `interview_turns.content` and
`interview_evaluations.raw_response`; soft-deleted past 30 days → hard-delete,
**audio bytes first and rows second**, and a session whose audio could not be
deleted keeps its row so the file stays discoverable.

## Consent

Consent is a **row**, not a `localStorage` key and not
`Conversation.consent_state` (which the student's own "Clear conversation"
destroys — a consent record the subject can delete by accident is not a consent
record).

**THE SCOPES ARE THE COLLEGE'S SINCE B6.1 (2026-09-13), AND THE ROW IS AN
ACKNOWLEDGEMENT.** `interview_policies` holds one row per `(college, course)`;
the two storage scopes are copied onto the grant by `POST /api/interview/consent`,
which now takes the version string and nothing else. `scope_live_ai` is written
true and has no policy column: the interview IS a live AI conversation, so "no"
to it is not an interview with a setting turned off — it is no interview, which
is what not pressing Start already means.

**Three booleans, not one**, because they are three different disclosures and a
reader a year later has to be able to see which of them was in force. What
changed is who states them, not how many there are:

| scope | what the student is agreeing to |
|---|---|
| `scope_live_ai` | their microphone audio is streamed to the interviewer's speech model while the interview runs — the panel names the actual recipient, which the server supplies as `provider` on `GET /api/interview/consent` |
| `scope_store_transcript` | a written transcript and an AI practice score are kept on the college's server, readable by their mentor and the placement director, for 180 days |
| `scope_store_audio` | the audio itself is kept — off by default at all THREE ends (the operator's flag, the college's `store_audio`, this row) |

**`POST` is idempotent when nothing changed**, and that is load-bearing rather
than tidy: the client posts at every Start, and superseding an identical
acknowledgement would stamp `revoked_at` on the row a RUNNING interview is
pinned to — closing it 4014 from a second tab, with a sentence the student did
not earn.

**`DELETE /api/interview/consent` IS GONE and answers 405 for everyone.** What it
withdrew is the college's decision now, so a button offering to overrule it would
be a promise the server does not keep. It was deleted rather than made to 403 —
`app/routers/admin_students.py`'s precedent — because a capability refusal would
mean the endpoint is still there waiting for a grant. Nothing is deleted from the
table; every historical grant stays readable.

`INTERVIEW_CONSENT_VERSION` (`"2026-08"`) is stamped on every grant. Consent is
not retroactive: bump it when the wording changes, and rows carrying the old
string stop counting for the new terms. `POST /api/interview/consent` **422s a
version it does not know**, so a stale cached SPA cannot grant against copy the
student never saw.

**Enforcement, and the ordering it needed.** The socket now requires a live grant
for the current version:

- no live grant → the interview never opens and the socket closes **4013**. The
  check is the first statement of `_open_records`, *before* anything is written,
  so a refusal leaves no conversation and no `interview_sessions` row for the
  sweeper to trip over.
- the grant revoked while an interview is running → the heartbeat notices within
  a minute and the socket closes **4014**. Since B6.1 it asks a SECOND question
  before stopping: does this user still hold a live grant covering every scope
  this interview is running under? If they do, the acknowledgement was replaced
  by an equal-or-wider one and the call continues; if they do not, a scope the
  interview depends on is gone. That is the compatibility board's "a policy
  change stops a running session with 4014 only when it removes a scope",
  expressed as a property of the grant rather than as a second read of the policy
  table on every heartbeat of every live interview
  (`_successor_covers`). The second query runs only when the first said the row
  is gone, so the steady state is still one indexed SELECT a minute.

That gate could only be turned on **after** the browser started posting grants;
enabling it first would have locked every existing student out of the feature on
the deploy that shipped it.

The revocation check is a **poll on the heartbeat, not a push from the DELETE
handler**, and that is deliberate: any registry the DELETE could consult is
per-process, so with N uvicorn workers the revoking request lands on a worker
that is not holding the socket in most cases, and the push would do nothing at
all while looking like it worked. It watches **the exact grant the interview
opened under, by id** — not "is there a live grant for the current version" — so
an operator bumping the version mid-session does not end every interview in
progress with "you withdrew consent".

The two gates fail in **opposite directions, on purpose**. Opening fails
**closed**: an unreachable database refuses the interview, because "we could not
check whether they agreed" and "they agreed" are not the same sentence. The
running check fails **open**: a transient database error must not end a live call
that a real grant authorised.

`interview_sessions.consent_id` pins the exact grant that was live at open, so
*"was this student consented, to what wording, at the time of interview X"* stays
answerable years later — after the grant has been revoked and re-given twice.
Revoking stamps `revoked_at` and never deletes the row.

## Audio — off by default, and "off" is three independent switches

This section replaces an earlier flat *"audio is not recorded"*. Capture now
exists (`app/interview_audio.py`), and a doc that denies a feature that exists is
as much of a trap as a setting that exists and does nothing.

**Nothing is captured unless ALL THREE of these are true**, and none of them is
true in a default deployment:

1. `INTERVIEW_RECORDING_ENABLED=true` in `apps/api-py/.env` (default `false`;
   the CDK stack sets it `true` on the AWS deployment) — the **operator's**;
2. the college's `interview_policies.store_audio` is `true` — the **college's**,
   the "Allow voice recording" box on the Interview records screen's policy
   card, and **unticked until somebody ticks it** (no policy row is seeded, and
   the absence of one is the default);
3. the student holds a live consent grant of the current version whose
   `scope_store_audio` is `true` — the **student's** acknowledgement, which
   since B6.1 is a copy of the college's decision taken at Start, shown to them
   in words that say plainly that staff can listen.

All three gates live inside `recorder_or_reason()` (`recorder_for()` is the
same decision with the reason thrown away) so that *"when does REEP record a
student's voice?"* has one answer in one function. It **fails closed** — an
unreachable database means "do not record", never "record anyway" — and it is
called from `routers/interview.py`, because the engines import no ORM model and
a recording feature is not what that containment gets spent on.

**WHICH GATE CLOSED IS WRITTEN DOWN (2026-09-17).** The four Nones used to be
told apart only by INFO lines in the API log, so the Interview records screen
drew "No audio" beside a grey "Download recording" button for every interview
on a deployment whose operator switch was on and whose college had simply never
ticked the box — and the office reported the recording feature as broken. The
finalizer now writes `interview_sessions.audio_skipped_reason` (`operator_off`,
`policy_off`, `no_consent`, `store_full`, `open_failed`, or `nothing_captured`
for a recorder that closed with nothing), the records API serves it, and the
open record spells it out with a button to the policy card when the fix is the
college's own tick. The Download button is drawn only when there is a file to
download. NULL on a recorded interview and on every row older than the column,
which the screen renders as "no recording was kept" and nothing more — the
gate that closed a year ago is not a thing to guess. The policy card also says
when the operator's switch is off, so "Allow voice recording" is not a box the
office ticks and then waits on.

**THE ROOM RE-ASKS WHEN THE POLICY CHANGED (2026-09-17, the same evening).** A
consent row is a copy of the policy taken when the student pressed "I agree",
and the room used to start straight away whenever it held one — so the Start
line's "recording on/off", read off that row, could disagree with what the
recorder was about to do in BOTH directions: "on" over an interview refused
`policy_off`, and, after the office ticked the box, "off" with `no_consent` on
every existing student while the record claimed they would be shown the terms
again. `shared/interview-room/consent-sync.ts` holds the two decisions as pure
functions with a spec: Start proceeds on the standing row only while the
policy's two storage scopes still equal the row's (the server's `acknowledged`
comparison, made on the client), otherwise the terms are shown again and "I
agree" posts the acknowledgement the server supersedes; and the label reads the
policy and `recording_enabled_on_server` (now on `GET /api/interview/policy`
too), not the row. Judged on the card already loaded, never on a fetch —
`start()` must run inside the click's gesture — and the card is re-read in the
background after each Start.

| | |
|---|---|
| **Format** | PCM16 LE mono 24 kHz wrapped in a RIFF/WAVE header by the stdlib `wave` module — the bytes already crossing the relay, so no encoder, no transcode and no new dependency |
| **Files** | **three per interview**: one per speaker (`student`, `interviewer`) plus a derived `mixed` listening copy. The two per-speaker files are still **the record** — do not "clean them up" as duplicates; the mix is regenerable from them and they are not recoverable from it. This row used to say the two must never be mixed, because before the timeline each file was a speech-ONLY concatenation with the silences squeezed out, so laying them side by side put answers under the wrong questions. `app/interview_audio.py` now pads each track against one monotonic session clock, so both files are session-length and the mix is a sum rather than a guess — accurate to a beat, not a frame (the interviewer's track is *when the model's audio was forwarded*, which can run ahead of the wall clock during a burst) |
| **Where** | a sibling of the uploads root, `<uploads>/../interview-audio`, each file named after the `interview_sessions.id` that owns it — so retention can find it from the primary key alone even if `audio_path` is ever lost. Not `app/document_store.py`: that store decides type by magic bytes and accepts only PDF/PNG/JPEG, and admitting audio would loosen the one control that makes it trustworthy |
| **Cap** | `INTERVIEW_RECORDING_MAX_BYTES` is a hard per-session ceiling on **captured PCM**. At the cap capture **stops**, `interview_sessions.audio_truncated` is set, and the interview continues — a call is never dropped to protect a file, and a truncation is never silent. **Size it against 96,000 B/s, not 48,000**: both tracks are padded to the session's wall clock, so an interview burns two streams whether or not anyone is talking. 128 MB is ~22 min, past the 900 s session cap; budget ~256 MB of *disk* per session, because the derived `mixed` copy is written on top of what survived. This row said "64 MB ≈ 45 min" for a release after the padding landed — arithmetic from the speech-only era, under which the cap bound first and quietly cut the last 3.8 minutes off every full-length interview |
| **Truncation** | three things stop a capture, and the WARNING names which: the byte cap above, a timeline gap longer than an interview can run (a suspended host, not a silence), and the write buffer bound — *"the disk is not keeping up"*, which now means only that. It used to fire on a healthy disk: pending silence was materialised into that buffer, so a 90-second answer left the interviewer owing one 4.3 MB lump and the next question ended the recording. Silence is an integer segment now, materialised in the writer, so the buffer holds real audio only |
| **Retrieval** | `GET /api/mentor/students/{sid}/interviews/{id}/audio?track=mixed\|student\|interviewer`, **defaulting to `mixed`**, behind the **`admin.interview_audio` capability** — the Main Admin by baseline, a MENTOR **only by an explicit grant**, deliberately narrower than every other read in that router — **and** `_assert_can_access_student` **and** a re-check that the row's subject is the student in the path. A faculty account that can read every transcript in the file still gets 403 here until somebody grants it that key; the asymmetry is intended, because a stored voice is an operator's artefact and not placement business. (This row said `_require_developer` and "a DIRECTOR gets 403" until Phase 5: that helper is gone, and so is the role — the gate is the capability.) 404 — never 403 — when nothing was recorded, so a caller cannot tell "not recorded" from "not a real id" |
| **Retention** | the same 180-day clock as the transcript. `purge_expired` deletes the bytes **before** the rows; a session whose audio could not be deleted keeps its row, because an orphaned voice file is undiscoverable and therefore undeletable |

Read **`interview_sessions.audio_recorded`**, never `audio_path IS NOT NULL`. A
NULL path collapses four different facts into one — capture disabled, consent
refused, the write failed, the interview predates capture — and the flag is the
only column that distinguishes "we know nothing was kept" from "we do not know".
`audio_truncated` can be true while `audio_recorded` is false: a capture stopped
before its first flush ended early *and* kept nothing.

Those five `audio_*` columns are written by the Layer 1 finalizer, in the same
UPDATE as the terminal status, and they are the **only** record that a file
exists — retention deletes recordings by reading them. A recording whose row
never received them is a named student's voice on disk that nothing will ever
find.

If you are turning this on: it is a stored voice recording of a named student,
readable by staff. That is a materially different consent and legal posture from
a transcript, which is why the default is off, why the consent scope is separate
and unticked, and why the download needs `admin.interview_audio`, which no faculty account holds by default.

## Configuration

Everything lives in `apps/api-py/.env` — the one file all processes share. See the
`Realtime mock interview` block in `.env.example`. **Readiness is asked of the
engine that is actually running**: on `nova` an unresolvable region is off, and
the local engine needs nothing.
Either way `/status` reports unavailable with a reason and the socket closes
4001. Nothing else in the dashboard is affected.

Nova's own settings, all optional except the region:

| setting | default | what it does |
|---|---|---|
| `NOVA_SONIC_MODEL` | `amazon.nova-2-sonic-v1:0` | the model id. **Not** an inference profile — the bidirectional API takes the bare id, which is why this is separate from `BEDROCK_MODEL` |
| `NOVA_SONIC_REGION` | *(unset)* | falls back to `BEDROCK_REGION`, then `AWS_REGION` / `AWS_DEFAULT_REGION`. The endpoint is composed from it here, so blank is "not configured" rather than "let the SDK decide" |
| `NOVA_SONIC_VOICE` | `matthew` | the voice for the GENERIC interview only; each matrix row casts its own |
| `NOVA_SONIC_ENDPOINTING` | `LOW` | `HIGH`/`MEDIUM`/`LOW` — the pause Nova waits for before it takes the turn (AWS documents 1.5 s / 1.75 s / ~2 s). `LOW` since 2026-09-17: a student gathering an example stops for a breath, `MEDIUM` read that breath as the end of the answer and asked the next question over its second half, and being cut off mid-answer is the most damaging thing a mock interviewer can do |
| `NOVA_SONIC_INPUT_RATE_HZ` | 16000 | the uplink rate. The browser captures 24 kHz and the engine resamples; Nova accepts 8/16/24 kHz and 16 is the documented path |
| `NOVA_SONIC_CONNECTION_SECONDS` | 480 | Bedrock's 8-minute stream limit, as the engine understands it. Lower it if a deployment sees streams cut sooner |

Credentials are **not** configured here: the stream is signed with SigV4 from
the standard AWS chain. Grant the task role
`bedrock:InvokeModelWithBidirectionalStream` on the model and paste nothing.

The v3 engine adds a deadline for every wait, because with the upstream no
longer driving, a stalled dependency becomes an interview that never continues
and reports no fault to anyone:

| setting | default | what it bounds |
|---|---|---|
| `INTERVIEW_TRANSCRIPTION_TIMEOUT_MS` | 8000 | the student's transcript after end-of-speech. On expiry the turn is recorded with an unknown transcript and the next question is asked anyway |
| `INTERVIEW_RESPONSE_CREATE_TIMEOUT_MS` | 10000 | our own `response.create` being acknowledged. Retried once, then 4011 |
| `INTERVIEW_REPORT_TIMEOUT_MS` | 20000 | the scorecard. On expiry the evaluation is persisted as `timeout` and the socket still closes 1000 |
| `INTERVIEW_MIN_ANSWER_WORDS` | 4 | the answer gate. 0 disables it — pre-v3 behaviour, where anything at all counted |
| `INTERVIEW_MAX_CLARIFICATIONS_PER_QUESTION` | 1 | how often one question may be re-asked before the interviewer accepts what it got |
| `INTERVIEW_ORPHAN_GRACE_SECONDS` | 1200 | how long a `running` row may sit with no heartbeat before the sweeper calls it abandoned |
| `INTERVIEW_RETENTION_DAYS` | 180 | how long the whole record — transcript, evaluation, any audio — is kept |
| `INTERVIEW_CONSENT_VERSION` | `2026-08` | the terms; bump it when the wording changes |
| `INTERVIEW_RECORDING_ENABLED` | `false` | audio capture — see *Audio* above |
| `INTERVIEW_RECORDING_MAX_BYTES` | 128000000 | the per-session ceiling on captured PCM — see the *Cap* row above before changing it |
| `INTERVIEW_VAD_SILENCE_DURATION_MS` | 600 | the pause that ends a student turn. At the upper edge of the 400–600 ms natural thinking-pause band; under v3 a split at a pause is merged into one answer, so it no longer risks cutting the candidate off |
| `INTERVIEW_VOICE_SPEED` | 1.0 | the interviewer's speaking rate (GA only, `audio.output.speed`, 0.25–1.5). Sent only when not 1.0; never sent on the beta shape |
| `INTERVIEW_TEMPERATURE` | *(unset)* | sampling temperature (0.0–2.0), both session shapes. Sent **only when set** — the default keeps the model's own, because an unverified parameter on `session.update` can kill the handshake |

There is **no API key on this path at all** any more. The stream is signed with
SigV4 from the standard AWS chain, so what used to be a containment problem —
one pasted credential, attached to exactly one socket, never logged — is now
absent by construction. `app/main.py` still pins the `websockets` logger to INFO
(this socket uses that library, as the removed LiveKit path did, and
`--log-level debug` prints handshake headers unredacted).

## Close codes

The client maps each of these to a sentence (`CLOSE_MESSAGES` in
`interview.service.ts`). An unmapped code degrades to "closed unexpectedly", so
**adding a close code to the server means adding it to that map**.

| code | meaning |
|---|---|
| 1000 | **the interview completed** — the relay closes it itself after `reep.report`. It also still covers a student pressing End at minute three, which is why the *record* does not read a 1000 as `completed` unless the scorecard actually settled |
| 1001 / 1012 | server restarting (1012 is what uvicorn itself sends) |
| 1006 | handshake refused or network dropped — no reason available |
| 1008 | not signed in, not a STUDENT, or a STUDENT session with no `studentId` (`interview_sessions.student_id` is NOT NULL, and meeting that as a 1011 thirty seconds in with an upstream session already billed is worse) |
| 1011 | internal error |
| 1013 | per-worker concurrency cap, or the fleet's `INTERVIEW_MAX_CONCURRENT` ceiling (the AWS quota for Nova 2 Sonic streams) — the server is full, everyone is affected |
| 4001 | the engine is not configured — on `nova`, no resolvable AWS region |
| 4002 | upstream 403/429/5xx/handshake failure, **or** `create_response: false` was not applied (refusing beats running a known-double interview). On `nova` it is also every way Bedrock can refuse the stream — credentials, IAM, an unsupported region, a throttle at the door — and the log line names which |
| 4003 | Origin refused (a deployment mistake) |
| 4008 | idle cap — **no inbound audio**. Not a backstop for a transcription stall: the browser's echo-gate keepalive keeps frames flowing, which is why the answer deadline exists |
| 4009 | hard session cap |
| 4010 | unknown `?specialization=` (a stale client, never the student's fault) |
| 4011 | the relay could not advance the interview — our own `response.create` was never acknowledged, or was refused twice. Deliberately not 4002: that says "upstream is unavailable, retry shortly", this is a working socket on which **our** sequencing came apart |
| 4012 | **this student** already holds `INTERVIEW_MAX_SESSIONS_PER_USER` (default 2) live interviews on this worker. Deliberately not 1013: "the server is full" sends them to support, "your other tab is still open" sends them to the tab |
| 4013 | no live consent grant for the current version |
| 4014 | consent withdrawn while the interview was running |

4008 and 4009 are the only two whose reason names a configurable figure, so they
are the only two the client renders with `detail`.

## Troubleshooting

**"The connection dropped. Check your network." (1006)** — the handshake was
refused before accept, so the API never saw a route. Check the API is on 3300 and
that `/api/interview/status` answers.

**Every session closes immediately (4001)** — no AWS region resolves for Nova
Sonic. `/api/interview/status` says so in words; set `NOVA_SONIC_REGION` to a
region that serves the model.

**Every session closes immediately (4002)** — Bedrock refused the stream. The
log line names which: credentials, IAM (the task role needs
`bedrock:InvokeModelWithBidirectionalStream`, a *third* action that
`InvokeModel` does not imply), a region that does not serve the model, model
access not granted in the account, or a throttle.

**Every session closes immediately (4002) with `UnsupportedTransportError`, in
every region, with or without credentials** — this one is not AWS refusing
anything; the request never left the process. `awscrt` is missing, or the engine
resolved its config without naming a transport. `aws-sdk-bedrock-runtime` does
not depend on `awscrt`, and the default aiohttp transport declares
`SUPPORTS_DUPLEX_STREAMING = False`, which the bidirectional API requires.
`pip install -r requirements.txt` (it is pinned there) and check
`AsyncBedrockRuntimeConfig.resolve` is still being passed `transport=`. Tests
that fake the upstream cannot see this, which is why two in
`tests/test_interview_nova.py::TestTheTransport` pin it directly.

**Connects, transcribes, plays no sound** — the client matches a *set* of audio
event names for exactly this reason, so a name change upstream cannot mute the
interviewer silently. Check the engine is emitting `response.audio.done` and that
`audioOutput` frames are arriving in the API log. Open the record on the
Interview records screen first: its facts line reads "N turns, M saved". N equal
to the student turns alone means the model produced no reply the engine could
record (no audio and no final text); N larger than M means the write path
dropped rows, and the cause is logged as `Dropped interview turn`.

**The transcript is in Hindi (or any language but English)** — Nova 2 Sonic
auto-detects the language and switches, and `kiara`/`arjun` are its Hindi voices
as well as its Indian-English ones. There is no language parameter; the prompt's
"Language and turn-taking" note pins English and tells the model to ask for
English. If a transcript still arrives in another script the word gate reads it
word for word (`words_of`) rather than as empty, and "अगला प्रश्न" is a skip.

**The interviewer asks two questions in a row at a phase boundary** — a control
note landed after the model had begun its reply. This is the known trade in
`app/interview_nova.py`'s header; the injection point is `_on_student_transcript`
and the alternative it rejected is documented there.

**The interviewer's voice cuts out mid-sentence on speakers, or pauses on
earphones** — the browser's echo gate. On speakers the microphone hears the
interviewer; anything of that echo that reaches Nova is "the student
interrupting", Nova abandons the question and the relay flushes the browser's
queue. The gate (`apps/web/src/app/core/echo-gate.ts`) holds the uplink while
the interviewer is audible, judging each microphone chunk against the playback
that can be arriving in it (the player's own timeline, read at the chunk's
capture time and reaching back by the device's `outputLatency`) scaled by an
echo coupling it learns once per session. Until 2026-09-22 it calibrated from
the first chunks after audio was *scheduled* instead — a jitter-buffer lead
plus the device's output latency before anything is *audible* — so it learned
the silence, and the interviewer's own voice opened it on nearly every
question; its echo reference was also capped at 0.03 RMS, below a speaking
voice. On earphones there is no echo to hold back, and the room's
**Speaker / Earphones** switch (`shared/interview-room/audio-route.ts`) turns
the gate off; before it existed nothing called `setEchoSuppression`, so a breath
on a headset mic paused the interviewer. To diagnose a report, read the
`Interview ended …` log line's `client=` field (the browser sends its counters
as `reep.client.stats`): `route` says which side the student was on;
`local_barge_ins` well above `confirmed_barge_ins` is the gate opening on
something Nova did not take for speech; `coupling` at the prior (2.0) means it
never measured any echo; `underruns` with `lead_ms` at 300 means the audio
arrived late (network, or the event loop), which no gate setting fixes.

**"Please accept the interview terms before starting." (4013)** — the student has
no live grant for `INTERVIEW_CONSENT_VERSION`. Normally the consent panel appears
instead, so 4013 means the client's own check disagreed with the server's: a
cached bundle against a bumped version, a hand-rolled socket, or a tab left open
across a revocation. A reload puts the current copy in front of them.

**The interview sounded fine and saved nothing** — the runbook query above is the
first check, and `interview_sessions` is the second: a row whose
`turns_emitted` far exceeds its `turns_persisted` says the relay had the turns
and the database did not take them, with no join required. A row still reading
`running` long after the fact means all three finalization layers missed it,
which in practice means the process was killed and the startup sweeper has not
run since.

**A row says a recording exists and the download 404s** — the file is not on
disk. Logged as a WARNING naming the session id and path, because a row claiming
audio that is not there is how a deletion request quietly fails to be honoured.

## What this replaced

`POST /api/agent/ask` is **retained, mounted and working**, and now has a UI
caller of its own (the REEP Agent chat at `/student/agent`). See the header on
`app/routers/agent.py` for the route-by-route audit of what is still live.

**The LiveKit voice stack is NOT.** This section said it was retained as the
rollback path; it was removed in 2026-09 — `voice_agent.py`, `app/routers/voice.py`
(`/api/voice/*`), `requirements-voice.txt`, `chat-voice.service.ts`, the fourth
process and its Python 3.12 venv are all gone. There is no rollback path; this
socket is the only voice experience in the product.
