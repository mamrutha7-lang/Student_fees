"""The live-interview count, published so autoscaling can follow it (2026-10-29).

WHY NOT CPU ALONE. The api already scales on CPU at 60%, and a mock interview
is a CPU cost — about 4-7% of a half-vCPU task each, measured on production's
Container Insights on 2026-10-29 — so CPU does rise with interviews. But it
rises late: a batch of students pressing Start together lands every handshake
on the tasks that exist, and a task carrying a dozen live audio relays is
already choppy by the time three one-minute CPU datapoints agree. The number
that matters is known exactly, in-process, the moment a socket opens: how many
interviews this task is holding. So that is what the scaling policy tracks,
and CPU stays as the backstop for everything else the api does.

ONE LINE OF EMBEDDED METRIC FORMAT A MINUTE, ON STDOUT. The awslogs driver
ships stdout to CloudWatch Logs, which turns an EMF line into a metric with no
SDK, no PutMetricData grant and no request on the hot path — so this module
needs nothing from IAM and cannot slow an interview down. The line is written
straight to stdout rather than through `logging`, because the log formatter
prefixes a timestamp and level, and an EMF line must be the whole event.

WHAT IT CARRIES: a count, a namespace and the task definition family. No
student, no session id, no address — the line sits in the same log group as
everything else and is read by a scaling policy, not a person.

EVERY TASK REPORTS EVERY MINUTE, IDLE ONES INCLUDED. The policy reads the
AVERAGE across tasks, so an idle task's 0 is what tells it the capacity it
added is there. A task that stops reporting simply drops out of the average;
if every task stops, the alarm goes to INSUFFICIENT_DATA and target tracking
does nothing, which leaves the CPU policy in charge — the failure mode is
"scales the way it did before", never "scales to zero".
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Final, TextIO

log = logging.getLogger(__name__)

#: Read by `infra/cdk/reep_core/stack.py`'s scaling policy. A rename here is a
#: metric nobody reads and a policy that never fires, so the stack imports
#: nothing from here and `tests/test_interview_metrics.py` compares the two.
NAMESPACE: Final[str] = "REEP/Interviews"
METRIC: Final[str] = "LiveInterviews"
DIMENSION: Final[str] = "Family"
INTERVAL_SECONDS: Final[int] = 60


def emf_line(family: str, live: int, *, now_ms: int | None = None) -> str:
    """One CloudWatch Embedded Metric Format event: `live` interviews on this task."""
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    return json.dumps(
        {
            "_aws": {
                "Timestamp": timestamp,
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [[DIMENSION]],
                        "Metrics": [{"Name": METRIC, "Unit": "Count"}],
                    }
                ],
            },
            DIMENSION: family,
            METRIC: max(0, int(live)),
        },
        separators=(",", ":"),
    )


async def publish_forever(
    family: str,
    read_live: Callable[[], int],
    *,
    out: TextIO | None = None,
    interval: float = INTERVAL_SECONDS,
) -> None:
    """Write one line now and one every `interval` seconds until cancelled.

    Never raises out of the loop: a metric line that could not be written is
    a minute the policy does not hear from this task, which it tolerates, and
    taking the lifespan's task down with it would be a far worse trade.
    """
    stream = out if out is not None else sys.stdout
    while True:
        try:
            stream.write(emf_line(family, read_live()) + "\n")
            stream.flush()
        except Exception:
            log.warning("Could not write the live-interview metric line", exc_info=True)
        await asyncio.sleep(interval)
