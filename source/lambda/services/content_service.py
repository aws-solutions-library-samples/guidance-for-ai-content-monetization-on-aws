"""
Content domain service (Service_Layer single home for the content domain).

This module is the designated ``services/*`` home for the ui_backend content domain
(Requirement 1.1 / 7.1 / 7.4): content CRUD, bulk operations, reprocessing, and all
DynamoDB/S3 access for content now live here as the single source of truth. The business
logic and data-store access were bodily extracted from the former top-level
``content_management`` module (now deleted); ``ContentService.handle_request`` is the
public entry and no longer delegates back to a root handler module.

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``try/except ImportError`` shim (Requirement 5.1
/ 5.2) and no ``sys.path`` manipulation (Requirement 15.2).
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.exceptions import ClientError
from shared.content_delete import DELETING_STATUS, ContentNotFound, soft_delete

# Import response validation middleware
# ``partner_error_response`` is the partner-plane error shape (``error`` message plus a registered
# ``error_code``) used for codes listed in x-error-codes.
from shared.utils.response_helpers import cors_headers
from shared.utils.response_helpers import create_error_response as partner_error_response
from shared.utils.response_validator import validate_response

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
cognito = boto3.client("cognito-idp")
events = boto3.client("events")

content_table = dynamodb.Table(os.environ["CONTENT_TABLE_NAME"])
jobs_table = dynamodb.Table(os.environ["JOBS_TABLE_NAME"])
content_bucket = os.environ["CONTENT_BUCKET_NAME"]

# Import TaxonomyManager for database-driven taxonomy
from services.processing_jobs_service import create_processing_job
from services.taxonomy_manager import TaxonomyManager
from services.usage_emitter import DEFAULT_CURRENCY, SOURCE_OBSERVED, emit_usage_event

# ``content_type`` stamped on the usage event for a URL-addressable retrieval (#3 task 10.1).
# Names *what kind of access* this was, keeping it distinguishable in the raw event store from
# the discovery surfaces.
USAGE_CONTENT_TYPE_URL_RETRIEVAL = "url_retrieval"
# A comparison run's short-lived record (spec scrape-vs-api-comparison R15). Not catalogue content, so
# the Content Library list leaves it out unless the caller filters on this status (R15.1.5).
COMPARISON_STATUS = "comparison"

# Initialize taxonomy manager
taxonomy_manager = TaxonomyManager()


# IAB Category standardization
def get_iab_taxonomy_map() -> Dict[str, Dict[str, Any]]:
    """Get IAB taxonomy mapping for category standardization"""
    return taxonomy_manager.get_all_categories()


def standardize_iab_categories(iab_categories: Any) -> List[Dict[str, Any]]:
    """
    Convert IAB categories from any format to standardized OpenAPI format

    Args:
        iab_categories: Categories in any format (list of IDs, strings, or objects)

    Returns:
        List of standardized category objects matching OpenAPI spec
    """
    if not iab_categories:
        return []

    taxonomy_map = get_iab_taxonomy_map()
    standardized = []

    for category in iab_categories:
        if isinstance(category, dict) and "id" in category:
            # Already in standard format - ensure all required fields
            standardized_cat = {
                "id": category["id"],
                "name": category.get("name", f"Category {category['id']}"),
                "tier_1": category.get("tier_1", category.get("name", f"Category {category['id']}")),
                "tier_2": category.get("tier_2"),
                "tier_3": category.get("tier_3"),
                "tier_4": category.get("tier_4"),
                "parent_id": category.get("parent_id"),
            }
            standardized.append(standardized_cat)
        elif isinstance(category, int):
            # Simple ID format - convert to full object
            category_info = taxonomy_map.get(str(category))
            if category_info:
                standardized.append(category_info)
            else:
                logger.warning(f"Unknown IAB category ID: {category}")
                # Create minimal object for unknown IDs (exactly one — wave0 Req 12)
                standardized.append(
                    {
                        "id": category,
                        "name": f"Unknown Category {category}",
                        "tier_1": f"Unknown Category {category}",
                        "tier_2": None,
                        "tier_3": None,
                        "tier_4": None,
                        "parent_id": None,
                    }
                )
        elif isinstance(category, str):
            # String format - try to map to known categories
            mapped_category = _map_string_to_category(category, taxonomy_map)
            if mapped_category:
                standardized.append(mapped_category)
            else:
                logger.warning(f"Could not map string category: {category}")
                # Create a generic category object
                standardized.append(
                    {
                        "id": hash(category) % 10000,  # Generate a pseudo-ID
                        "name": category,
                        "tier_1": category,
                        "tier_2": None,
                        "tier_3": None,
                        "tier_4": None,
                        "parent_id": None,
                    }
                )

    return standardized


def _map_string_to_category(category_string: str, taxonomy_map: Dict[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Map a string category to a standardized category object"""
    # Try exact name match first
    for cat_id, cat_data in taxonomy_map.items():
        if cat_data["name"].lower() == category_string.lower():
            return cat_data

    # Try partial matches in tier names
    category_lower = category_string.lower()
    for cat_id, cat_data in taxonomy_map.items():
        if cat_data["tier_1"] and category_lower in cat_data["tier_1"].lower():
            return cat_data

    return None


def get_user_from_token(headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Extract user information from Cognito JWT token
    """
    auth_header = headers.get("Authorization") or headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    try:
        # For demo purposes, we'll simulate user validation
        # In production, you would validate the JWT token with Cognito
        token = auth_header.replace("Bearer ", "")

        # Mock user info for demo
        return {"user_id": "demo_user_001", "email": "publisher@example.com", "username": "demo_publisher"}
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}")
        return None


@validate_response("/content", "POST")
def handle_create_content(user_info: Dict[str, Any], request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle content creation — async via the GEO enrichment pipeline.

    Drops the payload onto s3://{bucket}/ingest/ and returns 202 + content_id.
    The Step Functions pipeline (S3 event → extract → classify+enrich → embed →
    index → persist) is the single enrichment path; the old synchronous inline
    write produced records without GEO fields.
    """
    logger.info("=== HANDLE_CREATE_CONTENT START ===")
    logger.info(f"User: {user_info.get('username')}, Content type: {request_data.get('content_type')}")

    try:
        # Validate required fields
        required_fields = ["title", "content_type"]
        for field in required_fields:
            if field not in request_data:
                return create_error_response(400, f"Missing required field: {field}")

        content_id = f"cnt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        ingest_payload = {
            "title": request_data["title"],
            "content_type": request_data["content_type"],
            "content_text": request_data.get("content_text", ""),
            "content_markdown": request_data.get("content_markdown", ""),
            "author": request_data.get("author", user_info["username"]),
            "publication_date": request_data.get("publication_date", now),
            "canonical_url": request_data.get("canonical_url"),
            "tags": request_data.get("tags", []),
            "custom_metadata": request_data.get("custom_metadata", {}),
            "created_by": user_info["user_id"],
        }

        # S3 object metadata content-id → the pipeline reuses it, keeping
        # re-processing idempotent against this exact record
        ingest_key = f"ingest/{content_id}.json"
        s3.put_object(
            Bucket=content_bucket,
            Key=ingest_key,
            Body=json.dumps(ingest_payload, default=str),
            ContentType="application/json",
            Metadata={"content-id": content_id},
        )
        logger.info(f"Queued content {content_id} at s3://{content_bucket}/{ingest_key}")

        response = {
            "content_id": content_id,
            "status": "queued",
            "processing_status": "queued",
            "created_at": now,
            "message": "Content accepted and queued for GEO enrichment",
        }

        logger.info(f"=== HANDLE_CREATE_CONTENT END === Queued content: {content_id}")
        return create_success_response(response, 202)

    except Exception as e:
        logger.error(f"=== HANDLE_CREATE_CONTENT ERROR === {str(e)}")
        return create_error_response(500, "Failed to create content")


@validate_response("/content", "GET")
def handle_list_content(user_info: Dict[str, Any], query_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle content listing with filtering and pagination
    Uses feature flags to choose between real DynamoDB data and mock data
    """
    logger.info("=== HANDLE_LIST_CONTENT START ===")
    logger.info(f"User: {user_info.get('username')}, Filters: {query_params}")

    try:
        # Extract query parameters
        status_filter = query_params.get("status")
        content_type_filter = query_params.get("content_type")
        # Validate limit: a non-integer must be a 400 client error, not a 500 (wave0 Req 14).
        raw_limit = query_params.get("limit", 20)
        try:
            limit = min(int(raw_limit), 100)
        except (TypeError, ValueError):
            return create_error_response(400, f"Invalid 'limit' parameter: {raw_limit!r} is not an integer")
        page_token = query_params.get("page_token")  # For pagination

        # Real-data-only: read directly from DynamoDB (wave0 Req 9 — mock fallback removed).
        # Empty source -> empty items; source failure -> 500 via the surrounding except.
        response = _get_real_content_list(status_filter, content_type_filter, limit, user_info, page_token)

        logger.info(f"=== HANDLE_LIST_CONTENT END === Retrieved {len(response.get('items', []))} items")
        return create_success_response(response)

    except Exception as e:
        logger.error(f"=== HANDLE_LIST_CONTENT ERROR === {str(e)}")
        return create_error_response(500, "Failed to list content")


def _get_real_content_list(
    status_filter: str,
    content_type_filter: str,
    limit: int,
    user_info: Dict[str, Any],
    last_evaluated_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get real content list from DynamoDB with proper pagination and GSI usage
    """
    try:
        # Determine the best query strategy based on filters
        use_gsi = False
        gsi_name = None

        # Check if we can use GSI for more efficient queries
        if status_filter and not content_type_filter:
            # Use status GSI if it exists
            use_gsi = True
            gsi_name = "status-index"
        elif content_type_filter and not status_filter:
            # Use content_type GSI if it exists
            use_gsi = True
            gsi_name = "content-type-index"

        if use_gsi and gsi_name:
            # Use GSI query for better performance
            query_params = {
                "IndexName": gsi_name,
                "Limit": limit,
                "ScanIndexForward": False,  # Sort by sort key descending (newest first)
            }

            # Build key condition expression (single-key table, ADR-004 — no
            # version rows to filter out)
            if status_filter:
                query_params["KeyConditionExpression"] = "#status = :status"
                query_params["ExpressionAttributeNames"] = {"#status": "status"}
                query_params["ExpressionAttributeValues"] = {":status": status_filter}
            elif content_type_filter:
                query_params["KeyConditionExpression"] = "content_type = :content_type"
                query_params["ExpressionAttributeValues"] = {":content_type": content_type_filter}

            # Add pagination token if provided
            if last_evaluated_key:
                try:
                    import json

                    query_params["ExclusiveStartKey"] = json.loads(last_evaluated_key)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"Invalid pagination token: {last_evaluated_key}")

            # Execute query
            try:
                response = content_table.query(**query_params)
            except Exception as gsi_error:
                logger.warning(f"GSI query failed, falling back to scan: {str(gsi_error)}")
                use_gsi = False

        if not use_gsi:
            # Fall back to scan operation
            scan_params = {"Limit": limit}

            # Add filter expressions (single-key table, ADR-004 — no version
            # rows to filter out)
            filter_expressions = []
            expression_attribute_values = {}
            expression_attribute_names = {}

            if status_filter:
                filter_expressions.append("#status = :status")
                expression_attribute_names["#status"] = "status"
                expression_attribute_values[":status"] = status_filter

            if content_type_filter:
                filter_expressions.append("content_type = :content_type")
                expression_attribute_values[":content_type"] = content_type_filter

            if filter_expressions:
                scan_params["FilterExpression"] = " AND ".join(filter_expressions)
                scan_params["ExpressionAttributeValues"] = expression_attribute_values
                if expression_attribute_names:
                    scan_params["ExpressionAttributeNames"] = expression_attribute_names

            # Add pagination token if provided
            if last_evaluated_key:
                try:
                    import json

                    scan_params["ExclusiveStartKey"] = json.loads(last_evaluated_key)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"Invalid pagination token: {last_evaluated_key}")

            # Perform DynamoDB scan
            response = content_table.scan(**scan_params)

        items = response.get("Items", [])
        last_evaluated_key_response = response.get("LastEvaluatedKey")

        # The default list leaves comparison records out (R15.1.5); status=comparison lists them.
        excluded = 0
        if not status_filter:
            kept = [item for item in items if item.get("status") != COMPARISON_STATUS]
            excluded = len(items) - len(kept)
            items = kept

        # Convert DynamoDB items to API format
        formatted_items = []
        for item in items:
            formatted_item = {
                "content_id": item.get("content_id", ""),
                "title": item.get("title", ""),
                "content_type": item.get("content_type", ""),
                "markdown_schema_version": item.get("markdown_schema_version", "legacy"),
                "edge_eligible": bool(item.get("edge_eligible", False)),
                "status": item.get("status", "unknown"),
                "author": item.get("author", ""),
                "publication_date": _publication_date(item),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "iab_categories": standardize_iab_categories(item.get("iab_categories", [])),
                "tags": item.get("tags", []),
                "processing_metrics": item.get(
                    "processing_metrics", {"processing_time_ms": 0, "tokens_processed": 0, "images_processed": 0}
                ),
                # Add additional fields for better UI support
                "summary": item.get("summary", ""),
                "file_size": item.get("file_size", 0),
                "access_count": item.get("access_count", 0),
                "revenue_generated": item.get("revenue_generated", 0.0),
            }
            for field in (
                "canonical_url",
                "url_hash",
                "edge_ineligible_reason",
                "md_s3_key",
            ):
                if item.get(field) is not None:
                    formatted_item[field] = item[field]
            formatted_items.append(formatted_item)

        # Build pagination info
        pagination_info = {
            "items_per_page": limit,
            "has_next": last_evaluated_key_response is not None,
            "has_previous": last_evaluated_key is not None,
        }

        # Add next page token if there are more items
        if last_evaluated_key_response:
            import json

            pagination_info["next_page_token"] = json.dumps(last_evaluated_key_response, default=str)

        # For scan operations, we can't easily get total count without a full scan
        # So we provide an estimated count based on current results
        if use_gsi:
            pagination_info["total_items"] = response.get("Count", len(formatted_items) + excluded) - excluded
            pagination_info["query_method"] = "gsi_query"
        else:
            pagination_info["total_items"] = "unknown"  # Would require full scan to determine
            pagination_info["query_method"] = "scan"

        logger.info(f"Retrieved {len(formatted_items)} content items using {pagination_info['query_method']}")

        return {"items": formatted_items, "pagination": pagination_info}

    except Exception as e:
        logger.error(f"Error getting real content list: {str(e)}")
        raise


@validate_response("/content/{content_id}", "GET")
def handle_get_content(user_info: Dict[str, Any], content_id: str) -> Dict[str, Any]:
    """
    Handle getting detailed content information
    Uses feature flags to choose between real DynamoDB data and mock data
    """
    try:
        if not content_id:
            return create_error_response(400, "Content ID is required")

        # Real-data-only: read directly from DynamoDB (wave0 Req 9 — mock fallback removed).
        response = _get_real_content_details(content_id)
        result = create_success_response(response)
        if response.get("content_text") and not _fits_lambda_response(result):
            # Too large to return with its body: keep the details, as for an unreadable body.
            logger.error(f"Details body unavailable: content_id={content_id} reason=too_large")
            response["content_text"] = ""
            result = create_success_response(response)
        return result

    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            return create_error_response(404, f"Content {content_id} not found")
        logger.error(f"Error in handle_get_content: {msg}")
        return create_error_response(500, "Failed to get content details")


def _get_real_content_details(content_id: str) -> Dict[str, Any]:
    """
    Get real content details from DynamoDB
    """
    try:
        # Query DynamoDB for the specific content item (single-key table, ADR-004)
        response = content_table.get_item(Key={"content_id": content_id})

        if "Item" not in response:
            raise Exception(f"Content {content_id} not found")

        item = response["Item"]

        # Convert DynamoDB item to API format
        detailed_content = {
            "content_id": item.get("content_id", ""),
            "title": item.get("title", ""),
            "content_type": item.get("content_type", ""),
            "markdown_schema_version": item.get("markdown_schema_version", "legacy"),
            "edge_eligible": bool(item.get("edge_eligible", False)),
            "status": item.get("status", "unknown"),
            "content_text": _details_markdown(item),
            "content_html": item.get("content_html", ""),
            "summary": item.get("summary", ""),
            "author": item.get("author", ""),
            "publisher": item.get("publisher") or "",
            "publication_date": _publication_date(item),
            "modified_date": item.get("modified_date") or "",
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
            "tags": item.get("tags", []),
            "iab_categories": standardize_iab_categories(item.get("iab_categories", [])),
            "custom_metadata": item.get("custom_metadata", {}),
            "processing_metrics": item.get("processing_metrics", {}),
            "embedding_info": item.get("embedding_info", {}),
            "images": item.get("images", []),
            "attachments": item.get("attachments", []),
        }
        for field in (
            "canonical_url",
            "url_hash",
            "edge_ineligible_reason",
            "md_s3_key",
        ):
            if item.get(field) is not None:
                detailed_content[field] = item[field]

        # Add AI analysis if available
        if "ai_analysis" in item:
            detailed_content["ai_analysis"] = item["ai_analysis"]

        return detailed_content

    except Exception as e:
        logger.error(f"Error getting real content details for {content_id}: {str(e)}")
        raise


@validate_response("/content/{content_id}", "PUT")
def handle_update_content(user_info: Dict[str, Any], content_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle content updates using feature flag system
    """
    try:
        if not content_id:
            return create_error_response(400, "Content ID is required")

        # Real-data-only: update directly in DynamoDB (wave0 Req 9 — mock fallback removed)
        response = _update_real_content(content_id, request_data, user_info)
        return create_success_response(response)

    except ContentBeingDeleted:
        return create_error_response(409, f"Content {content_id} is being deleted")
    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            return create_error_response(404, f"Content {content_id} not found")
        logger.error(f"Error in handle_update_content: {msg}")
        return create_error_response(500, "Failed to update content")


class ContentBeingDeleted(Exception):
    """The record is ``deleting``: a delete was accepted and the deindexer is completing it."""


def _update_real_content(content_id: str, request_data: Dict[str, Any], user_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update real content in DynamoDB
    """
    try:
        # First check if content exists
        response = content_table.get_item(Key={"content_id": content_id})
        if "Item" not in response:
            raise Exception(f"Content {content_id} not found")
        if response["Item"].get("status") == DELETING_STATUS:
            raise ContentBeingDeleted(content_id)

        # Build update expression
        update_expression = "SET updated_at = :updated_at, updated_by = :updated_by"
        expression_attribute_values = {
            ":updated_at": datetime.now(timezone.utc).isoformat(),
            ":updated_by": user_info["user_id"],
        }

        # Add fields to update
        if "title" in request_data:
            update_expression += ", title = :title"
            expression_attribute_values[":title"] = request_data["title"]

        if "summary" in request_data:
            update_expression += ", summary = :summary"
            expression_attribute_values[":summary"] = request_data["summary"]

        if "tags" in request_data:
            update_expression += ", tags = :tags"
            expression_attribute_values[":tags"] = request_data["tags"]

        if "iab_categories" in request_data:
            update_expression += ", iab_categories = :iab_categories"
            expression_attribute_values[":iab_categories"] = standardize_iab_categories(request_data["iab_categories"])

        if "custom_metadata" in request_data:
            update_expression += ", custom_metadata = :custom_metadata"
            expression_attribute_values[":custom_metadata"] = request_data["custom_metadata"]

        # Update the item. The condition closes the race with a delete accepted after the read
        # above: an update must never write to a record the deindexer is removing.
        expression_attribute_values[":deleting"] = DELETING_STATUS
        try:
            update_response = content_table.update_item(
                Key={"content_id": content_id},
                UpdateExpression=update_expression,
                ConditionExpression="attribute_exists(content_id) AND (attribute_not_exists(#s) OR #s <> :deleting)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues=expression_attribute_values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            # Removed or marked deleting since the read above.
            if "Item" not in content_table.get_item(Key={"content_id": content_id}, ConsistentRead=True):
                raise Exception(f"Content {content_id} not found") from exc
            raise ContentBeingDeleted(content_id) from exc

        updated_item = update_response["Attributes"]

        # Create reprocessing job if requested
        reprocessing_job_id = None
        if request_data.get("reprocess", False):
            reprocessing_job_id = _create_reprocessing_job(content_id, user_info)

        # Format response
        updated_content = {
            "content_id": content_id,
            "title": updated_item.get("title", ""),
            "summary": updated_item.get("summary", ""),
            "tags": updated_item.get("tags", []),
            "iab_categories": standardize_iab_categories(updated_item.get("iab_categories", [])),
            "updated_at": updated_item.get("updated_at", ""),
            "updated_by": updated_item.get("updated_by", ""),
        }

        if reprocessing_job_id:
            updated_content["reprocessing_job_id"] = reprocessing_job_id

        return updated_content

    except Exception as e:
        logger.error(f"Error updating real content {content_id}: {str(e)}")
        raise


def _create_reprocessing_job(content_id: str, user_info: Dict[str, Any]) -> str:
    """
    Create a reprocessing job (real or mock based on feature flags)
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_item = {
        "job_id": job_id,
        "content_id": content_id,
        "job_type": "content_reprocessing",
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user_info["user_id"],
    }

    # Real-data-only: persist the reprocessing job (wave0 Req 9 — mock creation removed)
    try:
        _queue_job_unless_deleting(job_item)
        logger.info(f"Created reprocessing job {job_id} in DynamoDB")
    except Exception as e:
        logger.error(f"Failed to create reprocessing job: {str(e)}")
        raise

    return job_id


def _queue_job_unless_deleting(job_item: Dict[str, Any]) -> None:
    """Write a job for a content record, then withdraw it if a delete was accepted meanwhile.

    The deindexer removes a record's jobs after the record is marked ``deleting``. A job written
    after that scan would be orphaned. The re-read closes the window: a delete accepted before it
    makes this function withdraw the job, and a delete accepted after it scans the job and
    removes it.
    """
    jobs_table.put_item(Item=job_item)
    item = content_table.get_item(Key={"content_id": job_item["content_id"]}, ConsistentRead=True).get("Item")
    if item is None or item.get("status") == DELETING_STATUS:
        jobs_table.delete_item(Key={"job_id": job_item["job_id"]})
        raise ContentBeingDeleted(job_item["content_id"])


@validate_response("/content/{content_id}", "DELETE")
def handle_delete_content(user_info: Dict[str, Any], content_id: str) -> Dict[str, Any]:
    """Accept a content delete: ``202`` once the record is ``deleting`` and the event is emitted.

    The content deindexer completes the delete asynchronously. A record that is already
    ``deleting`` is accepted again, which re-emits the event and retries the cleanup.
    """
    try:
        if not content_id:
            return create_error_response(400, "Content ID is required")

        try:
            _delete_real_content(content_id, user_info)
        except ContentNotFound:
            return create_error_response(404, f"Content {content_id} not found")
        except Exception as e:
            logger.error(f"Failed to delete content {content_id}: {e}")
            return create_error_response(500, "Failed to delete content")

        return create_success_response({"content_id": content_id, "status": DELETING_STATUS}, 202)

    except Exception as e:
        logger.error(f"Error in handle_delete_content: {str(e)}")
        return create_error_response(500, "Failed to delete content")


def soft_delete_content(content_id: str, actor: str) -> Dict[str, Any]:
    """Mark ``content_id`` as ``deleting`` and emit ``content.deleted`` for the deindexer.

    The one entry point for deleting content from this service. Returns the event detail and
    raises ``ContentNotFound`` for an unknown record.
    """
    return soft_delete(content_table, events, content_id, actor)


def _delete_real_content(content_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
    """Soft-delete the record. The deindexer removes its search document, objects, jobs and row."""
    detail = soft_delete_content(content_id, user_info["user_id"])
    logger.info(f"Content {content_id} delete accepted for user {user_info['user_id']}")
    return detail


@validate_response("/content/{content_id}/reprocess", "POST")
def handle_reprocess_content(
    user_info: Dict[str, Any], content_id: str, request_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle content reprocessing
    """
    try:
        if not content_id:
            return create_error_response(400, "Content ID is required")

        item = content_table.get_item(Key={"content_id": content_id}, ConsistentRead=True).get("Item")
        if not item:
            return create_error_response(404, f"Content {content_id} not found")
        if item.get("status") == DELETING_STATUS:
            return create_error_response(409, f"Content {content_id} is being deleted")

        operations = request_data.get("operations", ["embeddings", "categorization"])
        priority = request_data.get("priority", "normal")

        # Create reprocessing job
        job_id = f"reprocess_{uuid.uuid4().hex[:12]}"
        job_item = {
            "job_id": job_id,
            "content_id": content_id,
            "job_type": "content_reprocessing",
            "status": "queued",
            "operations": operations,
            "priority": priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": user_info["user_id"],
            "estimated_completion": datetime.now(timezone.utc).isoformat(),
        }

        try:
            _queue_job_unless_deleting(job_item)
        except ContentBeingDeleted:
            return create_error_response(409, f"Content {content_id} is being deleted")

        response = {
            "job_id": job_id,
            "status": "queued",
            "operations": operations,
            "estimated_completion": job_item["estimated_completion"],
        }

        return create_success_response(response, 202)

    except Exception as e:
        logger.error(f"Error in handle_reprocess_content: {str(e)}")
        return create_error_response(500, "Failed to queue reprocessing")


@validate_response("/content/bulk", "POST")
def handle_bulk_operations(user_info: Dict[str, Any], request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle bulk operations on multiple content items
    """
    try:
        operation = request_data.get("operation")
        content_ids = request_data.get("content_ids", [])

        if not operation or not content_ids:
            return create_error_response(400, "Operation and content_ids are required")

        # Create bulk operation job
        operation_id = f"bulk_{uuid.uuid4().hex[:12]}"
        job_item = {
            "job_id": operation_id,
            "job_type": "bulk_operation",
            "operation": operation,
            "content_ids": content_ids,
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": user_info["user_id"],
            "total_items": len(content_ids),
            "processed_items": 0,
            "failed_items": 0,
        }

        jobs_table.put_item(Item=job_item)

        response = {
            "operation_id": operation_id,
            "status": "queued",
            "total_items": len(content_ids),
            "estimated_completion": datetime.now(timezone.utc).isoformat(),
        }

        return create_success_response(response, 202)

    except Exception as e:
        logger.error(f"Error in handle_bulk_operations: {str(e)}")
        return create_error_response(500, "Failed to queue bulk operation")


def create_success_response(data: Any, status_code: int = 200) -> Dict[str, Any]:
    """
    Create a successful API response
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(data, default=str),
    }


def create_error_response(
    status_code: int, message: str, details: str = None, error_code: str = None
) -> Dict[str, Any]:
    """
    Create standardized error response matching OpenAPI specification

    Args:
        status_code: HTTP status code
        message: Human-readable error message
        details: Additional error details (optional)
        error_code: Machine-readable error code (optional)

    Returns:
        Standardized error response with consistent field names
    """
    # Generate error code if not provided
    if not error_code:
        if status_code == 400:
            error_code = "bad_request"
        elif status_code == 401:
            error_code = "unauthorized"
        elif status_code == 403:
            error_code = "forbidden"
        elif status_code == 404:
            error_code = "not_found"
        elif status_code == 409:
            error_code = "conflict"
        elif status_code == 429:
            error_code = "rate_limit_exceeded"
        elif status_code >= 500:
            error_code = "internal_server_error"
        else:
            error_code = "request_error"

    # Standardized error response format matching OpenAPI spec
    error_data = {
        "error": error_code,
        "message": message,
        "code": error_code,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": f"req_{uuid.uuid4().hex[:8]}",
    }

    if details:
        error_data["details"] = details

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Custom Allow-Headers / Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(error_data),
    }


class ContentService:
    """Single home for the content domain's request handling (Requirement 1.1 / 7.1 / 7.4).

    The content domain's business logic and DynamoDB/S3 access now live in this module
    directly (module-level helpers above); this service no longer delegates back to a
    root handler module. The public entry is :meth:`handle_request`, which accepts the raw
    API Gateway proxy event and returns an API Gateway proxy response object. Path
    disambiguation across ``/content``, ``/content/{id}``, ``/content/bulk`` (and
    ``/content/{id}/reprocess``) plus Cognito auth are performed here, exactly as the
    monolith did (Requirement 2).
    """

    def handle_request(self, event: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
        """
        Content Management Lambda handler for publisher operations
        """
        request_id = getattr(context, "aws_request_id", "unknown")
        logger.info(f"=== CONTENT_MANAGEMENT_LAMBDA START === Request ID: {request_id}")
        logger.info(f"Event: {json.dumps(event, default=str)}")

        try:
            # Extract request information
            http_method = event.get("httpMethod", "")
            path = event.get("path", "")
            headers = event.get("headers", {})
            body = event.get("body", "")
            path_parameters = event.get("pathParameters") or {}
            query_params = event.get("queryStringParameters") or {}

            # Verify Cognito authentication
            user_info = get_user_from_token(headers)
            if not user_info:
                return create_error_response(401, "Invalid or missing authentication token")

            # Parse request body if present
            request_data = {}
            if body:
                try:
                    request_data = json.loads(body)
                except json.JSONDecodeError:
                    return create_error_response(400, "Invalid JSON in request body")

            # content_id from a dedicated {content_id} route param, or parsed from
            # the path when invoked through the UI backend's {proxy+} integration
            def _content_id_from_path() -> Optional[str]:
                explicit = path_parameters.get("content_id")
                if explicit:
                    return explicit
                parts = [p for p in path.split("/") if p]
                return parts[1] if len(parts) >= 2 and parts[0] == "content" else None

            # Route to appropriate handler based on path and method
            if path == "/content" and http_method == "POST":
                return handle_create_content(user_info, request_data)
            elif path == "/content" and http_method == "GET":
                if _is_single_document_read(query_params):
                    return _internal_content_read(user_info, query_params, headers)
                return handle_list_content(user_info, query_params)
            elif path.endswith("/reprocess") and http_method == "POST":
                return handle_reprocess_content(user_info, _content_id_from_path(), request_data)
            elif path.startswith("/content/") and http_method == "GET":
                return handle_get_content(user_info, _content_id_from_path())
            elif path.startswith("/content/") and http_method == "PUT":
                return handle_update_content(user_info, _content_id_from_path(), request_data)
            elif path.startswith("/content/") and http_method == "DELETE":
                return handle_delete_content(user_info, _content_id_from_path())
            elif path == "/content/bulk" and http_method == "POST":
                return handle_bulk_operations(user_info, request_data)
            else:
                return create_error_response(404, "Endpoint not found")

        except Exception as e:
            logger.error(f"=== CONTENT_MANAGEMENT_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
            return create_error_response(500, "Internal server error")


# A content_id as discovery and ingest produce it (``cnt_<hex>``, seeded and test identifiers),
# bounded so a malformed value is rejected before any table call.
_CONTENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# The one record status discovery exposes (SearchService filters on it). Both read modes expose
# exactly the same set, so a guessed identifier or URL cannot read a draft or a failed record.
_READABLE_STATUS = "published"
_NOT_FOUND_BY_ID = "Content not found for the specified content_id"
_CONTENT_NOT_FOUND = "content_not_found"


def _content_not_found_response(message: str) -> Dict[str, Any]:
    """The single-document read 404, carrying the registered ``content_not_found`` code.

    The existing body fields (``error: "not_found"``, ``message``, ``code``) are kept so current
    clients are unaffected; ``error_code`` is added for partners that branch on it.
    """
    response = create_error_response(404, message)
    body = json.loads(response["body"])
    body["error_code"] = _CONTENT_NOT_FOUND
    response["body"] = json.dumps(body)
    return response


def _is_single_document_read(query_params: Dict[str, Any]) -> bool:
    """Whether ``GET /content`` names a document (``url`` or ``content_id``) rather than a list."""
    return query_params.get("url") is not None or query_params.get("content_id") is not None


def _internal_content_read(
    user_info: Dict[str, Any], query_params: Dict[str, Any], headers: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """A Cognito single-document read on the internal plane.

    It is the partner read, unbilled: a publisher reading their own content is not a licensed
    access. ``If-None-Match`` is lifted off the request header as the partner handler does. The
    response carries the CORS headers and exposes ``ETag``, because the caller here is a browser.
    """
    headers = headers or {}
    if_none_match = headers.get("If-None-Match") or headers.get("if-none-match")
    params = {**query_params, "if_none_match": if_none_match} if if_none_match else dict(query_params)
    response = handle_content_read(user_info, params, billable=False)
    response["headers"] = {
        **cors_headers(),
        "Access-Control-Expose-Headers": "ETag",
        **(response.get("headers") or {}),
    }
    return response


def handle_content_read(
    user_info: Dict[str, Any], query_params: Dict[str, Any], *, billable: bool = True
) -> Dict[str, Any]:
    """Handle a single-document read: ``GET /content?url=`` or ``GET /content?content_id=``.

    Exactly one selector must be present. ``url`` keeps the canonical-URL read unchanged;
    ``content_id`` is the read reference for discovery results that have no canonical URL. Both
    serve through ``_serve_read``, so the representation, ``ETag``/``304`` and billing are the same.

    ``billable`` is the caller's decision. The partner plane leaves it on. The internal plane turns
    it off, because a publisher reading their own content is not a licensed access.
    """
    has_url = query_params.get("url") is not None
    has_content_id = query_params.get("content_id") is not None
    if has_url == has_content_id:
        return partner_error_response(
            400, "Supply exactly one of url or content_id", error_code="invalid_read_selector"
        )
    if has_url:
        return handle_get_content_by_url(user_info, query_params, billable=billable)

    content_id = str(query_params.get("content_id"))
    if not _CONTENT_ID_PATTERN.fullmatch(content_id):
        return partner_error_response(400, "content_id is malformed", error_code="invalid_read_selector")
    try:
        item = content_table.get_item(Key={"content_id": content_id}).get("Item")
        # One 404 for unknown, unpublished and failed records alike: the response must not tell a
        # caller that an identifier exists.
        if not item or item.get("status") != _READABLE_STATUS:
            return _content_not_found_response(_NOT_FOUND_BY_ID)
        return _serve_read(item, query_params, user_info, billable=billable)
    except Exception as e:  # noqa: BLE001 - one sanitized 500 for any read failure
        logger.error(f"Error in handle_content_read: {type(e).__name__}")
        return create_error_response(500, "Failed to retrieve content")


def handle_get_content_by_url(
    user_info: Dict[str, Any], query_params: Dict[str, Any], *, billable: bool = True
) -> Dict[str, Any]:
    """
    Handle URL-addressable content retrieval (Issue #14)
    GET /content?url=<canonical_url>
    
    Returns content by canonical URL with ETag support for conditional requests.
    """
    import hashlib

    from shared.utils.edge_address import edge_address_from_canonical_url
    
    try:
        url = query_params.get("url")
        if not url:
            return create_error_response(400, "Missing required parameter: url")
        
        # Normalize URL and compute hash (reusing Issue #12 logic)
        edge_address = edge_address_from_canonical_url(url)
        url_hash = edge_address.url_hash
        
        if not url_hash:
            return create_error_response(400, "Invalid or unsupported URL format")
        
        # Query by url_hash GSI. An unpublished record counts as a miss, so it gets the same 404
        # as an unknown URL and the trailing-slash retry still runs.
        items = _readable(_query_content_by_url_hash(url_hash))

        if not items:
            # REQ-1.5 names the trailing slash among the spellings a citation may use, but the
            # slash is NOT normalized away: it is load-bearing in the edge address, where `/a/b/`
            # resolves to `content/<host>/a/b/index.md` and `/a/b` to `content/<host>/a/b.md`.
            # Collapsing the two in the normalizer would change the Shape 1 object key and break
            # native edge delivery, so the citation tolerance lives HERE instead — on the read
            # path only, and only after the exact spelling has already missed. One extra GSI query
            # on a miss, never on a hit.
            alternate = _trailing_slash_variant(url)
            if alternate:
                alternate_hash = edge_address_from_canonical_url(alternate).url_hash
                if alternate_hash and alternate_hash != url_hash:
                    items = _readable(_query_content_by_url_hash(alternate_hash))

        if not items:
            # Check for redirect mapping (if content was moved to a new URL)
            # Look up redirect in a hypothetical redirects table or field
            redirect_url = _check_for_redirect(url_hash)
            
            if redirect_url:
                # 404 with redirectUrl for moved content (RFC 9457 Problem Details)
                return {
                    "statusCode": 404,
                    "headers": {
                        "Content-Type": "application/problem+json"
                    },
                    "body": json.dumps({
                        "type": "https://demopublisher.com/errors/content-moved",
                        "title": "Content Not Found at This URL",
                        "status": 404,
                        "detail": "The requested content has been moved to a different URL.",
                        "redirectUrl": redirect_url,
                        "instance": f"/content?url={url}"
                    })
                }
            
            # No content and no redirect - genuine 404
            return _content_not_found_response("Content not found for the specified URL")
        
        item = items[0]  # url_hash should be unique
        return _serve_read(item, query_params, user_info, billable=billable)

    except Exception as e:
        logger.error(f"Error in handle_get_content_by_url: {str(e)}")
        return create_error_response(500, "Failed to retrieve content by URL")


def _serve_read(
    item: Dict[str, Any], query_params: Dict[str, Any], user_info: Dict[str, Any], *, billable: bool = True
) -> Dict[str, Any]:
    """Serve one resolved document: ETag, the ``304`` early return, the body, the one usage event.

    Both read modes (``?url=`` and ``?content_id=``) end here, so the ``304`` billing exemption is
    a single control-flow guarantee rather than two copies that could drift. ``billable`` is off
    only for the internal plane's own reads; a ``304`` is never billed either way.
    """
    # For a record that names stored Markdown, the response ETag covers the object's own S3 ETag as
    # well as the record. The object is opened to learn that ETag, except when a conditional request
    # matches the record's md_etag (below). Only headers arrive at the open; the body is downloaded
    # on the 200 path alone.
    content_id = str(item.get("content_id", ""))
    # Check If-None-Match header for conditional request (REQ-2.2)
    if_none_match = query_params.get("if_none_match", query_params.get("If-None-Match", "")).strip('"')

    # Fast path: the record carries the stored object's S3 ETag (md_etag), so a matching conditional
    # request is answered from the record alone, with no S3 request. While a document is re-ingested,
    # this keeps answering for the previous version until the pipeline saves the new record (the
    # accepted window; content changes rarely). Any other request takes the S3 path.
    recorded_etag = item.get("md_etag") if item.get("md_s3_key") else None
    if if_none_match and recorded_etag:
        etag = _compute_etag(item, {"ETag": recorded_etag})
        if if_none_match == etag:
            return _not_modified(etag)

    try:
        stored = _open_stored_markdown(item)
    except _BodyUnavailable as exc:
        return _body_unavailable_response(exc)

    etag = _compute_etag(item, stored)

    if if_none_match and if_none_match == etag:
        if stored is not None:
            _close_quietly(stored["Body"])
        return _not_modified(etag)
    
    # The body is read only on the 200 path, after the 304 return (which downloads no body) and
    # before the usage event (a read that fails is never billed).
    try:
        markdown = item.get("content_text", "") if stored is None else _read_stored_markdown(stored, content_id)
    except _BodyUnavailable as exc:
        return _body_unavailable_response(exc)

    # Build response (200 OK)
    content_response = {
        "content_id": item.get("content_id", ""),
        "markdown": markdown,
        "metadata": {
            "title": item.get("title", ""),
            "author": item.get("author", ""),
            "publisher": item.get("publisher") or "",
            "pubdate": _publication_date(item),
            "modified_date": item.get("modified_date") or "",
            "update": item.get("updated_at", ""),
            "wordcount": int(item.get("word_count", 0)) if item.get("word_count") else 0,
            "language": item.get("language", "en"),
            "sourcetype": _map_content_type_to_sourcetype(item.get("content_type", "article"))
        },
        "canonical_url": item.get("canonical_url", "")
    }

    # Serialized before the usage event: a response too large for Lambda to return would fail
    # after the emit, billing a read the caller never received.
    response = {
        "statusCode": 200,
        "headers": {
            "ETag": f'"{etag}"',
            "Content-Type": "application/json",
            "Cache-Control": "private, must-revalidate"
        },
        "body": json.dumps(content_response),
    }
    if not _fits_lambda_response(response):
        return _body_unavailable_response(_BodyUnavailable(content_id, "too_large"))

    # Record usage event (billable — the #3 choke point, GitLab #3 task 10.1, Req 8.1).
    # NOTE: Only 200 OK responses reach this point due to early return for 304 above (REQ-2.3).
    # This ensures 304 Not Modified responses are never billed. That exemption is a CONTROL-FLOW
    # guarantee (the early return above), deliberately NOT a conditional inside the emitter or
    # inside this call — so it cannot be defeated by a later edit to services/usage_emitter.py
    # (design.md B4, Req 8.7). Do not move this call above the 304 return.
    #
    # Placed AFTER `content_response` is assembled so no emit work sits between the data read
    # and the response (design.md B3). `emit_usage_event` never raises, performs no lookup of
    # its own, and adds no read to this hot path: every value below is already in hand.
    if billable:
        emit_usage_event(
            partner_id=user_info.get("partner_id", "") if user_info else "",
            content_id=item.get("content_id", ""),
            # Denormalized at emit (Req 8.2) — values-at-time-of-access.
            content_type=USAGE_CONTENT_TYPE_URL_RETRIEVAL,
            # Read off the content record (ContentMetadata.license_id), already loaded above. NOT a
            # request-time join back to cm-licenses — that would violate the read-path
            # denormalization rule.
            license_id=item.get("license_id", ""),
            # Pricing is not resolved on this path: this is Path B (entitlement/subscription, paid
            # out-of-band), so the per-access charge is genuinely 0.0 today. A price is NOT invented
            # here and no pricing lookup is added. Path C's x402 per-access charge (#25/#7) is what
            # will populate this, at the call site that knows the charge.
            price_charged=0.0,
            currency=DEFAULT_CURRENCY,
            declared_use=None,  # not a CoMP request; no function code was declared
            source=SOURCE_OBSERVED,  # server-observed, never self-reported
            # request_id is deliberately omitted: this function's scope has only `user_info` and
            # `query_params` — neither the API Gateway event nor the Lambda context reaches here, so
            # there is no real request id to pass. The emitter treats it as optional and the sink
            # generates its own uniqueness token; fabricating one here would be a fake trace id.
        )

    return response


def _query_content_by_url_hash(url_hash: str) -> List[Dict[str, Any]]:
    """Resolve content by its canonical-URL hash through the ``url_hash-index`` GSI."""
    response = content_table.query(
        IndexName="url_hash-index",
        KeyConditionExpression="url_hash = :hash",
        ExpressionAttributeValues={":hash": url_hash},
    )
    return response.get("Items", [])


def _readable(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only the records a read may serve: ``status == "published"``, as for discovery."""
    return [item for item in items if item.get("status") == _READABLE_STATUS]


def _trailing_slash_variant(url: str) -> Optional[str]:
    """Return the same URL with its trailing slash toggled, or ``None`` when there is none to toggle.

    Only the PATH's trailing slash is toggled, and only for a path that has a segment: a root
    ``https://host/`` has no slash-free spelling (``https://host`` normalizes back to
    ``https://host/``), so there is nothing to try and this returns ``None`` rather than looping
    on the same hash. Any query or fragment is dropped, matching the normalizer.
    """
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    path = parsed.path or "/"
    if path in ("", "/"):
        return None
    toggled = path[:-1] if path.endswith("/") else f"{path}/"
    if toggled in ("", "/"):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, toggled, "", ""))


class _BodyUnavailable(Exception):
    """The record names a stored Markdown object that could not be read."""

    def __init__(self, content_id: str, reason: str) -> None:
        super().__init__(f"Markdown body unavailable for {content_id} ({reason})")
        self.content_id = content_id
        self.reason = reason


# Lambda returns at most 6 MB (6,291,456 bytes) synchronously; this leaves room for headers a
# caller adds after the response is built.
_MAX_READ_RESPONSE_BYTES = 6_000_000


def _fits_lambda_response(response: Dict[str, Any]) -> bool:
    """Whether a proxy response, serialized as the Lambda runtime will, fits the response limit.

    The runtime JSON-encodes the whole response, which escapes the already-encoded body a second
    time (``\\uXXXX`` becomes ``\\\\uXXXX``), so the body's own size understates what is returned.
    """
    return len(json.dumps(response, default=str).encode("utf-8")) <= _MAX_READ_RESPONSE_BYTES


def _publication_date(item: Dict[str, Any]) -> str:
    """The record's publication date: ``publication_date`` (direct writes) or ``publish_date`` (pipeline)."""
    return str(item.get("publication_date") or item.get("publish_date") or "")


def _storage_error_code(exc: Exception) -> str:
    error = getattr(exc, "response", None)
    code = error.get("Error", {}).get("Code") if isinstance(error, dict) else None
    return str(code or type(exc).__name__)


def _close_quietly(body: Any) -> None:
    try:
        body.close()
    except Exception:  # noqa: BLE001 - closing an unread stream must never fail a response
        pass


def _open_stored_markdown(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Open the stored Markdown object a record names, or return ``None`` when it names none.

    The GEO pipeline stores the body only in S3, at ``md_s3_key``: the same bytes native edge
    delivery serves. Records written directly carry ``content_text`` and no key. The returned
    ``GetObject`` response has its body stream unread, so a caller can answer a conditional
    request from the headers alone.
    """
    key = item.get("md_s3_key")
    if not key:
        return None
    try:
        return s3.get_object(Bucket=content_bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - any storage failure is one unavailable body
        raise _BodyUnavailable(str(item.get("content_id", "")), _storage_error_code(exc)) from exc


def _read_stored_markdown(stored: Dict[str, Any], content_id: str) -> str:
    """Download and decode an opened stored Markdown object.

    Raises ``_BodyUnavailable`` instead of returning an empty or partial body, and refuses an
    object too large to return before downloading it.
    """
    body = stored["Body"]
    try:
        if int(stored.get("ContentLength") or 0) > _MAX_READ_RESPONSE_BYTES:
            raise _BodyUnavailable(content_id, "too_large")
        return body.read().decode("utf-8")
    except _BodyUnavailable:
        raise
    except UnicodeDecodeError as exc:
        raise _BodyUnavailable(content_id, "invalid_utf8") from exc
    except Exception as exc:  # noqa: BLE001 - a failed download is one unavailable body
        raise _BodyUnavailable(content_id, _storage_error_code(exc)) from exc
    finally:
        _close_quietly(body)


def _read_markdown_body(item: Dict[str, Any]) -> str:
    """Return the document's Markdown: the stored object at ``md_s3_key``, else ``content_text``."""
    stored = _open_stored_markdown(item)
    if stored is None:
        return item.get("content_text", "")
    return _read_stored_markdown(stored, str(item.get("content_id", "")))


def _details_markdown(item: Dict[str, Any]) -> str:
    """``content_text`` for the internal details view, or ``""`` when the stored body can't be read.

    The details view is how a publisher diagnoses a record, so an unreadable body must not hide
    the record's status and metadata. The failure is logged instead.
    """
    try:
        return _read_markdown_body(item)
    except _BodyUnavailable as exc:
        logger.error(f"Details body unavailable: content_id={exc.content_id} reason={exc.reason}")
        return ""


def _body_unavailable_response(exc: _BodyUnavailable) -> Dict[str, Any]:
    """The sanitized, unbilled 500 for a read whose body can't be returned."""
    logger.error(f"Read body unavailable: content_id={exc.content_id} reason={exc.reason}")
    return create_error_response(500, "Failed to retrieve content")


def _not_modified(etag: str) -> Dict[str, Any]:
    """The ``304`` for a matching conditional read.

    BILLING EXEMPTION (REQ-2.3): both ``304`` returns in ``_serve_read`` come before the usage event,
    so a ``304`` is never billed. Only ``200`` responses (full content retrieval) are billable.
    """
    return {
        "statusCode": 304,
        "headers": {"ETag": f'"{etag}"', "Cache-Control": "private, must-revalidate"},
        "body": "",
    }


def _compute_etag(content: Dict[str, Any], stored: Optional[Dict[str, Any]] = None) -> str:
    """
    Compute ETag for content (timestamp-based for performance)

    When the body comes from a stored Markdown object, the object's own ETag is folded in: the
    pipeline replaces the object before it updates the record, so ``updated_at`` alone can't say
    which body is served. The object's ETag comes from the object itself, or from the record's
    ``md_etag`` (the fast path in ``_serve_read``).
    """
    import hashlib
    
    # Use updated_at as the version identifier
    updated_at = content.get("updated_at", content.get("created_at", ""))
    stored_etag = stored.get("ETag") if stored else None
    if stored_etag:
        return hashlib.sha256(f"{updated_at}|{stored_etag}".encode()).hexdigest()[:16]
    if updated_at:
        return hashlib.sha256(updated_at.encode()).hexdigest()[:16]
    
    # Fallback: hash content_id (stable but not reflecting updates)
    return hashlib.sha256(content["content_id"].encode()).hexdigest()[:16]


def _check_for_redirect(url_hash: str) -> Optional[str]:
    """
    Check if content at the given URL hash has been moved to a new location.
    
    Returns the new canonical URL if a redirect exists, None otherwise.
    
    Implementation note: This currently returns None (no redirect tracking yet).
    Future enhancement: Query a redirects table or check a redirect_to field
    on content records to support URL changes while maintaining discoverability.
    
    The redirect data structure should track:
    - old_url_hash -> new_canonical_url
    - timestamp of the move
    - reason (optional: "moved", "merged", "replaced")
    """
    # TODO: Implement redirect lookup when redirect tracking is added to the data model
    # Example future implementation:
    # try:
    #     response = content_table.query(
    #         IndexName="url_hash-index",
    #         KeyConditionExpression="url_hash = :hash",
    #         ExpressionAttributeValues={":hash": url_hash}
    #     )
    #     items = response.get("Items", [])
    #     if items and items[0].get("status") == "moved":
    #         return items[0].get("redirect_to_url")
    # except Exception as e:
    #     logger.warning(f"Error checking for redirect: {str(e)}")
    
    return None


def _map_content_type_to_sourcetype(content_type: str) -> int:
    """
    Map content_type to CoMP sourcetype integer
    0=human, 1=AI, 2=hybrid
    """
    # Default to human-authored (0) for now
    # This mapping can be enhanced based on content metadata
    return 0


