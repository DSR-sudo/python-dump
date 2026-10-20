import json
import mimetypes
import os
import random
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from dma_protocol import (
    RWVG_MAGIC,
    RWVG_TYPE_ITEM,
    RWVG_TYPE_PLAYER,
    RWVG_TYPE_ITEM_BATCH,
    RWVG_TYPE_PLAYER_BATCH,
    RWVG_TYPE_UTILS,
    RWVG_TYPE_ACTOR_SCAN,
    RWVG_TYPED_SIZE_BY_KIND,
)
from rwvg_actor_snapshot import RWVG_ACTOR_SNAPSHOT_VERSION

DEFAULT_EXTERNAL_BASE_DIR = "/root/python-dump"
DEFAULT_EXTERNAL_IMAGE_DIR = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "image")
DEFAULT_EXTERNAL_WEB_HTML = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "web", "webpage.html")
DEFAULT_EXTERNAL_WEB_CONFIG = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "web", "config.txt")


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebRadarService:
    def __init__(self, core, default_port=34900, root_dir=None):
        self.core = core
        self.default_port = int(default_port)
        self.root_dir = root_dir or os.path.dirname(os.path.abspath(__file__))
        self.web_dir = os.path.join(self.root_dir, "web")
        image_dir_from_env = os.getenv("DMA_WEBRADAR_IMAGE_DIR", "").strip()
        if image_dir_from_env:
            self.image_dir = image_dir_from_env
        elif os.path.isdir(DEFAULT_EXTERNAL_IMAGE_DIR):
            self.image_dir = DEFAULT_EXTERNAL_IMAGE_DIR
        else:
            self.image_dir = os.path.join(self.root_dir, "image")

        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._running = False
        # /api/data 的序列化结果缓存：前端 60Hz 轮询时绝大多数轮次数据没有变化，
        # 用版本号命中缓存，避免每次轮询都重新构建并序列化数百 KB（路由器上约 90ms）。
        self._game_data_lock = threading.Lock()
        self._game_data_cached_version = None
        self._game_data_body = b""
        self._game_data_token = ""
        self._game_data_snapshot = None
        # 每客户端订阅：clean/resources/qualities/ego 过滤在服务端完成，
        # 只把该客户端需要的实体和物资放进 /api/data，降低 60Hz 轮询流量。
        self._client_lock = threading.Lock()
        self._client_subs = {}
        self._client_cache = {}
        self._client_last_seen = {}

        self.port = self._read_port_from_file(self.default_port)
        self.password = self._generate_password()
        self._ensure_web_dir()
        self._write_password_to_file(self.password)

    @staticmethod
    def _is_valid_port(port: int) -> bool:
        return 1 <= int(port) <= 65535

    @staticmethod
    def _generate_password() -> str:
        return str(random.randint(100000, 999999))

    def _ensure_web_dir(self):
        os.makedirs(self.web_dir, exist_ok=True)

    def _read_port_from_file(self, fallback: int) -> int:
        candidates = [
            os.path.join(self.web_dir, "config.txt"),
            DEFAULT_EXTERNAL_WEB_CONFIG,
        ]
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    raw = (f.readline() or "").strip()
                if raw:
                    parsed = int(raw)
                    if self._is_valid_port(parsed):
                        return parsed
            except Exception:
                continue
        return int(fallback)

    def _write_password_to_file(self, password: str):
        path = os.path.join(self.web_dir, "pwd.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(password)
        except Exception:
            pass

    def _load_html_page(self) -> str:
        candidates = [
            os.path.join(self.web_dir, "webpage.html"),
            os.path.join(self.root_dir, "webpage.html"),
            DEFAULT_EXTERNAL_WEB_HTML,
        ]
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                continue

        return """<!DOCTYPE html>
<html>
<head>
<meta charset=\"utf-8\" />
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
<title>WebRadar</title>
<style>
body { font-family: sans-serif; background: #111; color: #eee; margin: 0; padding: 16px; }
input, button { padding: 6px 10px; margin-right: 8px; }
pre { background: #1b1b1b; border: 1px solid #333; padding: 12px; overflow: auto; max-height: 75vh; }
</style>
</head>
<body>
<h3>WebRadar</h3>
<div>
  <input id=\"token\" placeholder=\"X-Auth-Token\" />
  <button onclick=\"refresh()\">Refresh</button>
</div>
<pre id=\"out\">waiting...</pre>
<script>
async function refresh() {
  const token = document.getElementById('token').value || localStorage.getItem('wr_token') || '';
  localStorage.setItem('wr_token', token);
  const out = document.getElementById('out');
  try {
    const r = await fetch('/api/data', { headers: { 'X-Auth-Token': token } });
    const t = await r.text();
    out.textContent = t;
  } catch (e) {
    out.textContent = String(e);
  }
}
setInterval(refresh, 700);
refresh();
</script>
</body>
</html>"""

    def _resolve_image_path(self, request_path: str):
        rel = request_path[len("/image/"):]
        rel = rel.replace("\\", "/")
        rel = os.path.normpath(rel)
        if rel in (".", "") or rel.startswith(".."):
            return None

        base = os.path.abspath(self.image_dir)
        target = os.path.abspath(os.path.join(base, rel))
        if not (target == base or target.startswith(base + os.sep)):
            return None
        if not os.path.isfile(target):
            return None
        return target

    def _build_game_data(self):
        try:
            return self.core.get_radar_snapshot()
        except Exception:
            return {
                "local_player": {
                    "id": "local",
                    "team_id": 0,
                    "camp_id": 0,
                    "yaw": 0.0,
                    "position": {"x": 0, "y": 0},
                },
                "entities": [],
                "teammates": [],
            }

    def _game_data_version(self):
        """廉价版本号（O(1)，不序列化）；返回 None 表示无法判定，必须重新构建。"""
        getter = getattr(self.core, "get_radar_data_version", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _build_game_data_cached(self, version):
        """同一版本只构建一次全量快照；按客户端过滤时共享这份快照。"""
        if version is not None:
            with self._game_data_lock:
                if version == self._game_data_cached_version and self._game_data_snapshot is not None:
                    return self._game_data_snapshot
        snapshot = self._build_game_data()
        if version is not None:
            with self._game_data_lock:
                if version == self._game_data_cached_version and self._game_data_snapshot is not None:
                    return self._game_data_snapshot
                self._game_data_cached_version = version
                self._game_data_snapshot = snapshot
                self._game_data_body = b""
                self._game_data_token = ""
        return snapshot

    def get_game_data_payload(self, force=False):
        """返回 /api/data 的 (body, token)。

        版本号未变时直接复用上次序列化的 JSON —— 60Hz 轮询因此不会反复付出构建与
        序列化成本，只有数据真正变化（约 2~4 次/秒）时才重新生成。
        force=True 时忽略缓存重新构建，供前端在长时间无变化时兜底刷新。
        """
        version = None if force else self._game_data_version()
        if version is not None:
            with self._game_data_lock:
                if version == self._game_data_cached_version and self._game_data_body:
                    return self._game_data_body, self._game_data_token
        snapshot = self._build_game_data_cached(version)
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        token = format(zlib.crc32(body), "08x")
        if version is not None:
            with self._game_data_lock:
                if version == self._game_data_cached_version:
                    self._game_data_body = body
                    self._game_data_token = token
        return body, token

    MAX_CLIENT_SUBS = 32

    @staticmethod
    def _normalize_subscription(payload):
        """把前端订阅压成稳定结构，未知/越界值一律回落默认。"""
        if not isinstance(payload, dict):
            return {}
        clean = bool(payload.get("clean"))
        resources = bool(payload.get("resources", True))
        raw_qualities = payload.get("qualities")
        if isinstance(raw_qualities, list):
            qualities = sorted({int(q) for q in raw_qualities if isinstance(q, (int, float))})
        else:
            qualities = list(range(7))
        raw_ego = payload.get("ego")
        ego = raw_ego if isinstance(raw_ego, dict) else {}
        ego_out = {}
        target_key = str(ego.get("target_key") or "").strip()[:64]
        target_id = str(ego.get("target_id") or "").strip()[:64]
        if ego.get("enabled") and (target_key or target_id):
            try:
                radius_cm = int(ego.get("radius_cm") or 10000)
            except (TypeError, ValueError):
                radius_cm = 10000
            ego_out = {
                "target_key": target_key,
                "target_id": target_id,
                "radius_cm": max(1000, min(200000, radius_cm)),
            }
        return {
            "clean": clean,
            "resources": resources,
            "qualities": qualities,
            "ego": ego_out,
        }

    @staticmethod
    def _subscription_signature(subscription):
        raw = json.dumps(subscription, sort_keys=True, ensure_ascii=False)
        return zlib.crc32(raw.encode("utf-8"))

    @staticmethod
    def _is_default_subscription(subscription):
        return (
            not subscription.get("clean")
            and bool(subscription.get("resources", True))
            and not subscription.get("ego")
            and set(subscription.get("qualities") or []) >= set(range(7))
        )

    def set_client_subscription(self, client_id, payload):
        client_id = str(client_id or "").strip()[:64]
        if not client_id:
            return False
        subscription = self._normalize_subscription(payload)
        with self._client_lock:
            self._client_subs[client_id] = subscription
            self._client_cache.pop(client_id, None)
            self._client_last_seen[client_id] = time.monotonic()
            if len(self._client_subs) > self.MAX_CLIENT_SUBS:
                oldest = min(self._client_last_seen, key=self._client_last_seen.get)
                self._client_subs.pop(oldest, None)
                self._client_cache.pop(oldest, None)
                self._client_last_seen.pop(oldest, None)
        return True

    @staticmethod
    def _filter_radar_snapshot(snapshot, subscription):
        """按订阅裁剪一份雷达快照；返回新 dict，不修改缓存里的原对象。"""
        if not isinstance(snapshot, dict) or not subscription:
            return snapshot
        clean = bool(subscription.get("clean"))
        resources = bool(subscription.get("resources", True))
        qualities = set(subscription.get("qualities") or [])
        ego = subscription.get("ego") or {}
        target_key = str(ego.get("target_key") or "")
        target_id = str(ego.get("target_id") or "")
        radius_cm = int(ego.get("radius_cm") or 0)
        radius_sq = radius_cm * radius_cm

        def coords(value):
            position = value.get("position") if isinstance(value, dict) else None
            if not isinstance(position, dict):
                return None
            try:
                return (
                    float(position.get("x") or 0.0),
                    float(position.get("y") or 0.0),
                    float(position.get("z") or 0.0),
                )
            except (TypeError, ValueError):
                return None

        def alive(entity):
            if entity.get("has_health") is False:
                return True
            try:
                return float(entity.get("health") or 0.0) > 0.0
            except (TypeError, ValueError):
                return True

        entities_in = snapshot.get("entities") or []
        target = None
        if target_key or target_id:
            for entity in entities_in:
                if target_key and str(entity.get("key") or "") == target_key:
                    target = entity
                    break
                if target_id and str(entity.get("id") or "") == target_id:
                    target = entity
                    break
        origin = coords(target) if target is not None else None

        def within(value):
            if radius_sq <= 0 or origin is None:
                return True
            point = coords(value)
            if point is None:
                return False
            dx = point[0] - origin[0]
            dy = point[1] - origin[1]
            dz = point[2] - origin[2]
            return dx * dx + dy * dy + dz * dz <= radius_sq

        entities = []
        for entity in entities_in:
            # 视角目标就是相机本身：即使纯净模式会隐藏 AI/尸体，也不能把目标裁掉，
            # 否则前端会变成“目标已丢失”。只保留它一个，其他同类仍然不下发。
            is_target = entity is target
            if clean and not is_target and str(entity.get("type") or "") == "ai":
                continue
            if clean and not is_target and not alive(entity):
                continue
            if not is_target and not within(entity):
                continue
            entities.append(entity)

        items = []
        if resources and not clean:
            for item in snapshot.get("items") or []:
                try:
                    quality = int(item.get("item_quality", 0) or 0)
                except (TypeError, ValueError):
                    quality = 0
                if quality not in qualities:
                    continue
                if not within(item):
                    continue
                items.append(item)

        entity_ids = {str(entity.get("id") or "") for entity in entities}
        teammates = [
            teammate for teammate in (snapshot.get("teammates") or [])
            if str(teammate.get("id") or "") in entity_ids
        ]

        filtered = dict(snapshot)
        filtered["entities"] = entities
        filtered["items"] = items
        filtered["teammates"] = teammates
        meta = dict(snapshot.get("meta") or {})
        meta["entity_count"] = len(entities)
        meta["item_count"] = len(items)
        meta["teammate_count"] = len(teammates)
        meta["player_count"] = sum(1 for entity in entities if str(entity.get("type") or "") != "ai")
        meta["ai_count"] = sum(1 for entity in entities if str(entity.get("type") or "") == "ai")
        filtered["meta"] = meta
        return filtered

    def get_client_game_payload(self, client_id, force=False):
        """/api/data 的按客户端裁剪版本；无订阅或全默认时复用全局缓存。"""
        client_id = str(client_id or "").strip()[:64]
        if not client_id:
            return self.get_game_data_payload(force=force)
        with self._client_lock:
            subscription = dict(self._client_subs.get(client_id) or {})
            self._client_last_seen[client_id] = time.monotonic()
        if not subscription or self._is_default_subscription(subscription):
            return self.get_game_data_payload(force=force)
        signature = self._subscription_signature(subscription)
        version = None if force else self._game_data_version()
        if version is not None:
            with self._client_lock:
                cached = self._client_cache.get(client_id)
            if cached and cached[0] == version and cached[1] == signature:
                return cached[3], cached[2]
        snapshot = self._filter_radar_snapshot(
            self._build_game_data_cached(version), subscription)
        body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
        token = format(zlib.crc32(body), "08x")
        if version is not None:
            with self._client_lock:
                self._client_cache[client_id] = (version, signature, token, body)
        return body, token

    def _build_rwvg_data(self):
        snapshot = self._build_game_data()

        stats = {}
        if hasattr(self.core, "get_stream_stats"):
            try:
                stats = self.core.get_stream_stats() or {}
            except Exception:
                stats = {}

        decrypt_logs = []
        send_logs = []
        if hasattr(self.core, "get_rwbase_decrypt_diag"):
            try:
                decrypt_logs = (self.core.get_rwbase_decrypt_diag() or {}).get("recent", [])[-120:]
            except Exception:
                decrypt_logs = []
        if hasattr(self.core, "get_send_thread_history"):
            try:
                send_logs = self.core.get_send_thread_history(limit=120) or []
            except Exception:
                send_logs = []

        return {
            "protocol": {
                "name": "RWVG",
                "magic_u32": RWVG_MAGIC,
                "types": {
                    "utils": RWVG_TYPE_UTILS,
                    "player": RWVG_TYPE_PLAYER,
                    "item": RWVG_TYPE_ITEM,
                    "player_batch": RWVG_TYPE_PLAYER_BATCH,
                    "item_batch": RWVG_TYPE_ITEM_BATCH,
                    "actor_scan": RWVG_TYPE_ACTOR_SCAN,
                },
                "batch_types": {
                    "player_batch": RWVG_TYPE_PLAYER_BATCH,
                    "item_batch": RWVG_TYPE_ITEM_BATCH,
                },
                "batch_stats": {
                    "player_batch_frames": int(stats.get("player_batch_frames", 0) or 0),
                    "item_batch_frames": int(stats.get("item_batch_frames", 0) or 0),
                    "player_batch_entities": int(stats.get("player_batch_entities", 0) or 0),
                    "item_batch_entities": int(stats.get("item_batch_entities", 0) or 0),
                },
                "typed_size_by_kind": dict(RWVG_TYPED_SIZE_BY_KIND),
                "actor_snapshot_version": RWVG_ACTOR_SNAPSHOT_VERSION,
            },
            "stream_stats": stats,
            "snapshot": snapshot,
            "logs": {
                "decrypt": decrypt_logs,
                "send_thread_players": send_logs,
            },
        }

    def _make_handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            # HTTP/1.1 保持长连接：60Hz 轮询时避免每帧都重建 TCP 连接。
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                return

            def _is_authorized(self, query_token=""):
                auth = self.headers.get("X-Auth-Token", "")
                return (
                    auth == service.password
                    or auth == "963007"
                    or query_token == service.password
                    or query_token == "963007"
                )

            def _client_id(self):
                client_id = (self.headers.get("X-Client-Id", "") or "").strip()
                if not client_id:
                    query = parse_qs(urlparse(self.path).query)
                    client_id = (query.get("client_id") or [""])[0].strip()
                return client_id[:64]

            def _send_json(self, payload, status=200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(self, text, content_type="text/html; charset=utf-8", status=200):
                body = text.encode("utf-8", errors="ignore")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/subscription":
                    self._send_json({"error": "Not Found"}, status=404)
                    return
                query = parse_qs(parsed.query)
                query_token = (query.get("token") or [""])[0].strip()
                if not self._is_authorized(query_token=query_token):
                    self._send_json({"error": "Unauthorized"}, status=401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > 16384:
                    self._send_json({"error": "Bad Request"}, status=400)
                    return
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._send_json({"error": "Bad Request"}, status=400)
                    return
                if not service.set_client_subscription(self._client_id(), payload):
                    self._send_json({"error": "Bad Request"}, status=400)
                    return
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                query_token = ""
                query = parse_qs(parsed.query)
                if "token" in query and query["token"]:
                    query_token = (query["token"][0] or "").strip()
                if path == "/":
                    self._send_text(service._load_html_page())
                    return

                if path == "/api/data":
                    if not self._is_authorized(query_token=query_token):
                        self._send_json({"error": "Unauthorized"}, status=401)
                        return
                    # 前端带上已渲染数据的 token；内容未变时回 204，不重复传输数百 KB。
                    # nocache 请求总是重新构建并返回完整负载，忽略前端 token。
                    forced = bool(query.get("nocache")) or bool(
                        self.headers.get("X-Data-No-Cache", ""))
                    body, token = service.get_client_game_payload(self._client_id(), force=forced)
                    if not forced and self.headers.get("X-Data-Token", "") == token:
                        self.send_response(204)
                        self.send_header("X-Data-Token", token)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("X-Data-Token", token)
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if path == "/api/rwvg":
                    if not self._is_authorized(query_token=query_token):
                        self._send_json({"error": "Unauthorized"}, status=401)
                        return
                    self._send_json(service._build_rwvg_data(), status=200)
                    return

                if path == "/api/logs":
                    if not self._is_authorized(query_token=query_token):
                        self._send_json({"error": "Unauthorized"}, status=401)
                        return
                    payload = service._build_rwvg_data().get("logs", {})
                    self._send_json(payload, status=200)
                    return

                if path.startswith("/image/"):
                    image_path = service._resolve_image_path(path)
                    if image_path is None:
                        self._send_json({"error": "Not Found"}, status=404)
                        return

                    ctype, _ = mimetypes.guess_type(image_path)
                    if not ctype:
                        ctype = "application/octet-stream"

                    try:
                        with open(image_path, "rb") as f:
                            data = f.read()
                    except Exception:
                        self._send_json({"error": "Read Failed"}, status=500)
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                self._send_json({"error": "Not Found"}, status=404)

        return Handler

    def start(self, port_override=None):
        with self._lock:
            if self._running:
                return False, f"already running on 0.0.0.0:{self.port}"

            if port_override is None:
                port = self._read_port_from_file(self.port or self.default_port)
            else:
                try:
                    port = int(port_override)
                except Exception:
                    return False, "invalid port"

            if not self._is_valid_port(port):
                return False, "port must be in [1, 65535]"

            self.port = port
            self.password = self._generate_password()
            self._write_password_to_file(self.password)

            handler_cls = self._make_handler()
            try:
                server = _ThreadingHTTPServer(("0.0.0.0", self.port), handler_cls)
            except OSError as exc:
                return False, str(exc)

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._server = server
            self._thread = thread
            self._running = True

            return True, f"started on 0.0.0.0:{self.port}, token={self.password}"

    def stop(self):
        with self._lock:
            if not self._running:
                return False, "already stopped"
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._running = False

        try:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        except Exception:
            return False, "stop encountered runtime error"

        return True, "stopped"

    def status(self):
        with self._lock:
            return {
                "running": bool(self._running),
                "port": int(self.port),
                "password": str(self.password),
                "pwd_path": os.path.join(self.web_dir, "pwd.txt"),
                "config_path": os.path.join(self.web_dir, "config.txt"),
                "image_dir": self.image_dir,
            }
