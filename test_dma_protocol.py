import struct
import unittest

from dma_protocol import (
    CODEC_LZ4_BLOCK,
    CODEC_LZSS_1K,
    CODEC_LZSS_4K,
    CODEC_PACK_BITS,
    CODEC_ZERO_LITERAL,
    PACKET_TYPE_DATA,
    PROTOCOL_HEADER_SIZE,
    PROTOCOL_MAGICS,
    ProtocolReassembler,
    pack_protocol_datagrams,
    parse_protocol_datagram,
    decode_codec,
    encode_codec,
)
from pcap_capture import PcapCapture


class ProtocolTest(unittest.TestCase):
    def test_interface_selection_prefers_target_address(self):
        entries = ((b"route", ("192.168.10.2",)), (b"target", ("192.168.10.1",)))
        self.assertEqual(
            PcapCapture._select_interface(entries, "192.168.10.1", "192.168.10.2"),
            b"target",
        )

    def test_interface_selection_uses_route_when_target_is_remote(self):
        entries = ((b"route", ("10.0.0.7",)), (b"other", ("172.16.0.4",)))
        self.assertEqual(
            PcapCapture._select_interface(entries, "192.168.10.1", "10.0.0.7"),
            b"route",
        )

    def test_ethernet_ipv4_udp_frame_parser(self):
        payload = b"wire"
        udp = struct.pack("!HHHH", 50000, 34902, 8 + len(payload), 0) + payload
        ip_header = bytearray(20)
        ip_header[0] = 0x45
        struct.pack_into("!H", ip_header, 2, 20 + len(udp))
        ip_header[8] = 64
        ip_header[9] = 17
        ip_header[12:16] = bytes((192, 168, 10, 142))
        ip_header[16:20] = bytes((192, 168, 10, 1))
        ethernet = b"\x00" * 12 + struct.pack("!H", 0x0800)
        self.assertEqual(
            PcapCapture._parse_ipv4(ethernet + bytes(ip_header) + udp, PcapCapture.DLT_EN10MB),
            ("192.168.10.142", 50000, payload),
        )

    def test_all_codecs_round_trip(self):
        payload = (b"\x00" * 300) + (b"abcdef" * 400) + bytes(range(256))
        for codec in (CODEC_LZ4_BLOCK, CODEC_ZERO_LITERAL, CODEC_LZSS_1K, CODEC_LZSS_4K, CODEC_PACK_BITS):
            self.assertEqual(decode_codec(encode_codec(payload, codec), codec), payload)

    def test_fragment_reassembly_validates_checksum_and_range(self):
        payload = b"snapshot" * 500
        datagrams = pack_protocol_datagrams(PACKET_TYPE_DATA, payload, 7, 11, CODEC_LZSS_4K, 96)
        receiver = ProtocolReassembler()
        result = None
        for datagram in reversed(datagrams):
            result = receiver.add(datagram) or result
        self.assertEqual(result[0], PACKET_TYPE_DATA)
        self.assertEqual(result[1], payload)

        invalid = bytearray(datagrams[0])
        invalid[0] ^= 1
        with self.assertRaises(ValueError):
            parse_protocol_datagram(bytes(invalid))

    def test_sender_always_emits_codec_stream(self):
        self.assertEqual(PROTOCOL_HEADER_SIZE, 22)
        magic = next(iter(PROTOCOL_MAGICS))
        datagram = pack_protocol_datagrams(
            PACKET_TYPE_DATA, b"abc", 1, 1, CODEC_LZ4_BLOCK, magic=magic)[0]
        header, body = parse_protocol_datagram(datagram)
        self.assertEqual(header.magic, magic)
        self.assertEqual(header.codec_id, CODEC_LZ4_BLOCK)
        self.assertEqual(decode_codec(body, CODEC_LZ4_BLOCK), b"abc")

    def test_invalid_fragment_range_is_rejected(self):
        datagram = bytearray(pack_protocol_datagrams(PACKET_TYPE_DATA, b"abc", 1, 1, CODEC_LZ4_BLOCK)[0])
        struct.pack_into("<H", datagram, 14, 1)
        with self.assertRaises(ValueError):
            parse_protocol_datagram(bytes(datagram))


if __name__ == "__main__":
    unittest.main()
