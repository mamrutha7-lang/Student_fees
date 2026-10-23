"""The daily job agent's entrypoint: ``python -m app.job_scout_job [--dry-run]``.

``retention_job``'s rule: this file is only the seam between a schedule and the
job. WHAT the agent searches for, keeps and posts is ``app/job_scout.py``; WHICH
boards it asks is ``app/job_sources.py``; WHEN it runs is
``infra/cdk/reep_core/stack.py`` (``reep-job-scout-daily``, 06:00 IST, so the
morning's postings are on the board before students open it).

Exit code 0 means the run completed, including a morning on which one of the
two boards failed (Sentry is told, as ERROR on the monitor) and a deployment
with no keys at all (it says so and does nothing). Non-zero means it raised,
or that EVERY configured board failed, so whatever supervises this can tell a
quiet morning from one on which the agent learned nothing.

``--dry-run`` asks the boards and judges every advert, then rolls back: what
the Ops task's ``job-scout-dry-run`` runs before anybody trusts the real one.

It reports as ``reep-scheduled-jobs`` through ``SENTRY_JOBS_DSN``, never the
api's DSN, for the reason ``alerts_job`` gives.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from . import job_scout
from .config import settings
from .db import SessionLocal
from .job_sources import sources_from_settings
from .observability import SERVICE_JOBS, flush, init_sentry, job_run

log = logging.getLogger("reep.job_scout")

#: The Sentry cron monitor, upserted with each check-in. It MUST equal the
#: EventBridge schedule (cron(30 0 * * ? *), UTC, window OFF);
#: tests/test_codebase_guards.py compares the two. Fifteen minutes late before
#: MISSED absorbs a cold image pull; thirty before TIMEOUT is far beyond a run
#: of a few dozen ten-second calls.
MONITOR_SLUG = "reep-job-scout-daily"
MONITOR_CONFIG = {
    "schedule": {"type": "crontab", "value": "30 0 * * *"},
    "timezone": "Etc/UTC",
    "checkin_margin": 15,
    "max_runtime": 30,
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.job_scout_job")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and judge every advert, then write nothing.",
    )
    args = parser.parse_args(argv)

    # basicConfig, not print, for retention_job's reason: an unconfigured root
    # logger sits at WARNING and would drop the summary line below.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    init_sentry(SERVICE_JOBS, settings.sentry_jobs_dsn)

    # ONE clock for the whole run: the age sweep and every new posting's
    # fallback date read the same instant.
    now = datetime.now(timezone.utc)

    try:
        with job_run("jobs.scout", monitor_slug=MONITOR_SLUG, monitor_config=MONITOR_CONFIG) as run:
            sources = sources_from_settings()
            if not sources:
                log.info(
                    "Job agent is off: no board has a key (ADZUNA_APP_ID/ADZUNA_APP_KEY, "
                    "JOOBLE_API_KEY), or JOB_SCOUT_ENABLED=false. Nothing searched."
                )
                return 0
            with SessionLocal() as db:
                summary = job_scout.run(db, now=now, sources=sources, dry_run=args.dry_run)
            log.info(
                "Job agent %s: %d stream(s), %d search(es), %d advert(s) found, %d fresher "
                "role(s) kept, %d posted, %d widened to another specialization, %d already "
                "on the board, %d over the daily limit, %d closed as expired. Per stream: %s.",
                "dry run (nothing written)" if summary.dry_run else "run complete",
                summary.leaves,
                summary.calls,
                summary.found,
                summary.kept,
                summary.created,
                summary.merged,
                summary.duplicates,
                summary.capped,
                summary.closed,
                ", ".join(f"{name} {n}" for name, n in summary.per_leaf.items()) or "none",
            )
            for provider, sentence in summary.errors.items():
                log.error("Job agent: %s failed this morning: %s", provider, sentence)
            if summary.every_source_failed:
                log.error("Job agent: every board failed, so nothing new was found.")
                run.mark_error("every_source_failed")
                return 1
            if summary.errors:
                run.mark_error("source_failed")
    except Exception:
        log.exception("Job agent FAILED.")
        return 1
    finally:
        flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
