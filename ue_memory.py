# ue_memory.py
import struct

class UEMemory:
    def __init__(self, api, pid):
        self.api = api
        self.pid = pid

    def read_ptr(self, addr):
        data = self.api.read_mem(self.pid, addr, 8)
        return struct.unpack("<Q", data)[0] if data else 0

    def read_u32(self, addr):
        data = self.api.read_mem(self.pid, addr, 4)
        return struct.unpack("<I", data)[0] if data else 0

    def read_u16(self, addr):
        data = self.api.read_mem(self.pid, addr, 2)
        return struct.unpack("<H", data)[0] if data else 0
        
    def read_u64(self, addr):
        data = self.api.read_mem(self.pid, addr, 8)
        return struct.unpack("<Q", data)[0] if data else 0

    def read_bytes(self, addr, size):
        return self.api.read_mem(self.pid, addr, size)

# 全局实例，由 main 初始化
mem = None
