#!/usr/bin/env python3
"""Development-time parity harness: Python dma_core/web_radar vs the C++ port.

Both servers are fed identical synthetic UDP frames; HTTP payloads must match
after normalising timestamps and monotonic-time item keys.

Usage: python3 cpp/tests/parity_test.py <python-dump-root> <dma_webradar-binary>
"""
import http.client
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PY_UDP_PORT = 53790
CXX_UDP_PORT = 53791
PY_WEB_PORT = 34910
CXX_WEB_PORT = 34911


# ---------------------------------------------------------------------------
# Frame builders (authoritative Python struct layouts)
# ---------------------------------------------------------------------------
def f32(value):
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


def s32(value):
    return struct.unpack("<i", struct.pack("<f", float(value)))[0]


def actor_record(kind, mesh=0, pos=(0.0, 0.0, 0.0), team=0, health=0.0,
                 max_health=0.0, weapon=0, hero=0, item_id=0, quality=0,
                 valid=0, direction=0.0, pitch=0.0, helmet=0,
                 helmet_dur=0.0, helmet_max=0.0, armor=0, armor_dur=0.0,
                 armor_max=0.0):
    return struct.pack(
        "<BQQQIIIBQiIIHQQIIIIiIi" "iIIiII",
        kind, mesh, 0x1000, 0x2000,
        f32(pos[0]), f32(pos[1]), f32(pos[2]), 1, 0, team,
        f32(health), f32(max_health), weapon, hero, item_id, quality,
        valid, 3, 1, -1, f32(direction), s32(pitch),
        helmet, f32(helmet_dur), f32(helmet_max),
        armor, f32(armor_dur), f32(armor_max))


def actor_fragment(snapshot_id, fragment_index, fragment_count,
                   total_record_count, flags, yaw, records,
                   local_pawn=0xAAAA, view_fields=0x3):
    header = struct.pack(
        "<HHIIIIQIIIIiI",
        len(records), 10, snapshot_id, fragment_index, fragment_count,
        total_record_count, local_pawn, f32(yaw), view_fields, 5, 1, -2, flags)
    return header + b"".join(records)


def typed_frame(kind, payload, magic=0x47564352):
    return struct.pack("<III", magic, kind, len(payload)) + payload


def utils_payload(team=7, pos=(100.0, 200.0, 30.0), neck=(101.0, 201.0, 31.0),
                  matrix=None, weapon=51841):
    matrix = matrix or [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                        0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return struct.pack(
        "<16f3Bi3f3fiQfB4f2i", *matrix, 1, 0, 0, team,
        *neck, *pos, weapon, 0x12345678, 0.5, 0, 1.0, 2.0, 3.0, 4.0, 5, 6)


def player_payload(name=b"Alice", bot=b"", team=7, entity_ptr=0x1234,
                   health=88.0, class_name=b""):
    return struct.pack(
        "<2f2i18s18s18s32s32sQ3f3ffiiB",
        health, 100.0, 2, 3,
        class_name.ljust(18, b"\0"), b"detective".ljust(18, b"\0"),
        b"M4A1".ljust(18, b"\0"), name.ljust(32, b"\0"), bot.ljust(32, b"\0"),
        entity_ptr, 10.0, 20.0, 30.0, 11.0, 21.0, 31.0, 90.0, 1500, team, 1)


def item_payload(item_type=3, dead_box_type=2, pos=(400.0, 500.0, 6.0),
                 quality=0, money=1234, name=b""):
    return struct.pack(
        "<Q3fii32s18siii", dead_box_type, *pos, item_type, 1200,
        name.ljust(32, b"\0"), b"proj".ljust(18, b"\0"), money, quality, 0)


def log_packet(text):
    return b"\x01" + text.encode("utf-8")


def data_packet(payload):
    return b"\x02" + payload


def snapshot_packet(payload):
    return b"\x06" + payload


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_request(port, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    headers_out = {}
    for key, value in response.getheaders():
        headers_out.setdefault(key.lower(), value)
    status = response.status
    connection.close()
    return status, headers_out, data


def get_json(port, path, token, extra_headers=None):
    headers = {"X-Auth-Token": token}
    headers.update(extra_headers or {})
    status, _, body = http_request(port, "GET", path, headers)
    if status == 204:
        return status, None
    return status, json.loads(body.decode("utf-8"))


def post_json(port, path, token, payload, extra_headers=None):
    headers = {"X-Auth-Token": token, "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    status, response_headers, body = http_request(
        port, "POST", path, headers, json.dumps(payload))
    parsed = json.loads(body.decode("utf-8")) if body else None
    return status, parsed, response_headers


# ---------------------------------------------------------------------------
# Normalisation / diff
# ---------------------------------------------------------------------------
def normalise(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("ts", "_ts", "last_ts"):
                out[key] = 0.0
            elif key in ("utils_age_ms", "world_items_age_ms"):
                out[key] = -2
            elif key in ("id", "entity_id") and isinstance(item, str) and item.startswith("item:"):
                out[key] = "item:*"
            else:
                out[key] = normalise(item)
        return out
    if isinstance(value, list):
        return [normalise(item) for item in value]
    return value


def diff(a, b, path="$", problems=None, limit=12):
    problems = problems if problems is not None else []
    if len(problems) >= limit:
        return problems
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        problems.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
        return problems
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                problems.append(f"{path}.{key}: missing on python side")
            elif key not in b:
                problems.append(f"{path}.{key}: missing on c++ side")
            else:
                diff(a[key], b[key], f"{path}.{key}", problems, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            problems.append(f"{path}: length {len(a)} != {len(b)}")
        for index in range(min(len(a), len(b))):
            diff(a[index], b[index], f"{path}[{index}]", problems, limit)
    elif isinstance(a, float) or isinstance(b, float):
        if abs(float(a) - float(b)) > 1e-6:
            problems.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        problems.append(f"{path}: {a!r} != {b!r}")
    return problems


class Checker:
    def __init__(self):
        self.failures = 0
        self.checks = 0

    def same(self, label, py_value, cxx_value):
        self.checks += 1
        problems = diff(normalise(py_value), normalise(cxx_value))
        if problems:
            self.failures += 1
            print(f"FAIL {label}")
            for problem in problems:
                print("     " + problem)
        else:
            print(f"ok   {label}")

    def equal(self, label, py_value, cxx_value):
        self.checks += 1
        if py_value != cxx_value:
            self.failures += 1
            print(f"FAIL {label}: {py_value!r} != {cxx_value!r}")
        else:
            print(f"ok   {label}")


# ---------------------------------------------------------------------------
# Test runtime
# ---------------------------------------------------------------------------
class AimResponder(threading.Thread):
    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock = sock
        self.stop_event = threading.Event()
        self.replies = []

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stop_event.is_set():
            try:
                data, peer = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.startswith(b"aim "):
                self.replies.append(data.decode("utf-8", "replace"))
                self.sock.sendto(b"\x01[PMU][AIMCTL] ok", peer)
            elif len(data) == 1049:
                magic, command = struct.unpack_from("<IB", data, 0)
                if magic == 0xDEADBEEF and command == 3:  # GET_CR3
                    reply = struct.pack("<QQQ", 0x1111222233334444,
                                        0x5555666677778888, 0x9999AAAA)
                    self.sock.sendto(b"\x02" + reply, peer)
                elif magic == 0xDEADBEEF and command == 15:  # PINGPONG
                    self.sock.sendto(b"\x02" + struct.pack("<Q", 1), peer)

    def stop(self):
        self.stop_event.set()


def wait_for_port(port, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def wait_for_http(port, token, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, _ = http_request(port, "GET", "/api/aim-state",
                                        {"X-Auth-Token": token})
            if status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def main():
    root = Path(sys.argv[1]).resolve()
    binary = Path(sys.argv[2]).resolve()
    work = Path(tempfile.mkdtemp(prefix="dma_parity_"))
    cpp_root = work / "cpp_root"
    (cpp_root / "web").mkdir(parents=True)
    shutil.copy2(root / "web" / "config.txt", cpp_root / "web" / "config.txt")
    shutil.copy2(root / "web" / "webpage.html", cpp_root / "web" / "webpage.html")
    os.symlink(root / "image", cpp_root / "image")
    cpp_pwd = cpp_root / "web" / "pwd.txt"

    os.environ["DMA_BIND_PORT"] = str(PY_UDP_PORT)
    sys.path.insert(0, str(root))
    from dma_core import DMACore
    from web_radar import WebRadarService

    py_core = DMACore(session_log=None)
    py_service = WebRadarService(py_core, default_port=PY_WEB_PORT)
    ok, message = py_service.start(port_override=PY_WEB_PORT)
    if not ok:
        raise SystemExit("python webradar failed: " + message)
    py_token = (root / "web" / "pwd.txt").read_text()

    cpp_log = open(work / "cpp.log", "wb")
    cpp_env = dict(os.environ)
    cpp_env["DMA_BIND_PORT"] = str(CXX_UDP_PORT)
    cpp_proc = subprocess.Popen(
        [str(binary), f"--webradar={CXX_WEB_PORT}", f"--root={cpp_root}"],
        env=cpp_env, stdin=subprocess.PIPE, stdout=cpp_log, stderr=subprocess.STDOUT)
    cpp_stdin = cpp_proc.stdin  # keep the console loop alive

    try:
        if not wait_for_port(CXX_WEB_PORT):
            raise SystemExit("c++ webradar did not become ready")
        cpp_token = ""
        for _ in range(50):
            if cpp_pwd.exists():
                cpp_token = cpp_pwd.read_text()
                if cpp_token:
                    status, _, _ = http_request(
                        CXX_WEB_PORT, "GET", "/api/aim-state",
                        {"X-Auth-Token": cpp_token})
                    if status == 200:
                        break
            time.sleep(0.1)
        else:
            raise SystemExit("c++ webradar token not usable")
        if not wait_for_http(PY_WEB_PORT, py_token):
            raise SystemExit("python webradar did not become ready")

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))
        responder = AimResponder(sender)
        responder.start()

        def send(frame):
            for port in (PY_UDP_PORT, CXX_UDP_PORT):
                sender.sendto(frame, ("127.0.0.1", port))

        checker = Checker()

        # 1. Initial empty payload.
        status_py, data_py = get_json(PY_WEB_PORT, "/api/data", py_token)
        status_cxx, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token)
        checker.equal("initial /api/data status", status_py, status_cxx)
        checker.same("initial /api/data", data_py, data_cxx)

        # 2. LOG packets (status / module / region).
        for text in (
            "[PMU][STATUS] scan started",
            "[User] Base: 0x7FF612340000 | Size: 0x1000 | Name: gamecore.dll",
            "[UserRegion] Base: 0x10000 | Size: 0x2000 | State: 0x1000 | Protect: 0x4 | Type: 0x20000",
            "[UserRegion] Done PID=0x1234 Count=2",
        ):
            send(log_packet(text))
        time.sleep(0.4)

        # 3. Legacy utils + player + item + batches -> fallback radar path.
        send(data_packet(typed_frame(1, utils_payload())))
        send(data_packet(typed_frame(2, player_payload())))
        send(data_packet(typed_frame(2, player_payload(name=b"", bot=b"BotA",
                                                       team=0, entity_ptr=0,
                                                       class_name=b"AI"))))
        send(data_packet(typed_frame(3, item_payload())))
        send(data_packet(typed_frame(4, struct.pack("<I", 1) + player_payload(name=b"Batch"))))
        send(data_packet(typed_frame(5, struct.pack("<I", 1) + item_payload(item_type=1, quality=4))))
        time.sleep(0.5)
        status_py, data_py = get_json(PY_WEB_PORT, "/api/data", py_token)
        status_cxx, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token)
        checker.equal("fallback /api/data status", status_py, status_cxx)
        checker.same("fallback /api/data", data_py, data_cxx)

        # 4. Type=6 actor snapshot, two fragments, FULL world.
        V_POS = 1 << 3
        V_TEAM = 1 << 4
        V_HEALTH = 1 << 5
        V_MAX_HEALTH = 1 << 6
        V_WEAPON = 1 << 7
        V_HERO = 1 << 8
        V_ITEM_ID = 1 << 9
        V_ITEM_QUALITY = 1 << 10
        V_DIRECTION = 1 << 11
        V_VIEW_PITCH = 1 << 12
        V_HELMET = 1 << 13
        V_ARMOR = 1 << 14
        V_LOCAL = 1 << 15
        player_valid = (V_POS | V_TEAM | V_HEALTH | V_MAX_HEALTH | V_WEAPON |
                        V_HERO | V_DIRECTION | V_VIEW_PITCH | V_LOCAL |
                        V_HELMET | V_ARMOR)
        ai_valid = V_POS | V_TEAM | V_HEALTH | V_MAX_HEALTH
        item_valid = V_POS | V_ITEM_ID | V_ITEM_QUALITY
        fragment0 = [
            actor_record(1, mesh=0x1234, pos=(1.5, 2.5, 3.5), team=7,
                         health=100.0, max_health=100.0, weapon=51841,
                         hero=88000000030, valid=player_valid,
                         direction=90.0, pitch=-12.5, helmet=3,
                         helmet_dur=40.0, helmet_max=50.0, armor=4,
                         armor_dur=30.0, armor_max=60.0),
            actor_record(8, mesh=0x8888, pos=(10.0, 20.0, 30.0), team=0,
                         health=50.0, max_health=100.0, valid=ai_valid,
                         direction=180.0),
        ]
        fragment1 = [
            actor_record(4, mesh=0x4444, pos=(100.0, 200.0, 300.0),
                         item_id=11010001001, quality=0, valid=item_valid),
            actor_record(6, mesh=0x6666, pos=(5.0, 6.0, 7.0),
                         item_id=0, valid=V_POS),
            actor_record(5, mesh=0x5555, pos=(-5.0, -6.0, -7.0),
                         item_id=18010000016, quality=0,
                         valid=item_valid),
        ]
        total = len(fragment0) + len(fragment1)
        send(snapshot_packet(typed_frame(
            6, actor_fragment(9001, 0, 2, total, 1, 45.0, fragment0))))
        time.sleep(0.15)
        send(snapshot_packet(typed_frame(
            6, actor_fragment(9001, 1, 2, total, 1, 45.0, fragment1))))
        time.sleep(0.6)
        status_py, data_py = get_json(PY_WEB_PORT, "/api/data", py_token)
        status_cxx, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token)
        checker.equal("actor /api/data status", status_py, status_cxx)
        checker.same("actor /api/data", data_py, data_cxx)

        # 5. /api/rwvg protocol + stats + snapshot.
        _, rwvg_py = get_json(PY_WEB_PORT, "/api/rwvg", py_token)
        _, rwvg_cxx = get_json(CXX_WEB_PORT, "/api/rwvg", cpp_token)
        checker.same("/api/rwvg", rwvg_py, rwvg_cxx)

        # 6. players-only snapshot reuses cached world items.
        only_players = [
            actor_record(1, mesh=0x9999, pos=(7.0, 8.0, 9.0), team=2,
                         health=70.0, max_health=100.0, valid=ai_valid,
                         direction=33.0),
        ]
        send(snapshot_packet(typed_frame(
            6, actor_fragment(9002, 0, 1, 1, 0, 0.0, only_players,
                              local_pawn=0, view_fields=0))))
        time.sleep(0.5)
        _, data_py = get_json(PY_WEB_PORT, "/api/data", py_token)
        _, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token)
        checker.same("players-only /api/data", data_py, data_cxx)

        # 7. Subscriptions.
        for label, subscription in (
            ("clean", {"clean": True}),
            ("qualities+deadbox", {"qualities": [0, 1, 2], "deadbox": False}),
            ("ego", {"ego": {"enabled": True, "target_key": "mesh:1234",
                              "target_id": "", "radius_cm": 4000}}),
            ("resources-off", {"resources": False}),
        ):
            py_status, py_parsed, _ = post_json(
                PY_WEB_PORT, "/api/subscription", py_token, subscription,
                {"X-Client-Id": "client-" + label})
            cxx_status, cxx_parsed, _ = post_json(
                CXX_WEB_PORT, "/api/subscription", cpp_token, subscription,
                {"X-Client-Id": "client-" + label})
            checker.equal(f"subscription {label} status", py_status, cxx_status)
            get_status_py, data_py = get_json(PY_WEB_PORT, "/api/data", py_token,
                                  {"X-Client-Id": "client-" + label})
            get_status_cxx, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token,
                                   {"X-Client-Id": "client-" + label})
            checker.same(f"subscription {label} /api/data", data_py, data_cxx)

        # 8. Logs endpoint.
        _, logs_py = get_json(PY_WEB_PORT, "/api/logs", py_token)
        _, logs_cxx = get_json(CXX_WEB_PORT, "/api/logs", cpp_token)
        checker.same("/api/logs", logs_py, logs_cxx)

        # 9. Auth / aim / map / 404 behaviour.
        status_py, _, _ = http_request(PY_WEB_PORT, "GET", "/api/data")
        status_cxx, _, _ = http_request(CXX_WEB_PORT, "GET", "/api/data")
        checker.equal("unauthorized /api/data", status_py, status_cxx)

        status_py, parsed_py, _ = post_json(
            PY_WEB_PORT, "/api/aim", py_token, {"command": "status"})
        status_cxx, parsed_cxx, _ = post_json(
            CXX_WEB_PORT, "/api/aim", cpp_token, {"command": "status"})
        checker.equal("non-dev /api/aim status", status_py, status_cxx)
        checker.same("non-dev /api/aim body", parsed_py, parsed_cxx)

        status_py, parsed_py, _ = post_json(
            PY_WEB_PORT, "/api/aim", "963007", {"command": "status"})
        status_cxx, parsed_cxx, _ = post_json(
            CXX_WEB_PORT, "/api/aim", "963007", {"command": "status"})
        checker.equal("dev /api/aim status", status_py, status_cxx)
        checker.same("dev /api/aim body", parsed_py, parsed_cxx)

        status_py, parsed_py, _ = post_json(
            PY_WEB_PORT, "/api/aim", "963007", {"command": "rm -rf /"})
        status_cxx, parsed_cxx, _ = post_json(
            CXX_WEB_PORT, "/api/aim", "963007", {"command": "rm -rf /"})
        checker.equal("bad aim command", status_py, status_cxx)
        checker.same("bad aim body", parsed_py, parsed_cxx)

        status_py, parsed_py, _ = post_json(
            PY_WEB_PORT, "/api/map", py_token, {"map": "db.png"})
        status_cxx, parsed_cxx, _ = post_json(
            CXX_WEB_PORT, "/api/map", cpp_token, {"map": "db.png"})
        checker.equal("/api/map status", status_py, status_cxx)
        checker.same("/api/map body", parsed_py, parsed_cxx)

        for path in ("/api/nope", "/nope"):
            status_py, _, _ = http_request(PY_WEB_PORT, "GET", path,
                                           {"X-Auth-Token": py_token})
            status_cxx, _, _ = http_request(CXX_WEB_PORT, "GET", path,
                                            {"X-Auth-Token": cpp_token})
            checker.equal(f"404 {path}", status_py, status_cxx)

        status_py, parsed_py, _ = post_json(
            PY_WEB_PORT, "/api/aim", py_token,
            {"command": "status"}, {"X-Client-Id": "x"})
        status_cxx, parsed_cxx, _ = post_json(
            CXX_WEB_PORT, "/api/aim", cpp_token,
            {"command": "status"}, {"X-Client-Id": "x"})
        checker.equal("POST aim unauthorized body", status_py, status_cxx)

        # 10. Image auth + ETag conditional request.
        status_py, headers_py, body_py = http_request(
            PY_WEB_PORT, "GET", "/image/db.png", {"X-Auth-Token": py_token})
        status_cxx, headers_cxx, body_cxx = http_request(
            CXX_WEB_PORT, "GET", "/image/db.png", {"X-Auth-Token": cpp_token})
        checker.equal("image status", status_py, status_cxx)
        checker.equal("image bytes", len(body_py), len(body_cxx))
        checker.equal("image content-type", headers_py.get("content-type"),
                      headers_cxx.get("content-type"))
        etag_py = headers_py.get("etag", "")
        etag_cxx = headers_cxx.get("etag", "")
        checker.equal("image etag", etag_py, etag_cxx)
        if etag_py and etag_cxx:
            status_py, _, _ = http_request(
                PY_WEB_PORT, "GET", "/image/db.png",
                {"X-Auth-Token": py_token, "If-None-Match": etag_py})
            status_cxx, _, _ = http_request(
                CXX_WEB_PORT, "GET", "/image/db.png",
                {"X-Auth-Token": cpp_token, "If-None-Match": etag_cxx})
            checker.equal("image 304", status_py, status_cxx)

        status_py, _, _ = http_request(
            PY_WEB_PORT, "GET", "/image/db.png")
        status_cxx, _, _ = http_request(
            CXX_WEB_PORT, "GET", "/image/db.png")
        checker.equal("image unauthorized", status_py, status_cxx)

        # 11. Data token conditional request (204 path).
        _, data_headers_py, _ = http_request(
            PY_WEB_PORT, "GET", "/api/data", {"X-Auth-Token": py_token})
        token_value_py = data_headers_py.get("x-data-token", "")
        _, data_headers_cxx, _ = http_request(
            CXX_WEB_PORT, "GET", "/api/data", {"X-Auth-Token": cpp_token})
        token_value_cxx = data_headers_cxx.get("x-data-token", "")
        status_py, _, _ = http_request(
            PY_WEB_PORT, "GET", "/api/data",
            {"X-Auth-Token": py_token, "X-Data-Token": token_value_py})
        status_cxx, _, _ = http_request(
            CXX_WEB_PORT, "GET", "/api/data",
            {"X-Auth-Token": cpp_token, "X-Data-Token": token_value_cxx})
        checker.equal("data token 204", status_py, status_cxx)

        # 11a. HTTP/1.1 keep-alive: two requests on one connection.
        for port, token, label in ((PY_WEB_PORT, py_token, "python"),
                                   (CXX_WEB_PORT, cpp_token, "c++")):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            saw = []
            for _ in range(2):
                connection.request("GET", "/api/aim-state",
                                   headers={"X-Auth-Token": token})
                response = connection.getresponse()
                saw.append((response.status, response.read()))
            connection.close()
            checker.equal(f"keep-alive {label}",
                          [item[0] for item in saw], [200, 200])

        # 11b. Malformed frames must not disturb either server.
        malformed = [
            b"\x02",
            b"\x02RWVG",
            data_packet(typed_frame(6, b"short")),
            data_packet(struct.pack("<III", 0x47564352, 3, 999) + b"x"),
            data_packet(struct.pack("<III", 0x12345678, 3, 90) + b"\0" * 90),
            snapshot_packet(b"\0" * 8),
            b"\x06" + b"garbage",
            b"\x02" + b"aGVsbG8=",
            b"\x09badtype",
            data_packet(typed_frame(4, struct.pack("<I", 0xFFFFFFFF))),
            data_packet(typed_frame(6, struct.pack("<HHIIIIQIIIIiI", 3, 10, 1, 0, 1, 3,
                                                   0, 0, 0, 0, 0, 0, 1))),
        ]
        for frame in malformed:
            send(frame)
        time.sleep(0.4)
        _, data_py = get_json(PY_WEB_PORT, "/api/data", py_token)
        _, data_cxx = get_json(CXX_WEB_PORT, "/api/data", cpp_token)
        checker.same("post-malformed /api/data", data_py, data_cxx)

        # 12. Raw DMA request path through the console.
        cpp_stdin.write(b"cr3 1234\n")
        cpp_stdin.flush()
        deadline = time.time() + 5.0
        saw_cr3 = False
        while time.time() < deadline:
            text = (work / "cpp.log").read_text(errors="replace")
            if "UserDTB: 0x1111222233334444" in text:
                saw_cr3 = True
                break
            time.sleep(0.1)
        checker.equal("console cr3 response", saw_cr3, True)
        cpp_stdin.write(b"cpueaxh_ping\n")
        cpp_stdin.flush()
        deadline = time.time() + 5.0
        saw_ping = False
        while time.time() < deadline:
            text = (work / "cpp.log").read_text(errors="replace")
            if "Ping ACK: 1" in text:
                saw_ping = True
                break
            time.sleep(0.1)
        checker.equal("console ping response", saw_ping, True)

        responder.stop()
        sender.close()

        print(f"\n{checker.checks - checker.failures}/{checker.checks} checks passed")
        return 0 if checker.failures == 0 else 1
    finally:
        try:
            py_service.stop()
            py_core.shutdown()
        except Exception:
            pass
        try:
            cpp_stdin.close()
        except Exception:
            pass
        try:
            cpp_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cpp_proc.kill()
        cpp_log.close()


if __name__ == "__main__":
    sys.exit(main())
