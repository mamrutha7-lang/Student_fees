"""The production purge: everyone but the Main Admin, and everything they did.

The load-bearing test here is `test_the_delete_order_survives_every_foreign_key`,
and it is written the way it is on purpose. `app.purge_people` is correct only
if its delete order satisfies 188 real foreign keys — several of which carry no
ON DELETE clause at all (`login_days.user_id`, `mentors.user_id`,
`students.user_id`, and the four `created_by_user_id` columns on the
institutional spine, which point at rows that are about to be deleted from
tables that are being KEPT). A synthetic fixture graph would prove the order
against the constraints somebody remembered to reproduce. So instead the test
runs the REAL delete against the REAL schema and the data that is actually
there, inside a transaction it rolls back — which is why `_delete_rows` is
factored out of `execute` without a commit.

`test_every_table_has_a_verdict` is the other half, and it is the one that will
fail for someone who has never read this file: add a table, and the purge stops
until you have said in `VERDICTS` whether it holds people or catalogue. That is
deliberate. The alternative — a prefix rule, or a default — is how a purge
either leaves a student's records behind or destroys the badge catalogue.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import column as sa_column, func, select, table as sa_table

from conftest import requires_db

from app import purge_people
from app.db import Base, SessionLocal
from app.models.user import Role, User


def test_every_table_has_a_verdict():
    """No database needed, so this one runs everywhere and is the guard that
    actually catches the next person adding a model."""
    purge_people.check_verdicts()  # raises PurgeRefused naming the offenders


def test_the_verdicts_cover_the_whole_schema_exactly():
    # `known_tables()` and NOT `Base.metadata` — that is the whole of the fix
    # this assertion is testing. The models are not the schema: a table created
    # by a migration and deliberately given no model is absent from the
    # metadata, so comparing against it asked "is every MODEL classified" while
    # reading as "is every TABLE classified".
    known = purge_people.known_tables()
    assert set(purge_people.VERDICTS) == known
    assert "students_orphaned_cohort_ids" in known, (
        "the preserved rescue tables must be inside the coverage question, not "
        "beside it — they are exactly the tables nobody maintains a model for"
    )
    assert "students_orphaned_cohort_ids" not in {t.name for t in Base.metadata.sorted_tables}
    # The institution is kept, the people are not. Spot-check both directions so
    # a mass find-and-replace cannot flip every verdict and stay green.
    assert purge_people.VERDICTS["cohorts"] == purge_people.KEEP
    assert purge_people.VERDICTS["colleges"] == purge_people.KEEP
    assert purge_people.VERDICTS["knowledge_chunks"] == purge_people.KEEP
    assert purge_people.VERDICTS["students"] == purge_people.EMPTY
    assert purge_people.VERDICTS["mentors"] == purge_people.EMPTY
    assert purge_people.VERDICTS["interview_sessions"] == purge_people.EMPTY
    assert purge_people.VERDICTS["messages"] == purge_people.EMPTY
    assert purge_people.VERDICTS["users"] == purge_people.SURVIVOR


def test_the_written_counts_are_the_real_counts():
    """The two destructors quote their own size, and the numbers went stale.

    `93` was the table count AND the foreign-key count in six places at once —
    in the two modules' docstrings, in both test modules, and in AGENTS.md —
    long after the schema had outgrown both of those figures. Nobody
    noticed, because prose is not executed. That matters more here than almost
    anywhere else in the repository: these paragraphs are what an operator reads
    to check their understanding of a destructive, irreversible pass BEFORE they
    type `--i-understand-this-is-permanent`, and a number they can see is wrong
    costs the rest of the page its credibility.

    So the prose is executed now. Add a model and this fails beside
    `test_every_table_has_a_verdict`, which is the point: one event, both
    obligations — classify the table, and correct the sentence that counts it.
    """
    repo = pathlib.Path(__file__).resolve().parents[3]
    tables = len(purge_people.VERDICTS)
    foreign_keys = sum(len(t.foreign_keys) for t in Base.metadata.tables.values())

    sources = [
        repo / "apps" / "api-py" / "app" / "purge_people.py",
        repo / "apps" / "api-py" / "app" / "purge_students.py",
        repo / "apps" / "api-py" / "tests" / "test_purge_people.py",
        repo / "apps" / "api-py" / "tests" / "test_purge_students.py",
        repo / "AGENTS.md",
    ]

    # The two claims are DIFFERENT counts and were conflated into one number
    # for months, so they are matched separately and never by a bare \d+.
    table_claim = re.compile(
        r"(?:all |each of the |Every one of the |dict of )(\d+)\s+(?:tables|entries)"
    )
    fk_claim = re.compile(r"(\d+)\s+real foreign keys")

    for path in sources:
        text = path.read_text(encoding="utf-8")
        for claimed in table_claim.findall(text):
            assert int(claimed) == tables, (
                f"{path.name} says {claimed} tables; the schema has {tables}"
            )
        for claimed in fk_claim.findall(text):
            assert int(claimed) == foreign_keys, (
                f"{path.name} says {claimed} foreign keys; the schema has {foreign_keys}"
            )

    # AGENTS.md also breaks purge_students down by verdict, and that sentence
    # was wrong in a second way: its three numbers summed to 92, not to any
    # schema this repository has ever had.
    from app import purge_students

    breakdown = re.search(
        r"(\d+) tables are emptied outright, (\d+) are untouched, (\d+) are scoped",
        (repo / "AGENTS.md").read_text(encoding="utf-8"),
    )
    assert breakdown, "the purge_students breakdown sentence was reworded; re-pin it"
    emptied, untouched, scoped = (int(g) for g in breakdown.groups())
    verdicts = purge_students.STUDENT_VERDICTS.values()
    assert emptied == sum(1 for v in verdicts if v == purge_students.ALL)
    assert untouched == sum(1 for v in verdicts if v == purge_students.KEEP)
    assert scoped == sum(
        1 for v in verdicts if v not in (purge_students.ALL, purge_students.KEEP)
    )
    assert emptied + untouched + scoped == tables


def test_every_file_backed_table_is_emptied_not_kept():
    """A KEPT table whose rows point at stored files would leave those files
    orphaned on the volume with nothing left to find them by."""
    for name in purge_people.FILE_COLUMNS:
        assert purge_people.VERDICTS[name] == purge_people.EMPTY, name


def test_the_created_by_columns_are_all_on_kept_tables():
    """The nulling pass exists only for KEPT tables. A column listed here whose
    table is emptied anyway is a leftover that will confuse the next reader."""
    for name, column in purge_people.CREATED_BY_COLUMNS:
        assert purge_people.VERDICTS[name] == purge_people.KEEP, name
        assert column in purge_people.table_for(name).c


def _plan_with_one_admin(db):
    """Plan the purge with EXACTLY ONE ADMIN, whatever else the suite has left
    lying around.

    `find_survivor` refuses more than one office account, on purpose — picking
    between two would be the module deciding which colleague keeps their
    account. But other modules in this suite mint their own ADMIN rows
    (`test_main_admin.py`, `test_admin_faculty.py`), so whether this test saw
    one admin or three depended on what had run before it: it passed alone and
    failed in the full suite, which is the least useful kind of red.

    So the condition is CREATED here rather than borrowed from the ambient
    database. The demotion rides inside the caller's transaction and dies with
    the same `rollback()` the delete does, so nothing outside this test ever
    sees it.
    """
    admins = db.execute(
        select(Base.metadata.tables["users"].c.id).where(
            Base.metadata.tables["users"].c.role.in_((Role.ADMIN, "ADMIN"))
        )
    ).scalars().all()
    assert admins, "the dev seed's Main Admin is missing; run python -m app.seed"
    if len(admins) > 1:
        # Keep admins[0]; the rest become faculty for the life of this
        # transaction. Which one survives does not matter to the delete order.
        users = Base.metadata.tables["users"]
        db.execute(users.update().where(users.c.id.in_(admins[1:])).values(role="MENTOR"))
    return purge_people.build_plan(db)


@requires_db
def test_the_delete_order_survives_every_foreign_key():
    """THE REAL DELETE, ROLLED BACK. If any table is deleted before something
    that references it, Postgres raises here and this test fails — which is the
    only way to learn that before a production console does."""
    users = Base.metadata.tables["users"]
    kept_before = {}
    with SessionLocal() as db:
        plan = _plan_with_one_admin(db)
        for name, verdict in purge_people.VERDICTS.items():
            if verdict == purge_people.KEEP:
                kept_before[name] = db.scalar(
                    select(func.count()).select_from(purge_people.table_for(name))
                )
        try:
            purge_people._null_created_by(db, plan)
            purge_people._delete_rows(db, plan)

            # Exactly one account, and it is the Main Admin.
            assert db.scalar(select(func.count()).select_from(users)) == 1
            survivor = db.scalar(select(users.c.id))
            assert survivor == plan.survivor_id
            assert db.scalar(select(users.c.role)) in (Role.ADMIN, "ADMIN")

            # Every EMPTY table is empty.
            for name, verdict in purge_people.VERDICTS.items():
                if verdict == purge_people.EMPTY:
                    n = db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
                    assert n == 0, f"{name} still holds {n} row(s)"

            # Every KEPT table is untouched. This is the half that makes the
            # purge usable: an office that has to retype its whole hierarchy
            # afterwards will simply not run it.
            for name, before in kept_before.items():
                after = db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
                assert after == before, f"{name} lost rows: {before} -> {after}"
        finally:
            db.rollback()


@requires_db
def test_the_plan_counts_what_the_delete_deletes():
    """A dry run an operator reads and then acts on is only useful if the two
    passes agree. Same session, same transaction: plan, delete, compare."""
    with SessionLocal() as db:
        plan = _plan_with_one_admin(db)
        before = {
            name: db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
            for name in plan.rows
        }
        try:
            purge_people._null_created_by(db, plan)
            purge_people._delete_rows(db, plan)
            for name, planned in plan.rows.items():
                # users keeps one row; every other planned table goes to zero.
                expected = 1 if name == "users" else 0
                after = db.scalar(select(func.count()).select_from(purge_people.table_for(name)))
                assert after == expected
                assert before[name] - after == planned, name
        finally:
            db.rollback()


@requires_db
def test_it_refuses_when_the_main_admin_is_not_exactly_one(make_user):
    """Zero admins would lock every human out of the console; two would make
    this module pick which colleague keeps their account. Both refuse, and
    both refusals name the problem."""
    with SessionLocal() as db:
        try:
            # Two: add a second ADMIN and watch it refuse by count.
            db.add(
                User(
                    id="purge-test-second-admin",
                    email="second.admin@bgscet.ac.in",
                    name="Second Admin",
                    password_hash="google-only",
                    role=Role.ADMIN,
                )
            )
            db.flush()
            with pytest.raises(purge_people.PurgeRefused) as exc:
                purge_people.find_survivor(db)
            assert "ADMIN accounts exist" in str(exc.value)
            assert "second.admin@bgscet.ac.in" in str(exc.value)

            # Zero: demote every admin and watch it refuse the other way.
            # DEMOTED, not deleted — deleting them here trips
            # `login_days_user_id_fkey`, which is exactly the no-ON-DELETE
            # constraint `_delete_rows` orders around, and this test is about
            # the refusal rather than the order.
            users = Base.metadata.tables["users"]
            db.execute(
                users.update().where(users.c.role == Role.ADMIN).values(role=Role.MENTOR)
            )
            with pytest.raises(purge_people.PurgeRefused) as exc:
                purge_people.find_survivor(db)
            assert "No ADMIN account" in str(exc.value)
        finally:
            db.rollback()


@requires_db
def test_a_dry_run_changes_nothing(make_user):
    """`main()` with no flags must be readable by an operator who is not yet
    sure, and must leave the deployment exactly as it found it.

    `main()` opens its OWN session, so unlike the tests above this one cannot
    stage a single-admin database inside a transaction it rolls back. It
    therefore accepts either exit code, and the reason is the point being made:
    0 is a dry run that planned, 2 is `find_survivor` refusing because another
    module in this suite has an ADMIN row live — and BOTH must leave the
    deployment untouched. The refusal path is the one more likely to be got
    wrong, because it aborts half way through `build_plan`.
    """
    users = Base.metadata.tables["users"]
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(users))
        admins_before = db.scalar(
            select(func.count()).select_from(users).where(users.c.role.in_((Role.ADMIN, "ADMIN")))
        )

    code = purge_people.main([])
    assert code in (0, 2), code
    if admins_before == 1:
        assert code == 0, "one Main Admin: the dry run should plan, not refuse"

    with SessionLocal() as db:
        after = db.scalar(select(func.count()).select_from(users))
    assert after == before


@requires_db
def test_the_receipt_actually_writes():
    """`_stamp` swallows its own failure so a purge that worked is not reported
    as failed by its receipt -- which means a broken receipt is SILENT, and the
    receipt is the only record left that the deployment was emptied at all
    (the sweep empties the audit table immediately before it). So it is written
    for real here, read back, and removed."""
    audit = Base.metadata.tables["redesign_audit_events"]
    with SessionLocal() as db:
        plan = _plan_with_one_admin(db)
        # ROLLED BACK BEFORE THE COMMIT, and this line is load-bearing.
        #
        # `_plan_with_one_admin` demotes every surplus ADMIN to MENTOR and says
        # in its docstring that the demotion "dies with the same rollback() the
        # delete does" — which is true of the two tests above and was FALSE
        # here: this one never rolls back, and `_stamp` COMMITS, so the pending
        # demotion was committed with the receipt. On a database where any
        # earlier module had left a second ADMIN row, that permanently demoted
        # the seeded Main Admin, and every purge test afterwards failed with
        # "the dev seed's Main Admin is missing" — on a shared dev database,
        # for everybody, until somebody put the role back by hand. `app.seed`
        # does not repair it either: the account still EXISTS, so the seed
        # reports "already exists" and changes nothing.
        #
        # The plan is a plain object and holds the ids it needs, so undoing the
        # demotion before the receipt is written costs nothing.
        db.rollback()
        plan.rows = {"users": 11}
        purge_people._stamp(db, plan)  # commits its own row

    try:
        with SessionLocal() as db:
            row = db.execute(
                select(audit.c.id, audit.c.action, audit.c.after_json, audit.c.actor_user_id)
                .where(audit.c.entity_type == "deployment", audit.c.action == "PURGE")
                .order_by(audit.c.occurred_at.desc())
            ).first()
            assert row is not None, "the purge left no record of itself"
            assert row.actor_user_id == plan.survivor_id
            assert row.after_json["kept_account"] == plan.survivor_email
            assert row.after_json["rows_deleted"] == 11
            written_id = row.id
    finally:
        with SessionLocal() as db:
            db.execute(audit.delete().where(audit.c.id == written_id))
            db.commit()


def test_apply_alone_is_refused():
    """The second flag is the whole confirmation. --apply on its own must not
    delete anything, and must say why it did not."""
    assert purge_people.main(["--apply"]) == 2


@requires_db
def test_the_preserved_rescue_table_is_actually_emptied():
    """The blind spot this pair of guards had, proved end to end.

    `students_orphaned_cohort_ids` is written by migration `d5a1c8b30f47` to
    preserve `cohort_id`s it was about to null, and it has NO MODEL by design
    (`migrations/env.py::_PRESERVED_DATA_TABLES` — it is an operator's receipt,
    not part of the schema). Everything in `purge_people` walked
    `Base.metadata`, so the table was invisible to all of it: not classified by
    `check_verdicts`, whose "a table nobody classified ABORTS THE RUN" promise
    was therefore only ever about tables somebody had written a model for, and
    never reached by `_delete_rows`.

    What that left behind is the part worth stating: a list of `students.id`
    values, surviving a pass whose entire purpose is to remove every trace of
    the people those ids identify — in the one table nobody thinks to look at.

    Verdict-level coverage is asserted above. This runs the REAL delete against
    the REAL schema and rolls back, because a verdict that no code path acts on
    is the same blind spot one level up.
    """
    # The module's own resolver for the reads, so this test fails if that stops
    # answering for a model-less table. Columns are spelled out only for the
    # INSERT, which is the one operation neither destructor performs and which
    # a bare `TableClause` therefore has no need to know about.
    receipts = purge_people.table_for("students_orphaned_cohort_ids")
    writable = sa_table(
        "students_orphaned_cohort_ids", sa_column("student_id"), sa_column("cohort_id")
    )
    with SessionLocal() as db:
        plan = _plan_with_one_admin(db)
        try:
            db.execute(
                writable.insert().values(student_id="stu-ghost", cohort_id="coh-ghost")
            )
            assert db.scalar(select(func.count()).select_from(receipts)) >= 1

            purge_people._null_created_by(db, plan)
            purge_people._delete_rows(db, plan)

            n = db.scalar(select(func.count()).select_from(receipts))
            assert n == 0, f"the rescue table still holds {n} row(s) after the purge"
        finally:
            db.rollback()


def test_the_preserved_table_list_matches_the_migration_environment():
    """ONE FACT, TWO FILES, PINNED — the pattern `test_codebase_guards.py` uses
    for the `postgresql-client` pin against `engine_version`.

    `migrations/env.py::_PRESERVED_DATA_TABLES` keeps these tables OUT of
    autogenerate, so nobody proposes `op.drop_table` on an operator's receipt.
    `purge_people.PRESERVED_DATA_TABLES` puts them INTO the delete order and the
    coverage question. They are the same fact written twice, and the two ends
    fail in opposite, silent ways: a name only env.py knows is a table the
    destructors go back to ignoring, and a name only this module knows is a
    table autogenerate offers to drop.

    Parsed from the source rather than imported, because importing
    `migrations/env.py` runs Alembic's context at module scope.
    """
    import ast

    repo_env = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "env.py"
    tree = ast.parse(repo_env.read_text(encoding="utf-8"))
    found: tuple[str, ...] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_PRESERVED_DATA_TABLES"
            for t in node.targets
        ):
            found = tuple(ast.literal_eval(node.value))
    assert found is not None, (
        "migrations/env.py no longer declares _PRESERVED_DATA_TABLES as a literal; "
        "re-pin this guard rather than deleting it"
    )
    assert set(found) == set(purge_people.PRESERVED_DATA_TABLES), (
        "migrations/env.py and purge_people disagree about which tables are "
        f"preserved: env.py={sorted(found)}, "
        f"purge_people={sorted(purge_people.PRESERVED_DATA_TABLES)}"
    )
