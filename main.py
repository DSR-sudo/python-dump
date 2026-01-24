# main.py
from dma_core import DMACore
from dma_api import DMAApi
import ue_memory
from ue_types import FNameEntryArray_UE424, TUObjectArray
from ue_reflection import ReflectionDumper
import time
import sys

# 全局上下文
class UEContext:
    def __init__(self):
        self.GNames = 0
        self.GObjects = 0
        self.NameStore = None # 4.24 使用 GNames 数组
        self.ObjArray = None

ue_ctx = UEContext()

def print_banner():
    print(r"""
  __  __ ______  _  _  ____  _  _    
 / / / /|  ____|| || ||___ \| || |   
/ /_/ / | |__   | || |_ __) | || |_  
\__, |  |  __|  |__   _|__ <|__   _| 
  / /   | |____    | | ___) |  | |   
 /_/    |______|   |_||____/   |_|   
    DMA UE4.24 Reflection Dumper     
    """)

def print_detailed_help():
    print("\n" + "="*60)
    print("COMMAND HELP MENU".center(60))
    print("="*60)
    
    cmds = [
        ("attach <PID>", 
         "绑定到目标游戏进程。",
         "示例: attach 10564"),
         
        ("init <GNames> <GObjects>", 
         "初始化 UE 引擎基址 (Hex 格式)。\n需先通过 Cheat Engine 或 Scan 获取地址。",
         "示例: init 0x7FF62A000000 0x7FF62A450000"),
         
        ("dump <ClassName>", 
         "搜索指定类名并打印其所有成员变量偏移。\n支持模糊搜索 (大小写敏感)。",
         "示例: dump TslCharacter\n示例: dump PlayerController"),
         
        ("cr3 <PID>", 
         "获取指定进程的 DTB (Directory Table Base)。\n用于调试物理内存读写是否正常。",
         "示例: cr3 10564"),
         
        ("scan <Mod> <Sig>", 
         "在指定模块中扫描特征码 (Signature Scanning)。\n支持 ?? 通配符。",
         "示例: scan TslGame.exe 48 8B 05 ? ? ? ?"),
         
        ("exit", 
         "断开连接并退出程序。", 
         "")
    ]
    
    for cmd, desc, ex in cmds:
        print(f"\n[ {cmd} ]")
        for line in desc.split('\n'):
            print(f"    {line}")
        if ex:
            print(f"    -> {ex}")
    print("\n" + "="*60 + "\n")

def main():
    print_banner()
    core = DMACore()
    api = DMAApi(core)
    
    print("[*] Waiting for DMA Driver...")
    while not core.driver_online: time.sleep(0.1)
    print("[+] Driver Connected. Type 'help' for commands.")

    while True:
        try:
            line = input(">> ").strip()
            if not line: continue
            parts = line.split()
            cmd = parts[0].lower()

            if cmd == "help":
                print_detailed_help()

            elif cmd == "attach":
                if len(parts) < 2:
                    print("[-] Usage: attach <PID>")
                    continue
                pid = int(parts[1])
                ue_memory.mem = ue_memory.UEMemory(api, pid)
                print(f"[+] Attached to PID {pid}")

            elif cmd == "init":
                if len(parts) < 3:
                    print("[-] Usage: init <GNamesHex> <GObjectsHex>")
                    continue
                try:
                    ue_ctx.GNames = int(parts[1], 16)
                    ue_ctx.GObjects = int(parts[2], 16)
                    
                    # 4.24 初始化逻辑
                    ue_ctx.NameStore = FNameEntryArray_UE424(ue_ctx.GNames)
                    ue_ctx.ObjArray = TUObjectArray(ue_ctx.GObjects)
                    
                    print(f"[+] UE4.24 Engine Initialized")
                    print(f"    GNames  : 0x{ue_ctx.GNames:X}")
                    print(f"    GObjects: 0x{ue_ctx.GObjects:X}")
                    print(f"    Count   : {ue_ctx.ObjArray.num_elements}")
                except Exception as e:
                    print(f"[-] Init Failed: {e}")

            elif cmd == "dump":
                if not ue_ctx.ObjArray:
                    print("[-] Please run 'init' first.")
                    continue
                if len(parts) < 2:
                    print("[-] Usage: dump <ClassName>")
                    continue
                
                target = parts[1]
                print(f"[*] Searching for '{target}'...")
                
                found = False
                # 限制搜索数量，防止过慢
                limit = min(ue_ctx.ObjArray.num_elements, 300000)
                
                for i in range(limit):
                    obj = ue_ctx.ObjArray.get_object_ptr(i)
                    if not obj: continue
                    
                    # UE4.24 UObject Name Index 在 0x18
                    name_idx = ue_memory.mem.read_u32(obj + 0x18)
                    name = ue_ctx.NameStore.get_name(name_idx)
                    
                    if name == target:
                        print(f"[+] Found Class at 0x{obj:X}")
                        dumper = ReflectionDumper(ue_ctx.NameStore)
                        dumper.dump_struct(obj)
                        found = True
                        break
                
                if not found:
                    print(f"[-] Class '{target}' not found in first {limit} objects.")

            elif cmd == "cr3":
                if len(parts) < 2: 
                    print("Usage: cr3 <pid>")
                    continue
                u, k = api.get_cr3(int(parts[1]))
                print(f"UserDTB: {hex(u) if u else 'N/A'}")

            elif cmd == "exit":
                print("[*] Exiting...")
                break
                
            else:
                print("[-] Unknown command. Type 'help'.")
                
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Error] {e}")

if __name__ == "__main__":
    main()
