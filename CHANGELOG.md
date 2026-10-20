# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
