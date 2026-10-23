# REEP — the stack

REEP is a college placement-readiness dashboard. It is an **Angular front end** talking to a **Python/FastAPI back end** over HTTP, on **PostgreSQL**. (It used to be a Next.js/React app with a NestJS API and Prisma — that stack has been fully migrated away and deleted. Ignore any lingering references to Next.js, React, Prisma, `server-only`, or `apps/api`; they are gone.)

```
apps/web      Angular 22 SPA (standalone components, signals, ReactiveForms)
apps/api-py   FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 (psycopg 3, PyJWT)
docker-compose.yml   Postgres 17 (container reep-postgres, host port 5433)
```

## Who is who — the naming conventions

Four kinds of account. The on-screen name and the stored role differ for two of them, and that gap is where every diagram and document has drifted (a poster that said "DIRECTOR / ADMIN" and "FACULTY / MENTOR" for a month after DIRECTOR stopped being a role), so this table is the reference and the rest of this file uses its words.

| On screen | Stored role (`users.role`) | In the code | What it is |
|---|---|---|---|
| **Student** | `STUDENT` | `/student/*`, `app/routers/student.py`, a `students` row, the session's `studentId` | A current student. The roster is the access control: an address with no `users` row cannot sign in. |
| **Faculty** | `MENTOR` | `/mentor/*`, `app/routers/mentor.py`, `require_mentor`, the console's `/admin/faculty` (`features/admin/faculty/`) | A faculty account. It is **not a mentor by existing** — it has no `mentors` row and sees nobody until the Main Admin assigns it a student. |
| a student's **mentor** | `MENTOR` **plus a `mentors` row** | `students.mentor_id` → `mentors.id`, the session's `mentorId`, `app/mentor_functions.py` | The faculty member assigned to that student. "Your mentor" on the student's screens; the `Mentor` group row is what rule 2 scopes on, and the two `mentor.*` functions (the Mentee Log and the notebook) and `admin.student_records` (their mentees' full records) are derived from having a mentee. Verifying a student's skills is NOT: it is a grant the Main Admin makes (`app/verifiers.py`). |
| **Alumni** | `ALUMNI` | `/alumni/*`, `app/routers/alumni.py` | A graduate: no `students` row, no `mentors` row, no staff scope. |
| **Main Admin** — the placement office | `ADMIN` | `/admin/*`, `app/routers/console.py` and `admin_*.py`, `require_admin`, `features/admin/`, the login's dashed "Main Admin" door | **One account per deployment.** "Placement office", "placement cell" and "TPO" on the screens all mean this account, and its SWOC lines are stamped `SwocSource.PLACEMENT`, the placement cell's viewpoint; it is the one role Governance admits, and faculty reach a console screen only as a grant it makes. |

- **"Mentor" is a stored value (`MENTOR`) and a relationship (`students.mentor_id`), never the name of an account kind.** The account is Faculty. The student's screens say "your mentor" and "Faculty / TPO Log" because, to that student, the assigned faculty member is one.
- **The placement office is the Main Admin, role `ADMIN`, and there is exactly one.** DIRECTOR is not a role — see "DIRECTOR is not a role" below for what survives of the word (an enum value nothing admits, and the PROGRAM DIRECTOR block on the college's own leave form).
- The login's portal chooser is Student / Faculty / Alumni, and the office comes in through the dashed "Main Admin" door below it; the pick is descriptive and the role is always the roster's.
- Diagrams draw the pair, on-screen name first — "Faculty (role MENTOR)", "Main Admin (role ADMIN)" — and name `/admin/*` and `require_admin`, never `/director/*` or `require_director`. The root `README.md`, `docs/architecture.md` §5 and `docs/institutional-spine-build-log.md` "Naming conventions" (L0) carry this same table; `docs/diagrams/` is generated from `tools/diagrams/`, so a wording change there is a code change.

## Running it

1. **Database** — `docker compose up -d` starts Postgres on `localhost:5433`. The API uses a database named `reep_py` on that server.
2. **Back end** — from `apps/api-py` (venv at `.venv`, Python 3.14):
   ```
   .venv/Scripts/pip install -r requirements-dev.txt  # runtime + pytest
   .venv/Scripts/python -m alembic upgrade head      # apply migrations
   .venv/Scripts/python -m app.seed                  # idempotent dev seed
   .venv/Scripts/python -m uvicorn app.main:app --port 3300
   ```
   Docs at http://127.0.0.1:3300/docs. **Windows note:** `uvicorn --reload` has wedged a stale worker here — after editing backend files, kill port 3300 and restart rather than relying on `--reload`.

   **On `ENV=prod` this process now REFUSES TO BOOT on a bad secret, and that is not a bug in your deploy script.** `Settings.production_boot_failures()` (`app/config.py`) is raised from `app/main.py`'s lifespan, so uvicorn never binds a port, and the log names every problem it found. It fires on: an `AUTH_SECRET` that is blank, still the value published in this repo and `.env.example`, an obvious placeholder, or shorter than 32 characters; and a `DATABASE_URL` still carrying this repo's dev password. Set real values — the message includes the command to generate a secret. It is deliberately a refusal and not a warning: `AUTH_SECRET` signs the `reep_session` cookie, so a production host running on the repo default is one forged `{"role":"ADMIN"}` cookie away from every student's marks, attendance and USN, with no login and no database row involved. On every development `ENV` the check returns nothing at all, and `tests/test_boot_guard.py` pins that as hard as it pins the refusal — a guard that trips on a laptop gets deleted by whoever is trying to ship that afternoon.
3. **Front end** — from `apps/web`: `npx ng serve` (port 4200). `proxy.conf.json` forwards `/api` → `http://localhost:3300`, so the app is same-origin and the httpOnly session cookie is carried. The whole API surface the client calls lives under `/api`.
Seeded logins: `student@bgscet.ac.in` / `student123`, `mentor@bgscet.ac.in` / `mentor123`, `alumni@bgscet.ac.in` / `alumni123` (no profile row — so the alumni first-login create-profile flow is what you see on a fresh database), and `admin@bgscet.ac.in` / `admin123`: the **Main Admin**, the only role Governance admits (`require_admin` in `routers/mentor.py`). **There is no `director@bgscet.ac.in` any more** — see “DIRECTOR is not a role” below.

### Two requirements files, two seeds — the split is deliberate

- `requirements.txt` is **runtime only** and pinned `==`; it is what the Dockerfile installs. `requirements-dev.txt` pulls it in and adds pytest. A test runner has no business in a production image, and `>=` bounds meant a rebuild months later resolved a dependency set nobody had run the suite against.
- `python -m app.seed` **refuses to run when `ENV=prod`.** It creates the logins above — including the Main Admin, who by rule 2 below reads every student's marks, attendance and USN — behind passwords published in this file. Those accounts must never exist on a production host, so there is no override flag.
- `python -m app.seed_kb` is the production-safe seed: the grounded assistant's Knowledge Base, no accounts. Production needs it (without it the assistant has nothing to ground against) and never needs the demo users, which is why they no longer travel together.
- `python -m app.seed_catalogue --college 1MP` is the **third** seed and the same kind of thing: the institutional spine -- college, department, courses, specializations and one batch per LEAF -- as code, reviewed in a pull request, written by pressing one button on the Ops task menu. Production-safe (no accounts, nothing to refuse on `ENV=prod`), **idempotent**, **dry run by default** and **ADDITIVE ONLY**: an existing row is returned untouched even where its fields differ, because the office renames things on screen and a seeder that reasserted its own names would undo that work on every press -- divergence is REPORTED, never corrected. Before it, the only route into a real deployment was about fifteen Catalogue forms per college. **THE FIELD THAT MATTERS IS A LEAF'S `code`**, because `_default_track` preselects the interview track by exact match on it: `fa` gets the Financial Analytics interviewer and `FIN` -- which the model's own column comment suggests -- gets nothing at all, so that college's students meet "General interview", which has no wrap-up phase and CANNOT BE SCORED. That is `grant_access --department-id`'s dead end reached from the catalogue instead of the account, and closing one door while leaving the other open is closing neither. A code matching no track is LEGITIMATE (BGSCET's Marketing and Logistics & Supply Chain have none, and the office can add one on the Interview Tracks screen whenever it likes, at which point the match starts working with no change to the file) -- so every run prints, per leaf, whether a student there is preselected today and names the exact string that would fix it. `tests/test_seed_catalogue.py` pins the codes BY NAME, including that **Marketing is deliberately not mapped to `dm`**: the only marketing track in the matrix is DIGITAL marketing, a different discipline and one of that college's own courses, so the tempting "fix" would sit a Marketing student in front of a growth CMO asking about CAC/LTV ratios and score them against it.

**Handing a deployment over: `python -m app.purge_people`.** The counterpart to the seeds — it empties a deployment of PEOPLE (every account except the Main Admin, every record those accounts produced, every transcript and every recording, and the documents and audio on the volume) while leaving the institution and the catalogues standing: colleges, departments, courses, specializations, batches, job postings, the badge and certification catalogues and the Knowledge Base. It exists because nothing else can do it: `grant_access` creates and updates accounts and cannot remove one, the Main Admin console has no screen at all for deleting a faculty account, and the Ops task menu is a FIXED LIST because free text there is RCE on the cluster. **Every one of the 111 tables carries a written verdict in `VERDICTS`, and a table nobody classified ABORTS THE RUN** — not kept (which leaves a student's records behind) and not emptied (which destroys a catalogue somebody added last week); `tests/test_purge_people.py` fails in CI first, so the next person to add a table is made to decide. **That question is asked of the DATABASE and not of `app/models/`** (`known_tables`), and until `e1c4b7a209d6` it was not: every reader here walked `Base.metadata`, so a table created by a MIGRATION and deliberately given no model was invisible to the coverage check AND to the delete pass. `students_orphaned_cohort_ids` — migration `d5a1c8b30f47`'s rescue table, model-less on purpose (`_PRESERVED_DATA_TABLES` in `migrations/env.py`: it is an operator's receipt, not part of the schema) — sat outside both destructors that way, so a pass whose whole purpose is removing every trace of people left a list of their `students.id`s standing, in the one table nobody thinks to look at because it is not in `app/models/`. It is `EMPTY` in `VERDICTS` and `ALL` in `STUDENT_VERDICTS` now, and reflecting the live database is what makes the refusal cover the NEXT one even if nobody remembers to add it to the tuple. **Files go before rows**, because a row is the last pointer to a student's resume and a named student's recorded voice, and a delete that loses the pointer first leaves bytes nobody can find — `retention._delete_interview_audio`'s reasoning, applied to the five document stores and the S3 recordings. It refuses unless there is EXACTLY ONE ADMIN: zero would lock every human out of the console, and picking between two would be this module choosing which colleague keeps their account. **Dry run is the default**; `--apply` also demands `--i-understand-this-is-permanent`, and the Ops task's `purge-people` demands a typed sentence of its own on top of the menu's `confirm: run`, because that box is muscle memory by the time anyone reaches this task and nothing else on the menu destroys anything. The delete order satisfies 184 real foreign keys — several with no `ON DELETE` at all (`login_days.user_id`, `mentors.user_id`, `students.user_id`, and the four `created_by_user_id` columns on the KEPT institutional spine, which are nulled first) — and the test proves it by running the real delete against the real schema inside a transaction it rolls back, which is why `_delete_rows` is factored out of `execute` without a commit.

**Clearing a demonstration cohort: `python -m app.purge_students`.** The narrow counterpart, and the one that gets run on a deployment that is STAYING IN USE: it deletes every account whose role is STUDENT, everything those accounts produced, and everything staff wrote ABOUT them — while every MENTOR, ALUMNI and ADMIN account, their signatures, their upskilling shelf, their governance grants and their own leave requests stay exactly where they are. `purge_people` cannot express that, because its verdicts are whole-table (`students: EMPTY`, `users: SURVIVOR`): running it to clear a cohort deletes every faculty account on the way through. So here a verdict is THREE values and not two — "delete the student rows" is a different sentence in a table only a student can own (`resumes`) and in one shared with staff (`leave_requests`, `conversations`, `agent_runs`, `login_days`). 47 tables are emptied outright, 40 are untouched, 24 are scoped, and **`STUDENT_VERDICTS`'s key set is checked against `purge_people.VERDICTS`**, so the next person to add a model is stopped by BOTH destructors rather than the one they happened to read. **The doomed set is read ONCE, before anything is deleted**, and held as literal ids: as a live subquery over `users` it would be right only by accident, because the accounts go near the END of the pass and any table ordered after them would match nothing and silently leave its rows behind. `registrations` is the one table scoped by something other than an account — an application that was APPROVED, or that carries a doomed address, goes; the office's PENDING queue survives, because an approved application left behind makes `POST /api/register`'s duplicate guard refuse that address forever with no account left to explain why. The three file stores are `purge_people`'s own functions handed a subset, never a copy. Dry run is the default, the Ops task's `purge-students` demands the typed sentence `DELETE EVERY STUDENT` (deliberately NOT `purge-people`'s, so the words name the act), and `execute` refuses to commit if the number of non-student accounts changed.

**THE DESTRUCTORS ASK A HUMAN FIRST. THE NIGHTLY SWEEP DOES NOT, AND THAT IS THE DIFFERENCE §35 GUARDS.** `app/retention.py` is the one scheduled destructor in the product — it deletes every night with nobody watching — and the row it must never reach is the one that decides whether a student can sign in. It does not today: it imports no `User` and no `Student` and writes to six tables, none of them `users` or `students`. Nothing asserted that, and `tests/test_codebase_guards.py` §35 now does, in the one shape that actually holds. **Do not write it as "identity is out of scope for every destructor"**: `users` is deliberately IN scope for both purge modules (`VERDICTS["users"] is SURVIVOR`, `STUDENT_VERDICTS["users"] is ACCOUNTS`) because deleting accounts is what they are FOR, so that assertion either fails on the day it lands or gets weakened into one that asserts nothing. And **an import guard on `retention.py` alone is one call too shallow**: `purge_expired` calls `sweep_login_codes`, which lives in `app/account_links.py`, which *does* import `User` because it is where activation and reset links are minted — so the helpers are read in their own files, by name. `RETENTION_APP_IMPORTS` pins the import surface, function-local imports included (writing that guard immediately found one — `interview_audio.delete_session_audio`, imported lazily on purpose so the sweep's database half survives a host where that module will not import). Raw SQL is refused outright on this path, because an AST cannot read inside `text("DELETE FROM users …")`.

**The identity ledger: `python -m app.export_identity`.** The counterpart to both purges, and the only artefact in this deployment that is meant to outlive the schema. It writes ONE JSON object per account per day -- `person_uuid`, `email`, `google_sub`, `usn`, `role`, `password_hash` verbatim, and the institution as RESOLVED LABELS -- to an S3 bucket with versioning and Object Lock and **no lifecycle rule at all**. That absence is the feature: every database artefact this deployment has is bounded by `backupRetentionDays`, which RDS caps at 35, so a login deleted 36 days ago is gone in every region at once. The three fields that matter are already portable and were made so for other reasons -- `users.id` is `uuid4().hex` rather than an autoincrement, the hash is self-describing `scrypt:salt:digest` (it survived the Next.js -> FastAPI rewrite without one password reset), and `google_sub` pins the Google principal rather than the address, which is a lease the college re-issues. **Labels and never foreign keys**, because a future schema mints new cohort ids and an id here is a pointer into a database that no longer exists. It carries no marks, attendance, uploads or transcripts: that is the logical dump's job, keyed by the same `person_uuid`, and the ordering is deliberate -- access comes back in minutes from megabytes and the heavy data follows. An EMPTY export is REFUSED rather than written, because a file with no accounts is indistinguishable from a healthy export of an empty deployment and would overwrite a good ledger on the day a query regressed. It runs at 23:30 IST, **before** the 03:00 retention sweep, since a ledger written after the product's one scheduled destructor never saw what it removed. The task's grant is `s3:PutObject` and nothing else: a writer that can shorten its own retention is not being protected by Object Lock. `tests/test_identity_ledger.py` pins the record's shape without a database; the bucket, the grant and the ordering are pinned in `infra/cdk/tests/test_core_synth.py`.

**The logical backup: `python -m app.backup_database`.** The ledger's heavy half, and the answer to a question the RDS snapshots cannot be asked. Every backup this deployment has -- the automated snapshots, the daily AWS Backup rule, the Singapore copy, the monthly archive -- is PHYSICAL: a block-level image of PostgreSQL 17 on RDS, restorable only by standing up PostgreSQL 17 on RDS. That is the right tool for "we dropped a table" and the wrong one for the two days this module exists for, an engine or vendor that is no longer available and an application that has been rewritten. So it writes `pg_dump -Fc` -- PostgreSQL's own portable archive, compressed, with a table of contents, restorable table-by-table and across major versions -- keyed by the same `person_uuid` the ledger carries, and the ORDERING of the pair is the point: access comes back in minutes from megabytes, and the heavy data follows behind it rather than the student waiting on it.

**TWO BUCKETS, BECAUSE OBJECT LOCK'S DEFAULT RETENTION IS BUCKET-WIDE AND NOT PER-PREFIX.** The daily tier keeps 90 days and must then delete; the archive tier keeps the first successful dump of each month for years and must never delete. One bucket cannot hold both promises, and the way that fails is the worst available: **a lifecycle expiration aimed at an object whose lock has not lapsed is not an error** -- S3 re-evaluates it tomorrow, and the day after, deleting nothing and reporting nothing, so the bucket grows without bound behind a rule the console shows as working. `DUMP_LIFECYCLE_LAG_DAYS` (7) is why the daily rule fires strictly after its own lock, rather than on the same day, which would make that a coin toss per object. The archive bucket carries **no lifecycle rule at all**, the ledger bucket's deliberate absence for the ledger's reason, and `noncurrent_version_expiration` is not optional on the daily one: on a versioned bucket an expiration writes a delete MARKER and leaves the version stored and billed, so a rule without it frees nothing while looking right.

**THE `.partial` RULE DOES NOT PORT, AND COPYING IT WOULD BE HARMFUL.** `docker-compose.prod.yml`'s sidecar writes `<name>.partial` and renames on success. On S3 the rename half is unnecessary -- an incomplete `PutObject` leaves no object -- and the `.partial` half is a trap, because an Object-Locked bucket would keep that truncated file undeletable for the full retention. What the rule actually guards is not a half-written FILE but a half-made DUMP that uploaded perfectly, so the discipline moves EARLIER: `verify()` reads the archive's table of contents back with `pg_restore --list` and refuses unless `users` and `students` carry TABLE DATA. A byte floor was considered and rejected -- it is a number somebody must keep true as the deployment grows, it is wrong on a new college's first day, and it cannot tell a truncated archive from a small one. Nothing reaches either bucket unopened.

**THE TASK MAY WRITE THE DUMPS AND MUST NEVER READ THEM.** These buckets hold a complete copy of every student record, so a task that can read them is one compromise away from exfiltrating the whole database from the BACKUPS -- past every control on the database and past rule 1 entirely. The trap is specific: `head_object` is the obvious way to ask "is this month archived" and S3 authorises HeadObject with `s3:GetObject`. `month_is_archived` uses `list_objects_v2` instead, which returns key names and never contents, under an `s3:ListBucket` grant conditioned on the `monthly/` prefix; the synth guard asserts no policy naming either bucket carries an `s3:Get*` or `s3:Delete*`. And the month's copy is the FIRST SUCCESSFUL dump of the month rather than the one taken on the 1st: keyed to the date, a month whose 1st failed has no archive copy at all and the gap is invisible for years, because the daily tier still looks healthy.

**THE FILE HALF: `python -m app.archive_documents`, and the manifest under it.** The two tiers above carry ROWS. Nothing carried BYTES, and the arithmetic was worse than it looks: an uploaded file's only copy beyond the EFS volume is the daily AWS Backup plan, bounded by `backupRetentionDays` — 35, RDS's ceiling — and the ARCHIVE selection that reaches past 35 days names `[db_arn]` and nothing else, deliberately, so a multi-year lifecycle over EFS could not keep every recorded interview for years. So **a marksheet a student deleted was recoverable for 35 days and then gone in both regions at once**, with nothing on any screen saying so. A college keeps a student's academic record for decades; 35 days is not a retention policy, it is the absence of one. `app/document_archive.py` copies every file into `reep-documents-archive-<account>` — versioned, Object-Locked, **no lifecycle rule at all**, the ledger's deliberate absence for the ledger's reason — as it is stored. **THE LIVE STORE IS UNCHANGED**: a student deletes an upload and the bytes leave EFS that second, the row goes, the screen is correct, the quota is freed. The website's delete and the archive's permanence are promises about DIFFERENT COPIES, which is the only way to keep both. The inline PUT sits in `document_store.save_bytes`, the one choke point all six stores pass through, and is BEST-EFFORT by contract — it raises nothing, because an S3 blip must never be why a student is told their valid certificate was rejected. The nightly sweep is what makes the promise hold anyway, and it is the ONLY writer that archives INTERVIEW AUDIO: the recorder writes its WAVs incrementally, so during a live interview there is no complete file to upload. It runs at 02:00 IST, and `test_the_archive_sweep_runs_strictly_before_the_retention_sweep` pins that against 03:00 — a stronger ordering than the ledger's, because retention DELETES audio off the volume and a pass that ran after it has missed those bytes permanently, with both jobs green. **The task may WRITE and must never READ**: `s3:PutObject` plus `s3:ListBucket`, and the sweep asks what exists with `list_objects_v2` rather than `head_object`, which S3 authorises with `s3:GetObject` — read access to every document the college holds, as original bytes rather than a dump needing restoration. **The sweep never deletes**: an object with no file behind it is the normal state of every document a student has since removed, which is the whole point.

**`archived_documents` IS THE INDEX, AND A `deleted_at` COLUMN COULD NOT HAVE BEEN.** The archive key is the `stored_name`, a bare `uuid4().hex`; the only thing that ever knew whose file that was is the live row pointing at it, so deleting the row leaves the bucket holding a file nobody can name — `retention._delete_interview_audio`'s "a delete that loses the pointer first leaves bytes nobody can find", arriving from the other direction. The first design was a soft delete on the six document tables and **it cannot express what actually happens**: `staff_signatures.user_id` is UNIQUE and its PUT overwrites `stored_name` in place, and `alumni_profiles` holds exactly one resume and does the same — in both there is no second row to flag and nowhere on the live row to keep the old name. It also could not survive `python -m app.purge_people`, where a flag on a row in a table being EMPTIED protects nothing. So the manifest is a table beside them: one append-only row per file, `released_at` stamped when the live row stops pointing at it, and **nothing in the product ever deletes one** — it is `KEEP` in BOTH destructors, which `tests/test_codebase_guards.py` §36 asserts rather than leaving to a reading of the dicts, because `archived_documents` names files belonging to the very people a purge removes and `EMPTY` is the instinctive answer. **`owner_id` is a plain String and never a foreign key**: an FK would mirror the `ON DELETE CASCADE` the columns it shadows carry, so the manifest would be destroyed at exactly the moment it becomes the only remaining record — `export_identity`'s labels-not-keys argument, for the same reason. It carries a name, a size, a type, an owner id and two timestamps and **no marks, attendance, USN or address**, pinned by a test, because a table that survives a deletion somebody asked for must not be a quiet way for those to survive it too. **`document_manifest.save_and_record` is the only spelling the routers use** and §36 fails the build on a router that imports `save_bytes` directly — `document_store`'s own docstring records what happened the last time this invariant lived in the callers, when the comment claimed a single caller and three writers later `routers/alumni.py` had no quota check at all. The store cannot host this one, because a manifest row needs a `Session` and the store holds no ORM. **There is NO BACKFILL**, and the migration says why: every seeded row would carry `recorded_at = now()`, telling every future reader the whole store arrived on deploy day — `mentor_assignments`' lesson — and there is no honest substitute to reach for, because the two replace-in-place stores keep only the CURRENT file's timestamp. Files predating the deploy are still uploaded by the sweep (it reads the disk, not this table), so they are unnamed, never lost.

**THE CLIENT MUST BE THE SERVER'S MAJOR VERSION, AND THAT IS ONE FACT IN TWO FILES.** `pg_dump` refuses a server newer than itself -- the good failure, it stops rather than writing something subtly wrong, but it stops at 01:00 with nobody watching and the API keeps serving. Debian bookworm ships PostgreSQL 16's client and the database is 17, so `apps/api-py/Dockerfile` installs `postgresql-client-17` from PGDG explicitly, and `tests/test_codebase_guards.py` compares that pin against `engine_version` in `infra/cdk/reep_core/stack.py`, the same way it already compares the deregistration delay against `nova_sonic_connection_seconds`. An RDS upgrade touches the stack and has no reason to touch the Dockerfile, which is exactly why the comparison exists. The password reaches `pg_dump` through libpq's `PG*` variables and never argv, `set_password`'s argument applied to the database's own credential: argv is readable in `ps` and recorded verbatim in the CloudTrail entry for an ECS task override. It runs at 01:00 IST -- after the ledger, before the retention sweep for the ledger's reason, and clear of the RDS backup window, because `pg_dump` is a long read over every table and that window is when the snapshot is taken. `dbDumpArchiveYears` is a PLACEHOLDER for a decision the college has not made, exactly as `archiveRetentionDays` is; the run says so on every night the archive bucket is unconfigured rather than letting a reader assume the monthly copy exists.

**A task definition is immutable, and that is why `SES_FROM_ADDRESS` and `IDENTITY_LEDGER_BUCKET` are gated on `harden_ecs` while their IAM grants are gated on `harden`.** It reads like two gates for one feature and it is not. Adding an environment variable registers a NEW REVISION that the service rolls onto, and step 9a of the cutover (`hardenEcs=false`) exists so an ECS circuit-breaker rollback cannot undo a Multi-AZ conversion in the same update -- so the task definition has to leave that phase byte-identical to the import mirror. The grant can go early because it is INERT without the variable: a task holding `s3:PutObject` that does not know the bucket name writes nothing. `test_the_database_half_does_not_touch_the_ecs_trio` is the guard, and it caught the ledger's variable when it was first written on `harden`. **`SES_CONFIGURATION_SET` and `LEAVE_MAIL_ENABLED` ride the same gate and are NESTED INSIDE the `SES_FROM_ADDRESS` branch rather than merely ordered after it** — a configuration set with no sender is inert noise, and a leave switch with no sender is the `mail_logs` lie below, so the invariant is a shape in the code and not a second rule to remember. `leaveMailEnabled` without `sesFromAddress` is refused at synth outright.

**Tests:** `cd apps/api-py && .venv/Scripts/python -m pytest` (the backend suite). Front end: `cd apps/web && npx ng build`.

**THE END-TO-END SUITE IS AT THE ROOT, COVERS THE WHOLE APP, AND EVERY TEST IN IT IS A MANUAL CASE'S TWIN (2026-09-22).** `npm ci && npm run test:e2e` from the repository root runs Playwright (`playwright.config.ts`, `tests/*.spec.ts`) against the RUNNING app: the SPA on 4200 in front of the API on 3300, `ENV=dev`, dev seed applied, nothing mocked. Install the browser once with `npx playwright install chromium`. The suite lives at the root and not in `apps/web` because the Angular unit-test builder collects every `**/*.spec.ts` under its project root, so a Playwright spec there would be run by vitest. **Eight modules, one case file and one spec each.** `test-management/manual-test-cases.md` is the index (the modules with their ID ranges and screens, setup, the seeded accounts, what each CSV status means), and the cases are in `test-management/cases/0N-*.md`. The specs are `tests/auth-sync.spec.ts` (module 01, the name the suite started with) and `tests/0N-*.spec.ts`. **The numbers are the run order, and one of them is load-bearing:** module 08 gives the seeded student a mock-interview record and a consent, which module 04's "before any interview" cases need to be absent, so 04 must run first. **Each test is linked to one case by the `@TC-NNN` tag in its title.** Its `test.step()` titles are that case's numbered steps, and every assertion message names the expected result it checks (`TC-001 ER-4: ...`). `tests/reporters/manual-csv-reporter.ts` reads every case file and writes `manual-test-results.csv` with a row for every case. It **fails the run when the two drift**: an untagged test, a tag naming no case, a step list or step number that differs, an ID in two files, a case marked not automated that a test automates, or, on an unfiltered run, a case whose field names a tag no test carries. The problem is also written into that case's "Sync Problems" cell, so a drifted case never reads as a clean Passed. `npx playwright test --list` runs the tag checks without the app and leaves the CSV alone. **That reporter is a local file because `playwright-csv-reporter` does not exist on npm** (a 404 on 2026-09-22). Do not install whatever is published under that name later: a reporter runs in-process on every run. **Every spec imports `test` and `expect` from `tests/support/reep.ts`, never from `@playwright/test`,** because that `test` carries the auto fixture that checks the environment before each test and the `signIn(account)` fixture (the API sets the session cookie in the page's own context, so only the cases about the login screen type into it). **BLOCKED, SKIPPED AND KNOWN FAILURE ARE THREE DIFFERENT ANSWERS, AND WHICH ONE A TEST GIVES IS A DECISION.** Blocked is a broken environment (API down, database down, seed missing, account locked out): the test fails with the reason, so a run against a stopped API is red rather than a green row of skips. Skipped is the absence of something the environment is allowed not to have: an optional feature switched off (Google sign-in, TC-034 and TC-039) or test data earlier runs used up for good (a submitted ledger day cannot be reopened, an interview record cannot be deleted); the reason is in the CSV's Error column and a fresh database runs them all. Known failure is a `test.fail(true, 'BUG: ...')` that asserts the CORRECT behaviour of a product bug the suite found, so the test starts passing, and must be unmarked, the day the bug is fixed. **One worker, and never beside pytest:** the cases sign in as the seeded accounts, and REEP keeps one live session per account, so a second worker, or pytest's `login` fixture, would sign the suite out mid-test; for the same reason a run signs out any browser a developer has signed in as a seeded account. Cases that need more than one person use a second browser context or an API context of their own. It is **not a CI job**: it needs a live stack, and a sixth job would have to be added to all four files §34 compares. The runs leave data behind by design where the product keeps it (removed accounts, decided leave, archived test colleges), each case's post-conditions say what, and nothing asserts a count other cases can change.

**BRANCHES: `feature/*` → `dev` → `stage` → `main`, AND `hotfix/*` FROM `main` (2026-10-07).** `docs/branching-strategy.md` is the whole of it. Work is cut from `dev` and PRs target `dev`; `dev` is promoted to `stage`, `stage` to `main`, both with MERGE COMMITS (a squash leaves the source without the commit and every later promotion re-conflicts — the rulesets allow `merge` only there). `main` still deploys and still alone: `deploy.yml`'s OIDC role is pinned to `refs/heads/main`. A ruleset cannot say which branch a PR comes FROM, so `tools/ci/branch_policy.py` does, as the required check "Branch policy (promotion path)" on `main` and `stage` — its own workflow, not a sixth `ci.yml` job, for §34's reason. `stage` is a gate branch: no staging environment deploys from it. Keep `main` the default branch (`workflow_run`, schedules and `~DEFAULT_BRANCH` read it).

**ONLY THE LEAD MERGES, AND ONLY THE OWNER SHIPS (2026-10-15).** The repository has a second collaborator with write access, and on a personal-account repository write access is the only role there is. `.github/rulesets/lead-review.json` is what keeps that from being merge access: an `update` rule on `main`, `stage` and `dev` that only the admin role bypasses, and only through a pull request, plus one code-owner approval (`.github/CODEOWNERS`, every path `@darshani8`). It is a FOURTH ruleset and not more rules in the branch three because a bypass is per ruleset: the admin skips the approval and nothing else, so CI still binds the lead. Never name a collaborator as a code owner: the approval is the second line behind the `update` rule, and a collaborator owner's approval would satisfy it for everyone else. `deploy.yml`, `cdk-deploy.yml` and `ops-task.yml` refuse any run whose `github.triggering_actor` is not the owner (`deploy.yml` also admits `github-actions[bot]`, which is `agent-release.yml`'s dispatch and can only run `main`'s copy). The AWS role is already pinned to `refs/heads/main`, so a workflow edited on a branch cannot reach production; these checks close the "Run workflow" button on `main`. Repository SECRETS are not covered by either: any write collaborator can read them from a workflow pushed to their own branch. `tests/test_repo_access.py` pins all of it.

**EVERY BUG FIX IS LOGGED, WITH ITS DATE (2026-10-14).** The owner's rule. `docs/bug-log.md` holds one dated entry per bug or production error fixed: what was seen, the cause, the evidence, the fix, the PR and how it was checked. Entries go newest first and are written in the same PR as the fix. Update the entry's Status line when the fix is merged and when it is deployed. A problem found and not fixed goes under that file's "Open" list with the date it was found. This file still carries the long reasoning; the log is where a date answers "what broke and what was done".

**AGENTS IN THE LOOP (2026-09-29).** Every phase of the lifecycle has an agent; `docs/agentic-sdlc.md` is the map. `claude.yml` turns an `agent`-labelled issue or an `@claude` into a PR; `claude-review.yml` reviews every non-draft PR; `agent-release.yml` runs after green CI on `main`, writes release notes and — only when `vars.AGENT_AUTODEPLOY` is `true` AND `tools/ci/release_gate.py` says so — dispatches `deploy.yml` and watches it; `agent-ops.yml` sends infra drift, failed human deploys and `incident` issues to `agent-triage.yml`; `agent-maintenance.yml` sweeps dependencies weekly. **The gate is code, not a prompt**, pinned by `tests/test_release_gate.py`: a migration, a model, `infra/`, auth / rule 1 / rule 2 / deletion code, the service worker, a manifest, the deploy pipeline or the gate itself always needs a human, and loosening that list is loosening what reaches students unreviewed. No agent holds AWS credentials or a production secret (rule 1 applies to agents), none runs `cdk-deploy.yml` or `ops-task.yml`, and none is a CI job (§34). Triage is a CALLED workflow on purpose: issues opened with `GITHUB_TOKEN`, as `infra-drift.yml` opens them, trigger no other workflow.

**ONE THING AT A TIME TOUCHES ONE DATABASE.** `tests/conftest.py` says it in its first paragraph and the consequence is nowhere: the integration tests hit the SEEDED DEV DATABASE — the same `reep_py` a running `uvicorn` is using, the same one `python -m app.seed` wrote. So **two pytest runs at once, or a pytest run beside a live API, corrupt each other's fixtures**, and the failures that come out of it are the expensive kind: they look completely real, they name a plausible cause, and every one of them passes when you re-run it on its own. The two that cost an afternoon were a purge-plan count off by one (the other run's throwaway student was in the table when the plan was taken) and a fixture student answering 404 (the other run's teardown had deleted them mid-test). Nothing in the suite is isolated by a transaction — `make_user` and its friends commit, because the endpoints under test commit.

The workaround is one environment variable and no code: give each concurrent run **its own database**. `createdb reep_alice`, `DATABASE_URL="postgresql+psycopg://reep:reep_dev_password@localhost:5433/reep_alice" .venv/Scripts/python -m alembic upgrade head`, then the same `DATABASE_URL` on `python -m app.seed` and on every `pytest`, `alembic` and script invocation for that run. Two agents, two humans, or a human and a CI container on one machine are three separate databases, not one shared one. `alembic check` and `alembic current` read the database too, so they belong to whichever run owns it.

**CI has five jobs** (`api`, `pii-gate`, `api-imports`, `web`, `cdk`), and one of
them exists because a manifest shipped incomplete. `api-imports` proves
`requirements.txt` ALONE covers every module under `app/`
(`tools/ci/check_api_imports.py`). It was added
after `app/interview_local.py` reached main importing numpy undeclared — the
import is lazy, inside a request handler, so the API still booted and every test
still passed, and the break surfaced only as a pytest COLLECTION failure on a
clean machine. A lazy import does not make an undeclared dependency acceptable;
it only moves the crash from boot to the first student who reaches that path.

Those five names are also the five REQUIRED STATUS CHECKS in
`.github/rulesets/main.json` and in `tools/ci/protect-main.sh`'s
`REQUIRED_CHECKS`, and GitHub matches a required check by the job's DISPLAY NAME
as a string. So deleting a job does NOT retire its requirement: the ruleset
asked for "Voice worker (dependency completeness)" for months after that job
went with the LiveKit stack, which — had the ruleset ever been applied — would
have blocked every pull request on a check that can never report. Rename or
remove a job and edit all three files in the same commit.

**FOUR files, and the agreement is a TEST now, not a habit** (Phase 5).
`tools/ci/preflight.sh` runs the same five locally, in the order that fails
fastest — and it ran only FOUR of them until Phase 5, skipping `Infra (CDK synth
guards)` with a reason written in its own usage text ("it needs its own Python
3.12 environment and only matters when infra/ is touched"). Both halves were
true and neither made it optional: a required check runs on every pull request
whether `infra/` was touched or not, and a local runner that covers four fifths
of the gate teaches you to trust it and then lets you push into the fifth. It
now SKIPs that check (exit 2, never a silent pass) when `aws-cdk-lib` is not
installed. `tests/test_codebase_guards.py` §34 parses `ci.yml`'s job names and
compares them against the ruleset, against `REQUIRED_CHECKS` and against
`preflight.sh`, and fails the `api` job when any of the four disagrees —
`protect-main.sh`'s own grep only ever fired for whoever remembered to run it,
and said nothing about the committed ruleset, which is how that stale entry
survived.

The `web` job also runs four static checks over `apps/web/src` before the slow
steps, each guarding a rule that is invisible at the call site:
`check_brand_magenta.py` (magenta is reserved for `--primary-gradient`, and had
leaked to 21 sites, two of them chart colours in TypeScript that a stylesheet
grep would never have found), `check_style_duplicates.py` (one owner per global
class — see the two-stylesheets note below; it ratchets in both directions, so a
merged duplicate must be struck off `KNOWN_DUPLICATES` too),
`check_theme_tokens.py` (the grid and chart themes copy tokens as literals,
because neither library reads CSS custom properties — the duplication is forced,
the drift is not) and `check_form_submit.py`, below.

**SOMETHING MUST OWN EVERY `<form>`'s SUBMIT, AND `(ngSubmit)` ON ITS OWN IS
NOT SOMETHING (2026-09-17).** `ngSubmit` is an `@Output` of exactly two
directives — `FormGroupDirective` (`[formGroup]`, ReactiveFormsModule) and
`NgForm` (`form:not([ngNoForm]):not([formGroup])`, **FormsModule and nothing
else**). A component that imports only `ReactiveFormsModule` and writes a bare
`<form (ngSubmit)="save()">` matches neither, and Angular does not complain: an
event binding naming no directive output is legal, because custom DOM events
are legal, so it compiles to `addEventListener('ngSubmit', …)` for an event
nothing dispatches. The handler never runs — and because no directive is
listening for the native `submit` either, nothing calls `preventDefault()`, so
the button does what a submit button does with no JavaScript in the way: a
full-page GET to the current URL **whose query string is REPLACED** by the
form's named fields, of which a reactive form has none.

That is how an approved student lost their setup token. `/onboard?token=…`
rendered correctly, they typed their college address, pressed "Send me a code",
and the browser navigated to `/onboard?` — so the screen drew "This page needs
the setup link from the email we sent you" one keystroke after the link had
worked. The approval mail was fine and the API was never called; the only
evidence on screen was about the link, so it was reported to the office as a
broken link. **Steps 1 and 2 of the onboarding walk had never worked**, on
every deploy since the screen was written, and `ng build` passed on all of
them. `tsc` cannot see it, the template type-checker cannot see it, and no test
that does not press the button can see it — which is why the rule is a check
and not a convention. The same defect was sitting on College structure's
"Add domain" form, found by writing the check rather than by anybody's reading.

A `<form>` must carry `[formGroup]`, or bind `(submit)` and prevent the default
itself (the login screen's "Forgot password?" spelling, for a form whose inputs
are signal-driven and have no group to bind), or `ngNoForm` — or its component
must import `FormsModule`, which the login screen legitimately does for its
`[ngModel]` code form. That last allowance is why the check reads the component
and not just the template: strip that import and the check goes red rather than
the button going quiet.

**Routes are lazy.** `app.routes.ts` uses `loadComponent`, never a static `component:` reference. Every route was once eagerly imported, which put the whole app — mentor and admin screens, the resume builder, the realtime assistant — into a single 1.23 MB `main` chunk that a student on a phone downloaded before the login form could paint. `main` is ~142 kB now. Since Angular 22.2 the builder's initial total also counts the framework chunks `main` imports (~419 kB with styles, which 22.1 shipped too while reporting 209 kB), and the budget (460 kB warning, 610 kB error) keeps the headroom it had before that recount. It is a backstop against a heavy route or library going eager, not a per-route lint.

## Auth — Google-only sign-in over the session retained from the migration

**Sign-in is Google, for every role, and the roster is the access control.** `app/google_auth.py` verifies the Google ID token properly — RS256 signature against Google's JWKS, `aud` = our client id, `iss` = accounts.google.com, unexpired, `email_verified` true, plus a single-use `state` cookie and a `nonce` — and then looks the verified email up in `users`. **A Google account with no matching row is refused** (`302 /login?error=sso_not_enrolled`); nothing self-provisions, and no role is ever guessed. Students come from `python -m app.seed_roster` — production-safe and idempotent like `app.seed_kb`, no passwords — which derives the email from the USN (`1MP25MDM01` → `1mp25mdm01@bgscet.ac.in`, domain from `ROSTER_EMAIL_DOMAIN`, alias `COLLEGE_EMAIL_DOMAIN`; `--rekey-domain` moves an already-seeded batch if that guess was wrong), so a student's USN is already filled in on their profile and they never type it.

What Google issues is **the same session as before, byte for byte**: passwords are `scrypt:salt:digest` (Node `scryptSync`-compatible: N=16384, r=8, p=1, dklen=64, salt as a hex string), sessions are HS256 JWTs signed with a shared `AUTH_SECRET` carrying the same claims (`userId, email, name, role, studentId?, mentorId?`), in the httpOnly `reep_session` cookie. `require_*` dependencies in `apps/api-py/app/identity.py` / the routers read the session and were **not changed** — they cannot tell the two paths apart.

**ONE DEVICE AT A TIME (2026-09-10).** A REEP account holds exactly **one live session**: every sign-in — password, emailed code, Google, and the activation link — advances `users.token_version` before the token is minted, and `app/security.py` refuses any token whose version is behind the row. So signing in on a phone drops the laptop on its next request. **The newest sign-in wins, deliberately**: refusing the *new* device instead would lock out the student whose browser crashed until a row aged out, and it would need a sessions table, an eviction policy and a device list that this design does not have. `_retire_other_sessions` is the in-memory half and **the caller's commit is what persists it** — on the code door that commit is the same one that lands the one-time code's `consumed_at`, so a separate earlier commit would make "code spent, session refused" reachable. `_confirm_exclusive_session` seeds this worker's revocation cache and must run **after** the commit. If that commit fails the session is still issued and the older devices survive: fail-open on exclusivity, never on authentication, and logged where it happens. `tests/test_single_device_session.py` covers all four doors, and the mutation that matters most is building the payload *before* the bump — that mints a token one version behind the row, which is a 200 from `/login` followed by a 401 on the very next request, for everyone.

Because this makes being signed out a routine event rather than an incident, the server says **why**: a 401 whose cookie was retired (rather than expired or absent) carries the header `X-Reep-Session: retired` (`session_was_retired` in `app/security.py`). `AuthService.refresh()` reads it, the auth guard redirects to `/login?signedOut=elsewhere`, and the login screen explains it. Without that the student meets a login form mid-task with no explanation, signs in again, drops the other device, and learns the app logs you out at random. **The same header says `disabled` (2026-10-17)** when the cookie is genuine but the Main Admin has disabled the account (`session_was_disabled`; never for a REMOVED account, which has not been told about a security issue); the guard sends them to `/login?signedOut=disabled`, which shows the same red-box sentence the password and code doors answer with (`DISABLED_SIGN_IN_MESSAGE`, "Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue.") and Google gives as `?error=sso_disabled`. A removed account hears `REMOVED_SIGN_IN_MESSAGE` and Google's `sso_not_enrolled` instead. `tests/test_disabled_account.py`.

`POST /api/auth/login` (password) is **always on in dev/CI and off everywhere else unless `PASSWORD_LOGIN=true`**: the `login` fixture in `tests/conftest.py` and the six test modules that use it authenticate through it, so deleting it would take the DB-backed suite and CI with it. The guard is `settings.password_login_allowed` — an allowlist of dev/CI environment names **or** the explicit flag, never `not is_prod`, so an unrecognised `ENV` (`staging`, `uat`, a typo, a blank from a broken deploy) still shuts the password door rather than opening it. A default production deployment answers 403 and names Google instead. Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `apps/api-py/.env`; blank means `GET /api/auth/sso/status` reports unavailable and the login screen's Google button renders disabled with the reason — nothing else is affected. Full notes, including the exact authorised redirect URI and a troubleshooting section: `docs/google-sign-in.md`.

**Running passwords in production is a supported choice, and the door is DERIVED, not toggled.** `password_door_open(db)` in `app/routers/auth.py` is the one answer `/login` and `/sso/status` both give: dev/CI always; `PASSWORD_LOGIN=true` always; `PASSWORD_LOGIN=false` never; and otherwise **open exactly when at least one account holds a real `scrypt:` hash**. That is deliberate and it is the whole design. `app.grant_access` and `app.seed_roster` mint accounts holding `SSO_ONLY_PASSWORD_HASH`, a sentinel that is not `scrypt:salt:digest`, so `verify_password` returns False for every password ever tried and a fresh deployment has no keys and a shut door. Nothing self-issues a key, so "a key exists" can only mean an operator issued one on purpose — and that act *is* the decision to open the door. A separate env switch would ask them to make the same decision twice, in a different tool, and on Fargate that second tool is a `terraform apply` (the env lives in the task definition; `deploy.yml` ships code, never infrastructure). Two sources of truth for one door is how a login form ends up 403ing over keys in circulation.

**Issuing a key: the plaintext never travels, the hash does.** `python -m app.set_password <email>` types the password **at a prompt, never a flag**, because a `--password` option puts the secret in shell history, in `ps`, and in the CloudTrail record of ECS task overrides; it enforces a 12-character floor and refuses the demo passwords published in this file; `--revoke` restores the sentinel and leaves Google sign-in working. For a browser-only operator, `set_password --print-hash` applies the same checks locally and prints the scrypt hash, and `app.grant_access --password-hash <hash>` (exposed as the ops-task workflow's `password_hash` input) writes it — a hash with a random 16-byte salt is safe in argv and CloudTrail in a way a password is not, and `grant_access` refuses anything that is not exactly `scrypt:<32 hex>:<128 hex>` so a password pasted into that box by mistake is rejected rather than stored. `--mentor <email>` puts a granted STUDENT into a granted mentor's group, because a demo mentor with nobody in their group correctly sees nobody (rule 2). And `app.seed` **still refuses on `ENV=prod`**, so `admin123` and friends cannot be what the open door admits (`director123` is still on `set_password`'s refusal list — the account is gone, but the string was published, so it must keep being refused) — that refusal is the point of the whole arrangement and must not be relaxed.

**Onboarding, forgot-password and change-password (2026-09-10) — OPTION B IS OVER.** Students hold passwords now. Google sign-in is untouched and still works for every role; what changed is that it is no longer the *only* door a student has, and that the mailbox proof moved from BEFORE the decision to AFTER it.

**The whole student path is: apply → a human decides → three steps → sign in.** `POST /api/register` applies the rule engine at submit time and the application lands in the review queue **immediately, with no email in between**. The Main Admin approves or rejects. APPROVE provisions the account and emails a setup link; REJECT **requires a reason** (422 without one) and emails it, because a rejected applicant is not a user and that mail is the only channel the product has to them. The link opens `/onboard?token=`, which is **three steps on one URL** (`app/routers/onboarding.py`): `/auth/onboard/start` takes the token plus the address typed back and mails a six-digit code; `/auth/onboard/verify` spends the code and returns a 15-minute **ticket**; `/auth/onboard/password` takes the ticket — **never the link** — and sets the password. Three purposes, three different proofs: the link proves somebody opened mail sent there, which a FORWARDED link also proves; the code proves the mailbox is readable *now*; the ticket is the only thing the password step accepts, so the code can never be skipped by holding the link. It **signs nobody in** — the flow ends at `/login`, so the new password is typed once at the ordinary front door, which applies the ordinary brute-force limiter, revocation and single-device retirement. One door, not two.

**EVERY BOX ON THE FORM IS COMPULSORY EXCEPT SPECIALIZATION (2026-09-16), and
the CV and the photo with them.** The owner's rule for `/register`. The typed
half is held at the schema — `RegisterIn` requires USN, phone, a personal
email and a LinkedIn profile, stripped and refused blank, and the two new
columns (`registrations.personal_email` / `linkedin_url`, migration
`a3f9c2e17b48`) are NULLABLE because the rule is about new applications and
the column is a promise about the rows already written; approval copies phone
and LinkedIn onto `student_profiles`, where the placement record already asks
for both. **THE TWO FILES ARE REQUIRED BY THE API TOO, SINCE 2026-09-22, AND
THE REQUEST IS ONE MULTIPART POST.** Until then they were posted to
`attach_document` AFTER the 201, keyed on the id `submit` minted, with the
form refusing to submit without them and a retry button on the result card —
and the office's queue still filled with applications that had neither, from
students who had filled in every box. Every way the second half could fail
left one behind (the edge refusing a body over its limit, a phone losing its
connection, the applicant closing the tab at "Try attaching again"), and one
case failed BY DESIGN: a rule that auto-approves decides the application at
submit time, `attach_document` refuses a decided application, so every
auto-admitted student's CV and photo were rejected a second after the 201.
`POST /register` now takes `RegisterForm` — `RegisterIn`'s fields as
multipart parts plus `cv` and `photo`, both required, as fields of the ONE
form model because FastAPI embeds a form model under its parameter name the
moment a second body parameter appears — and `_read_document` judges both
files (size after a `read(MAX+1)`, then the sniff, then the kind's own mime
list) BEFORE a row is written; the row, its two `registration_documents` and
their bytes land in one transaction, bytes unlinked on a rollback, and only
then does `_apply_rule` run, so `_provision_student` finds the documents it
moves onto the new student's uploads. A refused file is a 413 or 415 with no
application behind it, so the applicant resubmits the same form and never
meets the duplicate guard. `attach_document` stays as the REPLACEMENT path
(a held applicant asked for a better scan, and the rows from before), built
on the same two helpers so the two doors cannot disagree about what a CV is.
The form still checks each file's type and size before it posts and states
the accepted format and the cap on the dropzone itself (PDF, and PNG or JPG,
each up to `document_store.MAX_BYTES`). The reviewer's checklist keeps
`CHECK_DOCUMENTS`, a WARN and never a block, which can now fire only on a row
written before the rule. Course and Batch are required on the form WHENEVER
THE OFFICE HAS LISTED ANY under the chosen department, and not otherwise: a
box that cannot be filled cannot be compulsory, and a half-set-up college must
not refuse every applicant. `tests/test_registration_required.py` pins the
API half, `test_an_application_cannot_exist_without_its_cv_and_photo` and
`test_registration_documents.py::test_an_auto_approved_application_keeps_its_files`
the two halves of this rule.

**SPECIALIZATION IS A CHECKLIST, AND A STUDENT MAY TICK TWO (2026-09-22).**
Some students opt for a DUAL specialization, and the box was a `<select>`, so
they could name one of their two and the office learned of the other by phone
or not at all. `RegisterIn` takes `specialization_ids` — the ticks in order,
capped at `MAX_SPECIALIZATIONS_PER_APPLICATION` (two, because the office's
word is "dual" and the row has two columns; the cap is served to the form as
`max_specializations` on `GET /register/hierarchy` so the checklist and the
schema refuse at the same number) — and the legacy `specialization_id` is
folded in first, so an older client still works and a client sending both
with one pick has ticked one box. `_resolve_claim` writes the first tick into
`specialization_id`, which every reader that column already had (the scope
clause, the queue, the batch settling) goes on reading unchanged, and the
other into `registrations.second_specialization_id` (migration
`d4c8e1f7a2b9`, a sixth nullable SET NULL FK beside `c5f9a3e7d2b4`'s five —
a COLUMN and not a table, because two is the definition and a table would need
a verdict in both purge modules and both deletion walks before it could hold a
row). Both must sit under ONE course, refused otherwise; where the requested
batch pins one of the two, that one is moved to the front so the batch and
the column it has always been checked against still agree, and a batch
pinning a specialization the applicant did not tick is the contradiction it
always was. The reviewer's queue prints both ("Finance and Marketing", the
same " and " on the applicant's card, `core/specializations.ts`'s
`specializationLabel`) and gains a `dual_specialization` WARN, never a block,
because seating is by batch and a batch hangs on one specialization at most;
`registration_scope_clause` reads BOTH columns, so a reviewer scoped to the
second stream still lists the row.
`tests/test_registration_dual_specialization.py` pins all of it.

**THE SECOND CHOICE REACHES THE STUDENT NOW, AND THE MAIN ADMIN CAN SET IT
(2026-09-23).** Until then nothing on `students` carried it, so after approval
the dual choice lived on the application alone and the roster editor had no
way to assign one to a student whose second stream reached the office by
phone. `students.second_specialization_id` (migration `e2b7c4d9f1a6`, nullable
SET NULL FK, backfilled from approved dual applications) is the SECOND and
nothing else: the first is still the batch's own, read through the cohort
join, because a batch hangs on one specialization. `app/dual_specialization.py`
holds the three rules every writer shares — approval copies the tick the batch
does not already say (`second_for_seat`); `PATCH /admin/students/{id}` takes
`second_specialization_id` and refuses the batch's own stream or one of another
course (`refusal`); and any move into another batch, single or bulk, clears a
second the new batch contradicts (`settle_after_move`), so a row never reads
"Finance and Finance". The roster draws it as a second chip and the
Specialization filter matches either; the edit dialog offers the batch
course's other streams (`secondSpecializationOptions`, mirroring `refusal`).
The profile card and the 360 still read the batch alone.
`tests/test_student_dual_specialization.py` pins it.

**THE REGISTER FORM'S BATCH BOX IS THE YEAR AND NOTHING ELSE (2026-09-23).** It
drew each batch's `display_label` — "General MBA - Finance · 2026-28" — which
repeated the Course and Specialization boxes above it and, for a dual pick,
listed one year twice. `features/register/batch-years.ts` draws each year once
and turns the picked year back into the batch of the FIRST ticked
specialization, else the second, else a course-level batch, else a
department-level one; two streams in one year with nothing ticked is resolved
by asking for the specialization, never by guessing. The result card prints
the year too. `display_label` on `GET /api/register/hierarchy` is unchanged,
because the console's Registrations, Imports and Students screens read it.

**Why the confirmation link moved.** It used to run BEFORE the rule engine: `POST /register` wrote `PENDING_VERIFICATION` and waited. The reasoning was sound (approval mints a `users` row, so no address should auto-approve itself onto the roster) and the mail is the half that fails. On a deployment whose SES account is still sandboxed nothing can reach a student address at all, so **every** applicant sat invisible — the student saw a 201, the admin saw an empty queue, and the retry hit the duplicate guard's deliberately opaque 409, which reads as a broken form. Verified on production 2026-09-10: not one `GET /api/register/verify` in 30 days. A gate nobody can pass is not a gate, it is an outage. Migration `9b2d47f0ce15` moved the stuck rows into the queue; `/api/register/verify`, `EmailVerification`'s three helpers and `retention.sweep_unverified_registrations` are gone with the status that fed them.

**Changing a password later is the same idiom**: `POST /auth/change-password/code` mails a code to the address ON THE ACCOUNT (never one from the request), and `/auth/change-password` takes **one proof, never two** — `code` or `current_password`, and supplying both is refused, so a guessed code cannot ride a known password. Staff keep the current-password path; it is the door that still works when mail does not.

**STAFF still activate, and that refusal is not vestigial.** `python -m app.grant_access` prints an activation link for any staff account created without `--password-hash`, and `POST /api/admin/users/{id}/activation-link` (Main Admin) mints or re-mints it on screen — **the on-screen link is permanent, not a stopgap**, because "the email never arrived" is a support call the admin should be able to close by reading out a link. `issue_activation` still refuses a STUDENT: a student's equivalent is the onboarding walk, which proves the mailbox with a code first. Links are rows in `auth_tokens` (`app/models/auth_token.py`): stored as **sha256, never raw**; consumed by **one atomic `UPDATE … WHERE consumed_at IS NULL`** whose row count is the arbiter; issuing a new one **supersedes** every older live one; activation lives 7 days, reset 1 hour. Six-digit CODES bend two of those rules and the set that says which is `CODE_PURPOSES` — hashed WITH their row id (six digits are not unique across users, and `uq_auth_token_hash` is global), found by (user, purpose) and never by hash, predecessors DELETED rather than kept. A code purpose left out of that set would be minted with a bare sha256 and could collide with somebody else's identical six digits: a 500 in the face of a person who typed the right code.

`/auth/forgot` answers **the same 202 and the same words** whether the address is real, password-less or unknown, with the mail work in a background task so the timing matches too; it **now serves students**, who have a password to forget. `/auth/reset` puts every device out via `token_version`, kills other pending links and signs in nobody. Mail leaves through `app/mail_transport.py`: **Amazon SES** when `SES_FROM_ADDRESS` is set (task-role auth, no key to paste), otherwise a console transport that logs the message and keeps it in a bounded `outbox` — which is how a developer and the test suite read a link. **B3.7 SHIPPED ON 2026-09-15 and the sentences that said it had not are gone** (`config.py`, `leave_mail.py`, `.env.example`, `test_leave_mail.py`): the identity `sast-skills.com` is verified with DKIM, the account holds SES production access in ap-south-1, and the api task carries `no-reply@sast-skills.com`. Read `docs/ses-mail.md` before touching any of it — it is the decision record for **why the college's own `bgscet.ac.in` is NOT the sender** (its identity was left unverified and has been deleted; moving there costs three DKIM CNAMEs from college IT and a stack deploy, neither in this team's hands) and the runbook for `sesManaged`. **`LEAVE_MAIL_ENABLED` is true in production now and `settings.leave_mail_enabled` still defaults to FALSE**, and those are not in tension: the default is for a machine with no transport, where mail switched on writes a `mail_logs` row reading SENT about a message that reached NOBODY. One thing is still worth knowing before trusting that row anywhere: SENT means SES ACCEPTED it, never that it arrived. The sentence that used to follow it — that an address on SES's account suppression list is delivered nothing while `mail_logs` keeps saying SENT — is answered below, and it was answered because it happened. **That log line was going nowhere until 2026-09-10**: nothing ever called `logging.basicConfig`, so the root logger had no handler and sat at WARNING, and every `log.info` in `app/` was discarded in development AND in production. `app/main.py`'s lifespan configures it now — deliberately WITHOUT `force=True`, which removes handlers somebody else installed (it broke `test_boot_guard`'s caplog assertion while the message was still plainly on stderr). Screens: `/onboard` (three steps), `/activate` and `/reset` (one component, two modes), all outside the shell; `/account/password` inside it; and the login's inline "Forgot password?" form. Full record: `docs/institutional-spine-build-log.md`, round 4.

**AND IT IS HOW A PASSWORD-LESS STUDENT GETS THEIR FIRST ONE (2026-09-16).** Every account `app.seed_roster` and `app.grant_access` mint, and every student provisioned before 2026-09-10, holds the `google-only` sentinel and was never sent a setup link — so such a student could sign in with Google and reach a password by NO path: `forgot` skipped the sentinel in silence (option B's reasoning, "mailing a link would quietly turn it into a password account", which is now the intended outcome), `change-password` answered 409 naming "the link you were emailed", and the console has no button for it. From the student's side that read as "I typed the right address and the code never came". A STUDENT holding the sentinel is now mailed the SETUP link (`account_links.issue_password_setup`, the same `PURPOSE_ONBOARD` token and the same three-step walk provisioning sends, with a mail that says why it arrived rather than "your registration has been approved"); the signed-in `/account/password` screen offers an "Email me a setup link" button that posts the same endpoint with the session's own address. STAFF holding the sentinel are still sent nothing there: their first password is the activation link the admin holds and can read out, and that link sets a password by itself. It is deliberately self-service rather than a console button, because a student's walk needs the mailbox TWICE (link, then code) — a link read out on the phone helps nobody whose mail is not arriving, and the student is the one person who can tell whether that inbox works. `tests/test_passwords.py` walks a sentinel student from `forgot` to a working password.

**AN ADDRESS THE PROVIDER HAS GIVEN UP ON IS THE ONE MAIL FAILURE THAT LOOKS
EXACTLY LIKE SUCCESS (2026-09-17).** Reported from production: a student could
sign in with "Continue with Google" and no code ever arrived, on any path. The
password code was not the bug — that chain is correct end to end, and the
2026-09-16 setup-link branch above was already doing its job. What was wrong is
that **a hard bounce or a complaint puts an address on SES's ACCOUNT SUPPRESSION
LIST**, and SES then ACCEPTS every later send for it — a message id comes back,
`deliver_once` writes SENT — and delivers nothing, for ever. A single bounce
during the sandbox period suppresses an address that has been fine ever since.
So the onboarding walk, "Forgot password?", `/account/password` and the
setup-link button all told that student "we have emailed you a code", every
time, truthfully as far as REEP could tell, and Google sign-in kept working
because no mail is involved. **There was no error anywhere**: not a failed
request, not a FAILED row, not a log line, not an alarm. A failure whose only
symptom is that nothing happens will never be found by watching for errors.

`mail_transport.suppression_for` asks SES about the recipient immediately
before the send, and a suppressed address raises `SuppressedRecipient`
**instead of being sent** — which routes it into the FAILED row, the `error`
sentence and the `Mail send failed` CloudWatch line that every other mail
failure has produced since 2026-09-15. Nothing new had to be built downstream;
the case simply never reached what was already there. **The check FAILS OPEN**
(no grant, a throttle, an unreachable region → send anyway): failing closed
would turn one missing IAM permission into every student's link, which is a far
worse outage than the one it guards, and the exception message deliberately
carries the reason and the date but **never the recipient**, because
`deliver_once` puts it in a log line that carries no address by design.

**AND THE OFFICE CAN NOW SEE ANY OF THIS AT ALL.** `mail_logs` had been written
on every send since the Prisma days and **read by nothing** — no endpoint, no
screen, no script; `ix_maillog_sent_at` was added for "the ops mail screen" and
that screen was never built. So "the code never came" could not be answered
even at the first question, which is whether REEP tried. `routers/admin_mail.py`
and `/admin/mail` ("Email delivery") are the reader: the log, filtered; a live
**per-address** question to SES; and the lift, audited, because a suppressed
address otherwise needs somebody with an AWS console. It is `require_admin` and
**not a capability** — `mail_logs.recipient` is every address the deployment has
ever written to, which is a roster of people by another name. The suppression
answer is **asked, never stored**: it changes in both directions without REEP
being involved, so a column would be stale exactly when it mattered — and
`checked: false` renders as its own state, because "we asked and it is fine" and
"we could not ask" are opposite facts (`X-Reep-Scope`'s rule, again).
`ses:GetSuppressedDestination` and `ses:DeleteSuppressedDestination` are granted
on the api task role; **`ses:ListSuppressedDestinations` is deliberately not**,
and a synth guard keeps it off — the product only ever asks about an address it
already holds, and List would hand a compromised task every address the
deployment has ever bounced.

**What this still does not see**, said plainly rather than left to be
discovered: a transient bounce that does not suppress, and a message that was
accepted and delivered to a spam folder. The suppression list is where a
DURABLE delivery failure comes to rest, which is the one that produces "no code,
ever, on every path"; a message that bounced once and will arrive next time is
not that failure.

**A REJECTED APPLICANT MAY APPLY AGAIN (2026-09-16).** `registrations.email` was UNIQUE outright and `submit`'s duplicate guard read every row, so an address the office had rejected — for a mistyped USN, say — met the deliberately opaque 409 on its corrected second attempt, and the rejection mail had sent them to the same office; neither side could see why. Migration `d7e2f9a41c86` replaces the constraint with `uq_registration_live_email`, a PARTIAL unique on `email WHERE status <> 'REJECTED'` (the `uq_mentor_assignment_one_open_spell` shape: one LIVE row, enforced by the database so two submissions racing the guard cannot both land), declared on the model with the same predicate. The guard reads the same rule, the REJECTED row stays as the record of that decision, and the reviewer's checklist on the new row carries `prior_applications` — a WARN naming the reason given last time, never a block. `tests/test_registration_reapply.py` pins all of it, including the index against the real schema.

**A USN ANOTHER STUDENT HOLDS CAN BE CORRECTED ON THE APPLICATION (2026-10-16).** Approve refuses it (GUARD 3), and until this date the refusal said "correct the USN on this application" when nothing could, so Reject was the only button. `PATCH /api/register/{id}` takes `{usn}` and nothing else, only on PENDING_REVIEW or HOLD, upper-cases it, audits it as `CORRECTED`, refuses a USN another student holds, and answers the row WITH its checklist. The `usn_unique` line names the holder (name, address, `student_id`, and "Open their record" on screen) only for a programme-wide reach; a scoped reviewer is told the USN is taken and not by whom. **The 409 from `_provision_student` must never name the holder**: `_apply_rule` copies that sentence into the PUBLIC `decision_reason` when a rule's auto-approve is refused. `tests/test_registration_usn_correction.py` pins it. **A USN is stored UPPER CASE and compared upper case on both sides**: `app/usn.py`'s `normalise_usn` for every writer, `usn_is` for every "who holds this USN" lookup. The form stored it as typed until the same day, so `1mp25ba033` slipped past a student holding `1MP25BA033`; `usn_is` also finds the lowercase rows written before that. The unique index on `students.usn` is still case-sensitive, so a new lookup that compares with `==` reopens the hole. A rule's `usn_pattern` is compiled `re.IGNORECASE` for the same reason: a pattern written in lower case would otherwise match no upper-cased USN at all.

The endpoint carries a brute-force limiter, and **it is keyed on the account, never on the source address**. Behind the ALB `request.client.host` is one value for the entire internet: an address bucket there is a global outage waiting to happen — ten wrong passwords from anyone locks out every student at once — and raising the limit until that stops hurting makes it stop working. This was written the wrong way first and the suite caught it immediately (24 failures became 134, because every `TestClient` request shares one peer address). Ten failures per email per fifteen minutes, only failures count, a success returns the budget, and the 429 names Google because that door is not gated by this counter — so an attacker who burns a known address's budget costs a real user a redirect, not their access. What no in-process counter can bound behind a proxy is **spraying** (one guess each against a thousand accounts); that control belongs at the edge, as a WAF rate rule.

## Deleting people and colleges from the console (2026-09-16)

The owner asked for a Delete button on Students, Faculty and Colleges, with a
second factor for the office, and for "delete, but not permanently" to be a
real choice. Both are `routers/admin_deletion.py`, Main-Admin-only
(`require_admin`, never a capability: deleting a person is the office's act
and a capability is a thing the office hands to somebody else), and every one
of them writes an audit row.

**REMOVE and DELETE are not degrees of one thing, and the dialog draws them as
a choice.** REMOVE writes `users.deleted_at` / `deleted_by_user_id` /
`delete_reason` (migration `c7d3e9a1f5b2`): the person leaves every roster,
picker and queue, cannot sign in by any door, every row they own stays exactly
where it is, and `POST .../restore` brings them back with nothing lost. It asks
for a reason in words, disabling's rule. It is a column BESIDE `disabled_at`,
not a reuse of it: a disabled account is meant to stay listed, greyed with its
date; a removed one is meant to leave the lists; and an account that was
disabled and then removed comes back disabled, which two columns can say and
one cannot. **Every sign-in door reads `User.barred_at`** (`deleted_at or
disabled_at`) — `refuse_disabled_sign_in`, the Google callback, onboarding,
reset, activation, the handover grant — and `security.account_state` reads
both columns in its one query, so a removed account's cookie dies on its next
request. The lists that hide a removed account: the roster (`?removed=true`
lists exactly those, for Restore), the faculty list (same), mentor-load,
unassigned students, a mentor's mentees, and `mentee_count` — so a faculty
member whose only mentee was removed holds no mentor functions until the
student is restored; the `students.mentor_id` pointer is never touched, which
is what makes Restore exact.

**DELETE FOR GOOD is `app/account_deletion.py` over `app/deletion_walk.py`,
and the walk is the design.** `purge_people` and `purge_students` empty
TABLES; one person is a ROW and its dependants, and the schema already
answers "which rows go with it" for 148 of its 184 foreign keys with an ON
DELETE clause. The walk starts at the account's own rows (`users`,
`students`, `mentors`, the `registrations` that became or named it, its
`mail_logs`, its idempotency keys), follows every CASCADE, counts every SET
NULL as a record that survives without the name, and REFUSES on any column
with no clause and no entry in `ACCOUNT_POLICY` — the purge modules' "a table
nobody classified aborts the run", applied per column. The policy decides the
undeclared ones (`students.user_id`, `mentors.user_id`, `login_days.user_id`,
the two history tables, the RESTRICT notebook columns — the person's own
instrument, gone with them; the four `created_by_user_id` columns and
`students.mentor_id` — cleared) and overrides two SET NULLs the schema
carries (`import_rows.student_id`, `platform_candidates.user_id`: a line
holding the student's USN and marks, or their name and address, left behind
under somebody else's receipt has not deleted the student). Files go before
rows, through `purge_people`'s own destroyers handed the subset, and the
manifest is stamped released. On a FACULTY delete the mentees are released
(counted), and their meeting notes ABOUT students are DELETED — `mentor_notes.mentor_id`
is CASCADE and NOT NULL, so they cannot survive the group row — which the plan
says in words and which is the reason REMOVE exists. `tests/test_account_deletion.py`
runs the real delete against the real schema in a rolled-back transaction, pins
the policy against every FK in the metadata, and cross-checks the walk against
`purge_students.STUDENT_VERDICTS`: every table the cohort purge would scope is
either reached or named in `NOT_PER_ACCOUNT` with a reason.

**THE CODE IS THE SECOND FACTOR.** `POST /api/admin/deletions/code` mails a
six-digit code (`PURPOSE_DELETE_CODE`, in `CODE_PURPOSES`, so it is hashed
with its row id and found by `(user, purpose)` like every other code) to the
office account's OWN address, never one from the request; it lives
`otp_code_minutes`, is spent by exactly one act through `consume_user_code`'s
atomic UPDATE, and is consumed AFTER every refusal and BEFORE anything is
destroyed. A wrong code is a 403 that names nothing about the target. Requests
are throttled per admin (`DELETE_CODES_PER_HOUR`). The plan endpoints
(`GET .../delete-plan`) are what the dialog prints before the button — the same
walk the delete runs, so the sentences and the numbers cannot drift.

**A COLLEGE DELETES ITS STRUCTURE AND REFUSES WHILE ANYBODY IS UNDER IT.**
`app/college_deletion.py` walks from `colleges` with `COLLEGE_POLICY`:
departments, courses, specializations, batches and the configuration hung on
them (the calendar, interview policies, stage rules, badge-course map) go;
catalogue rows that merely pointed at the college (jobs, tracks, criteria,
approved certifications, bank questions, import runs) stay and lose the
pointer, counted; and `students.cohort_id`, `students.department_id` and
`users.department_id` are `REFUSE_IF_ANY` — a college with a student seated or
a faculty account filed under it is refused in words, re-checked inside the
transaction, as is an application still in the review queue. **The bulk form
is `python -m app.purge_colleges --keep 1MP`**: every college except the one
named goes, each in its own transaction, dry run by default, `--apply` plus
`--i-understand-this-is-permanent`, an unknown `--keep` code refused rather
than read as "everything"; on the Ops task menu as `purge-colleges[-dry-run]`
with `college_code` naming the SURVIVOR and its own typed sentence.

**Leave is one signature, the Main Admin's (2026-09-16).** The two-signature
chain (SUBMITTED → FIRST_APPROVED → APPROVED, a mentor or a scoped grantee
first and a different approver second) deadlocked every staff request on a
one-admin deployment, and the owner's answer was a single decision by the
office. `_require_leave_approver` is `require_admin`; `_assert_can_decide` —
still THE gate the paper, the attachments, the alternate table and the balance
read import — admits the Main Admin and nobody else with the same flattened
404; `decide_leave` writes `first_*` and the terminal status in one step and
completes a legacy FIRST_APPROVED row in the `second_*` slot without rewriting
the first stamp. `mentor.leave_approve` LEFT the catalogue and
`MENTOR_FUNCTIONS` (three now), and migration `d8b1f4c2a7e9` revoked every
live grant of it with the reason written on the row. `LeaveStatus.FIRST_APPROVED`
and the `second_*` columns stay for the rows that carry them. The faculty
leave screen no longer has an approver's queue at all; the admin screen's
"Sanction" is the whole chain.

**AND THE OFFICE MAY HAND THAT ONE SIGNATURE TO A FACULTY MEMBER
(2026-09-17).** `admin.leave_approvals` ("Approve leave") is a PROGRAMME
capability in the catalogue: the Main Admin holds it by baseline (nothing about
the office's path moved) and grants it in Governance to a named faculty
account, with a reason, on the trail, revocable the same hour.
`_require_leave_approver` is `require_mentor` plus `require_capability` on
that key; `_assert_can_decide` admits a holder as `SIGNED_AS_DELEGATE`, which
the paper prints beside the name. A delegate sees the office's whole queue
(minus their own requests, which nobody may decide) — the reach is the
programme, because a leave request hangs on no rung this router narrows by.
**It is NOT `mentor.leave_approve` coming back**: that key was DERIVED from
mentoring somebody, reached one group's queue and was one half of the chain
that deadlocked; this one is a decision the office makes about a person, and a
stray row still naming the retired key opens nothing, which
`tests/test_leave_chain.py` pins beside the delegate's own walk. `carries_pii`
(a leave reason is routinely medical), so a DEPUTY's grant waits for a second
signature and the Main Admin's is live at once. The route guard on
`/admin/leave-approvals` is `capabilityGuard`, the row is under "Granted
access" for a faculty holder, and the policy and calendar buttons on that
screen stay the Main Admin's (`require_admin`) and say so.

**LEAVE MAIL HAS TWO MORE AUDIENCES, AND "ON LEAVE TODAY" GOES ON THE LEAVE DAY
(2026-09-24).** Both are in `app/leave_mail.py`, both run only where
`LEAVE_MAIL_ENABLED` is on, and both are for FACULTY (role MENTOR) requests
only, because a student's absence is not something to broadcast. (1) A faculty
application is mailed to everybody who can decide it (`leave_approvers`: every
ADMIN, plus every faculty member `has_capability` says holds a live
`admin.leave_approvals`, so the list and `_assert_can_decide` agree), never to
the applicant. (2) **An approved faculty leave is announced to every other
active faculty member ON EACH DAY OF THE LEAVE, and never when the office
approves it.** The owner's words: the mail says "this person is on leave
today", so it goes on that day, not at approval. `python -m app.leave_today_job`
sends it at 07:00 IST (`reep-leave-today-daily`, `cron(30 1 * * ? *)`, gated on
exactly what puts `LEAVE_MAIL_ENABLED` in the task definition). The one other
sender is `decide_leave`, and only when the approval lands on a day the leave
already covers, because the morning run has passed by then. Every mail is keyed
`leave-today:{id}:{day}:{recipient}`, so the approval and the job never both
send one, and a rerun sends nothing twice (and retries nothing: a FAILED row
holds its key). **The broadcast carries the name and the dates and NOT the
printed option**: the applicant's own mail says "casual leave", but a mail to
forty colleagues must not say "loss-of-pay" or "restricted holiday". Neither
mail carries the `reason`. Both are background tasks with their own session, so
neither the Apply button nor Sanction waits on SES.
`tests/test_leave_on_leave_today.py` pins it, including that an approval ahead
of time writes no `leave-today:` row at all. **The applicant's own three mails
(received, sanctioned, not approved) name NOBODY (2026-09-25)** — not the
placement office, not an approver, not a department, and the greeting is a
bare "Hello,": a rejection that read as a named office turning somebody down
was reported, and the owner's rule is to say what happened to the request and
nothing about who. `test_leave_mail.py::test_the_applicants_mail_names_nobody`.

**A LEAVE DECISION CAN BE CHANGED AFTER IT WAS MADE (2026-10-13).** Until
then APPROVED and REJECTED were terminal and a sanction given by mistake needed
a database edit. `POST /api/leaves/{id}/change-decision` takes `action` REVOKE
(APPROVED → the new `LeaveStatus.REVOKED`) or REOPEN (REJECTED or REVOKED →
SUBMITTED, signatures cleared, decided again through `/decision`), and a
`reason` in words that the applicant reads on their screen. The gate is
`decide_leave`'s exactly — the Main Admin or a holder of
`admin.leave_approvals`, never on one's own request — with one extra rule:
**once a leave's first day has arrived (`local_today`), only the Main Admin may
revoke it, and only with `confirm_started`**, because those days were taken on
the office's word. Reopening refuses a request whose days now clash with
another live one. The row keeps only the LATEST change
(`decision_change_kind/_at/_by_user_id/_reason`, migration `daaeb23b750e`);
the decision each change undid is the before-image in `audit_events`. REVOKED
is not live, so the days are free and the "on leave today" job stops on its
own; the allowance is NOT touched, because `consumed_days` is the office's own
number and approval never wrote it either. Mail: the applicant and every
colleague named to cover (`alt_rows[].user_id`) are told, carrying neither the
purpose nor the change reason; a REOPEN also mails the approvers, as a fresh
faculty application does; `decision_revision` suffixes the dedupe keys
(`leave:{id}:{status}:r{n}`, `leave-approval:{id}:{approver}:r{n}`) so a second
sanction or a reopened queue entry is not swallowed by the first one's row. `tests/test_leave_decision_change.py` pins it.

**The signature image: normalised on the way in, never silent on the way
out.** `PUT /api/staff/signature` re-encodes what the sniffer accepted as a
flat, upright RGBA PNG through Pillow (EXIF rotation applied) before it is
stored, best-effort — a file Pillow cannot read is stored as uploaded with a
warning, never refused on Pillow's word alone. `leave_paper._image` tries
ReportLab, then a Pillow re-encode, and LOGS when neither can draw the file;
it used to swallow the exception, which is how "my signature is not on the
PDF" reached the office with no line anywhere saying why. The faculty leave
form now says whether a signature image is on file, with the link to add one
(`/mentor/signature`, in the account menu for staff and the Main Admin), and
the admin's Sanction note says the same for the PROGRAM DIRECTOR block.

### Runbook: the call sounded fine but saved nothing

The worst failure mode in this stack is silent — the conversation is perfect in the room and empty in the database, because transcript writes are deliberately fire-and-forget so a bad write can never kill a live call. After a test interview:

```sql
select channel, count(*), max(created_at) from messages group by channel;
```

No `interview` rows, or a stale `max(created_at)`, means turns are being dropped. The cause is logged as `Dropped interview turn`, with its exception. The interview also keeps a record of its own (`interview_sessions`), where the `turns_emitted` vs `turns_persisted` pair makes the same gap visible without a join — if those two disagree, writes are failing.

### The assistant screen is the mock interviewer (2026-08)

`/student/assistant` is the realtime mock interviewer — and since 2026-09-15 so is the "Mock interview" tab of the floating dock on every student screen; both render `shared/interview-room/`. It is a WebSocket to `/api/interview` relaying 24 kHz PCM to a speech-to-speech model and back. It runs **inside the API process** — it is not a fifth process, and it needs no extra venv. Readiness is asked of the engine that is actually running (see below), and blank means `GET /api/interview/status` reports unavailable and the socket closes 4001, with nothing else in the dashboard affected. Full notes: `docs/interview-assistant.md`.

**Two engines, one setting, one contract.** `INTERVIEW_ENGINE` picks between **`nova`** (the default — `app/interview_nova.py` → **Amazon Nova 2 Sonic** on Bedrock, `amazon.nova-2-sonic-v1:0` over `InvokeModelWithBidirectionalStream`) and `local` (`app/interview_local.py` → nothing leaves the machine). Both take the same constructor and return the same `(code, reason)` from `run()`, and both emit the same DOWNSTREAM event names, so the router, the caps, the recorder, the writers, the three close layers and the Angular client never learn which one spoke. What they share — the persona, the payload records, the close codes, both concurrency caps, the scorecard parse — lives in **`app/interview_core.py`**, imported and never copied: a parallel `_TurnRecord` would drift the moment either side gained a field, silently. An unrecognised value falls back to the default **with a warning**: `INTERVIEW_ENGINE=loca` must not quietly leave the machine.

**A THIRD ENGINE, `openai`, ran this interview until 2026-09 and is gone.** `app/interview_relay.py` (3 968 lines) and its test module were deleted, along with `OPENAI_API_KEY`, the realtime model/voice/beta-header settings, the server-VAD tuning, the transcription and `response.create` deadlines, and the per-row OpenAI `voice` column on the Specialization Matrix. The reasoning that engine produced did NOT go with it: `docs/interview-engine-v3.md` is still the design record for the phase machine, the deterministic word gate and "one open question at a time", all of which Nova inherits. What died with it is the mechanism, not the argument — read that document before changing the arc. `OPENAI_API_KEY` is left in the operator-owned AWS secret rather than deleted by an apply (`infra/aws/secrets.tf` says why); nothing reads it.

**Nova has no API key, and Nova owns the turn.** The stream is signed with SigV4 from the standard AWS chain, so an AWS-hosted REEP grants the task role `bedrock:InvokeModelWithBidirectionalStream` and pastes nothing; `NOVA_SONIC_REGION` (falling back to `BEDROCK_REGION`, then `AWS_REGION`) is the only thing that must resolve, because this process composes the endpoint. Rule 1 is unchanged — Bedrock is off-machine, so no student record enters the session, and the module imports no ORM model. What differs from the retired relay is forced by the API and is deliberate: Nova 2 Sonic has no `create_response: false` and no `session.update`, so the **phase machine still ticks on accepted answers** (same `classify_answer`, same thresholds, same persona verbatim) but the model is steered in two different ways. **The question phases are briefed once, at the handshake**: `build_arc_briefing` in `app/interview_matrix.py` appends the probing and deep-dive directives to the system prompt with the word gate's own counting rule, and the model moves through them on its own. Only the beats that must land at a fixed point — the invitation to ask questions, the verdict, the clock's forced verdict and the scorecard request — travel as a **control note** (a cross-modal text input prefixed `[INTERVIEW CONTROL]`, a fixed directive and never a word the student said), and every one of them is **held while a model turn is in flight** (`completionStart` → `completionEnd`) and sent in the gap between turns, with the directive saying what to do with a question the model has just asked. **A NOTE IS A USER TURN, AND THAT IS THE WHOLE CONSTRAINT (2026-09-16).** Bedrock delivers the student's ASR transcript as the FIRST block of the completion in which Nova is already composing its reply, so a note sent when the transcript lands barges in on that reply — Nova abandons it, emits its interruption marker, and the browser's queue is flushed: the voice does not fade, it vanishes mid-word and a different question follows (reported twice from real interviews). A note held until the reply ends provokes a second question on top of the first, which is why the phase directives moved into the briefing rather than into the hold, and why the two stop beats stay engine-driven rather than briefed: a model that decided for itself when the questioning was over could decide it one answer before the engine does and then wait for a verdict note that never comes. The first fix for the symptom keyed the hold on the AUDIO block, which arrives AFTER the transcript, and its test fed the events in the reverse order — it passed with the bug intact. `tests/test_interview_nova.py` and `test_interview_simulation.py` now feed the documented order and assert that nothing goes upstream between `completionStart` and `completionEnd`. Nova offers no alternative: it has no client-owned-turn mode, and non-interactive text (`interactive: false`) is documented for the system prompt and pre-audio history only, so nothing in AWS can be configured to change this — the fix is entirely in how the engine sequences what it sends. A clarification turn is NOT injected (the model has already begun replying; a second directive is a second question) — the briefing tells the model to ask for more itself, and the turn is still recorded `too_short`/`filler` and still does not advance the arc. The scorecard is a **tool call** (`submit_scorecard`), because Nova speaks everything it generates and a text-only request would read the JSON aloud; it is parsed by the relay's own `_parse_report`, never spoken, and salvaged from speech if the model ignores the tool. Voices are a different vocabulary from OpenAI's, so each matrix row carries `nova_voice` too (HR `kiara`, DM `tiffany`, BA `arjun`, FA `matthew`). **Bedrock closes the stream after 8 minutes**, which is shorter than `INTERVIEW_MAX_SECONDS` (900): the engine caps the session at `NOVA_SONIC_CONNECTION_SECONDS`, reports THAT to the client's countdown, and forces the wrap-up 90 s early so the verdict and the scorecard fit — an interview cut off mid-verdict is the exact failure the phase machine exists to prevent.

Turns are persisted through `app/conversations.py` with `channel='interview'`, so the runbook query above still answers "did it save anything" — group by `channel` and look for `interview`. The writes are fire-and-forget for the same reason the voice ones are, so the same silent failure applies; the cause is logged as `Dropped interview turn`.

**The v3 engine: the relay owned the turn (RETIRED 2026-09, and the reasoning outlives it).** The session runs `turn_detection.create_response: false`, so upstream no longer asks the next question by itself the moment the student stops speaking — it did, and that is why the phase directive was structurally one response late and why a cough could create a question. Now the relay waits for the transcript (on a deadline, because a wait nobody bounds is an interview that never continues), judges it with a deterministic word-count gate rather than a hot-path model call, ticks the phase machine, and only then issues **one** `response.create` from **one** call site — which is what makes "one open question at a time" a property of the call graph instead of a sentence in the persona. If a second `response.create` site ever appears after the handshake, that invariant is gone and no test will notice. The arc is still `opening → probing → deep_dive → wrap_up` on each *accepted* answer, steered by an instructions-only `session.update` per phase change; the student picks HR, Digital Marketing, Business Analytics or Financial Analytics and the client sends it as `?specialization=` (no param runs the generic interview, which never reaches wrap-up and so never produces a report; an unknown key is refused with close 4010). At wrap-up the model speaks its verdict, and then one further **text-only** `response.create` in the same session produces a strict-JSON scorecard — parsed defensively, never spoken, never written into the chat history, and persisted whatever happens to it. The arc now opens and closes like a real interview: OPENING is a greet/self-introduction beat (the hard scenario question moved to PROBING), and the tick into WRAP_UP first asks "any questions for us?" — the student's reply, never word-gated, earns the verdict, and only the verdict's `response.done` (gated on `_verdict_requested`) requests the scorecard. Voices are per-specialization on the matrix row (HR `coral`, DM `marin`, BA `cedar`, FA `ash`), set in the single startup `session.update`, with `OPENAI_REALTIME_VOICE` as the generic fallback (validated against the known set, logged fallback to `alloy`); `_advance_turn` remains the single post-handshake create site, the invite/verdict beats included. Instructions are still the base persona **verbatim** plus a fixed specialization block, a phase directive and a fixed clarification override; **no student transcript is ever composed into them**, which is a guardrail of shape rather than of necessity — the moment student text goes into an instruction string, the next editor puts a resume there. Rule 1 is unaffected.

The interview now leaves a **record of its own**, in four tables (`app/models/interview.py`) that are *in addition* to `messages`, never instead: `interview_sessions` (one per interview — the terminal status, the phase it reached, `turns_emitted` vs `turns_persisted`, and the consent grant), `interview_turns` (the phase a turn happened in, whether the transcriber actually heard it, whether it advanced the arc — an empty `content` is legal and means exactly that), `interview_evaluations` (the scorecard, with **nullable** scores, because a missing score and a zero mean opposite things to a mentor), and `interview_consents`. The student reads their own at `/student/interviews`; staff read them through rule 2's gate. A `running` row that is never closed is a record that lies, so three layers close it — the relay's finalizer, the router's `finally` backstop, and retention's orphan sweeper for the process that was killed — all idempotent against each other by one `AND status = 'running'` predicate. The whole record is deleted after `INTERVIEW_RETENTION_DAYS` (180).

**Consent is a row and the socket enforces it.** No live `interview_consents` grant for the current `INTERVIEW_CONSENT_VERSION` and the interview never opens — close **4013**, refused before anything is written; revoked while it runs and the heartbeat notices within a minute — close **4014**. Three separate booleans (live AI, store transcript, store audio), because they are three different disclosures and one boolean makes "they consented" unfalsifiable. `interview_sessions.consent_id` pins the exact grant, so *"was this student consented, to what wording, at the time of interview X"* stays answerable after it has been revoked. Opening fails **closed** and the mid-interview check fails **open**, deliberately: "we could not check whether they agreed" must never start an interview, and a database hiccup must never end one that a real grant authorised.

**WHAT IS CONSENTED TO IS THE COLLEGE'S NOW, AND THE ROW IS AN ACKNOWLEDGEMENT (B6.1, 2026-09-13).** `interview_policies` (`app/models/interview_policy.py`, resolved by `app/interview_policy.py`) holds one row per `(college, course)` — `store_transcript`, `store_audio`, `retention_days`, `daily_cap`, `attempt_cap`, `time_limit_seconds` — edited through `PUT /api/admin/interview-policies/{college}[/{course}]` behind the new `admin.interviews` capability. **No row is seeded and the absence of one IS the default**: a deployment that never opens the policy screen behaves exactly as it did before the table existed, and the console's "not configured" state stays reachable rather than being a row holding the defaults. The client posts `POST /api/interview/consent` at Start with **the version string and nothing else**; the server copies the policy's two storage scopes onto the row and writes `scope_live_ai` true. **The three booleans survive** — they are what gets copied — and a stale bundle that still sends its own is neither refused nor obeyed. The POST is **idempotent when nothing changed**, and that is load-bearing: superseding an identical acknowledgement would stamp `revoked_at` on the row a RUNNING interview is pinned to and close it 4014 from a second tab. **`DELETE /api/interview/consent` is GONE and answers 405 for everyone** — deleted rather than made to 403, `admin_students.py`'s precedent, because a capability refusal would mean the endpoint is still there waiting for a grant. 4014 therefore fires when the pinned grant is superseded by one covering LESS (`_successor_covers` in `routers/interview.py`), which is the compatibility board's "a policy change stops a running session with 4014 only when it removes a scope". `store_transcript=false` suppresses **both** the `messages` row and the `interview_turns` row and stamps `interview_sessions.transcript_suppressed` — without that flag `turns_emitted` > `turns_persisted` would fire the runbook above on every interview at such a college, and "we chose not to keep this" must never look like "we lost this". The report is still written.

**THE TRACK IS READ FROM THE COURSE RUNG TOO, AND `interview_tracks.course_id` HAD BEEN DEAD THE WHOLE TIME (2026-09-15).** `_default_track` matched a batch's SPECIALIZATION and nothing else -- a track mapped to it, then the specialization's own `code`. The column `interview_tracks.course_id` was written by the admin screen, returned by the track API and rendered as a mapping, and **no reader ever looked at it**: a track mapped to a course reported itself mapped and preselected nothing. B6.6's shape exactly, found the same way, by trying to file a real catalogue. It is not a rare shape either -- a specialization is OPTIONAL (`HIERARCHY_LEVELS`), and a two-year programme that IS the qualification (BGSCET's Digital Marketing and Logistics & Supply Chain MBAs) has no specialization under it to hang a track on, because there is nothing to specialise into. So every student on such a course fell through to the generic interview and could never be scored. It now tries four rungs, most specific first -- track mapped to the specialization, the specialization's `code`, track mapped to the COURSE, the course's `code` -- and **none of them is a guess**: every one is an exact, case-folded match on a code somebody typed on purpose, and no match still means None and the picker stays. Specialization still wins where a batch names one, the same precedence `ancestry_of_student` applies to the two department pointers.

**THE STUDENT GETS TO FINISH, AND A COUGH GETS THE QUESTION BACK (2026-09-17).** Three things arrived together after real interviews reported "the voice drops, and it goes to the next question". `NOVA_SONIC_ENDPOINTING` defaults to `LOW` — AWS documents the levels as the pause Nova waits for before taking the turn (HIGH 1.5 s, MEDIUM 1.75 s, LOW ~2 s), and at MEDIUM a student gathering an example was answered over the second half of the answer. `classify_answer` has a `skipped` verdict (`is_skip_request`: "next question", "skip", "pass" and friends on a transcript of at most twelve words) that counts for nothing and is not a failed answer; the arc briefing and the persona's turn-taking note (`_TURN_TAKING_NOTE`, both interviews) tell the model to move on without pressing, so the record and the room agree. And when Nova's interruption marker is followed by a transcript that was `empty`, `filler` or `too_short` — a chair, a "hmm", not the first word of an answer — the engine steers `resume` (HELD like every note, sent in the gap after the reply) so the question Nova abandoned and the browser flushed comes back; a transcript that is the interviewer's own last sentence (`_looks_like_echo`, a run of six or more consecutive words covering 80% of it) is recorded `echo`, counts for nothing and gets the same recovery, because loud speakers were making the model answer its own question and advance the arc on it. The client's local barge-in needs 200 ms of sustained level now rather than 120 — a cough is 120 ms. `tests/test_interview_nova.py::TestAnInterruptionThatWasNotAnAnswer` pins all of it.

**THE INTERVIEW IS IN ENGLISH, AND THE PROMPT IS THE ONLY PLACE THAT CAN SAY SO (2026-09-17, the same evening).** The first interview after that deploy came back with five student turns in Devanagari — "हेलो हाई मैं दर्शन हूँ" for an Indian-accented "hello, hi, I'm Darshan" — and no interviewer turns at all. Nova 2 Sonic "supports multilingual with automatic language detection and switching", `kiara` and `arjun` (HR, BA) are its en-IN AND its hi-IN voices, and there is no language parameter on the session, so `_TURN_TAKING_NOTE` (now "## Language and turn-taking") pins English and tells the model to ask, in English, for English. The same note says to ALWAYS answer out loud: its first draft said "wait for the student", which a model can read as permission to say nothing, and an interviewer that says nothing is a student saying "next question" into silence. And `classify_answer`'s word pattern was `[a-z0-9']+` — an ENGLISH word pattern — so every Hindi answer was recorded `empty`, a false fact about five audible answers; `words_of` tokenises by Unicode category (letters, digits AND marks — Python's `\w` drops Hindi vowel signs and shatters "प्रश्न"), and the skip phrases carry "अगला प्रश्न" so the record is true whichever language the student used. `GET /api/mentor/students/{id}/interviews` serves `turns_emitted` / `turns_persisted` now and the open record prints "Ended: … · N turns, M saved", because that pair is the runbook's way of telling "the interviewer never spoke" from "its turns were dropped" and it was reachable only from a database client.

**THE ECHO GATE IS REFERENCED TO THE PLAYBACK NOW, AND THE ROOM HAS A SPEAKER / EARPHONES SWITCH (2026-09-22).** "The voice drops on speakers, and even on earphones it breaks" was the browser's echo gate, not the network and not the relay. The gate holds the uplink while the interviewer is audible so Nova never hears its own voice as a barge-in (it abandons the question, the relay flushes the browser's queue, the question is gone). It decided "audible" from `isPlaying`, which is true the moment a buffer is SCHEDULED, a 140–300 ms jitter-buffer lead plus the device's output latency before any sound; it spent its five calibration chunks measuring that silence, learned an echo level near zero, and then opened on the interviewer's own first syllables — on nearly every question on a phone, and again after every resume, which is the chop. Its echo reference was also capped at 0.03 RMS, below a speaking voice, so a loud speaker cleared it even when calibration worked. A replica of that gate driven through the spec's simulated room fires 5–20 times per 20 s of interviewer speech at speaker-level coupling and never at earphone-level coupling — the symptom exactly. `apps/web/src/app/core/echo-gate.ts` replaces it: the player records the level of every buffer it puts on the clock (`FarEndTimeline`), the worklet stamps each microphone chunk with its capture time, and `EchoGate` judges the chunk against the loudest playback that can be in it — reaching back by the device's `outputLatency` — times an echo **coupling** learned once per session from a conservative prior (2.0), so the bar follows the interviewer's loudness and nothing is measured at the top of a response. **The timeline is pruned against the query clock, never its newest entry**: Nova delivers twenty seconds of speech in three, and pruning against the newest entry discarded audio that had not played — the spec caught it. Earphones need no gate, and `setEchoSuppression` had **no caller**, so a breath on a headset mic paused the interviewer: `shared/interview-room/audio-route.ts` is the switch, defaulting to Speaker, remembered on the device, preselected to Earphones from the browser's device names, and read from the service so the page and the dock agree. A device-paused `AudioContext` is resumed (visibility, tap) instead of flushed, because a suspended clock loses nothing; the context asks for `'balanced'` latency, not `'interactive'`. The browser sends its counters as `reep.client.stats` after every interviewer turn and on End, and the relay prints them in the `Interview ended …` line as `client=` — until now they were a console line on the student's own machine. Server side, `stopReason: INTERRUPTED` without the text marker now stamps the interruption (so `resume` fires), the echo check falls back to the SPECULATIVE text of a turn cut before its FINAL text (so the echo of that question is `echo`, not an answer that advances the arc), the `reep.mic.gate` counter reads the `state` the browser actually sends, and uvicorn runs with `--ws-per-message-deflate false` because zlib over every PCM frame on half a vCPU bought nothing. `docs/interview-assistant.md` has the diagnosis.

**Two ceilings, not one (B6.4).** `daily_cap` counts **completed** interviews in the rolling 24 h — a dropped call no longer costs a student a turn — and `attempt_cap` counts **every session row**, because each one billed an upstream handshake and "a cap that only counts clean finishes is a cap a crash loop never hits". `ck_interview_policy_bounds` refuses `attempt_cap < daily_cap`, which would make the allowance unreachable. The deployment defaults are `INTERVIEW_MAX_PER_STUDENT_PER_DAY` (8) and `INTERVIEW_MAX_ATTEMPTS_PER_STUDENT_PER_DAY` (20). `POST /api/admin/students/{id}/interview-cap/reset {reason}` writes `interview_cap_resets` (reason mandatory, in words, B3.1's rule — this is the other console action whose effect is invisible a day later) and the count's window becomes `GREATEST(now - 24 h, the latest reset)` — an **extra lower bound**, so a second reset can only move it forward and a student mid-day never loses attempts already counted.

**Audio: off, and "off" is now THREE independent switches** (it was two until B6.1). Nothing is captured unless `INTERVIEW_RECORDING_ENABLED=true` (the operator's), *and* the college's `interview_policies.store_audio` is true, *and* the student holds a live grant whose `scope_store_audio` is true — which since B6.1 is a copy of the college's decision that the student was shown and acknowledged, no longer a checkbox they tick. All three are read in `recorder_for`, the one function that answers "when does REEP record a student's voice"; the college's is passed in from the advisory-locked transaction that opened the interview rather than re-read, so an edit landing mid-handshake cannot build a recorder the rest of the session does not expect. The panel's copy says plainly that staff can listen. NONE of the three is true in a default deployment. When all three are, `app/interview_audio.py` writes two WAV files per interview (one per speaker, never mixed — the two directions are not time-aligned), capped by `INTERVIEW_RECORDING_MAX_BYTES` with a truncation flag rather than a silent cut, retrievable only by whoever holds `admin.interview_audio` (the Main Admin by baseline; a MENTOR only by an explicit grant) and deleted on the same 180-day clock. Branch on `interview_sessions.audio_recorded`, **never** on `audio_path IS NOT NULL` — a NULL path collapses four different facts into one. This overrides `docs/interview-engine-v3.md` §8.4, which argued against capture; read that section anyway, because it is why every guard above exists.

**WHICH OF THE THREE CLOSED IS WRITTEN ON THE ROW (2026-09-17).** `recorder_or_reason` (`recorder_for` is the same decision with the reason thrown away) returns one of `SKIP_OPERATOR_OFF` / `SKIP_POLICY_OFF` / `SKIP_NO_CONSENT` / `SKIP_STORE_FULL` / `SKIP_OPEN_FAILED`, the socket hands it to `_make_finalizer`, and the finalizer writes `interview_sessions.audio_skipped_reason` (migration `b7d2e4f9a1c3`; `nothing_captured` for a recorder that closed with nothing; NULL on a recorded interview and on every older row, never a guess). It exists because on the AWS deployment the operator's switch is TRUE (`infra/cdk/reep_core/stack.py` defaults `interviewRecordingEnabled` to `"true"`), no policy row is ever seeded, and "Allow voice recording" on the Interview records screen's policy card is unticked until somebody ticks it — so every interview read "No audio" beside a grey Download button, the only line saying why was an INFO in the API log, and the office reported the recording feature as broken. The records API serves the word, the open record spells it out with a button to the policy card when the fix is the college's own tick, the Download button is drawn only when there is a file, and the policy sheet carries `recording_enabled_on_server` so the card says when the box it offers can do nothing.

**THE ROOM'S "RECORDING ON" IS A STATEMENT ABOUT THE NEXT INTERVIEW, NOT ABOUT THE DAY THE STUDENT AGREED (2026-09-17, the same evening).** The Start line read `scope_store_audio` off the student's own consent row, and a consent row is a COPY of the college's policy taken when "I agree" was pressed and never edited again (`grant_consent`'s "a grant is a row and a row is never edited"). The room started straight away whenever it held a row, so after the first agreement nothing ever compared that copy with the policy: a student whose row said "on" from the day they agreed read "recording on" beside a Start button whose interview the recorder then refused, and the office's screen said "No audio" for the same interview. The other direction is the one that matters more and was equally live: the office ticks "Allow voice recording", every existing student's row still says `false`, the recorder answers `no_consent`, and the record's own explanation ("they are shown the terms again at their next Start") was untrue, because nothing showed them. `shared/interview-room/consent-sync.ts` is the fix and it is two pure functions with a spec: `grantMatchesPolicy` is the server's `acknowledged` comparison made on the client, and Start proceeds on the standing row ONLY while the policy still says what the row says — otherwise the terms are shown again and "I agree" posts the acknowledgement `POST /api/interview/consent` was always written to supersede. It is judged on the policy card already loaded and NOT on a fetch, because `InterviewService.start()` must run inside the click's user gesture (getUserMedia and `ctx.resume()`); the card is re-read in the background after each Start so the next press judges fresh data. `recordingLabel` reads the policy AND `recording_enabled_on_server`, which `StudentPolicyOut` carries now (the admin sheet already did), so a college that ticked the box on a server that cannot record does not tell its students they are being recorded; an absent flag reads as unknown and falls back to the policy alone, never to "off".

**The LiveKit voice stack was REMOVED in 2026-09.** `voice_agent.py`, `app/routers/voice.py` (`/api/voice/*`), `requirements-voice.txt`, the `chat-voice.service.ts` client, the orb's voice overlay, the CI `worker-imports` job and both `livekit-*` dependencies are gone, along with the fourth process and its separate Python 3.12 venv. It was a four-stage cascade (Groq Whisper -> Groq Llama -> TTS) over LiveKit's WebRTC transport, and it was superseded by the mock interviewer, which is genuinely speech-to-speech. **The one voice experience now is `/student/assistant`** (Amazon Nova 2 Sonic, in-process, no extra venv). Three things survived the removal on purpose: `Message.channel` is still a plain String column, so historical `voice` rows read back unchanged and the runbook query above still groups by it; `conversations.append_message`'s `provider_turn_id` dedup is still the interview's first dedup layer, now pinned by `tests/test_conversation_dedup.py` instead of through the deleted endpoint; and `AgentHistoryService` (`apps/web/src/app/core/`) carries the three non-voice members the interview screen needs — `chatHistory`, `loadHistory()`, `clearConversation()` — out of the 840-line service that was deleted.

The text orchestrator (`POST /api/agent/ask`) **has a UI caller**: the REEP Agent chat screen (`apps/web/src/app/features/agent/`) is routed at `/student/agent`, `/mentor/agent` and `/admin/agent`, and the floating orb now taps straight through to it. So `AGENT_RUNS_COLLECTED` in `app/routers/agent.py` is **`True`**, `POST /api/agent/feedback` records thumbs against the run, and `GET /api/agent/metrics` reports real counters. Flip that one constant to `False` if the chat screen is ever unmounted, and both surfaces say so rather than reading a frozen history as a live zero.

`apps/interview-realtime/`, the superseded standalone prototype of this relay (no authentication, no database), was **deleted in 2026-09** along with `ollama/` and `tools/cascade`; the in-process relay above is the only interviewer.

## The voice-assistant platform (2026-09)

`apps/api-py/app/voice_platform/` is the dual-path (Undergraduate / Postgraduate)
interview platform from the system-architecture diagram — Admin CRUD for a
per-degree catalogue (specializations, questions, time limits, recording
policies, candidates), the `/ws/media-bridge` socket, S3 → Lambda → SQS
candidate ingest, dual-channel call recordings uploaded to S3 with presigned
`recording_s3_url`s, DynamoDB session state, OpenSearch session logs and
question vectors, and CloudWatch/Sentry hooks. Full notes:
`docs/voice-platform.md`; infrastructure: **AWS CDK** in `infra/cdk/` (the core
stack stays Terraform in `infra/aws/`, untouched; the api reads the CDK stack's
SSM parameters at boot via `app/voice_platform/ssm_config.py`, so Terraform is
not part of the platform's deploy path).

**It reuses the interviewer; it does not fork it.** The media bridge compiles
catalogue rows into an `interview_matrix.Specialization` (which gained an
optional `question_bank`) and runs `NovaSonicSession` (which gained an optional
per-session `max_seconds`), calling the interview router's own `_open_records`
and writers — so consent (4013), the caps (4012/4015), rule 1's containment and
the `interview_sessions` record all apply unchanged. A platform call is a real
interview with a `platform_call_sessions` row beside it. **Every AWS projection
is optional and honest**: no bucket/queue/table/endpoint configured means that
piece is off and `GET /api/platform/admin/status` says so; nothing pretends.
Recording needs THREE switches — the degree's policy, `INTERVIEW_RECORDING_ENABLED`,
and the student's live `scope_store_audio` grant.

**THE RECORDINGS BUCKET NO LONGER EXPIRES, AND THAT REVERSES A DELIBERATE
DECISION.** `recordingRetentionDays` is **0** in `cdk.json` — no lifecycle rule
at all — and the bucket is now **versioned**, so `RecordingStore.delete`'s real
`delete_object` writes a delete marker and the bytes stay. The comment it
replaces was correct on its own terms: a non-current version outlives a
lifecycle delete marker, so versioning would keep student voice past the
retention the student consented to, which is exactly why it was off. That
argument holds only while the consented clock is the governing promise, and the
college's rule is now that nothing in storage is deleted. **The obligation that
comes with it is a PRODUCT change nobody else will make**: the consent panel
still tells the student their recording is destroyed after the policy's
`retention_days`, and `interview_policies.retention_days` is still the number it
shows. That copy is now inaccurate and must be rewritten before this is
described to students as their policy. A number is still accepted and 0 is off,
so a synth with no context renders a bucket that still expires — and a
deployment that turns the clock back on gets `noncurrent_version_expiration`
with it, because on a versioned bucket an expiration alone frees nothing.

## The design-v4 screens (2026-09)

The four UIs (student, mentor, admin, alumni) follow the Claude Design export
at `docs/design-v4/reep-app-standalone.html`. The sidebars are the design's:
students get Home, Jobs, Skilling, Leaderboards, Time Sheet, Faculty / TPO Log
(the design's "Mentor / TPO Log" — see "Who is who"), Resume Builder — **no Badges screen and no agent link** (the badge grid lives on
Skilling; the orb's "Type instead" opens `/student/agent`); admins get
Analytics, Leave Approvals, Mentors & Students, Jobs Sheet, Exports, REEP Agent
(Registrations, Catalogue and Placement stay routed and are reached from the
Analytics tiles). The REEP Agent chat (`features/agent`, `POST /api/agent/ask`)
is the design's assistant, so `AGENT_RUNS_COLLECTED` is `True` again. **The
REEP Agent row left every sidebar on 2026-09-16** — admin's "Ask REEP" and
faculty's "REEP Agent" both — because the floating orb on every screen opens
the same chat and a sidebar row was the same door drawn twice; the three
`/agent` routes stay as deep links, the orb's "Type instead" and the admin
Home's "Ask REEP" tile still reach them, and `AGENT_RUNS_COLLECTED` stays
`True` because the screen is still mounted. Deleted
with this: the student Badges, Overview, Academics and Offers screens, the
mentor Badge Centre and the placeholder component. **The mock interviewer is
NOT in the design and was kept on purpose** — it is deployed and working, and
it is the landing's Elevate "Mock Interview" module (`/student/assistant`).

## The 2026-09 redesign — the shell, and the navigation as data

The approved boards are `docs/redesign-2026-09/` (the implementation kit, kept
in the repository because every phase prompt cites paths under it). Phase 0 is
the shell and the design system; the screens themselves arrive in the later
phases, and the sidebar says so rather than hiding them.

**The navigation is DATA, not markup.** `layout/app-shell.component.ts` declares
`ADMIN_NAVIGATION`, `STUDENT_NAVIGATION`, `FACULTY_NAVIGATION` and
`ALUMNI_NAVIGATION` as arrays of `NavigationGroup`, and the template renders
whatever the `navigation()` computed returns. That is what makes the answer to
"which screens does this role see" a list one can read, rather than a `@if`
tree; the old template had the four roles' items interleaved in one block with
role conditions on each, and adding a screen meant finding the right `@if`.

**THE MAIN ADMIN LANDS ON A LIST OF TASKS, AND THE SIDEBAR IS IN PLAIN WORDS
(2026-09-15).** `/admin` is `features/admin/home` — what is waiting (five live
counts, each a button to its queue: applications, leave, students without a
faculty member, offers, access reviews) and every screen as a button whose
label is the task ("Approve new students", "Post a job"). Analytics moved to
`/admin/analytics` with every control it had; `HOME_FOR_ROLE` still says
`/admin`, which is why the landing changed in the route table and not in the
two home maps `test_codebase_guards.py` compares. `ADMIN_NAVIGATION` no longer
uses the boards' words — "Mentor mapping" is "Assign faculty", "Roles &
functions" is "Who can do what", "Exports" is "Download reports" — and the
groups are nouns (People, Every day, Interviews, College setup, Settings). Same
eighteen screens, plus Home and a Placement row that used to be reachable only
through the Jobs sheet. **The rule that produced this: nothing on the console
explains itself.** No tooltip that says what a screen is for, no "how to use
this" card, no tutorial; if a label needs decoding, change the label. The
owner's words were that a screen which has to be explained is a screen that
was not built.

**SETTING UP A COLLEGE IS ONE SCREEN NOW (2026-09-15): `/admin/setup`,
`features/admin/college-setup`.** The spine used to be about fifteen forms
across Colleges, College structure and Catalogue, and the owner named
"complete college setup" as the main problem. The screen is six steps — the
college, its departments, their courses (degree and years), the optional
specializations, one batch per leaf, then "Create everything" — and it sends
EXACTLY the five POSTs College structure sends, with the same payloads, so it
is the existing way onto the spine typed once rather than a new one. Step 1
offers every college already on the deployment; picking one loads what it has
(departments, courses, specializations, batches), shows those rows locked, and
lets the office add what is missing — a half-set-up college finally has a
screen that says what is left. **"Create everything" is additive and
re-runnable**, `app.seed_catalogue`'s rule: a 409 is answered by looking the
row up by its code and using it ("Already there"), a row whose parent failed
is "Skipped" with the reason, and pressing the button again resumes from what
landed. The batch conventions are the seeder's, in `college-setup.model.ts`
and pinned by its spec: code `COLLEGE-DEPT-COURSE[-SPEC]-LABEL`, name the YEAR
alone ("2026-28" — a batch is a year, and its course and specialization are the
links the same five POSTs set; see "A BATCH IS A YEAR" below), July to the last
day of June, and the mock-interview
chip per leaf is the same case-folded code match `_default_track` applies —
"Mock interview: general" is a fact the office sees before the first student
does, not a surprise on the day. Nothing on the screen explains itself.

**THE THREE COLLEGE-SETUP SCREENS NO LONGER REPEAT EACH OTHER (2026-09-15).**
Colleges carried its own Add-college form beside the header's "Set up a
college" button; College structure carried a third Add-college form plus
inline Add-department / Add-course / Add-specialization forms that were the
setup flow's five POSTs typed one at a time on a second screen; and the owner
named the repetition. The split is one sentence now: **Set up a college ADDS,
College structure SEES AND CHANGES, Colleges LISTS.** `/admin/setup` step 1
took the one field Colleges' form had that it lacked (`contact`), and every
"Add a …" on College structure is a link into
`/admin/setup?college=<id>&step=<n>` that loads that college and opens on that
step, so adding one department is not six presses of Continue. What stays on
College structure is what the flow cannot do: edit or archive a course, map a
mock interview, edit a batch, add a NON-standard batch (a section, odd dates —
the flow writes one per leaf on the academic year), seat students, and the
email domains. The one thing Colleges' form did that the flow could not —
appointing a college admin — is an "Appoint" action on each college card,
which also works for a college that already exists rather than only at the
moment of creation. Both screens are the setup screen's shape now: on
Colleges a college is a card with its facts and two buttons ("Open", "Add
departments, courses, batches"); College structure picks ONE college in a
select at the top (`?college=` preselects it, which is how "Open" lands) and
lists its sections top to bottom, a department or course a bordered row that
opens on press, in place of the rail-and-detail-stack board. The card that
described what a college admin may and may not do was prose about the
capability catalogue and is gone; Who can do what lists it.

**THE INTERVIEWS GROUP TOOK THE SAME SHAPE (2026-09-15).** Interview
questions: the 290px track rail is a row of pill tabs, then one card for the
track and one for its questions, with the add-one and add-many forms opening
inside the questions card; the "Asked" and "Avg score" columns — a permanent
dash each, with a tooltip saying why (B6.6: the free-style interviewer
rephrases, so no turn can be attributed to a row) — went, and the column
chooser and density toggle whose only job was to hide them went with them;
the help line under every field is a placeholder now; the duplicate "Add many"
on the grid bar went. Interview records: the side column (record panel,
progress card, retention card with the policy behind a toggle) is gone; the
open record is a card under the list with its report or transcript beside the
student's score-over-time chart, and the policy is its own card, always open,
with the college picker on it; the Transcript/Report buttons that duplicated
the record's own tabs went, as did the Batch filter for a session whose grant
cannot read the batch list (a grey control with the reason in a tooltip) and
every paragraph — consent note, chart note, retention list, the two policy
notes. SWOC notes: the three scope sentences, the semester-slice note, the
"each line shows…" notice and the "edits save when you click away" footnote
are gone; the reach chip, the counts and the chips on each line say the same
things as facts; the Semester filter is drawn only when a line carries one;
the student rows are the bordered pick-rows. Nothing that could do anything
went, and nothing on any of the three explains itself.

An item with `path: null` renders as a LABELLED, NON-CLICKABLE row carrying its
`arrivesIn` badge ("Available with Phase 2"). This is deliberate and it is the
opposite of the usual instinct, which is to leave an unbuilt screen out until it
works: the boards show the admin six groups, and a sidebar that grows a new
group each fortnight reads as an app that keeps changing shape. A row nobody can
click is honest about what is coming; a missing row is not. `.nav-pending`'s
label ellipsises, because "Students & batches" does not fit the 220px nav
alongside a badge.

**`capability` on an item is a filter, never a gate.** `grantedAdminScreens()`
hides a row the session does not hold, and that is a convenience — the API's
`require_capability` is what refuses the request. A client-side check that looks
like authorisation is how the next person stops writing the server-side one.

Four capabilities are deliberately absent from the Main Admin's sidebar
(`mentor.mentees`, `mentor.notebook`, `mentor.verifications`,
`mentor.upskilling`): `_FACULTY_ONLY` in `app/governance.py` keeps them out of
the ADMIN baseline, so those screens would render empty for the office account.
Confirmed live rather than by reading — asking `GET /api/mentor/mentees` as the
seeded Main Admin answers 403 naming `mentor.mentees`, while the seeded mentor
gets their one mentee.

### Phase 3 — access became a decision with a reach (2026-09)

Phase 2 drew the whole admin console on the endpoints that existed, and disabled
every control whose endpoint did not, with the phase it arrives in written on the
control (`shared/pending/pending.directive.ts` — read its docstring, it is the
rule). Phase 3 is those endpoints. The theme running through all of it: **access
stopped being one bit and became a decision with a reach, a reason and an
expiry.**

**A FACULTY ACCOUNT IS NOT A MENTOR BY EXISTING, and the capability finally says
so (B2.3).** `ROLE_BASELINE["MENTOR"]` used to be every SCOPED key in the
catalogue, so "is this person staff" and "may this person read a mentee's ledger"
were one question with one answer. It is now `{mentor.agent, mentor.upskilling}`
— the assistant and one's own certificate shelf, which belong to the PERSON. The
two that belong to a GROUP (`mentor.mentees`, `mentor.notebook`) are **derived,
not granted** (`mentor.leave_approve` was the fourth until 2026-09-16, when
leave approval became the Main Admin's alone and the key left the catalogue —
see "Leave is one signature" below; `mentor.verifications` was the third until
2026-10-16 and is now a grant only — see "Skill verification is the office's
choice of person" below):
`app/mentor_functions.py` is a pure function of "do you currently mentor
anybody". The spec asked for `capability_grants` rows written by
`ensure_mentor_group` and backfilled by a migration; that was built first and
thrown away, because **five places set `students.mentor_id`** — three routers,
`app/seed.py` and `app/grant_access.py` — and a stored grant is correct only
while every one of them remembers to re-derive. The sixth writer somebody adds
next year would not fail loudly: a faculty member would simply be unable to open
their own mentee log, and the row that would explain why is the one nobody wrote.
What a derived function costs, honestly: no expiry, no independent revocation, no
record of who could see what last March. The first two are features here and the
third is the mentor assignment history's job.

**A GRANT HANGS ON A RUNG OF THE SPINE (B1.2).** `capability_grants.scope_level`
/ `scope_id` are a `ScopeLevel` (COLLEGE → DEPARTMENT → COURSE → SPECIALIZATION →
COHORT → STUDENT — **renamed from `FeatureScope`**, PG type
`governance_feature_scope` → `governance_scope_level`, because a grant hangs on
the same rungs a feature override does and two identical enums is the same
mistake twice). `governance.granted_reaches` answers "how far does this key
reach", `reaches_target` compares that against a target's ancestry, and
`require_capability(db, session, key, target=...)` refuses a holder who has the
key **somewhere else**. `target` is opt-in and the eighty existing three-argument
call sites keep their meaning exactly. `policies.scope_filter` is the same answer
for a LIST, handing out a `Reach` with a subquery per model.

`ancestry_of_student` reads **both** department pointers — `cohorts.department_id`
and `students.department_id` — and that was a live bug in feature overrides
before it was a hole here: an override hung on a department reached the seated
students and silently missed every unseated one, which is every student at a
college that has not built its batches yet.

**NULL ON BOTH COLUMNS IS PROGRAMME-WIDE, AND THAT IS LOAD-BEARING.** Every grant
written before B1.2 has NULLs, and if either reader ever read that pair as
"reaches nothing", every grant in the product would silently stop working on
deploy — with the screen going *empty* rather than refusing, which nobody reports
as a permissions bug. `tests/test_phase3_compatibility.py` pins it against a raw
row.

`POST /api/admin/governance/grants` takes `scope_level` + `scope_id`, **both or
neither** (a level alone names no target; an id alone cannot be resolved; either
stored by itself reads back as programme-wide, the widest reading of a request
that asked for the narrowest). The target is checked through `_target_label`, the
same resolver a feature override's target goes through — two existence checks
against the same five tables is how one of them ends up accepting a rung the
other refuses. **A scope target is NOT refused for a `PROGRAMME` capability**:
that word on the catalogue means "no mentor GROUP narrows this", and B1.4 narrows
exactly those six keys by exactly these rungs. And `already_live` compares the
reach, or "and Civil as well" is answered by the no-duplicates rule finding the
Mechanical grant and doing nothing while the console reports success.

**THE SERVER NARROWS THE LIST AND SAYS SO IN A HEADER (B1.4).**
`app/scope_views.py`. 04 asked that every response carry `scope: {college,
department}`; eleven list endpoints answer a bare `list[...]` and the Phase 2
console is built against those arrays, so wrapping each in `{scope, rows}` is a
breaking change to all eleven spent on the least important half of the
requirement. The answer is response headers — `X-Reep-Scope` (**`programme` |
`narrowed` | `none`**), `X-Reep-Scope-Colleges`, `X-Reep-Scope-Departments`,
capped at `MAX_SCOPE_IDS` because a header is not a payload. The three words
matter: *"may see everything"* and *"may see nothing"* are opposite facts, not
two ends of a scale, and must never render the same — a `none` reach rendering as
an empty queue tells the office there is no work rather than that they cannot see
it. `registrations` gets its projection here rather than in `policies.py`: **a
registration is not a student yet**, it carries its own spine pointers, and there
is no `students` row to join to until somebody approves it.

**THE COLLEGE DECIDES WHICH ADDRESSES MAY HOLD AN ACCOUNT (B1.1).**
`app/institution_domains.py` is the one helper. The `ENV` list is a **FALLBACK,
not a floor** — a college that has named its own domains is not also subject to
the deployment's.

**FACULTY ACCOUNTS HAVE A LIFECYCLE (B3.1–B3.6), and disabling demands a reason
in words.** `POST /api/admin/users/{id}/disable` refuses an empty one:
disabling is the single console action whose effect is invisible from the console
afterwards — the person simply cannot get in — and six months later nobody
remembers whether it was a resignation, a secondment or a security incident. The
reason is on the row AND on the trail, and the re-enable window is read against
it. `enable` and `sign-out-everywhere` sit beside it. **The same two endpoints
disable and enable a STUDENT (2026-10-17)**, from the More menu on the student's
record, Main Admin only as they always were; a student has no 90-day enable
window, because "create a new account instead" means nothing for one. **Deleting a faculty
account from the console arrived on 2026-09-16** — remove-and-restore and
delete-for-good behind an emailed code, in `routers/admin_deletion.py`; see
"Deleting people and colleges from the console" below. Emptying a whole
deployment of people is still `python -m app.purge_people`.

**GOVERNANCE GREW A SECOND SIGNATURE, AN EXPIRY AND A QUEUE (B2.4–B2.7), AND
THE MAIN ADMIN IS EXEMPT FROM THE SIGNATURE (2026-09-16).** A capability that
`carries_pii`, granted by a DEPUTY, is written `pending_approval` and **holds
nothing** until a different holder of `admin.governance` approves it — so a
pending grant that renders like a live one is an admin believing they granted
access that does not exist. **The Main Admin's own grants are live the moment
they are written, whatever they carry.** The office is one account by rule and
every deputy holds Governance on its say-so, so a second signature on the
office's decision could only ever come from somebody the office appointed to
give it: one decision asked twice. Until this date that is exactly what
happened — the office's `carries_pii` grant sat `pending_approval` on the
one-admin deployment this product is built for until a deputy was appointed to
agree with it. `initial_approval_state` in `routers/governance.py` is the ONE
place that reads who is granting, and BOTH writers of `capability_grants`
(`create_grants` and `appoint_college_admin`) call it; the readers do not care
who wrote the row, so a deputy's pending grant is still enforced where access
is resolved. **The self-approval refusal on `/approve` is a DEPUTY's**: the
Main Admin may approve any pending row, its own included, because rows the
office granted before this date were written pending under the old rule and
the office is the one account that can clear them — refusing it left every
such row stuck behind a deputy appointed for the purpose, which is the
arrangement the amendment removed. `tests/test_governance_review.py` pins all
three halves. Grants carry
`review_at`; one with **no** expiry is the grant that most
needs a date, because nothing else will ever bring it back to anybody's
attention. `role_at_grant` stops a grant counting when the account becomes
something else (and stays NULL on a group grant, because that decision named "the
placement coordinators", not a role). `GET /api/admin/audit` is the trail, read
through the console; listing it writes no event of its own — it would be the most
frequent action in the table within a week and would bury everything the table
exists to show — but **the CSV download audits itself**.

**WHO CAN DO WHAT WAS REDESIGNED, AND ACCESS IS GIVEN AND REMOVED MANY AT A
TIME (2026-10-16).** The owner found the screen too confusing: a nine-column
grid whose "Scope" and "Reach" columns answered near-identical questions, a
side panel that granted ONE function at a time, and a catalogue tab of internal
keys. `features/admin/governance` is now four tabs in plain words: **Give
access** (tick people or a group, tick every kind of access, choose which
students and an optional end date, give a reason; one summary sentence says
what will happen), **Who has access** (every grant grouped by access type or by
person; tick rows, or a whole access type, and remove them), **Needs
attention** (the `/review` queue) and **Groups**. Each access type says which
sidebar row it opens for a faculty member, read from `GRANTABLE_ADMIN_SCREENS`
rather than typed again. The API grew two batch forms to match, both one
transaction and all or nothing: `POST /admin/governance/grants` takes
`capabilities: [...]` beside the old single `capability` (folded in, so older
clients still work) and writes every (access type, subject) pair with the one
reason, the approval state decided per access type; `POST
/admin/governance/grants/revoke` takes `grant_ids` and one reason, refuses the
whole request on an unknown id, and names ids already revoked rather than
failing on them. Both are capped (`MAX_GRANT_ROWS_PER_REQUEST`,
`MAX_REVOKES_PER_REQUEST`). Approve and extend still go one grant at a time,
because each can be refused on its own. The old `?tab=review`, `grants` and
`catalogue` links land on the tab that now holds the same thing.
`tests/test_governance_bulk.py` pins the API; TC-530, TC-531 and TC-540 drive
the screen.

**B2.1: ENFORCE EVERY CATALOGUE KEY OR DELETE IT.** A row naming a capability
nothing checks is a promise the API does not keep, and Governance is where the
office looks to answer "who can see what". `tools/ci/check_capability_enforcement.py`
is an AST guard over the routers. The ten `student.*` keys went, because they
gated nothing anywhere and no client read one.

**B2.2: a feature switch that does nothing must not be settable.** `PUT
/api/admin/governance/features` answers **422** for a feature whose `enforced` is
false, in *both* directions — an `enabled=true` row on an unwired key is equally a
promise, is indistinguishable on screen from a rule that is working, and
`enabled=true` is the default anyway. 422 and not 403: the caller holds the
capability and the request is well formed; it is the FEATURE that cannot take a
rule. `students_affected` is the blast radius and is why the console can say
"switch off for 86 students" rather than naming a specialization.

**B14/B15: exports leave receipts, and an account can see its own doors.**
`GET /api/admin/exports/history` is the only B14 endpoint returning JSON, which
is why `scope` is in its body and in headers on the three CSVs — a JSON envelope
around a CSV is not a CSV. `GET /api/auth/me` carries `google_linked`,
`last_sign_ins` and `notification_prefs`, and **all three are `None`/empty
everywhere else on purpose**: `None` means *not asked*, never "no Google account
is linked", and a client reading absent as `false` will tell somebody their
Google sign-in is unlinked on the screen immediately after they used it. A
preference whose `enforced` is false is one nothing reads yet, shown as such and
refused by the PUT — the same rule as an unwired feature switch.

**The compatibility guardrails are a test module, not a checklist.**
`tests/test_phase3_compatibility.py` holds 07 §5: the role is unchanged, a mentor
with mentees keeps the group functions (`DERIVED_FROM_MENTEES`) and one without keeps their own two, **the
capability and the endpoint give the same answer** (the split that made
`test_no_director_privilege.py` necessary, in the other direction), a pre-B1.2
grant still reaches the programme, the Main Admin is never narrowed, student
routes grew no scope header, disabling one account bumps nobody else's
`token_version`, and one activation link does not consume another's. The five
guardrails whose subject is Phase 4 are listed at the foot of that module with
the reason each cannot be pinned yet — a checklist with five quiet gaps is one
somebody signs off as complete.

### Phase 4 — the console's promises came due (2026-09-13)

Phase 2 drew the whole admin console and disabled every control whose endpoint
did not exist yet, with the phase written on the control. Phase 4 is those
endpoints, in six areas at once — **4a** semesters, promotion and graduation
(B4) plus the catalogue copy (B13); **4b** spreadsheet imports, placement
criteria, the alert engine and analytics (B8) with jobs and placement (B12);
**4c** interview tracks, the college's interview policy and the interview
record (B5/B6/B17); **4d** mentor assignment history and SWOC ownership
(B9/B7); **4e** the leave chain, balances, attachments and the paper (B10);
**4f** the registration queue, its checks, HOLD and the rules CRUD (B11). The
areas that changed a rule already have their own sections above; what follows is
the part that has no other home.

**81 disabled controls went to 1, and the survivor is honest.** A
`[reepPending]` badge is truthful exactly while the endpoint is missing; the
moment it lands, that badge is the stale label `45b91a9` had just finished
removing from the Faculty screen. So every control was taken back to its router
and decided by READING THE ROUTER, never by trusting a template comment that may
predate it. **Five groups were DEMOTED rather than wired** — plain `disabled`
with the real reason in a `title`, no phase number, because a phase number
promises a date and these are not waiting for one:

  * **Student 360's "Hold back"**. `KIND_HOLD_BACK` exists in
    `app/models/semester_history.py` and **nothing writes it**: a student is
    held back through `PromoteIn.hold_back`, on the batch dialog, not from their
    own panel. A button calling a writer that does not exist is worse than a
    disabled one.
  * **Student 360's SWOC, Leave and Uploads tabs.** Every one of those endpoints
    is a programme-wide QUEUE, not one person's file. Wiring them would have made
    a per-student screen fetch and filter a roster.
  * **Analytics' Batch / Course / Track filters.** Neither analytics endpoint
    takes a cohort and both aggregate server-side, so there is nothing to narrow
    client-side either — and a Track is the INTERVIEW vocabulary: no attendance
    record, ledger entry or offer carries one.
  * **The roster's Readiness / CGPA / Attendance columns.** All three are real,
    but only on `GET /admin/students/{id}/360` — one request per student, so a
    roster that filled them would be one request per ROW. The tooltips point at
    360.
  * **The shell's notifications bell.** There is no per-account feed at all.

**THE DEMOTED CONTROLS ARE GONE (2026-09-15), AND SO ARE THE APP BAR'S SEARCH
AND BELL.** Every control above, and every other one that was `disabled`
unconditionally with its reason in a `title` — Analytics' Batch/Course/Track
filters, the Jobs sheet's "Import postings" and its CTC/Openings fields, Assign
faculty's "Assignment history" button and Specialization filter, the Leave
queue's Requester filter, Exports' "Schedule an export" and its two "Not built"
cards, Placement's Track filter, the audit trail's College filter, the feature
switches' College picker, SWOC's Export and Mentor filter, the question bank's
Session cap, Interview records' "Daily-cap resets" tile, Student 360's "Hold
back", "Re-send invite" and its three tabs that opened nothing, College
structure's Degree level / Total semesters / Semesters per year, Colleges' "Copy
catalogues from", the Add-faculty wizard's Employee ID and its whole Functions
step, Governance's derived Review select, Faculty's two "Grant function"
buttons and its empty History tab — was deleted, template and constant both.
The board-fidelity argument ("a reviewer cannot tell a control that is missing
from one that was missed") lost to the owner's rule for the console: a grey
control that can never work is the first thing a first-day clerk presses, and
the reason on it is exactly the explaining they refused. Nothing that could do
anything went: the check that gates each of these commits counts `(click)`,
`routerLink`, `(change)`, `(input)`, `(submit)`, `[href]`, `download=` and
`formControlName` per template against HEAD and refuses a decrease. The
reasoning each control carried is in this paragraph's commit, not in a tooltip.

**Building the wiring found two real defects that no test had.** `placement`
called a `selectValue` helper that did not exist, so the screen's own filter was
a template reference to nothing; and BOTH batch dialogs emitted a `completed`
output that no parent bound, which meant the grid behind a successful promote or
graduate went on rendering the PRE-WRITE read — the admin pressed the button, it
worked, and the screen said it had not. Neither is visible from a router and
neither is visible from a board. They are visible from pressing the button.

**THE FOUR REFUSALS, and the reason each is not the obvious answer.** These
matter more than the list above, because a task refused with no record is a task
somebody re-opens next quarter and re-refuses at the same cost.

**B4.4's alumni-profile auto-create is IMPOSSIBLE, not merely unwise.**
Graduation was asked to create the graduate's `alumni_profiles` row so they
arrive as a finished alumnus. `alumni_profiles.company` is **NOT NULL**, so such
a row must invent an employer for somebody who has not told us one — and worse,
**row EXISTENCE is what `GET /api/alumni/profile`'s `created:` flag reports**,
and that flag is the entire branch behind the first-login create-profile form.
Minting the row does not pre-fill the form; it DELETES the form, permanently,
for every graduate. So graduation flips the role and the status and stops there,
and the graduate meets the create-profile form, which is what it is for. What
was added instead is a nullable `alumni_profiles.student_id`, which the form
fills in, so the alumnus and the record they left behind are joinable without
either one being guessed.

**B6.6's `interview_turns.question_id` cannot be filled by anything, and B5.5
depends on it.** The column is there — free on an empty nullable column, costly
to add later to a table with hundreds of thousands of rows — and **nothing
writes it**. The question bank is rendered into the INSTRUCTIONS once, as a
block of `[phase] text` lines with the model explicitly told to rephrase rather
than recite; the engine never SELECTS a question, so there is no "the question
it injected" to record, `_TurnRecord` has no slot for one and neither engine
could fill it. 04's B5.5 (`asked_count` / `avg_score` per question) is built on
that column and is therefore refused with it. If it is ever filled it will be by
POST-HOC matching of the interviewer's words against the bank, which is lossy
BY CONSTRUCTION because the prompt orders the rephrasing — NULL would then mean
"unmatched", never "not asked", and every count built on it is an ESTIMATE the
screen must label as one. The alternative, a `mark_question` tool call inside
the turn loop, puts a round trip on the hot path the deterministic word gate
exists to keep off, and the local engine has no equivalent, so the two engines
would stop sharing one contract.

**B10.1's HOD and Principal signing functions have nobody to name.** There is no
HOD ACCOUNT in this product — `departments.head` is FREE TEXT, a name typed on a
form, with no `users` row behind it — and no principal concept at all. A
"function-based signing chain" over those two would be a vocabulary whose values
can never be resolved to a person who can sign in, which is how a leave request
ends up in a state only a database edit can leave. What B10.1's real bug turned
out to be is narrower and provable: `_assert_can_decide` resolved the requester
to a `Student` row, so a FACULTY member's own leave was decidable by ADMIN
alone — and `grant_access` permits exactly one ADMIN while `decide_leave`
requires two DISTINCT signatures. Every staff leave request on every real
deployment reached FIRST_APPROVED and could never reach APPROVED, silently,
behind a live "Mark Sanctioned" button. The third door is a SCOPED GRANT of
`mentor.leave_approve` at `ScopeLevel.DEPARTMENT` or `COLLEGE`, made in
Governance with a reason and an audit row. `leave_requests.first_signed_as` /
`second_signed_as` record the function the signer was ACTING IN, as a plain
`String` and not an enum, precisely because that vocabulary is the part still
being argued about and must stay a data change. **SUPERSEDED ON 2026-09-16:
leave is ONE signature, the Main Admin's** — the third door and the second
signature are gone together; the paragraph stays because the columns it
explains are still on the row. See "Leave is one signature" below.

**B10.8's "refuse to render" clause is refused.** B10.8 asks that the paper
print the signer's function, and it does — as a third element on the attestation
line already drawn in the blank margin, "Asha Rao · Mentor · 10 Sep 2026, 09:00",
never as part of the form. Two things it is written as asking for must not
happen. Printing the function INSTEAD OF the fixed "PROGRAM DIRECTOR" label
means whiting out and reprinting over the college's own PDF — defacing the
office's form — and the test asserts that label survives. And **refusing (422) to
render a decided request whose signer has no signature image** would refuse an
APPLICANT their own sanctioned leave because a DIFFERENT person never uploaded a
PNG. A signature image is decoration on top of a record that already exists; if
the office wants pressure on signers, that belongs on a console screen, not in a
download. A function prints only where one was recorded — every row decided
before the column existed has NULL, and NULL prints the line exactly as it
printed before, never a function guessed from a mentor group that may have
changed since.

**One live defect fixed in passing, because it was the same guardrail.**
`_attendance_pct` returned `0.0` for an empty table, and `attendance_records`
has exactly one writer — B8.1's spreadsheet import. So on every deployment where
that import had not been run, every student's own home screen read **"Attendance
0.0% vs required 75.0%"** with a red Not-met chip, for a bar nobody had measured
them against. It answers `None` now, and `None` renders as a dash. That is 07
§5's "screens say 'no import yet' instead of zeros", met on the screen where
being told you are failing costs the most.

### The dev MCP surface at `/mcp` (development only)

`app/dev_mcp.py` publishes every GET under `/api` as an MCP tool, so a screen's
data can be read AS THE SEEDED ROLE while the screen is being built — the check
that the board and the payload agree, made without clicking through a browser
and trusting what is drawn. `apps/api-py/tools/mcp_probe.py` is a client for it;
`tools/pg_mcp_probe.py` is the same idea against `postgres-mcp` in restricted
(read-only) mode, for the before-and-after of a migration. Setup:
`docs/redesign-2026-09/MCP-SETUP.md`.

**Three things keep it from being a second front door**, and they are in the
module's docstring for the next reader: it mounts only when `settings.mcp_enabled`
(an ENV allowlist AND an explicit `MCP_DEV_SURFACE` flag, so an unrecognised ENV
shuts it like every other door in `config.py`); it is read-only BY CONSTRUCTION,
deriving its tool list from the app's own GET routes rather than from an exclude
list somebody maintains; and `fastapi_mcp` is imported inside the function and
lives in `requirements-dev.txt` alone, so it is not in the production image at
all and `api-imports` still passes. `tests/test_codebase_guards.py` §33 pins all
three, the last by parsing the `FastApiMCP(...)` call and asserting no write
operation reaches the tool set.

Two traps are worth carrying forward. `include_tags=` does NOT work here: in
fastapi-mcp 0.4.0 the filters UNION rather than intersect, and every tag REEP's
routers carry contains write routes, so the kit's draft would have published
`DELETE /api/admin/cohorts/{id}` as a tool. And the operation ids must be read
from `app.openapi()`, not from `app.routes`: FastAPI 0.141 keeps included
routers as nested objects rather than flattening them, so walking `app.routes`
finds four documentation endpoints and the mount silently publishes nothing.

`python -m app.dev_session --email <address>` mints the cookie those tools
forward. It mints at the CURRENT `token_version` WITHOUT advancing it — a helper
that did "the same as login" would retire the developer's own browser session
for that account every time it ran, and the two would take turns, with the loser
each time looking like a bug in the mount.

## Infrastructure — CDK, and the cutover that HAS been run (2026-09)

**CloudFormation owns `reep-core`. Terraform is released and its fifteen `.tf` files are deleted** (commit `38eb7dd`, steps 6 and 7 of `docs/cdk-cutover.md`): `terraform_release.sh` dropped 82 managed addresses after checking the stack was `IMPORT_COMPLETE`, and the plan afterwards reads "82 to add, 0 to change, 0 to destroy" — the proof Terraform no longer knows these resources, and **a plan that must never be applied**. The state file before release was kept locally and gitignored, so the undo exists on one machine and not in this repository. `infra/aws/` still holds `bootstrap-state.sh` and `grant.sh` and nothing else.

Read the rest of this section as the RECORD of how that was done, because the reasoning is what protects the next migration of this shape — not as pending work. The rule that governed the order was: **never delete a `.tf` file before Terraform has released its state, and never release the state before `cdk import` has succeeded and drift detection is clean** — the wrong order is a `terraform apply` that destroys the database. The window between release and deletion is the most dangerous in the procedure (configuration present, state empty, one apply away from a duplicate production), which is why step 7 followed step 6 immediately. The synth guards that compared physical names against `infra/aws/*.tf` now SKIP themselves (`requires_terraform` — 61 passed, 22 skipped); the mirror is proven from here by the import and by drift detection instead. `reep-core` has two phases on one context key: `phase=import` is a mirror of what exists, carrying the LIVE values the import tool reads from the state, and nothing else (CloudFormation refuses an import template that adds resources it cannot adopt); `phase=harden` adds the fixes, split by `hardenEcs` so a circuit-breaker rollback cannot undo a Multi-AZ conversion in the same update. Every physical name is the Terraform name; the synth tests (`infra/cdk/tests/test_core_synth.py`, run by CI's `cdk` job) read them out of the `.tf` files where those still existed, and skip where they do not; no `MasterUserPassword` can appear; every resource in all three stacks is `Retain` (the database included — *Snapshot* means delete-after-snapshot). **Fargate caps `stopTimeout` at 120 s.** What keeps a 480 s interview socket alive through a deploy is the target group's 600 s deregistration delay, and `tests/test_codebase_guards.py` compares that CDK constant against `nova_sonic_connection_seconds` so the two cannot drift. `deploy.yml` therefore no longer uses `aws ecs wait services-stable` (its 10-minute cap is exactly one drain). Backup after harden: one retention number (35) for RDS, the daily rule, the cross-region copy to `reep-vault-dr` in ap-southeast-1 and the governance vault lock; a weekly restore test; three failure alarms.

**THE 35 IS RDS'S CEILING, NOT AWS BACKUP'S, AND THAT IS WHY THERE IS AN ARCHIVE TIER.** `backupRetentionDays` is validated `1..35` because RDS refuses more on its *automated* backups — and until 2026-09-15 that one number also drove the AWS Backup rule and the Singapore copy, so **every copy of the database in both regions expired on day 36** and a loss discovered five weeks later was unrecoverable everywhere at once. `archiveRetentionDays` (365 in `cdk.json`, **0 and off in the code**, harden-only) is a monthly rule with a lifecycle measured in years. Three things about it are load-bearing. It is a SECOND PLAN and not a second rule, because **a selection is plan-scoped**: the daily selection covers the database AND the EFS file system, so a multi-year rule on that plan would keep every student's resume, marksheet, certificate, signature and recorded voice for years, outliving `INTERVIEW_RETENTION_DAYS` and the "files go before rows" rule both purge modules are built on — silently, because the plan would be green. It carries **no cold-storage clause**, because AWS Backup does not support cold storage for RDS and says so *by ignoring the setting*: the rule would synthesise, deploy, report success and do nothing, the same shape as the Sunday backup that failed for weeks. And its **start window is explicit**, because AWS Backup runs one job per resource — a monthly job landing on the daily one is CANCELLED, not doubled. What it costs besides money is written on the constant: a student erased by `python -m app.purge_students` stays restorable, and fully identified, for as long as that number runs, in a vault whose lock neither destructor can reach. That is a records-retention decision for the college, which is the other reason it is a separate key.

A **fourth alarm** goes with it, and it is the one the other three could not be. All three fire on `NumberOfBackupJobs*Failed` with missing data NOT breaching — correct, because a day with no failures publishes no datapoint — so the one state none of them reports is a plan that has stopped running ENTIRELY: no jobs, no failures, no metric, three green alarms and a vault going stale. That is the 2026-09-07 incident with the alarm already deployed. AWS Backup publishes a metric only for a nonzero value, so "nothing completed" *is* missing data, and `reep-backup-no-job-completed` treats it as **breaching**. What it still cannot see, said plainly in the code: with both rules writing into one vault, the metric stays nonzero even if the monthly rule never fires. A per-rule "did not fire" alarm is not expressible as a metric alarm on a shared vault; what guarantees the monthly rule is well formed is the synth guard, not the alarm. `test_backup_schedule_clears_the_rds_windows` now reads **every rule of every plan** and parses **all six cron fields** — it matched `.*` after the hour, so the day-of-month field, which is the entire meaning of a monthly rule, was unchecked. The `cleanup-orphans.sh` script that deleted the production cluster by name is gone. The import identifiers in `tools/import_map.py` are the CloudFormation registry's composite keys, cross-checked by `get-template-summary` in the runbook — eight of the first draft's were wrong, which is why the runbook was reviewed adversarially before it was written down. **Step 0 of that runbook is one read-only command**, `infra/cdk/tools/cutover_preflight.sh`: it checks the tools, the venv, the synth guards, the credentials' account, the three bootstraps and a clean `terraform plan` — after reconstructing `infra/aws/prod.tfvars` from the state with `tools/tfvars_from_state.py`, because the first apply's values were typed as `-var` flags and recorded nowhere, and without them the plan can never be clean. The import tool runs in two passes, **context before synth** (`import_map.py tf-state.json`, then `cdk synth`, then the map against that template): a context-free synth renders one plain-HTTP listener where the live ALB has two. `infra/cdk/tests/test_cutover_tools.py` rehearses that sequence against a synthetic state, and writing it found three blockers no guard had reached — the wrong order above, an OIDC ARN written while the provider was in the state (the re-synth dropped the mapped resource), and a TLS branch that did not synthesise at all (`SslPolicy.TLS13_12` is not a member of the library; none of the 58 guards had ever set a certificate). No cutover tool runs for the first time with credentials in hand any more, and the GitHub deploy role cannot run the cutover from the browser by design: it holds ECR, ECS, S3 and CloudFront rights only, none for CloudFormation, RDS or the state bucket.

**STEP 9 IS IN THE BROWSER NOW, AS TWO OPTIONS AND NEVER ONE.** `cdk import` stays human-attended for the reason above, but the harden deploy that follows it had no door at all: `.github/workflows/cdk-deploy.yml`'s stack dropdown offered `voice-platform`, `edge-waf` and `dr-vault`, and `core` was removed because a bare `cdk deploy reep-core` is the WHOLE harden — the Multi-AZ conversion and the ECS roll in one update, which is exactly what step 9 splits. So the dropdown does not offer `core`; it offers **`core-9a`** and **`core-9b`**, and the `-c hardenEcs=false` flag that IS the difference between them is set in the workflow beside the stack name rather than left to whoever is running it — a flag the operator has to remember is a flag that gets forgotten on the night it matters. `core-9b` demands the typed sentence `MULTI-AZ IS AVAILABLE` instead of the word `deploy`, for the ops task's purge-sentence reason: by the time anybody reaches that box the word is muscle memory, and 9b is the one run here whose precondition cannot be read from this repository, this workflow's role, the template or the diff — only from the RDS console, by a human, who then types what it says. Both halves share one `concurrency` group, because they are two updates to one stack and the second arriving early is a `ValidationError` that reads like a broken workflow rather than like 9a still running.

**A MERGED INFRA FIX THAT IS NEVER DEPLOYED COSTS EXACTLY AS MUCH AS NEVER WRITING IT, AND THE RUNBOOK'S DRIFT CHECK CANNOT SEE IT (2026-09-15).** `ecdb37b` removed the OpenSearch Serverless collection -- $361/month, two indexes with zero readers, proven dead before anything was deleted -- and was reviewed and merged the same day. Seven days later the collection was still ACTIVE, still in the deployed stack's 34 resources and still billing $11.87/day, and **every signal a human looks at said the work was done**: CI green, PR merged, the file gone. Two things hid it. The account's promotional credits were absorbing ~75% of the bill, so grouped by service the way the console shows it by default the collection read **$0.00** -- it is visible only under `RECORD_TYPE=Usage`, which is why `infra-drift.yml`'s cost step filters on exactly that and why a budget on NET spend (AWS's default "My Zero-Spend Budget") could never have fired. And `docs/cdk-cutover.md` step 5's `detect-stack-drift` returned CLEAN, correctly: it asks *"does AWS match the deployed template"*, the deployed template still declared the collection, and nothing had drifted. **The question nobody asked is `cdk diff` against `main`** -- *"does the deployed template still match the repository"* -- which would have gone red the morning after the merge. `.github/workflows/infra-drift.yml` runs BOTH daily and reports them SEPARATELY, because they fail for opposite reasons and have opposite fixes: a diff means DEPLOY IT, drift means RECONCILE IT. Each stack is diffed on its own so a noisy `reep-core` cannot bury a quiet `reep-voice-platform`, and `reep-core` is diffed both bare and with `-c hardenEcs=false` because those render different task definitions and diffing one form alone would report the other as drift every single run -- a check that cries wolf daily is a check nobody reads. It **never deploys**, for `cdk-deploy.yml`'s own reason: `core-9b`'s precondition cannot be read from this repository at all, so an auto-deploy here could run precisely the one update the 9a/9b split exists to prevent. It is NOT a sixth CI job and must not become one -- §34 pins `ci.yml`'s five job names against the ruleset and `protect-main.sh`, and a scheduled workflow that blocks pull requests is a required check that can never report. The full measurement, the remaining line items and the two right-sizing changes that were DECLINED rather than taken (`apiCpu` stays 512 because the interview relay carries 24 kHz PCM in-process and CPU starvation there is choppy audio in a live mock interview, not a slower page; `apiMinTasks` stays 2) are in `docs/cost-review-2026-09.md`.

**AND THE SAME WORKFLOW CATCHES THE MIRROR IMAGE, WHICH IS WHAT ACTUALLY HAPPENED NEXT (2026-09-16).** Graviton -- `RuntimePlatform: ARM64` on the api task definition, same vCPU, same memory, ~20% cheaper -- was deployed by passing `-c apiArm64=true` from a `core-arm64` dropdown option, and **the flag was never written into `cdk.json`**. Deployed and never merged, the exact inverse of `ecdb37b`, and `infra-drift.yml` would have opened an issue the next morning: `main` rendered no `RuntimePlatform` while the running service was arm64. Being red for a known-good reason is how a daily check becomes one nobody reads, so it is not a false positive to tolerate. The second half is worse and is invisible from the diff: **every `core-*` option in `cdk-deploy.yml` passes only its OWN context flag**, so the next deploy of `core-9a`, `core-9b` or either NAT option would have re-rendered the task definition WITHOUT the platform and rolled the api silently back to x86 at twice the price -- CI green, PR merged, nothing on any screen. The fix needed no deploy at all, because the repository is what was wrong: `"apiArm64": true` in `infra/cdk/cdk.json`, pinned by `test_cdk_json_carries_the_arm64_the_deployment_is_actually_running`, and `core-arm64` DELETED from the dropdown -- with the flag in `cdk.json` that option is a second source of truth for one setting, and it was a bare `reep-core` deploy gated only by the word `deploy`. **A context flag set at deploy time and not persisted is not configuration, it is a thing somebody has to remember**; `cdk.json` is the file that remembers, and the two `-c` flags still in that workflow (`hardenEcs=false`, the NAT pair) are there because they name a PHASE of a one-way migration rather than a standing property of the deployment. One consequence to carry forward: `deploy.yml`'s multi-arch manifest assertion is now load-bearing rather than belt-and-braces -- the api runs ARM64 unconditionally, so that check is the only thing between a routine image build and a service whose every task dies at pull with "Manifest does not contain descriptor matching platform linux/arm64". Do not remove it.

**THE DRIFT HALF ANSWERS NOW, AS ITS OWN ROLE, AND THE NAT AMI IS PINNED (2026-10-16, #117, #118, #124).** `detect-stack-drift` never ran: CloudFormation reads every live resource with the CALLER's credentials, and the deploy role was refused that read on purpose. `reep-github-drift` (`GithubDriftRole` in `reep_core/stack.py`, harden only, the deploy role's OIDC trust, so `main` only) is the separate role that comment asked for. It holds the five drift calls on `reep-core` and `reep-voice-platform`, each resource type's read-handler permissions copied from its CloudFormation schema (`reep_core/drift_reader.py`), and an explicit Deny on object bodies, table items, secrets, `kms:Decrypt` and log lines. **Not `ReadOnlyAccess`, which reads the dump buckets** that even the dump task may not read. `s3:ListBucket` is on the list because S3's read handler needs it, and every key in the sensitive buckets is a date or a uuid. A resource type missing from `READ_PERMISSIONS` would leave the check blank again (DETECTION_FAILED), so `test_every_resource_type_the_drift_check_reads_is_classified` refuses it at synth. `infra-drift.yml` assumes the role for that one step and reports "could not assume" as a blocked check until a `core-*` deploy creates it. **The NAT AMI is `natAmiId` in `cdk.json`**, not AWS's moving `ami-amazon-linux-latest` parameter: `ImageId` is a replacement property, so every `reep-core` deploy after AWS shipped an image rebuilt the NAT box (egress lost for the swap, the user data run on an untried image, the old box left running behind Retain), and the drift check reported it every such morning. Bump it on purpose, at night. The NAT security group, role, profile and instance, and the drift role, are `RetainExceptOnCreate`: under plain Retain a rolled-back create kept the fixed-name `reep-nat-instance` role and the retry failed on "already exists" (#124).


## The two rules that must not be broken

### 1. Student data must not leave the machine unbidden

`LLM_BASE_URL` is a URL, not a promise — it may point at a free model that trains on submissions. A resume brief carries a student's name, USN, marks and attendance.

`student_data_egress_allowed(base_url)` in **`apps/api-py/app/ai/llm.py`** is the gate: **loopback is always allowed; anything else requires `LLM_ALLOW_REMOTE_STUDENT_DATA=true`.** Call the model through `complete_chat(messages, carries_student_data=True, ...)` (or `stream_chat(...)`) on any path that sends a student's private records, and it is refused before leaving the process unless the gate permits it. When it refuses, `/student/resume/generate` composes the resume **deterministically** and says so (`used_ai=false`). Route any *new* student-PII-to-model path through this gate. Public data (a job posting) does not need it.

### 2. Staff scope is decided by role, not by a missing field

`require_mentor(session)` admits **MENTOR and ADMIN**; `require_admin` admits the Main Admin alone, and it is now the ONE console gate (`require_director` is gone — see “DIRECTOR is not a role” below). To narrow to students, use `_assert_can_access_student(...)` in **`apps/api-py/app/routers/mentor.py`**: a MENTOR sees only students in their own `Mentor` group; the Main Admin sees all. **A MENTOR with no `Mentor` group sees NOBODY** — never the whole programme. Never read "no mentor group" as "whole programme".

**Phase 3 added a THIRD fence beside that one, and it does not replace it.** A
grant now hangs on a rung of the institutional spine, so a staff member can hold
a capability *somewhere* and not *here*: `governance.require_capability(db,
session, key, target=ancestry_of_student(db, sid))` refuses that, and
`policies.scope_filter(db, session, key)` is the same answer for a list. The two
fences are checked SEPARATELY AND ON PURPOSE — `app/governance.py` says it in
one line, "a capability can never relax the student filter". Rule 2 is the
stricter gate on a mentor's own students and runs regardless; scope narrows a
grant somebody chose to hand over. A change that reaches one system and not the
other **opens the other**: that is not a hypothetical, it is what the DIRECTOR
removal did for a few hours, and `tests/test_no_director_privilege.py` exists
because of it.

**THE 90-DAY HANDOVER IS A GRANT, NOT A BRANCH, AND THE ONE BRANCH IT NEEDS IS
READ-ONLY (B9.1, 2026-09-13).** When a student is released or reassigned,
`app/mentor_history.py` mints a `mentor.mentees` grant scoped
`ScopeLevel.STUDENT` to *that student*, expiring in 90 days, `reason="handover"`
— so expiry and revocation are filtered in SQL by the one `_live_grant_clauses`,
the Governance screen lists it, and revoking it shuts the door the same hour.
04 asks for this as "`policies.assert_student_scope` honours it", and
implemented literally that is a third pass-branch in the function gating **36
call sites, 15 of which are WRITES** — mentor notes, badge-evidence approval
that mints an EARNED badge, capability assessments. That function cannot express
"read only": what it returns to a GET it returns to a POST. So the branch is
`allow_handover: bool = False`, **keyword-only, default False, passed True at
GET call sites only** (`mentor.py`'s notes list and the three reads in
`mentee_records.py`); `_assert_can_access_student` gained the parameter and is
still the single delegating `return` the AST guard pins.
**And the grant is what makes the feature visible at all**: `mentor_functions_for`
derives the two `mentor.*` capabilities and `admin.student_records` live from `mentee_count > 0`, so a
mentor whose last mentee was just reassigned holds NONE of them and
`require_capability` answers 403 *before* rule 2 ever runs — a handover honoured
only inside `assert_student_scope` would be invisible to exactly the person it
was built for. `capabilities_for` unions grants in, so it works.
**The branch must never read the window with `reaches_target`.**
`mentor_history.holds_handover_for` matches the scope pair `(STUDENT, this
student)` EXACTLY, because a programme-wide `mentor.mentees` grant satisfies
`reaches_target` for every student alive — and such grants exist (it is how the
Main Admin reaches a stuck student's evidence). Reading it the loose way would
turn every one of them into universal access to every student's records,
silently, on deploy. `tests/test_mentor_assignments.py` pins all four facts.

**THE OFFICE'S "VIEW STUDENT RECORDS" GRANT IS THE OTHER READ-ONLY WIDENING
(2026-10-15, the owner's decision).** When the Main Admin gives a faculty member
`admin.student_records` for one student, a batch, a department or the college,
that person may READ those students' records on the Student 360 page whether or
not they mentor them. It is the handover's shape, wider, and fenced the same
way: `allow_records_grant` is a keyword-only, default-False parameter of
`assert_student_scope`, passed True only by the GET reads of that page
(`routers/admin_student_360.py` and `routers/admin_student_record.py`, which has
no write path), and `policies._holds_records_grant` asks the GRANT, so revoking
it in Governance shuts the door the same hour. **It honours only a grant that
NAMES A RUNG.** Every `admin.student_records` grant written before this date is
programme-wide and meant "your own mentees"; reading `(None, None)` as "every
student" would widen all of them silently on deploy, so a programme-wide grant
still reaches only the holder's mentees. The mentor endpoints never pass the
keyword, so the same grant opens no mentor read or write (`notes`, `badges`,
`interviews`, `/mentor/uploads/{id}/file`). `tests/test_student_record_access.py`
pins all of it, including that the "Student records" list
(`GET /admin/student-records`) names exactly the students the page opens.

**A MENTOR OPENS THEIR OWN MENTEES' RECORDS WITH NO GRANT (2026-10-16, the
owner's report: "mentors' mentee records are not visible").** Until then the
Mentee Log listed a mentor's students and nothing behind them: "Full record",
the "Student records" row and the route guard all needed an
`admin.student_records` grant from the office, even for the mentor's own
group. `admin.student_records` is now DERIVED from having a mentee, beside the
two `mentor.*` functions (`mentor_functions.MENTEE_RECORD_FUNCTIONS`,
`DERIVED_FROM_MENTEES`), for the same reason they are derived: five places set
`students.mentor_id`. It reaches nobody else, because it widens nothing rule 2
decides: every read behind the key runs `_assert_can_access_student` with the
mentor's own group, `_holds_records_grant` reads GRANTS and never functions, so
a student outside the group still needs the office's scoped grant, and every
endpoint behind the key is a GET. The roster editor (`admin.students`) is a
different key and mentoring does not bring it. `_openable_clause` lists the
mentor's mentees always, so the Mentee Log draws "Full record" for each of them.
`test_student_record_access.py::test_a_mentor_opens_their_own_mentees_record_and_nobody_elses`.

### Rule 1 applies to telemetry too: Sentry is one init per process, and the scrubbers are the floor

Sentry is a second HTTP transport out of the process, carrying whatever the SDK
attaches, and `student_data_egress_allowed` does not guard it. Three processes
report, each to its own Sentry project through its own DSN, each through
**`init_sentry(service, dsn)` in `apps/api-py/app/observability.py`** and
nothing else: `reep-api` (`app/main.py`, `SENTRY_DSN`), `reep-scheduled-jobs`
(`app/retention_job.py`, `SENTRY_JOBS_DSN`) and `reep-interview-worker`
(`app/voice_platform/queue/worker.py`, `SENTRY_INTERVIEW_WORKER_DSN`). A blank
DSN is OFF and says so once; a process never borrows another's DSN — the
retention job runs on the api's task definition with `SENTRY_DSN` in its
environment and must not read it, because a nightly sweep filed under the api's
issues is a job nobody watches. Three constructor flags are rule 1 —
`send_default_pii=False`, `include_local_variables=False`,
`max_request_body_size="never"` — and `tests/test_codebase_guards.py` reads
that file as text to pin them, plus the four hooks (`before_send`,
`before_send_transaction`, `before_breadcrumb`, `before_send_log`) that route
every payload through **`app/telemetry_scrub.py`**: the query string is
allowlisted (a `?token=` never travels, a `?board=` does), log arguments are
redacted as well as the formatted line, the mail transport's body log is muted,
and a scrubber that raises drops the event rather than shipping it.
`tests/test_observability.py` proves it against the real SDK with a capturing
transport. The browser twin is `apps/web/src/app/core/telemetry-scrub.ts`, the
SDK is reached only through `apps/web/src/app/core/sentry-lazy.ts` (a direct
dynamic import of the package ships the Session Replay recorder to reach four
symbols), and **Session Replay stays unconstructed** — it cannot be blocked per
route and nineteen routes render a student's records. The environment tag is
`ENV` verbatim (`prod`, not `production`), the release is the commit sha baked
into the image and stamped into the SPA by `deploy.yml`, and the whole design
record is `docs/sentry-playbook.md`.

## The v2 student screens (2026-08)

Three screens the handoff adds, with their own tables and endpoints in
`app/routers/student_programme.py` — its own module so `routers/student.py`
(2 200 lines, and the file every other student change touches) did not grow
another 600. It mounts under the same `/student` prefix, so the client sees one
flat surface.

**Time Allocation Ledger** (`/student/time-log`) — six slots covering a 24-hour
day x five activity heads. **The unit is the half hour, stored as an integer.**
The day must reconcile to exactly 24 h before it may be submitted, and a float
column makes that a game of epsilons: drift across thirty cells leaves a
perfectly filled day sitting at 23.999999 and refusing to submit, with nothing
on screen to explain it. Integers make "does this add to 24" an exact comparison
against 48, and the API converts at the edge so the client still speaks hours.
Every cell is bounded twice, both from `SLOT_CAPACITY_HALVES`: no cell over its
slot's capacity, and no slot's five cells summing past it. `copy-yesterday`
copies only a **SUBMITTED** day — copying a half-finished draft spreads a mistake
forward with nothing saying where the numbers came from. Submitting latches the
day; it is then read-only. It does **not** replace `time_sheet_entries`, which
still answers the weekly SKILLING-hours-vs-target question the dashboard draws.

**English Baseline** (`/student/english`) — CEFR-aligned, AI-scored, one attempt
per semester. **Every score is nullable and that is load-bearing**: speaking is
scored after the other three, so "3 of 4 sections scored - Speaking pending" is
the healthy state, and a pending section rendering as a confident `0` in a 27px
numeral tells a student they failed something they have not sat. The client
branches on `status`, never on a falsy score. `provisional` is derived from how
many sections are scored, so the word cannot outlive the section that resolves
it.

**Mentor Meeting Log** (`/student/mentor-log`) — the student's own 1:1 history,
read from `mentor_notes` (extended with nullable `title` / `location`; existing
notes are **not** backfilled, because inventing a heading puts words in a
mentor's mouth on a screen the student reads). The mentor's internal vocabulary
is translated server-side — `FLAGGED` reads as "Flagged for follow-up".

**The landing stage cards** (`GET /api/student/programme`) — Reboot / Excel /
Elevate. **The catalogue is code and only the status is a row**
(`app/models/milestone.py`): seeding fourteen rows per student to say "not
started" would put the programme's shape in the database in thousands of places,
where a rename becomes a migration. One item is derived rather than stored —
`english_baseline` reads its status from the attempt, because two sources of
truth for one row is how that row ends up saying "not started" under a finished
report.

`python -m app.seed` seeds all four, including a ledger deliberately 0.5 h short
so the "0.5 h to reconcile" state is the one you see on a fresh database.

**THE LANDING LOST ITS STAT STRIP AND ITS "SPECIALIZATION CERT" ROW
(2026-09-17).** At the owner's request the four cards under the readiness /
recommendations row — the stage donut, the skill-badge row, "Mocks taken" and
the "Login streak" card — are gone from `features/student/home`, and the
Elevate card's "Specialization Cert" item (`spec_cert`) went with the whole
screen behind it: `features/student/certifications/` (the "Certification
Tracker"), its route, `GET /api/student/certifications`, and the
`student.certifications` feature switch — a row that gates nothing is the state
B2.2 exists to end, so the switch left with the endpoint. The two
"Finish <certification>" nudges that routed there (`/student/next-actions`
and the landing's recommendations fallback) went too, because a button to a
route that no longer resolves is worse than no button. **Deliberately NOT
removed:** the `certifications` / `certification_progress` tables, their seed
rows, the admin Catalogue screen that maintains them, and the readiness check
and analytics that read them — the "Certification completion" factor on the
student's readiness card still counts the same rows. That is the institution's
catalogue, not the student screen that was asked for, and the readiness score
would change on every deployment if it went. No migration: a stale
`student_milestones` row for `spec_cert` and a stale `feature_overrides` row
for the old key are both inert by construction (the milestone reader ignores
unknown keys; the override resolver skips them and the console lists them
under the bare key). The header's login-streak chip stays; only the card went.
The global `.donut`, `.bar-chart`, `.bar-labels` and `.streak-*` classes had
no other consumer and left `reep-v2.scss` with it. **The assistant read that
endpoint too**: `assistant_tools.deadlines` projected the tracker's due-dates
and `_deadlines` in the orchestrator read them out with a "View certifications"
action — found by the golden-set gate in CI, not by `api-imports`, because a
deleted function reached through a module attribute is a crash at call time
and not at import. The DEADLINES intent answers the courses' next tasks now
(`/student/courses`), and the readiness "Certification completion" factor's
`_FACTOR_ACTION` points at Uploads, where the tracker's own "Continue" button
already sent the student — `test_readiness_is_deterministic_with_score_and_weakest_factor`
indexes that map directly, so every measurable factor must name a live route.

**THE LEDGER LOCKS, THE STEPPER WALKED THROUGH UTC, AND THE WEEKLY STRIP SUMMED
A TABLE NOBODY WRITES (2026-09-22).** Three defects reported as one — "the time
sheet is completely broken, the filled data shows nothing, and a student can
enter any old day" — and each is a different fix in a different place.

  * **The date stepper.** `step()` did `new Date(`${day}T00:00:00`)`, added
    the days, and printed `toISOString().slice(0, 10)`. The first parses LOCAL
    midnight; the last prints UTC. In India — UTC+5:30, every student this
    app has — local midnight is 18:30 the previous day in UTC, so "Previous
    day" went back TWO days and "Next day" landed on the day it started from,
    every time. A student who filled Monday in, opened the screen on Tuesday
    and pressed the back arrow saw Sunday, empty, and reported their record
    gone. It never was; the stepper could not reach it, `ng build` cannot see
    it, and a test run in UTC cannot either. `features/student/ledger/ledger-days.ts`
    does the calendar arithmetic through `Date.UTC` and never touches local
    time; its spec walks forty days back and forty forward.
  * **"Today" is the SERVER's, in the college's zone.** The container runs UTC,
    so `date.today()` in a handler is yesterday for the whole of 00:00–05:30
    IST, and a student pressing Save at 00:30 was told the day "has not
    happened yet". `app/clock.py` (`local_today`, `PROGRAMME_TIMEZONE`,
    default `Asia/Kolkata`) is what the ledger reads now, the first load asks
    for no day so the server picks it, every `LedgerOut` carries `today`, and
    the client steps from THAT rather than from the handset's clock. An unknown
    zone name falls back to UTC with a warning rather than refusing to boot.
  * **The lock.** `LEDGER_EDIT_WINDOW_DAYS` (2) is how long a day stays open
    after it ends — Monday can be written until the end of Wednesday. Past
    that it is LOCKED in every direction: no save, no submit, no "copy
    yesterday" onto it, 409 with a sentence naming the day and the last day it
    could have been filled in. `_day_window` in `routers/student_programme.py`
    is the ONE function that decides open/future/locked; the read, the history,
    the save, the copy and the submit all ask it, so the chip on the screen and
    the 409 are one sentence. The inputs enable off `editable` alone.
  * **The record.** `GET /api/student/ledger/history?days=14` is one entry per
    calendar day — EMPTY / DRAFT / SUBMITTED, hours, locked or open — padded to
    every day in the window, because "Not logged" and "Locked" are things the
    strip must be able to say rather than things a student infers from a gap.
    The screen draws it as a strip of day chips, each a button onto that day.
  * **The weekly strip.** "Skilling this week" read `time_sheet_entries`, the
    old free-form time log's table, which nothing has written since the ledger
    replaced that screen — so it sat at "0 h" under the very cells it should
    have been adding up. `GET /student/timesheet` sums the ledger's SKILLING
    cells now, per day, and counts a legacy row only for a day that has no
    ledger row, so the seed's demo data and any old-screen history count once.

`tests/test_time_ledger.py` pins the lock's inclusive boundary (today minus N
is open, minus N+1 is not), the history's shape and the strip's sum;
`tests/test_clock.py` the zone and its fallback. Every `TODAY` in that module
is `local_today()`, so the tests mean the same thing at 01:00 IST as at noon.

**THE LEADERBOARD IS THE BATCH, AND THE BATCH IS ON THE SCREEN (2026-09-22).**
A student who registered naming a batch, was approved and seated, opened
Leaderboards and saw none of their batch mates. Two causes, both in
`routers/student.py`'s leaderboard section. A board listed only the students
who already HELD something on it (the 2026-09-17 rule, kept — "Rank 2 of 30"
over a zero is a lie), so a fresh batch drew "No ranking yet" for everybody and
nothing said the classmates were there; the board now carries the batch mates
who are NOT yet ranked as a second, unnumbered list (`unranked`, names only,
capped at 200), so the student sees their batch and sees who is ranked. And a
student with `students.cohort_id` NULL was "ranked" among every other
NULL-cohort student on the deployment, which is not a batch. `_leaderboard_scope`
resolves the batch where there is one, else the DEPARTMENT
(`students.department_id`, the second pointer `ancestry_of_student` reads —
every batch of it plus its unseated students), else nobody — and the response
SAYS which (`scope`, `scope_label`, composed by `batch_labels.compose` and
never `cohorts.name`), because "your batch has no results yet" and "you are not
seated in a batch" are different sentences with different fixes and used to be
one empty table. `overall` is the new default board: skills, VTU results,
streak and mocks each worth up to `OVERALL_POINTS_PER_COMPONENT` (25), SCALED
against the best in the batch — added raw, a streak of eighty days would BE the
leaderboard — summed to a score out of 100 and ranked on the ROUNDED number so
two students the label calls "72 pts" are level. `overall_points` is pure and
pinned without a database; a fresh account is on it through its sign-in streak
alone, which `test_board_values_leave_out_a_student_with_nothing_recorded` now
says out loud. Certificates are not a component: the skills board already ranks
what a certificate was verified into. The cache key is `(scope kind, scope id,
board)`, and a visibility change, or the office removing, restoring or deleting
an account with a `students` row, clears the whole cache
(`clear_leaderboard_cache`) rather than guessing which scopes a student is in.
A REMOVED account (`users.deleted_at`) is on no board, the badge boards on
Skilling (`routers/badges.py`) included; those keep their own programme-wide
scope and were otherwise not touched.

**Staff read these through rule 2's gate**, in `app/routers/mentee_records.py`:
`GET /api/mentor/students/{id}/ledger`, `.../ledger/summary` and
`.../english-baseline`. Every one names a student in the PATH, so every one goes
through `_assert_can_access_student` — imported from `routers/mentor.py`, never
reimplemented. The views are the **student's own**: `compose_ledger` and
`compose_english_baseline` are shared builders, so a mentor cannot see a
confident `0` where the student sees a dash. Read-only by design — a mentor's
instrument is the meeting note, which already has a write path.

The write paths behind the screens' buttons: `POST /english-baseline/start`
(idempotent — one attempt per semester is enforced by a unique index, so a
double-tap reads the existing row rather than 409-ing), `GET
/english-baseline/report` (ReportLab, local, so rule 1's gate does not apply —
do not add a remote renderer), and `POST /mentor-meetings/request`, which writes
a **mentor note** rather than inventing a requests table: that is already the
mentor's instrument for this student and already on their screen.

## The resume template and the builder's versions (2026-10-13)

**Every resume the builder composes follows ONE layout**, `RESUME_TEMPLATE_SECTIONS`
in `routers/student.py` (id `reep-standard-v1`, stamped into `resumes.content`):
name and a centred contact line, then Career Objective, Education (a table, the
current programme from the spine first, then earlier qualifications newest
first), Experience, Internships, Projects, Verified Skills, Key Expertise
(self-reported), Certifications (self-reported), Positions of Responsibility,
Achievements, Awards, Publications, Seminars, Activities, Languages, References,
Declaration. Empty sections still drop out. The AI polish is told to keep the
headings and the Education table and is DISCARDED when it changes either
(`_polish_keeps_template`), so a model cannot give one student a different
layout from their batch, nor print a CGPA or an institution REEP does not hold. A saved
version is CRUD: `POST /resume/generate` creates, `GET /resume[/{id}]` reads,
`PATCH /resume/{id}` renames / sets the target role / finalises,
`POST /resume/{id}/regenerate` recomposes it in the template from the builder as
it is now (same id and version), and `DELETE /resume/{id}` removes it. A
FINALISED version refuses regenerate and delete (409) until it is unlocked.
**There is no endpoint that edits the markdown as text, on purpose**: the
"Verified Skills" heading is a mentor's claim, and free text would let anything
be printed under it. `tests/test_resume_template.py` pins the order and the
CRUD walk.

## The daily job agent (2026-10-23)

**Every morning at 06:00 IST, `python -m app.job_scout_job` posts fresher jobs and internships for every specialization to the Jobs board, with nobody's hand on it.** The owner asked for exactly that. `docs/job-scout.md` is the runbook (keys, flags, deploy, how to check a morning), `app/job_scout.py` decides everything a run does, `app/job_sources.py` is the sources, and the schedule is `reep-job-scout-daily` in `infra/cdk/reep_core/stack.py`. **OFFICIAL APIs ONLY, all of India: the companies' own job feeds, and Adzuna and Jooble.** LinkedIn and Naukri forbid scraping and block it within days, which would make the morning run a thing that quietly stops working. Adzuna's terms ask for "Jobs by Adzuna" with a link on every advert shown, so `source` rides on all three job rows (`office` / `adzuna` / `jooble` / `careers`) and `core/job-source.ts` prints the credit.

- **ONE ROW PER (ADVERT, COURSE), and that is what makes targeting exact.** `visible_clauses` skips the track clause for a student with no track, and a student on a course with no specialization (BGSCET's Digital Marketing and Logistics MBAs) has none. So a row with its course widened to NULL would reach those students with adverts found for HR. `source_ref` (unique, and unused until now) is `<board>:<advert id>:<course id>`, so **no migration was needed**.
  - The same advert found for two specializations of one course is one row with both codes.
  - An advert on both boards, or one the office already posted, is not posted again, and that dedupe also reads rows closed in the last 90 days.
  - A closed posting is never reopened.
- **An agent posting is CLOSED, never removed.** `DELETE /admin/jobs/{id}` answers 409 for one, and the sheet greys Remove with the reason when one is ticked (the same way it does for a posting with applicants): a deleted row is forgotten and the next morning would post the same advert again. The age sweep closes agent postings older than `JOB_SCOUT_MAX_AGE_DAYS` (30) and never touches the office's.
- **BOTH BOARDS PUT A KEY IN THE URL, and three layers keep it out of logs and Sentry.** Adzuna takes its key in the query, Jooble in the path.
  - httpx logs every request at INFO with the whole URL, and the job turns INFO on. A `logging.Filter` on the `httpx` logger in `job_sources.py` drops the query and blanks Jooble's path. **Writing the end-to-end test is how this was found**: it passed alone and failed beside the DB suite, because only there was the root logger at INFO.
  - `telemetry_scrub.py` filters `http.query` on breadcrumbs and on the SDK's `http.client` spans, and `_SECRET_PATH_SEGMENTS` blanks the Jooble path.
  - Every failure is re-raised as `SourceError` naming the board and the status, `from None`.
  - `tests/test_job_sources.py` proves all of it against the real SDK, and each test fails with its layer removed.
- **The keys are opt-in per board and gated on `harden_ecs`**: `jobScoutAdzuna` / `jobScoutJooble` in `cdk.json`, set only AFTER the keys are in `reep/external`, for `sentryJobsDsn`'s reason (a task naming a missing secret key does not start, the api included). The schedule exists only when a board is on. `JOB_SCOUT_ENABLED=false` is the incident switch with the keys left in place.
- **THE COMPANY FEEDS NEED NO KEY, AND THEY ARE WHAT RUNS TODAY (2026-10-23).** The owner would not sign up for Adzuna or Jooble, and both block a robot's sign-up (Adzuna's form has a reCAPTCHA; Jooble answers 403). So `CareersSource` reads the public, read-only job APIs that Greenhouse, Lever and SmartRecruiters publish for each company in `COMPANY_BOARDS` (38 companies, each checked by hand to have open roles in India). Its `search` has the boards' shape, so `job_scout.run` treats it as one more board. `jobScoutCompanies: true` in `cdk.json` sets `JOB_SCOUT_COMPANY_BOARDS=true` and creates the schedule; no secret, so it cannot stop a task from starting. `tests/test_job_careers.py` holds the source.
  - **A feed cannot be searched, so its rules are strict.** India only; a title or level that says entry or intern (SmartRecruiters' own `experienceLevel` must be `entry_level` or `internship`, because "Associate" there is a level with experience); "Staff", "Account Executive" and levelled titles ("Analyst II") are not entry roles; and a topic per stream in `CAREERS_TOPICS`, keyed by `SEARCH_TERMS`' phrases (a test fails when a phrase has none). The whole description is read to judge the years asked for, and `_new_posting` stores the first `DESCRIPTION_MAX` characters.
  - **A description is fetched only for a role a search matched, and once.** SmartRecruiters' and Greenhouse's lists carry none; reading every posting in full would be a thousand calls a morning to Bosch alone.
  - **A COMPANY POSTING CLOSES WHEN THE COMPANY STOPS LISTING IT, NOT BY AGE.** A feed lists only open roles, so `lists_open_roles_only` exempts it from the 30-day stale rule and the age sweep, and `close_withdrawn` closes a careers posting whose company's feed was read IN FULL that morning and no longer lists it. A company that failed, or a SmartRecruiters feed cut short by `SMARTRECRUITERS_MAX_PAGES`, closes nothing. Backstops: `CAREERS_MAX_ON_BOARD` (60 days on the board) and `CAREERS_MAX_POSTING_AGE_DAYS` (a role first posted over 120 days ago is a standing advert and is not posted). The external id is `<ats>/<token>/<id>`, because a slash cannot be part of a URL segment, so the company is everything before the last slash.
  - **The volume is honest, not large**: about 15 to 25 fresher roles across all streams on a first morning, mostly from startups and MNCs. Adzuna and Jooble, when their keys are added, cover the whole market.
- **A student reads the JD on REEP (2026-10-23).** `JobRowOut.description` carries the posting's description (None when there is none) and "View JD" opens it in a row of its own under the posting (TC-330). For an Adzuna or Jooble posting it is the BOARD'S SUMMARY; for a company feed it is the company's own text, shortened; both at most `DESCRIPTION_MAX` characters, and the panel says which (`jdNote` in `core/job-source.ts`). The full text stays behind Apply.
- **Rule 1 by shape:** a search carries a catalogue phrase, a location and the key, a company feed is asked for its list with a GET and nothing else (a test checks every request), and `job_sources.py` imports no model (a test reads its imports). Each run writes one `job_scout_run` audit row with `actor_type="SYSTEM"`, and `GET /api/admin/jobs/scout-status` reads it for the sheet's status line.
- **IT WAS ATTACKED BEFORE IT SHIPPED, AND `tests/test_job_agent_hostile.py` IS THE RECORD.** Every test there failed on the first commit. Each is a way the real world breaks a job that runs with nobody watching:
  - **ONE BAD ADVERT MUST NEVER COST THE MORNING, because the run is ONE transaction.** A NUL byte in a title, or an advert id longer than the unique btree on `source_ref` accepts, failed the COMMIT, so every good posting that day was lost with it. `plain_text` strips control characters, an id or a link carrying one is refused (they reach the row as they arrive), ids over `MAX_EXTERNAL_ID` are skipped, and a row of a shape nobody expected (Adzuna's `company` as a string) is skipped by `_parse_page` instead of raising out of the whole run. `search_all` also catches what nobody planned for, records the error TYPE (never the message, which can carry the URL and its key) and stops asking that board.
  - **THE PORTAL IS THE COLLEGE'S OWN, SO A SCAM ON IT READS AS CHECKED.** `is_suspicious` is STRICT, the owner's choice: a fee, a deposit, "pay Rs 999 to apply", "guaranteed placement", "earn … per day", and "data entry" or "work from home" in the TITLE. Negations ("no registration fee") are read, and a description's "work from home" counts only beside "part time", so ordinary hybrid jobs and an accountant's Tally duties stay.
  - **"100% MATCH" ON A POSTING THAT LISTS NO SKILLS WAS A LIE**, and most adverts name none of the skill catalogue's words. `JobRowOut.match_percent` is None there and every screen prints "Skills not listed", the office's postings included. Do not put the 100.0 back.
  - **A DELETED COLLEGE MUST NOT TURN ITS POSTINGS INTO EVERYONE'S.** `college_deletion` clears `jobs.college_id` and `course_id`, and NULL on both reads as "every college, every course" to `visible_clauses`. `close_expired` closes an agent posting with no course, and the dedupe ignores such orphans.
  - **Two runs at once** (the Ops task pressed at 06:00) used to collide on `source_ref` at COMMIT and page Sentry. `run()` takes `pg_try_advisory_xact_lock(RUN_LOCK_KEY)`, a key outside int4 so it can never meet the interview's `hashtext` locks, and the loser exits 0.
  - The smaller ones: the internship search's say-so no longer makes a full-time job an "Internship · " posting (the description must describe one); "minimum 3 years" and "3 to 5 years" are read as experience and "Age 21 to 28 years" is not; Relationship Manager is a fresher role; an advert older than the age limit takes no place in the day; another college's office posting no longer hides this college's job; and the cap is 5 a day per specialization.

## The Skills & Badge dashboard (2026-08)

Implements the "REEP Student Skills & Badge Dashboard Developer Framework"
document end to end for CURRENT students, structured exactly as its §19 asks:
REEP stage → skill category → skill → evidence → badge → growth.

**The 48-badge catalogue is code** (`BADGES` in `app/models/badge.py` — 12
managerial, 16 sectoral across four tracks, 10 platform/technical, 6 thinking,
4 readiness), the milestone rule again: only student state is rows. The
framework's §18 "admins add/edit badges" is therefore a code change HERE, on
purpose; what admins maintain in the database is the **Approved Certification
Catalogue** (§12, CRUD under `/api/admin/approved-certifications`), evidence
verdicts, manual awards/revocations and assessment scores. `test_badges.py`
pins the catalogue's shape against the document.

**Certificate ≠ badge, enforced by the write path**: a student attaches
evidence (`badge_evidence` — several per badge, one per §11 type; the document's
own Negotiation example) and only a staff APPROVE on
`/api/mentor/badge-evidence/{id}/review` mints the `student_badges` EARNED row,
points stamped from the catalogue at that moment. §13 display status is DERIVED
(`compose_badges`), never stored. Readiness badges (§8) refuse evidence — they
arrive only through the manual-award endpoint when assessment thresholds are
met. Revoke is the Main Admin's and deletes the award row, never the evidence
history — **and it now writes the before-image first**, which it did not. It was
the one destructive endpoint in the product leaving no record of what it
destroyed: the row went and with it "this student earned this badge, on this
date, for these points, awarded by this person", while every sibling destructive
endpoint (governance, the leave policies, the registration rules, SWOC, the
stage-rule catalogue) already wrote one. **A `deleted_at` flag cannot do this job
here**: `uq_student_badge` is one row per (student, badge) and `_award` re-earns
a badge by UPDATING that same row, so a flagged row would be revived and
overwritten the next time the badge was awarded — losing the revocation exactly
as the delete did. The record has to live where the unique constraint does not
reach, which is `audit_events`.

**Growth (§9/§15)** is `capability_assessments` — seven capabilities, 1–10, at
T0–T4, staff-entered and upserted. Every derived score is nullable and a
missing score renders as a dash: growth is claimed only when T0 AND a later
checkpoint both exist, because 0.0 with only a baseline says "has not improved"
when nobody has looked. The §16 **Most Improved** leaderboard ranks that growth,
not points; every leaderboard honours the existing `leaderboard_opt_out`.

Screens: `/student/badges` (journey strip, tiles, §14 detail + claim form,
growth table, leaderboards) and `/mentor/badge-centre` (verification queue
with the scoped certificate stream, assessment entry, §17 skill profile,
cohort CSV at `/api/admin/badges/export.csv`). Staff reads reuse
`compose_badges`/`compose_growth` — the mentor sees exactly the student's own
screen. Rule 1 untouched (nothing here calls a model); rule 2 via
`_assert_can_access_student`, with the pending queue narrowed in SQL.

### The claim-to-verification walk, and the day every upload was refused at the edge (2026-09-17)

**EVERY FILE UPLOAD IN THE PRODUCT WAS ANSWERED 403 BY THE WAF, AND THE SCREEN
SAID THE FILE WAS THE WRONG KIND.** `infra/cdk/reep_core/edge.py` applies
`AWSManagedRulesCommonRuleSet` to the CloudFront distribution, and that group's
`SizeRestrictions_BODY` rule blocks any request body over 8 KB. A certificate
on Skilling, the CV and photo `/register` requires, a leave attachment, a
faculty signature, an alumni resume, an upskilling certificate — every one is a
multipart POST far past 8 KB, refused before CloudFront, the ALB or the API saw
it, with a body that is not JSON. The Skilling client read no `detail` and
printed its fallback, "Certificate upload failed (PDF or JPEG, up to 5 MB)", so
a student attaching a 2 MB JPEG was told their JPEG was the wrong file, and the
one fact that pointed at the edge — the status code — was the one thing the
message left out. Nothing in the API could catch it and nothing in the suite:
the WAF exists only in front of the deployment. The rule is COUNTED now
(`COMMON_RULE_SET_COUNTED`, harden phase only, so the import mirror stays
byte-identical), pinned by `test_the_common_rule_set_counts_the_body_size_rule_
so_uploads_reach_the_api`, and **it takes a deploy of the `edge-waf` option in
`cdk-deploy.yml` to take effect** — a merged infra fix that is never deployed
costs exactly as much as never writing it. The API bounds every body itself
(`document_store.MAX_BYTES`, the per-handler `read(MAX + 1)`), which is why the
edge rule is counted and not scoped down to a hand-kept list of upload paths.
Every upload client now prints the server's sentence or the STATUS
(`detailOf`, the verifications screen's helper copied where it was missing), so
the next refusal at the edge reads "(403)" and not a sentence about JPEGs.

**THE REGISTRATION COMPLAINTS OF 2026-09-29 WERE FOUR BUGS, NOT ONE.**
Sixteen students mailed about `/register`. (1) **403**: the size rule above
was counted, but `CrossSiteScripting_BODY` and its three siblings still
blocked, and they read compressed bytes in a CV or a photo as markup. About
half of realistic generated CVs were refused, and the same file was refused
on every retry. The four body rules are counted in the group and re-blocked
by LABEL (`body-rules-outside-uploads`) for every request that is not
`multipart/form-data`, so a JSON body is exactly as protected as before.
The label casing is `_Body`, not `_BODY`; a wrong label matches nothing.
Needs the `edge-waf` deploy. (2) **504**: `submit` and `attach_document`
were `async def` doing blocking database, EFS and S3 work, so one save froze
the whole process past CloudFront's 60 s timeout. They are plain `def` on
the threadpool now, the per-address limiter has a lock, and
`tests/test_registration_concurrency.py` fails if the loop blocks again.
(3) **422**: the LinkedIn check refused `in.linkedin.com`, `m.linkedin.com`
and `lnkd.in`, and the form printed FastAPI's list `detail` as "(422)". Both
are fixed; the form names the box (`features/register/form-checks.ts`).
(4) **Only a Gmail on the admin side**: the college-email box had
`autocomplete="email"`, so phones filled it with the saved Gmail, and the
panel labelled only the personal address. The form and the API now refuse
the same address in both boxes, and a public-mail domain in the college box.
They deliberately do NOT refuse an address merely off the college's list,
because a college that has not listed its domains must not lose every
applicant; the reviewer's domain check still covers that. The panel has a
"College email" row.

**AND THE HEADROOM FOR A RESULTS DAY, THE SAME EVENING.** A load test and 30
days of CloudWatch found ceilings nothing had hit yet, each worst on the
busiest day. The WAF's per-IP rate rule (2000 per 5 minutes, about 6.7 a
second for a whole campus or a carrier-NAT'd mobile network behind one
address) is one of them and is deliberately LEFT for the owner to decide,
because raising it loosens a security control. (1) The live database was `db.t4g.micro` (about 80-110
connections) while each api task may hold `DB_POOL_SIZE + DB_MAX_OVERFLOW`,
which defaulted to 40, for up to `apiMaxTasks` tasks. `dbInstanceClassTarget`
in `cdk.json` moves harden to `db.t4g.small`; `dbInstanceClass` in
`cdk.context.json` stays the LIVE value the import tool read, the
`dbMultiAz`/`liveDbMultiAz` split again. The task sets the pool to 10 + 10
(`API_DB_POOL_SIZE`, gated on `harden_ecs` like every variable). (2) Every
alarm watched from INSIDE the account, so a broken DNS record or distribution
would leave the ALB healthy and nobody told. A Route 53 health check through
the public name, with an alarm that treats silence as down, lives in the edge
stack because Route 53 publishes its metric in us-east-1 only. **It is a TCP
connect to 443, not an HTTPS request**, and the first deploy is why: the
distribution is TLS 1.3 only and Route 53's checkers speak TLS 1.2 at most, so
the HTTPS version failed all sixteen checkers with `protocol_version` against a
site answering 200. Do not "fix" that by relaxing the viewer policy; a
Synthetics canary is the upgrade if the certificate ever needs watching. Its
SNS email must be CONFIRMED from the inbox before it delivers anything.

**THE 504 OF 2026-10-01 WAS MADE ON THE PHONE, AND NOTHING ON THE SERVER COULD
HAVE SEEN IT.** A student pressed Submit on `/register` over weak mobile data
and was shown "(504)". CloudFront's 5xx rate was zero at one-minute resolution,
the ALB's too, and the API had no request at all — an upload cut on its way IN
does reach the app as a `ClientDisconnect` 400 (one exists, 2026-09-29), and
there was none. The Angular service worker had answered it: ngsw intercepts
every fetch, POSTs included, and its `safeFetch` turns a network error into a
`504 Gateway Timeout` it makes up, empty body. Reproduced in Chromium against
the exact ngsw-worker.js the app ships. Four things changed together. (1)
**`/api/` never passes through ngsw** — `apps/web/public/reep-sw.js` is the
registered worker; see "The phone". (2) **The form tells the truth**:
`fetch` throwing now says the connection dropped and that pressing Submit again
is safe, where it used to say "Is the API running on :3300?" — a developer's
sentence on the most public screen — and a bodiless 502/503/504 says the
application may still have arrived (`form-checks.ts`). (3) **A retry of the
same form is the same application.** The reply can be lost AFTER the row is
saved, and the retry then met the duplicate guard's opaque 409, which reads as
a refusal to somebody who has just applied. The form mints one random key per
filled-in form (`newSubmissionKey`, re-minted only by `reset()`), sends it as
`submission_key` on every submit, and `registrations.submission_key_hash`
(migration `f4a2c9e7b1d3`, sha256, never the key, no backfill) lets
`_duplicate_answer` answer a repeat with the ORIGINAL 201. A different key, no
key, or an older row still meets the opaque 409, so the form is no more of a
"has X applied" lookup than before. Two submits racing past the guard used to
500 at the unique index on `db.flush()`; the loser is now answered exactly as
a retry would be. `tests/test_registration_retry.py`. (4) **Visibility**: the
browser reports a registration network failure to Sentry through Angular's
`ErrorHandler` (a fixed sentence, nothing from the form), because it is the
one failure no log or triage agent can otherwise see; a 422 on
`POST /api/register` logs the refused field NAMES and error types and never
their values (`log_form_refusal`, `tests/test_registration_refusal_log.py`) —
seventy fast 422s that morning had no recorded cause; and CloudFront writes a
request log (standard logging v2 to `reep-cloudfront-logs-<account>`, edge
stack, harden only, `cloudfrontLogDays` 30) whose field list deliberately
LEAVES OUT `cs-uri-query`, `cs(Cookie)` and `cs(Referer)`: the setup, reset
and activation links are `?token=`, the cookie is the session, and a referer
carries the previous page's query string. The legacy log cannot omit fields,
which is why it is not used. Takes an `edge-waf` deploy to exist.

**"COULD NOT REACH THE SERVER" ON SKILLING WAS THE PHONE FAILING TO READ THE
FILE (2026-10-14).** Students reported certificate claims failing with that
sentence on a day the API took 138 uploads. The CloudFront log showed the
failing POSTs to `/api/student/uploads` from Android sending their headers
(136 and 142 bytes) and then no file before hanging up. A picked `File` is a
reference the browser reads again at send time, and a file held by Drive,
Photos or another app can be unreadable by then (`ERR_UPLOAD_FILE_CHANGED`).
The claim form asks for four more fields after the certificate, so there is
always time for that. `core/picked-file.ts` copies the bytes into memory at
pick time, and a file unreadable even then is refused on the spot with
`UNREADABLE_FILE_MESSAGE`. A thrown upload now says the connection dropped,
the upload id is kept so a retry files the claim without uploading twice,
and both failures go to the browser's Sentry as fixed sentences. `/register`
(all nine of Sentry's "Registration submit failed" reports, 1 to 7 Oct, came
from Android) and `/student/uploads` copy the file the same way since the
same day, and so do the alumni resume, faculty upskilling, leave attachments,
both signature uploads (`/mentor/signature` and the account page) and the
resume builder's photo. Any new upload screen should read the file through
`readPickedFile` and say `UPLOAD_DROPPED_MESSAGE` when `fetch` throws, both
from `core/picked-file.ts`. Only the Main Admin's catalogue and spreadsheet
imports, desktop screens, still send the picker's reference. The other failure in that
log was a phone sending 0.87 MB in 121 s (CloudFront 408). That is the
photo-downscaling item the 2026-10-01 entry above left undone.

**THE CLAIM FORM FILED INTO A QUEUE THE MENTOR'S SCREEN NEVER OPENED.**
`/student/skilling` stores the certificate through `POST /student/uploads` and
files `badge_evidence` against it (`POST /student/badges/{code}/evidence`);
`/mentor/verifications` read `GET /mentor/skill-claims/pending` — the legacy
`skill_claims` table, which no client has written to since the Skilling screen
replaced the per-skill claim form. Every claim a student filed went into a
queue nobody's screen listed, and the mentor saw "Nothing waiting for your
review" over a growing list of real claims. The screen reads
`/mentor/badge-evidence/{pending,reviewed}` now, with the file name on each
row, and the `skill-claims` endpoints stay only for the resume builder's read.
Three decisions: APPROVE lights the badge, MORE_INFO sends it back, REJECT
refuses it — **and the last two are 422 without a note**, because the note is
the whole of what the student is told, on screen and by mail. **One decision
writes both rows**: the certificate behind a claim was PENDING_REVIEW in the
Documents queue on the same screen, decidable in the opposite direction, so
`review_evidence` now writes the claim's verdict onto the upload too (VERIFIED /
NEEDS_CHANGES / REJECTED, same reviewer, same note — never over a verdict the
document already carries), and `pending_uploads` leaves out a file a pending
claim stands on. The board draws the reviewer's approval as a BLUE `verified`
tick on an EARNED tile and nothing else: an unclaimed badge is unmarked, a
claim in review wears a clock, and the tap-to-preview lights the hexagon
without the tick, because a preview that wore it would be indistinguishable
from a verified badge. `tests/test_skill_claim_flow.py` walks all of it.

**SKILL VERIFICATION IS THE OFFICE'S CHOICE OF PERSON, NOT A MENTOR'S BY
MENTORING (2026-10-16, the owner's decision: "remove skill verification from
all mentors").** `mentor.verifications` ("Verify skills & evidence") left
`MENTOR_FUNCTIONS`; it is held ONLY as a grant the Main Admin makes in Who can
do what, with a reason and a reach (a student, a batch, a department, a
college or the programme), and a holder sees exactly the students that reach
covers — their own mentees included only if it covers them. `app/verifiers.py`
is the one gate, and every verification door asks it: the claim queue, its
reviewed strip, a claim's file and decision (`routers/badge_verification.py`),
the Documents queue and its decision (`routers/mentor.py`, the other half of
the same screen, which had been the mentor's group and, for the Main Admin,
every file with no grant), the legacy `skill-claims` review (no screen, but it
still marks a `student_skills` row verified, which the resume prints under
"Verified Skills"), and a manual award of a skill badge (an award is a
verification without a claim; the Main Admin keeps that escape hatch). Two
read doors widen for a verifier and nothing else: `/mentor/uploads/{id}/file`
and a claim's file, both GETs. RULE 2 IS UNCHANGED: `_assert_can_access_student`
still admits a mentor to their own group everywhere else (notes, the notebook,
the 360 read); verification simply stopped asking it. It also stopped letting
a Main Admin with a SCOPED verification grant decide any student by id, which
rule 2's "the admin sees all" had quietly allowed while the list was narrowed.
The sidebar row and the `/mentor/verifications` route guard both read the
capability. A claim nobody's grant reaches waits, and nobody is mailed, until
the office grants somebody. The dev seed grants Test Mentor the key for the
seeded college so the screen has a reviewer on a fresh database. The student's
screens say "a faculty reviewer" and "Waiting for review" rather than "your
mentor". `tests/test_skill_claim_flow.py` pins the mentor's refusal at every
door and the grant's reach as the fence.

**MAIL: THE VERIFIERS HEAR OF THE CLAIM, THE STUDENT OF THE DECISION, AND THE
SWITCH IS DERIVED.** `app/badge_mail.py`, through `mailer.deliver_once` — keys
`badge-claim:{id}:{recipient}` (one message per claim per verifier, however
often it is retried; the recipients are `verifiers.verifiers_for`, the same
question the gate asks, so they were the student's mentor until 2026-10-16) and
`badge-decision:{id}:{status}` (stable per transition, different per
transition). Both run AFTER the commit and never raise. The student's mail
carries the reviewer's note: unlike a leave approver's note, which is written
for the office's file and stays out of the mail, this one is written TO the
student and the API refuses a rejection without it. Nothing from the record
travels — no USN, no marks, no certificate. `settings.badge_mail_active` is
`password_login`'s three-state idiom and deliberately NOT `leave_mail_enabled`'s
boolean: `BADGE_MAIL_ENABLED=true` forces on (the console outbox), `false`
forces off, and blank DERIVES the answer from `mail_configured`. That protects
the machine `leave_mail_enabled`'s false default protects — no transport, no
SENT row about a message nobody received — and turns the notifications on for
the production task, which already carries `SES_FROM_ADDRESS`, with no second
variable in a task definition to remember. `app.badge_mail` is muted from
Sentry beside `app.leave_mail`.

**A CANDIDATE'S COMPLETE DETAILS ARE THE OFFICE'S, AND THE OFFICE MAY HAND THE
READ TO A FACULTY MEMBER.** `admin.student_records` ("View student records",
PROGRAMME, `carries_pii`) is the read side of the roster, split off
`admin.students`, which is the EDITOR. `GET /admin/students/{id}/360` and the
route `/admin/students/:id` hang on it; the Main Admin holds both keys by
baseline and sees no difference, a faculty member holds whichever Governance
granted, and rule 2 still runs underneath: a MENTOR opens their OWN mentees'
records (by mentoring them since 2026-10-16; see rule 2's "a mentor opens
their own mentees' records"), plus the students a SCOPED grant names
(2026-10-15, see rule 2's "the other read-only widening"). Two panels were missing from "complete":
the profile as FILLED IN (the open-items card named only the gaps) and the
student's DOCUMENTS, which had no by-id read anywhere; both are on the
composite read now (`profile`, `documents`), the Documents tab is real, and
the bytes stream through `GET /mentor/uploads/{id}/file` — rule 2's gate
applied to a file. The way in: a **View** eye beside the pencil in the
roster's pinned actions column (the Student column's link was the only route
to a record and that column can be hidden — the office had), and a **Full
record** link on the Mentee Log, drawn only where the record will open
(`record_openable` on `GET /mentor/mentees`, the same `_openable_clause` the
list below reads, so a grant for another department draws no link that the
page would refuse), and since 2026-10-15 a
**Student records** row under "Granted access" (`/admin/student-records`), the
list a faculty member uses to find the students a scoped grant gives them.

**THE STUDENT 360 PAGE WAS REDESIGNED ON 2026-10-15 FROM AN APPROVED PROTOTYPE**
(`docs/prototypes/student-360/index.html`): a candidate column (contact, the CV,
who can open the record), six KPIs, "Needs attention", and tabs for Overview,
Resume, Skills, Leaderboard, Academics, Interviews, Time sheet, Documents and
Access. **The resume is the CV the student uploaded when they registered** (the
owner's words): approval moves it into their uploads as kind `RESUME`, titled
`registration.APPLICATION_CV_TITLE`, and `Document360Out.from_application`
marks it. **It is drawn with pdf.js (`shared/pdf-preview`), never an iframe**,
because every upload is served as an attachment so a student's PDF never runs
in this origin; pdf.js paints canvases and the core library never runs a
PDF's scripts. Pinned at 4.10.38: 5.x calls `Map.prototype.getOrInsertComputed`,
which only the newest browsers have, and the one unguarded built-in 4.x needs,
`Promise.withResolvers`, is filled in on the lazy path. It runs on the main
thread (`globalThis.pdfjsWorker`), so there is no separate worker asset outside
the service worker's manifest. `GET /admin/students/{id}/record` serves the
other panels from the composers the mentor and student screens use, and
`?download=true` on a file or built resume writes a `DOWNLOADED` audit row
against the student, which the record's own history lists; a view writes
nothing. "Give access" posts Governance's own `POST /admin/governance/grants`
with a scope, so a grant has one writer and one audit trail. **The Access tab
is the Main Admin's alone (2026-10-17, the owner's decision)**: `GET
.../access` is `require_admin`, the page draws the tab and the rail's "Who can
open this record" card for the office only, and `/360` sends an empty sign-in
list and history, and no disable or removal reason, to anybody else. A faculty
member reading on a grant or a mentee still sees THAT the account is disabled.
**Two panels of
the old page came back the same day**, because the prototype had no place for
them and this was the only screen showing the first: the mentor history (every
faculty mentor, who moved the student and why) on the Overview's Faculty mentor
card, and Promotions and graduations (who made each move, the reason) on the
Academics tab. Both read the `/360` payload the page already loads.

## Reachability and keyboard access — what a full browser audit found (2026-09-10)

All 38 routes were driven in a real browser as all four roles. **The business
logic held: five cross-role write chains passed end to end** (leave → approval →
official PDF with both signatures; jobs → student feed with match % and alumni
feed correctly without it; SWOC → student home + Faculty/TPO Log with the
viewpoint derived from role; governance grant → faculty sidebar → revoke;
student create → roster + batch + SWOC + analytics counters → delete with no
orphan rows). Eleven defects were found and every one was about **reachability
or accessibility**, not business rules — with a single exception. The lessons
worth keeping:

**A leave request whose dates ran backwards was accepted, approvable and
printable.** `from_date` and `to_date` were two independently declared `date`
fields with nothing relating them, so 20 Dec → 10 Dec stored a span of MINUS
TEN days, reached the approver's queue with a live "Mark Sanctioned" button, and
rendered onto the college's own form. The refusal is `LeaveIn`'s
`dates_run_forwards` validator — **on the schema, because the leave form and its
buttons must not change**, so the request has to die before it is built.
`from == to` stays legal: a PERMISSION slip and a one-day CASUAL leave are both
written that way on the paper form. It needed a SECOND fix to be usable at all:
FastAPI answers a schema error with `detail` as a **list**, so the form rendered
`[object Object]` — `leave.component.ts` now uses the same `detailOf` helper
`governance.component.ts` already had.

**A screen that is routed is not a screen anyone can reach.** Four fully working
screens — `/student/profile`, `/student/uploads`, `/student/courses` and
`/admin/catalogue` — were reachable only by typing the URL. The sidebar
comments *said* they were "reached from the screen that owns the work" and "from
the Analytics tiles", and for the others that was true; for these four nobody
ever added the link. **A route with no `routerLink` anywhere is dead**, so grep
for the path, not for the component. Two further traps live here: `routerLink`
in a standalone component with `imports: []` is **inert markup that renders and
does nothing**, and `/mentor` loading the notebook component *as well as*
`/mentor/notebook` meant `routerLinkActive` matched nothing, so every faculty
member landed at every sign-in on a screen with no nav item lit. One screen, one
URL — `/mentor` is a `redirectTo` now, deliberately **without** a `canActivate`,
because Angular resolves redirects while matching the URL, before guards, so a
guard there would be dead config that reads as protection.

**`hidden` on a file input makes the picker mouse-only.** `hidden` is
`display:none`, which removes the input from the focus order, and a `<label>`
wrapper is not tabbable either — so on `/mentor/signature` the only
keyboard-reachable control was **"Remove…"**: you could delete your signature
but never upload one, and on the public `/register` an applicant could not
attach a CV or headshot at all. Hide these with geometry
(`position:absolute; width:1px; clip`), never `display:none`, and give the label
a `:focus-within` ring or the focusable invisible input is a trap. Five other
screens already used the correct `<button (click)="picker.click()">` pattern.

**`.icon` renders from ligatures, so an unlabelled icon is read aloud.** A
screen reader announced "hourglass top" and "add circle". 286 spans across 38
templates now carry `aria-hidden="true"` — but **blanket `aria-hidden` is not
safe on its own**: it silently unnames any control whose only content is the
icon. Five such buttons had to be given `aria-label`s, and the sweep script
reports them rather than guessing a name.

**A message inserted by `@if` is announced to nobody unless it is a live
region.** `/register` — the most public form in the product — showed "Your full
name and college email are both required." in a plain `<div>`: no `role="alert"`,
no `aria-invalid`, no focus move. 21 other templates already did this correctly.
Page titles had the same shape of problem: they were `<div class="dt-title">`,
so 29 of 38 screens had **no heading element at all**. Converting to `<h1>` is
visually free because `.dt-title` sets `margin: 0`. `/student/assistant` is the
screen to copy — real `<h1>`, `role="status"`, `role="log"`, named regions.

**And a warning about testing this stack in a browser harness.** Five findings
in that audit were false alarms produced by the harness, each of which looked
exactly like an app bug: a synthetic `.click()` does not drive the agent orb's
pointer-threshold gesture; `window.prompt` is auto-dismissed, so the code path
posts an empty value and the server's 422 looks like a broken button;
`element.textContent` does not include a `<textarea>`'s value, so a saved SWOC
line looked unrendered; a DOM read taken before a post-write refetch shows the
old list; and slicing an options list to the first 16 produced the conclusion
"Governance offers no `admin.*` capabilities" when it offers all 28. Restart the
API after touching a `.py` — there is no `--reload` here, and a stale worker
silently accepted a request the new validator refuses.

## The faculty & alumni pages (2026-08)

**Faculty** (any staff role, in the shell's staff nav): **Mentee Log**
(`/mentor/mentees` — mentees + meeting notes, on the existing
`/api/mentor/mentees` and `/notes` endpoints, all behind rule 2's gate),
**Leave Requests** (`/mentor/leave` — submit own, withdraw, the cover one is
asked for; the approval queue is the Main Admin's alone since 2026-09-16), and
**Upskilling** (`/mentor/upskilling` — the staff member's
OWN completed-course certificates, `app/routers/staff_upskilling.py`). The
upskilling shelf is keyed on `users.id`, not a Student row, goes through the
same hardened document_store as student uploads, applies its own per-user quota
(document_store's contract for any second `save_bytes` writer), and has **no review
workflow** — a staff certificate is a record, not evidence awaiting a verdict.

**The leave paper and the signature (2026-09).** `GET /api/leaves/{id}/paper.pdf` (`app/routers/leave_paper.py`, rendered by `app/leave_paper.py` with ReportLab, locally) is the college's OWN form PDF (`app/assets/leave_form_template.pdf`, the office's "Leave Form.pdf", pinned by size) with the request written onto it as a ReportLab overlay merged by pypdf — the form is never redrawn, and the field coordinates are measured from that exact file — for the applicant and for exactly the staff `_assert_can_decide` admits, every refusal the same 404. A staff member uploads ONE signature image at `/mentor/signature` (`PUT /api/staff/signature`, PNG/JPEG under 2 MB, replaced in place, `app/models/staff_signature.py`); it is drawn ABOVE the name and time in the two staff blocks of every paper they apply on and in the PROGRAM DIRECTOR block of every paper they sanction. **A signature is still a name and a time**; the image never replaces either, and the leave form's submit/decide endpoints and buttons are untouched (the owner asked for that) — the paper router imports `_leave_out` and `_assert_can_decide` rather than restating them.

**Alumni** are a real role: `Role.ALUMNI`, no Student/Mentor row, no staff
scope, session claims carry neither `studentId` nor `mentorId`. Their surface
is `app/routers/alumni.py`: `GET /api/alumni/profile` answers `created: false`
until they save one — that flag (never a falsy company string) is what makes
the client show the FIRST-LOGIN create form (current company + current resume,
resume required on create, kept-if-omitted on update) — plus their resume
download and `GET /api/alumni/jobs`, the postings sheet **without** the student
feed's match % / eligibility verdict (those are computed from a Student's
skills and marks, which an alumnus does not have). The shell's sidebar switches
on role (`navKind` in `layout/app-shell.component.ts`), and the SPA's `''`
route now routes by role through `homeRedirectGuard` instead of sending every
role to `/student`.

## The institutional spine and the Main Admin console (2026-09)

    College -> Department -> Course -> Specialization -> Cohort("Batch") -> Student.cohort_id

`app/models/institution.py` holds College, Department, `AcademicCourse` and
`AcademicSpecialization`; `students.cohort_id` is a real FK. **The student's
locked profile card is read THROUGH that join and stored on nothing** — one flat
LEFT-JOIN query in `routers/student.py::_institution_for`. Do not copy any of
it onto `students`: that is the backfill this shape exists to avoid.
`AcademicCourse` is prefixed because `Course` (app/models/course.py) is a taught
subject; `Specialization` is already three other things. The UI says "Course",
"Specialization" and "Batch".

**A BATCH IS A YEAR, AND THE SPINE IS ITS LINKS (2026-09-16).** A student who
joined a two-year MBA in 2026 is in batch **2026-28** — that span is the whole
of what a `cohorts` row is. WHICH college, department, course and
specialization they joined is the spine the row hangs off, and the row already
carries every rung of it as a real foreign key. Until this date both writers of
the spine MANUFACTURED the spine into the name as well — `seed_catalogue.batch_name`
and "Set up a college" agreed on "General MBA - Finance 2026-28" — so one fact
was stored twice: once as a foreign key `ancestry_of_student` reads, once as
words inside a string nothing can join on, free to disagree the moment the
office renamed a course on screen. And because `batch_label` is its own column,
**five endpoints each carried their own copy of `f"{cohort.name} · {cohort.batch_label}"`**
(`admin_students`, `swoc`, `registration`, `governance`, `admin_promotion`) and
six templates carried a sixth, so the year printed twice on every screen that
showed a batch. `app/batch_labels.py` is the one place the two are put back
together — `compose(course_name, specialization_name, name)` → "General MBA -
Finance · 2026-28" — with `apps/web/src/app/core/batch-label.ts` as its twin and
`tests/test_batch_labels.py` comparing the two separators as text, because two
implementations of one sentence drift the first time somebody edits one.
`display_label` is now served by `GET /api/register/hierarchy`, `/admin/cohorts`
and the admin cohort endpoints so no client composes it again.

**THE TAIL CARRIES BOTH `name` AND `batch_label`, AND GETTING THAT WRONG COST A
CI ROUND.** The first version took only `name`, reasoning that the seeder and the
setup screen now write the year into it so the label would be a duplicate. It is
— for the batches *those two* write. It is not for a batch the office typed a
name for, and `test_registration_hierarchy.test_a_batch_pins_its_department_and_college`
builds exactly one: "Chain Batch" at department level, where composing from the
name alone printed "Chain Batch" and the SPAN vanished from the reviewer's
"Approving seats them in …". A batch IS a year; a rendering that can drop the
year is this same bug arriving from the other side. So the year leads the tail
and the office's words follow — "2024-26 · Chain Batch" — with **one substring
test** keeping a section from stuttering: where `name` already contains the span
("2026-28", "2026-28 Section B", a legacy row renamed by hand) it stands alone,
because "2026-28 · 2026-28 Section B" is the duplication again. That is also what
keeps the NON-STANDARD batch the office types on College structure; its Name
field is **optional** now and `saveBatch` sends the label when it is blank. Migration `a4e7c92d1f38` repairs the rows already written, and its
predicate is the whole of its safety: it rewrites `name` to `batch_label` only
where the name is EXACTLY the string those two writers would have manufactured
from THAT ROW'S OWN links. A name a person typed, or one whose course has since
been renamed, no longer matches and is left alone — the seeder's own rule
("divergence is REPORTED, never corrected") applied to a one-time repair, which
is the difference between fixing what this codebase wrote and overwriting what
the office wrote. The downgrade is written rather than raising because the
manufactured string is a pure function of the links and the label, so going back
is exact.

**Course and Specialization are OPTIONAL, and the switch is one line.**
`HIERARCHY_LEVELS` in `app/models/institution.py` says which must be named on a
new batch; flip `required=False` to `True` and deploy — no migration, no second
edit. `AdminCohortIn`'s validator reads it and `GET /api/admin/hierarchy/levels`
serves it, so the Angular form (`features/admin/institution/`) builds
`Validators.required` from the same constant. **The columns stay nullable
forever** (`test_hierarchy_columns_stay_nullable`): nullability is a promise
about existing rows, `required` is a rule about new ones. The flip does NOT make
old batches compliant — `GET /api/admin/cohorts/incomplete` lists them,
`missing_levels` flags them, and PATCH refuses only an edit that *widens* the
gap. Ship the switch without that escape hatch and the console refuses saves on
rows nobody can fix.

`cohorts` carries all three parent pointers, and **`_resolve_ancestry` in
`app/routers/admin.py` is their only writer**: the client sends the deepest
level, the API derives the rest, and a contradicting shallower value is a 422 —
never a silent pick. `/api/admin/*` is Main Admin throughout and every
operation is proven to refuse a STUDENT (`tests/test_admin_institution.py`).

**One Main Admin (2026-09).** The placement office is ONE account, role ADMIN. `python -m app.grant_access` refuses a second ADMIN while an office account exists (the message says how a handover is done: demote the current one to MENTOR first) and refuses DIRECTOR outright — that role held every console screen by baseline, which was a second admin under another name, and it has since been removed entirely. Faculty are MENTOR, and a console screen reaches them ONLY as a grant the Main Admin makes in Governance, which is `require_admin` end to end (API and route guard) — so a granted mentor can use the screen they were given and never hand it on. The login's portal chooser is Student / Faculty / Alumni (the Faculty card's key is still `mentor`); the office comes in through the dashed "Main Admin" door. **And a faculty account is not a mentor by existing**: it has no `Mentor` row and sees nobody until the Main Admin assigns it a student on Mentors & Students — `GET /api/admin/mentor-load` lists every MENTOR-role account (`mentor_id` null until then) and `POST /api/admin/students/{id}/mentor` with `mentor_user_id` creates the group on that first assignment. No flag at creation, no second step; `grant_access --with-group` is now optional. **The Main Admin creates faculty accounts on screen**: Faculty & Students → "Add faculty member" → `POST /api/admin/faculty` (`app/routers/admin_faculty.py`, `require_admin`) mints the MENTOR row with the unusable password sentinel and no group, and shows the ACTIVATION LINK to hand over; "Activation link" on a faculty card re-mints it through the existing `POST /api/admin/users/{id}/activation-link`. The Ops task's `grant-access` still works and is the fallback for the very first account, and **it now REFUSES to create an account without `--department-id`**. It was the last door into the roster that placed nobody, and it placed nobody silently: an account filed under no department resolves to no college and no batch, so a student gets no `default_track`, so the assistant's picker stays on "General interview" -- and a general interview has no wrap-up phase and can NEVER be scored. Five interviews on a granted test account, five dashes where the score goes, nothing on any screen saying why (2026-09-15). One level and not two, because `departments.college_id` is NOT NULL: naming a department names a college, and asking for both invites them to disagree. The refusal is at the OPERATOR boundary -- `main()` passes `require_department=True` -- so `seed_roster` and the suite's fixtures, which never reach a screen, are unaffected; `tests/test_grant_access_department.py` pins that line deliberately so the next reader can see it was chosen. Required to CREATE, never to UPDATE: re-running with a different `--role` is the supported promotion path, and demanding a department to do it would make the operator retype a value already on the row -- which is how `--name` renames people who only needed a role change. **The Main Admin EDITS students and cannot mint or erase one (2026-09-10)** (`app/routers/admin_students.py`, capability `admin.students`, screen `/admin/students`): edit (name, address, USN, batch, faculty, stage, semester) and per batch `POST /api/admin/cohorts/{id}/students/bulk` (move / assign faculty / set stage / set semester, the single action repeated) plus `DELETE /api/admin/cohorts/{id}` for an EMPTY batch only. **There is no `POST /admin/students` and no `DELETE /admin/students/{id}`, and no bulk delete** — all three answer 405, never 403, because a capability refusal would mean the endpoint is still there waiting for a grant. A student account is minted by exactly one path, approving a registration, which records an application, a reason and a reviewer and then makes the person prove their mailbox; an admin-side create was a second way onto a roster that IS the access control, with none of that. The 2026-09-10 delete erased marks, attendance, uploads, interviews and mentor notes behind a two-click confirm sitting among buttons that only move somebody between batches, and it went; **what replaced it on 2026-09-16 lives in its own module** (`routers/admin_deletion.py`: remove-and-restore, and delete-for-good behind an emailed code — see "Deleting people and colleges from the console" below), never as a button among the roster edits. Emptying a deployment of people is still `python -m app.purge_people`, which dry-runs by default and is built for it. Empty a batch by MOVING its students out. The vocabulary on screen is Student / Faculty / Alumni and Main Admin; "mentor" is a stored value, not a label.

**DIRECTOR is not a role (2026-09-10).** REEP had two office roles and only ever
wanted one. DIRECTOR held every console screen *by baseline* while ADMIN held
them by being the Main Admin, so it was a second administrator under a different
name; `grant_access` had already refused to mint one for months, and it survived
only in the dev seed, a few role sets and `ROLE_BASELINE`. It is gone: the role
gates (`policies.STAFF_ROLES`, `require_mentor`, `require_admin`), the capability
baseline, the seeded `director@bgscet.ac.in` account, `require_director` (29 call
sites, all now `require_admin` — one console gate, not two), the
`/api/director/*` routes (now `/api/admin/*`, verified collision-free against the
35 that were already there), `app/routers/director.py` — gone, and now
`app/routers/console.py` — and
`apps/web/src/app/features/director/` (now `features/admin/`, 14 screens).
Migration `7c4e0b21d9aa` converts any surviving row to MENTOR and bumps its
`token_version`, so no live session keeps the claim; its `downgrade()` raises
deliberately, because nothing records which MENTOR rows used to be DIRECTOR.

**The half-finished state was worse than not starting, and that is the lesson to
keep.** The role gates were done first, and for a few hours
`governance.ROLE_BASELINE["DIRECTOR"]` still read `_ALL`. Two systems decide
access here and they are checked separately ON PURPOSE (`app/governance.py`: "a
capability can never relax the student filter"), so the result was a session
refused by every `require_*` gate — it could not open its own mentee log — and
admitted by every `require_capability` one, which is roughly fifty endpoints
including `DELETE /api/admin/students/{id}` and the full-roster exports CSV. A
role removal that reaches one system and not the other opens the other. Both
halves are pinned together, for one session, in
`tests/test_no_director_privilege.py`, and `tests/test_codebase_guards.py` keeps
the naming from coming back — routes, modules, OpenAPI tags, the client's `Role`
union, and the two post-login home maps, which had already drifted (Google
sign-in was redirecting the Main Admin to the deleted `/director`).

**The word still appears in three places, all correct.** PROGRAM DIRECTOR is a
job title printed on the college's own leave form, so `leave.py`'s
`director_name` / `director_decided_at` / `director_note` and the block
`leave_paper.py` draws stay exactly as they are. `Role.DIRECTOR` survives as an
enum VALUE, because a Postgres enum value cannot be dropped without recreating
the type and the test needs to mint one to prove it reaches nothing. And
`director123` stays on `set_password`'s refusal list: the account is gone, but
the string was published here, so it must keep being refused.

**The Main Admin is not a faculty member, either.** Four capabilities —
`mentor.mentees`, `mentor.notebook`, `mentor.verifications`, `mentor.upskilling`
— are a mentor's own instruments, and `_FACULTY_ONLY` in `app/governance.py`
keeps them OUT of the office account's baseline: it has no mentees, no private
notebook, nobody's evidence to verify and no upskilling shelf, so those screens
would render empty for it. They stay GRANTABLE, so when a student's evidence is
stuck and nobody else will look, the Main Admin grants itself the instrument in
Governance — with a reason, on the audit trail. `ROLE_BASELINE["ADMIN"]` is
therefore deliberately NOT a superset of `ROLE_BASELINE["MENTOR"]`, which is what
`tests/test_governance.py` now pins, and the `granted` fixture in
`tests/conftest.py` is how a test takes that same path.


**WHO MENTORED WHOM, AND WHY THEY WERE MOVED (B9.1/B9.2, 2026-09-13).**
`students.mentor_id` stays the current pointer and stays the only thing rule 2
filters on; `mentor_assignments` (`app/models/mentor_assignment.py`) is history
BESIDE it — one row per (student, mentor) spell, `to_at IS NULL` meaning open, so
"one open row per current pair" is a query rather than a convention — and since
`e1c4b7a209d6` it is a CONSTRAINT rather than a query:
`uq_mentor_assignment_one_open_spell` is UNIQUE on `(student_id) WHERE to_at IS
NULL`. It had to be. `record_mentor_change` reads the open row, closes it and
opens the next, which is a read-then-write, so two concurrent assignments for
one student both saw no open row and both inserted — and the console posts one
request per ticked student on a batch save. Rule 2 was never at risk (it filters
on `students.mentor_id`, one column that cannot disagree with itself); what a
duplicate cost was the history card's whole answer, because `open_assignment()`
returns `.first()` of two. **PARTIAL, and a plain `UNIQUE (student_id, to_at)`
would NOT have done it** — NULLs are distinct in Postgres, so it permits exactly
the duplicate it looks like it forbids. The migration REPAIRS before it
constrains (a unique index over violating data fails at CREATE, on production,
inside the migration task) and keeps the spell that AGREES WITH THE POINTER,
closing the losers `duplicate_open` — a value deliberately outside `END_KINDS`,
because that vocabulary is of human acts and nobody chose this one. Nothing is
deleted: this table is append-only. 04's column
list has ONE `by_user_id`/`reason`/`kind`, which cannot describe a period: a
reassignment would either overwrite who made the original assignment or leave
the closing act unrecorded, and the second is the question the screen exists to
answer. So there are two sets — `kind`/`by_user_id`/`reason` for the act that
OPENED the spell, `end_kind`/`ended_by_user_id`/`end_reason` for the one that
CLOSED it — and neither is ever rewritten. **`app/mentor_history.py` is the one
writer**, called by all five places that set the pointer
(`admin_mentoring.py`, both paths in `admin_students.py`, `app/seed.py`,
`app/grant_access.py`) plus
`admin_faculty.disable_account`, which now RELEASES a disabled faculty member's
mentees with `end_kind=faculty_disabled` and mints no handover grant — an
offboarded account cannot sign in to be asked about a note it wrote. It flushes
and never commits, so the pointer and its history land together. Setting the
same mentor again writes nothing. **The migration seeds one open row per current
pair and `from_at` is the ACCOUNT's creation time, never `now()`** — `students`
has no `created_at`, the pairing date is genuinely unknowable, and `now()` would
tell every reader the whole roster was seated on deploy day; NULL is legal and
means "since before this was recorded". A student who never had a mentor gets NO
ROW, because "never had one" and "has had one since forever" must not read the
same. Read it at `GET /api/admin/students/{id}/mentor-history`.

**`reason` is REQUIRED on `POST /api/admin/students/{id}/mentor`, release
included, and that landed with its client.** `mentor_id` is rule 2's scope key —
moving a student changes who may read their marks, attendance, USN, notes and
interview transcripts — and a release is the move nothing on any screen reports.
It is a BREAKING change to an endpoint the live Angular screen was posting
`{mentor_id}` to, so the server field, the reason input (previously
`[reepPending]="4"`) and the eight test call sites are one commit; shipping the
server half alone is an assign button that 422s on a working console. **The
release asks for its reason on the mentee's own row (2026-10-14)**: it used to
read the ASSIGN card's "Why this seating" box, which every successful
assignment empties, so a student the office had just seated could not be
released. The icon sent nothing and printed "press Release again" at the top of
the page. On the
BATCH path (`POST /admin/cohorts/{id}/students/bulk`) `reason` is OPTIONAL, and
the asymmetry is deliberate: a sentence asked once and applied to thirty people
describes the batch, not any student in it. **There is still no `student_ids` on
`BatchActionIn`** — 04 says it is already there and it is not, and the two bulk
models are separate for `RosterBulkIn`'s written reason.

**And that batch path now applies B1.5 and B1.2, which it never did.** Both
`PATCH /admin/students/{id}` with `mentor_user_id` and the batch `mentor` action
set the same column `admin_mentoring.set_student_mentor` guards, and neither called
`_assert_same_college` nor `require_capability(target=ancestry_of_user(...))` —
so the roster editor was a way around the cross-college fence on a screen nobody
thinks of as the assignment screen. That was a present defect, not a Phase-4
feature. One implementation, imported from `console.py` rather than restated.

**Capacity is a number now, and STILL NOT A RULE.** `departments.mentor_capacity`
is nullable and falls back to `settings.mentor_capacity`; `mentor-load` returns
it with `capacity_source` so a programme default is not presented as a
departmental decision. Nothing refuses an assignment past it — the endpoint and
the Angular screen both argue in writing that an admin who overloads one faculty
member in a thin year should not have to edit `.env` first, and both are still
right. What was wrong was only that the number needed a deploy. There is no
`governance_settings` table and this does not invent one.

**Mentor mapping moved OUT of `console.py` into
`app/routers/admin_mentoring.py`.** Same `/admin` prefix, same capabilities,
same four paths (`GET /mentor-load`, `GET /unassigned-students`,
`POST /students/{id}/mentor`, `GET /students/{id}/mentor-history`) — a move, not
a redesign, and `console.py` is 548 lines shorter for it. The reason is not
tidiness: `console.py` is programme-wide AGGREGATES, and this is the WRITE that
decides rule 2's scope key. Somebody asking "where is mentor assignment
decided" has to find it, because the next person to edit it is editing access
control. `ensure_mentor_group` and `_assert_same_college` moved with it and
`admin_students.py` imports them from there now. The history is composed once,
in `compose_mentor_history`, and read by both the per-student endpoint and B4.5's
Student 360 panel — `mentor_history` there is REAL now, and its `available` flag
still means "is anything recorded for THIS student", never "does the deployment
have the feature": an empty list with a faculty member on the card is a pairing
that predates the table, and a student who was never assigned is a student
waiting to be seated. The panel says which in words.

**SWOC grew ownership, a viewpoint that means something, history and a semester
(B7, 2026-09-13).** `_source_for` stamps MENTOR only when the author actually
mentors THAT student — it read `session["role"]` alone, so one granted lecturer
filed every line on a department's board as MENTOR, about students they had
never met, collapsing the two viewpoints the board exists to keep apart. A
former mentor inside the 90-day window writes as PLACEMENT: the window is a
READ. `PATCH`/`DELETE` are now the AUTHOR's or the Main Admin's (the role, not
`admin.swoc` — that key is what everybody on the screen holds, so "author or
holder" is "anybody" at more length), and an AUTHORLESS line is the office's
alone, because treating "no author" as "everybody is the author" makes exactly
the rows nobody is answerable for the easiest to rewrite. `swoc_entry_revisions`
is the scoped copy of the before/after `redesign_audit_events` has always kept —
that data is retroactive and its reader is Main-Admin-only, so a History button
pointed at it would 403 for everybody who can press it; this table starts empty
and says so. `swoc_entries.updated_at` carries **no `onupdate`** on purpose: it
would fire when the student acknowledges a line and report it as edited by
nobody. **`semester` is stamped at write time and NEVER backfilled** — there is
no semester history anywhere to backfill from, and today's semester on a line
written last year is a lie on the student's own screen; NULL renders as "semester
not recorded". `SwocItemOut` gained `id`, `author`, `author_recorded`,
`recorded_at`, `semester` and `acknowledged_at` plus
`POST /api/student/swoc/{entry_id}/acknowledge` — additively, because the board
in `docs/redesign-2026-09/design/` draws none of them and the client
concatenates a quadrant into one string; **the tile was not invented**.
`author_recorded` exists because `author: null` meant two opposite things and
the screen's one string said a person had left the roster about rows nobody ever
wrote.

**Approval provisions, and two guards make that safe.** `POST
/api/register/{id}/decision` APPROVE now mints the User + Student + profile.
Because the roster IS the access control and the form is public, provisioning
refuses an address off `settings.provisionable_email_domains` and refuses an
address that already belongs to a non-STUDENT account (a MENTOR gaining a
`studentId` is rule 2 edited by a form). That fence is on provisioning and
deliberately NOT on sign-in — the two run in opposite directions, and the
property's docstring says why.

## Backend conventions

- **Models** live in `apps/api-py/app/models/` and are the schema's source of truth; each new module is imported in `models/__init__.py` so Alembic autogenerate sees it.
- **A JSON column is `JSONB`, never `JSON`.** All 41 of them are, as of `e1c4b7a209d6` — `users.notification_prefs` and `export_events.filters` were the last two holdouts. `json` stores the raw text: no containment, no GIN index, and no equality operator at all, so `SELECT DISTINCT notification_prefs FROM users` is an *error* rather than an answer. Both were read whole into Python, so nothing was broken — which is exactly why it was cheap to change, and why the next one should never be written. Note `alembic check` does NOT compare server defaults, so a `server_default` belongs on the model as well as in the migration: `export_events.filters` had one in the database and none on the model, silently, from the day it was created.
- **An index created in a migration must ALSO be declared on the model**, and this is not tidiness. Three Phase 3 indexes (`ix_capgrant_scope`, `ix_login_events_user_at`, `ix_users_disabled_at`) lived only in their migrations, so `alembic check` asked to DROP all three on every single run — which is how a real drop goes unnoticed, in the noise. Declaring them was not free: it immediately failed `tests/test_codebase_guards.py::test_no_index_duplicates_the_prefix_of_another`, because `login_events.user_id` also carried `index=True` and `ix_login_events_user_id` was a second btree buying nothing that `(user_id, at)` did not already give. That redundancy was real in every database and **invisible to the guard, which reads the models** — an undeclared index is a hole the schema guards see straight through. Dropped by `f3a8d61c07be`; the FK stays indexed by the composite, which leads with it. Note `ix_users_disabled_at` is PARTIAL — the `postgresql_where` predicate is part of the declaration, or the two definitions differ and the drift comes back.
- **Alembic enum gotchas** (hit these repeatedly): (a) adding an enum *column* to an existing table does not auto-`CREATE TYPE` — create it first; (b) a *new table* reusing an *existing* enum must use `postgresql.ENUM(..., name='x', create_type=False)` in the migration (autogenerate emits a bare `sa.Enum` that errors "type already exists" — hand-fix it); (c) two columns sharing one enum reuse a single `Enum` instance.
- **Universal LLM adapter** (`app/ai/llm.py`) is OpenAI-compatible and auto-selects the first configured provider (Sakana → Groq → Mistral → OpenRouter → Gemini → Cohere), or an explicit `LLM_BASE_URL`+`LLM_MODEL`+`LLM_API_KEY`. One set of keys, any provider, no code change.
- **Knowledge Base = pgvector.** The docker image is `pgvector/pgvector:pg17` (stock PG17 + `CREATE EXTENSION vector`); `KnowledgeChunk.embedding` is a dimensionless `vector`. Retrieval (`app/knowledge.py`) is HYBRID — Postgres full-text blended with pgvector cosine (`embedding <=> :q`), gated by a distance floor so an off-topic query still hits the honest "no approved answer" fallback. The embedder (`app/ai/embeddings.py`) mirrors the LLM adapter: explicit `EMBEDDING_*`, else auto-select Mistral (`mistral-embed`) — and **no embedder configured ⇒ full-text only** (the KB always works). The KB is APPROVED public policy text, so embedding it is outside the student-data egress gate.

## Frontend conventions

- Standalone components + Angular **signals**; `fetch(\`${environment.apiBase}/...\`, { credentials: 'include' })` for API calls (see `apps/web/src/app/features/student/jobs/jobs.component.ts` for the house pattern).
- The "REEP v2" design system is **global CSS classes** in `apps/web/src/styles/reep-v2.scss` (`.card`, `.dt-table`, `.chip good/warn/risk/neutral`, `.dense-*`, `.ledger-*`, …) — reuse them; don't redefine globals in a component. Status is always shown as **text + colour together**, never colour alone.
- The design references are `docs/design-v2/*.html`.

### The v2 look, and the token shim under it (2026-08)

The visual language is the **Y2K-chrome / glass** handoff: a lilac-to-pink page
wash, white translucent cards on 1px lavender hairlines, **Plus Jakarta Sans**
for headings/labels (`--font-display`) and **Inter** for body and data
(`--font`), with a purple->magenta gradient reserved for primary actions and the
active nav pill. The handoff's own Orbitron/Chakra Petch pair was replaced
because Orbitron's wide techno letterforms were hurting the dense data screens —
the ledger, the analytics tables, the leaderboard — at the 13-14px those tables
actually use; the comment above the two tokens in `reep-v2.scss` is the record,
and several sizing rules downstream (`.dt-table`'s 9.5px uppercase became 10px)
were retuned for Inter's narrower caps rather than left at values chosen for
Orbitron's. It is **one committed
theme**, not a light/dark pair — every colour is painted explicitly and there is
no `prefers-color-scheme` block.

`reep-v2.scss` defines the handoff's tokens under honest names
(`--brand-purple`, `--ink`, `--surface`, `--hairline`, ...) and **every consumer
reads those**. The warm-paper aliases that stood there while the two designs
coexisted are gone — 286 references across 26 files were renamed — so a colour is
defined once and called one thing. The MUI-era `--reep-*` names survive for the
login, register and resume-builder surfaces, which sit outside the shell and
never adopted the component classes, but they are **defined in `reep-theme.scss`
with the v2 values** rather than overridden from elsewhere. One token, one place.
`reep-theme.scss`'s dark block is deleted: this is a single committed theme,
and a palette nobody can reach is a second set of colours to keep correct for no
one. **Phase 5 deleted `ThemeService` too** — with the dark block gone it was a
toggle that stamped a `data-theme` attribute no selector in the repository reads,
which the next person to find it reads as a theme feature that broke. `data-theme`
is now a static `light` written once on `<html>` in `index.html`; nothing branches
on it. `IconComponent` (`shared/icon.component.ts`) went in the same pass: the
app renders icons with the `.icon` class directly and no template ever used
`<app-icon>`. So did four of the five components in `shared/kit/` — section,
stat, empty and banner, and `kit/tone.ts` whose `TONE_INK` map only the stat
read. `kit-page-intro` stays because `features/assistant` imports it.

**Two global stylesheets, and they must not claim each other's names.**
`reep-v2-resume.scss` loads after `reep-v2.scss`, so it wins on every property it
sets and *leaks every property it does not*. Adding `display: flex` to
`.step-group`, `.completeness` or `.entry` in `reep-v2.scss` silently reflowed
the resume builder, because that file owns those names for a heading, a card and
a card. Before adding a class to `reep-v2.scss`, check it is not already defined
in `reep-v2-resume.scss`.

**`.icon` is clamped to a 1em box and hidden until the font reports itself
loaded, and both halves matter.** Material Symbols renders from LIGATURES, so
`<span class="icon">leaderboard</span>` is the literal word until the font
arrives. Without the clamp a sidebar item reserves ~90px for its icon and the
label is pushed out of the 220px nav; without the `fonts-ready` gate (set in
`main.ts` from the resolved `FontFace.status`, **not** from
`document.fonts.check()`, which answers yes for a fallback) the stray words show.
**The fonts are self-hosted**, in `apps/web/public/fonts/`, generated by
`tools/fonts/fetch-fonts.sh`. The icon face is SUBSET to the glyphs the app can
actually render (`tools/fonts/icon-names.txt`, regenerated by
`collect-icon-names.py`) — the full Material Symbols face is 5.2 MB against
172 kB for the 113 glyphs currently in it — which makes adding an icon a
two-command step rather than an optimisation: a glyph missing from the subset
renders as nothing at all.

`collect-icon-names.py` reads the templates, so it can only find an icon a
template already uses — which is a circular problem for a glyph a screen that
does not exist yet will need, and for one the scanner's denylist rejects as an
English word (`block`, `key`). `tools/fonts/icon-names.extra.txt` is the answer:
names DECLARED rather than discovered, unioned in after the denylist filter, one
per line with a comment saying which screen will want it. Put a glyph there when
you are about to build the screen that uses it; the alternative is discovering
on the day that the button is blank.

### The phone (2026-09-22)

Three Sentry issues and one request, and they turned out to be one subject: the
student reaches REEP on a handset and almost nothing here was built for one.

**THE APP DID NOT PAINT AT ALL ON AN OLD PHONE BROWSER.** `Object.hasOwn` is
ES2022 (Chromium 93) and Angular 22 calls it in the ROUTER and in core's
`__ngSimpleChanges__` reader during `bootstrapApplication` — so on a Chromium
fork below that the app dies before its first frame, with no screen left to
explain itself on and no login form to reach. `src/polyfills.ts` fills it,
along with `Array/String.prototype.at` and `String.replaceAll`, which sit in
the same 85–93 band; every entry was chosen by grepping the BUILT bundle, and
`structuredClone`, `findLast` and `toSorted` are deliberately absent because
nothing calls them. All four installs go through `define`, which is what makes
them non-enumerable — a bare `Array.prototype.at = fn` appears in every
`for...in` over an array in every dependency. **There is deliberately no
`.browserslistrc`**: one was written (Chrome 87) and removed, because the
bundle had already PARSED on that browser — syntax was never the gap — and it
cost ~4 kB of lowering plus an Angular-support warning on every build, which is
a warning nobody reads by the second week. The spec tests the exported
implementations against the RUNTIME'S OWN native method as an oracle; that
caught the first `replaceAll`, which used split/join to avoid misreading `$&`
and was wrong about the spec (replaceAll runs GetSubstitution exactly as
replace does). Deleting a native and re-importing to reach the guarded branch
is what the spec did first, and it took vitest down with it — eleven unrelated
files failing on `this.executionStack.at is not a function`, because the runner
shares the realm.

**THE SHELL PUT A FIXED 220px SIDEBAR BESIDE THE CONTENT**, which on a 360px
handset is two thirds of the screen for navigation. Below 900px it is an
off-canvas drawer behind a hamburger — the SAME `<nav>` and the same
`navigation()` groups, because a second mobile menu is a second list to keep in
step with `ADMIN_NAVIGATION` and its three siblings. Closed is
`visibility: hidden` and not only a transform, or the drawer stays in the focus
order and tabbing from the app bar walks every row of an invisible panel first
— the `hidden` file-input defect from the reachability audit, arriving again.
`height: 100dvh` sits beside the `100vh` fallback because `vh` is the viewport
with the URL bar scrolled AWAY, and the frame is `overflow: hidden`, so the
bottom of every screen was unreachable rather than scrollable.

**TABLES WERE CLIPPED, NOT OVERFLOWING**, which is why no scroll bar ever hinted
at it: `body` is `overflow-x: hidden` and `.desktop-frame` is
`overflow: hidden`, so a student at 390px saw the first three columns of their
marks and had no gesture to reach the rest. Tables inside `.desktop-main`
scroll now. Jobs, Records and the Ledger already did this properly with their
own wrappers and are EXCLUDED — `.jobs-table` is `table-layout: fixed` with six
weighted percentage columns that the block treatment would drop. **The
exclusion lists WRAPPERS and not tables**, because the two lists fail in
opposite directions: forget a wrapper and its table merely gets both
treatments, forget a table and it clips again. It is written as a specificity
override rather than `:not(.jobs-frame *, …)` — a `:not()` holding COMPLEX
selectors is Selectors 4, and a parser that cannot read it drops the whole
rule, putting the clipping back on exactly the browsers `polyfills.ts` exists
for. And `minmax(Npx, 1fr)` does not collapse below its own minimum: the track
keeps the Npx and the grid overflows, so six student grids and the global
`.dense-grid` cap it with `min(Npx, 100%)`.

**IT INSTALLS, AND THE MANIFEST IS THE STUDENT'S.** `public/manifest.webmanifest`
is "REEP Student" starting at `/student`; `tools/icons/make-app-icons.py`
redraws the four icons from `--primary-gradient`'s own stops and the app's own
Plus Jakarta Sans, which is a VARIABLE font whose default instance is 400 — the
first icons came out at Regular beside an app bar drawing 800, so the axis is
pinned with `instancer`. `scope` is `/` and CANNOT be narrowed to `/student`:
scope is what the installed window keeps, and narrowing it opens every
`/account` and `/login` URL in the browser instead, which signs the student out
of the app they just installed. The apple-touch-icon is a separate `<link>`
because iOS ignores the manifest's icons, and the one opaque icon of the four
because iOS composites transparency against black.

**`ngsw-config.json` HAS NO `dataGroups`, AND THAT ABSENCE IS RULE 1.** A
dataGroup is how ngsw caches API responses, and every interesting response here
is marks, attendance, a USN or an interview transcript. Cached, they are
written to the handset's disk by the BROWSER, outliving the httpOnly
`reep_session` cookie, surviving sign-out and surviving `_retire_other_sessions`
— and unreachable from `purge_students` and `purge_people`, which can empty a
database and cannot touch Cache Storage on a phone in Bengaluru. The file is
strict JSON and cannot hold that reasoning, so it lives beside
`provideServiceWorker` in `app.config.ts`. `navigationUrls` excludes `/api/**`
for the neighbouring reason. The asset groups are split so INSTALL costs 243 kB
and not 4 MB: prefetching `/*.js` took every admin chunk, ag-grid and echarts
onto a student's metered connection, which is the bill "Routes are lazy" was
written to avoid.

**TWO DEPLOY BUGS CAME WITH IT AND BOTH WOULD HAVE BEEN SILENT.** `ngsw.json`
and `ngsw-worker.js` are fetched at FIXED names and were inside the
`max-age=31536000,immutable` pass, so an installed student's app would have been
frozen on its install build for a year — index.html's own bug, but worse,
because a service worker survives a tab close. They are a third pass with
`no-cache` now, and in the CloudFront invalidation. And ngsw validates every
file against a SHA-1 taken at build time, while `sentry-cli sourcemaps inject`
runs AFTER `ng build`: a file changed in that window makes the worker refuse the
new version and keep serving the old one forever, with the bucket and CloudFront
both holding the new build and nothing in any log saying so.
`tools/ci/check_ngsw_integrity.py` fails the deploy there instead, and also
refuses a `.map` in the hashTable — deploy deletes maps before upload, so a
worker expecting one could never install.

**THE REGISTERED WORKER IS `reep-sw.js`, AND ngsw NEVER SEES AN `/api/`
REQUEST (2026-10-01).** ngsw answers a network failure with a 504 it makes up,
so a phone that lost its signal mid-upload showed "(504)" for a request no
server ever received. `apps/web/public/reep-sw.js` adds a `fetch` listener that
calls `stopImmediatePropagation()` for same-origin `/api/` and only THEN
`importScripts('./ngsw-worker.js')` — listeners run in the order they were
added, so the import must come second, and nobody calls `respondWith`, so the
browser performs the request itself and a real network error reaches the page.
ngsw served nothing under `/api/` anyway (no `dataGroups`, rule 1), so this
costs nothing it did. It is a fixed name like ngsw-worker.js: in the no-cache
pass and the invalidation in deploy.yml (ngsw-worker.js stays there too — it is
imported, and browsers byte-compare imported scripts for updates), matched by
no asset group so `check_ngsw_integrity.py` never hashes it, and refused by
the release gate together with `app.config.ts`, so no agent auto-ships a worker
change to every installed phone. Installed phones move over on their next app
load. `ngsw-bypass` per request was the alternative and was rejected: it must
be on every fetch in the app, and the one somebody forgets brings this back.
`tests/test_codebase_guards.py` §38 pins all four files.

**Regenerating the icon subset for `menu` found `refresh` missing**, which
`/register` had been rendering as a blank space. `collect-icon-names.py` reads
templates, so an icon is only ever as discoverable as its markup; `running` was
a state value it mistook for a glyph inside an interpolation and is denylisted
beside `draft` and `completed`.

The floating **agent orb** and the **dock** it opens live in the SHELL
(`layout/agent-orb.component.ts`, `layout/agent-dock.component.ts`), not in a
route, because they are on every screen. **The dock is BOTH assistants under
one button (2026-09-15)**: "Ask REEP" is the typed agent
(`shared/agent-chat/`, the same component `/student/agent`, `/mentor/agent` and
`/admin/agent` render) and, for a student, "Mock interview" is the interview
room (`shared/interview-room/`, the same component `/student/assistant`
renders) on the design system's dark stage, calibrated to a Tier-1
multinational's campus round (`_INTERVIEWER_PERSONA`). The pages stay as deep
links. Both tabs are `@defer`red — the room pulls `InterviewService` and the
visualizer, and the shell is in the initial bundle — and the inactive tab is
`hidden`, never destroyed, because destroying the room ends the interview.
Closing the dock over a live one asks first (`AgentDockService.requestClose`).
**THE MAIN ADMIN GETS THE TAB TOO, AS A REHEARSAL THAT STORES NOTHING
(2026-09-16).** `_is_rehearsal` in `app/routers/interview.py` admits role
ADMIN to the socket and the status probe (`rehearsal: true`), and the whole
mechanism is that `_open_records` is never called: no conversation, no
`interview_sessions` row, no turns, no report row, no recording, no consent
row — every hook the engine takes is `None`, which the engines already treat
as "do not write", and Layer 2's backstop skips a run with no row. The
cookie, the Origin check, engine readiness and BOTH halves of the concurrency
limiter still apply (a rehearsal bills an upstream session like any other).
A MENTOR is still 1008. `tests/test_interview_rehearsal.py` pins it.
The orb reads the interview's state from that service, which the room mirrors
into it, and never imports `InterviewService` itself. Drag and tap are one gesture separated by a 4px threshold; the pointer
listeners go on `document` (a pointer leaving the 58px box mid-drag stops
delivering events to it) and are removed on pointerup **and** in `ngOnDestroy`.
