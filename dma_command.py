import datetime
import struct
import time

import ue_memory
from sdk_helper import SDKLoader
from ue_generator import SDKGenerator
from ue_scanner import UEScanner
from ue_types import FNameCache, FNameEntryArray_UE424, TUObjectArray


def log(msg, level="INFO"):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = "[*]"
    if level == "SUCCESS":
        prefix = "[+]"
    elif level == "ERROR":
        prefix = "[-]"
    elif level == "WARN":
        prefix = "[!]"
    print(f"{prefix} [{timestamp}] {msg}")


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
    print("\n" + "=" * 60)
    print("COMMAND HELP MENU".center(60))
    print("=" * 60)
    cmds = [
        ("attach <PID>", "Bind process and cache CR3."),
        ("cr3 <PID>", "Query process CR3 tuple."),
        ("modules <PID>", "Enumerate user modules by CR3 path."),
        ("vt_getp <PID>", "VT CMD11: get VM_PROCESS_INFO (Cr3/Eprocess/Peb)."),
        ("vt_memc <PID> <SrcVA> <Size> [OutFile]", "VT CMD12: copy process memory, optional file output."),
        ("vt_hoon <PID> <TargetVA> <ColdVA>", "VT CMD6: install callback interceptor (HookN)."),
        ("vt_uoon <PID> <TargetVA>", "VT CMD7: uninstall callback interceptor (HookN)."),
        ("vt_mhok <PID> <TargetColdVA> <ShellcodeHex>", "VT CMD8: cold code EPT patch."),
        ("vt_khok <PID> <TargetColdVA> <ShellcodeHex>", "VT CMD9: cold code physical patch."),
        ("vt_epat <PID> <TargetVA> <PatchHex>", "VT CMD10: EPT patch."),
        ("auto_init", "Auto scan signatures."),
        ("cache_gnames", "Build local FName cache."),
        ("dump_sdk <ClassName>", "Generate C++ SDK header for class."),
        ("pe_info", "Print PE section table from base."),
        ("dump_mem <Addr> <Size> <File>", "Dump raw memory range to file."),
        ("fast_init", "Use SDK RVA for fast init."),
        ("watch <ClassName> <MemberName> <ObjIndex>", "Live watch one member value."),
        ("watch2file <continuous/isolated> <Start> <End/none> <FPS> <Duration> <File>", "Record memory changes to file."),
        ("exit", "Exit program."),
    ]
    for cmd, desc in cmds:
        print(f"\n[ {cmd} ]\n    {desc}")
    print("\n" + "=" * 60 + "\n")


class UEContext:
    def __init__(self):
        self.GNames = 0
        self.GObjects = 0
        self.NameStore = None
        self.NameCache = None
        self.ObjArray = None
        self.g_base_addr = 0


class CommandHandler:
    def __init__(self, api):
        self.api = api
        self.ctx = UEContext()
        try:
            self.sdk = SDKLoader()
            log("SDK JSONs loaded successfully.", "SUCCESS")
        except Exception as e:
            self.sdk = None
            log(f"Failed to load SDK JSONs: {e}", "WARN")

    def handle_attach(self, args):
        if not args:
            log("Usage: attach <PID>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            u, k, base = self.api.get_cr3(pid)
            if self.api.cached_dtb:
                ue_memory.mem = ue_memory.UEMemory(self.api, pid)
                self.ctx.g_base_addr = base
                log(f"Attached! UDTB: {hex(u)}, KDTB: {hex(k)}, Base: {hex(base)}", "SUCCESS")
            else:
                log("Failed to get a valid DTB.", "ERROR")
        except ValueError:
            log("PID must be a number.", "ERROR")

    def handle_pe_info(self):
        if self.ctx.g_base_addr == 0:
            log("Base address is 0. Run 'attach' first.", "ERROR")
            return
        log(f"Parsing PE headers at 0x{self.ctx.g_base_addr:X}...")
        dos = self.api.read_mem(self.ctx.g_base_addr, 0x40)
        if not dos or dos[0:2] != b"MZ":
            log("Invalid DOS signature", "ERROR")
            return
        e_lfanew = struct.unpack("<I", dos[0x3C:0x40])[0]
        nt = self.api.read_mem(self.ctx.g_base_addr + e_lfanew, 0x108)
        if not nt or nt[0:4] != b"PE\0\0":
            log("Invalid PE signature", "ERROR")
            return
        num_sections = struct.unpack("<H", nt[6:8])[0]
        size_opt = struct.unpack("<H", nt[20:22])[0]
        sec_table_base = self.ctx.g_base_addr + e_lfanew + 4 + 20 + size_opt
        sec_data = self.api.read_mem(sec_table_base, num_sections * 40)
        print(f"\n{'Idx':<4} {'Name':<10} {'RVA':<10} {'VSize':<10}")
        for i in range(num_sections):
            off = i * 40
            name = sec_data[off:off + 8].rstrip(b"\x00").decode("utf-8", errors="ignore")
            v_addr = struct.unpack("<I", sec_data[off + 12:off + 16])[0]
            v_size = struct.unpack("<I", sec_data[off + 8:off + 12])[0]
            print(f"{i:<4} {name:<10} {v_addr:<10X} {v_size:<10X}")

    def handle_auto_init(self):
        if self.api.cached_dtb == 0:
            log("Please run 'attach <PID>' first!", "ERROR")
            return
        scanner = UEScanner(self.api, self.ctx.g_base_addr)
        gnames = scanner.find_gnames()
        gobjects = scanner.find_gobjects()
        if gnames and gobjects:
            self.ctx.GNames = gnames
            self.ctx.GObjects = gobjects
            self.ctx.NameStore = FNameEntryArray_UE424(gnames)
            self.ctx.ObjArray = TUObjectArray(gobjects)
            log(f"Engine initialized: GN={hex(gnames)}, GO={hex(gobjects)}", "SUCCESS")
        else:
            log("Auto-init failed.", "ERROR")

    def handle_cache_gnames(self):
        if not self.ctx.GNames:
            log("Run 'auto_init' first.", "ERROR")
            return
        cache = FNameCache(self.ctx.GNames)
        cache.build_cache()
        if cache.is_cached:
            self.ctx.NameCache = cache
            log("GNames cached successfully!", "SUCCESS")

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
            if not obj:
                continue
            if name_provider.get_name(ue_memory.mem.read_u32(obj + 0x18)) == target:
                found_addr = obj
                break
        if found_addr:
            gen = SDKGenerator(name_provider, self.ctx.ObjArray)
            gen.generate_class_sdk(found_addr)
        else:
            log("Class not found.", "ERROR")

    def handle_modules(self, args):
        if not args:
            log("Usage: modules <PID>", "WARN")
            return
        try:
            target_pid = int(args[0], 0)
            self.api.enum_user_modules(target_pid)
            log("Command sent. Check [LOG] for module stream.", "SUCCESS")
        except ValueError:
            log("PID must be a number.", "ERROR")

    def handle_vt_getp(self, args):
        if not args:
            log("Usage: vt_getp <PID>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            info = self.api.vm_get_process_info(pid)
            if not info:
                log("vt_getp failed (no response).", "ERROR")
                return
            cr3, eprocess, peb = info
            log(f"VT_GETP => CR3={hex(cr3)} EPROCESS={hex(eprocess)} PEB={hex(peb)}", "SUCCESS")
        except ValueError:
            log("PID must be a number.", "ERROR")

    def handle_vt_memc(self, args):
        if len(args) < 3:
            log("Usage: vt_memc <PID> <SrcVA> <Size> [OutFile]", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            src_va = int(args[1], 0)
            size = int(args[2], 0)
            data = self.api.vm_memory_copy(pid, src_va, size)
            if not data or len(data) != size:
                got = len(data) if data else 0
                log(f"vt_memc failed: got {got}/{size} bytes.", "ERROR")
                return
            if len(args) >= 4:
                out_file = args[3]
                with open(out_file, "wb") as f:
                    f.write(data)
                log(f"vt_memc ok: wrote {size} bytes to {out_file}", "SUCCESS")
            else:
                log(f"vt_memc ok: {size} bytes, head={data[:32].hex()}", "SUCCESS")
        except ValueError:
            log("PID/SrcVA/Size must be numbers.", "ERROR")

    def handle_vt_hoon(self, args):
        if len(args) < 3:
            log("Usage: vt_hoon <PID> <TargetVA> <ColdVA>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            target_va = int(args[1], 0)
            cold_va = int(args[2], 0)
            result = self.api.install_callback_interceptor(pid, target_va, cold_va)
            if result is None:
                log("vt_hoon failed (no response).", "ERROR")
                return
            log(f"vt_hoon vmResult={hex(result)}", "SUCCESS")
        except ValueError:
            log("PID/TargetVA/ColdVA must be numbers.", "ERROR")

    def handle_vt_uoon(self, args):
        if len(args) < 2:
            log("Usage: vt_uoon <PID> <TargetVA>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            target_va = int(args[1], 0)
            result = self.api.uninstall_callback_interceptor(pid, target_va)
            if result is None:
                log("vt_uoon failed (no response).", "ERROR")
                return
            log(f"vt_uoon vmResult={hex(result)}", "SUCCESS")
        except ValueError:
            log("PID/TargetVA must be numbers.", "ERROR")

    def handle_vt_mhok(self, args):
        if len(args) < 3:
            log("Usage: vt_mhok <PID> <TargetColdVA> <ShellcodeHex>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            target_va = int(args[1], 0)
            shellcode = bytes.fromhex(args[2].replace(" ", ""))
            result = self.api.cold_code_adapt_memory_page(pid, target_va, shellcode)
            if result is None:
                log("vt_mhok failed (no response).", "ERROR")
                return
            log(f"vt_mhok vmResult={hex(result)}", "SUCCESS")
        except ValueError:
            log("Invalid PID/TargetColdVA/ShellcodeHex.", "ERROR")

    def handle_vt_khok(self, args):
        if len(args) < 3:
            log("Usage: vt_khok <PID> <TargetColdVA> <ShellcodeHex>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            target_va = int(args[1], 0)
            shellcode = bytes.fromhex(args[2].replace(" ", ""))
            result = self.api.cold_code_adapt_physical(pid, target_va, shellcode)
            if result is None:
                log("vt_khok failed (no response).", "ERROR")
                return
            log(f"vt_khok vmResult={hex(result)}", "SUCCESS")
        except ValueError:
            log("Invalid PID/TargetColdVA/ShellcodeHex.", "ERROR")

    def handle_vt_epat(self, args):
        if len(args) < 3:
            log("Usage: vt_epat <PID> <TargetVA> <PatchHex>", "ERROR")
            return
        try:
            pid = int(args[0], 0)
            target_va = int(args[1], 0)
            patch = bytes.fromhex(args[2].replace(" ", ""))
            result = self.api.memory_page_adapt(pid, target_va, patch)
            if result is None:
                log("vt_epat failed (no response).", "ERROR")
                return
            log(f"vt_epat vmResult={hex(result)}", "SUCCESS")
        except ValueError:
            log("Invalid PID/TargetVA/PatchHex.", "ERROR")

    def handle_dump_mem(self, args):
        if len(args) < 3:
            log("Usage: dump_mem <HexAddr> <HexSize> <Filename>", "ERROR")
            return
        try:
            target_addr = int(args[0], 16)
            target_size = int(args[1], 16)
            filename = args[2]

            log(f"Dumping {target_size/1024:.2f} KB from 0x{target_addr:X} to '{filename}'...")
            start_time = time.time()
            data = self.api.read_mem(target_addr, target_size)

            if data and len(data) == target_size:
                with open(filename, "wb") as f:
                    f.write(data)
                duration = time.time() - start_time
                log(f"Dump saved successfully! Speed: {target_size/1024/1024/duration:.2f} MB/s", "SUCCESS")
            else:
                log(f"Dump failed. Received {len(data) if data else 0}/{target_size} bytes.", "ERROR")
        except Exception as e:
            log(f"Dump error: {e}", "ERROR")

    def handle_fast_init(self):
        if self.ctx.g_base_addr == 0:
            log("Please run 'attach <PID>' first!", "ERROR")
            return
        if not self.sdk:
            log("SDK is not loaded.", "ERROR")
            return

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

        log(f"Fast init success! GNames: {hex(real_gn)}, GObjects: {hex(real_go)}", "SUCCESS")
        log("Auto-dumping 'UObject' to verify path...")
        self.handle_dump_sdk(["UObject"])
        self.handle_dump_sdk(["UNames"])

    def handle_watch(self, args):
        if len(args) < 3:
            log("Usage: watch <ClassName> <MemberName> <ObjIndex>", "ERROR")
            return

        cls_name, mem_name, idx = args[0], args[1], int(args[2])
        offset = self.sdk.get_member_offset(cls_name, mem_name)
        if offset is None:
            log(f"Member '{mem_name}' not found in class '{cls_name}'", "ERROR")
            return

        obj_ptr = self.ctx.ObjArray.get_object_ptr(idx)
        if not obj_ptr:
            log(f"Object at index {idx} is null.", "ERROR")
            return

        log(f"Monitoring {cls_name}->{mem_name} at 0x{obj_ptr + offset:X}...")

        try:
            while True:
                data = ue_memory.mem.read_bytes(obj_ptr + offset, 4)
                if data:
                    val = struct.unpack("<f", data)[0]
                    print(f"\r[LIVE] {mem_name}: {val:.2f}    ", end="")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n")
            log("Watch stopped.")

    def handle_watch2file(self, args):
        if len(args) < 6:
            log("Usage: watch2file <continuous/isolated> <addr1> <addr2/none> <fps> <duration_s> <filename>", "ERROR")
            return

        try:
            mode = args[0].lower()
            addr1 = int(args[1], 16)
            addr2_str = args[2].lower()
            addr2 = int(addr2_str, 16) if addr2_str not in ["none", "empty", "0"] else None

            fps = int(args[3].lower().replace("fps", ""))
            duration = int(args[4].lower().replace("s", ""))
            filename = args[5]

            interval = 1.0 / fps
            total_frames = duration * fps

            log(f"Starting {mode} watch for {duration}s at {fps}fps...")
            log(f"Target Addr: {hex(addr1)} | End Addr: {hex(addr2) if addr2 else 'N/A'}")

            frames = []
            for i in range(total_frames):
                frame_start = time.time()

                if mode == "continuous":
                    if addr2 is None:
                        log("Continuous mode requires an end address.", "ERROR")
                        return
                    size = addr2 - addr1
                    data = self.api.read_mem(addr1, size)
                else:
                    d1 = self.api.read_mem(addr1, 16)
                    d2 = self.api.read_mem(addr2, 16) if addr2 else b""
                    data = (d1 if d1 else b"\x00" * 16) + (d2 if d2 else b"")

                if data:
                    frames.append(data)

                elapsed = time.time() - frame_start
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

                if (i + 1) % fps == 0:
                    print(f"\r[*] Progress: {(i + 1) // fps}/{duration}s (Frames: {len(frames)})", end="")

            print("\n")
            with open(filename, "wb") as f:
                for f_data in frames:
                    f.write(f_data)

            log(f"Successfully recorded {len(frames)} frames to '{filename}'", "SUCCESS")
        except Exception as e:
            log(f"Watch error: {e}", "ERROR")
