import base64
import math
import struct

from rwvg_actor_snapshot import (
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_HEADER_SIZE,
    RWVG_ACTOR_SNAPSHOT_MAX_CLASS_NAME_BYTES,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    parse_rwvg_actor_scan_payload as _parse_actor_snapshot_payload,
)

# RWbase typed game stream header:
# struct PacketHeader { ULONG Magic; ULONG Type; ULONG Size; }
RWVG_MAGIC = 0x47564352  # "RWVG"
RWVG_TYPE_UTILS = 1
RWVG_TYPE_PLAYER = 2
RWVG_TYPE_ITEM = 3
RWVG_TYPE_PLAYER_BATCH = 4
RWVG_TYPE_ITEM_BATCH = 5
RWVG_TYPE_ACTOR_SCAN = 6  # 新增：驱动扫描全量 Actor 的分类转储帧
RWVG_TYPED_KIND_SET = {
    RWVG_TYPE_UTILS,
    RWVG_TYPE_PLAYER,
    RWVG_TYPE_ITEM,
    RWVG_TYPE_PLAYER_BATCH,
    RWVG_TYPE_ITEM_BATCH,
    RWVG_TYPE_ACTOR_SCAN,
}
# RWbase::GameCore typed payload sizes (pack(1) structs)
RWVG_TYPED_SIZE_BY_KIND = {
    RWVG_TYPE_UTILS: 136,
    RWVG_TYPE_PLAYER: 179,
    RWVG_TYPE_ITEM: 90,
    # RWVG_TYPE_ACTOR_SCAN 故意不在此表：记录是变长的，无法用一个固定 Size 校验。
    # try_parse_rwvg_typed_payload 对未登记类型会回退到“仅按 Size 校验”的宽松路径。
}

# ----------------------------------------------------------------------------
# Actor 分类转储 (Type=6) 字节布局 — 供 C++ 驱动侧实现匹配的编码器。
#
# 一个 Type=6 帧 = [PacketHeader(12B)] + [ActorScanHeader(4B)] + N 条 ActorScanRecord
#
#   PacketHeader (复用所有 typed 帧的通用头):
#       ULONG Magic   = 0x47564352  ("RWVG", 小端)
#       ULONG Type    = 6           (RWVG_TYPE_ACTOR_SCAN)
#       ULONG Size    = sizeof(ActorScanHeader) + sum(sizeof(每条 record))
#
#   ActorScanHeader (4B, pack(1)):
#       UINT16 record_count   = N  (本帧内的 Actor 记录数，可为 0)
#       UINT16 record_version = 1  (布局版本号；解析侧按版本号决定字段，当前固定 1)
#
#   每条 ActorScanRecord (变长，pack(1)):
#       UINT64 entity     = UObject 指针（实体句柄/地址）
#       UINT32 objectId   = UObject 的 ObjectID/FNameIndex
#       UINT8  kind       = 分类码（见 RWVG_ACTOR_KIND_* 枚举）
#       UINT16 gname_len  = GName 字符串字节数（UTF-8 字节数，不含结尾 '\0'）
#       CHAR   gname[gname_len] = GName 文本（UTF-8，不带结尾 '\0'）
#       FLOAT  pos_x      = 3D 世界坐标 x (float32, 小端)
#       FLOAT  pos_y      = 3D 世界坐标 y (float32, 小端)
#       FLOAT  pos_z      = 3D 世界坐标 z (float32, 小端)
#
#   定长部分大小 = 8(entity) + 4(objectId) + 1(kind) + 2(gname_len) + 12(pos) = 27 字节
#   单条总大小   = 27 + gname_len
#
#   kind 取值（与前端 Kind 下拉、Python 侧 RWVG_ACTOR_KIND_NAMES 一一对应）:
#       0=Unknown, 1=Player, 2=Minion, 3=Boss, 4=Item,
#       5=Container, 6=DeadBox, 7=Box, 8=AI
# ----------------------------------------------------------------------------
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

# ActorScanRecord 定长部分: "<QIB" = entity(u64)+objectId(u32)+kind(u8)，随后紧跟 gname_len(u16)
RWVG_ACTOR_SCAN_RECORD_FIXED_FMT = "<QIBH"
RWVG_ACTOR_SCAN_RECORD_FIXED_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_RECORD_FIXED_FMT)  # = 15
RWVG_ACTOR_SCAN_POS_FMT = "<3f"
RWVG_ACTOR_SCAN_POS_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_POS_FMT)  # = 12
# 单条记录的定长字节总数（不含 GName 文本）= 15 + 12 = 27
RWVG_ACTOR_SCAN_RECORD_MIN_SIZE = RWVG_ACTOR_SCAN_RECORD_FIXED_SIZE + RWVG_ACTOR_SCAN_POS_SIZE
# ActorScanHeader 大小
RWVG_ACTOR_SCAN_HEADER_FMT = "<HH"
RWVG_ACTOR_SCAN_HEADER_SIZE = struct.calcsize(RWVG_ACTOR_SCAN_HEADER_FMT)  # = 4
RWVG_ACTOR_SCAN_VERSION = 1
# 单帧内 Actor 记录数上限（防失控/畸形帧，与 web_actor_kinds 的快照容量保持一致量级）
RWVG_ACTOR_SCAN_MAX_RECORDS = 2000
# 单条 GName 文本字节上限（防畸形长度）
RWVG_ACTOR_SCAN_MAX_GNAME_BYTES = 512

# Mirrors RWbase::GameCore::GameCoreTypes.hpp with #pragma pack(push, 1)
RWVG_UTILS_FMT = "<16f3Bi3f3fiQfB4f2i"
RWVG_PLAYER_FMT = "<2f2i18s18s18s32s32sQ3f3ffiiB"
RWVG_ITEM_FMT = "<Q3fii32s18siii"
FLOAT32_TEXT_SIG_DIGITS = 9

# RWbase host-compat aggregate payload layout (base64 wrapped, raw UDP payload):
# [HostUtilsStruct][SIZE_T playerCount][HostSendPlayerStruct * N][SIZE_T itemCount][HostSendItemsStruct * M]
HOST_UTILS_SIZE = 145
HOST_PLAYER_SIZE = 402
HOST_ITEM_SIZE = 90
HOST_COUNT_SIZE = 8  # SIZE_T on x64 kernel build
ZOMBIE_ACK_OK = 1

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
    if typed_kind not in RWVG_TYPED_KIND_SET:
        return None
    if typed_size != (len(payload) - 12):
        return None

    if strict_size:
        expected = RWVG_TYPED_SIZE_BY_KIND.get(typed_kind)
        if expected is not None and typed_size != expected:
            return None

    return typed_kind, payload[12:12 + typed_size]


def parse_rwvg_batch_payload(payload: bytes, element_size: int):
    if not payload or len(payload) < 4:
        return None

    try:
        parsed_element_size = int(element_size)
    except (TypeError, ValueError):
        return None
    if parsed_element_size <= 0:
        return None

    count = struct.unpack_from("<I", payload, 0)[0]
    expected_size = 4 + (count * parsed_element_size)
    if expected_size != len(payload):
        return None

    view = memoryview(payload)
    return [
        bytes(view[4 + (index * parsed_element_size): 4 + ((index + 1) * parsed_element_size)])
        for index in range(count)
    ]


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
        "distance": int(data[17]),
        "team_id": int(data[18]),
        "is_visible": bool(data[19]),
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


def parse_rwvg_actor_scan_payload(payload: bytes):
    """
    解析 Type=6 Actor 分类转储帧的 typed payload（已剥除 12B PacketHeader 之后的部分）。

    布局见模块顶部注释：
        ActorScanHeader(4B: record_count(u16) + record_version(u16))
        后随 record_count 条变长 ActorScanRecord

    返回 dict: {"records": [...], "record_count": int, "version": int}
    解析失败（长度不匹配/越界/版本不符）返回 None。
    """
    if not payload or len(payload) < RWVG_ACTOR_SCAN_HEADER_SIZE:
        return None

    record_count, record_version = struct.unpack_from(RWVG_ACTOR_SCAN_HEADER_FMT, payload, 0)
    if record_version == RWVG_ACTOR_SNAPSHOT_VERSION:
        return _parse_actor_snapshot_payload(payload)
    if record_version != RWVG_ACTOR_SCAN_VERSION:
        return None
    if record_count < 0 or record_count > RWVG_ACTOR_SCAN_MAX_RECORDS:
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

        gname_bytes = payload[offset:offset + gname_len]
        offset += gname_len

        pos_x, pos_y, pos_z = struct.unpack_from(RWVG_ACTOR_SCAN_POS_FMT, payload, offset)
        offset += RWVG_ACTOR_SCAN_POS_SIZE

        gname = gname_bytes.decode("utf-8", errors="ignore")
        records.append({
            "entity": int(entity),
            "object_id": int(object_id),
            "kind": int(kind),
            "kind_name": RWVG_ACTOR_KIND_NAMES.get(int(kind), "Unknown"),
            "gname": gname,
            "pos": {
                "x": coerce_float32(pos_x),
                "y": coerce_float32(pos_y),
                "z": coerce_float32(pos_z),
            },
        })

    # 末尾若仍有残留字节，视为布局不匹配，避免静默吃掉多余数据。
    if offset != len(payload):
        return None

    return {
        "records": records,
        "record_count": int(record_count),
        "version": int(record_version),
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


