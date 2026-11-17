"""Deploy-time reconciler for the public ``llms.txt`` bootstrap object.

Packaged as the bootstrap custom resource's Lambda asset. The provenance rule and
its rationale live in ``.kiro/specs/llms-txt-management`` (design, Bootstrap).
"""

import hashlib
import logging
import os
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError, ParamValidationError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PHYSICAL_ID = "managed-llms-txt-bootstrap"
KEY = "llms-txt/bootstrap/llms.txt"
MARKER = "cm-bootstrap-sha256"
MISSING = ("404", "NoSuchKey", "NotFound")
CONFLICT = ("PreconditionFailed", "412", "ConditionalRequestConflict")
# Digests of every bootstrap text shipped before the marker existed. A closed set:
# never extended, because every seed written from here on carries the marker.
PRE_MARKER_SEEDS = (
    "3dae761798b7103438f3b2795b3d9217834a16e5071989ed3bb5b1f82e73fb5e",
    "8e1a1d439bc83ec710fb701f8d3910fc70259b4c27fcc6557f1481a4804054e2",
)


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _result(action: str, digest: str) -> Dict[str, Any]:
    return {"PhysicalResourceId": PHYSICAL_ID, "Data": {"BootstrapAction": action, "Sha256": digest}}


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("RequestType") == "Delete":
        return {"PhysicalResourceId": PHYSICAL_ID}
    properties = event.get("ResourceProperties") or {}
    if "Manifest" not in properties:
        # UPDATE_ROLLBACK re-sends the PRE-update properties, and a template older than this
        # reconciler carries no manifest: nothing to reconcile, and failing a rollback would
        # leave the stack in UPDATE_ROLLBACK_FAILED.
        logger.info("no Manifest property (pre-reconciler template); nothing to reconcile")
        return _result("skipped", "")
    bucket = os.environ["BUCKET_NAME"]
    desired = properties["Manifest"].encode("utf-8")
    want = _digest(desired)
    s3 = boto3.client("s3")
    try:
        stored = s3.get_object(Bucket=bucket, Key=KEY)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in MISSING:
            raise
        action, have, condition = "seeded", "", {"IfNoneMatch": "*"}
    else:
        have = _digest(stored["Body"].read())
        if have == want:
            logger.info("bootstrap %s already current (%s)", KEY, want)
            return _result("unchanged", want)
        if (stored.get("Metadata") or {}).get(MARKER) != have and have not in PRE_MARKER_SEEDS:
            logger.info("bootstrap %s is not an unmodified seed (%s); preserved", KEY, have)
            return _result("preserved", have)
        action, condition = "refreshed", {"IfMatch": stored["ETag"]}
    put = {
        "Bucket": bucket,
        "Key": KEY,
        "Body": desired,
        "ContentType": "text/markdown; charset=utf-8",
        "CacheControl": "public, max-age=60",
        "ServerSideEncryption": "AES256",
        "Metadata": {MARKER: want},
    }
    try:
        # `condition` makes the write atomic with the state decided from above, so an edit
        # landing in between gets a 412 and is preserved instead of clobbered.
        s3.put_object(**put, **condition)
    except ParamValidationError:  # managed-runtime boto3 predating conditional writes
        logger.warning("runtime boto3 rejects %s; writing unconditionally", sorted(condition))
        s3.put_object(**put)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in CONFLICT:
            raise
        logger.info("bootstrap %s changed during the %s (was %s); preserved", KEY, action, have)
        return _result("preserved", have)
    logger.info("bootstrap %s %s (%s)", KEY, action, want)
    return _result(action, want)
