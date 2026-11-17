"""Scrape-vs-licensed-Markdown comparison runs (spec ``scrape-vs-api-comparison``, design §3a).

Two operations behind the ``ui_backend`` route ``/test/comparison-runs`` (Cognito only):

* **submit** — fetch one publisher page server-side under the SSRF guard (``shared.utils.safe_fetch``),
  measure the scrape side (document bytes, same-origin subresource sizes, request count, time),
  make sure the page carries a canonical link it is entitled to (R15.5), and write it under
  ``ingest/comparison/`` so the GEO pipeline renders it into the **comparison partition** (R15.1:
  ``status: comparison``, ``comparison/<url_hash>.md``, never a catalogue record). When the page is
  on a publisher host (``owned``) and its article is already published, the run reuses that record
  and writes nothing (R1.7).
* **poll** — resolve the run's canonical URL through the ``url_hash-index`` GSI; once the run's own
  record (a reused ``published`` one, or its partition record) is newer than the submit time
  (R1.6), read the Markdown object at ``md_s3_key`` and return it with the resolve and fetch
  timings (R11.1: the run reads the object directly, not through the content API).

The run identifier is stateless: ``<url_hash>-<submit epoch>``; there is no run table. Table and
bucket names come from the CDK environment, with the deployed ``cm-*`` names as fallbacks
(steering: contract chain).
"""

from __future__ import annotations

import hashlib
import html as html_lib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import boto3
from botocore.exceptions import ClientError
from shared.utils import response_helpers, safe_fetch
from shared.utils.canonical_url import normalize_canonical_url
from shared.utils.edge_address import edge_address_from_canonical_url
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.safe_fetch import FetchRefused

logger = logging.getLogger(__name__)

# ── Configuration (CDK environment; deployed names as fallbacks) ─────────────

CONTENT_TABLE_NAME = os.environ.get("CONTENT_TABLE_NAME", "cm-content")
CONTENT_BUCKET_NAME = os.environ.get("CONTENT_BUCKET_NAME", "")
URL_HASH_INDEX = "url_hash-index"
INGEST_PREFIX = "ingest/comparison/"

# The submit response carries the fetched document (for the browser-side cleanup steps) inside a
# JSON body, and a Lambda response is capped at 6 MB; 2 MiB of HTML leaves room for escaping.
MAX_DOCUMENT_BYTES = int(os.environ.get("COMPARISON_MAX_DOCUMENT_BYTES", str(2 * 1024 * 1024)))
FETCH_DEADLINE_S = float(os.environ.get("COMPARISON_FETCH_DEADLINE_S", "10"))
PROBE_BUDGET_S = float(os.environ.get("COMPARISON_PROBE_BUDGET_S", "5"))
MAX_SUBRESOURCES = int(os.environ.get("COMPARISON_MAX_SUBRESOURCES", "50"))
PATH_B_MODE = os.environ.get("COMPARISON_PATH_B", "object")  # R11.3: "api" is not built in V1

RUN_ID_RE = re.compile(r"^(?P<hash>[0-9a-f]{64})-(?P<epoch>[0-9]{1,10})$")
_READABLE_STATUS = "published"
PARTITION_STATUS = "comparison"  # set by the pipeline for an ingest/comparison/ object (R15.1)

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")


OPEN_MODE = "*"


class AllowListUnavailable(Exception):
    """The default allow-list could not be derived and no explicit list is configured (fail closed)."""


def allowed_hosts() -> List[str]:
    """Resolve the host allow-list (R7.3, D5-b). Read on every call so a change needs no restart.

    * ``COMPARISON_ALLOWED_HOSTS`` set to ``*`` → open mode (``[]``): any public host that passes the
      SSRF guard. The deployer's explicit choice, for the publisher-URL demo.
    * ``COMPARISON_ALLOWED_HOSTS`` set to a list → that list.
    * Unset → the deployment's own portal host, derived from the same CORS origin allow-list the
      response helpers resolve (``ALLOWED_ORIGINS`` env, else SSM ``/cm/ui/origin`` published by
      CMUIStack). The fixture article lives there, so the default demo works and nothing else is
      reachable. If no origin can be resolved the route refuses rather than opening up.
    """
    raw = os.environ.get("COMPARISON_ALLOWED_HOSTS")
    if raw is not None and raw.strip():
        if raw.strip() == OPEN_MODE:
            return []
        return safe_fetch.parse_allow_list(raw)
    derived = []
    for origin in response_helpers._resolve_allow_list():
        host = (urlsplit(origin).hostname or "").lower()
        if host:
            derived.append(host)
    if not derived:
        raise AllowListUnavailable("no allow-list configured and the portal origin could not be resolved")
    return derived


def publisher_hosts() -> List[str]:
    """The deployment's own hosts (R15.4): ``COMPARISON_PUBLISHER_HOSTS``, the allow-list syntax.

    Unset or empty → ``[]``: no host is owned, so every run is ``external`` (the safe default). Read
    on every call so a change needs no restart.
    """
    return safe_fetch.parse_allow_list(os.environ.get("COMPARISON_PUBLISHER_HOSTS"))


def is_owned_host(hostname: Optional[str], hosts: List[str]) -> bool:
    """True only for a configured publisher host. Unlike the allow-list, an empty list owns nothing."""
    return bool(hosts) and bool(hostname) and safe_fetch.host_allowed(hostname, hosts)


def _content_table():
    return dynamodb.Table(CONTENT_TABLE_NAME)


# ── Identity helpers ─────────────────────────────────────────────────────────


def ingest_key_for(url_hash: str) -> str:
    """The ingest object key for a canonical URL hash. Deterministic: one article, one key."""
    return f"{INGEST_PREFIX}{url_hash[:12]}.html"


def derived_content_id(url_hash: str) -> str:
    """The content id the pipeline assigns to ``ingest_key_for(url_hash)``.

    Mirrors ``content_pipeline.handler._derive_content_id`` (strip ``ingest/``, drop the extension,
    SHA-1 of the stem, 12 hex chars). A unit test imports both and asserts they agree; the id is
    also written as the object's ``content-id`` metadata, which the pipeline honours first, so the
    mirror only has to stay right for the equality check in R1.7.
    """
    stem = ingest_key_for(url_hash)[len("ingest/") :].rsplit(".", 1)[0]
    return "cnt_" + hashlib.sha1(stem.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def partition_markdown_key(url_hash: str) -> str:
    """Where the pipeline writes a comparison ingest's Markdown (mirrors
    ``content_pipeline.handler.comparison_markdown_key``; a unit test asserts they agree)."""
    return f"comparison/{url_hash}.md"


def make_run_id(url_hash: str, submitted_epoch: int) -> str:
    return f"{url_hash}-{submitted_epoch}"


def parse_run_id(run_id: str) -> Optional[Tuple[str, int]]:
    match = RUN_ID_RE.match(run_id or "")
    if not match:
        return None
    return match.group("hash"), int(match.group("epoch"))


# ── HTML helpers (regex only: no HTML execution, no extra dependency) ─────────

_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_HTML_OPEN_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_SUBRESOURCE_RES = (
    (re.compile(r"<script\b[^>]*>", re.IGNORECASE), "src"),
    (re.compile(r"<img\b[^>]*>", re.IGNORECASE), "src"),
    (re.compile(r"<iframe\b[^>]*>", re.IGNORECASE), "src"),
    (re.compile(r"<source\b[^>]*>", re.IGNORECASE), "src"),
    (re.compile(r"<video\b[^>]*>", re.IGNORECASE), "poster"),
)


def _attrs(tag: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for match in _ATTR_RE.finditer(tag):
        name = match.group(1).lower()
        value = next((g for g in match.groups()[1:] if g is not None), "")
        out[name] = html_lib.unescape(value).strip()
    return out


def find_canonical(document: str) -> Optional[str]:
    """``<link rel="canonical" href>`` first, then ``<meta property="og:url">`` — as the extractor does."""
    for tag in _LINK_TAG_RE.finditer(document):
        attrs = _attrs(tag.group(0))
        if "canonical" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            return attrs["href"]
    for tag in _META_TAG_RE.finditer(document):
        attrs = _attrs(tag.group(0))
        if attrs.get("property", "").lower() == "og:url" and attrs.get("content"):
            return attrs["content"]
    return None


def strip_canonical_claims(document: str) -> str:
    """Remove every ``<link rel="canonical">`` and ``<meta property="og:url">`` tag (R15.5).

    Used when a page claims a canonical URL it is not entitled to: after this the pipeline's head
    scraper (first canonical link, then ``og:url``) can only see the URL the route injects.
    """

    def drop_link(match: re.Match) -> str:
        return "" if "canonical" in _attrs(match.group(0)).get("rel", "").lower().split() else match.group(0)

    def drop_meta(match: re.Match) -> str:
        return "" if _attrs(match.group(0)).get("property", "").lower() == "og:url" else match.group(0)

    return _META_TAG_RE.sub(drop_meta, _LINK_TAG_RE.sub(drop_link, document))


def canonical_entitled(canonical_url: str, final_url: str, owned: bool, hosts: List[str]) -> bool:
    """May the page claim ``canonical_url``? (R15.5)

    * owned page → only a publisher host;
    * external page → only its own final-URL host, so it cannot point at another site's article.
    """
    claimed = (urlsplit(canonical_url).hostname or "").lower().rstrip(".")
    if not claimed:
        return False
    if owned:
        return is_owned_host(claimed, hosts)
    return claimed == (urlsplit(final_url).hostname or "").lower().rstrip(".")


def inject_canonical(document: str, canonical_url: str) -> str:
    """Add ``<link rel="canonical">`` where the pipeline's head scraper will find it."""
    link = f'<link rel="canonical" href="{html_lib.escape(canonical_url, quote=True)}">'
    match = _HEAD_CLOSE_RE.search(document)
    if match:
        return document[: match.start()] + link + document[match.start() :]
    match = _HTML_OPEN_RE.search(document)
    if match:
        return document[: match.end()] + f"<head>{link}</head>" + document[match.end() :]
    return f"<head>{link}</head>" + document


def discover_subresources(document: str, base_url: str) -> Tuple[List[str], List[str]]:
    """Return ``(same_origin, cross_origin)`` absolute subresource URLs, deduplicated, in order."""
    candidates: List[str] = []
    for pattern, attr in _SUBRESOURCE_RES:
        for tag in pattern.finditer(document):
            value = _attrs(tag.group(0)).get(attr)
            if value:
                candidates.append(value)
    for tag in _LINK_TAG_RE.finditer(document):
        attrs = _attrs(tag.group(0))
        if "stylesheet" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            candidates.append(attrs["href"])

    same: List[str] = []
    cross: List[str] = []
    for value in candidates:
        lowered = value.lower()
        if lowered.startswith(("data:", "blob:", "javascript:", "about:", "#")):
            continue
        absolute = urljoin(base_url, value)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        (same if safe_fetch.same_origin(base_url, absolute) else cross).append(absolute)
    return safe_fetch.unique(same), safe_fetch.unique(cross)


_LINK_HEADER_PART_RE = re.compile(r"<([^>]+)>\s*((?:;\s*[^,;]+)*)")


def licence_from_link_header(link_header: Optional[str]) -> Optional[str]:
    """The target of a ``rel="license"`` entry in a ``Link`` response header, or ``None``."""
    if not link_header:
        return None
    for match in _LINK_HEADER_PART_RE.finditer(link_header):
        target, params = match.group(1), match.group(2)
        if re.search(r"""rel\s*=\s*"?[^";]*\blicense\b""", params, re.IGNORECASE):
            return target.strip()
    return None


# ── Scrape side ──────────────────────────────────────────────────────────────


def measure_scrape(url: str, allow_list: List[str]) -> Tuple[safe_fetch.FetchResult, Dict[str, Any]]:
    """Fetch the document and probe its same-origin subresources; return the fetch and the figures."""
    started = time.monotonic()
    fetched = safe_fetch.fetch_html(
        url, allow_list=allow_list, max_bytes=MAX_DOCUMENT_BYTES, deadline_s=FETCH_DEADLINE_S
    )
    document = fetched.body.decode("utf-8", errors="replace")
    same, cross = discover_subresources(document, fetched.final_url)

    total_bytes = len(fetched.body)
    probed = 0
    skipped = 0
    probe_deadline = time.monotonic() + PROBE_BUDGET_S
    for resource in same[:MAX_SUBRESOURCES]:
        remaining = probe_deadline - time.monotonic()
        if remaining <= 0:
            skipped += 1
            continue
        try:
            probe = safe_fetch.probe_size(resource, allow_list=allow_list, deadline_s=remaining)
        except FetchRefused:
            skipped += 1
            continue
        probed += 1
        if probe.content_length is not None:
            total_bytes += probe.content_length
    skipped += max(0, len(same) - MAX_SUBRESOURCES)

    scrape = {
        "status": fetched.status,
        "document_bytes": len(fetched.body),
        "total_bytes": total_bytes,
        "requests": 1 + len(same),
        "external_refs": len(cross),
        "document_ms": fetched.wall_clock_ms,
        "total_ms": int((time.monotonic() - started) * 1000),
        "subresources_probed": probed,
        "subresources_skipped": skipped,
    }
    return fetched, scrape


# ── Records ──────────────────────────────────────────────────────────────────


def _query_by_url_hash(url_hash: str) -> Tuple[List[Dict[str, Any]], int]:
    started = time.monotonic()
    response = _content_table().query(
        IndexName=URL_HASH_INDEX,
        KeyConditionExpression="url_hash = :hash",
        ExpressionAttributeValues={":hash": url_hash},
    )
    return response.get("Items", []), int((time.monotonic() - started) * 1000)


def _published(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    published = [item for item in items if item.get("status") == _READABLE_STATUS]
    if not published:
        return None
    # One article, one record. Should two exist (a legacy ingest and a comparison run), prefer
    # the newest so the poll sees the most recent publish.
    published.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return published[0]


def _partition_record(items: List[Dict[str, Any]], url_hash: str) -> Optional[Dict[str, Any]]:
    """The run's own comparison-partition record (R15.3): its derived id, ``status: comparison``.

    A catalogue record for the same URL is never accepted for a submitted run, so the run always
    measures the Markdown it ingested itself.
    """
    content_id = derived_content_id(url_hash)
    for item in items:
        if item.get("content_id") == content_id and item.get("status") == PARTITION_STATUS:
            return item
    return None


def _epoch(iso_timestamp: Any) -> Optional[float]:
    if not isinstance(iso_timestamp, str) or not iso_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


# ── Operations ───────────────────────────────────────────────────────────────


def submit(
    user: Dict[str, Any], request_data: Dict[str, Any], event: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """``POST /test/comparison-runs`` (design §3a submit flow)."""
    url = request_data.get("url") if isinstance(request_data, dict) else None
    reingest = bool(request_data.get("reingest")) if isinstance(request_data, dict) else False
    try:
        allow_list = allowed_hosts()
    except AllowListUnavailable as exc:
        logger.error("comparison allow-list unavailable: %s", exc)
        return create_error_response(
            503,
            "The comparison allow-list is not configured and the portal origin could not be resolved",
            error_code="allow_list_unavailable",
            event=event,
        )
    allow_list_mode = "restricted" if allow_list else "open"

    try:
        fetched, scrape = measure_scrape(url, allow_list)
    except FetchRefused as refused:
        status = 400
        logger.warning("comparison fetch refused code=%s url=%r", refused.error_code, url)
        return create_error_response(status, refused.message, error_code=refused.error_code, event=event)

    hosts = publisher_hosts()
    owned = is_owned_host(urlsplit(fetched.final_url).hostname, hosts)
    origin = "owned" if owned else "external"

    document = fetched.body.decode("utf-8", errors="replace")
    canonical_raw = find_canonical(document)
    canonical_injected = False
    canonical_overridden = False
    claimed = normalize_canonical_url(canonical_raw)
    if claimed is not None and not canonical_entitled(claimed, fetched.final_url, owned, hosts):
        # R15.5: the page may not choose which article it is. Drop its claim, use the final URL.
        logger.warning("comparison canonical claim overridden origin=%s claimed=%r", origin, claimed)
        document = strip_canonical_claims(document)
        claimed = None
        canonical_overridden = True
    if claimed is None:
        canonical_raw = fetched.final_url
        document = inject_canonical(document, canonical_raw)
        canonical_injected = not canonical_overridden

    address = edge_address_from_canonical_url(canonical_raw)
    if not address.eligible or not address.url_hash or not address.s3_key:
        return create_error_response(
            400,
            f"The page's canonical URL cannot be addressed by the pipeline ({address.reason})",
            error_code="canonical_ineligible",
            event=event,
        )

    submitted_epoch = int(time.time())
    content_id = derived_content_id(address.url_hash)
    # R1.7 / R15: only the customer's own, already-published article is reused. Everything else goes
    # to the comparison partition, which never touches a catalogue record, so nothing can collide.
    existing = _published(_query_by_url_hash(address.url_hash)[0]) if owned and not reingest else None
    if existing is not None:
        ingest = "reused"
        submitted_epoch = 0
        markdown_key = existing.get("md_s3_key") or address.s3_key
    else:
        ingest = "submitted"
        markdown_key = partition_markdown_key(address.url_hash)
        s3.put_object(
            Bucket=CONTENT_BUCKET_NAME,
            Key=ingest_key_for(address.url_hash),
            Body=document.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            Metadata={
                "content-id": content_id,
                "source": "comparison",
                "submitted-by": str(user.get("user_id") or user.get("username") or "unknown")[:128],
                "website-url": str(url)[:512],
            },
        )
        logger.info("comparison ingest queued key=%s content_id=%s", ingest_key_for(address.url_hash), content_id)

    body = {
        "run_id": make_run_id(address.url_hash, submitted_epoch),
        "submitted_at": datetime.fromtimestamp(submitted_epoch or time.time(), tz=timezone.utc).isoformat(),
        "ingest": ingest,
        "origin": origin,
        "allow_list_mode": allow_list_mode,
        "article": {
            "website_url": url,
            "final_url": fetched.final_url,
            "canonical_url": address.canonical_url,
            "canonical_injected": canonical_injected,
            "canonical_overridden": canonical_overridden,
            "markdown_key": markdown_key,
            "licence_advertised": licence_from_link_header(fetched.headers.get("link")),
        },
        "scrape": scrape,
        "html": document,
    }
    return create_success_response(body, 202, event=event)


def poll(run_id: str, event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``GET /test/comparison-runs/{run_id}`` (design §3a poll flow)."""
    parsed = parse_run_id(run_id)
    if parsed is None:
        return create_error_response(404, "Unknown run", error_code="run_not_found", event=event)
    url_hash, submitted_epoch = parsed
    elapsed_ms = int((time.time() - submitted_epoch) * 1000) if submitted_epoch else 0

    items, resolve_ms = _query_by_url_hash(url_hash)
    record = _published(items) if not submitted_epoch else _partition_record(items, url_hash)
    if record is None:
        return create_success_response({"run_id": run_id, "status": "pending", "elapsed_ms": elapsed_ms}, event=event)
    updated_epoch = _epoch(record.get("updated_at"))
    if submitted_epoch and (updated_epoch is None or updated_epoch <= submitted_epoch):
        return create_success_response({"run_id": run_id, "status": "pending", "elapsed_ms": elapsed_ms}, event=event)

    md_key = record.get("md_s3_key")
    if not md_key:
        # A record without an object cannot be served; the poll stays pending rather than inventing a body.
        return create_success_response({"run_id": run_id, "status": "pending", "elapsed_ms": elapsed_ms}, event=event)

    started = time.monotonic()
    try:
        obj = s3.get_object(Bucket=CONTENT_BUCKET_NAME, Key=md_key)
        markdown_bytes = obj["Body"].read()
    except ClientError as exc:
        logger.error(
            "comparison markdown read failed key=%s error=%s", md_key, exc.response.get("Error", {}).get("Code")
        )
        return create_error_response(500, "Failed to read the published Markdown", event=event)
    fetch_ms = int((time.monotonic() - started) * 1000)

    api = {
        "content_id": record.get("content_id"),
        "resolve_ms": resolve_ms,
        "fetch_ms": fetch_ms,
        "bytes": len(markdown_bytes),
        "markdown": markdown_bytes.decode("utf-8", errors="replace"),
        "etag": (obj.get("ETag") or "").strip('"') or None,
        "updated_at": record.get("updated_at"),
        "provenance": {
            "title": record.get("title"),
            "author": record.get("author") or None,
            "pubdate": record.get("publish_date"),
            "canonical_url": record.get("canonical_url"),
        },
    }
    return create_success_response(
        {"run_id": run_id, "status": "ready", "elapsed_ms": elapsed_ms, "api": api}, event=event
    )
