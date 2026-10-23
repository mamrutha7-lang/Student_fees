/**
 * Student: profile, jobs and tools. The automated twins of the cases in
 * test-management/cases/04-student-tools.md.
 *
 * THE TAG IS THE LINK, as in auth-sync.spec.ts: each title ends with its case's
 * `@TC-NNN`, the top-level `test.step()` titles are the case's steps word for
 * word, and every assertion names the expected result it checks. The CSV
 * reporter fails the run when the two drift apart.
 *
 * WHAT IS ARRANGED OUTSIDE THE STEPS, and why. A case whose expected results
 * are counted from a known state (the profile's completion, the resume
 * builder's percentage) puts that state in place through the API first and
 * restores what it found afterwards. A case that needs a posting, an upload
 * or a meeting request to act on creates one with a name unique to the run
 * and removes it afterwards, through the same API the screens use: the job
 * postings as the Main Admin, the meeting note as the mentor who owns it.
 * Nothing is mocked. The one route this file intercepts is the EXTERNAL apply
 * link of a posting (example.com), so the jobs case does not depend on the
 * internet; the API is never intercepted.
 *
 * THREE OF THESE CALL THE LANGUAGE-MODEL ENDPOINTS in a full run (the resume
 * generation in TC-310, one REEP Agent answer each in TC-328 and TC-329; TC-311
 * reuses TC-310's resume and generates only when run on its own with none).
 * They share a limit of 5 per student per minute (`llm_rate_limited`), and
 * they sit at opposite ends of the file, so two full runs back to back stay
 * under it with room to spare.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';

import { ACCOUNTS, block, expect, test, type AccountKey } from './support/reep';
import type { APIRequestContext, Locator, Page, TestInfo } from '@playwright/test';

/** A suffix unique to this run, for names that must not collide with the last. */
const runId = () => Date.now().toString(36);

const fixture = (name: string) => fs.readFileSync(path.join(__dirname, 'fixtures', name));

/** Today as Angular's `date: 'd MMM y'` prints it in an en-US browser. */
function todayLabel(): string {
  const d = new Date();
  return `${d.getDate()} ${d.toLocaleString('en-US', { month: 'short' })} ${d.getFullYear()}`;
}

/** Signs the `request` fixture (not the page) in as a seeded account, for the
 *  arrangement and clean-up a case does as somebody other than the student. */
async function apiSignIn(
  request: APIRequestContext,
  testInfo: TestInfo,
  account: AccountKey,
): Promise<void> {
  const { email, password } = ACCOUNTS[account];
  const response = await request.post('/api/auth/login', { data: { email, password } });
  if (!response.ok()) {
    block(
      testInfo,
      `${email} could not sign in through the API (${response.status()}), so this case cannot arrange its data.`,
    );
  }
}

// --- profile ---------------------------------------------------------------

/** The editable fields of PUT /api/student/profile (`ProfileUpdateIn`). */
interface ProfileFields {
  phone: string | null;
  email: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  portfolio_url: string | null;
  city: string | null;
  career_summary: string | null;
  interested_in_jobs: boolean;
  interested_in_internships: boolean;
  leaderboard_opt_out: boolean;
}

/** What `python -m app.seed` gives the student's profile row. */
const SEEDED_PROFILE: ProfileFields = {
  phone: null,
  email: null,
  linkedin_url: null,
  github_url: null,
  portfolio_url: null,
  city: 'Bengaluru',
  career_summary: 'MBA finance candidate seeking placement.',
  interested_in_jobs: true,
  interested_in_internships: true,
  leaderboard_opt_out: false,
};

async function readProfile(page: Page): Promise<ProfileFields> {
  const response = await page.request.get('/api/student/profile');
  expect(response.ok(), 'arrange: GET /api/student/profile answers').toBe(true);
  const body = (await response.json()) as ProfileFields;
  const fields = {} as Record<keyof ProfileFields, unknown>;
  for (const key of Object.keys(SEEDED_PROFILE) as (keyof ProfileFields)[]) fields[key] = body[key];
  return fields as unknown as ProfileFields;
}

async function writeProfile(page: Page, fields: ProfileFields): Promise<void> {
  const response = await page.request.put('/api/student/profile', { data: fields });
  expect(response.ok(), 'arrange: PUT /api/student/profile accepts the values').toBe(true);
}

// --- uploads ---------------------------------------------------------------

interface UploadRow {
  id: string;
  original_name: string;
}

/** The card under "Your documents" whose file name is exactly `fileName`. */
const uploadCard = (page: Page, fileName: string) =>
  page.locator('.up-card').filter({
    has: page.locator('.up-file', {
      hasText: new RegExp(`^\\s*${fileName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`),
    }),
  });

async function uploadThroughApi(
  page: Page,
  name: string,
  mimeType: string,
  buffer: Buffer,
): Promise<void> {
  const response = await page.request.post('/api/student/uploads', {
    multipart: { file: { name, mimeType, buffer }, kind: 'DOCUMENT', title: name },
  });
  expect(response.status(), `arrange: the API stores ${name}`).toBe(201);
}

/** Deletes every upload of the student's whose file name is one of `names`. */
async function deleteUploads(page: Page, ...names: string[]): Promise<void> {
  const response = await page.request.get('/api/student/uploads');
  if (!response.ok()) return;
  for (const row of (await response.json()) as UploadRow[]) {
    if (names.includes(row.original_name)) {
      await page.request.delete(`/api/student/uploads/${row.id}`);
    }
  }
}

/** Clicks the dropzone (or `trigger`) and answers the file dialog. */
async function chooseFile(
  page: Page,
  trigger: Locator,
  file: { name: string; mimeType: string; buffer: Buffer },
): Promise<void> {
  const chooser = page.waitForEvent('filechooser');
  await trigger.click();
  await (await chooser).setFiles(file);
}

const dropzone = (page: Page) => page.getByRole('button', { name: /click to browse/ });

// --- jobs ------------------------------------------------------------------

interface JobIn {
  title: string;
  company: string;
  degree_level: 'PG' | 'UG';
  location: string;
  closes_on?: string;
  apply_url?: string;
  required_skills: string[];
}

/** Publishes a posting as the Main Admin, through the Jobs sheet's endpoint. */
async function createJob(request: APIRequestContext, job: JobIn): Promise<string> {
  const response = await request.post('/api/admin/jobs', { data: job });
  expect(response.status(), `arrange: the Main Admin publishes "${job.title}"`).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

const jobRow = (page: Page, title: string) => page.getByRole('row').filter({ hasText: title });

// --- resume ----------------------------------------------------------------

/**
 * The student's newest generated resume, generating one titled `title` only
 * when there is none. A generation counts against the per-minute model limit
 * (see the file header), so the version TC-310 has just made is reused.
 */
async function newestResume(page: Page, title: string): Promise<{ id: string; title: string }> {
  const list = await page.request.get('/api/student/resume');
  expect(list.ok(), 'arrange: GET /api/student/resume answers').toBe(true);
  const rows = (await list.json()) as { id: string; title: string }[];
  if (rows.length > 0) return rows[0];
  const response = await page.request.post('/api/student/resume/generate', { data: { title } });
  expect(response.ok(), `arrange: the API generates the resume "${title}"`).toBe(true);
  return { id: ((await response.json()) as { id: string }).id, title };
}

test.describe('Student: profile, jobs and tools', () => {
  // ------------------------------------------------------------ profile --

  test('Profile shows the locked identity and institutional assignment @TC-300', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const usn = page.getByLabel('USN', { exact: true });
    const lockedCards = page.locator('.card').filter({
      has: page.getByRole('heading', { name: /^(Identity|Institutional assignment)\b/ }),
    });

    await test.step('1. Open /student/profile', async () => {
      await page.goto('/student/profile');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Profile', exact: true }),
        'TC-300 ER-1: the Profile page opens',
      ).toBeVisible();
      await expect(
        page.getByRole('heading', { name: /^Identity/ }),
        'TC-300 ER-1: the Identity card is marked "Synced · read-only"',
      ).toContainText('Synced · read-only');
      await expect(
        page.getByLabel('Full name'),
        'TC-300 ER-1: Full name reads "Test Student"',
      ).toHaveValue(ACCOUNTS.student.name);
      await expect(usn, 'TC-300 ER-1: USN reads "1BG24MBA001"').toHaveValue('1BG24MBA001');

      await expect(
        page.getByRole('heading', { name: /^Institutional assignment/ }),
        'TC-300 ER-2: the card is marked "Verified by Main Admin · read-only"',
      ).toContainText('Verified by Main Admin · read-only');
      const expected: [string, string][] = [
        ['College', 'BGS College of Engineering and Technology'],
        ['Department', 'Department of Management Studies'],
        ['Course', 'Master of Business Administration'],
        ['Specialization', 'Finance'],
        ['Batch', '2024-26'],
        ['Entry date', '2024-08-01'],
        ['Expected completion', '2026-07-31'],
      ];
      for (const [label, value] of expected) {
        await expect(
          page.getByLabel(label, { exact: true }),
          `TC-300 ER-2: ${label} reads "${value}", read through the batch`,
        ).toHaveValue(value);
      }
      await expect(
        page.getByText(
          'Locked — not editable by students. Your college, department and batch are set by the Main Admin; ask the office if any of it is wrong.',
        ),
        'TC-300 ER-2: the card says it is locked and who sets it',
      ).toBeVisible();

      await expect(
        page.getByRole('heading', { name: /^Placement preferences/ }),
        'TC-300 ER-3: Placement preferences carries "Cleared for placements"',
      ).toContainText('Cleared for placements');
    });

    await test.step('2. Click in the USN field and type the letter X', async () => {
      await usn.click();
      await page.keyboard.type('X');
      await expect(usn, 'TC-300 ER-4: typing does not change the USN').toHaveValue('1BG24MBA001');
      const inputs = await lockedCards.locator('input').all();
      expect(inputs.length, 'TC-300 ER-4: the two locked cards hold nine fields').toBe(9);
      for (const input of inputs) {
        await expect(
          input,
          'TC-300 ER-4: every Identity and Institutional assignment field is read-only',
        ).not.toBeEditable();
      }
    });
  });

  test('Saving edited contact details on the profile @TC-301', async ({ page, signIn }) => {
    await signIn('student');
    const city = `E2E City ${runId()}`;
    const phone = page.getByPlaceholder('e.g. 98765 43210');
    const cityField = page.getByPlaceholder('e.g. Bengaluru');
    const save = page.getByRole('button', { name: 'Save changes' });
    const status = page.locator('.save-state');
    const completion = page.locator('.completion-pct');

    const original = await readProfile(page);
    await writeProfile(page, SEEDED_PROFILE);
    try {
      await test.step('1. Open /student/profile', async () => {
        await page.goto('/student/profile');
        await expect(completion, 'TC-301 ER-1: the completion card reads 57%').toHaveText(
          '57% complete',
        );
        await expect(
          page.locator('.completion-card'),
          'TC-301 ER-1: the completion card names the three missing fields',
        ).toContainText(
          '4 of 7 placement fields filled. Add phone, contact email, LinkedIn to strengthen your placement profile.',
        );
        await expect(status, 'TC-301 ER-1: the status reads "Up to date"').toContainText(
          'Up to date',
        );
        await expect(save, 'TC-301 ER-1: Save changes is disabled').toBeDisabled();
      });

      await test.step('2. Type 98765 43210 in the Phone field', async () => {
        await phone.fill('98765 43210');
        await expect(status, 'TC-301 ER-2: the status reads "Unsaved changes"').toContainText(
          'Unsaved changes',
        );
        await expect(save, 'TC-301 ER-2: Save changes becomes enabled').toBeEnabled();
        await expect(completion, 'TC-301 ER-2: the completion card reads 71%').toHaveText(
          '71% complete',
        );
      });

      await test.step('3. Replace the value in the City field with the test city', async () => {
        await cityField.fill(city);
      });

      await test.step('4. Click Save changes', async () => {
        await save.click();
        await expect(status, 'TC-301 ER-3: the status reads "Saved just now"').toContainText(
          'Saved just now',
        );
        await expect(status.locator('.save-time'), 'TC-301 ER-3: followed by the time').toHaveText(
          /^· \d{1,2}:\d{2}/,
        );
        await expect(save, 'TC-301 ER-3: Save changes is disabled again').toBeDisabled();
      });

      await test.step('5. Reload the page', async () => {
        await page.reload();
        await expect(
          status,
          'TC-301 ER-4: the status reads "Up to date" after the reload',
        ).toContainText('Up to date');
        await expect(phone, 'TC-301 ER-4: Phone was saved').toHaveValue('98765 43210');
        await expect(cityField, 'TC-301 ER-4: City was saved').toHaveValue(city);
        await expect(completion, 'TC-301 ER-4: the completion card still reads 71%').toHaveText(
          '71% complete',
        );
      });
    } finally {
      await writeProfile(page, original);
    }
  });

  test('Invalid phone and LinkedIn entries are refused before saving @TC-302', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const phone = page.getByPlaceholder('e.g. 98765 43210');
    const linkedIn = page.getByPlaceholder('linkedin.com/in/…');
    const save = page.getByRole('button', { name: 'Save changes' });
    const status = page.locator('.save-state');
    const errorUnder = (field: Locator) =>
      page.locator('.field').filter({ has: field }).locator('.field-error');

    const original = await readProfile(page);
    await writeProfile(page, { ...original, phone: null, linkedin_url: null });
    try {
      await test.step('1. Open /student/profile', async () => {
        await page.goto('/student/profile');
        await expect(status, 'arrange: the profile has loaded').toContainText('Up to date');
      });

      await test.step('2. Type 98765 43210 in the Phone field', async () => {
        await phone.fill('98765 43210');
        await expect(status, 'TC-302 ER-1: the status reads "Unsaved changes"').toContainText(
          'Unsaved changes',
        );
        await expect(save, 'TC-302 ER-1: Save changes is enabled').toBeEnabled();
      });

      await test.step('3. Replace the Phone value with 98765-ABC and press Tab', async () => {
        await phone.fill('98765-ABC');
        await phone.press('Tab');
        await expect(
          errorUnder(phone),
          'TC-302 ER-2: the phone error is shown under Phone',
        ).toContainText('Use only digits, spaces or a leading +.');
        await expect(
          page.getByText(
            'Some fields need fixing before you can save. Corrections are marked in red below.',
          ),
          'TC-302 ER-2: the summary at the top says fields need fixing',
        ).toBeVisible();
        await expect(save, 'TC-302 ER-2: Save changes is disabled').toBeDisabled();
      });

      await test.step('4. Empty the Phone field, type example.com/in/test-student in the LinkedIn field and press Tab', async () => {
        await phone.fill('');
        await linkedIn.fill('example.com/in/test-student');
        await linkedIn.press('Tab');
        await expect(errorUnder(phone), 'TC-302 ER-3: the Phone error is gone').toHaveCount(0);
        await expect(
          errorUnder(linkedIn),
          'TC-302 ER-3: the LinkedIn error is shown under LinkedIn',
        ).toContainText('Enter a linkedin.com link.');
        await expect(save, 'TC-302 ER-3: Save changes stays disabled').toBeDisabled();
      });

      await test.step('5. Reload the page', async () => {
        await page.reload();
        await expect(status, 'TC-302 ER-4: the status reads "Up to date"').toContainText(
          'Up to date',
        );
        await expect(phone, 'TC-302 ER-4: no phone was saved').toHaveValue('');
        await expect(linkedIn, 'TC-302 ER-4: no LinkedIn link was saved').toHaveValue('');
      });
    } finally {
      await writeProfile(page, original);
    }
  });

  // ------------------------------------------------------------ uploads --

  test("Uploads lists the student's documents and their review status @TC-303", async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open /student/uploads', async () => {
      await page.goto('/student/uploads');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Uploads', exact: true }),
        'TC-303 ER-1: the Uploads page opens',
      ).toBeVisible();
      await expect(
        page.getByRole('list', { name: 'Upload steps' }).getByRole('listitem'),
        'TC-303 ER-1: the three steps are shown',
      ).toHaveText([/Choose document type/, /Upload file/, /In review/]);
      await expect(
        page.locator('.up-accepts'),
        'TC-303 ER-1: the accepted formats and the size cap are stated',
      ).toContainText('Accepted: PDF, PNG, JPEG · up to 10 MB');

      const seeded: [string, string, string, string][] = [
        [
          'leadership_completion.pdf',
          'Leadership certificate',
          'Certificate proof',
          'Pending review',
        ],
        ['me.png', 'Profile photo', 'Profile photo', 'Pending review'],
        ['resume_v1.pdf', 'Existing CV', 'Resume / CV', 'Verified'],
      ];
      for (const [file, title, kind, state] of seeded) {
        const card = uploadCard(page, file);
        await expect(
          card.locator('.up-title'),
          `TC-303 ER-2: ${file} is titled "${title}"`,
        ).toHaveText(title);
        await expect(card.locator('.up-meta'), `TC-303 ER-2: ${file} is a ${kind}`).toContainText(
          `${kind} ·`,
        );
        await expect(card.locator('.chip'), `TC-303 ER-2: ${file} reads "${state}"`).toContainText(
          state,
        );
      }
      await expect(
        uploadCard(page, 'resume_v1.pdf').locator('.up-note'),
        "TC-303 ER-3: the verified CV shows the reviewer's comment",
      ).toContainText('Reviewer: Looks good.');

      for (const item of ['Profile photo', 'Resume / CV', 'Certificate proof']) {
        await expect(
          page.locator('.ck').filter({ hasText: item }),
          `TC-303 ER-4: the checklist marks ${item} as added`,
        ).toContainText('Added');
      }
      await expect(
        page.locator('.checklist__head'),
        'TC-303 ER-4: the checklist carries "All in"',
      ).toContainText('All in');
    });
  });

  test('Uploading a document puts it in review with a preview @TC-304', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const fileName = `e2e-${runId()}.png`;
    const success = page.locator('.feedback--ok');

    try {
      await test.step('1. Open /student/uploads', async () => {
        await page.goto('/student/uploads');
        await expect(
          uploadCard(page, 'resume_v1.pdf'),
          'arrange: the list has loaded',
        ).toBeVisible();
      });

      await test.step('2. Under Document type, select Other document', async () => {
        await page.getByLabel('Document type').selectOption({ label: 'Other document' });
        await expect(
          dropzone(page),
          'TC-304 ER-1: the dropzone uploads as a Document',
        ).toContainText('Uploading as Document');
      });

      await test.step('3. Click the dropzone that reads "Drag & drop a file here, or click to browse" and choose the test file', async () => {
        await chooseFile(page, dropzone(page), {
          name: fileName,
          mimeType: 'image/png',
          buffer: fixture('sample.png'),
        });
        await expect(success, 'TC-304 ER-2: the success panel says it is in review').toContainText(
          'Uploaded — now in review',
        );
        await expect(
          success,
          'TC-304 ER-2: the success panel names the file and its size',
        ).toContainText(`${fileName} · 551 B. A mentor will verify it shortly.`);
        const preview = success.getByRole('img', { name: fileName });
        await expect
          .poll(
            () =>
              preview.evaluate((img) =>
                (img as HTMLImageElement).complete ? (img as HTMLImageElement).naturalWidth : 0,
              ),
            {
              message: 'TC-304 ER-2: the preview shows the 240-pixel image opened from the server',
            },
          )
          .toBe(240);
        await expect(
          page.locator('.up-step').filter({ hasText: 'In review' }),
          'TC-304 ER-2: "In review" is the active step',
        ).toHaveClass(/\bactive\b/);

        const card = uploadCard(page, fileName);
        await expect(
          page.locator('.up-card').first(),
          'TC-304 ER-3: the new card is at the top of Your documents',
        ).toContainText(fileName);
        await expect(
          card.locator('.chip'),
          'TC-304 ER-3: the new card reads "Pending review"',
        ).toContainText('Pending review');
        await expect(
          card.locator('.up-meta'),
          'TC-304 ER-3: type, size and date are shown',
        ).toHaveText(`Document · 551 B · ${todayLabel()}`);
      });
    } finally {
      await deleteUploads(page, fileName);
    }
  });

  test('Removing an uploaded document @TC-305', async ({ page, signIn }) => {
    await signIn('student');
    const fileName = `e2e-remove-${runId()}.pdf`;
    const card = uploadCard(page, fileName);
    await uploadThroughApi(page, fileName, 'application/pdf', fixture('sample.pdf'));
    const confirmation = `Remove "${fileName}"? This permanently deletes the file from your record.`;

    try {
      await test.step('1. Open /student/uploads', async () => {
        await page.goto('/student/uploads');
        await expect(card, 'arrange: the disposable upload is listed').toBeVisible();
      });

      await test.step("2. Click Remove on the test upload's card, and click Cancel in the confirmation", async () => {
        let asked = '';
        page.once('dialog', (dialog) => {
          asked = dialog.message();
          void dialog.dismiss();
        });
        await card.getByRole('button', { name: 'Remove' }).click();
        await expect
          .poll(() => asked, { message: 'TC-305 ER-1: the browser asks for confirmation' })
          .toBe(confirmation);
        await expect(card, 'TC-305 ER-1: after Cancel the card is still listed').toBeVisible();
      });

      await test.step('3. Click Remove on the same card again, and click OK in the confirmation', async () => {
        page.once('dialog', (dialog) => void dialog.accept());
        await card.getByRole('button', { name: 'Remove' }).click();
        await expect(card, 'TC-305 ER-2: the card disappears').toHaveCount(0);
      });

      await test.step('4. Reload the page', async () => {
        await page.reload();
        await expect(
          uploadCard(page, 'resume_v1.pdf'),
          'TC-305 ER-3: the seeded uploads are untouched',
        ).toBeVisible();
        await expect(card, 'TC-305 ER-3: the upload was deleted on the server').toHaveCount(0);
      });
    } finally {
      await deleteUploads(page, fileName);
    }
  });

  test('Replacing an uploaded document @TC-306', async ({ page, signIn }) => {
    await signIn('student');
    const run = runId();
    const oldName = `e2e-old-${run}.pdf`;
    const newName = `e2e-new-${run}.jpg`;
    await uploadThroughApi(page, oldName, 'application/pdf', fixture('sample.pdf'));

    try {
      await test.step('1. Open /student/uploads', async () => {
        await page.goto('/student/uploads');
        await expect(
          uploadCard(page, oldName),
          'arrange: the upload to replace is listed',
        ).toBeVisible();
      });

      await test.step("2. Click Replace on the test upload's card and choose the new file", async () => {
        await chooseFile(page, uploadCard(page, oldName).getByRole('button', { name: 'Replace' }), {
          name: newName,
          mimeType: 'image/jpeg',
          buffer: fixture('sample.jpg'),
        });
        const success = page.locator('.feedback--ok');
        await expect(success, 'TC-306 ER-1: the success panel says it is in review').toContainText(
          'Uploaded — now in review',
        );
        await expect(success, 'TC-306 ER-1: and names the new file').toContainText(newName);
        const card = uploadCard(page, newName);
        await expect(
          card.locator('.chip'),
          'TC-306 ER-2: the new card reads "Pending review"',
        ).toContainText('Pending review');
        await expect(
          card.locator('.up-meta'),
          "TC-306 ER-2: it keeps the replaced card's type",
        ).toHaveText(`Document · 2.8 KB · ${todayLabel()}`);
        await expect(uploadCard(page, oldName), 'TC-306 ER-2: the old card is gone').toHaveCount(0);
      });
    } finally {
      await deleteUploads(page, oldName, newName);
    }
  });

  test('A file that is not a PDF, PNG or JPEG is refused @TC-307', async ({ page, signIn }) => {
    await signIn('student');
    const fileName = `e2e-fake-${runId()}.pdf`;

    try {
      await test.step('1. Open /student/uploads', async () => {
        await page.goto('/student/uploads');
        await expect(
          uploadCard(page, 'resume_v1.pdf'),
          'arrange: the list has loaded',
        ).toBeVisible();
      });

      await test.step('2. Click the dropzone and choose the test file', async () => {
        await chooseFile(page, dropzone(page), {
          name: fileName,
          mimeType: 'application/pdf',
          buffer: Buffer.from('just some text'),
        });
        const error = page.getByRole('alert').filter({ hasText: 'Upload failed' });
        await expect(
          error,
          'TC-307 ER-1: the server refuses the file by its content',
        ).toContainText('Unsupported file type — only PDF, PNG and JPEG are accepted.');
      });

      await test.step('3. Reload the page', async () => {
        await page.reload();
        await expect(
          uploadCard(page, 'resume_v1.pdf'),
          'TC-307 ER-2: the list has loaded',
        ).toBeVisible();
        await expect(uploadCard(page, fileName), 'TC-307 ER-2: nothing was stored').toHaveCount(0);
      });
    } finally {
      await deleteUploads(page, fileName);
    }
  });

  test('A file over 10 MB is refused @TC-308', async ({ page, signIn }) => {
    await signIn('student');
    const fileName = `e2e-big-${runId()}.pdf`;

    try {
      await test.step('1. Open /student/uploads', async () => {
        await page.goto('/student/uploads');
        await expect(
          uploadCard(page, 'resume_v1.pdf'),
          'arrange: the list has loaded',
        ).toBeVisible();
      });

      await test.step('2. Click the dropzone and choose the test file', async () => {
        const uploads: string[] = [];
        page.on('request', (r) => {
          if (r.method() === 'POST' && r.url().endsWith('/api/student/uploads'))
            uploads.push(r.url());
        });
        // 10 MB is 10 485 760 bytes; this file is one byte over, and starts
        // like a real PDF so only its size can be the reason it is refused.
        const buffer = Buffer.alloc(10 * 1024 * 1024 + 1, 0x20);
        buffer.write('%PDF-1.4\n');
        await chooseFile(page, dropzone(page), {
          name: fileName,
          mimeType: 'application/pdf',
          buffer,
        });
        await expect(
          page.getByRole('alert').filter({ hasText: 'Upload failed' }),
          'TC-308 ER-1: the page refuses the file for its size',
        ).toContainText('That file is over 10 MB. Please upload a smaller PDF, PNG or JPEG.');
        expect(uploads, 'TC-308 ER-1: the file was refused before it was sent').toEqual([]);
        await expect(uploadCard(page, fileName), 'TC-308 ER-1: no card appears for it').toHaveCount(
          0,
        );
      });
    } finally {
      await deleteUploads(page, fileName);
    }
  });

  // ------------------------------------------------------------- resume --

  test('Filling a resume builder section and saving it @TC-309', async ({ page, signIn }) => {
    await signIn('student');
    const objective = `E2E objective ${runId()}: analyst roles in fintech, with MBA finance training.`;
    const percentage = page.locator('.completeness .pct');
    const saveChip = page.locator('.save-chip');
    const otherDetails = page.locator('.stepper .step-item', { hasText: 'Other Details' });
    const objectiveBox = page.getByPlaceholder(
      'Describe your expectations, both for yourself and the organization…',
    );

    const before = await page.request.get('/api/student/resume-profile');
    expect(before.ok(), 'arrange: GET /api/student/resume-profile answers').toBe(true);
    const original = ((await before.json()) as { data: Record<string, unknown> }).data;
    const emptied = await page.request.put('/api/student/resume-profile', { data: { data: {} } });
    expect(emptied.ok(), 'arrange: the builder is emptied').toBe(true);
    try {
      await test.step('1. Open /student/resume', async () => {
        await page.goto('/student/resume');
        await expect(
          page.getByRole('heading', { level: 1, name: 'Resume Builder' }),
          'TC-309 ER-1: the Resume Builder opens',
        ).toBeVisible();
        await expect(
          page.locator('.rb-steps .step.active'),
          'TC-309 ER-1: it opens on "1 Build content"',
        ).toContainText('Build content');
        await expect(
          page.getByRole('heading', { level: 2, name: 'Basic Details' }),
          'TC-309 ER-1: showing Basic Details',
        ).toBeVisible();
        await expect(percentage, 'TC-309 ER-1: Profile complete reads 0%').toHaveText('0%');
        await expect(saveChip, 'TC-309 ER-1: the save chip reads "Not saved yet"').toContainText(
          'Not saved yet',
        );
      });

      await test.step('2. In the list of sections on the left, click Other Details', async () => {
        await otherDetails.click();
        await expect(
          page.getByRole('heading', { level: 2, name: 'Other Details' }),
          'TC-309 ER-2: the section heading is Other Details',
        ).toBeVisible();
        await expect(
          page.getByText('Objective, key expertise, achievements, awards and activities.'),
          'TC-309 ER-2: with its description',
        ).toBeVisible();
        await expect(
          page.getByRole('heading', { level: 3, name: 'Career objective' }),
          'TC-309 ER-2: and a Career objective card',
        ).toBeVisible();
      });

      await test.step('3. Type the test objective in the Career objective box', async () => {
        await objectiveBox.fill(objective);
        await expect(saveChip, 'TC-309 ER-3: the save chip reads "Unsaved changes"').toContainText(
          'Unsaved changes',
        );
        await expect(
          page.getByText(`${objective.length} / 6000`),
          "TC-309 ER-3: the counter shows the objective's length",
        ).toBeVisible();
      });

      await test.step('4. Click Save section', async () => {
        await page.getByRole('button', { name: 'Save section' }).click();
        await expect(saveChip, 'TC-309 ER-4: the save chip reads "Saved just now"').toContainText(
          'Saved just now',
        );
        await expect(percentage, 'TC-309 ER-4: Profile complete reads 8%').toHaveText('8%');
      });

      await test.step('5. Reload the page', async () => {
        await page.reload();
        await expect(percentage, 'TC-309 ER-5: Profile complete still reads 8%').toHaveText('8%');
      });

      await test.step('6. Click Other Details again', async () => {
        await otherDetails.click();
        await expect(objectiveBox, 'TC-309 ER-6: the objective was saved').toHaveValue(objective);
      });
    } finally {
      await page.request.put('/api/student/resume-profile', { data: { data: original } });
    }
  });

  test('Generating a resume composes it on this machine and downloads it as a PDF @TC-310', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const title = `E2E resume ${runId()}`;
    const preview = page.locator('.preview');
    const download = page.getByRole('button', { name: 'Download PDF' });
    const pageCount = page.locator('.page-count b');

    await test.step('1. Open /student/resume', async () => {
      await page.goto('/student/resume');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Resume Builder' }),
        'arrange: the Resume Builder has opened',
      ).toBeVisible();
    });

    await test.step('2. Click the step 3 Preview at the top of the page', async () => {
      await page.getByRole('button', { name: /^3\s*Preview$/ }).click();
      await expect(
        page.getByRole('heading', { level: 2, name: 'Generated Resume' }),
        'TC-310 ER-1: the Generated Resume view opens',
      ).toBeVisible();
      await expect(preview, 'TC-310 ER-1: nothing is generated yet').toContainText(
        'No resume generated yet. Press Generate Resume to compose one from your saved REEP records.',
      );
      await expect(download, 'TC-310 ER-1: Download PDF is disabled').toBeDisabled();
      await expect(pageCount, 'TC-310 ER-1: the page count reads —').toHaveText('—');
    });

    await test.step('3. Type the test title in the "Resume title (optional)" box', async () => {
      await page.getByPlaceholder(/^Resume title \(optional\)/).fill(title);
    });

    let resumeId = '';
    await test.step('4. Click Generate Resume', async () => {
      await page.getByRole('button', { name: 'Generate Resume' }).click();
      await expect(
        preview.getByRole('heading', { level: 2, name: ACCOUNTS.student.name }),
        "TC-310 ER-2: the resume is headed with the student's name",
      ).toBeVisible();
      await expect(
        preview.locator('.psec', { hasText: /^\s*Verified Skills\s*$/ }),
        'TC-310 ER-2: the resume has a Verified Skills section',
      ).toBeVisible();
      await expect(
        preview.locator('.ppara', { hasText: /^\s*MS Excel\s*$/ }),
        'TC-310 ER-2: listing MS Excel, the only verified skill',
      ).toBeVisible();
      await expect(
        preview.locator('.psec', { hasText: /^\s*Academics\s*$/ }),
        'TC-310 ER-2: the resume has an Academics section',
      ).toBeVisible();
      await expect(
        preview.locator('.pline').first(),
        'TC-310 ER-2: Academics starts with the latest CGPA',
      ).toContainText('Latest CGPA: 8.2');
      const trace = page.locator('.trace');
      await expect(trace, 'TC-310 ER-2: the trace says it is a deterministic draft').toContainText(
        'Deterministic draft',
      );
      await expect(trace, 'TC-310 ER-2: composed on this machine').toContainText(
        'composed on this machine',
      );
      await expect(trace, 'TC-310 ER-2: and says why no model was used').toContainText(
        'No language model is configured, so the resume was composed on this machine from your saved REEP records.',
      );
      await expect(
        page.getByRole('button', { name: 'Regenerate' }),
        'TC-310 ER-2: the button now reads Regenerate',
      ).toBeVisible();
      await expect(pageCount, 'TC-310 ER-2: the page count reads 1').toHaveText('1');
      await expect(download, 'TC-310 ER-2: Download PDF is enabled').toBeEnabled();

      const list = await page.request.get('/api/student/resume');
      const newest = ((await list.json()) as { id: string; title: string }[])[0];
      expect(newest?.title, 'TC-310 ER-2: the new version is saved with the test title').toBe(
        title,
      );
      resumeId = newest.id;
    });

    await test.step('5. Click Download PDF', async () => {
      const pdfUrl = `/api/student/resume/${resumeId}/pdf`;
      const opened = page.context().waitForEvent('page');
      const served = page.context().waitForEvent('response', (r) => r.url().endsWith(pdfUrl));
      await download.click();
      const tab = await opened;
      const response = await served;
      expect(response.status(), 'TC-310 ER-3: the PDF is served').toBe(200);
      expect(response.headers()['content-type'], 'TC-310 ER-3: the new tab receives a PDF').toBe(
        'application/pdf',
      );
      await expect(tab, 'TC-310 ER-3: a new tab opens this resume as a PDF').toHaveURL(
        new RegExp(`${pdfUrl}$`),
      );
      await tab.close();
    });
  });

  test('Exporting a resume needs the sharing confirmation @TC-311', async ({ page, signIn }) => {
    await signIn('student');
    const { id: resumeId, title } = await newestResume(page, `E2E export ${runId()}`);
    const exportButton = page.getByRole('button', { name: 'Export & share', exact: true });
    const hint = page.getByText('Tick the confirmation above to enable this.');
    const confirm = page.getByRole('checkbox', {
      name: /I confirm this resume shares only what I intend recruiters to see/,
    });

    await test.step('1. Open /student/resume', async () => {
      await page.goto('/student/resume');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Resume Builder' }),
        'arrange: the Resume Builder has opened',
      ).toBeVisible();
    });

    await test.step('2. Click the step 4 Export & share at the top of the page', async () => {
      await page.getByRole('button', { name: /^4\s*Export & share$/ }).click();
      const newest = page.locator('.version').first();
      await expect(
        newest.locator('.version-name'),
        'TC-311 ER-1: the newest version is listed first',
      ).toContainText(title);
      await expect(newest.locator('.version-name'), 'TC-311 ER-1: and is Selected').toContainText(
        'Selected',
      );
      await expect(newest.locator('.version-meta'), 'TC-311 ER-1: a generated version').toHaveText(
        /v\d+ · Generated\s+· Updated \d{1,2} \w{3} \d{4}/,
      );
      await expect(
        newest.getByRole('button', { name: 'In use' }),
        'TC-311 ER-1: its button reads "In use" and is disabled',
      ).toBeDisabled();
      await expect(
        page.getByRole('checkbox', { name: /Include an evidence appendix/ }),
        'TC-311 ER-1: the evidence appendix is off',
      ).not.toBeChecked();
      await expect(exportButton, 'TC-311 ER-1: Export & share is disabled').toBeDisabled();
      await expect(hint, 'TC-311 ER-1: the page says why').toBeVisible();
    });

    await test.step('3. Tick "I confirm this resume shares only what I intend recruiters to see."', async () => {
      await confirm.check();
      await expect(exportButton, 'TC-311 ER-2: Export & share becomes enabled').toBeEnabled();
      await expect(hint, 'TC-311 ER-2: the hint disappears').toBeHidden();
    });

    await test.step('4. Click the Export & share button under the confirmation', async () => {
      const pdfUrl = `/api/student/resume/${resumeId}/pdf`;
      const opened = page.context().waitForEvent('page');
      const served = page.context().waitForEvent('response', (r) => r.url().includes(pdfUrl));
      await exportButton.click();
      const tab = await opened;
      const response = await served;
      expect(response.headers()['content-type'], 'TC-311 ER-3: the new tab receives a PDF').toBe(
        'application/pdf',
      );
      await expect(
        tab,
        'TC-311 ER-3: it is the selected version, with no evidence appendix',
      ).toHaveURL(new RegExp(`${pdfUrl}$`));
      await tab.close();
      await expect(
        page.getByText('Your PDF opened in a new tab.'),
        'TC-311 ER-3: the page says the PDF opened',
      ).toBeVisible();
    });
  });

  // --------------------------------------------------------------- jobs --

  test('The jobs board shows match and eligibility for each posting @TC-312', async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open /student/jobs', async () => {
      await page.goto('/student/jobs');
      await expect(
        page.getByRole('columnheader'),
        'TC-312 ER-1: one table with the six columns',
      ).toHaveText(['Role', 'Location', 'Eligibility', 'Skill match', 'Status', 'Action']);

      const cells = (title: string) => jobRow(page, title).getByRole('cell');
      await expect(cells('Financial Analyst'), 'TC-312 ER-2: Financial Analyst').toHaveText([
        /Financial Analyst\s*Acme Capital/,
        /Bengaluru/,
        /Eligible\s*$/,
        /100%/,
        /.*/,
        /.*/,
      ]);

      const bi = jobRow(page, 'BI Developer');
      await expect(cells('BI Developer'), 'TC-312 ER-3: BI Developer is not eligible').toHaveText([
        /BI Developer\s*DataWorks/,
        /Remote/,
        /Not eligible\s*$/,
        /100%/,
        /CGPA 8\.2 is below the required 8\.5/,
        /Not eligible/,
      ]);
      await expect(
        bi.getByRole('button', { name: 'Not eligible' }),
        'TC-312 ER-3: its action button is disabled',
      ).toBeDisabled();

      await expect(cells('Junior Accountant'), 'TC-312 ER-4: Junior Accountant').toHaveText([
        /Junior Accountant\s*LedgerCo/,
        /Mysuru/,
        /Eligible\s*$/,
        /100%/,
        /.*/,
        /.*/,
      ]);
    });
  });

  test('Filtering the jobs board @TC-313', async ({ page, signIn, request }, testInfo) => {
    await signIn('student');
    await apiSignIn(request, testInfo, 'admin');
    const title = `E2E closing ${runId()}`;
    const inFiveDays = new Date(Date.now() + 5 * 86_400_000).toISOString().slice(0, 10);
    const jobId = await createJob(request, {
      title,
      company: 'E2E Co',
      degree_level: 'PG',
      location: 'E2E Town',
      closes_on: inFiveDays,
      required_skills: [],
    });
    const seeded = ['Financial Analyst', 'BI Developer', 'Junior Accountant'];
    const clear = page.getByRole('button', { name: 'Clear' });

    try {
      await test.step('1. Open /student/jobs', async () => {
        await page.goto('/student/jobs');
        await expect(
          jobRow(page, title).locator('.closes'),
          'TC-313 ER-1: the test posting closes within the week',
        ).toHaveText(/^\s*Closes in [5-7] days\s*$/);
      });

      await test.step('2. Set Eligibility to Not eligible', async () => {
        await page.getByLabel('Eligibility').selectOption({ label: 'Not eligible' });
        await expect(
          jobRow(page, 'BI Developer'),
          'TC-313 ER-2: BI Developer is listed',
        ).toBeVisible();
        for (const hidden of ['Financial Analyst', 'Junior Accountant', title]) {
          await expect(jobRow(page, hidden), `TC-313 ER-2: ${hidden} is hidden`).toHaveCount(0);
        }
        await expect(clear, 'TC-313 ER-2: a Clear button appears').toBeVisible();
      });

      await test.step('3. Set Location to Mysuru', async () => {
        await page.getByLabel('Location').selectOption({ label: 'Mysuru' });
        await expect(
          page.getByRole('cell', { name: 'No roles match these filters.' }),
          'TC-313 ER-3: no posting matches both filters',
        ).toBeVisible();
      });

      await test.step('4. Click Clear', async () => {
        await clear.click();
        for (const shown of [...seeded, title]) {
          await expect(jobRow(page, shown), `TC-313 ER-4: ${shown} is listed again`).toBeVisible();
        }
        await expect(
          page.getByLabel('Eligibility'),
          'TC-313 ER-4: Eligibility reads All',
        ).toHaveValue('all');
        await expect(
          page.getByLabel('Location'),
          'TC-313 ER-4: Location reads All locations',
        ).toHaveValue('all');
        await expect(page.getByLabel('Deadline'), 'TC-313 ER-4: Deadline reads All').toHaveValue(
          'all',
        );
        await expect(clear, 'TC-313 ER-4: the Clear button is gone').toBeHidden();
      });

      await test.step('5. Set Deadline to Closing soon (≤7 days)', async () => {
        await page.getByLabel('Deadline').selectOption({ label: 'Closing soon (≤7 days)' });
        await expect(jobRow(page, title), 'TC-313 ER-5: the test posting is listed').toBeVisible();
        for (const hidden of seeded) {
          await expect(
            jobRow(page, hidden),
            `TC-313 ER-5: ${hidden}, with no deadline, is hidden`,
          ).toHaveCount(0);
        }
      });
    } finally {
      await request.delete(`/api/admin/jobs/${jobId}`);
    }
  });

  test('Applying to an eligible posting @TC-314', async ({ page, signIn, request }, testInfo) => {
    await signIn('student');
    await apiSignIn(request, testInfo, 'admin');
    const run = runId();
    const title = `E2E apply ${run}`;
    const applyUrl = `https://example.com/apply/${run}`;
    const jobId = await createJob(request, {
      title,
      company: 'E2E Co',
      degree_level: 'PG',
      location: 'E2E Town',
      apply_url: applyUrl,
      required_skills: [],
    });
    // The posting's own apply page is outside REEP; answer it locally.
    await page
      .context()
      .route('https://example.com/**', (route) =>
        route.fulfill({ contentType: 'text/html', body: '<title>Employer apply page</title>' }),
      );
    const row = jobRow(page, title);
    const cells = row.getByRole('cell');

    try {
      await test.step('1. Open /student/jobs', async () => {
        await page.goto('/student/jobs');
        await expect(cells, 'TC-314 ER-1: eligible, skills not listed, not applied').toHaveText([
          new RegExp(title),
          /E2E Town/,
          /Eligible\s*$/,
          /Skills not listed/,
          /Not applied\s*$/,
          /Apply\s*$/,
        ]);
        await expect(
          row.getByRole('button', { name: 'Apply' }),
          'TC-314 ER-1: Apply is offered',
        ).toBeEnabled();
      });

      await test.step("2. Click Apply on the test posting's row", async () => {
        const opened = page.context().waitForEvent('page');
        const recorded = page.waitForResponse(
          (r) =>
            r.url().endsWith(`/api/student/jobs/${jobId}/apply`) && r.request().method() === 'POST',
        );
        await row.getByRole('button', { name: 'Apply' }).click();
        const tab = await opened;
        await expect(tab, "TC-314 ER-2: the posting's apply link opens in a new tab").toHaveURL(
          applyUrl,
        );
        await tab.close();
        expect((await recorded).status(), 'TC-314 ER-2: the application is sent to REEP').toBe(200);
        await expect(cells.nth(4), 'TC-314 ER-2: the status reads "Applied"').toContainText(
          'Applied',
        );
        await expect(
          row.getByRole('button', { name: 'Applied' }),
          'TC-314 ER-2: the button reads "Applied" and is disabled',
        ).toBeDisabled();
      });

      await test.step('3. Reload the page', async () => {
        await page.reload();
        await expect(cells.nth(4), 'TC-314 ER-3: still "Applied" after the reload').toContainText(
          'Applied',
        );
        await expect(
          row.getByRole('button', { name: 'Applied' }),
          'TC-314 ER-3: the application was recorded on the server',
        ).toBeDisabled();
      });
    } finally {
      await page.context().unroute('https://example.com/**');
      // Applied to, so it cannot be deleted: closing takes it off the board.
      await request.post(`/api/admin/jobs/${jobId}/close`);
    }
  });

  // ------------------------------------------------------------ english --

  test('The English baseline shows pending sections as dashes, not zeros @TC-315', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const section = (label: string) =>
      page
        .locator('.eb-skill')
        .filter({ has: page.locator('.eb-skill__name', { hasText: label }) });

    await test.step('1. Open /student/english', async () => {
      await page.goto('/student/english');
      const hero = page.locator('.eb-hero');
      await expect(
        hero.locator('.eb-dial__score'),
        'TC-315 ER-1: the overall score is 62',
      ).toHaveText('62');
      await expect(hero.locator('.eb-dial__of'), 'TC-315 ER-1: out of 100').toHaveText('/ 100');
      await expect(hero.locator('.eb-eyebrow'), 'TC-315 ER-1: the band is provisional').toHaveText(
        'Provisional band',
      );
      await expect(hero.locator('.eb-band'), 'TC-315 ER-1: band B1+').toHaveText('B1+');
      await expect(hero.locator('.eb-band-label'), 'TC-315 ER-1: Independent user').toHaveText(
        'Independent user',
      );
      await expect(
        hero.locator('.eb-chip').nth(0),
        'TC-315 ER-1: 3 of 4 sections scored',
      ).toHaveText('3 of 4 sections scored');
      await expect(hero.locator('.eb-chip').nth(1), 'TC-315 ER-1: Speaking is pending').toHaveText(
        'Speaking pending · 12 min',
      );
      await expect(
        page.getByRole('progressbar', { name: 'Assessment progress' }),
        'TC-315 ER-1: Assessment progress is 75%',
      ).toHaveAttribute('aria-valuenow', '75');
      await expect(hero, 'TC-315 ER-1: the band is provisional until Speaking').toContainText(
        'Your band is provisional until the speaking section is submitted.',
      );

      for (const [label, score, band] of [
        ['Reading', '68', 'B2'],
        ['Writing', '57', 'B1'],
        ['Listening', '61', 'B1'],
      ]) {
        await expect(
          section(label).locator('.eb-skill__head'),
          `TC-315 ER-2: ${label} is Scored`,
        ).toContainText('Scored');
        await expect(
          section(label).locator('.eb-skill__big'),
          `TC-315 ER-2: ${label} scored ${score}`,
        ).toHaveText(score);
        await expect(
          section(label).locator('.eb-skill__of'),
          `TC-315 ER-2: ${label} is CEFR ${band}`,
        ).toHaveText(`/ 100 · CEFR ${band}`);
      }
      await expect(
        section('Listening'),
        'TC-315 ER-2: Listening has no written report',
      ).toContainText('No written report for this section.');

      const speaking = section('Speaking');
      await expect(
        speaking.locator('.eb-skill__head'),
        'TC-315 ER-3: Speaking is Pending',
      ).toContainText('Pending');
      await expect(
        speaking.locator('.eb-skill__big'),
        'TC-315 ER-3: its score is "--", not 0',
      ).toHaveText('--');
      await expect(speaking.locator('.eb-skill__of'), 'TC-315 ER-3: CEFR —').toHaveText(
        '/ 100 · CEFR —',
      );
      await expect(
        speaking.locator('.eb-meter-head--sub'),
        'TC-315 ER-3: its sub-scores read "--"',
      ).toHaveText([/Fluency\s*--/, /Pronunciation\s*--/, /Interaction\s*--/]);
      await expect(
        speaking.getByRole('button', { name: 'Start speaking test' }),
        'TC-315 ER-3: it offers Start speaking test',
      ).toBeVisible();
    });

    await test.step('2. Click View AI report on the Reading card', async () => {
      await section('Reading').getByRole('button', { name: 'View AI report' }).click();
      await expect(
        section('Reading').locator('.eb-report'),
        "TC-315 ER-4: the assessor's note",
      ).toHaveText(
        'Reads at pace and handles detail well; inference under time pressure is the next lift.',
      );
      await expect(
        section('Reading').getByRole('button', { name: 'Hide AI report' }),
        'TC-315 ER-4: the button now reads Hide AI report',
      ).toBeVisible();
    });
  });

  test('The English baseline allows one attempt per semester @TC-316', async ({ page, signIn }) => {
    await signIn('student');
    const notice = page.locator('.eb-notice');
    const resuming = 'Resuming the attempt already open for this semester.';
    const taken = page.locator('.eb-hero .eb-chip').filter({ hasText: /^Taken / });
    let takenOn = '';

    await test.step('1. Open /student/english', async () => {
      await page.goto('/student/english');
      await expect(
        page.getByRole('button', { name: 'Resume assessment' }),
        'TC-316 ER-1: the main button reads Resume assessment',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Start assessment' }),
        'TC-316 ER-1: no Start assessment is offered',
      ).toHaveCount(0);
      takenOn = await taken.innerText();
    });

    await test.step('2. Click Resume assessment', async () => {
      await page.getByRole('button', { name: 'Resume assessment' }).click();
      await expect(notice, 'TC-316 ER-2: the page says the attempt is resumed').toContainText(
        resuming,
      );
      await expect(page.locator('.eb-dial__score'), 'TC-316 ER-2: the same attempt, 62').toHaveText(
        '62',
      );
      await expect(
        page.getByText('3 of 4 sections scored'),
        'TC-316 ER-2: still 3 of 4 sections scored, not a blank attempt',
      ).toBeVisible();
      await expect(taken, 'TC-316 ER-2: taken on the same day as before').toHaveText(takenOn);
    });

    await test.step('3. Reload the page', async () => {
      await page.reload();
      await page.getByRole('button', { name: 'Start speaking test' }).waitFor();
    });

    await test.step('4. Click Start speaking test on the Speaking card', async () => {
      await page.getByRole('button', { name: 'Start speaking test' }).click();
      await expect(notice, 'TC-316 ER-3: the same message appears again').toContainText(resuming);
      await expect(
        page.getByText('3 of 4 sections scored'),
        'TC-316 ER-3: the attempt is unchanged',
      ).toBeVisible();
      await expect(taken, 'TC-316 ER-3: taken on the same day as before').toHaveText(takenOn);
    });
  });

  test('Downloading the English baseline report @TC-317', async ({ page, signIn }) => {
    await signIn('student');

    await test.step('1. Open /student/english', async () => {
      await page.goto('/student/english');
      await expect(
        page.getByRole('button', { name: 'Download report' }),
        'arrange: the attempt has loaded',
      ).toBeEnabled();
    });

    await test.step('2. Click Download report', async () => {
      const saved = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Download report' }).click();
      const file = await saved;
      expect(file.suggestedFilename(), 'TC-317 ER-1: the file is english-baseline.pdf').toBe(
        'english-baseline.pdf',
      );
      const bytes = fs.readFileSync(await file.path());
      expect(bytes.subarray(0, 5).toString('latin1'), 'TC-317 ER-1: the file is a PDF').toBe(
        '%PDF-',
      );
    });
  });

  // --------------------------------------------------------- mentor log --

  test('The Faculty / TPO Log shows the meeting history and SWOC @TC-319', async ({
    page,
    signIn,
  }) => {
    await signIn('student');

    await test.step('1. Open /student/mentor-log', async () => {
      await page.goto('/student/mentor-log');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Faculty / TPO Log' }),
        'TC-319 ER-1: the Faculty / TPO Log opens',
      ).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Request a meeting' }),
        'TC-319 ER-1: with a Request a meeting button',
      ).toBeEnabled();

      const swoc = page.locator('.swoc-card');
      await expect(swoc.getByRole('heading'), 'TC-319 ER-2: the SWOC card is shown').toContainText(
        'SWOC — Strengths, Weaknesses, Opportunities, Challenges',
      );
      const lines: [string, string, string, string][] = [
        ['Strength', 'Strong analytical and quantitative skills.', 'Test Mentor', 'Your mentor'],
        [
          'Weakness',
          'Needs structured problem-solving practice.',
          'Main Admin (seed)',
          'Placement cell',
        ],
        [
          'Opportunity',
          'Fintech internships opening this quarter.',
          'Main Admin (seed)',
          'Programme',
        ],
        ['Challenge', 'Public speaking under time pressure.', 'Test Mentor', 'Your mentor'],
      ];
      for (const [quadrant, text, author, source] of lines) {
        const line = swoc
          .locator('.swoc-box')
          .filter({ has: page.locator('b', { hasText: new RegExp(`^${quadrant}$`) }) })
          .locator('.swoc-line')
          .filter({ hasText: text });
        await expect(
          line.locator('.swoc-who'),
          `TC-319 ER-2: ${quadrant} line by ${author}`,
        ).toHaveText(author);
        await expect(
          line.locator('.swoc-src'),
          `TC-319 ER-2: ${quadrant} line from ${source}`,
        ).toHaveText(`· ${source}`);
        await expect(
          line.locator('.swoc-when'),
          `TC-319 ER-2: ${quadrant} line is dated`,
        ).toHaveText(/^· \w{3} \d{1,2}, \d{4}$/);
      }

      const meeting = page.locator('.meeting').filter({ hasText: '1:1 review' });
      await expect(
        meeting.locator('.title'),
        'TC-319 ER-3: the seeded meeting and place',
      ).toHaveText(/^\s*1:1 review\s*· Cabin 3\s*$/);
      await expect(meeting.locator('.chip'), 'TC-319 ER-3: 1:1 scheduled').toContainText(
        '1:1 scheduled',
      );
      await expect(meeting.locator('.note'), 'TC-319 ER-3: its note').toHaveText(
        'Discussed placement readiness; strong on analytics, work on GD delivery.',
      );
      await expect(meeting.locator('.by'), 'TC-319 ER-3: logged by Test Mentor').toHaveText(
        'Logged by Test Mentor',
      );
    });
  });

  test('Requesting a meeting with the mentor @TC-320', async ({
    page,
    signIn,
    request,
  }, testInfo) => {
    const student = await signIn('student');
    const reason = `E2E meeting ${runId()}`;
    const noteText = `Meeting requested by the student. ${reason} Preferred time: Thursday afternoon.`;
    const requestButton = page.getByRole('button', { name: 'Request a meeting' });
    const send = page.getByRole('button', { name: 'Send request' });
    const form = page.locator('.request-card');

    try {
      await test.step('1. Open /student/mentor-log', async () => {
        await page.goto('/student/mentor-log');
        await expect(page.locator('.meeting').first(), 'arrange: the log has loaded').toBeVisible();
      });

      await test.step('2. Click Request a meeting', async () => {
        await requestButton.click();
        await expect(
          form.getByRole('heading', { name: 'Request a meeting' }),
          'TC-320 ER-1: the request form opens',
        ).toBeVisible();
        await expect(form, 'TC-320 ER-1: saying where the request goes').toContainText(
          'This goes to your mentor and appears in the log below, so you both have the same record of what was asked.',
        );
        await expect(
          send,
          'TC-320 ER-1: Send request is disabled while the reason is empty',
        ).toBeDisabled();
        await expect(
          requestButton,
          'TC-320 ER-1: Request a meeting is disabled while the form is open',
        ).toBeDisabled();
      });

      await test.step('3. Type the test reason in the "What would you like to discuss?" box', async () => {
        await page.getByLabel('What would you like to discuss?').fill(reason);
        await expect(send, 'TC-320 ER-2: Send request becomes enabled').toBeEnabled();
      });

      await test.step('4. Type Thursday afternoon in the "Preferred time (optional)" box', async () => {
        await page.getByLabel('Preferred time (optional)').fill('Thursday afternoon');
      });

      await test.step('5. Click Send request', async () => {
        await send.click();
        await expect(form, 'TC-320 ER-3: the form closes').toHaveCount(0);
        await expect(
          page.locator('.log-notice'),
          'TC-320 ER-3: the page says it was sent',
        ).toContainText('Sent to Test Mentor. It appears in your meeting log below.');
        const newest = page.locator('.meeting').first();
        await expect(
          newest.locator('.title'),
          'TC-320 ER-3: "Meeting requested" tops the history',
        ).toHaveText('Meeting requested');
        await expect(
          newest.locator('.chip'),
          'TC-320 ER-3: with the chip "Note only"',
        ).toContainText('Note only');
        await expect(
          newest.locator('.note'),
          'TC-320 ER-3: the note carries the reason and time',
        ).toHaveText(noteText);
        await expect(newest.locator('.by'), 'TC-320 ER-3: logged by Test Mentor').toHaveText(
          'Logged by Test Mentor',
        );

        await apiSignIn(request, testInfo, 'mentor');
        const notes = await request.get(`/api/mentor/students/${student.studentId}/notes`);
        expect(notes.ok(), "TC-320 ER-4: the mentor can read the student's notes").toBe(true);
        const texts = ((await notes.json()) as { note_text: string }[]).map((n) => n.note_text);
        expect(texts, "TC-320 ER-4: the request is among the mentor's notes").toContain(noteText);
      });
    } finally {
      // The mentor removes the request, as they could on the Mentee Log.
      await apiSignIn(request, testInfo, 'mentor');
      const notes = await request.get(`/api/mentor/students/${student.studentId}/notes`);
      if (notes.ok()) {
        for (const note of (await notes.json()) as { id: string; note_text: string }[]) {
          if (note.note_text.includes(reason)) {
            await request.delete(`/api/mentor/students/${student.studentId}/notes/${note.id}`);
          }
        }
      }
    }
  });

  // --------------------------------------------------------- interviews --

  test('The Mock interviews screen before any interview @TC-322', async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const sessions = await page.request.get('/api/interview/sessions');
    // SKIPPED, not Blocked: an interview record is test data other cases
    // create on purpose (module 08 records one to test the office's screens),
    // not a broken environment. The full suite runs this module before module
    // 08, so a run on a fresh database always reaches the empty state.
    testInfo.skip(
      sessions.ok() && ((await sessions.json()) as unknown[]).length > 0,
      'the student already has a mock interview record, so the empty state cannot show. Reset the database.',
    );

    await test.step('1. Open /student/interviews', async () => {
      await page.goto('/student/interviews');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Mock interviews' }),
        'TC-322 ER-1: the Mock interviews page opens',
      ).toBeVisible();
      await expect(
        page.locator('.iv-scope'),
        'TC-322 ER-1: it says who can read the records',
      ).toHaveText(
        "These are kept on the college's server as part of your interview record. Your mentor and the placement office can read them. Clearing your conversation on the interview screen does not delete them — ask the placement cell if you need one removed.",
      );
      const empty = page.locator('.iv-empty');
      await expect(
        empty.locator('.iv-empty__title'),
        'TC-322 ER-2: "No mock interviews yet"',
      ).toHaveText('No mock interviews yet');
      await expect(empty, 'TC-322 ER-2: with the explanation').toContainText(
        'The AI interviewer asks one question at a time, out loud, and writes you a practice report at the end. Nothing here is graded and nothing counts towards placement.',
      );
      await expect(page.getByRole('table'), 'TC-322 ER-2: instead of a table').toHaveCount(0);
    });

    await test.step('2. Click Take your first interview', async () => {
      await page.getByRole('link', { name: 'Take your first interview' }).click();
      await expect(page, 'TC-322 ER-3: the browser goes to /student/assistant').toHaveURL(
        /\/student\/assistant$/,
      );
      await expect(
        page.getByRole('heading', { level: 1, name: 'Mock interview' }),
        'TC-322 ER-3: the Mock interview page',
      ).toBeVisible();
    });
  });

  // ---------------------------------------------------------- assistant --

  test('The mock interview page and its round picker @TC-324', async ({ page, signIn }) => {
    await signIn('student');
    // The selected pill's name gains a "✓" (drawn by CSS), hence the pattern.
    const round = (name: string) => page.getByRole('radio', { name: new RegExp(`^(✓ )?${name}$`) });
    const status = page.locator('.stage__status');

    await test.step('1. Open /student/assistant', async () => {
      await page.goto('/student/assistant');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Mock interview' }),
        'TC-324 ER-1: the Mock interview page opens',
      ).toBeVisible();
      await expect(
        page.getByText(
          'The interviewer hears your voice and nothing else — it cannot see your marks, attendance or USN.',
        ),
        'TC-324 ER-1: it says what the interviewer can and cannot see',
      ).toBeVisible();
      await expect(status, 'TC-324 ER-1: the stage reads "Not connected"').toHaveText(
        'Not connected',
      );
      await expect(page.locator('.stage__caption'), 'TC-324 ER-1: with its caption').toHaveText(
        'Pick a round, then press Start when you are ready.',
      );
      await expect(
        page.getByRole('list', { name: 'Interview stages' }).getByRole('listitem'),
        'TC-324 ER-1: the four stages',
      ).toHaveText(['Opening', 'Probing', 'Deep dive', 'Wrap-up']);
      await expect(
        page.getByRole('button', { name: 'Start interview' }),
        'TC-324 ER-1: Start interview is offered',
      ).toBeEnabled();

      await expect(
        page.getByRole('radiogroup', { name: 'Interview round' }).getByRole('radio'),
        'TC-324 ER-2: the five rounds',
      ).toHaveText(['General', 'HR', 'Marketing', 'Analytics', 'Finance']);
      await expect(round('General'), 'TC-324 ER-2: General is selected').toHaveAttribute(
        'aria-checked',
        'true',
      );
      await expect(page.locator('.who__title'), 'TC-324 ER-2: the general interviewer').toHaveText(
        'Campus panel interviewer',
      );
      await expect(page.locator('.who__sub'), 'TC-324 ER-2: the general round').toHaveText(
        'General placement round · Tier-1 MNC campus bar',
      );
      await expect(page.locator('.picker__blurb'), 'TC-324 ER-2: the general blurb').toHaveText(
        'Placement readiness across the board — no single track, and no scored report.',
      );
    });

    await test.step('2. In the round picker, click Finance', async () => {
      await round('Finance').click();
      await expect(round('Finance'), 'TC-324 ER-3: Finance is selected').toHaveAttribute(
        'aria-checked',
        'true',
      );
      await expect(round('General'), 'TC-324 ER-3: General is no longer selected').toHaveAttribute(
        'aria-checked',
        'false',
      );
      await expect(page.locator('.who__title'), 'TC-324 ER-3: the finance interviewer').toHaveText(
        'Managing Director, Finance',
      );
      await expect(page.locator('.who__sub'), 'TC-324 ER-3: the finance round').toHaveText(
        'Finance & valuation round · Tier-1 MNC campus bar',
      );
      await expect(page.locator('.picker__blurb'), 'TC-324 ER-3: the finance blurb').toHaveText(
        'DCF modelling, financial ratios, risk, valuation, M&A.',
      );
      await expect(status, 'TC-324 ER-3: nothing starts').toHaveText('Not connected');
    });
  });

  test("Start shows the college's interview terms, and Cancel records nothing @TC-325", async ({
    page,
    signIn,
  }, testInfo) => {
    await signIn('student');
    const consent = await page.request.get('/api/interview/consent');
    // SKIPPED, not Blocked, for the reason given in TC-322: agreeing to the
    // terms cannot be undone, and module 08 agrees on the student's behalf.
    testInfo.skip(
      ((await consent.json()) as { consent: unknown }).consent !== null,
      'the student has already agreed to the interview terms, so Start no longer shows them. Reset the database.',
    );
    const card = (await (await page.request.get('/api/interview/policy')).json()) as {
      policy: { source: string };
      usage: { completed: number };
    };
    if (card.policy.source !== 'default') {
      block(
        testInfo,
        `the college has its own interview policy (${card.policy.source}), so the terms differ from the default this case checks.`,
      );
    }
    const dialog = page.getByRole('dialog', { name: 'Before you start' });

    await test.step('1. Open /student/assistant', async () => {
      await page.goto('/student/assistant');
      await expect(
        page.getByRole('button', { name: 'Start interview' }),
        'arrange: the interview room has loaded',
      ).toBeEnabled();
    });

    await test.step('2. Click Start interview', async () => {
      await page.getByRole('button', { name: 'Start interview' }).click();
      await expect(dialog, 'TC-325 ER-1: the "Before you start" dialog opens').toBeVisible();
      await expect(
        dialog.getByRole('heading', { name: '1. The interviewer hears you live' }),
        'TC-325 ER-1: section 1',
      ).toBeVisible();
      await expect(dialog, 'TC-325 ER-1: it names where the audio goes').toContainText(
        "your microphone audio is streamed to Amazon's Nova Sonic model, running on AWS Bedrock so it can hear you and reply.",
      );
      await expect(dialog, 'TC-325 ER-1: and that no record is sent').toContainText(
        'Nothing from your student record — marks, attendance, USN, resume — is sent with it',
      );
      await expect(
        page.locator('.stage__status'),
        'TC-325 ER-1: the microphone is not asked for yet',
      ).toHaveText('Not connected');

      await expect(dialog, 'TC-325 ER-2: transcripts are kept for 180 days').toContainText(
        'It is deleted automatically after 180 days.',
      );
      await expect(dialog, 'TC-325 ER-2: the college does not record').toContainText(
        'Your college does not record these interviews.',
      );
      await expect(dialog, "TC-325 ER-2: today's allowance and the time limit").toContainText(
        `You have ${card.usage.completed} of 8 practice interviews used in the last 24 hours, and each one runs for up to 8 minutes.`,
      );
      await expect(dialog.getByRole('button'), 'TC-325 ER-2: the two buttons').toHaveText([
        'Cancel',
        'I agree — start the interview',
      ]);
    });

    await test.step('3. Click Cancel', async () => {
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await expect(dialog, 'TC-325 ER-3: the dialog closes').toHaveCount(0);
      await expect(page.locator('.stage__status'), 'TC-325 ER-3: still "Not connected"').toHaveText(
        'Not connected',
      );
      await expect(
        page.getByText(/Terms accepted/),
        'TC-325 ER-3: no "Terms accepted" line appears',
      ).toHaveCount(0);
      const after = await page.request.get('/api/interview/consent');
      expect(
        ((await after.json()) as { consent: unknown }).consent,
        'TC-325 ER-3: no consent was recorded',
      ).toBeNull();
    });
  });

  // --------------------------------------------------------- REEP Agent --

  test('Asking the REEP Agent, rating the answer and clearing the conversation @TC-328', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const cleared = await page.request.delete('/api/agent/conversation');
    expect(cleared.status(), 'arrange: the conversation starts empty').toBe(204);
    const question = 'How do I verify a skill?';
    const empty = page.getByText('How can I help today?');
    const clear = page.getByRole('button', { name: 'Clear conversation' });
    const answer = page
      .locator('.ag-row:not(.ag-row--user)')
      .filter({ has: page.locator('.ag-bubble') });

    await test.step('1. Open /student/agent', async () => {
      await page.goto('/student/agent');
      await expect(
        page.getByRole('heading', { level: 1, name: 'REEP Agent' }),
        'TC-328 ER-1: the REEP Agent page opens',
      ).toBeVisible();
      await expect(
        page.locator('.ag-disclaimer'),
        'TC-328 ER-1: it says what the agent cannot see',
      ).toContainText(
        'The REEP Agent is a general helper — it does not see your private records, marks or attendance.',
      );
      await expect(empty, 'TC-328 ER-1: the conversation is empty').toBeVisible();
      await expect(
        page.locator('.ag-starters--centred').getByRole('button'),
        'TC-328 ER-1: the four suggested questions',
      ).toHaveText([
        'What should I complete this week?',
        'Am I placement-ready?',
        'Show jobs I qualify for',
        question,
      ]);
      await expect(clear, 'TC-328 ER-1: Clear conversation is disabled').toBeDisabled();
    });

    await test.step('2. Click the suggested question How do I verify a skill?', async () => {
      await page.getByRole('button', { name: question }).click();
      await expect(
        page.locator('.ag-bubble--user'),
        'TC-328 ER-2: the question is your message',
      ).toHaveText(question);
      const bubble = answer.locator('.ag-bubble');
      await expect(bubble, 'TC-328 ER-2: the first reply opens with the greeting').toHaveText(
        /^Jai Shri Gurudev! /,
      );
      await expect(bubble, 'TC-328 ER-2: and explains how to get a skill verified').toContainText(
        'To get a skill like Power BI verified, upload a certificate or proof of completion as evidence and raise a skill claim with the level you are claiming.',
      );
      await expect(
        answer.getByText('Source: Verifying a skill (e.g. Power BI)'),
        'TC-328 ER-2: it cites the approved guidance',
      ).toBeVisible();
      for (const control of ['Copy', 'Helpful', 'Not helpful', 'Report']) {
        await expect(
          answer.getByRole('button', { name: control, exact: true }),
          `TC-328 ER-2: the answer offers ${control}`,
        ).toBeVisible();
      }
    });

    await test.step('3. Click the thumbs-up (Helpful) button under the answer', async () => {
      const helpful = answer.getByRole('button', { name: 'Helpful', exact: true });
      const rated = page.waitForResponse((r) => r.url().endsWith('/api/agent/feedback'));
      await helpful.click();
      await expect(helpful, 'TC-328 ER-3: Helpful is pressed').toHaveAttribute(
        'aria-pressed',
        'true',
      );
      await expect(
        answer.getByText('Thanks for the feedback'),
        'TC-328 ER-3: thanks are shown',
      ).toBeVisible();
      expect((await rated).status(), 'TC-328 ER-3: the server accepts the rating').toBe(200);
      await expect(helpful, 'TC-328 ER-3: so the button stays pressed').toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });

    await test.step('4. Click Clear conversation', async () => {
      await clear.click();
      await expect(empty, 'TC-328 ER-4: the conversation is empty again').toBeVisible();
      await expect(answer, 'TC-328 ER-4: the answer is gone').toHaveCount(0);
      await expect(clear, 'TC-328 ER-4: Clear conversation is disabled again').toBeDisabled();
    });

    await test.step('5. Reload the page', async () => {
      await page.reload();
      await expect(empty, 'TC-328 ER-5: still empty: it was cleared on the server').toBeVisible();
      await expect(page.locator('.ag-bubble'), 'TC-328 ER-5: no message is replayed').toHaveCount(
        0,
      );
    });
  });

  test('The floating orb opens the assistant dock with both tabs @TC-329', async ({
    page,
    signIn,
  }) => {
    await signIn('student');
    const dock = page.getByRole('dialog', { name: 'REEP assistant' });
    const openAsPage = dock.getByRole('link', { name: 'Open as a page' });

    await test.step('1. Open /student/jobs', async () => {
      await page.goto('/student/jobs');
      await expect(
        page.getByRole('heading', { level: 1, name: 'Jobs' }),
        'arrange: the Jobs page has opened',
      ).toBeVisible();
    });

    await test.step('2. Click the round assistant button at the bottom right of the screen', async () => {
      await page.getByRole('button', { name: 'Open the REEP assistant' }).click();
      await expect(dock, 'TC-329 ER-1: the REEP assistant dock opens').toBeVisible();
      await expect(
        dock.getByRole('tab', { name: 'Ask REEP' }),
        'TC-329 ER-1: on the Ask REEP tab',
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        dock.getByRole('tab', { name: 'Mock interview' }),
        'TC-329 ER-1: beside a Mock interview tab',
      ).toHaveAttribute('aria-selected', 'false');
      await expect(
        dock.locator('.ag-foot__note'),
        'TC-329 ER-1: its foot says what it cannot see',
      ).toHaveText('Does not see your marks, attendance or USN — for those, open your records.');
      await expect(
        openAsPage,
        'TC-329 ER-1: "Open as a page" leads to /student/agent',
      ).toHaveAttribute('href', '/student/agent');
    });

    await test.step('3. Type How do I apply for a job? in the message box and press Enter', async () => {
      const box = dock.getByRole('textbox', { name: 'Message the REEP Agent' });
      await box.fill('How do I apply for a job?');
      await box.press('Enter');
      await expect(
        dock.locator('.ag-bubble--user').last(),
        'TC-329 ER-2: the question is sent',
      ).toHaveText('How do I apply for a job?');
      await expect(
        dock.locator('.ag-row:not(.ag-row--user) .ag-bubble').last(),
        'TC-329 ER-2: the agent answers from the approved guidance',
      ).toContainText(
        'To apply for a job: open the posting and check the eligibility gates and your match percentage.',
      );
      await expect(
        dock.getByText('Source: Steps to apply for a job'),
        'TC-329 ER-2: under the source chip',
      ).toBeVisible();
    });

    await test.step('4. Click the Mock interview tab', async () => {
      await dock.getByRole('tab', { name: 'Mock interview' }).click();
      await expect(
        dock.getByRole('tab', { name: 'Mock interview' }),
        'TC-329 ER-3: the Mock interview tab is selected',
      ).toHaveAttribute('aria-selected', 'true');
      await expect(
        dock.getByRole('radiogroup', { name: 'Interview round' }),
        'TC-329 ER-3: the room shows its round picker',
      ).toBeVisible();
      await expect(
        dock.getByRole('button', { name: 'Start interview' }),
        'TC-329 ER-3: and a Start interview button',
      ).toBeVisible();
      await expect(
        openAsPage,
        'TC-329 ER-3: "Open as a page" now leads to /student/assistant',
      ).toHaveAttribute('href', '/student/assistant');
    });

    await test.step('5. Click the Close button at the top of the dock', async () => {
      await dock.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(dock, 'TC-329 ER-4: the dock closes').toHaveCount(0);
      await expect(
        page.getByRole('button', { name: 'Open the REEP assistant' }),
        'TC-329 ER-4: the round button reads "Open the REEP assistant" again',
      ).toBeVisible();
    });
  });
});
