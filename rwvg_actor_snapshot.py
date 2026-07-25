"""RWVG Type=6 actor snapshot protocol versions."""

import struct


RWVG_ACTOR_KIND_UNKNOWN = 0
RWVG_ACTOR_KIND_PLAYER = 1
RWVG_ACTOR_KIND_MINION = 2
RWVG_ACTOR_KIND_BOSS = 3
RWVG_ACTOR_KIND_ITEM = 4
RWVG_ACTOR_KIND_CONTAINER = 5
RWVG_ACTOR_KIND_DEADBOX = 6
RWVG_ACTOR_KIND_BOX = 7
RWVG_ACTOR_KIND_AI = 8

RWVG_ACTOR_KIND_NAMES = {
    RWVG_ACTOR_KIND_UNKNOWN: "Unknown",
    RWVG_ACTOR_KIND_PLAYER: "Player",
    RWVG_ACTOR_KIND_MINION: "Minion",
    RWVG_ACTOR_KIND_BOSS: "Boss",
    RWVG_ACTOR_KIND_ITEM: "Item",
    RWVG_ACTOR_KIND_CONTAINER: "Container",
    RWVG_ACTOR_KIND_DEADBOX: "DeadBox",
    RWVG_ACTOR_KIND_BOX: "Box",
    RWVG_ACTOR_KIND_AI: "AI",
}

RWVG_ACTOR_SCAN_VERSION = 1
RWVG_ACTOR_SCAN_HEADER_FMT = "<HH"
RWVG_ACTOR_SCAN_HEADER_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_HEADER_FMT)
RWVG_ACTOR_SCAN_RECORD_FIXED_FMT = "<QIBH"
RWVG_ACTOR_SCAN_RECORD_FIXED_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_RECORD_FIXED_FMT)
RWVG_ACTOR_SCAN_POS_FMT = "<3f"
RWVG_ACTOR_SCAN_POS_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_POS_FMT)
RWVG_ACTOR_SCAN_RECORD_MIN_SIZE = RWVG_ACTOR_SCAN_RECORD_FIXED_SIZE + RWVG_ACTOR_SCAN_POS_SIZE
RWVG_ACTOR_SCAN_MAX_RECORDS = 2000
RWVG_ACTOR_SCAN_MAX_GNAME_BYTES = 512

RWVG_ACTOR_SNAPSHOT_VERSION = 2
RWVG_ACTOR_SNAPSHOT_HEADER_FMT = "<HHIIII"
RWVG_ACTOR_SNAPSHOT_HEADER_SIZE = struct.calcsize(RWVG_ACTOR_SNAPSHOT_HEADER_FMT)
RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT = "<QIBBQQQIIIBQiIIHIIIi"
RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE = struct.calcsize(RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT)
RWVG_ACTOR_SNAPSHOT_MAX_CLASS_NAME_BYTES = 64


def _float_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _kind_name(kind: int) -> str:
    return RWVG_ACTOR_KIND_NAMES.get(int(kind), "Unknown")


def _parse_v1(payload: bytes, record_count: int):
    if record_count > RWVG_ACTOR_SCAN_MAX_RECORDS:
        return None

    offset = RWVG_ACTOR_SCAN_HEADER_SIZE
    records = []
    for _ in range(record_count):
        if offset + RWVG_ACTOR_SCAN_RECORD_MIN_SIZE > len(payload):
            return None
        entity, object_id, kind, gname_len = struct.unpack_from(
            RWVG_ACTOR_SCAN_RECORD_FIXED_FMT, payload, offset
        )
        offset += RWVG_ACTOR_SCAN_RECORD_FIXED_SIZE
        if gname_len > RWVG_ACTOR_SCAN_MAX_GNAME_BYTES:
            return None
        if offset + gname_len + RWVG_ACTOR_SCAN_POS_SIZE > len(payload):
            return None
        gname = payload[offset:offset + gname_len].decode("utf-8", errors="ignore")
        offset += gname_len
        pos_x, pos_y, pos_z = struct.unpack_from(RWVG_ACTOR_SCAN_POS_FMT, payload, offset)
        offset += RWVG_ACTOR_SCAN_POS_SIZE
        records.append({
            "entity": int(entity), "actor_address": int(entity), "object_id": int(object_id),
            "kind": int(kind), "kind_name": _kind_name(kind), "gname": gname,
            "class_name": gname, "pos": {"x": pos_x, "y": pos_y, "z": pos_z},
        })
    if offset != len(payload):
        return None
    return {"records": records, "record_count": record_count, "version": RWVG_ACTOR_SCAN_VERSION}


def _parse_v2_record(payload: bytes, offset: int):
    end = offset + RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE
    if end > len(payload):
        return None
    values = struct.unpack_from(RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT, payload, offset)
    class_name_len = values[3]
    if class_name_len > RWVG_ACTOR_SNAPSHOT_MAX_CLASS_NAME_BYTES:
        return None
    class_name_end = end + class_name_len
    if class_name_end > len(payload):
        return None
    class_name = payload[end:class_name_end].decode("utf-8", errors="ignore")
    (actor_address, object_id, kind, _, mesh, root_component, player_state,
     pos_x, pos_y, pos_z, position_source, last_db_position_tsc, team_id,
     health_bits, max_health_bits, weapon_id, valid_fields, attempts, failures,
     first_failure) = values
    record = {
        "entity": int(actor_address), "actor_address": int(actor_address),
        "actor_address_hex": f"0x{actor_address:X}",
        "object_id": int(object_id), "kind": int(kind), "kind_name": _kind_name(kind),
        "gname": class_name, "class_name": class_name, "mesh": int(mesh),
        "root_component": int(root_component), "player_state": int(player_state),
        "mesh_hex": f"0x{mesh:X}", "root_component_hex": f"0x{root_component:X}",
        "player_state_hex": f"0x{player_state:X}",
        "pos": {"x": _float_from_bits(pos_x), "y": _float_from_bits(pos_y), "z": _float_from_bits(pos_z)},
        "position_bits": {"x": int(pos_x), "y": int(pos_y), "z": int(pos_z)},
        "position_source": int(position_source), "last_db_position_tsc": int(last_db_position_tsc),
        "team_id": int(team_id), "health_bits": int(health_bits),
        "max_health_bits": int(max_health_bits), "health": _float_from_bits(health_bits),
        "max_health": _float_from_bits(max_health_bits), "weapon_id": int(weapon_id),
        "valid_fields": int(valid_fields),
        "diagnostics": {"attempts": int(attempts), "failures": int(failures), "first_failure": int(first_failure)},
    }
    return record, class_name_end


def _parse_v2(payload: bytes, record_count: int):
    if len(payload) < RWVG_ACTOR_SNAPSHOT_HEADER_SIZE:
        return None
    (_, version, snapshot_id, fragment_index, fragment_count,
     total_record_count) = struct.unpack_from(RWVG_ACTOR_SNAPSHOT_HEADER_FMT, payload, 0)
    if version != RWVG_ACTOR_SNAPSHOT_VERSION or fragment_count == 0 or fragment_index >= fragment_count:
        return None
    if total_record_count == 0 and (record_count != 0 or fragment_count != 1):
        return None
    offset = RWVG_ACTOR_SNAPSHOT_HEADER_SIZE
    records = []
    for _ in range(record_count):
        parsed = _parse_v2_record(payload, offset)
        if parsed is None:
            return None
        record, offset = parsed
        records.append(record)
    if offset != len(payload):
        return None
    return {
        "records": records, "record_count": int(record_count), "version": int(version),
        "snapshot_id": int(snapshot_id), "fragment_index": int(fragment_index),
        "fragment_count": int(fragment_count), "total_record_count": int(total_record_count),
    }


def parse_rwvg_actor_scan_payload(payload: bytes):
    """Parse legacy V1 or fragmented V2 Type=6 payloads."""
    if not payload or len(payload) < RWVG_ACTOR_SCAN_HEADER_SIZE:
        return None
    record_count, version = struct.unpack_from(RWVG_ACTOR_SCAN_HEADER_FMT, payload, 0)
    if version == RWVG_ACTOR_SCAN_VERSION:
        return _parse_v1(payload, record_count)
    if version == RWVG_ACTOR_SNAPSHOT_VERSION:
        return _parse_v2(payload, record_count)
    return None
