#!/usr/bin/env python3
"""One-shot wrapper: reads {"client_id":..., "client_secret":...} from stdin,
injects them as env vars, and runs the PayPilot sandbox smoke test.

Credentials are never written to disk, never logged, never echoed.
Stdin is the only input channel; values live only in this process's memory.
"""
import json
import os
import runpy
import sys

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("wrapper: invalid JSON on stdin", file=sys.stderr)
        return 2
    cid = payload.get("client_id", "")
    secret = payload.get("client_secret", "")
    if not cid or not secret:
        print("wrapper: client_id and client_secret required", file=sys.stderr)
        return 2
    os.environ["PAYPAL_CLIENT_ID"] = cid
    os.environ["PAYPAL_CLIENT_SECRET"] = secret
    # Never print the values.
    sys.argv = ["sandbox-smoke.py"]
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        runpy.run_path("scripts/sandbox-smoke.py", run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
