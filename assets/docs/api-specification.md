# API Specification

## Reading this guide

The complete, authoritative API contract is **[`openapi-spec.yaml`](../../openapi-spec.yaml)** — an OpenAPI 3.0 document that is the single source of truth for every path, request/response schema, status code, and security scheme. The API Gateway deployment, the Lambda handlers, and the in-portal developer reference are all derived from it, so it never drifts from the running API.

Two ways to consume it:

- **Interactive developer portal** — the publisher portal serves a [Scalar](https://github.com/scalar/scalar) reference at its **`/docs`** route. It renders the live spec, lets you try endpoints with your signed-in session, and has an **Internal | Partner** toggle to preview exactly the surface external AI operators see.
- **The raw contract** — read or lint [`openapi-spec.yaml`](../../openapi-spec.yaml) directly, or load it into any OpenAPI tool.

This document is a conceptual overview — the API's shape, auth models, and conventions. For exact fields, enums, and examples, go to the spec or the portal.

## What the API does

The API implements the IAB Tech Lab **Content Monetization Protocol (CoMP)** for publisher content monetization and AI-agent integration. It serves two audiences through one contract:

- **AI operators** — discover and query licensed publisher content.
- **Publishers** — manage content, pricing, licensing, partners, bot rules, analytics, and the `llms.txt` manifest.

## Two authentication planes

Authentication is organized as two planes over the same backend (see the [Authentication Guide](authentication-guide.md) for details):

| Plane | Who | Mechanism |
|---|---|---|
| **Partner** | AI operators / external agents | Amazon API Gateway **native API keys + usage plans** (`x-api-key` header). Keys are provisioned per partner; usage plans enforce throttle and quota at the edge. |
| **Internal** | Publishers (portal and direct API) | **Amazon Cognito JWT** (`Authorization: Bearer <token>`). |

A small set of discovery endpoints are **public** (no auth). Each operation's required scheme is declared in the spec under its `security` block and surfaced by the `x-audience` tag (`partner` vs `internal`).

## Endpoint groups

The contract organizes operations into these groups. For each group's exact
methods, paths, parameters, and response shapes, see the spec (or the portal's
`/docs` reference) — this is a map, not a restatement of the contract.

- **Partner / discovery** (AI operators) — discovery documents, the IAB
  taxonomy, the `llms.txt` manifest, the single `POST /query` content-discovery
  operation, and usage reporting. Discovery documents and the taxonomy are
  public; `query` and logging are native-key authenticated.
- **Content & pipeline** (publisher) — content CRUD (create queues the GEO
  enrichment pipeline), contextual metadata, processing/job status, and the
  reviewable `llms.txt` generate/publish lifecycle.
- **Pricing & licensing** (publisher) — versioned pricing rules and RSL (Really
  Simple Licensing) terms.
- **Partners, analytics, bot control, connections** (publisher) — partner
  lifecycle and API-key provisioning, analytics and the AI analytics assistant,
  bot access rules with WAF-metric visibility, and integration-adapter
  connections.

> Endpoints are added and refined in the spec first (contract-first); the
> groups above are a conceptual map.

## IAB Content Taxonomy 3.1

Content is classified against the IAB Content Taxonomy 3.1 (a 4-tier hierarchy). Categories are denormalized onto each content record at ingestion as `{id, name, tier_path}` objects, so query and discovery responses carry category identity without a separate lookup. `GET /taxonomy` and `GET /taxonomy/categories` expose the taxonomy for browsing and UI dropdowns.

## Pricing models

The API supports per-access and tiered pricing with IAB-category, recency, and demand multipliers, plus RSL license terms. Pay-per-access (x402) can be enforced at the edge. Pricing rules are versioned so historical transactions bill against the terms in effect at access time.

## Conventions

- **Transport:** HTTPS only.
- **Spec dialect:** OpenAPI 3.0.3.
- **Errors:** a consistent coded error envelope (`error`, `message`/`code`, `request_id`) with standard HTTP status codes (`400`, `401`, `403`, `404`, `429`, `500`, `503`).
- **Rate limiting / quota:** enforced per API key through API Gateway usage plans on the partner plane.
- **Standards:** ISO 3166 country codes, ISO 639-1 language codes, RFC 3339 timestamps, RFC 3986 URIs.

## See also

- [`openapi-spec.yaml`](../../openapi-spec.yaml) — the authoritative contract.
- [Authentication Guide](authentication-guide.md) — auth planes and flows.
- [Partner API Guide](partner-api.md) — the AI-operator consumer walkthrough.
- [Architecture Overview](architecture-overview.md) — system components and data flow.
