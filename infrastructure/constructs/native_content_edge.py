"""Disabled-by-default central CloudFront edge for deterministic Markdown."""

import re
from pathlib import Path
from typing import Optional

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct

_EDGE_SOURCE = Path(__file__).resolve().parents[2] / "source" / "edge"
_CONTENT_RESPONSE_FUNCTION = _EDGE_SOURCE / "central-viewer-response.js"
_PUBLISHER_ROUTER_FUNCTION = _EDGE_SOURCE / "publisher-viewer-request.js"
_PUBLISHER_ORIGIN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _deployable_function_code(path: Path, replacements: Optional[dict] = None) -> str:
    """Convert testable ES-module source into CloudFront's handler script form."""
    source = path.read_text(encoding="utf-8")
    for old, new in (replacements or {}).items():
        source = source.replace(old, new)
    source = source.replace("export function ", "function ")
    source = source.replace("export const ", "const ")
    if "export " in source:
        raise ValueError(f"Unsupported export remains in CloudFront function {path}")
    return source


class NativeContentEdge(Construct):
    """Central API + private Markdown distribution for issue #12."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        content_bucket: s3.IBucket,
        api: apigateway.RestApiBase,
        enabled: bool = False,
        publisher_central_origin_id: str = "central-content-origin",
    ) -> None:
        super().__init__(scope, construct_id)

        if enabled:
            raise ValueError(
                "Native edge delivery remains disabled until issue #29 supplies "
                "the concrete central gate integration"
            )
        if not _PUBLISHER_ORIGIN_ID.fullmatch(publisher_central_origin_id):
            raise ValueError("Publisher central origin ID is invalid")

        api_origin = origins.RestApiOrigin(api, origin_id="CentralApiOrigin")
        content_origin = origins.S3BucketOrigin.with_origin_access_control(
            content_bucket,
            origin_id="PrivateMarkdownOrigin",
        )
        content_cache_policy = cloudfront.CachePolicy(
            self,
            "ContentPathOnlyCachePolicy",
            comment="Shared Markdown bytes keyed only by the canonical object path",
            default_ttl=Duration.hours(1),
            min_ttl=Duration.seconds(0),
            max_ttl=Duration.days(1),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
            header_behavior=cloudfront.CacheHeaderBehavior.none(),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.none(),
            enable_accept_encoding_brotli=True,
            enable_accept_encoding_gzip=True,
        )
        content_response_function = cloudfront.Function(
            self,
            "ContentResponseFunction",
            code=cloudfront.FunctionCode.from_inline(
                _deployable_function_code(_CONTENT_RESPONSE_FUNCTION)
            ),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Projects trusted S3 license metadata into the RSL Link response header",
        )
        publisher_router_code = _deployable_function_code(
            _PUBLISHER_ROUTER_FUNCTION,
            {"{{CENTRAL_ORIGIN_ID}}": publisher_central_origin_id},
        )
        self.publisher_router_function = cloudfront.Function(
            self,
            "PublisherRouterFunction",
            code=cloudfront.FunctionCode.from_inline(publisher_router_code),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Unattached reference router for authorized publisher Path A requests",
        )
        access_log_bucket = s3.Bucket(
            self,
            "AccessLogs",
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment="Central Content-as-an-API edge (disabled until access gates are configured)",
            enabled=False,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            enable_logging=True,
            log_bucket=access_log_bucket,
            log_file_prefix="native-content-edge/",
            default_behavior=cloudfront.BehaviorOptions(
                origin=api_origin,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "content/*": cloudfront.BehaviorOptions(
                    origin=content_origin,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD,
                    cache_policy=content_cache_policy,
                    compress=True,
                    function_associations=[
                        cloudfront.FunctionAssociation(
                            function=content_response_function,
                            event_type=cloudfront.FunctionEventType.VIEWER_RESPONSE,
                        )
                    ],
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                )
            },
        )
        NagSuppressions.add_resource_suppressions(
            self.distribution,
            [
                {
                    "id": "AwsSolutions-CFR1",
                    "reason": (
                        "The shared content edge is intentionally global; publisher/license "
                        "policy gates access instead of geography."
                    ),
                },
                {
                    "id": "AwsSolutions-CFR2",
                    "reason": (
                        "The distribution is disabled by default and synthesis rejects "
                        "enablement until issue #29 supplies the concrete central gate."
                    ),
                },
            ],
        )
        self.error_rate_alarm = cloudwatch.Alarm(
            self,
            "ErrorRateAlarm",
            alarm_name="cm-native-content-edge-5xx-rate",
            alarm_description="Central native content edge 5xx error rate is elevated",
            metric=cloudwatch.Metric(
                namespace="AWS/CloudFront",
                metric_name="5xxErrorRate",
                dimensions_map={
                    "DistributionId": self.distribution.distribution_id,
                    "Region": "Global",
                },
                statistic="Average",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
