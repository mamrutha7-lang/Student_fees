"""The core stack synthesises in both phases, and the template has the shape
the cutover depends on.

These are the guards that make `cdk import` safe to run against live student
data. Each pins a property that, if it drifted, would either make the import
fail (a mismatched physical name) or make the first deploy after it destroy
something (a master password, a replaced security group).
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from reep_core import DEREGISTRATION_DELAY_SECONDS, STOP_TIMEOUT_SECONDS, CoreStack, DrVaultStack, EdgeWafStack, drift_reader
from reep_core.edge import (
    BODY_RULES_OUTSIDE_UPLOADS,
    CLOUDFRONT_LOG_FIELDS,
    CLOUDFRONT_LOG_FIELDS_NEVER,
    COMMON_RULE_SET_BODY_LABELS,
    COMMON_RULE_SET_COUNTED,
    UPTIME_CHECK_TYPE,
)
from reep_voice_platform import VoicePlatformStack

TF_DIR = Path(__file__).resolve().parents[2] / "aws"

#: The guards that read the .tf files are for the coexistence window. Step 7
#: of docs/cdk-cutover.md deletes those files; from then on the mirror is the
#: only record and these tests skip rather than fail the cutover commit.
requires_terraform = pytest.mark.skipif(
    not list(TF_DIR.glob("*.tf")),
    reason="Terraform released (docs/cdk-cutover.md step 7); the mirror is now the only record",
)


CDK_JSON_CONTEXT = json.loads((Path(__file__).resolve().parents[1] / "cdk.json").read_text(encoding="utf-8"))["context"]


def _core(phase: str, **context: object) -> Template:
    # cdk.json's context is loaded the way the CLI loads it, feature flags and
    # harden targets included — the import phase must stay correct WITH them.
    app = cdk.App(context={**CDK_JSON_CONTEXT, "phase": phase, **context})
    stack = CoreStack(app, "test-core", project="reep", env=cdk.Environment(account="123456789012", region="ap-south-1"))
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def imported() -> Template:
    return _core("import")


@pytest.fixture(scope="module")
def hardened() -> Template:
    return _core(
        "harden",
        wafWebAclArn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/reep-edge/abc",
        drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr",
        sesIdentityDomain="bgscet.ac.in",
        sesFromAddress="reep@bgscet.ac.in",
        alertEmail="ops@bgscet.ac.in",
        # Deliberately ON in the fixture every whole-template guard reads, so
        # the SES resources are covered by the Retain rule, the tag plan and the
        # import-subset rule without any of those tests naming SES. sesManaged
        # DEFAULTS OFF in cdk.json; test_the_ses_resources_stay_out_until_they_
        # are_adopted is what pins that, and it is the one that matters until
        # the adoption in docs/ses-mail.md has been run.
        sesManaged=True,
    )


# ------------------------------------------------------- the two phases --


def test_both_phases_synthesise(imported: Template, hardened: Template) -> None:
    assert imported.to_json()["Resources"]
    assert hardened.to_json()["Resources"]


def test_import_phase_adds_nothing_the_import_cannot_adopt(imported: Template) -> None:
    """`cdk import` refuses a template whose diff creates non-imported
    resources. Custom resources (cross-region references, log-retention
    helpers, OIDC L2) are exactly that, and none may appear."""
    types = {r["Type"] for r in imported.to_json()["Resources"].values()}
    forbidden = {t for t in types if t.startswith("Custom::") or t == "AWS::CloudFormation::CustomResource" or t.startswith("AWS::Lambda::")}
    assert not forbidden, f"import-hostile resources in the import phase: {sorted(forbidden)}"


def test_import_phase_is_a_strict_subset_of_harden(imported: Template, hardened: Template) -> None:
    """Everything the import adopts is still there after hardening, under the
    same logical id — so the second deploy updates in place and replaces
    nothing."""
    imp = set(imported.to_json()["Resources"])
    hard = set(hardened.to_json()["Resources"])
    # The one deliberate exception: the managed NAT gateway and its EIP were
    # retired on 2026-10-03 (natGateway=false in cdk.json) after the NAT
    # instance was proven. Anything else missing is still a replacement.
    retired = {"Nat", "NatEip"} if CDK_JSON_CONTEXT.get("natGateway") is False else set()
    assert imp - hard <= retired, f"import-phase resources missing from harden: {sorted(imp - hard - retired)}"
    added = sorted(hard - imp)
    assert added, "harden must add something, or it is not a hardening"


# ------------------------------------------------- the one fatal property --


def test_no_master_password_in_the_template(imported: Template, hardened: Template) -> None:
    """A MasterUserPassword here would rotate the live database's password
    under the running api. It must never appear, in either phase."""
    for t in (imported, hardened):
        for res in t.to_json()["Resources"].values():
            if res["Type"] == "AWS::RDS::DBInstance":
                props = res.get("Properties", {})
                assert "MasterUserPassword" not in props
                assert "ManageMasterUserPassword" not in props
                assert props["MasterUsername"] == "reep"
                assert props["DBInstanceIdentifier"] == "reep-postgres"
                assert props["DeletionProtection"] is True
                # Retain, NOT Snapshot: Snapshot means delete-after-snapshot, and
                # a delete-stack would then call DeleteDBInstance.
                assert res.get("DeletionPolicy") == "Retain"
                assert res.get("UpdateReplacePolicy") == "Retain"


# -------------------------------------------------------- physical names --

#: Names the Terraform files declare and an import must present verbatim.
#: Read from the .tf files rather than typed twice, so the guard cannot agree
#: with a stale copy of itself.
_TF_NAME_PATTERNS = [
    (r'family\s*=\s*"\$\{var\.project\}-api"', "reep-api"),
    (r'name\s*=\s*"\$\{var\.project\}-api-task"', "reep-api-task"),
    (r'name\s*=\s*"\$\{var\.project\}-task-execution"', "reep-task-execution"),
    (r'name\s*=\s*"\$\{var\.project\}-github-deploy"', "reep-github-deploy"),
    (r'name\s*=\s*"\$\{var\.project\}-scheduler"', "reep-scheduler"),
    (r'name\s*=\s*"\$\{var\.project\}-backup"', "reep-backup"),
    (r'name\s*=\s*"\$\{var\.project\}-claude-observer"', "reep-claude-observer"),
    (r'name\s*=\s*"\$\{var\.project\}-vault"', "reep-vault"),
    (r'name\s*=\s*"\$\{var\.project\}-daily"', "reep-daily"),
    (r'identifier\s*=\s*"\$\{var\.project\}-postgres"', "reep-postgres"),
    (r'name\s*=\s*"\$\{var\.project\}-alb"', "reep-alb"),
    (r'name\s*=\s*"/reep/api"', "/reep/api"),
    (r'name\s*=\s*"\$\{var\.project\}-alerts"', "reep-alerts"),
    (r'name\s*=\s*"\$\{var\.project\}/api"', "reep/api"),
    (r'name\s*=\s*"\$\{var\.project\}-retention-daily"', "reep-retention-daily"),
    (r'name\s*=\s*"\$\{var\.project\}-spa-fallback"', "reep-spa-fallback"),
    (r'name\s*=\s*"interview-dropped-turns"', "interview-dropped-turns"),
    (r'name\s*=\s*"cpu-target"', "cpu-target"),
    (r'name\s*=\s*"memory-target"', "memory-target"),
]


@requires_terraform
@pytest.mark.parametrize("pattern, expected", _TF_NAME_PATTERNS, ids=[e for _, e in _TF_NAME_PATTERNS])
def test_physical_names_match_terraform(imported: Template, pattern: str, expected: str) -> None:
    tf_text = "\n".join(p.read_text(encoding="utf-8") for p in TF_DIR.glob("*.tf"))
    assert re.search(pattern, tf_text), f"the Terraform files no longer declare {expected!r}; update both sides deliberately"
    assert expected in json.dumps(imported.to_json()), f"{expected!r} is in Terraform but not in the import-phase template"


@requires_terraform
def test_alarm_names_match_terraform(imported: Template) -> None:
    tf_text = (TF_DIR / "observability.tf").read_text(encoding="utf-8")
    tf_alarms = {m.replace("${var.project}", "reep") for m in re.findall(r'alarm_name\s*=\s*"([^"]+)"', tf_text)}
    cdk_alarms = {r["Properties"]["AlarmName"] for r in imported.to_json()["Resources"].values() if r["Type"] == "AWS::CloudWatch::Alarm"}
    assert tf_alarms == cdk_alarms, f"terraform={sorted(tf_alarms)} cdk={sorted(cdk_alarms)}"


def test_network_is_the_terraform_layout(imported: Template) -> None:
    imported.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.42.0.0/16"})
    for cidr in ("10.42.0.0/20", "10.42.16.0/20", "10.42.128.0/20", "10.42.144.0/20"):
        imported.has_resource_properties("AWS::EC2::Subnet", {"CidrBlock": cidr})
    imported.resource_count_is("AWS::EC2::NatGateway", 1)


# ------------------------------------------------------- the hardening --


def test_stop_timeout_is_fargates_maximum(hardened: Template) -> None:
    """120 is the ceiling Fargate enforces; the first plan wanted 500 and
    would have been refused. What protects a 480 s interview is the
    deregistration delay below, not this."""
    assert STOP_TIMEOUT_SECONDS == 120
    hardened.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"ContainerDefinitions": Match.array_with([Match.object_like({"Name": "api", "StopTimeout": 120})])},
    )


def test_deregistration_delay_outlasts_an_interview(hardened: Template) -> None:
    assert DEREGISTRATION_DELAY_SECONDS >= 480 + 90
    hardened.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"TargetGroupAttributes": Match.array_with([{"Key": "deregistration_delay.timeout_seconds", "Value": str(DEREGISTRATION_DELAY_SECONDS)}])},
    )


def test_old_task_survives_until_the_new_one_is_healthy(hardened: Template) -> None:
    hardened.has_resource_properties(
        "AWS::ECS::Service",
        {"DeploymentConfiguration": Match.object_like({"MinimumHealthyPercent": 100, "MaximumPercent": 200})},
    )


def test_the_service_keeps_both_tasks_in_every_phase(imported: Template, hardened: Template) -> None:
    """INCIDENT (2026-09-07, the pre-import review, before any import ran):
    this property was written `100 if harden_ecs else 50`, on the assumption
    that 50 was live and 100 the improvement. **Live is already 100** —
    `ecs.tf` never sets it, so 100 is the ECS default. Rendering 50 in the
    import mirror would have produced a drift row at step 5, the checkpoint
    whose entire job is to be empty; and step 9a, which still updates this
    service to flip its ManagedBy tag, would have sent 50 to the live service
    for the length of the Multi-AZ conversion.

    The database's live values are pinned by
    `test_import_phase_carries_the_live_database_values_not_the_harden_targets`.
    Nothing pinned the ECS half. This is that guard: the number is the same in
    both phases, so it can never appear in a diff, a drift report, or a deploy.
    """
    for phase, t in (("import", imported), ("harden", hardened)):
        service = next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::Service")["Properties"]
        assert service["DeploymentConfiguration"]["MinimumHealthyPercent"] == 100, f"{phase} phase renders a value that is not the live 100"
    # And with the ECS half of harden held back — the step 9a template, the one
    # that updates the service for its tag while hardenEcs is false.
    held = _core("harden", hardenEcs="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    service = next(r for r in held.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::Service")["Properties"]
    assert service["DeploymentConfiguration"]["MinimumHealthyPercent"] == 100, "step 9a would send 50 to the live service mid-conversion"


def _plans_by_name(t: Template) -> dict[str, dict]:
    """Every backup plan in the template, keyed by its plan name.

    There are two once the archive tier is on, and `next(r for r in ...)` would
    pick whichever CDK happened to emit first — which is how a guard about the
    DAILY rule silently starts asserting things about the archive one.
    """
    plans = {}
    for r in t.to_json()["Resources"].values():
        if r["Type"] == "AWS::Backup::BackupPlan":
            plan = r["Properties"]["BackupPlan"]
            plans[plan["BackupPlanName"]] = plan
    return plans


def test_one_retention_number_everywhere(hardened: Template) -> None:
    """RDS automated backups, the daily rule, the DR copy and the vault lock
    all read backupRetentionDays. They were 14 and 35.

    The ARCHIVE rule is deliberately not in this set — it is the one number
    that is allowed to differ, and `test_the_archive_tier_outlives_the_daily_one`
    is what holds it to being LONGER rather than merely different.
    """
    t = hardened.to_json()["Resources"]
    db = next(r for r in t.values() if r["Type"] == "AWS::RDS::DBInstance")["Properties"]
    daily = _plans_by_name(hardened)["reep-daily"]
    vault = next(r for r in t.values() if r["Type"] == "AWS::Backup::BackupVault")["Properties"]
    rule = daily["BackupPlanRule"][0]
    n = db["BackupRetentionPeriod"]
    assert rule["Lifecycle"]["DeleteAfterDays"] == n
    assert rule["CopyActions"][0]["Lifecycle"]["DeleteAfterDays"] == n
    assert vault["LockConfiguration"]["MinRetentionDays"] == n
    assert n >= 35


def test_the_vault_is_locked_in_governance_mode_by_default(hardened: Template) -> None:
    vault = next(r for r in hardened.to_json()["Resources"].values() if r["Type"] == "AWS::Backup::BackupVault")["Properties"]
    assert "MinRetentionDays" in vault["LockConfiguration"]
    assert "ChangeableForDays" not in vault["LockConfiguration"], "compliance mode is irreversible and must be opt-in"


def test_recovery_points_are_copied_to_the_dr_region(hardened: Template) -> None:
    hardened.has_resource_properties(
        "AWS::Backup::BackupPlan",
        {"BackupPlan": Match.object_like({"BackupPlanRule": Match.array_with([Match.object_like({"CopyActions": Match.array_with([Match.object_like({"DestinationBackupVaultArn": Match.string_like_regexp("ap-southeast-1")})])})])})},
    )


def test_a_weekly_restore_test_exists_for_the_database(hardened: Template) -> None:
    hardened.resource_count_is("AWS::Backup::RestoreTestingPlan", 1)
    hardened.has_resource_properties("AWS::Backup::RestoreTestingSelection", {"ProtectedResourceType": "RDS"})
    hardened.has_resource_properties("AWS::CloudWatch::Alarm", {"AlarmName": "reep-backup-job-failed"})


#: How far a backup job must stay from an RDS window. RDS rejects a snapshot
#: taken "inside or too close to" the maintenance window without saying how
#: close is too close, so an hour is the margin we can defend.
BACKUP_WINDOW_CLEARANCE_MINUTES = 60


def _minutes_past_midnight(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _window_bounds(window: str) -> tuple[int, int]:
    """`sun:21:30-sun:22:30` or `20:30-21:30` -> (start, end) in minutes UTC."""
    start, end = window.split("-")
    # The maintenance window carries a weekday prefix; the backup window does not.
    strip_day = lambda part: part.split(":", 1)[1] if part.count(":") == 2 else part  # noqa: E731
    return _minutes_past_midnight(strip_day(start)), _minutes_past_midnight(strip_day(end))


def test_backup_schedule_clears_the_rds_windows(hardened: Template) -> None:
    """The AWS Backup rule must not fire inside — or within an hour of — either
    RDS window.

    It fired at 21:30 UTC, which is the exact minute `PreferredMaintenanceWindow`
    opens on a Sunday, and RDS refused the snapshot: "could not start because it
    is either inside or too close to the weekly maintenance window". So every
    Sunday's DATABASE backup failed while the EFS half of the same plan kept
    succeeding — the vault looked healthy, and the newest RDS recovery point
    quietly went stale. Nothing reported it, because the alarm that would
    (`reep-backup-job-failed`) ships in this same harden phase, which had never
    been deployed. Found against the live account on 2026-09-07, where the
    2026-09-06 job was FAILED and the newest RDS point was two days old.

    This asserts the relationship, not the literal time, because moving either
    window is a legitimate fix and pinning one string would only pin the bug.
    """
    # EVERY rule of EVERY plan. This read one plan, which was every plan until
    # the archive tier added a second — and a clearance guard that checks only
    # the plan CDK happened to emit first is the guard that was not there on
    # 2026-09-07.
    rules = [rule for plan in _plans_by_name(hardened).values() for rule in plan["BackupPlanRule"]]
    assert rules, "the harden phase must define at least one backup rule"

    db = next(iter(hardened.find_resources("AWS::RDS::DBInstance").values()))["Properties"]
    windows = {
        "maintenance": _window_bounds(db["PreferredMaintenanceWindow"]),
        "automated backup": _window_bounds(db["PreferredBackupWindow"]),
    }

    for rule in rules:
        # cron(minute hour day-of-month month day-of-week year) — all six
        # fields, not just the two this reads. The regex matched `.*` after the
        # hour, so the day-of-month field was unchecked: a monthly rule, whose
        # whole meaning is in that field, could carry anything at all and this
        # guard would still pass. AWS Backup uses CloudWatch Events cron, where
        # exactly one of day-of-month / day-of-week must be `?`, and matching
        # the shape is how a hand-written expression gets caught here rather
        # than by a rule that silently never fires.
        fields = re.fullmatch(
            r"cron\((\d{1,2}) (\d{1,2}) (\S+) (\S+) (\S+) (\S+)\)", rule["ScheduleExpression"]
        )
        assert fields, f"unparseable schedule expression {rule['ScheduleExpression']!r}"
        day_of_month, day_of_week = fields.group(3), fields.group(5)
        assert (day_of_month == "?") != (day_of_week == "?"), (
            f"backup rule {rule['RuleName']!r} has day-of-month {day_of_month!r} and "
            f"day-of-week {day_of_week!r}: EventBridge cron requires exactly one of "
            "them to be '?', and rejects the expression otherwise"
        )
        fires_at = int(fields.group(2)) * 60 + int(fields.group(1))
        for name, (start, end) in windows.items():
            too_close = start - BACKUP_WINDOW_CLEARANCE_MINUTES <= fires_at <= end + BACKUP_WINDOW_CLEARANCE_MINUTES
            assert not too_close, (
                f"backup rule {rule['RuleName']!r} fires at "
                f"{fires_at // 60:02d}:{fires_at % 60:02d} UTC, inside or within "
                f"{BACKUP_WINDOW_CLEARANCE_MINUTES} min of the RDS {name} window "
                f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} — "
                "every job that lands there fails, and it fails silently"
            )


# --------------------------------------------------------------------------- #
#  The archive tier — the second plan that outlives RDS's 35-day ceiling        #
# --------------------------------------------------------------------------- #


def _archive_rule(t: Template) -> dict:
    return _plans_by_name(t)["reep-archive"]["BackupPlanRule"][0]


def test_the_archive_tier_outlives_the_daily_one(hardened: Template) -> None:
    """The point of the whole tier: a number RDS's 35-day cap cannot express.

    `backupRetentionDays` is capped at 35 because RDS refuses more on its own
    automated backups, and until this plan existed that cap reached every copy
    in both regions — so a record lost 36 days ago was gone everywhere at once.
    """
    daily = _plans_by_name(hardened)["reep-daily"]["BackupPlanRule"][0]["Lifecycle"]["DeleteAfterDays"]
    archive = _archive_rule(hardened)["Lifecycle"]["DeleteAfterDays"]
    assert archive > daily, "an archive shorter than the daily rule is not an archive"
    assert archive > 35, "the tier exists to pass RDS's ceiling; at or below it, it buys nothing"


def test_the_archive_rule_never_asks_for_cold_storage(hardened: Template) -> None:
    """AWS Backup does not support cold storage for RDS, and says so by IGNORING
    the setting rather than refusing it.

    A `MoveToColdStorageAfterDays` here would synthesise, deploy, report success
    and do nothing for the database — the same silent shape as the Sunday
    backup that failed for weeks against a green-looking vault. The only honest
    lifecycle for an RDS rule is a delete-after.
    """
    rule = _archive_rule(hardened)
    assert "MoveToColdStorageAfterDays" not in rule["Lifecycle"], (
        "AWS Backup ignores cold-storage transitions for resource types that do "
        "not support them, RDS among them: this clause would be a no-op nothing reports"
    )
    for copy in rule.get("CopyActions", []):
        assert "MoveToColdStorageAfterDays" not in copy["Lifecycle"]


def test_the_archive_selection_is_the_database_alone(hardened: Template) -> None:
    """A selection is PLAN-scoped, not rule-scoped, which is why this is a second
    plan rather than a second rule.

    The daily selection covers the database AND the EFS file system. A
    multi-year rule inheriting that would keep every student's resume,
    marksheet, certificate, staff signature and recorded voice in a locked
    vault for years — outliving INTERVIEW_RETENTION_DAYS and the "files go
    before rows" rule both purge modules are built on, with nothing reporting
    it.
    """
    selections = [
        r["Properties"]["BackupSelection"]
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Backup::BackupSelection"
    ]
    archive = next(s for s in selections if s["SelectionName"] == "reep-db-archive")
    assert len(archive["Resources"]) == 1, (
        f"the archive selection covers {len(archive['Resources'])} resources; it must "
        "be the database and nothing else"
    )
    rendered = json.dumps(archive["Resources"])
    assert "elasticfilesystem" not in rendered and "FileSystem" not in rendered, (
        "the EFS file system reached the archive selection — student documents would "
        "be retained for years by a rule written for the database"
    )


def test_the_archive_rule_bounds_its_own_start(hardened: Template) -> None:
    """AWS Backup runs ONE job per resource. A monthly job landing on the daily
    one does not run twice — it queues, and is cancelled if the queue outlasts
    its start window. Without an explicit window that cancellation is the
    default behaviour on the one day a month this point is taken."""
    rule = _archive_rule(hardened)
    assert rule["StartWindowMinutes"] >= 60
    assert rule["CompletionWindowMinutes"] >= rule["StartWindowMinutes"] + 60, (
        "AWS Backup requires the completion window to exceed the start window by at "
        "least an hour"
    )
    daily = _plans_by_name(hardened)["reep-daily"]["BackupPlanRule"][0]
    both = {r["RuleName"]: r["ScheduleExpression"] for r in (rule, daily)}
    hours = {name: int(re.fullmatch(r"cron\(\d{1,2} (\d{1,2}) .*\)", expr).group(1)) for name, expr in both.items()}
    assert len(set(hours.values())) == 2, f"the two rules fire in the same hour: {both}"


def test_the_archive_point_is_copied_to_the_dr_region(hardened: Template) -> None:
    """And with a lifecycle at least the DR vault's minimum — a copy shorter
    than the destination lock's MinRetentionDays fails the copy job."""
    rule = _archive_rule(hardened)
    copies = rule["CopyActions"]
    assert copies, "an archive that exists in one region only is not a disaster plan"
    assert "ap-southeast-1" in json.dumps(copies[0]["DestinationBackupVaultArn"])
    assert copies[0]["Lifecycle"]["DeleteAfterDays"] == rule["Lifecycle"]["DeleteAfterDays"]


def test_the_archive_tier_is_off_unless_it_is_asked_for() -> None:
    """Zero is off, and off is the code default: a synth with no context renders
    the stack that exists today. cdk.json is where the number is turned on,
    because the number is a decision about how long a deleted student stays
    restorable."""
    off = _core("harden", archiveRetentionDays=0)
    assert "reep-archive" not in _plans_by_name(off), "archiveRetentionDays=0 still built the plan"
    off.resource_count_is("AWS::Backup::BackupPlan", 1)


def test_the_import_mirror_grows_no_archive_plan(imported: Template) -> None:
    """The import template is a MIRROR of what exists. CloudFormation refuses an
    import template that adds a resource it cannot adopt, so a second plan here
    would break the cutover it was written after."""
    imported.resource_count_is("AWS::Backup::BackupPlan", 1)
    assert "reep-archive" not in _plans_by_name(imported)


@pytest.mark.parametrize("bad", [35, 20, 36501])
def test_an_archive_number_that_is_not_an_archive_is_refused(bad: int) -> None:
    """Shorter than or equal to the daily rule is not an archive, and past AWS
    Backup's own ceiling is not a lifecycle. Both are caught at synth rather
    than by a copy job failing in a month's time."""
    with pytest.raises(ValueError):
        _core("harden", archiveRetentionDays=bad)


def test_an_alarm_reports_a_plan_that_stopped_running(hardened: Template) -> None:
    """The three failure alarms cannot see a job that never started.

    They fire on NumberOfBackupJobs*Failed with missing data NOT breaching,
    which is right for them — a day with no failures publishes no datapoint.
    The consequence is that a plan which stops running entirely is the one
    state none of them reports. AWS Backup publishes a metric only for a
    nonzero value, so "nothing completed" IS missing data, and BREACHING is
    what makes the absence legible.
    """
    hardened.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "reep-backup-no-job-completed",
            "MetricName": "NumberOfBackupJobsCompleted",
            "ComparisonOperator": "LessThanThreshold",
            "TreatMissingData": "breaching",
        },
    )


def test_multi_az_follows_the_flag_in_harden() -> None:
    """The flag is spelled out on BOTH sides, because cdk.json now turns it
    off: a bare `_core("harden")` reads the repository's decision, not the
    code default, and the code default (on) is still what a context-free
    synth renders."""
    on = _core("harden", dbMultiAz="true")
    on.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": True})
    opted_out = _core("harden", dbMultiAz="false")
    opted_out.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": False})


def test_cdk_json_carries_the_single_az_database_the_owner_chose(hardened: Template) -> None:
    """2026-10-03: the owner chose the "middle option" of the cost review —
    one managed database instance rather than two. A database failure becomes
    a ~20-30 minute RDS recovery instead of a ~1 minute failover; backups,
    point-in-time restore and the 35-day retention are unchanged.

    WRITTEN DOWN, not passed with -c: a flag set at deploy time and not
    persisted is not configuration (the apiArm64 lesson). If this flips back
    to true, the next reep-core deploy converts the database to Multi-AZ again
    and roughly doubles its line on the bill, with nothing on any screen
    saying so — so the flip must be a decision in a diff, not an accident."""
    assert CDK_JSON_CONTEXT.get("dbMultiAz") is False, (
        "cdk.json no longer holds dbMultiAz=false — the next reep-core deploy would "
        "convert the database back to Multi-AZ (docs/cost-review-2026-09.md §3c)"
    )
    hardened.has_resource_properties(
        "AWS::RDS::DBInstance",
        {"MultiAZ": False, "BackupRetentionPeriod": 35, "DBInstanceClass": "db.t4g.small"},
    )


def test_task_role_may_send_mail_only_for_the_verified_identity(hardened: Template) -> None:
    hardened.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "reep-api-task",
            "Policies": Match.array_with(
                [
                    Match.object_like(
                        {
                            "PolicyName": "send-mail",
                            "PolicyDocument": Match.object_like(
                                {
                                    "Statement": Match.array_with(
                                        [
                                            Match.object_like(
                                                {
                                                    "Action": ["ses:SendEmail", "ses:SendRawEmail"],
                                                    "Resource": "arn:aws:ses:ap-south-1:123456789012:identity/bgscet.ac.in",
                                                    "Condition": {"StringEquals": {"ses:FromAddress": "reep@bgscet.ac.in"}},
                                                }
                                            )
                                        ]
                                    )
                                }
                            ),
                        }
                    )
                ]
            ),
        },
    )
    hardened.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"ContainerDefinitions": Match.array_with([Match.object_like({"Environment": Match.array_with([{"Name": "SES_FROM_ADDRESS", "Value": "reep@bgscet.ac.in"}])})])},
    )


def test_the_task_role_may_also_send_under_the_configuration_set(hardened: Template) -> None:
    """The permission whose absence took all mail down for eighty minutes.

    2026-09-15: `SES_CONFIGURATION_SET` reached the task in the same deploy that
    introduced it, so every send named `reep-transactional` while the role held
    only the identity. Every one was refused, `deliver_once` swallowed the
    exception into `mail_logs.error` without logging, and the endpoints above
    kept answering 200 -- so a rejected applicant got no rejection and nothing
    anywhere said why.

    Two halves, and the test asserts BOTH, because each is load-bearing in a
    different direction: the identity statement keeps its `ses:FromAddress`
    condition (drop it and a compromised task can spoof any sender at the
    domain), and the configuration-set statement must NOT carry one (that key
    is absent when SES evaluates the set, and a StringEquals on an absent key
    denies -- which is the outage again, arrived at from the other side).
    """
    role = next(
        r for r in _core("harden", sesFromAddress="reep@bgscet.ac.in",
                         sesIdentityDomain="bgscet.ac.in").to_json()["Resources"].values()
        if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == "reep-api-task"
    )
    send_mail = next(p for p in role["Properties"]["Policies"] if p["PolicyName"] == "send-mail")
    by_sid = {s.get("Sid"): s for s in send_mail["PolicyDocument"]["Statement"]}

    assert set(by_sid) == {
        "SendFromVerifiedIdentity",
        "SendUnderConfigurationSet",
        "ReadAndLiftOneSuppression",
    }, (
        "the send-mail policy must name the identity AND the configuration set; a send that names a "
        "set is authorised against both"
    )
    identity = by_sid["SendFromVerifiedIdentity"]
    assert identity["Resource"] == "arn:aws:ses:ap-south-1:123456789012:identity/bgscet.ac.in"
    assert identity["Condition"] == {"StringEquals": {"ses:FromAddress": "reep@bgscet.ac.in"}}

    configuration_set = by_sid["SendUnderConfigurationSet"]
    assert configuration_set["Resource"] == (
        "arn:aws:ses:ap-south-1:123456789012:configuration-set/reep-transactional"
    )
    assert "Condition" not in configuration_set, (
        "ses:FromAddress is not in context when SES evaluates the configuration set, so a condition "
        "on it denies every send -- this is the 2026-09-15 outage rewritten as a passing-looking policy"
    )


def test_the_task_can_ask_about_one_suppression_and_never_list_them_all() -> None:
    """`ses:GetSuppressedDestination` YES, `ses:ListSuppressedDestinations` NO.

    An address SES has suppressed after a bounce or a complaint is accepted on
    every later send and delivered nowhere, so without the Get the product
    cannot tell "we emailed them" from "nothing we send can reach them" -- the
    2026-09-17 report, where a student signed in with Google and no code ever
    arrived on any path. The Delete is the remedy, on the office's say-so and
    audited, so a fixed mailbox does not need somebody with an AWS console.

    List is refused because the suppression list is a roster of people by
    another name: every address the deployment has ever bounced, which a
    compromised task could read out in one call. Nothing here needs it --
    `mail_transport` only ever asks about an address it is already holding.

    Both are account-level in SESv2 and take no resource-level permission, so
    `*` is the only resource that authorises them; the fence is the ACTION list
    and this test is what keeps List off it.
    """
    role = next(
        r for r in _core("harden", sesFromAddress="reep@bgscet.ac.in",
                         sesIdentityDomain="bgscet.ac.in").to_json()["Resources"].values()
        if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == "reep-api-task"
    )
    send_mail = next(p for p in role["Properties"]["Policies"] if p["PolicyName"] == "send-mail")
    granted = {
        action
        for statement in send_mail["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    }
    assert "ses:GetSuppressedDestination" in granted, (
        "without this the suppression check fails open on every send and the feature is inert in "
        "production while every test passes"
    )
    assert "ses:DeleteSuppressedDestination" in granted
    assert not any(a.startswith("ses:List") for a in granted), (
        "listing the suppression list hands a compromised task every address the deployment has "
        "ever bounced; the product only ever asks about one it already holds"
    )


def test_a_configuration_set_on_the_task_is_never_ungranted() -> None:
    """The two halves of this feature ship together or not at all.

    The variable and the grant are set from the same context key, so the state
    that caused the outage -- the task naming a set the role cannot use -- is
    not reachable from any combination of context. Pinned rather than left to
    the reading, because the variable and the policy are 200 lines apart.
    """
    template = _core("harden", sesFromAddress="reep@bgscet.ac.in",
                     sesIdentityDomain="bgscet.ac.in").to_json()["Resources"]
    task_definition = next(r for r in template.values() if r["Type"] == "AWS::ECS::TaskDefinition")
    env = {e["Name"]: e.get("Value") for e in task_definition["Properties"]["ContainerDefinitions"][0]["Environment"]}
    named_set = env.get("SES_CONFIGURATION_SET")

    role = next(r for r in template.values()
                if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == "reep-api-task")
    send_mail = next(p for p in role["Properties"]["Policies"] if p["PolicyName"] == "send-mail")
    granted = {
        s["Resource"] for s in send_mail["PolicyDocument"]["Statement"]
        if isinstance(s.get("Resource"), str) and ":configuration-set/" in s["Resource"]
    }

    if named_set:
        assert any(r.endswith(f":configuration-set/{named_set}") for r in granted), (
            f"the task is told to send under {named_set!r} and the role cannot use it -- every send fails silently"
        )
    else:
        assert not granted, "a configuration set is granted that nothing names"


def test_a_mail_send_that_fails_raises_an_alarm(hardened: Template) -> None:
    """The alarm the 2026-09-15 outage did not have.

    Eighty minutes of total mail failure produced no alarm, no log line and no
    failed request: `deliver_once` swallows the driver's exception so a decision
    stands whether or not its mail went, and the only witness was a
    `mail_logs.error` column nothing reads.

    NOT_BREACHING is right here and would be wrong for the backup sweep: no
    failures publishes no datapoint, and a quiet hour means mail is FINE. The
    state this cannot see -- the api not running at all -- belongs to
    `reep-no-healthy-api`, which treats missing data as breaching for exactly
    that reason.
    """
    hardened.has_resource_properties(
        "AWS::Logs::MetricFilter",
        {
            "FilterName": "mail-send-failed",
            "FilterPattern": '"Mail send failed"',
            "MetricTransformations": Match.array_with(
                [Match.object_like({"MetricNamespace": "REEP/Mail", "MetricName": "MailSendFailed"})]
            ),
        },
    )
    hardened.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "reep-mail-send-failed",
            "Namespace": "REEP/Mail",
            "MetricName": "MailSendFailed",
            "Threshold": 1,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "TreatMissingData": "notBreaching",
        },
    )


# ------------------------------------------------------ ses (the mail path) --


def test_the_ses_resources_stay_out_until_they_are_adopted() -> None:
    """`sesManaged` defaults OFF, and the default is load-bearing.

    The identity, the configuration set, its event destination, the
    notifications topic and both reputation alarms EXIST -- made by hand in the
    console on 2026-09-09/10, before any of this was in a repository. A
    CloudFormation CREATE for an SES identity that is already verified fails
    with AlreadyExists and rolls the stack back, and a rollback of reep-core is
    a rollback of the whole api. So they appear only once somebody has run the
    adoption in docs/ses-mail.md and flipped the key.
    """
    default_harden = _core("harden")
    types = {r["Type"] for r in default_harden.to_json()["Resources"].values()}
    assert not {t for t in types if t.startswith("AWS::SES::")}, (
        "an SES resource synthesised with sesManaged off -- `cdk deploy` would try to CREATE what already exists"
    )
    # And the half that was always managed is still there: a template that can
    # rebuild the PERMISSION to send but not the identity is the state this
    # whole block exists to stop being the only one available.
    default_harden.has_resource_properties(
        "AWS::IAM::Role",
        {"RoleName": "reep-api-task", "Policies": Match.array_with([Match.object_like({"PolicyName": "send-mail"})])},
    )


def test_the_ses_mirror_is_what_is_live_and_not_an_improvement_on_it(hardened: Template) -> None:
    """Property for property, including the two that look wrong.

    `cdk import` does not compare properties -- it adopts whatever the template
    says -- so the FIRST DEPLOY after the adoption is what sends these. A mirror
    that quietly raised TlsPolicy to REQUIRE would change how mail is delivered
    in the commit that was supposed to change nothing.
    """
    hardened.has_resource_properties(
        "AWS::SES::ConfigurationSet",
        {
            "Name": "reep-transactional",
            "DeliveryOptions": {"TlsPolicy": "OPTIONAL"},
            "ReputationOptions": {"ReputationMetricsEnabled": False},
            "SendingOptions": {"SendingEnabled": True},
        },
    )
    hardened.has_resource_properties(
        "AWS::SES::ConfigurationSetEventDestination",
        {
            "ConfigurationSetName": "reep-transactional",
            "EventDestination": Match.object_like(
                {
                    "Name": "sns-bounces-complaints",
                    "Enabled": True,
                    # SEND and DELIVERY ride along with the two that matter:
                    # without them a silent failure and a healthy quiet week
                    # publish the same nothing.
                    "MatchingEventTypes": ["BOUNCE", "COMPLAINT", "DELIVERY", "REJECT", "SEND"],
                }
            ),
        },
    )
    hardened.has_resource_properties("AWS::SNS::Topic", {"TopicName": "reep-ses-notifications"})


def test_the_identity_signs_with_dkim_and_declares_neither_signing_keys_nor_a_mail_from() -> None:
    """The two omissions, and each is a way to stop mail with a green deploy.

    `DkimSigningAttributes` is how a deploy ROTATES SES-managed keys: three new
    CNAMEs published to a zone this team does not hold, and nothing delivers
    until somebody else adds them. `MailFromAttributes` publishes a subdomain
    whose MX and SPF records nobody has added either. Neither is live, and
    neither may arrive in a template whose whole job is to adopt.
    """
    identity = next(
        r
        for r in _core("harden", sesManaged=True).to_json()["Resources"].values()
        if r["Type"] == "AWS::SES::EmailIdentity"
    )
    props = identity["Properties"]
    assert props["EmailIdentity"] == "sast-skills.com"
    assert props["DkimAttributes"] == {"SigningEnabled": True}
    assert props["FeedbackAttributes"] == {"EmailForwardingEnabled": True}
    # The identity default AND the per-send name are both declared on purpose:
    # an edit to the identity kills the default silently, a missing set makes a
    # named send fail loudly, and neither alone covers both directions.
    assert props["ConfigurationSetAttributes"] == {"ConfigurationSetName": "reep-transactional"}
    assert "DkimSigningAttributes" not in props, "a deploy would rotate the DKIM keys and stop mail until DNS catches up"
    assert "MailFromAttributes" not in props, "a deploy would publish a MAIL FROM subdomain with no MX or SPF behind it"


def test_the_reputation_alarms_sit_at_the_numbers_aws_suspends_sending_at(hardened: Template) -> None:
    """5% bounces and 0.1% complaints, per AWS, and MISSING is not BREACHING.

    The backup alarms treat absence as failure because a plan that stops running
    IS the incident. Absence here means nobody was mailed this hour, which on a
    college's volume is most hours -- an alarm that shouts on every quiet hour
    is one somebody filters.
    """
    for name, metric, threshold in (
        ("reep-ses-bounce-rate", "Reputation.BounceRate", 0.05),
        ("reep-ses-complaint-rate", "Reputation.ComplaintRate", 0.001),
    ):
        hardened.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "AlarmName": name,
                "Namespace": "AWS/SES",
                "MetricName": metric,
                "Threshold": threshold,
                "ComparisonOperator": "GreaterThanThreshold",
                "Period": 3600,
                "TreatMissingData": "missing",
            },
        )


def test_the_task_definition_carries_the_whole_mail_trio(hardened: Template) -> None:
    """A sender, the configuration set named on every send, and the leave switch.

    SES_CONFIGURATION_SET is the one that reads as redundant: the set is already
    the identity's default. It is not redundant, it is the half that cannot be
    lost quietly -- SES refuses a send naming a set that does not exist, where
    an identity whose default was edited away keeps sending and stops reporting.
    """
    for name, value in (
        ("SES_FROM_ADDRESS", "reep@bgscet.ac.in"),
        ("SES_CONFIGURATION_SET", "reep-transactional"),
        ("LEAVE_MAIL_ENABLED", "true"),
    ):
        hardened.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            {
                "ContainerDefinitions": Match.array_with(
                    [Match.object_like({"Environment": Match.array_with([{"Name": name, "Value": value}])})]
                )
            },
        )


def test_leave_mail_without_a_sender_is_refused_at_synth() -> None:
    """The one combination worse than either half.

    Mail switched on over the console transport writes a `mail_logs` row reading
    SENT about a message that reached NOBODY, and that row is the only thing
    anyone looks at afterwards -- the shape of the failure that killed
    PENDING_VERIFICATION. Refused here, where it costs nothing, rather than
    found in a mail_logs table months later.
    """
    with pytest.raises(ValueError):
        _core("harden", leaveMailEnabled=True, sesFromAddress="")


def test_adopting_ses_without_naming_what_it_adopts_is_refused() -> None:
    with pytest.raises(ValueError):
        _core("harden", sesManaged=True, sesIdentityDomain="")
    with pytest.raises(ValueError):
        _core("harden", sesManaged=True, sesConfigurationSet="")


def test_the_mail_variables_are_held_back_from_step_9a() -> None:
    """Same rule as SES_FROM_ADDRESS, for the same reason, and worth its own name.

    A task definition is IMMUTABLE: adding an environment variable registers a
    new revision the service rolls onto. Step 9a exists so an ECS
    circuit-breaker rollback cannot undo a Multi-AZ conversion in the same
    update. `test_the_database_half_does_not_touch_the_ecs_trio` proves the
    whole resource byte for byte; this one says WHICH variables and why, so the
    next person adding one reads a reason rather than a diff.
    """
    step_9a = _core("harden", hardenEcs="false", sesManaged=True, leaveMailEnabled=True)
    task_definitions = [r for r in step_9a.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::TaskDefinition"]
    assert task_definitions
    for task_definition in task_definitions:
        for container in task_definition["Properties"]["ContainerDefinitions"]:
            names = {e["Name"] for e in container.get("Environment", [])}
            assert not names & {"SES_FROM_ADDRESS", "SES_CONFIGURATION_SET", "LEAVE_MAIL_ENABLED"}, (
                "a mail variable reached step 9a -- that is an api roll during the Multi-AZ conversion"
            )


def test_the_ses_import_map_names_every_resource_the_adoption_must_carry() -> None:
    """The map and the template cannot drift apart silently.

    A logical id in the map that the template does not render is a `cdk import`
    that adopts NOTHING for that resource, and a first deploy that tries to
    create it beside the real one. The SUBSCRIPTION is deliberately absent: an
    AWS::SNS::Subscription cannot be imported, so the adoption run must not
    carry it -- which is why docs/ses-mail.md writes both commands out instead
    of leaving the flag to memory.
    """
    template = _core("harden", sesManaged=True, sesNotificationsEmail="ops@bgscet.ac.in").to_json()["Resources"]
    mapped = json.loads((Path(__file__).resolve().parents[1] / "ses-import-map.json").read_text(encoding="utf-8"))

    importable = {
        lid: r["Type"]
        for lid, r in template.items()
        if (r["Type"].startswith("AWS::SES::") or lid.startswith("Ses")) and r["Type"] != "AWS::SNS::Subscription"
    }
    assert set(mapped) == set(importable), f"map and template disagree: {sorted(set(mapped) ^ set(importable))}"

    # The identifier KEYS are the registry's, not a guess: eight of the first
    # cutover map's thirty were wrong. These were read out of
    # `get-template-summary --query ResourceIdentifierSummaries`.
    keys = {
        "AWS::SES::EmailIdentity": {"EmailIdentity"},
        "AWS::SES::ConfigurationSet": {"Name"},
        # Composite, and the half that gets dropped is ConfigurationSetName.
        "AWS::SES::ConfigurationSetEventDestination": {"Id", "ConfigurationSetName"},
        "AWS::SNS::Topic": {"TopicArn"},
        "AWS::CloudWatch::Alarm": {"AlarmName"},
    }
    for lid, cfn_type in importable.items():
        assert set(mapped[lid]) == keys[cfn_type], f"{lid} ({cfn_type}) carries the wrong identifier keys"
        assert all(str(v).strip() for v in mapped[lid].values()), f"{lid} has a blank identifier"


# ------------------------------------------------- the identity ledger --


def test_the_ledger_bucket_is_versioned_and_object_locked(hardened: Template) -> None:
    """Both, at creation, or the bucket cannot do its job.

    OBJECT LOCK CANNOT BE ADDED LATER. A ledger bucket created without it has to
    be replaced -- every object copied, under a new name, by hand -- which is
    why this is asserted rather than left to a follow-up. Versioning is the
    other half: a day's object is rewritten by a re-run, so the versions ARE the
    history.
    """
    hardened.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": Match.string_like_regexp(r"^reep-identity-ledger-"),
            "VersioningConfiguration": {"Status": "Enabled"},
            "ObjectLockEnabled": True,
            "ObjectLockConfiguration": Match.object_like(
                {"Rule": {"DefaultRetention": Match.object_like({"Mode": "GOVERNANCE"})}}
            ),
        },
    )


def test_the_ledger_bucket_has_no_lifecycle_rule(hardened: Template) -> None:
    """The absence of an expiry is the whole feature.

    Every other store in this stack carries one -- alb-logs expires at 90 days
    -- and the ledger exists precisely because `backupRetentionDays` caps every
    database artefact at 35. A lifecycle rule here would re-impose the ceiling
    this bucket was created to escape, and it would read as protection right up
    until somebody asked for something older than it.
    """
    buckets = [
        r["Properties"]
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::Bucket"
        and str(r["Properties"].get("BucketName", "")).startswith("reep-identity-ledger-")
    ]
    assert len(buckets) == 1, "expected exactly one identity ledger bucket"
    assert "LifecycleConfiguration" not in buckets[0]


def test_the_import_phase_has_no_ledger_bucket(imported: Template) -> None:
    """It is a resource this stack ADDS, so the mirror must not carry it."""
    names = [
        str(r["Properties"].get("BucketName", ""))
        for r in imported.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::Bucket"
    ]
    assert not [n for n in names if n.startswith("reep-identity-ledger-")]


def test_the_task_may_write_the_ledger_and_never_weaken_it(hardened: Template) -> None:
    """PutObject only.

    A writer that could delete its own output, or shorten its own retention, is
    not being protected by Object Lock -- it is being asked politely. The grant
    carries no Delete*, no PutBucketLifecycle and no PutObjectRetention, and
    this test is what keeps a later convenience from adding one.
    """
    role = next(
        r for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == "reep-api-task"
    )
    policy = next(
        p for p in role["Properties"]["Policies"] if p["PolicyName"] == "write-identity-ledger"
    )
    actions = [
        a
        for st in policy["PolicyDocument"]["Statement"]
        for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
    ]
    assert actions == ["s3:PutObject"], actions


def test_the_ledger_runs_before_the_retention_sweep(hardened: Template) -> None:
    """Ordering is the only dependency two schedules can have, so it is asserted.

    `app/retention.py` is the one scheduled destructor in the product. A ledger
    written AFTER it is a ledger that never saw whatever it removed, and nothing
    would report the difference -- both jobs would be green.
    """
    schedules = {
        r["Properties"]["Name"]: r["Properties"]["ScheduleExpression"]
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
    }
    ledger = schedules["reep-identity-ledger-daily"]
    sweep = schedules["reep-retention-daily"]
    hour = lambda expr: int(re.match(r"cron\((\d+) (\d+)", expr).group(2))
    assert hour(ledger) < hour(sweep), f"ledger {ledger} must run before sweep {sweep}"


def test_the_ledger_schedule_runs_the_ledger(hardened: Template) -> None:
    """The command is the contract: a schedule pointing at the wrong module is a
    green job that writes nothing."""
    sched = next(
        r for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
        and r["Properties"]["Name"] == "reep-identity-ledger-daily"
    )
    assert "app.export_identity" in sched["Properties"]["Target"]["Input"]


# --------------------------------------------------- the logical backup (M2) --


def _dump_buckets(t: Template, prefix: str) -> list[dict]:
    return [
        r["Properties"]
        for r in t.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::Bucket"
        and str(r["Properties"].get("BucketName", "")).startswith(prefix)
    ]


def test_both_dump_tiers_are_versioned_and_object_locked(hardened: Template) -> None:
    """The recovery matrix's "vault deleted / account compromised" row.

    A copy an attacker can delete does not answer that row, and Object Lock
    cannot be added to a bucket after creation -- a dump bucket created without
    it has to be REPLACED, every object copied by hand under a new name.
    """
    for prefix in ("reep-db-dumps-", "reep-db-archive-"):
        buckets = _dump_buckets(hardened, prefix)
        assert len(buckets) == 1, f"expected exactly one {prefix}* bucket"
        b = buckets[0]
        assert b["VersioningConfiguration"] == {"Status": "Enabled"}, prefix
        assert b["ObjectLockEnabled"] is True, prefix
        assert (
            b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Mode"]
            == "GOVERNANCE"
        ), prefix


def test_the_daily_lifecycle_fires_after_its_lock_lapses(hardened: Template) -> None:
    """THE SILENT FAILURE THIS WHOLE ARRANGEMENT IS SHAPED AROUND.

    A lifecycle expiration aimed at an object whose Object Lock retention has
    not lapsed is NOT an error. S3 re-evaluates it the next day, and the next,
    deleting nothing and reporting nothing -- so the bucket grows without bound
    behind a rule the console shows as working, and the first symptom is a bill.

    Setting the two numbers equal makes that a race on every single object.
    They must differ, and in this direction.
    """
    b = _dump_buckets(hardened, "reep-db-dumps-")[0]
    lock_days = b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Days"]
    rules = b["LifecycleConfiguration"]["Rules"]
    assert len(rules) == 1, rules
    assert rules[0]["ExpirationInDays"] > lock_days, (
        f"the daily dump's lifecycle expires at {rules[0]['ExpirationInDays']}d but its "
        f"Object Lock holds for {lock_days}d. The delete is refused and silently retried "
        "forever; nothing ever leaves the bucket."
    )
    # A versioned bucket's expiration writes a DELETE MARKER and leaves the
    # version behind, still stored and still billed. A rule without this half
    # looks right in the console and frees nothing.
    assert rules[0]["NoncurrentVersionExpiration"]["NoncurrentDays"] > lock_days


def test_the_archive_tier_has_no_lifecycle_rule(hardened: Template) -> None:
    """The ledger bucket's rule, for the same reason.

    This is the copy that outlives the 35-day ceiling. A lifecycle rule here --
    added later, by someone tidying up storage costs -- is that promise quietly
    expiring, and it would read as protection until the day it was asked for
    something older than the rule.
    """
    assert "LifecycleConfiguration" not in _dump_buckets(hardened, "reep-db-archive-")[0]


def test_the_two_tiers_are_separate_buckets(hardened: Template) -> None:
    """Not two prefixes, and the reason is not taste.

    S3 Object Lock's DEFAULT RETENTION IS BUCKET-WIDE. One bucket cannot hold
    both "the daily copy goes at 90 days" and "the monthly copy is kept for a
    decade"; the attempt produces one retention and two rules that disagree
    with it.
    """
    daily = _dump_buckets(hardened, "reep-db-dumps-")[0]
    archive = _dump_buckets(hardened, "reep-db-archive-")[0]
    assert daily["BucketName"] != archive["BucketName"]
    assert (
        archive["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Days"]
        > daily["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Days"]
    )


def test_the_import_phase_has_no_dump_buckets(imported: Template) -> None:
    """Resources this stack ADDS never appear in the mirror."""
    names = [
        str(r["Properties"].get("BucketName", ""))
        for r in imported.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::Bucket"
    ]
    assert not [n for n in names if n.startswith(("reep-db-dumps-", "reep-db-archive-"))]


def test_the_backup_task_can_write_the_dumps_and_never_read_them(
    hardened: Template,
) -> None:
    """PutObject to write; ListBucket to ask; s3:GetObject NOWHERE.

    These buckets hold a complete copy of every student record in the
    deployment. A task that can READ them is one compromise away from
    exfiltrating the whole database from the BACKUPS -- past every control on
    the database itself, and past rule 1 entirely.

    The trap is specific and easy to walk into: `head_object` is the obvious way
    to ask "is this month already archived", and S3 authorises HeadObject with
    s3:GetObject. `backup_database.month_is_archived` uses `list_objects_v2`
    instead, which returns key names and never contents.
    """
    role = next(
        r
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::IAM::Role"
        and r["Properties"].get("RoleName") == "reep-api-task"
    )
    policies = {p["PolicyName"]: p for p in role["Properties"]["Policies"]}

    def actions_of(name: str) -> list[str]:
        return [
            a
            for st in policies[name]["PolicyDocument"]["Statement"]
            for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
        ]

    assert actions_of("write-db-dumps") == ["s3:PutObject"]
    assert actions_of("list-db-archive") == ["s3:ListBucket"]

    for name, policy in policies.items():
        for st in policy["PolicyDocument"]["Statement"]:
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            resources = st.get("Resource", [])
            resources = resources if isinstance(resources, list) else [resources]
            if any("db-dumps" in str(r) or "db-archive" in str(r) for r in resources):
                assert not [
                    a for a in acts if a.startswith(("s3:Get", "s3:Delete"))
                ], f"policy {name} can read or delete a dump: {acts}"


def test_the_dump_runs_before_the_sweep_and_outside_the_rds_backup_window(
    hardened: Template,
) -> None:
    """Three clocks, and the dump has to clear all of them.

    * BEFORE the retention sweep, the ledger's argument: `app/retention.py` is
      the one scheduled destructor in the product, and a dump taken after it
      never saw what it removed. Both jobs would be green.
    * OUTSIDE the RDS backup window. pg_dump is a long read over every table;
      landing it inside the window puts that load on the instance while the
      snapshot is being taken.
    """
    res = hardened.to_json()["Resources"]
    schedules = {
        r["Properties"]["Name"]: r["Properties"]["ScheduleExpression"]
        for r in res.values()
        if r["Type"] == "AWS::Scheduler::Schedule"
    }

    def minutes(expr: str) -> int:
        m = re.match(r"cron\((\d+) (\d+)", expr)
        return int(m.group(2)) * 60 + int(m.group(1))

    dump = minutes(schedules["reep-db-dump-daily"])
    assert dump < minutes(schedules["reep-retention-daily"]), (
        "the logical dump must run before the retention sweep, or it never sees "
        "what the sweep removed"
    )

    db = next(r for r in res.values() if r["Type"] == "AWS::RDS::DBInstance")
    window = db["Properties"]["PreferredBackupWindow"]  # "HH:MM-HH:MM"
    start, end = window.split("-")
    to_min = lambda hm: int(hm[:2]) * 60 + int(hm[3:])
    assert not (to_min(start) <= dump <= to_min(end)), (
        f"the dump at {schedules['reep-db-dump-daily']} lands inside the RDS backup "
        f"window {window}"
    )


def test_the_dump_schedule_runs_the_dump(hardened: Template) -> None:
    """A schedule pointing at the wrong module is a green job that backs up
    nothing."""
    sched = next(
        r
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
        and r["Properties"]["Name"] == "reep-db-dump-daily"
    )
    assert "app.backup_database" in sched["Properties"]["Target"]["Input"]


# --------------------------------------------- the permanent document archive --


def _cron_minutes(expr: str) -> int:
    """Minutes past midnight UTC for a `cron(m h ...)` expression.

    Deliberately finer than the `hour()` lambda the ledger's ordering test uses.
    That comparison is enough while the jobs sit whole hours apart, and it
    silently stops meaning anything the day two of them land in the same hour --
    which is a plausible edit, since every one of these schedules is placed
    against the RDS backup window rather than against a clean hour.
    """
    m = re.match(r"cron\((\d+) (\d+)", expr)
    assert m, expr
    return int(m.group(2)) * 60 + int(m.group(1))



def test_the_document_archive_is_versioned_and_object_locked(hardened: Template) -> None:
    """The one bucket in this stack that holds FILE BYTES for years.

    Until it existed, an uploaded marksheet's only copy beyond the EFS volume
    was the daily backup plan, bounded by `backupRetentionDays` -- 35, RDS's
    ceiling. The archive backup selection that reaches past 35 days names the
    DATABASE ALONE on purpose, and both pg_dump tiers carry rows and never
    bytes. So a file deleted from the website was gone in both regions on day
    36. Object Lock cannot be added after creation, which is why this is asserted
    on the bucket that exists rather than deferred.
    """
    buckets = _dump_buckets(hardened, "reep-documents-archive-")
    assert len(buckets) == 1, "expected exactly one reep-documents-archive-* bucket"
    b = buckets[0]
    assert b["VersioningConfiguration"] == {"Status": "Enabled"}
    assert b["ObjectLockEnabled"] is True
    assert b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Mode"] == "GOVERNANCE"
    assert b["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]["Days"] >= 365


def test_the_document_archive_has_no_lifecycle_rule(hardened: Template) -> None:
    """The ledger bucket's rule and the dump archive's, for the same reason, and
    here the absence protects the only copy of a scanned certificate rather than
    a re-derivable dump. A rule acquired by accident is that promise expiring in
    the one direction S3 never reports."""
    assert "LifecycleConfiguration" not in _dump_buckets(hardened, "reep-documents-archive-")[0]


def test_the_import_phase_has_no_document_archive(imported: Template) -> None:
    """Resources this stack ADDS never appear in the mirror."""
    names = [
        str(r["Properties"].get("BucketName", ""))
        for r in imported.to_json()["Resources"].values()
        if r["Type"] == "AWS::S3::Bucket"
    ]
    assert not [n for n in names if n.startswith("reep-documents-archive-")]


def test_the_task_may_write_documents_and_never_read_them(hardened: Template) -> None:
    """PutObject to write, ListBucket to ask, s3:GetObject NOWHERE -- and here
    the rule bites hardest of the three buckets it applies to.

    This one holds every marksheet, certificate, photograph, CV, staff signature
    and recorded interview in the deployment, as ORIGINAL BYTES rather than a
    dump that would need restoring. A task that can read it is one compromise
    away from exfiltrating every document the college holds.

    The trap is `head_object`, the obvious way for the sweep to ask which
    objects already exist: S3 authorises HeadObject with s3:GetObject and has no
    separate permission for it. `archive_documents` lists instead.
    """
    role = next(
        r
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::IAM::Role"
        and r["Properties"].get("RoleName") == "reep-api-task"
    )
    policies = {p["PolicyName"]: p for p in role["Properties"]["Policies"]}
    acts = [
        a
        for st in policies["write-document-archive"]["PolicyDocument"]["Statement"]
        for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
    ]
    assert sorted(acts) == ["s3:ListBucket", "s3:PutObject"]

    for name, policy in policies.items():
        for st in policy["PolicyDocument"]["Statement"]:
            a = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            resources = st.get("Resource", [])
            resources = resources if isinstance(resources, list) else [resources]
            if any("documents-archive" in str(r) for r in resources):
                assert not [
                    x for x in a if x.startswith(("s3:Get", "s3:Delete"))
                ], f"policy {name} can read or delete an archived document: {a}"


def test_the_archive_sweep_runs_strictly_before_the_retention_sweep(hardened: Template) -> None:
    """THE ORDERING THAT CANNOT BE RECOVERED FROM.

    `app/retention.py` DELETES interview audio off the volume on its own nightly
    clock. An archive pass that ran after it has permanently missed every
    recording that expired that night -- the bytes are gone and no row points at
    them, so there is no second chance and no way to notice. Both jobs are green.

    This is a stronger version of the ordering the ledger and the dump schedules
    already argue for, where running late costs a day of freshness rather than
    the artefact.
    """
    schedules = {
        r["Properties"]["Name"]: r["Properties"]["ScheduleExpression"]
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
    }
    archive = _cron_minutes(schedules["reep-document-archive-daily"])
    sweep = _cron_minutes(schedules["reep-retention-daily"])
    assert archive < sweep, (
        f"the document archive sweep runs at {archive} min UTC and the retention "
        f"sweep at {sweep}. Retention deletes interview audio; archiving after it "
        "loses every recording that expired that night, permanently and silently."
    )


def test_the_archive_schedule_runs_the_sweep(hardened: Template) -> None:
    """A schedule pointing at the wrong module is a green job that archives
    nothing."""
    sched = next(
        r
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
        and r["Properties"]["Name"] == "reep-document-archive-daily"
    )
    assert "app.archive_documents" in sched["Properties"]["Target"]["Input"]


# ----------------------------------------- "<name> is on leave today" (leave) --

LEAVE_TODAY_SCHEDULE = "reep-leave-today-daily"


def _schedule_names(t: Template) -> set[str]:
    return {
        r["Properties"]["Name"]
        for r in t.to_json()["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
    }


def test_the_leave_today_schedule_runs_the_announcement_before_the_working_day(hardened: Template) -> None:
    """The mail says "this person is on leave TODAY", so it must land on the
    leave day, in India's morning, before the first class a colleague might
    have to cover -- and the cron is UTC, so the check converts it. Not at
    approval time either, which is the whole reason this is a schedule. A
    schedule pointing at the wrong module is a green job that tells nobody."""
    res = hardened.to_json()["Resources"]
    sched = next(
        r
        for r in res.values()
        if r["Type"] == "AWS::Scheduler::Schedule" and r["Properties"]["Name"] == LEAVE_TODAY_SCHEDULE
    )
    assert "app.leave_today_job" in sched["Properties"]["Target"]["Input"]

    utc = _cron_minutes(sched["Properties"]["ScheduleExpression"])
    ist = (utc + 5 * 60 + 30) % (24 * 60)
    assert 6 * 60 <= ist <= 8 * 60 + 30, f"runs at {ist // 60:02d}:{ist % 60:02d} IST, not the morning of the leave day"

    db = next(r for r in res.values() if r["Type"] == "AWS::RDS::DBInstance")
    start, end = db["Properties"]["PreferredBackupWindow"].split("-")
    to_min = lambda hm: int(hm[:2]) * 60 + int(hm[3:])
    assert not (to_min(start) <= utc <= to_min(end)), "lands inside the RDS backup window"


def test_the_leave_today_schedule_exists_only_where_leave_mail_can_be_sent(imported: Template) -> None:
    """Gated on exactly what puts LEAVE_MAIL_ENABLED in the task definition. A
    schedule without it wakes every morning and announces nothing; one in the
    import mirror is a resource `cdk import` cannot adopt; one at step 9a rides
    along with the Multi-AZ conversion for no reason."""
    assert LEAVE_TODAY_SCHEDULE not in _schedule_names(imported)
    assert LEAVE_TODAY_SCHEDULE not in _schedule_names(_core("harden", leaveMailEnabled=False))
    assert LEAVE_TODAY_SCHEDULE not in _schedule_names(_core("harden", hardenEcs="false", leaveMailEnabled=True))


JOB_SCOUT_SCHEDULE = "reep-job-scout-daily"
JOB_SCOUT_SECRETS = {"ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY"}


def _job_scout(**flags: object) -> Template:
    return _core(
        "harden",
        wafWebAclArn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/reep-edge/abc",
        drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr",
        sesIdentityDomain="bgscet.ac.in",
        sesFromAddress="reep@bgscet.ac.in",
        alertEmail="ops@bgscet.ac.in",
        **flags,
    )


def test_the_job_agent_schedule_runs_the_agent_before_students_open_the_board() -> None:
    """06:00 IST, so the morning's postings are there before the day starts,
    clear of the RDS windows and of every other job's clock. A schedule naming
    the wrong module is a green job that posts nothing."""
    t = _job_scout(jobScoutAdzuna="true", jobScoutJooble="true")
    res = t.to_json()["Resources"]
    schedules = [r for r in res.values() if r["Type"] == "AWS::Scheduler::Schedule"]
    sched = next(r for r in schedules if r["Properties"]["Name"] == JOB_SCOUT_SCHEDULE)
    assert json.loads(sched["Properties"]["Target"]["Input"]) == {
        "containerOverrides": [{"name": "api", "command": ["python", "-m", "app.job_scout_job"]}]
    }
    assert sched["Properties"]["FlexibleTimeWindow"] == {"Mode": "OFF"}

    utc = _cron_minutes(sched["Properties"]["ScheduleExpression"])
    ist = (utc + 5 * 60 + 30) % (24 * 60)
    assert 5 * 60 <= ist <= 7 * 60, f"runs at {ist // 60:02d}:{ist % 60:02d} IST, not before the day starts"

    db = next(r for r in res.values() if r["Type"] == "AWS::RDS::DBInstance")
    to_min = lambda hm: int(hm[:2]) * 60 + int(hm[3:])
    start, end = db["Properties"]["PreferredBackupWindow"].split("-")
    assert not (to_min(start) <= utc <= to_min(end)), "lands inside the RDS backup window"
    others = {
        _cron_minutes(r["Properties"]["ScheduleExpression"])
        for r in schedules
        if r["Properties"]["Name"] != JOB_SCOUT_SCHEDULE
    }
    assert utc not in others, "shares a minute with another job and contends for the same capacity"


def test_the_job_agent_keys_and_schedule_are_opt_in_one_board_at_a_time(imported: Template, hardened: Template) -> None:
    """A task definition naming a secret KEY the JSON does not hold fails to
    START, the api included, so each board's keys are referenced only through
    its own flag, set after the key is in reep/external. Without a key the
    schedule would wake every morning and post nothing, so it goes with them;
    and none of it rides in the import mirror or in step 9a."""
    for template in (imported, hardened):
        assert not (JOB_SCOUT_SECRETS & _container_secret_names(template))
    assert JOB_SCOUT_SCHEDULE not in _schedule_names(imported)
    # No board and no company feeds: nothing to run, so no schedule.
    assert JOB_SCOUT_SCHEDULE not in _schedule_names(_job_scout(jobScoutCompanies="false"))

    adzuna = _job_scout(jobScoutAdzuna="true")
    assert JOB_SCOUT_SECRETS & _container_secret_names(adzuna) == {"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}
    assert JOB_SCOUT_SCHEDULE in _schedule_names(adzuna)
    adzuna.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"ContainerDefinitions": Match.array_with([Match.object_like({"Secrets": Match.array_with([Match.object_like({"Name": "ADZUNA_APP_KEY", "ValueFrom": Match.string_like_regexp(":ADZUNA_APP_KEY::$")})])})])},
    )

    jooble = _job_scout(jobScoutJooble="true")
    assert JOB_SCOUT_SECRETS & _container_secret_names(jooble) == {"JOOBLE_API_KEY"}
    assert JOB_SCOUT_SCHEDULE in _schedule_names(jooble)

    step_9a = _job_scout(hardenEcs="false", jobScoutAdzuna="true", jobScoutJooble="true")
    assert not (JOB_SCOUT_SECRETS & _container_secret_names(step_9a))
    assert JOB_SCOUT_SCHEDULE not in _schedule_names(step_9a)


def _api_environment(template: Template) -> dict[str, str]:
    return {
        v["Name"]: v["Value"]
        for r in template.to_json()["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
        for c in r["Properties"]["ContainerDefinitions"]
        for v in c.get("Environment", [])
    }


def test_the_company_job_feeds_are_a_variable_and_a_schedule_and_no_secret() -> None:
    """The companies' own feeds need no key, so the flag can never stop a task
    from starting: one plain variable, the schedule, and no secret. Neither
    rides in step 9a, whose task definition must not change."""
    on = _job_scout(jobScoutCompanies="true", jobScoutAdzuna="false", jobScoutJooble="false")
    assert _api_environment(on).get("JOB_SCOUT_COMPANY_BOARDS") == "true"
    assert JOB_SCOUT_SCHEDULE in _schedule_names(on)
    assert not (JOB_SCOUT_SECRETS & _container_secret_names(on))

    off = _job_scout(jobScoutCompanies="false")
    assert "JOB_SCOUT_COMPANY_BOARDS" not in _api_environment(off)

    step_9a = _job_scout(hardenEcs="false", jobScoutCompanies="true")
    assert "JOB_SCOUT_COMPANY_BOARDS" not in _api_environment(step_9a)
    assert JOB_SCOUT_SCHEDULE not in _schedule_names(step_9a)


def test_cdk_json_turns_the_company_job_feeds_on(hardened: Template) -> None:
    """The owner asked for jobs on the board with no sign-up anywhere, and the
    company feeds are how. cdk.json is the file that remembers it: a flag
    passed only at deploy time would be dropped by the next deploy of any
    other `core-*` option, silently (the apiArm64 lesson)."""
    assert CDK_JSON_CONTEXT.get("jobScoutCompanies") is True
    assert _api_environment(hardened).get("JOB_SCOUT_COMPANY_BOARDS") == "true"
    assert JOB_SCOUT_SCHEDULE in _schedule_names(hardened)


def test_the_interview_audio_directory_is_named_and_not_inferred(hardened: Template) -> None:
    """`interview_audio._store_root()`'s fallback derives the audio store from
    UPLOAD_DIR's PARENT, so it lands on the EFS mount only because UPLOAD_DIR
    happens to be /data/uploads. app/config.py records what that coincidence
    cost when it broke: recordings landed in the container's writable layer and
    were destroyed on every redeploy, silently. Naming it makes the mount a
    statement rather than an accident of another variable."""
    env = {
        v["Name"]: v["Value"]
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
        for c in r["Properties"]["ContainerDefinitions"]
        for v in c.get("Environment", [])
    }
    assert env["INTERVIEW_AUDIO_DIR"] == "/data/interview-audio"
    assert env["INTERVIEW_AUDIO_DIR"].startswith("/data/"), "must be on the EFS mount"


def test_an_unbounded_document_retention_is_refused() -> None:
    """It is an Object Lock retention in years and cannot be shortened on
    objects already written."""
    with pytest.raises(ValueError):
        _core("harden", documentArchiveYears=0)


def test_a_daily_tier_no_longer_than_the_snapshots_is_refused() -> None:
    """35 days of physical snapshots already exist. A logical tier that expires
    no later adds nothing they do not already give, and would read on a diagram
    as a second line of defence that is not one."""
    with pytest.raises(ValueError):
        _core("harden", dbDumpDailyDays=30)


def test_secrets_are_referenced_never_written(imported: Template, hardened: Template) -> None:
    for t in (imported, hardened):
        types = {r["Type"] for r in t.to_json()["Resources"].values()}
        assert "AWS::SecretsManager::Secret" not in types
        assert "AWS::SecretsManager::SecretTargetAttachment" not in types
    hardened.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"ContainerDefinitions": Match.array_with([Match.object_like({"Secrets": Match.array_with([Match.object_like({"Name": "DATABASE_URL", "ValueFrom": Match.string_like_regexp(":DATABASE_URL::$")})])})])},
    )


def _container_secret_names(template: Template) -> set[str]:
    names: set[str] = set()
    for resource in template.to_json()["Resources"].values():
        if resource["Type"] != "AWS::ECS::TaskDefinition":
            continue
        for container in resource["Properties"].get("ContainerDefinitions", []):
            names.update(s["Name"] for s in container.get("Secrets", []))
    return names


def test_the_jobs_dsn_secret_is_opt_in_and_off_by_default(imported: Template, hardened: Template) -> None:
    """An ECS task that references a secret KEY the JSON does not hold fails to
    START — every task on that definition, the api included — so the
    reep-scheduled-jobs DSN (read by app/retention_job.py as SENTRY_JOBS_DSN)
    must not be referenced until an operator has added the key to the
    reep/external secret. Off by default in BOTH phases, so the import mirror
    stays byte-identical; on only through the `sentryJobsDsn` context flag."""
    assert "SENTRY_DSN" in _container_secret_names(hardened)
    for template in (imported, hardened):
        assert "SENTRY_JOBS_DSN" not in _container_secret_names(template)
    opted = _core(
        "harden",
        sentryJobsDsn="true",
        wafWebAclArn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/reep-edge/abc",
        drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr",
        sesIdentityDomain="bgscet.ac.in",
        sesFromAddress="reep@bgscet.ac.in",
        alertEmail="ops@bgscet.ac.in",
    )
    assert "SENTRY_JOBS_DSN" in _container_secret_names(opted)
    opted.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"ContainerDefinitions": Match.array_with([Match.object_like({"Secrets": Match.array_with([Match.object_like({"Name": "SENTRY_JOBS_DSN", "ValueFrom": Match.string_like_regexp(":SENTRY_JOBS_DSN::$")})])})])},
    )


def test_invalid_phase_is_refused() -> None:
    with pytest.raises(ValueError):
        _core("production")


# ------------------------------------------------------ companion stacks --


def _edge_rules(phase: str | None = None) -> list[dict]:
    app = cdk.App(context={"phase": phase} if phase else None)
    t = Template.from_stack(EdgeWafStack(app, "test-edge", env=cdk.Environment(account="123456789012", region="us-east-1")))
    t.has_resource_properties("AWS::WAFv2::WebACL", {"Name": "reep-edge", "Scope": "CLOUDFRONT"})
    return next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::WAFv2::WebACL")["Properties"]["Rules"]


def test_edge_waf_has_the_three_terraform_rules() -> None:
    """The import mirror carries exactly Terraform's three rules; harden adds
    the re-block for the counted body rules after them."""
    rules = _edge_rules("import")
    assert [r["Name"] for r in rules] == ["aws-common", "aws-bad-inputs", "rate-limit"]
    assert rules[2]["Statement"]["RateBasedStatement"]["Limit"] == 2000
    # Priorities are what WAF evaluates by; the live ACL lists them in a
    # different array order and that is not a difference.
    assert sorted((r["Priority"], r["Name"]) for r in rules) == [(1, "aws-common"), (2, "aws-bad-inputs"), (3, "rate-limit")]
    harden = _edge_rules()  # the default phase
    assert sorted((r["Priority"], r["Name"]) for r in harden) == [
        (1, "aws-common"), (2, "aws-bad-inputs"), (3, "rate-limit"), (4, BODY_RULES_OUTSIDE_UPLOADS),
    ]


def _common_rule_set(phase: str) -> dict:
    return next(r for r in _edge_rules(phase) if r["Name"] == "aws-common")["Statement"]["ManagedRuleGroupStatement"]


def test_the_body_injection_rules_still_block_everything_but_a_file_upload() -> None:
    """INCIDENT (2026-09-29): CrossSiteScripting_BODY read compressed bytes in
    CVs and photos as markup and refused the registration 403, on every retry.
    The four body rules are counted in the group and re-blocked here by their
    LABELS for any request that is not multipart/form-data, so a JSON body is
    exactly as protected as before. A mistyped label matches nothing and would
    turn that rule into a count everywhere, so the documented casing is pinned.
    """
    rule = next(r for r in _edge_rules("harden") if r["Name"] == BODY_RULES_OUTSIDE_UPLOADS)
    assert rule["Action"] == {"Block": {}}
    labelled, not_multipart = rule["Statement"]["AndStatement"]["Statements"]
    keys = {s["LabelMatchStatement"]["Key"] for s in labelled["OrStatement"]["Statements"]}
    assert keys == {
        "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body",
        "awswaf:managed:aws:core-rule-set:GenericLFI_Body",
        "awswaf:managed:aws:core-rule-set:GenericRFI_Body",
        "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body",
    }
    assert set(COMMON_RULE_SET_BODY_LABELS) <= set(COMMON_RULE_SET_COUNTED)
    match = not_multipart["NotStatement"]["Statement"]["ByteMatchStatement"]
    assert match["FieldToMatch"] == {"SingleHeader": {"Name": "content-type"}}
    assert match["PositionalConstraint"] == "STARTS_WITH"
    assert match["SearchString"] == "multipart/form-data"
    assert match["TextTransformations"] == [{"Priority": 0, "Type": "LOWERCASE"}]


def test_the_common_rule_set_counts_the_body_size_rule_so_uploads_reach_the_api() -> None:
    """INCIDENT (2026-09-17): every file upload in the product was answered 403
    at the edge. AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY blocks a
    body over 8 KB, and a certificate, a CV, a photo, a signature or a leave
    attachment is a multipart POST well past that. The API never saw the
    request, the client saw a non-JSON body and printed its fallback, and the
    student was told their JPEG was the wrong kind of file.

    The rule is COUNTED in the harden phase -- still measured, never blocking
    -- and the tuple that says so is read here rather than the string retyped,
    so a second rule added to it is covered by this test the day it lands.
    """
    assert "SizeRestrictions_BODY" in COMMON_RULE_SET_COUNTED
    statement = _common_rule_set("harden")
    overrides = {o["Name"]: o["ActionToUse"] for o in statement["RuleActionOverrides"]}
    for rule in COMMON_RULE_SET_COUNTED:
        assert overrides[rule] == {"Count": {}}, f"{rule} must be counted, not blocked"
    # The rest of the group still blocks: only the named rules are overridden.
    assert set(overrides) == set(COMMON_RULE_SET_COUNTED)


def test_the_import_phase_mirror_carries_no_rule_override() -> None:
    """The import phase is a byte-identical mirror of the adopted ACL, and the
    rehearsal's whole value is that its diff shows nothing. The override is a
    harden-phase change, like ManagedBy=cdk, and must not leak backwards."""
    statement = _common_rule_set("import")
    assert "RuleActionOverrides" not in statement


def test_no_stack_level_tags_in_any_phase() -> None:
    """INCIDENT (2026-09-07): the step-3 rehearsal import failed with

        As part of the import operation, you cannot modify or add [RoleArn, Tags]

    `Tags.of(stack).add(...)` tags the Stack itself, and CDK passes a tagged
    stack's tags to CreateChangeSet as STACK tags, which CloudFormation refuses
    on an import change set. Nothing offline could have caught it: the template
    is identical either way — the difference is in the manifest, and so in the
    API call. The core import would have failed the same way on all 65
    resources.

    Tags are applied to the stack's CHILDREN now. This asserts the stack itself
    carries none, in every phase and for every stack the cutover imports, while
    the resources still carry theirs.
    """
    for label, stack_factory in (
        ("core/import", lambda app: CoreStack(app, "c", project="reep", env=cdk.Environment(account="123456789012", region="ap-south-1"))),
        ("edge/import", lambda app: EdgeWafStack(app, "e", project="reep", env=cdk.Environment(account="123456789012", region="us-east-1"))),
    ):
        app = cdk.App(context={**CDK_JSON_CONTEXT, "phase": "import"})
        stack = stack_factory(app)
        assert not stack.tags.render_tags(), f"{label}: the STACK is tagged, which CloudFormation refuses on an import change set"
        rendered = Template.from_stack(stack).to_json()["Resources"]
        tagged = [r for r in rendered.values() if r.get("Properties", {}).get("Tags")]
        assert tagged, f"{label}: no resource carries tags — the aspect stopped reaching the children"


def test_the_edge_waf_is_adopted_with_terraforms_tag() -> None:
    """INCIDENT (2026-09-07, the pre-import review): the core stack keeps
    ManagedBy=terraform in the import phase so nothing shows MODIFIED for a
    label. The edge stack was the one place that rule was not applied — it
    tagged the ACL `cdk` unconditionally, which would have put a property
    difference into step 3's diff. Step 3 is the REHEARSAL: its entire value is
    that it comes back showing only CDKMetadata and the output, so an operator
    who sees anything else knows the mirror is wrong."""
    for phase, expected in (("import", "terraform"), ("harden", "cdk")):
        app = cdk.App(context={"phase": phase})
        t = Template.from_stack(EdgeWafStack(app, "test-edge", env=cdk.Environment(account="123456789012", region="us-east-1")))
        acl = next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::WAFv2::WebACL")
        tags = {tag["Key"]: tag["Value"] for tag in acl["Properties"]["Tags"]}
        assert tags["ManagedBy"] == expected, f"{phase} phase tags the adopted ACL {tags['ManagedBy']!r}"
        assert acl.get("DeletionPolicy") == "Retain"


def test_dr_vault_is_locked_with_the_same_minimum() -> None:
    app = cdk.App()
    t = Template.from_stack(DrVaultStack(app, "test-dr", min_retention_days=35, env=cdk.Environment(account="123456789012", region="ap-southeast-1")))
    t.has_resource_properties("AWS::Backup::BackupVault", {"BackupVaultName": "reep-vault-dr", "LockConfiguration": {"MinRetentionDays": 35}})


def test_nothing_in_the_stack_deletes_on_removal(imported: Template, hardened: Template) -> None:
    """`cdk import` requires Retain on every adopted resource, and after the
    cutover a rename or a stray `cdk destroy` must forget student data, never
    delete it. The database is Snapshot; everything else is Retain."""
    for t in (imported, hardened):
        for lid, res in t.to_json()["Resources"].items():
            policy = res.get("DeletionPolicy")
            assert policy in ("Retain", "Snapshot", "RetainExceptOnCreate"), f"{lid} ({res['Type']}) would be deleted: {policy!r}"


# --------------------------------------------- the council's blockers --


def test_import_phase_carries_the_live_database_values_not_the_harden_targets(imported: Template) -> None:
    """cdk.json sets dbMultiAz and backupRetentionDays=35 — the HARDEN
    targets. The import mirror must ignore them: import does not compare
    properties, so a mirror saying Multi-AZ against a single-AZ instance
    imports fine and then harden, carrying the same value, sends no change.
    Multi-AZ would silently never happen."""
    imported.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": False, "BackupRetentionPeriod": 14, "AllocatedStorage": "20"})
    live = _core("import", liveDbMultiAz="true", liveBackupRetentionDays=7, liveAllocatedStorage=45)
    live.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": True, "BackupRetentionPeriod": 7, "AllocatedStorage": "45"})


def test_import_phase_has_no_resource_that_exists_nowhere(imported: Template) -> None:
    """The L2s used to add three: a 0.0.0.0/0:80 ingress on the ALB group
    (bypassing the WAF), a duplicate :3300 ingress, and an IAM::Policy on the
    execution role. None is in Terraform or in the account; one is a hole."""
    types = [r["Type"] for r in imported.to_json()["Resources"].values()]
    assert "AWS::EC2::SecurityGroupIngress" not in types
    assert "AWS::IAM::Policy" not in types


def test_every_import_phase_type_has_a_registry_identifier(imported: Template) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from import_map import IDENTIFIERS  # noqa: E402

    types = {r["Type"] for r in imported.to_json()["Resources"].values()}
    unknown = sorted(types - set(IDENTIFIERS))
    assert not unknown, f"types the import tool cannot identify: {unknown}"


def test_the_service_does_not_pin_desired_count(imported: Template, hardened: Template) -> None:
    for t in (imported, hardened):
        svc = next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::Service")["Properties"]
        assert "DesiredCount" not in svc, "autoscaling owns DesiredCount; sending it scales a busy day back down"


def test_the_ecs_half_of_harden_can_be_held_back(hardened: Template) -> None:
    """hardenEcs=false: RDS/backup/vault/alarms without the task-definition,
    target-group and service changes, so a circuit-breaker rollback cannot
    also undo a Multi-AZ conversion in the same stack update."""
    t = _core("harden", hardenEcs="false", dbMultiAz="true", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    t.has_resource_properties("AWS::RDS::DBInstance", {"MultiAZ": True, "BackupRetentionPeriod": 35})
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"TargetGroupAttributes": Match.array_with([{"Key": "deregistration_delay.timeout_seconds", "Value": "30"}])},
    )
    task = next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::ECS::TaskDefinition")["Properties"]
    assert "StopTimeout" not in task["ContainerDefinitions"][0]


def test_the_database_half_does_not_touch_the_ecs_trio(imported: Template) -> None:
    """INCIDENT (2026-09-07, the pre-import review): step 9a exists so that an
    ECS circuit-breaker rollback cannot undo a Multi-AZ conversion in the same
    stack update. It did not do that. The harden phase flips every resource's
    ManagedBy tag, and **a task definition is immutable** — changing its tags
    registers a NEW REVISION, which the service then rolls onto. So 9a
    modified 46 resources, the task definition, service and target group
    among them, and would have rolled the API in the same update as the
    database conversion. The split was a comment, not a property.

    Now it is a property: at `hardenEcs=false` those three resources are
    byte-identical to the import mirror, so CloudFormation has nothing to send
    for them.
    """
    trio = ("AWS::ECS::TaskDefinition", "AWS::ECS::Service", "AWS::ElasticLoadBalancingV2::TargetGroup")
    step_9a = _core("harden", hardenEcs="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    before, after = imported.to_json()["Resources"], step_9a.to_json()["Resources"]

    for cfn_type in trio:
        lid = next(l for l, r in before.items() if r["Type"] == cfn_type)
        assert before[lid] == after[lid], f"step 9a would update {cfn_type} ({lid}) — that is an API roll during the Multi-AZ conversion"

    # And 9b must still make the changes it is supposed to: the tag flip and
    # the deregistration delay. A split that never converges is not a split.
    step_9b = _core("harden", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    final = step_9b.to_json()["Resources"]
    for cfn_type in trio:
        lid = next(l for l, r in before.items() if r["Type"] == cfn_type)
        assert final[lid] != after[lid], f"9b never hardens {cfn_type} — the ECS half would be held back forever"
        tags = {t["Key"]: t["Value"] for t in final[lid]["Properties"].get("Tags", [])}
        assert tags.get("ManagedBy") == "cdk", f"{cfn_type} never reaches ManagedBy=cdk"


def test_retention_above_the_rds_maximum_is_refused() -> None:
    with pytest.raises(ValueError):
        _core("harden", backupRetentionDays=36)


def test_security_groups_and_subnet_group_carry_terraforms_descriptions(imported: Template) -> None:
    """GroupDescription is create-only. Terraform never set one, so the live
    value is its default; anything else is a permanent MODIFIED and a
    replacement attempt the day someone tries to fix it."""
    for res in imported.to_json()["Resources"].values():
        if res["Type"] == "AWS::EC2::SecurityGroup":
            assert res["Properties"]["GroupDescription"] == "Managed by Terraform"
    imported.has_resource_properties("AWS::RDS::DBSubnetGroup", {"DBSubnetGroupDescription": "Managed by Terraform"})


def test_the_import_mirror_keeps_terraforms_tag_and_the_eip_keeps_it_forever(imported: Template, hardened: Template) -> None:
    def tag(res: dict, key: str) -> str | None:
        return next((t["Value"] for t in res.get("Properties", {}).get("Tags", []) if t["Key"] == key), None)

    vpc = next(r for r in imported.to_json()["Resources"].values() if r["Type"] == "AWS::EC2::VPC")
    assert tag(vpc, "ManagedBy") == "terraform"
    with_gateway = _core("harden", natGateway="true", natInstance="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    eip = next(r for r in with_gateway.to_json()["Resources"].values() if r["Type"] == "AWS::EC2::EIP")
    assert tag(eip, "ManagedBy") == "terraform", "a tag update on an EIP may reassociate the address"
    vpc_h = next(r for r in hardened.to_json()["Resources"].values() if r["Type"] == "AWS::EC2::VPC")
    assert tag(vpc_h, "ManagedBy") == "cdk"


def _edge_with_uptime(phase: str = "harden") -> dict:
    app = cdk.App(context={"phase": phase, "domainName": "reep.example.edu", "alertEmail": "ops@example.edu"})
    stack = EdgeWafStack(app, "test-edge-uptime", env=cdk.Environment(account="123456789012", region="us-east-1"))
    return Template.from_stack(stack).to_json()["Resources"]


def test_the_uptime_check_knocks_from_outside_through_the_public_name() -> None:
    """2026-09-29: every reep-core alarm watches from inside the account, so a
    broken certificate, DNS record or distribution left the ALB healthy and
    nobody told. Route 53 asks from outside, through the name a student types;
    the alarm treats silence as down and mails someone.

    TCP and not HTTPS: the distribution is TLS 1.3 only and Route 53's checkers
    speak TLS 1.2 at most, so an HTTPS check failed every handshake against a
    site answering 200 -- a permanent false alarm on the first deploy."""
    res = _edge_with_uptime()
    check = next(r for r in res.values() if r["Type"] == "AWS::Route53::HealthCheck")["Properties"]["HealthCheckConfig"]
    assert UPTIME_CHECK_TYPE == "TCP" and check["Type"] == "TCP" and check["Port"] == 443
    assert "ResourcePath" not in check and "EnableSNI" not in check, "TCP checks take neither"
    assert check["FullyQualifiedDomainName"] == "reep.example.edu"
    alarm = next(r for r in res.values() if r["Type"] == "AWS::CloudWatch::Alarm")["Properties"]
    assert alarm["Namespace"] == "AWS/Route53" and alarm["MetricName"] == "HealthCheckStatus"
    assert alarm["ComparisonOperator"] == "LessThanThreshold" and alarm["Threshold"] == 1
    assert alarm["TreatMissingData"] == "breaching", "a checker that stopped reporting is not an up site"
    assert alarm["AlarmActions"] and alarm["OKActions"]
    sub = next(r for r in res.values() if r["Type"] == "AWS::SNS::Subscription")["Properties"]
    assert sub == {**sub, "Protocol": "email", "Endpoint": "ops@example.edu"}
    for lid, r in res.items():
        assert r.get("DeletionPolicy") == "Retain", lid


def test_the_import_mirror_grows_no_uptime_check() -> None:
    """The import phase is a byte-identical mirror of the adopted ACL; a new
    resource in it is a template CloudFormation refuses to import."""
    types = {r["Type"] for r in _edge_with_uptime("import").values()}
    assert types == {"AWS::WAFv2::WebACL"}


def test_all_three_stacks_retain_everything() -> None:
    app = cdk.App()
    for stack in (
        EdgeWafStack(app, "e", env=cdk.Environment(account="123456789012", region="us-east-1")),
        DrVaultStack(app, "d", min_retention_days=35, env=cdk.Environment(account="123456789012", region="ap-southeast-1")),
    ):
        for lid, res in Template.from_stack(stack).to_json()["Resources"].items():
            assert res.get("DeletionPolicy") == "Retain", f"{stack.stack_name}/{lid}"


def test_three_backup_failure_alarms(hardened: Template) -> None:
    for name in ("reep-backup-job-failed", "reep-backup-copy-failed", "reep-backup-restore-test-failed"):
        hardened.has_resource_properties("AWS::CloudWatch::Alarm", {"AlarmName": name})


@requires_terraform
def test_the_tls_branch_synthesises_with_terraforms_listener_policy() -> None:
    """The TLS branch is the LIVE branch — the ALB has a certificate — and no
    guard had ever synthesised it: the first rehearsal (test_cutover_tools.py)
    found `SslPolicy.TLS13_12`, a member the library does not have, so
    `cdk synth` with the real context crashed before any import could start.
    The policy is a mutable listener property, so a mismatch is a MODIFIED
    drift at step 5 and a changed live policy at step 9; it is read from
    alb.tf rather than typed twice."""
    tf_text = (TF_DIR / "alb.tf").read_text(encoding="utf-8")
    policy = re.search(r'ssl_policy\s*=\s*"([^"]+)"', tf_text).group(1)
    t = _core("import", albAcmCertificateArn="arn:aws:acm:ap-south-1:123456789012:certificate/alb-cert")
    listeners = [r["Properties"] for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::ElasticLoadBalancingV2::Listener"]
    assert sorted(lst["Port"] for lst in listeners) == [80, 443]
    https = next(lst for lst in listeners if lst["Port"] == 443)
    assert https["Protocol"] == "HTTPS"
    assert https["SslPolicy"] == policy, f"alb.tf says {policy!r}; the mirror renders {https.get('SslPolicy')!r}"
    redirect = next(lst for lst in listeners if lst["Port"] == 80)
    assert redirect["DefaultActions"][0]["Type"] == "redirect"


def test_an_existing_oidc_provider_is_referenced_not_redeclared() -> None:
    t = _core("import", githubOidcProviderArn="arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com")
    t.resource_count_is("AWS::IAM::OIDCProvider", 0)


def test_graviton_off_renders_nothing_rather_than_an_explicit_x86() -> None:
    """With the flag OFF the task definition carries NO RuntimePlatform at all.

    Not tidiness. Rendering one — even `X86_64`, which is what ECS defaults to
    anyway — is a diff against a live task definition that has no such
    property, so a change altering nothing about how the api runs would show up
    as an ECS change. CDK renders the property only when `runtime_platform` is
    not None, which is why the flag passes None rather than an explicit
    X86_64. Asserted with the flag set explicitly false, because cdk.json now
    turns it ON (see the test below) and the bare fixture would no longer be
    testing the off path.
    """
    t = _core(
        "harden",
        apiArm64="false",
        drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr",
    )
    for td in t.find_resources("AWS::ECS::TaskDefinition").values():
        assert "RuntimePlatform" not in td["Properties"], (
            "a RuntimePlatform appeared with apiArm64=false — an unasked-for ECS diff"
        )


def test_cdk_json_carries_the_arm64_the_deployment_is_actually_running(hardened: Template) -> None:
    """cdk.json must hold `apiArm64: true`, because the deployed service does.

    WHY THIS TEST EXISTS. Graviton was first deployed by passing
    `-c apiArm64=true` on the command line and NOT writing it down. That works
    once and is wrong twice over. The deployed template then carried ARM64
    while `main` rendered nothing, which is precisely the repo-vs-AWS
    divergence `infra-drift.yml` was built to catch — it would have gone red
    the next morning, correctly, and a daily red check is one nobody reads. And
    every other `core-*` option in cdk-deploy.yml passes only its OWN flag, so
    the next deploy of ANY of them (9a, 9b, a NAT option) would have rendered
    no RuntimePlatform and quietly rolled the api back to x86 at twice the
    price, with nothing on any screen saying so.

    A context flag that is set at deploy time and not persisted is not
    configuration, it is a thing somebody has to remember. This is the file
    that remembers.
    """
    assert CDK_JSON_CONTEXT.get("apiArm64") is True, (
        "cdk.json lost apiArm64 — main would render no RuntimePlatform while the "
        "service runs ARM64, and the next core-* deploy would revert it to x86"
    )
    for lid, td in hardened.find_resources("AWS::ECS::TaskDefinition").items():
        rp = td["Properties"].get("RuntimePlatform")
        assert rp is not None and rp.get("CpuArchitecture") == "ARM64", (
            f"{lid} does not render ARM64 from cdk.json alone: {rp}"
        )


def test_graviton_flag_moves_every_api_family_to_arm64() -> None:
    """`-c apiArm64=true` puts ARM64 on the task definition, and on EVERY api
    family rather than one of them.

    Under blue/green there are three (`reep-api`, `-blue`, `-green`) built by
    one helper, and a colour left on x86 while the others move is a service
    whose rollback target cannot pull the image the live colour is running.
    """
    t = _core(
        "harden",
        apiArm64="true",
        blueGreen="true",
        drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr",
    )
    tds = t.find_resources("AWS::ECS::TaskDefinition")
    assert len(tds) == 3, f"expected the three api families under blueGreen, found {len(tds)}"
    for lid, td in tds.items():
        rp = td["Properties"].get("RuntimePlatform")
        assert rp is not None, f"{lid} kept no RuntimePlatform while apiArm64 was set"
        assert rp.get("CpuArchitecture") == "ARM64", f"{lid} is not ARM64: {rp}"
        assert rp.get("OperatingSystemFamily") == "LINUX", f"{lid} lost its OS family: {rp}"


def test_graviton_changes_nothing_but_the_platform() -> None:
    """Flipping the flag must not move cpu, memory, the image or the secrets.

    The whole claim of this change is "same capacity, 20% cheaper". If the flag
    were ever to alter a sizing property as well, that claim stops being true
    and nothing else would notice.
    """
    # apiArm64 is spelled out on BOTH sides. cdk.json turns it on, so a bare
    # `base` would be ARM64 too and this test would compare ARM to ARM and
    # prove nothing at all — passing for the rest of its life.
    base = _core("harden", apiArm64="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    arm = _core("harden", apiArm64="true", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    b = next(iter(base.find_resources("AWS::ECS::TaskDefinition").values()))["Properties"]
    a = next(iter(arm.find_resources("AWS::ECS::TaskDefinition").values()))["Properties"]
    assert a["Cpu"] == b["Cpu"], "apiArm64 changed the vCPU allocation"
    assert a["Memory"] == b["Memory"], "apiArm64 changed the memory allocation"
    assert a["ContainerDefinitions"] == b["ContainerDefinitions"], "apiArm64 changed the container"
    assert {k: v for k, v in a.items() if k != "RuntimePlatform"} == {
        k: v for k, v in b.items() if k != "RuntimePlatform"
    }, "apiArm64 changed more than the platform"


def test_nat_gateway_egress_when_the_flags_ask_for_it() -> None:
    """natGateway=true, natInstance=false: the managed gateway, as it was live
    until 2026-10-03. Spelled out, because cdk.json now says otherwise."""
    t = _core("harden", natGateway="true", natInstance="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    t.resource_count_is("AWS::EC2::NatGateway", 1)
    t.resource_count_is("AWS::EC2::Instance", 0)
    route = t.to_json()["Resources"]["PrivateDefaultRoute"]["Properties"]
    assert "NatGatewayId" in route, "the private subnets stopped routing through the gateway"
    assert "InstanceId" not in route


def test_nat_instance_does_not_remove_the_gateway() -> None:
    """THE REVERSIBILITY, and the whole reason this is two flags.

    `natInstance=true` points the route at the instance and LEAVES THE GATEWAY
    STANDING. Flipping the flag back restores egress through a gateway that
    never went away — no re-create, and so no new public address for anything
    that allowlisted the old one.
    """
    t = _core("harden", natInstance="true", natGateway="true", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    t.resource_count_is("AWS::EC2::NatGateway", 1)
    t.resource_count_is("AWS::EC2::Instance", 1)
    route = t.to_json()["Resources"]["PrivateDefaultRoute"]["Properties"]
    assert "InstanceId" in route and "NatGatewayId" not in route


def test_nat_instance_forwards_at_all() -> None:
    """`SourceDestCheck: false` and IP forwarding, pinned.

    Without the first, EC2 drops every packet whose source is not the instance
    and the box forwards NOTHING — with a healthy instance, a correct route and
    connections that simply time out. Without the second the kernel does not
    route between interfaces. Both are invisible from the console and total.
    """
    t = _core("harden", natInstance="true", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    inst = next(v for v in t.to_json()["Resources"].values() if v["Type"] == "AWS::EC2::Instance")
    p = inst["Properties"]
    assert p["SourceDestCheck"] is False, "the NAT instance would forward nothing"
    assert p["InstanceType"] == "t4g.nano"
    user_data = p["UserData"]["Fn::Base64"]
    assert "net.ipv4.ip_forward=1" in user_data
    # AL2023 has no iptables until it is installed; without this line every
    # rule below fails with "command not found" and the box forwards nothing
    # (what happened on the first deploy, 2026-10-03). It must come BEFORE the
    # unit is started.
    assert "dnf install -y iptables-nft" in user_data
    assert user_data.index("dnf install -y iptables-nft") < user_data.index("systemctl enable --now reep-nat.service")
    # ...and swap before the install: dnf was OOM-killed on the 512 MB box.
    assert "swapon /swapfile" in user_data
    assert user_data.index("swapon /swapfile") < user_data.index("dnf install -y iptables-nft")
    assert "MASQUERADE" in user_data
    # A systemd unit, not an inline apply: cloud-init runs user data on FIRST
    # BOOT ONLY, so rules applied inline vanish on the first reboot and the
    # deployment loses egress behind a green instance.
    assert "systemctl enable --now reep-nat.service" in user_data
    assert "RemainAfterExit=yes" in user_data


def test_nat_instance_takes_no_traffic_from_outside_the_vpc() -> None:
    """It forwards for the private subnets; it is not dialled from anywhere."""
    t = _core("harden", natInstance="true", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    sg = next(v for v in t.to_json()["Resources"].values()
              if v["Type"] == "AWS::EC2::SecurityGroup"
              and "NAT instance" in str(v["Properties"].get("GroupDescription", "")))
    for rule in sg["Properties"]["SecurityGroupIngress"]:
        assert rule.get("CidrIp") == "10.42.0.0/16", f"NAT instance ingress is open beyond the VPC: {rule}"


def test_retiring_the_gateway_needs_the_instance_first() -> None:
    """`natGateway=false` alone is a template with NO egress at all.

    It synthesises perfectly and it would take the deployment down: every task
    fails to START (the ECS agent reads Secrets Manager over that route and
    there are no VPC endpoints), Google sign-in's JWKS fetch fails, and the
    three scheduled tasks stop at 23:30, 01:00 and 03:00 with no alarm that
    covers them. Refused at synth rather than discovered at 3am.
    """
    with pytest.raises(ValueError, match="NO route to the internet"):
        _core("harden", natGateway="false", natInstance="false", drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")


def test_the_money_only_stops_when_the_gateway_goes() -> None:
    """Both flags together: the instance carries egress and the gateway and its
    EIP are gone. This is the state that actually saves the ~$43.6/month."""
    t = _core("harden", natInstance="true", natGateway="false",
              drVaultArn="arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr")
    t.resource_count_is("AWS::EC2::NatGateway", 0)
    t.resource_count_is("AWS::EC2::EIP", 0)
    t.resource_count_is("AWS::EC2::Instance", 1)
    route = t.to_json()["Resources"]["PrivateDefaultRoute"]["Properties"]
    assert "InstanceId" in route


def test_cdk_json_carries_the_nat_instance_the_deployment_is_actually_running(hardened: Template) -> None:
    """2026-10-03: core-nat-instance, then the egress probe, then core-nat-retire.
    WRITTEN DOWN, not passed with -c (the apiArm64 lesson): without these two
    keys every bare core-9b deploy would re-create the NAT gateway with a NEW
    public address and point the route back at it, silently."""
    assert CDK_JSON_CONTEXT.get("natInstance") is True
    assert CDK_JSON_CONTEXT.get("natGateway") is False
    hardened.resource_count_is("AWS::EC2::NatGateway", 0)
    hardened.resource_count_is("AWS::EC2::Instance", 1)


def test_the_deploy_role_still_has_no_codedeploy_cloudformation_or_rds(hardened: Template) -> None:
    """`reep-github-deploy` ships code. It must not be able to deploy STACKS.

    THIS GUARD DID NOT EXIST UNTIL 2026-09-17, and the comment above the role
    in reep_core/stack.py had been naming it — by this exact function name —
    for as long as the role has. A stated invariant nothing checks is a
    comment, and this one is the reason `cdk import` stays human-attended:
    the role assumes the account's CDK bootstrap roles and CloudFormation acts
    under the bootstrap's own execution role, so a compromised workflow run
    cannot reach RDS, rewrite IAM, or run the cutover from a browser.

    It was written the day infra-drift.yml asked for `cloudformation:*` on
    this role so its drift-detection step could answer. That grant was
    refused: CloudFormation reads every live resource with the CALLER's
    credentials, so a drift check that actually works needs read access across
    most of the account. `ce:GetCostAndUsage` was granted instead — Cost
    Explorer names no resource and reaches none — and `ce:` is therefore the
    one allowance below. The drift check got its own role on 2026-10-16
    (`test_the_drift_role_reads_configuration_and_never_data`).

    If a future change needs one of these prefixes here, that is a decision
    about who may deploy infrastructure from a pull request, not a test to
    relax quietly.
    """
    forbidden = ("codedeploy:", "cloudformation:", "rds:", "backup:", "secretsmanager:", "sts:AssumeRole")
    roles = hardened.find_resources("AWS::IAM::Role")
    deploy = [
        r for r in roles.values()
        if r["Properties"].get("RoleName") == "reep-github-deploy"
    ]
    assert len(deploy) == 1, f"expected exactly one reep-github-deploy role, found {len(deploy)}"

    actions: list[str] = []
    for doc in deploy[0]["Properties"].get("Policies", []):
        for stmt in doc["PolicyDocument"]["Statement"]:
            a = stmt.get("Action", [])
            actions.extend([a] if isinstance(a, str) else a)
    assert actions, "the deploy role rendered no actions at all — this guard would pass vacuously"

    for action in actions:
        if not isinstance(action, str):
            continue  # an Fn::If or similar; the literal prefixes are what this pins
        for bad in forbidden:
            assert not action.lower().startswith(bad.lower()), (
                f"reep-github-deploy gained {action!r}. That role ships code; it must not be able to "
                f"deploy stacks, read the database or rewrite IAM. See the docstring."
            )

    # The positive half: the one grant that WAS added for infra-drift.yml is
    # billing-only, so a future edit cannot quietly swap it for something that
    # reaches a resource.
    ce = [a for a in actions if isinstance(a, str) and a.startswith("ce:")]
    assert ce == ["ce:GetCostAndUsage"], (
        f"the deploy role's Cost Explorer grant changed to {ce!r}; only ce:GetCostAndUsage is intended"
    )


_DR_VAULT = "arn:aws:backup:ap-southeast-1:123456789012:backup-vault:reep-vault-dr"


def _drift_role(t: Template) -> dict:
    roles = [
        r for r in t.find_resources("AWS::IAM::Role").values()
        if r["Properties"].get("RoleName") == "reep-github-drift"
    ]
    assert len(roles) == 1, f"expected exactly one reep-github-drift role, found {len(roles)}"
    return roles[0]


def _as_list(value: object) -> list:
    return [value] if isinstance(value, (str, dict)) else list(value)  # type: ignore[arg-type]


def test_every_resource_type_the_drift_check_reads_is_classified(hardened: Template) -> None:
    """#117. CloudFormation reads each resource with the CALLER's credentials,
    so `reep-github-drift` needs the read permissions of every type in the two
    stacks infra-drift.yml drift-checks. A type missing from
    `drift_reader.READ_PERMISSIONS` does not fail anything loudly: the next
    morning's detection ends DETECTION_FAILED, the workflow files it as "a check
    could not run", and the drift half goes blank again. So a new type is
    refused HERE, where the fix is one line copied from its CloudFormation
    schema. The variants cover the types the other flags render: the NAT
    gateway and its EIP, blue/green's listener rule, managed SES.
    """
    platform = Template.from_stack(
        VoicePlatformStack(
            cdk.App(),
            "test-voice-platform",
            project="reep",
            recording_retention_days=90,
            env=cdk.Environment(account="123456789012", region="ap-south-1"),
        )
    )
    variants = [
        hardened,
        platform,
        _core("harden", natGateway="true", natInstance="false", drVaultArn=_DR_VAULT),
        _core("harden", blueGreen="true", drVaultArn=_DR_VAULT),
    ]
    missing = sorted(
        {
            res["Type"]
            for t in variants
            for res in t.to_json()["Resources"].values()
            if res["Type"] not in drift_reader.READ_PERMISSIONS
        }
    )
    assert not missing, (
        f"{missing} would leave the daily drift check blank (DETECTION_FAILED). Add each type to "
        "READ_PERMISSIONS in reep_core/drift_reader.py with its schema's handlers.read.permissions."
    )


def test_the_drift_role_reads_configuration_and_never_data(hardened: Template) -> None:
    """`reep-github-drift` answers `detect-stack-drift` and can do nothing else.

    Every allowed action is one of CloudFormation's five drift calls or a
    Describe/Get/List, the three stack-scoped calls reach only reep-core and
    reep-voice-platform, and the reads that would reach a student's record
    (object bodies, table items, secrets, decryption, log lines) are DENIED as
    well as absent. The Deny is what still holds if a managed policy is ever
    attached; `ReadOnlyAccess` was the obvious choice and it reads the dump
    buckets, which even the dump task may not.
    """
    role = _drift_role(hardened)
    props = role["Properties"]
    assert not props.get("ManagedPolicyArns"), "the drift role must not carry a managed policy"

    trust = props["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1 and trust[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    subjects = _as_list(trust[0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"])
    assert subjects and all(s.endswith(":ref:refs/heads/main") for s in subjects), (
        f"the drift role is assumable from outside main: {subjects}"
    )

    allowed: list[tuple[str, list]] = []
    denied: set[str] = set()
    for doc in props.get("Policies", []):
        for stmt in doc["PolicyDocument"]["Statement"]:
            actions = _as_list(stmt.get("Action", []))
            if stmt["Effect"] == "Deny":
                denied.update(actions)
            else:
                allowed.extend((a, _as_list(stmt["Resource"])) for a in actions)
    assert allowed, "the drift role rendered no allowed actions — this guard would pass vacuously"

    cloudformation = set(drift_reader.CLOUDFORMATION_STACK_ACTIONS) | set(drift_reader.CLOUDFORMATION_UNSCOPED_ACTIONS)
    stacks = {
        "arn:aws:cloudformation:ap-south-1:123456789012:stack/reep-core/*",
        "arn:aws:cloudformation:ap-south-1:123456789012:stack/reep-voice-platform/*",
    }
    for action, resources in allowed:
        assert action in cloudformation or re.fullmatch(r"[a-z0-9-]+:(Describe|Get|List)[A-Za-z]+", action), (
            f"reep-github-drift gained {action!r}, which is not a read"
        )
        for data_read in drift_reader.DATA_READS_DENIED:
            assert not fnmatch.fnmatch(action, data_read), f"reep-github-drift may {action!r}, a read of DATA"
        if action in drift_reader.CLOUDFORMATION_STACK_ACTIONS:
            assert set(resources) == stacks, f"{action} reaches {resources}, not just the two stacks"
    assert {a for a, _ in allowed} >= cloudformation, "a drift call is missing, so the check cannot answer"
    assert set(drift_reader.DATA_READS_DENIED) <= denied, (
        f"the explicit Deny lost {sorted(set(drift_reader.DATA_READS_DENIED) - denied)}"
    )


def test_the_drift_role_is_harden_only_and_a_failed_create_leaves_nothing(imported: Template, hardened: Template) -> None:
    """The import phase mirrors what Terraform handed over, and this role was
    never part of it. And a fixed name under plain Retain is #124: a create
    that rolls back keeps the role, and the retry fails on "already exists"."""
    names = [r["Properties"].get("RoleName") for r in imported.find_resources("AWS::IAM::Role").values()]
    assert "reep-github-drift" not in names
    assert _drift_role(hardened).get("DeletionPolicy") == "RetainExceptOnCreate"
    outputs = hardened.to_json().get("Outputs", {})
    assert "GithubDriftRoleArn" in outputs


def test_cdk_json_pins_the_nat_ami_the_deployment_is_actually_running(hardened: Template) -> None:
    """#118. The NAT AMI came from AWS's moving `ami-amazon-linux-latest`
    parameter, ImageId is a replacement property, and so every reep-core deploy
    after AWS shipped an AL2023 image rebuilt the NAT box: a few seconds of no
    egress, an untried image under the user data, and the old instance left
    running behind Retain. infra-drift.yml reported it on every such morning.
    The pin is the AMI the live instance runs (the drift report of 2026-10-15
    shows it as the deployed ImageId); bump it on purpose, never by accident."""
    ami = CDK_JSON_CONTEXT.get("natAmiId")
    assert isinstance(ami, str) and re.fullmatch(r"ami-[0-9a-f]{8,17}", ami), f"cdk.json natAmiId is {ami!r}"
    nat = hardened.to_json()["Resources"]["NatInstance"]["Properties"]
    assert nat["ImageId"] == ami, f"the NAT instance does not run the pinned AMI: {nat['ImageId']}"
    parameters = hardened.to_json().get("Parameters", {})
    assert not [p for p in parameters if "amiamazonlinuxlatest" in p.lower()], (
        "the template still resolves the NAT AMI from the moving 'latest' parameter"
    )


def test_a_malformed_nat_ami_is_refused() -> None:
    with pytest.raises(ValueError, match="natAmiId"):
        _core("harden", natAmiId="al2023-latest", drVaultArn=_DR_VAULT)


def test_without_a_pin_the_nat_ami_still_resolves() -> None:
    """A fresh account has no AMI to pin yet; the first deploy must still work."""
    t = _core("harden", natAmiId="", drVaultArn=_DR_VAULT)
    image = t.to_json()["Resources"]["NatInstance"]["Properties"]["ImageId"]
    assert isinstance(image, dict) and "Ref" in image


def test_a_failed_nat_create_leaves_nothing_to_collide_with(hardened: Template) -> None:
    """#124. The first core-nat-instance run rolled back on 2026-10-03, plain
    Retain kept the security group, `reep-nat-instance` role and profile and
    the instance, and the retry was refused at change-set validation because
    the role "already exists". RetainExceptOnCreate deletes what a failed
    create made and still never deletes a box that was running."""
    resources = hardened.to_json()["Resources"]
    nat = {
        lid: res for lid, res in resources.items()
        if lid in ("NatInstanceSg", "NatInstanceRole", "NatInstanceProfile", "NatInstance")
    }
    assert len(nat) == 4, f"expected the four NAT resources, found {sorted(nat)}"
    for lid, res in nat.items():
        assert res.get("DeletionPolicy") == "RetainExceptOnCreate", f"{lid}: {res.get('DeletionPolicy')}"
        assert res.get("UpdateReplacePolicy") == "Retain", f"{lid}: {res.get('UpdateReplacePolicy')}"


def _db_class(t: Template) -> str:
    return next(r for r in t.to_json()["Resources"].values() if r["Type"] == "AWS::RDS::DBInstance")["Properties"]["DBInstanceClass"]


def test_harden_moves_the_database_to_its_target_size_and_the_mirror_keeps_the_live_one() -> None:
    """2026-09-29: the live db.t4g.micro allows roughly 80-110 connections, too
    few for the api tasks autoscaling may start. The target is its own key in
    cdk.json (dbInstanceClassTarget); `dbInstanceClass` stays what the import
    tool read from the live instance, so the mirror never describes a database
    that does not exist yet -- the dbMultiAz / liveDbMultiAz split, again."""
    assert CDK_JSON_CONTEXT["dbInstanceClassTarget"] == "db.t4g.small"
    assert _db_class(_core("import", dbInstanceClass="db.t4g.micro")) == "db.t4g.micro"
    assert _db_class(_core("harden", dbInstanceClass="db.t4g.micro")) == "db.t4g.small"


def test_the_api_pool_times_the_task_ceiling_fits_the_database(hardened: Template) -> None:
    """Every task may hold DB_POOL_SIZE + DB_MAX_OVERFLOW connections, and
    autoscaling may run apiMaxTasks of them. Left at app/config.py's 20 + 20,
    ten tasks would ask for 400 -- the refusal would land on the busiest day."""
    from reep_core.stack import API_DB_MAX_OVERFLOW, API_DB_POOL_SIZE

    envs = [
        {e["Name"]: e["Value"] for e in c.get("Environment", [])}
        for r in hardened.to_json()["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
        for c in r["Properties"]["ContainerDefinitions"]
    ]
    api = [e for e in envs if e.get("ENV") == "prod"]
    assert api, "no api container found"
    for env in api:
        assert env["DB_POOL_SIZE"] == str(API_DB_POOL_SIZE)
        assert env["DB_MAX_OVERFLOW"] == str(API_DB_MAX_OVERFLOW)
    # db.t4g.small (2 GiB): max_connections is LEAST(DBInstanceClassMemory/9531392, 5000), about 200.
    assert (API_DB_POOL_SIZE + API_DB_MAX_OVERFLOW) * 10 <= 200


def _api_envs(t: Template) -> list[dict[str, str]]:
    return [
        {e["Name"]: e["Value"] for e in c.get("Environment", [])}
        for r in t.to_json()["Resources"].values()
        if r["Type"] == "AWS::ECS::TaskDefinition"
        for c in r["Properties"]["ContainerDefinitions"]
        if any(e.get("Name") == "ENV" and e.get("Value") == "prod" for e in c.get("Environment", []))
    ]


def _interview_policies(t: Template) -> list[dict]:
    return [
        r["Properties"]
        for r in t.to_json()["Resources"].values()
        if r["Type"] == "AWS::ApplicationAutoScaling::ScalingPolicy"
        and r["Properties"].get("PolicyName") == "interview-target"
    ]


def test_the_api_scales_on_the_interviews_it_publishes(hardened: Template) -> None:
    """2026-10-29: the policy watches the metric the api writes, dimensioned by
    the family the task definition tells it — a mismatch in any of the three
    is a policy that never fires and never errors."""
    from reep_core.stack import (
        INTERVIEW_METRIC_DIMENSION,
        INTERVIEW_METRIC_NAME,
        INTERVIEW_METRIC_NAMESPACE,
        INTERVIEWS_PER_TASK_MAX,
        INTERVIEWS_PER_TASK_TARGET,
    )

    (policy,) = _interview_policies(hardened)
    cfg = policy["TargetTrackingScalingPolicyConfiguration"]
    spec = cfg["CustomizedMetricSpecification"]
    assert spec["Namespace"] == INTERVIEW_METRIC_NAMESPACE
    assert spec["MetricName"] == INTERVIEW_METRIC_NAME
    assert spec["Statistic"] == "Average"
    assert spec["Dimensions"] == [{"Name": INTERVIEW_METRIC_DIMENSION, "Value": "reep-api"}]
    assert cfg["TargetValue"] == INTERVIEWS_PER_TASK_TARGET
    assert INTERVIEWS_PER_TASK_TARGET < INTERVIEWS_PER_TASK_MAX, "scale out before a task starts refusing"

    (env,) = _api_envs(hardened)
    assert env["INTERVIEW_METRICS_FAMILY"] == "reep-api"
    assert env["INTERVIEW_MAX_SESSIONS"] == str(INTERVIEWS_PER_TASK_MAX)


def test_the_import_mirror_carries_no_interview_scaling(imported: Template) -> None:
    """A new policy cannot be adopted by `cdk import`, and a new variable is a
    task-definition revision the mirror must not register."""
    assert not _interview_policies(imported)
    for env in _api_envs(imported):
        assert "INTERVIEW_METRICS_FAMILY" not in env
        assert "INTERVIEW_MAX_SESSIONS" not in env


def test_the_fleet_ceiling_is_the_bedrock_quota_and_reaches_the_task() -> None:
    """cdk.json holds the account's Nova 2 Sonic concurrent-stream quota (2 on
    2026-10-29, not adjustable in Service Quotas; raised only by an AWS Support
    case). Raise this number in the same commit as the quota, never before."""
    assert CDK_JSON_CONTEXT["interviewMaxConcurrent"] == 2
    env = _api_envs(_core("harden", interviewMaxConcurrent=40))
    assert env and all(e["INTERVIEW_MAX_CONCURRENT"] == "40" for e in env)
    off = _api_envs(_core("harden", interviewMaxConcurrent=0))
    assert off and all("INTERVIEW_MAX_CONCURRENT" not in e for e in off)


def test_a_ceiling_the_tasks_could_never_carry_is_refused_at_synth() -> None:
    from reep_core.stack import INTERVIEWS_PER_TASK_MAX

    reachable = 10 * INTERVIEWS_PER_TASK_MAX
    _core("harden", apiMaxTasks=10, interviewMaxConcurrent=reachable)
    with pytest.raises(ValueError, match="interviewMaxConcurrent"):
        _core("harden", apiMaxTasks=10, interviewMaxConcurrent=reachable + 1)


def _edge_with_logs(phase: str = "harden", days: int | None = 30) -> dict:
    ctx: dict = {"phase": phase, "cloudfrontDistributionId": "E2TESTDIST"}
    if days is not None:
        ctx["cloudfrontLogDays"] = days
    stack = EdgeWafStack(cdk.App(context=ctx), "test-edge-logs", env=cdk.Environment(account="123456789012", region="us-east-1"))
    return Template.from_stack(stack).to_json()["Resources"]


def _one(res: dict, kind: str) -> dict:
    (found,) = [r for r in res.values() if r["Type"] == kind]
    return found


def test_cloudfront_logs_every_request_and_never_a_token_or_a_session() -> None:
    """2026-10-01: "did CloudFront send that 504?" could only be answered by
    reasoning from per-minute metrics, because the distribution logged
    nothing. Standard logging v2 to S3, harden only — and v2 because only it
    can leave fields out: the query string carries the setup, reset and
    activation tokens, the cookie IS the session, and the referer carries the
    previous page's query string."""
    res = _edge_with_logs()
    fields = _one(res, "AWS::Logs::Delivery")["Properties"]["RecordFields"]
    assert fields == list(CLOUDFRONT_LOG_FIELDS)
    for never in CLOUDFRONT_LOG_FIELDS_NEVER:
        assert never not in fields, f"{never} would put bearer material in a log"
    assert {"cs-uri-query", "cs(Cookie)", "cs(Referer)"} <= set(CLOUDFRONT_LOG_FIELDS_NEVER)
    for needed in ("date", "time", "sc-status", "cs-uri-stem", "x-edge-detailed-result-type", "x-edge-request-id"):
        assert needed in fields, f"{needed} is what settles an incident"
    source = _one(res, "AWS::Logs::DeliverySource")["Properties"]
    assert source["LogType"] == "ACCESS_LOGS"
    assert source["ResourceArn"] == "arn:aws:cloudfront::123456789012:distribution/E2TESTDIST"


def test_the_log_bucket_is_private_expires_and_admits_only_log_delivery() -> None:
    res = _edge_with_logs(days=30)
    bucket = _one(res, "AWS::S3::Bucket")["Properties"]
    assert bucket["BucketName"] == "reep-cloudfront-logs-123456789012"
    assert bucket["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
    }
    rules = bucket["LifecycleConfiguration"]["Rules"]
    assert [r["ExpirationInDays"] for r in rules] == [30], "c-ip is personal data; the log must expire"
    statements = _one(res, "AWS::S3::BucketPolicy")["Properties"]["PolicyDocument"]["Statement"]
    writes = [s for s in statements if s.get("Sid") == "CloudFrontLogDeliveryWrite"]
    assert len(writes) == 1
    (write,) = writes
    assert write["Principal"] == {"Service": "delivery.logs.amazonaws.com"}
    assert write["Action"] == "s3:PutObject"
    assert write["Condition"]["StringEquals"]["aws:SourceAccount"] == "123456789012"
    for lid, r in res.items():
        assert r.get("DeletionPolicy") == "Retain", lid


def test_cloudfront_logging_is_off_without_a_retention_and_in_the_import_mirror() -> None:
    for res in (_edge_with_logs(days=None), _edge_with_logs(days=0), _edge_with_logs(phase="import")):
        types = {r["Type"] for r in res.values()}
        assert not types & {"AWS::Logs::Delivery", "AWS::Logs::DeliverySource", "AWS::S3::Bucket"}, types


def test_the_repository_carries_the_logging_the_edge_deploy_should_ship() -> None:
    """Written down, not passed with -c: a flag set at deploy time and not
    persisted is not configuration (see the apiArm64 test above)."""
    here = Path(__file__).resolve().parents[1]
    assert json.loads((here / "cdk.json").read_text())["context"].get("cloudfrontLogDays", 0) > 0
    assert json.loads((here / "cdk.context.json").read_text()).get("cloudfrontDistributionId")
