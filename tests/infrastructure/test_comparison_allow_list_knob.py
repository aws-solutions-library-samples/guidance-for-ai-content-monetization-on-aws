"""
The comparison run route's host allow-list is a deploy-time opt-in (spec scrape-vs-api-comparison,
R7.3 / D5-b): CDK context ``comparison-allowed-hosts`` (or the ``COMPARISON_ALLOWED_HOSTS``
environment variable at deploy time) becomes the ui_backend Lambda's ``COMPARISON_ALLOWED_HOSTS``.
Unset, the Lambda gets no such variable and the route falls back to the portal's own host, derived
at request time (``services/comparison_service.allowed_hosts``). The guard pins three things: the
default is closed, ``*`` is passed through verbatim (the service's open-mode sentinel), and a list is
passed through as given.
"""

from __future__ import annotations

import pytest
from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

UI_BACKEND = {"Description": "UI Backend Lambda - handles internal dashboard endpoints with Cognito auth"}


def _ui_backend_env(context: dict | None = None) -> dict:
    app = App(context=context or {})
    template = Template.from_stack(ContentMonetizationStack(app, "TestStack"))
    functions = template.find_resources("AWS::Lambda::Function", {"Properties": UI_BACKEND})
    assert len(functions) == 1, "expected exactly one ui_backend Lambda"
    return next(iter(functions.values()))["Properties"]["Environment"]["Variables"]


class TestComparisonAllowListKnob:
    def test_default_is_closed_to_the_portal_host(self, monkeypatch):
        """No context, no env var: the Lambda carries no allow-list and the service derives the portal host."""
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        assert "COMPARISON_ALLOWED_HOSTS" not in _ui_backend_env()

    def test_open_mode_sentinel_is_passed_through(self, monkeypatch):
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        assert _ui_backend_env({"comparison-allowed-hosts": "*"})["COMPARISON_ALLOWED_HOSTS"] == "*"

    def test_explicit_list_is_passed_through_as_given(self, monkeypatch):
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        env = _ui_backend_env({"comparison-allowed-hosts": "publisher.example, *.news.example"})
        assert env["COMPARISON_ALLOWED_HOSTS"] == "publisher.example, *.news.example"

    def test_environment_variable_is_the_fallback_and_context_wins(self, monkeypatch):
        monkeypatch.setenv("COMPARISON_ALLOWED_HOSTS", "env.example")
        assert _ui_backend_env()["COMPARISON_ALLOWED_HOSTS"] == "env.example"
        assert _ui_backend_env({"comparison-allowed-hosts": "*"})["COMPARISON_ALLOWED_HOSTS"] == "*"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_values_do_not_open_anything(self, monkeypatch, blank):
        monkeypatch.setenv("COMPARISON_ALLOWED_HOSTS", blank)
        assert "COMPARISON_ALLOWED_HOSTS" not in _ui_backend_env({"comparison-allowed-hosts": blank})

    def test_only_the_ui_backend_gets_the_knob(self, monkeypatch):
        """The partner-plane and worker Lambdas never fetch operator URLs; the knob must not leak to them."""
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        template = Template.from_stack(
            ContentMonetizationStack(App(context={"comparison-allowed-hosts": "*"}), "TestStack")
        )
        carrying = template.find_resources(
            "AWS::Lambda::Function",
            {"Properties": {"Environment": {"Variables": {"COMPARISON_ALLOWED_HOSTS": Match.any_value()}}}},
        )
        assert len(carrying) == 1
        assert next(iter(carrying.values()))["Properties"]["Description"] == UI_BACKEND["Description"]


class TestComparisonPublisherHostsKnob:
    """R15.4: ``comparison-publisher-hosts`` → ui_backend ``COMPARISON_PUBLISHER_HOSTS``; unset owns nothing."""

    def test_default_sets_nothing_so_every_run_is_external(self, monkeypatch):
        monkeypatch.delenv("COMPARISON_PUBLISHER_HOSTS", raising=False)
        assert "COMPARISON_PUBLISHER_HOSTS" not in _ui_backend_env()

    def test_context_is_passed_through_and_wins_over_the_env(self, monkeypatch):
        monkeypatch.setenv("COMPARISON_PUBLISHER_HOSTS", "env.example")
        assert _ui_backend_env()["COMPARISON_PUBLISHER_HOSTS"] == "env.example"
        env = _ui_backend_env({"comparison-publisher-hosts": "www.pub.example *.pub.example"})
        assert env["COMPARISON_PUBLISHER_HOSTS"] == "www.pub.example *.pub.example"
