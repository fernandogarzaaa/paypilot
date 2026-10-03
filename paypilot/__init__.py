"""PayPilot: an agentic commerce operator for PayPal.

Natural language in, PayPal operations out. A self-hosted MCP server
exposes PayPal invoice/payment/refund operations; a tool-calling agent
drives them; a small web UI is the demo surface.

Built for the PayPal AI Hackathon (solo entry).
"""
from .errors import PayPilotError, PayPalAPIError, ConfigurationError, ToolError

__all__ = [
    "PayPilotError",
    "PayPalAPIError",
    "ConfigurationError",
    "ToolError",
]
__version__ = "0.1.0"
