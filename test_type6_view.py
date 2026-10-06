import struct
import unittest

from actor_snapshot_radar import build_radar_snapshot
from id_catalog import hero_name, weapon_name
from item_descriptor import effective_item_quality
from rwvg_actor_snapshot import (
    RWVG_ACTOR_KIND_CONTAINER,
    RWVG_ACTOR_KIND_ITEM,
    RWVG_ACTOR_SNAPSHOT_FLAG_FULL,
    RWVG_ACTOR_SNAPSHOT_HAS_DIRECTION,
    RWVG_ACTOR_SNAPSHOT_HAS_PASSWORD,
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_HEADER_SIZE,
    RWVG_ACTOR_SNAPSHOT_PREFIX_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    parse_rwvg_actor_scan_payload,
)


# v8 历史头部布局（48 字节，无 flags），只用于版本拒绝性回归。
RWVG_ACTOR_SNAPSHOT_V8_HEADER_FMT = "<HHIIIIQIIIIi"
LOCAL_PAWN = 0x123456789ABCDEF0
ITEM_ID = 15050200005
POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
HEALTH_FIELD = 1 << 5
MAX_HEALTH_FIELD = 1 << 6
WEAPON_FIELD = 1 << 7
HERO_FIELD = 1 << 8
ITEM_ID_FIELD = 1 << 9
YAW_BITS = struct.unpack("<I", struct.pack("<f", 135.5))[0]
DIRECTION_DEGREES = 76.5
CONTAINER_PASSWORD = 314159


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _build_type6_frame(
    item_quality: int = 0,
    item_kind: int = RWVG_ACTOR_KIND_ITEM,
    direction_degrees: float | None = None,
    password: int | None = None,
    flags: int = 0,
    include_item: bool = True,
) -> bytes:
    player_valid_fields = (
        POSITION_FIELD | TEAM_FIELD | HEALTH_FIELD | MAX_HEALTH_FIELD | WEAPON_FIELD | HERO_FIELD
    )
    direction_bits = 0
    if direction_degrees is not None:
        player_valid_fields |= RWVG_ACTOR_SNAPSHOT_HAS_DIRECTION
        direction_bits = _float_bits(direction_degrees)
    record = struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        1, 0, 0, 0,
        _float_bits(100.0), _float_bits(200.0), _float_bits(300.0), 1, 0,
        4, _float_bits(100.0), _float_bits(100.0), 0, 0x1122334455667788, 0,
        0,
        player_valid_fields,
        1, 0, 0,
        direction_bits, 0,
    )
    item_valid_fields = POSITION_FIELD | ITEM_ID_FIELD | (1 << 10)
    if password is not None:
        item_valid_fields |= RWVG_ACTOR_SNAPSHOT_HAS_PASSWORD
    item_record = struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        item_kind, 0, 0, 0,
        _float_bits(150.0), _float_bits(250.0), _float_bits(350.0), 5, 0,
        0, 0, 0, 0, 0, ITEM_ID,
        item_quality,
        item_valid_fields,
        1, 0, 0,
        0, int(password or 0),
    )
    record_count = 2 if include_item else 1
    header = struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        record_count, RWVG_ACTOR_SNAPSHOT_VERSION, 7, 0, 1, record_count,
        LOCAL_PAWN, YAW_BITS, 3, 3, 0, 0, flags,
    )
    records = record + item_record if include_item else record
    return header + records


class Type6ViewTest(unittest.TestCase):
    def test_item_id_suffix_decodes_equipment_quality(self):
        self.assertEqual(effective_item_quality(11010003004), 3)
        self.assertEqual(effective_item_quality(11080006004), 6)
        self.assertEqual(effective_item_quality(15050200005), 0)

    def test_explicit_quality_and_forced_red_override_item_id_grade(self):
        self.assertEqual(effective_item_quality(11010003004, 5), 5)
        self.assertEqual(effective_item_quality(18010000016, 1), 6)

    def test_id_catalog_distinguishes_known_and_unknown_values(self):
        self.assertEqual(hero_name(88000000030), "红狼")
        self.assertEqual(weapon_name(51878), "腾龙")
        # 0x612E / 0x581 / 0x610C / 0x24A1：现场实测补充的武器 ID
        self.assertEqual(weapon_name(0x612E), "汤姆逊")
        self.assertEqual(weapon_name(0x581), "复合弓")
        self.assertEqual(weapon_name(0x610C), "MK4")
        self.assertEqual(weapon_name(0x24A1), "SVCH")
        self.assertEqual(hero_name(123), "未知探员(123)")
        self.assertEqual(weapon_name(123), "未知武器(123)")

    def test_type6_view_maps_to_the_local_actor_only(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame())

        self.assertIsNotNone(parsed)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_VERSION, 9)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_HEADER_SIZE, 52)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE, 104)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_FLAG_FULL, 1)
        self.assertEqual(parsed["local_view"]["local_pawn"], LOCAL_PAWN)
        self.assertEqual(parsed["local_view"]["yaw"], 135.5)
        self.assertEqual(parsed["records"][0]["hero_id"], 0x1122334455667788)
        self.assertEqual(parsed["records"][0]["hero_id_hex"], "0x1122334455667788")
        self.assertEqual(parsed["records"][0]["hero_name"], "未知探员(1234605616436508552)")
        self.assertEqual(parsed["records"][0]["weapon_name"], "未知武器(0)")
        self.assertEqual(parsed["records"][0]["item_id"], 0)
        self.assertEqual(parsed["records"][0]["item_name"], "")
        for field in ("entity", "actor_address", "object_id", "gname", "class_name"):
            self.assertNotIn(field, parsed["records"][0])

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertIsNone(radar["local_player"])
        self.assertIsNone(radar["entities"][0]["orientation"])
        self.assertFalse(radar["entities"][0]["has_orientation"])
        self.assertEqual(radar["entities"][0]["hero_id"], 0x1122334455667788)
        self.assertEqual(radar["entities"][0]["hero_id_hex"], "0x1122334455667788")
        self.assertEqual(radar["entities"][0]["hero_name"], "未知探员(1234605616436508552)")
        self.assertEqual(radar["entities"][0]["weapon_name"], "未知武器(0)")
        self.assertEqual(radar["entities"][0]["weapon_id"], 0)
        self.assertEqual(radar["entities"][0]["health"], 100.0)
        self.assertEqual(radar["entities"][0]["max_health"], 100.0)

    def test_type6_resolves_item_names_from_the_item_id(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame())

        self.assertIsNotNone(parsed)
        item = parsed["records"][1]
        self.assertEqual(item["kind_name"], "Item")
        self.assertEqual(item["item_id"], ITEM_ID)
        self.assertEqual(item["item_quality"], 0)
        self.assertEqual(item["item_id_hex"], f"0x{ITEM_ID:X}")
        self.assertEqual(item["item_name"], "蓝室核心")

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertEqual(len(radar["items"]), 1)
        self.assertEqual(radar["items"][0]["item_id"], ITEM_ID)
        self.assertEqual(radar["items"][0]["item_id_hex"], f"0x{ITEM_ID:X}")
        self.assertEqual(radar["items"][0]["item_name"], "蓝室核心")
        self.assertEqual(radar["items"][0]["id"], "record:1")
        self.assertEqual(radar["items"][0]["password"], 0)
        self.assertFalse(radar["items"][0]["has_password"])

    def test_type6_item_exposes_quality_display_fields(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame())

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        item = radar["items"][0]
        self.assertEqual(item["item_quality"], 0)
        self.assertEqual(item["item_quality_label"], "None")
        self.assertEqual(item["item_quality_color"], "#c8c8c8")

    def test_type6_prefers_quality_read_from_item_component(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame(item_quality=4))
        snapshot = {
            "actors": [parsed["records"][1]],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }

        item = build_radar_snapshot(snapshot, None)["items"][0]

        self.assertEqual(item["item_quality"], 4)
        self.assertEqual(item["item_quality_label"], "Purple")

    def test_type6_keeps_items_from_the_legacy_item_stream(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame())
        legacy_item = {
            "id": "item:legacy",
            "item_name": "Legacy item",
            "position": {"x": 400, "y": 500, "z": 0},
        }

        radar = build_radar_snapshot(
            {"actors": parsed["records"], "local_view": parsed["local_view"]},
            None,
            [legacy_item],
        )

        self.assertEqual([item["id"] for item in radar["items"]], [
            "record:1",
            "item:legacy",
        ])

    def test_type6_maps_the_direction_bit_to_the_entity_orientation(self):
        parsed = parse_rwvg_actor_scan_payload(
            _build_type6_frame(direction_degrees=DIRECTION_DEGREES))

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["records"][0]["direction_bits"], _float_bits(DIRECTION_DEGREES))
        self.assertEqual(parsed["records"][0]["direction"], DIRECTION_DEGREES)
        self.assertEqual(parsed["records"][0]["password"], 0)

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertEqual(radar["entities"][0]["orientation"], DIRECTION_DEGREES)
        self.assertTrue(radar["entities"][0]["has_orientation"])

    def test_type6_maps_the_password_bit_to_container_items(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame(
            item_kind=RWVG_ACTOR_KIND_CONTAINER, password=CONTAINER_PASSWORD))

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["records"][1]["password"], CONTAINER_PASSWORD)
        self.assertEqual(parsed["records"][1]["direction"], 0.0)

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertEqual(radar["items"][0]["source_kind"], "Container")
        self.assertEqual(radar["items"][0]["password"], CONTAINER_PASSWORD)
        self.assertTrue(radar["items"][0]["has_password"])

    def test_type6_parses_the_flags_field_into_world_completeness(self):
        players_only = parse_rwvg_actor_scan_payload(_build_type6_frame(flags=0))

        self.assertIsNotNone(players_only)
        self.assertEqual(players_only["flags"], 0)
        self.assertFalse(players_only["has_full_world"])

        full = parse_rwvg_actor_scan_payload(
            _build_type6_frame(flags=RWVG_ACTOR_SNAPSHOT_FLAG_FULL))

        self.assertIsNotNone(full)
        self.assertEqual(full["flags"], RWVG_ACTOR_SNAPSHOT_FLAG_FULL)
        self.assertTrue(full["has_full_world"])
        self.assertEqual(len(full["records"]), 2)

    def test_type6_keeps_unknown_flag_bits_without_treating_them_as_full(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame(flags=0x8000_0000))

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["flags"], 0x8000_0000)
        self.assertFalse(parsed["has_full_world"])

    def test_type6_players_only_frame_parses_without_world_items(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame(include_item=False))

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["record_count"], 1)
        self.assertEqual([record["kind_name"] for record in parsed["records"]], ["Player"])

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)
        self.assertEqual(radar["items"], [])

    def test_type6_rejects_a_non_current_protocol_version(self):
        # v8 帧：48 字节头部（无 flags）+ 原有 104 字节记录体，整体拒收。
        v8_frame = struct.pack(
            RWVG_ACTOR_SNAPSHOT_V8_HEADER_FMT,
            2, 8, 7, 0, 1, 2,
            LOCAL_PAWN, YAW_BITS, 3, 3, 0, 0,
        ) + _build_type6_frame()[RWVG_ACTOR_SNAPSHOT_HEADER_SIZE:]
        self.assertEqual(len(v8_frame), 48 + 2 * RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE)
        self.assertIsNone(parse_rwvg_actor_scan_payload(v8_frame))

        # 旧版（v6 时代）前缀同样拒收：版本校验不做兼容分支。
        self.assertIsNone(parse_rwvg_actor_scan_payload(
            struct.pack(RWVG_ACTOR_SNAPSHOT_PREFIX_FMT, 0, 6)))

    def test_type6_rejects_a_v9_frame_missing_the_flags_field(self):
        # v9 头部被 4 字节 flags 拉长：裁掉 flags 后长度不再匹配，无法误解析。
        v9_frame = _build_type6_frame(include_item=False)
        truncated = v9_frame[:RWVG_ACTOR_SNAPSHOT_HEADER_SIZE - 4] + v9_frame[RWVG_ACTOR_SNAPSHOT_HEADER_SIZE:]
        self.assertIsNone(parse_rwvg_actor_scan_payload(truncated))


if __name__ == "__main__":
    unittest.main()
