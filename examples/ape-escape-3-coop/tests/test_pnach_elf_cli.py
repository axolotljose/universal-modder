"""The standalone patch export, the ELF reader, and the command line."""

from __future__ import annotations

import contextlib
import io
import struct
import tempfile
import threading
import unittest.mock
import time
import unittest
from pathlib import Path

from aecoops import cave, elf, mips, pnach
from aecoops.cli import main
from aecoops.pine import Pine

from .fakepine import FakePCSX2
from .gamesim import FakeAEC3, make_table


class TestPnach(unittest.TestCase):
    def setUp(self) -> None:
        self.site = 0x00123450
        self.cave_addr = 0x00140000
        self.callee = 0x001D0000
        self.text = pnach.build_from_words(
            site=self.site,
            site_word=mips.jal(self.callee),
            cave_addr=self.cave_addr,
            gametitle="Ape Escape 3",
            serial="SCUS-97501",
            crc="a1b2c3d4",
            comment="co-op split screen: player one Kei, player two Yumi.",
            entries=[cave.const(0x00210000, 2), cave.copy(0x00210004, 0x00200000)],
        )

    def test_header(self) -> None:
        self.assertIn("gametitle=Ape Escape 3 [SCUS-97501]", self.text)
        self.assertIn("DISC CRC A1B2C3D4", self.text.upper())
        self.assertIn("// co-op split screen", self.text)

    def test_trampoline_line(self) -> None:
        self.assertIn(f"patch=1,EE,{self.site:08X},word,0x{mips.jal(self.cave_addr):08X}", self.text)

    def test_extended_lines_carry_the_cave(self) -> None:
        interp = cave.build_interpreter(self.cave_addr, self.site, self.callee)
        words = [int(w, 16) for w in _extended_words(self.text, self.cave_addr)]
        self.assertEqual(
            struct.pack(f"<{len(words)}I", *words),
            interp.code[: 4 * len(words)],
        )

    def test_extended_lines_carry_the_parameter_block(self) -> None:
        block = cave.param_block_at(self.cave_addr)
        words = [int(w, 16) for w in _extended_words(self.text, block)]
        self.assertEqual(words[0], int.from_bytes(cave.MAGIC, "little"))
        self.assertEqual(words[1], 0)  # tick starts at zero
        self.assertEqual(words[2], cave.MAX_ENTRIES)

    def test_every_patch_line_parses(self) -> None:
        for line in self.text.splitlines():
            if not line.startswith("patch="):
                continue
            parts = line.split(",")
            self.assertEqual(len(parts), 5, line)
            self.assertIn(parts[0], ("patch=1", "patch=0"))
            self.assertEqual(parts[1], "EE")
            int(parts[2], 16)
            self.assertIn(parts[3], ("word", "extended", "byte", "short"))

    def test_explain_summarises(self) -> None:
        summary = pnach.explain(self.text)
        self.assertIn("word", summary)
        self.assertIn("extended", summary)

    def test_name_convention(self) -> None:
        self.assertEqual(pnach.pnach_name("SCUS-97501", "A1B2C3D4"), "A1B2C3D4.pnach")
        self.assertEqual(pnach.pnach_name("SCUS-97501"), "SCUS97501.pnach")

    def test_write_creates_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cheats" / "game.pnach"
            pnach.write(self.text, target)
            self.assertTrue(target.exists())

    def test_config_entries_use_indirect_for_the_tether(self) -> None:
        entries = pnach.make_entries_from_config(
            split_value=1,
            actor_count_addr=0x00110014,
            partner_addr=0x00110018,
            view_mode_addr=0x00110010,
            char_ids=[(0x00180004, 0), (0x00180104, 1)],
            tether=[(0x00180110, 0x00180010)],
        )
        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0], cave.const(0x00110010, 1))
        self.assertTrue(cave.entry_is_indirect(entries[-1][1]))

    def test_unresolved_addresses_are_simply_absent(self) -> None:
        entries = pnach.make_entries_from_config(
            split_value=0, actor_count_addr=None, partner_addr=None, view_mode_addr=None
        )
        self.assertEqual(entries, [])

    def test_a_non_word_block_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            pnach.PnachEntry(0x1000, b"\x01\x02\x03").lines()


class TestPnachIsFrozenConfiguration(unittest.TestCase):
    """The point of the export: co-op with no host process running at all."""

    def test_pnach_carries_exactly_what_the_watchdog_would_write(self) -> None:
        sim = FakeAEC3()
        pine = Pine(slot=sim.port, timeout=5.0).connect()
        try:
            table = make_table(sim.stage)
            from aecoops.coop import CoopConfig, CoopMod, Split

            mod = CoopMod(pine, table, CoopConfig(split=Split.HORIZONTAL))
            mod.install(cave_addr=sim.stage.cave)
            live = mod.entries()
            frozen = pnach.make_entries_from_config(
                split_value=table.layout_id("horizontal"),
                actor_count_addr=table.globals["actor_count"].addr,
                partner_addr=table.globals["partner_enabled"].addr,
                view_mode_addr=table.globals["view_mode"].addr,
                char_ids=[],
            )
            addresses = {addr for addr, _ in live}
            for addr, value in frozen:
                self.assertIn(addr, addresses)
                self.assertEqual(dict(live)[addr], value)
        finally:
            pine.close()
            sim.close()


class TestElf(unittest.TestCase):
    def _elf(self, symbols: list[tuple[str, int, int]]) -> bytes:
        """Hand-build a minimal little-endian ELF32 with a symtab and a strtab.

        Building it here rather than shipping a binary keeps the test honest about
        what the parser is looking at, and keeps any real game image out of the
        repository. Section order is: 0 null, 1 .text, 2 .strtab, 3 .symtab,
        4 .shstrtab -- the symtab points at the strtab through sh_link, which is
        the part a careless parser gets wrong and then reads section names as
        symbol names.
        """
        strtab = b"\x00"
        entries = [b"\x00" * 16]
        for name, value, size in symbols:
            offset = len(strtab)
            strtab += name.encode() + b"\x00"
            entries.append(struct.pack("<IIIBBH", offset, value, size, (1 << 4) | 2, 0, 1))
        symtab = b"".join(entries)
        shstrtab = b"\x00.shstrtab\x00.symtab\x00.strtab\x00.text\x00"
        text = b"\xAA" * 16

        text_off = 52
        strtab_off = text_off + len(text)
        sym_off = strtab_off + len(strtab)
        shstr_off = sym_off + len(symtab)
        sh_off = shstr_off + len(shstrtab)

        def section(name_off: int, stype: int, flags: int, addr: int, offset: int, size: int,
                    link: int = 0, info: int = 0, align: int = 4, entsize: int = 0) -> bytes:
            return struct.pack(
                "<IIIIIIIIII", name_off, stype, flags, addr, offset, size, link, info, align, entsize
            )

        sections = section(0, 0, 0, 0, 0, 0, align=0)
        sections += section(0x1B, 1, 6, 0x00100000, text_off, len(text))          # .text
        sections += section(0x13, 3, 0, 0, strtab_off, len(strtab), align=1)      # .strtab
        sections += section(0x0B, 2, 0, 0, sym_off, len(symtab), link=2, info=1, entsize=16)  # .symtab
        sections += section(0x01, 3, 0, 0, shstr_off, len(shstrtab), align=1)     # .shstrtab

        header = struct.pack(
            "<16sHHIIIIIHHHHHH",
            b"\x7fELF\x01\x01\x01" + b"\x00" * 9,
            2,      # ET_EXEC
            8,      # EM_MIPS
            1,      # version
            0x00100000,
            0,      # no program headers
            sh_off,
            0,
            52,
            0,
            0,
            40,
            5,      # e_shnum
            4,      # e_shstrndx
        )
        return header + text + strtab + symtab + shstrtab + sections

    def setUp(self) -> None:
        self.raw = self._elf(
            [("stage_update", 0x00140000, 0x120), ("draw_scene", 0x00150000, 0x300), ("tiny", 0x00160000, 4)]
        )

    def test_parse_symbols(self) -> None:
        image = elf.parse(self.raw, "fake.elf")
        names = [s.name for s in image.symbols if s.name]
        self.assertIn("stage_update", names)
        self.assertEqual(image.machine, 8)
        self.assertEqual(image.entry, 0x00100000)

    def test_functions_filter_by_size_and_type(self) -> None:
        image = elf.parse(self.raw, "fake.elf")
        funcs = image.functions(min_size=0x10)
        self.assertEqual([f.name for f in funcs], ["stage_update", "draw_scene"])
        self.assertIn("fake.elf", image.summary())

    def test_objects_are_separate_from_functions(self) -> None:
        image = elf.parse(self.raw, "fake.elf")
        self.assertEqual(image.objects(), [])

    def test_rejects_non_elf(self) -> None:
        with self.assertRaises(elf.ElfError):
            elf.parse(b"not an elf at all")

    def test_rejects_big_endian_and_64_bit(self) -> None:
        with self.assertRaises(elf.ElfError):
            elf.parse(self.raw[:5] + b"\x02" + self.raw[6:])
        with self.assertRaises(elf.ElfError):
            elf.parse(self.raw[:4] + b"\x02" + self.raw[5:])

    def test_a_sectionless_image_says_so(self) -> None:
        stripped = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + b"\x00" * 9, 2, 8, 1, 0, 0, 0, 0, 52, 0, 0, 40, 0, 0)
        with self.assertRaises(elf.ElfError) as caught:
            elf.parse(stripped)
        self.assertIn("Ghidra", str(caught.exception))

    def test_ghidra_function_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "funcs.csv"
            path.write_text("name,address,size\nstage_update,00140000,120\ndraw,00150000,300\n")
            entries = elf.read_ghidra_function_map(path)
            self.assertEqual(entries, [("stage_update", 0x00140000), ("draw", 0x00150000)])


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakePCSX2()
        self.sim = FakeAEC3()
        self.sim.pcsx2.close()  # unused: the CLI talks to self.fake
        self.args = ["--slot", str(self.fake.port)]

    def tearDown(self) -> None:
        self.fake.close()
        self.sim.close()

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main([*self.args, *argv])
        return code, out.getvalue()

    def test_doctor_reports_the_unresolved_table_and_pine(self) -> None:
        code, output = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("NOT FOUND YET", output)
        self.assertIn("frame_hook", output)
        self.assertIn("PINE ok", output)
        self.assertIn("SCUS-97501", output)

    def test_doctor_warns_about_the_wrong_game(self) -> None:
        self.fake.title = "Some Other Game"
        code, output = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("does not look like Ape Escape 3", output)

    def test_info_prints_disc_details(self) -> None:
        code, output = self.run_cli("info")
        self.assertEqual(code, 0)
        self.assertIn("SCUS-97501", output)
        self.assertIn("a1b2c3d4".upper() if "A1B2C3D4" in output else "a1b2c3d4", output)

    def test_selfcheck_passes_without_a_console(self) -> None:
        # selfcheck is the one command that must work with no emulator at all.
        code, output = self.run_cli("selfcheck")
        self.assertEqual(code, 0, output)
        self.assertIn("passed", output)

    def test_find_free(self) -> None:
        code, output = self.run_cli("find-free", "--size", "0x300")
        self.assertEqual(code, 0)
        self.assertIn("stable=True", output)

    def test_install_without_a_hook_refuses(self) -> None:
        # With an empty worksheet there is nothing to hook, and the error has to
        # say which field is missing rather than failing somewhere deeper.
        code, output = self.run_cli("install")
        self.assertEqual(code, 1)
        self.assertIn("frame_hook", output)
        self.assertIn("DISCOVERY.md", output)

    def test_missing_emulator_is_reported_cleanly(self) -> None:
        closed = FakePCSX2()
        port = closed.port
        closed.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(["--slot", str(port), "info"])
        self.assertEqual(code, 2)
        self.assertIn("no PINE endpoint answered", out.getvalue())

    def test_parser_requires_a_command(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([])

    def test_split_choices_are_validated(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["install", "--split", "diagonal"])


class TestCliAgainstSimulatedConsole(unittest.TestCase):
    """The full path a user takes: install, then play, against a fake console."""

    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.table = make_table(self.sim.stage)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "addresses.json"
        self.table.save(self.path)

    def tearDown(self) -> None:
        self.sim.close()
        self.tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        args = ["--slot", str(self.sim.port), "--table", str(self.path), *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(args)
        return code, out.getvalue()

    def test_install_then_play_then_eject(self) -> None:
        journal = str(Path(self.tmp.name) / "journal.json")
        code, output = self.run_cli(
            "install",
            "--cave",
            hex(self.sim.stage.cave),
            "--p1",
            "yumi",
            "--p2",
            "kei",
            "--journal",
            journal,
        )
        self.assertEqual(code, 0, output)
        self.assertTrue(self.sim.cave_installed)
        self.assertIn("cave installed", output)

        # The watchdog loop needs the game to run; drive a few frames first so the
        # hook reports a live tick.
        self.sim.frame(2)
        code, output = self.run_cli(
            "play",
            "--cave",
            hex(self.sim.stage.cave),
            "--p1",
            "yumi",
            "--p2",
            "kei",
            "--duration",
            "0.3",
            "--hz",
            "20",
        )
        self.assertEqual(code, 0, output)
        self.assertIn("stopped after", output)
        self.sim.frame(2)
        self.assertEqual(self.sim.read(self.sim.stage.actor_count), 2)
        self.assertEqual(self.sim.actor(0)["char_id"], self.table.character_id("yumi"))
        self.assertEqual(self.sim.actor(1)["char_id"], self.table.character_id("kei"))

    def test_discover_poke_can_call_a_function_of_the_game(self) -> None:
        # The CLI spelling of the call route: a candidate that has to be *run*
        # rather than written, tried once, with everything put back afterwards.
        fn = 0x00160000
        source = 0x00110020  # a word the fake engine leaves alone
        self.sim.stage.external = (fn,)
        self.sim.vm.write32(source, 0x77)
        stop = threading.Event()

        def drive() -> None:
            while not stop.is_set():
                self.sim.frame(1)
                time.sleep(1.0 / 120.0)

        driver = threading.Thread(target=drive, daemon=True)
        driver.start()
        try:
            code, output = self.run_cli(
                "discover",
                "poke",
                "--site",
                hex(self.sim.stage.hook_site),
                "--cave",
                hex(self.sim.stage.cave),
                "--call-read",
                f"{fn:#x}={source:#x}",
                "--duration",
                "0.4",
            )
        finally:
            stop.set()
            driver.join(timeout=2.0)
        self.assertEqual(code, 0, output)
        self.assertIn("call", output)
        # Once, not once per frame -- and the argument was read inside the game.
        self.assertEqual(self.sim.called(fn), [0x77], output)
        self.assertFalse(self.sim.cave_installed)
        self.assertEqual(
            self.sim.vm.read_bytes(self.sim.stage.cave, cave.CAVE_CODE_SIZE),
            b"\x00" * cave.CAVE_CODE_SIZE,
        )

    def test_discover_try_walks_a_diff_and_records_the_winner(self) -> None:
        # The loop that ends every recipe on this route: two snapshots in, a candidate
        # list out, one poke per candidate, one question per poke. The answers are
        # scripted here because a human supplies them in real life.
        from aecoops import discover
        from aecoops.scan import EE_RAM

        before = discover.Snapshot(ranges=(EE_RAM,), words={0x00110010: 0, 0x00110014: 1, 0x00110020: 9})
        after = discover.Snapshot(ranges=(EE_RAM,), words={0x00110010: 3, 0x00110014: 2, 0x00110020: 9})
        left = Path(self.tmp.name) / "before.json"
        right = Path(self.tmp.name) / "after.json"
        before.save(left)
        after.save(right)

        stop = threading.Event()

        def drive() -> None:
            # At frame rate, so the tick verdicts in the output are the real thing.
            while not stop.is_set():
                self.sim.frame(1)
                time.sleep(1.0 / 60.0)

        driver = threading.Thread(target=drive, daemon=True)
        driver.start()
        try:
            with unittest.mock.patch("builtins.input", side_effect=["n", "y"]):
                code, output = self.run_cli(
                    "discover",
                    "try",
                    "--from-diff",
                    str(left),
                    str(right),
                    "--cave",
                    hex(self.sim.stage.cave),
                    "--site",
                    hex(self.sim.stage.hook_site),
                    "--seconds",
                    "0.15",
                )
        finally:
            stop.set()
            driver.join(timeout=2.0)
        self.assertEqual(code, 0, output)
        self.assertIn("2 candidate(s) from the diff", output)
        # Each candidate is named as it is tried -- including the one that did not
        # work, so the log of a run is the record of what was ruled out.
        self.assertIn("trying 0x00110010 = 3", output)
        self.assertIn("trying 0x00110014 = 2", output)
        # The diff's "after" side is what gets written, and the second one won.
        self.assertIn("0x00110014 = 2 (candidate 2)", output)
        self.assertIn("set globals.view_mode.addr=0x00110014", output)
        self.assertFalse(self.sim.cave_installed)

    def test_discover_try_needs_something_to_try(self) -> None:
        code, output = self.run_cli("discover", "try", "--cave", hex(self.sim.stage.cave))
        self.assertEqual(code, 2, output)
        self.assertIn("nothing to try", output)

    def test_discover_try_refuses_without_a_frame_hook(self) -> None:
        # No hook in the table and no --site means there is nothing to poke through,
        # and the message says where the hook comes from rather than failing obscurely.
        table = make_table(self.sim.stage)
        table.globals["frame_hook"].addr = None
        path = Path(self.tmp.name) / "nohook.json"
        table.save(path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(
                [
                    "--slot",
                    str(self.sim.port),
                    "--table",
                    str(path),
                    "discover",
                    "try",
                    "--candidate",
                    "0x110020=1",
                ]
            )
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("no frame hook", out.getvalue())
        self.assertIn("DISCOVERY.md#frame-hook", out.getvalue())

    def test_status_and_eject(self) -> None:
        self.run_cli(
            "install",
            "--cave",
            hex(self.sim.stage.cave),
            "--journal",
            str(Path(self.tmp.name) / "journal.json"),
        )
        self.sim.frame(1)
        code, output = self.run_cli("status", "--cave", hex(self.sim.stage.cave))
        self.assertEqual(code, 0)
        self.assertIn("installed", output)

        code, output = self.run_cli(
            "eject", "--site", hex(self.sim.stage.hook_site), "--cave", hex(self.sim.stage.cave)
        )
        self.assertEqual(code, 0)
        self.assertFalse(self.sim.cave_installed)

    def test_export_pnach_from_a_live_console(self) -> None:
        self.run_cli(
            "install",
            "--cave",
            hex(self.sim.stage.cave),
            "--journal",
            str(Path(self.tmp.name) / "journal.json"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.pnach"
            code, output = self.run_cli(
                "export-pnach",
                "--out",
                str(target),
                "--site",
                hex(self.sim.stage.hook_site),
                "--cave",
                hex(self.sim.stage.cave),
            )
            self.assertEqual(code, 0, output)
            text = target.read_text()
            self.assertIn(f"patch=1,EE,{self.sim.stage.hook_site:08X},word", text)
            self.assertIn("Ape Escape 3", text)
            self.assertIn("extended", text)


def _extended_words(text: str, addr: int) -> list[str]:
    prefix = f"patch=1,EE,{addr:08X},extended,"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].split()
    raise AssertionError(f"no extended write at {addr:#x}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
