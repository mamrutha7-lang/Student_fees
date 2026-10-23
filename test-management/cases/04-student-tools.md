# Student: profile, jobs and tools

| Field | Value |
|---|---|
| Screens | `/student/profile`, `/student/uploads`, `/student/resume`, `/student/jobs`, `/student/english`, `/student/mentor-log`, `/student/interviews`, `/student/assistant`, `/student/agent`, and the floating assistant orb and dock on every student screen |
| Automated tests | [`tests/04-student-tools.spec.ts`](../../tests/04-student-tools.spec.ts) |
| ID range | TC-300 to TC-399 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

Unless a case says otherwise, every case runs as the dev seed's student,
`student@bgscet.ac.in` (password `student123`, name Test Student, USN
`1BG24MBA001`). The automated tests sign that account in through the API
before step 1, so their step 1 starts on a signed-in browser. By hand, sign in
on `/login` first, with the Student portal selected.

---

## TC-300 — Profile shows the locked identity and institutional assignment

| Field | Value |
|---|---|
| ID | TC-300 |
| Module | Student: profile |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-300` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account. REEP keeps one live session per account.
3. The student is still seated in the seeded batch (`MBA-2026-B`) under the
   seeded college, department, course and specialization. The card is read
   through that batch, so moving the student on the admin roster changes what
   this case sees.
4. The student is still cleared for placements. Only the placement office can
   change that.

### Test data

| Field | Value on the seeded record |
|---|---|
| Full name | Test Student |
| USN | `1BG24MBA001` |
| College | BGS College of Engineering and Technology |
| Department | Department of Management Studies |
| Course | Master of Business Administration |
| Specialization | Finance |
| Batch | 2024-26 |
| Entry date | 2024-08-01 |
| Expected completion | 2026-07-31 |

### Steps

1. Open `/student/profile`.
2. Click in the USN field and type the letter X.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Profile" page opens. The "Identity" card, marked "Synced · read-only", shows Full name "Test Student" and USN "1BG24MBA001". |
| ER-2 | 1 | The "Institutional assignment" card, marked "Verified by Main Admin · read-only", shows each value in the test data under its label: College, Department, Course, Specialization, Batch, Entry date and Expected completion. Under them: "Locked — not editable by students. Your college, department and batch are set by the Main Admin; ask the office if any of it is wrong." |
| ER-3 | 1 | The "Placement preferences" card carries the chip "Cleared for placements". |
| ER-4 | 2 | The USN still reads "1BG24MBA001": typing changes nothing. Every field on the Identity and Institutional assignment cards is read-only. |

---

## TC-301 — Saving edited contact details on the profile

| Field | Value |
|---|---|
| ID | TC-301 |
| Module | Student: profile |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-301` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The profile holds the seeded values: Phone, Contact email, LinkedIn,
   GitHub and Portfolio empty; City "Bengaluru"; career summary "MBA finance
   candidate seeking placement."; "Interested in jobs" and "Interested in
   internships" ticked; "Hide me from the leaderboards" unticked. The
   percentages in ER-1 and ER-2 are counted from these. The automated test
   puts these values in place through the API before step 1, and afterwards
   restores whatever the profile held before it ran.

### Test data

| Field | Value |
|---|---|
| Phone | `98765 43210` |
| City | A new value, e.g. `Mysuru`. The automated run uses `E2E City <run id>`. |

### Steps

1. Open `/student/profile`.
2. Type 98765 43210 in the Phone field.
3. Replace the value in the City field with the test city.
4. Click Save changes.
5. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Placement profile completion" card reads "57% complete" and "4 of 7 placement fields filled. Add phone, contact email, LinkedIn to strengthen your placement profile." The status beside the button reads "Up to date", and Save changes is disabled. |
| ER-2 | 2 | The status reads "Unsaved changes", Save changes becomes enabled, and the completion card reads "71% complete". |
| ER-3 | 4 | The status reads "Saved just now", followed by the time of the save, and Save changes is disabled again. |
| ER-4 | 5 | The saved values came back from the server: Phone shows "98765 43210", City shows the test city, the status reads "Up to date" and the completion card still reads "71% complete". |

### Post-conditions

The profile keeps the new phone number and city. Put the seeded values back
by hand (Phone empty, City "Bengaluru", then Save changes). The automated test
restores the profile it found through the API.

---

## TC-302 — Invalid phone and LinkedIn entries are refused before saving

| Field | Value |
|---|---|
| ID | TC-302 |
| Module | Student: profile |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-302` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The profile's Phone and LinkedIn fields are empty, as seeded. The automated
   test empties them through the API before step 1 and restores the profile
   afterwards.

### Test data

| Field | Value |
|---|---|
| A valid phone | `98765 43210` |
| An invalid phone | `98765-ABC` (a hyphen and letters) |
| A link that is not LinkedIn | `example.com/in/test-student` |

### Steps

1. Open `/student/profile`.
2. Type 98765 43210 in the Phone field.
3. Replace the Phone value with 98765-ABC and press Tab.
4. Empty the Phone field, type example.com/in/test-student in the LinkedIn field and press Tab.
5. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The status reads "Unsaved changes" and Save changes is enabled. |
| ER-2 | 3 | Under Phone, in red: "Use only digits, spaces or a leading +." At the top of the page: "Some fields need fixing before you can save. Corrections are marked in red below." Save changes is disabled. |
| ER-3 | 4 | The Phone error is gone. Under LinkedIn, in red: "Enter a linkedin.com link." Save changes stays disabled. |
| ER-4 | 5 | Nothing was saved: Phone and LinkedIn are empty again and the status reads "Up to date". |

---

## TC-303 — Uploads lists the student's documents and their review status

| Field | Value |
|---|---|
| ID | TC-303 |
| Module | Student: uploads |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-303` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The three seeded uploads are still on the record and have not been
   reviewed since the seed: a profile photo and a certificate awaiting review,
   and a CV already verified.

### Test data

| Title | File | Type | Status |
|---|---|---|---|
| Leadership certificate | `leadership_completion.pdf` | Certificate proof | Pending review |
| Profile photo | `me.png` | Profile photo | Pending review |
| Existing CV | `resume_v1.pdf` | Resume / CV | Verified, reviewer note "Looks good." |

### Steps

1. Open `/student/uploads`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Uploads" page opens with the three steps "Choose document type", "Upload file" and "In review", and the note "Accepted: PDF, PNG, JPEG · up to 10 MB". |
| ER-2 | 1 | Under "Your documents", each seeded upload is a card showing its title, file name, type and status as in the test data. |
| ER-3 | 1 | The "Existing CV" card shows the reviewer's comment "Reviewer: Looks good." |
| ER-4 | 1 | The "Placement documents" checklist marks Profile photo, Resume / CV and Certificate proof as "Added" and carries the chip "All in". |

---

## TC-304 — Uploading a document puts it in review with a preview

| Field | Value |
|---|---|
| ID | TC-304 |
| Module | Student: uploads |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-304` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has fewer than 40 uploads and under 200 MB stored, the
   per-student allowance.

### Test data

| Field | Value |
|---|---|
| Document type | Other document |
| File | `tests/fixtures/sample.png` (a 240 × 240 PNG, 551 bytes), renamed to a name used by no other upload. The automated run names it `e2e-<run id>.png`. |

### Steps

1. Open `/student/uploads`.
2. Under Document type, select Other document.
3. Click the dropzone that reads "Drag & drop a file here, or click to browse" and choose the test file.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The dropzone reads "Uploading as Document", the name the list gives this type. |
| ER-2 | 3 | A success panel reads "Uploaded — now in review" and "<file name> · 551 B. A mentor will verify it shortly." It shows a preview of the uploaded image, opened from the server. The "In review" step is now the active one. |
| ER-3 | 3 | A new card with the file's name is at the top of "Your documents", with the status "Pending review" and the line "Document · 551 B · <today's date>". |

### Post-conditions

The upload stays on the record, awaiting review. Remove it with its Remove
button (TC-305). The automated test deletes it through the API.

---

## TC-305 — Removing an uploaded document

| Field | Value |
|---|---|
| ID | TC-305 |
| Module | Student: uploads |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-305` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has an upload that may be deleted, for example the one TC-304
   leaves behind. The automated test uploads `tests/fixtures/sample.pdf`
   through the API first, titled `e2e-remove-<run id>.pdf`.

### Test data

| Field | Value |
|---|---|
| Upload to remove | The disposable upload from pre-condition 3 |

### Steps

1. Open `/student/uploads`.
2. Click Remove on the test upload's card, and click Cancel in the confirmation.
3. Click Remove on the same card again, and click OK in the confirmation.
4. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser asks: "Remove "<title>"? This permanently deletes the file from your record." After Cancel, the card is still listed. |
| ER-2 | 3 | The card disappears from "Your documents". |
| ER-3 | 4 | The card is still gone: the upload and its file were deleted on the server. The seeded uploads are untouched. |

---

## TC-306 — Replacing an uploaded document

| Field | Value |
|---|---|
| ID | TC-306 |
| Module | Student: uploads |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-306` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has a disposable upload of type "Document". The automated test
   uploads `tests/fixtures/sample.pdf` through the API first, titled
   `e2e-old-<run id>.pdf`.

### Test data

| Field | Value |
|---|---|
| Upload to replace | The disposable upload from pre-condition 3 |
| New file | `tests/fixtures/sample.jpg` (2 884 bytes), renamed to a name used by no other upload. The automated run names it `e2e-new-<run id>.jpg`. |

### Steps

1. Open `/student/uploads`.
2. Click Replace on the test upload's card and choose the new file.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The success panel reads "Uploaded — now in review" and names the new file. |
| ER-2 | 2 | "Your documents" lists a card for the new file, "Pending review", with the replaced card's type ("Document · 2.8 KB · <today's date>"). The old card is gone, so the record holds one current document instead of two. |

### Post-conditions

The new upload stays on the record. The automated test deletes it through the
API.

---

## TC-307 — A file that is not a PDF, PNG or JPEG is refused

| Field | Value |
|---|---|
| ID | TC-307 |
| Module | Student: uploads |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-307` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.

### Test data

| Field | Value |
|---|---|
| File | A plain-text file renamed to end in `.pdf`, e.g. `not-really-a.pdf` holding the words "just some text". The server reads a file's first bytes, not its name. The automated run names it `e2e-fake-<run id>.pdf`. |

The picker offers PDF, PNG and JPEG files only. To choose the test file by
hand, switch the dialog's file-type filter to "All files".

### Steps

1. Open `/student/uploads`.
2. Click the dropzone and choose the test file.
3. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | An error panel reads "Upload failed" and "Unsupported file type — only PDF, PNG and JPEG are accepted." |
| ER-2 | 3 | No card with the test file's name is listed under "Your documents". |

---

## TC-308 — A file over 10 MB is refused

| Field | Value |
|---|---|
| ID | TC-308 |
| Module | Student: uploads |
| Priority | P2 |
| Type | Functional, negative, boundary |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-308` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.

### Test data

| Field | Value |
|---|---|
| File | Any PDF of more than 10 MB (10 485 760 bytes). The automated run uses a generated file of exactly 10 485 761 bytes, one byte over, named `e2e-big-<run id>.pdf`. |

### Steps

1. Open `/student/uploads`.
2. Click the dropzone and choose the test file.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | An error panel reads "Upload failed" and "That file is over 10 MB. Please upload a smaller PDF, PNG or JPEG." The page refuses the file itself, before sending it, and no card with its name appears under "Your documents". |

---

## TC-309 — Filling a resume builder section and saving it

| Field | Value |
|---|---|
| ID | TC-309 |
| Module | Student: resume builder |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-309` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The resume builder holds nothing yet, as on a fresh seed, so "Profile
   complete" reads 0%. Each of the 12 content sections counts for 1/12 of the
   percentage. The automated test empties the builder through the API before
   step 1 and restores what it held afterwards.

### Test data

| Field | Value |
|---|---|
| Career objective | Any sentence, e.g. `Analyst roles in fintech, bringing MBA finance training.` The automated run starts it with `E2E objective <run id>:`. |

### Steps

1. Open `/student/resume`.
2. In the list of sections on the left, click Other Details.
3. Type the test objective in the Career objective box.
4. Click Save section.
5. Reload the page.
6. Click Other Details again.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Resume Builder" page opens on "1 Build content", showing Basic Details. The sidebar reads "Profile complete" 0%, and the save chip at the foot reads "Not saved yet". |
| ER-2 | 2 | The section heading changes to "Other Details", with "Objective, key expertise, achievements, awards and activities." and a "Career objective" card. |
| ER-3 | 3 | The counter under the box shows the objective's length out of 6000, e.g. "56 / 6000", and the save chip reads "Unsaved changes". The builder autosaves about 1.5 seconds after the last keystroke, so a slow tester may see "Saved just now" already. |
| ER-4 | 4 | The save chip reads "Saved just now" and "Profile complete" reads 8% (one section of 12). |
| ER-5 | 5 | "Profile complete" still reads 8%, read back from the server. |
| ER-6 | 6 | The Career objective box holds the test objective. |

### Post-conditions

The builder keeps the objective, and the next generated resume opens with it
instead of the profile's career summary. Empty the box and save to undo it.
The automated test restores the builder it found through the API.

---

## TC-310 — Generating a resume composes it on this machine and downloads it as a PDF

| Field | Value |
|---|---|
| ID | TC-310 |
| Module | Student: resume builder |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-310` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. The API has no language model configured (every `LLM_*` and provider key
   blank, the development default). With one configured, the trace names the
   model or says why it was not used, instead of the note in ER-2.
3. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
4. The student has asked for fewer than 5 generated resumes or REEP Agent
   answers in the last minute. Past that, the API answers "Too many AI
   requests. Wait a moment and try again — the limit is 5 per minute."

### Test data

| Field | Value |
|---|---|
| Resume title | Any, e.g. `General — Finance roles`. The automated run uses `E2E resume <run id>`. |

### Steps

1. Open `/student/resume`.
2. Click the step 3 Preview at the top of the page.
3. Type the test title in the "Resume title (optional)" box.
4. Click Generate Resume.
5. Click Download PDF.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "Generated Resume" view opens with "No resume generated yet. Press Generate Resume to compose one from your saved REEP records." Download PDF is disabled and "Page count:" reads "—". |
| ER-2 | 4 | The resume appears in the preview, headed "Test Student", with a "Verified Skills" section listing "MS Excel" (the only verified skill) and an "Academics" section starting "Latest CGPA: 8.2". The "Generation trace" card reads "Deterministic draft" and "composed on this machine", with the note "No language model is configured, so the resume was composed on this machine from your saved REEP records." The button now reads Regenerate, "Page count:" reads 1, and Download PDF is enabled. |
| ER-3 | 5 | A new tab opens the resume as a PDF, from `/api/student/resume/<id>/pdf`. |

### Post-conditions

A new resume version, with the test title, is added to the student's "All
Resumes" list. Nothing in the product deletes a generated resume.

---

## TC-311 — Exporting a resume needs the sharing confirmation

| Field | Value |
|---|---|
| ID | TC-311 |
| Module | Student: resume builder |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-311` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has at least one generated resume. Run TC-310 first, or
   generate one on the Preview step. The automated test reuses the newest
   one, and generates one through the API only when there is none, in which
   case pre-condition 4 of TC-310 applies to it too.

### Test data

| Field | Value |
|---|---|
| Resume to export | The newest generated version: the one TC-310 made, or, when the automated test runs on its own with none, `E2E export <run id>` |

### Steps

1. Open `/student/resume`.
2. Click the step 4 Export & share at the top of the page.
3. Tick "I confirm this resume shares only what I intend recruiters to see."
4. Click the Export & share button under the confirmation.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | "All Resumes" lists the generated versions, newest first. The newest carries its title, the chip "Selected", the line "v<n> · Generated · Updated <date>" and a disabled "In use" button. "Include an evidence appendix" is unticked. The Export & share button is disabled, beside "Tick the confirmation above to enable this." |
| ER-2 | 3 | The Export & share button becomes enabled and the hint disappears. |
| ER-3 | 4 | A new tab opens the selected version as a PDF, from `/api/student/resume/<id>/pdf` with no evidence appendix, and the page says "Your PDF opened in a new tab." |

---

## TC-312 — The jobs board shows match and eligibility for each posting

| Field | Value |
|---|---|
| ID | TC-312 |
| Module | Student: jobs |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-312` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The three seeded postings are open, and the student's record is as seeded:
   latest CGPA 8.2, no live backlogs, and the skills MS Excel (verified),
   Power BI and Financial Modeling.

### Test data

| Posting | Company | Location | Needs | Gate |
|---|---|---|---|---|
| Financial Analyst | Acme Capital | Bengaluru | excel, financial-modeling | CGPA 7.0 or more |
| BI Developer | DataWorks | Remote | power-bi, excel | CGPA 8.5 or more |
| Junior Accountant | LedgerCo | Mysuru | excel | none of its own |

### Steps

1. Open `/student/jobs`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Jobs" page shows one table with the columns Role, Location, Eligibility, Skill match, Status and Action. |
| ER-2 | 1 | Financial Analyst (Acme Capital, Bengaluru) is marked "Eligible" with a 100% skill match. |
| ER-3 | 1 | BI Developer (DataWorks, Remote) is marked "Not eligible" with a 100% skill match. Its Status names the reason, "CGPA 8.2 is below the required 8.5", and its action button reads "Not eligible" and is disabled. |
| ER-4 | 1 | Junior Accountant (LedgerCo, Mysuru) is marked "Eligible" with a 100% skill match. |

---

## TC-313 — Filtering the jobs board

| Field | Value |
|---|---|
| ID | TC-313 |
| Module | Student: jobs |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-313` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The three seeded postings are open, as in TC-312. None of them has a
   closing date.
4. One more open posting closes within the week. Create it as the Main Admin
   with "Post a job" on `/admin/jobs`, with the test data below and no
   college, course or track, so every student sees it. The automated test
   creates it through the same API as `admin@bgscet.ac.in`.

### Test data

| Field | Value |
|---|---|
| Title | `E2E closing <run id>` (any unique title) |
| Company | `E2E Co` |
| Level | PG |
| Location | `E2E Town` |
| Deadline | Five days from today |
| Apply link | none |

### Steps

1. Open `/student/jobs`.
2. Set Eligibility to Not eligible.
3. Set Location to Mysuru.
4. Click Clear.
5. Set Deadline to Closing soon (≤7 days).

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The test posting is listed. Under its location, its deadline reads "Closes in N days" with N between 5 and 7. The count rounds a part-day up, and the posting closes at 23:59 UTC on its deadline date. |
| ER-2 | 2 | Only BI Developer is listed. Financial Analyst, Junior Accountant and the test posting are hidden, and a Clear button appears. |
| ER-3 | 3 | No posting is both not eligible and in Mysuru, so the table reads "No roles match these filters." |
| ER-4 | 4 | Every posting is listed again, the filters read All, All locations and All, and the Clear button is gone. |
| ER-5 | 5 | Only postings closing within seven days are listed: the test posting is shown, and the three seeded postings, which have no deadline, are hidden. |

### Post-conditions

The test posting stays on the board. Delete it as the Main Admin on
`/admin/jobs` (nobody has applied to it). The automated test deletes it
through the API.

---

## TC-314 — Applying to an eligible posting

| Field | Value |
|---|---|
| ID | TC-314 |
| Module | Student: jobs |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-314` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. An open posting the student is eligible for and has not applied to, with
   an apply link. Create it as the Main Admin with "Post a job" on
   `/admin/jobs`, with the test data below and no college, course or track.
   The automated test creates it through the same API as
   `admin@bgscet.ac.in`.

### Test data

| Field | Value |
|---|---|
| Title | `E2E apply <run id>` (any unique title) |
| Company | `E2E Co` |
| Level | PG |
| Location | `E2E Town` |
| Deadline | none |
| Apply link | `https://example.com/apply/<run id>` |

The form cannot name required skills (it only carries them over from a
duplicated posting), and a posting that names none reads "Skills not listed"
for every student rather than a match percentage.

The automated run answers the apply link inside the browser with a small
local page, so it does not depend on example.com being reachable.

### Steps

1. Open `/student/jobs`.
2. Click Apply on the test posting's row.
3. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The test posting's row reads "Eligible", "Skills not listed" in the Skill match column and "Not applied", with an Apply button. |
| ER-2 | 2 | The posting's apply link opens in a new tab. The row's status changes to "Applied", and its button reads "Applied" and is disabled. |
| ER-3 | 3 | The row still reads "Applied": the application was recorded on the server. |

### Post-conditions

The application stays on the student's record: a student cannot withdraw it,
and the posting can no longer be deleted. Close the posting as the Main Admin
on `/admin/jobs` to take it off the board. The automated test closes it
through the API.

---

## TC-315 — The English baseline shows pending sections as dashes, not zeros

| Field | Value |
|---|---|
| ID | TC-315 |
| Module | Student: English baseline |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-315` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student's latest English baseline attempt is the seeded one, for
   semester 2: Reading, Writing and Listening scored, Speaking pending. Only
   the assessment pipeline scores a section, so nothing on screen changes it.

### Test data

| Section | Score | CEFR |
|---|---|---|
| Reading | 68 | B2 |
| Writing | 57 | B1 |
| Listening | 61 | B1 |
| Speaking | pending | — |
| Overall | 62 | B1+ (provisional) |

### Steps

1. Open `/student/english`.
2. Click View AI report on the Reading card.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "English Proficiency Baseline" page shows the overall score "62" "/ 100" under "Provisional band" "B1+" "Independent user", with the chips "3 of 4 sections scored" and "Speaking pending · 12 min", "Assessment progress" at 75%, and "Your band is provisional until the speaking section is submitted." |
| ER-2 | 1 | The Reading, Writing and Listening cards are marked "Scored" and show 68, 57 and 61 with CEFR B2, B1 and B1. Listening reads "No written report for this section." |
| ER-3 | 1 | The Speaking card is marked "Pending" and shows "--" where the score goes, never 0, with "CEFR —". Its Fluency, Pronunciation and Interaction bars also read "--", and it offers "Start speaking test". |
| ER-4 | 2 | The Reading card shows the assessor's note "Reads at pace and handles detail well; inference under time pressure is the next lift." and its button now reads "Hide AI report". |

---

## TC-316 — The English baseline allows one attempt per semester

| Field | Value |
|---|---|
| ID | TC-316 |
| Module | Student: English baseline |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-316` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student's current semester is 2, and the seeded semester-2 attempt of
   TC-315 exists.

### Test data

None beyond the seeded attempt.

### Steps

1. Open `/student/english`.
2. Click Resume assessment.
3. Reload the page.
4. Click Start speaking test on the Speaking card.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The main button reads "Resume assessment", not "Start assessment", because this semester's attempt already exists. |
| ER-2 | 2 | The page says "Resuming the attempt already open for this semester." and still shows the same attempt: "62", "3 of 4 sections scored". No second, blank attempt is opened. |
| ER-3 | 4 | The same message appears again and the attempt is unchanged: starting a single section also reopens this semester's one attempt. |

---

## TC-317 — Downloading the English baseline report

| Field | Value |
|---|---|
| ID | TC-317 |
| Module | Student: English baseline |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-317` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The seeded attempt of TC-315 exists.

### Test data

None.

### Steps

1. Open `/student/english`.
2. Click Download report.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser saves a PDF file named `english-baseline.pdf`. |
| ER-2 | 2 | **Manual only.** The PDF is headed "English Proficiency Baseline", then "Test Student · 1BG24MBA001 · Taken <date>". It reads "Provisional band: B1+ · Independent user", "Overall: 62 / 100" and "3 of 4 sections scored — Speaking pending · 12 min". In its Sections table, Speaking reads "Not yet taken", "—" and "Pending assessment", never 0. |

---

## TC-318 — Starting the English baseline for the first time

| Field | Value |
|---|---|
| ID | TC-318 |
| Module | Student: English baseline |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | None (manual only): it needs a student with no attempt for their current semester. The dev seed's only student already has one, and nothing in the product deletes an attempt, so an automated run could pass once per database and never again. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV`.
2. You are signed in as a student who has never opened an English baseline
   attempt, for example one approved through the registration flow of module
   02. Nothing else is signed in as that account.

### Test data

None.

### Steps

1. Open `/student/english`.
2. In the same browser, open `/api/student/english-baseline/report`.
3. Go back to `/student/english` and click Start assessment.
4. Click Resume assessment.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page reads "You have not taken the baseline yet." and "It takes about 70 minutes across four sections and places you on the CEFR scale. Your mentor sees the band, not your answers." with Start assessment buttons. There is no Download report button. |
| ER-2 | 2 | The API answers 404 with `{"detail":"You have not taken the English baseline yet."}`. No report exists before an attempt. |
| ER-3 | 3 | The page says "Assessment opened. Your sections are listed below — take them in any order." The attempt shows "--" "/ 100", band "—" and "Not yet banded", "0 of 4 sections scored" and "Reading pending". All four cards are "Pending" with "--". The main button now reads "Resume assessment", and Download report appears. |
| ER-4 | 4 | The page says "Resuming the attempt already open for this semester." A second attempt is never created in the same semester. |

### Post-conditions

The student has an attempt for this semester, so this case cannot be run
again with the same account.

---

## TC-319 — The Faculty / TPO Log shows the meeting history and SWOC

| Field | Value |
|---|---|
| ID | TC-319 |
| Module | Student: mentor log |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-319` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The seeded mentor note and the four seeded SWOC lines are still on the
   student's record.

### Test data

| Quadrant | Line | Written by |
|---|---|---|
| Strength | Strong analytical and quantitative skills. | Test Mentor · Your mentor |
| Weakness | Needs structured problem-solving practice. | Main Admin (seed) · Placement cell |
| Opportunity | Fintech internships opening this quarter. | Main Admin (seed) · Programme |
| Challenge | Public speaking under time pressure. | Test Mentor · Your mentor |

The seeded meeting: "1:1 review" at Cabin 3, action "1:1 scheduled", note
"Discussed placement readiness; strong on analytics, work on GD delivery.",
logged by Test Mentor.

### Steps

1. Open `/student/mentor-log`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Faculty / TPO Log" page opens with a Request a meeting button. |
| ER-2 | 1 | The card "SWOC — Strengths, Weaknesses, Opportunities, Challenges" shows each line of the test data in its quadrant, with its author, its source and the date it was written. |
| ER-3 | 1 | "Meeting history" lists the seeded meeting: "1:1 review · Cabin 3", the chip "1:1 scheduled", its note, and "Logged by Test Mentor". |

---

## TC-320 — Requesting a meeting with the mentor

| Field | Value |
|---|---|
| ID | TC-320 |
| Module | Student: mentor log |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-320` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student's mentor is still Test Mentor (`mentor@bgscet.ac.in`), as
   seeded.

### Test data

| Field | Value |
|---|---|
| What would you like to discuss? | Any unique text, e.g. `GD delivery before the mock next month`. The automated run uses `E2E meeting <run id>`. |
| Preferred time (optional) | `Thursday afternoon` |

### Steps

1. Open `/student/mentor-log`.
2. Click Request a meeting.
3. Type the test reason in the "What would you like to discuss?" box.
4. Type Thursday afternoon in the "Preferred time (optional)" box.
5. Click Send request.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A "Request a meeting" form opens with "This goes to your mentor and appears in the log below, so you both have the same record of what was asked." The Send request button is disabled while the reason is empty, and the Request a meeting button is disabled while the form is open. |
| ER-2 | 3 | Send request becomes enabled. |
| ER-3 | 5 | The form closes and the page says "Sent to Test Mentor. It appears in your meeting log below." The top of "Meeting history" shows "Meeting requested" with the chip "Note only", the note "Meeting requested by the student. <reason> Preferred time: Thursday afternoon.", and "Logged by Test Mentor". |
| ER-4 | 5 | The mentor receives it: signed in as `mentor@bgscet.ac.in`, the student's notes on the Mentee Log include the same note. |

### Post-conditions

The request stays as a note in the student's log and the mentor's notes. The
mentor can remove it from the Mentee Log. The automated test removes it
through the API as the mentor.

---

## TC-321 — A student with no mentor cannot request a meeting

| Field | Value |
|---|---|
| ID | TC-321 |
| Module | Student: mentor log |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | None (manual only): it needs a student with no mentor. The dev seed's only student has one, and releasing that mentor to set this up writes the student's mentor history and gives the mentor a 90-day read-only handover grant, which the product never removes. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV`.
2. You are signed in as a student with no faculty member assigned, and
   nothing else is signed in as that account. The Main Admin releases a
   student on the Assign faculty screen (`/admin/mentors`).

### Test data

| Field | Value |
|---|---|
| What would you like to discuss? | Any text |

### Steps

1. Open `/student/mentor-log`.
2. Click Request a meeting.
3. Type the test reason in the "What would you like to discuss?" box.
4. Click Send request.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The page says "You do not have a mentor assigned yet, so there is nobody to send this to. The placement office assigns mentors." The form stays open with the text still in it, and nothing is added to "Meeting history". |

---

## TC-322 — The Mock interviews screen before any interview

| Field | Value |
|---|---|
| ID | TC-322 |
| Module | Student: mock interview records |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-322` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has never started a mock interview, as on a fresh seed. Any
   interview started on `/student/assistant`, even one that failed to
   connect (TC-327), leaves a record here, and so do module 08's interview
   record cases. The automated test is Skipped when one exists: that is used-up
   test data, not a broken environment. The full suite runs this module before
   module 08, so a fresh database always reaches this case.

### Test data

None.

### Steps

1. Open `/student/interviews`.
2. Click Take your first interview.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Mock interviews" page opens with "These are kept on the college's server as part of your interview record. Your mentor and the placement office can read them. Clearing your conversation on the interview screen does not delete them — ask the placement cell if you need one removed." |
| ER-2 | 1 | Instead of a table, a card reads "No mock interviews yet" and "The AI interviewer asks one question at a time, out loud, and writes you a practice report at the end. Nothing here is graded and nothing counts towards placement." |
| ER-3 | 2 | The browser goes to `/student/assistant`, the "Mock interview" page. |

---

## TC-323 — Reading a past interview's transcript and report

| Field | Value |
|---|---|
| ID | TC-323 |
| Module | Student: mock interview records |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): a record with turns and a report exists only after a real voice interview with Amazon Nova 2 Sonic on AWS Bedrock (TC-326), which needs AWS credentials and a microphone that the local test run does not have. |

### Pre-conditions

1. The web app and the API are running, with an interview engine that can
   reach its model, as in TC-326.
2. You are signed in as a student who has finished at least one mock
   interview, for example in TC-326.

### Test data

The interview held in TC-326.

### Steps

1. Open `/student/interviews`.
2. Click Open on the interview's row.
3. Click Close on the same row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | **Manual only.** The tiles "Interviews taken", "Completed to the end" and "Reports written" count the student's interviews. The table has the columns Date, Track, Status, Answers, Length and Report, and the interview is listed with its track in words (e.g. "Financial Analytics"), a status such as "Completed" or "Ended early", and its report status. |
| ER-2 | 2 | **Manual only.** Under the table, the interview opens: its track as a heading, its date, time and length, and "reached <stage>". It states whether a recording was kept, and with recording off: "No recording of your voice was kept for this interview — the transcript below is the whole of what was saved." Below come the practice report card (or "No report for this interview" with the reason) and the "Transcript", with each line marked You or Interviewer under its stage. |
| ER-3 | 3 | **Manual only.** The detail closes and the row's button reads Open again. |

---

## TC-324 — The mock interview page and its round picker

| Field | Value |
|---|---|
| ID | TC-324 |
| Module | Student: mock interview |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-324` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student's batch implies no interview track. The seeded specialization
   is Finance with the code `FIN`, and no track has that code (Financial
   Analytics is `fa`), so no round is preselected and General is selected.
   On a batch whose code matches a track, that track is preselected instead.

### Test data

| Field | Value |
|---|---|
| Round | Finance |

### Steps

1. Open `/student/assistant`.
2. In the round picker, click Finance.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "Mock interview" page opens with the note "The interviewer hears your voice and nothing else — it cannot see your marks, attendance or USN." The interview stage reads "Not connected" and "Pick a round, then press Start when you are ready.", lists the stages Opening, Probing, Deep dive and Wrap-up, and offers a Start interview button. |
| ER-2 | 1 | The round picker offers General, HR, Marketing, Analytics and Finance, with General selected. The briefing reads "Campus panel interviewer", "General placement round · Tier-1 MNC campus bar" and "Placement readiness across the board — no single track, and no scored report." |
| ER-3 | 2 | Finance is selected instead of General. The briefing changes to "Managing Director, Finance", "Finance & valuation round · Tier-1 MNC campus bar" and "DCF modelling, financial ratios, risk, valuation, M&A." Nothing starts: the stage still reads "Not connected". |

---

## TC-325 — Start shows the college's interview terms, and Cancel records nothing

| Field | Value |
|---|---|
| ID | TC-325 |
| Module | Student: mock interview |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-325` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The student has never agreed to the interview terms, as on a fresh seed.
   Agreeing (TC-326, TC-327) records a consent the student cannot withdraw,
   and after that Start goes straight to the interview. Module 08's interview
   record cases agree on the student's behalf too. The automated test is
   Skipped when a consent exists, for the reason given in TC-322.
4. The student's college has no interview policy of its own, so the default
   applies: transcripts kept for 180 days, no recording, 8 interviews a day,
   8 minutes each. The automated test is Blocked otherwise.
5. The API's interview engine is the default, Amazon Nova 2 Sonic
   (`INTERVIEW_ENGINE` blank or `nova`).

### Test data

None.

### Steps

1. Open `/student/assistant`.
2. Click Start interview.
3. Click Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A "Before you start" dialog opens, before the microphone is asked for. Section 1, "The interviewer hears you live", says the microphone audio is streamed to "Amazon's Nova Sonic model, running on AWS Bedrock" and that nothing from the student record is sent with it. |
| ER-2 | 2 | Sections 2 to 4 state the college's policy: a transcript is kept and "It is deleted automatically after 180 days."; "Your college does not record these interviews."; and "You have N of 8 practice interviews used in the last 24 hours, and each one runs for up to 8 minutes.", where N is the number of interviews completed in the last 24 hours (0 on a fresh seed). The buttons are Cancel and "I agree — start the interview". |
| ER-3 | 3 | The dialog closes, the stage still reads "Not connected", and no "Terms accepted" line appears under Start interview: cancelling records no consent. |

---

## TC-326 — Holding a real mock interview

| Field | Value |
|---|---|
| ID | TC-326 |
| Module | Student: mock interview |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | None (manual only): the interview is a live speech-to-speech call to Amazon Nova 2 Sonic on AWS Bedrock. It needs AWS credentials allowed to call the model, and a microphone and a person to answer out loud, none of which the local test run has. |

### Pre-conditions

1. The web app and the API are running with the interview engine able to
   reach its model: `INTERVIEW_ENGINE` blank or `nova`, a region set
   (`NOVA_SONIC_REGION`, `BEDROCK_REGION` or `AWS_REGION`), and AWS
   credentials allowed `bedrock:InvokeModelWithBidirectionalStream` on
   `amazon.nova-2-sonic-v1:0`.
2. The browser has a working microphone and speakers, and the page is served
   over HTTPS or from localhost.
3. You are signed in as a student who has completed fewer than 8 interviews in
   the last 24 hours, and nothing else is signed in as that account.

### Test data

| Field | Value |
|---|---|
| Round | Finance |

### Steps

1. Open `/student/assistant`.
2. In the round picker, click Finance.
3. Click Start interview.
4. If the "Before you start" dialog opens, click "I agree — start the interview".
5. Allow the browser to use the microphone.
6. Answer the interviewer's questions out loud until it gives its verdict, or click End interview.
7. Open `/student/interviews`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | **Manual only.** The dialog closes. Under Start interview a line reads "Terms accepted <date> · recording off" with a "Read again" button. |
| ER-2 | 5 | **Manual only.** The stage moves from "Connecting…" to "Connected" and "Listening", a clock counts up to the limit (e.g. "/ 08:00"), and the interviewer speaks first. The floating orb shows "Live" and the clock. |
| ER-3 | 6 | **Manual only.** The transcript lists the Interviewer's and your lines as they are spoken, and the stages light up in order. After the verdict, a practice report card appears with "Saved to your past interviews with the full transcript." |
| ER-4 | 7 | **Manual only.** The interview is listed, with Track "Financial Analytics" and a status of "Completed" or "Ended early". |

### Post-conditions

The student holds a consent under the current terms, so TC-325's
pre-condition no longer holds for them. The interview record is kept for 180
days.

---

## TC-327 — Starting an interview when the speech model cannot be reached

| Field | Value |
|---|---|
| ID | TC-327 |
| Module | Student: mock interview |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | None (manual only): it needs a microphone, which the automated browser does not have, and every attempt permanently adds a consent and a Failed interview record that nothing in the product removes, so a second automated run would meet a different screen. |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied, and the interview
   engine cannot reach its model. On the local stack the engine is Nova with
   a region set, so `GET /api/interview/status` answers `"available": true`,
   but Bedrock refuses the stream.
2. The browser has a microphone, and the page is served from localhost.
3. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.

### Test data

None.

### Steps

1. Open `/student/assistant`.
2. Click Start interview.
3. If the "Before you start" dialog opens, click "I agree — start the interview".
4. Allow the browser to use the microphone.
5. Open `/student/interviews`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | **Manual only.** The stage reads "Preparing the interviewer…" for a few seconds, then "Problem", with the message "The interview service is temporarily unavailable. Please try again shortly." and a Dismiss button. The microphone is released. |
| ER-2 | 5 | **Manual only.** The attempt is listed with the status "Failed" and no report. It counts towards the 20 attempts a day, but not towards the 8 completed interviews. |

### Post-conditions

The student holds a consent and a Failed interview record, so the
pre-conditions of TC-322 and TC-325 no longer hold for them.

---

## TC-328 — Asking the REEP Agent, rating the answer and clearing the conversation

| Field | Value |
|---|---|
| ID | TC-328 |
| Module | Student: REEP Agent |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-328` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied, so the Knowledge Base
   holds the approved guidance documents.
2. The API has no language model configured, so answers are composed from
   the Knowledge Base and the student's records without a model, and are the
   same on every run.
3. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
4. The student's REEP Agent conversation is empty. If it is not, click Clear
   conversation first. The automated test clears it through the API.
5. The student has asked for fewer than 5 REEP Agent answers or generated
   resumes in the last minute.

### Test data

| Field | Value |
|---|---|
| Question | "How do I verify a skill?" (one of the four suggested questions) |

### Steps

1. Open `/student/agent`.
2. Click the suggested question How do I verify a skill?
3. Click the thumbs-up (Helpful) button under the answer.
4. Click Clear conversation.
5. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The "REEP Agent" page opens with "The REEP Agent is a general helper — it does not see your private records, marks or attendance." and an empty conversation: "How can I help today?" with the suggested questions "What should I complete this week?", "Am I placement-ready?", "Show jobs I qualify for" and "How do I verify a skill?". Clear conversation is disabled. |
| ER-2 | 2 | The question appears as your message, followed by the agent's answer. As the first reply in the conversation it opens with the greeting "Jai Shri Gurudev!", then explains: "To get a skill like Power BI verified, upload a certificate or proof of completion as evidence and raise a skill claim with the level you are claiming." Under it is the source chip "Source: Verifying a skill (e.g. Power BI)", with Copy, Helpful, Not helpful and Report controls. |
| ER-3 | 3 | Helpful is shown as pressed and "Thanks for the feedback" appears. The server accepted the rating, so the button stays pressed. |
| ER-4 | 4 | The conversation empties back to "How can I help today?", and Clear conversation is disabled again. |
| ER-5 | 5 | The conversation is still empty after the reload: it was cleared on the server. |

### Post-conditions

The rating stays recorded against the answer, for the office's assistant
metrics. The cleared conversation is kept on the server, marked deleted, and
is no longer shown.

---

## TC-329 — The floating orb opens the assistant dock with both tabs

| Field | Value |
|---|---|
| ID | TC-329 |
| Module | Student: REEP Agent |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-329` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. The API has no language model configured, as in TC-328.
3. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
4. The student has asked for fewer than 5 REEP Agent answers or generated
   resumes in the last minute.

### Test data

| Field | Value |
|---|---|
| Question | `How do I apply for a job?` |

### Steps

1. Open `/student/jobs`.
2. Click the round assistant button at the bottom right of the screen.
3. Type How do I apply for a job? in the message box and press Enter.
4. Click the Mock interview tab.
5. Click the Close button at the top of the dock.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The "REEP assistant" dock opens on the "Ask REEP" tab, beside a "Mock interview" tab. Its foot reads "Does not see your marks, attendance or USN — for those, open your records.", and its "Open as a page" link leads to `/student/agent`. |
| ER-2 | 3 | The agent answers "To apply for a job: open the posting and check the eligibility gates and your match percentage." under the source chip "Source: Steps to apply for a job". |
| ER-3 | 4 | The Mock interview tab is selected and shows the interview room, with its round picker and a Start interview button. "Open as a page" now leads to `/student/assistant`. |
| ER-4 | 5 | The dock closes, and the round button's label reads "Open the REEP assistant" again. |

### Post-conditions

The question and its answer stay in the student's REEP Agent conversation,
which `/student/agent` shows too.

---

## TC-330 — Reading a posting's job description

| Field | Value |
|---|---|
| ID | TC-330 |
| Module | Student: jobs |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/04-student-tools.spec.ts`, title tagged `@TC-330` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV` and the dev seed applied.
2. You are signed in as the dev seed's student, and nothing else is signed in
   as that account.
3. The seeded Financial Analyst posting is open, with its seeded description.

### Test data

| Posting | Company | Description |
|---|---|---|
| Financial Analyst | Acme Capital | `Financial analysis and modeling.` |

### Steps

1. Open `/student/jobs`.
2. Click View JD on the Financial Analyst row.
3. Click Hide JD on the same row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The Financial Analyst row shows a "View JD" button under the company name. |
| ER-2 | 2 | A "Job description" panel opens under the row and reads "Financial analysis and modeling.". The button now reads "Hide JD". |
| ER-3 | 3 | The panel closes, and the button reads "View JD" again. |

### Post-conditions

None. Opening a description changes nothing on the server.
