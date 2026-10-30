"""The live-interview metric autoscaling follows (app/interview_metrics.py).

No database: the line is a pure function of a count, and the loop writes to
whatever stream it is handed. What these pin is the part a wrong edit breaks
silently — CloudWatch drops a malformed EMF line without a word, and a policy
watching a metric name nobody writes never fires and never errors.
"""

import asyncio
import io
import json
from pathlib import Path

from app import interview_metrics as m

STACK = Path(__file__).resolve().parents[3] / "infra" / "cdk" / "reep_core" / "stack.py"


def test_the_line_is_one_valid_emf_event_naming_the_family_and_the_count():
    event = json.loads(m.emf_line("reep-api", 7, now_ms=1_793_000_000_000))
    directive = event["_aws"]["CloudWatchMetrics"][0]
    assert event["_aws"]["Timestamp"] == 1_793_000_000_000
    assert directive["Namespace"] == "REEP/Interviews"
    assert directive["Dimensions"] == [["Family"]]
    assert directive["Metrics"] == [{"Name": "LiveInterviews", "Unit": "Count"}]
    assert event["Family"] == "reep-api"
    assert event["LiveInterviews"] == 7


def test_the_line_carries_nothing_but_the_count_and_the_family():
    event = json.loads(m.emf_line("reep-api", 3))
    assert set(event) == {"_aws", "Family", "LiveInterviews"}


def test_a_negative_count_is_reported_as_zero():
    # The limiter guards its own counter, but the policy must never see a
    # negative average: it would read as spare capacity that does not exist.
    assert json.loads(m.emf_line("reep-api", -2))["LiveInterviews"] == 0


def test_the_loop_reports_at_once_and_then_every_interval():
    live = iter([0, 4, 9])
    out = io.StringIO()

    async def run():
        task = asyncio.create_task(
            m.publish_forever("reep-api", lambda: next(live), out=out, interval=0.01)
        )
        await asyncio.sleep(0.025)
        task.cancel()

    asyncio.run(run())
    counts = [json.loads(line)["LiveInterviews"] for line in out.getvalue().splitlines()]
    assert counts[:3] == [0, 4, 9]


def test_a_failing_read_costs_a_minute_and_not_the_loop():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("limiter unavailable")
        return 2

    out = io.StringIO()

    async def run():
        task = asyncio.create_task(m.publish_forever("reep-api", flaky, out=out, interval=0.01))
        await asyncio.sleep(0.025)
        task.cancel()

    asyncio.run(run())
    assert json.loads(out.getvalue().splitlines()[0])["LiveInterviews"] == 2


def test_the_scaling_policy_reads_the_names_this_module_writes():
    """The stack does not import the api (two Python environments), so the
    agreement is checked as text: a rename on either side fails here rather
    than leaving a policy watching a metric nobody publishes."""
    stack = STACK.read_text(encoding="utf-8")
    assert f'INTERVIEW_METRIC_NAMESPACE = "{m.NAMESPACE}"' in stack
    assert f'INTERVIEW_METRIC_NAME = "{m.METRIC}"' in stack
    assert f'INTERVIEW_METRIC_DIMENSION = "{m.DIMENSION}"' in stack
