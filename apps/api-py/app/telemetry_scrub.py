"""Last-line PII scrubbing for Sentry events, transactions, breadcrumbs and logs.

Rule 1 applied to telemetry. The three constructor flags in app/observability.py
cover frame locals, request bodies and cookies. NOTHING covers the query string,
and nothing covers a log record whose formatted message IS the secret. Those are
carried by mechanisms the SDK enables by default, so they must be removed by a
hook rather than by an option a future SDK release can rename underneath us.

Why a query string is the first carrier this file names: in sentry-sdk 2.68.1
the ASGI integration filters the query string ONLY when
`_experiments["data_collection"]` is set, so `GET /api/register/verify?token=`
(a live, single-use, account-provisioning token) and
`GET /api/auth/sso/google/callback?code=&state=` (a Google authorization code)
ship verbatim on error events AND on sampled transactions — request data is
attached by an event processor and those run for transactions too. A token does
not need an exception to leak; it needs a dice roll on a successful 302.

No sentry_sdk import: these are plain dict transforms, so the tests run with no
DSN, no network and no database, and the functions can be called on any payload.
Every public function is total — a malformed payload comes back unchanged rather
than raising, because a scrubber that raises inside the SDK's hook drops the
event, which is the safe direction but not a reason to be sloppy.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .redaction import redact_pii

#: The SDK's own SENSITIVE_DATA_SUBSTITUTE, spelled the same so a filtered value
#: reads the same whichever layer filtered it.
FILTERED = "[Filtered]"

#: An ALLOWLIST, not a denylist, because a denylist has to be right about the
#: parameter the NEXT feature adds. These are the ones this repo actually sends,
#: enumerated — each is an enumeration or a small integer, never free text.
#: Regenerate with:
#:     grep -rnoE "[?&][a-zA-Z_]+=" apps/web/src
#:     grep -rn "Query(" apps/api-py/app/routers apps/api-py/app/voice_platform
#:     grep -rn "query_params.get" apps/api-py/app
#:
#: DELIBERATELY ABSENT, each for its own reason:
#:   token, code, state — credentials (registration.py verify, auth.py callback).
#:   q               — free text a person typed (agent.py knowledge search,
#:                     platform admin candidate search).
#:   next            — auth.guard.ts sets it to `state.url`, the WHOLE url of
#:                     the route that bounced, query string included.
QUERY_ALLOW = frozenset(
    {
        "board",  # /student/leaderboards?board=
        "day",  # /student/ledger?day=
        "days",  # /student/timesheet?days=, mentee_records.py
        "degree",  # /ws/media-bridge?degree=UG
        "download",  # /interviews/{id}/audio?download=1
        "error",  # /login?error=<sso code>
        "include_inactive",
        "limit",
        "mode",
        "specialization",
        "status",
        "track",  # ?track=mixed|student|assistant
        "verified",  # /login?verified=1|0
        "why",  # /login?verified=0&why=expired_or_used
    }
)

#: Credentials a provider takes IN THE URL PATH, where `filter_query` cannot see
#: them. Jooble's job search is `POST https://jooble.org/api/<key>`
#: (app/job_sources.py), so the key is the last path segment, and the SDK's
#: httpx integration writes that URL into a breadcrumb and an `http.client`
#: span on every call the daily job agent makes. The match keeps the host and
#: the path prefix so a trace still says which provider was slow.
_SECRET_PATH_SEGMENTS = (re.compile(r"(jooble\.org/api/)[^/?#\s'\"]+", re.IGNORECASE),)

#: Loggers whose records ARE the sensitive thing rather than a description of
#: it. app/mail_transport.py logs the complete outbound message body at INFO
#: when no transport is configured — the raw activation link, the raw reset
#: link, the one-time code — and every INFO record becomes a breadcrumb on the
#: next captured event. That log line is how a developer reads the link off a
#: laptop, so it stays; this is the layer that keeps it off a third party.
#:
#: The cost is honest: the SES-failure line uses the same logger, so muting the
#: module also mutes the breadcrumb an operator wants when a student says the
#: link never arrived. The MailLog row and the CloudWatch line still carry it.
#: `app.leave_mail` joins it for a related but not identical reason (B10.5). It
#: composes the body one frame above `app.mail_transport`, about a LEAVE
#: REQUEST — whose `reason` is the form's "Purpose" cell, free text and
#: routinely medical. The module deliberately keeps that field out of every
#: message it builds, and the mute is the layer that holds when somebody adds a
#: debug line while chasing "why did this mail not go".
MUTED_LOGGERS = frozenset({"app.mail_transport", "app.leave_mail", "app.badge_mail"})

#: Header names the SDK already substitutes under send_default_pii=False. They
#: are removed here as well so the invariant holds if that flag is ever changed
#: or an integration attaches headers of its own.
SENSITIVE_HEADERS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-forwarded-for",
        "x-real-ip",
    }
)

#: A key whose VALUE is a credential by construction. Matched case-insensitively
#: against dict keys in `extra`, `contexts` and log attributes; the value is
#: replaced whole. `dsn` is on the list because a misconfiguration exception
#: happily embeds the DSN in its message and someone will `extra=` it.
_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|jwt|bearer|dsn)",
    re.IGNORECASE,
)

#: How deep the recursive string redaction walks. Sentry's own payloads are
#: shallow; this exists so a pathological `extra` cannot make the hook slow.
_MAX_DEPTH = 6


# --------------------------------------------------------------------------- #
# URL and query string
# --------------------------------------------------------------------------- #


def filter_query(qs: str | None) -> str | None:
    """Keep the screen selectors, blank everything else.

    `board=cgpa` survives so a trace can still say which leaderboard was slow;
    `token=…` comes back as `token=[Filtered]` so the key is visible and the
    value is not. Deleting the whole string would pass a PII test and make
    "interview hr vs interview generic" unanswerable — which is the
    observability this instrumentation was added for.
    """
    if not qs:
        return qs
    try:
        pairs = parse_qsl(qs, keep_blank_values=True)
    except Exception:  # noqa: BLE001 - a string parse_qsl cannot read is not worth an event
        return FILTERED
    return urlencode([(k, v if k in QUERY_ALLOW else FILTERED) for k, v in pairs])


def redact_secret_paths(text: str) -> str:
    """Blank every path segment `_SECRET_PATH_SEGMENTS` names, in any string."""
    for pattern in _SECRET_PATH_SEGMENTS:
        text = pattern.sub(lambda m: m.group(1) + FILTERED, text)
    return text


def filter_url(url: str | None) -> str | None:
    """The browser SDK sets request.url from location.href, so the SPA's
    /reset?token=... arrives here whole. The API's own URL extraction excludes
    the query string, so this is a no-op there; filtering both costs one branch
    and removes the distinction.

    The fragment goes too. Angular routes by path, so nothing is lost — and a
    fragment is one more place a link could carry a secret."""
    if not url:
        return url
    url = redact_secret_paths(url)
    if "?" not in url and "#" not in url:
        return url
    try:
        parts = urlsplit(url)
    except Exception:  # noqa: BLE001
        return FILTERED
    return urlunsplit(parts._replace(query=filter_query(parts.query) or "", fragment=""))


# --------------------------------------------------------------------------- #
# Recursive value redaction
# --------------------------------------------------------------------------- #


def _redact_value(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return FILTERED
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                out[key] = FILTERED
            else:
                out[key] = _redact_value(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in value]
    return value


def redact_params(params: Any) -> Any:
    """`record.args`, verbatim: a tuple for %s-style, a Mapping for %(name)s."""
    return _redact_value(params)


# --------------------------------------------------------------------------- #
# The event
# --------------------------------------------------------------------------- #


def strip_request_secrets(request: dict[str, Any]) -> None:
    """Cookies, the body and the credential headers — removed in place.

    `max_request_body_size="never"` and `send_default_pii=False` already keep
    these out. Popped anyway: this hook is the layer that still holds if either
    flag is ever dropped, and the test for it does not need a live SDK.
    """
    request.pop("cookies", None)
    request.pop("data", None)
    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in list(headers):
            if isinstance(name, str) and name.lower() in SENSITIVE_HEADERS:
                headers[name] = FILTERED
    env = request.get("env")
    if isinstance(env, dict):
        # REMOTE_ADDR is the client IP; the SDK omits it under
        # send_default_pii=False, and this is the backstop.
        env.pop("REMOTE_ADDR", None)
    if "query_string" in request:
        request["query_string"] = filter_query(request.get("query_string"))
    if "url" in request:
        request["url"] = filter_url(request.get("url"))


def strip_frame_vars(event: dict[str, Any]) -> None:
    """The include_local_variables invariant, enforced rather than configured."""
    for entry in (event.get("exception") or {}).get("values") or []:
        if not isinstance(entry, dict):
            continue
        for frame in (entry.get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict):
                frame.pop("vars", None)
    for thread in (event.get("threads") or {}).get("values") or []:
        if not isinstance(thread, dict):
            continue
        for frame in (thread.get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def _scrub_logentry(event: dict[str, Any]) -> None:
    """THREE fields, not one. The logging integration writes
    {"message": <raw template>, "formatted": record.getMessage(), "params": record.args}.
    Scrubbing `formatted` alone leaves the value sitting one key over,
    uninterpolated — app/routers/auth.py has a log.error whose args are an
    identity's email and Google sub, and ERROR is a captured EVENT. On this
    deployment the email IS the USN."""
    logentry = event.get("logentry")
    if not isinstance(logentry, dict):
        return
    for key in ("formatted", "message"):
        if isinstance(logentry.get(key), str):
            logentry[key] = redact_pii(logentry[key])
    if "params" in logentry:
        logentry["params"] = redact_params(logentry["params"])


def _scrub_exception_messages(event: dict[str, Any]) -> None:
    """An exception MESSAGE is not a local, not a body, not a header.
    `ValueError(f"could not parse {transcript!r}")` ships whole; redact_pii
    takes the identifiers out of it, and max_value_length bounds the rest."""
    for entry in (event.get("exception") or {}).get("values") or []:
        if isinstance(entry, dict) and isinstance(entry.get("value"), str):
            entry["value"] = redact_pii(entry["value"])
    message = event.get("message")
    if isinstance(message, str):
        event["message"] = redact_pii(message)


def scrub_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """before_breadcrumb. Returning None DROPS the breadcrumb.

    `category` is the LOGGER NAME for a logging breadcrumb — the integration
    sets `"category": record.name` — which is what makes MUTED_LOGGERS work."""
    if not isinstance(crumb, dict):
        return crumb
    if crumb.get("category") in MUTED_LOGGERS:
        return None
    message = crumb.get("message")
    if isinstance(message, str):
        # redact_pii already knows this deployment's three shapes — email,
        # phone, 10-char VTU USN — and the email shape is the USN shape here.
        crumb["message"] = redact_pii(message)
    data = crumb.get("data")
    if isinstance(data, dict):
        # 'from'/'to' on a navigation breadcrumb are path AND query AND fragment.
        for key in ("from", "to", "url"):
            if isinstance(data.get(key), str):
                data[key] = filter_url(data[key])
        # The httpx integration splits an outbound URL: `url` above has no
        # query, and the raw query rides here. Adzuna takes its key pair as
        # `?app_id=&app_key=`, so this is where an outbound credential sits.
        if isinstance(data.get("http.query"), str):
            data["http.query"] = filter_query(data["http.query"])
        for key in list(data):
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                data[key] = FILTERED
    return crumb


def _scrub_breadcrumbs_in_event(event: dict[str, Any]) -> None:
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
    if not isinstance(values, list):
        return
    kept = [c for c in (scrub_breadcrumb(c) for c in values) if c is not None]
    if isinstance(crumbs, dict):
        crumbs["values"] = kept
    else:
        event["breadcrumbs"] = kept


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """before_send. Error and message events: everything below applies.

    Order matters only in that the request block is handled first, because it
    is the one carrier with a working credential in it."""
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        strip_request_secrets(request)
    strip_frame_vars(event)
    _scrub_logentry(event)
    _scrub_exception_messages(event)
    _scrub_breadcrumbs_in_event(event)
    if isinstance(event.get("extra"), dict):
        event["extra"] = _redact_value(event["extra"])
    # `user` is never set by this codebase (send_default_pii=False keeps the
    # SDK from setting it either). If a future call site does, it goes.
    event.pop("user", None)
    return event


def _scrub_outbound_http_spans(event: dict[str, Any]) -> None:
    """The one span this codebase does not write itself: the SDK's httpx
    integration records every OUTBOUND call as an `http.client` span whose
    description and `url` carry the path and whose `http.query` carries the raw
    query string. Only those spans are touched, so the cost on the interview's
    couple of hundred spans is one string comparison each."""
    spans = event.get("spans")
    if not isinstance(spans, list):
        return
    for span in spans:
        if not isinstance(span, dict) or not str(span.get("op") or "").startswith("http"):
            continue
        if isinstance(span.get("description"), str):
            span["description"] = redact_secret_paths(span["description"])
        data = span.get("data")
        if isinstance(data, dict):
            if isinstance(data.get("url"), str):
                data["url"] = filter_url(data["url"])
            if isinstance(data.get("http.query"), str):
                data["http.query"] = filter_query(data["http.query"])


def scrub_transaction(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """before_send_transaction. A sampled 200 carries request data too, and the
    interview transaction carries a couple of hundred spans — so this touches
    the request block, the breadcrumbs and the user, and walks only the
    OUTBOUND http spans: span data this codebase writes itself is names, counts
    and identifiers by contract (tests/test_tracing.py pins it), and the SDK's
    httpx spans are the one kind it does not write."""
    if not isinstance(event, dict):
        return event
    request = event.get("request")
    if isinstance(request, dict):
        strip_request_secrets(request)
    _scrub_breadcrumbs_in_event(event)
    _scrub_outbound_http_spans(event)
    event.pop("user", None)
    return event


def scrub_log(log: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """before_send_log — the structured-logs pipeline, which is OFF by default
    (SENTRY_LOGS_ENABLED) and bypasses before_breadcrumb entirely. Redacts the
    body and every string attribute; drops a record from a muted logger."""
    if not isinstance(log, dict):
        return log
    attributes = log.get("attributes")
    if isinstance(attributes, dict):
        logger_name = attributes.get("logger.name")
        if isinstance(logger_name, dict):  # {"value": ..., "type": ...} form
            logger_name = logger_name.get("value")
        if logger_name in MUTED_LOGGERS:
            return None
        log["attributes"] = _redact_value(attributes)
    if isinstance(log.get("body"), str):
        log["body"] = redact_pii(log["body"])
    return log


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #


def stamp_tags(event: dict[str, Any], **tags: str) -> dict[str, Any]:
    """Guarantee low-cardinality tags on the event itself, whatever the scope
    said. Tags are a dict at this point in the client (the scope has applied
    its own); a list of pairs is tolerated because that is the wire shape."""
    if not isinstance(event, dict):
        return event
    current = event.get("tags")
    if isinstance(current, dict):
        for key, value in tags.items():
            current.setdefault(key, value)
    elif isinstance(current, list):
        present = {pair[0] for pair in current if isinstance(pair, (list, tuple)) and pair}
        current.extend([key, value] for key, value in tags.items() if key not in present)
    else:
        event["tags"] = dict(tags)
    return event
