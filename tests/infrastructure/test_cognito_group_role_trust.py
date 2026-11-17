"""Every Cognito group role trusts THIS identity pool, with a real pool id in the ``aud`` condition.

Regression guard for a bug found while testing #10 as a group member: the Administrators,
Publishers and Viewers roles were created with the literal string ``"${aws:cognito-identity-pool-id}"``
as their ``cognito-identity.amazonaws.com:aud`` trust condition. That is not a CloudFormation
intrinsic and was never substituted, so STS could never match it. Because the identity pool's
``RoleMappings`` are of type ``Token``, any user with a ``cognito:groups`` claim was mapped to the
group role, the assume-role failed, ``GetCredentialsForIdentity`` returned 400, Amplify's
``fetchAuthSession()`` threw, and the portal sent every API request without an Authorization header
(401 on all routes). Group-less users hit the plain AuthenticatedRole and worked -- which is exactly
why automated tests with a throwaway user never caught it.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infrastructure.cognito_stack import CognitoStack

GROUP_ROLE_IDS = ("AdminRole", "PublisherRole", "ViewerRole")


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = CognitoStack(app, "CognitoTest", env=cdk.Environment(account="111122223333", region="us-west-2"))
    return Template.from_stack(stack)


def _roles(template: Template) -> dict:
    return template.find_resources("AWS::IAM::Role")


def _group_role_trusts(template: Template) -> dict[str, dict]:
    out = {}
    for logical_id, res in _roles(template).items():
        for gid in GROUP_ROLE_IDS:
            if logical_id.startswith(gid):
                out[gid] = res["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    return out


def test_all_three_group_roles_exist(template):
    assert set(_group_role_trusts(template)) == set(GROUP_ROLE_IDS)


@pytest.mark.parametrize("gid", GROUP_ROLE_IDS)
def test_group_role_aud_is_the_identity_pool_ref_not_a_literal_placeholder(template, gid):
    stmt = _group_role_trusts(template)[gid]
    aud = stmt["Condition"]["StringEquals"]["cognito-identity.amazonaws.com:aud"]
    # A CloudFormation Ref to the identity pool resource -- never a plain string.
    assert isinstance(aud, dict) and "Ref" in aud, f"{gid} aud must be a Ref to the identity pool, got {aud!r}"
    assert aud["Ref"].startswith("UIIdentityPool"), f"{gid} aud refs {aud['Ref']!r}, not the identity pool"
    assert "${aws:cognito-identity-pool-id}" not in json.dumps(stmt)


@pytest.mark.parametrize("gid", GROUP_ROLE_IDS)
def test_group_role_trust_matches_the_working_authenticated_role(template, gid):
    """Same principal, action and amr condition as the AuthenticatedRole that was always correct."""
    stmt = _group_role_trusts(template)[gid]
    assert stmt["Principal"] == {"Federated": "cognito-identity.amazonaws.com"}
    assert stmt["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert stmt["Condition"]["ForAnyValue:StringLike"] == {"cognito-identity.amazonaws.com:amr": "authenticated"}


def test_no_role_in_the_stack_carries_the_placeholder(template):
    """Belt and braces: the broken literal must not appear in ANY role's trust policy."""
    for logical_id, res in _roles(template).items():
        doc = json.dumps(res["Properties"]["AssumeRolePolicyDocument"])
        assert "${aws:cognito-identity-pool-id}" not in doc, logical_id


def test_identity_pool_is_created_before_the_groups_depend_on_it(template):
    """The groups' roles Ref the pool, so the pool must be a real resource in the same template."""
    pools = template.find_resources("AWS::Cognito::IdentityPool")
    assert len(pools) == 1
    (pool_id,) = pools
    for gid, stmt in _group_role_trusts(template).items():
        assert stmt["Condition"]["StringEquals"]["cognito-identity.amazonaws.com:aud"] == {"Ref": pool_id}, gid


# --- The group roles are permissionless (!62 review; TM-03; #36) ------------------------------
# Fixing the trust makes the group roles assumable for the first time. Any group member (or
# anything running in their browser) can then exchange the ID token for the role's credentials.
# The portal never signs requests with those credentials -- it sends only the user-pool ID token --
# so the roles must grant nothing, exactly like AuthenticatedRole. Authorization by group is the
# backend's job, on the ``cognito:groups`` claim (#36).


def _group_role_logical_ids(template: Template) -> dict[str, str]:
    out = {}
    for logical_id in _roles(template):
        for gid in GROUP_ROLE_IDS:
            if logical_id.startswith(gid):
                out[gid] = logical_id
    return out


@pytest.mark.parametrize("gid", GROUP_ROLE_IDS)
def test_group_role_has_no_managed_or_inline_policy(template, gid):
    logical_id = _group_role_logical_ids(template)[gid]
    props = _roles(template)[logical_id]["Properties"]
    assert not props.get("ManagedPolicyArns"), (
        f"{gid} must not attach managed policies: {props.get('ManagedPolicyArns')!r}"
    )
    assert not props.get("Policies"), f"{gid} must not carry inline policies: {props.get('Policies')!r}"


def test_no_standalone_policy_attaches_to_a_group_role(template):
    """Also catch grants added later via ``role.add_to_policy`` / ``grant_*`` (AWS::IAM::Policy)."""
    group_ids = set(_group_role_logical_ids(template).values())
    for pid, res in template.find_resources("AWS::IAM::Policy").items():
        attached = {r.get("Ref") for r in res["Properties"].get("Roles", []) if isinstance(r, dict)}
        assert not (attached & group_ids), f"{pid} attaches to group role(s) {attached & group_ids}"
    for pid, res in template.find_resources("AWS::IAM::ManagedPolicy").items():
        attached = {r.get("Ref") for r in res["Properties"].get("Roles", []) if isinstance(r, dict)}
        assert not (attached & group_ids), f"{pid} attaches to group role(s) {attached & group_ids}"
