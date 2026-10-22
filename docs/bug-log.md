# Bug log

One entry per bug or production error that was fixed, newest first, with the
date it was fixed. The entry is written in the same pull request as the fix,
and its **Status** line is updated when that fix is merged and when it is
deployed. A problem that was found but not fixed goes under **Open** with the
date it was found, and moves into an entry when it is fixed.

`AGENTS.md` keeps the longer reasoning behind each rule. This file answers the
shorter question: what broke, when, and what was done about it.

Entry format:

```
## YYYY-MM-DD — what the user saw, in a few words

- **Symptom:** who saw what, on which screen.
- **Cause:** the file and function at fault, and why.
- **Evidence:** the logs, metrics or reports that showed it.
- **Fix:** what changed.
- **Where:** pull request, commit, files.
- **Checked by:** the test that fails without the fix, and the checks run.
- **Status:** open PR / merged to dev / deployed (with dates).
- **Left open:** anything deliberately not done.
```

## Open

- **2026-10-14 — Two Main Admin screens still send a picked file by
  reference.** The catalogue upload (`features/admin/catalogue`) and the
  spreadsheet import (`features/admin/imports`) do not copy the file at pick
  time. They are used from an office desktop, where the Android failure below
  does not happen, so they were left as they are. Fix if needed: read the file
  with `readPickedFile` from `core/picked-file.ts`, as every other upload
  screen now does.
- **2026-10-14 — Large phone photos are uploaded at full size.** An iPhone sent
  0.87 MB in 121 s and CloudFront timed it out (408). Shrinking photos in the
  browser before upload would help on slow mobile data.
- **2026-10-14 — After a failed claim on Skilling, a retry can file the
  certificate under the old title.** The retry reuses the certificate that
  already uploaded, so if the student changes the badge or the issuer before
  pressing **Submit claim** again, the stored document keeps the first title.
  The claim itself is filed correctly. Found in review of
  [PR #165](https://github.com/darshani8/reep-/pull/165).
- **2026-10-14 — An unreadable answer to a successful upload would be shown
  as a dropped connection.** On the alumni profile, both signature screens and
  the resume photo, the same `catch` covers the upload and reading its JSON
  answer, so a 200 whose body is not JSON would say "the connection dropped"
  when the file had arrived. The API always answers JSON, so this has not been
  seen. Found in review of
  [PR #169](https://github.com/darshani8/reep-/pull/169) and
  [PR #170](https://github.com/darshani8/reep-/pull/170).
- **2026-10-14 — A leave-day email was not delivered.** Sentry
  [REEP-API-3](https://bgs-college-of-engineering-and.sentry.io/issues/REEP-API-3):
  the address bounced on 2026-10-07 and Amazon SES now delivers nothing to it.
  No code fix needed; the office can lift the block on `/admin/mail`.

## 2026-10-17 — Faculty saw who else could open a student, and a disabled account was not told why

- **Symptom:** reported by the owner from the Student 360 page. (1) The
  record's Access tab ("Who can open this record", with every grant's reach)
  and the rail card pointing at it were drawn for every faculty member who
  could open the record, not only for the Main Admin. (2) The office could
  disable a faculty account on the Faculty screen but had no button anywhere to
  disable a student. (3) A disabled person was not told clearly why: Google
  sign-in said "That Google account is not on the programme roster", and a
  session cut by the disable landed on the login screen with no word at all.
- **Cause:** (1) `GET /api/admin/students/{id}/access` hung on
  `admin.student_records`, which a mentor or a granted faculty member holds,
  and `GET .../360` sent the sign-ins, the record's history and the disable
  reason to the same readers. (2) `POST /api/admin/users/{id}/disable` was
  always `require_admin` and works for a student, but only the Faculty screen
  called it. (3) The Google callback reused `sso_not_enrolled` for a disabled
  account, and `get_current_session` set `X-Reep-Session` only for a retired
  session, never for a disabled one.
- **Evidence:** the owner's screenshot of the Access tab; reading the routers
  above.
- **Fix:** (1) `/access` is `require_admin`; `/360` sends an empty sign-in list
  and history, and no disable or removal reason, to anybody but the Main Admin;
  the page draws the Access tab, the rail card and the "recorded on the Access
  tab" line for the Main Admin alone. (2) The student record's More menu (Main
  Admin only) has "Disable account" (a reason is required) and "Enable
  account". A student has no 90-day enable window, because there is no "new
  account" to give one. (3) A disabled account now hears "Your account has been
  disabled for security reasons. Contact the placement office (Main Admin) to
  resolve the issue." at the password and code doors, at Google
  (`?error=sso_disabled`, added to the three-way code contract) and when its
  open session ends (`X-Reep-Session: disabled` → `/login?signedOut=disabled`).
  A removed account hears that it is no longer on the roster instead.
- **Where:** [PR #207](https://github.com/darshani8/reep-/pull/207);
  `app/routers/admin_student_record.py`, `admin_student_360.py`,
  `admin_faculty.py`, `auth.py`, `app/security.py`, `app/identity.py`;
  `features/admin/student-detail/`, `features/login/`, `core/auth.service.ts`,
  `core/auth.guard.ts`; `docs/google-sign-in.md`.
- **Checked by:** `tests/test_disabled_account.py` (new),
  `test_student_record_access.py::test_the_access_tab_half_of_the_record_is_the_offices`,
  `test_google_callback.py::test_a_barred_roster_row_gets_no_session_and_the_right_code`,
  `test_sso_contract.py`; E2E TC-403, TC-523, TC-541 (new), TC-542 (new).
- **Status:** merged to `dev` 2026-10-17, promoted to `main` 2026-10-17 ([PR #209](https://github.com/darshani8/reep-/pull/209), [PR #210](https://github.com/darshani8/reep-/pull/210)); deployed
  2026-10-17 by [Deploy run 140](https://github.com/darshani8/reep-/actions/runs/39023915955).
- **Left open:** a screen that is already open when the account is disabled
  shows the screen's own "Sign in required" errors until the next page load;
  the note appears on that load. Nothing intercepts every `fetch` in the app.

## 2026-10-16 — The daily drift check never compared AWS with its templates

- **Symptom:** issue [#117](https://github.com/darshani8/reep-/issues/117)
  reopened every morning since 2026-09-30: `detect-stack-drift` "could not
  start" on `reep-core` and `reep-voice-platform`, so a change made by hand in
  AWS would have gone unreported.
- **Cause:** CloudFormation reads every resource with the caller's
  credentials. `infra-drift.yml` ran the check as `reep-github-deploy`, which
  was refused CloudFormation and account-wide read rights on purpose (it ships
  code), and no other role existed.
- **Evidence:** every run of `infra-drift.yml` since 2026-09-30 (the comments
  on #117 and #118).
- **Fix:** a separate role, `reep-github-drift` (`GithubDriftRole` in
  `infra/cdk/reep_core/stack.py`, harden only, OIDC pinned to `main`). It may
  run the five drift calls on the two stacks and read each resource type's
  configuration with the permissions copied from the CloudFormation schema
  (`reep_core/drift_reader.py`). Object bodies, table items, secrets,
  decryption and log lines are explicitly denied. The workflow assumes it for
  the drift step only, reports "could not assume" as a blocked check until the
  role exists, and prints CloudFormation's reason when a detection fails.
- **Where:** [PR #201](https://github.com/darshani8/reep-/pull/201); `infra/cdk/reep_core/drift_reader.py`,
  `infra/cdk/reep_core/stack.py`, `.github/workflows/infra-drift.yml`.
- **Checked by:** `test_every_resource_type_the_drift_check_reads_is_classified`,
  `test_the_drift_role_reads_configuration_and_never_data`,
  `test_the_drift_role_is_harden_only_and_a_failed_create_leaves_nothing`
  (each fails when its table entry, data deny or cdk.json key is removed);
  every action checked against AWS's service reference as a non-write; the
  workflow step run against a stubbed `aws` CLI for the blocked and the
  DETECTION_FAILED paths; CDK suite 148 passed, 22 skipped.
- **Status:** merged to `dev` 2026-10-16, promoted to `main` 2026-10-17 ([PR #209](https://github.com/darshani8/reep-/pull/209), [PR #210](https://github.com/darshani8/reep-/pull/210)). Not deployed:
  needs a `reep-core` deploy (CDK deploy, a `core-*` option) before the role
  exists; #117 closes itself on the first run where
  every check completes.
- **Left open:** `reep-edge-waf` and `reep-dr-vault` are diffed but still not
  drift-checked; they live in other regions.

## 2026-10-16 — Every reep-core deploy would rebuild the NAT instance

- **Symptom:** issue [#118](https://github.com/darshani8/reep-/issues/118):
  on 2026-10-14 and 2026-10-15 the daily `cdk diff` showed `NatInstance
  replace` (ImageId `ami-0cf36b0c962e50fd7` to a newer AL2023 image) and a
  re-pointed `PrivateDefaultRoute`, with nothing merged that touched the NAT.
  The same report printed its spend table as "Amazon 0.00" on every line.
- **Cause:** `reep_core/stack.py` resolved the NAT AMI from AWS's public
  `ami-amazon-linux-latest` SSM parameter at deploy time. That parameter moves
  whenever AWS ships an image, and `ImageId` is a replacement property, so any
  `reep-core` deploy would rebuild the NAT box: egress lost during the swap,
  the user data run on an image nobody had tried, and the old instance left
  running behind `UpdateReplacePolicy: Retain`. The spend table split the CLI's
  tab-separated output on spaces, and service names contain spaces. The CDK
  jobs ran on Node 20, which jsii flags as end-of-life.
- **Evidence:** the 2026-10-14 and 2026-10-15 comments on #118.
- **Fix:** `natAmiId` in `infra/cdk/cdk.json` pins the AMI the live instance
  runs, with the parameter kept only as a fallback for a fresh account; the
  spend table splits on tabs; `infra-drift.yml`, `cdk-deploy.yml` and the CI
  `cdk` job use Node 22.
- **Where:** [PR #201](https://github.com/darshani8/reep-/pull/201); `infra/cdk/cdk.json`,
  `infra/cdk/reep_core/stack.py`, the three workflows.
- **Checked by:** `test_cdk_json_pins_the_nat_ami_the_deployment_is_actually_running`
  (fails without the cdk.json key), `test_a_malformed_nat_ami_is_refused`,
  `test_without_a_pin_the_nat_ami_still_resolves`; a synth with the real
  context changes only the NAT `ImageId` (to the value already deployed), the
  four deletion policies below and the new drift role.
- **Status:** merged to `dev` 2026-10-16, promoted to `main` 2026-10-17 ([PR #209](https://github.com/darshani8/reep-/pull/209), [PR #210](https://github.com/darshani8/reep-/pull/210)). Not deployed:
  rides the next `reep-core` deploy. The first `cdk diff` should show
  `NatInstance` unchanged; if it shows `replace`, the pin is wrong and must
  not be deployed.
- **Left open:** the tag-only difference on the API scaling target that #118
  also reported clears with the next `reep-core` deploy; nothing in the
  repository is wrong there.

## 2026-10-16 — A rolled-back NAT create blocked the retry

- **Symptom:** issue [#124](https://github.com/darshani8/reep-/issues/124):
  on 2026-10-03 `core-nat-instance` was refused at change-set validation
  because the IAM role and instance profile `reep-nat-instance` "already
  exist".
- **Cause:** every resource in `reep-core` is `DeletionPolicy: Retain`, so the
  morning's failed create kept the instance, its security group, role and
  profile, and the fixed names collided. They were deleted by hand that day
  and the swap finished (`docs/cost-review-2026-09.md`); nothing stopped it
  happening again.
- **Fix:** the four NAT resources and the new drift role are
  `RetainExceptOnCreate`: a create that rolls back is deleted, and a box that
  was running is still only ever forgotten.
- **Where:** [PR #201](https://github.com/darshani8/reep-/pull/201); `infra/cdk/reep_core/stack.py`.
- **Checked by:** `test_a_failed_nat_create_leaves_nothing_to_collide_with`;
  `test_nothing_in_the_stack_deletes_on_removal` still passes.
- **Status:** merged to `dev` 2026-10-16, promoted to `main` 2026-10-17 ([PR #209](https://github.com/darshani8/reep-/pull/209), [PR #210](https://github.com/darshani8/reep-/pull/210)). Not deployed:
  the policy change rides the next `reep-core` deploy and touches no resource.

## 2026-10-16 — Known vulnerabilities in PyJWT, pypdf and the web build

- **Symptom:** the weekly maintenance issue
  [#104](https://github.com/darshani8/reep-/issues/104) listed advisories
  that needed minor bumps: PyJWT 2.13.0 (13), pypdf 6.18.x (3 more after the
  6.18.1 patch), and `piscina` (critical, via `@angular/build`) and
  `@angular/router` (high) in the web app.
- **Cause:** pinned versions older than the fixes.
- **Fix:** `pyjwt[crypto]` 2.15.1 and `pypdf` 6.20.1; Angular 22.2.3 across
  every `@angular/*` package, which brings `piscina` 5.3.2. Angular 22.2 now
  counts the framework chunks `main` imports in the initial bundle (418.7 kB,
  where 22.1 reported 209 kB for the same JS: 349.8 kB against 348.3 kB
  loaded at startup), so the initial budget moved up by that recount, to
  460 kB warning and 610 kB error. The Angular CLI now needs Node 22.22.3 or
  later; CI's `node-version: "22"` resolves to 22.23.3.
- **Where:** [PR #201](https://github.com/darshani8/reep-/pull/201); `apps/api-py/requirements.txt`,
  `apps/web/package.json`, `apps/web/package-lock.json`,
  `apps/web/angular.json`.
- **Checked by:** `pip-audit` 17 findings to 0 on the pins and 0 on the full
  environment; `npm audit` 0; the backend suite against a migrated, seeded
  database; `tsc`, `ng test` and `ng build` on Node 22.23.3. Every
  `jwt.decode` uses a one-family algorithm list, so the mixed HMAC and
  asymmetric advisory does not apply.
- **Status:** merged to `dev` 2026-10-16, promoted to `main` 2026-10-17 ([PR #209](https://github.com/darshani8/reep-/pull/209), [PR #210](https://github.com/darshani8/reep-/pull/210)); deployed
  2026-10-17 by [Deploy run 140](https://github.com/darshani8/reep-/actions/runs/39023915955).

## 2026-10-16 — Every mentor could verify their mentees' skills, and the office could not choose who verifies

- **Symptom:** the owner asked for skill verification to be taken away from
  all mentors. Every faculty member assigned a student held "Verify skills &
  evidence" for that student without anybody granting it, so the office could
  not choose who verifies, could not take it from one mentor, and Who can do
  what did not list it.
- **Cause:** `MENTOR_FUNCTIONS` in `app/mentor_functions.py` derived
  `mentor.verifications` from having a mentee. Four other doors also let a
  mentor verify through rule 2 alone: the Documents queue and its decision
  (`routers/mentor.py::pending_uploads`, `review_upload`), the legacy
  skill-claim review (`review_skill_claim`, which marks a skill verified for
  the resume), and a manual award of a skill badge
  (`badge_verification.manual_award`). Separately, `_assert_can_verify` let a
  Main Admin with a verification grant scoped to one college decide any
  student's claim by id, while the queue showed only that college.
- **Evidence:** reading the code. `tests/test_skill_claim_flow.py` gave its
  reviewer no grant at all; mentoring was enough.
- **Fix:** `mentor.verifications` is now a grant only. `app/verifiers.py` is
  one gate that every verification door asks: the grant's reach decides who is
  listed, opened and decided. Mentoring brings none of it. The claim email goes
  to the faculty whose grant covers the student, not to the mentor. The
  `/mentor/verifications` route guard reads the capability. The student's
  Skilling and resume screens say "a faculty reviewer" and "Waiting for
  review" instead of "your mentor". The dev seed grants Test Mentor the key for
  the seeded college. Rule 2 is unchanged for everything else (notes, the
  notebook, the Mentee Log, the 360 read).
- **Where:** [PR #200](https://github.com/darshani8/reep-/pull/200);
  `app/verifiers.py`, `app/mentor_functions.py`,
  `app/routers/badge_verification.py`, `app/routers/mentor.py`,
  `app/badge_mail.py`, `app/seed.py`, `apps/web/src/app/app.routes.ts`,
  `features/student/skilling`, `features/mentor/verifications`.
- **Checked by:** `tests/test_skill_claim_flow.py` (the student's own mentor is
  refused at every door and gets no email; a granted faculty member who
  mentors nobody verifies claims and documents; a grant for one student reaches
  no other), `test_mentor_functions.py`, `test_scoped_lists.py`,
  `test_badges.py`, `test_governance.py`, `test_skill_verification.py`; four of
  the new tests fail on the old code. Also the full backend suite (2,075
  tests), the front-end unit tests, `ng build`, and the end-to-end cases
  TC-210 to 214, all of module 05 and TC-530 to 549 against a live app.
- **Status:** open PR, 2026-10-16.
- **Left open:** once deployed, nobody on production verifies until the office
  grants "Verify skills & evidence" in Who can do what; pending claims and
  documents wait until then. Nothing on the admin Home counts claims that no
  grant reaches.

## 2026-10-16 — Eight end-to-end cases in modules 05 and 06 failed on `dev`

- **Symptom:** a local run of `tests/05-faculty-alumni.spec.ts` and
  `tests/06-admin-people.spec.ts` against a seeded stack failed TC-407, TC-506,
  TC-507, TC-510, TC-513, TC-514, TC-532 and TC-539, the same way with or
  without the governance change in the same pull request.
- **Cause:** three screens had changed and their cases had not, and one test
  helper was wrong. The Verifications eyebrow became "Faculty · skill
  verification" in #140 (TC-407). The roster's Batch box lists each YEAR once
  since 2026-10-06 (`b6fdc2f`), so picking a batch by id or by its long label
  matched no option (TC-506, 507, 510, 539). TC-513 and TC-514 still read the
  Student 360 page from before its 2026-10-15 redesign (the old header line,
  the Results, Attendance and Audit tabs, an "Open" link). And `seeded()` in
  `tests/06-admin-people.spec.ts` took the seeded batch from wherever Test
  Student sat at the time: once a stopped run left them in a test's throwaway
  batch, every later `restoreSeededStudent` put them back into that batch, so
  the roster showed the seeded batch empty and TC-532 counted the wrong batch.
- **Evidence:** each failure reproduced on `dev` with this pull request's
  changes stashed; the database showed Test Student in "2026-28 E2E …".
- **Fix:** the cases and tests follow the screens as they are now (the Batch
  box picked by year, TC-513 and TC-514 rewritten against the new record
  page, the download checked as a PDF), and `seeded()` finds the seeded batch
  by its own label. TC-506 restores Test Student to that batch before it
  reads it.
- **Where:** this pull request; `tests/05-faculty-alumni.spec.ts`,
  `tests/06-admin-people.spec.ts`, `test-management/cases/05-faculty-alumni.md`,
  `test-management/cases/06-admin-people.md`.
- **Checked by:** modules 05 and 06 in full against a seeded local stack:
  70 passed; `npx playwright test --list` reports no drift.
- **Status:** merged 2026-10-16 ([#189](https://github.com/darshani8/reep-/pull/189)), released to `main` through [#191](https://github.com/darshani8/reep-/pull/191) and [#192](https://github.com/darshani8/reep-/pull/192), and deployed to production 2026-10-16 ([Deploy run #135](https://github.com/darshani8/reep-/actions/runs/38857675700)). This Status line was written in [#193](https://github.com/darshani8/reep-/pull/193), released through [#195](https://github.com/darshani8/reep-/pull/195) and [#196](https://github.com/darshani8/reep-/pull/196), and deployed 2026-10-16 in [Deploy run #137](https://github.com/darshani8/reep-/actions/runs/38861681203) (`f81146c`, documentation only).

## 2026-10-16 — Mentors could not see their own mentees' records, and access was hard to give and take away

- **Symptom:** the owner: a faculty member's mentees were listed on the
  Mentee Log with nothing behind them, and "Who can do what" was too confusing
  to use; giving access meant one screen per request, and taking one access
  away from several people meant one removal per person.
- **Cause:** the Student 360 page and the "Student records" list hang on
  `admin.student_records`, which a faculty member held only by a grant from
  the office, even for their own group. So the Mentee Log drew no "Full
  record" link (`record_openable` was false), the route guard turned the
  mentor away, and `_openable_clause` in `routers/admin_student_record.py`
  listed a mentor's mentees only beside a programme-wide grant. The access
  screen (`features/admin/governance`) granted one capability per request
  from a side panel, showed two near-identical "Scope" and "Reach" columns and
  a catalogue of internal keys, and removed a selection by posting
  `/grants/{id}/revoke` once per row.
- **Evidence:** the owner's report;
  `test_the_mentee_log_offers_the_full_record_only_where_it_opens` and TC-403
  both pinned "no Full record link without the grant".
- **Fix:** `admin.student_records` is derived from having a mentee, like the
  three `mentor.*` functions (`mentor_functions.MENTEE_RECORD_FUNCTIONS`);
  rule 2 still admits a mentor to their own group only, and a student outside
  it still needs the office's scoped grant. "Student records" is a row of the
  faculty sidebar. The access screen is redesigned as Give access, Who has
  access, Needs attention and Groups. `POST /admin/governance/grants` takes
  `capabilities: [...]` and writes every (access type, person) pair in one
  transaction; the new `POST /admin/governance/grants/revoke` removes many
  grants with one reason, all or nothing.
- **Where:** this pull request; `apps/api-py/app/mentor_functions.py`,
  `apps/api-py/app/routers/admin_student_record.py`,
  `apps/api-py/app/routers/governance.py`,
  `apps/web/src/app/features/admin/governance/`,
  `apps/web/src/app/layout/app-shell.component.ts`.
- **Checked by:** `test_student_record_access.py::test_a_mentor_opens_their_own_mentees_record_and_nobody_elses`
  and `tests/test_governance_bulk.py` (new); the governance, mentor-function,
  record and 360 test modules; `governance.component.spec.ts`; `ng test`,
  `ng build`, `tsc` and the four static web checks. End-to-end against a local
  stack: TC-403 (the mentor opens the record from the Mentee Log), TC-424,
  TC-530, TC-531 and the new TC-540.
- **Status:** merged 2026-10-16 ([#189](https://github.com/darshani8/reep-/pull/189)), released to `main` through [#191](https://github.com/darshani8/reep-/pull/191) and [#192](https://github.com/darshani8/reep-/pull/192), and deployed to production 2026-10-16 ([Deploy run #135](https://github.com/darshani8/reep-/actions/runs/38857675700)). This Status line was written in [#193](https://github.com/darshani8/reep-/pull/193), released through [#195](https://github.com/darshani8/reep-/pull/195) and [#196](https://github.com/darshani8/reep-/pull/196), and deployed 2026-10-16 in [Deploy run #137](https://github.com/darshani8/reep-/actions/runs/38861681203) (`f81146c`, documentation only).
- **Left open:** approving and extending still go one grant per request,
  because each can be refused on its own.

## 2026-10-16 — A USN typed in lower case was not matched against the roster

- **Symptom:** found while fixing the entry below, then fixed at the owner's
  request. An applicant who typed `1mp25ba033` was not flagged against a
  student holding `1MP25BA033`, and approving them put a second student on the
  roster with the same number in lower case.
- **Cause:** the roster stores USNs upper case (`seed_roster`, the Students
  editor), but `RegisterIn` in `app/routers/registration.py` stored the USN as
  typed. Every "who holds this USN" lookup was case-sensitive: the approval
  guard in `_provision_student`, the checklist in `_checks_for`, the Students
  editor's check in `admin_students.py`, `grant_access` and `seed_roster`. So
  was the unique index on `students.usn`.
- **Evidence:** reproduced in tests. Not seen in production.
- **Fix:** `app/usn.py` holds two helpers. `normalise_usn` strips and
  upper-cases a USN. `usn_is` compares upper case on both sides. The form now
  stores the USN upper case, approval seats the upper-case USN, and every
  lookup above, plus the new correction endpoint, compares upper case. That
  last part also catches a lowercase USN
  written before this fix, both on a pending application and on a student
  record. One side effect: a rule's USN pattern now sees the upper-case USN,
  so an applicant who typed lower case can now match an upper-case pattern
  (and be auto-approved by it), where before they went to manual review.
  Rule patterns are also compiled case-insensitively now
  (`validate_usn_pattern`), because a rule written in lower case (`^1mp25`)
  would otherwise match nobody once every USN is upper case. The PR's
  automated review found that.
- **Where:** `app/usn.py`, `app/routers/registration.py`,
  `app/routers/admin_students.py`, `app/grant_access.py`, `app/seed_roster.py`.
- **Checked by:** six new tests in
  `tests/test_registration_usn_correction.py` (the five that touch the
  database fail on the old code) and
  `tests/test_registration_rules.py::test_usn_pattern_ignores_case`. Every test module that touches
  registration, the Students editor, `grant_access` or `seed_roster` passes
  (42 modules, 567 tests).
- **Status:** [PR #186](https://github.com/darshani8/reep-/pull/186) merged into `dev`
  2026-10-16, released to `main` in
  [PR #188](https://github.com/darshani8/reep-/pull/188) and deployed 2026-10-16
  ([Deploy run 133](https://github.com/darshani8/reep-/actions/runs/38851884711),
  `60d29aa`). This Status line was written in
  [PR #190](https://github.com/darshani8/reep-/pull/190), released through
  [#195](https://github.com/darshani8/reep-/pull/195) and
  [#196](https://github.com/darshani8/reep-/pull/196), and deployed 2026-10-16
  in [Deploy run 137](https://github.com/darshani8/reep-/actions/runs/38861681203)
  (`f81146c`, documentation only).
- **Left open:** no migration. Lowercase USNs already on the roster stay as
  they are until somebody edits them on Students; lookups find them either
  way. A unique index on `upper(usn)` would make the database refuse a case
  twin too. It needs production checked for existing twins first, because
  such an index fails to build if one exists.

## 2026-10-16 — An application whose USN another student held could only be rejected

- **Symptom:** on New applications, pressing Approve on Prajwal Gowda Ic's
  application (USN 1MP25BA033) showed "USN 1MP25BA033 already belongs to
  another student. Correct the USN on this application, or reject it as a
  duplicate". The panel said "Fix the application or reject it". Nothing on
  the screen could correct a USN, and nothing said who held the number.
- **Cause:** `_provision_student` in `app/routers/registration.py` refuses a
  USN another student holds (GUARD 3, correct: `students.usn` is unique). But
  the API had no way to edit an application, so the refusal's advice pointed
  at a control that did not exist. The checklist line (`_checks_for_one`)
  named nobody, so the reviewer could not tell a typo from the same person
  applying under a second address, or from another record holding the wrong
  number.
- **Evidence:** the screenshot from the office. Reproduced locally with a
  student holding 1MP25BA033 and a pending application carrying it.
- **Fix:** `PATCH /api/register/{id}` corrects the USN on an application that
  is pending or on hold. It is upper-cased, audited (`CORRECTED`, before and
  after) and refused (409) if another student holds the new number, and it
  answers the row with its checklist recomputed. For a programme-wide reviewer
  the clash line now names the holder ("… already belongs to Prajwal G
  (1mp25ba033@bgscet.ac.in)") and carries `student_id`. The panel draws
  "Open their record" for a session that holds `admin.student_records`. A
  scoped reviewer is still told only that the USN is taken. The panel's USN
  row has a "Correct USN" button. The 409 from Approve still names nobody,
  because `_apply_rule` copies it into the applicant's public
  `decision_reason`.
- **Where:** `app/routers/registration.py`,
  `features/admin/registrations/` (component, template, styles, spec).
- **Checked by:** `tests/test_registration_usn_correction.py` (12 tests,
  which fail without the fix), the whole `tests/test_registration_*.py`
  suite, `test_codebase_guards.py` and `test_phase3_compatibility.py` (203
  passed), the registrations spec (4 tests) and the full `ng test` (269
  passed), `ng build`, and the four web static checks. Also walked in a
  browser against a local stack: the clash line named the holder, a taken
  USN was refused under the box, and a free one turned the line green.
- **Status:** [PR #186](https://github.com/darshani8/reep-/pull/186) merged into `dev`
  2026-10-16, released to `main` in
  [PR #188](https://github.com/darshani8/reep-/pull/188) and deployed 2026-10-16
  ([Deploy run 133](https://github.com/darshani8/reep-/actions/runs/38851884711),
  `60d29aa`). This Status line was written in
  [PR #190](https://github.com/darshani8/reep-/pull/190), released through
  [#195](https://github.com/darshani8/reep-/pull/195) and
  [#196](https://github.com/darshani8/reep-/pull/196), and deployed 2026-10-16
  in [Deploy run 137](https://github.com/darshani8/reep-/actions/runs/38861681203)
  (`f81146c`, documentation only).
- **Left open:** what to do when the holder IS the applicant under another
  address is still the reviewer's call: reject as a duplicate, or change that
  record's address on Students to the applicant's and approve, which then
  reuses the record. There is no one-step "approve onto that record". It would
  attach an existing student's marks to a new address on a reviewer's click,
  and that needs the owner's decision.

## 2026-10-15 — The redesigned student page lost two panels, and "Full record" could lead to a refusal

- **Symptom:** found while checking the Student 360 release for the owner. The
  new `/admin/students/:id` no longer showed a student's mentor history (every
  faculty mentor they have had, with who moved them and why) or their
  promotions and graduations (who made each move and the reason). The old page
  showed both, and it was the only screen that showed the mentor history. And
  on the Mentee Log, a faculty member holding "View student records" for
  another department saw a "Full record" link beside their mentee that opened
  onto "Your 'View student records' capability does not reach this record".
- **Cause:** the approved prototype had no place for the two panels, so the
  rebuild in #177 dropped them, though `GET /admin/students/{id}/360` still
  sends both (`mentor_history`, `semester_history`). The Mentee Log drew the
  link whenever the session held `admin.student_records` anywhere
  (`canOpenRecord` in `mentee-log.component.ts`), while the record page asks
  whether the grant reaches that student. Before today every grant was
  programme-wide, so the two answers agreed.
- **Evidence:** the old and new templates compared side by side; the link
  reproduced on a local copy with a Civil-only grant, `GET /360` answering 403.
- **Fix:** the Overview's Faculty mentor card lists the mentor history again,
  and the Academics tab has a Promotions and graduations table, both from the
  data the page already loads (`mentorSpellRowsOf` and `moveLabelOf` in
  `student-record.ts`). `GET /api/mentor/mentees` now carries
  `record_openable` for each mentee, answered by the record page's own rule
  (`_openable_clause` in `routers/admin_student_record.py`), and the Mentee
  Log draws "Full record" only when it is true.
- **Where:** this pull request; `apps/api-py/app/routers/mentor.py`,
  `apps/web/src/app/features/admin/student-detail/`,
  `features/mentor/mentee-log/`.
- **Checked by:**
  `test_student_record_access.py::test_the_mentee_log_offers_the_full_record_only_where_it_opens`
  fails without the server change; new cases in `student-record.spec.ts`;
  the record, admin, governance and guard test modules, `ng test`, `ng build`,
  `tsc` and the four static web checks. Driven in a browser on a local copy:
  the two panels with a recorded mentor change and promotion, and the link
  hidden with a Civil-only grant and shown, and opening, with a grant on the
  student's department.
- **Status:** [PR #182](https://github.com/darshani8/reep-/pull/182) merged into `dev`
  2026-10-15, released to `main` in
  [PR #184](https://github.com/darshani8/reep-/pull/184) and deployed 2026-10-16
  (Deploy run 132, `3c48939`).

## 2026-10-15 — The office's student page had no resume, skills, leaderboard or KPIs

- **Symptom:** the owner, on `/admin/students/:id`: the student's resume could
  not be seen or downloaded, skills and the leaderboard were missing, there
  were no headline numbers, and nothing on the page let the office choose who
  else could open a student's record.
- **Cause:** the CV from the application is stored as an upload (kind
  `RESUME`, "CV from application", `_move_documents_to_uploads` in
  `routers/registration.py`) but the page listed it only as a Documents row
  with no preview. There was no staff read of a student's leaderboard rank
  (`GET /student/leaderboards` is the caller's own) or built resumes
  (`_own_resume`). The badge read was loaded but drawn as one "11/48" bar.
  The time sheet and English baseline came from reads gated on
  `mentor.mentees`, which the Main Admin does not hold, so they said "not
  readable". And `admin.student_records` granted to a faculty member reached
  only their own mentees, because rule 2 ran underneath it.
- **Evidence:** the owner's report; `GET /admin/students/{id}/360` carried
  none of these panels; the old screen showed the refusals in words.
- **Fix:** the page is rebuilt from the approved prototype
  (`docs/prototypes/student-360/index.html`): candidate column, six KPIs,
  Needs attention, and Overview, Resume, Skills, Leaderboard, Academics,
  Interviews, Time sheet, Documents and Access tabs. New reads on the page's
  own key in `routers/admin_student_record.py`: `/record` (built resumes,
  skills and growth, standing on every board, the ledger fortnight, the
  English baseline, interviews, six weeks of attendance), `/access`, `/files`
  and `/resumes/{id}/pdf` (a download writes an audit row), and
  `/admin/student-records`, the list a faculty member uses to find the
  students they were given. The CV is drawn with pdf.js (4.10.38), never
  inline. On the owner's decision, a SCOPED `admin.student_records` grant now
  opens the students it names, read only, through a new keyword on rule 2's
  gate (`allow_records_grant`, GET reads of this page only); a programme-wide
  grant still reaches only the holder's mentees. The review of the promotion
  found that the Leaderboard tab still named the student's top-ranked
  classmates, with their CGPA on the VTU board, to a faculty member granted
  that one student; the rows are now cut to the students the viewer may open
  (`_openable_clause`, shared with the Student records list).
- **Where:** [PR #177](https://github.com/darshani8/reep-/pull/177) and
  [PR #179](https://github.com/darshani8/reep-/pull/179);
  `apps/api-py/app/policies.py`, `app/routers/admin_student_record.py`,
  `app/routers/admin_student_360.py`,
  `apps/web/src/app/features/admin/student-detail/`,
  `features/admin/student-records/`, `shared/pdf-preview/`.
- **Checked by:** `tests/test_student_record_access.py` (the
  widening, programme-wide grants unchanged, revoked and lapsed grants, no
  mentor endpoint opened, downloads recorded, another student's file refused,
  the list matches the page), and in #179 a test that fails without the
  narrowing; `test_codebase_guards.py`'s delegate test now
  pins the new keyword's default; `student-record.spec.ts`; the full API
  suite, `ng test`, `ng build` and the four static web checks. Driven in a
  browser as the Main Admin and as a faculty member given a department grant,
  including giving and removing access.
- **Status:** #177 and #179 merged into `dev` and released to `main` in
  #180, 2026-10-15; deployed the same day (Deploy run 131, `1e09480`).
  Two non-blocking findings from the review of #180 are fixed in
  [PR #181](https://github.com/darshani8/reep-/pull/181), merged into `dev`
  2026-10-15 and deployed 2026-10-16 with #182 (Deploy run 132,
  `3c48939`): the Student records list offered a faculty
  member's own mentee that the page then refused, and `/access` showed the
  office's grant reasons to faculty viewers. Neither could expose anything
  before the office makes a scoped grant.
- **Left open:** interview reports and transcripts are not opened from this
  page (the Interviews tab lists every sitting with its score); a granted
  faculty member reads them on Interview records only for their own mentees.

## 2026-10-14 — Staff, alumni and resume-photo uploads: the same file-read failure

- **Symptom:** nobody had reported it yet; it was found while fixing the
  student screens below. Six more upload screens sent the picked file by
  reference: the alumni profile resume, faculty **Upskilling**, leave
  attachments, the signature on `/mentor/signature` and on the account page,
  and the resume builder's photo. A file the phone could not read at send time
  failed with "Could not reach the server.", and the two signature screens
  printed the browser's own "Failed to fetch".
- **Cause:** the same as the Skilling entry below. The alumni profile had the
  longest window: the resume is picked first and sent only when **Save** is
  pressed, after the company, designation and dates are filled in.
- **Evidence:** the same mechanism as Skilling, found by reading the code. None
  of these screens reported anything to Sentry before this fix, so there are
  no events to count.
- **Fix:** every one of them now uploads the in-memory copy from
  `core/picked-file.ts`, refuses an unreadable file on the spot, and says the
  connection dropped when no answer comes back (the new shared
  `UPLOAD_DROPPED_MESSAGE`). Both failures go to Sentry as fixed sentences.
  The alumni profile shows "Opening the file…" and holds **Save** until the
  copy is made. A refusal with no message from the server now shows its
  status code instead of blaming the file type (Upskilling, alumni profile,
  resume photo).
- **Where:** [PR #168](https://github.com/darshani8/reep-/pull/168);
  `features/alumni/profile`, `features/mentor/{upskilling,leave,signature}`,
  `features/account`, `features/student/resume/sections/basic.component.ts`,
  `core/picked-file.ts`.
- **Checked by:** six new spec files, three tests each. The main test of each
  screen fails when that screen sends the picker's reference (checked by
  putting the old line back). Type check, 251 unit tests, the four static
  checks and the production build pass.
- **Status:** merged to `dev` on 2026-10-14
  ([PR #168](https://github.com/darshani8/reep-/pull/168)), promoted through
  `stage` ([PR #169](https://github.com/darshani8/reep-/pull/169)) to `main`
  ([PR #170](https://github.com/darshani8/reep-/pull/170)), and deployed to
  production on 2026-10-14 at 15:25 UTC (front end only,
  [deploy run #129](https://github.com/darshani8/reep-/actions/runs/38588969905)).
- **Left open:** the two Main Admin screens, photo downscaling and the
  unreadable-answer case found in review (see **Open**).

## 2026-10-14 — Registration and Documents: the same file-read failure as Skilling

- **Symptom:** on `/register`, applicants on Android pressed Submit and were
  told their connection dropped. On `/student/uploads`, a failed upload said
  "Could not reach the server."
- **Cause:** both screens kept the picked file as a reference, like Skilling.
  On `/register` the CV is the first box and is sent only after about a
  dozen more are filled in, so there is always time for an Android phone to
  lose read access to a file held by Drive, Photos or another app.
  `/student/uploads` sends at pick time, so its window is short, but it
  showed the same misleading sentence.
- **Evidence:** Sentry
  [REEP-WEB-4](https://bgs-college-of-engineering-and.sentry.io/issues/REEP-WEB-4),
  "Registration submit failed: no HTTP response": 9 events from 1 to 7 Oct,
  all from Android (6 Chrome Mobile, 3 Samsung Internet), none from iPhone or
  desktop.
- **Fix:** both screens now copy the file into memory when it is picked
  (`core/picked-file.ts`). An unreadable file is refused immediately with a
  message about the file and reported to Sentry without its name. `/register`
  shows "Opening the file…" and holds Submit until the copy is made. On
  `/student/uploads`, a dropped connection now says so; a refusal with no
  message from the server shows its status code instead of blaming the
  file; and an unreadable file is refused before a Replace deletes anything.
- **Where:** [PR #165](https://github.com/darshani8/reep-/pull/165);
  `features/register/registration.component.{ts,html}`,
  `features/student/uploads/uploads.component.ts`.
- **Checked by:** new `features/register/registration.component.spec.ts` (3
  tests; the key one fails when the form keeps the picker's reference);
  type check, 233 unit tests, the four static checks and the production
  build pass.
- **Status:** merged to `dev` on 2026-10-14
  ([PR #165](https://github.com/darshani8/reep-/pull/165)), promoted through
  `stage` ([PR #166](https://github.com/darshani8/reep-/pull/166)) to `main`
  ([PR #167](https://github.com/darshani8/reep-/pull/167)), and deployed to
  production on 2026-10-14 at 12:45 UTC (front end only,
  [deploy run #128](https://github.com/darshani8/reep-/actions/runs/38566139358)).
- **Left open:** the four staff and alumni upload screens (see **Open**).

## 2026-10-14 — Skilling: certificate uploads failed with "Could not reach the server."

- **Symptom:** students pressing **Submit claim** on `/student/skilling` were
  told "Could not reach the server." and could not claim their skills.
- **Cause:** `skilling.component.ts` kept the picked file as a reference and
  the browser read it again only when the upload was sent, after the student
  had filled in four more fields. On Android, a file held by Google Drive,
  Photos or another app can be unreadable by then (Chrome
  `ERR_UPLOAD_FILE_CHANGED`). The request then failed inside the phone, and
  the screen's catch-all blamed the server.
- **Evidence:** the API logged 138 successful certificate uploads that day and
  one failure, so the failing requests never reached it. The CloudFront log
  showed failing Android uploads sending only headers (136 and 142 bytes),
  then hanging up without the file. The firewall (WAF) blocked nothing on
  that route.
- **Fix:** the new `apps/web/src/app/core/picked-file.ts` copies the file into
  memory the moment it is picked. A file the phone cannot read even then is
  refused immediately with a message telling the student to download it to
  the device first. A dropped connection now says so, keeps the form filled
  in, and a retry does not upload the certificate twice. Both failures are
  reported to Sentry (`reep-web`) as fixed sentences, with nothing from the
  form.
- **Where:** [PR #165](https://github.com/darshani8/reep-/pull/165), commit
  `9c42f89`; `core/picked-file.ts`,
  `features/student/skilling/skilling.component.{ts,html}`.
- **Checked by:** `apps/web/src/app/core/picked-file.spec.ts` (3 of its 4 tests
  fail with the old behaviour); type check, 230 unit tests and the production
  build pass; all five required CI checks green.
- **Status:** merged to `dev` on 2026-10-14
  ([PR #165](https://github.com/darshani8/reep-/pull/165)), promoted through
  `stage` ([PR #166](https://github.com/darshani8/reep-/pull/166)) to `main`
  ([PR #167](https://github.com/darshani8/reep-/pull/167)), and deployed to
  production on 2026-10-14 at 12:45 UTC (front end only,
  [deploy run #128](https://github.com/darshani8/reep-/actions/runs/38566139358)).
- **Left open:** photo downscaling (see **Open**). The registration and
  Documents screens were fixed the same day (entry above).
