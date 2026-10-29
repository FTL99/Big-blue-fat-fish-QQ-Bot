"""Offline checks: credentials never printed and written only after validation."""
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("login_script", Path(__file__).resolve().parents[1] / "scripts/bilibili_login.py")
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


class LoginTests(unittest.TestCase):
    def test_only_bilibili_session_fields_are_selected(self):
        class Cookie:
            def __init__(self, name, domain): self.name, self.domain, self.value = name, domain, "fixture"
        values = login.credential_values([Cookie("SESSDATA", ".bilibili.com"), Cookie("SESSDATA", "other.test"), Cookie("refresh_token", ".bilibili.com")])
        self.assertEqual(values, {"SESSDATA": "fixture"})

    def test_missing_login_cookies_do_not_create_a_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "TARGET", Path(tmp) / "credential.json"):
            with self.assertRaises(ValueError): login.save_credentials({"SESSDATA": "fixture"})
            self.assertFalse(login.TARGET.exists())

    def test_successful_save_keeps_expected_values(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "TARGET", Path(tmp) / "credential.json"):
            values = {"SESSDATA": "fixture", "DedeUserID": "1"}
            login.save_credentials(values)
            self.assertEqual(json.loads(login.TARGET.read_text(encoding="utf-8")), values)


if __name__ == "__main__": unittest.main()
