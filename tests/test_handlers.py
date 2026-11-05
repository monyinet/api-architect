"""Handler-stack smoke tests — run ONLY where Hermes core is importable;
skipped cleanly on bare machines (peers/CI) by design: the four tool handlers
live on top of plugin_data_dir and ctx, which exist only inside a Hermes host.

No network: BOTH mocks patch the REAL top-level `learn` and `executor` modules,
because tools lazy-imports them BY NAME ("from learn import learn") at call
time — patching a synthetic package copy is a no-op (lesson of 5-nov). A fresh
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
    import learn as real_learn_mod  # noqa: F401,E402
    import executor as real_executor_mod  # noqa: F401,E402

    _HERMES_OK = True
except Exception:
    _HERMES_OK = False

try:
    _FIXTURE = (REPO / "tests" / "fixtures" / "petstore.json").read_text(encoding="utf-8")
except OSError:
    _FIXTURE = ""


@unittest.skipUnless(_HERMES_OK, "handlers need Hermes core importable (plugin host)")
@unittest.skipUnless(_FIXTURE, "petstore fixture missing")
class TestHandlerStack(unittest.TestCase):
    """learn -> persist draft -> refuse call -> commit -> list -> api_call (mocked net)."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="architect-test-home-")
        os.environ["HERMES_HOME"] = str(cls.home)
        pkg = types.ModuleType("apiarch_pkg")
        pkg.__path__ = [str(REPO)]
        sys.modules["apiarch_pkg"] = pkg
        for name in ("digest", "tools"):  # only these need the synthetic pkg (relative imports)
            spec = importlib.util.spec_from_file_location("apiarch_pkg." + name, REPO / (name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["apiarch_pkg." + name] = mod
            spec.loader.exec_module(mod)
        cls.tools = sys.modules["apiarch_pkg.tools"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)
        for name in list(sys.modules):
            if name.startswith("apiarch_pkg."):
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
            real_learn_mod, "guarded_fetch", return_value=canned_learn
        ), mock.patch.object(real_executor_mod, "guarded_fetch", return_value=canned_call):
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


if __name__ == "__main__":
    unittest.main()
