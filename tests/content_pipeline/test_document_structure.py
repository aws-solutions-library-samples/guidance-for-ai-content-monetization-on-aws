"""Schema 1.1 document structure: the layout, the metadata block and the HTML extraction rules.

One case per rule in ``.kiro/specs/markdown-document-structure/schema-1.1.md``. The golden files
cover whole documents; these pin each rule on its own, so removing a rule fails a named test.
"""

import json

import pytest

from content_pipeline import extractor
from content_pipeline.extractor import extract
from content_pipeline.markdown_renderer import (
    DocumentMetadata,
    compose_document,
    render_markdown,
    render_markdown_document,
)

CANONICAL = "https://news.example.com/business/story"


def _html(article: str = "", head: str = "", *, canonical: bool = True, wrapper: str = "article") -> dict:
    link = f'<link rel="canonical" href="{CANONICAL}">' if canonical else ""
    body = f"<{wrapper}>{article}</{wrapper}>" if wrapper else article
    page = f"<html><head><title>Page</title>{link}{head}</head><body>{body}</body></html>"
    return extract(page.encode("utf-8"), "ingest/page.html")


def _jsonld(value) -> str:
    return f'<script type="application/ld+json">{json.dumps(value)}</script>'


def _block(markdown: str) -> list:
    """The metadata block's lines: the list directly under the H1."""
    lines = markdown.split("\n")
    assert lines[0].startswith("# ")
    block = []
    for line in lines[2:]:
        if not line.startswith("- "):
            break
        block.append(line)
    return block


# ── Metadata block (renderer) ──────────────────────────────────────────────


def test_block_fields_are_in_fixed_order_and_absent_fields_are_omitted():
    full = render_markdown_document(
        "Body",
        title="T",
        metadata=DocumentMetadata(
            author="Ana",
            publisher="Daily",
            published="2026-08-03",
            updated="2026-08-04T09:00:00Z",
            source="https://example.com/a",
        ),
    )
    assert _block(full.markdown) == [
        "- Author: Ana",
        "- Publisher: Daily",
        "- Published: 2026-08-03",
        "- Updated: 2026-08-04",
        "- Source: <https://example.com/a>",
    ]
    partial = render_markdown_document("Body", title="T", metadata=DocumentMetadata(source="https://example.com/a"))
    assert partial.markdown == "# T\n\n- Source: <https://example.com/a>\n\nBody\n\nSource: T. <https://example.com/a>"


@pytest.mark.parametrize("metadata", [None, DocumentMetadata(), DocumentMetadata(author="  ", source="not a url")])
def test_no_known_field_means_no_block(metadata):
    assert render_markdown_document("Body", title="T", metadata=metadata).markdown == "# T\n\nBody"


def test_block_values_are_plain_text_and_the_block_stays_out_of_body_text():
    rendered = render_markdown_document(
        "Body text",
        title="T",
        metadata=DocumentMetadata(author="Ana *Ruiz* [x](https://evil.example)", published="2026-08-03"),
    )
    author_line = _block(rendered.markdown)[0]
    assert author_line == "- Author: Ana Ruiz xhttps://evil.example"
    for _ in range(2):
        assert render_markdown(rendered.markdown) == rendered.markdown
        assert "](" not in render_markdown(render_markdown(rendered.markdown))
    assert rendered.body_text == "Body text"
    assert render_markdown(rendered.markdown) == rendered.markdown


@pytest.mark.parametrize(
    ("published", "shown"),
    [
        ("2026-11-02T08:30:00-05:00", "2026-11-02"),
        ("2026-11-02T13:30:00Z", "2026-11-02"),
        ("2026-11-02", "2026-11-02"),
        ("November 2, 2026", "November 2, 2026"),
    ],
)
def test_published_shows_the_calendar_date_as_written(published, shown):
    rendered = render_markdown_document("Body", title="T", metadata=DocumentMetadata(published=published))
    assert _block(rendered.markdown) == [f"- Published: {shown}"]


def test_source_needs_a_valid_canonical_url():
    rendered = render_markdown_document("Body", title="T", metadata=DocumentMetadata(source="javascript:alert(1)"))
    assert rendered.markdown == "# T\n\nBody"


def test_a_body_opening_with_a_dash_list_does_not_continue_the_block():
    with_block = render_markdown_document("- one\n- two", title="T", metadata=DocumentMetadata(author="Ana"))
    assert with_block.markdown == "# T\n\n- Author: Ana\n\n* one\n* two\n\nSource: T by Ana."
    assert render_markdown(with_block.markdown) == with_block.markdown
    followed = render_markdown_document("- one\n\n* two", title="T", metadata=DocumentMetadata(author="Ana"))
    assert "\n\n+ one\n\n* two\n\n" in followed.markdown
    assert render_markdown(followed.markdown) == followed.markdown
    assert render_markdown_document("- one\n- two", title="T").markdown == "# T\n\n- one\n- two"


def test_reference_definitions_are_not_written_because_links_are_inline():
    rendered = render_markdown("See [a][1].\n\n[1]: https://example.com/a\n", title="T")
    assert rendered == "# T\n\nSee [a](https://example.com/a)."


# ── Title ──────────────────────────────────────────────────────────────────


def test_the_article_h1_wins_over_og_title_and_title():
    head = '<meta property="og:title" content="SEO title">' + _jsonld({"@type": "NewsArticle", "headline": "LD"})
    assert _html("<h1>Visible headline</h1><p>Body</p>", head)["title"] == "Visible headline"


def test_jsonld_headline_is_used_when_the_article_has_no_h1():
    head = '<meta property="og:title" content="SEO title">' + _jsonld({"@type": "NewsArticle", "headline": "LD"})
    assert _html("<p>Body</p>", head)["title"] == "LD"


def test_an_h1_outside_article_or_main_is_not_the_title_source():
    head = '<meta property="og:title" content="Social title">'
    result = _html("<div><h1>Site logo</h1></div><p>Body</p>", head, wrapper="")
    assert result["title"] == "Social title"


@pytest.mark.parametrize(
    ("title", "head", "expected"),
    [
        ("Story | Example News", '<meta property="og:site_name" content="Example News">', "Story"),
        ("Story — Example News", _jsonld({"@type": "Article", "publisher": {"name": "Example News"}}), "Story"),
        ("Story - Example News", '<meta name="application-name" content="Example News">', "Story"),
        ("Story | Example News", "", "Story | Example News"),
        ("Story | Other Site", '<meta property="og:site_name" content="Example News">', "Story | Other Site"),
    ],
)
def test_title_site_suffix_is_removed_only_when_it_is_the_site_name(title, head, expected):
    page = f"<html><head><title>{title}</title>{head}</head><body><div><p>Body</p></div></body></html>"
    assert extract(page.encode(), "ingest/x.html")["title"] == expected


# ── Author and date sources ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("Ana Ruiz", "Ana Ruiz"),
        ({"@type": "Person", "name": "Ana Ruiz"}, "Ana Ruiz"),
        ([{"name": "Ana Ruiz"}, "Tom Lee", {"name": "Ana Ruiz"}], "Ana Ruiz, Tom Lee"),
    ],
)
def test_jsonld_author_shapes(author, expected):
    result = _html("<p>Body</p>", _jsonld({"@type": "NewsArticle", "author": author}))
    assert result["author"] == expected
    assert _block(result["markdown"])[0] == f"- Author: {expected}"


def test_jsonld_is_read_from_a_graph_a_list_and_a_type_list_and_bad_scripts_are_skipped():
    head = '<script type="application/ld+json">{ broken</script>' + _jsonld(
        [{"@type": "WebSite"}, {"@graph": [{"@type": ["Thing", "BlogPosting"], "author": "From graph"}]}]
    )
    assert _html("<p>Body</p>", head)["author"] == "From graph"


def test_oversized_jsonld_is_ignored(monkeypatch):
    monkeypatch.setattr(extractor, "_JSONLD_MAX_BYTES", 64)
    head = _jsonld({"@type": "NewsArticle", "author": "X" * 100})
    assert _html("<p>Body</p>", head)["author"] == ""


@pytest.mark.parametrize(
    ("head", "article", "expected"),
    [
        ('<meta name="author" content="Meta Author">', "", "Meta Author"),
        ('<meta property="article:author" content="Article Author">', "", "Article Author"),
        ('<meta property="article:author" content="https://example.com/staff/a">', "", ""),
        ("", '<p>By <a rel="author" href="/staff/r">Rel Author</a></p>', "Rel Author"),
        ("", '<p>By <span itemprop="author">Prop Author</span></p>', "Prop Author"),
    ],
)
def test_author_fallbacks(head, article, expected):
    assert _html(f"{article}<p>Body</p>", head)["author"] == expected


@pytest.mark.parametrize(
    ("head", "header", "expected"),
    [
        (_jsonld({"@type": "NewsArticle", "datePublished": "2026-01-02"}), "", "2026-01-02"),
        ('<meta property="article:published_time" content="2026-01-03T10:00:00Z">', "", "2026-01-03T10:00:00Z"),
        (
            "",
            '<header><time datetime="2026-01-04">x</time><time itemprop="datePublished" datetime="2026-01-05">y</time></header>',
            "2026-01-05",
        ),
        ("", '<header><time datetime="2026-01-06">Jan 6</time></header>', "2026-01-06"),
    ],
)
def test_publish_date_sources(head, header, expected):
    assert _html(f"{header}<p>Body</p>", head)["publish_date"] == expected


def test_the_publish_date_fallback_skips_the_header_modified_time():
    header = '<header><time itemprop="dateModified" datetime="2026-01-09">x</time><time datetime="2026-01-08">y</time></header>'
    result = _html(f"{header}<p>Body</p>")
    assert result["publish_date"] == "2026-01-08"
    assert result["modified_date"] == "2026-01-09"


@pytest.mark.parametrize(
    ("head", "header", "expected"),
    [
        (
            _jsonld({"@type": "NewsArticle", "dateModified": "2026-02-02"})
            + '<meta property="article:modified_time" content="2026-02-03">',
            '<header><time itemprop="dateModified" datetime="2026-02-04">x</time></header>',
            "2026-02-02",
        ),
        (
            '<meta property="article:modified_time" content="2026-02-03T10:00:00Z">',
            '<header><time itemprop="dateModified" datetime="2026-02-04">x</time></header>',
            "2026-02-03T10:00:00Z",
        ),
        ("", '<header><time itemprop="dateModified" datetime="2026-02-04">x</time></header>', "2026-02-04"),
        ("", '<header><time datetime="2026-02-05">x</time></header>', None),
    ],
)
def test_updated_date_sources(head, header, expected):
    result = _html(f"{header}<p>Body</p>", head)
    assert result["modified_date"] == expected
    updated = [line for line in _block(result["markdown"]) if line.startswith("- Updated:")]
    assert updated == ([f"- Updated: {expected[:10]}"] if expected else [])


@pytest.mark.parametrize(
    "head",
    [
        '<meta property="og:site_name" content="Example News">'
        + _jsonld({"@type": "Article", "publisher": {"name": "Other"}}),
        _jsonld({"@type": "Article", "publisher": {"@type": "Organization", "name": "Example News"}})
        + '<meta name="application-name" content="Other">',
        '<meta name="application-name" content="Example News">',
    ],
    ids=["og-site-name", "jsonld-publisher", "application-name"],
)
def test_the_publisher_is_the_site_name(head):
    result = _html("<p>Body</p>", head)
    assert result["publisher"] == "Example News"
    assert "- Publisher: Example News" in _block(result["markdown"])


def test_without_a_site_name_there_is_no_publisher():
    result = _html("<p>Body</p>")
    assert result["publisher"] == ""
    assert not [line for line in _block(result["markdown"]) if line.startswith("- Publisher:")]


def test_the_extracted_document_is_the_composition_of_its_parts():
    head = _jsonld(
        {"@type": "NewsArticle", "author": "Ana", "datePublished": "2026-01-02", "dateModified": "2026-01-03"}
    )
    head += '<meta property="og:site_name" content="Example News">'
    result = _html("<h1>Headline</h1><p>Body</p>", head)
    metadata = DocumentMetadata(
        author=result["author"],
        publisher=result["publisher"],
        published=result["publish_date"],
        updated=result["modified_date"],
        source=result["canonical_url"],
    )
    composed = compose_document(title=result["title"], body_markdown=result["body_markdown"], metadata=metadata)
    assert composed == result["markdown"]
    assert result["body_markdown"] == "Body"


def test_a_time_outside_the_article_header_is_not_a_date_source():
    result = _html('<p>Updated <time datetime="2026-01-07">Tuesday</time>.</p>')
    assert result["publish_date"] is None
    assert "Updated Tuesday." in result["markdown"]


# ── Article header and byline ──────────────────────────────────────────────


def test_the_article_header_is_kept_and_its_byline_and_time_go_once_the_block_has_them():
    header = (
        "<header><h1>Headline</h1><p class='dek'>The dek.</p>"
        "<p class='byline'>By Ana Ruiz</p><time datetime='2026-01-02'>Jan 2</time></header>"
    )
    head = _jsonld({"@type": "NewsArticle", "author": "Ana Ruiz", "datePublished": "2026-01-02"})
    result = _html(f"{header}<p>Body</p>", head)
    markdown = result["markdown"]
    assert "The dek." in markdown
    assert "By Ana Ruiz" not in markdown
    assert "Jan 2" not in markdown
    assert markdown.startswith("# Headline\n")
    assert "Headline" not in result["body_markdown"]


@pytest.mark.parametrize(
    "byline",
    [
        "<p class='byline'>By Someone</p>",
        "<p class='story-dateline'>By Someone</p>",
        "<address>By Someone</address>",
    ],
)
def test_byline_elements_go_only_when_the_block_has_an_author(byline):
    header = f"<header><h1>Headline</h1>{byline}</header><p>Body</p>"
    assert "By Someone" in _html(header)["markdown"]
    with_author = _html(header, '<meta name="author" content="Ana Ruiz">')["markdown"]
    assert "By Someone" not in with_author


def test_headers_are_all_removed_when_the_container_is_the_body():
    result = _html("<header><p>Site header</p></header><div><p>Body</p></div>", wrapper="")
    assert "Site header" not in result["markdown"]


# ── In-article boilerplate ─────────────────────────────────────────────────


@pytest.mark.parametrize("tag", [tag for tag in extractor._REMOVED_TAGS if tag != "embed"])  # embed is void
def test_widget_tags_are_removed_with_their_contents(tag):
    markdown = _html(f"<p>Keep this.</p><{tag}>Widget text</{tag}>")["markdown"]
    assert "Keep this." in markdown
    assert "Widget text" not in markdown


@pytest.mark.parametrize("role", sorted(extractor._REMOVED_ROLES))
def test_page_roles_are_removed(role):
    assert "Role text" not in _html(f"<p>Keep.</p><div role='{role}'>Role text</div>")["markdown"]


@pytest.mark.parametrize("attribute", ["aria-hidden='true'", "hidden"])
def test_hidden_elements_are_removed(attribute):
    assert "Hidden text" not in _html(f"<p>Keep.</p><div {attribute}>Hidden text</div>")["markdown"]


@pytest.mark.parametrize("token", sorted(extractor._BOILERPLATE_TOKENS))
def test_boilerplate_tokens_remove_their_element(token):
    markdown = _html(f"<p>Keep.</p><div class='box-{token}_x'>Token text</div>")["markdown"]
    assert "Token text" not in markdown


@pytest.mark.parametrize(
    "element",
    ["<div id='top-ad'>Token text</div>", "<div data-ad-slot='1'>Token text</div>", "<span data-ad>Token text</span>"],
)
def test_ids_and_data_ad_attributes_remove_their_element(element):
    assert "Token text" not in _html(f"<p>Keep.</p>{element}")["markdown"]


@pytest.mark.parametrize(
    "element",
    [
        "<table class='share-prices'><tr><td>Token text</td></tr></table>",
        "<h2 class='related'>Token text</h2>",
        "<p class='header shadow'>Token text</p>",
    ],
)
def test_the_token_rule_spares_tables_headings_and_partial_words(element):
    assert "Token text" in _html(f"<p>Keep.</p>{element}")["markdown"]


def test_a_link_dense_block_of_same_site_links_is_removed():
    cards = "<div><a href='/a'>Story A</a> <a href='/b'>Story B</a> <a href='/c'>Story C</a></div>"
    assert "Story A" not in _html(f"<p>Keep this paragraph.</p>{cards}")["markdown"]


@pytest.mark.parametrize(
    ("block", "kept"),
    [
        ("<div><a href='/a'>Story A</a> <a href='/b'>Story B</a></div>", "Story A"),
        (
            "<div><a href='/a'>A</a> <a href='/b'>B</a> <a href='/c'>C</a> and a long run of ordinary sentence text.</div>",
            "ordinary sentence text",
        ),
        (
            "<ul><li><a href='https://other.example.org/1'>Story A</a></li>"
            "<li><a href='https://other.example.org/2'>Story B</a></li>"
            "<li><a href='https://other.example.org/3'>Story C</a></li></ul>",
            "Story A",
        ),
        (
            f"<div><a href='{CANONICAL}#a'>Story A</a> <a href='#b'>Story B</a> <a href='{CANONICAL}'>Story C</a>"
            f" <a href='{CANONICAL}?page=2'>Story D</a></div>",
            "Story A",
        ),
    ],
)
def test_blocks_that_are_not_link_dense_same_site_lists_are_kept(block, kept):
    assert kept in _html(f"<p>Keep this paragraph.</p>{block}")["markdown"]


def test_without_a_canonical_url_relative_links_count_as_site_links():
    cards = "<div><a href='/a'>Story A</a> <a href='/b'>Story B</a> <a href='/c'>Story C</a></div>"
    assert "Story A" not in _html(f"<p>Keep this paragraph.</p>{cards}", canonical=False)["markdown"]


# ── Images ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "image",
    [
        "<img src='/i.png' alt=''>",
        "<img src='/i.png'>",
        "<img src='/i.png' alt='Icon' role='presentation'>",
        "<img src='/i.png' alt='Icon' role='none'>",
        "<img src='/i.png' alt='Icon' aria-hidden='true'>",
    ],
)
def test_decorative_images_add_no_text_but_keep_words_apart(image):
    markdown = _html(f"<p>Before.{image}After.</p>")["markdown"]
    assert "Before. After." in markdown
    assert "Icon" not in markdown


def test_a_figure_with_a_caption_keeps_the_caption_and_drops_the_alt_text():
    markdown = _html("<figure><img src='/i.png' alt='Alt text'><figcaption>The caption.</figcaption></figure>")[
        "markdown"
    ]
    assert "The caption." in markdown
    assert "Alt text" not in markdown


@pytest.mark.parametrize(
    "block",
    [
        "<figure><img src='/i.png' alt='Alt text'></figure>",
        "<p><img src='/i.png' alt='Alt text'></p>",
        "<p><a href='/big.png'><img src='/i.png' alt='Alt text'></a></p>",
    ],
)
def test_a_block_image_becomes_its_own_paragraph(block):
    markdown = _html(f"<p>Before.</p>{block}<p>After.</p>")["markdown"]
    assert "\n\nAlt text\n\n" in markdown


def test_inline_alt_text_is_kept_apart_from_its_neighbours():
    markdown = _html("<p>Volumes fell<img src='/c.png' alt='Chart'>during the strike.</p>")["markdown"]
    assert "Volumes fell Chart during the strike." in markdown


# ── Markdown frontmatter and JSON ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("front", "block"),
    [
        ("author: Ana Ruiz\ndate: 2026-03-04", ["- Author: Ana Ruiz", "- Published: 2026-03-04"]),
        (
            "author:\n  - name: Ana Ruiz\n  - Tom Lee\npublished: 2026-03-05",
            ["- Author: Ana Ruiz, Tom Lee", "- Published: 2026-03-05"],
        ),
        ("publish_date: '2026-03-06'", ["- Published: 2026-03-06"]),
        ("publisher: Example Press\nupdated: 2026-03-07", ["- Publisher: Example Press", "- Updated: 2026-03-07"]),
        (
            "publisher:\n  name: Example Press\nmodified: 2026-03-08",
            ["- Publisher: Example Press", "- Updated: 2026-03-08"],
        ),
        ("modified_date: '2026-03-09T10:00:00Z'", ["- Updated: 2026-03-09"]),
    ],
)
def test_markdown_frontmatter_fills_the_block(front, block):
    result = extract(f"---\n{front}\n---\n# Doc\n\nBody".encode(), "ingest/doc.md")
    assert _block(result["markdown"]) == block
    assert "Body" in result["markdown"]


def test_frontmatter_title_comes_before_the_first_heading():
    result = extract(b"---\ntitle: From Frontmatter\n---\n# Heading\n\nBody", "ingest/doc.md")
    assert result["title"] == "From Frontmatter"
    assert result["markdown"].startswith("# From Frontmatter\n\n## Heading")


@pytest.mark.parametrize(
    "front",
    ["author: [unclosed", "- just\n- a list", "author: x\n" + "y: z\n" * 4000],
    ids=["invalid-yaml", "not-a-mapping", "oversized"],
)
def test_unusable_frontmatter_is_ignored(front):
    result = extract(f"---\n{front}\n---\n# Doc\n\nBody".encode(), "ingest/doc.md")
    assert result["markdown"] == "# Doc\n\nBody"
    assert result["author"] == ""


@pytest.mark.parametrize(
    "document",
    [
        {
            "title": "T",
            "content_text": "Body",
            "author": {"name": "Ana Ruiz"},
            "publisher": "Example Press",
            "publication_date": "2026-05-06",
            "modified_date": "2026-05-07",
            "canonical_url": "https://example.com/a",
        },
        {
            "article": {
                "content": {"headline": "T", "body": "Body"},
                "metadata": {
                    "author": "Ana Ruiz",
                    "publisher": {"name": "Example Press"},
                    "publishDate": "2026-05-06",
                    "modifiedDate": "2026-05-07",
                    "canonicalUrl": "https://example.com/a",
                },
            }
        },
    ],
    ids=["post-content-shape", "cms-export-shape"],
)
def test_json_inputs_fill_the_block_from_their_fields(document):
    result = extract(json.dumps(document).encode(), "ingest/doc.json")
    assert _block(result["markdown"]) == [
        "- Author: Ana Ruiz",
        "- Publisher: Example Press",
        "- Published: 2026-05-06",
        "- Updated: 2026-05-07",
        "- Source: <https://example.com/a>",
    ]
    assert (result["publisher"], result["modified_date"]) == ("Example Press", "2026-05-07")


# ── Generated parts and the footer (compose_document) ──────────────────────

META = DocumentMetadata(author="Ana Ruiz", published="2026-11-02", source="https://example.com/a")
FAQ = [{"question": "How long?", "answer": "Twelve days."}, {"question": "Who?", "answer": "Dockworkers."}]


def _compose(**overrides) -> str:
    arguments = {"title": "T", "body_markdown": "Body", "metadata": META, "summary": "", "faq": ()}
    arguments.update(overrides)
    return compose_document(**arguments)


def test_the_six_parts_are_in_schema_order_and_the_document_re_renders_identically():
    document = _compose(summary="A short summary.", faq=FAQ)
    assert document == (
        "# T\n\n"
        "- Author: Ana Ruiz\n- Published: 2026-11-02\n- Source: <https://example.com/a>\n\n"
        "> Summary (generated): A short summary.\n\n"
        "Body\n\n"
        "## Frequently asked questions (generated)\n\n"
        "Generated automatically from the article above. Not part of the original article.\n\n"
        "### How long?\n\nTwelve days.\n\n"
        "### Who?\n\nDockworkers.\n\n"
        "Source: T by Ana Ruiz, 2026-11-02. <https://example.com/a>"
    )
    assert render_markdown(document) == document
    assert render_markdown(render_markdown(document)) == document


@pytest.mark.parametrize("summary", ["", "   ", None, 42, ["a list"]])
def test_no_usable_capsule_means_no_summary(summary):
    assert "Summary (generated)" not in _compose(summary=summary)


@pytest.mark.parametrize(
    ("capsule", "shown"),
    [
        ("See [x](https://evil.example) now", "See x(https://evil.example) now"),
        ("<img src=x onerror=alert(1)> *bold* `code` a|b ~~s~~ \\x", "img src=x onerror=alert(1) bold code ab s x"),
        ("C# (and F#) rose 3%! Done.", "C# (and F#) rose 3%\\! Done."),  # the serializer's literal escape
        ("Line one\n\n- not a list", "Line one - not a list"),
    ],
)
def test_the_capsule_is_prose_that_cannot_form_markup(capsule, shown):
    document = _compose(summary=capsule)
    assert f"> Summary (generated): {shown}\n\n" in document
    assert render_markdown(document) == document


@pytest.mark.parametrize(
    "faq",
    [
        [],
        None,
        "not a list",
        {"question": "Q?", "answer": "A."},
        [{"question": "", "answer": "A."}, {"question": "Q?", "answer": "  "}],
        [{"question": "Q?"}, {"answer": "A."}, {"question": 1, "answer": "A."}, "pair", None],
        [
            {"question": "[`<>`]", "answer": "A."},
            {"question": "## #", "answer": "A."},
            {"question": "Q?", "answer": "- - -"},
        ],
        5,
    ],
    ids=["empty", "none", "string", "mapping", "empty-text", "invalid-pairs", "empty-after-reduction", "number"],
)
def test_no_valid_pair_means_no_faq_section(faq):
    assert "Frequently asked questions" not in _compose(faq=faq)


def test_invalid_pairs_are_skipped_and_the_rest_keep_enrichment_order():
    faq = [
        {"question": "First?", "answer": "One."},
        {"question": "", "answer": "x"},
        {"question": "Second?", "answer": "Two."},
    ]
    document = _compose(faq=faq)
    assert "### First?\n\nOne.\n\n### Second?\n\nTwo." in document


def test_the_faq_holds_at_most_ten_pairs():
    faq = [{"question": f"Q{n}?", "answer": f"A{n}."} for n in range(1, 13)]
    document = _compose(faq=faq)
    assert document.count("\n### ") == 10
    assert "### Q10?" in document and "Q11" not in document


@pytest.mark.parametrize(
    ("question", "shown"),
    [
        ("How long? ##", "How long?"),
        ("What is it #", "What is it"),
        ("Is it over # #", "Is it over"),
        ("What is C#", "What is C#"),
        ("Why *now* [x](y)?", "Why now x(y)?"),
    ],
)
def test_faq_questions_lose_trailing_hashes_and_markup(question, shown):
    document = _compose(faq=[{"question": question, "answer": "A."}])
    assert f"\n### {shown}\n\nA.\n\n" in document
    assert render_markdown(document) == document


@pytest.mark.parametrize(
    ("answer", "shown"),
    [
        ("> quoted", "quoted"),
        ("# Heading", "Heading"),
        ("- item", "item"),
        ("+ item", "item"),
        ("1. first", "first"),
        ("2) second", "second"),
        ("- > # nested", "nested"),
        ("-5 degrees", "-5 degrees"),
        ("1.5 million", "1.5 million"),
        ("#hashtag", "#hashtag"),
    ],
)
def test_faq_answers_lose_a_leading_block_marker(answer, shown):
    document = _compose(faq=[{"question": "Q?", "answer": answer}])
    assert f"### Q?\n\n{shown}\n\n" in document
    assert render_markdown(document) == document


def test_a_publisher_faq_in_the_body_stays_unchanged():
    body = "## FAQ\n\n### Is it free?\n\nYes."
    document = _compose(body_markdown=body, faq=FAQ)
    assert f"\n\n{body}\n\n## Frequently asked questions (generated)\n\n" in document


@pytest.mark.parametrize(
    ("metadata", "footer"),
    [
        (META, "Source: T by Ana Ruiz, 2026-11-02. <https://example.com/a>"),
        (DocumentMetadata(published="2026-11-02T08:30:00-05:00"), "Source: T, 2026-11-02."),
        (DocumentMetadata(author="Ana Ruiz"), "Source: T by Ana Ruiz."),
        (DocumentMetadata(publisher="Daily", source="https://example.com/a"), "Source: T. <https://example.com/a>"),
        (DocumentMetadata(updated="2026-11-03"), "Source: T."),
    ],
)
def test_the_footer_uses_the_rsl_format_and_leaves_out_unknown_parts(metadata, footer):
    document = _compose(metadata=metadata)
    assert document.endswith(f"\n\n{footer}")
    assert render_markdown(document) == document


@pytest.mark.parametrize("metadata", [None, DocumentMetadata(), DocumentMetadata(source="not a url")])
def test_without_a_block_there_is_no_footer(metadata):
    assert _compose(metadata=metadata, faq=FAQ).endswith("### Who?\n\nDockworkers.")


def test_footer_values_are_reduced_like_the_block():
    document = _compose(title="A [b](c) *title*", metadata=DocumentMetadata(author="<Ana>"))
    assert document.endswith("\n\nSource: A bc title by Ana.")
    assert render_markdown(document) == document


def test_generated_parts_stay_out_of_the_extracted_body_text():
    result = _html("<h1>Headline</h1><p>Body</p>", _jsonld({"@type": "NewsArticle", "author": "Ana"}))
    assert result["text"] == "Body"
