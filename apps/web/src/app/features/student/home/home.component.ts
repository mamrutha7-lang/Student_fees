/**
 * The student landing screen — the handoff's Landing panel in full.
 *
 * Top to bottom: the greeting and login-streak chip; the three programme stage
 * cards (Reboot · Excel · Elevate) with the status legend; attendance by course
 * beside the VTU marks line chart; the Academic History section (10th / 12th /
 * UG cards and the declared education gaps); and placement readiness beside the
 * recommendations. Every card carries its own empty state, exactly as the design
 * writes them, so a fresh student sees the same page shape as a senior one.
 *
 * THE STAT STRIP IS GONE (2026-09-17). The stage donut, the skill-badge row, the
 * "Mocks taken" bar chart and the "Login streak" card were removed from the
 * landing at the owner's request. The overview payload still carries `mocks`
 * and `skills` — the leaderboards and Skilling screens read the same records
 * through their own endpoints — but nothing on this screen reads them, so the
 * interface below no longer declares them. `streak` stays: the header chip
 * still reads it.
 *
 * TWO READS, NOT TWELVE. `GET /student/overview` is the aggregate the API
 * composes in one DB session for exactly this screen (dashboard, attendance,
 * results, streak, readiness, recommendations — and now the academic
 * history); `GET /student/programme` is the stage catalogue with this
 * student's status per item. Only the overview decides the page's state: the
 * programme failing degrades the three cards, not the whole landing.
 *
 * The stage cards come from a code catalogue where only a student's STATUS is a
 * row (see app/models/milestone.py). A row with a `route` navigates — English
 * Baseline to its own screen, Mock Interview to the mock interviewer — and the
 * component keeps whatever route the API gives rather than mapping keys itself.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';

// ---- GET /student/programme ------------------------------------------------

interface ProgrammeItem {
  key: string;
  label: string;
  route: string | null;
  status: string;
  glyph: string;
  tone: 'good' | 'warn' | 'neutral';
  title: string;
}

interface Stage {
  key: string;
  label: string;
  completed: number;
  total: number;
  items: ProgrammeItem[];
}

interface Programme {
  stages: Stage[];
  completed: number;
  total: number;
  percent: number;
}

// ---- GET /student/overview (snake_case, verbatim from the API) --------------

interface Dashboard {
  name: string;
  usn: string | null;
  current_stage: string;
  current_semester: number;
  latest_cgpa: number | null;
  attendance_percent: number;
}

interface CourseAttendance {
  course_code: string;
  present: number;
  total: number;
  percent: number;
}
interface AttendanceSummary {
  overall_percent: number;
  present: number;
  total: number;
  by_course: CourseAttendance[];
}

interface SemesterResult {
  semester: number;
  sgpa: number | null;
  cgpa: number | null;
}

interface Streak {
  current: number;
  longest: number;
  days_active: number;
  last_active: string | null;
}

interface ReadinessFactor {
  label: string;
  met: boolean;
  detail: string;
  weight: number;
  /**
   * FALSE means nothing has been imported or recorded for this check, so `met`
   * is not an answer. Read this BEFORE `met`: until 2026-09-13 the API returned
   * 0.0 for a student with no attendance rows and this card drew
   * "Attendance 0.0% vs required 75.0%" with a red Not-met chip — a failing
   * grade for an assessment that had not happened. See
   * `routers/student.py::_attendance_pct`.
   */
  measured: boolean;
}
interface PlacementReadiness {
  /** NULL when not one check could be measured — never 0. */
  score: number | null;
  band: string;
  summary: string;
  factors: ReadinessFactor[];
}

interface Recommendation {
  title: string;
  why: string;
  cta_label: string;
  cta_route: string;
}

interface Qualification {
  level: string;
  institution: string;
  board: string | null;
  year: number;
  marks: number;
  max_marks: number;
  percent: number;
  medium: string | null;
  location: string | null;
  subjects: string | null;
}
interface AcademicGap {
  twelfth_to_grad_mo: number;
  diploma_to_grad_mo: number;
  grad_to_pg_mo: number;
  other_mo: number;
  total_mo: number;
}
interface Academics {
  qualifications: Qualification[];
  gap: AcademicGap;
}

interface Overview {
  dashboard: Dashboard;
  attendance: AttendanceSummary | null;
  results: SemesterResult[] | null;
  streak: Streak | null;
  placement_readiness: PlacementReadiness | null;
  recommendations: { items: Recommendation[] } | null;
  swoc: SwocBoard | null;
  academics: Academics | null;
  /** The role preferences the office loaded, first choice first. */
  career_goal: { preferences: string[] } | null;
}

// ---- overview.swoc -----------------------------------------------------------

/** One SWOC line as /student/overview returns it: text, the viewpoint that
 *  wrote it (PLACEMENT / MENTOR / PM) and a 1-5 weight the API sorts by. */
interface SwocItem {
  source: string;
  text: string;
  weight: number;
}

/** The board: four lists, heaviest first. Written by the placement cell and
 *  mentors in the admin's SWOC Notes; the student only reads. */
interface SwocBoard {
  strengths: SwocItem[];
  weaknesses: SwocItem[];
  opportunities: SwocItem[];
  challenges: SwocItem[];
}

/** The card's four tiles in the design's order, each with its tint. */
const SWOC_TILES: { key: keyof SwocBoard; label: string; tone: 'good' | 'risk' | 'warn' | 'neutral' }[] = [
  { key: 'strengths', label: 'Strength', tone: 'good' },
  { key: 'weaknesses', label: 'Weakness', tone: 'risk' },
  { key: 'opportunities', label: 'Opportunity', tone: 'warn' },
  { key: 'challenges', label: 'Challenge', tone: 'neutral' },
];

/** Several lines arrive per quadrant; the landing tile shows them as one
 *  sentence.
 *
 *  THE MENTOR / TPO LOG NO LONGER JOINS — B7.5 gave every line its author, its
 *  date and an acknowledgement, and a concatenated string has nowhere to put any
 *  of the three, so that screen lists them. This tile stays a summary on
 *  purpose: it is four boxes on a landing page beside everything else the
 *  student has to do, and a name and a date per line there is a card that has
 *  become the board. The two still read the same texts in the same order out of
 *  the same payload; the Log is simply where the board is read in full. */
function joinSwoc(items: SwocItem[]): string | null {
  return items.length ? items.map((i) => i.text).join(' · ') : null;
}

// ---- view models -------------------------------------------------------------

type ChipTone = 'good' | 'warn' | 'risk' | 'neutral';

interface AttendanceBar {
  label: string;
  percent: number;
  caption: string;
}

interface MarksPoint {
  sem: number;
  x: number;
  y: number;
  label: string;
}
interface MarksChart {
  semesters: string[];
  points: MarksPoint[];
  solid: string;
  dashed: string;
  caption: string;
  aria: string;
}

interface QualificationCard {
  level: string;
  institution: string;
  board: string | null;
  year: number;
  percent: string;
  marks: string;
  medium: string | null;
  location: string | null;
}

/** The programme's stage names, in order — the header's "Excel-Adv stage"
 *  reads from here. */
const STAGES: { key: string; label: string }[] = [
  { key: 'REBOOT', label: 'Reboot' },
  { key: 'EXCEL', label: 'Excel' },
  { key: 'EXCEL_ADVANCED', label: 'Excel-Adv' },
  { key: 'ELEVATE', label: 'Elevate' },
];

/** The three cards' names when the programme read itself fails, so the page
 *  keeps its shape and the cards can say why they are empty. */
const STAGE_CARD_LABELS = ['Reboot', 'Excel', 'Elevate'];

const QUALIFICATION_LABEL: Record<string, string> = {
  TENTH: '10th Standard',
  TWELFTH: '12th Standard',
  DIPLOMA: 'Diploma',
  UNDERGRAD: 'Undergraduate',
  POSTGRAD: 'Postgraduate',
};

const GAP_LABEL: [keyof AcademicGap, string][] = [
  ['twelfth_to_grad_mo', '12th → graduation'],
  ['diploma_to_grad_mo', 'Diploma → graduation'],
  ['grad_to_pg_mo', 'Graduation → PG'],
  ['other_mo', 'Other'],
];

/** An MBA is four semesters; the chart always draws at least that many so the
 *  unpublished ones read as "not yet" rather than as absent. */
const MIN_SEMESTERS = 4;

@Component({
  selector: 'app-student-home',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class StudentHomeComponent {
  private readonly auth = inject(AuthService);

  readonly stageCardLabels = STAGE_CARD_LABELS;
  readonly gapLabels = GAP_LABEL;

  readonly state = signal<'loading' | 'data' | 'error'>('loading');
  readonly overview = signal<Overview | null>(null);
  readonly programme = signal<Programme | null>(null);
  /** True when the programme read failed while the overview succeeded. */
  readonly programmeError = signal(false);

  constructor() {
    void this.load();
  }

  // ---- header ----------------------------------------------------------------

  /** First name only — the greeting reads "Welcome back, Asha", not a full legal
   *  name. The dashboard carries the session's name; the session itself is the
   *  fallback while the read is in flight. */
  readonly firstName = computed(() => {
    const name = (this.overview()?.dashboard.name ?? this.auth.session()?.name ?? '').trim();
    return name ? name.split(/\s+/)[0] : null;
  });

  readonly subline = computed(() => {
    const d = this.overview()?.dashboard;
    if (!d) return null;
    const stage = STAGES.find((s) => s.key === d.current_stage)?.label ?? this.prettify(d.current_stage);
    const parts = [`${stage} stage`, `Semester ${d.current_semester}`];
    if (d.usn) parts.push(d.usn);
    return parts.join(' · ');
  });

  readonly streak = computed(() => this.overview()?.streak ?? null);

  // ---- SWOC ------------------------------------------------------------------

  readonly swocTiles = computed(() => {
    const board = this.overview()?.swoc ?? null;
    return SWOC_TILES.map((t) => ({ ...t, text: board ? joinSwoc(board[t.key]) : null }));
  });
  readonly swocWritten = computed(() => this.swocTiles().some((t) => !!t.text));

  // ---- attendance --------------------------------------------------------------

  readonly attendanceBars = computed<AttendanceBar[]>(() => {
    const att = this.overview()?.attendance;
    if (!att) return [];
    if (att.by_course.length) {
      return att.by_course.map((c) => ({
        label: c.course_code,
        percent: this.clampPct(c.percent),
        caption: `${Math.round(c.percent)}%`,
      }));
    }
    // Attendance recorded but not broken down by course: one overall row is
    // still the truth, so it is shown rather than the empty line.
    if (att.total > 0) {
      return [
        {
          label: 'Overall',
          percent: this.clampPct(att.overall_percent),
          caption: `${Math.round(att.overall_percent)}%`,
        },
      ];
    }
    return [];
  });

  // ---- VTU marks -----------------------------------------------------------------

  readonly marksChart = computed<MarksChart | null>(() => {
    const rows = this.overview()?.results ?? [];
    const published = rows
      .map((r) => ({ sem: r.semester, val: r.cgpa ?? r.sgpa }))
      .filter((r): r is { sem: number; val: number } => r.val != null)
      .sort((a, b) => a.sem - b.sem);
    if (!published.length) return null;

    const n = Math.max(MIN_SEMESTERS, ...published.map((p) => p.sem));
    const x = (sem: number) => ((sem - 0.5) / n) * 100;
    const y = (val: number) => 100 - Math.max(0, Math.min(10, val)) * 10;

    const points = published.map((p) => ({
      sem: p.sem,
      x: x(p.sem),
      y: y(p.val),
      label: p.val.toFixed(1),
    }));
    const solid = points.map((p) => `${p.x},${p.y}`).join(' ');

    // The dashed continuation holds the last published value across the
    // semesters still to come — a flat "not yet" line, never a projection.
    const last = points[points.length - 1];
    const pending = Array.from({ length: n }, (_, i) => i + 1).filter(
      (sem) => !published.some((p) => p.sem === sem),
    );
    const future = pending.filter((sem) => sem > last.sem);
    const dashed = future.length
      ? [last, ...future.map((sem) => ({ x: x(sem), y: last.y }))]
          .map((p) => `${p.x},${p.y}`)
          .join(' ')
      : '';

    const semesters = Array.from({ length: n }, (_, i) => `Sem ${i + 1}`);
    const caption = pending.length
      ? `CGPA out of 10 · ${this.semRange(pending)} not published yet`
      : 'CGPA out of 10';
    const aria =
      'CGPA by semester: ' +
      points.map((p) => `Sem ${p.sem} ${p.label}`).join(', ') +
      (pending.length ? `; ${this.semRange(pending)} not published yet` : '');

    return { semesters, points, solid, dashed, caption, aria };
  });

  readonly marksAxis = [10, 8, 6, 4, 2, 0];

  // ---- academic history ----------------------------------------------------------

  readonly qualifications = computed<QualificationCard[]>(() => {
    const quals = this.overview()?.academics?.qualifications ?? [];
    return quals.map((q) => ({
      level: QUALIFICATION_LABEL[q.level] ?? this.prettify(q.level),
      institution: q.institution,
      board: q.board,
      year: q.year,
      percent: `${Math.round(q.percent)}%`,
      marks: `${this.trimNumber(q.marks)} / ${this.trimNumber(q.max_marks)}`,
      medium: q.medium,
      location: q.location,
    }));
  });

  readonly gap = computed(() => this.overview()?.academics?.gap ?? null);

  /** Only the gap lines with months in them — a zero line says nothing. */
  readonly gapLines = computed(() => {
    const g = this.gap();
    if (!g) return [];
    return GAP_LABEL.filter(([key]) => g[key] > 0).map(([key, label]) => ({
      label,
      months: g[key],
    }));
  });

  // ---- placement readiness -----------------------------------------------------

  readonly readiness = computed(() => this.overview()?.placement_readiness ?? null);

  /** The first preference is the goal; the rest are backups, in order. Null
   *  when the office has not loaded this student's preferences. */
  readonly careerGoal = computed(() => {
    const prefs = this.overview()?.career_goal?.preferences ?? [];
    if (!prefs.length) return null;
    return { first: prefs[0], backups: prefs.slice(1) };
  });

  bandChip(band: string): ChipTone {
    if (band === 'Ready' || band === 'On track') return 'good';
    if (band === 'Developing') return 'warn';
    // "Not assessed yet" is NEUTRAL, not risk. A red chip over a score nobody
    // could compute is the same false verdict in a colour — and the house rule
    // is that status is text AND colour together, so the two must agree.
    if (band.startsWith('Not assessed')) return 'neutral';
    return 'risk';
  }

  /** The chip beside one factor: met, not met, or not measured at all. */
  factorChip(f: ReadinessFactor): { tone: ChipTone; icon: string; text: string } {
    if (!f.measured) return { tone: 'neutral', icon: 'remove', text: 'Not measured' };
    return f.met
      ? { tone: 'good', icon: 'check', text: 'Met' }
      : { tone: 'risk', icon: 'close', text: 'Not met' };
  }

  readonly recommendations = computed<Recommendation[] | null>(() => {
    const r = this.overview()?.recommendations;
    return r ? r.items : null;
  });

  // ---- loading -------------------------------------------------------------------------

  async load(): Promise<void> {
    this.state.set('loading');
    this.programmeError.set(false);
    const [overview, programme] = await Promise.allSettled([
      this.get<Overview>('/student/overview'),
      this.get<Programme>('/student/programme'),
    ]);

    if (programme.status === 'fulfilled') this.programme.set(programme.value);
    else this.programmeError.set(true);

    if (overview.status === 'fulfilled' && overview.value?.dashboard) {
      this.overview.set(overview.value);
      this.state.set('data');
    } else {
      this.state.set('error');
    }
  }

  private async get<T>(path: string): Promise<T> {
    const res = await fetch(`${environment.apiBase}${path}`, { credentials: 'include' });
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as T;
  }

  // ---- helpers ---------------------------------------------------------------------------

  private clampPct(v: number): number {
    return Math.max(0, Math.min(100, Math.round(v)));
  }

  private prettify(value: string): string {
    const words = value.replace(/_/g, ' ').toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  /** 88 → "88", 88.5 → "88.5" — marks print as recorded, without a trailing .0. */
  private trimNumber(v: number): string {
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  }

  /** [3, 4] → "Sem 3–4"; [4] → "Sem 4"; [2, 4] → "Sem 2, 4". */
  private semRange(sems: number[]): string {
    if (sems.length === 1) return `Sem ${sems[0]}`;
    const contiguous = sems.every((s, i) => i === 0 || s === sems[i - 1] + 1);
    return contiguous ? `Sem ${sems[0]}–${sems[sems.length - 1]}` : `Sem ${sems.join(', ')}`;
  }
}
