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
    TCP_FRAME_PREFIX_SIZE,
    ProtocolStreamReassembler,
    pack_protocol_frames,
    parse_protocol_frame,
    decode_codec,
    encode_codec,
)


class ProtocolTest(unittest.TestCase):
    def test_all_codecs_round_trip(self):
        payload = (b"\x00" * 300) + (b"abcdef" * 400) + bytes(range(256))
        for codec in (CODEC_LZ4_BLOCK, CODEC_ZERO_LITERAL, CODEC_LZSS_1K, CODEC_LZSS_4K, CODEC_PACK_BITS):
            self.assertEqual(decode_codec(encode_codec(payload, codec), codec), payload)

    def test_tcp_stream_reassembles_split_frames(self):
        payload = b"snapshot" * 500
        frames = pack_protocol_frames(PACKET_TYPE_DATA, payload, 7, 11, CODEC_LZSS_4K, 96)
        receiver = ProtocolStreamReassembler()
        results = []
        stream = b"".join(frames)
        for offset in range(0, len(stream), 7):
            results.extend(receiver.feed(stream[offset:offset + 7]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], PACKET_TYPE_DATA)
        self.assertEqual(results[0][1], payload)

    def test_tcp_frame_length_prefix_is_validated(self):
        frame = bytearray(pack_protocol_frames(PACKET_TYPE_DATA, b"abc", 1, 1, CODEC_LZ4_BLOCK)[0])
        struct.pack_into("<I", frame, 0, len(frame))
        with self.assertRaises(ValueError):
            parse_protocol_frame(bytes(frame))

    def test_sender_emits_codec_stream_after_tcp_prefix(self):
        self.assertEqual(PROTOCOL_HEADER_SIZE, 22)
        magic = next(iter(PROTOCOL_MAGICS))
        frame = pack_protocol_frames(
            PACKET_TYPE_DATA, b"abc", 1, 1, CODEC_LZ4_BLOCK, magic=magic,
        )[0]
        self.assertEqual(struct.unpack_from("<I", frame)[0], len(frame) - TCP_FRAME_PREFIX_SIZE)
        header, body = parse_protocol_frame(frame)
        self.assertEqual(header.magic, magic)
        self.assertEqual(header.codec_id, CODEC_LZ4_BLOCK)
        self.assertEqual(decode_codec(body, CODEC_LZ4_BLOCK), b"abc")

    def test_invalid_fragment_range_is_rejected(self):
        frame = bytearray(pack_protocol_frames(PACKET_TYPE_DATA, b"abc", 1, 1, CODEC_LZ4_BLOCK)[0])
        struct.pack_into("<H", frame, TCP_FRAME_PREFIX_SIZE + 14, 1)
        with self.assertRaises(ValueError):
            parse_protocol_frame(bytes(frame))


if __name__ == "__main__":
    unittest.main()
