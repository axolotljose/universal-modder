"""A small MIPS III / R5900 encoder, just big enough to write PS2 code caves.

A code cave is a block of unused bytes in the game's memory that we fill with our
own machine code, reached by overwriting an instruction in the game's own code
with a jump. Everything here is integer-only: no FPU, no VU, no branch-likely
peculiarities beyond what a cave needs. The encoders are checked against known
instruction words in the test suite, which is the only reason we trust bytes we
cannot execute here.

Register aliases follow the o32 ABI names, because that is what PS2 code is
written in: $zero, $at, $v0-$v1, $a0-$a3, $t0-$t9, $s0-$s7, $k0-$k1, $gp, $sp,
$fp, $ra.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

REGISTERS = {
    "zero": 0, "at": 1, "v0": 2, "v1": 3, "a0": 4, "a1": 5, "a2": 6, "a3": 7,
    "t0": 8, "t1": 9, "t2": 10, "t3": 11, "t4": 12, "t5": 13, "t6": 14, "t7": 15,
    "s0": 16, "s1": 17, "s2": 18, "s3": 19, "s4": 20, "s5": 21, "s6": 22, "s7": 23,
    "t8": 24, "t9": 25, "k0": 26, "k1": 27, "gp": 28, "sp": 29, "fp": 30, "ra": 31,
    "s8": 30,
}

FUNCT = {
    "sll": 0x00, "srl": 0x02, "sra": 0x03, "sllv": 0x04, "srlv": 0x06, "srav": 0x07,
    "jr": 0x08, "jalr": 0x09, "syscall": 0x0C, "break": 0x0D,
    "mfhi": 0x10, "mflo": 0x12, "mult": 0x18, "multu": 0x19, "div": 0x1A, "divu": 0x1B,
    "add": 0x20, "addu": 0x21, "sub": 0x22, "subu": 0x23,
    "and": 0x24, "or": 0x25, "xor": 0x26, "nor": 0x27,
    "slt": 0x2A, "sltu": 0x2B,
}

OPCODE = {
    "j": 0x02, "jal": 0x03, "beq": 0x04, "bne": 0x05, "blez": 0x06, "bgtz": 0x07,
    "addi": 0x08, "addiu": 0x09, "slti": 0x0A, "sltiu": 0x0B,
    "andi": 0x0C, "ori": 0x0D, "xori": 0x0E, "lui": 0x0F,
    "lb": 0x20, "lh": 0x21, "lwl": 0x22, "lw": 0x23, "lbu": 0x24, "lhu": 0x25, "lwr": 0x26,
    "sb": 0x28, "sh": 0x29, "swl": 0x2A, "sw": 0x2B, "swr": 0x2E,
}


def reg(name: str | int) -> int:
    if isinstance(name, int):
        if not 0 <= name <= 31:
            raise ValueError(f"register number out of range: {name}")
        return name
    key = name.lstrip("$").lower()
    if key in REGISTERS:
        return REGISTERS[key]
    raise ValueError(f"unknown register {name!r}")


def _signed(value: int, bits: int, what: str) -> int:
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not lo <= value <= hi:
        raise ValueError(f"{what} {value} does not fit in {bits} signed bits")
    return value & ((1 << bits) - 1)


def _unsigned(value: int, bits: int, what: str) -> int:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{what} {value} does not fit in {bits} unsigned bits")
    return value


# --------------------------------------------------------------------- R-type


def rtype(rs: str | int, rt: str | int, rd: str | int, sa: int, funct: int) -> int:
    return (
        (reg(rs) << 21) | (reg(rt) << 16) | (reg(rd) << 11) | (_unsigned(sa, 5, "shift") << 6) | funct
    )


def addu(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["addu"])


def subu(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["subu"])


def sll(rd, rt, sa):
    return rtype(0, rt, rd, sa, FUNCT["sll"])


def srl(rd, rt, sa):
    return rtype(0, rt, rd, sa, FUNCT["srl"])


def sra(rd, rt, sa):
    return rtype(0, rt, rd, sa, FUNCT["sra"])


def sllv(rd, rt, rs):
    return rtype(rs, rt, rd, 0, FUNCT["sllv"])


def sltu(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["sltu"])


def slt(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["slt"])


def and_(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["and"])


def or_(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["or"])


def xor_(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["xor"])


def nor(rd, rs, rt):
    return rtype(rs, rt, rd, 0, FUNCT["nor"])


def mflo(rd):
    return rtype(0, 0, rd, 0, FUNCT["mflo"])


def mfhi(rd):
    return rtype(0, 0, rd, 0, FUNCT["mfhi"])


def mult(rs, rt):
    return rtype(rs, rt, 0, 0, FUNCT["mult"])


def multu(rs, rt):
    return rtype(rs, rt, 0, 0, FUNCT["multu"])


def jr(rs):
    return rtype(rs, 0, 0, 0, FUNCT["jr"])


def jalr(rd, rs):
    return rtype(rs, 0, rd, 0, FUNCT["jalr"])


# --------------------------------------------------------------------- I-type


def itype(op: int, rs, rt, imm: int) -> int:
    return (op << 26) | (reg(rs) << 21) | (reg(rt) << 16) | (imm & 0xFFFF)


def addiu(rt, rs, imm):
    return itype(OPCODE["addiu"], rs, rt, _signed(imm, 16, "addiu immediate"))


def addi(rt, rs, imm):
    return itype(OPCODE["addi"], rs, rt, _signed(imm, 16, "addi immediate"))


def slti(rt, rs, imm):
    return itype(OPCODE["slti"], rs, rt, _signed(imm, 16, "slti immediate"))


def sltiu(rt, rs, imm):
    return itype(OPCODE["sltiu"], rs, rt, _signed(imm, 16, "sltiu immediate"))


def andi(rt, rs, imm):
    return itype(OPCODE["andi"], rs, rt, _unsigned(imm, 16, "andi immediate"))


def ori(rt, rs, imm):
    return itype(OPCODE["ori"], rs, rt, _unsigned(imm, 16, "ori immediate"))


def xori(rt, rs, imm):
    return itype(OPCODE["xori"], rs, rt, _unsigned(imm, 16, "xori immediate"))


def beq(rs, rt, offset16: int) -> int:
    return itype(OPCODE["beq"], rs, rt, offset16)


def bne(rs, rt, offset16: int) -> int:
    return itype(OPCODE["bne"], rs, rt, offset16)


def blez(rs, offset16: int) -> int:
    return itype(OPCODE["blez"], rs, 0, offset16)


def bgtz(rs, offset16: int) -> int:
    return itype(OPCODE["bgtz"], rs, 0, offset16)


def lui(rt, imm):
    return itype(OPCODE["lui"], 0, rt, _unsigned(imm & 0xFFFF, 16, "lui immediate"))


def _mem(op: str, rt, offset: int, base):
    return itype(OPCODE[op], base, rt, _signed(offset, 16, f"{op} offset"))


def lw(rt, offset, base):
    return _mem("lw", rt, offset, base)


def lh(rt, offset, base):
    return _mem("lh", rt, offset, base)


def lhu(rt, offset, base):
    return _mem("lhu", rt, offset, base)


def lb(rt, offset, base):
    return _mem("lb", rt, offset, base)


def lbu(rt, offset, base):
    return _mem("lbu", rt, offset, base)


def sw(rt, offset, base):
    return _mem("sw", rt, offset, base)


def sh(rt, offset, base):
    return _mem("sh", rt, offset, base)


def sb(rt, offset, base):
    return _mem("sb", rt, offset, base)


# --------------------------------------------------------------------- J-type


def j(target: int) -> int:
    if target & 3:
        raise ValueError("jump target must be word aligned")
    return (OPCODE["j"] << 26) | ((target >> 2) & 0x03FFFFFF)


def jal(target: int) -> int:
    if target & 3:
        raise ValueError("jump target must be word aligned")
    return (OPCODE["jal"] << 26) | ((target >> 2) & 0x03FFFFFF)


REGIMM_OP = 0x01
REGIMM_BLTZ = 0
REGIMM_BGEZ = 1


def bltz(rs, offset16: int) -> int:
    return itype(REGIMM_OP, rs, REGIMM_BLTZ, offset16)


def bgez(rs, offset16: int) -> int:
    return itype(REGIMM_OP, rs, REGIMM_BGEZ, offset16)


NOP = 0
JALR_RA_T9 = jalr("ra", "t9")


def to_bytes(words: list[int]) -> bytes:
    return b"".join(struct.pack("<I", w & 0xFFFFFFFF) for w in words)


def load_immediate(rt, value: int) -> list[int]:
    """``li``: one instruction when the value fits, otherwise lui+ori."""
    value &= 0xFFFFFFFF
    signed = value - (1 << 32) if value >= (1 << 31) else value
    if -0x8000 <= signed <= 0x7FFF:
        if signed < 0:
            return [addiu(rt, "zero", signed)]
        return [ori(rt, "zero", value)]
    return [lui(rt, value >> 16), ori(rt, rt, value & 0xFFFF)]


def la(rt, address: int) -> list[int]:
    """``la`` for an absolute address (always lui+ori, never a signed addiu)."""
    address &= 0xFFFFFFFF
    return [lui(rt, address >> 16), ori(rt, rt, address & 0xFFFF)]


# ------------------------------------------------------------------ assembling


@dataclass
class Program:
    """An assembler with labels, so a cave can be written top-down.

    >>> p = Program(0x00140000)
    >>> p.label("cave")
    >>> p.emit(load_immediate("t0", 2))
    >>> p.b("cave")
    >>> p.finish()
    """

    base: int = 0
    words: list[int] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    _fixups: list[tuple[int, str, int]] = field(default_factory=list)  # (index, label, kind)

    # -- emission ----------------------------------------------------------

    def emit(self, *words: int | list[int]) -> "Program":
        for w in words:
            if isinstance(w, list):
                self.words.extend(x & 0xFFFFFFFF for x in w)
            else:
                self.words.append(w & 0xFFFFFFFF)
        return self

    def emit_bytes(self, data: bytes) -> "Program":
        if len(data) % 4:
            raise ValueError("code cave payload must be word sized")
        for i in range(0, len(data), 4):
            self.words.append(struct.unpack_from("<I", data, i)[0])
        return self

    def nop(self, count: int = 1) -> "Program":
        return self.emit(*([NOP] * count))

    def label(self, name: str) -> "Program":
        if name in self.labels:
            raise ValueError(f"duplicate label {name!r}")
        self.labels[name] = len(self.words)
        return self

    # -- branches and jumps ------------------------------------------------

    def _branch(self, op: str, rs, rt, label: str) -> "Program":
        self._fixups.append((len(self.words), label, f"branch:{op}"))
        self.words.append(itype(OPCODE[op], rs, rt, 0))
        return self

    def _regimm(self, rt_bits: int, rs, label: str) -> "Program":
        self._fixups.append((len(self.words), label, "branch:regimm"))
        self.words.append(itype(REGIMM_OP, rs, rt_bits, 0))
        return self

    def beq(self, rs, rt, label):
        return self._branch("beq", rs, rt, label)

    def bne(self, rs, rt, label):
        return self._branch("bne", rs, rt, label)

    def bgtz(self, rs, label):
        return self._branch("bgtz", rs, "zero", label)

    def blez(self, rs, label):
        return self._branch("blez", rs, "zero", label)

    def b(self, label):
        return self.beq("zero", "zero", label)

    def bltz(self, rs, label):
        """Branch if ``rs`` is negative -- how the cave spots an indirect entry."""
        return self._regimm(REGIMM_BLTZ, rs, label)

    def j(self, label: str) -> "Program":
        """Jump to a label. Placeholder is patched with the real address."""
        self._fixups.append((len(self.words), label, "j"))
        self.words.append(0)
        return self

    def jump_out(self, dest: int) -> "Program":
        """Unconditional jump to an absolute in-game address, plus its delay slot."""
        return self.emit(j(dest & 0xFFFFFFFF), NOP)

    def finish(self) -> bytes:
        """Resolve fixups and return the cave's bytes."""
        for index, label, kind in self._fixups:
            if label not in self.labels:
                raise ValueError(f"undefined label {label!r}")
            target = self.base + self.labels[label] * 4
            if kind == "j":
                self.words[index] = j(target)
                continue
            op = kind.split(":", 1)[1]
            delta = self.labels[label] - index - 1  # relative to the delay slot
            imm = _signed(delta, 16, f"{op} displacement")
            self.words[index] = (self.words[index] & 0xFFFF0000) | imm
        return to_bytes(self.words)

    def __len__(self) -> int:
        return len(self.words)

    @property
    def end(self) -> int:
        return self.base + len(self.words) * 4


def trampoline(site: int, cave: int, original: bytes) -> bytes:
    """Instructions that replace an in-game instruction and reach the cave.

    MIPS has no "jump and return": the cave has to jump back itself. So we save
    the instruction we are stealing, jump, and let the cave re-run it before
    returning to ``site + 8``. ``original`` is the 4 bytes being replaced.
    """
    if len(original) != 4:
        raise ValueError("exactly one instruction can be displaced")
    if site % 4 or cave % 4:
        raise ValueError("both the patch site and the cave must be word aligned")
    if abs(cave - site) > 0x0FFFFFFC:
        raise ValueError("cave is too far for a j/jal immediate (256 MB region)")
    # j cave / nop: the delay slot is free, and the cave finishes with the
    # displaced instruction, then j site+8 / nop.
    return to_bytes([j(cave), NOP]) + original


def cave_return(cave_end: int, original: bytes, resume: int) -> bytes:
    """The tail of a cave: the displaced instruction, then back to the caller."""
    return original + to_bytes([j(resume), NOP])


# ------------------------------------------------------------------ disassembly

_RTYPE_NAMES = {v: k for k, v in FUNCT.items()}
_ITYPE_NAMES = {v: k for k, v in OPCODE.items()}
_REG_NAMES = {v: k for k, v in REGISTERS.items() if k != "s8"}


def _rname(n: int) -> str:
    return "$" + _REG_NAMES.get(n, str(n))


def disassemble(word: int, pc: int = 0) -> str:
    """One readable line for one instruction word.

    Only used for looking at code; PINE has no disassembler of its own. Anything
    outside the subset this module encodes is shown in a generic form rather than
    guessed at, so a listing never lies about what it does not know.
    """
    op = (word >> 26) & 0x3F
    rs = (word >> 21) & 0x1F
    rt = (word >> 16) & 0x1F
    rd = (word >> 11) & 0x1F
    sa = (word >> 6) & 0x1F
    funct = word & 0x3F
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm

    if op == 0:
        if funct == 0 and word == 0:
            return "nop"
        name = _RTYPE_NAMES.get(funct)
        if name in ("sll", "srl", "sra"):
            return f"{name} {_rname(rd)}, {_rname(rt)}, {sa}"
        if name in ("sllv", "srlv", "srav"):
            return f"{name} {_rname(rd)}, {_rname(rt)}, {_rname(rs)}"
        if name == "jr":
            return f"jr {_rname(rs)}"
        if name == "jalr":
            return f"jalr {_rname(rd)}, {_rname(rs)}"
        if name in ("mult", "multu", "div", "divu"):
            return f"{name} {_rname(rs)}, {_rname(rt)}"
        if name in ("mfhi", "mflo"):
            return f"{name} {_rname(rd)}"
        if name:
            return f"{name} {_rname(rd)}, {_rname(rs)}, {_rname(rt)}"
        return f".word {word:08x}  (special funct={funct:#x})"
    if op in (0x02, 0x03):
        target = ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        return f"{'jal' if op == 0x03 else 'j'} {target:#010x}"
    if op == REGIMM_OP:
        name = {REGIMM_BLTZ: "bltz", REGIMM_BGEZ: "bgez", 0x10: "bltzal", 0x11: "bgezal"}.get(rt, f"regimm{rt}")
        return f"{name} {_rname(rs)}, {(pc + 4 + simm * 4):#010x}"
    if op in (0x04, 0x05, 0x06, 0x07):
        name = _ITYPE_NAMES[op]
        delta = simm * 4
        dest = pc + 4 + delta
        if op in (0x04, 0x05):
            return f"{name} {_rname(rs)}, {_rname(rt)}, {dest:#010x}"
        return f"{name} {_rname(rs)}, {dest:#010x}"
    name = _ITYPE_NAMES.get(op)
    if name:
        if op == 0x0F:
            return f"lui {_rname(rt)}, {imm:#06x}"
        if name in ("lb", "lh", "lwl", "lw", "lbu", "lhu", "lwr", "sb", "sh", "swl", "sw", "swr"):
            return f"{name} {_rname(rt)}, {simm}({_rname(rs)})"
        if name in ("andi", "ori", "xori"):
            return f"{name} {_rname(rt)}, {_rname(rs)}, {imm:#06x}"
        return f"{name} {_rname(rt)}, {_rname(rs)}, {simm}"
    return f".word {word:08x}  (op={op:#x})"
