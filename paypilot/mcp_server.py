"""PayPilot MCP server: PayPal operations as MCP tools over Streamable HTTP.

Every tool is backed by the PayPalClient, so the server speaks to the real
PayPal sandbox (or the local mock when PAYPILOT_TRANSPORT=mock). This is
the agent-commerce surface: any MCP client can drive invoices, payment
lookup, and refunds through it.

Run:  python -m paypilot.mcp_server [--host 127.0.0.1] [--port 8899]
"""
from __future__ import annotations

import argparse
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import LATEST_PROTOCOL_VERSION

from . import tools
from .errors import PayPilotError
from .paypal_client import PayPalClient

SERVER_NAME = "paypilot"
SERVER_VERSION = "0.1.0"


_client_instance: "PayPalClient | None" = None


def _client() -> PayPalClient:
    """Process-level client singleton.

    One client per server process: the OAuth token cache is shared, and
    the mock transport (when PAYPILOT_TRANSPORT=mock) keeps its state
    across tool calls so multi-step flows work.
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = PayPalClient.from_env()
    return _client_instance


def _run(name: str, args: dict) -> str:
    try:
        return json.dumps(tools.execute_tool(_client(), name, args))
    except PayPilotError as e:
        return json.dumps({"error": str(e)})


mcp = MCPServer(SERVER_NAME, version=SERVER_VERSION)


@mcp.tool()
def invoice_create(recipient_email: str, items: str,
                   recipient_name: str = "", currency: str = "USD",
                   due_days: int = 30, note: str = "") -> str:
    """Create a DRAFT PayPal invoice. items is a JSON array like
    [{"name": "Design work", "quantity": 2, "unit_price": 150}].
    Returns the invoice id; call invoice_send to send it."""
    return _run("invoice_create", {"recipient_email": recipient_email,
                                   "recipient_name": recipient_name,
                                   "items": json.loads(items),
                                   "currency": currency,
                                   "due_days": due_days, "note": note})


@mcp.tool()
def invoice_send(invoice_id: str) -> str:
    """Send a DRAFT invoice to the recipient by email."""
    return _run("invoice_send", {"invoice_id": invoice_id})


@mcp.tool()
def invoice_remind(invoice_id: str, note: str = "") -> str:
    """Send a payment reminder for a SENT unpaid invoice."""
    return _run("invoice_remind", {"invoice_id": invoice_id, "note": note})


@mcp.tool()
def invoice_list(status: str = "", limit: int = 20) -> str:
    """List invoices. status: DRAFT, SENT, PAID, CANCELLED, or UNPAID."""
    return _run("invoice_list", {"status": status, "limit": limit})


@mcp.tool()
def invoice_status(invoice_id: str) -> str:
    """Current status and totals of one invoice."""
    return _run("invoice_status", {"invoice_id": invoice_id})


@mcp.tool()
def payment_search(query: str = "", days_back: int = 30) -> str:
    """Search recent received payments by payer email or name."""
    return _run("payment_search", {"query": query, "days_back": days_back})


@mcp.tool()
def refund_payment(capture_id: str, amount: str = "",
                   note: str = "") -> str:
    """Refund a captured payment (full refund when amount is omitted)."""
    return _run("refund_payment", {"capture_id": capture_id,
                                   "amount": amount, "note": note})


@mcp.tool()
def account_summary() -> str:
    """Receivables snapshot: outstanding/paid invoices and recent payments."""
    return _run("account_summary", {})


def main() -> None:
    parser = argparse.ArgumentParser(description="PayPilot MCP server")
    parser.add_argument("--host", default=os.environ.get("PAYPILOT_MCP_HOST",
                                                         "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PAYPILOT_MCP_PORT",
                                                   "8899")))
    args = parser.parse_args()
    backend = PayPalClient.from_env().backend
    print(f"PayPilot MCP server {SERVER_VERSION} on "
          f"http://{args.host}:{args.port}/mcp "
          f"(protocol {LATEST_PROTOCOL_VERSION}, backend {backend})",
          flush=True)
    mcp.run(transport="streamable-http", host=args.host, port=args.port,
            streamable_http_path="/mcp")


if __name__ == "__main__":
    main()
