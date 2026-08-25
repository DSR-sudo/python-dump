import struct
import re
import ue_memory

class UEScanner:
    def __init__(self, api, base_addr):
        self.api = api
        self.base = base_addr # 必须传入基址

    def python_scan(self, pattern_str, max_size=0xA00000): # 默认扫前 6MB
        """在 Python 端分块读取内存并匹配特征码"""
        if not self.base:
            return 0
            
        print(f"[*] Reading {max_size/1024/1024:.1f}MB memory for local scan...")
        
        # =========================================================
        # [优化] 分块读取逻辑 (防止 TCP 帧缓冲区溢出)
        # =========================================================
        CHUNK_LIMIT = 1 * 1024 * 1024  # 每次只读 1MB
        full_data = bytearray()
        
        left = max_size
        offset = 0
        
        while left > 0:
            to_read = min(left, CHUNK_LIMIT)
            # 打印进度提示（可选）
            print(f"    -> Reading chunk: {offset//1024}KB / {max_size//1024}KB")
            
            chunk = ue_memory.mem.read_bytes(self.base + offset, to_read)
            
            if chunk:
                full_data.extend(chunk)
            else:
                # 如果中间某一块读取失败，不要直接放弃，尝试继续或者打印警告
                print(f"[-] Warning: Chunk at offset {offset:X} dropped/failed.")
                # 填充 0 以保持偏移对齐，防止特征码错位
                full_data.extend(b'\x00' * to_read)
            
            left -= to_read
            offset += to_read
            
        if not full_data:
            print("[-] Failed to read memory for scanning.")
            return 0
            
        # 转换为 bytes 以供正则使用
        mem_data = bytes(full_data)
        
        # =========================================================
        # 正则匹配逻辑 (保持不变)
        # =========================================================
        import re
        parts = pattern_str.strip().split()
        regex_parts = []
        for p in parts:
            if p == '?' or p == '??':
                regex_parts.append(b'.')
            else:
                regex_parts.append(re.escape(bytes.fromhex(p)))
        
        regex = b''.join(regex_parts)
        
        match = re.search(regex, mem_data)
        if match:
            return self.base + match.start()
        return 0

    def resolve_relative(self, addr, offset_pos=3, instr_len=7):
        """解析 RIP 相对寻址"""
        if not addr: return 0
        
        # 读取指令中的偏移量 (int32)
        offset_val_bytes = ue_memory.mem.read_bytes(addr + offset_pos, 4)
        if not offset_val_bytes: return 0
        
        offset_val = struct.unpack("<i", offset_val_bytes)[0]
        return addr + instr_len + offset_val

    def find_gnames(self):
        
        print(f"[*] Scanning for GNames at Base: 0x{self.base:X}...")
        
        # 特征码 (4.24 / 4.25 通用): 48 8B 05 ? ? ? ? 48 85 C0 75 50 B9
        # 对应: MOV RAX, [GNames]; TEST RAX, RAX; JNZ ...
        #pat = "48 8B 05 ? ? ? ? 48 85 C0 75 50 B9" 
        
        pat = "48 8B 1D ? ? ? ? 48 85 DB 75"
        addr = self.python_scan(pat)
        return self.resolve_relative(addr) if addr else 0

    def find_gobjects(self):
    
        print(f"[*] Scanning for GObjects at Base: 0x{self.base:X}...")
        
        # 特征码: 48 8B 05 ? ? ? ? 48 8B 0C C8
        # 对应: MOV RAX, [GObjects]; MOV RCX, [RAX+RCX*8]
        pat = "48 8B 05 ? ? ? ? 48 8B 0C C8"
        addr = self.python_scan(pat)
        return self.resolve_relative(addr) if addr else 0
