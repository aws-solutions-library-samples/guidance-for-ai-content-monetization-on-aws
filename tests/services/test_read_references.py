"""Unit tests for the ``/query`` read-reference builder.

Pins every row of the reference table (plane x canonical URL), the plane mapping of each
``auth_type``, the trusted-origin rule (configured partner URL, else API Gateway's request
context, never the ``Host`` header), structured encoding, and the CoMP ``retrieval`` object.
"""

import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.read_references import (  # noqa: E402
    ReadContext,
    ReadReferenceError,
    plane_for,
    read_context,
    read_reference,
    retrieval_object,
)

PARTNER = ReadContext(plane="partner", base_url="https://partner.example/v1")
INTERNAL = ReadContext(plane="internal", base_url="https://internal.example/v1")


@pytest.fixture(autouse=True)
def _no_configured_partner_url(monkeypatch):
    monkeypatch.delenv("PARTNER_API_URL", raising=False)
    monkeypatch.delenv("PARTNER_API_URL_PARAMETER", raising=False)


def _event(domain="gw.example", stage="v1", host="attacker.example"):
    request_context = {"domainName": domain} if domain else {}
    if stage is not None:
        request_context["stage"] = stage
    return {"headers": {"Host": host}, "requestContext": request_context}


# --------------------------------------------------------------------------- #
# Plane mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("auth_type,plane", [("partner_key", "partner"), ("jwt", "partner"), ("cognito", "internal")])
def test_auth_type_maps_to_the_plane_that_admitted_it(auth_type, plane):
    assert plane_for(auth_type) == plane
    assert read_context(_event(), auth_type).plane == plane


@pytest.mark.parametrize("auth_type", ["", "anonymous", "PARTNER_KEY", None])
def test_unknown_auth_type_raises_rather_than_guessing(auth_type):
    with pytest.raises(ReadReferenceError):
        read_context(_event(), auth_type)


# --------------------------------------------------------------------------- #
# Trusted origin
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("auth_type", ["partner_key", "cognito"])
def test_origin_comes_from_the_request_context_never_the_host_header(auth_type):
    ctx = read_context(_event(domain="gw.example", stage="v1", host="attacker.example"), auth_type)

    assert ctx.base_url == "https://gw.example/v1"
    assert "attacker" not in ctx.base_url


@pytest.mark.parametrize("stage", [None, "$default"])
def test_default_or_absent_stage_adds_no_suffix(stage):
    assert read_context(_event(stage=stage), "cognito").base_url == "https://gw.example"


@pytest.mark.parametrize("auth_type", ["partner_key", "cognito"])
def test_missing_domain_name_raises(auth_type):
    with pytest.raises(ReadReferenceError):
        read_context(_event(domain=None), auth_type)


def test_partner_plane_prefers_the_configured_partner_url(monkeypatch):
    monkeypatch.setenv("PARTNER_API_URL", "https://custom.example/v1/")

    assert read_context(_event(), "partner_key").base_url == "https://custom.example/v1"
    # The internal plane is never redirected to the partner origin.
    assert read_context(_event(), "cognito").base_url == "https://gw.example/v1"


# --------------------------------------------------------------------------- #
# The reference table
# --------------------------------------------------------------------------- #
def test_partner_plane_with_canonical_url_reads_by_url():
    reference = read_reference(PARTNER, "cnt_1", "https://news.example.com/a")

    assert reference == "https://partner.example/v1/content?url=https%3A%2F%2Fnews.example.com%2Fa"


@pytest.mark.parametrize("canonical_url", [None, ""])
def test_partner_plane_without_canonical_url_reads_by_content_id(canonical_url):
    assert read_reference(PARTNER, "cnt_1", canonical_url) == "https://partner.example/v1/content?content_id=cnt_1"


@pytest.mark.parametrize("canonical_url", [None, "https://news.example.com/a"])
def test_internal_plane_always_reads_by_content_id_path(canonical_url):
    assert read_reference(INTERNAL, "cnt_1", canonical_url) == "https://internal.example/v1/content/cnt_1"


def test_empty_content_id_raises():
    with pytest.raises(ReadReferenceError):
        read_reference(PARTNER, "", None)


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "canonical_url",
    [
        "https://news.example.com/a?b=1&c=2#frag",
        "https://news.example.com/path with spaces/é",
        "https://news.example.com/a+b%2Fc",
    ],
)
def test_canonical_url_round_trips_as_one_query_value(canonical_url):
    reference = read_reference(PARTNER, "cnt_1", canonical_url)
    query = parse_qs(urlsplit(reference).query, keep_blank_values=True)

    assert query == {"url": [canonical_url]}


def test_content_id_is_path_quoted_on_the_internal_plane():
    assert read_reference(INTERNAL, "a/b?c", None) == "https://internal.example/v1/content/a%2Fb%3Fc"


@pytest.mark.parametrize("ctx", [PARTNER, INTERNAL])
@pytest.mark.parametrize("canonical_url", [None, "https://news.example.com/a"])
def test_no_reference_exposes_storage_or_edge_addresses(ctx, canonical_url):
    reference = read_reference(ctx, "cnt_1", canonical_url)

    assert reference.startswith(ctx.base_url + "/content")
    for leak in ("amazonaws.com/content/", "s3", ".md", "md_s3_key", "cloudfront"):
        assert leak not in reference.replace(ctx.base_url, "")


# --------------------------------------------------------------------------- #
# CoMP Package.retrieval
# --------------------------------------------------------------------------- #
def test_partner_retrieval_names_both_read_forms_with_api_key_auth():
    assert retrieval_object(PARTNER) == {
        "auth": 1,
        "endpoint": "https://partner.example/v1/content",
        "type": [2],
        "ext": {
            "read_by_url": "https://partner.example/v1/content?url={canonical_url}",
            "read_by_id": "https://partner.example/v1/content?content_id={content_id}",
        },
    }


def test_internal_retrieval_names_the_cognito_read_with_oauth2_auth():
    assert retrieval_object(INTERNAL) == {
        "auth": 2,
        "endpoint": "https://internal.example/v1/content",
        "type": [2],
        "ext": {"read_by_id": "https://internal.example/v1/content/{content_id}"},
    }
