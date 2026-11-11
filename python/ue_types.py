import struct
import ue_memory
from ue_memory import MemoryReader

class UE_Offsets:
    # UE 4.24 专用
    UStruct_SuperStruct     = 0x40 
    UStruct_Children        = 0x48 
    UField_Class            = 0x10
    UField_Next             = 0x28
    UField_Name             = 0x18
    UProperty_Offset        = 0x44
    UProperty_PropertyFlags = 0x38 # 或 0x40
    
    UBoolProperty_ByteMask  = 0x70 
    UBoolProperty_FieldMask = 0x72
    UArrayProperty_Inner    = 0x70 

# ==========================================
# 兼容模式类 (使用 ue_memory.mem)
# ==========================================

class UStruct:
    def __init__(self, addr):
        self.addr = addr
    def get_children(self):
        return ue_memory.mem.read_ptr(self.addr + UE_Offsets.UStruct_Children)
    def get_super(self):
        return ue_memory.mem.read_ptr(self.addr + UE_Offsets.UStruct_SuperStruct)

class UProperty:
    def __init__(self, addr):
        self.addr = addr
    def get_next(self):
        addr = ue_memory.mem.read_ptr(self.addr + UE_Offsets.UField_Next)
        return UProperty(addr) if addr else None
    def get_name_id(self):
        return ue_memory.mem.read_u32(self.addr + UE_Offsets.UField_Name)
    def get_offset(self):
        return ue_memory.mem.read_u32(self.addr + UE_Offsets.UProperty_Offset)

class TUObjectArray:
    def __init__(self, gobjects_addr):
        # Issue 3 修复：在 UE 4.24 中，GUObjectArray 包含一个嵌套的 TUObjectArray 结构
        # 嵌套结构通常位于 0x10 偏移处
        self.base = gobjects_addr
        # Objects 指针位于嵌套结构的 0x00 (总偏移 0x10)
        self.objects_ptr = ue_memory.mem.read_ptr(self.base + 0x10)
        # NumElements 位于嵌套结构的 0x0C (总偏移 0x1C)
        self.num_elements = ue_memory.mem.read_u32(self.base + 0x1C) 
    
    def get_object_ptr(self, index):
        if index >= self.num_elements or not self.objects_ptr: return 0
        
        chunk_idx = index // 0x10000
        in_chunk_idx = index % 0x10000
        
        # 从已解析的 objects_ptr 开始读取 Chunk
        chunk_addr = ue_memory.mem.read_ptr(self.objects_ptr + chunk_idx * 8)
        if not chunk_addr: return 0
        
        # UE 4.24 的 FUObjectItem 大小通常为 24 字节 (0x18)
        return ue_memory.mem.read_ptr(chunk_addr + in_chunk_idx * 24)

class FNameEntryArray_UE424:
    def __init__(self, gnames_addr):
        self.base = gnames_addr
        
    def get_name(self, index):
        chunk_idx = index // 0x4000
        in_chunk_idx = index % 0x4000
        
        # 同样需要应用 0x10 的 Chunks 数组偏移
        chunk_ptr = ue_memory.mem.read_ptr(self.base + 0x10 + chunk_idx * 8)
        if not chunk_ptr: return f"ErrChunk[{index}]"
        
        entry_ptr = ue_memory.mem.read_ptr(chunk_ptr + in_chunk_idx * 8)
        if not entry_ptr: return f"ErrEntry[{index}]"
        
        return ue_memory.mem.read_string(entry_ptr + 0x10)

# ==========================================
# 缓存 & 智能读取 (使用 MemoryReader)
# ==========================================

class FNameCache:
    def __init__(self, gnames_addr):
        self.base = gnames_addr
        self.cache = {}         # ID -> Name 字符串缓存
        self.chunk_ptrs = {}    # ChunkID -> ChunkAddr 缓存
        self.is_cached = False

    def build_cache(self):
        print("[*] Enabling On-Demand GNames Cache...")
        try:
            # Issue 2 修复：FNameEntryArray 的 Chunks 指针数组从 0x10 偏移开始
            # 0x00-0x0F 存储的是 Lock 和计数器
            raw_chunks = ue_memory.mem.read_bytes(self.base + 0x10, 128 * 8)
            if raw_chunks:
                for i in range(128):
                    ptr = struct.unpack_from("<Q", raw_chunks, i * 8)[0]
                    if ptr:
                        self.chunk_ptrs[i] = ptr
                print(f"[+] Pre-cached {len(self.chunk_ptrs)} GNames chunk pointers.")
            
            self.is_cached = True
            print("[+] Name caching active. SDK generation will accelerate over time.")
        except Exception as e:
            print(f"[-] Cache init failed: {e}")
            self.is_cached = False

    def get_name(self, index):
        # 1. 优先查本地字典 (极速)
        if index in self.cache:
            return self.cache[index]
        
        # 2. 本地没有，走远程读取逻辑
        chunk_idx = index // 0x4000
        in_chunk_idx = index % 0x4000
        
        # 尝试从预读的 Chunk 表里拿地址
        chunk_ptr = self.chunk_ptrs.get(chunk_idx)
        if not chunk_ptr:
            # 修复：手动读取时也必须加上 0x10 的起始偏移
            chunk_ptr = ue_memory.mem.read_ptr(self.base + 0x10 + chunk_idx * 8)
            if chunk_ptr:
                self.chunk_ptrs[chunk_idx] = chunk_ptr # 存起来下次用
        
        if not chunk_ptr: return f"ErrChunk[{index}]"
        
        # 读取 Entry 指针
        entry_ptr = ue_memory.mem.read_ptr(chunk_ptr + in_chunk_idx * 8)
        if not entry_ptr: return f"ErrEntry[{index}]"
        
        # 读取字符串 (FNameEntry->String 在 0x10 偏移)
        name = ue_memory.mem.read_string(entry_ptr + 0x10)
        
        # 3. 读到了就存入缓存，下次不再读
        if name:
            self.cache[index] = name
            return name
            
        return "None"

class FPropertyReader:
    def __init__(self, reader: MemoryReader, offset_in_reader):
        self.reader = reader
        self.base = offset_in_reader 
    def get_offset(self):
        return self.reader.read_u32(self.base + UE_Offsets.UProperty_Offset)
    def get_name_id(self):
        return self.reader.read_u32(self.base + UE_Offsets.UField_Name)
    def get_flags(self):
        return self.reader.read_u64(self.base + UE_Offsets.UProperty_PropertyFlags)
    def get_next_ptr(self):
        return self.reader.read_ptr(self.base + UE_Offsets.UField_Next)
    def get_byte_mask(self):
        return self.reader.read_u8(self.base + UE_Offsets.UBoolProperty_ByteMask)
    def get_field_mask(self):
        return self.reader.read_u8(self.base + UE_Offsets.UBoolProperty_FieldMask)
    def get_inner_ptr(self):
        return self.reader.read_ptr(self.base + UE_Offsets.UArrayProperty_Inner)