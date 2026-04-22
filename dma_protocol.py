import base64
import math
import os
import struct

# RWbase src/Core/Network/Network.hpp defaults.
# Keep env overrides first; fallback matches RWbase default listen/target port.
DRIVER_IP = os.getenv("DMA_DRIVER_IP", "192.168.10.142")
DRIVER_PORT = int(os.getenv("DMA_DRIVER_PORT", "10010"))
BIND_PORT = int(os.getenv("DMA_BIND_PORT", str(DRIVER_PORT)))

MAGIC_KEY = 0xDEADBEEF

CMD_READ_MEM = 1
CMD_WRITE_MEM = 2
CMD_GET_CR3 = 3
CMD_ENUM_USER_REGIONS = 4
CMD_ENUM_USER_MODULES = 5
CMD_START_DATA_THREADS = 13
CMD_STOP_DATA_THREADS = 14
CMD_PINGPONG = 15

CTRL_ACK_HANDLED = 1 << 0
CTRL_ACK_CPUEAXH_ONLINE = 1 << 1

PACKET_TYPE_LOG = 0x01
PACKET_TYPE_DATA = 0x02

# RWbase typed game stream header:
# struct PacketHeader { ULONG Magic; ULONG Type; ULONG Size; }
RWVG_MAGIC = 0x47564352  # "RWVG"
RWVG_TYPE_UTILS = 1
RWVG_TYPE_PLAYER = 2
RWVG_TYPE_ITEM = 3
# RWbase::GameCore typed payload sizes (pack(1) structs)
RWVG_TYPED_SIZE_BY_KIND = {
    RWVG_TYPE_UTILS: 136,
    RWVG_TYPE_PLAYER: 255,
    RWVG_TYPE_ITEM: 90,
}

# Mirrors RWbase::GameCore::GameCoreTypes.hpp with #pragma pack(push, 1)
RWVG_UTILS_FMT = "<16f3Bi3f3fiQfB4f2i"
RWVG_PLAYER_FMT = "<2f2i18s18s18s32s32sQ3f3ffiiB18fi"
RWVG_ITEM_FMT = "<Q3fii32s18siii"
FLOAT32_TEXT_SIG_DIGITS = 9

# RWbase host-compat aggregate payload layout (base64 wrapped, raw UDP payload):
# [HostUtilsStruct][SIZE_T playerCount][HostSendPlayerStruct * N][SIZE_T itemCount][HostSendItemsStruct * M]
HOST_UTILS_SIZE = 145
HOST_PLAYER_SIZE = 402
HOST_ITEM_SIZE = 90
HOST_COUNT_SIZE = 8  # SIZE_T on x64 kernel build
ZOMBIE_ACK_OK = 1

# RWbase src/Utils/Definitions.hpp:
# #pragma pack(push, 1)
# struct PACKET_REQUEST {
#   UINT32 Magic;
#   UINT8  Command;
#   UINT64 Value;
#   UINT64 Address;
#   UINT32 Size;
#   UCHAR  Data[1024];
# }
# #pragma pack(pop)
PACKET_DATA_CAP = 1024
PACKET_FMT = "<IBQQI1024s"


def parse_packet_header(data: bytes):
    if not data:
        return None, None
    return data[0], data[1:]


def try_parse_rwvg_typed_payload(payload: bytes, strict_size: bool = True):
    """
    Parse PACKET_TYPE_DATA payload sent by RWbase::GameCore::SendTypedPacket.
    Returns (typed_kind, typed_payload_bytes) on success; otherwise None.
    """
    if not payload or len(payload) < 12:
        return None

    magic, typed_kind, typed_size = struct.unpack_from("<III", payload, 0)
    if magic != RWVG_MAGIC:
        return None
    if typed_kind not in (RWVG_TYPE_UTILS, RWVG_TYPE_PLAYER, RWVG_TYPE_ITEM):
        return None
    if typed_size != (len(payload) - 12):
        return None

    if strict_size:
        expected = RWVG_TYPED_SIZE_BY_KIND.get(typed_kind)
        if expected is not None and typed_size != expected:
            return None

    return typed_kind, payload[12:12 + typed_size]


def _decode_c_string(raw: bytes) -> str:
    if not raw:
        return ""
    nul_pos = raw.find(b"\x00")
    if nul_pos >= 0:
        raw = raw[:nul_pos]
    return raw.decode("utf-8", errors="ignore").strip()


def coerce_float32(value, default=0.0, allow_non_finite=True):
    try:
        parsed = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    except (TypeError, ValueError, struct.error):
        parsed = struct.unpack("<f", struct.pack("<f", float(default)))[0]
    if (not allow_non_finite) and (not math.isfinite(parsed)):
        return struct.unpack("<f", struct.pack("<f", float(default)))[0]
    return parsed


def format_float32(value, sig_digits: int = FLOAT32_TEXT_SIG_DIGITS):
    parsed = coerce_float32(value, default=0.0, allow_non_finite=True)
    if math.isnan(parsed):
        return "nan"
    if math.isinf(parsed):
        return "-inf" if parsed < 0.0 else "inf"
    if parsed == 0.0:
        return "-0" if math.copysign(1.0, parsed) < 0.0 else "0"

    digits = int(sig_digits) if sig_digits else FLOAT32_TEXT_SIG_DIGITS
    if digits < 1:
        digits = FLOAT32_TEXT_SIG_DIGITS
    return format(parsed, f".{digits}g")


def parse_rwvg_utils_payload(payload: bytes):
    if not payload or len(payload) != RWVG_TYPED_SIZE_BY_KIND[RWVG_TYPE_UTILS]:
        return None

    data = struct.unpack(RWVG_UTILS_FMT, payload)
    return {
        "matrix": [coerce_float32(v) for v in data[0:16]],
        "fight_mode": bool(data[16]),
        "home_show": bool(data[17]),
        "aim_bot": bool(data[18]),
        "local_team_id": int(data[19]),
        "local_neck_pos": {
            "x": coerce_float32(data[20]),
            "y": coerce_float32(data[21]),
            "z": coerce_float32(data[22]),
        },
        "local_pos": {
            "x": coerce_float32(data[23]),
            "y": coerce_float32(data[24]),
            "z": coerce_float32(data[25]),
        },
        "local_weapon_id": int(data[26]),
        "get_data_ptr": int(data[27]),
        "local_weapon_speed": coerce_float32(data[28]),
        "pre_should_draw": bool(data[29]),
        "map_info": {
            "x": coerce_float32(data[30]),
            "y": coerce_float32(data[31]),
            "w": coerce_float32(data[32]),
            "h": coerce_float32(data[33]),
            "map_x": int(data[34]),
            "map_y": int(data[35]),
        },
    }


def parse_rwvg_player_payload(payload: bytes):
    if not payload or len(payload) != RWVG_TYPED_SIZE_BY_KIND[RWVG_TYPE_PLAYER]:
        return None

    data = struct.unpack(RWVG_PLAYER_FMT, payload)
    bones_raw = data[17:35]
    bones = []
    for idx in range(0, len(bones_raw), 3):
        bones.append({
            "x": coerce_float32(bones_raw[idx]),
            "y": coerce_float32(bones_raw[idx + 1]),
            "z": coerce_float32(bones_raw[idx + 2]),
        })

    return {
        "health": coerce_float32(data[0]),
        "max_health": coerce_float32(data[1]),
        "armor_head": int(data[2]),
        "armor_body": int(data[3]),
        "class_name": _decode_c_string(data[4]),
        "detective": _decode_c_string(data[5]),
        "weapon_name": _decode_c_string(data[6]),
        "player_name": _decode_c_string(data[7]),
        "bot_name": _decode_c_string(data[8]),
        "entity_ptr": int(data[9]),
        "pos": {
            "x": coerce_float32(data[10]),
            "y": coerce_float32(data[11]),
            "z": coerce_float32(data[12]),
        },
        "prediction": {
            "x": coerce_float32(data[13]),
            "y": coerce_float32(data[14]),
            "z": coerce_float32(data[15]),
        },
        "direction": coerce_float32(data[16]),
        "distance": int(data[35]),
        "team_id": int(data[36]),
        "is_visible": bool(data[37]),
        "bone_pos": bones,
        "valid_flags": int(data[38]),
    }


def parse_rwvg_item_payload(payload: bytes):
    if not payload or len(payload) != RWVG_TYPED_SIZE_BY_KIND[RWVG_TYPE_ITEM]:
        return None

    data = struct.unpack(RWVG_ITEM_FMT, payload)
    return {
        "dead_box_type": int(data[0]),
        "pos": {
            "x": coerce_float32(data[1]),
            "y": coerce_float32(data[2]),
            "z": coerce_float32(data[3]),
        },
        "item_type": int(data[4]),
        "distance": int(data[5]),
        "item_name": _decode_c_string(data[6]),
        "project_name": _decode_c_string(data[7]),
        "item_money": int(data[8]),
        "item_quality": int(data[9]),
        "password": int(data[10]),
    }


def parse_zombie_control_ack(payload: bytes):
    """
    Parse CMD13/CMD14/CMD15 ACK returned by GhostCore control path.
    ACK format is a single ULONG64 value.
    """
    if not payload or len(payload) != 8:
        return None
    return int(struct.unpack_from("<Q", payload, 0)[0])


def try_parse_host_aggregate_payload(datagram: bytes):
    """
    Parse RWbase host-compat aggregate stream, which is sent as raw UDP payload
    (without PACKET_TYPE prefix) and base64-encoded.
    Returns {"player_count": int, "item_count": int, "raw_size": int} or None.
    """
    if not datagram:
        return None

    try:
        decoded = base64.b64decode(datagram, validate=True)
    except Exception:
        return None

    min_size = HOST_UTILS_SIZE + HOST_COUNT_SIZE + HOST_COUNT_SIZE
    if len(decoded) < min_size:
        return None

    offset = HOST_UTILS_SIZE
    player_count = struct.unpack_from("<Q", decoded, offset)[0]
    offset += HOST_COUNT_SIZE

    players_bytes = player_count * HOST_PLAYER_SIZE
    if players_bytes > (len(decoded) - offset - HOST_COUNT_SIZE):
        return None
    offset += players_bytes

    item_count = struct.unpack_from("<Q", decoded, offset)[0]
    offset += HOST_COUNT_SIZE

    items_bytes = item_count * HOST_ITEM_SIZE
    expected_size = HOST_UTILS_SIZE + HOST_COUNT_SIZE + players_bytes + HOST_COUNT_SIZE + items_bytes
    if expected_size != len(decoded):
        return None

    return {
        "player_count": int(player_count),
        "item_count": int(item_count),
        "raw_size": len(decoded),
    }


def _pack_request(command, value=0, address=0, size=0, data=b""):
    payload = (data or b"")[:PACKET_DATA_CAP]
    payload = payload.ljust(PACKET_DATA_CAP, b"\x00")
    return struct.pack(
        PACKET_FMT,
        MAGIC_KEY,
        command & 0xFF,
        value & 0xFFFFFFFFFFFFFFFF,
        address & 0xFFFFFFFFFFFFFFFF,
        size & 0xFFFFFFFF,
        payload,
    )


def pack_read_req(cr3, addr, size):
    return _pack_request(CMD_READ_MEM, value=cr3, address=addr, size=size)


def pack_write_req(cr3, addr, data: bytes):
    data = data or b""
    return _pack_request(CMD_WRITE_MEM, value=cr3, address=addr, size=len(data), data=data)


def pack_cr3_req(pid):
    return _pack_request(CMD_GET_CR3, value=pid)


def pack_enum_modules_req(pid):
    return _pack_request(CMD_ENUM_USER_MODULES, value=pid)


def pack_enum_regions_req(pid):
    return _pack_request(CMD_ENUM_USER_REGIONS, value=pid)


def pack_start_data_threads_req():
    return _pack_request(CMD_START_DATA_THREADS)


def pack_stop_data_threads_req():
    return _pack_request(CMD_STOP_DATA_THREADS)


def pack_pingpong_req():
    return _pack_request(CMD_PINGPONG)


