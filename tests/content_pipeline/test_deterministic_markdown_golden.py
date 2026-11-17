"""Golden extraction tests for all issue #4 ingestion formats."""

import re
from pathlib import Path

import pytest

from content_pipeline.extractor import extract
from content_pipeline.markdown_renderer import render_markdown
from shared.utils.models import MARKDOWN_SCHEMA_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "deterministic_markdown"


@pytest.mark.parametrize(
    ("input_name", "expected_name"),
    [
        ("article.html", "article.html.expected.md"),
        ("article.md", "article.md.expected.md"),
        ("article.json", "article.json.expected.md"),
        ("article-demo.html", "article-demo.html.expected.md"),
        ("article-news.html", "article-news.html.expected.md"),
    ],
)
def test_golden_markdown_output(input_name, expected_name):
    result = extract((FIXTURES / input_name).read_bytes(), f"ingest/{input_name}")
    expected = (FIXTURES / expected_name).read_text(encoding="utf-8").rstrip("\n")

    assert result["markdown"] == expected
    assert result["markdown_schema_version"] == MARKDOWN_SCHEMA_VERSION
    assert render_markdown(result["markdown"], canonical_url=result["canonical_url"]) == expected
    assert not result["markdown"].endswith("\n")
    assert len(re.findall(r"^# ", result["markdown"], re.MULTILINE)) == 1


def test_html_metadata_and_canonical_url_are_normalized():
    result = extract((FIXTURES / "article.html").read_bytes(), "ingest/article.html")
    assert result["title"] == "Deterministic Publishing"
    assert result["canonical_url"] == "https://example.com/guides/markdown/"
    assert result["author"] == "Ankit Patel"
    assert "Navigation must disappear" not in result["text"]
    assert "Footer must disappear" not in result["text"]


def test_json_metadata_and_relative_link_resolution():
    result = extract((FIXTURES / "article.json").read_bytes(), "ingest/article.json")
    assert result["canonical_url"] == "https://example.com/articles/2026/item"
    assert result["cms_tags"] == ["markdown", "publishing"]
    assert "[relative guide](https://example.com/articles/guide)" in result["markdown"]


def test_invalid_utf8_is_replaced_and_still_renders():
    result = extract(b"# Byte Input\n\nvalue: \xff", "ingest/invalid.md")
    assert result["markdown"].startswith("# Byte Input")
    assert "\ufffd" in result["markdown"]
    assert result["markdown_schema_version"] == MARKDOWN_SCHEMA_VERSION
