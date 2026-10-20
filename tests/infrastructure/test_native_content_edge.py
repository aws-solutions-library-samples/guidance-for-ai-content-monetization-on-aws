"""CDK assertions for the default-off native content edge."""

import json

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

ENV = Environment(account="000000000000", region="us-west-2")


def _template(*, enabled=False):
    app = App(
        context={
            "iab": {
                "nativeEdgeEnabled": enabled,
            }
        }
    )
    return Template.from_stack(
        ContentMonetizationStack(app, "NativeContentEdgeStack", env=ENV)
    )


def test_distribution_is_disabled_by_default_with_api_and_private_s3_origins():
    template = _template()

    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": "iab-llm-content-000000000000-us-west-2",
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Enabled": False,
                "Origins": Match.array_with(
                    [
                        Match.object_like({"Id": "CentralApiOrigin"}),
                        Match.object_like(
                            {
                                "Id": "PrivateMarkdownOrigin",
                                "OriginAccessControlId": Match.any_value(),
                            }
                        ),
                    ]
                ),
            }
        },
    )
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)


def test_content_behavior_is_path_only_get_head_and_projects_license_header():
    rendered = _template().to_json()
    distributions = [
        value
        for value in rendered["Resources"].values()
        if value["Type"] == "AWS::CloudFront::Distribution"
    ]
    assert len(distributions) == 1
    config = distributions[0]["Properties"]["DistributionConfig"]
    behavior = next(
        item for item in config["CacheBehaviors"] if item["PathPattern"] == "content/*"
    )

    assert behavior["AllowedMethods"] == ["GET", "HEAD"]
    assert behavior["CachedMethods"] == ["GET", "HEAD"]
    assert behavior["TargetOriginId"] == "PrivateMarkdownOrigin"
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    assert "OriginRequestPolicyId" not in behavior
    assert len(behavior["FunctionAssociations"]) == 1
    association = behavior["FunctionAssociations"][0]
    assert association["EventType"] == "viewer-response"
    assert "Fn::GetAtt" in association["FunctionARN"]


def test_content_cache_policy_excludes_headers_cookies_and_query_strings():
    template = _template()
    template.has_resource_properties(
        "AWS::CloudFront::CachePolicy",
        {
            "CachePolicyConfig": {
                "DefaultTTL": 3600,
                "MinTTL": 0,
                "MaxTTL": 86400,
                "ParametersInCacheKeyAndForwardedToOrigin": {
                    "CookiesConfig": {"CookieBehavior": "none"},
                    "HeadersConfig": {"HeaderBehavior": "none"},
                    "QueryStringsConfig": {"QueryStringBehavior": "none"},
                },
            }
        },
    )


def test_bucket_policy_scopes_oac_read_to_the_distribution():
    rendered = json.dumps(_template().to_json())

    assert "cloudfront.amazonaws.com" in rendered
    assert "AWS:SourceArn" in rendered
    assert "distribution/" in rendered


def test_distribution_access_logs_and_error_alarm_are_configured():
    template = _template()
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Logging": {
                    "Bucket": Match.any_value(),
                    "Prefix": "native-content-edge/",
                }
            }
        },
    )
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "AlarmName": "cm-native-content-edge-5xx-rate",
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "EvaluationPeriods": 2,
            "Threshold": 1,
            "TreatMissingData": "notBreaching",
        },
    )


def test_no_cloudfront_kvs_resource_exists():
    template = _template()
    template.resource_count_is("AWS::CloudFront::KeyValueStore", 0)
    template.resource_count_is("AWS::CloudFront::Function", 2)
    rendered = json.dumps(template.to_json())
    assert "{{CENTRAL_ORIGIN_ID}}" not in rendered
    assert "export function" not in rendered
    assert "export const" not in rendered
    assert "selectRequestOriginById" in rendered


def test_enablement_is_impossible_before_issue_29_gate_integration():
    with pytest.raises(
        ValueError,
        match="remains disabled until issue #29",
    ):
        _template(enabled=True)
