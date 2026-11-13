"""Unit tests for the extraction step (HTML / MD / JSON)."""

import json

import pytest

from extractor import extract


def test_html_extraction_scrapes_head_and_converts(sample_html):
    result = extract(sample_html, "ingest/ev.html")
    assert result["format"] == "html"
    assert result["title"] == "EV Costs Fall"
    assert result["canonical_url"] == "https://x.com/ev"
    assert result["publish_date"] == "2026-03-04T09:00:00Z"
    assert result["author"] == "Priya Raman"
    # Markdown keeps the heading; text is stripped of markup and nav/script
    assert result["markdown"].startswith("# EV Costs Fall")
    assert "eighty dollars" in result["text"]
    assert "tracking()" not in result["text"]
    assert "skip me" not in result["text"]


def test_markdown_extraction_uses_first_heading_as_title():
    md = b"# Diabetes Screening\n\nCGMs move into routine screening in 2026."
    result = extract(md, "ingest/note.md")
    assert result["format"] == "md"
    assert result["title"] == "Diabetes Screening"
    assert "routine screening" in result["text"]


def test_json_post_content_shape():
    payload = json.dumps(
        {
            "title": "Semiconductor Capex",
            "content_type": "article",
            "content_text": "Foundries bet on advanced packaging in 2026.",
            "canonical_url": "https://x.com/chips",
            "tags": ["semiconductors"],
            "author": "Daniel Okoro",
        }
    ).encode()
    result = extract(payload, "ingest/report.json")
    assert result["format"] == "json"
    assert result["title"] == "Semiconductor Capex"
    assert result["content_type"] == "article"
    assert result["canonical_url"] == "https://x.com/chips"
    assert result["cms_tags"] == ["semiconductors"]
    assert "Semiconductor Capex" not in result["text"]
    assert "advanced packaging" in result["text"]


def test_json_title_metadata_is_unescaped_and_excluded_from_body_text():
    payload = json.dumps(
        {
            "title": "C# Guide [2026]",
            "content_type": "report",
            "content_text": "Body text only.",
        }
    ).encode()

    result = extract(payload, "ingest/report.json")

    assert result["title"] == "C# Guide [2026]"
    assert result["content_type"] == "report"
    assert result["markdown"].startswith(r"# C\# Guide \[2026\]")
    assert result["text"] == "Body text only."


def test_json_cms_export_shape():
    payload = json.dumps(
        {
            "article": {
                "id": "a1",
                "metadata": {
                    "publishDate": "2024-04-15T10:30:00Z",
                    "author": {"name": "Jane"},
                    "tags": ["health"],
                    "canonicalUrl": "https://x.com/a1",
                },
                "content": {"headline": "AI in Healthcare", "body": "New models detect disease early."},
            }
        }
    ).encode()
    result = extract(payload, "ingest/cms.json")
    assert result["title"] == "AI in Healthcare"
    assert result["author"] == "Jane"
    assert result["publish_date"] == "2024-04-15T10:30:00Z"
    assert "detect disease" in result["text"]


def test_unsupported_format_raises():
    with pytest.raises(ValueError, match="Unsupported format"):
        extract(b"%PDF-1.4", "ingest/doc.pdf")
