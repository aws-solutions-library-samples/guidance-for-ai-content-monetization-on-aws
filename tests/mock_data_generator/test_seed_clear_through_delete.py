"""The seed step's clear goes through the content delete.

A reset (``make seed-data CLEAR_EXISTING=true``, which CI runs after deploying the pipeline) used to delete the ``cm-content`` rows
directly, which left their search documents behind, and stale discovery results then broke
discovery-based tests.
So the clear soft-deletes every record (each emits ``content.deleted``), waits for the
deindexer, and only then lets the other tables be cleared and reseeded. If records remain when
the wait ends, it falls back to deleting those rows directly, for example when no deindexer is
deployed yet.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from mock_data_generator import generator as generator_module
from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator

REGION = "us-west-2"


@pytest.fixture
def content_table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)
        yield resource.create_table(
            TableName="cm-content",
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def _generator(content_table, events, wait_seconds=30):
    with patch("boto3.Session"):
        gen = MockDataGenerator(
            GeneratorConfig(aws_profile="", partner_table_name="partners", deindex_wait_seconds=wait_seconds)
        )
    resource = MagicMock()
    resource.Table.return_value = content_table
    gen._dynamodb_resource = resource
    gen._events_client = events
    return gen


def _events(on_put=None):
    events = MagicMock()

    def put_events(Entries):
        if on_put:
            for entry in Entries:
                on_put(json.loads(entry["Detail"]))
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "e"}]}

    events.put_events.side_effect = put_events
    return events


@pytest.fixture
def no_sleep(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(generator_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        generator_module.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    )
    return clock


def _ids(table):
    return sorted(item["content_id"] for item in table.scan()["Items"])


def test_every_record_is_soft_deleted_and_the_deindexer_is_awaited(content_table, no_sleep):
    for n in range(3):
        content_table.put_item(Item={"content_id": f"cnt_{n}", "status": "published"})
    emitted = []
    deindexed_after_one_poll = []

    def deindexer(detail):
        emitted.append(detail)
        deindexed_after_one_poll.append(detail["content_id"])

    events = _events(on_put=deindexer)
    gen = _generator(content_table, events)

    original_sleep = generator_module.time.sleep

    def sleep_then_deindex(seconds):
        original_sleep(seconds)
        for content_id in deindexed_after_one_poll:
            content_table.delete_item(Key={"content_id": content_id})
        deindexed_after_one_poll.clear()

    with patch.object(generator_module.time, "sleep", side_effect=sleep_then_deindex):
        assert gen.clear_content_through_delete("cm-content") is True

    assert sorted(d["content_id"] for d in emitted) == ["cnt_0", "cnt_1", "cnt_2"]
    assert {d["deleted_by"] for d in emitted} == {"system:seed"}
    assert _ids(content_table) == []


def test_the_scan_follows_every_page(no_sleep):
    table = MagicMock()
    table.scan.side_effect = [
        {"Items": [{"content_id": "cnt_a"}], "LastEvaluatedKey": {"content_id": "cnt_a"}},
        {"Items": [{"content_id": "cnt_b"}]},
    ]
    table.update_item.side_effect = lambda **kw: {"Attributes": {"content_id": kw["Key"]["content_id"]}}
    table.get_item.return_value = {}
    gen = _generator(table, _events())

    assert gen.clear_content_through_delete("cm-content") is True

    assert [c.kwargs["Key"] for c in table.update_item.call_args_list] == [
        {"content_id": "cnt_a"},
        {"content_id": "cnt_b"},
    ]
    assert table.scan.call_args_list[1].kwargs["ExclusiveStartKey"] == {"content_id": "cnt_a"}


def test_records_left_when_the_wait_ends_are_deleted_directly_with_a_warning(content_table, no_sleep, caplog):
    content_table.put_item(Item={"content_id": "cnt_stuck", "status": "published"})
    gen = _generator(content_table, _events(), wait_seconds=12)

    assert gen.clear_content_through_delete("cm-content") is True

    assert _ids(content_table) == []
    assert no_sleep["now"] >= 12  # waited the full bound before falling back
    assert "not removed by the deindexer" in caplog.text
    assert "cnt_stuck" in caplog.text


def test_a_partial_failure_waits_for_the_deletes_already_accepted(content_table, no_sleep):
    """Deindexers already running must finish before the direct clear and the reseed."""
    for n in range(3):
        content_table.put_item(Item={"content_id": f"cnt_{n}", "status": "published"})
    calls = {"n": 0}

    def flaky_put_events(Entries):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("ThrottlingException")
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "e"}]}

    events = MagicMock()
    events.put_events.side_effect = flaky_put_events
    gen = _generator(content_table, events, wait_seconds=30)

    with (
        patch.object(gen, "_await_deindex", return_value=set()) as awaited,
        patch.object(gen, "clear_table", return_value=True) as clear_table,
    ):
        assert gen.clear_content_through_delete("cm-content") is True

    (waited_ids,) = awaited.call_args.args[1:]
    assert len(waited_ids) == 1  # the one delete accepted before the failure
    clear_table.assert_called_once_with("cm-content")


def test_an_unavailable_event_bus_falls_back_to_the_direct_clear(content_table, no_sleep, caplog):
    content_table.put_item(Item={"content_id": "cnt_1", "status": "published"})
    events = MagicMock()
    events.put_events.side_effect = RuntimeError("AccessDenied: events:PutEvents")
    gen = _generator(content_table, events)

    with patch.object(gen, "clear_table", return_value=True) as clear_table:
        assert gen.clear_content_through_delete("cm-content") is True

    clear_table.assert_called_once_with("cm-content")
    assert "Content delete unavailable" in caplog.text


def test_an_empty_table_needs_no_wait(content_table, no_sleep):
    events = _events()
    gen = _generator(content_table, events)

    assert gen.clear_content_through_delete("cm-content") is True

    events.put_events.assert_not_called()
    assert no_sleep["now"] == 0


def test_clear_all_tables_clears_content_through_the_delete_before_any_other_table():
    with patch("boto3.Session"):
        gen = MockDataGenerator(GeneratorConfig(aws_profile="", partner_table_name="partners"))
    order = []
    with (
        patch.object(gen, "clear_content_through_delete", side_effect=lambda t: order.append(("delete", t)) or True),
        patch.object(gen, "clear_table", side_effect=lambda t: order.append(("clear", t)) or True),
    ):
        assert gen.clear_all_tables() is True

    assert order[0] == ("delete", "cm-content")
    assert ("clear", "cm-content") not in order
    assert ("clear", "cm-jobs") in order
    assert all(table != "partners" for _, table in order)


def _make_dry_run(target, *assignments, env_value=None):
    """``make -n <target>`` from the repository root, with the parent make's state stripped."""
    import os
    import subprocess
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "CLEAR_EXISTING"}}
    if env_value is not None:
        env["CLEAR_EXISTING"] = env_value
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["make", "-n", target, *assignments], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.mark.skipif(__import__("shutil").which("make") is None, reason="needs make")
@pytest.mark.parametrize(
    "assignments, clears",
    [
        ((), False),
        (("CLEAR_EXISTING=true",), True),
        (("CLEAR_EXISTING=1",), True),
        (("CLEAR_EXISTING=false",), False),
        (("CLEAR_EXISTING=0",), False),
        (("CLEAR_EXISTING=",), False),
    ],
    ids=["unset", "true", "one", "false", "zero", "empty"],
)
def test_the_seed_resets_only_on_an_explicit_yes(assignments, clears):
    """The reset deletes every document and its stored objects; CLEAR_EXISTING=false must not run it."""
    commands = _make_dry_run("seed-data", *assignments)

    assert ("--clear-existing" in commands) is clears


@pytest.mark.skipif(__import__("shutil").which("make") is None, reason="needs make")
@pytest.mark.parametrize("assignments", [(), ("CLEAR_EXISTING=true",)], ids=["plain", "clear-asked"])
def test_a_deploy_never_seeds(assignments):
    """A customer redeploy must not touch content; demo data is the separate, optional seed-data."""
    commands = _make_dry_run("deploy-backend", *assignments)

    assert "seed" not in commands
    assert "migrate-taxonomy" in commands


def test_ci_seeds_after_the_pipeline_deploy_and_before_the_integration_tests():
    """The reset goes through the content delete, so the deindexer (pipeline stack) must exist first."""
    from pathlib import Path

    import yaml

    ci = yaml.safe_load((Path(__file__).resolve().parents[2] / ".gitlab-ci.yml").read_text())
    seeding_jobs = {
        name
        for name, job in ci.items()
        if isinstance(job, dict) and any("seed-data" in line for line in job.get("script", []))
    }

    assert seeding_jobs == {"deploy-pipeline"}
    pipeline_script = ci["deploy-pipeline"]["script"]
    assert pipeline_script[-1] == "make seed-data CLEAR_EXISTING=true"
    assert pipeline_script.index("make deploy-pipeline") < len(pipeline_script) - 1
    assert "make deploy-backend" in ci["deploy-backend"]["script"]
    assert not any("CLEAR_EXISTING" in line for line in ci["deploy-backend"]["script"])
    # needs: lets a job start before its stage; the integration tests must wait for the seed.
    assert "deploy-pipeline" in ci["integration-test"]["needs"]
