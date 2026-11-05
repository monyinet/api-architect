"""The api_call interpreter — deterministic execution over a COMMITTED manifest.

The manifest is the source of truth: an unknown parameter or a placeholder left
unfilled fails LOCALLY, never as a remote 400. Destructive classification is by
METHOD (the caller's job); secrets resolve here, at call time, only; every
output string passes the redactor; network goes exclusively through netguard.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import Optional

try:  # importable both as a plugin package submodule and as a root-level module
    from .digest import Digest, Endpoint
    from .netguard import GuardError, guarded_fetch
    from .redactor import redact_message, sanitize_http_error
    from .secrets_bridge import resolve_secret
except ImportError:  # pragma: no cover - test/standalone import path
    from digest import Digest, Endpoint
    from netguard import GuardError, guarded_fetch
    from redactor import redact_message, sanitize_http_error
    from secrets_bridge import resolve_secret

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_PLACEHOLDER_RE = re.compile(r"\{([^/{}]+)\}")

# Transport-controlled headers (red-lens finding J): a manifest-declared header
# param (or auth location) with these names enables request-smuggling/spoofing;
# where legitimate, the interpreter sets them itself (Content-Type, Host, etc.).
_FORBIDDEN_HEADER_KEYS = frozenset(
    {
        "contentlength", "contenttype", "transferencoding", "te", "connection",
        "host", "acceptencoding", "xforwardedfor", "xforwardedhost",
        "xforwardedproto", "xforwardedport", "expect",
    }
)


class ExecutionError(Exception):
    """User-facing failure; message is already redacted and actionable."""


def find_endpoint(manifest: Digest, endpoint: str) -> Optional[Endpoint]:
    """Match by id, then by 'METHOD /path', then by a UNIQUE path match."""
    want = endpoint.strip().lower()
    if not want:
        return None
    for e in manifest.endpoints:
        if e.id.lower() == want:
            return e
    parts = want.split(None, 1)
    if len(parts) == 2 and parts[0] in [m.lower() for m in _METHODS]:
        for e in manifest.endpoints:
            if e.method.lower() == parts[0] and e.path.lower().rstrip("/") == parts[1].rstrip("/"):
                return e
    # EXACT path match only (red-lens finding G: the old fuzzy 'in/endswith' made
    # every bare input hit path "/" — confused deputy). A bare path must equal a
    # manifest path exactly; otherwise the caller offers the endpoint list.
    hits = [e for e in manifest.endpoints if e.path.lower().rstrip("/") == want.rstrip("/")]
    if len(hits) == 1:
        return hits[0]
    return None


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    low = str(v).strip().lower()
    if low in ("true", "1", "yes", "y"):
        return True
    if low in ("false", "0", "no", "n"):
        return False
    raise ValueError("not a boolean: {!r}".format(v))


def _coerce(param, v, problems: list):
    if param.enum is not None and str(v) not in [str(x) for x in param.enum]:
        problems.append("'{}' must be one of {} (got {!r})".format(param.name, param.enum, v))
        return None
    if param.type in ("integer", "number", "boolean"):
        try:
            if param.type == "integer":
                return int(v)
            if param.type == "number":
                return float(v)
            return _as_bool(v)
        except (TypeError, ValueError):
            problems.append("'{}' must be {} (got {!r})".format(param.name, param.type, v))
            return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def build_request(manifest: Digest, endpoint: Endpoint, values: dict, body: Optional[dict]) -> dict:
    """Validate ALL params against the manifest; build url/method/headers/json_body.
    No network here. Raises ExecutionError on any validation problem."""
    values = dict(values or {})
    problems: list[str] = []
    path_vals: dict[str, str] = {}
    query: list = []
    headers: dict[str, str] = {}
    body_param = None

    for p in endpoint.params:
        raw = values.pop(p.name, None)
        if raw is None:
            raw = values.pop("{" + p.name + "}", None)
        if p.location == "body":
            body_param = p
            continue  # its value comes from the caller's raw_query_body below
        if raw is None:
            if p.required:
                problems.append("'{}' ({}: {}) is required".format(p.name, p.location, p.type))
            continue
        v = _coerce(p, raw, problems)
        if v is None:
            continue
        if p.location == "path":
            path_vals[p.name] = str(v)
        elif p.location == "query":
            query.append((p.name, str(v)))
        elif p.location == "header":
            if p.name.lower().replace("-", "").replace("_", "") in _FORBIDDEN_HEADER_KEYS:
                problems.append("'{}': transport-controlled header is not allowed from the manifest".format(p.name))
            else:
                headers[p.name] = str(v)
        else:  # cookie
            problems.append("'{}': cookie params are not supported at v0".format(p.name))

    if values:
        problems.append(
            "Unknown parameter(s): {} — not declared by the manifest for this endpoint".format(", ".join(sorted(values)))
        )

    if body_param is not None:
        if body is None:
            if getattr(body_param, "required", False):
                problems.append("'{}' (body) is required: pass raw_query_body".format(getattr(body_param, "name", "body")))
        elif not isinstance(body, (dict, list)):
            problems.append("raw_query_body must be a JSON object/array")
    elif body is not None and endpoint.method == "GET":
        problems.append("raw_query_body is not accepted on a GET endpoint")
    elif body is not None:
        problems.append("This endpoint declares no body parameter — raw_query_body is not accepted (manifest is the source of truth)")

    if problems:
        raise ExecutionError("Parameter validation failed: " + "; ".join(problems))

    path = endpoint.path
    for name, val in path_vals.items():
        path = path.replace("{" + name + "}", urllib.parse.quote(val, safe=""))
    leftover = _PLACEHOLDER_RE.findall(path)
    if leftover:
        raise ExecutionError(
            "Manifest defect: path {} declares unparameterized segment(s): {} — recommit a corrected manifest".format(
                endpoint.path, ", ".join(leftover)
            )
        )

    url = manifest.base_url.rstrip("/")
    if path:
        url = url + "/" + path.lstrip("/")
    if query:
        url = url + "?" + urllib.parse.urlencode(query)

    return {"url": url, "method": endpoint.method, "headers": headers, "json_body": body if body is not None else None}


def execute(
    manifest: Digest,
    endpoint: Endpoint,
    values: dict,
    body: Optional[dict],
    timeout_s: int,
    user_agent: str,
    allow_private: bool = False,
) -> dict:
    """Full api_call execution path. Raises only ExecutionError. The destructive
    (non-GET → approval) gate is the CALLER's job; secrets resolve here, at call time."""
    req = build_request(manifest, endpoint, values, body)
    headers = dict(req["headers"])
    if user_agent:
        headers.setdefault("User-Agent", user_agent)

    secret = None
    note = ""
    if manifest.auth.type in ("bearer", "api_key_header", "api_key_query", "basic"):
        secret, note = resolve_secret(manifest.auth.secret_binding) if manifest.auth.secret_binding else (None, "unbound")
        if secret is None:
            if manifest.auth.type == "api_key_query" and manifest.auth.location:
                # API-key-in-query APIs (e.g. OpenWeather appid): a MISSING key fails
                # before the request; the credential value never touches any log.
                raise ExecutionError(
                    "This API authenticates via '{}' query parameter (slot '{}' → {}) and no "
                    "credential is configured. Fix once in plugin settings (Desktop → Settings → "
                    "Plugins → api-architect), then call again.".format(
                        manifest.auth.location, manifest.auth.secret_binding or "(none)", note or "not_set"
                    )
                )
            raise ExecutionError(
                "This API authenticates with {} (bound to secret slot '{}', currently {}) "
                "and no credential is configured. Fix once in the plugin settings (Desktop → "
                "Settings → Plugins → api-architect), then call again.".format(
                    manifest.auth.type, manifest.auth.secret_binding or "(none)", note or "not_set"
                )
            )
        if manifest.auth.type == "bearer":
            headers["Authorization"] = "Bearer " + secret
        elif manifest.auth.type == "api_key_header":
            if manifest.auth.location.lower().replace("-", "").replace("_", "") in _FORBIDDEN_HEADER_KEYS:
                raise ExecutionError(
                    "Manifest defect: auth location '{}' is a transport-controlled header — "
                    "recommit with a real credential header name.".format(manifest.auth.location)
                )
            headers[manifest.auth.location] = secret
        elif manifest.auth.type == "api_key_query":
            sep = "&" if "?" in req["url"] else "?"
            req["url"] = req["url"] + sep + urllib.parse.urlencode({manifest.auth.location: secret})
        elif manifest.auth.type == "basic":
            headers["Authorization"] = "Basic " + base64.b64encode(secret.encode("utf-8")).decode("ascii")

    payload = json.dumps(req["json_body"], ensure_ascii=False).encode("utf-8") if req["json_body"] is not None else None
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept-Encoding", "identity")  # size caps must stay predictable

    secret_values = [v for v in (secret,) if v]
    try:
        resp = guarded_fetch(
            req["url"],
            method=req["method"],
            headers=headers,
            body=payload,
            timeout_s=timeout_s,
            user_agent="",  # already set above
            allow_private=allow_private,
        )
        if resp["status"] == 401:
            return {
                "ok": False,
                "status": 401,
                "error": "The configured credential for '{}' was rejected (401). Check the slot value in the plugin settings.".format(
                    manifest.api_name
                ),
                "request": {"url": redact_message(req["url"], values=secret_values), "method": req["method"]},
            }
        text = redact_message(resp["body"].decode("utf-8", errors="replace"), values=secret_values)
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = {"_raw": text[:4000]}
        return {
            "ok": True,
            "status": resp["status"],
            "request": {"url": redact_message(req["url"], values=secret_values), "method": req["method"]},
            "response": parsed,
        }
    except GuardError as exc:
        raise ExecutionError(sanitize_http_error(str(exc), values=secret_values))
    except ExecutionError:
        raise
    except Exception as exc:
        raise ExecutionError(sanitize_http_error(str(exc), values=secret_values))
