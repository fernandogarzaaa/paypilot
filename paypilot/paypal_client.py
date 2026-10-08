"""PayPal REST API client.

Covers the endpoints PayPilot needs: OAuth2 client-credentials, Invoices
v2 (create/get/list/send/remind/cancel), Transaction Search (payment
lookup), and Payments v2 refunds.

The client never touches the network directly; every request goes through
the injected transport (real UrllibTransport or MockPayPalTransport).
Credentials come from the constructor or from_env(); they are never read
from anywhere else and never logged.
"""
from __future__ import annotations

import base64
import datetime
import os
import time
import urllib.parse
from typing import Any, Optional

from .errors import ConfigurationError, PayPalAPIError
from .transport import MockPayPalTransport, Request, Transport, UrllibTransport

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"

# PayPal payment_term.term_type values; anything else becomes an explicit date.
_TERM_TYPES = {0: "DUE_ON_RECEIPT", 10: "NET_10", 15: "NET_15", 30: "NET_30",
               45: "NET_45", 60: "NET_60", 90: "NET_90"}

# Transaction Search status codes -> readable status.
_TXN_STATUS = {"S": "COMPLETED", "P": "PENDING", "V": "REVERSED",
               "D": "DENIED", "R": "REFUNDED", "H": "ON_HOLD"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class PayPalClient:
    """Thin, honest wrapper over the PayPal REST API."""

    def __init__(self, client_id: str, client_secret: str,
                 mode: str = "sandbox",
                 transport: Optional[Transport] = None):
        if not client_id or not client_secret:
            raise ConfigurationError(
                "PayPal client_id and client_secret are required. Set "
                "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (see README for "
                "sandbox setup), or pass a MockPayPalTransport for tests.")
        if mode not in ("sandbox", "live"):
            raise ConfigurationError(
                f"PAYPAL_MODE must be 'sandbox' or 'live', got {mode!r}.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.mode = mode
        base = SANDBOX_BASE if mode == "sandbox" else LIVE_BASE
        self.transport = transport or UrllibTransport(base)
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls, transport: Optional[Transport] = None) -> "PayPalClient":
        """Build from PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_MODE.

        When PAYPILOT_TRANSPORT=mock (or "1"/"true"), a MockPayPalTransport
        is used and placeholder credentials are accepted so the demo and
        tests run without secrets.
        """
        use_mock = os.environ.get("PAYPILOT_TRANSPORT",
                                  "").strip().lower() in ("mock", "1", "true")
        client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
        client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
        if use_mock:
            client_id = client_id or "mock-client-id"
            client_secret = client_secret or "mock-client-secret"
            transport = transport or MockPayPalTransport()
        if not client_id or not client_secret:
            raise ConfigurationError(
                "Missing PayPal credentials. Set PAYPAL_CLIENT_ID and "
                "PAYPAL_CLIENT_SECRET (sandbox credentials from "
                "https://developer.paypal.com/dashboard/applications/sandbox), "
                "or set PAYPILOT_TRANSPORT=mock to run against the local mock.")
        mode = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
        return cls(client_id, client_secret, mode=mode, transport=transport)

    @property
    def backend(self) -> str:
        """Human-readable backend description (safe to display)."""
        kind = "mock" if isinstance(self.transport,
                                    MockPayPalTransport) else "paypal-" + self.mode
        return kind

    # -- auth --------------------------------------------------------
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self.transport.request(Request(
            "POST", "/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            # PayPal requires application/x-www-form-urlencoded here; a JSON
            # body is rejected with HTTP 400.
            form_body={"grant_type": "client_credentials"}))
        body = resp.body or {}
        self._token = body.get("access_token", "")
        expires_in = int(body.get("expires_in", 300))
        self._token_expires_at = time.time() + max(expires_in - 60, 60)
        if not self._token:
            raise PayPalAPIError(resp.status, "AUTH_FAILED",
                                 "token endpoint returned no access_token")
        return self._token

    def _call(self, method: str, path: str,
              params: Optional[dict] = None,
              json_body: Optional[dict] = None) -> Any:
        token = self._access_token()
        resp = self.transport.request(Request(
            method, path, params=params or {},
            json_body=json_body, headers=_bearer(token)))
        return resp.body

    # -- invoices ----------------------------------------------------
    def create_invoice(self, to_email: str, to_name: str,
                       items: list[dict], currency: str = "USD",
                       due_days: int = 30, note: str = "") -> dict:
        """Create a DRAFT invoice. items: [{name, quantity, unit_price}]."""
        if not to_email or "@" not in to_email:
            raise ConfigurationError(
                f"invoice_create needs a valid recipient email, got {to_email!r}.")
        if not items:
            raise ConfigurationError("invoice_create needs at least one item.")
        given, _, surname = to_name.partition(" ")
        payload_items = []
        for it in items:
            qty = it.get("quantity", 1)
            price = it.get("unit_price")
            if price is None:
                raise ConfigurationError(
                    f"item {it.get('name', '?')!r} needs unit_price.")
            payload_items.append({
                "name": str(it.get("name", "Item")),
                "quantity": str(qty),
                "unit_amount": {"currency_code": currency,
                                "value": f"{float(price):.2f}"},
            })
        detail: dict[str, Any] = {"currency_code": currency}
        if note:
            detail["note"] = note
        if due_days in _TERM_TYPES:
            detail["payment_term"] = {"term_type": _TERM_TYPES[due_days]}
        else:
            due = (datetime.date.today()
                   + datetime.timedelta(days=max(due_days, 0))).isoformat()
            detail["payment_term"] = {"term_type": "DUE_ON_DATE_SPECIFIED",
                                      "due_date": due}
        body = self._call("POST", "/v2/invoicing/invoices", json_body={
            "detail": detail,
            "primary_recipients": [{"billing_info": {
                "email_address": to_email,
                "name": {"given_name": given or to_name,
                         "surname": surname}}}] ,
            "items": payload_items,
        })
        if "id" not in body and body.get("href"):
            # PayPal may answer 201 with only a self link
            # ({"rel": "self", "href": ".../invoices/<id>", "method": "GET"});
            # follow it to fetch the created invoice.
            href = body["href"]
            path = urllib.parse.urlparse(href).path or href
            body = self._call("GET", path)
        return {"invoice_id": body.get("id", ""),
                "status": body.get("status", ""),
                "currency": currency}

    def get_invoice(self, invoice_id: str) -> dict:
        body = self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")
        return self._simplify_invoice(body)

    def list_invoices(self, limit: int = 20) -> dict:
        body = self._call("GET", "/v2/invoicing/invoices",
                          params={"page_size": max(1, min(limit, 100)),
                                  "total_required": "true"})
        items = [self._simplify_invoice(i)
                 for i in (body.get("items") or [])]
        return {"invoices": items,
                "total": body.get("total_items", len(items))}

    @staticmethod
    def _simplify_invoice(body: dict) -> dict:
        recipients = body.get("primary_recipients") or []
        billing = (recipients[0].get("billing_info")
                   if recipients else {}) or {}
        amount = body.get("amount") or {}
        # PayPal returns the total as amount.value; older shapes nested it
        # under amount.summary.total. Accept both.
        total = amount.get("value") or (amount.get("summary") or {}).get("total", "")
        return {
            "invoice_id": body.get("id", ""),
            "status": body.get("status", ""),
            "currency": (body.get("detail") or {}).get("currency_code", ""),
            "total": total,
            "recipient_email": billing.get("email_address", ""),
            "due_date": body.get("due_date", ""),
        }

    def send_invoice(self, invoice_id: str) -> dict:
        self._call("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                   json_body={"send_to_recipient": True})
        return {"invoice_id": invoice_id, "sent": True}

    def remind_invoice(self, invoice_id: str, note: str = "") -> dict:
        payload: dict[str, Any] = {"send_to_recipient": True}
        if note:
            payload["note"] = note
        self._call("POST",
                   f"/v2/invoicing/invoices/{invoice_id}/remind",
                   json_body=payload)
        return {"invoice_id": invoice_id, "reminded": True}

    def cancel_invoice(self, invoice_id: str) -> dict:
        # PayPal's sandbox answers 415 to a bodiless POST here; an empty
        # JSON object succeeds (204).
        body = self._call("POST",
                          f"/v2/invoicing/invoices/{invoice_id}/cancel",
                          json_body={})
        return {"invoice_id": invoice_id,
                "status": (body or {}).get("status", "CANCELLED")}

    # -- payments ----------------------------------------------------
    def search_transactions(self, days_back: int = 30,
                            query: str = "") -> list[dict]:
        """Recent transactions, optionally filtered by email/name substring."""
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(days=max(days_back, 1))
        fmt = "%Y-%m-%dT%H:%M:%S%z"
        body = self._call("GET", "/v1/reporting/transactions", params={
            "start_date": start.strftime(fmt),
            "end_date": end.strftime(fmt),
            "fields": "all",
            "page_size": "100",
        })
        out = []
        for d in (body.get("transaction_details") or []):
            info = d.get("transaction_info", {})
            payer = d.get("payer_info", {})
            pname = (payer.get("payer_name") or {}).get(
                "alternate_full_name", "")
            email = payer.get("email_address", "")
            if query and query.lower() not in (
                    email + " " + pname).lower():
                continue
            amt = info.get("transaction_amount", {})
            out.append({
                "transaction_id": info.get("transaction_id", ""),
                "capture_id": d.get("mock_capture_id")
                or info.get("transaction_id", ""),
                "status": _TXN_STATUS.get(info.get("transaction_status",
                                                   ""), "UNKNOWN"),
                "amount": amt.get("value", ""),
                "currency": amt.get("currency_code", ""),
                "email": email,
                "name": pname,
                "date": info.get("transaction_initiation_date", ""),
                "note": info.get("transaction_note", ""),
            })
        return out

    def refund_capture(self, capture_id: str, amount: Optional[str] = None,
                       currency: str = "USD", note: str = "") -> dict:
        """Refund a capture in full (amount omitted) or partially."""
        payload: dict[str, Any] = {}
        if amount is not None:
            payload["amount"] = {"currency_code": currency,
                                 "value": f"{float(amount):.2f}"}
        if note:
            payload["note_to_payer"] = note
        body = self._call(
            "POST", f"/v2/payments/captures/{capture_id}/refund",
            json_body=payload)
        amt = (body or {}).get("amount", {})
        return {"refund_id": (body or {}).get("id", ""),
                "status": (body or {}).get("status", ""),
                "amount": amt.get("value", ""),
                "currency": amt.get("currency_code", currency)}
