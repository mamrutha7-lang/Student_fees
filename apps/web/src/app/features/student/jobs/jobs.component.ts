/**
 * Student Jobs — one board of opportunities, filtered.
 *
 * One TABLE over GET /student/jobs — Role · Location · Eligibility · Skill match
 * · Status · Action, as the handoff draws it: title and company, location with
 * the deadline (derived from closes_on and coloured as it nears) beneath it, the
 * eligibility verdict, a match-% bar, an application-status chip that gives way
 * to the server's own reason on an ineligible row, and one primary CTA (Apply
 * opens apply_url and POSTs /jobs/{id}/apply). "View JD" opens the posting's
 * description in a row of its own beneath it; for a job-agent posting that is
 * the board's summary, and the full text is still behind Apply. Three client-side filters —
 * eligibility, location, deadline — narrow the fetched list.
 *
 * IT USED TO BE THREE TABS AND IS NOW ONE BOARD. Applications was a table of
 * rows already on this screen carrying an "Applied" chip, so it re-listed what
 * the cards said. Offers duplicated /student/offers, which is the fuller screen
 * — draft-then-submit lifecycle, joining date, more fields — so the copy here
 * was the weaker of two implementations of the same thing. Both are gone; the
 * Offers ROUTE is untouched and still the place offers are recorded.
 *
 * The UG/PG toggle went with them. The feed is already scoped to this student,
 * so a filter on their own degree level could only ever hide rows that were
 * theirs to see.
 *
 * AN INELIGIBLE POSTING NAMES ITS REASON. The server returns `reasons` with the
 * verdict, and the card prints them: "not eligible" with no cause reads as a
 * bug in the product rather than a gate the student could act on.
 *
 * Markup reuses the global reep-v2 classes (.card / .chip / .match-bar / .btn);
 * the scss adds the framed table, its column weights and the deadline/match
 * tints.
 */

import { DecimalPipe } from '@angular/common';
import { Component, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../environments/environment';
import { failedReadMessage } from '../../../core/feature-refusal';
import { jobSourceCredit } from '../../../core/job-source';

type Tone = 'good' | 'warn' | 'risk' | 'neutral';

type EligFilter = 'all' | 'eligible' | 'ineligible';
type DeadlineFilter = 'all' | 'soon' | 'open';

/** Row shape of GET /student/jobs (snake_case, verbatim from JobRowOut). */
interface JobRow {
  id: string;
  title: string;
  company: string;
  degree_level: string;
  location: string | null;
  apply_url: string | null;
  required_skills: string[];
  /** Null when the posting lists no skills: the screen says so rather than a 100%. */
  match_percent: number | null;
  eligible: boolean;
  reasons: string[];
  applied: boolean;
  closes_on: string | null;
  posted_on: string | null;
  /** `office`, or the board the daily job agent found it on. */
  source?: string;
  /** The job description (JD); for an agent posting, the board's summary. */
  description?: string | null;
}

/** Resolved deadline view for a card. */
interface DeadlineInfo {
  tone: Tone;
  icon: string;
  label: string;
  closed: boolean;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

@Component({
  selector: 'app-student-jobs',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './jobs.component.html',
  styleUrl: './jobs.component.scss',
})
export class JobsComponent {
  readonly jobs = signal<JobRow[]>([]);

  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  /** "Jobs by Adzuna" under a posting the job agent found; null for the office's. */
  readonly credit = jobSourceCredit;

  /** The postings whose JD is open under their row. More than one may be. */
  readonly openJd = signal<ReadonlySet<string>>(new Set());

  // --- opportunity filters (client-side over the fetched list) ---
  readonly filterElig = signal<EligFilter>('all');
  readonly filterLocation = signal<string>('all');
  readonly filterDeadline = signal<DeadlineFilter>('all');

  /** Distinct, sorted locations actually present on the board. */
  readonly locations = computed(() => {
    const set = new Set<string>();
    for (const j of this.jobs()) if (j.location) set.add(j.location);
    return [...set].sort((a, b) => a.localeCompare(b));
  });

  /** The visible cards after the three filters are applied. */
  readonly opportunityRows = computed(() => {
    const elig = this.filterElig();
    const loc = this.filterLocation();
    const dl = this.filterDeadline();
    return this.jobs().filter((j) => {
      if (elig === 'eligible' && !j.eligible) return false;
      if (elig === 'ineligible' && j.eligible) return false;
      if (loc !== 'all' && j.location !== loc) return false;
      if (dl === 'soon') {
        const days = this.daysLeft(j.closes_on);
        if (days === null || days < 0 || days > 7) return false;
      }
      if (dl === 'open' && this.deadline(j).closed) return false;
      return true;
    });
  });

  readonly anyFilter = computed(
    () =>
      this.filterElig() !== 'all' ||
      this.filterLocation() !== 'all' ||
      this.filterDeadline() !== 'all',
  );

  constructor() {
    void this.loadJobs();
  }

  private async loadJobs(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const res = await fetch(`${environment.apiBase}/student/jobs`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set(await failedReadMessage(res, 'Could not load the jobs board.'));
        return;
      }
      this.jobs.set((await res.json()) as JobRow[]);
    } catch {
      this.error.set('Could not reach the server.');
    } finally {
      this.loading.set(false);
    }
  }

  clearFilters(): void {
    this.filterElig.set('all');
    this.filterLocation.set('all');
    this.filterDeadline.set('all');
  }

  // --- deadline helpers -----------------------------------------------------

  /** Whole days from now until the deadline; null when there is no deadline. */
  daysLeft(closesOn: string | null): number | null {
    if (!closesOn) return null;
    const t = new Date(closesOn).getTime();
    if (Number.isNaN(t)) return null;
    return Math.ceil((t - Date.now()) / MS_PER_DAY);
  }

  private fmtDate(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  /** The deadline chip's tone, icon and label for a row. */
  deadline(row: JobRow): DeadlineInfo {
    const days = this.daysLeft(row.closes_on);
    if (days === null)
      return { tone: 'neutral', icon: 'event_available', label: 'No deadline', closed: false };
    if (days < 0) return { tone: 'risk', icon: 'event_busy', label: 'Closed', closed: true };
    if (days === 0) return { tone: 'risk', icon: 'schedule', label: 'Closes today', closed: false };
    if (days <= 3)
      return {
        tone: 'risk',
        icon: 'schedule',
        label: `Closes in ${days} day${days === 1 ? '' : 's'}`,
        closed: false,
      };
    if (days <= 7)
      return { tone: 'warn', icon: 'schedule', label: `Closes in ${days} days`, closed: false };
    return {
      tone: 'neutral',
      icon: 'event',
      label: `Closes ${this.fmtDate(row.closes_on!)}`,
      closed: false,
    };
  }

  jdOpen(id: string): boolean {
    return this.openJd().has(id);
  }

  toggleJd(id: string): void {
    const next = new Set(this.openJd());
    if (!next.delete(id)) next.add(id);
    this.openJd.set(next);
  }

  /** Tint the match bar so a weak match reads at a glance. */
  matchTone(pct: number): Tone {
    if (pct >= 70) return 'good';
    if (pct >= 40) return 'warn';
    return 'risk';
  }

  // --- apply ----------------------------------------------------------------

  /** Open the external posting (if any) and record the application; the chip
   *  flips optimistically and reverts on failure. */
  async apply(row: JobRow): Promise<void> {
    if (row.applied || !row.eligible) return;
    if (row.apply_url) window.open(row.apply_url, '_blank', 'noopener');
    this.jobs.update((rows) => rows.map((r) => (r.id === row.id ? { ...r, applied: true } : r)));
    try {
      const res = await fetch(`${environment.apiBase}/student/jobs/${row.id}/apply`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: null }),
      });
      if (!res.ok) throw new Error();
    } catch {
      this.jobs.update((rows) => rows.map((r) => (r.id === row.id ? { ...r, applied: false } : r)));
    }
  }
}
