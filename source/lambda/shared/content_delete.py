"""Soft delete of a content record, and the ``content.deleted`` event contract.

A content delete happens in two steps:

1. ``soft_delete`` marks the record ``deleting`` and emits ``content.deleted`` on the default
   EventBridge bus. Partner reads serve only ``published`` content, so they return ``404`` from
   this moment.
2. The content deindexer in the pipeline stack consumes the event. It removes the search
   document, the stored objects and the processing jobs, and then, last, the record. The record
   survives until the end, so a delete that fails part-way can be completed by deleting again,
   which emits the event again.

Callers pass in their own clients, so the dashboard backend, the seed step and the CDK rule all
share this one definition of the contract.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from botocore.exceptions import ClientError

CONTENT_EVENT_SOURCE = "cm.content"
CONTENT_DELETED_DETAIL_TYPE = "content.deleted"
DELETING_STATUS = "deleting"

# Record attributes that name the stored objects the deindexer removes, in the event detail.
OBJECT_KEY_FIELDS = ("md_s3_key", "source_s3_key", "embedding_s3_key")


class ContentNotFound(Exception):
    """The content record does not exist."""

    def __init__(self, content_id: str) -> None:
        super().__init__(f"Content {content_id} not found")
        self.content_id = content_id


class ContentDeleteEventError(Exception):
    """EventBridge did not accept the ``content.deleted`` event."""


def deleted_event_detail(record: Mapping[str, Any], actor: str, deleting_at: str) -> Dict[str, Any]:
    """Build the ``content.deleted`` detail from a record.

    Every object key is present, and is ``None`` when the record has no usable value, so the
    deindexer never needs to read the record to find what to delete.
    """
    detail: Dict[str, Any] = {"content_id": record["content_id"]}
    for field in OBJECT_KEY_FIELDS:
        value = record.get(field)
        detail[field] = value if isinstance(value, str) and value else None
    detail["deleted_by"] = actor
    detail["deleting_at"] = deleting_at
    return detail


def soft_delete(
    content_table: Any,
    events_client: Any,
    content_id: str,
    actor: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Mark ``content_id`` as ``deleting`` and emit ``content.deleted``.

    A record that is already ``deleting`` is marked again and its event is emitted again. That
    is the retry path for a delete that did not complete.

    Returns the emitted event detail. Raises ``ContentNotFound`` if the record does not exist,
    and ``ContentDeleteEventError`` if EventBridge rejects the event. The record then stays
    ``deleting``, and deleting again retries.
    """
    deleting_at = (now or datetime.now(timezone.utc)).isoformat()
    try:
        response = content_table.update_item(
            Key={"content_id": content_id},
            UpdateExpression="SET #s = :deleting, deleting_at = :deleting_at, deleted_by = :deleted_by",
            ConditionExpression="attribute_exists(content_id)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":deleting": DELETING_STATUS,
                ":deleting_at": deleting_at,
                ":deleted_by": actor,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ContentNotFound(content_id) from exc
        raise

    detail = deleted_event_detail(response["Attributes"], actor, deleting_at)
    result = events_client.put_events(
        Entries=[
            {
                "Source": CONTENT_EVENT_SOURCE,
                "DetailType": CONTENT_DELETED_DETAIL_TYPE,
                "Detail": json.dumps(detail),
            }
        ]
    )
    if result.get("FailedEntryCount"):
        entry = (result.get("Entries") or [{}])[0]
        raise ContentDeleteEventError(
            f"content.deleted was not accepted for {content_id}: "
            f"{entry.get('ErrorCode', 'unknown')} {entry.get('ErrorMessage', '')}".rstrip()
        )
    return detail
