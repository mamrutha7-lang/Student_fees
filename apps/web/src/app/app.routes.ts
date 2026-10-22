import { Routes } from '@angular/router';

import { AppShellComponent } from './layout/app-shell.component';
import { authGuard, capabilityGuard, homeRedirectGuard, roleGuard } from './core/auth.guard';

/**
 * Every nav destination in the shell needs a route, or clicking it goes nowhere
 * and navigation reads as broken. Every route here is a built screen from the
 * design (docs/design-v4/reep-app-standalone.html); there are no placeholders
 * left to fill.
 *
 * ROUTES ARE LAZY (`loadComponent`, not `component`). A static `import` at the
 * top of this file pulls the component into the initial bundle no matter which
 * route the user visits — which is how every screen in the app ended up in one
 * 1.23 MB `main` chunk with no lazy chunks at all. A student on a phone was
 * downloading the mentor and admin UIs, plus the resume builder and the
 * assistant, before the login form could paint.
 *
 * Only the two things needed to render the first frame stay eager: the shell
 * (every authenticated route lives inside it) and the guard. Adding a screen
 * here means adding a `loadComponent` — a plain `component:` reference silently
 * un-splits the bundle again and only shows up as a budget failure later.
 */

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'register',
    loadComponent: () =>
      import('./features/register/registration.component').then((m) => m.RegistrationComponent),
  },
  // Set a password from an emailed link. One component, two modes — the API
  // is POST /auth/activate and POST /auth/reset with the same body. Outside
  // the shell: nobody on these screens has a session yet.
  {
    path: 'activate',
    data: { mode: 'activate' },
    loadComponent: () =>
      import('./features/login/password-link/password-link.component').then(
        (m) => m.PasswordLinkComponent,
      ),
  },
  {
    path: 'reset',
    data: { mode: 'reset' },
    loadComponent: () =>
      import('./features/login/password-link/password-link.component').then(
        (m) => m.PasswordLinkComponent,
      ),
  },
  // An approved student setting their account up: address -> emailed code ->
  // password. THREE STEPS, ONE URL — each step's proof is spent by the next,
  // so a route that could land on step 2 or 3 would be a route that skips one.
  // Outside the shell: nobody here has a session yet, and it ends at /login
  // rather than signing anybody in.
  {
    path: 'onboard',
    loadComponent: () =>
      import('./features/login/onboard/onboard.component').then((m) => m.OnboardComponent),
  },
  {
    path: '',
    component: AppShellComponent,
    canActivate: [authGuard],
    children: [
      // --- account (any signed-in role; the API answers 409 for Google-only) ---
      // My account (02-admin-console-spec.md §24): sign-in and security, this
      // device, the signature and the email digests, on one screen. Every role
      // reaches it from the avatar menu, so it carries no guard beyond the
      // shell's authGuard — the panels inside it hide themselves when they do
      // not apply (a Google-only account has no password to change, a student
      // has no signature).
      {
        path: 'account',
        loadComponent: () =>
          import('./features/account/account.component').then((m) => m.AccountComponent),
      },
      {
        path: 'account/password',
        loadComponent: () =>
          import('./features/account/change-password.component').then(
            (m) => m.ChangePasswordComponent,
          ),
      },
      // --- student ---
      //
      // EVERY route below carries a roleGuard, and the reason is a screen that
      // looked broken rather than forbidden. These 24 routes had `authGuard` on
      // the shell and nothing else, so any signed-in account could open any of
      // them. The API refused correctly -- `/api/student/ledger` answers 403 to
      // a MENTOR or ADMIN, and rule 2 was never in question -- but the component
      // had already rendered its header by then, so the Main Admin opening
      // /student/time-log got the title, the date picker and the Submit button
      // above the words "Could not load today's time sheet", with the six slot
      // rows and the four tiles simply absent. That reads as a blank screen and
      // a broken app, not as "this is not your screen".
      //
      // roleGuard returns the UrlTree for the session's OWN home, so the wrong
      // role is now sent somewhere that works. Two mentor routes
      // (notebook, verifications) already had exactly this guard, which is what
      // says the omission on the other 24 was an oversight and not a decision.
      //
      // Staff do NOT read student screens to see a student: they use
      // /mentor/students/{id}/... , which goes through rule 2's gate. There is
      // no case where a MENTOR legitimately opens /student/*.
      {
        path: 'student',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/home/home.component').then((m) => m.StudentHomeComponent),
      },
      {
        path: 'student/skilling',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/skilling/skilling.component').then((m) => m.SkillingComponent),
      },
      // The Time Allocation Ledger. It replaced the old free-form time log at
      // this path, and the legacy screen is now DELETED rather than parked at a
      // second route: the only thing it still showed that the ledger did not was
      // SKILLING-hours-against-the-weekly-target, and that now renders on the
      // ledger itself, beside the days it is accumulated from.
      {
        path: 'student/time-log',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/ledger/ledger.component').then((m) => m.LedgerComponent),
      },
      {
        path: 'student/courses',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/courses/courses.component').then((m) => m.CoursesComponent),
      },
      {
        path: 'student/records',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/records/records.component').then((m) => m.RecordsComponent),
      },
      {
        path: 'student/leaderboards',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/leaderboards/leaderboards.component').then(
            (m) => m.LeaderboardsComponent,
          ),
      },
      {
        path: 'student/uploads',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/uploads/uploads.component').then((m) => m.UploadsComponent),
      },
      {
        path: 'student/resume',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/resume/resume-builder.component').then(
            (m) => m.ResumeBuilderComponent,
          ),
      },
      {
        path: 'student/jobs',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/jobs/jobs.component').then((m) => m.JobsComponent),
      },
      // The REEP Agent — the design's Knowledge-Base chat, one component for
      // every role (three routes, ONE dynamic import, so the bundler emits a
      // single chunk they all reuse). It answers on programme rules and never
      // sees a student's record; the orb's "Type instead" lands here.
      {
        path: 'student/agent',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/agent/agent.component').then((m) => m.AgentComponent),
      },
      // The mock interviewer. NOT in the design's sidebar: it is the Elevate
      // stage's "Mock Interview" module on the landing (app/models/milestone.py
      // routes it here), and it stays because it is a working, deployed
      // feature the placement office asked to keep — see AGENTS.md.
      {
        path: 'student/assistant',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/assistant/assistant.component').then((m) => m.AssistantComponent),
      },
      // The durable half of the mock interviewer: past interviews, their
      // transcripts and their practice reports. Its own chunk on purpose — a
      // student who never opens it never downloads it, and it shares the report
      // card with the assistant screen, which the bundler resolves into a chunk
      // the two reuse rather than a copy in each.
      {
        path: 'student/interviews',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/interviews/interviews.component').then(
            (m) => m.InterviewsComponent,
          ),
      },
      {
        path: 'student/english',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/english/english.component').then(
            (m) => m.EnglishBaselineComponent,
          ),
      },
      {
        path: 'student/mentor-log',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/mentor-log/mentor-log.component').then(
            (m) => m.MentorLogComponent,
          ),
      },
      {
        path: 'student/profile',
        canActivate: [roleGuard('STUDENT')],
        loadComponent: () =>
          import('./features/student/profile/profile.component').then((m) => m.ProfileComponent),
      },

      // --- mentor / faculty ---
      // `/mentor` REDIRECTS; it does not render. It used to load the notebook
      // component itself, which meant two URLs for one screen — and because
      // `routerLinkActive` compares URLs, a faculty member arriving at
      // `/mentor` (which is where HOME_FOR_ROLE sends them at every sign-in)
      // saw the notebook with NOTHING highlighted in the sidebar. Measured:
      // zero active links on `/mentor`, correct highlighting on
      // `/mentor/notebook`. One screen, one URL, and the nav agrees with it.
      //
      // No `canActivate` here on purpose: Angular resolves `redirectTo` while
      // matching the URL, BEFORE guards run, so a guard on this entry would be
      // dead configuration that reads as protection. The protection is real and
      // lives on the target below — a STUDENT following this redirect meets
      // `roleGuard` there and is refused, exactly as before.
      { path: 'mentor', pathMatch: 'full', redirectTo: 'mentor/notebook' },
      {
        path: 'mentor/notebook',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/mentor/notebook/mentor-notebook.component').then(
            (m) => m.MentorNotebookComponent,
          ),
      },
      {
        path: 'mentor/mentees',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/mentor/mentee-log/mentee-log.component').then(
            (m) => m.MenteeLogComponent,
          ),
      },
      // The skill-claim review queue: the ONLY route by which a skill becomes
      // verified. Since 2026-10-16 it is reached through a grant of "Verify
      // skills & evidence" and never by mentoring, so the guard reads the
      // capability as the sidebar row does; the API refuses either way.
      {
        path: 'mentor/verifications',
        canActivate: [roleGuard('MENTOR', 'ADMIN'), capabilityGuard('mentor.verifications')],
        loadComponent: () =>
          import('./features/mentor/verifications/verifications.component').then(
            (m) => m.MentorVerificationsComponent,
          ),
      },
      {
        path: 'mentor/upskilling',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/mentor/upskilling/upskilling.component').then(
            (m) => m.UpskillingComponent,
          ),
      },
      // Signature: the staff member's own uploaded signature image, drawn into
      // the leave papers they apply on and sanction. Nothing about the leave
      // form changes; this only holds the picture.
      {
        path: 'mentor/signature',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/mentor/signature/signature.component').then(
            (m) => m.SignatureComponent,
          ),
      },
      {
        path: 'mentor/leave',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/mentor/leave/leave.component').then((m) => m.LeaveComponent),
      },
      {
        path: 'mentor/agent',
        canActivate: [roleGuard('MENTOR', 'ADMIN')],
        loadComponent: () =>
          import('./features/agent/agent.component').then((m) => m.AgentComponent),
      },
      // The old staff path pointed at the interviewer, a student feature (the
      // socket refuses non-students with 1008). Kept as a redirect.
      { path: 'mentor/assistant', redirectTo: 'mentor/agent' },

      // --- admin (the Main Admin; the console the placement office runs on) ---
      //
      // THE LANDING IS A LIST OF TASKS, NOT A CHART. `/admin` was Analytics,
      // which is the right first screen for somebody who knows the console and
      // the wrong one for somebody who does not. Home is what is waiting and
      // every task as a button; Analytics kept every control it had and moved
      // one segment down. `HOME_FOR_ROLE` and `_HOME_FOR_ROLE` still say
      // `/admin`, which is why the landing changed here and not there.
      {
        path: 'admin',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/home/home.component').then((m) => m.AdminHomeComponent),
      },
      {
        path: 'admin/analytics',
        // A capability, not a role: the Main Admin holds admin.analytics through
        // the baseline, and a MENTOR reaches this only when granted it.
        canActivate: [capabilityGuard('admin.analytics')],
        loadComponent: () =>
          import('./features/admin/analytics/analytics.component').then(
            (m) => m.AdminAnalyticsComponent,
          ),
      },
      {
        // A capability, not a role (2026-09-17): the Main Admin holds
        // admin.leave_approvals by baseline and can grant it to a faculty
        // member, who then reaches this queue under "Granted access".
        path: 'admin/leave-approvals',
        canActivate: [capabilityGuard('admin.leave_approvals')],
        loadComponent: () =>
          import('./features/admin/leave-approvals/leave-approvals.component').then(
            (m) => m.AdminLeaveApprovalsComponent,
          ),
      },
      {
        path: 'admin/registrations',
        canActivate: [capabilityGuard('admin.registrations')],
        loadComponent: () =>
          import('./features/admin/registrations/registrations.component').then(
            (m) => m.AdminRegistrationsComponent,
          ),
      },
      {
        path: 'admin/mentors',
        canActivate: [capabilityGuard('admin.mentors')],
        loadComponent: () =>
          import('./features/admin/mentors-students/mentors-students.component').then(
            (m) => m.AdminMentorsStudentsComponent,
          ),
      },
      // Colleges (02-admin-console-spec.md §3): the tenant list, the registered
      // email domains and the platform card. ONE capability since Phase 5.
      // It carried `ui.console_v2` as well — the preview switch the owner
      // reviewed the redesigned console behind — and that key is deleted, so
      // `admin.institution` is the whole question now. The sidebar row is gated
      // on the same key, which is what keeps a row that renders a row that
      // navigates.
      {
        path: 'admin/colleges',
        canActivate: [capabilityGuard('admin.institution')],
        loadComponent: () =>
          import('./features/admin/colleges/colleges.component').then(
            (m) => m.AdminCollegesComponent,
          ),
      },
      // Set up a college: the spine typed once and written in one press, on
      // the same five POSTs College structure makes. The same key as the two
      // screens it replaces the fifteen forms of.
      {
        path: 'admin/setup',
        canActivate: [capabilityGuard('admin.institution')],
        loadComponent: () =>
          import('./features/admin/college-setup/college-setup.component').then(
            (m) => m.AdminCollegeSetupComponent,
          ),
      },
      // The institution console: College -> Department -> Course ->
      // Specialization -> Batch, and seating. First and only caller of
      // /api/admin/*. Lazy like every other route (AGENTS.md: one re-eager-ed
      // route fails the bundle budget).
      {
        path: 'admin/institution',
        canActivate: [capabilityGuard('admin.institution')],
        loadComponent: () =>
          import('./features/admin/institution/institution.component').then(
            (m) => m.AdminInstitutionComponent,
          ),
      },
      // Interview Records: every mock interview with the student named and a
      // download for each recording. Lazy like the rest.
      //
      // A CAPABILITY, NOT A ROLE, SINCE B6.7. `admin.interviews` is what
      // `GET /api/admin/interviews` checks, and a route still guarded on
      // `roleGuard('ADMIN')` would mean a faculty member the Main Admin handed
      // the screen to holds the key and cannot open the door — a grant that
      // reports success in Governance and does nothing, which is the worst
      // shape of permissions bug because nothing anywhere says no.
      {
        path: 'admin/interviews',
        canActivate: [capabilityGuard('admin.interviews')],
        loadComponent: () =>
          import('./features/admin/interviews/interviews.component').then(
            (m) => m.InterviewRecordsComponent,
          ),
      },
      // Interview Questions: the admin's question bank for the free-style
      // interviewer. A capability, so it can be granted to faculty.
      {
        path: 'admin/interview-questions',
        canActivate: [capabilityGuard('admin.interview_questions')],
        loadComponent: () =>
          import('./features/admin/interview-questions/interview-questions.component').then(
            (m) => m.InterviewQuestionsComponent,
          ),
      },
      // Students: the Main Admin's roster - create, edit, remove, and act on a
      // whole batch. A capability, so it can be lent to faculty in Governance.
      {
        path: 'admin/students',
        canActivate: [capabilityGuard('admin.students')],
        loadComponent: () =>
          import('./features/admin/students/students.component').then(
            (m) => m.AdminStudentsComponent,
          ),
      },
      // The students a holder of `admin.student_records` may open (2026-10-15):
      // a faculty member given access has no roster, so this is how they find
      // the students the grant covers.
      {
        path: 'admin/student-records',
        canActivate: [capabilityGuard('admin.student_records')],
        loadComponent: () =>
          import('./features/admin/student-records/student-records.component').then(
            (m) => m.StudentRecordsComponent,
          ),
      },
      // Student 360 (§6): one student across every semester. AFTER the roster
      // route, though the order is not what separates them — both are full-path
      // matches, so `admin/students` never swallows `admin/students/5`.
      //
      // ITS OWN KEY (2026-09-17). `admin.students` is the roster EDITOR;
      // `admin.student_records` is the read of one candidate's complete
      // record, split off so the office can grant a faculty member the second
      // without the first. The Main Admin holds both by baseline. That is what
      // `RosterGridContext.canOpenDetail` mirrors — see students.component.ts —
      // and what the Mentee Log's "Full record" link reads.
      {
        path: 'admin/students/:id',
        canActivate: [capabilityGuard('admin.student_records')],
        loadComponent: () =>
          import('./features/admin/student-detail/student-detail.component').then(
            (m) => m.AdminStudentDetailComponent,
          ),
      },
      // Faculty (§7) and the four-step Add faculty wizard (§8). Today both live
      // inside /admin/mentors, which is why they share its capability: the
      // screens split, the permission did not.
      {
        path: 'admin/faculty/new',
        canActivate: [capabilityGuard('admin.mentors')],
        loadComponent: () =>
          import('./features/admin/faculty-new/faculty-new.component').then(
            (m) => m.AdminAddFacultyComponent,
          ),
      },
      {
        path: 'admin/faculty',
        canActivate: [capabilityGuard('admin.mentors')],
        loadComponent: () =>
          import('./features/admin/faculty/faculty.component').then((m) => m.AdminFacultyComponent),
      },
      // Data imports & criteria (§11): attendance and marks uploads, and the
      // placement criteria per course. `admin.imports` is the screen's own key
      // since B8.1 (Phase 4b) — it stood on `admin.institution` while the
      // endpoints did not exist, which admitted anyone who could edit a batch
      // to a screen that reads every student's marks. The key `carries_pii`,
      // so a deputy's grant of it needs a second signature (B2.4); the Main
      // Admin's is live at once.
      //
      // THE GUARD IS A FILTER AND NOT THE FENCE. `require_capability` in
      // app/routers/admin_imports.py is what refuses the request; this only
      // keeps the nav item and the URL from leading somewhere that will 403.
      // The criteria card on this screen still reads `admin.analytics` and the
      // component says so in its own refusal message — two keys on one screen
      // is deliberate: reading the gates is the analytics function, uploading a
      // roster's marks is not.
      {
        path: 'admin/imports',
        canActivate: [capabilityGuard('admin.imports')],
        loadComponent: () =>
          import('./features/admin/imports/imports.component').then((m) => m.AdminImportsComponent),
      },
      // SWOC Notes: the four lines on each student's landing. A capability, so
      // the office can lend it to faculty in Governance.
      {
        path: 'admin/swoc',
        canActivate: [capabilityGuard('admin.swoc')],
        loadComponent: () =>
          import('./features/admin/swoc/swoc.component').then((m) => m.AdminSwocComponent),
      },
      // Governance: capability grants for staff and student feature switches.
      // Its own screen rather than a tab on Institution, because the two answer
      // opposite questions — Institution says who EXISTS, Governance says who
      // may SEE. Lazy like every other route (AGENTS.md: one re-eager-ed route
      // fails the bundle budget).
      {
        path: 'admin/governance',
        // The Main Admin's alone: one account decides what faculty may see.
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/governance/governance.component').then(
            (m) => m.GovernanceComponent,
          ),
      },
      // Student feature switches (§20): the panel that lived on Governance,
      // given its own URL so the two questions stop sharing one screen — who
      // may SEE a console screen, and what a STUDENT is shown. Reached from
      // Roles & functions rather than the sidebar, which is how the boards
      // route it; the Main Admin's alone, exactly as Governance is.
      {
        path: 'admin/governance/features',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/feature-switches/feature-switches.component').then(
            (m) => m.AdminFeatureSwitchesComponent,
          ),
      },
      // Audit log (§21): every write the console made. roleGuard('ADMIN')
      // rather than a capability, for the same reason Governance carries one —
      // `admin.governance` does not exist until B2.6, and a guard naming a key
      // nobody holds refuses everyone including the office account.
      {
        path: 'admin/audit',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/audit/audit.component').then((m) => m.AdminAuditLogComponent),
      },
      // Email delivery: the mail log, and whether the provider will deliver to
      // an address at all. Main Admin alone — `mail_logs.recipient` is every
      // address the deployment has ever written to, which is a roster of
      // people by another name (see routers/admin_mail.py).
      {
        path: 'admin/role-preferences',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/role-preferences/role-preferences.component').then(
            (m) => m.AdminRolePreferencesComponent,
          ),
      },
      {
        path: 'admin/mail',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/admin/mail/mail.component').then((m) => m.AdminMailComponent),
      },
      // Courses and certifications are ONE screen: a certification only means
      // anything against the course it certifies, so they are read together.
      // Both paths resolve to it rather than leaving one a dead placeholder.
      {
        path: 'admin/catalogue',
        canActivate: [capabilityGuard('admin.catalogue')],
        loadComponent: () =>
          import('./features/admin/catalogue/catalogue.component').then(
            (m) => m.AdminCatalogueComponent,
          ),
      },
      { path: 'admin/courses', redirectTo: 'admin/catalogue' },
      { path: 'admin/certifications', redirectTo: 'admin/catalogue' },
      {
        path: 'admin/placement',
        canActivate: [capabilityGuard('admin.placement')],
        loadComponent: () =>
          import('./features/admin/placement/placement.component').then(
            (m) => m.AdminPlacementComponent,
          ),
      },
      {
        path: 'admin/jobs',
        canActivate: [capabilityGuard('admin.jobs')],
        loadComponent: () =>
          import('./features/admin/jobs-sheet/jobs-sheet.component').then(
            (m) => m.AdminJobsSheetComponent,
          ),
      },
      {
        path: 'admin/agent',
        canActivate: [roleGuard('ADMIN')],
        loadComponent: () =>
          import('./features/agent/agent.component').then((m) => m.AgentComponent),
      },
      { path: 'admin/assistant', redirectTo: 'admin/agent' },
      {
        path: 'admin/exports',
        canActivate: [capabilityGuard('admin.exports')],
        loadComponent: () =>
          import('./features/admin/exports/exports.component').then((m) => m.AdminExportsComponent),
      },

      // --- alumni ---
      {
        path: 'alumni',
        canActivate: [roleGuard('ALUMNI')],
        loadComponent: () =>
          import('./features/alumni/profile/alumni-profile.component').then(
            (m) => m.AlumniProfileComponent,
          ),
      },
      {
        path: 'alumni/jobs',
        canActivate: [roleGuard('ALUMNI')],
        loadComponent: () =>
          import('./features/alumni/jobs/alumni-jobs.component').then((m) => m.AlumniJobsComponent),
      },

      // Role-aware landing: `redirectTo: 'student'` sent every role to the
      // student home; the guard reads the resolved session and returns the
      // UrlTree for that role's own home instead, so it never activates.
      { path: '', pathMatch: 'full', canActivate: [homeRedirectGuard], children: [] },
    ],
  },
  { path: '**', redirectTo: '' },
];
