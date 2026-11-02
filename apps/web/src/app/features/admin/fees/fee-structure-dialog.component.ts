/**
 * Fee structure: the standard amount per fee head and quota, for one academic
 * year. It only FILLS the Fee amount box on Raise a fee; changing it never
 * touches a fee already raised (the server says so too).
 */

import { Component, OnInit, computed, input, output, signal } from '@angular/core';

import { feeApi } from '../../../core/fees';
import { FeeOptions, FeeStructureRow } from './fees.model';

@Component({
  selector: 'app-fee-structure-dialog',
  standalone: true,
  templateUrl: './fee-structure-dialog.component.html',
  styleUrls: ['./fee-dialog.scss', './fee-structure-dialog.component.scss'],
})
export class FeeStructureDialogComponent implements OnInit {
  readonly options = input.required<FeeOptions>();
  readonly academicYear = input('');
  readonly closed = output<void>();

  readonly year = signal('');
  /** What the server holds, keyed "QUOTA|HEAD". */
  readonly saved = signal<Record<string, number>>({});
  /** What is typed in each box, keyed the same way. */
  readonly drafts = signal<Record<string, string>>({});
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);

  readonly changed = computed(() => {
    const saved = this.saved();
    return Object.entries(this.drafts()).filter(
      ([key, value]) => value !== '' && Number(value) >= 1 && Number(value) !== saved[key],
    );
  });

  ngOnInit(): void {
    this.year.set(this.academicYear() || this.options().current_academic_year);
    void this.load();
  }

  key(quota: string, head: string): string {
    return `${quota}|${head}`;
  }

  async setYear(value: string): Promise<void> {
    this.year.set(value);
    await this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.notice.set(null);
    try {
      const rows = await feeApi<FeeStructureRow[]>(
        `/admin/fees/structures?academic_year=${encodeURIComponent(this.year())}`,
        {},
        'Could not load the fee structure',
      );
      const saved: Record<string, number> = {};
      const drafts: Record<string, string> = {};
      for (const r of rows) {
        saved[this.key(r.quota, r.fee_head)] = r.amount_rupees;
        drafts[this.key(r.quota, r.fee_head)] = String(r.amount_rupees);
      }
      this.saved.set(saved);
      this.drafts.set(drafts);
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  setDraft(key: string, value: string): void {
    this.drafts.set({ ...this.drafts(), [key]: value.replace(/[^\d]/g, '') });
  }

  async save(): Promise<void> {
    const changes = this.changed();
    if (!changes.length || this.saving()) return;
    this.saving.set(true);
    this.error.set(null);
    this.notice.set(null);
    let done = 0;
    try {
      for (const [key, value] of changes) {
        const [quota, fee_head] = key.split('|');
        await feeApi(
          '/admin/fees/structures',
          {
            method: 'PUT',
            body: JSON.stringify({ academic_year: this.year(), quota, fee_head, amount_rupees: Number(value) }),
          },
          'Could not save the amount',
        );
        this.saved.set({ ...this.saved(), [key]: Number(value) });
        done += 1;
      }
      this.notice.set(`${done} amount(s) saved for ${this.year()}. Fees already raised are unchanged.`);
    } catch (e) {
      this.error.set(`${done} saved before this: ${(e as Error).message}`);
    } finally {
      this.saving.set(false);
    }
  }
}
