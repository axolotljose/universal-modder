"""A tiny R5900 interpreter, used to prove the cave behaves before it meets a PS2.

We cannot run PlayStation 2 code in CI, and "the assembler looks right" is not
evidence. So the interpreter the cave uses is small enough to execute here, in a
plain Python VM that models the register file, memory and branch delay slots of
the instruction subset the cave is built from.

That turns the most dangerous part of the mod into something testable:

* the cave must write every entry the parameter block lists;
* it must call the function it displaced, exactly once, with the caller's ``$ra``
  still intact;
* it must return to ``site + 8`` -- the instruction after the original call's
  delay slot -- and not somewhere approximate;
* it must leave ``$sp`` exactly where it found it, and only clobber caller-saved
  registers.

The VM is deliberately strict: an instruction it does not model raises instead of
being skipped, so a cave that starts using something new fails loudly here rather
than quietly on someone's console.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

RAM_SIZE = 0x02000000  # 32 MB of EE main RAM, addressed physically
REG_NAMES = [
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
    "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra",
]
REG = {name: i for i, name in enumerate(REG_NAMES)}
REG["s8"] = 30
SP, RA = REG["sp"], REG["ra"]


class VMError(RuntimeError):
    pass


@dataclass
class Call:
    callee: int
    ra: int


@dataclass
class RunResult:
    steps: int = 0
    calls: list[Call] = field(default_factory=list)
    halted: str = ""
    pc: int = 0


class R5900VM:
    """Enough R5900 to run a code cave. Not a PS2 emulator."""

    def __init__(self, ram_size: int = RAM_SIZE) -> None:
        self.ram = bytearray(ram_size)
        self.regs = [0] * 32
        self.pc = 0
        self.calls: list[Call] = []
        self.steps = 0
        self.trace: list[str] = []
        self.trace_limit = 4096

    # ------------------------------------------------------------------ memory

    def read32(self, addr: int) -> int:
        addr &= 0x1FFFFFFF
        if addr + 4 > len(self.ram):
            raise VMError(f"read outside RAM at {addr:#x}")
        return struct.unpack_from("<I", self.ram, addr)[0]

    def write32(self, addr: int, value: int) -> None:
        addr &= 0x1FFFFFFF
        if addr + 4 > len(self.ram):
            raise VMError(f"write outside RAM at {addr:#x}")
        struct.pack_into("<I", self.ram, addr, value & 0xFFFFFFFF)

    def read_bytes(self, addr: int, length: int) -> bytes:
        addr &= 0x1FFFFFFF
        return bytes(self.ram[addr : addr + length])

    def write_bytes(self, addr: int, data: bytes) -> None:
        addr &= 0x1FFFFFFF
        self.ram[addr : addr + len(data)] = data

    # ------------------------------------------------------------------ running

    def set_reg(self, name: str | int, value: int) -> None:
        self.regs[REG[name] if isinstance(name, str) else name] = value & 0xFFFFFFFF

    def get_reg(self, name: str | int) -> int:
        return self.regs[REG[name] if isinstance(name, str) else name]

    def run(
        self,
        start: int,
        stop_at: int | None = None,
        external_calls: tuple[int, ...] = (),
        max_steps: int = 100_000,
    ) -> RunResult:
        """Run until ``stop_at`` or a step limit.

        Addresses in ``external_calls`` behave like a call into the game's own
        function: the VM records the call and returns immediately via ``$ra``,
        which is exactly what the cave has to survive.
        """
        self.pc = start
        result = RunResult()
        externals = set(external_calls)
        while True:
            if stop_at is not None and self.pc == stop_at:
                result.halted = "stop-at"
                result.pc = self.pc
                result.steps = self.steps
                result.calls = list(self.calls)
                return result
            if self.steps >= max_steps:
                raise VMError(f"ran {max_steps} steps without stopping (pc={self.pc:#x}); likely a runaway loop")
            if self.pc in externals:
                self.calls.append(Call(self.pc, self.regs[RA]))
                if len(self.trace) < self.trace_limit:
                    self.trace.append(f"call {self.pc:#010x} ra={self.regs[RA]:#010x}")
                self.pc = self.regs[RA]
                continue
            self.step()

    def step(self) -> None:
        word = self.read32(self.pc)
        pc = self.pc
        next_pc = pc + 4
        delay = False
        self.steps += 1
        op = (word >> 26) & 0x3F
        rs, rt, rd = (word >> 21) & 0x1F, (word >> 16) & 0x1F, (word >> 11) & 0x1F
        sa, funct = (word >> 6) & 0x1F, word & 0x3F
        imm = word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm

        def signed(v: int) -> int:
            return v - 0x100000000 if v & 0x80000000 else v

        if word == 0:
            self.pc = next_pc
            return
        if op == 0:
            if funct == 0x00:
                self._set(rd, (self.regs[rt] << sa) & 0xFFFFFFFF)
            elif funct == 0x02:
                self._set(rd, self.regs[rt] >> sa)
            elif funct == 0x03:
                self._set(rd, (signed(self.regs[rt]) >> sa) & 0xFFFFFFFF)
            elif funct == 0x04:
                self._set(rd, (self.regs[rt] << (self.regs[rs] & 31)) & 0xFFFFFFFF)
            elif funct == 0x06:
                self._set(rd, self.regs[rt] >> (self.regs[rs] & 31))
            elif funct == 0x08:
                next_pc = self.regs[rs]
                delay = True
            elif funct == 0x09:
                target = self.regs[rs]
                self._set(rd, pc + 8)
                next_pc = target
                delay = True
            elif funct == 0x20:
                self._set(rd, (signed(self.regs[rs]) + signed(self.regs[rt])) & 0xFFFFFFFF)
            elif funct == 0x21:
                self._set(rd, (self.regs[rs] + self.regs[rt]) & 0xFFFFFFFF)
            elif funct == 0x23:
                self._set(rd, (self.regs[rs] - self.regs[rt]) & 0xFFFFFFFF)
            elif funct == 0x24:
                self._set(rd, self.regs[rs] & self.regs[rt])
            elif funct == 0x25:
                self._set(rd, self.regs[rs] | self.regs[rt])
            elif funct == 0x26:
                self._set(rd, self.regs[rs] ^ self.regs[rt])
            elif funct == 0x27:
                self._set(rd, ~(self.regs[rs] | self.regs[rt]) & 0xFFFFFFFF)
            elif funct == 0x2A:
                self._set(rd, 1 if signed(self.regs[rs]) < signed(self.regs[rt]) else 0)
            elif funct == 0x2B:
                self._set(rd, 1 if self.regs[rs] < self.regs[rt] else 0)
            else:
                raise VMError(f"unmodelled special funct {funct:#x} at {pc:#x}")
        elif op in (0x02, 0x03):  # j / jal
            target = ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
            if op == 0x03:
                self._set(RA, pc + 8)
            next_pc = target
            delay = True
        elif op == 0x04:
            if self.regs[rs] == self.regs[rt]:
                next_pc = pc + 4 + simm * 4
                delay = True
        elif op == 0x05:
            if self.regs[rs] != self.regs[rt]:
                next_pc = pc + 4 + simm * 4
                delay = True
        elif op == 0x06:
            if signed(self.regs[rs]) <= 0:
                next_pc = pc + 4 + simm * 4
                delay = True
        elif op == 0x07:
            if signed(self.regs[rs]) > 0:
                next_pc = pc + 4 + simm * 4
                delay = True
        elif op == 0x01:  # regimm
            take = (rt == 0x00 and signed(self.regs[rs]) < 0) or (rt == 0x01 and signed(self.regs[rs]) >= 0)
            if take:
                next_pc = pc + 4 + simm * 4
                delay = True
        elif op == 0x08:
            self._set(rt, (signed(self.regs[rs]) + simm) & 0xFFFFFFFF)
        elif op == 0x09:
            self._set(rt, (self.regs[rs] + simm) & 0xFFFFFFFF)
        elif op == 0x0A:
            self._set(rt, 1 if signed(self.regs[rs]) < simm else 0)
        elif op == 0x0C:
            self._set(rt, self.regs[rs] & imm)
        elif op == 0x0D:
            self._set(rt, self.regs[rs] | imm)
        elif op == 0x0E:
            self._set(rt, self.regs[rs] ^ imm)
        elif op == 0x0F:
            self._set(rt, (imm << 16) & 0xFFFFFFFF)
        elif op in (0x20, 0x21, 0x23, 0x24, 0x25):
            addr = (self.regs[rs] + simm) & 0xFFFFFFFF
            if op == 0x20:
                self._set(rt, signed(self._read(addr, 1)))
            elif op == 0x21:
                self._set(rt, signed(self._read(addr, 2)))
            elif op == 0x23:
                self._set(rt, self.read32(addr))
            elif op == 0x24:
                self._set(rt, self._read(addr, 1))
            else:
                self._set(rt, self._read(addr, 2))
        elif op in (0x28, 0x29, 0x2B):
            addr = (self.regs[rs] + simm) & 0xFFFFFFFF
            size = {0x28: 1, 0x29: 2, 0x2B: 4}[op]
            self._write(addr, self.regs[rt], size)
        else:
            raise VMError(f"unmodelled opcode {op:#x} at {pc:#x} ({word:08x})")

        if delay:
            # Execute the delay slot before taking the branch, like the real CPU.
            self.pc = pc + 4
            self.step()
            self.pc = next_pc & 0xFFFFFFFF
        else:
            self.pc = next_pc & 0xFFFFFFFF
        self.regs[0] = 0

    # ------------------------------------------------------------------ helpers

    def _set(self, index: int, value: int) -> None:
        if index:
            self.regs[index] = value & 0xFFFFFFFF

    def _read(self, addr: int, size: int) -> int:
        addr &= 0x1FFFFFFF
        if addr + size > len(self.ram):
            raise VMError(f"read outside RAM at {addr:#x}")
        return int.from_bytes(self.ram[addr : addr + size], "little")

    def _write(self, addr: int, value: int, size: int) -> None:
        addr &= 0x1FFFFFFF
        if addr + size > len(self.ram):
            raise VMError(f"write outside RAM at {addr:#x}")
        self.ram[addr : addr + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")
