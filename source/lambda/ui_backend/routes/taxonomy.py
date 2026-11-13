"""Taxonomy domain route module (thin adapter).

Serves ``GET /taxonomy/categories`` for the ui_backend BFF. This module is intentionally
thin (Requirements 1.3, 4.3): it applies Cognito auth via the unchanged
``@require_cognito_auth`` wrapper, delegates the taxonomy data lookup to
``services.taxonomy_manager.TaxonomyManager`` (the single home for taxonomy data +
data-store access), and formats the UI dropdown payload with the shared
``create_success_response`` / ``create_error_response`` helpers.

It is a behavior-preserving replacement for the inline
``ui_backend/handler.py:handle_taxonomy_categories`` (Requirement 2): the same
``@validate_response("/taxonomy/categories", "GET")`` + ``@require_cognito_auth`` decorator
stack, the same success/error responses, the same UI dropdown payload shape, the same
hardcoded taxonomy fallback used when the real taxonomy lookup fails, and the same 500
``"Failed to retrieve taxonomy categories: ..."`` error message on failure.

This module is created ahead of the dispatcher flip (task 3.3); the dispatcher continues
to route ``/taxonomy/categories`` to the inline handler until that flip lands (Requirement
16.4). All imports are absolute, rooted at the canonical import root ``source/lambda/``
(Requirement 15.1), declared at module top level with no ``sys.path`` manipulation
(Requirements 5.1, 15.1, 15.2).
"""

import logging
from typing import Any, Dict

from services.taxonomy_manager import TaxonomyManager
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)


@validate_response("/taxonomy/categories", "GET")
@require_cognito_auth
def _handle_taxonomy_categories(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Get category ID to name mappings for UI dropdowns and forms."""
    try:
        user = kwargs["authenticated_user"]

        # Try to get real taxonomy data
        try:
            # Get taxonomy manager
            taxonomy_manager = TaxonomyManager()

            # Get all categories for UI consumption
            categories = taxonomy_manager.get_all_categories()

            # Format for UI dropdowns
            category_options = []
            category_mappings = {}

            for category_id, category_info in categories.items():
                category_options.append(
                    {
                        "value": str(category_id),
                        "label": category_info["name"],
                        "full_path": category_info.get("full_path", category_info["name"]),
                        "tier_level": category_info.get("tier_level", 1),
                    }
                )
                category_mappings[str(category_id)] = category_info["name"]

            response_data = {
                "categories": category_options,
                "mappings": category_mappings,
                "total_count": len(categories),
            }

            return create_success_response(response_data)

        except Exception as e:
            logger.error(f"Real taxonomy data failed: {str(e)}")

            # Fallback to hardcoded taxonomy data
            logger.warning("Falling back to hardcoded taxonomy data")

            fallback_categories = [
                {"value": "1", "label": "Automotive", "full_path": "Automotive", "tier_level": 1},
                {"value": "52", "label": "Business and Finance", "full_path": "Business and Finance", "tier_level": 1},
                {"value": "111", "label": "Medical Health", "full_path": "Medical Health", "tier_level": 1},
                {
                    "value": "126",
                    "label": "Technology & Computing",
                    "full_path": "Technology & Computing",
                    "tier_level": 1,
                },
                {"value": "150", "label": "Attractions", "full_path": "Attractions", "tier_level": 1},
            ]

            fallback_mappings = {
                "1": "Automotive",
                "52": "Business and Finance",
                "111": "Medical Health",
                "126": "Technology & Computing",
                "150": "Attractions",
            }

            response_data = {
                "categories": fallback_categories,
                "mappings": fallback_mappings,
                "total_count": len(fallback_categories),
            }

            return create_success_response(response_data)

    except Exception as e:
        logger.error(f"Error in handle_taxonomy_categories: {str(e)}")
        return create_error_response(500, f"Failed to retrieve taxonomy categories: {str(e)}")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Uniform dispatch entry for the taxonomy domain.

    Mirrors the interim ``_route_taxonomy`` adapter in ``handler.py``: it forwards the
    query-string parameters as the ``request_data`` argument (the taxonomy categories
    endpoint takes no request body), preserving the pre-refactor call shape.
    """
    headers = event.get("headers") or {}
    query_params = event.get("queryStringParameters") or {}
    return _handle_taxonomy_categories(headers, query_params, event)
