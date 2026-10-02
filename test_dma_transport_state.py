import threading
import time
import unittest

from dma_core import DMACore, DRIVER_LIVENESS_TIMEOUT_SEC, DriverConnectionState


class DriverTransportStateTest(unittest.TestCase):
    @staticmethod
    def make_core():
        core = object.__new__(DMACore)
        core.driver_state_lock = threading.Lock()
        core.connection_lock = threading.Lock()
        core.driver_endpoint_lock = threading.Lock()
        core.connection = None
        core.driver_endpoint = None
        core.driver_connection_state = DriverConnectionState.DISCONNECTED
        core.driver_online = False
        core.last_driver_packet_ts = 0.0
        core.console_lines = []
        core._emit_console_line = lambda *args, **kwargs: core.console_lines.append(args[0])
        return core

    def test_compatibility_send_failure_drops_existing_connection(self):
        core = self.make_core()

        class FakeConnection:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        connection = FakeConnection()
        core.connection = connection

        core._handle_heartbeat_failure()

        self.assertTrue(connection.closed)
        self.assertIsNone(core.connection)
        self.assertFalse(core.driver_online)
        self.assertEqual(core.driver_connection_state, DriverConnectionState.DISCONNECTED)

    def test_connection_waits_for_driver_frame_before_online(self):
        core = self.make_core()

        core._set_driver_connection_state(DriverConnectionState.CONNECTED_WAITING_FRAME)

        self.assertFalse(core.driver_online)
        self.assertEqual(
            core.driver_connection_state,
            DriverConnectionState.CONNECTED_WAITING_FRAME,
        )

    def test_received_driver_frame_marks_online(self):
        core = self.make_core()

        core._mark_driver_packet_received()

        self.assertTrue(core.driver_online)
        self.assertEqual(core.driver_connection_state, DriverConnectionState.ONLINE)
        self.assertGreater(core.last_driver_packet_ts, 0.0)

    def test_stale_online_frame_returns_to_waiting_state(self):
        core = self.make_core()
        core._set_driver_connection_state(DriverConnectionState.ONLINE)
        core.last_driver_packet_ts = time.monotonic() - DRIVER_LIVENESS_TIMEOUT_SEC - 1.0

        core._expire_driver_online_if_stale()

        self.assertFalse(core.driver_online)
        self.assertEqual(
            core.driver_connection_state,
            DriverConnectionState.CONNECTED_WAITING_FRAME,
        )


if __name__ == "__main__":
    unittest.main()
