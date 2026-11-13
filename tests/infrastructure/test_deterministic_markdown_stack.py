"""Infrastructure contract tests for deterministic Markdown state flow."""

import json

import pytest

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


def _pipeline_template() -> Template:
    stack = PipelineStack(
        App(),
        "PipelineStatePayloads",
        content_table_name="cm-content",
        content_bucket_name="iab-llm-content-000000000000-us-west-2",
        env=Environment(account="000000000000", region="us-west-2"),
    )
    return Template.from_stack(stack)


def _state_machine_states():
    resources = _pipeline_template().find_resources("AWS::StepFunctions::StateMachine")
    definition = next(iter(resources.values()))["Properties"]["DefinitionString"]
    # Fn::Join of literal fragments and tokens (Lambda ARNs, inside JSON strings).
    fragments = definition["Fn::Join"][1] if isinstance(definition, dict) else [definition]
    text = "".join(part if isinstance(part, str) else "TOKEN" for part in fragments)
    return json.loads(text)["States"]


def _handler_function(function_name):
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "source" / "lambda" / "content_pipeline" / "handler.py"
    tree = ast.parse(source.read_text())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)


def _event_keys_read_by(function_name):
    import ast

    fn = _handler_function(function_name)
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", None) == "event":
            if isinstance(node.slice, ast.Constant):
                keys.add(node.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and getattr(node.func.value, "id", None) == "event"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
    return keys


def _keys_returned_by(function_name):
    """The keys a step's output can hold: its returned dict literals, plus its event when echoed."""
    import ast

    keys, echoes_event = set(), False
    for node in ast.walk(_handler_function(function_name)):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "event":
            echoes_event = True
        elif isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if key is None and isinstance(value, ast.Name) and value.id == "event":
                    echoes_event = True
                elif isinstance(key, ast.Constant):
                    keys.add(key.value)
    return keys, echoes_event


def _payload(state):
    parameters = _state_machine_states()[state]["Parameters"]
    # payload_response_only=True: the task's Parameters are the Lambda payload itself.
    return parameters.get("Payload", parameters)


_ORDER = ["Extract", "ClassifyEnrich", "RenderMarkdown", "Embed", "IndexOpenSearch", "PersistDynamoDB"]

# Each task after Extract gets the shared payload, not the previous step's output.
_STEP_OF_STATE = {
    "Extract": "step_extract",
    "ClassifyEnrich": "step_classify_enrich",
    "RenderMarkdown": "step_render",
    "Embed": "step_embed",
    "IndexOpenSearch": "step_index",
    "PersistDynamoDB": "step_persist",
}


def test_the_document_is_rendered_after_enrichment_and_before_the_record():
    states = _state_machine_states()
    assert [name for name in _ORDER if name in states] == _ORDER
    for current, following in zip(_ORDER, _ORDER[1:]):
        assert states[current]["Next"] == following
    assert states["PersistDynamoDB"].get("End") is True
    assert _payload("RenderMarkdown")["step"] == "render"


@pytest.mark.parametrize("previous, state", list(zip(_ORDER, _ORDER[1:])))
def test_every_path_a_step_maps_is_in_the_previous_steps_output(previous, state):
    """A $.field the previous task didn't return fails the execution at run time, not at deploy."""
    returned, echoes_event = _keys_returned_by(_STEP_OF_STATE[previous])
    available = set(returned)
    if echoes_event:
        available |= {key[:-2] if key.endswith(".$") else key for key in _payload(previous)}
    paths = {value[2:] for key, value in _payload(state).items() if key.endswith(".$")}

    assert paths <= available, f"{state} maps fields {previous} never returns: {sorted(paths - available)}"


@pytest.mark.parametrize("state", sorted(_STEP_OF_STATE))
def test_md_etag_is_mapped_only_after_the_render_step(state):
    after_render = _ORDER.index(state) > _ORDER.index("RenderMarkdown")
    assert ("md_etag.$" in _payload(state)) is after_render


@pytest.mark.parametrize("state, step", sorted((s, f) for s, f in _STEP_OF_STATE.items() if s != "Extract"))
def test_each_step_receives_every_event_field_it_reads(state, step):
    """The state machine passes an explicit payload per step, so a new field must be mapped too."""
    payload = _payload(state)
    mapped = {key[:-2] if key.endswith(".$") else key for key in payload}

    read = _event_keys_read_by(step)

    assert read, f"no event reads found in {step}; the scan is broken"
    assert read <= mapped, f"{step} reads fields the state machine never passes: {sorted(read - mapped)}"


def test_persist_receives_the_markdown_objects_etag():
    payload = _state_machine_states()["PersistDynamoDB"]["Parameters"]
    assert "md_etag" in _event_keys_read_by("step_persist")
    assert payload["md_etag.$"] == "$.md_etag"


def test_pipeline_state_carries_the_partition_to_index_and_persist():
    """Each task gets an explicit payload, so ``partition`` must be mapped or index/persist never see it
    (scrape-vs-api-comparison R15): a comparison ingest would then be indexed and published."""
    resources = _pipeline_template().find_resources("AWS::StepFunctions::StateMachine")
    definition = json.dumps(next(iter(resources.values()))["Properties"]["DefinitionString"])
    # One mapping per task after extract: classify, render, embed, index, persist.
    assert definition.count('\\"partition.$\\":\\"$.partition\\"') == 5


def test_pipeline_role_can_write_the_comparison_partition():
    policies = _pipeline_template().find_resources("AWS::IAM::Policy")
    resources = json.dumps([p["Properties"]["PolicyDocument"] for p in policies.values()])
    assert "/comparison/*" in resources
