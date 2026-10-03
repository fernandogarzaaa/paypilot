"""Transport tests: mock behavior and real-transport error mapping."""
import urllib.error

import pytest

from paypilot.errors import PayPalAPIError
from paypilot.transport import MockPayPalTransport, Request, UrllibTransport


def test_mock_oauth_issues_token():
    t = MockPayPalTransport()
    resp = t.request(Request("POST", "/v1/oauth2/token",
                             headers={"Authorization": "Basic dGVzdC1pZDp0ZXN0LXNlY3JldA=="},
                             json_body={"grant_type": "client_credentials"}))
    assert resp.status == 200
    assert resp.body["access_token"].startswith("mock-access-token-")
    assert resp.body["token_type"] == "Bearer"


def test_mock_oauth_rejects_missing_basic_auth():
    t = MockPayPalTransport()
    with pytest.raises(PayPalAPIError) as e:
        t.request(Request("POST", "/v1/oauth2/token", json_body={}))
    assert e.value.status == 401


def test_mock_records_requests():
    t = MockPayPalTransport()
    t.request(Request("POST", "/v1/oauth2/token",
                      headers={"Authorization": "Basic dGVzdC1pZDp0ZXN0LXNlY3JldA=="},
                      json_body={"grant_type": "client_credentials"}))
    assert len(t.requests) == 1
    assert t.requests[0].method == "POST"
    assert t.requests[0].path == "/v1/oauth2/token"


def test_mock_unknown_endpoint_404s():
    t = MockPayPalTransport()
    with pytest.raises(PayPalAPIError) as e:
        t.request(Request("GET", "/v2/nope"))
    assert e.value.status == 404


def test_mock_wrong_state_transition_422s():
    t = MockPayPalTransport()
    r = t.request(Request(
        "POST", "/v2/invoicing/invoices",
        json_body={
            "detail": {"currency_code": "USD"},
            "primary_recipients": [{"billing_info": {
                "email_address": "a@b.co",
                "name": {"given_name": "A", "surname": "B"}}}],
            "items": [{"name": "X", "quantity": "1",
                       "unit_amount": {"currency_code": "USD",
                                       "value": "10.00"}}]}))
    inv_id = r.body["id"]
    # remind on a DRAFT invoice is illegal
    with pytest.raises(PayPalAPIError) as e:
        t.request(Request("POST",
                          f"/v2/invoicing/invoices/{inv_id}/remind",
                          json_body={}))
    assert e.value.status == 422
    # send twice is illegal
    t.request(Request("POST", f"/v2/invoicing/invoices/{inv_id}/send",
                      json_body={}))
    with pytest.raises(PayPalAPIError) as e2:
        t.request(Request("POST", f"/v2/invoicing/invoices/{inv_id}/send",
                          json_body={}))
    assert e2.value.status == 422


def test_urllib_transport_maps_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            __import__("io").BytesIO(
                b'{"name":"INVALID_REQUEST","message":"bad","details":'
                b'[{"issue":"x"}]}'))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    t = UrllibTransport("https://api-m.sandbox.paypal.com")
    with pytest.raises(PayPalAPIError) as e:
        t.request(Request("GET", "/v2/nope"))
    assert e.value.status == 400
    assert e.value.error_name == "INVALID_REQUEST"
    assert "bad" in e.value.detail


def test_urllib_transport_maps_connection_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    t = UrllibTransport("https://api-m.sandbox.paypal.com")
    with pytest.raises(PayPalAPIError) as e:
        t.request(Request("GET", "/v2/nope"))
    assert e.value.status == 0
