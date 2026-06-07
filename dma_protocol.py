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
CMD_FIND_USER_PATTERN = 16

CTRL_ACK_HANDLED = 1 << 0
CTRL_ACK_CPUEAXH_ONLINE = 1 << 1

PACKET_TYPE_LOG = 0x01
PACKET_TYPE_DATA = 0x02

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
PATTERN_SECTION_NAME_CAP = 16
PATTERN_BYTES_CAP = 256
FIND_USER_PATTERN_WIRE_FMT = f"<{PATTERN_SECTION_NAME_CAP}sHH{PATTERN_BYTES_CAP}s{PATTERN_BYTES_CAP}s"


def parse_packet_header(data: bytes):
    if not data:
        return None, None
    return data[0], data[1:]


from rwvg_protocol import (
    FLOAT32_TEXT_SIG_DIGITS,
    RWVG_ITEM_FMT,
    RWVG_MAGIC,
    RWVG_PLAYER_FMT,
    RWVG_TYPED_KIND_SET,
    RWVG_TYPED_SIZE_BY_KIND,
    RWVG_TYPE_ITEM,
    RWVG_TYPE_ITEM_BATCH,
    RWVG_TYPE_PLAYER,
    RWVG_TYPE_PLAYER_BATCH,
    RWVG_TYPE_UTILS,
    RWVG_UTILS_FMT,
    ZOMBIE_ACK_OK,
    coerce_float32,
    format_float32,
    parse_rwvg_batch_payload,
    parse_rwvg_item_payload,
    parse_rwvg_player_payload,
    parse_rwvg_utils_payload,
    parse_zombie_control_ack,
    try_parse_host_aggregate_payload,
    try_parse_rwvg_typed_payload,
)

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


def pack_find_user_pattern_req(pid, section_name: str, pattern: bytes, mask: str):
    pattern = bytes(pattern or b"")
    mask_text = (mask or "")
    section_text = "" if section_name in (None, "", "-") else str(section_name)
    if not pattern or len(pattern) > PATTERN_BYTES_CAP:
        raise ValueError(f"pattern length must be 1..{PATTERN_BYTES_CAP}")
    if mask_text and len(mask_text) != len(pattern):
        raise ValueError("mask length must equal pattern length")
    if len(section_text.encode("ascii", errors="ignore")) >= PATTERN_SECTION_NAME_CAP:
        raise ValueError(f"section name too long (max {PATTERN_SECTION_NAME_CAP - 1} ascii chars)")

    section_bytes = section_text.encode("ascii", errors="ignore")[:PATTERN_SECTION_NAME_CAP - 1]
    section_bytes = section_bytes.ljust(PATTERN_SECTION_NAME_CAP, b"\x00")
    pattern_bytes = pattern.ljust(PATTERN_BYTES_CAP, b"\x00")
    mask_bytes = mask_text.encode("ascii", errors="ignore")[:PATTERN_BYTES_CAP].ljust(PATTERN_BYTES_CAP, b"\x00")
    wire = struct.pack(
        FIND_USER_PATTERN_WIRE_FMT,
        section_bytes,
        len(pattern),
        len(mask_text),
        pattern_bytes,
        mask_bytes,
    )
    return _pack_request(CMD_FIND_USER_PATTERN, value=pid, size=len(wire), data=wire)


