/**
 * One student's fees, opened from "View" on the board. The accounts office
 * records a payment taken at the counter or by bank here (a cheque, draft or
 * bank transfer needs its reference number), and downloads a paid fee's
 * receipt.
 */

import { Component, OnInit, input, output, signal } from '@angular/core';

import {
  STATUS_LABELS,
  calendarDay,
  feeApi,
  feeDownloadUrl,
  quotaLabel,
  rupees,
  statusTone,
} from '../../../core/fees';
import { FeeCharge, FeeOptions, RosterRow } from './fees.model';

@Component({
  selector: 'app-student-fees-dialog',
  standalone: true,
  templateUrl: './student-fees-dialog.component.html',
  styleUrls: ['./fee-dialog.scss', './student-fees-dialog.component.scss'],
})
export class StudentFeesDialogComponent implements OnInit {
  readonly student = input.required<RosterRow>();
  readonly options = input.required<FeeOptions>();
  readonly closed = output<void>();
  readonly changed = output<void>();
  readonly raiseAnother = output<void>();

  readonly rupees = rupees;
  readonly calendarDay = calendarDay;
  readonly quotaLabel = quotaLabel;
  readonly statusTone = statusTone;
  readonly statusLabels = STATUS_LABELS;

  readonly charges = signal<FeeCharge[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  /** The charge whose "Record payment" form is open. */
  readonly paying = signal<string | null>(null);
  readonly method = signal('CASH');
  readonly reference = signal('');
  readonly busy = signal(false);

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.charges.set(
        await feeApi<FeeCharge[]>(
          `/admin/fees/charges?student_id=${encodeURIComponent(this.student().student_id)}`,
          {},
          "Could not load this student's fees",
        ),
      );
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  methodLabel(value: string | null): string {
    if (!value) return '';
    if (value === 'RAZORPAY') return 'Online (Razorpay)';
    return this.options().payment_methods.find((m) => m.value === value)?.label ?? value;
  }

  receiptUrl(chargeId: string): string {
    return feeDownloadUrl(`/admin/fees/charges/${chargeId}/receipt`);
  }

  openPayment(chargeId: string): void {
    this.paying.set(chargeId);
    this.method.set('CASH');
    this.reference.set('');
    this.error.set(null);
  }

  async recordPayment(): Promise<void> {
    const id = this.paying();
    if (!id || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      await feeApi(
        `/admin/fees/charges/${id}/payments`,
        {
          method: 'POST',
          body: JSON.stringify({ method: this.method(), reference: this.reference().trim() || null }),
        },
        'Could not record the payment',
      );
      this.paying.set(null);
      await this.load();
      this.changed.emit();
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.busy.set(false);
    }
  }
}
