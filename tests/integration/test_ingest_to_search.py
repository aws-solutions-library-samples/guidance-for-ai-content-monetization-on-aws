"""
End-to-end integration test: ingest → classify → index → search.

The M2-equivalent gate (spec task 6.3). Uploads a sample document to the
content bucket's ingest/ prefix, waits for the Step Functions pipeline to
finish, and asserts:
  - a LATEST DynamoDB record exists with populated IAB 3.1 + GEO fields
  - the .md rendition was written to its deterministic canonical-host key
  - the document is retrievable from the OpenSearch hybrid index

Deterministic Markdown: the golden fixtures (HTML, Markdown, JSON) go through
the deployed pipeline under stable content ids, so reruns overwrite rather than
accumulate. The ingested items are kept as sandbox demo content. The golden files
hold the deterministic document; the stored .md bytes must equal it composed with
the record's generated summary and FAQ (schema 1.1),
`markdown_schema_version` must be persisted on the S3 object, the DynamoDB record
and the OpenSearch document, and the object key must follow the native-edge
Shape 1 address (`content/{host}/{path}.md`, `.../index.md` for a directory URL,
`content/{content_id}.md` when there is no canonical URL).

Runs against the LIVE deployment; skipped unless RUN_INTEGRATION=1 and AWS
credentials are present. Discovers resource names from CloudFormation outputs.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest
from content_pipeline.extractor import extract
from content_pipeline.markdown_renderer import DocumentMetadata, compose_document
from shared.utils.models import MARKDOWN_SCHEMA_VERSION

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

REGION = os.environ.get("AWS_REGION", "us-east-1")
POLL_TIMEOUT_S = 300
POLL_INTERVAL_S = 10

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "content_pipeline" / "fixtures" / "deterministic_markdown"
# Stable ids: reruns overwrite the same records and objects. content_id -> (fixture, golden output, Shape 1 key).
GOLDEN_CASES = {
    "cnt_golden10_html": ("article.html", "article.html.expected.md", "content/example.com/guides/markdown/index.md"),
    "cnt_golden10_json": ("article.json", "article.json.expected.md", "content/example.com/articles/2026/item.md"),
    # The Markdown fixture declares no canonical URL, so it takes the opaque fallback key.
    "cnt_golden10_md": ("article.md", "article.md.expected.md", "content/cnt_golden10_md.md"),
    # Schema 1.1: a news-style page (JSON-LD author list, share menu, promo cards, ad slots) and the
    # demo publisher's own article, whose og:url is relative, so it has no canonical URL either.
    "cnt_golden11_news": (
        "article-news.html",
        "article-news.html.expected.md",
        "content/news.example.com/business/port-strike-ends.md",
    ),
    "cnt_golden11_demo": ("article-demo.html", "article-demo.html.expected.md", "content/cnt_golden11_demo.md"),
}
_CONTENT_TYPES = {"html": "text/html", "json": "application/json", "md": "text/markdown"}


def _outputs(stack_name):
    cfn = boto3.client("cloudformation", region_name=REGION)
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


@pytest.fixture(scope="module")
def env():
    main = _outputs("ContentMonetizationStack")
    pipeline = _outputs("CMPipelineStack")
    # The OpenSearch collection + its endpoint output moved to the free-standing
    # top-level CMSearchStack (isolated so a SolutionVersion bump cannot replace
    # the collection). The pipeline still owns the state machine.
    search = _outputs("CMSearchStack")
    account = boto3.client("sts").get_caller_identity()["Account"]
    return {
        "bucket": main["ContentBucketName"],
        "table": main["ContentTableName"],
        "state_machine": pipeline["StateMachineArn"],
        "opensearch_endpoint": search["OpenSearchCollectionEndpoint"],
        "account": account,
    }


@pytest.fixture(scope="module")
def ingested(env):
    """Upload one sample doc under ingest/ with a run-unique content_id and
    block until its Step Functions execution reaches a terminal state, so the
    downstream assertions see a freshly-indexed record (not a stale prior run).
    """
    s3 = boto3.client("s3", region_name=REGION)
    sfn = boto3.client("stepfunctions", region_name=REGION)

    # Unique per run: RUN_ID (CI build id) or the fixed marker for local reuse
    run_suffix = os.environ.get("RUN_ID", "local").lower().replace("_", "")[:10]
    content_id = f"cnt_itest{run_suffix}"[:16]
    key = f"ingest/itest_{run_suffix}.html"
    sample_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "assets", "sample_content", "free_tier_ev_adoption.html"
    )
    with open(sample_path, "rb") as f:
        body = f.read()
    canonical_slug = f"ev-adoption-2026-{run_suffix}"
    body = body.replace(
        b"https://demopublisher.com/auto/ev-adoption-2026",
        f"https://demopublisher.com/auto/{canonical_slug}".encode("utf-8"),
    )
    md_key = f"content/demopublisher.com/auto/{canonical_slug}.md"

    before = {
        e["executionArn"]
        for e in sfn.list_executions(stateMachineArn=env["state_machine"], maxResults=25)["executions"]
    }
    s3.put_object(
        Bucket=env["bucket"], Key=key, Body=body, ContentType="text/html", Metadata={"content-id": content_id}
    )

    # Find the execution our upload triggered and wait for it to finish
    exec_arn = None
    deadline = time.time() + 120
    while time.time() < deadline and not exec_arn:
        time.sleep(8)
        for e in sfn.list_executions(stateMachineArn=env["state_machine"], maxResults=25)["executions"]:
            if e["executionArn"] not in before:
                exec_arn = e["executionArn"]
                break
    assert exec_arn, "no new pipeline execution observed after upload"

    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        status = sfn.describe_execution(executionArn=exec_arn)["status"]
        if status != "RUNNING":
            assert status == "SUCCEEDED", f"pipeline execution ended {status}"
            break
        time.sleep(POLL_INTERVAL_S)

    return {"content_id": content_id, "key": key, "md_key": md_key}


def _wait_for_record(env, content_id):
    table = boto3.resource("dynamodb", region_name=REGION).Table(env["table"])
    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        resp = table.get_item(Key={"content_id": content_id})
        if "Item" in resp:
            return resp["Item"]
        time.sleep(POLL_INTERVAL_S)
    return None


def _wait_for_executions(sfn, state_machine, before, keys, timeout=120):
    """Return {key: executionArn} for the new executions whose input names each uploaded key."""
    found = {}
    deadline = time.time() + timeout
    while time.time() < deadline and len(found) < len(keys):
        time.sleep(8)
        for e in sfn.list_executions(stateMachineArn=state_machine, maxResults=50)["executions"]:
            arn = e["executionArn"]
            if arn in before or arn in found.values():
                continue
            payload = sfn.describe_execution(executionArn=arn).get("input", "")
            for key in keys:
                if key not in found and key in payload:
                    found[key] = arn
    return found


@pytest.fixture(scope="module")
def golden_ingested(env):
    """Run the golden fixtures through the deployed pipeline under stable ids."""
    s3 = boto3.client("s3", region_name=REGION)
    sfn = boto3.client("stepfunctions", region_name=REGION)
    started_at = datetime.now(timezone.utc).isoformat()
    uploads = {}
    for content_id, (fixture, _expected, _key) in GOLDEN_CASES.items():
        ext = fixture.rsplit(".", 1)[1]
        uploads[f"ingest/golden-markdown-1.0/{content_id}.{ext}"] = (content_id, fixture, ext)

    before = {
        e["executionArn"]
        for e in sfn.list_executions(stateMachineArn=env["state_machine"], maxResults=50)["executions"]
    }
    for key, (content_id, fixture, ext) in uploads.items():
        s3.put_object(
            Bucket=env["bucket"],
            Key=key,
            Body=(GOLDEN_DIR / fixture).read_bytes(),
            ContentType=_CONTENT_TYPES[ext],
            Metadata={"content-id": content_id},
        )

    executions = _wait_for_executions(sfn, env["state_machine"], before, list(uploads))
    assert set(executions) == set(uploads), f"no pipeline execution observed for {set(uploads) - set(executions)}"
    deadline = time.time() + POLL_TIMEOUT_S
    pending = dict(executions)
    while pending and time.time() < deadline:
        for key, arn in list(pending.items()):
            status = sfn.describe_execution(executionArn=arn)["status"]
            if status != "RUNNING":
                assert status == "SUCCEEDED", f"pipeline execution for {key} ended {status}"
                del pending[key]
        if pending:
            time.sleep(POLL_INTERVAL_S)
    assert not pending, f"pipeline executions still running: {sorted(pending)}"
    return {"started_at": started_at}


def _fresh_record(env, content_id, since):
    """The record this run wrote (stable ids are overwritten, so a stale prior record must not pass)."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(env["table"])
    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        item = table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
        if item and str(item.get("updated_at", "")) >= since:
            return item
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(f"no fresh record for {content_id} since {since}")


def test_pipeline_persists_geo_record(env, ingested):
    item = _wait_for_record(env, ingested["content_id"])
    assert item is not None, "pipeline did not persist a LATEST record within timeout"

    # IAB 3.1 classification present
    assert item.get("iab_categories"), "no IAB categories on the record"
    assert item.get("cattax") == 9

    # GEO enrichment fields populated
    assert item.get("answer_capsule"), "answer_capsule is empty"
    assert "faq_schema" in item
    assert item.get("word_count", 0) > 0
    assert item.get("md_s3_key") == ingested["md_key"]
    assert item.get("edge_eligible") is True
    assert item.get("url_hash")


def test_md_rendition_written(env, ingested):
    s3 = boto3.client("s3", region_name=REGION)
    item = _wait_for_record(env, ingested["content_id"])
    obj = s3.get_object(Bucket=env["bucket"], Key=item["md_s3_key"])
    md = obj["Body"].read().decode("utf-8")
    assert md.startswith("#")
    assert len(md) > 50
    assert obj["Metadata"]["content-id"] == ingested["content_id"]
    assert obj["Metadata"]["canonical-url-hash"] == item["url_hash"]


def test_record_carries_the_markdown_objects_etag(env, ingested):
    """step_persist saves the stored object's S3 ETag, so a conditional read can answer from the record.

    The state machine hands each step an explicit payload; a field it doesn't map never reaches
    step_persist, and no unit test sees that.
    """
    s3 = boto3.client("s3", region_name=REGION)
    item = _wait_for_record(env, ingested["content_id"])
    live = s3.head_object(Bucket=env["bucket"], Key=item["md_s3_key"])["ETag"]
    assert item.get("md_etag") == live, (
        f"record md_etag {item.get('md_etag')!r} != the stored object's S3 ETag {live!r}: the pipeline "
        "did not carry the ETag to step_persist, so every conditional read falls back to S3"
    )


def test_pipeline_document_reads_share_an_etag_and_revalidate_to_304(env, ingested):
    """Both read modes give a pipeline document one ETag, and it revalidates to 304 in each.

    The ETag and 304 tests in test_url_retrieval_api.py read records written directly with an inline
    body. This document's body is the stored Markdown object, and its ETag comes from md_etag.
    """
    import requests
    from tests.integration._partner_helpers import BROWSER_UA, partner_key_value, stack_outputs

    item = _wait_for_record(env, ingested["content_id"])
    outputs = stack_outputs()
    base = outputs["PartnerApiUrl"].rstrip("/")
    headers = {"x-api-key": partner_key_value(outputs), "User-Agent": BROWSER_UA}
    selectors = {"content_id": {"content_id": ingested["content_id"]}, "url": {"url": item["canonical_url"]}}

    first = {
        mode: requests.get(f"{base}/content", params=p, headers=headers, timeout=45) for mode, p in selectors.items()
    }
    assert {mode: r.status_code for mode, r in first.items()} == {"content_id": 200, "url": 200}
    etags = {mode: r.headers.get("ETag") for mode, r in first.items()}
    assert etags["content_id"] and etags["content_id"] == etags["url"], f"ETag differs between modes: {etags}"

    for mode, params in selectors.items():
        revalidated = requests.get(
            f"{base}/content", params=params, headers={**headers, "If-None-Match": etags[mode]}, timeout=45
        )
        assert revalidated.status_code == 304, (
            f"{mode}: matching If-None-Match must be 304, got {revalidated.status_code}"
        )

    # Prove the 304 comes from the record: change the object under it (keeping its metadata), and a
    # read that fetched the object would answer 200. Then put the original bytes back, which
    # restores the original S3 ETag.
    s3 = boto3.client("s3", region_name=REGION)
    original = s3.get_object(Bucket=env["bucket"], Key=item["md_s3_key"])
    original_body = original["Body"].read()
    put = {"Bucket": env["bucket"], "Key": item["md_s3_key"], "Metadata": original["Metadata"]}
    put.update({k: original[k] for k in ("ContentType", "CacheControl") if original.get(k)})
    try:
        s3.put_object(Body=original_body + b"\n<!-- integration probe -->\n", **put)
        for mode, params in selectors.items():
            from_record = requests.get(
                f"{base}/content", params=params, headers={**headers, "If-None-Match": etags[mode]}, timeout=45
            )
            assert from_record.status_code == 304, (
                f"{mode}: with the object changed under an unchanged record, the conditional read "
                f"answered {from_record.status_code}; it fetched the object instead of using md_etag"
            )
    finally:
        s3.put_object(Body=original_body, **put)
    assert s3.head_object(Bucket=env["bucket"], Key=item["md_s3_key"])["ETag"] == item["md_etag"], (
        "restoring the original bytes did not restore the object's ETag"
    )


@pytest.mark.parametrize("content_id", sorted(GOLDEN_CASES))
def test_golden_markdown_bytes_metadata_and_shape1_key(env, golden_ingested, content_id):
    fixture, expected_name, expected_key = GOLDEN_CASES[content_id]
    item = _fresh_record(env, content_id, golden_ingested["started_at"])
    assert item["md_s3_key"] == expected_key
    assert item["markdown_schema_version"] == MARKDOWN_SCHEMA_VERSION
    assert item["edge_eligible"] is (not expected_key.startswith(f"content/{content_id}"))

    obj = boto3.client("s3", region_name=REGION).get_object(Bucket=env["bucket"], Key=expected_key)
    golden = (GOLDEN_DIR / expected_name).read_text(encoding="utf-8").rstrip("\n")
    body_markdown = extract((GOLDEN_DIR / fixture).read_bytes(), f"ingest/{fixture}")["body_markdown"]
    parts = {"title": item["title"], "body_markdown": body_markdown, "metadata": _record_metadata(item)}
    # The record's own fields rebuild the golden block and footer, so the block can't drift from it.
    assert compose_document(**parts) == golden, f"{fixture}: the record's fields don't rebuild its golden document"
    generated = item.get("processing_status") != "classification_failed"
    expected = compose_document(
        **parts,
        summary=item.get("answer_capsule", "") if generated else "",
        faq=item.get("faq_schema", []) if generated else (),
    )
    assert obj["Body"].read().decode("utf-8") == expected, (
        f"{fixture}: deployed Markdown differs from its golden document composed with the record's summary and FAQ"
    )

    assert obj["ContentType"] == "text/markdown; charset=utf-8"
    assert obj["Metadata"]["markdown-schema-version"] == MARKDOWN_SCHEMA_VERSION
    assert obj["Metadata"]["content-id"] == content_id
    if item["edge_eligible"]:
        assert obj["Metadata"]["canonical-url-hash"] == item["url_hash"]


def _record_metadata(item):
    return DocumentMetadata(
        author=item.get("author"),
        publisher=item.get("publisher"),
        published=item.get("publish_date"),
        updated=item.get("modified_date"),
        source=item.get("canonical_url"),
    )


def test_the_metadata_block_matches_the_record(env, golden_ingested):
    """Schema 1.1 builds the block from the fields the record stores, so the two agree."""
    item = _fresh_record(env, "cnt_golden11_news", golden_ingested["started_at"])
    assert item["author"] == "Ana Ruiz, Tom Lee"
    assert item["publisher"] == "Example Daily News"
    assert item["publish_date"] == "2026-11-02T08:30:00-05:00"
    assert item["modified_date"] == "2026-11-03T10:15:00-05:00"
    assert item["canonical_url"] == "https://news.example.com/business/port-strike-ends"
    assert item["title"] == "Port strike ends after 12 days"


def test_the_stored_document_carries_the_generated_summary(env, golden_ingested):
    """The composed-equality check passes vacuously without enrichment, so require a real capsule once."""
    item = _fresh_record(env, "cnt_golden11_news", golden_ingested["started_at"])
    assert item["processing_status"] == "completed", "enrichment failed, so no generated part was exercised"
    assert item.get("answer_capsule"), "the record has no answer capsule"
    obj = boto3.client("s3", region_name=REGION).get_object(Bucket=env["bucket"], Key=item["md_s3_key"])
    assert "\n\n> Summary (generated): " in obj["Body"].read().decode("utf-8")


def test_golden_schema_version_in_opensearch(env, golden_ingested, aoss_access):
    from services import opensearch_client
    from opensearchpy.exceptions import AuthorizationException

    for content_id in GOLDEN_CASES:
        _fresh_record(env, content_id, golden_ingested["started_at"])
    docs = {}
    for _ in range(10):
        try:
            hits = opensearch_client.filter_search(size=10, filters=[{"terms": {"content_id": sorted(GOLDEN_CASES)}}])
        except AuthorizationException:
            time.sleep(12)  # data-access policy propagation
            continue
        docs = {hit["content_id"]: hit for hit in hits}
        if set(docs) == set(GOLDEN_CASES):
            break
        time.sleep(10)
    assert set(docs) == set(GOLDEN_CASES), f"golden documents missing from OpenSearch: {set(GOLDEN_CASES) - set(docs)}"
    for content_id, (_fixture, _expected, expected_key) in GOLDEN_CASES.items():
        assert docs[content_id]["markdown_schema_version"] == MARKDOWN_SCHEMA_VERSION
        assert docs[content_id]["md_s3_key"] == expected_key


@pytest.fixture(scope="module")
def aoss_access(env):
    """Grant the test runner's IAM principal read access to the collection for this module only.

    In normal operation the partner API's /query path uses the shared Lambda role, which the
    pipeline stack already authorizes; the test principal gets a temporary data-access policy so
    the module is self-contained and portable across accounts.
    """
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
    os.environ["OPENSEARCH_ENDPOINT"] = env["opensearch_endpoint"]
    os.environ["REGION"] = REGION

    aoss = boto3.client("opensearchserverless", region_name=REGION)
    principal = _caller_role_arn()
    policy_name = "itest-search-access"
    policy_doc = json.dumps(
        [
            {
                "Rules": [
                    {
                        "ResourceType": "index",
                        "Resource": ["index/cm-content-search/*"],
                        "Permission": ["aoss:DescribeIndex", "aoss:UpdateIndex", "aoss:ReadDocument"],
                    },
                    {
                        "ResourceType": "collection",
                        "Resource": ["collection/cm-content-search"],
                        "Permission": ["aoss:DescribeCollectionItems"],
                    },
                ],
                "Principal": [principal],
            }
        ]
    )
    try:
        aoss.create_access_policy(name=policy_name, type="data", policy=policy_doc)
    except aoss.exceptions.ConflictException:
        pass
    time.sleep(30)  # AOSS data-access policies can take 30-60s to propagate
    try:
        yield
    finally:
        # Distinct client token each attempt — reusing the policy version as the
        # token triggers a ConflictException that silently skips the delete.
        try:
            aoss.delete_access_policy(name=policy_name, type="data", clientToken=f"itest-del-{int(time.time())}")
        except Exception:
            pass


def test_document_searchable_in_opensearch(env, ingested, aoss_access):
    """Direct hybrid-search check through the temporary data-access grant."""
    assert _wait_for_record(env, ingested["content_id"]) is not None

    from opensearchpy.exceptions import AuthorizationException
    from shared.bedrock_embeddings import generate_search_embedding
    from services import opensearch_client

    emb = generate_search_embedding("electric vehicle battery cost", "titan-embed-text-v2")
    assert emb["success"]
    # Retry covers both NextGen cold-start wake and policy-propagation 403s
    hits = []
    for _ in range(10):
        try:
            hits = opensearch_client.hybrid_search("electric vehicle battery cost", emb["embedding"], size=10)
        except AuthorizationException:
            time.sleep(12)
            continue
        if any(h.get("content_id") == ingested["content_id"] for h in hits):
            break
        time.sleep(10)
    ids = [h.get("content_id") for h in hits]
    assert ingested["content_id"] in ids, f"ingested doc not found in search hits: {ids}"


def _caller_role_arn() -> str:
    """Assumed-role STS ARN → the underlying IAM role ARN AOSS policies expect."""
    ident = boto3.client("sts").get_caller_identity()["Arn"]
    if ":assumed-role/" in ident:
        parts = ident.split("/")
        role_name = parts[1]
        account = ident.split(":")[4]
        return f"arn:aws:iam::{account}:role/{role_name}"
    return ident
