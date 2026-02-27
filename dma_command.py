# dma_command.py
import time
import sys
import struct
import datetime
import ue_memory
from sdk_helper import SDKLoader
from ue_types import FNameCache, FNameEntryArray_UE424, TUObjectArray
from ue_reflection import ReflectionDumper
from ue_generator import SDKGenerator
from ue_scanner import UEScanner

def log(msg, level="INFO"):
    """带时间戳的日志输出"""
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = "[*]"
    if level == "SUCCESS": prefix = "[+]"
    elif level == "ERROR": prefix = "[-]"
    elif level == "WARN":  prefix = "[!]"
    print(f"{prefix} [{timestamp}] {msg}")

def print_banner():
    """打印欢迎信息"""
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
    """打印详细帮助菜单"""
    print("\n" + "="*60)
    print("COMMAND HELP MENU".center(60))
    print("="*60)
    cmds = [
        ("attach <PID>", "步骤1: 绑定到目标游戏进程 (获取 CR3)。"),
        ("auto_init",    "步骤2: 自动扫描特征码获取引擎基址。"),
        ("cache_gnames", "步骤3: [推荐] 缓存所有字符串到本地内存。"),
        ("dump_sdk <ClassName>", "步骤4: 生成完整的 C++ SDK 头文件。"),
        ("dump <ClassName>",     "调试: 快速查看类成员偏移。"),
        ("init <GN> <GO>",       "调试: 手动初始化基址 (Hex)。"),
        ("cr3 <PID>",            "调试: 测试 DTB 获取。"),
        ("pe_info",              "调试: 手动获取 PE 中的 text 位置。"),
        ("dump_mem <Addr> <Size> <File>", "调试: 手动 Dump 特定内存范围。"),
        ("modules <PID>",        "新功能: 通过 CR3 隔离枚举用户态模块。"),
        ("dump_mem <PID>",        "调试：直接dump内存为文件"),
        ("exit",                 "退出程序。"),
        ("fast_init",             "读取SDK获取RVA偏移初始化读取"),
        ("watch",                 "监控特定数据"),
        ("watch2file <continuous/isolated> <Start> <End/none> <FPS> <Duration> <File>", 
         "高级监控: 以指定帧率录制内存变动到文件。用于分析碰撞引擎明文。"),
    ]
    for cmd, desc in cmds:
        print(f"\n[ {cmd} ]\n    {desc}")
    print("\n" + "="*60 + "\n")

class UEContext:
    """引擎全局上下文管理"""
    def __init__(self):
        self.GNames = 0
        self.GObjects = 0
        self.NameStore = None
        self.NameCache = None
        self.ObjArray = None
        self.g_base_addr = 0

class CommandHandler:
    """命令处理器类，封装所有业务逻辑"""
    def __init__(self, api):
        self.api = api
        self.ctx = UEContext()
# 启动即加载 SDK
        try:
            self.sdk = SDKLoader()
            log("SDK JSONs loaded successfully.", "SUCCESS")
        except Exception as e:
            log(f"Failed to load SDK JSONs: {e}", "WARN")

    def handle_attach(self, args):
        if not args:
            log("Usage: attach <PID>", "ERROR")
            return
        try:
            pid = int(args[0])
            u, k, base = self.api.get_cr3(pid)
            # 只要存在有效 DTB 即可附加
            if self.api.cached_dtb:
                ue_memory.mem = ue_memory.UEMemory(self.api, pid)
                self.ctx.g_base_addr = base
                log(f"Attached! UDTB: {hex(u)}, KDTB: {hex(k)}, Base: {hex(base)}", "SUCCESS")
            else:
                log("Failed to get a valid DTB. Is the process protected?", "ERROR")
        except ValueError:
            log("PID must be a number.", "ERROR")

    def handle_pe_info(self):
        if self.ctx.g_base_addr == 0:
            log("Base Address is 0! Run 'attach' first.", "ERROR")
            return
        log(f"Parsing PE Headers at 0x{self.ctx.g_base_addr:X}...")
        dos = self.api.read_mem(self.ctx.g_base_addr, 0x40) #
        if not dos or dos[0:2] != b'MZ':
            log("Invalid DOS Signature", "ERROR")
            return
        e_lfanew = struct.unpack("<I", dos[0x3C:0x40])[0]
        nt = self.api.read_mem(self.ctx.g_base_addr + e_lfanew, 0x108)
        if not nt or nt[0:4] != b'PE\0\0':
            log("Invalid PE Signature", "ERROR")
            return
        num_sections = struct.unpack("<H", nt[6:8])[0]
        size_opt = struct.unpack("<H", nt[20:22])[0]
        sec_table_base = self.ctx.g_base_addr + e_lfanew + 4 + 20 + size_opt
        sec_data = self.api.read_mem(sec_table_base, num_sections * 40)
        print(f"\n{'Idx':<4} {'Name':<10} {'RVA':<10} {'VSize':<10}")
        for i in range(num_sections):
            off = i * 40
            name = sec_data[off:off+8].rstrip(b'\x00').decode('utf-8', errors='ignore')
            v_addr = struct.unpack("<I", sec_data[off+12:off+16])[0]
            v_size = struct.unpack("<I", sec_data[off+8:off+12])[0]
            print(f"{i:<4} {name:<10} {v_addr:<10X} {v_size:<10X}")

    def handle_auto_init(self):
        if self.api.cached_dtb == 0:
            log("Please run 'attach <PID>' first!", "ERROR")
            return
        scanner = UEScanner(self.api, self.ctx.g_base_addr) #
        gnames = scanner.find_gnames()
        gobjects = scanner.find_gobjects()
        if gnames and gobjects:
            self.ctx.GNames = gnames
            self.ctx.GObjects = gobjects
            self.ctx.NameStore = FNameEntryArray_UE424(gnames) #
            self.ctx.ObjArray = TUObjectArray(gobjects)
            log(f"Engine Initialized: GN={hex(gnames)}, GO={hex(gobjects)}", "SUCCESS")
        else:
            log("Auto-init failed.", "ERROR")

    def handle_cache_gnames(self):
        if not self.ctx.GNames:
            log("Run 'auto_init' first.", "ERROR")
            return
        cache = FNameCache(self.ctx.GNames) #
        cache.build_cache()
        if cache.is_cached:
            self.ctx.NameCache = cache
            log("GNames Cached Successfully!", "SUCCESS")

    def handle_dump_sdk(self, args):
        if not args or not self.ctx.ObjArray:
            log("Usage: dump_sdk <ClassName> (or engine not init)", "ERROR")
            return
        target = args[0]
        name_provider = self.ctx.NameCache if self.ctx.NameCache else self.ctx.NameStore
        log(f"Searching for class '{target}'...")
        found_addr = 0
        for i in range(min(self.ctx.ObjArray.num_elements, 300000)):
            obj = self.ctx.ObjArray.get_object_ptr(i)
            if not obj: continue
            if name_provider.get_name(ue_memory.mem.read_u32(obj + 0x18)) == target:
                found_addr = obj
                break
        if found_addr:
            gen = SDKGenerator(name_provider, self.ctx.ObjArray) #
            gen.generate_class_sdk(found_addr)
        else:
            log("Class not found.", "ERROR")

    def handle_modules(self, args):
        if not args:
            log("Usage: modules <PID>", "WARN")
            return
        try:
            target_pid = int(args[0])
            self.api.enum_user_modules(target_pid) #
            log("Command sent. Check [LOG] for module stream.", "SUCCESS")
        except ValueError:
            log("PID must be a number.", "ERROR")
            
    def handle_dump_mem(self, args):
        """通用内存 Dump (支持特定基址+范围)"""
        if len(args) < 3:
            log("Usage: dump_mem <HexAddr> <HexSize> <Filename>", "ERROR")
            return
        try:
            target_addr = int(args[0], 16)
            target_size = int(args[1], 16)
            filename = args[2]
            
            log(f"Dumping {target_size/1024:.2f} KB from 0x{target_addr:X} to '{filename}'...")
            
            start_time = time.time()
            # 调用 api 进行读取
            data = self.api.read_mem(target_addr, target_size)
            
            if data and len(data) == target_size:
                with open(filename, "wb") as f:
                    f.write(data)
                duration = time.time() - start_time
                log(f"Dump saved successfully! Speed: {target_size/1024/1024/duration:.2f} MB/s", "SUCCESS")
            else:
                log(f"Dump failed. Received {len(data) if data else 0}/{target_size} bytes.", "ERROR")
                
        except Exception as e:
            log(f"Dump Error: {e}", "ERROR")
            
    def handle_fast_init(self):
        """跳过扫描，直接使用 RVA 初始化引擎"""
        if self.ctx.g_base_addr == 0:
            log("Please run 'attach <PID>' first!", "ERROR")
            return
        
        # 直接从 SDK 计算绝对地址
        gn_va = self.ctx.g_base_addr + self.sdk.get_rva("OFFSET_GNAMES")
        go_va = self.ctx.g_base_addr + self.sdk.get_rva("OFFSET_GOBJECTS")
        real_gn = ue_memory.mem.read_ptr(gn_va)
        real_go = ue_memory.mem.read_ptr(go_va)
        if real_gn == 0 or real_go == 0:
            log(f"Failed to read heap address. VA_GN: {hex(gn_va)}, VA_GO: {hex(go_va)}", "ERROR")
            return

        self.ctx.GNames = real_gn
        self.ctx.GObjects = real_go

        self.ctx.NameStore = FNameEntryArray_UE424(real_gn)
        self.ctx.ObjArray = TUObjectArray(real_go)

        log(f"Fast Init Success! GNames: {hex(real_gn)}, GObjects: {hex(real_go)}", "SUCCESS")

        log("Auto-dumping 'UObject' to verify path...")

        self.handle_dump_sdk(["UObject"])
        self.handle_dump_sdk(["UNames"])
        log(f"Fast Init Success! GNames: {hex(gn_addr)}, GObjects: {hex(go_addr)}", "SUCCESS")

    def handle_watch(self, args):
        """基于偏移的实时监听: watch <ClassName> <MemberName> <ObjIndex>"""
        if len(args) < 3:
            log("Usage: watch <ClassName> <MemberName> <ObjIndex>", "ERROR")
            return
            
        cls_name, mem_name, idx = args[0], args[1], int(args[2])
        
        # 1. 获取 SDK 记录的偏移
        offset = self.sdk.get_member_offset(cls_name, mem_name)
        if offset is None:
            log(f"Member '{mem_name}' not found in class '{cls_name}'", "ERROR")
            return
            
        # 2. 获取对象当前内存地址
        obj_ptr = self.ctx.ObjArray.get_object_ptr(idx)
        if not obj_ptr:
            log(f"Object at index {idx} is null.", "ERROR")
            return
            
        log(f"Monitoring {cls_name}->{mem_name} at 0x{obj_ptr+offset:X}...")
        
        try:
            while True:
                # 3. 高速读取 (利用已优化的 10MB/s 通道)
                data = ue_memory.mem.read_bytes(obj_ptr + offset, 4)
                if data:
                    val = struct.unpack("<f", data)[0] # 假设是 float
                    print(f"\r[LIVE] {mem_name}: {val:.2f}    ", end="")
                time.sleep(0.01) # 100Hz 刷新
        except KeyboardInterrupt:
            print("\n")
            log("Watch stopped.")
    
    def handle_watch2file(self, args):
        """
        watch2file <continuous/isolated> <地址1> <地址2/none> <fps> <持续时间> <文件名>
        用于手动检索和分析物理堆中“远处”的明文数组变动。
        """
        if len(args) < 6:
            log("Usage: watch2file <continuous/isolated> <addr1> <addr2/none> <fps> <duration_s> <filename>", "ERROR")
            return
        
        try:
            mode = args[0].lower()
            addr1 = int(args[1], 16)
            addr2_str = args[2].lower()
            addr2 = int(addr2_str, 16) if addr2_str not in ["none", "empty", "0"] else None
            
            # 解析帧率和时间 (支持 60fps, 60s 等后缀)
            fps = int(args[3].lower().replace("fps", ""))
            duration = int(args[4].lower().replace("s", ""))
            filename = args[5]
            
            interval = 1.0 / fps
            total_frames = duration * fps
            
            log(f"Starting {mode} watch for {duration}s at {fps}fps...", "INFO")
            log(f"Target Addr: {hex(addr1)} | End Addr: {hex(addr2) if addr2 else 'N/A'}", "INFO")

            frames = []
            
            # 循环采集
            for i in range(total_frames):
                frame_start = time.time()
                
                if mode == "continuous":
                    if addr2 is None:
                        log("Continuous mode requires an end address.", "ERROR")
                        return
                    size = addr2 - addr1
                    # 调用 dma_api.py 的 read_mem (使用驱动 CR3 切换读)
                    data = self.api.read_mem(addr1, size)
                else: # isolated (采集两个孤立点，如 Handle 及其内容)
                    d1 = self.api.read_mem(addr1, 16) # 读取 16 字节
                    d2 = self.api.read_mem(addr2, 16) if addr2 else b""
                    data = (d1 if d1 else b"\x00"*16) + (d2 if d2 else b"")
                
                if data:
                    frames.append(data)
                
                # 帧率控制
                elapsed = time.time() - frame_start
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
                # 打印进度
                if (i + 1) % fps == 0:
                    print(f"\r[*] Progress: {(i+1)//fps}/{duration}s (Frames: {len(frames)})", end="")
            
            print("\n")
            # 序列化到文件
            with open(filename, "wb") as f:
                for f_data in frames:
                    f.write(f_data)
                    
            log(f"Successfully recorded {len(frames)} frames to '{filename}'", "SUCCESS")
            
        except Exception as e:
            log(f"Watch error: {e}", "ERROR")
