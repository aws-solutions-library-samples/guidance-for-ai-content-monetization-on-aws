"""The content deindexer: it completes a delete from its content.deleted event.

These tests pin the step order (search document, objects, jobs, then the record), the
"not found is done" idempotence, the precondition, the paginated jobs scan and the conditional
final delete.
"""

from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from content_deindexer import handler
from content_deindexer.handler import PIPELINE_ARTIFACTS, deindex

REGION = "us-west-2"
BUCKET = "iab-llm-content-test"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _intermediates(content_id="cnt_1"):
    return [f"pipeline/{content_id}/{name}" for name in PIPELINE_ARTIFACTS]


@pytest.fixture
def aws():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        content = dynamodb.create_table(
            TableName="cm-content",
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        jobs = dynamodb.create_table(
            TableName="cm-jobs",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield content, jobs, s3


def _detail(content_id="cnt_1", **keys):
    detail = {
        "content_id": content_id,
        "md_s3_key": f"content/example.com/{content_id}.md",
        "source_s3_key": f"ingest/{content_id}.json",
        "embedding_s3_key": f"embeddings/{content_id}.json",
        "deleted_by": "user-123",
        "deleting_at": "2026-11-07T12:00:00+00:00",
    }
    detail.update(keys)
    return detail


def _seed(content, jobs, s3, content_id="cnt_1", status="deleting", job_count=2):
    detail = _detail(content_id)
    keys = {f: detail[f] for f in ("md_s3_key", "source_s3_key", "embedding_s3_key")}
    content.put_item(Item={"content_id": content_id, "status": status, **keys})
    for key in list(keys.values()) + _intermediates(content_id):
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x", Metadata={"content-id": content_id})
    for n in range(job_count):
        jobs.put_item(Item={"job_id": f"job_{content_id}_{n}", "content_id": content_id})
    jobs.put_item(Item={"job_id": "job_other", "content_id": "cnt_other"})
    return detail


def _run(detail, content, jobs, s3, delete_search_document=None):
    calls = []
    return (
        deindex(
            detail,
            content_table=content,
            jobs_table=jobs,
            s3_client=s3,
            bucket=BUCKET,
            delete_search_document=delete_search_document or (lambda cid: calls.append(cid) or True),
        ),
        calls,
    )


def _object_exists(s3, key):
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError:
        return False


def test_a_deleting_record_is_removed_with_everything_it_owns(aws):
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)

    summary, search_calls = _run(detail, content, jobs, s3)

    assert summary == {
        "content_id": "cnt_1",
        "result": "deleted",
        "search_document": True,
        "objects": [detail["md_s3_key"], detail["source_s3_key"], detail["embedding_s3_key"], *_intermediates()],
        "jobs": 2,
    }
    assert search_calls == ["cnt_1"]
    assert "Item" not in content.get_item(Key={"content_id": "cnt_1"})
    for key in summary["objects"]:
        assert not _object_exists(s3, key)
    # Only this record's jobs go.
    assert [job["job_id"] for job in jobs.scan()["Items"]] == ["job_other"]


def test_steps_run_in_order_with_the_record_last():
    order = []
    record = {"status": "deleting", **{f: _detail()[f] for f in ("md_s3_key", "source_s3_key", "embedding_s3_key")}}
    content = MagicMock()
    content.get_item.side_effect = lambda **kw: order.append("read") or {"Item": record}
    content.delete_item.side_effect = lambda **kw: order.append("record")
    s3 = MagicMock()
    s3.head_object.side_effect = lambda **kw: order.append(f"head:{kw['Key']}") or {"Metadata": {"content-id": "cnt_1"}}
    s3.delete_object.side_effect = lambda **kw: order.append(f"object:{kw['Key']}")
    jobs = MagicMock()
    jobs.scan.side_effect = lambda **kw: order.append("jobs-scan") or {"Items": [{"job_id": "j1"}]}
    jobs.delete_item.side_effect = lambda **kw: order.append("job")

    deindex(
        _detail(),
        content_table=content,
        jobs_table=jobs,
        s3_client=s3,
        bucket=BUCKET,
        delete_search_document=lambda cid: order.append("search") or True,
    )

    assert order == [
        "read",
        "search",
        "head:content/example.com/cnt_1.md",
        "object:content/example.com/cnt_1.md",
        "head:ingest/cnt_1.json",
        "object:ingest/cnt_1.json",
        "object:embeddings/cnt_1.json",
        "object:pipeline/cnt_1/extracted.json",
        "object:pipeline/cnt_1/enrichment.json",
        "jobs-scan",
        "job",
        "record",
    ]
    assert content.delete_item.call_args.kwargs["ConditionExpression"] == "#s = :deleting"


def test_a_repeat_run_after_partial_progress_still_completes(aws):
    """Not found is done: missing search document, objects and jobs don't fail a retry."""
    content, jobs, s3 = aws
    content.put_item(Item={"content_id": "cnt_1", "status": "deleting", "md_s3_key": "content/example.com/cnt_1.md"})

    summary, _ = _run(_detail(), content, jobs, s3, delete_search_document=lambda cid: False)

    assert summary["result"] == "deleted"
    assert summary["search_document"] is False
    assert summary["jobs"] == 0
    assert "Item" not in content.get_item(Key={"content_id": "cnt_1"})


def test_a_republished_record_is_left_alone(aws):
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3, status="published")

    summary, search_calls = _run(detail, content, jobs, s3)

    assert summary == {"content_id": "cnt_1", "result": "skipped", "status": "published"}
    assert search_calls == []
    assert content.get_item(Key={"content_id": "cnt_1"})["Item"]["status"] == "published"
    assert _object_exists(s3, detail["md_s3_key"])
    assert len(jobs.scan()["Items"]) == 3


def test_an_already_deleted_record_is_a_no_op(aws):
    content, jobs, s3 = aws

    summary, search_calls = _run(_detail(), content, jobs, s3)

    assert summary == {"content_id": "cnt_1", "result": "already_deleted"}
    assert search_calls == []


def test_the_record_is_kept_if_it_is_republished_mid_run(aws):
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)

    def republish_then_delete(content_id):
        # Not reachable through the pipeline, which never overwrites a deleting record
        # (tests/content_pipeline/test_handler.py); pinned for any other writer.
        content.put_item(Item={"content_id": content_id, "status": "published"})
        return True

    summary, _ = _run(detail, content, jobs, s3, delete_search_document=republish_then_delete)

    assert summary["result"] == "record_kept"
    assert content.get_item(Key={"content_id": "cnt_1"})["Item"]["status"] == "published"


def test_object_keys_come_from_the_record_not_the_event(aws):
    """An event naming other keys can't steer the deletes; the record is the authority."""
    content, jobs, s3 = aws
    _seed(content, jobs, s3)
    s3.put_object(Bucket=BUCKET, Key="content/example.com/other.md", Body=b"other")

    summary, _ = _run(_detail(md_s3_key="content/example.com/other.md"), content, jobs, s3)

    assert summary["result"] == "deleted"
    assert "content/example.com/other.md" not in summary["objects"]
    assert _object_exists(s3, "content/example.com/other.md")
    assert not _object_exists(s3, "content/example.com/cnt_1.md")


def test_a_shared_address_now_owned_by_another_document_is_kept(aws):
    """A canonical-URL address can pass to another document; a late retry must not delete it."""
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)
    s3.put_object(Bucket=BUCKET, Key=detail["md_s3_key"], Body=b"new owner", Metadata={"content-id": "cnt_2"})

    summary, _ = _run(detail, content, jobs, s3)

    assert summary["result"] == "deleted"
    assert detail["md_s3_key"] not in summary["objects"]
    assert s3.get_object(Bucket=BUCKET, Key=detail["md_s3_key"])["Body"].read() == b"new owner"
    assert not _object_exists(s3, detail["embedding_s3_key"])


def test_a_forbidden_head_means_the_object_is_already_gone():
    """Without ListBucket, S3 answers a HEAD of a missing key with 403; that is not a failure."""
    record = {"status": "deleting", "md_s3_key": "content/example.com/cnt_1.md"}
    content = MagicMock()
    content.get_item.return_value = {"Item": record}
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    summary = deindex(
        _detail(),
        content_table=content,
        jobs_table=MagicMock(**{"scan.return_value": {"Items": []}}),
        s3_client=s3,
        bucket=BUCKET,
        delete_search_document=lambda cid: True,
    )

    assert summary["result"] == "deleted"
    assert "content/example.com/cnt_1.md" not in summary["objects"]


def test_other_head_errors_fail_the_run_for_a_retry():
    content = MagicMock()
    content.get_item.return_value = {"Item": {"status": "deleting", "md_s3_key": "content/a.md"}}
    s3 = MagicMock()
    s3.head_object.side_effect = ClientError({"Error": {"Code": "SlowDown", "Message": "slow"}}, "HeadObject")

    with pytest.raises(ClientError):
        deindex(
            _detail(),
            content_table=content,
            jobs_table=MagicMock(),
            s3_client=s3,
            bucket=BUCKET,
            delete_search_document=lambda cid: True,
        )
    content.delete_item.assert_not_called()


def test_an_owned_object_without_metadata_is_deleted(aws):
    """Objects uploaded without content-id metadata (for example a key-derived ingest) still go."""
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)
    s3.put_object(Bucket=BUCKET, Key=detail["source_s3_key"], Body=b"x")

    summary, _ = _run(detail, content, jobs, s3)

    assert detail["source_s3_key"] in summary["objects"]
    assert not _object_exists(s3, detail["source_s3_key"])


def test_a_failure_propagates_and_leaves_the_record_for_a_retry(aws):
    """The failed run goes to Lambda's retries and then the DLQ; a repeat delete completes it."""
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)

    def refused(content_id):
        raise RuntimeError("403 from the collection")

    with pytest.raises(RuntimeError):
        _run(detail, content, jobs, s3, delete_search_document=refused)

    assert content.get_item(Key={"content_id": "cnt_1"})["Item"]["status"] == "deleting"
    assert _object_exists(s3, detail["md_s3_key"])


def test_the_jobs_scan_follows_every_page():
    jobs = MagicMock()
    jobs.scan.side_effect = [
        {"Items": [{"job_id": "j1"}], "LastEvaluatedKey": {"job_id": "j1"}},
        {"Items": [{"job_id": "j2"}, {"job_id": "j3"}]},
    ]
    content = MagicMock()
    content.get_item.return_value = {"Item": {"status": "deleting"}}

    summary = deindex(
        _detail(md_s3_key=None, source_s3_key=None, embedding_s3_key=None),
        content_table=content,
        jobs_table=jobs,
        s3_client=MagicMock(),
        bucket=BUCKET,
        delete_search_document=lambda cid: True,
    )

    assert summary["jobs"] == 3
    assert jobs.scan.call_args_list[1].kwargs["ExclusiveStartKey"] == {"job_id": "j1"}
    assert [c.kwargs["Key"] for c in jobs.delete_item.call_args_list] == [
        {"job_id": "j1"},
        {"job_id": "j2"},
        {"job_id": "j3"},
    ]


def test_a_key_outside_the_deletable_prefixes_is_skipped_not_retried(aws):
    content, jobs, s3 = aws
    content.put_item(
        Item={
            "content_id": "cnt_1",
            "status": "deleting",
            "md_s3_key": "llms/llms.txt",
            "embedding_s3_key": "embeddings/cnt_1.json",
        }
    )

    summary, _ = _run(_detail(), content, jobs, s3)

    assert summary["result"] == "deleted"
    assert summary["objects"] == ["embeddings/cnt_1.json", *_intermediates()]


def test_the_intermediates_match_what_the_pipeline_writes():
    """Drift guard: the deindexer derives these keys, so they must match the pipeline's."""
    pipeline = (REPO_ROOT / "source" / "lambda" / "content_pipeline" / "handler.py").read_text()
    assert set(PIPELINE_ARTIFACTS) == {"extracted.json", "enrichment.json"}
    assert 'f"pipeline/{content_id}/extracted.json"' in pipeline
    assert "f\"pipeline/{event['content_id']}/enrichment.json\"" in pipeline


@pytest.mark.parametrize("detail", [{}, {"content_id": ""}, {"content_id": 7}])
def test_an_event_without_a_content_id_is_ignored(detail):
    content = MagicMock()

    summary = deindex(
        detail,
        content_table=content,
        jobs_table=MagicMock(),
        s3_client=MagicMock(),
        bucket=BUCKET,
        delete_search_document=lambda cid: True,
    )

    assert summary == {"result": "ignored", "reason": "missing_content_id"}
    content.get_item.assert_not_called()


def test_lambda_handler_reads_the_event_detail_and_its_environment(aws, monkeypatch):
    content, jobs, s3 = aws
    detail = _seed(content, jobs, s3)
    monkeypatch.setenv("CONTENT_TABLE_NAME", "cm-content")
    monkeypatch.setenv("JOBS_TABLE_NAME", "cm-jobs")
    monkeypatch.setenv("CONTENT_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    deleted = []
    monkeypatch.setattr(handler, "delete_document", lambda cid: deleted.append(cid) or True)

    summary = handler.lambda_handler({"source": "cm.content", "detail-type": "content.deleted", "detail": detail}, None)

    assert summary["result"] == "deleted"
    assert deleted == ["cnt_1"]


def test_a_comparison_partition_record_is_removed_with_its_markdown(aws):
    """scrape-vs-api-comparison R15 / task 8.6a: a comparison run's Markdown lives under ``comparison/``
    (never ``content/``). The deindexer must delete it, not skip it as an unknown prefix, or the expiry
    sweep would orphan the object while removing the record."""
    content, jobs, s3 = aws
    url_hash = "a" * 64
    detail = _detail(
        md_s3_key=f"comparison/{url_hash}.md",
        source_s3_key="ingest/comparison/aaaaaaaaaaaa.html",
    )
    keys = {f: detail[f] for f in ("md_s3_key", "source_s3_key", "embedding_s3_key")}
    content.put_item(Item={"content_id": "cnt_1", "status": "deleting", "source": "comparison", **keys})
    for key in list(keys.values()) + _intermediates():
        s3.put_object(Bucket=BUCKET, Key=key, Body=b"x", Metadata={"content-id": "cnt_1"})

    summary, _ = _run(detail, content, jobs, s3)

    assert summary["result"] == "deleted"
    assert f"comparison/{url_hash}.md" in summary["objects"]
    assert "ingest/comparison/aaaaaaaaaaaa.html" in summary["objects"]
    for key in summary["objects"]:
        assert not _object_exists(s3, key)
    assert "Item" not in content.get_item(Key={"content_id": "cnt_1"})
