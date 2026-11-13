"""
CoMP v1.0 & RSL License Management — Vestigial_Shim (re-export only).

The RSL (Really Simple Licensing) license domain logic that previously lived here — license
CRUD/versioning/validation, CoMP v1.0 discovery-document (``comp.json``) generation, and every
``cm-licenses`` Licenses_Table access — has been extracted into the Service_Layer single home
``services/license_service.py`` (Requirement 19.1). That service is now the single source of truth
for the RSL/CoMP-discovery surface.

This module was VERIFIED unwired before its ``lambda_handler`` was retired (task 16.2, Requirement
19.2): a grep of ``infrastructure/**/*.py`` and ``deployment/**/*.py`` finds **no**
``handler="comp_licensing.lambda_handler"`` and **no** API Gateway route to ``/licenses`` or
``/.well-known/comp.json``, and nothing in the repo imports it. It is dormant residue analogous to
``analytics_api.py``, so — consistent with Requirement 19.3/19.5 — its ``lambda_handler`` is retired
with **no CDK change**.

Following the same treatment applied to the sibling CoMP module ``partner_api/comp_handler.py``
(task 14.5), this file is retained only as a thin re-export so any future consumer of
``comp_licensing`` (e.g. the optional ``tests/comp_licensing/`` module-surface suite) continues to
resolve. It contains **zero license domain-logic statements and zero Licenses_Table access
statements**. Prefer importing from ``services.license_service`` in new code.

The re-export is an absolute import rooted at the Canonical_Import_Root ``source/lambda/`` with no
``sys.path`` manipulation (Requirement 15.1 / 15.2 / 15.3).

IAB CoMP v1.0: https://github.com/IABTechLab/CoMP/blob/main/CoMP-1.0.md
RSL Specification: https://github.com/IABTechLab/RSL
"""

from services.license_service import (  # noqa: F401  (re-export for import compatibility)
    API_BASE_URL,
    COMP_FUNCTION_CODES,
    MCP_SERVER_AVAILABLE,
    SELLER_DOMAIN,
    USE_CASE_MAPPING,
    archive_license,
    convert_floats_to_decimals,
    create_license,
    decimal_to_float,
    error_response,
    format_license_document,
    format_license_summary,
    format_version_summary,
    get_comp_discovery,
    get_example_licenses,
    get_license,
    get_license_versions,
    get_licenses_table,
    list_licenses,
    success_response,
    update_license,
    validate_attribution,
    validate_license,
)

__all__ = [
    "API_BASE_URL",
    "COMP_FUNCTION_CODES",
    "MCP_SERVER_AVAILABLE",
    "SELLER_DOMAIN",
    "USE_CASE_MAPPING",
    "archive_license",
    "convert_floats_to_decimals",
    "create_license",
    "decimal_to_float",
    "error_response",
    "format_license_document",
    "format_license_summary",
    "format_version_summary",
    "get_comp_discovery",
    "get_example_licenses",
    "get_license",
    "get_license_versions",
    "get_licenses_table",
    "list_licenses",
    "success_response",
    "update_license",
    "validate_attribution",
    "validate_license",
]
