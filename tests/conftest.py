"""Shared fixtures: mock PayPal transport and a real MCP server subprocess."""
import os
import socket
import subprocess
import sys
import time

import pytest

from paypilot.netenv import sanitize_proxy_env  # noqa: E402

sanitize_proxy_env()

from paypilot.paypal_client import PayPalClient  # noqa: E402
from paypilot.transport import MockPayPalTransport  # noqa: E402

os.environ.setdefault("PAYPILOT_TRANSPORT", "mock")


@pytest.fixture
def transport():
    return MockPayPalTransport()


@pytest.fixture
def client(transport):
    return PayPalClient("test-id", "test-secret", mode="sandbox",
                        transport=transport)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mcp_server_url():
    """A real `python -m paypilot.mcp_server` subprocess on an ephemeral port."""
    port = _free_port()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PAYPILOT_TRANSPORT": "mock",
           "PYTHONPATH": root}
    proc = subprocess.Popen(
        [sys.executable, "-m", "paypilot.mcp_server",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail("MCP server exited early:\n" + out)
        with socket.socket() as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("MCP server did not start in 30s")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
