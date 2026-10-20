"""Live execution-role checks for MR !29, not API Gateway auth acceptance.

RUN_INTEGRATION=1, AWS_REGION, and IAB_EXPECTED_ACCOUNT_ID are required.
The caller uses IAM Lambda invocation and supplies representative trusted
Gateway claims. Public partner-key acceptance remains in test_api.py (#32).
Fault injection and strict-mode errors are covered by the routed unit tests.
"""

import io
import json
import os
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import pytest
import requests
from botocore.config import Config

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def deployment():
    region = os.environ["AWS_REGION"]
    expected_account = os.environ["IAB_EXPECTED_ACCOUNT_ID"]
    session = boto3.Session(region_name=region)
    assert session.client("sts").get_caller_identity()["Account"] == expected_account
    stack = session.client("cloudformation").describe_stacks(StackName="ContentMonetizationStack")["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    client = session.client("lambda", config=Config(read_timeout=90))
    return client, outputs


def _invoke(deployment, path, body):
    client, outputs = deployment
    host = urlsplit(outputs["ApiGatewayUrl"]).netloc
    event = {
        "httpMethod": "POST",
        "path": path,
        "headers": {"Host": host},
        "requestContext": {
            "stage": "v1",
            "authorizer": {"claims": {"sub": "issue19-review-acceptance", "name": "Internal acceptance"}},
        },
        "body": json.dumps(body),
    }
    for attempt in range(3):
        invocation = client.invoke(
            FunctionName=outputs["PartnerApiLambdaName"],
            Payload=json.dumps(event).encode(),
        )
        assert "FunctionError" not in invocation
        response = json.loads(invocation["Payload"].read())
        if response["statusCode"] != 503 or attempt == 2:
            return response
        data = json.loads(response["body"])
        assert data["error_code"] == "search_unavailable"
        assert response["headers"]["Retry-After"] == "3"
        time.sleep(3)
    raise AssertionError("Unreachable invocation state")


def _download_artifact(url):
    try:
        download = requests.get(url, timeout=60)
    except requests.RequestException:
        raise RuntimeError("Unable to download the deployed Lambda artifact") from None
    assert download.status_code == 200
    return download.content


def test_deployed_partner_artifact_matches_reviewed_source(deployment):
    client, outputs = deployment
    function = client.get_function(FunctionName=outputs["PartnerApiLambdaName"])
    with zipfile.ZipFile(io.BytesIO(_download_artifact(function["Code"]["Location"]))) as artifact:
        assert "enhanced_search_handlers.py" not in artifact.namelist()
        for packaged, source in [
            ("handler.py", "partner_api/handler.py"),
            ("services/search_service.py", "services/search_service.py"),
            ("services/comp_query_service.py", "services/comp_query_service.py"),
            ("shared/bedrock_embeddings.py", "shared/bedrock_embeddings.py"),
            ("shared/utils/response_validator.py", "shared/utils/response_validator.py"),
        ]:
            assert artifact.read(packaged) == (ROOT / "source/lambda" / source).read_bytes(), packaged


def test_deployed_standard_discovery(deployment):
    response = _invoke(deployment, "/query", {"query": "electric vehicle battery cost", "max_results": 3})
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert data["results"]
    assert len(data["results"]) <= 3
    for result in data["results"]:
        assert result["content_id"]
        assert result["markdown_schema_version"]
    assert "public" not in response["headers"].get("Cache-Control", "")
    assert data["query_id"]
    assert "billing" in data


def test_deployed_validation_error_contract(deployment):
    response = _invoke(deployment, "/query", {})
    assert response["statusCode"] == 400
    data = json.loads(response["body"])
    assert data["error"] == "Query parameter is required"
    assert "message" not in data
    assert data.get("_validation", {}).get("valid", True)
    assert "Retry-After" not in response["headers"]


def test_deployed_comp_empty_selection(deployment):
    response = _invoke(
        deployment,
        "/query",
        {
            "aisystem": {
                "name": "issue19-review-acceptance",
                "aisysuse": {
                    "lid": os.environ.get("IAB_TEST_LICENSE_ID", "lic_default_v1"),
                    "aiauth": 2,
                    "scope": 5,
                    "ext": {"discovery": {"content_ids": ["issue19-review-absent-content"]}},
                },
            }
        },
    )
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert data["_meta"]["total_results"] == 0
    assert data["package"]["scope"]["text"] == []
    # Interim until the content read contract (#14): the canonical /query, delivered as an API.
    assert data["package"]["retrieval"]["endpoint"].endswith("/v1/query")
    assert data["package"]["retrieval"]["type"] == [2]


@pytest.mark.parametrize("extra", [{"limit": 10}, {"content_types": ["article"]}, {"similarity_threshold": None}])
def test_deployed_query_rejects_noncanonical_parameters(deployment, extra):
    response = _invoke(deployment, "/query", {"query": "electric vehicle", **extra})
    assert response["statusCode"] == 400
    assert next(iter(extra)) in json.loads(response["body"])["error"]


def test_deployed_filter_only_threshold_rejected_before_license_or_search(deployment):
    response = _invoke(deployment, "/query", {
        "aisystem": {"name": "issue38-acceptance", "aisysuse": {
            "lid": "PENDING_REVIEW", "aiauth": 2, "scope": 0,
            "ext": {"discovery": {"similarity_threshold": 0}},
        }},
    })
    assert response["statusCode"] == 400
    assert "explicit query text" in json.loads(response["body"])["error"]


@pytest.mark.parametrize("threshold", [0, 1])
def test_deployed_query_thresholds_keep_standard_envelope(deployment, threshold):
    response = _invoke(deployment, "/query", {
        "query": "electric vehicle battery cost", "max_results": 10, "similarity_threshold": threshold,
    })
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert data["results"]
    assert all(item["similarity_score"] >= threshold for item in data["results"])
    assert data["query_id"] and "billing" in data
    assert "public" not in response["headers"].get("Cache-Control", "")


def test_deployed_comp_count_matches_represented_assets(deployment):
    response = _invoke(deployment, "/query", {
        "aisystem": {"name": "issue38-acceptance", "aisysuse": {
            "lid": os.environ.get("IAB_TEST_LICENSE_ID", "lic_default_v1"), "aiauth": 2, "scope": 0,
            "ext": {"discovery": {"max_results": 50}},
        }},
    })
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert data["_meta"]["total_results"] == len(data["package"]["scope"]["text"])
    assert data["_meta"]["total_results"] <= 50
