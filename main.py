# main.py
import atexit
import time
from pathlib import Path
from dma_core import DMACore
from dma_api import DMAApi
from dma_command import CommandHandler, log, print_banner, print_detailed_help
from session_log import SessionLog

def main():
    session_log = SessionLog.start(Path(__file__).resolve().parent)
    atexit.register(session_log.close)
    print(f"[+] Session log: {session_log.path}")
    print_banner()
    
    # 1. 初始化 DMA 通信
    core = DMACore(session_log=session_log) #
    api = DMAApi(core) #

    log("Waiting for DMA Driver connection...")
    while not core.driver_online: 
        time.sleep(0.1)
    log("Driver Connected!", "SUCCESS")

    # 2. 初始化命令处理器
    handler = CommandHandler(api)

    # 3. 主循环
    while True:
        try:
            core.begin_console_input()
            try:
                line = input("\n>> ").strip()
            finally:
                core.end_console_input()
            if not line: continue
            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd == "help":
                print_detailed_help()
            elif cmd == "attach":
                handler.handle_attach(args)
            elif cmd == "pe_info":
                handler.handle_pe_info()
            elif cmd == "auto_init":
                handler.handle_auto_init()
            elif cmd == "cache_gnames":
                handler.handle_cache_gnames()
            elif cmd == "dump_sdk":
                handler.handle_dump_sdk(args)
            elif cmd == "modules":
                handler.handle_modules(args)
            elif cmd == "vt_fpat":
                handler.handle_vt_fpat(args)
            elif cmd == "start_data_threads":
                handler.handle_start_data_threads()
            elif cmd == "stop_data_threads":
                handler.handle_stop_data_threads()
            elif cmd == "cpueaxh_ping":
                handler.handle_cpueaxh_ping()
            elif cmd == "stream_stats":
                handler.handle_stream_stats()
            elif cmd == "stream_log":
                handler.handle_stream_log(args)
            elif cmd == "rwbase_decrypt":
                handler.handle_rwbase_decrypt(args)
            elif cmd == "coord_raw":
                handler.handle_coord_raw(args)
            elif cmd == "rwbase_stream":
                handler.handle_rwbase_stream(args)
            elif cmd == "rwbase_data":
                handler.handle_rwbase_data(args)
            elif cmd == "webradar":
                handler.handle_webradar(args)
            elif cmd == "actorkinds":
                handler.handle_actorkinds(args)
            elif cmd == "dump_mem":
                handler.handle_dump_mem(args)
            elif cmd == "regions":
                handler.handle_regions(args)
            elif cmd == "dump_private":
                handler.handle_dump_private(args)
            elif cmd == "pack_private":
                handler.handle_pack_private(args)
            elif cmd == "retry_bad_pages":
                handler.handle_retry_bad_pages(args)
                
            elif cmd == "fast_init":
                handler.handle_fast_init()
            elif cmd == "watch":
                handler.handle_watch(args)
                
            elif cmd == "cr3":
                if args:
                    u, k, b = api.get_cr3(int(args[0]))
                    log(f"UserDTB: {hex(u)}, KernelDTB: {hex(k)}, Base: {hex(b)}")
                    
            elif cmd == "watch2file":
                handler.handle_watch2file(args)
                
            elif cmd == "exit":
                log("Exiting...")
                break
            else:
                log("Unknown command. Type 'help'.", "WARN")

        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Error: {e}", "ERROR")
    handler.shutdown()

if __name__ == "__main__":
    main()
