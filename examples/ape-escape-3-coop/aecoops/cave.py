"""Code caves: the mod's in-game half.

PINE can read and write memory, but it cannot run *every frame* -- a Python round
trip is a millisecond, a PS2 frame is 16.7 ms, and the game rewrites its own
frame state long before we get back. Anything that must hold (view mode, actor
count, which character an actor wears) has to be re-applied from inside the game.

So the mod installs one small routine into unused RAM and redirects a per-frame
engine call into it:

    site      : jal CALLEE     ->   jal CAVE      (one instruction, same slot)
    site+4    : delay slot          untouched
    site+8    : resumes as before

The cave saves ``$ra``, runs a ~20 instruction interpreter that applies a list of
(address, value) pairs, calls the original callee, restores ``$ra`` and jumps
back to ``site+8``. Semantics are identical to the original call, which is the
whole point: the game never notices.

The interpreter makes the mod a tiny coprocessor. All the *policy* (which
character, which split, when to spawn) stays in Python, which just refills the
parameter block. Want new behaviour? Change the Python, not the machine code.

    cave layout
    +0x000  interpreter, at most CAVE_CODE_SIZE bytes
    +0x1C0  parameter block for the interpreter
            +0x00  magic        u32  'AE3C'
            +0x04  frame tick   u32  incremented in-game, read as a liveness proof
            +0x08  max entries  u32
            +0x0C  applied      u32  how many entries ran on the last tick
            +0x10  writes[]     (addr u32, value u32) * MAX_ENTRIES, 0-terminated
            +0x150 calls[]      (function u32, argument u32) * MAX_CALLS, consumed

An entry's value word is either a small constant, or -- when bit 31 is set -- the
address of a word to copy from. That one flag is what lets a rule like "player
two stands where player one stands" run entirely inside the game, at frame rate,
with no host process involved. See ``copy()`` and ``const()``.

The second list is for the cases where a write is not enough. Some state cannot be
set by storing a word: a second actor may have to be *constructed*, a view mode may
live behind a function that also rebuilds the viewport rectangles, a pad may only
bind through a setter. PINE cannot call a function; the cave can. A call entry is
``(address of a function, its single $a0 argument)``, and the argument may itself be
indirect (bit 31 set: read the word at that address). The cave **consumes** a call
entry -- it zeroes the slot after calling -- so a refilled list fires each call
once per refill rather than once per frame, which is the difference between
spawning a partner and spawning sixty of them a second. See ``call()``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import mips
from .mips import NOP, Program, load_immediate

MAGIC = b"AE3C"
CAVE_CODE_SIZE = 0x1C0
# The block is one allocation inside the free-RAM search, so its size costs nothing
# but bytes of RAM nobody was using -- and running out of *slots* is a much worse
# failure than using 0x200 of a 32 MB console.
#
# 40 write slots: the mandatory writes (split, actor count, partner flag, character
# ids, pad ports, actives) are about ten, and the optional sets -- the tether, the
# per-view HUD, the viewport rectangles, the camera targets -- have been measured at
# 35 in a fully configured install.
#
# 16 call slots, at a fixed offset so both loops have a bound they can check without
# a counter in a register the called function is free to trash.
PARAM_BLOCK_SIZE = 0x200
ENTRIES_OFFSET = 0x10
MAX_ENTRIES = 40
# The entry list is zero *terminated*, so a full list of 40 entries occupies 41
# slots. The call region starts after that terminator, not on top of it: a call
# slot that shares eight bytes with the entry terminator is a call that is silently
# wiped by the frame that fills the entry budget.
CALLS_OFFSET = ENTRIES_OFFSET + 8 * (MAX_ENTRIES + 1)  # 0x158
MAX_CALLS = 16
CALLS_SIZE = 8 * MAX_CALLS
CALLS_END = CALLS_OFFSET + CALLS_SIZE                  # 0x1D8

TICKS = "ticks"


def param_block_at(cave: int) -> int:
    return cave + CAVE_CODE_SIZE


@dataclass
class Interpreter:
    """The cave's code, assembled for one site/cave/callee triple."""

    cave: int
    site: int
    callee: int
    code: bytes = b""

    @property
    def resume(self) -> int:
        return self.site + 8

    @property
    def param_block(self) -> int:
        return param_block_at(self.cave)

    @property
    def trampoline(self) -> bytes:
        """The single instruction that replaces ``jal CALLEE`` at ``site``.

        It is a ``jal``, not a ``j``, so ``$ra`` ends up holding ``site + 8`` --
        exactly what the displaced call would have left there. The cave saves and
        restores it around its own call, so the interrupted function sees the same
        link register it always did.
        """
        return mips.to_bytes([mips.jal(self.cave)])

    def total_size(self) -> int:
        return CAVE_CODE_SIZE + PARAM_BLOCK_SIZE


def callee_of_jal(word: int, site: int) -> int:
    """The target of a MIPS ``jal``: top 4 bits of the delay slot PC, plus the imm."""
    if (word >> 26) != 0x03:
        raise ValueError(f"instruction {word:#010x} at {site:#x} is not a jal")
    return ((site + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)


def build_interpreter(
    cave: int,
    site: int,
    callee: int,
    param_block: int | None = None,
    max_entries: int = MAX_ENTRIES,
) -> Interpreter:
    """Assemble the per-frame interpreter.

    Clobbers only ``$at`` and ``$t0..$t6`` (every one of them caller-saved, so the
    interrupted function cannot care) plus nine words of stack: ``$ra``, the four
    argument registers, the block base and the call cursor.
    """
    if cave % 4 or site % 4 or callee % 4:
        raise ValueError("cave, site and callee addresses must all be word aligned")
    par = param_block if param_block is not None else param_block_at(cave)

    p = Program(base=cave)
    p.label("cave")

    # --- prologue: keep what the interrupted function expects -------------------
    # $ra has to survive our own call to the callee, and $a0-$a3 have to survive
    # every call *we* make on the mod's behalf: they are the arguments the game
    # passed to the function we displaced, and that function is still expecting
    # them when we finally call it.
    p.emit(mips.addiu("sp", "sp", -0x30))
    p.emit(mips.sw("ra", 0x14, "sp"))
    p.emit(mips.sw("a0", 0x18, "sp"))
    p.emit(mips.sw("a1", 0x1C, "sp"))
    p.emit(mips.sw("a2", 0x20, "sp"))
    p.emit(mips.sw("a3", 0x24, "sp"))

    # --- load the parameter block base into $t0 -------------------------------
    p.emit(load_immediate("t0", par))
    p.emit(mips.sw("t0", 0x10, "sp"))        # a called function may trashed $t0

    # --- tick the frame counter (also our liveness oracle) --------------------
    p.emit(mips.lw("t1", 0x04, "t0"))
    p.emit(mips.addiu("t1", "t1", 1))
    p.emit(mips.sw("t1", 0x04, "t0"))

    # --- entry list ----------------------------------------------------------
    p.emit(mips.lw("t5", 0x08, "t0"))       # $t5 = remaining budget
    p.emit(mips.addiu("t2", "t0", ENTRIES_OFFSET))
    p.emit(mips.sw("zero", 0x0C, "t0"))     # applied = 0

    p.label("loop")
    p.blez("t5", "done_loop")               # budget exhausted
    p.emit(NOP)
    p.emit(mips.lw("t3", 0x00, "t2"))       # target address
    p.emit(mips.lw("t4", 0x04, "t2"))       # value, or a source address if negative
    p.beq("t3", "zero", "done_loop")
    p.emit(NOP)
    p.bltz("t4", "indirect")
    p.emit(NOP)
    p.emit(mips.sw("t4", 0x00, "t3"))       # constant store
    p.b("advance")
    p.emit(NOP)

    # Indirect entry: the value word had bit 31 set, so its low 31 bits are an
    # address to read from. This is what lets the mod copy one actor's position
    # onto another every frame without any host process running.
    p.label("indirect")
    p.emit(mips.sll("t4", "t4", 1))
    p.emit(mips.srl("t4", "t4", 1))
    p.emit(mips.lw("t4", 0x00, "t4"))
    p.emit(mips.sw("t4", 0x00, "t3"))

    p.label("advance")
    p.emit(mips.lw("t1", 0x0C, "t0"))
    p.emit(mips.addiu("t1", "t1", 1))
    p.emit(mips.sw("t1", 0x0C, "t0"))
    p.emit(mips.addiu("t2", "t2", 8))
    p.emit(mips.addiu("t5", "t5", -1))
    p.b("loop")
    p.emit(NOP)

    p.label("done_loop")

    # --- the call list --------------------------------------------------------
    # Everything here has to survive an arbitrary function call, so the cursor
    # lives on the stack and the bounds are recomputed from the block base after
    # every call. A called function is free to trash every $t register, and one of
    # them will.
    p.emit(mips.addiu("t2", "t0", CALLS_OFFSET))
    p.emit(mips.sw("t2", 0x28, "sp"))

    p.label("call_loop")
    p.emit(mips.lw("t0", 0x10, "sp"))       # base, again
    p.emit(mips.lw("t2", 0x28, "sp"))       # cursor
    p.emit(mips.addiu("t1", "t0", CALLS_END))
    p.emit(mips.sltu("t6", "t2", "t1"))     # inside the call region?
    p.beq("t6", "zero", "call_done")
    p.emit(NOP)
    p.emit(mips.lw("t3", 0x00, "t2"))       # the function
    p.emit(mips.lw("t4", 0x04, "t2"))       # its argument (bit 31 = read it)
    p.emit(mips.addiu("t1", "t2", 8))
    p.emit(mips.sw("t1", 0x28, "sp"))       # advance now: every path below re-enters
    p.beq("t3", "zero", "call_loop")        # an empty slot is skipped, not a terminator
    p.emit(NOP)
    p.emit(mips.sw("zero", 0x00, "t2"))     # consume: one call per refill, not per frame
    p.bltz("t4", "call_indirect")
    p.emit(NOP)
    p.emit(mips.or_("a0", "zero", "t4"))
    p.b("call_invoke")
    p.emit(NOP)

    p.label("call_indirect")
    p.emit(mips.sll("t4", "t4", 1))
    p.emit(mips.srl("t4", "t4", 1))
    p.emit(mips.lw("a0", 0x00, "t4"))       # pass a live value: an actor slot, a handle

    p.label("call_invoke")
    p.emit(mips.jalr("ra", "t3"))
    p.emit(NOP)
    # Count the call against the same readout as the writes, so `applied` is still
    # "everything the block did this tick" -- and so a call proves liveness too.
    p.emit(mips.lw("t0", 0x10, "sp"))
    p.emit(mips.lw("t1", 0x0C, "t0"))
    p.emit(mips.addiu("t1", "t1", 1))
    p.emit(mips.sw("t1", 0x0C, "t0"))
    p.b("call_loop")
    p.emit(NOP)

    p.label("call_done")
    p.emit(mips.lw("a0", 0x18, "sp"))
    p.emit(mips.lw("a1", 0x1C, "sp"))
    p.emit(mips.lw("a2", 0x20, "sp"))
    p.emit(mips.lw("a3", 0x24, "sp"))

    # --- restore, call the original callee, return to the original caller -----
    p.emit(mips.jal(callee))
    p.emit(NOP)
    p.emit(mips.lw("ra", 0x14, "sp"))
    p.emit(mips.addiu("sp", "sp", 0x30))
    p.emit(mips.j(site + 8))
    p.emit(NOP)

    code = p.finish()
    if len(code) > CAVE_CODE_SIZE:
        raise ValueError(
            f"interpreter grew to {len(code)} bytes, past the {CAVE_CODE_SIZE}-byte reservation"
        )
    # Pad so the parameter block sits at a fixed offset.
    code = code + b"\x00" * (CAVE_CODE_SIZE - len(code))
    return Interpreter(cave=cave, site=site, callee=callee, code=code)


def initial_param_block(max_entries: int = MAX_ENTRIES, magic: bytes = MAGIC) -> bytes:
    """A quiet block: no writes and no calls, so the cave is harmless until we fill it."""
    if len(magic) != 4:
        raise ValueError("magic must be exactly 4 bytes")
    block = bytearray(PARAM_BLOCK_SIZE)
    struct.pack_into("<4sIII", block, 0, magic, 0, max_entries, 0)
    return bytes(block)


def const(addr: int, value: int) -> tuple[int, int]:
    """An entry that stores a constant. Values with bit 31 set are rejected.

    Bit 31 of the value word is the interpreter's "this is an address, read from
    it" flag, so a constant that happens to have it set cannot be expressed. Real
    frame state (ids, enums, flags, pad ports) never does; a raw pointer would,
    and copying a pointer between actors is not something we want to allow
    anyway.
    """
    if value & 0x80000000:
        raise ValueError(
            f"constant {value:#x} has bit 31 set, which the interpreter reads as an indirect "
            "entry. Pass a smaller value, or use copy() if you meant an address."
        )
    return addr & 0xFFFFFFFF, value & 0xFFFFFFFF


def copy(addr: int, source: int) -> tuple[int, int]:
    """An entry that copies the word at ``source`` into ``addr``, in-game, per frame."""
    if source & 0x80000000:
        raise ValueError("source address has bit 31 set; it must be a plain EE address")
    return addr & 0xFFFFFFFF, source | 0x80000000


def entry_is_indirect(value: int) -> bool:
    return bool(value & 0x80000000)


def entry_source(value: int) -> int:
    return value & 0x7FFFFFFF


def call(fn: int, arg: int = 0) -> tuple[int, int]:
    """A call entry: call the function at ``fn`` with ``arg`` in ``$a0``.

    Constants only. For "pass whatever is in that word" uses :func:`call_read`.
    """
    if arg & 0x80000000:
        raise ValueError(
            f"argument {arg:#x} has bit 31 set, which the interpreter reads as an indirect "
            "argument. Pass a smaller value, or use call_read() if you meant an address."
        )
    return fn & 0xFFFFFFFF, arg & 0xFFFFFFFF


def call_read(fn: int, source: int) -> tuple[int, int]:
    """A call entry whose argument is read from memory at call time.

    The useful shape is a handle the game owns: an actor slot, a pointer the stage
    just allocated, a counter the engine keeps rewriting. Reading it in-game means
    the function gets the value as it is *now*, not as it was one watchdog pass ago.
    """
    if source & 0x80000000:
        raise ValueError("source address has bit 31 set; it must be a plain EE address")
    return fn & 0xFFFFFFFF, source | 0x80000000


def encode_calls(calls: list[tuple[int, int]], max_calls: int = MAX_CALLS) -> bytes:
    """Pack (function, argument) pairs into the call region, zero terminated.

    Empty slots are skipped by the interpreter rather than treated as the end of
    the list, because the cave *consumes* a call when it makes it -- so the region
    has to be scannable with holes in it.
    """
    if len(calls) > max_calls:
        raise ValueError(
            f"{len(calls)} calls but the cave's budget is {max_calls}; a refill that needs to "
            "call more functions than that is doing something the mod was not designed for"
        )
    if any(fn == 0 for fn, _ in calls):
        raise ValueError("function address 0 is an empty slot and cannot be called")
    out = bytearray()
    for fn, arg in calls:
        out += struct.pack("<II", fn & 0xFFFFFFFF, arg & 0xFFFFFFFF)
    out += b"\x00" * 8
    return bytes(out)


def encode_entries(entries: list[tuple[int, int]], max_entries: int = MAX_ENTRIES) -> bytes:
    """Pack (address, value) pairs, zero terminated, into a full block payload."""
    if len(entries) > max_entries:
        raise ValueError(
            f"{len(entries)} entries but the cave's budget is {max_entries}; raise MAX_ENTRIES and "
            "rebuild the interpreter, or apply fewer things per frame"
        )
    if any(addr == 0 for addr, _ in entries):
        raise ValueError("address 0 terminates the list and cannot be used as a target")
    out = bytearray()
    for addr, value in entries:
        out += struct.pack("<II", addr & 0xFFFFFFFF, value & 0xFFFFFFFF)
    out += b"\x00" * 8
    return bytes(out)


def encode_block(
    entries: list[tuple[int, int]],
    max_entries: int = MAX_ENTRIES,
    magic: bytes = MAGIC,
    calls: list[tuple[int, int]] | None = None,
    max_calls: int = MAX_CALLS,
) -> bytes:
    """Full parameter-block image, for the one moment a cave is installed.

    The watchdog must **not** use this: it would reset the tick counter that
    proves the hook is alive. Ongoing passes write ``encode_entries`` at
    ``ENTRIES_OFFSET`` and ``encode_calls`` at ``CALLS_OFFSET`` instead -- the whole
    call region every time, which is safe because the host re-sends the calls it has
    not yet seen the cave consume (see ``CoopMod._settle_calls``).
    """
    block = bytearray(PARAM_BLOCK_SIZE)
    struct.pack_into("<4sIII", block, 0, magic, 0, max_entries, 0)
    payload = encode_entries(entries, max_entries)
    block[ENTRIES_OFFSET : ENTRIES_OFFSET + len(payload)] = payload
    if calls:
        call_payload = encode_calls(calls, max_calls)
        block[CALLS_OFFSET : CALLS_OFFSET + len(call_payload)] = call_payload
    return bytes(block)


def read_calls(pine, cave: int) -> list[tuple[int, int]]:
    """The call slots that are still waiting to be made, in slot order.

    The cave zeroes a slot's function word when it makes that call, so what is left
    here is exactly "queued, not yet run" -- which is the evidence the host needs to
    say a call has been made, and the reason it must not re-arm a slot the game has
    simply not reached yet.
    """
    raw = pine.read_bytes(param_block_at(cave) + CALLS_OFFSET, CALLS_SIZE)
    out: list[tuple[int, int]] = []
    for i in range(0, len(raw) - 7, 8):
        fn, arg = struct.unpack_from("<II", raw, i)
        if fn:
            out.append((fn, arg))
    return out


def read_block(pine, cave: int, magic: bytes = MAGIC) -> dict:
    """Read the live parameter block back: liveness, budget and applied count."""
    import struct as _struct

    raw = pine.read_bytes(param_block_at(cave), 0x10)
    got_magic, tick, budget, applied = _struct.unpack("<4sIII", raw)
    return {
        "magic_ok": got_magic == magic,
        "magic": got_magic,
        TICKS: tick,
        "budget": budget,
        "applied": applied,
    }


@dataclass
class CaveInstall:
    """Everything needed to install or remove one interceptor."""

    cave: int
    site: int
    interpreter: Interpreter
    original_site_word: bytes = b""
    installed: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def callee(self) -> int:
        return self.interpreter.callee

    def patch_pairs(self) -> list[tuple[int, bytes]]:
        """(address, bytes) writes: the cave body, the block, then the trampoline."""
        block = param_block_at(self.cave)
        return [
            (self.cave, self.interpreter.code),
            (block, initial_param_block()),
            (self.site, self.interpreter.trampoline),
        ]


def find_free_region(pine, size: int, ranges, alignment: int = 4) -> int:
    """First aligned run of ``size`` untouched bytes in ``ranges``.

    "Untouched" is judged by a cheap entropy rule: a run that is entirely 0x00 (or
    entirely 0xFF) has almost certainly never been written by a running game. It
    is a heuristic, which is why ``find_free_region`` only *proposes* an address:
    the caller samples it across frames before trusting it (``verify_stable``).
    """
    from .scan import CHUNK

    zeros = b"\x00" * size
    for start, end in ranges:
        addr = start
        carry = b""
        while addr < end:
            take = min(CHUNK, end - addr)
            blob = carry + pine.read_bytes(addr, take)
            base = addr - len(carry)
            pos = 0
            while True:
                idx = blob.find(zeros, pos)
                if idx < 0:
                    break
                candidate = base + idx
                slack = (alignment - (candidate % alignment)) % alignment
                if candidate + slack + size <= end and pine.read_bytes(candidate + slack, size) == zeros:
                    return candidate + slack
                pos = idx + 1
            carry = blob[-(size - 1) :]
            addr += take
    raise LookupError(
        f"no run of {size} zero bytes found. Free space moves between builds; pass explicit "
        "--range values, or host the cave in the scratchpad (0x70000000) if the game leaves it alone."
    )


def verify_stable(pine, addr: int, size: int, samples: int = 8, delay: float = 0.12) -> bool:
    """Sample a region over time and confirm nothing writes there."""
    import time

    first = pine.read_bytes(addr, size)
    if first != b"\x00" * size:
        return False
    for _ in range(max(1, samples - 1)):
        time.sleep(delay)
        if pine.read_bytes(addr, size) != first:
            return False
    return True
