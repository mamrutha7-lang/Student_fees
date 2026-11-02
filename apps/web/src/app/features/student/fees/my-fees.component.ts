/**
 * My fees — every fee the college office raised for this student, where each
 * one stands, and Pay now through Razorpay.
 *
 * PAYING: Pay now asks the API to open (or reuse) a Razorpay order for that
 * fee, opens Razorpay's checkout window, and sends Razorpay's signed reply to
 * `/verify`. The fee turns Paid only when the SERVER has checked that
 * signature; nothing on this screen can mark a fee paid. If the window closes
 * after the money left but before the reply arrived, Razorpay's webhook
 * settles it on the server, so the screen says to check back rather than
 * claiming a failure.
 *
 * Razorpay's checkout script is loaded only when Pay now is pressed, so a
 * student who only reads their fees downloads nothing from Razorpay.
 */

import { Component, inject, signal } from '@angular/core';

import { AuthService } from '../../../core/auth.service';
import {
  STATUS_LABELS,
  calendarDay,
  feeApi,
  feeDownloadUrl,
  quotaLabel,
  rupees,
  statusTone,
} from '../../../core/fees';

interface MyFee {
  id: string;
  title: string;
  description: string | null;
  amount_rupees: number;
  due_on: string | null;
  status: string;
  paid_at: string | null;
  fee_head: string | null;
  academic_year: string | null;
  quota: string | null;
  raised_on: string | null;
  display_status: string | null;
  payment_method: string | null;
  receipt_number: string | null;
}

interface MyFees {
  payments_enabled: boolean;
  fees: MyFee[];
  academic_year: string | null;
  summary: {
    total_raised_rupees: number;
    paid_rupees: number;
    balance_rupees: number;
    fee_count: number;
    paid_count: number;
    pending_count: number;
    overdue_count: number;
    quota: string | null;
  } | null;
  overdue_titles: string[];
}

interface Order {
  key_id: string;
  order_id: string;
  amount_paise: number;
  currency: string;
  title: string;
}

interface CheckoutReply {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

type RazorpayConstructor = new (options: Record<string, unknown>) => {
  open(): void;
  on(event: string, handler: (response: { error?: { description?: string } }) => void): void;
};

const CHECKOUT_SCRIPT = 'https://checkout.razorpay.com/v1/checkout.js';
let checkoutLoading: Promise<RazorpayConstructor> | null = null;

/** Load Razorpay's checkout once, on first use. */
function loadCheckout(): Promise<RazorpayConstructor> {
  const existing = (window as unknown as { Razorpay?: RazorpayConstructor }).Razorpay;
  if (existing) return Promise.resolve(existing);
  checkoutLoading ??= new Promise<RazorpayConstructor>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = CHECKOUT_SCRIPT;
    script.async = true;
    script.onload = () => {
      const loaded = (window as unknown as { Razorpay?: RazorpayConstructor }).Razorpay;
      if (loaded) resolve(loaded);
      else reject(new Error('The payment window did not load.'));
    };
    script.onerror = () => {
      checkoutLoading = null;
      reject(new Error('Could not load the payment window. Check your connection and try again.'));
    };
    document.head.appendChild(script);
  });
  return checkoutLoading;
}

@Component({
  selector: 'app-my-fees',
  standalone: true,
  templateUrl: './my-fees.component.html',
  styleUrl: './my-fees.component.scss',
})
export class MyFeesComponent {
  private readonly auth = inject(AuthService);

  readonly rupees = rupees;
  readonly calendarDay = calendarDay;
  readonly quotaLabel = quotaLabel;
  readonly statusTone = statusTone;
  readonly statusLabels = STATUS_LABELS;
  readonly statementUrl = feeDownloadUrl('/student/fees/statement');

  readonly data = signal<MyFees | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);
  /** The fee whose payment is in progress. */
  readonly paying = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.data.set(await feeApi<MyFees>('/student/fees', {}, 'Could not load your fees'));
    } catch (e) {
      this.error.set((e as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  receiptUrl(id: string): string {
    return feeDownloadUrl(`/student/fees/${id}/receipt`);
  }

  async pay(fee: MyFee): Promise<void> {
    if (this.paying()) return;
    this.paying.set(fee.id);
    this.error.set(null);
    this.notice.set(null);
    try {
      const [order, Razorpay] = await Promise.all([
        feeApi<Order>(`/student/fees/${fee.id}/order`, { method: 'POST' }, 'Could not start the payment'),
        loadCheckout(),
      ]);
      const session = this.auth.session();
      const checkout = new Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount_paise,
        currency: order.currency,
        name: 'REEP',
        description: `${order.title} fee`,
        prefill: { name: session?.name ?? '', email: session?.email ?? '' },
        theme: { color: '#5b2a86' },
        handler: (reply: CheckoutReply) => void this.verify(fee.id, reply),
        modal: { ondismiss: () => this.paying.set(null) },
      });
      checkout.on('payment.failed', (response) => {
        this.error.set(
          `The payment did not go through${response.error?.description ? `: ${response.error.description}` : ''}. ` +
            'Nothing was charged; you can try again.',
        );
        this.paying.set(null);
      });
      checkout.open();
    } catch (e) {
      this.error.set((e as Error).message);
      this.paying.set(null);
    }
  }

  private async verify(feeId: string, reply: CheckoutReply): Promise<void> {
    try {
      await feeApi(
        `/student/fees/${feeId}/verify`,
        { method: 'POST', body: JSON.stringify(reply) },
        'Could not confirm the payment',
      );
      this.notice.set('Payment received. Your receipt is ready.');
    } catch (e) {
      this.error.set(
        `${(e as Error).message} If money left your account, the fee will show as Paid here shortly.`,
      );
    } finally {
      this.paying.set(null);
      await this.load();
    }
  }
}
