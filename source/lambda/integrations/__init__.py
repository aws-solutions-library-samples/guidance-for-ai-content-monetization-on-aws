# Integrations_Layer (GitLab #35): additive home for integration ports and adapters.
#
# Grouped by domain (content / marketplace / telemetry / access), each with a `port.py`
# beside its adapter files, plus `registry.py`. Ports depend on nothing external
# (no boto3, no vendor SDK, no cross-domain import) — the hexagonal dependency direction.
# See .kiro/specs/integration-adapters/ and
# internal-assets/analysis/integration-adapter-pattern.md.
