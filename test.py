import socket
import struct

from dma_protocol import (
    DRIVER_IP,
    DRIVER_PORT,
    MAGIC_KEY,
    CMD_GET_CR3,
    PACKET_FMT,
)


def send_cr3_probe(pid=4, timeout=2.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    # Build a full PACKET_REQUEST-compatible request.
    packet = struct.pack(
        PACKET_FMT,
        MAGIC_KEY,
        CMD_GET_CR3,
        pid,
        0,
        0,
        b"\x00" * 1024,
    )

    print(f"[*] Sending CMD_GET_CR3 to {DRIVER_IP}:{DRIVER_PORT} (pid={pid})")

    try:
        sock.sendto(packet, (DRIVER_IP, DRIVER_PORT))
        data, addr = sock.recvfrom(4096)
        print(f"[+] Received {len(data)} bytes from {addr}")
    except socket.timeout:
        print("[-] Timeout: no response")
    finally:
        sock.close()


if __name__ == "__main__":
    send_cr3_probe()
