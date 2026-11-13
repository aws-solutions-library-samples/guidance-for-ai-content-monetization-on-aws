"""Partner API routing and OpenSearch discovery behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest

from partner_api.handler import (
    handle_query_request,
    lambda_handler,
)
from services.search_service import DiscoveryRequest, SearchUnavailableError
from opensearchpy.exceptions import TransportError
from shared.utils import response_validator


def _user():
    return {"partner_id": "partner_1", "name": "Partner"}


def _gateway_event(**extra):
    """A minimal API Gateway event: the gateway always sets the serving domain and stage."""
    return {"requestContext": {"domainName": "api.example", "stage": "v1"}, **extra}


def _unwrapped(function):
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


def test_lambda_handler_routes_query_and_search():
    context = MagicMock(aws_request_id="request")
    with patch("partner_api.handler.handle_query_request", return_value={"statusCode": 200}) as query:
        result = lambda_handler(
            {"httpMethod": "POST", "path": "/query", "headers": {}, "body": '{"query":"test"}'},
            context,
        )
    assert result["statusCode"] == 200
    query.assert_called_once()

    # /search is deleted (no tombstone): the handler's unknown-route branch answers it and no
    # discovery runs.
    with (
        patch("partner_api.handler.search_service") as search_service,
        patch("partner_api.handler.get_analytics_collector") as analytics,
    ):
        result = lambda_handler(
            {"httpMethod": "POST", "path": "/search", "headers": {}, "body": '{"query":"test"}'},
            context,
        )
    assert result["statusCode"] == 404
    assert json.loads(result["body"])["error"] == "Endpoint not found"
    search_service.execute_discovery.assert_not_called()
    analytics.assert_not_called()


@patch("partner_api.handler.get_analytics_collector")
@patch("partner_api.handler.search_content")
def test_standard_query_builds_open_search_request(search_content, get_analytics):
    search_content.return_value = [{"content_id": "cnt_1", "content_type": "article"}]
    get_analytics.return_value = MagicMock()

    result = _unwrapped(handle_query_request)(
        {},
        {
            "query": "health",
            "content_type": ["article"],
            "iab_categories": [111],
            "max_results": 20,
            "format": "markdown",
        },
        _gateway_event(),
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 200
    search_content.assert_called_once_with("health", ["article"], ["111"], 20, None)
    body = json.loads(result["body"])
    assert body["metadata"]["total_results_available"] == 1
    assert body["results"][0]["url"] == "https://api.example/v1/content?content_id=cnt_1"


@patch("partner_api.handler.get_analytics_collector")
def test_standard_query_rejects_invalid_max_results_without_analytics(get_analytics):
    result = _unwrapped(handle_query_request)(
        {},
        {"query": "health", "max_results": "10"},
        _gateway_event(),
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 400
    assert "max_results" in json.loads(result["body"])["error"]
    get_analytics.assert_not_called()


@patch("partner_api.handler.handle_comp_query_request")
def test_query_detects_comp_before_standard_validation(comp_query):
    comp_query.return_value = ({"package": {"id": "PKG"}}, None)
    result = _unwrapped(handle_query_request)(
        {},
        {"aisystem": {"name": "agent", "aisysuse": {"lid": "lic", "aiauth": 2}}},
        _gateway_event(headers={"Host": "api.example"}),
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 200
    comp_query.assert_called_once()


@patch("partner_api.handler.handle_comp_query_request")
def test_comp_query_base_url_comes_from_the_request_context_not_the_host_header(comp_query):
    comp_query.return_value = ({"package": {"id": "PKG"}}, None)
    result = _unwrapped(handle_query_request)(
        {},
        {"aisystem": {"name": "agent", "aisysuse": {"lid": "lic", "aiauth": 2}}},
        {
            "headers": {"Host": "attacker.example"},
            "requestContext": {"domainName": "api.example", "stage": "v1"},
        },
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 200
    assert comp_query.call_args.kwargs["api_base_url"] == "https://api.example/v1"


@patch("partner_api.handler.get_analytics_collector")
@patch("partner_api.handler.search_content", side_effect=SearchUnavailableError("unavailable"))
def test_query_returns_typed_503_without_success_analytics(_search, get_analytics):
    result = _unwrapped(handle_query_request)(
        {},
        {"query": "health"},
        _gateway_event(),
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 503
    assert result["headers"]["Retry-After"] == "3"
    assert json.loads(result["body"])["error_code"] == "search_unavailable"
    get_analytics.assert_not_called()


@pytest.mark.parametrize("handler", [handle_query_request])
@pytest.mark.parametrize("comp", [False, True])
@pytest.mark.parametrize("error", [KeyError("sensitive-payload"), TypeError("sensitive-payload")])
def test_discovery_bugs_return_sanitized_500(handler, comp, error, caplog):
    body = {"query": "health"}
    if comp:
        body = {"aisystem": {"name": "agent", "aisysuse": {"lid": "lic", "aiauth": 2}}}
    with (
        patch("services.comp_query_service.validate_comp_license", return_value=(True, None, {})),
        patch("partner_api.handler.search_service.execute_discovery", side_effect=error),
        patch("partner_api.handler.get_analytics_collector") as analytics,
    ):
        response = _unwrapped(handler)({}, body, _gateway_event(), authenticated_user=_user(), auth_type="partner_key")

    assert response["statusCode"] == 500
    assert "Retry-After" not in response["headers"]
    assert "sensitive-payload" not in response["body"]
    assert "sensitive-payload" not in caplog.text
    assert type(error).__name__ in caplog.text
    analytics.assert_not_called()


@pytest.mark.parametrize("path", ["/query"])
@pytest.mark.parametrize("comp", [False, True])
@pytest.mark.parametrize(
    "error,status",
    [(TransportError(503, "sensitive-backend-payload"), 503), (TypeError("sensitive-backend-payload"), 500)],
)
def test_routed_errors_preserve_status_with_strict_validation(monkeypatch, path, comp, error, status):
    validator = response_validator.APIResponseValidator()
    validator.validation_enabled = True
    validator.validation_mode = "strict"
    monkeypatch.setattr(response_validator, "_validator", validator)
    body = {"query": "health"}
    if comp:
        body = {"aisystem": {"name": "agent", "aisysuse": {"lid": "lic", "aiauth": 2}}}
    with (
        patch("shared.auth.authentication_decorators.extract_cognito_user_context", return_value={"user_id": "test"}),
        patch("services.comp_query_service.validate_comp_license", return_value=(True, None, {})),
        patch("services.search_service.generate_search_embedding", return_value={"success": True, "embedding": [0.1]}),
        patch("services.search_service.hybrid_search", side_effect=error),
        patch("services.search_service.filter_search", side_effect=error),
        patch("partner_api.handler.get_analytics_collector") as analytics,
    ):
        response = lambda_handler(
            _gateway_event(path=path, httpMethod="POST", headers={}, body=json.dumps(body)),
            MagicMock(aws_request_id="test"),
        )
    assert response["statusCode"] == status
    data = json.loads(response["body"])
    assert data["status_code"] == status
    assert isinstance(data["error"], str)
    assert "sensitive-backend-payload" not in response["body"]
    if status == 503:
        assert data["error_code"] == "search_unavailable"
        assert response["headers"]["Retry-After"] == "3"
    else:
        assert "error_code" not in data
        assert "Retry-After" not in response["headers"]
    analytics.assert_not_called()


class TestPartnerApiUrl:
    """The partner plane URL is one fact (Q8): env first, then the request that reached us."""

    def test_info_advertises_absolute_query_endpoint_from_env(self, monkeypatch):
        from partner_api.handler import handle_info_request

        monkeypatch.setenv("PARTNER_API_URL", "https://partner.example/v1")
        response = handle_info_request({}, {}, {"httpMethod": "GET", "path": "/info"})
        body = json.loads(response["body"])
        assert body["content"]["query_endpoint"] == "https://partner.example/v1/query"
        assert body["authentication"] == {
            "method": "api_key",
            "header": "x-api-key",
            "registration_url": body["authentication"]["registration_url"],
        }

    def test_info_falls_back_to_request_host_and_stage(self, monkeypatch):
        from partner_api.handler import handle_info_request

        monkeypatch.delenv("PARTNER_API_URL", raising=False)
        event = {
            "httpMethod": "GET",
            "path": "/info",
            "headers": {"Host": "xyz.execute-api.us-west-2.amazonaws.com"},
            "requestContext": {"stage": "v1"},
        }
        body = json.loads(handle_info_request({}, {}, event)["body"])
        assert body["content"]["query_endpoint"] == "https://xyz.execute-api.us-west-2.amazonaws.com/v1/query"

    def test_helper_contract(self, monkeypatch):
        from shared.utils.partner_api_url import partner_api_base_url, partner_api_link

        monkeypatch.delenv("PARTNER_API_URL", raising=False)
        assert partner_api_base_url() is None
        assert partner_api_link("/query") == "/query"
        monkeypatch.setenv("PARTNER_API_URL", "https://p.example/v1/")
        assert partner_api_base_url() == "https://p.example/v1"
        assert partner_api_link("/info", {"headers": {"Host": "ignored"}}) == "https://p.example/v1/info"


# --------------------------------------------------------------------------- #
# Read references on standard /query results
# --------------------------------------------------------------------------- #
_RESULTS = [
    {"content_id": "cnt_url", "content_type": "article", "canonical_url": "https://news.example.com/a"},
    {"content_id": "cnt_nourl", "content_type": "article", "canonical_url": None},
    {"content_id": "cnt_inline", "content_type": "article", "canonical_url": None, "content": "short body"},
]


@pytest.mark.parametrize(
    "auth_type,expected",
    [
        (
            "partner_key",
            [
                "https://api.example/v1/content?url=https%3A%2F%2Fnews.example.com%2Fa",
                "https://api.example/v1/content?content_id=cnt_nourl",
                "https://api.example/v1/content?content_id=cnt_inline",
            ],
        ),
        (
            "cognito",
            [
                "https://api.example/v1/content/cnt_url",
                "https://api.example/v1/content/cnt_nourl",
                "https://api.example/v1/content/cnt_inline",
            ],
        ),
    ],
)
@patch("partner_api.handler.get_analytics_collector")
@patch("partner_api.handler.search_content")
def test_every_standard_result_carries_a_read_reference_for_its_plane(
    search_content, get_analytics, monkeypatch, auth_type, expected
):
    monkeypatch.delenv("PARTNER_API_URL", raising=False)
    monkeypatch.delenv("PARTNER_API_URL_PARAMETER", raising=False)
    search_content.return_value = [dict(result) for result in _RESULTS]
    get_analytics.return_value = MagicMock()

    result = _unwrapped(handle_query_request)(
        {},
        {"query": "health"},
        _gateway_event(headers={"Host": "attacker.example"}),
        authenticated_user=_user(),
        auth_type=auth_type,
    )

    assert result["statusCode"] == 200
    results = json.loads(result["body"])["results"]
    assert [item["url"] for item in results] == expected
    assert all(item["content_id"] for item in results)
    # Inline content is additional data, never a replacement for the reference.
    assert results[2]["content"] == "short body"


@patch("partner_api.handler.get_analytics_collector")
@patch("partner_api.handler.search_content")
def test_query_without_a_trusted_origin_fails_closed_before_discovery(search_content, get_analytics):
    result = _unwrapped(handle_query_request)(
        {},
        {"query": "health"},
        {"headers": {"Host": "attacker.example"}},
        authenticated_user=_user(),
        auth_type="partner_key",
    )

    assert result["statusCode"] == 500
    search_content.assert_not_called()
    get_analytics.assert_not_called()
