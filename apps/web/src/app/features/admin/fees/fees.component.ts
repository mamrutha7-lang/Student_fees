/**
 * Student fees — the Main Admin raises a fee for a student, then tracks it
 * until it is paid. Students see every raised fee on their own My fees.
 *
 * The board is one GET (`/admin/fees/roster`) per change of Department,
 * Course, Academic year or Quota; the quick filter, the Status filter and the
 * paging work over those rows on the screen, as the endpoint's docstring says.
 */

import { Component, computed, inject, signal } from '@angular/core';

import {
  STATUS_LABELS,
  calendarDay,
  feeApi,
  feeDownloadUrl,
  initials,
  quotaLabel,
  rupees,
  statusTone,
} from '../../../core/fees';
import { FeeStructureDialogComponent } from './fee-structure-dialog.component';
import { FeeOptions, RemindersResult, Roster, RosterRow } from './fees.model';
import { RaiseFeeDialogComponent } from './raise-fee-dialog.component';
import { StudentFeesDialogComponent } from './student-fees-dialog.component';

const PAGE_SIZE = 10;

@Component({
  selector: 'app-admin-fees',
  standalone: true,
  imports: [RaiseFeeDialogComponent, FeeStructureDialogComponent, StudentFeesDialogComponent],
  templateUrl: './fees.component.html',
  styleUrl: './fees.component.scss',
})
export class AdminFeesComponent {
  readonly rupees = rupees;
  readonly calendarDay = calendarDay;
  readonly initials = initials;
  readonly quotaLabel = quotaLabel;
  readonly statusTone = statusTone;
  readonly statusLabels = STATUS_LABELS;

  readonly options = signal<FeeOptions | null>(null);
  readonly roster = signal<Roster | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);

  // -- the filter bar --------------------------------------------------------
  readonly departmentId = signal('');
  readonly courseId = signal('');
  readonly academicYear = signal('');
  readonly quota = signal('');
  readonly status = signal('');
  readonly quickFilter = signal('');

  readonly selected = signal<ReadonlySet<string>>(new Set());
  readonly page = signal(0);
  readonly reminding = signal(false);

  // -- dialogs ---------------------------------------------------------------
  /** null = closed; '' = open with no student chosen; else the student's id. */
  readonly raiseFor = signal<string | null>(null);
  readonly raiseSearch = signal('');
  readonly structureOpen = signal(false);
  readonly viewing = signal<RosterRow | null>(null);

  readonly courses = computed(() => {
    const all = this.options()?.courses ?? [];
    const department = this.departmentId();
    return department ? all.filter((c) => c.department_id === department) : all;
  });

  readonly filtered = computed(() => {
    const rows = this.roster()?.rows ?? [];
    const needle = this.quickFilter().trim().toLowerCase();
    const status = this.status();
    return rows.filter(
      (r) =>
        (!status || r.status === status) &&
        (!needle ||
          r.name.toLowerCase().includes(needle) ||
          (r.usn ?? '').toLowerCase().includes(needle) ||
          (r.course_name ?? '').toLowerCase().includes(needle)),
    );
  });

  readonly pageCount = computed(() => Math.max(1, Math.ceil(this.filtered().length / PAGE_SIZE)));
  readonly pageRows = computed(() => {
    const start = Math.min(this.page(), this.pageCount() - 1) * PAGE_SIZE;
    return this.filtered().slice(start, start + PAGE_SIZE);
  });
  readonly allOnPageSelected = computed(
    () => this.pageRows().length > 0 && this.pageRows().every((r) => this.selected().has(r.student_id)),
  );

  readonly exportUrl = computed(() => feeDownloadUrl(`/admin/fees/roster.csv?${this.query(true)}`));

  constructor() {
    void this.start();
  }

  private async start(): Promise<void> {
    try {
      const options = await feeApi<FeeOptions>('/admin/fees/options', {}, 'Could not load the fee options');
      this.options.set(options);
      this.academicYear.set(options.current_academic_year);
      await this.load();
    } catch (e) {
      this.error.set((e as Error).message);
    }
  }

  private query(withStatus = false): string {
    const q = new URLSearchParams({ academic_year: this.academicYear() });
    if (this.departmentId()) q.set('department_id', this.departmentId());
    if (this.courseId()) q.set('course_id', this.courseId());
    if (this.quota()) q.set('quota', this.quota());
    if (withStatus && this.status()) q.set('status', this.status());
    return q.toString();
  }

  async load(): Promise<void> {
    if (!this.academicYear()) return;
    this.loading.set(true);
    this.error.set(null);
    try {
      this.roster.set(await feeApi<Roster>(`/admin/fees/roster?${this.query()}`, {}, 'Could not load student fees'));
      this.selected.set(new Set());
      this.page.set(0);
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  // -- filter handlers ---------------------------------------------------------
  setDepartment(value: string): void {
    this.departmentId.set(value);
    if (this.courseId() && !this.courses().some((c) => c.value === this.courseId())) this.courseId.set('');
    void this.load();
  }
  setCourse(value: string): void {
    this.courseId.set(value);
    void this.load();
  }
  setYear(value: string): void {
    this.academicYear.set(value);
    void this.load();
  }
  setQuota(value: string): void {
    this.quota.set(value);
    void this.load();
  }
  setStatus(value: string): void {
    this.status.set(value);
    this.page.set(0);
  }
  setQuickFilter(value: string): void {
    this.quickFilter.set(value);
    this.page.set(0);
  }

  // -- selection and paging -----------------------------------------------------
  toggle(id: string): void {
    const next = new Set(this.selected());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.selected.set(next);
  }
  togglePage(): void {
    const next = new Set(this.selected());
    const all = this.allOnPageSelected();
    for (const r of this.pageRows()) {
      if (all) next.delete(r.student_id);
      else next.add(r.student_id);
    }
    this.selected.set(next);
  }
  previousPage(): void {
    this.page.set(Math.max(0, this.page() - 1));
  }
  nextPage(): void {
    this.page.set(Math.min(this.pageCount() - 1, this.page() + 1));
  }

  // -- actions ---------------------------------------------------------------
  openRaise(row?: RosterRow): void {
    this.raiseSearch.set(row ? (row.usn ?? row.name) : '');
    this.raiseFor.set(row ? row.student_id : '');
  }

  async onRaised(message: string): Promise<void> {
    this.raiseFor.set(null);
    this.notice.set(message);
    await this.load();
  }

  async onPaymentRecorded(): Promise<void> {
    await this.load();
  }

  /** "Send reminders" (header, everyone with an unpaid fee) and "Send
   *  reminder" (table, only the ticked students). */
  async remind(selectedOnly: boolean): Promise<void> {
    if (this.reminding()) return;
    const ids = [...this.selected()];
    if (selectedOnly && !ids.length) {
      this.notice.set('Tick the students to remind first.');
      return;
    }
    this.reminding.set(true);
    this.error.set(null);
    try {
      const r = await feeApi<RemindersResult>(
        '/admin/fees/reminders',
        {
          method: 'POST',
          body: JSON.stringify({
            academic_year: this.academicYear(),
            student_ids: selectedOnly ? ids : null,
          }),
        },
        'Could not send reminders',
      );
      const parts = [`${r.sent} reminder(s) sent`];
      if (r.already_reminded_today) parts.push(`${r.already_reminded_today} already reminded today`);
      if (r.nothing_due) parts.push(`${r.nothing_due} with nothing due`);
      if (r.no_address) parts.push(`${r.no_address} without a working account`);
      if (r.failed) parts.push(`${r.failed} failed`);
      const where = r.delivered_by === 'console' ? ' (no mail service here: written to the server log)' : '';
      this.notice.set(parts.join(', ') + '.' + where);
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.reminding.set(false);
    }
  }
}
