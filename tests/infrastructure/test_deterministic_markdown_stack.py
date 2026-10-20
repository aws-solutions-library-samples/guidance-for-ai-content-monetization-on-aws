"""Infrastructure contract tests for deterministic Markdown state flow."""

import json

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from infrastructure.pipeline_stack import PipelineStack


def test_pipeline_state_carries_markdown_schema_version():
    stack = PipelineStack(
        App(),
        "DeterministicMarkdownPipeline",
        content_table_name="cm-content",
        content_bucket_name="iab-llm-content-000000000000-us-west-2",
        env=Environment(account="000000000000", region="us-west-2"),
    )
    resources = Template.from_stack(stack).find_resources("AWS::StepFunctions::StateMachine")
    assert len(resources) == 1
    definition = json.dumps(next(iter(resources.values()))["Properties"]["DefinitionString"])
    assert "markdown_schema_version" in definition
    assert "$.markdown_schema_version" in definition
