/**
 * Raise a fee — one student at a time. The student sees the fee on My fees as
 * soon as it is raised, with status Pending.
 *
 * Pick the student (search by USN or name), and the card fills in their name,
 * course, department, batch and quota from the student record. Picking a fee
 * head fills the amount from the fee structure for that quota and year; the
 * office can still type another amount. A student with no recorded quota is
 * asked for one here, and raising the fee records it.
 *
 * The server decides everything: a second Tuition for the same year, a quota
 * that contradicts the record, a student who is not current — each comes back
 * as a sentence and is shown as it came.
 */

import { Component, OnInit, computed, input, output, signal } from '@angular/core';

import { feeApi, quotaLabel } from '../../../core/fees';
import { FeeOptions, StudentLookup, StudentSearch } from './fees.model';

interface RaisedOut {
  title: string;
  amount_rupees: number;
  quota_recorded: boolean;
}

@Component({
  selector: 'app-raise-fee-dialog',
  standalone: true,
  templateUrl: './raise-fee-dialog.component.html',
  styleUrls: ['./fee-dialog.scss', './raise-fee-dialog.component.scss'],
})
export class RaiseFeeDialogComponent implements OnInit {
  readonly options = input.required<FeeOptions>();
  /** Pre-selects this student (the board's "Raise fee" on a row). */
  readonly studentId = input<string | null>(null);
  readonly initialSearch = input('');
  readonly academicYear = input('');
  /** The board's filters, so the dropdown lists the same students. */
  readonly departmentId = input('');
  readonly courseId = input('');

  readonly closed = output<void>();
  readonly raised = output<string>();

  readonly quotaLabel = quotaLabel;

  readonly student = signal<StudentLookup | null>(null);
  readonly pickerOpen = signal(false);
  readonly search = signal('');
  readonly results = signal<StudentSearch | null>(null);
  readonly searching = signal(false);

  readonly year = signal('');
  readonly feeHead = signal('TUITION');
  readonly quota = signal('');
  readonly amount = signal('');
  readonly amountFromStructure = signal(false);
  readonly dueOn = signal('');
  readonly note = signal('');

  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  private searchTimer: ReturnType<typeof setTimeout> | null = null;
  private searchSeq = 0;

  /** The quota the fee is raised under: the record's, or the one picked here. */
  readonly effectiveQuota = computed(() => this.student()?.quota ?? this.quota());

  readonly canSubmit = computed(
    () =>
      !!this.student() &&
      !!this.year() &&
      !!this.feeHead() &&
      !!this.effectiveQuota() &&
      Number(this.amount()) >= 1 &&
      !!this.dueOn() &&
      !this.busy(),
  );

  readonly scopeLabel = computed(() => {
    const o = this.options();
    const dept = o.departments.find((d) => d.value === this.departmentId())?.label;
    const course = o.courses.find((c) => c.value === this.courseId())?.label;
    return [dept, course].filter(Boolean).join(' · ');
  });

  ngOnInit(): void {
    this.year.set(this.academicYear() || this.options().current_academic_year);
    this.search.set(this.initialSearch());
    void this.runSearch(true);
    if (!this.studentId()) this.pickerOpen.set(true);
  }

  // -- the Student ID dropdown --------------------------------------------------

  onSearch(value: string): void {
    this.search.set(value);
    if (this.searchTimer) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => void this.runSearch(false), 250);
  }

  private async runSearch(pickPreselected: boolean): Promise<void> {
    const seq = ++this.searchSeq;
    this.searching.set(true);
    const q = new URLSearchParams({ limit: '20' });
    if (this.search().trim()) q.set('q', this.search().trim());
    if (this.departmentId()) q.set('department_id', this.departmentId());
    if (this.courseId()) q.set('course_id', this.courseId());
    try {
      const found = await feeApi<StudentSearch>(`/admin/fees/students/search?${q}`, {}, 'Could not search students');
      if (seq !== this.searchSeq) return; // a newer search has answered
      this.results.set(found);
      if (pickPreselected && this.studentId()) {
        const match = found.rows.find((r) => r.student_id === this.studentId());
        if (match) this.pick(match);
      }
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      if (seq === this.searchSeq) this.searching.set(false);
    }
  }

  pick(row: StudentLookup): void {
    this.student.set(row);
    this.pickerOpen.set(false);
    this.quota.set(row.quota ?? '');
    this.error.set(null);
    void this.fillAmount();
  }

  // -- the fields ---------------------------------------------------------------

  setYear(value: string): void {
    this.year.set(value);
    void this.fillAmount();
  }
  setHead(value: string): void {
    this.feeHead.set(value);
    void this.fillAmount();
  }
  setQuota(value: string): void {
    this.quota.set(value);
    void this.fillAmount();
  }
  setAmount(value: string): void {
    this.amount.set(value.replace(/[^\d]/g, ''));
    this.amountFromStructure.set(false);
  }

  /** Fee amount = the fee structure's standard amount, when one is set. */
  private async fillAmount(): Promise<void> {
    const quota = this.effectiveQuota();
    if (!quota || !this.year() || !this.feeHead()) return;
    const q = new URLSearchParams({ academic_year: this.year(), quota, fee_head: this.feeHead() });
    try {
      const r = await feeApi<{ amount_rupees: number | null }>(`/admin/fees/structures/amount?${q}`);
      if (r.amount_rupees !== null) {
        this.amount.set(String(r.amount_rupees));
        this.amountFromStructure.set(true);
      } else if (this.amountFromStructure()) {
        this.amount.set('');
        this.amountFromStructure.set(false);
      }
    } catch {
      /* leave the box as it is; the office can type the amount */
    }
  }

  async submit(): Promise<void> {
    const s = this.student();
    if (!s || !this.canSubmit()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const r = await feeApi<RaisedOut>(
        '/admin/fees/charges',
        {
          method: 'POST',
          body: JSON.stringify({
            student_id: s.student_id,
            academic_year: this.year(),
            quota: this.effectiveQuota(),
            fee_head: this.feeHead(),
            amount_rupees: Number(this.amount()),
            due_on: this.dueOn(),
            note: this.note().trim() || null,
          }),
        },
        'Could not raise the fee',
      );
      const recorded = r.quota_recorded ? ` Their quota is now recorded as ${quotaLabel(this.effectiveQuota())}.` : '';
      this.raised.emit(
        `${r.title} fee of ₹${r.amount_rupees.toLocaleString('en-IN')} raised for ${s.name}.${recorded}`,
      );
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.busy.set(false);
    }
  }
}
