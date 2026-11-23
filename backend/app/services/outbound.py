"""Outbound connections to user-supplied targets (webhooks, Slack, pings, SMTP).

Every target is resolved first and each address is checked; the connection then goes to the
checked address (the URL host is replaced by the IP, with the Host header and TLS SNI set to
the real name), so a DNS answer that changes between check and connect cannot redirect it.
"""

import asyncio
import ipaddress
import smtplib
import socket
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_TIMEOUT = 10.0
MAX_URL_LENGTH = 2048

Resolver = Callable[[str, int], list[str]]


class OutboundBlocked(Exception):  # noqa: N818  (reads better as a domain error)
    """The target is not allowed or cannot be reached safely. The message is safe to show."""


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def check_address(address: str, allow_private: bool) -> None:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        raise OutboundBlocked("The target resolved to an invalid address.") from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise OutboundBlocked("Loopback, link-local and multicast targets are not allowed.")
    if ip.is_reserved:
        raise OutboundBlocked("Reserved addresses are not allowed as targets.")
    if not ip.is_global and not allow_private:
        raise OutboundBlocked(
            "Private network targets are not allowed "
            "(set ALLOW_PRIVATE_NOTIFICATION_TARGETS=true to permit them)."
        )


def check_host_static(host: str, allow_private: bool) -> None:
    """Checks that need no DNS lookup (literal IPs, localhost), for validation at save time."""
    name = host.strip("[]").lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        raise OutboundBlocked("Loopback, link-local and multicast targets are not allowed.")
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return
    check_address(name, allow_private)


def check_url_static(url: str, allow_private: bool) -> None:
    check_host_static(urlsplit(url).hostname or "", allow_private)


def validate_url(url: str, *, https_only: bool = False) -> str:
    """Shape checks for a target URL. Raises ValueError with a message safe to show."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValueError(f"URL must be 1-{MAX_URL_LENGTH} characters.")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("URL must not contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("URL is not valid.") from None
    schemes = ("https",) if https_only else ("http", "https")
    if parts.scheme not in schemes or not parts.hostname:
        raise ValueError(f"URL must start with {' or '.join(s + '://' for s in schemes)}.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL must not contain credentials; use headers instead.")
    if port == 0:
        raise ValueError("URL port is not valid.")
    if parts.fragment:
        raise ValueError("URL must not contain a fragment.")
    return url


@dataclass
class OutboundPolicy:
    allow_private: bool = False
    resolver: Resolver = system_resolver
    # Tests pass an httpx.MockTransport here; production uses httpx's default transport.
    transport: httpx.AsyncBaseTransport | None = None
    # Tests stub smtplib here: called as factory(security, address, host, port, timeout).
    smtp_factory: Callable[[str, str, str, int, float], smtplib.SMTP] | None = None

    async def resolve(self, host: str, port: int) -> str:
        """Resolve `host` and check every address; return the one to connect to."""
        bare = host.strip("[]")
        try:
            ipaddress.ip_address(bare)
            addresses = [bare]
        except ValueError:
            addresses = await asyncio.to_thread(self.resolver, host, port)
        if not addresses:
            raise OutboundBlocked("The target host name could not be resolved.")
        for address in addresses:
            check_address(address, self.allow_private)
        return addresses[0]


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


async def send_http(
    policy: OutboundPolicy,
    method: str,
    url: str,
    *,
    content: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Response:
    """One request with no redirects, no proxies from the environment and a pinned address.

    The response body is read but must never be logged by callers."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or _default_port(parts.scheme)
    address = await policy.resolve(host, port)
    literal = f"[{address}]" if ":" in address else address
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = f"{host}:{parts.port}" if parts.port else host
    request_headers = {**(headers or {}), "Host": host_header}
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    async with httpx.AsyncClient(
        transport=policy.transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
    ) as client:
        response = await client.request(
            method, pinned, content=content, headers=request_headers, extensions=extensions
        )
    _check_peer(response, address)
    return response


def _check_peer(response: httpx.Response, address: str) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    peer = stream.get_extra_info("server_addr")
    if peer and str(peer[0]).split("%", 1)[0] != address.split("%", 1)[0]:
        raise OutboundBlocked("The connection reached a different address than was checked.")


def expect_success(response: httpx.Response) -> None:
    if response.is_redirect or 300 <= response.status_code < 400:
        raise OutboundBlocked(
            f"The target answered with a redirect (HTTP {response.status_code}); "
            "redirects are not followed."
        )
    if not response.is_success:
        raise OutboundBlocked(f"The target answered HTTP {response.status_code}.")


class PinnedSMTP(smtplib.SMTP):
    """smtplib.SMTP that connects to a pre-checked address but keeps the host name for TLS."""

    def __init__(self, address: str, **kwargs: Any) -> None:
        self._address = address
        super().__init__(**kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection((self._address, port), timeout, self.source_address)


class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, address: str, **kwargs: Any) -> None:
        self._address = address
        super().__init__(**kwargs)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        sock = socket.create_connection((self._address, port), timeout, self.source_address)
        hostname: str = getattr(self, "_host", "")
        return self.context.wrap_socket(sock, server_hostname=hostname)


def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()
