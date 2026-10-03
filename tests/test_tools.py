"""Tool dispatcher tests: validation, routing, and account summary math."""
import pytest

from paypilot import tools
from paypilot.errors import PayPalAPIError, ToolError


def test_unknown_tool_raises(client):
    with pytest.raises(ToolError):
        tools.execute_tool(client, "teleport_cash", {})


def test_invoice_create_requires_args(client):
    with pytest.raises(ToolError):
        tools.execute_tool(client, "invoice_create",
                           {"recipient_email": "a@b.co"})
    with pytest.raises(ToolError):
        tools.execute_tool(client, "invoice_create",
                           {"items": [{"name": "x", "unit_price": 1}]})


def test_invoice_create_send_status_roundtrip(client):
    created = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "bill@acme.example",
        "recipient_name": "Acme Corp",
        "items": [{"name": "Design", "quantity": 1, "unit_price": 1250}],
        "due_days": 30})
    assert created["invoice_id"].startswith("INV2-")

    sent = tools.execute_tool(client, "invoice_send",
                              {"invoice_id": created["invoice_id"]})
    assert sent["sent"] is True

    status = tools.execute_tool(client, "invoice_status",
                                {"invoice_id": created["invoice_id"]})
    assert status["status"] == "SENT"
    assert status["total"] == "1250.00"


def test_invoice_list_status_filter(client):
    a = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "a@b.co",
        "items": [{"name": "x", "unit_price": 10}]})
    tools.execute_tool(client, "invoice_send",
                       {"invoice_id": a["invoice_id"]})
    unpaid = tools.execute_tool(client, "invoice_list",
                                {"status": "UNPAID"})
    assert unpaid["count"] >= 1
    assert all(i["status"] == "SENT" for i in unpaid["invoices"])
    drafts = tools.execute_tool(client, "invoice_list",
                                {"status": "DRAFT"})
    assert all(i["status"] == "DRAFT" for i in drafts["invoices"])


def test_payment_search_and_refund_flow(client):
    found = tools.execute_tool(client, "payment_search", {"query": "john"})
    assert found["count"] == 1
    capture_id = found["payments"][0]["capture_id"]

    refunded = tools.execute_tool(client, "refund_payment",
                                  {"capture_id": capture_id,
                                   "note": "duplicate charge"})
    assert refunded["status"] == "COMPLETED"
    assert refunded["amount"] == "250.00"


def test_refund_propagates_paypal_errors(client):
    with pytest.raises(PayPalAPIError):
        tools.execute_tool(client, "refund_payment",
                           {"capture_id": "CAP-DOES-NOT-EXIST"})


def test_account_summary_math(client, transport):
    created = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "bill@acme.example",
        "items": [{"name": "Work", "quantity": 1, "unit_price": 100}]})
    tools.execute_tool(client, "invoice_send",
                       {"invoice_id": created["invoice_id"]})
    transport.mock_mark_invoice_paid(created["invoice_id"])

    created2 = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "late@example.com",
        "items": [{"name": "Work", "quantity": 1, "unit_price": 50}]})
    tools.execute_tool(client, "invoice_send",
                       {"invoice_id": created2["invoice_id"]})

    summary = tools.execute_tool(client, "account_summary", {})
    assert summary["outstanding_invoices"] == 1
    assert summary["outstanding_total"] == 50.0
    assert summary["paid_invoices"] == 1
    assert summary["paid_total"] == 100.0
    # seeded mock transactions: 250 + 89 + the 100 just marked paid
    assert summary["payments_received_30d"] == 3
    assert summary["payments_total_30d"] == 439.0
    assert summary["currency"] == "USD"


def test_tool_specs_cover_all_tools():
    spec_names = {t["name"] for t in tools.TOOL_SPECS}
    assert spec_names == {"invoice_create", "invoice_send",
                          "invoice_remind", "invoice_list",
                          "invoice_status", "payment_search",
                          "refund_payment", "account_summary"}
    for spec in tools.TOOL_SPECS:
        assert spec["description"], spec["name"]
        assert spec["parameters"]["type"] == "object"
