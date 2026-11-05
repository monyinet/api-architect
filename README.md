# api-architect

**Compile API specs into first-class Hermes tools.**

*unbrowse turns pages into text. api-architect compiles API specs* — OpenAPI /
Swagger / Postman — *into schema-checked Hermes tools: validated calls through
one audited network guard, host-scoped credentials that never touch model
context, and manifests that survive the session. It doesn't read the
documentation; it compiles it.*

Status: **work-in-progress for the Hermes Agent Plugin Hackathon (submission due 2026-11-11, 5PM EST).**

## Tools

| Tool | What it does | State |
|---|---|---|
| `architect_learn` | Fetch a spec URL (OpenAPI 3 JSON / Swagger 2 / Postman v2.1), compile endpoints+params with per-endpoint provenance into a manifest | 🔨 days 2-3 |
| `architect_commit` | Draft → active (human-visible review: base_url, auth binding, provenance) | ✅ working |
| `architect_list` | Learned APIs, persistent across sessions | ✅ working |
| `api_call` | Schema-checked execution; invalid params fail locally, never as server 400s | 🔨 day 4 |

## Design commitments (disclosure — catalog rule 13)

- **Network:** fetches user-supplied URLs (specs) and calls committed API hosts.
  Private networks (loopback, RFC 1918, link-local, ULA) are **blocked by
  default**; opt-in `allow_private_hosts` in settings. Requests carry
  `request_timeout_s` and response size caps; redirects are not followed at v0.
- **Credentials:** only through Hermes secret routes — `config_schema` `secret`
  slots (`api_key_1`/`api_key_2`) resolved via `agent.secret_scope` at call
  time. Manifests store the slot *name*, never the value. No `.env`/`auth.json`
 /keychain access; no OAuth flows at v0.
- **Destructive policy:** non-GET endpoints always require interactive
  approval; under unattended runs (cron/gateway) they fail clean. The spec's
  own metadata never authorizes anything (the spec can lie).
- **No hallucinated endpoints:** every endpoint in a manifest carries
  provenance (where in the doc it was read). No spec found → `endpoints: []`
  plus an honest message.
- **No telemetry.** Nothing leaves the machine except the calls the user asked for.
- **Zero Python dependencies at v0** (stdlib only) — nothing to consent, nothing to break on NixOS.

## Compatibility

- Hermes `>=0.22.0` (manifest v2, plugin API 1) · Python 3.11–3.14 · Windows/macOS/Linux.
- Manifests persist in the plugin data dir (`plugin-data/api-architect/manifests/`), survive
  `update`/`remove`, and are profile-scoped.

## Development

Run tests: `python -m unittest discover tests` (stdlib).

Day-by-day build plan and the Red/Blue decision behind this design live in a private
planning vault (not published here).

## License

MIT — © 2026 Jose Juan Moñino Coll (monyinet)
