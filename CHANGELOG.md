# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-13

### Added

- **Scrape vs. licensed Markdown comparison (Test page).** A publisher enters an
  article URL and reads the verdict first: the measured gains of the licensed
  Markdown over scraping the same page (tokens, bytes, time), then an
  at-a-glance table with an evidence label on every row, then collapsed
  step-by-step evidence for both sides. A Content fidelity tab shows the article
  text and the rendered Markdown side by side with sentence-level coverage and a
  quality score. A Cost Model pull-out holds every assumption and the
  break-even price. The Test page is now a grid of scenario cards; the agent
  simulator stays as a card labelled as mock data. A fixture article served from
  the portal is the default target.
  - Two new internal (Cognito) operations back it: `POST /test/comparison-runs`
    fetches the page server-side behind an SSRF guard (https only, public
    addresses only, bounded size and time, a host allow-list that defaults to the
    deployment's own portal host) and queues it for enrichment;
    `GET /test/comparison-runs/{run_id}` returns the run's Markdown once the
    pipeline has written it.
  - Comparison runs are stored in a separate partition, `comparison/<url_hash>.md`,
    outside `content/`, with no edge address and no search document. Their
    records have the new status `comparison` and an `expires_at` seven days out
    (written but not yet enforced). Partner, discovery and search reads serve
    only `published` records, and the Content Library list leaves comparison
    records out unless the request has `status=comparison`. A run on one of the
    deployment's publisher hosts reuses the article's published record when there
    is one and never changes it.
  - Known limitation: the run reads the Markdown object directly, so the
    conditional re-fetch step (`If-None-Match`, `304`) is shown as not measured.
- **Read a document by `content_id`: `GET /content?content_id=`.** Content
  without a canonical URL can be read on the partner plane. It is the same
  `listContentOrGetByUrl` operation as `GET /content?url=`, so one
  `allowed_endpoints` entry grants both, with the same body, `ETag`, `304` and
  billing (a `200` is one usage event, a `304` is none). It returns published
  content only; an unknown or unpublished identifier gets the same `404`. A
  request with neither or both of `url` and `content_id`, or a malformed
  `content_id`, returns `400 invalid_read_selector`.

### Changed

- **Breaking: `x-error-codes` statuses are quoted strings** (`'403'`, `'4XX'`), the same
  form as OpenAPI response keys. A consumer that read them as integers must
  compare strings.
- **Breaking: `DELETE /content/{content_id}` returns `202 Accepted`** with
  `{"content_id", "status": "deleting"}` instead of `204`, and completes in the
  background. A client that checked for exactly `204` must accept `202`.
- **Breaking: `make deploy-backend` and `make deploy-cdk` no longer seed or reset
  content.** They deploy and migrate the taxonomy only. Seeding demo data is the
  separate, optional `make seed-data`. `make seed-data CLEAR_EXISTING=true`
  resets first; only `true`, `1` or `yes` counts, where before any non-empty
  value, including `false`, did.
- **Pipeline Markdown is a structured document for agents** (Markdown schema
  `1.1`). Each document has up to six parts, in this order: the title; a metadata
  list (`Author`, `Publisher`, `Published`, `Updated`, `Source`); a
  `Summary (generated)` blockquote; the article; a
  `Frequently asked questions (generated)` section with up to 10 questions; and
  a `Source: {title} by {author}, {date}. {url}` attribution line. A part with
  nothing to show is left out, and the generated parts are left out when
  classification fails.
  - Content records and `GET /content` gain `publisher` and `modified_date`.
  - The pipeline writes the document once, in a new step after enrichment. An
    ingest whose enrichment fails leaves no Markdown object, and a re-ingest of
    an unchanged source can change the document's `ETag`. Stored documents keep
    their version until they are next ingested.
  - HTML extraction keeps the article and drops the page around it: ads, share
    menus, promotions, "related" blocks, embedded widgets and same-site link
    lists are removed. The title is the article's visible headline, and the
    author, dates and publisher are also read from JSON-LD and from the
    frontmatter of Markdown inputs. Image alt text no longer runs into the
    surrounding text.
- **Every `POST /query` result carries a read reference.** Each standard result
  has an absolute `url` the caller can follow with the credential that made the
  query: `GET /content?url=` (partner key, canonical URL),
  `GET /content?content_id=` (partner key, no canonical URL) or
  `GET /content/{content_id}` (Cognito). Results also carry `canonical_url`.
  Inline `content` is additional data only.
- **CoMP Packages describe the real read contract.** Each Text asset carries
  `ext.content_id` and `ext.url`, and `Package.retrieval` points at
  `GET <base>/content` with `auth` 1 (API key) on the partner plane or 2
  (Cognito) on the internal plane. The pending-license Package points at the
  same read.
- **`GET /content?url=` returns published content only**, matching discovery and
  the `content_id` read. A record that is not published gets the same `404` as an
  unknown URL and is not billed.
- **Response links use a trusted origin.** Read references and CoMP Package links
  come from the published partner API URL or API Gateway's request context,
  never the request's `Host` header. A request without a trusted origin fails
  with `500` before any discovery runs.
- **The single-document read `404` carries an `error_code`.**
  `GET /content?url=` and `?content_id=` for unpublished or unknown content also
  return `error_code: content_not_found`, registered in `x-error-codes`. The
  existing body fields are unchanged.
- **A record that is being deleted can't be updated or reprocessed.** `PUT` and
  `POST /content/{content_id}/reprocess` return `409` for it. Reprocessing an
  unknown `content_id` returns `404` instead of queueing a job. An ingest can't
  overwrite a record that is being deleted; ingest again once the delete has
  completed.
- **Delete behavior.** Partner reads return `404` from the moment a delete is
  accepted, the dashboard lists the record as `deleting` until it completes
  (usually within seconds), and deleting an already-`deleting` record retries the
  cleanup.
- **Resetting sample data deletes through the same delete.**
  `make seed-data CLEAR_EXISTING=true` soft-deletes every content record,
  including content ingested through `ingest/`. The deindexer removes each
  record's search document and stored objects, and the reset waits for it before
  reseeding.
- **A matching conditional read no longer calls S3.** The pipeline saves the
  stored Markdown object's S3 `ETag` on the content record (`md_etag`), and a
  `GET /content` whose `If-None-Match` matches is answered `304` from the record
  alone. The `ETag` value is unchanged, and records saved earlier keep the
  previous behavior until they are next ingested.
  - While a document is being re-ingested, a reader holding the previous `ETag`
    keeps getting `304` until the pipeline saves the new record, usually under a
    minute. If that run fails after writing the new Markdown, this lasts until
    the document is ingested again.
  - To change a document's Markdown, upload its source under `ingest/` again
    rather than editing or deleting the object in S3.
- **Old object versions in the content bucket expire after 30 days.** The bucket
  is versioned, so deleted and replaced objects previously kept every earlier
  version indefinitely.

### Fixed

- **Partner content reads are billed again.** A `200` from `GET /content?url=`
  recorded no usage event on a fresh deployment, so billable reads went
  unmetered. The read itself always worked. Three independent causes are fixed:
  - No telemetry Connection was provisioned. A deployment now creates the
    canonical `telemetry` → `dynamo_usage_events` Connection.
  - A Connection that needs no credential could not resolve, because the registry
    asked Secrets Manager for a secret even when none was declared. A declared but
    unreadable secret still fails closed.
  - The reading roles were granted a different secrets prefix than the one the
    Connections API writes (`cm-connections/`), so a Connection with a credential
    was refused. Both roles now read the correct prefix.

  **Operator action, one deployment only.** If you created a telemetry
  Connection by hand, its secret probably sits outside `cm-connections/` and
  will stop resolving. The deployment step detects this, explains it, prints the
  command that fixes it (clearing the secret reference, since this sink uses its
  execution role), and exits non-zero so the problem is not missed.
- **Deleting content removes everything stored for it.** A delete previously
  removed only the record, its embedding and its jobs, so the stored Markdown,
  source, pipeline intermediates and search document stayed and `POST /query`
  kept returning the deleted document. A content deindexer in the pipeline stack
  now removes all of them, and the record last. A failure retries, then lands in
  a dead-letter queue with a CloudWatch alarm; the record stays `deleting`, so
  deleting again completes it.
  - For the few seconds before the deindexer runs, the document can still appear
    in `POST /query`.
  - Deleting a document while an ingest of it is running can leave the new copy
    incomplete; delete it again once the ingest has finished.
  - The dashboard backend still has no access to the search collection. Its only
    new permission is `events:PutEvents` on the default event bus.
- **Single-document reads return the document's Markdown.** `GET /content?url=`,
  `GET /content?content_id=` and the internal `GET /content/{content_id}`
  returned an empty body for every pipeline-ingested document. They now serve the
  stored Markdown, the same bytes native edge delivery serves.
  - The `ETag` also changes when the stored document changes, so a reprocessed
    document is never answered with a stale `304`.
  - If the stored document can't be returned, the partner read answers `500`,
    naming no storage location and not billed, instead of an empty `200`. The
    internal read still returns the record's details with an empty
    `content_text`.
- **Pipeline documents report their publication date.** `metadata.pubdate` on
  `GET /content` and `publication_date` on the internal reads were empty for
  pipeline-ingested documents.
- **The publisher dashboard's content list works again.** On the internal API,
  `GET /content` was sent to the partner read and returned
  `403 invalid_api_key`. The internal API now serves every `/content` method
  through the dashboard backend. A Cognito `GET /content?url=` or `?content_id=`
  returns the same document, `ETag` and `304` as the partner read, is not billed,
  and carries CORS headers that expose `ETag`. The partner API is unchanged.
- **`make seed-data` no longer fails at random on a duplicate tag.** A generated
  tag set could contain a repeated value and DynamoDB rejected it. Every set now
  holds each value once, and number sets compare by value.

### Security

- **Removed the third-party geo-IP lookup.** The access rules engine Lambda
  called a free third-party geo-IP service over plain HTTP. It now makes no
  outbound request. When a rule configures geographic restrictions, the check
  denies access and points to CloudFront geographic restrictions or AWS WAF geo
  match. With no geographic restrictions configured, behavior is unchanged.
- **Release package excludes the CI pip cache.** `.cache/` is now git-ignored and
  excluded from the release package.

### Documentation

- README states that the Guidance supports one deployment per AWS account and
  Region, and lists the fixed resource names that cause the limit.

## [0.1.1] - 2026-10-31

### Changed

- **Partner-plane authentication and gateway rejections carry an
  `error_code`.** Partners can branch on the code for these rejections, and
  every code is listed in `x-error-codes` in `openapi-spec.yaml`. Other handler
  errors, such as a malformed request, still return `error` and `message`
  without one:
  - The generic `401` answers `authentication_required`. A key created
    moments ago can return it while API Gateway propagates the key.
  - API Gateway's own rejections answer `request_blocked` (AWS WAF),
    `access_denied`, `route_not_found` (no such path or method),
    `request_rejected` (any other 4xx) and `gateway_error` (5xx).

  HTTP statuses are unchanged. Only the response bodies changed. **Breaking for
  gateway rejections:** API Gateway's default body was `{"message": ...}`, and
  these responses now return `{"error": ..., "error_code": ...}`, so a client
  that read `message` must read `error` and `error_code` instead.

### Fixed

- **A partner suspended moments ago is no longer admitted on a stale read.**
  Partner status was read with an eventually consistent DynamoDB read, which
  could still return the old `active` status for a short time after
  suspension. It is now read with a strongly consistent read.

### Security

- **`follow-redirects` 1.16.2 in the demo UI.** The UI's lockfile pinned
  `follow-redirects` 1.16.1, a runtime dependency of `axios`. That version is
  affected by a high-severity advisory published on 2026-10-30:
  [GHSA-mrwp-f3q8-3g9p](https://github.com/advisories/GHSA-mrwp-f3q8-3g9p)
  (a `sensitiveHeaders` array created in another JavaScript realm is
  discarded, so a custom credential header can follow a cross-origin
  redirect). The lockfile now pins the patched 1.16.2. No other dependency
  changes. The built UI was not exposed: in browser builds, axios replaces its
  Node HTTP adapter, the only code that imports `follow-redirects`, with a null
  stub, and the UI does not import axios.

## [0.1.0] - 2026-10-26

### Added

- **Usage-event metering with a single emit choke point.** Every billable
  content access — URL retrieval, CoMP discovery, standard discovery, and a
  partner self-report — is recorded through one `emit_usage_event` call site, so
  there is exactly one billable-event source. A static guardrail asserts no
  other module constructs a `UsageEvent`, so the choke point cannot be bypassed
  by a later edit. The `cm-usage-events` table is the canonical raw store:
  append-optimized and partner-and-day bucketed
  (`PK = PARTNER#<partner_id>#<YYYY-MM-DD>`, `SK = <accessed_at>#<request_id>`),
  with a `NEW_IMAGE` stream for the derived analytics view and a 90-day TTL.
  IAM is append-only — `PutItem` and `BatchWriteItem`, nothing else — because an
  immutable event store the emitter could rewrite would not be an audit trail.
- **Per-request entitlement gate** and entitlements persisted as partner-child
  items, so the authorization decision is one item-collection read rather than
  an application-side join.
- **Publisher licence management surface**, replacing the placeholder page:
  list with a status filter, create, view, update, soft revoke, renew, validate,
  and version history. The status filter offers only `active` and `revoked` —
  `draft` and `archived` have no writer in the runtime, so offering them would
  yield filter options that can only ever return an empty list.
- **Licence renewal** (`POST /licenses/{license_id}/renew`), the
  `revoked -> active` transition, which the contract previously had no path for.
- **URL-addressable content retrieval** (`GET /content?url=<canonical>`). An
  agent can fetch a document by the URL it cites rather than by an opaque
  `content_id`, resolved through a new `url_hash-index` GSI. Supports
  conditional requests: `If-None-Match` returns `304` when the content is
  unchanged, and a `304` is **not** a billable access — the early return sits
  above the emit, so the exemption is a control-flow guarantee rather than a
  condition inside the emitter. Moved content returns `404` with `redirectUrl`
  as RFC 9457 problem details. Consumer guidance documents the
  confirm-before-follow policy: an agent acting for a human surfaces a
  `redirectUrl` as a candidate for confirmation, never silently re-fetching it.
  Citation spellings are tolerated: host and scheme case, a query string or
  fragment, and a trailing slash all resolve to the same document. The scheme
  is deliberately not collapsed — an `http://` citation does not resolve
  content published over `https://`. The trailing slash is tolerated on the
  read path rather than normalized away, because it is load-bearing in the
  edge address (`/a/b/` is `.../a/b/index.md`, `/a/b` is `.../a/b.md`), so
  collapsing it would change the Shape 1 object key and break native edge
  delivery.

### Fixed

- **CoMP function codes disagreed between two modules.** `license_service`
  declared its own table (`1=Search/Discovery, 4=Evaluation, 5=Analysis`) while
  `comp_query_service` declared the canonical IAB list
  (`4=ai-index, 5=search`). A licence authored under one reading was
  mis-authorized under the other on the authorization path. The divergent table
  is deleted and the canonical one imported, so the two can no longer drift.
  The `evaluation`/`eval`/`analysis` aliases are removed rather than silently
  repointed at a different meaning — an unrecognized `intended_use` now returns
  `400` listing the valid values.
- **Revoking a licence destroyed its history.** `archive_license` hard-deleted
  every version; it now appends a `revoked` version, so the authored history
  stays auditable.
- **Revoked licences still appeared as active.** `list_licenses` applied the
  status filter in the scan and grouped by `license_id` second, so it picked the
  latest of the *matching* rows rather than the licence's current version: a
  revoked licence whose earlier versions were active appeared under
  `status=active` at a stale version, and under `status=revoked` at the same
  time. The scan is now unfiltered, the latest version resolved, and the status
  filter applied to that.
- **Licence create and revoke responses were discarded by browsers.** The `201`
  and both `204` responses carried no CORS header, so a browser rejected them
  as a network error after the write had already committed — a false failure
  that invited duplicate submissions.
- **`GET /content` was unreachable by partner keys.** The path had no API
  Gateway resource of its own, so it fell through to the Cognito `/{proxy+}`
  plane and every partner-key request was rejected at the gateway before any
  handler ran. It is now partner-explicit for the `GET` only; `POST /content`
  (publisher ingest) stays on the Cognito plane.

### Changed

- **Licence audience labels now match the gate.** Every licence operation was
  labelled `x-audience: partner`, including the writes. The writes already
  carried `CognitoAuth`-only security, so no partner key ever had a route to
  one — but the partner-facing documentation advertised operations the gate
  refused. Only the single-document read (the public `licenseUrl` an agent
  dereferences) and native-key `validate` are partner-facing; the collection
  read, version history and every write are internal-plane.
- **`GET /content` is partner-facing** (`x-audience: partner`, tag
  `Content Access`). It was labelled internal, which excluded it from the
  partner-facing document that AI operators read — the opposite of its intent.
- Discovery metering is consolidated onto `/query`, matching the endpoint
  consolidation. The `comp_search` and `search` content-type markers are
  retained but unused: the raw event store is immutable, so rows already written
  under those labels cannot be re-labelled.

## [0.0.2] - 2026-10-20

### Fixed

- **Partner-plane integration tests no longer fail on API key propagation.**
  A key that API Gateway has just associated with a usage plan can take a few
  minutes to bind at every edge node. In that window the edge either rejects
  it (`403 invalid_api_key`) or accepts it without the key id, which the
  handler answers with its generic 401. The tests misread both shapes as
  product verdicts. They now:
  - Poll until the response they assert on settles, and assert on that same
    response.
  - Retry past an edge rejection that arrives after admitted calls.
  - Bind the quota key to an active throwaway partner, so an admitted call
    proves the key resolved.
  - Fail hard when quota enforcement is missing.
  - Mark the test `xfail` only when the edge still had not bound the key after
    the propagation window.

- **The `llms.txt` bootstrap fallback is refreshed on deployment.** An
  installation that had not yet published a managed `llms.txt` served the
  bootstrap object written by its first deployment. Later deployments never
  updated that object, so it kept advertising the retired `/search` endpoint.
  Deployments now reconcile it with the bundled `assets/bootstrap-llms.txt`:
  - It is rewritten only when it is an unmodified copy written by an earlier
    deployment.
  - Operator-edited content is preserved.
  - Published manifest revisions are never touched.

  The `LlmsTxtBootstrapAction` stack output reports what each deployment did,
  and the integration tests now fail if the served `llms.txt` links `/search`
  on either plane.

- **The security scan runs once.** `make security-scan` (and so the pre-push
  hook) and the CI `security-scan` job called `ash scan`. In ASH v3.1.4 that
  runs the whole scan twice, because the scan is registered as both the default
  command and the `scan` subcommand. The CI job took about 32 minutes and could
  hit its 35-minute timeout. Both now call `ash` directly, which runs one scan
  and fails the same way on findings.

### Security

- **`follow-redirects` 1.16.1 in the demo UI.** The UI's lockfile pinned
  `follow-redirects` 1.16.0, a runtime dependency of `axios`. That version is
  affected by two high-severity advisories published on 2026-10-20:
  [GHSA-8r9p-f939-6h3c](https://github.com/advisories/GHSA-8r9p-f939-6h3c)
  (credential headers can be exposed) and
  [GHSA-q3jh-wrc6-jgq8](https://github.com/advisories/GHSA-q3jh-wrc6-jgq8)
  (cross-origin credential stripping can be bypassed). The lockfile now pins
  the patched 1.16.1. No other dependency changes. The built UI was not
  exposed: in browser builds, axios replaces its Node HTTP adapter, the only
  code that imports `follow-redirects`, with a null stub, and the UI does not
  import axios.

## [0.0.1] - 2026-10-14

Initial public release. This is the first open-source release of the Guidance;
there is no prior public history.

### Highlights

This release ships an end-to-end reference architecture for publisher content
monetization and AI-agent integration on AWS, implementing the IAB Tech Lab
Content Monetization Protocol (CoMP) v1.0:

- **Content ingestion & GEO enrichment pipeline** — Step Functions orchestration
  (extract → classify → enrich → embed → index) over Amazon Bedrock and
  OpenSearch Serverless, producing deterministic Markdown renditions and IAB
  Content Taxonomy 3.1 classification.
- **Dual-plane API** — a partner plane (AI operators / external agents) secured
  by API Gateway native API keys and usage plans, and an internal plane
  (publishers) secured by Amazon Cognito. A single OpenAPI contract is the
  source of truth for both.
- **Content discovery & retrieval** — semantic + keyword search over the
  enriched corpus, CoMP discovery (`/info`, `/.well-known/comp.json`), and a
  managed `llms.txt` discovery manifest with a reviewable generate/publish
  lifecycle.
- **Monetization** — versioned, IAB-category-aware pricing rules; RSL (Really
  Simple Licensing) terms; and pay-per-access enforcement at the edge via AWS
  WAF.
- **Partner lifecycle** — create, rotate, suspend, and retire partner API keys
  with per-tier usage-plan quotas.
- **Publisher portal** — a React dashboard for content, pricing, licensing,
  partner, and analytics management, including an embedded analytics assistant
  on Amazon Bedrock AgentCore.
- **Infrastructure as code** — the full stack is defined in AWS CDK and deploys
  as a set of composable stacks.
