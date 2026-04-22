import socket
import threading
import time
import os
import re
import math
from dma_protocol import *

DEFAULT_EXPECTED_TRANSFER_BPS = 8 * 1024 * 1024  # 8 MB/s conservative baseline.
DEFAULT_TRANSFER_GRACE_SEC = 5.0
DEFAULT_IDLE_TIMEOUT_SEC = 2.5
WAIT_SLICE_SEC = 0.2
VERBOSE_EXPECTING_LOG = os.getenv("DMA_VERBOSE_EXPECTING", "0") == "1"
DEFAULT_PLAYER_TTL_SEC = float(os.getenv("DMA_WEBRADAR_PLAYER_TTL", "1.5"))


def _safe_float(value, default=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return parsed


def _safe_int(value, default=0):
    return int(_safe_float(value, default))


def _safe_pos_dict(pos):
    pos = pos or {}
    return {
        "x": _safe_int(pos.get("x", 0.0)),
        "y": _safe_int(pos.get("y", 0.0)),
        "z": _safe_int(pos.get("z", 0.0)),
    }


def _safe_wire_float(value, default=0.0):
    return coerce_float32(value, default=default, allow_non_finite=True)


def _safe_wire_pos_dict(pos):
    pos = pos or {}
    return {
        "x": _safe_wire_float(pos.get("x", 0.0)),
        "y": _safe_wire_float(pos.get("y", 0.0)),
        "z": _safe_wire_float(pos.get("z", 0.0)),
    }


class DMACore:
    DECODE_PATH_CATEGORIES = {
        "emu_runtime_init",
        "emu_runtime_exec_fail",
        "emu_bridge_last",
        "emu_call_last",
        "coord_decode_summary",
        "coord_decode_group",
    }

    MODULE_LOG_RE = re.compile(
        r"^\[User\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+Name:\s+(.+)$"
    )
    REGION_LOG_RE = re.compile(
        r"^\[UserRegion\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+State:\s+0x([0-9A-Fa-f]+)\s+\|\s+Protect:\s+0x([0-9A-Fa-f]+)\s+\|\s+Type:\s+0x([0-9A-Fa-f]+)$"
    )
    REGION_DONE_RE = re.compile(
        r"^\[UserRegion\]\s+Done\s+PID=([0-9A-Fa-fx]+)\s+Count=(\d+)$"
    )
    TEST_OFFSETS_RE = re.compile(
        r"^\[GameCore\]\[Test\]\[Offsets\]\s+uworld=(0x[0-9A-Fa-f]+)\s+fnames=(0x[0-9A-Fa-f]+)\s+lpp=(0x[0-9A-Fa-f]+)\s+pc=0x([0-9A-Fa-f]+)\s+pawn=0x([0-9A-Fa-f]+)\s+mesh=0x([0-9A-Fa-f]+)\s+bone=0x([0-9A-Fa-f]+)\s+c2w=0x([0-9A-Fa-f]+)\s+ps=0x([0-9A-Fa-f]+)\s+team=0x([0-9A-Fa-f]+)$"
    )
    TRACK_READY_RE = re.compile(
        r"^\[GameCore\]\[Track\]\s+target ready pid=(0x[0-9A-Fa-f]+)\s+old=(0x[0-9A-Fa-f]+)\s+ucr3=(0x[0-9A-Fa-f]+)\s+kcr3=(0x[0-9A-Fa-f]+)\s+base=(0x[0-9A-Fa-f]+)\s+net=(\w+)$"
    )
    TEST_CHAIN_RE = re.compile(
        r"^\[GameCore\]\[Test\]\[Chain\]\s+uworld=(0x[0-9A-Fa-f]+)\s+fnames=(0x[0-9A-Fa-f]+)\s+lpp=(0x[0-9A-Fa-f]+)\s+pc=(0x[0-9A-Fa-f]+)\s+pawn=(0x[0-9A-Fa-f]+)\s+ps=(0x[0-9A-Fa-f]+)\s+team=(-?\d+)\s+mesh=(0x[0-9A-Fa-f]+)\s+ack=0x([0-9A-Fa-f]+)$"
    )
    EMU_RUNTIME_INIT_RE = re.compile(
        r"^\[Emu\]\[Runtime\]\s+initialize status=0x([0-9A-Fa-f]+)\s+ready=(\d+)$"
    )
    EMU_RUNTIME_EXEC_FAIL_RE = re.compile(
        r"^\[Emu\]\[Runtime\]\s+execute-fail status=0x([0-9A-Fa-f]+)\s+exit=(\d+)\s+cpueaxh=(\d+)\s+exc=(\d+)\s+rip=(0x[0-9A-Fa-f]+)\s+cr3=(0x[0-9A-Fa-f]+)$"
    )
    EMU_BRIDGE_RE = re.compile(
        r"^\[Emu\]\[Bridge\]\s+(dispatch-fail|guest-fail|guest-ok)\s+(.+)$"
    )
    EMU_CALL_RE = re.compile(
        r"^\[Emu\]\[Call\]\s+(fail|ok)\s+(.+)$"
    )
    COORD_DECRYPT_FAIL_SUMMARY_RE = re.compile(
        r"^\[CoordDecrypt\]\s+text-emu fail agg frame=(\d+)\s+total=(\d+)\s+groups=(\d+)\s+dropped=(\d+)$"
    )
    COORD_DECRYPT_FAIL_GROUP_RE = re.compile(
        r"^\[CoordDecrypt\]\s+text-emu fail agg#(\d+)\s+cnt=(\d+)\s+type=(\w+)\s+strategy=(\w+)\s+algo=(\d+)\s+kind=(\d+)\s+source=(\w+)\s+confidence=(\w+)\s+rcode=([^(]+)\((\d+)\)\s+last_entry=0x([0-9A-Fa-f]+)\s+pid=0x([0-9A-Fa-f]+)\s+cr3=0x([0-9A-Fa-f]+)$"
    )

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", BIND_PORT))

        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
        except Exception:
            print("[!] Warning: Could not set 64MB Recv Buffer. OS limit might be lower.")
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)

        self.is_running = True
        self.driver_online = False
        self.seq = 0
        self.rwvg_stream_detected = False
        self.host_aggregate_detected = False
        self.rwvg_stats = {
            "utils_frames": 0,
            "player_frames": 0,
            "item_frames": 0,
            "typed_bytes": 0,
            "host_aggregate_frames": 0,
            "host_aggregate_raw_bytes": 0,
            "command_bytes": 0,
            "dropped_data_packets": 0,
            "zombie_ack_packets": 0,
            "zombie_ack_non_ok": 0,
            "zombie_last_ack": None,
        }

        self.recv_event = threading.Event()
        self.buffer = bytearray()
        self.view = memoryview(self.buffer)
        self.expected_size = 0
        self.recvd_bytes = 0
        self.last_data_ts = 0.0
        self.last_untyped_data_ts = 0.0
        self.last_request_timed_out = False
        self.lock = threading.Lock()
        self.module_lock = threading.Lock()
        self.module_entries = []
        self.region_lock = threading.Lock()
        self.region_entries = []
        self.region_enum_done = False
        self.region_enum_count = 0
        self.host_lock = threading.Lock()
        self.host_diag = {
            "offsets": None,
            "track_ready": None,
            "chain": None,
            "emu_runtime_init": None,
            "emu_runtime_exec_fail": None,
            "emu_bridge_last": None,
            "emu_call_last": None,
            "coord_decode_summary": None,
            "coord_decode_groups": [],
            "recent_logs": [],
        }
        self.trace_lock = threading.Lock()
        self.send_thread_history = []
        self.decode_path_history = []
        self.trace_history_limit = 512
        self.radar_lock = threading.Lock()
        self.radar_latest_utils = None
        self.radar_latest_utils_ts = 0.0
        self.radar_players = {}
        self.radar_items = {}
        self.radar_player_ttl_sec = DEFAULT_PLAYER_TTL_SEC
        self.console_lock = threading.Lock()
        self.console_input_active = False
        self.console_deferred_lines = []
        self.console_deferred_dropped = 0
        self.console_deferred_limit = 256

        threading.Thread(target=self._receiver_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _emit_console_line(self, line: str, defer_while_input: bool = True):
        if not line:
            return

        with self.console_lock:
            if defer_while_input and self.console_input_active:
                if len(self.console_deferred_lines) < self.console_deferred_limit:
                    self.console_deferred_lines.append(line)
                else:
                    self.console_deferred_dropped += 1
                return
        print(line)

    def begin_console_input(self):
        with self.console_lock:
            self.console_input_active = True

    def end_console_input(self):
        deferred = []
        dropped = 0
        with self.console_lock:
            self.console_input_active = False
            if self.console_deferred_lines:
                deferred = self.console_deferred_lines
                self.console_deferred_lines = []
            dropped = self.console_deferred_dropped
            self.console_deferred_dropped = 0

        for line in deferred:
            print(line)
        if dropped > 0:
            print(f"[LOG] {dropped} background lines dropped while typing.")

    def _handle_rwvg_typed_frame(self, typed_kind, typed_payload):
        now_ts = time.monotonic()
        if typed_kind == RWVG_TYPE_UTILS:
            self.rwvg_stats["utils_frames"] += 1
            parsed = parse_rwvg_utils_payload(typed_payload)
            if parsed is not None:
                with self.radar_lock:
                    self.radar_latest_utils = parsed
                    self.radar_latest_utils_ts = now_ts
        elif typed_kind == RWVG_TYPE_PLAYER:
            self.rwvg_stats["player_frames"] += 1
            parsed = parse_rwvg_player_payload(typed_payload)
            if parsed is not None:
                entity_id = self._build_player_entity_id(parsed)
                parsed["_entity_id"] = entity_id
                parsed["_ts"] = now_ts
                self._append_send_thread_log({
                    "ts": now_ts,
                    "kind": "player",
                    "entity_id": entity_id,
                    "team_id": int(parsed.get("team_id", 0) or 0),
                    "health": _safe_wire_float(parsed.get("health", 0.0)),
                    "max_health": _safe_wire_float(parsed.get("max_health", 0.0)),
                    "distance": int(parsed.get("distance", 0) or 0),
                    "visible": bool(parsed.get("is_visible", False)),
                    "pos": _safe_wire_pos_dict(parsed.get("pos") or {}),
                    "name": str(parsed.get("player_name") or ""),
                    "weapon": str(parsed.get("weapon_name") or ""),
                })
                with self.radar_lock:
                    self.radar_players[entity_id] = parsed
                    self._purge_radar_stale_locked(now_ts)
        elif typed_kind == RWVG_TYPE_ITEM:
            self.rwvg_stats["item_frames"] += 1
            parsed = parse_rwvg_item_payload(typed_payload)
            if parsed is not None:
                item_key = f"{parsed.get('dead_box_type', 0)}:{now_ts:.6f}"
                parsed["_ts"] = now_ts
                with self.radar_lock:
                    self.radar_items[item_key] = parsed
                    self._purge_radar_stale_locked(now_ts)
        self.rwvg_stats["typed_bytes"] += len(typed_payload)

        if not self.rwvg_stream_detected:
            self._emit_console_line("[+] RWVG typed stream detected (GameCore data path aligned).")
            self.rwvg_stream_detected = True

    def _build_player_entity_id(self, player: dict) -> str:
        entity_ptr = int(player.get("entity_ptr", 0) or 0)
        if entity_ptr != 0:
            return f"0x{entity_ptr:X}"

        team_id = int(player.get("team_id", 0) or 0)
        player_name = str(player.get("player_name") or "")
        detective = str(player.get("detective") or "")
        return f"FALLBACK_{team_id}_{player_name}_{detective}"

    def _purge_radar_stale_locked(self, now_ts: float):
        cutoff = now_ts - max(self.radar_player_ttl_sec, 0.25)

        stale_players = [
            key
            for key, value in self.radar_players.items()
            if float(value.get("_ts", 0.0)) < cutoff
        ]
        for key in stale_players:
            self.radar_players.pop(key, None)

        stale_items = [
            key
            for key, value in self.radar_items.items()
            if float(value.get("_ts", 0.0)) < cutoff
        ]
        for key in stale_items:
            self.radar_items.pop(key, None)

    @staticmethod
    def _calculate_local_yaw(matrix_values):
        if not matrix_values or len(matrix_values) < 5:
            return 0.0
        yaw_rad = math.atan2(matrix_values[4], matrix_values[0])
        yaw_deg = yaw_rad * (180.0 / math.pi)
        yaw_deg -= 90.0
        if yaw_deg < 0.0:
            yaw_deg += 360.0
        if yaw_deg >= 360.0:
            yaw_deg -= 360.0
        return yaw_deg

    def get_radar_snapshot(self):
        now_ts = time.monotonic()
        with self.radar_lock:
            self._purge_radar_stale_locked(now_ts)
            utils = dict(self.radar_latest_utils or {})
            players = [dict(player) for player in self.radar_players.values()]
            utils_ts = float(self.radar_latest_utils_ts or 0.0)

        local_team_id = int(utils.get("local_team_id", 0) or 0)
        local_pos = utils.get("local_pos") or {}
        local_neck_pos = utils.get("local_neck_pos") or {}
        local_player = {
            "id": "local",
            "team_id": local_team_id,
            "camp_id": 0,
            "yaw": self._calculate_local_yaw(utils.get("matrix")),
            "position": _safe_pos_dict(local_pos),
            "neck_position": _safe_pos_dict(local_neck_pos),
        }

        entities = []
        teammates = []
        for player in players:
            class_name = str(player.get("class_name") or "")
            is_actual_player = class_name != "AI"
            if not class_name or not is_actual_player:
                continue

            entity_id = str(player.get("_entity_id") or self._build_player_entity_id(player))
            player_name = str(player.get("player_name") or "")
            pos = player.get("pos") or {}
            entity = {
                "id": entity_id,
                "name": player_name if player_name else f"Player_{entity_id}",
                "type": "player",
                "team_id": int(player.get("team_id", 0) or 0),
                "position": _safe_pos_dict(pos),
                "orientation": _safe_float(player.get("direction", 0.0)),
                "health": _safe_float(player.get("health", 0.0)),
                "max_health": _safe_float(player.get("max_health", 0.0)),
            }
            entities.append(entity)

            if local_team_id > 0 and entity["team_id"] == local_team_id:
                teammates.append(dict(entity))

        return {
            "meta": {
                "utils_present": bool(utils),
                "utils_age_ms": max(0, int((now_ts - utils_ts) * 1000.0)) if utils_ts > 0.0 else -1,
                "player_count": len(entities),
                "teammate_count": len(teammates),
            },
            "local_player": local_player,
            "entities": entities,
            "teammates": teammates,
        }

    def _handle_zombie_ack_packet(self, payload):
        ack = parse_zombie_control_ack(payload)
        if ack is None:
            return False

        self.rwvg_stats["zombie_ack_packets"] += 1
        self.rwvg_stats["zombie_last_ack"] = ack
        if ack != ZOMBIE_ACK_OK:
            self.rwvg_stats["zombie_ack_non_ok"] += 1
        return True

    def get_stream_stats(self):
        return dict(self.rwvg_stats)

    def _append_host_log(self, category: str, raw: str, parsed: dict):
        with self.host_lock:
            self.host_diag[category] = parsed
            recent = self.host_diag["recent_logs"]
            recent.append({"category": category, "raw": raw, "parsed": parsed})
            if len(recent) > self.trace_history_limit:
                del recent[:-self.trace_history_limit]
        if category in self.DECODE_PATH_CATEGORIES:
            self._append_decode_path_log(category, raw, parsed)

    def _append_send_thread_log(self, item: dict):
        if not item:
            return
        with self.trace_lock:
            self.send_thread_history.append(item)
            if len(self.send_thread_history) > self.trace_history_limit:
                del self.send_thread_history[:-self.trace_history_limit]

    def _append_decode_path_log(self, category: str, raw: str, parsed: dict):
        with self.trace_lock:
            self.decode_path_history.append({
                "ts": time.monotonic(),
                "category": category,
                "raw": raw,
                "parsed": parsed,
            })
            if len(self.decode_path_history) > self.trace_history_limit:
                del self.decode_path_history[:-self.trace_history_limit]

    def _try_capture_rwbase_host_log(self, msg: str):
        if not msg:
            return False

        match = self.TEST_OFFSETS_RE.match(msg)
        if match:
            parsed = {
                "uworld": int(match.group(1), 16),
                "fnames": int(match.group(2), 16),
                "local_player_ptr": int(match.group(3), 16),
                "player_controller_offset": int(match.group(4), 16),
                "acknowledged_pawn_offset": int(match.group(5), 16),
                "mesh_offset": int(match.group(6), 16),
                "bone_array_offset": int(match.group(7), 16),
                "component_to_world_offset": int(match.group(8), 16),
                "player_state_offset": int(match.group(9), 16),
                "team_offset": int(match.group(10), 16),
            }
            self._append_host_log("offsets", msg, parsed)
            return True

        match = self.TRACK_READY_RE.match(msg)
        if match:
            parsed = {
                "pid": int(match.group(1), 16),
                "old_pid": int(match.group(2), 16),
                "user_cr3": int(match.group(3), 16),
                "kernel_cr3": int(match.group(4), 16),
                "base": int(match.group(5), 16),
                "network": match.group(6),
            }
            self._append_host_log("track_ready", msg, parsed)
            return True

        match = self.TEST_CHAIN_RE.match(msg)
        if match:
            parsed = {
                "uworld": int(match.group(1), 16),
                "fnames": int(match.group(2), 16),
                "local_player_ptr": int(match.group(3), 16),
                "player_controller": int(match.group(4), 16),
                "pawn": int(match.group(5), 16),
                "player_state": int(match.group(6), 16),
                "team_id": int(match.group(7)),
                "mesh": int(match.group(8), 16),
                "acknowledged_pawn_offset": int(match.group(9), 16),
            }
            self._append_host_log("chain", msg, parsed)
            return True

        match = self.EMU_RUNTIME_INIT_RE.match(msg)
        if match:
            parsed = {
                "status": int(match.group(1), 16),
                "ready": int(match.group(2)),
            }
            self._append_host_log("emu_runtime_init", msg, parsed)
            return True

        match = self.EMU_RUNTIME_EXEC_FAIL_RE.match(msg)
        if match:
            parsed = {
                "status": int(match.group(1), 16),
                "exit": int(match.group(2)),
                "cpueaxh": int(match.group(3)),
                "exception": int(match.group(4)),
                "rip": int(match.group(5), 16),
                "cr3": int(match.group(6), 16),
            }
            self._append_host_log("emu_runtime_exec_fail", msg, parsed)
            return True

        match = self.EMU_BRIDGE_RE.match(msg)
        if match:
            parsed = {
                "kind": match.group(1),
                "details": match.group(2),
            }
            self._append_host_log("emu_bridge_last", msg, parsed)
            return True

        match = self.EMU_CALL_RE.match(msg)
        if match:
            parsed = {
                "kind": match.group(1),
                "details": match.group(2),
            }
            self._append_host_log("emu_call_last", msg, parsed)
            return True

        match = self.COORD_DECRYPT_FAIL_SUMMARY_RE.match(msg)
        if match:
            parsed = {
                "frame": int(match.group(1)),
                "total": int(match.group(2)),
                "groups": int(match.group(3)),
                "dropped": int(match.group(4)),
                "updated_at_monotonic": time.monotonic(),
            }
            with self.host_lock:
                self.host_diag["coord_decode_summary"] = parsed
                self.host_diag["coord_decode_groups"] = []
                recent = self.host_diag["recent_logs"]
                recent.append({"category": "coord_decode_summary", "raw": msg, "parsed": parsed})
                if len(recent) > 32:
                    del recent[:-32]
            return True

        match = self.COORD_DECRYPT_FAIL_GROUP_RE.match(msg)
        if match:
            parsed = {
                "rank": int(match.group(1)),
                "count": int(match.group(2)),
                "type": match.group(3),
                "strategy": match.group(4),
                "algo": int(match.group(5)),
                "kind": int(match.group(6)),
                "source": match.group(7),
                "confidence": match.group(8),
                "rcode": match.group(9),
                "rcode_num": int(match.group(10)),
                "last_entry": int(match.group(11), 16),
                "pid": int(match.group(12), 16),
                "cr3": int(match.group(13), 16),
            }
            with self.host_lock:
                groups = self.host_diag["coord_decode_groups"]
                if len(groups) < 8:
                    groups.append(parsed)
                recent = self.host_diag["recent_logs"]
                recent.append({"category": "coord_decode_group", "raw": msg, "parsed": parsed})
                if len(recent) > 32:
                    del recent[:-32]
            return True

        return False

    def get_rwbase_host_diag(self):
        with self.host_lock:
            return {
                "offsets": None if self.host_diag["offsets"] is None else dict(self.host_diag["offsets"]),
                "track_ready": None if self.host_diag["track_ready"] is None else dict(self.host_diag["track_ready"]),
                "chain": None if self.host_diag["chain"] is None else dict(self.host_diag["chain"]),
                "emu_runtime_init": None if self.host_diag["emu_runtime_init"] is None else dict(self.host_diag["emu_runtime_init"]),
                "emu_runtime_exec_fail": None if self.host_diag["emu_runtime_exec_fail"] is None else dict(self.host_diag["emu_runtime_exec_fail"]),
                "emu_bridge_last": None if self.host_diag["emu_bridge_last"] is None else dict(self.host_diag["emu_bridge_last"]),
                "emu_call_last": None if self.host_diag["emu_call_last"] is None else dict(self.host_diag["emu_call_last"]),
                "coord_decode_summary": None if self.host_diag["coord_decode_summary"] is None else dict(self.host_diag["coord_decode_summary"]),
                "coord_decode_groups": [dict(item) for item in self.host_diag["coord_decode_groups"]],
                "recent_logs": list(self.host_diag["recent_logs"]),
            }

    def get_send_thread_history(self, limit: int = 50):
        n = max(1, int(limit))
        with self.trace_lock:
            return [dict(item) for item in self.send_thread_history[-n:]]

    def get_decode_path_history(self, limit: int = 80):
        n = max(1, int(limit))
        with self.trace_lock:
            out = []
            for item in self.decode_path_history[-n:]:
                out.append({
                    "ts": float(item.get("ts", 0.0) or 0.0),
                    "category": str(item.get("category") or ""),
                    "raw": str(item.get("raw") or ""),
                    "parsed": dict(item.get("parsed") or {}),
                })
            return out

    def _receiver_loop(self):
        self._emit_console_line(f"[*] UDP Receiver started on port {BIND_PORT}", defer_while_input=False)
        scratch_buffer = bytearray(65536)

        while self.is_running:
            try:
                nbytes = self.sock.recv_into(scratch_buffer)
                if nbytes < 1:
                    continue

                pkt_type = scratch_buffer[0]

                if pkt_type == PACKET_TYPE_LOG:
                    try:
                        msg = scratch_buffer[1:nbytes].decode("utf-8", errors="ignore").strip()
                        self._try_capture_module_log(msg)
                        consumed_region_log = self._try_capture_region_log(msg)
                        self._try_capture_rwbase_host_log(msg)
                        if "ALIVE_ACK" in msg or "DRIVER_ONLINE" in msg:
                            if not self.driver_online:
                                self._emit_console_line("[+] Driver is ONLINE.")
                            self.driver_online = True
                            continue

                        if consumed_region_log:
                            continue

                        if msg:
                            self._emit_console_line(f"[LOG] {msg}")
                    except Exception:
                        pass
                    continue

                if pkt_type == PACKET_TYPE_DATA:
                    payload = scratch_buffer[1:nbytes]
                    typed_parsed = try_parse_rwvg_typed_payload(payload)
                    if typed_parsed is not None:
                        typed_kind, typed_payload = typed_parsed
                        self._handle_rwvg_typed_frame(typed_kind, typed_payload)
                        continue

                    if self.expected_size > 0:
                        payload_len = len(payload)
                        self.rwvg_stats["command_bytes"] += payload_len
                        self.last_untyped_data_ts = time.monotonic()

                        if self.recvd_bytes + payload_len <= self.expected_size:
                            self.view[self.recvd_bytes:self.recvd_bytes + payload_len] = payload
                            self.recvd_bytes += payload_len
                            self.last_data_ts = time.monotonic()
                        else:
                            self.rwvg_stats["dropped_data_packets"] += 1

                        if self.recvd_bytes >= self.expected_size:
                            self.expected_size = 0
                            self.recv_event.set()
                    else:
                        self.last_untyped_data_ts = time.monotonic()
                        if self._handle_zombie_ack_packet(payload):
                            continue
                        self.rwvg_stats["dropped_data_packets"] += 1
                    continue

                host_agg = try_parse_host_aggregate_payload(scratch_buffer[:nbytes])
                if host_agg is not None:
                    self.rwvg_stats["host_aggregate_frames"] += 1
                    self.rwvg_stats["host_aggregate_raw_bytes"] += host_agg["raw_size"]
                    if not self.host_aggregate_detected:
                        self._emit_console_line(
                            "[+] Host-compat aggregate stream detected "
                            f"(players={host_agg['player_count']}, items={host_agg['item_count']})."
                        )
                        self.host_aggregate_detected = True
            except Exception:
                if not self.is_running:
                    break

    def _try_capture_module_log(self, msg: str):
        if not msg:
            return
        match = self.MODULE_LOG_RE.match(msg)
        if not match:
            return
        try:
            base = int(match.group(1), 16)
            size = int(match.group(2), 16)
            name = match.group(3).strip()
            if base == 0 or size == 0 or not name:
                return
        except Exception:
            return
        entry = {"base": base, "size": size, "name": name}
        with self.module_lock:
            self.module_entries.append(entry)

    def clear_module_snapshot(self):
        with self.module_lock:
            self.module_entries.clear()

    def get_module_snapshot(self):
        with self.module_lock:
            return list(self.module_entries)

    def _try_capture_region_log(self, msg: str):
        if not msg:
            return False
        match = self.REGION_LOG_RE.match(msg)
        if match:
            try:
                entry = {
                    "base": int(match.group(1), 16),
                    "size": int(match.group(2), 16),
                    "state": int(match.group(3), 16),
                    "protect": int(match.group(4), 16),
                    "type": int(match.group(5), 16),
                }
            except Exception:
                return False
            with self.region_lock:
                self.region_entries.append(entry)
            return True

        done = self.REGION_DONE_RE.match(msg)
        if done:
            try:
                count = int(done.group(2))
            except Exception:
                count = 0
            with self.region_lock:
                self.region_enum_done = True
                self.region_enum_count = count
            return True
        return False

    def clear_region_snapshot(self):
        with self.region_lock:
            self.region_entries.clear()
            self.region_enum_done = False
            self.region_enum_count = 0

    def get_region_snapshot(self):
        with self.region_lock:
            return list(self.region_entries), self.region_enum_done, self.region_enum_count

    def _heartbeat_loop(self):
        while self.is_running:
            try:
                payload = b"HELO".ljust(32, b"\x00")
                self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
                self.seq += 1
                time.sleep(1.0)
            except Exception:
                pass

    def request_bytes(self, payload, size, timeout=3.0):
        with self.lock:
            if self.last_request_timed_out:
                quiet_window_sec = 0.6
                max_quiet_wait_sec = 6.0
                quiet_start = time.monotonic()
                while (time.monotonic() - self.last_untyped_data_ts) < quiet_window_sec:
                    if (time.monotonic() - quiet_start) >= max_quiet_wait_sec:
                        print(
                            "[-] RX channel still busy with previous command stream. "
                            "Refusing to start a new request."
                        )
                        return None
                    time.sleep(0.05)

            self.recv_event.clear()

            self.buffer = bytearray(size)
            self.view = memoryview(self.buffer)
            self.recvd_bytes = 0
            start_ts = time.monotonic()
            self.last_data_ts = start_ts
            self.expected_size = size

            self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))

            requested_timeout = max(float(timeout), 1.0)
            transfer_budget = (size / DEFAULT_EXPECTED_TRANSFER_BPS) + DEFAULT_TRANSFER_GRACE_SEC
            total_timeout = max(requested_timeout, transfer_budget)
            idle_timeout = max(DEFAULT_IDLE_TIMEOUT_SEC, requested_timeout / 2.0)
            deadline_ts = start_ts + total_timeout
            if VERBOSE_EXPECTING_LOG:
                print(
                    f"[*] Expecting {size} bytes, timeout set to {total_timeout:.1f}s "
                    f"(idle {idle_timeout:.1f}s)"
                )

            timeout_reason = "transfer timeout"
            while True:
                if self.recv_event.wait(timeout=WAIT_SLICE_SEC):
                    self.last_request_timed_out = False
                    return self.buffer

                now_ts = time.monotonic()
                if now_ts >= deadline_ts:
                    timeout_reason = "transfer timeout"
                    break

                if self.recvd_bytes > 0 and (now_ts - self.last_data_ts) >= idle_timeout:
                    timeout_reason = f"idle timeout ({idle_timeout:.1f}s no new packets)"
                    break

            self.expected_size = 0
            self.last_request_timed_out = True
            percent = (self.recvd_bytes / size) * 100 if size > 0 else 0.0
            print(
                f"[-] Timeout ({timeout_reason})! Received "
                f"{self.recvd_bytes}/{size} bytes ({percent:.1f}%)."
            )
            return None
