"""Stdlib-only tests for netguard.py — check_url contract + guarded_fetch (L3).

Real sockets are used ONLY against a local http.server bound to 127.0.0.1
(loopback, the one place loopback connections are permitted in tests). The
SSL/TLS code path is exercised for CONSTRUCTION only — no external network,
no real TLS handshake in the suite.

Run: python -m unittest tests.test_netguard -v
"""
import http.server
import ssl
import sys
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import netguard  # noqa: E402
from netguard import (  # noqa: E402
    GuardError,
    _PinnedHTTPSConnection,
    _sni_hostname,
    check_url,
    guarded_fetch,
    ip_is_denied,
)

# --- local loopback server -----------------------------------------------------

_ONE_MB = 1024 * 1024

_LAST = {"method": "", "path": "", "host": "", "user_agent": "", "accept_encoding": ""}
_HITS = {}
_LOCK = threading.Lock()


def _record_hit(path, method, request_headers):
    with _LOCK:
        _LAST["method"] = method
        _LAST["path"] = path
        _LAST["host"] = request_headers.get("Host", "")
        _LAST["user_agent"] = request_headers.get("User-Agent", "")
        _LAST["accept_encoding"] = request_headers.get("Accept-Encoding", "")
        _HITS[path] = _HITS.get(path, 0) + 1


class _EchoHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep test output clean; never echo URLs
        pass

    def _send(self, status, body, ctype="text/plain", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve(self):
        path = self.path.split("?", 1)[0]
        _record_hit(path, self.command, self.headers)
        if path == "/big":
            self._send(200, b"x" * _ONE_MB)
            return
        if path == "/post-echo":
            length = int(self.headers.get("Content-Length") or 0)
            echo = self.rfile.read(length) if length else b""
            self._send(200, echo, ctype="application/octet-stream")
            return
        if path == "/hop-1":
            self._send(302, b"", extra={"Location": "/hop-2"})
            return
        if path == "/hop-2":
            self._send(302, b"", extra={"Location": "/final"})
            return
        if path == "/chain-1":
            self._send(302, b"", extra={"Location": "/chain-2"})
            return
        if path == "/chain-2":
            self._send(302, b"", extra={"Location": "/chain-3"})
            return
        if path == "/chain-3":
            self._send(302, b"", extra={"Location": "/chain-4"})
            return
        if path == "/redir-rebind":
            self._send(302, b"", extra={"Location": "http://rebind-target.example/final"})
            return
        if path == "/redir-localhost":
            self._send(302, b"", extra={"Location": "http://localhost:{}/final".format(_HITS_PORT)})
            return
        if path == "/redir-abs":
            self._send(302, b"", extra={"Location": "http://127.0.0.1:{}/final".format(_HITS_PORT)})
            return
        # everything else: echo path + the Host header the client actually sent
        body = "path={}|host={}".format(self.path, self.headers.get("Host", "")).encode("utf-8")
        self._send(200, body)

    do_GET = do_POST = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _serve


_HITS_PORT = 0  # filled in by setUpClass (the server's bound port)


class _QuietLoopbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # cap test abruptly closes the socket mid-read — expected, stay silent


class _LocalFetchCase(unittest.TestCase):
    """Case with the loopback server up; guarded_fetch talks to http://127.0.0.1:port."""

    port: int
    base: str

    @classmethod
    def setUpClass(cls):
        cls._server = _QuietLoopbackServer(("127.0.0.1", 0), _EchoHandler)
        cls.port = cls._server.server_address[1]
        cls.base = "http://127.0.0.1:{}".format(cls.port)
        global _HITS_PORT
        _HITS_PORT = cls.port
        _HITS.clear()
        _LAST.update(method="", path="", host="", user_agent="", accept_encoding="")
        cls._thread = threading.Thread(
            target=cls._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._thread.join(timeout=5)

    def assertHit(self, path, count):
        with _LOCK:
            self.assertEqual(_HITS.get(path, 0), count)


# test doubles: the pin path must be drivable offline against 127.0.0.1 by faking
# the ONE resolution (resolve_host) and letting loopback through the deny-list for
# the duration of the fetch — the deny-list itself is tested untouched below.
_REAL_DENIED = netguard.ip_is_denied


def _allow_loopback(addr, allow_private=False):
    return False if addr == "127.0.0.1" else _REAL_DENIED(addr, allow_private)


@contextmanager
def _pin_loopback(resolver=None):
    def _resolve(host):
        if resolver is not None:
            return resolver(host)
        return ["127.0.0.1"]

    with patch.object(netguard, "resolve_host", _resolve), \
         patch.object(netguard, "ip_is_denied", _allow_loopback):
        yield


# --- check_url (frozen contract) ------------------------------------------------


class TestCheckUrlContract(unittest.TestCase):
    def test_scheme_must_be_http_or_https(self):
        for bad in ("ftp://example.com/x", "file:///etc/passwd", "//example.com/x", "example.com"):
            with self.subTest(url=bad), self.assertRaises(GuardError) as ctx:
                check_url(bad)
            self.assertIn("http/https", str(ctx.exception))

    def test_embedded_credentials_refused(self):
        for bad in ("https://user:pass@example.com/x", "http://token@example.com/"):
            with self.subTest(url=bad), self.assertRaises(GuardError) as ctx:
                check_url(bad)
            self.assertIn("credentials", str(ctx.exception))

    def test_hostname_level_private_names_refused(self):
        for bad in ("https://localhost/x", "http://api.internal/v1", "http://srv.home.arpa/"):
            with self.subTest(url=bad), self.assertRaises(GuardError) as ctx:
                check_url(bad)
            self.assertIn("private-network", str(ctx.exception))

    def test_no_hostname_refused(self):
        with self.assertRaises(GuardError):
            check_url("http:///x")

    def test_resolves_to_private_refused(self):
        # evil.example.com is a public-looking name; the (faked) resolution lands
        # in a deny range — exactly the rebinding shape the guard must refuse.
        with patch.object(netguard, "resolve_host", lambda host: ["127.0.0.1"]):
            with self.assertRaises(GuardError) as ctx:
                check_url("https://evil.example.com/")
        self.assertIn("private", str(ctx.exception))

    def test_resolves_to_public_passes_with_ips(self):
        with patch.object(netguard, "resolve_host", lambda host: ["93.184.216.34"]):
            p, ips = check_url("https://evil.example.com/")
        self.assertEqual(ips, ["93.184.216.34"])
        self.assertEqual(p.hostname, "evil.example.com")
        self.assertIsNone(p.username)

    def test_multiple_public_addresses_pass(self):
        with patch.object(netguard, "resolve_host", lambda host: ["8.8.8.8", "1.1.1.1"]):
            p, ips = check_url("https://dns.example.com/")
        # pass-through order stays the resolver's; both addresses must survive
        self.assertEqual(set(ips), {"1.1.1.1", "8.8.8.8"})
        self.assertFalse(any(ip_is_denied(ip) for ip in ips))


# --- ip_is_denied matrix ---------------------------------------------------------


class TestIpIsDenied(unittest.TestCase):
    def test_denied_ranges(self):
        denied = [
            "127.0.0.1", "10.0.0.5", "172.16.0.9", "192.168.1.1", "169.254.169.254",
            "224.0.0.1", "fc00::1", "fe80::1", "100.64.0.5",
        ]
        for addr in denied:
            with self.subTest(addr=addr):
                self.assertTrue(ip_is_denied(addr))

    def test_allowed_public_addresses(self):
        allowed = ["8.8.8.8", "1.1.1.1", "2606:4700::1111"]
        for addr in allowed:
            with self.subTest(addr=addr):
                self.assertFalse(ip_is_denied(addr))

    def test_unparseable_is_denied(self):
        self.assertTrue(ip_is_denied("999.1.2.3"))
        self.assertTrue(ip_is_denied("not-an-ip"))

    def test_allow_private_opt_in_cleared_the_flag(self):
        self.assertFalse(ip_is_denied("127.0.0.1", allow_private=True))
        self.assertFalse(ip_is_denied("10.0.0.5", allow_private=True))


# --- guarded_fetch against the loopback server -----------------------------------


class TestGuardedFetchBasics(_LocalFetchCase):
    def test_get_by_pin_sends_correct_host_header(self):
        # allow_private=False + fake resolution → the FULL pin path is exercised:
        # check_url validates → socket opens to the validated IP → Host stays 127.0.0.1:port.
        with _pin_loopback():
            res = guarded_fetch(self.base + "/echo", timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertIn("host=127.0.0.1:{}".format(self.port).encode(), res["body"])
        self.assertIn(b"path=/echo", res["body"])
        self.assertEqual(_LAST["method"], "GET")
        self.assertEqual(_LAST["host"], "127.0.0.1:{}".format(self.port))
        self.assertEqual(res["url"], self.base + "/echo")
        # digest-readable subset survived
        self.assertEqual(res["headers"]["content-type"], "text/plain")
        self.assertIn("date", res["headers"])
        self.assertIn("server", res["headers"])
        self.assertTrue(set(res["headers"]) <= set(netguard._HEADERS_KEPT))

    def test_post_body_roundtrip(self):
        payload = b'{"x": 1}'
        res = guarded_fetch(self.base + "/post-echo", method="POST", body=payload,
                            allow_private=True, timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertEqual(res["body"], payload)
        self.assertEqual(res["headers"]["content-type"], "application/octet-stream")
        self.assertEqual(_LAST["method"], "POST")

    def test_head_returns_empty_body(self):
        res = guarded_fetch(self.base + "/echo", method="HEAD", allow_private=True, timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertEqual(res["body"], b"")

    def test_user_agent_added_when_caller_did_not_bring_one(self):
        res = guarded_fetch(self.base + "/echo", user_agent="netguard-tests/0.1",
                            allow_private=True, timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertEqual(_LAST["user_agent"], "netguard-tests/0.1")

    def test_caller_user_agent_wins(self):
        guarded_fetch(self.base + "/echo", headers={"User-Agent": "caller/1.0"},
                      user_agent="netguard-tests/0.1", allow_private=True, timeout_s=5)
        self.assertEqual(_LAST["user_agent"], "caller/1.0")

    def test_accept_encoding_identity_is_forced(self):
        # The cap must be predictable: whatever the caller asks, the wire says identity.
        guarded_fetch(self.base + "/echo", headers={"Accept-Encoding": "gzip"},
                      allow_private=True, timeout_s=5)
        self.assertEqual(_LAST["accept_encoding"], "identity")

    def test_caller_host_header_allowed_when_it_matches(self):
        res = guarded_fetch(self.base + "/echo", headers={"Host": "127.0.0.1:{}".format(self.port)},
                            allow_private=True, timeout_s=5)
        self.assertEqual(res["status"], 200)

    def test_caller_host_header_mismatch_refused(self):
        with self.assertRaises(GuardError) as ctx:
            guarded_fetch(self.base + "/echo", headers={"Host": "evil-redirect.example"},
                          allow_private=True, timeout_s=5)
        self.assertIn("Host header", str(ctx.exception))

    def test_header_value_with_carriage_return_refused(self):
        for evil in ("a" + chr(13), "a" + chr(10), "a" + chr(13) + chr(10) + "b", "a\x00b"):
            with self.subTest(repr(evil)):  # never reaches the socket
                with self.assertRaises(GuardError) as ctx:
                    guarded_fetch(self.base + "/echo", headers={"X-Test": evil},
                                  allow_private=True, timeout_s=5)
                self.assertIn("injection", str(ctx.exception))

    def test_header_name_injected_refused(self):
        for evil in ({"X\n-Test": "v"}, {"X-Test\r": "v"}, {"X Bad": "v"}, {"": "v"}):
            with self.subTest(repr(list(evil))):
                with self.assertRaises(GuardError):
                    guarded_fetch(self.base + "/echo", headers=evil,
                                  allow_private=True, timeout_s=5)

    def test_non_str_header_value_refused(self):
        with self.assertRaises(GuardError):
            guarded_fetch(self.base + "/echo", headers={"X-N": 5}, allow_private=True, timeout_s=5)

    def test_headers_non_mapping_refused(self):
        with self.assertRaises(GuardError):
            guarded_fetch(self.base + "/echo", headers=["a", "b"], allow_private=True, timeout_s=5)

    def test_invalid_method_refused(self):
        for bad in ("TRACE", "CONNECT", "get", "PURGE"):
            with self.subTest(method=bad):
                with self.assertRaises(GuardError) as ctx:
                    guarded_fetch(self.base + "/echo", method=bad,
                                  allow_private=True, timeout_s=5)
                self.assertIn("Method", str(ctx.exception))

    def test_body_must_be_bytes_or_none(self):
        with self.assertRaises(GuardError):
            guarded_fetch(self.base + "/post-echo", method="POST", body={"json": True},
                          allow_private=True, timeout_s=5)
        # str is the one allowed friendly coercion
        res = guarded_fetch(self.base + "/post-echo", method="POST", body="hello",
                            allow_private=True, timeout_s=5)
        self.assertEqual(res["body"], b"hello")

    def test_byte_cap_refuses_and_reports(self):
        # server sends 1 MB; cap 10k → read probes max_bytes+1 and refuses
        with self.assertRaises(GuardError) as ctx:
            guarded_fetch(self.base + "/big", max_bytes=10_000, allow_private=True, timeout_s=5)
        self.assertIn("cap de bytes", str(ctx.exception))

    def test_byte_cap_below_limit_passes(self):
        res = guarded_fetch(self.base + "/big", max_bytes=_ONE_MB, allow_private=True, timeout_s=10)
        self.assertEqual(res["status"], 200)
        self.assertEqual(len(res["body"]), _ONE_MB)

    def test_non_positive_limits_refused_without_connecting(self):
        with self.assertRaises(GuardError):
            guarded_fetch(self.base + "/echo", timeout_s=0, allow_private=True)
        with self.assertRaises(GuardError):
            guarded_fetch(self.base + "/echo", max_bytes=0, allow_private=True)


class TestGuardedFetchRefusals(_LocalFetchCase):
    def test_default_policy_refuses_loopback_target(self):
        # No patches at all: 127.0.0.1 must be refused by the REAL deny-list path
        # (resolve + deny) — the loopback opt-in is explicit.
        self.assertGreater(self.port, 0)
        with self.assertRaises(GuardError) as ctx:
            guarded_fetch(self.base + "/echo", timeout_s=5)
        self.assertIn("private", str(ctx.exception))


class TestRedirectPolicy(_LocalFetchCase):
    def test_3xx_returned_as_is_when_not_following(self):
        res = guarded_fetch(self.base + "/hop-1", allow_private=True, timeout_s=5)
        self.assertEqual(res["status"], 302)
        self.assertEqual(res["headers"]["location"], "/hop-2")
        self.assertEqual(_LAST["path"], "/hop-1")
        with _LOCK:
            self.assertEqual(_HITS.get("/hop-2", 0), 0)  # nothing chased

    def test_follow_relative_chain_to_final(self):
        # hop1 /hop-1 → hop2 /hop-2 → hop3 /final: 3 requests, budget exactly spent.
        res = guarded_fetch(self.base + "/hop-1", allow_private=True,
                            follow_redirects=True, timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertIn(b"path=/final", res["body"])
        self.assertTrue(res["url"].endswith("/final"))

    def test_follow_absolute_location_still_pinned(self):
        with _pin_loopback():  # allow_private=False + fake resolve → every hop re-pinned
            res = guarded_fetch(self.base + "/redir-abs", allow_private=False,
                                follow_redirects=True, timeout_s=5)
        self.assertEqual(res["status"], 200)
        self.assertTrue(res["url"].endswith("/final"))
        self.assertEqual(_LAST["host"], "127.0.0.1:{}".format(self.port))

    def test_redirect_to_private_refused(self):
        # The redirect target looks public by NAME, but its (faked) resolution is a
        # private range — the hop re-runs check_url and must raise, never follow.
        def resolver(host):
            return ["10.44.1.5"] if host == "rebind-target.example" else ["127.0.0.1"]

        with _pin_loopback(resolver=resolver):
            with self.assertRaises(GuardError) as ctx:
                guarded_fetch(self.base + "/redir-rebind", allow_private=False,
                              follow_redirects=True, timeout_s=5)
        self.assertIn("private", str(ctx.exception))

    def test_redirect_to_localhost_name_refused(self):
        with _pin_loopback():  # re-check of localhost is refused by NAME, no resolve
            with self.assertRaises(GuardError) as ctx:
                guarded_fetch(self.base + "/redir-localhost", allow_private=False,
                              follow_redirects=True, timeout_s=5)
        self.assertIn("private-network", str(ctx.exception))

    def test_hops_capped_at_three(self):
        # /chain-1 → /chain-2 → /chain-3 → (/chain-4 never fetched): budget of 3
        # requests including the initial; the 302 on the last hop is returned as data.
        res = guarded_fetch(self.base + "/chain-1", allow_private=True,
                            follow_redirects=True, timeout_s=5)
        self.assertEqual(res["status"], 302)
        self.assertTrue(res["url"].endswith("/chain-3"))
        with _LOCK:
            self.assertEqual(_HITS.get("/chain-4", 0), 0)
            self.assertEqual(_HITS.get("/chain-3", 0), 1)
            self.assertEqual(_HITS.get("/chain-2", 0), 1)


# --- TLS subclass construction (no network) ---------------------------------------


class TestPinnedTlsConstruction(unittest.TestCase):
    def test_https_subclass_builds_with_sni_and_pin(self):
        context = ssl.create_default_context()
        conn = _PinnedHTTPSConnection(
            "example.com", 443, timeout=7, context=context,
            pin_ip="93.184.216.34", sni_hostname="example.com",
        )
        self.assertEqual(conn.host, "example.com")
        self.assertEqual(conn.port, 443)
        self.assertEqual(conn.timeout, 7)
        self.assertEqual(conn._pin_ip, "93.184.216.34")
        self.assertEqual(conn._sni_hostname, "example.com")
        self.assertIs(conn._context, context)

    def test_http_subclass_builds_with_pinned_ip(self):
        conn = netguard._PinnedHTTPConnection(
            "127.0.0.1", 8080, timeout=7, pin_ip="127.0.0.1",
        )
        self.assertEqual(conn.host, "127.0.0.1")
        self.assertEqual(conn.port, 8080)
        self.assertEqual(conn._pin_ip, "127.0.0.1")

    def test_sni_hostname_idna_and_fallback(self):
        self.assertEqual(_sni_hostname("münchen.example"), "xn--mnchen-3ya.example")
        self.assertEqual(_sni_hostname("example.com"), "example.com")
        self.assertIsInstance(_sni_hostname("!!"), str)  # idna-fail fallback stays a str


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
