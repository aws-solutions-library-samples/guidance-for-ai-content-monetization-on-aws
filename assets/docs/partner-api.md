# Partner API

Licensees (AI operators, bots, marketplaces) call a dedicated REST API whose operation set is
derived from `openapi-spec.yaml` at deploy time: every operation marked for the partner audience
that is reachable with a partner key or anonymously, minus retired or not-yet-implemented
operations. The publisher UI uses a separate, Cognito-authenticated API. Nothing internal is
routed on the partner plane.

## Authentication

Key-gated operations (`POST /query`, `POST /log`, …) require a native API Gateway key sent as

```
x-api-key: <partner-api-key>
```

API Gateway validates the key and enforces the quota and throttle of the usage plan it is bound
to before any code runs; the Lambda then resolves the key id to its partner and applies the
partner's `allowed_endpoints`. Error bodies carry stable codes:

| Status | `error_code` | Meaning |
|---|---|---|
| 403 | `invalid_api_key` | Missing, unknown or disabled key |
| 403 | `partner_inactive` | Key maps to a suspended or deleted partner |
| 403 | `endpoint_not_allowed` | Operation not in the partner's allow-list |
| 429 | `quota_exceeded` | Usage plan quota exhausted (`Retry-After` set) |
| 429 | `throttled` | Rate or burst limit (`Retry-After: 1`) |

Public operations (`GET /info`, `GET /taxonomy`, `GET /llms.txt`, `GET /openapi`) need no key.

`allowed_endpoints` holds operation ids (`queryContent`, `submitLog`, `validateLicense`), not
paths. A dev partner record with stale permissions is fixed by `make seed-data` or by editing
the partner; there is no permission migration tool.

## Discovery: `POST /query`

`POST /query` is the only discovery operation. `/search` was deleted: there is no contract path,
Gateway resource, alias or tombstone, so a stale caller gets API Gateway's answer for a path that
does not exist. On the partner plane that is `403 {"message": "Missing Authentication Token"}`,
even with a valid key. On the internal plane it is `401` without a token and `404 Endpoint not
found` with one.

```json
{
  "query": "electric vehicle battery cost",
  "content_type": ["article", "report"],
  "max_results": 10,
  "similarity_threshold": 0.7
}
```

- `similarity_threshold` is optional and omitting it means no cutoff. Explicit values are finite
  numbers from 0 through 1. The score is relative to the response, not a calibrated confidence.
- The legacy names `content_types` and `limit` are rejected with `400`, even alongside the
  canonical fields.
- CoMP requests set the threshold at `aisystem.aisysuse.ext.discovery.similarity_threshold`, which
  requires `ext.discovery.query`. Filter-only requests skip embeddings.
- A CoMP Package's `retrieval` is `{"auth", "endpoint": "<base>/query", "type": [2]}`. This is an
  interim rediscovery entry point, not a per-item read reference; per-item reads arrive with the
  content read contract (a planned future enhancement).

## Keys, tiers and quotas

Each pricing tier (`basic`, `standard`, `premium`, `enterprise`) is one usage plan
(`cm-partner-<tier>`). A partner's key is bound to its tier's plan; changing the tier moves the
key. Quota is therefore a property of the tier, not of the partner record. Key values are shown
exactly once when a key is created or rotated and are never stored by this system.

## Base URL and the served contract

The stack exports `PartnerApiUrl`:

```
https://<api-id>.execute-api.<region>.amazonaws.com/v1
```

Every Lambda that advertises partner links (the managed `llms.txt` renderer, `GET /info`) reads
`PARTNER_API_URL`, so the manifest and discovery responses point at the deployed plane.
`GET <PartnerApiUrl>/openapi` serves the contract for exactly the operations this plane routes,
with `servers` set to the host that answered.

### Custom domain (optional, not built by this guidance)

1. Create an ACM certificate for the domain in the API's region.
2. Create an API Gateway custom domain name and a base-path mapping to the partner API's `v1`
   stage (`aws apigateway create-domain-name`, `create-base-path-mapping`).
3. Point DNS at the domain's regional target.
4. Set `PARTNER_API_URL` on the Lambdas to `https://<your-domain>` (for example through a CDK
   context value) and regenerate `llms.txt` so the published Access links move with it.

## Test partner key

Deployments create one test key (output `TestPartnerApiKeyId`) bound to the `standard` plan.
Read its value with

```
aws apigateway get-api-key --include-value --api-key <TestPartnerApiKeyId> --query value --output text
```

Integration tests and the MCP tooling resolve it this way; nothing stores the value.
