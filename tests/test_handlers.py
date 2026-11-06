"""Handler-stack smoke tests — run ONLY where Hermes core is importable;
skipped cleanly on bare machines (peers/CI) by design: the four tool handlers
live on top of plugin_data_dir and ctx, which exist only inside a Hermes host.

No network: the loader below materialises the plugin as a synthetic package the
way Hermes does (hermes_plugins.<slug>, dir NOT on sys.path), and the mocks
patch the PACKAGE copies (_sib resolves package-relative first — plugin-owned
names beat any foreign top-level module, catalog-review regression). Patching a
flat module a package _sib doesn't return is a no-op: patch the copy the handler
actually binds (lesson of 5-nov, updated for package-first ownership). A fresh
tempfile HERMES_HOME per run kills state leaks between runs."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    import plugins.plugin_storage  # type: ignore[attr-defined]  # noqa: F401

    _HERMES_OK = True
except Exception:
    _HERMES_OK = False

try:
    _FIXTURE = (REPO / "tests" / "fixtures" / "petstore.json").read_text(encoding="utf-8")
except OSError:
    _FIXTURE = ""

_PKG = "apiarch_pkg"
_PKG_LAZY = ("learn", "executor", "redactor", "secrets_bridge", "parse_spec", "parse_postman")


@unittest.skipUnless(_HERMES_OK, "handlers need Hermes core importable (plugin host)")
@unittest.skipUnless(_FIXTURE, "petstore fixture missing")
class TestHandlerStack(unittest.TestCase):
    """learn -> persist draft -> refuse call -> commit -> list -> api_call (mocked net)."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="architect-test-home-")
        os.environ["HERMES_HOME"] = str(cls.home)
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(REPO)]
        sys.modules[_PKG] = pkg
        for name in ("digest",) + _PKG_LAZY + ("parse_swagger2", "tools"):
            if _PKG + "." + name in sys.modules:
                continue
            spec = importlib.util.spec_from_file_location(_PKG + "." + name, REPO / (name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[_PKG + "." + name] = mod
            spec.loader.exec_module(mod)
        cls.tools = sys.modules[_PKG + ".tools"]
        cls.pkg_learn = sys.modules[_PKG + ".learn"]
        cls.pkg_executor = sys.modules[_PKG + ".executor"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)
        for name in list(sys.modules):
            if name.startswith(_PKG + "."):
                sys.modules.pop(name, None)

    def test_learn_persists_draft_not_callable_then_commit_then_call(self):
        canned_learn = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _FIXTURE.encode("utf-8"),
            "url": "https://petstore3.swagger.io/api/v3/openapi.json",
        }
        canned_call = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps([{"id": 1, "name": "Dog 1", "status": "available"}]).encode("utf-8"),
            "url": "https://petstore3.swagger.io/api/v3/pet/findByStatus?status=available",
        }
        with mock.patch.object(
            type(self).pkg_learn, "guarded_fetch", return_value=canned_learn
        ), mock.patch.object(type(self).pkg_executor, "guarded_fetch", return_value=canned_call):
            r = json.loads(
                self.tools.architect_learn(
                    {"url": "https://petstore3.swagger.io/api/v3/openapi.json", "api_name": "petstore"}
                )
            )
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r.get("state"), "draft")

            # draft NOT callable
            r2 = json.loads(self.tools.api_call({"api_name": "petstore", "endpoint": "GET /pet/findByStatus"}))
            self.assertFalse(r2.get("ok"))
            self.assertIn("DRAFT", str(r2.get("error", "")))

            r3 = json.loads(self.tools.architect_commit({"api_name": "petstore"}))
            self.assertTrue(r3.get("ok"), r3)
            self.assertEqual(r3.get("endpoints"), 19)

            r4 = json.loads(self.tools.architect_list({}))
            slugs = {a["slug"] for a in r4.get("apis", [])}
            self.assertIn("petstore", slugs)

            r5 = json.loads(
                self.tools.api_call(
                    {"api_name": "petstore", "endpoint": "GET /pet/findByStatus", "params": {"status": "available"}}
                )
            )
            self.assertTrue(r5.get("ok"), r5)
            self.assertEqual(r5.get("status"), 200)
            names = [p.get("name") for p in (r5.get("response") or []) if isinstance(p, dict)]
            self.assertIn("Dog 1", names)

            # destructive gate + unknown endpoint
            r6 = json.loads(self.tools.api_call({"api_name": "petstore", "endpoint": "PUT /pet"}))
            self.assertTrue(r6.get("needs_confirmation"))
            r7 = json.loads(self.tools.api_call({"api_name": "petstore", "endpoint": "DELETE /not-there"}))
            self.assertFalse(r7.get("ok"))
            self.assertIn("available", r7)

    def test_learned_manifest_warnings_are_not_duplicated(self):
        """Parser warnings used to reach the file twice (digest.warnings + the pipeline list)."""
        canned_learn = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _FIXTURE.encode("utf-8"),
            "url": "https://petstore3.swagger.io/api/v3/openapi.json",
        }
        with mock.patch.object(type(self).pkg_learn, "guarded_fetch", return_value=canned_learn):
            r = json.loads(
                self.tools.architect_learn(
                    {"url": "https://petstore3.swagger.io/api/v3/openapi.json", "api_name": "warncheck"}
                )
            )
        self.assertTrue(r.get("ok"), r)
        draft = json.loads(
            (self.tools._manifests_dir() / "warncheck.draft.json").read_text(encoding="utf-8")
        )
        stored = draft.get("warnings") or []
        self.assertTrue(stored, "a learned OpenAPI 3 manifest should carry the parser's warnings")
        self.assertEqual(len(stored), len(set(stored)), "duplicate warnings in the manifest: {}".format(stored))


if __name__ == "__main__":
    unittest.main()
