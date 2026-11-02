/**
 * Shared by the fee screens: the office's Student fees board
 * (features/admin/fees) and the student's My fees (features/student/fees).
 *
 * The API speaks whole rupees and the rules live server-side
 * (apps/api-py/app/fees.py); this file only formats and fetches.
 */

import { environment } from '../../environments/environment';

export type Quota = 'CET' | 'MANAGEMENT';
export type FeeHead = 'TUITION' | 'EXAMINATION' | 'LIBRARY_LAB' | 'OTHER';
/** What a chip says. OVERDUE is derived by the server from the due date. */
export type FeeStatus = 'NOT_RAISED' | 'PENDING' | 'OVERDUE' | 'PAID' | 'CANCELLED';

export const QUOTA_LABELS: Record<string, string> = { CET: 'CET', MANAGEMENT: 'Management' };

export const STATUS_LABELS: Record<string, string> = {
  NOT_RAISED: 'Not raised',
  PENDING: 'Pending',
  OVERDUE: 'Overdue',
  PAID: 'Paid',
  CANCELLED: 'Cancelled',
};

/** The chip colour for a status. Text and colour always travel together. */
export function statusTone(status: string | null | undefined): 'good' | 'warn' | 'risk' | 'neutral' {
  switch (status) {
    case 'PAID':
      return 'good';
    case 'PENDING':
      return 'warn';
    case 'OVERDUE':
      return 'risk';
    default:
      return 'neutral';
  }
}

export function quotaLabel(quota: string | null | undefined): string {
  return quota ? (QUOTA_LABELS[quota] ?? quota) : 'Not recorded';
}

/** 150000 -> "₹1,50,000" (Indian grouping). */
export function rupees(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return '—';
  return `₹${amount.toLocaleString('en-IN')}`;
}

/** "2026-10-29" -> "29 Oct 2026", read as a calendar day (never shifted by a zone). */
export function calendarDay(value: string | null | undefined): string {
  if (!value) return '—';
  const [y, m, d] = value.slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return value;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${d} ${months[m - 1]} ${y}`;
}

/** Two letters for the avatar: "Priya S" -> "PS". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? '?').slice(0, 2);
  return letters.toUpperCase();
}

/** The server's sentence for a refusal, or a fallback that names the status.
 *  A 422 from FastAPI carries a LIST of problems, which is joined. */
async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((d: { msg?: string }) => (d?.msg ?? '').replace(/^Value error, /, ''))
        .filter(Boolean)
        .join(' ');
    }
  } catch {
    /* not JSON */
  }
  return `${fallback} (${res.status})`;
}

/** A JSON call to the API. Throws an Error whose message is the screen's sentence. */
export async function feeApi<T>(path: string, init: RequestInit = {}, fallback = 'Something went wrong'): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${environment.apiBase}${path}`, {
      credentials: 'include',
      ...init,
      headers: init.body ? { 'Content-Type': 'application/json', ...(init.headers ?? {}) } : init.headers,
    });
  } catch {
    throw new Error('Could not reach the server. Check your connection and try again.');
  }
  if (!res.ok) throw new Error(await detailOf(res, fallback));
  return (await res.json()) as T;
}

/** The full URL of a download (a PDF or a CSV), for an `<a href download>`. */
export function feeDownloadUrl(path: string): string {
  return `${environment.apiBase}${path}`;
}
