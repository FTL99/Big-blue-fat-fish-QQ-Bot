import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import runtime_harness as h


class RecoveryInstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_install_without_private_seed_starts_at_current_time(self):
        owner = SimpleNamespace(get_kv_data=AsyncMock(return_value=None))
        recovery = h.support_module.Recovery(owner)
        before = int(time.time()) - 1
        with patch.object(recovery, 'run', AsyncMock()):
            await recovery.start()
            self.assertGreaterEqual(recovery.state['time'], before)
            self.assertLessEqual(recovery.state['time'], int(time.time()))
            self.assertEqual(recovery.state['seen'], [])
            await recovery.stop()

    async def test_existing_cursor_survives_restart(self):
        cursor = {'time': 1234, 'seen': ['fixture-message']}
        recovery = h.support_module.Recovery(SimpleNamespace(get_kv_data=AsyncMock(return_value=cursor)))
        with patch.object(recovery, 'run', AsyncMock()):
            await recovery.start()
            self.assertEqual(recovery.state, cursor)
            await recovery.stop()


if __name__ == '__main__':
    unittest.main()
