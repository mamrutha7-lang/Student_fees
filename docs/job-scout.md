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
3. **Keeps only fresher roles.**
   - It drops senior titles (senior, lead, head, director, manager — except assistant manager), adverts asking for two or more years, links that are not https, and adverts with no company.
   - An internship's title starts "Internship · " unless it already says intern.
4. **Posts once per course**, aimed at that course and specialization: `source_ref = <board>:<advert id>:<course id>`.
   - An advert found for two specializations of one course is one posting with both codes.
   - An advert on both boards, or one the office already posted, is not posted again.
   - A closed posting never comes back.
   - At most 10 new postings per specialization per morning (`JOB_SCOUT_NEW_PER_SPECIALIZATION`).
5. **Matches skills.** Any skill-catalogue name or alias in the advert becomes a `required_skills` slug, so the skill-match % works.
6. **Closes its own old postings.** An agent posting more than 30 days old (`JOB_SCOUT_MAX_AGE_DAYS`) is closed. The office's own postings are never touched.
7. **Leaves a receipt.** It writes one audit row (`job_scout_run`, actor `SYSTEM`), which is what the sheet's status line reads.

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
- **CloudWatch, api log group:** `Job agent run complete: …` names each stream's count. A board that failed logs `Job agent: adzuna failed this morning: …`.
- **Sentry (`reep-scheduled-jobs`, monitor `reep-job-scout-daily`):** this needs `SENTRY_JOBS_DSN`.
  - One board failing is ERROR on the monitor, with exit 0.
  - Both boards failing is exit 1.
