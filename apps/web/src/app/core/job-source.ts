/**
 * Where a posting on the Jobs board came from, as the three jobs screens say
 * it — the client's half of `source` on `JobRowOut`, `JobSheetRowOut` and
 * `JobSheetOut` (apps/api-py/app/job_scout.py, 2026-10-23).
 *
 * `office` is every posting a person typed on the Jobs sheet, and prints
 * nothing: the board has always been the office's. `adzuna` and `jooble` are
 * the boards the daily job agent reads, and each prints a link back to its
 * board, because Adzuna's terms ask that every advert shown say "Jobs by
 * Adzuna" with a link to Adzuna, and Jooble's postings get the same courtesy
 * rather than a second, different shape. `careers` is a company's own job
 * feed: there is no board to credit, and Apply already opens the company's
 * page, so it prints where the posting came from and links nowhere.
 */

export type JobSource = 'office' | 'adzuna' | 'jooble' | 'careers';

export interface JobSourceCredit {
  /** The words on the screen, verbatim from each board's own wording. */
  label: string;
  /** The board's own site, opened in a new tab; null where there is none. */
  href: string | null;
  /** The line under the job description: what the text is, and where the rest is. */
  jdNote: string;
}

const BOARD_SUMMARY = "A summary from the job board. The full description is on the employer's page.";

const CREDITS: Record<Exclude<JobSource, 'office'>, JobSourceCredit> = {
  adzuna: { label: 'Jobs by Adzuna', href: 'https://www.adzuna.in', jdNote: BOARD_SUMMARY },
  jooble: { label: 'Jobs by Jooble', href: 'https://jooble.org', jdNote: BOARD_SUMMARY },
  careers: {
    label: "From the company's careers page",
    href: null,
    jdNote: "From the company's own careers page, shortened. The full description is behind Apply.",
  },
};

/** The credit line for an agent posting; null for the office's own. */
export function jobSourceCredit(source: string | null | undefined): JobSourceCredit | null {
  return source === 'adzuna' || source === 'jooble' || source === 'careers' ? CREDITS[source] : null;
}

/** True for a posting the job agent found: it is closed, never removed. */
export function isAgentPosting(source: string | null | undefined): boolean {
  return jobSourceCredit(source) !== null;
}
