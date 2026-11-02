/** The shapes `routers/admin_fees.py` answers with. */

export interface Option {
  value: string;
  label: string;
}

export interface CourseOption extends Option {
  department_id: string;
}

export interface FeeOptions {
  current_academic_year: string;
  academic_years: string[];
  fee_heads: Option[];
  quotas: Option[];
  payment_methods: Option[];
  departments: Option[];
  courses: CourseOption[];
}

export interface RosterRow {
  student_id: string;
  usn: string | null;
  name: string;
  course_name: string | null;
  quota: string | null;
  academic_year: string;
  amount_rupees: number;
  fee_count: number;
  unpaid_count: number;
  raised_on: string | null;
  next_due_on: string | null;
  status: string;
}

export interface RosterTotals {
  students: number;
  students_with_fee: number;
  pending_rupees: number;
  paid_rupees: number;
  payments_recorded: number;
  overdue_students: number;
}

export interface Roster {
  academic_year: string;
  totals: RosterTotals;
  rows: RosterRow[];
}

export interface StudentLookup {
  student_id: string;
  usn: string | null;
  name: string;
  course_name: string | null;
  department_name: string | null;
  batch_label: string | null;
  quota: string | null;
  current_semester: number | null;
}

export interface StudentSearch {
  total: number;
  rows: StudentLookup[];
}

export interface FeeCharge {
  id: string;
  demand_title: string;
  amount_rupees: number;
  status: string;
  display_status: string | null;
  fee_head: string | null;
  academic_year: string | null;
  due_on: string | null;
  paid_at: string | null;
  payment_method: string | null;
  payment_reference: string | null;
  receipt_number: string | null;
}

export interface FeeStructureRow {
  id: string;
  academic_year: string;
  quota: string;
  fee_head: string;
  fee_head_label: string;
  amount_rupees: number;
  updated_at: string;
}

export interface RemindersResult {
  sent: number;
  already_reminded_today: number;
  failed: number;
  nothing_due: number;
  no_address: number;
  delivered_by: string;
}
