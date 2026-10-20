"""
Shared data models for the GEO enrichment pipeline.

ContentMetadata is the canonical record persisted to the cm-content table.
Field groups follow the contextual-metadata-api spec (ContentMetaData_DBChange):
identity, CoMP asset fields, license/RSL, attribution, GEO enrichment, CMS-native.

Stdlib dataclass by design — Pydantic is not a dependency in this repo and its
compiled binaries complicate Lambda packaging.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

# cattax constant: 9 = IAB Content Taxonomy 3.1 (per AdCOM 1.0 list)
CATTAX_IAB_3_1 = 9
MARKDOWN_SCHEMA_VERSION = "1.0"


@dataclass
class ContentMetadata:
    # ── Identity / existing ──
    content_id: str  # partition key; re-processing overwrites in place (ADR-004)
    title: str
    content_type: str  # "document" | "article"

    # ── GEO enrichment / classification ──
    iab_categories: List[dict] = field(default_factory=list)  # [{id, name, tier_path, confidence}] — IAB 3.1
    entities: List[dict] = field(default_factory=list)  # [{name, type, relevance}]
    topics: List[str] = field(default_factory=list)
    sentiment: Dict[str, Any] = field(default_factory=dict)  # {polarity, score}
    quality_score: float = 0.0
    answer_capsule: str = ""  # 40-60 word self-contained fact block
    faq_schema: List[dict] = field(default_factory=list)  # [{question, answer}]

    # ── Timestamps ──
    indexed_at: str = ""  # ISO 8601
    publish_date: Optional[str] = None

    # ── CoMP asset fields (per ContentMetaData_DBChange) ──
    cattax: int = CATTAX_IAB_3_1
    word_count: int = 0  # CoMP Text.wordcount
    language: str = "en"  # CoMP Text.language
    sourcetype: int = 0  # 0=human, 1=AI, 2=hybrid (publisher-supplied; default human)
    provenance: int = 0  # 0=no, 1=yes (no C2PA source for sample content)
    provent: Optional[str] = None  # provenance entity domain, required if provenance=1

    # ── License / RSL ──
    license_id: str = "lic_default_v1"  # stub until A5b naming convention lands
    license_url: str = ""  # precomputed /licenses/{id} URL
    # citation_required intentionally NOT stored on the content record: it is
    # license-derived (part of the License file schema, docs/LICENSE_SCHEMA.md on
    # abrlobo-dev), so the CoMP Package builder reads it from the license, not from
    # here. Confirmed by Abraham on MR !6 (§2.3). Do not re-add.

    # ── Attribution ──
    canonical_url: Optional[str] = None  # source URL; CoMP attribution input
    url_hash: Optional[str] = None  # sha256(normalized canonical URL); #14 adds the lookup GSI
    edge_eligible: bool = False  # safe for deterministic Shape 1 delivery
    edge_ineligible_reason: Optional[str] = None

    # ── Content artifacts ──
    author: str = ""
    summary: str = ""
    md_s3_key: Optional[str] = None  # canonical-host key when eligible; opaque fallback otherwise
    markdown_schema_version: str = MARKDOWN_SCHEMA_VERSION
    source_s3_key: Optional[str] = None  # original ingest object
    embedding_s3_key: Optional[str] = None

    # ── CMS-native ──
    cms_tags: List[str] = field(default_factory=list)

    # ── Operational ──
    status: str = "published"
    processing_status: str = "completed"
    created_at: str = ""
    updated_at: str = ""

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item dict: floats become Decimal, None values dropped."""
        return _to_dynamo(asdict(self))


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value

# ─────────────────────────────────────────────────────────────────────────────
# Integration-adapter supporting value types (spec: integration-adapters, task 2.1)
#
# Type-only value dataclasses referenced by the ratified port signatures
# (integrations/*/port.py). These are not persisted — no to_item() — they are the
# small return/parameter shapes the ports exchange. Stdlib @dataclass by design
# (no Pydantic), matching the ContentMetadata convention above.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TestResult:
    """Result of a port's safe, read-only test() connectivity/auth check."""

    ok: bool
    message: str = ""


@dataclass
class HealthStatus:
    """Ongoing health of an active Connection, returned by a port's status()."""

    healthy: bool
    detail: str = ""


@dataclass
class DriftReport:
    """Read-only drift-detection result for an outbound projection port.

    Reports divergence between the authored source of truth and the target
    system's live state; it is reported, never auto-adopted (one-way projection).
    """

    in_sync: bool = True
    drifted_rules: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class AccessMetrics:
    """Read-only metrics read back from an access-policy target (e.g. WAF)."""

    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetConfig:
    """The resulting target configuration returned by AccessPolicyPort.push().

    Describes the state of the target (e.g. a WAF WebACL) after a scoped, one-way
    projection of our authored rule partition — the applied rule ids within our
    name-prefix / priority band. Type-only; not persisted.
    """

    target_id: str = ""  # identifier of the target (e.g. WebACL id/arn)
    name_prefix: str = ""  # the scoped partition our authored rules own on the target
    applied_rules: List[str] = field(default_factory=list)  # rule ids applied by this push
    detail: str = ""


@dataclass
class ReportResult:
    """Result of a BillingSinkPort.report() batch submission."""

    accepted: int = 0
    rejected: int = 0
    idempotency_key: str = ""
    detail: str = ""


@dataclass
class ChangeRequest:
    """Async, review-gated change request returned by MarketplaceListingPort.push()."""

    change_request_id: str
    status: str = "pending"  # pending | approved | rejected
    marketplace: str = ""


@dataclass
class ListingStatus:
    """Per-marketplace listing state returned by MarketplaceListingPort.get_listing_status()."""

    marketplace: str
    status: str = "pending"  # pending | approved | rejected
    detail: str = ""


@dataclass
class ListingDefinition:
    """An authored marketplace listing pushed outbound by MarketplaceListingPort.push().

    The authored, SSOT-side listing definition (product/pricing/dimensions) that
    is projected out to a marketplace as an async, review-gated change request.
    One-way: authored here, never pulled back from marketplace state as authoring
    input. Type-only; not persisted (Catalog-API authoring is out of scope — this
    spec commits the interface only).
    """

    listing_id: str
    marketplace: str = ""
    product_code: str = ""
    definition: Dict[str, Any] = field(default_factory=dict)  # non-committal authored payload


@dataclass
class CustomerRegistration:
    """Marketplace onboarding result from MarketplaceOnboardingPort.resolve_customer()."""

    customer_id: str
    aws_account_id: str
    product_code: str
    license_arn: Optional[str] = None


@dataclass
class AdapterDescriptor:
    """A catalog entry describing a built-in adapter for a domain (Registry.list_adapters())."""

    domain: str  # content | marketplace | telemetry | access
    adapter: str
    available: bool = True  # built-in adapters are available in every deployment
    has_active_connection: bool = False


@dataclass
class Connection:
    """A persisted ``cm-connections`` record — an activated adapter instance (a Connection).

    This is the **one** entity this spec (integration-adapters, GitLab #35) persists: it owns
    the connection-management capability, so — unlike the other integration-adapter model types
    above (which are type-only, population/persistence owned by the verticals) — this dataclass
    IS persisted here and therefore carries :meth:`to_item` (floats → ``Decimal``, ``None``
    dropped, via the shared ``_to_dynamo`` helper), per #33's convention.

    Fields follow the design "Connection config (persisted by this spec — ``cm-connections``)"
    Data Models table. The attributes the Registry reads (``domain``, ``adapter``, ``brand``,
    ``config``, ``secret_ref``, ``active``) satisfy the registry's structural ``ConnectionRecord``
    Protocol (``integrations/registry.py``), so this record plugs into the Registry with no
    rewrite (dependency inversion).

    **Invariant (enforced by construction):** a Connection record holds only ``secret_ref`` (the
    Secrets Manager ARN/name) — **never** a plaintext credential. There is no field for a secret
    value, so a plaintext secret cannot be persisted on this record (Requirements 6.4, 7.2).
    """

    connection_id: str  # partition key
    domain: str  # content | marketplace | telemetry | access
    adapter: str  # which built-in adapter this Connection activates
    brand: Optional[str] = None  # tenant scope; None for single-brand deployments (Req 6.3a)
    config: Dict[str, Any] = field(default_factory=dict)  # non-secret config (endpoints/region/etc.)
    secret_ref: str = ""  # Secrets Manager ARN/name — NEVER the secret value (Req 6.4, 7.2)
    active: bool = True  # activation state
    created_at: str = ""  # ISO 8601
    updated_at: str = ""  # ISO 8601

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item dict: floats become Decimal, None values dropped."""
        return _to_dynamo(asdict(self))

# ─────────────────────────────────────────────────────────────────────────────
# Integration-adapter core port-facing model types (spec: integration-adapters, task 2.2)
#
# The canonical model types the ratified ports reference (Requirement 4.1):
# UsageEvent (UsageSinkPort.emit / BillingSinkPort.report), Entitlement
# (LicenseResolverPort.resolve), and the AccessPolicy/rule_set shape
# (AccessPolicyPort.push projects it). Type definitions only — population,
# persistence, and wiring stay with the owning verticals (Requirement 4.2):
# UsageEvent write path/emit() → #3; Entitlement records → #3/#15; versioned
# rule persistence → #29. Because this spec does not persist these types, they
# carry no to_item() (persistence is added by the vertical that owns it).
#
# UsageEvent and Entitlement are internal-only — below the wire — and are NOT
# added to openapi-spec.yaml (Requirement 4.3). Stdlib @dataclass, no Pydantic,
# matching the ContentMetadata convention above.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class UsageEvent:
    """A single content-access usage event — the choke point UsageSinkPort.emit()
    consumes and BillingSinkPort.report() batches.

    Per the denormalization rule (architecture-content-as-an-api steering), the
    billing/analytics-relevant values are stamped **at emit time** (values-at-
    time-of-access) so billing/analytics never join back to mutable pricing/
    license rows: price_charged, currency, license_id, content_type.

    Internal-only (below the wire) — NOT part of openapi-spec.yaml. Population and
    emit() semantics are owned by #3.
    """

    # ── Identity of the access ──
    content_id: str
    partner_id: str
    accessed_at: str = ""  # ISO 8601, stamped at emit

    # ── Denormalized values-at-time-of-access (stamped at emit) ──
    price_charged: float = 0.0
    currency: str = "USD"
    license_id: str = ""
    content_type: str = ""

    # ── Optional access context ──
    request_id: Optional[str] = None
    declared_use: Optional[int] = None  # CoMP function code declared by the caller


@dataclass
class Entitlement:
    """The server-side "partner holds license" authorization record backing the
    CoMP license check (LicenseResolverPort.resolve()).

    Internal, below the wire — NOT a CoMP wire object and NOT part of
    openapi-spec.yaml (Requirement 4.3). Denormalized AWS keys are carried where
    the entitlement is marketplace-sourced: product_code, customer_identifier,
    license_arn. Record population is owned by #3/#15.
    """

    # ── Authorization link ──
    partner_id: str
    license_id: str
    allowed_uses: List[int] = field(default_factory=list)  # CoMP function codes
    active: bool = True

    # ── Denormalized AWS Marketplace keys (present when marketplace-sourced) ──
    product_code: Optional[str] = None
    customer_identifier: Optional[str] = None
    license_arn: Optional[str] = None


@dataclass
class AccessRule:
    """A single authored access/bot rule within an AccessPolicy rule_set.

    Part of the authored ruleset AccessPolicyPort.push projects (one-way,
    declarative, idempotent, scoped). Versioned-ruleset persistence is owned by
    #29; this defines only the port-facing type.
    """

    rule_id: str
    action: str = ""  # e.g. allow | block | count | monetize
    priority: int = 0
    statement: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessPolicy:
    """The authored ruleset (rule_set) shape AccessPolicyPort.push projects onto a
    target (e.g. a WAF WebACL).

    push is a **scoped** overwrite of our authored rule partition (name-prefix /
    priority band), preserving foreign rules on the same target (AWS managed rule
    groups, the shared MonetizeRuleGroup). This is the port-facing type only;
    the versioned authored ruleset and its persistence are owned by #29/#7.
    """

    policy_id: str
    version: str = ""
    name_prefix: str = ""  # the scoped partition this policy owns on the target
    rules: List[AccessRule] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Partner identity and credentials (spec: native-apikey-auth-migration, Req 7)
#
# ``cm-partners`` is keyed on the partner: PK ``PARTNER#<partner_id>``, SK ``META`` for the
# account record and ``KEY#<api_key_id>`` for each API Gateway key. The key *value* is held by
# API Gateway only; this table never stores a secret or a hash of one. GSI ``api-key-id-index``
# (PK ``api_key_id``) resolves a request's key id to its partner.
# ─────────────────────────────────────────────────────────────────────────────

PARTNER_PK_PREFIX = "PARTNER#"
PARTNER_META_SK = "META"
PARTNER_KEY_SK_PREFIX = "KEY#"

PARTNER_STATUSES = ("active", "suspended", "deleted")
PARTNER_CREDENTIAL_STATUSES = ("active", "disabled")
PARTNER_IDENTITY_SOURCES = ("direct", "marketplace")
PARTNER_PRICING_TIERS = ("basic", "standard", "premium", "enterprise")


def partner_pk(partner_id: str) -> str:
    """Partition key for every item belonging to ``partner_id``."""
    return f"{PARTNER_PK_PREFIX}{partner_id}"


def partner_key_sk(api_key_id: str) -> str:
    """Sort key of the credential item for an API Gateway key id."""
    return f"{PARTNER_KEY_SK_PREFIX}{api_key_id}"


@dataclass
class Partner:
    """The ``META`` item of a partner (licensee) in ``cm-partners``.

    ``allowed_endpoints`` holds key-gated partner-plane ``operationId`` values; an empty list means
    every key-gated operation is allowed. Quota and usage are not attributes of the partner: they
    belong to the usage plan backing ``pricing_tier`` and are read from API Gateway on demand.
    """

    partner_id: str
    partner_name: str
    pricing_tier: str = "basic"  # one of PARTNER_PRICING_TIERS
    status: str = "active"  # one of PARTNER_STATUSES
    identity_source: str = "direct"  # one of PARTNER_IDENTITY_SOURCES
    partner_type: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    allowed_endpoints: List[str] = field(default_factory=list)
    marketplace_type: Optional[str] = None
    marketplace_customer_id: Optional[str] = None
    created_at: str = ""  # ISO 8601
    updated_at: str = ""  # ISO 8601

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item with the PK/SK pair added; None values dropped."""
        item = _to_dynamo(asdict(self))
        item["PK"] = partner_pk(self.partner_id)
        item["SK"] = PARTNER_META_SK
        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Partner":
        """Build from a DynamoDB item, ignoring the key attributes and unknown fields."""
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in item.items() if k in known}
        data["allowed_endpoints"] = list(item.get("allowed_endpoints") or [])
        return cls(**data)


@dataclass
class PartnerCredential:
    """A ``KEY#<api_key_id>`` item: one API Gateway key bound to a partner.

    Carries the key *id* and the usage plan it is attached to, never the key value.
    """

    partner_id: str
    api_key_id: str
    usage_plan_id: str
    status: str = "active"  # one of PARTNER_CREDENTIAL_STATUSES
    created_at: str = ""  # ISO 8601
    disabled_at: Optional[str] = None  # ISO 8601

    def to_item(self) -> Dict[str, Any]:
        """DynamoDB item with the PK/SK pair added; None values dropped."""
        item = _to_dynamo(asdict(self))
        item["PK"] = partner_pk(self.partner_id)
        item["SK"] = partner_key_sk(self.api_key_id)
        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "PartnerCredential":
        """Build from a DynamoDB item, ignoring the key attributes and unknown fields."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in item.items() if k in known})
