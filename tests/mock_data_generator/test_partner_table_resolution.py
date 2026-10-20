"""The seed generator must use the stack's partner table, never a hardcoded name.

The partner table has a CloudFormation-generated name (it is replaced when its key schema
changes), so the generator resolves it from config, ``PARTNER_TABLE_NAME`` or the
``PartnerTableName`` stack output. A literal here broke the dev deploy's seed step once.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mock_data_generator import generate_mock_data
from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator

SOURCE = Path(__file__).resolve().parents[2] / "source" / "mock_data_generator"


def _generator(partner_table_name="", monkeypatch=None, env=None):
    if monkeypatch is not None:
        if env is None:
            monkeypatch.delenv("PARTNER_TABLE_NAME", raising=False)
        else:
            monkeypatch.setenv("PARTNER_TABLE_NAME", env)
    with patch("boto3.Session"):
        return MockDataGenerator(GeneratorConfig(partner_table_name=partner_table_name, aws_profile=""))


def test_no_hardcoded_partner_table_name_in_seed_code():
    offenders = [
        f"{path.name}:{n}"
        for path in SOURCE.glob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"""["']cm-partners["']""", line)
    ]
    assert not offenders, f"hardcoded partner table name: {offenders}"


def test_config_name_wins_over_environment(monkeypatch):
    generator = _generator("from-config", monkeypatch, env="from-env")
    assert generator.TABLE_NAMES["partners"] == "from-config"


def test_environment_name_used_when_config_is_empty(monkeypatch):
    assert _generator("", monkeypatch, env="from-env").TABLE_NAMES["partners"] == "from-env"


def test_instances_do_not_share_or_mutate_the_class_mapping(monkeypatch):
    first = _generator("table-a", monkeypatch)
    second = _generator("table-b", monkeypatch)
    assert (first.TABLE_NAMES["partners"], second.TABLE_NAMES["partners"]) == ("table-a", "table-b")
    assert MockDataGenerator.TABLE_NAMES["partners"] == ""


def test_validation_fails_clearly_without_a_partner_table(monkeypatch, caplog):
    generator = _generator("", monkeypatch)
    generator.validate_table_access = MagicMock(return_value=True)
    assert generator.validate_all_tables() is False
    assert "PARTNER_TABLE_NAME" in caplog.text
    called = {call.args[0] for call in generator.validate_table_access.call_args_list}
    assert "" not in called


def test_clear_all_tables_never_clears_the_partner_table(monkeypatch):
    generator = _generator("live-partner-table", monkeypatch)
    generator.clear_table = MagicMock(return_value=True)
    assert generator.clear_all_tables() is True
    cleared = [call.args[0] for call in generator.clear_table.call_args_list]
    expected = [name for kind, name in generator.TABLE_NAMES.items() if kind != "partners"]
    assert cleared == expected


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ([{"OutputKey": "PartnerTableName", "OutputValue": "Stack-PartnerTable-ABC"}], "Stack-PartnerTable-ABC"),
        ([], ""),
    ],
)
def test_cli_resolves_the_partner_table_from_the_stack_output(outputs, expected):
    session = MagicMock()
    session.client.return_value.describe_stacks.return_value = {"Stacks": [{"Outputs": outputs}]}
    with patch("boto3.Session", return_value=session):
        assert generate_mock_data.resolve_partner_table_name("ContentMonetizationStack", "", "us-west-2") == expected
    session.client.return_value.describe_stacks.assert_called_once_with(StackName="ContentMonetizationStack")


def test_cli_returns_empty_when_the_stack_is_unreadable():
    from botocore.exceptions import ClientError

    session = MagicMock()
    session.client.return_value.describe_stacks.side_effect = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"
    )
    with patch("boto3.Session", return_value=session):
        assert generate_mock_data.resolve_partner_table_name("Missing", "", "us-west-2") == ""


def test_make_seed_targets_pass_the_stack_name():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    for target in ("seed-data", "generate-mock-data"):
        body = makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
        assert "generate_mock_data.py" in body and "--stack-name $(STACK_NAME)" in body, target
