import socket
import threading
import time
from dma_protocol import *

DEFAULT_EXPECTED_TRANSFER_BPS = 8 * 1024 * 1024  # 8 MB/s conservative baseline.
DEFAULT_TRANSFER_GRACE_SEC = 5.0
DEFAULT_IDLE_TIMEOUT_SEC = 2.5
WAIT_SLICE_SEC = 0.2


class DMACore:
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
        self.lock = threading.Lock()

        threading.Thread(target=self._receiver_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _handle_rwvg_typed_frame(self, typed_kind, typed_payload):
        if typed_kind == RWVG_TYPE_UTILS:
            self.rwvg_stats["utils_frames"] += 1
        elif typed_kind == RWVG_TYPE_PLAYER:
            self.rwvg_stats["player_frames"] += 1
        elif typed_kind == RWVG_TYPE_ITEM:
            self.rwvg_stats["item_frames"] += 1
        self.rwvg_stats["typed_bytes"] += len(typed_payload)

        if not self.rwvg_stream_detected:
            print("[+] RWVG typed stream detected (GameCore data path aligned).")
            self.rwvg_stream_detected = True

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

    def _receiver_loop(self):
        print(f"[*] UDP Receiver started on port {BIND_PORT}")
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
                        if "ALIVE_ACK" in msg or "DRIVER_ONLINE" in msg:
                            if not self.driver_online:
                                print("[+] Driver is ONLINE.")
                            self.driver_online = True
                            continue

                        if msg:
                            print(f"\r[LOG] {msg}\n>> ", end="")
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
                        print(
                            "[+] Host-compat aggregate stream detected "
                            f"(players={host_agg['player_count']}, items={host_agg['item_count']})."
                        )
                        self.host_aggregate_detected = True
            except Exception:
                if not self.is_running:
                    break

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
            print(
                f"[*] Expecting {size} bytes, timeout set to {total_timeout:.1f}s "
                f"(idle {idle_timeout:.1f}s)"
            )

            timeout_reason = "transfer timeout"
            while True:
                if self.recv_event.wait(timeout=WAIT_SLICE_SEC):
                    return self.buffer

                now_ts = time.monotonic()
                if now_ts >= deadline_ts:
                    timeout_reason = "transfer timeout"
                    break

                if self.recvd_bytes > 0 and (now_ts - self.last_data_ts) >= idle_timeout:
                    timeout_reason = f"idle timeout ({idle_timeout:.1f}s no new packets)"
                    break

            self.expected_size = 0
            percent = (self.recvd_bytes / size) * 100 if size > 0 else 0.0
            print(
                f"[-] Timeout ({timeout_reason})! Received "
                f"{self.recvd_bytes}/{size} bytes ({percent:.1f}%)."
            )
            return None
