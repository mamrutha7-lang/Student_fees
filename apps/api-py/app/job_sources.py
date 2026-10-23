"""The two job boards the daily job agent reads (app/job_scout.py): Adzuna and
Jooble, through their OFFICIAL search APIs (2026-10-23).

Official APIs and nothing else, on purpose. Scraping LinkedIn or Naukri is
against both sites' terms, is blocked within days, and would make the morning
run a thing that quietly stops working. Both APIs here are free, keyed, and
documented to be republished: Adzuna's terms ask that every advert shown carry
"Jobs by Adzuna", which the three jobs screens do (`source` on each row).

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
from dataclasses import dataclass
from datetime import datetime, timezone
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


_URL_FILTER = _RedactOutboundUrls()
if not any(isinstance(f, _RedactOutboundUrls) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_URL_FILTER)

ADZUNA = "adzuna"
JOOBLE = "jooble"
PROVIDERS = (ADZUNA, JOOBLE)

#: What each provider is called on a screen. Adzuna's wording is its own
#: terms' ("Jobs by Adzuna"); the client prints it, this is the provider name.
PROVIDER_NAMES = {ADZUNA: "Adzuna", JOOBLE: "Jooble"}

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
#: Long enough for the advert's first paragraphs, short enough to be a row.
DESCRIPTION_MAX = 2000

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
    """An advert's HTML as one line of text: tags out, entities decoded."""
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()


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
    if isinstance(url, str) and url.strip().lower().startswith("https://") and len(url) <= 1000:
        return url.strip()
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
        results = body.get("results") if isinstance(body, dict) else None
        return [job for job in (_adzuna_job(r) for r in results or []) if job is not None]


def _adzuna_job(row: Any) -> FoundJob | None:
    if not isinstance(row, dict):
        return None
    external_id = str(row.get("id") or "").strip()
    company = plain_text((row.get("company") or {}).get("display_name"))
    title = plain_text(row.get("title"))
    url = _https(row.get("redirect_url"))
    if not (external_id and company and title and url):
        return None
    return FoundJob(
        provider=ADZUNA,
        external_id=external_id,
        title=title,
        company=company,
        location=plain_text((row.get("location") or {}).get("display_name")) or None,
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
        rows = body.get("jobs") if isinstance(body, dict) else None
        return [job for job in (_jooble_job(r) for r in rows or []) if job is not None]


def _jooble_job(row: Any) -> FoundJob | None:
    if not isinstance(row, dict):
        return None
    external_id = str(row.get("id") or "").strip()
    company = plain_text(row.get("company"))
    title = plain_text(row.get("title"))
    url = _https(row.get("link"))
    if not (external_id and company and title and url):
        return None
    return FoundJob(
        provider=JOOBLE,
        external_id=external_id,
        title=title,
        company=company,
        location=plain_text(row.get("location")) or None,
        apply_url=url,
        description=plain_text(row.get("snippet"))[:DESCRIPTION_MAX],
        posted_on=parse_posted(row.get("updated")),
    )


def configured_providers() -> list[str]:
    """Which boards have a key on this deployment, in a fixed order."""
    out: list[str] = []
    if settings.adzuna_configured:
        out.append(ADZUNA)
    if settings.jooble_configured:
        out.append(JOOBLE)
    return out


def sources_from_settings() -> list[Source]:
    """One client per board that has a key. Empty when the agent is off."""
    if not settings.job_scout_active:
        return []
    sources: list[Source] = []
    if settings.adzuna_configured:
        sources.append(AdzunaSource(settings.adzuna_app_id.strip(), settings.adzuna_app_key.strip()))
    if settings.jooble_configured:
        sources.append(JoobleSource(settings.jooble_api_key.strip()))
    return sources
