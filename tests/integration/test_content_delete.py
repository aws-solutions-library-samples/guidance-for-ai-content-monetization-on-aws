"""Deployed content delete: ingest -> delete -> everything the document owned is gone.

A dashboard ``DELETE /content/{content_id}`` is accepted with ``202`` and marks the record
``deleting``. Partner reads return ``404`` at once. The pipeline stack's deindexer then removes
the search document, the stored Markdown and source objects, the processing jobs and, last, the
record. This tier pins that end to end:

- the delete is ``202`` with ``{"content_id", "status": "deleting"}``
- the partner reads are ``404`` straight away
- the dashboard details read shows ``deleting`` until the deindexer finishes
- within a bounded poll, the document has left ``POST /query``, its objects are ``404`` and the
  record is gone
- a second delete of the gone record is ``404``

Each run ingests its own uniquely named article through the S3 ``ingest/`` path, so it never
touches the shared navigation fixtures.

Runs against the LIVE deployment; skipped unless ``RUN_INTEGRATION=1``. ``DELETE`` is a dashboard
route, so it also needs ``COGNITO_USERNAME``/``COGNITO_PASSWORD`` (never hardcoded).
"""

import json
import os
import time
import uuid

import pytest
import requests
from botocore.exceptions import ClientError

from tests.integration._partner_helpers import (
    BROWSER_UA,
    MAIN_STACK,
    RUN,
    json_body,
    partner_key_value,
    session,
    stack_outputs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
    pytest.mark.timeout(900),
]

TIMEOUT = 45
LICENSE_ID = os.environ.get("IAB_TEST_LICENSE_ID", "lic_default_v1")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")
PUBLISH_DEADLINE_S = 420
# The deindexer normally finishes within seconds; the collection's refresh interval is 5 s.
DEINDEX_DEADLINE_S = 90
POLL_S = 5


@pytest.fixture(scope="module")
def outputs():
    found = stack_outputs(MAIN_STACK)
    try:
        found = {**stack_outputs("CMCognitoStack"), **found}
    except Exception:  # noqa: BLE001 - Cognito outputs may come from the environment instead
        pass
    return found


@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    yield s
    s.close()


@pytest.fixture(scope="module")
def partner_base(outputs):
    url = outputs.get("PartnerApiUrl")
    if not url:
        pytest.skip("no PartnerApiUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def internal_base(outputs):
    url = outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no ApiGatewayUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def key_headers(outputs):
    value = partner_key_value(outputs)
    if not value:
        pytest.skip("no test partner key: deploy ContentMonetizationStack")
    return {"x-api-key": value}


@pytest.fixture(scope="module")
def cognito_headers(outputs):
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_USERNAME/COGNITO_PASSWORD to run the content delete (never hardcoded)")
    pool = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not pool or not client:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy CMCognitoStack")
    resp = (
        session()
        .client("cognito-idp")
        .admin_initiate_auth(
            UserPoolId=pool,
            ClientId=client,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
        )
    )
    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def content_table(outputs):
    return session().resource("dynamodb").Table(outputs.get("ContentTableName", "cm-content"))


def _comp_query(content_id):
    return {
        "aisystem": {
            "name": "content-delete-acceptance",
            "aisysuse": {
                "lid": LICENSE_ID,
                "aiauth": 2,
                "scope": 5,
                "ext": {"discovery": {"content_ids": [content_id]}},
            },
        }
    }


def _discoverable(http, base, headers, content_id):
    resp = http.post(f"{base}/query", json=_comp_query(content_id), headers=headers, timeout=TIMEOUT)
    assert resp.status_code == 200, resp.text[:300]
    return content_id in {asset["ext"]["content_id"] for asset in json_body(resp)["package"]["scope"].get("text", [])}


def _object_exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


@pytest.fixture(scope="module")
def published(outputs, content_table, http, partner_base, key_headers):
    """A fresh article, published and discoverable, owned by this module."""
    content_id = f"cnt_itdel_{uuid.uuid4().hex[:10]}"
    article = {
        "title": f"Content delete probe {content_id}",
        "content_type": "article",
        "content_markdown": f"# Content delete probe\n\nAn article the delete tier removes ({content_id}).",
        "author": "Integration Tier",
    }
    session().client("s3").put_object(
        Bucket=outputs["ContentBucketName"],
        Key=f"ingest/content-delete/{content_id}.json",
        Body=json.dumps(article).encode(),
        ContentType="application/json",
        Metadata={"content-id": content_id},
    )
    deadline = time.monotonic() + PUBLISH_DEADLINE_S
    while True:
        record = content_table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
        if (
            record
            and record.get("status") == "published"
            and _discoverable(http, partner_base, key_headers, content_id)
        ):
            return record
        if time.monotonic() >= deadline:
            pytest.fail(f"{content_id} not published and discoverable within {PUBLISH_DEADLINE_S}s: {record}")
        time.sleep(POLL_S * 2)


def test_delete_removes_the_document_its_objects_and_its_record(
    outputs, content_table, http, partner_base, internal_base, key_headers, cognito_headers, published
):
    content_id = published["content_id"]
    s3 = session().client("s3")
    bucket = outputs["ContentBucketName"]
    keys = [published[field] for field in ("md_s3_key", "source_s3_key", "embedding_s3_key") if published.get(field)]
    keys += [f"pipeline/{content_id}/{name}" for name in ("extracted.json", "enrichment.json")]
    assert published.get("md_s3_key") and published.get("source_s3_key") and published.get("embedding_s3_key"), (
        published
    )
    assert all(_object_exists(s3, bucket, key) for key in keys)

    accepted = http.delete(f"{internal_base}/content/{content_id}", headers=cognito_headers, timeout=TIMEOUT)
    assert accepted.status_code == 202, accepted.text[:300]
    body = accepted.json()
    body.pop("_validation", None)
    assert body == {"content_id": content_id, "status": "deleting"}

    # Partners lose the document at once: their reads serve only published content.
    read = http.get(f"{partner_base}/content", params={"content_id": content_id}, headers=key_headers, timeout=TIMEOUT)
    assert read.status_code == 404, read.text[:300]

    # The dashboard keeps the record, as deleting, until the deindexer finishes (if it hasn't yet).
    details = http.get(f"{internal_base}/content/{content_id}", headers=cognito_headers, timeout=TIMEOUT)
    assert details.status_code in (200, 404), details.text[:300]
    if details.status_code == 200:
        assert details.json()["status"] == "deleting"

    deadline = time.monotonic() + DEINDEX_DEADLINE_S
    while True:
        record = content_table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
        objects = [key for key in keys if _object_exists(s3, bucket, key)]
        discoverable = _discoverable(http, partner_base, key_headers, content_id)
        if record is None and not objects and not discoverable:
            break
        if time.monotonic() >= deadline:
            pytest.fail(
                f"delete of {content_id} incomplete after {DEINDEX_DEADLINE_S}s: "
                f"record={'present' if record else 'gone'} objects={objects} discoverable={discoverable}"
            )
        time.sleep(POLL_S)

    gone = http.delete(f"{internal_base}/content/{content_id}", headers=cognito_headers, timeout=TIMEOUT)
    assert gone.status_code == 404, gone.text[:300]
