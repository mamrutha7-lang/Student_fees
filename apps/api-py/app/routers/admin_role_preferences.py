"""The college's role-preference form, loaded onto the students it names.

`POST /api/admin/role-preferences/import` takes the form's response sheet
(.xlsx: a USN column and FIRST / SECOND / THIRD PREFERENCE columns) and writes
each matched student's `students.role_preferences`, first choice first. The
student's landing reads them as the career goal, with no student-side write.

Main Admin only (`require_admin`): it writes onto every student it names.
Matching is by USN, upper case on both sides (`usn_is`), and a USN the roster
does not hold is REPORTED and written nowhere -- the sheet is a public form and
nothing here creates a student. A student absent from the sheet is untouched.
"""
from __future__ import annotations

import io
from typing import Final

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..architecture_events import record_change
from ..db import get_db
from ..identity import get_current_session
from ..imports_sheet import MAX_UPLOAD_BYTES
from ..models.user import Student
from ..usn import normalise_usn, usn_is
from .mentor import require_admin

router = APIRouter(prefix="/admin/role-preferences", tags=["admin"])

MAX_ROWS: Final[int] = 5000

#: The three columns, in priority order, matched by the words in their header.
PREFERENCE_HEADERS: Final[tuple[str, ...]] = ("first preference", "second preference", "third preference")


class RolePreferenceImportOut(BaseModel):
    rows: int
    updated: int
    #: USNs on the sheet that no student on the roster holds.
    unmatched: list[str]
    #: Rows with no USN or no preference at all.
    skipped: int


def _header_index(headers: list[str]) -> tuple[int, list[int]]:
    norm = [" ".join(str(h or "").lower().split()) for h in headers]
    usn_col = next((i for i, h in enumerate(norm) if "usn" in h), -1)
    pref_cols = [next((i for i, h in enumerate(norm) if want in h), -1) for want in PREFERENCE_HEADERS]
    if usn_col < 0 or pref_cols[0] < 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The sheet needs a USN column and a FIRST PREFERENCE column.",
        )
    return usn_col, pref_cols


def parse_preferences(rows: list[tuple]) -> tuple[dict[str, list[str]], int]:
    """USN -> preferences in priority order, from the sheet's rows (header
    first). A later row for the same USN wins, as a resubmitted form should."""
    if not rows:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The sheet is empty.")
    usn_col, pref_cols = _header_index(list(rows[0]))
    found: dict[str, list[str]] = {}
    skipped = 0
    for row in rows[1:]:
        cells = list(row)
        usn = normalise_usn(str(cells[usn_col]) if usn_col < len(cells) and cells[usn_col] is not None else None)
        prefs: list[str] = []
        for col in pref_cols:
            if col < 0 or col >= len(cells) or cells[col] is None:
                continue
            text = " ".join(str(cells[col]).split())
            if text and text not in prefs:
                prefs.append(text)
        if not usn or not prefs:
            if any(c not in (None, "") for c in cells):
                skipped += 1
            continue
        found[usn] = prefs
    return found, skipped


@router.post("/import", response_model=RolePreferenceImportOut)
def import_role_preferences(
    request: Request,
    file: UploadFile = File(...),
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RolePreferenceImportOut:
    require_admin(session)
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    import openpyxl  # lazy: only this request needs a spreadsheet parser

    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That file is not an .xlsx spreadsheet."
        ) from None
    ws = wb.worksheets[0]
    rows = [r for _, r in zip(range(MAX_ROWS + 2), ws.iter_rows(values_only=True))]
    wb.close()
    if len(rows) > MAX_ROWS + 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"That sheet has more than {MAX_ROWS} rows. Split it."
        )

    found, skipped = parse_preferences(rows)
    updated = 0
    unmatched: list[str] = []
    for usn, prefs in found.items():
        student = db.scalars(select(Student).where(usn_is(Student.usn, usn))).first()
        if student is None:
            unmatched.append(usn)
            continue
        if student.role_preferences != prefs:
            student.role_preferences = prefs
            updated += 1

    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="role_preferences", entity_id="import", action="IMPORTED",
        before=None, after={"updated": updated, "unmatched": len(unmatched)},
        event_type="role_preferences.imported",
        payload={"rows": len(found), "updated": updated, "unmatched": len(unmatched)},
    )
    db.commit()
    return RolePreferenceImportOut(
        rows=len(found), updated=updated, unmatched=sorted(unmatched), skipped=skipped
    )
