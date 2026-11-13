"""CoMP discovery normalization and Package construction tests."""

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ["LICENSES_TABLE_NAME"] = "cm-licenses"
os.environ.setdefault("PUBLISHER_DOMAIN", "characterization-publisher.example")

from services import comp_query_service as service
from services.search_service import DiscoveryRequest, SearchUnavailableError

API_BASE_URL = "https://api.characterization.example"


@pytest.fixture
def licenses_table():
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="cm-licenses",
            KeySchema=[
                {"AttributeName": "license_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "license_id", "AttributeType": "S"},
                {"AttributeName": "version", "AttributeType": "N"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        table.put_item(
            Item={
                "license_id": "lic_1",
                "version": 1,
                "status": "active",
                "allowed_uses": [3],
                "allowed_use_type": 0,
            }
        )
        service.get_licenses_table._table = table
        yield table
        if hasattr(service.get_licenses_table, "_table"):
            del service.get_licenses_table._table


def _request(scope=0, discovery=None, uris=None):
    aisysuse = {"lid": "lic_1", "aiauth": 2, "scope": scope, "function": [3]}
    if discovery is not None:
        aisysuse["ext"] = {"discovery": discovery}
    if uris is not None:
        aisysuse["uri"] = uris
    return {"aisystem": {"name": "agent.example", "aisysuse": aisysuse}}


@pytest.mark.parametrize(
    ("scope", "discovery", "uris", "expected"),
    [
        (0, None, None, {"query_text": "", "limit": 10}),
        (1, None, ["https://News.Example/health/", "https://finance.example/markets"], {"pairs": 2}),
        (2, {"date_range": {"start_date": "2026-01-01T00:00:00Z"}}, None, {"start": True}),
        (3, {"cat": ["111"]}, None, {"categories": ["111"]}),
        (4, {"query": "oncology"}, None, {"query_text": "oncology"}),
        (5, {"content_ids": ["cnt_1"]}, None, {"ids": ["cnt_1"]}),
        (6, {"topics": ["Cancer"]}, None, {"topics": ["Cancer"]}),
    ],
)
def test_scope_codes_build_real_discovery_requests(scope, discovery, uris, expected):
    request = service.build_comp_discovery_request(_request(scope, discovery, uris)["aisystem"]["aisysuse"])

    for field, value in expected.items():
        if field == "pairs":
            assert len(request.section_pairs) == value
        elif field == "start":
            assert bool(request.publish_date_start) is value
        elif field == "categories":
            assert request.iab_category_ids == value
        elif field == "ids":
            assert request.content_ids == value
        else:
            assert getattr(request, field) == value


@pytest.mark.parametrize(
    "scope,discovery",
    [
        (1, None),
        (2, {}),
        (3, {}),
        (4, {}),
        (5, {}),
        (6, {}),
    ],
)
def test_incomplete_scopes_fail_before_search(scope, discovery):
    with pytest.raises(service.CompRequestValidationError):
        service.build_comp_discovery_request(_request(scope, discovery)["aisystem"]["aisysuse"])


def test_scope_one_rejects_invalid_uri_and_does_not_cross_match_pairs():
    with pytest.raises(service.CompRequestValidationError):
        service.build_comp_discovery_request(
            _request(1, None, ["javascript:alert(1)"])["aisystem"]["aisysuse"]
        )

    request = service.build_comp_discovery_request(
        _request(1, None, ["https://one.example/health", "https://two.example/finance"])["aisystem"]["aisysuse"]
    )
    assert [(pair.canonical_host, pair.canonical_path_ancestor) for pair in request.section_pairs] == [
        ("one.example", "/health"),
        ("two.example", "/finance"),
    ]


def test_package_retrieval_describes_the_content_read_contract(licenses_table):
    result = [
        {
            "content_id": "cnt_1",
            "title": "Result",
            "content_type": "article",
            "iab_categories": [{"id": "111", "name": "Medical Health"}],
            "word_count": 10,
            "publication_date": "2026-01-01T00:00:00Z",
            "markdown_schema_version": "1.0",
        }
    ]
    search = Mock(return_value=result)
    request = _request(4, {"query": "oncology"})
    frozen_uuid = uuid.UUID(int=1)
    frozen_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fake_datetime = Mock(wraps=datetime)
    fake_datetime.now.return_value = frozen_time
    with patch("uuid.uuid4", return_value=frozen_uuid), patch.object(service, "datetime", fake_datetime):
        query_response, query_error = service.handle_comp_query_request(
            request, {}, "partner_key", search, API_BASE_URL
        )

    assert query_error is None
    # The read contract, not a rediscovery loop: GET /content by api_key (1), delivered as an
    # API (2). Each Text asset carries its own read reference; /search is deleted.
    assert query_response["package"]["retrieval"] == {
        "auth": 1,
        "endpoint": f"{API_BASE_URL}/content",
        "type": [2],
        "ext": {
            "read_by_url": f"{API_BASE_URL}/content?url={{canonical_url}}",
            "read_by_id": f"{API_BASE_URL}/content?content_id={{content_id}}",
        },
    }
    assert query_response["package"]["scope"]["text"][0]["ext"] == {
        "content_id": "cnt_1",
        "url": f"{API_BASE_URL}/content?content_id=cnt_1",
    }
    assert isinstance(search.call_args.args[0], DiscoveryRequest)
    assert not hasattr(service, "handle_comp_search_request")


def test_pending_license_package_points_at_the_content_read_contract(licenses_table):
    response, error = service.handle_comp_query_request(
        {"aisystem": {"name": "agent", "aisysuse": {"lid": "PENDING_REVIEW", "aiauth": 2, "scope": 0}}},
        {},
        "partner_key",
        Mock(side_effect=AssertionError("pending licenses never search")),
        API_BASE_URL,
    )
    assert error is None
    # No assets until a licence exists; retrieval names the read a licensed caller would use.
    assert "text" not in response["package"]["scope"]
    assert response["package"]["retrieval"]["endpoint"] == f"{API_BASE_URL}/content"
    assert response["package"]["retrieval"]["auth"] == 1
    assert "/query" not in json.dumps(response["package"]["retrieval"])


def test_search_unavailable_propagates_to_handler_boundary(licenses_table):
    with pytest.raises(SearchUnavailableError):
        service.handle_comp_query_request(
            _request(4, {"query": "oncology"}),
            {},
            "partner_key",
            Mock(side_effect=SearchUnavailableError("unavailable")),
            API_BASE_URL,
        )


def test_comp_handler_shim_imports_and_reexports_only_live_symbols():
    """The compatibility shim must stay importable after handlers are deleted."""
    import importlib

    shim = importlib.import_module("partner_api.comp_handler")
    missing = [name for name in shim.__all__ if not hasattr(shim, name)]
    assert not missing, missing
    assert "handle_comp_search_request" not in shim.__all__


def test_cognito_package_links_every_text_asset_to_the_internal_read(licenses_table):
    results = [
        {"content_id": "cnt_url", "title": "A", "content_type": "article", "canonical_url": "https://n.example/a"},
        {"content_id": "cnt_nourl", "title": "B", "content_type": "article", "canonical_url": None},
    ]
    response, error = service.handle_comp_query_request(
        _request(4, {"query": "oncology"}), {}, "cognito", Mock(return_value=results), API_BASE_URL
    )

    assert error is None
    assert [asset["ext"] for asset in response["package"]["scope"]["text"]] == [
        {"content_id": "cnt_url", "url": f"{API_BASE_URL}/content/cnt_url"},
        {"content_id": "cnt_nourl", "url": f"{API_BASE_URL}/content/cnt_nourl"},
    ]
    assert response["package"]["retrieval"]["auth"] == 2
    assert response["package"]["retrieval"]["endpoint"] == f"{API_BASE_URL}/content"
