"""Managed llms.txt async worker tests."""

import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

import llms_txt_worker.handler as worker
from services.llms_txt_service import (
    PermanentGenerationError,
    RetryableGenerationError,
)


@pytest.fixture
def service(monkeypatch):
    instance = MagicMock()
    monkeypatch.setattr(
        worker,
        "_load_service",
        lambda: (
            instance,
            PermanentGenerationError,
            RetryableGenerationError,
        ),
    )
    return instance


def test_worker_requires_run_id():
    with pytest.raises(ValueError, match="run_id"):
        worker.lambda_handler({}, None)


def test_worker_completes_run(service):
    service.process_run.return_value = {
        "run_id": "r1", "status": "completed", "renderer_revision": "query-access-v1",
    }
    result = worker.lambda_handler({"run_id": "r1"}, None)
    assert result["status"] == "completed"
    assert result["renderer_revision"] == "query-access-v1"


def test_permanent_error_fails_without_retry(service):
    service.process_run.side_effect = PermanentGenerationError("bad output")
    service.get_run.return_value = {"run_id": "r1", "status": "failed"}
    result = worker.lambda_handler({"run_id": "r1"}, None)
    assert result["status"] == "failed"
    service.fail_run.assert_called_once_with("r1", "invalid_generation", "bad output")


def test_transient_error_rethrows_before_final_attempt(service):
    service.process_run.side_effect = RetryableGenerationError("throttle", 2)
    with pytest.raises(RetryableGenerationError, match="throttle"):
        worker.lambda_handler({"run_id": "r1"}, None)
    service.fail_run.assert_not_called()


def test_third_transient_error_becomes_failed(service):
    service.process_run.side_effect = RetryableGenerationError("throttle", 3)
    with pytest.raises(RetryableGenerationError, match="throttle"):
        worker.lambda_handler({"run_id": "r1"}, None)
    service.fail_run.assert_called_once_with("r1", "generation_failed", "throttle")


@pytest.mark.parametrize("current_attempt", [0, 1, 2, 3])
def test_claim_contention_uses_current_attempt_budget_and_rethrows(service, current_attempt):
    error = RetryableGenerationError("Generation attempt claim changed", current_attempt)
    service.process_run.side_effect = error

    with pytest.raises(RetryableGenerationError) as raised:
        worker.lambda_handler({"run_id": "r1"}, None)

    assert raised.value is error
    if current_attempt < 3:
        service.fail_run.assert_not_called()
    else:
        service.fail_run.assert_called_once_with("r1", "generation_failed", str(error))


@mock_aws
def test_dlq_destination_reconciles_without_loading_generation_service(monkeypatch):
    monkeypatch.setenv("JOBS_TABLE_NAME", "cm-jobs")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(
        worker,
        "_load_service",
        MagicMock(side_effect=AssertionError("generation service must not load")),
    )
    table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName="cm-jobs",
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.put_item(
        Item={
            "job_id": "r1",
            "run_id": "r1",
            "status": "processing",
            "attempt_token": "attempt",
            "attempt_started_at": "2026-08-28T00:00:00+00:00",
            "renderer_revision": "older-renderer",
        }
    )
    table.put_item(
        Item={
            "job_id": "CONTROL#LLMS_TXT",
            "active_run_id": "r1",
            "active_run_status": "processing",
        }
    )
    event = {
        "Records": [
            {
                "body": json.dumps(
                    {
                        "requestPayload": {"run_id": "r1"},
                        "responseContext": {"functionError": "Unhandled"},
                    }
                )
            }
        ]
    }

    result = worker.lambda_handler(event, None)

    assert result == {"reconciled_run_ids": ["r1"]}
    failed = table.get_item(Key={"job_id": "r1"})["Item"]
    control = table.get_item(Key={"job_id": "CONTROL#LLMS_TXT"})["Item"]
    assert failed["status"] == "failed"
    assert failed["error_code"] == "terminal_async_failure"
    assert failed["renderer_revision"] == "older-renderer"
    assert "attempt_token" not in failed
    assert "attempt_started_at" not in failed
    assert "active_run_id" not in control
    assert control["active_run_status"] == "failed"
