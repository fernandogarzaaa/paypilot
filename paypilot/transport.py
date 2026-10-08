"""HTTP transport abstraction for the PayPal REST API.

The PayPalClient never touches the network directly: every request goes
through a Transport. Two implementations ship:

- UrllibTransport: real HTTPS against api-m.sandbox.paypal.com (or live).
- MockPayPalTransport: an in-memory simulation of the PayPal endpoints
  PayPilot uses (OAuth, Invoices v2, Transaction Search, Refunds). The
  full test suite runs against the mock, so CI needs no credentials.

The mock records every request it receives, which lets tests assert on
the exact endpoint, method, and payload the client produced.
"""
from __future__ import annotations

import base64
import json
import random
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import PayPalAPIError


@dataclass
class Request:
    method: str
    path: str  # e.g. "/v2/invoicing/invoices/INV2-123"
    params: dict = field(default_factory=dict)
    json_body: Optional[dict] = None
    form_body: Optional[dict] = None  # application/x-www-form-urlencoded
    headers: dict = field(default_factory=dict)


@dataclass
class Response:
    status: int
    body: Any  # decoded JSON (dict/list) or "" for empty bodies


class Transport:
    """Abstract PayPal API transport."""

    def request(self, req: Request) -> Response:
        raise NotImplementedError


class UrllibTransport(Transport):
    """Real HTTPS transport using only the standard library."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, req: Request) -> Response:
        url = self.base_url + req.path
        if req.params:
            url += "?" + urllib.parse.urlencode(req.params)
        data = None
        headers = {"Accept": "application/json"}
        headers.update(req.headers)
        if req.form_body is not None:
            data = urllib.parse.urlencode(req.form_body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif req.json_body is not None:
            data = json.dumps(req.json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        http_req = urllib.request.Request(url, data=data, headers=headers,
                                          method=req.method.upper())
        try:
            with urllib.request.urlopen(http_req,
                                        timeout=self.timeout) as resp:
                raw = resp.read()
                body = json.loads(raw.decode("utf-8")) if raw else ""
                return Response(status=resp.status, body=body)
        except urllib.error.HTTPError as e:
            detail, name = "", ""
            try:
                payload = json.loads(e.read().decode("utf-8"))
                name = payload.get("name", "")
                detail = payload.get("message", "")
                dets = payload.get("details")
                if dets:
                    detail += " | " + "; ".join(
                        str(d.get("issue", d)) for d in dets[:3])
            except Exception:
                detail = "no parseable error body"
            raise PayPalAPIError(e.code, name, detail) from None
        except OSError as e:
            raise PayPalAPIError(0, "TRANSPORT_ERROR", str(e)) from None


def _rand_invoice_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    parts = ["".join(random.choices(alphabet, k=4)) for _ in range(3)]
    return "INV2-" + "-".join(parts)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MockPayPalTransport(Transport):
    """In-memory simulation of the PayPal endpoints PayPilot uses.

    Faithful where it matters for PayPilot: OAuth token issuance,
    invoice lifecycle (DRAFT -> SENT -> PAID/CANCELLED), transaction
    search results, and capture refunds. Request validation mirrors the
    real API's failure modes (unknown ids -> 404, wrong-state
    transitions -> 422) so client error handling is genuinely tested.
    """

    def __init__(self):
        self.requests: list[Request] = []
        self.invoices: dict[str, dict] = {}
        self.transactions: list[dict] = []
        self.refunds: dict[str, dict] = {}
        self._token_n = 0
        self._seed_transactions()

    # -- test helpers -------------------------------------------------
    def _seed_transactions(self):
        self.transactions = [
            {
                "transaction_id": "9RU12345AB678901C",
                "capture_id": "CAP-9RU12345AB",
                "status": "S",
                "amount": "250.00",
                "currency": "USD",
                "email": "john@example.com",
                "name": "John Carter",
                "date": "2026-09-28T14:02:11Z",
                "note": "Consulting retainer",
            },
            {
                "transaction_id": "7XY98765ZZ123456D",
                "capture_id": "CAP-7XY98765ZZ",
                "status": "S",
                "amount": "89.00",
                "currency": "USD",
                "email": "ops@acmecorp.example",
                "name": "Acme Corp",
                "date": "2026-09-15T09:30:00Z",
                "note": "Monthly plan",
            },
        ]

    def mock_mark_invoice_paid(self, invoice_id: str):
        """Simulate the payer paying an invoice (test helper)."""
        inv = self.invoices.get(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        inv["status"] = "PAID"
        self.transactions.append({
            "transaction_id": "PAY-" + invoice_id.replace("-", "")[:12],
            "capture_id": "CAP-" + invoice_id.replace("-", "")[:12],
            "status": "S",
            "amount": inv["total"],
            "currency": inv["currency"],
            "email": inv["recipient_email"],
            "name": inv["recipient_name"],
            "date": _now_iso(),
            "note": f"Invoice {invoice_id}",
        })

    # -- transport ----------------------------------------------------
    def request(self, req: Request) -> Response:
        self.requests.append(req)
        m, p = req.method.upper(), req.path
        if m == "POST" and p == "/v1/oauth2/token":
            return self._oauth(req)
        if p == "/v2/invoicing/invoices" and m == "POST":
            return self._invoice_create(req)
        if p == "/v2/invoicing/invoices" and m == "GET":
            return self._invoice_list(req)
        if p.startswith("/v2/invoicing/invoices/"):
            return self._invoice_subresource(req)
        if m == "GET" and p == "/v1/reporting/transactions":
            return self._transaction_search(req)
        if m == "POST" and p.startswith("/v2/payments/captures/") \
                and p.endswith("/refund"):
            return self._refund(req)
        raise PayPalAPIError(404, "NOT_FOUND",
                             f"mock has no handler for {m} {p}")

    def _oauth(self, req: Request) -> Response:
        auth = req.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            raise PayPalAPIError(401, "INVALID_CLIENT",
                                 "mock: missing Basic auth on token request")
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
        except Exception:
            raise PayPalAPIError(401, "INVALID_CLIENT",
                                 "mock: malformed Basic auth")
        if ":" not in decoded or decoded.startswith(":") \
                or decoded.endswith(":"):
            raise PayPalAPIError(401, "INVALID_CLIENT",
                                 "mock: empty client id or secret")
        # PayPal's real token endpoint requires a form-encoded body; a JSON
        # body gets HTTP 400 (this exact bug shipped once and the live
        # sandbox caught it). The mock enforces the same contract.
        grant = (req.form_body or {}).get("grant_type")
        if grant != "client_credentials":
            raise PayPalAPIError(400, "INVALID_REQUEST",
                                 "mock: token request must be form-encoded "
                                 "with grant_type=client_credentials")
        self._token_n += 1
        return Response(200, {
            "access_token": f"mock-access-token-{self._token_n}",
            "token_type": "Bearer",
            "expires_in": 32400,
        })

    def _invoice_create(self, req: Request) -> Response:
        body = req.json_body or {}
        recipients = body.get("primary_recipients", [])
        if not recipients or not recipients[0].get("billing_info", {}).get(
                "email_address"):
            raise PayPalAPIError(422, "INVALID_REQUEST",
                                 "mock: recipient email_address is required")
        items = body.get("items", [])
        if not items:
            raise PayPalAPIError(422, "INVALID_REQUEST",
                                 "mock: at least one item is required")
        currency = body.get("detail", {}).get("currency_code", "USD")
        total = sum(float(i.get("quantity", 1))
                    * float(i["unit_amount"]["value"]) for i in items)
        inv_id = _rand_invoice_id()
        billing = recipients[0]["billing_info"]
        name = billing.get("name", {}) or {}
        full_name = " ".join(x for x in
                             (name.get("given_name", ""),
                              name.get("surname", "")) if x).strip()
        inv = {
            "id": inv_id,
            "status": "DRAFT",
            "currency": currency,
            "total": f"{total:.2f}",
            "recipient_email": billing.get("email_address", ""),
            "recipient_name": full_name,
            "note": body.get("detail", {}).get("note", ""),
            "created": _now_iso(),
            "raw": body,
        }
        self.invoices[inv_id] = inv
        return Response(201, {
            "id": inv_id,
            "status": "DRAFT",
            "detail": {"currency_code": currency},
            "links": [{"rel": "self",
                       "href": f"/v2/invoicing/invoices/{inv_id}"}],
        })

    def _invoice_list(self, req: Request) -> Response:
        items = [{
            "id": i["id"],
            "status": i["status"],
            "detail": {"currency_code": i["currency"]},
            "amount": {"summary": {"total": i["total"]}},
            "primary_recipients": [{"billing_info": {
                "email_address": i["recipient_email"]}}],
        } for i in self.invoices.values()]
        return Response(200, {"items": items,
                              "total_items": len(items),
                              "total_pages": 1})

    def _invoice_subresource(self, req: Request) -> Response:
        rest = req.path[len("/v2/invoicing/invoices/"):]
        inv_id, _, action = rest.partition("/")
        inv = self.invoices.get(inv_id)
        if inv is None:
            raise PayPalAPIError(404, "RESOURCE_NOT_FOUND",
                                 f"mock: no invoice {inv_id}")
        m = req.method.upper()
        if m == "GET" and not action:
            return Response(200, self._invoice_detail(inv))
        if m == "DELETE" and not action:
            if inv["status"] != "DRAFT":
                raise PayPalAPIError(422, "INVALID_REQUEST",
                                     "mock: only DRAFT invoices can be deleted")
            del self.invoices[inv_id]
            return Response(204, "")
        if m == "POST" and action == "send":
            if inv["status"] != "DRAFT":
                raise PayPalAPIError(422, "INVALID_REQUEST",
                                     "mock: only DRAFT invoices can be sent")
            inv["status"] = "SENT"
            inv["sent_at"] = _now_iso()
            return Response(202, "")
        if m == "POST" and action == "remind":
            if inv["status"] != "SENT":
                raise PayPalAPIError(422, "INVALID_REQUEST",
                                     "mock: only SENT invoices can be reminded")
            inv["reminded_at"] = _now_iso()
            return Response(202, "")
        if m == "POST" and action == "cancel":
            if inv["status"] not in ("DRAFT", "SENT"):
                raise PayPalAPIError(422, "INVALID_REQUEST",
                                     "mock: only DRAFT/SENT invoices can be "
                                     "cancelled")
            inv["status"] = "CANCELLED"
            return Response(200, {"id": inv_id, "status": "CANCELLED"})
        raise PayPalAPIError(404, "NOT_FOUND",
                             f"mock: no handler for {m} {req.path}")

    def _invoice_detail(self, inv: dict) -> dict:
        return {
            "id": inv["id"],
            "status": inv["status"],
            "detail": {"currency_code": inv["currency"],
                       "note": inv["note"]},
            "amount": {"summary": {"total": inv["total"]}},
            "primary_recipients": [{"billing_info": {
                "email_address": inv["recipient_email"],
                "name": {"given_name": inv["recipient_name"]}}}],
            "due_date": inv.get("due_date", ""),
        }

    def _transaction_search(self, req: Request) -> Response:
        details = []
        for t in self.transactions:
            details.append({
                "transaction_info": {
                    "transaction_id": t["transaction_id"],
                    "transaction_event_code": "T0006",
                    "transaction_initiation_date": t["date"].replace(
                        "Z", "+0000"),
                    "transaction_status": t["status"],
                    "transaction_amount": {
                        "currency_code": t["currency"],
                        "value": t["amount"]},
                    "transaction_note": t["note"],
                },
                "payer_info": {
                    "email_address": t["email"],
                    "payer_name": {
                        "alternate_full_name": t["name"],
                        "given_name": t["name"].split(" ")[0],
                        "surname": " ".join(t["name"].split(" ")[1:]),
                    },
                },
                "mock_capture_id": t["capture_id"],
            })
        return Response(200, {
            "transaction_details": details,
            "total_items": len(details),
            "total_pages": 1,
            "page": 1,
        })

    def _refund(self, req: Request) -> Response:
        capture_id = req.path[len("/v2/payments/captures/"):-len("/refund")]
        txn = next((t for t in self.transactions
                    if t["capture_id"] == capture_id), None)
        if txn is None:
            raise PayPalAPIError(404, "RESOURCE_NOT_FOUND",
                                 f"mock: no capture {capture_id}")
        body = req.json_body or {}
        amount = (body.get("amount", {}) or {}).get("value") or txn["amount"]
        currency = (body.get("amount", {}) or {}).get("currency_code") \
            or txn["currency"]
        ref_id = "REF-" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=10))
        self.refunds[ref_id] = {"capture_id": capture_id, "amount": amount,
                                "currency": currency, "status": "COMPLETED"}
        return Response(201, {
            "id": ref_id,
            "status": "COMPLETED",
            "amount": {"currency_code": currency, "value": f"{float(amount):.2f}"},
            "note_to_payer": (body.get("note_to_payer") or ""),
        })
