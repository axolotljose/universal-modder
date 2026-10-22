"""PINE client: talk to a running PCSX2 from Python.

PINE is PCSX2's IPC protocol. It is enabled with *Settings -> Advanced -> Enable
PINE IPC Server* (default slot/port 28011). On Windows it is a TCP socket bound
to 127.0.0.1; on Linux/macOS it is an ``AF_UNIX`` socket at
``$XDG_RUNTIME_DIR/pcsx2.sock`` (``/tmp/pcsx2.sock`` when that is unset), with
``.<slot>`` appended when the slot is not the default one.

Wire format, taken from ``pcsx2/PINE.cpp``:

    request  = u32 total_length, then a stream of commands
    command  = u8 opcode, then arguments
    reply    = u32 size, u8 result, then payload

``total_length`` counts itself. ``size`` counts itself too, so a reply with no
payload is 5 bytes. A single request may batch many commands; the payload of the
reply is the concatenation of every command's result, in order. That is the
whole trick behind fast scanning: 8000 reads come back in one round trip.

Result codes: ``0x00`` OK, ``0xFF`` fail (no VM running, bad opcode, or a reply
that would be too large). After a failure PCSX2 resets its receive state, so the
client must drop the connection and reconnect instead of trying to resync.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

DEFAULT_SLOT = 28011

# Opcodes (see the IPCCommand enum in PINE.cpp).
MSG_READ8 = 0x00
MSG_READ16 = 0x01
MSG_READ32 = 0x02
MSG_READ64 = 0x03
MSG_WRITE8 = 0x04
MSG_WRITE16 = 0x05
MSG_WRITE32 = 0x06
MSG_WRITE64 = 0x07
MSG_VERSION = 0x08
MSG_SAVESTATE = 0x09
MSG_LOADSTATE = 0x0A
MSG_TITLE = 0x0B
MSG_ID = 0x0C
MSG_UUID = 0x0D
MSG_GAMEVERSION = 0x0E
MSG_STATUS = 0x0F

OK = 0x00
FAIL = 0xFF

STATUS_RUNNING = 0
STATUS_PAUSED = 1
STATUS_SHUTDOWN = 2

# PCSX2 caps a request at 650000 bytes and a reply at 450000. We stay well
# under both so a batch never gets rejected with a bare 0xFF.
MAX_REQUEST = 650000
MAX_REPLY = 450000
DEFAULT_BATCH_WORDS = 8192  # 8192 * 9 bytes of reply = 73 KiB, comfortably safe


class PineError(RuntimeError):
    """Base class for PINE problems."""


class PineNotRunning(PineError):
    """Could not reach PCSX2 at all (not started, PINE disabled, wrong slot)."""


class PineCommandFailed(PineError):
    """PCSX2 answered, but with the failure code."""


class PineUnusable(PineError):
    """PCSX2 is reachable but there is no game running, or it is paused."""


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            raise PineNotRunning("PCSX2 closed the PINE connection mid-reply")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


@dataclass
class EmuInfo:
    """Everything PINE can tell us about the emulator and the loaded disc."""

    version: str = ""
    title: str = ""
    serial: str = ""
    uuid: str = ""
    disc_version: str = ""
    status: int = STATUS_SHUTDOWN

    @property
    def running(self) -> bool:
        return self.status == STATUS_RUNNING

    def __str__(self) -> str:  # pragma: no cover - display only
        state = {0: "running", 1: "paused", 2: "shutdown"}.get(self.status, "?")
        return f"{self.serial or '-'} / {self.title or '-'} [{self.uuid or '-'}] ({state}) via {self.version}"


class Pine:
    """A PINE connection.

    ``Pine(slot)`` or ``Pine("path/to/pcsx2.sock")``. Use as a context manager::

        with Pine() as pcsx2:
            pcsx2.write32(0x00123456, 0xDEADBEEF)
    """

    def __init__(
        self,
        slot: int | str = DEFAULT_SLOT,
        host: str = "127.0.0.1",
        timeout: float = 10.0,
        batch_words: int = DEFAULT_BATCH_WORDS,
    ) -> None:
        self.slot = slot
        self.host = host
        self.timeout = timeout
        self.batch_words = batch_words
        self._sock: socket.socket | None = None
        # Writes can land on a stale connection: remember what we have written so
        # the watchdog can tell "already applied" from "needs re-applying".
        self.writes = 0
        self.reads = 0

    # ---------------------------------------------------------------- transport

    def _addresses(self) -> list[tuple[str, object]]:
        """Candidate endpoints, in the order we try them."""
        if isinstance(self.slot, str):
            return [("unix", self.slot)]
        out: list[tuple[str, object]] = []
        if os.name != "nt":
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if runtime:
                out.append(("unix", os.path.join(runtime, "pcsx2.sock")))
            out.append(("unix", "/tmp/pcsx2.sock"))
            if self.slot != DEFAULT_SLOT:
                out.append(("unix", f"/tmp/pcsx2.sock.{self.slot}"))
        out.append(("tcp", (self.host, int(self.slot))))
        return out

    def connect(self) -> "Pine":
        if self._sock is not None:
            return self
        errors: list[str] = []
        for kind, target in self._addresses():
            sock = socket.socket(socket.AF_UNIX if kind == "unix" else socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(target)  # type: ignore[arg-type]
            except OSError as exc:
                sock.close()
                errors.append(f"{kind} {target}: {exc}")
                continue
            self._sock = sock
            return self
        raise PineNotRunning(
            "no PINE endpoint answered. Start PCSX2 with a game loaded, enable "
            "Settings -> Advanced -> Enable PINE IPC Server, and check the slot "
            f"({self.slot}). Tried: " + "; ".join(errors)
        )

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "Pine":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def reconnect(self) -> "Pine":
        """Drop the socket and open a fresh one (PCSX2 serves one client)."""
        self.close()
        return self.connect()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------ protocol

    def _roundtrip(self, commands: bytes) -> bytes:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        if len(commands) + 4 > MAX_REQUEST:
            raise ValueError(f"request of {len(commands) + 4} bytes exceeds PCSX2's {MAX_REQUEST} limit")
        try:
            self._sock.sendall(struct.pack("<I", len(commands) + 4) + commands)
            header = _recv_exactly(self._sock, 4)
            (size,) = struct.unpack("<I", header)
            if size < 5 or size > MAX_REPLY:
                self.close()
                raise PineError(f"PCSX2 announced an implausible reply size of {size}")
            rest = _recv_exactly(self._sock, size - 4)
        except (OSError, PineNotRunning):
            self.close()
            raise
        code = rest[0]
        if code != OK:
            # The emulator resets its receive buffer after a failure; a new
            # connection is the only reliable way back in sync.
            self.close()
            raise PineCommandFailed(
                "PCSX2 rejected the command batch (0xFF). Usual causes: no game "
                "running, the VM paused/shut down, or a reply that would exceed "
                "450000 bytes."
            )
        return rest[1:]

    @staticmethod
    def _cmd_read(size: int, addr: int) -> bytes:
        opcode = {1: MSG_READ8, 2: MSG_READ16, 4: MSG_READ32, 8: MSG_READ64}[size]
        return struct.pack("<BI", opcode, addr)

    @staticmethod
    def _cmd_write(size: int, addr: int, value: int) -> bytes:
        fmt = {1: "<BI B", 2: "<BIH", 4: "<BII", 8: "<BIQ"}[size]
        return struct.pack(fmt, {1: MSG_WRITE8, 2: MSG_WRITE16, 4: MSG_WRITE32, 8: MSG_WRITE64}[size], addr, value)

    # -------------------------------------------------------------------- basics

    def status(self) -> int:
        (value,) = struct.unpack("<I", self._roundtrip(bytes([MSG_STATUS])))
        return value

    def version(self) -> str:
        raw = self._roundtrip(bytes([MSG_VERSION]))
        (length,) = struct.unpack("<I", raw[:4])
        return _cstr(raw[4 : 4 + length])

    def title(self) -> str:
        return self._string(MSG_TITLE)

    def serial(self) -> str:
        return self._string(MSG_ID)

    def uuid(self) -> str:
        return self._string(MSG_UUID)

    def disc_version(self) -> str:
        return self._string(MSG_GAMEVERSION)

    def _string(self, opcode: int) -> str:
        raw = self._roundtrip(bytes([opcode]))
        (length,) = struct.unpack("<I", raw[:4])
        return _cstr(raw[4 : 4 + length])

    def info(self) -> EmuInfo:
        """One round trip per query, but tolerant: missing pieces become blanks."""
        info = EmuInfo()
        info.status = self.status()
        for field, fn in (
            ("version", self.version),
            ("title", self.title),
            ("serial", self.serial),
            ("uuid", self.uuid),
            ("disc_version", self.disc_version),
        ):
            try:
                setattr(info, field, fn())
            except (PineError, struct.error):
                pass
        return info

    def require_running(self) -> EmuInfo:
        info = self.info()
        if info.status == STATUS_SHUTDOWN:
            raise PineUnusable("PCSX2 has no game running")
        if info.status == STATUS_PAUSED:
            raise PineUnusable("PCSX2 is paused; the watchdog must not fight the pause screen")
        return info

    # --------------------------------------------------------------------- reads

    def read(self, addr: int, size: int = 4) -> int:
        if size not in (1, 2, 4, 8):
            raise ValueError("size must be 1, 2, 4 or 8")
        raw = self._roundtrip(self._cmd_read(size, addr))
        self.reads += 1
        return int.from_bytes(raw[:size], "little")

    def read8(self, addr: int) -> int:
        return self.read(addr, 1)

    def read16(self, addr: int) -> int:
        return self.read(addr, 2)

    def read32(self, addr: int) -> int:
        return self.read(addr, 4)

    def read64(self, addr: int) -> int:
        return self.read(addr, 8)

    def read_words(self, addrs: Sequence[int], size: int = 4) -> list[int]:
        """Read many same-width values in one round trip."""
        cmds = b"".join(self._cmd_read(size, a) for a in addrs)
        if not cmds:
            return []
        raw = self._roundtrip(cmds)
        self.reads += len(addrs)
        return [int.from_bytes(raw[i * size : (i + 1) * size], "little") for i in range(len(addrs))]

    def read_bytes(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes starting at ``addr``, batching 4-byte reads.

        Unaligned addresses are handled with narrow reads at the two edges.
        """
        if length <= 0:
            return b""
        out = bytearray()
        cur = addr
        end = addr + length
        if cur % 4:
            head = min(4 - (cur % 4), end - cur)
            out += self._read_narrow(cur, head)
            cur += head
        words = (end - cur) // 4
        while words:
            take = min(words, self.batch_words)
            addrs = [cur + 4 * i for i in range(take)]
            values = self.read_words(addrs, 4)
            out += b"".join(v.to_bytes(4, "little") for v in values)
            cur += take * 4
            words -= take
        if cur < end:
            out += self._read_narrow(cur, end - cur)
        return bytes(out[:length])

    def _read_narrow(self, addr: int, length: int) -> bytes:
        out = bytearray()
        for i in range(length):
            out.append(self.read8(addr + i))
        return bytes(out)

    # -------------------------------------------------------------------- writes

    def write(self, addr: int, value: int, size: int = 4) -> None:
        if size not in (1, 2, 4, 8):
            raise ValueError("size must be 1, 2, 4 or 8")
        self._roundtrip(self._cmd_write(size, addr, value))
        self.writes += 1

    def write8(self, addr: int, value: int) -> None:
        self.write(addr, value, 1)

    def write16(self, addr: int, value: int) -> None:
        self.write(addr, value, 2)

    def write32(self, addr: int, value: int) -> None:
        self.write(addr, value, 4)

    def write64(self, addr: int, value: int) -> None:
        self.write(addr, value, 8)

    def write_words(self, items: Iterable[tuple[int, int]], size: int = 4) -> None:
        items = list(items)
        cmds = b"".join(self._cmd_write(size, a, v) for a, v in items)
        if not cmds:
            return
        for start in range(0, len(cmds), MAX_REQUEST - 8):
            self._roundtrip(cmds[start : start + MAX_REQUEST - 8])
        self.writes += len(items)

    def write_bytes(self, addr: int, data: bytes) -> None:
        """Write bytes, batching aligned words and doing RMW on the edges.

        PS2 memory writes through PINE are raw memory writes, so an unaligned
        poke must not clobber the neighbouring bytes of the word it lands in.
        """
        if not data:
            return
        cur = addr
        pos = 0
        if cur % 4:
            head = min(4 - (cur % 4), len(data))
            for i in range(head):
                self.write8(cur + i, data[i])
            cur += head
            pos += head
        words = (len(data) - pos) // 4
        if words:
            items = []
            for i in range(words):
                off = pos + i * 4
                items.append((cur + i * 4, int.from_bytes(data[off : off + 4], "little")))
            self.write_words(items, 4)
            cur += words * 4
            pos += words * 4
        while pos < len(data):
            self.write8(cur, data[pos])
            cur += 1
            pos += 1

    # ------------------------------------------------------------- savestates

    def save_state(self, slot: int = 1) -> None:
        if not 0 <= slot <= 255:
            raise ValueError("savestate slot must be 0..255")
        self._roundtrip(bytes([MSG_SAVESTATE, slot]))

    def load_state(self, slot: int = 1) -> None:
        if not 0 <= slot <= 255:
            raise ValueError("savestate slot must be 0..255")
        self._roundtrip(bytes([MSG_LOADSTATE, slot]))


def wait_for_pine(slot: int | str = DEFAULT_SLOT, timeout: float = 60.0, poll: float = 1.0) -> Pine:
    """Block until PCSX2 answers, then hand back a live connection."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return Pine(slot).connect()
        except PineNotRunning as exc:
            last = exc
            time.sleep(poll)
    raise PineNotRunning(f"PINE never came up within {timeout:.0f}s: {last}")
