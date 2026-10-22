"""The split's second lever: the rectangles the renderer draws into.

`view_mode` is one word, and if the story renderer ignores it, co-op has nothing
to fall back on. These tests cover the fallback: driving the viewport rectangles
themselves, which the engine is *known* to take at runtime because the MonkeyCam
picture-in-picture is exactly that -- a second view drawn into a rectangle it can
move.

The pattern from the HUD tests repeats here, for the same reason. Against a
worksheet: an unmeasured disc produces no writes and a reason, and a half-measured
one produces no writes either rather than a rectangle with three sides. Against
the console: the writes have to survive the renderer rebuilding its own rectangles
every frame, which is a claim no policy test can make.
"""

from __future__ import annotations

import unittest

from aecoops import views
from aecoops.addresses import Field
from aecoops.coop import CoopConfig, CoopMod, Split
from aecoops.hud import HudMode, Screen
from aecoops.pine import Pine

from .gamesim import FakeAEC3, make_table


class TestViewPlanAgainstAWorksheet(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.full = make_table(
            self.sim.stage, with_hud=True, with_cameras=True, with_viewports=True
        )
        self.bare = make_table(self.sim.stage)

    def tearDown(self) -> None:
        self.sim.close()

    def test_an_unmeasured_viewport_table_is_a_note_not_a_failure(self) -> None:
        # A missing backup is not a problem to report: the value-based route is
        # still in play, and `status` should not cry wolf about an optional path.
        plan = views.plan_views(self.bare, "vertical", Screen())
        self.assertEqual(plan.entries, [])
        self.assertEqual(plan.warnings, [])
        self.assertIn("rests entirely on view_mode", " ".join(plan.notes))

    def test_the_two_rectangles_tile_the_frame(self) -> None:
        plan = views.plan_views(self.full, "vertical", Screen(640, 448, margin=0), targets=(0, 1))
        values = dict(plan.entries)
        stage = self.sim.stage
        self.assertEqual(values[stage.viewport_count], 2)
        first = [values[stage.viewport(0, off)] for off in (stage.off_view_x, stage.off_view_y, stage.off_view_w, stage.off_view_h)]
        second = [values[stage.viewport(1, off)] for off in (stage.off_view_x, stage.off_view_y, stage.off_view_w, stage.off_view_h)]
        self.assertEqual(first, [0, 0, 320, 448])
        self.assertEqual(second, [320, 0, 320, 448])
        self.assertEqual(first[2] + second[2], 640)
        self.assertTrue(plan.placed)
        self.assertEqual(plan.warnings, [])

    def test_a_horizontal_split_cuts_the_other_way(self) -> None:
        plan = views.plan_views(self.full, "horizontal", Screen(640, 448, margin=0))
        values = dict(plan.entries)
        stage = self.sim.stage
        self.assertEqual(values[stage.viewport(1, stage.off_view_y)], 224)
        self.assertEqual(values[stage.viewport(1, stage.off_view_h)], 224)

    def test_each_view_follows_one_hero_and_they_are_different_heroes(self) -> None:
        plan = views.plan_views(self.full, "vertical", Screen(), targets=(1, 0))
        values = dict(plan.entries)
        stage = self.sim.stage
        self.assertEqual(values[stage.viewport(0, stage.off_view_target)], 1)
        self.assertEqual(values[stage.viewport(1, stage.off_view_target)], 0)
        self.assertIn("view1.target=hero2", plan.labels)
        self.assertIn("view2.target=hero1", plan.labels)

    def test_a_half_measured_rectangle_is_not_half_written(self) -> None:
        # Writing x and y but not w and h would leave a view the size of the old
        # one, sitting in the wrong place: worse than doing nothing.
        table = make_table(self.sim.stage, with_viewports=True)
        table.structs["viewport"].fields["w"] = Field("w", None)
        plan = views.plan_views(table, "vertical", Screen())
        self.assertEqual(plan.entries, [])
        self.assertIn("w", " ".join(plan.notes))

    def test_a_single_addressed_rectangle_is_refused_entirely(self) -> None:
        # With stride 0 both views would land on one rectangle, so the renderer
        # would draw the same half of the frame twice. Nothing is written -- not
        # even the count, because telling it there are two views is what produces
        # the doubled picture.
        table = make_table(self.sim.stage, with_viewports=True)
        table.structs["viewport"].stride = 0
        plan = views.plan_views(table, "vertical", Screen())
        self.assertEqual(plan.entries, [])
        self.assertFalse(plan.placed)
        self.assertIn("stride is 0", " ".join(plan.notes))

    def test_requirements_name_everything_the_route_needs(self) -> None:
        wanted = " ".join(views.requirements())
        for fragment in ("viewport.base", "stride", "x", "y", "w", "h", "viewport_count"):
            self.assertIn(fragment, wanted)


class ViewCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def mod(self, **kwargs) -> CoopMod:
        table = make_table(
            self.sim.stage, with_hud=True, with_cameras=True, with_viewports=True
        )
        config = CoopConfig(p1="kei", p2="yumi", split=Split.VERTICAL, hud_mode=HudMode.SHARED, **kwargs)
        mod = CoopMod(self.pine, table, config)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        return mod


class TestViewportsOnTheConsole(ViewCase):
    def test_the_rectangles_hold_against_a_renderer_that_rebuilds_them(self) -> None:
        mod = self.mod()
        for _ in range(3):
            self.sim.frame(1)
            mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.viewport_count), 2)
        self.assertEqual(self.sim.view_rect(0), (0, 0, 320, 448))
        # Not the MonkeyCam's corner: the second view is the right half of the frame.
        self.assertEqual(self.sim.view_rect(1), (320, 0, 320, 448))

    def test_turning_the_route_off_leaves_the_renderers_own_viewports(self) -> None:
        mod = self.mod(place_viewports=False)
        for _ in range(2):
            self.sim.frame(1)
            mod.tick()
        self.assertEqual(self.sim.read(self.sim.stage.viewport_count), 1)
        self.assertEqual(self.sim.view_rect(1), self.sim.stage.engine_view2)

    def test_a_signature_only_viewport_base_is_resolved_before_writing(self) -> None:
        table = make_table(self.sim.stage, with_viewports=True)
        struct = table.structs["viewport"]
        struct.base.signature = "56 50 4f 52"  # "VPOR"
        struct.base.addr = None
        del table.globals["viewport_count"]
        self.sim.vm.write_bytes(self.sim.stage.viewport_base, b"VPOR")
        mod = CoopMod(self.pine, table, CoopConfig(split=Split.VERTICAL))
        mod.resolve(ranges=[(self.sim.stage.viewport_base, self.sim.stage.viewport_base + 0x40)])
        self.assertEqual(struct.base.addr, self.sim.stage.viewport_base)
        self.assertTrue(mod.install(cave_addr=self.sim.stage.cave).ok)
        self.sim.frame(1)
        self.assertEqual(self.sim.view_rect(1), (320, 0, 320, 448))

    def test_the_install_report_says_which_route_the_split_uses(self) -> None:
        table = make_table(self.sim.stage, with_viewports=True)
        mod = CoopMod(self.pine, table, CoopConfig(split=Split.VERTICAL))
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any(n.startswith("viewports: 11 writes") for n in report.notes), report.notes)
        self.assertTrue(any("640x448" not in n and "320x448" in n for n in report.notes), report.notes)

    def test_an_unmeasured_disc_reports_the_value_route_as_the_only_one(self) -> None:
        table = make_table(self.sim.stage)
        mod = CoopMod(self.pine, table, CoopConfig(split=Split.VERTICAL))
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("viewports: left to the game" in n for n in report.notes), report.notes)
        self.assertTrue(any("view_mode" in n for n in report.notes), report.notes)

    def test_status_reports_the_view_route(self) -> None:
        mod = self.mod()
        self.sim.frame(1)
        status = mod.status()
        self.assertEqual(status["views"], "viewports: 11 writes, rectangles placed")
