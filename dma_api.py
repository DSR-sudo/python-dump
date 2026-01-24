import struct
from dma_protocol import *

class DMAApi:
    def __init__(self, core):
        self.core = core
        self.cached_pid = 0
        self.cached_user_dtb = 0

    def get_cr3(self, pid):
        """获取并缓存 CR3 (返回 User 和 Kernel 两个 DTB)"""
        # 按照 PACKET_FMT 发送请求
        payload = pack_command(CMD_GET_CR3, pid, 0, 0, 0)
        data = self.core.request(payload, 16) # 驱动会回传 16 字节
    
        if data and len(data) >= 16:
            # <QQ 代表两个 8 字节的无符号长整型
            user_cr3, kernel_cr3 = struct.unpack("<QQ", data[:16])
        
            # 更新本地缓存 (通常读写内存使用 User CR3)
            self.cached_pid = pid
            self.cached_user_dtb = user_cr3
        
            return user_cr3, kernel_cr3


    def read_mem(self, pid, addr, size):
        """读取内存 (自动处理 CR3)"""
        if pid != self.cached_pid:
            if not self.get_cr3(pid)[0]: return None
        payload = pack_command(CMD_READ_MEM, pid, addr, self.cached_user_dtb, size)
        return self.core.request(payload, size)

    def scan_pattern(self, module, pattern):
        """扫描特征码"""
        payload = pack_scan(module, pattern)
        # 扫描可能较慢，给予 5 秒超时
        data = self.core.request(payload, 8, timeout=5.0)
        if data:
            return struct.unpack("<Q", data)[0]
        return 0
