import ctypes
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
spec = spec_from_file_location("qq_health", Path(__file__).resolve().parents[1] / "scripts/qq_health.py")
health = module_from_spec(spec)
spec.loader.exec_module(health)


class HealthAlertTests(unittest.TestCase):
    def test_transient_unknown_does_not_alert(self):
        with patch.object(health.time, 'monotonic', return_value=0) as clock:
            state = health.AlertState()
            self.assertFalse(state.update(None))
            clock.return_value = 17
            self.assertFalse(state.update(None))
            clock.return_value = 34
            self.assertFalse(state.update(True))
            self.assertEqual(state.failures, 0)

    def test_persistent_unknown_alerts_once_after_five_minutes(self):
        with patch.object(health.time, 'monotonic', return_value=0) as clock:
            state = health.AlertState()
            self.assertFalse(state.update(None))
            clock.return_value = 299
            self.assertFalse(state.update(None))
            clock.return_value = 300
            self.assertTrue(state.update(None))
            clock.return_value = 600
            self.assertFalse(state.update(None))

    def test_confirmed_offline_still_alerts_on_second_check(self):
        state = health.AlertState()
        self.assertFalse(state.update(False))
        self.assertTrue(state.update(False))
        self.assertFalse(state.update(False))

    def test_unknown_does_not_delay_confirmed_offline(self):
        state = health.AlertState()
        self.assertFalse(state.update(None))
        self.assertFalse(state.update(False))
        self.assertTrue(state.update(False))

    def test_recovery_and_pause_rearm_alerts(self):
        state = health.AlertState()
        for reset, paused in ((True, False), (False, True)):
            self.assertFalse(state.update(False))
            self.assertTrue(state.update(False))
            self.assertFalse(state.update(reset, paused=paused))

    def test_management_failure_logs_safe_details_as_unknown(self):
        client = health.NapCatHealth.__new__(health.NapCatHealth)
        client.credential, client.expires = 'test-only', float('inf')
        client.request_path = '/QQLogin/CheckLoginStatus'
        client.post = Mock(side_effect=URLError(ConnectionRefusedError(10061, 'refused')))
        result = client.check()
        self.assertIsNone(result['online'])
        self.assertEqual(result['error_code'], 10061)
        self.assertEqual(result['request_path'], '/QQLogin/CheckLoginStatus')
        self.assertNotIn('test-only', str(result))

    def test_close_targets_only_the_popup_thread(self):
        thread = Mock(native_id=1234)
        thread.is_alive.return_value = True
        user32 = Mock()
        user32.EnumThreadWindows.side_effect = lambda tid, callback, arg: callback(0x100000123, arg)
        with patch.object(ctypes.windll, 'user32', user32):
            health.close_popup(thread)
        self.assertEqual(user32.EnumThreadWindows.call_args.args[0], 1234)
        user32.PostMessageW.assert_called_once_with(0x100000123, 0x0010, 0, 0)
        thread.join.assert_called_once_with(timeout=0.2)

    def test_closing_absent_popup_is_noop(self):
        health.close_popup(None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
