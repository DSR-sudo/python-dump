"""v9 Type=6 世界物体缓存：FULL 帧缓存 + players-only 帧回放 + TTL 过期。"""

import contextlib
import os
import struct
import time
import unittest
from unittest.mock import patch

import dma_core
from dma_core import DMACore
from rwvg_actor_snapshot import (
    RWVG_ACTOR_KIND_ITEM,
    RWVG_ACTOR_KIND_PLAYER,
    RWVG_ACTOR_SNAPSHOT_FLAG_FULL,
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    parse_rwvg_actor_scan_payload,
)


POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
ITEM_ID_FIELD = 1 << 9
ITEM_ID = 15050200005  # 蓝室核心


def _bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


def _record(kind: int, x: float, item_id: int = 0, team_id: int = 0,
            valid_fields: int = POSITION_FIELD) -> bytes:
    return struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        kind, 0, 0, 0,
        _bits(x), _bits(0.0), _bits(0.0), 1, 0,
        team_id, _bits(100.0), _bits(100.0), 0, 0, item_id, 0,
        valid_fields, 1, 0, 0,
        0, 0,
    )


def _player(x: float = 10.0, team_id: int = 1) -> bytes:
    return _record(RWVG_ACTOR_KIND_PLAYER, x, team_id=team_id,
                   valid_fields=POSITION_FIELD | TEAM_FIELD)


def _item(x: float = 20.0, item_id: int = ITEM_ID) -> bytes:
    return _record(RWVG_ACTOR_KIND_ITEM, x, item_id=item_id,
                   valid_fields=POSITION_FIELD | ITEM_ID_FIELD)


def _frame(snapshot_id: int, records: list, flags: int = 0, fragment_index: int = 0,
           fragment_count: int = 1, total: int | None = None) -> bytes:
    total = len(records) if total is None else total
    header = struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        len(records), RWVG_ACTOR_SNAPSHOT_VERSION, snapshot_id, fragment_index,
        fragment_count, total, 0, 0, 0, 0, 0, 0, flags,
    )
    return header + b"".join(records)


def _parsed(snapshot_id: int, records: list, **kwargs):
    parsed = parse_rwvg_actor_scan_payload(_frame(snapshot_id, records, **kwargs))
    assert parsed is not None, "test frame must parse under the v9 layout"
    return parsed


class Type6WorldCacheTest(unittest.TestCase):
    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in (("UDP_LISTEN_HOST", "127.0.0.1"), ("BIND_PORT", 0),
                            ("RWBASE_DECRYPT_LOG_ENABLED", False)):
            self.patches.enter_context(patch.object(dma_core, name, value))
        self.patches.enter_context(patch.object(
            DMACore, "_heartbeat_loop", lambda core: core.stop_event.wait()))
        self.core = self._new_core()

    def _new_core(self) -> DMACore:
        core = DMACore()
        self.addCleanup(core.shutdown)
        return core

    def accept(self, parsed: dict, now_ts: float | None = None) -> int:
        return self.core._accept_actor_snapshot(
            parsed, time.monotonic() if now_ts is None else now_ts)

    def test_full_frame_fills_items_then_players_only_frame_reuses_them(self):
        full = _parsed(1, [_player(), _item(20.0), _item(30.0)],
                       flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL)
        self.assertEqual(self.accept(full), 3)

        snapshot = self.core.get_actor_scan_snapshot()
        self.assertTrue(snapshot["has_full_world"])
        self.assertEqual(snapshot["flags"], RWVG_ACTOR_SNAPSHOT_FLAG_FULL)
        self.assertEqual(snapshot["count"], 3)
        self.assertEqual(snapshot["world_items_cached"], 2)
        self.assertEqual(snapshot["world_items_merged"], 0)

        radar = self.core.get_radar_snapshot()
        self.assertEqual(len(radar["items"]), 2)
        full_item_ids = [item["id"] for item in radar["items"]]

        players_only = _parsed(2, [_player(11.0)])
        self.assertEqual(self.accept(players_only), 1)

        snapshot = self.core.get_actor_scan_snapshot()
        self.assertFalse(snapshot["has_full_world"])
        self.assertEqual(snapshot["flags"], 0)
        self.assertEqual([actor["kind_name"] for actor in snapshot["actors"]],
                         ["Player", "Item", "Item"])
        self.assertEqual(snapshot["world_items_merged"], 2)

        # 回放缓存不改变 /api/data 的对外结构。
        radar = self.core.get_radar_snapshot()
        self.assertEqual(sorted(radar), ["entities", "items", "local_player", "meta", "teammates"])
        self.assertEqual(sorted(radar["meta"]), [
            "entity_count", "item_count", "snapshot_complete", "snapshot_id",
            "snapshot_status", "source", "type6_view_present", "utils_age_ms",
            "utils_present",
        ])
        self.assertEqual(radar["meta"]["source"], "actor_snapshot_v9")
        self.assertEqual(len(radar["entities"]), 1)
        self.assertEqual([item["id"] for item in radar["items"]], full_item_ids)
        self.assertEqual([item["item_name"] for item in radar["items"]],
                         ["蓝室核心", "蓝室核心"])
        self.assertEqual([item["position"]["x"] for item in radar["items"]], [20, 30])

    def test_players_only_frame_without_a_full_frame_has_no_items(self):
        self.accept(_parsed(1, [_player()]))

        snapshot = self.core.get_actor_scan_snapshot()
        self.assertEqual(snapshot["count"], 1)
        self.assertFalse(snapshot["has_full_world"])
        self.assertEqual(snapshot["world_items_cached"], 0)
        self.assertEqual(self.core.get_radar_snapshot()["items"], [])

    def test_incomplete_full_snapshot_does_not_touch_the_cache(self):
        # FULL 快照的第 0 片段：尚未集齐，不建缓存。
        self.accept(_parsed(1, [_item(20.0)], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL,
                           fragment_index=0, fragment_count=2, total=2))
        snapshot = self.core.get_actor_scan_snapshot()
        self.assertFalse(snapshot["complete"])
        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["world_items_cached"], 0)
        self.assertEqual(snapshot["world_items_merged"], 0)
        # 该片段自带的物品照常显示，但它不是缓存回放（缓存仍为空）。
        self.assertEqual([item["position"]["x"] for item in self.core.get_radar_snapshot()["items"]],
                         [20])

        # 集齐第 1 片段后缓存才建立，players-only 帧即可回放。
        self.accept(_parsed(1, [_item(30.0)], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL,
                           fragment_index=1, fragment_count=2, total=2))
        snapshot = self.core.get_actor_scan_snapshot()
        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["world_items_cached"], 2)

        self.accept(_parsed(2, [_player()]))
        self.assertEqual(len(self.core.get_radar_snapshot()["items"]), 2)

    def test_full_frame_without_world_items_replaces_the_cache(self):
        self.accept(_parsed(1, [_player(), _item()], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        self.assertEqual(len(self.core.get_radar_snapshot()["items"]), 1)

        self.accept(_parsed(2, [_player(11.0)], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        snapshot = self.core.get_actor_scan_snapshot()
        self.assertEqual(snapshot["world_items_cached"], 0)
        self.assertEqual(self.core.get_radar_snapshot()["items"], [])

    def test_cache_age_counts_from_the_full_frame_and_expires(self):
        base = time.monotonic() - 1.0  # FULL 帧已经过去 1 秒
        self.accept(_parsed(1, [_player(), _item()], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL), base)
        self.accept(_parsed(2, [_player(11.0)]), base + 0.5)

        snapshot = self.core.get_actor_scan_snapshot()
        self.assertEqual(snapshot["world_items_merged"], 1)
        # players-only 帧不刷新缓存时间戳，age 仍从 FULL 帧起算（>1000 ms 而非 ~500 ms）。
        self.assertGreaterEqual(snapshot["world_items_age_ms"], 1000)

        # 注入时间：缓存已超过默认 6000 ms TTL，立即停止合并并丢弃。
        self.core.actor_scan_world_items_ts -= 7.0
        snapshot = self.core.get_actor_scan_snapshot()
        self.assertEqual(snapshot["world_items_cached"], 0)
        self.assertEqual(snapshot["world_items_merged"], 0)
        self.assertEqual(snapshot["world_items_age_ms"], -1)
        self.assertEqual(self.core.get_radar_snapshot()["items"], [])

        # 已丢弃的缓存不会复活；下一帧 FULL 才重新填充。
        self.accept(_parsed(3, [_player(12.0)]))
        self.assertEqual(self.core.get_radar_snapshot()["items"], [])
        self.accept(_parsed(4, [_player(13.0), _item(40.0)],
                            flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        self.assertEqual(len(self.core.get_radar_snapshot()["items"]), 1)

    def test_actor_items_ttl_env_override(self):
        self.assertEqual(dma_core.DEFAULT_ACTOR_ITEMS_TTL_MS,
                         float(os.getenv("DMA_ACTOR_ITEMS_TTL_MS", "6000")))

        with patch.object(dma_core, "DEFAULT_ACTOR_ITEMS_TTL_MS", 25.0):
            core = self._new_core()
        self.assertEqual(core.actor_scan_world_items_ttl_ms, 25.0)

        base = time.monotonic() - 0.2  # 200 ms > 25 ms 的自定义 TTL
        core._accept_actor_snapshot(
            _parsed(1, [_player(), _item()], flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL), base)
        core._accept_actor_snapshot(_parsed(2, [_player(11.0)]), time.monotonic())
        self.assertEqual(core.get_radar_snapshot()["items"], [])


if __name__ == "__main__":
    unittest.main()
