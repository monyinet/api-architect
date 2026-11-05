"""Stdlib-only tests for lane L2: Swagger 2.0 (JSON) → Digest v1.

Run: python -m unittest tests.test_parse_swagger2 -v
Covers tests/fixtures/petstore_v2.json (14 paths × methods) plus synthetic
specs for auth selection, base_url fallbacks and one-level $ref resolution.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parse_swagger2 import detect_swagger2, digest_from_swagger2  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "petstore_v2.json"
SOURCE_URL = "https://petstore.swagger.io/v2/swagger.json"


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_path_method(digest):
    return {(e.method, e.path): e for e in digest.endpoints}


class TestDetectSwagger2(unittest.TestCase):
    def test_fixture_detected(self):
        self.assertTrue(detect_swagger2(_fixture()))

    def test_not_swagger2(self):
        self.assertFalse(detect_swagger2({"openapi": "3.0.0"}))
        self.assertFalse(detect_swagger2({"info": {}, "paths": {}}))
        self.assertFalse(detect_swagger2(None))
        self.assertFalse(detect_swagger2("nope"))
        self.assertFalse(detect_swagger2({"swagger": "3.0.0"}))


class TestPetstoreV2Fixture(unittest.TestCase):
    """Assertions anchored on the real fixture content (read BEFORE writing)."""

    @classmethod
    def setUpClass(cls):
        cls.digest, cls.warnings = digest_from_swagger2(_fixture(), SOURCE_URL, "petstore-test")
        cls.endpoints = _by_path_method(cls.digest) if cls.digest else {}

    def test_digest_built_and_valid(self):
        self.assertIsNotNone(self.digest)
        self.assertTrue(self.digest.valid())

    def test_base_url_schemes_host_basepath(self):
        self.assertEqual(self.digest.base_url, "https://petstore.swagger.io/v2")

    def test_api_name_from_arg(self):
        self.assertEqual(self.digest.api_name, "petstore-test")

    def test_operation_count_and_unique_ids(self):
        # 14 paths, 20 operations on the fixture (checked, not assumed)
        self.assertEqual(len(self.digest.endpoints), 20)
        ids = [e.id for e in self.digest.endpoints]
        self.assertEqual(len(set(ids)), 20)

    def test_auth_favours_api_key_over_oauth2(self):
        auth = self.digest.auth
        self.assertEqual(auth.type, "api_key_header")
        self.assertEqual(auth.location, "api_key")  # securityDefinitions.api_key.name
        self.assertEqual(auth.secret_binding, "")

    def test_oauth2_discarded_warning(self):
        self.assertTrue(
            any("oauth2" in w and "petstore_auth" in w for w in self.warnings)
        )

    def test_formdata_skipped_with_grouped_warning(self):
        # 4 formData params on the fixture (2 uploadImage + 2 post /pet/{petId})
        self.assertTrue(any("formData" in w and "4" in w for w in self.warnings))
        up = self.endpoints[("POST", "/pet/{petId}/uploadImage")]
        names = [p.name for p in up.params]
        self.assertIn("petId", names)
        self.assertNotIn("additionalMetadata", names)
        self.assertNotIn("file", names)

    def test_upload_image_endpoint_shape(self):
        e = self.endpoints[("POST", "/pet/{petId}/uploadImage")]
        pet_id = [p for p in e.params if p.name == "petId"][0]
        self.assertEqual(pet_id.location, "path")
        self.assertTrue(pet_id.required)
        self.assertEqual(pet_id.description, "ID of pet to update")
        self.assertEqual(e.summary, "uploads an image")
        self.assertEqual(e.request_example, None)
        self.assertEqual(e.provenance.source, "swagger2-path-item")
        self.assertEqual(e.provenance.detail, "/paths//pet/{petId}/uploadImage/post")
        self.assertTrue(e.valid())

    def test_body_param_pet(self):
        e = self.endpoints[("POST", "/pet")]
        body = [p for p in e.params if p.location == "body"]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0].name, "body")
        self.assertEqual(body[0].type, "object")
        self.assertTrue(body[0].required)  # required at the swagger level
        self.assertIn("Pet object", body[0].description)

    def test_status_enum_query_parameter(self):
        e = self.endpoints[("GET", "/pet/findByStatus")]
        status = [p for p in e.params if p.name == "status"][0]
        self.assertEqual(status.location, "query")
        self.assertEqual(status.enum, ["available", "pending", "sold"])

    def test_find_pets_by_tags_query_params(self):
        e = self.endpoints[("GET", "/pet/findByTags")]
        self.assertEqual([p.name for p in e.params], ["tags"])

    def test_store_order_post_body_is_object(self):
        e = self.endpoints[("POST", "/store/order")]
        body = [p for p in e.params if p.name == "body"][0]
        self.assertEqual(body.location, "body")
        self.assertTrue(body.required)

    def test_delete_order_param(self):
        e = self.endpoints[("DELETE", "/store/order/{orderId}")]
        self.assertEqual([p.name for p in e.params], ["orderId"])
        self.assertTrue(e.is_destructive())

    def test_get_user_login_query_params(self):
        e = self.endpoints[("GET", "/user/login")]
        self.assertIn("username", [p.name for p in e.params])
        self.assertIn("password", [p.name for p in e.params])

    def test_provenance_every_endpoint(self):
        for e in self.digest.endpoints:
            self.assertEqual(e.provenance.source, "swagger2-path-item")
            self.assertEqual(e.provenance.detail, "/paths/{}/{}".format(e.path, e.method.lower()))

    def test_detection(self):
        det = self.digest.detection
        self.assertEqual(det["mode"], "swagger2")
        self.assertEqual(det["spec_url"], SOURCE_URL)
        self.assertEqual(det["confidence"], 0.95)

    def test_digest_warnings_stay_empty_for_caller_merge(self):
        self.assertEqual(self.digest.warnings, [])

    def test_generated_at_iso_utc(self):
        self.assertTrue(self.digest.generated_at.endswith("+00:00"))

    def test_info_title_fallback_api_name(self):
        digest, _ = digest_from_swagger2(_fixture(), SOURCE_URL, "")
        self.assertEqual(digest.api_name, "Swagger Petstore")


class TestSwagger2Edges(unittest.TestCase):
    @staticmethod
    def _spec(**over):
        spec = {
            "swagger": "2.0",
            "info": {"title": "Mini"},
            "host": "api.example.org",
            "basePath": "/v1",
            "schemes": ["https"],
            "paths": {
                "/things": {
                    "get": {"operationId": "listThings", "parameters": [
                        {"name": "limit", "in": "query", "type": "integer", "required": False}
                    ]}
                }
            },
        }
        spec.update(over)
        return spec

    def test_global_security_oauth_first_then_apikey(self):
        spec = self._spec(
            securityDefinitions={
                "oauth_foo": {"type": "oauth2", "flow": "implicit", "authorizationUrl": "https://x/y"},
                "key_one": {"type": "apiKey", "name": "X-Api-Key", "in": "header"},
            },
            security=[{"oauth_foo": ["read"]}, {"key_one": []}],
        )
        digest, warn = digest_from_swagger2(spec, "https://origin.c/s.json", "mini")
        self.assertEqual(digest.auth.type, "api_key_header")
        self.assertEqual(digest.auth.location, "X-Api-Key")  # the PARAM name travels
        self.assertTrue(any("oauth2" in w and "oauth_foo" in w for w in warn))
        self.assertTrue(digest.valid())

    def test_oauth2_only_becomes_none_with_warning(self):
        spec = self._spec(
            securityDefinitions={"only_oauth": {"type": "oauth2", "flow": "implicit"}},
            security=[{"only_oauth": []}],
        )
        digest, warn = digest_from_swagger2(spec, "https://origin.c/s.json", "mini")
        self.assertEqual(digest.auth.type, "none")
        self.assertTrue(any("oauth2" in w and "only_oauth" in w for w in warn))

    def test_basic_auth(self):
        spec = self._spec(
            securityDefinitions={"basic_auth": {"type": "basic"}},
            security=[{"basic_auth": []}],
        )
        digest, _ = digest_from_swagger2(spec, "https://origin.c/s.json", "mini")
        self.assertEqual(digest.auth.type, "basic")

    def test_api_key_query(self):
        spec = self._spec(
            securityDefinitions={"k": {"type": "apiKey", "name": "token", "in": "query"}},
            security=[{"k": []}],
        )
        digest, _ = digest_from_swagger2(spec, "https://origin.c/s.json", "mini")
        self.assertEqual((digest.auth.type, digest.auth.location), ("api_key_query", "token"))

    def test_explicit_empty_global_security_is_public(self):
        spec = self._spec(securityDefinitions={"k": {"type": "apiKey", "name": "t", "in": "header"}}, security=[])
        digest, warn = digest_from_swagger2(spec, "https://origin.c/s.json", "mini")
        self.assertEqual(digest.auth.type, "none")
        self.assertTrue(any("públic" in w for w in warn))

    def test_host_missing_falls_back_to_origin(self):
        spec = self._spec()
        del spec["host"]
        digest, warn = digest_from_swagger2(spec, "https://origin.example/x/s.json", "mini")
        self.assertEqual(digest.base_url, "https://origin.example/v1")
        self.assertTrue(any("sin 'host'" in w for w in warn))
        self.assertTrue(digest.valid())

    def test_host_with_variable_not_invented(self):
        spec = self._spec(host="{{tenant}}.example.org")
        digest, warn = digest_from_swagger2(spec, "https://origin.example/s.json", "mini")
        self.assertEqual(digest.base_url, "https://origin.example/v1")  # origin + basePath (spec data)
        self.assertTrue(any("no se inventan" in w for w in warn))
        self.assertNotIn("{{tenant}}", digest.to_json())

    def test_non_http_scheme_falls_back_on_scheme_only(self):
        spec = self._spec(schemes=["ws"])
        digest, warn = digest_from_swagger2(spec, "https://origin.example/s.json", "mini")
        self.assertEqual(digest.base_url, "https://api.example.org/v1")  # host kept
        self.assertTrue(any("no es http" in w for w in warn))

    def test_missing_paths_returns_none(self):
        digest, warn = digest_from_swagger2({"swagger": "2.0", "info": {}}, "https://o.c/x.json", "m")
        self.assertIsNone(digest)
        self.assertTrue(warn)

    def test_path_item_params_and_operation_override(self):
        spec = self._spec(paths={
            "/widgets/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "type": "string", "required": True},
                    {"name": "verbose", "in": "query", "type": "boolean", "description": "path-item level"},
                ],
                "get": {"operationId": "getOne", "parameters": [
                    {"name": "verbose", "in": "query", "type": "boolean", "description": "op level wins"}
                ]},
            }
        })
        digest, _ = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        e = digest.endpoints[0]
        self.assertEqual(e.method, "GET")
        by_name = {p.name: p for p in e.params}
        self.assertEqual(by_name["id"].location, "path")
        self.assertTrue(by_name["id"].required)
        self.assertEqual(by_name["verbose"].description, "op level wins")
        self.assertEqual(len(e.params), 2)  # merged, not duplicated

    def test_body_schema_ref_description_one_level(self):
        spec = self._spec(paths={
            "/pets": {
                "post": {"parameters": [
                    {"in": "body", "name": "body", "required": True, "schema": {"$ref": "#/definitions/Pet"}}
                ]}
            }
        }, definitions={"Pet": {"type": "object", "description": "Una mascota inventada para el test"}})
        digest, _ = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        body = [p for p in digest.endpoints[0].params if p.location == "body"][0]
        self.assertEqual(body.type, "object")
        self.assertEqual(body.description, "Una mascota inventada para el test")

    def test_parameter_level_ref_resolved(self):
        spec = self._spec(parameters={"Limit": {"name": "limit", "in": "query", "type": "integer"}}, paths={
            "/x": {"get": {"parameters": [{"$ref": "#/parameters/Limit"}]}}
        })
        digest, warn = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        self.assertEqual([p.name for p in digest.endpoints[0].params], ["limit"])
        self.assertEqual(digest.endpoints[0].params[0].type, "integer")

    def test_broken_ref_skipped_with_warning(self):
        spec = self._spec(paths={
            "/x": {"get": {"parameters": [{"$ref": "#/definitions/Nope"}]}}
        })
        digest, warn = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        self.assertEqual(digest.endpoints[0].params, [])
        self.assertTrue(any("no resoluble" in w for w in warn))

    def test_no_supported_scheme_at_all(self):
        spec = self._spec(securityDefinitions={
            "sig": {"type": "unknownKind"}
        }, security=[{"sig": []}])
        digest, warn = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        self.assertEqual(digest.auth.type, "none")
        self.assertTrue(any("no soportada" in w for w in warn))

    def test_non_object_paths_skipped_safely(self):
        spec = self._spec(paths={"/ok": {"get": {}}, "/bad": "not a dict"})
        digest, _ = digest_from_swagger2(spec, "https://o.c/x.json", "mini")
        self.assertEqual(len(digest.endpoints), 1)
        self.assertTrue(digest.valid())


if __name__ == "__main__":
    unittest.main()
