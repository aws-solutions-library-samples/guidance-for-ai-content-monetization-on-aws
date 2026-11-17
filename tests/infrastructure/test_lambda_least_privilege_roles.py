"""Regression guards for the Lambda least-privilege and container-pin hardening.

Locks three concrete fixes so a security finding cannot silently return:

- Per-function role split: the three request/worker functions — partner_api, ui_backend,
  analytics_aggregation — each run under a DISTINCT execution role; no role is shared
  across them (the former single shared execution role is gone).
- Scoped IAM resources: the scopable statements carry no `Resource: "*"` — DynamoDB, S3,
  Bedrock InvokeModel, bedrock-agentcore invoke, wafv2, and lambda:InvokeFunction are
  ARN-scoped. The genuinely-unscopable actions (cloudwatch metric APIs, bedrock/agentcore
  `List*`) are the only `*` statements allowed, and they are asserted to be isolated to those
  actions (not riding alongside a scopable action in the same statement).
- Agent Dockerfile pin: the `FROM` line is digest-pinned and no unpinned `pip install …>=…`
  remains in it.

Fixtures mirror the existing `Template.from_stack(ContentMonetizationStack(...))` pattern
used across tests/infrastructure. (Owning spec: security-hardening-phase3.)
"""

import json
import pathlib
import re

from aws_cdk import App
from aws_cdk.assertions import Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_DOCKERFILE = REPO_ROOT / "source" / "agent" / "Dockerfile"

# The three functions the per-function role split targets, matched by their stack Description.
_TARGET_FN_DESCRIPTIONS = {
    "partner_api": "Partner API Lambda",
    "ui_backend": "UI Backend Lambda",
    "analytics_aggregation": "Analytics Aggregation Lambda",
}

# Actions that AWS does not support resource-level scoping for; a `*` resource on a
# statement is only acceptable when EVERY action in that statement is one of these.
_WILDCARD_ONLY_ACTIONS = {
    "cloudwatch:GetMetricStatistics",
    "cloudwatch:PutMetricData",
    "bedrock:ListFoundationModels",
    "bedrock:GetFoundationModel",
    "bedrock-agentcore:ListAgentRuntimes",
}


def _template() -> Template:
    return Template.from_stack(ContentMonetizationStack(App(), "TestStack"))


def _function_role_logical_id(fn_resource: dict) -> str:
    role = fn_resource["Properties"]["Role"]
    # Lambda Role is `{"Fn::GetAtt": ["<RoleLogicalId>", "Arn"]}`.
    return role["Fn::GetAtt"][0]


def _target_functions(template: Template) -> dict:
    """Map our logical function key -> (lambda logical id, role logical id)."""
    out = {}
    for lid, res in template.find_resources("AWS::Lambda::Function").items():
        desc = res["Properties"].get("Description", "")
        for key, needle in _TARGET_FN_DESCRIPTIONS.items():
            if needle in desc:
                out[key] = (lid, _function_role_logical_id(res))
    return out


def _statements_for_role(template: Template, role_logical_id: str) -> list:
    """All IAM policy statements attached to a role (inline Policies + AWS::IAM::Policy)."""
    statements = []
    # Inline policies on the role resource.
    roles = template.find_resources("AWS::IAM::Role")
    role = roles.get(role_logical_id, {})
    for pol in role.get("Properties", {}).get("Policies", []) or []:
        statements.extend(pol["PolicyDocument"]["Statement"])
    # Standalone AWS::IAM::Policy resources whose Roles reference this role.
    for _, pol in template.find_resources("AWS::IAM::Policy").items():
        refs = [r.get("Ref") for r in pol["Properties"].get("Roles", []) if isinstance(r, dict)]
        if role_logical_id in refs:
            statements.extend(pol["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _as_actions(stmt: dict) -> list:
    a = stmt.get("Action", [])
    return [a] if isinstance(a, str) else list(a)


def _is_wildcard_resource(resource) -> bool:
    # A scalar "*" (never an ARN string or a Fn::* intrinsic / list of ARNs).
    return resource == "*"


def _resource_names_partner_table(resource) -> bool:
    """True if a statement Resource points at the PartnerTable (its ARN or an index of it).

    Synthesized DynamoDB ARNs are `{"Fn::GetAtt": ["PartnerTable…", "Arn"]}` for the table and
    `{"Fn::Join": [..., {"Fn::GetAtt": ["PartnerTable…", "Arn"]}, "/index/*"]}` for a GSI.
    """
    text = json.dumps(resource)
    return "PartnerTable" in text


# ── Per-function role split ─────────────────────────────────────────────────


class TestPerFunctionRoleSplit:
    def setup_method(self):
        self.template = _template()
        self.funcs = _target_functions(self.template)

    def test_all_three_target_functions_present(self):
        assert set(self.funcs) == set(_TARGET_FN_DESCRIPTIONS), (
            f"expected all three target functions, found {sorted(self.funcs)}"
        )

    def test_each_function_has_a_distinct_role(self):
        role_ids = [role for (_, role) in self.funcs.values()]
        assert len(role_ids) == len(set(role_ids)), (
            f"a role is shared across the three functions: {role_ids}"
        )

    def test_no_shared_lambda_execution_role_remains(self):
        # The former shared role used a fixed region-suffixed name; it must be gone.
        for _, res in self.template.find_resources("AWS::IAM::Role").items():
            name = res["Properties"].get("RoleName")
            if isinstance(name, str):
                assert name != "iab-llm-lambda-execution-role-us-west-2", (
                    "the shared lambda_execution_role must not be re-introduced"
                )

    def test_partner_api_can_query_the_partner_table(self):
        # The partner-key authorizer resolves the API key to a partner by querying the
        # partner table's api-key-id-index GSI, then reads the partner META item. A role
        # that cannot Query the partner table makes every partner request a 403
        # "partner_inactive" (the handler swallows the AccessDenied) — a runtime regression
        # the dev-integration gate caught once. Assert the grant is present.
        _, role_id = self.funcs["partner_api"]
        grants_partner_query = False
        for stmt in _statements_for_role(self.template, role_id):
            if stmt.get("Effect") != "Allow":
                continue
            if "dynamodb:Query" not in _as_actions(stmt):
                continue
            resources = stmt.get("Resource")
            resources = resources if isinstance(resources, list) else [resources]
            if any(_resource_names_partner_table(r) for r in resources):
                grants_partner_query = True
                break
        assert grants_partner_query, (
            "partner_api role does not grant dynamodb:Query on the partner table — the "
            "partner-key authorizer will fail and every partner request becomes a 403"
        )


# ── No wildcard resource on scopable statements ─────────────────────────────


class TestScopedWildcards:
    def setup_method(self):
        self.template = _template()
        self.funcs = _target_functions(self.template)

    def test_scopable_statements_carry_no_wildcard_resource(self):
        offenders = []
        for key, (_, role_id) in self.funcs.items():
            for stmt in _statements_for_role(self.template, role_id):
                if stmt.get("Effect") != "Allow":
                    continue
                resources = stmt.get("Resource")
                resources = resources if isinstance(resources, list) else [resources]
                if not any(_is_wildcard_resource(r) for r in resources):
                    continue
                # A `*` resource is only allowed if EVERY action is wildcard-only.
                actions = _as_actions(stmt)
                non_wildcard_only = [a for a in actions if a not in _WILDCARD_ONLY_ACTIONS]
                if non_wildcard_only:
                    offenders.append((key, actions))
        assert not offenders, (
            "scopable actions still ride a Resource:'*' statement (must be ARN-scoped): "
            f"{offenders}"
        )

    def test_wildcard_statements_are_isolated_to_unscopable_actions(self):
        # Every `*` statement must contain ONLY wildcard-only actions — proving the
        # List*/metric actions were isolated into their own statement.
        for key, (_, role_id) in self.funcs.items():
            for stmt in _statements_for_role(self.template, role_id):
                resources = stmt.get("Resource")
                resources = resources if isinstance(resources, list) else [resources]
                if any(_is_wildcard_resource(r) for r in resources):
                    actions = _as_actions(stmt)
                    assert all(a in _WILDCARD_ONLY_ACTIONS for a in actions), (
                        f"{key}: a Resource:'*' statement mixes scopable actions: {actions}"
                    )

    def test_dynamodb_statements_are_arn_scoped(self):
        # Belt-and-suspenders: no DynamoDB action anywhere on the three roles uses `*`.
        for key, (_, role_id) in self.funcs.items():
            for stmt in _statements_for_role(self.template, role_id):
                actions = _as_actions(stmt)
                if any(a.startswith("dynamodb:") for a in actions):
                    resources = stmt.get("Resource")
                    resources = resources if isinstance(resources, list) else [resources]
                    assert not any(_is_wildcard_resource(r) for r in resources), (
                        f"{key}: a dynamodb statement uses Resource:'*'"
                    )


# ── Agent Dockerfile base-image digest pin + no unpinned pip ────────────────


class TestAgentDockerfilePin:
    def setup_method(self):
        self.text = AGENT_DOCKERFILE.read_text()

    def test_from_is_digest_pinned(self):
        from_lines = [ln for ln in self.text.splitlines() if ln.strip().startswith("FROM ")]
        assert from_lines, "no FROM line in the agent Dockerfile"
        assert all("@sha256:" in ln for ln in from_lines), (
            f"agent Dockerfile FROM is not digest-pinned: {from_lines}"
        )

    def test_no_unpinned_pip_floor_install(self):
        # No `pip install <pkg>>=<ver>` (unpinned floor) remains in the Dockerfile.
        # Installing from a pinned requirements file is fine.
        for ln in self.text.splitlines():
            s = ln.strip()
            if s.startswith("RUN") and "pip install" in s and "-r " not in s:
                assert not re.search(r">=\s*\d", s), (
                    f"unpinned pip floor install remains in Dockerfile: {s}"
                )
