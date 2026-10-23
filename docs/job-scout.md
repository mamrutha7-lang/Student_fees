# The daily job agent (Job Scout)

Every morning at **06:00 IST** the job agent searches two official job-board APIs, **Adzuna** and **Jooble**, for **fresher jobs and internships across India**. It searches once for every specialization the college offers and posts what it finds to the Jobs board. Each posting is aimed at exactly the students of that specialization. Once the two keys below are in place, it needs nobody.

| Piece | Where |
|---|---|
| What it searches, keeps and posts | `apps/api-py/app/job_scout.py` |
| The two boards | `apps/api-py/app/job_sources.py` |
| The scheduled entry point | `apps/api-py/app/job_scout_job.py` (`python -m app.job_scout_job [--dry-run]`) |
| The schedule | `reep-job-scout-daily`, `cron(30 0 * * ? *)` in `infra/cdk/reep_core/stack.py` |
| Run it now | Actions → **Ops task** → `job-scout-dry-run`, then `job-scout` |
| What it did | The line at the top of **Job postings** (`GET /api/admin/jobs/scout-status`) |

## One-time setup

1. **Get the keys (free).**
   - Adzuna: sign up at <https://developer.adzuna.com> and create an application. You get an *application id* and an *application key*; both are needed.
   - Jooble: request a key at <https://jooble.org/api/about>.
2. **Put them in AWS.** Add these keys to the `reep/external` secret in Secrets Manager, beside `GOOGLE_CLIENT_ID` and the others:
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `JOOBLE_API_KEY`
3. **Switch the boards on.** Set `"jobScoutAdzuna": true` and/or `"jobScoutJooble": true` in `infra/cdk/cdk.json`.
   - **Do this only after step 2.** A task definition that names a secret key the secret does not hold fails to start, and that includes the api itself.
   - Each flag adds its board's keys to the api task definition. The schedule is created when either flag is on.
4. **Deploy.**
   - Merge, then deploy the app with `deploy.yml`.
   - Deploy `reep-core` with `cdk-deploy.yml`, option `core-9b`: run a diff first, then deploy.
5. **Prove it.**
   - Run the Ops task `job-scout-dry-run`. It asks both boards and judges every advert, then writes nothing; read its log.
   - Then run `job-scout` once. The **Job postings** sheet now reads "Job agent last ran …".
   - From the next morning it runs by itself.

Adzuna's terms require every advert shown to carry **"Jobs by Adzuna"** with a link to Adzuna. The student, alumni and admin screens all do this. Their terms also say that commercial or academic use beyond the trial may need Adzuna's agreement, so the college should confirm this with Adzuna.

## What one run does

1. **Finds every stream.**
   - Each ACTIVE specialization is a stream, and so is each ACTIVE course with no specialization (BGSCET's Digital Marketing and Logistics MBAs).
   - A college the office sets up on screen is searched the next morning.
2. **Builds the search phrases.**
   - Each stream gets two phrases (`SEARCH_TERMS`); an unknown code falls back to the specialization's own name.
   - Each phrase is searched as a fresher job and as an internship.
   - A phrase two streams share is searched once.
   - That is about 30 calls per board per day, well inside Adzuna's free 250.
   - Calls to one board are spaced out: 2.6 s apart for Adzuna, which also refuses the 26th call in a minute, and 1 s for Jooble.
3. **Keeps only safe, fresh, fresher roles.**
   - **Too old:** an advert older than 30 days (`JOB_SCOUT_MAX_AGE_DAYS`) is skipped, so it never takes one of the day's places.
   - **Suspicious** (`is_suspicious`, strict, the owner's choice): anything asking the candidate for money (a registration, processing or security fee, a deposit, "pay Rs 999 to apply", "guaranteed placement"), "earn … per day" anywhere, and "data entry", "work from home" or "home based" in the title. In the description alone, work from home or data entry counts only beside "part time", so an ordinary hybrid job stays. "No registration fee" is not a money ask.
   - **Not a fresher role:** senior titles (senior, lead, head, director, manager — except assistant manager and relationship manager), and two or more years asked for, written any way ("2+ years experience", "minimum 3 years", "3 to 5 years", "2-4 yrs"). "Age 21 to 28 years" is an age, not experience, and "a 2 years MBA" is a degree.
   - The parser strips control characters from the text, and drops an advert whose id or link carries one, whose id is over 128 characters, whose link is not https, or that has no company. One advert of a shape nobody expected is skipped, never the whole page.
   - An internship's title starts "Internship · " only when the description describes one (a stipend, "a 3-month internship"), unless the title already says intern. Adzuna matches the word anywhere, so "internship experience preferred" on a full-time job is not an internship.
4. **Posts once per course**, aimed at that course and specialization: `source_ref = <board>:<advert id>:<course id>`.
   - An advert found for two specializations of one course is one posting with both codes.
   - An advert on both boards, or one the office already posted, is not posted again.
   - A closed posting never comes back.
   - At most 5 new postings per specialization per morning (`JOB_SCOUT_NEW_PER_SPECIALIZATION`), newest first. Kept for 30 days, that is at most about 150 agent postings on one student's board.
   - An office posting with no course only counts as the same advert inside its own college, or when it names no college.
5. **Keeps the description.** The board's summary of the advert (up to 2,000 characters) is the posting's description, which a student opens with **View JD** under the row. The full text stays on the employer's page, behind Apply, and the panel says so.
6. **Matches skills.** Any skill-catalogue name or alias in the advert becomes a `required_skills` slug. A posting that lists no skills shows **"Skills not listed"** to the student instead of a match %, office postings included. It used to say 100%.
7. **Closes its own old postings.** An agent posting more than 30 days old (`JOB_SCOUT_MAX_AGE_DAYS`) is closed, and so is one whose course is gone. Deleting a college clears a posting's college and course, and a posting with neither would otherwise reach every student of that specialization in every college. The office's own postings are never touched.
8. **Leaves a receipt.** It writes one audit row (`job_scout_run`, actor `SYSTEM`), which is what the sheet's status line reads.

**Two runs at once:** the run holds a Postgres advisory lock (`RUN_LOCK_KEY`). A second run started while one is going (the Ops task pressed at 06:00) logs "another run is in progress" and exits 0, writing nothing.

**One board breaking in a new way** (a parser bug, a library error) is recorded as "<board> failed unexpectedly (<error type>)" and that board is not asked again that morning. The other board carries on.

**An agent posting can be closed but not removed.** A removed row is forgotten, so the next morning's run would post the same advert again. The sheet greys Remove with the reason when one is ticked, and the API answers 409.

## Turning it off

- **For a morning or for good, keys kept:** set `JOB_SCOUT_ENABLED=false` on the task. Every run then says it is off and does nothing.
- **For one board:** set its flag back to `false` in `cdk.json` and deploy `reep-core`.
- **Postings already up:** close them on the Job postings sheet, or wait 30 days and they close themselves.

## What it never does

- **It never sends a student's data anywhere.** A search carries a phrase from the catalogue, a location and the key. `app/job_sources.py` imports no model, and a test reads its imports to keep it that way.
- **It never logs or reports a key.**
  - Adzuna takes its key in the query string and Jooble in the URL path.
  - httpx's own request line is redacted by a filter in `job_sources.py`.
  - Sentry's breadcrumbs and `http.client` spans are scrubbed in `app/telemetry_scrub.py`.
  - Every failure is re-raised without the URL.
  - `tests/test_job_sources.py` proves all of this against the real Sentry SDK.
- **It never scrapes.** LinkedIn and Naukri forbid scraping and block it within days, so a scraper would be a morning job that quietly stops working.

## Checking a morning

- **The sheet's status line:** the last run, how many it posted and closed, which boards are on, and which failed.
- **CloudWatch, api log group:** `Job agent run complete: …` gives the counts (found, too old, suspicious, kept, posted) and each stream's postings. A board that failed logs `Job agent: adzuna failed this morning: …`.
- **Sentry (`reep-scheduled-jobs`, monitor `reep-job-scout-daily`):** this needs `SENTRY_JOBS_DSN`.
  - One board failing is ERROR on the monitor, with exit 0.
  - Both boards failing is exit 1.
