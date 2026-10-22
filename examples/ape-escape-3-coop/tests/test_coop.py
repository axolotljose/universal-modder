"""The mod end to end: policy in Python, effect inside a running game.

The fake console rewrites its own frame state every frame, exactly like the real
engine does, so a test that only wrote the values once and read them back would
fail here -- which is the point.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from aecoops import cave
from aecoops.addresses import AddressTable
from aecoops.coop import CoopConfig, CoopMod, Split, character_from, partner_of
from aecoops.pine import Pine

from .gamesim import FakeAEC3, make_table


class CoopCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.table = make_table(self.sim.stage)
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def mod(self, **kwargs) -> CoopMod:
        config = CoopConfig(**{"p1": "kei", "p2": "yumi", "split": Split.VERTICAL, **kwargs})
        return CoopMod(self.pine, self.table, config)


class TestConfig(unittest.TestCase):
    def test_characters(self) -> None:
        self.assertEqual(character_from("kei"), "kei")
        self.assertEqual(character_from("YUMI"), "yumi")
        self.assertEqual(character_from("0"), "kei")
        self.assertEqual(character_from("1"), "yumi")
        self.assertEqual(character_from(None, "yumi"), "yumi")
        self.assertEqual(character_from("auto", "kei"), "kei")
        self.assertEqual(character_from("satoru"), "kei")
        self.assertEqual(character_from("sayaka"), "yumi")
        with self.assertRaises(ValueError):
            character_from("specter")
        self.assertEqual(partner_of("kei"), "yumi")
        self.assertEqual(partner_of("yumi"), "kei")

    def test_both_players_may_not_be_the_same_hero(self) -> None:
        with self.assertRaises(ValueError) as caught:
            CoopConfig(p1="kei", p2="kei").validate()
        self.assertIn("same hero", str(caught.exception))

    def test_mirror_is_an_explicit_escape_hatch(self) -> None:
        config = CoopConfig.mirror("yumi")
        self.assertEqual((config.p1, config.p2), ("yumi", "yumi"))

    def test_tether_radius_must_be_sane(self) -> None:
        with self.assertRaises(ValueError):
            CoopConfig(tether_radius=-1).validate()


class TestInstall(CoopCase):
    def test_install_writes_the_cave_and_the_trampoline(self) -> None:
        mod = self.mod()
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(self.sim.cave_installed)
        self.assertEqual(report.site, self.sim.stage.hook_site)
        self.assertEqual(report.callee, self.sim.stage.update_fn)
        self.assertEqual(
            self.sim.vm.read_bytes(cave.param_block_at(self.sim.stage.cave) + 0x00, 4),
            cave.MAGIC,
        )

    def test_install_is_idempotent(self) -> None:
        mod = self.mod()
        self.assertTrue(mod.install(cave_addr=self.sim.stage.cave).ok)
        again = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(again.ok)
        self.assertTrue(any("already installed" in n for n in again.notes))

    def test_install_refuses_a_site_that_is_not_a_call(self) -> None:
        # Writing a jal over a random instruction would corrupt the game; the mod
        # checks before it writes, not after.
        self.sim.vm.write_bytes(self.sim.stage.hook_site, b"\x00\x00\x00\x00")
        report = self.mod().install(cave_addr=self.sim.stage.cave)
        self.assertFalse(report.ok)
        self.assertIn("does not hold a jal", report.errors[0])

    def test_install_explains_a_missing_hook(self) -> None:
        table = make_table(self.sim.stage)
        del table.globals["frame_hook"]
        report = CoopMod(self.pine, table, CoopConfig()).install(cave_addr=self.sim.stage.cave)
        self.assertFalse(report.ok)
        self.assertIn("no frame hook", report.errors[0])

    def test_install_refuses_a_paused_emulator(self) -> None:
        self.sim.pcsx2.status = 1
        report = self.mod().install(cave_addr=self.sim.stage.cave)
        self.assertFalse(report.ok)
        self.assertIn("paused", report.errors[0])

    def test_eject_puts_the_call_back(self) -> None:
        mod = self.mod()
        mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(self.sim.cave_installed)
        done = mod.eject()
        self.assertFalse(self.sim.cave_installed)
        self.assertEqual(self.sim.vm.read32(self.sim.stage.hook_site), 0x0C000000 | (self.sim.stage.update_fn >> 2))
        self.assertTrue(any("restored" in d for d in done))
        self.assertEqual(self.sim.vm.read_bytes(self.sim.stage.cave, 16), b"\x00" * 16)


class TestCoopRuntime(CoopCase):
    def install(self, **kwargs) -> CoopMod:
        mod = self.mod(**kwargs)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        return mod

    def test_two_heroes_and_two_viewports_survive_the_engine(self) -> None:
        mod = self.install()
        self.assertEqual(self.sim.read(self.sim.stage.view_mode), 0)
        mod.tick()
        self.sim.frame(3)
        # The engine resets these every frame; the cave re-applies them every frame.
        self.assertEqual(self.sim.read(self.sim.stage.view_mode), self.table.layout_id("vertical"))
        self.assertEqual(self.sim.read(self.sim.stage.actor_count), 2)
        self.assertEqual(self.sim.read(self.sim.stage.partner_enabled), 1)

    def test_either_hero_can_be_either_player(self) -> None:
        mod = self.install(p1="yumi", p2="kei")
        mod.tick()
        self.sim.frame(2)
        self.assertEqual(self.sim.actor(0)["char_id"], self.table.character_id("yumi"))
        self.assertEqual(self.sim.actor(1)["char_id"], self.table.character_id("kei"))
        # Pad ports follow the players, not the characters.
        self.assertEqual(self.sim.actor(0)["pad_port"], 0)
        self.assertEqual(self.sim.actor(1)["pad_port"], 1)
        self.assertEqual(self.sim.actor(1)["active"], 1)

    def test_characters_can_be_swapped_mid_stage(self) -> None:
        mod = self.install(p1="kei", p2="yumi")
        mod.tick()
        self.sim.frame(2)
        before = self.sim.actor(0)["char_id"]
        mod.swap()
        mod.tick()
        self.sim.frame(2)
        self.assertEqual(self.sim.actor(0)["char_id"], self.table.character_id("yumi"))
        self.assertEqual(self.sim.actor(1)["char_id"], before)

    def test_assign_refuses_a_bad_pair(self) -> None:
        mod = self.install()
        with self.assertRaises(ValueError):
            mod.assign("kei", "kei")

    def test_split_layout_can_change_at_runtime(self) -> None:
        mod = self.install()
        mod.tick()
        self.sim.frame(2)
        self.assertEqual(self.sim.read(self.sim.stage.view_mode), self.table.layout_id("vertical"))
        mod.set_split(Split.HORIZONTAL)
        mod.tick()
        self.sim.frame(2)
        self.assertEqual(self.sim.read(self.sim.stage.view_mode), self.table.layout_id("horizontal"))

    def test_tether_copies_player_one_onto_player_two_in_game(self) -> None:
        mod = self.install()
        mod.tick()
        self.sim.move_p1(0x4ABCDEF0)
        self.sim.frame(2)
        # The copy happens inside the cave, at frame rate: no host round trip, so
        # there is no lag between the two heroes.
        self.assertEqual(self.sim.actor(1)["pos_x"], 0x4ABCDEF0)
        self.assertEqual(self.sim.actor(1)["pos_z"], self.sim.actor(0)["pos_z"])

    def test_tether_can_be_turned_off(self) -> None:
        mod = self.install(tether_until_independent=False)
        mod.tick()
        self.sim.move_p1(0x12345678)
        self.sim.frame(2)
        self.assertNotEqual(self.sim.actor(1)["pos_x"], 0x12345678)

    def test_tether_releases_once_the_partner_moves_on_its_own(self) -> None:
        mod = self.install()
        mod.tick()
        self.sim.frame(1)
        self.assertFalse(mod.independent)
        # Pretend the engine has started driving slot 1 somewhere else.
        self.sim.vm.write32(self.sim.stage.field(1, self.sim.stage.off_pos_x), 0x99999999)
        self.assertFalse(mod.independent)  # needs to hold for a moment
        time.sleep(1.05)
        self.assertTrue(mod.independent)

    def test_tick_reports_a_stalled_hook(self) -> None:
        mod = self.install()
        self.sim.frame(1)
        first = mod.tick()
        self.assertTrue(first.ok, first.warnings)
        stalled = mod.tick()  # no frames between ticks
        self.assertFalse(stalled.ok)
        self.assertIn("has not run", stalled.warnings[0])

    def test_tick_recovers_when_the_game_runs_again(self) -> None:
        mod = self.install()
        self.sim.frame(1)
        mod.tick()
        self.assertFalse(mod.tick().ok)
        self.sim.frame(1)
        self.assertTrue(mod.tick().ok)

    def test_tick_notices_a_clobbered_parameter_block(self) -> None:
        mod = self.install()
        self.sim.frame(1)
        self.sim.vm.write32(cave.param_block_at(self.sim.stage.cave), 0xDEADDEAD)
        report = mod.tick()
        self.assertFalse(report.ok)
        self.assertIn("lost its magic", report.warnings[0])

    def test_watchdog_survives_a_paused_emulator(self) -> None:
        mod = self.install()
        self.sim.frame(1)
        self.sim.pcsx2.status = 1
        report = mod.tick()
        self.assertFalse(report.ok)
        self.assertIn("paused", report.warnings[0])

    def test_run_loop_stops_after_the_requested_time(self) -> None:
        mod = self.install()
        seen = []
        passes = mod.run(hz=50.0, duration=0.3, on_tick=seen.append)
        self.assertGreater(passes, 3)
        self.assertEqual(len(seen), passes)
        # Frames only advance when the game does; ticking the host alone cannot
        # keep the hook "alive", which is exactly what the stall warning means.
        self.assertTrue(all(not report.ok for report in seen[1:]))

    def test_status_is_readable(self) -> None:
        mod = self.install(p1="yumi", p2="kei")
        self.sim.frame(1)
        mod.tick()
        status = mod.status()
        self.assertTrue(status["installed"])
        self.assertEqual(status["p1"], "yumi")
        self.assertEqual(status["p2"], "kei")
        self.assertEqual(status["split"], "vertical")
        self.assertIn("Ape Escape 3", status["disc"])

    def test_share_quota_is_only_written_when_configured(self) -> None:
        table = make_table(self.sim.stage)
        table.globals["quota_shared"] = type(table.globals["actor_count"])(name="quota_shared", addr=0x00110020)
        mod = CoopMod(self.pine, table, CoopConfig(share_quota=True))
        mod.install(cave_addr=self.sim.stage.cave)
        mod.tick()
        self.sim.frame(1)
        self.assertEqual(self.sim.read(0x00110020), 1)

    def test_entries_are_capped_by_the_cave_budget(self) -> None:
        mod = self.install()
        entries = mod.entries()
        self.assertLessEqual(len(entries), cave.MAX_ENTRIES, [hex(a) for a, _ in entries])

    def test_table_reports_unresolved_fields(self) -> None:
        table = AddressTable.load()
        self.assertTrue(table.unresolved())
        with self.assertRaises(Exception):
            table.character_id("kei")


class TestJournaledInstall(CoopCase):
    def test_install_writes_a_journal_that_can_be_reverted(self) -> None:
        from aecoops.patcher import Patch, PatchSet, revert

        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "journal.json"
            patchset = PatchSet(
                name="sim",
                patches=[Patch(name="tag", addr=0x00110030, data="deadbeef", expect="00000000")],
            )
            mod = CoopMod(self.pine, make_table(self.sim.stage), CoopConfig(), patchset)
            report = mod.install(cave_addr=self.sim.stage.cave, journal=journal)
            self.assertTrue(report.ok, report.errors)
            self.assertTrue(journal.exists())
            # Patch data is bytes in order, so "deadbeef" lands as de ad be ef.
            self.assertEqual(self.sim.vm.read_bytes(0x00110030, 4), bytes.fromhex("deadbeef"))
            revert(self.pine, journal)
            self.assertEqual(self.sim.vm.read_bytes(0x00110030, 4), b"\x00\x00\x00\x00")

    def test_wrong_disc_is_refused_before_anything_is_written(self) -> None:
        from aecoops.patcher import Patch, PatchSet

        patchset = PatchSet(
            name="pal",
            serials=["SLES-00000"],
            patches=[Patch(name="tag", addr=0x00110030, data="deadbeef", expect="00000000")],
        )
        mod = CoopMod(self.pine, make_table(self.sim.stage), CoopConfig(), patchset)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertFalse(report.ok)
        self.assertIn("wrong disc", report.errors[0])
        self.assertEqual(self.sim.read(0x00110030), 0)
        self.assertFalse(self.sim.cave_installed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
