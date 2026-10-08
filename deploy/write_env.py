"""Write the demo env file, fetching the model key from SSM Parameter Store.

The key lives in AWS Systems Manager Parameter Store (SecureString) at
/paypilot/demo-key, created via the AWS console. This script runs on the
EC2 instance (which has an IAM role allowing ssm:GetParameter on it) and
writes /home/ec2-user/paypilot-demo.env. The key never appears on a
command line or in the repo.
"""

import sys
import time

import boto3
from botocore.exceptions import ClientError

PARAM_NAME = "/paypilot/demo-key"
ENV_PATH = "/home/ec2-user/paypilot-demo.env"


def fetch_key() -> str:
    client = boto3.client("ssm", region_name="us-east-1")
    last_error = None
    for attempt in range(6):
        try:
            value = client.get_parameter(Name=PARAM_NAME, WithDecryption=True)[
                "Parameter"
            ]["Value"].strip()
            if value:
                return value
        except ClientError as exc:
            last_error = exc
        time.sleep(10)
    raise RuntimeError(f"could not fetch {PARAM_NAME}: {last_error}")


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
    # Print only the byte count, never the key.
    print(f"env written: {len(env)} bytes (expected 243)")


if __name__ == "__main__":
    sys.exit(main())
