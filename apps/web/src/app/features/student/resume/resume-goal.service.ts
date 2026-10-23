/**
 * The resume's GOAL — which role, where, and against which posting.
 *
 * A resume is only "good" relative to something. The builder used to be a
 * 15-section form with no target, so completeness was the only feedback it could
 * give and "80% complete" said nothing about whether the document would survive
 * the job the student actually wanted. The goal makes that judgement possible:
 * every downstream step (tailor, preview, export) reads it.
 *
 * IT PERSISTS THROUGH THE PROFILE MAP. ResumeBuilderService.data is an opaque
 * section-key map that round-trips to /student/resume-profile, so `goal` rides
 * along with it and needs no endpoint of its own. The backend's completeness
 * calculation counts a FIXED list of section keys, so an extra key cannot
 * inflate the percentage.
 *
 * THE MATCH NUMBER IS THE SERVER'S, NOT OURS. `match_percent`, `eligible` and
 * `reasons` come from GET /student/jobs, which applies the per-posting CGPA and
 * live-backlog gates. Recomputing a match here would produce a second, quieter
 * answer that could disagree with the Jobs screen — and the one thing this
 * screen must never do is tell a student they are eligible when the gate says
 * otherwise.
 */

import { Injectable, computed, inject, signal } from '@angular/core';

import { environment } from '../../../../environments/environment';
import { failedReadMessage } from '../../../core/feature-refusal';
import { ResumeBuilderService } from './resume-builder.service';

export interface JobRow {
  id: string;
  title: string;
  company: string;
  location: string | null;
  required_skills: string[];
  /** Null when the posting lists no skills. */
  match_percent: number | null;
  eligible: boolean;
  reasons: string[];
}

export interface ResumeGoal {
  role: string;
  location: string;
  opportunityId: string;
}

const DEFAULT_GOAL: ResumeGoal = { role: '', location: '', opportunityId: '' };

@Injectable({ providedIn: 'root' })
export class ResumeGoalService {
  private readonly svc = inject(ResumeBuilderService);

  /// null = not loaded yet; [] = loaded and there are no postings.
  readonly jobs = signal<JobRow[] | null>(null);
  readonly jobsError = signal<string | null>(null);

  readonly goal = computed<ResumeGoal>(() => ({
    ...DEFAULT_GOAL,
    ...(this.svc.section('goal', {}) as Partial<ResumeGoal>),
  }));

  /// Role options are the titles actually on offer plus whatever the student
  /// already chose, so the select can always show its own value.
  readonly roleOptions = computed<string[]>(() => {
    const titles = (this.jobs() ?? []).map((j) => j.title);
    const chosen = this.goal().role;
    return [...new Set([...titles, ...(chosen ? [chosen] : [])])];
  });

  readonly locationOptions = computed<string[]>(() => {
    const locs = (this.jobs() ?? []).map((j) => j.location).filter((l): l is string => !!l);
    const chosen = this.goal().location;
    return [...new Set([...locs, ...(chosen ? [chosen] : [])])];
  });

  readonly selectedJob = computed<JobRow | null>(() => {
    const id = this.goal().opportunityId;
    if (!id) return null;
    return (this.jobs() ?? []).find((j) => j.id === id) ?? null;
  });

  /**
   * The strip's right-hand chip. With no posting chosen there is no honest
   * number to show, so it says so rather than printing a 0% that reads as a
   * verdict.
   */
  readonly matchLabel = computed<string>(() => {
    const job = this.selectedJob();
    if (!job) return 'Choose an opportunity to see your match';
    // A posting that lists no skills has no match to state, and a 100% there
    // told students they were a perfect fit for anything.
    const match = job.match_percent === null ? 'Skills not listed' : `${Math.round(job.match_percent)}% match`;
    // Eligibility is a gate, not a score: a 90% skill match on a posting whose
    // CGPA cut-off you miss is still not eligible, and saying "90% match" alone
    // would read as "you can apply".
    return job.eligible ? `${match} · eligible` : `${match} · not yet eligible`;
  });

  readonly matchTone = computed<'good' | 'warn' | 'neutral'>(() => {
    const job = this.selectedJob();
    if (!job) return 'neutral';
    return job.eligible ? 'good' : 'warn';
  });

  /**
   * Changing the role or the location ABANDONS a posting that no longer matches.
   *
   * The three controls could disagree: picking "DataWorks — BI Developer ·
   * Remote" and then changing the role to "Junior Accountant" left the posting
   * selected, so the Tailor step headed its advice "Tailored for Junior
   * Accountant" directly above a verdict card reading "BI Developer ·
   * DataWorks". A goal that contradicts itself gives advice for a job the
   * student is not looking at.
   *
   * The posting is the strongest statement of intent (it fills the other two in
   * `setOpportunity`), so editing one of the weaker two means the student has
   * moved on from it — and the strip says "Not chosen" rather than quietly
   * keeping an answer they have just contradicted.
   */
  setRole(role: string): void {
    const job = this.selectedJob();
    const keeps = !job || job.title === role;
    this.svc.patch('goal', {
      ...this.goal(),
      role,
      opportunityId: keeps ? this.goal().opportunityId : '',
    });
  }
  setLocation(location: string): void {
    const job = this.selectedJob();
    const keeps = !job || (job.location ?? '') === location;
    this.svc.patch('goal', {
      ...this.goal(),
      location,
      opportunityId: keeps ? this.goal().opportunityId : '',
    });
  }
  setOpportunity(opportunityId: string): void {
    const job = (this.jobs() ?? []).find((j) => j.id === opportunityId);
    // Picking a posting is the strongest statement of intent on the strip, so it
    // fills in the role and location it implies rather than leaving three
    // controls that can disagree with each other.
    this.svc.patch('goal', {
      role: job?.title ?? this.goal().role,
      location: job?.location ?? this.goal().location,
      opportunityId,
    });
  }

  async load(): Promise<void> {
    if (this.jobs() !== null) return; // one fetch per session
    try {
      const res = await fetch(`${environment.apiBase}/student/jobs`, { credentials: 'include' });
      if (!res.ok) {
        this.jobsError.set(await failedReadMessage(res, 'Could not load opportunities.'));
        this.jobs.set([]);
        return;
      }
      this.jobs.set((await res.json()) as JobRow[]);
    } catch {
      this.jobsError.set('Could not reach the server.');
      this.jobs.set([]);
    }
  }
}
