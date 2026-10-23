/**
 * Where a posting on the Jobs board came from, as the three jobs screens say
 * it — the client's half of `source` on `JobRowOut`, `JobSheetRowOut` and
 * `JobSheetOut` (apps/api-py/app/job_scout.py, 2026-10-23).
 *
 * `office` is every posting a person typed on the Jobs sheet, and prints
 * nothing: the board has always been the office's. The other two are the
 * boards the daily job agent reads, and each prints a link back to its board,
 * because Adzuna's terms ask that every advert shown say "Jobs by Adzuna"
 * with a link to Adzuna, and Jooble's postings get the same courtesy rather
 * than a second, different shape.
 */

export type JobSource = 'office' | 'adzuna' | 'jooble';

export interface JobSourceCredit {
  /** The words on the screen, verbatim from each board's own wording. */
  label: string;
  /** The board's own site, opened in a new tab. */
  href: string;
}

const CREDITS: Record<Exclude<JobSource, 'office'>, JobSourceCredit> = {
  adzuna: { label: 'Jobs by Adzuna', href: 'https://www.adzuna.in' },
  jooble: { label: 'Jobs by Jooble', href: 'https://jooble.org' },
};

/** The credit line for an agent posting; null for the office's own. */
export function jobSourceCredit(source: string | null | undefined): JobSourceCredit | null {
  return source === 'adzuna' || source === 'jooble' ? CREDITS[source] : null;
}

/** True for a posting the job agent found: it is closed, never removed. */
export function isAgentPosting(source: string | null | undefined): boolean {
  return jobSourceCredit(source) !== null;
}
