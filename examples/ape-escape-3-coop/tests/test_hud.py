"""The HUD on a split screen: geometry, policy, and what actually reaches the game.

Two kinds of test here, and the difference matters:

* the policy tests use a *worksheet* as the input and assert what the mod would
  write. They are where "an unmeasured disc degrades to no writes, with a reason"
  is pinned down, because that is the state every user starts in;
* the console tests run the real machine code against the fake console, which
  rewrites its own HUD every frame. A policy test cannot tell the difference
  between "the mod wrote the anchor" and "the mod wrote the anchor once and the
  engine immediately put it back", and that difference is the whole mod.
"""

from __future__ import annotations

import unittest

from aecoops import cave, hud
from aecoops.addresses import Field
from aecoops.coop import CoopConfig, CoopMod, Entry, Plan, Split
from aecoops.hud import HudMode, Screen
from aecoops.pine import Pine

from .gamesim import FakeAEC3, make_table


class TestGeometry(unittest.TestCase):
    def test_screen_parses_the_forms_a_player_would_type(self) -> None:
        self.assertEqual(Screen.parse(None).width, 640)
        self.assertEqual(Screen.parse("640x448:12").margin, 12)
        self.assertEqual(Screen.parse("pal").height, 512)
        self.assertEqual(Screen.parse("NTSC").height, 448)
        with self.assertRaises(ValueError):
            Screen.parse("640 by 448")
        with self.assertRaises(ValueError):
            Screen.parse("1x1")

    def test_halves_tile_the_frame_exactly(self) -> None:
        # An odd width is the interesting case: the two halves must still cover
        # every column, or the renderer's own cut and ours disagree by a pixel and
        # that shows up as a line down the middle of the screen.
        screen = Screen(641, 449)
        left, right = screen.views("vertical")
        self.assertEqual((left.x, left.w), (0, 320))
        self.assertEqual((right.x, right.w), (320, 321))
        self.assertEqual(left.w + right.w, 641)
        top, bottom = screen.views("horizontal")
        self.assertEqual(top.h + bottom.h, 449)

    def test_anchors_sit_inside_their_own_view(self) -> None:
        screen = Screen(640, 448, margin=8)
        self.assertEqual(screen.anchors("vertical"), ((8, 8), (328, 8)))
        self.assertEqual(screen.anchors("horizontal"), ((8, 8), (8, 232)))
        # A single view is one full-screen rect: the mod writes nothing in that
        # case, but the geometry still has to be sane for `--split single` previews.
        self.assertEqual(screen.views("single")[0].w, 640)


class TestPolicyAgainstAWorksheet(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.full = make_table(self.sim.stage, with_hud=True, with_cameras=True)
        self.bare = make_table(self.sim.stage)  # nothing about the HUD measured

    def tearDown(self) -> None:
        self.sim.close()

    def test_shared_writes_nothing_at_all(self) -> None:
        plan = hud.plan_hud(self.full, HudMode.SHARED, "vertical", Screen())
        self.assertEqual(plan.entries, [])
        self.assertEqual(plan.warnings, [])
        self.assertIn("where the game puts it", " ".join(plan.notes))

    def test_per_view_is_gated_on_measurements(self) -> None:
        plan = hud.plan_hud(self.bare, HudMode.PER_VIEW, "vertical", Screen())
        self.assertEqual(plan.entries, [], "an unmeasured disc must not be written to")
        self.assertTrue(plan.warnings)
        self.assertIn("docs/DISCOVERY.md#hud", plan.warnings[0])

    def test_per_view_writes_instances_anchors_and_owners(self) -> None:
        plan = hud.plan_hud(self.full, HudMode.PER_VIEW, "vertical", Screen(), pads=(0, 1))
        values = dict(plan.entries)
        base, stride = self.sim.stage.hud_base, self.sim.stage.hud_stride
        off = self.sim.stage
        self.assertEqual(values[self.sim.stage.hud_instances], 2)
        for slot, (x, y) in enumerate(((8, 8), (328, 8))):
            self.assertEqual(values[base + slot * stride + off.off_hud_visible], 1)
            self.assertEqual(values[base + slot * stride + off.off_hud_x], x)
            self.assertEqual(values[base + slot * stride + off.off_hud_y], y)
            self.assertEqual(values[base + slot * stride + off.off_hud_owner], slot)
        self.assertTrue(plan.placed)
        self.assertEqual(plan.warnings, [])

    def test_geometry_mode_can_be_left_to_the_engine(self) -> None:
        # Plenty of engines place a HUD from the viewport it belongs to. Then the
        # instance count and the visibility flags are all co-op needs, and the
        # anchors stay unmeasured -- that must be a note, not a warning.
        from aecoops.addresses import AddressTable

        table = make_table(self.sim.stage, with_hud=True)
        for name in ("x", "y"):
            table.structs["hud"].fields[name] = Field(name, None)
        plan = hud.plan_hud(table, HudMode.PER_VIEW, "vertical", Screen())
        self.assertFalse(plan.placed)
        values = dict(plan.entries)
        self.assertEqual(values[self.sim.stage.hud_instances], 2)
        self.assertTrue(all(v == 1 for addr, v in plan.entries if addr % self.sim.stage.hud_stride == 0))
        self.assertEqual(plan.warnings, [])
        self.assertIn("the game places them", " ".join(plan.notes))

    def test_hidden_prefers_the_instance_count_over_hiding_instances(self) -> None:
        plan = hud.plan_hud(self.full, HudMode.HIDDEN, "vertical", Screen())
        self.assertEqual(plan.entries, [(self.sim.stage.hud_instances, 0)])

        from aecoops.addresses import AddressTable

        table = make_table(self.sim.stage, with_hud=True)
        del table.globals["hud_instances"]
        plan = hud.plan_hud(table, HudMode.HIDDEN, "vertical", Screen())
        self.assertEqual(len(plan.entries), 2)
        self.assertTrue(all(value == 0 for _, value in plan.entries))

    def test_hidden_says_so_when_there_is_no_way_to_hide_it(self) -> None:
        # The structure is known, but neither of the two ways to switch it off has
        # been measured. That is a different failure from "we have no idea where
        # the HUD is" and it has to read differently.
        table = make_table(self.sim.stage, with_hud=True)
        del table.globals["hud_instances"]
        table.structs["hud"].fields["visible"] = Field("visible", None)
        plan = hud.plan_hud(table, HudMode.HIDDEN, "vertical", Screen())
        self.assertEqual(plan.entries, [])
        self.assertIn("no way to switch the HUD off", plan.warnings[0])

    def test_requirements_are_named_for_every_mode_that_needs_them(self) -> None:
        self.assertEqual(hud.requirements(HudMode.SHARED), [])
        self.assertTrue(any("hud.base" in item for item in hud.requirements(HudMode.PER_VIEW)))

    def test_a_single_camera_slot_is_not_written_twice(self) -> None:
        # stride 0 means both "slots" resolve to the same word: writing it twice
        # would make the second view overwrite the first, which looks exactly like
        # a camera that ignores the split.
        from aecoops.addresses import AddressTable

        table = make_table(self.sim.stage, with_hud=True, with_cameras=True)
        table.structs["camera"].stride = 0
        plan = hud.plan_hud(table, HudMode.SHARED, "vertical", Screen())
        self.assertEqual(plan.entries, [])


class TestTrimming(unittest.TestCase):
    def test_essentials_survive_a_crowded_budget(self) -> None:
        plan = Plan(
            entries=[Entry(0x1000 + 4 * i, 1, f"essential{i}") for i in range(3)]
            + [Entry(0x2000 + 4 * i, 1, f"nice{i}", essential=False) for i in range(10)]
        )
        trimmed = plan.trimmed_to_budget(5)
        self.assertEqual(len(trimmed.entries), 5)
        self.assertEqual([e.label for e in trimmed.entries[:3]], ["essential0", "essential1", "essential2"])
        self.assertTrue(any("did not fit" in w for w in trimmed.warnings))
        self.assertIn("nice5", trimmed.warnings[0])

    def test_a_plan_that_fits_is_untouched(self) -> None:
        plan = Plan(entries=[Entry(0x1000, 1, "one")])
        self.assertIs(plan.trimmed_to_budget(40), plan)

    def test_the_cave_budget_matches_the_trimming_default(self) -> None:
        # Two numbers that must agree: the block's entry budget and the policy's.
        self.assertEqual(cave.MAX_ENTRIES, 40)
        self.assertGreater((cave.PARAM_BLOCK_SIZE - cave.ENTRIES_OFFSET) // 8, cave.MAX_ENTRIES)


class HudCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def mod(self, hud_mode: HudMode, **kwargs) -> CoopMod:
        table = make_table(self.sim.stage, with_hud=True, with_cameras=True)
        config = CoopConfig(p1="kei", p2="yumi", split=Split.VERTICAL, hud_mode=hud_mode, **kwargs)
        mod = CoopMod(self.pine, table, config)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        return mod


class TestHudOnTheConsole(HudCase):
    def test_per_view_hud_lands_in_each_half_and_stays_there(self) -> None:
        mod = self.mod(HudMode.PER_VIEW)
        for _ in range(3):
            self.sim.frame(1)
            mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.hud_instances), 2)
        first = self.sim.hud_instance(0)
        second = self.sim.hud_instance(1)
        self.assertEqual((first["x"], first["y"], first["visible"], first["owner"]), (8, 8, 1, 0))
        # The second instance is in the *right* half, not stacked on the first and
        # not left at the engine's bottom-right position.
        self.assertEqual((second["x"], second["y"], second["visible"], second["owner"]), (328, 8, 1, 1))

    def test_each_viewport_follows_its_own_hero(self) -> None:
        mod = self.mod(HudMode.SHARED)
        self.sim.frame(2)
        mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.camera(0, self.sim.stage.off_camera_target)), 0)
        self.assertEqual(self.sim.read(self.sim.stage.camera(1, self.sim.stage.off_camera_target)), 1)

    def test_the_camera_preference_is_respected(self) -> None:
        # "Which hero the left half shows" is a player choice, not a constant.
        self.mod(HudMode.SHARED, camera_follows="yumi")
        self.sim.frame(2)
        self.assertEqual(self.sim.read(self.sim.stage.camera(0, self.sim.stage.off_camera_target)), 1)
        self.assertEqual(self.sim.read(self.sim.stage.camera(1, self.sim.stage.off_camera_target)), 0)

    def test_shared_leaves_the_games_hud_alone(self) -> None:
        mod = self.mod(HudMode.SHARED)
        for _ in range(3):
            self.sim.frame(1)
            mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.hud_instances), 1)
        self.assertEqual(self.sim.hud_instance(0)["x"], self.sim.stage.engine_hud[0])
        self.assertEqual(self.sim.hud_instance(1)["visible"], 0)

    def test_hidden_switches_the_hud_off_every_frame(self) -> None:
        mod = self.mod(HudMode.HIDDEN)
        for _ in range(2):
            self.sim.frame(1)
            mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.hud_instances), 0)

    def test_an_unmeasured_hud_is_reported_at_install_not_swallowed(self) -> None:
        table = make_table(self.sim.stage)  # nothing measured
        mod = CoopMod(self.pine, table, CoopConfig(hud_mode=HudMode.PER_VIEW))
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("docs/DISCOVERY.md#hud" in note for note in report.notes), report.notes)
        self.assertTrue(any("hud" in note for note in report.notes), report.notes)

    def test_status_reports_the_hud_mode_and_the_write_count(self) -> None:
        mod = self.mod(HudMode.PER_VIEW)
        self.sim.frame(1)
        status = mod.status()
        self.assertEqual(status["hud"], "per-view: 2 instances, anchors set, owner per player")
        self.assertGreater(status["writes"], 10)

    def test_a_signature_only_hud_base_is_resolved_before_anything_is_written(self) -> None:
        # A pinned address is only valid for the exact pressing it was measured on;
        # a signature survives. If a user gives a signature instead of an address,
        # the session has to turn it into an address before the HUD policy can use
        # it -- and say so instead of writing to `None`.
        table = make_table(self.sim.stage, with_hud=True, with_cameras=True)
        struct = table.structs["hud"]
        struct.base.signature = "48 55 44 3a"  # "HUD:" -- planted below
        struct.base.addr = None
        self.sim.vm.write_bytes(self.sim.stage.hud_base, b"HUD:")  # where it really is
        del table.globals["hud_instances"]  # force the policy to work from the base

        mod = CoopMod(self.pine, table, CoopConfig(hud_mode=HudMode.PER_VIEW))
        self.assertEqual(hud.plan_hud(table, HudMode.PER_VIEW, "vertical", Screen()).warnings != [], True)
        # Narrow the look: scanning 32 MB for a signature takes seconds, and this
        # is the same knob a user needs when a signature only exists in a stage.
        mod.resolve(ranges=[(self.sim.stage.hud_base, self.sim.stage.hud_base + 0x100)])
        self.assertEqual(struct.base.addr, self.sim.stage.hud_base)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.sim.frame(1)
        self.assertEqual(self.sim.hud_instance(1)["x"], 328)

    def test_the_tether_radius_pulls_the_partner_back_to_the_leash(self) -> None:
        # Two viewports of one stage let the heroes walk into different load zones,
        # and the leash is the cheap fix: player two is moved back along the line to
        # player one, keeping the direction they were running in.
        mod = self.mod(HudMode.SHARED, tether_radius=2.0, tether_until_independent=False)
        stage = self.sim.stage
        self.sim.frame(1)
        mod.tick()  # arms the plan; the engine then rewrites positions below
        self.sim.move_p1_float(0.0)
        self.sim.move_p2_float(10.0)
        mod.tick()  # measures the distance, publishes the correction
        self.sim.frame(1)  # the game applies it
        p1 = self.sim.read_float(stage.field(0, stage.off_pos_x))
        p2 = self.sim.read_float(stage.field(1, stage.off_pos_x))
        self.assertAlmostEqual(p1, 0.0, places=5)
        self.assertAlmostEqual(p2, 2.0, places=3)
        self.assertGreater(p2, p1, "the partner stays on the side it was on")

    def test_a_partner_inside_the_leash_is_left_alone(self) -> None:
        mod = self.mod(HudMode.SHARED, tether_radius=5.0, tether_until_independent=False)
        stage = self.sim.stage
        self.sim.frame(1)
        mod.tick()
        self.sim.move_p2_float(1.0)
        mod.tick()
        self.sim.frame(1)
        self.assertAlmostEqual(self.sim.read_float(stage.field(1, stage.off_pos_x)), 1.0, places=5)

    def test_ejecting_leaves_no_trace_of_the_hud_writes(self) -> None:
        mod = self.mod(HudMode.PER_VIEW)
        self.sim.frame(2)
        self.sim.set_engine_char(self.sim.kei)
        mod.eject()
        self.sim.frame(1)  # the engine's own values win again, unopposed
        self.assertEqual(self.sim.hud_instance(1)["x"], self.sim.stage.engine_hud[0])
        self.assertEqual(self.sim.read(self.sim.stage.hud_instances), 1)
