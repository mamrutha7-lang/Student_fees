# Admin: people and access

| Field | Value |
|---|---|
| Screens | `/admin`, `/admin/students`, `/admin/students/:id`, `/admin/faculty`, `/admin/faculty/new`, `/admin/mentors`, `/admin/governance`, `/admin/governance/features`, `/admin/audit` |
| Automated tests | [`tests/06-admin-people.spec.ts`](../../tests/06-admin-people.spec.ts) |
| ID range | TC-500 to TC-599 |

Setup, the seeded accounts and the rules that link these cases to their
automated tests are in [the index](../manual-test-cases.md).

Every case below starts from the same place unless its own pre-conditions say
otherwise: the web app and the API are running as described in "Setup" in the
index, with a development `ENV` (no mail transport, so links and codes are
printed to the API console instead of being emailed), the dev seed has been
applied, and you are signed in as the Main Admin, `admin@bgscet.ac.in`. Use a
browser window about 1920 pixels wide, so that every column of the roster and
faculty grids is on screen; in a narrower window, scroll a grid sideways to
read the columns on its right.

Several cases need a throwaway faculty member or student of their own, so that
nothing seeded is disabled, removed or deleted:

- **A new faculty member**: on `/admin/faculty/new`, file one under
  "MGMT · Department of Management Studies" with a unique name and a unique
  `@bgscet.ac.in` address (TC-517 walks through it), and keep the activation
  link it shows.
- **An activated faculty member**: a new faculty member whose activation link
  has been opened in a private window and a password of at least 12
  characters set on it (TC-520 walks through it).
- **A new student**: submit `/register` with a unique name, a unique
  `@bgscet.ac.in` address and a USN that is not of the form `1BG2?MBA???`, so
  that no rule seats them in a batch, then approve the application on
  `/admin/registrations`. A new student has never signed in, so their Status
  reads "Invited"; they start at stage Excel, in semester 1.
- **A new batch**: on College structure (`/admin/institution`), add a batch
  under Department of Management Studies, course Master of Business
  Administration, with a unique name, running from 1 Jul 2026 to 30 Jun 2028.
  Move the students a case names into it with the pencil on the roster (Batch).
  Other test runs leave students in the seeded batch, and a batch action
  writes to every student on the roster in that batch, so the cases about a
  whole batch use a batch of their own. When a
  case moves Test Student into it, move them back to "Master of Business
  Administration - Finance · 2024-26 Section B" afterwards, in semester 2 at
  stage Excel-Adv, then delete the empty batch ("Batch actions", "Remove this
  empty batch…").

Other test modules leave data behind (more students, removed accounts, more
colleges and departments), so the cases below name the seeded records they
check and give counts as they read on a freshly seeded database. Where a count
on screen depends on what else exists, the case says so.

The automated tests make these through the API, with names unique to the run,
and remove them again when the case ends. Removed accounts stay on the Removed
lists, record kept: deleting for good needs a code emailed to the office, which
no automated test reads.

---

## TC-500 — The Main Admin home shows the waiting counts and every task

| Field | Value |
|---|---|
| ID | TC-500 |
| Module | Admin: home |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-500` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

None.

### Steps

1. Open `/admin`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "What do you want to do?" and has a "Find a task…" box. |
| ER-2 | 1 | Under "Waiting for you" there are five buttons, each with a number: "New student applications", "Leave requests", "Students without a faculty member", "Job offers to approve" and "Access requests to review". |
| ER-3 | 1 | Each number is how many items wait on the screen the button opens. On a freshly seeded database they read 1 (Ravi Kumar's application), 0, 0, 0 and 0. A number that could not be read shows a dash, never a 0. |
| ER-4 | 1 | The tasks are buttons in six groups: Students ("Approve new students", "Find a student", "Assign faculty to students", "Upload marks & attendance"), Faculty ("Add a faculty member", "See all faculty", "Approve leave"), Jobs ("Post a job", "Approve job offers"), Interviews ("Edit interview questions", "See interview records", "Write SWOC notes"), Reports ("See charts & numbers", "Download a report") and Setup ("Set up a college", "Colleges", "Departments, courses & batches", "Subjects & certificates", "Decide who can do what", "See what changed", "Ask REEP"). |

---

## TC-501 — A waiting count and a task button open their screens

| Field | Value |
|---|---|
| ID | TC-501 |
| Module | Admin: home |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-501` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

None.

### Steps

1. Open `/admin`.
2. Click the "New student applications" count.
3. Open `/admin` again.
4. Click "Find a student".
5. Open `/admin` again.
6. Click "Decide who can do what".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser opens `/admin/registrations`, headed "New applications". |
| ER-2 | 4 | The browser opens `/admin/students`, headed "Students & batches". |
| ER-3 | 6 | The browser opens `/admin/governance`, headed "Who can do what". |

---

## TC-502 — Finding a task narrows the buttons

| Field | Value |
|---|---|
| ID | TC-502 |
| Module | Admin: home |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-502` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

| Field | Value |
|---|---|
| A word that matches one task | `audit` |
| A word that matches no task | `zzqx` |

### Steps

1. Open `/admin`.
2. Type `audit` into "Find a task".
3. Replace the text with `zzqx`.
4. Clear the box.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | One task button is left, "See what changed" (it also answers to "audit"), and the "Waiting for you" counts are hidden. |
| ER-2 | 3 | No task button is left, and the page says "Nothing called “zzqx”." |
| ER-3 | 4 | The "Waiting for you" counts and every task button are back. |

---

## TC-503 — The roster lists the seeded student with their details

| Field | Value |
|---|---|
| ID | TC-503 |
| Module | Admin: students roster |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-503` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The seeded student, `student@bgscet.ac.in`, has signed in at least once.
   Until they do, their Status reads "Invited", not "Active".

### Test data

| Field | Value |
|---|---|
| Student | Test Student, `student@bgscet.ac.in`, USN `1BG24MBA001` |

### Steps

1. Open `/admin/students`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Students & batches". Test Student's row reads: USN "1BG24MBA001"; Student "Test Student" with "student@bgscet.ac.in" under it; Spec. "FIN"; Sem "2"; Stage "Excel-Adv"; Faculty "Test Mentor"; Status "Active". |
| ER-2 | 1 | There is no "Add student" control. A link, "Students arrive from Registrations", leads to `/admin/registrations`. |

---

## TC-504 — Searching the roster by USN and by name

| Field | Value |
|---|---|
| ID | TC-504 |
| Module | Admin: students roster |
| Priority | P2 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-504` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

| Field | Value |
|---|---|
| A USN | `1BG24MBA001` |
| Text that matches nobody | `no-such-student-zz` |
| A name | `Test Student` |

### Steps

1. Open `/admin/students`.
2. Type `1BG24MBA001` into "Quick filter…".
3. Replace the text with `no-such-student-zz`.
4. Replace the text with `Test Student`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | One row is left, Test Student's, and the bar under the grid reads "Rows: 1". |
| ER-2 | 3 | The grid is empty and says "No student matches “no-such-student-zz”.", and the bar reads "Rows: 0". |
| ER-3 | 4 | Test Student's row is listed again ("Rows: 1"). |

---

## TC-505 — Searching the roster by college email finds the student

| Field | Value |
|---|---|
| ID | TC-505 |
| Module | Admin: students roster |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-505` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

| Field | Value |
|---|---|
| An address | `student@bgscet.ac.in` |

### Steps

1. Open `/admin/students`.
2. Type `student@bgscet.ac.in` into "Quick filter…".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Test Student's row is listed and the bar reads "Rows: 1". The box is labelled for screen readers as "Search students by name, email or USN", and the line under the heading counts "1 student in view". |

---

## TC-506 — Filtering the roster by batch and status

| Field | Value |
|---|---|
| ID | TC-506 |
| Module | Admin: students roster |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-506` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The seeded student has signed in at least once (see TC-503).

### Test data

| Field | Value |
|---|---|
| Batch | Master of Business Administration - Finance · 2024-26 Section B, which ended on 31 Jul 2026 |

### Steps

1. Open `/admin/students`.
2. Under "Batch", choose "2024-26 Section B (ended)".
3. Under "Status", choose "Invited · not signed in yet".
4. Under "Status", choose "All".
5. Under "Batch", choose "No batch yet".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | Test Student is listed. The card beside the grid is headed "Batch · 2024-26 Section B" and reads Course "Master of Business Administration · PG", Specialization "Finance", Department "Department of Management Studies" and Term "Ended · 2024-26". |
| ER-2 | 3 | Test Student is hidden, because they have signed in. Any student still listed reads "Invited" (on a freshly seeded database none is left, and the bar reads "Rows: 0"). |
| ER-3 | 4 | Test Student is listed again. |
| ER-4 | 5 | The line under the heading ends "… with no batch yet · N seated with a faculty member", and Test Student, who is in a batch, is not listed. On a freshly seeded database the grid says "Every student is in a batch." |

---

## TC-507 — A batch whose students are all filtered out is not called empty

| Field | Value |
|---|---|
| ID | TC-507 |
| Module | Admin: students roster |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-507` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student, who has signed in at least once (see TC-503), has been moved
   into a new batch (see the top of this file) and is its only student.

### Test data

| Field | Value |
|---|---|
| Batch | The new batch |

### Steps

1. Open `/admin/students`.
2. Under "Batch", choose the new batch.
3. Under "Status", choose "Invited · not signed in yet".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | Test Student, who has signed in, is hidden, and the grid says "No student matches these filters.", not that the batch is empty. |

### Post-conditions

Move Test Student back to the seeded batch and delete the empty batch (see the
top of this file). The automated test does this through the API.

---

## TC-508 — Editing a student and putting the change back

| Field | Value |
|---|---|
| ID | TC-508 |
| Module | Admin: students roster |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-508` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student is at stage Excel-Adv, in semester 2, with Test Mentor.

### Test data

| Field | Value |
|---|---|
| Stage to set | Elevate |
| Stage to put back | Excel-Adv |

### Steps

1. Open `/admin/students`.
2. Click the pencil on Test Student's row.
3. Under "Stage", choose "Elevate".
4. Click Save.
5. Click the pencil on Test Student's row again.
6. Under "Stage", choose "Excel-Adv".
7. Click Save.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A dialog "Edit Test Student" opens, saying "Nothing here sets a password.", filled in with Name "Test Student", College email "student@bgscet.ac.in", USN "1BG24MBA001", Faculty member "Test Mentor", Stage "Excel-Adv" and Semester "2". |
| ER-2 | 4 | The dialog closes, the screen says "Saved." and the Stage column reads "Elevate". |
| ER-3 | 5 | The dialog opens with Stage "Elevate". |
| ER-4 | 7 | The dialog closes, the screen says "Saved." and the Stage column reads "Excel-Adv" again. |

### Post-conditions

Test Student is back at Excel-Adv. The audit trail has two `student` events
with the action `UPDATE`, one for each save.

---

## TC-509 — Editing a student refuses an address off the college domain

| Field | Value |
|---|---|
| ID | TC-509 |
| Module | Admin: students roster |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-509` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The seeded college has no email domains of its own, so the deployment's
   list applies, which on a development server is `bgscet.ac.in`.

### Test data

| Field | Value |
|---|---|
| Address off the college domain | `test.student@example.com` |

### Steps

1. Open `/admin/students`.
2. Click the pencil on Test Student's row.
3. Replace the "College email" with `test.student@example.com`.
4. Click Save.
5. Click Cancel.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The save is refused: "test.student@example.com is not on a college domain (bgscet.ac.in). A student account is a sign-in, and only a college address gets one." The dialog stays open. |
| ER-2 | 5 | The dialog closes and the row still shows "student@bgscet.ac.in". |

---

## TC-510 — A batch action sets the semester and the stage for the whole batch

| Field | Value |
|---|---|
| ID | TC-510 |
| Module | Admin: students roster, batch actions |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-510` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student has been moved into a new batch (see the top of this file)
   and is its only student. They are in semester 2, at stage Excel-Adv, as
   seeded.

### Test data

| Field | Value |
|---|---|
| Batch | The new batch |
| Semester to set | 3 |
| Stage to set | Elevate |

### Steps

1. Open `/admin/students`.
2. Under "Batch", choose the new batch.
3. Click "Batch actions".
4. Under "Set semester", choose `3` and click the Set button beside it.
5. Click "Batch actions" again.
6. Under "Set stage", choose "Elevate" and click the Set button beside it.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A dialog "Batch actions · <the new batch>" opens, saying "Every action here touches all 1 student in this batch — the filters above do not narrow it." |
| ER-2 | 4 | The dialog closes, the screen says "1 student: set to semester 3." and Test Student's Sem column reads 3. |
| ER-3 | 5 | The dialog opens again. |
| ER-4 | 6 | The screen says "1 student: set to Elevate." and Test Student's Stage column reads Elevate. |

### Post-conditions

The audit trail has a `cohort` event for the batch with the action
`STUDENTS_SEMESTER` and one with `STUDENTS_STAGE`. Put Test Student back in
the seeded batch, in semester 2 at stage Excel-Adv, and delete the empty batch
(see the top of this file). The automated test does this through the API.

---

## TC-539 — A batch action writes only to the students its dialog counts

| Field | Value |
|---|---|
| ID | TC-539 |
| Module | Admin: students roster, batch actions |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-539` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student, in semester 2 as seeded, has been moved into a new batch
   (see the top of this file). A new student, in semester 1, has been moved
   into it too and then removed from the roster (the pencil on their row,
   "Remove or delete…", a reason, "Remove from the roster"; see TC-515), so the
   roster lists only Test Student in the batch.

### Test data

| Field | Value |
|---|---|
| Batch | The new batch |
| Semester to set | 3 |

### Steps

1. Open `/admin/students`.
2. Under "Batch", choose the new batch.
3. Click "Batch actions".
4. Under "Set semester", choose `3` and click the Set button beside it.
5. Under "Status", choose "Removed · off the roster, record kept".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The dialog says "Every action here touches all 1 student in this batch — the filters above do not narrow it." |
| ER-2 | 4 | The screen says "1 student: set to semester 3.", the one student the dialog counted. |
| ER-3 | 5 | The removed student is listed and is still in semester 1: a student off the roster is not moved by an action whose dialog did not count them. |

### Post-conditions

Test Student's Sem column reads 3. Put them back in the seeded batch, in
semester 2 at stage Excel-Adv. A removed student cannot be edited or moved by a
batch action, so restore the removed student (TC-515, steps 7 to 9), take them
out of the batch with the pencil and remove them again, then delete the empty
batch (see the top of this file). The removed student stays on the Removed
list. The automated test does this through the API.

---

## TC-511 — Assigning a faculty member to the ticked students

| Field | Value |
|---|---|
| ID | TC-511 |
| Module | Admin: students roster, selection actions |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-511` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists (see the top of this file). They have no
   students yet.
3. Test Student is with Test Mentor. On a freshly seeded database Test
   Student is Test Mentor's only student and the programme's mentor capacity
   is 20.

### Test data

| Field | Value |
|---|---|
| Faculty member | The new faculty member |

### Steps

1. Open `/admin/students`.
2. Tick Test Student's row.
3. Click "Assign faculty to 1 selected".
4. Under "Faculty member", choose the new faculty member and click "Apply to 1 student".
5. Click the pencil on Test Student's row.
6. Under "Faculty member", choose "Test Mentor" and click Save.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The toolbar button reads "Assign faculty to 1 selected" and the bar under the grid reads "Selected: 1". |
| ER-2 | 3 | A dialog "Assign a faculty member" opens, saying "1 student ticked in the grid.". Each faculty member is offered with their load: the number of students they have now "of" the capacity, as the Mentor load screen (`/admin/mentors`) counts them. On a freshly seeded database that is "Test Mentor — 1 of 20" and "<new faculty member> — 0 of 20". |
| ER-3 | 4 | The dialog closes, the screen says "1 of 1 student: assigned to <new faculty member>." and the Faculty column names the new faculty member. |
| ER-4 | 5 | The edit dialog opens with Faculty member set to the new faculty member. |
| ER-5 | 6 | The screen says "Saved." and the Faculty column reads "Test Mentor" again. |

### Post-conditions

Test Student is with Test Mentor again. Both moves are on their assignment
history, and moving them off Test Mentor gave Test Mentor a 90-day read-only
"Mentee log" grant for this student, listed on `/admin/governance`.

---

## TC-512 — After a selection action the grid's ticks match the toolbar

| Field | Value |
|---|---|
| ID | TC-512 |
| Module | Admin: students roster, selection actions |
| Priority | P3 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-512` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student is with Test Mentor.

### Test data

None.

### Steps

1. Open `/admin/students`.
2. Tick Test Student's row.
3. Click "Assign faculty to 1 selected".
4. Under "Faculty member", choose "Test Mentor", who already mentors them, and click "Apply to 1 student".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The screen says "1 of 1 student: assigned to Test Mentor.", the bar under the grid reads "Selected: 0", and Test Student's row is no longer ticked, so the tick and the count agree. |

---

## TC-513 — The View button opens the student's full record

| Field | Value |
|---|---|
| ID | TC-513 |
| Module | Admin: Student 360 |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-513` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The seeded student has signed in at least once (see TC-503).

### Test data

None.

### Steps

1. Open `/admin/students`.
2. Click the eye on Test Student's row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The browser opens `/admin/students/<the student's id>`. The summary card beside the record names "Test Student" with the USN 1BG24MBA001, an "Active" chip and an "Excel-Adv stage" chip, and reads Batch "Master of Business Administration - Finance · 2024-26 Section B", Department "Department of Management Studies", Semester "2" and Faculty mentor "Test Mentor". |
| ER-2 | 2 | The record has the tabs Overview, Resume, Skills, Leaderboard, Academics, Interviews, Time sheet, Documents and Access, in that order. |

---

## TC-514 — The full record shows the profile, results, documents and mentor history

| Field | Value |
|---|---|
| ID | TC-514 |
| Module | Admin: Student 360 |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-514` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Student is with Test Mentor. As seeded, the profile photo and the
   leadership certificate are waiting for a verdict and the CV is verified
   with the note "Looks good."; other test modules may have decided one of
   them since, so read each verdict as the record holds it (the Verifications
   screen of their faculty member shows the same).

### Test data

None.

### Steps

1. Open Test Student's full record with the eye on the roster.
2. Read the "Contact", "Profile as filled in" and "Faculty mentor" cards.
3. Click the Academics tab.
4. Click the Documents tab.
5. Click the download button on the resume_v1.pdf row.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | "Contact" reads City "Bengaluru", and "Profile as filled in" reads Career summary "MBA finance candidate seeking placement.". |
| ER-2 | 2 | "Faculty mentor" names Test Mentor, and the newest entry under "Mentor history" names Test Mentor with a "Current" chip. |
| ER-3 | 3 | "Results, semester by semester" lists semester 1 with SGPA 8.2 and CGPA 8.2. |
| ER-4 | 4 | "Documents" lists the three seeded files with their kind and verdict, and the reviewer's note under the file name when there is one: leadership_completion.pdf (Certificate), me.png (Photo) and resume_v1.pdf (CV). On a freshly seeded database the certificate and the photo read "Pending review" with no note, and the CV reads "Verified" with the note "Looks good.". |
| ER-5 | 5 | The browser downloads resume_v1.pdf, a PDF. |

---

## TC-515 — Removing a student and restoring them from the Removed list

| Field | Value |
|---|---|
| ID | TC-515 |
| Module | Admin: students roster, remove and restore |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-515` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new student exists (see the top of this file).

### Test data

| Field | Value |
|---|---|
| Student | The new student |
| Too short a reason | `ab` |
| Reason | `E2E: left the programme` |

### Steps

1. Open `/admin/students`.
2. Type the student's name into "Quick filter…".
3. Click the pencil on their row.
4. Click "Remove or delete…".
5. Type `ab` into "Reason".
6. Replace the reason with `E2E: left the programme` and click "Remove from the roster".
7. Under "Status", choose "Removed · off the roster, record kept".
8. Click the pencil on their row.
9. Click "Restore to the roster".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | A dialog "Remove or delete <name>" opens with "Remove — keep the record" chosen. It says "Off every list, picker and screen at once.", "Cannot sign in by any door — REEP password and Google both — and every device is signed out.", "Every record is kept exactly as it is: marks, uploads, notes, interviews, leave." and "Restore brings the account back with nothing lost." "Remove from the roster" is disabled. |
| ER-2 | 5 | "Remove from the roster" is still disabled: a reason needs three characters or more. |
| ER-3 | 6 | The dialog closes, the screen says "<name> is off every screen and cannot sign in. Every record is kept; Restore brings them back." and the student leaves the roster. |
| ER-4 | 7 | The student is listed, with Status "Removed". |
| ER-5 | 8 | The edit dialog offers "Restore to the roster" in place of "Remove or delete…", and Save is disabled. |
| ER-6 | 9 | The dialog closes, the screen says "<name> is back on the screens with every record. They can sign in again." and the student leaves the Removed list. |

### Post-conditions

The new student is back on the roster, with no batch and no faculty member.
The automated test removes them again when the case ends.

---

## TC-516 — The faculty list shows the seeded faculty member

| Field | Value |
|---|---|
| ID | TC-516 |
| Module | Admin: faculty |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-516` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Mentor mentors Test Student and is filed in no department, as
   seeded. On a freshly seeded database Test Student is their only student.

### Test data

| Field | Value |
|---|---|
| Faculty member | Test Mentor, `mentor@bgscet.ac.in` |

### Steps

1. Open `/admin/faculty`.
2. Click Test Mentor's row.
3. Click the Access tab.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Faculty". Test Mentor's row reads: "Test Mentor" with "mentor@bgscet.ac.in"; Department "Not filed"; Designation "Not on record"; Mentor group "Mentor · N", where N is the number of students they have now (the Mentor load screen, `/admin/mentors`, gives the same number; 1 on a freshly seeded database); Status "Active". |
| ER-2 | 2 | A side panel opens for Test Mentor with tabs Profile, Access and Sessions. Profile shows Name "Test Mentor", Official email "mentor@bgscet.ac.in" and College "Not filed". |
| ER-3 | 3 | The panel says "N mentees in their group." with the same N as ER-1 ("1 mentee in their group." on a freshly seeded database) and that access is given on "Who can do what", which links to `/admin/governance`. |

---

## TC-517 — Adding a faculty member hands over an activation link

| Field | Value |
|---|---|
| ID | TC-517 |
| Module | Admin: add faculty |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-517` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. No mail transport is configured (`SES_FROM_ADDRESS` blank), which is the
   development default.

### Test data

| Field | Value |
|---|---|
| Full name | A unique name, for example `E2E Faculty <today's date and time>` |
| College email | A unique address on `bgscet.ac.in`, for example `e2e.faculty.<date-time>@bgscet.ac.in` |

### Steps

1. Open `/admin/faculty`.
2. Click "Add faculty".
3. Under "College", choose "BGSCET · BGS College of Engineering and Technology".
4. Under "Department", choose "MGMT · Department of Management Studies".
5. Click Continue.
6. Enter the name in "Full name" and the address in "College email".
7. Click Continue.
8. Click "Create account & invite".
9. Click Done.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | `/admin/faculty/new` opens, headed "Add faculty member", on "Step 1 of 3" (Institution, Identity, Invite). "College" offers "BGSCET · BGS College of Engineering and Technology". (When the deployment has only one college the wizard chooses it by itself; with more, College starts at "Select a college".) |
| ER-2 | 3 | "Department" can be chosen and offers "MGMT · Department of Management Studies". |
| ER-3 | 5 | "Step 2 of 3" says which addresses the college admits: "BGSCET admits addresses on <its domains>." or, when it has none of its own (as seeded), "BGSCET has no domains of its own, so the deployment’s list applies." "Filed under" names "MGMT · Department of Management Studies". |
| ER-4 | 7 | "Step 3 of 3" reviews Name, College email (lower-cased), College "BGSCET · BGS College of Engineering and Technology", Department "MGMT · Department of Management Studies", Designation "—" and Role "Faculty · no functions". |
| ER-5 | 8 | The screen says "<name> now has a Faculty account on <address>. Nothing can sign in to it until they redeem the invitation and set their own password." and "No mail was sent. The link below is the only way <name> gets in — hand it over yourself. It expires in 168 hours and is shown once." The link, `…/activate?token=…`, is on screen, with "Copy link", "Add another" and "Done". |
| ER-6 | 9 | `/admin/faculty` opens. The new faculty member's row reads Department "Department of Management Studies", Designation "Not on record", Mentor group "No mentor group" and Status "Active". |
| ER-7 | 8 | **Manual only.** On a deployment with a mail transport, the screen says instead "The sign-in link was emailed to <address> and expires in 168 hours." and the mail arrives. |

### Post-conditions

A new faculty account exists, with no password. The automated test removes it
when the case ends.

---

## TC-518 — Adding a faculty member refuses missing or invalid details

| Field | Value |
|---|---|
| ID | TC-518 |
| Module | Admin: add faculty |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-518` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

| Field | Value |
|---|---|
| Full name | `E2E Visitor` |
| An address off the college domain | `visitor@example.com` |
| An address that already has an account | `mentor@bgscet.ac.in` |

### Steps

1. Open `/admin/faculty/new`.
2. Under "College", choose "BGSCET · BGS College of Engineering and Technology".
3. Click Continue without choosing a department.
4. Under "Department", choose "MGMT · Department of Management Studies" and click Continue.
5. Click Continue with "Full name" and "College email" empty.
6. Enter `E2E Visitor` in "Full name" and `visitor@example.com` in "College email", then click Continue.
7. Click "Create account & invite".
8. Click Back.
9. Replace "College email" with `mentor@bgscet.ac.in` and click Continue.
10. Click "Create account & invite".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The wizard stays on "Step 1 of 3" and says "A department is required: it is how this account reaches its college, and the college decides which addresses may hold one." |
| ER-2 | 5 | The wizard stays on "Step 2 of 3" and says "A name is required." and "Type a full email address — this is the address they sign in with." |
| ER-3 | 7 | Nothing is created. The wizard says "visitor@example.com is not on this college's domains (bgscet.ac.in). Tick "outside the college domain" and give a reason if that is deliberate." and still offers "Create account & invite". The brackets list the college's own domains, or the deployment's list (`bgscet.ac.in` in development) when it has none, as seeded. |
| ER-4 | 10 | Nothing is created. The wizard says "mentor@bgscet.ac.in already belongs to a MENTOR account." |

---

## TC-519 — Editing a faculty member's profile

| Field | Value |
|---|---|
| ID | TC-519 |
| Module | Admin: faculty |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-519` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists (see the top of this file), with no
   designation.

### Test data

| Field | Value |
|---|---|
| Designation | `Assistant Professor` |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row.
3. Type `Assistant Professor` into "Designation" and click "Save changes".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The side panel shows their Name and Official email, College "BGS College of Engineering and Technology" (read from the department) and Department "Department of Management Studies · BGSCET". |
| ER-2 | 3 | The screen says "Saved <name>.", the Designation column reads "Assistant Professor", and the panel names them "Assistant Professor · Department of Management Studies · BGS College of Engineering and Technology". |

---

## TC-520 — A new activation link replaces the earlier one

| Field | Value |
|---|---|
| ID | TC-520 |
| Module | Admin: faculty, activation links |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-520` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists, and you kept the activation link shown when
   the account was created. Nobody has opened it yet.
3. A second, private browser window that is signed in to nothing.

### Test data

| Field | Value |
|---|---|
| Password to set | Any password of 12 characters or more that is not a demo password, for example `e2e faculty passphrase 2026` |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row, then the Sessions tab.
3. Click "Make a sign-in link".
4. In a private window, open the link from the account's creation and set a password.
5. In the private window, open the new link and set a password.

To set a password on the "Set up your REEP password" page, type it into "New
password" and "Type it again", and click "Set password and sign in".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The screen says "A new activation link for <name> is ready to hand over." The "Hand this over" box holds a new `…/activate?token=…` link, different from the first, with the chips "Expires in 168 h" and "Mail is off — read it out". |
| ER-2 | 4 | The page refuses: "This link has already been used. If that was not you, ask for a new one." and "Ask the placement office to send you a new activation link." |
| ER-3 | 5 | The password is set and the faculty member lands on `/mentor/notebook`, headed "Faculty notebook". |
| ER-4 | 3 | **Manual only.** On a deployment with a mail transport the chip reads "Emailed as well" and the new link arrives by mail. |

### Post-conditions

The faculty member has a password and is signed in in the private window. The
automated test removes the account when the case ends.

---

## TC-521 — Disabling a faculty account needs a reason, and enabling it again

| Field | Value |
|---|---|
| ID | TC-521 |
| Module | Admin: faculty, disable and enable |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-521` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. An activated faculty member exists (see the top of this file), with no
   students, and you know their password.
3. A second, private browser window that is signed in to nothing.

### Test data

| Field | Value |
|---|---|
| Too short a reason | `ab` |
| Reason | `E2E: on sabbatical for a term` |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row.
3. Click "Disable account".
4. Type `ab` into "Reason".
5. Replace the reason with `E2E: on sabbatical for a term` and click "Disable account" in the dialog.
6. In a private window, sign in on `/login` as the faculty member with their password.
7. Under "Status", choose "Disabled".
8. Click "Enable account" in the side panel.
9. Under "Status", choose "All".

To sign in on `/login`, choose the Faculty portal, type the address into
"Institutional email" and the password into "Password", and click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A dialog "Disable <name>’s account" opens. Among what it states: "Sign-in stops immediately — REEP password and Google both. Every device it holds is signed out." The "Reason" box is marked "Required." and "Disable account" is disabled. |
| ER-2 | 4 | "Disable account" is still disabled: a reason needs three characters or more. |
| ER-3 | 5 | The dialog closes, and the screen says "<address> can no longer sign in, and every device it held has been signed out. Nothing they wrote has been removed." Status reads "Disabled" with today's date, and the side panel says "Disabled on <date> — E2E: on sabbatical for a term". |
| ER-4 | 6 | The sign-in is refused: the sign-in page shows an error and stays on `/login`. (What the error says is TC-523.) |
| ER-5 | 7 | The faculty member is listed as Disabled. |
| ER-6 | 8 | The screen says "<address> can sign in again. Capability grants were not restored - grant what is still needed in Governance." and the faculty member leaves the Disabled list. |
| ER-7 | 9 | Their Status reads "Active" again. |

### Post-conditions

The account is active again. The audit trail has a `user` event with the
action `DISABLE`, carrying the reason, and one with `ENABLE`.

---

## TC-522 — The disable dialog says what happens to the faculty member's mentees

| Field | Value |
|---|---|
| ID | TC-522 |
| Module | Admin: faculty, disable |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-522` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists and mentors Test Student: assign them on
   `/admin/mentors` (or with TC-511's steps 1 to 4), so their row reads
   "Mentor · 1".

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: checking the mentee sentence` |

### Steps

1. Open `/admin/faculty`.
2. Click their row and click "Disable account".
3. Type `E2E: checking the mentee sentence` into "Reason" and click "Disable account" in the dialog.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The dialog tells the truth about the mentee: it does not say they stay filed under this faculty member, and it does not say the mentor group is NOT released. |
| ER-2 | 3 | The screen says, among the rest, "1 student(s) were released back to the unassigned pool and need a new faculty member." |

### Post-conditions

Test Student has no faculty member. Put them back with Test Mentor on
`/admin/mentors` (the automated test does this through the API), and remove
or enable the new faculty member.

---

## TC-523 — A disabled faculty member is told their account is disabled

| Field | Value |
|---|---|
| ID | TC-523 |
| Module | Admin: faculty, disable; the sign-in page |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-523` |

### Pre-conditions

1. The common pre-conditions at the top of this file, except that this case
   is run signed out, in a private window.
2. An activated faculty member exists, you know their password, and the Main
   Admin has disabled the account (TC-521, steps 1 to 5).

### Test data

| Field | Value |
|---|---|
| Portal | Faculty |
| Email and password | The disabled faculty member's |

### Steps

1. Open `/login`.
2. Under "Choose your portal", select Faculty.
3. Enter the faculty member's address and password.
4. Click Sign in.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The page says "Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue.", which is what the server answers. |

### Post-conditions

The faculty member is still disabled. Enable or remove them in the console.

---

## TC-524 — Signing a faculty member out of every device

| Field | Value |
|---|---|
| ID | TC-524 |
| Module | Admin: faculty, sessions |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-524` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. An activated faculty member exists and you know their password.
3. A second browser, or a private window, for the faculty member.

### Test data

None.

### Steps

1. In a second browser, sign in as the new faculty member and open their notebook.
2. In the admin browser, open `/admin/faculty`.
3. Click the faculty member's row, then the Sessions tab.
4. Click "Sign out everywhere" in the Sessions card.
5. In the second browser, reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The faculty member sees their notebook, headed "Faculty notebook". |
| ER-2 | 4 | The screen says "Every device holding <address> has been signed out." Their Status stays "Active". |
| ER-3 | 5 | The second browser is sent to the sign-in page, `/login?next=%2Fmentor%2Fnotebook…`. |

### Post-conditions

The faculty member can sign in again as usual.

---

## TC-525 — Removing a faculty account and restoring it

| Field | Value |
|---|---|
| ID | TC-525 |
| Module | Admin: faculty, remove and restore |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-525` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists, with no students.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: left the college` |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row.
3. Click "Remove or delete…".
4. Type `E2E: left the college` into "Reason" and click "Remove from the roster".
5. Under "Status", choose "Removed · off the roster, record kept".
6. Click their row and click "Restore to the roster".
7. Under "Status", choose "All".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A dialog "Remove or delete <name>" opens with "Remove — keep the record" chosen. Besides the student version's sentences (TC-515, ER-1) it says "Their mentees are released to the unassigned pool and need a new faculty member." "Remove from the roster" is disabled until a reason of three characters or more is typed. |
| ER-2 | 4 | The dialog closes, the screen says "<name> is off every screen and cannot sign in. Every record is kept; Restore brings them back." and the faculty member leaves the list. |
| ER-3 | 5 | The faculty member is listed, with Status "Removed" and today's date. |
| ER-4 | 6 | The screen says "<name> is back on the screens with every record. They can sign in again." (followed by a sentence about released students). |
| ER-5 | 7 | The faculty member is listed once, with Status "Active". |

---

## TC-526 — Deleting for good refuses a wrong code

| Field | Value |
|---|---|
| ID | TC-526 |
| Module | Admin: faculty, delete for good |
| Priority | P1 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-526` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists.
3. No deletion code has been asked for in the last 10 minutes, so there is
   no live code `000000` could match by chance.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: duplicate account` |
| A wrong code | `000000` |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row and click "Remove or delete…".
3. Choose "Delete for good".
4. Type `E2E: duplicate account` into "Reason" and `000000` into "Code from your email".
5. Click "Delete for good".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | The dialog lists what a delete would take, ending "The audit trail keeps that this was done, by whom and why; the person's name leaves every other record." It asks for a "Code from your email", offers "Email me a code", and "Delete for good" is disabled. |
| ER-2 | 4 | "Delete for good" is enabled. |
| ER-3 | 5 | The dialog says "That code is not right, has expired, or was already used. Ask for a new one - it is emailed to the office account's own address." and the account is still there. |

---

## TC-527 — Deleting a faculty account for good with the emailed code

| Field | Value |
|---|---|
| ID | TC-527 |
| Module | Admin: faculty, delete for good |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): the six-digit code is emailed to the Main Admin's own address, and only a person reading that mailbox, or the API console of a development server, can type it. Code requests are also limited to 6 an hour per admin. |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new faculty member exists, made for this case alone: this case destroys
   it.
3. Access to the Main Admin's mailbox, or to the API's console output on a
   development server with no mail transport.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: created by mistake` |
| Code | The six digits from the email |

### Steps

1. Open `/admin/faculty`.
2. Click the new faculty member's row and click "Remove or delete…".
3. Choose "Delete for good".
4. Click "Email me a code".
5. Type `E2E: created by mistake` into "Reason" and the code from the email into "Code from your email".
6. Click "Delete for good".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 4 | The dialog says "We have emailed a code to admin@bgscet.ac.in. It expires in 10 minutes and works for one deletion." and the link now reads "Send another code". |
| ER-2 | 4 | **Manual only.** A mail with the code reaches the Main Admin's mailbox, or, with no mail transport, the API console logs it. |
| ER-3 | 6 | The dialog closes and the screen says "<name> (<address>) has been deleted for good: N record(s) and M file(s)." |
| ER-4 | 6 | The faculty member is on neither the list nor the "Removed · off the roster, record kept" list, and the audit trail keeps a `user` event with the action `DELETE_PERMANENT`. |

### Post-conditions

The account is gone for good, and the code is spent.

---

## TC-528 — The mentor load shows each faculty member's students against capacity

| Field | Value |
|---|---|
| ID | TC-528 |
| Module | Admin: assign faculty |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-528` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Mentor mentors Test Student, the programme's mentor capacity is 20,
   and no department has set its own. On a freshly seeded database Test
   Student is Test Mentor's only student; other test modules may have given
   them more.

### Test data

None.

### Steps

1. Open `/admin/mentors`.
2. Click Test Mentor on the Mentors list.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "Assign faculty". The line under it counts the students with no faculty member; on a freshly seeded database it reads "Every student has a faculty member." |
| ER-2 | 1 | Test Mentor is on the Mentors list with "Department not on record", their load as "N/20", where N is the number of students they have now, and the places left: "<20 − N> places free of 20, the programme default" ("At capacity — 20, the programme default" once N reaches 20). On a freshly seeded database that is "1/20" and "19 places free of 20, the programme default". |
| ER-3 | 2 | Test Mentor is selected, and "Current mentees · N", with the same N, lists Test Student with "1BG24MBA001 · Excel-Adv" and three numbers: attendance, verified skills and hours logged (for example "85% attendance · 1 verified skill · 47.5 h logged"). |

---

## TC-529 — Releasing a student and assigning them again, with the history

| Field | Value |
|---|---|
| ID | TC-529 |
| Module | Admin: assign faculty |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-529` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Test Mentor mentors Test Student, with a capacity of 20. Note Test
   Mentor's load on the Mentors list before you start, "N/20" (1/20 on a
   freshly seeded database).

### Test data

| Field | Value |
|---|---|
| Reason for the release | `E2E: moving between groups` |
| Reason for the assignment | `E2E: back with their mentor` |

### Steps

1. Open `/admin/mentors`.
2. Click Test Mentor on the Mentors list.
3. Click the release button on Test Student's row.
4. Type `E2E: moving between groups` into "Why is Test Student being released?" and click "Release".
5. Tick Test Student in "Unassigned students".
6. Type `E2E: back with their mentor` into "Reason" and click "Assign 1 selected to Test Mentor".
7. Click the history button on Test Student's row.

The release button is the one with the person-remove icon, named "Remove Test
Student from Test Mentor"; the history button has the clock icon.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 3 | A box opens on Test Student's row asking "Why is Test Student being released?". Its "Release" button is disabled until a reason is typed, and Test Student is still in the group. |
| ER-2 | 4 | The screen says "Test Student released from Test Mentor". Test Student is under "Unassigned students", and Test Mentor holds one student fewer: "N − 1/20" ("0/20" on a freshly seeded database). |
| ER-3 | 5 | The button reads "Assign 1 selected to Test Mentor" and is disabled until a reason is typed. |
| ER-4 | 6 | The screen says "1 student assigned to Test Mentor". Test Student is back under "Current mentees", and Test Mentor reads "N/20" again ("1/20" on a freshly seeded database). |
| ER-5 | 7 | "Assignment history · Test Student" lists, newest first, a "Current" spell with Test Mentor, "Assigned by Main Admin (seed) — “E2E: back with their mentor”", then an "Ended" spell with Test Mentor, "Released to the unassigned pool by Main Admin (seed) — “E2E: moving between groups”". |

### Post-conditions

Test Student is with Test Mentor again. The release gave Test Mentor a 90-day
read-only "Mentee log" grant for this student, listed on `/admin/governance`.

---

## TC-530 — Giving one kind of access with a reason, scope and dates, then removing it

| Field | Value |
|---|---|
| ID | TC-530 |
| Module | Admin: who can do what |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-530` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. An activated faculty member exists, with no grants, and you know their
   password.
3. A second browser, signed in as that faculty member.

### Test data

| Field | Value |
|---|---|
| Access | Analytics |
| Which students | Department · Department of Management Studies |
| Too short a reason | `E2E: short` |
| Reason for the grant | `E2E: reviewing placement numbers this term` |
| Ends on | 60 days from today |
| New review date | 10 days from today |
| Reason for the extension | `E2E: still reviewing the placement numbers` |
| Reason for the removal | `E2E: the review of the numbers is finished` |

### Steps

1. Open `/admin/governance`.
2. Under "Who", search for the new faculty member and tick them.
3. Under "What they can open", tick "Analytics".
4. Under "Which students", choose "Department · Department of Management Studies".
5. Type `E2E: short` into "Reason".
6. Set "Ends on" to a date 60 days ahead and replace the reason with `E2E: reviewing placement numbers this term`.
7. Click "Give access".
8. In the faculty member's browser, open `/admin/analytics`.
9. Click Extend on the faculty member's row.
10. Set "New review date" to a date 10 days ahead, type `E2E: still reviewing the placement numbers` into "Reason" and click Extend.
11. Click the "Needs attention" tab.
12. Click the "Who has access" tab and click "Remove" on the faculty member's row.
13. Type `E2E: the review of the numbers is finished` into "Reason" and click "Remove access".
14. In the faculty member's browser, reload `/admin/analytics`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | On the "Give access" tab, "Give access" is disabled and the form says "Choose at least one person". |
| ER-2 | 2 | "1 chosen" shows beside "Who", and the form now says "Choose at least one kind of access". |
| ER-3 | 3 | The Analytics choice says it opens "Charts & numbers". |
| ER-4 | 4 | The summary says "1 person will get “Analytics” for department Department of Management Studies (N students), with no end date." (1 student on a freshly seeded database). |
| ER-5 | 5 | "Give access" is disabled and the form says "A reason of at least 20 characters is required". |
| ER-6 | 6 | "Give access" is enabled. |
| ER-7 | 7 | The screen says "Gave “Analytics” to <name>." and the "Who has access" tab opens. Under "Analytics" the faculty member is listed, marked "Faculty"; Students "Department · Department of Management Studies"; an end date; Status "Active". |
| ER-8 | 8 | The grant works at once: "Charts & numbers" opens for the faculty member. |
| ER-9 | 9 | An "Extend 1 grant" box opens above the tabs' content. |
| ER-10 | 10 | The screen says "Extended “Analytics” for <name>." and the row's Status reads "Review due". |
| ER-11 | 11 | Under "Ending or due for review in the next 30 days" the faculty member's Analytics grant is listed. |
| ER-12 | 12 | A "Remove access" box opens, naming the grant and the faculty member. |
| ER-13 | 13 | The screen says "Removed “Analytics” from <name>." and the row leaves the list. |
| ER-14 | 14 | "Charts & numbers" is closed to them again: they land on `/mentor/notebook`. |

### Post-conditions

The faculty member holds no grant. The audit trail has `capability_grant`
events with the actions `GRANTED`, `EXTENDED` and `REVOKED`, each carrying its
reason.

---

## TC-531 — A grant's end date reads back as the day that was typed

| Field | Value |
|---|---|
| ID | TC-531 |
| Module | Admin: who can do what |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-531` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The computer's time zone is India Standard Time (UTC+5:30), as the
   college's is. The automated test sets its browser to `Asia/Kolkata`.
3. A new faculty member exists, with no grants.

### Test data

| Field | Value |
|---|---|
| Access | Analytics |
| Ends on | 45 days from today |
| Reason | `E2E: checking the expiry date` |

### Steps

1. Open `/admin/governance`.
2. Under "Who", search for the new faculty member and tick them.
3. Under "What they can open", tick "Analytics".
4. Set "Ends on" to a date 45 days ahead.
5. Type `E2E: checking the expiry date` into "Reason" and click "Give access".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 5 | The access is given, and on "Who has access" its Ends column shows the day that was typed. |

### Post-conditions

The faculty member holds a live Analytics grant. Remove it on "Who has access"
("Remove", with a reason of at least 20 characters); the automated test does
this through the API.

---

## TC-532 — Switching a student feature off for a batch and removing the rule

| Field | Value |
|---|---|
| ID | TC-532 |
| Module | Admin: student feature switches |
| Priority | P1 |
| Type | Functional, positive and negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-532` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Leaderboards has no rule: it is on for every student, as seeded.
3. Below, N is the number of students seated in the batch, counted the way
   this screen counts them: every student in it, including students removed
   from the roster (their records are kept). "N students" reads "1 student" on
   a freshly seeded database, where Test Student is the batch's only student;
   earlier test runs add more.

### Test data

| Field | Value |
|---|---|
| Feature | Leaderboards (`student.leaderboards`) |
| Batch | Master of Business Administration - Finance · 2024-26 Section B |
| Too short a reason | `E2E: short` |
| Reason | `E2E: leaderboards paused during the audit` |
| Student-facing message | `Leaderboards are paused this week.` |

### Steps

1. Open `/admin/governance/features`.
2. Click "Leaderboards" in the table.
3. Under "Scope", choose "Batch".
4. Under "Applies to", choose "Master of Business Administration - Finance · 2024-26 Section B".
5. Type `E2E: short` into "Reason".
6. Replace the reason with `E2E: leaderboards paused during the audit`, type `Leaderboards are paused this week.` into "Student-facing message" and click Save.
7. Click "Remove override" on the rule.
8. Click "Remove override" in the confirmation.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The panel is headed "Override · Leaderboards" and says "The API checks this switch before it serves the feature." and "No rule on this feature — it is on for every student." Save is disabled, with "Pick who this applies to." |
| ER-2 | 4 | The panel counts the reach: "N students · beaten only by a student-level rule". |
| ER-3 | 5 | Save is disabled, with "A reason of at least 20 characters is required." |
| ER-4 | 6 | The screen says "Leaderboards is off for 2024-26 Section B — N students." The table's Leaderboards row reads Applies to "Batch · 2024-26 Section B · N students" and Value "Off". The panel shows "1 rule in force": "Batch · 2024-26 Section B", the reason, and "Students are shown: “Leaderboards are paused this week.”" |
| ER-5 | 7 | The panel asks "Remove this rule? The next rung up decides again for N students." |
| ER-6 | 8 | The screen says "Rule removed for 2024-26 Section B. The next rung up decides again for N students." Leaderboards reads "On everywhere" again, and the panel says "No rule on this feature — it is on for every student." |

### Post-conditions

Leaderboards is on for every student again. The audit trail has
`feature_override` events for the rule being set and cleared.

---

## TC-533 — Every student feature switch is enforced by the API

| Field | Value |
|---|---|
| ID | TC-533 |
| Module | Admin: student feature switches |
| Priority | P3 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-533` |

### Pre-conditions

1. The common pre-conditions at the top of this file.

### Test data

None.

### Steps

1. Open `/admin/governance/features`.
2. Under "Enforcement", choose "Not wired yet".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The screen says "All 10 switches are enforced by the API." The table lists Voice interviewer (Mock Interview), REEP Agent (chat), Resume Builder, English baseline test, Jobs feed & applications, Leaderboards, Document uploads, Time allocation ledger, Skilling & badges and Mentor meeting log, each "Server-enforced". |
| ER-2 | 2 | The table says "No switch matches these filters." No switch is unwired, so none of them shows "This switch is not wired yet, so it cannot be set." and the server's refusal of a rule on an unwired switch cannot be reached from this screen in this build. |

---

## TC-534 — A student refused a switched-off feature reads the office's message

| Field | Value |
|---|---|
| ID | TC-534 |
| Module | Admin: student feature switches; the student's Leaderboards |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-534` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Leaderboards has no rule, as seeded.
3. A second browser, or a private window, for the student.

### Test data

| Field | Value |
|---|---|
| Batch | Master of Business Administration - Finance · 2024-26 Section B |
| Student-facing message | `Leaderboards are paused this week.` |
| Student | `student@bgscet.ac.in` / `student123` |

### Steps

1. As the Main Admin, switch "Leaderboards" off for the batch "Master of Business Administration - Finance · 2024-26 Section B", with a student-facing message.
2. Sign in as the student and open `/student/leaderboards`.

Step 1 is TC-532's steps 1 to 6, with the message from the test data.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | The Leaderboards screen shows the office's message, "Leaderboards are paused this week." |

### Post-conditions

Remove the rule again (TC-532, steps 7 and 8); the automated test does this
through the API.

---

## TC-535 — The audit trail shows a console change and opens its event

| Field | Value |
|---|---|
| ID | TC-535 |
| Module | Admin: what changed |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-535` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. In the last 7 days the Main Admin has disabled a new faculty member, with
   the reason `E2E: an event for the trail` (TC-521, steps 1 to 5). You know
   the account's id: it is the last part of the Route of that event, and the
   automated test reads it from the API.

### Test data

| Field | Value |
|---|---|
| Target | `user · <the faculty member's id>` |

### Steps

1. Open `/admin/audit`.
2. Under "What", choose "DISABLE".
3. Type the faculty member's id into "Filter the events on this page…".
4. Click the row whose Target is user · the faculty member's id.
5. Click "Open Faculty".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The page is headed "What changed" and says "Every change made in this console, newest first." The date range is "Last 7 days". |
| ER-2 | 2 | Every event listed has the Action "DISABLE". |
| ER-3 | 3 | One event is left: Actor "Main Admin (seed)", Action "DISABLE", Target "user · <id>", Route "/api/admin/users/<id>/disable". |
| ER-4 | 4 | The Event panel names "Main Admin (seed)", "DISABLE" and "user · <id>". Under Change, Before shows `"disable_reason": null` and After shows `"disable_reason": "E2E: an event for the trail"`. |
| ER-5 | 5 | The Faculty screen, `/admin/faculty`, opens. |

---

## TC-536 — Exporting the audit trail as a CSV file

| Field | Value |
|---|---|
| ID | TC-536 |
| Module | Admin: what changed |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-536` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. In the last 7 days the Main Admin has disabled a new faculty member
   (TC-521, steps 1 to 5), so the range has events to export.

### Test data

None.

### Steps

1. Open `/admin/audit`.
2. Click "Export range".
3. Reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A file named `audit-log.csv` downloads. Its first line is `When,Actor,Actor email,Actor type,Action,Target type,Target id,Route,Request id`, and it has a row for the faculty member's `DISABLE`, by `Main Admin (seed),admin@bgscet.ac.in`. |
| ER-2 | 3 | The newest event on the trail is the export itself: Action "EXPORTED", Target "audit_export · …", Actor "Main Admin (seed)". |

---

## TC-537 — A faculty member cannot open the admin console

| Field | Value |
|---|---|
| ID | TC-537 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-537` |

### Pre-conditions

1. The web app and the API are running with a development `ENV`, and the dev
   seed has been applied.
2. You are signed in as the seeded faculty member, `mentor@bgscet.ac.in` /
   `mentor123`, who holds no console grant.

### Test data

None.

### Steps

1. Open `/admin`.
2. Open `/admin/students`.
3. Open `/admin/faculty`.
4. Open `/admin/governance`.
5. Open `/admin/audit`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends them to `/mentor/notebook`, headed "Faculty notebook", not the console. |
| ER-2 | 2 | The same: `/mentor/notebook`, "Faculty notebook". |
| ER-3 | 3 | The same: `/mentor/notebook`, "Faculty notebook". |
| ER-4 | 4 | The same: `/mentor/notebook`, "Faculty notebook". |
| ER-5 | 5 | The same: `/mentor/notebook`, "Faculty notebook". |

---

## TC-538 — A student cannot open the admin console

| Field | Value |
|---|---|
| ID | TC-538 |
| Module | Admin: access |
| Priority | P1 |
| Type | Security, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-538` |

### Pre-conditions

1. The web app and the API are running with a development `ENV`, and the dev
   seed has been applied.
2. You are signed in as the seeded student, `student@bgscet.ac.in` /
   `student123`.

### Test data

None.

### Steps

1. Open `/admin`.
2. Open `/admin/students`.
3. Open `/admin/governance`.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The app sends them to `/student`, headed "Welcome back, Test", not the console. |
| ER-2 | 2 | The same: `/student`, "Welcome back, Test". |
| ER-3 | 3 | The same: `/student`, "Welcome back, Test". |

---

## TC-540 — Giving two kinds of access to two people at once, then removing one from both

| Field | Value |
|---|---|
| ID | TC-540 |
| Module | Admin: who can do what |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-540` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. Two new faculty members exist, named "E2E Faculty …", with no grants.

### Test data

| Field | Value |
|---|---|
| Access | Analytics and Jobs sheet |
| Reason for the grant | `E2E: placement drive coordinators this term` |
| Reason for the removal | `E2E: the drive no longer needs the charts` |

### Steps

1. Open `/admin/governance`.
2. Under "Who", search for "E2E Faculty" and tick both new faculty members.
3. Under "What they can open", tick "Analytics" and "Jobs sheet".
4. Type `E2E: placement drive coordinators this term` into "Reason" and click "Give access".
5. Under "Analytics", tick both new faculty members and click "Remove Analytics from 2 people".
6. Type `E2E: the drive no longer needs the charts` into "Reason" and click "Remove 2 grants".

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | "2 chosen" shows beside "Who". |
| ER-2 | 3 | The summary says "2 people will get 2 kinds of access for all students, with no end date." |
| ER-3 | 4 | The screen says "Gave access: 4 grants." On "Who has access" both people are listed under "Analytics" and under "Jobs sheet". |
| ER-4 | 5 | A "Remove access" box opens, naming both people. |
| ER-5 | 6 | The screen says "Removed “Analytics” from 2 people." Neither is listed under "Analytics" any more; both are still listed under "Jobs sheet". |

### Post-conditions

Both faculty members hold Jobs sheet. Remove it on "Who has access" (tick both
under "Jobs sheet" and click "Remove Jobs sheet from 2 people"); the automated
test revokes every grant and removes both accounts through the API.

---

## TC-541 — Disabling a student's account from their record and enabling it again

| Field | Value |
|---|---|
| ID | TC-541 |
| Module | Admin: student record, disable |
| Priority | P1 |
| Type | Functional, positive |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-541` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. A new student exists, named "E2E Student …", approved from an application
   and never signed in. Not the seeded student: disabling them would sign them
   out of every other case.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: password shared with another student` |

### Steps

1. Open the student's record at `/admin/students/<id>`.
2. Click "More", then "Disable account".
3. Type `E2E: password shared with another student` into "Reason" and click "Disable account" in the box.
4. Click "More", then "Enable account".
5. Click "Enable account" in the box.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 2 | A box "Disable <name>'s account?" opens. It says they cannot sign in, that every device is signed out, and that "they are told the account was disabled for a security reason and to contact the placement office". Its "Disable account" button is disabled until a reason is typed. |
| ER-2 | 3 | The box closes and the page says "<address> can no longer sign in, and every device it held has been signed out. Nothing they wrote has been removed." The status chip in the summary reads "Disabled", and the line under it gives the reason. |
| ER-3 | 4 | The menu offers "Enable account" and not "Disable account". A box "Enable <name>'s account?" opens. |
| ER-4 | 5 | The box closes and the page says "<address> can sign in again." The status chip reads "Not signed in yet" again. |

### Post-conditions

The student can sign in again. The automated test removes them from the
roster through the API.

---

## TC-542 — A faculty member signed in when their account is disabled is told why

| Field | Value |
|---|---|
| ID | TC-542 |
| Module | Admin: faculty, disable; the sign-in page |
| Priority | P2 |
| Type | Functional, negative |
| Automated test | `tests/06-admin-people.spec.ts`, title tagged `@TC-542` |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. An activated faculty member exists and you know their password.
3. A second browser, or a private window, for the faculty member.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: checking the signed-out note` |

### Steps

1. In a second browser, sign in as the new faculty member and open their notebook.
2. In the admin browser, disable their account with the reason `E2E: checking the signed-out note`.
3. In the second browser, reload the page.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The faculty member sees their notebook, headed "Faculty notebook". |
| ER-2 | 3 | The second browser is sent to `/login?next=%2Fmentor%2Fnotebook&signedOut=disabled`, and the page says "Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue." |

### Post-conditions

The faculty member is still disabled. Enable or remove them in the console;
the automated test removes them through the API.

---

## TC-543 — A disabled student is emailed, and the office can send the email again

| Field | Value |
|---|---|
| ID | TC-543 |
| Module | Admin: student record, disable; email |
| Priority | P2 |
| Type | Functional, positive |
| Automated test | None (manual only): the proof is an email in the student's inbox, and the automated suite does not read mail. Locally the API sends no mail unless `ACCOUNT_MAIL_ENABLED=true`, and then the message is only in the API's console log. |

### Pre-conditions

1. The common pre-conditions at the top of this file.
2. The API can send mail: SES is configured (production), or locally the API
   was started with `ACCOUNT_MAIL_ENABLED=true` and you can read its console log.
3. A new student exists, named "E2E Student …", whose college address you can
   read. Not the seeded student.

### Test data

| Field | Value |
|---|---|
| Reason | `E2E: checking the disabled email` |

### Steps

1. Open the student's record at `/admin/students/<id>`, click "More", then "Disable account".
2. Type `E2E: checking the disabled email` into "Reason" and click "Disable account" in the box.
3. Open the student's inbox (or the API's console log).
4. Back on the record, click "More", then "Email the disabled notice".
5. Click "Send email" in the box.

### Expected results

| # | After step | Expected result |
|---|---|---|
| ER-1 | 1 | The box says "They are emailed the same words now." and, under "Reason", that the reason is not in the email. |
| ER-2 | 2 | The page says "<address> can no longer sign in, …" and "We emailed <address> to say the account is disabled and to contact the placement office. Your reason is not in the email." The line under the status chip ends "Emailed to them <date, time>." |
| ER-3 | 3 | One email, "Your REEP account has been disabled", says "Your account has been disabled for security reasons. Contact the placement office (Main Admin) to resolve the issue." It does not contain the reason. |
| ER-4 | 4 | A box "Email <name> that their account is disabled?" opens and says when they were last emailed about it. |
| ER-5 | 5 | The box closes, the page says "We emailed <address> …", and a second copy of the same email arrives. |

### Post-conditions

The student is still disabled, and two messages are on Email delivery
(`/admin/mail`) with the kind `account-disabled`. Enable or remove them in
the console.
