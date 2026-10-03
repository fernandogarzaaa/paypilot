"""MCP server tests: real Streamable HTTP server, official SDK client."""
import asyncio
import json

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "invoice_create", "invoice_send", "invoice_remind", "invoice_list",
    "invoice_status", "payment_search", "refund_payment", "account_summary",
}


async def _with_session(url, fn):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await fn(session, init)


def test_protocol_version(mcp_server_url):
    async def go():
        async def fn(session, init):
            return init.protocol_version
        return await _with_session(mcp_server_url, fn)
    version = asyncio.run(go())
    assert version >= "2025-11-25", f"protocol version {version} too old"
    print(f"MCP protocol version: {version}")


def test_list_tools(mcp_server_url):
    async def go():
        async def fn(session, _init):
            tools = await session.list_tools()
            return {t.name for t in tools.tools}
        return await _with_session(mcp_server_url, fn)
    names = asyncio.run(go())
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


def test_invoice_lifecycle_over_mcp(mcp_server_url):
    async def go():
        async def fn(session, _init):
            created = await session.call_tool("invoice_create", {
                "recipient_email": "mcp-test@example.com",
                "recipient_name": "MCP Test",
                "items": json.dumps(
                    [{"name": "Widget", "quantity": 2,
                      "unit_price": 25}]),
                "due_days": 15,
            })
            body = json.loads(created.content[0].text)
            inv_id = body["invoice_id"]
            assert inv_id.startswith("INV2-")

            sent = await session.call_tool("invoice_send",
                                           {"invoice_id": inv_id})
            assert json.loads(sent.content[0].text)["sent"] is True

            status = await session.call_tool("invoice_status",
                                             {"invoice_id": inv_id})
            assert json.loads(status.content[0].text)["status"] == "SENT"
            return True
        return await _with_session(mcp_server_url, fn)
    assert asyncio.run(go()) is True


def test_payment_search_and_summary_over_mcp(mcp_server_url):
    async def go():
        async def fn(session, _init):
            found = await session.call_tool("payment_search",
                                            {"query": "john"})
            body = json.loads(found.content[0].text)
            assert body["count"] == 1

            summary = await session.call_tool("account_summary", {})
            sbody = json.loads(summary.content[0].text)
            assert "outstanding_invoices" in sbody
            assert "payments_received_30d" in sbody
            return True
        return await _with_session(mcp_server_url, fn)
    assert asyncio.run(go()) is True
