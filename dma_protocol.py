import struct

# 配置
DRIVER_IP = "192.168.2.187"
DRIVER_PORT = 12003
BIND_PORT = 12003

# 指令
MAGIC_KEY = 0xDEADBEEF
CMD_READ_MEM = 1
CMD_GET_CR3 = 3
CMD_SCAN_PATTERN = 4

# 6参数格式: Magic(I), Cmd(B), Pid(I), Addr(Q), CR3(Q), Size(I)
PACKET_FMT = "<IBIQQI" 
# 扫描格式
PACKET_SCAN_FMT = "<IB64s8s64s64sI"

def pack_command(cmd, pid, addr, cr3, size):
    """构建 6 参数的标准控制包"""
    return struct.pack(PACKET_FMT, MAGIC_KEY, cmd, pid, addr, cr3, size)

def pack_scan(module, signature_str, section=""):
    """构建特征码扫描包"""
    parts = signature_str.strip().split()
    sig_bytes = [0 if p in ('?', '??') else int(p, 16) for p in parts]
    mask_chars = ["?" if p in ('?', '??') else "x" for p in parts]
    sig_len = len(sig_bytes)
    
    return struct.pack(PACKET_SCAN_FMT, MAGIC_KEY, CMD_SCAN_PATTERN, 
                       module.encode().ljust(64, b'\x00'), 
                       section.encode().ljust(8, b'\x00'),
                       bytes(sig_bytes).ljust(64, b'\x00'), 
                       "".join(mask_chars).encode().ljust(64, b'\x00'), 
                       sig_len)
