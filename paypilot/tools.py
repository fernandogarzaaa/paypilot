"""PayPilot tool definitions.

One shared tool table drives the agent loop, the MCP server, and the web
UI, so all three surfaces expose exactly the same capabilities with the
same validation. Each spec is a JSON-Schema-style dict the model sees;
execute_tool() dispatches to the PayPalClient with argument validation.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
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
                "currency": {"type": "string", "default": "",
                             "description": "Currency of the original "
                             "payment; looked up from recent payments "
                             "when omitted."},
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

# Tools that move money or send something to a customer. The web UI never
# runs these straight from the model: it holds them as pending actions that
# a human must confirm (POST /api/confirm).
CONFIRM_REQUIRED: frozenset[str] = frozenset({"refund_payment", "invoice_send"})


def _require(args: dict, name: str, tool: str) -> Any:
    if name not in args or args[name] in (None, ""):
        raise ToolError(f"{tool} requires argument '{name}'.")
    return args[name]


def _int_arg(args: dict, name: str, tool: str, default: int,
             lo: int, hi: int) -> int:
    """Coerce an integer tool argument, raising ToolError on junk.

    A bare int() on model-supplied input ("thirty", "", 1.5e9) raised a raw
    ValueError that escaped the agent loop's PayPilotError handling and
    turned one bad tool call into an HTTP 500 for the whole chat turn.
    """
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
        if isinstance(raw, float) and raw != value:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ToolError(f"{tool}: '{name}' must be an integer, got {raw!r}.")
    if not lo <= value <= hi:
        raise ToolError(f"{tool}: '{name}' must be between {lo} and {hi}, "
                        f"got {value}.")
    return value


def _sum_by_currency(rows: list[dict], key: str, fallback: str) -> dict:
    """Exact (Decimal) totals per currency, rounded to cents."""
    totals: dict[str, Decimal] = {}
    for r in rows:
        cur = str(r.get("currency") or fallback)
        try:
            amt = Decimal(str(r.get(key) or 0))
        except InvalidOperation:
            amt = Decimal(0)
        totals[cur] = totals.get(cur, Decimal(0)) + amt
    return {c: float(v.quantize(Decimal("0.01"))) for c, v in totals.items()}


def _capture_currency(client: PayPalClient, capture_id: str) -> str | None:
    """Currency of a recent capture, so a partial refund is not sent as USD
    against a EUR/GBP/... payment (PayPal rejects the currency mismatch)."""
    for txn in client.search_transactions(days_back=180):
        if capture_id in (txn.get("capture_id"), txn.get("transaction_id")):
            return txn.get("currency") or None
    return None


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
            due_days=_int_arg(args, "due_days", name, 30, 0, 365),
            note=str(args.get("note") or ""))
    if name == "invoice_send":
        return client.send_invoice(_require(args, "invoice_id", name))
    if name == "invoice_remind":
        return client.remind_invoice(_require(args, "invoice_id", name),
                                     note=str(args.get("note") or ""))
    if name == "invoice_list":
        status = str(args.get("status") or "").upper()
        data = client.list_invoices(limit=_int_arg(args, "limit", name, 20, 1, 100))
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
            days_back=_int_arg(args, "days_back", name, 30, 1, 1095),
            query=str(args.get("query") or ""))
        return {"payments": txns, "count": len(txns)}
    if name == "refund_payment":
        amount = str(args.get("amount") or "").strip() or None
        capture_id = _require(args, "capture_id", name)
        currency = str(args.get("currency") or "").strip().upper()
        if amount is not None and not currency:
            currency = _capture_currency(client, capture_id) or "USD"
        return client.refund_capture(
            capture_id,
            amount=amount,
            currency=currency or "USD",
            note=str(args.get("note") or ""))
    if name == "account_summary":
        data = client.list_invoices(limit=100)
        invs = data["invoices"]
        outstanding = [i for i in invs if i["status"] in _UNPAID]
        paid = [i for i in invs if i["status"] == "PAID"]

        txns = client.search_transactions(days_back=30)
        received = [t for t in txns if t["status"] == "COMPLETED"]
        currency = "USD"
        for row in outstanding + paid + received:
            if row.get("currency"):
                currency = row["currency"]
                break
        out_by = _sum_by_currency(outstanding, "total", currency)
        paid_by = _sum_by_currency(paid, "total", currency)
        recv_by = _sum_by_currency(received, "amount", currency)
        currencies = sorted(set(out_by) | set(paid_by) | set(recv_by))
        summary: dict = {
            "outstanding_invoices": len(outstanding),
            "paid_invoices": len(paid),
            "payments_received_30d": len(received),
        }
        if len(currencies) <= 1:
            summary.update({
                "outstanding_total": out_by.get(currency, 0.0),
                "paid_total": paid_by.get(currency, 0.0),
                "payments_total_30d": recv_by.get(currency, 0.0),
                "currency": currency,
            })
        else:
            # Never add EUR to USD: report one total per currency instead.
            summary.update({
                "currency": "MIXED",
                "totals_by_currency": {
                    c: {"outstanding_total": out_by.get(c, 0.0),
                        "paid_total": paid_by.get(c, 0.0),
                        "payments_total_30d": recv_by.get(c, 0.0)}
                    for c in currencies},
            })
        return summary
    raise ToolError(f"Unknown tool: {name!r}. Available: "
                    + ", ".join(t["name"] for t in TOOL_SPECS))
