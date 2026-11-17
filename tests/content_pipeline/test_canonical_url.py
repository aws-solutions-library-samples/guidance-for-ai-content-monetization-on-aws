"""Tests for canonical URL normalization and safe Markdown links."""

import pytest

from shared.utils.canonical_url import normalize_canonical_url, resolve_safe_link


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("HTTPS://WWW.Exämple.com:443/a/../b/?q=1#top", "https://www.xn--exmple-cua.com/b/"),
        ("http://Example.COM:80", "http://example.com/"),
        ("https://example.com:8443/a//b", "https://example.com:8443/a/b"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/file%2Ehtml", "https://example.com/file%2Ehtml"),
        ("https://[2001:db8::1]:443/a", "https://[2001:db8::1]/a"),
    ],
)
def test_normalize_canonical_url(source, expected):
    assert normalize_canonical_url(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        None,
        "",
        "javascript:alert(1)",
        "ftp://example.com/file",
        "https://user:secret@example.com/a",
        "https:///missing-host",
        "https://example.com/%2e%2e/private",
        "https://example.com/%252e%252e/private",
        "https://example.com/a%2fb",
        "https://example.com/a%5Cb",
        "https://example.com/%ZZ",
    ],
)
def test_normalize_canonical_url_rejects_invalid_or_unsafe_values(source):
    assert normalize_canonical_url(source) is None


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("../guide?q=1#part", "https://example.com/articles/guide?q=1#part"),
        ("/root", "https://example.com/root"),
        ("HTTPS://Other.Example:443/a", "https://other.example/a"),
        ("mailto:editor@example.com", "mailto:editor@example.com"),
    ],
)
def test_resolve_safe_link(target, expected):
    assert resolve_safe_link(target, "https://example.com/articles/2026/item") == expected


@pytest.mark.parametrize(
    "target",
    [
        "../relative-without-base",
        "javascript:alert(1)",
        "data:text/plain,no",
        "file:///etc/passwd",
        "https://user@example.com/private",
        "/%2e%2e/private",
        "https://example.com/%ZZ",
    ],
)
def test_resolve_safe_link_rejects_unsafe_targets(target):
    assert resolve_safe_link(target, None if target.startswith("../") else "https://example.com/a/") is None
