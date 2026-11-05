"""Postman Collection v2.x (JSON) → Digest v1.

Lane L2 contract (red-blue-plugin-pivot, 2026-11-05):
- JSON-only: YAML-saved Postman collections are out of scope for v0 (caller told).
- The Digest NEVER copies credential VALUES — auth types and location names only.
- URL/credential variables stay unresolved: no values are invented here.
- Warnings travel ONLY in the returned (digest|None, warnings) tuple. learn.py
  merges them into digest.warnings itself, so this module leaves that field
  empty to avoid duplicated lines in the persisted manifest.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

try:  # importable both as a plugin package submodule and as a root-level module
    from . import digest as _digest
    from .digest import Auth, Digest, Endpoint, Param, Provenance, slugify
except ImportError:  # pragma: no cover - test/standalone import path
    import digest as _digest
    from digest import Auth, Digest, Endpoint, Param, Provenance, slugify

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
_SCHEMA_RE = re.compile(r"getpostman\.com/json/collection/v2(\.\d+)+(/\S*)?$", re.I)
_MAX_FOLDER_DEPTH = 8
_MAX_BODY_EXAMPLE = 4096  # 4 KB, per contract
_AUTH_TYPES = ("bearer", "basic", "apikey")


def _postman_marker(obj) -> str | None:
    """'schema' (explicit v2/v2.1 collection.json) | 'heuristic' (_postman_id) | None."""
    if not isinstance(obj, dict):
        return None
    info = obj.get("info")
    if isinstance(info, dict):
        schema = info.get("schema")
        if isinstance(schema, str) and _SCHEMA_RE.search(schema):
            return "schema"
        if isinstance(info.get("_postman_id"), str) and info.get("_postman_id").strip():
            return "heuristic"
    if isinstance(obj.get("_postman_id"), str) and obj.get("_postman_id").strip():
        return "heuristic"
    return None


def detect_postman(obj) -> bool:
    """True when the object looks like a Postman Collection v2.x (JSON)."""
    return _postman_marker(obj) is not None


# ----------------------------------------------------------------- helpers

def _text(value) -> str:
    """Postman description/values may be str, {content: str} or lists — flatten."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        joined = " - ".join(p for p in parts if p)
        return joined.strip()
    if isinstance(value, dict):
        return _text(value.get("content") or value.get("value") or "")
    return str(value).strip()


def _collection_vars(obj) -> dict:
    out = {}
    for v in obj.get("variable") or []:
        if isinstance(v, dict) and isinstance(v.get("key"), str) and v["key"]:
            out[v["key"]] = _text(v.get("value"))
    return out


def _resolve_var_piece(piece: str, variables: dict, warnings: list, where: str) -> str:
    """Resolve '{{name}}' pieces through collection-level variables when a plain
    (non-'{{'-containing, non-empty) value exists. Never invent values."""
    def _sub(match):
        name = match.group(1)
        val = variables.get(name)
        if isinstance(val, str) and val and "{{" not in val:
            return val
        warnings.append(
            "URL variable '{{{}}}' en host de '{}' sin valor resoluble en la colección; "
            "no se resuelve la base (no se inventan valores).".format(name, where)
        )
        return match.group(0)

    return _VAR_RE.sub(_sub, piece)


def _origin(source_url: str) -> str:
    try:
        parts = urlsplit(str(source_url or ""))
    except ValueError:
        return str(source_url or "")
    if parts.scheme and parts.netloc:
        return "{}://{}".format(parts.scheme, parts.netloc)
    return str(source_url or "")


def _iso_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(method: str, path: str) -> str:
    return slugify("{} {}".format(method, path.replace("/", " ")), 40)


def _unique_id(base: str, used: set) -> str:
    if base not in used:
        used.add(base)
        return base
    n = 2
    while "{}-{}".format(base, n) in used:
        n += 1
    out = "{}-{}".format(base, n)
    used.add(out)
    return out


# ------------------------------------------------------------------- auth

def _auth_entries(auth_block: dict, ptype: str) -> list:
    entries = auth_block.get(ptype) if ptype else None
    return entries if isinstance(entries, list) else []


def _has_inline_credential(auth_block: dict, ptype: str) -> bool:
    """True when an auth block carries a plain (non-'{{var}}') credential value."""
    if ptype not in _AUTH_TYPES:
        return False
    for e in _auth_entries(auth_block, ptype):
        if not isinstance(e, dict):
            continue
        key = str(e.get("key") or "").lower()
        if key not in ("value", "token", "password"):
            continue
        val = e.get("value")
        if isinstance(val, str) and val and not _VAR_RE.search(val):
            return True
        if isinstance(val, list) and any(  # Postman rich values may wrap strings
            isinstance(part, str) and part and not _VAR_RE.search(part) for part in val
        ):
            return True
    return False


def _auth_from_block(auth_block, warnings: list, level: str):
    """Postman auth block → (Auth|None, diverges: no). Returns (None, ...) for
    noauth/absent/unsupported. Collection-level inline credential values → warning."""
    if not isinstance(auth_block, dict):
        return None
    ptype = str(auth_block.get("type") or "").lower()
    if ptype in ("", "none", "noauth"):
        return None
    if level == "collection" and _has_inline_credential(auth_block, ptype):
        warnings.append(
            "postman collection embedded credential value — no se copia al Digest "
            "(solo tipos y nombres de location); el valor lo pone el executor/commit."
        )
    if ptype == "bearer":
        return Auth(type="bearer")
    if ptype == "basic":
        return Auth(type="basic")
    if ptype == "apikey":
        entries = _auth_entries(auth_block, "apikey")
        loc_in = _text(next((e.get("value") for e in entries if isinstance(e, dict) and str(e.get("key", "")).lower() == "in"), "")).lower()
        key_name = _text(next((e.get("value") for e in entries if isinstance(e, dict) and str(e.get("key", "")).lower() == "key"), ""))
        if loc_in == "query":
            if not key_name:
                warnings.append("postman auth apikey en query sin nombre de key — ignorado en v0.")
                return None
            return Auth(type="api_key_query", location=key_name)
        if loc_in == "header":
            if not key_name:
                warnings.append("postman auth apikey en header sin nombre de key — ignorado en v0.")
                return None
            return Auth(type="api_key_header", location=key_name)
        warnings.append("postman auth apikey sin 'in' (header|query) legible — ignorado en v0.")
        return None
    warnings.append(
        "postman auth type '{}' no soportado en v0 (bearer|basic|apikey; no oauth) — tratado como none.".format(ptype)
    )
    return None


def _request_auth_block(request):
    auth = request.get("auth")
    return auth if isinstance(auth, dict) else None


def _infer_auth_from_headers(requests, warnings):
    """No collection auth → dominant pattern from Authorization headers."""
    bearers = 0
    others = 0
    for _route, _name, req in requests:
        for h in req.get("header") or []:
            if isinstance(h, dict) and str(h.get("key") or "").lower() == "authorization":
                val = _text(h.get("value"))
                if val:
                    if val.lower().startswith("bearer "):
                        bearers += 1
                    else:
                        others += 1
    if bearers:
        warnings.append(
            "postman sin auth de colección; patrón inferido de headers Authorization: bearer."
        )
        return Auth(type="bearer")
    if others:
        warnings.append(
            "postman sin auth de colección; patrón inferido de headers Authorization: api_key_header (Authorization)."
        )
        return Auth(type="api_key_header", location="Authorization")
    return Auth(type="none")


# ------------------------------------------------------------------- URL

def _split_url_dict(url: dict):
    """dict-form url → (protocol, host_str, path_segments, query_list, var_list)."""
    protocol = url.get("protocol")
    protocol = protocol.strip() if isinstance(protocol, str) else ""
    host = url.get("host")
    host_segments = []
    if isinstance(host, list):
        host_segments = [_text(h) for h in host if _text(h)]
    elif isinstance(host, str) and host.strip():
        host_segments = [host.strip()]
    path = url.get("path")
    path_segments = []
    if isinstance(path, list):
        for seg in path:
            if isinstance(seg, dict) and isinstance(seg.get("key"), str) and seg["key"]:
                path_segments.append("{{" + seg["key"] + "}}")
                continue
            t = _text(seg)
            if t:
                path_segments.append(t)
    elif isinstance(path, str) and path.strip():
        path_segments = [s for s in path.strip().split("/") if s]
    query = [q for q in (url.get("query") or []) if isinstance(q, dict)]
    variables = [v for v in (url.get("variable") or []) if isinstance(v, dict)]
    raw = url.get("raw")
    if not protocol and isinstance(raw, str) and "://" in raw:
        try:
            protocol = urlsplit(raw).scheme
        except ValueError:
            protocol = ""
    return protocol, host_segments, path_segments, query, variables


def _split_url_str(raw: str):
    """str-form url → (protocol, host_or_, path_segments, query_list)."""
    s = str(raw or "").strip()
    if not s:
        return "", [], [], []
    protocol, host, path = "", "", s
    query: list = []
    try:
        parts = urlsplit(s)
    except ValueError:
        return "", [], [], []
    if parts.scheme:
        protocol = parts.scheme
        host = parts.netloc
        path = parts.path or ""
        if parts.query:
            query = [
                {"key": k, "value": v}
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
            ]
    elif "?" in path:
        head, _, tail = path.partition("?")
        path = head
        query = [
            {"key": k, "value": v}
            for k, v in parse_qsl(tail, keep_blank_values=True)
        ]
    path_segments = [seg for seg in path.split("/") if seg]
    return protocol, ([host] if host else []), path_segments, query


def _endpoint_path(segments: list) -> str:
    cleaned = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        cleaned.append(_VAR_RE.sub(lambda m: "{%s}" % m.group(1), seg))
    return "/" + "/".join(cleaned)


# ------------------------------------------------------------ body/headers

def _body_raw(request, body_counts: dict):
    """raw-JSON body → (body Param, example) or (None, None) with grouped warning."""
    body = request.get("body")
    if not isinstance(body, dict) or not body.get("mode"):
        return None, None
    mode = str(body.get("mode") or "").lower()
    if mode != "raw":
        body_counts["nonsupported_body"] = body_counts.get("nonsupported_body", 0) + 1
        return None, None
    options = body.get("options")
    lang = ""
    if isinstance(options, dict) and isinstance(options.get("raw"), dict):
        lang = _text(options["raw"].get("language")).lower()
    raw = body.get("raw")
    raw_text = raw if isinstance(raw, str) else _text(raw)
    if lang != "json":
        body_counts["nonjson_raw"] = body_counts.get("nonjson_raw", 0) + 1
        return None, None
    example = None
    if len(raw_text.encode("utf-8")) <= _MAX_BODY_EXAMPLE:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, (dict, list)):
                example = parsed
        except ValueError:
            pass
    return Param(name="body", location="body", type="object", required=True), example


# ------------------------------------------------------------------- walk

def _walk_items(items, route, out, warnings, depth=0):
    for it in items:
        if not isinstance(it, dict):
            continue
        name = _text(it.get("name")) or "unnamed"
        nested = it.get("item")
        if isinstance(nested, list) and "request" not in it:
            if depth >= _MAX_FOLDER_DEPTH:
                warnings.append("item group depth cap alcanzado en '{}' — más profundo se omite (v0).".format(route))
                continue
            _walk_items(nested, route + (name,), out, warnings, depth + 1)
            continue
        request = it.get("request")
        if isinstance(request, dict):
            out.append((route, name, request))
        # item sin request (ejemplos de respuesta) se omite


def _digest_from_collection(obj, source_url: str, api_name: str, marker: str):
    warnings: list = []
    if not isinstance(obj, dict) or not isinstance(obj.get("item"), list):
        return None, ["objeto sin 'item'[] — no es una colección Postman utilizable (v0)."]

    info = obj.get("info") if isinstance(obj.get("info"), dict) else {}
    variables = _collection_vars(obj)

    # --- auth: collection-level first
    collection_auth = _auth_from_block(obj.get("auth"), warnings, "collection")
    requests = []
    _walk_items(obj["item"], (), requests, warnings)

    if collection_auth is not None:
        digest_auth = collection_auth
    else:
        digest_auth = _infer_auth_from_headers(requests, warnings)
        if digest_auth.type == "none":
            warnings.append("postman sin esquema de auth detectado — Digest.auth = none.")

    skip_headers = {"authorization"}
    if digest_auth.type == "api_key_header" and digest_auth.location:
        skip_headers.add(digest_auth.location.strip().lower())

    # --- base_url from the first parseable request
    base_url = ""
    headers_warn: list = []
    body_counts: dict = {}
    endpoints: list = []
    used_ids: set = set()

    parsed_requests = []  # (route, name, request, path_segments, query, pathvars)
    for route, name, request in requests:
        url = request.get("url")
        if isinstance(url, dict):
            protocol, host_segments, path_segments, query, variables_list = _split_url_dict(url)
            host_str = ".".join(host_segments)
        elif isinstance(url, str) or url is None:
            protocol, host_segments, path_segments, query = _split_url_str(_text(url))
            variables_list = []
            host_str = ".".join(host_segments)
        else:
            warnings.append("request '{}' con url no legible — omitido (v0).".format(name))
            continue

        path_segments = [sg for sg in path_segments if sg]  # {{var}} stays → path param '{var}'
        path = _endpoint_path(path_segments)
        pathvars = {}
        for v in variables_list:
            key = _text(v.get("key"))
            if key:
                pathvars.setdefault(key, {"description": _text(v.get("description"))})
        parsed_requests.append((route, name, request, path, query, pathvars))

        if base_url:
            continue
        if not host_str:
            continue  # path-only request: endpoint still usable, base from elsewhere
        resolved_segments = [_resolve_var_piece(sg, variables, warnings, name) for sg in host_segments]
        if not resolved_segments or any((not sg) or "{{" in sg for sg in resolved_segments):
            continue  # host unresolved → not a parseable base_url source
        host_joined = ".".join(resolved_segments)
        if "://" in resolved_segments[0]:
            candidate = host_joined  # {{baseUrl}}-style variable carries the scheme
        elif protocol:
            candidate = "{}://{}".format(protocol, host_joined)
        else:
            continue  # no scheme derivable from data → not a base source (no defaults invented)
        if not candidate.startswith(("http://", "https://")):
            continue
        base_url = candidate.rstrip("/")

    if not base_url:
        warnings.append(
            "ninguna request con (protocol+host) parseable → base_url derivada del origen de source_url."
        )
        base_url = _origin(source_url)

    # --- endpoints
    divergences = 0
    for route, name, request, path, query, pathvars in parsed_requests:
        method = str(request.get("method") or "GET").strip().upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            warnings.append("request '{}' con método '{}' no soportado — omitido (v0).".format(name, method))
            continue

        req_auth_block = _request_auth_block(request)
        req_auth = _auth_from_block(req_auth_block, warnings, "request") if isinstance(req_auth_block, dict) else None
        if req_auth_block is not None and req_auth is not None and collection_auth is not None:
            if (req_auth.type, req_auth.location) != (digest_auth.type, digest_auth.location):
                divergences += 1

        params: list = []
        if req_auth and req_auth.type == "api_key_header" and req_auth.location:
            skip_headers.add(req_auth.location.strip().lower())

        for q in query:
            key = _text(q.get("key"))
            if not key:
                continue
            params.append(
                Param(
                    name=key,
                    location="query",
                    type="string",
                    required=False,
                    description=_text(q.get("description")),
                )
            )

        for key, meta in sorted(pathvars.items()):
            params.append(
                Param(
                    name=key,
                    location="path",
                    type="string",
                    required=True,
                    description=meta.get("description") or "no resuelto en la colección",
                )
            )
        covered = {key for key, _meta in pathvars.items()}
        for seg in re.findall(r"\{([^{}]+)\}", path):
            if seg not in covered:
                params.append(
                    Param(
                        name=seg,
                        location="path",
                        type="string",
                        required=True,
                        description="no resuelto en la colección",
                    )
                )

        # headers
        header_items = request.get("header")
        if isinstance(header_items, dict):  # tolerated dict form {Name: Value}
            header_items = [{"key": k, "value": v} for k, v in header_items.items()]
        for h in header_items or []:
            if not isinstance(h, dict):
                continue
            hname = _text(h.get("key"))
            if not hname:
                continue
            if hname.lower() in skip_headers:
                continue
            headers_warn.append("{}@{}".format(hname, name))

        body_param, example = _body_raw(request, body_counts)
        if body_param is not None:
            params.append(body_param)

        detail = "item: " + "/".join([*route, name])[:120]
        endpoints.append(
            Endpoint(
                id=_unique_id(_slug(method, path), used_ids),
                method=method,
                path=path,
                summary=_text(request.get("description"))[:160],
                params=params,
                request_example=example,
                provenance=Provenance(source="postman-request", detail=detail),
                operation_id=(name or None),
            )
        )

    if divergences:
        warnings.append(
            "{} request(s) con auth de request distinta de la dominante — override NO representable en Digest v0; revisa las request en la colección.".format(divergences)
        )
    if headers_warn:
        uniq = sorted(set(headers_warn))
        warnings.append(
            "headers no parametrizables en v0 (omitidos de los endpoints): {} ({}).".format(", ".join(uniq), len(headers_warn))
        )
    if body_counts.get("nonsupported_body"):
        warnings.append(
            "postman bodies formdata/file/urlencoded/graphql o raw sin JSON omitidos en v0: {}.".format(body_counts["nonsupported_body"])
        )
    if body_counts.get("nonjson_raw"):
        warnings.append("raw bodies sin options.language=json omitidos en v0: {}.".format(body_counts["nonjson_raw"]))

    api_name_final = api_name.strip() or _text(info.get("name")) or "api"
    detection = {
        "mode": "postman",
        "spec_url": source_url,
        "confidence": 0.95 if marker == "schema" else 0.7,
    }
    digest = Digest(
        api_name=api_name_final,
        source_url=source_url,
        base_url=base_url,
        auth=digest_auth,
        detection=detection,
        endpoints=endpoints,
        generated_at=_iso_now_utc(),
    )
    return digest, warnings


def digest_from_postman(obj, source_url: str, api_name: str = ""):
    """Collection v2 JSON → (Digest|None, warnings[Str]). Never copies secrets."""
    marker = _postman_marker(obj)
    if marker is None:
        return None, ["objeto no reconocido como Postman Collection v2.x (JSON) — nada compilado."]
    return _digest_from_collection(obj, source_url, api_name, marker)
