from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dma_protocol  # noqa: E402


def test_dma_protocol_exposes_batch_parser():
    assert hasattr(dma_protocol, "parse_rwvg_batch_payload"), "missing batch parser helper"


def test_dma_protocol_accepts_batch_kinds_in_typed_payloads():
    header = dma_protocol.RWVG_MAGIC.to_bytes(4, "little")
    for kind in (4, 5):
        payload = header + kind.to_bytes(4, "little") + (4).to_bytes(4, "little") + (0).to_bytes(4, "little")
        assert dma_protocol.try_parse_rwvg_typed_payload(payload) is not None


if __name__ == "__main__":
    test_dma_protocol_exposes_batch_parser()
    test_dma_protocol_accepts_batch_kinds_in_typed_payloads()
    print("ok")
