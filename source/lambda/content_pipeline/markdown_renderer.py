"""Deterministic Markdown schema 1.0 renderer."""

import html
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import mistune
from bs4 import BeautifulSoup
from mistune.core import BlockState
from mistune.renderers.markdown import MarkdownRenderer
from shared.utils.canonical_url import resolve_safe_link
from shared.utils.models import MARKDOWN_SCHEMA_VERSION

_FRONTMATTER = re.compile(r"\A(?:\ufeff)?---[ \t]*\n.*?\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)
_REMOVED_HTML_BLOCK = re.compile(
    r"(?is)<(script|style|nav|header|footer|aside)\b[^>]*>.*?</\1\s*>"
)
_HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
_HTML_IMAGE = re.compile(r"(?is)<img\b[^>]*>")
_TRAILING_WHITESPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_MARKDOWN_TITLE_CHARACTER = re.compile(r"([\\`*_[\]{}()#+.!|<>])")
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^<>\s]+)>", re.IGNORECASE)

_PARSER = mistune.create_markdown(
    renderer=None,
    plugins=["table", "strikethrough", "task_lists"],
)
_TEXT_PARSER = mistune.create_markdown(
    renderer="html",
    plugins=["table", "strikethrough", "task_lists"],
)


@dataclass(frozen=True)
class RenderedMarkdown:
    """Canonical bytes plus semantic metadata derived from the same AST."""

    markdown: str
    title: str
    body_text: str


def render_markdown(
    source: object,
    *,
    title: object = "",
    canonical_url: Optional[str] = None,
) -> str:
    """Render source content according to deterministic Markdown schema 1.0."""
    return render_markdown_document(
        source,
        title=title,
        canonical_url=canonical_url,
    ).markdown


def render_markdown_document(
    source: object,
    *,
    title: object = "",
    canonical_url: Optional[str] = None,
) -> RenderedMarkdown:
    """Render canonical Markdown and retain unescaped title/body semantics."""
    cleaned = _sanitize_source(source)
    tokens, state = _PARSER.parse(cleaned)
    normalized_tokens = deepcopy(tokens)

    explicit_title = _clean_title(title)
    first_heading_index = _first_top_level_heading(normalized_tokens)
    source_title = (
        _plain_node_text(normalized_tokens[first_heading_index])
        if first_heading_index is not None
        else ""
    )
    document_title = explicit_title or source_title or "Untitled"

    if first_heading_index is not None and _same_title(source_title, document_title):
        normalized_tokens.pop(first_heading_index)

    _demote_h1_tokens(normalized_tokens)
    renderer = _SchemaRenderer(canonical_url)
    body_markdown = _normalize_rendered(renderer(normalized_tokens, state))
    normalized_tokens.insert(
        0,
        {
            "type": "heading",
            "attrs": {"level": 1},
            "style": "atx",
            "children": [{"type": "text", "raw": document_title}],
            "schema_title": True,
        },
    )

    rendered = _normalize_rendered(renderer(normalized_tokens, state))
    return RenderedMarkdown(
        markdown=rendered,
        title=document_title,
        body_text=markdown_to_plain_text(body_markdown),
    )


def markdown_to_plain_text(markdown: str) -> str:
    """Return stable plain text from already-rendered Markdown."""
    rendered_html = _TEXT_PARSER(markdown or "")
    soup = BeautifulSoup(rendered_html, "html.parser")
    for image in soup.find_all("img"):
        image.replace_with(image.get("alt", ""))
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


class _SchemaRenderer(MarkdownRenderer):
    def __init__(self, canonical_url: Optional[str]) -> None:
        super().__init__()
        self.canonical_url = canonical_url

    def heading(self, token: Dict[str, Any], state: BlockState) -> str:
        if token.get("schema_title"):
            return f"# {_escape_title(_plain_node_text(token))}\n\n"
        elif token.get("attrs", {}).get("level") == 1:
            token = {**token, "attrs": {**token["attrs"], "level": 2}}
        return super().heading(token, state)

    def link(self, token: Dict[str, Any], state: BlockState) -> str:
        text = self.render_children(token, state)
        safe_url = resolve_safe_link(token.get("attrs", {}).get("url"), self.canonical_url)
        if safe_url is None:
            return text
        normalized = {
            **token,
            "label": None,
            "attrs": {**token.get("attrs", {}), "url": safe_url},
        }
        return super().link(normalized, state)

    def image(self, token: Dict[str, Any], state: BlockState) -> str:
        return self.render_children(token, state).strip()

    def strikethrough(self, token: Dict[str, Any], state: BlockState) -> str:
        return "~~" + self.render_children(token, state) + "~~"

    def inline_html(self, token: Dict[str, Any], state: BlockState) -> str:
        raw = token.get("raw", "")
        if re.match(r"(?is)<br\s*/?>", raw):
            return "\n"
        return _visible_html_text(raw)

    def block_html(self, token: Dict[str, Any], state: BlockState) -> str:
        visible = _visible_html_text(token.get("raw", ""))
        return f"{visible}\n\n" if visible else ""


def _sanitize_source(source: object) -> str:
    if source is None:
        value = ""
    elif isinstance(source, str):
        value = source
    else:
        value = str(source)

    value = value.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    value = _FRONTMATTER.sub("", value, count=1)
    value = _REMOVED_HTML_BLOCK.sub("", value)
    value = _HTML_COMMENT.sub("", value)
    value = _HTML_IMAGE.sub(_replace_html_image, value)
    return value


def _replace_html_image(match: re.Match[str]) -> str:
    tag = BeautifulSoup(match.group(0), "html.parser").find("img")
    return html.unescape(tag.get("alt", "")).strip() if tag else ""


def _visible_html_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    for image in soup.find_all("img"):
        image.replace_with(image.get("alt", ""))
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _clean_title(value: object) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # Preserve literal autolinks as plain title text while normal Markdown
    # links/images still reduce to their visible labels.
    protected = _AUTOLINK.sub(r"\\<\1\\>", text.replace("\n", " "))
    tokens, _ = _PARSER.parse(protected)
    parts = [_plain_node_text(token) for token in tokens]
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


def _escape_title(value: str) -> str:
    return _MARKDOWN_TITLE_CHARACTER.sub(r"\\\1", value)


def _first_top_level_heading(tokens: List[Dict[str, Any]]) -> Optional[int]:
    for index, token in enumerate(tokens):
        if token.get("type") == "heading":
            return index
    return None


def _plain_node_text(token: Dict[str, Any]) -> str:
    parts: List[str] = []
    _collect_inline_text(token, parts)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _collect_inline_text(token: Dict[str, Any], parts: List[str]) -> None:
    token_type = token.get("type")
    if token_type in {"text", "codespan", "block_code", "inline_html"}:
        raw = str(token.get("raw", ""))
        if raw:
            parts.append(raw)
    for child in token.get("children", []):
        _collect_inline_text(child, parts)


def _same_title(left: str, right: str) -> bool:
    return re.sub(r"\s+", " ", left).strip().casefold() == re.sub(r"\s+", " ", right).strip().casefold()


def _demote_h1_tokens(tokens: Iterable[Dict[str, Any]]) -> None:
    for token in tokens:
        if token.get("type") == "heading" and token.get("attrs", {}).get("level") == 1:
            token["attrs"]["level"] = 2
        _demote_h1_tokens(token.get("children", []))


def _normalize_rendered(rendered: str) -> str:
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    rendered = _TRAILING_WHITESPACE.sub("", rendered)
    rendered = _EXCESS_BLANK_LINES.sub("\n\n", rendered)
    return rendered.strip("\n")
