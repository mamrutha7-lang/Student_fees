"""The daily job agent: fresher jobs and internships for every specialization,
found on Adzuna and Jooble and posted to the Jobs board (2026-10-23).

The owner's request was an agent that finds fresher jobs for every
specialization and puts them on the portal every day with nobody's hand on it.
`python -m app.job_scout_job` runs this module at 06:00 IST; this file decides
everything the run does and the entry point decides nothing.

WHAT ONE RUN DOES, in order:

  1. Reads every LEAF of the catalogue: each ACTIVE specialization, and each
     ACTIVE course with no specialization under it (BGSCET's Digital Marketing
     and Logistics MBAs). `seed_catalogue.leaves` has the same idea over the
     in-code catalogue; this one reads the database, so a college the office
     sets up on screen is searched the next morning with no change here.
  2. Turns each leaf into search PHRASES (`SEARCH_TERMS`, keyed on the code,
     falling back to the leaf's own name) and asks every configured board for
     a fresher job and an internship per phrase. A phrase two leaves share is
     asked once.
  3. Skips an advert older than the age limit, one `is_suspicious` reads as a
     scam, and one `is_fresher_role` does not admit, and posts each of the
     rest ONCE PER COURSE, aimed at exactly that course and that
     specialization.
  4. Closes its own postings once they are `job_scout_max_age_days` old.
  5. Writes one audit row saying what it did, which is what the Jobs sheet's
     status line reads.

TARGETING IS EXACT, AND THAT IS WHY A ROW IS PER COURSE AND NOT PER ADVERT.
`jobs_visibility.visible_clauses` skips the track clause for a viewer with no
track, and a student on a course with no specialization has none. One row per
advert with its course widened to NULL would therefore reach every
Logistics student with an advert found only for HR and Digital Marketing. So
`source_ref` is `<provider>:<advert id>:<course id>`, unique in the table, and
the row names its college, its course and the specialization codes it was
found for. The same advert found for HR and Marketing on one course is ONE row
with both codes.

THE OFFICE'S WORD WINS, IN BOTH DIRECTIONS. A posting the office closed is
never reopened (closing is one-way everywhere), an advert the office already
posted for that course is not posted again, and the office's own postings are
never closed by the age sweep. An agent posting cannot be DELETED from the
sheet (`console.delete_job` answers 409): a deleted row is forgotten, and the
same advert would be posted again the next morning. Close it instead.

RULE 1. The phrases come from the catalogue's names and codes. Nothing here
reads a student, and `app/job_sources.py` has no parameter one could ride on.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from .config import settings
from .job_sources import (
    PROVIDER_NAMES,
    PROVIDERS,
    FoundJob,
    Source,
    SourceError,
    configured_providers,
)
from .jobs_visibility import STATUS_CLOSED, STATUS_OPEN, normalise_track
from .models.institution import (
    STATUS_ACTIVE,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from .models.job import DegreeLevel, Job
from .models.redesign import AuditEvent
from .models.skill import Skill

log = logging.getLogger(__name__)

#: Where a posting came from, as the three jobs screens print it. "office" is
#: every posting a person typed on the Jobs sheet, which carries no source_ref.
SOURCE_OFFICE = "office"

#: The audit row one run writes. `actor_type` is SYSTEM because no person
#: pressed anything; `routers/audit.py` already serves the column.
AUDIT_ENTITY = "job_scout_run"
AUDIT_ACTION = "RUN"
AUDIT_ACTOR = "SYSTEM"

#: Search phrases per catalogue code, upper case as `tracks` stores them.
#: These are the roles a fresher from each stream is actually hired into in
#: India, two per stream so one board's wording does not decide the morning.
#: A code with no entry is searched by its own name, which is the right answer
#: for a specialization the office adds next year.
#:
#: MARKETING IS NOT DIGITAL MARKETING, `seed_catalogue`'s reason: BGSCET runs
#: both, and the General MBA's Marketing students are hired into sales and
#: brand roles, not SEO desks.
SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    "HR": ("HR executive", "HR recruiter"),
    "MARKETING": ("marketing executive", "sales and marketing"),
    "FA": ("financial analyst", "finance executive"),
    "FIN": ("financial analyst", "finance executive"),
    "BA": ("business analyst", "data analyst"),
    "DM": ("digital marketing", "SEO executive"),
    "LSCM": ("supply chain", "logistics executive"),
}

KIND_JOB = "job"
KIND_INTERNSHIP = "internship"
KINDS = (KIND_JOB, KIND_INTERNSHIP)

#: What an internship's title starts with when its own title does not say so,
#: because the owner asked that a student can tell the two apart at a glance.
INTERNSHIP_PREFIX = "Internship · "

#: A senior title is not a fresher's job, whatever the search matched on.
_SENIOR_TITLE = re.compile(
    r"\b(senior|sr|lead|head|principal|director|vice president|vp|avp|chief|"
    r"architect|general manager|gm)\b",
    re.IGNORECASE,
)
#: A bare "manager" is an experienced hire. An assistant manager is the role an
#: MBA fresher is most often offered in India, and a bank's relationship
#: manager is the commonest campus offer for a Finance or Marketing MBA, so
#: those two stay.
_MANAGER = re.compile(r"\bmanager\b", re.IGNORECASE)
_FRESHER_MANAGER = re.compile(r"\b(assistant|asst\.?|relationship)\s+manager\b", re.IGNORECASE)
#: "2-5 years of experience", "3+ yrs exp", "experience: minimum 2 years". The
#: LOWER bound is what is read: "0-2 years" is a fresher role and "2+" is not.
_YEARS = r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2})?\s*(?:years?|yrs?)"
_UNIT = r"\s*(?:years?|yrs?)\b"
_EXPERIENCE = (
    re.compile(_YEARS + r"(?:\s+of)?(?:\s+\w+){0,2}?\s+(?:experience|exp)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:experience|exp)\s*(?:of|:|-)?\s*(?:minimum|min\.?|at least)?\s*" + _YEARS,
        re.IGNORECASE,
    ),
    # Adverts that never say "experience": "minimum 3 years in analytics",
    # "3 to 5 years in banking", "2-4 yrs in FMCG sales", "5+ years". A bare
    # "2 years" is NOT read, because "a 2 years MBA" is a qualification.
    re.compile(r"\b(?:minimum|min\.?|at\s*least)\s*(?:of\s+)?(\d{1,2})\s*\+?" + _UNIT, re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:-|–|to)\s*\d{1,2}" + _UNIT, re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:\+|plus)" + _UNIT, re.IGNORECASE),
)
#: "Age 21 to 28 years" is the commonest line on an Indian fresher advert and
#: is not experience; it is removed before the patterns above read the text.
_AGE = re.compile(
    r"\bage\b[^.;\n]{0,25}?\d{1,2}\s*(?:-|–|to)\s*\d{1,2}" + _UNIT + r"(?:\s+(?:of\s+age|old))?"
    r"|\b\d{1,2}\s*(?:-|–|to)\s*\d{1,2}" + _UNIT + r"\s+(?:of\s+age|old)\b",
    re.IGNORECASE,
)
#: An advert asking for at most this many years is still a fresher role: one
#: year is what a campus hire with an internship behind them is asked for.
MAX_FRESHER_YEARS = 1

#: How far back a closed posting still counts as the same advert.
DUPLICATE_WINDOW = timedelta(days=90)

#: The run holds this transaction-level advisory lock, so a second run started
#: while one is going (the Ops task pressed at 06:00) finds it taken and stops
#: quietly, instead of colliding on `source_ref`'s unique index at COMMIT and
#: failing with an alert. Outside int4 on purpose: the interview's locks are
#: `hashtext(...)`, an int4, so the two can never share a key.
RUN_LOCK_KEY = 0x6A6F6273636F7574  # "jobscout"

#: An advert written to take a student's money, or the "data entry / work from
#: home / earn per day" family that is the same trick with a softer opening.
#: Jooble aggregates small sites where these are common, and the portal is the
#: college's own: a student reasonably reads a posting there as checked. The
#: owner chose a STRICT filter (2026-10-23): a real employer never charges a
#: candidate, so the cost of a false positive is one advert nobody sees.
_MONEY_ASK = re.compile(
    r"\b(?:registration|processing|security|joining|training|application|interview|enrol?ment|"
    r"onboarding|kit|verification)\s+(?:fees?|charges?|deposit|amount)\b"
    r"|\b(?:security|refundable|caution)\s+deposit\b"
    r"|\bpay\s+(?:a\s+)?(?:refundable\s+)?deposit\b"
    r"|\bdeposit\s+of\s+(?:rs\b|inr\b|₹)"
    r"|\bpay\s+(?:rs\.?\s*|inr\s*|₹\s*)?\d[\d,]*\s*(?:/-)?\s*(?:rs\b\.?|inr\b|rupees\b)?\s*"
    r"(?:to|for|before)\s+(?:apply|applying|register|registration|join|joining|the\s+interview)\b"
    r"|\bguaranteed\s+(?:jobs?|placements?)\b",
    re.IGNORECASE,
)
#: "No registration fee", "never charges a fee": a sentence an honest advert
#: writes to say the opposite. A money phrase right after one of these, in the
#: SAME sentence, is not an ask; "We do not charge. Registration fee Rs 500"
#: still is.
_NEGATED = re.compile(
    r"\b(?:no|not|never|without|zero|free\s+of)\b(?:[^\w.;!?\n]+\w+){0,2}[^\w.;!?\n]*$", re.IGNORECASE
)
_DATA_ENTRY = re.compile(r"\bdata\s+entry\b", re.IGNORECASE)
_EASY_MONEY = re.compile(
    r"\bearn\w*\b[^.\n]{0,40}?\b(?:per\s+(?:day|week|hour)|daily|weekly|a\s+day)\b",
    re.IGNORECASE,
)
_WORK_FROM_HOME = re.compile(r"\b(?:work\s+from\s+home|home[\s-]+based|wfh)\b", re.IGNORECASE)
_PART_TIME = re.compile(r"\bpart[\s-]+time\b", re.IGNORECASE)

#: An advert the INTERNSHIP search found is an internship only when its title
#: says so or its description describes one. Adzuna matches the word anywhere,
#: so "internship experience preferred" on a full-time job used to make that
#: job an "Internship · " posting.
_INTERNSHIP_TITLE = re.compile(r"\bintern(ship)?s?\b", re.IGNORECASE)
_INTERNSHIP_DESCRIBED = re.compile(
    r"\b\d{1,2}[\s-]*months?[\s-]+(?:paid\s+|unpaid\s+)?internship\b"
    r"|\bstipend\b"
    r"|\binternship\s+(?:period|duration|program(?:me)?|opportunity|role|position)\b"
    r"|\bthis\s+is\s+an?\s+(?:paid\s+|unpaid\s+)?internship\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Leaf:
    """One stream a student can be in: a specialization, or a course with none."""

    college_id: str
    course_id: str
    #: The specialization code as `tracks` stores it; None for a course leaf,
    #: whose postings are then for every student on that course.
    track: str | None
    #: The code SEARCH_TERMS is keyed on: the specialization's, else the course's.
    code: str
    name: str
    degree_level: DegreeLevel

    @property
    def label(self) -> str:
        return self.name


@dataclass
class ScoutSummary:
    """What one run did. `as_dict` is the audit row's body."""

    enabled: bool
    dry_run: bool = False
    sources: list[str] = field(default_factory=list)
    leaves: int = 0
    calls: int = 0
    found: int = 0
    kept: int = 0
    created: int = 0
    merged: int = 0
    duplicates: int = 0
    capped: int = 0
    closed: int = 0
    #: Adverts skipped because they were older than the age limit.
    stale: int = 0
    #: Adverts skipped because `is_suspicious` read them as a scam.
    suspicious: int = 0
    #: Another run held the lock, so this one did nothing at all.
    busy: bool = False
    #: New postings per leaf, by the leaf's name.
    per_leaf: dict[str, int] = field(default_factory=dict)
    #: One sentence per board that failed this morning; never a URL.
    errors: dict[str, str] = field(default_factory=dict)
    #: The boards that answered at least one search.
    answered: list[str] = field(default_factory=list)

    @property
    def every_source_failed(self) -> bool:
        """Nothing answered at all. One failed call among good ones is a
        board having a bad minute, which `errors` records; this is the run
        that learned nothing, which is what the exit code reports."""
        return bool(self.sources) and not self.answered

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": list(self.sources),
            "leaves": self.leaves,
            "calls": self.calls,
            "found": self.found,
            "kept": self.kept,
            "created": self.created,
            "merged": self.merged,
            "duplicates": self.duplicates,
            "capped": self.capped,
            "closed": self.closed,
            "stale": self.stale,
            "suspicious": self.suspicious,
            "per_leaf": dict(self.per_leaf),
            "errors": dict(self.errors),
            "answered": list(self.answered),
        }


# --------------------------------------------------------------------------- #
# Reading a posting's source back
# --------------------------------------------------------------------------- #


def source_of_ref(source_ref: str | None) -> str:
    """`adzuna` / `jooble` for an agent posting, `office` for everything else.

    `source_ref` had no writer before this module, so a posting with no ref, or
    with a ref this module did not write, is the office's.
    """
    if source_ref:
        prefix = source_ref.split(":", 1)[0]
        if prefix in PROVIDERS:
            return prefix
    return SOURCE_OFFICE


def job_source(job: Job) -> str:
    return source_of_ref(job.source_ref)


def is_agent_posting(job: Job) -> bool:
    return job_source(job) != SOURCE_OFFICE


def _agent_ref_clause():
    return or_(*(Job.source_ref.like(f"{provider}:%") for provider in PROVIDERS))


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #


def leaves(db: Session) -> list[Leaf]:
    """Every stream to search for, in a stable order (college, course, code).

    Only ACTIVE rungs count, all the way up: an archived college's courses are
    not searched, and a DRAFT specialization is not yet a stream anybody is in.
    """
    courses = db.execute(
        select(AcademicCourse, Department.college_id)
        .join(Department, Department.id == AcademicCourse.department_id)
        .join(College, College.id == Department.college_id)
        .where(
            AcademicCourse.status == STATUS_ACTIVE,
            Department.status == STATUS_ACTIVE,
            College.status == STATUS_ACTIVE,
        )
        .order_by(College.code, Department.code, AcademicCourse.code)
    ).all()
    if not courses:
        return []
    specializations: dict[str, list[AcademicSpecialization]] = {}
    for spec in db.scalars(
        select(AcademicSpecialization)
        .where(
            AcademicSpecialization.course_id.in_([course.id for course, _ in courses]),
            AcademicSpecialization.status == STATUS_ACTIVE,
        )
        .order_by(AcademicSpecialization.code)
    ):
        specializations.setdefault(spec.course_id, []).append(spec)

    out: list[Leaf] = []
    for course, college_id in courses:
        # A course with no level is filed as PG, the Jobs sheet form's default.
        level = course.degree_level or DegreeLevel.PG
        specs = specializations.get(course.id)
        if not specs:
            out.append(
                Leaf(
                    college_id=college_id,
                    course_id=course.id,
                    track=None,
                    code=normalise_track(course.code),
                    name=course.name.strip(),
                    degree_level=level,
                )
            )
            continue
        for spec in specs:
            code = normalise_track(spec.code)
            out.append(
                Leaf(
                    college_id=college_id,
                    course_id=course.id,
                    track=code,
                    code=code,
                    name=spec.name.strip(),
                    degree_level=level,
                )
            )
    return out


def phrases_for(leaf: Leaf) -> tuple[str, ...]:
    return SEARCH_TERMS.get(leaf.code) or (leaf.name,)


# --------------------------------------------------------------------------- #
# Judging one advert
# --------------------------------------------------------------------------- #


def _min_years(text: str) -> int | None:
    text = _AGE.sub(" ", text)
    lows = [int(m.group(1)) for pattern in _EXPERIENCE for m in pattern.finditer(text)]
    return min(lows) if lows else None


def is_fresher_role(found: FoundJob) -> bool:
    """A role a fresher can apply to: no senior title, no 2+ years asked for."""
    if _SENIOR_TITLE.search(found.title):
        return False
    if _MANAGER.search(found.title) and not _FRESHER_MANAGER.search(found.title):
        return False
    years = _min_years(f"{found.title} {found.description}")
    return years is None or years <= MAX_FRESHER_YEARS


def _asks_for_money(text: str) -> bool:
    return any(not _NEGATED.search(text[max(0, m.start() - 40) : m.start()]) for m in _MONEY_ASK.finditer(text))


def is_suspicious(found: FoundJob) -> bool:
    """An advert the portal must not carry, whatever else it says.

    Money asked of the candidate anywhere (a fee, a deposit, "pay Rs 999 to
    apply", "guaranteed placement"); "data entry", "work from home" or "home
    based" in the TITLE; and "earn ... per day" anywhere. In the description
    alone, work from home or data entry counts only beside part time or easy
    money, because "work from home two days a week" is an ordinary hybrid job
    and an analyst's duties can include data entry.
    """
    text = f"{found.title} {found.description}"
    if _asks_for_money(text) or _EASY_MONEY.search(text):
        return True
    if _DATA_ENTRY.search(found.title) or _WORK_FROM_HOME.search(found.title):
        return True
    if _DATA_ENTRY.search(found.description) or _WORK_FROM_HOME.search(found.description):
        return bool(_PART_TIME.search(text))
    return False


def is_internship(found: FoundJob) -> bool:
    return bool(_INTERNSHIP_TITLE.search(found.title))


def posting_title(found: FoundJob, kind: str) -> str:
    """The advert's own title, marked when it is an internship that does not
    say so. The internship search's say-so is not enough: the description has
    to describe one, or the advert is an ordinary job."""
    title = found.title
    if kind == KIND_INTERNSHIP and not is_internship(found) and _INTERNSHIP_DESCRIBED.search(found.description):
        title = INTERNSHIP_PREFIX + title
    return title[:200]


def _skill_terms(db: Session) -> list[tuple[str, re.Pattern[str]]]:
    """Each catalogue skill with one pattern over its name, aliases and slug."""
    out: list[tuple[str, re.Pattern[str]]] = []
    for skill in db.scalars(select(Skill).order_by(Skill.slug)):
        terms = {skill.name, skill.slug.replace("-", " "), *(skill.aliases or [])}
        words = sorted({t.strip().lower() for t in terms if t and len(t.strip()) >= 3})
        if words:
            out.append(
                (
                    skill.slug,
                    re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE),
                )
            )
    return out


def match_skills(text: str, terms: Sequence[tuple[str, re.Pattern[str]]]) -> list[str]:
    """The catalogue slugs an advert names, so match % means what it means for
    the office's postings. An advert naming none has none, which the feed reads
    as "no skills asked for", exactly as it reads an office posting with none."""
    return [slug for slug, pattern in terms if pattern.search(text)][:30]


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _dup_key(title: str, company: str) -> tuple[str, str]:
    """Title and company as two boards (and the office) might each spell them.
    The internship prefix is dropped first, so it cannot hide a duplicate."""
    if title.startswith(INTERNSHIP_PREFIX):
        title = title[len(INTERNSHIP_PREFIX) :]
    return (_norm(title), _norm(company))


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def search_all(
    sources: Sequence[Source], phrases: Iterable[str], summary: ScoutSummary
) -> dict[tuple[str, str], list[tuple[FoundJob, str]]]:
    """Ask every board about every (phrase, kind), once each.

    A board that fails is recorded and, when asking again is pointless (the
    key was refused, the allowance is spent, the host is down), not asked
    again this morning. The other board carries on regardless.
    """
    results: dict[tuple[str, str], list[tuple[FoundJob, str]]] = {}
    unique = list(dict.fromkeys(phrases))
    for source in sources:
        for phrase in unique:
            stopped = False
            for kind in KINDS:
                summary.calls += 1
                try:
                    found = source.search(phrase, internship=kind == KIND_INTERNSHIP)
                except SourceError as exc:
                    summary.errors[source.name] = str(exc)
                    log.warning("Job agent: %s", exc)
                    if exc.stop:
                        stopped = True
                        break
                    continue
                except Exception as exc:  # noqa: BLE001 - one board must never stop the other
                    # Something nobody planned for (a parser bug, a library
                    # error). The TYPE is recorded and never the message,
                    # because an HTTP library's message can carry the URL and
                    # both boards put their key in it. The board is not asked
                    # again: an error nobody understands will happen again.
                    name = PROVIDER_NAMES.get(source.name, source.name)
                    summary.errors[source.name] = f"{name} failed unexpectedly ({type(exc).__name__})."
                    log.warning("Job agent: %s failed unexpectedly (%s).", source.name, type(exc).__name__)
                    stopped = True
                    break
                summary.found += len(found)
                if source.name not in summary.answered:
                    summary.answered.append(source.name)
                results.setdefault((phrase, kind), []).extend((job, kind) for job in found)
            if stopped:
                break
    return results


def _newest_first(item: tuple[FoundJob, str]) -> tuple[int, float]:
    posted = item[0].posted_on
    return (0, -posted.timestamp()) if posted else (1, 0.0)


def close_expired(db: Session, now: datetime) -> int:
    """Close every OPEN agent posting older than the age limit, past its date,
    or whose course is gone.

    The last is the orphan a college deletion leaves: `college_deletion`
    clears `jobs.college_id` and `course_id`, and a row with neither reads as
    "every college, every course" to `visible_clauses`, so an HR advert found
    for one deleted course would reach every HR student and every student with
    no track. An agent posting only ever exists for one course, so one with no
    course has nobody it was meant for.

    The office's own postings are never touched, which is why this filters on
    the `source_ref` prefixes this module writes and nothing broader.
    """
    cutoff = now - timedelta(days=settings.job_scout_max_age_days)
    rows = db.scalars(
        select(Job).where(
            Job.status == STATUS_OPEN,
            _agent_ref_clause(),
            or_(Job.posted_on < cutoff, Job.closes_on < now, Job.course_id.is_(None)),
        )
    ).all()
    for job in rows:
        job.status = STATUS_CLOSED
    return len(rows)


def run(
    db: Session,
    *,
    now: datetime,
    sources: Sequence[Source],
    dry_run: bool = False,
    streams: Sequence[Leaf] | None = None,
) -> ScoutSummary:
    """One morning's run, in one transaction. `dry_run` searches and judges
    everything and then rolls back, so a dry run costs the board calls and
    writes nothing, the audit row included. `streams` defaults to every leaf
    of the catalogue; the age sweep is programme-wide whatever it is."""
    summary = ScoutSummary(enabled=bool(sources), dry_run=dry_run, sources=[s.name for s in sources])
    if not sources:
        return summary
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": RUN_LOCK_KEY}):
        db.rollback()
        summary.busy = True
        return summary

    streams = list(streams) if streams is not None else leaves(db)
    summary.leaves = len(streams)
    found = search_all(sources, (p for leaf in streams for p in phrases_for(leaf)), summary)
    terms = _skill_terms(db)
    cap = settings.job_scout_new_per_specialization
    oldest = now - timedelta(days=settings.job_scout_max_age_days)

    # The rows this run may meet again, read once: every row a ref could name,
    # and every open posting a course's students already see (the office's
    # programme-wide ones included), keyed on title and company.
    by_ref: dict[str, Job] = {}
    by_key: dict[str, dict[tuple[str, str], Job]] = {}

    for leaf in streams:
        candidates: list[tuple[FoundJob, str]] = []
        seen: set[tuple[str, str]] = set()
        for phrase in phrases_for(leaf):
            for kind in KINDS:
                for item in found.get((phrase, kind), []):
                    ident = (item[0].provider, item[0].external_id)
                    if ident not in seen:
                        seen.add(ident)
                        candidates.append(item)
        candidates.sort(key=_newest_first)

        if leaf.course_id not in by_key:
            # Closed rows count too, for a while: an advert the office closed
            # (or the age sweep did) must not come back the next morning
            # through the OTHER board, under a ref the closed row does not
            # carry. Past the window a re-listing is a new advert.
            #
            # A posting with no course counts only where these students can
            # see it: their college's, or one naming no college. Another
            # college's office posting must not hide this college's job, and
            # an orphaned AGENT posting (its college deleted) is nobody's.
            by_key[leaf.course_id] = {
                _dup_key(job.title, job.company): job
                for job in db.scalars(
                    select(Job)
                    .where(
                        or_(
                            Job.course_id == leaf.course_id,
                            and_(
                                Job.course_id.is_(None),
                                or_(Job.college_id.is_(None), Job.college_id == leaf.college_id),
                                or_(Job.source_ref.is_(None), ~_agent_ref_clause()),
                            ),
                        ),
                        or_(Job.status == STATUS_OPEN, Job.created_at >= now - DUPLICATE_WINDOW),
                    )
                    .order_by(Job.created_at)
                )
            }
        refs = [f"{job.provider}:{job.external_id}:{leaf.course_id}" for job, _ in candidates]
        missing = [ref for ref in refs if ref not in by_ref]
        if missing:
            by_ref |= {job.source_ref: job for job in db.scalars(select(Job).where(Job.source_ref.in_(missing)))}

        created = 0
        for (advert, kind), ref in zip(candidates, refs):
            # An advert past the age limit would be posted and closed by the
            # sweep in the same run, and take one of the day's places doing it.
            if advert.posted_on is not None and advert.posted_on < oldest:
                summary.stale += 1
                continue
            if is_suspicious(advert):
                summary.suspicious += 1
                continue
            if not is_fresher_role(advert):
                continue
            summary.kept += 1
            existing = by_ref.get(ref)
            if existing is None:
                twin = by_key[leaf.course_id].get(_dup_key(advert.title, advert.company))
                if twin is not None:
                    summary.duplicates += 1
                    if is_agent_posting(twin) and _add_track(twin, leaf):
                        summary.merged += 1
                    continue
                if created >= cap:
                    summary.capped += 1
                    continue
                job = _new_posting(advert, kind, leaf, ref, terms, now)
                db.add(job)
                by_ref[ref] = job
                by_key[leaf.course_id][_dup_key(job.title, job.company)] = job
                created += 1
                summary.created += 1
            elif _add_track(existing, leaf):
                summary.merged += 1
        summary.per_leaf[leaf.label] = summary.per_leaf.get(leaf.label, 0) + created

    summary.closed = close_expired(db, now)

    if dry_run:
        db.rollback()
        return summary
    db.add(
        AuditEvent(
            actor_user_id=None,
            actor_type=AUDIT_ACTOR,
            entity_type=AUDIT_ENTITY,
            entity_id=uuid.uuid4().hex,
            action=AUDIT_ACTION,
            after_json=summary.as_dict(),
            occurred_at=now,
        )
    )
    db.commit()
    return summary


def _add_track(job: Job, leaf: Leaf) -> bool:
    """Widen an OPEN agent posting to one more specialization of its course.

    A closed posting is never touched: closing is the office's (or the age
    sweep's) decision and this is not a way round it. A posting with no track
    already reaches the whole course, so there is nothing to add.
    """
    if job.status != STATUS_OPEN or not leaf.track or not job.tracks or leaf.track in job.tracks:
        return False
    job.tracks = [*job.tracks, leaf.track]
    return True


def _new_posting(
    advert: FoundJob,
    kind: str,
    leaf: Leaf,
    ref: str,
    terms: Sequence[tuple[str, re.Pattern[str]]],
    now: datetime,
) -> Job:
    # The employer's date when it is a real one in the past, so the board's
    # "posted" order and the age sweep both read the advert's age; a date in
    # the future or none at all is the moment it was found, which is a fact.
    posted = advert.posted_on if advert.posted_on and advert.posted_on <= now else now
    return Job(
        source_ref=ref,
        title=posting_title(advert, kind),
        company=advert.company[:200],
        degree_level=leaf.degree_level,
        location=(advert.location or "")[:200] or None,
        apply_url=advert.apply_url,
        description=advert.description,
        required_skills=match_skills(f"{advert.title} {advert.description}", terms),
        posted_on=posted,
        closes_on=None,
        college_id=leaf.college_id,
        course_id=leaf.course_id,
        tracks=[leaf.track] if leaf.track else [],
        status=STATUS_OPEN,
    )


# --------------------------------------------------------------------------- #
# What the Jobs sheet's status line reads
# --------------------------------------------------------------------------- #


def latest_run(db: Session) -> AuditEvent | None:
    return db.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == AUDIT_ENTITY, AuditEvent.action == AUDIT_ACTION)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(1)
    ).first()


def status_sources() -> list[dict[str, Any]]:
    """Each board, whether it has a key here, in the order the sheet prints them."""
    on = set(configured_providers()) if settings.job_scout_active else set()
    return [{"key": key, "name": PROVIDER_NAMES[key], "configured": key in on} for key in PROVIDERS]
