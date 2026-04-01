# main.py
import time
from dma_core import DMACore
from dma_api import DMAApi
from dma_command import CommandHandler, log, print_banner, print_detailed_help

def main():
    print_banner()
    
    # 1. 初始化 DMA 通信
    core = DMACore() #
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
            line = input("\n>> ").strip()
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
            elif cmd == "vt_getp":
                handler.handle_vt_getp(args)
            elif cmd == "vt_memc":
                handler.handle_vt_memc(args)
            elif cmd == "vt_hoon":
                handler.handle_vt_hoon(args)
            elif cmd == "vt_uoon":
                handler.handle_vt_uoon(args)
            elif cmd == "vt_mhok":
                handler.handle_vt_mhok(args)
            elif cmd == "vt_khok":
                handler.handle_vt_khok(args)
            elif cmd == "vt_epat":
                handler.handle_vt_epat(args)
            elif cmd == "dump_mem":
                handler.handle_dump_mem(args)
                
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

if __name__ == "__main__":
    main()
