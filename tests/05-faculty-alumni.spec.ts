/**
 * Faculty and alumni: the automated twins of the cases in
 * test-management/cases/05-faculty-alumni.md.
 *
 * THE TAG IS THE LINK. Each test's title ends with the `@TC-NNN` ID of the case
 * it automates, its top-level `test.step()` titles are that case's steps word
 * for word, and every assertion message names the expected result it checks.
 * tests/reporters/manual-csv-reporter.ts fails the run when the two drift.
 *
 * WHO IS SIGNED IN WHERE. REEP keeps ONE live session per account, so a second
 * sign-in as the same account retires the first. The page is signed in through
 * the `signIn` fixture, and anything done as that SAME account outside the
 * page (a clean-up, a read-back) goes through `page.request`, which shares the
 * page's cookie jar. Another account gets its own API context (`apiAs`) or its
 * own browser context (`openAsStudent`), never the page's.
 *
 * REPEATABLE ON ONE DATABASE. Everything a test creates carries RUN, a base-36
 * timestamp, and is removed afterwards where REEP lets anyone remove it. Where
 * it cannot be removed (a decided skill claim, a withdrawn leave request, an
 * archived notebook entry) the case's post-conditions say what stays, and no
 * assertion counts rows other runs can add.
 *
 * Nothing is mocked: every expected result is read off the real app and API.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { APIRequest, APIRequestContext, Browser, Locator, Page } from '@playwright/test';
import { ACCOUNTS, expect, test, type AccountKey } from './support/reep';

/** Unique to this run, so repeated runs on one database never collide. */
const RUN = Date.now().toString(36);

const FIXTURE = {
  pdf: path.join(__dirname, 'fixtures', 'sample.pdf'),
  png: path.join(__dirname, 'fixtures', 'sample.png'),
  jpg: path.join(__dirname, 'fixtures', 'sample.jpg'),
} as const;
/** `formatSize` in the upskilling and alumni screens, for sample.pdf's 1,411 bytes. */
const SAMPLE_PDF_SIZE = '1.4 KB';

/** The dev seed's pairing (apps/api-py/app/seed.py). */
const STUDENT = { name: ACCOUNTS.student.name, usn: '1BG24MBA001' } as const;
const MENTOR_NAME = ACCOUNTS.mentor.name;
/** The badge the claim cases file against: "SQL", Platform / Technical Skills. */
const SQL_BADGE = { code: 'TECH-SQL', name: 'SQL', category: 'Platform / Technical Skills' };

// ---------------------------------------------------------------- helpers --

interface Session {
  api: APIRequestContext;
  studentId: string | null;
}

/** A signed-in API client for a seeded account, with its OWN cookie jar. */
async function apiAs(request: APIRequest, baseURL: string, account: AccountKey): Promise<Session> {
  const api = await request.newContext({ baseURL });
  const { email, password } = ACCOUNTS[account];
  const response = await api.post('/api/auth/login', { data: { email, password } });
  expect(response.status(), `arrange: ${email} signs in through the API`).toBe(200);
  const body = (await response.json()) as { studentId?: string | null };
  return { api, studentId: body.studentId ?? null };
}

/** A second browser, signed in as the dev seed's student, on `path`. */
async function openAsStudent(browser: Browser, baseURL: string, where: string): Promise<Page> {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  const { email, password } = ACCOUNTS.student;
  const response = await page.request.post('/api/auth/login', { data: { email, password } });
  expect(response.status(), 'arrange: the student signs in in the second browser').toBe(200);
  await page.goto(where);
  return page;
}

/** A chip whose text ends with `text`. Chips carry a Material Symbols glyph
 *  whose ligature name is part of their text (sometimes with no space before
 *  the label), so an exact match cannot work. */
function chip(scope: Page | Locator, text: string): Locator {
  return scope.locator('.chip').filter({ hasText: endsWith(text) });
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Text that ends with `text`: for a message or a link led by an icon glyph,
 *  whose ligature name is part of the element's text. */
function endsWith(text: string): RegExp {
  return new RegExp(`${escapeRegExp(text)}\\s*$`);
}

/** Accepts the next `window.confirm` and hands back its message. Playwright
 *  dismisses dialogs by default, which would cancel every delete here. */
function acceptNextDialog(page: Page): Promise<string> {
  return new Promise((resolve) => {
    page.once('dialog', (dialog) => {
      const message = dialog.message();
      void dialog.accept().then(() => resolve(message));
    });
  });
}

function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** Today in the browser's time zone (the same machine's), as Angular's
 *  DatePipe prints it with 'd MMM y'. */
function todayLabel(): string {
  const d = new Date();
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

let spanSalt = 0;
/** A leave span that is new for this run and this test, so the dashboard card
 *  can be told apart by its dates on a database that already holds every
 *  earlier run's requests: a day from 2031 onwards, moved on by the clock
 *  second, so two runs meet only if they start a whole 300,000 s cycle apart,
 *  to the second. */
function freshSpan(days: number): { from: string; to: string } {
  spanSalt += 1;
  const day = 86_400_000;
  const offset = (Math.floor(Date.now() / 1000) + spanSalt * 1000) % 300_000;
  const from = Date.UTC(2031, 0, 1) + offset * day;
  return { from: isoDay(from), to: isoDay(from + (days - 1) * day) };
}

/** `dateSpan` in leave.component.ts. */
function spanText(span: { from: string; to: string }): string {
  return span.from === span.to ? span.from : `${span.from} — ${span.to}`;
}

interface LeaveRow {
  id: string;
  from_date: string;
  to_date: string;
  status: string;
}

/** Submits a leave request as the faculty member, through the page's session. */
async function submitLeave(
  page: Page,
  kind: string,
  span: { from: string; to: string },
  reason: string,
): Promise<LeaveRow> {
  const response = await page.request.post('/api/leaves', {
    data: { from_date: span.from, to_date: span.to, reason, leave_kind: kind, alt_rows: [] },
  });
  expect(response.status(), 'arrange: the leave request is submitted').toBe(201);
  return (await response.json()) as LeaveRow;
}

/** Withdraws the faculty member's own requests for `span`, if still open. */
async function withdrawLeave(page: Page, span: { from: string; to: string }): Promise<void> {
  const mine = (await (await page.request.get('/api/leaves/mine')).json()) as LeaveRow[];
  for (const row of mine) {
    if (row.from_date === span.from && ['SUBMITTED', 'FIRST_APPROVED'].includes(row.status)) {
      await page.request.post(`/api/leaves/${row.id}/cancel`);
    }
  }
}

interface EvidenceRow {
  id: string;
  badge_code: string;
  title: string;
  status: string;
  review_note: string | null;
}
interface BadgeRow {
  code: string;
  status: string;
  evidence: EvidenceRow[];
}
interface UploadRow {
  id: string;
  title: string;
  status: string;
  review_note: string | null;
}

async function studentBadge(student: APIRequestContext, code: string): Promise<BadgeRow> {
  const board = (await (await student.get('/api/student/badges')).json()) as {
    categories: { badges: BadgeRow[] }[];
  };
  const badge = board.categories.flatMap((c) => c.badges).find((b) => b.code === code);
  if (!badge) throw new Error(`the student's board has no badge ${code}`);
  return badge;
}

async function studentUpload(student: APIRequestContext, id: string): Promise<UploadRow> {
  const uploads = (await (await student.get('/api/student/uploads')).json()) as UploadRow[];
  const upload = uploads.find((u) => u.id === id);
  if (!upload) throw new Error(`the student has no upload ${id}`);
  return upload;
}

/** Uploads sample.pdf as the student, under a file name unique to the run. */
async function uploadAsStudent(
  student: APIRequestContext,
  kind: 'CERTIFICATE_PROOF' | 'DOCUMENT',
  title: string,
  fileName: string,
): Promise<string> {
  const response = await student.post('/api/student/uploads', {
    multipart: {
      file: { name: fileName, mimeType: 'application/pdf', buffer: fs.readFileSync(FIXTURE.pdf) },
      kind,
      title,
    },
  });
  expect(response.status(), `arrange: the student uploads ${fileName}`).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

/** This module's own uploads, by title, so an interrupted run's leftovers can
 *  be cleared without touching anything another module wrote. */
const OWN_UPLOAD = /^E2E (SQL (verify|changes|reject) certificate|offer letter|report) /;

/**
 * Clears what an INTERRUPTED earlier run left behind: pending claims of this
 * module's on the SQL badge (rejected by the faculty member, through the
 * page's session) and this module's uploads (deleted by the student). With
 * them gone, the claim a test files is the only SQL claim in the queue.
 */
async function clearLeftovers(page: Page, student: APIRequestContext): Promise<void> {
  const pending = (await (
    await page.request.get('/api/mentor/badge-evidence/pending')
  ).json()) as EvidenceRow[];
  for (const claim of pending) {
    if (claim.badge_code === SQL_BADGE.code && claim.title.startsWith('E2E ')) {
      await page.request.post(`/api/mentor/badge-evidence/${claim.id}/review`, {
        data: { decision: 'REJECT', note: 'E2E clean-up: left over from an interrupted run' },
      });
    }
  }
  const uploads = (await (await student.get('/api/student/uploads')).json()) as UploadRow[];
  for (const upload of uploads) {
    if (OWN_UPLOAD.test(upload.title)) await student.delete(`/api/student/uploads/${upload.id}`);
  }
}

interface Claim {
  title: string;
  fileName: string;
  uploadId: string;
  evidenceId: string;
}

/** Files a claim on the SQL badge as the student: a certificate, then the
 *  evidence that stands on it, exactly as the Skilling screen posts them. */
async function fileSqlClaim(student: APIRequestContext, label: string): Promise<Claim> {
  const title = `E2E SQL ${label} ${RUN}`;
  const fileName = `e2e-${label}-${RUN}.pdf`;
  const uploadId = await uploadAsStudent(
    student,
    'CERTIFICATE_PROOF',
    `E2E SQL ${label} certificate ${RUN}`,
    fileName,
  );
  const response = await student.post(`/api/student/badges/${SQL_BADGE.code}/evidence`, {
    data: {
      evidence_type: 'EXTERNAL_VERIFIED',
      upload_id: uploadId,
      title,
      provider: 'E2E Academy',
      note: `Filed by the end-to-end run ${RUN}.`,
    },
  });
  expect(response.status(), 'arrange: the student files the claim').toBe(201);
  const evidence = (await studentBadge(student, SQL_BADGE.code)).evidence.find(
    (e) => e.title === title,
  );
  if (!evidence) throw new Error(`the claim ${title} is not on the student's board`);
  return { title, fileName, uploadId, evidenceId: evidence.id };
}

/** The pending claim card for the SQL badge (see `clearLeftovers`). */
function sqlClaimCard(page: Page): Locator {
  return page
    .locator('.card.claim')
    .filter({ has: page.getByText(SQL_BADGE.name, { exact: true }) });
}

/** The Documents card for an upload, by its unique title. */
function documentCard(page: Page, title: string): Locator {
  return page.locator('.card.claim').filter({ hasText: title });
}

/** The value cell beside a label in the alumni profile's facts list. */
function fact(page: Page, label: string): Locator {
  return page.locator(`dl.profile-facts dt:text-is("${label}") + dd`);
}

/** `formatSize` in alumni-profile.component.ts. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/** The faculty member's students, through the page's session. */
async function menteeIds(page: Page): Promise<string[]> {
  const mentees = (await (await page.request.get('/api/mentor/mentees')).json()) as {
    student_id: string;
  }[];
  return mentees.map((m) => m.student_id);
}

interface AlumniProfile {
  created: boolean;
  company: string | null;
  resume: { original_name: string; size_bytes: number } | null;
}

async function alumniProfile(page: Page): Promise<AlumniProfile> {
  return (await (await page.request.get('/api/alumni/profile')).json()) as AlumniProfile;
}

// ------------------------------------------------------------------ tests --

test.describe('Faculty and alumni', () => {
  // ---------------------------------------------------------- notebook --

  test('Faculty notebook shows the assigned student and the log form @TC-400', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    // By role: the list's label wraps it, so the label's text includes the
    // selected option and a `getByLabel('Student', { exact: true })` misses.
    const studentList = page.getByRole('combobox', { name: 'Student', exact: true });

    await test.step('1. Open /mentor/notebook', async () => {
      await page.goto('/mentor/notebook');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Faculty notebook' }),
        'TC-400 ER-1: the page is headed "Faculty notebook"',
      ).toBeVisible();
      await expect(
        page.getByText('Staff · private notebook', { exact: true }),
        'TC-400 ER-1: the eyebrow reads "Staff · private notebook"',
      ).toBeVisible();
      await expect(
        chip(page, 'Private by default'),
        'TC-400 ER-1: the "Private by default" chip is shown',
      ).toBeVisible();
      await expect(
        page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Notebook' }),
        'TC-400 ER-1: the sidebar highlights Notebook',
      ).toHaveClass(/\bactive\b/);
      await expect(
        page.getByText(
          "Entries stay staff-private until you publish them to the student's Mentor Meeting Log.",
        ),
        'TC-400 ER-1: the footer names the publishing rule',
      ).toBeVisible();
      await expect(
        page.getByRole('columnheader'),
        'TC-400 ER-1: the log has the printed columns',
      ).toHaveText(['Date', 'Key discussions', 'Follow up', 'Remarks', 'Actions']);
    });

    await test.step('2. Open the "Student" list at the top of the Mentoring log card', async () => {
      await studentList.click();
      await expect(
        studentList.locator('option'),
        'TC-400 ER-2: exactly one student, the assigned one, is offered',
      ).toHaveText([`${STUDENT.name} · ${STUDENT.usn}`]);
      await expect(
        studentList.locator('option:checked'),
        'TC-400 ER-2: the assigned student is selected',
      ).toHaveText(`${STUDENT.name} · ${STUDENT.usn}`);
      await studentList.press('Escape');
    });

    await test.step('3. Click Add entry', async () => {
      await page.getByRole('button', { name: 'Add entry' }).click();
      const today = new Date();
      const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      await expect(
        page.getByLabel('Date', { exact: true }),
        "TC-400 ER-3: the Date field holds today's date",
      ).toHaveValue(iso);
      await expect(
        page.getByLabel('Key discussions'),
        'TC-400 ER-3: "Key discussions" is offered',
      ).toBeVisible();
      await expect(
        page.getByLabel('Follow up'),
        'TC-400 ER-3: "Follow up" is offered',
      ).toBeVisible();
      await expect(
        page.getByLabel('Remarks').locator('option'),
        'TC-400 ER-3: the four printed remarks are offered',
      ).toHaveText(['On track', 'Watch', 'Done', 'Escalate']);
      await expect(page.getByLabel('Remarks'), 'TC-400 ER-3: On track is chosen').toHaveValue(
        'On track',
      );
      await expect(
        page.getByRole('button', { name: 'Save entry' }),
        'TC-400 ER-3: Save entry is offered',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Close', exact: true }),
        'TC-400 ER-3: the toggle now reads Close',
      ).toBeVisible();
    });

    await test.step('4. Click Close', async () => {
      await page.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(page.getByLabel('Key discussions'), 'TC-400 ER-4: the form closes').toBeHidden();
      await expect(
        page.getByRole('button', { name: 'Add entry' }),
        'TC-400 ER-4: the toggle reads Add entry again',
      ).toBeVisible();
    });
  });

  test('Add, publish and delete a notebook entry @TC-401', async ({
    page,
    signIn,
    browser,
    baseURL,
  }) => {
    await signIn('mentor');
    const discussion = `E2E notebook ${RUN}: reviewed the internship shortlist`;
    const followUp = 'Send two applications by Friday';
    const row = page.getByRole('row').filter({ hasText: discussion });
    const publishButton = row.getByRole('button', {
      name: "Publish to the student's Mentor Meeting Log",
    });
    let student: Page | undefined;

    try {
      await test.step('1. Open /mentor/notebook', async () => {
        await page.goto('/mentor/notebook');
        await expect(page.getByRole('combobox', { name: 'Student', exact: true })).toHaveValue(
          /.+/,
        );
      });

      await test.step('2. Click Add entry', async () => {
        await page.getByRole('button', { name: 'Add entry' }).click();
      });

      await test.step('3. Enter the date in the "Date" field, the text in the "Key discussions" and "Follow up" fields, and choose Watch under "Remarks"', async () => {
        await page.getByLabel('Date', { exact: true }).fill('2026-09-15');
        await page.getByLabel('Key discussions').fill(discussion);
        await page.getByLabel('Follow up').fill(followUp);
        await page.getByLabel('Remarks').selectOption('Watch');
      });

      await test.step('4. Click Save entry', async () => {
        await page.getByRole('button', { name: 'Save entry' }).click();
        await expect(
          page.getByLabel('Key discussions'),
          'TC-401 ER-1: the form closes',
        ).toBeHidden();
        await expect(row, 'TC-401 ER-1: the log gains the new row').toHaveCount(1);
        await expect(
          row.getByRole('cell').first(),
          'TC-401 ER-1: the row is dated 15 Sep 2026',
        ).toHaveText('15 Sep 2026');
        await expect(
          row.getByRole('cell').nth(2),
          'TC-401 ER-1: the follow-up is in its column',
        ).toHaveText(followUp);
        await expect(
          row.getByRole('cell').nth(3),
          'TC-401 ER-1: the remark is a "Watch" chip',
        ).toHaveText('Watch');
        await expect(
          row.locator('.nb-pub'),
          'TC-401 ER-1: the new row is not published',
        ).toHaveCount(0);
        await expect(publishButton, 'TC-401 ER-1: the row offers the publish button').toBeVisible();
      });

      await test.step('5. On the new row, click the publish button ("Publish to the student\'s Mentor Meeting Log")', async () => {
        await publishButton.click();
        await expect(
          row.locator('.nb-pub'),
          'TC-401 ER-2: the row is marked Published',
        ).toContainText('Published');
        await expect(publishButton, 'TC-401 ER-2: the publish button is gone').toHaveCount(0);
      });

      await test.step('6. In the second browser, sign in as the student and open /student/mentor-log', async () => {
        student = await openAsStudent(browser, baseURL!, '/student/mentor-log');
        const meeting = student.locator('.meeting').filter({ hasText: discussion });
        await expect(meeting, "TC-401 ER-3: the student's log lists the entry").toBeVisible();
        await expect(meeting, 'TC-401 ER-3: it is logged by the faculty member').toContainText(
          `Logged by ${MENTOR_NAME}`,
        );
      });

      await test.step('7. Back in the faculty browser, click the delete button ("Delete entry") on the row, and accept the confirmation', async () => {
        const question = acceptNextDialog(page);
        await row.getByRole('button', { name: 'Delete entry' }).click();
        expect(await question, 'TC-401 ER-4: the browser asks before removing').toBe(
          'Remove this entry? The student can already see it on their Mentor Meeting Log; it will disappear from there too.',
        );
        await expect(row, 'TC-401 ER-4: the row leaves the notebook').toHaveCount(0);
      });

      await test.step("8. In the student's browser, reload /student/mentor-log", async () => {
        await student!.reload();
        await expect(
          student!.getByRole('heading', { level: 1, name: 'Faculty / TPO Log' }),
        ).toBeVisible();
        await expect(student!.locator('.meeting').first()).toBeVisible();
        await expect(
          student!.locator('.meeting').filter({ hasText: discussion }),
          "TC-401 ER-5: the entry is gone from the student's log",
        ).toHaveCount(0);
      });
    } finally {
      await student?.context().close();
      // A failure before step 7 would leave the entry behind: archive it.
      for (const studentId of await menteeIds(page)) {
        const entries = (await (
          await page.request.get(`/api/v1/mentor/notebook/students/${studentId}/entries`)
        ).json()) as { id: string; body: string }[];
        for (const left of entries.filter((e) => e.body === discussion)) {
          await page.request.post(`/api/v1/mentor/notebook/entries/${left.id}/archive`, {
            headers: { 'Idempotency-Key': `e2e-${RUN}-${left.id}` },
          });
        }
      }
    }
  });

  test('A notebook entry is refused without the key discussions @TC-402', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    const followUp = `E2E follow-up ${RUN}`;

    await test.step('1. Open /mentor/notebook', async () => {
      await page.goto('/mentor/notebook');
      await expect(page.getByRole('combobox', { name: 'Student', exact: true })).toHaveValue(/.+/);
    });

    await test.step('2. Click Add entry', async () => {
      await page.getByRole('button', { name: 'Add entry' }).click();
    });

    await test.step('3. Leave "Key discussions" empty and enter the text in the "Follow up" field', async () => {
      await page.getByLabel('Follow up').fill(followUp);
    });

    await test.step('4. Click Save entry', async () => {
      await page.getByRole('button', { name: 'Save entry' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'Write the key discussions first.' }),
        'TC-402 ER-1: the form says the key discussions are needed',
      ).toBeVisible();
      await expect(
        page.getByLabel('Follow up'),
        'TC-402 ER-1: the form stays open with the follow-up in it',
      ).toHaveValue(followUp);
      await expect(
        page.getByRole('row').filter({ hasText: followUp }),
        'TC-402 ER-1: no row is added',
      ).toHaveCount(0);
    });
  });

  // -------------------------------------------------------- mentee log --

  test('Mentee Log lists the assigned student and their meeting notes @TC-403', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');

    await test.step('1. Open /mentor/mentees', async () => {
      await page.goto('/mentor/mentees');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Mentee Log' }),
        'TC-403 ER-1: the page is headed "Mentee Log"',
      ).toBeVisible();
      await expect(
        page.getByText('Your mentees and the 1:1 meeting notes they can read.'),
        'TC-403 ER-1: the sub-heading is shown',
      ).toBeVisible();
      const rows = page.locator('.ml-rows').getByRole('button');
      await expect(rows, 'TC-403 ER-1: one student is listed').toHaveCount(1);
      await expect(rows.first(), 'TC-403 ER-1: it is the assigned student').toContainText(
        STUDENT.name,
      );
      await expect(rows.first(), 'TC-403 ER-1: with USN, semester and stage').toContainText(
        `${STUDENT.usn} · Sem 2 · EXCEL_ADVANCED`,
      );
      await expect(rows.first(), 'TC-403 ER-1: the student is selected').toHaveClass(/\bactive\b/);

      await expect(
        page.getByText(`Log a meeting with ${STUDENT.name}`),
        'TC-403 ER-2: the form names the selected student',
      ).toBeVisible();
      await expect(page.getByLabel('Heading'), 'TC-403 ER-2: "Heading (optional)"').toBeVisible();
      await expect(page.getByLabel('Linked action'), 'TC-403 ER-2: "Linked action"').toBeVisible();
      await expect(page.getByLabel('Meeting note'), 'TC-403 ER-2: "Meeting note"').toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Save note' }),
        'TC-403 ER-2: Save note is offered',
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Full record' }),
        'TC-403 ER-2: mentoring the student is enough for a "Full record" link',
      ).toBeVisible();

      const seeded = page.locator('.ml-note').filter({
        hasText: 'Discussed placement readiness; strong on analytics, work on GD delivery.',
      });
      await expect(seeded, "TC-403 ER-3: the seed's note is listed").toBeVisible();
      await expect(
        seeded.locator('.ml-note__head strong'),
        'TC-403 ER-3: under the heading "1:1 review · Cabin 3"',
      ).toHaveText('1:1 review · Cabin 3');
      await expect(
        chip(seeded, '1:1 scheduled'),
        'TC-403 ER-3: with a "1:1 scheduled" chip',
      ).toBeVisible();
    });

    await test.step('2. Click "Full record"', async () => {
      await page.getByRole('link', { name: 'Full record' }).click();
      await expect(page, 'TC-403 ER-4: the record opens at /admin/students/<id>').toHaveURL(
        /\/admin\/students\/[0-9a-f]+$/,
      );
      await expect(
        page.getByRole('heading', { level: 1, name: STUDENT.name, exact: true }),
        'TC-403 ER-4: for Test Student',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Edit profile' }),
        'TC-403 ER-4: read only: no Edit button',
      ).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: /^Remove/ }),
        'TC-403 ER-4: read only: no Remove button',
      ).toHaveCount(0);
      await expect(
        page.getByRole('tablist', { name: 'Parts of the record' }).getByRole('tab'),
        "TC-403 ER-4: eight tabs and no Access tab, which is the Main Admin's alone",
      ).toHaveText([
        'Overview',
        'Resume',
        'Skills',
        'Leaderboard',
        'Academics',
        'Interviews',
        'Time sheet',
        'Documents',
      ]);
      await expect(
        page.getByRole('heading', { name: 'Who can open this record' }),
        'TC-403 ER-4: no "Who can open this record" card',
      ).toHaveCount(0);
    });
  });

  test('Search the mentee list by name or USN @TC-404', async ({ page, signIn }) => {
    await signIn('mentor');
    const search = page.getByPlaceholder('Search name or USN');
    const listed = page.locator('.ml-rows').getByRole('button', { name: new RegExp(STUDENT.name) });

    await test.step('1. Open /mentor/mentees', async () => {
      await page.goto('/mentor/mentees');
      await expect(listed).toBeVisible();
    });

    await test.step('2. Enter zzz-nobody in the "Search name or USN" box', async () => {
      await search.fill('zzz-nobody');
      await expect(
        page.getByText('No student matches that search.'),
        'TC-404 ER-1: the list says nobody matches',
      ).toBeVisible();
      await expect(listed, 'TC-404 ER-1: no student is listed').toHaveCount(0);
    });

    await test.step('3. Replace the search with 1bg24mba001', async () => {
      await search.fill('1bg24mba001');
      await expect(listed, 'TC-404 ER-2: the USN, in lower case, finds the student').toBeVisible();
      await expect(
        page.getByText('No student matches that search.'),
        'TC-404 ER-2: the empty message is gone',
      ).toBeHidden();
    });
  });

  test('Save and delete a meeting note @TC-405', async ({ page, signIn, browser, baseURL }) => {
    await signIn('mentor');
    const heading = `E2E review ${RUN}`;
    const text = `E2E note ${RUN}: practise one GD topic a day`;
    const note = page.locator('.ml-note').filter({ hasText: text });
    let student: Page | undefined;

    try {
      await test.step('1. Open /mentor/mentees', async () => {
        await page.goto('/mentor/mentees');
        await expect(page.getByText(`Log a meeting with ${STUDENT.name}`)).toBeVisible();
      });

      await test.step('2. Enter the heading in "Heading (optional)", choose "Flagged for follow-up" under "Linked action", and enter the note in "Meeting note"', async () => {
        await page.getByLabel('Heading').fill(heading);
        await page.getByLabel('Linked action').selectOption({ label: 'Flagged for follow-up' });
        await page.getByLabel('Meeting note').fill(text);
      });

      await test.step('3. Click Save note', async () => {
        await page.getByRole('button', { name: 'Save note' }).click();
        await expect(chip(page, 'Saved'), 'TC-405 ER-1: a "Saved" chip shows').toBeVisible();
        await expect(
          page.getByLabel('Meeting note'),
          'TC-405 ER-1: the form is emptied',
        ).toHaveValue('');
        await expect(note, 'TC-405 ER-1: the note is listed').toBeVisible();
        await expect(
          note.locator('.ml-note__head strong'),
          'TC-405 ER-1: under its heading',
        ).toHaveText(heading);
        await expect(
          chip(note, 'Flagged for follow-up'),
          'TC-405 ER-1: with the linked action as a chip',
        ).toBeVisible();
      });

      await test.step('4. Reload the page', async () => {
        await page.reload();
        await expect(note, 'TC-405 ER-2: the note is still listed after the reload').toBeVisible();
      });

      await test.step('5. In the second browser, sign in as the student and open /student/mentor-log', async () => {
        student = await openAsStudent(browser, baseURL!, '/student/mentor-log');
        const meeting = student.locator('.meeting').filter({ hasText: text });
        await expect(meeting, "TC-405 ER-3: the note is on the student's log").toBeVisible();
        await expect(meeting, 'TC-405 ER-3: under its heading').toContainText(heading);
        await expect(meeting, 'TC-405 ER-3: logged by the faculty member').toContainText(
          `Logged by ${MENTOR_NAME}`,
        );
      });

      await test.step('6. Back in the faculty browser, click the delete button ("Delete note") on the new note, and accept the confirmation', async () => {
        const question = acceptNextDialog(page);
        await note.getByRole('button', { name: 'Delete note' }).click();
        expect(await question, 'TC-405 ER-4: the browser asks before deleting').toBe(
          'Delete this note? It disappears from the student’s Mentor Meeting Log as well.',
        );
        await expect(note, 'TC-405 ER-4: the note leaves the list').toHaveCount(0);
      });

      await test.step("7. In the student's browser, reload /student/mentor-log", async () => {
        await student!.reload();
        await expect(student!.locator('.meeting').first()).toBeVisible();
        await expect(
          student!.locator('.meeting').filter({ hasText: text }),
          "TC-405 ER-5: the note is gone from the student's log",
        ).toHaveCount(0);
      });
    } finally {
      await student?.context().close();
      // A failure before step 6 would leave the note behind: take it back.
      for (const studentId of await menteeIds(page)) {
        const notes = (await (
          await page.request.get(`/api/mentor/students/${studentId}/notes`)
        ).json()) as { id: string; note_text: string }[];
        for (const left of notes.filter((n) => n.note_text === text)) {
          await page.request.delete(`/api/mentor/students/${studentId}/notes/${left.id}`);
        }
      }
    }
  });

  test('A meeting note is refused when it is empty @TC-406', async ({ page, signIn }) => {
    await signIn('mentor');
    const heading = `E2E empty ${RUN}`;

    await test.step('1. Open /mentor/mentees', async () => {
      await page.goto('/mentor/mentees');
      await expect(page.getByText(`Log a meeting with ${STUDENT.name}`)).toBeVisible();
    });

    await test.step('2. Enter the heading in "Heading (optional)" and three spaces in "Meeting note"', async () => {
      await page.getByLabel('Heading').fill(heading);
      await page.getByLabel('Meeting note').fill('   ');
    });

    await test.step('3. Click Save note', async () => {
      await page.getByRole('button', { name: 'Save note' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'Write the note first' }),
        'TC-406 ER-1: the warning shows beside Save note',
      ).toBeVisible();
      await expect(page.getByLabel('Heading'), 'TC-406 ER-1: the heading stays').toHaveValue(
        heading,
      );
      await expect(
        page.locator('.ml-note').filter({ hasText: heading }),
        'TC-406 ER-1: no note is added',
      ).toHaveCount(0);
    });
  });

  // ----------------------------------------------------- verifications --

  test('Verifications lists the pending skill claims and documents the faculty member may verify @TC-407', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    const seededClaim = page
      .locator('.card.claim')
      .filter({ has: page.getByText('Business Analytics Fundamentals', { exact: true }) });

    await test.step('1. Open /mentor/verifications', async () => {
      await page.goto('/mentor/verifications');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Verifications' }),
        'TC-407 ER-1: the page is headed "Verifications"',
      ).toBeVisible();
      await expect(
        page.getByText('Faculty · skill verification', { exact: true }),
        'TC-407 ER-1: the eyebrow reads "Faculty · skill verification"',
      ).toBeVisible();
      await expect(
        page.locator('.queue-chip').filter({ hasText: /Skill claims\s+\d+/ }),
        'TC-407 ER-1: the header counts the skill claims',
      ).toBeVisible();
      await expect(
        page.locator('.queue-chip').filter({ hasText: /Documents\s+\d+/ }),
        'TC-407 ER-1: the header counts the documents',
      ).toBeVisible();

      await expect(seededClaim, "TC-407 ER-2: the seed's pending claim is listed").toHaveCount(1);
      await expect(
        seededClaim.locator('.claim-lvl'),
        'TC-407 ER-2: with its category and kind of evidence',
      ).toHaveText('· Sectoral Skills · Certificate');
      await expect(
        seededClaim.locator('.claim-who'),
        'TC-407 ER-2: filed by Test Student, with the date',
      ).toHaveText(/^\s*Test Student · submitted \d{1,2} [A-Z][a-z]{2} \d{4}\s*$/);
      await expect(chip(seededClaim, 'Submitted'), 'TC-407 ER-2: a "Submitted" chip').toBeVisible();
      await expect(
        seededClaim.getByRole('button', { name: 'Review evidence' }),
        'TC-407 ER-2: a Review evidence button',
      ).toBeVisible();

      for (const [title, kind, file] of [
        ['Profile photo', 'Photo', 'me.png'],
        ['Leadership certificate', 'Certificate', 'leadership_completion.pdf'],
      ] as const) {
        const card = documentCard(page, title);
        await expect(card, `TC-407 ER-3: the document "${title}" is listed`).toHaveCount(1);
        await expect(card.locator('.claim-lvl'), `TC-407 ER-3: as a ${kind}`).toHaveText(
          `· ${kind}`,
        );
        await expect(card.locator('.claim-who'), 'TC-407 ER-3: for Test Student').toContainText(
          `${STUDENT.name} · uploaded`,
        );
        await expect(chip(card, 'Pending review'), 'TC-407 ER-3: "Pending review"').toBeVisible();
        await expect(
          card.getByRole('link', { name: `Open ${file}` }),
          'TC-407 ER-3: a link naming the file',
        ).toBeVisible();
        await expect(
          card.getByLabel('Reviewer note'),
          'TC-407 ER-3: a "Reviewer note" field',
        ).toBeVisible();
        await expect(
          card.getByRole('button', { name: 'Verify' }),
          'TC-407 ER-3: Verify',
        ).toBeVisible();
        await expect(
          card.getByRole('button', { name: 'Reject' }),
          'TC-407 ER-3: Reject',
        ).toBeVisible();
      }
    });

    await test.step('2. On the "Business Analytics Fundamentals" claim, click Review evidence', async () => {
      await seededClaim.getByRole('button', { name: 'Review evidence' }).click();
      await expect(
        chip(seededClaim, 'Under review'),
        'TC-407 ER-4: the chip reads "Under review"',
      ).toBeVisible();
      const grid = seededClaim.locator('.claim-grid');
      await expect(grid.locator('.fld'), 'TC-407 ER-4: the claim facts are labelled').toHaveText([
        'Badge claimed',
        'Category',
        'Evidence',
        'Issued by',
        'Student',
        'Submitted',
      ]);
      await expect(grid.locator('.val').nth(0), 'TC-407 ER-4: badge claimed').toHaveText(
        'Business Analytics Fundamentals',
      );
      await expect(grid.locator('.val').nth(1), 'TC-407 ER-4: category').toHaveText(
        'Sectoral Skills',
      );
      await expect(grid.locator('.val').nth(2), 'TC-407 ER-4: evidence').toHaveText(
        'Certificate · Business Analytics Fundamentals',
      );
      await expect(grid.locator('.val').nth(3), 'TC-407 ER-4: issued by').toHaveText('Coursera');
      await expect(grid.locator('.val').nth(4), 'TC-407 ER-4: the student and USN').toHaveText(
        `${STUDENT.name} · ${STUDENT.usn}`,
      );
      await expect(
        seededClaim.locator('.claim-note-block .val'),
        "TC-407 ER-4: the student's own note",
      ).toHaveText('Completed last week — certificate attached on my uploads.');
      await expect(
        chip(seededClaim, 'No file attached'),
        'TC-407 ER-4: "No file attached"',
      ).toBeVisible();
      await expect(
        seededClaim.getByText('Verification rubric'),
        'TC-407 ER-4: the verification rubric',
      ).toBeVisible();
      await expect(
        seededClaim.getByLabel('Note to the student'),
        'TC-407 ER-4: the "Note to the student" field',
      ).toBeVisible();
      for (const name of ['Verify', 'Request changes', 'Reject', 'Close']) {
        await expect(
          seededClaim.getByRole('button', { name, exact: true }),
          `TC-407 ER-4: the ${name} button`,
        ).toBeVisible();
      }
    });

    await test.step('3. Click Close', async () => {
      await seededClaim.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(
        chip(seededClaim, 'Submitted'),
        'TC-407 ER-5: back to the "Submitted" summary',
      ).toBeVisible();
      await expect(
        seededClaim.getByRole('button', { name: 'Review evidence' }),
        'TC-407 ER-5: the card is closed',
      ).toBeVisible();
      const pending = (await (
        await page.request.get('/api/mentor/badge-evidence/pending')
      ).json()) as EvidenceRow[];
      expect(
        pending.some((c) => c.title === 'Business Analytics Fundamentals'),
        'TC-407 ER-5: the claim is still waiting for review',
      ).toBe(true);
    });
  });

  test('Verify a skill claim @TC-408', async ({ page, signIn, playwright, baseURL }) => {
    await signIn('mentor');
    const student = await apiAs(playwright.request, baseURL!, 'student');
    const admin = await apiAs(playwright.request, baseURL!, 'admin');
    const revokeSql = () =>
      admin.api.post(`/api/mentor/students/${student.studentId}/badges/${SQL_BADGE.code}/revoke`, {
        data: { note: `E2E clean-up ${RUN}: the badge was awarded by a test` },
      });
    const note = `E2E verified ${RUN}`;
    let claim: Claim | undefined;
    try {
      await clearLeftovers(page, student.api);
      if ((await studentBadge(student.api, SQL_BADGE.code)).status === 'EARNED') await revokeSql();
      claim = await fileSqlClaim(student.api, 'verify');
      const card = sqlClaimCard(page);

      await test.step('1. Open /mentor/verifications', async () => {
        await page.goto('/mentor/verifications');
        await expect(card).toHaveCount(1);
      });

      await test.step('2. On the "SQL" claim, click Review evidence', async () => {
        await card.getByRole('button', { name: 'Review evidence' }).click();
        const values = card.locator('.claim-grid .val');
        await expect(values.nth(0), 'TC-408 ER-1: badge claimed').toHaveText(SQL_BADGE.name);
        await expect(values.nth(1), 'TC-408 ER-1: category').toHaveText(SQL_BADGE.category);
        await expect(values.nth(2), 'TC-408 ER-1: evidence').toHaveText(
          `Certificate · ${claim!.title}`,
        );
        await expect(values.nth(3), 'TC-408 ER-1: issued by').toHaveText('E2E Academy');
        await expect(
          card.getByRole('link', { name: claim!.fileName }),
          'TC-408 ER-1: the certificate is named on a link',
        ).toBeVisible();
      });

      await test.step("3. Click the certificate's file name", async () => {
        const download = page.waitForEvent('download');
        await card.getByRole('link', { name: claim!.fileName }).click();
        expect(
          (await download).suggestedFilename(),
          'TC-408 ER-2: the certificate downloads under its file name',
        ).toBe(claim!.fileName);
      });

      await test.step('4. Enter the note in "Note to the student"', async () => {
        await card.getByLabel('Note to the student').fill(note);
      });

      await test.step('5. Click Verify', async () => {
        await card.getByRole('button', { name: 'Verify', exact: true }).click();
        await expect(card, 'TC-408 ER-3: the claim leaves the queue').toHaveCount(0);
        const reviewed = page.locator('.history-row');
        await expect(
          reviewed.first(),
          'TC-408 ER-3: "Recently reviewed" lists it first',
        ).toContainText(`“${note}”`);
        await expect(
          chip(reviewed.first(), 'Verified'),
          'TC-408 ER-3: with a "Verified" chip',
        ).toBeVisible();
        await expect(
          reviewed.first().locator('.history-text'),
          'TC-408 ER-3: naming the badge, the student and the day',
        ).toHaveText(
          new RegExp(
            `^\\s*SQL\\s*·\\s*${STUDENT.name}\\s*·\\s*${todayLabel()}\\s*·\\s*“${note}”\\s*$`,
          ),
        );
        await expect(
          page.locator('.card.claim').filter({ hasText: claim!.fileName }),
          'TC-408 ER-3: the certificate is not under Documents',
        ).toHaveCount(0);

        expect(
          (await studentBadge(student.api, SQL_BADGE.code)).status,
          'TC-408 ER-4: the student has earned the SQL badge',
        ).toBe('EARNED');
        const upload = await studentUpload(student.api, claim!.uploadId);
        expect(upload.status, 'TC-408 ER-4: the certificate is Verified').toBe('VERIFIED');
        expect(upload.review_note, "TC-408 ER-4: with the reviewer's note").toBe(note);
      });
    } finally {
      if ((await studentBadge(student.api, SQL_BADGE.code)).status === 'EARNED') await revokeSql();
      if (claim) await student.api.delete(`/api/student/uploads/${claim.uploadId}`);
      await student.api.dispose();
      await admin.api.dispose();
    }
  });

  for (const variant of [
    {
      id: 'TC-409',
      title: 'Request changes on a skill claim, which needs a note',
      label: 'changes',
      button: 'Request changes',
      refusal:
        'Say what needs to change — the note is all the student is told, on screen and by email.',
      note: `E2E changes ${RUN}: the certificate has no date`,
      chip: 'Needs changes',
      evidence: 'MORE_INFO_REQUIRED',
      upload: 'NEEDS_CHANGES',
    },
    {
      id: 'TC-410',
      title: 'Reject a skill claim, which needs a reason',
      label: 'reject',
      button: 'Reject',
      refusal:
        'Say why it is rejected — the reason is all the student is told, on screen and by email.',
      note: `E2E rejected ${RUN}: the certificate is in another name`,
      chip: 'Rejected',
      evidence: 'REJECTED',
      upload: 'REJECTED',
    },
  ] as const) {
    test(`${variant.title} @${variant.id}`, async ({ page, signIn, playwright, baseURL }) => {
      await signIn('mentor');
      const student = await apiAs(playwright.request, baseURL!, 'student');
      let claim: Claim | undefined;
      try {
        await clearLeftovers(page, student.api);
        claim = await fileSqlClaim(student.api, variant.label);
        const card = sqlClaimCard(page);
        const decide = card.getByRole('button', { name: variant.button, exact: true });

        await test.step('1. Open /mentor/verifications', async () => {
          await page.goto('/mentor/verifications');
          await expect(card).toHaveCount(1);
        });

        await test.step('2. On the "SQL" claim, click Review evidence', async () => {
          await card.getByRole('button', { name: 'Review evidence' }).click();
          await expect(card.locator('.claim-grid .val').nth(2)).toHaveText(
            `Certificate · ${claim!.title}`,
          );
        });

        await test.step(`3. Leave "Note to the student" empty and click ${variant.button}`, async () => {
          await decide.click();
          await expect(
            card.getByRole('alert'),
            `${variant.id} ER-1: the card asks for the note`,
          ).toHaveText(variant.refusal);
          await expect(decide, `${variant.id} ER-1: the card stays open`).toBeVisible();
          const pending = (await (
            await page.request.get('/api/mentor/badge-evidence/pending')
          ).json()) as EvidenceRow[];
          expect(
            pending.some((c) => c.id === claim!.evidenceId),
            `${variant.id} ER-1: the claim is still waiting for review`,
          ).toBe(true);
        });

        await test.step('4. Enter the note in "Note to the student"', async () => {
          await card.getByLabel('Note to the student').fill(variant.note);
        });

        await test.step(`5. Click ${variant.button}`, async () => {
          await decide.click();
          await expect(card, `${variant.id} ER-2: the claim leaves the queue`).toHaveCount(0);
          const reviewed = page.locator('.history-row').filter({ hasText: `“${variant.note}”` });
          await expect(reviewed, `${variant.id} ER-2: "Recently reviewed" lists it`).toHaveCount(1);
          await expect(
            chip(reviewed, variant.chip),
            `${variant.id} ER-2: with a "${variant.chip}" chip`,
          ).toBeVisible();

          const badge = await studentBadge(student.api, SQL_BADGE.code);
          const evidence = badge.evidence.find((e) => e.id === claim!.evidenceId);
          expect(
            evidence?.status,
            `${variant.id} ER-3: the student's claim carries the decision`,
          ).toBe(variant.evidence);
          expect(evidence?.review_note, `${variant.id} ER-3: with the note`).toBe(variant.note);
          expect(
            (await studentUpload(student.api, claim!.uploadId)).status,
            `${variant.id} ER-3: the certificate carries the decision too`,
          ).toBe(variant.upload);
          expect(badge.status, `${variant.id} ER-3: the badge is not earned`).not.toBe('EARNED');
        });
      } finally {
        if (claim) await student.api.delete(`/api/student/uploads/${claim.uploadId}`);
        await student.api.dispose();
      }
    });
  }

  test('Verify an uploaded document @TC-411', async ({ page, signIn, playwright, baseURL }) => {
    await signIn('mentor');
    const student = await apiAs(playwright.request, baseURL!, 'student');
    const title = `E2E offer letter ${RUN}`;
    const fileName = `e2e-doc-verify-${RUN}.pdf`;
    let uploadId: string | undefined;
    try {
      await clearLeftovers(page, student.api);
      uploadId = await uploadAsStudent(student.api, 'DOCUMENT', title, fileName);
      const card = documentCard(page, title);

      await test.step('1. Open /mentor/verifications', async () => {
        await page.goto('/mentor/verifications');
        await expect(page.getByRole('heading', { level: 1, name: 'Verifications' })).toBeVisible();
      });

      await test.step('2. Under "Documents", find the document by its title', async () => {
        await expect(card, 'TC-411 ER-1: the document is in the queue').toHaveCount(1);
        await expect(card.locator('strong').first(), 'TC-411 ER-1: by its title').toHaveText(title);
        await expect(card.locator('.claim-lvl'), 'TC-411 ER-1: as a Document').toHaveText(
          '· Document',
        );
        await expect(
          card.locator('.claim-who'),
          'TC-411 ER-1: uploaded by Test Student',
        ).toHaveText(
          new RegExp(
            `^\\s*${STUDENT.name} · uploaded \\d{1,2} [A-Z][a-z]{2} \\d{4}, \\d{2}:\\d{2}\\s*$`,
          ),
        );
        await expect(chip(card, 'Pending review'), 'TC-411 ER-1: "Pending review"').toBeVisible();
        await expect(
          card.getByRole('link', { name: `Open ${fileName}` }),
          'TC-411 ER-1: an "Open" link naming the file',
        ).toBeVisible();
      });

      await test.step('3. Click its "Open <file name>" link', async () => {
        const download = page.waitForEvent('download');
        await card.getByRole('link', { name: `Open ${fileName}` }).click();
        expect(
          (await download).suggestedFilename(),
          'TC-411 ER-2: the document downloads under its file name',
        ).toBe(fileName);
      });

      await test.step('4. Click Verify on it, leaving "Reviewer note" empty', async () => {
        await card.getByRole('button', { name: 'Verify' }).click();
        await expect(card, 'TC-411 ER-3: the card leaves the Documents queue').toHaveCount(0);
        const upload = await studentUpload(student.api, uploadId!);
        expect(upload.status, "TC-411 ER-4: the student's document reads Verified").toBe(
          'VERIFIED',
        );
      });
    } finally {
      if (uploadId) await student.api.delete(`/api/student/uploads/${uploadId}`);
      await student.api.dispose();
    }
  });

  test('Reject an uploaded document, which needs a note @TC-412', async ({
    page,
    signIn,
    playwright,
    baseURL,
  }) => {
    await signIn('mentor');
    const student = await apiAs(playwright.request, baseURL!, 'student');
    const title = `E2E report ${RUN}`;
    const note = `E2E rejected ${RUN}: this is not your report`;
    let uploadId: string | undefined;
    try {
      await clearLeftovers(page, student.api);
      uploadId = await uploadAsStudent(student.api, 'DOCUMENT', title, `e2e-doc-reject-${RUN}.pdf`);
      const card = documentCard(page, title);

      await test.step('1. Open /mentor/verifications', async () => {
        await page.goto('/mentor/verifications');
        await expect(page.getByRole('heading', { level: 1, name: 'Verifications' })).toBeVisible();
      });

      await test.step('2. Under "Documents", find the document by its title', async () => {
        await expect(card).toHaveCount(1);
      });

      await test.step('3. Click Reject on it, leaving "Reviewer note" empty', async () => {
        await card.getByRole('button', { name: 'Reject' }).click();
        await expect(
          card.getByRole('alert'),
          'TC-412 ER-1: the card asks what is wrong',
        ).toHaveText('Say what is wrong with it — this note is all the student is shown.');
        await expect(card, 'TC-412 ER-1: the card stays in the queue').toHaveCount(1);
        expect(
          (await studentUpload(student.api, uploadId!)).status,
          'TC-412 ER-1: nothing was decided',
        ).toBe('PENDING_REVIEW');
      });

      await test.step('4. Enter the note in its "Reviewer note" field', async () => {
        await card.getByLabel('Reviewer note').fill(note);
      });

      await test.step('5. Click Reject on it', async () => {
        await card.getByRole('button', { name: 'Reject' }).click();
        await expect(card, 'TC-412 ER-2: the card leaves the Documents queue').toHaveCount(0);
        const upload = await studentUpload(student.api, uploadId!);
        expect(upload.status, "TC-412 ER-3: the student's document reads Rejected").toBe(
          'REJECTED',
        );
        expect(upload.review_note, 'TC-412 ER-3: with the reviewer note').toBe(note);
      });
    } finally {
      if (uploadId) await student.api.delete(`/api/student/uploads/${uploadId}`);
      await student.api.dispose();
    }
  });

  // -------------------------------------------------------- upskilling --

  test('Upload, view and remove an Upskilling certificate @TC-413', async ({ page, signIn }) => {
    await signIn('mentor');
    const title = `E2E Power BI ${RUN}`;
    const row = page.getByRole('row').filter({ hasText: title });
    const count = page.locator('.up-count');
    let before = 0;
    try {
      await test.step('1. Open /mentor/upskilling', async () => {
        await page.goto('/mentor/upskilling');
        await expect(
          page.getByRole('heading', { level: 1, name: 'Upskilling' }),
          'TC-413 ER-1: the page is headed "Upskilling"',
        ).toBeVisible();
        await expect(
          page.getByText('Certificates for courses you have completed — your own record.'),
          'TC-413 ER-1: the sub-heading is shown',
        ).toBeVisible();
        await expect(
          page.getByText('Upload a certificate', { exact: true }),
          'TC-413 ER-1: the upload card is shown',
        ).toBeVisible();
        await expect(
          page.locator('.up-accepted'),
          'TC-413 ER-1: the accepted types and size are stated',
        ).toHaveText('Accepted: PDF, PNG, JPEG · up to 10 MB');
        await expect(
          page.locator('.up-section'),
          'TC-413 ER-1: the "Your certificates" table is headed',
        ).toHaveText(/^\s*Your certificates\s*· \d+ on file\s*$/);
        await expect(
          page.getByRole('columnheader'),
          'TC-413 ER-1: the table has its columns',
        ).toHaveText(['Certificate', 'Provider', 'Completed', 'File', 'Actions']);
        await expect(count).toHaveText(/· \d+ on file/);
        before = Number((await count.textContent())!.match(/(\d+) on file/)![1]);
      });

      await test.step('2. Enter the name in "Course / certificate name", the provider in "Provider", and the date in "Completed on"', async () => {
        await page.getByLabel('Course / certificate name').fill(title);
        await page.getByLabel('Provider').fill('E2E Academy');
        await page.getByLabel('Completed on').fill('2026-08-14');
      });

      await test.step('3. Click "Choose file & upload" and choose the file', async () => {
        const chooser = page.waitForEvent('filechooser');
        await page.getByRole('button', { name: 'Choose file & upload' }).click();
        await (await chooser).setFiles(FIXTURE.pdf);
        await expect(chip(page, 'Uploaded'), 'TC-413 ER-2: an "Uploaded" chip shows').toBeVisible();
        await expect(
          page.getByLabel('Course / certificate name'),
          'TC-413 ER-2: the name field is emptied',
        ).toHaveValue('');
        await expect(
          page.getByLabel('Provider'),
          'TC-413 ER-2: the provider field is emptied',
        ).toHaveValue('');
        await expect(row, 'TC-413 ER-2: the table gains the row').toHaveCount(1);
        const cells = row.getByRole('cell');
        await expect(cells.nth(0).locator('strong'), 'TC-413 ER-2: the name').toHaveText(title);
        await expect(cells.nth(0).locator('.up-sub'), 'TC-413 ER-2: uploaded today').toHaveText(
          `Uploaded ${todayLabel()}`,
        );
        await expect(cells.nth(1), 'TC-413 ER-2: the provider').toHaveText('E2E Academy');
        await expect(cells.nth(2), 'TC-413 ER-2: the completion date').toHaveText('14 Aug 2026');
        await expect(cells.nth(3), 'TC-413 ER-2: the file and its size').toHaveText(
          `sample.pdf · ${SAMPLE_PDF_SIZE}`,
        );
        await expect(count, 'TC-413 ER-2: one more on file').toHaveText(`· ${before + 1} on file`);
      });

      await test.step('4. On the new row, click View', async () => {
        const download = page.waitForEvent('download');
        await row.getByRole('link', { name: 'View' }).click();
        const file = await download;
        expect(file.suggestedFilename(), 'TC-413 ER-3: the certificate downloads').toBe(
          'sample.pdf',
        );
        expect(
          fs
            .readFileSync((await file.path())!)
            .subarray(0, 5)
            .toString(),
          'TC-413 ER-3: it is the PDF that was uploaded',
        ).toBe('%PDF-');
      });

      await test.step('5. On the same row, click Remove and accept the confirmation', async () => {
        const question = acceptNextDialog(page);
        await row.getByRole('button', { name: 'Remove' }).click();
        expect(await question, 'TC-413 ER-4: the browser asks before deleting').toBe(
          `Remove "${title}"? This permanently deletes the certificate.`,
        );
        await expect(row, 'TC-413 ER-4: the row leaves the table').toHaveCount(0);
        await expect(count, 'TC-413 ER-4: the count goes back down').toHaveText(
          `· ${before} on file`,
        );
      });
    } finally {
      const rows = (await (await page.request.get('/api/staff/upskilling')).json()) as {
        id: string;
        title: string;
      }[];
      for (const left of rows.filter((r) => r.title === title)) {
        await page.request.delete(`/api/staff/upskilling/${left.id}`);
      }
    }
  });

  test("Upskilling asks for the certificate's name before the file @TC-414", async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    let pickerOpened = false;
    page.on('filechooser', () => {
      pickerOpened = true;
    });

    await test.step('1. Open /mentor/upskilling', async () => {
      await page.goto('/mentor/upskilling');
      await expect(page.locator('.up-count')).toBeVisible();
    });

    await test.step('2. Leave "Course / certificate name" empty and click "Choose file & upload"', async () => {
      await page.getByRole('button', { name: 'Choose file & upload' }).click();
      await expect(
        page.getByRole('alert').filter({ hasText: 'Name the certificate first' }),
        'TC-414 ER-1: the warning asks for the name first',
      ).toBeVisible();
      expect(pickerOpened, 'TC-414 ER-1: no file picker opened').toBe(false);
    });
  });

  test('Upskilling refuses a file that is not a PDF, PNG or JPEG @TC-415', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    const title = `E2E wrong type ${RUN}`;

    await test.step('1. Open /mentor/upskilling', async () => {
      await page.goto('/mentor/upskilling');
      await expect(page.locator('.up-count')).toBeVisible();
    });

    await test.step('2. Enter the name in "Course / certificate name"', async () => {
      await page.getByLabel('Course / certificate name').fill(title);
    });

    await test.step('3. Click "Choose file & upload" and choose the text file', async () => {
      const chooser = page.waitForEvent('filechooser');
      await page.getByRole('button', { name: 'Choose file & upload' }).click();
      await (
        await chooser
      ).setFiles({
        name: 'notes.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('Plain text, not a certificate.\n'),
      });
      await expect(
        page.getByRole('alert').filter({ hasText: 'Upload failed.' }),
        'TC-415 ER-1: the server refuses the type, in its own words',
      ).toHaveText(
        endsWith('Upload failed. Unsupported file type — only PDF, PNG and JPEG are accepted.'),
      );
      await expect(
        page.getByRole('row').filter({ hasText: title }),
        'TC-415 ER-1: no row is added',
      ).toHaveCount(0);
      await expect(
        page.getByLabel('Course / certificate name'),
        'TC-415 ER-1: the name stays in its field',
      ).toHaveValue(title);
    });
  });

  // --------------------------------------------------------- signature --

  test('Upload, replace and remove the signature image @TC-416', async ({ page, signIn }) => {
    await signIn('mentor');
    // Leave it as found: a signature already on file is set aside and put back.
    const found = (await (await page.request.get('/api/staff/signature')).json()) as {
      present: boolean;
    };
    const kept = found.present
      ? await (await page.request.get('/api/staff/signature/image')).body()
      : null;
    if (kept) await page.request.delete('/api/staff/signature');

    const saved = page
      .getByRole('status')
      .filter({ hasText: 'Signature saved. It appears on your leave papers from now on.' });
    const preview = page.getByRole('img', { name: 'Your signature' });
    try {
      await test.step('1. Open the account menu (Test Mentor, top right) and choose Signature', async () => {
        await page.goto('/mentor/notebook');
        await page.getByRole('button', { name: new RegExp(MENTOR_NAME) }).click();
        await page.getByRole('menuitem', { name: 'Signature' }).click();
        await expect(page, 'TC-416 ER-1: the Signature page opens').toHaveURL(
          /\/mentor\/signature$/,
        );
        await expect(
          page.getByRole('heading', { level: 1, name: 'Signature' }),
          'TC-416 ER-1: it is headed "Signature"',
        ).toBeVisible();
        await expect(
          page.getByText('No signature on file', { exact: true }),
          'TC-416 ER-1: no signature is on file',
        ).toBeVisible();
        await expect(
          page.getByText(/PNG or JPEG,\s+under 2 MB/),
          'TC-416 ER-1: the advice',
        ).toBeVisible();
        await expect(
          page.getByLabel('Upload signature'),
          'TC-416 ER-1: "Upload signature" is offered',
        ).toBeAttached();
      });

      await test.step('2. Click "Upload signature" and choose the first image', async () => {
        await page.getByLabel('Upload signature').setInputFiles(FIXTURE.png);
        await expect(saved, 'TC-416 ER-2: the signature is saved').toBeVisible();
        await expect(preview, 'TC-416 ER-2: the image is previewed').toBeVisible();
        await expect(
          page.getByText(/On file since .+ · 1 KB · PNG/),
          'TC-416 ER-2: when, how large and what type',
        ).toBeVisible();
        await expect(page.getByLabel('Replace'), 'TC-416 ER-2: Replace is offered').toBeAttached();
        await expect(
          page.getByRole('button', { name: 'Remove…' }),
          'TC-416 ER-2: "Remove…" is offered',
        ).toBeVisible();
      });

      await test.step('3. Click Replace and choose the replacement image', async () => {
        const first = await preview.getAttribute('src');
        await page.getByLabel('Replace').setInputFiles(FIXTURE.jpg);
        await expect(
          preview,
          'TC-416 ER-3: the preview changes to the new picture',
        ).not.toHaveAttribute('src', first!);
        await expect(saved, 'TC-416 ER-3: the saved message shows').toBeVisible();
        await expect(
          page.getByText(/On file since .+ · PNG/),
          'TC-416 ER-3: a JPEG is stored as a PNG',
        ).toBeVisible();
      });

      await test.step('4. Click "Remove…"', async () => {
        await page.getByRole('button', { name: 'Remove…' }).click();
        await expect(
          page.getByText('Your papers will show your name and the time only.'),
          'TC-416 ER-4: the consequence is stated',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Yes, remove it' }),
          'TC-416 ER-4: "Yes, remove it" is offered',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Keep it' }),
          'TC-416 ER-4: "Keep it" is offered',
        ).toBeVisible();
        await expect(preview, 'TC-416 ER-4: nothing is removed yet').toBeVisible();
      });

      await test.step('5. Click "Yes, remove it"', async () => {
        await page.getByRole('button', { name: 'Yes, remove it' }).click();
        await expect(
          page.getByRole('status').filter({
            hasText: 'Signature removed. Your leave papers show your name and the time only.',
          }),
          'TC-416 ER-5: the removal is confirmed',
        ).toBeVisible();
        await expect(
          page.getByText('No signature on file', { exact: true }),
          'TC-416 ER-5: back to "No signature on file"',
        ).toBeVisible();
        await expect(preview, 'TC-416 ER-5: the preview is gone').toHaveCount(0);
      });
    } finally {
      const now = (await (await page.request.get('/api/staff/signature')).json()) as {
        present: boolean;
      };
      if (now.present) await page.request.delete('/api/staff/signature');
      if (kept) {
        await page.request.put('/api/staff/signature', {
          multipart: { file: { name: 'signature.png', mimeType: 'image/png', buffer: kept } },
        });
      }
    }
  });

  test('The signature refuses a file that is not an image @TC-417', async ({ page, signIn }) => {
    await signIn('mentor');
    const found = (await (await page.request.get('/api/staff/signature')).json()) as {
      present: boolean;
    };
    const kept = found.present
      ? await (await page.request.get('/api/staff/signature/image')).body()
      : null;
    if (kept) await page.request.delete('/api/staff/signature');
    try {
      await test.step('1. Open /mentor/signature', async () => {
        await page.goto('/mentor/signature');
        await expect(page.getByText('No signature on file', { exact: true })).toBeVisible();
      });

      await test.step('2. Click "Upload signature" and choose the PDF', async () => {
        await page.getByLabel('Upload signature').setInputFiles(FIXTURE.pdf);
        await expect(
          page.getByRole('alert'),
          'TC-417 ER-1: the server refuses a PDF, in its own words',
        ).toHaveText('A signature is an image: upload a PNG or a JPEG.');
        await expect(
          page.getByText('No signature on file', { exact: true }),
          'TC-417 ER-1: still no signature on file',
        ).toBeVisible();
      });
    } finally {
      if (kept) {
        await page.request.put('/api/staff/signature', {
          multipart: { file: { name: 'signature.png', mimeType: 'image/png', buffer: kept } },
        });
      }
    }
  });

  // ------------------------------------------------------------- leave --

  test('Submit a leave request on the official form @TC-418', async ({ page, signIn }) => {
    await signIn('mentor');
    const span = freshSpan(2);
    const purpose = `E2E leave ${RUN}: attending a workshop`;
    const sign = page.getByRole('button', { name: 'Sign & submit to Program Director' });
    const sheetRow = (label: string) =>
      page
        .getByRole('row')
        .filter({ has: page.getByRole('rowheader', { name: label, exact: true }) });
    try {
      await test.step('1. Open /mentor/leave', async () => {
        await page.goto('/mentor/leave');
        await expect(
          page.getByRole('heading', { level: 1, name: 'Leave requests' }),
          'TC-418 ER-1: the page is headed "Leave requests"',
        ).toBeVisible();
        await expect(
          page.getByText(
            'The official BGSCET leave form — sign and send to Admin / Program Director.',
          ),
          'TC-418 ER-1: the sub-heading is shown',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'New leave request' }),
          'TC-418 ER-1: New leave request is offered',
        ).toBeVisible();
        const allowance = page.locator('.lv-balance');
        await expect(
          allowance.locator('.lv-balance-head'),
          'TC-418 ER-1: the allowance card names the academic year',
        ).toHaveText(/^\s*Your leave allowance\s*\d{4}-\d{2}\s*$/);
        await expect(allowance, 'TC-418 ER-1: no allowance is recorded').toContainText(
          'The office has recorded no allowance for you this year, so no request of yours is measured against one. That is not a balance of zero.',
        );
      });

      await test.step('2. Click New leave request', async () => {
        await page.getByRole('button', { name: 'New leave request' }).click();
        await expect(
          page.getByText('|| Jai Sri Gurudev ||'),
          'TC-418 ER-2: the form is shown',
        ).toBeVisible();
        await expect(
          page.getByText('BGS COLLEGE OF ENGINEERING AND TECHNOLOGY,MBA'),
          "TC-418 ER-2: under the college's name",
        ).toBeVisible();
        await expect(sheetRow('Name'), 'TC-418 ER-2: the name is synced').toContainText(
          `${MENTOR_NAME} (synced)`,
        );
        await expect(sheetRow('Sanctioned'), 'TC-418 ER-2: Sanctioned reads Pending').toContainText(
          'Pending',
        );
        await expect(
          page.getByText('SIGNATURE OF STAFF', { exact: true }),
          'TC-418 ER-2: the staff block',
        ).toBeVisible();
        await expect(
          page.getByText('PROGRAM DIRECTOR', { exact: true }),
          'TC-418 ER-2: the director block',
        ).toBeVisible();
        await expect(sign, 'TC-418 ER-2: signing is disabled while empty').toBeDisabled();
        await expect(
          page.getByText('Dates and a purpose are needed before this can be signed.'),
          'TC-418 ER-2: and says why',
        ).toBeVisible();
      });

      await test.step('3. Click Permission on the "Application for" line', async () => {
        await page.getByRole('button', { name: 'Permission', exact: true }).click();
        await expect(
          page.getByRole('button', { name: 'Permission', exact: true }),
          'TC-418 ER-3: Permission is not struck off',
        ).not.toHaveClass(/kind--off/);
        for (const other of ['Casual Leave', 'OOD', 'RH', 'LOP']) {
          await expect(
            page.getByRole('button', { name: other, exact: true }),
            `TC-418 ER-3: ${other} is struck off`,
          ).toHaveClass(/kind--off/);
        }
      });

      await test.step('4. Enter the from date and the to date, the purpose, the credit, and the first row of the Alternate Arrangements table', async () => {
        await page.getByLabel('From date').fill(span.from);
        await page.getByLabel('To date').fill(span.to);
        await page.getByLabel('Purpose').fill(purpose);
        await page.getByLabel('Credit').fill('2 days');
        const first = page.locator('.alt-table tbody tr').first();
        await first.getByLabel('Date', { exact: true }).fill(span.from);
        await first.getByLabel('Staff name').fill('E2E Colleague');
        await first.getByLabel('Class').fill('MBA 1');
        await first.getByLabel('Time').fill('10:00');
        await first.getByLabel('Remarks').fill('Covering');
        await expect(sign, 'TC-418 ER-4: signing becomes enabled').toBeEnabled();
        await expect(
          page.getByText('Dates and a purpose are needed before this can be signed.'),
          'TC-418 ER-4: the hint goes',
        ).toBeHidden();
      });

      await test.step('5. Click "Sign & submit to Program Director"', async () => {
        await sign.click();
        await expect(
          page.getByRole('link', { name: 'Download PDF' }),
          'TC-418 ER-5: the signed request offers its paper',
        ).toBeVisible();
        await expect(sheetRow('Date'), 'TC-418 ER-5: the date span').toContainText(spanText(span));
        await expect(sheetRow('Purpose'), 'TC-418 ER-5: the purpose').toContainText(purpose);
        await expect(sheetRow('Credit'), 'TC-418 ER-5: the credit').toContainText('2 days');
        await expect(sheetRow('Sanctioned'), 'TC-418 ER-5: Sanctioned reads Pending').toContainText(
          'Pending',
        );
        const staff = page.locator('.sig-row .sig').first();
        await expect(
          staff.locator('.sig-name'),
          'TC-418 ER-5: signed by the faculty member',
        ).toHaveText(MENTOR_NAME);
        await expect(staff.locator('.sig-when'), 'TC-418 ER-5: with the signing time').toHaveText(
          new RegExp(`^${todayLabel()}, \\d{2}:\\d{2}$`),
        );
        await expect(
          page.locator('.sig-row .sig').nth(1).locator('.sig-await'),
          'TC-418 ER-5: the director block is Awaiting',
        ).toHaveText('Awaiting');
        await expect(
          page.locator('.alt-table tbody tr').first(),
          'TC-418 ER-5: the alternate arrangement is recorded',
        ).toHaveText(new RegExp(`${span.from}\\s*E2E Colleague\\s*MBA 1\\s*10:00\\s*Covering`));
      });

      await test.step('6. Click the back arrow ("Back to requests")', async () => {
        await page.getByRole('button', { name: 'Back to requests' }).click();
        const card = page
          .getByRole('button', { name: 'Open the Permission request' })
          .filter({ hasText: spanText(span) });
        await expect(card, 'TC-418 ER-6: the request is listed').toHaveCount(1);
        await expect(card, 'TC-418 ER-6: applied today').toContainText(`Applied ${todayLabel()}`);
        await expect(
          chip(card, 'Awaiting the Main Admin'),
          'TC-418 ER-6: awaiting the Main Admin',
        ).toBeVisible();
      });
    } finally {
      await withdrawLeave(page, span);
    }
  });

  test('A leave request whose dates run backwards is refused @TC-419', async ({ page, signIn }) => {
    await signIn('mentor');
    const span = freshSpan(11);
    const backwards = { from: span.to, to: span.from };
    const purpose = `E2E backwards ${RUN}`;

    await test.step('1. Open /mentor/leave', async () => {
      await page.goto('/mentor/leave');
      await expect(page.getByRole('heading', { level: 1, name: 'Leave requests' })).toBeVisible();
    });

    await test.step('2. Click New leave request', async () => {
      await page.getByRole('button', { name: 'New leave request' }).click();
    });

    await test.step('3. Enter the from date, the earlier to date, and the purpose', async () => {
      await page.getByLabel('From date').fill(backwards.from);
      await page.getByLabel('To date').fill(backwards.to);
      await page.getByLabel('Purpose').fill(purpose);
    });

    await test.step('4. Click "Sign & submit to Program Director"', async () => {
      await page.getByRole('button', { name: 'Sign & submit to Program Director' }).click();
      await expect(
        page.getByRole('alert'),
        'TC-419 ER-1: the form refuses the dates and names them',
      ).toHaveText(
        endsWith(
          `The last day of leave cannot fall before the first day. You asked for ${backwards.from} to ${backwards.to}.`,
        ),
      );
      await expect(page.getByLabel('Purpose'), 'TC-419 ER-1: the form stays filled in').toHaveValue(
        purpose,
      );
      const mine = (await (await page.request.get('/api/leaves/mine')).json()) as LeaveRow[];
      expect(
        mine.filter((r) => r.from_date === backwards.from || r.to_date === backwards.to),
        'TC-419 ER-2: no request was created for those dates',
      ).toEqual([]);
    });
  });

  test('Withdraw a leave request that is awaiting a decision @TC-420', async ({ page, signIn }) => {
    await signIn('mentor');
    const span = freshSpan(3);
    const purpose = `E2E withdraw ${RUN}`;
    await submitLeave(page, 'CASUAL', span, purpose);
    const card = page
      .getByRole('button', { name: 'Open the Casual Leave request' })
      .filter({ hasText: spanText(span) });
    const sanctioned = page
      .getByRole('row')
      .filter({ has: page.getByRole('rowheader', { name: 'Sanctioned', exact: true }) });
    try {
      await test.step('1. Open /mentor/leave', async () => {
        await page.goto('/mentor/leave');
        await expect(card).toHaveCount(1);
      });

      await test.step('2. Click the request, identified by its dates', async () => {
        await card.click();
        await expect(page.getByText(purpose), 'TC-420 ER-1: the request is shown').toBeVisible();
        await expect(sanctioned, 'TC-420 ER-1: Sanctioned reads Pending').toContainText('Pending');
        await expect(
          page.getByRole('button', { name: 'Withdraw this request' }),
          'TC-420 ER-1: Withdraw this request is offered',
        ).toBeVisible();
        await expect(
          page.getByText(
            'Only while it is still awaiting a signature. Once it is decided, the decision stands.',
          ),
          'TC-420 ER-1: with the rule beside it',
        ).toBeVisible();
      });

      await test.step('3. Click Withdraw this request', async () => {
        await page.getByRole('button', { name: 'Withdraw this request' }).click();
        await expect(
          page.getByText(
            'This takes the request back. It cannot be undone — a new form is how you ask again.',
          ),
          'TC-420 ER-2: the consequence is stated',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Keep it' }),
          'TC-420 ER-2: "Keep it" is offered',
        ).toBeVisible();
      });

      await test.step('4. Click "Yes, withdraw it"', async () => {
        await page.getByRole('button', { name: 'Yes, withdraw it' }).click();
        await expect(sanctioned, 'TC-420 ER-3: Sanctioned reads Cancelled').toContainText(
          'Cancelled',
        );
        await expect(
          page.getByRole('button', { name: 'Withdraw this request' }),
          'TC-420 ER-3: the withdraw controls are gone',
        ).toHaveCount(0);
        await expect(
          page.getByRole('button', { name: 'Yes, withdraw it' }),
          'TC-420 ER-3: the confirmation is gone too',
        ).toHaveCount(0);
      });

      await test.step('5. Click the back arrow ("Back to requests")', async () => {
        await page.getByRole('button', { name: 'Back to requests' }).click();
        await expect(
          chip(card, 'Cancelled'),
          'TC-420 ER-4: the request is listed as Cancelled',
        ).toBeVisible();
      });
    } finally {
      await withdrawLeave(page, span);
    }
  });

  test("Download a leave request's paper as a PDF @TC-421", async ({ page, signIn }) => {
    await signIn('mentor');
    const span = freshSpan(1);
    await submitLeave(page, 'RH', span, `E2E paper ${RUN}`);
    const card = page
      .getByRole('button', { name: 'Open the RH request' })
      .filter({ hasText: spanText(span) });
    try {
      await test.step('1. Open /mentor/leave', async () => {
        await page.goto('/mentor/leave');
        await expect(card).toHaveCount(1);
      });

      await test.step('2. Click the request, identified by its dates', async () => {
        await card.click();
      });

      await test.step('3. Click Download PDF', async () => {
        const download = page.waitForEvent('download');
        await page.getByRole('link', { name: 'Download PDF' }).click();
        const file = await download;
        expect(
          file.suggestedFilename(),
          'TC-421 ER-1: the paper is named for the applicant and date',
        ).toBe(`leave-test-mentor-${span.from}.pdf`);
        expect(
          fs
            .readFileSync((await file.path())!)
            .subarray(0, 5)
            .toString(),
          'TC-421 ER-1: it is a PDF',
        ).toBe('%PDF-');
      });
    } finally {
      await withdrawLeave(page, span);
    }
  });

  test('Save a leave request as a draft on this device, and discard it @TC-422', async ({
    page,
    signIn,
  }) => {
    await signIn('mentor');
    const span = { from: '2031-03-02', to: '2031-03-04' };
    const purpose = `E2E draft ${RUN}`;
    const draftCard = page.getByRole('button').filter({ hasText: 'Draft · saved on this device' });
    let requestsSent = 0;
    page.on('request', (request) => {
      if (request.method() === 'POST' && /\/api\/leaves$/.test(request.url())) requestsSent += 1;
    });

    await test.step('1. Open /mentor/leave', async () => {
      await page.goto('/mentor/leave');
      await expect(page.getByRole('heading', { level: 1, name: 'Leave requests' })).toBeVisible();
    });

    await test.step('2. Click New leave request', async () => {
      await page.getByRole('button', { name: 'New leave request' }).click();
    });

    await test.step('3. Click OOD, and enter the dates and the purpose', async () => {
      await page.getByRole('button', { name: 'OOD', exact: true }).click();
      await page.getByLabel('From date').fill(span.from);
      await page.getByLabel('To date').fill(span.to);
      await page.getByLabel('Purpose').fill(purpose);
    });

    await test.step('4. Click Save draft', async () => {
      await page.getByRole('button', { name: 'Save draft' }).click();
      await expect(
        page.getByText('Draft saved on this device.'),
        'TC-422 ER-1: the list says the draft is saved',
      ).toBeVisible();
      await expect(draftCard, 'TC-422 ER-1: a draft card is listed').toHaveCount(1);
      await expect(draftCard.locator('.req-top strong'), 'TC-422 ER-1: for OOD').toHaveText('OOD');
      await expect(
        draftCard.locator('.req-dates'),
        'TC-422 ER-1: with its dates and time',
      ).toHaveText(
        new RegExp(
          `^\\s*${spanText(span)} · saved \\d{1,2} [A-Z][a-z]{2} \\d{4}, \\d{2}:\\d{2}\\s*$`,
        ),
      );
      await expect(chip(draftCard, 'Draft'), 'TC-422 ER-1: and a "Draft" chip').toBeVisible();
    });

    await test.step('5. Click the draft card', async () => {
      await draftCard.click();
      await expect(
        page.getByRole('button', { name: 'OOD', exact: true }),
        'TC-422 ER-2: OOD is chosen',
      ).not.toHaveClass(/kind--off/);
      await expect(page.getByLabel('From date'), 'TC-422 ER-2: the from date').toHaveValue(
        span.from,
      );
      await expect(page.getByLabel('To date'), 'TC-422 ER-2: the to date').toHaveValue(span.to);
      await expect(page.getByLabel('Purpose'), 'TC-422 ER-2: the purpose').toHaveValue(purpose);
      await expect(
        page.getByRole('button', { name: 'Discard draft' }),
        'TC-422 ER-2: Discard draft is offered',
      ).toBeVisible();
    });

    await test.step('6. Click Discard draft and accept the confirmation', async () => {
      const question = acceptNextDialog(page);
      await page.getByRole('button', { name: 'Discard draft' }).click();
      expect(await question, 'TC-422 ER-3: the browser asks first').toBe(
        'Discard the draft saved on this device?',
      );
      await expect(page.getByLabel('Purpose'), 'TC-422 ER-3: the form is emptied').toHaveValue('');
      await expect(page.getByLabel('From date'), 'TC-422 ER-3: the dates too').toHaveValue('');
      await expect(
        page.getByRole('button', { name: 'Discard draft' }),
        'TC-422 ER-3: Discard draft is gone',
      ).toHaveCount(0);
    });

    await test.step('7. Click the back arrow ("Back to requests")', async () => {
      await page.getByRole('button', { name: 'Back to requests' }).click();
      await expect(page.getByRole('button', { name: 'New leave request' })).toBeVisible();
      await expect(draftCard, 'TC-422 ER-4: no draft card is listed').toHaveCount(0);
      expect(requestsSent, 'TC-422 ER-4: nothing was sent to the server').toBe(0);
    });
  });

  // ------------------------------------------------------------- agent --

  test('Ask the REEP Agent a question as a faculty member @TC-423', async ({ page, signIn }) => {
    await signIn('mentor');
    // Pre-condition 2: an empty conversation, whatever earlier runs asked.
    await page.request.delete('/api/agent/conversation');
    const thread = page.getByRole('log', { name: 'Conversation' });
    const emptyState = thread.getByText('How can I help today?');
    const clear = page.getByRole('button', { name: 'Clear conversation' });

    await test.step('1. Open /mentor/agent', async () => {
      await page.goto('/mentor/agent');
      await expect(
        page.getByRole('heading', { level: 1, name: 'REEP Agent' }),
        'TC-423 ER-1: the page is headed "REEP Agent"',
      ).toBeVisible();
      await expect(
        page.locator('.ag-disclaimer'),
        'TC-423 ER-1: the staff disclaimer is shown',
      ).toHaveText(
        endsWith(
          'The REEP Agent is a general helper — it answers on programme rules, deadlines and how REEP works, and it does not read student records. Personalised answers are only available on student accounts.',
        ),
      );
      await expect(emptyState, 'TC-423 ER-1: the thread is empty').toBeVisible();
      await expect(thread.locator('.ag-starter'), 'TC-423 ER-1: four suggestions').toHaveText([
        'What should I complete this week?',
        'Am I placement-ready?',
        'Show jobs I qualify for',
        'How do I verify a skill?',
      ]);
      await expect(clear, 'TC-423 ER-1: nothing to clear').toBeDisabled();
    });

    await test.step('2. Click the suggestion "How do I verify a skill?"', async () => {
      await page.getByRole('button', { name: 'How do I verify a skill?' }).click();
      await expect(
        thread.locator('.ag-bubble--user').last(),
        "TC-423 ER-2: the question is the faculty member's message",
      ).toHaveText('How do I verify a skill?');
      const answer = thread.locator('.ag-row:not(.ag-row--user)').last();
      await expect(
        answer.locator('.ag-bubble'),
        'TC-423 ER-2: the agent says how a skill is verified',
      ).toContainText('upload a certificate or proof of completion as evidence');
      await expect(
        answer.getByText('Source: Verifying a skill (e.g. Power BI)'),
        'TC-423 ER-2: from the approved policy text',
      ).toBeVisible();
      await expect(
        answer.getByRole('button', { name: 'Helpful', exact: true }),
        'TC-423 ER-2: Helpful',
      ).toBeVisible();
      await expect(
        answer.getByRole('button', { name: 'Not helpful' }),
        'TC-423 ER-2: Not helpful',
      ).toBeVisible();
    });

    await test.step('3. Type the question in the "Message the REEP Agent" box and click Send', async () => {
      await page
        .getByRole('textbox', { name: 'Message the REEP Agent' })
        .fill('Am I placement-ready?');
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      const answer = thread.locator('.ag-row:not(.ag-row--user)').last();
      await expect(
        answer.locator('.ag-bubble'),
        'TC-423 ER-3: personalised answers are for students',
      ).toHaveText(
        'Personalised insights — placement readiness, your next steps, eligible jobs, skills, profile and deadlines — are available on student accounts. I can still answer policy and how-to questions.',
      );
      await expect(
        answer.locator('.ag-limits'),
        'TC-423 ER-3: with the stated limitation',
      ).toHaveText('Personalised tools are student-only.');
    });

    await test.step('4. Click Clear conversation', async () => {
      await clear.click();
      await expect(emptyState, 'TC-423 ER-4: the thread is empty again').toBeVisible();
      await expect(thread.locator('.ag-bubble'), 'TC-423 ER-4: no message is left').toHaveCount(0);
    });
  });

  // ------------------------------------------------------------ access --

  test('A faculty member is kept out of the admin console @TC-424', async ({ page, signIn }) => {
    await signIn('mentor');
    const notebook = page.getByRole('heading', { level: 1, name: 'Faculty notebook' });

    await test.step('1. Open /mentor/notebook and look at the sidebar', async () => {
      await page.goto('/mentor/notebook');
      const nav = page.getByRole('navigation', { name: 'Main' });
      await expect(
        nav.getByRole('link'),
        'TC-424 ER-1: exactly the six faculty screens',
      ).toHaveText([
        endsWith('Notebook'),
        endsWith('Mentee Log'),
        endsWith('Student records'),
        endsWith('Leave Requests'),
        endsWith('Skill Verifications'),
        endsWith('Upskilling'),
      ]);
      await expect(
        nav.getByText('Granted access'),
        'TC-424 ER-1: no "Granted access" group',
      ).toHaveCount(0);
    });

    await test.step('2. Open /admin in the address bar', async () => {
      await page.goto('/admin');
      await expect(page, 'TC-424 ER-2: sent back to the notebook').toHaveURL(/\/mentor\/notebook$/);
      await expect(notebook, 'TC-424 ER-2: the notebook is shown').toBeVisible();
    });

    await test.step('3. Open /admin/students in the address bar', async () => {
      await page.goto('/admin/students');
      await expect(page, 'TC-424 ER-3: sent back to the notebook').toHaveURL(/\/mentor\/notebook$/);
      await expect(notebook, 'TC-424 ER-3: the notebook is shown').toBeVisible();
    });

    await test.step('4. Open /api/admin/students in the address bar', async () => {
      const response = await page.goto('/api/admin/students');
      expect(response?.status(), 'TC-424 ER-4: the API refuses').toBe(403);
      await expect(page.locator('body'), 'TC-424 ER-4: naming the missing capability').toHaveText(
        `{"detail":"You do not hold the 'Students' capability. An administrator can grant it in Governance."}`,
      );
    });
  });

  // ------------------------------------------------------------ alumni --

  test('Alumni creates their profile on first sign-in, or edits it afterwards @TC-425', async ({
    page,
    signIn,
  }) => {
    await signIn('alumni');
    const company = `E2E Co ${RUN}`;
    const before = await alumniProfile(page);
    const firstSignIn = !before.created;
    // Which branch of the case's steps this run takes, in the HTML report.
    test.info().annotations.push({
      type: 'state',
      description: firstSignIn
        ? 'first sign-in: no profile yet, so the setup form'
        : 'a saved profile, so its Edit form',
    });
    const save = page.getByRole('button', {
      name: firstSignIn ? 'Create my profile' : 'Save changes',
    });

    await test.step('1. Open /alumni', async () => {
      await page.goto('/alumni');
      await expect(
        page.getByRole('heading', { level: 1, name: 'My Profile' }),
        'TC-425 ER-1: the page is headed "My Profile"',
      ).toBeVisible();
      await expect(
        page.getByText('Where you work now, and the resume the placement office can share.'),
        'TC-425 ER-1: the sub-heading is shown',
      ).toBeVisible();
      if (firstSignIn) {
        await expect(
          page.getByRole('heading', { name: `Welcome back, ${ACCOUNTS.alumni.name}!` }),
          'TC-425 ER-1: the first sign-in shows the setup form',
        ).toBeVisible();
        await expect(
          page.getByText(
            'Set up your alumni profile — where you currently work and your current resume.',
          ),
          'TC-425 ER-1: which says what it asks for',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Upload current resume *' }),
          'TC-425 ER-1: the resume is marked required',
        ).toBeVisible();
        await expect(
          page.getByText('PDF preferred · up to 10 MB'),
          'TC-425 ER-1: the file rule',
        ).toBeVisible();
        await expect(save, 'TC-425 ER-1: "Create my profile"').toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Cancel' }),
          'TC-425 ER-1: no Cancel before a profile exists',
        ).toHaveCount(0);
      } else {
        await expect(
          page.getByRole('heading', { name: ACCOUNTS.alumni.name, exact: true }),
          'TC-425 ER-1: the saved profile names the alumnus',
        ).toBeVisible();
        await expect(
          page.getByText(ACCOUNTS.alumni.email),
          'TC-425 ER-1: with their email',
        ).toBeVisible();
        await expect(
          page.getByRole('button', { name: 'Edit profile' }),
          'TC-425 ER-1: and an Edit profile button',
        ).toBeVisible();
      }
    });

    await test.step('2. If the page shows the saved profile rather than the setup form, click Edit profile', async () => {
      if (firstSignIn) {
        await expect(
          page.getByLabel('Company you currently work in'),
          'TC-425 ER-1: the setup form is already open, nothing to click',
        ).toBeVisible();
        return;
      }
      await page.getByRole('button', { name: 'Edit profile' }).click();
      await expect(
        page.getByRole('heading', { name: 'Edit profile' }),
        'TC-425 ER-2: the form is headed "Edit profile"',
      ).toBeVisible();
      await expect(
        page.getByLabel('Company you currently work in'),
        'TC-425 ER-2: filled with the saved company',
      ).toHaveValue(before.company ?? '');
      await expect(
        page.getByRole('button', { name: 'Replace resume' }),
        'TC-425 ER-2: "Replace resume" is offered',
      ).toBeVisible();
      await expect(
        page.getByText(`Keeping: ${before.resume?.original_name}`),
        'TC-425 ER-2: the resume on record is kept unless replaced',
      ).toBeVisible();
      await expect(save, 'TC-425 ER-2: "Save changes"').toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Cancel' }),
        'TC-425 ER-2: Cancel',
      ).toBeVisible();
    });

    await test.step('3. Enter the company in "Company you currently work in", the designation in "Designation" and the year in "Graduation year"', async () => {
      await page.getByLabel('Company you currently work in').fill(company);
      await page.getByLabel('Designation').fill('Business Analyst');
      await page.getByLabel('Graduation year').fill('2024');
    });

    await test.step('4. If the save button reads "Create my profile", click it before choosing a resume', async () => {
      if (!firstSignIn) {
        await expect(
          save,
          'TC-425 ER-3: a saved profile reads "Save changes", so the step is skipped',
        ).toBeVisible();
        return;
      }
      await save.click();
      await expect(
        page.getByRole('alert'),
        'TC-425 ER-3: the first save needs a resume',
      ).toHaveText(endsWith('Upload your current resume to create your profile.'));
      expect((await alumniProfile(page)).created, 'TC-425 ER-3: nothing was saved').toBe(false);
    });

    await test.step('5. Click the resume button ("Upload current resume *", or "Replace resume" on a saved profile) and choose the resume file', async () => {
      const chooser = page.waitForEvent('filechooser');
      await page
        .getByRole('button', { name: firstSignIn ? 'Upload current resume *' : 'Replace resume' })
        .click();
      await (await chooser).setFiles(FIXTURE.pdf);
      await expect(
        chip(page, 'sample.pdf'),
        'TC-425 ER-4: a chip names the chosen file',
      ).toBeVisible();
    });

    await test.step('6. Click the save button ("Create my profile", or "Save changes" on a saved profile)', async () => {
      await save.click();
      await expect(chip(page, 'Saved'), 'TC-425 ER-5: a "Saved" chip shows').toBeVisible();
      await expect(fact(page, 'Company'), 'TC-425 ER-5: the company').toHaveText(company);
      await expect(fact(page, 'Designation'), 'TC-425 ER-5: the designation').toHaveText(
        'Business Analyst',
      );
      await expect(fact(page, 'Graduated'), 'TC-425 ER-5: the graduation year').toHaveText('2024');
      await expect(
        fact(page, 'Resume').getByRole('link'),
        'TC-425 ER-5: the resume and its size',
      ).toHaveText(endsWith(`sample.pdf · ${SAMPLE_PDF_SIZE}`));
      await expect(
        page.getByRole('button', { name: 'Edit profile' }),
        'TC-425 ER-5: Edit profile is offered',
      ).toBeVisible();
    });

    await test.step('7. Reload the page', async () => {
      await page.reload();
      await expect(
        fact(page, 'Company'),
        'TC-425 ER-6: the saved company, after the reload',
      ).toHaveText(company);
      await expect(
        page.getByRole('heading', { name: `Welcome back, ${ACCOUNTS.alumni.name}!` }),
        'TC-425 ER-6: not the setup form',
      ).toHaveCount(0);
    });
  });

  test('The alumni profile is not saved without a company @TC-426', async ({ page, signIn }) => {
    await signIn('alumni');
    const before = await alumniProfile(page);
    const firstSignIn = !before.created;
    // Which branch of the case's steps this run takes, in the HTML report.
    test.info().annotations.push({
      type: 'state',
      description: firstSignIn
        ? 'first sign-in: no profile yet, so the setup form'
        : 'a saved profile, so its Edit form',
    });
    const companyField = page.getByLabel('Company you currently work in');

    await test.step('1. Open /alumni', async () => {
      await page.goto('/alumni');
      await expect(page.getByRole('heading', { level: 1, name: 'My Profile' })).toBeVisible();
    });

    await test.step('2. If the page shows the saved profile rather than the setup form, click Edit profile', async () => {
      if (!firstSignIn) await page.getByRole('button', { name: 'Edit profile' }).click();
      await expect(companyField).toBeVisible();
    });

    await test.step('3. Clear the "Company you currently work in" field', async () => {
      await companyField.fill('');
    });

    await test.step('4. Click the save button ("Create my profile", or "Save changes" on a saved profile)', async () => {
      await page
        .getByRole('button', { name: firstSignIn ? 'Create my profile' : 'Save changes' })
        .click();
      await expect(
        page.getByRole('alert'),
        'TC-426 ER-1: the form asks for the company',
      ).toHaveText(endsWith('Tell us the company you currently work in.'));
      await expect(companyField, 'TC-426 ER-1: the form stays open').toBeVisible();
    });

    await test.step('5. Reload the page', async () => {
      await page.reload();
      if (firstSignIn) {
        await expect(
          page.getByRole('heading', { name: `Welcome back, ${ACCOUNTS.alumni.name}!` }),
          'TC-426 ER-2: a fresh database shows the setup form again',
        ).toBeVisible();
      } else {
        await expect(
          fact(page, 'Company'),
          'TC-426 ER-2: the saved profile keeps its company',
        ).toHaveText(before.company!);
      }
      const after = await alumniProfile(page);
      expect([after.created, after.company], 'TC-426 ER-2: nothing was saved').toEqual([
        before.created,
        before.company,
      ]);
    });
  });

  test('Alumni downloads their resume @TC-427', async ({ page, signIn }) => {
    await signIn('alumni');
    let profile = await alumniProfile(page);
    if (!profile.created) {
      const response = await page.request.post('/api/alumni/profile', {
        multipart: {
          company: `E2E Co ${RUN}`,
          resume: {
            name: 'sample.pdf',
            mimeType: 'application/pdf',
            buffer: fs.readFileSync(FIXTURE.pdf),
          },
        },
      });
      expect(response.status(), 'arrange: the alumnus has a profile with a resume').toBe(200);
      profile = await alumniProfile(page);
    }
    const resume = profile.resume!;
    const link = fact(page, 'Resume').getByRole('link');

    await test.step('1. Open /alumni', async () => {
      await page.goto('/alumni');
      await expect(link, 'TC-427 ER-1: the resume on record, with its size').toHaveText(
        endsWith(`${resume.original_name} · ${formatSize(resume.size_bytes)}`),
      );
    });

    await test.step('2. In the Resume row, click the resume', async () => {
      const download = page.waitForEvent('download');
      await link.click();
      expect(
        (await download).suggestedFilename(),
        'TC-427 ER-2: the resume downloads under the name shown',
      ).toBe(resume.original_name);
    });
  });

  test('Alumni jobs sheet lists open postings without match or eligibility @TC-428', async ({
    page,
    signIn,
  }) => {
    await signIn('alumni');

    await test.step("1. Open the sidebar's Jobs Sheet item", async () => {
      await page.goto('/alumni');
      await page
        .getByRole('navigation', { name: 'Main' })
        .getByRole('link', { name: 'Jobs Sheet' })
        .click();
      await expect(page, 'TC-428 ER-1: the jobs sheet opens').toHaveURL(/\/alumni\/jobs$/);
      await expect(
        page.getByRole('heading', { level: 1, name: 'Jobs Sheet' }),
        'TC-428 ER-1: headed "Jobs Sheet"',
      ).toBeVisible();
      await expect(
        page.getByText('Openings from the placement office and the daily job agent.'),
        'TC-428 ER-1: with its sub-heading',
      ).toBeVisible();
      await expect(
        page.getByPlaceholder('Search role, company or location'),
        'TC-428 ER-1: and a search box',
      ).toBeVisible();

      const table = page.getByRole('table');
      await expect(table.getByRole('columnheader'), 'TC-428 ER-2: the columns').toHaveText([
        'Role',
        'Company',
        'Level',
        'Location',
        'Skills',
        'Closes',
        '',
      ]);
      await expect(table, 'TC-428 ER-2: no eligibility').not.toContainText('Eligib');
      await expect(table, 'TC-428 ER-2: no match percentage').not.toContainText('%');

      for (const job of [
        {
          title: 'Financial Analyst',
          cells: ['Acme Capital', 'PG', 'Bengaluru'],
          skills: ['excel', 'financial-modeling'],
          apply: 'https://example.com/apply/1',
        },
        {
          title: 'BI Developer',
          cells: ['DataWorks', 'PG', 'Remote'],
          skills: ['power-bi', 'excel'],
          apply: 'https://example.com/apply/2',
        },
        {
          title: 'Junior Accountant',
          cells: ['LedgerCo', 'UG', 'Mysuru'],
          skills: ['excel'],
          apply: null,
        },
      ]) {
        const row = table.getByRole('row').filter({ hasText: job.title });
        await expect(row, `TC-428 ER-3: ${job.title} is listed`).toHaveCount(1);
        const cells = row.getByRole('cell');
        for (const [i, value] of job.cells.entries()) {
          await expect(cells.nth(i + 1), `TC-428 ER-3: ${job.title} ${value}`).toHaveText(value);
        }
        await expect(cells.nth(4).locator('.chip'), `TC-428 ER-3: ${job.title} skills`).toHaveText(
          job.skills,
        );
        await expect(cells.nth(5), `TC-428 ER-3: ${job.title} has no closing date`).toHaveText('—');
        const apply = row.getByRole('link', { name: 'Apply' });
        if (job.apply) {
          await expect(apply, `TC-428 ER-3: ${job.title} links to the employer`).toHaveAttribute(
            'href',
            job.apply,
          );
        } else {
          await expect(apply, `TC-428 ER-3: ${job.title} has no Apply link`).toHaveCount(0);
        }
      }
    });
  });

  test('Search the alumni jobs sheet @TC-429', async ({ page, signIn }) => {
    await signIn('alumni');
    const search = page.getByPlaceholder('Search role, company or location');
    const rows = page.getByRole('table').locator('tbody tr');

    await test.step('1. Open /alumni/jobs', async () => {
      await page.goto('/alumni/jobs');
      await expect(rows.filter({ hasText: 'Junior Accountant' })).toHaveCount(1);
    });

    await test.step('2. Enter ledgerco in the "Search role, company or location" box', async () => {
      await search.fill('ledgerco');
      await expect(rows, 'TC-429 ER-1: one posting matches').toHaveCount(1);
      await expect(rows, 'TC-429 ER-1: Junior Accountant, whatever the case').toContainText(
        'Junior Accountant',
      );
    });

    await test.step('3. Replace the search with Remote', async () => {
      await search.fill('Remote');
      await expect(rows, 'TC-429 ER-2: one posting matches').toHaveCount(1);
      await expect(rows, 'TC-429 ER-2: BI Developer, by its location').toContainText(
        'BI Developer',
      );
    });

    await test.step('4. Replace the search with zzz-no-such-job', async () => {
      await search.fill('zzz-no-such-job');
      await expect(rows, 'TC-429 ER-3: nothing matches').toHaveText([
        'No opening matches that search.',
      ]);
    });
  });
});
