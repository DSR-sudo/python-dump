from ue_types import UE_Offsets, FPropertyReader
import ue_memory # [关键修复]

class SDKGenerator:
    def __init__(self, name_pool, obj_array):
        self.name_pool = name_pool
        self.obj_array = obj_array

    def generate_class_sdk(self, class_addr):
        struct_list = []
        curr = class_addr
        while curr:
            struct_list.insert(0, curr)
            curr = ue_memory.mem.read_ptr(curr + UE_Offsets.UStruct_SuperStruct)
            
        print(f"[*] Analyzing Inheritance Chain: {len(struct_list)} classes")
        
        full_properties = []
        
        for struct_addr in struct_list:
            children_ptr = ue_memory.mem.read_ptr(struct_addr + UE_Offsets.UStruct_Children)
            if not children_ptr: continue
            
            curr_prop = children_ptr
            while curr_prop:
                # 使用 Smart Read 读取 192 字节
                reader = ue_memory.mem.read_smart(curr_prop, 0xC0)
                if not reader: break
                
                prop = FPropertyReader(reader, 0)
                
                p_name_id = prop.get_name_id()
                p_name = self.name_pool.get_name(p_name_id)
                p_offset = prop.get_offset()
                
                # 类型推断 (简化版: 仅作为示例)
                p_type_name = "Unknown"
                class_ptr = reader.read_ptr(UE_Offsets.UField_Class)
                if class_ptr:
                     # 这里需要远程读 Class Name，为了速度可以缓存
                    c_name_id = ue_memory.mem.read_u32(class_ptr + 0x18)
                    p_type_name = self.name_pool.get_name(c_name_id)

                extra = ""
                if "BoolProperty" in p_type_name:
                    extra = f" (Bit: {prop.get_field_mask():02X})"

                full_properties.append({
                    "offset": p_offset,
                    "name": p_name,
                    "type": p_type_name,
                    "extra": extra
                })
                
                curr_prop = prop.get_next_ptr()

        full_properties.sort(key=lambda x: x["offset"])
        
        # 获取当前类名
        my_name = self.name_pool.get_name(ue_memory.mem.read_u32(class_addr + 0x18))
        print(f"\n// Generated SDK for {my_name}")
        print(f"class {my_name} : public Super {{")
        for p in full_properties:
            print(f"    {p['type']:<20} {p['name']:<30}; // 0x{p['offset']:04X}{p['extra']}")
        print("};")