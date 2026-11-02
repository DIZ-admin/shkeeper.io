"""Isolated checks for fail-closed DB configuration and callback signing."""

import hashlib
import hmac
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from shkeeper import _build_sqlalchemy_database_uri
from shkeeper.callback import _post_signed_callback


class DatabaseUriConfigurationTests(unittest.TestCase):
    def test_absent_database_configuration_keeps_optional_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_build_sqlalchemy_database_uri())

    def test_configured_missing_password_file_fails_closed(self):
        env = {
            "DB_USER": "test_user",
            "DB_HOST": "db",
            "DB_NAME": "test_db",
            "DB_PASSWORD_FILE": "/run/secrets/not-mounted",
            "POSTGRES_PASSWORD": "must-not-fallback",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "password file is unavailable"):
                _build_sqlalchemy_database_uri()

    def test_readable_password_file_builds_database_uri(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as secret:
            secret.write("synthetic-test-password")
            secret.flush()
            env = {
                "DB_USER": "test_user",
                "DB_HOST": "db",
                "DB_PORT": "5432",
                "DB_NAME": "test_db",
                "DB_PASSWORD_FILE": secret.name,
            }
            with patch.dict(os.environ, env, clear=True):
                uri = _build_sqlalchemy_database_uri()
        self.assertEqual(
            uri,
            "postgresql://test_user:synthetic-test-password@db:5432/test_db",
        )


class CallbackSignatureTests(unittest.TestCase):
    def test_callback_signs_exact_raw_body_and_timestamp(self):
        payload = {"external_id": "SYNTHETIC-1", "status": "PAID"}
        api_key = "synthetic-test-key"
        with Flask("callback-test").app_context():
            with patch("shkeeper.callback.requests.post") as post:
                post.return_value.status_code = 202
                _post_signed_callback("http://127.0.0.1/callback", payload, api_key)

        request = post.call_args.kwargs
        body = request["data"]
        headers = request["headers"]
        expected = hmac.new(
            api_key.encode("utf-8"),
            headers["X-Shkeeper-Timestamp"].encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["X-Shkeeper-Signature"], expected)
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(headers["X-Shkeeper-Api-Key"], api_key)


if __name__ == "__main__":
    unittest.main()
