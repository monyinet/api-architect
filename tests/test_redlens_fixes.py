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


class TestStrictConfirmTruthiness(unittest.TestCase):  # red-lens B/C
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


if __name__ == "__main__":
    unittest.main()
