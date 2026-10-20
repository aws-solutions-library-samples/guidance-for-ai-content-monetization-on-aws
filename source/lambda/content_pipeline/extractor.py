"""
Text extraction step — v1 formats: HTML, Markdown, JSON.

HTML→markdown conversion ported to Python from the markdown demo's Generator
Lambda (turndown equivalent) using markdownify. Head metadata (title,
description, canonical URL, publish date) is scraped like the demo does.
PDF/DOCX are phase 2 (task 7.3).
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify
from shared.utils.canonical_url import normalize_canonical_url

from content_pipeline.markdown_renderer import (
    MARKDOWN_SCHEMA_VERSION,
    render_markdown_document,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def extract(raw: bytes, key: str) -> Dict[str, Any]:
    """
    Dispatch on file extension → {title, markdown, text, canonical_url,
    publish_date, author, cms_tags, format}.
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


def _extract_html(body: str) -> Dict[str, Any]:
    meta = _scrape_head(body)
    canonical_url = normalize_canonical_url(meta.get("canonical_url"))
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    if soup.head:
        soup.head.decompose()
    for image in soup.find_all("img"):
        image.replace_with(NavigableString(image.get("alt", "")))

    container = soup.find("article") or soup.find("main") or soup.body or soup
    candidate = markdownify(str(container), heading_style="ATX")
    title = meta.get("title") or _first_heading(candidate) or ""
    rendered = render_markdown_document(candidate, title=title, canonical_url=canonical_url)

    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "text": rendered.body_text,
        "canonical_url": canonical_url,
        "publish_date": meta.get("publish_date"),
        "author": _author_name(meta.get("author")),
        "cms_tags": [],
        "format": "html",
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


def _extract_markdown(body: str) -> Dict[str, Any]:
    rendered = render_markdown_document(body)
    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "text": rendered.body_text,
        "canonical_url": None,
        "publish_date": None,
        "author": "",
        "cms_tags": [],
        "format": "md",
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


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
        rendered = render_markdown_document(text, title=title, canonical_url=canonical_url)
        return {
            "title": rendered.title,
            "markdown": rendered.markdown,
            "text": rendered.body_text,
            "canonical_url": canonical_url,
            "publish_date": meta.get("publishDate"),
            "author": _author_name(meta.get("author")),
            "cms_tags": meta.get("tags", []) or [],
            "format": "json",
            "content_type": _content_type(doc) or _content_type(art),
            "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
        }

    title = doc.get("title", "")
    text = doc.get("content_text") or doc.get("content") or doc.get("body") or ""
    candidate = doc.get("content_markdown") or text
    canonical_url = normalize_canonical_url(doc.get("canonical_url"))
    rendered = render_markdown_document(candidate, title=title, canonical_url=canonical_url)
    return {
        "title": rendered.title,
        "markdown": rendered.markdown,
        "text": rendered.body_text,
        "canonical_url": canonical_url,
        "publish_date": doc.get("publication_date"),
        "author": _author_name(doc.get("author")),
        "cms_tags": doc.get("tags", []) or [],
        "format": "json",
        "content_type": _content_type(doc),
        "markdown_schema_version": MARKDOWN_SCHEMA_VERSION,
    }


def _author_name(author: Any) -> str:
    if isinstance(author, dict):
        return author.get("name", "")
    return str(author) if author else ""


def _content_type(document: Dict[str, Any]) -> Optional[str]:
    value = document.get("content_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _scrape_head(body: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(body, "html.parser")

    def meta_content(*, property_name: Optional[str] = None, name: Optional[str] = None) -> Optional[str]:
        attrs = {"property": property_name} if property_name else {"name": name}
        tag = soup.find("meta", attrs=attrs)
        value = tag.get("content") if tag else None
        return str(value).strip() if value else None

    title_tag = soup.find("title")
    title = (
        title_tag.get_text(" ", strip=True) if title_tag else None
    ) or meta_content(property_name="og:title")
    canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical = (
        str(canonical_tag.get("href")).strip()
        if canonical_tag and canonical_tag.get("href")
        else meta_content(property_name="og:url")
    )
    publish_date = meta_content(property_name="article:published_time")
    author = meta_content(name="author")

    return {"title": title, "canonical_url": canonical, "publish_date": publish_date, "author": author}


def _first_heading(md: str) -> Optional[str]:
    m = re.search(r"^#{1,2}\s+(.+)$", md, re.MULTILINE)
    return m.group(1).strip() if m else None
