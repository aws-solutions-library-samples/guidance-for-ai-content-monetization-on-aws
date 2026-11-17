"""Canonical URL normalization and safe Markdown link resolution."""

import html
import ipaddress
import posixpath
import re
from typing import Optional
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_PATH_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def normalize_canonical_url(value: object) -> Optional[str]:
    """Return a deterministic HTTP(S) canonical URL or ``None`` if unsafe."""
    if not isinstance(value, str):
        return None
    candidate = html.unescape(value).strip()
    if not candidate or _CONTROL_CHARACTERS.search(candidate):
        return None

    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        authority = _normalized_authority(parsed)
        path = _normalized_path(parsed.path or "/", reject_encoded_traversal=True)
    except (UnicodeError, ValueError):
        return None

    return urlunsplit((parsed.scheme.lower(), authority, path, "", ""))


def resolve_safe_link(target: object, canonical_url: Optional[str]) -> Optional[str]:
    """Resolve an allowed link target, returning ``None`` for unsafe links."""
    if not isinstance(target, str):
        return None
    candidate = html.unescape(target).strip()
    if not candidate or _CONTROL_CHARACTERS.search(candidate):
        return None

    try:
        parsed_candidate = urlsplit(candidate)
    except ValueError:
        return None

    if parsed_candidate.scheme.lower() == "mailto":
        return _normalize_mailto(parsed_candidate)
    if parsed_candidate.scheme and parsed_candidate.scheme.lower() not in _ALLOWED_LINK_SCHEMES:
        return None

    if not parsed_candidate.scheme:
        base = normalize_canonical_url(canonical_url)
        if base is None:
            return None
        candidate = urljoin(base, candidate)

    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        authority = _normalized_authority(parsed)
        path = _normalized_path(parsed.path or "/", reject_encoded_traversal=True)
        query = _normalized_percent_escapes(parsed.query)
        fragment = _normalized_percent_escapes(parsed.fragment)
    except (UnicodeError, ValueError):
        return None

    return urlunsplit((parsed.scheme.lower(), authority, path, query, fragment))


def _normalized_authority(parsed) -> str:
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL hostname is required")

    try:
        ip = ipaddress.ip_address(hostname)
        normalized_host = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    except ValueError:
        normalized_host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not normalized_host:
            raise ValueError("URL hostname is required")

    port = parsed.port
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        return f"{normalized_host}:{port}"
    return normalized_host


def _normalized_path(path: str, *, reject_encoded_traversal: bool) -> str:
    if _CONTROL_CHARACTERS.search(path) or _INVALID_PERCENT_ESCAPE.search(path):
        raise ValueError("Invalid URL path")
    if "\\" in path:
        raise ValueError("Backslashes are not valid URL path separators")
    if reject_encoded_traversal:
        if _ENCODED_PATH_SEPARATOR.search(path):
            raise ValueError("Encoded path traversal is not allowed")
        for segment in path.split("/"):
            if "%" not in segment:
                continue
            decoded = segment
            for _ in range(2):
                decoded = unquote(decoded)
                if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
                    raise ValueError("Encoded path traversal is not allowed")

    trailing_slash = path.endswith("/")
    normalized = posixpath.normpath(re.sub(r"/+", "/", path))
    if normalized in {"", "."}:
        normalized = "/"
    elif not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if trailing_slash and normalized != "/":
        normalized += "/"
    return _normalized_percent_escapes(normalized)


def _normalized_percent_escapes(value: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(value):
        raise ValueError("Invalid percent escape")
    return _PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), value)


def _normalize_mailto(parsed) -> Optional[str]:
    if parsed.netloc or not parsed.path or _CONTROL_CHARACTERS.search(parsed.path):
        return None
    try:
        query = _normalized_percent_escapes(parsed.query)
        fragment = _normalized_percent_escapes(parsed.fragment)
    except ValueError:
        return None
    return urlunsplit(("mailto", "", parsed.path, query, fragment))
