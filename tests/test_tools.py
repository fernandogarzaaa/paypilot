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


def test_garbage_money_raises_paypilot_error(client):
    """Bad numeric input must surface as a PayPilotError (so the MCP
    server and web demo return their documented {"error": ...} envelope),
    not as a raw ValueError."""
    from paypilot.errors import PayPilotError

    with pytest.raises(PayPilotError):
        tools.execute_tool(client, "invoice_create", {
            "recipient_email": "a@b.co",
            "items": [{"name": "x", "quantity": 1,
                       "unit_price": "not-a-number"}]})
    with pytest.raises(PayPilotError):
        tools.execute_tool(client, "invoice_create", {
            "recipient_email": "a@b.co",
            "items": [{"name": "x", "quantity": "many", "unit_price": 5}]})
    with pytest.raises(PayPilotError):
        tools.execute_tool(client, "refund_payment",
                           {"capture_id": "CAP-9RU12345AB",
                            "amount": "not-a-number"})
    with pytest.raises(PayPilotError):
        tools.execute_tool(client, "refund_payment",
                           {"capture_id": "CAP-9RU12345AB",
                            "amount": "-5"})


def test_invoice_status_reports_due_date(client):
    """The mock must mirror the live API: NET_30 terms produce a real
    due date on invoice_status."""
    import datetime

    created = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "due@example.com",
        "items": [{"name": "Work", "quantity": 1, "unit_price": 100}],
        "due_days": 30})
    status = tools.execute_tool(client, "invoice_status",
                                {"invoice_id": created["invoice_id"]})
    expected = (datetime.date.today()
                + datetime.timedelta(days=30)).isoformat()
    assert status["due_date"] == expected


@pytest.mark.parametrize("tool,args", [
    ("payment_search", {"days_back": "thirty"}),
    ("payment_search", {"days_back": 0}),
    ("payment_search", {"days_back": 2.5}),
    ("invoice_list", {"limit": "lots"}),
    ("invoice_list", {"limit": 10_000}),
    ("invoice_create", {"recipient_email": "a@b.co", "due_days": "soon",
                        "items": [{"name": "x", "quantity": 1,
                                   "unit_price": 1}]}),
])
def test_bad_integer_args_raise_tool_error(client, tool, args):
    # A raw ValueError used to escape the agent loop's PayPilotError
    # handling and fail the whole chat turn with HTTP 500.
    with pytest.raises(ToolError):
        tools.execute_tool(client, tool, args)


def test_integer_args_accept_numeric_strings(client):
    out = tools.execute_tool(client, "payment_search", {"days_back": "7"})
    assert "payments" in out


def test_partial_refund_uses_capture_currency(client, transport):
    created = tools.execute_tool(client, "invoice_create", {
        "recipient_email": "eu@example.com", "currency": "EUR",
        "items": [{"name": "Work", "quantity": 1, "unit_price": 80}]})
    tools.execute_tool(client, "invoice_send",
                       {"invoice_id": created["invoice_id"]})
    transport.mock_mark_invoice_paid(created["invoice_id"])
    pay = [p for p in tools.execute_tool(
        client, "payment_search", {"query": "eu@example.com"})["payments"]]
    assert pay and pay[0]["currency"] == "EUR"
    refund = tools.execute_tool(client, "refund_payment", {
        "capture_id": pay[0]["capture_id"], "amount": "10"})
    assert refund["currency"] == "EUR"
    sent = [r for r in transport.requests if r.path.endswith("/refund")][-1]
    assert sent.json_body["amount"]["currency_code"] == "EUR"


def test_explicit_refund_currency_wins(client, transport):
    pay = tools.execute_tool(client, "payment_search", {})["payments"][0]
    refund = tools.execute_tool(client, "refund_payment", {
        "capture_id": pay["capture_id"], "amount": "1", "currency": "usd"})
    assert refund["currency"] == "USD"


def test_account_summary_never_adds_currencies(client, transport):
    for cur, price in (("EUR", 80), ("USD", 20)):
        created = tools.execute_tool(client, "invoice_create", {
            "recipient_email": f"{cur.lower()}@example.com",
            "currency": cur,
            "items": [{"name": "Work", "quantity": 1, "unit_price": price}]})
        tools.execute_tool(client, "invoice_send",
                           {"invoice_id": created["invoice_id"]})
    summary = tools.execute_tool(client, "account_summary", {})
    assert summary["currency"] == "MIXED"
    assert "outstanding_total" not in summary
    by = summary["totals_by_currency"]
    assert by["EUR"]["outstanding_total"] == 80.0
    assert by["USD"]["outstanding_total"] == 20.0


def test_account_summary_is_exact_to_the_cent(client, transport):
    for _ in range(3):
        created = tools.execute_tool(client, "invoice_create", {
            "recipient_email": "c@example.com",
            "items": [{"name": "W", "quantity": 1, "unit_price": "0.10"}]})
        tools.execute_tool(client, "invoice_send",
                           {"invoice_id": created["invoice_id"]})
    summary = tools.execute_tool(client, "account_summary", {})
    assert summary["outstanding_total"] == 0.3
