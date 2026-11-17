"""Opt-in manual draft acceptance via IAM invocation, never publication or public auth.

RUN_INTEGRATION=1, IAB_GENERATE_MANIFEST_DRAFT=1, AWS_REGION,
IAB_EXPECTED_ACCOUNT_ID and IAB_MANIFEST_EVIDENCE_DIR are required.
The explicit opt-in can stage a new draft using Bedrock; it never publishes.
An unrelated unpublished draft or active run stops this acceptance.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import boto3
import pytest
from botocore.config import Config

from services.llms_txt_service import MANIFEST_RENDERER_REVISION as RENDERER_REVISION

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("IAB_GENERATE_MANIFEST_DRAFT") != "1",
        reason="requires explicit manual draft acceptance opt-in",
    ),
]


def test_manual_renderer_draft_and_unchanged_rerun_without_publication():
    session = boto3.Session(region_name=os.environ["AWS_REGION"])
    assert session.client("sts").get_caller_identity()["Account"] == os.environ["IAB_EXPECTED_ACCOUNT_ID"]
    stack = session.client("cloudformation").describe_stacks(StackName="ContentMonetizationStack")["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    client = session.client("lambda", config=Config(read_timeout=90))
    evidence = Path(os.environ["IAB_MANIFEST_EVIDENCE_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)

    def invoke(method, path):
        event = {
            "httpMethod": method,
            "path": path,
            "headers": {},
            "requestContext": {"authorizer": {"claims": {"sub": "issue38-manual-acceptance"}}},
            "body": "{}",
        }
        result = client.invoke(FunctionName=outputs["UIBackendLambdaName"], Payload=json.dumps(event).encode())
        assert "FunctionError" not in result
        response = json.loads(result["Payload"].read())
        assert response["statusCode"] == (202 if method == "POST" else 200)
        return json.loads(response["body"])

    def wait(run_id):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            run = invoke("GET", f"/content/llms-txt/runs/{run_id}")
            if run["status"] in {"completed", "no_change", "failed"}:
                return run
            time.sleep(5)
        pytest.fail(f"Manifest run did not finish: {run_id}")

    before = invoke("GET", "/content/llms-txt")
    assert before["active_run"] is None, "Do not interrupt another generation"
    existing = before["draft"]
    if existing and existing["status"] not in {"published", "superseded"}:
        previous_evidence = evidence / "summary.json"
        assert previous_evidence.exists(), "Review the existing draft before replacing it"
        recorded = json.loads(previous_evidence.read_text())
        assert existing.get("renderer_revision") == RENDERER_REVISION
        assert (existing["run_id"], existing["etag"]) == (
            recorded["draft_run_id"], recorded["draft_etag"],
        ), "Do not supersede another operator's draft"
        generated = invoke("GET", f"/content/llms-txt/runs/{existing['run_id']}")
    else:
        started = invoke("POST", "/content/llms-txt/runs")
        assert not started["already_running"]
        generated = wait(started["run"]["run_id"])

    assert generated["status"] == "completed"
    assert generated["renderer_revision"] == RENDERER_REVISION
    draft = generated["draft"]
    assert draft["renderer_revision"] == RENDERER_REVISION
    assert draft["status"] == "in_review"
    assert "- Content query: `" in draft["content"] and "/query`" in draft["content"]
    assert "- Semantic search: `/search`" not in draft["content"]
    (evidence / "draft.md").write_text(draft["content"])
    (evidence / "draft.diff").write_text(draft["diff"])

    repeated = invoke("POST", "/content/llms-txt/runs")
    assert not repeated["already_running"]
    unchanged = wait(repeated["run"]["run_id"])
    assert unchanged["status"] == "no_change"
    assert unchanged["renderer_revision"] == generated["renderer_revision"]
    assert unchanged["snapshot_fingerprint"] == generated["snapshot_fingerprint"]
    after = invoke("GET", "/content/llms-txt")
    assert after["published"] == before["published"], "Generation must never change publication"
    assert after["draft"]["run_id"] == draft["run_id"]

    summary = {
        "account": os.environ["IAB_EXPECTED_ACCOUNT_ID"],
        "region": os.environ["AWS_REGION"],
        "draft_run_id": draft["run_id"],
        "draft_revision": draft["revision"],
        "draft_etag": draft["etag"],
        "renderer_revision": draft["renderer_revision"],
        "snapshot_fingerprint": draft["snapshot_fingerprint"],
        "no_change_run_id": unchanged["run_id"],
        "published_unchanged": True,
        "published_run_id": before["published"]["run_id"],
        "published_sha256": hashlib.sha256(before["published"]["content"].encode()).hexdigest(),
        "same_catalog_as_published": draft["snapshot_fingerprint"] == before["published"]["snapshot_fingerprint"],
        "published": False,
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
