"""Deterministic Markdown renderer (schema version in ``MARKDOWN_SCHEMA_VERSION``)."""

import html
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import mistune
from bs4 import BeautifulSoup
from mistune.core import BlockState
from mistune.renderers.markdown import MarkdownRenderer
from shared.utils.canonical_url import normalize_canonical_url, resolve_safe_link
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
# Characters that could make markup out of a metadata value. They are dropped, not escaped: the
# Markdown serializer drops escapes on re-render, so an escaped "[x](y)" would become a link.
_BLOCK_UNSAFE = re.compile(r"[\\`*_\[\]{}()<>#|!~]")
# Generated prose keeps ( ) # !, which can't form markup once the characters around them are gone.
_PROSE_UNSAFE = re.compile(r"[\\`*_\[\]{}<>|~]")
# A block marker at the start of a FAQ answer: >, a run of #, - or +, or a list number, each
# followed by a space or the end. Removed until none is left, so "- - -" can't become a rule.
_LEADING_BLOCK_MARKER = re.compile(r"\A(?:>|[#+-]+(?=\s|\Z)|\d+[.)](?=\s|\Z))\s*")
# A heading's closing sequence: runs of # after a space, or a question of # alone. "C#" keeps its #.
_TRAILING_HASHES = re.compile(r"(?:(?:\A|\s+)#+)+\s*\Z")
_FAQ_MAX_PAIRS = 10
_FAQ_HEADING = "## Frequently asked questions (generated)"
_FAQ_NOTICE = "Generated automatically from the article above. Not part of the original article."
_SUMMARY_LABEL = "Summary (generated):"
_ISO_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?\Z")

_PARSER = mistune.create_markdown(
    renderer=None,
    plugins=["table", "strikethrough", "task_lists"],
)
_TEXT_PARSER = mistune.create_markdown(
    renderer="html",
    plugins=["table", "strikethrough", "task_lists"],
)


@dataclass(frozen=True)
class DocumentMetadata:
    """The fields of the metadata block under the H1. Each is the value the record stores."""

    author: Optional[str] = None
    publisher: Optional[str] = None
    published: Optional[str] = None
    updated: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class RenderedMarkdown:
    """Canonical bytes plus semantic metadata derived from the same AST.

    ``markdown`` is the deterministic document: the title, the metadata block, the body and the
    footer. ``body_markdown`` is the body alone, which ``compose_document`` joins with the
    generated parts once enrichment has run.
    """

    markdown: str
    title: str
    body_text: str
    body_markdown: str


def render_markdown(
    source: object,
    *,
    title: object = "",
    canonical_url: Optional[str] = None,
    metadata: Optional[DocumentMetadata] = None,
) -> str:
    """Render source content according to the deterministic Markdown schema."""
    return render_markdown_document(
        source,
        title=title,
        canonical_url=canonical_url,
        metadata=metadata,
    ).markdown


def render_markdown_document(
    source: object,
    *,
    title: object = "",
    canonical_url: Optional[str] = None,
    metadata: Optional[DocumentMetadata] = None,
) -> RenderedMarkdown:
    """Render canonical Markdown and retain unescaped title/body semantics.

    ``metadata`` adds the block under the H1. Like the H1, it is left out of ``body_text``, so
    classification and embedding never see it.
    """
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
    if _block_lines(metadata):
        _separate_leading_list(normalized_tokens)
    body_markdown = _normalize_rendered(_SchemaRenderer(canonical_url)(normalized_tokens, state))
    return RenderedMarkdown(
        markdown=compose_document(title=document_title, body_markdown=body_markdown, metadata=metadata),
        title=document_title,
        body_text=markdown_to_plain_text(body_markdown),
        body_markdown=body_markdown,
    )


def compose_document(
    *,
    title: object,
    body_markdown: object,
    metadata: Optional[DocumentMetadata] = None,
    summary: object = "",
    faq: object = (),
) -> str:
    """Join the document's parts in schema order, leaving out each part with nothing to show.

    ``title`` and ``body_markdown`` are ``RenderedMarkdown.title`` and ``.body_markdown``, rendered
    with the same ``metadata``.
    ``summary`` and ``faq`` are enrichment's ``answer_capsule`` and ``faq_schema``. Every part is
    built from plain text and passed through the schema renderer once, so the document is already
    canonical and ``render_markdown(document)`` returns the same bytes.
    """
    source = normalize_canonical_url(metadata.source) if metadata and metadata.source else None
    renderer = _SchemaRenderer(source)
    title_text = re.sub(r"\s+", " ", str(title or "")).strip() or "Untitled"
    block = _metadata_block(metadata, renderer)
    parts = [
        f"# {_escape_title(title_text)}",
        block,
        _summary_part(summary, renderer),
        _normalize_rendered(str(body_markdown or "")),
        _faq_section(faq, renderer),
        _footer(title_text, metadata, source, renderer) if block else "",
    ]
    return "\n\n".join(part for part in parts if part)


def _metadata_block(metadata: Optional[DocumentMetadata], renderer: "_SchemaRenderer") -> str:
    """Serialize the metadata block, or return "" when no field is known."""
    return _render_fragment(_block_lines(metadata), renderer)


def _block_lines(metadata: Optional[DocumentMetadata]) -> List[str]:
    if metadata is None:
        return []
    fields = (
        ("Author", _plain_value(metadata.author)),
        ("Publisher", _plain_value(metadata.publisher)),
        ("Published", _published_text(metadata.published)),
        ("Updated", _published_text(metadata.updated)),
    )
    lines = [f"- {label}: {value}" for label, value in fields if value]
    source = normalize_canonical_url(metadata.source) if metadata.source else None
    if source:
        lines.append(f"- Source: <{source}>")
    return lines


def _summary_part(summary: object, renderer: "_SchemaRenderer") -> str:
    capsule = _prose(summary) if isinstance(summary, str) else ""
    return _render_fragment([f"> {_SUMMARY_LABEL} {capsule}"], renderer) if capsule else ""


def _faq_section(faq: object, renderer: "_SchemaRenderer") -> str:
    """The generated FAQ: up to ten valid pairs, in enrichment order, or "" without one."""
    pairs = []
    for item in faq if isinstance(faq, Sequence) and not isinstance(faq, str) else ():
        if len(pairs) == _FAQ_MAX_PAIRS:
            break
        if not isinstance(item, Mapping):
            continue
        question, answer = item.get("question"), item.get("answer")
        if not isinstance(question, str) or not isinstance(answer, str):
            continue
        question = _TRAILING_HASHES.sub("", _prose(question))
        answer = _prose(answer)
        while _LEADING_BLOCK_MARKER.match(answer):
            answer = _LEADING_BLOCK_MARKER.sub("", answer, count=1)
        if question and answer:
            pairs.append((question, answer))
    if not pairs:
        return ""
    lines = [_FAQ_HEADING, "", _FAQ_NOTICE]
    for question, answer in pairs:
        lines += ["", f"### {question}", "", answer]
    return _render_fragment(lines, renderer)


def _footer(
    title: str, metadata: Optional[DocumentMetadata], source: Optional[str], renderer: "_SchemaRenderer"
) -> str:
    """The default RSL attribution line: ``Source: {title} by {author}, {publication_date}. {url}``."""
    text = f"Source: {_plain_value(title) or 'Untitled'}"
    author = _plain_value(metadata.author) if metadata else ""
    if author:
        text += f" by {author}"
    published = _published_text(metadata.published) if metadata else ""
    if published:
        text += f", {published}"
    text += "."
    if source:
        text += f" <{source}>"
    return _render_fragment([text], renderer)


def _render_fragment(lines: List[str], renderer: "_SchemaRenderer") -> str:
    """Pass schema-built lines through the renderer once, so they are already in canonical form."""
    if not lines:
        return ""
    tokens, state = _PARSER.parse("\n".join(lines))
    return _normalize_rendered(renderer(tokens, state))


def _separate_leading_list(tokens: List[Dict[str, Any]]) -> None:
    """Keep a body that opens with a ``-`` list from continuing the metadata block's list.

    Two lists with the same bullet, separated by a blank line, parse as one list, so the opening
    list is written with ``*``, or ``+`` when the block after it is a ``*`` list.
    """
    blocks = [token for token in tokens if token.get("type") != "blank_line"]
    if not blocks or not _is_bullet_list(blocks[0], "-"):
        return
    following = blocks[1] if len(blocks) > 1 else {}
    blocks[0]["bullet"] = "+" if _is_bullet_list(following, "*") else "*"


def _is_bullet_list(token: Dict[str, Any], bullet: str) -> bool:
    return token.get("type") == "list" and not token.get("attrs", {}).get("ordered") and token.get("bullet") == bullet


def _prose(value: object) -> str:
    """Collapse whitespace and drop the characters that could form markup in generated prose."""
    text = _PROSE_UNSAFE.sub("", html.unescape(str(value)))
    return re.sub(r"\s+", " ", text).strip()


def _plain_value(value: object) -> str:
    """Collapse whitespace and drop the characters that could form Markdown markup."""
    if value is None:
        return ""
    text = _BLOCK_UNSAFE.sub("", html.unescape(str(value)))
    return re.sub(r"\s+", " ", text).strip()


def _published_text(value: object) -> str:
    """An ISO 8601 date or date-time renders as its calendar date as written; anything else as is."""
    text = _plain_value(value)
    return text[:10] if _ISO_DATE.match(text) else text


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

    def render_referrences(self, state: BlockState) -> Iterable[str]:
        # Links are written inline, so reference definitions would only dangle, and a re-render
        # would move them from the body's end to the document's end.
        return ()

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
