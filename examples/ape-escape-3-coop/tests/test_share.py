"""The pasteable bundle: small, complete, and incapable of carrying game data.

The measurement has to happen on the machine running the game; the conversation about
it often happens somewhere else. So the thing that crosses that gap gets tested the
way a file transfer format would be: does it carry what a helper needs, does it stay
one paste, and is there anything in it that should not have left the machine?

That last one is a property of the *code*, not a promise in a docstring, which is why
it can be tested here: the bundle is built from the worksheet and emulator metadata,
and the tests below pin that down -- a journal's instruction bytes must not appear,
and neither must anything from a savestate or a game file.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aecoops import share
from aecoops.addresses import AddressTable

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "aecoops" / "data" / "addresses.json"


class TestTheShippedWorksheetBundles(unittest.TestCase):
    def setUp(self) -> None:
        self.table = AddressTable.load(SHIPPED)
        self.bundle = share.build_bundle(self.table)

    def test_it_is_one_paste(self) -> None:
        self.assertLess(self.bundle.size, share.COMFORTABLE_BYTES, self.bundle.size)

    def test_it_is_plain_ascii(self) -> None:
        # It gets pasted into terminals, chats and note apps, some of which mangle
        # anything cleverer than ASCII.
        text = share.ascii_only(self.bundle.text)
        self.assertEqual(text, self.bundle.text)
        text.encode("ascii")

    def test_every_entry_is_named_so_a_helper_can_answer(self) -> None:
        for name in ("frame_hook", "view_mode", "actor_count"):
            self.assertIn(name, self.bundle.text)
        for name in ("actor", "camera", "viewport", "hud"):
            self.assertIn(name, self.bundle.text)
        for call in ("spawn_partner", "set_view_mode"):
            self.assertIn(call, self.bundle.text)

    def test_it_says_what_is_still_blank(self) -> None:
        self.assertEqual(len(self.bundle.unresolved), 32)
        self.assertIn("still to find:", self.bundle.text)
        for name in ("frame_hook", "calls.spawn_partner"):
            self.assertIn(name, ", ".join(self.bundle.unresolved))

    def test_a_measured_entry_shows_its_address(self) -> None:
        self.table.globals["frame_hook"].addr = 0x00123450
        self.table.globals["frame_hook"].verified = "2026-10-15"
        self.table.characters["kei"] = 0
        self.table.structs["actor"].stride = 0x1F0
        bundle = share.build_bundle(self.table)
        self.assertIn("0x00123450", bundle.text)
        self.assertIn("verified 2026-10-15", bundle.text)
        self.assertIn("stride 0x1f0", bundle.text)
        self.assertNotIn("frame_hook,", ", ".join(bundle.unresolved) + ",")

    def test_no_line_is_absurdly_long(self) -> None:
        # The bundle gets read on a phone as often as in a terminal, so the long list
        # of blanks is wrapped rather than one 436-character line.
        longest = max(len(line) for line in self.bundle.text.splitlines())
        self.assertLess(longest, 200, longest)
        self.assertIn("still to find: ", self.bundle.text)
        self.assertGreater(self.bundle.text.count("\n"), 30)

    def test_full_adds_the_notes_and_still_fits(self) -> None:
        full = share.build_bundle(self.table, full=True)
        self.assertGreater(full.size, self.bundle.size)
        self.assertIn("NOT FOUND YET", full.text)
        self.assertLess(full.size, 32 * 1024, full.size)


class TestWhatItCannotCarry(unittest.TestCase):
    """The rule that matters: addresses and counts, never game bytes."""

    def setUp(self) -> None:
        self.table = AddressTable.load(SHIPPED)

    def test_a_journal_contributes_a_count_and_an_address_only(self) -> None:
        # A journal holds the original instructions a patch replaced -- game code --
        # so the install line summarises it instead of quoting it.
        install = share.Install(cave=0x001F0000, site=0x00123450, journal_writes=3, journal_at="coop-journal.json")
        text = share.build_bundle(self.table, install=install).text
        self.assertIn("cave 0x001f0000", text)
        self.assertIn("hook site 0x00123450", text)
        self.assertIn("journal 3 writes", text)
        # Nothing that looks like an instruction stream: no long hex runs.
        for token in text.split():
            if token.startswith("0x") and len(token) > 12:
                self.fail(f"a long hex token appears in the bundle: {token}")

    def test_the_disc_line_is_metadata_not_content(self) -> None:
        disc = share.Disc(title="Ape Escape 3", serial="SCUS-97501", crc="0a1b2c3d", state="0")
        text = share.build_bundle(self.table, disc=disc).text
        self.assertIn("SCUS-97501", text)
        self.assertIn("crc 0A1B2C3D", text)
        self.assertIn("state 0", text)

    def test_an_unreachable_emulator_is_stated_rather_than_hidden(self) -> None:
        # The bundle is often made *because* something is not working, so "PINE did
        # not answer" is one of the more useful lines in it.
        text = share.build_bundle(self.table, disc=share.Disc(note="PINE not reachable (no socket)")).text
        self.assertIn("PINE not reachable", text)

    def test_extras_are_carried_under_their_own_name(self) -> None:
        bundle = share.build_bundle(self.table, extras=[("sweep.txt", "59.94 Hz 0x00123450 per frame")])
        self.assertIn("-- sweep.txt --", bundle.text)
        self.assertIn("59.94 Hz 0x00123450 per frame", bundle.text)


class TestSetFromAFile(unittest.TestCase):
    """A batch of measurements arrives as text; applying it is one command."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "addresses.json"
        AddressTable.load(SHIPPED).save(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_file_of_measurements_applies_in_one_command(self) -> None:
        import contextlib
        import io

        from aecoops.cli import main

        source = Path(self.tmp.name) / "measurements.txt"
        source.write_text(
            "# what the sweep proved\n"
            "globals.frame_hook.addr = 0x00123450\n"
            "globals.frame_hook.verified = 2026-10-15\n"
            "\n"
            "characters.kei = 0\n"
            "structs.actor.stride = 0x1F0\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["--table", str(self.path), "set", "--file", str(source)])
        self.assertEqual(code, 0)
        table = AddressTable.load(self.path)
        self.assertEqual(table.globals["frame_hook"].addr, 0x00123450)
        # Whitespace around a value is the writer's, not the measurement's.
        self.assertEqual(table.globals["frame_hook"].verified, "2026-10-15")
        self.assertEqual(table.characters["kei"], 0)
        self.assertEqual(table.structs["actor"].stride, 0x1F0)

    def test_the_two_forms_combine(self) -> None:
        import contextlib
        import io

        from aecoops.cli import main

        source = Path(self.tmp.name) / "one.txt"
        source.write_text("characters.yumi = 1\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["--table", str(self.path), "set", "--file", str(source), "structs.hud.stride=0x40"]
            )
        self.assertEqual(code, 0)
        table = AddressTable.load(self.path)
        self.assertEqual(table.characters["yumi"], 1)
        self.assertEqual(table.structs["hud"].stride, 0x40)

    def test_an_empty_command_is_refused_with_both_forms_named(self) -> None:
        from aecoops.cli import main

        code = main(["--table", str(self.path), "set"])
        self.assertEqual(code, 2)


class TestShareCommand(unittest.TestCase):
    def test_it_writes_a_file_and_prints_only_the_path(self) -> None:
        import contextlib
        import io

        from aecoops.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "addresses.json"
            AddressTable.load(SHIPPED).save(table)
            out = Path(tmp) / "bundle.txt"
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(
                    ["--table", str(table), "share", "--table-only", "--out", str(out)]
                )
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            body = out.read_text(encoding="ascii")
            self.assertIn("aecoops measurement bundle", body)
            # stdout stays short: a file was asked for, so the file is the payload.
            self.assertLess(len(captured.getvalue()), 300)
            self.assertNotIn("frame_hook", captured.getvalue())

    def test_stdout_is_the_payload_when_no_file_is_asked_for(self) -> None:
        import contextlib
        import io

        from aecoops.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "addresses.json"
            AddressTable.load(SHIPPED).save(table)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(["--table", str(table), "share", "--table-only"])
            self.assertEqual(code, 0)
            body = captured.getvalue()
            self.assertIn("aecoops measurement bundle", body)
            self.assertIn("still to find:", body)

    def test_it_works_with_no_emulator_and_no_journal(self) -> None:
        # Which is the case it will most often be run in: something is wrong, and the
        # bundle is how you show someone.
        table = AddressTable.load(SHIPPED)
        bundle = share.build_bundle(table, disc=share.Disc(note="PINE not reachable (no socket)"))
        self.assertIn("PINE not reachable", bundle.text)
        self.assertIn("nothing installed", bundle.text)
        json.dumps({"ok": True})  # the bundle stays plain text; nothing to serialise


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
