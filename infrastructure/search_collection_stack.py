"""
OpenSearch Serverless search collection — isolated TOP-LEVEL stack.

WHY THIS IS A SEPARATE, TOP-LEVEL STACK
=======================================
The AOSS collection (``AWS::OpenSearchServerless::Collection``) has
``Update-requires-Replacement`` on its ``Tags`` property. The solution stamps a
``SolutionVersion`` tag on every stack via ``tag_solution(...)`` in ``app.py``,
and that value bumps on every release. If ``SolutionVersion`` lands on the
collection, each version bump mutates an immutable-tag property and CloudFormation
REPLACES the collection — wiping the index and forcing a full cold-start re-ingest
on every release. We must keep ``SolutionVersion`` OFF the collection while every
other pipeline resource keeps it for cost-allocation/compliance.

TWO FAILED APPROACHES (both verified against the DEPLOYED collection)
---------------------------------------------------------------------
1. Collection in ``PipelineStack`` + resource-scoped
   ``Tags.of(collection).remove("SolutionVersion")``. The removal wins in the
   SYNTHESIZED template but LOSES live: CloudFormation propagates a stack's own
   stack-level tags onto every resource in that stack at deploy, re-applying
   ``SolutionVersion`` after CDK's aspect removed it.
2. Collection in a NESTED stack with the same synth-time remove. ALSO FAILS,
   and this was proven on a correctly-materialized nested stack: CFN propagates
   the PARENT stack's stack-level tags onto the nested ``AWS::CloudFormation::Stack``
   resource, which makes ``SolutionVersion`` a STACK-LEVEL tag on the child stack,
   and CFN then propagates the child's stack-level tags onto its resources. The
   deployed collection still carried ``SolutionVersion: 0.0.1``. Nesting does NOT
   isolate stack tags — an earlier claim here that it would was wrong.

THE FIX (this stack)
--------------------
A free-standing TOP-LEVEL stack whose STACK-LEVEL tags never include
``SolutionVersion``. ``app.py`` tags this stack with a governance-only helper
(``tag_solution_no_version``: Project/SolutionName/SolutionId + Component) and
deliberately never calls ``tag_solution`` on it, so ``SolutionVersion`` is never a
stack-level tag ANYWHERE in this stack's hierarchy. With no ``SolutionVersion``
stack tag to propagate, CFN has nothing to re-apply to the collection at deploy —
which is the one thing the two failed approaches could not prevent. A version bump
no longer touches the collection's ``Tags``, so it is never replaced.

ZERO CROSS-STACK EXPORTS (repo steering: reference shared resources by name)
============================================================================
The data-access policy here needs the WRITER principals (the pipeline + backfill
Lambda roles, which live in ``PipelineStack`` because they attach to Lambdas
there) and the READER principals (partner_api / ui_backend roles). To avoid a
CloudFormation ``Export``/``ImportValue`` cross-stack reference — which the repo's
``project-standards`` / ``spec-first-and-quality-gates`` steering forbids — the
roles are given FIXED, region-suffixed names and their ARNs are passed in as plain
STRINGS (``writer_role_arns`` / ``reader_role_arns``), composed as
``arn:aws:iam::{account}:role/{name}-{region}``. A string ARN creates no CFN
export. Conversely, ``PipelineStack`` grants its Lambda roles ``aoss:APIAccessAll``
on a WILDCARD collection ARN (``collection/*``) rather than this collection's
CFN-assigned id — so the parent needs NO reference back into this stack, and there
is no cross-stack coupling in either direction. Consumers discover the collection
endpoint at runtime from the SSM parameter BY NAME (no construct reference, no
export) — the same mechanism partner_api/ui_backend already use.
"""

import json

from aws_cdk import CfnOutput, CfnResource, Stack
from aws_cdk import aws_opensearchserverless as aoss
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class SearchCollectionStack(Stack):
    """Top-level stack holding the AOSS collection, its group, policies, and the
    SSM endpoint parameter. Tagged by ``app.py`` with governance tags ONLY
    (``tag_solution_no_version``) so ``SolutionVersion`` never reaches the
    collection and cannot trigger its replacement on a release bump."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        collection_name: str,
        collection_group_name: str,
        endpoint_ssm_param: str,
        # Writer principals (pipeline + backfill roles) and reader principals
        # (partner_api + ui_backend roles) passed in as composed ARN STRINGS so
        # no cross-stack CloudFormation export is created (see module docstring).
        writer_role_arns: list[str],
        reader_role_arns: list[str],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # All AOSS policies match the collection by the ``{name}*`` PREFIX
        # wildcard, NOT by construct reference — so a future immutable-property
        # change can bump the collection name (create-before-delete replacement)
        # with no policy edits, and the policies auto-attach at creation.
        collection_resource_wildcard = f"{collection_name}*"

        # ── NextGen collection group (scale-to-zero) ──
        # NextGen (GA May 2026) scales compute to 0 OCU after ~10 idle minutes;
        # only storage bills while idle. Classic collections have a 2-OCU
        # always-on floor — do NOT deploy those. Max 8/8 OCU as a cost cap.
        # Trade-off: 10-30 s wake latency on first request after idle.
        # aws-cdk-lib 2.170 predates CfnCollectionGroup, so raw CfnResource.
        collection_group = CfnResource(
            self,
            "ContentCollectionGroup",
            type="AWS::OpenSearchServerless::CollectionGroup",
            properties={
                "Name": collection_group_name,
                "Description": "Scale-to-zero group for the GEO content search collection",
                "Generation": "NEXTGEN",
                # NEXTGEN requires standby replicas (multi-AZ) — the service
                # rejects DISABLED. Storage-only idle billing still holds; the
                # OCU floor is what NextGen removes, not the replica.
                "StandbyReplicas": "ENABLED",
                "CapacityLimits": {
                    "MinIndexingCapacityInOcu": 0,
                    "MinSearchCapacityInOcu": 0,
                    "MaxIndexingCapacityInOcu": 8,
                    "MaxSearchCapacityInOcu": 8,
                },
            },
        )

        encryption_policy = aoss.CfnSecurityPolicy(
            self,
            "CollectionEncryptionPolicy",
            name=f"{collection_name}-enc",
            type="encryption",
            policy=json.dumps(
                {
                    "Rules": [
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{collection_resource_wildcard}"],
                        }
                    ],
                    "AWSOwnedKey": True,
                }
            ),
        )

        network_policy = aoss.CfnSecurityPolicy(
            self,
            "CollectionNetworkPolicy",
            name=f"{collection_name}-net",
            type="network",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{collection_resource_wildcard}"],
                            },
                            {
                                "ResourceType": "dashboard",
                                "Resource": [f"collection/{collection_resource_wildcard}"],
                            },
                        ],
                        "AllowFromPublic": True,
                    }
                ]
            ),
        )

        self.collection = aoss.CfnCollection(
            self,
            "ContentSearchCollection",
            # Name is REQUIRED by the L1 and immutable (Update-requires-Replacement).
            # If an immutable property must ever change, bump this name so CFN does a
            # create-before-delete REPLACEMENT with a new physical name instead of
            # colliding on a fixed-name clash. All AOSS policies match by the
            # ``{name}*`` prefix wildcard, so a name bump needs no policy edits.
            # Downstream reads use the endpoint/id attributes.
            name=collection_name,
            type="VECTORSEARCH",
            description="Hybrid (vector + BM25) index for GEO-enriched content",
        )
        # CollectionGroupName is newer than this cdk-lib's CfnCollection props.
        self.collection.add_property_override("CollectionGroupName", collection_group_name)
        self.collection.add_dependency(encryption_policy)
        self.collection.node.add_dependency(collection_group)
        self.collection.node.add_dependency(network_policy)

        # NOTE: no ``Tags.of(collection).remove("SolutionVersion")`` here. The
        # whole point of this top-level stack is that SolutionVersion never
        # exists as a stack-level tag on it — app.py tags it with
        # ``tag_solution_no_version`` (governance tags only), so there is no
        # SolutionVersion stack tag for CFN to propagate onto the collection at
        # deploy. A resource-scoped remove is unnecessary (and, as the two prior
        # approaches proved, insufficient against CFN stack-tag propagation).

        # SSM parameter lets consumers (pipeline/backfill Lambdas in the parent
        # stack, and partner_api/ui_backend in OTHER stacks) discover the endpoint
        # at runtime BY NAME — no cross-stack env-var/export dependency.
        self.endpoint_param = ssm.StringParameter(
            self,
            "OpenSearchEndpointParam",
            parameter_name=endpoint_ssm_param,
            string_value=self.collection.attr_collection_endpoint,
            description="OpenSearch Serverless collection endpoint for content search",
        )

        # ── Data access policy: writers (pipeline+backfill) write; readers
        #    (partner_api+ui_backend) search. Principals are composed ARN STRINGS
        #    passed from the parent — no cross-stack export (see module docstring). ──
        aoss.CfnAccessPolicy(
            self,
            "CollectionDataAccessPolicy",
            name=f"{collection_name}-access",
            type="data",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{collection_resource_wildcard}"],
                                "Permission": ["aoss:DescribeCollectionItems"],
                            },
                            {
                                "ResourceType": "index",
                                "Resource": [f"index/{collection_resource_wildcard}/*"],
                                "Permission": [
                                    "aoss:CreateIndex",
                                    "aoss:DescribeIndex",
                                    "aoss:UpdateIndex",
                                    "aoss:ReadDocument",
                                    "aoss:WriteDocument",
                                ],
                            },
                        ],
                        "Principal": list(writer_role_arns),
                    },
                    {
                        "Rules": [
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{collection_resource_wildcard}"],
                                "Permission": ["aoss:DescribeCollectionItems"],
                            },
                            {
                                "ResourceType": "index",
                                "Resource": [f"index/{collection_resource_wildcard}/*"],
                                "Permission": [
                                    "aoss:DescribeIndex",
                                    "aoss:ReadDocument",
                                ],
                            },
                        ],
                        "Principal": list(reader_role_arns),
                    },
                ]
            ),
        )

        # Expose the collection id/endpoint as plain attributes for this stack's
        # own outputs and for tests. PipelineStack does NOT read these (it grants
        # aoss:APIAccessAll on a wildcard collection ARN, not this id), so there
        # is no cross-stack reference or export in either direction.
        self.collection_id = self.collection.attr_id
        self.collection_endpoint = self.collection.attr_collection_endpoint

        CfnOutput(
            self,
            "OpenSearchCollectionEndpoint",
            value=self.collection.attr_collection_endpoint,
            description="OpenSearch Serverless collection endpoint",
        )
        CfnOutput(
            self,
            "OpenSearchEndpointSSMParameter",
            value=self.endpoint_param.parameter_name,
            description="SSM parameter holding the collection endpoint",
        )
