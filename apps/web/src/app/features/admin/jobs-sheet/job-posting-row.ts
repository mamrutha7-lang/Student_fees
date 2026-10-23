/**
 * The Jobs sheet's row: what `GET /api/admin/jobs` answers, and what the grid
 * draws from it.
 *
 * THE STATUS COLUMN ANSWERS "IS THIS ON THE STUDENT BOARD", NOT "HAS THE DATE
 * PASSED", and that correction is the reason this file changed with B12.2.
 * `app/jobs_visibility.py::visible_clauses` filters the two candidate feeds on
 * `Job.status == 'open'` ALONE and never looks at `closes_on` — so a posting
 * whose deadline went by last week is still on every student's board, and a
 * sheet that read "Closed" off that date told the office a posting was gone
 * while students were still applying to it. The column therefore leads with
 * `status`: `Withdrawn` when the office has closed it, and otherwise a reading
 * of the deadline in which a date in the past is `Past deadline` — a WARNING
 * that the posting is still listed, not a statement that it is not.
 *
 * WHAT THE BOARD DRAWS THAT NOTHING ANSWERS, and why it is an em dash here
 * rather than a number:
 *
 *   CTC        A posting records no pay. Nothing in 04-backend-changes.md adds
 *              one either — B12.3's "median CTC" is computed from OFFERS, which
 *              is a different record — so this is a gap in the plan, not a task
 *              waiting its turn, and it is reported as one.
 *   Eligible   The board reads "6 / 13": applicants over the students the
 *              posting is open to. The numerator is real; the denominator is a
 *              count of the students the college/course/track filters admit,
 *              which no endpoint answers, so the column shows the count alone.
 *
 * A plausible number in either of those cells is indistinguishable from working
 * software in a screenshot, which is the one thing this phase must not ship.
 *
 * AN ABSENT NARROWING IS THE WIDEST ONE, on both of the fields B12.1 added: a
 * NULL college and an EMPTY track list mean EVERY college and EVERY track —
 * `app/jobs_visibility.py` argues why at length, and it is the reading every
 * posting published before B12.1 depends on. So the cells say "Every college"
 * and "Every track" and never a dash, which would read as "nobody".
 */

import { isAgentPosting } from '../../../core/job-source';
import { plural } from '../../../shared/text/plural.pipe';

/** Rows per page the status bar offers, and the one it starts on. */
export const PAGE_SIZES = [10, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = 10;

/** The comfortable and compact row heights (01-design-system.md §4). */
export const COMFORTABLE_ROW_HEIGHT_PX = 40;
export const COMPACT_ROW_HEIGHT_PX = 36;

/** A posting inside this many days of its closing date reads "closing soon" —
 *  the same reading the student's jobs board gives it. */
export const CLOSING_SOON_DAYS = 7;

/** `jobs.status`, as `app/jobs_visibility.py` spells the two values. The feeds
 *  filter on `open` alone. */
export const JOB_OPEN = 'open';
export const JOB_CLOSED = 'closed';

const MILLISECONDS_IN_A_DAY = 86_400_000;

/** What an unreadable cell says. Never a zero: to a placement office a missing
 *  number and a zero mean opposite things. */
export const NOT_READABLE = '—';

/** Real columns that are off the board's six, so they start hidden and the
 *  Columns popover offers them rather than losing them: the degree level, the
 *  two per-posting eligibility gates the endpoint answers, and B12.1's college
 *  and course — which the filter row above the grid already states, so the
 *  columns are the detail view rather than the default one. */
export const INITIALLY_HIDDEN_COLUMN_IDS = [
  'level',
  'minCgpa',
  'maxLiveBacklogs',
  'college',
  'course',
];

/** Long enough that typing does not fire a filter per keystroke. */
export const SEARCH_DEBOUNCE_MS = 200;

// ------------------------------------------------------- the API payloads --

/** `JobSheetOut` in `app/routers/console.py`. */
export interface JobPostingApiRow {
  id: string;
  title: string;
  company: string;
  degree_level: string;
  location: string | null;
  apply_url: string | null;
  required_skills: string[];
  posted_on: string;
  closes_on: string | null;
  min_cgpa: number | null;
  max_live_backlogs: number | null;
  applicants: number;
  // B12.1. NULL on either id means EVERY college / every course. The names
  // travel beside the ids so the grid prints a label without a round trip per
  // row; a posting naming a college that has since been archived still
  // resolves, because the server reads the labels off the rows it returned.
  college_id: string | null;
  college_name: string | null;
  course_id: string | null;
  course_name: string | null;
  /** Specialization CODES the posting is for, compared against the student's
   *  own batch specialization (`audience_for_student`). EMPTY means every
   *  track. */
  tracks: string[];
  /** B12.2. `open` or `closed`, as the office asserts it — and the ONLY thing
   *  the student and alumni feeds filter on. */
  status: string;
  /** `office` for a posting typed here, else the board the daily job agent
   *  found it on (`adzuna`, `jooble`). Optional so a row from before the field
   *  existed reads as the office's, which it is. */
  source?: string;
}

/** `ScoutStatusOut` in `app/routers/console.py`: what the daily job agent
 *  last did. `last_run_at` null means it has never run here. */
export interface ScoutStatusApi {
  active: boolean;
  sources: { key: string; name: string; configured: boolean }[];
  last_run_at: string | null;
  created: number | null;
  closed: number | null;
  errors: Record<string, string>;
}

/** `AdminCatalogueCourseOut` in `app/routers/admin_catalogue.py`, narrowed to
 *  what the posting form's scope picker needs: one flat list of programmes
 *  across every college, each carrying its college's id and name. */
export interface CatalogueCourseApi {
  id: string;
  code: string;
  name: string;
  college_id: string | null;
  college: string | null;
  department: string | null;
}

/** `CriteriaOut` in `app/routers/console.py`, narrowed to the two gates the
 *  posting form inherits. */
export interface PlacementCriteriaApi {
  name: string;
  min_cgpa: number;
  max_live_backlogs: number;
}

// ------------------------------------------------------------- the screen --

export type DeadlineTone = 'good' | 'warn' | 'neutral';

/** One posting, dressed for the grid. */
export interface JobPostingRow {
  jobId: string;
  title: string;
  company: string;
  location: string | null;
  /** The pinned cell's second line: "Deloitte · Bengaluru". */
  companyAndLocation: string;
  degreeLevel: string;
  applyUrl: string | null;
  /** The slugs the student board matches a resume against. No screen writes
   *  them today, so the sheet carries them through a Duplicate rather than
   *  quietly publishing a copy that asks for nothing. */
  requiredSkills: string[];
  /** The posting's OWN eligibility gates, or null when it inherits the
   *  programme's placement criteria. Null is not zero and is not rendered as
   *  one — `routers/student.py` falls back to the criteria on null. */
  minCgpa: number | null;
  maxLiveBacklogs: number | null;
  applicants: number;
  postedOn: string;
  closesOn: string | null;
  /** The Deadline column: "22 Sep 2026", or main's "No deadline". */
  deadlineLabel: string;
  /** The Status column, in main's own words. */
  statusLabel: string;
  statusTone: DeadlineTone;
  /** True once the closing date has passed. NOT the same as off the boards:
   *  the feeds ignore this date entirely — see the module docstring. */
  isPastDeadline: boolean;
  /** True while the posting is listed and inside CLOSING_SOON_DAYS. */
  isClosingSoon: boolean;
  /** True once the office has closed the posting (B12.2). THIS is what takes
   *  it off the student and alumni boards. */
  isWithdrawn: boolean;
  // --- B12.1: where the posting is offered -------------------------------
  collegeId: string | null;
  courseId: string | null;
  /** "BGSCET", or EVERY_COLLEGE. Never a dash: a NULL college is the widest
   *  audience there is, and a dash reads as the narrowest. */
  collegeLabel: string;
  courseLabel: string;
  /** The specialization codes, upper case as the server stores them. */
  tracks: string[];
  /** "HR · DM", or EVERY_TRACK for the empty list. */
  tracksLabel: string;
  /** The Source column: "Office", "Adzuna", "Jooble" or "Company careers page". */
  sourceLabel: string;
  /** Found by the daily job agent. Such a posting is CLOSED, never removed:
   *  a removed row is forgotten and the agent would post it again. */
  isAgentPosting: boolean;
}

/** What the Source column calls each origin. */
const SOURCE_LABELS: Record<string, string> = {
  adzuna: 'Adzuna',
  jooble: 'Jooble',
  careers: 'Company careers page',
};
export const OFFICE_SOURCE_LABEL = 'Office';

/** What a cell says where the posting names no college, course or track. The
 *  server's reading, spelled out: absent is WIDEST, not narrowest. */
export const EVERY_COLLEGE = 'Every college';
export const EVERY_COURSE = 'Every course';
export const EVERY_TRACK = 'Every track';

/** What the sheet's Status filter offers.
 *
 *  "On the boards" and "Past deadline" are NOT opposites and the two buckets
 *  OVERLAP deliberately: a posting whose date went by is still on the boards
 *  until somebody closes it, and the office needs to be able to ask for exactly
 *  those. "Withdrawn" is the one bucket that is disjoint from the other two. */
export const STATUS_FILTERS = [
  { key: '', label: 'All' },
  { key: 'listed', label: 'On the boards' },
  { key: 'closing', label: 'Closing this week' },
  { key: 'past', label: 'Past deadline' },
  { key: 'withdrawn', label: 'Withdrawn' },
];

export const DEGREE_LEVELS = [
  { key: 'PG', label: 'PG' },
  { key: 'UG', label: 'UG' },
];

/** What a new posting starts as. */
export interface JobPostingDraft {
  title: string;
  company: string;
  location: string;
  degreeLevel: string;
  closesOn: string;
  applyUrl: string;
  /** Carried, never typed: the form has no skills field (the board has none
   *  either), but a Duplicate that dropped them would publish a posting that
   *  matches every resume equally. */
  requiredSkills: string[];
  // --- B12.1 -------------------------------------------------------------
  /** The course this posting is for, or '' for every course. The college is
   *  DERIVED from it rather than typed beside it: a course belongs to exactly
   *  one college through the spine, and two independent pickers is how a
   *  posting ends up naming a college the course does not sit in — which the
   *  feeds read as "no student matches both", i.e. a posting nobody sees. */
  courseId: string;
  /** Chosen on its own when the posting is for a whole college rather than one
   *  programme; set from the course when a course is chosen. */
  collegeId: string;
  /** Comma-separated specialization codes, exactly as typed. Normalised (trim,
   *  upper case, de-duplicated) by `app/routers/console.py::create_job`, which
   *  is the one writer, so this stays the reader's text until it is sent. */
  tracks: string;
}

export const EMPTY_DRAFT: JobPostingDraft = {
  title: '',
  company: '',
  location: '',
  degreeLevel: 'PG',
  closesOn: '',
  applyUrl: '',
  requiredSkills: [],
  courseId: '',
  collegeId: '',
  tracks: '',
};

/** The typed Tracks box, as the API wants it: trimmed, upper case, in order,
 *  no blanks and no repeats. The SERVER normalises too (`normalise_track` at
 *  the one writer) — this is so the form shows the reader what it will send,
 *  not a second source of truth. */
export function parseTracks(typed: string): string[] {
  const codes: string[] = [];
  for (const part of typed.split(',')) {
    const code = part.trim().toUpperCase();
    if (code !== '' && !codes.includes(code)) codes.push(code);
  }
  return codes;
}

// ------------------------------------------------------------- the helpers --

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** "22 Sep 2026". The grid's renderers run outside Angular, so DatePipe cannot
 *  reach them and the date is formatted once, here, when the row is built. */
export function formatDeadlineDate(closesOn: string | null): string {
  if (closesOn === null) return 'No deadline';
  const deadline = new Date(closesOn);
  if (Number.isNaN(deadline.getTime())) return 'No deadline';
  return deadline.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** The closing instant, or null when the posting has no deadline or carries
 *  one nothing can parse. */
function deadlineAt(closesOn: string | null): number | null {
  if (closesOn === null) return null;
  const deadline = new Date(closesOn).getTime();
  return Number.isNaN(deadline) ? null : deadline;
}

/** Whole days from now until the closing date; null when there is none. */
export function daysUntilDeadline(closesOn: string | null): number | null {
  const deadline = deadlineAt(closesOn);
  if (deadline === null) return null;
  return Math.ceil((deadline - Date.now()) / MILLISECONDS_IN_A_DAY);
}

/** Has the deadline passed? READ FROM THE TIMESTAMP, never from the day count
 *  above: `Math.ceil` of a small negative number is `-0` in JavaScript and
 *  `-0 < 0` is false, so a posting that shut a few hours ago counted as OPEN —
 *  in the stat strip, in the Status filter's Open bucket, and on the row, which
 *  read "Closes in 0d". */
export function hasClosed(closesOn: string | null): boolean {
  const deadline = deadlineAt(closesOn);
  return deadline !== null && deadline < Date.now();
}

/** Turn one API posting into the row the grid draws.
 *
 *  The status reads `jobs.status` FIRST and the closing date second — see the
 *  module docstring: the feeds filter on the column alone, so the date is a
 *  deadline the office printed and never the reason a posting is off the
 *  boards. The "Closes in 3 days" wording is the student's own board's
 *  (features/student/jobs), so the sheet and the board it publishes to say the
 *  same thing about the same posting. */
export function toJobPostingRow(posting: JobPostingApiRow): JobPostingRow {
  const daysLeft = daysUntilDeadline(posting.closes_on);
  const isPastDeadline = hasClosed(posting.closes_on);
  const isWithdrawn = posting.status === JOB_CLOSED;
  const isClosingSoon =
    !isWithdrawn && !isPastDeadline && daysLeft !== null && daysLeft <= CLOSING_SOON_DAYS;
  const tracks = [...posting.tracks];
  return {
    jobId: posting.id,
    title: posting.title,
    company: posting.company,
    location: posting.location,
    companyAndLocation:
      posting.location === null ? posting.company : `${posting.company} · ${posting.location}`,
    degreeLevel: posting.degree_level,
    applyUrl: posting.apply_url,
    requiredSkills: [...posting.required_skills],
    minCgpa: posting.min_cgpa,
    maxLiveBacklogs: posting.max_live_backlogs,
    applicants: posting.applicants,
    postedOn: posting.posted_on,
    closesOn: posting.closes_on,
    deadlineLabel: formatDeadlineDate(posting.closes_on),
    statusLabel: statusLabelOf(daysLeft, isWithdrawn, isPastDeadline, isClosingSoon),
    statusTone: statusToneOf(isWithdrawn, isPastDeadline, isClosingSoon),
    isPastDeadline,
    isClosingSoon,
    isWithdrawn,
    collegeId: posting.college_id,
    courseId: posting.course_id,
    collegeLabel: posting.college_id === null ? EVERY_COLLEGE : posting.college_name ?? 'Unnamed college',
    courseLabel: posting.course_id === null ? EVERY_COURSE : posting.course_name ?? 'Unnamed course',
    tracks,
    tracksLabel: tracks.length === 0 ? EVERY_TRACK : tracks.join(' · '),
    sourceLabel: SOURCE_LABELS[posting.source ?? ''] ?? OFFICE_SOURCE_LABEL,
    isAgentPosting: isAgentPosting(posting.source),
  };
}

function statusLabelOf(
  daysLeft: number | null,
  isWithdrawn: boolean,
  isPastDeadline: boolean,
  isClosingSoon: boolean,
): string {
  if (isWithdrawn) return 'Withdrawn';
  // Still on the boards, and that is the point of saying it this way: the
  // feeds do not read this date, so a posting past it is one the office should
  // look at, not one that has already gone.
  if (isPastDeadline) return 'Past deadline';
  if (daysLeft === null) return 'Open · no deadline';
  if (isClosingSoon) {
    if (daysLeft <= 0) return 'Closes today';
    return `Closes in ${plural(daysLeft, 'day')}`;
  }
  return 'Open';
}

function statusToneOf(
  isWithdrawn: boolean,
  isPastDeadline: boolean,
  isClosingSoon: boolean,
): DeadlineTone {
  if (isWithdrawn) return 'neutral';
  if (isPastDeadline) return 'warn';
  if (isClosingSoon) return 'warn';
  return 'good';
}
