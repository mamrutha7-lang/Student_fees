/**
 * Jobs sheet — the openings the placement office publishes (spec §14, board
 * `design/admin/JobsSheet.html`).
 *
 * Students and alumni read the same `jobs` table, so "Publish" is one POST and
 * the posting is on both boards at once.
 *
 * WHAT IS LIVE, AND ON WHICH ENDPOINT.
 *   GET    /api/admin/jobs             the grid, with each posting's applicant
 *                                       count, its college/course/tracks and its
 *                                       open-or-withdrawn state
 *   POST   /api/admin/jobs             Publish, and Duplicate (which is Publish
 *                                       with the selected posting typed back
 *                                       into the form)
 *   POST   /api/admin/jobs/{id}/close  Close posting — takes it off the student
 *                                       and alumni boards without touching the
 *                                       applications against it
 *   DELETE /api/admin/jobs/{id}        Remove — REFUSED 409 once anyone has
 *                                       applied, and for every posting the daily
 *                                       job agent found (it would be posted
 *                                       again the next morning), in the
 *                                       server's own words
 *   GET    /api/admin/jobs/scout-status the job agent's line over the grid:
 *                                       when it last ran, what it posted and
 *                                       closed, which boards have a key
 *   GET    /api/admin/criteria         the programme's placement criteria, which
 *                                       is where the form's two eligibility gates
 *                                       come from; read-only here
 *   GET    /api/admin/catalogue/courses the flat course picker the Scope field
 *                                       offers. `admin.catalogue`, NOT this
 *                                       screen's `admin.jobs` — so it can be
 *                                       refused, and the field then says so
 *
 * CLOSE AND REMOVE ARE BOTH HERE AND THEY ARE DIFFERENT ACTS. Remove deletes
 * the posting and the server refuses it with a 409 once anybody has applied,
 * because those applications are part of students' records. Close leaves every
 * row where it is and flips `jobs.status`, which is the ONLY thing the two
 * candidate feeds filter on — so it is the action for the posting that has done
 * its work, and the one that works on a posting people applied to. It is
 * one-way by design: the server has no reopen, so the button asks first.
 *
 * THE THREE FILTERS NARROW WHAT IS DRAWN, NEVER WHAT MAY BE READ. `GET
 * /api/admin/jobs` takes no query parameters and is scoped server-side (B1.4);
 * college, course and track are all on every row, so the selects narrow the
 * array already in hand — the Leave queue's Department filter, exactly. Picking
 * a college INCLUDES the postings that name none, because that is what a
 * student at that college actually sees (`app/jobs_visibility.py::
 * visible_clauses`); a filter that hid them would answer a different question
 * from the one the office is asking.
 *
 * WHAT THE BOARD DRAWS THAT NOTHING ANSWERS is listed once in the screen's
 * `.notice.accent` and again in the column's header tooltip: CTC and Openings,
 * which no backend task adds at all, and the eligible denominator under
 * "Applied". Each renders an em dash or a disabled control. A plausible number
 * in a screenshot is indistinguishable from working software.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AgGridAngular } from 'ag-grid-angular';
import type { GetRowIdParams, GridApi, GridReadyEvent } from 'ag-grid-community';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';
import { registerReepGrid } from '../../../shared/grid/grid-bootstrap';
import { reepGridTheme } from '../../../shared/grid/reep-grid-theme';
import { PluralPipe } from '../../../shared/text/plural.pipe';
import {
  COMFORTABLE_ROW_HEIGHT_PX,
  COMPACT_ROW_HEIGHT_PX,
  DEFAULT_PAGE_SIZE,
  DEGREE_LEVELS,
  EMPTY_DRAFT,
  EVERY_COLLEGE,
  EVERY_COURSE,
  EVERY_TRACK,
  INITIALLY_HIDDEN_COLUMN_IDS,
  NOT_READABLE,
  PAGE_SIZES,
  SEARCH_DEBOUNCE_MS,
  STATUS_FILTERS,
  escapeHtml,
  parseTracks,
  toJobPostingRow,
  type CatalogueCourseApi,
  type JobPostingApiRow,
  type JobPostingDraft,
  type JobPostingRow,
  type PlacementCriteriaApi,
  type ScoutStatusApi,
} from './job-posting-row';
import {
  DEFAULT_JOBS_COLUMN,
  JOBS_COLUMNS,
  JOBS_ROW_SELECTION,
  JOBS_SELECTION_COLUMN,
  TOGGLEABLE_JOBS_COLUMNS,
} from './jobs-grid';

/** The tab that reaches the funnel and the offers queue. Both live on the
 *  Placement screen (spec §15), which is guarded by its own capability. */
const PLACEMENT_CAPABILITY = 'admin.placement';

/** The filter value that means "every posting". */
const ANY = '';

/** The filter value that means "the postings which name NONE of this thing" —
 *  every college, every course, every track. An asterisk cannot collide with a
 *  uuid hex id or with a specialization code, and it is not a value any of the
 *  three columns can hold. */
const NAMES_NONE = '*';

/** What the three narrowing selects say when they have nothing to offer. A
 *  DEMOTION, not a phase: the filter works, this sheet simply has no posting
 *  that names one, so there is nothing to narrow by. */
const NOTHING_TO_NARROW_BY = (thing: string): string =>
  `No posting on this sheet names a ${thing}, so there is nothing to narrow by.`;

@Component({
  selector: 'app-admin-jobs-sheet',
  standalone: true,
  // RouterLink is REQUIRED for the Placement tabs and the funnel action: a
  // `routerLink` in a standalone component that does not import it is inert
  // markup — it renders, it looks like a link, and clicking it does nothing.
  imports: [RouterLink, AgGridAngular, PluralPipe],
  templateUrl: './jobs-sheet.component.html',
  styleUrl: './jobs-sheet.component.scss',
})
export class AdminJobsSheetComponent {
  private readonly auth = inject(AuthService);

  readonly gridTheme = reepGridTheme;
  readonly pageSizes = PAGE_SIZES;
  readonly statusFilters = STATUS_FILTERS;
  readonly degreeLevels = DEGREE_LEVELS;
  readonly notReadable = NOT_READABLE;
  readonly everyCollege = EVERY_COLLEGE;
  readonly everyCourse = EVERY_COURSE;
  readonly everyTrack = EVERY_TRACK;
  readonly anyFilter = ANY;
  readonly namesNone = NAMES_NONE;

  // --- what the server said ----------------------------------------------

  readonly apiRows = signal<JobPostingApiRow[] | null>(null);
  /** The active placement criteria, or null when this session may not read
   *  them (`/criteria` checks `admin.analytics`, not `admin.jobs`). */
  readonly criteria = signal<PlacementCriteriaApi | null>(null);

  /** The flat course list the Scope field picks from, or null when it has not
   *  answered. `/admin/catalogue/courses` checks `admin.catalogue`, which a
   *  faculty member granted only `admin.jobs` does not hold. */
  readonly catalogueCourses = signal<CatalogueCourseApi[] | null>(null);
  /** Why the Scope field is grey, in words, or null while it works. */
  readonly scopeUnavailable = signal<string | null>(null);

  /** What the daily job agent last did, or null until (or unless) the
   *  endpoint answers; the sheet is whole without it. */
  readonly scoutStatus = signal<ScoutStatusApi | null>(null);

  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);
  readonly busy = signal(false);

  // --- what the reader chose ---------------------------------------------

  /** '' = every posting, or one of STATUS_FILTERS' keys. Read from the closing
   *  date, which is the only state a posting records today. */
  readonly statusFilter = signal<string>(ANY);
  /** ANY, NAMES_NONE, or one id / one track code. All three narrow the array
   *  already in hand — see the class docstring. */
  readonly collegeFilter = signal<string>(ANY);
  readonly courseFilter = signal<string>(ANY);
  readonly trackFilter = signal<string>(ANY);
  /** Given to the grid as its quick filter. */
  readonly quickFilter = signal('');

  // --- the grid -----------------------------------------------------------

  private gridApi: GridApi<JobPostingRow> | null = null;
  readonly selectedRows = signal<JobPostingRow[]>([]);
  readonly pageSize = signal(DEFAULT_PAGE_SIZE);
  readonly rowHeight = signal(COMFORTABLE_ROW_HEIGHT_PX);
  readonly isCompact = computed(() => this.rowHeight() === COMPACT_ROW_HEIGHT_PX);
  readonly filteredRowCount = signal(0);
  readonly currentPage = signal(0);
  readonly totalPages = signal(0);
  readonly columnsPanelOpen = signal(false);
  readonly hiddenColumnIds = signal<string[]>([...INITIALLY_HIDDEN_COLUMN_IDS]);

  // --- the Post a job panel -----------------------------------------------

  readonly formOpen = signal(false);
  readonly draft = signal<JobPostingDraft>({ ...EMPTY_DRAFT });
  readonly formError = signal<string | null>(null);
  readonly saving = signal(false);

  readonly rowSelection = JOBS_ROW_SELECTION;
  readonly selectionColumn = JOBS_SELECTION_COLUMN;
  readonly defaultColumn = DEFAULT_JOBS_COLUMN;
  readonly columns = JOBS_COLUMNS;
  readonly toggleableColumns = TOGGLEABLE_JOBS_COLUMNS;

  readonly rowId = (params: GetRowIdParams<JobPostingRow>): string => params.data.jobId;

  constructor() {
    registerReepGrid();
    void this.loadPostings();
    void this.loadCriteria();
    void this.loadCatalogueCourses();
    void this.loadScoutStatus();
  }

  // ==================================================== derived state ====

  readonly isLoading = computed(() => this.apiRows() === null);

  readonly allRows = computed<JobPostingRow[]>(() => {
    const postings = this.apiRows();
    if (postings === null) return [];
    return postings.map((posting) => toJobPostingRow(posting));
  });

  /** What the grid shows: every posting, narrowed by the four filters, each of
   *  which reads a field the row already carries. */
  readonly visibleRows = computed<JobPostingRow[]>(() => {
    const status = this.statusFilter();
    const college = this.collegeFilter();
    const course = this.courseFilter();
    const track = this.trackFilter();
    return this.allRows().filter(
      (row) =>
        this.rowMatchesStatus(row, status) &&
        this.rowMatchesId(row.collegeId, college) &&
        this.rowMatchesId(row.courseId, course) &&
        this.rowMatchesTrack(row, track),
    );
  });

  /** On the student and alumni boards right now. NOT "deadline in the future":
   *  the feeds read `jobs.status` alone, so a posting past its date is still
   *  listed and is counted here — which is the number the office needs when it
   *  asks how many openings are live. */
  readonly listedCount = computed(() => this.allRows().filter((row) => !row.isWithdrawn).length);
  readonly closingThisWeekCount = computed(
    () => this.allRows().filter((row) => row.isClosingSoon).length,
  );
  /** Listed, and past the date printed on them — the row the office is meant to
   *  act on. Drawn only when there is at least one. */
  readonly pastDeadlineCount = computed(
    () => this.allRows().filter((row) => !row.isWithdrawn && row.isPastDeadline).length,
  );
  readonly applicationCount = computed(() => {
    let total = 0;
    for (const row of this.allRows()) total += row.applicants;
    return total;
  });

  readonly statusFilterLabel = computed(() => {
    const chosen = this.statusFilters.find((entry) => entry.key === this.statusFilter());
    return chosen?.label ?? 'All';
  });

  // --- the three B12.1 filters, built from the rows the server returned ----

  /** Every college NAMED by a posting on this sheet, once each. A posting that
   *  names none is not in this list — it is the separate NAMES_NONE option,
   *  because "published to everybody" is not one college among many. */
  readonly collegeOptions = computed(() => this.distinctLabels('college'));
  readonly courseOptions = computed(() => this.distinctLabels('course'));

  /** Every track code any posting names, once each, in code order. */
  readonly trackOptions = computed<string[]>(() => {
    const codes = new Set<string>();
    for (const row of this.allRows()) {
      for (const code of row.tracks) codes.add(code);
    }
    return [...codes].sort((left, right) => left.localeCompare(right));
  });

  readonly hasUnnarrowedCollege = computed(() =>
    this.allRows().some((row) => row.collegeId === null),
  );
  readonly hasUnnarrowedCourse = computed(() => this.allRows().some((row) => row.courseId === null));
  readonly hasUnnarrowedTrack = computed(() =>
    this.allRows().some((row) => row.tracks.length === 0),
  );

  /** Why a narrowing select is grey, or null while it has something to offer.
   *  This is the DEMOTION treatment applied to a live control: the filter
   *  works, the sheet simply has nothing for it to narrow by, and that is the
   *  reason on the control rather than a phase number. */
  readonly collegeFilterDisabledReason = computed(() => {
    if (this.isLoading()) return 'Reading the sheet…';
    return this.collegeOptions().length === 0 ? NOTHING_TO_NARROW_BY('college') : null;
  });
  readonly courseFilterDisabledReason = computed(() => {
    if (this.isLoading()) return 'Reading the sheet…';
    return this.courseOptions().length === 0 ? NOTHING_TO_NARROW_BY('course') : null;
  });
  readonly trackFilterDisabledReason = computed(() => {
    if (this.isLoading()) return 'Reading the sheet…';
    return this.trackOptions().length === 0 ? NOTHING_TO_NARROW_BY('track') : null;
  });

  readonly collegeFilterLabel = computed(() =>
    this.chosenLabel(this.collegeFilter(), this.collegeOptions(), EVERY_COLLEGE),
  );
  readonly courseFilterLabel = computed(() =>
    this.chosenLabel(this.courseFilter(), this.courseOptions(), EVERY_COURSE),
  );
  readonly trackFilterLabel = computed(() => {
    const chosen = this.trackFilter();
    if (chosen === ANY) return 'All';
    if (chosen === NAMES_NONE) return EVERY_TRACK;
    return chosen;
  });

  readonly selectedCount = computed(() => this.selectedRows().length);
  readonly hasSelection = computed(() => this.selectedCount() > 0);

  /** Duplicate and Remove act on ONE posting: "duplicate these four" has no
   *  meaning on a form with one title in it, and a multi-row delete is exactly
   *  the two-click mistake the roster screen refuses to offer. */
  readonly selectedPosting = computed<JobPostingRow | null>(() => {
    const selected = this.selectedRows();
    if (selected.length !== 1) return null;
    return selected[0];
  });

  readonly canDuplicate = computed(() => this.selectedPosting() !== null && !this.busy());

  readonly canRemove = computed(() => {
    const posting = this.selectedPosting();
    if (posting === null) return false;
    if (posting.applicants > 0) return false;
    if (posting.isAgentPosting) return false;
    return !this.busy();
  });

  /** Close is the action that works on the posting Remove refuses: it leaves
   *  every application where it is. The only thing that greys it is a posting
   *  already withdrawn — the server is idempotent there, but a button that
   *  reports success without changing anything is a button that lies. */
  readonly canClose = computed(() => {
    const posting = this.selectedPosting();
    if (posting === null) return false;
    if (posting.isWithdrawn) return false;
    return !this.busy();
  });

  readonly closeHint = computed(() => {
    const posting = this.selectedPosting();
    if (posting === null) return 'Tick one posting to take it off the boards';
    if (posting.isWithdrawn) return 'Already withdrawn — it is off both boards';
    return 'Take it off the student and alumni boards, keeping every application against it';
  });

  /** Why Remove is grey, in the sheet's own words — the same rule the server
   *  states in its 409. */
  readonly removeHint = computed(() => {
    const posting = this.selectedPosting();
    if (posting === null) return 'Tick one posting to remove it';
    if (posting.applicants > 0) {
      return 'Cannot be removed — students have applied';
    }
    if (posting.isAgentPosting) {
      return 'Found by the job agent — close it instead, or it is posted again tomorrow';
    }
    return 'Remove from the sheet';
  });

  /** The job agent's line over the grid: when it last ran and what it did, or
   *  that it is off. Null hides the line, while the endpoint has not answered. */
  readonly scoutLine = computed<string | null>(() => {
    const scout = this.scoutStatus();
    if (scout === null) return null;
    const boards = scout.sources
      .map((source) => `${source.name} ${source.configured ? 'on' : 'off'}`)
      .join(' · ');
    if (!scout.active) {
      return scout.last_run_at === null
        ? `Job agent off: no job board has a key (${boards})`
        : `Job agent off · last ran ${this.formatRunAt(scout.last_run_at)}`;
    }
    if (scout.last_run_at === null) return `Job agent on, first run at 06:00 IST · ${boards}`;
    const failed = Object.keys(scout.errors);
    return [
      `Job agent last ran ${this.formatRunAt(scout.last_run_at)}`,
      `${scout.created ?? 0} new`,
      `${scout.closed ?? 0} closed`,
      boards,
      ...(failed.length ? [`${failed.join(' and ')} failed`] : []),
    ].join(' · ');
  });

  readonly scoutFailed = computed(() => Object.keys(this.scoutStatus()?.errors ?? {}).length > 0);

  /** The header button's two states, named here rather than branched in the
   *  template (09-coding-standards.md §2). */
  readonly postingFormToggleIcon = computed(() => (this.formOpen() ? 'close' : 'add'));
  readonly postingFormToggleLabel = computed(() =>
    this.formOpen() ? 'Close the form' : 'Post a job',
  );
  readonly publishButtonLabel = computed(() => (this.saving() ? 'Publishing…' : 'Publish'));

  readonly canPublish = computed(() => {
    const draft = this.draft();
    if (draft.title.trim() === '') return false;
    if (draft.company.trim() === '') return false;
    return !this.saving();
  });

  /** The eligibility gates the posting inherits, as text. Never a zero when the
   *  criteria are unreadable: a missing gate and a gate of 0 backlogs mean
   *  opposite things to the office. */
  readonly minimumCgpaLabel = computed(() => {
    const criteria = this.criteria();
    if (criteria === null) return 'Programme default';
    return `${criteria.min_cgpa}`;
  });

  readonly maximumBacklogsLabel = computed(() => {
    const criteria = this.criteria();
    if (criteria === null) return 'Programme default';
    return `${criteria.max_live_backlogs}`;
  });

  /** The skills a Duplicate brought with it, as one line. Empty for a posting
   *  typed from scratch, which is every posting this form can otherwise make:
   *  nothing on this screen writes skills, so they are shown, not edited. */
  readonly carriedSkillsLabel = computed(() => this.draft().requiredSkills.join(', '));
  readonly hasCarriedSkills = computed(() => this.draft().requiredSkills.length > 0);

  // --- the posting form's Scope field -------------------------------------

  /** Whether the Scope selects can be used at all. False while the list is in
   *  flight, and false for good when `/admin/catalogue/courses` was refused,
   *  could not be reached, or came back empty: the field is then disabled with
   *  THAT as its reason, and a posting published from this form names no
   *  college and no course, which puts it in front of everybody. That is stated
   *  on the form rather than left to be discovered. */
  readonly scopePickerReady = computed(
    () => this.scopeUnavailable() === null && this.catalogueCourses() !== null,
  );

  /** The sentence under the Scope field: why it is grey, or what it does. */
  readonly scopeNote = computed(() => {
    const refused = this.scopeUnavailable();
    if (refused !== null) return refused;
    if (this.catalogueCourses() === null) return 'Reading the course list…';
    return 'Leave both empty to publish to every college and course — that is what every posting made before this field did.';
  });

  /** Every college the catalogue named, once each. Built from the course list
   *  rather than from `/admin/colleges`, which is a second request behind a
   *  second capability for a list this one already carries. */
  readonly scopeColleges = computed<{ id: string; label: string }[]>(() => {
    const seen = new Map<string, string>();
    for (const course of this.catalogueCourses() ?? []) {
      if (course.college_id === null) continue;
      seen.set(course.college_id, course.college ?? 'Unnamed college');
    }
    return [...seen.entries()]
      .map(([id, label]) => ({ id, label }))
      .sort((left, right) => left.label.localeCompare(right.label));
  });

  /** The courses the Course select offers: every one the catalogue returned, or
   *  just the chosen college's once a college is picked. */
  readonly scopeCourses = computed<CatalogueCourseApi[]>(() => {
    const courses = this.catalogueCourses() ?? [];
    const college = this.draft().collegeId;
    if (college === '') return courses;
    return courses.filter((course) => course.college_id === college);
  });

  /** "MBA · Management Studies" — the course, disambiguated by its department,
   *  because a deployment can teach the same code under two of them. */
  scopeCourseLabel(course: CatalogueCourseApi): string {
    const name = `${course.code} · ${course.name}`;
    return course.department === null ? name : `${name} · ${course.department}`;
  }

  /** What the posting being drafted will actually be published to, in the same
   *  words the grid's cells use, so the form and the sheet agree. */
  readonly scopeSentence = computed(() => {
    const draft = this.draft();
    const college =
      draft.collegeId === ''
        ? EVERY_COLLEGE
        : this.scopeColleges().find((entry) => entry.id === draft.collegeId)?.label ?? EVERY_COLLEGE;
    const course =
      draft.courseId === ''
        ? EVERY_COURSE
        : (this.catalogueCourses() ?? []).find((entry) => entry.id === draft.courseId)?.code ??
          EVERY_COURSE;
    const tracks = parseTracks(draft.tracks);
    const trackLabel = tracks.length === 0 ? EVERY_TRACK : tracks.join(' · ');
    return `${college} · ${course} · ${trackLabel}`;
  });

  /** The typed track codes as they will be sent, so the reader sees the
   *  normalisation before pressing Publish rather than after. */
  readonly draftTracks = computed(() => parseTracks(this.draft().tracks));

  /** Choosing a college clears a course that does not sit in it: a posting
   *  naming a college and a course from a different one matches no student at
   *  all, which reads on the sheet as published and on every board as absent.
   *
   *  AND "EVERY COLLEGE" CLEARS THE COURSE TOO, because a course names a
   *  college: "every college, but only the MBA" is a sentence the spine cannot
   *  mean, and leaving the course behind would publish under a narrowing the
   *  reader has just said they did not want. The sentence under the field
   *  redraws as it happens, so the change is never silent. */
  setScopeCollege(collegeId: string): void {
    const chosenCourse = (this.catalogueCourses() ?? []).find(
      (course) => course.id === this.draft().courseId,
    );
    const keepsCourse = chosenCourse !== undefined && chosenCourse.college_id === collegeId;
    this.draft.update((draft) => ({
      ...draft,
      collegeId,
      courseId: keepsCourse ? draft.courseId : '',
    }));
  }

  /** A course names exactly one college through the spine, so picking one sets
   *  both — there is no second question to ask. */
  setScopeCourse(courseId: string): void {
    const course = (this.catalogueCourses() ?? []).find((entry) => entry.id === courseId);
    this.draft.update((draft) => ({
      ...draft,
      courseId,
      collegeId: course?.college_id ?? (courseId === '' ? draft.collegeId : ''),
    }));
  }

  /** The Placement tabs are hidden from a session that would be bounced off
   *  that route. This is a CONVENIENCE, not authorisation — the route's own
   *  capability guard and the API are what refuse. */
  readonly canSeePlacement = computed(() => {
    const held = this.auth.session()?.capabilities ?? [];
    return held.includes(PLACEMENT_CAPABILITY);
  });

  /** Main's own words for each empty case, kept exactly. */
  readonly emptyMessage = computed(() => {
    if (this.quickFilter().trim() !== '') {
      return `No posting matches “${this.quickFilter().trim()}”.`;
    }
    if (this.collegeFilter() !== ANY || this.courseFilter() !== ANY || this.trackFilter() !== ANY) {
      return 'No posting is published to that college, course or track.';
    }
    if (this.statusFilter() !== ANY) return 'No posting is in that state.';
    return 'No openings on the sheet yet — publish the first one above.';
  });

  /** AG Grid's no-rows overlay takes a string of HTML, so the message is
   *  escaped here: it can carry whatever the reader typed into the filter. */
  readonly emptyOverlay = computed(
    () =>
      `<span style="font-size: 13px; color: var(--muted);">${escapeHtml(this.emptyMessage())}</span>`,
  );

  readonly pagerLabel = computed(() => {
    if (this.totalPages() === 0) return 'No pages';
    return `Page ${this.currentPage() + 1} of ${this.totalPages()}`;
  });

  readonly rowRangeLabel = computed(() => {
    const total = this.filteredRowCount();
    if (total === 0) return 'no rows';
    const firstRow = this.currentPage() * this.pageSize() + 1;
    const lastRow = Math.min(firstRow + this.pageSize() - 1, total);
    return `${firstRow} to ${lastRow} of ${total}`;
  });

  readonly isFirstPage = computed(() => this.currentPage() === 0);
  readonly isLastPage = computed(() => this.currentPage() + 1 >= this.totalPages());

  // =========================================================== loading ====

  private async loadPostings(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/jobs`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Could not load the jobs sheet.');
      this.apiRows.set((await response.json()) as JobPostingApiRow[]);
      this.selectedRows.set([]);
    } catch (failure) {
      this.apiRows.set([]);
      this.error.set(failure instanceof Error ? failure.message : 'Could not reach the server.');
    }
  }

  /** The job agent's status line. Best-effort: a refusal or an error leaves
   *  the line off and the sheet otherwise whole. */
  private async loadScoutStatus(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/jobs/scout-status`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      this.scoutStatus.set((await response.json()) as ScoutStatusApi);
    } catch {
      /* no line; nothing else on the sheet depends on it */
    }
  }

  private formatRunAt(iso: string): string {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return 'at an unknown time';
    return at.toLocaleString('en-GB', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  /** The two eligibility gates the form shows as inherited. `/criteria` checks
   *  `admin.analytics`, so a faculty member granted only `admin.jobs` is
   *  refused it — the fields then read "Programme default" rather than a
   *  number nobody computed. */
  private async loadCriteria(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/criteria`, {
        credentials: 'include',
      });
      if (!response.ok) return;
      this.criteria.set((await response.json()) as PlacementCriteriaApi);
    } catch {
      /* the gates read "Programme default" and the sheet is otherwise whole */
    }
  }

  /** The Scope field's course list. `/admin/catalogue/courses` answers to
   *  `admin.catalogue`, not to this screen's `admin.jobs`, so it can be
   *  refused — and an empty list is a different fact again: a deployment with
   *  no course in the catalogue has nothing to scope a posting to. Both are
   *  said in words on the field, because a grey select with no reason is the
   *  thing this phase exists to stop. */
  private async loadCatalogueCourses(): Promise<void> {
    try {
      const response = await fetch(`${environment.apiBase}/admin/catalogue/courses`, {
        credentials: 'include',
      });
      if (response.status === 403) {
        this.scopeUnavailable.set(
          'The course list answers to the Catalogue function, which this account does not hold — a posting published here goes to every college and course.',
        );
        return;
      }
      if (!response.ok) {
        this.scopeUnavailable.set(
          'The course list could not be read, so a posting published here goes to every college and course.',
        );
        return;
      }
      const courses = (await response.json()) as CatalogueCourseApi[];
      this.catalogueCourses.set(courses);
      if (courses.length === 0) {
        this.scopeUnavailable.set(
          'No course is in the catalogue yet, so there is nothing to narrow a posting to — it goes to every college and course.',
        );
        return;
      }
      this.scopeUnavailable.set(null);
    } catch {
      this.scopeUnavailable.set(
        'The course list could not be read, so a posting published here goes to every college and course.',
      );
    }
  }

  // ======================================================= the filters ====

  private rowMatchesStatus(row: JobPostingRow, chosen: string): boolean {
    if (chosen === 'listed') return !row.isWithdrawn;
    if (chosen === 'closing') return row.isClosingSoon;
    if (chosen === 'past') return !row.isWithdrawn && row.isPastDeadline;
    if (chosen === 'withdrawn') return row.isWithdrawn;
    return true;
  }

  /** A posting matches a chosen college or course when it NAMES it — or when it
   *  names none at all, because such a posting is on that college's board too.
   *  `app/jobs_visibility.py::visible_clauses` is the same predicate, and a
   *  filter that disagreed with it would answer a question nobody asked. */
  private rowMatchesId(on: string | null, chosen: string): boolean {
    if (chosen === ANY) return true;
    if (chosen === NAMES_NONE) return on === null;
    return on === null || on === chosen;
  }

  private rowMatchesTrack(row: JobPostingRow, chosen: string): boolean {
    if (chosen === ANY) return true;
    if (chosen === NAMES_NONE) return row.tracks.length === 0;
    return row.tracks.length === 0 || row.tracks.includes(chosen);
  }

  /** The distinct (id, label) pairs one spine column holds across the sheet. */
  private distinctLabels(level: 'college' | 'course'): { id: string; label: string }[] {
    const seen = new Map<string, string>();
    for (const row of this.allRows()) {
      const id = level === 'college' ? row.collegeId : row.courseId;
      if (id === null) continue;
      seen.set(id, level === 'college' ? row.collegeLabel : row.courseLabel);
    }
    return [...seen.entries()]
      .map(([id, label]) => ({ id, label }))
      .sort((left, right) => left.label.localeCompare(right.label));
  }

  private chosenLabel(
    chosen: string,
    options: { id: string; label: string }[],
    namesNoneLabel: string,
  ): string {
    if (chosen === ANY) return 'All';
    if (chosen === NAMES_NONE) return namesNoneLabel;
    return options.find((option) => option.id === chosen)?.label ?? 'All';
  }

  setStatusFilter(status: string): void {
    this.statusFilter.set(status);
  }

  setCollegeFilter(collegeId: string): void {
    this.collegeFilter.set(collegeId);
  }

  setCourseFilter(courseId: string): void {
    this.courseFilter.set(courseId);
  }

  setTrackFilter(track: string): void {
    this.trackFilter.set(track);
  }

  private searchTimer: ReturnType<typeof setTimeout> | null = null;

  onQuickFilterInput(event: Event): void {
    const typed = this.inputValue(event);
    if (this.searchTimer !== null) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.quickFilter.set(typed), SEARCH_DEBOUNCE_MS);
  }

  // ========================================================== the grid ====

  onGridReady(event: GridReadyEvent<JobPostingRow>): void {
    this.gridApi = event.api;
    this.refreshPagerState();
  }

  onSelectionChanged(): void {
    if (this.gridApi === null) return;
    this.selectedRows.set(this.gridApi.getSelectedRows());
  }

  onPaginationChanged(): void {
    this.refreshPagerState();
  }

  private refreshPagerState(): void {
    if (this.gridApi === null) return;
    this.filteredRowCount.set(this.gridApi.paginationGetRowCount());
    this.currentPage.set(this.gridApi.paginationGetCurrentPage());
    this.totalPages.set(this.gridApi.paginationGetTotalPages());
  }

  goToPreviousPage(): void {
    this.gridApi?.paginationGoToPreviousPage();
  }

  goToNextPage(): void {
    this.gridApi?.paginationGoToNextPage();
  }

  setPageSize(size: number): void {
    this.pageSize.set(size);
  }

  toggleDensity(): void {
    const next = this.isCompact() ? COMFORTABLE_ROW_HEIGHT_PX : COMPACT_ROW_HEIGHT_PX;
    this.rowHeight.set(next);
    this.gridApi?.resetRowHeights();
  }

  toggleColumnsPanel(): void {
    this.columnsPanelOpen.update((open) => !open);
  }

  isColumnVisible(columnId: string): boolean {
    return !this.hiddenColumnIds().includes(columnId);
  }

  toggleColumn(columnId: string): void {
    const wasVisible = this.isColumnVisible(columnId);
    this.hiddenColumnIds.update((hidden) => {
      if (wasVisible) return [...hidden, columnId];
      return hidden.filter((id) => id !== columnId);
    });
    this.gridApi?.setColumnsVisible([columnId], !wasVisible);
  }

  // ================================================= the posting form ====

  togglePostingForm(): void {
    this.formOpen.update((open) => !open);
    this.formError.set(null);
  }

  closePostingForm(): void {
    this.formOpen.set(false);
    this.formError.set(null);
  }

  setDraft<Key extends keyof JobPostingDraft>(key: Key, value: JobPostingDraft[Key]): void {
    this.draft.update((draft) => ({ ...draft, [key]: value }));
  }

  /** Type the ticked posting back into the form. Nothing is written until the
   *  reader presses Publish, so a duplicate is always a deliberate second
   *  posting rather than a click that silently doubled one. */
  duplicateSelectedPosting(): void {
    const posting = this.selectedPosting();
    if (posting === null) return;
    this.draft.set({
      title: posting.title,
      company: posting.company,
      location: posting.location ?? '',
      degreeLevel: posting.degreeLevel,
      closesOn: this.dateInputValueOf(posting.closesOn),
      applyUrl: posting.applyUrl ?? '',
      // Carried rather than dropped: `required_skills` is what the student
      // board matches a resume against, and a "duplicate" that published
      // without them is a different posting wearing the same title.
      requiredSkills: [...posting.requiredSkills],
      // And so is the audience. A duplicate that dropped the college, course
      // and tracks would publish the copy to EVERYBODY — the widest possible
      // change made by the narrowest possible gesture, and invisible on the
      // sheet until a student in another college applied to it.
      collegeId: posting.collegeId ?? '',
      courseId: posting.courseId ?? '',
      tracks: posting.tracks.join(', '),
    });
    this.formOpen.set(true);
    this.formError.set(null);
    this.flash.set('Copied into the form. Check the closing date, then Publish.');
  }

  async publish(): Promise<void> {
    const draft = this.draft();
    const title = draft.title.trim();
    const company = draft.company.trim();
    if (title === '' || company === '') {
      this.formError.set('Role and company are required');
      return;
    }
    const applyUrl = draft.applyUrl.trim();
    if (applyUrl !== '' && !/^https?:\/\//i.test(applyUrl)) {
      this.formError.set('The apply link must start with http:// or https://');
      return;
    }

    this.saving.set(true);
    this.formError.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/jobs`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          company,
          degree_level: draft.degreeLevel,
          location: draft.location.trim() === '' ? null : draft.location.trim(),
          closes_on: draft.closesOn === '' ? null : draft.closesOn,
          apply_url: applyUrl === '' ? null : applyUrl,
          required_skills: draft.requiredSkills,
          // B12.1. NULL and [] are what the server reads as "every college"
          // and "every track", so an untouched Scope field publishes exactly
          // what this form published before it existed.
          college_id: draft.collegeId === '' ? null : draft.collegeId,
          course_id: draft.courseId === '' ? null : draft.courseId,
          tracks: parseTracks(draft.tracks),
        }),
      });
      if (!response.ok) {
        this.formError.set(await this.detailOf(response, 'Could not publish that opening.'));
        return;
      }
      const published = (await response.json()) as JobPostingApiRow;
      this.apiRows.update((postings) => [published, ...(postings ?? [])]);
      this.draft.set({ ...EMPTY_DRAFT });
      this.formOpen.set(false);
      this.flash.set(`${published.title} at ${published.company} is on the sheet.`);
    } catch {
      this.formError.set('Could not reach the server.');
    } finally {
      this.saving.set(false);
    }
  }

  /** Take the posting off both candidate boards (B12.2). NOT a delete: every
   *  application against it stays, which is why this is the action that works
   *  on the posting Remove refuses with a 409.
   *
   *  IT ASKS FIRST, and Remove's own confirm is not the precedent — the server
   *  has NO REOPEN, so a mis-click here is undone only by publishing the
   *  posting again, which leaves the applications on the original. */
  async closeSelectedPosting(): Promise<void> {
    const posting = this.selectedPosting();
    if (posting === null || posting.isWithdrawn) return;
    const applicants =
      posting.applicants === 0
        ? ''
        : ` ${posting.applicants} application${posting.applicants === 1 ? '' : 's'} stay on the record.`;
    const agreed = window.confirm(
      `Take “${posting.title}” at ${posting.company} off the student and alumni boards?` +
        `${applicants} There is no reopen — republishing is the only way back.`,
    );
    if (!agreed) return;

    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(
        `${environment.apiBase}/admin/jobs/${posting.jobId}/close`,
        { method: 'POST', credentials: 'include' },
      );
      if (!response.ok) {
        this.error.set(await this.detailOf(response, 'Could not close that posting.'));
        return;
      }
      // The server answers the posting as it now stands, so the row is
      // replaced rather than patched — the applicant count and the status come
      // back together and cannot disagree.
      const closed = (await response.json()) as JobPostingApiRow;
      this.apiRows.update((postings) =>
        (postings ?? []).map((candidate) => (candidate.id === closed.id ? closed : candidate)),
      );
      // The tick goes with the act, as it does after Remove. Left ticked, the
      // toolbar would still be holding the row as it was BEFORE the close —
      // `selectedRows` only moves on a selection event — and Close would read
      // as still available on a posting that is already off the boards.
      this.gridApi?.deselectAll();
      this.selectedRows.set([]);
      this.flash.set(
        `${closed.title} at ${closed.company} is off both boards. Applications against it are untouched.`,
      );
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  /** The server refuses this with a 409 once anyone has applied, and its
   *  sentence names how many — that is the message the sheet shows. */
  async removeSelectedPosting(): Promise<void> {
    const posting = this.selectedPosting();
    if (posting === null) return;
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${environment.apiBase}/admin/jobs/${posting.jobId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response, 'Could not remove that posting.'));
        return;
      }
      this.apiRows.update((postings) =>
        (postings ?? []).filter((candidate) => candidate.id !== posting.jobId),
      );
      this.selectedRows.set([]);
      this.flash.set(`${posting.title} at ${posting.company} was removed from the sheet.`);
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.busy.set(false);
    }
  }

  dismissFlash(): void {
    this.flash.set(null);
  }

  dismissError(): void {
    this.error.set(null);
  }

  // ========================================================== plumbing ====

  inputValue(event: Event): string {
    const target = event.target as HTMLInputElement;
    return target.value;
  }

  selectValue(event: Event): string {
    const target = event.target as HTMLSelectElement;
    return target.value;
  }

  numberValue(event: Event): number {
    return Number(this.selectValue(event));
  }

  /** `<input type="date">` wants YYYY-MM-DD; the API answers a timestamp. */
  private dateInputValueOf(closesOn: string | null): string {
    if (closesOn === null) return '';
    const deadline = new Date(closesOn);
    if (Number.isNaN(deadline.getTime())) return '';
    return deadline.toISOString().slice(0, 10);
  }

  /** FastAPI answers a schema error with `detail` as a LIST; rendered raw it
   *  reads "[object Object]". Its refusals name the numbers — the 409 says how
   *  many students applied — so the server's own sentence is kept wherever
   *  there is one. */
  private async detailOf(response: Response, fallback: string): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((entry) => (entry as { msg?: string }).msg)
          .filter((message): message is string => typeof message === 'string');
        if (messages.length > 0) return messages.join(' ');
      }
    } catch {
      /* not JSON — fall through to the fallback sentence */
    }
    return fallback;
  }
}
