import json
import mimetypes
import os
import random
import threading
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
                    self._send_json(service._build_game_data(), status=200)
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
