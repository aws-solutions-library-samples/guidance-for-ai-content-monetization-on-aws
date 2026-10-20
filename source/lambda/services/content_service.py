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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Import response validation middleware
from shared.utils.response_helpers import cors_headers
from shared.utils.response_validator import validate_response

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
cognito = boto3.client("cognito-idp")

content_table = dynamodb.Table(os.environ["CONTENT_TABLE_NAME"])
jobs_table = dynamodb.Table(os.environ["JOBS_TABLE_NAME"])
content_bucket = os.environ["CONTENT_BUCKET_NAME"]

# Import TaxonomyManager for database-driven taxonomy
from services.processing_jobs_service import create_processing_job
from services.taxonomy_manager import TaxonomyManager

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
                "publication_date": item.get("publication_date", ""),
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
            pagination_info["total_items"] = response.get("Count", len(formatted_items))
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
        return create_success_response(response)

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
            "content_text": item.get("content_text", ""),
            "content_html": item.get("content_html", ""),
            "summary": item.get("summary", ""),
            "author": item.get("author", ""),
            "publication_date": item.get("publication_date", ""),
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

    except Exception as e:
        msg = str(e)
        if "not found" in msg.lower():
            return create_error_response(404, f"Content {content_id} not found")
        logger.error(f"Error in handle_update_content: {msg}")
        return create_error_response(500, "Failed to update content")


def _update_real_content(content_id: str, request_data: Dict[str, Any], user_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update real content in DynamoDB
    """
    try:
        # First check if content exists
        response = content_table.get_item(Key={"content_id": content_id})
        if "Item" not in response:
            raise Exception(f"Content {content_id} not found")

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

        # Update the item
        update_response = content_table.update_item(
            Key={"content_id": content_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )

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
        jobs_table.put_item(Item=job_item)
        logger.info(f"Created reprocessing job {job_id} in DynamoDB")
    except Exception as e:
        logger.error(f"Failed to create reprocessing job: {str(e)}")
        raise

    return job_id


@validate_response("/content/{content_id}", "DELETE")
def handle_delete_content(user_info: Dict[str, Any], content_id: str) -> Dict[str, Any]:
    """
    Handle content deletion using feature flag system
    """
    try:
        if not content_id:
            return create_error_response(400, "Content ID is required")

        # Real-data-only: delete directly from DynamoDB/S3 (wave0 Req 9 — mock fallback removed)
        try:
            _delete_real_content(content_id, user_info)
        except Exception as e:
            msg = str(e)
            if "not found" in msg.lower():
                return create_error_response(404, f"Content {content_id} not found")
            logger.error(f"Failed to delete content: {msg}")
            return create_error_response(500, "Failed to delete content")

        # Return 204 No Content for successful deletion
        return {
            "statusCode": 204,
            "headers": {
                "Content-Type": "application/json",
                # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
                # Methods preserved after the spread (Requirement 2.6).
                **cors_headers(),
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            },
            "body": "",
        }

    except Exception as e:
        logger.error(f"Error in handle_delete_content: {str(e)}")
        return create_error_response(500, "Failed to delete content")


def _delete_real_content(content_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Delete real content from DynamoDB and S3
    """
    try:
        # First check if content exists
        response = content_table.get_item(Key={"content_id": content_id})
        if "Item" not in response:
            raise Exception(f"Content {content_id} not found")

        content_item = response["Item"]

        # Single-key table (ADR-004): one record per content_id, delete it directly
        content_table.delete_item(Key={"content_id": content_id})
        logger.info(f"Deleted content {content_id} from DynamoDB")

        # Delete associated files from S3 if they exist
        try:
            # Delete embedding file if it exists
            if "embedding_s3_key" in content_item:
                s3.delete_object(Bucket=content_bucket, Key=content_item["embedding_s3_key"])
                logger.info(f"Deleted embedding file for content {content_id}")

            # Delete any other associated files
            if "file_path" in content_item and content_item["file_path"]:
                s3.delete_object(Bucket=content_bucket, Key=content_item["file_path"])
                logger.info(f"Deleted content file for content {content_id}")

        except Exception as s3_error:
            # Log S3 errors but don't fail the deletion
            logger.warning(f"Failed to delete S3 files for content {content_id}: {str(s3_error)}")

        # Delete associated processing jobs (wave0 Req 9 — real-data-only)
        try:
            # Query for jobs associated with this content
            job_response = jobs_table.scan(
                FilterExpression="content_id = :content_id", ExpressionAttributeValues={":content_id": content_id}
            )

            # Delete associated jobs
            for job in job_response.get("Items", []):
                jobs_table.delete_item(Key={"job_id": job["job_id"]})
                logger.info(f"Deleted associated job {job['job_id']}")

        except Exception as job_error:
            logger.warning(f"Failed to delete associated jobs for content {content_id}: {str(job_error)}")

        logger.info(f"Content {content_id} successfully deleted by user {user_info['user_id']}")
        return {"deleted": True}

    except Exception as e:
        logger.error(f"Error deleting real content {content_id}: {str(e)}")
        raise


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

        jobs_table.put_item(Item=job_item)

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
