"""Tests for secrets_bridge.py: slot mapping, lazy-secret-route resolution with
injected fakes (never real env reads), route/unset classification, and the
strip_secret_values deep-scrub. Catalog rule 11 guard included.

Run: python -m unittest tests.test_secrets_bridge -v
"""
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import secrets_bridge  # noqa: E402
from secrets_bridge import (  # noqa: E402
    SLOT_ENV,
    resolve_secret,
    slot_env_name,
    strip_secret_values,
)

SECRET = "SECRETVALUE-XYZ-0123456789"


class TestSlotEnvName(unittest.TestCase):
    def test_table_matches_plugin_yaml_slots(self):
        self.assertEqual(
            SLOT_ENV,
            {"api_key_1": "API_ARCHITECT_API_KEY_1", "api_key_2": "API_ARCHITECT_API_KEY_2"},
        )

    def test_valid_slots(self):
        self.assertEqual(slot_env_name("api_key_1"), "API_ARCHITECT_API_KEY_1")
        self.assertEqual(slot_env_name("api_key_2"), "API_ARCHITECT_API_KEY_2")

    def test_invalid_slots_return_none(self):
        for bad in ["", "api_key_3", "slot9", "API_KEY_1", "api_key None", "0", None, " api_key_1"]:
            self.assertIsNone(slot_env_name(bad), bad)


class TestResolveSecret(unittest.TestCase):
    """The route function is injected via secrets_bridge._get_secret (test seam);
    nothing here reads a real credential store or os.environ."""

    def setUp(self):
        self._orig_fn = secrets_bridge._get_secret
        self._orig_err = secrets_bridge._secret_route_error
        secrets_bridge._secret_route_error = None

    def tearDown(self):
        secrets_bridge._get_secret = self._orig_fn
        secrets_bridge._secret_route_error = self._orig_err

    def test_resolved_value_and_note(self):
        secrets_bridge._get_secret = lambda env: SECRET
        self.assertEqual(resolve_secret("api_key_1"), (SECRET, "resolved"))

    def test_fake_receives_env_name_not_slot_name(self):
        seen = []
        secrets_bridge._get_secret = lambda env: seen.append(env) or SECRET
        resolve_secret("api_key_2")
        self.assertEqual(seen, ["API_ARCHITECT_API_KEY_2"])

    def test_value_never_appears_in_note(self):
        secrets_bridge._get_secret = lambda env: SECRET
        _, note = resolve_secret("api_key_1")
        self.assertNotIn(SECRET, note)

    def test_missing_key_none_and_empty_map_to_not_set(self):
        # Verified 0.22.5 behavior: get_secret returns None for an absent key.
        for fake in (lambda env: None, lambda env: ""):
            secrets_bridge._get_secret = fake
            self.assertEqual(resolve_secret("api_key_1"), (None, "not_set"))
            self.assertEqual(resolve_secret("api_key_2"), (None, "not_set"))

    def test_keyerror_exception_maps_to_not_set(self):
        # Absent-key signaling style (hypothetical future route / dict fakes).
        def fake(env):
            raise KeyError(env)

        secrets_bridge._get_secret = fake
        self.assertEqual(resolve_secret("api_key_1"), (None, "not_set"))

    def test_scope_failure_maps_to_route_unavailable(self):
        def fake(env):
            raise RuntimeError("no profile secret scope active while multiplexing is on")

        secrets_bridge._get_secret = fake
        value, note = resolve_secret("api_key_1")
        self.assertIsNone(value)
        self.assertTrue(note.startswith("route_unavailable:"), note)
        self.assertIn("no profile secret scope active", note)

    def test_unscoped_secret_error_shape_is_route_unavailable(self):
        # Mirrors agent.secret_scope.UnscopedSecretError (carries secret_name).
        class FakeUnscoped(RuntimeError):
            def __init__(self):
                super().__init__("Hermes could not read this profile's API key (...)")
                self.secret_name = "API_ARCHITECT_API_KEY_1"

        secrets_bridge._get_secret = lambda env: (_ for _ in ()).throw(FakeUnscoped())
        value, note = resolve_secret("api_key_1")
        self.assertIsNone(value)
        self.assertTrue(note.startswith("route_unavailable:"), note)

    def test_multiline_exception_note_is_single_line_and_capped(self):
        def fake(env):
            raise RuntimeError("line one\nline two " + "x" * 500)

        secrets_bridge._get_secret = fake
        _, note = resolve_secret("api_key_1")
        self.assertNotIn("\n", note)
        self.assertLessEqual(len(note), 220)  # prefix + capped line

    def test_invalid_slot_short_circuits_route(self):
        def fake(env):  # route must NOT be called
            raise AssertionError("route must not be called for an invalid slot")

        secrets_bridge._get_secret = fake
        self.assertEqual(resolve_secret("api_key_3"), (None, "invalid_slot"))

    def test_never_touches_real_route_when_seam_set_even_if_agent_missing(self):
        with unittest.mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None}):
            secrets_bridge._get_secret = lambda env: SECRET
            self.assertEqual(resolve_secret("api_key_1"), (SECRET, "resolved"))


class TestLazyRouteImport(unittest.TestCase):
    """Without the seam, resolve_secret lazily imports agent.secret_scope; in the
    test environment that module does not exist -> clean route_unavailable note
    (no crash, no env read)."""

    def setUp(self):
        self._orig_fn = secrets_bridge._get_secret = None
        self._orig_err = secrets_bridge._secret_route_error

    def tearDown(self):
        secrets_bridge._get_secret = self._orig_fn
        secrets_bridge._secret_route_error = self._orig_err

    def test_import_failure_is_route_unavailable(self):
        with unittest.mock.patch.dict(sys.modules, {"agent": None, "agent.secret_scope": None}):
            value, note = resolve_secret("api_key_1")
        self.assertIsNone(value)
        self.assertTrue(note.startswith("route_unavailable:"), note)
        self.assertNotIn("API_ARCHITECT_API_KEY_1:", note)

    def test_module_never_imports_os_rule11(self):
        src = Path(secrets_bridge.__file__).read_text(encoding="utf-8")
        bad = re.search(
            r"^\s*(?:import\s+os\s*(?:#.*)?$|from\s+os\b)", src, re.MULTILINE
        )
        self.assertIsNone(bad, "secrets_bridge must not import os (catalog rule 11)")


class TestStripSecretValues(unittest.TestCase):
    def test_nested_dict_and_list_dotted(self):
        data = {
            "auth": {"secret_binding": "api_key_1", "value": SECRET},
            "endpoints": [{"id": "get-user", "note": SECRET}],
            "count": 2,
        }
        out = strip_secret_values(data, [SECRET])
        self.assertEqual(
            out,
            {
                "auth": {"secret_binding": "api_key_1", "value": "[REDACTED]"},
                "endpoints": [{"id": "get-user", "note": "[REDACTED]"}],
                "count": 2,
            },
        )

    def test_non_string_leaves_untouched(self):
        self.assertEqual(strip_secret_values({"n": 5, "f": 1.5, "b": False, "z": None}, []), {"n": 5, "f": 1.5, "b": False, "z": None})

    def test_exact_equality_only_substrings_survive(self):
        self.assertEqual(
            strip_secret_values({"a": SECRET, "b": "prefix-" + SECRET + "-suffix", "c": "clean"}, [SECRET]),
            {"a": "[REDACTED]", "b": "prefix-" + SECRET + "-suffix", "c": "clean"},
        )

    def test_input_not_mutated(self):
        data = {"a": SECRET}
        strip_secret_values(data, [SECRET])
        self.assertEqual(data, {"a": SECRET})

    def test_no_or_empty_values_noop(self):
        data = {"a": SECRET, "b": ""}
        self.assertEqual(strip_secret_values(data), data)
        self.assertEqual(strip_secret_values(data, [None, ""]), data)
        self.assertEqual(strip_secret_values({"a": ""}, [""]), {"a": ""})


if __name__ == "__main__":
    unittest.main()
