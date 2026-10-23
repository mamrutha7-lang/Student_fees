"""The two job boards the daily job agent reads, and the keys that must never
leave with what they send (2026-10-23).

No database and no network: every call goes through `httpx.MockTransport`.
What each block holds down:

1. PARSING. Delete the two parse tests and the first thing to change on either
   board's side (an HTML tag in a title, a seven-digit fraction on a date, an
   advert with no company) becomes a posting a student reads as garbage.
2. WHAT GOES OUT. The request carries a phrase, a location, paging and the key,
   and nothing else, so rule 1 holds by the shape of the call.
3. A FAILURE NAMES NO URL. Both boards put a key in the URL, and an httpx error
   prints the URL.
4. SENTRY NEVER SEES A KEY. The SDK's own httpx breadcrumbs and spans carry the
   path and the raw query; `app/telemetry_scrub.py` is what keeps them out, and
   the last test proves it against the real SDK with a capturing transport.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app import job_sources
from app import observability as obs
from app import telemetry_scrub as scrub
from app.config import settings
from app.job_sources import (
    ADZUNA,
    JOOBLE,
    AdzunaSource,
    JoobleSource,
    SourceError,
    parse_posted,
    plain_text,
    sources_from_settings,
)

APP = Path(__file__).resolve().parents[1] / "app"

ADZUNA_ID = "adz-id-5f1c"
ADZUNA_KEY = "adz-key-should-never-travel-9d2e"
JOOBLE_KEY = "jooble-key-should-never-travel-7a1b"

ADZUNA_REPLY = {
    "results": [
        {
            "id": "4911",
            "title": "<strong>HR</strong> Executive &amp; Recruiter",
            "company": {"display_name": "Acme People"},
            "location": {"display_name": "Bengaluru, Karnataka"},
            "redirect_url": "https://www.adzuna.in/land/ad/4911?se=x",
            "description": "<p>Freshers welcome. Excel a plus.</p>",
            "created": "2026-10-20T09:30:00Z",
        },
        # Not https: dropped, because the Apply button would open it as-is.
        {
            "id": "4912",
            "title": "HR Intern",
            "company": {"display_name": "Acme"},
            "redirect_url": "http://insecure.example/ad",
        },
        # No company: dropped; `jobs.company` is NOT NULL and "unknown" is a guess.
        {"id": "4913", "title": "HR Trainee", "company": {}, "redirect_url": "https://a.example/1"},
    ]
}

JOOBLE_REPLY = {
    "totalCount": 2,
    "jobs": [
        {
            "id": 77001,
            "title": "Supply Chain Trainee",
            "company": "Movers Ltd",
            "location": "Pune",
            "snippet": "Graduate trainee&nbsp;programme",
            "link": "https://jooble.org/desc/77001",
            "updated": "2026-10-19T00:00:00.0000000",
        },
        {"id": 77002, "title": "", "company": "Movers", "link": "https://jooble.org/desc/77002"},
    ],
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_adzuna_adverts_are_parsed_and_the_unusable_ones_dropped() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ADZUNA_REPLY)

    jobs = AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(handler)).search("HR executive", internship=False)

    assert [j.external_id for j in jobs] == ["4911"]
    job = jobs[0]
    assert job.provider == ADZUNA
    assert job.title == "HR Executive & Recruiter"
    assert job.company == "Acme People"
    assert job.location == "Bengaluru, Karnataka"
    assert job.apply_url == "https://www.adzuna.in/land/ad/4911?se=x"
    assert job.description == "Freshers welcome. Excel a plus."
    assert job.posted_on is not None and job.posted_on.isoformat() == "2026-10-20T09:30:00+00:00"

    (request,) = seen
    assert request.method == "GET"
    assert request.url.host == "api.adzuna.com"
    assert request.url.path == "/v1/api/jobs/in/search/1"
    params = dict(request.url.params)
    assert set(params) == {
        "app_id", "app_key", "what", "what_or", "results_per_page", "max_days_old", "sort_by",
    }, "a new parameter on the call is a new thing leaving the machine; decide it here"
    assert params["what"] == "HR executive"
    assert "fresher" in params["what_or"]


def test_an_internship_search_asks_for_interns() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(handler)).search("HR executive", internship=True)
    assert "internship" in dict(seen[0].url.params)["what_or"]


def test_jooble_adverts_are_parsed_and_the_body_carries_only_the_search() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=JOOBLE_REPLY)

    jobs = JoobleSource(JOOBLE_KEY, client=_client(handler)).search("supply chain", internship=False)

    assert [j.external_id for j in jobs] == ["77001"]
    job = jobs[0]
    assert job.provider == JOOBLE
    assert job.description == "Graduate trainee programme"
    assert job.posted_on is not None and job.posted_on.isoformat() == "2026-10-19T00:00:00+00:00"

    (request,) = seen
    assert request.method == "POST"
    assert request.url.host == "jooble.org"
    assert request.url.path == f"/api/{JOOBLE_KEY}"
    body = json.loads(request.content)
    assert set(body) == {"keywords", "location", "page"}
    assert body["keywords"] == "supply chain fresher"
    assert body["location"] == "India"


def test_calls_to_one_board_are_spaced_inside_its_rate_limit(monkeypatch) -> None:
    """Adzuna's free tier refuses the 26th call in a minute; a morning makes
    about thirty. The first call waits for nothing, every later one for the
    rest of the interval."""
    waits: list[float] = []
    monkeypatch.setattr(job_sources.time, "sleep", waits.append)
    handler = lambda request: httpx.Response(200, json={"results": [], "jobs": []})  # noqa: E731
    adzuna = AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(handler))
    for _ in range(3):
        adzuna.search("HR", internship=False)
    assert len(waits) == 2
    assert all(0 < w <= job_sources.ADZUNA_MIN_INTERVAL_SECONDS for w in waits)
    assert job_sources.ADZUNA_MIN_INTERVAL_SECONDS * 25 >= 60, "25 calls must not fit in a minute"


def test_dates_and_text_are_read_defensively() -> None:
    assert parse_posted("2026-10-20T10:00:00").isoformat() == "2026-10-20T10:00:00+00:00"
    assert parse_posted("not a date") is None
    assert parse_posted(None) is None
    assert plain_text("<b>A</b>\n\n  B &amp; C") == "A B & C"
    assert plain_text(None) == ""


# --------------------------------------------------------------------------- #
# Failures name no URL
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "stop"),
    [(401, True), (403, True), (429, True), (500, False), (503, False)],
)
def test_a_refusal_is_a_source_error_with_no_key_in_it(status: int, stop: bool) -> None:
    for source in (
        AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(lambda r: httpx.Response(status))),
        JoobleSource(JOOBLE_KEY, client=_client(lambda r: httpx.Response(status))),
    ):
        with pytest.raises(SourceError) as caught:
            source.search("HR executive", internship=False)
        message = str(caught.value)
        assert caught.value.stop is stop
        assert ADZUNA_KEY not in message and JOOBLE_KEY not in message and "http" not in message
        assert caught.value.__cause__ is None


def test_an_unreachable_board_stops_and_says_so_without_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    with pytest.raises(SourceError) as caught:
        JoobleSource(JOOBLE_KEY, client=_client(handler)).search("HR", internship=False)
    assert caught.value.stop is True
    assert JOOBLE_KEY not in str(caught.value)
    # `from None`: the httpx error, whose message is the URL, is not chained
    # onto the exception Sentry would capture.
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_reply_that_is_not_json_is_a_source_error() -> None:
    source = AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(SourceError):
        source.search("HR", internship=False)


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


def test_the_agent_runs_exactly_when_a_board_has_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adzuna_app_id", "")
    monkeypatch.setattr(settings, "adzuna_app_key", "")
    monkeypatch.setattr(settings, "jooble_api_key", "")
    monkeypatch.setattr(settings, "job_scout_enabled", "")
    assert sources_from_settings() == [] and not settings.job_scout_active

    # Half of Adzuna's pair is no key.
    monkeypatch.setattr(settings, "adzuna_app_id", ADZUNA_ID)
    assert sources_from_settings() == []

    monkeypatch.setattr(settings, "adzuna_app_key", ADZUNA_KEY)
    monkeypatch.setattr(settings, "jooble_api_key", JOOBLE_KEY)
    assert [s.name for s in sources_from_settings()] == [ADZUNA, JOOBLE]

    # The incident switch stops it with the keys still in place.
    monkeypatch.setattr(settings, "job_scout_enabled", "false")
    assert sources_from_settings() == [] and not settings.job_scout_active
    monkeypatch.setattr(settings, "job_scout_enabled", "FALSE ")
    assert sources_from_settings() == []
    # Anything else derives the answer from the keys.
    monkeypatch.setattr(settings, "job_scout_enabled", "flase")
    assert settings.job_scout_active


# --------------------------------------------------------------------------- #
# Rule 1, by shape
# --------------------------------------------------------------------------- #


def test_the_job_agent_imports_nothing_about_a_student() -> None:
    """The phrases come from the catalogue. A model import here is the first
    step towards a search that carries somebody's record, so the two modules
    are read and their imports listed."""
    forbidden = {"app.models.user", "app.models.student_profile", "app.models.registration"}
    for name in ("job_sources.py", "job_scout.py"):
        tree = ast.parse((APP / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = ("app." + node.module.lstrip(".")) if node.level else node.module
                assert module not in forbidden, f"app/{name} imports {module}"
    sources = ast.parse((APP / "job_sources.py").read_text(encoding="utf-8"))
    for node in ast.walk(sources):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "models" not in node.module, "app/job_sources.py must import no model at all"


# --------------------------------------------------------------------------- #
# Sentry never sees a key
# --------------------------------------------------------------------------- #


def test_the_scrubber_blanks_a_key_in_a_path_and_in_an_outbound_query() -> None:
    assert scrub.filter_url(f"https://jooble.org/api/{JOOBLE_KEY}") == "https://jooble.org/api/[Filtered]"
    crumb = scrub.scrub_breadcrumb(
        {
            "category": "httplib",
            "data": {
                "url": f"https://jooble.org/api/{JOOBLE_KEY}",
                "http.query": f"app_id={ADZUNA_ID}&app_key={ADZUNA_KEY}&what=HR",
            },
        }
    )
    blob = json.dumps(crumb)
    assert JOOBLE_KEY not in blob and ADZUNA_KEY not in blob and ADZUNA_ID not in blob
    event = scrub.scrub_transaction(
        {
            "spans": [
                {
                    "op": "http.client",
                    "description": f"POST https://jooble.org/api/{JOOBLE_KEY}",
                    "data": {"url": f"https://jooble.org/api/{JOOBLE_KEY}", "http.query": f"app_key={ADZUNA_KEY}"},
                },
                {"op": "db", "description": "SELECT 1"},
            ]
        }
    )
    blob = json.dumps(event)
    assert JOOBLE_KEY not in blob and ADZUNA_KEY not in blob
    assert "SELECT 1" in blob


class _Capturing(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.envelopes: list[Any] = []

    def capture_envelope(self, envelope: Any) -> None:
        self.envelopes.append(envelope)

    def flush(self, timeout: float, callback: Any = None) -> None:
        if callback is not None:
            callback(0, timeout)

    def kill(self) -> None:
        return None

    def blob(self) -> str:
        return json.dumps(
            [item.payload.json for envelope in self.envelopes for item in envelope.items if item.payload.json],
            default=str,
        )


def test_a_morning_reported_to_sentry_carries_neither_key(caplog) -> None:
    """The real SDK, its real httpx integration, a capturing transport: one job
    transaction with a call to each board and an ERROR log after them, so the
    breadcrumbs, the spans and the event are all on the wire."""
    # INFO on, as the job turns it on: httpx's own request line is one of the
    # carriers, and with the root logger at WARNING it is never emitted, so
    # the test would pass for the wrong reason.
    caplog.set_level(logging.INFO)
    obs._reset_for_tests()
    transport = _Capturing()
    try:
        assert obs.init_sentry(obs.SERVICE_JOBS, "https://abc123@o0.ingest.sentry.io/0", transport=transport)
        handler = lambda request: httpx.Response(200, json={"results": [], "jobs": []})  # noqa: E731
        with obs.job_run("jobs.scout"):
            AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(handler)).search("HR", internship=False)
            JoobleSource(JOOBLE_KEY, client=_client(handler)).search("HR", internship=False)
            logging.getLogger("reep.job_scout").error("Job agent: a board failed this morning.")
        sentry_sdk.flush()
        blob = transport.blob()
        assert "jobs.scout" in blob, "nothing was captured, so this test proves nothing"
        assert "jooble.org" in blob, "the outbound call left no trace, so this test proves nothing"
        for secret in (ADZUNA_ID, ADZUNA_KEY, JOOBLE_KEY):
            assert secret not in blob, f"{secret} reached Sentry"
    finally:
        obs._reset_for_tests()


def test_the_request_line_httpx_logs_carries_no_key(caplog) -> None:
    """The line the job writes to CloudWatch for every call, read back."""
    caplog.set_level(logging.INFO, logger="httpx")
    handler = lambda request: httpx.Response(200, json={"results": [], "jobs": []})  # noqa: E731
    AdzunaSource(ADZUNA_ID, ADZUNA_KEY, client=_client(handler)).search("HR", internship=False)
    JoobleSource(JOOBLE_KEY, client=_client(handler)).search("HR", internship=False)
    lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert len(lines) == 2, "httpx logged nothing, so this test proves nothing"
    assert "api.adzuna.com/v1/api/jobs/in/search/1" in lines[0] and "200" in lines[0]
    assert "jooble.org/api/[Filtered]" in lines[1]
    for secret in (ADZUNA_ID, ADZUNA_KEY, JOOBLE_KEY):
        assert all(secret not in line for line in lines), f"{secret} is in the request log line"


def test_the_module_keeps_its_provider_names_in_one_place() -> None:
    assert set(job_sources.PROVIDER_NAMES) == set(job_sources.PROVIDERS)
