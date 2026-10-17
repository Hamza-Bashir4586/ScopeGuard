"""Load server-side Box CCG credentials without exposing them in output."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from threading import Lock
from time import monotonic

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Configuration could not be loaded; messages contain no secret values."""


@dataclass(frozen=True)
class BoxCredentials:
    enterprise_id: str = field(repr=False)
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)


_cache: dict[tuple[str, str], tuple[float, BoxCredentials]] = {}
_lock = Lock()
_CACHE_SECONDS = 300


def _parse_credentials(raw: str) -> BoxCredentials:
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        raise ConfigurationError("The Box secret must contain a JSON object.") from None
    if not isinstance(values, dict):
        raise ConfigurationError("The Box secret must contain a JSON object.")
    keys = ("BOX_ENTERPRISE_ID", "BOX_CLIENT_ID", "BOX_CLIENT_SECRET")
    missing = [key for key in keys if not isinstance(values.get(key), str) or not values[key].strip()]
    if missing:
        raise ConfigurationError("Populate non-empty string fields in the Box secret: " + ", ".join(missing))
    return BoxCredentials(*(values[key] for key in keys))


def get_box_credentials(*, force_refresh: bool = False) -> BoxCredentials:
    """Read AWSCURRENT from Secrets Manager; cache successful reads for five minutes.

    Uses the AWS SDK credential chain (local AWS login/profile or Lambda IAM role).
    Never place the returned object in an API response or frontend configuration.
    """
    # Local convenience only: Lambda uses deployment environment and its IAM role.
    # Existing shell configuration wins over values in .env.
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    secret_id = os.getenv("BOX_SECRET_ID", "scopeguard/box/ccg").strip()
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip()
    if not secret_id or not region:
        raise ConfigurationError("BOX_SECRET_ID and the AWS region must not be blank.")
    key = (region, secret_id)
    with _lock:
        cached = _cache.get(key)
        if not force_refresh and cached and monotonic() < cached[0]:
            return cached[1]
        try:
            client = boto3.client(
                "secretsmanager",
                region_name=region,
                config=Config(connect_timeout=5, read_timeout=10,
                              retries={"mode": "standard", "total_max_attempts": 3}),
            )
            response = client.get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"AccessDeniedException", "AccessDenied"}:
                message = "AWS denied secret access. Check secretsmanager:GetSecretValue, organization policies, and KMS permissions."
            elif code == "ResourceNotFoundException":
                message = "Box secret not found. Check BOX_SECRET_ID and the AWS region."
            else:
                message = "AWS could not retrieve the Box secret. Check secret configuration and service availability."
            raise ConfigurationError(message) from None
        except BotoCoreError:
            raise ConfigurationError("AWS credentials or connection unavailable. Refresh AWS login or check the Lambda IAM role and network.") from None
        if not isinstance(response.get("SecretString"), str):
            raise ConfigurationError("Store Box credentials as a JSON SecretString, not a binary secret.")
        credentials = _parse_credentials(response["SecretString"])
        _cache[key] = (monotonic() + _CACHE_SECONDS, credentials)
        return credentials


def main() -> int:
    """Validate retrieval without displaying credentials or requesting a Box token."""
    try:
        get_box_credentials(force_refresh=True)
    except ConfigurationError as exc:
        print(f"Box configuration unavailable: {exc}")
        return 1
    print("Box configuration loaded: all three required fields are populated. Values hidden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
