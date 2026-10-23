"""The job boards the daily job agent reads (app/job_scout.py): Adzuna and
Jooble, through their OFFICIAL search APIs (2026-10-23), and the public job
feeds companies publish from their own careers pages (2026-10-23, later).

Official APIs and nothing else, on purpose. Scraping LinkedIn or Naukri is
against both sites' terms, is blocked within days, and would make the morning
run a thing that quietly stops working. Both APIs here are free, keyed, and
documented to be republished: Adzuna's terms ask that every advert shown carry
"Jobs by Adzuna", which the three jobs screens do (`source` on each row).

THE COMPANY FEEDS NEED NO KEY AND NO SIGN-UP, and that is why they exist.
Greenhouse, Lever and SmartRecruiters each publish a documented, public,
read-only API of a company's OPEN postings, built so the company's jobs can be
listed on other sites. `COMPANY_BOARDS` names the companies read; each board
lists only roles that are open today, with a link to the company's own page.
They carry far fewer jobs than an aggregator (one company each, and mostly
not fresher roles), so the morning keeps the strict rules below: India only, a
title or level that says entry or intern, and a topic matched per stream.

RULE 1 BY SHAPE. A search takes a PHRASE built from the catalogue ("HR
executive") and a kind (job or internship), and nothing else; there is no
parameter a student's name, USN or record could ride out on, and this module
imports no model at all. What comes back is public: the employer's own advert.

A FAILURE NEVER CARRIES THE URL. Jooble takes its key in the URL path and
Adzuna in the query string, and an `httpx.HTTPStatusError` prints the whole
URL in its message, so every failure is re-raised as `SourceError` naming the
provider and the status only, `from None` so the original is not chained onto
the Sentry event. The SDK's own breadcrumbs and spans for these calls are
scrubbed in app/telemetry_scrub.py (`_SECRET_PATH_SEGMENTS`, `http.query`).
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx

from .config import settings
from .telemetry_scrub import redact_secret_paths


class _RedactOutboundUrls(logging.Filter):
    """httpx logs every request at INFO as `HTTP Request: GET <url> "HTTP/1.1
    200 OK"`, and the URL is the whole URL: Adzuna's key pair in the query and
    Jooble's key in the path. The job turns INFO on (it must, for its own
    summary line), so without this both keys would be written to CloudWatch on
    every morning's run, and into a Sentry breadcrumb on the next event. The
    line is kept, because which board answered what is worth knowing; only the
    query goes, and the path key is blanked.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and any(isinstance(a, httpx.URL) for a in record.args):
            record.args = tuple(
                redact_secret_paths(str(a.copy_with(query=None))) if isinstance(a, httpx.URL) else a
                for a in record.args
            )
        return True


log = logging.getLogger(__name__)

_URL_FILTER = _RedactOutboundUrls()
if not any(isinstance(f, _RedactOutboundUrls) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_URL_FILTER)

ADZUNA = "adzuna"
JOOBLE = "jooble"
CAREERS = "careers"
PROVIDERS = (ADZUNA, JOOBLE, CAREERS)

#: What each provider is called on a screen. Adzuna's wording is its own
#: terms' ("Jobs by Adzuna"); the client prints it, this is the provider name.
PROVIDER_NAMES = {ADZUNA: "Adzuna", JOOBLE: "Jooble", CAREERS: "Company careers pages"}

ADZUNA_SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"
JOOBLE_SEARCH_URL = "https://jooble.org/api/"

#: Seconds, the google_auth JWKS fetch's figure. A board that does not answer
#: in ten seconds is not going to answer this morning.
HTTP_TIMEOUT_SECONDS = 10.0
#: Adzuna's ceiling per page; one page per phrase keeps the run inside the
#: free tier's 250 calls a day with room for every college a deployment adds.
RESULTS_PER_PAGE = 50
#: An advert older than this when it is found is not news to anybody.
MAX_DAYS_OLD = 14
#: All of India, the owner's choice. Adzuna's country is in the URL (`/in/`);
#: Jooble takes a place name.
JOOBLE_LOCATION = "India"

#: The words that turn a phrase into a FRESHER search. Adzuna's `what_or` is
#: "any of these", ANDed with `what`; Jooble has one keyword string, so the
#: first word is appended there.
FRESHER_WORDS = ("fresher", "freshers", "trainee", "graduate", "entry")
INTERNSHIP_WORDS = ("intern", "internship")

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
#: Control characters other than ordinary whitespace, C1 included. Postgres
#: refuses NUL in a text column, and the morning is ONE transaction, so a
#: single advert carrying "\x00" used to fail the commit and lose every
#: posting found that day (the break test that found it is in
#: tests/test_job_agent_hostile.py).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
#: The same, ordinary whitespace controls included, for the two values that
#: reach the row as they arrive (the id and the link): those are refused, not
#: cleaned, because a link with a byte taken out is a different link.
_ANY_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: Long enough for the advert's first paragraphs, short enough to be a row.
DESCRIPTION_MAX = 2000
#: An advert id longer than this is not an id. It becomes part of
#: `jobs.source_ref`, whose unique btree index refuses a key over about 2.7 KB
#: -- at COMMIT, losing the morning the same way a NUL did.
MAX_EXTERNAL_ID = 128

#: Seconds between two calls to one board. Adzuna's free tier allows 25 calls
#: a MINUTE as well as 250 a day, and a morning's thirty calls made back to
#: back would be refused from the twenty-sixth, losing that board for the day.
#: Jooble publishes no figure; one a second is a polite guess. A run is a
#: minute or two longer for it, at six in the morning.
ADZUNA_MIN_INTERVAL_SECONDS = 2.6
JOOBLE_MIN_INTERVAL_SECONDS = 1.0


class SourceError(Exception):
    """A provider failed this morning. The message names the provider and what
    went wrong in words, never a URL, because two of the URLs carry a key."""

    def __init__(self, provider: str, message: str, *, stop: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        #: True when asking again this run is pointless or harmful: the key was
        #: refused, the daily allowance is spent, or the host is unreachable.
        self.stop = stop


@dataclass(frozen=True)
class FoundJob:
    """One advert, as either board describes it, in this codebase's words."""

    provider: str
    external_id: str
    title: str
    company: str
    location: str | None
    apply_url: str
    description: str
    posted_on: datetime | None


class Source(Protocol):
    name: str

    def search(self, phrase: str, *, internship: bool) -> list[FoundJob]: ...


def plain_text(value: Any) -> str:
    """An advert's HTML as one line of text: tags out, entities decoded,
    control characters out (after decoding, because "&#0;" is one too)."""
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", _CONTROL.sub("", html.unescape(_TAG.sub(" ", value)))).strip()


def _name(value: Any) -> str:
    """A company or a place, which a board sends as `{"display_name": ...}` or,
    on a bad day, as plain text. Anything else is no name at all."""
    if isinstance(value, dict):
        value = value.get("display_name")
    return plain_text(value)


def _external_id(value: Any) -> str:
    """The board's own id for an advert, or "" when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    return text if len(text) <= MAX_EXTERNAL_ID and not _ANY_CONTROL.search(text) else ""


def _parse_page(provider: str, rows: Any, parse: Callable[[Any], FoundJob | None]) -> list[FoundJob]:
    """Every advert on a page that parses. ONE BAD ROW COSTS ONE ROW: a field
    of a shape nobody expected used to raise out of the parser, out of the
    search and out of the run, so neither board posted anything that morning.
    The row is skipped and counted in the log by provider only, because its
    content is the board's and not ours to repeat."""
    if not isinstance(rows, list):
        return []
    out: list[FoundJob] = []
    skipped = 0
    for row in rows:
        try:
            job = parse(row)
        except Exception:  # noqa: BLE001 - see the docstring
            job = None
            skipped += 1
        if job is not None:
            out.append(job)
    if skipped:
        log.warning("Job agent: skipped %d advert(s) from %s that could not be read.", skipped, provider)
    return out


def parse_posted(value: Any) -> datetime | None:
    """An ISO timestamp from either board, as an aware UTC datetime.

    Jooble writes seven fractional digits ("2026-10-20T00:00:00.0000000"),
    which `fromisoformat` refuses, so the fraction is cut to six first. A
    value that still does not parse is None, never a guess: the row then
    takes the time it was found, which is a fact.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _https(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if url.lower().startswith("https://") and len(url) <= 1000 and not _ANY_CONTROL.search(url):
        return url
    return None


def _request(provider: str, client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    """One call, every failure turned into a `SourceError` with no URL in it."""
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise SourceError(
            provider, f"{PROVIDER_NAMES[provider]} could not be reached ({type(exc).__name__}).", stop=True
        ) from None
    if response.status_code in (401, 403):
        raise SourceError(
            provider, f"{PROVIDER_NAMES[provider]} refused the key ({response.status_code}).", stop=True
        )
    if response.status_code == 429:
        raise SourceError(
            provider, f"{PROVIDER_NAMES[provider]}'s daily allowance is used up (429).", stop=True
        )
    if response.status_code >= 400:
        raise SourceError(provider, f"{PROVIDER_NAMES[provider]} answered {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise SourceError(provider, f"{PROVIDER_NAMES[provider]} sent a reply that is not JSON.") from None


class _Paced:
    """Keeps one board's calls `min_interval` seconds apart. The first call
    waits for nothing."""

    min_interval = 0.0
    _next_at = 0.0

    def _pace(self) -> None:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._next_at = time.monotonic() + self.min_interval


class AdzunaSource(_Paced):
    """`GET /v1/api/jobs/in/search/1`, newest first, at most 14 days old."""

    name = ADZUNA
    min_interval = ADZUNA_MIN_INTERVAL_SECONDS

    def __init__(self, app_id: str, app_key: str, *, client: httpx.Client | None = None) -> None:
        self._app_id = app_id
        self._app_key = app_key
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
        self._pace()
        body = _request(
            ADZUNA,
            self._client,
            "GET",
            ADZUNA_SEARCH_URL,
            params={
                "app_id": self._app_id,
                "app_key": self._app_key,
                "what": phrase,
                "what_or": " ".join(INTERNSHIP_WORDS if internship else FRESHER_WORDS),
                "results_per_page": RESULTS_PER_PAGE,
                "max_days_old": MAX_DAYS_OLD,
                "sort_by": "date",
            },
            headers={"Accept": "application/json"},
        )
        return _parse_page(ADZUNA, body.get("results") if isinstance(body, dict) else None, _adzuna_job)


def _adzuna_job(row: Any) -> FoundJob | None:
    if not isinstance(row, dict):
        return None
    external_id = _external_id(row.get("id"))
    company = _name(row.get("company"))
    title = plain_text(row.get("title"))
    url = _https(row.get("redirect_url"))
    if not (external_id and company and title and url):
        return None
    return FoundJob(
        provider=ADZUNA,
        external_id=external_id,
        title=title,
        company=company,
        location=_name(row.get("location")) or None,
        apply_url=url,
        description=plain_text(row.get("description"))[:DESCRIPTION_MAX],
        posted_on=parse_posted(row.get("created")),
    )


class JoobleSource(_Paced):
    """`POST https://jooble.org/api/<key>`, keywords and a location in the body."""

    name = JOOBLE
    min_interval = JOOBLE_MIN_INTERVAL_SECONDS

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)

    def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
        self._pace()
        body = _request(
            JOOBLE,
            self._client,
            "POST",
            JOOBLE_SEARCH_URL + self._api_key,
            json={
                "keywords": f"{phrase} {(INTERNSHIP_WORDS if internship else FRESHER_WORDS)[0]}",
                "location": JOOBLE_LOCATION,
                "page": "1",
            },
        )
        return _parse_page(JOOBLE, body.get("jobs") if isinstance(body, dict) else None, _jooble_job)


def _jooble_job(row: Any) -> FoundJob | None:
    if not isinstance(row, dict):
        return None
    external_id = _external_id(row.get("id"))
    company = _name(row.get("company"))
    title = plain_text(row.get("title"))
    url = _https(row.get("link"))
    if not (external_id and company and title and url):
        return None
    return FoundJob(
        provider=JOOBLE,
        external_id=external_id,
        title=title,
        company=company,
        location=_name(row.get("location")) or None,
        apply_url=url,
        description=plain_text(row.get("snippet"))[:DESCRIPTION_MAX],
        posted_on=parse_posted(row.get("updated")),
    )


# --------------------------------------------------------------------------- #
# The companies' own careers pages: no key, no sign-up
# --------------------------------------------------------------------------- #

GREENHOUSE = "greenhouse"
LEVER = "lever"
SMARTRECRUITERS = "smartrecruiters"


@dataclass(frozen=True)
class CompanyBoard:
    """One company's public job feed. `token` is the company's name on its
    applicant-tracking system, exactly as its careers page URL spells it."""

    ats: str
    token: str
    company: str


#: The companies read every morning, each checked by hand on 2026-10-23 to
#: answer with open roles in India. ADDING ONE IS ONE LINE: the token is the
#: part of the company's careers URL after `jobs.lever.co/`,
#: `boards.greenhouse.io/` or `jobs.smartrecruiters.com/`. A board that has
#: moved answers 404, is logged by name and skipped; the others carry on.
COMPANY_BOARDS: tuple[CompanyBoard, ...] = (
    CompanyBoard(SMARTRECRUITERS, "BoschGroup", "Bosch"),
    CompanyBoard(SMARTRECRUITERS, "SGS", "SGS"),
    CompanyBoard(SMARTRECRUITERS, "Freshworks", "Freshworks"),
    CompanyBoard(SMARTRECRUITERS, "LinkedIn3", "LinkedIn"),
    CompanyBoard(SMARTRECRUITERS, "AbbVie", "AbbVie"),
    CompanyBoard(SMARTRECRUITERS, "AveryDennison", "Avery Dennison"),
    CompanyBoard(SMARTRECRUITERS, "Continental", "Continental"),
    CompanyBoard(SMARTRECRUITERS, "Experian", "Experian"),
    CompanyBoard(SMARTRECRUITERS, "NielsenIQ", "NielsenIQ"),
    CompanyBoard(SMARTRECRUITERS, "ServiceNow", "ServiceNow"),
    CompanyBoard(SMARTRECRUITERS, "Wabtec", "Wabtec"),
    CompanyBoard(SMARTRECRUITERS, "WesternDigital", "Western Digital"),
    CompanyBoard(LEVER, "paytm", "Paytm"),
    CompanyBoard(LEVER, "meesho", "Meesho"),
    CompanyBoard(LEVER, "cred", "CRED"),
    CompanyBoard(LEVER, "zeta", "Zeta"),
    CompanyBoard(LEVER, "fampay", "FamPay"),
    CompanyBoard(LEVER, "pocketfm", "Pocket FM"),
    CompanyBoard(GREENHOUSE, "groww", "Groww"),
    CompanyBoard(GREENHOUSE, "inmobi", "InMobi"),
    CompanyBoard(GREENHOUSE, "druva", "Druva"),
    CompanyBoard(GREENHOUSE, "rubrik", "Rubrik"),
    CompanyBoard(GREENHOUSE, "observeai", "Observe.AI"),
    CompanyBoard(GREENHOUSE, "hackerrank", "HackerRank"),
    CompanyBoard(GREENHOUSE, "coursera", "Coursera"),
    CompanyBoard(GREENHOUSE, "agoda", "Agoda"),
    CompanyBoard(GREENHOUSE, "airbnb", "Airbnb"),
    CompanyBoard(GREENHOUSE, "stripe", "Stripe"),
    CompanyBoard(GREENHOUSE, "adyen", "Adyen"),
    CompanyBoard(GREENHOUSE, "databricks", "Databricks"),
    CompanyBoard(GREENHOUSE, "mongodb", "MongoDB"),
    CompanyBoard(GREENHOUSE, "okta", "Okta"),
    CompanyBoard(GREENHOUSE, "elastic", "Elastic"),
    CompanyBoard(GREENHOUSE, "twilio", "Twilio"),
    CompanyBoard(GREENHOUSE, "zscaler", "Zscaler"),
    CompanyBoard(GREENHOUSE, "toast", "Toast"),
    CompanyBoard(GREENHOUSE, "imc", "IMC Trading"),
    CompanyBoard(GREENHOUSE, "turing", "Turing"),
)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"
SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
SMARTRECRUITERS_PUBLIC_URL = "https://jobs.smartrecruiters.com/{token}/{id}"

#: SmartRecruiters pages its list; a company with more open roles in India than
#: this many pages hold is read up to here, newest first.
SMARTRECRUITERS_PAGE = 100
SMARTRECRUITERS_MAX_PAGES = 10
#: Seconds between two calls. No board publishes a figure for these public
#: feeds; half a second keeps a morning of about a hundred calls polite.
CAREERS_MIN_INTERVAL_SECONDS = 0.5
#: The most postings read in full in one run. A posting's description is
#: fetched only when a search matches it, and the matches are a few dozen.
CAREERS_MAX_DETAILS = 200
#: How much of a company's own description is kept for JUDGING the role. The
#: years asked for are usually in the requirements, near the end, past the
#: `DESCRIPTION_MAX` the posting stores; app/job_scout.py cuts it to that.
CAREERS_DESCRIPTION_READ_MAX = 20000
#: A role first posted longer ago than this is a standing "always hiring"
#: advert rather than an opening, even though the company still lists it.
CAREERS_MAX_POSTING_AGE_DAYS = 120

#: An Indian location, as the three feeds write it ("Bengaluru-VTP, India",
#: "hyderabad", "Remote - India"). SmartRecruiters is asked for India itself.
_INDIA = re.compile(
    r"\b(india|bengaluru|bangalore|mumbai|delhi|ncr|gurgaon|gurugram|noida|hyderabad|pune|chennai|"
    r"kolkata|ahmedabad|jaipur|kochi|coimbatore|chandigarh|indore|lucknow|nagpur|vadodara|surat|"
    r"mysuru|mysore|mangaluru|bhubaneswar|visakhapatnam|thiruvananthapuram)\b",
    re.IGNORECASE,
)
#: A title that a fresher's job has. No feed is searched for "fresher", so a
#: posting must say so itself; `job_scout.is_fresher_role` still refuses a
#: senior title or two years asked for in the description.
_ENTRY_TITLE = re.compile(
    r"\b(interns?|internship|trainee|graduate|freshers?|junior|jr|entry|apprentice\w*|associate|"
    r"analyst|executive|officer|coordinator|assistant)\b",
    re.IGNORECASE,
)
#: The narrower set for SmartRecruiters, whose own level field is the better
#: evidence: "Associate" there is a level with experience, not a title word.
_FRESHER_TITLE = re.compile(r"\b(interns?|internship|trainee|graduate|freshers?|entry[\s-]level|apprentice\w*)\b", re.IGNORECASE)
_INTERN_TITLE = re.compile(r"\b(interns?|internship|apprentice\w*)\b", re.IGNORECASE)
#: Titles that a company's own feed uses for EXPERIENCED roles even though
#: they carry an entry word: "Staff Data Analyst", "Product Analyst II",
#: "Enterprise Account Executive". An Account Executive on a software
#: company's feed carries a sales quota; "Sales Executive" stays a fresher's.
_NOT_ENTRY_TITLE = re.compile(r"\b(staff|account executive|experienced|ii|iii|iv)\b", re.IGNORECASE)
_SMARTRECRUITERS_ENTRY_LEVELS = frozenset({"entry_level", "internship"})

#: What a posting's title (and its team or function) must mention to answer a
#: stream's search, keyed by `job_scout.SEARCH_TERMS`' phrases. A feed cannot
#: be searched, so each phrase is a topic here rather than an exact phrase;
#: tests/test_job_sources.py fails when a phrase there has no topic here.
CAREERS_TOPICS: dict[str, re.Pattern[str]] = {
    phrase: re.compile(pattern, re.IGNORECASE)
    for phrase, pattern in {
        "hr executive": r"\b(hr|human resources?|people (?:team|operations|experience|partner)|payroll|hrbp|"
        r"employee (?:relations|experience|engagement)|onboarding)\b",
        "hr recruiter": r"\b(recruit\w*|talent acquisition|sourcer|hiring)\b",
        "marketing executive": r"\b(marketing|brand|communications|public relations|events?)\b",
        "sales and marketing": r"\b(sales|business development|key account|account (?:executive|manager|management)|"
        r"inside sales|territory|channel partner|pre-?sales)\b",
        "financial analyst": r"\b(financ\w*|fp&a|fpa|investment|valuation|credit|risk analyst|equity|treasury)\b",
        "finance executive": r"\b(accounts (?:payable|receivable|executive|officer)|accounting|accountant|audit\w*|tax\w*|"
        r"treasury|payables|receivables|billing|collections?|controllership|book-?keep\w*)\b",
        "business analyst": r"\b(business analys\w*|strategy|business operations|bizops|operations analyst|"
        r"consultant|process excellence)\b",
        "data analyst": r"\b(data analys\w*|analytics|insights?|mis|business intelligence|reporting analyst|product analyst)\b",
        "digital marketing": r"\b(digital marketing|performance marketing|growth marketing|social media|seo|sem|ppc|"
        r"paid (?:media|marketing)|content marketing|email marketing|crm marketing|user growth)\b",
        "seo executive": r"\b(seo|sem|search engine|content (?:writer|marketing|strategist)|copywrit\w*|social media)\b",
        "supply chain": r"\b(supply chain|procurement|purchas\w*|buyer|demand plann\w*|supply plann\w*|inventory|"
        r"vendor (?:management|development)|scm|materials management)\b",
        "logistics executive": r"\b(logistics|warehous\w*|fulfil+ment|last mile|transport\w*|fleet|shipping|dispatch|"
        r"distribution|exim|exports?|imports?|customs|freight)\b",
    }.items()
}

_STOP_WORDS = frozenset({"and", "the", "of", "for", "in", "with", "management", "general"})


def topic_pattern(phrase: str) -> re.Pattern[str]:
    """The topic a stream's phrase stands for on a company feed. A phrase with
    no topic (a stream the office adds later, searched by its own name) needs
    every one of its words in the title."""
    known = CAREERS_TOPICS.get(phrase.strip().lower())
    if known is not None:
        return known
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if len(w) >= 2 and w not in _STOP_WORDS]
    if not words:
        return re.compile(r"(?!)")
    return re.compile("".join(rf"(?=.*\b{re.escape(w)}\b)" for w in words), re.IGNORECASE | re.DOTALL)


@dataclass
class _Posting:
    """One open role on a company's feed, judged once when the feed is read."""

    found: FoundJob
    entry: bool
    intern: bool
    topic_text: str
    #: Where the full description is, and how to read it, when the list did
    #: not carry it.
    detail_url: str | None = None
    detail_parse: Callable[[Any], str] | None = None
    detail_read: bool = False


def _careers_get(client: httpx.Client, company: str, url: str, params: dict[str, Any] | None = None) -> Any:
    """One call to a public feed. No key rides on any of these URLs, so a
    failure may name the company; it still never repeats the URL."""
    try:
        response = client.get(url, params=params, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise SourceError(CAREERS, f"{company}'s careers page could not be reached ({type(exc).__name__}).") from None
    if response.status_code >= 400:
        raise SourceError(CAREERS, f"{company}'s careers page answered {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise SourceError(CAREERS, f"{company}'s careers page sent a reply that is not JSON.") from None


def board_key(board: CompanyBoard) -> str:
    """`<ats>/<token>`. A slash cannot be part of either, because both are
    one segment of the company's careers URL."""
    return f"{board.ats}/{board.token}"


def _posting_id(board: CompanyBoard, value: Any) -> str:
    """`<ats>/<token>/<id>`, so two companies' ids can never meet, and the
    company a posting came from is everything before the last slash."""
    raw = _external_id(value)
    return _external_id(f"{board_key(board)}/{raw}") if raw and "/" not in raw else ""


def _label(value: Any) -> str:
    return plain_text(value.get("label")) if isinstance(value, dict) else ""


def _smartrecruiters_posting(board: CompanyBoard, row: Any) -> _Posting | None:
    if not isinstance(row, dict):
        return None
    raw_id = _external_id(row.get("id"))
    external_id = _posting_id(board, raw_id)
    title = plain_text(row.get("name"))
    if not (external_id and title):
        return None
    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    if str(location.get("country") or "").lower() != "in":
        return None
    level = row.get("experienceLevel") if isinstance(row.get("experienceLevel"), dict) else {}
    employment = _label(row.get("typeOfEmployment")).lower()
    intern = level.get("id") == "internship" or "intern" in employment or bool(_INTERN_TITLE.search(title))
    entry = (intern or level.get("id") in _SMARTRECRUITERS_ENTRY_LEVELS or bool(_FRESHER_TITLE.search(title))) and not (
        _NOT_ENTRY_TITLE.search(title)
    )
    company = board.company
    if isinstance(row.get("company"), dict):
        company = plain_text(row["company"].get("name")) or company
    found = FoundJob(
        provider=CAREERS,
        external_id=external_id,
        title=title,
        company=company,
        location=plain_text(location.get("fullLocation") or location.get("city")) or None,
        apply_url=SMARTRECRUITERS_PUBLIC_URL.format(token=board.token, id=raw_id),
        description="",
        posted_on=parse_posted(row.get("releasedDate")),
    )
    topic = " ".join((title, _label(row.get("function")), _label(row.get("department"))))
    return _Posting(
        found,
        entry,
        intern,
        topic,
        detail_url=f"{SMARTRECRUITERS_URL.format(token=board.token)}/{raw_id}",
        detail_parse=_smartrecruiters_description,
    )


def _smartrecruiters_description(body: Any) -> str:
    sections = body.get("jobAd", {}).get("sections", {}) if isinstance(body, dict) else {}
    if not isinstance(sections, dict):
        return ""
    parts = []
    for key in ("jobDescription", "qualifications"):
        section = sections.get(key)
        if isinstance(section, dict):
            parts.append(plain_text(section.get("text")))
    return " ".join(p for p in parts if p)


def _lever_posting(board: CompanyBoard, row: Any) -> _Posting | None:
    if not isinstance(row, dict):
        return None
    external_id = _posting_id(board, row.get("id"))
    title = plain_text(row.get("text"))
    url = _https(row.get("hostedUrl"))
    if not (external_id and title and url):
        return None
    categories = row.get("categories") if isinstance(row.get("categories"), dict) else {}
    places = " ".join(
        plain_text(p) for p in [categories.get("location"), *(categories.get("allLocations") or [])] if isinstance(p, str)
    )
    if str(row.get("country") or "").upper() != "IN" and not _INDIA.search(places):
        return None
    commitment = plain_text(categories.get("commitment")).lower()
    intern = "intern" in commitment or bool(_INTERN_TITLE.search(title))
    entry = (intern or bool(_ENTRY_TITLE.search(title))) and not _NOT_ENTRY_TITLE.search(title)
    lists = row.get("lists") if isinstance(row.get("lists"), list) else []
    description = " ".join(
        p
        for p in (
            plain_text(row.get("descriptionPlain")),
            *(plain_text(item.get("content")) for item in lists if isinstance(item, dict)),
            plain_text(row.get("additionalPlain")),
        )
        if p
    )
    created = row.get("createdAt")
    posted = (
        datetime.fromtimestamp(created / 1000, timezone.utc)
        if isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0
        else None
    )
    found = FoundJob(
        provider=CAREERS,
        external_id=external_id,
        title=title,
        company=board.company,
        location=plain_text(categories.get("location")) or None,
        apply_url=url,
        description=description[:CAREERS_DESCRIPTION_READ_MAX],
        posted_on=posted,
    )
    topic = " ".join((title, plain_text(categories.get("team")), plain_text(categories.get("department"))))
    return _Posting(found, entry, intern, topic, detail_read=True)


def _greenhouse_posting(board: CompanyBoard, row: Any) -> _Posting | None:
    if not isinstance(row, dict):
        return None
    raw_id = _external_id(row.get("id"))
    external_id = _posting_id(board, raw_id)
    title = plain_text(row.get("title"))
    url = _https(row.get("absolute_url"))
    if not (external_id and title and url):
        return None
    location = _name(row.get("location").get("name") if isinstance(row.get("location"), dict) else None)
    if not _INDIA.search(location):
        return None
    intern = bool(_INTERN_TITLE.search(title))
    found = FoundJob(
        provider=CAREERS,
        external_id=external_id,
        title=title,
        company=plain_text(row.get("company_name")) or board.company,
        location=location or None,
        apply_url=url,
        description="",
        posted_on=parse_posted(row.get("first_published")) or parse_posted(row.get("updated_at")),
    )
    return _Posting(
        found,
        (intern or bool(_ENTRY_TITLE.search(title))) and not _NOT_ENTRY_TITLE.search(title),
        intern,
        title,
        detail_url=f"{GREENHOUSE_URL.format(token=board.token)}/{raw_id}",
        detail_parse=_greenhouse_description,
    )


def _greenhouse_description(body: Any) -> str:
    # The content is HTML with its own markup entity-escaped ("&lt;div&gt;").
    return plain_text(html.unescape(body.get("content") or "")) if isinstance(body, dict) else ""


class CareersSource(_Paced):
    """Every company in `COMPANY_BOARDS`, read once per run, then searched by
    topic. Its `search` has the boards' shape so app/job_scout.py treats it as
    one more board: same filters, same dedupe, same daily cap per stream."""

    name = CAREERS
    min_interval = CAREERS_MIN_INTERVAL_SECONDS
    #: Every role on a company's feed is open today, so its age is no reason
    #: to skip or close it; app/job_scout.py closes it when the company stops
    #: listing it instead (`still_listed`).
    lists_open_roles_only = True

    def __init__(self, boards: tuple[CompanyBoard, ...] = COMPANY_BOARDS, *, client: httpx.Client | None = None) -> None:
        self._boards = tuple(boards)
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
        self._postings: list[_Posting] | None = None
        self._details = 0
        #: The companies whose feed could not be read this run, by name.
        self.failed: list[str] = []
        self._read: set[str] = set()
        self._listed: set[str] = set()

    def search(self, phrase: str, *, internship: bool) -> list[FoundJob]:
        topic = topic_pattern(phrase)
        out: list[FoundJob] = []
        for posting in self._read_all():
            wanted = posting.intern if internship else posting.entry and not posting.intern
            if wanted and topic.search(posting.topic_text):
                out.append(self._with_description(posting))
        return out

    def _read_all(self) -> list[_Posting]:
        if self._postings is not None:
            return self._postings
        postings: list[_Posting] = []
        for board in self._boards:
            try:
                rows, complete = self._read_board(board)
            except SourceError as exc:
                self.failed.append(board.company)
                log.warning("Job agent: %s", exc)
            except Exception as exc:  # noqa: BLE001 - one company must never cost the others
                self.failed.append(board.company)
                log.warning("Job agent: %s's careers page failed unexpectedly (%s).", board.company, type(exc).__name__)
            else:
                # Every role the feed lists counts as listed; only the ones
                # recent enough to be an opening are searched.
                self._listed.update(p.found.external_id for p in rows)
                oldest = datetime.now(timezone.utc) - timedelta(days=CAREERS_MAX_POSTING_AGE_DAYS)
                postings.extend(p for p in rows if p.found.posted_on is None or p.found.posted_on >= oldest)
                if complete:
                    # Only a feed read to its end can say a role is gone.
                    self._read.add(board_key(board))
        self._postings = postings
        if self._boards and len(self.failed) == len(self._boards):
            raise SourceError(CAREERS, "No company careers page could be read.", stop=True)
        if self.failed:
            log.warning("Job agent: %d of %d careers pages could not be read.", len(self.failed), len(self._boards))
        return postings

    def still_listed(self) -> tuple[frozenset[str], frozenset[str]] | None:
        """(the companies whose feed was read this run, by `board_key`, and
        every posting id those feeds list), or None when no feed was read.
        A company that could not be read this morning closes nothing."""
        if self._postings is None or not self._read:
            return None
        return frozenset(self._read), frozenset(self._listed)

    def _read_board(self, board: CompanyBoard) -> tuple[list[_Posting], bool]:
        """The board's postings in India, and whether that is all of them."""
        if board.ats == SMARTRECRUITERS:
            rows: list[Any] = []
            complete = False
            for page in range(SMARTRECRUITERS_MAX_PAGES):
                self._pace()
                body = _careers_get(
                    self._client,
                    board.company,
                    SMARTRECRUITERS_URL.format(token=board.token),
                    {"country": "in", "limit": SMARTRECRUITERS_PAGE, "offset": page * SMARTRECRUITERS_PAGE},
                )
                content = body.get("content") if isinstance(body, dict) else None
                if not isinstance(content, list):
                    raise SourceError(CAREERS, f"{board.company}'s careers page sent no list of jobs.")
                rows.extend(content)
                total = body.get("totalFound")
                if not content or (isinstance(total, int) and len(rows) >= total):
                    complete = True
                    break
            postings, skipped = _parse_postings(board, rows, _smartrecruiters_posting)
            return postings, complete and not skipped
        self._pace()
        if board.ats == LEVER:
            body = _careers_get(self._client, board.company, LEVER_URL.format(token=board.token), {"mode": "json"})
            postings, skipped = _parse_postings(board, body, _lever_posting)
            return postings, not skipped
        if board.ats == GREENHOUSE:
            body = _careers_get(self._client, board.company, GREENHOUSE_URL.format(token=board.token))
            jobs = body.get("jobs") if isinstance(body, dict) else None
            postings, skipped = _parse_postings(board, jobs, _greenhouse_posting)
            return postings, not skipped
        raise SourceError(CAREERS, f"{board.company} names a careers system this module cannot read.")

    def _with_description(self, posting: _Posting) -> FoundJob:
        """The posting with its full description, read the first time a search
        matches it. A description that cannot be read leaves the posting
        without one: it is still an open role on the company's own page."""
        if posting.detail_read or posting.detail_url is None or posting.detail_parse is None:
            return posting.found
        if self._details >= CAREERS_MAX_DETAILS:
            return posting.found
        posting.detail_read = True
        self._details += 1
        self._pace()
        try:
            body = _careers_get(self._client, posting.found.company, posting.detail_url)
        except SourceError as exc:
            log.info("Job agent: %s", exc)
            return posting.found
        text = posting.detail_parse(body)
        if text:
            posting.found = replace(posting.found, description=text[:CAREERS_DESCRIPTION_READ_MAX])
        return posting.found


def _parse_postings(
    board: CompanyBoard, rows: Any, parse: Callable[[CompanyBoard, Any], _Posting | None]
) -> tuple[list[_Posting], int]:
    """`_parse_page`'s rule for a company feed: one bad row costs one row. The
    count of rows that could not be read comes back too, because a row that
    raised may be a role already on the board: its id is unknown, so that
    morning the feed cannot say any role is gone (`still_listed`)."""
    if not isinstance(rows, list):
        raise SourceError(CAREERS, f"{board.company}'s careers page sent no list of jobs.")
    out: list[_Posting] = []
    skipped = 0
    for row in rows:
        try:
            posting = parse(board, row)
        except Exception:  # noqa: BLE001 - see _parse_page
            posting = None
            skipped += 1
        if posting is not None:
            out.append(posting)
    if skipped:
        log.warning("Job agent: skipped %d job(s) from %s's careers page that could not be read.", skipped, board.company)
    return out, skipped


def configured_providers() -> list[str]:
    """Which boards have a key on this deployment, in a fixed order."""
    out: list[str] = []
    if settings.adzuna_configured:
        out.append(ADZUNA)
    if settings.jooble_configured:
        out.append(JOOBLE)
    if settings.job_scout_company_boards:
        out.append(CAREERS)
    return out


def sources_from_settings() -> list[Source]:
    """One client per board that is on. Empty when the agent is off."""
    if not settings.job_scout_active:
        return []
    sources: list[Source] = []
    if settings.adzuna_configured:
        sources.append(AdzunaSource(settings.adzuna_app_id.strip(), settings.adzuna_app_key.strip()))
    if settings.jooble_configured:
        sources.append(JoobleSource(settings.jooble_api_key.strip()))
    if settings.job_scout_company_boards:
        sources.append(CareersSource())
    return sources
