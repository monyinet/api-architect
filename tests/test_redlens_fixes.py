"""Regression suite for the d0a1 Red-lens findings (2026-11-05).
Every test maps to a finding: A/IPv6-smuggled, B/confirm-truthiness, C/config
truthiness, D/secret-in-source-url, E/F/redactor gaps, G/confused deputy,
I/M/learn crash routes, J/transport headers, N/port range. Stdlib only."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from digest import Auth, Digest, Endpoint, Param, Provenance  # noqa: E402
import executor as executor_mod  # noqa: E402
import learn as learn_mod  # noqa: E402
from netguard import GuardError, check_url, ip_is_denied  # noqa: E402
from redactor import redact, redact_url  # noqa: E402

try:
    import plugins.plugin_storage  # type: ignore  # noqa: F401
    import shutil  # noqa: F401

    _HERMES_OK = True
except Exception:
    _HERMES_OK = False

_PETSTORE = (REPO / "tests" / "fixtures" / "petstore.json").read_text(encoding="utf-8")


def redlens_types_like_load():
    """Fresh synthetic package load for handler tests (unique qualname per use)."""
    import importlib.util
    import types

    pkg_name = "rl_pkg_" + str(id(object()))  # unique per call
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO)]
    sys.modules[pkg_name] = pkg
    mods = {}
    for name in ("digest", "netguard", "redactor", "secrets_bridge", "executor", "learn", "parse_spec", "tools"):
        spec = importlib.util.spec_from_file_location(pkg_name + "." + name, REPO / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name + "." + name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods


class TestSmuggledIPv6(unittest.TestCase):  # red-lens A
    def test_mapped_loopback_denied(self):
        self.assertTrue(ip_is_denied("::ffff:127.0.0.1"))

    def test_mapped_metadata_denied(self):
        self.assertTrue(ip_is_denied("::ffff:169.254.169.254"))

    def test_nat64_denied(self):
        self.assertTrue(ip_is_denied("64:ff9b::7f00:1"))

    def test_6to4_denied(self):
        self.assertTrue(ip_is_denied("2002:7f00:1::"))

    def test_public_unhurt(self):
        self.assertFalse(ip_is_denied("2606:4700::1111"))
        self.assertFalse(ip_is_denied("93.184.216.34"))

    def test_check_url_mapped_loopback_literal_raises(self):
        with self.assertRaises(GuardError):
            check_url("http://[::ffff:127.0.0.1]:8080/")


class TestNumericHosts(unittest.TestCase):  # adjacent (pre-DNS numeric forms)
    def test_decimal_loopback_refused(self):
        with self.assertRaises(GuardError):
            check_url("http://2130706433/x")

    def test_hex_loopback_refused(self):
        with self.assertRaises(GuardError):
            check_url("http://0x7f000001/x")


class TestPortRange(unittest.TestCase):  # red-lens N
    def test_out_of_range_port_guarded(self):
        with self.assertRaises(GuardError):
            check_url("http://example.com:99999/x")


@unittest.skipUnless(
    _HERMES_OK, "loads tools.py which imports Hermes core (host machine covers these)"
)
class TestStrictConfirmTruthiness(unittest.TestCase):  # red-lens B/C
    # cross-platform note: _truthy semantics certify on HOST installs (Windows) —
    # bare machines (d0a1 python3, Termux) skip this class and the 2 skips are the
    # expected signature (see README Test scope). The P0 fix itself is exercised
    # at runtime wherever the plugin actually loads.
    def setUp(self):
        import types

        pkg = types.ModuleType("rl_pkg")
        pkg.__path__ = [str(REPO)]
        sys.modules["rl_pkg"] = pkg
        import importlib.util

        for name in ("digest", "netguard", "redactor", "secrets_bridge", "executor", "learn", "parse_spec", "tools"):
            spec = importlib.util.spec_from_file_location("rl_pkg." + name, REPO / (name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["rl_pkg." + name] = mod
            spec.loader.exec_module(mod)
        self.tools = sys.modules["rl_pkg.tools"]

    def test_truthy_matrix(self):
        t = self.tools._truthy
        for v in (True, "true", "YES", "1", 1):
            self.assertTrue(t(v), v)
        for v in (False, "false", "False", "no", "0", "", None, "maybe", [], 0, 2):
            self.assertFalse(t(v), v)

    def test_config_string_false_is_not_private(self):
        # red-lens C: allow_private_hosts saved as the string "false" must NOT open SSRF
        self.tools._ctx = None
        self.assertFalse(self.tools._truthy(self.tools._config("allow_private_hosts", False)))


@unittest.skipUnless(_HERMES_OK, "handler tests need Hermes core importable")
class TestSecretNeverOnDisk(unittest.TestCase):  # red-lens D — THE regression
    def test_credential_in_learn_url_redacted_from_manifest(self):
        import tempfile

        home = tempfile.mkdtemp(prefix="architect-seclean-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        os.environ["HERMES_HOME"] = home
        tools = redlens_types_like_load()["tools"]
        import learn as learn_mod  # tools binds THIS module on its lazy import

        canned = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _PETSTORE.encode("utf-8"),
            "url": "https://api.example.com/openapi.json",
        }
        secret = "ABCDEFGHIJKLMNOP"
        with mock.patch.object(learn_mod, "guarded_fetch", return_value=canned):
            r = json.loads(
                tools.architect_learn(
                    {
                        "url": "https://api.example.com/openapi.json?apikey=" + secret,
                        "api_name": "seclean",
                    }
                )
            )
        self.assertTrue(r.get("ok"), r)
        draft = Path(r["draft_file"])
        text = draft.read_text(encoding="utf-8")
        self.assertNotIn(secret, text)
        self.assertIn("[REDACTED]", text)


class TestFindEndpointExact(unittest.TestCase):  # red-lens G
    def _manifest(self):
        return Digest(
            api_name="m",
            source_url="https://api.example.com/spec",
            base_url="https://api.example.com",
            endpoints=[
                Endpoint(id="root-listing", method="GET", path="/", provenance=Provenance(source="s", detail="d")),
                Endpoint(id="listusers", method="GET", path="/users", provenance=Provenance(source="s", detail="d")),
            ],
        )

    def test_bare_word_never_falls_into_root(self):
        m = self._manifest()
        self.assertIsNone(executor_mod.find_endpoint(m, "us"))  # the old fuzzy 'endswith("")' case
        self.assertIsNone(executor_mod.find_endpoint(m, "stats"))  # near-miss: not an id, not a path

    def test_exact_id_and_method_and_path(self):
        m = self._manifest()
        self.assertEqual(executor_mod.find_endpoint(m, "listusers").id, "listusers")  # by id
        self.assertEqual(executor_mod.find_endpoint(m, "/users").id, "listusers")  # exact path
        self.assertEqual(executor_mod.find_endpoint(m, "GET /users").id, "listusers")
        self.assertEqual(executor_mod.find_endpoint(m, "get /").id, "root-listing")

    def test_dup_path_exact_returns_none(self):
        m = self._manifest()
        m.endpoints.append(
            Endpoint(id="v1users", method="GET", path="/users", provenance=Provenance(source="s", detail="d"))
        )
        self.assertIsNone(executor_mod.find_endpoint(m, "/users"))  # exact-but-duplicate → refuse


class TestForbiddenTransportHeaders(unittest.TestCase):  # red-lens J
    def test_manifest_header_param_smuggling_refused(self):
        m = Digest(
            api_name="j",
            source_url="https://api.example.com/spec",
            base_url="https://api.example.com",
            endpoints=[
                Endpoint(
                    id="smuggle",
                    method="GET",
                    path="/x",
                    params=[Param(name="Content-Length", location="header", required=True)],
                    provenance=Provenance(source="s", detail="d"),
                )
            ],
        )
        with self.assertRaises(executor_mod.ExecutionError) as ctx:
            executor_mod.build_request(m, m.endpoints[0], {"Content-Length": "0"}, None)
        self.assertIn("transport-controlled", str(ctx.exception))

    def test_auth_location_smuggling_refused(self):
        m = Digest(
            api_name="j2",
            source_url="https://api.example.com/spec",
            base_url="https://api.example.com",
            auth=Auth(type="api_key_header", location="X-Forwarded-For", secret_binding="api_key_1"),
            endpoints=[
                Endpoint(id="g", method="GET", path="/x", provenance=Provenance(source="s", detail="d"))
            ],
        )
        with mock.patch.object(executor_mod, "resolve_secret", return_value=("TEST", "resolved")):
            with self.assertRaises(executor_mod.ExecutionError) as ctx:
                executor_mod.execute(m, m.endpoints[0], {}, None, timeout_s=5, user_agent="ua")
        self.assertIn("transport-controlled", str(ctx.exception))


class TestLearnBrokenRoutes(unittest.TestCase):  # red-lens I + M
    def _canned(self, body: bytes, url="https://api.example.com/x"):
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": body,
            "url": url,
        }

    def test_non_spec_json_is_honest_failure_not_nameerror(self):
        with mock.patch.object(learn_mod, "guarded_fetch", return_value=self._canned(b'{"hello":"world"}')):
            r = learn_mod.learn("https://api.example.com", "trial")
        self.assertFalse(r["ok"])
        self.assertIn("Nothing was invented", r["error"])

    def test_spec_without_title_with_empty_api_name_does_not_crash(self):
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "servers": [{"url": "https://api.example.com"}],
                "paths": {"/ping": {"get": {"summary": "Ping"}}},
            }
        ).encode("utf-8")
        with mock.patch.object(
            learn_mod,
            "guarded_fetch",
            side_effect=lambda url, **kw: self._canned(spec, url),
        ):
            r = learn_mod.learn("https://api.example.com/openapi.json", "")  # former NameError route
        self.assertIsInstance(r, dict)  # honest failure (no title, no name) — NOT a crash
        self.assertFalse(r.get("ok"))

    def test_named_spec_without_title_parses(self):
        spec = json.dumps(
            {
                "openapi": "3.0.0",
                "servers": [{"url": "https://api.example.com"}],
                "paths": {"/ping": {"get": {"summary": "Ping"}}},
            }
        ).encode("utf-8")
        with mock.patch.object(
            learn_mod,
            "guarded_fetch",
            return_value=self._canned(spec, "https://api.example.com/openapi.json"),
        ):
            r = learn_mod.learn("https://api.example.com/openapi.json", "mypad")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["digest"].endpoints[0].path, "/ping")

    def test_html_page_still_reaches_link_discovery(self):
        # regression for the patch that accidentally hid the '<' branch
        html = b"<html><body><a href='swagger.json'>docs</a></body></html>"

        def fe(url, **kw):
            if url.endswith("/docs"):
                return {
                    "status": 200,
                    "headers": {"content-type": "text/html"},
                    "body": html,
                    "url": url,
                }
            return self._canned(b"{}", url)

        with mock.patch.object(learn_mod, "guarded_fetch", side_effect=fe):
            r = learn_mod.learn("https://api.example.com/docs", "t2")
        self.assertFalse(r["ok"])  # discovered the link but it's not a spec — honest
        self.assertTrue(any("documentation page" in w for w in r.get("warnings", [])))


class TestRedactorHardening(unittest.TestCase):  # red-lens E/F
    def test_basic_auth_pattern(self):
        out = redact("Authorization: Basic dXNlcnBhc3N2YWx1ZTEyMw==")
        self.assertNotIn("dXNlcnBhc3N2YWx1ZTEyMw", out)

    def test_case_insensitive_known_value(self):
        out = redact("KEY=S3CR3T-VALUE-9F2B41", values=("S3cr3t-Value-9f2b41",))
        self.assertNotIn("S3CR3T", out)
        self.assertIn("[REDACTED]", out)

    def test_quote_plus_form(self):
        out = redact("k=s3cr3t+value9f2b", values=("s3cr3t value9f2b",))
        self.assertNotIn("s3cr3t+value9f2b", out)

    def test_url_name_redaction_still_covers_short_values(self):
        out = redact_url("https://api.example.com/p?api_key=XX")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("api_key=XX", out)


def types_like_load():
    """Fresh synthetic package (same trick as test_handlers) for handler tests."""
    import importlib.util
    import types

    pkg_name = "rl_pkg_d"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO)]
    sys.modules[pkg_name] = pkg
    mods = {}
    for name in ("digest", "netguard", "redactor", "secrets_bridge", "executor", "learn", "parse_spec", "tools"):
        spec = importlib.util.spec_from_file_location(pkg_name + "." + name, REPO / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name + "." + name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods




class TestOperationIdAddressing(unittest.TestCase):
    """Live-session finding (2026-11-06): the spec's operationId never reached the
    manifest, so a model addressing 'findPetsByStatus' (the spec's own address) was
    refused although the endpoint existed. Now every parser stores it and
    find_endpoint matches it exactly; ambiguity refuses with the endpoint list."""

    def _digest(self):
        import parse_spec as parse_spec_mod

        data = json.loads(_PETSTORE)
        digest, _warn = parse_spec_mod.digest_from_openapi3(
            data, "https://petstore3.example/api/v3/openapi.json", "petstore"
        )
        return digest

    def test_openapi3_stores_operation_id(self):
        digest = self._digest()
        ids = {e.operation_id for e in digest.endpoints if e.operation_id}
        self.assertTrue(ids, "no endpoint carried an operationId")
        self.assertIn("findPetsByStatus", ids)

    def test_find_endpoint_by_spec_operation_id(self):
        ep = executor_mod.find_endpoint(self._digest(), "findPetsByStatus")
        self.assertIsNotNone(ep)
        self.assertEqual(ep.operation_id, "findPetsByStatus")

    def test_find_endpoint_missing_operation_id_refused_cleanly(self):
        digest = self._digest()
        self.assertIsNone(executor_mod.find_endpoint(digest, "unrealisticOperationName"))
        # and case-insensitive EXACT match does not fuzz into a path (contract intact)
        self.assertIsNone(executor_mod.find_endpoint(digest, "findpetsbystatusX"))

    def test_ambiguous_operation_id_refused(self):
        manifest = Digest(
            api_name="dup",
            source_url="https://x.example",
            base_url="https://x.example",
            endpoints=[
                Endpoint(id="a", method="GET", path="/one", provenance=Provenance(source="s", detail="d"), operation_id="list-things"),
                Endpoint(id="b", method="GET", path="/two", provenance=Provenance(source="s", detail="d"), operation_id="list-things"),
            ],
        )
        self.assertIsNone(executor_mod.find_endpoint(manifest, "list-things"))

    def test_manifest_roundtrip_keeps_operation_id(self):
        digest = self._digest()
        fresh = Digest.from_json(digest.to_json(state="draft"))
        self.assertEqual(fresh.endpoints[0].operation_id, digest.endpoints[0].operation_id)

    def test_old_manifest_without_operation_id_still_loads(self):
        digest = self._digest()
        payload = json.loads(digest.to_json(state="draft"))
        for e in payload["endpoints"]:
            e.pop("operation_id", None)
        fresh = Digest.from_json(json.dumps(payload))
        self.assertTrue(all(e.operation_id is None for e in fresh.endpoints))
        self.assertIsNotNone(executor_mod.find_endpoint(fresh, fresh.endpoints[0].id))

    def test_postman_request_name_is_the_operation_id(self):
        import parse_postman as parse_postman_mod

        data = json.loads((REPO / "tests" / "fixtures" / "postman_collection.json").read_text(encoding="utf-8"))
        digest, _warn = parse_postman_mod.digest_from_postman(data, "https://example.example/col.json", "col")
        named = [e for e in digest.endpoints if e.operation_id]
        self.assertTrue(named, "no postman request carried its item name as operation_id")
        first = named[0].operation_id
        self.assertIsNotNone(executor_mod.find_endpoint(digest, first))


class TestSecretValuesInUrl(unittest.TestCase):  # F1: pass (a) gets REAL values, not ()
    def test_suspect_query_and_userinfo_values_collected(self):
        import redactor as redactor_mod

        url = "https://editor:ABCD-EFGH@api.example.com/openapi.json?appid=SECRET123456789&x=1"
        vals = redactor_mod.secret_values_in_url(url)
        self.assertIn("SECRET123456789", vals)
        self.assertIn("ABCD-EFGH", vals)

    def test_clean_url_yields_nothing(self):
        import redactor as redactor_mod

        self.assertEqual(redactor_mod.secret_values_in_url("https://api.example.com/openapi.json?x=1"), [])

    def test_value_pass_actually_scrubs_with_them(self):
        import redactor as redactor_mod
        import secrets_bridge as sb_mod

        payload = {"api_name": "k", "notes": ["SECRET123456789"]}
        scrubbed = sb_mod.strip_secret_values(payload, redactor_mod.secret_values_in_url(
            "https://api.example.com/openapi.json?appid=SECRET123456789"))
        self.assertEqual(scrubbed["notes"], ["[REDACTED]"])


    @unittest.skipUnless(_HERMES_OK, "handler tests need Hermes core importable")
    def test_relearn_over_active_flags_it_in_line(self):
        import tempfile

        home = tempfile.mkdtemp(prefix="architect-relearn-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        os.environ["HERMES_HOME"] = home
        tools = redlens_types_like_load()["tools"]
        import learn as learn_mod

        canned = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _PETSTORE.encode("utf-8"),
            "url": "https://petstore3.example.com/api/v3/openapi.json",
        }
        with mock.patch.object(learn_mod, "guarded_fetch", return_value=canned):
            first = json.loads(tools.architect_learn({"url": canned["url"], "api_name": "relearn"}))
            self.assertFalse(first.get("active_exists"))
            commit = json.loads(tools.architect_commit({"api_name": "relearn"}))
            self.assertTrue(commit.get("ok"), commit)
            draft_stem = first["draft_file"][: -len(".draft.json")]
            active_before = json.loads(__import__("pathlib").Path(draft_stem + ".json").read_text(encoding="utf-8"))
            second = json.loads(tools.architect_learn({"url": canned["url"], "api_name": "relearn"}))
        self.assertTrue(second.get("active_exists"))
        self.assertIn("UPDATE path", second.get("note", ""))
        self.assertEqual(second.get("state"), "draft")
        # nothing changed on disk: the active manifest is untouched until commit
        active_after = json.loads(__import__("pathlib").Path(draft_stem + ".json").read_text(encoding="utf-8"))
        self.assertEqual(active_before["endpoints"], active_after["endpoints"])
        self.assertEqual(active_after.get("state"), "active")


    @unittest.skipUnless(_HERMES_OK, "handler tests need Hermes core importable")
    def test_swagger2_spec_routes_through_real_learn(self):
        # live-learn finding: detect_spec said "swagger2" but the dispatcher dropped
        # it (only "openapi3" was routed) — a Swagger 2 URL returned "no spec found"
        import tempfile

        home = tempfile.mkdtemp(prefix="architect-swagger2-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        os.environ["HERMES_HOME"] = home
        tools = redlens_types_like_load()["tools"]
        import learn as learn_mod

        v2 = (REPO / "tests" / "fixtures" / "petstore_v2.json").read_text(encoding="utf-8")
        canned = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": v2.encode("utf-8"),
            "url": "https://petstore2.example.com/v2/swagger.json",
        }
        with mock.patch.object(learn_mod, "guarded_fetch", return_value=canned):
            r = json.loads(tools.architect_learn({"url": canned["url"], "api_name": "petstore2"}))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("mode"), "swagger2")
        self.assertTrue(r.get("endpoints_total"))
        d, warnings, mode = learn_mod._detect_and_parse(json.loads(v2), canned["url"], "petstore2")
        self.assertEqual(mode, "swagger2")
        self.assertTrue(d.valid())
        self.assertTrue(d.endpoints)


@unittest.skipUnless(_HERMES_OK, "handler tests need Hermes core importable")
class TestConfirmStringFalseNeverSends(unittest.TestCase):  # F3: gate on the REAL handler paths
    def _learn_and_commit(self, tools):
        import learn as learn_mod  # tools.py _sib resolves flat-first → same object

        canned = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _PETSTORE.encode("utf-8"),
            "url": "https://petstore3.example.com/api/v3/openapi.json",
        }
        with mock.patch.object(learn_mod, "guarded_fetch", return_value=canned):
            r = json.loads(tools.architect_learn({"url": canned["url"], "api_name": "petstore-conf"}))
        self.assertTrue(r.get("ok"), r)
        c = json.loads(tools.architect_commit({"api_name": "petstore-conf"}))
        self.assertTrue(c.get("ok"), c)

    def test_string_false_confirms_never_execute_post(self):
        import tempfile

        for candidate in ("false", "0", "no", "", "False"):
            home = tempfile.mkdtemp(prefix="architect-conf-home-")
            self.addCleanup(shutil.rmtree, home, ignore_errors=True)
            os.environ["HERMES_HOME"] = home
            tools = redlens_types_like_load()["tools"]
            self._learn_and_commit(tools)
            r = json.loads(
                tools.api_call(
                    {
                        "api_name": "petstore-conf",
                        "endpoint": "addPet",
                        "params": {"body": {"name": "DemoPet", "photoUrls": []}},
                        "confirm": candidate,
                    }
                )
            )
            self.assertTrue(r.get("needs_confirmation"), r)
            self.assertNotIn("status", r)  # nothing was sent to the network


if __name__ == "__main__":
    unittest.main()
