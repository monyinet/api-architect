"""Redactor — scrub known secret values and secret-shaped patterns out of text.

Layer of api-architect plugin (hackathon Nous, catalog rule 11 discipline):
no secret value that the plugin ever resolved may survive into model context,
logs, manifests or error strings.

Class Redactor
--------------
``Redactor(values)`` stores ONLY the non-empty values as re-escaped (regex-safe)
literal patterns — occurrences are matched case-sensitively, plus the
``urllib.parse.quote(value, safe='')`` form so a value embedded URL-encoded in a
query string still matches (the lowercase-hex percent variant is matched too).

"Bytes re-escapados" note: the parent contract says values are stored as
re-escaped bytes. This implementation keeps them as ``re.escape``-escaped *str*
patterns instead of ``re.escape(bytes)`` bytes patterns, because matching bytes
would force ``text.encode()``/``decode()`` round-trips that corrupt arbitrary
UTF-8 (surrogates, latin-1 leftovers) and the redact(text: str) -> str contract
must never mangle payload text. The security goal (literal, metachar-safe,
occurrence-exact matching) is identical.

Default value-shaped patterns (work even when the concrete value is unknown)
----------------------------------------------------------------------------
    (?i)authorization\\s*[:=]\\s*bearer\\s+\\S+   -> "authorization: [REDACTED]"
    (?i)x-api-key\\s*[:=]\\s*\\S+                 -> "x-api-key: [REDACTED]"
    (?<=[?&]appid=)[A-Za-z0-9_~.-]{16,}          -> [REDACTED]
    (?<=[?&]apikey= / api_key=)[...]{16,}        -> [REDACTED]   (two fixed-width
                                                 lookbehinds: the contract's
                                                 'api_?key' / 'access_?token'
                                                 optional-char lookbehinds do NOT
                                                 compile — CPython re requires
                                                 fixed-width lookbehind, verified
                                                 on 3.14; split per spelling)
    (?<=[?&]accesstoken= / access_token=)[...]{16,} -> [REDACTED]
    (?<=[?&]token=)[A-Za-z0-9_~.-]{16,}          -> [REDACTED]
    (?<=[?&]secret=)[A-Za-z0-9_~.-]{16,}         -> [REDACTED]
    sk-\\w{20,}                                   -> [REDACTED]   (openai-style)
    ghp_\\w{30,} / github_pat_\\w{30,}             -> [REDACTED]   (github-style)

DECLARED LIMIT: the free-text query-param patterns above only fire on values of
16+ characters. A SHORT value (e.g. 8 chars) appearing as plain ``appid=ABC12345``
in free text is NOT redacted by pattern — declare-and-accept (tested in
tests/test_redactor.py). ``redact_url()`` is stricter and independent: its
NAME-matched query params are redacted at ANY value length.

Functions
---------
- redact(text, values=())            — one-shot Redactor over text.
- redact_url(url) -> str             — structural redaction of one URL: userinfo
  ``user:pass@`` -> ``[REDACTED]@``, name-matched query/fragment param values ->
  ``[REDACTED]``. Order and untouched params survive intact (raw re-assembly,
  no re-encoding of non-touched pairs; the URL is never structurally broken).
- redact_message(payload, values=()) — safe wrapper for ANY plugin output.
- sanitize_http_error(exc, values=())->str — str(exc) through redact_message, ALWAYS.

Stdlib only (re, urllib.parse). No I/O, no os.environ, no logging: this module
must be safe to import anywhere (catalog rules 10/11).
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, quote_plus, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

# (compiled pattern, replacement template) applied in order. Replacements are
# literal templates (no backrefs), so '[REDACTED]' cannot be mangled.
_DEFAULT_PATTERNS = (
    (re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+\S+"), "authorization: " + REDACTED),
    (re.compile(r"(?i)authorization\s*[:=]\s*basic\s+\S+"), "authorization: " + REDACTED),  # red-lens E
    (re.compile(r"(?i)x-api-key\s*[:=]\s*\S+"), "x-api-key: " + REDACTED),
    (re.compile(r"(?<=[?&]appid=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    # The contract's 'api_?key' / 'access_?token' optional-char lookbehinds do
    # NOT compile in CPython re (fixed-width lookbehind requirement, verified
    # on 3.14) — split into exact-spelling pairs; semantics unchanged.
    (re.compile(r"(?<=[?&]apikey=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"(?<=[?&]api_key=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"(?<=[?&]accesstoken=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"(?<=[?&]access_token=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"(?<=[?&]token=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"(?<=[?&]secret=)[A-Za-z0-9_~.-]{16,}"), REDACTED),
    (re.compile(r"sk-\w{20,}"), REDACTED),
    (re.compile(r"ghp_\w{30,}"), REDACTED),
    (re.compile(r"github_pat_\w{30,}"), REDACTED),
)

# Query/fragment PARAM NAMES whose VALUE must be redacted in redact_url()
# (case-insensitive). Suffix variants (e.g. 'api_key_v2') covered below.
_SUSPECT_PARAM_RE = re.compile(
    r"^(?:appid|app_id|appkey|app_key|api_?key|api_?token|access_?token|access_?key|"
    r"auth|authorization|bearer|client_?secret|consumer_?key|id_?token|key|pat|"
    r"password|passwd|pwd|refresh_?token|secret|sig|signature|token|"
    r"subscription_?key|x_?api_?key)"
    r"(?:[-_.].*)?$",
    re.IGNORECASE,
)
_SUSPECT_NAME_PREFIXES = ("microsoft",)


def _normalize_value(value) -> str | None:
    """Non-empty str form of a value (bytes decoded, others str()-ed)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        s = value.decode("utf-8", "replace")
    elif isinstance(value, str):
        s = value
    else:
        s = str(value)
    return s if s else None


class Redactor:
    """Replaces occurrences of known values (case-sensitive, plus urlencoded
    forms) and the default secret-shaped patterns with '[REDACTED]'."""

    def __init__(self, values):
        pats: list[str] = []
        seen: set[str] = set()

        def _add(p: str) -> None:
            if p and p not in seen:
                seen.add(p)
                pats.append(p)

        for value in (values or ()):
            s = _normalize_value(value)
            if s is None:
                continue
            _add(re.escape(s))
            enc = quote(s, safe="")
            if enc != s and "%" in enc:
                _add(re.escape(enc))
                if enc.lower() != enc:
                    _add(re.escape(enc.lower()))
            encp = quote_plus(s, safe="")  # red-lens E/F: 'space->+' rewrite form
            if encp != s and encp != enc and ("%" in encp or "+" in encp):
                _add(re.escape(encp))
        # Longest first: a value that contains another wins without overlap leaks.
        pats.sort(key=len, reverse=True)
        self._value_re = re.compile("|".join(pats)) if pats else None
        long = [p for p in pats if len(p) >= 12]
        self._value_re_ci = re.compile("|".join(long), re.IGNORECASE) if long else None

    def redact(self, text) -> str:
        text = "" if text is None else str(text)
        if self._value_re is not None:
            text = self._value_re.sub(REDACTED, text)
        if self._value_re_ci is not None:  # red-lens F: case-normalizing servers
            text = self._value_re_ci.sub(REDACTED, text)
        for pattern, repl in _DEFAULT_PATTERNS:
            text = pattern.sub(repl, text)
        return text


def redact(text, values=()) -> str:
    """One-shot redaction: Redactor([*values]).redact(text)."""
    return Redactor(list(values or ())).redact(text)


def redact_url(url) -> str:
    """Structurally-safe redaction of a single URL.

    - userinfo 'user:pass@host' -> '[REDACTED]@host' (everything before the LAST
      '@' in the netloc is credential material).
    - query (and fragment) pairs whose param NAME matches the suspect list are
      rebuilt as 'name=[REDACTED]' at ANY value length; every other pair —
      including blank values, duplicates and odd encodings — is kept byte-for-byte
      in original order (the query is re-split/re-joined, never re-encoded).
    - On unparsable input the string passes through the generic value redactor
      and comes back otherwise unchanged.
    """
    url = "" if url is None else str(url)
    try:
        parts = urlsplit(url)
        scheme, netloc, path, query, fragment = (
            parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment,
        )
    except ValueError:
        return _apply_default_patterns(url)

    if netloc:  # userinfo: everything before the last '@'
        at = netloc.rfind("@")
        if at > 0:
            netloc = REDACTED + netloc[at:]

    if query:
        query = _redact_pairs(query)
    if fragment:
        fragment = _redact_pairs(fragment)

    if (scheme, netloc, path, query, fragment) == (
        parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment,
    ):
        return url
    return urlunsplit((scheme, netloc, path, query, fragment))


def _redact_pairs(raw: str) -> str:
    """Split on '&', redact values of suspect-named pairs, keep order/bytes."""
    out = []
    for part in raw.split("&"):
        name, sep, value = part.partition("=")
        if sep and value and _is_suspect_param(name):
            out.append(name + "=" + REDACTED)
        else:
            out.append(part)
    return "&".join(out)


def _is_suspect_param(raw_name: str) -> bool:
    name = unquote(raw_name).strip().lower()
    if not name:
        return False
    if _SUSPECT_PARAM_RE.match(name):
        return True
    return any(name.startswith(prefix) for prefix in _SUSPECT_NAME_PREFIXES)


_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_message(payload, values=()) -> str:
    """Scrub ANY plugin output before it leaves: value redaction + every URL
    found in the text passes through redact_url(). Idempotent on clean text."""
    text = redact(payload, values)
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


def sanitize_http_error(exc, values=()) -> str:
    """str(exc) through redact_message — ALWAYS (HTTPException, HTTPError, ...).
    Empty/None messages degrade to the exception class name, still sanitized."""
    if exc is None:
        msg = ""
    else:
        try:
            msg = str(exc)
        except Exception:
            msg = ""
    if not msg.strip():
        msg = type(exc).__name__ if exc is not None else "error"
    return redact_message(msg, values)


def _apply_default_patterns(text: str) -> str:
    """Redactor([], ).redact() equivalent for code paths without values."""
    text = "" if text is None else str(text)
    for pattern, repl in _DEFAULT_PATTERNS:
        text = pattern.sub(repl, text)
    return text
