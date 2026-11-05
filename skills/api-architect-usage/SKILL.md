---
name: api-architect-usage
description: How to use the api-architect plugin — learn an API from its spec URL, commit it, call it schema-checked, and reuse learned APIs across sessions without re-learning.
---

# api-architect usage guide

Compile API specs into first-class tools. The workflow has a hard draft/commit
gate: **drafts are NOT callable** — a human-visible review happens at commit.

## Workflow

1. **Learn** — `architect_learn(url, api_name optional)`. Point it at a spec
   URL (openapi.json, swagger.json, api-docs, Postman collection). Result: a
   DRAFT manifest with endpoints, params and provenance per endpoint. If no
   spec is found it says so honestly and invents nothing.
2. **Inspect** — `architect_list()` shows every learned API with its state,
   endpoint count and auth type. Check the draft's base_url and endpoints
   before committing.
3. **Commit** — `architect_commit(api_name, secret_binding optional)` promotes
   the draft to active. This is where the user chooses a credential slot
   (`api_key_1` / `api_key_2`, configured in the plugin settings) instead of
   pasting keys in the chat. Keys never enter model context.
4. **Call** — `api_call(api_name, endpoint, params, raw_query_body optional)`.
   Parameters are validated against the manifest BEFORE the request: an
   invalid param fails locally with a precise message, not a server 400.

## Rules the agent MUST follow

- Never re-learn an API that `architect_list` already shows as active —
  manifests persist between sessions; call it directly.
- Non-GET endpoints are destructive: they require interactive approval and
  never fire under unattended runs.
- Never paste API keys into the chat or into `params` — bind them via
  `architect_commit(secret_binding=...)` and plugin settings.
- Never call `raw_query_body` on a GET endpoint.
- MVP scope (v0.1.0): OpenAPI 3 JSON first (Swagger 2 / Postman next); HTML
  documentation is only searched for a spec link — no spec, no endpoints.

## Why a plugin and not a prompt

Committed manifests are host-validated data; credentials resolve through
Hermes's secret scope at call time; calls go through one audited network
guard (private ranges blocked by default). A prompt can't do any of that.
