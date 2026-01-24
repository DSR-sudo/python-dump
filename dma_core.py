import socket
import threading
import time
import struct
from dma_protocol import DRIVER_IP, DRIVER_PORT, BIND_PORT

class DMACore:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", BIND_PORT))
        
        self.is_running = True
        self.driver_online = False
        self.last_keepalive = time.time()
        self.seq = 0
        
        self.recv_event = threading.Event()
        self.buffer = bytearray()
        self.target_size = 0

        threading.Thread(target=self._receiver_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _receiver_loop(self):
        while self.is_running:
            try:
                data, addr = self.sock.recvfrom(65536)
                # 拦截心跳包响应，防止污染缓冲区
                if data.startswith(b"[ACK]") or data.startswith(b"Alive"):
                    self.last_keepalive = time.time()
                    self.driver_online = True
                    continue

                self.last_keepalive = time.time()
                if self.target_size > 0:
                    self.buffer.extend(data)
                    if len(self.buffer) >= self.target_size:
                        self.recv_event.set()
                else:
                    # 异步日志处理
                    msg = data.decode('utf-8', errors='ignore').strip()
                    if msg: print(f"\r[LOG] {msg}\n>> ", end="")
            except: pass

    def _heartbeat_loop(self):
        """每秒心跳，20秒无响应警告"""
        while self.is_running:
            try:
                payload = b'HELO' + struct.pack('<I', self.seq) + b'\x01'
                self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
                self.seq += 1
                
                if self.driver_online and (time.time() - self.last_keepalive > 20.0):
                    self.driver_online = False
                    print(f"\n[!] WARNING: Driver Timeout (20s+)")
                time.sleep(1.0)
            except: pass

    def request(self, payload, expected_size, timeout=3.0):
        self.recv_event.clear()
        self.buffer = bytearray()
        self.target_size = expected_size
        self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
        if self.recv_event.wait(timeout=timeout):
            return bytes(self.buffer[:expected_size])
        return None
