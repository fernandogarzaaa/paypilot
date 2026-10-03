"""PayPilot web demo: a small chat UI driving the agent.

POST /api/chat {"message": "..."} runs the agent loop and returns the
reply plus every tool call made. GET / serves the demo page.
GET /api/health reports the active backend (mock or paypal-sandbox/live)
and whether a model is configured, so the demo never misleads about what
is behind it.

Run:  python -m paypilot.web [--host 127.0.0.1] [--port 8931]
"""
from __future__ import annotations

import argparse
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import agent as agent_module
from .errors import ConfigurationError, PayPilotError
from .paypal_client import PayPalClient

app = FastAPI(title="PayPilot demo")

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_HERE, "..", "webui", "index.html")


class ChatRequest(BaseModel):
    message: str


def _client() -> PayPalClient:
    return PayPalClient.from_env()


def _model():
    return agent_module.build_model()


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
    return {"ok": True, "backend": backend, "model": model}


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        client = _client()
        model = _model()
    except ConfigurationError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    try:
        result = agent_module.run_agent(client, model, req.message)
    except PayPilotError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return {"reply": result.reply, "tool_calls": result.tool_calls,
            "turns": result.turns}


@app.get("/", response_class=HTMLResponse)
def index():
    with open(_INDEX, encoding="utf-8") as f:
        return f.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="PayPilot web demo")
    parser.add_argument("--host", default=os.environ.get("PAYPILOT_WEB_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PAYPILOT_WEB_PORT",
                                                   "8931")))
    args = parser.parse_args()
    import uvicorn
    print(f"PayPilot web demo on http://{args.host}:{args.port}/",
          flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
