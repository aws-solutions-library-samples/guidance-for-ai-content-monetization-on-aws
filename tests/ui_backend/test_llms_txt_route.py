"""Managed llms.txt UI backend route tests."""

import json
from unittest.mock import MagicMock

import pytest

from services.llms_txt_service import ConflictError, NotFoundError, PreconditionFailedError
from ui_backend.routes import llms_txt


@pytest.fixture
def service(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(llms_txt, "_service", mock)
    return mock


def _event(body=None, etag=None):
    event = {
        "requestContext": {"authorizer": {"claims": {"sub": "user-1"}}},
        "headers": {},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    if etag is not None:
        event["headers"]["If-Match"] = etag
    return event


def test_get_state(service):
    service.get_management_state.return_value = {"published": {"content": "# P"}}
    response = llms_txt.route(_event(), "GET", "/content/llms-txt")
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["published"]["content"] == "# P"


def test_start_run_returns_202_and_actor(service):
    service.start_run.return_value = {"run": {"run_id": "r1", "status": "queued"}, "already_running": False}
    response = llms_txt.route(_event(), "POST", "/content/llms-txt/runs")
    assert response["statusCode"] == 202
    service.start_run.assert_called_once_with("user-1")


def test_get_run_extracts_path_id(service):
    service.get_run.return_value = {"run_id": "r1", "status": "completed"}
    response = llms_txt.route(_event(), "GET", "/content/llms-txt/runs/r1")
    assert response["statusCode"] == 200
    service.get_run.assert_called_once_with("r1")


def test_update_requires_if_match(service):
    response = llms_txt.route(
        _event({"content": "# Edited"}),
        "PUT",
        "/content/llms-txt/drafts/r1",
    )
    assert response["statusCode"] == 412
    service.update_draft.assert_not_called()


def test_update_and_publish_forward_etag(service):
    service.update_draft.return_value = {"run_id": "r1", "etag": '"rev-2"'}
    update = llms_txt.route(
        _event({"content": "# Edited"}, '"rev-1"'),
        "PUT",
        "/content/llms-txt/drafts/r1",
    )
    assert update["statusCode"] == 200
    service.update_draft.assert_called_once_with("r1", "# Edited", '"rev-1"', "user-1")

    service.publish_draft.return_value = {"run_id": "r1", "status": "published"}
    publish = llms_txt.route(
        _event(etag='"rev-2"'),
        "POST",
        "/content/llms-txt/drafts/r1/publish",
    )
    assert publish["statusCode"] == 200
    service.publish_draft.assert_called_once_with("r1", '"rev-2"', "user-1")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("missing"), 404),
        (ConflictError("conflict"), 409),
        (PreconditionFailedError("stale"), 412),
    ],
)
def test_domain_errors_map_to_contract_status(service, error, status):
    service.get_management_state.side_effect = error
    response = llms_txt.route(_event(), "GET", "/content/llms-txt")
    assert response["statusCode"] == status


def test_unknown_path_returns_404(service):
    response = llms_txt.route(_event(), "GET", "/content/llms-txt/unknown")
    assert response["statusCode"] == 404
