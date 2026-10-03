"""PayPilot tool definitions.

One shared tool table drives the agent loop, the MCP server, and the web
UI, so all three surfaces expose exactly the same capabilities with the
same validation. Each spec is a JSON-Schema-style dict the model sees;
execute_tool() dispatches to the PayPalClient with argument validation.
"""
from __future__ import annotations

from typing import Any

from .errors import PayPalAPIError, ToolError
from .paypal_client import PayPalClient

TOOL_SPECS: list[dict] = [
    {
        "name": "invoice_create",
        "description": (
            "Create a DRAFT PayPal invoice. items is a list of objects with "
            "name, quantity (default 1) and unit_price, e.g. "
            "[{\"name\": \"Design work\", \"quantity\": 2, \"unit_price\": 150}]. "
            "due_days maps to standard payment terms (0 = due on receipt). "
            "Returns the invoice id; call invoice_send to actually send it."),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string",
                                    "description": "Payer's email address"},
                "recipient_name": {"type": "string",
                                   "description": "Payer's full name"},
                "items": {"type": "array",
                          "description": "Line items with name/quantity/unit_price",
                          "items": {"type": "object"}},
                "currency": {"type": "string", "default": "USD"},
                "due_days": {"type": "integer", "default": 30},
                "note": {"type": "string", "default": ""},
            },
            "required": ["recipient_email", "items"],
        },
    },
    {
        "name": "invoice_send",
        "description": (
            "Send a DRAFT invoice to the recipient by email. "
            "Only DRAFT invoices can be sent."),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string",
                               "description": "Invoice id, e.g. INV2-XXXX-XXXX-XXXX"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "invoice_remind",
        "description": (
            "Send a payment reminder for a SENT invoice that has not been "
            "paid yet."),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "note": {"type": "string", "default": ""},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "invoice_list",
        "description": (
            "List invoices. status filters: DRAFT, SENT, PAID, CANCELLED, "
            "or UNPAID (SENT but not paid). Omit status to list everything."),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "invoice_status",
        "description": "Get the current status and totals of one invoice.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "payment_search",
        "description": (
            "Search recent received payments. query filters by payer email "
            "or name substring, e.g. \"john\". Returns transaction id, "
            "capture id (needed for refunds), amount, and status."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "days_back": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "refund_payment",
        "description": (
            "Refund a captured payment. Use the capture_id from "
            "payment_search. Omit amount to refund in full."),
        "parameters": {
            "type": "object",
            "properties": {
                "capture_id": {"type": "string"},
                "amount": {"type": "string", "default": ""},
                "note": {"type": "string", "default": ""},
            },
            "required": ["capture_id"],
        },
    },
    {
        "name": "account_summary",
        "description": (
            "Receivables snapshot: outstanding (unpaid) invoices with totals, "
            "paid invoices, and recent received payments."),
        "parameters": {"type": "object", "properties": {}},
    },
]

_UNPAID = {"SENT"}


def _require(args: dict, name: str, tool: str) -> Any:
    if name not in args or args[name] in (None, ""):
        raise ToolError(f"{tool} requires argument '{name}'.")
    return args[name]


def execute_tool(client: PayPalClient, name: str,
                 arguments: dict) -> dict:
    """Run one tool against the PayPal client. Returns a JSON dict.

    Raises ToolError for unknown tools or invalid arguments, and
    PayPalAPIError when the PayPal API itself rejects the call.
    """
    args = dict(arguments or {})
    if name == "invoice_create":
        return client.create_invoice(
            to_email=_require(args, "recipient_email", name),
            to_name=str(args.get("recipient_name", "")),
            items=_require(args, "items", name),
            currency=str(args.get("currency") or "USD"),
            due_days=int(args.get("due_days") or 30),
            note=str(args.get("note") or ""))
    if name == "invoice_send":
        return client.send_invoice(_require(args, "invoice_id", name))
    if name == "invoice_remind":
        return client.remind_invoice(_require(args, "invoice_id", name),
                                     note=str(args.get("note") or ""))
    if name == "invoice_list":
        status = str(args.get("status") or "").upper()
        data = client.list_invoices(limit=int(args.get("limit") or 20))
        invs = data["invoices"]
        if status == "UNPAID":
            invs = [i for i in invs if i["status"] in _UNPAID]
        elif status:
            invs = [i for i in invs if i["status"] == status]
        return {"invoices": invs, "count": len(invs)}
    if name == "invoice_status":
        return client.get_invoice(_require(args, "invoice_id", name))
    if name == "payment_search":
        txns = client.search_transactions(
            days_back=int(args.get("days_back") or 30),
            query=str(args.get("query") or ""))
        return {"payments": txns, "count": len(txns)}
    if name == "refund_payment":
        amount = str(args.get("amount") or "").strip() or None
        return client.refund_capture(
            _require(args, "capture_id", name),
            amount=amount,
            note=str(args.get("note") or ""))
    if name == "account_summary":
        data = client.list_invoices(limit=100)
        invs = data["invoices"]
        outstanding = [i for i in invs if i["status"] in _UNPAID]
        paid = [i for i in invs if i["status"] == "PAID"]

        def _total(rows):
            return round(sum(float(r["total"] or 0) for r in rows), 2)

        txns = client.search_transactions(days_back=30)
        received = [t for t in txns if t["status"] == "COMPLETED"]
        currency = "USD"
        for row in outstanding + paid:
            if row.get("currency"):
                currency = row["currency"]
                break
        return {
            "outstanding_invoices": len(outstanding),
            "outstanding_total": _total(outstanding),
            "paid_invoices": len(paid),
            "paid_total": _total(paid),
            "payments_received_30d": len(received),
            "payments_total_30d": _total(
                [{"total": t["amount"]} for t in received]),
            "currency": currency,
        }
    raise ToolError(f"Unknown tool: {name!r}. Available: "
                    + ", ".join(t["name"] for t in TOOL_SPECS))
