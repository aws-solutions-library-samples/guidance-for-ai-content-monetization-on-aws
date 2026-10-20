"""Deterministic native-edge addressing tests."""

import hashlib
import json
from pathlib import Path

import pytest

from shared.utils.edge_address import (
    edge_address_from_canonical_url,
    edge_uri_from_host_path,
    legacy_markdown_key,
)

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "edge"
        / "addressing-vectors-v1.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    ("source", "canonical", "key"),
    [
        ("https://News.Example.com", "https://news.example.com/", "content/news.example.com/index.md"),
        ("https://news.example.com/a/b?q=1#x", "https://news.example.com/a/b", "content/news.example.com/a/b.md"),
        ("https://news.example.com/a/b/", "https://news.example.com/a/b/", "content/news.example.com/a/b/index.md"),
        (
            "https://news.example.com/a/file.html",
            "https://news.example.com/a/file.html",
            "content/news.example.com/a/file.html.md",
        ),
        (
            "https://www.Exämple.com/über/",
            "https://www.xn--exmple-cua.com/%C3%BCber/",
            "content/www.xn--exmple-cua.com/%C3%BCber/index.md",
        ),
    ],
)
def test_edge_address_mapping(source, canonical, key):
    address = edge_address_from_canonical_url(source)

    assert address.eligible is True
    assert address.canonical_url == canonical
    assert address.url_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert address.s3_key == key
    assert address.public_uri == f"/{key}"
    assert address.reason is None


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (None, "missing_or_invalid_canonical_url"),
        ("https://example.com:8443/a", "nonstandard_port"),
        ("http://example.com/a", "non_https_scheme"),
        ("https://127.0.0.1/a", "ip_host"),
        ("https://[2001:db8::1]/a", "ip_host"),
        ("https://bad_host.example/a", "invalid_dns_host"),
        ("https://example.com/%2e%2e/private", "missing_or_invalid_canonical_url"),
    ],
)
def test_ineligible_addresses_are_explicit(source, reason):
    address = edge_address_from_canonical_url(source)

    assert address.eligible is False
    assert address.s3_key is None
    assert address.public_uri is None
    assert address.reason == reason


def test_http_is_ineligible_so_scheme_cannot_collide_at_the_edge():
    http = edge_address_from_canonical_url("http://example.com/a")
    https = edge_address_from_canonical_url("https://example.com/a")

    assert http.eligible is False
    assert http.s3_key is None
    assert https.eligible is True
    assert http.url_hash != https.url_hash


def test_legacy_key_is_opaque_and_stable():
    assert legacy_markdown_key("cnt_abc123") == "content/cnt_abc123.md"


@pytest.mark.parametrize(
    "vector",
    VECTORS["vectors"],
    ids=lambda vector: vector["name"],
)
def test_python_and_cloudfront_share_addressing_vectors(vector):
    assert edge_uri_from_host_path(vector["host"], vector["uri"]) == vector["expected_uri"]
    if vector["expected_uri"] is not None:
        address = edge_address_from_canonical_url(
            f"https://{vector['host']}{vector['uri']}"
        )
        assert address.public_uri == vector["expected_uri"]


def test_encoded_unreserved_characters_match_the_router_key():
    address = edge_address_from_canonical_url("https://news.example.com/caf%65")
    assert address.canonical_url == "https://news.example.com/cafe"
    assert address.s3_key == "content/news.example.com/cafe.md"


def test_equivalent_percent_encoding_has_one_canonical_identity():
    encoded = edge_address_from_canonical_url("https://news.example.com/caf%65")
    literal = edge_address_from_canonical_url("https://news.example.com/cafe")

    assert encoded == literal
