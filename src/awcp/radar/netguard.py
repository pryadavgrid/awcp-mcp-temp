"""SSRF guard for operator/agent-supplied URLs the radar itself fetches.

Two places in the radar make an outbound request to a URL it did NOT choose:
  * onboarding.link_mcp()  — connects to an agent's declared SSE endpoint;
  * api._post_control()    — POSTs suspend/resume to an agent's control_endpoint.

Both URLs originate from a registration payload, so a hostile registrant could
aim them at a private/link-local address (the classic cloud-metadata SSRF, e.g.
http://169.254.169.254/...). assert_safe_url() resolves the host to its actual
IP(s) FIRST and refuses anything that lands inside a private/loopback/link-local
range — closing the gap without changing any legitimate same-host wiring, which
is allowed back in explicitly via AGENT_RADAR_ALLOW_LOOPBACK (default on for
local dev, off in prod).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Address ranges an operator-supplied URL must never resolve into. Loopback is
# listed separately so local-dev wiring (agents on 127.0.0.1) can opt back in.
_LOOPBACK_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128")]
_PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",   # link-local — includes the cloud metadata service
        "::ffff:0:0/96",    # IPv4-mapped IPv6 (defeats ::ffff:169.254.169.254)
        "fc00::/7",         # unique-local IPv6
        "fe80::/10",        # link-local IPv6
    )
]

# Local-dev wiring (agents on localhost) is legitimate; production should set
# AGENT_RADAR_ALLOW_LOOPBACK=false so even loopback is refused.
ALLOW_LOOPBACK = os.getenv("AGENT_RADAR_ALLOW_LOOPBACK", "true").lower() == "true"


class UnsafeURLError(ValueError):
    """Raised when a URL resolves to a blocked (private/link-local) address."""


def _blocked_nets() -> list:
    nets = list(_PRIVATE_NETS)
    if not ALLOW_LOOPBACK:
        nets += _LOOPBACK_NETS
    return nets


def assert_safe_url(url: str) -> None:
    """Raise UnsafeURLError if `url` is malformed or resolves to a private,
    loopback (when disallowed), or link-local address. Returns None when safe.

    DNS is resolved here so a hostname that *points* at a private IP is caught —
    a check on the literal string alone would miss that."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty or non-string url")
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"url has no host: {url!r}")
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"refusing non-http(s) scheme: {parsed.scheme!r}")

    blocked = _blocked_nets()
    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc

    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if any(ip in net for net in blocked):
            raise UnsafeURLError(f"refusing to fetch private/link-local address: {ip}")


def is_safe_url(url: str) -> bool:
    """Boolean convenience wrapper around assert_safe_url (never raises)."""
    try:
        assert_safe_url(url)
        return True
    except UnsafeURLError:
        return False
