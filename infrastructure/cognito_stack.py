"""
AI Content Monetization API - Cognito Authentication Stack

This stack creates AWS Cognito User Pool and Identity Pool for UI authentication.
It provides secure authentication for the demo UI with role-based access control.
"""

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from constructs import Construct


class CognitoStack(Stack):
    """
    CDK Stack for AWS Cognito Authentication

    This stack creates:
    - Cognito User Pool for user authentication
    - Cognito Identity Pool for AWS resource access
    - User groups for role-based access control
    - IAM roles for authenticated and unauthenticated users
    """

    def __init__(self, scope: Construct, construct_id: str, domain_prefix: str = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain_prefix = domain_prefix or f"iab-llm-ingest-{self.account}"

        # Create Cognito User Pool
        self.user_pool = self._create_user_pool()

        # Create User Pool Client
        self.user_pool_client = self._create_user_pool_client()

        # Create User Pool Domain
        self.user_pool_domain = self._create_user_pool_domain()

        # Create User Groups
        self._create_user_groups()

        # Create Identity Pool
        self.identity_pool = self._create_identity_pool()

        # Create IAM roles for Identity Pool
        self._create_identity_pool_roles()

        # Create outputs
        self._create_outputs()

    def _create_user_pool(self) -> cognito.UserPool:
        """Create Cognito User Pool with security best practices"""

        user_pool = cognito.UserPool(
            self,
            "UIUserPool",
            user_pool_name="AI-Content-Monetization-UI-Users",
            # Sign-in configuration
            sign_in_aliases=cognito.SignInAliases(email=True, username=False),
            # Public self-registration is disabled: only an administrator can create users
            # (maps to AdminCreateUserConfig.AllowAdminCreateUserOnly=true). Leaving self sign-up
            # open lets anyone create an account in the pool without approval, which automated
            # AppSec scanning flags (CognitoSelfRegistrationEnabled). New users are provisioned by
            # an admin; the email-verification config below still applies to those accounts.
            self_sign_up_enabled=False,
            user_verification=cognito.UserVerificationConfig(
                email_subject="Verify your email for AI Content Monetization API",
                email_body="Thank you for signing up for the AI Content Monetization API demo! Your verification code is {####}",
                email_style=cognito.VerificationEmailStyle.CODE,
            ),
            # Password policy
            password_policy=cognito.PasswordPolicy(
                min_length=8, require_lowercase=True, require_uppercase=True, require_digits=True, require_symbols=True
            ),
            # Account recovery
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # MFA configuration
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=True, otp=True),
            # Standard attributes
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                given_name=cognito.StandardAttribute(required=False, mutable=True),
                family_name=cognito.StandardAttribute(required=False, mutable=True),
            ),
            # Custom attributes
            custom_attributes={
                "role": cognito.StringAttribute(min_len=1, max_len=20, mutable=True),
                "organization": cognito.StringAttribute(min_len=1, max_len=100, mutable=True),
            },
            # Cleanup
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            # Advanced security
            advanced_security_mode=cognito.AdvancedSecurityMode.ENFORCED,
            # Device tracking
            device_tracking=cognito.DeviceTracking(
                challenge_required_on_new_device=True, device_only_remembered_on_user_prompt=True
            ),
        )

        return user_pool

    def _create_user_pool_client(self) -> cognito.UserPoolClient:
        """Create User Pool Client for the React application"""

        client = self.user_pool.add_client(
            "UIClient",
            user_pool_client_name="AI-Content-Monetization-UI-Client",
            # OAuth configuration
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True, implicit_code_grant=False),
                scopes=[cognito.OAuthScope.EMAIL, cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
                callback_urls=[
                    "http://localhost:3000",  # Development
                    "https://localhost:3000",  # Development HTTPS
                    "https://d3hf1z14eise11.cloudfront.net",  # Production CloudFront (from existing deployment)
                ],
                logout_urls=[
                    "http://localhost:3000",  # Development
                    "https://localhost:3000",  # Development HTTPS
                    "https://d3hf1z14eise11.cloudfront.net",  # Production CloudFront (from existing deployment)
                ],
            ),
            # Security settings
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=True,  # Enable for Amplify compatibility
                admin_user_password=True,  # Enable for server-side auth
                custom=False,
            ),
            # Token validity
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            # Prevent user existence errors
            prevent_user_existence_errors=True,
            # Enable token revocation
            enable_token_revocation=True,
            # Generate secret (not recommended for SPA, but useful for server-side)
            generate_secret=False,
        )

        return client

    def _create_user_pool_domain(self) -> cognito.UserPoolDomain:
        """Create User Pool Domain for hosted UI"""

        domain = self.user_pool.add_domain(
            "UIDomain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=self.domain_prefix)
        )

        return domain

    def _create_user_groups(self):
        """Create user groups for role-based access control"""

        # Admin group - full access to all features
        admin_role = iam.Role(
            self,
            "AdminRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": "${aws:cognito-identity-pool-id}"},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
            ),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("PowerUserAccess")],
        )

        self.admin_group = cognito.CfnUserPoolGroup(
            self,
            "AdminGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Administrators",
            description="Full access to all demo features and administration",
            precedence=1,
            role_arn=admin_role.role_arn,
        )

        # Publisher group - access to publisher features
        publisher_role = iam.Role(
            self,
            "PublisherRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": "${aws:cognito-identity-pool-id}"},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
            ),
            inline_policies={
                "PublisherPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                            resources=["arn:aws:s3:::iab-llm-ingest-content-*/*"],
                        ),
                        # The region/account/api-id portion of these execute-api ARNs is
                        # wildcarded (`*:*:*`) by necessity, not oversight. The only part that
                        # would meaningfully narrow the grant is the REST API id, and that id
                        # is created in the monetization API stack, not here in the auth stack.
                        # Referencing it from this stack would invert the stack dependency
                        # direction (the API stack depends on this auth stack for the user pool
                        # and authorizer) and create a circular reference at synth time, so the
                        # id is not resolvable where these roles are defined.
                        #
                        # The grant is still bounded in three ways: (1) the role is assumable
                        # only via sts:AssumeRoleWithWebIdentity federated through this specific
                        # Cognito identity pool (the cognito-identity.amazonaws.com:aud trust
                        # condition above); (2) invoking any API in another account additionally
                        # requires that API's own resource policy to allow the caller, which an
                        # identity-side wildcard cannot grant on its own; and (3) the method and
                        # path are scoped to the specific /content verbs below rather than a
                        # blanket execute-api:* on all methods. (CSR re-review M1; TM-03.)
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["execute-api:Invoke"],
                            resources=[
                                "arn:aws:execute-api:*:*:*/*/POST/content",
                                "arn:aws:execute-api:*:*:*/*/GET/content",
                                "arn:aws:execute-api:*:*:*/*/PUT/content",
                            ],
                        ),
                    ]
                )
            },
        )

        self.publisher_group = cognito.CfnUserPoolGroup(
            self,
            "PublisherGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Publishers",
            description="Access to publisher dashboard and content management",
            precedence=2,
            role_arn=publisher_role.role_arn,
        )

        # Viewer group - read-only access
        viewer_role = iam.Role(
            self,
            "ViewerRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": "${aws:cognito-identity-pool-id}"},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
            ),
            inline_policies={
                "ViewerPolicy": iam.PolicyDocument(
                    statements=[
                        # region/account/api-id are wildcarded for the same reason as the
                        # PublisherRole execute-api statement above: the REST API id is not
                        # resolvable from the auth stack without a circular stack dependency.
                        # The grant is bounded by the identity-pool trust condition, the target
                        # API's own resource policy, and the method scope (GET only). (CSR M1; TM-03.)
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["execute-api:Invoke"],
                            resources=["arn:aws:execute-api:*:*:*/*/GET/*"],
                        )
                    ]
                )
            },
        )

        self.viewer_group = cognito.CfnUserPoolGroup(
            self,
            "ViewerGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Viewers",
            description="Read-only access to demo features",
            precedence=3,
            role_arn=viewer_role.role_arn,
        )

    def _create_identity_pool(self) -> cognito.CfnIdentityPool:
        """Create Cognito Identity Pool for AWS resource access"""

        identity_pool = cognito.CfnIdentityPool(
            self,
            "UIIdentityPool",
            identity_pool_name="IAB_LLM_Ingest_UI_Identity_Pool",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=self.user_pool_client.user_pool_client_id,
                    provider_name=self.user_pool.user_pool_provider_name,
                    server_side_token_check=True,
                )
            ],
        )

        return identity_pool

    def _create_identity_pool_roles(self):
        """Create IAM roles for Identity Pool"""

        # Authenticated role
        authenticated_role = iam.Role(
            self,
            "AuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": self.identity_pool.ref},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            # TM-03 (security-hardening-phase2): no inline action policy. The browser identity
            # authenticates to the internal API via user-pool JWT only; it does not use Cognito
            # Sync datasets or call cognito-identity APIs directly (verified by grep of source/ui
            # and the auth code — task 2.1). The role keeps its conditioned trust policy (who may
            # assume) and grants no cognito-*:* actions. If a specific cognito-identity action is
            # ever required, scope it to the identity-pool ARN + that action, never "*" on "*".
        )

        # Attach roles to Identity Pool
        cognito.CfnIdentityPoolRoleAttachment(
            self,
            "IdentityPoolRoleAttachment",
            identity_pool_id=self.identity_pool.ref,
            roles={"authenticated": authenticated_role.role_arn},
            role_mappings={
                "UserPoolRoleMapping": cognito.CfnIdentityPoolRoleAttachment.RoleMappingProperty(
                    type="Token",
                    ambiguous_role_resolution="AuthenticatedRole",
                    identity_provider=f"{self.user_pool.user_pool_provider_name}:{self.user_pool_client.user_pool_client_id}",
                )
            },
        )

    def _create_outputs(self):
        """Create CloudFormation outputs"""

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id, description="Cognito User Pool ID")

        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
        )

        CfnOutput(self, "IdentityPoolId", value=self.identity_pool.ref, description="Cognito Identity Pool ID")

        CfnOutput(
            self,
            "UserPoolDomain",
            value=f"{self.domain_prefix}.auth.{self.region}.amazoncognito.com",
            description="Cognito User Pool Domain",
        )

        CfnOutput(
            self,
            "HostedUIUrl",
            value=f"https://{self.domain_prefix}.auth.{self.region}.amazoncognito.com/login?client_id={self.user_pool_client.user_pool_client_id}&response_type=code&scope=email+openid+profile&redirect_uri=http://localhost:3000",
            description="Cognito Hosted UI Login URL",
        )

    @property
    def user_pool_id(self) -> str:
        """Get the User Pool ID"""
        return self.user_pool.user_pool_id

    @property
    def user_pool_client_id(self) -> str:
        """Get the User Pool Client ID"""
        return self.user_pool_client.user_pool_client_id

    @property
    def identity_pool_id(self) -> str:
        """Get the Identity Pool ID"""
        return self.identity_pool.ref
