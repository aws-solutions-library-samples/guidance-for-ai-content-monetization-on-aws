"""Async Lambda entry point for managed llms.txt generation."""

import json
import logging
import os
from typing import Any, Dict

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("Records"):
        return _reconcile_dlq(event)

    run_id = event.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    service, permanent_error, retryable_error = _load_service()
    try:
        result = service.process_run(str(run_id))
        logger.info("llms.txt run %s reached %s", run_id, result.get("status"))
        return result
    except permanent_error as exc:
        logger.error("llms.txt run %s failed permanently: %s", run_id, exc)
        service.fail_run(str(run_id), "invalid_generation", str(exc))
        return service.get_run(str(run_id))
    except retryable_error as exc:
        logger.exception(
            "llms.txt run %s failed on attempt %s",
            run_id,
            exc.attempt_count,
        )
        if exc.attempt_count >= 3:
            service.fail_run(str(run_id), "generation_failed", str(exc))
        raise
    except Exception:
        logger.exception("llms.txt run %s failed outside a claimed attempt", run_id)
        raise


def _load_service():
    from services.llms_txt_service import (
        LlmsTxtService,
        PermanentGenerationError,
        RetryableGenerationError,
    )

    return LlmsTxtService(), PermanentGenerationError, RetryableGenerationError


def _reconcile_dlq(event: Dict[str, Any]) -> Dict[str, Any]:
    table_name = os.environ["JOBS_TABLE_NAME"]
    ddb = boto3.client("dynamodb")
    reconciled = []
    for record in event.get("Records", []):
        envelope = json.loads(record.get("body") or "{}")
        payload = envelope.get("requestPayload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if not run_id:
            raise ValueError("DLQ destination record has no run_id")
        _mark_terminal_failure(ddb, table_name, str(run_id))
        reconciled.append(str(run_id))
    logger.info("Reconciled %s terminal llms.txt run(s)", len(reconciled))
    return {"reconciled_run_ids": reconciled}


def _mark_terminal_failure(ddb: Any, table_name: str, run_id: str) -> None:
    control = ddb.get_item(
        TableName=table_name,
        Key=_serialize({"job_id": "CONTROL#LLMS_TXT"}),
        ConsistentRead=True,
    ).get("Item")
    control_item = (
        {key: _deserializer.deserialize(value) for key, value in control.items()}
        if control
        else {}
    )
    now = _now()
    transactions = [
        {
            "Update": {
                "TableName": table_name,
                "Key": _serialize({"job_id": run_id}),
                "UpdateExpression": (
                    "SET #status = :failed, updated_at = :now, completed_at = :now, "
                    "error_code = :code, error_message = :message "
                    "REMOVE attempt_token, attempt_started_at"
                ),
                "ConditionExpression": "#status IN (:queued, :processing)",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize(
                    {
                        ":queued": "queued",
                        ":processing": "processing",
                        ":failed": "failed",
                        ":now": now,
                        ":code": "terminal_async_failure",
                        ":message": "The asynchronous generation worker exhausted retries.",
                    }
                ),
            }
        }
    ]
    if control_item.get("active_run_id") == run_id:
        transactions.append(
            {
                "Update": {
                    "TableName": table_name,
                    "Key": _serialize({"job_id": "CONTROL#LLMS_TXT"}),
                    "UpdateExpression": (
                        "SET active_run_status = :failed, updated_at = :now "
                        "REMOVE active_run_id"
                    ),
                    "ConditionExpression": "active_run_id = :run",
                    "ExpressionAttributeValues": _serialize(
                        {":failed": "failed", ":now": now, ":run": run_id}
                    ),
                }
            }
        )
    try:
        ddb.transact_write_items(TransactItems=transactions)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        current = ddb.get_item(
            TableName=table_name,
            Key=_serialize({"job_id": run_id}),
            ConsistentRead=True,
        ).get("Item")
        current_status = (
            _deserializer.deserialize(current["status"]) if current and current.get("status") else None
        )
        if current_status not in {"completed", "failed", "no_change"}:
            raise


def _serialize(values: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
