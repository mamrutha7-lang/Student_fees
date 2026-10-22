/**
 * The Main Admin's front door — `/admin`.
 *
 * Two things and nothing else: what is WAITING (five live counts, each one a
 * button to the queue it counts), and every task the console can do, as a
 * button whose label is the task in plain words. No paragraphs, no tips, no
 * "how to": a screen that needs explaining is a screen that is wrong, and this
 * one is where a person who has never seen REEP decides what to press.
 *
 * ANALYTICS USED TO BE THE LANDING. It is one row down now, at
 * /admin/analytics, with every control it had; a chart is the right first
 * screen for somebody who already knows the console and the wrong one for
 * somebody who does not.
 *
 * EVERY TILE IS GATED ON EXACTLY WHAT ITS ROUTE GUARD CHECKS — the shell's
 * rule for a navigation row, applied here for the same reason: a tile the
 * guard would bounce is a dead button that looks live. `capability` is the
 * key of `capabilityGuard`; `mainAdminOnly` is `roleGuard('ADMIN')`. The Main
 * Admin holds every `admin.*` key through the baseline, so for the one
 * account that reaches this screen the filter changes nothing today — it is
 * here so that it cannot change silently tomorrow.
 *
 * THE COUNTS ARE BEST-EFFORT. A queue that cannot be counted shows a dash and
 * stays a button: the office can still open the screen, and the screen's own
 * error state says why. Five small reads on one landing page is deliberate —
 * each is the same list the queue screen itself loads, unfiltered, so the
 * number here and the number there are the same number.
 */

import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth.service';

/** One button on the board. */
export interface TaskTile {
  /** The task, as a person would say it. Verb first. */
  readonly label: string;
  /** Material Symbols ligature — must be in tools/fonts/icon-names.txt. */
  readonly icon: string;
  readonly path: string;
  /** The key the route's `capabilityGuard` checks, when it has one. */
  readonly capability?: string;
  /** The route carries `roleGuard('ADMIN')`. */
  readonly mainAdminOnly?: boolean;
  /** Extra words the finder matches, for what the label does not say. */
  readonly words?: string;
}

export interface TaskGroup {
  readonly title: string;
  readonly tiles: readonly TaskTile[];
}

/** One live count. `read` turns the endpoint's body into the number. */
export interface QueueSpec {
  readonly key: string;
  readonly label: string;
  readonly path: string;
  readonly url: string;
  readonly read: (body: unknown) => number;
  readonly capability?: string;
  readonly mainAdminOnly?: boolean;
}

/** The count as shown: a number, `null` while loading, `'error'` when the
 *  read failed. A failed count is a dash, never a zero — zero means "nothing
 *  waiting" and that is the one thing a failed read cannot claim. */
type Count = number | null | 'error';

interface Queue extends QueueSpec {
  readonly count: ReturnType<typeof signal<Count>>;
}

const arrayLength = (body: unknown): number => (Array.isArray(body) ? body.length : 0);

/**
 * The five queues, in the order the office works them. Each URL is the list
 * the queue screen loads by default, so the count here matches the row count
 * there.
 */
export const HOME_QUEUES: readonly QueueSpec[] = [
  {
    key: 'applications',
    label: 'New student applications',
    path: '/admin/registrations',
    url: '/register/pending',
    read: arrayLength,
    capability: 'admin.registrations',
  },
  {
    key: 'leave',
    label: 'Leave requests',
    path: '/admin/leave-approvals',
    url: '/leaves/pending',
    read: arrayLength,
    capability: 'admin.leave_approvals',
  },
  {
    key: 'unassigned',
    label: 'Students without a faculty member',
    path: '/admin/mentors',
    url: '/admin/unassigned-students',
    read: arrayLength,
    capability: 'admin.mentors',
  },
  {
    key: 'offers',
    label: 'Job offers to approve',
    path: '/admin/placement',
    url: '/mentor/offers/pending',
    read: arrayLength,
    capability: 'admin.placement',
  },
  {
    key: 'access',
    label: 'Access requests to review',
    path: '/admin/governance',
    url: '/admin/governance/review',
    read: (body) => {
      const review = body as { pending?: unknown[]; expiring?: unknown[] } | null;
      return arrayLength(review?.pending) + arrayLength(review?.expiring);
    },
    mainAdminOnly: true,
  },
];

/**
 * Every screen the console has, as the task a person does there. The same
 * screens as the sidebar, in the same groups — the sidebar is the map, this is
 * the list of things to do, and a novice reads the list.
 */
export const HOME_GROUPS: readonly TaskGroup[] = [
  {
    title: 'Students',
    tiles: [
      {
        label: 'Approve new students',
        icon: 'pending_actions',
        path: '/admin/registrations',
        capability: 'admin.registrations',
        words: 'applications registrations sign up reject hold',
      },
      {
        label: 'Find a student',
        icon: 'how_to_reg',
        path: '/admin/students',
        capability: 'admin.students',
        words: 'batches roster edit promote graduate semester usn',
      },
      {
        label: 'Assign faculty to students',
        icon: 'group',
        path: '/admin/mentors',
        capability: 'admin.mentors',
        words: 'mentor mapping pair unassigned',
      },
      {
        label: 'Upload marks & attendance',
        icon: 'upload',
        path: '/admin/imports',
        capability: 'admin.imports',
        words: 'import excel spreadsheet results criteria',
      },
    ],
  },
  {
    title: 'Faculty',
    tiles: [
      {
        label: 'Add a faculty member',
        icon: 'person_add',
        path: '/admin/faculty/new',
        capability: 'admin.mentors',
        words: 'new teacher staff invite account',
      },
      {
        label: 'See all faculty',
        icon: 'shield_person',
        path: '/admin/faculty',
        capability: 'admin.mentors',
        words: 'teachers staff disable enable activation link',
      },
      {
        label: 'Approve leave',
        icon: 'event_available',
        path: '/admin/leave-approvals',
        capability: 'admin.leave_approvals',
        words: 'leave requests holiday sanction policy calendar',
      },
    ],
  },
  {
    title: 'Jobs',
    tiles: [
      {
        label: 'Post a job',
        icon: 'work',
        path: '/admin/jobs',
        capability: 'admin.jobs',
        words: 'jobs sheet company opening posting',
      },
      {
        label: 'Approve job offers',
        icon: 'verified',
        path: '/admin/placement',
        capability: 'admin.placement',
        words: 'placement placed offers funnel',
      },
    ],
  },
  {
    title: 'Interviews',
    tiles: [
      {
        label: 'Edit interview questions',
        icon: 'edit_note',
        path: '/admin/interview-questions',
        capability: 'admin.interview_questions',
        words: 'question bank tracks',
      },
      {
        label: 'See interview records',
        icon: 'mic',
        path: '/admin/interviews',
        capability: 'admin.interviews',
        words: 'mock interview transcript recording score policy',
      },
      {
        label: 'Write SWOC notes',
        icon: 'rate_review',
        path: '/admin/swoc',
        capability: 'admin.swoc',
        words: 'strengths weaknesses opportunities challenges',
      },
    ],
  },
  {
    title: 'Reports',
    tiles: [
      {
        label: 'See charts & numbers',
        icon: 'insights',
        path: '/admin/analytics',
        capability: 'admin.analytics',
        words: 'analytics placement rate kpi alerts',
      },
      {
        label: 'Download a report',
        icon: 'download',
        path: '/admin/exports',
        capability: 'admin.exports',
        words: 'export csv excel file',
      },
    ],
  },
  {
    title: 'Setup',
    tiles: [
      {
        label: 'Set up a college',
        icon: 'add_circle',
        path: '/admin/setup',
        capability: 'admin.institution',
        words: 'new college departments courses specializations batches setup wizard',
      },
      {
        label: 'Colleges',
        icon: 'apartment',
        path: '/admin/colleges',
        capability: 'admin.institution',
        words: 'add a college campus domains',
      },
      {
        label: 'Departments, courses & batches',
        icon: 'school',
        path: '/admin/institution',
        capability: 'admin.institution',
        words: 'structure specialization',
      },
      {
        label: 'Subjects & certificates',
        icon: 'menu_book',
        path: '/admin/catalogue',
        capability: 'admin.catalogue',
        words: 'catalogue badges stage rules tracks',
      },
      {
        label: 'Decide who can do what',
        icon: 'admin_panel_settings',
        path: '/admin/governance',
        mainAdminOnly: true,
        words: 'governance roles functions grants access permissions groups switches',
      },
      {
        label: 'See what changed',
        icon: 'history',
        path: '/admin/audit',
        mainAdminOnly: true,
        words: 'audit log activity history',
      },
      {
        // "the code never came" starts here. The words are the ones somebody
        // types when a student says it, not the ones this screen is called.
        label: 'Check email delivery',
        icon: 'mail',
        path: '/admin/mail',
        mainAdminOnly: true,
        words: 'mail email code link not received never arrived bounced delivery sent',
      },
      {
        label: 'Upload role preferences',
        icon: 'upload',
        path: '/admin/role-preferences',
        mainAdminOnly: true,
        words: 'role preference career goal choice form sheet xlsx',
      },
      {
        label: 'Ask REEP',
        icon: 'smart_toy',
        path: '/admin/agent',
        mainAdminOnly: true,
        words: 'agent assistant chat ai help',
      },
    ],
  },
];

@Component({
  selector: 'app-admin-home',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class AdminHomeComponent {
  private readonly auth = inject(AuthService);

  private readonly isMainAdmin = computed(() => this.auth.session()?.role === 'ADMIN');

  /** The finder's text, lower-cased and trimmed. */
  private readonly _query = signal('');
  readonly query = this._query.asReadonly();

  /** The counts, one signal each, read once when the screen opens. */
  readonly queues: readonly Queue[] = HOME_QUEUES.filter((q) => this.allowed(q)).map((q) => ({
    ...q,
    count: signal<Count>(null),
  }));

  /** The groups this session may open, narrowed by the finder. A group with
   *  no matching tile is dropped rather than drawn empty. */
  readonly groups = computed<readonly TaskGroup[]>(() => {
    const words = this.query().split(/\s+/).filter(Boolean);
    const out: TaskGroup[] = [];
    for (const group of HOME_GROUPS) {
      const tiles = group.tiles.filter((tile) => this.allowed(tile) && this.matches(tile, words));
      if (tiles.length) out.push({ title: group.title, tiles });
    }
    return out;
  });

  constructor() {
    for (const queue of this.queues) void this.count(queue);
  }

  setQuery(event: Event): void {
    this._query.set((event.target as HTMLInputElement).value.trim().toLowerCase());
  }

  /** The number as text: a dash while loading or failed, never a zero that
   *  was not counted. */
  countText(queue: Queue): string {
    const count = queue.count();
    return typeof count === 'number' ? String(count) : '—';
  }

  private allowed(item: { capability?: string; mainAdminOnly?: boolean }): boolean {
    if (item.mainAdminOnly && !this.isMainAdmin()) return false;
    if (item.capability) {
      const held = this.auth.session()?.capabilities ?? [];
      if (!held.includes(item.capability)) return false;
    }
    return true;
  }

  private matches(tile: TaskTile, words: readonly string[]): boolean {
    if (!words.length) return true;
    const haystack = `${tile.label} ${tile.words ?? ''}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  }

  private async count(queue: Queue): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}${queue.url}`, { credentials: 'include' });
      if (!res.ok) {
        queue.count.set('error');
        return;
      }
      queue.count.set(queue.read(await res.json()));
    } catch {
      queue.count.set('error');
    }
  }
}
