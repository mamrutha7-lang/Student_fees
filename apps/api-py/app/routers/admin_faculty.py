"""Faculty accounts, created and offboarded by the Main Admin on screen.

  POST   /api/admin/faculty                        create the login (B3.1, B3.2)
  GET    /api/admin/faculty[?unfiled=true]         every faculty account (B3.1)
  PATCH  /api/admin/faculty/{user_id}              identity + filing (B3.5)
  POST   /api/admin/users/{user_id}/disable        offboard (B3.3); a student
                                                   too, from their record
  POST   /api/admin/users/{user_id}/enable         within 90 days (B3.3); a
                                                   student at any time
  POST   /api/admin/users/{user_id}/sign-out-everywhere   (B3.6)
  POST   /api/admin/users/{user_id}/disabled-notice   email a disabled
                                                   student to say so
                                                   (2026-10-27)

Until 2026-09 a faculty login was minted only by `python -m app.grant_access` -
on a production host, the GitHub "Ops task" workflow - and its activation link
was read out of a run log. This is that same act on the Faculty & Students
screen: the Main Admin types a name and an address, the account is created,
and the ACTIVATION LINK is shown on screen to hand over (WhatsApp, in person),
because production cannot send mail yet and the on-screen link is permanent
by design (routers/admin.py::issue_activation_link).

ADMIN ONLY (`require_admin`): the owner's rule is that only the Main Admin
gives faculty their access, and an account is the first grant of all.

WHAT THE ACCOUNT IS. Role MENTOR; the unusable password sentinel, so nothing
signs in until the person redeems the link and sets their own password (or
Google, once configured); NO `Mentor` group - a faculty member becomes a
mentor when the Main Admin assigns them a student
(routers/admin_mentoring.py). An
address already in `users` is refused - a MENTOR row must never be attached to
someone else's account.

THE ADDRESS IS NOW FENCED TO THE COLLEGE'S DOMAINS (B3.2), which is a reversal
of what this module used to say. The old argument was that grant_access does
not fence either and that the Main Admin minting the row deliberately IS the
check. That held while there was one college and one operator. It stops holding
the moment there are two, because "one of ours" becomes "one of THEIRS", and a
typo in a domain (gmail.com for the college's own) mints a REEP account on an
address the institution does not control - which is a roster row, and the roster
is the access control. So the fence is the college's `email_domains`
(app/institution_domains.py, B1.1), with the deployment's env list as the
fallback for a college that has recorded none. An address outside it is still
mintable, because a visiting lecturer on a personal address is a real case - but
only with `allow_external` AND a written reason, and both land in the audit row.

OFFBOARDING IS A COLUMN, NOT A DELETE (B3.3). `users.disabled_at` and its two
companions; nothing the person wrote is removed, because the mentor notes, the
sanctioned leave and the verified evidence are parts of OTHER people's records
and were true when they were written.

DELETING IS TWO OTHER COLUMNS AND ONE OTHER MODULE (2026-09-16). REMOVE
(`users.deleted_at`, `routers/admin_deletion.py`) takes the account off this
list and every other while keeping every row; `?removed=true` lists exactly
those for Restore. DELETE, for good, is the same module with a code emailed to
the office account, through `app/account_deletion.py`; `python -m
app.purge_people` remains the way to empty a whole deployment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import account_links, account_mail
from ..architecture_events import record_change
from ..config import settings
from ..db import get_db
from ..identity import get_current_session
from ..institution_domains import domain_of, provisionable_domains_for
from ..mentor_history import release_mentees_of
from ..models.mail import MailLog, MailStatus
from ..models.user import Role, User
from ..security import note_revocation
from ..staff_placement import UNFILED, StaffPlacement, placement_for, placements_for, resolve_department
from .mentor import require_admin
from .registration import SSO_ONLY_PASSWORD_HASH

log = logging.getLogger(__name__)

#: `router` is what app/main.py mounts, and it is a COMPOSITE: the faculty
#: screen's own surface hangs off `/admin/faculty`, while disable/enable/
#: sign-out-everywhere are properties of an ACCOUNT rather than of a faculty
#: member and hang off `/admin/users` beside the activation-link endpoint that
#: is already there. Two prefixes, one mount, so nothing in main.py changes.
router = APIRouter()
faculty_router = APIRouter(prefix="/admin/faculty", tags=["admin-faculty"])
users_router = APIRouter(prefix="/admin/users", tags=["admin-faculty"])

#: How long after being disabled an account may be switched back on. Past it the
#: answer is a NEW account: ninety days is long enough for "she is back from
#: sabbatical" and short enough that re-enabling is never the way somebody's old
#: access quietly returns a year later. The window is measured from
#: `disabled_at`, which is why that column is a timestamp and not a boolean.
ENABLE_WINDOW_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _department_or_422(db: Session, department_id: str | None):
    """Resolve a named department, or refuse by name.

    A department id that does not exist must not fall through to "filed under
    nothing": that is a write which reports success and stores null, which is
    the exact class of bug this whole change exists to remove.
    """
    try:
        return resolve_department(db, department_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"No department with id {department_id!r}.",
        ) from None


def _email_policy(
    db: Session,
    email: str,
    *,
    college_id: str | None,
    allow_external: bool,
    reason: str | None,
) -> dict:
    """Decide whether this address may hold a REEP faculty account, and say why.

    Returns the decision, which the caller puts in the audit row VERBATIM. That
    is the point of returning it rather than a bool: "we let an outside address
    through" is exactly the fact somebody will be asked about in six months, and
    a policy decision that leaves no trace is indistinguishable from no policy.
    """
    domains = provisionable_domains_for(db, college_id)
    domain = domain_of(email)
    if domain and domain in domains:
        return {"domain": domain, "external": False}
    listed = ", ".join(sorted(domains)) or "(none configured)"
    if not allow_external:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{email} is not on this college's domains ({listed}). Tick "
                "\"outside the college domain\" and give a reason if that is "
                "deliberate."
            ),
        )
    if not (reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "An address outside the college's domains needs a reason. It is "
                "recorded against this account."
            ),
        )
    return {
        "domain": domain,
        "external": True,
        "reason": reason.strip(),
        "college_domains": sorted(domains),
    }


class AdminFacultyIn(BaseModel):
    name: str
    email: str
    designation: str | None = None
    #: Free text, kept because the official leave form prints this line and
    #: existing rows carry it. `department_id` below is the real pointer.
    department: str | None = None
    #: WHERE THIS PERSON WORKS, as a real department id. REQUIRED (B3.1).
    #:
    #: It was optional, and the argument for that was a good one: the Main Admin
    #: could mint a login now and file it later, the same way `students.cohort_id`
    #: is nullable, with the screen asking for it anyway. What that argument
    #: missed is that the department is no longer only a label to group by - it
    #: is how the account reaches its COLLEGE, and the college is what decides
    #: which email domains may hold an account (B3.2) and, through B1.2, what a
    #: scoped grant on this person can ever mean. An unfiled faculty account is
    #: therefore not merely invisible to "who teaches in..."; it is an account
    #: with no tenant, fenced by the deployment's fallback list rather than by
    #: anybody's policy.
    #:
    #: The escape hatch stays where it belongs: the COLUMN is still nullable, so
    #: every account created before this rule keeps working, and
    #: `GET /api/admin/faculty?unfiled=true` lists them for filing through PATCH.
    #: This is a rule about new rows, not a promise about old ones - the same
    #: distinction `HIERARCHY_LEVELS` draws for batches.
    department_id: str
    #: B3.2. An address off the college's domains is refused unless BOTH of these
    #: are supplied; the pair, and the domain, are written into the audit row.
    allow_external: bool = False
    external_reason: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("name must not be blank")
        return " ".join(v.split())

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: object) -> str:
        e = str(v or "").strip().lower()
        if "@" not in e or e.startswith("@") or e.endswith("@"):
            raise ValueError("not an email address")
        return e

    @field_validator("department_id", mode="before")
    @classmethod
    def _department_id(cls, v: object) -> str:
        value = str(v or "").strip()
        if not value:
            raise ValueError(
                "a department is required: it is how this account reaches its "
                "college, and the college decides which addresses may hold one"
            )
        return value

    @field_validator("designation", "department", "external_reason", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> str | None:
        if v is None:
            return None
        s = " ".join(str(v).split())
        return s[:240] or None


class StaffPlacementOut(BaseModel):
    """The resolved institutional position — read through the join, never stored.

    `filed` is what a client branches on. A falsy `department_name` cannot tell
    "nobody has filed this person" apart from "a department with a blank name",
    and those mean opposite things on a screen whose whole job is to show which
    faculty still need filing.
    """

    filed: bool = False
    department_id: str | None = None
    department_code: str | None = None
    department_name: str | None = None
    college_id: str | None = None
    college_code: str | None = None
    college_name: str | None = None

    @classmethod
    def of(cls, placement: StaffPlacement) -> "StaffPlacementOut":
        return cls(
            filed=placement.filed,
            department_id=placement.department_id,
            department_code=placement.department_code,
            department_name=placement.department_name,
            college_id=placement.college_id,
            college_code=placement.college_code,
            college_name=placement.college_name,
        )


class AdminFacultyOut(BaseModel):
    user_id: str
    name: str
    email: str
    designation: str | None
    department: str | None
    #: Resolved through `users.department_id`. See StaffPlacementOut.
    placement: StaffPlacementOut = StaffPlacementOut()
    #: Hand this over. It is the same link the Ops task prints; it expires.
    activation_link: str
    #: True only when a mail transport is configured and the link was sent.
    emailed: bool
    expires_in_hours: int
    #: B3.4. The link is shown ONCE and never listed again — nothing stores the
    #: raw token, only its sha256, so this response is the only place it exists.
    #: The client says so instead of letting an admin think they can come back
    #: for it; re-minting is a deliberate act with its own endpoint.
    shown_once: bool = True


class AdminFacultyPatchIn(BaseModel):
    """Editing an EXISTING faculty member. Only what is sent is changed.

    This endpoint exists because the column arrived after the accounts did:
    without it, every faculty member created before this change is permanently
    unfiled and the only way to place them is a hand-written UPDATE. B3.5 adds
    the two identity fields — a married name, a typo in an address read out over
    a phone — for the same reason: the alternative is a hand-written UPDATE on
    `users`, which no audit row will ever mention.
    """

    name: str | None = None
    email: str | None = None
    designation: str | None = None
    department: str | None = None
    department_id: str | None = None
    allow_external: bool = False
    external_reason: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: object) -> str | None:
        if v is None:
            return None
        name = " ".join(str(v).split())
        if not name:
            raise ValueError("name must not be blank")
        return name

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: object) -> str | None:
        if v is None:
            return None
        e = str(v).strip().lower()
        if "@" not in e or e.startswith("@") or e.endswith("@"):
            raise ValueError("not an email address")
        return e

    @field_validator("designation", "department", "external_reason", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> str | None:
        if v is None:
            return None
        s = " ".join(str(v).split())
        return s[:240] or None


class AdminFacultyRowOut(BaseModel):
    """A faculty member as the console lists them. No activation link here —
    minting one is a deliberate act with its own endpoint, not a side effect of
    reading a list."""

    user_id: str
    name: str
    email: str
    designation: str | None
    department: str | None
    placement: StaffPlacementOut
    #: B3.3. Null for every account that is signed in as usual. The screen shows
    #: a disabled account greyed with its date rather than hiding it — a faculty
    #: member who vanishes from the list reads as a bug, and the office still has
    #: to be able to find them to switch them back on.
    disabled_at: datetime | None = None
    disable_reason: str | None = None
    #: REMOVED (2026-09-16): off every screen, rows kept, Restore undoes it.
    #: Null on every listed account; the removed ones are listed only by
    #: `?removed=true`, where these two say when and why.
    deleted_at: datetime | None = None
    delete_reason: str | None = None


@faculty_router.post("", response_model=AdminFacultyOut, status_code=status.HTTP_201_CREATED)
def create_faculty(
    body: AdminFacultyIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminFacultyOut:
    require_admin(session)
    existing = db.scalar(select(User).where(func.lower(User.email) == body.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.email} already belongs to a {existing.role.value} account.",
        )
    dep = _department_or_422(db, body.department_id)
    # `department_id` is required by the schema, so `dep` is never None here —
    # but `_department_or_422` is shared with PATCH, where it can be.
    assert dep is not None
    policy = _email_policy(
        db,
        body.email,
        college_id=dep.college_id,
        allow_external=body.allow_external,
        reason=body.external_reason,
    )
    user = User(
        email=body.email,
        name=body.name,
        role=Role.MENTOR,
        password_hash=SSO_ONLY_PASSWORD_HASH,
        designation=body.designation,
        # The free-text line the leave form prints. When a real department was
        # chosen its NAME is what goes here, so the two can never disagree on
        # paper; a caller who names no department may still type the line.
        department=dep.name,
        department_id=dep.id,
    )
    db.add(user)
    db.flush()
    link, emailed = account_links.issue_activation(db, user, created_by_user_id=session.get("userId"))
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="faculty", entity_id=user.id, action="CREATE",
        before=None,
        after={
            "email": user.email, "name": user.name, "designation": user.designation,
            "department": user.department, "department_id": user.department_id,
            "email_policy": policy,
        },
        event_type="faculty.create",
        payload={"email": user.email, "emailed": emailed, "email_policy": policy},
    )
    db.commit()
    return AdminFacultyOut(
        user_id=user.id, name=user.name, email=user.email,
        designation=user.designation, department=user.department,
        placement=StaffPlacementOut.of(placement_for(db, user.id)),
        activation_link=link, emailed=emailed, expires_in_hours=settings.activation_link_hours,
    )


@faculty_router.get("", response_model=list[AdminFacultyRowOut])
def list_faculty(
    unfiled: bool = Query(
        False,
        description="Only the accounts with no department — the ones to file.",
    ),
    removed: bool = Query(
        False,
        description="Only the accounts REMOVED from the roster (users.deleted_at) — the ones to restore.",
    ),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[AdminFacultyRowOut]:
    """Every faculty account and where it is filed.

    A REMOVED account (2026-09-16) is not in this list: that is the whole
    meaning of removal, and `?removed=true` is the list of exactly those, for
    the Restore button. A DISABLED account stays listed, greyed — the model
    says why the two are different columns.

    UNFILED FIRST, then by name. The screen's job is to get everyone placed, and
    a list that buries the six unfiled accounts alphabetically among two hundred
    filed ones is a list nobody finishes.

    `?unfiled=true` narrows to exactly those (B3.1). Sorting them to the top is
    not the same affordance: with the department now REQUIRED on creation, the
    unfiled set is a fixed backlog of accounts that predate the rule, and the
    admin working through it wants a list that EMPTIES. Filtered in SQL on
    `department_id IS NULL`, not on `placement.filed` after the fact, so the
    count is the query's and cannot drift from the rows.

    B1.4 ASKS FOR THIS LIST TO BE SCOPED AND IT IS NOT, DELIBERATELY. The gate
    is `require_admin` — the Main Admin and nobody else — so the "college admin"
    B1.3 describes (a MENTOR account holding scoped `admin.*` grants) is refused
    at the door and never reaches a filter. Adding `scope_filter` under a role
    gate that admits one account whose baseline resolves to `everything` would
    narrow nothing for anybody, while reading as a fence that is doing work; the
    next person to widen the gate would then believe the scope was already
    there. Whoever widens it — B3.x owns this module's gate — narrows it in the
    same commit, with `Reach.user_ids()`, and should know that an UNFILED
    account (`users.department_id IS NULL`, the backlog `?unfiled=true` exists
    to empty) hangs under nothing and so is reached by no scoped grant: the
    unfiled list stays the Main Admin's work by construction.
    """
    require_admin(session)
    query = (
        select(
            User.id, User.name, User.email, User.designation, User.department,
            User.disabled_at, User.disable_reason, User.deleted_at, User.delete_reason,
        )
        .where(User.role == Role.MENTOR)
        .where(User.deleted_at.is_not(None) if removed else User.deleted_at.is_(None))
        .order_by(User.name)
    )
    if unfiled:
        query = query.where(User.department_id.is_(None))
    rows = db.execute(query).all()
    placements = placements_for(db, [r[0] for r in rows])
    out = [
        AdminFacultyRowOut(
            user_id=uid,
            name=name,
            email=email,
            designation=designation,
            department=department,
            placement=StaffPlacementOut.of(placements.get(uid, UNFILED)),
            disabled_at=disabled_at,
            disable_reason=disable_reason,
            deleted_at=deleted_at,
            delete_reason=delete_reason,
        )
        for (uid, name, email, designation, department, disabled_at, disable_reason,
             deleted_at, delete_reason) in rows
    ]
    out.sort(key=lambda r: (r.placement.filed, r.name.lower()))
    return out


def _faculty_or_404(db: Session, user_id: str) -> User:
    """ONLY FACULTY. A path id naming a student, an alumnus or the Main Admin is
    a 404 rather than an edit: this endpoint writes `role`-adjacent
    institutional fields and must never become a way to reach another kind of
    account."""
    user = db.get(User, user_id)
    if user is None or user.role is not Role.MENTOR:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faculty member not found.")
    return user


@faculty_router.patch("/{user_id}", response_model=AdminFacultyRowOut)
def update_faculty(
    user_id: str,
    body: AdminFacultyPatchIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AdminFacultyRowOut:
    """File an existing faculty member, move them, or correct who they are.

    CHANGING THE ADDRESS CHANGES THE IDENTITY, so it does three things a
    designation change does not: it is lower-cased and checked for uniqueness
    (the address is how Google sign-in finds the row, and two rows answering one
    address is an account takeover with no attacker in it); it goes through the
    same college-domain fence as creation (B3.2), because an edit that could put
    an account on any domain would make the fence on creation decorative; and it
    bumps `token_version`, which signs the account out everywhere. That last one
    is not tidiness - the session cookie carries `email` as a claim, so an
    un-retired session would keep acting under the old address until it expired.
    """
    require_admin(session)
    user = _faculty_or_404(db, user_id)

    fields = body.model_dump(exclude_unset=True)
    before = {
        "name": user.name,
        "email": user.email,
        "designation": user.designation,
        "department": user.department,
        "department_id": user.department_id,
    }
    policy: dict | None = None
    signed_out = False

    if "department_id" in fields:
        dep = _department_or_422(db, fields["department_id"])
        user.department_id = dep.id if dep is not None else None
        # Keep the printed line in step with the pointer, for the same reason
        # create does: the leave form prints `department`, and two sources of
        # truth for one line is how a paper form contradicts the console.
        if dep is not None:
            user.department = dep.name
    if "email" in fields and fields["email"] and fields["email"] != user.email.lower():
        email = fields["email"]
        clash = db.scalar(
            select(User).where(func.lower(User.email) == email, User.id != user.id)
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{email} already belongs to a {clash.role.value} account.",
            )
        # The fence reads the college the person is filed under AFTER any move in
        # this same request: moving somebody to another college and giving them
        # that college's address is one edit, and checking the old college would
        # refuse it.
        policy = _email_policy(
            db,
            email,
            college_id=placement_for(db, user.id).college_id if user.department_id else None,
            allow_external=body.allow_external,
            reason=body.external_reason,
        )
        user.email = email
        user.token_version = (user.token_version or 0) + 1
        signed_out = True
    if "name" in fields and fields["name"]:
        user.name = fields["name"]
    if "designation" in fields:
        user.designation = fields["designation"]
    # An explicit free-text department only wins when no real one was named in
    # the same request.
    if "department" in fields and not user.department_id:
        user.department = fields["department"]

    after = {
        "name": user.name,
        "email": user.email,
        "designation": user.designation,
        "department": user.department,
        "department_id": user.department_id,
    }
    if policy is not None:
        after["email_policy"] = policy
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="faculty", entity_id=user.id, action="UPDATE",
        before=before,
        after=after,
        event_type="faculty.update",
        payload={"email": user.email, "signed_out": signed_out},
    )
    db.commit()
    db.refresh(user)
    if signed_out:
        note_revocation(user.id, user.token_version)
        log.info("faculty %s renamed address; all sessions revoked", user.id)
    return AdminFacultyRowOut(
        user_id=user.id, name=user.name, email=user.email,
        designation=user.designation, department=user.department,
        placement=StaffPlacementOut.of(placement_for(db, user.id)),
        disabled_at=user.disabled_at, disable_reason=user.disable_reason,
        deleted_at=user.deleted_at, delete_reason=user.delete_reason,
    )


# ------------------------------------------------- the account itself (B3.3) --


class DisableIn(BaseModel):
    """A reason, and it is not optional.

    Disabling an account is the one console action whose effect is invisible
    from the console afterwards - the person simply cannot get in, and six
    months later nobody remembers whether it was a resignation, a secondment or
    a security incident. The reason is stored on the row AND in the audit
    trail, and the 90-day re-enable window is read against it.
    """

    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: object) -> str:
        text = " ".join(str(v or "").split())
        if len(text) < 3:
            raise ValueError("a reason is required, in words")
        return text[:500]


class AccountStateOut(BaseModel):
    user_id: str
    email: str
    role: str
    disabled: bool
    disabled_at: datetime | None = None
    disable_reason: str | None = None
    token_version: int
    #: How many live links and codes were killed with the account. Zero is the
    #: usual answer and is worth saying: it means there was nothing outstanding.
    links_revoked: int = 0
    #: B9.1. How many students were unseated because this faculty account was
    #: disabled. Zero on every ENABLE and on a faculty member with no group, and
    #: it is stated rather than omitted for `links_revoked`'s reason: "nobody
    #: was moved" is a fact the admin wants confirmed, not an absent field.
    mentees_released: int = 0
    #: The email that tells a disabled STUDENT so (app/account_mail.py):
    #: "SENT", "FAILED", or None when none was attempted (a faculty account,
    #: an enable, or mail switched off on this server). SENT means SES took
    #: it, never that it arrived; the Email delivery screen says the rest.
    notice_mail: str | None = None
    detail: str


@users_router.post("/{user_id}/disable", response_model=AccountStateOut)
def disable_account(
    user_id: str,
    body: DisableIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountStateOut:
    """Offboard an account: it cannot sign in by any door, and every device goes.

    THREE WRITES AND NO DELETES. `disabled_at` (the check every door reads,
    through app/security.py's one lookup), `token_version + 1` (the live
    sessions, which app/security.py refuses on their next request), and every
    outstanding activation/reset/onboarding link consumed. Everything the person
    wrote stays: their mentor notes are part of a student's record, the leave
    they sanctioned is on somebody's form, the evidence they verified is the
    reason a badge exists. Emptying a deployment of people is
    `python -m app.purge_people`, which dry-runs by default and says so.

    IT NOW RELEASES THEIR MENTEES (B9.1), which this docstring used to say it
    deliberately did not. The old sentence was right about ACCESS - a disabled
    account cannot make a request, so a mentee still pointing at it was never a
    leak - and wrong about the roster: those students sat in a group nobody can
    open, invisible in the unassigned pool, and the office found out when one of
    them asked why their mentor never replied. The release is
    `mentor_history.release_mentees_of`, which unseats each of them, writes a
    `faculty_disabled` history row naming this act, and puts them back in the
    pool the assignment screen draws from. `released` on the response says how
    many, so the admin sees the consequence in the same breath as the act.

    NO HANDOVER GRANT IS MINTED HERE, unlike every other release. The 90-day
    window exists so the previous mentor can still be asked about a note they
    wrote; an offboarded account cannot sign in to answer, and the grant would
    sit on the Governance screen looking like access somebody forgot to revoke.

    IT STILL DOES NOT REVOKE THEIR CAPABILITY GRANTS, and that half stands
    (B2.5): a grant is a permission to make a REQUEST and a disabled account
    cannot make one. `governance.py` owns grants and the office revokes what is
    no longer needed there, with a reason, on the trail.

    REFUSES AN ADMIN, and refuses you. The Main Admin IS the console; disabling
    it locks every human out of the only screen that could switch it back on,
    and there is no second admin by design (`grant_access` refuses to mint one).
    A handover demotes the current office account to MENTOR first, which is the
    path `grant_access` already documents.
    """
    require_admin(session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if user.id == session.get("userId"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="You cannot disable the account you are signed in with.",
        )
    if user.role is Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "The Main Admin account cannot be disabled - it is the only way "
                "into the console. Hand the office over first (demote it to "
                "MENTOR with `python -m app.grant_access`), then disable it."
            ),
        )
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{user.email} was removed from the roster on {user.deleted_at:%Y-%m-%d} "
                "and cannot sign in already. Restore it first if you mean to disable it."
            ),
        )
    if user.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{user.email} was already disabled on {user.disabled_at:%Y-%m-%d}.",
        )

    before = {
        "disabled_at": None,
        "disable_reason": None,
        "token_version": int(user.token_version or 0),
    }
    user.disabled_at = _now()
    user.disabled_by_user_id = session.get("userId")
    user.disable_reason = body.reason
    user.token_version = int(user.token_version or 0) + 1
    revoked = account_links.revoke_all_user_tokens(db, user.id)
    # B9.1. Before the audit row, so `released` is on it.
    released = release_mentees_of(
        db,
        faculty_user_id=user.id,
        by_user_id=session.get("userId"),
        reason=f"faculty account disabled: {body.reason}",
        session=session,
        request=request,
    )
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user.id, action="DISABLE",
        before=before,
        after={
            "disabled_at": user.disabled_at.isoformat(),
            "disable_reason": user.disable_reason,
            "token_version": user.token_version,
            "links_revoked": revoked,
            "mentees_released": len(released),
        },
        event_type="user.disable",
        payload={
            "email": user.email, "role": user.role.value, "reason": body.reason,
            "mentees_released": len(released),
        },
    )
    db.commit()
    # AFTER the commit, and carrying `disabled=True`, so this worker refuses the
    # account at once rather than at the end of the revocation cache window.
    note_revocation(user.id, user.token_version, disabled=True)
    log.warning(
        "account %s (%s) DISABLED by %s: %s",
        user.email, user.role.value, session.get("email"), body.reason,
    )
    # A STUDENT IS TOLD BY EMAIL (2026-10-27), after the commit so the mail
    # never describes a block that did not land. Inline rather than in the
    # background, so the sentence below can say whether it went. The block
    # has landed whatever happens here, so nothing here may turn it into a 500.
    try:
        notice = account_mail.notify_student_disabled(db, user)
    except Exception as exc:  # a database fault while writing the mail row
        log.error("Disabled notice not written for account %s: %s", user.id, type(exc).__name__)
        db.rollback()
        notice = None
    return AccountStateOut(
        user_id=user.id, email=user.email, role=user.role.value, disabled=True,
        disabled_at=user.disabled_at, disable_reason=user.disable_reason,
        token_version=user.token_version, links_revoked=revoked,
        mentees_released=len(released),
        notice_mail=notice.status.value if notice is not None else None,
        detail=(
            f"{user.email} can no longer sign in, and every device it held has "
            "been signed out. Nothing they wrote has been removed."
            + (
                f" {len(released)} student(s) were released back to the "
                "unassigned pool and need a new faculty member."
                if released
                else ""
            )
            + _notice_sentence(user, notice)
        ),
    )


def _notice_sentence(user: User, notice: MailLog | None) -> str:
    """What the screen says about the disabled notice, after the act's own
    sentence. Nothing for a faculty account, which is never sent one."""
    if user.role is not Role.STUDENT:
        return ""
    if notice is None:
        return " No email was sent: email is switched off on this server."
    if notice.status is MailStatus.SENT:
        return (
            f" We emailed {user.email} to say the account is disabled and to "
            "contact the placement office. Your reason is not in the email."
        )
    return (
        " The email telling them could not be sent. Email delivery has the "
        "reason; press Email the disabled notice under More to try again."
    )


@users_router.post("/{user_id}/disabled-notice", response_model=AccountStateOut)
def send_disabled_notice(
    user_id: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountStateOut:
    """Email a disabled student that their account is disabled (2026-10-27).

    The way the students disabled before the mail existed are told, and the
    way to try again when a student says it never came. Every press sends one
    message (app/account_mail.py says how a double-tap still sends once). The
    Main Admin's, like the disable itself. Writes no audit row: it changes no
    account, and the `mail_logs` row it writes is the record, on the Email
    delivery screen.
    """
    require_admin(session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if user.role is not Role.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only a student is sent this email.",
        )
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{user.email} was removed from the roster, so it is not sent this email.",
        )
    if user.disabled_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{user.email} is not disabled, so there is nothing to tell them.",
        )
    if not settings.account_mail_active:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email is switched off on this server, so nothing was sent.",
        )
    if not account_mail.mailable(user):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This account has no email address to send to.",
        )
    row = account_mail.send_disabled_notice(db, user)
    sent = row.status is MailStatus.SENT
    return AccountStateOut(
        user_id=user.id, email=user.email, role=user.role.value, disabled=True,
        disabled_at=user.disabled_at, token_version=int(user.token_version or 0),
        notice_mail=row.status.value,
        detail=(
            f"We emailed {user.email} to say the account is disabled and to contact "
            "the placement office. Your reason is not in the email."
            if sent
            else "The email could not be sent. Email delivery has the reason."
        ),
    )


@users_router.post("/{user_id}/enable", response_model=AccountStateOut)
def enable_account(
    user_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountStateOut:
    """Switch a disabled account back on. THE LOGIN ONLY.

    Capability grants are NOT re-granted, and that asymmetry is the design: a
    grant is made in Governance with a reason on the audit trail, so restoring
    one silently - as a side effect of a different button, on the word of
    whoever is re-enabling - would be a grant nobody made. The office re-grants
    what is still needed, which is also the moment to notice what no longer is.

    NINETY DAYS, from `disabled_at`. Past that the answer is a new account,
    because "re-enable" a year later is how a departed colleague's access comes
    back with nobody having decided it should. Never for a STUDENT, who has no
    "new account" to be given (see the check below).
    """
    require_admin(session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if user.disabled_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{user.email} is not disabled.",
        )
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{user.email} was removed from the roster on {user.deleted_at:%Y-%m-%d}. "
                "Restore it first; enabling a removed account would admit nobody."
            ),
        )
    disabled_at = user.disabled_at
    if disabled_at.tzinfo is None:  # a column read back from a naive driver
        disabled_at = disabled_at.replace(tzinfo=timezone.utc)
    # A STUDENT has no window. "Create a new account instead" has no meaning
    # for one: a student account is minted only by approving an application,
    # their USN is already taken by this row, and every mark, upload and note
    # hangs off it. The window guards a colleague's access coming back by
    # accident; a student the office disabled over a security issue comes back
    # when the office has resolved it, however long that took (2026-10-17).
    if user.role is not Role.STUDENT and _now() - disabled_at > timedelta(days=ENABLE_WINDOW_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{user.email} was disabled on {disabled_at:%Y-%m-%d}, more than "
                f"{ENABLE_WINDOW_DAYS} days ago. Create a new account instead - "
                "re-enabling this one would restore access nobody has decided to "
                "give back."
            ),
        )

    before = {
        "disabled_at": disabled_at.isoformat(),
        "disable_reason": user.disable_reason,
    }
    user.disabled_at = None
    user.disabled_by_user_id = None
    user.disable_reason = None
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user.id, action="ENABLE",
        before=before,
        after={"disabled_at": None, "disable_reason": None},
        event_type="user.enable",
        payload={"email": user.email, "role": user.role.value},
    )
    db.commit()
    # The cached `disabled` flag would otherwise keep refusing this account for
    # the rest of the revocation window on this worker — an admin who re-enables
    # somebody and watches them still be refused reads it as the button failing.
    note_revocation(user.id, int(user.token_version or 0), disabled=False)
    log.warning("account %s (%s) ENABLED by %s", user.email, user.role.value, session.get("email"))
    return AccountStateOut(
        user_id=user.id, email=user.email, role=user.role.value, disabled=False,
        token_version=int(user.token_version or 0),
        detail=(
            f"{user.email} can sign in again."
            if user.role is Role.STUDENT
            else (
                f"{user.email} can sign in again. Capability grants were not "
                "restored - grant what is still needed in Governance."
            )
        ),
    )


@users_router.post("/{user_id}/sign-out-everywhere", response_model=AccountStateOut)
def sign_out_account_everywhere(
    user_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> AccountStateOut:
    """Retire every session an account holds, without touching its password (B3.6).

    The office's version of `POST /api/auth/sign-out-everywhere`, for the call
    that starts "I left myself signed in on the lab machine and I am at home".
    It changes nothing the person can see afterwards except that they sign in
    again - no password reset, no disable, nothing to undo - which is what makes
    it the right first move while the facts are still unclear.
    """
    require_admin(session)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    before = int(user.token_version or 0)
    user.token_version = before + 1
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="user", entity_id=user.id, action="SIGN_OUT_EVERYWHERE",
        before={"token_version": before},
        after={"token_version": user.token_version},
        event_type="user.sign_out_everywhere",
        payload={"email": user.email, "self": False},
    )
    db.commit()
    note_revocation(user.id, user.token_version, disabled=user.disabled_at is not None)
    log.info("all sessions revoked for %s by %s", user.email, session.get("email"))
    return AccountStateOut(
        user_id=user.id, email=user.email, role=user.role.value,
        disabled=user.disabled_at is not None,
        disabled_at=user.disabled_at, disable_reason=user.disable_reason,
        token_version=user.token_version,
        detail=f"Every device holding {user.email} has been signed out.",
    )


router.include_router(faculty_router)
router.include_router(users_router)
