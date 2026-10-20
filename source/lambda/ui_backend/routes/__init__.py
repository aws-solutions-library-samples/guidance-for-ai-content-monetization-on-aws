"""
UI Backend route resolution package (thin-dispatcher Route_Table).

This package backs the thin ``handler.lambda_handler`` dispatcher. ``resolve_route``
maps an incoming ``(path, method)`` to exactly one route name using
longest-matching-prefix + method-set matching (Requirement 3.2). The dispatcher in
``handler.py`` owns the cross-cutting concerns (CORS preflight, JSON body guard,
404/500 mapping, structured logging) and translates the resolved route name into the
handler that serves that domain.

During the strangler-fig migration (Requirement 16) every route name initially resolves
to the existing inline handler in ``handler.py`` so behavior is unchanged. As each domain
is migrated, its dispatch entry is flipped to a dedicated per-domain module added to this
package (``routes/analytics.py``, ``routes/pricing.py``, ...), each exposing a uniform
``route(event, method, path)`` entry.

Import form note: ``handler.py`` is packaged at the deployment zip root, so this package is
imported as ``from routes import ...`` (the zip-root layout), matching how the UI Backend
zip is assembled. No ``sys.path`` manipulation is used to resolve it (Requirement 15.2/15.3).
"""

from dataclasses import dataclass
from typing import FrozenSet, Optional

# Sentinel for a Route_Table entry that matches any HTTP method. Used by the broad
# domain prefixes (analytics, pricing, bot-control, content, analytics-agent) whose own
# sub-router handles method dispatch, matching the pre-refactor monolith behavior.
ALL_METHODS: Optional[FrozenSet[str]] = None

# Meta routes serve/observe the API itself rather than being part of the documented business
# surface (e.g. /openapi serves the contract). They are intentionally NOT contract paths, so the
# reachability guardrail (Direction B) exempts them from "every route must map to a contract path".
META_ROUTE_NAMES: FrozenSet[str] = frozenset({"openapi"})


@dataclass(frozen=True)
class Route:
    """A single Route_Table entry.

    Attributes:
        name: Stable identifier the dispatcher maps to a handler.
        prefix: The path prefix (or exact path when ``exact`` is True) to match.
        exact: When True the request path must equal ``prefix``; otherwise the path
            must start with ``prefix``.
        methods: The set of HTTP methods this entry accepts, or ``ALL_METHODS`` (None)
            to accept any method.
    """

    name: str
    prefix: str
    exact: bool
    methods: Optional[FrozenSet[str]]


# Ordered Route_Table.
#
# ``/analytics-agent/`` is listed before ``/analytics/`` so the agent routes are never
# swallowed by the analytics prefix (Requirement 3.3). ``resolve_route`` selects the entry
# with the longest matching prefix, so specific/static routes take precedence over broad
# prefixes regardless of table order; the ordering here mirrors the monolith for clarity.
#
# The ``/content`` entry intentionally uses a startswith match (no trailing slash) to match
# ``/content``, ``/content/{id}``, and ``/content/bulk`` exactly as the monolith did; the
# ``/content/bulk`` vs ``/content/{id}`` disambiguation is performed inside the content
# handler (and, post-migration, inside ``routes/content.py``).
ROUTE_TABLE = (
    Route("analytics_agent", "/analytics-agent/", False, ALL_METHODS),
    Route("analytics", "/analytics/", False, ALL_METHODS),
    # Partner lifecycle: list/create on the collection, get/update/delete on a partner, and the
    # key sub-resource; routes/partners.py dispatches on method + path shape.
    Route("partners", "/partners", False, ALL_METHODS),
    Route("pricing", "/pricing/", False, ALL_METHODS),
    Route("bot_control", "/bot-control/", False, ALL_METHODS),
    Route("llms_txt", "/content/llms-txt", False, ALL_METHODS),
    Route("content", "/content", False, ALL_METHODS),
    # Connections API (integration-adapters #35): internal (Cognito) only. A single startswith
    # prefix covers /connections, /connections/adapters, /connections/{id}(/test|/status), and
    # /connections/mirrors/{domain}; the connections route module handles method+path dispatch.
    Route("connections", "/connections", False, ALL_METHODS),
    Route("processing", "/processing/status", True, frozenset({"GET"})),
    Route("taxonomy", "/taxonomy/categories", True, frozenset({"GET"})),
    Route("log", "/log", True, frozenset({"POST"})),
    # Meta route (#31): serves the OpenAPI contract itself to the in-app API reference page
    # (full, or ?audience=partner via the shared filter). It documents the API rather than being
    # part of it, so it is intentionally NOT a contract path — the reachability guardrail exempts
    # it via META_ROUTE_NAMES.
    Route("openapi", "/openapi", True, frozenset({"GET"})),
)


def _matches(route: Route, path: str, method: str) -> bool:
    """Return True if ``route`` matches the given path and method."""
    if route.exact:
        if path != route.prefix:
            return False
    elif not path.startswith(route.prefix):
        return False

    if route.methods is not None and method not in route.methods:
        return False

    return True


def resolve_route(path: str, method: str) -> Optional[str]:
    """Resolve ``(path, method)`` to a single route name.

    Selects the Route_Table entry whose configured prefix is the longest matching
    prefix of ``path`` and whose method set includes ``method`` (Requirement 3.2).
    Ties on prefix length are broken by Route_Table order (first match wins).

    Returns:
        The matched route name, or ``None`` when no entry matches (the dispatcher maps
        ``None`` to a 404 ``Endpoint not found`` response, Requirement 6.3).
    """
    best: Optional[Route] = None
    best_len = -1
    for route in ROUTE_TABLE:
        if _matches(route, path, method) and len(route.prefix) > best_len:
            best = route
            best_len = len(route.prefix)
    return best.name if best is not None else None
