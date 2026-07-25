from pathlib import Path
import json
import shutil
import socket
import struct
import sys
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dma_core  # noqa: E402
from dma_protocol import PACKET_TYPE_DATA, RWVG_MAGIC, RWVG_TYPE_ACTOR_SCAN  # noqa: E402
from rwvg_actor_snapshot import (  # noqa: E402
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_VERSION,
)
from web_actor_kinds import WebActorKindsService  # noqa: E402


def free_port(sock_type: int) -> int:
    with socket.socket(socket.AF_INET, sock_type) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def record(actor_address: int) -> bytes:
    class_name = b"BP_Player"
    return struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        actor_address, 7, 1, len(class_name), 1, 2, 3,
        0x3F800000, 0x40000000, 0x40400000, 6, 77, 2,
        0x41200000, 0x41A00000, 9, 15, 1, 0, 0,
    ) + class_name


def v2_frame(snapshot_id: int, fragment_index: int, records: list[bytes]) -> bytes:
    payload = struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        len(records), RWVG_ACTOR_SNAPSHOT_VERSION, snapshot_id,
        fragment_index, 2, 2,
    ) + b"".join(records)
    return struct.pack("<III", RWVG_MAGIC, RWVG_TYPE_ACTOR_SCAN, len(payload)) + payload


def wait_for_complete(core):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if core.get_actor_scan_snapshot()["complete"]:
            return
        time.sleep(0.01)
    raise AssertionError("UDP V2 snapshot did not reach complete state")


def test_helo_udp_fixture_to_8081_snapshot_chain(monkeypatch):
    udp_port = free_port(socket.SOCK_DGRAM)
    http_port = free_port(socket.SOCK_STREAM)
    monkeypatch.setattr(dma_core, "BIND_PORT", udp_port)
    monkeypatch.setattr(dma_core, "DRIVER_PORT", udp_port)
    monkeypatch.setattr(dma_core, "DRIVER_IP", "127.0.0.1")
    core = dma_core.DMACore()
    service = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"HELO".ljust(32, b"\x00"), ("127.0.0.1", udp_port))
            sender.sendto(bytes([PACKET_TYPE_DATA]) + v2_frame(21, 1, [record(0xB)]), ("127.0.0.1", udp_port))
            sender.sendto(bytes([PACKET_TYPE_DATA]) + v2_frame(21, 0, [record(0xA)]), ("127.0.0.1", udp_port))
        wait_for_complete(core)

        with tempfile.TemporaryDirectory() as tmpdir:
            web_dir = Path(tmpdir) / "web"
            web_dir.mkdir()
            shutil.copy(ROOT / "web" / "actor_kinds.html", web_dir / "actor_kinds.html")
            service = WebActorKindsService(core, root_dir=tmpdir)
            started, _ = service.start(port_override=http_port)
            assert started
            request = urllib.request.Request(
                f"http://127.0.0.1:{http_port}/api/actor_kinds",
                headers={"X-Auth-Token": service.password},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        assert payload["complete"] is True
        assert payload["received_fragments"] == 2
        assert payload["total_record_count"] == 2
        assert {actor["actor_address"] for actor in payload["actors"]} == {0xA, 0xB}
    finally:
        if service is not None:
            service.stop()
        core.is_running = False
        core.sock.close()
