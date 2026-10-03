# Friction log

Honest notes from building PayPilot. Real issues hit during development,
with the workarounds used.

## 1. No "list payments" endpoint (by design, worked around)
The Payments API v2 has no list operation — you cannot ask PayPal "show
me recent payments" through it. Payment discovery lives in the separate
Transaction Search API (`/v1/reporting/transactions`), which has its own
auth scope, date-range params, and a deeply nested response shape
(`transaction_details[].transaction_info.transaction_amount.value`).
PayPilot's `payment_search` tool wraps both the search call and the
response flattening. Suggestion: a unified "payments" read surface would
save every integration this discovery step.

## 2. Refund needs a capture id you cannot easily mint (documented)
`POST /v2/payments/captures/{id}/refund` is clean, but producing a real
capture id in the sandbox requires completing a buyer checkout flow —
there is no API-only way to mint one. The smoke test therefore covers
OAuth + the full invoice lifecycle live, and refunds run against the
mock plus a documented `PAYPAL_SMOKE_CAPTURE_ID` escape hatch. This is a
PayPal sandbox limitation, not a code gap.

## 3. Invoice list has no status filter (worked around)
`GET /v2/invoicing/invoices` accepts pagination params but no status
filter, so "who hasn't paid yet" requires fetching a page and filtering
client-side. PayPilot does this in `invoice_list` (status=UNPAID maps to
SENT). Fine at hackathon scale; worth knowing before building dashboards.

## 4. Vendored httpx2 vs no_proxy, again (worked around)
Same environment quirk as the previous build: the `mcp>=2` vendored
httpx copy crashes on `no_proxy` entries like `*[::1]`. Reused the
`netenv.sanitize_proxy_env()` approach so localhost MCP tests run.
