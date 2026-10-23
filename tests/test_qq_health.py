from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("qq_health", ROOT / "scripts" / "qq_health.py")
health = module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def test_unknown_and_offline_alerts_are_separate(self):
        state = health.AlertState()
        self.assertFalse(state.update(None))
        self.assertTrue(state.update(None))
        self.assertFalse(state.update(None))
        self.assertFalse(state.update(False))
        self.assertTrue(state.update(False))

    def test_qr_login_page_is_definitely_offline(self):
        client = health.NapCatHealth.__new__(health.NapCatHealth)
        client.credential, client.expires = "test-only", float("inf")
        client.post = lambda *args: {
            "isLogin": False, "coreReady": False, "loginPhase": "waiting_qrcode"
        }
        self.assertFalse(client.check()["online"])

    def test_automatic_reconnect_is_not_reported_as_logout(self):
        client = health.NapCatHealth.__new__(health.NapCatHealth)
        client.credential, client.expires = "test-only", float("inf")
        client.post = lambda *args: {
            "isLogin": False, "coreReady": False, "loginPhase": "reconnecting"
        }
        self.assertIsNone(client.check()["online"])

    def test_unavailable_management_api_is_not_reported_as_offline(self):
        client = health.NapCatHealth.__new__(health.NapCatHealth)
        client.credential, client.expires = "test-only", float("inf")

        def unavailable(*args):
            raise URLError("temporary outage")

        client.post = unavailable
        self.assertIsNone(client.check()["online"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
