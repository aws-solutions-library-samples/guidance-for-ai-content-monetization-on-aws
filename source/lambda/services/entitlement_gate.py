"""Per-request entitlement gate (license-lifecycle-usage-events, GitLab #3, task 8.3).

The gate answers exactly one question per request: **is this partner authorized for this use of
this content, right now?** It is the *entitlement* half of CoMP authorization ("does this partner
hold that license?"). The *license terms* half — "does that license permit these CoMP function
codes?" — already lives in :func:`services.comp_query_service.validate_comp_license`. The two are
complementary: this module never reads ``cm-licenses`` (Requirement 7.4, the read-path
denormalization rule), because ``allowed_uses`` is denormalized onto the entitlement child item by
``services/entitlements_store.py``.

Position in the request flow (design.md B7, Requirement 10.1)::

    Path B   validate key -> [ENTITLEMENT GATE (uncached)] -> emit usage (async)
                                                          -> serve shared cached .md + ETag
    Path C   Path B's gate  +  x402 payment gate (owned by #25/#7)  -> emit usage

The two neighbouring steps are deliberately **not** implemented here and the seams are left clear:

* **Usage emit** — the ``emit()`` choke point is task 9.2 (``services/usage_emitter.py``, not yet
  built). The gate returns a decision and emits nothing; the caller emits *after* the gate allows.
* **x402 payment gate** — Path C's payment step is owned by **#25/#7**. It composes *after* this
  gate (Path C is Path B plus a payment step); this module has no payment branch and reads no
  payment signature into its decision.

Cache rules this module implements (``architecture-content-as-an-api.md``, "Cache rules — gate in
front of a shared object", Requirements 7.6 / 7.7 / 7.8 / 10.3)
---------------------------------------------------------------------------------------------
1. **Per-request and uncached.** :func:`authorize` recomputes the decision on every call. Nothing
   in this module memoizes a decision — not in module state, not on the store, not in an
   ``lru_cache``. See "What is and is not cached" below; a stale allow after a revoked entitlement
   is the failure mode this forecloses.
2. **Runs before the cache lookup, and does not vary the cached bytes.** The cache key is the
   brand-prefixed object path only. Credentials, license keys, and payment signatures are *read*
   by the gate, never keyed on. The decision is an authorization outcome, not a cache variant —
   :class:`EntitlementDecision` is structurally unusable as a cache-key contribution (see its
   docstring).
3. **Deny is uncached; allow serves the shared object.** :func:`deny_response` carries
   ``Cache-Control: no-store, no-cache, must-revalidate``. An allow contributes **no** headers
   (:func:`decision_headers` returns ``{}``), so the shared object keeps its own cacheability and
   one cached ``200`` is shared across all authorized callers.
4. **Obscurity is not a gate** (Requirement 7.7). There is no parameter, sentinel, or code path by
   which an opaque or unguessable ``object_path`` skips the check. ``object_path`` is carried for
   audit logging only and is never branched on.

What is and is not cached
-------------------------
*Not cached:* the authorization decision, the entitlement record it was derived from, and the
``allowed_uses`` codes. Every :func:`authorize` call performs a fresh DynamoDB read, so revoking
or deactivating an entitlement takes effect on the very next request.

*Cached:* only the boto3 table **resource** held lazily by the shared :class:`EntitlementsStore`
(``_default_store()``) — a connection, not a decision. It holds no entitlement data and no
authorization outcome, so it cannot serve a stale allow. Callers that want even that isolated can
pass their own ``store``.

Absolute imports rooted at the Canonical_Import_Root ``source/lambda/``, declared at module top
level: no ``sys.path`` manipulation and no ``try/except ImportError`` shim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from shared.utils.models import Entitlement
from shared.utils.response_helpers import create_error_response

from services.comp_query_service import COMP_FUNCTION_CODES, COMP_FUNCTION_NAMES
from services.entitlements_store import EntitlementsStore

logger = logging.getLogger(__name__)

# ── Canonical CoMP function-code semantics ───────────────────────────────────────────────────
#
# Codes come from the single canonical table owned by services/comp_query_service.py
# (0=all, 1=ai-all, 2=ai-train, 3=ai-input, 4=ai-index, 5=search). This module adds only the
# *broadening* relation between them, which no other module expresses.

#: ``0 = all`` — any automated processing. A superset of every other code.
COMP_FUNCTION_ALL = 0

#: ``1 = ai-all`` — any AI use. A superset of the AI codes only (see AI_USE_CODES).
COMP_FUNCTION_AI_ALL = 1

#: The AI use codes ``1 = ai-all`` broadens over: ai-train (2), ai-input (3), ai-index (4).
#:
#: ``5 = search`` is deliberately **excluded**. ``openapi-spec.yaml`` describes the contract's own
#: semantics for it as "traditional search index, NOT AI summaries" — i.e. the contract explicitly
#: distinguishes traditional search from AI use, so "any AI use" does not reach it. Treating
#: ``ai-all`` as covering ``search`` would silently widen a licence beyond what the publisher
#: authored, which is the same class of bug Requirement 1 fixed for the function-code table. The
#: gate fails closed: a partner who needs ``5`` must be entitled to ``5`` (or to ``0 = all``).
AI_USE_CODES: frozenset = frozenset({COMP_FUNCTION_AI_ALL, 2, 3, 4})

#: Cache directives for a denial. Requirement 7.8 / cache rule 3: deny is never cached.
NO_STORE_CACHE_CONTROL = "no-store, no-cache, must-revalidate"

#: Headers applied to a denial so no cache layer (CloudFront, browser, proxy) retains it.
DENY_HEADERS: Dict[str, str] = {
    "Cache-Control": NO_STORE_CACHE_CONTROL,
    "Pragma": "no-cache",
}


class DenialReason(str, Enum):
    """Why the gate denied. Stable machine-readable codes for logging and error payloads."""

    #: The request did not identify a partner, or declared no CoMP function code.
    INVALID_REQUEST = "INVALID_REQUEST"
    #: The partner holds no entitlement matching the request.
    NO_ENTITLEMENT = "NO_ENTITLEMENT"
    #: An entitlement exists but ``active`` is False (Requirement: inactive denies).
    ENTITLEMENT_INACTIVE = "ENTITLEMENT_INACTIVE"
    #: The entitlement exists and is active, but its ``allowed_uses`` do not cover the declared use.
    USE_NOT_AUTHORIZED = "USE_NOT_AUTHORIZED"


class CacheKeyContaminationError(TypeError):
    """Raised when an :class:`EntitlementDecision` is coerced towards a cache-key input.

    A :class:`TypeError` subclass so the failure surfaces the same way any other "wrong type for
    this operation" mistake does.
    """


@dataclass(frozen=True)
class EntitlementDecision:
    """The gate's output: an authorization decision, **not** a cache variant.

    Structurally incapable of contributing to a cache key (Requirement 10.3, cache rule 1). Three
    properties combine to make that a compile-/run-time fact rather than a convention:

    1. **It holds no credential material.** There is no field for a partner key, licence key,
       bearer token, or payment signature. The gate reads those upstream and never copies them
       into the decision, so a decision cannot leak one into a key even by accident.
       ``partner_id`` and ``license_id`` are identifiers, not credentials.
    2. **It is unhashable.** :meth:`__hash__` raises, so the decision cannot be a ``dict`` key, a
       ``set`` member, or an input to any ``hash()``-based key derivation.
    3. **It cannot be stringified into a key.** :meth:`__str__` raises, so ``str(decision)``,
       ``f"{decision}"``, ``"/".join([path, decision])`` and ``"%s" % decision`` all fail loudly
       instead of quietly appending an authorization outcome to a cache key. ``repr()`` is left
       intact (it carries no secrets) so logging and test failures stay debuggable — log the
       individual attributes, never the object with ``%s``.

    There is deliberately no ``to_cache_key()``, ``__bytes__``, ``__iter__``, or ``__index__``.
    """

    #: True when the partner is authorized for the declared use, right now.
    allowed: bool
    #: The authenticated partner the decision was computed for.
    partner_id: str
    #: The CoMP function codes the caller declared.
    declared_uses: Tuple[int, ...] = ()
    #: The entitlement's license that authorized the request (``None`` on a deny).
    license_id: Optional[str] = None
    #: The ``allowed_uses`` code that matched — the declared code itself, or the broadening code
    #: (``0`` / ``1``) that covered it. ``None`` on a deny.
    authorizing_code: Optional[int] = None
    #: Why the request was denied (``None`` on an allow).
    reason: Optional[DenialReason] = None
    #: Human-readable denial detail, safe to log. Never contains credential material.
    detail: Optional[str] = None
    #: The object reference the decision is about — audit only, never branched on (Req 7.7).
    object_path: Optional[str] = None
    #: Every declared code that was not authorized (empty on an allow).
    denied_uses: Tuple[int, ...] = field(default=())

    def __hash__(self) -> int:  # noqa: D105 - behavior documented in the class docstring
        raise CacheKeyContaminationError(
            "EntitlementDecision is unhashable on purpose: an authorization decision must never "
            "contribute to a cache key (cache key = object path only)."
        )

    def __str__(self) -> str:  # noqa: D105 - behavior documented in the class docstring
        raise CacheKeyContaminationError(
            "EntitlementDecision must not be rendered to a string: an authorization decision must "
            "never be concatenated into a cache key. Log its attributes instead (use repr() for "
            "debugging)."
        )


def is_use_authorized(allowed_uses: Iterable[int], declared_use: int) -> Optional[int]:
    """Return the ``allowed_uses`` code authorizing ``declared_use``, or ``None`` if none does.

    Implements the canonical CoMP function-code semantics including the two broadening codes:

    * ``0 = all`` is a superset of every code — an entitlement carrying ``0`` authorizes any
      declared function.
    * ``1 = ai-all`` is a superset of the **AI** codes only (:data:`AI_USE_CODES`: ``1``, ``2``,
      ``3``, ``4``). It does **not** authorize ``5 = search``, which the contract describes as
      "traditional search index, NOT AI summaries".
    * Otherwise an exact match is required.

    A declared ``0`` ("any automated processing") is only authorized by an entitlement that itself
    carries ``0``: asking for everything requires being granted everything. Likewise a declared
    ``1`` needs ``0`` or ``1``. The relation is one-directional, so the gate fails closed.

    Returns the matching code (useful for audit: it records *why* the request was authorized)
    rather than a bool, so the caller can distinguish an exact grant from a broadened one.
    """
    codes = {int(code) for code in allowed_uses}

    if COMP_FUNCTION_ALL in codes:
        return COMP_FUNCTION_ALL
    if declared_use in codes:
        return declared_use
    if COMP_FUNCTION_AI_ALL in codes and declared_use in AI_USE_CODES:
        return COMP_FUNCTION_AI_ALL
    return None


def _function_label(code: int) -> str:
    """Return a human label for a CoMP function code, for denial details and logs."""
    return COMP_FUNCTION_NAMES.get(code, COMP_FUNCTION_CODES.get(code, f"Function {code}"))


def _normalize_declared_uses(declared_use: Any) -> Tuple[int, ...]:
    """Coerce a declared-use input (single code or iterable of codes) to a tuple of ints.

    Non-integer members are dropped rather than guessed at; an empty result is an
    :attr:`DenialReason.INVALID_REQUEST` deny, so a malformed declaration never becomes an allow.
    """
    if declared_use is None:
        return ()
    if isinstance(declared_use, bool):  # bool is an int subclass; never a CoMP code
        return ()
    if isinstance(declared_use, int):
        return (declared_use,)

    codes: List[int] = []
    if isinstance(declared_use, (str, bytes)):
        return ()
    if isinstance(declared_use, Iterable):
        for candidate in declared_use:
            if isinstance(candidate, bool):
                continue
            if isinstance(candidate, int):
                codes.append(int(candidate))
    return tuple(codes)


#: The process-wide store used when a caller passes no ``store``.
#:
#: This caches a boto3 table **resource** (a connection), never a decision or an entitlement — see
#: the module docstring's "What is and is not cached". It is created on first use so importing this
#: module performs no AWS work.
_DEFAULT_STORE: Optional[EntitlementsStore] = None


def _default_store() -> EntitlementsStore:
    """Return the shared :class:`EntitlementsStore`, creating it on first use."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = EntitlementsStore()
    return _DEFAULT_STORE


def reset_default_store() -> None:
    """Drop the shared store so the next call rebuilds it (test/teardown helper)."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = None


def authorize(
    *,
    partner_id: str,
    declared_use: Any,
    license_id: Optional[str] = None,
    object_path: Optional[str] = None,
    store: Optional[EntitlementsStore] = None,
) -> EntitlementDecision:
    """Compute the per-request entitlement decision. Uncached, on every call.

    Runs **before** the cache lookup and produces an authorization outcome that does not vary the
    cached bytes (Requirements 7.6, 10.3). Nothing here is memoized: the entitlement is re-read
    from DynamoDB every call, so a revoked or deactivated entitlement denies on the next request.

    :param partner_id: the authenticated partner. Empty/missing denies
        (:attr:`DenialReason.INVALID_REQUEST`) — the gate never falls back to an anonymous allow.
    :param declared_use: the declared CoMP function code(s): a single ``int`` or an iterable of
        ``int``. An empty or unparseable declaration denies.
    :param license_id: the license the caller presented (CoMP ``aisysuse.lid``). When given, only
        that entitlement is considered — one point read. When ``None`` (a Path B caller presenting
        only a partner key), the partner's entitlements are read in a single item-collection
        ``query`` and the request is allowed if **any** active entitlement authorizes the use.
    :param object_path: the object being requested. **Audit only.** It is logged and echoed on the
        decision, and is never branched on — an opaque or unguessable key gets exactly the same
        check as a guessable one (Requirement 7.7).
    :param store: entitlement store override (tests, or a caller isolating the boto3 resource).
        Defaults to the shared store, whose only cached state is a table resource.
    :returns: an :class:`EntitlementDecision`. Never raises for a denial; a store failure
        propagates, because failing the request is correct when authorization is unknowable.

    No ``cm-licenses`` read happens here: ``allowed_uses`` is denormalized onto the entitlement
    (Requirement 7.4). Usage emission (task 9.2) and Path C's x402 payment gate (#25/#7) are
    separate steps composed by the caller *after* an allow.
    """
    declared_uses = _normalize_declared_uses(declared_use)

    if not partner_id or not declared_uses:
        return _deny(
            partner_id=partner_id or "",
            declared_uses=declared_uses,
            object_path=object_path,
            reason=DenialReason.INVALID_REQUEST,
            detail=(
                "partner_id is required for the entitlement gate"
                if not partner_id
                else "at least one CoMP function code must be declared"
            ),
            denied_uses=declared_uses,
        )

    entitlements = _load_entitlements(
        partner_id=partner_id, license_id=license_id, store=store or _default_store()
    )

    if not entitlements:
        return _deny(
            partner_id=partner_id,
            declared_uses=declared_uses,
            object_path=object_path,
            reason=DenialReason.NO_ENTITLEMENT,
            detail=(
                f"partner {partner_id} holds no entitlement for license {license_id}"
                if license_id
                else f"partner {partner_id} holds no entitlements"
            ),
            denied_uses=declared_uses,
        )

    active = [entitlement for entitlement in entitlements if entitlement.active]
    if not active:
        # An entitlement exists but is deactivated. Inactive is a deny, never an allow.
        return _deny(
            partner_id=partner_id,
            declared_uses=declared_uses,
            object_path=object_path,
            reason=DenialReason.ENTITLEMENT_INACTIVE,
            detail=(
                f"entitlement for partner {partner_id} / license "
                f"{license_id or entitlements[0].license_id} is inactive"
            ),
            denied_uses=declared_uses,
        )

    for entitlement in active:
        authorizing_code = _authorizing_code_for_all(entitlement.allowed_uses, declared_uses)
        if authorizing_code is not None:
            decision = EntitlementDecision(
                allowed=True,
                partner_id=partner_id,
                declared_uses=declared_uses,
                license_id=entitlement.license_id,
                authorizing_code=authorizing_code,
                object_path=object_path,
            )
            logger.info(
                "Entitlement gate ALLOW partner_id=%s license_id=%s declared_uses=%s "
                "authorizing_code=%s object_path=%s",
                partner_id,
                entitlement.license_id,
                list(declared_uses),
                authorizing_code,
                object_path,
            )
            return decision

    # Active entitlements exist, but none covers every declared code.
    denied = _denied_uses(active, declared_uses)
    allowed_summary = sorted({int(code) for e in active for code in e.allowed_uses})
    return _deny(
        partner_id=partner_id,
        declared_uses=declared_uses,
        object_path=object_path,
        reason=DenialReason.USE_NOT_AUTHORIZED,
        detail=(
            f"declared functions {list(denied)} "
            f"({', '.join(_function_label(code) for code in denied)}) "
            f"not permitted by the partner's entitlements (allowed: {allowed_summary})"
        ),
        denied_uses=denied,
    )


def _load_entitlements(
    *, partner_id: str, license_id: Optional[str], store: EntitlementsStore
) -> List[Entitlement]:
    """Read the entitlements relevant to this request. One DynamoDB read, no ``cm-licenses``.

    With a ``license_id`` this is a single point read on the entitlement child key; without one it
    is the single item-collection ``query`` from task 8.2. Either way there is no in-Lambda
    partner -> entitlement -> license join, and the result is not cached anywhere.
    """
    if license_id:
        entitlement = store.get(partner_id, license_id)
        return [entitlement] if entitlement else []
    return store.list_for_partner(partner_id)


def _authorizing_code_for_all(
    allowed_uses: Iterable[int], declared_uses: Sequence[int]
) -> Optional[int]:
    """Return the code authorizing **every** declared use, or ``None`` if any is unauthorized.

    All declared codes must be covered — a partially-authorized request is a deny, matching
    ``validate_comp_license``'s all-or-nothing semantics. The reported code is the broadest one
    used (``0`` beats ``1`` beats an exact match), so audit records why access was granted.
    """
    codes = list(allowed_uses)
    matches = [is_use_authorized(codes, declared) for declared in declared_uses]
    if not matches or any(match is None for match in matches):
        return None
    if COMP_FUNCTION_ALL in matches:
        return COMP_FUNCTION_ALL
    if COMP_FUNCTION_AI_ALL in matches:
        return COMP_FUNCTION_AI_ALL
    return matches[0]


def _denied_uses(
    entitlements: Sequence[Entitlement], declared_uses: Sequence[int]
) -> Tuple[int, ...]:
    """Return the declared codes no active entitlement authorizes."""
    return tuple(
        declared
        for declared in declared_uses
        if all(is_use_authorized(e.allowed_uses, declared) is None for e in entitlements)
    )


def _deny(
    *,
    partner_id: str,
    declared_uses: Tuple[int, ...],
    object_path: Optional[str],
    reason: DenialReason,
    detail: str,
    denied_uses: Tuple[int, ...],
) -> EntitlementDecision:
    """Build (and log) a denial decision. Carries no credential material."""
    logger.info(
        "Entitlement gate DENY partner_id=%s reason=%s declared_uses=%s denied_uses=%s "
        "object_path=%s detail=%s",
        partner_id,
        reason.value,
        list(declared_uses),
        list(denied_uses),
        object_path,
        detail,
    )
    return EntitlementDecision(
        allowed=False,
        partner_id=partner_id,
        declared_uses=declared_uses,
        reason=reason,
        detail=detail,
        object_path=object_path,
        denied_uses=denied_uses,
    )


def decision_headers(decision: EntitlementDecision) -> Dict[str, str]:
    """Return the cache headers a decision contributes to the response.

    * **Deny** -> :data:`DENY_HEADERS` (``no-store, no-cache, must-revalidate``) so a denial is
      never retained by any cache layer (Requirement 7.8).
    * **Allow** -> ``{}``. An allow contributes nothing, so the shared object keeps its own
      cacheability and one cached ``200`` is shared by every authorized caller. The gate decides
      *whether* the object is served, never *how* it is cached.
    """
    return dict(DENY_HEADERS) if not decision.allowed else {}


def deny_response(decision: EntitlementDecision) -> Dict[str, Any]:
    """Render a denial as an uncached API Gateway ``403`` response.

    Uses the shared :func:`~shared.utils.response_helpers.create_error_response` (so the error
    shape matches the rest of the API) with :data:`DENY_HEADERS` attached. ``403`` is correct here:
    the caller authenticated fine, but holds no entitlement for this use.

    Raises :class:`ValueError` on an allow — rendering a denial for an authorized request would be
    a caller bug, and silently emitting a ``403`` would be worse.
    """
    if decision.allowed:
        raise ValueError("deny_response() called for an allowed decision")

    return create_error_response(
        status_code=403,
        message="Not entitled to this content for the declared use",
        details=decision.detail,
        error_code=(decision.reason.value if decision.reason else DenialReason.NO_ENTITLEMENT.value),
        additional_headers=dict(DENY_HEADERS),
    )


__all__ = [
    "AI_USE_CODES",
    "COMP_FUNCTION_AI_ALL",
    "COMP_FUNCTION_ALL",
    "DENY_HEADERS",
    "NO_STORE_CACHE_CONTROL",
    "CacheKeyContaminationError",
    "DenialReason",
    "EntitlementDecision",
    "authorize",
    "decision_headers",
    "deny_response",
    "is_use_authorized",
    "reset_default_store",
]
