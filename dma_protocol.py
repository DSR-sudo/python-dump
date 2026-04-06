import os
import struct
import base64

# RWbase src/Core/Network/Network.hpp defaults.
# Keep env overrides first; fallback matches RWbase default listen/target port.
DRIVER_IP = os.getenv("DMA_DRIVER_IP", "192.168.10.142")
DRIVER_PORT = int(os.getenv("DMA_DRIVER_PORT", "10010"))
BIND_PORT = int(os.getenv("DMA_BIND_PORT", str(DRIVER_PORT)))

MAGIC_KEY = 0xDEADBEEF

CMD_READ_MEM = 1
CMD_WRITE_MEM = 2
CMD_GET_CR3 = 3
CMD_ENUM_USER_MODULES = 5
CMD_START_DATA_THREADS = 13
CMD_STOP_DATA_THREADS = 14

PACKET_TYPE_LOG = 0x01
PACKET_TYPE_DATA = 0x02

# RWbase typed game stream header:
# struct PacketHeader { ULONG Magic; ULONG Type; ULONG Size; }
RWVG_MAGIC = 0x47564352  # "RWVG"
RWVG_TYPE_UTILS = 1
RWVG_TYPE_PLAYER = 2
RWVG_TYPE_ITEM = 3

# RWbase host-compat aggregate payload layout (base64 wrapped, raw UDP payload):
# [HostUtilsStruct][SIZE_T playerCount][HostSendPlayerStruct * N][SIZE_T itemCount][HostSendItemsStruct * M]
HOST_UTILS_SIZE = 145
HOST_PLAYER_SIZE = 402
HOST_ITEM_SIZE = 90
HOST_COUNT_SIZE = 8  # SIZE_T on x64 kernel build

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


def try_parse_rwvg_typed_payload(payload: bytes):
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
    return typed_kind, payload[12:12 + typed_size]


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


def pack_start_data_threads_req():
    return _pack_request(CMD_START_DATA_THREADS)


def pack_stop_data_threads_req():
    return _pack_request(CMD_STOP_DATA_THREADS)
