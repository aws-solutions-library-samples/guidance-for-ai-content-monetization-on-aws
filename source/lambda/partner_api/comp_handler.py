"""
CoMP v1.0 handling — Vestigial_Shim (re-export only).

The CoMP (IAB Content Monetization Protocol) v1.0 domain logic that previously lived here —
request detection, license validation (including all ``cm-licenses`` Licenses_Table access),
and Package-response building — has been extracted into the Service_Layer single home
``services/comp_query_service.py`` (Requirement 18.1 / 18.6). That service is now the single source
of truth for the CoMP surface.

This module is retained only as a thin re-export so any external importer of
``partner_api.comp_handler`` continues to resolve; it contains **zero CoMP domain-logic statements
and zero Licenses_Table access statements**. The partner_api handler no longer imports from here —
it delegates directly to ``services.comp_query_service`` (Requirement 18.3). Prefer importing from
``services.comp_query_service`` in new code.

The re-export is an absolute import rooted at the Canonical_Import_Root ``source/lambda/`` with no
``sys.path`` manipulation (Requirement 15.1 / 15.2 / 15.3).
"""

from services.comp_query_service import (  # noqa: F401  (re-export for import compatibility)
    COMP_FUNCTION_CODES,
    COMP_FUNCTION_NAMES,
    COMP_SUB_FUNCTION_CODES,
    CompRequestValidationError,
    build_comp_discovery_request,
    build_comp_package_response,
    build_comp_text_asset,
    get_license_from_db,
    get_licenses_table,
    handle_comp_query_request,
    is_comp_request,
    validate_comp_license,
    validate_comp_request,
)

__all__ = [
    "COMP_FUNCTION_CODES",
    "COMP_FUNCTION_NAMES",
    "COMP_SUB_FUNCTION_CODES",
    "CompRequestValidationError",
    "build_comp_discovery_request",
    "build_comp_package_response",
    "build_comp_text_asset",
    "get_license_from_db",
    "get_licenses_table",
    "handle_comp_query_request",
    "is_comp_request",
    "validate_comp_license",
    "validate_comp_request",
]
