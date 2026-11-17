"""CDK assertion tests for the ``cm-usage-events`` raw usage-event table.

license-lifecycle-usage-events task 9.1 (Requirements 8.3, 8.4). Follows the pattern in
``tests/infrastructure/test_analytics_table.py``: the stack is synthesized in a fresh
``aws_cdk.App`` and asserted via ``aws_cdk.assertions.Template``.

What is pinned here:

* the table exists under the deployed name ``cm-usage-events`` with the append-optimized
  ``PK``/``SK`` schema and no GSI (write amplification stays off the hot append path);
* TTL is enabled on the ``ttl`` attribute (Req 8.3 — the raw store is TTL'd, and the name
  matches what the folded legacy ``iab-access-logs`` writer already sets);
* the stream is enabled with ``NEW_IMAGE`` so #16 can derive ``cm-analytics`` from it
  without reading each item back (Req 8.4);
* ``USAGE_EVENTS_TABLE_NAME`` reaches the Lambdas, and ``cm-analytics`` keeps its own
  separate env var (the derived view is not the raw store);
* the emitters hold **append-only** grants on this table — no read, update, or delete.
"""

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

USAGE_EVENTS_TABLE_NAME = "cm-usage-events"
TEST_REGION = "us-west-2"


@pytest.fixture(scope="module")
def template() -> Template:
    """The synthesized ``ContentMonetizationStack`` template."""
    app = App()
    # Explicit env so self.region resolves to a concrete string: the shared
    # Lambda role name is region-suffixed, and without an env it would render
    # as an unresolved Fn::Join token instead of a plain RoleName.
    stack = ContentMonetizationStack(
        app, "TestStack", env=Environment(account="000000000000", region=TEST_REGION)
    )
    return Template.from_stack(stack)


def _usage_events_table(template: Template) -> dict:
    """The single ``cm-usage-events`` table resource's properties."""
    tables = template.find_resources(
        "AWS::DynamoDB::Table",
        {"Properties": {"TableName": USAGE_EVENTS_TABLE_NAME}},
    )
    assert len(tables) == 1, f"expected exactly one {USAGE_EVENTS_TABLE_NAME} table, got {len(tables)}"
    return next(iter(tables.values()))["Properties"]


class TestUsageEventsTable:
    """The raw store itself (Req 8.3)."""

    def test_table_created_with_deployed_name(self, template: Template):
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": USAGE_EVENTS_TABLE_NAME,
                "BillingMode": "PAY_PER_REQUEST",
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        )

    def test_append_optimized_key_schema(self, template: Template):
        """PK = PARTNER#<id>#<day>, SK = <accessed_at>#<request_id> — unique per event."""
        props = _usage_events_table(template)

        assert props["KeySchema"] == [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ]
        assert props["AttributeDefinitions"] == [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ]

    def test_no_secondary_indexes(self, template: Template):
        """No GSI: cross-partner/content aggregation is the #16 derived view's job (Req 8.4)."""
        props = _usage_events_table(template)

        assert "GlobalSecondaryIndexes" not in props
        assert "LocalSecondaryIndexes" not in props

    def test_ttl_enabled_on_ttl_attribute(self, template: Template):
        """Req 8.3 — the raw store is TTL'd; the attribute name matches the folded writer."""
        props = _usage_events_table(template)

        assert props["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}

    def test_stream_enabled_with_new_image(self, template: Template):
        """Req 8.4 — #16 derives cm-analytics from this stream.

        NEW_IMAGE, not NEW_AND_OLD_IMAGES: a raw event is immutable and append-only, so the
        old image is always null. Not KEYS_ONLY either: the aggregator needs price_charged /
        currency / content_type in the record rather than reading each item back.
        """
        props = _usage_events_table(template)

        assert props["StreamSpecification"] == {"StreamViewType": "NEW_IMAGE"}


class TestUsageEventsTableWiring:
    """Env var to the Lambdas with a fallback default matching the deployed name."""

    def test_table_name_env_var_reaches_lambdas(self, template: Template):
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Environment": {
                    "Variables": {
                        "USAGE_EVENTS_TABLE_NAME": {"Ref": Match.any_value()},
                        "USAGE_EVENTS_TTL_DAYS": Match.any_value(),
                    }
                }
            },
        )

    def test_raw_store_env_var_is_distinct_from_derived_analytics_view(self, template: Template):
        """cm-analytics is a derived view (Req 8.4), so it keeps its own separate env var."""
        functions = template.find_resources(
            "AWS::Lambda::Function",
            {
                "Properties": {
                    "Environment": {"Variables": {"USAGE_EVENTS_TABLE_NAME": Match.any_value()}}
                }
            },
        )
        assert functions, "no Lambda received USAGE_EVENTS_TABLE_NAME"

        for fn in functions.values():
            env = fn["Properties"]["Environment"]["Variables"]
            assert env["USAGE_EVENTS_TABLE_NAME"] != env["ANALYTICS_TABLE_NAME"]

    def test_table_name_exposed_as_stack_output(self, template: Template):
        template.has_output("UsageEventsTableName", Match.any_value())


class TestUsageEventsTableGrants:
    """Least privilege: an emitter appends, it does not read, mutate, or delete."""

    def _usage_events_statements(self, template: Template) -> list:
        """Every IAM policy statement, on any role, that names this table.

        Phase-2 hardening replaced the single shared Lambda execution role with per-function
        least-privilege roles, so the grant is no longer on one findable role — it is on each
        emitting function's own role (partner_api, ui_backend). Collecting across every
        AWS::IAM::Policy keeps the assertion about the GRANT rather than about which role
        happens to hold it, so adding a third emitter cannot slip a read/delete grant past it.
        """
        matched = []
        for policy in template.find_resources("AWS::IAM::Policy").values():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                resources = statement.get("Resource")
                if not isinstance(resources, list):
                    resources = [resources]
                if any(self._targets_usage_events(resource) for resource in resources):
                    matched.append(statement)
        return matched

    @staticmethod
    def _targets_usage_events(resource) -> bool:
        """True when a policy Resource entry is the UsageEventsTable ARN reference."""
        if not isinstance(resource, dict):
            return False
        arn = resource.get("Fn::GetAtt")
        return isinstance(arn, list) and arn[0].startswith("UsageEventsTable") and arn[1] == "Arn"

    def test_append_grant_present(self, template: Template):
        statements = self._usage_events_statements(template)
        assert statements, "no IAM statement grants access to the usage-events table"

        granted = {action for statement in statements for action in statement["Action"]}
        assert granted == {"dynamodb:PutItem", "dynamodb:BatchWriteItem"}

    def test_no_read_update_or_delete_granted(self, template: Template):
        """A raw billable event must not be readable-back, mutable, or deletable."""
        granted = {
            action
            for statement in self._usage_events_statements(template)
            for action in statement["Action"]
        }

        forbidden = {
            "dynamodb:GetItem",
            "dynamodb:BatchGetItem",
            "dynamodb:Query",
            "dynamodb:Scan",
            "dynamodb:UpdateItem",
            "dynamodb:DeleteItem",
            "dynamodb:*",
        }
        assert not granted & forbidden

    def test_grant_is_scoped_to_the_table_not_an_index(self, template: Template):
        """No GSI exists, so no /index/* resource should be granted."""
        for statement in self._usage_events_statements(template):
            resources = statement["Resource"]
            if not isinstance(resources, list):
                resources = [resources]
            for resource in resources:
                assert "Fn::Join" not in resource, "unexpected /index/* style grant"


if __name__ == "__main__":
    pytest.main([__file__])
