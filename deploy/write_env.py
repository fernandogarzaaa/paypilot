"""Write the demo env file, fetching the model key from SSM Parameter Store.

The key lives in AWS Systems Manager Parameter Store (SecureString) at
/paypilot/demo-key, created via the AWS console. This script runs on the
EC2 instance (which has an IAM role allowing ssm:GetParameter on it) and
writes /home/ec2-user/paypilot-demo.env. The key never appears on a
command line or in the repo.

Paste artifacts (interior whitespace from wrapped copies) are stripped;
the cleaned key must match the expected OpenRouter format or the script
fails loudly instead of writing a broken env file.
"""

import re
import sys
import time

import boto3
from botocore.exceptions import ClientError

PARAM_NAME = "/paypilot/demo-key"
ENV_PATH = "/home/ec2-user/paypilot-demo.env"
KEY_RE = re.compile(r"sk-or-v1-[A-Za-z0-9]+")


def fetch_key() -> str:
    client = boto3.client("ssm", region_name="us-east-1")
    last_error = None
    for _ in range(6):
        try:
            raw = client.get_parameter(Name=PARAM_NAME, WithDecryption=True)[
                "Parameter"
            ]["Value"]
            # Remove ALL whitespace: paste artifacts (wrapped lines, spaces)
            # can land inside the value, not just at the ends.
            key = re.sub(r"\s+", "", raw)
            if KEY_RE.fullmatch(key):
                return key
            last_error = f"format check failed (length {len(key)})"
        except ClientError as exc:
            last_error = exc
        time.sleep(10)
    raise RuntimeError(f"could not fetch a valid key from {PARAM_NAME}: {last_error}")


def main() -> None:
    key = fetch_key()
    env = (
        "PAYPILOT_TRANSPORT=mock\n"
        "PAYPILOT_MODEL_BASE_URL=https://openrouter.ai/api/v1\n"
        f"PAYPILOT_MODEL_API_KEY={key}\n"
        "PAYPILOT_MODEL_ID=deepseek/deepseek-chat-v3-0324\n"
        "PAYPILOT_WEB_PORT=8931\n"
    )
    with open(ENV_PATH, "w") as f:
        f.write(env)
    # Print only lengths, never the key.
    print(f"key length: {len(key)} (expected 73); env written: {len(env)} bytes")


if __name__ == "__main__":
    sys.exit(main())
