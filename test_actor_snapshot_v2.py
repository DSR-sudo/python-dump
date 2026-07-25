from pathlib import Path
import struct
import sys
import threading


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dma_core import DMACore  # noqa: E402
from rwvg_actor_snapshot import (  # noqa: E402
    RWVG_ACTOR_SCAN_HEADER_FMT,
    RWVG_ACTOR_SCAN_VERSION,
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    parse_rwvg_actor_scan_payload,
)


def make_core() -> DMACore:
    core = DMACore.__new__(DMACore)
    core.rwvg_stats = {"actor_scan_frames": 0, "actor_scan_entities": 0}
    core.actor_scan_lock = threading.Lock()
    core.actor_scan_entities = {}
    core.actor_scan_order = []
    core.actor_scan_snapshot_id = None
    core.actor_scan_fragment_count = 0
    core.actor_scan_received_fragments = set()
    core.actor_scan_total_record_count = 0
    core.actor_scan_complete = False
    core.actor_scan_dropped_late_fragments = 0
    core.actor_scan_duplicate_fragments = 0
    core.actor_scan_invalid_fragments = 0
    core.actor_scan_last_status = "awaiting_snapshot"
    core.actor_scan_last_ts = 0.0
    core.actor_scan_last_count = 0
    core.actor_scan_frames = 0
    return core


def make_record(actor_address: int, class_name: bytes = b"BP_Player") -> bytes:
    fixed = struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        actor_address, 42, 1, len(class_name), 0x1000, 0x2000, 0x3000,
        0x3F800000, 0x40000000, 0x40400000, 6, 99, -7,
        0x41200000, 0x41A00000, 17, 0xFF, 5, 2, -1073741811,
    )
    return fixed + class_name


def make_v2(snapshot_id: int, fragment_index: int, fragment_count: int, total: int, records: list[bytes]) -> bytes:
    return struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        len(records), RWVG_ACTOR_SNAPSHOT_VERSION, snapshot_id,
        fragment_index, fragment_count, total,
    ) + b"".join(records)


def test_v2_layout_preserves_every_snapshot_field():
    payload = make_v2(9, 0, 1, 1, [make_record(0xABCDEF)])
    parsed = parse_rwvg_actor_scan_payload(payload)

    assert parsed["version"] == 2
    assert parsed["snapshot_id"] == 9
    assert parsed["fragment_count"] == 1
    record = parsed["records"][0]
    assert record["actor_address"] == 0xABCDEF
    assert record["class_name"] == "BP_Player"
    assert record["mesh"] == 0x1000 and record["root_component"] == 0x2000
    assert record["player_state"] == 0x3000
    assert record["position_bits"] == {"x": 0x3F800000, "y": 0x40000000, "z": 0x40400000}
    assert record["position_source"] == 6 and record["last_db_position_tsc"] == 99
    assert record["team_id"] == -7 and record["weapon_id"] == 17
    assert record["health_bits"] == 0x41200000 and record["max_health_bits"] == 0x41A00000
    assert record["valid_fields"] == 0xFF
    assert record["diagnostics"] == {"attempts": 5, "failures": 2, "first_failure": -1073741811}


def test_v2_reassembles_out_of_order_and_ignores_duplicate_and_late_fragments():
    core = make_core()
    second = make_v2(10, 1, 2, 2, [make_record(0xB)])
    first = make_v2(10, 0, 2, 2, [make_record(0xA)])
    old = make_v2(9, 0, 1, 1, [make_record(0xDEAD)])

    assert core._handle_actor_scan_payload(second, 1.0) == 1
    partial = core.get_actor_scan_snapshot()
    assert partial["complete"] is False
    assert partial["actors"][0]["actor_address"] == 0xB
    assert partial["received_fragments"] == 1

    assert core._handle_actor_scan_payload(second, 2.0) == 0
    assert core.get_actor_scan_snapshot()["duplicate_fragments"] == 1
    assert core._handle_actor_scan_payload(first, 3.0) == 1
    complete = core.get_actor_scan_snapshot()
    assert complete["complete"] is True
    assert {actor["actor_address"] for actor in complete["actors"]} == {0xA, 0xB}

    assert core._handle_actor_scan_payload(old, 4.0) == 0
    assert core.get_actor_scan_snapshot()["dropped_late_fragments"] == 1


def test_v2_new_snapshot_clears_partial_view_and_empty_snapshot_clears_actors():
    core = make_core()
    assert core._handle_actor_scan_payload(make_v2(1, 0, 2, 2, [make_record(1)]), 1.0) == 1
    assert core._handle_actor_scan_payload(make_v2(2, 0, 1, 0, []), 2.0) == 0

    snapshot = core.get_actor_scan_snapshot()
    assert snapshot["snapshot_id"] == 2
    assert snapshot["actors"] == []
    assert snapshot["total_record_count"] == 0
    assert snapshot["complete"] is True
    assert snapshot["has_data"] is True


def test_v1_remains_parse_compatible():
    name = b"Legacy"
    payload = struct.pack(RWVG_ACTOR_SCAN_HEADER_FMT, 1, RWVG_ACTOR_SCAN_VERSION)
    payload += struct.pack("<QIBH", 0x1234, 4, 4, len(name)) + name
    payload += struct.pack("<3f", 1.0, 2.0, 3.0)

    parsed = parse_rwvg_actor_scan_payload(payload)
    assert parsed["version"] == 1
    assert parsed["records"][0]["gname"] == "Legacy"
    assert parsed["records"][0]["pos"] == {"x": 1.0, "y": 2.0, "z": 3.0}
