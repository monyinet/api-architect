"""Handler-stack smoke tests — run ONLY where Hermes core is importable;
skipped cleanly on bare machines (peers/CI) by design: the four tool handlers
live on top of plugin_data_dir and ctx, which exist only inside a Hermes host.
No network: netguard.guarded_fetch is mocked with canned spec bytes."""
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    import plugins.plugin_storage  # type: ignore[attr-defined]  # noqa: F401

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
    """learn -> persist draft -> refuse call -> commit -> list -> api_call with mock net."""

    @classmethod
    def setUpClass(cls):
        cls.home = Path(os.environ.get("ARCHITECT_TEST_HOME") or (REPO.parent / ".architect-test-home"))
        os.environ["HERMES_HOME"] = str(cls.home)
        pkg = types.ModuleType("apiarch_pkg")
        pkg.__path__ = [str(REPO)]
        sys.modules.pop("apiarch_pkg.tools", None)
        sys.modules["apiarch_pkg"] = pkg
        for name in (
            "digest",
            "netguard",
            "redactor",
            "secrets_bridge",
            "executor",
            "learn",
            "parse_spec",
            "tools",
        ):
            spec = importlib.util.spec_from_file_location("apiarch_pkg." + name, REPO / (name + ".py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["apiarch_pkg." + name] = mod
            spec.loader.exec_module(mod)
        cls.tools = sys.modules["apiarch_pkg.tools"]
        cls.learn = sys.modules["apiarch_pkg.learn"]

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name.startswith("apiarch_pkg."):
                sys.modules.pop(name, None)

    def _mock_net(self):
        from unittest import mock

        resp = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": _FIXTURE.encode("utf-8"),
            "url": "https://petstore3.swagger.io/api/v3/openapi.json",
        }
        return mock.patch.object(self.learn, "guarded_fetch", return_value=resp)

    def test_learn_persists_draft_not_callable_then_commit_then_call(self):
        with self._mock_net():
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

        # real execution still needs the network through netguard — PATCH executor's view:
        from unittest import mock

        import subprocess  # noqa: F401

        executor = sys.modules["apiarch_pkg.executor"]
        canned = mock.Mock(
            return_value={
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": json.dumps([{"id": 1, "name": "Dog 1", "status": "available"}]).encode("utf-8"),
                "url": "https://petstore3.swagger.io/api/v3/pet/findByStatus?status=available",
            }
        )
        with mock.patch.object(executor, "guarded_fetch", canned):
            r5 = json.loads(
                self.tools.api_call(
                    {"api_name": "petstore", "endpoint": "GET /pet/findByStatus", "params": {"status": "available"}}
                )
            )
        self.assertTrue(r5.get("ok"), r5)
        self.assertEqual(r5.get("status"), 200)
        names = [p.get("name") for p in (r5.get("response") or []) if isinstance(p, dict)]
        self.assertIn("Dog 1", names)

        # unknown endpoint refused, destructive needs confirm
        r6 = json.loads(self.tools.api_call({"api_name": "petstore", "endpoint": "PUT /pet"}))
        self.assertTrue(r6.get("needs_confirmation"))
        r7 = json.loads(self.tools.api_call({"api_name": "petstore", "endpoint": "DELETE /not-there"}))
        self.assertFalse(r7.get("ok"))
        self.assertIn("available", r7)


if __name__ == "__main__":
    unittest.main()
