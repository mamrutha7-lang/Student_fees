/**
 * Alumni Jobs Sheet — the one-page postings list.
 *
 * GET /alumni/jobs returns the public posting fields only: no match % and no
 * eligibility verdict, because those are computed from a Student's skills and
 * marks, which an alumnus does not have. Apply links open the employer's page.
 */

import { Component, computed, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { environment } from '../../../../environments/environment';
import { jobSourceCredit } from '../../../core/job-source';

interface JobRow {
  id: string;
  title: string;
  company: string;
  degree_level: string;
  location: string | null;
  apply_url: string | null;
  required_skills: string[];
  closes_on: string | null;
  posted_on: string | null;
  /** `office`, or the board the daily job agent found it on. */
  source?: string;
}

@Component({
  selector: 'app-alumni-jobs',
  standalone: true,
  imports: [DatePipe, FormsModule],
  templateUrl: './alumni-jobs.component.html',
})
export class AlumniJobsComponent {
  private readonly apiBase = environment.apiBase;

  readonly jobs = signal<JobRow[] | null>(null);
  readonly error = signal<string | null>(null);
  /** "Jobs by Adzuna" under a posting the job agent found; null for the office's. */
  readonly credit = jobSourceCredit;

  readonly filter = signal('');
  readonly filtered = computed(() => {
    const list = this.jobs() ?? [];
    const q = this.filter().trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (j) =>
        j.title.toLowerCase().includes(q) ||
        j.company.toLowerCase().includes(q) ||
        (j.location ?? '').toLowerCase().includes(q),
    );
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch(`${this.apiBase}/alumni/jobs`, { credentials: 'include' });
      if (!res.ok) {
        this.error.set('Could not load the jobs sheet.');
        return;
      }
      this.jobs.set((await res.json()) as JobRow[]);
      this.error.set(null);
    } catch {
      this.error.set('Could not reach the server.');
    }
  }

  closed(j: JobRow): boolean {
    if (!j.closes_on) return false;
    return new Date(j.closes_on).getTime() < Date.now() - 24 * 60 * 60 * 1000;
  }
}
