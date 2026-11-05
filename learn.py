"""architect_learn — the ingestion pipeline.

Flow: probe candidate spec URLs with the guard → detect (openapi3|swagger2|
postman) → parse deterministically → optional ctx.llm polish (name/description,
fail-soft, NEVER structure) → draft manifest persisted with provenance.

Anti-hallucination contract: a spec parses deterministically or endpoints=[].
The LLM never invents endpoints at v0; its role in the happy path is bounded to
describing what the parser already validated.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from typing import Optional

# Sibling modules resolve in BOTH load modes: flat when this directory is on sys.path (the repo's
# tests and E2E driver), package-relative when Hermes loads the plugin as hermes_plugins.<slug>
# with the plugin dir NOT on sys.path (hermes_cli/plugins_loader.py::_load_directory_module).
try:
    from .digest import Digest, slugify
    from .netguard import GuardError, check_url, guarded_fetch
    from .redactor import redact_message
except ImportError:  # pragma: no cover - test/standalone import path
    from digest import Digest, slugify
    from netguard import GuardError, check_url, guarded_fetch
    from redactor import redact_message


def _parser(stem):
    """Import a sibling parser module in both load modes (see the import block)."""
    import importlib

    if not __package__:
        return importlib.import_module(stem)
    try:
        return importlib.import_module(stem)
    except ImportError:
        return importlib.import_module("." + stem, __package__)

SPEC_SUFFIX_CANDIDATES = (
    "openapi.json",
    "swagger.json",
    "openapi.yaml",  # probed but JSON-only parsing at v0 — YAML is a declared gap
    "swagger.yaml",
    "v1/openapi.json",
    "v2/openapi.json",
    "v3/openapi.json",
    "api-docs",
    "v3/api-docs",
    "postman_collection.json",
    "postman.json",
    "collection.json",
)
_LINK_RE = re.compile(r"href=[\"']([^\"']*(openapi|swagger|api-docs|api_doc|postman)[^\"'\.]?(?:\.json|\.yaml)?)")
_MAX_LINKS = 3

MAX_PROBE_CAP = 400_000  # cap per probe; enough for a big spec, small enough for the 30 s budget


def candidate_urls(source_url: str) -> list[str]:
    """Probe order: the URL itself first (it may BE the spec), then same-origin candidates."""
    from urllib.parse import urljoin, urlsplit

    out = [source_url]
    p = urlsplit(source_url)
    base = "{}://{}{}".format(p.scheme, p.netloc, (p.path.rsplit("/", 1)[0] or "/"))
    if not base.endswith("/"):
        base = base + "/"
    out.extend(urljoin(base, suffix) for suffix in SPEC_SUFFIX_CANDIDATES)
    # dedupe preserving order
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def discover_links(html: str, source_url: str) -> list[str]:
    """≤3 same-page spec-link candidates from HTML docs (link-discovery ONLY —
    no endpoint extraction from HTML, ever)."""
    from urllib.parse import urljoin

    links = []
    for m in _LINK_RE.finditer(html):
        candidate = urljoin(source_url, m.group(1))
        if candidate not in links:
            links.append(candidate)
        if len(links) >= _MAX_LINKS:
            break
    return links


def _detect_and_parse(obj, source_url: str, api_name: str):
    """(digest|None, warnings, mode) — imports land lazily so all lanes can own their files."""
    mode = None
    try:
        parse_spec = _parser("parse_spec")

        mode = parse_spec.detect_spec(obj)
    except ImportError:
        mode = None
    if mode == "openapi3":
        return parse_spec.digest_from_openapi3(obj, source_url, api_name) + ("openapi3",)
    if mode is None:
        try:
            parse_swagger2 = _parser("parse_swagger2")
        except ImportError:
            return None, ["no parser could be imported"], None
        if parse_swagger2.detect_swagger2(obj):
            return parse_swagger2.digest_from_swagger2(obj, source_url, api_name) + ("swagger2",)
        try:
            parse_postman = _parser("parse_postman")
        except ImportError:
            return None, ["no parser could be imported"], None
        if parse_postman.detect_postman(obj):
            return parse_postman.digest_from_postman(obj, source_url, api_name) + ("postman",)
    return None, [], mode


def learn(
    source_url: str,
    api_name: str,
    timeout_s: int = 15,
    user_agent: str = "",
    allow_private: bool = False,
    llm=None,
) -> dict:
    """Full learn pipeline. Returns a dict the handler wraps in JSON. The `llm`
    argument is the session ctx.llm (host-owned, optional, fail-soft)."""
    warnings: list[str] = []
    tried: list[str] = []

    def _attempt(url: str):
        try:
            resp = guarded_fetch(
                url,
                method="GET",
                headers={"Accept": "application/json, text/html, */*"},
                timeout_s=timeout_s,
                max_bytes=MAX_PROBE_CAP,
                user_agent=user_agent,
                allow_private=allow_private,
            )
        except GuardError as exc:
            tried.append("{} -> refused: {}".format(url, str(exc)[:120]))
            return None
        body = resp["body"]
        head = body[:512].lstrip()
        if head.startswith(b"{") or head.startswith(b"[") or (resp["headers"].get("content-type", "").lower().startswith("application/json")):
            try:
                obj = json.loads(body.decode("utf-8", errors="strict"))
            except (ValueError, UnicodeDecodeError) as exc:
                try:
                    warnings.append("spec candidate is not strict json: {}".format(str(exc)[:80]))
                    return None
                except Exception:
                    return None
            digest, warn, mode = _detect_and_parse(obj, url, api_name)
            for w in warn:
                warnings.append(w)
            if digest is not None:
                return ("spec", digest, url, mode)
            tried.append(url + " -> json but no spec detected")  # red-lens I: keep probing
            warnings.append("candidate is valid JSON but not a recognized spec (openapi3/swagger2/postman)")
            return None
        if head[:1] == b"<":
            links = discover_links(body.decode("utf-8", errors="replace"), url)
            if links:
                return ("links", links, url)
            return ("html", None, url)
        tried.append("{} -> not json/html (content-type {})".format(url, resp["headers"].get("content-type", "?")[:40]))
        return None

    result = _attempt(source_url)
    if result and result[0] == "links":
        warnings.append("documentation page found; following up to {} spec link candidate(s) — HTML itself yields NO endpoints".format(len(result[1])))
        for link in result[1]:
            follow = _attempt(link)
            if follow and follow[0] == "spec":
                result = follow
                break
    elif result is None:
        warnings.append("probing standard spec URLs:")
        for candidate in candidate_urls(source_url)[1:]:
            follow = _attempt(candidate)
            if follow and follow[0] == "spec":
                result = follow
                break

    if not result or result[0] != "spec":
        notes = tried[:6] or ["no response available"]
        return {
            "ok": False,
            "error": "No machine-readable spec found (OpenAPI/Swagger/Postman JSON). "
            "Nothing was invented: provide a spec URL, or contact the API's docs. "
            "Probes: " + " | ".join(notes),
            "warnings": warnings,
        }

    _, digest, spec_url, mode = result
    if not api_name:
        api_name = slugify(digest.api_name or urlsplit(spec_url).netloc.split(".")[0] or "api")

    # Optional ctx.llm polish — fail-soft, structure NEVER comes from the LLM.
    if llm is not None:
        try:
            res = llm.complete_structured(
                instructions=(
                    "Given this API manifest summary, write ONE friendly sentence for the "
                    "manifest description. Do not invent endpoints or capabilities; describe "
                    "only what is listed."
                ),
                input=[
                    {"type": "text", "text": json.dumps({"name": digest.api_name, "base_url": digest.base_url, "endpoints": [e.method + " " + e.path for e in digest.endpoints[:40]], "auth": digest.auth.type}, ensure_ascii=False)}
                ],
                json_schema={"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
                schema_name="api_architect.description",
                purpose="api-architect.learn-polish",
                temperature=0.0,
                max_tokens=120,
            )
            if res.parsed and res.parsed.get("description"):
                digest.warnings.append("description-polish-llm: " + str(res.parsed["description"])[:280])
        except Exception as exc:  # fail-soft by design
            warnings.append("llm polish skipped ({}); manifest unaffected".format(str(exc)[:80]))

    if digest:
        digest.warnings = digest.warnings + warnings[-20:]
        digest.detection["spec_url"] = spec_url
        return {
            "ok": True,
            "state": "draft",
            "slug": slugify(api_name or digest.api_name),
            "api_name": digest.api_name,
            "source_url": source_url,
            "spec_url": spec_url,
            "mode": mode or digest.detection.get("mode"),
            "base_url": digest.base_url,
            "auth": {"type": digest.auth.type, "location": digest.auth.location},
            "endpoints": len(digest.endpoints),
            "warnings": warnings[-10:],
            "note": "Draft manifest — not callable until architect_commit.",
            "digest": digest,
        }
    return {"ok": False, "error": "unknown learn state", "warnings": warnings}
