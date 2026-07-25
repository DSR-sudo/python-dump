from pathlib import Path
import socket
import sys
import time


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dma_core  # noqa: E402
from dma_protocol import PACKET_TYPE_LOG  # noqa: E402


TEST_TIMEOUT_SEC = 2.0


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_endpoint(core, expected_endpoint):
    deadline = time.monotonic() + TEST_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if core.get_driver_endpoint() == expected_endpoint:
            return
        time.sleep(0.01)
    raise AssertionError("driver endpoint was not learned")


def stop_core(core):
    core.is_running = False
    core.sock.close()


def test_learned_driver_endpoint_receives_heartbeat_and_command(monkeypatch):
    listener_port = free_udp_port()
    configured_driver_port = free_udp_port()
    monkeypatch.setattr(dma_core, "BIND_PORT", listener_port)
    monkeypatch.setattr(dma_core, "DRIVER_IP", "127.0.0.1")
    monkeypatch.setattr(dma_core, "DRIVER_PORT", configured_driver_port)
    core = dma_core.DMACore()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as driver_socket:
        driver_socket.bind(("127.0.0.1", 0))
        driver_socket.settimeout(TEST_TIMEOUT_SEC)
        learned_endpoint = driver_socket.getsockname()
        try:
            driver_socket.sendto(
                bytes([PACKET_TYPE_LOG]) + b"[PMU][GLOBALS] ready",
                ("127.0.0.1", listener_port),
            )
            wait_for_endpoint(core, learned_endpoint)

            core._send_heartbeat()
            assert driver_socket.recvfrom(64)[0].startswith(b"HELO")

            core.send_to_driver(b"command")
            assert driver_socket.recvfrom(64)[0] == b"command"
        finally:
            stop_core(core)
