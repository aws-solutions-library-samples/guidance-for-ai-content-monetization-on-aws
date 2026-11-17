"""The mock data generator has no default AWS profile; boto3's standard credential chain applies."""

import argparse
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "source"))

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator


def test_config_default_profile_is_none():
    assert GeneratorConfig().aws_profile is None


def test_session_is_created_without_profile_name_when_unset():
    with patch("mock_data_generator.generator.boto3.Session") as session:
        MockDataGenerator(GeneratorConfig(partner_table_name="partners"))
    session.assert_called_once_with(region_name="us-west-2")


@pytest.mark.parametrize("module", ["generate_mock_data", "seed_bot_rules_and_licenses"])
def test_cli_aws_profile_defaults_to_none(module):
    mod = importlib.import_module(f"mock_data_generator.{module}")
    captured = {}
    real_init = argparse.ArgumentParser.add_argument

    def spy(self, *args, **kwargs):
        if "--aws-profile" in args:
            captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    with patch.object(argparse.ArgumentParser, "add_argument", spy):
        parser_factory = getattr(mod, "parse_arguments", None) or getattr(mod, "parse_args", None)
        assert parser_factory is not None, f"{module} has no argument parser function"
        with patch.object(sys, "argv", [module]):
            try:
                parser_factory()
            except SystemExit:
                pass
    assert captured.get("default") is None


def test_boto3_session_accepts_none_profile():
    import boto3

    assert boto3.Session(profile_name=None, region_name="us-west-2").region_name == "us-west-2"
