/**
 * The Jobs sheet's grid: its columns, its cell renderers and its selection
 * rules (board `design/admin/JobsSheet.html`, spec §14).
 *
 * AG Grid builds cell contents outside Angular's template compiler, so a
 * component-scoped class never reaches them. Every renderer below therefore
 * uses GLOBAL classes (`.chip`, `.icon` in styles/reep-v2.scss) and inline
 * styles, exactly as the roster grid does — and escapes every value it
 * interpolates, because a company name is somebody else's text.
 *
 * TRACK IS REAL NOW (B12.1) AND CTC IS STILL EMPTY. `jobs.tracks` decides
 * which students see a posting, so the column prints the codes — and prints
 * "Every track" for the empty list rather than a dash, because an empty list is
 * the WIDEST audience and a dash reads as the narrowest. CTC has no column
 * anywhere in the schema and no task adds one, so it renders an em dash and
 * says so in its header tooltip; the screen repeats that once in a
 * `.notice.accent`.
 *
 * THE STATUS COLUMN IS `jobs.status`, NOT THE DEADLINE. `job-posting-row.ts`
 * argues it: the two candidate feeds filter on the column alone, so a posting
 * past its date is still on the boards and the cell says "Past deadline" rather
 * than "Closed".
 */

import type {
  ColDef,
  GetQuickFilterTextParams,
  ICellRendererParams,
  RowSelectionOptions,
  SelectionColumnDef,
  ValueFormatterParams,
} from 'ag-grid-community';

import {
  EVERY_TRACK,
  INITIALLY_HIDDEN_COLUMN_IDS,
  NOT_READABLE,
  escapeHtml,
  type JobPostingRow,
} from './job-posting-row';

function renderPostingCell(params: ICellRendererParams<JobPostingRow>): string {
  const row = params.data;
  if (!row) return '';
  return (
    `<span style="display: inline-flex; flex-direction: column; line-height: 1.25; min-width: 0;">` +
    `<span style="font-weight: 600; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.title)}</span>` +
    `<span style="font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis;">${escapeHtml(row.companyAndLocation)}</span>` +
    `</span>`
  );
}

/** What the quick filter matches on the Posting column: the role AND the
 *  company and location drawn under it. The column is keyed on the title and
 *  the second line exists only in the renderer's HTML, so a box labelled
 *  "Search postings by role, company or location" found a posting by its role
 *  alone and answered "Rows: 0" for the company it is at. */
function quickFilterPosting(params: GetQuickFilterTextParams<JobPostingRow>): string {
  const row = params.data;
  if (row === undefined) return '';
  return `${row.title} ${row.companyAndLocation}`;
}

function renderStatusCell(params: ICellRendererParams<JobPostingRow>): string {
  const row = params.data;
  if (!row) return '';
  // Label and tone are this file's own words, never posting text.
  return `<span class="chip dot ${row.statusTone}">${escapeHtml(row.statusLabel)}</span>`;
}

function renderLevelCell(params: ICellRendererParams<JobPostingRow>): string {
  const row = params.data;
  if (!row) return '';
  return `<span class="chip">${escapeHtml(row.degreeLevel)}</span>`;
}

/** The one column the board draws and nothing answers. */
function formatNotReadable(): string {
  return NOT_READABLE;
}

/** The tracks a posting is published to. "Every track" is a STATEMENT, not a
 *  placeholder, so it is drawn as faint text rather than as chips that would
 *  read like one named track called "Every track". */
function renderTracksCell(params: ICellRendererParams<JobPostingRow>): string {
  const row = params.data;
  if (!row) return '';
  if (row.tracks.length === 0) {
    return `<span style="color: var(--faint);">${EVERY_TRACK}</span>`;
  }
  return row.tracks.map((code) => `<span class="chip">${escapeHtml(code)}</span>`).join(' ');
}

/** A posting's OWN eligibility gate. `GET /api/admin/jobs` answers these per
 *  row and `routers/student.py` prefers them over the programme's criteria, so
 *  a posting that carries one is stricter than the form's "Synced" fields
 *  claim. Null is the inherit case and says so — never a 0, which on a backlogs
 *  gate is the strictest value there is. */
function formatInheritedGate(params: ValueFormatterParams<JobPostingRow, number | null>): string {
  if (params.value === null || params.value === undefined) return 'Programme default';
  return `${params.value}`;
}

export const JOBS_ROW_SELECTION: RowSelectionOptions<JobPostingRow> = {
  mode: 'multiRow',
  checkboxes: true,
  headerCheckbox: true,
  // Ticking is a deliberate act: the toolbar's Duplicate and Remove act on the
  // ticked row, and Remove is not undoable, so a stray click on a cell must not
  // change what they would touch.
  enableClickSelection: false,
};

/** The tick column travels with the pinned Posting column rather than
 *  scrolling away from the row it selects. */
export const JOBS_SELECTION_COLUMN: SelectionColumnDef = { pinned: 'left', width: 44 };

export const DEFAULT_JOBS_COLUMN: ColDef<JobPostingRow> = {
  sortable: true,
  resizable: true,
  filter: true,
  floatingFilter: true,
  suppressHeaderMenuButton: false,
};

export const JOBS_COLUMNS: ColDef<JobPostingRow>[] = [
  {
    colId: 'posting',
    field: 'title',
    headerName: 'Posting',
    pinned: 'left',
    minWidth: 260,
    flex: 1.6,
    cellStyle: { display: 'flex', alignItems: 'center' },
    cellRenderer: renderPostingCell,
    getQuickFilterText: quickFilterPosting,
    headerTooltip: 'The role, with the company and location under it',
  },
  {
    colId: 'track',
    field: 'tracksLabel',
    headerName: 'Track',
    minWidth: 140,
    cellStyle: { display: 'flex', alignItems: 'center', gap: '4px' },
    cellRenderer: renderTracksCell,
    headerTooltip:
      'The specialization codes this posting is published to, matched against the student’s own batch. “Every track” means it names none, which puts it in front of everybody',
  },
  {
    colId: 'ctc',
    headerName: 'CTC',
    type: 'numericColumn',
    minWidth: 110,
    sortable: false,
    filter: false,
    floatingFilter: false,
    valueFormatter: formatNotReadable,
    headerTooltip: 'A posting records no pay today, and no backend task adds one',
  },
  {
    colId: 'deadline',
    field: 'deadlineLabel',
    headerName: 'Deadline',
    minWidth: 140,
    headerTooltip: 'The closing date the posting was published with',
  },
  {
    colId: 'applied',
    field: 'applicants',
    headerName: 'Applied',
    type: 'numericColumn',
    minWidth: 110,
    headerTooltip:
      'Students who have applied. The board draws this over a denominator — the students the posting is open to — which no endpoint counts, so the count stands alone',
  },
  {
    colId: 'status',
    field: 'statusLabel',
    headerName: 'Status',
    minWidth: 160,
    cellRenderer: renderStatusCell,
    headerTooltip:
      'Whether the posting is on the student and alumni boards. Only “Withdrawn” takes it off them — the closing date does not, so “Past deadline” means it is still listed',
  },
  {
    colId: 'source',
    field: 'sourceLabel',
    headerName: 'Source',
    minWidth: 120,
    headerTooltip:
      'Office for a posting typed here; Adzuna or Jooble for one the daily job agent found. An agent posting can be closed but not removed',
  },
  {
    colId: 'college',
    field: 'collegeLabel',
    headerName: 'College',
    minWidth: 160,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('college'),
    headerTooltip:
      'Which college sees the posting. “Every college” means it names none — the widest audience, not the narrowest',
  },
  {
    colId: 'course',
    field: 'courseLabel',
    headerName: 'Course',
    minWidth: 160,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('course'),
    headerTooltip:
      'Which programme sees the posting. “Every course” means it names none',
  },
  {
    colId: 'minCgpa',
    field: 'minCgpa',
    headerName: 'Min CGPA',
    minWidth: 150,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('minCgpa'),
    valueFormatter: formatInheritedGate,
    headerTooltip:
      "This posting's own CGPA gate. Blank means it inherits the programme's placement criteria",
  },
  {
    colId: 'maxLiveBacklogs',
    field: 'maxLiveBacklogs',
    headerName: 'Max backlogs',
    minWidth: 150,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('maxLiveBacklogs'),
    valueFormatter: formatInheritedGate,
    headerTooltip:
      "This posting's own backlog gate. Blank means it inherits the programme's placement criteria",
  },
  {
    colId: 'level',
    field: 'degreeLevel',
    headerName: 'Level',
    minWidth: 100,
    hide: INITIALLY_HIDDEN_COLUMN_IDS.includes('level'),
    cellRenderer: renderLevelCell,
    headerTooltip: 'PG or UG — which board the posting is published to',
  },
];

/** What the Columns panel lists. The selection column is the grid's own and is
 *  not on it. */
export const TOGGLEABLE_JOBS_COLUMNS = JOBS_COLUMNS.map((column) => ({
  id: column.colId ?? '',
  label: column.headerName ?? '',
}));
