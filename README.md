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
| `architect_learn` | Fetch a spec URL (OpenAPI 3 JSON / Swagger 2 / Postman v2.1), compile endpoints+params with per-endpoint provenance into a manifest | ✅ working |
| `architect_commit` | Draft → active (human-visible review: base_url, auth binding, provenance) | ✅ working |
| `architect_list` | Learned APIs, persistent across sessions | ✅ working |
| `api_call` | Schema-checked execution; invalid params fail locally, never as server 400s | ✅ working |

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

## Test scope (honest)

- `python -m unittest discover tests` — **247 tests**: 208 module tests (digest, parsers,
  netguard, redactor, secrets_bridge) **+ the red-lens regression suite**
  (`tests/test_redlens_fixes.py`, from an external adversarial audit of this
  code) + the handler-stack tests: pure stdlib, no network except loopback
  servers inside netguard's own tests.
- `tests/test_handlers.py` runs the FULL handler stack (learn → draft refusal →
  commit → list → api_call) with a mocked network adapter, and **skips cleanly
  where Hermes core is not importable** (bare machines): the four tool handlers
  sit on `plugin_data_dir`/`ctx`, which exist only inside a Hermes host.
- Covered so far on Python 3.12 (Ubuntu) and 3.14 (Windows + Android/Termux).
  The 3.11 floor (core's minimum) is untested at this time — declared.

## v0 security limits (stated, not hidden)

- **DNS rebinding, pre-pin:** the guard resolves ONCE, validates the addresses,
  and pins the connection to the validated IP (SNI/Host keep the hostname). A
  resolver POISONED *before* `guarded_fetch` is called can still hand us an
  attacker-controlled but public-looking IP — out of scope at v0.
- **TLS:** system trust store, standard certificate + hostname verification; no
  client certs, no public-key pinning, no TLS fingerprint handling.
- **Proxies:** none — direct socket connection only.
- **Compression:** never negotiated (`Accept-Encoding: identity` forced); the
  byte cap counts ordinary network bytes; a server that gzip-stamps anyway gets
  its data read opaquely (we do not decompress).
- **Confirm semantics:** destructive (non-GET) calls need an explicit
  `confirm: true` from the calling session. The confirm cannot be issued by
  spec/doc content, but the calling model itself still decides — the tool call
  stays visible in the session's tool trail.
- **Numeric/IPv6-smuggled private addresses** (decimal/hex hosts, `::ffff:…`,
  NAT64/6to4/Teredo) ARE denied by the guard (tested).

## License

MIT — © 2026 Jose Juan Moñino Coll (monyinet)
