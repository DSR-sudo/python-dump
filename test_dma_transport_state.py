import contextlib
import concurrent.futures
import os
import queue
import socket
import subprocess
import unittest
import io
from unittest.mock import patch

import dma_core
from dma_core import DMACore, DRIVER_LIVENESS_TIMEOUT_SEC, DriverConnectionState
from dma_protocol import HEARTBEAT, pack_packet, pack_read_req


class DriverTransportStateTest(unittest.TestCase):
    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in [('UDP_LISTEN_HOST', '127.0.0.1'), ('BIND_PORT', 0),
                            ('RWBASE_DECRYPT_LOG_ENABLED', False)]:
            self.patches.enter_context(patch.object(dma_core, name, value))
        # Exercise heartbeat explicitly, keeping command wire tests deterministic.
        self.patches.enter_context(patch.object(DMACore, '_heartbeat_loop',
                                               lambda core: core.stop_event.wait()))
        self.core = DMACore()
        self.addCleanup(self.core.shutdown)
        self.packets = queue.Queue()
        original = self.core._process_packet

        def record(kind, payload):
            original(kind, payload)
            self.packets.put((kind, payload))

        self.core._process_packet = record
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer.bind(('127.0.0.1', 0))
        self.peer.settimeout(1)
        self.addCleanup(self.peer.close)
        self.address = self.core.sock.getsockname()

    def send_packet(self, peer=None, payload=b'DRIVER_ONLINE'):
        (peer or self.peer).sendto(pack_packet(1, payload), self.address)
        self.assertEqual(self.packets.get(timeout=2), (1, payload))

    def test_plaintext_duplex_same_socket(self):
        self.assertFalse(self.core.driver_online)
        with self.assertRaises(RuntimeError):
            self.core.send_to_driver(HEARTBEAT)
        self.send_packet(payload='hello 中文'.encode())
        self.assertTrue(self.core.driver_online)
        self.assertEqual(self.core.get_driver_endpoint(), self.peer.getsockname())
        request = pack_read_req(0x12345000, 0x7ff700001000, 8)
        self.assertEqual(self.core.send_to_driver(request), 1049)
        data, sender = self.peer.recvfrom(65536)
        self.assertEqual(data, request)
        self.assertEqual(sender, self.address)
        self.core._send_heartbeat()
        self.assertEqual(self.peer.recv(65536), HEARTBEAT)
        with self.assertRaises(ValueError):
            self.core.send_to_driver(b'arbitrary data')

    def test_main_status_deduplicated_and_preserves_input(self):
        self.core.begin_console_input()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.send_packet(payload=b'[PMU][STATUS] scan: waiting for game')
            first_ts = self.core.main_status_ts
            self.send_packet(payload=b'[PMU][STATUS] scan: waiting for game')
            self.assertGreaterEqual(self.core.main_status_ts, first_ts)
            self.assertEqual(out.getvalue(), '')
            self.assertEqual(len(self.core.console_deferred_lines), 2)
            self.core.end_console_input()
        self.assertEqual(out.getvalue().count('scan: waiting for game'), 1)
        self.assertIn('scan: waiting for game', self.core.format_status())
        self.assertIn('frames=0', self.core.format_status())
        self.core.last_driver_packet_ts -= DRIVER_LIVENESS_TIMEOUT_SEC + 1
        self.core._expire_driver_online_if_stale()
        self.assertIn('offline', self.core.format_status())

    def test_invalid_packet_does_not_block_next_datagram(self):
        for packet in (b'', b'\xffjunk', b'\x1a\x00\x00\x00old TCP'):
            self.peer.sendto(packet, self.address)
        self.send_packet()
        self.assertEqual(self.core.protocol_invalid_packets, 3)

    def test_live_peer_pinned_then_expires(self):
        self.send_packet()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
            other.bind(('127.0.0.1', 0))
            other.sendto(pack_packet(1, b'foreign'), self.address)
            with self.assertRaises(queue.Empty):
                self.packets.get(timeout=0.1)
            self.assertEqual(self.core.get_driver_endpoint(), self.peer.getsockname())
            self.core.last_driver_packet_ts -= DRIVER_LIVENESS_TIMEOUT_SEC + 1
            self.core._expire_driver_online_if_stale()
            self.assertEqual(self.core.driver_connection_state, DriverConnectionState.DISCONNECTED)
            self.assertIsNone(self.core.get_driver_endpoint())
            self.send_packet(other)
            self.assertEqual(self.core.get_driver_endpoint(), other.getsockname())

    def test_send_error_keeps_udp_receiver_usable(self):
        self.send_packet()
        self.core._handle_heartbeat_failure()
        self.assertTrue(self.core.driver_online)
        self.send_packet(payload=b'after error')

    def test_send_failure_clears_pending_request(self):
        with self.assertRaises(RuntimeError):
            self.core.request_bytes(pack_read_req(1, 2, 8), 8)
        self.assertEqual(self.core.expected_size, 0)

    def test_request_response_and_pending_peer_pinning(self):
        self.send_packet()
        request = pack_read_req(1, 2, 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(self.core.request_bytes, request, 8)
            self.assertEqual(self.peer.recv(65536), request)
            self.core.last_driver_packet_ts -= DRIVER_LIVENESS_TIMEOUT_SEC + 1
            self.core._expire_driver_online_if_stale()
            self.assertEqual(self.core.get_driver_endpoint(), self.peer.getsockname())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                other.sendto(pack_packet(2, b'wrong!!!'), self.address)
                with self.assertRaises(queue.Empty):
                    self.packets.get(timeout=0.1)
            self.peer.sendto(pack_packet(2, b'correct!'), self.address)
            self.assertEqual(bytes(result.result(timeout=2)), b'correct!')

    def test_shutdown_stops_threads_and_releases_port(self):
        self.core.shutdown()
        self.assertTrue(all(not thread.is_alive() for thread in self.core.threads))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as new:
            new.bind(self.address)

    @unittest.skipUnless(os.getenv('QEMU_PROXY_MAIN'), 'requires compiled C Business main')
    def test_c_business_main_duplex(self):
        with subprocess.Popen([os.environ['QEMU_PROXY_MAIN'], 'log', self.address[0],
                               str(self.address[1]), 'C plaintext 中文', '3000'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                self.assertEqual(self.packets.get(timeout=2), (1, 'C plaintext 中文'.encode()))
                self.core.send_to_driver(pack_read_req(0x12345000, 0x7ff700001000, 8))
                out, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, err)
                self.assertIn('command=1 value=0x12345000 address=0x7ff700001000 size=8', out)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    @unittest.skipUnless(os.getenv('QEMU_PROXY_DMA_PEER'), 'requires compiled C protocol peer')
    def test_c_all_packet_types_and_binary_request(self):
        with subprocess.Popen([os.environ['QEMU_PROXY_DMA_PEER'], self.address[0],
                               str(self.address[1])], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as child:
            try:
                for kind in (1, 2, 3, 6):
                    self.assertEqual(self.packets.get(timeout=2), (kind, bytes(range(256))))
                    from dma_protocol import pack_write_req
                    self.core.send_to_driver(pack_write_req(0x1122334455667788,
                                              0x8877665544332211, bytes(range(256)) * 4))
                self.assertEqual(self.packets.get(timeout=2), (1, b'heartbeat'))
                self.core._send_heartbeat()
                out, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, err)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    unittest.main()
