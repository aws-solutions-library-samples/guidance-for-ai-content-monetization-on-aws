"""Deployed comparison delete: a comparison record goes together with its partition Markdown.

Spec scrape-vs-api-comparison R15.6 and task 8.6a. A comparison record is removed through the soft
delete (``DELETE /content/{content_id}`` marks it ``deleting``; the content deindexer finishes the
delete), never through a DynamoDB TTL. Its Markdown lives at ``comparison/<url_hash>.md``, outside
``content/``. The deindexer must be allowed to read and delete that prefix. Without that grant it
would skip the object (a ``403`` on the ownership read counts as "already gone") and remove the
record, leaving the Markdown behind.

This tier runs one comparison of the fixture article, deletes the resulting partition record through
the dashboard route, and waits until the record, the partition Markdown, the source object, the
embedding and the pipeline intermediates are gone. A second delete is ``404``.

Runs against the LIVE deployment; skipped unless ``RUN_INTEGRATION=1``. Both routes are dashboard
routes, so it also needs ``COGNITO_USERNAME``/``COGNITO_PASSWORD`` (never hardcoded). It lives in its
own module because every run of the fixture shares one partition record: the delete must not run in
the middle of ``test_comparison_runs.py``.
"""

import os
import time

import pytest
import requests
from botocore.exceptions import ClientError

from tests.integration._partner_helpers import BROWSER_UA, MAIN_STACK, RUN, session, stack_outputs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
    pytest.mark.timeout(600),
]

UI_STACK = "CMUIStack"
TIMEOUT = 60
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")
POLL_LIMIT_S = int(os.environ.get("COMPARISON_POLL_LIMIT_S", "240"))
# The deindexer normally finishes within seconds of the delete.
DEINDEX_DEADLINE_S = 120
POLL_S = 5


@pytest.fixture(scope="module")
def outputs() -> dict:
    found = stack_outputs(MAIN_STACK)
    try:
        found = {**stack_outputs("CMCognitoStack"), **found}
    except Exception:  # noqa: BLE001 - Cognito outputs may come from the environment instead
        pass
    return found


@pytest.fixture(scope="module")
def internal_base(outputs: dict) -> str:
    url = outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no ApiGatewayUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def fixture_url() -> str:
    url = os.environ.get("COMPARISON_FIXTURE_URL")
    if not url:
        try:
            url = stack_outputs(UI_STACK).get("ComparisonFixtureUrl")
        except Exception as exc:  # noqa: BLE001 - no UI stack => skip, not error
            pytest.skip(f"no fixture URL: set COMPARISON_FIXTURE_URL or deploy CMUIStack ({exc})")
    if not url:
        pytest.skip("no fixture URL: set COMPARISON_FIXTURE_URL or deploy CMUIStack")
    return url


@pytest.fixture(scope="module")
def cognito_headers(outputs: dict) -> dict:
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_USERNAME/COGNITO_PASSWORD to run the comparison delete (never hardcoded)")
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
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    yield s
    s.close()


def _object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _poll_until_ready(http, base: str, headers: dict, run_id: str) -> dict:
    deadline = time.monotonic() + POLL_LIMIT_S
    delay = 3.0
    last = None
    while time.monotonic() < deadline:
        resp = http.get(f"{base}/test/comparison-runs/{run_id}", headers=headers, timeout=TIMEOUT)
        assert resp.status_code == 200, f"poll {resp.status_code}: {resp.text[:300]}"
        last = resp.json()
        if last["status"] == "ready":
            return last
        time.sleep(delay)
        delay = min(delay * 1.5, 15.0)
    pytest.fail(f"run {run_id} not ready within {POLL_LIMIT_S}s; last status {last}")


def test_deleting_a_comparison_record_removes_its_partition_markdown(
    outputs, http, internal_base, cognito_headers, fixture_url
):
    submitted = http.post(
        f"{internal_base}/test/comparison-runs",
        json={"url": fixture_url, "reingest": False},
        headers=cognito_headers,
        timeout=TIMEOUT,
    )
    assert submitted.status_code == 202, submitted.text[:300]
    run = submitted.json()
    if run["ingest"] != "submitted":
        pytest.skip("the fixture is an owned, published article here: the run reused it, nothing to delete")
    content_id = _poll_until_ready(http, internal_base, cognito_headers, run["run_id"])["api"]["content_id"]

    table = session().resource("dynamodb").Table(outputs.get("ContentTableName", "cm-content"))
    record = table.get_item(Key={"content_id": content_id}, ConsistentRead=True)["Item"]
    assert record["status"] == "comparison", record.get("status")
    assert record["md_s3_key"].startswith("comparison/"), record["md_s3_key"]
    assert record["source_s3_key"].startswith("ingest/comparison/"), record["source_s3_key"]
    keys = [record[field] for field in ("md_s3_key", "source_s3_key", "embedding_s3_key") if record.get(field)]
    keys += [f"pipeline/{content_id}/{name}" for name in ("extracted.json", "enrichment.json")]
    s3 = session().client("s3")
    bucket = outputs["ContentBucketName"]
    assert all(_object_exists(s3, bucket, key) for key in keys), keys

    accepted = http.delete(f"{internal_base}/content/{content_id}", headers=cognito_headers, timeout=TIMEOUT)
    assert accepted.status_code == 202, accepted.text[:300]
    body = accepted.json()
    body.pop("_validation", None)
    assert body == {"content_id": content_id, "status": "deleting"}

    deadline = time.monotonic() + DEINDEX_DEADLINE_S
    while True:
        record = table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
        left = [key for key in keys if _object_exists(s3, bucket, key)]
        if record is None and not left:
            break
        if time.monotonic() >= deadline:
            pytest.fail(
                f"delete of {content_id} incomplete after {DEINDEX_DEADLINE_S}s: "
                f"record={'present' if record else 'gone'} objects left={left}"
            )
        time.sleep(POLL_S)

    gone = http.delete(f"{internal_base}/content/{content_id}", headers=cognito_headers, timeout=TIMEOUT)
    assert gone.status_code == 404, gone.text[:300]
