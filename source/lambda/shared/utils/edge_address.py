"""Deterministic canonical URL addressing for native edge Markdown delivery."""

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote_to_bytes, urlsplit, urlunsplit

from shared.utils.canonical_url import normalize_canonical_url

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PATH_SAFE = "/:@-._~!$&'()*+,;=%"
_EDGE_SEGMENT = re.compile(r"^(?:[A-Za-z0-9\-._~!$&'()*+,;=:@]|%[0-9A-F]{2})+$")
_UNRESERVED_BYTE = re.compile(r"^[A-Za-z0-9\-._~]$")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class EdgeAddress:
    """The ingest-time result of evaluating a canonical URL for Shape 1."""

    canonical_url: Optional[str]
    url_hash: Optional[str]
    s3_key: Optional[str]
    public_uri: Optional[str]
    eligible: bool
    reason: Optional[str] = None


def edge_address_from_canonical_url(value: object) -> EdgeAddress:
    """Return the deterministic edge address, or an explicit ineligibility result."""
    canonical_url = normalize_canonical_url(value)
    if canonical_url is None:
        return EdgeAddress(None, None, None, None, False, "missing_or_invalid_canonical_url")

    parsed = urlsplit(canonical_url)
    url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    hostname = parsed.hostname or ""

    if parsed.scheme != "https":
        return EdgeAddress(canonical_url, url_hash, None, None, False, "non_https_scheme")
    if parsed.port is not None:
        return EdgeAddress(canonical_url, url_hash, None, None, False, "nonstandard_port")
    if _is_ip_address(hostname):
        return EdgeAddress(canonical_url, url_hash, None, None, False, "ip_host")
    if not _is_dns_hostname(hostname):
        return EdgeAddress(canonical_url, url_hash, None, None, False, "invalid_dns_host")

    encoded_path = _normalize_edge_path(
        quote(parsed.path or "/", safe=_PATH_SAFE),
        strict_canonical=False,
    )
    if encoded_path is None:
        return EdgeAddress(canonical_url, url_hash, None, None, False, "invalid_edge_path")
    canonical_url = urlunsplit(("https", hostname, encoded_path, "", ""))
    url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    suffix = _markdown_suffix(encoded_path)
    s3_key = f"content/{hostname}/{suffix}"
    return EdgeAddress(canonical_url, url_hash, s3_key, f"/{s3_key}", True)


def legacy_markdown_key(content_id: str) -> str:
    """Return the opaque-key fallback used by content without an edge address."""
    return f"content/{content_id}.md"


def edge_uri_from_host_path(host: object, path: object) -> Optional[str]:
    """Apply the strict publisher-edge Shape 1 contract to a host and URI."""
    normalized_host = _normalize_edge_host(host)
    normalized_path = _normalize_edge_path(path, strict_canonical=True)
    if normalized_host is None or normalized_path is None:
        return None
    target = f"/content/{normalized_host}/{_markdown_suffix(normalized_path)}"
    return target if len(target.encode("utf-8")) <= 1025 else None


def _markdown_suffix(path: str) -> str:
    if path == "/":
        return "index.md"
    relative = path.lstrip("/")
    if path.endswith("/"):
        return f"{relative}index.md"
    return f"{relative}.md"


def _is_ip_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _is_dns_hostname(hostname: str) -> bool:
    if not hostname or len(hostname) > 253:
        return False
    labels = hostname.split(".")
    return len(labels) >= 2 and all(_DNS_LABEL.fullmatch(label) for label in labels)


def _normalize_edge_host(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if value.lower().endswith(":443"):
        value = value[:-4]
    if value.endswith("."):
        value = value[:-1]
    if len(value) > 253 or not value.isascii() or re.search(r"[:\\/@%]", value):
        return None
    host = value.lower()
    if _is_ip_address(host) or not _is_dns_hostname(host):
        return None
    return host


def _normalize_edge_path(value: object, *, strict_canonical: bool) -> Optional[str]:
    if not isinstance(value, str) or not value or len(value) > 900:
        return None
    if not value.startswith("/") or "\\" in value or "?" in value or "#" in value:
        return None
    if strict_canonical and not value.isascii():
        return None
    if value == "/":
        return "/"

    directory_path = value.endswith("/")
    raw_segments = value[1:-1].split("/") if directory_path else value[1:].split("/")
    normalized_segments = []
    for raw_segment in raw_segments:
        if not raw_segment or raw_segment == ".":
            continue
        if raw_segment == "..":
            if not normalized_segments:
                return None
            normalized_segments.pop()
            continue
        segment = _normalize_edge_segment(raw_segment)
        if segment is None or segment in {".", ".."} or not _EDGE_SEGMENT.fullmatch(segment):
            return None
        normalized_segments.append(segment)

    normalized = "/" + "/".join(normalized_segments)
    return normalized + "/" if directory_path else normalized


def _normalize_edge_segment(segment: str) -> Optional[str]:
    try:
        decoded = unquote_to_bytes(segment).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded):
        return None

    normalized = []
    cursor = 0
    for match in _PERCENT_ESCAPE.finditer(segment):
        normalized.append(segment[cursor : match.start()])
        byte = int(match.group(1), 16)
        character = chr(byte)
        if byte in {0x2F, 0x5C, 0x25, 0x3F, 0x23}:
            return None
        normalized.append(character if _UNRESERVED_BYTE.fullmatch(character) else f"%{byte:02X}")
        cursor = match.end()
    normalized.append(segment[cursor:])
    result = "".join(normalized)
    if "%" in _PERCENT_ESCAPE.sub("", segment):
        return None
    return result
