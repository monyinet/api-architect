"""Stdlib-only tests for the Digest v1 contract. Run: python -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import Auth, Digest, Endpoint, Param, Provenance, slugify  # noqa: E402


def _endpoint(**kw):
    kwargs = dict(
        id="get-user",
        method="GET",
        path="/user/{id}",
        summary="Fetch a user",
        params=[Param(name="id", location="path", type="string", required=True)],
        provenance=Provenance(source="openapi-path-item", detail="/paths/~1user~1?id"),
    )
    kwargs.update(kw)
    return Endpoint(**kwargs)


class TestSlugify(unittest.TestCase):
    def test_ascii_and_no_traversal(self):
        # Vector V4: manifest filenames can never escape the data dir.
        for evil in ["../../.hermes/config", "..\\..\\Users", "a/b?c*d", "..%2F.."]:
            s = slugify(evil)
            self.assertRegex(s, r"^[a-z0-9-]+$", evil)

    def test_unicode_folds_to_ascii(self):
        self.assertEqual(slugify("API pública ñ"), "api-publica-n")

    def test_empty_falls_back(self):
        self.assertEqual(slugify("   "), "api")


class TestProvenanceGate(unittest.TestCase):
    def test_endpoint_without_provenance_is_invalid(self):
        e = _endpoint(provenance=None)
        self.assertFalse(e.valid())

    def test_endpoint_with_source_needs_something(self):
        e = _endpoint(provenance=Provenance(source="", detail="x"))
        self.assertFalse(e.valid())

    def test_valid_endpoint_passes(self):
        self.assertTrue(_endpoint().valid())


class TestDestructiveByMethod(unittest.TestCase):
    def test_non_get_is_destructive_even_if_spec_claims_safe(self):
        # The spec can lie (x-safe: true) — the interpreter decides by METHOD.
        e = _endpoint(method="POST")
        self.assertTrue(e.is_destructive())
        self.assertFalse(_endpoint(method="GET").is_destructive())


class TestDigestRoundtrip(unittest.TestCase):
    def test_to_json_from_json_preserves_contract(self):
        d = Digest(
            api_name="petstore",
            source_url="https://petstore3.swagger.io/api/v3/openapi.json",
            base_url="https://petstore3.swagger.io/api/v3",
            auth=Auth(type="api_key_header", location="X-Api-Key", secret_binding="api_key_1"),
            detection={"mode": "openapi-v3", "spec_url": "https://petstore3.swagger.io/api/v3/openapi.json", "confidence": 0.99},
            endpoints=[_endpoint(), _endpoint(method="POST", id="add-pet", path="/pet")],
            generated_at="2026-11-05T01:00:00+00:00",
            warnings=[],
        )
        payload = d.to_json(state="active")
        self.assertIn('"state": "active"', payload)
        d2 = Digest.from_json(payload)
        self.assertEqual(d2.api_name, d.api_name)
        self.assertEqual(d2.auth.secret_binding, "api_key_1")
        self.assertTrue(d2.valid())
        self.assertEqual(len(d2.endpoints), 2)

    def test_auth_validator(self):
        self.assertFalse(Auth(type="nonsense").valid())
        self.assertFalse(Auth(type="api_key_header", location="").valid())
        self.assertTrue(Auth(type="bearer").valid())


class TestFixtureSpec(unittest.TestCase):
    def test_petstore_fixture_is_parseable_json(self):
        p = Path(__file__).parent / "fixtures" / "petstore.json"
        self.assertTrue(p.exists(), "tests/fixtures/petstore.json missing — fetch it (README: fixtures)")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("openapi", data)
        self.assertIn("paths", data)


if __name__ == "__main__":
    unittest.main()
