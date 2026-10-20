# Native Edge Markdown Delivery

This capability adds deterministic, lookup-free Markdown delivery through a shared
CloudFront cache. The feature is disabled by default until the publisher and
central access gates are configured.

This is the first phase of native edge delivery. It ships the deployed, private,
disabled core and stable integration contracts. A later phase adds the adjacent
gates/resolver, attaches the publisher router, enables delivery, and completes
end-to-end Path A/B/C acceptance.

## Request Flow

```text
Publisher request
  -> publisher WAF authorizes and inserts x-amzn-waf-cm-format: markdown
  -> publisher CloudFront Function computes /content/<host>/<path>.md
  -> publisher distribution selects its preconfigured central origin
  -> central gate validates the publisher handoff
  -> central CloudFront cache
  -> private S3 through OAC on cache miss
```

The successful content path performs no CloudFront KVS, API Gateway, Lambda,
or DynamoDB lookup.

## Deterministic Addresses

Examples:

| Publisher URL | Central object URI |
|---|---|
| `https://news.example.com/` | `/content/news.example.com/index.md` |
| `https://news.example.com/a/b` | `/content/news.example.com/a/b.md` |
| `https://news.example.com/a/b/` | `/content/news.example.com/a/b/index.md` |
| `https://news.example.com/a/file.html` | `/content/news.example.com/a/file.html.md` |

Only direct same-host paths use this fast path. Arbitrary `?url=` resolution,
moved URLs, cross-host lookup, collections, and variants are handled by the
Shape 2 resolver, which is a planned future enhancement.

Content without an eligible canonical URL remains available internally at
`content/{content_id}.md` but cannot use native edge routing.

## Deployment Configuration

The central distribution is created by `ContentMonetizationStack`.

```json
{
  "nativeEdgeEnabled": false,
  "publisherCentralOriginId": "central-content-origin"
}
```

Stack outputs:

- `NativeContentEdgeDomainName`
- `NativeContentEdgeDistributionId`
- `PublisherEdgeRouterFunctionArn`

The distribution remains disabled when `nativeEdgeEnabled` is false.
In this core release, synthesis rejects every attempt to set
`nativeEdgeEnabled` to true. A future phase must add the concrete central gate
and enablement path; a WebACL ARN by itself is not sufficient proof of a gate.

Do not enable the distribution until the central WAF, signed Path A handoff
validation, and pre-cache gate are in place.

## Publisher Distribution Integration

For an existing publisher CloudFront distribution:

1. Add the central edge domain as a custom HTTPS origin.
2. Give that origin the exact ID configured by
   `publisherCentralOriginId`.
3. Configure the protected origin handoff required by the central gate. Never
   embed the credential in CloudFront Function source.
4. Configure publisher WAF to block viewer-supplied headers beginning with
   `x-amzn-waf-`.
5. After payment/authorization succeeds, have WAF insert:

   ```text
   x-amzn-waf-cm-format: markdown
   ```

6. Associate `PublisherEdgeRouterFunctionArn` with the publisher behavior's
   viewer-request event.
7. Keep ordinary website requests on the publisher origin. The router selects
   the central origin only for trusted `GET` and `HEAD` Markdown requests.

The router consumes the trusted format header and does not forward it to the
central origin.

## Cache and Security Invariants

- The content bucket is private and reachable from CloudFront only through
  OAC.
- The content cache key is the object path only.
- Cookies, query strings, authorization headers, license keys, WAF signals,
  handoff values, and payment signatures do not vary the cache.
- The `/content/*` behavior permits only `GET` and `HEAD`.
- Direct S3 access is denied.
- The router contains no KVS, fetch, secret, credential, or database call.
- A central viewer-response function converts trusted license object metadata
  to an RSL `Link` header and removes internal S3 metadata headers.

## Missing Objects

A missing deterministic object is not resolved through KVS. The future miss
integration falls through to the Shape 2 resolver and existing `ingest/` GEO
pipeline. That integration remains disabled until the resolver and the central
gate are ready.

## Validation

Before enabling native delivery:

1. Run `make test-edge`.
2. Run `make lint-strict`, `make test`, and `make package-lambda`.
3. Synthesize and inspect `ContentMonetizationStack` and `CMPipelineStack`.
4. Confirm the central distribution is disabled and the S3 origin uses OAC.
5. Confirm direct S3 requests fail.
6. Ingest HTML, Markdown, and JSON and verify canonical keys, object metadata,
   DynamoDB, and OpenSearch.
7. After gate integration, prove unauthorized requests are denied before
   cache lookup and an authorized cache hit invokes no API Gateway, Lambda,
   DynamoDB, or KVS operation.
