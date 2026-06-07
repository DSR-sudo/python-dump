from pathlib import Path
import tempfile
import sys


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web_radar import WebRadarService  # noqa: E402


class _StubCore:
    def get_radar_snapshot(self):
        return {"local_player": {"id": "local", "team_id": 0, "camp_id": 0, "yaw": 0.0, "position": {"x": 0, "y": 0}}, "entities": [], "teammates": []}

    def get_stream_stats(self):
        return {
            "player_batch_frames": 3,
            "item_batch_frames": 2,
            "player_batch_entities": 6,
            "item_batch_entities": 4,
        }

    def get_rwbase_decrypt_diag(self):
        return {"recent": []}

    def get_send_thread_history(self, limit=120):
        return []


def test_api_rwvg_metadata_includes_batch_contract():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = WebRadarService(_StubCore(), root_dir=tmpdir)
        data = service._build_rwvg_data()

    protocol = data["protocol"]
    assert protocol["types"]["player_batch"] == 4
    assert protocol["types"]["item_batch"] == 5
    assert protocol["batch_stats"]["player_batch_frames"] == 3
    assert protocol["batch_stats"]["item_batch_entities"] == 4


if __name__ == "__main__":
    test_api_rwvg_metadata_includes_batch_contract()
    print("ok")

