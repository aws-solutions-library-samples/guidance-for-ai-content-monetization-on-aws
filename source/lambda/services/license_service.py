"""
License service (Service_Layer single home for RSL license management + CoMP discovery).

This module is the designated ``services/*`` home for the RSL (Really Simple Licensing) license
domain logic that landed in MR7 (Requirement 1.1 / 19.1): RSL license CRUD, versioning, and
validation, plus CoMP v1.0 discovery-document (``comp.json``) generation. It owns **all**
``cm-licenses`` Licenses_Table access as the single source of truth.

The logic was extracted verbatim from the fat, currently-unwired top-level module
``comp_licensing.py`` so behavior is preserved byte-for-byte (Requirement 2 / 19.6). Public
function names and signatures are kept identical to the original module so the ``comp_licensing``
handler can delegate here without changing observable behavior (its ``lambda_handler`` is retired
separately in task 16.2).

Imports are declared at module top level with no ``try/except ImportError`` shim (Requirement 5.1
/ 5.2) and no ``sys.path`` manipulation (Requirement 15.2). This module uses only the standard
library and ``boto3``, so there are no cross-module first-party imports to root at the
Canonical_Import_Root ``source/lambda/``.

IAB CoMP v1.0: https://github.com/IABTechLab/CoMP/blob/main/CoMP-1.0.md
RSL Specification: https://github.com/IABTechLab/RSL
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from shared.utils.response_helpers import cors_headers

# Canonical CoMP v1.0 function codes — single source of truth (GitLab #3).
#
# This module previously declared its OWN ``COMP_FUNCTION_CODES`` table with incompatible
# semantics (``1=Search/Discovery, 4=Evaluation, 5=Analysis``) while
# ``services/comp_query_service.py`` declared the canonical IAB list
# (``0=all, 1=ai-all, 2=ai-train, 3=ai-input, 4=ai-index, 5=search``). The two disagreed on the
# meaning of codes 1, 4 and 5, so a license authored under one reading was mis-authorized under
# the other on the authorization/compliance path — a live correctness bug, not a cosmetic
# duplication. The divergent table is DELETED and the canonical one imported from the
# Service_Layer home that owns it, so the two services can no longer drift.
# ``COMP_FUNCTION_CODES`` carries the slug form ("ai-train"), ``COMP_FUNCTION_NAMES`` the human
# form ("Training"); this module renders the human form in validation responses. Both names stay
# importable from here — ``comp_licensing`` re-exports them.
#
# The import is absolute, rooted at the Canonical_Import_Root ``source/lambda/``, declared at
# module top level with no ``try/except ImportError`` shim and no ``sys.path`` manipulation.
# ``services.comp_query_service`` imports nothing first-party, so this direction is acyclic.
from services.comp_query_service import COMP_FUNCTION_CODES, COMP_FUNCTION_NAMES

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Friendly name to function code mapping for the validation endpoint, expressed in the canonical
# CoMP semantics imported above. NOTE: the pre-canonicalization 'evaluation'/'eval' -> 4 and
# 'analysis' -> 5 entries have NO canonical equivalent (4 is ai-index, 5 is search), so they are
# REMOVED rather than silently remapped to a different meaning — an unrecognized intended_use now
# 400s with the valid values listed.
USE_CASE_MAPPING = {
    'training': 2,      # ai-train
    'fine-tuning': 2,   # ai-train
    'finetune': 2,      # ai-train
    'inference': 3,     # ai-input
    'rag': 3,           # ai-input
    'search': 5,        # search
    'discovery': 5,     # search
    'indexing': 4,      # ai-index
    'index': 4          # ai-index
}

# Configuration
API_BASE_URL = os.environ.get('API_BASE_URL', 'https://api.publisher.com/ai-content-monetization/v1')
SELLER_DOMAIN = os.environ.get('SELLER_DOMAIN', 'publisher.com')
MCP_SERVER_AVAILABLE = os.environ.get('MCP_SERVER_AVAILABLE', 'true').lower() == 'true'


def get_licenses_table():
    """Get the Licenses_Table (``cm-licenses``) with lazy initialization.

    This is the single access point for the licenses DynamoDB table (Requirement 19.1). The table
    resource is cached on the function attribute so it is created once per process.
    """
    if not hasattr(get_licenses_table, '_table'):
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('LICENSES_TABLE_NAME', 'LicensesTable')
        get_licenses_table._table = dynamodb.Table(table_name)
    return get_licenses_table._table


def convert_floats_to_decimals(obj: Any) -> Any:
    """
    Recursively convert all float values to Decimal for DynamoDB compatibility.
    
    Args:
        obj: Any Python object (dict, list, float, etc.)
        
    Returns:
        Object with all floats converted to Decimal
    """
    if isinstance(obj, list):
        return [convert_floats_to_decimals(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj


def decimal_to_float(obj: Any) -> Any:
    """Convert Decimal objects to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(i) for i in obj]
    return obj


def get_comp_discovery() -> Dict[str, Any]:
    """
    Generate CoMP v1.0 discovery document.
    Public endpoint - no authentication required.
    
    Returns:
        Discovery document with endpoints, auth methods, and license URLs
    """
    
    discovery_doc = {
        "comp_version": "1.0",
        "seller": SELLER_DOMAIN,
        "content_endpoints": [
            {
                "path": "/query",
                "method": "POST",
                "description": "Keyword and IAB category filtered content query",
                "delivery_formats": [2, 3]  # 2=API, 3=MCP
            }
        ],
        "license_catalog_url": f"{API_BASE_URL}/licenses",
        "default_license_url": f"{API_BASE_URL}/licenses/lic_default_v1",
        "report_url": f"{API_BASE_URL}/log",
        "supported_auth_methods": [1, 2, 4],  # Bearer, OAuth, API Key
        "llms_txt_url": f"{API_BASE_URL}/llms.txt"
    }
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Cache-Control': 'public, max-age=3600',
            # TM-02: public CoMP discovery document — explicit opt-in, still config-driven.
            **cors_headers(public=True)
        },
        'body': json.dumps(discovery_doc, indent=2)
    }


def list_licenses(query_params: Dict[str, str], username: Optional[str]) -> Dict[str, Any]:
    """
    List all license documents with filtering and pagination.
    Public endpoint for CoMP license catalog discovery.
    
    Args:
        query_params: Query string parameters (status, limit, next_token)
        username: Authenticated user identifier (optional for public read)
        
    Returns:
        List of license documents
    """
    
    # GET /licenses is public for CoMP license catalog discovery
    # Authentication only required for POST/PUT/DELETE operations
    
    try:
        status = query_params.get('status', 'active')
        limit = int(query_params.get('limit', '20'))
        next_token = query_params.get('next_token')
        
        # Validate parameters. 'revoked' is the status written by archive_license()'s soft revoke —
        # it MUST be an accepted filter value or a revoked licence becomes unlistable, while the
        # default ('active') keeps revoked documents out of ordinary reads.
        if status not in ['active', 'draft', 'archived', 'revoked']:
            return error_response(400, 'Invalid status. Must be: active, draft, archived, or revoked')
        
        if limit < 1 or limit > 100:
            return error_response(400, 'Limit must be between 1 and 100')
        
        # Scan WITHOUT a status filter, then resolve each licence's latest version, and only then
        # filter by status.
        #
        # The order matters. Revoke/renew are additive: archive_license appends a new 'revoked'
        # version and renew_license appends a new 'active' one, so one license_id holds several
        # versions with DIFFERENT statuses. Filtering in the scan first and grouping second picked
        # the latest of the MATCHING rows rather than the licence's actual current version — so a
        # revoked licence whose earlier versions were active still appeared under status=active,
        # showing a stale version, and showed up under status=revoked at the same time.
        scan_kwargs = {
            'FilterExpression': 'attribute_exists(license_id)',
        }

        if next_token:
            # Decode pagination token
            scan_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(next_token))

        # Paginate the scan to completion: the grouping below is only correct with every version
        # of a license_id in hand. A per-page 'Limit' could split one licence's versions across
        # pages and resolve the wrong "latest", so the limit is applied to the GROUPED result.
        all_licenses = []
        while True:
            response = get_licenses_table().scan(**scan_kwargs)
            all_licenses.extend(response.get('Items', []))
            last_evaluated_key = response.get('LastEvaluatedKey')
            if not last_evaluated_key:
                break
            scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

        # Group by license_id and keep only the latest version of each
        latest_licenses = {}
        for lic in all_licenses:
            license_id = lic['license_id']
            version = lic.get('version', 0)
            if license_id not in latest_licenses or version > latest_licenses[license_id].get('version', 0):
                latest_licenses[license_id] = lic

        # Filter the resolved current versions by the requested status.
        matching = [lic for lic in latest_licenses.values() if lic.get('status') == status]
        licenses = matching[:limit]

        # 'total_available' is the full count at this status (pre-limit) so a caller can tell the
        # page is partial; 'count' is what this page carries.
        result = {
            'licenses': [format_license_summary(lic) for lic in licenses],
            'count': len(licenses),
            'total_available': len(matching)
        }

        # No next_token: the scan is fully drained above (required for correct version
        # resolution), so the DynamoDB LastEvaluatedKey is not a valid cursor for the GROUPED
        # result. Paging over grouped licences would need a cursor over license_ids — not
        # implemented, and not reachable while 'limit' (max 100) exceeds the licence count.

        return success_response(result)
    
    except Exception as e:
        logger.exception("Error listing licenses")
        return error_response(500, f'Failed to list licenses: {str(e)}')


def create_license(license_data: Dict[str, Any], username: Optional[str]) -> Dict[str, Any]:
    """
    Create a new RSL license document.
    UI-Only endpoint - requires Cognito authentication.
    
    Args:
        license_data: License document data
        username: Authenticated user identifier
        
    Returns:
        Created license document with assigned license_id
    """
    
    if not username:
        return error_response(401, 'Authentication required')
    
    try:
        # Validate required fields
        required_fields = ['title', 'description', 'allowed_uses']
        for field in required_fields:
            if field not in license_data:
                return error_response(400, f'Missing required field: {field}')
        
        # Validate attribution field if present
        if 'attribution' in license_data:
            is_valid, error_msg = validate_attribution(license_data['attribution'])
            if not is_valid:
                return error_response(400, f'Invalid attribution: {error_msg}')
        
        # Generate license ID
        license_id = license_data.get('license_id') or f"lic_{uuid.uuid4().hex[:12]}"
        
        # Create license document
        now = datetime.now(timezone.utc).isoformat()
        
        license_doc = {
            'license_id': license_id,
            'version': 1,
            'status': license_data.get('status', 'active'),  # Default to 'active' instead of 'draft'
            'title': license_data['title'],
            'description': license_data['description'],
            'allowed_uses': license_data['allowed_uses'],
            'allowed_subfunctions': license_data.get('allowed_subfunctions', [0, 1, 2, 3, 4, 5]),
            'restrictions': license_data.get('restrictions', {}),
            'attribution': license_data.get('attribution', {
                'required': False,
                'format': None,
                'text': None,
                'url': None
            }),
            'schema_url': license_data.get('schema_url'),  # Optional Schema.org CreativeWork reference
            'pricing': license_data.get('pricing', {}),
            'effective_date': license_data.get('effective_date', now),
            'expiration_date': license_data.get('expiration_date'),
            'created_at': now,
            'updated_at': now,
            'created_by': username,
            'updated_by': username,
            'metadata': license_data.get('metadata', {})
        }
        
        # Convert all floats to Decimals for DynamoDB compatibility
        license_doc = convert_floats_to_decimals(license_doc)
        
        # Save to DynamoDB
        get_licenses_table().put_item(Item=license_doc)
        
        logger.info(f"Created license: {license_id} by {username}")
        
        return {
            'statusCode': 201,
            'headers': {
                'Content-Type': 'application/json',
                # CORS must be on the ACTUAL response, not only the OPTIONS preflight: without it
                # the browser discards a successful 201 and fetch() rejects as "NetworkError"
                # even though the licence was created — a false failure that invites duplicate
                # submissions. Resolved via the shared helper (ALLOWED_ORIGINS / SSM), never "*".
                **cors_headers(),
                'Location': f"{API_BASE_URL}/licenses/{license_id}"
            },
            'body': json.dumps(format_license_document(license_doc), default=str)
        }
    
    except Exception as e:
        logger.exception("Error creating license")
        return error_response(500, f'Failed to create license: {str(e)}')


def get_license(license_id: str) -> Dict[str, Any]:
    """
    Get a specific license document.
    PUBLIC endpoint - no authentication required.
    
    This is the licenseUrl referenced in CoMP responses.
    
    Args:
        license_id: License identifier
        
    Returns:
        License document in RSL format
    """
    
    try:
        # Query for the latest active version
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            FilterExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':lid': license_id,
                ':status': 'active'
            },
            ScanIndexForward=False,  # Get latest version first
            Limit=1
        )
        
        items = response.get('Items', [])
        
        if not items:
            return error_response(404, f'License not found: {license_id}')
        
        license_doc = items[0]
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Cache-Control': 'public, max-age=3600',
                # TM-02: public CoMP license-catalog document — explicit opt-in, config-driven.
                **cors_headers(public=True)
            },
            'body': json.dumps(format_license_document(license_doc), default=str)
        }
    
    except Exception as e:
        logger.exception(f"Error retrieving license: {license_id}")
        return error_response(500, f'Failed to retrieve license: {str(e)}')


def update_license(license_id: str, updates: Dict[str, Any], username: Optional[str]) -> Dict[str, Any]:
    """
    Update an existing license document.
    Creates a new version automatically.
    UI-Only endpoint - requires Cognito authentication.
    
    Args:
        license_id: License identifier
        updates: Updated fields
        username: Authenticated user identifier
        
    Returns:
        Updated license document
    """
    
    if not username:
        return error_response(401, 'Authentication required')
    
    try:
        # Get current version
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            ExpressionAttributeValues={':lid': license_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        items = response.get('Items', [])
        
        if not items:
            return error_response(404, f'License not found: {license_id}')
        
        current = items[0]
        new_version = current.get('version', 0) + 1
        
        # Create new version
        now = datetime.now(timezone.utc).isoformat()
        
        updated_doc = {
            **current,
            'version': new_version,
            'updated_at': now,
            'updated_by': username,
            **{k: v for k, v in updates.items() if k not in ['license_id', 'version', 'created_at', 'created_by']}
        }
        
        # Convert all floats to Decimals for DynamoDB compatibility
        updated_doc = convert_floats_to_decimals(updated_doc)
        
        # Save new version
        get_licenses_table().put_item(Item=updated_doc)
        
        logger.info(f"Updated license: {license_id} v{new_version} by {username}")
        
        return success_response(format_license_document(updated_doc))
    
    except Exception as e:
        logger.exception(f"Error updating license: {license_id}")
        return error_response(500, f'Failed to update license: {str(e)}')


def archive_license(license_id: str, username: Optional[str]) -> Dict[str, Any]:
    """
    Revoke a license document (SOFT revoke — version history preserved).
    UI-Only endpoint - requires Cognito authentication.

    Writes a NEW version of the license with ``status='revoked'`` rather than deleting rows, so the
    full authored history stays auditable (the previous behavior hard-deleted every version despite
    the function's name). A revoked license stops being served: ``get_license`` filters on
    ``status='active'``, and ``list_licenses`` only returns revoked documents when explicitly
    filtered with ``status=revoked``. Use :func:`renew_license` to bring one back to active.

    The function name is retained because ``comp_licensing`` re-exports it and callers/tests
    reference it; the 204-with-empty-body response shape is likewise unchanged so the
    ``openapi-spec.yaml`` contract for ``DELETE /licenses/{license_id}`` stays satisfied.

    Args:
        license_id: License identifier
        username: Authenticated user identifier

    Returns:
        Success confirmation (HTTP 204, empty body)
    """
    
    if not username:
        return error_response(401, 'Authentication required')
    
    try:
        # Get the latest version of the license
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            ExpressionAttributeValues={':lid': license_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        items = response.get('Items', [])
        
        if not items:
            return error_response(404, f'License not found: {license_id}')
        
        current = items[0]
        
        if current.get('status') == 'revoked':
            logger.info(f"License already revoked: {license_id}")
            return {
                'statusCode': 204,
                # Same CORS requirement as the 201 above — a 204 without it is discarded by the
                # browser and surfaces as a false NetworkError. Shared helper, never "*".
                'headers': {
                    'Content-Type': 'application/json',
                    **cors_headers(),
                },
                'body': ''
            }
        
        # Soft revoke: append a new version marked 'revoked'; prior versions are left intact.
        now = datetime.now(timezone.utc).isoformat()
        new_version = int(current.get('version', 0)) + 1
        
        revoked_doc = {
            **current,
            'version': new_version,
            'status': 'revoked',
            'revoked_at': now,
            'revoked_by': username,
            'updated_at': now,
            'updated_by': username
        }
        
        get_licenses_table().put_item(Item=convert_floats_to_decimals(revoked_doc))
        
        logger.info(f"Revoked license: {license_id} v{new_version} by {username}")
        
        return {
            'statusCode': 204,
            # Same CORS requirement as the 201 above — a 204 without it is discarded by the
            # browser and surfaces as a false NetworkError. Shared helper, never "*".
            'headers': {
                'Content-Type': 'application/json',
                **cors_headers(),
            },
            'body': ''
        }
    
    except Exception as e:
        logger.exception(f"Error revoking license: {license_id}")
        return error_response(500, f'Failed to revoke license: {str(e)}')


def renew_license(license_id: str, updates: Dict[str, Any], username: Optional[str]) -> Dict[str, Any]:
    """
    Renew a revoked license document, transitioning it back to ``active`` as a NEW version.
    UI-Only endpoint - requires Cognito authentication.

    The renewal is additive: the revoked version is preserved and a new version with
    ``status='active'`` is appended. ``updates`` may optionally carry ``expiration_date`` (and other
    mutable license fields) to extend the term; identity/history fields (``license_id``, ``version``,
    ``created_at``, ``created_by``, ``status``) are never taken from the caller.

    Behavior when the license is already active: HTTP **409 Conflict**. Renewal is defined as a
    revoked -> active transition, so renewing an active license is a client error rather than a
    silent no-op or an accidental term extension (use ``PUT /licenses/{license_id}`` to change terms
    on an active license). This is reflected in ``openapi-spec.yaml``.

    Args:
        license_id: License identifier
        updates: Optional fields to apply on renewal (e.g. ``expiration_date``)
        username: Authenticated user identifier

    Returns:
        The renewed (active) license document
    """
    
    if not username:
        return error_response(401, 'Authentication required')
    
    try:
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            ExpressionAttributeValues={':lid': license_id},
            ScanIndexForward=False,
            Limit=1
        )
        
        items = response.get('Items', [])
        
        if not items:
            return error_response(404, f'License not found: {license_id}')
        
        current = items[0]
        
        if current.get('status') == 'active':
            return error_response(
                409,
                f'License {license_id} is already active; nothing to renew',
                'LICENSE_ALREADY_ACTIVE'
            )
        
        now = datetime.now(timezone.utc).isoformat()
        new_version = int(current.get('version', 0)) + 1
        
        protected_fields = {'license_id', 'version', 'created_at', 'created_by', 'status'}
        applied_updates = {k: v for k, v in (updates or {}).items() if k not in protected_fields}
        
        renewed_doc = {
            **current,
            'version': new_version,
            'status': 'active',
            'renewed_at': now,
            'renewed_by': username,
            'updated_at': now,
            'updated_by': username,
            **applied_updates
        }
        # The revocation stamps belong to the revoked version only.
        renewed_doc.pop('revoked_at', None)
        renewed_doc.pop('revoked_by', None)
        
        renewed_doc = convert_floats_to_decimals(renewed_doc)
        
        get_licenses_table().put_item(Item=renewed_doc)
        
        logger.info(f"Renewed license: {license_id} v{new_version} by {username}")
        
        return success_response(format_license_document(renewed_doc))
    
    except Exception as e:
        logger.exception(f"Error renewing license: {license_id}")
        return error_response(500, f'Failed to renew license: {str(e)}')


def get_license_versions(license_id: str, username: Optional[str]) -> Dict[str, Any]:
    """
    Get version history for a license document.
    UI-Only endpoint - requires Cognito authentication.
    
    Args:
        license_id: License identifier
        username: Authenticated user identifier
        
    Returns:
        List of all versions with diffs
    """
    
    if not username:
        return error_response(401, 'Authentication required')
    
    try:
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            ExpressionAttributeValues={':lid': license_id},
            ScanIndexForward=False  # Latest first
        )
        
        versions = response.get('Items', [])
        
        if not versions:
            return error_response(404, f'License not found: {license_id}')
        
        result = {
            'license_id': license_id,
            'versions': [format_version_summary(v) for v in versions],
            'total_versions': len(versions)
        }
        
        return success_response(result)
    
    except Exception as e:
        logger.exception(f"Error retrieving version history: {license_id}")
        return error_response(500, f'Failed to retrieve version history: {str(e)}')


def validate_license(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a CoMP request against a license document.
    Public endpoint - no authentication required (for partner validation).
    
    Accepts two formats:
    1. CoMP format: {"license_id": "...", "aisysuse": {"function": [...], "subfn": [...]}}
    2. Simplified format: {"license_id": "...", "intended_use": "...", "content_types": [...]}
    
    Args:
        request_data: Validation request with license_id and usage details
        
    Returns:
        Validation result with allowed/denied and details
    """
    
    try:
        license_id = request_data.get('license_id')
        
        if not license_id:
            return error_response(400, 'Missing license_id')
        
        # Support both CoMP format (aisysuse) and simplified format
        aisysuse = request_data.get('aisysuse', {})
        
        # Map simplified format to CoMP format if needed
        if not aisysuse:
            intended_use = request_data.get('intended_use')
            
            if intended_use:
                # Use the global mapping constants
                use_lower = intended_use.lower()
                if use_lower not in USE_CASE_MAPPING:
                    return error_response(400, 
                        f'Invalid intended_use: "{intended_use}". '
                        f'Valid values: {", ".join(sorted(USE_CASE_MAPPING.keys()))}')
                
                aisysuse = {
                    'function': [USE_CASE_MAPPING[use_lower]],
                    'subfn': [0]  # Default subfunction
                }
            else:
                return error_response(400, 'Missing usage information (aisysuse or intended_use)')
        
        # Get license
        response = get_licenses_table().query(
            KeyConditionExpression='license_id = :lid',
            FilterExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':lid': license_id,
                ':status': 'active'
            },
            ScanIndexForward=False,
            Limit=1
        )
        
        items = response.get('Items', [])
        
        if not items:
            return error_response(404, f'License not found or inactive: {license_id}')
        
        license_doc = items[0]
        
        # Validate functions
        requested_functions = aisysuse.get('function', [])
        allowed_uses = license_doc.get('allowed_uses', [])
        
        # Convert Decimals to ints for comparison
        allowed_uses = [int(u) for u in allowed_uses]
        
        denied_functions = [f for f in requested_functions if f not in allowed_uses]
        
        if denied_functions:
            # Create human-readable error message
            denied_names = [f"{f} ({COMP_FUNCTION_NAMES.get(f, 'Unknown')})" for f in denied_functions]
            allowed_names = [f"{u} ({COMP_FUNCTION_NAMES.get(u, 'Unknown')})" for u in allowed_uses]
            
            return success_response({
                'valid': False,
                'license_id': license_id,
                'denied_functions': denied_functions,
                'denied_function_names': [COMP_FUNCTION_NAMES.get(f, 'Unknown') for f in denied_functions],
                'allowed_uses': allowed_uses,
                'allowed_use_names': [COMP_FUNCTION_NAMES.get(u, 'Unknown') for u in allowed_uses],
                'message': f'Requested use {", ".join(denied_names)} not permitted. License allows: {", ".join(allowed_names)}'
            })
        
        # Validate subfunctions
        requested_subfunctions = aisysuse.get('subfn', [])
        allowed_subfunctions = license_doc.get('allowed_subfunctions', [])
        
        # Convert Decimals to ints for comparison
        allowed_subfunctions = [int(sf) for sf in allowed_subfunctions]
        
        denied_subfunctions = [sf for sf in requested_subfunctions if sf not in allowed_subfunctions]
        
        if denied_subfunctions:
            return success_response({
                'valid': False,
                'license_id': license_id,
                'denied_subfunctions': denied_subfunctions,
                'allowed_subfunctions': allowed_subfunctions,
                'message': f'Subfunction(s) {denied_subfunctions} not permitted under license {license_id}'
            })
        
        # Check expiration
        expiration_date = license_doc.get('expiration_date')
        if expiration_date:
            exp_dt = datetime.fromisoformat(expiration_date.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) > exp_dt:
                return success_response({
                    'valid': False,
                    'license_id': license_id,
                    'message': f'License {license_id} has expired'
                })
        
        # All validations passed - return success with human-readable names
        return success_response({
            'valid': True,
            'license_id': license_id,
            'allowed_uses': allowed_uses,
            'allowed_use_names': [COMP_FUNCTION_NAMES.get(u, 'Unknown') for u in allowed_uses],
            'allowed_subfunctions': allowed_subfunctions,
            'attribution': license_doc.get('attribution', {}),
            'restrictions': license_doc.get('restrictions', {}),
            'message': 'License validation successful'
        })
    
    except Exception as e:
        logger.exception("Error validating license")
        return error_response(500, f'Failed to validate license: {str(e)}')


# Helper functions

def format_license_document(license_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format a license document for API response."""
    
    return decimal_to_float({
        'license_id': license_doc['license_id'],
        'version': license_doc.get('version', 1),
        'status': license_doc.get('status', 'draft'),
        'title': license_doc['title'],
        'description': license_doc['description'],
        'allowed_uses': license_doc.get('allowed_uses', []),
        'allowed_subfunctions': license_doc.get('allowed_subfunctions', []),
        'restrictions': license_doc.get('restrictions', {}),
        'attribution': license_doc.get('attribution', {
            'required': False,
            'format': None,
            'text': None,
            'url': None
        }),
        'schema_url': license_doc.get('schema_url'),
        'pricing': license_doc.get('pricing', {}),
        'effective_date': license_doc.get('effective_date'),
        'expiration_date': license_doc.get('expiration_date'),
        'license_url': f"{API_BASE_URL}/licenses/{license_doc['license_id']}",
        'created_at': license_doc.get('created_at'),
        'updated_at': license_doc.get('updated_at'),
        'metadata': license_doc.get('metadata', {})
    })


def format_license_summary(license_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format a license summary for list responses."""
    
    return decimal_to_float({
        'license_id': license_doc['license_id'],
        'version': license_doc.get('version', 1),
        'status': license_doc.get('status', 'draft'),
        'title': license_doc['title'],
        'description': license_doc['description'],
        'allowed_uses': license_doc.get('allowed_uses', []),
        'attribution': license_doc.get('attribution', {}).get('required', False),
        'license_url': f"{API_BASE_URL}/licenses/{license_doc['license_id']}",
        'updated_at': license_doc.get('updated_at')
    })


def format_version_summary(version: Dict[str, Any]) -> Dict[str, Any]:
    """Format a version summary for version history."""
    
    return decimal_to_float({
        'version': version.get('version', 1),
        'status': version.get('status', 'draft'),
        'updated_at': version.get('updated_at'),
        'updated_by': version.get('updated_by'),
        'changes': version.get('changes', 'No change log available')
    })


def validate_attribution(attribution: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate attribution field structure.
    
    Args:
        attribution: Attribution configuration object
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    
    if not isinstance(attribution, dict):
        return False, "Attribution must be an object"
    
    required = attribution.get('required', False)
    
    if required:
        attr_format = attribution.get('format')
        
        if not attr_format:
            return False, "Attribution format is required when attribution is required"
        
        valid_formats = ['text', 'html', 'link', 'schema.org']
        if attr_format not in valid_formats:
            return False, f"Invalid attribution format. Must be one of: {', '.join(valid_formats)}"
        
        # If format is 'text' or 'html', text field should be present
        if attr_format in ['text', 'html']:
            if not attribution.get('text'):
                return False, f"Attribution text is required for format '{attr_format}'"
        
        # If format is 'link', url field is required
        if attr_format == 'link':
            if not attribution.get('url'):
                return False, "Attribution URL is required for format 'link'"
    
    return True, None


def get_example_licenses() -> List[Dict[str, Any]]:
    """
    Get example license configurations for documentation and testing.
    
    Returns:
        List of example license documents
    """
    
    return [
        {
            "license_id": "lic_inference_with_attribution_v1",
            "title": "Inference with Attribution",
            "description": "Permits inference and RAG with required text attribution",
            "allowed_uses": [3],
            "allowed_subfunctions": [0, 1, 3, 5],
            "attribution": {
                "required": True,
                "format": "text",
                "text": "Content provided by {publisher_name}. Visit {content_url} for details.",
                "url": "https://publisher.com/attribution-guidelines"
            },
            "restrictions": {
                "max_queries_per_day": 10000,
                "commercial_use": True
            }
        },
        {
            "license_id": "lic_training_schema_org_v1",
            "title": "Training with Schema.org Attribution",
            "description": "Training data license with Schema.org metadata reference",
            "allowed_uses": [2],
            "allowed_subfunctions": [0],
            "attribution": {
                "required": True,
                "format": "schema.org",
                "text": None,
                "url": None
            },
            "schema_url": "https://publisher.com/schema/training-dataset.json",
            "restrictions": {
                "max_documents": 1000000,
                "commercial_use": False,
                "research_only": True
            }
        },
        {
            "license_id": "lic_no_attribution_paid_v1",
            "title": "Commercial License - No Attribution Required",
            "description": "Paid commercial license without attribution requirement",
            "allowed_uses": [1, 3, 4],
            "allowed_subfunctions": [0, 1, 2, 3, 4, 5],
            "attribution": {
                "required": False,
                "format": None,
                "text": None,
                "url": None
            },
            "restrictions": {
                "commercial_use": True
            },
            "pricing": {
                "model": "subscription",
                "base_rate": 1000.00,
                "currency": "USD",
                "billing_period": "monthly"
            }
        }
    ]


def success_response(data: Any, status_code: int = 200) -> Dict[str, Any]:
    """Generate a successful API response."""
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            **cors_headers()
        },
        'body': json.dumps(decimal_to_float(data), default=str)
    }


def error_response(status_code: int, message: str, error_code: Optional[str] = None) -> Dict[str, Any]:
    """Generate an error API response."""
    
    error_body = {
        'error': {
            'code': error_code or f'ERROR_{status_code}',
            'message': message,
            'status': status_code
        }
    }
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            **cors_headers()
        },
        'body': json.dumps(error_body)
    }
