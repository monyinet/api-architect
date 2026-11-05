"""OpenAPI 3 (JSON) -> Digest v1 compiler. Lane L1 of the ingestion split (2026-11-05).

Public surface (frozen call contract, see red-blue-plugin-pivot.md):
- ParseSkip: raised by the ingestion CALLER when detect_spec() returns None
  (document that is not an interpretable spec); digest_from_openapi3 itself
  NEVER raises — broken or unsupported docs come back as (None, warnings+[...]).
- detect_spec(obj) -> 'openapi3' | 'swagger2' | 'postman' | None.
- digest_from_openapi3(obj, source_url, api_name='') -> (Digest|None, warnings).
  A Digest is only returned when Digest.valid() passes (provenance gate etc.);
  failures are reported honestly, never patched up.

Anti-fabrication guarantees honored here:
- Every endpoint carries provenance {'source': 'openapi-path-item',
  'detail': '/paths/<json-pointer-escaped-path>/<method>'} — no exceptions.
- base_url is never invented: absolute server urls are used as-is, relative
  ones are joined with the fetch origin, each substitution reported as a
  warning so the user can audit what was assumed.
- Only what the spec says exists. Unknown/deep constructs ($ref chains,
  requestBodies refs, oauth2/OpenID) are skipped or downgraded WITH a warning,
  not silently faked.
"""
from __future__ import annotations

import datetime
import json
import re
from collections.abc import Mapping
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

try:  # importable both as a plugin package submodule and as a root-level module
    from . import digest as digest_mod
    from .digest import Auth, Digest, Endpoint, Param, Provenance, slugify
except ImportError:  # pragma: no cover - test/standalone import path
    import digest as digest_mod
    from digest import Auth, Digest, Endpoint, Param, Provenance, slugify

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
MAX_ENDPOINTS = 40
MAX_ENUM_ITEMS = 12
MAX_BODY_EXAMPLE_BYTES = 4096
_ALLOWED_LOCATIONS = ("path", "query", "header", "body", "cookie")
_COMPONENT_REF_RE = re.compile(r"^#/components/(parameters|schemas)/([^/]+)$")


class ParseSkip(Exception):
    """The document cannot be interpreted as a supported spec.

    Raised by the ingestion caller (spec-guard stage) when detect_spec() is
    None; the caller turns it into an actionable message for the LLM/user.
    digest_from_openapi3 itself never raises it — it reports (None, warnings).
    """


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
def detect_spec(obj: Any) -> Optional[str]:
    """Classify a parsed JSON document. None => not a spec we can compile
    (the caller should raise ParseSkip). Non-dict input is None, never a raise."""
    if not isinstance(obj, Mapping):
        return None
    if str(obj.get("openapi", "") or "").startswith("3"):
        return "openapi3"
    if str(obj.get("swagger", "") or "").startswith("2."):
        return "swagger2"
    info = obj.get("info")
    if isinstance(info, Mapping):
        if str(info.get("_postman_id", "") or ""):
            return "postman"
        if str(info.get("schema", "") or "").endswith("v2.1.0/collection.json"):
            return "postman"
    return None


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _clip(text: str, max_len: int = 140) -> str:
    return str(text or "").strip()[:max_len]


def _pointer_escape(path: str) -> str:
    """JSON Pointer escaping of a path key ('~' -> '~0', '/' -> '~1')."""
    return str(path).replace("~", "~0").replace("/", "~1")


def _pointer_unescape(name: str) -> str:
    return str(name).replace("~1", "/").replace("~0", "~")


def _http_origin(source_url: Any) -> str:
    """scheme://host[:port] of source_url, or '' when it is not an http(s) URL."""
    try:
        parts = urlparse(str(source_url or ""))
    except ValueError:
        return ""
    if parts.scheme in ("http", "https") and parts.netloc:
        return "{}://{}".format(parts.scheme.lower(), parts.netloc)
    return ""


def _submap(d: Mapping) -> Mapping:
    """Guard for optional nested mappings."""
    return d if isinstance(d, Mapping) else {}


def _resolve_component_ref(node: Any, root: Mapping, collection: str) -> Tuple[Optional[dict], str]:
    """ONE level of local $ref resolution into components.<collection>/<Name>.

    Returns (target, note): note == '' on success; otherwise a human explanation
    of why the item cannot be resolved (caller should skip it and warn).
    Deliberately limited to exactly one level — nested $ref chains and refs to
    other collections (requestBodies, external files) are not followed in v0.
    """
    ref = str(node.get("$ref") or "").strip() if isinstance(node, Mapping) else ""
    if not ref:
        return (dict(node) if isinstance(node, Mapping) else None), ""
    match = _COMPONENT_REF_RE.match(ref)
    if match is None:
        return None, "external or non-components $ref '{}'".format(ref)
    got_collection, raw_name = match.group(1), _pointer_unescape(match.group(2))
    if got_collection != collection:
        return None, "$ref '{}' points into components.{} (expected {})".format(ref, got_collection, collection)
    comps = _submap(_submap(root).get("components"))
    bucket = _submap(comps.get(collection))
    target = bucket.get(raw_name)
    if not isinstance(target, Mapping):
        return None, "unresolvable $ref '{}' (components.{}/'{}' missing or not an object)".format(ref, collection, raw_name)
    if str(target.get("$ref") or ""):
        return None, "nested $ref '{}' inside '{}' not followed (one level only in v0)".format(ref, raw_name)
    return dict(target), ""


def _copy_enum(schema: Mapping) -> Optional[List[Any]]:
    """Copy schema.enum when it is a list of strings/numbers with <=12 items."""
    values = schema.get("enum") if isinstance(schema, Mapping) else None
    if not isinstance(values, list) or not values or len(values) > MAX_ENUM_ITEMS:
        return None
    if all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in values):
        return list(values)
    return None


# --------------------------------------------------------------------------- #
# base_url
# --------------------------------------------------------------------------- #
def _resolve_base_url(obj: Mapping, source_url: Any) -> Tuple[str, List[str]]:
    """base_url from root servers[0].url (with server-variable defaults), else
    from the fetch origin. Never fabricated: every step away from the literal
    spec is reported as a warning. Returns ('', warnings) on true impossibility."""
    warnings: List[str] = []
    servers = obj.get("servers")
    server_url = ""
    if isinstance(servers, list) and servers and isinstance(servers[0], Mapping):
        raw = servers[0]
        server_url = str(raw.get("url", "") or "").strip()
        # OpenAPI 3 spells this 'variables'; the contract also names it
        # 'serverVariables' — both are accepted at the server-object level.
        variables: Any = raw.get("variables")
        if not isinstance(variables, Mapping):
            variables = raw.get("serverVariables")
        if server_url and isinstance(variables, Mapping):
            for name, definition in variables.items():
                default = definition.get("default", "") if isinstance(definition, Mapping) else ""
                placeholder = "{" + str(name) + "}"
                if str(default or ""):
                    server_url = server_url.replace(placeholder, str(default))
                elif placeholder in server_url:
                    warnings.append(
                        "server variable '{}' has no default — placeholder '{}' left as-is".format(name, placeholder)
                    )
    if server_url:
        parts = urlparse(server_url)
        if parts.scheme in ("http", "https") and parts.netloc:
            for placeholder in re.findall(r"\{[^{}]+\}", server_url):
                warnings.append("server url still contains unresolved placeholder {}".format(placeholder))
            return server_url, warnings
        origin = _http_origin(source_url)
        if origin:
            joined = origin + (server_url if server_url.startswith("/") else "/" + server_url)
            warnings.append(
                "server url '{}' is relative — joined with the fetch origin '{}' as base_url".format(server_url, joined)
            )
            return joined, warnings
        warnings.append(
            "cannot resolve relative server url '{}': source_url '{}' has no http(s) origin".format(server_url, source_url)
        )
        return "", warnings
    origin = _http_origin(source_url)
    if origin:
        warnings.append(
            "no servers[].url in the spec — using the fetch origin '{}' as base_url (an API path prefix, if any, is unknown)".format(origin)
        )
        return origin, warnings
    warnings.append("no usable servers[] and source_url '{}' has no http(s) origin — base_url unavailable".format(source_url))
    return "", warnings


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def _auth_from_scheme(name: str, scheme: Mapping, warnings: List[str]) -> Auth:
    stype = str(scheme.get("type", "") or "").strip().lower()
    if stype == "http":
        http_scheme = str(scheme.get("scheme", "") or "").strip().lower()
        if http_scheme == "bearer":
            return Auth(type="bearer")
        if http_scheme == "basic":
            return Auth(type="basic")
        warnings.append(
            "securityScheme '{}' uses http scheme '{}' — http auth alternatives are out of v0 scope; auth=none".format(name, http_scheme or "?")
        )
        return Auth(type="none")
    if stype == "apikey":
        loc = str(scheme.get("in", "") or "").strip().lower()
        location = str(scheme.get("name", "") or "").strip() or name
        if loc == "query":
            return Auth(type="api_key_query", location=location)
        if loc == "header":
            return Auth(type="api_key_header", location=location)
        warnings.append(
            "securityScheme '{}' is an apiKey in '{}' — cookie api keys are out of v0 scope; auth=none".format(name, loc or "?")
        )
        return Auth(type="none")
    if stype in ("oauth2", "openidconnect"):
        warnings.append(
            "securityScheme '{}' uses {} — OAuth/OpenID flows are out of scope for v0; auth=none "
            "(endpoints needing it will run without credentials and may be rejected by the server)".format(
                name, "oauth2" if stype == "oauth2" else "openIdConnect"
            )
        )
        return Auth(type="none")
    warnings.append("securityScheme '{}' has unsupported type '{}' — auth=none".format(name, stype or "?"))
    return Auth(type="none")


def _resolve_auth(obj: Mapping, warnings: List[str]) -> Auth:
    """First security scheme referenced by the global 'security' list, else
    none. 'security' empty + declared schemes => none + one subtle warning."""
    root = _submap(obj)
    schemes = _submap(_submap(_submap(root).get("components")).get("securitySchemes"))
    security = root.get("security") if isinstance(root.get("security"), list) else []
    for requirement in security:
        if not isinstance(requirement, Mapping) or not requirement:
            continue
        for name in requirement:
            scheme = schemes.get(name)
            if isinstance(scheme, Mapping):
                return _auth_from_scheme(name, scheme, warnings)
        referenced = ", ".join(str(k) for k in list(requirement)[:4])
        warnings.append("security requirement references '{}' but it is not declared in components.securitySchemes — auth=none".format(referenced))
    if not security and schemes:
        warnings.append(
            "top-level 'security' is empty — declared securityScheme(s) {} are unreferenced; auth=none "
            "(bind a slot at commit if the API actually needs one of them)".format(", ".join(sorted(schemes)))
        )
    return Auth(type="none")


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
def _extract_body_example(media: Mapping) -> Optional[Any]:
    """example / first examples[..].value from a media type, only when it is
    JSON-serializable and <= MAX_BODY_EXAMPLE_BYTES; otherwise None."""
    if not isinstance(media, Mapping):
        return None
    candidate = media.get("example")
    if candidate is None:
        examples = media.get("examples")
        if isinstance(examples, Mapping) and examples:
            first = next(iter(examples.values()), None)
            if isinstance(first, Mapping):
                candidate = first.get("value")
    if candidate is None:
        return None
    try:
        blob = json.dumps(candidate, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(blob.encode("utf-8")) > MAX_BODY_EXAMPLE_BYTES:
        return None
    return candidate


def _collect_params(
    root: Mapping,
    raw_params: List[Any],
    operation_where: str,
    warnings: List[str],
    cookies: List[str],
    already_seen: "set[Tuple[str, str]]",
) -> List[Param]:
    """Resolve ($ref, one level) + compile a parameters list. Returns the
    compiled params; skips (with warnings) cookie params and broken ones."""
    compiled: List[Param] = []
    for raw in raw_params:
        if not isinstance(raw, Mapping):
            warnings.append("malformed parameter at {} (not an object) — skipped".format(operation_where))
            continue
        node, note = _resolve_component_ref(raw, root, "parameters")
        if note:
            warnings.append("parameter at {} skipped: {}".format(operation_where, note))
            continue
        if node is None:
            warnings.append("malformed parameter at {} (not an object) — skipped".format(operation_where))
            continue
        name = str(node.get("name", "") or "").strip()
        location = str(node.get("in", "") or "").strip().lower()
        if not name or not location:
            warnings.append("parameter at {} skipped: missing name/in".format(operation_where))
            continue
        if location == "cookie":
            cookies.append(name)
            continue
        if location not in _ALLOWED_LOCATIONS:
            warnings.append("parameter '{}' at {} has unsupported location '{}' — skipped".format(name, operation_where, location))
            continue
        key = (name, location)
        if key in already_seen:
            continue  # operation-level parameter overrides the path-item one
        already_seen.add(key)
        schema = _submap(node.get("schema"))
        ptype = str(schema.get("type", "") or "") or str(node.get("type", "") or "") or "string"
        description = str(node.get("description", "") or "").strip()
        if node.get("deprecated") is True:
            description = "[deprecated] " + description
        compiled.append(
            Param(
                name=name,
                location=location,
                type=ptype,
                required=bool(node.get("required", False)),  # path is forced below
                description=description,
                enum=_copy_enum(schema),
            )
        )
    return compiled


def _compile_endpoints(root: Mapping, warnings: List[str]) -> List[Endpoint]:
    paths = root.get("paths")
    if not isinstance(paths, Mapping):
        warnings.append(
            "no usable 'paths' mapping (type {}) — 0 endpoints compiled".format(type(paths).__name__ if paths is not None else "absent")
        )
        return []
    endpoints: List[Endpoint] = []
    cookies: List[str] = []
    seen_ids = {}
    total_ops = 0
    truncated = False
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            warnings.append("path item '{}' is not a mapping — skipped".format(path))
            continue
        path_item_params = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for key in [k for k in item if isinstance(k, str) and k.lower() in HTTP_METHODS]:
            total_ops += 1
            if len(endpoints) >= MAX_ENDPOINTS:
                truncated = True
                continue
            method_l = key.lower()
            op = item[key] if isinstance(item[key], Mapping) else {}
            where = "/paths/{}/{}".format(_pointer_escape(str(path)), method_l)
            base_slug = digest_mod.slugify(method_l.upper() + " " + str(path))
            if base_slug in seen_ids:
                seen_ids[base_slug] += 1
                ep_id = "{}-{}".format(base_slug, seen_ids[base_slug])
            else:
                seen_ids[base_slug] = 1
                ep_id = base_slug

            seen_locations = set()
            op_params = op.get("parameters") if isinstance(op.get("parameters"), list) else []
            params = _collect_params(root, op_params, where, warnings, cookies, seen_locations)
            params += _collect_params(root, path_item_params, where, warnings, cookies, seen_locations)
            for param in params:
                if param.location == "path":
                    param.required = True  # a path placeholder is always required, spec can not opt it out

            request_example: Optional[Any] = None
            if method_l != "get" and isinstance(op.get("requestBody"), Mapping):
                request_body = op["requestBody"]
                ref_note = str(request_body.get("$ref") or "")
                if ref_note:
                    warnings.append(
                        "requestBody $ref '{}' at {} not followed (components.requestBodies are out of v0 scope) — no body parameter compiled".format(
                            ref_note, where
                        )
                    )
                else:
                    content = _submap(request_body.get("content"))
                    json_key = next(
                        (k for k in content if isinstance(k, str) and k.split(";")[0].strip().lower() == "application/json"),
                        None,
                    )
                    if json_key is not None and isinstance(content[json_key], Mapping):
                        media = content[json_key]
                        schema = media.get("schema")
                        schema_dict: Optional[Mapping] = None
                        if isinstance(schema, Mapping):
                            if schema.get("$ref"):
                                schema_dict, note = _resolve_component_ref(schema, root, "schemas")
                                if note:
                                    warnings.append("requestBody schema at {} skipped: {}".format(where, note))
                                    schema_dict = None
                            else:
                                schema_dict = schema
                        if schema_dict is not None and (schema_dict.get("type") == "object" or "properties" in schema_dict):
                            description = str(schema_dict.get("description", "") or "").strip() or str(
                                request_body.get("description", "") or ""
                            ).strip()
                            params.append(
                                Param(
                                    name="body",
                                    location="body",
                                    type="object",
                                    required=bool(request_body.get("required", False)),
                                    description=description,
                                )
                            )
                        request_example = _extract_body_example(media)

            summary = str(op.get("summary", "") or "").strip() or _clip(op.get("description", ""))
            endpoints.append(
                Endpoint(
                    id=ep_id,
                    method=method_l.upper(),
                    path=str(path),
                    summary=summary,
                    params=params,
                    request_example=request_example,
                    provenance=Provenance(source="openapi-path-item", detail=where),
                    operation_id=str(op.get("operationId") or "").strip() or None,
                )
            )
    if cookies:
        shown = ", ".join(cookies[:6]) + (", ..." if len(cookies) > 6 else "")
        warnings.append(
            "skipped {} cookie parameter(s) (cookie params/auth are out of v0 scope): {}".format(len(cookies), shown)
        )
    if truncated:
        warnings.append(
            "endpoint cap reached ({}): compiled {} of {} operations — the rest were dropped in order of appearance".format(
                MAX_ENDPOINTS, MAX_ENDPOINTS, total_ops
            )
        )
    return endpoints


def _digest_from_openapi3_inner(
    obj: Any, source_url: Any, api_name: Any, warnings: List[str]
) -> Tuple[Optional[Digest], List[str]]:
    if not isinstance(obj, Mapping):
        warnings.append("spec document is not a JSON object ({} received) — nothing to parse".format(type(obj).__name__))
        return None, warnings
    mode = detect_spec(obj)
    if mode != "openapi3":
        warnings.append(
            "not an OpenAPI 3 JSON document (detect_spec={}): this lane compiles only OpenAPI 3 JSON".format(mode or "unknown")
        )
        return None, warnings

    info = _submap(obj.get("info"))
    title = str(info.get("title", "") or "").strip()
    if title:
        warnings.append("info.title: {}".format(title))
    raw_name = api_name if isinstance(api_name, str) else str(api_name or "")
    if not raw_name:
        warnings.append(
            "api_name is empty — the caller should pass a short name (info.title is '{}' and is NOT auto-slugified into it)".format(title)
        )

    base_url, base_warnings = _resolve_base_url(obj, source_url)
    warnings.extend(base_warnings)
    auth = _resolve_auth(obj, warnings)
    endpoints = _compile_endpoints(obj, warnings)

    digest = Digest(
        api_name=raw_name,
        source_url=str(source_url or ""),
        base_url=base_url,
        auth=auth,
        detection={"mode": "openapi3", "spec_url": str(source_url or ""), "confidence": 0.95},
        endpoints=endpoints,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        warnings=list(warnings),
    )
    if not digest.valid():
        reasons: List[str] = []
        if not digest.api_name.strip():
            reasons.append("empty api_name")
        if not digest.base_url.strip():
            reasons.append("empty base_url")
        elif not digest.base_url.startswith(("http://", "https://")):
            reasons.append("base_url is not http(s)")
        if not digest.auth.valid():
            reasons.append("invalid auth")
        bad = [e.id or "<no-id>" for e in digest.endpoints if not e.valid()]
        if bad:
            reasons.append("invalid endpoints (provenance/params): {}".format(", ".join(bad[:8])))
        warnings.append("digest.invalid: {}".format("; ".join(reasons) or "unknown validation failure"))
        return None, warnings
    return digest, warnings


def digest_from_openapi3(obj: dict, source_url: str, api_name: str = "") -> "tuple[Digest | None, list[str]]":
    """Compile an OpenAPI 3 JSON document into a validated Digest.

    Never raises: a broken/foreign/non-object document yields (None, warnings)
    with an actionable first warning. Returns a Digest only after Digest.valid()
    passed — validity is never patched up.
    """
    warnings: List[str] = []
    try:
        return _digest_from_openapi3_inner(obj, source_url, api_name, warnings)
    except Exception as exc:  # pragma: no cover - data-driven guard, not logic
        warnings.append("openapi3 compilation failed unexpectedly: {}".format(str(exc)[:200]))
        return None, warnings
