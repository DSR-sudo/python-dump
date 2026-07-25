from pathlib import Path
import json
import shutil
import socket
import sys
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web_actor_kinds import WebActorKindsService  # noqa: E402


class SnapshotCore:
    def get_actor_scan_snapshot(self):
        return {
            "actors": [{
                "actor_address": 0x1234, "entity": 0x1234, "object_id": 7,
                "kind": 6, "kind_name": "DeadBox", "class_name": "BP_Box",
                "gname": "BP_Box", "mesh": 1, "root_component": 2, "player_state": 3,
                "pos": {"x": 1.0, "y": 2.0, "z": 3.0},
                "position_bits": {"x": 1, "y": 2, "z": 3}, "position_source": 6,
                "last_db_position_tsc": 5, "team_id": 4, "health_bits": 6,
                "max_health_bits": 7, "weapon_id": 8, "valid_fields": 9,
                "diagnostics": {"attempts": 1, "failures": 0, "first_failure": 0},
            }],
            "count": 1, "frames": 2, "last_ts": 1.0, "last_count": 1, "has_data": True,
            "version": 2, "snapshot_id": 88, "fragment_count": 3,
            "received_fragments": 2, "total_record_count": 4, "complete": False,
            "status": "partial", "dropped_late_fragments": 1,
            "duplicate_fragments": 2, "invalid_fragments": 3,
        }


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_http_api_exposes_complete_actor_fields_and_snapshot_progress():
    with tempfile.TemporaryDirectory() as tmpdir:
        web_dir = Path(tmpdir) / "web"
        web_dir.mkdir()
        shutil.copy(ROOT / "web" / "actor_kinds.html", web_dir / "actor_kinds.html")
        service = WebActorKindsService(SnapshotCore(), root_dir=tmpdir)
        port = free_port()
        started, _ = service.start(port_override=port)
        assert started
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/actor_kinds",
                headers={"X-Auth-Token": service.password},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            service.stop()

    assert payload["complete"] is False
    assert payload["received_fragments"] == 2
    assert payload["total_record_count"] == 4
    assert payload["actors"][0]["diagnostics"]["attempts"] == 1
    assert payload["actors"][0]["root_component"] == 2


def test_actor_page_renders_summary_and_expandable_full_fields():
    page = (ROOT / "web" / "actor_kinds.html").read_text(encoding="utf-8")
    assert "kind-summary" in page
    assert "<details><summary>Full fields</summary>" in page
    assert "position_bits" in page and "diagnostics" in page
