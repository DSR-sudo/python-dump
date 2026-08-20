import struct
import unittest

from actor_snapshot_radar import build_radar_snapshot
from id_catalog import hero_name, weapon_name
from rwvg_actor_snapshot import (
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_HEADER_SIZE,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    parse_rwvg_actor_scan_payload,
)


ACTOR_ADDRESS = 0x123456789ABCDEF0
ITEM_ADDRESS = 0x23456789ABCDEF01
ITEM_ID = 15050200005
POSITION_FIELD = 1 << 3
TEAM_FIELD = 1 << 4
HEALTH_FIELD = 1 << 5
MAX_HEALTH_FIELD = 1 << 6
WEAPON_FIELD = 1 << 7
HERO_FIELD = 1 << 8
ITEM_ID_FIELD = 1 << 9
YAW_BITS = struct.unpack("<I", struct.pack("<f", 135.5))[0]


def _float_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _build_type6_frame() -> bytes:
    class_name = b"BP_DFMCharacter_C"
    item_class_name = b"BP_InventoryPickup_C"
    record = struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        ACTOR_ADDRESS, 17, 1, len(class_name), 0, 0, 0,
        _float_bits(100.0), _float_bits(200.0), _float_bits(300.0), 1, 0,
        4, _float_bits(100.0), _float_bits(100.0), 0, 0x1122334455667788, 0,
        POSITION_FIELD | TEAM_FIELD | HEALTH_FIELD | MAX_HEALTH_FIELD | WEAPON_FIELD | HERO_FIELD,
        1, 0, 0,
    ) + class_name
    item_record = struct.pack(
        RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
        ITEM_ADDRESS, 18, 4, len(item_class_name), 0, 0, 0,
        _float_bits(150.0), _float_bits(250.0), _float_bits(350.0), 5, 0,
        0, 0, 0, 0, 0, ITEM_ID,
        POSITION_FIELD | ITEM_ID_FIELD,
        1, 0, 0,
    ) + item_class_name
    header = struct.pack(
        RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
        2, RWVG_ACTOR_SNAPSHOT_VERSION, 7, 0, 1, 2,
        ACTOR_ADDRESS, YAW_BITS, 3, 3, 0, 0,
    )
    return header + record + item_record


class Type6ViewTest(unittest.TestCase):
    def test_id_catalog_distinguishes_known_and_unknown_values(self):
        self.assertEqual(hero_name(88000000030), "红狼")
        self.assertEqual(weapon_name(51878), "腾龙")
        self.assertEqual(hero_name(123), "未知探员(123)")
        self.assertEqual(weapon_name(123), "未知武器(123)")

    def test_type6_view_maps_to_the_local_actor_only(self):
        parsed = parse_rwvg_actor_scan_payload(_build_type6_frame())

        self.assertIsNotNone(parsed)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_HEADER_SIZE, 48)
        self.assertEqual(RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE, 105)
        self.assertEqual(parsed["local_view"]["local_pawn"], ACTOR_ADDRESS)
        self.assertEqual(parsed["local_view"]["yaw"], 135.5)
        self.assertEqual(parsed["records"][0]["hero_id"], 0x1122334455667788)
        self.assertEqual(parsed["records"][0]["hero_id_hex"], "0x1122334455667788")
        self.assertEqual(parsed["records"][0]["hero_name"], "未知探员(1234605616436508552)")
        self.assertEqual(parsed["records"][0]["weapon_name"], "未知武器(0)")
        self.assertEqual(parsed["records"][0]["item_id"], 0)
        self.assertEqual(parsed["records"][0]["item_name"], "")

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertEqual(radar["local_player"]["id"], f"0x{ACTOR_ADDRESS:X}")
        self.assertEqual(radar["local_player"]["yaw"], 135.5)
        self.assertTrue(radar["entities"][0]["has_orientation"])
        self.assertEqual(radar["entities"][0]["orientation"], 135.5)
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
        self.assertEqual(item["item_name"], "蓝室核心")

        radar = build_radar_snapshot({
            "actors": parsed["records"],
            "local_view": parsed["local_view"],
            "version": parsed["version"],
        }, None)

        self.assertEqual(len(radar["items"]), 1)
        self.assertEqual(radar["items"][0]["item_id"], ITEM_ID)
        self.assertEqual(radar["items"][0]["item_name"], "蓝室核心")
        self.assertEqual(radar["items"][0]["id"], f"0x{ITEM_ADDRESS:X}")

    def test_type6_rejects_a_non_current_protocol_version(self):
        self.assertIsNone(parse_rwvg_actor_scan_payload(struct.pack("<HH", 0, 2)))


if __name__ == "__main__":
    unittest.main()
