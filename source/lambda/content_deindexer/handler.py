"""Content deindexer: completes a content delete from its ``content.deleted`` event.

The dashboard backend's delete only marks the record ``deleting`` and emits the event (see
``shared/content_delete.py``). This Lambda removes everything else, in this order:

1. the search document
2. the stored objects: those named in the event (Markdown, source, embedding) and the
   pipeline's intermediates under ``pipeline/{content_id}/``
3. the processing jobs for the record
4. the record, last, and only while it is still ``deleting``

Every step treats "not found" as done, so a retried event repeats the steps safely. The record
outlives every other step, so a run that fails part-way leaves a ``deleting`` record that a
repeat delete completes.

The object keys come from the record, read with a consistent read, not from the event. The
Markdown and source objects are deleted only if their ``content-id`` metadata, when present,
names this record. A canonical-URL Markdown address can pass to another document once this one
is gone, so a late retry must never delete that document's object. The pipeline never
overwrites a ``deleting`` record, so the record can't be re-published under a running delete.
"""

import logging
import os
from typing import Any, Callable, Dict, List

import boto3
from botocore.exceptions import ClientError
from services.opensearch_client import delete_document
from shared.content_delete import DELETING_STATUS, OBJECT_KEY_FIELDS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The prefixes this function's role may delete under. A key outside them would be refused on
# every retry, so it is logged and skipped rather than failing the run.
# "comparison/" holds the comparison partition's Markdown (scrape-vs-api-comparison R15): a
# comparison record's md_s3_key lives there, never under content/.
DELETABLE_PREFIXES = ("content/", "comparison/", "ingest/", "embeddings/", "pipeline/")

# The pipeline's per-document intermediates (content_pipeline/handler.py). The extracted text is
# the document itself, so a delete removes these too. Their keys derive from the content_id.
PIPELINE_ARTIFACTS = ("extracted.json", "enrichment.json")

# Objects at addresses another document can later own; checked against their content-id metadata.
OWNED_KEY_FIELDS = ("md_s3_key", "source_s3_key")
# The role may read objects under the owned prefixes but may not list the bucket, so S3 answers a
# HEAD of a missing key there with 403 rather than 404. Both mean the object is gone.
_ABSENT = {"403", "404", "NoSuchKey", "NotFound", "Forbidden"}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    dynamodb = boto3.resource("dynamodb")
    return deindex(
        event.get("detail") or {},
        content_table=dynamodb.Table(os.environ["CONTENT_TABLE_NAME"]),
        jobs_table=dynamodb.Table(os.environ["JOBS_TABLE_NAME"]),
        s3_client=boto3.client("s3"),
        bucket=os.environ["CONTENT_BUCKET_NAME"],
        delete_search_document=delete_document,
    )


def deindex(
    detail: Dict[str, Any],
    *,
    content_table: Any,
    jobs_table: Any,
    s3_client: Any,
    bucket: str,
    delete_search_document: Callable[[str], bool],
) -> Dict[str, Any]:
    """Complete the delete described by a ``content.deleted`` detail. Returns a summary."""
    content_id = detail.get("content_id")
    if not isinstance(content_id, str) or not content_id:
        # Retrying can't fix a malformed event, so don't fail into the DLQ for it.
        logger.error("content.deleted event without a content_id: %s", sorted(detail))
        return {"result": "ignored", "reason": "missing_content_id"}

    record = content_table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
    if record is None:
        logger.info("Deindex %s: record already gone", content_id)
        return {"content_id": content_id, "result": "already_deleted"}
    if record.get("status") != DELETING_STATUS:
        logger.warning("Deindex %s: status is %r, not deleting; nothing deleted", content_id, record.get("status"))
        return {"content_id": content_id, "result": "skipped", "status": record.get("status")}

    search_deleted = delete_search_document(content_id)
    objects = _delete_objects(s3_client, bucket, content_id, record)
    jobs = _delete_jobs(jobs_table, content_id)

    try:
        content_table.delete_item(
            Key={"content_id": content_id},
            ConditionExpression="#s = :deleting",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":deleting": DELETING_STATUS},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        logger.error("Deindex %s: record changed before its final delete; kept", content_id)
        return {"content_id": content_id, "result": "record_kept", "objects": objects, "jobs": jobs}

    logger.info(
        "Deindex %s complete: search_document=%s objects=%d jobs=%d",
        content_id,
        "deleted" if search_deleted else "absent",
        len(objects),
        jobs,
    )
    return {
        "content_id": content_id,
        "result": "deleted",
        "search_document": search_deleted,
        "objects": objects,
        "jobs": jobs,
    }


def _object_keys(content_id: str, record: Dict[str, Any]) -> List[tuple]:
    """(key, ownership-checked) pairs for every object this record owns."""
    keys = [(record.get(field), field in OWNED_KEY_FIELDS) for field in OBJECT_KEY_FIELDS]
    keys += [(f"pipeline/{content_id}/{name}", False) for name in PIPELINE_ARTIFACTS]
    return [(key, owned) for key, owned in keys if isinstance(key, str) and key]


def _owned_by(s3_client: Any, bucket: str, key: str, content_id: str) -> bool:
    """False if the object is gone, or its content-id metadata names another document."""
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _ABSENT:
            return False
        raise
    owner = (head.get("Metadata") or {}).get("content-id")
    if owner and owner != content_id:
        logger.warning("Deindex %s: %r belongs to %s; kept", content_id, key, owner)
        return False
    return True


def _delete_objects(s3_client: Any, bucket: str, content_id: str, record: Dict[str, Any]) -> List[str]:
    deleted: List[str] = []
    for key, ownership_checked in _object_keys(content_id, record):
        if not key.startswith(DELETABLE_PREFIXES):
            logger.warning("Deindex %s: %r is outside the deletable prefixes; skipped", content_id, key)
            continue
        if ownership_checked and not _owned_by(s3_client, bucket, key, content_id):
            continue
        # Deleting a missing key succeeds, so a retry is safe.
        s3_client.delete_object(Bucket=bucket, Key=key)
        deleted.append(key)
    return deleted


def _delete_jobs(jobs_table: Any, content_id: str) -> int:
    count = 0
    scan_kwargs: Dict[str, Any] = {
        "FilterExpression": "content_id = :content_id",
        "ExpressionAttributeValues": {":content_id": content_id},
        "ProjectionExpression": "job_id",
    }
    while True:
        page = jobs_table.scan(**scan_kwargs)
        for job in page.get("Items", []):
            jobs_table.delete_item(Key={"job_id": job["job_id"]})
            count += 1
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return count
        scan_kwargs["ExclusiveStartKey"] = last_key
