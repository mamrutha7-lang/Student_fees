# Faculty and alumni

| Field | Value |
|---|---|
| Screens | `/mentor/notebook`, `/mentor/mentees`, `/mentor/verifications`, `/mentor/upskilling`, `/mentor/signature`, `/mentor/leave`, `/mentor/agent`, `/alumni`, `/alumni/jobs` |
| Automated tests | [`tests/05-faculty-alumni.spec.ts`](../../tests/05-faculty-alumni.spec.ts) |
| ID range | TC-400 to TC-499 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

Every faculty case is run as the dev seed's faculty member,
`mentor@bgscet.ac.in` (Test Mentor), who mentors exactly one student: the dev
seed's Test Student, USN `1BG24MBA001`, Semester 2, stage `EXCEL_ADVANCED`.
REEP keeps one live session per account, so nothing else may sign in as the
faculty member, the student or the alumnus while a case runs: no other
browser, no pytest run and no other Playwright run against the same database.

Where a case says `<run id>`, use any short text that is new for this run,
for example the time of day (`1432`). The automated run uses a base-36
timestamp. It keeps repeated runs apart on the same database.

---

## TC-400 — Faculty notebook shows the assigned student and the log form

| Field | Value |
|---|---|
| ID | TC-400 |
| Module | Faculty: notebook |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-400` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV`, and the dev seed has been applied.
2. The browser is signed in as the faculty member, `mentor@bgscet.ac.in`.

### Test data

| Field | Value |
|---|---|
| Faculty member | `mentor@bgscet.ac.in` (Test Mentor) |
| Assigned student | Test Student, USN `1BG24MBA001` |

### Steps

1. Open `/mentor/notebook`.
2. Open the "Student" list at the top of the Mentoring log card.
3. Click Add entry.
4. Click Close.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Faculty notebook", under the eyebrow "Staff · private notebook", with the chip "Private by default". The sidebar's "Notebook" item is highlighted. The card's footer reads "Entries stay staff-private until you publish them to the student's Mentor Meeting Log." The log table has the columns Date, Key discussions, Follow up and Remarks. |
| ER-2 | 2 | The list offers exactly one student, "Test Student · 1BG24MBA001", and it is selected. A faculty member sees only the students assigned to them. |
| ER-3 | 3 | A form opens with the fields "Date" (today's date), "Key discussions", "Follow up" and "Remarks", and a Save entry button. "Remarks" offers On track, Watch, Done and Escalate, with On track chosen. The Add entry button now reads Close. |
| ER-4 | 4 | The form closes and the button reads Add entry again. |

---

## TC-401 — Add, publish and delete a notebook entry

| Field | Value |
|---|---|
| ID | TC-401 |
| Module | Faculty: notebook |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-401` |

### Pre-conditions

1. As TC-400. The faculty member is signed in in one browser.
2. A second browser (or a private window) is available to sign in as the
   student, `student@bgscet.ac.in` / `student123`, for steps 6 and 8.

### Test data

| Field | Value |
|---|---|
| Date | `2026-09-15` |
| Key discussions | `E2E notebook <run id>: reviewed the internship shortlist` |
| Follow up | `Send two applications by Friday` |
| Remarks | Watch |

### Steps

1. Open `/mentor/notebook`.
2. Click Add entry.
3. Enter the date in the "Date" field, the text in the "Key discussions" and "Follow up" fields, and choose Watch under "Remarks".
4. Click Save entry.
5. On the new row, click the publish button ("Publish to the student's Mentor Meeting Log").
6. In the second browser, sign in as the student and open `/student/mentor-log`.
7. Back in the faculty browser, click the delete button ("Delete entry") on the row, and accept the confirmation.
8. In the student's browser, reload `/student/mentor-log`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The form closes and the log gains a row: Date "15 Sep 2026", the key discussions text, "Send two applications by Friday" under Follow up, and a "Watch" chip under Remarks. The row is not marked "Published", and it offers the publish button. |
| ER-2 | 5 | The row is marked "Published" beside its date, and the publish button is gone. |
| ER-3 | 6 | The student's "Faculty / TPO Log" lists the entry: the key discussions text, "Logged by Test Mentor". |
| ER-4 | 7 | The browser asks "Remove this entry? The student can already see it on their Mentor Meeting Log; it will disappear from there too." After it is accepted, the row leaves the notebook. |
| ER-5 | 8 | The entry is no longer on the student's log. |

### Post-conditions

The entry is archived, not destroyed: the API keeps it, with its revision
history, and no screen shows it. The student's browser holds a live student
session.

---

## TC-402 — A notebook entry is refused without the key discussions

| Field | Value |
|---|---|
| ID | TC-402 |
| Module | Faculty: notebook |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-402` |

### Pre-conditions

1. As TC-400.

### Test data

| Field | Value |
|---|---|
| Follow up | `E2E follow-up <run id>` |
| Key discussions | left empty |

### Steps

1. Open `/mentor/notebook`.
2. Click Add entry.
3. Leave "Key discussions" empty and enter the text in the "Follow up" field.
4. Click Save entry.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The form shows the error "Write the key discussions first." and stays open with the follow-up text still in it. No row with that follow-up is added to the log. |

---

## TC-403 — Mentee Log lists the assigned student and their meeting notes

| Field | Value |
|---|---|
| ID | TC-403 |
| Module | Faculty: mentee log |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-403` |

### Pre-conditions

1. As TC-400.
2. The seed's meeting note for the student has not been deleted. The dev
   seed writes it with the heading "1:1 review", the location "Cabin 3" and the
   linked action "1:1 scheduled".
3. The faculty member holds no "View student records" grant from the Main
   Admin. Mentoring the student is enough to open their record (2026-10-16).

### Test data

| Field | Value |
|---|---|
| Seeded note | "Discussed placement readiness; strong on analytics, work on GD delivery." |

### Steps

1. Open `/mentor/mentees`.
2. Click "Full record".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Mentee Log", with "Your mentees and the 1:1 meeting notes they can read." under it. The "My students" card lists one student, "Test Student", with "1BG24MBA001 · Sem 2 · EXCEL_ADVANCED", and that student is selected. |
| ER-2 | 1 | The right-hand card reads "Log a meeting with Test Student", with the fields "Heading (optional)", "Linked action" and "Meeting note" and a Save note button, and a "Full record" link beside the heading. |
| ER-3 | 1 | The seeded note is listed under the form: the heading "1:1 review · Cabin 3", its text, and a "1:1 scheduled" chip. |
| ER-4 | 2 | The student's full record opens at `/admin/students/<id>`, for Test Student, read only: there is no Edit or Remove button. Its tabs are Overview, Resume, Skills, Leaderboard, Academics, Interviews, Time sheet and Documents: there is no Access tab and no "Who can open this record" card, which are the Main Admin's alone. |

---

## TC-404 — Search the mentee list by name or USN

| Field | Value |
|---|---|
| ID | TC-404 |
| Module | Faculty: mentee log |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-404` |

### Pre-conditions

1. As TC-400.

### Test data

| Field | Value |
|---|---|
| A search that matches nothing | `zzz-nobody` |
| The student's USN, in lower case | `1bg24mba001` |

### Steps

1. Open `/mentor/mentees`.
2. Enter `zzz-nobody` in the "Search name or USN" box.
3. Replace the search with `1bg24mba001`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The list shows "No student matches that search." and no student. |
| ER-2 | 3 | Test Student is listed again. The search ignores case and matches the USN as well as the name. |

---

## TC-405 — Save and delete a meeting note

| Field | Value |
|---|---|
| ID | TC-405 |
| Module | Faculty: mentee log |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-405` |

### Pre-conditions

1. As TC-400.
2. A second browser is available to sign in as the student,
   `student@bgscet.ac.in`, for steps 5 and 7.

### Test data

| Field | Value |
|---|---|
| Heading | `E2E review <run id>` |
| Linked action | Flagged for follow-up |
| Meeting note | `E2E note <run id>: practise one GD topic a day` |

### Steps

1. Open `/mentor/mentees`.
2. Enter the heading in "Heading (optional)", choose "Flagged for follow-up" under "Linked action", and enter the note in "Meeting note".
3. Click Save note.
4. Reload the page.
5. In the second browser, sign in as the student and open `/student/mentor-log`.
6. Back in the faculty browser, click the delete button ("Delete note") on the new note, and accept the confirmation.
7. In the student's browser, reload `/student/mentor-log`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A "Saved" chip shows beside Save note, the form is emptied, and the note is listed under it: the heading, the note text and a "Flagged for follow-up" chip. |
| ER-2 | 4 | The note is still listed after the reload. |
| ER-3 | 5 | The student's "Faculty / TPO Log" shows the note under the heading, with "Logged by Test Mentor". |
| ER-4 | 6 | The browser asks "Delete this note? It disappears from the student’s Mentor Meeting Log as well." After it is accepted, the note leaves the list. |
| ER-5 | 7 | The note is no longer on the student's log. |

### Post-conditions

The note is soft-deleted: the database keeps it, marked deleted, and no screen
shows it.

---

## TC-406 — A meeting note is refused when it is empty

| Field | Value |
|---|---|
| ID | TC-406 |
| Module | Faculty: mentee log |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-406` |

### Pre-conditions

1. As TC-400.

### Test data

| Field | Value |
|---|---|
| Heading | `E2E empty <run id>` |
| Meeting note | three spaces |

### Steps

1. Open `/mentor/mentees`.
2. Enter the heading in "Heading (optional)" and three spaces in "Meeting note".
3. Click Save note.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A "Write the note first" warning shows beside Save note. No note with the heading is added, and the heading stays in its field. |

---

## TC-407 — Verifications lists the pending skill claims and documents the faculty member may verify

| Field | Value |
|---|---|
| ID | TC-407 |
| Module | Faculty: verifications |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-407` |

### Pre-conditions

1. As TC-400, and the faculty member holds "Verify skills & evidence" for the
   seeded college. The dev seed grants it as the placement office would in Who
   can do what; mentoring the student does not open this screen.
2. The dev seed's pending items for the student have not been decided: the
   skill claim "Business Analytics Fundamentals" (issued by Coursera, no file
   attached), and two uploaded documents waiting for review, "Profile photo"
   (`me.png`) and "Leadership certificate" (`leadership_completion.pdf`).

### Test data

None beyond the seeded items above.

### Steps

1. Open `/mentor/verifications`.
2. On the "Business Analytics Fundamentals" claim, click Review evidence.
3. Click Close.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Verifications", under the eyebrow "Faculty · skill verification", with a "Skill claims" and a "Documents" count chip in the header. |
| ER-2 | 1 | Under "Skill claims", a card reads "Business Analytics Fundamentals · Sectoral Skills · Certificate", "Test Student · submitted" and the date it was filed, with a "Submitted" chip and a Review evidence button. |
| ER-3 | 1 | Under "Documents", "Profile photo · Photo" and "Leadership certificate · Certificate" are listed, each for Test Student, with a "Pending review" chip, an "Open …" link naming the file, a "Reviewer note" field and Verify and Reject buttons. |
| ER-4 | 2 | The card opens and its chip reads "Under review". It shows "Badge claimed: Business Analytics Fundamentals", "Category: Sectoral Skills", "Evidence: Certificate · Business Analytics Fundamentals", "Issued by: Coursera", "Student: Test Student · 1BG24MBA001", and under "What the student told you": "Completed last week — certificate attached on my uploads." A "No file attached" chip, the "Verification rubric", the "Note to the student" field and the Verify, Request changes, Reject and Close buttons are shown. |
| ER-5 | 3 | The card closes back to its summary with a "Submitted" chip. Nothing was decided: the claim is still waiting for review. |

---

## TC-408 — Verify a skill claim

| Field | Value |
|---|---|
| ID | TC-408 |
| Module | Faculty: verifications |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-408` |

### Pre-conditions

1. As TC-400, and the faculty member holds "Verify skills & evidence" for the
   seeded college. The dev seed grants it as the placement office would in Who
   can do what; mentoring the student does not open this screen.
2. The student has filed a claim to be decided. Sign in as the student on
   `/student/skilling`, and claim the "SQL" badge with a certificate: evidence
   type Certificate, the title and provider in the test data, the file
   `tests/fixtures/sample.pdf`. The automated test files the same claim
   through the API, as the student, under a file name unique to the run.
3. No other claim of the student's on the "SQL" badge is waiting for review.
   The automated test rejects leftovers from interrupted earlier runs (titles
   starting "E2E ") before it starts.
4. The student has not already earned "SQL". The dev seed does not award it.

### Test data

| Field | Value |
|---|---|
| Badge | SQL (Platform / Technical Skills) |
| Evidence title | `E2E SQL verify <run id>` |
| Issued by | `E2E Academy` |
| Certificate file | `tests/fixtures/sample.pdf` (the automated test names it `e2e-verify-<run id>.pdf`) |
| Note to the student | `E2E verified <run id>` |

### Steps

1. Open `/mentor/verifications`.
2. On the "SQL" claim, click Review evidence.
3. Click the certificate's file name.
4. Enter the note in "Note to the student".
5. Click Verify.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The card shows "Badge claimed: SQL", "Category: Platform / Technical Skills", "Evidence: Certificate · E2E SQL verify <run id>" and "Issued by: E2E Academy", with a link named after the certificate file. |
| ER-2 | 3 | The browser downloads the certificate under its file name. |
| ER-3 | 5 | The claim leaves the "Skill claims" queue. "Recently reviewed" lists it first, with a "Verified" chip: "SQL · Test Student · <today> · “E2E verified <run id>”". The certificate does not appear under "Documents". |
| ER-4 | 5 | For the student, the "SQL" badge is earned, and the certificate on their Uploads screen reads Verified with the reviewer's note. |
| ER-5 | 5 | **Manual only.** On a server with mail configured (`SES_FROM_ADDRESS` set, or `BADGE_MAIL_ENABLED=true` with the console outbox), the student is emailed "Your SQL badge is verified". A development server with neither sends nothing, by design. |

### Post-conditions

The student holds the "SQL" badge, and the claim is on record as approved. The
automated test then puts the board back: the Main Admin revokes the badge and
the student deletes the certificate, both through the API. No screen revokes a
badge, so by hand the Main Admin calls
`POST /api/mentor/students/<student id>/badges/TECH-SQL/revoke` (from the API
docs at `/docs`, for example), and the student deletes the file on
`/student/uploads`.

---

## TC-409 — Request changes on a skill claim, which needs a note

| Field | Value |
|---|---|
| ID | TC-409 |
| Module | Faculty: verifications |
| Priority | P1 |
| Type | Functional, negative and positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-409` |

### Pre-conditions

1. As TC-408, with a claim titled `E2E SQL changes <run id>`.

### Test data

| Field | Value |
|---|---|
| Badge | SQL |
| Evidence title | `E2E SQL changes <run id>` |
| Note to the student | `E2E changes <run id>: the certificate has no date` |

### Steps

1. Open `/mentor/verifications`.
2. On the "SQL" claim, click Review evidence.
3. Leave "Note to the student" empty and click Request changes.
4. Enter the note in "Note to the student".
5. Click Request changes.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The card shows "Say what needs to change — the note is all the student is told, on screen and by email." and stays open. The claim is still waiting for review. |
| ER-2 | 5 | The claim leaves the queue, and "Recently reviewed" lists it with a "Needs changes" chip and the note in quotes. |
| ER-3 | 5 | For the student, the claim reads as needing more information, with the note, and the certificate on their Uploads screen reads "Needs changes": the decision is written onto it too. The badge is not earned. |
| ER-4 | 5 | **Manual only.** On a server with mail configured, the student is emailed "Your SQL badge claim needs changes", with the note under "What to change:". |

### Post-conditions

The claim stays on record as needing changes. The automated test deletes the
student's certificate afterwards. The student's "SQL" tile keeps a live row,
since only an earned badge can be revoked.

---

## TC-410 — Reject a skill claim, which needs a reason

| Field | Value |
|---|---|
| ID | TC-410 |
| Module | Faculty: verifications |
| Priority | P1 |
| Type | Functional, negative and positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-410` |

### Pre-conditions

1. As TC-408, with a claim titled `E2E SQL reject <run id>`.

### Test data

| Field | Value |
|---|---|
| Badge | SQL |
| Evidence title | `E2E SQL reject <run id>` |
| Note to the student | `E2E rejected <run id>: the certificate is in another name` |

### Steps

1. Open `/mentor/verifications`.
2. On the "SQL" claim, click Review evidence.
3. Leave "Note to the student" empty and click Reject.
4. Enter the note in "Note to the student".
5. Click Reject.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The card shows "Say why it is rejected — the reason is all the student is told, on screen and by email." and stays open. The claim is still waiting for review. |
| ER-2 | 5 | The claim leaves the queue, and "Recently reviewed" lists it with a "Rejected" chip and the reason in quotes. |
| ER-3 | 5 | For the student, the claim reads as rejected with the reason, and the certificate on their Uploads screen reads Rejected. The badge is not earned. |
| ER-4 | 5 | **Manual only.** On a server with mail configured, the student is emailed "Your SQL badge claim was not verified", with the reason. |

### Post-conditions

As TC-409.

---

## TC-411 — Verify an uploaded document

| Field | Value |
|---|---|
| ID | TC-411 |
| Module | Faculty: verifications |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-411` |

### Pre-conditions

1. As TC-400, and the faculty member holds "Verify skills & evidence" for the
   seeded college. The dev seed grants it as the placement office would in Who
   can do what; mentoring the student does not open this screen.
2. The student has uploaded a document that no skill claim stands on. Sign in
   as the student on `/student/uploads`, choose the Document type "Other
   document", and upload a copy of `tests/fixtures/sample.pdf` renamed to the
   title in the test data: that screen titles an upload with its file name,
   so by hand the title ends in `.pdf`. The automated test uploads it through
   the API, as the student, with the title below and a file name unique to the
   run.

### Test data

| Field | Value |
|---|---|
| Document title | `E2E offer letter <run id>` |
| File | `tests/fixtures/sample.pdf` (the automated test names it `e2e-doc-verify-<run id>.pdf`) |

### Steps

1. Open `/mentor/verifications`.
2. Under "Documents", find the document by its title.
3. Click its "Open <file name>" link.
4. Click Verify on it, leaving "Reviewer note" empty.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The card reads "E2E offer letter <run id> · Document", "Test Student · uploaded" with the date and time, a "Pending review" chip, and an "Open <file name>" link. |
| ER-2 | 3 | The browser downloads the document under its file name. |
| ER-3 | 4 | The card leaves the Documents queue. A note is optional for Verify. |
| ER-4 | 4 | On the student's Uploads screen the document reads Verified. |

### Post-conditions

The automated test deletes the student's document afterwards. By hand, delete
it on `/student/uploads`.

---

## TC-412 — Reject an uploaded document, which needs a note

| Field | Value |
|---|---|
| ID | TC-412 |
| Module | Faculty: verifications |
| Priority | P2 |
| Type | Functional, negative and positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-412` |

### Pre-conditions

1. As TC-411, with a document titled `E2E report <run id>`.

### Test data

| Field | Value |
|---|---|
| Document title | `E2E report <run id>` |
| Reviewer note | `E2E rejected <run id>: this is not your report` |

### Steps

1. Open `/mentor/verifications`.
2. Under "Documents", find the document by its title.
3. Click Reject on it, leaving "Reviewer note" empty.
4. Enter the note in its "Reviewer note" field.
5. Click Reject on it.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The card shows "Say what is wrong with it — this note is all the student is shown." and stays in the queue. |
| ER-2 | 5 | The card leaves the Documents queue. |
| ER-3 | 5 | On the student's Uploads screen the document reads Rejected, with "Reviewer:" and the note. |

### Post-conditions

As TC-411.

---

## TC-413 — Upload, view and remove an Upskilling certificate

| Field | Value |
|---|---|
| ID | TC-413 |
| Module | Faculty: upskilling |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-413` |

### Pre-conditions

1. As TC-400.
2. The faculty member has fewer than 20 certificates on the shelf, the
   per-account limit.

### Test data

| Field | Value |
|---|---|
| Course / certificate name | `E2E Power BI <run id>` |
| Provider | `E2E Academy` |
| Completed on | `2026-08-14` |
| File | `tests/fixtures/sample.pdf` (1,411 bytes) |

### Steps

1. Open `/mentor/upskilling`.
2. Enter the name in "Course / certificate name", the provider in "Provider", and the date in "Completed on".
3. Click "Choose file & upload" and choose the file.
4. On the new row, click View.
5. On the same row, click Remove and accept the confirmation.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Upskilling", with "Certificates for courses you have completed — your own record." under it, an "Upload a certificate" card that says "Accepted: PDF, PNG, JPEG · up to 10 MB", and a "Your certificates" table with the columns Certificate, Provider, Completed and File. |
| ER-2 | 3 | An "Uploaded" chip shows, the name and provider fields are emptied, and the table gains a row: the name with "Uploaded <today>", "E2E Academy", "14 Aug 2026" and "sample.pdf · 1.4 KB". The "on file" count goes up by one. |
| ER-3 | 4 | The browser downloads the certificate as `sample.pdf`. |
| ER-4 | 5 | The browser asks `Remove "E2E Power BI <run id>"? This permanently deletes the certificate.` After it is accepted, the row leaves the table and the count goes back down. |

---

## TC-414 — Upskilling asks for the certificate's name before the file

| Field | Value |
|---|---|
| ID | TC-414 |
| Module | Faculty: upskilling |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-414` |

### Pre-conditions

1. As TC-400.

### Test data

| Field | Value |
|---|---|
| Course / certificate name | left empty |

### Steps

1. Open `/mentor/upskilling`.
2. Leave "Course / certificate name" empty and click "Choose file & upload".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A "Name the certificate first" warning shows beside the button, and no file picker opens. |

---

## TC-415 — Upskilling refuses a file that is not a PDF, PNG or JPEG

| Field | Value |
|---|---|
| ID | TC-415 |
| Module | Faculty: upskilling |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-415` |

### Pre-conditions

1. As TC-400.

### Test data

| Field | Value |
|---|---|
| Course / certificate name | `E2E wrong type <run id>` |
| File | any plain-text file, for example `notes.txt`. The file picker lists only PDF, PNG and JPEG files, so switch it to show all files. The server decides the type from the file's content, not its name. |

### Steps

1. Open `/mentor/upskilling`.
2. Enter the name in "Course / certificate name".
3. Click "Choose file & upload" and choose the text file.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The card shows "Upload failed. Unsupported file type — only PDF, PNG and JPEG are accepted." No row with the name is added, and the name stays in its field. |

---

## TC-416 — Upload, replace and remove the signature image

| Field | Value |
|---|---|
| ID | TC-416 |
| Module | Faculty: signature |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-416` |

### Pre-conditions

1. As TC-400.
2. No signature image is on file for the faculty member. The dev seed adds
   none. If one is on file, note it, remove it first and upload it again when
   the case is done. The automated test does that through the API.

### Test data

| Field | Value |
|---|---|
| First image | `tests/fixtures/sample.png` |
| Replacement | `tests/fixtures/sample.jpg` |

### Steps

1. Open the account menu (Test Mentor, top right) and choose Signature.
2. Click "Upload signature" and choose the first image.
3. Click Replace and choose the replacement image.
4. Click "Remove…".
5. Click "Yes, remove it".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page `/mentor/signature` is headed "Signature" and shows "No signature on file", the advice "PNG or JPEG, under 2 MB", and an "Upload signature" button. |
| ER-2 | 2 | "Signature saved. It appears on your leave papers from now on." is shown, with a preview of the image and "On file since <date and time> · 1 KB · PNG". Replace and "Remove…" are offered. |
| ER-3 | 3 | The same saved message shows and the preview changes to the new picture. The line under it still says PNG: the server stores every signature as a PNG. |
| ER-4 | 4 | "Your papers will show your name and the time only." is shown with "Yes, remove it" and "Keep it". Nothing is removed yet. |
| ER-5 | 5 | "Signature removed. Your leave papers show your name and the time only." is shown, and the page is back to "No signature on file". |

### Post-conditions

No signature image is on file, as before the case.

---

## TC-417 — The signature refuses a file that is not an image

| Field | Value |
|---|---|
| ID | TC-417 |
| Module | Faculty: signature |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-417` |

### Pre-conditions

1. As TC-416: no signature image is on file.

### Test data

| Field | Value |
|---|---|
| File | `tests/fixtures/sample.pdf`. Switch the file picker to show all files. |

### Steps

1. Open `/mentor/signature`.
2. Click "Upload signature" and choose the PDF.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The page shows the error "A signature is an image: upload a PNG or a JPEG." and still reads "No signature on file". |

---

## TC-418 — Submit a leave request on the official form

| Field | Value |
|---|---|
| ID | TC-418 |
| Module | Faculty: leave |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-418` |

### Pre-conditions

1. As TC-400.
2. The office has recorded no leave allowance for the faculty member (the dev
   seed records none). With one, a request past the allowance, or one
   overlapping an earlier request, is refused.

### Test data

| Field | Value |
|---|---|
| Kind | Permission |
| From date / To date | any two days in the future no other request of this account covers. The automated test picks a span after 2031 that is new for each run. |
| Purpose | `E2E leave <run id>: attending a workshop` |
| Credit | `2 days` |
| Alternate arrangement, first row | Date: the from date. Staff name `E2E Colleague`, Class `MBA 1`, Time `10:00`, Remarks `Covering` |

### Steps

1. Open `/mentor/leave`.
2. Click New leave request.
3. Click Permission on the "Application for" line.
4. Enter the from date and the to date, the purpose, the credit, and the first row of the Alternate Arrangements table.
5. Click "Sign & submit to Program Director".
6. Click the back arrow ("Back to requests").

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Leave requests", with "The official BGSCET leave form — sign and send to Admin / Program Director." and a New leave request button. The "Your leave allowance" card names the academic year and says "The office has recorded no allowance for you this year, so no request of yours is measured against one. That is not a balance of zero." |
| ER-2 | 2 | The college's form is shown: "\|\| Jai Sri Gurudev \|\|", "BGS COLLEGE OF ENGINEERING AND TECHNOLOGY,MBA", Name "Test Mentor (synced)", Sanctioned "Pending", "SIGNATURE OF STAFF" and "PROGRAM DIRECTOR". "Sign & submit to Program Director" is disabled, with "Dates and a purpose are needed before this can be signed." |
| ER-3 | 3 | Permission is the one option not struck off; Casual Leave, OOD, RH and LOP are struck off. |
| ER-4 | 4 | "Sign & submit to Program Director" becomes enabled and the hint goes. |
| ER-5 | 5 | The signed request is shown read-only: the date span ("<from> — <to>"), the purpose, "2 days", Sanctioned "Pending", "Test Mentor" and the signing time above "SIGNATURE OF STAFF", "Awaiting" above "PROGRAM DIRECTOR", the alternate-arrangement row, and a Download PDF link. |
| ER-6 | 6 | The request is listed as "Permission", "Applied <today>", its date span, with the chip "Awaiting the Main Admin". |

### Post-conditions

The request waits in the Main Admin's leave queue. The automated test
withdraws it afterwards through the API. By hand, withdraw it as in TC-420.

---

## TC-419 — A leave request whose dates run backwards is refused

| Field | Value |
|---|---|
| ID | TC-419 |
| Module | Faculty: leave |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-419` |

### Pre-conditions

1. As TC-418.

### Test data

| Field | Value |
|---|---|
| From date | a date later than the to date, for example `2031-12-20` |
| To date | for example `2031-12-10`. The automated test picks a new pair each run. |
| Purpose | `E2E backwards <run id>` |

### Steps

1. Open `/mentor/leave`.
2. Click New leave request.
3. Enter the from date, the earlier to date, and the purpose.
4. Click "Sign & submit to Program Director".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The form shows "The last day of leave cannot fall before the first day. You asked for <from> to <to>." with the two dates, and stays open with the purpose still filled in. |
| ER-2 | 4 | No request is created: the account's requests include none for those dates. |

---

## TC-420 — Withdraw a leave request that is awaiting a decision

| Field | Value |
|---|---|
| ID | TC-420 |
| Module | Faculty: leave |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-420` |

### Pre-conditions

1. As TC-418.
2. The faculty member has a Casual Leave request awaiting the Main Admin,
   for dates no other request of theirs covers. Submit one as in TC-418. The
   automated test submits it through the API.

### Test data

| Field | Value |
|---|---|
| Kind | Casual Leave |
| Purpose | `E2E withdraw <run id>` |

### Steps

1. Open `/mentor/leave`.
2. Click the request, identified by its dates.
3. Click Withdraw this request.
4. Click "Yes, withdraw it".
5. Click the back arrow ("Back to requests").

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The request is shown with the purpose, Sanctioned "Pending", and a Withdraw this request button beside "Only while it is still awaiting a signature. Once it is decided, the decision stands." |
| ER-2 | 3 | "This takes the request back. It cannot be undone — a new form is how you ask again." is shown with "Yes, withdraw it" and "Keep it". |
| ER-3 | 4 | Sanctioned reads "Cancelled" and the withdraw controls are gone. |
| ER-4 | 5 | The request is listed with the chip "Cancelled". |

### Post-conditions

The request stays on record as cancelled. It cannot be reopened.

---

## TC-421 — Download a leave request's paper as a PDF

| Field | Value |
|---|---|
| ID | TC-421 |
| Module | Faculty: leave |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-421` |

### Pre-conditions

1. As TC-420, with an RH request.

### Test data

| Field | Value |
|---|---|
| Kind | RH |
| Purpose | `E2E paper <run id>` |

### Steps

1. Open `/mentor/leave`.
2. Click the request, identified by its dates.
3. Click Download PDF.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The browser downloads a PDF named `leave-test-mentor-<from date>.pdf`. |
| ER-2 | 3 | **Manual only.** The PDF is the college's own one-page leave form with the request written onto it: RH is the option not struck off, and the dates, the purpose, "Test Mentor" and the signing time are filled in. The PROGRAM DIRECTOR block is empty. |

### Post-conditions

The automated test withdraws the request afterwards through the API.

---

## TC-422 — Save a leave request as a draft on this device, and discard it

| Field | Value |
|---|---|
| ID | TC-422 |
| Module | Faculty: leave |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-422` |

### Pre-conditions

1. As TC-400.
2. This browser holds no leave draft. A draft lives in the browser's local
   storage, one per browser, and never reaches the server.

### Test data

| Field | Value |
|---|---|
| Kind | OOD |
| From date / To date | `2031-03-02` / `2031-03-04` |
| Purpose | `E2E draft <run id>` |

### Steps

1. Open `/mentor/leave`.
2. Click New leave request.
3. Click OOD, and enter the dates and the purpose.
4. Click Save draft.
5. Click the draft card.
6. Click Discard draft and accept the confirmation.
7. Click the back arrow ("Back to requests").

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The list is shown again with "Draft saved on this device." and a card reading "OOD", "Draft · saved on this device", "2031-03-02 — 2031-03-04" and the time it was saved, with a "Draft" chip. |
| ER-2 | 5 | The form reopens with OOD chosen and the dates and purpose restored, and a Discard draft button. |
| ER-3 | 6 | The browser asks "Discard the draft saved on this device?" After it is accepted, the form is emptied and Discard draft is gone. |
| ER-4 | 7 | No draft card is listed. No request was sent to the server at any point. |

---

## TC-423 — Ask the REEP Agent a question as a faculty member

| Field | Value |
|---|---|
| ID | TC-423 |
| Module | Faculty: REEP Agent |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-423` |

### Pre-conditions

1. As TC-400.
2. The faculty member's agent conversation is empty. If it is not, click
   Clear conversation first. The automated test clears it through the API.
3. No LLM provider is configured, the development default, so the agent
   answers with the Knowledge Base's approved text, word for word. With a model
   configured, the wording of the answers may differ from ER-2 and ER-3.

### Test data

| Field | Value |
|---|---|
| Suggested question | "How do I verify a skill?" |
| Typed question | `Am I placement-ready?` |

### Steps

1. Open `/mentor/agent`.
2. Click the suggestion "How do I verify a skill?".
3. Type the question in the "Message the REEP Agent" box and click Send.
4. Click Clear conversation.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "REEP Agent" and carries the staff disclaimer: "The REEP Agent is a general helper — it answers on programme rules, deadlines and how REEP works, and it does not read student records. Personalised answers are only available on student accounts." The thread shows "How can I help today?" with four suggestions, and Clear conversation is disabled. |
| ER-2 | 2 | The question is shown as the faculty member's message, and the agent answers how a skill is verified ("…upload a certificate or proof of completion as evidence…"), with the source chip "Source: Verifying a skill (e.g. Power BI)" and Helpful and Not helpful buttons. |
| ER-3 | 3 | The agent answers "Personalised insights — placement readiness, your next steps, eligible jobs, skills, profile and deadlines — are available on student accounts. I can still answer policy and how-to questions." with the limitation "Personalised tools are student-only." |
| ER-4 | 4 | The thread is emptied and shows "How can I help today?" again. |

---

## TC-424 — A faculty member is kept out of the admin console

| Field | Value |
|---|---|
| ID | TC-424 |
| Module | Faculty: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-424` |

### Pre-conditions

1. As TC-400.
2. The Main Admin has granted the faculty member no admin screen in
   Governance. A grant adds a "Granted access" group to the sidebar and opens
   that screen.

### Test data

None.

### Steps

1. Open `/mentor/notebook` and look at the sidebar.
2. Open `/admin` in the address bar.
3. Open `/admin/students` in the address bar.
4. Open `/api/admin/students` in the address bar.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The sidebar lists exactly Notebook, Mentee Log, Student records, Leave Requests, Skill Verifications and Upskilling (Student records because they mentor a student). There is no "Granted access" group. |
| ER-2 | 2 | The app does not open the admin console. It sends the browser to `/mentor/notebook`, headed "Faculty notebook". |
| ER-3 | 3 | Again the browser lands on `/mentor/notebook`. |
| ER-4 | 4 | The API refuses: the page shows `{"detail":"You do not hold the 'Students' capability. An administrator can grant it in Governance."}`. |

---

## TC-425 — Alumni creates their profile on first sign-in, or edits it afterwards

| Field | Value |
|---|---|
| ID | TC-425 |
| Module | Alumni: profile |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-425` |

### Pre-conditions

1. The web app and the API are running as described in "Setup" in the index,
   with a development `ENV`, and the dev seed has been applied.
2. The browser is signed in as the alumnus, `alumni@bgscet.ac.in`.
3. Which branch of the steps applies depends on the database. The dev seed
   creates the alumnus with no profile, so on a fresh database this is the
   first sign-in and the setup form is shown. Nothing in REEP deletes an
   alumni profile once saved, so on every later run the saved profile and its
   Edit form are shown instead. Both branches are expected results below.

### Test data

| Field | Value |
|---|---|
| Company | `E2E Co <run id>` |
| Designation | `Business Analyst` |
| Graduation year | `2024` |
| Resume | `tests/fixtures/sample.pdf` (1,411 bytes) |

### Steps

1. Open `/alumni`.
2. If the page shows the saved profile rather than the setup form, click Edit profile.
3. Enter the company in "Company you currently work in", the designation in "Designation" and the year in "Graduation year".
4. If the save button reads "Create my profile", click it before choosing a resume.
5. Click the resume button ("Upload current resume *", or "Replace resume" on a saved profile) and choose the resume file.
6. Click the save button ("Create my profile", or "Save changes" on a saved profile).
7. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "My Profile", with "Where you work now, and the resume the placement office can share." On a fresh database the setup form is shown: "Welcome back, Test Alumnus!", "Set up your alumni profile — where you currently work and your current resume.", the fields, "Upload current resume *", "PDF preferred · up to 10 MB" and "Create my profile", with no Cancel. Once a profile exists, the saved profile is shown instead: "Test Alumnus", `alumni@bgscet.ac.in` and an Edit profile button. |
| ER-2 | 2 | On a saved profile, the form is headed "Edit profile" and filled with the saved values, with "Replace resume", "Keeping: <file name on record>", "Save changes" and Cancel. |
| ER-3 | 4 | On the first sign-in only, the form shows "Upload your current resume to create your profile." and nothing is saved. A saved profile skips this step: its resume is optional and is kept when none is chosen. |
| ER-4 | 5 | A chip names the chosen file, "sample.pdf". |
| ER-5 | 6 | The profile is shown with a "Saved" chip: Company "E2E Co <run id>", Designation "Business Analyst", Graduated "2024", and the resume "sample.pdf · 1.4 KB". An Edit profile button is offered. |
| ER-6 | 7 | The same profile is shown after the reload, not the setup form. |

### Post-conditions

The alumnus has a saved profile with the test data and the resume. The setup
form is not seen again on this database.

---

## TC-426 — The alumni profile is not saved without a company

| Field | Value |
|---|---|
| ID | TC-426 |
| Module | Alumni: profile |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-426` |

### Pre-conditions

1. As TC-425. Either branch applies: on the setup form the company field
   starts empty; on a saved profile it is cleared in step 3.

### Test data

| Field | Value |
|---|---|
| Company | left empty |

### Steps

1. Open `/alumni`.
2. If the page shows the saved profile rather than the setup form, click Edit profile.
3. Clear the "Company you currently work in" field.
4. Click the save button ("Create my profile", or "Save changes" on a saved profile).
5. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The form shows "Tell us the company you currently work in." and stays open. |
| ER-2 | 5 | Nothing was saved: a fresh database shows the setup form again, and a saved profile still shows the company it had before the case. |

---

## TC-427 — Alumni downloads their resume

| Field | Value |
|---|---|
| ID | TC-427 |
| Module | Alumni: profile |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-427` |

### Pre-conditions

1. As TC-425, and the alumnus has a saved profile with a resume. Run TC-425
   first on a fresh database. The automated test creates the profile through
   the API when there is none.

### Test data

None: the resume on record is used.

### Steps

1. Open `/alumni`.
2. In the Resume row, click the resume.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The profile's Resume row shows the file name on record and its size. |
| ER-2 | 2 | The browser downloads the resume under the file name shown. |

---

## TC-428 — Alumni jobs sheet lists open postings without match or eligibility

| Field | Value |
|---|---|
| ID | TC-428 |
| Module | Alumni: jobs |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-428` |

### Pre-conditions

1. As TC-425 (signed in as the alumnus).
2. The dev seed's three job postings are open and unchanged: Financial
   Analyst (Acme Capital, PG, Bengaluru, skills excel and financial-modeling,
   with an apply link), BI Developer (DataWorks, PG, Remote, skills power-bi
   and excel, with an apply link) and Junior Accountant (LedgerCo, UG, Mysuru,
   skill excel, no apply link). None has a closing date.

### Test data

None beyond the seeded postings.

### Steps

1. Open the sidebar's Jobs Sheet item.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page `/alumni/jobs` is headed "Jobs Sheet", with "Openings from the placement office and the daily job agent." and a "Search role, company or location" box. |
| ER-2 | 1 | The table's columns are Role, Company, Level, Location, Skills, Closes and an unlabelled action column. There is no Eligibility or Skill match column and no match percentage anywhere: those are computed from a student's marks and skills, which an alumnus does not have. |
| ER-3 | 1 | The three seeded postings are listed with their company, level, location and skill chips, and "—" under Closes. Financial Analyst and BI Developer each have an Apply link to the employer's page; Junior Accountant has none. |

---

## TC-429 — Search the alumni jobs sheet

| Field | Value |
|---|---|
| ID | TC-429 |
| Module | Alumni: jobs |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/05-faculty-alumni.spec.ts`, title tagged `@TC-429` |

### Pre-conditions

1. As TC-428.

### Test data

| Field | Value |
|---|---|
| Company search | `ledgerco` |
| Location search | `Remote` |
| A search that matches nothing | `zzz-no-such-job` |

### Steps

1. Open `/alumni/jobs`.
2. Enter `ledgerco` in the "Search role, company or location" box.
3. Replace the search with `Remote`.
4. Replace the search with `zzz-no-such-job`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Only Junior Accountant is listed. The search ignores case. |
| ER-2 | 3 | Only BI Developer is listed: the search matches the location too. |
| ER-3 | 4 | The table shows "No opening matches that search." |

---

## TC-430 — Skill-claim emails reach the faculty member and the student

| Field | Value |
|---|---|
| ID | TC-430 |
| Module | Faculty: verifications |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): it needs real mail delivery, and a development server with no mail transport sends none by design. |

### Pre-conditions

1. A server with mail configured: `SES_FROM_ADDRESS` set on a deployment with
   SES access, or `BADGE_MAIL_ENABLED=true` on a development server, which
   writes each message to the API console instead of sending it.
2. The faculty member's and the student's mailboxes can be read, or the API
   console can.

### Test data

| Field | Value |
|---|---|
| Badge | SQL |
| Evidence title | `Manual mail check <date>` |

### Steps

1. Sign in as the student, and on `/student/skilling` claim the "SQL" badge with a certificate.
2. Sign in as the faculty member, and on `/mentor/verifications` click Verify on that claim.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | **Manual only.** The faculty member receives one email, "Test Student has claimed the SQL badge". Filing the claim again with the same request id does not send a second one. |
| ER-2 | 2 | **Manual only.** The student receives "Your SQL badge is verified", saying the badge is now marked verified on their Skilling board, with a link to `/student/skilling`. No USN, marks or certificate is in either email. |

### Post-conditions

The student holds the "SQL" badge. The Main Admin can revoke it through the
API, as in TC-408's post-conditions.

---

## TC-431 — The leave paper prints the faculty member's signature image

| Field | Value |
|---|---|
| ID | TC-431 |
| Module | Faculty: leave |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | None (manual only): whether the image is drawn in the right block of the PDF can only be judged by looking at the page. |

### Pre-conditions

1. As TC-400.
2. The faculty member has a leave request they submitted, and no signature
   image on file.

### Test data

| Field | Value |
|---|---|
| Signature image | `tests/fixtures/sample.png` |

### Steps

1. On `/mentor/leave`, open the request and click Download PDF.
2. On `/mentor/signature`, upload the signature image.
3. On `/mentor/leave`, open the same request and click Download PDF again.
4. On `/mentor/signature`, remove the signature image.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | **Manual only.** The paper shows "Test Mentor" and the signing time in the two staff signature blocks, with no picture. |
| ER-2 | 2 | **Manual only.** On `/mentor/leave`, a New leave request form now says "Your signature image is on file and prints above your name on the paper." instead of "No signature image is on file…". |
| ER-3 | 3 | **Manual only.** The new paper draws the image above the name and time in both staff signature blocks. The form itself is unchanged. |
| ER-4 | 4 | **Manual only.** A paper downloaded now is back to the name and time only: the paper reads whatever is on file when it is downloaded. |

### Post-conditions

No signature image is on file.
