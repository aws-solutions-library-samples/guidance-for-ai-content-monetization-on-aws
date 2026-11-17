"""
Text extraction step — v1 formats: HTML, Markdown, JSON.

Each format adapter reads the source's metadata (title, author, publish date, canonical URL) and a
Markdown candidate, then calls the shared renderer, which writes the H1, the metadata block and
the body. The rules are normative in the markdown-document-structure spec (``schema-1.1.md``).

HTML is converted with markdownify after the article container is cleaned: page chrome, embedded
widgets, advertising, sharing and promotion blocks, and link-dense lists of other pages on the
same site are removed, and images are reduced to their useful alt text. PDF/DOCX are phase 2.
"""

import datetime as dt
import json
import logging
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import urljoin, urlsplit

import yaml
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import markdownify
from shared.utils.canonical_url import normalize_canonical_url

from content_pipeline.markdown_renderer import (
    MARKDOWN_SCHEMA_VERSION,
    DocumentMetadata,
    render_markdown_document,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Page chrome removed everywhere, as in schema 1.0.
_CHROME_TAGS = ("script", "style", "nav", "footer", "aside")
# Embedded widgets removed with their contents inside the article container.
_REMOVED_TAGS = (
    "form",
    "button",
    "dialog",
    "menu",
    "iframe",
    "object",
    "embed",
    "canvas",
    "video",
    "audio",
    "svg",
    "noscript",
    "template",
)
_REMOVED_ROLES = frozenset({"navigation", "complementary", "banner", "contentinfo", "search", "dialog", "menu"})
# A class or id token in this list marks advertising, sharing, promotion, recirculation, newsletter
# or comment widgets. Tokens are compared after splitting on whitespace, "-" and "_".
_BOILERPLATE_TOKENS = frozenset(
    {
        "ad",
        "ads",
        "adslot",
        "advert",
        "advertisement",
        "sponsor",
        "sponsored",
        "promo",
        "promotion",
        "share",
        "sharing",
        "social",
        "related",
        "recirc",
        "recirculation",
        "newsletter",
        "subscribe",
        "signup",
        "comment",
        "comments",
        "outbrain",
        "taboola",
    }
)
_TOKEN_RULE_TAGS = ("div", "section", "ul", "ol", "li", "p", "span", "a", "aside")
_BYLINE_TOKENS = frozenset({"byline", "author", "dateline"})
# A block made mostly of links to other pages on the same site (promo cards, "more stories").
_LINK_DENSE_TAGS = ("div", "section", "ul", "ol")
_LINK_DENSE_MIN_LINKS = 3
_LINK_DENSE_MIN_RATIO = 0.6

_TITLE_SEPARATORS = (" - ", " – ", " — ", " | ", " · ")
_JSONLD_MAX_BYTES = 256 * 1024
_FRONTMATTER = re.compile(r"\A(?:\ufeff)?---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)
_FRONTMATTER_MAX_BYTES = 16 * 1024
_MAX_AUTHORS = 20
_TOKEN_SPLIT = re.compile(r"[\s_-]+")
_WHITESPACE = re.compile(r"\s+")


def extract(raw: bytes, key: str) -> Dict[str, Any]:
    """
    Dispatch on file extension → {title, markdown, body_markdown, text, canonical_url,
    publish_date, modified_date, author, publisher, cms_tags, format}.
    """
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    body = raw.decode("utf-8", errors="replace")

    if ext in ("html", "htm"):
        return _extract_html(body)
    if ext in ("md", "markdown"):
        return _extract_markdown(body)
    if ext == "json":
        return _extract_json(body)
    raise ValueError(f"Unsupported format '{ext}' for key {key} (v1 supports html/md/json)")


# ── HTML ───────────────────────────────────────────────────────────────────


def _extract_html(body: str) -> Dict[str, Any]:
    meta = _scrape_document_metadata(BeautifulSoup(body, "html.parser"))
    canonical_url = normalize_canonical_url(meta["canonical_url"])

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(_CHROME_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    if soup.head:
        soup.head.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup
    in_article = container.name in ("article", "main")
    if not in_article:
        for header in container.find_all("header"):
            header.decompose()
    article_header = container.find("header") if in_article else None

    heading = container.find("h1") if in_article else None
    author = meta["author"] or _structured_author(container)
    publish_date = meta["publish_date"] or _header_date(article_header)
    modified_date = meta["modified_date"] or _header_modified_date(article_header)
    publisher = meta["site_name"]

    _remove_boilerplate(container)
    if article_header is not None and not article_header.decomposed:
        _remove_header_byline(article_header, has_author=bool(author), has_date=bool(publish_date))
    _remove_link_dense_blocks(container, canonical_url)
    _rewrite_images(soup, container)

    candidate = markdownify(str(container), heading_style="ATX")
    title = _first_text(
        _element_text(heading),
        meta["headline"],
        meta["og_title"],
        meta["title"],
        _first_heading(candidate),
    )
    rendered = render_markdown_document(
        candidate,
        title=title,
        canonical_url=canonical_url,
        metadata=DocumentMetadata(
            author=author,
            publisher=publisher,
            published=publish_date,
            updated=modified_date,
            source=canonical_url,
        ),
    )

    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "body_markdown": rendered.body_markdown,
        "text": rendered.body_text,
        "canonical_url": canonical_url,
        "publish_date": publish_date,
        "modified_date": modified_date,
        "author": author or "",
        "publisher": publisher or "",
        "cms_tags": [],
        "format": "html",
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


def _scrape_document_metadata(soup: BeautifulSoup) -> Dict[str, Optional[str]]:
    """Read title candidates, site name, author, dates and canonical URL from the original page."""

    def meta_content(*, property_name: Optional[str] = None, name: Optional[str] = None) -> Optional[str]:
        attrs = {"property": property_name} if property_name else {"name": name}
        tag = soup.find("meta", attrs=attrs)
        value = tag.get("content") if tag else None
        return _clean(value)

    article = _jsonld_article(soup)
    site_name = _first_text(
        meta_content(property_name="og:site_name"),
        _jsonld_name(article.get("publisher")),
        meta_content(name="application-name"),
    )
    title_tag = soup.find("title")
    page_title = _strip_site_suffix(_clean(title_tag.get_text(" ", strip=True)) if title_tag else None, site_name)

    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical = (
        _clean(canonical_tag.get("href"))
        if canonical_tag and canonical_tag.get("href")
        else meta_content(property_name="og:url")
    )

    article_author = meta_content(property_name="article:author")
    if article_author and urlsplit(article_author).scheme in ("http", "https"):
        article_author = None

    return {
        "title": page_title,
        "og_title": meta_content(property_name="og:title"),
        "headline": _clean(article.get("headline")),
        "site_name": site_name,
        "author": _first_text(_authors_text(article.get("author")), meta_content(name="author"), article_author),
        "publish_date": _first_text(
            _clean(article.get("datePublished")), meta_content(property_name="article:published_time")
        ),
        "modified_date": _first_text(
            _clean(article.get("dateModified")), meta_content(property_name="article:modified_time")
        ),
        "canonical_url": canonical,
    }


def _jsonld_article(soup: BeautifulSoup) -> Dict[str, Any]:
    """Return the first JSON-LD article node in document order, or {}. Never raises."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text or len(text.encode("utf-8")) > _JSONLD_MAX_BYTES:
            continue
        try:
            document = json.loads(text)
        except ValueError:
            continue
        for node in _jsonld_nodes(document):
            if _is_article_type(node.get("@type")):
                return node
    return {}


def _jsonld_nodes(value: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        yield from _jsonld_nodes(value.get("@graph"))
    elif isinstance(value, list):
        for item in value:
            yield from _jsonld_nodes(item)


def _is_article_type(value: Any) -> bool:
    types = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return any(
        isinstance(item, str) and (item.endswith("Article") or item in ("BlogPosting", "Report")) for item in types
    )


def _jsonld_name(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return _clean(value.get("name"))
    return _clean(value) if isinstance(value, str) else None


def _authors_text(value: Any) -> Optional[str]:
    """Join one or several authors (strings or objects with a name) with ", ", without duplicates."""
    items = value if isinstance(value, list) else [value]
    names: List[str] = []
    for item in items[:_MAX_AUTHORS]:
        name = _jsonld_name(item)
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def _strip_site_suffix(title: Optional[str], site_name: Optional[str]) -> Optional[str]:
    if not title or not site_name:
        return title
    for separator in _TITLE_SEPARATORS:
        head, found, tail = title.rpartition(separator)
        if found and head.strip() and tail.strip().casefold() == site_name.casefold():
            return head.strip()
    return title


def _structured_author(container: Tag) -> Optional[str]:
    element = container.find(attrs={"rel": "author"}) or container.find(attrs={"itemprop": "author"})
    return _element_text(element)


def _header_date(header: Optional[Tag]) -> Optional[str]:
    """The header's datePublished time, otherwise its first dated time that isn't dateModified."""
    if header is None:
        return None
    element = header.find("time", attrs={"itemprop": "datePublished"}) or next(
        (time for time in header.find_all("time", attrs={"datetime": True}) if time.get("itemprop") != "dateModified"),
        None,
    )
    return _clean(element.get("datetime")) if element else None


def _header_modified_date(header: Optional[Tag]) -> Optional[str]:
    element = header.find("time", attrs={"itemprop": "dateModified"}) if header is not None else None
    return _clean(element.get("datetime")) if element else None


def _remove_boilerplate(container: Tag) -> None:
    for tag in container.find_all(_REMOVED_TAGS):
        if not tag.decomposed:
            tag.decompose()
    for tag in container.find_all(True):
        if tag.decomposed or tag.name == "img":
            continue
        role = str(tag.get("role") or "").strip().lower()
        if role in _REMOVED_ROLES or str(tag.get("aria-hidden") or "").lower() == "true" or tag.has_attr("hidden"):
            tag.decompose()
    for tag in container.find_all(_TOKEN_RULE_TAGS):
        if tag.decomposed:
            continue
        if any(attribute.startswith("data-ad") for attribute in tag.attrs) or _tokens(tag) & _BOILERPLATE_TOKENS:
            tag.decompose()


def _remove_header_byline(header: Tag, *, has_author: bool, has_date: bool) -> None:
    if has_author:
        for tag in header.find_all(True):
            if tag.decomposed:
                continue
            if (
                tag.name == "address"
                or _tokens(tag) & _BYLINE_TOKENS
                or "author" in (tag.get("rel") or [])
                or tag.get("itemprop") == "author"
            ):
                tag.decompose()
    if has_date:
        for tag in header.find_all("time"):
            if not tag.decomposed:
                tag.decompose()


def _remove_link_dense_blocks(container: Tag, canonical_url: Optional[str]) -> None:
    """Judge every candidate on the same tree, then remove the link-dense ones."""
    canonical = urlsplit(canonical_url) if canonical_url else None
    dense = []
    for block in container.find_all(_LINK_DENSE_TAGS):
        site_links = [link for link in block.find_all("a", href=True) if _is_site_link(link["href"], canonical)]
        if len(site_links) < _LINK_DENSE_MIN_LINKS:
            continue
        visible = len(_WHITESPACE.sub(" ", block.get_text(" ")).strip())
        linked = sum(len(_WHITESPACE.sub(" ", link.get_text(" ")).strip()) for link in site_links)
        if visible and linked / visible >= _LINK_DENSE_MIN_RATIO:
            dense.append(block)
    for block in dense:
        if not block.decomposed:
            block.decompose()


def _is_site_link(href: str, canonical: Any) -> bool:
    href = href.strip()
    if not href or href.startswith("#"):
        return False
    parts = urlsplit(href)
    if canonical is None:
        return not parts.scheme and not parts.netloc
    resolved = urlsplit(urljoin(canonical.geturl(), href))
    return (
        resolved.scheme in ("http", "https") and resolved.netloc == canonical.netloc and resolved.path != canonical.path
    )


def _rewrite_images(soup: BeautifulSoup, container: Tag) -> None:
    for figure in container.find_all("figure"):
        caption = figure.find("figcaption")
        if caption is not None and _element_text(caption):
            for image in figure.find_all("img"):
                image.decompose()
            caption.name = "p"
        figure.name = "div"
    for image in container.find_all("img"):
        alt = _clean(image.get("alt"))
        role = str(image.get("role") or "").strip().lower()
        if not alt or role in ("presentation", "none") or str(image.get("aria-hidden") or "").lower() == "true":
            image.replace_with(NavigableString(" "))
        elif _is_block_image(image):
            paragraph = soup.new_tag("p")
            paragraph.string = alt
            (image.parent if image.parent.name == "a" else image).replace_with(paragraph)
        else:
            image.replace_with(NavigableString(f" {alt} "))


def _is_block_image(image: Tag) -> bool:
    """True when the image is the only content of its paragraph, figure, or a link filling one."""
    holder = image.parent
    if holder is not None and holder.name == "a" and holder.parent is not None and holder.parent.name == "p":
        if _element_text(holder):
            return False
        holder = holder.parent
    if holder is None or holder.name not in ("p", "div"):
        return False
    return not _element_text(holder) and len(holder.find_all("img")) == 1


# ── Markdown and JSON ──────────────────────────────────────────────────────


def _extract_markdown(body: str) -> Dict[str, Any]:
    front = _frontmatter(body)
    title = _clean(front.get("title"))
    author = _authors_text(front.get("author"))
    publisher = _jsonld_name(front.get("publisher"))
    publish_date = _date_text(_first_value(front, ("date", "published", "publish_date")))
    modified_date = _date_text(_first_value(front, ("updated", "modified", "modified_date")))
    rendered = render_markdown_document(
        body,
        title=title or "",
        metadata=DocumentMetadata(author=author, publisher=publisher, published=publish_date, updated=modified_date),
    )
    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "body_markdown": rendered.body_markdown,
        "text": rendered.body_text,
        "canonical_url": None,
        "publish_date": publish_date,
        "modified_date": modified_date,
        "author": author or "",
        "publisher": publisher or "",
        "cms_tags": [],
        "format": "md",
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


def _frontmatter(body: str) -> Dict[str, Any]:
    """Return the YAML frontmatter as a mapping, or {} when there is none or it doesn't parse."""
    match = _FRONTMATTER.match(body.replace("\r\n", "\n").replace("\r", "\n"))
    if not match or len(match.group(1).encode("utf-8")) > _FRONTMATTER_MAX_BYTES:
        return {}
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        logger.warning("Ignoring frontmatter that is not valid YAML")
        return {}
    return value if isinstance(value, dict) else {}


def _first_value(mapping: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _date_text(value: Any) -> Optional[str]:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return _clean(value) if isinstance(value, (str, int)) else None


def _extract_json(body: str) -> Dict[str, Any]:
    """
    Structured article JSON. Accepts either the POST /content payload shape
    ({title, content_text, ...}) or the CMS export shape
    ({article: {metadata, content: {headline, body|fullText}, ...}}).
    """
    doc = json.loads(body)

    if "article" in doc and isinstance(doc["article"], dict):
        art = doc["article"]
        content = art.get("content", {})
        meta = art.get("metadata", {})
        title = content.get("headline", "")
        text = content.get("fullText") or content.get("body") or ""
        if isinstance(text, list):
            text = "\n\n".join(str(p) for p in text)
        canonical_url = normalize_canonical_url(meta.get("canonicalUrl") or meta.get("url"))
        author = _author_name(meta.get("author"))
        publisher = _jsonld_name(meta.get("publisher")) or ""
        publish_date = meta.get("publishDate")
        modified_date = _date_text(meta.get("modifiedDate"))
        rendered = render_markdown_document(
            text,
            title=title,
            canonical_url=canonical_url,
            metadata=DocumentMetadata(
                author=author,
                publisher=publisher,
                published=publish_date,
                updated=modified_date,
                source=canonical_url,
            ),
        )
        return {
            "title": rendered.title,
            "markdown": rendered.markdown,
            "body_markdown": rendered.body_markdown,
            "text": rendered.body_text,
            "canonical_url": canonical_url,
            "publish_date": publish_date,
            "modified_date": modified_date,
            "author": author,
            "publisher": publisher,
            "cms_tags": meta.get("tags", []) or [],
            "format": "json",
            "content_type": _content_type(doc) or _content_type(art),
            "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
        }

    title = doc.get("title", "")
    text = doc.get("content_text") or doc.get("content") or doc.get("body") or ""
    candidate = doc.get("content_markdown") or text
    canonical_url = normalize_canonical_url(doc.get("canonical_url"))
    author = _author_name(doc.get("author"))
    publisher = _jsonld_name(doc.get("publisher")) or ""
    publish_date = doc.get("publication_date")
    modified_date = _date_text(doc.get("modified_date"))
    rendered = render_markdown_document(
        candidate,
        title=title,
        canonical_url=canonical_url,
        metadata=DocumentMetadata(
            author=author,
            publisher=publisher,
            published=publish_date,
            updated=modified_date,
            source=canonical_url,
        ),
    )
    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "body_markdown": rendered.body_markdown,
        "text": rendered.body_text,
        "canonical_url": canonical_url,
        "publish_date": publish_date,
        "modified_date": modified_date,
        "author": author,
        "publisher": publisher,
        "cms_tags": doc.get("tags", []) or [],
        "format": "json",
        "content_type": _content_type(doc),
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


# ── Small helpers ──────────────────────────────────────────────────────────


def _author_name(author: Any) -> str:
    if isinstance(author, dict):
        return author.get("name", "")
    return str(author) if author else ""


def _content_type(document: Dict[str, Any]) -> Optional[str]:
    value = document.get("content_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean(value: Any) -> Optional[str]:
    """Collapse whitespace in a scalar value; return None when nothing is left."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    return text or None


def _element_text(element: Optional[Tag]) -> Optional[str]:
    return _clean(element.get_text(" ", strip=True)) if element is not None else None


def _first_text(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def _tokens(tag: Tag) -> set:
    classes = tag.get("class") or []
    values = list(classes) if isinstance(classes, list) else [str(classes)]
    values.append(str(tag.get("id") or ""))
    return {token for value in values for token in _TOKEN_SPLIT.split(value.lower()) if token}


def _first_heading(md: str) -> Optional[str]:
    m = re.search(r"^#{1,2}\s+(.+)$", md, re.MULTILINE)
    return m.group(1).strip() if m else None
