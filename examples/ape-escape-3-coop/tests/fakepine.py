"""A fake PCSX2 that speaks PINE, for tests and for `aecoops selfcheck`.

The protocol is implemented from PCSX2's own ``PINEServer::ParseCommand``:
4-byte total length, then commands, then a reply of ``size, result, payload``.
Having a second implementation that the client is tested against is the point --
if the client's framing were wrong, a mock that shared its bug would happily agree
with it, but this one is written against the C++ and does not.

It models a 32 MiB EE RAM image, one client at a time (as PCSX2 does), and the
request/reply size ceilings (650000 / 450000) so the client's batching is
exercised at a realistic limit.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Callable

MAX_IPC_SIZE = 650000
MAX_IPC_RETURN_SIZE = 450000

MSG_READ8, MSG_READ16, MSG_READ32, MSG_READ64 = 0x00, 0x01, 0x02, 0x03
MSG_WRITE8, MSG_WRITE16, MSG_WRITE32, MSG_WRITE64 = 0x04, 0x05, 0x06, 0x07
MSG_VERSION, MSG_SAVESTATE, MSG_LOADSTATE = 0x08, 0x09, 0x0A
MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION, MSG_STATUS = 0x0B, 0x0C, 0x0D, 0x0E, 0x0F

OK, FAIL = 0x00, 0xFF


class ProtocolError(RuntimeError):
    pass


# The EE's 16 KiB scratchpad is a physically separate memory, mapped at
# 0x70000000. A fake that stops at the end of main RAM would reject reads the real
# emulator serves happily -- which matters because the default scan range covers
# both, and a signature scan that dies on the scratchpad would look like a broken
# client rather than an incomplete fake.
SCRATCHPAD_BASE = 0x70000000
SCRATCHPAD_SIZE = 0x4000


class FakePCSX2:
    """A PINE server backed by a plain bytearray.

    >>> with FakePCSX2() as fake:
    ...     fake.ram[0x00100000] = 0x42
    ...     port = fake.port
    """

    def __init__(
        self,
        ram_size: int = 0x02000000,
        status: int = 0,
        strict: bool = True,
        ram: bytearray | None = None,
    ) -> None:
        # `ram` lets a test share one memory image with the interpreter, so writes
        # that arrive over PINE are visible to code the mod has installed.
        self.ram = ram if ram is not None else bytearray(ram_size)
        self.scratchpad = bytearray(SCRATCHPAD_SIZE)
        self.status = status
        self.strict = strict
        self.requests = 0
        self.commands = 0
        self.fail_next = False
        self.title = "Ape Escape 3"
        self.serial = "SCUS-97501"
        self.uuid = "a1b2c3d4"
        self.disc_version = "1.00"
        self.emulator_version = "PCSX2 v2.5.0-test"
        self.saves: list[int] = []
        self.loads: list[int] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # No SO_REUSEADDR: PCSX2 does not set it either, and a fake that grabs a
        # port another process already owns would test the wrong thing.
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ context

    def __enter__(self) -> "FakePCSX2":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        # shutdown() first: a thread blocked in accept() does not wake up for a
        # bare close(), and the listener would stay open. PCSX2's own
        # Deinitialize() does exactly this, for exactly this reason.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------ serving

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    self._session(conn)
                except (OSError, ProtocolError):
                    continue

    def _session(self, conn: socket.socket) -> None:
        while not self._stop.is_set():
            header = self._read_exactly(conn, 4)
            if header is None:
                return
            (total,) = struct.unpack("<I", header)
            if total < 4 or total > MAX_IPC_SIZE:
                raise ProtocolError(f"client announced {total} bytes")
            body = self._read_exactly(conn, total - 4)
            if body is None:
                return
            self.requests += 1
            reply = self._parse(body)
            conn.sendall(reply)

    @staticmethod
    def _read_exactly(conn: socket.socket, n: int) -> bytes | None:
        chunks = b""
        while len(chunks) < n:
            piece = conn.recv(n - len(chunks))
            if not piece:
                return None
            chunks += piece
        return chunks

    def _parse(self, buf: bytes) -> bytes:
        ret = bytearray(b"\x00\x00\x00\x00\x00")  # size + result, payload appended after
        assert len(ret) == 5
        i = 0
        while i < len(buf):
            opcode = buf[i]
            i += 1
            self.commands += 1
            if self.fail_next or self.status == 2 and opcode not in (MSG_STATUS, MSG_VERSION):
                return bytes(struct.pack("<I", 5) + bytes([FAIL]))
            if opcode in (MSG_READ8, MSG_READ16, MSG_READ32, MSG_READ64):
                if self.status == 2:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
                (addr,) = struct.unpack_from("<I", buf, i)
                i += 4
                size = {MSG_READ8: 1, MSG_READ16: 2, MSG_READ32: 4, MSG_READ64: 8}[opcode]
                if not self._can_read(addr, size) and self.strict:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
                ret += self._mem_read(addr, size)
                if len(ret) >= MAX_IPC_RETURN_SIZE:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
            elif opcode in (MSG_WRITE8, MSG_WRITE16, MSG_WRITE32, MSG_WRITE64):
                if self.status == 2:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
                size = {MSG_WRITE8: 1, MSG_WRITE16: 2, MSG_WRITE32: 4, MSG_WRITE64: 8}[opcode]
                (addr,) = struct.unpack_from("<I", buf, i)
                i += 4
                (value,) = struct.unpack_from({1: "<B", 2: "<H", 4: "<I", 8: "<Q"}[size], buf, i)
                i += size
                if not self._can_read(addr, size) and self.strict:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
                self._mem_write(addr, value, size)
            elif opcode in (MSG_VERSION, MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION):
                if opcode != MSG_VERSION and self.status == 2:
                    return bytes(struct.pack("<I", 5) + bytes([FAIL]))
                text = {
                    MSG_VERSION: self.emulator_version,
                    MSG_TITLE: self.title,
                    MSG_ID: self.serial,
                    MSG_UUID: self.uuid,
                    MSG_GAMEVERSION: self.disc_version,
                }[opcode].encode() + b"\x00"
                ret += struct.pack("<I", len(text)) + text
            elif opcode == MSG_STATUS:
                ret += struct.pack("<I", self.status)
            elif opcode == MSG_SAVESTATE:
                self.saves.append(buf[i])
                i += 1
            elif opcode == MSG_LOADSTATE:
                self.loads.append(buf[i])
                i += 1
            else:
                return bytes(struct.pack("<I", 5) + bytes([FAIL]))
        struct.pack_into("<I", ret, 0, len(ret))
        ret[4] = OK
        return bytes(ret)

    # ------------------------------------------------------------------ storage

    def _region(self, addr: int, size: int) -> tuple[bytearray, int] | None:
        """Which memory an address lands in, as (buffer, offset) or None."""
        if 0 <= addr and addr + size <= len(self.ram):
            return self.ram, addr
        if SCRATCHPAD_BASE <= addr and addr + size <= SCRATCHPAD_BASE + SCRATCHPAD_SIZE:
            return self.scratchpad, addr - SCRATCHPAD_BASE
        return None

    def _can_read(self, addr: int, size: int) -> bool:
        return self._region(addr, size) is not None

    def _mem_read(self, addr: int, size: int) -> bytes:
        region = self._region(addr, size)
        if region is None:
            # Only reachable in non-strict mode: an unmapped EE read returns zeros,
            # which is what PCSX2 hands back for an address the guest does not map.
            return b"\x00" * size
        buf, offset = region
        return bytes(buf[offset : offset + size])

    def _mem_write(self, addr: int, value: int, size: int) -> None:
        region = self._region(addr, size)
        if region is None:
            return
        buf, offset = region
        buf[offset : offset + size] = value.to_bytes(size, "little")

    # ------------------------------------------------------------------ helpers

    def tick(self, addr: int, count: int = 1) -> None:
        """Advance a word in RAM: stands in for the game's frame counter."""
        (value,) = struct.unpack_from("<I", self.ram, addr)
        struct.pack_into("<I", self.ram, addr, (value + count) & 0xFFFFFFFF)

    def plant_signature(self, addr: int, data: bytes) -> None:
        self.ram[addr : addr + len(data)] = data


def serve_ram(handler: Callable[[int, int, bytes], None] | None = None) -> FakePCSX2:
    """Convenience for tests that just want a server."""
    fake = FakePCSX2()
    if handler is not None:
        fake.on_write = handler  # type: ignore[attr-defined]
    return fake
