"""Web demo tests: health endpoint and chat through the agent."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PAYPILOT_TRANSPORT", "mock")

from paypilot import agent as agent_module  # noqa: E402
from paypilot.agent import ModelResponse, ScriptedModel  # noqa: E402
from paypilot.web import app  # noqa: E402


@pytest.fixture
def web_client():
    return TestClient(app)


def test_health_reports_mock_backend(web_client):
    r = web_client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["backend"] == "mock"


def test_index_serves_demo_page(web_client):
    r = web_client.get("/")
    assert r.status_code == 200
    assert "PayPilot" in r.text
    assert "/api/chat" in r.text


def test_chat_runs_agent_and_returns_tool_calls(web_client, monkeypatch):
    scripted = ScriptedModel([
        ModelResponse(content="Here is your account summary.")])
    monkeypatch.setattr(agent_module, "build_model", lambda: scripted)
    r = web_client.post("/api/chat", json={"message": "summary please"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Here is your account summary."
    assert body["turns"] == 1


def test_chat_503_when_model_unconfigured(web_client, monkeypatch):
    from paypilot.errors import ConfigurationError

    def boom():
        raise ConfigurationError("no model")
    monkeypatch.setattr(agent_module, "build_model", boom)
    r = web_client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert "no model" in r.json()["error"]


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
def test_chat_rejects_blank_or_oversized_message(web_client, monkeypatch,
                                                 message):
    called = []
    monkeypatch.setattr(agent_module, "build_model",
                        lambda: called.append(1))
    r = web_client.post("/api/chat", json={"message": message})
    assert r.status_code == 422
    assert r.json()["error"].startswith("message")
    assert called == []  # rejected before any model is built


def test_validation_errors_use_error_shape(web_client):
    r = web_client.post("/api/confirm", json={})
    assert r.status_code == 422
    assert "action_id" in r.json()["error"]


def test_index_has_accessible_input_and_live_log(web_client):
    html = web_client.get("/").text
    assert '<label for="q"' in html
    assert 'role="log"' in html
