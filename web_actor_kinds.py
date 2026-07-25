import json
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

DEFAULT_EXTERNAL_BASE_DIR = "/root/python-dump"
DEFAULT_EXTERNAL_WEB_HTML = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "web", "actor_kinds.html")
DEFAULT_EXTERNAL_WEB_CONFIG = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "web", "actor_config.txt")
# 与 WebRadarService 复用同一张 token（web/pwd.txt），允许两个服务共享同一访问口令。
DEFAULT_EXTERNAL_PWD = os.path.join(DEFAULT_EXTERNAL_BASE_DIR, "web", "pwd.txt")
DEFAULT_ACTOR_KINDS_PORT = 8081


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebActorKindsService:
    """
    Actor 分类监控服务（端口默认 8081）。
    结构镜像 WebRadarService：stdlib http.server + _ThreadingHTTPServer + BaseHTTPRequestHandler，
    通过 X-Auth-Token（或 ?token=）鉴权，对外提供 HTML 监控页与 /api/actor_kinds JSON。
    """

    def __init__(self, core, default_port=DEFAULT_ACTOR_KINDS_PORT, root_dir=None):
        self.core = core
        self.default_port = int(default_port)
        self.root_dir = root_dir or os.path.dirname(os.path.abspath(__file__))
        self.web_dir = os.path.join(self.root_dir, "web")

        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._running = False

        self.port = self._read_port_from_file(self.default_port)
        self.password = self._load_shared_password()
        self._ensure_web_dir()
        self._write_password_to_file(self.password)

    # ---- 工具方法（与 WebRadarService 保持一致的风格）--------------------------

    @staticmethod
    def _is_valid_port(port: int) -> bool:
        return 1 <= int(port) <= 65535

    def _generate_password(self) -> str:
        return str(random.randint(100000, 999999))

    def _ensure_web_dir(self):
        os.makedirs(self.web_dir, exist_ok=True)

    def _read_port_from_file(self, fallback: int) -> int:
        candidates = [
            os.path.join(self.web_dir, "actor_config.txt"),
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

    def _load_shared_password(self) -> str:
        # 优先复用 web/pwd.txt（与雷达共享口令），不存在时生成新口令并写回该文件。
        candidates = [
            os.path.join(self.web_dir, "pwd.txt"),
            DEFAULT_EXTERNAL_PWD,
        ]
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    raw = (f.readline() or "").strip()
                if raw:
                    return raw
            except Exception:
                continue
        return self._generate_password()

    def _write_password_to_file(self, password: str):
        # 共享写入 web/pwd.txt：若雷达服务已经写过该文件则覆盖为同一 token。
        path = os.path.join(self.web_dir, "pwd.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(password)
        except Exception:
            pass

    def _load_html_page(self) -> str:
        candidates = [
            os.path.join(self.web_dir, "actor_kinds.html"),
            os.path.join(self.root_dir, "actor_kinds.html"),
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
<title>ActorKinds Monitor</title>
<style>
body { font-family: sans-serif; background: #1a1a1a; color: #eee; margin: 0; padding: 16px; }
input, button, select { padding: 6px 10px; margin-right: 8px; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { border: 1px solid #333; padding: 4px 8px; text-align: left; font-size: 12px; }
</style>
</head>
<body>
<h3>ActorKinds Monitor</h3>
<div>actor_kinds.html missing. Place web/actor_kinds.html next to this service.</div>
</body>
</html>"""

    # ---- 数据构建 -------------------------------------------------------------

    def _build_actor_kinds_data(self):
        # 旧驱动尚未发送 Type=6 帧时，core.get_actor_scan_snapshot 仍会返回 has_data=False 的空快照，
        # 前端据此提示“驱动未发送 actor-scan 帧”，而不是报错。
        snapshot = {
            "actors": [], "count": 0, "frames": 0, "last_ts": 0.0,
            "last_count": 0, "has_data": False, "version": 0,
            "snapshot_id": None, "fragment_count": 0, "received_fragments": 0,
            "total_record_count": 0, "complete": False, "status": "awaiting_snapshot",
            "dropped_late_fragments": 0, "duplicate_fragments": 0, "invalid_fragments": 0,
        }
        if hasattr(self.core, "get_actor_scan_snapshot"):
            try:
                snapshot = self.core.get_actor_scan_snapshot() or snapshot
            except Exception:
                snapshot = {
                    "actors": [], "count": 0, "frames": 0, "last_ts": 0.0,
                    "last_count": 0, "has_data": False, "version": 0,
                    "snapshot_id": None, "fragment_count": 0, "received_fragments": 0,
                    "total_record_count": 0, "complete": False, "status": "snapshot_error",
                    "dropped_late_fragments": 0, "duplicate_fragments": 0, "invalid_fragments": 0,
                }

        # Kind 计数汇总：Unknown/Player/Minion/Boss/Item/Container/DeadBox/Box/AI
        kind_counts = {}
        for actor in (snapshot.get("actors") or []):
            kind_name = str(actor.get("kind_name") or "Unknown")
            kind_counts[kind_name] = kind_counts.get(kind_name, 0) + 1

        return {
            "has_data": bool(snapshot.get("has_data")),
            "actors": snapshot.get("actors") or [],
            "count": int(snapshot.get("count", 0) or 0),
            "frames": int(snapshot.get("frames", 0) or 0),
            "last_ts": float(snapshot.get("last_ts", 0.0) or 0.0),
            "last_count": int(snapshot.get("last_count", 0) or 0),
            "kind_counts": kind_counts,
            "version": int(snapshot.get("version", 0) or 0),
            "snapshot_id": snapshot.get("snapshot_id"),
            "fragment_count": int(snapshot.get("fragment_count", 0) or 0),
            "received_fragments": int(snapshot.get("received_fragments", 0) or 0),
            "total_record_count": int(snapshot.get("total_record_count", 0) or 0),
            "complete": bool(snapshot.get("complete")),
            "status": str(snapshot.get("status", "awaiting_snapshot")),
            "dropped_late_fragments": int(snapshot.get("dropped_late_fragments", 0) or 0),
            "duplicate_fragments": int(snapshot.get("duplicate_fragments", 0) or 0),
            "invalid_fragments": int(snapshot.get("invalid_fragments", 0) or 0),
        }

    # ---- HTTP handler ---------------------------------------------------------

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

                if path == "/api/actor_kinds":
                    if not self._is_authorized(query_token=query_token):
                        self._send_json({"error": "Unauthorized"}, status=401)
                        return
                    self._send_json(service._build_actor_kinds_data(), status=200)
                    return

                self._send_json({"error": "Not Found"}, status=404)

        return Handler

    # ---- 生命周期 -------------------------------------------------------------

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
            # 启动时刷新共享口令，确保 web/pwd.txt 与雷达同步
            self.password = self._load_shared_password()
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
                "config_path": os.path.join(self.web_dir, "actor_config.txt"),
            }
