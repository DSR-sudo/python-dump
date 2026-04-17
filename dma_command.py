import datetime
import os
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
    print("命令帮助菜单".center(60))
    print("=" * 60)
    cmds = [
        ("attach <PID>", "绑定目标进程并缓存 CR3。"),
        ("cr3 <PID>", "查询进程 CR3（用户/内核）与基址。"),
        ("modules <PID>", "按 CR3 路径枚举用户模块。"),
        ("start_data_threads", "发送控制命令开启 GhostCore/RWbase 流式回传。"),
        ("stop_data_threads", "发送控制命令关闭 GhostCore/RWbase 流式回传。"),
        ("stream_stats", "查看接收线程维护的 RWbase/RWVG 流式统计。"),
        ("stream_log [IntervalSec]", "按固定间隔流式打印 RWbase/RWVG 统计；数据本身由后台持续接收，Ctrl+C 停止。"),
        ("rwbase_stream <on/off/stats/watch> [IntervalSec]", "RWbase 流式回传控制命令；on/off 开关流式回传，stats/watch 查看流式统计。"),
        ("rwbase_data <on/off/stats/watch> [IntervalSec]", "兼容别名，等价 rwbase_stream。"),
        ("auto_init", "自动扫描并初始化关键签名。"),
        ("cache_gnames", "构建本地 FName 缓存。"),
        ("dump_sdk <ClassName>", "为指定类生成 C++ SDK 头文件。"),
        ("pe_info", "打印当前基址对应的 PE 节区信息。"),
        ("dump_mem <Addr> <Size> <File>", "将内存范围导出到文件。"),
        ("retry_bad_pages <DumpFile> [BadPagesFile]", "对 bad_pages 列表进行二次读取回填。"),
        ("fast_init", "使用 SDK RVA 快速初始化。"),
        ("watch <ClassName> <MemberName> <ObjIndex>", "实时监控单个成员值。"),
        ("watch2file <continuous/isolated> <Start> <End/none> <FPS> <Duration> <File>", "按指定模式录制内存变化到文件。"),
        ("exit", "退出程序。"),
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

    def _format_stream_stats(self):
        stats = self.api.core.get_stream_stats()
        return (
            "RWVG stats: "
            f"utils={stats.get('utils_frames', 0)}, "
            f"player={stats.get('player_frames', 0)}, "
            f"item={stats.get('item_frames', 0)}, "
            f"typed_bytes={stats.get('typed_bytes', 0)}, "
            f"host_agg={stats.get('host_aggregate_frames', 0)}, "
            f"host_raw_bytes={stats.get('host_aggregate_raw_bytes', 0)}, "
            f"command_bytes={stats.get('command_bytes', 0)}, "
            f"dropped={stats.get('dropped_data_packets', 0)}, "
            f"zombie_ack={stats.get('zombie_ack_packets', 0)}, "
            f"zombie_non_ok={stats.get('zombie_ack_non_ok', 0)}, "
            f"zombie_last_ack={stats.get('zombie_last_ack')}"
        )

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

    def handle_start_data_threads(self):
        ack = self.api.start_data_threads()
        if ack is None:
            log("start_data_threads failed (no response).", "ERROR")
            return
        if ack == 1:
            log("RWbase streaming enabled (ACK=1).", "SUCCESS")
        else:
            log(f"RWbase streaming enable returned ACK={ack}.", "WARN")

    def handle_stop_data_threads(self):
        ack = self.api.stop_data_threads()
        if ack is None:
            log("stop_data_threads failed (no response).", "ERROR")
            return
        if ack == 1:
            log("RWbase streaming disabled (ACK=1).", "SUCCESS")
        else:
            log(f"RWbase streaming disable returned ACK={ack}.", "WARN")

    def handle_stream_stats(self):
        log(self._format_stream_stats())

    def handle_stream_log(self, args):
        interval = 1.0
        if args:
            try:
                interval = float(args[0])
            except ValueError:
                log("Usage: stream_log [IntervalSec]", "ERROR")
                return

        if interval <= 0:
            log("IntervalSec must be > 0.", "ERROR")
            return

        log(
            "Watching RWbase/RWVG streaming stats. "
            "This is a passive stream view, not ping-pong polling; incoming [LOG] packets are printed automatically. Ctrl+C stops this watcher."
        )
        try:
            while True:
                log(self._format_stream_stats())
                time.sleep(interval)
        except KeyboardInterrupt:
            log("stream_log stopped.")

    def handle_rwbase_stream(self, args):
        if not args:
            log("Usage: rwbase_stream <on/off/stats/watch> [IntervalSec]", "ERROR")
            return

        action = args[0].lower()
        rest = args[1:]

        if action == "on":
            self.handle_start_data_threads()
        elif action == "off":
            self.handle_stop_data_threads()
        elif action == "stats":
            self.handle_stream_stats()
        elif action == "watch":
            self.handle_stream_log(rest)
        else:
            log("Usage: rwbase_stream <on/off/stats/watch> [IntervalSec]", "ERROR")

    def handle_rwbase_data(self, args):
        self.handle_rwbase_stream(args)

    def handle_dump_mem(self, args):
        if len(args) < 3:
            log("Usage: dump_mem <HexAddr> <HexSize> <Filename>", "ERROR")
            return
        try:
            target_addr = int(args[0], 16)
            target_size = int(args[1], 16)
            filename = args[2]

            if target_size <= 0:
                log("HexSize must be > 0.", "ERROR")
                return

            log(f"Dumping {target_size/1024:.2f} KB from 0x{target_addr:X} to '{filename}'...")
            if self.api.cached_dtb == 0:
                log("Cached DTB is 0. Trying to refresh from cached PID...", "WARN")

            # Reliable page-by-page mode:
            # - Read one page at a time.
            # - On page failure, write zero placeholder and continue.
            # - Record failed pages to bad_pages.txt for later retry.
            page_size = 0x1000
            page_timeout = 3.0
            max_retries = 3
            progress_step = 32 * 1024 * 1024

            start_time = time.time()
            total_pages = (target_size + page_size - 1) // page_size
            success_pages = 0
            bad_pages = []
            written = 0
            next_progress_mark = progress_step
            zero_page = b"\x00" * page_size
            bad_pages_file = os.path.join(os.path.dirname(os.path.abspath(filename)), "bad_pages.txt")

            with open(filename, "wb") as f:
                for page_index in range(total_pages):
                    remaining = target_size - written
                    current_size = page_size if remaining >= page_size else remaining
                    current_addr = target_addr + (page_index * page_size)
                    page_ok = False
                    last_recv = 0
                    last_data_len = 0

                    for attempt in range(1, max_retries + 1):
                        data = self.api.read_chunk(current_addr, current_size, timeout=page_timeout)
                        if data and len(data) == current_size:
                            f.write(data)
                            success_pages += 1
                            page_ok = True
                            break

                        last_recv = int(getattr(self.api.core, "recvd_bytes", 0))
                        last_data_len = len(data) if data else 0
                        if attempt < max_retries:
                            time.sleep(0.05 * attempt)

                    if not page_ok:
                        if current_size == page_size:
                            f.write(zero_page)
                        else:
                            f.write(b"\x00" * current_size)
                        bad_pages.append((current_addr, current_size, last_data_len, last_recv))

                    written += current_size

                    if written >= next_progress_mark or written == target_size:
                        pct = (written / target_size) * 100 if target_size else 100.0
                        log(
                            f"Dump progress: {written}/{target_size} bytes ({pct:.1f}%), "
                            f"pages: {page_index + 1}/{total_pages}, bad_pages={len(bad_pages)}"
                        )
                        next_progress_mark += progress_step

            with open(bad_pages_file, "w", encoding="utf-8") as bf:
                bf.write("# bad pages generated by dump_mem reliable mode\n")
                bf.write(f"# start=0x{target_addr:X}, size=0x{target_size:X}, page_size=0x{page_size:X}\n")
                bf.write("# columns: va,size,last_data_len,last_recv_bytes\n")
                for va, sz, last_len, recv_now in bad_pages:
                    bf.write(f"0x{va:X},0x{sz:X},{last_len},{recv_now}\n")

            failed_bytes = sum(item[1] for item in bad_pages)
            success_bytes = target_size - failed_bytes
            success_ratio = (success_pages / total_pages) * 100 if total_pages else 100.0
            byte_ratio = (success_bytes / target_size) * 100 if target_size else 100.0
            duration = time.time() - start_time
            speed = target_size / 1024 / 1024 / max(duration, 1e-6)

            log(
                f"Dump finished. Speed: {speed:.2f} MB/s | "
                f"page_success={success_pages}/{total_pages} ({success_ratio:.2f}%) | "
                f"byte_success={success_bytes}/{target_size} ({byte_ratio:.2f}%)"
            )
            if bad_pages:
                log(f"bad_pages count={len(bad_pages)}; details saved to '{bad_pages_file}'", "WARN")
            else:
                log("All pages dumped successfully (no bad pages).", "SUCCESS")
        except Exception as e:
            log(f"Dump error: {e}", "ERROR")

    def handle_retry_bad_pages(self, args):
        if len(args) < 1:
            log("Usage: retry_bad_pages <DumpFile> [BadPagesFile]", "ERROR")
            return
        try:
            dump_file = args[0]
            bad_pages_file = args[1] if len(args) >= 2 else os.path.join(
                os.path.dirname(os.path.abspath(dump_file)),
                "bad_pages.txt",
            )

            if not os.path.exists(dump_file):
                log(f"Dump file not found: {dump_file}", "ERROR")
                return
            if not os.path.exists(bad_pages_file):
                log(f"Bad pages file not found: {bad_pages_file}", "ERROR")
                return

            start_addr = None
            total_size = None
            page_size = 0x1000
            bad_entries = []

            with open(bad_pages_file, "r", encoding="utf-8", errors="ignore") as bf:
                for line in bf:
                    text = line.strip()
                    if not text:
                        continue
                    if text.startswith("#"):
                        # expected header:
                        # # start=0x..., size=0x..., page_size=0x...
                        if "start=" in text and "size=" in text:
                            parts = [p.strip() for p in text.split(",")]
                            for p in parts:
                                if "start=" in p:
                                    start_addr = int(p.split("start=")[1], 16)
                                elif "size=" in p:
                                    total_size = int(p.split("size=")[1], 16)
                                elif "page_size=" in p:
                                    page_size = int(p.split("page_size=")[1], 16)
                        continue

                    cols = [x.strip() for x in text.split(",")]
                    if len(cols) < 2:
                        continue
                    va = int(cols[0], 16)
                    sz = int(cols[1], 16)
                    bad_entries.append((va, sz))

            if not bad_entries:
                log("No bad pages found in file.", "SUCCESS")
                return

            if start_addr is None:
                start_addr = min(va for va, _ in bad_entries)
                log(
                    f"Header start not found in bad pages file, using min VA as base: 0x{start_addr:X}",
                    "WARN",
                )

            file_size = os.path.getsize(dump_file)
            if total_size is None:
                total_size = file_size

            log(
                f"Retrying {len(bad_entries)} bad pages from '{bad_pages_file}' "
                f"into '{dump_file}' (base=0x{start_addr:X}, page=0x{page_size:X})..."
            )

            page_timeout = 3.0
            max_retries = 3
            recovered = 0
            still_bad = []

            with open(dump_file, "r+b") as f:
                for idx, (va, sz) in enumerate(bad_entries, 1):
                    if sz <= 0:
                        still_bad.append((va, sz, "invalid_size"))
                        continue

                    off = va - start_addr
                    if off < 0 or (off + sz) > file_size:
                        still_bad.append((va, sz, "offset_oob"))
                        continue

                    ok = False
                    for attempt in range(1, max_retries + 1):
                        data = self.api.read_chunk(va, sz, timeout=page_timeout)
                        if data and len(data) == sz:
                            f.seek(off)
                            f.write(data)
                            recovered += 1
                            ok = True
                            break
                        if attempt < max_retries:
                            time.sleep(0.05 * attempt)

                    if not ok:
                        still_bad.append((va, sz, "read_failed"))

                    if idx % 256 == 0 or idx == len(bad_entries):
                        pct = (idx / len(bad_entries)) * 100.0
                        log(
                            f"Retry progress: {idx}/{len(bad_entries)} ({pct:.1f}%), "
                            f"recovered={recovered}, remaining={len(still_bad)}"
                        )

            remaining_file = os.path.join(
                os.path.dirname(os.path.abspath(bad_pages_file)),
                "bad_pages_remaining.txt",
            )
            with open(remaining_file, "w", encoding="utf-8") as rf:
                rf.write("# remaining bad pages after retry_bad_pages\n")
                rf.write(
                    f"# source={bad_pages_file}\n"
                    f"# recovered={recovered}, remaining={len(still_bad)}, total={len(bad_entries)}\n"
                )
                rf.write("# columns: va,size,reason\n")
                for va, sz, reason in still_bad:
                    rf.write(f"0x{va:X},0x{sz:X},{reason}\n")

            ratio = (recovered / len(bad_entries)) * 100.0
            log(
                f"Retry finished. recovered={recovered}/{len(bad_entries)} ({ratio:.2f}%), "
                f"remaining={len(still_bad)}",
                "SUCCESS" if not still_bad else "WARN",
            )
            log(f"Remaining bad pages saved to '{remaining_file}'")

        except Exception as e:
            log(f"retry_bad_pages error: {e}", "ERROR")

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

