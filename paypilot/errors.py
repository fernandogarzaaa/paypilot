"""PayPilot exception hierarchy."""


class PayPilotError(RuntimeError):
    """Base class for all PayPilot errors."""


class ConfigurationError(PayPilotError):
    """Raised when required configuration (credentials, model) is missing.

    Always carries an actionable message naming the exact missing piece;
    PayPilot never silently degrades to a weaker backend.
    """


class PayPalAPIError(PayPilotError):
    """A PayPal REST API call failed.

    Attributes:
        status: HTTP status code (0 when the request never got a response).
        error_name: PayPal's error name (e.g. INVALID_REQUEST), if provided.
        detail: human-readable detail from PayPal or the transport.
    """

    def __init__(self, status: int, error_name: str = "",
                 detail: str = ""):
        self.status = status
        self.error_name = error_name
        self.detail = detail
        msg = f"PayPal API error (HTTP {status})"
        if error_name:
            msg += f" [{error_name}]"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class ToolError(PayPilotError):
    """A tool call was invalid (unknown tool, missing/invalid arguments)."""
