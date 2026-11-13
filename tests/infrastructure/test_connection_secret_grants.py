"""CDK guard: the emitting roles can read the secrets the Connections API writes (GitLab #58).

The usage emitter resolves its sink through an active ``cm-connections`` Connection, and the
Registry fails CLOSED when a declared ``secret_ref`` cannot be read. So an IAM scope that does not
cover where Connection secrets actually live does not raise — it silently drops every billable
event, which is exactly how #58 presented.

The two sides that must agree:

* **Writer** — ``ui_backend/routes/connections.py`` creates secrets as
  ``cm-connections/<connection_id>``.
* **Readers** — ``partner_api_role`` (serves ``GET /content?url=``) and ``ui_backend_role`` need
  ``secretsmanager:GetSecretValue`` on that prefix.

Before the fix both read grants were scoped to ``secret:iab-llm-*``, which never matches a
Connection secret. These assertions lock the alignment so the mismatch cannot return, and they
check the *prefix relationship* rather than an exact ARN string so a legitimate refactor of the
ARN construction does not produce a false failure.

Mirrors the ``Template.from_stack(ContentMonetizationStack(...))`` pattern used across
tests/infrastructure.
"""

from __future__ import annotations

import pytest
from aws_cdk import App
from aws_cdk.assertions import Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

#: The prefix ui_backend/routes/connections.py writes Connection secrets under. If this changes,
#: the grants must change with it — that coupling is the point of this test.
CONNECTION_SECRET_PREFIX = "cm-connections/"

#: The roles that resolve a Connection on an emit path and therefore must be able to read its
#: secret. partner_api is the one that serves GET /content?url=, the path #58 was reported against.
EMITTING_ROLE_HINTS = ("PartnerApi", "UIBackend")

GET_SECRET_VALUE = "secretsmanager:GetSecretValue"


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_stack(ContentMonetizationStack(App(), "TestStack"))


def _policy_statements(template: Template) -> list[dict]:
    """Every statement across every IAM policy in the synthesized stack."""
    statements: list[dict] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        doc = policy.get("Properties", {}).get("PolicyDocument", {})
        statements.extend(doc.get("Statement", []))
    return statements


def _actions(statement: dict) -> list[str]:
    action = statement.get("Action", [])
    return [action] if isinstance(action, str) else list(action)


def _resources(statement: dict) -> list:
    resource = statement.get("Resource", [])
    return [resource] if not isinstance(resource, list) else resource


def _resource_text(resource) -> str:
    """Flatten a CDK resource entry (string, Fn::Join, Fn::Sub, ...) to searchable text."""
    return str(resource)


def _secret_read_statements(template: Template) -> list[dict]:
    return [s for s in _policy_statements(template) if GET_SECRET_VALUE in _actions(s)]


def test_a_secret_read_grant_exists(template):
    """Sanity: the emit path's secret-read grant has not been removed outright."""
    assert _secret_read_statements(template), (
        f"no statement grants {GET_SECRET_VALUE}; the Registry fails closed on an unreadable "
        "secret, so removing this grant silently stops all usage metering (#58)"
    )


def test_every_secret_read_grant_covers_the_connection_prefix(template):
    """Each GetSecretValue grant targets where Connection secrets are actually written.

    The failure this prevents is subtle: a grant scoped to some *other* prefix looks like a
    present, correctly-scoped permission in review, but resolves no Connection at runtime.
    """
    offenders = []
    for statement in _secret_read_statements(template):
        covered = any(
            CONNECTION_SECRET_PREFIX in _resource_text(resource)
            for resource in _resources(statement)
        )
        if not covered:
            offenders.append([_resource_text(r) for r in _resources(statement)])

    assert not offenders, (
        f"{GET_SECRET_VALUE} is granted on resources that do not include "
        f"'{CONNECTION_SECRET_PREFIX}': {offenders}. Connection secrets are written as "
        f"'{CONNECTION_SECRET_PREFIX}<connection_id>' by ui_backend/routes/connections.py, so such "
        "a grant cannot read one — the Registry then fails closed and the emit drops the event "
        "with no error (#58)."
    )


def test_no_secret_read_grant_uses_the_stale_iab_llm_scope(template):
    """The pre-fix `iab-llm-*` scope is gone — it never matched a Connection secret.

    Pinned by name because that exact scope was the #58 defect; a revert would otherwise look
    plausible.
    """
    stale = [
        [_resource_text(r) for r in _resources(statement)]
        for statement in _secret_read_statements(template)
        if any("iab-llm-" in _resource_text(r) for r in _resources(statement))
    ]
    assert not stale, (
        f"a {GET_SECRET_VALUE} grant is still scoped to 'iab-llm-*': {stale}. Connection secrets "
        f"live under '{CONNECTION_SECRET_PREFIX}', so this scope grants nothing useful to the "
        "emit path (#58)."
    )


def test_secret_read_grants_are_not_wildcarded(template):
    """Fixing the scope must not widen it to every secret in the account."""
    wildcards = [
        [_resource_text(r) for r in _resources(statement)]
        for statement in _secret_read_statements(template)
        if any(_resource_text(r).strip() == "*" for r in _resources(statement))
    ]
    assert not wildcards, (
        f"{GET_SECRET_VALUE} is granted on '*': {wildcards}. The #58 fix corrects the prefix; it "
        "must not replace a wrong scope with no scope."
    )


def test_the_connection_writer_prefix_and_reader_prefix_are_the_same(template):
    """The write grant and the read grants target one prefix — the alignment #58 broke.

    Asserted as a relationship rather than two independent constants, so the two cannot drift
    apart again without this failing.
    """
    write_prefixes = set()
    read_prefixes = set()
    for statement in _policy_statements(template):
        actions = _actions(statement)
        texts = [_resource_text(r) for r in _resources(statement)]
        if any("secretsmanager:CreateSecret" in a or "secretsmanager:PutSecretValue" in a for a in actions):
            write_prefixes.update(t for t in texts if CONNECTION_SECRET_PREFIX in t)
        if GET_SECRET_VALUE in actions:
            read_prefixes.update(t for t in texts if CONNECTION_SECRET_PREFIX in t)

    assert write_prefixes, (
        "no statement creates Connection secrets under "
        f"'{CONNECTION_SECRET_PREFIX}'; the Connections API cannot be authoring them"
    )
    assert read_prefixes, (
        "no statement reads Connection secrets under "
        f"'{CONNECTION_SECRET_PREFIX}'; the emit path cannot resolve a credentialed Connection"
    )
