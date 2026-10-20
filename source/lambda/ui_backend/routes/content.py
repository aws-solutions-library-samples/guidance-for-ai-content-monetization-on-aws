"""
Content domain Route_Module (``/content*``).

Thin per-domain adapter for the ui_backend content domain. Exposes the uniform
``route(event, method, path)`` entry (Requirement 4.2) and delegates to
``services.content_service.ContentService`` — the Service_Layer single home for content
logic (Requirement 4.3). The route performs no domain logic and no data-store access
itself (Requirement 1.3 / 1.4); disambiguation of ``/content``, ``/content/{id}``, and
``/content/bulk``, along with Cognito auth, is handled inside the content service (now the
single home for the content domain's logic), preserving behavior exactly (Requirement 2).

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``try/except ImportError`` shim (Requirement
5.1 / 5.2) and no ``sys.path`` manipulation (Requirement 15.2). This module is imported by
the thin dispatcher as ``from routes.content import route`` under the zip-root layout;
the dispatcher flip to this module is a separate task and is not performed here.
"""

from typing import Any, Dict

from services.content_service import ContentService

# Stateless service; instantiated once at module load so import failures surface at load
# time rather than per request (Requirement 5.4).
_content_service = ContentService()


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch a ``/content*`` request to the content service.

    Args:
        event: The API Gateway ``aws_proxy`` event.
        method: The HTTP method (accepted for the uniform route signature; the content
            service resolves method + path internally).
        path: The request path (accepted for the uniform route signature).

    Returns:
        The API Gateway proxy response produced by the content domain.
    """
    return _content_service.handle_request(event, None)
