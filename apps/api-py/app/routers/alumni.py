"""Alumni area — the profile an alumnus creates on first sign-in, and the jobs
sheet.

ALUMNI only, and their OWN row only: an alumnus is neither staff (rule 2's gate
never admits them) nor a student. That second half USED to rest on "no Student
row", and B4.4 ended it — a graduated batch keeps its `students` rows, because
they are the record of the marks, badges and interviews those people earned
here, so a graduate's session carries a perfectly valid `studentId` claim. What
refuses them the student endpoints now is the ROLE check inside
`routers/student.py::_require_student` and its twin in `student_programme.py`.
The two surfaces here are deliberately small:

  * GET/POST /alumni/profile — `created: false` from the GET is what sends the
    client to the first-login create form; the POST upserts (company required,
    resume through the same hardened document_store as student uploads).
  * GET /alumni/jobs — the postings sheet, WITHOUT the student feed's match %
    and eligibility verdict: those are computed from a Student's skills and
    marks, which an alumnus does not have. Public posting fields only, so
    rule 1 is untouched — nothing here goes near a model.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..identity import get_current_session
from ..document_store import MAX_BYTES, UploadRejected, content_disposition
from ..document_store import delete as document_store_delete
from ..document_store import QuotaRejected, VolumeQuota, read_bytes
from ..document_manifest import DocumentFacts, release, save_and_record
from ..models.alumni import AlumniProfile
from ..models.archived_document import DocumentOwnerKind
# B12.1/B12.2. The one answer to "which postings does this viewer see", shared
# with the student feed — see `app/jobs_visibility.py`.
from ..jobs_visibility import audience_for_alumnus, postings_for
from ..job_scout import job_source

router = APIRouter(prefix="/alumni", tags=["alumni"])


def require_alumni(session: dict) -> dict:
    if session.get("role") != "ALUMNI":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Alumni access required.")
    return session


class ResumeOut(BaseModel):
    original_name: str
    mime_type: str
    size_bytes: int


class ProfileOut(BaseModel):
    # False => the client shows the first-login create form. Branch on this,
    # never on a falsy company string.
    created: bool
    name: str
    email: str
    company: str | None = None
    designation: str | None = None
    joined_on: date | None = None
    graduation_year: int | None = None
    resume: ResumeOut | None = None
    updated_at: datetime | None = None


def _profile_out(session: dict, prof: AlumniProfile | None) -> ProfileOut:
    if prof is None:
        return ProfileOut(created=False, name=session["name"], email=session["email"])
    resume = None
    if prof.resume_stored_name:
        resume = ResumeOut(
            original_name=prof.resume_original_name or "resume",
            mime_type=prof.resume_mime_type or "application/octet-stream",
            size_bytes=prof.resume_size_bytes or 0,
        )
    return ProfileOut(
        created=True,
        name=session["name"],
        email=session["email"],
        company=prof.company,
        designation=prof.designation,
        joined_on=prof.joined_on,
        graduation_year=prof.graduation_year,
        resume=resume,
        updated_at=prof.updated_at,
    )


def _own_profile(session: dict, db: Session) -> AlumniProfile | None:
    return db.scalar(select(AlumniProfile).where(AlumniProfile.user_id == session["userId"]))


@router.get("/profile", response_model=ProfileOut)
def my_profile(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> ProfileOut:
    require_alumni(session)
    return _profile_out(session, _own_profile(session, db))


@router.post("/profile", response_model=ProfileOut)
def save_profile(
    company: str = Form(...),
    designation: str = Form(""),
    joined_on: str = Form(""),
    graduation_year: int | None = Form(None),
    resume: UploadFile | None = File(None),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> ProfileOut:
    """Create on first call, update after. Sync `def` for the same event-loop
    reason as every other upload endpoint (see student.create_upload).

    The resume is required on CREATE — the first-login flow's whole point is
    "company + current resume" — and optional on update, where omitting it means
    "keep the one on file".
    """
    require_alumni(session)
    company = company.strip()
    if not company:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Company is required."
        )
    if graduation_year is not None and not (1990 <= graduation_year <= datetime.now().year + 1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Graduation year looks wrong.",
        )

    prof = _own_profile(session, db)
    if prof is None and (resume is None or not resume.filename):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Upload your current resume to create your profile.",
        )

    # PARSED BEFORE ANYTHING IS STORED, AND THAT ORDER IS THE POINT.
    # A blank field clears the date; a malformed one is refused rather than
    # silently dropped, so the form cannot appear to save a value it discarded.
    # It used to be parsed AFTER the resume was stored, which was harmless until
    # `save_bytes` gained the permanent archive: the 422 below rolls the request
    # back, but the bytes have already been PUT into an Object-Locked bucket and
    # the manifest row that would have named them rolls back with the request.
    # An unnameable object, kept for a decade, because a date was mistyped.
    # This is `document_store.sniff`'s change applied to the one endpoint of the
    # six that can still raise between the store write and the commit.
    parsed_joined_on = None
    if joined_on.strip():
        try:
            parsed_joined_on = date.fromisoformat(joined_on.strip())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Date of joining must be a valid date.",
            )

    stored = None
    if resume is not None and resume.filename:
        # read(MAX+1), never read(): save_bytes refuses anything past the
        # per-file cap, so one extra byte trips it without buffering an
        # unbounded body in RAM (routers/student.py create_upload, same
        # reasoning).
        content = resume.file.read(MAX_BYTES + 1)
        try:
            # One profile, one resume, and the old bytes are deleted below
            # before the row points at the new ones -- so this owner's volume is
            # bounded at a single file by construction rather than by counting.
            # It still has to SAY so: save_bytes takes no caller that stays
            # silent about who is counting, which is how this endpoint came to
            # have no quota at all while the store's comment still claimed a
            # single writer.
            stored = save_and_record(
                db,
                content,
                quota=VolumeQuota.single_slot("resume"),
                kind=DocumentOwnerKind.ALUMNI_RESUME,
                owner_id=session["userId"],
                original_name=resume.filename or "resume",
            )  # (stored_name, mime, size)
        except QuotaRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc))
        except UploadRejected as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            )

    if prof is None:
        prof = AlumniProfile(user_id=session["userId"], company=company)
        db.add(prof)

    prof.company = company
    prof.designation = designation.strip() or None
    prof.joined_on = parsed_joined_on
    prof.graduation_year = graduation_year
    if stored is not None:
        # Replacing the resume: drop the old bytes BEFORE the row points at the
        # new ones, so a crash between the two leaves a dangling file, never a
        # row naming bytes that are gone.
        if prof.resume_stored_name:
            # THIS IS THE CASE A `deleted_at` COLUMN CANNOT EXPRESS. One
            # profile holds exactly one resume, so the superseded file is not a
            # row to flag -- it is a pointer about to be overwritten below,
            # after which nothing in the database names the old bytes at all.
            # The manifest is where they keep their name.
            #
            # Released BEFORE the unlink -- routers/student.py's delete carries
            # the full reasoning: a SELECT between an irreversible delete and
            # the commit turns any query failure into destroyed bytes plus a
            # row that survives pointing at them.
            release(
                db,
                prof.resume_stored_name,
                reason="resume replaced",
                facts=DocumentFacts(
                    kind=DocumentOwnerKind.ALUMNI_RESUME,
                    owner_id=prof.user_id,
                    original_name=prof.resume_original_name or "resume",
                    mime_type=prof.resume_mime_type or "application/pdf",
                    size_bytes=prof.resume_size_bytes or 0,
                    recorded_at=prof.created_at,
                ),
            )
            document_store_delete(prof.resume_stored_name)
        stored_name, mime, size = stored
        prof.resume_original_name = resume.filename
        prof.resume_stored_name = stored_name
        prof.resume_mime_type = mime
        prof.resume_size_bytes = size

    db.commit()
    db.refresh(prof)
    return _profile_out(session, prof)


@router.get("/profile/resume")
def download_resume(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> Response:
    require_alumni(session)
    prof = _own_profile(session, db)
    if prof is None or not prof.resume_stored_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No resume on file.")
    try:
        content = read_bytes(prof.resume_stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    return Response(
        content=content,
        media_type=prof.resume_mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(prof.resume_original_name or "resume")
        },
    )


class JobSheetRowOut(BaseModel):
    id: str
    title: str
    company: str
    degree_level: str
    location: str | None
    apply_url: str | None
    required_skills: list[str]
    closes_on: str | None
    posted_on: str | None
    #: `office`, `adzuna` or `jooble`; see `student.JobRowOut.source`.
    source: str = "office"


@router.get("/jobs", response_model=list[JobSheetRowOut])
def jobs_sheet(
    session: dict = Depends(get_current_session), db: Session = Depends(get_db)
) -> list[JobSheetRowOut]:
    """Every OPEN posting an alumnus may see.

    SCOPED THROUGH THE LINKED STUDENT ROW (B12.1) when there is one — an
    alumnus who graduated here has `alumni_profiles.student_id` filled in by the
    first-login form (B4.4) and gets their old college's board. An alumnus with
    no link has no college, no course and no track, and sees the whole open
    board, which is what this screen has always shown them. Showing them nothing
    until somebody links them would empty a working screen for the role with
    three pages in total.

    Still no match % and no eligibility verdict: those are computed from a
    Student's skills and marks, and the fact that a graduate now keeps their
    `students` row does not make their two-year-old CGPA the right thing to
    score an alumni job board against.
    """
    require_alumni(session)
    audience = audience_for_alumnus(db, session["userId"])
    return [
        JobSheetRowOut(
            id=j.id,
            title=j.title,
            company=j.company,
            degree_level=j.degree_level.value,
            location=j.location,
            apply_url=j.apply_url,
            required_skills=j.required_skills or [],
            closes_on=j.closes_on.isoformat() if j.closes_on else None,
            posted_on=j.posted_on.isoformat() if j.posted_on else None,
            source=job_source(j),
        )
        for j in db.scalars(postings_for(audience)).all()
    ]
