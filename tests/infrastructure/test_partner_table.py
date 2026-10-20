"""Infrastructure assertions for the partner table shape (spec: native-apikey-auth-migration, task 2.3).

The partner table is keyed on the partner identity (``PK``/``SK``) with a GSI that resolves an
API Gateway key id to its partner. The former credential-hash key and ``partner-id-index`` GSI
must not come back. The table has no fixed physical name so a key-schema change is a normal
CloudFormation replacement; consumers read the name from ``PARTNER_TABLE_NAME``.
"""

from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack


def _partner_table_resource(template: Template) -> dict:
    tables = {
        lid: res for lid, res in template.find_resources("AWS::DynamoDB::Table").items() if lid.startswith("PartnerTable")
    }
    assert len(tables) == 1, f"expected exactly one PartnerTable resource, found {list(tables)}"
    return next(iter(tables.values()))


def _partner_table(template: Template) -> dict:
    return _partner_table_resource(template)["Properties"]


class TestPartnerTableShape:
    def setup_method(self):
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        self.template = Template.from_stack(stack)
        self.props = _partner_table(self.template)

    def test_keyed_on_partner_identity(self):
        assert self.props["KeySchema"] == [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ]

    def test_api_key_id_index_projects_resolution_attributes_only(self):
        gsis = self.props["GlobalSecondaryIndexes"]
        assert [g["IndexName"] for g in gsis] == ["api-key-id-index"]
        gsi = gsis[0]
        assert gsi["KeySchema"] == [{"AttributeName": "api_key_id", "KeyType": "HASH"}]
        assert gsi["Projection"]["ProjectionType"] == "INCLUDE"
        assert sorted(gsi["Projection"]["NonKeyAttributes"]) == ["partner_id", "status", "usage_plan_id"]

    def test_no_credential_hash_or_legacy_index(self):
        attr_names = {a["AttributeName"] for a in self.props["AttributeDefinitions"]}
        assert "partner_key_hash" not in attr_names
        assert attr_names == {"PK", "SK", "api_key_id"}
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            Match.not_(
                {"GlobalSecondaryIndexes": Match.array_with([Match.object_like({"IndexName": "partner-id-index"})])}
            ),
        )

    def test_dev_lifecycle_settings_kept(self):
        assert self.props["BillingMode"] == "PAY_PER_REQUEST"
        assert self.props["PointInTimeRecoverySpecification"] == {"PointInTimeRecoveryEnabled": True}
        resource = _partner_table_resource(self.template)
        assert resource["DeletionPolicy"] == "Delete"

    def test_no_fixed_physical_name(self):
        # A generated name is what lets a future key-schema change deploy as a replacement.
        assert "TableName" not in self.props

    def test_lambdas_receive_the_table_name(self):
        envs = [
            fn["Properties"]["Environment"]["Variables"]
            for fn in self.template.find_resources("AWS::Lambda::Function").values()
            if "Environment" in fn["Properties"] and "PARTNER_TABLE_NAME" in fn["Properties"]["Environment"]["Variables"]
        ]
        assert envs, "no Lambda receives PARTNER_TABLE_NAME"
        for env in envs:
            assert env["PARTNER_TABLE_NAME"]["Ref"].startswith("PartnerTable")
            assert env["PARTNERS_TABLE_NAME"] == env["PARTNER_TABLE_NAME"]
