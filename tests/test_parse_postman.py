"""Stdlib-only tests for lane L2: Postman v2.x (JSON) → Digest v1.

Run: python -m unittest tests.test_parse_postman -v
Covers tests/fixtures/postman_collection.json exhaustively (3 requests) plus
synthetic collections for var resolution, auth inference, divergence and skips.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parse_postman import detect_postman, digest_from_postman  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "postman_collection.json"
SOURCE_URL = "https://example.com/posts/collection.json"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_id(digest):
    return {e.id: e for e in digest.endpoints}


class TestDetectPostman(unittest.TestCase):
    def test_fixture_detected(self):
        self.assertTrue(detect_postman(_fixture()))

    def test_heuristic_postman_id_detected(self):
        self.assertTrue(
            detect_postman({"info": {"name": "x", "_postman_id": "abc-uuid"}})
        )

    def test_not_postman(self):
        self.assertFalse(detect_postman({"openapi": "3.0.0"}))
        self.assertFalse(detect_postman({"swagger": "2.0"}))
        self.assertFalse(detect_postman(None))
        self.assertFalse(detect_postman("string"))
        self.assertFalse(detect_postman({"info": {"name": "no schema, no id"}}))


class TestFixtureDigest(unittest.TestCase):
    """The 3 fixture requests, asserted endpoint by endpoint."""

    @classmethod
    def setUpClass(cls):
        cls.digest, cls.warnings = digest_from_postman(_fixture(), SOURCE_URL, "architect-test")
        cls.endpoints = _by_id(cls.digest) if cls.digest else {}

    def test_digest_built_and_valid(self):
        self.assertIsNotNone(self.digest)
        self.assertTrue(self.digest.valid())

    def test_base_url_from_first_parseable_request(self):
        self.assertEqual(self.digest.base_url, "https://echo.example.org")

    def test_api_name_argument_wins(self):
        self.assertEqual(self.digest.api_name, "architect-test")

    def test_detection(self):
        det = self.digest.detection
        self.assertEqual(det["mode"], "postman")
        self.assertEqual(det["spec_url"], SOURCE_URL)
        self.assertEqual(det["confidence"], 0.95)  # explicit info.schema → not heuristic
        det = json.loads(self.digest.to_json())["detection"]  # survives roundtrip
        self.assertEqual(det["confidence"], 0.95)

    def test_collection_auth_bearer_without_copying_values(self):
        auth = self.digest.auth
        self.assertEqual(auth.type, "bearer")
        self.assertEqual(auth.location, "")  # bearer has no header/query location
        self.assertEqual(auth.secret_binding, "")  # parser never binds secrets
        # The fixture bearer value is a variable ({{api_token}}) — no value leaked:
        self.assertFalse(any("embedded credential" in w for w in self.warnings))
        payload = self.digest.to_json()
        self.assertNotIn("{{token}}", payload)
        self.assertNotIn("{{api_token}}", payload)
        self.assertNotIn("123", payload)  # DELETE pathvar example value leaks nowhere

    def test_three_endpoints(self):
        self.assertEqual(
            sorted(self.endpoints.keys()),
            ["delete-messages-messageid", "get-greeting", "post-messages"],
        )

    def test_get_greeting_endpoint(self):
        e = self.endpoints["get-greeting"]
        self.assertEqual(e.method, "GET")
        self.assertEqual(e.path, "/greeting")
        self.assertEqual(
            [(p.name, p.location, p.required, p.description) for p in e.params],
            [("name", "query", False, "Who to greet")],
        )
        self.assertEqual(e.summary, "Returns a greeting; name is optional.")
        self.assertTrue(e.provenance.valid())
        self.assertEqual(e.provenance.source, "postman-request")
        self.assertEqual(e.provenance.detail, "item: Get Greeting")
        self.assertFalse(e.is_destructive())

    def test_post_messages_endpoint(self):
        e = self.endpoints["post-messages"]
        self.assertEqual(e.method, "POST")
        self.assertEqual(e.path, "/messages")
        self.assertEqual(e.params[0].name, "body")
        body = [p for p in e.params if p.location == "body"]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0].type, "object")
        self.assertTrue(body[0].required)
        self.assertEqual(e.request_example, {"text": "hello"})  # raw JSON ≤ 4KB
        # Authorization header IS the digest scheme → not duplicated as a param
        self.assertFalse(any(p.location == "header" for p in e.params))
        self.assertTrue(e.is_destructive())
        self.assertEqual(e.provenance.detail, "item: Create Message")
        self.assertTrue(e.valid())

    def test_delete_messages_endpoint_pathvar(self):
        e = self.endpoints["delete-messages-messageid"]
        self.assertEqual(e.method, "DELETE")
        self.assertEqual(e.path, "/messages/{messageId}")
        self.assertEqual(
            [(p.name, p.location, p.required) for p in e.params],
            [("messageId", "path", True)],
        )
        self.assertEqual(e.params[0].description, "Message id (path)")
        self.assertEqual(e.provenance.detail, "item: Delete Message")

    def test_no_schema_variable_warnings_for_fixture(self):
        clean = [w for w in self.warnings if "sin valor resoluble" in w or "inferido" in w]
        self.assertEqual(clean, [])
        self.assertEqual(self.digest.warnings, [])  # caller merges returned warnings

    def test_generated_at_iso_utc(self):
        self.assertTrue(self.digest.generated_at.endswith("+00:00"))

    def test_warning_list_is_plain_strings(self):
        self.assertTrue(all(isinstance(w, str) for w in self.warnings))


class TestPostmanEdges(unittest.TestCase):
    def test_heuristic_confidence(self):
        obj = _fixture()
        del obj["info"]["schema"]
        digest, _warn = digest_from_postman(obj, SOURCE_URL, "api")
        self.assertEqual(digest.detection["confidence"], 0.7)
        self.assertTrue(digest.valid())

    def test_folder_prefix_in_id_and_provenance(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Admin",
                    "item": [
                        {
                            "name": "List Users",
                            "request": {
                                "method": "GET",
                                "url": {"host": ["api", "x", "com"], "path": ["users"], "protocol": "https"},
                            },
                        }
                    ],
                }
            ],
        }
        digest, warn = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual(digest.base_url, "https://api.x.com")
        self.assertEqual(digest.api_name, "api")  # fallback when none passed and no info.name
        self.assertEqual(digest.endpoints[0].path, "/users")
        self.assertEqual(digest.endpoints[0].provenance.detail, "item: Admin/List Users")
        self.assertFalse(any("inferido" in w for w in warn))

    def test_baseurl_collection_variable_resolved(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [{"key": "baseUrl", "value": "https://echo.example.org"}],
            "item": [
                {
                    "name": "Ping",
                    "request": {
                        "method": "GET",
                        "url": {"host": ["{{baseUrl}}"], "path": ["ping"]},
                    },
                }
            ],
        }
        digest, warn = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual(digest.base_url, "https://echo.example.org")
        self.assertEqual(digest.endpoints[0].path, "/ping")
        self.assertFalse(any("sin valor resoluble" in w for w in warn))

    def test_unresolvable_host_variable_falls_back_to_origin(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Ping",
                    "request": {
                        "method": "GET",
                        "url": {"host": ["{{missingVar}}"], "path": ["ping"], "protocol": "https"},
                    },
                }
            ],
        }
        digest, warn = digest_from_postman(obj, "https://origin.example/docs/x.json", "")
        self.assertEqual(digest.base_url, "https://origin.example")
        self.assertTrue(any("sin valor resoluble" in w for w in warn))
        self.assertTrue(any("origen de source_url" in w for w in warn))
        self.assertTrue(digest.valid())  # '{{missingVar}}' never enters the digest

    def test_string_url_with_query(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Things",
                    "request": {"method": "GET", "url": "https://api.example.org/v1/things?limit=5"},
                }
            ],
        }
        digest, _ = digest_from_postman(obj, "https://x.c/c.json", "")
        e = digest.endpoints[0]
        self.assertEqual(digest.base_url, "https://api.example.org")
        self.assertEqual(e.path, "/v1/things")
        self.assertEqual([p.name for p in e.params if p.location == "query"], ["limit"])

    def test_inference_from_authorization_headers(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Secret thing",
                    "request": {
                        "method": "GET",
                        "url": {"host": ["api", "x", "com"], "path": ["t"]},
                        "header": [{"key": "Authorization", "value": "Bearer whatever-never-copied"}],
                    },
                }
            ],
        }
        digest, warn = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual(digest.auth.type, "bearer")
        self.assertTrue(any("inferido" in w for w in warn))
        payload = digest.to_json()
        self.assertNotIn("whatever-never-copied", payload)

    def test_embedded_collection_credential_warned_not_copied(self):
        obj = _fixture()
        obj["auth"]["bearer"][0]["value"] = "hunter2-inline"
        digest, warn = digest_from_postman(obj, SOURCE_URL, "")
        self.assertTrue(any("postman collection embedded credential value" in w for w in warn))
        self.assertEqual(digest.auth.type, "bearer")
        self.assertNotIn("hunter2-inline", digest.to_json())

    def test_request_level_auth_divergence_warned(self):
        obj = _fixture()
        obj["item"][0]["request"]["auth"] = {
            "type": "apikey",
            "apikey": [{"key": "in", "value": "header"}, {"key": "key", "value": "X-Api-Key"}],
        }
        digest, warn = digest_from_postman(obj, SOURCE_URL, "")
        self.assertEqual(digest.auth.type, "bearer")  # dominant stays collection-level
        self.assertTrue(any("divergencia" in w or "distinta" in w for w in warn))

    def test_non_json_body_and_formdata_skipped(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Text thing",
                    "request": {
                        "method": "POST",
                        "url": {"host": ["api", "x", "com"], "path": ["t"]},
                        "body": {"mode": "raw", "raw": "plain", "options": {"raw": {"language": "text"}}},
                        "header": [{"key": "X-Rate-Limit-High", "value": "on"}],
                    },
                },
                {
                    "name": "Form thing",
                    "request": {
                        "method": "POST",
                        "url": {"host": ["api", "x", "com"], "path": ["f"]},
                        "body": {"mode": "formdata", "formdata": [{"key": "file"}]},
                    },
                },
            ],
        }
        digest, warn = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual(len(digest.endpoints), 2)
        self.assertEqual([p.location for p in digest.endpoints[0].params], [])
        self.assertTrue(any("headers no parametrizables en v0" in w for w in warn))
        self.assertTrue(any("omitidos en v0" in w for w in warn))
        self.assertFalse(any(p.location == "body" for p in digest.endpoints[1].params))

    def test_duplicate_ids_dedupe(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {"name": "A", "request": {"method": "GET", "url": {"host": ["api", "x", "com"], "path": ["users"]}}},
                {"name": "B", "request": {"method": "GET", "url": {"host": ["api", "x", "com"], "path": ["users"]}}},
            ],
        }
        digest, _ = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual([e.id for e in digest.endpoints], ["get-users", "get-users-2"])
        self.assertTrue(all(e.valid() for e in digest.endpoints))

    def test_path_variable_without_definition_documented(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {
                    "name": "Thing",
                    "request": {
                        "method": "GET",
                        "url": {"host": ["api", "x", "com"], "path": ["things", "{{thingId}}"]},
                    },
                }
            ],
        }
        digest, _ = digest_from_postman(obj, "https://x.c/c.json", "")
        p = digest.endpoints[0].params[0]
        self.assertEqual((p.name, p.location, p.required), ("thingId", "path", True))
        self.assertEqual(p.description, "no resuelto en la colección")

    def test_not_a_collection_returns_none(self):
        digest, warn = digest_from_postman({"foo": "bar"}, SOURCE_URL, "x")
        self.assertIsNone(digest)
        self.assertTrue(warn)

    def test_supported_methods_only(self):
        obj = {
            "info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [
                {"name": "Weird", "request": {"method": "PROPFIND", "url": {"host": ["api", "x", "com"], "path": ["w"]}}},
                {"name": "Ok", "request": {"method": "get", "url": {"host": ["api", "x", "com"], "path": ["o"]}}},
            ],
        }
        digest, warn = digest_from_postman(obj, "https://x.c/c.json", "")
        self.assertEqual([e.method for e in digest.endpoints], ["GET"])
        self.assertTrue(any("PROPFIND" in w for w in warn))


if __name__ == "__main__":
    unittest.main()
