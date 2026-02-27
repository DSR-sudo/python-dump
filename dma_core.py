import socket
import threading
import time
import struct
from dma_protocol import *

class DMACore:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 允许端口复用（可选，开发调试时有用）
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", BIND_PORT))
        
        # 增大内核缓冲区，防止大数据流传输时丢包
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024 * 1024)
        except:
            print("[!] Warning: Could not set 64MB Recv Buffer. OS limit might be lower.")
            # 降级尝试 32MB
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)

        self.is_running = True
        self.driver_online = False
        self.seq = 0
        
        # 接收同步
        self.recv_event = threading.Event()
        self.buffer = bytearray()
        self.expected_size = 0
        self.lock = threading.Lock() # 保证请求串行

        threading.Thread(target=self._receiver_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _receiver_loop(self):
        """
        [高性能版] 核心接收循环
        使用 recv_into + scratch_buffer 实现 零拷贝 + 头部解析
        """
        print(f"[*] UDP Receiver started on port {BIND_PORT}")
        
        # [优化 1] 静态分配一个 64KB 的暂存区
        # 整个生命周期只分配这一次内存，彻底消除 GC 压力
        scratch_buffer = bytearray(65536)
        
        while self.is_running:
            try:
                # [优化 2] 使用 recv_into 直接读入暂存区
                # nbytes 是实际读取到的字节数
                # 这一步不创建任何 Python 对象 (bytes)，极快
                nbytes = self.sock.recv_into(scratch_buffer)
                
                if nbytes < 1: continue

                # 解析头部 (直接访问内存，不切片)
                pkt_type = scratch_buffer[0]
                
                # ==========================================================
                # 分支 1: 日志包 / 心跳包 (Type = 0x01)
                # ==========================================================
                if pkt_type == PACKET_TYPE_LOG:
                    try:
                        # 只有日志包才需要解码，不影响数据包性能
                        # scratch_buffer[1:nbytes] 虽然切片了，但日志频率低，无所谓
                        msg = scratch_buffer[1:nbytes].decode('utf-8', errors='ignore').strip()
                        
                        if "ALIVE_ACK" in msg or "DRIVER_ONLINE" in msg:
                            if not self.driver_online:
                                print("[+] Driver is ONLINE.")
                            self.driver_online = True
                            continue
                        
                        if msg:
                            print(f"\r[LOG] {msg}\n>> ", end="")
                    except: pass

                # ==========================================================
                # 分支 2: 业务数据包 (Type = 0x02)
                # ==========================================================
                elif pkt_type == PACKET_TYPE_DATA:
                    # 仅在主线程处于"接收态"时处理
                    if self.expected_size > 0:
                        payload_len = nbytes - 1
                        
                        # [核心优化 3] 内存视图切片赋值
                        # 将暂存区的数据 (scratch_buffer) "搬运" 到最终 Buffer (self.view)
                        # 这是一个纯内存操作 (memcpy)，速度极快，且不涉及 Python 对象创建
                        if self.recvd_bytes + payload_len <= self.expected_size:
                            self.view[self.recvd_bytes : self.recvd_bytes + payload_len] = scratch_buffer[1:nbytes]
                            self.recvd_bytes += payload_len
                        
                        # 检查是否收满
                        if self.recvd_bytes >= self.expected_size:
                            self.expected_size = 0 # 关闭接收态
                            self.recv_event.set()  # 唤醒主线程
                
                # ==========================================================
                # 其他包忽略
                # ==========================================================
                else:
                    pass

            except Exception as e:
                # 只有 socket 关闭时才退出，普通错误忽略
                if not self.is_running: break
                pass

    def _heartbeat_loop(self):
        while self.is_running:
            try:
                payload = b'HELO'.ljust(32,b'\x00')
                self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
                self.seq += 1
                time.sleep(1.0)
            except: pass

    def request_bytes(self, payload, size, timeout=3.0):
        """
        [优化版] 预分配内存模式
        """
        with self.lock:
            self.recv_event.clear()
            
            # [核心优化] 预分配 Buffer，避免接收时的动态扩容
            self.buffer = bytearray(size)
            # 创建内存视图，允许直接对 buffer 进行切片写入，无需拷贝
            self.view = memoryview(self.buffer)
            self.recvd_bytes = 0
            self.expected_size = size # 告诉接收循环：开始干活了
            
            # 发送请求指令
            self.sock.sendto(payload, (DRIVER_IP, DRIVER_PORT))
            
            # 动态超时计算 (每 100MB 增加 1秒)
            dynamic_timeout = 50
            print(f"[*] Expecting {size} bytes, timeout set to {dynamic_timeout:.1f}s")

            if self.recv_event.wait(timeout=dynamic_timeout):
                # 接收完成，返回填满数据的 buffer
                # 注意：这里返回 bytes(self.buffer) 可能会有一次拷贝，
                # 如果追求极致，可以直接返回 self.buffer
                return self.buffer

            percent = (self.recvd_bytes / size) * 100
            
            print(f"[-] Timeout! Received {self.recvd_bytes}/{size} bytes ({percent:.1f}%).Packet Loss detected!")
            return None
