"""Deterministic Markdown schema 1.0 behavior tests."""

import re

import pytest

from content_pipeline.markdown_renderer import (
    MARKDOWN_SCHEMA_VERSION,
    markdown_to_plain_text,
    render_markdown,
    render_markdown_document,
)


def test_schema_version_constant():
    assert MARKDOWN_SCHEMA_VERSION == "1.0"


@pytest.mark.parametrize(
    ("source", "title", "expected_first_line"),
    [
        ("", "", "# Untitled"),
        ("Body only", "", "# Untitled"),
        ("## Source Title\n\nBody", "", "# Source Title"),
        ("# Source Title\n\nBody", "Explicit Title", "# Explicit Title"),
        ("# Same Title\n\nBody", "same title", "# same title"),
    ],
)
def test_title_selection_and_first_line(source, title, expected_first_line):
    rendered = render_markdown(source, title=title)
    assert rendered.splitlines()[0] == expected_first_line
    assert len(re.findall(r"^# ", rendered, re.MULTILINE)) == 1


@pytest.mark.parametrize(
    "title",
    [
        "![Injected](https://evil.example/title.png)",
        "[Injected](javascript:alert(1))",
        "<https://evil.example/autolink>",
        "`code` *strong* _emphasis_",
        "# Nested heading | table",
    ],
)
def test_title_metadata_is_plain_text_safe_and_idempotent(title):
    rendered = render_markdown("Body", title=title, canonical_url="https://example.com/article")

    assert rendered.startswith("# ")
    assert "https://evil.example" not in rendered or "\\<https://evil.example" in rendered
    assert "![" not in rendered
    assert "](" not in rendered
    assert "javascript:" not in rendered
    assert render_markdown(rendered, canonical_url="https://example.com/article") == rendered


def test_source_title_with_markdown_is_reemitted_as_plain_title_text():
    rendered = render_markdown("# ![Diagram](https://evil.example/image.png)\n\nBody")

    assert rendered.splitlines()[0] == r"# Diagram"
    assert "evil.example" not in rendered
    assert render_markdown(rendered) == rendered


@pytest.mark.parametrize(
    ("title", "first_line"),
    [
        ("C# Guide", r"# C\# Guide"),
        ("C++ / .NET", r"# C\+\+ / \.NET"),
        ("A [title] | details", r"# A \[title\] \| details"),
        ("AT&T", "# AT&T"),
        ("List<T> in C#", r"# List\<T\> in C\#"),
        ("<https://example.com/path>", r"# \<https://example\.com/path\>"),
    ],
)
def test_semantic_title_and_body_are_distinct_from_serialized_h1(title, first_line):
    document = render_markdown_document("Body only", title=title)

    assert document.title == title
    assert document.body_text == "Body only"
    assert document.markdown.splitlines()[0] == first_line
    assert render_markdown(document.markdown) == document.markdown


def test_additional_h1s_are_demoted_including_blockquotes():
    rendered = render_markdown("# Title\n\n# Section\n\n> # Quoted heading")
    assert rendered == "# Title\n\n## Section\n\n> ## Quoted heading"


def test_preserves_lists_blockquotes_callouts_code_and_tables():
    source = """# Title

- one
  - nested

3. third
4. fourth

> ### Tip
>
> Keep this.

```python
print("ok")
```

| Left | Right |
| :--- | ---: |
| A | B |
"""
    rendered = render_markdown(source)
    assert "- one\n  - nested" in rendered
    assert "3. third\n4. fourth" in rendered
    assert "> ### Tip\n>\n> Keep this." in rendered
    assert '```python\nprint("ok")\n```' in rendered
    assert "| :--- | ---: |" in rendered


@pytest.mark.parametrize("scheme", ["javascript", "data", "file", "vbscript"])
def test_unsafe_links_degrade_to_visible_label(scheme):
    rendered = render_markdown(f"# Title\n\n[Visible]({scheme}:payload)")
    assert rendered == "# Title\n\nVisible"
    assert scheme not in rendered


def test_relative_and_absolute_safe_links_are_normalized():
    rendered = render_markdown(
        "# Title\n\n[Relative](../guide?q=1#part) [Web](HTTPS://EXAMPLE.COM:443/x) "
        "[Email](mailto:editor@example.com)",
        canonical_url="https://example.com/articles/item",
    )
    assert "[Relative](https://example.com/guide?q=1#part)" in rendered
    assert "[Web](https://example.com/x)" in rendered
    assert "[Email](mailto:editor@example.com)" in rendered


def test_relative_link_without_canonical_url_becomes_text():
    assert render_markdown("# Title\n\n[Relative](../guide)") == "# Title\n\nRelative"


def test_images_are_removed_but_alt_text_is_preserved():
    rendered = render_markdown("# Title\n\nBefore ![Useful diagram](image.png) ![](empty.png) after.")
    assert rendered == "# Title\n\nBefore Useful diagram  after."
    assert "![" not in rendered
    assert "image.png" not in rendered
    assert "empty.png" not in rendered


def test_frontmatter_page_chrome_and_raw_html_are_removed():
    source = """---
title: hidden
---
# Title

<script>bad()</script>
<style>.bad { color: red }</style>
<nav>menu</nav>
<div><b>Visible raw HTML</b></div>
<!-- comment -->
"""
    rendered = render_markdown(source)
    assert rendered == "# Title\n\nVisible raw HTML"


def test_line_endings_blank_lines_and_trailing_whitespace_are_normalized():
    rendered = render_markdown(b"# Title\r\n\r\n\r\nBody   \r\n".decode())
    assert rendered == "# Title\n\nBody"
    assert not rendered.endswith("\n")
    assert "\r" not in rendered
    assert "\n\n\n" not in rendered


@pytest.mark.parametrize(
    "source",
    [
        "",
        "Body",
        "# Title\n\nBody",
        "# Title\n\n# Other\n\n![Alt](x.png)",
        "# Title\n\n- one\n  - nested\n\n> quote",
        "# Title\n\n| A | B |\n| --- | --- |\n| x | y |",
        "# Title\n\n````\ncode with ``` inside\n````",
        "# Title\n\n[Link](https://example.com/a?q=1#f)",
        r"# C\# Guide" + "\n\nBody",
        r"# A \[title\] \| details" + "\n\nBody",
    ],
)
def test_rendering_is_idempotent(source):
    once = render_markdown(source)
    assert render_markdown(once) == once


def test_plain_text_contains_content_without_markdown_destinations():
    text = markdown_to_plain_text("# Title\n\n[Label](https://example.com) ![Alt](image.png) `code`")
    assert text == "Title Label Alt code"
