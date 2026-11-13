from typing import List, Optional

from aws_cdk import CfnOutput, CfnParameter, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct


class BotControlStack(Stack):
    """
    IAB Bot Control Stack - Independent deployment for AI bot access control

    This stack implements AWS WAF Bot Control according to IAB framework recommendations
    for protecting publisher content from unauthorized AI bot access while preserving
    legitimate traffic from search engines, social media, and monitoring services.

    PRIMARY USE CASE: CloudFront distributions for publisher websites
    - Publishers associate the WebACL with their CloudFront distribution
    - Protects website content from unauthorized AI bot scraping
    - Preserves legitimate search engine and social media traffic

    SECONDARY USE CASE: API Gateway stages (optional)
    - Can be associated with API Gateway for API-specific bot protection
    - Useful for protecting API endpoints from automated scraping
    - Not the primary intended use case but supported for flexibility
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Parameters for customization
        inspection_level = CfnParameter(
            self,
            "InspectionLevel",
            type="String",
            default="COMMON",
            allowed_values=["COMMON", "TARGETED"],
            description="Bot Control inspection level (TARGETED costs more but is more accurate)",
        )

        enable_geographic_restrictions = CfnParameter(
            self,
            "EnableGeographicRestrictions",
            type="String",
            default="false",
            allowed_values=["true", "false"],
            description="Enable geographic restrictions (customize allowed countries in code)",
        )

        log_retention_days = CfnParameter(
            self, "LogRetentionDays", type="Number", default=30, description="CloudWatch log retention period in days"
        )

        api_rate_limit = CfnParameter(
            self,
            "ApiRateLimit",
            type="Number",
            default=1000,
            description="Rate limit for API endpoints (requests per 5 minutes per IP)",
        )

        # Optional parameter for API Gateway stage ARN to automatically associate WebACL
        api_gateway_stage_arn = CfnParameter(
            self,
            "ApiGatewayStageArn",
            type="String",
            default="",
            description="Optional: API Gateway stage ARN to associate with WebACL (format: arn:aws:apigateway:region::/restapis/api-id/stages/stage-name)",
        )

        # Map retention days to enum values
        retention_mapping = {
            1: logs.RetentionDays.ONE_DAY,
            3: logs.RetentionDays.THREE_DAYS,
            5: logs.RetentionDays.FIVE_DAYS,
            7: logs.RetentionDays.ONE_WEEK,
            14: logs.RetentionDays.TWO_WEEKS,
            30: logs.RetentionDays.ONE_MONTH,
            60: logs.RetentionDays.TWO_MONTHS,
            90: logs.RetentionDays.THREE_MONTHS,
            120: logs.RetentionDays.FOUR_MONTHS,
            150: logs.RetentionDays.FIVE_MONTHS,
            180: logs.RetentionDays.SIX_MONTHS,
            365: logs.RetentionDays.ONE_YEAR,
            400: logs.RetentionDays.THIRTEEN_MONTHS,
            545: logs.RetentionDays.EIGHTEEN_MONTHS,
            731: logs.RetentionDays.TWO_YEARS,
            1827: logs.RetentionDays.FIVE_YEARS,
            3653: logs.RetentionDays.TEN_YEARS,
        }

        # Get the closest valid retention period
        retention_days_value = int(log_retention_days.value_as_number)
        retention_period = retention_mapping.get(retention_days_value, logs.RetentionDays.ONE_MONTH)

        # CloudWatch Log Group for WAF logs
        waf_log_group = logs.LogGroup(
            self,
            "WAFLogGroup",
            log_group_name="/aws/wafv2/iab-bot-control",
            retention=retention_period,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # IAB Bot Control Web ACL
        web_acl = wafv2.CfnWebACL(
            self,
            "IABBotControlWebACL",
            name="iab-bot-control-webacl",
            description="IAB Bot Control WebACL for managing AI bot access according to IAB framework",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            rules=[
                # Rule 1: Allow legitimate search engines (highest priority)
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowSearchEngines",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # Googlebot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="googlebot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Bingbot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="bingbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # DuckDuckBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="duckduckbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Baidu
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="baiduspider",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Yandex
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="yandexbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # AppleBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="applebot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AllowSearchEngines",
                    ),
                ),
                # Rule 2: Allow social media platforms
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowSocialMediaBots",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # Facebook
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="facebookexternalhit",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Twitter
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="twitterbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # LinkedIn
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="linkedinbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # WhatsApp
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="whatsapp",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AllowSocialMediaBots",
                    ),
                ),
                # Rule 3: Allow beneficial monitoring and performance bots
                wafv2.CfnWebACL.RuleProperty(
                    name="AllowMonitoringBots",
                    priority=3,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # Pingdom
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="pingdom",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # UptimeRobot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="uptimerobot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # GTmetrix
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="gtmetrix",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Lighthouse
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="lighthouse",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AllowMonitoringBots",
                    ),
                ),
                # Rule 4: Block critical AI training bots (HIGHEST RISK)
                wafv2.CfnWebACL.RuleProperty(
                    name="BlockCriticalAIBots",
                    priority=10,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # GPTBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="gptbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # ChatGPT-User
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="chatgpt-user",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # ChatGPT
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="chatgpt",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # CCBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="ccbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Claude-Web
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="claude-web",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # Bard
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="bard",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="BlockCriticalAIBots",
                    ),
                ),
                # Rule 5: Block content scraping tools
                wafv2.CfnWebACL.RuleProperty(
                    name="BlockScrapingTools",
                    priority=11,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # Scrapy
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="scrapy",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # curl
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="curl/",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # wget
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="wget",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # python-requests
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="python-requests",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="BlockScrapingTools",
                    ),
                ),
                # Rule 6: Block competitor SEO bots
                wafv2.CfnWebACL.RuleProperty(
                    name="BlockCompetitorSEOBots",
                    priority=12,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=[
                                # AhrefsBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="ahrefsbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # SemrushBot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="semrushbot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                                # MJ12bot
                                wafv2.CfnWebACL.StatementProperty(
                                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                        search_string="mj12bot",
                                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                                            single_header=wafv2.CfnWebACL.SingleHeaderProperty(name="user-agent")
                                        ),
                                        text_transformations=[
                                            wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                        ],
                                        positional_constraint="CONTAINS",
                                    )
                                ),
                            ]
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="BlockCompetitorSEOBots",
                    ),
                ),
                # Rule 7: AWS Managed Bot Control Rule Set with overrides
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedBotControl",
                    priority=20,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesBotControlRuleSet",
                            managed_rule_group_configs=[
                                wafv2.CfnWebACL.ManagedRuleGroupConfigProperty(
                                    aws_managed_rules_bot_control_rule_set=wafv2.CfnWebACL.AWSManagedRulesBotControlRuleSetProperty(
                                        inspection_level=inspection_level.value_as_string
                                    )
                                )
                            ],
                            rule_action_overrides=[
                                # Allow verified bots (essential)
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CategorySearchEngine",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                                ),
                                # Block AI category
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CategoryAI", action_to_use=wafv2.CfnWebACL.RuleActionProperty(block={})
                                ),
                                # Block scraping frameworks
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CategoryScrapingFramework",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(block={}),
                                ),
                                # Block HTTP libraries
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CategoryHttpLibrary",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(block={}),
                                ),
                                # Challenge content fetchers
                                wafv2.CfnWebACL.RuleActionOverrideProperty(
                                    name="CategoryContentFetcher",
                                    action_to_use=wafv2.CfnWebACL.RuleActionProperty(challenge={}),
                                ),
                            ],
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="AWSManagedBotControl",
                    ),
                ),
                # Rule 8: Rate limiting for API endpoints
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitAPIEndpoints",
                    priority=30,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=api_rate_limit.value_as_number,
                            aggregate_key_type="IP",
                            scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                                    search_string="/api/",
                                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                                    text_transformations=[
                                        wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
                                    ],
                                    positional_constraint="STARTS_WITH",
                                )
                            ),
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimitAPIEndpoints",
                    ),
                ),
            ],
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                sampled_requests_enabled=True, cloud_watch_metrics_enabled=True, metric_name="IABBotControlWebACL"
            ),
        )

        # Optional WebACL Association with API Gateway
        # Only create association if API Gateway stage ARN is provided
        web_acl_association = None
        if api_gateway_stage_arn.value_as_string and api_gateway_stage_arn.value_as_string.strip():
            # Note: CDK will handle the case where association already exists
            # If association exists, this will be a no-op during update
            web_acl_association = wafv2.CfnWebACLAssociation(
                self,
                "WebACLAssociation",
                web_acl_arn=web_acl.attr_arn,
                resource_arn=api_gateway_stage_arn.value_as_string,
            )
            # Ensure association happens after WebACL is created
            web_acl_association.add_dependency(web_acl)

        # CloudWatch Dashboard for monitoring
        dashboard = cloudwatch.Dashboard(
            self,
            "BotControlDashboard",
            dashboard_name="iab-bot-control-dashboard",
            widgets=[
                [
                    cloudwatch.GraphWidget(
                        title="WAF Requests Overview",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/WAFV2",
                                metric_name="AllowedRequests",
                                dimensions_map={"WebACL": web_acl.name, "Rule": "ALL", "Region": self.region},
                                statistic="Sum",
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/WAFV2",
                                metric_name="BlockedRequests",
                                dimensions_map={"WebACL": web_acl.name, "Rule": "ALL", "Region": self.region},
                                statistic="Sum",
                            ),
                        ],
                        width=12,
                        height=6,
                    ),
                    cloudwatch.GraphWidget(
                        title="Blocked Requests by Rule",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/WAFV2",
                                metric_name="BlockedRequests",
                                dimensions_map={
                                    "WebACL": web_acl.name,
                                    "Rule": "BlockCriticalAIBots",
                                    "Region": self.region,
                                },
                                statistic="Sum",
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/WAFV2",
                                metric_name="BlockedRequests",
                                dimensions_map={
                                    "WebACL": web_acl.name,
                                    "Rule": "BlockScrapingTools",
                                    "Region": self.region,
                                },
                                statistic="Sum",
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/WAFV2",
                                metric_name="BlockedRequests",
                                dimensions_map={
                                    "WebACL": web_acl.name,
                                    "Rule": "BlockCompetitorSEOBots",
                                    "Region": self.region,
                                },
                                statistic="Sum",
                            ),
                        ],
                        width=12,
                        height=6,
                    ),
                ]
            ],
        )

        # CloudWatch Alarms
        high_blocked_requests_alarm = cloudwatch.Alarm(
            self,
            "HighBlockedRequestsAlarm",
            alarm_name="iab-bot-control-high-blocked-requests",
            alarm_description="High number of blocked requests detected",
            metric=cloudwatch.Metric(
                namespace="AWS/WAFV2",
                metric_name="BlockedRequests",
                dimensions_map={"WebACL": web_acl.name, "Region": self.region},
                statistic="Sum",
            ),
            threshold=1000,
            evaluation_periods=2,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        suspicious_activity_alarm = cloudwatch.Alarm(
            self,
            "SuspiciousActivityAlarm",
            alarm_name="iab-bot-control-suspicious-activity",
            alarm_description="Suspicious bot activity detected",
            metric=cloudwatch.Metric(
                namespace="AWS/WAFV2",
                metric_name="BlockedRequests",
                dimensions_map={"WebACL": web_acl.name, "Rule": "BlockCriticalAIBots", "Region": self.region},
                statistic="Sum",
                period=Duration.minutes(1),
            ),
            threshold=100,
            evaluation_periods=3,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Store the WebACL reference for potential use by other stacks
        self.web_acl = web_acl

        # Outputs
        CfnOutput(
            self,
            "WebACLId",
            value=web_acl.attr_id,
            description="ID of the IAB Bot Control WebACL",
            export_name="iab-bot-control-webacl-id",
        )

        CfnOutput(
            self,
            "WebACLArn",
            value=web_acl.attr_arn,
            description="ARN of the IAB Bot Control WebACL",
            export_name="iab-bot-control-webacl-arn",
        )

        CfnOutput(
            self,
            "DashboardURL",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards:name=iab-bot-control-dashboard",
            description="URL to the CloudWatch Dashboard",
        )

        CfnOutput(
            self,
            "LogGroupName",
            value=waf_log_group.log_group_name,
            description="CloudWatch Log Group for WAF logs",
            export_name="iab-bot-control-log-group",
        )

        # Output association status
        CfnOutput(
            self,
            "WebACLAssociationStatus",
            value="Associated"
            if web_acl_association
            else "Not Associated - Provide ApiGatewayStageArn parameter to auto-associate",
            description="Status of WebACL association with API Gateway",
        )

    def associate_with_api_gateway(self, api_gateway_stage_arn: str) -> None:
        """
        Associate this WebACL with an API Gateway stage
        """
        wafv2.CfnWebACLAssociation(
            self, "ApiGatewayAssociation", resource_arn=api_gateway_stage_arn, web_acl_arn=self.web_acl.attr_arn
        )

    def associate_with_load_balancer(self, load_balancer_arn: str) -> None:
        """
        Associate this WebACL with an Application Load Balancer
        """
        wafv2.CfnWebACLAssociation(
            self, "LoadBalancerAssociation", resource_arn=load_balancer_arn, web_acl_arn=self.web_acl.attr_arn
        )

    def associate_with_cloudfront(self, cloudfront_distribution_arn: str) -> None:
        """
        Associate this WebACL with a CloudFront distribution
        Note: CloudFront requires WebACL to be in us-east-1
        """
        wafv2.CfnWebACLAssociation(
            self, "CloudFrontAssociation", resource_arn=cloudfront_distribution_arn, web_acl_arn=self.web_acl.attr_arn
        )
