"""Proxy-environment sanitizer for localhost MCP connections.

Root cause: the vendored ``httpx2`` copy shipped with ``mcp>=2`` crashes
while building its proxy map when ``no_proxy``/``NO_PROXY`` contains entries
that combine a ``*`` wildcard with bracketed IPv6 literals (this sandbox
sets entries like ``*[::1]``). ``URLPattern('all://*[::1]')`` raises
``InvalidURL("Invalid port: ':1]'")`` inside ``httpx2.AsyncClient.__init__``,
which breaks every local MCP client (the official SDK client and the
Strands ``MCPClient`` alike) before any request is made.

Localhost MCP traffic must bypass the proxy anyway, so before constructing
a local client we normalize ``no_proxy``/``NO_PROXY`` to plain host forms
the vendored parser accepts.
"""
from __future__ import annotations

import os

_SAFE_DEFAULTS = ("localhost", "127.0.0.1", "::1")
_PROXY_VARS = ("no_proxy", "NO_PROXY")


def sanitize_proxy_env() -> dict[str, str]:
    """Rewrite no_proxy/NO_PROXY dropping entries the parser chokes on.

    Returns the previous values so callers can restore them if needed.
    """
    previous = {v: os.environ.get(v, "") for v in _PROXY_VARS}
    for var in _PROXY_VARS:
        kept: list[str] = []
        for part in os.environ.get(var, "").split(","):
            p = part.strip()
            if not p or p.startswith("*") or p.startswith("["):
                continue
            if p not in kept:
                kept.append(p)
        for default in _SAFE_DEFAULTS:
            if default not in kept:
                kept.append(default)
        os.environ[var] = ",".join(kept)
    return previous
