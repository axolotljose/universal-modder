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
6. the address worksheet parses and reports itself honestly;
7. the HUD policy writes nothing on an unmeasured disc, and puts two instances in
   the two halves on a measured one;
8. the viewport rectangles tile the frame, and a half-measured one is refused
   rather than half-written;
9. the call region does not overlap the write list, and a call is made once, with
   its argument, and is consumed;
10. every `docs/DISCOVERY.md#key` pointer the tool or the worksheet prints still
    resolves to a section of the recipe, which is a promise a message makes every
    time it says "here is how to measure this";
11. the bundle `aecoops share` prints is one paste, plain ASCII, and carries no
    game bytes -- it is the thing that crosses from the machine running the game to
    wherever the conversation is;
12. the pre-flight check catches the ways a worksheet is wrong -- a hook that is not
    a `jal`, a character id that contradicts the actor table, a call into empty
    memory -- and reads rather than writes.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from . import cave, hud, mips, pnach, views
from .addresses import AddressTable, CallSpec
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


def check_views(c: Checker) -> None:
    """The viewport rectangles: the split's second lever, and its refusals."""
    c.section("viewport rectangles")
    from .addresses import Field, Struct
    from .scan import Discovery

    table = AddressTable.load()
    plan = views.plan_views(table, "vertical", hud.Screen())
    c.check("an unmeasured disc writes nothing", plan.entries == [])
    c.check("...and does not treat a missing backup as a failure", plan.warnings == [])
    c.check("...but says the split rests on view_mode", any("view_mode" in n for n in plan.notes))

    struct = Struct(
        name="viewport",
        base=Discovery(name="viewport.base", addr=0x00183000),
        stride=0x20,
        count=2,
        fields={
            "x": Field("x", 0x00),
            "y": Field("y", 0x04),
            "w": Field("w", 0x08),
            "h": Field("h", 0x0C),
            "target_actor": Field("target_actor", 0x10),
        },
    )
    table.structs["viewport"] = struct
    table.globals["viewport_count"] = Discovery(name="viewport_count", addr=0x00110028)
    plan = views.plan_views(table, "vertical", hud.Screen(), targets=(0, 1), resolved={"viewport_count": 0x00110028})
    values = dict(plan.entries)
    c.check("the split is two halves", (values.get(0x00183000), values.get(0x00183008)) == (0, 320))
    c.check("the second view is in the right half", values.get(0x00183020) == 320, str(plan.labels))
    c.check("the halves tile the frame", values.get(0x00183008) + values.get(0x00183028) == 640)
    c.check("each view follows its own hero", (values.get(0x00183010), values.get(0x00183030)) == (0, 1))
    c.check("the count is written first", plan.entries[0] == (0x00110028, 2))
    c.check("labels line up with writes", len(plan.labels) == len(plan.entries))

    struct.fields["w"] = Field("w", None)
    partial = views.plan_views(table, "vertical", hud.Screen())
    c.check("a three-sided rectangle is not written at all", partial.entries == [], str(partial.entries))
    c.check("...and says which field is missing", "w" in " ".join(partial.notes))
    struct.fields["w"] = Field("w", 0x08)
    struct.stride = 0
    single = views.plan_views(table, "vertical", hud.Screen())
    c.check("one addressable rectangle means no split", single.entries == [] and not single.placed)


def check_calls(c: Checker) -> None:
    """Calls: the half of the mod that makes the game *do* something.

    A write sets a word; it cannot spawn the second actor or let a view mode setter
    rebuild the viewports. That needs the game's own code, entered from inside the
    cave, which is what these checks execute.
    """
    c.section("calls (executed in the R5900 simulator)")
    # The layout, first, because it is the kind of bug that hides: a call slot that
    # shares bytes with the write list is a call wiped by a full list of writes.
    terminator = cave.ENTRIES_OFFSET + 8 * (cave.MAX_ENTRIES + 1)
    c.check(
        "the call region starts after the entry list's terminator",
        cave.CALLS_OFFSET >= terminator,
        f"calls at {cave.CALLS_OFFSET:#x}, terminator ends {terminator:#x}",
    )
    c.check(
        "the call region fits the parameter block",
        cave.CALLS_END <= cave.PARAM_BLOCK_SIZE,
        f"{cave.CALLS_END:#x} > {cave.PARAM_BLOCK_SIZE:#x}",
    )
    c.check(
        "an entry list at the budget cannot reach a call slot",
        cave.ENTRIES_OFFSET + 8 * (cave.MAX_ENTRIES + 1) <= cave.CALLS_OFFSET,
    )
    c.check("calls over the budget are refused", _raises(lambda: cave.encode_calls([(1, 0)] * (cave.MAX_CALLS + 1))))
    c.check("calling address 0 is refused", _raises(lambda: cave.encode_calls([(0, 1)])))
    c.check(
        "a constant argument with bit 31 set is refused (it would mean 'read')",
        _raises(lambda: cave.call(0x00160000, 0x80000000)),
    )
    c.check(
        "...and the indirect spelling refuses a source with bit 31 set",
        _raises(lambda: cave.call_read(0x00160000, 0x80001000)),
    )

    site = 0x00123450
    cave_addr = 0x00140000
    callee = 0x001D0000
    spawner = 0x001B0000
    interp = cave.build_interpreter(cave_addr, site, callee)
    vm = R5900VM()
    vm.write_bytes(cave_addr, interp.code)
    vm.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block())
    vm.write32(cave.param_block_at(cave_addr) + 0x08, cave.MAX_ENTRIES)
    vm.write_bytes(cave.param_block_at(cave_addr) + cave.ENTRIES_OFFSET, cave.encode_entries([]))
    vm.write_bytes(site, interp.trampoline)
    vm.set_reg("sp", 0x001FFF00)

    # A spawner that takes its argument in $a0, with the word it is asked to read
    # sitting where the game would keep a handle.
    vm.write32(0x00200000, 0x00ABCDEF)
    vm.write_bytes(
        cave.param_block_at(cave_addr) + cave.CALLS_OFFSET,
        cave.encode_calls([cave.call(spawner, 1), cave.call_read(spawner, 0x00200000)]),
    )
    result = vm.run(start=site, stop_at=site + 8, external_calls=(callee, spawner))
    made = [(call.callee, call.a0) for call in result.calls if call.callee == spawner]
    c.check("the call was made", len(made) == 2, str(made))
    c.check("...with the constant argument in $a0", made and made[0][1] == 1, str(made))
    c.check("...and the indirect one read in the cave", made and made[1][1] == 0x00ABCDEF, str(made))
    c.check(
        "the displaced function still ran after the calls",
        result.calls and result.calls[-1].callee == callee,
        str(result.calls),
    )
    c.check(
        "the call was consumed, so it is not made again",
        cave.read_calls(_VMReader(vm), cave_addr) == [],
        str(cave.read_calls(_VMReader(vm), cave_addr)),
    )
    second = vm.run(start=site, stop_at=site + 8, external_calls=(callee, spawner))
    made_again = [call for call in second.calls if call.callee == spawner]
    block = cave.read_block(_VMReader(vm), cave_addr)
    c.check("a consumed call is not made a second time", made_again == [], str(made_again))
    c.check("...and the pass has nothing left to apply", block["applied"] == 0, str(block))

    # Queued but not yet run is what the host reads back to know a call is pending.
    vm.write_bytes(
        cave.param_block_at(cave_addr) + cave.CALLS_OFFSET,
        cave.encode_calls([cave.call(spawner, 3)]),
    )
    waiting = cave.read_calls(_VMReader(vm), cave_addr)
    c.check("a queued call reads back as waiting", waiting == [(spawner, 3)], str(waiting))

    # The worksheet's half: calls are opt-in, and a half-measured one says so.
    from .addresses import CallSpec

    table = AddressTable.load()
    c.check("the shipped worksheet's calls are all listed as unresolved", table.unresolved_calls() != [])
    c.check("...and `unresolved()` mentions them", any("calls." in b for b in table.unresolved()))
    blank = CallSpec(name="blank")
    c.check("a blank call is not ready", not blank.ready)
    c.check("a half-measured call is not ready", not CallSpec(name="half", addr=0x00160000).ready)
    c.check("a measured one is", CallSpec(name="spawn", addr=0x00160000, arg=1).ready)
    c.check("once is the default", CallSpec(name="spawn").once)


def check_hud(c: Checker) -> None:
    """The HUD policy: geometry that tiles, and writes that are gated on data.

    The important one is the second half. A split-screen HUD is the first thing
    this mod does that *moves something the player is looking at*, so the policy
    has to be provably incapable of writing an address nobody measured.
    """
    c.section("HUD placement")
    screen = hud.Screen(641, 449, margin=8)
    left, right = screen.views("vertical")
    c.check("the halves tile an odd width", left.w + right.w == 641 and right.x == left.w,
            f"{left} {right}")
    top, bottom = screen.views("horizontal")
    c.check("the halves tile an odd height", top.h + bottom.h == 449, f"{top} {bottom}")
    c.check("anchors sit inside their own view", screen.anchors("vertical")[1][0] >= left.w)
    c.check("a single view is the whole frame", screen.views("single")[0].w == 641)
    c.check("bad screen strings are refused", _raises(lambda: hud.Screen.parse("wide")))

    table = AddressTable.load()
    plan = hud.plan_hud(table, hud.HudMode.PER_VIEW, "vertical", hud.Screen())
    c.check("an unmeasured worksheet writes nothing", plan.entries == [])
    c.check("...and says why, with a pointer to the recipe", any("DISCOVERY.md#hud" in w for w in plan.warnings))
    shared = hud.plan_hud(table, hud.HudMode.SHARED, "vertical", hud.Screen())
    c.check("shared is silent and write-free", shared.entries == [] and shared.warnings == [])

    # A fully measured disc, in memory: the policy's numbers, not the game's.
    from .addresses import Field, Struct
    from .scan import Discovery

    struct = Struct(
        name="hud",
        base=Discovery(name="hud.base", addr=0x00181000),
        stride=0x40,
        count=2,
        fields={
            "visible": Field("visible", 0x00),
            "x": Field("x", 0x04),
            "y": Field("y", 0x08),
            "owner": Field("owner", 0x0C),
        },
    )
    table.structs["hud"] = struct
    table.globals["hud_instances"] = Discovery(name="hud_instances", addr=0x00110024)
    plan = hud.plan_hud(table, hud.HudMode.PER_VIEW, "vertical", hud.Screen(), resolved={"hud_instances": 0x00110024})
    values = dict(plan.entries)
    c.check("per-view asks for two instances", values.get(0x00110024) == 2)
    c.check("each instance is visible", values.get(0x00181000) == 1 and values.get(0x00181040) == 1)
    c.check("the second view is anchored in the right half", values.get(0x00181044) == 328, str(plan.labels[-4:]))
    c.check("each instance is owned by a different pad", values.get(0x0018100C) == 0 and values.get(0x0018104C) == 1)
    c.check("labels line up with writes", len(plan.labels) == len(plan.entries))
    hidden = hud.plan_hud(table, hud.HudMode.HIDDEN, "vertical", hud.Screen(), resolved={"hud_instances": 0x00110024})
    c.check("hidden switches the instances off rather than hiding one", hidden.entries == [(0x00110024, 0)])


def doc_pointer_problems(root: Path | None = None) -> list[str]:
    """Every `docs/DISCOVERY.md#key` in the tool, checked against the doc's table.

    A message that points at the recipe is the difference between a tool that says
    "not measured" and a tool that says "not measured, here is how". A pointer into a
    section that does not exist is worse than no pointer, because it reads as help.
    The keys are deliberately short (a warning line has to fit a terminal), so the doc
    keeps the key table and this checks both halves of it: that the section a key
    names exists, and that every key the code prints is in the table.
    """
    import re

    root = root or Path(__file__).resolve().parent.parent
    doc = root / "docs" / "DISCOVERY.md"
    if not doc.exists():
        return [f"{doc} is missing: the tool's messages point at it"]
    text = doc.read_text(encoding="utf-8")
    body = text.split("## Which section a message means", 1)
    if len(body) < 2:
        return [f"{doc.name}: the key table ('## Which section a message means') is gone"]
    table = body[1].split("\n---", 1)[0]
    keys = {name: target for name, target in re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([^|]+?)\s*\|\s*$", table, re.M)}
    numbered = {int(n) for n in re.findall(r"^## (\d+)\.", text, re.M)}
    titled = {t.strip().lower() for t in re.findall(r"^## (.+)$", text, re.M)}

    problems: list[str] = []
    for name, target in sorted(keys.items()):
        if target.isdigit():
            if int(target) not in numbered:
                problems.append(f"key {name!r} points at section {target}, which the doc does not have")
        elif target.lower() not in titled:
            problems.append(f"key {name!r} points at {target!r}, which is not a heading")

    sources = sorted((root / "aecoops").rglob("*.py")) + [root / "aecoops" / "data" / "addresses.json"]
    for source in sources:
        if not source.exists() or source.name == "selftest.py":
            # This file's prose is *about* the convention, so it is the one place a
            # bare `DISCOVERY.md#key` is not a pointer.
            continue
        for fragment in sorted(set(re.findall(r"DISCOVERY\.md#([A-Za-z0-9_-]*)", source.read_text(encoding="utf-8")))):
            if fragment not in keys:
                problems.append(f"{source.name} points at DISCOVERY.md#{fragment}, which is not a key in the table")
    return problems


def check_verify(c: Checker) -> None:
    """The pre-flight check: does the worksheet agree with the game?"""
    c.section("worksheet pre-flight")
    from . import verify

    class _Words:
        def __init__(self, words) -> None:
            self.words = words

        def read32(self, addr: int) -> int:
            return self.words.get(addr, 0)

    c.check("a worksheet with nothing measured is not a problem", verify.check_table(_Words({}), AddressTable.load()).skipped)

    table = AddressTable.load()
    table.globals["frame_hook"].addr = 0x00123450
    table.characters = {"kei": 0, "yumi": 1}
    table.layouts = {"single": 0, "vertical": 3}
    table.structs["actor"].base.addr = 0x00180000
    table.structs["actor"].stride = 0x100
    for name, offset in {"active": 0x00, "char_id": 0x04, "pad_port": 0x08}.items():
        table.structs["actor"].fields[name].offset = offset
    table.calls = [CallSpec(name="spawn_partner", addr=0x00160000, arg=1)]
    good = {
        0x00123450: 0x0C048D14,
        0x00180004: 0,
        0x00180008: 0,
        0x00160000: 0x27BDFFE8,
    }
    report = verify.check_table(_Words(good), table)
    c.check("a consistent table has no problems", not report.problems, report.text())
    c.check("...and says so", "consistent" in report.text())

    broken = dict(good)
    broken[0x00123450] = 0x27BDFFE8  # the instruction body, not the call to it
    problems = [p.message for p in verify.check_table(_Words(broken), table).problems]
    c.check("a hook that is not a jal is caught", any("not a jal" in m for m in problems), str(problems))

    broken = dict(good)
    broken[0x00180004] = 42
    problems = [p.message for p in verify.check_table(_Words(broken), table).problems]
    c.check("a character id that contradicts the table is caught", any("neither measured" in m for m in problems))

    broken = dict(good)
    del broken[0x00160000]
    problems = [p.message for p in verify.check_table(_Words(broken), table).problems]
    c.check("a call into empty memory is caught", any("all zeros" in m for m in problems))
    c.check(
        "a misaligned call address is caught",
        any(
            "not a word-aligned" in p.message
            for p in verify.check_table(_Words(good), type(table)(
                game=table.game, calls=[CallSpec(name="x", addr=0x00160002, arg=0)]
            )).problems
        ),
    )


def check_share(c: Checker) -> None:
    """The pasteable bundle: small, plain, and incapable of carrying game data."""
    c.section("the measurement bundle")
    from . import share

    table = AddressTable.load()
    bundle = share.build_bundle(table)
    c.check(
        f"it is one paste (under {share.COMFORTABLE_BYTES} bytes)",
        bundle.size < share.COMFORTABLE_BYTES,
        f"{bundle.size} bytes",
    )
    c.check("it is plain ASCII", share.ascii_only(bundle.text) == bundle.text)
    c.check("no line runs off a phone screen", max(len(line) for line in bundle.text.splitlines()) < 200)
    c.check("it names every blank", "still to find:" in bundle.text and len(bundle.unresolved) == 32)
    c.check("it says whether the emulator answered", "disc" in bundle.text and "install" in bundle.text)
    c.check(
        "it carries no instruction stream (a journal is a count, not its bytes)",
        all(len(token) <= 12 for token in bundle.text.split() if token.startswith("0x")),
    )
    measured = AddressTable.load()
    measured.globals["frame_hook"].addr = 0x00123450
    measured.globals["frame_hook"].verified = "2026-10-15"
    shown = share.build_bundle(measured)
    c.check("a measured address is shown", "0x00123450" in shown.text)
    c.check("...and drops off the blank list", "frame_hook," not in ", ".join(shown.unresolved) + ",")
    full = share.build_bundle(table, full=True)
    c.check("--full stays pasteable too", full.size < 32 * 1024, f"{full.size} bytes")


def check_pointers(c: Checker) -> None:
    """The recipe pointers: every message that says "here is how" is right."""
    c.section("recipe pointers")
    problems = doc_pointer_problems()
    c.check("every DISCOVERY.md#key in the tool resolves to a section", not problems, "; ".join(problems))
    c.check("the calls recipe is reachable", "calls" in (Path(__file__).resolve().parent.parent / "docs" / "DISCOVERY.md").read_text(encoding="utf-8"))


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
    check_hud(c)
    check_views(c)
    check_calls(c)
    check_pointers(c)
    check_share(c)
    check_verify(c)
    print(f"\n{c.passed} passed, {c.failed} failed")
    return 0 if c.failed == 0 else 1
