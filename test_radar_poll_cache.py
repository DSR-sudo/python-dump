"""60Hz 轮询的服务端契约：廉价版本号 + /api/data 内容缓存与 204。

覆盖两层：
1. DMACore.get_radar_data_version()：只由真实变化驱动（帧、世界物体 TTL 过期、
   utils），旧 RWVG 回退路径必须返回 None 交给调用方重建。
2. WebRadarService /api/data：版本未变时不重新构建、内容未变时回 204、数据变化或
   nocache 时返回新负载。
"""

import contextlib
import http.client
import socket
import struct
import tempfile
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
from web_radar import WebRadarService

POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
ITEM_ID_FIELD = 1 << 9


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


def _frame(snapshot_id: int, records: list, flags: int = 0) -> bytes:
    header = struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        len(records), RWVG_ACTOR_SNAPSHOT_VERSION, snapshot_id, 0,
        1, len(records), 0, 0, 0, 0, 0, 0, flags,
    )
    return header + b"".join(records)


def _parsed(snapshot_id: int, records: list, flags: int = 0) -> dict:
    parsed = parse_rwvg_actor_scan_payload(_frame(snapshot_id, records, flags))
    assert parsed is not None, "test frame must parse under the v9 layout"
    return parsed


def _player(x: float = 10.0, team_id: int = 1) -> bytes:
    return _record(RWVG_ACTOR_KIND_PLAYER, x, team_id=team_id,
                   valid_fields=POSITION_FIELD | TEAM_FIELD)


def _item(x: float = 20.0, item_id: int = 15050200005) -> bytes:
    return _record(RWVG_ACTOR_KIND_ITEM, x, item_id=item_id,
                   valid_fields=POSITION_FIELD | ITEM_ID_FIELD)


class RadarDataVersionTest(unittest.TestCase):
    """版本号必须覆盖所有会改变 /api/data 负载的状态。"""

    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in (("UDP_LISTEN_HOST", "127.0.0.1"), ("BIND_PORT", 0),
                            ("RWBASE_DECRYPT_LOG_ENABLED", False)):
            self.patches.enter_context(patch.object(dma_core, name, value))
        self.patches.enter_context(patch.object(
            DMACore, "_heartbeat_loop", lambda core: core.stop_event.wait()))
        self.core = DMACore()
        self.addCleanup(self.core.shutdown)

    def accept(self, parsed: dict, now_ts: float | None = None) -> int:
        return self.core._accept_actor_snapshot(
            parsed, time.monotonic() if now_ts is None else now_ts)

    def test_legacy_fallback_has_no_cheap_version(self):
        # 尚未收到任何 Type=6 快照时走旧 RWVG 回退路径，负载随时钟变化，必须返回 None。
        self.assertIsNone(self.core.get_radar_data_version())

        self.accept(_parsed(1, [_player()]))
        self.assertIsNotNone(self.core.get_radar_data_version())

    def test_legacy_radar_caches_force_a_rebuild(self):
        self.accept(_parsed(1, [_player()], RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        self.assertIsNotNone(self.core.get_radar_data_version())

        # 旧 RWVG 玩家/物资缓存有逐条 TTL，版本号无法廉价判定。
        self.core.radar_items["legacy"] = {"_entity_id": "legacy"}
        self.assertIsNone(self.core.get_radar_data_version())

    def test_version_is_stable_without_changes(self):
        self.accept(_parsed(1, [_player()], RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        first = self.core.get_radar_data_version()
        self.assertEqual(first, self.core.get_radar_data_version())

    def test_version_tracks_frames_and_players(self):
        self.accept(_parsed(1, [_player()], RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        first = self.core.get_radar_data_version()

        self.accept(_parsed(2, [_player(11.0)], RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        second = self.core.get_radar_data_version()
        self.assertNotEqual(first, second)

    def test_version_changes_when_world_items_expire(self):
        self.core.actor_scan_world_items_ttl_ms = 50.0
        self.accept(_parsed(1, [_player(), _item()], RWVG_ACTOR_SNAPSHOT_FLAG_FULL))
        with_items = self.core.get_radar_data_version()
        self.assertIsNotNone(with_items)
        self.assertEqual(self.core.get_actor_scan_snapshot()["world_items_cached"], 1)

        # TTL 过期后缓存被丢弃，负载随之变化，版本号必须跟着变。
        time.sleep(0.06)
        self.assertNotEqual(self.core.get_radar_data_version(), with_items)
        self.assertEqual(self.core.get_actor_scan_snapshot()["world_items_cached"], 0)


class _FakeCore:
    """只实现 /api/data 需要的两个接口，用于验证服务端的缓存与 204 契约。"""

    def __init__(self):
        self.version = 1
        self.builds = 0

    def get_radar_data_version(self):
        return self.version

    def get_radar_snapshot(self):
        self.builds += 1
        return {
            "meta": {"snapshot_id": self.version},
            "local_player": None,
            "entities": [{"id": "e1", "position": {"x": self.version, "y": 0}}],
            "items": [],
            "teammates": [],
        }


class _LegacyCore(_FakeCore):
    """没有版本号接口的旧 core：必须每轮重建，不能吃缓存。"""

    get_radar_data_version = None


class RadarPollCacheHttpTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.core = _FakeCore()
        self.service = WebRadarService(self.core, default_port=1, root_dir=self.root.name)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        started, message = self.service.start(port_override=port)
        self.assertTrue(started, message)
        self.addCleanup(self.service.stop)
        self.port = port

    def request(self, headers=None, path="/api/data"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def auth(self, **extra):
        return {"X-Auth-Token": self.service.password, **extra}

    def test_unchanged_payload_is_not_rebuilt_or_resent(self):
        status, headers, body = self.request(self.auth())
        self.assertEqual(status, 200)
        token = headers["X-Data-Token"]
        self.assertIn(b'"snapshot_id": 1', body)
        self.assertEqual(self.core.builds, 1)

        # 60Hz 轮询的常态：版本未变，不再构建，也不重复传输。
        for _ in range(5):
            status, headers, body = self.request(self.auth(**{"X-Data-Token": token}))
            self.assertEqual(status, 204)
            self.assertEqual(body, b"")
            self.assertEqual(headers["X-Data-Token"], token)
        self.assertEqual(self.core.builds, 1)

        # 版本号变化后必须重新构建并返回新负载与新 token。
        self.core.version = 2
        status, headers, body = self.request(self.auth(**{"X-Data-Token": token}))
        self.assertEqual(status, 200)
        self.assertIn(b'"snapshot_id": 2', body)
        self.assertNotEqual(headers["X-Data-Token"], token)
        self.assertEqual(self.core.builds, 2)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")

    def test_frontend_without_token_gets_the_cached_payload(self):
        self.request(self.auth())
        status, _, body = self.request(self.auth())
        self.assertEqual(status, 200)
        self.assertIn(b'"snapshot_id": 1', body)
        self.assertEqual(self.core.builds, 1)

    def test_nocache_forces_a_rebuild_and_always_returns_a_body(self):
        first = self.request(self.auth())[1]["X-Data-Token"]
        status, headers, body = self.request(self.auth(**{
            "X-Data-No-Cache": "1", "X-Data-Token": first}))
        self.assertEqual(status, 200)
        self.assertIn(b'"snapshot_id": 1', body)
        self.assertEqual(headers["X-Data-Token"], first)
        self.assertEqual(self.core.builds, 2)

    def test_legacy_core_rebuilds_every_poll(self):
        core = _LegacyCore()
        service = WebRadarService(core, default_port=1, root_dir=self.root.name)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        started, message = service.start(port_override=port)
        self.assertTrue(started, message)
        self.addCleanup(service.stop)
        self.service, self.port = service, port

        self.request(self.auth())
        self.request(self.auth())
        self.assertEqual(core.builds, 2)

    def test_unauthorized_still_rejected(self):
        status, _, _ = self.request({})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
