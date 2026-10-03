"""PayPalClient tests: full invoice lifecycle, payments, refunds, auth."""
import pytest

from paypilot.errors import ConfigurationError, PayPalAPIError
from paypilot.paypal_client import PayPalClient
from paypilot.transport import MockPayPalTransport


def _invoice(client, **kw):
    args = dict(to_email="bill@acme.example", to_name="Acme Corp",
                items=[{"name": "Design work", "quantity": 2,
                        "unit_price": 150}])
    args.update(kw)
    return client.create_invoice(**args)


def test_client_requires_credentials():
    with pytest.raises(ConfigurationError):
        PayPalClient("", "secret")


def test_client_rejects_bad_mode():
    with pytest.raises(ConfigurationError):
        PayPalClient("id", "secret", mode="narnia")


def test_oauth_token_is_cached(client, transport):
    _invoice(client)
    _invoice(client)
    token_posts = [r for r in transport.requests
                   if r.path == "/v1/oauth2/token"]
    assert len(token_posts) == 1, "token should be fetched once and cached"


def test_invoice_lifecycle_create_send_status(client):
    created = _invoice(client)
    assert created["invoice_id"].startswith("INV2-")
    assert created["status"] == "DRAFT"

    sent = client.send_invoice(created["invoice_id"])
    assert sent["sent"] is True

    got = client.get_invoice(created["invoice_id"])
    assert got["status"] == "SENT"
    assert got["total"] == "300.00"
    assert got["currency"] == "USD"
    assert got["recipient_email"] == "bill@acme.example"


def test_invoice_create_validates_inputs(client):
    with pytest.raises(ConfigurationError):
        client.create_invoice(to_email="not-an-email", to_name="X",
                              items=[{"name": "Y", "unit_price": 1}])
    with pytest.raises(ConfigurationError):
        client.create_invoice(to_email="a@b.co", to_name="X", items=[])


def test_invoice_create_sends_correct_paypal_payload(client, transport):
    _invoice(client, due_days=15, note="thanks",
             items=[{"name": "Widget", "quantity": 3, "unit_price": 9.99}])
    create_reqs = [r for r in transport.requests
                   if r.path == "/v2/invoicing/invoices"
                   and r.method == "POST"]
    assert len(create_reqs) == 1
    body = create_reqs[0].json_body
    assert body["detail"]["currency_code"] == "USD"
    assert body["detail"]["payment_term"]["term_type"] == "NET_15"
    assert body["detail"]["note"] == "thanks"
    item = body["items"][0]
    assert item["name"] == "Widget"
    assert item["quantity"] == "3"
    assert item["unit_amount"] == {"currency_code": "USD", "value": "9.99"}


def test_invoice_due_days_zero_maps_to_due_on_receipt(client, transport):
    _invoice(client, due_days=0)
    body = [r for r in transport.requests
            if r.method == "POST"
            and r.path == "/v2/invoicing/invoices"][0].json_body
    assert body["detail"]["payment_term"]["term_type"] == "DUE_ON_RECEIPT"


def test_invoice_list_and_filter(client):
    a = _invoice(client)
    b = _invoice(client, to_email="other@example.com")
    client.send_invoice(a["invoice_id"])
    data = client.list_invoices()
    assert data["total"] == 2
    assert {i["invoice_id"] for i in data["invoices"]} == {a["invoice_id"],
                                                          b["invoice_id"]}


def test_invoice_remind_requires_sent(client):
    created = _invoice(client)
    with pytest.raises(PayPalAPIError):
        client.remind_invoice(created["invoice_id"])
    client.send_invoice(created["invoice_id"])
    out = client.remind_invoice(created["invoice_id"], note="nudge")
    assert out["reminded"] is True


def test_invoice_cancel_and_unknown_id(client):
    created = _invoice(client)
    out = client.cancel_invoice(created["invoice_id"])
    assert out["status"] == "CANCELLED"
    with pytest.raises(PayPalAPIError) as e:
        client.get_invoice("INV2-DOES-NOT-EXIST")
    assert e.value.status == 404


def test_search_transactions_maps_paypal_shape(client):
    txns = client.search_transactions()
    assert len(txns) == 2
    john = next(t for t in txns if t["email"] == "john@example.com")
    assert john["name"] == "John Carter"
    assert john["amount"] == "250.00"
    assert john["status"] == "COMPLETED"
    assert john["capture_id"] == "CAP-9RU12345AB"


def test_search_transactions_filters_by_query(client):
    txns = client.search_transactions(query="acme")
    assert len(txns) == 1
    assert txns[0]["name"] == "Acme Corp"
    assert client.search_transactions(query="nobody-here") == []


def test_search_transactions_sends_date_range(client, transport):
    client.search_transactions(days_back=7)
    reqs = [r for r in transport.requests
            if r.path == "/v1/reporting/transactions"]
    assert len(reqs) == 1
    assert "start_date" in reqs[0].params
    assert "end_date" in reqs[0].params
    assert reqs[0].params["page_size"] == "100"


def test_refund_full_and_partial(client):
    full = client.refund_capture("CAP-9RU12345AB")
    assert full["status"] == "COMPLETED"
    assert full["refund_id"].startswith("REF-")
    assert full["amount"] == "250.00"

    partial = client.refund_capture("CAP-7XY98765ZZ", amount="10.00",
                                    note="goodwill")
    assert partial["amount"] == "10.00"


def test_refund_unknown_capture_404s(client):
    with pytest.raises(PayPalAPIError) as e:
        client.refund_capture("CAP-NOPE")
    assert e.value.status == 404


def test_paid_invoice_appears_in_summary_flow(client, transport):
    created = _invoice(client)
    client.send_invoice(created["invoice_id"])
    transport.mock_mark_invoice_paid(created["invoice_id"])
    got = client.get_invoice(created["invoice_id"])
    assert got["status"] == "PAID"
