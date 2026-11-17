"""
Shared OpenAPI audience filter (#31).

`derive_partner_spec` produces the **partner view** of the OpenAPI contract: only operations
whose `x-audience` is not `"internal"`, plus the `components/schemas` those operations transitively
`$ref`, the partner security scheme(s) still referenced, and the `Partner API` tag-group.

Audience is carried on the non-Scalar-reserved key `x-audience: internal|partner` (NOT
`x-internal`): recent `@scalar/api-reference` versions hide any operation marked `x-internal: true`
from the rendered sidebar, which would wrongly drop internal operations from the in-app Internal
view. `x-audience` is a pure classification flag with no rendering side effects.

Single implementation, multiple consumers (#31 Req 7.4):
  * the in-app `/docs` Internal|Partner toggle (via `/openapi?audience=partner`): the whole
    partner-facing product, including the Cognito-managed license operations;
  * the public document served on the partner API itself (`derive_partner_plane_spec`): only
    the operations that plane actually routes, with `servers` pointing at the host that served it.

Pure and dependency-free (operates on an already-parsed dict) so it is trivially unit-testable
and reusable server-side without importing yaml/framework code.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Optional, Set

from shared.auth.partner_operations import DEFERRED_NO_HANDLER

_OP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head", "trace")
PARTNER_KEY_SCHEME = "PartnerKeyAuth"
NATIVE_API_KEY_SCHEME: Dict[str, Any] = {
    "type": "apiKey",
    "name": "x-api-key",
    "in": "header",
    "description": "Partner API key issued by the publisher; sent verbatim on every key-gated request.",
}


def _iter_refs(node: Any) -> Iterable[str]:
    """Yield every local `$ref` string ('#/components/schemas/Foo') reachable in `node`."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _iter_refs(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _schema_name_from_ref(ref: str) -> str | None:
    prefix = "#/components/schemas/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def _closure(schemas: Dict[str, Any], seed_refs: Iterable[str]) -> Set[str]:
    """Transitively resolve the set of schema names reachable from `seed_refs`."""
    wanted: Set[str] = set()
    stack = [n for r in seed_refs if (n := _schema_name_from_ref(r)) is not None]
    while stack:
        name = stack.pop()
        if name in wanted or name not in schemas:
            continue
        wanted.add(name)
        for ref in _iter_refs(schemas[name]):
            n = _schema_name_from_ref(ref)
            if n and n not in wanted:
                stack.append(n)
    return wanted


def is_partner_operation(operation: Dict[str, Any]) -> bool:
    """An operation is partner-facing unless it is explicitly `x-audience: internal`.

    Fail-open on absence (an operation with no audience flag is treated as partner-facing) mirrors
    the prior `x-internal is not True` semantics, so an unclassified op never silently vanishes
    from the partner view without a gated-test failure surfacing it first.
    """
    return operation.get("x-audience") != "internal"


def derive_partner_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a partner-only copy of `spec`.

    - keep only operations where `x-audience` is not `"internal"` (Req 6.4);
    - prune `components/schemas` to the transitive `$ref` closure of the kept operations
      (internal-only schemas must not leak);
    - keep only security schemes still referenced by a kept operation (or the top-level
      `security`);
    - keep only the `Partner API` tag-group and the tags it lists.
    """
    out = copy.deepcopy(spec)

    # 1. Filter paths/operations.
    kept_paths: Dict[str, Any] = {}
    for path, item in (spec.get("paths") or {}).items():
        kept_ops = {
            method: op
            for method, op in item.items()
            if method not in _OP_METHODS or (isinstance(op, dict) and is_partner_operation(op))
        }
        # Drop non-operation keys only if no operation survived (keeps shared params if any op kept).
        if any(m in _OP_METHODS for m in kept_ops):
            kept_paths[path] = kept_ops
    out["paths"] = kept_paths

    # 2. Prune schemas to the reachable closure.
    all_schemas = (spec.get("components") or {}).get("schemas") or {}
    seed_refs = list(_iter_refs(kept_paths))
    wanted = _closure(all_schemas, seed_refs)
    if "components" in out:
        comps = out["components"]
        if "schemas" in comps:
            comps["schemas"] = {n: s for n, s in all_schemas.items() if n in wanted}

    # 3. Keep only security schemes still referenced.
    used_schemes: Set[str] = set()
    for _p, item in kept_paths.items():
        for method, op in item.items():
            if method in _OP_METHODS and isinstance(op, dict):
                for req in op.get("security") or []:
                    used_schemes.update(req.keys())
    for req in spec.get("security") or []:
        used_schemes.update(req.keys())
    comps = out.get("components") or {}
    if "securitySchemes" in comps:
        comps["securitySchemes"] = {
            name: sch for name, sch in comps["securitySchemes"].items() if name in used_schemes
        }

    # 4. Keep only the Partner API tag-group + its tags.
    partner_group_tags: Set[str] = set()
    partner_groups = []
    for group in spec.get("x-tagGroups") or []:
        if group.get("name") == "Partner API":
            partner_groups.append(group)
            partner_group_tags.update(group.get("tags") or [])
    if "x-tagGroups" in out:
        out["x-tagGroups"] = partner_groups
    if "tags" in out and partner_group_tags:
        out["tags"] = [t for t in out["tags"] if t.get("name") in partner_group_tags]

    return out


def is_partner_plane_operation(method: str, path: str, operation: Dict[str, Any]) -> bool:
    """True when the partner API routes this operation.

    Mirrors the CDK derivation: ``x-audience: partner``, reachable with ``PartnerKeyAuth`` or
    anonymously (a Cognito-only operation lives on the internal plane), and not deferred for lack
    of a handler.
    """
    if operation.get("x-audience") != "partner":
        return False
    security = operation.get("security")
    reachable = security is None or any(req == {} or PARTNER_KEY_SCHEME in req for req in security)
    if not reachable:
        return False
    key = (method.upper(), path)
    return key not in DEFERRED_NO_HANDLER


def derive_partner_plane_spec(spec: Dict[str, Any], server_url: Optional[str] = None) -> Dict[str, Any]:
    """Return the document the partner API serves about itself.

    Starts from the partner view, then keeps only the operations the plane routes, replaces the
    partner security scheme with the native API Gateway key, and points ``servers`` at the host
    that served the request (the plane has no custom domain; the generated hostname is the URL).
    """
    partner_view = derive_partner_spec(spec)
    plane_paths: Dict[str, Any] = {}
    for path, item in partner_view["paths"].items():
        kept: Dict[str, Any] = {}
        for method, op in item.items():
            if method not in _OP_METHODS:
                kept[method] = op
                continue
            if not (isinstance(op, dict) and is_partner_plane_operation(method, path, op)):
                continue
            op = copy.deepcopy(op)
            if op.get("security") is not None:
                # Only the native key (or anonymous access) works on this plane; Cognito is an
                # internal-API alternative and must not be advertised here.
                op["security"] = [req for req in op["security"] if req == {} or PARTNER_KEY_SCHEME in req]
            kept[method] = op
        if any(m in _OP_METHODS for m in kept):
            plane_paths[path] = kept
    plane = {**spec, "paths": plane_paths}
    plane.pop("security", None)  # a top-level default would re-admit schemes the plane lacks
    out = derive_partner_spec(plane)  # re-prune schemas, schemes and tags to what survived
    schemes = (out.get("components") or {}).get("securitySchemes")
    if schemes and PARTNER_KEY_SCHEME in schemes:
        schemes[PARTNER_KEY_SCHEME] = dict(NATIVE_API_KEY_SCHEME)
    if server_url:
        out["servers"] = [{"url": server_url, "description": "This deployment"}]
    return out
