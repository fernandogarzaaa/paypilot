"""Agent loop tests with a scripted model (no network)."""
import pytest

from paypilot import agent as agent_module
from paypilot.agent import (AgentResult, ModelResponse, OpenAICompatibleModel,
                            ScriptedModel, ToolCall, build_model,
                            run_agent)
from paypilot.errors import ConfigurationError


def _tc(name, arguments, call_id="call-1"):
    return ToolCall(id=call_id, name=name, arguments=arguments)


def test_agent_runs_tool_loop_and_replies(client):
    created_id: list[str] = []

    class TwoStepModel:
        def __init__(self):
            self.n = 0

        def complete(self, messages, tools):
            self.n += 1
            assert tools, "agent must pass tool specs to the model"
            if self.n == 1:
                return ModelResponse(tool_calls=[
                    _tc("invoice_create",
                        {"recipient_email": "bill@acme.example",
                         "items": [{"name": "Design", "quantity": 1,
                                    "unit_price": 1250}]})])
            if self.n == 2:
                import json
                tool_content = messages[-1]["content"]
                created_id.append(json.loads(tool_content)["invoice_id"])
                return ModelResponse(tool_calls=[
                    _tc("invoice_send",
                        {"invoice_id": json.loads(tool_content)["invoice_id"]},
                        call_id="call-2")])
            return ModelResponse(content="Done: invoiced Acme Corp $1,250.")

    result = run_agent(client, TwoStepModel(),
                       "Invoice Acme Corp $1,250")
    assert isinstance(result, AgentResult)
    assert result.turns == 3
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0]["name"] == "invoice_create"
    assert result.tool_calls[0]["result"]["invoice_id"].startswith("INV2-")
    assert result.tool_calls[1]["name"] == "invoice_send"
    assert result.tool_calls[1]["result"]["sent"] is True
    assert "Acme Corp" in result.reply
    assert created_id and created_id[0].startswith("INV2-")


def test_agent_reports_tool_errors_instead_of_crashing(client):
    model = ScriptedModel([
        ModelResponse(tool_calls=[
            _tc("refund_payment", {"capture_id": "CAP-NOPE"})]),
        ModelResponse(content="That refund failed."),
    ])
    result = run_agent(client, model, "Refund it")
    assert result.tool_calls[0]["result"]["error"]
    assert "That refund failed" in result.reply


def test_agent_stops_at_max_turns(client):
    model = ScriptedModel([ModelResponse(tool_calls=[_tc("account_summary",
                                                              {})])] * 50)
    result = run_agent(client, model, "go", max_turns=3)
    assert result.turns == 3
    assert "max turns" in result.reply


def test_agent_sends_system_prompt_first(client):
    seen: list = []

    class SpyModel:
        def complete(self, messages, tools):
            seen.append(messages)
            return ModelResponse(content="ok")

    run_agent(client, SpyModel(), "hi")
    assert seen[0][0]["role"] == "system"
    assert "PayPilot" in seen[0][0]["content"]
    assert seen[0][1] == {"role": "user", "content": "hi"}


def test_build_model_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PAYPILOT_MODEL_BASE_URL", raising=False)
    with pytest.raises(ConfigurationError) as e:
        build_model()
    assert "PAYPILOT_MODEL_BASE_URL" in str(e.value)


def test_build_model_reads_env(monkeypatch):
    monkeypatch.setenv("PAYPILOT_MODEL_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("PAYPILOT_MODEL_API_KEY", "k")
    monkeypatch.setenv("PAYPILOT_MODEL_ID", "m")
    m = build_model()
    assert isinstance(m, OpenAICompatibleModel)
    assert m.base_url == "http://127.0.0.1:9/v1"
    assert m.model_id == "m"


def test_scripted_model_records_calls():
    model = ScriptedModel([ModelResponse(content="hi")])
    model.complete([{"role": "user", "content": "x"}],
                   [{"name": "invoice_list"}])
    assert model.calls[0]["tools"] == ["invoice_list"]
