import struct
import unittest

from dma_protocol import (
    HEARTBEAT, MAGIC_KEY, PACKET_FMT, PACKET_SIZE, PACKET_TYPE_IDS,
    UDP_MAX_DATAGRAM_SIZE, pack_packet, parse_packet_header,
    validate_driver_request, pack_read_req, pack_write_req, pack_cr3_req,
    pack_enum_modules_req, pack_enum_regions_req, pack_start_data_threads_req,
    pack_stop_data_threads_req, pack_pingpong_req, pack_find_user_pattern_req,
)


class ProtocolTest(unittest.TestCase):
    def test_plaintext_wire_bytes_and_roundtrip(self):
        payload = bytes(range(256)) + '明文'.encode()
        for kind in PACKET_TYPE_IDS:
            packet = pack_packet(kind, payload)
            self.assertEqual(packet, bytes([kind]) + payload)
            self.assertEqual(parse_packet_header(packet), (kind, payload))
            self.assertEqual(parse_packet_header(pack_packet(kind, b'')), (kind, b''))

    def test_limits(self):
        data = pack_packet(2, b'x' * (UDP_MAX_DATAGRAM_SIZE - 1))
        self.assertEqual(len(data), UDP_MAX_DATAGRAM_SIZE)
        with self.assertRaises(ValueError):
            pack_packet(2, b'x' * UDP_MAX_DATAGRAM_SIZE)
        for bad in [b'', b'\x00abc', b'\xffabc', data + b'x']:
            with self.assertRaises(ValueError):
                parse_packet_header(bad)
        with self.assertRaises(ValueError):
            pack_packet(0, b'abc')

    def test_old_codec_and_tcp_frames_rejected(self):
        for magic in (0xA7C31E5B, 0x3D91F4A7, 0xE24B8C19, 0x6F05D2CD, 0xB89347F1):
            old = struct.pack('<IBBIIHHI', magic, 2, 1, 7, 11, 0, 1, 0) + b'\x30abc'
            for packet in (old, struct.pack('<I', len(old)) + old):
                with self.assertRaises(ValueError):
                    parse_packet_header(packet)

    def test_commands_keep_existing_little_endian_abi(self):
        self.assertEqual(PACKET_SIZE, 1049)
        packet = pack_read_req(0x1122334455667788, 0x8877665544332211, 8192)
        self.assertEqual(packet[:25], struct.pack('<IBQQI', MAGIC_KEY, 1,
                         0x1122334455667788, 0x8877665544332211, 8192))
        requests = [packet, pack_write_req(1, 2, b'a\x00b'), pack_cr3_req(4),
                    pack_enum_modules_req(4), pack_enum_regions_req(4),
                    pack_start_data_threads_req(), pack_stop_data_threads_req(),
                    pack_pingpong_req(), pack_find_user_pattern_req(4, '.text', b'ab', 'xx')]
        for request in requests:
            self.assertEqual(len(request), PACKET_SIZE)
            self.assertEqual(validate_driver_request(request), request)
        self.assertEqual(validate_driver_request(HEARTBEAT), b'HELO' + bytes(28))
        for bad in [packet[:-1], packet + b'x', b'HELO', HEARTBEAT[:-1] + b'x',
                    b'\x00' * PACKET_SIZE, struct.pack('<I', len(packet)) + packet,
                    struct.pack(PACKET_FMT, MAGIC_KEY, 99, 0, 0, 0, b'')]:
            with self.assertRaises(ValueError):
                validate_driver_request(bad)


if __name__ == '__main__':
    unittest.main()
