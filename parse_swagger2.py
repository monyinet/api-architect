"""Swagger 2.0 (JSON) → Digest v1.

Lane L2 contract (red-blue-plugin-pivot, 2026-11-05):
- JSON-only: Swagger 2 specs saved as YAML are out of scope for v0 (caller told).
- Auth: securityDefinitions + global security, else first seen per-operation
  security; apiKey/basic supported, oauth2 discarded with a warning (no flows).
- formData/multipart parameters are SKIPPED with a grouped warning (v0).
- request_example stays None: swagger2 inline body examples are rare and v0
  doesn't fabricate them.
- $refs resolve ONE level only (definitions/parameters sections).
- Warnings travel ONLY in the returned (digest|None, warnings) tuple; learn.py
  merges them into digest.warnings, so this module leaves that field empty.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

try:  # importable both as a plugin package submodule and as a root-level module
    from . import digest as _digest
    from .digest import Auth, Digest, Endpoint, Param, Provenance, slugify
except ImportError:  # pragma: no cover - test/standalone import path
    import digest as _digest
    from digest import Auth, Digest, Endpoint, Param, Provenance, slugify

_METHODS_LC = ("get", "post", "put", "patch", "delete", "head", "options")
_REF_RE = re.compile(r"^#+/(.+)$")


# ----------------------------------------------------------------- helpers

def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return str(value)
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        return " - ".join(p for p in parts if p).strip()
    if isinstance(value, dict):
        return _text(value.get("content") or value.get("value") or "")
    return str(value).strip()


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
    return slugify("{} {}".format(method.upper(), path.replace("/", " ")), 40)


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


def _resolve_ref(ref: str, root: dict):
    """Resolve '#/definitions/Pet' style refs ONE level: direct dict lookup,
    no nested-ref chasing. Returns the target dict or None."""
    if not isinstance(ref, str):
        return None
    m = _REF_RE.match(ref)
    if not m:
        return None
    node = root
    for raw_key in m.group(1).split("/"):
        key = raw_key.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


# ------------------------------------------------------------------ detect

def detect_swagger2(obj) -> bool:
    """True when the object declares swagger 2.x (JSON)."""
    if not isinstance(obj, dict):
        return False
    return str(obj.get("swagger", "")).startswith("2.")


# ------------------------------------------------------------------- auth

def _scheme_candidates(obj, warnings: list):
    """Ordered (first-seen) security scheme names: global security first; when
    absent (or empty = explicitly public), first seen across operations."""
    out = []
    seen = set()

    def _push(names):
        for req in names:
            if not isinstance(req, dict):
                continue
            for name in req.keys():
                if name not in seen:
                    seen.add(name)
                    out.append(name)

    global_sec = obj.get("security")
    if isinstance(global_sec, list):
        _push(global_sec)
        if not out:
            # Empty global security list means the API is explicitly public.
            return ["__public__"]
    else:
        paths = obj.get("paths")
        for pi in (paths or {}).values():
            if not isinstance(pi, dict):
                continue
            for method in _METHODS_LC:
                op = pi.get(method)
                if isinstance(op, dict) and isinstance(op.get("security"), list):
                    _push(op["security"])
                    if len(out) >= 8:  # plenty; first supported wins anyway
                        return out
    return out


def _auth_from_schemes(obj, warnings: list) -> Auth:
    defs = obj.get("securityDefinitions") if isinstance(obj.get("securityDefinitions"), dict) else {}
    oauth_discarded = set()
    selected: Auth | None = None
    for name in _scheme_candidates(obj, warnings):
        if name == "__public__":
            warnings.append("swagger2 con 'security': [] — API explícitamente pública (auth none).")
            break
        d = defs.get(name)
        if not isinstance(d, dict):
            warnings.append(
                "swagger2: security referenciando esquema indefinido '{}' — ignorado (v0).".format(name)
            )
            continue
        stype = str(d.get("type") or "").lower()
        if stype == "apikey":
            loc_in = str(d.get("in") or "").lower()
            key_name = str(d.get("name") or name)
            if loc_in == "query":
                selected = Auth(type="api_key_query", location=key_name)
                break
            if loc_in == "header":
                selected = Auth(type="api_key_header", location=key_name)
                break
            warnings.append(
                "swagger2: apiKey '{}' con 'in' no soportado ({}) — ignorado en v0.".format(name, loc_in or "?")
            )
            continue
        if stype == "basic":
            selected = Auth(type="basic")
            break
        if stype == "oauth2":
            if name not in oauth_discarded:
                oauth_discarded.add(name)
                warnings.append(
                    "swagger2: security scheme '{}' (oauth2) descartada en v0 — sin flows OAuth.".format(name)
                )
            continue
        warnings.append(
            "swagger2: security scheme '{}' (type '{}') no soportada en v0 — ignorada.".format(name, stype or "?")
        )
    if selected is None:
        return Auth(type="none")
    return selected


# ------------------------------------------------------------ parameters

def _merge_params(pi_params, op_params, root, counts, warnings):
    """Path-item params + operation params; operation-level wins on (name,in)."""
    merged: dict = {}
    order: list = []
    for src in (pi_params if isinstance(pi_params, list) else [], op_params if isinstance(op_params, list) else []):
        for raw in src if isinstance(src, list) else []:
            if not isinstance(raw, dict):
                continue
            raw = dict(raw)
            if "$ref" in raw and isinstance(raw.get("$ref"), str):
                target = _resolve_ref(raw["$ref"], root)
                if not isinstance(target, dict):
                    counts["broken_ref"] = counts.get("broken_ref", 0) + 1
                    continue
                raw = target
            loc = str(raw.get("in") or "").lower()
            name = str(raw.get("name") or "")
            if not name and loc == "body":
                name = "body"
            if not name:
                continue
            key = (name, loc)
            if key not in merged:
                order.append(key)
            merged[key] = raw
    # operation-level entries were merged last → they win on (name, in)
    params = []
    for key in order:
        raw = merged[key]
        loc = str(raw.get("in") or "").lower()
        name = key[0]
        if loc == "body":
            schema = raw.get("schema")
            description = _text(raw.get("description"))
            if isinstance(schema, dict):
                ref = schema.get("$ref")
                if isinstance(ref, str):
                    target = _resolve_ref(ref, root)
                    if isinstance(target, dict):
                        description = description or _text(target.get("description"))
                elif _text(schema.get("description")):
                    description = _text(schema.get("description"))
            params.append(
                Param(
                    name=name,
                    location="body",
                    type="object",
                    required=bool(raw.get("required", False)),
                    description=description[:200],
                )
            )
        elif loc == "formdata":
            counts["formData"] = counts.get("formData", 0) + 1
        elif loc in ("path", "query", "header"):
            stype = str(raw.get("type") or "string") or "string"
            enum = raw.get("enum")
            if enum is None and stype == "array" and isinstance(raw.get("items"), dict):
                enum = raw["items"].get("enum")  # swagger2 puts array enums in items (petstore status)
            enum_list: list | None = None
            if isinstance(enum, list) and enum:
                vals = [_text(e) for e in enum if _text(e)]
                enum_list = vals or None
            required = bool(raw.get("required", False))
            if loc == "path":
                required = True  # path vars are always required by construction
            params.append(
                Param(
                    name=name,
                    location=loc,
                    type={"array": "array" if isinstance(raw.get("items"), dict) else "string"}.get(stype, stype),
                    required=required,
                    description=_text(raw.get("description")),
                    enum=enum_list,
                )
            )
        elif loc == "cookie":
            warnings.append("swagger2: parámetro in:cookie ('{}') omitido en v0.".format(name))
        else:
            warnings.append(
                "swagger2: parámetro con 'in' desconocido ({!r}) omitido (v0).".format(loc or "?")
            )
    return params


def _digest_from_spec(obj, source_url: str, api_name: str):
    warnings: list = []
    if not isinstance(obj, dict) or not isinstance(obj.get("paths"), dict):
        return None, ["swagger2 sin 'paths' legible (dict) — nada que compilar (v0)."]

    paths = obj["paths"]

    # --- base_url: schemes[0] + '://' + host + basePath (host REQUIRED)
    schemes = obj.get("schemes")
    scheme = ""
    if isinstance(schemes, list) and schemes:
        scheme = str(schemes[0] or "").strip()
    fallback_scheme = ""
    try:
        fallback_scheme = urlsplit(str(source_url or "")).scheme or ""
    except ValueError:
        fallback_scheme = ""
    if scheme not in ("http", "https"):
        if scheme:
            warnings.append(
                "swagger2: schemes[0] '{}' no es http(s); se usa el esquema del origen ({}).".format(scheme, fallback_scheme or "https")
            )
        else:
            warnings.append("swagger2 sin 'schemes'; esquema tomado del origen ({}).".format(fallback_scheme or "https"))
        scheme = fallback_scheme or "https"

    host = str(obj.get("host") or "").strip()
    if not host or "{{" in host:
        if host:
            warnings.append(
                "swagger2: host con variables ({{...}}) no resolvibles — no se inventan valores; base_url desde el origen de source_url."
            )
        else:
            warnings.append("swagger2 sin 'host' — no se inventa; base_url desde el origen de source_url.")
        base_url = _origin(source_url)
    else:
        base_url = "{}://{}".format(scheme, host)
    base_path = str(obj.get("basePath") or "").strip()
    if base_path not in ("", "/"):
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        base_url = base_url.rstrip("/") + base_path
    if not base_url.startswith(("http://", "https://")):
        warnings.append(
            "swagger2: base_url resultante '{}' no es http(s); se usa el origen de source_url.".format(base_url)
        )
        base_url = _origin(source_url)

    # --- auth
    digest_auth = _auth_from_schemes(obj, warnings)

    # --- endpoints
    endpoints: list = []
    used_ids: set = set()
    counts: dict = {}
    for path in paths.keys():
        if not isinstance(path, str) or not path:
            continue
        pi = paths[path]
        if not isinstance(pi, dict):
            continue
        pi_params = pi.get("parameters") if isinstance(pi.get("parameters"), list) else []
        for method in _METHODS_LC:
            op = pi.get(method)
            if not isinstance(op, dict):
                continue
            op_params = op.get("parameters") if isinstance(op.get("parameters"), list) else []
            params = _merge_params(pi_params, op_params, obj, counts, warnings)  # counts/warnings threaded
            detail = "/paths/{}/{}".format(path, method)
            endpoints.append(
                Endpoint(
                    id=_unique_id(_slug(method, path), used_ids),
                    method=method.upper(),
                    path=path,
                    summary=(_text(op.get("summary")) or _text(op.get("description")) or "")[:160],
                    params=params,
                    request_example=None,  # v0: swagger2 inline body examples not taken
                    provenance=Provenance(source="swagger2-path-item", detail=detail[:120]),
                )
            )

    if counts.get("formData"):
        warnings.append(
            "swagger2: {} parámetro(s) in:formData omitidos en v0 (multipart/form-data sin soporte).".format(counts["formData"])
        )
    if counts.get("broken_ref"):
        warnings.append(
            "swagger2: {} $ref(s) no resoluble(s) a nivel parámetro/schema (1 nivel) — parámetros omitidos.".format(counts["broken_ref"])
        )

    api_name_final = api_name.strip() or _text((obj.get("info") or {}).get("title") if isinstance(obj.get("info"), dict) else "") or "api"
    detection = {"mode": "swagger2", "spec_url": source_url, "confidence": 0.95}
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


def digest_from_swagger2(obj, source_url: str, api_name: str = ""):
    """Swagger 2 JSON → (Digest|None, warnings[str]). Never copies secrets."""
    if not detect_swagger2(obj):
        return None, ["objeto sin 'swagger' 2.x — nada compilado por el parser swagger2."]
    return _digest_from_spec(obj, source_url, api_name)
