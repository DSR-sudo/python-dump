import struct
from dma_protocol import *


DRIVER_MAX_CHUNK = 65536


class DMAApi:
    def __init__(self, core):
        self.core = core
        self.cached_pid = 0
        self.cached_dtb = 0
        self.cached_kdtb = 0

    def get_cr3(self, pid):
        payload = pack_cr3_req(pid)
        data = self.core.request_bytes(payload, 24)

        if data and len(data) >= 24:
            user_cr3, kernel_cr3, base_addr = struct.unpack("<QQQ", data[:24])
            self.cached_pid = pid
            self.cached_dtb = kernel_cr3 if kernel_cr3 != 0 else user_cr3
            self.cached_kdtb = kernel_cr3
            return user_cr3, kernel_cr3, base_addr

        return None, None, None

    def read_chunk(self, addr, size):
        payload = pack_read_req(self.cached_dtb, addr, size)
        return self.core.request_bytes(payload, size)

    def read_mem(self, addr, size):
        if self.cached_dtb == 0:
            return None

        payload = pack_read_req(self.cached_dtb, addr, size)
        print(f"DEBUG REQ: {payload.hex()}")
        return self.core.request_bytes(payload, size, timeout=10.0)

    def enum_user_modules(self, pid):
        payload = pack_enum_modules_req(pid)
        self.core.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))

    def _request_u64(self, payload):
        data = self.core.request_bytes(payload, 8)
        if not data or len(data) < 8:
            return None
        return struct.unpack("<Q", data[:8])[0]

    def start_data_threads(self):
        payload = pack_start_data_threads_req()
        return self._request_u64(payload)

    def stop_data_threads(self):
        payload = pack_stop_data_threads_req()
        return self._request_u64(payload)
