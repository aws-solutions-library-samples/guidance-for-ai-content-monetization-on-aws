"""Deployed managed-``llms.txt`` lifecycle (spec: llms-txt-management, task 12).

Two tiers, both gated on ``RUN_INTEGRATION=1``:

* **Read-only** (always): public ``GET /llms.txt`` serves identical Markdown bytes on the internal
  and partner planes, and all five ``/content/llms-txt*`` management operations reject a request
  without a Cognito token.
* **Lifecycle** (opt-in, ``IAB_LLMS_TXT_LIFECYCLE=1`` plus ``COGNITO_USERNAME``/``COGNITO_PASSWORD``):
  singleton lock, stale-lock reclaim, renderer-revision draft on an unchanged catalog,
  ``no_change`` rerun, ``If-Match`` edits, publish and idempotent republish, and propagation of the
  published bytes through the 60-second stage cache. It generates with Bedrock and **publishes**
  the generated draft (with its content unchanged), so it never runs implicitly: publication stays
  a deliberate operator action.

The two lock tests write a synthetic run record and the control record's active-run fields
directly in the jobs table — the only deterministic way to hold a lock or make one stale — and
restore or delete everything they wrote. Every write is conditional on no real run being active.
A real worker crash and the DLQ reconciliation path are verified manually; see
``assets/docs/managed-llms-txt.md``.
"""

import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
import requests
from botocore.exceptions import ClientError

# The marker key and the bootstrap/legacy object keys come from the modules that own them, so a
# rename cannot leave this gate silently checking a key nobody writes.
from infrastructure.custom_resources.llms_txt_bootstrap import MARKER as BOOTSTRAP_MARKER
from services.llms_txt_service import BOOTSTRAP_KEY, CONTROL_ID, LEGACY_PUBLIC_KEY, MANIFEST_RENDERER_REVISION

RUN = os.environ.get("RUN_INTEGRATION") == "1"
LIFECYCLE = os.environ.get("IAB_LLMS_TXT_LIFECYCLE") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]
lifecycle_only = pytest.mark.skipif(
    not LIFECYCLE, reason="set IAB_LLMS_TXT_LIFECYCLE=1 to generate and publish against the deployment"
)

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
STACK_NAME = os.environ.get("TEST_STACK_NAME", "ContentMonetizationStack")
COGNITO_STACK_NAME = os.environ.get("TEST_COGNITO_STACK_NAME", "CMCognitoStack")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")
BROWSER_UA = "Mozilla/5.0 (integration-test) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
TIMEOUT = 30
RUN_TIMEOUT_S = 600
CACHE_WINDOW_S = 60
TERMINAL = {"completed", "no_change", "failed"}
COMMITTED_BOOTSTRAP = (
    (Path(__file__).resolve().parents[2] / "assets" / "bootstrap-llms.txt")
    .read_text(encoding="utf-8")
    .rstrip("\n")
    .encode("utf-8")
)
MANAGEMENT_OPERATIONS = (
    ("GET", "/content/llms-txt"),
    ("POST", "/content/llms-txt/runs"),
    ("GET", "/content/llms-txt/runs/llms_itest_absent"),
    ("PUT", "/content/llms-txt/drafts/llms_itest_absent"),
    ("POST", "/content/llms-txt/drafts/llms_itest_absent/publish"),
)


def _stack_outputs(stack_name):
    try:
        stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=stack_name)["Stacks"][0]
    except Exception:  # noqa: BLE001 - no deployment -> skip
        return {}
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


@pytest.fixture(scope="module")
def outputs():
    found = {**_stack_outputs(COGNITO_STACK_NAME), **_stack_outputs(STACK_NAME)}
    if not found.get("ApiGatewayUrl") or not found.get("PartnerApiUrl"):
        pytest.skip(f"no deployed {STACK_NAME} outputs in {REGION}")
    return found


@pytest.fixture(scope="module")
def api_url(outputs):
    return outputs["ApiGatewayUrl"].rstrip("/")


@pytest.fixture(scope="module")
def partner_api_url(outputs):
    return outputs["PartnerApiUrl"].rstrip("/")


@pytest.fixture(scope="module")
def http():
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    yield session
    session.close()


@pytest.fixture(scope="module")
def id_token(outputs):
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_PASSWORD to run Cognito-authed lifecycle tests (never hardcoded)")
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client_id = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not user_pool_id or not client_id:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy the stack")
    resp = boto3.client("cognito-idp", region_name=REGION).admin_initiate_auth(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
    )
    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return token


@pytest.fixture(scope="module")
def manage(http, api_url, id_token):
    """Call a management operation with the Cognito token; returns the response."""

    def call(method, path, *, if_match=None, body=None):
        headers = {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}
        if if_match is not None:
            headers["If-Match"] = if_match
        return http.request(method, f"{api_url}{path}", headers=headers, json=body, timeout=TIMEOUT)

    return call


@pytest.fixture(scope="module")
def content_bucket(outputs):
    name = outputs.get("ContentBucketName")
    if not name:
        pytest.skip("no ContentBucketName output")
    return name


@pytest.fixture(scope="module")
def jobs_table(outputs):
    name = outputs.get("JobsTableName")
    if not name:
        pytest.skip("no JobsTableName output")
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


@pytest.fixture(scope="module")
def lock_timeout_seconds(outputs):
    """The ui_backend's lock window (start_run runs there); the service default when unset."""
    config = boto3.client("lambda", region_name=REGION).get_function_configuration(
        FunctionName=outputs["UIBackendLambdaName"]
    )
    return int(config.get("Environment", {}).get("Variables", {}).get("LLMS_TXT_LOCK_TIMEOUT_SECONDS", "25200"))


def _now_iso(offset_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _wait_idle(manage, timeout=RUN_TIMEOUT_S):
    """Block until no generation run is active, so the test never interrupts a real one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manage("GET", "/content/llms-txt")
        assert state.status_code == 200, state.text[:300]
        if state.json().get("active_run") is None:
            return state.json()
        time.sleep(10)
    pytest.fail("another llms.txt generation run stayed active; not interrupting it")


def _wait_run(manage, run_id, timeout=RUN_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = manage("GET", f"/content/llms-txt/runs/{run_id}")
        assert resp.status_code == 200, resp.text[:300]
        run = resp.json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(5)
    pytest.fail(f"llms.txt run {run_id} did not finish within {timeout}s")


def _start(manage):
    resp = manage("POST", "/content/llms-txt/runs")
    assert resp.status_code == 202, resp.text[:300]
    return resp.json()


def _read_object(bucket, key):
    """``(body, metadata)``, or ``None`` when the key is absent; skips when reads are not allowed.

    Skipping on AccessDenied matches ``_stack_outputs``: a CI role without object reads should not
    turn this gate red, only report that it could not run.
    """
    try:
        obj = boto3.client("s3", region_name=REGION).get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return None
        if code in {"AccessDenied", "403"}:
            pytest.skip(f"caller cannot read s3://{bucket}/{key}")
        raise
    return obj["Body"].read(), (obj.get("Metadata") or {})


def _public_bytes(http, url):
    resp = http.get(f"{url}/llms.txt", timeout=TIMEOUT)
    assert resp.status_code == 200, f"GET {url}/llms.txt -> {resp.status_code}"
    return resp


def _hold_lock(jobs_table, run_id, updated_at):
    """Point the control record at a synthetic active run; returns the fields to restore."""
    jobs_table.put_item(
        Item={
            "job_id": run_id,
            "run_id": run_id,
            "job_type": "llms_txt_generation",
            "status": "processing",
            "source": "manual",
            "created_by": "integration-test",
            "created_at": updated_at,
            "updated_at": updated_at,
            "attempt_count": 1,
        },
        ConditionExpression="attribute_not_exists(job_id)",
    )
    control = jobs_table.get_item(Key={"job_id": CONTROL_ID}, ConsistentRead=True).get("Item", {})
    jobs_table.update_item(
        Key={"job_id": CONTROL_ID},
        UpdateExpression="SET active_run_id = :run, active_run_status = :processing, updated_at = :at",
        ConditionExpression="attribute_not_exists(active_run_id)",
        ExpressionAttributeValues={":run": run_id, ":processing": "processing", ":at": updated_at},
    )
    return {"active_run_status": control.get("active_run_status", "completed"), "updated_at": control.get("updated_at")}


def _release_synthetic_lock(jobs_table, run_id, restore):
    """Undo _hold_lock if the control still points at the synthetic run; always drop the run."""
    values = {":run": run_id, ":status": restore["active_run_status"]}
    update = "SET active_run_status = :status"
    if restore.get("updated_at"):
        update += ", updated_at = :at"
        values[":at"] = restore["updated_at"]
    try:
        jobs_table.update_item(
            Key={"job_id": CONTROL_ID},
            UpdateExpression=update + " REMOVE active_run_id",
            ConditionExpression="active_run_id = :run",
            ExpressionAttributeValues=values,
        )
    except jobs_table.meta.client.exceptions.ConditionalCheckFailedException:
        pass  # the service already moved the lock on (the reclaim case)
    jobs_table.delete_item(Key={"job_id": run_id})


# --------------------------------------------------------------------------- #
# Read-only tier
# --------------------------------------------------------------------------- #
def test_public_manifest_is_identical_markdown_on_both_planes(http, api_url, partner_api_url):
    internal = _public_bytes(http, api_url)
    partner = _public_bytes(http, partner_api_url)
    for resp in (internal, partner):
        assert resp.headers["Content-Type"] == "text/markdown; charset=utf-8"
        assert resp.content.strip(), "public llms.txt must never be empty"
        assert resp.content.startswith(b"# ")
    assert internal.content == partner.content

    # The served manifest must not link the retired /search. This endpoint reads whichever source
    # `_read_initial_manifest()` resolves (the legacy root object, a published manifest, or the
    # bootstrap key), so on failure the two object-level tests below name the stale one.
    assert b"/search" not in internal.content, "the managed manifest must not link the deleted /search"


def test_deployed_bootstrap_object_is_the_reconciled_asset(content_bucket):
    """The #38 gate: assert the DEPLOYED bytes, which is exactly what the issue was about.

    The committed asset has been correct since MR !34; the defect was the object in the bucket. The
    reconciler's ``preserved`` branch is a SUCCESS and its response Data never reaches a stack
    event, so a deployment that refreshed nothing is otherwise indistinguishable from one that did.
    Reading the object directly also skips the 60-second stage cache that makes the public-endpoint
    check above unreliable immediately after a deploy.

    Where the object carries this deployment's provenance marker it must be byte-identical to the
    bundled asset. An unmarked object is the design's operator-authored case, which the reconciler
    deliberately preserves — but it must still never advertise a retired endpoint.
    """
    read = _read_object(content_bucket, BOOTSTRAP_KEY)
    assert read is not None, (
        f"the bootstrap custom resource left s3://{content_bucket}/{BOOTSTRAP_KEY} absent; "
        "check the LlmsTxtBootstrap provider log for an AccessDenied on the seeding branch"
    )
    body, metadata = read

    assert b"/search" not in body, (
        f"s3://{content_bucket}/{BOOTSTRAP_KEY} still advertises the deleted /search; the "
        "reconciler reported `preserved`, so these bytes are not a seed it can prove it wrote"
    )
    if metadata.get(BOOTSTRAP_MARKER):
        assert body == COMMITTED_BOOTSTRAP, "a marked bootstrap object must match assets/bootstrap-llms.txt"
        assert metadata[BOOTSTRAP_MARKER] == hashlib.sha256(body).hexdigest()


def test_no_legacy_root_llms_txt_shadows_the_bootstrap_object(content_bucket):
    """``_read_initial_manifest()`` reads the legacy root key BEFORE the bootstrap key, so a root
    object left by an older deployment keeps serving its own text however well the reconciler does
    its job. Task 9.8.3's open question, converted from a manual checklist item into a gate."""
    read = _read_object(content_bucket, LEGACY_PUBLIC_KEY)
    if read is None:
        return  # the expected state: no legacy object, the bootstrap key is the fail-safe source
    body, _ = read

    assert b"/search" not in body, (
        f"a legacy root s3://{content_bucket}/{LEGACY_PUBLIC_KEY} advertises the deleted /search "
        "and takes precedence over the reconciled bootstrap object. Nothing in the codebase writes "
        "that key: delete it, or publish a managed manifest, so the public endpoint stops serving it"
    )


@pytest.mark.parametrize(("method", "path"), MANAGEMENT_OPERATIONS)
def test_management_operations_require_cognito(http, api_url, method, path):
    resp = http.request(method, f"{api_url}{path}", json={} if method != "GET" else None, timeout=TIMEOUT)
    assert resp.status_code == 401, f"{method} {path} without a token -> {resp.status_code}"


def test_management_state_readable_with_cognito(manage):
    resp = manage("GET", "/content/llms-txt")
    assert resp.status_code == 200, resp.text[:300]
    state = resp.json()
    assert {"published", "active_run", "draft"} <= set(state)
    assert state["published"]["content"].startswith("# ")


# --------------------------------------------------------------------------- #
# Lifecycle tier (opt-in: generates with Bedrock and publishes)
# --------------------------------------------------------------------------- #
@lifecycle_only
def test_singleton_lock_returns_the_active_run(manage, jobs_table):
    _wait_idle(manage)
    run_id = f"llms_itest_held_{uuid.uuid4().hex[:12]}"
    restore = _hold_lock(jobs_table, run_id, _now_iso())
    try:
        started = _start(manage)
        assert started["already_running"] is True
        assert started["run"]["run_id"] == run_id
        assert started["run"]["status"] == "processing"
    finally:
        _release_synthetic_lock(jobs_table, run_id, restore)
    assert manage("GET", "/content/llms-txt").json()["active_run"] is None


@lifecycle_only
def test_stale_lock_is_reclaimed_and_the_stale_run_failed(manage, jobs_table, lock_timeout_seconds):
    _wait_idle(manage)
    stale_id = f"llms_itest_stale_{uuid.uuid4().hex[:12]}"
    restore = _hold_lock(jobs_table, stale_id, _now_iso(-(lock_timeout_seconds + 3600)))
    try:
        started = _start(manage)
        assert started["already_running"] is False
        new_run_id = started["run"]["run_id"]
        assert new_run_id != stale_id

        stale = manage("GET", f"/content/llms-txt/runs/{stale_id}")
        assert stale.status_code == 200, stale.text[:300]
        assert stale.json()["status"] == "failed"
        assert stale.json()["error_code"] == "stale_run_reclaimed"

        finished = _wait_run(manage, new_run_id)
        assert finished["status"] in {"completed", "no_change"}, finished
    finally:
        _release_synthetic_lock(jobs_table, stale_id, restore)
    assert manage("GET", "/content/llms-txt").json()["active_run"] is None


@lifecycle_only
def test_renderer_draft_edit_publish_and_propagation(manage, jobs_table, http, api_url, partner_api_url):
    _wait_idle(manage)
    # An older renderer revision on an unchanged catalog must still yield a new draft (Q6/R6).
    jobs_table.update_item(
        Key={"job_id": CONTROL_ID},
        UpdateExpression="SET latest_renderer_revision = :older",
        ConditionExpression="attribute_not_exists(active_run_id)",
        ExpressionAttributeValues={":older": "integration-test-previous-renderer"},
    )
    started = _start(manage)
    assert started["already_running"] is False
    run = _wait_run(manage, started["run"]["run_id"])
    assert run["status"] == "completed", run
    assert run["renderer_revision"] == MANIFEST_RENDERER_REVISION
    draft = run["draft"]
    assert draft["status"] == "in_review"
    assert draft["renderer_revision"] == MANIFEST_RENDERER_REVISION
    assert draft["snapshot_fingerprint"] == run["snapshot_fingerprint"]
    assert isinstance(draft["diff"], str)
    content = draft["content"]
    assert content.startswith("# ") and not content.endswith("\n")
    assert "/query`" in content and "/info`" in content, "managed Access must link /info and /query"
    assert "/search" not in content

    # Same catalog and renderer: no_change, and the reviewed draft stays the latest one.
    rerun = _wait_run(manage, _start(manage)["run"]["run_id"])
    assert rerun["status"] == "no_change", rerun
    assert rerun["snapshot_fingerprint"] == run["snapshot_fingerprint"]
    assert manage("GET", "/content/llms-txt").json()["draft"]["run_id"] == draft["run_id"]

    # Conditional edit: the current ETag creates a new immutable revision; a stale one is refused.
    edited = manage(
        "PUT", f"/content/llms-txt/drafts/{draft['run_id']}", if_match=draft["etag"], body={"content": content}
    )
    assert edited.status_code == 200, edited.text[:300]
    revision = edited.json()
    assert revision["revision"] == draft["revision"] + 1
    assert revision["etag"] != draft["etag"]
    assert revision["content"] == content
    stale_edit = manage(
        "PUT", f"/content/llms-txt/drafts/{draft['run_id']}", if_match=draft["etag"], body={"content": content}
    )
    assert stale_edit.status_code == 412, stale_edit.text[:300]
    stale_publish = manage("POST", f"/content/llms-txt/drafts/{draft['run_id']}/publish", if_match=draft["etag"])
    assert stale_publish.status_code == 412, stale_publish.text[:300]

    # Publish moves the public pointer; repeating the same publish is idempotent.
    published = manage("POST", f"/content/llms-txt/drafts/{draft['run_id']}/publish", if_match=revision["etag"])
    assert published.status_code == 200, published.text[:300]
    assert published.json()["status"] == "published"
    again = manage("POST", f"/content/llms-txt/drafts/{draft['run_id']}/publish", if_match=revision["etag"])
    assert again.status_code == 200, again.text[:300]
    assert again.json()["status"] == "published"
    state = manage("GET", "/content/llms-txt").json()
    assert state["published"]["run_id"] == draft["run_id"]
    assert state["published"]["revision"] == revision["revision"]
    assert state["published"]["content"] == content

    # Public bytes on both planes converge within the stage cache window, anonymous and authed.
    expected = content.encode("utf-8")
    deadline = time.monotonic() + CACHE_WINDOW_S + 30
    observed = {}
    while time.monotonic() < deadline:
        observed = {base: _public_bytes(http, base).content for base in (api_url, partner_api_url)}
        if all(body == expected for body in observed.values()):
            break
        time.sleep(5)
    assert all(body == expected for body in observed.values()), (
        "published bytes did not propagate within the cache window"
    )
    authed = manage("GET", "/llms.txt")
    assert authed.status_code == 200 and authed.content == expected
