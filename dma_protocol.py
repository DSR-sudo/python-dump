import os
import struct
import zlib
from dataclasses import dataclass

# RWbase src/Core/Network/Network.hpp defaults.
# The PMU source port is dynamic; only its destination is fixed.
DRIVER_IP = os.getenv("DMA_DRIVER_IP", "192.168.10.142")
TARGET_IP = os.getenv("DMA_TARGET_IP", "192.168.10.1")
TARGET_PORT = 34902
PCAP_CAPTURE_IFACE = os.getenv("DMA_CAPTURE_IFACE")
PCAP_SOURCE_PORT_MIN = 49152
PCAP_SOURCE_PORT_MAX = 65535

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
PACKET_TYPE_ONLINE = 0x03
PACKET_TYPE_SNAPSHOT = 0x06

CODEC_LZ4_BLOCK = 1
CODEC_ZERO_LITERAL = 2
CODEC_LZSS_1K = 3
CODEC_LZSS_4K = 4
CODEC_PACK_BITS = 5
PROTOCOL_MAGICS = frozenset((
    0xA7C31E5B, 0x3D91F4A7, 0xE24B8C19, 0x6F05D2CD, 0xB89347F1,
))
DEFAULT_PROTOCOL_MAGIC = 0xA7C31E5B
PROTOCOL_HEADER_FMT = "<IBBIIHHI"
PROTOCOL_HEADER_SIZE = struct.calcsize(PROTOCOL_HEADER_FMT)
CODEC_IDS = frozenset((CODEC_LZ4_BLOCK, CODEC_ZERO_LITERAL, CODEC_LZSS_1K, CODEC_LZSS_4K, CODEC_PACK_BITS))
PACKET_TYPE_IDS = frozenset((PACKET_TYPE_LOG, PACKET_TYPE_DATA, PACKET_TYPE_ONLINE, PACKET_TYPE_SNAPSHOT))
DEFAULT_PROTOCOL_DATAGRAM_SIZE = 1400
MAX_LZSS_OFFSET = 0xFFF


@dataclass(frozen=True)
class ProtocolHeader:
    magic: int
    packet_type: int
    codec_id: int
    stream_id: int
    sequence: int
    fragment_index: int
    fragment_count: int
    checksum: int


def _zero_literal_encode(data):
    out = bytearray()
    index = 0
    while index < len(data):
        if data[index] == 0:
            end = index
            while end < len(data) and data[end] == 0:
                end += 1
            if end - index >= 3:
                run = end - index
                while run:
                    chunk = min(run, 127)
                    out.append(0x80 | chunk)
                    run -= chunk
                index = end
                continue
        end = index + 1
        while end < len(data) and data[end] != 0 and end - index < 127:
            end += 1
        out.append(end - index)
        out.extend(data[index:end])
        index = end
    return bytes(out)


def _zero_literal_decode(data):
    out = bytearray()
    index = 0
    while index < len(data):
        token = data[index]
        index += 1
        length = token & 0x7F
        if length == 0:
            raise ValueError("invalid zero/literal token")
        if token & 0x80:
            out.extend(b"\x00" * length)
        elif index + length <= len(data):
            out.extend(data[index:index + length])
            index += length
        else:
            raise ValueError("truncated literal token")
    return bytes(out)


def _lz4_hash(value):
    return ((value * 2654435761) & 0xFFFFFFFF) >> 22


def _lz4_length(out, length):
    while length >= 255:
        out.append(255)
        length -= 255
    out.append(length)


def _lz4_sequence(out, literals, match_offset=0, match_size=0):
    token_index = len(out)
    out.append(0)
    literal_size = len(literals)
    match_code = max(0, match_size - 4)
    token = (0xF0 if literal_size >= 15 else literal_size << 4)
    token |= 0x0F if match_size >= 4 and match_code >= 15 else match_code
    if literal_size >= 15:
        _lz4_length(out, literal_size - 15)
    out.extend(literals)
    if match_size >= 4:
        out.extend(struct.pack("<H", match_offset))
        if match_code >= 15:
            _lz4_length(out, match_code - 15)
    out[token_index] = token


def _lz4_encode(data):
    data = bytes(data)
    positions = [-1] * 1024
    out = bytearray()
    anchor = 0
    index = 0
    match_limit = max(0, len(data) - 5)
    while index < match_limit:
        value = struct.unpack_from("<I", data, index)[0]
        slot = _lz4_hash(value)
        candidate = positions[slot]
        positions[slot] = index
        if (candidate < 0 or index - candidate > 0xFFFF
                or data[candidate:candidate + 4] != data[index:index + 4]):
            index += 1
            continue
        match_end = index + 4
        while match_end < len(data) and data[candidate + match_end - index] == data[match_end]:
            match_end += 1
        _lz4_sequence(out, data[anchor:index], index - candidate, match_end - index)
        index = match_end
        anchor = index
    _lz4_sequence(out, data[anchor:])
    return bytes(out)


def _lz4_decode(data):
    out = bytearray()
    index = 0
    while index < len(data):
        token = data[index]
        index += 1
        literal_size = token >> 4
        if literal_size == 15:
            while True:
                if index >= len(data):
                    raise ValueError("truncated LZ4 literal length")
                length = data[index]
                index += 1
                literal_size += length
                if length != 255:
                    break
        if index + literal_size > len(data):
            raise ValueError("truncated LZ4 literals")
        out.extend(data[index:index + literal_size])
        index += literal_size
        if index == len(data):
            break
        if index + 2 > len(data):
            raise ValueError("truncated LZ4 offset")
        offset = struct.unpack_from("<H", data, index)[0]
        index += 2
        if offset == 0 or offset > len(out):
            raise ValueError("invalid LZ4 offset")
        match_size = token & 0x0F
        if match_size == 15:
            while True:
                if index >= len(data):
                    raise ValueError("truncated LZ4 match length")
                length = data[index]
                index += 1
                match_size += length
                if length != 255:
                    break
        match_size += 4
        for _ in range(match_size):
            out.append(out[-offset])
    return bytes(out)


def _packbits_encode(data):
    out = bytearray()
    index = 0
    while index < len(data):
        run_end = index + 1
        while (run_end < len(data) and data[run_end] == data[index]
               and run_end - index < 127):
            run_end += 1
        if run_end - index >= 3:
            run = run_end - index
            while run:
                chunk = min(run, 127)
                out.extend((0x80 | chunk, data[index]))
                run -= chunk
                index += chunk
            continue
        literal_start = index
        literal_end = index
        while literal_end < len(data) and literal_end - literal_start < 127:
            next_index = literal_end + 1
            while (next_index < len(data) and data[next_index] == data[literal_end]
                   and next_index - literal_end < 3):
                next_index += 1
            if next_index - literal_end >= 3:
                break
            literal_end = next_index
        if literal_end == literal_start:
            literal_end += 1
        out.append(literal_end - literal_start)
        out.extend(data[literal_start:literal_end])
        index = literal_end
    return bytes(out)


def _packbits_decode(data):
    out = bytearray()
    index = 0
    while index < len(data):
        token = data[index]
        index += 1
        length = token & 0x7F
        if length == 0:
            raise ValueError("invalid PackBits token")
        if token & 0x80:
            if index >= len(data):
                raise ValueError("truncated PackBits run")
            out.extend(bytes((data[index],)) * length)
            index += 1
        elif index + length <= len(data):
            out.extend(data[index:index + length])
            index += length
        else:
            raise ValueError("truncated PackBits literal")
    return bytes(out)


def _lzss_encode(data, window):
    out = bytearray()
    index = 0
    while index < len(data):
        control_index = len(out)
        out.append(0)
        control = 0
        for bit in range(8):
            if index >= len(data):
                break
            start = max(0, index - min(window, MAX_LZSS_OFFSET))
            best_length = 0
            best_offset = 0
            for candidate in range(start, index):
                length = 0
                while length < 18 and index + length < len(data):
                    source = candidate + length
                    if source >= index or data[source] != data[index + length]:
                        break
                    length += 1
                if length > best_length:
                    best_length = length
                    best_offset = index - candidate
            if best_length >= 3:
                control |= 1 << bit
                out.extend(struct.pack("<H", (best_offset << 4) | (best_length - 3)))
                index += best_length
            else:
                out.append(data[index])
                index += 1
        out[control_index] = control
    return bytes(out)


def _lzss_decode(data, window):
    out = bytearray()
    index = 0
    while index < len(data):
        control = data[index]
        index += 1
        for bit in range(8):
            if index >= len(data):
                break
            if control & (1 << bit):
                if index + 2 > len(data):
                    raise ValueError("truncated LZSS token")
                token = struct.unpack_from("<H", data, index)[0]
                index += 2
                offset = token >> 4
                length = (token & 0xF) + 3
                if offset == 0 or offset > window or offset > len(out):
                    raise ValueError("invalid LZSS offset")
                for _ in range(length):
                    out.append(out[-offset])
            else:
                out.append(data[index])
                index += 1
    return bytes(out)


def encode_codec(data, codec_id):
    codecs = {CODEC_LZ4_BLOCK: _lz4_encode, CODEC_ZERO_LITERAL: _zero_literal_encode,
              CODEC_LZSS_1K: lambda value: _lzss_encode(value, 1024),
              CODEC_LZSS_4K: lambda value: _lzss_encode(value, 4096),
              CODEC_PACK_BITS: _packbits_encode}
    if codec_id not in codecs:
        raise ValueError(f"unsupported codec id {codec_id}")
    return codecs[codec_id](bytes(data))


def decode_codec(data, codec_id):
    codecs = {CODEC_LZ4_BLOCK: _lz4_decode, CODEC_ZERO_LITERAL: _zero_literal_decode,
              CODEC_LZSS_1K: lambda value: _lzss_decode(value, 1024),
              CODEC_LZSS_4K: lambda value: _lzss_decode(value, 4096),
              CODEC_PACK_BITS: _packbits_decode}
    if codec_id not in codecs:
        raise ValueError(f"unsupported codec id {codec_id}")
    return codecs[codec_id](bytes(data))


def pack_protocol_datagrams(packet_type, payload, stream_id, sequence, codec_id,
                            max_datagram=DEFAULT_PROTOCOL_DATAGRAM_SIZE,
                            magic=DEFAULT_PROTOCOL_MAGIC):
    payload = bytes(payload)
    encoded = encode_codec(payload, codec_id)
    if magic not in PROTOCOL_MAGICS:
        raise ValueError("invalid protocol magic")
    chunk_size = max_datagram - PROTOCOL_HEADER_SIZE
    if not payload or chunk_size <= 0:
        raise ValueError("invalid protocol payload or datagram size")
    chunks = [encoded[index:index + chunk_size] for index in range(0, len(encoded), chunk_size)]
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return [
        struct.pack(PROTOCOL_HEADER_FMT, magic, packet_type,
                    codec_id, stream_id, sequence, index, len(chunks), checksum) + chunk
        for index, chunk in enumerate(chunks)
    ]


def parse_protocol_datagram(datagram):
    if len(datagram) < PROTOCOL_HEADER_SIZE:
        raise ValueError("protocol datagram is shorter than header")
    values = struct.unpack_from(PROTOCOL_HEADER_FMT, datagram)
    header = ProtocolHeader(*values)
    if (header.packet_type not in PACKET_TYPE_IDS or header.codec_id not in CODEC_IDS
            or header.fragment_count == 0 or header.fragment_index >= header.fragment_count):
        raise ValueError("invalid codec or fragment range")
    if header.magic not in PROTOCOL_MAGICS:
        raise ValueError("invalid protocol magic")
    body = datagram[PROTOCOL_HEADER_SIZE:]
    if not body:
        raise ValueError("invalid protocol fragment length")
    return header, body


class ProtocolReassembler:
    def __init__(self):
        self._frames = {}

    def add(self, datagram):
        header, body = parse_protocol_datagram(datagram)
        key = (header.stream_id, header.sequence, header.packet_type)
        frame = self._frames.setdefault(key, {"header": header, "parts": {}})
        expected = frame["header"]
        if (expected.magic, expected.codec_id, expected.fragment_count, expected.checksum) != (
                header.magic, header.codec_id, header.fragment_count, header.checksum):
            raise ValueError("inconsistent protocol fragment header")
        frame["parts"][header.fragment_index] = body
        if len(frame["parts"]) != header.fragment_count:
            return None
        encoded = b"".join(frame["parts"][index] for index in range(header.fragment_count))
        del self._frames[key]
        raw = decode_codec(encoded, header.codec_id)
        if (zlib.crc32(raw) & 0xFFFFFFFF) != header.checksum:
            raise ValueError("protocol checksum mismatch")
        return header.packet_type, raw, (header.stream_id, header.sequence)

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
    RWVG_ACTOR_KIND_AI,
    RWVG_ACTOR_KIND_BOSS,
    RWVG_ACTOR_KIND_BOX,
    RWVG_ACTOR_KIND_CONTAINER,
    RWVG_ACTOR_KIND_DEADBOX,
    RWVG_ACTOR_KIND_ITEM,
    RWVG_ACTOR_KIND_MINION,
    RWVG_ACTOR_KIND_NAMES,
    RWVG_ACTOR_KIND_PLAYER,
    RWVG_ACTOR_KIND_UNKNOWN,
    RWVG_ACTOR_SNAPSHOT_HEADER_FMT,
    RWVG_ACTOR_SNAPSHOT_HEADER_SIZE,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_FMT,
    RWVG_ACTOR_SNAPSHOT_RECORD_FIXED_SIZE,
    RWVG_ACTOR_SNAPSHOT_VERSION,
    RWVG_ITEM_FMT,
    RWVG_MAGIC,
    RWVG_PLAYER_FMT,
    RWVG_TYPED_KIND_SET,
    RWVG_TYPED_SIZE_BY_KIND,
    RWVG_TYPE_ACTOR_SCAN,
    RWVG_TYPE_ITEM,
    RWVG_TYPE_ITEM_BATCH,
    RWVG_TYPE_PLAYER,
    RWVG_TYPE_PLAYER_BATCH,
    RWVG_TYPE_UTILS,
    RWVG_UTILS_FMT,
    ZOMBIE_ACK_OK,
    coerce_float32,
    format_float32,
    parse_rwvg_actor_scan_payload,
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


