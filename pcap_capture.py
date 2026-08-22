import ctypes
import ctypes.util
import socket
import sys


ROUTE_PROBE_PORT = 9


class PcapError(RuntimeError):
    pass


class _PcapAddr(ctypes.Structure):
    pass


_PcapAddr._fields_ = [
    ("next", ctypes.POINTER(_PcapAddr)),
    ("addr", ctypes.c_void_p),
    ("netmask", ctypes.c_void_p),
    ("broadaddr", ctypes.c_void_p),
    ("dstaddr", ctypes.c_void_p),
]


class _PcapIf(ctypes.Structure):
    pass


_PcapIf._fields_ = [
    ("next", ctypes.POINTER(_PcapIf)),
    ("name", ctypes.c_char_p),
    ("description", ctypes.c_char_p),
    ("addresses", ctypes.POINTER(_PcapAddr)),
    ("flags", ctypes.c_uint),
]


class _PcapPkthdr(ctypes.Structure):
    _fields_ = [("ts_sec", ctypes.c_long), ("ts_usec", ctypes.c_long),
                ("caplen", ctypes.c_uint), ("len", ctypes.c_uint)]


class _BpfInsn(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class _BpfProgram(ctypes.Structure):
    _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.POINTER(_BpfInsn))]


class PcapCapture:
    DLT_EN10MB = 1
    DLT_LINUX_SLL = 113
    DLT_LINUX_SLL2 = 276
    PCAP_D_IN = 1

    def __init__(self, target_ip, source_port_min, source_port_max, interface=None):
        library_name = ctypes.util.find_library("pcap")
        if not library_name:
            raise PcapError("libpcap is not installed")
        self._lib = ctypes.CDLL(library_name)
        self._configure_api()
        self._handle = None
        self._program = _BpfProgram()
        selected = interface or self._find_interface(target_ip)
        if not selected:
            raise PcapError(
                f"no capture interface is associated with target {target_ip}; "
                "set DMA_CAPTURE_IFACE to a libpcap device name"
            )
        if isinstance(selected, str):
            selected = selected.encode()
        error = ctypes.create_string_buffer(256)
        handle = self._lib.pcap_open_live(selected, 65535, 0, 1000, error)
        if not handle:
            raise PcapError(error.value.decode(errors="replace"))
        self._handle = handle
        datalink = self._lib.pcap_datalink(handle)
        if datalink not in (self.DLT_EN10MB, self.DLT_LINUX_SLL, self.DLT_LINUX_SLL2):
            raise PcapError(f"unsupported link-layer type {datalink}")
        expression = f"udp and dst host {target_ip} and src portrange {source_port_min}-{source_port_max}".encode()
        if self._lib.pcap_compile(handle, ctypes.byref(self._program), expression, 1, 0) != 0:
            raise PcapError(self._error())
        if self._lib.pcap_setfilter(handle, ctypes.byref(self._program)) != 0:
            raise PcapError(self._error())
        if self._lib.pcap_setdirection(handle, self.PCAP_D_IN) != 0:
            raise PcapError(self._error())

    def _configure_api(self):
        lib = self._lib
        lib.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(_PcapIf)), ctypes.c_char_p]
        lib.pcap_findalldevs.restype = ctypes.c_int
        lib.pcap_freealldevs.argtypes = [ctypes.POINTER(_PcapIf)]
        lib.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
        lib.pcap_open_live.restype = ctypes.c_void_p
        lib.pcap_datalink.argtypes = [ctypes.c_void_p]
        lib.pcap_datalink.restype = ctypes.c_int
        lib.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram), ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
        lib.pcap_setfilter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BpfProgram)]
        lib.pcap_setdirection.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.pcap_next_ex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_PcapPkthdr)), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        lib.pcap_next_ex.restype = ctypes.c_int
        lib.pcap_geterr.argtypes = [ctypes.c_void_p]
        lib.pcap_geterr.restype = ctypes.c_char_p
        lib.pcap_freecode.argtypes = [ctypes.POINTER(_BpfProgram)]
        lib.pcap_close.argtypes = [ctypes.c_void_p]

    def _error(self):
        return self._lib.pcap_geterr(self._handle).decode(errors="replace")

    def _find_interface(self, target_ip):
        devices = ctypes.POINTER(_PcapIf)()
        error = ctypes.create_string_buffer(256)
        if self._lib.pcap_findalldevs(ctypes.byref(devices), error) != 0:
            raise PcapError(error.value.decode(errors="replace"))
        try:
            entries = self._device_entries(devices)
            route_ip = self._route_source_ip(target_ip)
            return self._select_interface(entries, target_ip, route_ip)
        finally:
            self._lib.pcap_freealldevs(devices)

    @staticmethod
    def _device_entries(devices):
        entries = []
        current = devices
        while current:
            addresses = []
            address = current.contents.addresses
            while address:
                if address.contents.addr:
                    sockaddr = ctypes.string_at(address.contents.addr, 16)
                    family = int.from_bytes(sockaddr[:2], byteorder=sys.byteorder)
                    if family == socket.AF_INET:
                        addresses.append(socket.inet_ntoa(sockaddr[4:8]))
                address = address.contents.next
            entries.append((current.contents.name, tuple(addresses)))
            current = current.contents.next
        return tuple(entries)

    @staticmethod
    def _route_source_ip(target_ip):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target_ip, ROUTE_PROBE_PORT))
            return probe.getsockname()[0]
        except OSError:
            return None
        finally:
            probe.close()

    @staticmethod
    def _select_interface(entries, target_ip, route_ip):
        for name, addresses in entries:
            if target_ip in addresses:
                return name
        if route_ip:
            for name, addresses in entries:
                if route_ip in addresses:
                    return name
        return None

    @staticmethod
    def _parse_ipv4(frame, datalink):
        offset = 14
        if datalink == PcapCapture.DLT_LINUX_SLL:
            if len(frame) < 16 or int.from_bytes(frame[14:16], "big") != 0x0800:
                return None
            offset = 16
        elif datalink == PcapCapture.DLT_LINUX_SLL2:
            if len(frame) < 20 or int.from_bytes(frame[0:2], "big") != 0x0800:
                return None
            offset = 20
        if len(frame) < offset + 20:
            return None
        if datalink == PcapCapture.DLT_EN10MB:
            ethertype = int.from_bytes(frame[12:14], "big")
            if ethertype == 0x8100 and len(frame) >= 18:
                offset = 18
                ethertype = int.from_bytes(frame[16:18], "big")
            if ethertype != 0x0800:
                return None
        version_ihl = frame[offset]
        if version_ihl >> 4 != 4:
            return None
        header_length = (version_ihl & 0x0F) * 4
        if frame[offset + 9] != socket.IPPROTO_UDP or len(frame) < offset + header_length + 8:
            return None
        source_ip = socket.inet_ntoa(frame[offset + 12:offset + 16])
        source_port = int.from_bytes(frame[offset + header_length:offset + header_length + 2], "big")
        udp_length = int.from_bytes(frame[offset + header_length + 4:offset + header_length + 6], "big")
        payload_start = offset + header_length + 8
        if udp_length < 8 or payload_start + udp_length - 8 > len(frame):
            return None
        return source_ip, source_port, frame[payload_start:payload_start + udp_length - 8]

    def next_packet(self):
        header = ctypes.POINTER(_PcapPkthdr)()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        result = self._lib.pcap_next_ex(self._handle, ctypes.byref(header), ctypes.byref(data))
        if result == 0:
            return None
        if result < 0:
            raise PcapError(self._error())
        frame = ctypes.string_at(data, header.contents.caplen)
        return self._parse_ipv4(frame, self._lib.pcap_datalink(self._handle))

    def close(self):
        if self._handle:
            self._lib.pcap_freecode(ctypes.byref(self._program))
            self._lib.pcap_close(self._handle)
            self._handle = None
