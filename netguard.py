"""Network guard — the SINGLE choke-point for every request this plugin makes.

Policies frozen in the red/blue decision (2026-11-05, plugin-data dir `decisions/`
kept in the planning vault):
- Deny-list of private/special ranges by default; opt-in only via config `allow_private_hosts`.
- Resolve-then-pin: the request is made to a VALIDATED IP (SNI/Host still correct),
  closing the basic DNS-rebinding escape (first-resolve check, connect re-solve race).
- Redirects are NOT followed at v0 (a 3xx is data; the caller decides).
- Hard timeout on every call + response size cap.
- One module owns ALL of this — handlers never touch urllib/httpx themselves.

Status (5-nov): check_url() DONE and tested; guarded_fetch() interface frozen,
implementation lands in the L3 lane tonight (stdlib http.client + ssl).
"""
from __future__ import annotations

import ipaddress
import urllib.parse

try:
    import socket
except ImportError:  # pragma: no cover
    socket = None  # type: ignore[assignment]

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
    {"status": int, "headers": dict, "body": bytes, "url": str}.
    Connection is made to a VALIDATED IP (pin); SNI/Host stay the hostname's.
    3xx responses are returned as-is (redirects not followed at v0)."""
    raise GuardError("guarded_fetch lands in lane L3 tonight (interface frozen).")
