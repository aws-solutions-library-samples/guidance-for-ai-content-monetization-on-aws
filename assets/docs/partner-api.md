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
| 401 | `authentication_required` | No credential resolved; a key created moments ago can return this while it propagates, so retry after a short wait |
| 429 | `quota_exceeded` | Usage plan quota exhausted (`Retry-After` set) |
| 429 | `throttled` | Rate or burst limit (`Retry-After: 1`) |
| 400 | `invalid_read_selector` | A `GET /content` read without exactly one of `url` and `content_id`, or with a malformed `content_id` |
| 404 | `content_not_found` | A `GET /content` read found no published content for the `url` or `content_id` |
| 403 | `route_not_found` | No such path or method on the partner API |
| 403 | `request_blocked` / `access_denied` | Blocked by AWS WAF / denied by API Gateway |
| 4xx | `request_rejected` | Other gateway rejection; `error` carries the gateway's message |
| 5xx | `gateway_error` | API Gateway could not complete the request; retry with backoff |

Public operations (`GET /info`, `GET /taxonomy`, `GET /llms.txt`, `GET /openapi`) need no key.

`allowed_endpoints` holds operation ids (`queryContent`, `submitLog`, `validateLicense`,
`listContentOrGetByUrl`), not paths. A dev partner record with stale permissions is fixed by `make seed-data` or by editing
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
- Every result carries `content_id` and `url`, an absolute read reference. Inline `content`, when
  present, is additional data and never replaces `url`. CoMP Text assets carry the same pair in
  `ext.content_id` and `ext.url`. See [Reading results](#reading-results-get-content).
- A CoMP Package's `retrieval` describes that read contract:
  `{"auth": 1, "endpoint": "<base>/content", "type": [2], "ext": {"read_by_url": ..., "read_by_id": ...}}`.
  The pending-license Package carries no assets but points at the same read.

## Reading results: `GET /content`

A result's `url` is followable with the same credential that made the query, on the same plane:

| Caller | Content | `url` |
|---|---|---|
| Partner key | Has a canonical URL | `GET <base>/content?url=<canonical URL>` |
| Partner key | No canonical URL | `GET <base>/content?content_id=<content_id>` |
| Cognito (publisher UI) | Either | `GET <base>/content/<content_id>` |

On the partner plane `GET /content` reads one document. Supply exactly one of `url` and
`content_id`, otherwise the answer is `400 invalid_read_selector`. Both forms return the same
body (`content_id`, `markdown`, `metadata`, `canonical_url`), an `ETag`, and `304 Not Modified`
for a matching `If-None-Match`. For pipeline-ingested content, `markdown` is the stored
Markdown document, the same bytes native edge delivery serves, and the `ETag` changes whenever
that document does. If the stored document can't be read or is too large to return, the answer
is `500` and the read is not billed. A `200` is one billable access; a `304` is not. Both forms
return published content only. An unknown or unpublished identifier or URL gets the same
`404`, so the response never reveals whether a record exists. Both forms are the
`listContentOrGetByUrl` operation, so one `allowed_endpoints` entry grants both.

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
