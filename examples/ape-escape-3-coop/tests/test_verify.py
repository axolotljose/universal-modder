"""Pre-flight: the worksheet checked against the game, before anything is written.

Finding a number and trusting a number are different jobs. This module is the second
one, and it earns its place by catching the failure that is worst to debug: a
transposed hex digit, a base off by one slot, an offset that is really the next field.
Those install without complaint and then write into whatever actually lives at that
address.

Every check is exercised in both directions here -- a good worksheet passes, and each
specific way of being wrong is caught -- because a checker that cannot fail is
decoration.
"""

from __future__ import annotations

import unittest

from aecoops import verify
from aecoops.addresses import AddressTable, CallSpec, Discovery, Field, Struct
from aecoops.cli import main

from .gamesim import FakeAEC3, make_table


class WordReader:
    """A memory image: enough for every check, and it cannot be written through."""

    def __init__(self, words: dict[int, int] | None = None, ram: bytes | None = None) -> None:
        self.words = words or {}
        self.ram = ram

    def read32(self, addr: int) -> int:
        return self.words.get(addr, 0)


def good_table(stage_or_none=None) -> AddressTable:
    """A worksheet that agrees with the simulated console (or with numbers we choose)."""
    if stage_or_none is not None:
        return make_table(stage_or_none)
    table = AddressTable.load()
    table.globals["frame_hook"].addr = 0x00123450
    table.globals["view_mode"].addr = 0x00110010
    table.globals["actor_count"].addr = 0x00110014
    table.globals["partner_enabled"].addr = 0x00110018
    table.globals["hud_instances"].addr = 0x00110024
    table.globals["viewport_count"].addr = 0x00110028
    table.layouts = {"single": 0, "vertical": 3, "horizontal": 1}
    table.characters = {"kei": 0, "yumi": 1}
    table.structs["actor"] = Struct(
        name="actor",
        base=Discovery(name="actor.base", addr=0x00180000),
        stride=0x100,
        count=2,
        fields={
            "active": Field("active", 0x00),
            "char_id": Field("char_id", 0x04),
            "pad_port": Field("pad_port", 0x08),
            "pos_x": Field("pos_x", 0x10),
            "pos_y": Field("pos_y", 0x14),
            "pos_z": Field("pos_z", 0x18),
        },
    )
    table.calls = [CallSpec(name="spawn_partner", addr=0x00160000, arg=1)]
    return table


def good_words() -> dict[int, int]:
    return {
        0x00123450: 0x0C048D14,  # jal 0x00123450 -> some function
        0x00110010: 0,  # view_mode, single
        0x00110014: 1,  # actor_count
        0x00110018: 0,  # partner_enabled
        0x00110024: 1,  # one HUD
        0x00110028: 1,  # one viewport
        0x00180004: 0,  # slot 0 char_id = kei
        0x00180008: 0,  # slot 0 pad_port
        0x00160000: 0x27BDFFE8,  # addiu $sp,$sp,-0x18: something that looks like code
    }


class TestAGoodWorksheetPasses(unittest.TestCase):
    def test_a_consistent_table_reports_no_problems(self) -> None:
        report = verify.check_table(WordReader(good_words()), good_table())
        self.assertEqual([f.message for f in report.problems], [])
        self.assertGreater(report.checked, 8)
        self.assertIn("consistent", report.text())

    def test_nothing_measured_is_not_a_problem(self) -> None:
        report = verify.check_table(WordReader(), AddressTable.load())
        self.assertTrue(report.skipped)
        self.assertEqual(report.problems, [])
        self.assertIn("nothing measured yet", report.text())

    def test_a_half_measured_table_checks_what_it_has(self) -> None:
        table = good_table()
        table.structs["viewport"] = Struct(name="viewport", base=Discovery(name="viewport.base"))
        report = verify.check_table(WordReader(good_words()), table)
        self.assertEqual(report.problems, [])
        self.assertTrue(any(f.level == verify.SKIP for f in report.findings))


class TestEachWayOfBeingWrongIsCaught(unittest.TestCase):
    def setUp(self) -> None:
        self.table = good_table()
        self.words = good_words()

    def _report(self) -> verify.Report:
        return verify.check_table(WordReader(self.words), self.table)

    def test_a_hook_that_is_not_a_jal(self) -> None:
        # The classic: an address a few bytes off lands inside the function instead of
        # on the call to it.
        self.words[0x00123450] = 0x27BDFFE8
        problems = self._report().problems
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("not a jal", problems[0].message)

    def test_a_character_id_that_contradicts_the_actor_table(self) -> None:
        # The strongest check in the module: two measurements that have to agree.
        self.words[0x00180004] = 42
        problems = self._report().problems
        self.assertTrue(any("neither measured hero id" in p.message for p in problems), problems)

    def test_a_struct_base_off_by_a_slot(self) -> None:
        # Slot 0 read at the wrong place. The word at base+4 is whatever lives there,
        # which in general is neither hero id -- but it is not *always*, so the check
        # that catches a wrongly-placed base in every case is the pad port: a pad port
        # has exactly two legal values.
        self.table.structs["actor"].base.addr = 0x00180100  # accidentally slot 1
        self.words[0x00180104] = 0x100000  # what the next structure along holds
        self.words[0x00180108] = 0x55667788  # ...and it is not a pad port
        problems = self._report().problems
        self.assertTrue(any("not a pad port" in p.message for p in problems), problems)

    def test_a_struct_base_off_by_a_slot_is_not_always_visible(self) -> None:
        # Honest bound on the check: an accidentally-placed base whose words happen to
        # look plausible passes. The recipe's answer is to verify with two saves, one
        # per hero, which makes slot 0 say two different things at the same address.
        self.table.structs["actor"].base.addr = 0x00180000 + 0x100
        self.words[0x00180104] = 0  # "kei" again, by coincidence
        self.words[0x00180108] = 0  # pad 0, by coincidence
        report = self._report()
        self.assertTrue(any(p.level == verify.OK and "slot 0 says" in p.message for p in report.findings))

    def test_a_stride_of_zero(self) -> None:
        self.table.structs["actor"].stride = 0
        problems = self._report().problems
        self.assertTrue(any("stride of 0" in p.message for p in problems), problems)

    def test_a_pad_port_that_is_not_a_pad(self) -> None:
        self.words[0x00180008] = 7
        problems = self._report().problems
        self.assertTrue(any("not a pad port" in p.message for p in problems), problems)

    def test_two_layouts_with_the_same_value(self) -> None:
        self.table.layouts = {"single": 0, "vertical": 3, "horizontal": 3}
        problems = self._report().problems
        self.assertTrue(any("both 3" in p.message for p in problems), problems)

    def test_a_call_into_empty_memory(self) -> None:
        del self.words[0x00160000]
        problems = self._report().problems
        self.assertTrue(any("all zeros" in p.message for p in problems), problems)

    def test_two_calls_at_the_same_function(self) -> None:
        self.table.calls.append(CallSpec(name="also_spawn", addr=0x00160000, arg=1))
        problems = self._report().problems
        self.assertTrue(any("copy-paste" in p.message for p in problems), problems)

    def test_a_call_outside_ram(self) -> None:
        self.table.calls[0].addr = 0x12345678
        problems = self._report().problems
        self.assertTrue(any("not a word-aligned EE address" in p.message for p in problems), problems)

    def test_a_count_that_cannot_mean_what_it_says(self) -> None:
        # Not a hard failure: the address may be right and the meaning wrong, which is
        # a question for the person holding the pad.
        self.words[0x00110014] = 900
        report = self._report()
        self.assertEqual(report.problems, [])
        self.assertTrue(any(f.level == verify.NOTE and "outside the 1..2" in f.message for f in report.findings))

    def test_a_view_mode_the_renderer_is_not_using(self) -> None:
        self.words[0x00110010] = 77
        report = self._report()
        self.assertEqual(report.problems, [])
        self.assertTrue(any("not one of the measured layouts" in f.message for f in report.findings))

    def test_a_rectangle_off_the_end_of_the_screen(self) -> None:
        self.table.structs["viewport"] = Struct(
            name="viewport",
            base=Discovery(name="viewport.base", addr=0x00183000),
            stride=0x20,
            count=2,
            fields={
                "x": Field("x", 0x00),
                "y": Field("y", 0x04),
                "w": Field("w", 0x08),
                "h": Field("h", 0x0C),
            },
        )
        self.words.update({0x00183000: 0, 0x00183004: 0, 0x00183008: 4000, 0x0018300C: 448})
        report = self._report()
        self.assertEqual(report.problems, [])
        self.assertTrue(any("does not fit" in f.message for f in report.findings), report.text())

    def test_an_unreadable_address_is_reported_rather_than_skipped(self) -> None:
        class Deaf(WordReader):
            def read32(self, addr: int) -> int:
                if addr == 0x00110010:
                    raise OSError("PINE said no")
                return super().read32(addr)

        report = verify.check_table(Deaf(good_words()), self.table)
        self.assertTrue(any("could not be read" in p.message for p in report.problems), report.text())


class TestAgainstTheSimulatedConsole(unittest.TestCase):
    """With the fake console driving a fake stage, the check runs on real reads."""

    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.table = make_table(self.sim.stage)

    def tearDown(self) -> None:
        self.sim.close()

    def test_the_simulator_s_own_table_is_consistent(self) -> None:
        # Whatever the fake console is doing, a table built against it must describe
        # it: this is the check that the checker itself is not lying.
        self.sim.vm.write32(self.sim.stage.hook_site, 0x0C048D14)
        report = verify.check_table(self.sim.vm, self.table)
        self.assertEqual([p.message for p in report.problems], [], report.text())

    def test_a_character_id_that_disagrees_with_the_engine_is_caught(self) -> None:
        self.sim.set_engine_char(0)
        self.table.characters["kei"] = 5  # not what the stage has in slot 0
        report = verify.check_table(self.sim.vm, self.table)
        self.assertTrue(any("neither measured hero id" in p.message for p in report.problems), report.text())


class TestTheVerifyCommand(unittest.TestCase):
    def test_it_refuses_cleanly_without_pine(self) -> None:
        import contextlib
        import io
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "addresses.json"
            good_table().save(table)
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                code = main(["--slot", "28099", "--table", str(table), "verify"])
            self.assertEqual(code, 2)
            self.assertIn("PINE is not usable", out.getvalue())

    def test_it_checks_a_live_console_and_exits_zero(self) -> None:
        import contextlib
        import io
        import tempfile
        from pathlib import Path

        sim = FakeAEC3()
        try:
            sim.vm.write32(sim.stage.hook_site, 0x0C048D14)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "addresses.json"
                make_table(sim.stage).save(path)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(["--slot", str(sim.port), "--table", str(path), "verify"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("consistent", out.getvalue())
        finally:
            sim.close()

    def test_it_exits_one_when_the_worksheet_is_wrong(self) -> None:
        import contextlib
        import io
        import tempfile
        from pathlib import Path

        sim = FakeAEC3()
        try:
            table = make_table(sim.stage)
            table.characters["kei"] = 9  # contradicts the running stage
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "addresses.json"
                table.save(path)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(["--slot", str(sim.port), "--table", str(path), "verify"])
            self.assertEqual(code, 1, out.getvalue())
            self.assertIn("problem(s) above", out.getvalue())
        finally:
            sim.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
