"""Branch coverage for the deploy-time llms.txt bootstrap reconciler.

The handler in ``infrastructure/custom_resources/llms_txt_bootstrap.py`` is the custom resource's
packaged Lambda asset, so these tests exercise exactly the code that deploys. Every
branch is a conditional S3 write, which the quality gates classify as integration-level behavior, so
the reads/writes run against a moto-backed, VERSIONED bucket (matching the real content bucket):
that is what lets "no write" be asserted as "no new object version" rather than merely as a
response field a mock could fabricate.

Branch matrix (spec: llms-txt-management, design Bootstrap):
- absent                                      -> seeded
- stored bytes already equal the bundle       -> unchanged, and NO PutObject
- pre-marker shipped bootstrap text           -> refreshed (the one-time migration)
- marker equals the stored digest             -> refreshed
- no marker, unknown bytes                    -> preserved, request still succeeds
- marker disagrees with the stored digest     -> preserved (edited after a marked seed)
- the object changes under the write           -> preserved (412 on the conditional write)
- Delete                                      -> no S3 call, stable physical id
- properties without a Manifest               -> skipped (UPDATE_ROLLBACK), no S3 call
- non-missing S3 error                        -> propagates, so the provider sends FAILED
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError, ParamValidationError
from moto import mock_aws

from infrastructure.custom_resources import llms_txt_bootstrap as h

BUCKET = "iab-llm-content-000000000000-us-west-2"
REGION = "us-west-2"
REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED = (REPO_ROOT / "assets" / "bootstrap-llms.txt").read_text(encoding="utf-8").rstrip("\n")

# The two bootstrap texts this repository shipped before the marker existed, inlined as literals
# because they are HISTORY: these are the bytes stale deployed objects actually hold, and the
# handler's migration list is documented as closed. Deriving them from the live asset instead
# (LEGACY_SEED = BUNDLED + the /search line) only agreed with the handler while the asset stayed
# byte-identical to v2, so the next routine asset edit would fail these tests and push a maintainer
# into adding a never-shipped digest to a closed set — weakening the provenance guard.
LEGACY_SEED = (  # v1, c08f651: the stale text that still advertises the retired /search
    "# Publisher Content\n\n"
    "> A curated map of the publisher's most important AI-accessible content.\n\n"
    "## Optional\n\nNo optional content is currently listed.\n\n"
    "## Access\n\n- Discovery API: `/info`\n- Content query: `/query`\n- Semantic search: `/search`"
)
PRE_MARKER_V2 = LEGACY_SEED.replace("\n- Semantic search: `/search`", "")  # 92e0c91, /search dropped
PRE_MARKER_DIGESTS = (
    "3dae761798b7103438f3b2795b3d9217834a16e5071989ed3bb5b1f82e73fb5e",
    "8e1a1d439bc83ec710fb701f8d3910fc70259b4c27fcc6557f1481a4804054e2",
)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


@pytest.fixture(autouse=True)
def bucket_env(monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)


def _store(s3, text, marker=None):
    s3.put_object(
        Bucket=BUCKET,
        Key=h.KEY,
        Body=text.encode("utf-8"),
        Metadata={h.MARKER: marker} if marker else {},
    )


def _invoke(request_type="Update", manifest=BUNDLED):
    return h.handler({"RequestType": request_type, "ResourceProperties": {"Manifest": manifest}}, None)


class _RecordingClient:
    """The moto client, with PutObject kwargs recorded and an optional write injected first.

    Wrapping the real client (rather than mocking it) keeps S3's own conditional-write semantics in
    play: a 412 comes from moto evaluating the precondition, not from a fixture asserting it.
    """

    def __init__(self, client, on_put=None):
        self._client = client
        self._on_put = on_put
        self.puts = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        if self._on_put is not None:
            self._on_put()
        return self._client.put_object(**kwargs)


@pytest.fixture
def proxied(s3, monkeypatch):
    def install(on_put=None):
        client = _RecordingClient(s3, on_put)
        monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))
        return client

    return install


def _stored(s3):
    obj = s3.get_object(Bucket=BUCKET, Key=h.KEY)
    return obj["Body"].read().decode("utf-8"), obj


def _version_count(s3):
    return len(s3.list_object_versions(Bucket=BUCKET, Prefix=h.KEY).get("Versions", []))


# --------------------------------------------------------------------------- #
# Absent -> seeded
# --------------------------------------------------------------------------- #
def test_absent_key_is_seeded_with_the_bundled_manifest(s3):
    result = _invoke("Create")

    assert result["Data"]["BootstrapAction"] == "seeded"
    assert result["Data"]["Sha256"] == _digest(BUNDLED)
    assert result["PhysicalResourceId"] == h.PHYSICAL_ID
    text, obj = _stored(s3)
    assert text == BUNDLED
    assert obj["Metadata"][h.MARKER] == _digest(BUNDLED)


def test_seeded_object_carries_the_public_read_metadata(s3):
    _invoke("Create")

    _, obj = _stored(s3)
    assert obj["ContentType"] == "text/markdown; charset=utf-8"
    assert obj["CacheControl"] == "public, max-age=60"
    assert obj["ServerSideEncryption"] == "AES256"


def test_seeded_bootstrap_does_not_advertise_the_retired_search_endpoint(s3):
    _invoke("Create")

    text, _ = _stored(s3)
    assert "/search" not in text
    assert "/info" in text and "/query" in text


# --------------------------------------------------------------------------- #
# Already current -> no write at all
# --------------------------------------------------------------------------- #
def test_matching_bytes_are_reported_unchanged(s3):
    _store(s3, BUNDLED, marker=_digest(BUNDLED))

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "unchanged"
    assert result["Data"]["Sha256"] == _digest(BUNDLED)


def test_matching_bytes_write_no_new_object_version(s3):
    """A redeploy on an unchanged asset must not accrue an object version per deployment."""
    _store(s3, BUNDLED, marker=_digest(BUNDLED))
    before = _version_count(s3)

    _invoke()
    _invoke()

    assert _version_count(s3) == before == 1


def test_matching_bytes_need_no_marker(s3):
    """Byte equality is decided before provenance, so an unmarked but correct object is left alone."""
    _store(s3, BUNDLED)

    assert _invoke()["Data"]["BootstrapAction"] == "unchanged"
    assert _version_count(s3) == 1


# --------------------------------------------------------------------------- #
# Provably an unedited seed -> refreshed
# --------------------------------------------------------------------------- #
def test_pre_marker_shipped_bootstrap_is_refreshed(s3):
    """The one-time migration: the stale deployed seed predates the marker, so it has none."""
    _store(s3, LEGACY_SEED)
    assert _digest(LEGACY_SEED) in h.PRE_MARKER_SEEDS

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "refreshed"
    text, obj = _stored(s3)
    assert text == BUNDLED
    assert "/search" not in text
    assert obj["Metadata"][h.MARKER] == _digest(BUNDLED)


def test_refresh_retains_the_prior_version_in_the_versioned_bucket(s3):
    _store(s3, LEGACY_SEED)
    original = s3.list_object_versions(Bucket=BUCKET, Prefix=h.KEY)["Versions"][0]["VersionId"]

    _invoke()

    assert _version_count(s3) == 2
    prior = s3.get_object(Bucket=BUCKET, Key=h.KEY, VersionId=original)["Body"].read().decode("utf-8")
    assert prior == LEGACY_SEED


def test_marked_unmodified_seed_is_refreshed_when_the_bundle_moves_on(s3):
    """Post-migration steady state: the marker matches the stored bytes, so they are our seed."""
    superseded = "# Publisher Content\n\n> An older managed manifest."
    _store(s3, superseded, marker=_digest(superseded))

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "refreshed"
    text, obj = _stored(s3)
    assert text == BUNDLED
    assert obj["Metadata"][h.MARKER] == _digest(BUNDLED)


# --------------------------------------------------------------------------- #
# Not provably a seed -> preserved
# --------------------------------------------------------------------------- #
def test_operator_authored_content_is_preserved(s3):
    authored = "# Example Publisher\n\n> Hand-written by the operator.\n\n## Access\n\n- Discovery API: `/info`"
    _store(s3, authored)

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "preserved"
    assert result["Data"]["Sha256"] == _digest(authored)
    text, _ = _stored(s3)
    assert text == authored
    assert _version_count(s3) == 1


def test_preserving_operator_content_still_succeeds(s3):
    """A hand-edited bootstrap must never fail the deployment."""
    _store(s3, "# Example Publisher\n\n> Hand-written.")

    assert _invoke()["PhysicalResourceId"] == h.PHYSICAL_ID


def test_edit_after_a_marked_seed_is_preserved(s3):
    """The marker records what we wrote; an edit changes the bytes, so the digests disagree."""
    seeded = "# Publisher Content\n\n> An older managed manifest."
    _store(s3, seeded + "\n\n## Our own section\n\nKeep me.", marker=_digest(seeded))

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "preserved"
    text, _ = _stored(s3)
    assert text.endswith("Keep me.")


# --------------------------------------------------------------------------- #
# Delete, response protocol, and error propagation
# --------------------------------------------------------------------------- #
def test_delete_is_a_no_op_and_touches_no_object(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    result = h.handler({"RequestType": "Delete", "ResourceProperties": {"Manifest": BUNDLED}}, None)

    assert result == {"PhysicalResourceId": h.PHYSICAL_ID}
    client.get_object.assert_not_called()
    client.put_object.assert_not_called()


@pytest.mark.parametrize("request_type", ["Create", "Update", "Delete"])
def test_every_request_type_returns_the_stable_physical_id(s3, request_type):
    """The provider framework keys the resource on this id; a changed id would mean a replacement."""
    assert _invoke(request_type)["PhysicalResourceId"] == h.PHYSICAL_ID


def test_non_missing_s3_error_propagates_so_the_provider_reports_failed(monkeypatch):
    client = MagicMock()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject"
    )
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    with pytest.raises(ClientError) as excinfo:
        _invoke()

    assert excinfo.value.response["Error"]["Code"] == "AccessDenied"
    client.put_object.assert_not_called()


def test_properties_without_a_manifest_are_skipped_not_failed(monkeypatch):
    """CloudFormation re-sends the PRE-update properties during UPDATE_ROLLBACK.

    The template this change replaces carried ``{"BootstrapVersion": "2"}`` and no manifest, so a
    rollback of any later resource in the stack invokes this handler with that shape. Raising
    would make the provider framework submit FAILED, and a FAILED rollback leaves the stack in
    UPDATE_ROLLBACK_FAILED — wedged until someone runs continue-update-rollback by hand.
    """
    client = MagicMock()
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    result = h.handler(
        {"RequestType": "Update", "ResourceProperties": {"ServiceToken": "arn:...", "BootstrapVersion": "2"}},
        None,
    )

    assert result["PhysicalResourceId"] == h.PHYSICAL_ID
    assert result["Data"]["BootstrapAction"] == "skipped"
    client.get_object.assert_not_called()
    client.put_object.assert_not_called()


# --------------------------------------------------------------------------- #
# The write is conditional on the state the decision was made from
# --------------------------------------------------------------------------- #
def test_seed_write_is_conditional_on_the_key_still_being_absent(monkeypatch):
    client = MagicMock()
    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    assert _invoke()["Data"]["BootstrapAction"] == "seeded"
    assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_refresh_write_is_conditional_on_the_etag_that_was_read(s3, proxied):
    _store(s3, LEGACY_SEED)
    etag = s3.head_object(Bucket=BUCKET, Key=h.KEY)["ETag"]
    proxy = proxied()

    assert _invoke()["Data"]["BootstrapAction"] == "refreshed"

    assert proxy.puts[0]["IfMatch"] == etag


def test_a_write_that_loses_the_race_preserves_the_other_writer(s3, proxied):
    """The provenance guard decides from bytes read earlier in the invocation, so without a
    precondition an operator edit landing in between is silently clobbered — the one outcome the
    guard exists to prevent. The versioned bucket would keep their text only as a dead version."""
    _store(s3, LEGACY_SEED)
    authored = "# Example Publisher\n\n> Saved by the operator mid-deployment."
    proxied(on_put=lambda: _store(s3, authored))  # lands after the GetObject, before the write

    result = _invoke()

    assert result["Data"]["BootstrapAction"] == "preserved"
    assert _stored(s3)[0] == authored


def test_a_runtime_sdk_without_conditional_writes_still_writes(monkeypatch):
    """The function runs on the managed runtime's boto3 (the asset bundles no SDK), whose version Lambda controls.

    If that SDK predates conditional writes on PutObject it raises ParamValidationError before any
    request is sent; degrading to an unconditional write keeps the #38 refresh working instead of
    failing the deployment on a parameter the handler only uses defensively.
    """
    client = MagicMock()
    client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    client.put_object.side_effect = [ParamValidationError(report="Unknown parameter IfNoneMatch"), None]
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    assert _invoke()["Data"]["BootstrapAction"] == "seeded"
    assert client.put_object.call_count == 2
    assert "IfNoneMatch" not in client.put_object.call_args.kwargs
    assert client.put_object.call_args.kwargs["Body"] == BUNDLED.encode("utf-8")


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_each_missing_object_code_is_treated_as_absent(monkeypatch, code):
    client = MagicMock()
    client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
    monkeypatch.setattr(h.boto3, "client", MagicMock(return_value=client))

    assert _invoke()["Data"]["BootstrapAction"] == "seeded"
    assert client.put_object.call_args.kwargs["Body"] == BUNDLED.encode("utf-8")


# --------------------------------------------------------------------------- #
# The closed pre-marker migration list
# --------------------------------------------------------------------------- #
def test_pre_marker_list_is_exactly_the_texts_shipped_before_the_marker():
    """Pins the closed set against literal history, so dropping an entry — and stranding a
    deployed seed on the retired /search forever — fails here."""
    assert (_digest(LEGACY_SEED), _digest(PRE_MARKER_V2)) == PRE_MARKER_DIGESTS
    assert set(h.PRE_MARKER_SEEDS) == set(PRE_MARKER_DIGESTS)


def test_a_future_bundled_asset_needs_no_new_pre_marker_entry(s3):
    """Why the set can stay closed: everything the reconciler writes carries the marker, so a
    bundle that moves on again is reached by the marker branch, never by the migration list.

    This is the routine edit that #38 itself was: the Access section changes with every endpoint
    change. It must not require touching PRE_MARKER_SEEDS.
    """
    future = BUNDLED + "\n- Content metadata: `/info/content`"
    _store(s3, BUNDLED, marker=_digest(BUNDLED))  # what the previous deployment left behind

    result = h.handler({"RequestType": "Update", "ResourceProperties": {"Manifest": future}}, None)

    assert result["Data"]["BootstrapAction"] == "refreshed"
    assert _stored(s3)[0] == future
    assert _digest(future) not in h.PRE_MARKER_SEEDS
