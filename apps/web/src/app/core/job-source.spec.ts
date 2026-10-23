import { isAgentPosting, jobSourceCredit } from './job-source';

/**
 * The credit line every agent posting carries. Adzuna's terms name the exact
 * words, so the label is pinned verbatim rather than left to taste.
 */
describe('jobSourceCredit', () => {
  it('credits each board with its own words and a link to it', () => {
    expect(jobSourceCredit('adzuna')).toEqual({ label: 'Jobs by Adzuna', href: 'https://www.adzuna.in' });
    expect(jobSourceCredit('jooble')).toEqual({ label: 'Jobs by Jooble', href: 'https://jooble.org' });
  });

  it("prints nothing for the office's own postings or an unknown value", () => {
    expect(jobSourceCredit('office')).toBeNull();
    expect(jobSourceCredit(undefined)).toBeNull();
    expect(jobSourceCredit(null)).toBeNull();
    expect(jobSourceCredit('linkedin')).toBeNull();
  });
});

describe('isAgentPosting', () => {
  it('is true only for the two boards the job agent reads', () => {
    expect(isAgentPosting('adzuna')).toBe(true);
    expect(isAgentPosting('jooble')).toBe(true);
    expect(isAgentPosting('office')).toBe(false);
    expect(isAgentPosting(undefined)).toBe(false);
  });
});
