"""Tests for the shared GEO/backfill OpenSearch projection builder."""

from services.search_projection import build_search_projection, normalize_topic_term


def test_projection_denormalizes_category_topics_and_section_ancestors():
    projection = build_search_projection(
        content_id="cnt_1",
        title="Health",
        body="Body",
        content_vector=[0.1],
        content_type="article",
        status="published",
        iab_categories=[
            {"id": 111, "name": "Medical Health", "tier_path": ["Medical Health", "Diseases"]},
            {"id": 111, "name": "Duplicate"},
        ],
        entities=[],
        topics=[" Machine   Learning ", "MACHINE LEARNING"],
        answer_capsule="Summary",
        language="en",
        license_id="lic_1",
        publish_date="2026-01-01T00:00:00Z",
        indexed_at="2026-01-02T00:00:00Z",
        markdown_schema_version="1.0",
        canonical_url="HTTPS://NEWS.EXAMPLE/health/cancer/article?ignored=yes",
        url_hash="hash",
        edge_eligible=True,
        md_s3_key="content/news.example/health/cancer/article.md",
        author="Author",
        word_count=10,
        cms_tags=["health"],
    )

    assert projection["iab_categories"] == ["111"]
    assert projection["iab_category_details"] == [
        {"id": "111", "name": "Medical Health", "tier_path": ["Medical Health", "Diseases"]}
    ]
    assert projection["topic_terms"] == ["machine learning"]
    assert projection["canonical_host"] == "news.example"
    assert projection["canonical_path_ancestors"] == ["/", "/health", "/health/cancer", "/health/cancer/article"]
    assert projection["status"] == "published"


def test_projection_without_canonical_url_is_not_section_addressable():
    projection = build_search_projection(
        content_id="cnt_1",
        title="No URL",
        body="Body",
        content_vector=[0.1],
        content_type="article",
        status="published",
        iab_categories=[],
        entities=[],
        topics=[],
        answer_capsule="",
        language="en",
        license_id="lic_1",
        publish_date=None,
        indexed_at="",
        markdown_schema_version="1.0",
        canonical_url=None,
        url_hash=None,
        edge_eligible=False,
        md_s3_key="content/cnt_1.md",
        author="",
        word_count=0,
        cms_tags=[],
    )

    assert projection["canonical_host"] == ""
    assert projection["canonical_path_ancestors"] == []


def test_topic_term_normalization_is_unicode_safe_and_idempotent():
    assert normalize_topic_term("  CAFÉ  ") == "café"
    assert normalize_topic_term("Cafe\u0301") == "café"
