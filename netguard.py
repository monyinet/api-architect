"""Network guard — the SINGLE choke-point for every request this plugin makes.

Policies frozen in the red/blue decision (2026-11-05, plugin-data dir `decisions/`
kept in the planning vault):
- Deny-list of private/special ranges by default; opt-in only via config `allow_private_hosts`.
- Resolve-then-pin: the request is made to a VALIDATED IP (SNI/Host still correct),
  closing the basic DNS-rebinding escape (first-resolve check, connect re-solve race).
- Redirects are NOT followed at v0 (a 3xx is data; the caller decides).
- Hard timeout on every call + response size cap.
- One module owns ALL of this — handlers never touch urllib/httpx themselves.

Status (5-nov, lane L3): check_url() DONE and tested (signature FROZEN — do not
change it); guarded_fetch() LANDED: resolve-then-pin with SNI, byte cap, header
hardening (no CR/LF, Host locked, Accept-Encoding: identity forced), redirects
re-admitted through check_url on every hop (max 3 requests counting the initial).

guarded_fetch() scope, stated honestly (v0 limits):
- ToC/DNS rebinding: the pin closes the race AFTER the one validated resolution
  (resolve once, connect to THE resolved IP, never re-resolve on connect). A
  resolver already poisoned BEFORE guarded_fetch is called can still hand back an
  attacker IP that looks public — out of scope for v0 (noted in the README).
- TLS: system trust store; standard certificate + hostname verification, with SNI
  carrying the HOSTNAME (never the pinned IP). No client certs, no public-key
  pinning, no TLS fingerprint handling.
- Proxies: none — plain socket.create_connection, direct only.
- Compression: NEVER negotiated. "Accept-Encoding: identity" is FORCED on every
  request so the byte cap counts ordinary (uncompressed) network bytes; a server
  that still sends gzip (against policy) gets those bytes returned OPAQUE — the
  digest reads headers/plain content only and we do not decompress.
- Caller headers are repeated verbatim on every redirect hop. Do not attach
  Authorization-style headers when following redirects unless you can tolerate
  them reaching a redirect target that independently passed check_url.
- Nothing here logs URLs, headers or decisions (call sites redact; netguard is
  silent by design — a redirect to private looks identical to a refused fetch).
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse

MAX_BYTES_DEFAULT = 2_000_000  # 2 MB response cap
RESOLVE_TIMEOUT_S = 8

# Special/reserved + private ranges (IPv4 + IPv6), always denied unless allow_private.
_DENIED_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
        "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "64:ff9b:1::/48", "100::/64",
    )
)


class GuardError(Exception):
    """Raised for any refused request. Message is user-facing: actionable, no secrets."""


def ip_is_denied(addr: str, allow_private: bool = False) -> bool:
    """True if addr (IPv4/IPv6 string) falls in a denied range."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])  # strip scope id (fe80::1%eth0)
    except ValueError:
        return True  # unparseable → deny
    if allow_private:
        return False
    return any(ip in net for net in _DENIED_NETS)


def resolve_host(hostname: str):
    """All A/AAAA addresses for hostname. Raises GuardError on failure."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise GuardError("DNS resolution failed for {}: {}".format(hostname, exc))
    return sorted({i[4][0] for i in infos})


def check_url(url: str, allow_private: bool = False):
    """Full admission check for a URL. Returns (p, ips) — parsed split + validated IPs.
    Raises GuardError with an actionable message otherwise. Never raises for the
    URL's CONTENT, only for refusal to connect."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise GuardError("Only http/https URLs are supported.")
    if not p.hostname:
        raise GuardError("URL has no hostname: {}".format(p.scheme + "://..."))
    if p.username or p.password:
        raise GuardError("URLs with embedded credentials are refused.")
    host = p.hostname
    # hostname-level blocks (resolution would fail later, but fail EARLY with a clear line)
    low = host.lower()
    if low.endswith(".localhost") or low == "localhost" or low.endswith(".internal") or low.endswith(".home.arpa"):
        if not allow_private:
            raise GuardError("Host {} is private-network; blocked by default (allow_private_hosts=false).".format(host))
    if allow_private:
        return p, []  # no resolve needed — raw connect path
    ips = resolve_host(host)
    if not ips:
        raise GuardError("Host {} resolved to no addresses.".format(host))
    bad = [ip for ip in ips if ip_is_denied(ip, allow_private=False)]
    if bad:
        # Do not echo private details; name the policy, not the address.
        raise GuardError(
            "Host {} resolves to private/reserved addresses; blocked by default "
            "(opt-in allow_private_hosts in plugin settings if this API is genuinely yours).".format(host)
        )
    return p, ips


MAX_REDIRECT_HOPS = 3  # total requests when following redirects, INCLUDING the initial

_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# Only 301/302/303/307/308 are genuine redirects; 300/304/305+ come back as data.
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

# Response headers that survive into the returned dict — the small, readable
# subset the digest/executor consume. Everything else is dropped on purpose.
_HEADERS_KEPT = ("content-type", "content-length", "server", "date", "location", "retry-after")

# RFC 7230 token characters for header names (defends against raw injection).
_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


if http.client is not None:

    class _PinnedHTTPConnection(http.client.HTTPConnection):
        """HTTPConnection that opens its socket to ONE pre-validated IP (the pin).
        The Host header and the request URL keep the real hostname: the pin only
        decides WHERE the packets go, never WHAT we claim to talk to."""

        def __init__(self, host, port=None, timeout=None, *, pin_ip="", **kw):
            http.client.HTTPConnection.__init__(self, host, port, timeout=timeout, **kw)
            self._pin_ip = pin_ip

        def connect(self):
            # Rebinding mitigation: connect to the address that was validated once,
            # never re-resolve. Empty pin (allow_private path) falls back to hostname.
            target = self._pin_ip or self.host
            self.sock = socket.create_connection((target, self.port), self.timeout)

    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        """HTTPS flavor of the pin: TCP is opened to the validated IP, then wrapped
        in TLS with server_hostname=THE HOSTNAME — SNI and certificate matching are
        both driven by the hostname, so a cert for the IP (or any other name)
        cannot pass. Standard anti-rebinding pattern."""

        def __init__(self, host, port=None, timeout=None, *, pin_ip="", sni_hostname="", **kw):
            http.client.HTTPSConnection.__init__(self, host, port, timeout=timeout, **kw)
            self._pin_ip = pin_ip
            self._sni_hostname = sni_hostname

        def connect(self):
            target = self._pin_ip or self.host
            self.sock = socket.create_connection((target, self.port), self.timeout)
            # check_hostname=True (default context) makes wrap_socket verify the
            # certificate against server_hostname, while SNI carries the same name.
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self._sni_hostname)


def _sni_hostname(hostname: str) -> str:
    """ASCII (IDNA/punycode) form of a hostname, for SNI and the Host header.
    IDNA encoding can legitimately fail on real-world unicode hosts (empty or
    malformed labels); on failure we keep the raw hostname and let the TLS/HTTP
    layer decide — never logged, never silently 'repaired'."""
    try:
        return hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return hostname


def _coerce_body(body):
    """Frozen contract says bytes|None; str is coerced to UTF-8 because the executor
    hands over text bodies. Anything else is refused — dict/json never smuggled in."""
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    if isinstance(body, (bytearray, memoryview)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    raise GuardError("Request body must be bytes or None (str is accepted as UTF-8).")


def _sanitize_headers(headers):
    """Validate caller headers defensively. Returns (clean_dict, host_values).
    Rejects: non-mapping input, non-text values, CR/LF/NUL anywhere in a name or
    value ('header injection'), non-token header names. A caller Host header is
    kept but re-checked against every hop's target later."""
    if headers is None:
        return {}, []
    try:
        items = list(headers.items())
    except AttributeError as exc:
        raise GuardError("headers must be a dict (name -> value).") from exc
    clean = {}
    host_values = []
    for name, value in items:
        if not isinstance(name, str) or not isinstance(value, str):
            raise GuardError("Header names and values must be text (str).")
        if any(c in name for c in "\r\n\x00") or any(c in value for c in "\r\n\x00"):
            raise GuardError("header injection: CR/LF/NUL is not allowed in caller headers.")
        if name.lower() == "host":
            host_values.append(value.strip())
            clean[name] = value
            continue
        if not _TOKEN_RE.match(name):
            raise GuardError("Invalid header name (RFC 7230 token characters only).")
        clean[name] = value
    return clean, host_values


def _host_header_allowed(value: str, hostname: str, port: int) -> bool:
    """True if a CALLER-provided Host header matches this hop's target. http.client
    would set exactly this header by itself; a different value is a classic
    request-smuggling shape, so anything other than the true host is refused."""
    allowed = set()
    for h in (hostname, _sni_hostname(hostname)):
        h = h.lower()
        allowed.add(h)
        allowed.add("{}:{}".format(h, port))
    return value.strip().lower() in allowed


def _request_target(p) -> str:
    """Path + query for http.client (the fragment never leaves the client)."""
    target = p.path or "/"
    if p.query:
        target += "?" + p.query
    return target


def _open_connection(scheme: str, hostname: str, port: int, pin_ip: str, timeout_s: int):
    """Build the connection object for one hop.
    pin_ip non-empty → socket to the pre-validated IP ONLY (the pin).
    pin_ip empty     → allow_private raw path: connect by hostname (documented:
                       no resolution/validation happens in that lane).
    In both cases Host and SNI keep the hostname."""
    host_for_wire = _sni_hostname(hostname)  # punycode when possible
    if scheme == "https":
        if ssl is None:  # pragma: no cover
            raise GuardError("SSL support (module ssl) is unavailable in this Python.")
        context = ssl.create_default_context()
        return _PinnedHTTPSConnection(
            host_for_wire, port, timeout=timeout_s, context=context,
            pin_ip=pin_ip, sni_hostname=host_for_wire,
        )
    if http.client is None:  # pragma: no cover
        raise GuardError("http.client is unavailable in this Python.")
    return _PinnedHTTPConnection(host_for_wire, port, timeout=timeout_s, pin_ip=pin_ip)


def _guarded_request(p, ips, method: str, headers: dict, body, timeout_s: int,
                     max_bytes: int, host_values=()):
    """One pre-checked hop (check_url already admitted it). Returns
    (status, kept-headers dict, body bytes). The socket is ALWAYS closed,
    including on the byte-cap refusal."""
    hostname = p.hostname
    port = p.port if p.port is not None else (443 if p.scheme == "https" else 80)
    for hv in host_values:
        if not _host_header_allowed(hv, hostname, port):
            raise GuardError(
                "Refusing caller Host header: netguard sets Host itself and it "
                "must match the request target."
            )
    pin_ip = ""
    if ips:
        # Pin = first NOT-denied, already-validated address (check_url guarantees
        # the whole list is clean when allow_private is False; the 'all denied'
        # arm stays as a defensive safety net).
        for ip in ips:
            if not ip_is_denied(ip, allow_private=False):
                pin_ip = ip
                break
        if not pin_ip:
            raise GuardError(
                "Every resolved address for {} is in a denied range; refusing to connect.".format(hostname)
            )
    conn = _open_connection(p.scheme, hostname, port, pin_ip, timeout_s)
    try:
        conn.request(method, _request_target(p), body=body, headers=headers)
        resp = conn.getresponse()
        # The cap is over bytes AS RECEIVED (identity was forced upstream); '+1'
        # overshoot probe keeps the check honest regardless of Content-Length.
        payload = resp.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise GuardError("respuesta sobre el cap de bytes (max_bytes)")
        status = resp.status
        kept = {}
        for name in _HEADERS_KEPT:
            value = resp.getheader(name)
            if value is not None:
                kept[name] = value
    except GuardError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # Hostname only in messages — never the full URL (query may hold a credential).
        raise GuardError(
            "Request to {} failed ({}); nothing followed further.".format(hostname, type(exc).__name__)
        ) from exc
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover — best-effort socket release
            pass
    return status, kept, payload


def guarded_fetch(
    url: str,
    method: str = "GET",
    headers=None,
    body: bytes | None = None,
    timeout_s: int = 15,
    max_bytes: int = MAX_BYTES_DEFAULT,
    user_agent: str = "",
    allow_private: bool = False,
    follow_redirects: bool = False,
) -> dict:
    """The ONLY way this plugin talks to the network. Returns
    {"status": int, "headers": dict, "body": bytes, "url": str} where headers is the
    small readable subset (_HEADERS_KEPT) and url is the address actually fetched
    (the last hop when redirects are followed).

    Connection semantics (frozen policies):
    - Resolve-then-pin: check_url resolves ONCE; the socket opens to the first
      validated (non-denied) IP, with SNI/Host still carrying the hostname. This
      closes the resolve-then-connect DNS rebinding race. Mitigation limit: a
      resolver poisoned BEFORE this call can still return a public-looking
      attacker IP — out of scope for v0 (noted in the README).
    - allow_private=True: NO resolution — connects by hostname (the "this host is
      genuinely my own internal service" opt-in). check_url still refuses
      non-http/https schemes and embedded credentials. The response carries
      nothing extra that would leak the decision; netguard never logs.
    - Byte cap: reads at most max_bytes+1 bytes; even one byte over refuses the
      whole response (GuardError) and closes the socket.
    - Headers: caller headers validated (no CR/LF/NUL — 'header injection';
      token-char names); a caller Host header must equal the hop's target or the
      request is refused; user_agent is added only when the caller did not bring
      their own; "Accept-Encoding: identity" is always forced (no decompression,
      cap stays predictable).

    Redirects:
    - follow_redirects=False (v0 default): a 3xx is returned AS-IS — it is data,
      the caller decides.
    - follow_redirects=True: Location is joined against the current URL (relative
      or absolute) and re-admitted through a FRESH check_url on every hop — never
      followed blindly, which is what closes redirect-based rebinding. Budget:
      MAX_REDIRECT_HOPS=3 requests counting the initial one; whatever the last
      request returned is handed back as-is. A redirect target refused by policy
      (e.g. resolving to private ranges under the default config) raises
      GuardError. 301/302/303 downgrade non-GET/HEAD to GET and drop the body;
      307/308 preserve method and body. Caller headers are repeated on each hop.
    """
    if not isinstance(url, str) or not url.strip():
        raise GuardError("url must be a non-empty string.")
    if method not in _ALLOWED_METHODS:
        raise GuardError(
            "Method must be one of {} (got {!r}).".format("/".join(_ALLOWED_METHODS), method)
        )
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise GuardError("timeout_s must be a positive number of seconds.")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise GuardError("max_bytes must be an integer >= 1.")

    resolved_body = _coerce_body(body)
    merged, host_values = _sanitize_headers(headers)
    if user_agent:
        if not isinstance(user_agent, str) or any(c in user_agent for c in "\r\n\x00"):
            raise GuardError("header injection: invalid user_agent value.")
        if not any(k.lower() == "user-agent" for k in merged):
            merged["User-Agent"] = user_agent
    # Compression is never negotiated: force identity so the byte cap is over
    # predictable network bytes. Overrides any caller Accept-Encoding, on purpose.
    merged = {k: v for k, v in merged.items() if k.lower() != "accept-encoding"}
    merged["Accept-Encoding"] = "identity"

    current_url = url
    current_method = method
    current_body = resolved_body
    result = {}
    hops = 0
    while True:
        p, ips = check_url(current_url, allow_private=allow_private)
        status, kept, payload = _guarded_request(
            p, ips, current_method, merged, current_body, timeout_s, max_bytes,
            host_values=host_values,
        )
        result = {"status": status, "headers": kept, "body": payload, "url": current_url}
        hops += 1
        if not follow_redirects:
            return result  # any 3xx is data; the caller decides
        if hops >= MAX_REDIRECT_HOPS:
            return result  # hop budget spent: hand back whatever we hold, chase no further
        if status not in _REDIRECT_CODES or not kept.get("location", "").strip():
            return result
        current_url = urllib.parse.urljoin(current_url, kept["location"].strip())
        if status in (301, 302, 303) and current_method not in ("GET", "HEAD"):
            # Classic redirect semantics: the payload is not repeated at the new URL.
            current_method = "GET"
            current_body = None
        # 307/308: method AND body preserved.
