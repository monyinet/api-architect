"""Tool handlers — the code that runs when the LLM calls each tool.

Day-1 state (2026-11-05): architect_list and architect_commit WORK against
the manifests store; architect_learn and api_call are honest stubs wired to the
final schemas — their pipelines land days 2-4. Handlers NEVER raise; every path
returns a JSON string, errors included.
"""
from __future__ import annotations

import json
from pathlib import Path

from plugins.plugin_storage import plugin_data_dir

from . import digest as digest_mod

PLUGIN_ID = "api-architect"

_ERR_NOT_IMPL_LEARN = (
    "not_implemented_yet: spec ingestion (guard + parser + ctx.llm) lands in days 2-3 "
    "of the hackathon build. Use architect_list for learned APIs, or paste a raw "
    "OpenAPI/Postman spec URL — the honest answer today is that learning is pending."
)
_ERR_NOT_IMPL_CALL = (
    "not_implemented_yet: the schema-checked executor lands on day 4 of the "
    "hackathon build. Committed manifests are stored and listed but not yet callable."
)


def _manifests_dir() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "manifests"


def _load_manifest(slug: str) -> Path:
    d = _manifests_dir()
    active = d / "{}.json".format(slug)
    if active.exists():
        return active
    draft = d / "{}.draft.json".format(slug)
    if draft.exists():
        return draft
    return active  # nonexistent — callers handle


def architect_list(args: dict, **kwargs) -> str:
    """List learned APIs: slug, state, endpoint count, warnings."""
    try:
        d = _manifests_dir()
        if not d.exists():
            return json.dumps({"ok": True, "apis": []})
        apis = []
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                apis.append(
                    {
                        "slug": p.stem.replace(".draft", ""),
                        "state": data.get("state", "unknown"),
                        "api_name": data.get("api_name", ""),
                        "base_url": data.get("base_url", ""),
                        "endpoints": len(data.get("endpoints", [])),
                        "auth": (data.get("auth") or {}).get("type", "none"),
                        "generated_at": data.get("generated_at", ""),
                    }
                )
            except Exception as exc:  # corrupt manifest is data, not crash
                apis.append({"slug": p.name, "state": "corrupt", "error": str(exc)[:120]})
        return json.dumps({"ok": True, "apis": apis})
    except Exception as exc:
        return json.dumps({"ok": False, "error": "architect_list failed: {}".format(str(exc)[:200])})


def architect_commit(args: dict, **kwargs) -> str:
    """Promote slug.draft.json -> slug.json (atomic rename on the same volume).

    Validation runs BEFORE the rename: a draft that no longer parses stays a
    draft with an actionable message."""
    try:
        slug = digest_mod.slugify(str(args.get("api_name") or ""))
        if slug in ("", "api"):
            return json.dumps(
                {"ok": False, "error": "Need a valid api_name (letters/digits). See architect_list."}
            )
        d = _manifests_dir()
        draft = d / "{}.draft.json".format(slug)
        active = d / "{}.json".format(slug)
        if active.exists():
            return json.dumps({"ok": True, "already_active": slug})
        if not draft.exists():
            known = ",".join(sorted(p.stem.replace(".draft", "") for p in d.glob("*.json"))) or "none"
            return json.dumps(
                {"ok": False, "error": "No draft manifest named '{}' (known: {}). Learn it first with architect_learn.".format(slug, known)}
            )
        parsed = digest_mod.Digest.from_json(draft.read_text(encoding="utf-8"))
        problems = []
        if not parsed.valid():
            problems.append("digest.invalid: every endpoint needs provenance; base_url must be http(s); auth types are " + "|".join(["none", "bearer", "api_key_header", "api_key_query", "basic"]))
        binding = str(args.get("secret_binding") or "").strip().lower()
        if binding:
            if binding not in ("api_key_1", "api_key_2"):
                problems.append("secret_binding must be 'api_key_1' or 'api_key_2' (both declared in plugin settings).")
            else:
                parsed.auth.secret_binding = binding
        if problems:
            return json.dumps({"ok": False, "error": "commit refused", "problems": problems, "slug": slug})
        if parsed.auth.secret_binding and parsed.auth.type in ("none",):
            parsed.warnings.append("secret slot bound while auth.type is 'none' — the binding is stored but unused.")
        payload = json.loads(draft.read_text(encoding="utf-8"))
        payload["state"] = "active"
        payload["auth"] = {
            "type": parsed.auth.type,
            "location": parsed.auth.location,
            "secret_binding": parsed.auth.secret_binding,
        }
        payload["engine_version"] = parsed.engine_version
        tmp = d / "{}.committing.json".format(slug)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(active)  # atomic on the same volume
        try:
            draft.unlink()
        except OSError:
            pass
        return json.dumps(
            {
                "ok": True,
                "slug": slug,
                "state": "active",
                "endpoints": len(parsed.endpoints),
                "auth": {"type": parsed.auth.type, "secret_binding": parsed.auth.secret_binding},
                "note": "Destructive (non-GET) endpoints always require interactive approval.",
            }
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": "architect_commit failed: {}".format(str(exc)[:200])})


def architect_learn(args: dict, **kwargs) -> str:
    url = str(args.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return json.dumps({"ok": False, "error": "Need a http(s) spec or docs URL."})
    return json.dumps({"ok": False, "error": _ERR_NOT_IMPL_LEARN, "url_accepted": True})


def api_call(args: dict, **kwargs) -> str:
    api_name = str(args.get("api_name") or "").strip()
    if not api_name:
        return json.dumps({"ok": False, "error": "Need api_name (see architect_list)."})
    return json.dumps({"ok": False, "error": _ERR_NOT_IMPL_CALL, "api_name_accepted": True})
