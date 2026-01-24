# ue_types.py
import struct
from ue_memory import mem

class UE424_Offsets:
    # --- UObject ---
    UObject_Index       = 0x0C  # Index in GObjects
    UObject_Class       = 0x10  # UClass pointer
    UObject_Name        = 0x18  # FName (Index)
    UObject_Outer       = 0x20

    # --- UField (UE4.24 继承自 UObject) ---
    UField_Next         = 0x28  # 链表指针 (UE4.24)
    
    # --- UStruct ---
    UStruct_SuperStruct = 0x40 
    UStruct_Children    = 0x48  # UE4.24 使用 Children (UField*), 位于 0x48
    
    # --- UProperty (UE4.24) ---
    # 在 4.24 中 UProperty 是 UObject -> UField -> UProperty
    UProperty_ElementSize = 0x34 # or 0x38 depending on build
    UProperty_PropertyFlags = 0x40
    UProperty_Offset      = 0x44  # 成员变量偏移量 (通常是 0x44, 4.25+ 变成了 0x4C)

class FNameEntryArray_UE424:
    """针对 UE 4.24 的 GNames (TNameEntryArray)"""
    def __init__(self, gnames_addr):
        self.base = gnames_addr
        
    def get_name(self, index):
        # 1. 计算 Chunk 和 InChunk 索引
        # UE4.24 每个 Chunk 通常存 16384 (0x4000) 个指针
        chunk_idx = index // 0x4000
        in_chunk_idx = index % 0x4000
        
        # 2. 读取 Chunk 指针
        # GNames 指向一个指针数组
        chunk_ptr = mem.read_ptr(self.base + chunk_idx * 8)
        if not chunk_ptr: return f"ErrChunk[{index}]"
        
        # 3. 读取 FNameEntry 指针 (这是旧版与新版最大的区别)
        # 旧版：Chunk 里存的是 FNameEntry* (指针)
        # 新版：Chunk 里存的是实际数据
        entry_ptr = mem.read_ptr(chunk_ptr + in_chunk_idx * 8)
        if not entry_ptr: return f"ErrEntry[{index}]"
        
        # 4. 读取字符串
        # UE4.24 FNameEntry 结构: { FNameEntry* NextHash; int32 Index; char AnsiName[]; }
        # 字符串偏移通常在 0x0C 或 0x10
        return mem.read_string(entry_ptr + 0x10) 

class TUObjectArray:
    """UE4 全局对象数组 (Chunked)"""
    def __init__(self, gobjects_addr):
        self.base = gobjects_addr
        self.num_elements = mem.read_u32(self.base + 0x14) 
    
    def get_object_ptr(self, index):
        if index >= self.num_elements: return 0
        
        # GObjects -> Objects (0x10) -> Chunks
        objects_ptr = mem.read_ptr(self.base + 0x10)
        if not objects_ptr: return 0
        
        chunk_index = index // 0x10000
        in_chunk_index = index % 0x10000
        
        chunk_addr = mem.read_ptr(objects_ptr + chunk_index * 8)
        if not chunk_addr: return 0
        
        # FUObjectItem 大小通常是 24 字节
        item_addr = chunk_addr + (in_chunk_index * 24)
        return mem.read_ptr(item_addr) # 第一个成员是 Object*

class UStruct:
    def __init__(self, addr):
        self.addr = addr
    
    def get_children(self):
        # UE4.24: Children at 0x48
        return mem.read_ptr(self.addr + UE424_Offsets.UStruct_Children)
    
    def get_super(self):
        return mem.read_ptr(self.addr + UE424_Offsets.UStruct_SuperStruct)

class UProperty:
    def __init__(self, addr):
        self.addr = addr
    
    def get_next(self):
        # UE4.24: UField_Next at 0x28
        addr = mem.read_ptr(self.addr + UE424_Offsets.UField_Next)
        return UProperty(addr) if addr else None
    
    def get_name_id(self):
        # UObject_Name at 0x18
        return mem.read_u32(self.addr + UE424_Offsets.UObject_Name)
    
    def get_offset(self):
        # UProperty_Offset at 0x44 (UE4.24)
        return mem.read_u32(self.addr + UE424_Offsets.UProperty_Offset)
