# ue_reflection.py
from ue_types import UStruct, UProperty

class ReflectionDumper:
    def __init__(self, name_pool):
        self.name_pool = name_pool

    def dump_struct(self, struct_addr):
        if not struct_addr: return

        print(f"\n[+] Dumping Struct (UE4.24) at 0x{struct_addr:X}")
        current_struct = UStruct(struct_addr)
        
        # 获取继承链，从父类开始打印会更清晰
        inheritance_chain = []
        ptr = struct_addr
        while ptr:
            inheritance_chain.insert(0, ptr)
            ptr = UStruct(ptr).get_super()
            
        total_props = []
        
        for s_addr in inheritance_chain:
            s = UStruct(s_addr)
            child_addr = s.get_children()
            
            # 读取类名
            class_name_id = mem.read_u32(s_addr + 0x18)
            class_name = self.name_pool.get_name(class_name_id)
            # print(f"  --- {class_name} ---") 
            
            while child_addr:
                prop = UProperty(child_addr)
                
                name_id = prop.get_name_id()
                name_str = self.name_pool.get_name(name_id)
                offset = prop.get_offset()
                
                # 过滤掉一些非成员变量的 UField (如 UFunction, UEnum 等)
                # 简单判断：如果 offset 为 0 且名字不是 None，可能是函数
                if offset > 0: 
                    total_props.append((offset, name_str, class_name))
                
                next_prop = prop.get_next()
                child_addr = next_prop.addr if next_prop else 0
        
        # 排序并打印
        total_props.sort(key=lambda x: x[0])
        
        print(f"{'Offset':<10} {'Name':<40} {'Inherited From'}")
        print("-" * 70)
        for offset, name, parent in total_props:
            print(f"0x{offset:04X}     {name:<40} {parent}")
