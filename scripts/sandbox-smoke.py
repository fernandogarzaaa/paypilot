#!/usr/bin/env python3
"""PayPilot sandbox smoke test: full invoice lifecycle against the REAL
PayPal sandbox (api-m.sandbox.paypal.com).

Requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in the environment
(sandbox app credentials from
https://developer.paypal.com/dashboard/applications/sandbox).
Without them the script exits 2 with setup instructions; nothing is
fabricated.

Covers live: OAuth token, invoice create (draft), get, list, send,
remind, cancel (cleanup). Refund is attempted only when
PAYPAL_SMOKE_CAPTURE_ID is set, because a real capture id cannot be
minted without a completed buyer payment; otherwise it reports SKIP.

Usage:
    PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... python scripts/sandbox-smoke.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paypilot.errors import PayPalAPIError  # noqa: E402
from paypilot.paypal_client import PayPalClient  # noqa: E402

results: list[tuple[str, str]] = []


def check(name: str, fn):
    try:
        detail = fn()
        results.append((name, f"PASS {detail}"))
    except PayPalAPIError as e:
        results.append((name, f"FAIL {e}"))
    except Exception as e:  # noqa: BLE001 - smoke test must not crash
        results.append((name, f"FAIL unexpected: {type(e).__name__}: {e}"))


def main() -> int:
    cid = os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if not cid or not secret:
        print("SKIP: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to run "
              "the live sandbox smoke test.\n"
              "Get them at https://developer.paypal.com/dashboard/applications/sandbox\n"
              "  -> Log in -> Sandbox -> Create App -> copy Client ID and Secret.")
        return 2

    client = PayPalClient(cid, secret, mode="sandbox")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    email = f"smoke-{stamp}@example.com"

    state: dict = {}

    def s_oauth():
        token = client._access_token()
        assert token, "empty token"
        return f"token acquired (len {len(token)})"
    check("oauth token", s_oauth)

    def s_create():
        inv = client.create_invoice(
            to_email=email, to_name="Smoke Test",
            items=[{"name": "Smoke widget", "quantity": 1,
                    "unit_price": 42.50}],
            due_days=30, note=f"sandbox smoke {stamp}")
        state["id"] = inv["invoice_id"]
        assert inv["status"] == "DRAFT", inv
        return inv["invoice_id"]
    check("invoice create (DRAFT)", s_create)

    def s_get():
        got = client.get_invoice(state["id"])
        assert got["status"] == "DRAFT" and got["total"] == "42.50", got
        return f"status={got['status']} total={got['total']}"
    check("invoice get", s_get)

    def s_list():
        data = client.list_invoices(limit=50)
        ids = {i["invoice_id"] for i in data["invoices"]}
        assert state["id"] in ids, "created invoice not in list"
        return f"found in list of {data['total']}"
    check("invoice list", s_list)

    def s_send():
        out = client.send_invoice(state["id"])
        assert out["sent"], out
        got = client.get_invoice(state["id"])
        assert got["status"] == "SENT", got
        return "status=SENT"
    check("invoice send", s_send)

    def s_remind():
        out = client.remind_invoice(state["id"], note="smoke reminder")
        assert out["reminded"], out
        return "reminder accepted"
    check("invoice remind", s_remind)

    def s_txn_search():
        txns = client.search_transactions(days_back=1)
        return f"{len(txns)} transactions in last day"
    check("transaction search", s_txn_search)

    capture = os.environ.get("PAYPAL_SMOKE_CAPTURE_ID", "")
    if capture:
        def s_refund():
            out = client.refund_capture(capture, note="smoke refund")
            assert out["status"] == "COMPLETED", out
            return f"refund {out['refund_id']}"
        check("refund capture", s_refund)
    else:
        results.append(("refund capture",
                        "SKIP needs PAYPAL_SMOKE_CAPTURE_ID (a real capture "
                        "id); refund logic is covered by the mock test suite"))

    def s_cancel():
        out = client.cancel_invoice(state["id"])
        assert out["status"] == "CANCELLED", out
        return "cancelled (cleanup)"
    check("invoice cancel (cleanup)", s_cancel)

    print("\nPayPilot sandbox smoke results (api-m.sandbox.paypal.com):")
    failed = 0
    for name, res in results:
        print(f"  [{res.split()[0]}] {name}: {res}")
        if res.startswith("FAIL"):
            failed += 1
    print(f"\n{len(results) - failed}/{len(results)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
