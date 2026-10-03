# Product feedback

Per the hackathon's spirit: which tools were used, what worked, what
needs work. All from actually building PayPilot against them.

## PayPal REST API (sandbox: api-m.sandbox.paypal.com)

What worked well: OAuth2 client-credentials is textbook and fast; the
Invoices v2 API is well-designed (draft -> send -> remind -> paid state
machine, idempotent-ish creates, clear 422s on illegal transitions);
error bodies carry machine-readable `name` fields that map cleanly to
client exceptions.

What needs work: payment discovery is split across a second API
(Transaction Search) with a much clunkier shape; there is no API-only
path to a refundable capture in the sandbox, which makes refund
integration tests painful; invoice list lacks a status filter.

Would I build with it again: yes. The invoice lifecycle is genuinely
agent-friendly once wrapped.

## MCP Python SDK (mcp 2.x)

Same read as the previous build: Streamable HTTP works out of the box,
`@mcp.tool()` decorators generate correct schemas, the client session
API is clean. The 1.x -> 2.x renames still break old examples with no
shims, and the vendored httpx2 still crashes on common `no_proxy`
values (see FRICTION_LOG.md). Would build with it again: yes.

## Devpost

Registration was quick and the rules page is thorough. The official
rules prize table ($67,500) disagrees with the marketing copy
($69,750) — the rules control, but the mismatch is sloppy.
