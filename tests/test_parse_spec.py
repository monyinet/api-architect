"""Lane L1 tests: OpenAPI 3 (JSON) -> Digest v1 compiler (parse_spec).

Stdlib-only, offline. Run from the repo root:
    python -m unittest tests.test_parse_spec -v

The real fixture (tests/fixtures/petstore.json) is compiled as-is; the other
fixtures (petstore_v2.json, postman_collection.json) belong to lanes L2/L3 and
are only used here through detect_spec(), never parsed by this lane.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parse_spec as ps  # noqa: E402
from digest import Digest  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "petstore.json"
SOURCE = "https://petstore3.swagger.io/api/v3/openapi.json"


def _fresh_fixture_digest():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return ps.digest_from_openapi3(data, SOURCE, "petstore")


# --------------------------------------------------------------------------- #
# synthetic specs (inline — nothing besides my own lane writes them)
# --------------------------------------------------------------------------- #
SPEC_NO_SERVERS = {
    "openapi": "3.0.0",
    "info": {"title": "Bare API"},
    "paths": {
        "/ping": {"get": {"description": "P" * 200}},
    },
}

_SPEC_VARS_TEMPLATE = {
    "openapi": "3.0.2",
    "info": {"title": "Vars API"},
    "paths": {"/v": {"get": {"summary": "V"}}},
}
SPEC_WITH_VARS = dict(
    _SPEC_VARS_TEMPLATE,
    servers=[
        {
            "url": "{scheme}://{env}.example.com/v2",
            "variables": {
                "scheme": {"enum": ["https", "http"], "default": "https"},
                "env": {"default": "prod"},
            },
        }
    ],
)
SPEC_WITH_SERVERVARS = dict(
    _SPEC_VARS_TEMPLATE,
    servers=[
        {
            "url": "https://{env}.example.com/v2",
            "serverVariables": {"env": {"default": "staging"}},
        }
    ],
)
SPEC_VAR_NO_DEFAULT = dict(
    _SPEC_VARS_TEMPLATE,
    servers=[{"url": "https://{env}.example.com", "variables": {"env": {"enum": ["x", "y"]}}}],
)

SPEC_AUTH_THINGS = {
    "openapi": "3.0.2",
    "info": {"title": "Things"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "security": [{"api_key": []}],
    "components": {
        "securitySchemes": {
            "api_key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
            "unused_bearer": {"type": "http", "scheme": "bearer"},
        },
        "parameters": {
            "Limit": {
                "name": "limit",
                "in": "query",
                "required": False,
                "description": "page size",
                "schema": {"type": "integer"},
            }
        },
    },
    "paths": {
        "/things/{thing_id}": {
            "parameters": [
                # path-item parameter: required omitted -> parser must force True
                {"name": "thing_id", "in": "path", "schema": {"type": "string"}},
                # cookie parameter: out of v0 scope -> skipped with grouped warning
                {"name": "session", "in": "cookie", "schema": {"type": "string"}},
                # duplicate of the operation-level 'kind' below -> operation wins
                {"name": "kind", "in": "query", "schema": {"type": "integer"}},
                {"name": "other", "in": "query", "schema": {"type": "string", "enum": ["only", "in", "path", "item"]}},
            ],
            "get": {
                "summary": "List things",
                "parameters": [
                    {"name": "kind", "in": "query", "required": True, "schema": {"type": "string", "enum": ["a", "b"]}},
                    {"name": "page", "in": "query", "schema": {"type": "integer"}},
                    {
                        "name": "legacy",
                        "in": "query",
                        "deprecated": True,
                        "description": "old thing",
                        "schema": {"type": "string"},
                    },
                    # enum too long (>12) -> enum must NOT be copied
                    {"name": "wide", "in": "query", "schema": {"type": "string", "enum": ["e{}".format(i) for i in range(13)]}},
                    {"$ref": "#/components/parameters/Limit"},
                ],
            },
        }
    },
}

SPEC_BODY = {
    "openapi": "3.0.2",
    "info": {"title": "Order API"},
    "servers": [{"url": "https://orders.example.com/v2"}],
    "paths": {
        "/orders": {
            "post": {
                "summary": "Create order",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "Order payload",
                                "properties": {"item": {"type": "string"}},
                            },
                            "example": {"item": "book"},
                        }
                    },
                },
            }
        },
        "/notes": {
            "post": {
                "summary": "Add note",
                "requestBody": {
                    "content": {
                        "application/json; charset=utf-8": {
                            "schema": {"type": "object"},
                            "examples": {"plain": {"summary": "plain", "value": {"note": "hi"}}},
                        }
                    }
                },
            }
        },
        "/big": {
            "post": {
                "summary": "Big",
                "requestBody": {
                    "content": {"application/json": {"schema": {"type": "object"}, "example": {"blob": "x" * 6000}}}
                },
            }
        },
        "/reports": {
            "get": {
                "summary": "Reports",
                "requestBody": {
                    "content": {"application/json": {"schema": {"type": "object"}, "example": {"a": 1}}}
                },
            }
        },
        "/pets": {
            "post": {
                "summary": "Ref body",
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}},
            }
        },
        "/nested": {
            "post": {
                "summary": "Nested ref body",
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NestedA"}}}},
            }
        },
    },
    "components": {
        "schemas": {
            "Pet": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            "NestedA": {"$ref": "#/components/schemas/NestedB"},  # ref chain: deeper than one level
            "NestedB": {"type": "object", "properties": {"b": {"type": "string"}}},
        }
    },
}

SPEC_OAUTH = {
    "openapi": "3.1.0",
    "info": {"title": "OAuth API"},
    "servers": [{"url": "https://o.example.com"}],
    "security": [{"petstore_auth": ["write:pets", "read:pets"]}],
    "components": {
        "securitySchemes": {
            "petstore_auth": {
                "type": "oauth2",
                "flows": {"implicit": {"authorizationUrl": "https://o.example.com/oauth", "scopes": {"write:pets": "w"}}},
            }
        }
    },
    "paths": {"/p": {"get": {"summary": "s"}}},
}

SPEC_CAP = {
    "openapi": "3.0.2",
    "info": {"title": "Big API"},
    "servers": [{"url": "https://big.example.com"}],
    "paths": {"/op-{}".format(i): {"get": {"summary": "s {}".format(i)}} for i in range(45)},
}

SPEC_DUP_IDS = {
    "openapi": "3.0.2",
    "info": {"title": "Dup API"},
    "servers": [{"url": "https://dup.example.com"}],
    "paths": {
        "/foo-bar": {"get": {"summary": "dash"}},
        "/foo/bar": {"get": {"summary": "slash"}},  # slugifies to the same id -> -2
    },
}


def _auth_scenario_spec(security, security_schemes):
    spec = {
        "openapi": "3.0.2",
        "info": {"title": "Auth Scenario"},
        "servers": [{"url": "https://auth.example.com"}],
        "paths": {"/p": {"get": {"summary": "s"}}},
    }
    if security is not None:
        spec["security"] = security
    if security_schemes is not None:
        spec["components"] = {"securitySchemes": security_schemes}
    return spec


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
class TestDetectSpec(unittest.TestCase):
    def test_openapi3(self):
        self.assertEqual(ps.detect_spec(json.loads(FIXTURE.read_text(encoding="utf-8"))), "openapi3")

    def test_swagger2_detected_but_not_parsed_here(self):
        self.assertEqual(ps.detect_spec({"swagger": "2.0", "info": {"title": "x"}}), "swagger2")

    def test_postman_by_postman_id(self):
        self.assertEqual(ps.detect_spec({"info": {"_postman_id": "9f2", "name": "c"}}), "postman")

    def test_postman_by_schema_url(self):
        spec = {"info": {"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"}}
        self.assertEqual(ps.detect_spec(spec), "postman")

    def test_unknown_documents(self):
        self.assertIsNone(ps.detect_spec({}))
        self.assertIsNone(ps.detect_spec(["not", "an", "object"]))
        self.assertIsNone(ps.detect_spec({"paths": {"/a": {}}}))
        self.assertIsNone(ps.detect_spec(None))

    def test_parse_skip_exists_as_exception(self):
        self.assertTrue(issubclass(ps.ParseSkip, Exception))
        self.assertIn("spec", str(ps.ParseSkip("the spec is not interpretable")).lower() or "spec")


# --------------------------------------------------------------------------- #
# the real fixture
# --------------------------------------------------------------------------- #
class TestPetstoreFixture(unittest.TestCase):
    def setUp(self):
        self.assertTrue(FIXTURE.exists(), "tests/fixtures/petstore.json missing")
        self.digest, self.warnings = _fresh_fixture_digest()

    def test_valid_digest_with_expected_shape(self):
        self.assertIsNotNone(self.digest, str(self.warnings))
        self.assertTrue(self.digest.valid())
        self.assertEqual(len(self.digest.endpoints), 19, "13 paths -> 19 method operations")
        self.assertEqual(self.digest.api_name, "petstore")  # passed name kept RAW
        self.assertEqual(self.digest.base_url, "https://petstore3.swagger.io/api/v3")
        self.assertEqual(self.digest.detection["mode"], "openapi3")
        self.assertEqual(self.digest.detection["confidence"], 0.95)
        self.assertEqual(self.digest.detection["spec_url"], SOURCE)
        self.assertTrue(self.digest.generated_at.startswith("20"), "UTC isoformat generated_at")

    def test_relative_server_url_resolved_with_warning(self):
        # The real fixture uses servers[0].url == '/api/v3' (relative).
        joined = [w for w in self.warnings if "is relative" in w and "petstore3" in w]
        self.assertTrue(joined, self.warnings)

    def test_no_global_security_means_none_plus_declared_warning(self):
        # Real fixture has NO top-level security; schemes api_key + petstore_auth exist.
        self.assertEqual(self.digest.auth.type, "none")
        subtle = [w for w in self.warnings if "security" in w and "api_key" in w and "petstore_auth" in w]
        self.assertTrue(subtle, self.warnings)

    def test_every_endpoint_has_provenance(self):
        for e in self.digest.endpoints:
            self.assertIsNotNone(e.provenance)
            self.assertEqual(e.provenance.source, "openapi-path-item")
            self.assertTrue(e.provenance.detail.startswith("/paths/"), e.provenance.detail)

    def test_provenance_detail_is_json_pointer_escaped(self):
        by_id = {e.id: e for e in self.digest.endpoints}
        self.assertIn("get-pet-findbystatus", by_id)
        self.assertEqual(by_id["get-pet-findbystatus"].provenance.detail, "/paths/~1pet~1findByStatus/get")
        self.assertEqual(by_id["get-pet-petid"].provenance.detail, "/paths/~1pet~1{petId}/get")

    def test_path_params_are_required_and_enums_survive(self):
        for e in self.digest.endpoints:
            for p in e.params:
                if p.location == "path":
                    self.assertTrue(p.required, "{} param {} must be required=True".format(e.id, p.name))
        by_id = {e.id: e for e in self.digest.endpoints}
        status = next(p for p in by_id["get-pet-findbystatus"].params if p.name == "status")
        self.assertEqual(status.enum, ["available", "pending", "sold"])
        self.assertTrue(status.required)
        self.assertEqual(status.location, "query")

    def test_body_params_only_for_json_object_schemas(self):
        by_id = {e.id: e for e in self.digest.endpoints}
        self.assertEqual(by_id["put-pet"].method, "PUT")
        body = [p for p in by_id["put-pet"].params if p.location == "body"]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0].name, "body")
        self.assertEqual(body[0].type, "object")
        self.assertTrue(body[0].required)  # requestBody.required true in the fixture
        # $ref to components/schemas/Pet resolved one level -> object body
        self.assertTrue([p for p in by_id["post-store-order"].params if p.location == "body"])
        # POST /user/createWithList sends an array schema -> no body param, still honest
        self.assertFalse([p for p in by_id["post-user-createwithlist"].params if p.location == "body"])
        # uploadImage requestBody is application/octet-stream -> no body param
        self.assertFalse([p for p in by_id["post-pet-petid-uploadimage"].params if p.location == "body"])
        # no JSON media example in the fixture requestBodies -> request_example stays None
        self.assertIsNone(by_id["put-pet"].request_example)

    def test_warnings_are_copies_into_the_digest(self):
        self.assertEqual(self.digest.warnings, self.warnings)

    def test_roundtrip_preserves_contract_fields(self):
        payload = self.digest.to_json(state="draft")
        again = Digest.from_json(payload)
        self.assertEqual(again.api_name, self.digest.api_name)
        self.assertEqual(again.base_url, self.digest.base_url)
        self.assertEqual(again.source_url, self.digest.source_url)
        self.assertEqual(again.auth.type, self.digest.auth.type)
        self.assertEqual(again.detection, self.digest.detection)
        self.assertEqual(again.generated_at, self.digest.generated_at)
        self.assertEqual(again.warnings, self.digest.warnings)
        self.assertEqual(len(again.endpoints), 19)
        self.assertEqual(again.endpoints[0].id, self.digest.endpoints[0].id)
        self.assertEqual(again.endpoints[0].provenance.source, self.digest.endpoints[0].provenance.source)
        self.assertEqual(again.endpoints[0].provenance.detail, self.digest.endpoints[0].provenance.detail)
        self.assertTrue(again.valid())


# --------------------------------------------------------------------------- #
# synthetic (a): no servers -> origin fallback + warning
# --------------------------------------------------------------------------- #
class TestNoServersFallback(unittest.TestCase):
    def test_origin_fallback_with_warning(self):
        digest, warnings = ps.digest_from_openapi3(SPEC_NO_SERVERS, "https://bare.example/openapi.json", "bare")
        self.assertIsNotNone(digest)
        self.assertTrue(digest.valid())
        self.assertEqual(digest.base_url, "https://bare.example")
        self.assertTrue(any("no servers" in w for w in warnings), warnings)
        # no declared schemes -> no subtle securityScheme warning
        self.assertFalse(any("securityScheme" in w for w in warnings), warnings)

    def test_summary_falls_back_to_clipped_description(self):
        digest2, _ = ps.digest_from_openapi3(SPEC_NO_SERVERS, "https://bare.example/openapi.json", "bare")
        ping = [e for e in digest2.endpoints if e.id == "get-ping"][0]
        self.assertEqual(ping.summary, "P" * 140)
        self.assertEqual(ping.method, "GET")


# --------------------------------------------------------------------------- #
# synthetic (b): server variables
# --------------------------------------------------------------------------- #
class TestServerVariables(unittest.TestCase):
    def test_variables_defaults_substituted(self):
        digest, warnings = ps.digest_from_openapi3(SPEC_WITH_VARS, "https://docs.example/vars.json", "vars")
        self.assertEqual(digest.base_url, "https://prod.example.com/v2")
        self.assertFalse([w for w in warnings if "placeholder" in w], warnings)

    def test_servervariables_contract_spelling_also_supported(self):
        digest, _ = ps.digest_from_openapi3(SPEC_WITH_SERVERVARS, "https://docs.example/vars.json", "vars")
        self.assertEqual(digest.base_url, "https://staging.example.com/v2")

    def test_missing_default_keeps_placeholder_and_warns(self):
        digest, warnings = ps.digest_from_openapi3(SPEC_VAR_NO_DEFAULT, "https://docs.example/vars.json", "vars")
        self.assertIn("{env}", digest.base_url)  # never invent a host
        self.assertTrue(any("no default" in w for w in warnings), warnings)


# --------------------------------------------------------------------------- #
# synthetic (c): api_key header auth, enums, required, refs, cookie skip
# --------------------------------------------------------------------------- #
class TestApiParamsAndAuth(unittest.TestCase):
    def setUp(self):
        self.digest, self.warnings = ps.digest_from_openapi3(SPEC_AUTH_THINGS, "https://docs.example/things.json", "things")

    def test_api_key_header_auth(self):
        self.assertEqual(self.digest.auth.type, "api_key_header")
        self.assertEqual(self.digest.auth.location, "X-Api-Key")

    def test_operation_params_first_then_inherited_path_item_ones(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        # operation params in document order, then inherited path-item params
        # ('session' dropped as cookie, path-item 'kind' dropped as override,
        #  operation-level enum-carrying params keep their order)
        self.assertEqual(
            [p.name for p in endpoint.params],
            ["kind", "page", "legacy", "wide", "limit", "thing_id", "other"],
        )

    def test_operation_overrides_path_item_parameter(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        kinds = [p for p in endpoint.params if p.name == "kind"]
        self.assertEqual(len(kinds), 1)
        self.assertEqual(kinds[0].type, "string")  # the operation definition wins

    def test_path_param_required_forced_true(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        thing_id = next(p for p in endpoint.params if p.name == "thing_id")
        self.assertEqual(thing_id.location, "path")
        self.assertTrue(thing_id.required)

    def test_enum_copied_when_small(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        kind = next(p for p in endpoint.params if p.name == "kind")
        self.assertEqual(kind.enum, ["a", "b"])
        wide = next(p for p in endpoint.params if p.name == "wide")
        self.assertIsNone(wide.enum, "enum with 13 items must not be copied")

    def test_deprecated_kept_with_prefix(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        legacy = next(p for p in endpoint.params if p.name == "legacy")
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy.description, "[deprecated] old thing")

    def test_cookie_param_skipped_with_grouped_warning(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        self.assertNotIn("session", [p.name for p in endpoint.params])
        cookie_warnings = [w for w in self.warnings if "cookie" in w.lower()]
        self.assertEqual(len(cookie_warnings), 1, self.warnings)  # grouped: one warning for N skips
        self.assertIn("1 cookie parameter", cookie_warnings[0])

    def test_components_parameters_ref_resolved_one_level(self):
        endpoint = [e for e in self.digest.endpoints if e.path == "/things/{thing_id}"][0]
        limit = next(p for p in endpoint.params if p.name == "limit")
        self.assertEqual(limit.location, "query")
        self.assertEqual(limit.type, "integer")
        self.assertFalse(limit.required)

    def test_unreferenced_schemes_do_not_warn_here(self):
        # 'unused_bearer' is declared but 'security' references api_key: the
        # subtle "declared but unreferenced" warning only belongs to the
        # empty-security case, not this one.
        self.assertFalse(any("unreferenced" in w for w in self.warnings), self.warnings)


# --------------------------------------------------------------------------- #
# synthetic (d): request bodies
# --------------------------------------------------------------------------- #
class TestRequestBody(unittest.TestCase):
    def setUp(self):
        self.digest, self.warnings = ps.digest_from_openapi3(SPEC_BODY, "https://docs.example/body.json", "body")
        self.by_id = {e.id: e for e in self.digest.endpoints}

    def _body_param(self, endpoint_id):
        endpoint = self.by_id[endpoint_id]
        bodies = [p for p in endpoint.params if p.location == "body"]
        self.assertEqual(len(bodies), 1, "{}".format(endpoint_id))
        return bodies[0]

    def test_inline_object_body_with_example(self):
        body = self._body_param("post-orders")
        self.assertEqual(body.name, "body")
        self.assertEqual(body.type, "object")
        self.assertTrue(body.required)  # requestBody.required: true
        self.assertEqual(body.description, "Order payload")
        self.assertEqual(self.by_id["post-orders"].request_example, {"item": "book"})

    def test_examples_map_first_value_with_charset_media(self):
        body = self._body_param("post-notes")
        self.assertFalse(body.required)  # default False
        self.assertEqual(self.by_id["post-notes"].request_example, {"note": "hi"})

    def test_oversized_example_is_dropped(self):
        body = self._body_param("post-big")  # serializes to > 4 KB
        self.assertEqual(body.type, "object")
        self.assertIsNone(self.by_id["post-big"].request_example)

    def test_get_ignores_request_body(self):
        reports = self.by_id["get-reports"]
        self.assertFalse([p for p in reports.params if p.location == "body"])
        self.assertIsNone(reports.request_example)

    def test_ref_body_resolved_one_level(self):
        body = self._body_param("post-pets")
        self.assertEqual(body.type, "object")
        self.assertEqual(body.description, "")  # no description anywhere -> empty, not invented

    def test_nested_ref_chain_not_followed_with_warning(self):
        self.assertFalse([p for p in self.by_id["post-nested"].params if p.location == "body"])
        nested = [w for w in self.warnings if "NestedA" in w]
        self.assertTrue(nested, self.warnings)

    def test_valid_and_roundtrip_keeps_request_example(self):
        self.assertTrue(self.digest.valid())
        again = Digest.from_json(self.digest.to_json())
        self.assertEqual(again.endpoints[0].request_example, {"item": "book"})


# --------------------------------------------------------------------------- #
# synthetic (e): oauth and friends -> auth none with explicit warning
# --------------------------------------------------------------------------- #
AUTH_CASES = [
    ({}, {}, "none"),
    ([{"bearer_key": []}], {"bearer_key": {"type": "http", "scheme": "bearer"}}, "bearer"),
    ([{"basic_key": []}], {"basic_key": {"type": "http", "scheme": "basic"}}, "basic"),
    ([{"q": []}], {"q": {"type": "apiKey", "in": "query", "name": "key"}}, "api_key_query"),
    ([{"h": []}], {"h": {"type": "apiKey", "in": "header", "name": "X-Key"}}, "api_key_header"),
    ([{"petstore_auth": []}], {"petstore_auth": {"type": "oauth2", "flows": {}}}, "none"),
    ([{"oidc": []}], {"oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://x/.well-known"}}, "none"),
    ([{"c": []}], {"c": {"type": "apiKey", "in": "cookie", "name": "sid"}}, "none"),
    ([{"d": []}], {"d": {"type": "http", "scheme": "digest"}}, "none"),
    # first referenced requirement wins even when another scheme is declared
    ([{"bearer_key": []}, {"other": []}],
     {"bearer_key": {"type": "http", "scheme": "bearer"}, "other": {"type": "apiKey", "in": "header", "name": "K"}},
     "bearer"),
]


class TestAuthResolution(unittest.TestCase):
    def test_auth_matrix(self):
        for security, schemes, expected in AUTH_CASES:
            with self.subTest(expected=expected):
                spec = _auth_scenario_spec(security, schemes)
                digest, _warnings = ps.digest_from_openapi3(spec, "https://auth.example/spec.json", "auth")
                self.assertIsNotNone(digest)
                self.assertEqual(digest.auth.type, expected)

    def test_oauth2_downgrades_to_none_with_explicit_warning(self):
        digest, warnings = ps.digest_from_openapi3(SPEC_OAUTH, "https://o.example/spec.json", "oauth")
        self.assertEqual(digest.auth.type, "none")
        oauth_warnings = [w for w in warnings if "oauth" in w.lower()]
        self.assertTrue(oauth_warnings, warnings)
        self.assertTrue(digest.valid())  # the warning does NOT fail the digestion

    def test_security_referencing_undeclared_scheme_warns(self):
        spec = _auth_scenario_spec([{"ghost": []}], {})
        digest, warnings = ps.digest_from_openapi3(spec, "https://auth.example/spec.json", "auth")
        self.assertEqual(digest.auth.type, "none")
        self.assertTrue(any("ghost" in w for w in warnings), warnings)


# --------------------------------------------------------------------------- #
# caps, dedup, honesty guards
# --------------------------------------------------------------------------- #
class TestCapsAndGuards(unittest.TestCase):
    def test_endpoint_cap_at_40_with_count_warning(self):
        digest, warnings = ps.digest_from_openapi3(SPEC_CAP, "https://big.example/spec.json", "big")
        self.assertEqual(len(digest.endpoints), 40)
        self.assertTrue(digest.valid())
        self.assertTrue(all(e.provenance and e.provenance.detail for e in digest.endpoints))
        cap_warnings = [w for w in warnings if "cap" in w]
        self.assertTrue(cap_warnings, warnings)
        self.assertIn("45", cap_warnings[0])  # the honest total of operations

    def test_duplicate_ids_get_dash_two(self):
        digest, _ = ps.digest_from_openapi3(SPEC_DUP_IDS, "https://dup.example/spec.json", "dup")
        ids = [e.id for e in digest.endpoints]
        self.assertEqual(ids, ["get-foo-bar", "get-foo-bar-2"])

    def test_non_dict_document_returns_none_never_raises(self):
        for bad in ([], "openapi.json", None, 3):
            digest, warnings = ps.digest_from_openapi3(bad, "https://x.example/spec.json", "x")
            self.assertIsNone(digest)
            self.assertTrue(warnings)
            self.assertIn("not a JSON object", warnings[0])

    def test_non_openapi3_dict_returns_none(self):
        digest, warnings = ps.digest_from_openapi3(
            {"swagger": "2.0", "info": {"title": "legacy"}}, "https://x.example/swagger.json", "x"
        )
        self.assertIsNone(digest)
        self.assertTrue(any("not an OpenAPI 3" in w for w in warnings), warnings)

    def test_invalid_digest_is_reported_not_patched(self):
        spec = {"openapi": "3.0.0", "paths": {"/p": {"get": {"summary": "s"}}}}  # no title, no api_name
        digest, warnings = ps.digest_from_openapi3(spec, "https://x.example/spec.json", "")
        self.assertIsNone(digest)
        self.assertTrue(any(w.startswith("digest.invalid") for w in warnings), warnings)

    def test_unresolvable_source_without_origin_cannot_build_base_url(self):
        spec = {"openapi": "3.0.0", "info": {"title": "T"}, "paths": {"/p": {"get": {"summary": "s"}}}}
        digest, warnings = ps.digest_from_openapi3(spec, "relative-file.json", "t")  # no http(s) origin
        self.assertIsNone(digest, "an unresolvable base_url must not be silently faked")
        self.assertTrue(any("base_url" in w for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
