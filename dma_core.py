import base64
import datetime
import json
import logging
import socket
import threading
import time
import os
import re
import math
import queue
from dma_protocol import *
from item_catalog import describe_item

DEFAULT_EXPECTED_TRANSFER_BPS = 8 * 1024 * 1024  # 8 MB/s conservative baseline.
DEFAULT_TRANSFER_GRACE_SEC = 5.0
DEFAULT_IDLE_TIMEOUT_SEC = 2.5
WAIT_SLICE_SEC = 0.2
VERBOSE_EXPECTING_LOG = os.getenv("DMA_VERBOSE_EXPECTING", "0") == "1"
DEFAULT_PLAYER_TTL_SEC = float(os.getenv("DMA_WEBRADAR_PLAYER_TTL", "1.5"))
RWBASE_DECRYPT_LOG_ENABLED = os.getenv("RWBASE_DECRYPT_LOG", "1").strip().lower() not in ("0", "false", "off", "no")
RWBASE_DECRYPT_LOG_PREFIX = "[CoordDecryptDebug][B64] "
COORD_RAW_LOG_PREFIX = "[COORDRAW][SEND] "
RWBASE_DECRYPT_QUEUE_CAPACITY = 1024
RWBASE_DECRYPT_FALLBACK_PATH = os.getenv(
    "RWBASE_DECRYPT_FALLBACK_PATH",
    "/var/log/rwbase/decrypt_fallback.log",
)
RWBASE_DECRYPT_FALLBACK_MAX_BYTES = 100 * 1024 * 1024
RWBASE_DECRYPT_FALLBACK_RETENTION_DAYS = 7

rwbase_decrypt_logger = logging.getLogger("rwbase_decrypt")
rwbase_decrypt_logger.addHandler(logging.NullHandler())
rwbase_decrypt_logger.setLevel(logging.DEBUG if RWBASE_DECRYPT_LOG_ENABLED else logging.CRITICAL)


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


def _utc_iso8601_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DMACore:
    MODULE_LOG_RE = re.compile(
        r"^\[User\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+Name:\s+(.+)$"
    )
    REGION_LOG_RE = re.compile(
        r"^\[UserRegion\]\s+Base:\s+0x([0-9A-Fa-f]+)\s+\|\s+Size:\s+0x([0-9A-Fa-f]+)\s+\|\s+State:\s+0x([0-9A-Fa-f]+)\s+\|\s+Protect:\s+0x([0-9A-Fa-f]+)\s+\|\s+Type:\s+0x([0-9A-Fa-f]+)$"
    )
    REGION_DONE_RE = re.compile(
        r"^\[UserRegion\]\s+Done\s+PID=([0-9A-Fa-fx]+)\s+Count=(\d+)$"
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
            "player_batch_frames": 0,
            "item_batch_frames": 0,
            "player_batch_entities": 0,
            "item_batch_entities": 0,
            "actor_scan_frames": 0,
            "actor_scan_entities": 0,
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
        self.decrypt_lock = threading.Lock()
        self.decrypt_diag = {
            "enabled": RWBASE_DECRYPT_LOG_ENABLED,
            "stats": {
                "total": 0,
                "success": 0,
                "fail": 0,
                "queue_dropped": 0,
                "decode_errors": 0,
                "worker_errors": 0,
                "last_perf_us": 0.0,
            },
            "fail_kinds": {},
            "recent": [],
        }
        self.decrypt_log_queue = queue.Queue(maxsize=RWBASE_DECRYPT_QUEUE_CAPACITY)
        self.coord_raw_lock = threading.Lock()
        self.coord_raw_diag = {
            "stats": {
                "total": 0,
            },
            "recent": [],
        }
        self.trace_lock = threading.Lock()
        self.send_thread_history = []
        self.trace_history_limit = 512
        self.radar_lock = threading.Lock()
        self.radar_latest_utils = None
        self.radar_latest_utils_ts = 0.0
        self.radar_players = {}
        self.radar_items = {}
        self.radar_player_ttl_sec = DEFAULT_PLAYER_TTL_SEC
        # Actor 分类转储 (Type=6) 快照：按 entity 指针去重，保留最近若干条
        self.actor_scan_lock = threading.Lock()
        self.actor_scan_entities = {}        # entity 指针 -> 解析后的 record dict
        self.actor_scan_order = []           # 保持插入顺序，便于按时间近似倒序展示
        self.actor_scan_max_entities = 2000  # 与 RWVG_ACTOR_SCAN_MAX_RECORDS 保持同量级
        self.actor_scan_last_ts = 0.0        # 最近一帧的 monotonic 时间戳
        self.actor_scan_last_count = 0       # 最近一帧解析到的记录数
        self.actor_scan_frames = 0           # 累计收到的 Type=6 帧数
        self.console_lock = threading.Lock()
        self.console_input_active = False
        self.console_deferred_lines = []
        self.console_deferred_dropped = 0
        self.console_deferred_limit = 256

        threading.Thread(target=self._receiver_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        if RWBASE_DECRYPT_LOG_ENABLED:
            threading.Thread(target=self._decrypt_log_worker, daemon=True).start()

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

    def _record_player_radar(self, parsed: dict, now_ts: float):
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
            "name": str(parsed.get("player_name") or parsed.get("bot_name") or ""),
            "weapon": str(parsed.get("weapon_name") or ""),
        })
        with self.radar_lock:
            self.radar_players[entity_id] = parsed
            self._purge_radar_stale_locked(now_ts)

    def _record_item_radar(self, parsed: dict, now_ts: float):
        item_key = self._build_item_entity_id(parsed, now_ts)
        parsed["_ts"] = now_ts
        parsed["_entity_id"] = item_key
        self._append_send_thread_log({
            "ts": now_ts,
            "kind": "item",
            "entity_id": item_key,
            "item_type": int(parsed.get("item_type", 0) or 0),
            "dead_box_type": int(parsed.get("dead_box_type", 0) or 0),
            "distance": int(parsed.get("distance", 0) or 0),
            "pos": _safe_wire_pos_dict(parsed.get("pos") or {}),
        })
        with self.radar_lock:
            self.radar_items[item_key] = parsed
            self._purge_radar_stale_locked(now_ts)

    def _handle_player_payload(self, typed_payload: bytes, now_ts: float) -> int:
        parsed = parse_rwvg_player_payload(typed_payload)
        if parsed is None:
            return 0
        self._record_player_radar(parsed, now_ts)
        return 1

    def _handle_item_payload(self, typed_payload: bytes, now_ts: float) -> int:
        parsed = parse_rwvg_item_payload(typed_payload)
        if parsed is None:
            return 0
        self._record_item_radar(parsed, now_ts)
        return 1

    def _handle_player_batch_payload(self, typed_payload: bytes, now_ts: float) -> int:
        payloads = parse_rwvg_batch_payload(typed_payload, RWVG_TYPED_SIZE_BY_KIND[RWVG_TYPE_PLAYER])
        if payloads is None:
            return 0
        self.rwvg_stats["player_batch_frames"] += 1
        handled = 0
        for payload in payloads:
            handled += self._handle_player_payload(payload, now_ts)
        self.rwvg_stats["player_batch_entities"] += handled
        return handled

    def _handle_item_batch_payload(self, typed_payload: bytes, now_ts: float) -> int:
        payloads = parse_rwvg_batch_payload(typed_payload, RWVG_TYPED_SIZE_BY_KIND[RWVG_TYPE_ITEM])
        if payloads is None:
            return 0
        self.rwvg_stats["item_batch_frames"] += 1
        handled = 0
        for payload in payloads:
            handled += self._handle_item_payload(payload, now_ts)
        self.rwvg_stats["item_batch_entities"] += handled
        return handled

    def _handle_actor_scan_payload(self, typed_payload: bytes, now_ts: float) -> int:
        # 解析 Type=6 Actor 分类转储帧，按 entity 指针去重后写入快照。
        parsed = parse_rwvg_actor_scan_payload(typed_payload)
        if parsed is None:
            return 0

        records = parsed.get("records") or []
        self.rwvg_stats["actor_scan_frames"] += 1
        self.rwvg_stats["actor_scan_entities"] += len(records)
        self.actor_scan_frames += 1
        self.actor_scan_last_count = len(records)
        self.actor_scan_last_ts = now_ts

        with self.actor_scan_lock:
            order = self.actor_scan_order
            entities = self.actor_scan_entities
            for record in records:
                entity = int(record.get("entity", 0) or 0)
                stamped = dict(record)
                stamped["_ts"] = now_ts
                if entity in entities:
                    # 已存在：刷新值并提到队首（移动到末尾，表示最近更新）
                    order.remove(entity)
                entities[entity] = stamped
                order.append(entity)

            # 超过容量上限：按最早插入顺序裁剪
            overflow = len(order) - self.actor_scan_max_entities
            if overflow > 0:
                evicted = order[:overflow]
                del order[:overflow]
                for entity in evicted:
                    entities.pop(entity, None)

        return len(records)

    def get_actor_scan_snapshot(self, limit: int = 2000):
        # 返回最近的 Actor 扫描快照（按 entity 指针去重后的列表），供 WebActorKindsService 使用。
        with self.actor_scan_lock:
            order = list(self.actor_scan_order)
            entities = self.actor_scan_entities

        n = max(0, int(limit))
        # 最近的（队尾）排在前面
        selected = order[-n:] if n else list(order)
        selected.reverse()
        return {
            "actors": [dict(entities[entity]) for entity in selected if entity in entities],
            "count": len(entities),
            "frames": int(self.actor_scan_frames),
            "last_ts": float(self.actor_scan_last_ts or 0.0),
            "last_count": int(self.actor_scan_last_count),
            "has_data": bool(entities),
        }

    def _start_current_rwvg_snapshot(self, parsed: dict, now_ts: float):
        self._append_send_thread_log({
            "ts": now_ts,
            "kind": "local",
            "entity_id": "local",
            "team_id": int(parsed.get("local_team_id", 0) or 0),
            "weapon_id": int(parsed.get("local_weapon_id", 0) or 0),
            "pos": _safe_wire_pos_dict(parsed.get("local_pos") or {}),
        })
        with self.radar_lock:
            self.radar_latest_utils = parsed
            self.radar_latest_utils_ts = now_ts
            self.radar_players = {}
            self.radar_items = {}

    def _handle_rwvg_typed_frame(self, typed_kind, typed_payload):
        now_ts = time.monotonic()
        if typed_kind == RWVG_TYPE_UTILS:
            self.rwvg_stats["utils_frames"] += 1
            parsed = parse_rwvg_utils_payload(typed_payload)
            if parsed is not None:
                self._start_current_rwvg_snapshot(parsed, now_ts)
        elif typed_kind == RWVG_TYPE_PLAYER:
            self.rwvg_stats["player_frames"] += 1
            self._handle_player_payload(typed_payload, now_ts)
        elif typed_kind == RWVG_TYPE_ITEM:
            self.rwvg_stats["item_frames"] += 1
            self._handle_item_payload(typed_payload, now_ts)
        elif typed_kind == RWVG_TYPE_PLAYER_BATCH:
            self._handle_player_batch_payload(typed_payload, now_ts)
        elif typed_kind == RWVG_TYPE_ITEM_BATCH:
            self._handle_item_batch_payload(typed_payload, now_ts)
        elif typed_kind == RWVG_TYPE_ACTOR_SCAN:
            self._handle_actor_scan_payload(typed_payload, now_ts)
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

    def _build_item_entity_id(self, item: dict, now_ts: float) -> str:
        pos = item.get("pos") or {}
        item_type = int(item.get("item_type", 0) or 0)
        dead_box_type = int(item.get("dead_box_type", 0) or 0)
        x = int(_safe_float(pos.get("x", 0.0)))
        y = int(_safe_float(pos.get("y", 0.0)))
        return f"item:{item_type}:{dead_box_type}:{x}:{y}:{now_ts:.3f}"

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
            items = [dict(item) for item in self.radar_items.values()]
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
        actual_player_count = 0
        ai_count = 0
        for player in players:
            class_name = str(player.get("class_name") or "")
            if not class_name:
                continue

            is_ai = class_name == "AI"
            entity_type = "ai" if is_ai else "player"
            if is_ai:
                ai_count += 1
            else:
                actual_player_count += 1

            entity_id = str(player.get("_entity_id") or self._build_player_entity_id(player))
            player_name = str(player.get("player_name") or "")
            bot_name = str(player.get("bot_name") or "")
            pos = player.get("pos") or {}
            entity = {
                "id": entity_id,
                "name": bot_name if is_ai and bot_name else (player_name if player_name else f"{entity_type.title()}_{entity_id}"),
                "type": entity_type,
                "class_name": class_name,
                "team_id": int(player.get("team_id", 0) or 0),
                "position": _safe_pos_dict(pos),
                "orientation": _safe_float(player.get("direction", 0.0)),
                "health": _safe_float(player.get("health", 0.0)),
                "max_health": _safe_float(player.get("max_health", 0.0)),
            }
            entities.append(entity)

            if (not is_ai) and local_team_id > 0 and entity["team_id"] == local_team_id:
                teammates.append(dict(entity))

        item_snapshots = []
        for raw_item in items:
            item = describe_item(raw_item)
            item_snapshots.append({
                "id": str(item.get("_entity_id") or ""),
                "type": str(item.get("type") or "item"),
                "item_type": int(item.get("item_type", 0) or 0),
                "item_name": str(item.get("item_name") or ""),
                "item_quality": int(item.get("item_quality", 0) or 0),
                "item_quality_label": str(item.get("item_quality_label") or ""),
                "item_quality_color": str(item.get("item_quality_color") or ""),
                "item_money": int(item.get("item_money", 0) or 0),
                "dead_box_type": int(item.get("dead_box_type", 0) or 0),
                "dead_box_name": str(item.get("dead_box_name") or ""),
                "distance": int(item.get("distance", 0) or 0),
                "position": _safe_pos_dict(item.get("pos")),
                "orientation": 0,
            })

        return {
            "meta": {
                "utils_present": bool(utils),
                "utils_age_ms": max(0, int((now_ts - utils_ts) * 1000.0)) if utils_ts > 0.0 else -1,
                "entity_count": len(entities),
                "item_count": len(item_snapshots),
                "player_count": actual_player_count,
                "ai_count": ai_count,
                "teammate_count": len(teammates),
            },
            "local_player": local_player,
            "entities": entities,
            "items": item_snapshots,
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

    def _append_send_thread_log(self, item: dict):
        if not item:
            return
        with self.trace_lock:
            self.send_thread_history.append(item)
            if len(self.send_thread_history) > self.trace_history_limit:
                del self.send_thread_history[:-self.trace_history_limit]

    def _rotate_decrypt_fallback_if_needed(self):
        path = RWBASE_DECRYPT_FALLBACK_PATH
        if not path:
            return
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) >= RWBASE_DECRYPT_FALLBACK_MAX_BYTES:
            rotated = f"{path}.{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            os.replace(path, rotated)

        cutoff = time.time() - (RWBASE_DECRYPT_FALLBACK_RETENTION_DAYS * 86400)
        prefix = os.path.basename(path) + "."
        for name in os.listdir(directory or "."):
            full = os.path.join(directory, name) if directory else name
            if not name.startswith(prefix):
                continue
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                continue

    def _write_decrypt_fallback(self, payload: dict):
        try:
            self._rotate_decrypt_fallback_if_needed()
            with open(RWBASE_DECRYPT_FALLBACK_PATH, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _record_decrypt_diag(self, event: dict, perf_us: float):
        status = str(event.get("status") or "").lower()
        fail_kind = str(event.get("fail_kind") or "")
        compact = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        if rwbase_decrypt_logger.isEnabledFor(logging.DEBUG):
            rwbase_decrypt_logger.debug(compact)

        with self.decrypt_lock:
            stats = self.decrypt_diag["stats"]
            stats["total"] += 1
            if status == "success":
                stats["success"] += 1
            elif status == "fail":
                stats["fail"] += 1
            stats["last_perf_us"] = perf_us
            if fail_kind:
                fail_kinds = self.decrypt_diag["fail_kinds"]
                fail_kinds[fail_kind] = int(fail_kinds.get(fail_kind, 0)) + 1
            recent = self.decrypt_diag["recent"]
            recent.append({
                "ts_utc": event.get("ts_utc"),
                "stage": event.get("stage"),
                "status": event.get("status"),
                "entity": event.get("entity"),
                "fail_kind": event.get("fail_kind"),
                "perf_us": perf_us,
                "raw": compact,
            })
            if len(recent) > self.trace_history_limit:
                del recent[:-self.trace_history_limit]

    def _decrypt_log_worker(self):
        while self.is_running:
            try:
                event = self.decrypt_log_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            perf_begin = time.perf_counter()
            try:
                self._record_decrypt_diag(event, (time.perf_counter() - perf_begin) * 1_000_000.0)
            except Exception as exc:
                with self.decrypt_lock:
                    self.decrypt_diag["stats"]["worker_errors"] += 1
                self._write_decrypt_fallback({
                    "ts_utc": _utc_iso8601_now(),
                    "reason": "worker_error",
                    "error": str(exc),
                    "event": event,
                })

    def _try_capture_rwbase_decrypt_log(self, msg: str):
        if not msg.startswith(RWBASE_DECRYPT_LOG_PREFIX):
            return False
        if not RWBASE_DECRYPT_LOG_ENABLED:
            return True

        perf_begin = time.perf_counter()
        encoded = msg[len(RWBASE_DECRYPT_LOG_PREFIX):].strip()
        try:
            decoded = base64.b64decode(encoded, validate=True)
            event = json.loads(decoded.decode("utf-8"))
            event["_recv_ts_utc"] = _utc_iso8601_now()
            event["_ingest_perf_us"] = (time.perf_counter() - perf_begin) * 1_000_000.0
        except Exception as exc:
            with self.decrypt_lock:
                self.decrypt_diag["stats"]["decode_errors"] += 1
            self._write_decrypt_fallback({
                "ts_utc": _utc_iso8601_now(),
                "reason": "decode_error",
                "error": str(exc),
                "raw": msg,
            })
            return True

        try:
            self.decrypt_log_queue.put_nowait(event)
        except queue.Full:
            with self.decrypt_lock:
                self.decrypt_diag["stats"]["queue_dropped"] += 1
            self._write_decrypt_fallback({
                "ts_utc": _utc_iso8601_now(),
                "reason": "queue_full",
                "event": event,
            })
        return True

    def _try_capture_coord_raw_log(self, msg: str):
        if not msg.startswith(COORD_RAW_LOG_PREFIX):
            return False

        body = msg[len(COORD_RAW_LOG_PREFIX):].strip()
        fields = {}
        for token in body.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            fields[key] = value

        event = {
            "pid": fields.get("pid", "n/a"),
            "entity": fields.get("entity", "n/a"),
            "identity": fields.get("identity", "n/a"),
            "handler": fields.get("handler", "n/a"),
            "flags": fields.get("flags", "n/a"),
            "raw": fields.get("raw", ""),
            "raw_text": body,
            "ts_utc": _utc_iso8601_now(),
        }
        with self.coord_raw_lock:
            self.coord_raw_diag["stats"]["total"] += 1
            recent = self.coord_raw_diag["recent"]
            recent.append(event)
            if len(recent) > self.trace_history_limit:
                del recent[:-self.trace_history_limit]
        return True

    def _try_capture_rwbase_host_log(self, msg: str):
        if not msg:
            return False
        if self._try_capture_coord_raw_log(msg):
            return True
        return self._try_capture_rwbase_decrypt_log(msg)

    def get_rwbase_decrypt_diag(self):
        with self.decrypt_lock:
            return {
                "enabled": bool(self.decrypt_diag["enabled"]),
                "stats": dict(self.decrypt_diag["stats"]),
                "fail_kinds": dict(self.decrypt_diag["fail_kinds"]),
                "recent": list(self.decrypt_diag["recent"]),
            }

    def get_coord_raw_diag(self):
        with self.coord_raw_lock:
            return {
                "stats": dict(self.coord_raw_diag["stats"]),
                "recent": list(self.coord_raw_diag["recent"]),
            }

    def get_send_thread_history(self, limit: int = 50):
        n = max(1, int(limit))
        with self.trace_lock:
            return [dict(item) for item in self.send_thread_history[-n:]]

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
                        consumed_host_log = self._try_capture_rwbase_host_log(msg)
                        if "ALIVE_ACK" in msg or "DRIVER_ONLINE" in msg:
                            if not self.driver_online:
                                self._emit_console_line("[+] Driver is ONLINE.")
                            self.driver_online = True
                            continue

                        if consumed_region_log or consumed_host_log:
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
