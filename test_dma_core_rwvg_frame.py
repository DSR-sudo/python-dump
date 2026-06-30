from pathlib import Path
import struct
import sys
import threading


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dma_core import DMACore  # noqa: E402
from rwvg_protocol import (  # noqa: E402
    RWVG_ITEM_FMT,
    RWVG_PLAYER_FMT,
    RWVG_TYPE_ITEM,
    RWVG_TYPE_PLAYER,
    RWVG_TYPE_UTILS,
    RWVG_UTILS_FMT,
)


def make_core() -> DMACore:
    core = DMACore.__new__(DMACore)
    core.rwvg_stream_detected = True
    core.rwvg_stats = {
        "utils_frames": 0,
        "player_frames": 0,
        "item_frames": 0,
        "player_batch_frames": 0,
        "item_batch_frames": 0,
        "player_batch_entities": 0,
        "item_batch_entities": 0,
        "typed_bytes": 0,
    }
    core.radar_lock = threading.Lock()
    core.radar_latest_utils = None
    core.radar_latest_utils_ts = 0.0
    core.radar_players = {}
    core.radar_items = {}
    core.radar_player_ttl_sec = 1.5
    core.trace_lock = threading.Lock()
    core.send_thread_history = []
    core.trace_history_limit = 512
    return core


def utils_payload(local_pos=(0.0, 0.0, 0.0)) -> bytes:
    matrix = [0.0] * 16
    local_neck = [local_pos[0], local_pos[1], local_pos[2] + 55.0]
    local = [local_pos[0], local_pos[1], local_pos[2]]
    return struct.pack(
        RWVG_UTILS_FMT,
        *matrix, 0, 0, 0, 1, *local_neck, *local, 7, 0, 0.0, 0, *([0.0] * 4), 0, 0,
    )


def player_payload(entity_ptr: int) -> bytes:
    return struct.pack(
        RWVG_PLAYER_FMT,
        100.0, 100.0, 0, 0, b"Player", b"", b"", b"p", b"", entity_ptr,
        1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 0.0, 0, 1, 1,
    )


def item_payload(x: float) -> bytes:
    return struct.pack(RWVG_ITEM_FMT, 0, x, 2.0, 3.0, 1, 0, b"Item", b"Proj", 0, 0, 0)


def test_utils_frame_starts_new_current_rwvg_snapshot():
    core = make_core()
    core._handle_rwvg_typed_frame(RWVG_TYPE_PLAYER, player_payload(0x1111))
    core._handle_rwvg_typed_frame(RWVG_TYPE_ITEM, item_payload(10.0))
    assert core.get_radar_snapshot()["meta"]["entity_count"] == 1
    assert core.get_radar_snapshot()["meta"]["item_count"] == 1

    core._handle_rwvg_typed_frame(RWVG_TYPE_UTILS, utils_payload())
    snapshot = core.get_radar_snapshot()

    assert snapshot["meta"]["entity_count"] == 0
    assert snapshot["meta"]["item_count"] == 0


def test_utils_frame_records_printable_local_coordinates():
    core = make_core()
    core._handle_rwvg_typed_frame(RWVG_TYPE_UTILS, utils_payload((10.0, 20.0, 30.0)))

    history = core.get_send_thread_history(limit=4)

    assert history[-1]["kind"] == "local"
    assert history[-1]["entity_id"] == "local"
    assert history[-1]["team_id"] == 1
    assert history[-1]["weapon_id"] == 7
    assert history[-1]["pos"] == {"x": 10.0, "y": 20.0, "z": 30.0}
