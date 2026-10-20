"""Unit tests for the ContentMetadata dataclass and its DynamoDB item shape."""

from decimal import Decimal

from shared.utils.models import CATTAX_IAB_3_1, MARKDOWN_SCHEMA_VERSION, ContentMetadata


def _record(**overrides):
    base = dict(
        content_id="cnt_abc123",
        title="Test",
        content_type="article",
        quality_score=0.83,
        answer_capsule="A 40-word capsule.",
    )
    base.update(overrides)
    return ContentMetadata(**base)


def test_defaults_and_cattax_constant():
    r = _record()
    assert r.cattax == CATTAX_IAB_3_1 == 9
    assert r.license_id == "lic_default_v1"
    assert r.markdown_schema_version == MARKDOWN_SCHEMA_VERSION == "1.0"
    assert r.edge_eligible is False
    # citation_required removed — it is license-derived, not a content-record field (MR !6 §2.3)
    assert not hasattr(r, "citation_required")
    assert r.iab_categories == [] and r.entities == []  # default_factory, not shared


def test_to_item_converts_floats_to_decimal():
    item = _record(quality_score=0.83).to_item()
    assert isinstance(item["quality_score"], Decimal)
    assert item["quality_score"] == Decimal("0.83")


def test_to_item_drops_none_values():
    item = _record(publish_date=None, canonical_url=None).to_item()
    assert "publish_date" not in item
    assert "canonical_url" not in item
    # Required fields survive
    assert item["content_id"] == "cnt_abc123"
    # Single-key table (ADR-004): no version sort key
    assert "version" not in item


def test_14_comp_geo_fields_present():
    """The persist step must emit the full CoMP/GEO field set (item1b bucketing)."""
    item = _record(
        word_count=250,
        language="en",
        answer_capsule="cap",
        faq_schema=[{"question": "q", "answer": "a"}],
        entities=[{"name": "X", "type": "org", "relevance": 0.9}],
        topics=["t"],
        sentiment={"polarity": "neutral", "score": 0.0},
        canonical_url="https://x.com/a",
        url_hash="abc123",
        edge_eligible=True,
    ).to_item()
    # citation_required is intentionally absent — license-derived, read from the
    # License file schema by the Package builder, not stored here (MR !6 §2.3).
    for field in (
        "cattax",
        "word_count",
        "language",
        "sourcetype",
        "provenance",
        "license_id",
        "license_url",
        "canonical_url",
        "url_hash",
        "edge_eligible",
        "answer_capsule",
        "faq_schema",
        "iab_categories",
        "entities",
        "quality_score",
    ):
        assert field in item, f"missing CoMP/GEO field: {field}"
    assert "citation_required" not in item


def test_single_key_no_version_or_ttl():
    """ADR-004: content records are single-key (content_id only). No version sort
    key and no expires_at TTL — re-processing overwrites the record in place."""
    item = _record().to_item()
    assert "version" not in item
    assert "expires_at" not in item
    # Re-processing the same content_id yields the same DynamoDB key
    again = _record(title="Re-classified").to_item()
    assert again["content_id"] == item["content_id"]
