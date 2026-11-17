"""Processing-jobs service (Service_Layer single home for the processing domain).

This module is the designated ``services/*`` home for the ui_backend processing domain
(Requirement 1.1 / 4.5 / 7.1): the feature-flagged real/mock status selection, the
graceful-fallback semantics, and the full processing-status / job-lifecycle logic (real
and mock status retrieval, queue/performance metric calculation, alert generation, and the
job CRUD + progress + timing helpers, along with all ``JOBS_TABLE_NAME`` DynamoDB access)
now live here as the single source of truth. The business logic and data-store access were
bodily relocated from the former top-level ``processing_status`` module (now deleted);
``ProcessingJobsService.get_processing_status`` is the public entry for the route module and
no longer delegates back to a root handler module.

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``try/except ImportError`` shim (Requirement 5.1
/ 5.2) and no ``sys.path`` manipulation (Requirement 15.2).
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

# Import response validation middleware
from shared.utils.response_helpers import cors_headers
from shared.utils.response_validator import validate_response
from shared.utils.safe_datetime_parser import safe_parse_datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
jobs_table = None


def get_jobs_table():
    """Get jobs table with lazy initialization."""
    logger.info("=== GET_JOBS_TABLE START ===")

    global jobs_table
    if jobs_table is None:
        table_name = os.environ.get("JOBS_TABLE_NAME")
        logger.info(f"Initializing jobs table with name: {table_name}")

        if not table_name:
            logger.error("JOBS_TABLE_NAME environment variable not set")
            raise ValueError("JOBS_TABLE_NAME environment variable not set")

        try:
            jobs_table = dynamodb.Table(table_name)
            logger.info(f"Jobs table initialized successfully: {jobs_table.name}")
        except Exception as e:
            logger.error(f"Failed to initialize jobs table: {str(e)}", exc_info=True)
            raise
    else:
        logger.info(f"Using existing jobs table: {jobs_table.name}")

    logger.info("=== GET_JOBS_TABLE END ===")
    return jobs_table


def get_user_from_token(headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Extract user information from Cognito JWT token
    """
    logger.info("=== GET_USER_FROM_TOKEN START ===")
    logger.info(f"Headers received: {list(headers.keys()) if headers else 'none'}")

    auth_header = headers.get("Authorization") or headers.get("authorization")
    logger.info(f"Auth header present: {bool(auth_header)}")

    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("No valid Authorization header found")
        logger.info("=== GET_USER_FROM_TOKEN END (no auth) ===")
        return None

    try:
        # For demo purposes, we'll simulate user validation
        # In production, you would validate the JWT token with Cognito
        token = auth_header.replace("Bearer ", "")
        logger.info(f"Token extracted, length: {len(token)}")

        # Mock user info for demo
        user_info = {"user_id": "demo_user_001", "email": "publisher@example.com", "username": "demo_publisher"}
        logger.info(f"Mock user info created: {user_info}")
        logger.info("=== GET_USER_FROM_TOKEN END (success) ===")
        return user_info
    except Exception as e:
        logger.error(f"=== GET_USER_FROM_TOKEN ERROR === {str(e)}", exc_info=True)
        return None


@validate_response("/processing/status", "GET")
def handle_processing_status(user_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle processing status request - now using real job data from DynamoDB
    """
    logger.info("=== HANDLE_PROCESSING_STATUS START ===")
    logger.info(f"User info: {user_info}")

    try:
        # Real-data-only: read job status from DynamoDB (wave0 Req 9 — mock fallback removed)
        processing_status = get_real_processing_status()
        logger.info(
            f"Real data retrieved successfully, active_jobs: {processing_status.get('active_jobs', 'unknown')}"
        )
        response = create_success_response(processing_status)
        logger.info("=== HANDLE_PROCESSING_STATUS END (success) ===")
        return response

    except Exception as e:
        logger.error(f"=== HANDLE_PROCESSING_STATUS ERROR === {str(e)}", exc_info=True)
        logger.error("=== HANDLE_PROCESSING_STATUS END (error) ===")
        return create_error_response(500, "Failed to get processing status")


def get_real_processing_status() -> Dict[str, Any]:
    """
    Get real processing status by querying DynamoDB jobs table
    """
    logger.info("=== GET_REAL_PROCESSING_STATUS START ===")

    from boto3.dynamodb.conditions import Key

    try:
        # Get jobs table
        logger.info("Getting jobs table...")
        jobs_table = get_jobs_table()
        logger.info(f"Jobs table obtained: {jobs_table.name}")

        # Query jobs by status using GSI
        logger.info("Querying active jobs (processing status)...")
        active_jobs_response = jobs_table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq("processing"),
            ScanIndexForward=False,  # Most recent first
            Limit=50,
        )
        logger.info(f"Active jobs query completed, count: {active_jobs_response.get('Count', 0)}")

        logger.info("Querying queued jobs...")
        queued_jobs_response = jobs_table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq("queued"),
            ScanIndexForward=False,  # Most recent first
            Limit=50,
        )
        logger.info(f"Queued jobs query completed, count: {queued_jobs_response.get('Count', 0)}")

        logger.info("Querying completed jobs...")
        completed_jobs_response = jobs_table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq("completed"),
            ScanIndexForward=False,  # Most recent first
            Limit=100,
        )
        logger.info(f"Completed jobs query completed, count: {completed_jobs_response.get('Count', 0)}")

        logger.info("Querying failed jobs...")
        failed_jobs_response = jobs_table.query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq("failed"),
            ScanIndexForward=False,  # Most recent first
            Limit=50,
        )
        logger.info(f"Failed jobs query completed, count: {failed_jobs_response.get('Count', 0)}")

        # Extract job lists
        active_jobs = active_jobs_response.get("Items", [])
        queued_jobs = queued_jobs_response.get("Items", [])
        completed_jobs = completed_jobs_response.get("Items", [])
        failed_jobs = failed_jobs_response.get("Items", [])

        logger.info(
            f"Job counts - Active: {len(active_jobs)}, Queued: {len(queued_jobs)}, Completed: {len(completed_jobs)}, Failed: {len(failed_jobs)}"
        )

        # Calculate metrics from real data
        logger.info("Calculating metrics from real data...")
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        logger.info(f"Today start time: {today_start}")

        # Filter completed and failed jobs for today - with safe datetime parsing
        logger.info("Filtering jobs for today...")
        completed_today = []
        failed_today = []

        for job in completed_jobs:
            if "created_at" in job:
                created_at = safe_parse_datetime(job["created_at"])
                if created_at and created_at >= today_start:
                    completed_today.append(job)
                elif not created_at:
                    logger.warning(
                        f"Failed to parse created_at for completed job {job.get('job_id', 'unknown')}: {job['created_at']}"
                    )

        for job in failed_jobs:
            if "created_at" in job:
                created_at = safe_parse_datetime(job["created_at"])
                if created_at and created_at >= today_start:
                    failed_today.append(job)
                elif not created_at:
                    logger.warning(
                        f"Failed to parse created_at for failed job {job.get('job_id', 'unknown')}: {job['created_at']}"
                    )

        logger.info(f"Today's jobs - Completed: {len(completed_today)}, Failed: {len(failed_today)}")

        # Calculate average processing time from completed jobs
        logger.info("Calculating average processing time...")
        processing_times = []
        for job in completed_jobs[:20]:  # Use recent 20 jobs for average
            if "completed_at" in job and "created_at" in job:
                try:
                    created = safe_parse_datetime(job["created_at"])
                    completed = safe_parse_datetime(job["completed_at"])
                    if created and completed:
                        processing_time = (completed - created).total_seconds() * 1000  # Convert to ms
                        processing_times.append(processing_time)
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning(f"Failed to calculate processing time for job {job.get('job_id', 'unknown')}: {e}")
                    continue

        avg_processing_time = int(sum(processing_times) / len(processing_times)) if processing_times else 2800
        logger.info(f"Average processing time calculated: {avg_processing_time}ms from {len(processing_times)} jobs")

        # Get recent jobs for display (mix of all statuses)
        all_recent_jobs = []
        all_recent_jobs.extend(active_jobs[:2])
        all_recent_jobs.extend(queued_jobs[:2])
        all_recent_jobs.extend(completed_jobs[:3])
        all_recent_jobs.extend(failed_jobs[:1])

        # Sort by created_at descending
        all_recent_jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        recent_jobs = all_recent_jobs[:8]  # Show top 8 recent jobs

        # Format recent jobs for UI compatibility (ProcessingJob interface)
        formatted_recent_jobs = []
        for job in recent_jobs:
            # Map job status to UI stage
            stage_mapping = {
                "queued": "Waiting",
                "processing": "IAB Classification",  # Default processing stage
                "completed": "Available",
                "failed": "Validation Error",
            }

            # Calculate progress based on status
            progress_mapping = {
                "queued": 0,
                "processing": 65,  # Mock progress for processing jobs
                "completed": 100,
                "failed": 25,
            }

            formatted_job = {
                "job_id": job.get("job_id", ""),
                "content_id": job.get("content_id", ""),
                "filename": f"{job.get('content_id', 'unknown')}.json",  # Generate filename from content_id
                "status": job.get("status", "unknown"),
                "stage": stage_mapping.get(job.get("status", "unknown"), "Unknown"),
                "progress": progress_mapping.get(job.get("status", "unknown"), 0),
                "created_at": job.get("created_at", ""),
                "updated_at": job.get("updated_at", job.get("created_at", "")),
            }

            # Add status-specific fields
            if job.get("status") == "completed" and "completed_at" in job:
                formatted_job["updated_at"] = job["completed_at"]
            elif job.get("status") == "processing":
                formatted_job["estimated_completion"] = job.get("estimated_completion", "")
                # Use more specific processing stages if available
                if "current_operation" in job:
                    operation_to_stage = {
                        "validation": "Validation",
                        "categorization": "IAB Classification",
                        "embedding": "Vector Embedding",
                        "processing": "IAB Classification",
                    }
                    formatted_job["stage"] = operation_to_stage.get(job["current_operation"], "IAB Classification")
            elif job.get("status") == "queued":
                formatted_job["estimated_completion"] = job.get("estimated_completion", "")
            elif job.get("status") == "failed":
                # Add error message for failed jobs
                formatted_job["error_message"] = job.get("error_message", "Processing failed")

            formatted_recent_jobs.append(formatted_job)

        # Calculate queue statistics by job type
        queue_stats = calculate_queue_statistics(active_jobs, queued_jobs)

        # Build the response with real data - UI expects 'jobs' array
        logger.info("Building final response...")
        processing_status = {
            "jobs": formatted_recent_jobs,  # UI expects this field
            "active_jobs": len(active_jobs),
            "queued_jobs": len(queued_jobs),
            "completed_today": len(completed_today),
            "failed_today": len(failed_today),
            "average_processing_time_ms": avg_processing_time,
            "system_status": "healthy" if len(failed_today) < 5 else "degraded",
            "services": get_service_status(),  # Keep mock service status for now
            "recent_jobs": formatted_recent_jobs,  # Keep for backward compatibility
            "queue_statistics": queue_stats,
            "resource_usage": get_resource_usage(),  # Keep mock resource usage for now
            "performance_metrics": calculate_performance_metrics(completed_jobs, failed_jobs),
            "alerts": generate_alerts(failed_today, avg_processing_time),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"Response built successfully with {len(formatted_recent_jobs)} recent jobs")
        logger.info("=== GET_REAL_PROCESSING_STATUS END (success) ===")
        return processing_status

    except Exception as e:
        logger.error(f"=== GET_REAL_PROCESSING_STATUS ERROR === {str(e)}", exc_info=True)
        raise


def calculate_queue_statistics(active_jobs: List[Dict], queued_jobs: List[Dict]) -> Dict[str, Any]:
    """
    Calculate queue statistics by job type from real job data
    """
    stats = {
        "content_processing": {"queued": 0, "processing": 0, "average_wait_time_ms": 30000},
        "content_reprocessing": {"queued": 0, "processing": 0, "average_wait_time_ms": 15000},
        "bulk_operations": {"queued": 0, "processing": 0, "average_wait_time_ms": 60000},
    }

    # Count by job type
    for job in active_jobs:
        job_type = job.get("job_type", "content_processing")
        if job_type in stats:
            stats[job_type]["processing"] += 1

    for job in queued_jobs:
        job_type = job.get("job_type", "content_processing")
        if job_type in stats:
            stats[job_type]["queued"] += 1

    return stats


def calculate_performance_metrics(completed_jobs: List[Dict], failed_jobs: List[Dict]) -> Dict[str, Any]:
    """
    Calculate performance metrics from real job data
    """
    now = datetime.now(timezone.utc)

    # Last 24 hours
    day_ago = now - timedelta(days=1)
    completed_24h = [job for job in completed_jobs if safe_parse_datetime(job["created_at"]) >= day_ago]
    failed_24h = [job for job in failed_jobs if safe_parse_datetime(job["created_at"]) >= day_ago]

    # Last 7 days
    week_ago = now - timedelta(days=7)
    completed_7d = [job for job in completed_jobs if safe_parse_datetime(job["created_at"]) >= week_ago]
    failed_7d = [job for job in failed_jobs if safe_parse_datetime(job["created_at"]) >= week_ago]

    # Calculate processing times for 24h
    processing_times_24h = []
    for job in completed_24h:
        if "completed_at" in job and "created_at" in job:
            try:
                created = safe_parse_datetime(job["created_at"])
                completed = safe_parse_datetime(job["completed_at"])
                processing_time = (completed - created).total_seconds() * 1000
                processing_times_24h.append(processing_time)
            except (ValueError, KeyError):
                continue

    avg_24h = int(sum(processing_times_24h) / len(processing_times_24h)) if processing_times_24h else 2650
    p95_24h = int(sorted(processing_times_24h)[int(len(processing_times_24h) * 0.95)]) if processing_times_24h else 4200
    p99_24h = int(sorted(processing_times_24h)[int(len(processing_times_24h) * 0.99)]) if processing_times_24h else 6800

    # Calculate success rate for 7 days
    total_7d = len(completed_7d) + len(failed_7d)
    success_rate_7d = (len(completed_7d) / total_7d * 100) if total_7d > 0 else 100.0

    return {
        "last_24_hours": {
            "total_jobs": len(completed_24h) + len(failed_24h),
            "successful_jobs": len(completed_24h),
            "failed_jobs": len(failed_24h),
            "average_processing_time_ms": avg_24h,
            "p95_processing_time_ms": p95_24h,
            "p99_processing_time_ms": p99_24h,
        },
        "last_7_days": {
            "total_jobs": total_7d,
            "successful_jobs": len(completed_7d),
            "failed_jobs": len(failed_7d),
            "average_processing_time_ms": avg_24h,  # Use 24h average for 7d as well
            "success_rate": round(success_rate_7d, 1),
        },
    }


def generate_alerts(failed_today: List[Dict], avg_processing_time: int) -> List[Dict]:
    """
    Generate alerts based on real data
    """
    alerts = []

    # Alert if too many failures today
    if len(failed_today) > 5:
        alerts.append(
            {
                "id": f"alert_failures_{datetime.now().strftime('%Y%m%d')}",
                "severity": "error",
                "message": f"High failure rate: {len(failed_today)} jobs failed today",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resolved": False,
            }
        )

    # Alert if processing time is too high
    if avg_processing_time > 5000:
        alerts.append(
            {
                "id": f"alert_performance_{datetime.now().strftime('%Y%m%d')}",
                "severity": "warning",
                "message": f"Processing time above threshold: {avg_processing_time}ms average",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "resolved": False,
            }
        )

    return alerts


def get_service_status() -> Dict[str, Any]:
    """
    Get service status - keeping mock data for now as these would require additional AWS API calls
    """
    return {
        "embedding_service": {
            "status": "online",
            "response_time_ms": 150,
            "error_rate": 0.01,
            "last_check": datetime.now(timezone.utc).isoformat(),
        },
        "image_processing": {
            "status": "online",
            "response_time_ms": 450,
            "error_rate": 0.02,
            "last_check": datetime.now(timezone.utc).isoformat(),
        },
        "categorization": {
            "status": "online",
            "response_time_ms": 200,
            "error_rate": 0.005,
            "last_check": datetime.now(timezone.utc).isoformat(),
        },
        "vector_search": {
            "status": "online",
            "response_time_ms": 75,
            "error_rate": 0.001,
            "last_check": datetime.now(timezone.utc).isoformat(),
        },
    }


def get_resource_usage() -> Dict[str, Any]:
    """
    Return resource-usage telemetry from the real source (CloudWatch) or an empty result.

    wave0 Req 10.2/10.3: the fabricated demonstration values were removed. Real CloudWatch
    metric retrieval is not yet wired, so this returns an empty result rather than mock data.
    """
    return {}


def create_success_response(data: Any) -> Dict[str, Any]:
    """
    Create a successful API response
    """
    logger.info("=== CREATE_SUCCESS_RESPONSE START ===")
    logger.info(f"Response data keys: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")

    response = {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(data, default=str),
    }

    logger.info(f"Success response created, body length: {len(response['body'])}")
    logger.info("=== CREATE_SUCCESS_RESPONSE END ===")
    return response


# Job Tracking Functions for Processing Lifecycle


def create_processing_job(
    content_id: str, user_id: str, job_type: str = "content_processing", processing_options: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Create a new processing job record in DynamoDB with proper tracking fields
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    job_item = {
        "job_id": job_id,
        "content_id": content_id,
        "job_type": job_type,
        "status": "queued",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "created_by": user_id,
        "processing_options": processing_options or {},
        "estimated_completion": (now + timedelta(minutes=5)).isoformat(),
        "processing_metrics": {
            "queue_time_ms": 0,
            "processing_time_ms": 0,
            "total_time_ms": 0,
            "tokens_processed": 0,
            "images_processed": 0,
            "operations_completed": [],
            "current_operation": None,
            "progress_percentage": 0,
        },
        "lifecycle_events": [
            {
                "event": "job_created",
                "timestamp": now.isoformat(),
                "details": {"job_type": job_type, "created_by": user_id},
            }
        ],
    }

    try:
        jobs_table = get_jobs_table()
        jobs_table.put_item(Item=job_item)
        logger.info(f"Created processing job {job_id} for content {content_id}")
        return {"success": True, "job_id": job_id, "job_item": job_item}
    except Exception as e:
        logger.error(f"Failed to create job {job_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def update_job_status(
    job_id: str, new_status: str, metrics: Dict[str, Any] = None, operation_details: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Update job status and add lifecycle tracking with metrics
    """
    try:
        jobs_table = get_jobs_table()
        now = datetime.now(timezone.utc)

        # Prepare update expression
        update_expression = "SET #status = :status, updated_at = :updated_at"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {":status": new_status, ":updated_at": now.isoformat()}

        # Add lifecycle event
        lifecycle_event = {
            "event": f"status_changed_to_{new_status}",
            "timestamp": now.isoformat(),
            "details": operation_details or {},
        }

        update_expression += ", lifecycle_events = list_append(lifecycle_events, :event)"
        expression_attribute_values[":event"] = [lifecycle_event]

        # Update processing metrics if provided
        if metrics:
            for key, value in metrics.items():
                update_expression += f", processing_metrics.{key} = :metric_{key}"
                expression_attribute_values[f":metric_{key}"] = value

        # Add completion timestamp for completed/failed jobs
        if new_status in ["completed", "failed"]:
            update_expression += ", completed_at = :completed_at"
            expression_attribute_values[":completed_at"] = now.isoformat()

        # Add started timestamp for processing jobs
        if new_status == "processing":
            update_expression += ", started_at = :started_at"
            expression_attribute_values[":started_at"] = now.isoformat()

        # Update the job
        response = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )

        updated_job = response.get("Attributes", {})
        logger.info(f"Updated job {job_id} status to {new_status}")

        # Calculate timing metrics for completed jobs
        if new_status in ["completed", "failed"] and updated_job:
            timing_metrics = calculate_job_timing_metrics(updated_job)
            if timing_metrics:
                # Update with calculated timing metrics
                jobs_table.update_item(
                    Key={"job_id": job_id},
                    UpdateExpression="SET processing_metrics.queue_time_ms = :queue_time, processing_metrics.processing_time_ms = :proc_time, processing_metrics.total_time_ms = :total_time",
                    ExpressionAttributeValues={
                        ":queue_time": timing_metrics["queue_time_ms"],
                        ":proc_time": timing_metrics["processing_time_ms"],
                        ":total_time": timing_metrics["total_time_ms"],
                    },
                )
                updated_job["processing_metrics"].update(timing_metrics)

        return {"success": True, "job": updated_job}

    except Exception as e:
        logger.error(f"Failed to update job {job_id} status to {new_status}: {str(e)}")
        return {"success": False, "error": str(e)}


def calculate_job_timing_metrics(job: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """
    Calculate timing metrics for a job based on lifecycle timestamps
    """
    try:
        created_at = safe_parse_datetime(job["created_at"])
        # An unparseable created_at (safe_parse_datetime -> None) makes every delta undefined;
        # return None rather than doing `None - None` arithmetic (wave0 Req 15.1).
        if created_at is None:
            logger.warning("Could not calculate timing metrics: unparseable created_at")
            return None

        # Calculate queue time (created to started)
        queue_time_ms = 0
        started_at = safe_parse_datetime(job["started_at"]) if "started_at" in job else None
        if started_at is not None:
            queue_time_ms = int((started_at - created_at).total_seconds() * 1000)

        # Calculate processing time (started to completed)
        processing_time_ms = 0
        total_time_ms = 0

        if "completed_at" in job:
            completed_at = safe_parse_datetime(job["completed_at"])
            if completed_at is not None:
                total_time_ms = int((completed_at - created_at).total_seconds() * 1000)

                if started_at is not None:
                    processing_time_ms = int((completed_at - started_at).total_seconds() * 1000)
                else:
                    # If no started_at, assume processing time is total time
                    processing_time_ms = total_time_ms

        return {
            "queue_time_ms": queue_time_ms,
            "processing_time_ms": processing_time_ms,
            "total_time_ms": total_time_ms,
        }

    except (ValueError, KeyError, TypeError) as e:
        logger.warning(f"Could not calculate timing metrics for job: {str(e)}")
        return None


def update_job_progress(
    job_id: str, progress_percentage: int, current_operation: str = None, operation_metrics: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Update job progress during processing
    """
    try:
        jobs_table = get_jobs_table()
        now = datetime.now(timezone.utc)

        update_expression = "SET updated_at = :updated_at, processing_metrics.progress_percentage = :progress"
        expression_attribute_values = {":updated_at": now.isoformat(), ":progress": progress_percentage}

        if current_operation:
            update_expression += ", processing_metrics.current_operation = :operation"
            expression_attribute_values[":operation"] = current_operation

        # Add operation-specific metrics
        if operation_metrics:
            for key, value in operation_metrics.items():
                update_expression += f", processing_metrics.{key} = :metric_{key}"
                expression_attribute_values[f":metric_{key}"] = value

        # Add progress event to lifecycle
        lifecycle_event = {
            "event": "progress_update",
            "timestamp": now.isoformat(),
            "details": {
                "progress_percentage": progress_percentage,
                "current_operation": current_operation,
                "metrics": operation_metrics or {},
            },
        }

        update_expression += ", lifecycle_events = list_append(lifecycle_events, :event)"
        expression_attribute_values[":event"] = [lifecycle_event]

        response = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )

        logger.info(f"Updated job {job_id} progress to {progress_percentage}%")
        return {"success": True, "job": response.get("Attributes", {})}

    except Exception as e:
        logger.error(f"Failed to update job {job_id} progress: {str(e)}")
        return {"success": False, "error": str(e)}


def complete_job_operation(
    job_id: str, operation_name: str, operation_metrics: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Mark a specific operation as completed within a job
    """
    try:
        jobs_table = get_jobs_table()
        now = datetime.now(timezone.utc)

        # Add operation to completed operations list
        update_expression = "SET updated_at = :updated_at, processing_metrics.operations_completed = list_append(if_not_exists(processing_metrics.operations_completed, :empty_list), :operation)"
        expression_attribute_values = {
            ":updated_at": now.isoformat(),
            ":empty_list": [],
            ":operation": [operation_name],
        }

        # Update operation-specific metrics
        if operation_metrics:
            for key, value in operation_metrics.items():
                update_expression += f", processing_metrics.{key} = :metric_{key}"
                expression_attribute_values[f":metric_{key}"] = value

        # Add lifecycle event
        lifecycle_event = {
            "event": "operation_completed",
            "timestamp": now.isoformat(),
            "details": {"operation": operation_name, "metrics": operation_metrics or {}},
        }

        update_expression += ", lifecycle_events = list_append(lifecycle_events, :event)"
        expression_attribute_values[":event"] = [lifecycle_event]

        response = jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",
        )

        logger.info(f"Completed operation '{operation_name}' for job {job_id}")
        return {"success": True, "job": response.get("Attributes", {})}

    except Exception as e:
        logger.error(f"Failed to complete operation '{operation_name}' for job {job_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific job
    """
    try:
        jobs_table = get_jobs_table()

        response = jobs_table.get_item(Key={"job_id": job_id})

        if "Item" not in response:
            return {"success": False, "error": "Job not found"}

        job = response["Item"]

        # Calculate current timing metrics if job is in progress
        if job.get("status") in ["processing", "completed", "failed"]:
            timing_metrics = calculate_job_timing_metrics(job)
            if timing_metrics:
                job["processing_metrics"].update(timing_metrics)

        return {"success": True, "job": job}

    except Exception as e:
        logger.error(f"Failed to get job details for {job_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def get_jobs_by_content_id(content_id: str) -> Dict[str, Any]:
    """
    Get all jobs associated with a specific content item
    """
    try:
        jobs_table = get_jobs_table()

        # Query using content_id GSI
        response = jobs_table.query(
            IndexName="content-id-index",
            KeyConditionExpression=Key("content_id").eq(content_id),
            ScanIndexForward=False,  # Most recent first
        )

        jobs = response.get("Items", [])

        # Calculate timing metrics for each job
        for job in jobs:
            if job.get("status") in ["processing", "completed", "failed"]:
                timing_metrics = calculate_job_timing_metrics(job)
                if timing_metrics:
                    job["processing_metrics"].update(timing_metrics)

        return {"success": True, "jobs": jobs}

    except Exception as e:
        logger.error(f"Failed to get jobs for content {content_id}: {str(e)}")
        return {"success": False, "error": str(e)}


def create_error_response(status_code: int, message: str, details: str = None) -> Dict[str, Any]:
    """
    Create an error API response
    """
    logger.info("=== CREATE_ERROR_RESPONSE START ===")
    logger.error(f"Creating error response: status_code={status_code}, message={message}, details={details}")

    error_data = {"error": message, "code": status_code, "request_id": f"req_{uuid.uuid4().hex[:8]}"}

    if details:
        error_data["details"] = details

    response = {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(error_data),
    }

    logger.info(f"Error response created with request_id: {error_data['request_id']}")
    logger.info("=== CREATE_ERROR_RESPONSE END ===")
    return response


class ProcessingJobsService:
    """Single home for processing-job status selection + graceful-fallback logic."""

    def get_processing_status(self) -> Dict[str, Any]:
        """Return the processing-status payload for ``GET /processing/status``.

        Real-data-only (wave0 Req 9): reads live job status from DynamoDB via the
        module-level ``get_real_processing_status``. The mock fallback was removed.

        Returns:
            The processing-status dict produced by ``get_real_processing_status``.

        Raises:
            Exception: Propagates any underlying real-data retrieval error.
        """
        # Real-data-only: read job status from DynamoDB (wave0 Req 9 — mock fallback removed)
        processing_status = get_real_processing_status()
        logger.info(
            f"Real data retrieved successfully, active_jobs: "
            f"{processing_status.get('active_jobs', 'unknown')}"
        )

        return processing_status
