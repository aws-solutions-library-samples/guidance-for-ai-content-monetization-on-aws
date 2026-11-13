"""Partner-plane operation registry shared by the Lambda, the CDK derivation and the tests.

The partner API is the subset of ``openapi-spec.yaml`` operations tagged ``x-audience: partner``
that are either public or secured by the native API Gateway key. This module is the single place
that names those operations so that:

* the Lambda can map an incoming ``(method, resource)`` to its ``operationId`` and enforce a
  partner's ``allowed_endpoints`` (design D3, grill Q5);
* the CDK builder can exclude the not-yet-backed operations (design D4);
* the partner lifecycle API can validate ``allowed_endpoints`` entries on write (Req 3.3).

``tests/auth/test_partner_operations.py`` derives the same sets from the contract and fails if
this file drifts from it.
"""

from typing import Dict, FrozenSet, Optional, Tuple

Operation = Tuple[str, str]  # (HTTP method upper-case, templated resource path)

# Operations that require a native API key. Only these may appear in ``allowed_endpoints``.
KEY_GATED_OPERATIONS: Dict[Operation, str] = {
    ("POST", "/query"): "queryContent",
    ("POST", "/log"): "submitLog",
    ("POST", "/licenses/{license_id}/validate"): "validateLicense",
    # URL-addressable content retrieval (#14 REQ-1.2). Key-gated: the agent fetching a
    # document by canonical URL is a billable consumer, so it must present a key.
    ("GET", "/content"): "listContentOrGetByUrl",
}

# Operations exposed on the partner API without a key requirement, including contract operations
# that admit an anonymous requirement ("key optional" cannot be expressed with a native key, so
# they are public). They are identified-if-keyed but never gated, and never valid
# ``allowed_endpoints`` entries.
PUBLIC_OPERATIONS: Dict[Operation, str] = {
    ("GET", "/info"): "getApiInfo",
    ("GET", "/taxonomy"): "getIABTaxonomy",
    ("GET", "/llms.txt"): "getLlmsTxt",
    ("GET", "/.well-known/comp.json"): "getCompDiscovery",
    ("GET", "/licenses/{license_id}"): "getLicense",
}

# In the derived set but without a backing handler yet (owned by the licensing work). Excluded
# from the partner API and from the served partner spec until a handler exists (design D4).
DEFERRED_NO_HANDLER: FrozenSet[Operation] = frozenset(
    {
        ("GET", "/.well-known/comp.json"),
        ("GET", "/licenses/{license_id}"),
        ("POST", "/licenses/{license_id}/validate"),
    }
)

KEY_GATED_OPERATION_IDS: FrozenSet[str] = frozenset(KEY_GATED_OPERATIONS.values())

_ALL_OPERATIONS: Dict[Operation, str] = {**KEY_GATED_OPERATIONS, **PUBLIC_OPERATIONS}


def operation_id_for(method: Optional[str], resource: Optional[str]) -> Optional[str]:
    """Return the partner-plane ``operationId`` for an API Gateway ``(httpMethod, resource)``.

    ``resource`` is the templated path API Gateway places on the event (``/licenses/{license_id}``),
    which is what the contract keys on. Returns ``None`` for anything not on the partner plane.
    """
    if not method or not resource:
        return None
    return _ALL_OPERATIONS.get((method.upper(), resource))


def is_key_gated(operation_id: Optional[str]) -> bool:
    return operation_id in KEY_GATED_OPERATION_IDS
