import struct

class MemoryReader:
    """本地内存解析器：封装一个 bytes 对象，提供类似内存读取的接口"""
    def __init__(self, data: bytes, base_addr: int):
        self.data = data
        self.base_addr = base_addr
        self.size = len(data)

    def read_u8(self, offset):
        if offset + 1 > self.size: return 0
        return self.data[offset]

    def read_u16(self, offset):
        if offset + 2 > self.size: return 0
        return struct.unpack_from("<H", self.data, offset)[0]

    def read_u32(self, offset):
        if offset + 4 > self.size: return 0
        return struct.unpack_from("<I", self.data, offset)[0]

    def read_u64(self, offset):
        if offset + 8 > self.size: return 0
        return struct.unpack_from("<Q", self.data, offset)[0]
    
    def read_ptr(self, offset):
        return self.read_u64(offset)

class UEMemory:
    def __init__(self, api, pid):
        self.api = api
        self.pid = pid

    def read_bytes(self, addr, size):
        # 这里的 pack_command 逻辑由 api 层处理
        return self.api.read_mem(addr, size)

    def read_smart(self, addr, size):
        """读取一块内存，并返回 MemoryReader 对象供本地解析"""
        data = self.read_bytes(addr, size)
        if data:
            return MemoryReader(data, addr)
        return None
    
    def read_ptr(self, addr):
        data = self.read_bytes(addr, 8)
        return struct.unpack("<Q", data)[0] if data else 0

    def read_u32(self, addr):
        data = self.read_bytes(addr, 4)
        return struct.unpack("<I", data)[0] if data else 0
        
    def read_string(self, addr, size=64):
        data = self.read_bytes(addr, size)
        if not data: return ""
        try:
            return data.split(b'\x00')[0].decode('utf-8', errors='ignore')
        except: return ""

# 全局实例，初始为 None
mem = None