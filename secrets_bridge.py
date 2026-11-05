"""secrets_bridge — the ONLY credential path of the api-architect plugin.

Catalog rule 11: a Hermes plugin NEVER reads .env/auth.json/keychain/os.environ
for credentials. Every secret comes from Hermes' profile secret scope. This
module is the single place that touches it; nothing else in the plugin may
import os/secret machinery for credential purposes — the only path is agent.secret_scope (this file does not use
the os module at all).

VERIFIED IMPORT ROUTE (Hermes checkout, LOCAL EVIDENCE — git tag ``v0.22.5``,
commit bf0203a794, 2026-10-22; file agent/secret_scope.py):

    agent/secret_scope.py:230  def get_secret(name: str, default: Optional[str] = None) -> Optional[str]
    agent/secret_scope.py:126  class UnscopedSecretError(RuntimeError)

Verified semantics of get_secret (lines 230-261):
- It takes the ENV-VAR NAME (e.g. ``API_ARCHITECT_API_KEY_1``), not a slot name.
- A MISSING key NEVER raises: with a scope installed a miss returns None (or the
  caller default); single-profile (multiplex off, no scope) falls through to the
  process environment through the scope itself. -> this bridge maps a None/empty
  return to note ``'not_set'``.
- The ONLY exception route raises is ``UnscopedSecretError`` — exclusively when
  NO profile secret scope is installed WHILE multiplexing is ACTIVE and no
  launch-body scope covers the call. That is a ROUTING failure (scope not
  installed on this call path), NOT 'key absent'. -> note
  ``'route_unavailable: <one sanitized line>``. In v0.22.5 a key-absent miss
  cannot arrive as an exception, but the heuristic classifier below still
  separates clear key-absent signals (KeyError/missing/notfound style classes,
  e.g. from future or fake route implementations) into 'not_set' so this bridge
  survives that API evolution without leaking the difference to callers.

Lazy import: ``from agent.secret_scope import get_secret`` happens INSIDE the
resolve path (never at module import), the resolved callable is cached once in
the module attribute ``_get_secret`` (the test seam: tests set
``secrets_bridge._get_secret = fake`` instead of monkeypatching Hermes). The
companion exception class is cached in ``_secret_route_error``.

Hygiene guaranteed by contract:
- the resolved VALUE never appears in any note, log or traceback path here;
  notes are 'resolved' / 'not_set' / 'invalid_slot' /
  'route_unavailable: <exception, one line, length-capped, no secret data>'.
- no os.environ access anywhere in this module; no .env parsing by hand.

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

SLOT_ENV = {
    "api_key_1": "API_ARCHITECT_API_KEY_1",
    "api_key_2": "API_ARCHITECT_API_KEY_2",
}

# Test seam / one-time cache for the lazy route import. Set by _load_route();
# tests replace it directly with a fake (see tests/test_secrets_bridge.py).
_get_secret = None          # Optional[Callable[[str], Optional[str]]]
_secret_route_error = None  # Optional[type] (UnscopedSecretError)

_NOTE_CAP = 200  # chars; route notes are diagnostic lines, never payloads

# Class-name fragments that CLEARLY mean "the key itself is absent" (not a
# scope/route failure). Everything else that raises is treated as a route
# problem (fail-safe: an ambiguous exception must read as route_unavailable).
_ABSENT_CLASS_HINTS = ("keyerror", "missing", "notfound", "not_found", "absent", "unset", "notset")


def slot_env_name(slot) -> str | None:
    """Config-schema secret slot -> its env-var name; anything else -> None."""
    return SLOT_ENV.get(str(slot or ""))


def resolve_secret(slot) -> tuple[str | None, str]:
    """Resolve a declared secret slot -> (value, note).

    Returns ('<secret>', 'resolved') on success; (None, other) otherwise.
    The value NEVER gets into the note or any log — it is returned only in
    position 0 for the immediate consumer (netguard header/query builder).
    """
    env = slot_env_name(slot)
    if env is None:
        return (None, "invalid_slot")
    if _get_secret is None:
        fail_note = _load_route()
        if fail_note is not None:
            return (None, fail_note)
    try:
        raw = _get_secret(env)  # type: ignore[misc]  # ensured non-None above
    except Exception as exc:  # noqa: BLE001 — route failures ARE the contract
        note = _classify_exception(exc)
        return (None, note)
    if raw is None:
        return (None, "not_set")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    elif not isinstance(raw, str):
        raw = str(raw)
    if raw == "":
        return (None, "not_set")
    return (raw, "resolved")


def strip_secret_values(obj, values=()) -> dict:
    """Deep-scrub a manifest-like structure before persisting.

    Any STRING leaf exactly equal to one of the values becomes '[REDACTED]'.
    Non-string leaves pass untouched; dict KEYS are structural and are NOT
    rewritten (avoids accidental key collisions). Returns a NEW structure (the
    input is never mutated); with no usable values the walk result is returned
    unchanged shape-for-shape.
    """
    vals = set()
    for value in (values or ()):
        if value is None:
            continue
        if isinstance(value, bytes):
            v = value.decode("utf-8", "replace")
        elif isinstance(value, str):
            v = value
        else:
            v = str(value)
        if v:
            vals.add(v)
    return _strip_walk(obj, vals)


def _strip_walk(node, vals):
    if isinstance(node, dict):
        return {key: _strip_walk(val, vals) for key, val in node.items()}
    if isinstance(node, list):
        return [_strip_walk(item, vals) for item in node]
    if isinstance(node, tuple):
        return tuple(_strip_walk(item, vals) for item in node)
    if isinstance(node, str) and node in vals:
        return "[REDACTED]"
    return node


def _load_route() -> str | None:
    """Lazy-import the verified route. Returns a fail note, or None on success."""
    global _get_secret, _secret_route_error
    try:
        from agent.secret_scope import get_secret as _gs, UnscopedSecretError as _use
    except Exception as exc:  # ImportError or import-time failure: route down
        _get_secret = None
        _secret_route_error = None
        return "route_unavailable: import agent.secret_scope failed: " + _one_line(exc)
    _get_secret = _gs
    _secret_route_error = _use
    return None


def _classify_exception(exc) -> str:
    """(value=None, note) for a route exception. UnscopedSecretError-style
    failures (real class or any instance carrying a 'secret_name' attribute)
    are routing failures, NOT key absence. Clear key-absent classes (KeyError,
    *Missing*/NotFound) map to 'not_set'. Notes carry no secret data (one line,
    capped) and in the verified v0.22.5 route can never contain the key value."""
    line = _one_line(exc)
    if _secret_route_error is not None and isinstance(exc, _secret_route_error):
        return "route_unavailable: " + line
    if hasattr(exc, "secret_name"):  # UnscopedSecretError-familiar shape
        return "route_unavailable: " + line
    name = type(exc).__name__.lower()
    if name == "keyerror" or any(hint in name for hint in _ABSENT_CLASS_HINTS):
        return "not_set"
    return "route_unavailable: " + line


def _one_line(exc) -> str:
    """One capped, no-newline diagnostic line from an exception (no secret data:
    the scoped route never puts the value into str(exc); developer detail rides
    ``__notes__`` in the real UnscopedSecretError and is not read here)."""
    try:
        text = str(exc)
    except Exception:
        text = ""
    text = " ".join(str(text).split())  # collapse all whitespace incl. newlines
    if not text:
        text = type(exc).__name__
    return text[:_NOTE_CAP].strip() or type(exc).__name__
