"""Empty this deployment of PEOPLE: ``python -m app.purge_people``.

Everyone except the Main Admin, everything they did, and every recording of
them — while the institution they belong to (colleges, departments, courses,
specializations, batches) and the content the office maintains (job postings,
catalogues, the assistant's Knowledge Base) are left standing.

It exists because there is no other way to do this. `app.grant_access` creates
and updates accounts and cannot remove one; the Main Admin console deletes
students one batch at a time and has no screen at all for a faculty
account; and the Ops task menu is deliberately a FIXED LIST, because a
free-text command input there is remote code execution on the production
cluster. So a purge that has to happen on production has to be a named task on
that menu, written down, reviewed, and tested — which is this module.

WHAT IT IS FOR: handing a deployment over for real intake after it has been
demonstrated. Seeded logins, test students, voice-test leftovers, every
transcript and every recording made while proving the thing works — gone, in
one audited pass, without touching the institutional shape somebody spent an
afternoon typing in.

**Three properties make it safe to point at production.**

FIRST, EVERY TABLE HAS A WRITTEN VERDICT. `VERDICTS` below names all of them.
A table in the metadata that nobody classified ABORTS THE RUN — it is not
quietly kept (which would leave a student's records behind) and not quietly
emptied (which would destroy a catalogue somebody added last week). The next
person to add a table is made to decide, by a test that fails in CI and by this
module refusing to run. That is the whole reason the verdicts are a dict of 114
entries — one per table, `len(VERDICTS)` — rather than a pair of prefixes and a
`startswith`. (114 is the table count; the 188 below is the foreign-key count.
They were both written as "93" for months, which is how a stale number spreads.)

SECOND, THE FILES GO BEFORE THE ROWS. A row is the last pointer to a student's
resume, a faculty member's signature and a named student's recorded voice.
Delete the row first and a failed file delete leaves bytes on the volume that
nobody can find again — the one outcome that cannot be repaired afterwards.
This is `retention._delete_interview_audio`'s reasoning, applied to every other
store in `FILE_COLUMNS` (six of them since B10.3's leave attachments), and it is
why `_destroy_files` runs first and why anything it could not destroy is
reported rather than swallowed.

THE VOICE PLATFORM'S STEREO RECORDING IS THE FOURTH STORE, and it was missed for
as long as it has existed. `voice_platform/api/call_close.py` writes a
dual-channel WAV of a named student's interview to `platform/<call id>.wav` on
the same volume — a path `interview_audio.track_path` can never produce, so the
per-speaker sweep walked past it — while this module deleted the
`platform_call_sessions` row that was the file's only pointer. That is the exact
outcome the paragraph above forbids, produced by the paragraph above being
applied to three stores out of four: after a purge the volume held a complete
recording of a student's voice and nothing left in the system could say whose.
`destroy_platform_call_audio` closes it, from the ids read before the rows go.

THIRD, IT REFUSES TO LEAVE NOBODY BEHIND. The survivor is found by role, and
the run aborts unless there is EXACTLY ONE ADMIN. Zero means this deployment
has no Main Admin and the purge would lock every human out of the console
permanently. More than one contradicts the invariant `app.grant_access`
enforces at the other end, and picking one of them here would be this module
guessing which colleague keeps their job. Both are an operator's problem to fix
before the destruction, not during it.

DRY RUN IS THE DEFAULT. `--apply` is the only thing that deletes, and the
counts printed by a dry run are the same counts the real pass acts on. Read
them. `--apply` also demands `--i-understand-this-is-permanent`, because the
one thing a menu cannot express is that the operator meant it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, inspect as sa_inspect, select, table as sa_table, update
from sqlalchemy.orm import Session

from .db import Base, SessionLocal
from .models import user as user_model

# Importing the package registers every model on Base.metadata. Without it the
# verdict check below would pass against a HALF-POPULATED metadata and the
# purge would skip real tables while reporting success.
from . import models  # noqa: F401

log = logging.getLogger("reep.purge")

KEEP = "keep"
EMPTY = "empty"
SURVIVOR = "survivor"  # `users`: every row but the Main Admin's

#: Every table in the schema, and what happens to it. See the module docstring:
#: a table missing from here stops the run. Grouped by the reason, because the
#: reason is the part a reviewer has to check and a table name is not one.
VERDICTS: dict[str, str] = {
    # -- the institution, which is the point of keeping anything ------------
    # Somebody typed this in. It names no person and outlives every intake.
    "colleges": KEEP,
    "departments": KEEP,
    "academic_courses": KEEP,
    "academic_specializations": KEEP,
    "cohorts": KEEP,
    # -- the archive's index, which BOTH destructors must leave standing ----
    #
    # KEEP, and this is the one entry whose reasoning runs against the grain of
    # this module. `archived_documents` names files belonging to people this
    # run is deleting, so the obvious verdict is EMPTY -- and EMPTY would
    # destroy the only thing that can ever say whose file a given object in the
    # permanent archive was. The bytes survive in an Object-Locked bucket no
    # destructor can reach; emptying the index leaves a bucket of uuids and no
    # way to answer "which of these was this student's marksheet", which is
    # `retention._delete_interview_audio`'s "a delete that loses the pointer
    # first leaves bytes nobody can find" arriving the other way round.
    #
    # It is also why a `deleted_at` flag on the six document tables was NOT the
    # answer here: a flag on a row in a table this module EMPTIES protects
    # nothing at all. See models/archived_document.py.
    #
    # What it costs, said plainly: after `purge_people`, this table still holds
    # a file name, a file size, a document title and a bare account id for
    # every person the run removed. It holds no marks, no attendance, no USN,
    # no address and no contents. That is a deliberate trade and the reason the
    # table carries nothing more.
    "archived_documents": KEEP,
    # -- catalogues and configuration the office maintains ------------------
    "approved_certifications": KEEP,
    "certifications": KEEP,
    "courses": KEEP,
    "skills": KEEP,
    "jobs": KEEP,
    # Student fees (2026-10-29). The fee STRUCTURE is the office's price list
    # and names nobody, so it stays; every demand and charge is a money claim
    # on a person, so both go with the people.
    "fee_structures": KEEP,
    "fee_demands": EMPTY,
    "fee_charges": EMPTY,
    "interview_bank_questions": KEEP,
    # B5.1 made the Specialization Matrix a table an admin edits, and B6.1 made
    # the interview's storage and caps a college's decision. Both are the
    # office's own configuration and name nobody: a persona, a framework list, a
    # syllabus, a retention number. They sit with `interview_bank_questions`
    # above because they are the same kind of thing — staff-authored text the
    # next intake is interviewed against.
    "interview_tracks": KEEP,
    "interview_policies": KEEP,
    # B13's per-course catalogue. Neither table names a person: one says which
    # of the 48 code-defined badges apply to a programme, the other which REEP
    # stage a semester of it sits in. Both are the office's own configuration
    # and outlive every intake, exactly like `approved_certifications` above.
    "badge_course_map": KEEP,
    "stage_rules": KEEP,
    # B10.2's calendar. It names NOBODY: a date, a word ("holiday" / "working")
    # and a label somebody in the office typed once for the whole college. It
    # sits with the catalogues for exactly the reason they are here — the next
    # intake's leave is counted against these same days, and emptying it would
    # silently change the arithmetic on every request made afterwards. (The
    # BALANCES are per-person and are emptied below; the two tables live in one
    # module and get opposite verdicts, which is the distinction worth checking.)
    "academic_calendar": KEEP,
    "placement_criteria": KEEP,
    "registration_rules": KEEP,
    "alert_rule_configs": KEEP,
    "feature_overrides": KEEP,  # config, not a grant; emptying it would silently change behaviour
    "access_groups": KEEP,  # the shape of the governance groups; their members go
    # -- the voice platform's catalogue (its PEOPLE are below) --------------
    "platform_specializations": KEEP,
    "platform_questions": KEEP,
    "platform_time_limits": KEEP,
    "platform_recording_policies": KEEP,
    # -- the Knowledge Base: approved public policy text, no student in it --
    "knowledge_documents": KEEP,
    "knowledge_chunks": KEEP,
    "redesign_knowledge_namespaces": KEEP,
    "redesign_knowledge_document_versions": KEEP,
    "redesign_knowledge_chunks": KEEP,
    "redesign_knowledge_chunk_embeddings": KEEP,
    "redesign_embedding_models": KEEP,
    "redesign_tenants": KEEP,
    # -- the accounts -------------------------------------------------------
    "users": SURVIVOR,
    "students": EMPTY,
    "mentors": EMPTY,
    "alumni_profiles": EMPTY,
    "platform_candidates": EMPTY,
    "redesign_tenant_memberships": EMPTY,
    "access_group_members": EMPTY,
    "capability_grants": EMPTY,  # nobody is left to hold a granted screen
    # -- credentials and sign-in trail --------------------------------------
    "auth_tokens": EMPTY,
    "login_days": EMPTY,
    "email_verifications": EMPTY,
    # -- a student's own records --------------------------------------------
    "student_profiles": EMPTY,
    # The record of a promotion or a graduation. It is BOTH a fact about a
    # student and the office's record of an academic act, and here the two
    # readings agree: this module empties the deployment of people, so the
    # student it names is going and the staff member who decided is going too.
    # (`by_user_id` is SET NULL, so the act would survive the actor; the row
    # does not survive its subject, because without them it records nothing.)
    "student_semester_history": EMPTY,
    # B9.1's assignment history, and it is the SAME QUESTION `student_semester_history`
    # above answered, decided the same way. A row is both a fact about a student
    # and the office's record of a staff decision — who seated them, who moved
    # them off, and why. Here the two readings agree, because this module empties
    # the deployment of PEOPLE: the student the row names is going and the staff
    # member who decided is going too. (`by_user_id`/`ended_by_user_id` are SET
    # NULL, so the decision would outlive the decider; the row does not outlive
    # its subject, because without the student it records nothing.) The office's
    # record that an assignment happened survives in `redesign_audit_events`,
    # which is KEPT — the same place the promotion above leaves its act.
    "mentor_assignments": EMPTY,
    "student_skills": EMPTY,
    "student_badges": EMPTY,
    "student_milestones": EMPTY,
    "badge_evidence": EMPTY,
    "skill_claims": EMPTY,
    "capability_assessments": EMPTY,
    "academic_gaps": EMPTY,
    "academic_qualifications": EMPTY,
    "attendance_records": EMPTY,
    "subject_marks": EMPTY,
    "semester_results": EMPTY,
    # B8.1's imports. The RUN is staff-authored — a filename, a batch, a date
    # and who read it — and the temptation is to KEEP it as office provenance
    # the way `export_events` two groups down is kept. It goes, for a reason
    # that is about the rows and not the run: `import_rows` holds every line of
    # the spreadsheet VERBATIM — a USN, a name, marks, attendance — so a KEPT
    # import history would be a complete copy of the academic records this
    # module has just deleted, sitting in a table nobody thinks of as student
    # data. And a run left standing over an emptied deployment names a batch
    # with nobody in it and counts nobody's rows.
    #
    # The office's record that an import happened survives where the office's
    # acts belong: `redesign_audit_events`, which is KEPT.
    "import_runs": EMPTY,
    "import_rows": EMPTY,
    # B8.6's nightly roll-up. Derived entirely from the people being deleted,
    # so keeping it leaves "41 students placed" standing over a deployment with
    # no students in it — a number on the Analytics screen that is not wrong
    # about anything so much as about nobody. Regenerated by the next run.
    "analytics_snapshots": EMPTY,
    "enrollments": EMPTY,
    "lab_sessions": EMPTY,
    "schedule_items": EMPTY,
    "mock_attempts": EMPTY,
    "alerts": EMPTY,
    "certification_progress": EMPTY,
    "english_baselines": EMPTY,
    "english_baseline_sections": EMPTY,
    "time_ledger_days": EMPTY,
    "time_ledger_cells": EMPTY,
    "time_sheet_entries": EMPTY,
    "resumes": EMPTY,
    "resume_profiles": EMPTY,
    "uploads": EMPTY,
    "job_applications": EMPTY,
    "placement_offers": EMPTY,
    "registrations": EMPTY,
    "registration_documents": EMPTY,
    # -- what staff wrote about them, and staff's own shelf -----------------
    "mentor_notes": EMPTY,
    "swoc_entries": EMPTY,
    # B7.3. A revision is what one SWOC line said before somebody edited it —
    # a named judgement about a student, twice over. It cannot outlive the entry
    # (CASCADE would take it anyway) and there is nothing in it that is not
    # about a person this module is removing.
    "swoc_entry_revisions": EMPTY,
    "leave_requests": EMPTY,
    # B10.3. A medical certificate or an OOD letter belonging to one
    # application, and FILE-BACKED — see FILE_COLUMNS below, which is what makes
    # the bytes go before the row that points at them.
    "leave_attachments": EMPTY,
    # B10.2. One person's allowance for one kind of leave in one year. Keyed on
    # `users.id` because staff hold balances too, which is also why it cannot be
    # read as a catalogue: the row is about a person, and the person is going.
    # Its sibling `academic_calendar` is KEPT, up with the catalogues.
    "leave_balances": EMPTY,
    "staff_signatures": EMPTY,
    "staff_upskilling_certs": EMPTY,
    "redesign_mentor_notebook_entries": EMPTY,
    "redesign_mentor_notebook_entry_revisions": EMPTY,
    "redesign_mentor_notebook_actions": EMPTY,
    "redesign_mentor_notebook_attachments": EMPTY,
    # -- recordings and transcripts -----------------------------------------
    "interview_sessions": EMPTY,
    "interview_turns": EMPTY,
    "interview_evaluations": EMPTY,
    "interview_consents": EMPTY,
    # B6.2's summary is built to OUTLIVE the interview it summarises — the
    # retention job deletes the transcript, the audio and the scorecard on the
    # 180-day clock and deliberately never touches this table. That is about a
    # CLOCK, and it is not a reason to keep the row here: every row names a
    # student by a NOT NULL foreign key, and "61, then 68, then 74" about
    # somebody who has been removed from the deployment is the student record
    # this module exists to remove. Surviving retention and surviving a purge
    # are different questions with different answers.
    "interview_score_summaries": EMPTY,
    # An admin gave a named student their practice attempts back, and said why.
    # The student is going; the row would name nobody.
    "interview_cap_resets": EMPTY,
    "platform_call_sessions": EMPTY,
    "conversations": EMPTY,
    "messages": EMPTY,
    "agent_runs": EMPTY,
    "assistant_feedback": EMPTY,
    # -- logs ---------------------------------------------------------------
    "mail_logs": EMPTY,
    # THE AUDIT TRAIL SURVIVES THE PEOPLE IN IT, and this line used to say EMPTY.
    #
    # B2.7 requires this table to be append-only and never purged, and the reason
    # is not paperwork: it records what the OFFICE did — who was approved, who
    # was granted a capability, who deleted a batch — and the purge is itself one
    # of the acts somebody may later have to ask about. Emptying it here made the
    # trail disappear at exactly the moment it mattered, and left the purge with
    # no record of having run.
    #
    # It holds `actor_user_id` values pointing at accounts this purge deletes.
    # Those columns are nullable and ON DELETE SET NULL, so the rows survive with
    # the actor anonymised — which is the shape the erasure regimes actually ask
    # for: remove the person, keep the fact. `entity_id` is a plain string and
    # not a foreign key, so a row about a deleted student keeps its id and stops
    # resolving to anybody.
    "redesign_audit_events": KEEP,
    "redesign_outbox_events": EMPTY,
    # Sign-ins belong to the person who made them and go with that person.
    # Unlike the audit trail, nobody else's record depends on them: the FK is
    # ON DELETE CASCADE and this line agrees with it.
    "login_events": EMPTY,
    # An export is an act by a member of STAFF, usually the Main Admin, who
    # survives this purge — and the fact that a spreadsheet of students left the
    # building is not erased by deleting the students who were in it. The FK is
    # SET NULL for the same reason, so a row whose downloader is deleted keeps
    # the fact and loses the name.
    "export_events": KEEP,
    "redesign_domain_jobs": EMPTY,
    "redesign_api_idempotency_keys": EMPTY,
    # -- a migration's receipt, which HAS no model ---------------------------
    # `students_orphaned_cohort_ids` is a rescue table written by migration
    # `d5a1c8b30f47` before it nulled a batch of unresolvable `cohort_id`s, so
    # that an operator could put them back. It has no model BY DESIGN (see
    # `_PRESERVED_DATA_TABLES` in migrations/env.py: it is an operator's
    # receipt, not part of the schema) and it is therefore the one table the
    # model-driven sweep below could not see.
    #
    # EMPTY, because it is a receipt ABOUT STUDENTS: every row is a
    # `students.id` beside the batch that student was in. Once this module has
    # run there is no student left for it to describe, so keeping it would
    # leave a list of the identifiers of people a purge was asked to remove —
    # in the one table nobody thought to look at, because it is not in
    # `app/models/`.
    "students_orphaned_cohort_ids": EMPTY,
}

#: Tables that exist in the database but have NO MODEL, mirroring
#: `_PRESERVED_DATA_TABLES` in `migrations/env.py`.
#:
#: WHY THIS LIST HAS TO EXIST HERE TOO. Every other reader in this module walks
#: `Base.metadata`, which is the models — so a table a MIGRATION created is
#: invisible to all of them: to the delete pass, which would silently leave its
#: rows; and to `check_verdicts`, whose "a table nobody classified ABORTS THE
#: RUN" promise was only ever true of tables somebody had written a model for.
#: That is the narrower promise the module docstring used to make without
#: saying so.
#:
#: `check_verdicts(db)` now also asks the LIVE DATABASE what tables it has, so
#: a preserved table nobody added here is still caught — at runtime, by the
#: refusal, rather than by trusting this tuple to be complete. This list is what
#: keeps the same check honest with no database in hand (the unit tests), and
#: what puts the rescue tables into the delete order.
PRESERVED_DATA_TABLES: tuple[str, ...] = ("students_orphaned_cohort_ids",)

#: `table -> column` holding a document_store name. The bytes live on the EFS
#: volume and no database delete touches them, so these are collected and
#: destroyed BEFORE the rows that point at them (see the docstring).
FILE_COLUMNS: dict[str, str] = {
    "uploads": "stored_name",
    "registration_documents": "stored_name",
    "staff_signatures": "stored_name",
    "staff_upskilling_certs": "stored_name",
    "alumni_profiles": "resume_stored_name",
    "leave_attachments": "stored_name",
}

#: KEPT tables that point at `users` with no ON DELETE clause. The institution
#: records who created it; that person is about to be deleted and the row must
#: outlive them. Nulled before the accounts go, or the delete fails on an FK
#: and takes the whole transaction with it.
CREATED_BY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("colleges", "created_by_user_id"),
    ("departments", "created_by_user_id"),
    ("academic_courses", "created_by_user_id"),
    ("academic_specializations", "created_by_user_id"),
)


class PurgeRefused(RuntimeError):
    """Raised before anything is destroyed. Always an operator's problem."""


@dataclass
class Plan:
    """What a run would do. A dry run prints this and stops; a real run acts on
    exactly these numbers, so the two can be compared afterwards."""

    survivor_id: str
    survivor_email: str
    rows: dict[str, int] = field(default_factory=dict)
    files: int = 0
    audio_sessions: int = 0
    #: Platform calls whose local stereo recording is swept off the volume. A
    #: COUNT OF CALLS and not of files, the same reading as `audio_sessions`
    #: above and for the same reason: the filesystem is the authority on which
    #: files exist, so the sweep is offered every call rather than only the ones
    #: whose row admits to a recording, and what a dry run can honestly promise
    #: is how many calls it will look under.
    platform_calls: int = 0
    s3_objects: int = 0

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def table_for(name: str):
    """The Core object to count or delete rows in `name` with.

    A preserved rescue table has no model, so `Base.metadata.tables` cannot
    answer for it. A bare `TableClause` can, and is enough: everything either
    destructor does to such a table is WHOLE-TABLE — `SELECT count(*)` and
    `DELETE FROM` — and needs no column to be declared. Anything that does need
    a column (a scope, a nulled FK) is refused for these tables where it is
    written, not silently approximated here.
    """
    mapped = Base.metadata.tables.get(name)
    return mapped if mapped is not None else sa_table(name)


def _tables_in_delete_order():
    """Children first. `sorted_tables` is topological with parents first, which
    is the order to CREATE in; deleting wants the reverse.

    The preserved rescue tables come FIRST and are `TableClause`s rather than
    mapped tables, because they have no model to map. First is safe and not
    merely convenient: a rescue table is written by `CREATE TABLE ... AS`, so it
    carries no foreign keys in either direction — nothing references it and it
    references nothing — which puts it outside the topological order entirely
    rather than somewhere in it.
    """
    preserved = [table_for(name) for name in PRESERVED_DATA_TABLES]
    return preserved + list(reversed(Base.metadata.sorted_tables))


def known_tables(db: Session | None = None) -> set[str]:
    """Every table this module must have a verdict for.

    THE MODELS ARE NOT THE SCHEMA, which is the assumption this used to make.
    `Base.metadata` is what `app/models/` declares; a table created by a
    MIGRATION and deliberately given no model — see `PRESERVED_DATA_TABLES` —
    is not in it, so the "a table nobody classified ABORTS THE RUN" promise
    silently did not cover exactly the tables nobody was maintaining a model
    for. `students_orphaned_cohort_ids` sat outside both destructors that way.

    So: the models, plus the preserved tables named above, plus — when a
    connection is in hand, which it always is on the path that actually
    destroys anything — WHAT THE DATABASE ITSELF REPORTS. The last of those is
    the one that cannot go stale, and it is why a second rescue table added by
    a future migration is refused rather than skipped even if nobody thinks to
    update the tuple.
    """
    known = {t.name for t in Base.metadata.sorted_tables} | set(PRESERVED_DATA_TABLES)
    if db is not None:
        known |= set(sa_inspect(db.get_bind()).get_table_names())
    return known - {"alembic_version"}


def check_verdicts(db: Session | None = None) -> None:
    """Every table classified, and no verdict for a table that is gone.

    Pinned by tests/test_purge_people.py so the failure lands in CI rather than
    on a production console at the moment somebody is trying to hand a
    deployment over.
    """
    known = known_tables(db)
    unclassified = sorted(known - set(VERDICTS) - {"alembic_version"})
    if unclassified:
        raise PurgeRefused(
            "These tables have no verdict in purge_people.VERDICTS: "
            + ", ".join(unclassified)
            + ". Classify each one KEEP or EMPTY before this can run - the purge "
            "will not guess."
        )
    stale = sorted(set(VERDICTS) - known)
    if stale:
        raise PurgeRefused(
            "purge_people.VERDICTS names tables that no longer exist: "
            + ", ".join(stale)
            + ". Remove them."
        )


def find_survivor(db: Session) -> tuple[str, str]:
    """The one account that lives. Found by ROLE, never by an address typed on
    a form: a purge that took an email argument would delete everybody the
    moment somebody fat-fingered it."""
    rows = db.execute(
        select(user_model.User.id, user_model.User.email).where(
            user_model.User.role == user_model.Role.ADMIN
        )
    ).all()
    if not rows:
        raise PurgeRefused(
            "No ADMIN account exists on this deployment, so there is nobody for "
            "the purge to keep and it would lock every human out of the console. "
            "Create the Main Admin first (Ops task > grant-access, role ADMIN)."
        )
    if len(rows) > 1:
        listed = ", ".join(e for _, e in rows)
        raise PurgeRefused(
            f"{len(rows)} ADMIN accounts exist ({listed}). There is exactly one "
            "Main Admin by design, and this module will not pick which of them "
            "survives. Demote the others to MENTOR first."
        )
    return rows[0][0], rows[0][1]


def build_plan(db: Session) -> Plan:
    # WITH the connection: this is the path that ends in a delete, so the
    # question "is every table in this database classified" must be asked of
    # the database and not of `app/models/`.
    check_verdicts(db)
    survivor_id, survivor_email = find_survivor(db)
    plan = Plan(survivor_id=survivor_id, survivor_email=survivor_email)

    for table in _tables_in_delete_order():
        verdict = VERDICTS.get(table.name)
        if verdict == KEEP or verdict is None:
            continue
        if verdict == SURVIVOR:
            n = db.scalar(
                select(func.count()).select_from(table).where(table.c.id != survivor_id)
            )
        else:
            n = db.scalar(select(func.count()).select_from(table))
        if n:
            plan.rows[table.name] = int(n)

    for name, column in FILE_COLUMNS.items():
        col = Base.metadata.tables[name].c[column]
        plan.files += int(
            db.scalar(select(func.count()).select_from(Base.metadata.tables[name]).where(col.is_not(None))) or 0
        )
    plan.audio_sessions = int(
        db.scalar(select(func.count()).select_from(Base.metadata.tables["interview_sessions"])) or 0
    )
    calls = Base.metadata.tables["platform_call_sessions"]
    # Two numbers off one table, because one call can leave bytes in two places
    # and an operator has to see both BEFORE the rows that point at them go: the
    # object in the recordings bucket (only where the upload happened, hence the
    # `recording_s3_key` predicate) and the stereo WAV on the local volume,
    # which is written whether or not a bucket was ever configured.
    plan.platform_calls = int(db.scalar(select(func.count()).select_from(calls)) or 0)
    plan.s3_objects = int(
        db.scalar(select(func.count()).select_from(calls).where(calls.c.recording_s3_key.is_not(None))) or 0
    )
    return plan


# The four file stores, each as a function over the rows it is handed rather
# than over the whole database. `app.purge_students` destroys a SUBSET of the
# same files and calls exactly these — copying them would leave two versions of
# the one step in a purge that cannot be rolled back, and the copy would be the
# one that stopped getting the fix.
#
# None of the four knows how to do another one's work, and that is a property of
# the stores rather than a style: a document's name is resolved inside the
# uploads root, a per-speaker WAV through `interview_audio.track_path`, and a
# platform call's stereo recording inside `platform/` — three different roots
# with three different naming rules, and the fourth is a bucket. Each delete
# therefore goes through the module that WROTE the bytes, and this file only
# hands each one the set of rows that is going.


def destroy_document_files(rows: Iterable[tuple[str, str]]) -> list[str]:
    """Delete stored documents. `rows` is (label, stored_name); the label only
    ever says WHICH table pointed at a file that would not die, because an
    operator holding a bare stored name has nowhere to start looking."""
    from .document_store import delete as delete_stored

    failures: list[str] = []
    for label, stored in rows:
        try:
            delete_stored(stored)
        except FileNotFoundError:
            pass  # already gone is the outcome we wanted
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            log.error("Could not delete %s file %s: %s", label, stored, exc)
            failures.append(f"{label}:{stored}")
    return failures


def destroy_interview_audio(sessions: Iterable[tuple[str, str | None]]) -> list[str]:
    """Interview audio through its own store, for EVERY session handed in and
    never only the ones whose row admits to having audio —
    retention._delete_interview_audio documents why the filesystem is the
    authority here and the columns are not."""
    try:
        from .interview_audio import delete_session_audio
    except ImportError:  # a slim image without the store
        return []

    failures: list[str] = []
    for sid, path in sessions:
        try:
            delete_session_audio(sid, path)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not delete interview audio for %s: %s", sid, exc)
            failures.append(f"interview_audio:{sid}")
    return failures


def destroy_platform_call_audio(session_ids: Iterable[str]) -> list[str]:
    """The voice platform's dual-channel recordings, off the local volume.

    A SEPARATE STORE FROM `destroy_interview_audio` ABOVE, and it has to be one.
    `voice_platform/api/call_close.py` writes `platform/<call id>.wav` (plus the
    mono mixdown and the mp3 a recording policy may ask for) under the
    interview-audio root, and `interview_audio.track_path` — the only path
    `delete_session_audio` will build — cannot produce a name in a subdirectory.
    So the per-speaker sweep ran to completion over these files and reported
    success, while `platform_call_sessions` went in the row pass immediately
    afterwards and took the last pointer to them with it.

    Handed every call the caller is taking and not only the ones whose row
    admits to a recording — `destroy_interview_audio`'s reason, and the same
    one: `recording_s3_key` records what reached the BUCKET, nothing on the row
    records what is on the DISK, and the filesystem is the authority on that. A
    call that never recorded costs three `unlink`s answering FileNotFoundError.

    This deletes the LIVE copy only. The archive sweep has taken these same
    files into the Object-Locked bucket (`app.archive_documents` recurses into
    `platform/`), which no destructor in this product may reach — the website's
    delete and the archive's permanence are promises about different copies.

    NO MANIFEST ROW IS RELEASED HERE, and that is not an omission. A row in
    `archived_documents` is written by `document_manifest.save_and_record`, the
    one choke point the six DOCUMENT stores pass through; this file is written
    by the media bridge and uploaded by the nightly sweep, which reads the disk
    and not that table, so there is no row naming it and `release` would be a
    no-op on a name it does not know. The manifest is KEEP in both destructors
    either way — see `VERDICTS["archived_documents"]` — so a row that did exist
    would keep saying whose file it was, which is the whole point of it.
    """
    try:
        from .voice_platform.api.call_close import (
            delete_platform_call_audio as delete_call_audio,
        )
    except ImportError:  # a slim image without the voice platform
        return []

    failures: list[str] = []
    for session_id in session_ids:
        try:
            delete_call_audio(session_id)
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            log.error("Could not delete platform call audio for %s: %s", session_id, exc)
            failures.append(f"platform_audio:{session_id}")
    return failures


def destroy_s3_recordings(keys: Sequence[str]) -> list[str]:
    """Platform call recordings in S3. No bucket configured means either that
    the platform never uploaded anything — a normal deployment — or that this
    process cannot reach the bucket holding them. The two are worth telling
    apart only when there are keys about to become unfindable, which is why the
    warning hangs off the count rather than off the configuration."""
    try:
        from .voice_platform.storage.s3 import recording_store
    except ImportError:
        return []

    store = recording_store()
    if store is None:
        if keys:
            log.warning(
                "%d call session(s) name an S3 recording but no recordings bucket "
                "is configured here, so those objects were NOT deleted. Their rows "
                "are about to go, which makes the objects undiscoverable - set "
                "PLATFORM_RECORDINGS_BUCKET and run again if they matter.",
                len(keys),
            )
        return []

    failures: list[str] = []
    for key in keys:
        try:
            store.delete(key)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not delete S3 recording %s: %s", key, exc)
            failures.append(f"s3:{key}")
    return failures


def _destroy_files(db: Session, plan: Plan) -> list[str]:
    """Bytes first, rows after. Every file in every store, because every account
    that could own one is going."""
    documents: list[tuple[str, str]] = []
    for name, column in FILE_COLUMNS.items():
        col = Base.metadata.tables[name].c[column]
        documents += [
            (name, stored)
            for (stored,) in db.execute(select(col).where(col.is_not(None))).all()
        ]

    sessions = Base.metadata.tables["interview_sessions"]
    audio = db.execute(select(sessions.c.id, sessions.c.audio_path)).all()

    calls = Base.metadata.tables["platform_call_sessions"]
    # ONE read of the call rows, because both of a call's recordings are
    # answered from the same row and this is the last moment either can be
    # answered at all: `_delete_rows` empties `platform_call_sessions` a few
    # statements from now, and the id IS the local file's name.
    call_rows = db.execute(select(calls.c.id, calls.c.recording_s3_key)).all()
    call_ids = [call_id for call_id, _ in call_rows]
    keys = [key for _, key in call_rows if key is not None]

    failures = (
        destroy_document_files(documents)
        + destroy_interview_audio(audio)
        + destroy_platform_call_audio(call_ids)
        + destroy_s3_recordings(keys)
    )
    # The manifest SURVIVES this run (`VERDICTS["archived_documents"] is KEEP`),
    # so the files whose rows are about to go keep their names. Stamping the
    # release is what makes those rows honest afterwards: without it every
    # entry reads as still referenced by a table that no longer has the row.
    #
    # After the deletes and not before, and best-effort by the same contract
    # `document_manifest.release` states: this is bookkeeping about bytes that
    # are already gone, and it must never be the reason a purge stops halfway.
    from .document_manifest import release

    for _label, stored in documents:
        try:
            release(db, stored, reason="purge_people")
        except Exception:  # noqa: BLE001 — reported, never fatal to a purge
            log.exception("Could not mark %s released in the manifest", stored)
    return failures


def _null_created_by(db: Session, plan: Plan) -> None:
    """The institution records who created it. That person is going, and the
    row must outlive them: these four FKs carry no ON DELETE, so without this
    the accounts delete fails on a constraint and takes the pass with it."""
    for name, column in CREATED_BY_COLUMNS:
        table = Base.metadata.tables[name]
        db.execute(
            update(table)
            .where(table.c[column].is_not(None), table.c[column] != plan.survivor_id)
            .values(**{column: None})
        )


def _delete_rows(db: Session, plan: Plan) -> None:
    """Every EMPTY table, children first, then the accounts.

    SEPARATE FROM `execute` AND WITHOUT A COMMIT ON PURPOSE. This is the half
    whose correctness is a property of the ORDER, and the only honest way to
    test an order against 188 real foreign keys is to run it against the real
    schema and roll back — which a function that commits cannot offer.
    tests/test_purge_people.py does exactly that.
    """
    for table in _tables_in_delete_order():
        verdict = VERDICTS.get(table.name)
        if verdict == EMPTY:
            db.execute(delete(table))
        elif verdict == SURVIVOR:
            db.execute(delete(table).where(table.c.id != plan.survivor_id))


def execute(db: Session, plan: Plan) -> list[str]:
    """Destroy. One transaction for the rows, so the database is either purged
    or untouched; the files are gone before it opens, because a filesystem has
    no transaction to join."""
    failures = _destroy_files(db, plan)
    _null_created_by(db, plan)
    _delete_rows(db, plan)
    db.commit()
    return failures


def _stamp(db: Session, plan: Plan) -> None:
    """One audit row, written AFTER the sweep that empties the audit table, so
    the deployment's history starts with the fact that it was emptied and by
    whom. Best-effort: a purge that worked must not be reported as failed
    because its own receipt would not write."""
    try:
        from .models.redesign import AuditEvent

        db.add(
            AuditEvent(
                actor_user_id=plan.survivor_id,
                entity_type="deployment",
                entity_id=plan.survivor_id,
                action="PURGE",
                after_json={
                    "purged_at": datetime.now(timezone.utc).isoformat(),
                    "kept_account": plan.survivor_email,
                    "rows_deleted": plan.total_rows,
                    "tables_emptied": sorted(plan.rows),
                    "files_deleted": plan.files,
                    "audio_sessions_swept": plan.audio_sessions,
                    "platform_calls_swept": plan.platform_calls,
                    "s3_objects_deleted": plan.s3_objects,
                },
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("Purge completed but its audit row could not be written: %s", exc)
        db.rollback()


def _report(plan: Plan, *, applied: bool) -> None:
    head = "PURGED" if applied else "DRY RUN - nothing was deleted"
    log.info("%s", head)
    log.info("Account kept: %s (%s)", plan.survivor_email, plan.survivor_id)
    if not plan.rows:
        log.info("Nothing to delete: this deployment holds no other accounts or records.")
    for name in sorted(plan.rows, key=lambda k: (-plan.rows[k], k)):
        log.info("  %-45s %8d row(s)", name, plan.rows[name])
    log.info("  %-45s %8d row(s)  TOTAL", "", plan.total_rows)
    log.info(
        "Stored files: %d document(s), %d interview session(s) swept for audio, "
        "%d platform call(s) swept for a local recording, %d S3 recording(s).",
        plan.files,
        plan.audio_sessions,
        plan.platform_calls,
        plan.s3_objects,
    )
    kept = sorted(n for n, v in VERDICTS.items() if v == KEEP)
    log.info("Kept intact (%d tables): %s", len(kept), ", ".join(kept))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.purge_people",
        description=(
            "Delete every account except the Main Admin, everything those "
            "accounts did, and every recording and transcript. Keeps the "
            "institutional hierarchy, the catalogues and the Knowledge Base."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the run reports and changes nothing.",
    )
    parser.add_argument(
        "--i-understand-this-is-permanent",
        dest="understood",
        action="store_true",
        help="Required with --apply. There is no undo and no soft-delete here.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.apply and not args.understood:
        log.error(
            "--apply also needs --i-understand-this-is-permanent. This deletes "
            "production records outright: there is no soft-delete, no undo, and "
            "the audio and documents are removed from the volume as well."
        )
        return 2

    try:
        with SessionLocal() as db:
            plan = build_plan(db)
            if not args.apply:
                _report(plan, applied=False)
                log.info("Re-run with --apply --i-understand-this-is-permanent to do it.")
                return 0
            failures = execute(db, plan)
            _stamp(db, plan)
            _report(plan, applied=True)
            if failures:
                log.error(
                    "%d stored file(s) could not be destroyed and their rows are "
                    "now gone, so the bytes are no longer discoverable from the "
                    "database: %s",
                    len(failures),
                    ", ".join(failures[:20]) + (" ..." if len(failures) > 20 else ""),
                )
                return 1
    except PurgeRefused as exc:
        log.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
