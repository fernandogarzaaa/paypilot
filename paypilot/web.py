"""PayPilot web demo: a small chat UI driving the agent.

POST /api/chat {"message": "..."} runs the agent loop and returns the
reply plus every tool call made. GET / serves the demo page.
GET /api/health reports the active backend (mock or paypal-sandbox/live)
and whether a model is configured, so the demo never misleads about what
is behind it.

Safety:
- PAYPILOT_API_KEY: when set, /api/chat, /api/confirm and /api/cancel
  require `Authorization: Bearer <key>` (or `X-API-Key: <key>`). When it is
  NOT set, those endpoints only work against the in-memory mock backend; a
  real PayPal backend (sandbox or live) refuses to run without a key.
- Money-moving / customer-facing tools (tools.CONFIRM_REQUIRED: refunds,
  sending invoices) are never executed straight from the model. The chat
  response lists them under `pending_actions`; a human confirms one with
  POST /api/confirm {"action_id": "..."} (or drops it via /api/cancel).
  Pending actions are single-use and expire after PENDING_TTL_S seconds.

Run:  python -m paypilot.web [--host 127.0.0.1] [--port 8931]
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import os
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import agent as agent_module
from . import tools as tool_module
from .errors import ConfigurationError, PayPilotError
from .paypal_client import PayPalClient

app = FastAPI(title="PayPilot demo")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_HERE, "..", "webui", "index.html")

PENDING_TTL_S = 600
PENDING_MAX = 200


class ChatRequest(BaseModel):
    message: str


class ActionRequest(BaseModel):
    action_id: str


@dataclass
class PendingAction:
    id: str
    name: str
    arguments: dict
    client: PayPalClient
    created_at: float


_pending: dict[str, PendingAction] = {}
_pending_lock = threading.Lock()


def _client() -> PayPalClient:
    return PayPalClient.from_env()


def _model():
    return agent_module.build_model()


def _api_key() -> str:
    return os.environ.get("PAYPILOT_API_KEY", "").strip()


def _presented_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _auth_error(request: Request) -> JSONResponse | None:
    """401 if an API key is configured and the request does not carry it."""
    key = _api_key()
    if not key:
        return None
    if hmac.compare_digest(_presented_key(request).encode(), key.encode()):
        return None
    return JSONResponse({"error": "unauthorized: valid API key required"},
                        status_code=401)


def _backend_guard(client: PayPalClient) -> JSONResponse | None:
    """A real PayPal backend is never exposed without an API key."""
    if _api_key() or client.backend == "mock":
        return None
    return JSONResponse(
        {"error": "PAYPILOT_API_KEY must be set to use a real PayPal "
                  f"backend ({client.backend}) through the web UI."},
        status_code=503)


def _prune(now: float) -> None:
    for aid in [a for a, p in _pending.items()
                if now - p.created_at > PENDING_TTL_S]:
        del _pending[aid]


def _hold(client: PayPalClient, created: list[PendingAction]):
    def gate(name: str, arguments: dict) -> dict | None:
        if name not in tool_module.CONFIRM_REQUIRED:
            return None
        now = time.time()
        action = PendingAction(id=secrets.token_urlsafe(16), name=name,
                               arguments=dict(arguments), client=client,
                               created_at=now)
        with _pending_lock:
            _prune(now)
            if len(_pending) >= PENDING_MAX:
                return {"error": "too many pending actions; confirm or "
                                 "cancel some first"}
            _pending[action.id] = action
        created.append(action)
        return {"status": "pending_confirmation", "action_id": action.id,
                "note": f"{name} was NOT executed. A human must confirm it "
                        "in the UI before it runs. Tell the user what is "
                        "waiting for their confirmation."}
    return gate


def _take(action_id: str) -> PendingAction | None:
    with _pending_lock:
        _prune(time.time())
        return _pending.pop(action_id, None)


@app.get("/api/health")
def health():
    try:
        backend = _client().backend
    except ConfigurationError as e:
        return {"ok": False, "error": str(e)}
    try:
        _model()
        model = "configured"
    except ConfigurationError:
        model = "unconfigured"
    return {"ok": True, "backend": backend, "model": model,
            "auth_required": bool(_api_key()),
            "confirm_required": sorted(tool_module.CONFIRM_REQUIRED)}


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    denied = _auth_error(request)
    if denied:
        return denied
    try:
        client = _client()
        model = _model()
    except ConfigurationError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    blocked = _backend_guard(client)
    if blocked:
        return blocked
    held: list[PendingAction] = []
    try:
        result = agent_module.run_agent(client, model, req.message,
                                        gate=_hold(client, held))
    except PayPilotError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"reply": result.reply, "tool_calls": result.tool_calls,
            "turns": result.turns,
            "pending_actions": [{"action_id": a.id, "name": a.name,
                                 "arguments": a.arguments} for a in held]}


@app.post("/api/confirm")
def confirm(req: ActionRequest, request: Request):
    denied = _auth_error(request)
    if denied:
        return denied
    action = _take(req.action_id)
    if action is None:
        return JSONResponse({"error": "unknown or expired action"},
                            status_code=404)
    try:
        result = tool_module.execute_tool(action.client, action.name,
                                          action.arguments)
    except PayPilotError as e:
        return JSONResponse({"error": str(e), "name": action.name},
                            status_code=502)
    return {"executed": action.name, "arguments": action.arguments,
            "result": result}


@app.post("/api/cancel")
def cancel(req: ActionRequest, request: Request):
    denied = _auth_error(request)
    if denied:
        return denied
    action = _take(req.action_id)
    if action is None:
        return JSONResponse({"error": "unknown or expired action"},
                            status_code=404)
    return {"cancelled": action.name}


@app.get("/", response_class=HTMLResponse)
def index():
    with open(_INDEX, encoding="utf-8") as f:
        return f.read()


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str) -> None:
    """Refuse a non-loopback bind without an API key.

    The only exception is an explicit, mock-only public demo:
    PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO=1 with PAYPILOT_TRANSPORT=mock.
    """
    if _is_loopback(host) or _api_key():
        return
    demo = os.environ.get("PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO") == "1"
    mock = os.environ.get("PAYPILOT_TRANSPORT", "").strip().lower() == "mock"
    if demo and mock:
        return
    raise SystemExit(
        f"Refusing to bind {host} without PAYPILOT_API_KEY. Set a key, bind "
        "127.0.0.1, or (mock backend only) set "
        "PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO=1.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PayPilot web demo")
    parser.add_argument("--host", default=os.environ.get("PAYPILOT_WEB_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PAYPILOT_WEB_PORT",
                                                   "8931")))
    args = parser.parse_args()
    check_bind(args.host)
    import uvicorn
    print(f"PayPilot web demo on http://{args.host}:{args.port}/",
          flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
