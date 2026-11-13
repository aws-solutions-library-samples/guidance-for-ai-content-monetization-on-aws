"""The content soft delete and the content.deleted event contract.

The event is the contract between the dashboard backend, the seed step and the deindexer in the
pipeline stack, and later the comparison-run expiry. Its source, detail-type and detail keys are
pinned here.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from shared.content_delete import (
    CONTENT_DELETED_DETAIL_TYPE,
    CONTENT_EVENT_SOURCE,
    DELETING_STATUS,
    ContentDeleteEventError,
    ContentNotFound,
    deleted_event_detail,
    soft_delete,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-west-2")
        yield resource.create_table(
            TableName="cm-content",
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


@pytest.fixture
def events():
    client = MagicMock()
    client.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}
    return client


def _published(table, content_id="cnt_1", **extra):
    item = {
        "content_id": content_id,
        "status": "published",
        "md_s3_key": f"content/example.com/{content_id}.md",
        "source_s3_key": f"ingest/{content_id}.json",
        "embedding_s3_key": f"embeddings/{content_id}.json",
        **extra,
    }
    table.put_item(Item=item)
    return item


def test_event_contract_is_pinned():
    assert CONTENT_EVENT_SOURCE == "cm.content"
    assert CONTENT_DELETED_DETAIL_TYPE == "content.deleted"
    assert DELETING_STATUS == "deleting"


def test_soft_delete_marks_the_record_and_emits_the_pinned_event(table, events):
    _published(table)

    detail = soft_delete(table, events, "cnt_1", "user-123", now=NOW)

    record = table.get_item(Key={"content_id": "cnt_1"})["Item"]
    assert record["status"] == "deleting"
    assert record["deleting_at"] == NOW.isoformat()
    assert record["deleted_by"] == "user-123"
    # Nothing else about the record changes: the deindexer still needs its keys.
    assert record["md_s3_key"] == "content/example.com/cnt_1.md"

    events.put_events.assert_called_once()
    (entry,) = events.put_events.call_args.kwargs["Entries"]
    assert set(entry) == {"Source", "DetailType", "Detail"}  # default bus: no EventBusName
    assert entry["Source"] == "cm.content"
    assert entry["DetailType"] == "content.deleted"
    assert (
        json.loads(entry["Detail"])
        == detail
        == {
            "content_id": "cnt_1",
            "md_s3_key": "content/example.com/cnt_1.md",
            "source_s3_key": "ingest/cnt_1.json",
            "embedding_s3_key": "embeddings/cnt_1.json",
            "deleted_by": "user-123",
            "deleting_at": NOW.isoformat(),
        }
    )


def test_unknown_record_is_not_found_and_neither_created_nor_announced(table, events):
    with pytest.raises(ContentNotFound) as excinfo:
        soft_delete(table, events, "cnt_missing", "user-123")

    assert str(excinfo.value) == "Content cnt_missing not found"
    assert "Item" not in table.get_item(Key={"content_id": "cnt_missing"})
    events.put_events.assert_not_called()


def test_deleting_again_re_emits_so_a_stuck_delete_can_be_retried(table, events):
    _published(table)

    soft_delete(table, events, "cnt_1", "user-123")
    soft_delete(table, events, "cnt_1", "user-456")

    assert events.put_events.call_count == 2
    assert table.get_item(Key={"content_id": "cnt_1"})["Item"]["deleted_by"] == "user-456"


def test_a_rejected_event_raises_and_leaves_the_record_deleting(table, events):
    _published(table)
    events.put_events.return_value = {
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "ThrottlingException", "ErrorMessage": "slow down"}],
    }

    with pytest.raises(ContentDeleteEventError, match="ThrottlingException"):
        soft_delete(table, events, "cnt_1", "user-123")

    assert table.get_item(Key={"content_id": "cnt_1"})["Item"]["status"] == "deleting"


def test_other_dynamodb_errors_propagate(events):
    from botocore.exceptions import ClientError

    failing = MagicMock()
    failing.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "busy"}}, "UpdateItem"
    )

    with pytest.raises(ClientError):
        soft_delete(failing, events, "cnt_1", "user-123")
    events.put_events.assert_not_called()


@pytest.mark.parametrize("value", [None, "", 7, ["content/a.md"]])
def test_unusable_object_keys_are_null_in_the_detail(value):
    record = {"content_id": "cnt_1", "md_s3_key": value}

    detail = deleted_event_detail(record, "system:seed", NOW.isoformat())

    assert detail["md_s3_key"] is None
    assert detail["source_s3_key"] is None
    assert detail["embedding_s3_key"] is None
