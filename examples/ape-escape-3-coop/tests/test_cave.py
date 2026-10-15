"""The cave's contract, executed in the R5900 simulator.

These are the assertions that matter most in the whole project: the cave is the
one piece of code that runs inside someone else's game, and a mistake there is a
crash rather than a warning. Everything here would be impossible to check on a
real console without a debugger, which is exactly why the simulator exists.
"""

from __future__ import annotations

import struct
import unittest

from aecoops import cave, mips
from aecoops.r5900vm import REG, R5900VM

SITE = 0x00123450
CAVE = 0x00140000
CALLEE = 0x001D0000


class CaveHarness:
    """Installs the interpreter into a VM and lets tests call it like the game would."""

    def __init__(self, site: int = SITE, cave_addr: int = CAVE, callee: int = CALLEE) -> None:
        self.site, self.cave, self.callee = site, cave_addr, callee
        self.interp = cave.build_interpreter(cave_addr, site, callee)
        self.vm = R5900VM()
        self.vm.write_bytes(cave_addr, self.interp.code)
        self.vm.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block())
        self.vm.write_bytes(site, self.interp.trampoline)
        self.vm.set_reg("sp", 0x001FF000)

    def set_entries(self, entries: list[tuple[int, int]], calls: list[tuple[int, int]] | None = None) -> None:
        block = cave.param_block_at(self.cave)
        self.vm.write_bytes(block + cave.ENTRIES_OFFSET, cave.encode_entries(entries))
        self.vm.write32(block + 0x08, cave.MAX_ENTRIES)
        if calls is not None:
            self.vm.write_bytes(block + cave.CALLS_OFFSET, cave.encode_calls(calls))

    def call(self, extra_externals: tuple[int, ...] = ()) -> object:
        """One frame's worth of the game: the jal at the site, and its effect."""
        self.vm.regs[0] = 0
        return self.vm.run(
            start=self.site,
            stop_at=self.site + 8,
            external_calls=(self.callee, *extra_externals),
        )

    def call_slot(self, index: int) -> tuple[int, int]:
        """What is left in call slot ``index`` afterwards (0, 0 means consumed)."""
        block = cave.param_block_at(self.cave) + cave.CALLS_OFFSET + index * 8
        return (
            self.vm.read32(block),
            self.vm.read32(block + 4),
        )

    def block(self) -> dict:
        return cave.read_block(_Reader(self.vm), self.cave)


class _Reader:
    def __init__(self, vm: R5900VM) -> None:
        self.vm = vm

    def read_bytes(self, addr: int, length: int) -> bytes:
        return self.vm.read_bytes(addr, length)


class TestCalls(unittest.TestCase):
    """Calling the game's own functions: the cave's second list.

    A write can set state; it cannot *construct* anything. These tests are about
    the one mechanism that can -- and about the two properties that keep it from
    being dangerous: a call fires once per refill rather than once per frame, and
    the interrupted function still sees its own arguments.
    """

    def setUp(self) -> None:
        self.h = CaveHarness()
        self.fn = 0x001D8000  # a "game function" the simulator will call and return from

    def test_a_call_reaches_the_function_with_its_argument(self) -> None:
        self.h.set_entries([], calls=[cave.call(self.fn, 7)])
        result = self.h.call(extra_externals=(self.fn,))
        called = [(c.callee, c.a0) for c in result.calls]
        self.assertIn((self.fn, 7), called)
        self.assertEqual(called[-1], (self.h.callee, 0), "the displaced callee still runs last")

    def test_a_call_fires_once_per_refill_not_once_per_frame(self) -> None:
        # The whole reason a call entry is consumed: a spawn function that runs at
        # 60 Hz would create sixty partners a second.
        self.h.set_entries([], calls=[cave.call(self.fn, 1)])
        first = self.h.call(extra_externals=(self.fn,))
        second = self.h.call(extra_externals=(self.fn,))
        third = self.h.call(extra_externals=(self.fn,))
        self.assertEqual([c.callee for c in first.calls].count(self.fn), 1)
        self.assertEqual([c.callee for c in second.calls].count(self.fn), 0)
        self.assertEqual([c.callee for c in third.calls].count(self.fn), 0)
        # Consumed: the function word is zeroed, which is what "empty slot" means.
        # The argument is left alone -- rewriting it would cost an instruction for
        # no information.
        self.assertEqual(self.h.call_slot(0)[0], 0)

    def test_a_refill_re_arms_a_consumed_call(self) -> None:
        self.h.set_entries([], calls=[cave.call(self.fn, 1)])
        self.h.call(extra_externals=(self.fn,))
        self.h.set_entries([], calls=[cave.call(self.fn, 2)])  # the watchdog refills
        result = self.h.call(extra_externals=(self.fn,))
        self.assertEqual([c.a0 for c in result.calls if c.callee == self.fn], [2])

    def test_an_indirect_argument_is_read_at_call_time(self) -> None:
        source = 0x00160000
        self.h.vm.write32(source, 0x1234)
        self.h.set_entries([], calls=[cave.call_read(self.fn, source)])
        result = self.h.call(extra_externals=(self.fn,))
        self.assertEqual([c.a0 for c in result.calls if c.callee == self.fn], [0x1234])

    def test_an_empty_slot_is_skipped_rather_than_ending_the_list(self) -> None:
        # Consuming a call leaves a hole, and the watchdog refills around it: a call
        # it has already made is not re-queued, but a new one is appended behind the
        # hole. If the hole ended the list, that new call would never fire.
        block = cave.param_block_at(self.h.cave) + cave.CALLS_OFFSET
        self.h.vm.write_bytes(block, cave.encode_calls([(self.fn, 1)]))
        self.h.call(extra_externals=(self.fn,))
        self.assertEqual(self.h.call_slot(0)[0], 0, "slot 0 should be consumed")
        # A refill that writes the same list minus the fired call, plus a new one.
        # The hole is hand-packed because encode_calls() refuses a zero function --
        # that refusal protects the host API, while this block simulates the *game's*
        # memory after a consumption.
        self.h.vm.write_bytes(block, struct.pack("<IIII", 0, 0, self.fn + 4, 2) + b"\x00" * 8)
        result = self.h.call(extra_externals=(self.fn, self.fn + 4))
        self.assertEqual(
            [(c.callee, c.a0) for c in result.calls if c.callee in (self.fn, self.fn + 4)],
            [(self.fn + 4, 2)],
        )

    def test_several_calls_fire_in_one_refill_in_order(self) -> None:
        calls = [cave.call(self.fn + i * 4, i) for i in range(3)]
        self.h.set_entries([], calls=calls)
        result = self.h.call(extra_externals=tuple(f for f, _ in calls))
        seen = [c.callee for c in result.calls if c.callee != self.h.callee]
        self.assertEqual(seen, [self.fn, self.fn + 4, self.fn + 8])

    def test_the_interrupted_function_still_sees_its_arguments(self) -> None:
        # The displaced function is called *by* the cave, at the end, with the
        # argument registers the game put there. A call entry that trashed them
        # would corrupt whatever the game's own per-frame function was about to do.
        self.h.set_entries([], calls=[cave.call(self.fn, 0x99)])
        self.h.vm.set_reg("a0", 0xAAAAAAAA)
        self.h.vm.set_reg("a1", 0xBBBBBBBB)
        self.h.vm.set_reg("a2", 0xCCCCCCCC)
        self.h.vm.set_reg("a3", 0xDDDDDDDD)
        result = self.h.call(extra_externals=(self.fn,))
        callee_call = [c for c in result.calls if c.callee == self.h.callee][-1]
        self.assertEqual(callee_call.a0, 0xAAAAAAAA)
        self.assertEqual(self.h.vm.regs[REG["a1"]], 0xBBBBBBBB)
        self.assertEqual(self.h.vm.regs[REG["a2"]], 0xCCCCCCCC)
        self.assertEqual(self.h.vm.regs[REG["a3"]], 0xDDDDDDDD)

    def test_the_call_region_is_bounded(self) -> None:
        # Memory past the call region is not ours: a stale block must not turn into
        # a call into whatever happens to be there.
        block = cave.param_block_at(self.h.cave) + cave.CALLS_OFFSET
        payload = bytearray()
        for i in range(cave.MAX_CALLS):
            payload += struct.pack("<II", self.fn + i * 4, i)
        self.h.vm.write_bytes(block, bytes(payload))  # no terminator, region full
        self.h.vm.write32(block + cave.MAX_CALLS * 8, self.fn + 0x1000)  # just past the end
        # Every function in the payload has to be declared, or the VM would run off
        # into unmapped code instead of returning -- which is what a real console
        # would do too, and the reason the docs insist on measuring the function.
        everything = tuple(self.fn + i * 4 for i in range(cave.MAX_CALLS)) + (self.fn + 0x1000,)
        result = self.h.call(extra_externals=everything)
        callees = [c.callee for c in result.calls]
        self.assertEqual(callees.count(self.fn), 1)
        self.assertNotIn(self.fn + 0x1000, callees)
        self.assertEqual(len([c for c in callees if self.fn <= c < self.fn + cave.MAX_CALLS * 4]), cave.MAX_CALLS)

    def test_the_layout_keeps_the_two_lists_apart(self) -> None:
        self.assertGreaterEqual(cave.CALLS_OFFSET, cave.ENTRIES_OFFSET + 8 * cave.MAX_ENTRIES)
        self.assertLessEqual(cave.CALLS_END, cave.PARAM_BLOCK_SIZE)

    def test_a_full_call_region_leaves_room_for_the_writes(self) -> None:
        # Both lists at once: 40 writes and 16 calls in one block image.
        entries = [cave.const(0x00200000 + 4 * i, i + 1) for i in range(cave.MAX_ENTRIES)]
        calls = [cave.call(self.fn + 4 * i, i) for i in range(cave.MAX_CALLS)]
        block = cave.encode_block(entries, calls=calls)
        self.assertEqual(block[cave.ENTRIES_OFFSET + 4], 1)
        self.assertEqual(block[cave.CALLS_OFFSET + 4], 0)

    def test_calls_cannot_be_smuggled_past_the_budget(self) -> None:
        too_many = [cave.call(self.fn, 0)] * (cave.MAX_CALLS + 1)
        with self.assertRaises(ValueError):
            cave.encode_calls(too_many)
        with self.assertRaises(ValueError):
            cave.encode_calls([(0, 0)])
        with self.assertRaises(ValueError):
            cave.call(self.fn, 0x80000000)
        with self.assertRaises(ValueError):
            cave.call_read(self.fn, 0x80000000)

    def test_calls_count_towards_the_applied_readout(self) -> None:
        # `applied` is the evidence that the block did something this tick, and it
        # is what the watchdog prints. A tick that only made calls must not look
        # like a tick that did nothing.
        self.h.set_entries([cave.const(0x00200000, 1)], calls=[cave.call(self.fn, 1)])
        self.h.call(extra_externals=(self.fn,))
        self.assertEqual(self.h.block()["applied"], 2)


class TestCaveShape(unittest.TestCase):
    def test_fits_the_reservation(self) -> None:
        interp = cave.build_interpreter(CAVE, SITE, CALLEE)
        self.assertLessEqual(len(interp.code), cave.CAVE_CODE_SIZE)
        self.assertEqual(len(interp.code), cave.CAVE_CODE_SIZE)  # padded to a fixed offset

    def test_parameter_block_does_not_overlap_code(self) -> None:
        interp = cave.build_interpreter(CAVE, SITE, CALLEE)
        self.assertEqual(interp.param_block, CAVE + cave.CAVE_CODE_SIZE)
        self.assertGreaterEqual(interp.param_block, CAVE + len(interp.code))

    def test_trampoline_is_one_jal(self) -> None:
        interp = cave.build_interpreter(CAVE, SITE, CALLEE)
        self.assertEqual(len(interp.trampoline), 4)
        word = struct.unpack("<I", interp.trampoline)[0]
        self.assertEqual(word >> 26, 0x03)
        self.assertEqual(cave.callee_of_jal(word, SITE), CAVE)

    def test_callee_is_decoded_from_the_displaced_instruction(self) -> None:
        word = struct.unpack("<I", mips.to_bytes([mips.jal(0x001D0000)]))[0]
        self.assertEqual(cave.callee_of_jal(word, SITE), 0x001D0000)
        with self.assertRaises(ValueError):
            cave.callee_of_jal(0x00000000, SITE)

    def test_alignment_is_required(self) -> None:
        with self.assertRaises(ValueError):
            cave.build_interpreter(CAVE + 2, SITE, CALLEE)

    def test_too_many_entries_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            cave.encode_entries([cave.const(0x1000 * i + 4, 1) for i in range(cave.MAX_ENTRIES + 1)])


class TestCaveSemantics(unittest.TestCase):
    def setUp(self) -> None:
        self.h = CaveHarness()

    def test_installs_and_returns_cleanly(self) -> None:
        self.h.set_entries([])
        result = self.h.call()
        self.assertEqual(result.halted, "stop-at")
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0].callee, CALLEE)

    def test_only_caller_saved_registers_are_clobbered(self) -> None:
        # A hook that leans on $s0-$s7, $gp or $fp would corrupt whichever
        # function the game was in the middle of.
        for name in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "gp", "fp"):
            self.h.vm.set_reg(name, 0x1000 + REG[name])
        self.h.set_entries([cave.const(0x00210000, 1)])
        self.h.call()
        for name in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "gp", "fp"):
            self.assertEqual(self.h.vm.get_reg(name), 0x1000 + REG[name], name)

    def test_temporaries_are_fair_game(self) -> None:
        # $t0..$t9 are caller-saved; a callee may clobber them, so the cave may too.
        self.h.vm.set_reg("t0", 0xDEADBEEF)
        self.h.set_entries([cave.const(0x00210000, 1)])
        self.h.call()
        self.assertNotEqual(self.h.vm.get_reg("t0"), 0xDEADBEEF)

    def test_sp_and_ra_are_restored(self) -> None:
        self.h.call()
        self.assertEqual(self.h.vm.get_reg("sp"), 0x001FF000)
        self.assertEqual(self.h.vm.get_reg("ra"), SITE + 8)

    def test_arguments_are_left_alone(self) -> None:
        # The displaced function may be called with arguments the cave must not
        # disturb; $a0-$a3 are passed straight through.
        for name, value in (("a0", 0x1111), ("a1", 0x2222), ("a2", 0x3333), ("a3", 0x4444)):
            self.h.vm.set_reg(name, value)
        self.h.set_entries([cave.const(0x00210000, 1)])
        self.h.call()
        for name, value in (("a0", 0x1111), ("a1", 0x2222), ("a2", 0x3333), ("a3", 0x4444)):
            self.assertEqual(self.h.vm.get_reg(name), value, name)

    def test_constants_are_written_every_pass(self) -> None:
        addr = 0x00210000
        self.h.set_entries([cave.const(addr, 7)])
        self.h.call()
        self.assertEqual(self.h.vm.read32(addr), 7)
        self.h.vm.write32(addr, 99)  # the engine "rewrites" it
        self.h.call()
        self.assertEqual(self.h.vm.read32(addr), 7)

    def test_indirect_entries_copy_inside_the_cave(self) -> None:
        src, dst = 0x00200000, 0x00200040
        self.h.vm.write32(src, 0x11223344)
        self.h.set_entries([cave.copy(dst, src)])
        self.h.call()
        self.assertEqual(self.h.vm.read32(dst), 0x11223344)
        self.h.vm.write32(src, 0x55667788)
        self.h.call()
        self.assertEqual(self.h.vm.read32(dst), 0x55667788)

    def test_a_copy_and_a_constant_with_the_same_target_behave_differently(self) -> None:
        # The tether only works because this distinction is exact.
        src, dst = 0x00200000, 0x00200040
        self.h.vm.write32(src, 0xAAAAAAAA)
        self.h.set_entries([cave.copy(dst, src)])
        self.h.call()
        self.assertEqual(self.h.vm.read32(dst), 0xAAAAAAAA)
        self.h.set_entries([cave.const(dst, 0x11111111)])
        self.h.call()
        self.assertEqual(self.h.vm.read32(dst), 0x11111111)

    def test_tick_advances_once_per_call(self) -> None:
        self.h.set_entries([])
        for expected in (1, 2, 3):
            self.h.call()
            self.assertEqual(self.h.block()["ticks"], expected)

    def test_applied_count_is_reported(self) -> None:
        self.h.set_entries([cave.const(0x00210000 + 4 * i, i) for i in range(5)])
        self.h.call()
        self.assertEqual(self.h.block()["applied"], 5)

    def test_empty_block_is_a_clean_no_op(self) -> None:
        self.h.set_entries([])
        self.h.vm.write32(0x00210000, 0x1234)
        self.h.call()
        self.assertEqual(self.h.vm.read32(0x00210000), 0x1234)
        self.assertEqual(self.h.block()["applied"], 0)

    def test_budget_is_enforced(self) -> None:
        # `encode_block` writes the budget into the header, so set the entries
        # first and then tighten the budget the cave will read.
        self.h.set_entries([cave.const(0x00210000 + 4 * i, i + 1) for i in range(6)])
        self.h.vm.write32(cave.param_block_at(CAVE) + 0x08, 3)
        self.h.call()
        self.assertEqual(self.h.block()["applied"], 3)
        self.assertEqual(self.h.vm.read32(0x00210000), 1)
        self.assertEqual(self.h.vm.read32(0x00210000 + 8), 3)
        self.assertEqual(self.h.vm.read32(0x00210000 + 12), 0)  # not reached

    def test_a_zero_address_terminates_the_list_early(self) -> None:
        # Defensive: a half-written block must not make the cave run off the end.
        block = cave.param_block_at(CAVE)
        self.h.vm.write_bytes(block + 0x10, struct.pack("<III", 0x00210000, 5, 0) + b"\x00" * 4)
        self.h.vm.write_bytes(block + 0x28, struct.pack("<II", 0x00210004, 9))
        self.h.vm.write32(block + 0x08, cave.MAX_ENTRIES)
        self.h.call()
        self.assertEqual(self.h.vm.read32(0x00210000), 5)
        self.assertEqual(self.h.vm.read32(0x00210004), 0)

    def test_magic_and_budget_are_readable(self) -> None:
        block = self.h.block()
        self.assertTrue(block["magic_ok"])
        self.assertEqual(block["budget"], cave.MAX_ENTRIES)

    def test_wrong_magic_is_detectable(self) -> None:
        self.h.vm.write32(cave.param_block_at(CAVE), 0x41414141)
        self.assertFalse(self.h.block()["magic_ok"])

    def test_entry_encoding_is_eight_bytes(self) -> None:
        self.assertEqual(len(cave.encode_entries([cave.const(0x1000, 1)])), 16)  # one entry + terminator
        self.assertEqual(len(cave.encode_entries([])), 8)

    def test_indirect_flag_helpers(self) -> None:
        _, value = cave.copy(0x2000, 0x1000)
        self.assertTrue(cave.entry_is_indirect(value))
        self.assertEqual(cave.entry_source(value), 0x1000)
        _, constant = cave.const(0x2000, 5)
        self.assertFalse(cave.entry_is_indirect(constant))


class TestFreeRegion(unittest.TestCase):
    """``find_free_region`` runs against the simulator's RAM through a stub client."""

    def _client(self, vm: R5900VM):
        class Stub:
            def __init__(self, vm: R5900VM) -> None:
                self.vm = vm

            def read_bytes(self, addr: int, length: int) -> bytes:
                return self.vm.read_bytes(addr, length)

        return Stub(vm)

    def test_finds_a_zero_run(self) -> None:
        vm = R5900VM()
        vm.write32(0x00100000, 0x12345678)
        client = self._client(vm)
        addr = cave.find_free_region(client, 0x400, [(0x00100000, 0x00300000)])
        self.assertGreaterEqual(addr, 0x00100000)
        self.assertEqual(vm.read_bytes(addr, 0x400), b"\x00" * 0x400)

    def test_skips_used_memory(self) -> None:
        vm = R5900VM()
        vm.write_bytes(0x00100000, b"\xAA" * 0x1000)
        client = self._client(vm)
        addr = cave.find_free_region(client, 0x100, [(0x00100000, 0x00300000)])
        self.assertGreaterEqual(addr, 0x00101000)

    def test_alignment_is_honoured(self) -> None:
        vm = R5900VM()
        vm.write_bytes(0x00100000, b"\x00\x00\x00\x00\xAA")
        client = self._client(vm)
        addr = cave.find_free_region(client, 0x40, [(0x00100000, 0x00300000)], alignment=16)
        self.assertEqual(addr % 16, 0)

    def test_reports_when_there_is_no_room(self) -> None:
        vm = R5900VM()
        vm.write_bytes(0x00100000, b"\xFF" * 0x2000)
        client = self._client(vm)
        with self.assertRaises(LookupError):
            cave.find_free_region(client, 0x4000, [(0x00100000, 0x00102000)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
