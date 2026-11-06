"""Tool handlers — the code that runs when the LLM calls each tool.

Night build (2026-11-05): all four tools are REAL. architect_learn runs the
deterministic ingestion pipeline (guard → detect → parse → llm-polish → draft);
api_call is the schema-checked interpreter. Handlers NEVER raise; every path
returns a JSON string, errors included. Secrets never appear in any output.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from plugins.plugin_storage import plugin_data_dir

from . import digest as digest_mod

PLUGIN_ID = "api-architect"
_ctx = None  # set at register(); gates config (request_timeout_s/allow_private_hosts) and ctx.llm


def _sib(stem):
    """Import a sibling module, plugin-owned FIRST in package mode.

    Package-relative first: Hermes loads a directory plugin as hermes_plugins.<slug>
    with the plugin directory NOT on sys.path (hermes_cli/plugins_loader.py;
    maintainer review of this PR, 2026-11-06), and an explicit relative import
    ignores any foreign top-level module of the same name — plugin ownership of
    its own modules must not depend on what other code preloaded into
    sys.modules (catalog-review regression). Flat fallback for the repo's flat
    import modes; the handler tests' mocks patch the loaded package copy they
    get from the load helper (same object, so mocks still bind honestly).
    """
    if __package__:
        try:
            return importlib.import_module("." + stem, __package__)
        except ImportError:
            pass
    return importlib.import_module(stem)


def _config(key: str, default):
    if _ctx is None:
        return default
    try:
        return _ctx.get_config(key, default=default)
    except Exception:
        return default


def _llm():
    return getattr(_ctx, "llm", None) if _ctx is not None else None


def _manifests_dir() -> Path:
    return plugin_data_dir(PLUGIN_ID) / "manifests"


def _truthy(v) -> bool:
    """Strict boolean semantics (red-lens C): config values saved as strings and
    model arguments MUST NOT go through bool() truthiness — 'false'/"0"/"no"/""
    are False even though they are non-empty."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v == 1
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "y")
    return False


def architect_list(args: dict, **kwargs) -> str:
    """List learned APIs: slug, state, endpoint count, warnings."""
    try:
        d = _manifests_dir()
        if not d.exists():
            return json.dumps({"ok": True, "apis": []})
        apis = []
        for p in sorted(d.glob("*.json")):
            if p.stem.endswith((".committing", ".learning")):  # red-lens K: intermediates are not APIs
                continue
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
        redact_message = _sib("redactor").redact_message
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
            return json.dumps({"ok": False, "error": redact_message("commit refused: " + "; ".join(problems))})
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
        # same two-pass hygiene as the draft write: what lands on disk must be
        # scrubbed even if the draft predates a later redactor rule (finding F2)
        tmp.write_text(
            redact_message(json.dumps(payload, ensure_ascii=False, indent=2)),
            encoding="utf-8",
        )
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
                "note": "Destructive (non-GET) endpoints require confirm; unattended runs fail clean.",
            }
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": "architect_commit failed: {}".format(str(exc)[:200])})


def architect_learn(args: dict, **kwargs) -> str:
    """Deterministic ingestion: probe with the guard → detect → parse → optional
    llm polish → DRAFT manifest persisted with provenance. Nothing hallucinated."""
    try:  # a missing sibling degrades to JSON: the handler contract is "never raise"
        redact_message = _sib("redactor").redact_message
        strip_secret_values = _sib("secrets_bridge").strip_secret_values
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": "architect_learn failed: plugin modules unavailable: {}".format(str(exc)[:200])}
        )

    url = str(args.get("url") or "").strip()
    api_name = str(args.get("api_name") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return json.dumps({"ok": False, "error": "Need a http(s) spec or docs URL."})
    clean_url = _sib("redactor").redact_url(url)  # red-lens D: a credential IN the pasted URL never reaches disk
    if clean_url != url:
        result_note = "source_url stored redacted (credential-shaped query parameters)"
    else:
        result_note = ""
    # pass (a) needs the RAW values the structural pass redacts from this URL
    # (finding F1): value-equality redaction on the manifest must actually scrub
    # them, not receive an empty tuple
    pasted_secret_values = _sib("redactor").secret_values_in_url(url)
    try:
        learn_mod = _sib("learn")

        result = learn_mod.learn(
            url,
            api_name,
            timeout_s=int(_config("request_timeout_s", 15) or 15),
            user_agent=str(_config("user_agent", "") or ""),
            allow_private=_truthy(_config("allow_private_hosts", False)),
            llm=_llm(),
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": "architect_learn failed: {}".format(str(exc)[:200])})

    if not result.get("ok"):
        result["warnings"] = [redact_message(str(w)) for w in (result.get("warnings") or [])][:10]
        result["error"] = redact_message(str(result.get("error", "")))
        return json.dumps(result, ensure_ascii=False)

    digest = result.pop("digest", None)
    if digest is None:
        return json.dumps({"ok": False, "error": "learn returned ok without a manifest"})
    slug = digest_mod.slugify(result.get("slug") or api_name or digest.api_name)
    d = _manifests_dir()
    d.mkdir(parents=True, exist_ok=True)
    payload = json.loads(digest.to_json(state="draft"))
    payload = strip_secret_values(payload, pasted_secret_values)  # value-equality pass (real values, F1)
    # structural pass (red-lens D): any credential-shaped query param in ANY url
    # field of the manifest — source_url, detection.spec_url, provenance, warnings —
    # loses its value before the bytes touch disk
    payload = json.loads(redact_message(json.dumps(payload, ensure_ascii=False)))
    payload["source_url"] = clean_url
    if result_note:
        payload["warnings"] = payload.get("warnings", []) + [result_note]
    tmp = d / "{}.learning.json".format(slug)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    draft_path = d / "{}.draft.json".format(slug)
    tmp.replace(draft_path)
    eps = payload.get("endpoints", [])
    result.update(
        {
            "slug": slug,
            "state": "draft",
            "draft_file": str(draft_path),
            "endpoints_preview": ["{} {}".format(e.get("method"), e.get("path")) for e in eps[:12]],
            "endpoints_shown": min(12, len(eps)),
            "endpoints_total": len(eps),
            "note": "Draft manifest — review with architect_list and activate with architect_commit.",
        }
    )
    active_path = d / "{}.json".format(slug)
    if active_path.exists():
        # the caller learns nothing about a committed twin unless the response
        # says so — say it in-line instead of leaving the model to improvise
        # an apology after architect_list (live dogfood finding, 2026-11-06)
        result["active_exists"] = True
        result["note"] = (
            "An ACTIVE manifest for '{}' already exists. Re-learning is the UPDATE path: "
            "this draft changes nothing until architect_commit reviews and replaces it — "
            "call the API directly if you just wanted to use it.".format(slug)
        )
    result["warnings"] = [redact_message(str(w)) for w in (result.get("warnings") or [])][:10]
    # final structural pass on EVERYTHING the handler returns (red-lens D)
    return redact_message(json.dumps(result, ensure_ascii=False))


def api_call(args: dict, **kwargs) -> str:
    """Schema-checked execution over a COMMITTED manifest. Non-GET endpoints are
    destructive: they need confirm=true in the SAME session (unattended runs
    fail clean). Validation happens locally against the manifest."""
    try:  # a missing sibling degrades to JSON: the handler contract is "never raise"
        executor_mod = _sib("executor")
        redact_message = _sib("redactor").redact_message
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": "api_call failed: plugin modules unavailable: {}".format(str(exc)[:200])}
        )

    slug = digest_mod.slugify(str(args.get("api_name") or ""))
    if slug in ("", "api"):
        return json.dumps({"ok": False, "error": "Need api_name (see architect_list)."})
    try:
        active = _manifests_dir() / "{}.json".format(slug)
        if not active.exists():
            if (_manifests_dir() / "{}.draft.json".format(slug)).exists():
                return json.dumps(
                    {"ok": False, "error": "'{}' is still a DRAFT — not callable until architect_commit activates it.".format(slug)}
                )
            return json.dumps({"ok": False, "error": "No committed API named '{}' — run architect_list to see what is learned.".format(slug)})
        manifest = digest_mod.Digest.from_json(active.read_text(encoding="utf-8"))
        if not manifest.valid():
            return json.dumps({"ok": False, "error": "The manifest for '{}' is invalid — relearn the API.".format(slug)})
        wanted = str(args.get("endpoint") or "")
        ep = executor_mod.find_endpoint(manifest, wanted)
        if ep is None:
            ids = [e.method + " " + e.path for e in manifest.endpoints[:15]]
            return json.dumps(
                {"ok": False, "error": "Endpoint '{}' not in the manifest for '{}'.".format(redact_message(wanted), slug), "available": ids, "available_total": len(manifest.endpoints)}
            )
        if ep.method != "GET" and not _truthy(args.get("confirm")):
            return json.dumps(
                {
                    "ok": False,
                    "needs_confirmation": True,
                    "destructive": True,
                    "method": ep.method,
                    "endpoint": ep.method + " " + ep.path,
                    "error": "Destructive endpoint ({}): nothing was sent. Confirm by repeating api_call with confirm: true in this interactive session.".format(ep.method),
                }
            )
        values = args.get("params") or {}
        body = args.get("raw_query_body")
        try:
            result = executor_mod.execute(
                manifest,
                ep,
                values,
                body,
                timeout_s=int(_config("request_timeout_s", 15) or 15),
                user_agent=str(_config("user_agent", "") or ""),
                allow_private=_truthy(_config("allow_private_hosts", False)),
            )
        except executor_mod.ExecutionError as exc:
            return json.dumps({"ok": False, "error": redact_message(str(exc))}, ensure_ascii=False)
        result["endpoint_id"] = ep.id
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "api_call failed: {}".format(redact_message(str(exc)[:200]))})
