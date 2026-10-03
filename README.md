# PayPilot — agentic commerce operator for PayPal

Natural language in, PayPal operations out. PayPilot is a solo-built entry
for the **PayPal AI Hackathon**, targeting the main prizes and
**Best Use of Agentic Commerce**.

Say *"Invoice Acme Corp $1,250 due in 30 days"*, *"Who hasn't paid yet?"*,
or *"Refund the last payment from John"* — a tool-calling agent turns it
into real PayPal REST API calls through a self-hosted MCP server.

## What it does

- **8 PayPal tools** over a self-hosted MCP server (Streamable HTTP):
  `invoice_create`, `invoice_send`, `invoice_remind`, `invoice_list`,
  `invoice_status`, `payment_search`, `refund_payment`, `account_summary`
- **Agent loop** (`paypilot/agent.py`): a compact ReAct-style tool-calling
  loop over any OpenAI-compatible chat endpoint. No heavy framework, no
  silent fallbacks — an unconfigured model raises a clear error.
- **PayPal REST client** (`paypilot/paypal_client.py`): OAuth2
  client-credentials, Invoices v2 (create/get/list/send/remind/cancel),
  Transaction Search (payment lookup), Payments v2 refunds. All network
  I/O goes through an injectable transport.
- **Mock transport** (`paypilot/transport.py`): an in-memory PayPal
  simulation (OAuth, invoice lifecycle with real state transitions,
  transaction search, refunds) so the whole suite runs without
  credentials.
- **Web demo** (`paypilot/web.py` + `webui/index.html`): chat UI that
  shows every tool call the agent makes.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# run the tests (mock backend, no credentials needed)
python -m pytest

# start the MCP server (mock backend by default)
PAYPILOT_TRANSPORT=mock python -m paypilot.mcp_server

# start the web demo (needs a model; see below)
PAYPILOT_TRANSPORT=mock \
PAYPILOT_MODEL_BASE_URL=http://127.0.0.1:11435/v1 \
PAYPILOT_MODEL_ID=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B \
python -m paypilot.web
# open http://127.0.0.1:8931/
```

## Real PayPal sandbox setup

1. Go to https://developer.paypal.com/dashboard/applications/sandbox
   and log in with a PayPal developer account.
2. Create an app (or open the default one) and copy the **Client ID**
   and **Secret**.
3. Export them:
   ```bash
   export PAYPAL_CLIENT_ID="your-sandbox-client-id"
   export PAYPAL_CLIENT_SECRET="your-sandbox-client-secret"
   export PAYPAL_MODE=sandbox   # the default; use "live" only deliberately
   ```
4. Run the live smoke test (full invoice lifecycle against
   `api-m.sandbox.paypal.com`, with cleanup):
   ```bash
   python scripts/sandbox-smoke.py
   ```
   Without credentials it exits 2 with these instructions; nothing is
   fabricated. To also smoke-test refunds, set `PAYPAL_SMOKE_CAPTURE_ID`
   to a real sandbox capture id (refunds need a completed payment).

Unset `PAYPILOT_TRANSPORT` (or set it to anything but `mock`) to use the
real sandbox backend in the MCP server and web demo.

## Model configuration

The agent needs an OpenAI-compatible chat-completions endpoint with tool
calling:

```bash
export PAYPILOT_MODEL_BASE_URL="http://127.0.0.1:11434/v1"
export PAYPILOT_MODEL_API_KEY="..."   # if the endpoint needs one
export PAYPILOT_MODEL_ID="model-id"
```

Without it, the agent raises `ConfigurationError` naming the missing
variables instead of pretending to work.

## Project layout

```
paypilot/
  paypal_client.py   PayPal REST client (OAuth, invoices, search, refunds)
  transport.py       UrllibTransport (real) + MockPayPalTransport (tests)
  tools.py           shared tool specs + dispatcher (agent/MCP/web)
  agent.py           tool-calling agent loop + model clients
  mcp_server.py      self-hosted MCP server (Streamable HTTP, 8 tools)
  web.py             FastAPI demo UI backend
  netenv.py          localhost proxy-env sanitizer (test env fix)
webui/index.html     demo chat page
tests/               full suite (mock backend; MCP over real HTTP)
scripts/sandbox-smoke.py  live sandbox lifecycle test
```

## License

MIT. Built solo for the PayPal AI Hackathon.
