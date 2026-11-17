"""Per-item read references for ``POST /query`` results.

Every discovery result carries a ``url`` the caller can follow with the credential that admitted
the query, on the plane that served it (design ``query-read-references`` §3):

* partner plane, item has a canonical URL: ``GET <base>/content?url=<canonical_url>``
* partner plane, no canonical URL: ``GET <base>/content?content_id=<content_id>``
* internal plane (Cognito): ``GET <base>/content/<content_id>``

The builder is a pure function of the discovery projection's ``content_id`` and ``canonical_url``
plus trusted request context. It reads no table, and it never takes the origin from the
viewer-supplied ``Host`` header: the partner plane uses its published base URL, and both planes
fall back to the ``domainName`` and ``stage`` API Gateway sets on the request context.
"""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote, urlencode

from shared.utils.partner_api_url import configured_partner_api_base_url

Plane = Literal["partner", "internal"]

# ``auth_type`` values set by ``require_partner_or_cognito_auth``. ``cognito`` comes from API
# Gateway authorizer claims, which only the internal plane's Cognito authorizer produces.
# ``partner_key`` and ``jwt`` only reach the handler behind the partner plane's gateway key check.
_PLANE_BY_AUTH_TYPE: Dict[str, Plane] = {
    "cognito": "internal",
    "partner_key": "partner",
    "jwt": "partner",
}

# RFC 6570 level-1 templates advertised in CoMP ``Package.retrieval.ext``.
_URL_TEMPLATE = "{canonical_url}"
_ID_TEMPLATE = "{content_id}"


class ReadReferenceError(ValueError):
    """The request context cannot produce a trustworthy read reference."""


@dataclass(frozen=True)
class ReadContext:
    """Where, and on which plane, the caller can read the items a query returned."""

    plane: Plane
    base_url: str  # absolute, no trailing slash


def plane_for(auth_type: str) -> Plane:
    """Return the plane a request admitted as ``auth_type`` arrived on; unknown values raise."""
    plane = _PLANE_BY_AUTH_TYPE.get(auth_type)
    if plane is None:
        raise ReadReferenceError(f"no read plane for auth_type {auth_type!r}")
    return plane


def read_context(event: Dict[str, Any], auth_type: str) -> ReadContext:
    """Return the read context for a ``/query`` request admitted as ``auth_type``.

    Raises ``ReadReferenceError`` for an unknown ``auth_type`` or when no trusted origin is
    available: a guessed origin would hand the caller a link it cannot follow.
    """
    plane = plane_for(auth_type)
    base_url = configured_partner_api_base_url() if plane == "partner" else None
    base_url = base_url or _request_context_base_url(event)
    if not base_url:
        raise ReadReferenceError("request context carries no domainName")
    return ReadContext(plane=plane, base_url=base_url)


def read_reference(ctx: ReadContext, content_id: str, canonical_url: Optional[str]) -> str:
    """Return the absolute read reference for one discovery result."""
    if not content_id:
        raise ReadReferenceError("a read reference needs a content_id")
    if ctx.plane == "internal":
        return f"{ctx.base_url}/content/{quote(content_id, safe='')}"
    if canonical_url:
        return f"{ctx.base_url}/content?{urlencode({'url': canonical_url})}"
    return f"{ctx.base_url}/content?{urlencode({'content_id': content_id})}"


def retrieval_object(ctx: ReadContext) -> Dict[str, Any]:
    """Return the CoMP ``Package.retrieval`` object describing the read contract for ``ctx``.

    ``auth`` is CoMP's Authorization Type: 1 (api_key) on the partner plane, 2 (oauth2, Cognito)
    on the internal plane. ``type`` 2 is API. The per-item ``ext.url`` on each Text asset is the
    authoritative link; the templates here only document the forms.
    """
    endpoint = f"{ctx.base_url}/content"
    if ctx.plane == "internal":
        return {
            "auth": 2,
            "endpoint": endpoint,
            "type": [2],
            "ext": {"read_by_id": f"{endpoint}/{_ID_TEMPLATE}"},
        }
    return {
        "auth": 1,
        "endpoint": endpoint,
        "type": [2],
        "ext": {
            "read_by_url": f"{endpoint}?url={_URL_TEMPLATE}",
            "read_by_id": f"{endpoint}?content_id={_ID_TEMPLATE}",
        },
    }


def _request_context_base_url(event: Dict[str, Any]) -> Optional[str]:
    request_context = (event or {}).get("requestContext") or {}
    domain = str(request_context.get("domainName") or "").strip()
    if not domain:
        return None
    stage = request_context.get("stage")
    suffix = f"/{stage}" if stage and stage != "$default" else ""
    return f"https://{domain}{suffix}"
