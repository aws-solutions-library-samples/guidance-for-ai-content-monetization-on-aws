"""Guarded outbound HTTP fetch for operator-supplied URLs (SSRF guard).

Spec: ``.kiro/specs/scrape-vs-api-comparison`` R7.2–R7.3, design §3a. Used by the comparison run
route to fetch one publisher page server-side. Pure standard library (``urllib``, ``socket``,
``ipaddress``): no boto3, no third-party HTTP client, so the guard is testable without AWS and the
rules are visible in one place.

Rules (each one has a refusal class and a unit test):

* ``https`` only; no credentials in the URL; default port only; a DNS hostname, never an IP literal.
* Every address the hostname resolves to must be globally routable: private, loopback, link-local,
  multicast, unspecified, reserved, IPv4-mapped/6to4/Teredo and the cloud metadata endpoints are
  refused. The check runs before the connection and again on every redirect hop.
* At most ``MAX_REDIRECTS`` redirects, each re-validated, never downgraded to ``http``.
* The body is read up to ``max_bytes`` and the whole fetch has one deadline; the response must be
  ``text/html``.
* No cookies, no ``Authorization``; a fixed, truthful ``User-Agent``.
* An optional host allow-list (exact host or ``*.suffix``); an empty list means "any host that
  passes the rules above" and the caller reports that mode to the user.

Nothing here retries a refusal.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

USER_AGENT = "AWS-Guidance-ContentComparison/1 (+https://github.com/aws-solutions-library-samples)"
MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_DEADLINE_S = 10.0
MAX_URL_LENGTH = 2048

# Cloud metadata endpoints are refused by address as well as by range (169.254/16 is link-local,
# but the IPv6 EC2 endpoint is in a ULA block that ``is_private`` already covers — listed anyway so
# the intent is explicit).
_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
        ipaddress.ip_address("100.100.100.200"),  # Alibaba/other metadata; in 100.64/10 (shared, refused anyway)
    }
)


class FetchRefused(Exception):
    """A URL or response was refused by the guard. ``error_code`` is stable (contract R7.2)."""

    def __init__(self, error_code: str, message: str, *, upstream_status: Optional[int] = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.upstream_status = upstream_status


@dataclass(frozen=True)
class FetchResult:
    status: int
    final_url: str
    headers: Dict[str, str]
    body: bytes
    wall_clock_ms: int
    redirects: int


@dataclass(frozen=True)
class ProbeResult:
    status: int
    content_length: Optional[int]
    wall_clock_ms: int


# ── URL validation ───────────────────────────────────────────────────────────


def validate_url(url: str) -> str:
    """Return the normalised URL, or raise ``FetchRefused('invalid_url', …)``.

    Normalisation lower-cases the host and drops the fragment; the path and query are kept as
    given. The result is what the fetch uses and what is shown back to the operator.
    """
    if not isinstance(url, str) or not url.strip():
        raise FetchRefused("invalid_url", "A URL is required")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise FetchRefused("invalid_url", f"The URL is longer than {MAX_URL_LENGTH} characters")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise FetchRefused("invalid_url", "The URL could not be parsed") from exc

    if parts.scheme != "https":
        raise FetchRefused("invalid_url", "Only https URLs are accepted")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("invalid_url", "Credentials in the URL are not accepted")
    hostname = parts.hostname
    if not hostname:
        raise FetchRefused("invalid_url", "The URL has no host")
    try:
        if parts.port is not None:
            raise FetchRefused("invalid_url", "Only the default https port is accepted")
    except ValueError as exc:  # non-numeric port
        raise FetchRefused("invalid_url", "The URL port is invalid") from exc
    if _looks_like_ip_literal(hostname):
        raise FetchRefused("invalid_url", "IP-literal hosts are not accepted; use a DNS name")
    if not _is_dns_hostname(hostname):
        raise FetchRefused("invalid_url", "The host is not a valid DNS name")

    netloc = hostname.lower()
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


def _looks_like_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.strip("[]"))
        return True
    except ValueError:
        return False


def _is_dns_hostname(hostname: str) -> bool:
    if len(hostname) > 253 or hostname.endswith("."):
        return False
    labels = hostname.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not all(c.isalnum() or c == "-" for c in label):
            return False
    return True


# ── Allow-list ───────────────────────────────────────────────────────────────


def parse_allow_list(value: Optional[str]) -> List[str]:
    """Parse ``COMPARISON_ALLOWED_HOSTS`` (comma or whitespace separated) into lower-case entries."""
    if not value:
        return []
    entries = []
    for raw in value.replace(",", " ").split():
        entry = raw.strip().lower().rstrip(".")
        if entry:
            entries.append(entry)
    return entries


def host_allowed(hostname: str, allow_list: Sequence[str]) -> bool:
    """True when ``allow_list`` is empty (open mode) or ``hostname`` matches an entry.

    An entry is an exact host (``portal.example.com``) or a suffix wildcard (``*.example.com``,
    which matches ``a.example.com`` and ``a.b.example.com`` but not ``example.com`` itself).
    """
    if not allow_list:
        return True
    host = hostname.lower().rstrip(".")
    for entry in allow_list:
        if entry.startswith("*."):
            suffix = entry[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == entry:
            return True
    return False


# ── Address checks ───────────────────────────────────────────────────────────


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally routable unicast address that is not a metadata endpoint."""
    if address in _METADATA_ADDRESSES:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return is_public_address(mapped)
        if address.sixtofour is not None:
            return is_public_address(address.sixtofour)
        if address.teredo is not None:
            return is_public_address(address.teredo[1])
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or not address.is_global
    ):
        return False
    return True


def resolve_public_addresses(hostname: str) -> List[str]:
    """Resolve ``hostname`` and return its addresses, or raise if any of them is not public.

    All records are checked, not just the first: a name that mixes a public and a private address
    is refused, since the connection may pick either.
    """
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchRefused("url_unresolvable", f"The host {hostname} does not resolve") from exc
    addresses: List[str] = []
    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw.split("%")[0])
        except ValueError as exc:
            raise FetchRefused("url_unresolvable", f"The host {hostname} resolved to an invalid address") from exc
        if not is_public_address(address):
            raise FetchRefused("url_not_allowed", f"The host {hostname} resolves to a non-public address")
        addresses.append(str(address))
    if not addresses:
        raise FetchRefused("url_unresolvable", f"The host {hostname} does not resolve")
    return addresses


# ── Fetch ────────────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects to the caller so each hop is re-validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


_opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler())
_opener.addheaders = []  # no default Python UA


def _request(url: str, method: str, timeout: float) -> Tuple[int, Dict[str, str], object]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
    )
    try:
        response = _opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # 3xx arrives here because redirects are not followed; 4xx/5xx too.
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            raise FetchRefused("fetch_timeout", "The page did not respond in time") from exc
        raise FetchRefused("fetch_failed", f"The page could not be fetched: {reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise FetchRefused("fetch_timeout", "The page did not respond in time") from exc
    return response.status, {k.lower(): v for k, v in response.headers.items()}, response


def _read_bounded(stream, max_bytes: int, deadline: float) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise FetchRefused("fetch_timeout", "The page did not finish downloading in time")
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FetchRefused("fetch_too_large", f"The page is larger than {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_html(
    url: str,
    *,
    allow_list: Sequence[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    deadline_s: float = DEFAULT_DEADLINE_S,
    max_redirects: int = MAX_REDIRECTS,
) -> FetchResult:
    """Fetch one HTML page under the guard. Raises ``FetchRefused`` for every refusal class."""
    started = time.monotonic()
    deadline = started + deadline_s
    current = validate_url(url)
    redirects = 0
    while True:
        hostname = urlsplit(current).hostname or ""
        if not host_allowed(hostname, allow_list):
            raise FetchRefused("url_not_allowed", f"The host {hostname} is not on the allow-list")
        resolve_public_addresses(hostname)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchRefused("fetch_timeout", "The page did not respond in time")
        status, headers, response = _request(current, "GET", remaining)

        if status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                raise FetchRefused("fetch_failed", "Redirect without a Location header", upstream_status=status)
            redirects += 1
            if redirects > max_redirects:
                raise FetchRefused("fetch_failed", f"More than {max_redirects} redirects", upstream_status=status)
            current = validate_url(urllib.parse.urljoin(current, location))  # re-validated: https, host, port
            continue

        if status != 200:
            raise FetchRefused("fetch_failed", f"The page answered HTTP {status}", upstream_status=status)

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in ("text/html", "application/xhtml+xml"):
            raise FetchRefused("fetch_not_html", f"The page is {content_type or 'of unknown type'}, not HTML")

        body = _read_bounded(response, max_bytes, deadline)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            status=status,
            final_url=current,
            headers=headers,
            body=body,
            wall_clock_ms=elapsed_ms,
            redirects=redirects,
        )


def probe_size(url: str, *, allow_list: Sequence[str] = (), deadline_s: float) -> ProbeResult:
    """``HEAD`` one same-origin subresource under the same guard; returns its ``Content-Length``."""
    started = time.monotonic()
    current = validate_url(url)
    hostname = urlsplit(current).hostname or ""
    if not host_allowed(hostname, allow_list):
        raise FetchRefused("url_not_allowed", f"The host {hostname} is not on the allow-list")
    resolve_public_addresses(hostname)
    status, headers, _ = _request(current, "HEAD", deadline_s)
    length = headers.get("content-length")
    content_length = int(length) if length and length.isdigit() else None
    return ProbeResult(
        status=status, content_length=content_length, wall_clock_ms=int((time.monotonic() - started) * 1000)
    )


def same_origin(url: str, other: str) -> bool:
    a, b = urlsplit(url), urlsplit(other)
    return (a.scheme, (a.hostname or "").lower(), a.port) == (b.scheme, (b.hostname or "").lower(), b.port)


def unique(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
