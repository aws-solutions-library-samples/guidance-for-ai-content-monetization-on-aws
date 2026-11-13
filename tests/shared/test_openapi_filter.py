"""
Unit tests for the shared partner-view filter (#31 Req 7.3/7.4).

Proves derive_partner_spec: keeps partner ops, drops internal ops, prunes schemas to the
reachable $ref closure (no internal-schema leak / self-contained), keeps only referenced
security schemes, and keeps only the Partner API tag-group. Also runs it against the real
openapi-spec.yaml to assert the partition end-state.
"""

from pathlib import Path

import yaml

from shared.utils.openapi_filter import derive_partner_spec

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _tiny_spec():
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "security": [{"CognitoAuth": []}],
        "tags": [{"name": "Discovery"}, {"name": "4. Pricing"}],
        "x-tagGroups": [
            {"name": "Publisher Workflow", "tags": ["4. Pricing"]},
            {"name": "Partner API", "tags": ["Discovery"]},
        ],
        "paths": {
            "/info": {
                "get": {
                    "tags": ["Discovery"],
                    "x-audience": "partner",
                    "security": [{"PartnerKeyAuth": []}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Info"}}}}},
                }
            },
            "/pricing/rules": {
                "get": {
                    "tags": ["4. Pricing"],
                    "x-audience": "internal",
                    "security": [{"CognitoAuth": []}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pricing"}}}}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "PartnerKeyAuth": {"type": "apiKey", "name": "x-api-key", "in": "header"},
                "CognitoAuth": {"type": "http", "scheme": "bearer"},
            },
            "schemas": {
                "Info": {"type": "object", "properties": {"nested": {"$ref": "#/components/schemas/InfoNested"}}},
                "InfoNested": {"type": "string"},
                "Pricing": {"type": "object"},  # internal-only
            },
        },
    }


class TestDerivePartnerSpec:
    def test_keeps_partner_ops_drops_internal(self):
        out = derive_partner_spec(_tiny_spec())
        assert "/info" in out["paths"]
        assert "/pricing/rules" not in out["paths"]  # x-audience: internal dropped

    def test_schema_closure_self_contained_no_internal_leak(self):
        out = derive_partner_spec(_tiny_spec())
        schemas = out["components"]["schemas"]
        # Info + its transitive InfoNested kept; internal-only Pricing pruned.
        assert set(schemas) == {"Info", "InfoNested"}
        assert "Pricing" not in schemas
        # every $ref in the partner doc resolves within the partner doc (self-contained)
        from shared.utils.openapi_filter import _iter_refs, _schema_name_from_ref
        for ref in _iter_refs(out.get("paths")):
            name = _schema_name_from_ref(ref)
            if name:
                assert name in schemas, f"dangling $ref to pruned schema: {name}"

    def test_keeps_only_referenced_security_schemes(self):
        out = derive_partner_spec(_tiny_spec())
        schemes = set(out["components"]["securitySchemes"])
        # PartnerKeyAuth used by /info; CognitoAuth still referenced by top-level security.
        assert "PartnerKeyAuth" in schemes

    def test_keeps_only_partner_tag_group(self):
        out = derive_partner_spec(_tiny_spec())
        groups = {g["name"] for g in out["x-tagGroups"]}
        assert groups == {"Partner API"}
        assert {t["name"] for t in out["tags"]} == {"Discovery"}

    def test_does_not_mutate_input(self):
        spec = _tiny_spec()
        derive_partner_spec(spec)
        assert "/pricing/rules" in spec["paths"]  # original untouched

    def test_against_real_contract(self):
        spec = yaml.safe_load(open(PROJECT_ROOT / "openapi-spec.yaml"))
        out = derive_partner_spec(spec)
        partner_paths = set(out["paths"])
        # Every kept op is partner-facing.
        for _p, item in out["paths"].items():
            for m, op in item.items():
                if isinstance(op, dict) and "x-audience" in op:
                    assert op["x-audience"] != "internal"
        # No internal path leaks in.
        assert "/analytics/metrics" not in partner_paths
        assert "/pricing/rules" not in partner_paths
        # /content is MIXED: the GET is the partner-facing URL read (#14); the POST
        # (publisher ingest) stays internal and must not leak into the partner spec.
        assert "/content" in partner_paths
        assert {m for m in out["paths"]["/content"] if m in {"get", "post"}} == {"get"}
        # Partner paths present.
        # /licenses (the collection) is internal-plane — only the single-document read and
        # validate are partner-facing (#3 Req 4.1/4.2), so the partner path is the
        # parameterized one.
        for p in ("/info", "/query", "/taxonomy", "/llms.txt", "/log", "/licenses/{license_id}"):
            assert p in partner_paths, f"expected partner path missing: {p}"
        assert "/search" not in partner_paths
        # Self-contained: every $ref resolves within the filtered schemas.
        from shared.utils.openapi_filter import _iter_refs, _schema_name_from_ref
        kept = set(out.get("components", {}).get("schemas", {}))
        for ref in _iter_refs(out["paths"]):
            name = _schema_name_from_ref(ref)
            if name:
                assert name in kept, f"partner view has dangling $ref: {name}"
        # Only Partner API tag-group survives.
        assert {g["name"] for g in out["x-tagGroups"]} == {"Partner API"}
