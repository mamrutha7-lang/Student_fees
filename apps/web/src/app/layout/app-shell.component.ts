/**
 * The authenticated frame every role screen sits in — the 2026-09 shell.
 *
 * A 52px app bar, a 220px sidebar of grouped pill items, and a scrolling main
 * area the child route renders into. The navigation switches on the session's
 * role: students get the student items with their identity card on top, staff
 * get the faculty pages, the Main Admin gets the console in plain words (Home
 * first), alumni get profile and jobs.
 *
 * Every visual token lives globally in src/styles/reep-v2.scss — AGENTS.md's
 * rule is that the design system is global CSS classes and a component does not
 * redefine them. This file owns only the frame's behaviour: who is signed in,
 * which links they get, and the account menu.
 *
 * THE NAVIGATION IS DATA, NOT MARKUP. It was five hand-written blocks of
 * anchors; the console's six groups would have made it six. One model, one
 * loop, and the two things that are easy to get wrong become properties you
 * can see: whether an item needs a capability, and whether its screen exists
 * yet.
 *
 * A SCREEN THAT IS NOT BUILT IS NOT A LINK. Angular's wildcard sends an
 * unmatched path through homeRedirectGuard back to /admin, so a `routerLink`
 * to a screen that does not exist would look live, do nothing, and read as a
 * broken console. An item with `path: null` renders instead as a disabled row
 * carrying the phase it arrives in — the same rule 06-phase-prompts.md sets
 * for Phase 2's plan-driven controls: never a dead control that looks live.
 * Phase 2 builds every admin screen on the boards, so no row is pending today;
 * the mechanism stays because the next unbuilt screen must not become a
 * routerLink to nowhere.
 */

import { Component, computed, effect, inject, signal } from '@angular/core';
import { UpperCasePipe } from '@angular/common';
import { Title } from '@angular/platform-browser';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { environment } from '../../environments/environment';
import { AuthService } from '../core/auth.service';
import type { Role } from '../core/session';
import { AgentDockComponent } from './agent-dock.component';
import { AgentOrbComponent } from './agent-orb.component';

/** One row in the sidebar. */
export interface NavigationItem {
  /** What the person reads. The product's vocabulary, not the schema's. */
  readonly label: string;
  /** Material Symbols ligature. Every glyph here must be in the subset
   *  (tools/fonts/icon-names.txt) or it renders as nothing at all. */
  readonly icon: string;
  /** The route, or null when the screen has not been built yet. */
  readonly path: string | null;
  /** Only render the row when the session holds this capability — or, given
   *  several, ALL of them.
   *
   *  The ARRAY form has no user left: every row now names one key, because
   *  Phase 5 deleted `ui.console_v2`, the preview switch the redesigned screens
   *  were paired with. It is kept because the rule it encodes is the one that
   *  matters — a row must be gated on exactly what its route guard checks, all
   *  of it. Gated on less, the row is a link the guard bounces; that is the
   *  dead link this model exists to make visible rather than easy. */
  readonly capability?: string | readonly string[];
  /** Only render the row for the Main Admin, whatever their capabilities. */
  readonly mainAdminOnly?: boolean;
  /** Shown on a row whose screen does not exist yet. */
  readonly arrivesIn?: string;
  /** Match the route exactly, for a path that prefixes its siblings. */
  readonly exact?: boolean;
}

/** A titled run of rows. A blank title renders the rows with no heading. */
export interface NavigationGroup {
  readonly title: string;
  readonly items: readonly NavigationItem[];
}

/*
 * THE PREVIEW SWITCH IS GONE (Phase 5). `CONSOLE_V2`, `CONSOLE_V2_INSTITUTION`,
 * `CONSOLE_V2_IMPORTS` and `CONSOLE_V2_MENTORS` stood here: `ui.console_v2` was
 * a capability rather than an environment flag because the owner reviewed the
 * redesigned console on the production deployment, where there is no flag to set
 * and no second build to serve, and it sat in the Main Admin's baseline so the
 * office saw the new screens on deploy while faculty kept the ones they knew.
 *
 * That review is over and the redesigned screens ARE the console, so each of
 * those rows is gated on the screen's own key alone — the same single key its
 * route guard in app.routes.ts checks. Data imports keeps `admin.imports`
 * rather than `admin.institution` (B8.1, Phase 4b): the screen reads every
 * student's marks, so it `carries_pii` and is granted on its own.
 */

/**
 * The Main Admin console, IN THE WORDS THE OFFICE USES.
 *
 * The boards (docs/redesign-2026-09/01-design-system.md §3) grouped these as
 * Overview · Institution · Operations · Student insight · Governance · Tools,
 * and named the rows "Mentor mapping", "Roles & functions", "Exports". Those
 * are this codebase's words. The person at the desk is not an engineer, and a
 * label they have to decode is a screen they do not open — so every row is
 * the thing itself, in plain words, and every group is a noun a first-day
 * clerk knows. Same eighteen screens as before, plus Home and Placement:
 * Placement was reachable only from the Jobs sheet's buttons, which for the
 * one account that decides offers is a screen hidden behind another one.
 *
 * Nothing here explains anything. A row is a name and a destination; if the
 * name needs a tooltip, the name is wrong.
 */
export const ADMIN_NAVIGATION: readonly NavigationGroup[] = [
  {
    title: '',
    items: [
      { label: 'Home', icon: 'home', path: '/admin', exact: true, mainAdminOnly: true },
      {
        label: 'Charts & numbers',
        icon: 'insights',
        path: '/admin/analytics',
        capability: 'admin.analytics',
      },
    ],
  },
  {
    title: 'People',
    items: [
      {
        label: 'New applications',
        icon: 'pending_actions',
        path: '/admin/registrations',
        capability: 'admin.registrations',
      },
      {
        label: 'Students & batches',
        icon: 'how_to_reg',
        path: '/admin/students',
        capability: 'admin.students',
      },
      {
        label: 'Faculty',
        icon: 'shield_person',
        path: '/admin/faculty',
        capability: 'admin.mentors',
      },
      {
        label: 'Assign faculty',
        icon: 'group',
        path: '/admin/mentors',
        capability: 'admin.mentors',
      },
    ],
  },
  {
    title: 'Every day',
    items: [
      {
        label: 'Leave requests',
        icon: 'event_available',
        path: '/admin/leave-approvals',
        capability: 'admin.leave_approvals',
      },
      {
        label: 'Upload spreadsheets',
        icon: 'upload',
        path: '/admin/imports',
        capability: 'admin.imports',
      },
      { label: 'Job postings', icon: 'work', path: '/admin/jobs', capability: 'admin.jobs' },
      {
        label: 'Placement & offers',
        icon: 'verified',
        path: '/admin/placement',
        capability: 'admin.placement',
      },
      {
        label: 'Download reports',
        icon: 'download',
        path: '/admin/exports',
        capability: 'admin.exports',
      },
    ],
  },
  {
    title: 'Interviews',
    items: [
      {
        label: 'Interview questions',
        icon: 'edit_note',
        path: '/admin/interview-questions',
        capability: 'admin.interview_questions',
      },
      {
        label: 'Interview records',
        icon: 'mic',
        path: '/admin/interviews',
        capability: 'admin.interviews',
      },
      { label: 'SWOC notes', icon: 'rate_review', path: '/admin/swoc', capability: 'admin.swoc' },
    ],
  },
  {
    title: 'College setup',
    items: [
      {
        label: 'Set up a college',
        icon: 'add_circle',
        path: '/admin/setup',
        capability: 'admin.institution',
      },
      {
        label: 'Colleges',
        icon: 'apartment',
        path: '/admin/colleges',
        capability: 'admin.institution',
      },
      {
        label: 'College structure',
        icon: 'school',
        path: '/admin/institution',
        capability: 'admin.institution',
      },
      {
        label: 'Catalogue',
        icon: 'menu_book',
        path: '/admin/catalogue',
        capability: 'admin.catalogue',
      },
    ],
  },
  {
    title: 'Settings',
    items: [
      {
        label: 'Who can do what',
        icon: 'admin_panel_settings',
        path: '/admin/governance',
        mainAdminOnly: true,
      },
      {
        label: 'What changed',
        icon: 'history',
        path: '/admin/audit',
        mainAdminOnly: true,
      },
      {
        label: 'Email delivery',
        icon: 'mail',
        path: '/admin/mail',
        mainAdminOnly: true,
      },
      {
        label: 'Role preferences',
        icon: 'upload',
        path: '/admin/role-preferences',
        mainAdminOnly: true,
      },
    ],
  },
];

/**
 * The student's items, unchanged from what the sidebar lists today — the
 * redesign restyles this console, it does not re-navigate it.
 *
 * Shorter than the route table on purpose: English Baseline, Courses, Records,
 * Uploads and Profile are each reached from the screen that owns the work, or
 * from the identity card. Their routes are kept, so a bookmark and a deep link
 * still resolve.
 */
const STUDENT_NAVIGATION: readonly NavigationGroup[] = [
  {
    title: '',
    items: [
      { label: 'Home', icon: 'home', path: '/student', exact: true },
      { label: 'Jobs', icon: 'work', path: '/student/jobs' },
      { label: 'Skilling', icon: 'verified', path: '/student/skilling' },
      { label: 'Leaderboards', icon: 'leaderboard', path: '/student/leaderboards' },
      { label: 'Time Sheet', icon: 'schedule', path: '/student/time-log' },
    ],
  },
  {
    title: 'Programme',
    items: [{ label: 'Faculty / TPO Log', icon: 'event_note', path: '/student/mentor-log' }],
  },
  {
    title: 'Documents',
    items: [{ label: 'Resume Builder', icon: 'description', path: '/student/resume' }],
  },
];

/**
 * Faculty items, unchanged. The redesign does not touch the faculty console
 * (02-admin-console-spec.md, "Faculty screens (no redesign)"); it inherits the
 * shell and the tokens and nothing else.
 *
 * THREE ROWS ARE CAPABILITY-FILTERED SINCE B2.3, and the comment that used to
 * sit here said why they were not: ROLE_BASELINE['MENTOR'] held every scoped
 * key, so every faculty account had them all and a filter would have hidden
 * screens people could still open. B2.3 shrank that baseline — the mentee log,
 * the notebook and evidence verification are derived from mentoring somebody
 * now (apps/api-py/app/mentor_functions.py) — so without the filter the
 * opposite would be true: a faculty account with no mentees would see three
 * links that answer 403.
 *
 * LEAVE REQUESTS IS DELIBERATELY NOT FILTERED. That screen is the faculty
 * member's OWN leave request (and the cover they are asked for), which nothing
 * gates and nobody should ever lose; approving leave is the Main Admin's alone
 * since 2026-09-16 and lives on the admin console.
 * Upskilling is baseline and always held.
 *
 * THE REEP AGENT HAS NO SIDEBAR ROW ON ANY ROLE (2026-09-16). The owner
 * asked for it to leave the navigation bar in every UI: the floating orb on
 * every screen already opens the same chat (agent-dock.component.ts), so a
 * sidebar row was the same door drawn twice. The routes stay as deep links.
 */
const FACULTY_NAVIGATION: readonly NavigationGroup[] = [
  {
    title: '',
    items: [
      {
        label: 'Notebook',
        icon: 'menu_book',
        path: '/mentor/notebook',
        capability: 'mentor.notebook',
      },
      {
        label: 'Mentee Log',
        icon: 'groups',
        path: '/mentor/mentees',
        capability: 'mentor.mentees',
      },
      // A faculty member holds `admin.student_records` by mentoring somebody
      // (2026-10-16), so the list of the records they may open is one of
      // their own rows, not "Granted access". It still needs the key: a
      // faculty member with no mentees and no grant has nothing to list.
      {
        label: 'Student records',
        icon: 'folder_open',
        path: '/admin/student-records',
        capability: 'admin.student_records',
      },
      { label: 'Leave Requests', icon: 'event_available', path: '/mentor/leave' },
      {
        label: 'Skill Verifications',
        icon: 'verified',
        path: '/mentor/verifications',
        capability: 'mentor.verifications',
      },
      { label: 'Upskilling', icon: 'workspace_premium', path: '/mentor/upskilling' },
    ],
  },
];

const ALUMNI_NAVIGATION: readonly NavigationGroup[] = [
  {
    title: '',
    items: [
      { label: 'My Profile', icon: 'person', path: '/alumni', exact: true },
      { label: 'Jobs Sheet', icon: 'work', path: '/alumni/jobs' },
    ],
  },
];

/**
 * Every admin.* capability the Main Admin can grant, and the screen it opens.
 * A faculty account that holds one sees it under "Granted access". A
 * capability with no row here is a grant that changes nothing on screen, so
 * this list and the catalogue in app/models/governance.py are kept in step.
 *
 * `admin.student_records` HAS A ROW SINCE 2026-10-15. Until then a grant of it
 * reached only the holder's own mentees, who are on their Mentee Log. The
 * office can now give it for a batch, a department or the college, and those
 * students are on no screen a faculty member has, so "Student records" lists
 * them (`/admin/student-records`).
 *
 * THE REDESIGNED SCREENS ARE HERE TOO. Left out, a faculty member granted
 * `admin.institution` would pass /admin/colleges' route guard with no row
 * anywhere offering it — reachable only by typing the URL, which is the exact
 * defect the 2026-09 browser audit found on four screens and which this list
 * exists to prevent. The Main Admin does not read this list at all; its own
 * rows are in ADMIN_NAVIGATION.
 */
export const GRANTABLE_ADMIN_SCREENS: readonly (NavigationItem & {
  capability: string | readonly string[];
})[] = [
  {
    capability: 'admin.analytics',
    path: '/admin/analytics',
    label: 'Charts & numbers',
    icon: 'insights',
  },
  {
    capability: 'admin.registrations',
    path: '/admin/registrations',
    label: 'New applications',
    icon: 'pending_actions',
  },
  { capability: 'admin.mentors', path: '/admin/mentors', label: 'Assign faculty', icon: 'group' },
  // Leave approval became grantable on 2026-09-17: a faculty member the office
  // hands the queue to signs as a delegate on the same screen the office uses.
  {
    capability: 'admin.leave_approvals',
    path: '/admin/leave-approvals',
    label: 'Leave requests',
    icon: 'event_available',
  },
  // Interview records were grantable (admin.interviews) before this row was:
  // a granted faculty member passed the route guard with no row anywhere
  // offering the screen, which is the defect this list exists to prevent.
  // The grid is still rule 2's -- a faculty member sees their own mentees'
  // interviews -- and "Download recording" on it needs admin.interview_audio
  // as well, granted separately and on purpose.
  {
    capability: 'admin.interviews',
    path: '/admin/interviews',
    label: 'Interview records',
    icon: 'mic',
  },
  {
    capability: 'admin.students',
    path: '/admin/students',
    label: 'Students & batches',
    icon: 'how_to_reg',
  },
  {
    capability: 'admin.student_records',
    path: '/admin/student-records',
    label: 'Student records',
    icon: 'folder_open',
  },
  {
    capability: 'admin.institution',
    path: '/admin/institution',
    label: 'College structure',
    icon: 'school',
  },
  {
    capability: 'admin.catalogue',
    path: '/admin/catalogue',
    label: 'Catalogue',
    icon: 'menu_book',
  },
  { capability: 'admin.jobs', path: '/admin/jobs', label: 'Job postings', icon: 'work' },
  {
    capability: 'admin.placement',
    path: '/admin/placement',
    label: 'Placement & offers',
    icon: 'verified',
  },
  {
    capability: 'admin.interview_questions',
    path: '/admin/interview-questions',
    label: 'Interview questions',
    icon: 'edit_note',
  },
  { capability: 'admin.swoc', path: '/admin/swoc', label: 'SWOC notes', icon: 'rate_review' },
  {
    capability: 'admin.exports',
    path: '/admin/exports',
    label: 'Download reports',
    icon: 'download',
  },

  // The 2026-09 console's screens. Each names the screen's own key, exactly as
  // its route guard does, so a row that renders is a row that navigates. Each
  // paired that key with `ui.console_v2` until Phase 5 deleted the switch.
  {
    capability: 'admin.institution',
    path: '/admin/colleges',
    label: 'Colleges',
    icon: 'apartment',
  },
  {
    capability: 'admin.imports',
    path: '/admin/imports',
    label: 'Upload spreadsheets',
    icon: 'upload',
  },
  {
    capability: 'admin.mentors',
    path: '/admin/faculty',
    label: 'Faculty',
    icon: 'shield_person',
  },
];

/**
 * The vocabulary the college uses: a MENTOR-role account is a faculty member,
 * and the one ADMIN is the Main Admin. There is no DIRECTOR — see
 * core/session.ts.
 */
const ROLE_LABEL: Record<Role, string> = {
  STUDENT: 'Student',
  MENTOR: 'Faculty',
  ADMIN: 'Main Admin',
  ALUMNI: 'Alumni',
};

/** The name of the workspace, shown beside the brand mark. */
const CONSOLE_NAME: Record<Role, string> = {
  STUDENT: 'Student',
  MENTOR: 'Faculty console',
  ADMIN: 'Admin console',
  ALUMNI: 'Alumni',
};

@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
    UpperCasePipe,
    AgentOrbComponent,
    AgentDockComponent,
  ],
  templateUrl: './app-shell.component.html',
  styleUrl: './app-shell.component.scss',
})
export class AppShellComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly title = inject(Title);

  readonly session = this.auth.session;
  readonly roleLabel = computed(() => ROLE_LABEL[this.session()?.role as Role] ?? 'Student');
  readonly consoleName = computed(() => CONSOLE_NAME[this.session()?.role as Role] ?? 'Student');

  /** The one account that may open Governance (routers/governance.py is
   *  ADMIN-only, and the route carries roleGuard('ADMIN')). */
  readonly isMainAdmin = computed(() => this.session()?.role === 'ADMIN');

  /** Which navigation set to render. STUDENT is the fallback while /auth/me is
   *  in flight — the guard has already verified a session exists, so this only
   *  decides which links paint first. */
  readonly navKind = computed<'student' | 'staff' | 'admin' | 'alumni'>(() => {
    const role = this.session()?.role;
    // Admin is its own set, not staff-plus-extras: the office account was
    // getting the faculty navigation, so every screen built for it was routed
    // and unreachable.
    if (role === 'ADMIN') return 'admin';
    if (role === 'MENTOR') return 'staff';
    if (role === 'ALUMNI') return 'alumni';
    return 'student';
  });

  /** The groups this session's sidebar shows, already filtered. */
  readonly navigation = computed<readonly NavigationGroup[]>(() => {
    const kind = this.navKind();
    const source =
      kind === 'admin'
        ? ADMIN_NAVIGATION
        : kind === 'staff'
          ? FACULTY_NAVIGATION
          : kind === 'alumni'
            ? ALUMNI_NAVIGATION
            : STUDENT_NAVIGATION;

    const held = this.session()?.capabilities ?? [];
    const isMainAdmin = this.isMainAdmin();
    const groups: NavigationGroup[] = [];

    for (const group of source) {
      const items = group.items.filter((item) => {
        if (item.mainAdminOnly && !isMainAdmin) return false;
        if (item.capability) {
          const needed = typeof item.capability === 'string' ? [item.capability] : item.capability;
          if (!needed.every((key) => held.includes(key))) return false;
        }
        return true;
      });
      if (items.length) groups.push({ title: group.title, items });
    }

    const granted = this.grantedAdminScreens();
    if (granted.length) groups.push({ title: 'Granted access', items: granted });
    return groups;
  });

  /**
   * Admin screens a FACULTY session has been granted. Only screens whose API
   * actually checks the capability belong here — a link to a screen whose API
   * still answers 403 by role is worse than no link.
   */
  private readonly grantedAdminScreens = computed<readonly NavigationItem[]>(() => {
    if (this.navKind() !== 'staff') return [];
    const held = this.session()?.capabilities ?? [];
    // A screen already among the faculty's own rows (Student records) is not
    // drawn twice.
    const own = new Set(FACULTY_NAVIGATION.flatMap((group) => group.items.map((item) => item.path)));
    return GRANTABLE_ADMIN_SCREENS.filter((screen) => {
      if (own.has(screen.path)) return false;
      const needed =
        typeof screen.capability === 'string' ? [screen.capability] : screen.capability;
      return needed.every((key) => held.includes(key));
    });
  });

  /**
   * The signed-in person's name, or a neutral placeholder while /auth/me is in
   * flight. Never a hardcoded demo name — shipping one would show every
   * student someone else's name for as long as the session takes to resolve.
   */
  readonly displayName = computed(() => this.session()?.name?.trim() || 'Signed in');

  /** Up to two initials for the avatar, from the name the session carries. */
  readonly initials = computed(() => {
    const words = this.displayName()
      .split(/\s+/)
      .filter((word) => /[a-z]/i.test(word));
    if (!words.length) return '··';
    const first = words[0][0];
    const last = words.length > 1 ? words[words.length - 1][0] : '';
    return (first + last).toUpperCase();
  });

  /**
   * The environment this bundle was built for, for the admin bar's pill.
   * deploy.yml rewrites `sentryEnvironment` to the API's ENV at build time, so
   * this says `prod` on production and `development` on a laptop. It is shown
   * only to the Main Admin, who is the one person for whom "which deployment
   * am I looking at" is a real question.
   */
  readonly environmentLabel = computed(() => environment.sentryEnvironment.trim());

  /**
   * The USN under the sidebar name.
   *
   * Fetched rather than read off the session on purpose: the session's claims
   * are a fixed contract (see ProfileOut's note in routers/student.py) and a
   * sidebar is not a reason to widen a signed cookie. One request, only for a
   * STUDENT, and a failure is silent — the card simply shows the name.
   */
  private readonly _usn = signal<string | null>(null);
  readonly usn = this._usn.asReadonly();

  /** The account menu behind the avatar. */
  private readonly _accountMenuOpen = signal(false);
  readonly accountMenuOpen = this._accountMenuOpen.asReadonly();

  /**
   * The sidebar's drawer state — meaningful only below 900px, where
   * reep-v2.scss takes `.desktop-nav` out of the flow and parks it off-screen.
   * Above that the sidebar is always on screen, the hamburger and the scrim
   * are `display: none`, and this stays false and costs nothing.
   *
   * ONE `<nav>`, NOT TWO. The drawer renders the same element with the same
   * `navigation()` groups; a second mobile navigation would be a second list
   * to keep in step with ADMIN_NAVIGATION and its three siblings, which is
   * exactly what "the navigation is DATA, not markup" above exists to avoid.
   */
  private readonly _navOpen = signal(false);
  readonly navOpen = this._navOpen.asReadonly();

  constructor() {
    if (this.session()?.role === 'STUDENT') void this.loadUsn();

    // The browser tab said "REEP · Student" on every screen for every role,
    // because index.html hardcodes it and nothing ever set it again. A Main
    // Admin with six REEP tabs open could not tell them apart. `effect`, not a
    // one-shot read: session() resolves asynchronously from /auth/me.
    effect(() => {
      this.title.setTitle(`REEP · ${this.roleLabel()}`);
    });
  }

  private async loadUsn(): Promise<void> {
    try {
      const res = await fetch(`${environment.apiBase}/student/profile`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const body = (await res.json()) as { usn?: string | null };
      this._usn.set(body.usn?.trim() || null);
    } catch {
      // The sidebar is not worth an error state. Name only.
    }
  }

  toggleNav(): void {
    this._navOpen.update((open) => !open);
  }

  /**
   * Bound on the scrim, on Escape, and on every nav link — the link case being
   * the one that matters: on a phone the destination is BEHIND the drawer, so
   * a drawer left standing reads as a link that did nothing. Above 900px it is
   * already false and every one of those is a no-op.
   */
  closeNav(): void {
    this._navOpen.set(false);
  }

  toggleAccountMenu(): void {
    this._accountMenuOpen.update((open) => !open);
  }

  closeAccountMenu(): void {
    this._accountMenuOpen.set(false);
  }

  async signOut(): Promise<void> {
    this.closeAccountMenu();
    await this.auth.logout();
    await this.router.navigate(['/login']);
  }
}
