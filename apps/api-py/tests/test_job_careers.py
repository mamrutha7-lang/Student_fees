"""The companies' own job feeds the daily job agent reads with no key and no
sign-up: Greenhouse, Lever and SmartRecruiters (app/job_sources.py,
`CareersSource`, 2026-10-23).

No database and no network: every call goes through `httpx.MockTransport`.
What each block holds down:

1. INDIA, ENTRY LEVEL, ONE TOPIC. A feed cannot be searched, so every rule a
   search used to apply is applied here: a role outside India, a senior or
   levelled title, or one about something else must not reach a stream.
2. A DESCRIPTION IS READ ONLY FOR A MATCH, AND ONCE. Reading every posting in
   full would be a thousand calls a morning to one company.
3. ONE COMPANY FAILING COSTS ONLY THAT COMPANY, and every company failing is
   the board failing, which the morning's exit code reports.
4. ONLY A FEED READ TO ITS END CAN SAY A ROLE IS GONE. app/job_scout.py closes
   a posting its company stopped listing; a feed cut short or not read at all
   must close nothing (tests/test_job_scout.py holds the closing itself).
5. THE SWITCH IS A FLAG, because there is no key whose presence could be one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app import job_scout, job_sources
from app.config import settings
from app.job_sources import (
    CAREERS,
    COMPANY_BOARDS,
    GREENHOUSE,
    LEVER,
    MAX_EXTERNAL_ID,
    SMARTRECRUITERS,
    CareersSource,
    CompanyBoard,
    SourceError,
    sources_from_settings,
    topic_pattern,
)

NOW = datetime.now(timezone.utc)
RECENT = NOW - timedelta(days=3)
LONG_AGO = NOW - timedelta(days=job_sources.CAREERS_MAX_POSTING_AGE_DAYS + 30)

BOSCH = CompanyBoard(SMARTRECRUITERS, "BoschGroup", "Bosch")
ACME = CompanyBoard(LEVER, "acme", "Acme")
BETA = CompanyBoard(GREENHOUSE, "beta", "Beta")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _sr(pid: str, name: str, level: str, *, function: str = "", country: str = "in", when: datetime = RECENT):
    return {
        "id": pid,
        "name": name,
        "company": {"identifier": "BoschGroup", "name": "Bosch Group"},
        "releasedDate": _iso(when),
        "location": {"city": "Bengaluru", "country": country, "fullLocation": "Bengaluru, KA, India"},
        "function": {"id": function.lower(), "label": function},
        "experienceLevel": {"id": level, "label": level},
        "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
    }


SMARTRECRUITERS_ROWS = [
    _sr("101", "Graduate Trainee - Procurement", "entry_level", function="Purchasing"),
    _sr("102", "Area Sales Manager / Power Tools", "associate", function="Sales"),
    _sr("103", "HR Intern (Bangalore)", "internship", function="Human Resources"),
    _sr("104", "Staff Analyst - Procurement", "entry_level", function="Purchasing"),
    _sr("105", "Buyer Trainee", "entry_level", function="Purchasing", country="de"),
]
SMARTRECRUITERS_DETAIL = {
    "id": "101",
    "jobAd": {
        "sections": {
            "companyDescription": {"title": "Company", "text": "<p>We make things.</p>"},
            "jobDescription": {"title": "Job", "text": "<p>Raise purchase orders &amp; track vendors.</p>"},
            "qualifications": {"title": "Qualifications", "text": "<ul><li>MBA, freshers welcome</li></ul>"},
        }
    },
}

LEVER_ROWS = [
    {
        "id": "a1",
        "text": "HR Executive",
        "country": "IN",
        "categories": {"commitment": "Full time", "team": "People", "location": "Pune"},
        "createdAt": _ms(RECENT),
        "hostedUrl": "https://jobs.lever.co/acme/a1",
        "descriptionPlain": "Freshers welcome.",
        "lists": [{"text": "You bring", "content": "<li>Excel</li><li>Good English</li>"}],
        "additionalPlain": "",
    },
    {
        "id": "a2",
        "text": "Talent Acquisition Intern",
        "country": "IN",
        "categories": {"commitment": "Intern", "team": "People", "location": "Noida"},
        "createdAt": _ms(RECENT),
        "hostedUrl": "https://jobs.lever.co/acme/a2",
        "descriptionPlain": "Six months.",
    },
    # Outside India: never read as a posting at all.
    {
        "id": "a3",
        "text": "HR Executive",
        "country": "US",
        "categories": {"commitment": "Full time", "location": "New York"},
        "createdAt": _ms(RECENT),
        "hostedUrl": "https://jobs.lever.co/acme/a3",
    },
    # A standing "always hiring" advert: listed, so it is not "gone", but not
    # an opening anybody should be told about this morning.
    {
        "id": "a4",
        "text": "HR Associate",
        "country": "IN",
        "categories": {"commitment": "Full time", "team": "People", "location": "Pune"},
        "createdAt": _ms(LONG_AGO),
        "hostedUrl": "https://jobs.lever.co/acme/a4",
    },
]

GREENHOUSE_ROWS = {
    "jobs": [
        {
            "id": 9001,
            "title": "Business Analyst",
            "company_name": "Beta Pvt Ltd",
            "location": {"name": "Bengaluru, India"},
            "absolute_url": "https://job-boards.greenhouse.io/beta/jobs/9001",
            "first_published": "2026-10-20T03:49:28-04:00",
            "updated_at": "2026-10-21T03:49:28-04:00",
        },
        {
            "id": 9002,
            "title": "Enterprise Account Executive, Mumbai",
            "company_name": "Beta Pvt Ltd",
            "location": {"name": "Mumbai, India"},
            "absolute_url": "https://job-boards.greenhouse.io/beta/jobs/9002",
            "first_published": "2026-10-20T03:49:28-04:00",
        },
        {
            "id": 9003,
            "title": "Business Analyst",
            "company_name": "Beta Pvt Ltd",
            "location": {"name": "London, UK"},
            "absolute_url": "https://job-boards.greenhouse.io/beta/jobs/9003",
            "first_published": "2026-10-20T03:49:28-04:00",
        },
    ]
}
GREENHOUSE_DETAIL = {"id": 9001, "content": "&lt;p&gt;Analyse sales &amp;amp; build dashboards.&lt;/p&gt;"}


class Feeds:
    """The three feeds behind one MockTransport, recording every request."""

    def __init__(self, **override: Any) -> None:
        self.requests: list[httpx.Request] = []
        self.override = override

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        for key, answer in self.override.items():
            if key in f"{host}{path}":
                return answer(request) if callable(answer) else answer
        if host == "api.smartrecruiters.com":
            if path.endswith("/postings"):
                offset = int(request.url.params.get("offset", "0"))
                limit = int(request.url.params.get("limit", "100"))
                page = SMARTRECRUITERS_ROWS[offset : offset + limit]
                return httpx.Response(200, json={"totalFound": len(SMARTRECRUITERS_ROWS), "content": page})
            return httpx.Response(200, json=SMARTRECRUITERS_DETAIL)
        if host == "api.lever.co":
            return httpx.Response(200, json=LEVER_ROWS)
        if host == "boards-api.greenhouse.io":
            if path.endswith("/jobs"):
                return httpx.Response(200, json=GREENHOUSE_ROWS)
            return httpx.Response(200, json=GREENHOUSE_DETAIL)
        return httpx.Response(404)

    def paths(self, needle: str) -> list[str]:
        return [str(r.url) for r in self.requests if needle in str(r.url)]


def _source(feeds: Feeds, boards: tuple[CompanyBoard, ...] = (BOSCH, ACME, BETA)) -> CareersSource:
    source = CareersSource(boards, client=httpx.Client(transport=httpx.MockTransport(feeds)))
    source.min_interval = 0.0  # no waiting between calls in a test
    return source


def _titles(found) -> list[str]:
    return [f.title for f in found]


# --------------------------------------------------------------------------- #
# 1. India, entry level, one topic
# --------------------------------------------------------------------------- #


def test_each_feed_gives_its_entry_roles_in_india_under_their_streams_topic() -> None:
    source = _source(Feeds())

    supply = source.search("supply chain", internship=False)
    assert _titles(supply) == ["Graduate Trainee - Procurement"]
    bosch = supply[0]
    assert bosch.provider == CAREERS
    assert bosch.external_id == "smartrecruiters/BoschGroup/101"
    assert bosch.company == "Bosch Group"
    assert bosch.apply_url == "https://jobs.smartrecruiters.com/BoschGroup/101"
    assert bosch.location == "Bengaluru, KA, India"
    assert bosch.posted_on is not None and abs((bosch.posted_on - RECENT).total_seconds()) < 1

    hr = source.search("hr executive", internship=False)
    assert _titles(hr) == ["HR Executive"]
    assert hr[0].external_id == "lever/acme/a1" and hr[0].company == "Acme"
    assert hr[0].apply_url == "https://jobs.lever.co/acme/a1"
    # Lever's list carries the whole advert: its requirements are judged too.
    assert "Freshers welcome." in hr[0].description and "Good English" in hr[0].description

    analyst = source.search("business analyst", internship=False)
    assert _titles(analyst) == ["Business Analyst"]
    assert analyst[0].external_id == "greenhouse/beta/9001" and analyst[0].company == "Beta Pvt Ltd"
    assert analyst[0].location == "Bengaluru, India"


def test_an_internship_search_gives_the_internships_and_a_job_search_does_not() -> None:
    source = _source(Feeds())
    assert sorted(_titles(source.search("hr recruiter", internship=True))) == ["Talent Acquisition Intern"]
    assert sorted(_titles(source.search("hr executive", internship=True))) == ["HR Intern (Bangalore)"]
    assert "Talent Acquisition Intern" not in _titles(source.search("hr recruiter", internship=False))


def test_a_senior_or_levelled_title_on_a_feed_is_not_a_freshers_role() -> None:
    source = _source(Feeds())
    everything = [
        title
        for phrase in job_sources.CAREERS_TOPICS
        for internship in (False, True)
        for title in _titles(source.search(phrase, internship=internship))
    ]
    # SmartRecruiters' own level says "associate"; "Staff" is a seniority; a
    # software company's Account Executive carries a quota.
    for title in ("Area Sales Manager / Power Tools", "Staff Analyst - Procurement", "Enterprise Account Executive, Mumbai"):
        assert title not in everything


def test_a_role_outside_india_is_never_read() -> None:
    source = _source(Feeds())
    ids = {f.external_id for phrase in job_sources.CAREERS_TOPICS for f in source.search(phrase, internship=False)}
    assert "lever/acme/a3" not in ids
    assert "greenhouse/beta/9003" not in ids
    assert "smartrecruiters/BoschGroup/105" not in ids


def test_a_standing_advert_is_still_listed_but_never_searched() -> None:
    source = _source(Feeds())
    assert "HR Associate" not in _titles(source.search("hr executive", internship=False))
    read, listed = source.still_listed()
    assert "lever/acme/a4" in listed, "a role the company still lists must not be closed as gone"


@pytest.mark.parametrize(
    ("phrase", "title", "matches"),
    [
        ("hr executive", "HR Generalist", True),
        ("hr executive", "People Operations Associate", True),
        ("hr recruiter", "Talent Acquisition Intern", True),
        ("hr recruiter", "Talent Account Executive", False),
        ("finance executive", "Accounts Payable Executive", True),
        ("finance executive", "Account Executive", False),
        ("supply chain", "Demand Planning Analyst", True),
        ("logistics executive", "Warehouse Operations Trainee", True),
        ("digital marketing", "Performance Marketing Associate", True),
        ("business analyst", "Business Analyst", True),
        ("data analyst", "MIS Executive", True),
        ("sales and marketing", "Inside Sales Associate", True),
        # A stream the office adds later is searched by its own name: every
        # word of it, "management" and the like aside.
        ("Hospital Administration", "Hospital Trainee", False),
        ("Hospital Administration", "Hospital Administration Trainee", True),
        ("Retail Management", "Retail Store Trainee", True),
    ],
)
def test_a_streams_phrase_is_a_topic_on_a_feed(phrase: str, title: str, matches: bool) -> None:
    assert bool(topic_pattern(phrase).search(title)) is matches


def test_every_stream_phrase_has_a_topic_on_the_feeds() -> None:
    """A phrase added to job_scout.SEARCH_TERMS with no topic here would be
    searched word for word on titles, and find next to nothing."""
    for code, phrases in job_scout.SEARCH_TERMS.items():
        for phrase in phrases:
            assert phrase.lower() in job_sources.CAREERS_TOPICS, f"{code}: {phrase!r} has no topic"


# --------------------------------------------------------------------------- #
# 2. A description is read only for a match, and once
# --------------------------------------------------------------------------- #


def test_a_description_is_read_once_and_only_for_a_posting_a_search_matched() -> None:
    feeds = Feeds()
    source = _source(feeds)
    first = source.search("supply chain", internship=False)
    again = source.search("supply chain", internship=False)

    assert first[0].description == "Raise purchase orders & track vendors. MBA, freshers welcome"
    assert again[0].description == first[0].description
    # The company boilerplate is not the role.
    assert "We make things" not in first[0].description
    assert feeds.paths("/postings/101") == ["https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/101"]
    # Nothing that did not match was read in full.
    assert not feeds.paths("/postings/102") and not feeds.paths("/postings/104")

    analyst = source.search("business analyst", internship=False)
    assert analyst[0].description == "Analyse sales & build dashboards."
    assert len(feeds.paths("/jobs/9001")) == 1 and not feeds.paths("/jobs/9002")
    # Each feed's list was read exactly once, whatever was searched.
    assert len(feeds.paths("api.lever.co")) == 1


def test_a_description_that_cannot_be_read_leaves_the_posting_without_one() -> None:
    feeds = Feeds(**{"/postings/101": httpx.Response(500)})
    found = _source(feeds).search("supply chain", internship=False)
    assert _titles(found) == ["Graduate Trainee - Procurement"]
    assert found[0].description == ""


# --------------------------------------------------------------------------- #
# 3. One company failing costs only that company
# --------------------------------------------------------------------------- #


def test_one_company_failing_costs_only_that_company(caplog) -> None:
    feeds = Feeds(
        **{
            "api.lever.co": httpx.Response(404),
            "boards-api.greenhouse.io/v1/boards/beta/jobs": httpx.Response(200, text="<html>moved</html>"),
        }
    )
    source = _source(feeds)
    assert _titles(source.search("supply chain", internship=False)) == ["Graduate Trainee - Procurement"]
    assert source.failed == ["Acme", "Beta"]
    read, _ = source.still_listed()
    assert read == {"smartrecruiters/BoschGroup"}
    assert "Acme's careers page answered 404." in caplog.text


def test_a_feed_of_an_unexpected_shape_costs_one_row_or_one_company() -> None:
    rows = [*LEVER_ROWS[:1], "not a posting", {"id": "x", "text": "HR Executive", "categories": "oops", "country": "IN"}]
    feeds = Feeds(**{"api.lever.co": httpx.Response(200, json=rows), "boards-api.greenhouse.io/v1/boards/beta/jobs": httpx.Response(200, json={"jobs": "none"})})
    source = _source(feeds)
    assert _titles(source.search("hr executive", internship=False)) == ["HR Executive"]
    assert source.failed == ["Beta"]


def test_every_company_failing_is_the_board_failing() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    source = _source(Feeds(**{"api.": unreachable, "boards-api.": unreachable}))
    with pytest.raises(SourceError) as caught:
        source.search("hr executive", internship=False)
    assert caught.value.stop and caught.value.provider == CAREERS
    assert source.still_listed() is None


def test_a_failure_names_the_company_and_never_the_url() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SourceError) as caught:
        _source(Feeds(**{"api.lever.co": unreachable}), boards=(ACME,)).search("hr executive", internship=False)
    assert "http" not in str(caught.value)


# --------------------------------------------------------------------------- #
# 4. Only a feed read to its end can say a role is gone
# --------------------------------------------------------------------------- #


def test_a_feed_cut_short_by_the_page_limit_closes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(job_sources, "SMARTRECRUITERS_PAGE", 2)
    monkeypatch.setattr(job_sources, "SMARTRECRUITERS_MAX_PAGES", 1)
    source = _source(Feeds(), boards=(BOSCH, ACME))
    source.search("supply chain", internship=False)
    read, listed = source.still_listed()
    assert read == {"lever/acme"}, "a SmartRecruiters feed read to page one of three cannot say a role is gone"
    assert "smartrecruiters/BoschGroup/101" in listed


def test_nothing_is_listed_before_a_feed_was_read() -> None:
    assert _source(Feeds()).still_listed() is None


def test_a_feed_posting_id_names_its_company_and_fits_the_ref() -> None:
    source = _source(Feeds())
    ids = {f.external_id for p in job_sources.CAREERS_TOPICS for f in source.search(p, internship=False)}
    ids |= {f.external_id for p in job_sources.CAREERS_TOPICS for f in source.search(p, internship=True)}
    assert ids
    for external_id in ids:
        board, _, raw = external_id.rpartition("/")
        assert board in {"smartrecruiters/BoschGroup", "lever/acme", "greenhouse/beta"} and raw
        assert ":" not in external_id and len(external_id) <= MAX_EXTERNAL_ID


# --------------------------------------------------------------------------- #
# 5. The switch, the list, and what goes out
# --------------------------------------------------------------------------- #


def test_the_company_feeds_are_switched_on_by_a_flag_not_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adzuna_app_id", "")
    monkeypatch.setattr(settings, "adzuna_app_key", "")
    monkeypatch.setattr(settings, "jooble_api_key", "")
    monkeypatch.setattr(settings, "job_scout_enabled", "")
    monkeypatch.setattr(settings, "job_scout_company_boards", False)
    assert sources_from_settings() == [] and not settings.job_scout_active

    monkeypatch.setattr(settings, "job_scout_company_boards", True)
    assert settings.job_scout_active
    assert [s.name for s in sources_from_settings()] == [CAREERS]
    assert {s["key"]: s["configured"] for s in job_scout.status_sources()} == {
        "adzuna": False,
        "jooble": False,
        "careers": True,
    }

    # The incident switch stops these too.
    monkeypatch.setattr(settings, "job_scout_enabled", "false")
    assert sources_from_settings() == [] and not settings.job_scout_active


def test_a_blank_switch_is_off_rather_than_a_crash_at_boot(monkeypatch) -> None:
    monkeypatch.setenv("JOB_SCOUT_COMPANY_BOARDS", "")
    assert type(settings)().job_scout_company_boards is False


def test_the_company_list_names_each_feed_once_on_a_system_it_can_read() -> None:
    seen: set[tuple[str, str]] = set()
    for board in COMPANY_BOARDS:
        assert board.ats in {GREENHOUSE, LEVER, SMARTRECRUITERS}, board
        assert board.token and "/" not in board.token and ":" not in board.token, board
        assert board.company.strip(), board
        assert (board.ats, board.token.lower()) not in seen, f"{board.token} is listed twice"
        seen.add((board.ats, board.token.lower()))


def test_the_feeds_ask_three_public_hosts_for_jobs_and_send_nothing_else() -> None:
    """Rule 1 by shape: GETs with paging and a country, to three hosts, no
    body, no cookie, no header naming anybody."""
    feeds = Feeds()
    source = _source(feeds)
    for phrase in job_sources.CAREERS_TOPICS:
        source.search(phrase, internship=False)
    assert {r.url.host for r in feeds.requests} == {"api.smartrecruiters.com", "api.lever.co", "boards-api.greenhouse.io"}
    for request in feeds.requests:
        assert request.method == "GET" and request.content == b""
        assert set(request.url.params) <= {"country", "limit", "offset", "mode"}
        assert "cookie" not in request.headers and "authorization" not in request.headers
