/**
 * Student 360 — one student, as the placement office reads a candidate
 * (redesigned 2026-10-15 from the approved prototype,
 * docs/prototypes/student-360/index.html).
 *
 * THIS SCREEN NAMES ONE STUDENT AND MUST NEVER WIDEN TO A LIST. Everything it
 * draws is that student's private record, so every read is by id and every
 * refusal is theirs.
 *
 * THREE READS, ALL ON THE PAGE'S OWN KEY (`admin.student_records`):
 *
 *   GET /api/admin/students/{id}/360      identity, login, profile, documents,
 *                                         semesters, readiness, open items,
 *                                         mentor and the recent history
 *   GET /api/admin/students/{id}/record   built resumes, skills and growth,
 *                                         leaderboard standing, the ledger
 *                                         fortnight, the English baseline,
 *                                         interviews, six weeks of attendance
 *   GET /api/admin/students/{id}/access   who can open this record (the
 *                                         Main Admin's alone, as is the
 *                                         Access tab that draws it)
 *
 * Until this redesign the time sheet, the English baseline and the attendance
 * came from mentor and analytics reads on other keys, so the office saw "not
 * readable" where they belong. `/record` serves them from the same composers
 * those reads use, so the numbers are the same everywhere.
 *
 * WHO SEES WHAT. The Main Admin edits (`admin.students`) and gives access
 * (`admin.governance`). A faculty member the office gave "View student
 * records" for a student, batch, department or college reads this page and
 * nothing more: the edit buttons, the account actions and the access controls
 * are not drawn, and the API refuses them anyway. These are FILTERS on what is
 * drawn, never gates; the server decides.
 *
 * THE ACCESS TAB IS THE MAIN ADMIN'S (2026-10-17, the owner's decision). Who
 * else can open a student, the sign-ins and the record's history are the
 * office's business, so the tab, the rail's "Who can open this record" card
 * and the line pointing at it are drawn for the Main Admin alone, and the API
 * answers them to nobody else (`/access` is `require_admin`; `/360` sends an
 * empty sign-in list and history to anybody else).
 *
 * THE RESUME IS THE CV FROM THE APPLICATION. The owner's words: the resume the
 * office wants is the one the student uploaded when they registered. Approval
 * moved it into their uploads, and `from_application` marks it. It is shown with
 * pdf.js (`shared/pdf-preview`), never inline, because a student's PDF must not
 * run in this origin. Resumes built in REEP are listed under it.
 *
 * EVERY MISSING NUMBER IS A DASH, NEVER 0. A KPI with nothing behind it says
 * why in words ("No marks imported"), the rule the server keeps with nulls.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AuthService } from '../../../core/auth.service';
import { environment } from '../../../../environments/environment';
import { PdfPreviewComponent } from '../../../shared/pdf-preview/pdf-preview.component';
import { plural } from '../../../shared/text/plural.pipe';
import {
  type AccessHolderOut,
  type AccessOut,
  type AdminStudentOut,
  type AuditRow360Out,
  type BadgeOut,
  type BoardStandingOut,
  type CapResetOut,
  type CohortOut,
  DEFAULT_HIGHEST_SEMESTER,
  type Document360Out,
  type MentorSpellRow,
  NOT_READABLE,
  type ReachOptionOut,
  type ReadinessFactorOut,
  type ResumeVersionOut,
  STAGES,
  type Semester360Out,
  type SemesterMove360Out,
  type StaffOut,
  type Student360Out,
  type StudentRecordOut,
  barsOf,
  cvsOf,
  distinctHolders,
  formatDay,
  formatMoment,
  initialsOf,
  mainCvOf,
  meanAttendancePercentOf,
  mentorSpellRowsOf,
  moveLabelOf,
  percentOf,
  pointsOf,
  stageLabelOf,
  topPercentOf,
} from './student-record';

/** The views, in the order the office reads a candidate. */
interface TabSpec {
  key: string;
  label: string;
}

const TABS: TabSpec[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'resume', label: 'Resume' },
  { key: 'skills', label: 'Skills' },
  { key: 'leaderboard', label: 'Leaderboard' },
  { key: 'academics', label: 'Academics' },
  { key: 'interviews', label: 'Interviews' },
  { key: 'timesheet', label: 'Time sheet' },
  { key: 'documents', label: 'Documents' },
  { key: 'access', label: 'Access' },
];

/** The tab only the Main Admin is shown. */
const OFFICE_TAB = 'access';

/** A document's verdict as text AND colour, never colour alone — the words the
 *  student reads on their own Uploads screen. */
const DOCUMENT_CHIP: Record<string, { tone: string; label: string }> = {
  PENDING_REVIEW: { tone: 'warn', label: 'Pending review' },
  VERIFIED: { tone: 'good', label: 'Verified' },
  NEEDS_CHANGES: { tone: 'warn', label: 'Needs changes' },
  REJECTED: { tone: 'risk', label: 'Rejected' },
};

const DOCUMENT_KIND: Record<string, string> = {
  CERTIFICATE_PROOF: 'Certificate',
  DOCUMENT: 'Document',
  RESUME: 'CV',
  PROFILE_PHOTO: 'Photo',
};

/** The attendance a semester requires, drawn as the dashed line. */
const ATTENDANCE_REQUIRED = 75;

/** The capability a "Give access" grant names. */
const RECORDS_KEY = 'admin.student_records';

/** Governance refuses a grant or a removal with a shorter reason. */
const MIN_REASON_CHARS = 20;

/** `admin_faculty.DisableIn` refuses a reason shorter than this. */
const MIN_DISABLE_REASON_CHARS = 3;

/** How long access lasts when the office does not change the date. */
const DEFAULT_ACCESS_DAYS = 180;

function formatBytes(size: number): string {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${Math.round(size / 1024)} KB`;
  return `${size} B`;
}

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

interface ReadResult<T> {
  value: T | null;
  refusal: string | null;
}

/** `admin_faculty.AccountStateOut` — the server's own sentence about what the
 *  sign-out did is shown verbatim. */
interface AccountStateOut {
  detail: string;
}

/** One line of the Needs attention card. */
interface AttentionItem {
  who: 'staff' | 'student';
  icon: string;
  title: string;
  why: string;
  tab: string;
  action: string;
}

/** One day chip of the time sheet strip. */
interface LedgerChip {
  day: string;
  label: string;
  hours: string;
  tone: string;
}

@Component({
  selector: 'app-admin-student-detail',
  standalone: true,
  imports: [RouterLink, PdfPreviewComponent],
  templateUrl: './student-detail.component.html',
  styleUrl: './student-detail.component.scss',
})
export class AdminStudentDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly auth = inject(AuthService);
  private readonly apiBase = environment.apiBase;

  readonly studentId = this.route.snapshot.paramMap.get('id') ?? '';

  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly flash = signal<string | null>(null);

  readonly record = signal<Student360Out | null>(null);
  readonly student = signal<AdminStudentOut | null>(null);
  readonly panels = signal<StudentRecordOut | null>(null);
  readonly panelsRefusal = signal<string | null>(null);
  readonly access = signal<AccessOut | null>(null);
  readonly batches = signal<CohortOut[]>([]);
  readonly batchesRefusal = signal<string | null>(null);

  /** Every tab for the Main Admin; the record without Access for anybody else. */
  readonly tabs = computed(() =>
    this.isMainAdmin() ? TABS : TABS.filter((spec) => spec.key !== OFFICE_TAB),
  );
  readonly tab = signal('overview');
  readonly notReadable = NOT_READABLE;
  readonly stages = STAGES;

  constructor() {
    const fromUrl = this.route.snapshot.fragment ?? '';
    if (this.tabs().some((spec) => spec.key === fromUrl)) {
      this.tab.set(fromUrl);
    }
    void this.loadEverything();
  }

  setTab(key: string): void {
    this.menuOpen.set(false);
    if (!this.tabs().some((spec) => spec.key === key)) return;
    this.tab.set(key);
  }

  /** Arrow keys move along the tab row, as a tablist should. */
  onTabKey(event: KeyboardEvent, index: number): void {
    const tabs = this.tabs();
    const last = tabs.length - 1;
    let next: number | null = null;
    if (event.key === 'ArrowRight') next = index === last ? 0 : index + 1;
    if (event.key === 'ArrowLeft') next = index === 0 ? last : index - 1;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = last;
    if (next === null) return;
    event.preventDefault();
    this.setTab(tabs[next].key);
    const target = document.getElementById(`tab-${tabs[next].key}`);
    target?.focus();
  }

  // --- who is looking -------------------------------------------------------

  private readonly capabilities = computed(() => this.auth.session()?.capabilities ?? []);

  readonly isMainAdmin = computed(() => this.auth.session()?.role === 'ADMIN');
  /** The roster editor: Edit profile and Move batch. */
  readonly canEdit = computed(() => this.capabilities().includes('admin.students'));
  /** A faculty member reading on a grant: the page says so, and draws no action. */
  readonly readOnly = computed(() => !this.isMainAdmin() && !this.canEdit());
  readonly canManageAccess = computed(() => this.access()?.can_manage === true);

  // --- the candidate ---------------------------------------------------------

  readonly studentName = computed(() => this.student()?.name ?? 'Student');
  readonly initials = computed(() => initialsOf(this.studentName()));
  readonly usnLabel = computed(() => this.student()?.usn ?? NOT_READABLE);
  readonly batchLabel = computed(() => this.student()?.batch ?? 'No batch yet');
  readonly departmentLabel = computed(() => this.student()?.department ?? 'No department yet');
  readonly mentorName = computed(() => this.student()?.mentor_name ?? null);
  readonly mentorInitials = computed(() => initialsOf(this.mentorName() ?? '?'));

  /** "3 students", for the template. */
  readonly plural = plural;
  readonly stageLabel = computed(() => {
    const row = this.student();
    return row === null ? NOT_READABLE : stageLabelOf(row.current_stage);
  });

  readonly hasSignedIn = computed(() => {
    const row = this.student();
    return row !== null && row.last_login_at !== null;
  });
  readonly isDisabled = computed(() => this.record()?.login.disabled === true);
  readonly isRemoved = computed(() => (this.record()?.login.deleted_at ?? null) !== null);
  readonly accountStatusLabel = computed(() => {
    if (this.isRemoved()) return 'Removed';
    if (this.isDisabled()) return 'Disabled';
    return this.hasSignedIn() ? 'Active' : 'Not signed in yet';
  });
  readonly accountStatusTone = computed(() => {
    if (this.isRemoved() || this.isDisabled()) return 'risk';
    return this.hasSignedIn() ? 'good' : 'warn';
  });
  readonly accountLine = computed(() => {
    const login = this.record()?.login;
    if (login === undefined) return null;
    // The reasons were written for the office, and the API sends them to it
    // alone; anybody else reads the fact and the date.
    if (!this.isMainAdmin()) {
      if (login.deleted_at !== null) return `Removed from the roster ${formatMoment(login.deleted_at)}.`;
      if (login.disabled) return `Disabled ${formatMoment(login.disabled_at)}.`;
      return null;
    }
    if (login.deleted_at !== null) {
      return `Removed from the roster ${formatMoment(login.deleted_at)}: ${login.delete_reason ?? 'no reason recorded'}.`;
    }
    if (login.disabled) {
      const emailed =
        login.disabled_notice_sent_at === null
          ? ' They have not been emailed about it.'
          : ` Emailed to them ${formatMoment(login.disabled_notice_sent_at)}.`;
      return `Disabled ${formatMoment(login.disabled_at)} by ${login.disabled_by_name ?? 'a removed account'}: ${login.disable_reason ?? 'no reason recorded'}.${emailed}`;
    }
    return null;
  });

  readonly semesterLabel = computed(() => {
    const row = this.student();
    if (row === null) return NOT_READABLE;
    const total = this.record()?.total_semesters ?? null;
    return total === null ? `${row.current_semester}` : `${row.current_semester} of ${total}`;
  });

  readonly lastSignInLabel = computed(() => {
    const row = this.student();
    if (row === null || row.last_login_at === null) return 'Not yet';
    return formatMoment(row.last_login_at);
  });

  readonly profile = computed(() => this.record()?.profile ?? null);

  readonly placementLine = computed(() => {
    const profile = this.profile();
    if (profile === null || !profile.on_record) return null;
    if (profile.placement_eligible === false) return 'Not eligible for placement';
    if (profile.interested_in_jobs) return 'Wants placement';
    if (profile.interested_in_internships) return 'Wants an internship';
    return null;
  });

  /** The contact rows, with a value to copy. A blank is said, not dashed. */
  readonly contactRows = computed(() => {
    const profile = this.profile();
    const row = this.student();
    const rows: { label: string; value: string | null; href: string | null }[] = [
      { label: 'College email', value: row?.email ?? null, href: null },
    ];
    if (profile !== null && profile.on_record) {
      rows.unshift({ label: 'Phone', value: profile.phone, href: null });
      rows.push({ label: 'Contact email', value: profile.contact_email, href: null });
      rows.push({ label: 'LinkedIn', value: profile.linkedin_url, href: profile.linkedin_url });
      rows.push({ label: 'City', value: profile.city, href: null });
    }
    return rows;
  });

  /** A copy that asks nothing of the reader: the value goes to the clipboard
   *  and the page says so. Where the clipboard is refused, it says that. */
  async copy(value: string | null, label: string): Promise<void> {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      this.flash.set(`${label} copied.`);
    } catch {
      this.flash.set(`${label}: ${value}`);
    }
  }

  // --- the CV (the resume the office reads) ------------------------------------

  readonly documents = computed<Document360Out[]>(() => this.record()?.documents ?? []);
  readonly cvs = computed(() => cvsOf(this.documents()));
  readonly mainCv = computed(() => mainCvOf(this.documents()));
  readonly selectedCvId = signal<string | null>(null);
  readonly shownCv = computed(() => {
    const id = this.selectedCvId();
    return this.cvs().find((cv) => cv.id === id) ?? this.mainCv();
  });

  selectCv(id: string): void {
    this.selectedCvId.set(id);
  }

  fileUrl(documentId: string): string {
    return `${this.apiBase}/admin/students/${this.studentId}/files/${documentId}`;
  }

  /** The download that is recorded on the student's history. */
  downloadUrl(documentId: string): string {
    return `${this.fileUrl(documentId)}?download=true`;
  }

  cvSource(cv: Document360Out): string {
    return cv.from_application ? 'Uploaded with the application' : 'Uploaded later';
  }

  readonly builtResumes = computed<ResumeVersionOut[]>(() => this.panels()?.resumes ?? []);

  builtResumeUrl(resume: ResumeVersionOut): string {
    return `${this.apiBase}/admin/students/${this.studentId}/resumes/${resume.id}/pdf?download=true`;
  }

  resumeStatusLabel(status: string): string {
    if (status === 'FINALISED') return 'Finalised';
    if (status === 'GENERATED') return 'Generated';
    return 'Draft';
  }

  /** A download leaves through the browser; the history is re-read a moment
   *  later so the row it wrote is on the Access tab. */
  noteDownload(): void {
    setTimeout(() => void this.load360(), 1500);
  }

  // --- the KPI strip -----------------------------------------------------------

  readonly readiness = computed(() => this.record()?.readiness ?? null);

  readonly readinessTone = computed(() => {
    const score = this.readiness()?.score ?? null;
    if (score === null) return 'neutral';
    if (score < 40) return 'risk';
    if (score < 70) return 'warn';
    return 'good';
  });

  readonly readinessFactors = computed<ReadinessFactorOut[]>(() => this.readiness()?.factors ?? []);

  factorChip(factor: ReadinessFactorOut): { tone: string; label: string } {
    if (!factor.measured) return { tone: 'neutral', label: 'Not measured' };
    return factor.met ? { tone: 'good', label: 'Met' } : { tone: 'warn', label: 'Not met' };
  }

  /** The latest semester with a CGPA, and its backlogs. */
  readonly latestResult = computed(() => {
    const withCgpa = (this.record()?.semesters ?? []).filter(
      (semester) => semester.results !== null && semester.results.cgpa !== null,
    );
    return withCgpa.length === 0 ? null : withCgpa[withCgpa.length - 1];
  });

  readonly cgpa = computed(() => this.latestResult()?.results?.cgpa ?? null);

  readonly liveBacklogs = computed(() => this.latestResult()?.results?.live_backlogs ?? null);

  readonly attendancePercent = computed(() => meanAttendancePercentOf(this.panels()?.weekly ?? null));

  readonly attendanceSpark = computed(() => {
    const weekly = this.panels()?.weekly;
    if (!weekly) return [];
    return barsOf(weekly.attendance_percent, weekly.weeks.map((w) => w.label), 120, 28, ATTENDANCE_REQUIRED);
  });

  readonly overallBoard = computed<BoardStandingOut | null>(
    () => this.panels()?.leaderboards.boards.find((board) => board.board === 'overall') ?? null,
  );

  readonly skills = computed(() => this.panels()?.skills ?? null);

  readonly pendingClaims = computed(() => this.record()?.open_items.pending_badge_claims ?? 0);

  readonly interviews = computed(() =>
    [...(this.panels()?.interviews ?? [])].sort(
      (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
    ),
  );

  /** Scored interviews, oldest first, for the line. */
  readonly scoredInterviews = computed(() =>
    [...this.interviews()].reverse().filter((sitting) => sitting.overall_score !== null),
  );

  readonly bestInterview = computed(() => {
    const scores = this.scoredInterviews().map((sitting) => sitting.overall_score as number);
    return scores.length === 0 ? null : Math.max(...scores);
  });

  readonly interviewSpark = computed(() =>
    pointsOf(
      this.scoredInterviews().map((sitting) => sitting.overall_score as number),
      116,
      24,
      0,
      100,
    ),
  );

  sparkLine(points: { x: number; y: number }[]): string {
    return points.map((point) => `${point.x + 2},${point.y + 2}`).join(' ');
  }

  // --- needs attention ---------------------------------------------------------

  readonly attention = computed<AttentionItem[]>(() => {
    const open = this.record()?.open_items;
    if (!open) return [];
    const items: AttentionItem[] = [];
    if (open.pending_badge_claims > 0) {
      items.push({
        who: 'staff',
        icon: 'pending_actions',
        title: `${plural(open.pending_badge_claims, 'skill claim')} waiting for a decision`,
        why: this.claimNames('VERIFICATION_PENDING'),
        tab: 'skills',
        action: 'Review',
      });
    }
    if (open.pending_uploads > 0) {
      items.push({
        who: 'staff',
        icon: 'pending_actions',
        title: `${plural(open.pending_uploads, 'document')} waiting for review`,
        why: 'On the Documents tab',
        tab: 'documents',
        action: 'Open',
      });
    }
    const sentBack =
      open.badge_claims_needing_info + this.documents().filter((d) => d.status === 'NEEDS_CHANGES').length;
    if (sentBack > 0) {
      items.push({
        who: 'student',
        icon: 'undo',
        title: `${plural(sentBack, 'item')} sent back for changes`,
        why: 'Claims and documents the student has to fix',
        tab: open.badge_claims_needing_info > 0 ? 'skills' : 'documents',
        action: 'See them',
      });
    }
    if (open.unsubmitted_ledger_days > 0) {
      items.push({
        who: 'student',
        icon: 'schedule',
        title: `${plural(open.unsubmitted_ledger_days, 'time sheet day')} started and not submitted`,
        why: 'On the Time sheet tab',
        tab: 'timesheet',
        action: 'See days',
      });
    }
    if (open.missing_profile_fields.length > 0) {
      items.push({
        who: 'student',
        icon: 'person',
        title: 'Profile is missing details',
        why: open.missing_profile_fields.join(', '),
        tab: 'overview',
        action: 'See profile',
      });
    }
    return items;
  });

  private claimNames(status: string): string {
    const names: string[] = [];
    for (const category of this.skills()?.categories ?? []) {
      for (const badge of category.badges) {
        if (badge.status === status) names.push(badge.name);
      }
    }
    return names.slice(0, 3).join(' · ');
  }

  // --- skills ------------------------------------------------------------------

  readonly earnedBadges = computed<BadgeOut[]>(() => {
    const out: BadgeOut[] = [];
    for (const category of this.skills()?.categories ?? []) {
      for (const badge of category.badges) if (badge.status === 'EARNED') out.push(badge);
    }
    return out.sort((a, b) => (b.earned_at ?? '').localeCompare(a.earned_at ?? ''));
  });

  /** Claims nobody has decided, and claims sent back to the student. */
  readonly openClaims = computed(() => {
    const out: { badge: BadgeOut; state: 'pending' | 'back'; filed: string; note: string | null }[] = [];
    for (const category of this.skills()?.categories ?? []) {
      for (const badge of category.badges) {
        for (const evidence of badge.evidence) {
          if (evidence.status === 'PENDING_VERIFICATION') {
            out.push({ badge, state: 'pending', filed: evidence.created_at, note: null });
          } else if (evidence.status === 'MORE_INFO_REQUIRED') {
            out.push({ badge, state: 'back', filed: evidence.created_at, note: evidence.review_note });
          }
        }
      }
    }
    return out;
  });

  categoryPercent(earned: number, total: number): number {
    return percentOf(earned, total);
  }

  readonly growth = computed(() => this.panels()?.growth ?? null);

  scoreOf(scores: Record<string, number | null>, checkpoint: string): string {
    const value = scores[checkpoint];
    return value === null || value === undefined ? NOT_READABLE : `${value}`;
  }

  growthLabel(growth: number | null): string {
    if (growth === null) return NOT_READABLE;
    return growth > 0 ? `+${growth}` : `${growth}`;
  }

  // --- leaderboard -------------------------------------------------------------

  readonly leaderboards = computed(() => this.panels()?.leaderboards ?? null);
  readonly boardKey = signal('overall');
  readonly shownBoard = computed(
    () => this.leaderboards()?.boards.find((board) => board.board === this.boardKey()) ?? null,
  );

  topPercent(board: BoardStandingOut): number | null {
    return board.rank === null ? null : topPercentOf(board.rank, board.ranked);
  }

  scopeLine(): string {
    const boards = this.leaderboards();
    if (!boards) return '';
    if (boards.scope === 'none') return 'Not seated in a batch or a department, so there is nobody to rank against.';
    const where = boards.scope === 'batch' ? 'batch' : 'department';
    return `Ranked within the ${where} ${boards.scope_label ?? ''} · ${plural(boards.classmates, 'student')}`;
  }

  // --- academics ---------------------------------------------------------------

  readonly resultRows = computed<Semester360Out[]>(() =>
    (this.record()?.semesters ?? []).filter((semester) => semester.results !== null),
  );

  readonly attendanceChart = computed(() => {
    const weekly = this.panels()?.weekly;
    if (!weekly) return [];
    return barsOf(weekly.attendance_percent, weekly.weeks.map((w) => w.label), 460, 160, ATTENDANCE_REQUIRED);
  });

  readonly attendanceRequired = ATTENDANCE_REQUIRED;
  /** The y of the required line in the 160-high plot. */
  readonly requiredY = 160 - (ATTENDANCE_REQUIRED / 100) * 160;

  readonly english = computed(() => this.panels()?.english ?? null);

  readonly englishLine = computed(() => {
    const attempt = this.english();
    if (attempt === null) return null;
    if (!attempt.exists) return 'Not taken yet';
    if (attempt.band_label) return attempt.band_label;
    return `${attempt.sections_scored} of ${plural(attempt.sections_total, 'section')} scored`;
  });

  // --- interviews --------------------------------------------------------------

  readonly interviewChart = computed(() =>
    pointsOf(
      this.scoredInterviews().map((sitting) => sitting.overall_score as number),
      440,
      150,
      0,
      100,
    ),
  );

  interviewLine(points: { x: number; y: number }[]): string {
    return points.map((point) => `${point.x + 40},${point.y + 10}`).join(' ');
  }

  scoredDates(): string[] {
    return this.scoredInterviews().map((sitting) => formatDay(sitting.started_at));
  }

  interviewStatus(status: string): { tone: string; label: string } {
    if (status === 'completed') return { tone: 'good', label: 'Completed' };
    if (status === 'running') return { tone: 'neutral', label: 'Running' };
    return { tone: 'warn', label: 'Ended early' };
  }

  // --- time sheet --------------------------------------------------------------

  readonly ledger = computed(() => this.panels()?.ledger ?? null);

  /** The last fortnight as day chips, oldest first. A day with no row is
   *  "Not logged", which is a fact about the student, not a failed read. */
  readonly ledgerChips = computed<LedgerChip[]>(() => {
    const summary = this.ledger();
    if (summary === null) return [];
    const byDay = new Map(summary.days.map((day) => [day.day, day]));
    const chips: LedgerChip[] = [];
    const today = new Date();
    for (let back = summary.window_days - 1; back >= 0; back -= 1) {
      const date = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate() - back));
      const key = isoDay(date);
      const row = byDay.get(key);
      const label = date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
      if (!row) {
        chips.push({ day: label, label: 'Not logged', hours: '', tone: 'none' });
      } else if (row.status === 'SUBMITTED') {
        chips.push({ day: label, label: 'Submitted', hours: `${row.logged_hours} h`, tone: 'sub' });
      } else {
        chips.push({ day: label, label: 'Draft', hours: `${row.logged_hours} h`, tone: 'draft' });
      }
    }
    return chips;
  });

  readonly skillingWeek = computed(() => {
    const weekly = this.panels()?.weekly;
    if (!weekly || weekly.logged_hours.length === 0) return null;
    return {
      hours: weekly.logged_hours[weekly.logged_hours.length - 1],
      target: weekly.weekly_hour_target,
    };
  });

  // --- overview ----------------------------------------------------------------

  readonly semesterSteps = computed(() => {
    const row = this.student();
    if (row === null) return [];
    const total = this.record()?.total_semesters ?? Math.max(row.current_semester, 1);
    const byNumber = new Map((this.record()?.semesters ?? []).map((s) => [s.semester, s]));
    return Array.from({ length: Math.max(total, row.current_semester) }, (_unused, index) => {
      const number = index + 1;
      const semester = byNumber.get(number) ?? null;
      return {
        number,
        state: number < row.current_semester ? 'done' : number === row.current_semester ? 'now' : 'next',
        when: this.windowOf(semester),
        what: this.semesterWhat(semester, number, row.current_semester),
      };
    });
  });

  private windowOf(semester: Semester360Out | null): string {
    if (semester === null || semester.started_on === null) return '';
    if (semester.ended_on === null) return `since ${formatDay(semester.started_on)}`;
    return `${formatDay(semester.started_on)} – ${formatDay(semester.ended_on)}`;
  }

  private semesterWhat(semester: Semester360Out | null, number: number, current: number): string {
    if (number > current) return 'Starts when the office promotes the batch';
    const parts: string[] = [];
    const results = semester?.results ?? null;
    if (results === null) {
      parts.push('Marks not imported yet');
    } else {
      if (results.sgpa !== null) parts.push(`SGPA ${results.sgpa.toFixed(2)}`);
      if (results.cgpa !== null) parts.push(`CGPA ${results.cgpa.toFixed(2)}`);
    }
    if (semester !== null && semester.activity_known) {
      parts.push(plural(semester.badges_earned ?? 0, 'badge'));
      parts.push(plural(semester.interviews ?? 0, 'interview'));
    }
    return parts.join(' · ');
  }

  readonly mentorSince = computed(() => {
    const spell = this.record()?.mentor_history.entries.find((entry) => entry.to_at === null);
    if (!spell || spell.from_at === null) return null;
    return formatDay(spell.from_at);
  });

  /** Every faculty mentor the student has had, newest first, with who made
   *  each change and why. */
  readonly mentorSpells = computed<MentorSpellRow[]>(() => mentorSpellRowsOf(this.record()?.mentor_history));

  /** The server's own sentence when there is no history to list. */
  readonly mentorHistoryNote = computed(() => this.record()?.mentor_history.note ?? '');

  /** Every promotion, graduation and reversal recorded for this student. */
  readonly semesterMoves = computed<SemesterMove360Out[]>(() => this.record()?.semester_history ?? []);

  moveLabel(kind: string): string {
    return moveLabelOf(kind);
  }

  readonly auditRows = computed<AuditRow360Out[]>(() => this.record()?.recent_audit ?? []);

  auditLine(row: AuditRow360Out): string {
    const who = row.actor_name ?? 'Somebody';
    const action = row.action.toLowerCase().replace(/_/g, ' ');
    return `${who} · ${action}`;
  }

  // --- documents ----------------------------------------------------------------

  documentChip(status: string): { tone: string; label: string } {
    return DOCUMENT_CHIP[status] ?? { tone: 'neutral', label: status };
  }

  documentKind(kind: string): string {
    return DOCUMENT_KIND[kind] ?? 'File';
  }

  documentSize(size: number): string {
    return formatBytes(size);
  }

  formatDay(iso: string | null): string {
    return formatDay(iso);
  }

  formatMoment(iso: string | null): string {
    return formatMoment(iso);
  }

  // --- access -------------------------------------------------------------------

  readonly holders = computed<AccessHolderOut[]>(() => this.access()?.holders ?? []);

  holderInitials(holder: AccessHolderOut): string {
    return initialsOf(holder.name);
  }

  /** The rail's avatars and count: each person once. */
  readonly people = computed(() => distinctHolders(this.holders()));

  /** People let in by a grant who are not already the office or the mentor. */
  readonly grantedCount = computed(() => {
    const listedOtherwise = new Set(this.holders().filter((h) => h.kind !== 'grant').map((h) => h.holder_id));
    return distinctHolders(this.holders().filter((h) => h.kind === 'grant' && !listedOtherwise.has(h.holder_id)))
      .length;
  });

  readonly signIns = computed(() => this.record()?.login.recent_sign_ins ?? []);

  readonly doorsLine = computed(() => {
    const login = this.record()?.login;
    if (login === undefined) return NOT_READABLE;
    if (login.password_set && login.google_linked) return 'Password and Google';
    if (login.google_linked) return 'Google only';
    if (login.password_set) return 'Password only';
    return 'Not set up yet';
  });

  // Give access ------------------------------------------------------------------

  readonly giveOpen = signal(false);
  readonly staff = signal<StaffOut[]>([]);
  readonly giveTo = signal('');
  readonly giveReach = signal('');
  readonly giveUntil = signal('');
  readonly giveReason = signal('');
  readonly minReason = MIN_REASON_CHARS;

  readonly reachOptions = computed<ReachOptionOut[]>(() => this.access()?.reach_options ?? []);

  /** Faculty only, and not the mentor or a former mentor, who already read this record. */
  readonly grantableStaff = computed(() => {
    const already = new Set(this.holders().filter((h) => h.kind !== 'grant').map((h) => h.holder_id));
    return this.staff().filter((person) => person.role === 'MENTOR' && !already.has(person.user_id));
  });

  readonly canGive = computed(
    () =>
      !this.busy() &&
      this.giveTo() !== '' &&
      this.giveReach() !== '' &&
      this.giveUntil() !== '' &&
      this.giveReason().trim().length >= MIN_REASON_CHARS,
  );

  readonly todayIso = isoDay(new Date());

  async openGive(): Promise<void> {
    this.error.set(null);
    this.flash.set(null);
    const until = new Date();
    until.setDate(until.getDate() + DEFAULT_ACCESS_DAYS);
    this.giveTo.set('');
    this.giveReason.set('');
    this.giveUntil.set(isoDay(until));
    const department = this.reachOptions().find((option) => option.scope_level === 'DEPARTMENT');
    this.giveReach.set(department ? this.reachKey(department) : '');
    this.giveOpen.set(true);
    if (this.staff().length === 0) {
      const staff = await this.read<StaffOut[]>('/admin/governance/staff');
      this.staff.set(staff.value ?? []);
      if (staff.refusal) this.error.set(staff.refusal);
    }
  }

  closeGive(): void {
    this.giveOpen.set(false);
  }

  reachKey(option: ReachOptionOut): string {
    return `${option.scope_level}:${option.scope_id}`;
  }

  async give(): Promise<void> {
    if (!this.canGive()) return;
    const [scopeLevel, scopeId] = this.giveReach().split(':');
    const person = this.staff().find((p) => p.user_id === this.giveTo());
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/governance/grants`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          capability: RECORDS_KEY,
          user_ids: [this.giveTo()],
          reason: this.giveReason().trim(),
          // The end of the chosen day, in the office's own time zone.
          expires_at: new Date(`${this.giveUntil()}T23:59:59`).toISOString(),
          scope_level: scopeLevel,
          scope_id: scopeId,
        }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      this.giveOpen.set(false);
      const reach = this.reachOptions().find((option) => this.reachKey(option) === this.giveReach());
      const whom =
        reach?.scope_level === 'STUDENT' ? `${this.studentName()}'s record` : `records in ${reach?.label ?? 'that group'}`;
      this.flash.set(`${person?.name ?? 'They'} can now open ${whom} until ${formatDay(this.giveUntil())}.`);
      await this.loadAccess();
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  // Remove access ----------------------------------------------------------------

  readonly removing = signal<string | null>(null);
  readonly removeReason = signal('');

  askRemove(grantId: string): void {
    this.removeReason.set('');
    this.removing.set(grantId);
  }

  cancelRemove(): void {
    this.removing.set(null);
  }

  readonly canRemove = computed(
    () => !this.busy() && this.removeReason().trim().length >= MIN_REASON_CHARS,
  );

  async remove(holder: AccessHolderOut): Promise<void> {
    if (!holder.grant_id || !this.canRemove()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/governance/grants/${holder.grant_id}/revoke`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: this.removeReason().trim() }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      this.removing.set(null);
      this.flash.set(`${holder.name} can no longer open these records.`);
      await this.loadAccess();
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  // --- the More menu, and the account actions it holds --------------------------

  readonly menuOpen = signal(false);

  toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  // Disable and enable (the Main Admin's; `POST /admin/users/{id}/disable` and
  // `/enable` are `require_admin`). The ACCOUNT's id, as for the sign-out.

  /** `admin_faculty.DisableIn` refuses a shorter reason. */
  readonly minDisableReason = MIN_DISABLE_REASON_CHARS;
  readonly disableOpen = signal(false);
  readonly disableReason = signal('');
  readonly canDisable = computed(
    () => !this.busy() && this.disableReason().trim().length >= MIN_DISABLE_REASON_CHARS,
  );
  readonly enableOpen = signal(false);

  askDisable(): void {
    this.menuOpen.set(false);
    this.error.set(null);
    this.flash.set(null);
    this.disableReason.set('');
    this.disableOpen.set(true);
  }

  cancelDisable(): void {
    this.disableOpen.set(false);
  }

  askEnable(): void {
    this.menuOpen.set(false);
    this.error.set(null);
    this.flash.set(null);
    this.enableOpen.set(true);
  }

  cancelEnable(): void {
    this.enableOpen.set(false);
  }

  // The disabled notice (`POST /admin/users/{id}/disabled-notice`, the Main
  // Admin's): the email that tells a disabled student so. Disabling sends it;
  // this is for a student disabled before that, or one who says it never came.
  readonly noticeOpen = signal(false);
  readonly noticeSentLabel = computed(() => {
    const sentAt = this.record()?.login.disabled_notice_sent_at ?? null;
    return sentAt === null ? null : formatMoment(sentAt);
  });

  askNotice(): void {
    this.menuOpen.set(false);
    this.error.set(null);
    this.flash.set(null);
    this.noticeOpen.set(true);
  }

  cancelNotice(): void {
    this.noticeOpen.set(false);
  }

  async sendNotice(): Promise<void> {
    if (this.busy()) return;
    const done = await this.accountAction('disabled-notice', null);
    if (done) this.noticeOpen.set(false);
  }

  async disableAccount(): Promise<void> {
    if (!this.canDisable()) return;
    const done = await this.accountAction('disable', { reason: this.disableReason().trim() });
    if (done) this.disableOpen.set(false);
  }

  async enableAccount(): Promise<void> {
    if (this.busy()) return;
    const done = await this.accountAction('enable', null);
    if (done) this.enableOpen.set(false);
  }

  /** One of the account posts; the server's own sentence is shown. */
  private async accountAction(
    verb: 'disable' | 'enable' | 'disabled-notice',
    body: object | null,
  ): Promise<boolean> {
    const row = this.student();
    if (row === null) return false;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/users/${row.user_id}/${verb}`, {
        method: 'POST',
        credentials: 'include',
        ...(body === null
          ? {}
          : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return false;
      }
      const state = (await response.json()) as AccountStateOut;
      this.flash.set(state.detail);
      await this.load360();
      return true;
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
      return false;
    } finally {
      this.busy.set(false);
    }
  }

  readonly confirmingSignOut = signal(false);

  askSignOutEverywhere(): void {
    this.menuOpen.set(false);
    this.error.set(null);
    this.flash.set(null);
    this.confirmingSignOut.set(true);
  }

  cancelSignOutEverywhere(): void {
    this.confirmingSignOut.set(false);
  }

  /** The ACCOUNT's id, not the student's: a session hangs off `users`. */
  async signOutEverywhere(): Promise<void> {
    const row = this.student();
    if (row === null || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/users/${row.user_id}/sign-out-everywhere`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const state = (await response.json()) as AccountStateOut;
      this.confirmingSignOut.set(false);
      this.flash.set(state.detail);
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  readonly capResetOpen = signal(false);
  readonly capReason = signal('');
  readonly canResetCap = computed(() => !this.busy() && this.capReason().trim() !== '');

  askCapReset(): void {
    this.menuOpen.set(false);
    this.error.set(null);
    this.flash.set(null);
    this.capReason.set('');
    this.capResetOpen.set(true);
  }

  cancelCapReset(): void {
    this.capResetOpen.set(false);
  }

  /** B6.4: the reason is required and goes on the history. It does not zero a
   *  counter; it moves the start of the 24-hour window forward. */
  async resetCap(): Promise<void> {
    const reason = this.capReason().trim();
    if (this.busy() || reason === '') return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/students/${this.studentId}/interview-cap/reset`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      const reset = (await response.json()) as CapResetOut;
      this.capResetOpen.set(false);
      this.flash.set(
        `${this.studentName()} can practise again. ${reset.usage.completed} of ${reset.usage.daily_cap} interviews now count today.`,
      );
      await this.load360();
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  // --- the editor (Edit profile, Move batch) ------------------------------------

  readonly editorOpen = signal(false);
  readonly editorIntent = signal<'profile' | 'batch'>('profile');
  readonly formName = signal('');
  readonly formEmail = signal('');
  readonly formUsn = signal('');
  readonly formBatchId = signal('');
  readonly formStage = signal('');
  readonly formSemester = signal(1);

  readonly editorTitle = computed(() => (this.editorIntent() === 'batch' ? 'Move batch' : 'Edit profile'));

  readonly semesters = computed<number[]>(() => {
    const declared = this.record()?.total_semesters ?? null;
    const current = this.student()?.current_semester ?? 1;
    const highest = Math.max(declared ?? DEFAULT_HIGHEST_SEMESTER, current);
    return Array.from({ length: highest }, (_unused, index) => index + 1);
  });

  readonly canSaveProfile = computed(
    () => !this.busy() && this.formName().trim() !== '' && this.formEmail().trim() !== '',
  );

  async openEditor(intent: 'profile' | 'batch'): Promise<void> {
    const row = this.student();
    if (row === null) return;
    this.formName.set(row.name);
    this.formEmail.set(row.email);
    this.formUsn.set(row.usn ?? '');
    this.formBatchId.set(row.cohort_id ?? '');
    this.formStage.set(row.current_stage);
    this.formSemester.set(row.current_semester);
    this.editorIntent.set(intent);
    this.editorOpen.set(true);
    if (this.batches().length === 0) {
      await this.loadBatches();
    }
  }

  closeEditor(): void {
    this.editorOpen.set(false);
  }

  onSemester(value: string): void {
    const semester = Number(value);
    if (!Number.isNaN(semester)) this.formSemester.set(semester);
  }

  async saveProfile(): Promise<void> {
    const row = this.student();
    if (row === null || this.busy()) return;
    const changes = this.changedFields(row);
    if (Object.keys(changes).length === 0) {
      this.flash.set('Nothing changed.');
      this.editorOpen.set(false);
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.flash.set(null);
    try {
      const response = await fetch(`${this.apiBase}/admin/students/${this.studentId}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      });
      if (!response.ok) {
        this.error.set(await this.detailOf(response));
        return;
      }
      this.student.set((await response.json()) as AdminStudentOut);
      this.editorOpen.set(false);
      this.flash.set('Saved.');
      await Promise.all([this.load360(), this.loadPanels(), this.loadAccess()]);
    } catch {
      this.error.set('The server could not be reached. Nothing was changed.');
    } finally {
      this.busy.set(false);
    }
  }

  /** Only what changed. `cohort_id: null` is the explicit un-seat the API reads
   *  through `model_fields_set`, so an empty batch travels as a null. */
  private changedFields(row: AdminStudentOut): Record<string, unknown> {
    const changes: Record<string, unknown> = {};
    const name = this.formName().trim();
    if (name !== row.name) changes['name'] = name;
    const email = this.formEmail().trim().toLowerCase();
    if (email !== row.email) changes['email'] = email;
    const usn = this.formUsn().trim().toUpperCase();
    if (usn !== (row.usn ?? '')) changes['usn'] = usn === '' ? null : usn;
    const batchId = this.formBatchId();
    if (batchId !== (row.cohort_id ?? '')) changes['cohort_id'] = batchId === '' ? null : batchId;
    if (this.formStage() !== row.current_stage) changes['current_stage'] = this.formStage();
    if (this.formSemester() !== row.current_semester) changes['current_semester'] = this.formSemester();
    return changes;
  }

  // --- reading ------------------------------------------------------------------

  private async loadEverything(): Promise<void> {
    if (this.studentId === '') {
      this.error.set('No student was named in the address.');
      this.loading.set(false);
      return;
    }
    this.loading.set(true);
    await this.load360();
    if (this.student() !== null) {
      await Promise.all([this.loadPanels(), this.loadAccess()]);
    }
    this.loading.set(false);
  }

  /** A refusal here stops the page: both fences run inside this endpoint, so a
   *  403 or 404 means this account may not read this student at all. */
  private async load360(): Promise<void> {
    const record = await this.read<Student360Out>(`/admin/students/${this.studentId}/360`);
    if (record.value === null) {
      this.error.set(record.refusal ?? 'This student could not be read.');
      return;
    }
    this.record.set(record.value);
    this.student.set(record.value.identity);
  }

  private async loadPanels(): Promise<void> {
    const panels = await this.read<StudentRecordOut>(`/admin/students/${this.studentId}/record`);
    this.panels.set(panels.value);
    this.panelsRefusal.set(panels.refusal);
  }

  /** The Main Admin's alone; the API refuses it to anybody else. */
  private async loadAccess(): Promise<void> {
    if (!this.isMainAdmin()) return;
    const access = await this.read<AccessOut>(`/admin/students/${this.studentId}/access`);
    this.access.set(access.value);
  }

  private async loadBatches(): Promise<void> {
    const cohorts = await this.read<CohortOut[]>('/admin/cohorts');
    this.batches.set(cohorts.value ?? []);
    this.batchesRefusal.set(cohorts.refusal);
  }

  private async read<T>(path: string): Promise<ReadResult<T>> {
    try {
      const response = await fetch(`${this.apiBase}${path}`, { credentials: 'include' });
      if (!response.ok) {
        return { value: null, refusal: await this.detailOf(response) };
      }
      return { value: (await response.json()) as T, refusal: null };
    } catch {
      return { value: null, refusal: 'The server could not be reached.' };
    }
  }

  /** FastAPI answers a schema error with `detail` as a LIST; the server's own
   *  sentence is kept wherever there is one. */
  private async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') return body.detail;
      if (Array.isArray(body.detail)) {
        const messages = body.detail
          .map((entry) => (entry as { msg?: string }).msg)
          .filter((msg): msg is string => typeof msg === 'string');
        if (messages.length > 0) return messages.join(' ');
      }
    } catch {
      /* not JSON */
    }
    return `The request was refused (${response.status}).`;
  }
}
