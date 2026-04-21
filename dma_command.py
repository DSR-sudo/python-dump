import datetime
import json
import os
import struct
import time

import ue_memory
from dma_protocol import CTRL_ACK_CPUEAXH_ONLINE, CTRL_ACK_HANDLED
from sdk_helper import SDKLoader
from ue_generator import SDKGenerator
from ue_scanner import UEScanner
from ue_types import FNameCache, FNameEntryArray_UE424, TUObjectArray
from web_radar import WebRadarService

DEFAULT_DUMP_CHUNK_SIZE = 0x10000  # 64KB
DEFAULT_RETRY_CHUNK_SIZE = 0x1000  # 4KB (robust bad-page recovery)

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PROTECTS = {
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
}

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
        ("stream_log [on/off/ping/stats/watch/all/decode/send] [IntervalSec]", "统一流式入口：on/off/ping 控制；watch/all 同时看两条线；decode 仅看 CPUEAXH-RWBASE 解码链路；send 仅看发送线程人物数据。"),
        ("webradar <start/stop/status> [Port]", "启动/停止/查看网页雷达服务；token 写入 web/pwd.txt。"),
        ("auto_init", "自动扫描并初始化关键签名。"),
        ("cache_gnames", "构建本地 FName 缓存。"),
        ("dump_sdk <ClassName>", "为指定类生成 C++ SDK 头文件。"),
        ("pe_info", "打印当前基址对应的 PE 节区信息。"),
        ("regions <PID> [WaitSec]", "枚举用户态内存区并缓存（含 MEM_PRIVATE）。"),
        ("dump_mem <Addr> <Size> <File> [ChunkSizeHex]", "将内存范围导出到文件。默认块大小 0x10000 (64KB)。"),
        ("dump_private <PID> <OutDir> [ChunkSizeHex] [WaitSec]", "批量导出 MEM_PRIVATE + MEM_COMMIT + 可读区域，并生成 manifest。"),
        ("pack_private <ManifestJson> <OutFile>", "将 dump_private 结果打包为单文件容器（含索引头 + 原始分段数据）。"),
        ("retry_bad_pages <DumpFile> [BadPagesFile] [ChunkSizeHex]", "对 bad_pages 列表进行二次读取回填；默认按 0x1000(4KB) 子块稳健回填。"),
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
        self.web_radar = WebRadarService(self.api.core)
        try:
            self.sdk = SDKLoader()
            log("SDK JSONs loaded successfully.", "SUCCESS")
        except Exception as e:
            self.sdk = None
            log(f"Failed to load SDK JSONs: {e}", "WARN")

    def _is_readable_region(self, protect: int) -> bool:
        base = protect & 0xFF
        if base == PAGE_NOACCESS:
            return False
        if (protect & PAGE_GUARD) != 0:
            return False
        return base in READABLE_PROTECTS

    def _sanitize_filename(self, name: str) -> str:
        invalid = '<>:"/\\|?*'
        out = []
        for ch in (name or ""):
            if ch in invalid or ord(ch) < 32:
                out.append("_")
            else:
                out.append(ch)
        candidate = "".join(out).strip().strip(".")
        return candidate if candidate else "noname"

    def _dump_range_reliable(self, target_addr, target_size, filename, chunk_size):
        if target_size <= 0:
            raise ValueError("target_size must be > 0")
        if chunk_size < 0x1000:
            raise ValueError("chunk_size must be >= 0x1000")

        os.makedirs(os.path.dirname(os.path.abspath(filename)) or ".", exist_ok=True)
        log(f"Dumping {target_size / 1024:.2f} KB from 0x{target_addr:X} to '{filename}'...")

        page_size = chunk_size
        page_timeout = 3.0
        max_retries = 3
        progress_step = 32 * 1024 * 1024
        log(
            f"Reliable dump mode enabled. chunk_size=0x{page_size:X}, retries={max_retries}, timeout={page_timeout:.1f}s"
        )

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

        return {
            "target_addr": target_addr,
            "target_size": target_size,
            "filename": filename,
            "bad_pages_file": bad_pages_file,
            "bad_pages_count": len(bad_pages),
            "success_pages": success_pages,
            "total_pages": total_pages,
            "success_bytes": success_bytes,
            "failed_bytes": failed_bytes,
            "speed_mb_s": speed,
        }

    def _enum_regions_with_wait(self, pid, wait_sec):
        self.api.core.clear_region_snapshot()
        self.api.enum_user_regions(pid)
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            entries, done, count = self.api.core.get_region_snapshot()
            if done:
                return entries, done, count
            time.sleep(0.1)
        entries, done, count = self.api.core.get_region_snapshot()
        return entries, done, count

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

    def _format_coord_brief(self):
        snapshot = self.api.core.get_radar_snapshot() or {}
        local = snapshot.get("local_player") or {}
        local_pos = local.get("position") or {}
        local_neck = local.get("neck_position") or {}
        entities = snapshot.get("entities") or []

        target = entities[0] if entities else None
        if target is None:
            return (
                "coord: "
                f"local=({local_pos.get('x', 0)},{local_pos.get('y', 0)},{local_pos.get('z', 0)}) "
                f"neck=({local_neck.get('x', 0)},{local_neck.get('y', 0)},{local_neck.get('z', 0)}) "
                f"players=0"
            )

        target_pos = target.get("position") or {}
        return (
            "coord: "
            f"local=({local_pos.get('x', 0)},{local_pos.get('y', 0)},{local_pos.get('z', 0)}) "
            f"target=({target_pos.get('x', 0)},{target_pos.get('y', 0)},{target_pos.get('z', 0)}) "
            f"players={len(entities)} "
            f"team={target.get('team_id', 0)} hp={int(float(target.get('health', 0.0) or 0.0))}"
        )

    def _format_decode_brief(self):
        diag = self.api.core.get_rwbase_host_diag()
        runtime = diag.get("emu_runtime_init")
        runtime_fail = diag.get("emu_runtime_exec_fail")
        decode_summary = diag.get("coord_decode_summary")
        decode_groups = diag.get("coord_decode_groups") or []

        parts = []
        if runtime:
            parts.append(f"runtime=0x{runtime.get('status', 0):08X}/ready={runtime.get('ready', 0)}")
        else:
            parts.append("runtime=n/a")

        if runtime_fail:
            parts.append(
                f"last_fail=0x{runtime_fail.get('status', 0):08X}/exit={runtime_fail.get('exit', 0)}/exc={runtime_fail.get('exception', 0)}"
            )

        if decode_summary:
            parts.append(
                f"decode=frame{decode_summary.get('frame', 0)} total={decode_summary.get('total', 0)} drop={decode_summary.get('dropped', 0)}"
            )
            if decode_groups:
                top = decode_groups[0]
                parts.append(
                    f"top={top.get('type', 'n/a')}:{top.get('count', 0)} kind={top.get('kind', 0)} rcode={top.get('rcode', 'n/a')}"
                )
        else:
            parts.append("decode=n/a")

        latest = None
        if hasattr(self.api.core, "get_decode_path_history"):
            history = self.api.core.get_decode_path_history(limit=1) or []
            latest = history[-1] if history else None
        if latest:
            parts.append(f"last={latest.get('category', 'n/a')}")

        return "decode_path: " + " ".join(parts)

    def _format_send_thread_brief(self):
        entries = []
        if hasattr(self.api.core, "get_send_thread_history"):
            entries = self.api.core.get_send_thread_history(limit=3) or []

        if not entries:
            return "send_thread: n/a"

        parts = []
        for item in entries:
            pos = item.get("pos") or {}
            parts.append(
                f"{item.get('entity_id', 'n/a')}"
                f"/t{item.get('team_id', 0)}"
                f"/hp{int(float(item.get('health', 0.0) or 0.0))}"
                f"/d{item.get('distance', 0)}"
                f"/({int(float(pos.get('x', 0.0) or 0.0))},{int(float(pos.get('y', 0.0) or 0.0))},{int(float(pos.get('z', 0.0) or 0.0))})"
            )
        return "send_thread: " + " ; ".join(parts)

    def _format_stream_focus(self):
        return (
            f"{self._format_stream_stats()} | "
            f"{self._format_coord_brief()} | "
            f"{self._format_decode_brief()} | "
            f"{self._format_send_thread_brief()}"
        )

    def _build_stream_lines(self, mode: str):
        selected = (mode or "all").lower()
        lines = []
        if selected in ("all", "watch"):
            lines.append(self._format_stream_stats())
            lines.append(self._format_decode_brief())
            lines.append(self._format_send_thread_brief())
            lines.append(self._format_coord_brief())
            return lines
        if selected == "decode":
            lines.append(self._format_decode_brief())
            return lines
        if selected == "send":
            lines.append(self._format_send_thread_brief())
            lines.append(self._format_coord_brief())
            return lines
        if selected == "stats":
            lines.append(self._format_stream_stats())
            return lines
        lines.append(self._format_stream_focus())
        return lines

    def _print_stream_lines(self, mode: str):
        for line in self._build_stream_lines(mode):
            log(line)

    def _format_rwbase_host_diag(self):
        diag = self.api.core.get_rwbase_host_diag()

        def fmt_hex(value):
            if value is None:
                return "n/a"
            return f"0x{int(value):X}"

        offsets = diag.get("offsets")
        track = diag.get("track_ready")
        chain = diag.get("chain")
        runtime = diag.get("emu_runtime_init")
        runtime_fail = diag.get("emu_runtime_exec_fail")
        bridge = diag.get("emu_bridge_last")
        call = diag.get("emu_call_last")

        parts = []
        if offsets:
            parts.append(
                "offsets:"
                f" uworld={fmt_hex(offsets.get('uworld'))}"
                f" fnames={fmt_hex(offsets.get('fnames'))}"
                f" lpp={fmt_hex(offsets.get('local_player_ptr'))}"
                f" ack=0x{offsets.get('acknowledged_pawn_offset', 0):X}"
                f" mesh=0x{offsets.get('mesh_offset', 0):X}"
                f" bone=0x{offsets.get('bone_array_offset', 0):X}"
                f" team=0x{offsets.get('team_offset', 0):X}"
            )
        else:
            parts.append("offsets: n/a")

        if track:
            parts.append(
                "track:"
                f" pid={fmt_hex(track.get('pid'))}"
                f" ucr3={fmt_hex(track.get('user_cr3'))}"
                f" kcr3={fmt_hex(track.get('kernel_cr3'))}"
                f" base={fmt_hex(track.get('base'))}"
                f" net={track.get('network', 'n/a')}"
            )
        else:
            parts.append("track: n/a")

        if chain:
            parts.append(
                "chain:"
                f" uworld={fmt_hex(chain.get('uworld'))}"
                f" fnames={fmt_hex(chain.get('fnames'))}"
                f" pc={fmt_hex(chain.get('player_controller'))}"
                f" pawn={fmt_hex(chain.get('pawn'))}"
                f" ps={fmt_hex(chain.get('player_state'))}"
                f" team={chain.get('team_id', 'n/a')}"
                f" mesh={fmt_hex(chain.get('mesh'))}"
            )
        else:
            parts.append("chain: n/a")

        if runtime:
            parts.append(
                "runtime:"
                f" init_status=0x{runtime.get('status', 0):08X}"
                f" ready={runtime.get('ready', 0)}"
            )
        else:
            parts.append("runtime: n/a")

        if runtime_fail:
            parts.append(
                "runtime_fail:"
                f" status=0x{runtime_fail.get('status', 0):08X}"
                f" exit={runtime_fail.get('exit', 0)}"
                f" exc={runtime_fail.get('exception', 0)}"
                f" rip={fmt_hex(runtime_fail.get('rip'))}"
            )

        if bridge:
            parts.append(f"bridge: {bridge.get('kind')} {bridge.get('details')}")
        else:
            parts.append("bridge: n/a")

        if call:
            parts.append(f"call: {call.get('kind')} {call.get('details')}")
        else:
            parts.append("call: n/a")

        return " | ".join(parts)

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

    def handle_regions(self, args):
        if not args:
            log("Usage: regions <PID> [WaitSec]", "ERROR")
            return
        try:
            target_pid = int(args[0], 0)
            wait_sec = float(args[1]) if len(args) >= 2 else 6.0
            if wait_sec <= 0:
                wait_sec = 6.0

            entries, done, count = self._enum_regions_with_wait(target_pid, wait_sec)
            log(
                f"Region snapshot: entries={len(entries)}, done={done}, driver_count={count}",
                "SUCCESS" if done else "WARN",
            )
            if entries:
                preview = min(5, len(entries))
                for idx in range(preview):
                    e = entries[idx]
                    log(
                        f"[{idx}] base=0x{e['base']:016X} size=0x{e['size']:X} "
                        f"state=0x{e['state']:X} protect=0x{e['protect']:X} type=0x{e['type']:X}"
                    )
        except ValueError:
            log("PID and WaitSec must be valid numbers.", "ERROR")

    def handle_dump_private(self, args):
        if len(args) < 2:
            log("Usage: dump_private <PID> <OutDir> [ChunkSizeHex] [WaitSec]", "ERROR")
            return
        try:
            target_pid = int(args[0], 0)
            out_dir = args[1]
            chunk_size = int(args[2], 0) if len(args) >= 3 else DEFAULT_DUMP_CHUNK_SIZE
            wait_sec = float(args[3]) if len(args) >= 4 else 6.0
            if chunk_size < 0x1000:
                log("ChunkSizeHex must be >= 0x1000.", "ERROR")
                return
            if wait_sec <= 0:
                wait_sec = 6.0

            user_cr3, kernel_cr3, base_addr = self.api.get_cr3(target_pid)
            if self.api.cached_dtb == 0:
                log("Failed to cache DTB for target pid.", "ERROR")
                return
            log(
                f"Target attached for dump_private. pid={target_pid}, "
                f"UDTB={hex(user_cr3 or 0)}, KDTB={hex(kernel_cr3 or 0)}, Base={hex(base_addr or 0)}"
            )

            entries, done, count = self._enum_regions_with_wait(target_pid, wait_sec)
            if not entries:
                log("No region entries captured.", "ERROR")
                return
            if not done:
                log("Region enumeration timeout; dumping captured partial snapshot.", "WARN")

            candidates = [
                e for e in entries
                if e["state"] == MEM_COMMIT
                and e["type"] == MEM_PRIVATE
                and self._is_readable_region(e["protect"])
                and e["size"] > 0
            ]
            candidates.sort(key=lambda x: x["base"])
            os.makedirs(out_dir, exist_ok=True)

            manifest = {
                "pid": target_pid,
                "udtb": int(user_cr3 or 0),
                "kdtb": int(kernel_cr3 or 0),
                "base_addr": int(base_addr or 0),
                "region_enum_done": bool(done),
                "region_enum_driver_count": int(count),
                "region_enum_captured_count": len(entries),
                "filters": {
                    "state": "MEM_COMMIT(0x1000)",
                    "type": "MEM_PRIVATE(0x20000)",
                    "readable_only": True,
                    "guard_filtered": True,
                },
                "dump_count": 0,
                "regions": [],
            }

            log(f"dump_private candidates={len(candidates)} (from entries={len(entries)})")
            if not candidates:
                manifest_path = os.path.join(out_dir, "private_manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as mf:
                    json.dump(manifest, mf, indent=2, ensure_ascii=False)
                log(f"No private readable committed regions found. Manifest: {manifest_path}", "WARN")
                return

            for idx, e in enumerate(candidates):
                region_name = f"private_{idx:05d}_{e['base']:016X}_{e['size']:X}.bin"
                file_path = os.path.join(out_dir, self._sanitize_filename(region_name))
                log(
                    f"[{idx + 1}/{len(candidates)}] dumping base=0x{e['base']:016X} size=0x{e['size']:X} -> {file_path}"
                )
                stats = self._dump_range_reliable(e["base"], e["size"], file_path, chunk_size)
                manifest["regions"].append({
                    "index": idx,
                    "base": e["base"],
                    "size": e["size"],
                    "state": e["state"],
                    "protect": e["protect"],
                    "type": e["type"],
                    "file": os.path.basename(file_path),
                    "bad_pages_count": stats["bad_pages_count"],
                    "success_pages": stats["success_pages"],
                    "total_pages": stats["total_pages"],
                    "success_bytes": stats["success_bytes"],
                    "failed_bytes": stats["failed_bytes"],
                })

            manifest["dump_count"] = len(manifest["regions"])
            manifest_path = os.path.join(out_dir, "private_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2, ensure_ascii=False)
            log(f"dump_private finished. Manifest: {manifest_path}", "SUCCESS")
        except ValueError:
            log("PID/ChunkSizeHex/WaitSec format error.", "ERROR")
        except Exception as e:
            log(f"dump_private error: {e}", "ERROR")

    def handle_pack_private(self, args):
        if len(args) < 2:
            log("Usage: pack_private <ManifestJson> <OutFile>", "ERROR")
            return

        manifest_path = os.path.abspath(args[0])
        out_file = os.path.abspath(args[1])

        if not os.path.exists(manifest_path):
            log(f"Manifest not found: {manifest_path}", "ERROR")
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
        except Exception as e:
            log(f"Failed to load manifest json: {e}", "ERROR")
            return

        regions = manifest.get("regions")
        if not isinstance(regions, list):
            log("Invalid manifest: 'regions' must be a list.", "ERROR")
            return

        manifest_dir = os.path.dirname(manifest_path)
        prepared = []
        total_payload = 0
        for idx, r in enumerate(regions):
            rel_name = r.get("file")
            if not rel_name:
                log(f"Region[{idx}] missing file field; skipped.", "WARN")
                continue

            region_path = os.path.join(manifest_dir, rel_name)
            if not os.path.exists(region_path):
                log(f"Region file missing: {region_path}", "ERROR")
                return

            data_size = os.path.getsize(region_path)
            total_payload += data_size
            prepared.append({
                "index": int(r.get("index", idx)),
                "base": int(r.get("base", 0)),
                "size": int(r.get("size", data_size)),
                "state": int(r.get("state", 0)),
                "protect": int(r.get("protect", 0)),
                "type": int(r.get("type", 0)),
                "bad_pages_count": int(r.get("bad_pages_count", 0)),
                "path": region_path,
                "name": os.path.basename(region_path),
                "data_size": data_size,
            })

        if not prepared:
            log("No valid region files to pack.", "ERROR")
            return

        cursor = 0
        packed_regions = []
        for p in prepared:
            packed_regions.append({
                "index": p["index"],
                "base": p["base"],
                "size": p["size"],
                "state": p["state"],
                "protect": p["protect"],
                "type": p["type"],
                "bad_pages_count": p["bad_pages_count"],
                "name": p["name"],
                "data_offset": cursor,
                "data_size": p["data_size"],
            })
            cursor += p["data_size"]

        header = {
            "format": "RWBASE_PRIVATE_PACK_V1",
            "created_at_local": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_manifest": os.path.basename(manifest_path),
            "pid": int(manifest.get("pid", 0)),
            "udtb": int(manifest.get("udtb", 0)),
            "kdtb": int(manifest.get("kdtb", 0)),
            "base_addr": int(manifest.get("base_addr", 0)),
            "region_count": len(packed_regions),
            "payload_size": total_payload,
            "regions": packed_regions,
        }
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        out_dir = os.path.dirname(out_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        try:
            with open(out_file, "wb") as wf:
                wf.write(b"RWPACKV1")
                wf.write(struct.pack("<IIQ", 1, len(header_bytes), total_payload))
                wf.write(header_bytes)

                copied = 0
                for i, p in enumerate(prepared):
                    log(
                        f"Packing [{i + 1}/{len(prepared)}] base=0x{p['base']:016X} "
                        f"size=0x{p['size']:X} file={p['name']}"
                    )
                    with open(p["path"], "rb") as rf:
                        while True:
                            chunk = rf.read(1024 * 1024)
                            if not chunk:
                                break
                            wf.write(chunk)
                            copied += len(chunk)

            log(
                f"pack_private finished. out={out_file}, "
                f"regions={len(prepared)}, payload={total_payload} bytes, copied={copied} bytes",
                "SUCCESS",
            )
        except Exception as e:
            log(f"pack_private error: {e}", "ERROR")

    def handle_start_data_threads(self):
        ack = self.api.start_data_threads()
        if ack is None:
            log("start_data_threads failed (no response).", "ERROR")
            return
        handled = (ack & CTRL_ACK_HANDLED) != 0
        engine_online = (ack & CTRL_ACK_CPUEAXH_ONLINE) != 0
        if not handled:
            log(f"RWbase streaming enable returned malformed ACK=0x{ack:X}.", "WARN")
            return

        level = "SUCCESS" if engine_online else "WARN"
        cpueaxh_state = "online" if engine_online else "offline"
        log(f"RWbase streaming enabled (ACK=0x{ack:X}, cpueaxh={cpueaxh_state}).", level)

    def handle_stop_data_threads(self):
        ack = self.api.stop_data_threads()
        if ack is None:
            log("stop_data_threads failed (no response).", "ERROR")
            return
        handled = (ack & CTRL_ACK_HANDLED) != 0
        engine_online = (ack & CTRL_ACK_CPUEAXH_ONLINE) != 0
        if not handled:
            log(f"RWbase streaming disable returned malformed ACK=0x{ack:X}.", "WARN")
            return

        level = "SUCCESS" if engine_online else "WARN"
        cpueaxh_state = "online" if engine_online else "offline"
        log(f"RWbase streaming disabled (ACK=0x{ack:X}, cpueaxh={cpueaxh_state}).", level)

    def handle_cpueaxh_ping(self):
        ack = self.api.ping_cpueaxh()
        if ack is None:
            log("cpueaxh_ping failed (no response).", "ERROR")
            return

        handled = (ack & CTRL_ACK_HANDLED) != 0
        engine_online = (ack & CTRL_ACK_CPUEAXH_ONLINE) != 0
        if not handled:
            log(f"cpueaxh_ping returned malformed ACK=0x{ack:X}.", "WARN")
            return

        level = "SUCCESS" if engine_online else "WARN"
        cpueaxh_state = "online" if engine_online else "offline"
        log(f"cpueaxh_ping ACK=0x{ack:X}, cpueaxh={cpueaxh_state}.", level)

    def handle_stream_stats(self):
        log("stream_stats is deprecated; use 'stream_log stats'.", "WARN")
        self._print_stream_lines("all")

    def handle_stream_log(self, args):
        action = "watch"
        rest = list(args)
        if rest:
            head = rest[0].lower()
            if head in ("on", "off", "ping", "stats", "watch", "all", "decode", "send"):
                action = head
                rest = rest[1:]

        if action == "on":
            self.handle_start_data_threads()
            return
        if action == "off":
            self.handle_stop_data_threads()
            return
        if action == "ping":
            self.handle_cpueaxh_ping()
            return
        if action == "stats":
            self._print_stream_lines("all")
            return

        mode = action
        if mode == "watch":
            mode = "all"
        if mode not in ("all", "decode", "send"):
            mode = "all"

        interval = 1.0
        if rest:
            try:
                interval = float(rest[0])
            except ValueError:
                log("Usage: stream_log [on/off/ping/stats/watch/all/decode/send] [IntervalSec]", "ERROR")
                return

        if interval <= 0:
            log("IntervalSec must be > 0.", "ERROR")
            return

        log(
            f"Watching stream mode={mode} "
            "This is passive view; incoming [LOG] packets are still printed automatically. Ctrl+C stops this watcher."
        )
        try:
            while True:
                self._print_stream_lines(mode)
                time.sleep(interval)
        except KeyboardInterrupt:
            log("stream_log stopped.")

    def handle_rwbase_host(self, args):
        action = args[0].lower() if args else "stats"

        if action == "stats":
            log("rwbase_host stats is deprecated; use 'stream_log stats'.", "WARN")
            self._print_stream_lines("decode")
            return

        if action == "recent":
            diag = self.api.core.get_rwbase_host_diag()
            recent = diag.get("recent_logs", [])
            if not recent:
                log("No parsed RWbase host logs captured yet.", "WARN")
                return
            for item in recent[-10:]:
                log(f"{item.get('category')}: {item.get('raw')}")
            return

        if action == "watch":
            interval = 1.0
            if len(args) >= 2:
                try:
                    interval = float(args[1])
                except ValueError:
                    log("Usage: rwbase_host [stats/watch/recent] [IntervalSec]", "ERROR")
                    return
            if interval <= 0:
                log("IntervalSec must be > 0.", "ERROR")
                return

            log("rwbase_host watch is deprecated; use 'stream_log watch [IntervalSec]'.", "WARN")
            try:
                while True:
                    self._print_stream_lines("decode")
                    time.sleep(interval)
            except KeyboardInterrupt:
                log("rwbase_host watch stopped.")
            return

        log("Usage: rwbase_host [stats/watch/recent] [IntervalSec]", "ERROR")

    def handle_rwbase_stream(self, args):
        log("rwbase_stream is deprecated; use 'stream_log ...'.", "WARN")
        self.handle_stream_log(args)

    def handle_rwbase_data(self, args):
        log("rwbase_data is deprecated; use 'stream_log ...'.", "WARN")
        self.handle_stream_log(args)

    def handle_webradar(self, args):
        action = args[0].lower() if args else "status"

        if action == "status":
            status = self.web_radar.status()
            state = "running" if status.get("running") else "stopped"
            log(
                f"WebRadar {state} on 0.0.0.0:{status.get('port')} "
                f"token={status.get('password')} pwd={status.get('pwd_path')}"
            )
            return

        if action == "start":
            port = None
            if len(args) >= 2:
                try:
                    port = int(args[1])
                except ValueError:
                    log("Usage: webradar <start/stop/status> [Port]", "ERROR")
                    return
            ok, message = self.web_radar.start(port_override=port)
            log(f"WebRadar {message}", "SUCCESS" if ok else "ERROR")
            return

        if action == "stop":
            ok, message = self.web_radar.stop()
            level = "SUCCESS" if ok else "WARN"
            log(f"WebRadar {message}", level)
            return

        log("Usage: webradar <start/stop/status> [Port]", "ERROR")

    def shutdown(self):
        if not hasattr(self, "web_radar"):
            return
        status = self.web_radar.status()
        if not status.get("running"):
            return
        ok, message = self.web_radar.stop()
        log(f"WebRadar {message}", "SUCCESS" if ok else "WARN")

    def handle_dump_mem(self, args):
        if len(args) < 3:
            log("Usage: dump_mem <HexAddr> <HexSize> <Filename> [ChunkSizeHex]", "ERROR")
            return
        try:
            target_addr = int(args[0], 16)
            target_size = int(args[1], 16)
            filename = args[2]
            chunk_size = int(args[3], 0) if len(args) >= 4 else DEFAULT_DUMP_CHUNK_SIZE

            if target_size <= 0:
                log("HexSize must be > 0.", "ERROR")
                return
            if chunk_size < 0x1000:
                log("ChunkSizeHex must be >= 0x1000.", "ERROR")
                return

            if self.api.cached_dtb == 0:
                log("Cached DTB is 0. Trying to refresh from cached PID...", "WARN")
            self._dump_range_reliable(target_addr, target_size, filename, chunk_size)
        except Exception as e:
            log(f"Dump error: {e}", "ERROR")

    def handle_retry_bad_pages(self, args):
        if len(args) < 1:
            log("Usage: retry_bad_pages <DumpFile> [BadPagesFile] [ChunkSizeHex]", "ERROR")
            return
        try:
            dump_file = args[0]
            bad_pages_file = args[1] if len(args) >= 2 else os.path.join(
                os.path.dirname(os.path.abspath(dump_file)),
                "bad_pages.txt",
            )
            chunk_override = int(args[2], 0) if len(args) >= 3 else None

            if not os.path.exists(dump_file):
                log(f"Dump file not found: {dump_file}", "ERROR")
                return
            if not os.path.exists(bad_pages_file):
                log(f"Bad pages file not found: {bad_pages_file}", "ERROR")
                return

            start_addr = None
            total_size = None
            source_page_size = DEFAULT_DUMP_CHUNK_SIZE
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
                                    source_page_size = int(p.split("page_size=")[1], 16)
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
            retry_chunk_size = chunk_override if chunk_override is not None else DEFAULT_RETRY_CHUNK_SIZE
            if chunk_override is not None:
                if chunk_override < 0x1000:
                    log("ChunkSizeHex must be >= 0x1000.", "ERROR")
                    return
            else:
                # Keep robust default for bad-page recovery regardless of dump chunk size.
                retry_chunk_size = DEFAULT_RETRY_CHUNK_SIZE

            log(
                f"Retrying {len(bad_entries)} bad pages from '{bad_pages_file}' "
                f"into '{dump_file}' (base=0x{start_addr:X}, source_page=0x{source_page_size:X}, "
                f"retry_chunk=0x{retry_chunk_size:X})..."
            )

            page_timeout = 3.0
            max_retries = 3
            total_retry_chunks = sum((sz + retry_chunk_size - 1) // retry_chunk_size for _, sz in bad_entries if sz > 0)
            recovered = 0
            still_bad = []
            processed_chunks = 0

            with open(dump_file, "r+b") as f:
                for idx, (va, sz) in enumerate(bad_entries, 1):
                    if sz <= 0:
                        still_bad.append((va, sz, "invalid_size"))
                        continue

                    off = va - start_addr
                    if off < 0 or (off + sz) > file_size:
                        still_bad.append((va, sz, "offset_oob"))
                        continue

                    chunk_off = 0
                    while chunk_off < sz:
                        sub_sz = min(retry_chunk_size, sz - chunk_off)
                        sub_va = va + chunk_off
                        sub_file_off = off + chunk_off

                        ok = False
                        for attempt in range(1, max_retries + 1):
                            data = self.api.read_chunk(sub_va, sub_sz, timeout=page_timeout)
                            if data and len(data) == sub_sz:
                                f.seek(sub_file_off)
                                f.write(data)
                                recovered += 1
                                ok = True
                                break
                            if attempt < max_retries:
                                time.sleep(0.05 * attempt)

                        processed_chunks += 1
                        if not ok:
                            still_bad.append((sub_va, sub_sz, "read_failed"))
                        chunk_off += sub_sz

                    if idx % 128 == 0 or idx == len(bad_entries):
                        pct = (processed_chunks / total_retry_chunks) * 100.0 if total_retry_chunks else 100.0
                        log(
                            f"Retry progress: chunks {processed_chunks}/{total_retry_chunks} ({pct:.1f}%), "
                            f"entries {idx}/{len(bad_entries)}, recovered={recovered}, remaining={len(still_bad)}"
                        )

            remaining_file = os.path.join(
                os.path.dirname(os.path.abspath(bad_pages_file)),
                "bad_pages_remaining.txt",
            )
            with open(remaining_file, "w", encoding="utf-8") as rf:
                rf.write("# remaining bad pages after retry_bad_pages\n")
                rf.write(
                    f"# source={bad_pages_file}\n"
                    f"# recovered={recovered}, remaining={len(still_bad)}, total_entries={len(bad_entries)}, "
                    f"total_chunks={total_retry_chunks}, retry_chunk_size=0x{retry_chunk_size:X}\n"
                )
                rf.write("# columns: va,size,reason\n")
                for va, sz, reason in still_bad:
                    rf.write(f"0x{va:X},0x{sz:X},{reason}\n")

            ratio = (recovered / total_retry_chunks) * 100.0 if total_retry_chunks else 100.0
            log(
                f"Retry finished. recovered={recovered}/{total_retry_chunks} chunks ({ratio:.2f}%), "
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

