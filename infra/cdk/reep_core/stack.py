"""REEP's core AWS footprint as one CDK stack — the successor to infra/aws/.

WHY THIS FILE IS SHAPED THE WAY IT IS. Every resource here already EXISTS,
created by Terraform and holding real student data. This stack is not a fresh
build; it is a description of what is running, precise enough that
`cdk import` can adopt each resource into CloudFormation without touching it.
That single fact decides three things:

  1. PHYSICAL NAMES ARE THE TERRAFORM NAMES, LITERALLY. `reep-api-task`,
     `/reep/api`, `reep-postgres`, `reep-vault`. An import matches on the
     identifier and then compares properties; a different name is a different
     resource, and for an immutable property (a security group's name, a task
     family) the next deploy would REPLACE it — which for the database is an
     outage and for a security group is every task losing its network. The
     names are asserted against the .tf files by the synth tests.

  2. L1 (`Cfn*`) WHEREVER L2 WOULD INVENT STRUCTURE. `ec2.Vpc` lays out its own
     subnets and route tables; `iam.OpenIdConnectProvider` is a Lambda-backed
     custom resource; `rds.DatabaseInstance` mints a new master password.
     None of those can be imported over what exists. L2 is used only where it
     maps 1:1 onto one CloudFormation resource with the properties we need.

  3. TWO PHASES, ONE CONTEXT KEY. `-c phase=import` renders the mirror of what
     exists and NOTHING else — `cdk import` refuses a template that also adds
     resources it cannot import. `-c phase=harden` (the default) is the same
     stack plus every fix this migration exists to make. The synth tests pin
     that the import phase is a strict subset.

WHAT `harden` ADDS, and why each is here rather than in Terraform:

  * The container's `stopTimeout` is 120 s — FARGATE'S HARD MAXIMUM, not the
    500 s the first plan assumed. A 480 s interview is therefore protected by
    the target group's DEREGISTRATION DELAY (600 s): the ALB stops routing
    new connections to a draining task but keeps its open WebSockets until
    they close, and ECS waits for draining before it sends SIGTERM. Then
    uvicorn's `--timeout-graceful-shutdown 110` (Dockerfile) fits inside the
    120. The api's own number is pinned against this file by
    tests/test_codebase_guards.py, so the two cannot drift apart silently.
  * The backup vault is LOCKED (governance mode: a minimum retention nobody
    can shorten by accident), every recovery point is COPIED to a vault in
    ap-southeast-1, and a weekly RESTORE TEST proves the copies are usable —
    a backup that has never been restored is a hope, not a backup.
  * RDS automated retention and the AWS Backup rule read ONE number
    (`backupRetentionDays`). They were 14 and 35 — two answers to "how far
    back can we go", and the honest one was the shorter.
  * An ARCHIVE TIER above it (`archiveRetentionDays`, off unless set): a
    second plan, monthly, with an RDS-ONLY selection and a lifecycle measured
    in years. 35 days is RDS's ceiling on its own automated backups, not AWS
    Backup's — so without this every copy of the database in both regions
    expired on day 36 and a loss discovered five weeks later was unrecoverable
    everywhere at once. It is a second PLAN because a selection is plan-scoped:
    on the daily plan the same rule would keep every student's uploaded
    document for years as well. See the constant for what that costs.
  * Multi-AZ on by default (`-c dbMultiAz=false` to opt out — it doubles the
    instance cost, and that is a decision, so it is written down here).
  * The task role may send mail through SES for the college's verified
    identity (activation and reset links, app/mail_transport.py).
  * BLUE/GREEN, behind `-c blueGreen=true` (default OFF, and only with the ECS
    half): a second long-lived service `api-green` on target group
    `reep-api-green`, two colour-pinned task families (`reep-api-blue` on
    `:blue`, `reep-api-green` on `:green`), and a priority-10 listener RULE
    whose weighted forward says which colour takes new connections. The
    deploy workflow rolls the IDLE colour, proves it through header-routed
    probe rules, then rewrites the rule's two weights; rollback is the same
    call the other way, in seconds, onto tasks that are already warm. No
    CodeDeploy and no deployment controller — switching an existing
    service's controller is a REPLACE of the live service. The existing
    `api` / `reep-api` pair is kept, name for name, as the blue colour.
    docs/blue-green-cutover.md.
  * A `MasterUserPassword` NEVER appears in this template. The instance keeps
    the password Terraform generated, held inside the app secret's
    DATABASE_URL. Writing one here would rotate it under the running api. A
    synth test refuses the template if the property ever shows up.

Two Terraform resources have no counterpart and are dropped on purpose:
`random_password.db` and `random_password.auth_secret` — their VALUES live in
the secrets already; the generators are not infrastructure. The two
`aws_secretsmanager_secret_version`s are likewise not modelled: this stack
imports the secrets by ARN and never writes a version. Secret values are the
operator's, and a CloudFormation deploy must not be able to overwrite them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aws_cdk import (
    Aspects,
    CfnOutput,
    CfnResource,
    Duration,
    Fn,
    IAspect,
    RemovalPolicy,
    Stack,
    Tags,
    aws_applicationautoscaling as appscaling,
    aws_backup as backup,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_efs as efs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
    aws_scheduler as scheduler,
    aws_secretsmanager as sm,
    aws_ses as ses,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_ssm as ssm,
)
from constructs import Construct, IConstruct
import jsii

from . import drift_reader


@jsii.implements(IAspect)
class RetainEverything:
    """`DeletionPolicy: Retain` on every resource that has not chosen one.

    Two reasons, one for each phase. `cdk import` REQUIRES Retain on every
    resource it adopts (the CLI adds it where missing; setting it here keeps
    it in the harden template too). And after
    the cutover this stack owns the database, the file system, the buckets and
    the log group — a construct-id rename, a refactor that moves a resource,
    or a `cdk destroy` typed into the wrong terminal must forget them, never
    delete them. Explicit policies (the database's Snapshot) are left alone.
    """

    def visit(self, node: IConstruct) -> None:
        if isinstance(node, CfnResource) and node.cfn_options.deletion_policy is None:
            node.apply_removal_policy(RemovalPolicy.RETAIN)


#: Fargate's hard ceiling for a container's stopTimeout. Not a choice: the
#: service refuses a task definition above it. See the module docstring for
#: what actually protects a live interview.
STOP_TIMEOUT_SECONDS = 120

#: How long the ALB keeps a draining target's open connections. Must exceed the
#: longest interview the api will hold on one socket (config.py's
#: nova_sonic_connection_seconds, 480) plus the scorecard tail. Pinned against
#: the api's number by tests/test_codebase_guards.py.
DEREGISTRATION_DELAY_SECONDS = 600

#: Each api task's SQLAlchemy pool: this many kept open, plus this many more
#: under burst. Times apiMaxTasks it must stay under the database's
#: max_connections, which a db.t4g.small puts near 200.
API_DB_POOL_SIZE = 10
API_DB_MAX_OVERFLOW = 10

#: THE MOCK INTERVIEW SCALES ON ITSELF (2026-10-29). Each api task publishes how
#: many interviews it is holding, once a minute, as an Embedded Metric Format
#: line (`apps/api-py/app/interview_metrics.py`; the three names below must match
#: it, and `tests/test_interview_metrics.py` there reads them out of this file).
#: A target-tracking policy holds the AVERAGE at INTERVIEWS_PER_TASK_TARGET, so a
#: batch of students starting together adds tasks within a few minutes and the
#: service shrinks back to apiMinTasks once they finish — the idle bill does not
#: move, and apiMaxTasks is still the ceiling on what a busy day can cost.
INTERVIEW_METRIC_NAMESPACE = "REEP/Interviews"
INTERVIEW_METRIC_NAME = "LiveInterviews"
INTERVIEW_METRIC_DIMENSION = "Family"
#: Measured, not guessed: Container Insights on 2026-10-29 put one live interview
#: at 20-35 CPU units of a 512-unit (half-vCPU) task over a ~10-unit idle floor,
#: i.e. 4-7% each. Eight is ~50% with room for the handshake spikes (46 units
#: seen); the CPU policy at 60% stays as the backstop.
INTERVIEWS_PER_TASK_TARGET = 8
#: Where one task REFUSES (INTERVIEW_MAX_SESSIONS, close 1013): ~75% CPU by the
#: same measurement, past which the 24 kHz relay is choppy audio rather than a
#: slower page. The api's own default (100) assumes a far bigger task.
INTERVIEWS_PER_TASK_MAX = 12

#: The one retention number. RDS automated backups and the AWS Backup rule both
#: read it, and the vault lock's minimum is derived from it.
DEFAULT_BACKUP_RETENTION_DAYS = 35

#: THE ARCHIVE TIER, AND WHY IT IS A SECOND NUMBER RATHER THAN A BIGGER ONE.
#: `backupRetentionDays` is capped at 35 below because RDS refuses more on its
#: automated backups — so every copy of the database, in both regions, expired
#: on day 36 and a record deleted, corrupted or mis-migrated five weeks ago was
#: gone everywhere at once. AWS Backup has no such ceiling: a second rule with
#: its own lifecycle is the whole fix, and it is the only one that reaches the
#: WHOLE database rather than a chosen subset of columns.
#:
#: ZERO IS OFF, and that is the code default rather than a year, so a synth
#: with no context renders the stack that exists today. cdk.json turns it on,
#: the way `blueGreen` is turned on: the number is a decision, so it is written
#: down where a decision is read.
#:
#: WHAT IT COSTS BESIDES MONEY, said here because nothing else will say it: a
#: student erased by `python -m app.purge_students` stays restorable, and fully
#: identified, for as long as this number runs — in a vault whose governance
#: lock neither destructor can reach. That is a records-retention decision for
#: the college, not a default, which is the other reason it is a separate key.
DEFAULT_ARCHIVE_RETENTION_DAYS = 0

#: AWS Backup's own ceiling on a lifecycle (100 years).
MAX_ARCHIVE_RETENTION_DAYS = 36500

#: How long the DAILY logical dump is locked, and therefore kept.
#:
#: The physical snapshots already answer "restore last Tuesday" for 35 days.
#: This tier exists to answer it from an artefact that does not need RDS at
#: all, so a number at or below the snapshots' would add nothing they do not
#: already give; 90 is the span across which a loss is usually noticed.
DEFAULT_DUMP_DAILY_DAYS = 90

#: The gap between the daily object's Object Lock expiry and the lifecycle
#: rule that deletes it.
#:
#: NOT padding, and not a rounding allowance. A lifecycle expiration aimed at
#: an object whose retention has not lapsed is NOT AN ERROR -- S3 re-evaluates
#: it the next day, and the next, deleting nothing and reporting nothing, so
#: the bucket grows without bound behind a rule the console shows as working.
#: Setting the two to the same number makes that race a coin toss on every
#: object. Seven days makes it impossible.
DUMP_LIFECYCLE_LAG_DAYS = 7

#: The monthly archive copy's Object Lock retention, in years.
#:
#: THIS IS A PLACEHOLDER FOR A DECISION THE COLLEGE HAS NOT MADE. The real
#: question is "how long must a graduate's academic record remain retrievable",
#: which is a records-retention policy and not an engineering choice —
#: `archiveRetentionDays` carries the same caveat for the same reason. Ten
#: years is long enough to be useful and short enough not to be a promise
#: nobody agreed to. It costs a few dollars a year at this data volume.
DEFAULT_DUMP_ARCHIVE_YEARS = 10

#: How long an uploaded DOCUMENT is locked in the permanent archive, in years.
#:
#: THE SAME PLACEHOLDER CAVEAT `DEFAULT_DUMP_ARCHIVE_YEARS` CARRIES, and the
#: same unanswered question: "how long must a graduate's academic record remain
#: retrievable" is a records-retention policy and not an engineering choice.
#: The difference is what the number governs. That one bounds a `pg_dump` --
#: rows, re-derivable in principle from a later dump. This one bounds the only
#: copy of a scanned marksheet a student has since deleted, which nothing
#: regenerates. It is deliberately the same ten so the two tiers expire
#: together: a deployment holding the rows that describe a certificate for a
#: decade and the certificate itself for one year has a record that decays into
#: a set of dangling pointers.
DEFAULT_DOCUMENT_ARCHIVE_YEARS = 10

#: CloudFront's managed policy ids, the same three cdn.tf hardcodes.
_CACHE_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
_CACHE_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
_ORIGIN_ALL_VIEWER = "216adef6-5c7f-47e4-b989-5492eafa07d3"

_SPA_FALLBACK_JS = """\
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  // Belt and braces: this function is not attached to the /api/* behavior,
  // but if it is ever attached more widely by mistake, the API must still
  // keep its own status codes.
  if (uri.startsWith('/api/')) {
    return request;
  }
  var last = uri.substring(uri.lastIndexOf('/') + 1);
  if (last.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
"""


class CoreStack(Stack):
    """See the module docstring. Every knob is a CDK context value so the
    import-map tool (tools/import_map.py) can fill the ones that only the live
    account knows — bucket suffixes, security-group names, secret ARNs."""

    def __init__(self, scope: Construct, construct_id: str, *, project: str = "reep", **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.project = project
        ctx = self.node.try_get_context
        self.phase = (ctx("phase") or "harden").strip().lower()
        if self.phase not in ("import", "harden"):
            raise ValueError(f"phase must be 'import' or 'harden', not {self.phase!r}")
        harden = self.phase == "harden"
        self.harden = harden

        # ------------------------------------------------------------ knobs --
        def opt(key: str, default: Any = None) -> Any:
            v = ctx(key)
            return default if v is None or v == "" else v

        def flag(key: str, default: bool) -> bool:
            v = ctx(key)
            if v is None or v == "":
                return default
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        domain_name: str = opt("domainName", "")
        cf_cert_arn: str = opt("cloudfrontAcmCertificateArn", "")
        alb_cert_arn: str = opt("albAcmCertificateArn", "")
        alb_origin_domain: str = opt("albOriginDomain", "")
        restrict_to_cloudfront = flag("restrictAlbToCloudfront", True)
        cloudfront_prefix_list = opt("cloudfrontPrefixListId", "")
        azs: list[str] = list(opt("availabilityZones", ["ap-south-1a", "ap-south-1b"]))
        api_cpu = int(opt("apiCpu", 512))
        api_memory = int(opt("apiMemory", 1024))
        api_min = int(opt("apiMinTasks", 2))
        api_max = int(opt("apiMaxTasks", 10))
        # THE FLEET'S INTERVIEW CEILING (INTERVIEW_MAX_CONCURRENT). Set it to the
        # account's Bedrock quota for Nova 2 Sonic concurrent streams, or lower
        # if the college wants a smaller bill: past the quota a student is
        # refused UPSTREAM after their attempt was counted, while past this the
        # api says "too many interviews are running" and counts nothing. 0 is
        # off. Refused at synth when the tasks could never carry it, because a
        # ceiling the fleet cannot reach is a promise the screen cannot keep.
        interview_max_concurrent = int(opt("interviewMaxConcurrent", 0))
        if interview_max_concurrent < 0:
            raise ValueError(f"interviewMaxConcurrent must be 0 (off) or positive, got {interview_max_concurrent}")
        if interview_max_concurrent > api_max * INTERVIEWS_PER_TASK_MAX:
            raise ValueError(
                f"interviewMaxConcurrent={interview_max_concurrent} needs more than apiMaxTasks={api_max} tasks of "
                f"{INTERVIEWS_PER_TASK_MAX} interviews each; raise apiMaxTasks (and check the database's connection "
                f"budget, API_DB_POOL_SIZE + API_DB_MAX_OVERFLOW per task) or lower the ceiling"
            )
        # GRAVITON: the same vCPU and the same memory for ~20% less, and the
        # ONLY change on the cost review's list that alters no capacity at all
        # (docs/cost-review-2026-09.md). It is a FLAG, defaulting to x86, for
        # two reasons that pull the same way.
        #
        # ORDER. `deploy.yml` publishes a MULTI-ARCH manifest (amd64 + arm64),
        # so the image can serve either architecture — but the manifest has to
        # exist first. Flipping this on a repository whose last image is
        # amd64-only gives `image Manifest does not contain descriptor matching
        # platform linux/arm64`: every task dies at pull, before a health check,
        # and the circuit breaker rolls back. Merge is therefore INERT; the flip
        # is a separate, deliberate act taken after one Deploy has run.
        #
        # ROLLBACK. Because the manifest carries both, going back is this flag
        # and nothing else — no rebuild, no image retag. That is worth more than
        # the arm64-only image that would have been simpler to publish.
        #
        # And it must stay conditional rather than become the default: passing
        # X86_64 explicitly would RENDER a RuntimePlatform property where the
        # live task definition has none, which is a diff against the import
        # mirror that test_the_database_half_does_not_touch_the_ecs_trio exists
        # to refuse. None renders nothing, which is what the mirror holds.
        api_arm64 = flag("apiArm64", False)
        db_class: str = opt("dbInstanceClass", "db.t4g.small")
        # `dbInstanceClass` is the LIVE size tools/import_map.py wrote into
        # cdk.context.json, which the import mirror must carry; the size harden
        # MOVES the database to is its own key in cdk.json, the dbMultiAz /
        # liveDbMultiAz split below applied to the instance class (2026-09-29).
        # One key read by both phases would make the import mirror describe a
        # database that does not exist yet.
        #
        # db.t4g.small and not the micro the import found: a micro's
        # max_connections is roughly 80-110, and every api task may open
        # DB_POOL_SIZE + DB_MAX_OVERFLOW connections, so a third task started by
        # autoscaling on a busy day could exhaust it. Changing this on a live
        # Multi-AZ instance is a failover of about a minute, not an outage.
        if harden:
            db_class = opt("dbInstanceClassTarget", db_class)
        # THE IMPORT MIRROR CARRIES THE LIVE VALUES, the harden template the
        # targets — and they must come from DIFFERENT context keys. cdk.json
        # sets the harden targets (dbMultiAz, backupRetentionDays) and the CLI
        # always loads cdk.json, so an import phase that read the same keys
        # would render Multi-AZ against a single-AZ instance. Import does not
        # compare properties, so it would succeed — and then harden, carrying
        # the same value, would send no ModifyDBInstance, and Multi-AZ would
        # silently never happen. tools/import_map.py writes the live* keys
        # from the state.
        if harden:
            db_multi_az = flag("dbMultiAz", True)
            retention_days = int(opt("backupRetentionDays", DEFAULT_BACKUP_RETENTION_DAYS))
        else:
            db_multi_az = flag("liveDbMultiAz", False)
            retention_days = int(opt("liveBackupRetentionDays", 14))
        if not 1 <= retention_days <= 35:
            raise ValueError(f"backupRetentionDays must be 1..35 (RDS refuses more), not {retention_days}")
        # The archive tier is harden-only. THE IMPORT MIRROR CARRIES THE LIVE
        # VALUES (see the block above): an import template that grew a second
        # backup plan would stop being a mirror of what exists, and
        # CloudFormation refuses an import template that adds a resource it
        # cannot adopt.
        # THE IDENTITY LEDGER'S BUCKET (app/export_identity.py). Harden-only,
        # like every other resource this stack ADDS rather than mirrors: the
        # import phase must stay a strict subset of it.
        #
        # Its own bucket and never a prefix under an existing one. Every other
        # store here carries a lifecycle rule -- alb-logs expires at 90 days --
        # and a prefix would inherit whatever the parent bucket's rules become.
        # A ledger that quietly acquires an expiry is worse than no ledger,
        # because it reads as protection right up until the day it is asked for
        # something older than the rule nobody remembered setting.
        identity_ledger = harden and flag("identityLedger", True)
        # GOVERNANCE, matching `vaultLockCompliance`'s default posture. Compliance
        # mode cannot be undone by anybody including root, which is the correct
        # end state and the wrong thing to switch on in the same change that
        # creates the bucket: a misconfigured retention would be permanent too.
        # Flip it once a few days of objects have been read back.
        identity_ledger_compliance = flag("identityLedgerCompliance", False)
        identity_ledger_years = int(opt("identityLedgerYears", 10))
        # THE LOGICAL BACKUP'S TWO BUCKETS (app/backup_database.py). Harden-only,
        # like the ledger and for the same reason: the import phase is a strict
        # subset of this template and CloudFormation refuses an import template
        # that adds a resource it cannot adopt.
        #
        # TWO BUCKETS AND NOT TWO PREFIXES, because S3 Object Lock's default
        # retention is a property of the BUCKET. One bucket cannot hold both
        # "the daily copy goes at 90 days" and "the monthly copy is kept for a
        # decade", and the way that fails is the worst kind: a lifecycle
        # expiration blocked by an unexpired lock is not an error, it is a rule
        # that deletes nothing forever while reporting success.
        db_dumps = harden and flag("dbDumps", True)
        db_dump_daily_days = int(opt("dbDumpDailyDays", DEFAULT_DUMP_DAILY_DAYS))
        if not retention_days < db_dump_daily_days <= 3650:
            raise ValueError(
                f"dbDumpDailyDays must be greater than backupRetentionDays "
                f"({retention_days}) and at most 3650, not {db_dump_daily_days}. "
                "A logical tier that expires no later than the physical snapshots "
                "adds nothing the snapshots do not already give."
            )
        # The archive tier can be switched off on its own. A deployment that
        # keeps 90 days and no more is a supported choice; what must not happen
        # is a deployment that believes it keeps years and does not, which is
        # why `app.backup_database` reports the absence on every single run.
        db_dump_archive = db_dumps and flag("dbDumpArchive", True)
        db_dump_archive_years = int(opt("dbDumpArchiveYears", DEFAULT_DUMP_ARCHIVE_YEARS))
        archive_retention_days = int(opt("archiveRetentionDays", DEFAULT_ARCHIVE_RETENTION_DAYS)) if harden else 0
        if archive_retention_days and not retention_days < archive_retention_days <= MAX_ARCHIVE_RETENTION_DAYS:
            raise ValueError(
                f"archiveRetentionDays must be greater than backupRetentionDays "
                f"({retention_days}) and at most {MAX_ARCHIVE_RETENTION_DAYS}, not "
                f"{archive_retention_days}. Shorter than the daily rule is not an "
                "archive, and the DR vault's lock refuses a copy whose lifecycle is "
                "below its minimum retention."
            )
        # THE PERMANENT DOCUMENT ARCHIVE (app/document_archive.py). The FILE
        # half of the two tiers above, and the only one of the three that
        # carries bytes.
        #
        # WHY IT IS NOT COVERED BY ANYTHING ALREADY HERE. The daily backup
        # selection below reaches the EFS file system, so an uploaded file had
        # exactly one copy beyond the volume and that copy expires at
        # `backupRetentionDays` -- 35, because RDS refuses more and both halves
        # read one number. The ARCHIVE selection that reaches past 35 days
        # deliberately names `[db_arn]` and nothing else, for the reason
        # written at that selection: a multi-year lifecycle over EFS would keep
        # every recorded interview for years too. And both `pg_dump` tiers and
        # the identity ledger carry Postgres rows, never file bytes. So a
        # marksheet deleted from the website was recoverable for 35 days and
        # then gone in both regions at once, with nothing on any screen saying
        # so. A college keeps a student's academic record for decades; 35 days
        # is not a retention policy, it is the absence of one.
        #
        # ITS OWN BUCKET, for the ledger's reason stated above: a prefix under
        # an existing bucket inherits whatever that bucket's lifecycle becomes,
        # and a permanent archive that quietly acquires an expiry reads as
        # protection right up until the day it is asked for something older
        # than the rule nobody remembered setting.
        document_archive = harden and flag("documentArchive", True)
        # GOVERNANCE, matching the ledger and the dump archive. Compliance mode
        # is the correct end state and the wrong thing to switch on in the
        # change that creates the bucket: a retention typed wrong would be
        # permanent too, on objects nobody -- including root -- could remove.
        document_archive_compliance = flag("documentArchiveCompliance", False)
        document_archive_years = int(opt("documentArchiveYears", DEFAULT_DOCUMENT_ARCHIVE_YEARS))
        if not 1 <= document_archive_years <= 100:
            raise ValueError(
                f"documentArchiveYears must be 1..100, not {document_archive_years}. "
                "It is an Object Lock retention in years and cannot be shortened "
                "on objects already written."
            )
        allocated_storage = str(opt("liveAllocatedStorage", 20))
        # hardenEcs=false: the database/backup half of harden without the ECS
        # half, so an ECS circuit-breaker rollback cannot also undo a Multi-AZ
        # conversion in the same stack update. Step 9 runs them separately.
        harden_ecs = harden and flag("hardenEcs", True)
        # blueGreen: a SECOND long-lived api service (`api-green`, target group
        # `reep-api-green`) beside the existing one, and a listener RULE whose
        # weighted forward decides which colour takes new connections. The
        # deploy workflow moves traffic by rewriting the rule's two weights;
        # rollback is the same call the other way. It sits behind hardenEcs
        # (it relies on the 600 s drain) and DEFAULTS OFF in cdk.json, so an
        # unintended `cdk deploy reep-core` after 9b cannot also create a second
        # service, and the import mirror is byte-identical with the key absent.
        # docs/blue-green-cutover.md is the runbook; every guard is in
        # tests/test_core_synth.py under "blue/green".
        blue_green = harden_ecs and flag("blueGreen", False)
        # liveColour: which colour the RULE's template gives weight 100. It is
        # co-owned: the workflow rewrites the live weights out of band, and any
        # later CloudFormation update that touches the rule re-sends THESE. So
        # this must always say what is live — tools/colour_preflight.sh refuses
        # a core deploy when it does not.
        live_colour = str(opt("liveColour", "blue")).strip().lower()
        if live_colour not in ("blue", "green"):
            raise ValueError(f"liveColour must be 'blue' or 'green', not {live_colour!r}")
        self.blue_green = blue_green
        self.live_colour = live_colour
        github_oidc_arn: str = opt("githubOidcProviderArn", "")
        alert_email: str = opt("alertEmail", "")
        observer_principal: str = opt("observerPrincipalArn", "")
        gh_repo: str = opt("githubRepository", "darshani8/reep-")
        gh_repo_ids: str = opt("githubRepositoryIds", "darshani8@285224354/reep-@1339637272")
        gh_ref: str = opt("githubDeployRef", "refs/heads/main")
        waf_acl_arn: str = opt("wafWebAclArn", "")
        dr_vault_arn: str = opt("drVaultArn", "")
        ses_identity_domain: str = opt("sesIdentityDomain", "")
        ses_from_address: str = opt("sesFromAddress", "")
        ses_configuration_set: str = opt("sesConfigurationSet", "")
        ses_notifications_email: str = opt("sesNotificationsEmail", alert_email)
        # sesManaged: does THIS TEMPLATE own the SES identity, the configuration
        # set, its event destination, the notifications topic and the two
        # reputation alarms? It DEFAULTS OFF and must stay off until those five
        # resources have been adopted with `cdk import`, because they already
        # exist -- they were made by hand on 2026-09-09/10, before any of this
        # was in a repository -- and CloudFormation cannot CREATE an SES
        # identity that is already verified. The failure if this is flipped
        # early is loud (AlreadyExists, rolled back), which is the good half;
        # the bad half is that a rollback of reep-core is a rollback of the
        # whole api. docs/ses-mail.md is the adoption runbook, and it is one
        # read-only command plus one `cdk import`.
        #
        # RECREATING THE IDENTITY IS NOT AN ALTERNATIVE TO IMPORTING IT.
        # SES-managed DKIM mints NEW tokens on create, so a delete-and-recreate
        # publishes three CNAMEs nobody has added yet and mail stops until DNS
        # propagates -- on a domain whose DNS this team does not hold.
        ses_managed = flag("sesManaged", False)
        # leaveMailEnabled: B10.5's notifications to the applicant. A DEPLOYMENT
        # decision, so it lives here rather than in app/config.py's default,
        # which stays false for every machine that has no transport.
        leave_mail_enabled = flag("leaveMailEnabled", False)
        if leave_mail_enabled and not ses_from_address:
            # The one combination that is worse than either half: leave mail on
            # over the console transport writes a mail_logs row reading SENT
            # about a message that reached NOBODY, and that row is the only
            # thing anyone looks at afterwards. Refused at synth, where it is
            # free, rather than discovered from a mail_logs table months later.
            raise ValueError("leaveMailEnabled needs sesFromAddress -- mail switched on with no transport records SENT for messages nobody receives")
        # The daily job agent's two boards (app/job_scout.py, 2026-10-23). One
        # flag per board, each OFF by default, and each set in cdk.json only
        # AFTER that board's keys exist in the reep/external secret: a task
        # definition that references a secret KEY the JSON does not hold
        # fails to START, every task on it, the api included -- the
        # `sentryJobsDsn` rule below. `harden_ecs` as well, because adding a
        # secret registers a new task-definition revision and step 9a must
        # leave the task definition byte-identical to the import mirror.
        job_scout_adzuna = harden_ecs and flag("jobScoutAdzuna", False)
        job_scout_jooble = harden_ecs and flag("jobScoutJooble", False)
        # The companies' own public job feeds (Greenhouse, Lever,
        # SmartRecruiters; `app/job_sources.py` COMPANY_BOARDS). No key and no
        # secret, so nothing here can stop a task from starting: the flag puts
        # one plain variable in the task definition and the schedule follows
        # it. `harden_ecs` for the same immutable-task-definition reason.
        job_scout_companies = harden_ecs and flag("jobScoutCompanies", False)
        if ses_managed and not (ses_identity_domain and ses_configuration_set):
            raise ValueError("sesManaged needs both sesIdentityDomain and sesConfigurationSet -- the mirror must name what it is adopting")
        vault_lock_compliance = flag("vaultLockCompliance", False)
        # Identifiers only the live account knows (name_prefix / bucket_prefix
        # gave them random suffixes). tools/import_map.py fills these from the
        # Terraform state; the defaults are shaped so a synth without them still
        # produces a checkable template.
        web_bucket_name: str = opt("webBucketName", f"{project}-web-00000000000000000000")
        alb_logs_bucket_name: str = opt("albLogsBucketName", f"{project}-alb-logs-00000000000000000000")
        app_secret_arn: str = opt(
            "appSecretArn",
            f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{project}/app-00000000000000000000-AbCdEf",
        )
        external_secret_arn: str = opt(
            "externalSecretArn",
            f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{project}/external-00000000000000000000-AbCdEf",
        )
        sg_names = {
            "alb": opt("albSecurityGroupName", f"{project}-alb-00000000000000000000"),
            "api": opt("apiSecurityGroupName", f"{project}-api-00000000000000000000"),
            "db": opt("dbSecurityGroupName", f"{project}-db-00000000000000000000"),
            "efs": opt("efsSecurityGroupName", f"{project}-efs-00000000000000000000"),
        }
        api_environment = {
            "ENV": "prod",
            "UPLOAD_DIR": "/data/uploads",
            "INTERVIEW_RECORDING_ENABLED": str(opt("interviewRecordingEnabled", "true")),
            "BEDROCK_MODEL": str(opt("bedrockModel", "apac.amazon.nova-pro-v1:0")),
            "BEDROCK_REGION": self.region,
            "INTERVIEW_CONSENT_VERSION": str(opt("interviewConsentVersion", "2026-09")),
            "INTERVIEW_ENGINE": str(opt("interviewEngine", "nova")),
            "NOVA_SONIC_REGION": str(opt("novaSonicRegion", "ap-northeast-1")),
            "LLM_ALLOW_REMOTE_STUDENT_DATA": str(opt("allowRemoteStudentData", "true")),
        }
        alb_tls = bool(alb_cert_arn.strip())

        # The CloudFront function carries NO tags live — the aws provider has no
        # tags argument for it, so there was never anything to record. Tagging
        # it in the mirror is a drift row at step 5 and a TagResource write at
        # step 9 for a label nobody reads, so it is excluded from both tags in
        # both phases and stays as it is.
        untagged = ["AWS::CloudFront::Function"]
        self._tag_plan: list[tuple[str, str, dict[str, Any]]] = [("Project", project, {"exclude_resource_types": untagged})]
        # The import mirror keeps Terraform's tag so nothing shows MODIFIED for
        # a label. Harden flips it — with two exceptions, both load-bearing.
        #
        # 1. The EIP, whose tag update the EC2 docs warn may reassociate the
        #    address. It keeps ManagedBy=terraform forever.
        # 2. THE ECS TRIO, while the ECS half is held back. A task definition is
        #    IMMUTABLE: changing its tags registers a NEW REVISION, and the
        #    service then rolls onto it. So flipping this tag at step 9a — the
        #    deploy whose entire purpose is to convert the database to Multi-AZ
        #    *without* touching the API — would have rolled the service in the
        #    same CloudFormation update as the conversion, which is the failure
        #    the two-deploy split exists to prevent. Found by the pre-import
        #    review, 2026-09-07: 9a modified 46 resources including the task
        #    definition, the service and the target group. It now modifies
        #    those three at 9b only, alongside the deregistration-delay change
        #    and the new revision that step already expects.
        #    `test_the_database_half_does_not_touch_the_ecs_trio` pins it.
        ecs_roll_types = ["AWS::ECS::TaskDefinition", "AWS::ECS::Service", "AWS::ElasticLoadBalancingV2::TargetGroup"]
        if harden:
            keep_terraform = ["AWS::EC2::EIP"] + ([] if harden_ecs else ecs_roll_types)
            self._tag_plan.append(("ManagedBy", "cdk", {"exclude_resource_types": keep_terraform + untagged}))
            self._tag_plan.append(("ManagedBy", "terraform", {"include_resource_types": keep_terraform}))
        else:
            self._tag_plan.append(("ManagedBy", "terraform", {"exclude_resource_types": untagged}))
        Aspects.of(self).add(RetainEverything())

        # ---------------------------------------------------------- network --
        # network.tf, resource for resource. /16 carved as cidrsubnet(…, 4, i):
        # public 0/1 are the first two /20s, private 0/1 are the ninth and tenth.
        vpc = ec2.CfnVPC(
            self,
            "Vpc",
            cidr_block="10.42.0.0/16",
            enable_dns_support=True,
            enable_dns_hostnames=True,
            tags=[{"key": "Name", "value": f"{project}-vpc"}],
        )
        igw = ec2.CfnInternetGateway(self, "Igw")
        igw_attach = ec2.CfnVPCGatewayAttachment(self, "IgwAttach", vpc_id=vpc.ref, internet_gateway_id=igw.ref)
        public_subnets = [
            ec2.CfnSubnet(
                self,
                f"PublicSubnet{i}",
                vpc_id=vpc.ref,
                availability_zone=azs[i],
                cidr_block=f"10.42.{16 * i}.0/20",
                map_public_ip_on_launch=True,
                tags=[{"key": "Name", "value": f"{project}-public-{i}"}],
            )
            for i in range(2)
        ]
        private_subnets = [
            ec2.CfnSubnet(
                self,
                f"PrivateSubnet{i}",
                vpc_id=vpc.ref,
                availability_zone=azs[i],
                cidr_block=f"10.42.{128 + 16 * i}.0/20",
                tags=[{"key": "Name", "value": f"{project}-private-{i}"}],
            )
            for i in range(2)
        ]
        # EGRESS FOR THE PRIVATE SUBNETS, AND IT IS TWO FLAGS BECAUSE THE SWAP
        # MUST BE REVERSIBLE IN ONE FLIP.
        #
        # The managed NAT gateway is ~$43.6/month at list price for a workload
        # that pushed 48 GB in a month (docs/cost-review-2026-09.md). A
        # `t4g.nano` doing the same job is ~$4.6/month and, unlike the gateway,
        # has NO per-GB processing charge at all.
        #
        # WHY AN INSTANCE AND NOT PUBLIC SUBNETS. Moving the tasks to public
        # subnets with `assignPublicIp=ENABLED` saves slightly more and looked
        # like the obvious answer until the call sites were counted: SEVEN
        # places pin the private subnets and `DISABLED` — this service, the
        # retention schedule, the identity-ledger schedule, the db-dump
        # schedule, and three `run-task` invocations in deploy.yml and
        # ops-task.yml whose subnet DEFAULTS are hardcoded subnet ids. Three of
        # those seven are SCHEDULED and would have failed at 23:30, 01:00 and
        # 03:00 with nobody watching and no alarm that covers them — the backup
        # alarms watch AWS Backup, not these tasks. Swapping the route target
        # changes ONE thing and leaves all seven working, and it keeps the
        # egress address stable, which a per-task public IP does not.
        #
        # TWO FLAGS, the `hardenEcs` precedent:
        #   natInstance=true   builds the instance and points the private
        #                      default route at it. The GATEWAY STAYS UP. If
        #                      egress misbehaves, flip this back and the route
        #                      returns to a gateway that never went away.
        #   natGateway=false   only once the instance is proven: deletes the
        #                      gateway and its EIP, which is where the money
        #                      actually stops.
        #
        # Deleting the gateway in the same update that first routes away from
        # it would make the rollback a re-create, and a re-created gateway gets
        # a NEW public address — so anything that ever allowlisted the old one
        # breaks on the worst possible day. Hence the middle state.
        #
        # HARDEN-ONLY, like every other target cdk.json sets (dbMultiAz,
        # backupRetentionDays). Since 2026-10-03 cdk.json carries
        # natInstance=true / natGateway=false because that is what is live; the
        # import phase is a mirror of what Terraform handed over, which had the
        # gateway and no instance, and it must keep describing that.
        use_nat_gateway = flag("natGateway", True) if harden else True
        use_nat_instance = flag("natInstance", False) if harden else False
        if not use_nat_gateway and not use_nat_instance:
            raise ValueError(
                "natGateway=false with natInstance=false leaves the private subnets with NO route to "
                "the internet. Every task would fail to START (the ECS agent reads Secrets Manager over "
                "that route and there are no VPC endpoints), Google sign-in's JWKS fetch would fail and "
                "the nightly ledger, dump and retention tasks would stop. Set natInstance=true first, "
                "prove egress, and only then set natGateway=false."
            )

        nat = nat_eip = None
        if use_nat_gateway:
            nat_eip = ec2.CfnEIP(self, "NatEip", domain="vpc")
            nat = ec2.CfnNatGateway(
                self, "Nat", allocation_id=nat_eip.attr_allocation_id, subnet_id=public_subnets[0].ref
            )
            nat.add_dependency(igw_attach)

        nat_instance = None
        if use_nat_instance:
            # Ingress from the VPC only. This box forwards for the private
            # subnets and is not a service anybody dials from outside.
            nat_sg = ec2.CfnSecurityGroup(
                self,
                "NatInstanceSg",
                group_description=f"{project} NAT instance - forwards egress for the private subnets",
                vpc_id=vpc.ref,
                security_group_ingress=[
                    ec2.CfnSecurityGroup.IngressProperty(
                        ip_protocol="-1", cidr_ip="10.42.0.0/16", description="all traffic from inside the VPC"
                    )
                ],
                security_group_egress=[
                    ec2.CfnSecurityGroup.EgressProperty(ip_protocol="-1", cidr_ip="0.0.0.0/0", description="egress to the internet")
                ],
                tags=[{"key": "Name", "value": f"{project}-nat"}],
            )
            # SSM Session Manager and nothing else: this instance sits in the
            # data path, so it has to be patchable without opening SSH to it
            # and without a bastion. No inbound port is ever required for that.
            nat_role = iam.CfnRole(
                self,
                "NatInstanceRole",
                role_name=f"{project}-nat-instance",
                assume_role_policy_document={
                    "Version": "2012-10-17",
                    "Statement": [
                        {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
                    ],
                },
                managed_policy_arns=["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"],
            )
            nat_profile = iam.CfnInstanceProfile(
                self, "NatInstanceProfile", roles=[nat_role.ref], instance_profile_name=f"{project}-nat-instance"
            )
            # THE AMI IS PINNED IN cdk.json (`natAmiId`), AND "LATEST" WAS THE
            # BUG. It used to be resolved by CloudFormation from AWS's public
            # `al2023-ami-kernel-default-arm64` SSM parameter at deploy time,
            # and that parameter moves every time AWS ships an AL2023 image.
            # ImageId is a replacement property, so every reep-core deploy,
            # including one that changed nothing here, rebuilt the NAT box:
            # the private subnets lost egress during the swap (a live
            # interview's upstream stream with them), the user data that failed
            # twice on 2026-10-03 ran again on an image nobody had tried it on,
            # and the old box stayed running and billing because
            # UpdateReplacePolicy is Retain. infra-drift.yml reported it as
            # drift on every morning after AWS published an image (#118).
            # Bump the pin deliberately, at night, after checking the new
            # image boots this user data. ARM64 for the same reason apiArm64
            # exists — a t4g is cheaper than the x86 equivalent for identical
            # work. Without a pin (a fresh account) it still falls back to the
            # parameter, so a first deploy needs no AMI lookup.
            nat_ami_id: str = str(opt("natAmiId", "")).strip()
            if nat_ami_id:
                if not re.fullmatch(r"ami-[0-9a-f]{8,17}", nat_ami_id):
                    raise ValueError(
                        f"natAmiId must be an AMI id like ami-0123456789abcdef0, not {nat_ami_id!r}. "
                        "It is the NAT instance's image, pinned so a deploy never replaces the box by surprise."
                    )
                nat_ami = nat_ami_id
            else:
                nat_ami = ssm.StringParameter.value_for_typed_string_parameter_v2(
                    self,
                    "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
                    ssm.ParameterValueType.AWS_EC2_IMAGE_ID,
                )
            # cloud-init runs user data on FIRST boot only, so the forwarding
            # rules are installed as a systemd unit rather than applied inline —
            # otherwise the first reboot silently turns this back into a plain
            # EC2 instance that routes nothing, and the symptom is the whole
            # deployment losing egress with a green instance in the console.
            # Every rule is applied idempotently (-C before -A) so a restart of
            # the unit cannot stack duplicates.
            nat_user_data = Fn.base64(
                "\n".join(
                    [
                        "#!/bin/bash",
                        "set -euxo pipefail",
                        # AL2023 SHIPS WITHOUT iptables. Found the hard way on
                        # 2026-10-03: the unit below failed on its first
                        # `iptables` with "command not found", the instance
                        # forwarded nothing, and the first task started behind
                        # it could not reach Secrets Manager. Installed here, at
                        # first boot, because a package persists across reboots
                        # and the unit only has to apply the rules.
                        # A t4g.nano has 512 MB and dnf's AL2023 metadata
                        # is ~80 MB: on 2026-10-03 the OOM killer took dnf on
                        # the second attempt too ("Killed"), the script stopped
                        # and the instance again forwarded nothing. Swap first.
                        "fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile",
                        "echo '/swapfile none swap defaults 0 0' >>/etc/fstab",
                        "dnf install -y iptables-nft",
                        "echo 'net.ipv4.ip_forward = 1' >/etc/sysctl.d/90-reep-nat.conf",
                        "cat >/usr/local/sbin/reep-nat.sh <<'NATEOF'",
                        "#!/bin/bash",
                        "set -eu",
                        "sysctl -w net.ipv4.ip_forward=1",
                        "IFACE=$(ip -o -4 route show to default | awk '{print $5}' | head -1)",
                        'iptables -t nat -C POSTROUTING -o "$IFACE" -s 10.42.0.0/16 -j MASQUERADE 2>/dev/null || \\',
                        '  iptables -t nat -A POSTROUTING -o "$IFACE" -s 10.42.0.0/16 -j MASQUERADE',
                        "iptables -C FORWARD -s 10.42.0.0/16 -j ACCEPT 2>/dev/null || \\",
                        "  iptables -A FORWARD -s 10.42.0.0/16 -j ACCEPT",
                        "NATEOF",
                        "chmod +x /usr/local/sbin/reep-nat.sh",
                        "cat >/etc/systemd/system/reep-nat.service <<'UNITEOF'",
                        "[Unit]",
                        "Description=REEP NAT forwarding for the private subnets",
                        "After=network-online.target",
                        "Wants=network-online.target",
                        "[Service]",
                        "Type=oneshot",
                        "RemainAfterExit=yes",
                        "ExecStart=/usr/local/sbin/reep-nat.sh",
                        "[Install]",
                        "WantedBy=multi-user.target",
                        "UNITEOF",
                        "systemctl daemon-reload",
                        "systemctl enable --now reep-nat.service",
                        "",
                    ]
                )
            )
            nat_instance = ec2.CfnInstance(
                self,
                "NatInstance",
                instance_type="t4g.nano",
                image_id=nat_ami,
                subnet_id=public_subnets[0].ref,
                security_group_ids=[nat_sg.ref],
                iam_instance_profile=nat_profile.ref,
                # WITHOUT THIS THE INSTANCE FORWARDS NOTHING. EC2 drops any
                # packet whose source or destination is not the instance itself
                # unless the check is off, and the failure is total and silent:
                # the instance is healthy, the route is correct, and every
                # connection from a private subnet simply times out.
                source_dest_check=False,
                user_data=nat_user_data,
                tags=[{"key": "Name", "value": f"{project}-nat"}],
            )
            nat_instance.add_dependency(igw_attach)
            # RETAIN, EXCEPT WHEN THE CREATE ITSELF ROLLS BACK (#124). The role
            # and the profile carry fixed names. On 2026-10-03 the first
            # attempt failed, plain Retain kept all four of these, and the
            # retry was refused at change-set validation because
            # `reep-nat-instance` "already exists"; they had to be deleted by
            # hand. RetainExceptOnCreate deletes what a failed create made and
            # still forgets, never deletes, a box that was running. None of the
            # four holds data.
            for nat_resource in (nat_sg, nat_role, nat_profile, nat_instance):
                nat_resource.apply_removal_policy(RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE)
        public_rt = ec2.CfnRouteTable(self, "PublicRouteTable", vpc_id=vpc.ref)
        ec2.CfnRoute(
            self, "PublicDefaultRoute", route_table_id=public_rt.ref, destination_cidr_block="0.0.0.0/0", gateway_id=igw.ref
        ).add_dependency(igw_attach)
        private_rt = ec2.CfnRouteTable(self, "PrivateRouteTable", vpc_id=vpc.ref)
        # Changing this target is a REPLACEMENT of the route (CloudFormation
        # deletes and recreates it), so the private subnets lose egress for a
        # few seconds during the swap. Harmless for requests already in flight
        # through the ALB, but it will interrupt a live interview's upstream
        # stream — run the flip outside interview hours.
        ec2.CfnRoute(
            self,
            "PrivateDefaultRoute",
            route_table_id=private_rt.ref,
            destination_cidr_block="0.0.0.0/0",
            instance_id=nat_instance.ref if nat_instance is not None else None,
            nat_gateway_id=None if nat_instance is not None else nat.ref,
        )
        for i, sn in enumerate(public_subnets):
            ec2.CfnSubnetRouteTableAssociation(self, f"PublicRta{i}", subnet_id=sn.ref, route_table_id=public_rt.ref)
        for i, sn in enumerate(private_subnets):
            ec2.CfnSubnetRouteTableAssociation(self, f"PrivateRta{i}", subnet_id=sn.ref, route_table_id=private_rt.ref)

        # The L2 view of the same network, for the constructs that need an IVpc.
        # from_vpc_attributes creates nothing — it is a typed handle.
        ivpc = ec2.Vpc.from_vpc_attributes(
            self,
            "VpcRef",
            vpc_id=vpc.ref,
            availability_zones=azs,
            public_subnet_ids=[s.ref for s in public_subnets],
            private_subnet_ids=[s.ref for s in private_subnets],
        )
        public_sel = ec2.SubnetSelection(subnets=[ec2.Subnet.from_subnet_id(self, f"PubRef{i}", s.ref) for i, s in enumerate(public_subnets)])
        private_sel = ec2.SubnetSelection(subnets=[ec2.Subnet.from_subnet_id(self, f"PrivRef{i}", s.ref) for i, s in enumerate(private_subnets)])

        # --------------------------------------------------------- security --
        # security.tf. Inline ingress/egress on the group, as Terraform wrote
        # them, so the imported group's rule set matches instead of gaining a
        # second copy of each rule as separate CfnSecurityGroupIngress rows.
        def cf_ingress(port: int, description: str) -> dict[str, Any]:
            rule: dict[str, Any] = {"ipProtocol": "tcp", "fromPort": port, "toPort": port, "description": description}
            if restrict_to_cloudfront and cloudfront_prefix_list:
                rule["sourcePrefixListId"] = cloudfront_prefix_list
            else:
                rule["cidrIp"] = "0.0.0.0/0"
            return rule

        all_egress = [{"ipProtocol": "-1", "cidrIp": "0.0.0.0/0"}]
        alb_ingress = [cf_ingress(443, "HTTPS from CloudFront")]
        if not alb_tls:
            alb_ingress.append(cf_ingress(80, "HTTP - the origin itself when no certificate is set"))
        alb_sg = ec2.CfnSecurityGroup(
            self,
            "AlbSg",
            group_name=sg_names["alb"],
            group_description="Managed by Terraform",  # create-only; Terraform's default, and the live value
            vpc_id=vpc.ref,
            security_group_ingress=alb_ingress,
            security_group_egress=all_egress,
        )
        api_sg = ec2.CfnSecurityGroup(
            self,
            "ApiSg",
            group_name=sg_names["api"],
            group_description="Managed by Terraform",  # create-only; Terraform's default, and the live value
            vpc_id=vpc.ref,
            security_group_ingress=[
                {"ipProtocol": "tcp", "fromPort": 3300, "toPort": 3300, "sourceSecurityGroupId": alb_sg.ref, "description": "uvicorn, from the ALB only"}
            ],
            security_group_egress=all_egress,
        )
        db_sg = ec2.CfnSecurityGroup(
            self,
            "DbSg",
            group_name=sg_names["db"],
            group_description="Managed by Terraform",  # create-only; Terraform's default, and the live value
            vpc_id=vpc.ref,
            security_group_ingress=[
                {"ipProtocol": "tcp", "fromPort": 5432, "toPort": 5432, "sourceSecurityGroupId": api_sg.ref, "description": "Postgres, from api tasks only"}
            ],
            security_group_egress=all_egress,
        )
        efs_sg = ec2.CfnSecurityGroup(
            self,
            "EfsSg",
            group_name=sg_names["efs"],
            group_description="Managed by Terraform",  # create-only; Terraform's default, and the live value
            vpc_id=vpc.ref,
            security_group_ingress=[
                {"ipProtocol": "tcp", "fromPort": 2049, "toPort": 2049, "sourceSecurityGroupId": api_sg.ref, "description": "NFS, from api tasks only"}
            ],
            security_group_egress=all_egress,
        )

        # ---------------------------------------------------------- storage --
        data_fs = efs.CfnFileSystem(
            self,
            "DataFs",
            encrypted=True,
            lifecycle_policies=[efs.CfnFileSystem.LifecyclePolicyProperty(transition_to_ia="AFTER_30_DAYS")],
            file_system_tags=[efs.CfnFileSystem.ElasticFileSystemTagProperty(key="Name", value=f"{project}-data")],
        )
        data_fs.apply_removal_policy(RemovalPolicy.RETAIN)
        for i, sn in enumerate(private_subnets):
            efs.CfnMountTarget(self, f"DataMount{i}", file_system_id=data_fs.ref, subnet_id=sn.ref, security_groups=[efs_sg.ref])
        data_ap = efs.CfnAccessPoint(
            self,
            "DataAccessPoint",
            file_system_id=data_fs.ref,
            posix_user=efs.CfnAccessPoint.PosixUserProperty(uid="1000", gid="1000"),
            root_directory=efs.CfnAccessPoint.RootDirectoryProperty(
                path="/reep-data",
                creation_info=efs.CfnAccessPoint.CreationInfoProperty(owner_uid="1000", owner_gid="1000", permissions="750"),
            ),
        )

        web_bucket = s3.Bucket(
            self,
            "WebBucket",
            bucket_name=web_bucket_name,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
        )
        ledger_bucket = None
        if identity_ledger:
            ledger_bucket = s3.Bucket(
                self,
                "IdentityLedgerBucket",
                bucket_name=f"{project}-identity-ledger-{self.account}",
                # VERSIONED, and it is not optional. A day's object is rewritten
                # by a re-run, so the versions ARE the history -- without them
                # the module's "it never deletes anything" promise is only true
                # of the object name.
                versioned=True,
                # OBJECT LOCK CAN ONLY BE SET AT CREATION. It cannot be added to
                # a bucket afterwards, so a ledger bucket created without it has
                # to be replaced -- copying every object, under a new name, by
                # hand. That is the whole reason this is here on day one rather
                # than deferred to "when we need it".
                object_lock_enabled=True,
                object_lock_default_retention=(
                    s3.ObjectLockRetention.compliance(Duration.days(365 * identity_ledger_years))
                    if identity_ledger_compliance
                    else s3.ObjectLockRetention.governance(Duration.days(365 * identity_ledger_years))
                ),
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                removal_policy=RemovalPolicy.RETAIN,
                # NO LIFECYCLE RULE, deliberately. See the knob's comment: the
                # absence of one is the feature.
            )
            CfnOutput(
                self,
                "IdentityLedgerBucketName",
                value=ledger_bucket.bucket_name,
                description="IDENTITY_LEDGER_BUCKET for the api task",
            )

        dump_bucket = None
        dump_archive_bucket = None
        if db_dumps:
            # THE DAILY TIER. Read on the worst night of the year, so it is
            # STANDARD_IA and never Glacier -- the artefact you most need in a
            # crisis must not have a retrieval time measured in hours. The
            # storage class is set by the WRITER on PutObject rather than by a
            # transition rule here, because a transition to IA cannot happen
            # before day 30 and would bill the first month at Standard rates.
            dump_bucket = s3.Bucket(
                self,
                "DbDumpBucket",
                bucket_name=f"{project}-db-dumps-{self.account}",
                versioned=True,
                # Object Lock, because this tier's job in the recovery matrix is
                # the row that reads "vault deleted / account compromised". A
                # copy an attacker can delete does not answer that row.
                object_lock_enabled=True,
                object_lock_default_retention=s3.ObjectLockRetention.governance(
                    Duration.days(db_dump_daily_days)
                ),
                # THE LIFECYCLE RUNS AFTER THE LOCK LAPSES, NEVER WITH IT. See
                # DUMP_LIFECYCLE_LAG_DAYS: equal numbers make every object's
                # deletion a race that silently resolves to "never".
                #
                # `noncurrent_version_expiration` is not optional on a versioned
                # bucket. Expiring a current version writes a delete MARKER and
                # leaves the object itself behind, still stored and still
                # billed, so a rule without it looks like it works in the
                # console and frees nothing at all.
                lifecycle_rules=[
                    s3.LifecycleRule(
                        id=f"expire-{db_dump_daily_days + DUMP_LIFECYCLE_LAG_DAYS}d",
                        enabled=True,
                        expiration=Duration.days(
                            db_dump_daily_days + DUMP_LIFECYCLE_LAG_DAYS
                        ),
                        noncurrent_version_expiration=Duration.days(
                            db_dump_daily_days + DUMP_LIFECYCLE_LAG_DAYS
                        ),
                    )
                ],
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                removal_policy=RemovalPolicy.RETAIN,
            )
            CfnOutput(
                self,
                "DbDumpBucketName",
                value=dump_bucket.bucket_name,
                description="DB_DUMP_BUCKET for the api task",
            )

        if db_dump_archive:
            # THE ARCHIVE TIER: the first successful dump of each month, kept
            # for years, in Deep Archive. NO LIFECYCLE RULE AT ALL -- the same
            # deliberate absence the identity ledger's bucket carries, and for
            # the same reason. This is the copy that outlives the 35-day
            # ceiling, and a rule it acquires by accident is that promise
            # quietly expiring.
            #
            # A SEPARATE BUCKET rather than a prefix, because the retention
            # above is bucket-wide: a ten-year default here and a ninety-day
            # default there cannot both be set on one bucket.
            dump_archive_bucket = s3.Bucket(
                self,
                "DbDumpArchiveBucket",
                bucket_name=f"{project}-db-archive-{self.account}",
                versioned=True,
                object_lock_enabled=True,
                # GOVERNANCE, matching the ledger and `vaultLockCompliance`.
                # Compliance mode cannot be undone by anybody including root,
                # which is the right end state and the wrong thing to switch on
                # in the change that creates the bucket: a retention typed wrong
                # would be permanent too.
                object_lock_default_retention=s3.ObjectLockRetention.governance(
                    Duration.days(365 * db_dump_archive_years)
                ),
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                removal_policy=RemovalPolicy.RETAIN,
            )
            CfnOutput(
                self,
                "DbDumpArchiveBucketName",
                value=dump_archive_bucket.bucket_name,
                description="DB_DUMP_ARCHIVE_BUCKET for the api task",
            )

        document_archive_bucket = None
        if document_archive:
            # THE PERMANENT DOCUMENT ARCHIVE: every uploaded file and every
            # finished interview recording, written once as it is stored and
            # never removed. See the knob's comment above for why nothing
            # already in this stack covers it.
            #
            # NO LIFECYCLE RULE AT ALL -- the ledger's deliberate absence and the
            # monthly dump archive's, for the same reason. This is the copy that
            # outlives the 35-day ceiling, and a rule it acquires by accident is
            # that promise quietly expiring, in the one direction S3 never
            # reports: an expiration aimed at an object whose lock has not
            # lapsed deletes nothing, forever, behind a console showing a rule
            # that looks like it works.
            #
            # VERSIONED, and not optional. `document_store` mints a fresh
            # `uuid4().hex` for every file, so a key is written once and a
            # second PUT to the same key cannot happen through the app -- but
            # versioning is what makes that a property of the BUCKET rather than
            # of the current shape of one module, and it is what the sweep's
            # "writes only, never deletes" promise rests on.
            document_archive_bucket = s3.Bucket(
                self,
                "DocumentArchiveBucket",
                bucket_name=f"{project}-documents-archive-{self.account}",
                versioned=True,
                # OBJECT LOCK CAN ONLY BE SET AT CREATION, the ledger's rule: a
                # bucket created without it has to be REPLACED, copying every
                # object by hand under a new name. That is the whole reason it
                # is here on day one rather than deferred.
                object_lock_enabled=True,
                object_lock_default_retention=(
                    s3.ObjectLockRetention.compliance(Duration.days(365 * document_archive_years))
                    if document_archive_compliance
                    else s3.ObjectLockRetention.governance(Duration.days(365 * document_archive_years))
                ),
                encryption=s3.BucketEncryption.S3_MANAGED,
                enforce_ssl=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                removal_policy=RemovalPolicy.RETAIN,
            )
            CfnOutput(
                self,
                "DocumentArchiveBucketName",
                value=document_archive_bucket.bucket_name,
                description="DOCUMENT_ARCHIVE_BUCKET for the api task",
            )

        alb_logs_bucket = s3.Bucket(
            self,
            "AlbLogsBucket",
            bucket_name=alb_logs_bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(id="expire-90d", enabled=True, expiration=Duration.days(90))],
        )

        # ---------------------------------------------------------- secrets --
        # Imported by ARN, never created, never versioned here. See docstring.
        app_secret = sm.Secret.from_secret_complete_arn(self, "AppSecret", app_secret_arn)
        external_secret = sm.Secret.from_secret_complete_arn(self, "ExternalSecret", external_secret_arn)

        # ---------------------------------------------------------- logging --
        log_group = logs.LogGroup(
            self, "ApiLogs", log_group_name=f"/{project}/api", retention=logs.RetentionDays.ONE_MONTH, removal_policy=RemovalPolicy.RETAIN
        )
        alerts = sns.Topic(self, "Alerts", topic_name=f"{project}-alerts")
        if alert_email and harden:
            # An email subscription is import-hostile (CloudFormation cannot adopt
            # one), so it is created in the harden phase. SNS dedupes on
            # (topic, endpoint), so the existing subscription is reused rather
            # than doubled; a confirmation mail may arrive once more.
            alerts.add_subscription(subs.EmailSubscription(alert_email))

        # -------------------------------------------------------------- ecr --
        api_repo = ecr.CfnRepository(
            self,
            "ApiRepo",
            repository_name=f"{project}/api",
            image_tag_mutability="MUTABLE",
            image_scanning_configuration=ecr.CfnRepository.ImageScanningConfigurationProperty(scan_on_push=True),
            lifecycle_policy=ecr.CfnRepository.LifecyclePolicyProperty(
                lifecycle_policy_text=json.dumps(
                    {
                        "rules": [
                            {
                                "rulePriority": 1,
                                "description": "keep the last 20 images",
                                "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 20},
                                "action": {"type": "expire"},
                            }
                        ]
                    }
                )
            ),
        )
        api_repo.apply_removal_policy(RemovalPolicy.RETAIN)
        api_image_repo = f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/{project}/api"
        # `:latest` is the family `reep-api` runs: the nightly retention job and
        # ops-task.yml's one-offs. Under blue/green the workflow retags it only
        # AFTER a flip has been proven, so it means "promoted", not "last
        # pushed"; the two colour families pin `:blue` / `:green`, and a deploy
        # only ever moves the IDLE colour's tag — which is what makes the live
        # colour a rollback target made of known bytes.
        api_image = f"{api_image_repo}:latest"

        # --------------------------------------------------------- database --
        db_subnets = rds.CfnDBSubnetGroup(
            self,
            "DbSubnets",
            db_subnet_group_name=f"{project}-db",
            db_subnet_group_description="Managed by Terraform",
            subnet_ids=[s.ref for s in private_subnets],
        )
        db = rds.CfnDBInstance(
            self,
            "Db",
            db_instance_identifier=f"{project}-postgres",
            engine="postgres",
            engine_version="17",
            db_instance_class=db_class,
            allocated_storage=allocated_storage,
            max_allocated_storage=100,
            storage_type="gp3",
            storage_encrypted=True,
            db_name="reep_py",
            master_username="reep",
            # NO master_user_password. Ever. See the module docstring and
            # tests/test_core_synth.py::test_no_master_password_in_the_template.
            multi_az=db_multi_az,
            db_subnet_group_name=db_subnets.ref,
            vpc_security_groups=[db_sg.ref],
            publicly_accessible=False,
            backup_retention_period=retention_days,
            preferred_backup_window="20:30-21:30",
            preferred_maintenance_window="sun:21:30-sun:22:30",
            deletion_protection=True,
            copy_tags_to_snapshot=True,
            enable_performance_insights=True,
            enable_cloudwatch_logs_exports=["postgresql"],
            auto_minor_version_upgrade=True,
        )
        # RETAIN, not SNAPSHOT. "Snapshot" means DELETE AFTER SNAPSHOTTING: a
        # delete-stack or cdk destroy would call DeleteDBInstance, held off only
        # by DeletionProtection — one toggled property from a new endpoint and
        # an invalid DATABASE_URL. Retain forgets the instance and touches it not.
        db.apply_removal_policy(RemovalPolicy.RETAIN)
        db_arn = f"arn:aws:rds:{self.region}:{self.account}:db:{project}-postgres"

        # ----------------------------------------------------------- backup --
        lock = None
        if harden:
            lock_kwargs: dict[str, Any] = {"min_retention_days": retention_days}
            if vault_lock_compliance:
                # Compliance mode: after 3 days NOBODY, including root, can
                # shorten retention or delete the vault. Irreversible; opt-in.
                lock_kwargs["changeable_for_days"] = 3
            lock = backup.CfnBackupVault.LockConfigurationTypeProperty(**lock_kwargs)
        vault = backup.CfnBackupVault(self, "Vault", backup_vault_name=f"{project}-vault", lock_configuration=lock)
        vault.apply_removal_policy(RemovalPolicy.RETAIN)
        backup_role = iam.Role(
            self,
            "BackupRole",
            role_name=f"{project}-backup",
            assumed_by=iam.ServicePrincipal("backup.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSBackupServiceRolePolicyForBackup"),
                *(
                    [iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSBackupServiceRolePolicyForRestores")]
                    if harden
                    else []
                ),
            ],
        )
        rule = backup.CfnBackupPlan.BackupRuleResourceTypeProperty(
            rule_name=f"daily-{retention_days}d" if harden else "daily-35d",
            target_backup_vault=vault.attr_backup_vault_name,
            # 00:30 IST. It was 21:30 UTC, which is the exact minute
            # `preferred_maintenance_window` opens on a Sunday (line ~535), and RDS
            # refuses a snapshot taken "inside or too close to" that window — so EVERY
            # Sunday's database backup job failed. It failed silently, because the
            # alarm that would report it (BackupJobsFailedAlarm) ships in the harden
            # phase, which had never been deployed; the EFS half of the same plan kept
            # succeeding, so the vault looked healthy while the DATABASE recovery point
            # quietly went stale. Found 2026-09-07 against the live account, where the
            # 2026-09-06 RDS job was FAILED and the newest RDS point was two days old.
            # Keep this at least an hour clear of BOTH RDS windows — the automated
            # backup window (20:30-21:30) and the maintenance window — or the failure
            # comes back. test_backup_schedule_clears_the_rds_windows is the guard.
            schedule_expression="cron(0 19 * * ? *)",  # 00:30 IST
            lifecycle=backup.CfnBackupPlan.LifecycleResourceTypeProperty(delete_after_days=retention_days if harden else 35),
            copy_actions=(
                [
                    backup.CfnBackupPlan.CopyActionResourceTypeProperty(
                        destination_backup_vault_arn=dr_vault_arn,
                        lifecycle=backup.CfnBackupPlan.LifecycleResourceTypeProperty(delete_after_days=retention_days),
                    )
                ]
                if harden and dr_vault_arn
                else None
            ),
        )
        plan = backup.CfnBackupPlan(
            self,
            "BackupPlan",
            backup_plan=backup.CfnBackupPlan.BackupPlanResourceTypeProperty(backup_plan_name=f"{project}-daily", backup_plan_rule=[rule]),
        )
        backup.CfnBackupSelection(
            self,
            "BackupSelection",
            backup_plan_id=plan.attr_backup_plan_id,
            backup_selection=backup.CfnBackupSelection.BackupSelectionResourceTypeProperty(
                selection_name=f"{project}-db-efs",
                iam_role_arn=backup_role.role_arn,
                resources=[db_arn, data_fs.attr_arn],
            ),
        )
        if archive_retention_days:
            # THE ARCHIVE TIER. A SECOND PLAN, NOT A SECOND RULE, and the
            # difference is the whole reason this is fifteen lines instead of
            # three.
            #
            # A SELECTION IS PLAN-SCOPED, NOT RULE-SCOPED. The daily selection
            # above covers the database AND the EFS file system, so a monthly
            # rule added to that plan would inherit both — and a multi-year
            # lifecycle over EFS means every student's resume, marksheet,
            # certificate, staff signature and (where it is switched on)
            # recorded voice sits in a locked vault for years, outliving
            # INTERVIEW_RETENTION_DAYS, the recordings lifecycle, and the
            # "files go before rows" rule both purge modules are built on.
            # Nothing would report that: the plan would be green.
            #
            # NO `move_to_cold_storage_after_days`, DELIBERATELY. AWS Backup
            # supports cold storage for DynamoDB, EFS, SAP HANA, Timestream and
            # VMware — not RDS — and the documentation is explicit that "if a
            # resource does not support transition to cold storage, AWS Backup
            # ignores this setting". A cold-storage clause here would
            # synthesise, deploy, report success and do nothing, which is the
            # same silent shape as the Sunday backup that failed for weeks.
            #
            # THE WINDOWS ARE EXPLICIT BECAUSE ONE JOB RUNS PER RESOURCE. AWS
            # Backup allows a single concurrent backup job per resource, so a
            # monthly job landing on top of the daily does not run twice — it
            # queues, and is CANCELLED if the queue outlasts its start window.
            # That would be a failed job on the one day a month the multi-year
            # point was meant to be taken. 16:00 UTC (21:30 IST) is three hours
            # ahead of the daily rule and clear of both RDS windows;
            # test_backup_schedule_clears_the_rds_windows checks every rule,
            # this one included.
            archive_rule = backup.CfnBackupPlan.BackupRuleResourceTypeProperty(
                rule_name=f"monthly-{archive_retention_days}d",
                target_backup_vault=vault.attr_backup_vault_name,
                schedule_expression="cron(0 16 1 * ? *)",  # 1st of the month, 21:30 IST
                start_window_minutes=60,
                completion_window_minutes=180,
                lifecycle=backup.CfnBackupPlan.LifecycleResourceTypeProperty(delete_after_days=archive_retention_days),
                copy_actions=(
                    [
                        backup.CfnBackupPlan.CopyActionResourceTypeProperty(
                            destination_backup_vault_arn=dr_vault_arn,
                            # The DR vault's lock sets a MINIMUM retention, and a
                            # copy whose lifecycle is SHORTER than it fails the
                            # copy job. The archive number is longer than the
                            # daily one by construction (validated above), so it
                            # clears that floor.
                            lifecycle=backup.CfnBackupPlan.LifecycleResourceTypeProperty(delete_after_days=archive_retention_days),
                        )
                    ]
                    if dr_vault_arn
                    else None
                ),
            )
            archive_plan = backup.CfnBackupPlan(
                self,
                "ArchiveBackupPlan",
                backup_plan=backup.CfnBackupPlan.BackupPlanResourceTypeProperty(
                    backup_plan_name=f"{project}-archive", backup_plan_rule=[archive_rule]
                ),
            )
            backup.CfnBackupSelection(
                self,
                "ArchiveBackupSelection",
                backup_plan_id=archive_plan.attr_backup_plan_id,
                backup_selection=backup.CfnBackupSelection.BackupSelectionResourceTypeProperty(
                    selection_name=f"{project}-db-archive",
                    iam_role_arn=backup_role.role_arn,
                    # THE DATABASE ALONE. See the plan comment above.
                    resources=[db_arn],
                ),
            )
        if harden:
            # A backup that has never been restored is a hope. Weekly, AWS
            # restores the newest recovery point into a throwaway instance,
            # checks it comes up, and deletes it. Failures raise the alarm below.
            rt_plan = backup.CfnRestoreTestingPlan(
                self,
                "RestoreTestingPlan",
                restore_testing_plan_name=f"{project}_weekly_restore",
                schedule_expression="cron(0 4 ? * SUN *)",  # 09:30 IST Sunday
                start_window_hours=4,
                recovery_point_selection=backup.CfnRestoreTestingPlan.RestoreTestingRecoveryPointSelectionProperty(
                    algorithm="LATEST_WITHIN_WINDOW",
                    include_vaults=[vault.attr_backup_vault_arn],
                    recovery_point_types=["SNAPSHOT", "CONTINUOUS"],
                    selection_window_days=7,
                ),
            )
            backup.CfnRestoreTestingSelection(
                self,
                "RestoreTestingSelectionDb",
                restore_testing_plan_name=rt_plan.ref,  # Ref is the plan NAME for this type
                restore_testing_selection_name=f"{project}_postgres",
                protected_resource_type="RDS",
                iam_role_arn=backup_role.role_arn,
                protected_resource_arns=[db_arn],
                validation_window_hours=1,
            )

        # -------------------------------------------------------------- iam --
        ecs_tasks = iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        task_execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name=f"{project}-task-execution",
            assumed_by=ecs_tasks,
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
            inline_policies={
                "read-app-secrets": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["secretsmanager:GetSecretValue"],
                            resources=[app_secret.secret_arn, external_secret.secret_arn],
                        )
                    ]
                )
            },
        )
        task_statements = [
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:InvokeModelWithBidirectionalStream",
                ],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        ]
        inline: dict[str, iam.PolicyDocument] = {"invoke-nova": iam.PolicyDocument(statements=task_statements)}
        if ledger_bucket is not None:
            # PUT ONLY. No Delete, no PutBucketLifecycle, no PutObjectRetention:
            # the writer must not be able to weaken the thing that is protecting
            # its own output. Object Lock is the control; a process that could
            # shorten it is not a control.
            inline["write-identity-ledger"] = iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        actions=["s3:PutObject"],
                        resources=[ledger_bucket.arn_for_objects("*")],
                    )
                ]
            )

        dump_targets = [b for b in (dump_bucket, dump_archive_bucket) if b is not None]
        if dump_targets:
            # PUT ONLY, and s3:GetObject is deliberately NOT here either.
            # `month_is_archived` asks the archive bucket whether this month is
            # already held, and HeadObject is authorised by s3:GetObject -- so a
            # reader might add it. It must not: this task writes every student
            # record in the deployment into these buckets, and a task that can
            # also READ them is one compromise away from exfiltrating the whole
            # database from the backups rather than from the database. The head
            # call is granted narrowly below, on the archive prefix alone.
            inline["write-db-dumps"] = iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        actions=["s3:PutObject"],
                        resources=[b.arn_for_objects("*") for b in dump_targets],
                    )
                ]
            )
            if dump_archive_bucket is not None:
                # LIST, NOT GET. `backup_database.month_is_archived` needs to
                # know whether this month's object exists; s3:ListBucket answers
                # that with key NAMES and never contents, while the obvious
                # HeadObject would need s3:GetObject -- read access to every
                # archived dump, which is a complete copy of every student
                # record in the deployment. The prefix condition keeps even the
                # listing to the one place the job looks.
                inline["list-db-archive"] = iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["s3:ListBucket"],
                            resources=[dump_archive_bucket.bucket_arn],
                            conditions={"StringLike": {"s3:prefix": ["monthly/*"]}},
                        )
                    ]
                )

        if document_archive_bucket is not None:
            # PUT AND LIST, NEVER GET, and here the rule bites hardest of the
            # three. This bucket accumulates every marksheet, certificate,
            # photograph, CV, staff signature and recorded interview the college
            # holds, in their original bytes -- not a dump that needs restoring,
            # but files a browser opens. A task that can READ it is one
            # compromise away from exfiltrating every document in the
            # deployment, past rule 1 and past every control on the database.
            #
            # `archive_documents` must ask which objects already exist, and the
            # obvious `head_object` is authorised by `s3:GetObject` -- S3 has no
            # separate permission for it -- so the sweep uses `list_objects_v2`,
            # which returns key names and never contents. No Delete and no
            # PutObjectRetention either: the writer must not be able to weaken
            # the lock protecting its own output.
            inline["write-document-archive"] = iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        actions=["s3:PutObject"],
                        resources=[document_archive_bucket.arn_for_objects("*")],
                    ),
                    iam.PolicyStatement(
                        actions=["s3:ListBucket"],
                        resources=[document_archive_bucket.bucket_arn],
                    ),
                ]
            )

        if harden and ses_identity_domain:
            # Activation, reset and confirmation mail (app/mail_transport.py).
            # Scoped to the college's verified identity, and only from the
            # configured sender, so a compromised task cannot spoof the domain.
            send_mail_statements = [
                iam.PolicyStatement(
                    sid="SendFromVerifiedIdentity",
                    actions=["ses:SendEmail", "ses:SendRawEmail"],
                    resources=[f"arn:aws:ses:{self.region}:{self.account}:identity/{ses_identity_domain}"],
                    conditions=(
                        {"StringEquals": {"ses:FromAddress": ses_from_address}} if ses_from_address else None
                    ),
                )
            ]
            if ses_configuration_set:
                # A SEND THAT NAMES A CONFIGURATION SET IS AUTHORISED AGAINST
                # THE SET AS WELL AS THE IDENTITY, AND THE STATEMENT ABOVE DOES
                # NOT COVER IT.
                #
                # INCIDENT, 2026-09-15, 16:24 to 17:45. `SES_CONFIGURATION_SET`
                # reached the task in the same deploy that introduced it, so
                # from that minute every application send named
                # `reep-transactional` and every one was refused: the role held
                # the identity and nothing else. ALL outbound mail was down for
                # eighty minutes -- activation links, password resets, sign-in
                # codes, leave notifications.
                #
                # IT WAS SILENT IN BOTH DIRECTIONS, which is why it lasted.
                # `mailer.deliver_once` catches the driver's exception, writes
                # it to `mail_logs.error` and LOGS NOTHING; the endpoints above
                # answer 200 because a decision must stand whether or not its
                # mail went. A registration was rejected at 17:27 and the
                # applicant -- who is not a user and has no other channel --
                # received nothing. It surfaced because a human said so, not
                # because anything reported it.
                #
                # WHAT LET IT PAST VERIFICATION, and the lesson worth more than
                # the fix: the sends that "proved SES worked" were made with
                # ACCOUNT ROOT credentials, which bypass IAM entirely. They
                # exercised the identity, the DKIM, the domain, the
                # configuration set and the delivery path, and never once
                # exercised the thing that was broken. A mail test that does not
                # run AS THE TASK ROLE proves nothing about whether the product
                # can send. `POST /api/auth/forgot` against the live host does,
                # and is what finally showed it -- no SES datapoint at 17:35
                # before this statement existed, Send and Delivery at 17:51
                # after it.
                #
                # NO `ses:FromAddress` CONDITION HERE, deliberately. That key is
                # evaluated where SES resolves the sender -- the identity -- and
                # a StringEquals on a key that is absent DENIES. The fence is
                # not weakened by leaving it off: a send still has to satisfy
                # the identity statement, which still pins the From address.
                # This statement on its own permits sending as nobody.
                send_mail_statements.append(
                    iam.PolicyStatement(
                        sid="SendUnderConfigurationSet",
                        actions=["ses:SendEmail", "ses:SendRawEmail"],
                        resources=[
                            f"arn:aws:ses:{self.region}:{self.account}:configuration-set/{ses_configuration_set}"
                        ],
                    )
                )
            # WHETHER SES WILL DELIVER TO ONE ADDRESS, AND LIFTING IT WHEN IT
            # WILL NOT (app/mail_transport.suppression_for, 2026-09-17).
            #
            # A hard bounce or a complaint puts an address on the ACCOUNT
            # SUPPRESSION LIST. SES then ACCEPTS every later send for it --
            # message id returned, `mail_logs` written SENT, every screen saying
            # "we have emailed you a code" -- and delivers nothing, for ever. A
            # student in that state can sign in with Google and reach a password
            # by no path at all, and until this grant existed the product had no
            # way to ask the question, so it read to the office as an unexplained
            # "the code never came".
            #
            # THE SAME SHAPE AS THE INCIDENT ABOVE, and the reason this is worth
            # writing out: a mail failure whose only symptom is that nothing
            # happens will not be found by watching for errors. There are none.
            #
            # `ses:ListSuppressedDestinations` IS DELIBERATELY ABSENT. The
            # application only ever asks about an address it already holds --
            # the recipient of a message it is about to send, or one the office
            # typed into the mail screen. List would let a compromised task
            # enumerate every address the deployment has ever bounced, which is
            # a roster of people and nothing this feature needs.
            #
            # Resource `*` because both are account-level operations: SESv2
            # supports no resource-level permission for either, so a narrower
            # ARN here would not restrict the call, it would refuse it.
            send_mail_statements.append(
                iam.PolicyStatement(
                    sid="ReadAndLiftOneSuppression",
                    actions=[
                        "ses:GetSuppressedDestination",
                        "ses:DeleteSuppressedDestination",
                    ],
                    resources=["*"],
                )
            )
            inline["send-mail"] = iam.PolicyDocument(statements=send_mail_statements)
        api_task_role = iam.Role(self, "ApiTaskRole", role_name=f"{project}-api-task", assumed_by=ecs_tasks, inline_policies=inline)

        # -------------------------------------------------------------- alb --
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=ivpc,
            internet_facing=True,
            load_balancer_name=f"{project}-alb",
            # mutable=False: the L2 must not append ingress rules to an imported
            # group. Without it, add_listener wrote a 0.0.0.0/0:80 rule the WAF is
            # there to prevent, and attach_to_application_target_group wrote a
            # duplicate of the inline :3300 rule — neither exists in reality.
            security_group=ec2.SecurityGroup.from_security_group_id(self, "AlbSgRef", alb_sg.ref, mutable=False),
            vpc_subnets=public_sel,
            idle_timeout=Duration.seconds(300),  # the interview WebSocket rides this listener
            drop_invalid_header_fields=True,
        )
        alb.log_access_logs(alb_logs_bucket)
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "ApiTargetGroup",
            vpc=ivpc,
            target_group_name=f"{project}-api",
            port=3300,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/ready",
                interval=Duration.seconds(15),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                healthy_http_codes="200",
            ),
            deregistration_delay=Duration.seconds(DEREGISTRATION_DELAY_SECONDS if harden_ecs else 30),
        )
        tg_green: elbv2.ApplicationTargetGroup | None = None
        if blue_green:
            # The GREEN colour's target group. Blue keeps `reep-api`, name for
            # name: a target group's Name is create-only, and the live service's
            # LoadBalancers binding must not move, so the asymmetry in names
            # (`reep-api` / `reep-api-green`) is the price of never replacing
            # the live one. Same port, same probe, and the SAME drain constant —
            # a flip moves only new connections, so the sockets a colour holds
            # when it goes idle are protected by this delay exactly as before.
            # test_every_target_group_drains_for_the_whole_interview asserts it
            # on EVERY target group, not just one.
            tg_green = elbv2.ApplicationTargetGroup(
                self,
                "ApiTargetGroupGreen",
                vpc=ivpc,
                target_group_name=f"{project}-api-green",
                port=3300,
                protocol=elbv2.ApplicationProtocol.HTTP,
                target_type=elbv2.TargetType.IP,
                health_check=elbv2.HealthCheck(
                    path="/ready",
                    interval=Duration.seconds(15),
                    timeout=Duration.seconds(5),
                    healthy_threshold_count=2,
                    unhealthy_threshold_count=3,
                    healthy_http_codes="200",
                ),
                deregistration_delay=Duration.seconds(DEREGISTRATION_DELAY_SECONDS),
            )
        listeners: list[elbv2.ApplicationListener] = []
        if alb_tls:
            listeners.append(
                alb.add_listener(
                    "HttpRedirect",
                    open=False,
                    port=80,
                    protocol=elbv2.ApplicationProtocol.HTTP,
                    # host/path/query are ELB's own defaults and the redirect
                    # behaves identically without them — but the LIVE listener
                    # reports them, so a mirror that omits them is a MODIFIED
                    # drift row at step 5 on a listener, which is the resource
                    # an operator is most likely to misread as a real error.
                    # Spelled out so the drift table stays short.
                    default_action=elbv2.ListenerAction.redirect(
                        host="#{host}", path="/#{path}", query="#{query}", port="443", protocol="HTTPS", permanent=True
                    ),
                )
            )
            listeners.append(
                alb.add_listener(
                    "Https",
                    open=False,
                    port=443,
                    protocol=elbv2.ApplicationProtocol.HTTPS,
                    certificates=[elbv2.ListenerCertificate.from_arn(alb_cert_arn)],
                    # alb.tf: ELBSecurityPolicy-TLS13-1-2-2021-06. That string is
                    # RECOMMENDED_TLS in the library; the first draft wrote
                    # `TLS13_12`, a member that does not exist, and no guard had
                    # synthesised this branch (none set a certificate), so the
                    # crash waited for the live context. test_core_synth.py now
                    # reads the policy out of alb.tf and synthesises this branch.
                    ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
                    default_target_groups=[target_group],
                )
            )
        else:
            listeners.append(
                alb.add_listener("HttpOrigin", open=False, port=80, protocol=elbv2.ApplicationProtocol.HTTP, default_target_groups=[target_group])
            )
        # The listener that carries traffic: Https when a certificate is set
        # (the live shape), the plain-HTTP origin otherwise.
        traffic_listener = listeners[-1]

        colour_rule: elbv2.ApplicationListenerRule | None = None
        if blue_green:
            assert tg_green is not None
            # THE WEIGHTS LIVE ON A RULE, NOT ON THE LISTENER'S DEFAULT ACTION.
            # Two reasons. The deploy role then needs `ModifyRule` on this one
            # rule's ARN rather than `ModifyListener` on the listener — and
            # ModifyListener has no condition key that limits it to weights, so
            # it would also let the role change the certificate and the TLS
            # policy. And CloudFormation only re-sends a property when the
            # resource's template changed: on the listener, a certificate
            # rotation would have snapped traffic back to `liveColour`; on a
            # rule nothing else ever changes, so the reset hazard shrinks to
            # edits of this rule alone. The listener's default action stays the
            # plain forward to `reep-api` it has always been, byte for byte —
            # it is shadowed by this rule and never fires.
            #
            # No stickiness: with it, users flipped onto a bad colour would be
            # pinned there through the rollback. Weights are per NEW connection,
            # so a flip terminates nothing — an interview socket opened on one
            # colour finishes on that colour.
            colour_rule = elbv2.ApplicationListenerRule(
                self,
                "ColourRule",
                listener=traffic_listener,
                priority=10,
                conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
                action=elbv2.ListenerAction.weighted_forward(
                    [
                        elbv2.WeightedTargetGroup(target_group=target_group, weight=100 if live_colour == "blue" else 0),
                        elbv2.WeightedTargetGroup(target_group=tg_green, weight=100 if live_colour == "green" else 0),
                    ]
                ),
            )
            # Probe rules, EVALUATED BEFORE the colour rule (lower number wins):
            # `X-Reep-Colour: green` reaches green whatever the weights say, so
            # the workflow can prove a candidate through CloudFront (the /api/*
            # behaviour forwards all viewer headers) before a single weight
            # moves. Static, CloudFormation-owned, never edited by the workflow.
            # Anyone can send the header and reach the idle colour: same
            # authentication, same database, same rule-1 and rule-2 gates, so
            # the exposure is "a student may briefly use an unpromoted build".
            for prio, colour, tg in ((1, "blue", target_group), (2, "green", tg_green)):
                elbv2.ApplicationListenerRule(
                    self,
                    f"ProbeRule{colour.capitalize()}",
                    listener=traffic_listener,
                    priority=prio,
                    conditions=[elbv2.ListenerCondition.http_header("X-Reep-Colour", [colour])],
                    action=elbv2.ListenerAction.forward([tg]),
                )

        # -------------------------------------------------------------- ecs --
        cluster = ecs.Cluster(self, "Cluster", cluster_name=project, vpc=ivpc, container_insights=True)
        if harden_ecs and ses_from_address:
            api_environment["SES_FROM_ADDRESS"] = ses_from_address
            # Both of these are NESTED under a sender on purpose, not merely
            # ordered after it. SES_CONFIGURATION_SET without a sender is inert
            # noise; LEAVE_MAIL_ENABLED without one is the mail_logs lie the
            # constructor already refuses. Nesting makes the invariant a shape
            # rather than a second rule somebody has to remember.
            if ses_configuration_set:
                # Named on every send so the bounce/complaint stream cannot be
                # lost by an edit to the identity's default. app/config.py's
                # `ses_configuration_set` carries the full reasoning.
                api_environment["SES_CONFIGURATION_SET"] = ses_configuration_set
            if leave_mail_enabled:
                api_environment["LEAVE_MAIL_ENABLED"] = "true"
        if harden_ecs and ledger_bucket is not None:
            # `harden_ecs`, NOT `harden`, and deliberately the same split
            # `SES_FROM_ADDRESS` uses one line up -- which looks like two gates
            # for one feature and is not. A TASK DEFINITION IS IMMUTABLE: adding
            # an environment variable registers a new revision and the service
            # rolls onto it. Step 9a of the cutover (`hardenEcs=false`) exists so
            # that an ECS circuit-breaker rollback cannot undo a Multi-AZ
            # conversion in the same update, so the task definition has to come
            # out of that phase byte-identical to the import mirror.
            # test_the_database_half_does_not_touch_the_ecs_trio pins it, and it
            # caught this line when it was written on `harden`.
            #
            # The IAM grant above stays on `harden` because it is INERT without
            # this variable: a task that holds s3:PutObject and does not know the
            # bucket name writes nothing. Granting early is free; setting the
            # environment early costs an API roll at the worst possible moment.
            # The asymmetry is the point, not an oversight.
            api_environment["IDENTITY_LEDGER_BUCKET"] = ledger_bucket.bucket_name
            api_environment["IDENTITY_LEDGER_REGION"] = self.region

        if harden_ecs and dump_bucket is not None:
            # `harden_ecs` and not `harden`, the ledger's rule and the same
            # reason: a task definition is IMMUTABLE, so setting an environment
            # variable registers a new revision the service rolls onto, and step
            # 9a must leave this definition byte-identical to the import mirror.
            # The IAM grants above sit on `harden` because they are inert
            # without these names -- s3:PutObject on a bucket a task cannot name
            # writes nothing.
            api_environment["DB_DUMP_BUCKET"] = dump_bucket.bucket_name
            api_environment["DB_DUMP_REGION"] = self.region
            if dump_archive_bucket is not None:
                api_environment["DB_DUMP_ARCHIVE_BUCKET"] = dump_archive_bucket.bucket_name
        if harden_ecs and document_archive_bucket is not None:
            # `harden_ecs` and not `harden`, the ledger's rule and the same
            # two-gates-for-one-feature shape: the GRANT can go early because it
            # is inert without the name (a task holding s3:PutObject that cannot
            # name a bucket writes nothing), while the VARIABLE registers a new
            # task-definition revision the service rolls onto, which step 9a
            # exists to keep out of the Multi-AZ conversion.
            api_environment["DOCUMENT_ARCHIVE_BUCKET"] = document_archive_bucket.bucket_name
        if harden_ecs:
            # INTERVIEW_AUDIO_DIR, SET EXPLICITLY AND NOT LEFT TO THE FALLBACK.
            # `interview_audio._store_root()` falls back to
            # `settings.uploads_path.parent / "interview-audio"`, which resolves
            # to /data/interview-audio here only because UPLOAD_DIR happens to
            # be /data/uploads and /data happens to be the EFS mount.
            # app/config.py records what that coincidence cost the last time it
            # broke: the fallback landed in the container's WRITABLE LAYER, so
            # consented recordings were destroyed on every redeploy, silently.
            # Naming it makes the mount an explicit statement rather than an
            # accident of another variable, and docker-compose.prod.yml already
            # sets it for exactly this reason.
            #
            # GATED ON `harden_ecs` LIKE EVERY OTHER VARIABLE HERE, and not
            # because it needs a grant -- it needs nothing. A task definition is
            # IMMUTABLE: any added variable registers a new revision. The import
            # mirror must stay byte-identical to what Terraform left behind, and
            # step 9a (`hardenEcs=false`) must not roll the service while the
            # database is converting to Multi-AZ. A variable whose value is a
            # constant is still a new revision.
            api_environment["INTERVIEW_AUDIO_DIR"] = "/data/interview-audio"
            # THE CONNECTION POOL, SIZED AGAINST THE DATABASE AND NOT LEFT TO
            # THE DEFAULT (2026-09-29). app/config.py's default is 20 + 20
            # overflow, so each task may hold 40 connections and autoscaling may
            # run apiMaxTasks of them: at ten tasks that is 400, several times
            # what the instance allows, and the refusal would arrive on the
            # busiest day. 10 + 10 per task keeps ten tasks at 200 while the
            # observed peak over thirty days was fifteen connections in all.
            # Same `harden_ecs` gate as every variable above.
            api_environment["DB_POOL_SIZE"] = str(API_DB_POOL_SIZE)
            api_environment["DB_MAX_OVERFLOW"] = str(API_DB_MAX_OVERFLOW)
            # The two interview caps (2026-10-29): what one task carries before
            # it refuses, sized to the half-vCPU it has, and the fleet's ceiling.
            # INTERVIEW_METRICS_FAMILY is added per family in _api_task_def.
            api_environment["INTERVIEW_MAX_SESSIONS"] = str(INTERVIEWS_PER_TASK_MAX)
            if interview_max_concurrent:
                api_environment["INTERVIEW_MAX_CONCURRENT"] = str(interview_max_concurrent)
            if job_scout_companies:
                api_environment["JOB_SCOUT_COMPANY_BOARDS"] = "true"

        def _api_task_def(cid: str, family: str, image_tag: str) -> tuple[ecs.FargateTaskDefinition, ecs.ContainerDefinition]:
            """One api task definition. ONE helper for the three families
            (`reep-api`, and under blue/green `reep-api-blue` / `reep-api-green`)
            so that a colour cannot drift from the image everything else runs:
            same roles, same EFS volume, same secrets, same environment, same
            log group, same stopTimeout. Only the family and the tag differ."""
            td = ecs.FargateTaskDefinition(
                self,
                cid,
                family=family,
                cpu=api_cpu,
                memory_limit_mib=api_memory,
                # None when the flag is off, so no RuntimePlatform property is
                # rendered and the import mirror is unchanged. See api_arm64.
                runtime_platform=(
                    ecs.RuntimePlatform(
                        cpu_architecture=ecs.CpuArchitecture.ARM64,
                        operating_system_family=ecs.OperatingSystemFamily.LINUX,
                    )
                    if api_arm64
                    else None
                ),
                # without_policy_updates: the secrets and log-driver helpers would
                # otherwise attach a generated AWS::IAM::Policy that exists nowhere
                # and cannot be imported. The inline read-app-secrets policy and the
                # managed execution policy already cover both.
                execution_role=task_execution_role.without_policy_updates(),
                task_role=api_task_role,
                volumes=[
                    ecs.Volume(
                        name="data",
                        efs_volume_configuration=ecs.EfsVolumeConfiguration(
                            file_system_id=data_fs.ref,
                            transit_encryption="ENABLED",
                            authorization_config=ecs.AuthorizationConfig(access_point_id=data_ap.ref, iam="DISABLED"),
                        ),
                    )
                ],
            )
            secrets = {
                "AUTH_SECRET": ecs.Secret.from_secrets_manager(app_secret, "AUTH_SECRET"),
                "DATABASE_URL": ecs.Secret.from_secrets_manager(app_secret, "DATABASE_URL"),
                "GOOGLE_CLIENT_ID": ecs.Secret.from_secrets_manager(external_secret, "GOOGLE_CLIENT_ID"),
                "GOOGLE_CLIENT_SECRET": ecs.Secret.from_secrets_manager(external_secret, "GOOGLE_CLIENT_SECRET"),
                "SENTRY_DSN": ecs.Secret.from_secrets_manager(external_secret, "SENTRY_DSN"),
                "VOICE_WORKER_SECRET": ecs.Secret.from_secrets_manager(external_secret, "VOICE_WORKER_SECRET"),
            }
            # The retention schedule runs THIS task definition with only a
            # command override, so the jobs project's DSN (app/retention_job.py
            # reads SENTRY_JOBS_DSN and never SENTRY_DSN) has to arrive the same
            # way the api's does. Gated on context and OFF by default, for two
            # reasons that pull the same way: an ECS task that references a
            # secret KEY the JSON does not hold fails to START — every task,
            # the api included — and the import mirror must stay byte-identical
            # until an operator has added the key. Set `sentryJobsDsn: true`
            # in cdk.json AFTER `SENTRY_JOBS_DSN` exists in the reep/external
            # secret, never before. The synth test pins both shapes.
            if flag("sentryJobsDsn", False):
                secrets["SENTRY_JOBS_DSN"] = ecs.Secret.from_secrets_manager(external_secret, "SENTRY_JOBS_DSN")
            # The job agent's keys, on the same terms as the DSN above: see
            # `job_scout_adzuna`. The execution role already reads the whole
            # reep/external secret, so no grant changes.
            if job_scout_adzuna:
                secrets["ADZUNA_APP_ID"] = ecs.Secret.from_secrets_manager(external_secret, "ADZUNA_APP_ID")
                secrets["ADZUNA_APP_KEY"] = ecs.Secret.from_secrets_manager(external_secret, "ADZUNA_APP_KEY")
            if job_scout_jooble:
                secrets["JOOBLE_API_KEY"] = ecs.Secret.from_secrets_manager(external_secret, "JOOBLE_API_KEY")
            c = td.add_container(
                "api",
                image=ecs.ContainerImage.from_registry(f"{api_image_repo}:{image_tag}"),
                essential=True,
                port_mappings=[ecs.PortMapping(container_port=3300, protocol=ecs.Protocol.TCP)],
                # WEB_ORIGIN is added below, once the distribution exists. The
                # metric family is this one's own, so a blue and a green service
                # each scale on their own tasks; harden_ecs for the reason every
                # variable above is.
                environment={
                    **api_environment,
                    **({"INTERVIEW_METRICS_FAMILY": family} if harden_ecs else {}),
                },
                secrets=secrets,
                logging=ecs.LogDrivers.aws_logs(log_group=log_group, stream_prefix="api"),
                stop_timeout=Duration.seconds(STOP_TIMEOUT_SECONDS) if harden_ecs else None,
            )
            c.add_mount_points(ecs.MountPoint(source_volume="data", container_path="/data", read_only=False))
            return td, c

        # `reep-api` on `:latest` is UNCHANGED under blue/green: it stays the
        # target of the retention schedule and of ops-task.yml, and neither
        # colour service runs it. The colour families are additions.
        task_def, container = _api_task_def("ApiTaskDef", f"{project}-api", "latest")
        containers = [container]
        task_def_blue = task_def_green = None
        if blue_green:
            task_def_blue, c_blue = _api_task_def("ApiTaskDefBlue", f"{project}-api-blue", "blue")
            task_def_green, c_green = _api_task_def("ApiTaskDefGreen", f"{project}-api-green", "green")
            containers += [c_blue, c_green]

        # Created ONCE and shared: a second from_security_group_id under the
        # same construct id would collide, and both colours sit in one group.
        api_sg_ref = ec2.SecurityGroup.from_security_group_id(self, "ApiSgRef", api_sg.ref, mutable=False)

        # Per-colour deployment alarms (graft from the design review): a
        # candidate that passes /ready but answers 5xx during its colour's roll
        # is rolled back BY ECS, before any flip. The alarm objects are created
        # in the observability section below; the names are deterministic, and
        # the services take a dependency on the alarms once they exist so the
        # service update cannot name an alarm that is not there yet.
        def _deploy_alarm_name(colour: str) -> str:
            return f"{project}-api-5xx-{colour}"

        def _api_service(cid: str, name: str, td: ecs.FargateTaskDefinition, tg: elbv2.ApplicationTargetGroup, colour: str | None) -> ecs.FargateService:
            """One api service. Factored so the two colours cannot drift: same
            network, same grace period, same circuit breaker, same 100/200.
            Both colours keep 100/200 rather than a cheaper minimum on the idle
            one — the roles swap every deploy, and the idle colour IS the
            rollback target, so it must never drop to zero."""
            svc = ecs.FargateService(
                self,
                cid,
                service_name=name,
                cluster=cluster,
                task_definition=td,
                # No desired_count: Terraform ignores it after creation because
                # autoscaling owns it. Sending DesiredCount=2 on the harden update
                # would scale a busy day back to 2. cdk.json's
                # removeDefaultDesiredCount flag keeps the property out.
                security_groups=[api_sg_ref],
                vpc_subnets=private_sel,
                assign_public_ip=False,
                health_check_grace_period=Duration.seconds(60),
                circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
                # 100/200: the old task is not stopped until the new one is healthy.
                #
                # NOT phase-conditional, and that is the fix for a real mirror
                # error (2026-09-07, found by the pre-import review). This read
                # `100 if harden_ecs else 50`, on the assumption that 50 was the
                # live value and 100 the improvement. **The live service is already
                # at 100** — `ecs.tf` never sets the property, so 100 is the ECS
                # default and the refresh recorded it. Rendering 50 in the import
                # phase would have produced a MODIFIED drift row the runbook does
                # not list, at the one checkpoint whose whole job is to be empty.
                # Worse: step 9a (`-c hardenEcs=false`) still flips this service's
                # ManagedBy tag, so CloudFormation updates the service and sends
                # the template's DeploymentConfiguration with it — lowering the
                # live minimum from 100 to 50 for the length of the Multi-AZ
                # conversion, which is exactly when losing a task hurts most.
                # Live and target are the same number, so the property now falls
                # out of every diff. `test_the_service_keeps_both_tasks_in_every_phase`
                # pins it in BOTH phases.
                min_healthy_percent=100,
                max_healthy_percent=200,
                # NO deployment_controller. Switching an existing service to
                # CODE_DEPLOY is a REPLACEMENT of AWS::ECS::Service, which is
                # the one thing this design exists to avoid. The default (ECS)
                # is rendered explicitly by the library and pinned by
                # test_blue_green_never_replaces_the_existing_service.
                deployment_alarms=(
                    ecs.DeploymentAlarmConfig(alarm_names=[_deploy_alarm_name(colour)], behavior=ecs.AlarmBehavior.ROLLBACK_ON_ALARM)
                    if colour
                    else None
                ),
            )
            svc.attach_to_application_target_group(tg)
            for lst in listeners:
                svc.node.add_dependency(lst)
            scaling = svc.auto_scale_task_count(min_capacity=api_min, max_capacity=api_max)
            scaling.scale_on_cpu_utilization(
                "CpuTarget", target_utilization_percent=60, scale_in_cooldown=Duration.seconds(300), scale_out_cooldown=Duration.seconds(60)
            )
            scaling.scale_on_memory_utilization(
                "MemoryTarget", target_utilization_percent=75, scale_in_cooldown=Duration.seconds(300), scale_out_cooldown=Duration.seconds(60)
            )
            if harden:
                # Live interviews per task (see INTERVIEWS_PER_TASK_TARGET). A
                # new resource, so never in the import mirror; on `harden` and
                # not `harden_ecs` like a grant, because it is inert until the
                # task definition starts publishing the metric. Scale-in waits
                # ten minutes rather than five: a batch of interviews arrives in
                # waves, and a task removed between two waves is a cold start
                # for the next one. A task chosen for scale-in drains through
                # the target group's DEREGISTRATION_DELAY_SECONDS, which already
                # outlasts the longest interview, so nobody is cut off.
                scaling.scale_to_track_custom_metric(
                    "InterviewTarget",
                    metric=cw.Metric(
                        namespace=INTERVIEW_METRIC_NAMESPACE,
                        metric_name=INTERVIEW_METRIC_NAME,
                        dimensions_map={INTERVIEW_METRIC_DIMENSION: td.family},
                        statistic="Average",
                        period=Duration.minutes(1),
                    ),
                    target_value=INTERVIEWS_PER_TASK_TARGET,
                    policy_name="interview-target",
                    scale_in_cooldown=Duration.seconds(600),
                    scale_out_cooldown=Duration.seconds(60),
                )

            # Terraform named the policies; PolicyName is immutable, so the import
            # must present the same names. scale_on_* returns nothing in this
            # binding and parents the policy under the service's scaling target,
            # so the L1s are found by walking THIS SERVICE's subtree and matched
            # on the metric each one tracks. Walking the whole stack — which is
            # what this did with one service — returns the first match, so with
            # two services it would rename blue's policy twice and green's never,
            # and a PolicyName change is a REPLACE of the live scaling policy.
            # Policy names are scoped per scalable target, so both services may
            # call theirs `cpu-target`.
            def _cfn_policy(predefined_metric: str) -> appscaling.CfnScalingPolicy:
                for child in svc.node.find_all():
                    if isinstance(child, appscaling.CfnScalingPolicy):
                        cfg = child.target_tracking_scaling_policy_configuration
                        spec = getattr(cfg, "predefined_metric_specification", None) if cfg is not None else None
                        if spec is not None and spec.predefined_metric_type == predefined_metric:
                            return child
                raise RuntimeError(f"no CfnScalingPolicy tracking {predefined_metric} under {cid}")

            _cfn_policy("ECSServiceAverageCPUUtilization").add_property_override("PolicyName", "cpu-target")
            _cfn_policy("ECSServiceAverageMemoryUtilization").add_property_override("PolicyName", "memory-target")
            return svc

        # The existing service keeps its logical id, its name and its target
        # group. Under blue/green it becomes the BLUE colour: the only property
        # that changes is the task definition (`reep-api-blue`, the same image
        # bytes retagged), which is one ordinary in-place roll at the cutover.
        service = _api_service("ApiService", "api", task_def_blue if blue_green else task_def, target_group, "blue" if blue_green else None)
        service_green: ecs.FargateService | None = None
        if blue_green:
            assert task_def_green is not None and tg_green is not None
            service_green = _api_service("ApiServiceGreen", "api-green", task_def_green, tg_green, "green")

        # The nightly retention job: the api image, one-off, `app.retention_job`.
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            role_name=f"{project}-scheduler",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            inline_policies={
                "run-retention-task": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["ecs:RunTask"],
                            resources=[f"arn:aws:ecs:{self.region}:{self.account}:task-definition/{project}-api:*"],
                        ),
                        iam.PolicyStatement(
                            actions=["iam:PassRole"], resources=[task_execution_role.role_arn, api_task_role.role_arn]
                        ),
                    ]
                )
            },
        )
        scheduler.CfnSchedule(
            self,
            "RetentionSchedule",
            name=f"{project}-retention-daily",
            schedule_expression="cron(30 21 * * ? *)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=cluster.cluster_arn,
                role_arn=scheduler_role.role_arn,
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=task_def.task_definition_arn,
                    launch_type="FARGATE",
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=[s.ref for s in private_subnets], security_groups=[api_sg.ref], assign_public_ip="DISABLED"
                        )
                    ),
                ),
                input=json.dumps(
                    {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.retention_job"]}]},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )

        if harden_ecs and ledger_bucket is not None:
            # GATED ON `harden_ecs`, THE SAME CONDITION AS THE BUCKET VARIABLE,
            # and not merely on the bucket existing. The bucket is created in the
            # harden phase while the variable arrives with the ECS half, so a
            # schedule gated on the bucket alone is live between step 9a and 9b
            # with a task that does not know where to write -- and an unconfigured
            # `app.export_identity` used to print the ledger to stdout, which on
            # Fargate is the `/reep/api` log group. Nightly, every password hash
            # and Google subject, into a place read by anyone holding
            # `logs:FilterLogEvents`. Found by Seer in review on PR #45.
            #
            # `run()` now refuses an unconfigured export outright, so this gate is
            # the second of two locks rather than the only one. Both are kept:
            # this one stops the job existing before it can work, that one stops
            # it leaking however it is invoked.
            #
            # THE IDENTITY LEDGER, DAILY AT 23:30 IST -- deliberately BEFORE the
            # retention sweep at 03:00, not after. The sweep is the one scheduled
            # destructor in the product, and a ledger written after it is a
            # ledger that never saw whatever it removed. Ordering two schedules
            # by their cron is weaker than a dependency and it is what is
            # available; the gap is five and a half hours, which is far more than
            # either job takes.
            #
            # 18:00 UTC also clears both RDS windows and the 19:00 backup job,
            # for test_backup_schedule_clears_the_rds_windows's reason: a job
            # placed inside one of those windows fails every time it collides and
            # nothing reports it.
            #
            # It reuses the api task definition and therefore the scheduler role's
            # existing ecs:RunTask on `{project}-api:*`. A second family would
            # need a second grant, which is a thing to forget.
            scheduler.CfnSchedule(
                self,
                "IdentityLedgerSchedule",
                name=f"{project}-identity-ledger-daily",
                schedule_expression="cron(0 18 * * ? *)",  # 23:30 IST
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=cluster.cluster_arn,
                    role_arn=scheduler_role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                                subnets=[s.ref for s in private_subnets],
                                security_groups=[api_sg.ref],
                                assign_public_ip="DISABLED",
                            )
                        ),
                    ),
                    input=json.dumps(
                        {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.export_identity"]}]},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

        if harden_ecs and dump_bucket is not None:
            # THE LOGICAL BACKUP, DAILY AT 01:00 IST. Gated on `harden_ecs` for
            # the ledger's reason -- a schedule that exists before the bucket
            # variable does is a job that fires nightly and fails nightly, and
            # here it would fail after putting the production database through a
            # full dump first. `run()` refuses before dumping for that reason;
            # this gate stops the job existing at all until it can work.
            #
            # 19:30 UTC is chosen against three other clocks and clears all of
            # them:
            #   * AFTER the identity ledger (18:00 UTC). Independent jobs, but
            #     the ledger is the small fast one and the file that matters
            #     most; it should not queue behind a multi-minute dump.
            #   * BEFORE the retention sweep (21:30 UTC). The sweep is the one
            #     scheduled destructor in the product, and a dump taken after it
            #     is a dump that never saw what it removed -- the same ordering
            #     argument the ledger's schedule makes, and the reason neither
            #     runs in the small hours.
            #   * BEFORE the RDS backup window (20:30-21:30 UTC). pg_dump is a
            #     long read over every table; landing it inside the window puts
            #     that load on the instance while the snapshot is being taken.
            #     An hour of clearance, which is far more than this dump needs
            #     at this data volume and leaves room for it to grow.
            #
            # It reuses the api task definition and so the scheduler role's
            # existing ecs:RunTask on `{project}-api:*`. A second family would
            # need a second grant, which is a thing to forget.
            scheduler.CfnSchedule(
                self,
                "DbDumpSchedule",
                name=f"{project}-db-dump-daily",
                schedule_expression="cron(30 19 * * ? *)",  # 01:00 IST
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=cluster.cluster_arn,
                    role_arn=scheduler_role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                                subnets=[s.ref for s in private_subnets],
                                security_groups=[api_sg.ref],
                                assign_public_ip="DISABLED",
                            )
                        ),
                    ),
                    input=json.dumps(
                        {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.backup_database"]}]},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

        if harden_ecs and document_archive_bucket is not None:
            # THE DOCUMENT ARCHIVE SWEEP, DAILY AT 02:00 IST.
            #
            # WHY A SWEEP EXISTS AT ALL, when `document_store.save_bytes`
            # already PUTs every file as it is stored: that inline write is
            # BEST-EFFORT by contract and raises nothing, because an S3 blip
            # must never be the reason a student is told their valid certificate
            # was rejected. This job is what makes the promise hold anyway -- it
            # uploads whatever the bucket does not already have, so an inline
            # failure costs hours rather than the file.
            #
            # It is also the ONLY writer that archives INTERVIEW AUDIO. The
            # recorder writes its WAVs incrementally and closes them in `run()`'s
            # `finally`; during a live interview there is no complete file to
            # upload, and a mid-call PUT would ship a truncated container into a
            # bucket where it could never be replaced or removed.
            #
            # 20:30 UTC, and the ordering against the other four clocks is the
            # whole reason it is not simply "some quiet hour":
            #   * AFTER the identity ledger (18:00) and the logical dump
            #     (19:30), which are small and must not queue behind a sweep
            #     that walks the entire volume.
            #   * STRICTLY BEFORE the retention sweep (21:30). This is the
            #     ordering that matters most and it is not the ledger's polite
            #     version of it: `retention.purge_expired` DELETES interview
            #     audio off the volume, so an archive pass that ran after it has
            #     permanently missed every recording that expired that night --
            #     there is no second chance, because the bytes are gone and no
            #     row points at them. An hour of clearance on a job that reads
            #     files and uploads the new ones.
            #   * CLEAR OF THE RDS WINDOW (20:30-21:30) in the only sense that
            #     applies: this job never touches the database. It is listed
            #     here so the next person adding a schedule sees all five in one
            #     place, which is what
            #     test_backup_schedule_clears_the_rds_windows exists to protect.
            #
            # It reuses the api task definition and so the scheduler role's
            # existing ecs:RunTask on `{project}-api:*`, and it runs in the same
            # subnets and security group because it needs the EFS mount -- which
            # is the one way this job differs from the other three, all of which
            # only need the database or nothing at all.
            scheduler.CfnSchedule(
                self,
                "DocumentArchiveSchedule",
                name=f"{project}-document-archive-daily",
                schedule_expression="cron(30 20 * * ? *)",  # 02:00 IST
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=cluster.cluster_arn,
                    role_arn=scheduler_role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                                subnets=[s.ref for s in private_subnets],
                                security_groups=[api_sg.ref],
                                assign_public_ip="DISABLED",
                            )
                        ),
                    ),
                    input=json.dumps(
                        {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.archive_documents"]}]},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

        if harden_ecs and ses_from_address and leave_mail_enabled:
            # "<NAME> IS ON LEAVE TODAY", DAILY AT 07:00 IST (app/leave_today_job.py).
            # Every approved faculty leave covering the day is mailed to every
            # other faculty member ON THE LEAVE DAY, not when it is approved,
            # which is the whole reason this is a schedule and not a line in
            # the decision endpoint.
            #
            # GATED ON EXACTLY THE CONDITION THAT PUTS `LEAVE_MAIL_ENABLED` IN
            # THE TASK DEFINITION above (`harden_ecs`, a sender, the switch). A
            # schedule without that variable is a job that wakes every morning
            # and announces nothing, and one ahead of the sender is refused at
            # synth already.
            #
            # 01:30 UTC is before the working day in India and clear of every
            # other clock in this stack: the RDS backup (20:30-21:30) and
            # maintenance (Sun 21:30-22:30) windows, the AWS Backup rule
            # (19:00), and the four jobs above (18:00-21:30). It only reads a
            # handful of rows and writes `mail_logs`, so the ordering is about
            # the inbox, not the database.
            #
            # It reuses the api task definition and so the scheduler role's
            # existing ecs:RunTask on `{project}-api:*`, and the api task role
            # already holds the SES send grant it needs.
            scheduler.CfnSchedule(
                self,
                "LeaveTodaySchedule",
                name=f"{project}-leave-today-daily",
                schedule_expression="cron(30 1 * * ? *)",  # 07:00 IST
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=cluster.cluster_arn,
                    role_arn=scheduler_role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                                subnets=[s.ref for s in private_subnets],
                                security_groups=[api_sg.ref],
                                assign_public_ip="DISABLED",
                            )
                        ),
                    ),
                    input=json.dumps(
                        {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.leave_today_job"]}]},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

        if job_scout_adzuna or job_scout_jooble or job_scout_companies:
            # THE DAILY JOB AGENT, AT 06:00 IST (app/job_scout_job.py): fresher
            # jobs and internships for every specialization, from Adzuna,
            # Jooble and the companies' own job feeds, on the Jobs board before
            # students open it.
            #
            # GATED ON A SOURCE BEING ON IN THE TASK DEFINITION, the leave-today
            # rule: a schedule with nothing behind it is a job that wakes every
            # morning, says "not configured" and posts nothing.
            #
            # 00:30 UTC is clear of every other clock here: the RDS backup
            # (20:30-21:30) and maintenance (Sun 21:30-22:30) windows, the AWS
            # Backup rule (19:00), the four nightly jobs (18:00-21:30) and the
            # leave announcement (01:30). It reads the catalogue, writes a few
            # dozen `jobs` rows and calls public APIs through the NAT instance,
            # the egress the Google JWKS fetch already uses.
            #
            # It reuses the api task definition and so the scheduler role's
            # existing ecs:RunTask on `{project}-api:*`; no new IAM.
            scheduler.CfnSchedule(
                self,
                "JobScoutSchedule",
                name=f"{project}-job-scout-daily",
                schedule_expression="cron(30 0 * * ? *)",  # 06:00 IST
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=cluster.cluster_arn,
                    role_arn=scheduler_role.role_arn,
                    ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                        task_definition_arn=task_def.task_definition_arn,
                        launch_type="FARGATE",
                        network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                            awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                                subnets=[s.ref for s in private_subnets],
                                security_groups=[api_sg.ref],
                                assign_public_ip="DISABLED",
                            )
                        ),
                    ),
                    input=json.dumps(
                        {"containerOverrides": [{"name": "api", "command": ["python", "-m", "app.job_scout_job"]}]},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

        # ------------------------------------------------------------- edge --
        spa_fallback = cloudfront.Function(
            self,
            "SpaFallback",
            function_name=f"{project}-spa-fallback",
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Serve index.html for SPA routes. Never runs on /api/*.",
            code=cloudfront.FunctionCode.from_inline(_SPA_FALLBACK_JS),
            auto_publish=True,
        )
        oac = cloudfront.S3OriginAccessControl(
            self,
            "WebOac",
            origin_access_control_name=f"{project}-web",
            # The live OAC's description, which is the aws provider's default
            # when the argument is unset. Omitting it is a drift row at step 5
            # and a live write at step 9 that clears the field for nothing.
            description="Managed by Terraform",
            signing=cloudfront.Signing.SIGV4_ALWAYS,
        )
        api_origin_domain = alb_origin_domain or alb.load_balancer_dns_name
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"{project} - SPA + /api",
            enable_ipv6=False,  # Terraform's default; CDK's is true, and it would flip on the next distribution update
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,
            web_acl_id=waf_acl_arn or None,
            domain_names=[domain_name] if domain_name else None,
            certificate=(
                __import__("aws_cdk.aws_certificatemanager", fromlist=["Certificate"]).Certificate.from_certificate_arn(
                    self, "CfCert", cf_cert_arn
                )
                if domain_name and cf_cert_arn
                else None
            ),
            minimum_protocol_version=(
                # The live distribution was given its alias, certificate and this
                # policy in the console on 2026-09-02, outside Terraform; the
                # runbook's refresh recorded it and cdn.tf was edited to match.
                # The mirror says what is there, not what the first apply chose.
                cloudfront.SecurityPolicyProtocol.TLS_V1_3_2025 if domain_name else cloudfront.SecurityPolicyProtocol.TLS_V1
            ),
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket, origin_access_control=oac, origin_id="web-s3"),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.from_cache_policy_id(self, "CacheOptimized", _CACHE_OPTIMIZED),
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(function=spa_fallback, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST)
                ],
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        api_origin_domain,
                        origin_id="api-alb",
                        protocol_policy=(
                            cloudfront.OriginProtocolPolicy.HTTPS_ONLY if alb_tls else cloudfront.OriginProtocolPolicy.HTTP_ONLY
                        ),
                        origin_ssl_protocols=[cloudfront.OriginSslPolicy.TLS_V1_2],
                        read_timeout=Duration.seconds(60),
                        http_port=80,
                        https_port=443,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    cache_policy=cloudfront.CachePolicy.from_cache_policy_id(self, "CacheDisabled", _CACHE_DISABLED),
                    origin_request_policy=cloudfront.OriginRequestPolicy.from_origin_request_policy_id(
                        self, "OriginAllViewer", _ORIGIN_ALL_VIEWER
                    ),
                    compress=True,
                ),
            },
        )
        web_origin = f"https://{domain_name}" if domain_name else f"https://{distribution.distribution_domain_name}"
        for c in containers:  # every family, or a colour would build links to the wrong origin
            c.add_environment("WEB_ORIGIN", web_origin)

        # ----------------------------------------------------- observability --
        alarm_action = cw_actions.SnsAction(alerts)

        def alarm(cid: str, *, name: str, metric: cw.Metric, threshold: float, periods: int, op: cw.ComparisonOperator,
                  missing: cw.TreatMissingData | None = None, description: str | None = None, ok: bool = False,
                  action: cw_actions.SnsAction | None = None) -> cw.Alarm:
            # `action` overrides the ops topic for the two SES reputation
            # alarms, which live were pointed at the mail topic instead. See
            # where they are declared for why that is mirrored and not fixed.
            destination = action or alarm_action
            a = cw.Alarm(
                self, cid, alarm_name=name, alarm_description=description, metric=metric, threshold=threshold,
                evaluation_periods=periods, comparison_operator=op, treat_missing_data=missing,
            )
            a.add_alarm_action(destination)
            if ok:
                a.add_ok_action(destination)
            return a

        dropped = logs.MetricFilter(
            self,
            "DroppedTurnsFilter",
            log_group=log_group,
            filter_name="interview-dropped-turns",
            filter_pattern=logs.FilterPattern.literal('"Dropped interview turn"'),
            metric_namespace="REEP/AI",
            metric_name="DroppedInterviewTurns",
            metric_value="1",
            default_value=0,
        )
        alarm(
            "DroppedTurnsAlarm",
            name=f"{project}-interview-dropped-turns",
            description="Interview turns are being dropped - conversations sound fine and save nothing. See the voice runbook in AGENTS.md.",
            metric=dropped.metric(statistic="Sum", period=Duration.minutes(5)),
            threshold=1, periods=1, op=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            missing=cw.TreatMissingData.NOT_BREACHING,
        )
        # THE SAME SHAPE AS THE DROPPED TURNS ABOVE, AND FOR THE SAME REASON:
        # a write the product deliberately does not let fail is a write nobody
        # hears about when it does.
        #
        # 2026-09-15: every outbound message was refused for eighty minutes --
        # the task role had lost permission to send under its configuration set
        # -- and NOTHING reported it. `mailer.deliver_once` swallows the driver's
        # exception so a decision stands whether or not its mail went, the
        # endpoints answered 200, and the only witness was a `mail_logs.error`
        # column nothing reads. A rejected applicant, who has no other channel,
        # got nothing. It surfaced because a person said so.
        #
        # WHY NOT AN ALARM ON THE ROW. `mail_logs` is in a private subnet and
        # CloudWatch cannot query it; an alarm over it would need a scheduled
        # task, which is one more thing that fails silently. The log line is
        # already leaving the process for CloudWatch, so the filter is free.
        #
        # NOT_BREACHING, unlike the backup sweep's silent alarm: no failures
        # publishes no datapoint, and a quiet hour here means mail is fine. The
        # state this cannot see -- the api not running at all -- is what
        # `reep-no-healthy-api` is for.
        # HARDEN ONLY, unlike the dropped-turns pair directly above, and the
        # difference is not style: that filter EXISTS IN TERRAFORM, so the
        # import mirror legitimately claims it. This one never did. Rendering
        # it in `phase=import` asks `tools/import_map.py` to find a live
        # address for a resource that was never managed, which is the whole
        # point of that tool's other direction -- eight of its tests fail on it,
        # correctly, and that is how this gate was found.
        if harden:
            mail_failed = logs.MetricFilter(
                self,
                "MailSendFailedFilter",
                log_group=log_group,
                filter_name="mail-send-failed",
                # The literal is `app.mailer.MAIL_SEND_FAILED`, and
                # tests/test_codebase_guards.py compares the two so that
                # renaming the message cannot quietly unhook this alarm.
                filter_pattern=logs.FilterPattern.literal('"Mail send failed"'),
                metric_namespace="REEP/Mail",
                metric_name="MailSendFailed",
                metric_value="1",
                default_value=0,
            )
            alarm(
                "MailSendFailedAlarm",
                name=f"{project}-mail-send-failed",
                description=(
                    "Outbound mail is being refused. Activation links, password resets, sign-in codes and "
                    "rejection notices are not arriving, and every endpoint above still answers 200. Check "
                    "the api task role's send-mail policy and the SES identity, then mail_logs for the "
                    "FAILED rows."
                ),
                metric=mail_failed.metric(statistic="Sum", period=Duration.minutes(5)),
                threshold=1, periods=1, op=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                missing=cw.TreatMissingData.NOT_BREACHING,
            )
        alarm(
            "Alb5xxAlarm",
            name=f"{project}-alb-5xx",
            metric=cw.Metric(namespace="AWS/ApplicationELB", metric_name="HTTPCode_Target_5XX_Count", statistic="Sum",
                             period=Duration.minutes(5), dimensions_map={"LoadBalancer": alb.load_balancer_full_name}),
            threshold=10, periods=1, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD, missing=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm(
            "NoHealthyApiAlarm",
            name=f"{project}-no-healthy-api",
            description="Fewer than one healthy api task behind the ALB - the dashboard is down.",
            metric=cw.Metric(namespace="AWS/ApplicationELB", metric_name="HealthyHostCount", statistic="Minimum",
                             period=Duration.minutes(1),
                             dimensions_map={"LoadBalancer": alb.load_balancer_full_name, "TargetGroup": target_group.target_group_full_name}),
            threshold=1, periods=3, op=cw.ComparisonOperator.LESS_THAN_THRESHOLD, missing=cw.TreatMissingData.BREACHING, ok=True,
        )
        alarm(
            "RdsStorageAlarm",
            name=f"{project}-rds-low-storage",
            metric=cw.Metric(namespace="AWS/RDS", metric_name="FreeStorageSpace", statistic="Minimum", period=Duration.minutes(5),
                             dimensions_map={"DBInstanceIdentifier": f"{project}-postgres"}),
            threshold=5 * 1024 * 1024 * 1024, periods=1, op=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
        )
        alarm(
            "RdsCpuAlarm",
            name=f"{project}-rds-cpu",
            metric=cw.Metric(namespace="AWS/RDS", metric_name="CPUUtilization", statistic="Average", period=Duration.minutes(5),
                             dimensions_map={"DBInstanceIdentifier": f"{project}-postgres"}),
            threshold=85, periods=2, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        alarm(
            "ApiCpuPeggedAlarm",
            name=f"{project}-api-cpu-at-max",
            description="CPU high while autoscaling should have absorbed it - likely at api_max_tasks. Raise the ceiling or find the hot path in Sentry.",
            metric=cw.Metric(namespace="AWS/ECS", metric_name="CPUUtilization", statistic="Average", period=Duration.minutes(5),
                             dimensions_map={"ClusterName": project, "ServiceName": "api"}),
            threshold=85, periods=3, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        )
        if blue_green:
            assert tg_green is not None and service_green is not None
            # Two colours means the alarms above see HALF the picture after
            # every odd-numbered deploy: `reep-no-healthy-api` and
            # `reep-api-cpu-at-max` are keyed on blue's target group and
            # service. These are their green twins, plus the one alarm that
            # neither per-colour alarm can express — the ALB's OWN 5xx, which
            # is what "the live colour has no healthy target" looks like from
            # the outside. Shipped in the same change as the second service,
            # never later.
            alarm(
                "NoHealthyApiGreenAlarm",
                name=f"{project}-no-healthy-api-green",
                description="Fewer than one healthy api-green task. If green is live the dashboard is down; if idle, the rollback target is dead.",
                metric=cw.Metric(namespace="AWS/ApplicationELB", metric_name="HealthyHostCount", statistic="Minimum",
                                 period=Duration.minutes(1),
                                 dimensions_map={"LoadBalancer": alb.load_balancer_full_name, "TargetGroup": tg_green.target_group_full_name}),
                threshold=1, periods=3, op=cw.ComparisonOperator.LESS_THAN_THRESHOLD, missing=cw.TreatMissingData.BREACHING, ok=True,
            )
            alarm(
                "AlbElb5xxAlarm",
                name=f"{project}-alb-elb-5xx",
                description="The ALB itself answered 5xx - the live colour has no healthy target, or the flip landed on an empty one. Rollback: Actions -> Rollback.",
                metric=cw.Metric(namespace="AWS/ApplicationELB", metric_name="HTTPCode_ELB_5XX_Count", statistic="Sum",
                                 period=Duration.minutes(1), dimensions_map={"LoadBalancer": alb.load_balancer_full_name}),
                threshold=10, periods=1, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD, missing=cw.TreatMissingData.NOT_BREACHING,
            )
            alarm(
                "ApiCpuPeggedGreenAlarm",
                name=f"{project}-api-green-cpu-at-max",
                description="CPU high on api-green while autoscaling should have absorbed it - likely at api_max_tasks.",
                metric=cw.Metric(namespace="AWS/ECS", metric_name="CPUUtilization", statistic="Average", period=Duration.minutes(5),
                                 dimensions_map={"ClusterName": project, "ServiceName": "api-green"}),
                threshold=85, periods=3, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            )
            # The per-colour target 5xx alarms each service's DeploymentConfiguration
            # names (ROLLBACK_ON_ALARM). A colour rolls only while it is idle, so
            # the traffic that can trip this during a roll is the smoke task and
            # the header-routed probes — which is the point: a candidate that
            # 5xxs the probe is rolled back by ECS before any weight moves.
            # Missing data is NOT breaching, so an idle colour with no traffic
            # never blocks its own deployment.
            for colour, tg, svc in (("blue", target_group, service), ("green", tg_green, service_green)):
                a = alarm(
                    f"Api5xx{colour.capitalize()}Alarm",
                    name=_deploy_alarm_name(colour),
                    description=f"Target 5xx on the {colour} colour. Named in api{'-green' if colour == 'green' else ''}'s deployment alarms: ECS rolls the deployment back on ALARM.",
                    metric=cw.Metric(namespace="AWS/ApplicationELB", metric_name="HTTPCode_Target_5XX_Count", statistic="Sum",
                                     period=Duration.minutes(1),
                                     dimensions_map={"LoadBalancer": alb.load_balancer_full_name, "TargetGroup": tg.target_group_full_name}),
                    threshold=5, periods=1, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD, missing=cw.TreatMissingData.NOT_BREACHING,
                )
                svc.node.add_dependency(a)
        if harden:
            # The backup that silently stopped happening is the one that hurts —
            # and a backup, a copy and a restore test are three different jobs
            # with three different failure metrics.
            for cid, suffix, metric_name, what in (
                ("BackupJobsFailedAlarm", "backup-job-failed", "NumberOfBackupJobsFailed", "daily snapshot"),
                ("CopyJobsFailedAlarm", "backup-copy-failed", "NumberOfCopyJobsFailed", "cross-region copy to ap-southeast-1"),
                ("RestoreJobsFailedAlarm", "backup-restore-test-failed", "NumberOfRestoreJobsFailed", "weekly restore test"),
            ):
                alarm(
                    cid,
                    name=f"{project}-{suffix}",
                    description=f"An AWS Backup {what} job failed. Check the Backup console before the next one runs.",
                    metric=cw.Metric(namespace="AWS/Backup", metric_name=metric_name, statistic="Sum",
                                     period=Duration.hours(24), dimensions_map={"BackupVaultName": f"{project}-vault"}),
                    threshold=1, periods=1, op=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                    missing=cw.TreatMissingData.NOT_BREACHING,
                )
            # A FAILURE ALARM CANNOT SEE A JOB THAT NEVER STARTED. The three
            # above fire on NumberOfBackupJobs*Failed and treat missing data as
            # NOT breaching — correct for them, because a day with no failures
            # publishes no datapoint. The consequence is that a plan which stops
            # running ENTIRELY is the one state none of them reports: no jobs,
            # no failures, no metric, three green alarms and a vault quietly
            # going stale. That is the shape of the 2026-09-07 incident with the
            # alarm already deployed.
            #
            # AWS Backup emits a metric only when the value is nonzero, so
            # "nothing completed" IS missing data, and BREACHING is what makes
            # the absence legible. A period of 24 h matches the daily rule; a
            # single missed day raises it.
            #
            # WHAT THIS STILL CANNOT SEE, so nobody reads more into it than it
            # says: with the daily rule writing into the same vault, the metric
            # stays nonzero even if the MONTHLY rule never fires. A per-rule
            # "did not fire" alarm is not expressible as a metric alarm on a
            # shared vault — it would need its own vault or an EventBridge
            # check. What guarantees the monthly rule is well formed is the
            # synth guard, not this.
            alarm(
                "BackupJobsSilentAlarm",
                name=f"{project}-backup-no-job-completed",
                description=(
                    "No AWS Backup job completed in 24 hours. The plan is not failing — it is not "
                    "running. Check the Backup console and the plan's selections."
                ),
                metric=cw.Metric(namespace="AWS/Backup", metric_name="NumberOfBackupJobsCompleted", statistic="Sum",
                                 period=Duration.hours(24), dimensions_map={"BackupVaultName": f"{project}-vault"}),
                threshold=1, periods=1, op=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
                missing=cw.TreatMissingData.BREACHING,
            )

        # -------------------------------------------------------------- ses --
        # THE MAIL PATH EXISTED FOR SIX DAYS BEFORE ANY OF IT WAS IN A
        # REPOSITORY. The identity, its DKIM, the configuration set, the event
        # destination, the notifications topic and the two reputation alarms
        # were all made by hand in the console on 2026-09-09/10, and until this
        # block none of them appeared in any template or any file: the api's
        # `send-mail` policy and `SES_FROM_ADDRESS` were the only managed half,
        # so `cdk deploy` could rebuild the PERMISSION to send and nothing that
        # makes sending work. A new region, a new account, or a rebuild after a
        # mistake reproduced an api that was allowed to mail and could not.
        #
        # These are therefore a MIRROR of what is live, property by property,
        # and deliberately not an improvement on it. Two values look wrong here
        # and are left alone because changing them is a separate decision with
        # its own consequence: `tls_policy="OPTIONAL"` (REQUIRE would refuse
        # delivery to a receiver with no STARTTLS rather than fall back), and
        # `reputation_metrics_enabled=False` (True publishes the per-set
        # reputation metrics, which is what would move the two alarms below off
        # INSUFFICIENT_DATA). Adopting first and arguing second is what keeps
        # the adoption a no-op.
        if ses_managed:
            # The topic SES publishes every bounce, complaint, delivery, reject
            # and send to. Separate from `reep-alerts` because it is an EVENT
            # stream, not an alert: one message per message sent.
            ses_topic = sns.Topic(self, "SesNotifications", topic_name=f"{project}-ses-notifications")
            if harden and ses_notifications_email:
                # Import-hostile, exactly as the alerts subscription is, so the
                # adoption run must not carry it: `cdk import` refuses a change
                # set that also creates something. docs/ses-mail.md spells the
                # two commands out rather than leaving the flag to memory.
                ses_topic.add_subscription(subs.EmailSubscription(ses_notifications_email))

            configuration_set = ses.CfnConfigurationSet(
                self,
                "SesConfigurationSet",
                name=ses_configuration_set,
                delivery_options=ses.CfnConfigurationSet.DeliveryOptionsProperty(tls_policy="OPTIONAL"),
                reputation_options=ses.CfnConfigurationSet.ReputationOptionsProperty(reputation_metrics_enabled=False),
                sending_options=ses.CfnConfigurationSet.SendingOptionsProperty(sending_enabled=True),
            )

            # NO `MailFromAttributes` AND NO `DkimSigningAttributes`, both on
            # purpose. There is no custom MAIL FROM domain live; declaring one
            # here would publish a subdomain whose MX and SPF records nobody has
            # added, and `BehaviorOnMxFailure` decides only whether that failure
            # is loud. And DKIM is SES-managed (`SigningAttributesOrigin:
            # AWS_SES`): naming signing attributes is how a deploy rotates the
            # keys, which republishes three CNAMEs and stops mail until DNS
            # catches up. What is declared is the fact that signing is ON.
            identity = ses.CfnEmailIdentity(
                self,
                "SesIdentity",
                email_identity=ses_identity_domain,
                dkim_attributes=ses.CfnEmailIdentity.DkimAttributesProperty(signing_enabled=True),
                feedback_attributes=ses.CfnEmailIdentity.FeedbackAttributesProperty(email_forwarding_enabled=True),
                configuration_set_attributes=ses.CfnEmailIdentity.ConfigurationSetAttributesProperty(
                    configuration_set_name=ses_configuration_set
                ),
            )
            identity.add_dependency(configuration_set)

            # THE IDENTITY DEFAULT AND THE PER-SEND NAME ARE BOTH DECLARED, AND
            # THEY ARE NOT REDUNDANT. `configuration_set_attributes` above makes
            # this set the identity's default, which is what carried the event
            # stream before `SES_CONFIGURATION_SET` reached the task; the api
            # now also names it on every call. Belt and braces is right here
            # because the two fail in opposite directions: an edit to the
            # identity kills the default silently, and a set that stops existing
            # makes a named send fail loudly. Neither alone covers both.
            event_destination = ses.CfnConfigurationSetEventDestination(
                self,
                "SesEventDestination",
                configuration_set_name=ses_configuration_set,
                event_destination=ses.CfnConfigurationSetEventDestination.EventDestinationProperty(
                    name="sns-bounces-complaints",
                    enabled=True,
                    # DELIVERY, REJECT and SEND ride along with the two that
                    # matter: without SEND and DELIVERY a silent failure and a
                    # healthy quiet week publish the same nothing.
                    matching_event_types=["BOUNCE", "COMPLAINT", "DELIVERY", "REJECT", "SEND"],
                    sns_destination=ses.CfnConfigurationSetEventDestination.SnsDestinationProperty(
                        topic_arn=ses_topic.topic_arn
                    ),
                ),
            )
            event_destination.add_dependency(configuration_set)

            # AWS SUSPENDS SENDING ABOVE ~5% BOUNCES AND ~0.1% COMPLAINTS, and
            # it does so for the whole account -- activation links, reset links
            # and sign-in codes with it, which is every door into the product
            # that is not Google. Hence an alarm on each, at exactly the two
            # numbers AWS publishes.
            #
            # `MISSING`, NOT `BREACHING`, and this is the opposite call from
            # BackupJobsSilentAlarm above. That alarm treats absence as failure
            # because a backup plan that stops running is the incident. Here
            # absence means nobody was mailed this hour, which on a college's
            # volume is most hours -- a quiet inbox is not a reputation problem,
            # and an alarm that shouts on every quiet hour is an alarm somebody
            # filters. What makes this legible rather than dishonest is that the
            # thing it cannot see (no mail going out at all) is not a mail
            # failure: a student who never asked for a reset was never failed.
            #
            # These fire into the MAIL topic rather than `reep-alerts`, which is
            # what is live and is mirrored rather than fixed. It is arguably
            # wrong -- an alarm arrives among per-message event JSON -- but
            # moving it is a change to where a human looks, not a template
            # detail, and it does not belong in the commit that adopts the
            # resources.
            ses_alarm_action = cw_actions.SnsAction(ses_topic)
            for cid, suffix, metric_name, threshold, what in (
                ("SesBounceRateAlarm", "ses-bounce-rate", "Reputation.BounceRate", 0.05, "bounce"),
                ("SesComplaintRateAlarm", "ses-complaint-rate", "Reputation.ComplaintRate", 0.001, "complaint"),
            ):
                alarm(
                    cid,
                    name=f"{project}-{suffix}",
                    description=(
                        f"SES {what} rate is above the level AWS suspends sending at. Sending is suspended for the "
                        "ACCOUNT, so activation links, password resets and sign-in codes all stop. Check the "
                        f"{project}-ses-notifications topic for which addresses are failing."
                    ),
                    metric=cw.Metric(namespace="AWS/SES", metric_name=metric_name, statistic="Average",
                                     period=Duration.hours(1)),
                    threshold=threshold, periods=1, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    missing=cw.TreatMissingData.MISSING,
                    action=ses_alarm_action,
                )

        observer_principal_obj: iam.IPrincipal = (
            iam.ArnPrincipal(observer_principal) if observer_principal else iam.AccountRootPrincipal()
        )
        iam.Role(
            self,
            "ClaudeObserverRole",
            role_name=f"{project}-claude-observer",
            assumed_by=observer_principal_obj,
            inline_policies={
                "read-only-diagnosis": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "cloudwatch:Get*", "cloudwatch:List*", "cloudwatch:Describe*",
                                "logs:Get*", "logs:Describe*", "logs:FilterLogEvents",
                                "logs:StartQuery", "logs:GetQueryResults", "logs:StopQuery",
                                "ecs:Describe*", "ecs:List*",
                                "rds:Describe*",
                                "elasticloadbalancing:Describe*",
                                "application-autoscaling:Describe*",
                            ],
                            resources=["*"],
                        )
                    ]
                )
            },
        )

        # --------------------------------------------------------- github --
        if github_oidc_arn:
            # The account already has the provider (Terraform's
            # create_github_oidc_provider=false, or it predates REEP): reference
            # it, never declare a second one — an account has one per URL.
            oidc_arn = github_oidc_arn
        else:
            oidc = iam.CfnOIDCProvider(
                self,
                "GithubOidc",
                url="https://token.actions.githubusercontent.com",
                client_id_list=["sts.amazonaws.com"],
                thumbprint_list=["6938fd4d98bab03faadb97b34396831e3780aea1"],
            )
            oidc_arn = oidc.attr_arn
        subjects = [f"repo:{gh_repo}:ref:{gh_ref}"]
        if gh_repo_ids:
            subjects.insert(0, f"repo:{gh_repo_ids}:ref:{gh_ref}")
        # What the deploy role may roll and run. Under blue/green: both colour
        # services, and one-off tasks on all three families (migrations and
        # the smoke task run on the IDLE colour's family, so they exercise the
        # candidate image). The role still holds NO CodeDeploy, CloudFormation,
        # RDS, Backup or IAM-write right; test_the_deploy_role_still_has_no_codedeploy_cloudformation_or_rds
        # keeps it that way.
        deploy_services = [service.service_arn] + ([service_green.service_arn] if service_green is not None else [])
        deploy_families = [f"{project}-api"] + ([f"{project}-api-blue", f"{project}-api-green"] if blue_green else [])
        in_this_cluster = {"ArnEquals": {"ecs:cluster": cluster.cluster_arn}}
        blue_green_statements: list[iam.PolicyStatement] = []
        if blue_green:
            assert colour_rule is not None
            blue_green_statements = [
                # ELBv2's Describe* actions are not resource-scopable.
                iam.PolicyStatement(
                    sid="ReadTheColours",
                    actions=[
                        "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeRules",
                        "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetHealth",
                    ],
                    resources=["*"],
                ),
                # THE FLIP. ModifyRule on this one rule's ARN — never
                # ModifyListener, which would also let the role change the
                # listener's certificate and TLS policy. This role can move
                # production traffic between the two colours and nothing else
                # on the ALB.
                iam.PolicyStatement(sid="FlipTheColour", actions=["elasticloadbalancing:ModifyRule"], resources=[colour_rule.listener_rule_arn]),
                # The rollback workflow reports what the idle colour is running
                # before it moves anything; the smoke step stops a task that
                # hangs past its budget. Both scoped to the cluster.
                iam.PolicyStatement(sid="ListAndStopTasksInTheCluster", actions=["ecs:ListTasks", "ecs:StopTask"], resources=["*"], conditions=in_this_cluster),
                # The post-flip watch reads the ALB alarms and flips back on
                # ALARM; the smoke step prints the task's log lines.
                iam.PolicyStatement(sid="WatchTheAlarms", actions=["cloudwatch:DescribeAlarms"], resources=["*"]),
                iam.PolicyStatement(sid="ReadTheSmokeLog", actions=["logs:GetLogEvents", "logs:FilterLogEvents"], resources=[log_group.log_group_arn]),
            ]
        deploy_role = iam.Role(
            self,
            "GithubDeployRole",
            role_name=f"{project}-github-deploy",
            assumed_by=iam.FederatedPrincipal(
                oidc_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": subjects,
                    }
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            inline_policies={
                "deploy-api-and-spa": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(sid="EcrLogin", actions=["ecr:GetAuthorizationToken"], resources=["*"]),
                        iam.PolicyStatement(
                            sid="PushTheApiImage",
                            actions=[
                                "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
                                "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages",
                            ],
                            resources=[api_repo.attr_arn],
                        ),
                        iam.PolicyStatement(sid="RollTheService", actions=["ecs:UpdateService", "ecs:DescribeServices"], resources=deploy_services),
                        iam.PolicyStatement(
                            sid="RunOneOffTasks",
                            actions=["ecs:RunTask"],
                            resources=[f"arn:aws:ecs:{self.region}:{self.account}:task-definition/{family}:*" for family in deploy_families],
                            conditions=in_this_cluster,
                        ),
                        iam.PolicyStatement(sid="WatchThoseTasks", actions=["ecs:DescribeTasks", "ecs:DescribeTaskDefinition"], resources=["*"]),
                        *blue_green_statements,
                        iam.PolicyStatement(
                            sid="HandTheTaskItsRoles",
                            actions=["iam:PassRole"],
                            resources=[task_execution_role.role_arn, api_task_role.role_arn],
                            conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
                        ),
                        iam.PolicyStatement(sid="PublishTheSpa", actions=["s3:ListBucket"], resources=[web_bucket.bucket_arn]),
                        iam.PolicyStatement(sid="WriteTheSpa", actions=["s3:PutObject", "s3:DeleteObject", "s3:GetObject"], resources=[web_bucket.arn_for_objects("*")]),
                        iam.PolicyStatement(
                            sid="BustTheCache",
                            actions=["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
                            resources=[f"arn:aws:cloudfront::{self.account}:distribution/{distribution.distribution_id}"],
                        ),
                        # infra-drift.yml's third check, and the ONLY one of its
                        # three that this role should be given.
                        #
                        # It reads GROSS spend under RECORD_TYPE=Usage, which is
                        # the view that would have shown the OpenSearch
                        # collection billing $11.87/day while promotional
                        # credits made it read $0.00 everywhere a human looks.
                        # Until it is granted the step warns and the run files
                        # "Gross spend was NOT measured this run — blank, not
                        # clean", which is honest and is not a measurement.
                        #
                        # COST EXPLORER HAS NO RESOURCE-LEVEL PERMISSIONS, so
                        # `*` here is the only spelling that exists, not a
                        # widening somebody skipped scoping. What it grants is
                        # an aggregate billing read: no resource is named, no
                        # resource is reached, and nothing can be changed.
                        #
                        # The drift-detection half of that workflow is
                        # deliberately NOT granted here. CloudFormation reads
                        # every live resource with the CALLER's credentials, so
                        # making `detect-stack-drift` actually answer (rather
                        # than return NOT_CHECKED for most of the stack) needs
                        # read access across EC2, ECS, RDS, ELB, S3, IAM,
                        # Scheduler, Backup and WAF — effectively
                        # ReadOnlyAccess on a role every GitHub Actions run can
                        # assume. That is a far larger grant than the question
                        # it answers is worth, and it would break this role's
                        # stated "no CloudFormation right" boundary. That check
                        # has its own role, `GithubDriftRole` below, not the
                        # role that ships code.
                        iam.PolicyStatement(sid="ReadGrossSpend", actions=["ce:GetCostAndUsage"], resources=["*"]),
                    ]
                )
            },
        )

        # THE SEPARATE ROLE THE COMMENT ABOVE ASKS FOR (#117). infra-drift.yml
        # assumes it for `detect-stack-drift` alone; `cdk diff` and the spend
        # read stay on the deploy role. It can start a drift detection on
        # reep-core and reep-voice-platform, read each resource's
        # CONFIGURATION the way CloudFormation's read handler does, and nothing
        # else: no write, no deploy, and an explicit Deny on every read that
        # reaches a student's record instead of a resource's settings.
        # reep_core/drift_reader.py carries the list and the reasoning.
        #
        # HARDEN ONLY: the import phase mirrors what Terraform handed over and
        # this role was never part of that. Same OIDC trust as the deploy
        # role, so only a workflow on `main` can assume it.
        drift_role = None
        if harden:
            drift_stack_arns = [
                f"arn:aws:cloudformation:{self.region}:{self.account}:stack/{project}-{suffix}/*"
                for suffix in drift_reader.DRIFT_CHECKED_STACKS
            ]
            drift_role = iam.Role(
                self,
                "GithubDriftRole",
                role_name=f"{project}-github-drift",
                assumed_by=iam.FederatedPrincipal(
                    oidc_arn,
                    conditions={
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                            "token.actions.githubusercontent.com:sub": subjects,
                        }
                    },
                    assume_role_action="sts:AssumeRoleWithWebIdentity",
                ),
                inline_policies={
                    "detect-drift-read-only": iam.PolicyDocument(
                        statements=[
                            iam.PolicyStatement(
                                sid="DetectDriftOnTheseStacks",
                                actions=list(drift_reader.CLOUDFORMATION_STACK_ACTIONS),
                                resources=drift_stack_arns,
                            ),
                            # Neither action accepts a resource ARN.
                            iam.PolicyStatement(
                                sid="ReadTheDetectionStatus",
                                actions=list(drift_reader.CLOUDFORMATION_UNSCOPED_ACTIONS),
                                resources=["*"],
                            ),
                            # Describe/Get/List only, and most of them accept
                            # no resource ARN either.
                            iam.PolicyStatement(
                                sid="ReadResourceConfiguration",
                                actions=drift_reader.read_actions(),
                                resources=["*"],
                            ),
                            iam.PolicyStatement(
                                sid="NeverReadTheData",
                                effect=iam.Effect.DENY,
                                actions=list(drift_reader.DATA_READS_DENIED),
                                resources=["*"],
                            ),
                        ]
                    )
                },
            )
            # A fixed name and plain Retain is how #124 happened: a create that
            # rolls back keeps the role, and the retry collides with it.
            drift_role.apply_removal_policy(RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE)

        # ---------------------------------------------------------- outputs --
        CfnOutput(self, "Phase", value=self.phase)
        CfnOutput(self, "CloudfrontDomain", value=distribution.distribution_domain_name)
        CfnOutput(self, "CloudfrontDistributionId", value=distribution.distribution_id)
        CfnOutput(self, "AlbDnsName", value=alb.load_balancer_dns_name)
        CfnOutput(self, "WebBucketName", value=web_bucket.bucket_name)
        CfnOutput(self, "EcsCluster", value=cluster.cluster_name)
        CfnOutput(self, "DbEndpoint", value=db.attr_endpoint_address)
        CfnOutput(self, "GithubDeployRoleArn", value=deploy_role.role_arn)
        if drift_role is not None:
            CfnOutput(self, "GithubDriftRoleArn", value=drift_role.role_arn)
        CfnOutput(self, "BackupVaultArn", value=vault.attr_backup_vault_arn)
        if blue_green:
            assert colour_rule is not None and tg_green is not None
            # The workflow's repository variable ALB_COLOUR_RULE_ARN comes from
            # here, like every other value deploy.yml needs — never typed.
            CfnOutput(self, "ColourRuleArn", value=colour_rule.listener_rule_arn)
            CfnOutput(self, "TrafficListenerArn", value=traffic_listener.listener_arn)
            CfnOutput(self, "ApiTargetGroupArn", value=target_group.target_group_arn)
            CfnOutput(self, "ApiTargetGroupGreenArn", value=tg_green.target_group_arn)
            CfnOutput(self, "LiveColourInTemplate", value=live_colour)

        # TAGS GO ON THE CHILDREN, NEVER ON THE STACK, AND THIS IS NOT A STYLE
        # CHOICE. `Tags.of(stack).add(...)` tags the Stack itself as well as its
        # resources, and CDK sends a tagged stack's tags to CreateChangeSet as
        # *stack* tags — which CloudFormation refuses on an IMPORT change set:
        #
        #     As part of the import operation, you cannot modify or add
        #     [RoleArn, Tags]
        #
        # That is exactly how the step-3 rehearsal failed on 2026-09-07, and
        # the core import would have failed the same way on all 65 resources.
        # Applying the aspects to each direct child instead leaves the stack
        # untagged in the manifest while every resource still carries its tags,
        # so the mirror is unchanged and the import is accepted. It runs LAST
        # so that every construct exists to be visited.
        # `test_no_stack_level_tags_in_the_import_phase` is the guard.
        for key, value, kwargs in self._tag_plan:
            for child in self.node.children:
                Tags.of(child).add(key, value, **kwargs)

        self.distribution = distribution
        self.service = service
        self.service_green = service_green
        self.colour_rule = colour_rule
        self.db = db
        self.vault = vault
