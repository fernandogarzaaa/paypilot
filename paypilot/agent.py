"""PayPilot agent: a compact tool-calling loop over the PayPal tools.

The agent is deliberately small and dependency-free: a ReAct-style loop
that sends the conversation plus tool specs to a chat model, executes
returned tool calls against the PayPalClient, and repeats until the
model answers or max_turns is hit.

Model providers (no silent fallbacks):
- Any OpenAI-compatible chat-completions endpoint with tool calling,
  configured via PAYPILOT_MODEL_BASE_URL / PAYPILOT_MODEL_API_KEY /
  PAYPILOT_MODEL_ID.
- ScriptedModel: canned responses for the test suite (no network).

When nothing is configured, build_model() raises ConfigurationError with
an actionable message.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from . import tools as tool_module
from .errors import ConfigurationError, PayPilotError
from .paypal_client import PayPalClient

SYSTEM_PROMPT = """You are PayPilot, an agentic commerce operator for PayPal.
You turn natural-language requests into PayPal operations using your tools.

Rules:
- Use invoice_create then invoice_send to bill someone; never invent an
  invoice id, always use the id returned by invoice_create.
- invoice_list with status UNPAID answers "who hasn't paid yet".
- payment_search finds received payments by payer email or name; its
  capture_id is what refund_payment needs.
- After acting, summarize what you did in one or two plain sentences with
  the concrete ids and amounts. Do not dump raw JSON at the user.
- If a tool call fails, report the failure plainly and stop; do not retry
  blindly more than once.
- All money is USD unless the user says otherwise.
"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelClient(Protocol):
    def complete(self, messages: list[dict],
                 tools: list[dict]) -> ModelResponse:
        ...


class OpenAICompatibleModel:
    """Any OpenAI-compatible /chat/completions endpoint with tool calling."""

    def __init__(self, base_url: str, api_key: str, model_id: str,
                 timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.timeout = timeout

    def complete(self, messages: list[dict],
                 tools: list[dict]) -> ModelResponse:
        payload = {
            "model": self.model_id,
            "messages": messages,
            "tools": [{"type": "function",
                       "function": {"name": t["name"],
                                    "description": t["description"],
                                    "parameters": t["parameters"]}}
                      for t in tools],
            "tool_choice": "auto",
            "temperature": 0,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req,
                                        timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except OSError as e:
            raise PayPilotError(f"model request failed: {e}") from None
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError):
            raise PayPilotError(
                "model returned no choices; is the endpoint OpenAI-compatible?")
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.get("id", ""),
                                  name=fn.get("name", ""),
                                  arguments=args))
        return ModelResponse(content=msg.get("content") or "",
                             tool_calls=calls)


class ScriptedModel:
    """Canned model responses for tests. Each complete() pops one response."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages: list[dict],
                 tools: list[dict]) -> ModelResponse:
        self.calls.append({"messages": messages,
                           "tools": [t["name"] for t in tools]})
        if not self._responses:
            return ModelResponse(content="done")
        return self._responses.pop(0)


def build_model(provider: str = "auto") -> OpenAICompatibleModel:
    """Build the agent's model from the environment.

    PAYPILOT_MODEL_BASE_URL (required), PAYPILOT_MODEL_API_KEY,
    PAYPILOT_MODEL_ID. Raises ConfigurationError when unconfigured.
    """
    base_url = os.environ.get("PAYPILOT_MODEL_BASE_URL", "").strip()
    if not base_url:
        raise ConfigurationError(
            "No model configured. Set PAYPILOT_MODEL_BASE_URL (+ "
            "PAYPILOT_MODEL_API_KEY, PAYPILOT_MODEL_ID) to any "
            "OpenAI-compatible chat-completions endpoint with tool calling.")
    return OpenAICompatibleModel(
        base_url=base_url,
        api_key=os.environ.get("PAYPILOT_MODEL_API_KEY", "not-needed"),
        model_id=os.environ.get("PAYPILOT_MODEL_ID", ""))


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict]  # [{name, arguments, result}]
    turns: int


def run_agent(client: PayPalClient, model: ModelClient,
              user_message: str, max_turns: int = 10) -> AgentResult:
    """Run the agent loop. Returns the final reply and every tool call made."""
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    calls: list[dict] = []
    turns = 0
    while turns < max_turns:
        turns += 1
        resp = model.complete(messages, tool_module.TOOL_SPECS)
        if not resp.tool_calls:
            reply = resp.content.strip() or "(no reply)"
            return AgentResult(reply=reply, tool_calls=calls, turns=turns)
        messages.append({
            "role": "assistant",
            "content": resp.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name,
                              "arguments": json.dumps(tc.arguments)}}
                for tc in resp.tool_calls],
        })
        for tc in resp.tool_calls:
            try:
                result = tool_module.execute_tool(client, tc.name,
                                                 tc.arguments)
                record = {"name": tc.name, "arguments": tc.arguments,
                          "result": result}
            except PayPilotError as e:
                record = {"name": tc.name, "arguments": tc.arguments,
                          "result": {"error": str(e)}}
            calls.append(record)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(record["result"]),
            })
    return AgentResult(
        reply="(stopped after max turns without a final answer)",
        tool_calls=calls, turns=turns)
