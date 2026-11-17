"""CoMP request validation, discovery normalization, and Package construction."""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import boto3
from shared.utils.canonical_url import normalize_canonical_url

from services.read_references import ReadContext, plane_for, read_reference, retrieval_object
from services.search_service import DiscoveryRequest, SearchUnavailableError, SectionPair, validate_similarity_threshold

logger = logging.getLogger(__name__)

COMP_FUNCTION_CODES = {
    0: "all",
    1: "ai-all",
    2: "ai-train",
    3: "ai-input",
    4: "ai-index",
    5: "search",
}

COMP_FUNCTION_NAMES = {
    0: "All Uses",
    1: "All AI Uses",
    2: "Training",
    3: "Inference/RAG",
    4: "Indexing",
    5: "Search",
}

COMP_SUB_FUNCTION_CODES = {
    0: "training",
    1: "rag",
    2: "grounding",
    3: "agent-view",
    4: "agent-actions",
    5: "other",
}

MAX_DISCOVERY_VALUES = 50
MAX_SECTION_URIS = 10
MAX_QUERY_LENGTH = 512


class CompRequestValidationError(ValueError):
    """A CoMP request cannot be translated to a safe discovery request."""


def get_licenses_table():
    """Get the latest-version license table with lazy initialization."""
    if not hasattr(get_licenses_table, "_table"):
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ.get("LICENSES_TABLE_NAME", "cm-licenses")
        get_licenses_table._table = dynamodb.Table(table_name)
    return get_licenses_table._table


def is_comp_request(request_data: Dict[str, Any]) -> bool:
    """Return whether the request uses the CoMP AISystem envelope."""
    return "aisystem" in request_data


def validate_comp_request(request_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Validate the minimum CoMP envelope before license and search work."""
    aisystem = request_data.get("aisystem", {})
    if not isinstance(aisystem, dict) or not aisystem.get("name"):
        return False, "aisystem.name is required"
    aisysuse = aisystem.get("aisysuse", {})
    if not isinstance(aisysuse, dict):
        return False, "aisystem.aisysuse is required"
    if not aisysuse.get("lid"):
        return False, "aisystem.aisysuse.lid (license ID) is required"
    if "aiauth" not in aisysuse:
        return False, "aisystem.aisysuse.aiauth is required"
    return True, None


def get_license_from_db(license_id: str) -> Optional[Dict[str, Any]]:
    """Return the latest active license document, if it exists."""
    try:
        response = get_licenses_table().query(
            KeyConditionExpression="license_id = :lid",
            ExpressionAttributeValues={":lid": license_id},
            ScanIndexForward=False,
            Limit=1,
        )
    except Exception as exc:
        logger.error("Error fetching license %s: %s", license_id, exc)
        return None
    items = response.get("Items", [])
    if not items or items[0].get("status") == "deleted":
        return None
    return items[0]


def validate_comp_license(
    license_id: str, requested_functions: List[int]
) -> tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Validate declared functions against the current license record."""
    license_doc = get_license_from_db(license_id)
    if not license_doc:
        return False, f"License {license_id} not found or inactive", None
    allowed_uses = license_doc.get("allowed_uses", [])
    denied = [function for function in requested_functions if function not in allowed_uses]
    if denied:
        denied_names = [COMP_FUNCTION_NAMES.get(value, f"Function {value}") for value in denied]
        allowed_names = [COMP_FUNCTION_NAMES.get(value, f"Function {value}") for value in allowed_uses]
        return (
            False,
            (
                f"Requested functions {denied} ({', '.join(denied_names)}) "
                f"not permitted under license {license_id}. "
                f"License allows: {allowed_uses} ({', '.join(allowed_names)})"
            ),
            license_doc,
        )
    return True, None, license_doc


def build_comp_discovery_request(aisysuse: Dict[str, Any]) -> DiscoveryRequest:
    """Translate CoMP scope plus the publisher discovery extension safely."""
    scope = aisysuse.get("scope", 0)
    if isinstance(scope, bool) or not isinstance(scope, int) or scope not in range(7):
        raise CompRequestValidationError("aisystem.aisysuse.scope must be an integer from 0 through 6")

    extension = aisysuse.get("ext") or {}
    if not isinstance(extension, dict):
        raise CompRequestValidationError("aisystem.aisysuse.ext must be an object")
    discovery = extension.get("discovery") or {}
    if not isinstance(discovery, dict):
        raise CompRequestValidationError("aisystem.aisysuse.ext.discovery must be an object")

    query_text = _bounded_text(discovery.get("query"), "ext.discovery.query")
    similarity_threshold = None
    if "similarity_threshold" in discovery:
        try:
            similarity_threshold = validate_similarity_threshold(discovery["similarity_threshold"], query_text)
        except ValueError as exc:
            raise CompRequestValidationError(f"ext.discovery.{exc}") from exc
    topics = _bounded_strings(discovery.get("topics"), "ext.discovery.topics")
    category_ids = _bounded_strings(discovery.get("cat"), "ext.discovery.cat")
    content_ids = _bounded_strings(discovery.get("content_ids"), "ext.discovery.content_ids")
    limit = _limit(discovery.get("max_results", 10))
    start_date, end_date = _date_range(discovery.get("date_range"))
    section_pairs = _section_pairs(aisysuse.get("uri", [])) if scope == 1 else []

    if scope == 1 and not section_pairs:
        raise CompRequestValidationError("scope 1 requires one to ten valid uri values")
    if scope == 2 and not (start_date or end_date):
        raise CompRequestValidationError("scope 2 requires ext.discovery.date_range")
    if scope == 3 and not category_ids:
        raise CompRequestValidationError("scope 3 requires ext.discovery.cat")
    if scope == 4 and not (query_text or topics):
        raise CompRequestValidationError("scope 4 requires ext.discovery.query or ext.discovery.topics")
    if scope == 5 and not content_ids:
        raise CompRequestValidationError("scope 5 requires ext.discovery.content_ids")
    if scope == 6 and not (query_text or topics or category_ids or content_ids or start_date or end_date):
        raise CompRequestValidationError("scope 6 requires an explicit discovery filter")

    return DiscoveryRequest(
        query_text=query_text,
        iab_category_ids=category_ids,
        topics=topics,
        publish_date_start=start_date,
        publish_date_end=end_date,
        section_pairs=section_pairs,
        content_ids=content_ids,
        limit=limit,
        similarity_threshold=similarity_threshold,
    )


def build_comp_text_asset(content_item: Dict[str, Any], read_ctx: ReadContext) -> Dict[str, Any]:
    """Build one CoMP Text asset from a denormalized discovery result.

    ``ext`` carries the publisher extensions ``content_id`` and ``url``: the item's identity and
    the read reference the caller can follow with the credential that admitted the query.
    """
    content_id = content_item.get("content_id") or ""
    return {
        "title": content_item.get("title"),
        "cat": [
            category.get("id")
            for category in content_item.get("iab_categories", [])
            if isinstance(category, dict) and category.get("id")
        ],
        "cattax": 9,
        "language": [content_item.get("language", "en")],
        "wordcount": [int(content_item.get("word_count") or 0)],
        "pubdate": content_item.get("publication_date") or content_item.get("publish_date"),
        "published": 1,
        "author": [content_item["author"]] if content_item.get("author") else [],
        "sourcetype": 0,
        "provenance": 0,
        "markdown_schema_version": content_item.get("markdown_schema_version", "legacy"),
        "ext": {
            "content_id": content_id,
            "url": read_reference(read_ctx, content_id, content_item.get("canonical_url")),
        },
    }


def build_comp_package_response(
    aisystem: Dict[str, Any],
    aisysuse: Dict[str, Any],
    search_results: List[Dict[str, Any]],
    license_doc: Dict[str, Any],
    read_ctx: ReadContext,
) -> Dict[str, Any]:
    """Build the CoMP Package for a validated, licensed discovery request.

    ``retrieval`` describes the read contract (``GET <base>/content``) for the caller's plane, and
    every Text asset carries its own read reference in ``ext.url``.
    """
    api_base_url = read_ctx.base_url
    package_id = f"PKG-{uuid.uuid4().hex[:8].upper()}"
    license_id = aisysuse.get("lid")
    scope = aisysuse.get("scope", 0)
    text_assets = []
    seen_content_ids = set()
    for result in search_results:
        if result.get("content_type") not in {"article", "report", "text"}:
            continue
        content_id = result.get("content_id")
        if content_id in seen_content_ids:
            continue
        if content_id:
            seen_content_ids.add(content_id)
        text_assets.append(build_comp_text_asset(result, read_ctx))
    return {
        "package": {
            "id": package_id,
            "title": f"Content Package for {aisystem.get('name', 'AI System')}",
            "seller": os.environ.get("PUBLISHER_DOMAIN", "publisher.com"),
            "licenseurl": f"{api_base_url}/licenses/{license_id}",
            "reporturl": f"{api_base_url}/log",
            "citation": 1 if aisysuse.get("resdis", 0) == 1 else 0,
            "scope": {
                "scope": scope,
                "ause": license_doc.get("allowed_use_type", 0),
                "max": 0,
                "ctype": [0],
                "text": text_assets,
            },
            "retrieval": retrieval_object(read_ctx),
        },
        "_meta": {
            "request_id": f"req_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "comp_version": "1.0",
            "total_results": len(text_assets),
            "license_validated": True,
        },
    }


def handle_comp_query_request(
    request_data: Dict[str, Any],
    user: Dict[str, Any],
    auth_type: str,
    search_function: Callable[[DiscoveryRequest], List[Dict[str, Any]]],
    api_base_url: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Process a validated CoMP request through the shared discovery adapter."""
    del user
    read_ctx = ReadContext(plane=plane_for(auth_type), base_url=api_base_url.rstrip("/"))
    valid, error = validate_comp_request(request_data)
    if not valid:
        return None, error
    aisystem = request_data["aisystem"]
    aisysuse = aisystem["aisysuse"]
    license_id = aisysuse["lid"]
    functions = aisysuse.get("function", [])

    try:
        discovery_request = build_comp_discovery_request(aisysuse)
    except CompRequestValidationError as exc:
        return None, str(exc)

    if license_id == "PENDING_REVIEW":
        return _pending_license_response(read_ctx), None

    license_valid, license_error, license_doc = validate_comp_license(license_id, functions)
    if not license_valid:
        return None, license_error
    try:
        results = search_function(discovery_request)
    except SearchUnavailableError:
        raise
    return build_comp_package_response(aisystem, aisysuse, results, license_doc or {}, read_ctx), None


def _bounded_text(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CompRequestValidationError(f"{field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > MAX_QUERY_LENGTH:
        raise CompRequestValidationError(f"{field_name} must contain 1 through {MAX_QUERY_LENGTH} characters")
    return normalized


def _bounded_strings(value: object, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > MAX_DISCOVERY_VALUES:
        raise CompRequestValidationError(f"{field_name} must contain 1 through {MAX_DISCOVERY_VALUES} values")
    values = []
    for item in value:
        if not isinstance(item, (str, int)) or not str(item).strip():
            raise CompRequestValidationError(f"{field_name} values must be non-empty strings")
        values.append(str(item).strip())
    return values


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise CompRequestValidationError("ext.discovery.max_results must be an integer from 1 through 50")
    return value


def _date_range(value: object) -> tuple[Optional[str], Optional[str]]:
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise CompRequestValidationError("ext.discovery.date_range must be an object")
    start = _timestamp(value.get("start_date"), "ext.discovery.date_range.start_date")
    end = _timestamp(value.get("end_date"), "ext.discovery.date_range.end_date")
    if start and end and start > end:
        raise CompRequestValidationError("ext.discovery.date_range.start_date must not be later than end_date")
    return start, end


def _timestamp(value: object, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CompRequestValidationError(f"{field_name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompRequestValidationError(f"{field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise CompRequestValidationError(f"{field_name} must include a timezone")
    return parsed.isoformat()


def _section_pairs(value: object) -> List[SectionPair]:
    if not isinstance(value, list) or not value or len(value) > MAX_SECTION_URIS:
        raise CompRequestValidationError(f"scope 1 uri must contain 1 through {MAX_SECTION_URIS} values")
    pairs: List[SectionPair] = []
    seen = set()
    for uri in value:
        normalized = normalize_canonical_url(uri)
        if not normalized:
            raise CompRequestValidationError("scope 1 uri values must be normalized HTTP(S) URLs")
        parsed = urlsplit(normalized)
        ancestor = (parsed.path or "/").rstrip("/") or "/"
        key = (parsed.netloc, ancestor)
        if key not in seen:
            seen.add(key)
            pairs.append(SectionPair(canonical_host=parsed.netloc, canonical_path_ancestor=ancestor))
    return pairs


def _pending_license_response(read_ctx: ReadContext) -> Dict[str, Any]:
    """A licence is required first: no assets, and ``retrieval`` names the read a licensed caller uses."""
    api_base_url = read_ctx.base_url
    return {
        "package": {
            "id": "PKG-PENDING",
            "title": "License Required",
            "seller": os.environ.get("PUBLISHER_DOMAIN", "publisher.com"),
            "licenseurl": f"{api_base_url}/licenses",
            "reporturl": f"{api_base_url}/log",
            "citation": 1,
            "scope": {"scope": 0, "max": 1, "ctype": [0]},
            "retrieval": retrieval_object(read_ctx),
        },
        "_meta": {"message": "Please visit licenseurl to negotiate terms before accessing content", "status": "pending_license"},
    }
