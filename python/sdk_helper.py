# sdk_helper.py
import json
import os

class SDKLoader:
    def __init__(self, sdk_path="../SDK"):
        # 1. 加载全局 RVA
        offsets_file = os.path.join(sdk_path, "OffsetsInfo.json")
        with open(offsets_file, "r") as f:
            data = json.load(f)["data"]
            # 将列表转换为字典方便查询: {'OFFSET_GNAMES': 387986176, ...}
            self.global_offsets = {item[0]: item[1] for item in data}
        
        # 2. 加载类偏移
        classes_file = os.path.join(sdk_path, "ClassesInfo.json")
        with open(classes_file, "r") as f:
            self.classes = {}
            for item in json.load(f)["data"]:
                for class_name, members in item.items():
                    self.classes[class_name] = members

    def get_rva(self, name):
        """获取 GNames/GObjects 的相对偏移"""
        return self.global_offsets.get(name, 0)

    def get_member_offset(self, class_name, member_name):
        """获取指定类成员的偏移量"""
        members = self.classes.get(class_name)
        if not members: return None
        for m in members:
            if member_name in m:
                # 返回 [Type, Offset, Size, ...] 中的 Offset
                return m[member_name][1]
        return None

# 预定义的 RVA 常量
# OFFSET_GNAMES: 387986176 (0x17203AC0)
# OFFSET_GOBJECTS: 388135928 (0x172283F8)
