"""Audit fixes (Oct 2026): API-key auth, human confirmation for money-moving
tools, real-backend guard, and refusing open public binds."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PAYPILOT_TRANSPORT", "mock")

from paypilot import agent as agent_module  # noqa: E402
from paypilot import mcp_server, web  # noqa: E402
from paypilot.agent import ModelResponse, ScriptedModel, ToolCall  # noqa: E402

REFUND = ToolCall(id="c1", name="refund_payment",
                  arguments={"capture_id": "CAP-9RU12345AB"})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PAYPILOT_TRANSPORT", "mock")
    monkeypatch.delenv("PAYPILOT_API_KEY", raising=False)
    monkeypatch.delenv("PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO", raising=False)
    monkeypatch.delenv("PAYPILOT_MCP_ALLOW_REMOTE", raising=False)
    web._pending.clear()
    yield
    web._pending.clear()


@pytest.fixture
def api():
    return TestClient(web.app)


def _script(monkeypatch, *responses):
    model = ScriptedModel(list(responses))
    monkeypatch.setattr(agent_module, "build_model", lambda: model)
    return model


def _refund_flow(monkeypatch):
    return _script(monkeypatch,
                   ModelResponse(tool_calls=[REFUND]),
                   ModelResponse(content="Refund is waiting for you."))


def _spy_refunds(monkeypatch):
    calls = []
    real = web.tool_module.execute_tool

    def spy(client, name, args):
        calls.append(name)
        return real(client, name, args)
    monkeypatch.setattr(web.tool_module, "execute_tool", spy)
    monkeypatch.setattr(agent_module.tool_module, "execute_tool", spy)
    return calls


# -- confirmation gate -------------------------------------------------------
def test_refund_is_held_not_executed(api, monkeypatch):
    calls = _spy_refunds(monkeypatch)
    _refund_flow(monkeypatch)
    body = api.post("/api/chat", json={"message": "refund john"}).json()
    assert calls == []
    assert len(body["pending_actions"]) == 1
    pending = body["pending_actions"][0]
    assert pending["name"] == "refund_payment"
    assert pending["arguments"] == {"capture_id": "CAP-9RU12345AB"}
    held = body["tool_calls"][0]["result"]
    assert held["status"] == "pending_confirmation"
    assert held["action_id"] == pending["action_id"]
    assert "NOT executed" in held["note"]


def test_confirm_executes_once(api, monkeypatch):
    calls = _spy_refunds(monkeypatch)
    _refund_flow(monkeypatch)
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    r = api.post("/api/confirm", json={"action_id": aid})
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] == "refund_payment"
    assert body["result"]["status"] == "COMPLETED"
    assert calls == ["refund_payment"]
    again = api.post("/api/confirm", json={"action_id": aid})
    assert again.status_code == 404
    assert calls == ["refund_payment"]


def test_cancel_drops_action(api, monkeypatch):
    calls = _spy_refunds(monkeypatch)
    _refund_flow(monkeypatch)
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    r = api.post("/api/cancel", json={"action_id": aid})
    assert r.json() == {"cancelled": "refund_payment"}
    assert api.post("/api/confirm", json={"action_id": aid}).status_code == 404
    assert api.post("/api/cancel", json={"action_id": aid}).status_code == 404
    assert calls == []


def test_unknown_action_is_404(api):
    r = api.post("/api/confirm", json={"action_id": "nope"})
    assert r.status_code == 404
    assert "unknown" in r.json()["error"]


def test_expired_action_cannot_be_confirmed(api, monkeypatch):
    _refund_flow(monkeypatch)
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    real = web.time.time
    monkeypatch.setattr(web.time, "time", lambda: real() + web.PENDING_TTL_S + 1)
    assert api.post("/api/confirm", json={"action_id": aid}).status_code == 404


def test_action_just_inside_ttl_still_confirms(api, monkeypatch):
    _refund_flow(monkeypatch)
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    created = web._pending[aid].created_at
    monkeypatch.setattr(web.time, "time", lambda: created + web.PENDING_TTL_S)
    assert api.post("/api/confirm", json={"action_id": aid}).status_code == 200


def test_invoice_send_is_also_held_but_reads_are_not(api, monkeypatch):
    calls = _spy_refunds(monkeypatch)
    _script(monkeypatch,
            ModelResponse(tool_calls=[
                ToolCall(id="a", name="invoice_list", arguments={}),
                ToolCall(id="b", name="invoice_send",
                         arguments={"invoice_id": "INV2-X"})]),
            ModelResponse(content="ok"))
    body = api.post("/api/chat", json={"message": "x"}).json()
    assert calls == ["invoice_list"]
    assert [p["name"] for p in body["pending_actions"]] == ["invoice_send"]


def test_confirm_reports_tool_errors(api, monkeypatch):
    _script(monkeypatch,
            ModelResponse(tool_calls=[ToolCall(
                id="c", name="refund_payment",
                arguments={"capture_id": "CAP-NOPE"})]),
            ModelResponse(content="ok"))
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    r = api.post("/api/confirm", json={"action_id": aid})
    assert r.status_code == 502
    assert r.json()["name"] == "refund_payment"


def test_pending_cap(api, monkeypatch):
    monkeypatch.setattr(web, "PENDING_MAX", 1)
    _script(monkeypatch,
            ModelResponse(tool_calls=[REFUND, ToolCall(
                id="c2", name="refund_payment",
                arguments={"capture_id": "CAP-7XY98765ZZ"})]),
            ModelResponse(content="ok"))
    body = api.post("/api/chat", json={"message": "x"}).json()
    assert len(body["pending_actions"]) == 1
    assert "too many" in body["tool_calls"][1]["result"]["error"]


def test_confirm_required_set():
    assert web.tool_module.CONFIRM_REQUIRED == {"refund_payment", "invoice_send"}


def test_run_agent_without_gate_still_executes(monkeypatch):
    from paypilot.paypal_client import PayPalClient
    model = ScriptedModel([ModelResponse(tool_calls=[REFUND]),
                           ModelResponse(content="done")])
    res = agent_module.run_agent(PayPalClient.from_env(), model, "x")
    assert res.tool_calls[0]["result"]["status"] == "COMPLETED"


# -- API key ------------------------------------------------------------------
@pytest.mark.parametrize("path,body", [
    ("/api/chat", {"message": "hi"}),
    ("/api/confirm", {"action_id": "x"}),
    ("/api/cancel", {"action_id": "x"}),
])
def test_key_required_when_configured(api, monkeypatch, path, body):
    monkeypatch.setenv("PAYPILOT_API_KEY", "s3cret")
    _script(monkeypatch, ModelResponse(content="hi"))
    assert api.post(path, json=body).status_code == 401
    assert api.post(path, json=body,
                    headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = api.post(path, json=body, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code != 401


def test_key_via_x_api_key_header(api, monkeypatch):
    monkeypatch.setenv("PAYPILOT_API_KEY", "s3cret")
    _script(monkeypatch, ModelResponse(content="hi"))
    r = api.post("/api/chat", json={"message": "hi"},
                 headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200 and r.json()["reply"] == "hi"
    r = api.post("/api/chat", json={"message": "hi"},
                 headers={"authorization": "bearer s3cret"})
    assert r.status_code == 200


def test_health_reports_auth_and_confirm(api, monkeypatch):
    assert api.get("/api/health").json()["auth_required"] is False
    monkeypatch.setenv("PAYPILOT_API_KEY", "k")
    h = api.get("/api/health").json()
    assert h["auth_required"] is True
    assert h["confirm_required"] == ["invoice_send", "refund_payment"]


# -- real backend guard -------------------------------------------------------
class _RealClient:
    backend = "paypal-live"


def test_real_backend_refused_without_key(api, monkeypatch):
    monkeypatch.setattr(web, "_client", lambda: _RealClient())
    _script(monkeypatch, ModelResponse(content="hi"))
    r = api.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert "PAYPILOT_API_KEY" in r.json()["error"]


def test_real_backend_allowed_with_key(api, monkeypatch):
    monkeypatch.setenv("PAYPILOT_API_KEY", "k")
    monkeypatch.setattr(web, "_client", lambda: _RealClient())
    _script(monkeypatch, ModelResponse(content="hi"))
    r = api.post("/api/chat", json={"message": "hi"},
                 headers={"Authorization": "Bearer k"})
    assert r.status_code == 200


# -- bind checks ----------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_binds_allowed(host):
    web.check_bind(host)
    mcp_server.check_bind(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "example.com"])
def test_public_bind_refused_without_key(host):
    with pytest.raises(SystemExit):
        web.check_bind(host)
    with pytest.raises(SystemExit):
        mcp_server.check_bind(host)


def test_public_bind_allowed_with_key(monkeypatch):
    monkeypatch.setenv("PAYPILOT_API_KEY", "k")
    web.check_bind("0.0.0.0")


def test_public_demo_opt_in_only_for_mock(monkeypatch):
    monkeypatch.setenv("PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO", "1")
    web.check_bind("0.0.0.0")
    monkeypatch.setenv("PAYPILOT_TRANSPORT", "")
    with pytest.raises(SystemExit):
        web.check_bind("0.0.0.0")


def test_mcp_remote_opt_in(monkeypatch):
    monkeypatch.setenv("PAYPILOT_MCP_ALLOW_REMOTE", "1")
    mcp_server.check_bind("0.0.0.0")



# -- mutation-testing follow-ups ------------------------------------------------
def test_model_is_told_the_action_is_pending(api, monkeypatch):
    model = _refund_flow(monkeypatch)
    api.post("/api/chat", json={"message": "x"})
    tool_msgs = [m for m in model.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert "pending_confirmation" in tool_msgs[0]["content"]


def test_ttl_is_ten_minutes(api, monkeypatch):
    _refund_flow(monkeypatch)
    aid = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    created = web._pending[aid].created_at
    monkeypatch.setattr(web.time, "time", lambda: created + 600.5)
    assert api.post("/api/confirm", json={"action_id": aid}).status_code == 404


def test_new_hold_prunes_expired_actions(api, monkeypatch):
    _refund_flow(monkeypatch)
    old = api.post("/api/chat", json={"message": "x"}).json()[
        "pending_actions"][0]["action_id"]
    real = web.time.time
    monkeypatch.setattr(web.time, "time", lambda: real() + 601)
    _refund_flow(monkeypatch)
    api.post("/api/chat", json={"message": "y"})
    assert old not in web._pending and len(web._pending) == 1


def test_hostname_is_not_loopback():
    assert web._is_loopback("example.com") is False
    assert web._is_loopback("127.0.0.1") is True


def test_web_main_checks_bind_before_serving(monkeypatch):
    import sys

    import uvicorn
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: served.append(k))
    monkeypatch.setattr(sys, "argv", ["paypilot.web", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        web.main()
    assert served == []
    monkeypatch.setattr(sys, "argv", ["paypilot.web", "--port", "9999"])
    web.main()
    assert served == [{"host": "127.0.0.1", "port": 9999, "log_level": "warning"}]


def test_mcp_main_checks_bind_before_serving(monkeypatch):
    import sys
    served = []
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **k: served.append(k))
    monkeypatch.setattr(sys, "argv", ["mcp", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        mcp_server.main()
    assert served == []
    monkeypatch.setattr(sys, "argv", ["mcp"])
    mcp_server.main()
    assert served[0]["host"] == "127.0.0.1" and served[0]["port"] == 8899
