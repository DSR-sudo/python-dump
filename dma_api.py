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

    # RWbase business commands (CMD 11/12 and 6~10)
    def vm_get_process_info(self, pid):
        payload = pack_vm_get_process_info_req(pid)
        data = self.core.request_bytes(payload, 24)
        if not data or len(data) < 24:
            return None
        cr3, eprocess, peb = struct.unpack("<QQQ", data[:24])
        return cr3, eprocess, peb

    def vm_memory_copy(self, pid, src_va, size):
        payload = pack_vm_memory_copy_req(pid, src_va, size)
        return self.core.request_bytes(payload, size)

    def install_callback_interceptor(self, pid, target_va, cold_va):
        payload = pack_install_callback_interceptor_req(pid, target_va, cold_va)
        return self._request_u64(payload)

    def uninstall_callback_interceptor(self, pid, target_va):
        payload = pack_uninstall_callback_interceptor_req(pid, target_va)
        return self._request_u64(payload)

    def cold_code_adapt_memory_page(self, pid, target_cold_va, shellcode: bytes):
        payload = pack_cold_code_adapt_memory_page_req(pid, target_cold_va, shellcode)
        return self._request_u64(payload)

    def cold_code_adapt_physical(self, pid, target_cold_va, shellcode: bytes):
        payload = pack_cold_code_adapt_physical_req(pid, target_cold_va, shellcode)
        return self._request_u64(payload)

    def memory_page_adapt(self, pid, target_va, patch: bytes):
        payload = pack_memory_page_adapt_req(pid, target_va, patch)
        return self._request_u64(payload)
