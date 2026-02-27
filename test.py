import socket
import struct
import time

# 配置信息
VM_IP = "10.10.10.140"  # 虚拟机的 IP 地址
VM_PORT = 10000        # 随意一个端口，VMM 会拦截所有流量
PAYLOAD_MAGIC = 0x504C444D  # "MLDP" 小端序 (0x4D, 0x4C, 0x50, 0x50)

def send_heartbeat():
    # 创建 UDP 套接字
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    
    # 构造魔包: [Magic (4 bytes)] [Command (4 bytes)] [Data (8 bytes)]
    # 这里我们发送 CMD_GET_CR3 (1) 来看 VMM 是否能返回 Guest 的 CR3
    command_id = 1 
    packet = struct.pack("<IIQ", PAYLOAD_MAGIC, command_id, 0)

    print(f"[*] Sending Magic Packet to {VM_IP}:{VM_PORT}...")
    
    try:
        # 发送魔包
        sock.sendto(packet, (VM_IP, VM_PORT))
        
        # 等待回包 (VMM 会伪造一个回包发送到宿主机)
        # 注意：由于 VMM 是直接劫持网卡发的，可能需要 Wireshark 辅助观察
        data, addr = sock.recvfrom(1024)
        if len(data) >= 8:
            res = struct.unpack("<Q", data[:8])[0]
            print(f"[+] VMM ALIVE! Guest CR3: {hex(res)}")
    except socket.timeout:
        print("[-] Timeout. No response from VMM. Possible reasons:")
        print("    1. NDIS Hook not installed.")
        print("    2. g_StolenNdisHandle is NULL (Guest hasn't sent any packet yet).")
    except Exception as e:
        print(f"[-] Error: {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    send_heartbeat()
