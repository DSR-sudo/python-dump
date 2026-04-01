import os
import struct

# RWbase src/Core/Network/Network.hpp defaults.
DRIVER_IP = os.getenv("DMA_DRIVER_IP", "192.168.10.142")
DRIVER_PORT = int(os.getenv("DMA_DRIVER_PORT", "10005"))
BIND_PORT = int(os.getenv("DMA_BIND_PORT", str(DRIVER_PORT)))

MAGIC_KEY = 0xDEADBEEF

CMD_READ_MEM = 1
CMD_WRITE_MEM = 2
CMD_GET_CR3 = 3
CMD_ENUM_USER_MODULES = 5
CMD_INSTALL_CALLBACK_INTERCEPTOR = 6
CMD_UNINSTALL_CALLBACK_INTERCEPTOR = 7
CMD_COLD_CODE_ADAPT_MEMORY_PAGE = 8
CMD_COLD_CODE_ADAPT_PHYSICAL = 9
CMD_MEMORY_PAGE_ADAPT = 10
CMD_VM_GET_PROCESS_INFO = 11
CMD_VM_MEMORY_COPY = 12

PACKET_TYPE_LOG = 0x01
PACKET_TYPE_DATA = 0x02

# RWbase src/Utils/Definitions.hpp:
# #pragma pack(push, 1)
# struct PACKET_REQUEST {
#   UINT32 Magic;
#   UINT8  Command;
#   UINT64 Value;
#   UINT64 Address;
#   UINT32 Size;
#   UCHAR  Data[1024];
# }
# #pragma pack(pop)
PACKET_DATA_CAP = 1024
PACKET_FMT = "<IBQQI1024s"


def parse_packet_header(data: bytes):
    if not data:
        return None, None
    return data[0], data[1:]


def _pack_request(command, value=0, address=0, size=0, data=b""):
    payload = (data or b"")[:PACKET_DATA_CAP]
    payload = payload.ljust(PACKET_DATA_CAP, b"\x00")
    return struct.pack(
        PACKET_FMT,
        MAGIC_KEY,
        command & 0xFF,
        value & 0xFFFFFFFFFFFFFFFF,
        address & 0xFFFFFFFFFFFFFFFF,
        size & 0xFFFFFFFF,
        payload,
    )


def pack_read_req(cr3, addr, size):
    return _pack_request(CMD_READ_MEM, value=cr3, address=addr, size=size)


def pack_write_req(cr3, addr, data: bytes):
    data = data or b""
    return _pack_request(CMD_WRITE_MEM, value=cr3, address=addr, size=len(data), data=data)


def pack_cr3_req(pid):
    return _pack_request(CMD_GET_CR3, value=pid)


def pack_enum_modules_req(pid):
    return _pack_request(CMD_ENUM_USER_MODULES, value=pid)


def pack_vm_get_process_info_req(pid):
    return _pack_request(CMD_VM_GET_PROCESS_INFO, value=pid)


def pack_vm_memory_copy_req(pid, src_va, size):
    return _pack_request(CMD_VM_MEMORY_COPY, value=pid, address=src_va, size=size)


def pack_install_callback_interceptor_req(pid, target_va, cold_va):
    return _pack_request(
        CMD_INSTALL_CALLBACK_INTERCEPTOR,
        value=pid,
        address=target_va,
        size=8,
        data=struct.pack("<Q", cold_va),
    )


def pack_uninstall_callback_interceptor_req(pid, target_va):
    return _pack_request(CMD_UNINSTALL_CALLBACK_INTERCEPTOR, value=pid, address=target_va)


def pack_cold_code_adapt_memory_page_req(pid, target_cold_va, shellcode: bytes):
    shellcode = shellcode or b""
    return _pack_request(
        CMD_COLD_CODE_ADAPT_MEMORY_PAGE,
        value=pid,
        address=target_cold_va,
        size=len(shellcode),
        data=shellcode,
    )


def pack_cold_code_adapt_physical_req(pid, target_cold_va, shellcode: bytes):
    shellcode = shellcode or b""
    return _pack_request(
        CMD_COLD_CODE_ADAPT_PHYSICAL,
        value=pid,
        address=target_cold_va,
        size=len(shellcode),
        data=shellcode,
    )


def pack_memory_page_adapt_req(pid, target_va, patch: bytes):
    patch = patch or b""
    return _pack_request(
        CMD_MEMORY_PAGE_ADAPT,
        value=pid,
        address=target_va,
        size=len(patch),
        data=patch,
    )
