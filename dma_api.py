import struct
from dma_protocol import *


#配合驱动 Lookaside List 大小 (4352 bytes)
# 我们预留头部空间，安全设置为 4096
DRIVER_MAX_CHUNK = 65536

class DMAApi:
    def __init__(self, core):
        self.core = core
        self.cached_pid = 0
        self.cached_dtb = 0 # 这是 User CR3
        self.cached_kdtb = 0 #这是Kernel CR3

    def get_cr3(self, pid):
        """获取 CR3 和 模块基址"""
        payload = pack_cr3_req(pid)
        # [修改] 现在接收 24 字节
        data = self.core.request_bytes(payload, 24) 
        
        if data and len(data) >= 24:
            # 解析 3 个 Q (unsigned long long)
            user_cr3, kernel_cr3, base_addr = struct.unpack("<QQQ", data[:24])
            
            self.cached_pid = pid
            self.cached_dtb = kernel_cr3 if kernel_cr3 != 0 else user_cr3
            self.cached_kdtb = kernel_cr3
            
            # [新增] 返回基址
            return user_cr3, kernel_cr3, base_addr
            
        return None, None, None


    def read_chunk(self, addr, size):
        # 内部辅助函数
        payload = pack_read_req(self.cached_dtb, addr, size)
        return self.core.request_bytes(payload, size)

    def read_mem(self, addr, size):
        """
        [极速版] 移除客户端切片，直接请求全量数据
        """
        if self.cached_dtb == 0: return None
        
        # 1. 临时增大 UDP 接收缓冲区 (防止 Python 处理不过来导致丢包)
        # 这一步通常在 dma_core 初始化时做，确保 SO_RCVBUF 至少 4MB+
        
        # 2. 直接构造全量请求
        # 假设我们要读 30MB，直接告诉驱动 "给我 30MB"
        # 你的驱动有 while (totalProcessed < size) 循环，它完全能处理！
        payload = pack_read_req(self.cached_dtb, addr, size)
        
        # 3. 调用核心接收逻辑
        # core.request_bytes 需要能处理 size 这么大的数据接收
        # 它会一直 recv 直到凑够 size 字节
        print(f"DEBUG REQ: {payload.hex()}")
        return self.core.request_bytes(payload, size, timeout=10.0) # 超时设长一点

    def enum_user_modules(self, pid):
        """发送枚举模块请求"""
        payload = pack_enum_modules_req(pid)
        # 注意: 这是一个流式指令，C++ 会发送多个 DATA 包回来
        # 此处仅负责发送指令，接收逻辑需由 core.py 的 _receiver_loop 处理
        self.core.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
