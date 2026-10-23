# NOTE (2026-08): the student-facing assistant is now the realtime mock
# interviewer in app/routers/interview.py; this orchestrator no longer backs
# the assistant screen. It is RETAINED, unchanged and still mounted behind
# POST /api/agent/ask, so the swap can be rolled back by re-pointing the
# client. See docs/interview-assistant.md. Delete nothing here.

"""The REEP assistant's TOOL-BACKED ORCHESTRATOR.

The model is a *language + orchestration* layer, never the source of truth.
Every specific fact about a student comes from a read-only tool in
``app.assistant_tools`` (which runs the same code path as the student's own
screens); every policy statement comes from an APPROVED knowledge chunk. The
model only ever *phrases* what the tools return — and student PII is never sent
to a refused (off-machine) provider.

``answer_question`` is the one entry point. It:

1. Classifies the question with rule-based intent routing — no PII leaves the
   process to classify (the router only reads the question text).
2. For STUDENT-DATA intents (readiness, gaps, jobs, skills, profile, deadlines)
   it composes the answer + actions DETERMINISTICALLY from the tool result. It
   may *optionally* polish the wording through a LOCAL/allowed model, but never
   sends the record to a provider the egress gate refuses.
3. For POLICY intents it retrieves approved chunks and grounds the answer over
   ONLY that public text (remote model OK — no student PII). No hit -> an honest
   "no approved answer", never a guess.
4. For GENERAL questions it falls back to the free-form assistant.

The return shape is an ``AssistantResponse`` dict::

    {
      "answer": str,
      "actions": [{"label": str, "route": str, "reason": str}],
      "sources": [{"label": str, "type": str}],   # type: student-record | policy
      "limitations": [str],
      "intent": str,      # the intent classify() routed on (grounding signal)
      "resolved": bool,   # True iff grounded in a student tool OR an approved
                          # policy chunk; False on refuse / no-approved-answer /
                          # generic fallback
    }
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from .. import assistant_tools as tools
from .llm import (
    complete_chat,
    llm_config,
    student_data_egress_allowed,
)

log = logging.getLogger(__name__)

# --- Intents -----------------------------------------------------------------

READINESS = "readiness"
GAPS = "gaps"
JOBS = "jobs"
SKILLS = "skills"
PROFILE = "profile"
DEADLINES = "deadlines"
POLICY = "policy"
GENERAL = "general"

# The intents that read a student's private record. They only run for STUDENT
# accounts and are composed deterministically (never sent to a refused model).
STUDENT_DATA_INTENTS = frozenset({READINESS, GAPS, JOBS, SKILLS, PROFILE, DEADLINES})

# Phrases that mark a question as "explain the rules" (how-to / what-is). When a
# skill/job question is phrased this way it is POLICY, not a status lookup.
_POLICY_SIGNALS = (
    "how do i", "how do we", "how to", "how can i", "how are", "how is",
    "how does", "what is", "what does", "what are", "what documents",
    "what document", "what do i need", "steps to", "explain", "what happens",
    "how long", "what counts",
)

# A weakest-readiness-factor -> where to fix it. Guarantees the readiness answer
# can always point an action at the exact factor that is dragging the score down.
_FACTOR_ACTION: dict[str, tuple[str, str]] = {
    "CGPA": ("/student/academics", "Review your academics"),
    "Live backlogs": ("/student/academics", "Clear your live backlogs"),
    "Attendance": ("/student/academics", "Check your attendance"),
    # Uploads, not the Certification Tracker: that screen went on 2026-09-17,
    # and its own "Continue" button already sent the student here — a
    # certification counts as complete once its proof is uploaded and verified.
    "Certification completion": ("/student/uploads", "Upload a certificate"),
    "Placement profile": ("/student/profile", "Complete your placement profile"),
    "Resume profile": ("/student/resume", "Complete your resume profile"),
}

FRIENDLY_FALLBACK = (
    "I'm having trouble reaching the assistant right now — please try again in a moment."
)
NO_POLICY_ANSWER = (
    "I couldn't find an approved answer for that — please check with your mentor or "
    "the placement office."
)


# --- Intent routing (rule-based, reliable, no PII sent to classify) ----------


def classify(question: str) -> str:
    """Route a question to an intent using only its text. Order matters: the
    most specific / highest-signal intents are checked first, and the HOW/what-is
    override sends skill and job *questions* to POLICY rather than a status tool."""
    q = (question or "").lower().strip()
    if not q:
        return GENERAL

    is_how = any(sig in q for sig in _POLICY_SIGNALS)

    # 1. Placement readiness.
    if any(
        s in q
        for s in (
            "placement ready", "placement-ready", "am i ready", "readiness",
            "ready for placement", "ready to be placed", "job ready", "job-ready",
        )
    ):
        return READINESS

    # 2. Skills / verify / badge. A HOW question ("how do I verify …") is POLICY;
    #    a status question ("my skills", "am I verified") hits the skill tool. A
    #    personal phrasing ("my …") always means the student's own status.
    if any(k in q for k in ("skill", "verify", "verified", "badge")):
        personal = (
            _has_word(q, "my")
            or "i have" in q
            or "i hold" in q
            or "i've" in q
            or "am i verified" in q
        )
        if personal:
            return SKILLS
        return POLICY if is_how else SKILLS

    # 3. Completion gaps / this week / priorities. (Bare "next" is intentionally
    #    NOT a trigger — "my next certification" is a deadline, not a to-do list.)
    if (
        "what should i do" in q
        or "what should i complete" in q
        or "what should i work" in q
        or "what should i focus" in q
        or "this week" in q
        or "priorit" in q
        or "what next" in q
        or "what's next" in q
        or "whats next" in q
        or "next step" in q
        or "do next" in q
    ):
        return GAPS

    # 4. Eligible jobs (the student's own opportunities). "How do I apply" /
    #    "steps to apply" is POLICY; a bare "which jobs / can I apply" is the tool.
    if not is_how and any(
        s in q
        for s in (
            "jobs i qualify", "qualify for", "eligible", "which jobs", "what jobs",
            "jobs can i", "jobs i can", "can i apply", "should i apply", "apply",
        )
    ):
        return JOBS

    # 5. Profile completion.
    if "profile" in q or "missing" in q:
        return PROFILE

    # 6. Deadlines.
    if any(s in q for s in ("deadline", "due", "overdue", "when is", "when are",
                            "when do", "by when", "when's")):
        return DEADLINES

    # 7. Policy / how-to / what-is / documents / steps.
    if is_how or "documents" in q or "policy" in q or "leaderboard" in q:
        return POLICY

    return GENERAL


def _has_word(text: str, word: str) -> bool:
    return word in text.split()


# --- Public entry point ------------------------------------------------------


def answer_question(
    db: Session, student_id: str | None, role: str, question: str
) -> dict[str, Any]:
    """Answer `question` for the caller, grounded in tools + approved knowledge.

    Returns an ``AssistantResponse`` dict (answer / actions / sources /
    limitations / intent / resolved). Never raises for an LLM/provider failure —
    it degrades to a deterministic or friendly answer and logs the cause.

    ``intent`` is the routed intent; ``resolved`` is the grounding signal — True
    only when the answer came from a student tool or an approved policy chunk,
    False when we refused, found no approved answer, or fell back to a generic
    reply. Both are stamped onto every return path here.
    """
    intent = classify(question)
    is_student = (role == "STUDENT") and bool(student_id)

    # Personalised tools are student-only. A non-student still gets policy/general.
    if intent in STUDENT_DATA_INTENTS and not is_student:
        result = {
            "answer": (
                "Personalised insights — placement readiness, your next steps, "
                "eligible jobs, skills, profile and deadlines — are available on "
                "student accounts. I can still answer policy and how-to questions."
            ),
            "actions": [],
            "sources": [],
            "limitations": ["Personalised tools are student-only."],
            "resolved": False,  # refused — not grounded in this caller's data
        }
        result["intent"] = intent
        return result

    try:
        if intent == READINESS:
            result = _readiness(db, student_id)
        elif intent == GAPS:
            result = _gaps(db, student_id)
        elif intent == JOBS:
            result = _jobs(db, student_id)
        elif intent == SKILLS:
            result = _skills(db, student_id)
        elif intent == PROFILE:
            result = _profile(db, student_id)
        elif intent == DEADLINES:
            result = _deadlines(db, student_id)
        elif intent == POLICY:
            result = _policy(db, question)
        else:
            result = _general(db, question)
    except Exception:  # a tool/DB fault must never 500 the assistant
        log.exception("orchestrator failed (intent=%s)", intent)
        result = {
            "answer": FRIENDLY_FALLBACK,
            "actions": [],
            "sources": [],
            "limitations": ["The assistant hit an unexpected error answering this."],
            "resolved": False,
        }

    result["intent"] = intent
    return result


# --- STUDENT-DATA builders (deterministic; never leak PII to a refused model) -


def _student_source(label: str) -> dict[str, str]:
    return {"label": f"{label} (your record)", "type": "student-record"}


def _readiness(db: Session, student_id: str) -> dict[str, Any]:
    data = tools.placement_readiness(db, student_id)
    summary = data["summary"]
    factors = data.get("factors", [])
    # UNMET IS NOT UNMEASURED, and the difference is the whole quality of this
    # answer. A factor whose `measured` is false has no rows behind it — no
    # marks imported, no attendance recorded — so "Your next win: Attendance"
    # is advice to a student about a number nobody has taken, pointing them at
    # a screen where they can do nothing about it. `.get("measured", True)`
    # rather than `["measured"]`: this reads a dict built by
    # `assistant_tools.placement_readiness`, and a missing key there must not
    # turn the assistant's only student-record answer into a KeyError.
    unmet = sorted(
        (f for f in factors if f.get("measured", True) and not f["met"]),
        key=lambda f: f["weight"],
        reverse=True,
    )
    unmeasured = [f for f in factors if not f.get("measured", True)]

    # `summary` already opens with "{score}/100 — {band}. …" (or with the band
    # alone when nothing could be scored), so don't repeat it.
    if unmet:
        top = unmet[0]
        answer = f"You're {summary} Your next win: {top['label']} — {top['detail']}."
    elif data.get("score") is None:
        answer = (
            f"You're {summary} Nothing here says you are behind — it says the "
            "college has not entered your marks or attendance yet."
        )
    elif unmeasured:
        answer = (
            f"You're {summary} Every check that can be scored is met; the rest "
            "are waiting on records the college enters."
        )
    else:
        answer = f"You're {summary} Every placement check is met — keep it up."

    actions: list[dict[str, str]] = []
    for f in unmet:
        route, label = _FACTOR_ACTION.get(
            f["label"], ("/student/placement", f"Improve {f['label']}")
        )
        actions.append({"label": label, "route": route, "reason": f["detail"]})

    return _finalize(
        answer,
        actions,
        [_student_source("Placement readiness")],
        [],
        polish_ctx="placement readiness",
    )


def _gaps(db: Session, student_id: str) -> dict[str, Any]:
    gaps = tools.completion_gaps(db, student_id)
    if not gaps:
        return _finalize(
            "You're all caught up — nothing urgent on your list right now. "
            "Keep your momentum going.",
            [],
            [_student_source("Next actions")],
            [],
        )

    lines = [f"{i}. {g['title']} — {g['reason']}" for i, g in enumerate(gaps[:5], 1)]
    answer = "Here's what to focus on next:\n" + "\n".join(lines)
    actions = [
        {"label": g["title"], "route": g["cta_route"], "reason": g["reason"]}
        for g in gaps[:5]
    ]
    return _finalize(
        answer, actions, [_student_source("Next actions")], [], polish_ctx="next steps"
    )


def _jobs(db: Session, student_id: str) -> dict[str, Any]:
    jobs = tools.eligible_jobs(db, student_id)
    eligible = [j for j in jobs if j["eligible"]]

    if not jobs:
        answer = "There are no open opportunities matching your profile right now."
    elif eligible:
        named = ", ".join(f"{j['title']} at {j['company']}" for j in eligible[:4])
        answer = (
            f"You currently qualify for {len(eligible)} of {len(jobs)} open "
            f"role(s): {named}."
        )
    else:
        sample = jobs[0]
        why = "; ".join(sample.get("reasons") or []) or "you don't meet the criteria yet"
        answer = (
            f"You don't currently qualify for the {len(jobs)} open role(s). "
            f"For example, {sample['title']} at {sample['company']}: {why}."
        )

    actions = [
        {
            "label": f"Apply: {j['title']}",
            "route": "/student/jobs",
            "reason": (
                f"Eligible — {round(j['match_percent'])}% match"
                if j.get("match_percent") is not None
                else "Eligible — skills not listed"
            ),
        }
        for j in eligible[:4]
    ]
    if not actions and jobs:
        actions = [
            {
                "label": "View opportunities",
                "route": "/student/jobs",
                "reason": "See every open role and what each one needs.",
            }
        ]
    return _finalize(
        answer, actions, [_student_source("Eligible jobs")], [], polish_ctx="eligible jobs"
    )


def _skills(db: Session, student_id: str) -> dict[str, Any]:
    data = tools.skill_status(db, student_id)
    total = data["total"]
    verified = data["verified_count"]
    pending = [c for c in data.get("claims", []) if c.get("status") == "PENDING"]

    answer = f"You hold {total} skill(s), {verified} of them verified."
    if pending:
        names = ", ".join(c["skill_name"] for c in pending[:5])
        answer += f" You have {len(pending)} pending skill claim(s): {names}."
    elif total and verified < total:
        answer += " Verify the rest to strengthen your placement profile."

    actions = [
        {
            "label": "Open skilling",
            "route": "/student/skilling",
            "reason": (
                "Track and verify your skill claims."
                if pending
                else "Add a claim or evidence to get a skill verified."
            ),
        }
    ]
    return _finalize(
        answer, actions, [_student_source("Skill status")], [], polish_ctx="your skills"
    )


def _profile(db: Session, student_id: str) -> dict[str, Any]:
    data = tools.profile_completion(db, student_id)
    percent = data["percent"]
    missing = data.get("missing", [])

    if missing:
        answer = (
            f"Your placement profile is {percent}% complete. "
            f"Still missing: {', '.join(missing)}."
        )
    else:
        answer = f"Your placement profile is {percent}% complete — nothing essential is missing."

    actions = (
        [
            {
                "label": "Complete profile",
                "route": "/student/profile",
                "reason": f"Missing: {', '.join(missing)}",
            }
        ]
        if missing
        else []
    )
    return _finalize(
        answer, actions, [_student_source("Profile completion")], [], polish_ctx="your profile"
    )


def _deadlines(db: Session, student_id: str) -> dict[str, Any]:
    """What is due next, per in-flight course.

    This used to list certification due-dates and point at the Certification
    Tracker; both went on 2026-09-17 (see `tools.deadlines`). A completed course
    has nothing due, so it is left off the list.
    """
    data = tools.deadlines(db, student_id)
    courses = [
        c for c in data.get("courses", [])
        if c.get("next_task") and c["next_task"] != "Completed"
    ]

    if courses:
        lines = [f"{c['name']} — {c['next_task']}" for c in courses[:5]]
        answer = "What is due next on your courses:\n" + "\n".join(lines)
    else:
        answer = "You have no course tasks due on record."

    actions = [
        {
            "label": "View courses",
            "route": "/student/courses",
            "reason": "See each course's next task and check in.",
        }
    ]
    return _finalize(
        answer, actions, [_student_source("Deadlines")], [], polish_ctx="your deadlines"
    )


# --- POLICY builder (public content; remote model OK) ------------------------

_POLICY_SYSTEM = (
    "You are the REEP placement assistant. Answer the student's question using "
    "ONLY the approved sources provided below. Be concise and practical. If the "
    "sources do not cover the question, say you don't have an approved answer — "
    "never invent a policy, a number, or a step that is not in the sources."
)


def _policy(db: Session, question: str) -> dict[str, Any]:
    hits = tools.policy_search(db, question, audience="student")
    if not hits:
        return {
            "answer": NO_POLICY_ANSWER,
            "actions": [],
            "sources": [],
            "limitations": ["No approved knowledge source matched."],
            "resolved": False,  # no approved answer — honest fallback, not grounded
        }

    context = "\n\n".join(
        f"[{h['document_title']}]\n{h['chunk_text']}" for h in hits
    )
    messages = [
        {"role": "system", "content": _POLICY_SYSTEM},
        {
            "role": "user",
            "content": f"Question: {question}\n\nApproved sources:\n{context}",
        },
    ]

    # PUBLIC content only — carries_student_data=False, so a remote model is fine.
    try:
        answer = complete_chat(messages, carries_student_data=False, max_tokens=600).strip()
    except Exception:
        log.exception("policy grounding LLM call failed")
        # Deterministic fallback: surface the most relevant approved chunk verbatim.
        answer = hits[0]["chunk_text"].strip()

    if not answer:
        answer = hits[0]["chunk_text"].strip()

    # Dedupe source titles, preserve order.
    seen: set[str] = set()
    sources: list[dict[str, str]] = []
    for h in hits:
        title = h["document_title"]
        if title not in seen:
            seen.add(title)
            sources.append({"label": title, "type": "policy"})

    # Grounded over approved public policy chunks -> resolved.
    return {
        "answer": answer,
        "actions": [],
        "sources": sources,
        "limitations": [],
        "resolved": True,
    }


# --- GENERAL builder ---------------------------------------------------------

_GENERAL_SYSTEM = (
    "You are the REEP student assistant for a college placement dashboard. Answer "
    "clearly and concisely. You are a general helper and do NOT have access to a "
    "student's private records or to college-specific policy; for specific "
    "marks/attendance point them to their records view, and for policy point them "
    "to their mentor or the placement office."
)


def _general(db: Session, question: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _GENERAL_SYSTEM},
        {"role": "user", "content": question},
    ]
    limitations = [
        "General guidance — not drawn from your records or an approved policy source."
    ]
    try:
        answer = complete_chat(messages, carries_student_data=False, max_tokens=800).strip()
    except Exception:
        log.exception("general assistant LLM call failed")
        return {
            "answer": FRIENDLY_FALLBACK,
            "actions": [],
            "sources": [],
            "limitations": limitations,
            "resolved": False,
        }
    # General guidance is not grounded in a tool or an approved policy chunk.
    return {
        "answer": answer,
        "actions": [],
        "sources": [],
        "limitations": limitations,
        "resolved": False,
    }


# --- Optional polish (LOCAL / egress-allowed model only) ---------------------

_POLISH_SYSTEM = (
    "Rewrite the assistant's answer to be warm, encouraging and concise. You MUST "
    "preserve every number, score, percentage, name and route exactly as given. Do "
    "not add any fact that is not already present. Return only the rewritten answer."
)


def _finalize(
    answer: str,
    actions: list[dict[str, str]],
    sources: list[dict[str, str]],
    limitations: list[str],
    *,
    polish_ctx: str | None = None,
) -> dict[str, Any]:
    """Assemble the response and, ONLY when the active model is local/allowed,
    optionally polish the wording. The deterministic text is authoritative — any
    failure or a refused provider keeps it unchanged, so PII never leaks."""
    final = answer
    if polish_ctx:
        cfg = llm_config()
        if cfg is not None and student_data_egress_allowed(cfg.base_url):
            try:
                polished = complete_chat(
                    [
                        {"role": "system", "content": _POLISH_SYSTEM},
                        {"role": "user", "content": answer},
                    ],
                    carries_student_data=True,  # local/allowed only — gate re-checks
                    temperature=0.3,
                    max_tokens=500,
                ).strip()
                if polished:
                    final = polished
            except Exception:
                log.exception("optional polish failed (%s) — keeping deterministic text", polish_ctx)
    # Every _finalize caller is a student-data builder grounded in a read-only
    # tool result, so the answer is grounded -> resolved is True.
    return {
        "answer": final,
        "actions": actions,
        "sources": sources,
        "limitations": limitations,
        "resolved": True,
    }
