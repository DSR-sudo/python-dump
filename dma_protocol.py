import struct

# ==========================================
# 协议定义 (L0) - 修复字节对齐
# ==========================================

DRIVER_IP = "10.10.10.75"
DRIVER_PORT = 10005
BIND_PORT = 10005

MAGIC_KEY = 0xDEADBEEF
CMD_READ_MEM = 1
CMD_GET_CR3 = 3
CMD_ENUM_USER_MODULES = 5

PACKET_TYPE_LOG  = 0x01
PACKET_TYPE_DATA = 0x02

# --- 核心包格式 (Standard Request) ---
# C++ 结构: Magic(4) + Cmd(1) + [Padding(3)] + Value(8) + Addr(8) + Size(8)
# Value 复用: GetCr3时为PID, ReadMem时为CR3
# 修复: 增加 '3x' (3个填充字节) 以匹配 C++ x64 对齐
PACKET_FMT = "<IBQQI" 
# --- 扫描包格式 (Scan Request) ---
# C++ 结构: Magic(4) + Cmd(1) + Module(64) + ...
# Char 数组通常只有 1 字节对齐，紧跟在 Cmd 后面，无需 Padding
PACKET_SCAN_FMT = "<IB64s8s64s64sI"

def parse_packet_header(data: bytes):
    """
    返回 (packet_type, payload)
    如果数据为空或长度不足，抛出异常或返回 None
    """
    if not data or len(data) < 1:
        return None, None
    
    pkt_type = data[0]
    payload = data[1:]
    return pkt_type, payload


def pack_read_req(cr3, addr, size):
    """构建读取请求 (Value=CR3)"""
    return struct.pack(PACKET_FMT, MAGIC_KEY, CMD_READ_MEM, cr3, addr, size)

def pack_cr3_req(pid):
    """构建CR3请求 (Value=PID)"""
    return struct.pack(PACKET_FMT, MAGIC_KEY, CMD_GET_CR3, pid, 0, 0)

def pack_enum_modules_req(pid):
    """
    构建枚举用户态模块请求
    C++ 端逻辑: currentReq.Value 存放 PID, 执行 StreamUserModules_CR3
    """
    # 使用标准 PACKET_FMT: Magic(4) + Cmd(1) + Padding(3) + Value(8) + Addr(8) + Size(8)
    # 对齐说明: '3x' 填充用于匹配 x64 C++ 结构体对齐
    return struct.pack(PACKET_FMT, 
                       MAGIC_KEY, 
                       CMD_ENUM_USER_MODULES, 
                       pid,  # 对应 C++ 的 currentReq.Value
                       0,    # Address 不使用
                       0)    # Size 不使用
