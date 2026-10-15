"""``aecoops selfcheck``: the checks that do not need a PlayStation 2.

These are the same assertions the test suite makes, in a form that runs on a
user's machine in one command. They answer the only question that matters before
you point this at a running game: *is the machine code it is about to install
actually correct?*

Checked here:
1. the instruction encodings, against hand-computed words;
2. the interpreter's size budget;
3. the parameter block's encoder, including indirect entries;
4. the cave's behaviour, executed in a simulator: writes applied, displaced
   function called once, ``$ra`` and ``$sp`` restored, control returned to
   ``site + 8``;
5. the PINE protocol's framing, against a local server that speaks it;
6. the address worksheet parses and reports itself honestly.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from . import cave, mips, pnach
from .addresses import AddressTable
from .r5900vm import R5900VM

FAIL = "FAIL"
PASS = "ok"


class Checker:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            if self.verbose:
                print(f"  {PASS}  {name}")
        else:
            self.failed += 1
            print(f"  {FAIL}  {name}" + (f"\n        {detail}" if detail else ""))
        return condition

    def section(self, title: str) -> None:
        print(f"\n{title}")


def check_encodings(c: Checker) -> None:
    c.section("instruction encodings")
    known = [
        ("nop", mips.NOP, 0x00000000),
        ("jr $ra", mips.jr("ra"), 0x03E00008),
        ("addiu $sp,$sp,-0x18", mips.addiu("sp", "sp", -0x18), 0x27BDFFE8),
        ("sw $ra,0x14($sp)", mips.sw("ra", 0x14, "sp"), 0xAFBF0014),
        ("lui $t0,0x0014", mips.lui("t0", 0x0014), 0x3C080014),
        ("ori $t0,$t0,0x0000", mips.ori("t0", "t0", 0), 0x35080000),
        ("addu $t0,$t1,$t2", mips.addu("t0", "t1", "t2"), 0x012A4021),
        ("sll $t4,$t4,1", mips.sll("t4", "t4", 1), 0x000C6040),
        ("beq $t3,$zero,+2", mips.beq("t3", "zero", 2), 0x11600002),
        ("blez $t5,+3", mips.blez("t5", 3), 0x19A00003),
        ("lw $t3,0($t2)", mips.lw("t3", 0, "t2"), 0x8D4B0000),
        ("jal 0x00140000", mips.jal(0x00140000), 0x0C050000),
        ("bltz $t4,+1", mips.bltz("t4", 1), 0x05800001),
    ]
    for name, got, want in known:
        c.check(f"{name} == {want:08x}", got == want, f"got {got:08x}")

    # li/la: the two shapes, and the sign rule that decides which one is used
    c.check("li $t0,2 is one instruction", len(mips.load_immediate("t0", 2)) == 1)
    c.check("li $t0,-1 is addiu, not ori", mips.load_immediate("t0", -1)[0] == mips.addiu("t0", "zero", -1))
    c.check("li $t0,0x12345 is lui+ori", [w >> 26 for w in mips.load_immediate("t0", 0x12345)] == [0x0F, 0x0D])
    c.check(
        "la $t0,0x0000FFFF stays unsigned",
        mips.la("t0", 0x0000FFFF) == [mips.lui("t0", 0), mips.ori("t0", "t0", 0xFFFF)],
    )

    # A jump only encodes 26 bits; both assembler and disassembler must agree.
    for target in (0x00140000, 0x00412340):
        line = mips.disassemble(mips.j(target), 0)
        c.check(f"j/{target:#x} survives a round trip", f"{target:#010x}" in line, line)
    for branch in (mips.beq("t1", "t2", 3), mips.bne("t1", "t2", -2)):
        c.check("branch decodes to a branch", mips.disassemble(branch, 0x1000).split()[0] in ("beq", "bne"))


def check_cave_semantics(c: Checker) -> None:
    c.section("cave semantics (executed in the R5900 simulator)")
    site = 0x00123450
    cave_addr = 0x00140000
    callee = 0x001D0000

    interp = cave.build_interpreter(cave_addr, site, callee)
    c.check(
        f"interpreter fits its {cave.CAVE_CODE_SIZE}-byte reservation",
        len(interp.code) <= cave.CAVE_CODE_SIZE,
        f"{len(interp.code)} bytes",
    )
    c.check(
        "the trampoline is exactly one instruction (the displaced jal)",
        len(interp.trampoline) == 4 and (struct.unpack("<I", interp.trampoline)[0] >> 26) == 0x03,
    )
    c.check(
        "the trampoline targets the cave",
        cave.callee_of_jal(struct.unpack("<I", interp.trampoline)[0], site) == cave_addr,
    )

    vm = R5900VM()
    vm.write_bytes(cave_addr, interp.code)
    vm.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block())
    # Three entries: two constants and one indirect copy, which is the shape the
    # tether uses.
    vm.write32(0x00200000, 0xCAFEBABE)
    entries = [
        cave.const(0x00210000, 2),
        cave.const(0x00210004, 0x1234),
        cave.copy(0x00210008, 0x00200000),
    ]
    vm.write_bytes(cave.param_block_at(cave_addr) + 0x10, cave.encode_entries(entries))
    vm.write32(cave.param_block_at(cave_addr) + 0x08, cave.MAX_ENTRIES)

    stack_before = 0x001FFF00
    vm.set_reg("sp", stack_before)
    vm.set_reg("ra", 0xFEEDFACE)  # clobbered by the trampoline's jal, as in the real game
    # The trampoline is what the game executes; being a jal, it sets $ra to site+8.
    vm.write_bytes(site, interp.trampoline)

    result = vm.run(start=site, stop_at=site + 8, external_calls=(callee,))

    c.check("control returned to site+8", result.halted == "stop-at", result.halted)
    c.check("the displaced function was called exactly once", len(result.calls) == 1, str(result.calls))
    c.check(
        "the displaced function was entered from inside the cave",
        result.calls and cave_addr <= result.calls[0].ra < cave_addr + len(interp.code),
        f"{result.calls[0].ra:#x}" if result.calls else "no call",
    )
    c.check("$sp is where the game left it", vm.get_reg("sp") == stack_before)
    c.check(
        "$ra holds what the displaced jal would have left",
        vm.get_reg("ra") == site + 8,
        f"{vm.get_reg('ra'):#x}",
    )
    c.check("constant entry 1 applied", vm.read32(0x00210000) == 2, hex(vm.read32(0x00210000)))
    c.check("constant entry 2 applied", vm.read32(0x00210004) == 0x1234)
    c.check("indirect entry copied the live word", vm.read32(0x00210008) == 0xCAFEBABE)

    block = cave.read_block(_VMReader(vm), cave_addr)
    c.check("frame tick advanced", block["ticks"] == 1, str(block))
    c.check("applied count is reported", block["applied"] == 3, str(block))

    # Run it a second time: the tick must keep climbing and the writes must be
    # idempotent, because that is what the watchdog relies on.
    vm.write32(0x00200000, 0xDEADBEEF)
    vm.run(start=site, stop_at=site + 8, external_calls=(callee,))
    block = cave.read_block(_VMReader(vm), cave_addr)
    c.check("second pass ticks again", block["ticks"] == 2, str(block))
    c.check("second pass re-copied", vm.read32(0x00210008) == 0xDEADBEEF)

    # An empty block must be a no-op: this is the state the game is left in if
    # the host process dies mid-session.
    vm.write_bytes(cave.param_block_at(cave_addr) + 0x10, cave.encode_entries([]))
    before = vm.read32(0x00210000)
    vm.run(start=site, stop_at=site + 8, external_calls=(callee,))
    c.check("an empty block writes nothing", vm.read32(0x00210000) == before)

    # The budget must be honoured even if the list is longer than the block.
    long_entries = [cave.const(0x00210000 + 4 * i, i + 1) for i in range(cave.MAX_ENTRIES + 8)]
    vm.write32(cave.param_block_at(cave_addr) + 0x08, cave.MAX_ENTRIES)
    vm.write_bytes(cave.param_block_at(cave_addr) + 0x10, cave.encode_entries(
        long_entries[: cave.MAX_ENTRIES]
    ))
    vm.run(start=site, stop_at=site + 8, external_calls=(callee,))
    block = cave.read_block(_VMReader(vm), cave_addr)
    c.check("the budget caps one pass", block["applied"] == cave.MAX_ENTRIES, str(block))

    c.check(
        "indirect entries reject a source with bit 31 set",
        _raises(lambda: cave.copy(0x1000, 0x80001000)),
    )
    c.check(
        "constants reject bit 31 (it would mean 'indirect')",
        _raises(lambda: cave.const(0x1000, 0x80000000)),
    )
    c.check(
        "address 0 is refused (it terminates the list)",
        _raises(lambda: cave.encode_entries([cave.const(0, 1)])),
    )


def check_pnach(c: Checker) -> None:
    c.section("standalone patch export")
    text = pnach.build_from_words(
        site=0x00123450,
        site_word=mips.jal(0x001D0000),
        cave_addr=0x00140000,
        gametitle="Ape Escape 3",
        serial="SCUS-97501",
        crc="A1B2C3D4",
        entries=[cave.const(0x00210000, 2), cave.copy(0x00210004, 0x00200000)],
    )
    lines = text.splitlines()
    c.check("has a gametitle line", any(ln.startswith("gametitle=") for ln in lines))
    patches = [ln for ln in lines if ln.startswith("patch=")]
    c.check("emits patch lines", bool(patches))
    c.check("every patch line is well formed", all(len(ln.split(",")) >= 5 for ln in patches), str(patches[:2]))
    c.check(
        "the trampoline is written as a single word patch",
        any(ln.endswith(",word,0x0C050000") for ln in patches),
        str(patches[-1:]),
    )
    c.check(
        "big blocks use extended writes",
        any(",extended," in ln for ln in patches),
    )
    c.check("the filename follows PCSX2's convention", pnach.pnach_name("SCUS-97501", "a1b2c3d4") == "A1B2C3D4.pnach")

    # Round-trip: the words in the extended lines are the cave's own bytes.
    first_extended = next(ln for ln in patches if ",extended," in ln)
    words = [int(w, 16) for w in first_extended.split(",")[4].split()]
    interp = cave.build_interpreter(0x00140000, 0x00123450, 0x001D0000)
    c.check(
        "the first extended line is the cave's first words",
        struct.pack(f"<{len(words)}I", *words) == interp.code[: 4 * len(words)],
    )


def check_protocol(c: Checker) -> None:
    c.section("PINE protocol framing (against a local server)")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from tests.fakepine import FakePCSX2
    except ImportError as exc:  # pragma: no cover - packaging accident
        c.check("fake PCSX2 importable", False, str(exc))
        return

    from .pine import Pine

    with FakePCSX2() as fake:
        with Pine(slot=fake.port, timeout=5.0) as pine:
            c.check("status() decodes the 5-byte reply", pine.status() == 0)
            c.check("version() reads a length-prefixed string", pine.version().startswith("PCSX2"))
            c.check("title() round trips", pine.title() == fake.title)
            c.check("serial() round trips", pine.serial() == fake.serial)
            fake.ram[0x00200000 : 0x00200004] = b"\x78\x56\x34\x12"
            c.check("read32", pine.read32(0x00200000) == 0x12345678)
            c.check("read_bytes spans words", pine.read_bytes(0x00200000, 4) == b"\x78\x56\x34\x12")
            pine.write32(0x00200010, 0xDEADBEEF)
            c.check("write32 lands", fake.ram[0x00200010 : 0x00200014] == b"\xef\xbe\xad\xde")
            pine.write8(0x00200020, 0x41)
            c.check("write8 lands", fake.ram[0x00200020] == 0x41)
            pine.write_bytes(0x00200021, b"\x01\x02\x03")
            c.check("unaligned write_bytes does not clobber neighbours", fake.ram[0x00200020 : 0x00200024] == b"\x41\x01\x02\x03")
            big = bytes(range(256)) * 400  # 100 KiB: many batched round trips
            fake.ram[0x00300000 : 0x00300000 + len(big)] = big
            c.check("batched read matches", pine.read_bytes(0x00300000, len(big)) == big)
            c.check("batched write matches", _write_round_trip(pine, fake, 0x00400000, big))
            c.check("server saw batched requests, not one per word", fake.requests < 2000, str(fake.requests))


def check_table(c: Checker) -> None:
    c.section("address worksheet")
    table = AddressTable.load()
    c.check("loads", bool(table.globals))
    c.check("knows three regional eras", len(table.eras) >= 3)
    c.check("keeps unresolved entries visible", bool(table.unresolved()))
    report = table.report()
    c.check("reports itself", "frame_hook" in report and "NOT FOUND YET" in report)
    c.check(
        "unknown characters are refused, not guessed",
        _raises(lambda: table.character_id("kei")),
    )


# ------------------------------------------------------------------- utilities


class _VMReader:
    """Adapts the VM's memory to the ``read_bytes`` interface cave.read_block wants."""

    def __init__(self, vm: R5900VM) -> None:
        self.vm = vm

    def read_bytes(self, addr: int, length: int) -> bytes:
        return self.vm.read_bytes(addr, length)


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def _write_round_trip(pine, fake: "object", addr: int, data: bytes) -> bool:
    pine.write_bytes(addr, data)
    return fake.ram[addr : addr + len(data)] == data


def run_all(verbose: bool = False) -> int:
    c = Checker(verbose)
    check_encodings(c)
    check_cave_semantics(c)
    check_pnach(c)
    check_protocol(c)
    check_table(c)
    print(f"\n{c.passed} passed, {c.failed} failed")
    return 0 if c.failed == 0 else 1
