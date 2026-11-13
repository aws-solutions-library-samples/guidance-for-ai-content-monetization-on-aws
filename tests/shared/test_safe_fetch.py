"""Unit tests for ``shared/utils/safe_fetch.py`` — the SSRF guard behind the comparison run route.

Spec: ``.kiro/specs/scrape-vs-api-comparison`` R7.2, R7.3, R13.1. One test per refusal class, so a
regression in any rule fails by name. No network: DNS and HTTP are stubbed at the module seams
(``socket.getaddrinfo`` and ``safe_fetch._request``).
"""

from __future__ import annotations

import io
import ipaddress
import socket
from typing import Dict, List, Tuple
from unittest.mock import patch

import pytest

from shared.utils import safe_fetch
from shared.utils.safe_fetch import (
    FetchRefused,
    fetch_html,
    host_allowed,
    is_public_address,
    parse_allow_list,
    probe_size,
    resolve_public_addresses,
    same_origin,
    validate_url,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


# ── helpers ──────────────────────────────────────────────────────────────────


def _addrinfo(*addresses: str):
    return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses]


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)


def _responder(script: List[Tuple[int, Dict[str, str], bytes]]):
    """Return a ``_request`` stub that replays ``script`` (one entry per hop) and records calls."""
    calls: List[Tuple[str, str]] = []

    def _request(url: str, method: str, timeout: float):
        calls.append((url, method))
        status, headers, body = script[min(len(calls) - 1, len(script) - 1)]
        return status, {k.lower(): v for k, v in headers.items()}, _FakeResponse(body)

    _request.calls = calls  # type: ignore[attr-defined]
    return _request


HTML = {"Content-Type": "text/html; charset=utf-8"}


# ── validate_url ─────────────────────────────────────────────────────────────


class TestValidateUrl:
    def test_normalises_host_case_and_drops_fragment(self):
        assert validate_url("https://Example.COM/a/b?x=1#frag") == "https://example.com/a/b?x=1"

    def test_adds_root_path(self):
        assert validate_url("https://example.com") == "https://example.com/"

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/",  # scheme
            "ftp://example.com/",
            "https://user:pw@example.com/",  # credentials
            "https://user@example.com/",
            "https://example.com:8443/",  # non-default port
            "https://10.0.0.1/",  # IP literal
            "https://[::1]/",
            "https://localhost/",  # single label is not a DNS name we accept
            "https:///path",  # no host
            "",
            "   ",
            "https://exa mple.com/",
            "https://-bad.example.com/",
        ],
    )
    def test_refuses_invalid_urls(self, url):
        with pytest.raises(FetchRefused) as exc:
            validate_url(url)
        assert exc.value.error_code == "invalid_url"

    def test_refuses_overlong_url(self):
        with pytest.raises(FetchRefused) as exc:
            validate_url("https://example.com/" + "a" * 2100)
        assert exc.value.error_code == "invalid_url"


# ── allow-list ───────────────────────────────────────────────────────────────


class TestAllowList:
    def test_parse_accepts_commas_whitespace_and_case(self):
        assert parse_allow_list("A.example.com, *.Demo.org  b.example.com.") == [
            "a.example.com",
            "*.demo.org",
            "b.example.com",
        ]
        assert parse_allow_list(None) == []
        assert parse_allow_list("") == []

    def test_empty_list_is_open_mode(self):
        assert host_allowed("anything.example", [])

    def test_exact_match_case_insensitive(self):
        assert host_allowed("Portal.Example.com", ["portal.example.com"])
        assert not host_allowed("other.example.com", ["portal.example.com"])

    def test_suffix_wildcard_matches_subdomains_only(self):
        assert host_allowed("a.example.com", ["*.example.com"])
        assert host_allowed("a.b.example.com", ["*.example.com"])
        assert not host_allowed("example.com", ["*.example.com"])
        assert not host_allowed("notexample.com", ["*.example.com"])


# ── address classification ───────────────────────────────────────────────────


class TestIsPublicAddress:
    @pytest.mark.parametrize(
        "address",
        [
            "10.0.0.1",
            "172.16.5.5",
            "192.168.1.1",  # private
            "127.0.0.1",
            "::1",  # loopback
            "169.254.1.1",
            "fe80::1",  # link-local
            "224.0.0.1",
            "ff02::1",  # multicast
            "0.0.0.0",
            "::",  # unspecified
            "100.64.0.1",  # shared address space (not global)
            "240.0.0.1",  # reserved
            "fc00::1",
            "fd12::1",  # ULA
            "::ffff:10.0.0.1",  # IPv4-mapped private
            "2002:0a00:0001::",  # 6to4 of 10.0.0.1
            "fd00:ec2::254",  # EC2 IPv6 metadata
            "100.100.100.200",  # other-cloud metadata
        ],
    )
    def test_refuses_non_public(self, address):
        assert not is_public_address(ipaddress.ip_address(address))

    def test_refuses_ipv4_metadata_endpoint(self):
        # Built from parts so the literal does not appear in the source.
        metadata = ipaddress.ip_address(".".join(["169", "254", "169", "254"]))
        assert not is_public_address(metadata)

    @pytest.mark.parametrize("address", [PUBLIC_V4, "8.8.8.8", PUBLIC_V6, "2001:4860:4860::8888"])
    def test_accepts_public(self, address):
        assert is_public_address(ipaddress.ip_address(address))


class TestResolvePublicAddresses:
    def test_returns_all_public_addresses(self):
        with patch.object(socket, "getaddrinfo", return_value=_addrinfo(PUBLIC_V4, PUBLIC_V6)):
            assert resolve_public_addresses("example.com") == [PUBLIC_V4, PUBLIC_V6]

    def test_refuses_when_any_record_is_private(self):
        with patch.object(socket, "getaddrinfo", return_value=_addrinfo(PUBLIC_V4, "10.0.0.5")):
            with pytest.raises(FetchRefused) as exc:
                resolve_public_addresses("example.com")
        assert exc.value.error_code == "url_not_allowed"

    def test_unresolvable_host(self):
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("nx")):
            with pytest.raises(FetchRefused) as exc:
                resolve_public_addresses("nx.example.com")
        assert exc.value.error_code == "url_unresolvable"

    def test_empty_resolution(self):
        with patch.object(socket, "getaddrinfo", return_value=[]):
            with pytest.raises(FetchRefused) as exc:
                resolve_public_addresses("empty.example.com")
        assert exc.value.error_code == "url_unresolvable"


# ── fetch_html ───────────────────────────────────────────────────────────────


@pytest.fixture
def public_dns():
    with patch.object(socket, "getaddrinfo", return_value=_addrinfo(PUBLIC_V4)):
        yield


class TestFetchHtml:
    def test_happy_path_returns_body_and_headers(self, public_dns):
        stub = _responder([(200, {**HTML, "Link": '<https://example.com/l>; rel="license"'}, b"<html>ok</html>")])
        with patch.object(safe_fetch, "_request", stub):
            result = fetch_html("https://example.com/article")
        assert result.status == 200
        assert result.body == b"<html>ok</html>"
        assert result.final_url == "https://example.com/article"
        assert result.headers["link"].startswith("<https://example.com/l>")
        assert result.redirects == 0
        assert stub.calls == [("https://example.com/article", "GET")]

    def test_sends_fixed_user_agent_and_no_credentials(self, public_dns):
        captured = {}

        class _Opener:
            def open(self, request, timeout):
                captured["headers"] = dict(request.header_items())
                captured["method"] = request.get_method()
                resp = _FakeResponse(b"<html/>")
                resp.status = 200  # type: ignore[attr-defined]
                resp.headers = {"Content-Type": "text/html"}  # type: ignore[attr-defined]
                return resp

        with patch.object(safe_fetch, "_opener", _Opener()):
            fetch_html("https://example.com/")
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers["user-agent"] == safe_fetch.USER_AGENT
        assert "cookie" not in headers and "authorization" not in headers
        assert captured["method"] == "GET"

    def test_follows_redirect_and_revalidates_each_hop(self, public_dns):
        stub = _responder(
            [
                (301, {"Location": "https://Example.com/new"}, b""),
                (200, HTML, b"<html>moved</html>"),
            ]
        )
        with patch.object(safe_fetch, "_request", stub):
            result = fetch_html("https://example.com/old")
        assert result.final_url == "https://example.com/new"
        assert result.redirects == 1
        assert [u for u, _ in stub.calls] == ["https://example.com/old", "https://example.com/new"]

    def test_redirect_to_http_is_refused(self, public_dns):
        stub = _responder([(302, {"Location": "http://example.com/plain"}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/")
        assert exc.value.error_code == "invalid_url"

    def test_redirect_to_private_host_is_refused(self):
        def _dns(host, *args, **kwargs):
            return _addrinfo("10.1.1.1" if host == "internal.example.com" else PUBLIC_V4)

        stub = _responder([(302, {"Location": "https://internal.example.com/"}, b"")])
        with patch.object(socket, "getaddrinfo", side_effect=_dns), patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/")
        assert exc.value.error_code == "url_not_allowed"

    def test_redirect_off_allow_list_is_refused(self, public_dns):
        stub = _responder([(302, {"Location": "https://other.example.org/"}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/", allow_list=["example.com"])
        assert exc.value.error_code == "url_not_allowed"

    def test_too_many_redirects(self, public_dns):
        stub = _responder([(302, {"Location": "https://example.com/again"}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/")
        assert exc.value.error_code == "fetch_failed"
        assert len(stub.calls) == safe_fetch.MAX_REDIRECTS + 1

    def test_redirect_without_location(self, public_dns):
        stub = _responder([(302, {}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/")
        assert exc.value.error_code == "fetch_failed"

    def test_non_200_is_fetch_failed_with_upstream_status(self, public_dns):
        stub = _responder([(404, HTML, b"nope")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/missing")
        assert exc.value.error_code == "fetch_failed"
        assert exc.value.upstream_status == 404

    def test_non_html_is_refused(self, public_dns):
        stub = _responder([(200, {"Content-Type": "application/json"}, b"{}")])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/api")
        assert exc.value.error_code == "fetch_not_html"

    def test_oversize_body_is_refused_at_the_cap(self, public_dns):
        stub = _responder([(200, HTML, b"x" * 2000)])
        with patch.object(safe_fetch, "_request", stub):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/big", max_bytes=1000)
        assert exc.value.error_code == "fetch_too_large"

    def test_timeout_during_read(self, public_dns):
        stub = _responder([(200, HTML, b"<html>slow</html>")])
        clock = iter([0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
        with (
            patch.object(safe_fetch, "_request", stub),
            patch.object(safe_fetch.time, "monotonic", side_effect=lambda: next(clock)),
        ):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/slow", deadline_s=1.0)
        assert exc.value.error_code == "fetch_timeout"

    def test_timeout_from_transport(self, public_dns):
        def _request(url, method, timeout):
            raise FetchRefused("fetch_timeout", "The page did not respond in time")

        with patch.object(safe_fetch, "_request", _request):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://example.com/")
        assert exc.value.error_code == "fetch_timeout"

    def test_host_off_allow_list_is_refused_before_dns(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not be called")):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://other.example.org/", allow_list=["portal.example.com"])
        assert exc.value.error_code == "url_not_allowed"

    def test_private_host_is_refused_before_request(self):
        with (
            patch.object(socket, "getaddrinfo", return_value=_addrinfo("192.168.0.10")),
            patch.object(safe_fetch, "_request", side_effect=AssertionError("must not connect")),
        ):
            with pytest.raises(FetchRefused) as exc:
                fetch_html("https://intranet.example.com/")
        assert exc.value.error_code == "url_not_allowed"


class TestProbeSize:
    def test_head_returns_content_length(self, public_dns):
        stub = _responder([(200, {"Content-Length": "1234"}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            probe = probe_size("https://example.com/app.js", deadline_s=2.0)
        assert probe.status == 200 and probe.content_length == 1234
        assert stub.calls == [("https://example.com/app.js", "HEAD")]

    def test_missing_length_is_none(self, public_dns):
        stub = _responder([(200, {}, b"")])
        with patch.object(safe_fetch, "_request", stub):
            assert probe_size("https://example.com/x.css", deadline_s=2.0).content_length is None

    def test_probe_respects_allow_list(self):
        with pytest.raises(FetchRefused) as exc:
            probe_size("https://cdn.example.org/x.js", allow_list=["example.com"], deadline_s=2.0)
        assert exc.value.error_code == "url_not_allowed"


class TestSameOrigin:
    def test_same_and_different_origins(self):
        assert same_origin("https://a.example.com/x", "https://A.example.com/y?z")
        assert not same_origin("https://a.example.com/x", "https://b.example.com/x")
        assert not same_origin("https://a.example.com/x", "http://a.example.com/x")
