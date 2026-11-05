"""Tests for redactor.py (values + default patterns + url + messages + HTTP errors).

Run: python -m unittest discover tests
"""
import http.client
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redactor import (  # noqa: E402
    REDACTED,
    Redactor,
    redact,
    redact_message,
    redact_url,
    sanitize_http_error,
)

VALUE = "sk-test-0123456789abcdefghij"  # 28 chars; NOT matched by sk-\w{20,} (has '-')
URL_16 = "https://api.x.com/v1/data?appid=ABCDEFGHIJKLMNOPQR&foo=bar"
APPID_16 = "ABCDEFGHIJKLMNOPQR"  # exactly 16 chars -> pattern floor


class TestValueRedaction(unittest.TestCase):
    def test_dummy_value_disappears_from_output(self):
        out = redact(f"error at host: token {VALUE} and more", values=[VALUE])
        self.assertNotIn(VALUE, out)
        self.assertIn(REDACTED, out)

    def test_value_inside_url_body_is_gone_but_foo_survives(self):
        out = redact(f"https://api.x.com/v1/data?foo={VALUE}&foo=bar", values=[VALUE])
        self.assertNotIn(VALUE, out)
        self.assertIn("foo=bar", out)

    def test_urlencoded_form_of_value_is_redacted(self):
        value_with_slash = "ABC/DEF+GHI"
        enc = "ABC%2FDEF%2BGHI"
        out = redact(f"q={enc}&z=1", values=[value_with_slash])
        self.assertNotIn(enc, out)
        self.assertIn(REDACTED, out)
        self.assertIn("z=1", out)

    def test_empty_and_none_values_are_dropped_not_crash(self):
        out = redact("plain text ok", values=[None, "", VALUE])
        self.assertEqual(out, "plain text ok")

    def test_case_sensitive_values_lowercased_value_survives(self):
        out = redact("abc and ABC", values=["ABC"])
        self.assertEqual(out, f"abc and {REDACTED}")

    def test_regex_metacharacters_in_value_are_literal(self):
        v = "a.b*c(d)"
        out = redact(f"v={v}", values=[v])
        self.assertNotIn(v, out)
        self.assertIn(REDACTED, out)


class TestDefaultPatterns(unittest.TestCase):
    def test_authorization_bearer_header_pattern(self):
        self.assertEqual(
            redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload"),
            "authorization: [REDACTED]",
        )
        self.assertEqual(redact("AUTHORIZATION:Bearer xyz-abc"), "authorization: [REDACTED]")

    def test_authorization_colon_variant_and_leading_text(self):
        out = redact("headers: Authorization: Bearer t0k3n; next headers")
        self.assertIn("authorization: [REDACTED]", out)
        self.assertNotIn("t0k3n", out)
        self.assertIn("next headers", out)

    def test_x_api_key_header_pattern(self):
        out = redact("X-API-KEY: supersecretvalue1")
        self.assertEqual(out, "x-api-key: [REDACTED]")

    def test_16_chars_appid_is_redacted_15_survives(self):
        self.assertIn(f"?appid={REDACTED}", redact("?appid=ABCDEFGHIJKLMNOP"))
        self.assertIn("appid=ABCDEFGHIJKLMNO", redact("?appid=ABCDEFGHIJKLMNO"))  # 15 chars: below floor

    def test_short_8_char_appid_is_not_pattern_redacted(self):
        # Declared limit from the docstring: free-text patterns need 16+ chars.
        out = redact("?appid=ABC12345&foo=bar")
        self.assertIn("appid=ABC12345", out)
        self.assertNotIn(REDACTED, out)

    def test_api_key_access_token_token_secret_query_patterns(self):
        sixteen = "ABCDEFGHIJKLMNOPQRSTUVWXYZA"[:16]
        self.assertIn(f"&api_key={REDACTED}", redact(f"&api_key={sixteen}&x=1"))
        self.assertIn(f"&apikey={REDACTED}", redact(f"&apikey={sixteen}&x=1"))
        self.assertIn(f"&access_token={REDACTED}", redact(f"&access_token={sixteen}&x=1"))
        self.assertIn(f"&token={REDACTED}", redact(f"&token={sixteen}&x=1"))
        self.assertIn(f"&secret={REDACTED}", redact(f"&secret={sixteen}&x=1"))
        # neighbouring params untouched
        self.assertIn("&x=1", redact(f"&token={sixteen}&x=1"))

    def test_openai_style_key_pattern(self):
        out = redact("key=sk-abcdefghijklmnopqrstuvwxyz")  # 26 chars after sk-
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", out)
        self.assertIn(REDACTED, out)

    def test_github_style_key_patterns(self):
        out = redact("ghp_" + "a" * 36 + " github_pat_" + "b" * 36)
        self.assertNotIn("ghp_", out)
        self.assertNotIn("github_pat_", out)

    def test_short_sk_token_survives_pattern(self):
        # 'sk-test...' has only 4 \w before '-' -> below {20,} floor survives.
        self.assertIn("sk-test-0123456789abcdefghij", redact("sk-test-0123456789abcdefghij"))


class TestRedactorClass(unittest.TestCase):
    def test_init_filters_empty_keeps_reusable(self):
        r = Redactor([None, "", VALUE])
        self.assertEqual(r.redact(f"v {VALUE} v"), f"v {REDACTED} v")
        # reusable instance
        self.assertEqual(r.redact("clean text"), "clean text")

    def test_no_values_still_applies_patterns(self):
        self.assertEqual(Redactor([]).redact("Authorization: Bearer xx"), "authorization: [REDACTED]")


class TestRedactUrl(unittest.TestCase):
    def test_appid_16_redacted_foo_survives_order_kept(self):
        out = redact_url(URL_16)
        self.assertNotIn(APPID_16, out)
        self.assertIn("appid=" + REDACTED, out)
        self.assertIn("foo=bar", out)
        self.assertIn("/v1/data?", out)
        self.assertTrue(out.startswith("https://api.x.com/v1/data"))

    def test_userinfo_redacted_rest_unchanged(self):
        out = redact_url("https://user:pass@api.x.com/v1/pets")
        self.assertEqual(out, f"https://{REDACTED}@api.x.com/v1/pets")
        self.assertEqual(redact_url("https://api.x.com/v1/pets"), "https://api.x.com/v1/pets")

    def test_suspect_names_any_length_value_redacted(self):
        out = redact_url("https://h/p?a=1&token=AB&b=2")
        self.assertEqual(out, f"https://h/p?a=1&token={REDACTED}&b=2")
        out = redact_url("https://h/p?x=1&password=shorty&y=2")
        self.assertIn(f"password={REDACTED}", out)
        self.assertIn("x=1", out)
        self.assertIn("&y=2", out)

    def test_non_suspect_params_and_blank_values_untouched(self):
        out = redact_url("https://h/p?fields=key&appid=&a=1")
        self.assertEqual(out, "https://h/p?fields=key&appid=&a=1")

    def test_case_insensitive_names(self):
        self.assertIn(f"AppID={REDACTED}", redact_url("https://h/p?AppID=abcdefghijklmnop0"))  # 16+ chars
        self.assertIn(f"TOKEN={REDACTED}", redact_url("https://h/p?TOKEN=abc"))

    def test_fragment_pairs_redacted_too(self):
        out = redact_url("https://h/p#greeting=hi&access_token=abcdefghijklmnop")
        self.assertIn(f"access_token={REDACTED}", out)
        self.assertIn("greeting=hi", out)

    def test_microsoft_prefixed_params(self):
        out = redact_url("https://h/p?microsoft_api_key=abcdefghijklmnop")
        self.assertIn(f"microsoft_api_key={REDACTED}", out)

    def test_dup_param_each_redacted(self):
        out = redact_url("https://h/p?appid=ABCDEFGHIJKLMNOP&a=1")
        self.assertIn(f"appid={REDACTED}", out)
        self.assertIn("&a=1", out)


class TestRedactMessage(unittest.TestCase):
    def test_message_url_and_values(self):
        payload = f'GET {URL_16} failed with headers Authorization: Bearer {VALUE}'
        out = redact_message(payload, values=[VALUE])
        self.assertNotIn(APPID_16, out)
        self.assertIn("foo=bar", out)
        self.assertNotIn(VALUE, out)
        self.assertIn("authorization: [REDACTED]", out)

    def test_plain_text_passthrough(self):
        self.assertEqual(redact_message("nothing here"), "nothing here")

    def test_idempotent(self):
        once = redact_message(URL_16, values=[VALUE])
        self.assertEqual(once, redact_message(once, values=[VALUE]))


class TestSanitizeHttpError(unittest.TestCase):
    def test_httpexception_message_fully_scrubbed(self):
        exc = http.client.HTTPException(f"bad response for {URL_16} with Authorization: Bearer {VALUE}")
        out = sanitize_http_error(exc, values=[VALUE])
        self.assertNotIn(APPID_16, out)
        self.assertNotIn(VALUE, out)
        self.assertNotIn("Bearer", out)
        self.assertIn(REDACTED, out)

    def test_httperror_repr_scrubbed(self):
        # services embed detail in the reason line; HTTPError str() keeps it
        exc = urllib.error.HTTPError(URL_16, 401, f"Unauthorized for {URL_16}", None, None)
        self.addCleanup(exc.close)  # avoid ResourceWarning from implicit GC cleanup
        out = sanitize_http_error(exc, values=[])
        self.assertNotIn(APPID_16, out)
        self.assertIn(REDACTED, out)

    def test_never_raises_for_weird_exception(self):
        class Broken(Exception):
            def __str__(self):
                raise RuntimeError("str() itself fails")

        out = sanitize_http_error(Broken(), values=[])
        self.assertIn("Broken", out)


if __name__ == "__main__":
    unittest.main()
