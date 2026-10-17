"""Offline checks: never contact AWS or use real credentials."""

import json
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from backend import config


class BoxConfigurationTests(unittest.TestCase):
    def setUp(self):
        config._cache.clear()
        self.environment = patch.dict("os.environ", {
            "BOX_SECRET_ID": "test/box", "AWS_REGION": "us-east-2"
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.values = {
            "BOX_ENTERPRISE_ID": "test-enterprise",
            "BOX_CLIENT_ID": "test-client",
            "BOX_CLIENT_SECRET": "fake-sensitive-value",
        }

    def test_cache_and_explicit_refresh(self):
        client = Mock()
        client.get_secret_value.return_value = {"SecretString": json.dumps(self.values)}
        with patch.object(config.boto3, "client", return_value=client):
            credentials = config.get_box_credentials()
            self.assertEqual(credentials.client_secret, "fake-sensitive-value")
            self.assertNotIn("fake-sensitive-value", repr(credentials))
            self.assertIs(config.get_box_credentials(), credentials)
            self.assertEqual(client.get_secret_value.call_count, 1)
            config.get_box_credentials(force_refresh=True)
            self.assertEqual(client.get_secret_value.call_count, 2)
        client.get_secret_value.assert_called_with(SecretId="test/box", VersionStage="AWSCURRENT")

    def test_bad_or_blank_values_are_rejected_without_echoing(self):
        for payload in ("not JSON", "[]", "{}", json.dumps({**self.values, "BOX_CLIENT_SECRET": " "}),
                        json.dumps({**self.values, "BOX_ENTERPRISE_ID": 123})):
            with self.subTest(payload=payload):
                with self.assertRaises(config.ConfigurationError) as raised:
                    config._parse_credentials(payload)
                self.assertNotIn("fake-sensitive-value", str(raised.exception))

    def test_access_denial_is_safe_and_not_cached(self):
        client = Mock()
        client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "fake-sensitive-value"}},
            "GetSecretValue",
        )
        with patch.object(config.boto3, "client", return_value=client):
            with self.assertRaises(config.ConfigurationError) as raised:
                config.get_box_credentials()
        self.assertNotIn("fake-sensitive-value", str(raised.exception))
        self.assertIn("organization policies", str(raised.exception))
        self.assertFalse(config._cache)


if __name__ == "__main__":
    unittest.main()
