"""Calls end to end: the half of the mod that makes the game *do* something.

A write can set a word. It cannot spawn the second actor, it cannot ask a view
mode setter to rebuild the viewports, and it cannot bind a pad through whatever
the game's own binding routine is. Those are the jobs of the calls, and the
property that matters is the opposite of the one the writes need.

The engine rewrites its state every frame, so a write is worth nothing unless it
is re-applied every frame. A call is worth nothing if it *is* re-applied every
frame -- "spawn the partner" run sixty times a second is sixty partners. So the
interesting tests here are about counting: a one-shot call has to reach the game
exactly once, across ticks, frames and refills; a call declared to repeat has to
actually repeat; and a call that has not been measured must not be made at all,
because the cave would be jumping into an address nobody has looked at.
"""

from __future__ import annotations

import unittest

from aecoops import cave
from aecoops.addresses import CallSpec
from aecoops.coop import CoopMod
from aecoops.pine import Pine

from .gamesim import FakeAEC3, make_table


class CallCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        # Two functions that are "the game's own code": the cave may call them, and
        # the fake console records every call with its argument.
        self.spawn = 0x00160000
        self.setter = 0x00170000
        self.sim.stage.external = (self.spawn, self.setter)
        self.table = make_table(self.sim.stage)
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def declare(self, name: str, addr: int | None, arg: int | None, **kwargs) -> CallSpec:
        spec = CallSpec(name=name, addr=addr, arg=arg, **kwargs)
        self.table.calls.append(spec)
        return spec

    def install(self, **kwargs) -> CoopMod:
        mod = CoopMod(self.pine, self.table, kwargs.pop("config", None))
        report = mod.install(cave_addr=self.sim.stage.cave, **kwargs)
        self.assertTrue(report.ok, report.errors)
        return mod

    def _source_word(self) -> int:
        """A word the game owns that the fake engine leaves alone.

        A spare word rather than a real actor field on purpose: the point of the
        indirect argument is *when* it is read, and a value the test sets after the
        install can only reach `$a0` if the cave read it at call time.
        """
        return 0x00110020


class TestDeclaredCalls(CallCase):
    """What the worksheet turns into: calls, notes, or nothing at all."""

    def test_a_measured_call_is_queued(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [cave.call(self.spawn, 1)])
        self.assertEqual(plan.call_labels, ["spawn_partner"])
        self.assertEqual(plan.calls_once, ["spawn_partner"])
        self.assertIn("spawn_partner", plan.describe_calls())

    def test_planning_does_not_spend_a_call(self) -> None:
        # `plan()` is also what `status` and the dry runs build, so it must be a
        # question, not a decision: a call it marks spent is a call that is never
        # made.
        self.declare("spawn_partner", self.spawn, 1)
        mod = CoopMod(self.pine, self.table)
        self.assertEqual(mod.plan().calls, mod.plan().calls)
        self.assertEqual(mod.plan().calls_once, ["spawn_partner"])

    def test_a_repeating_call_is_never_marked_once(self) -> None:
        self.declare("rebuild_views", self.setter, 2, once=False)
        mod = CoopMod(self.pine, self.table)
        self.assertEqual(mod.plan().calls, [cave.call(self.setter, 2)])
        self.assertEqual(mod.plan().calls_once, [])

    def test_an_unmeasured_call_is_skipped_in_silence(self) -> None:
        # A blank in the worksheet is not a warning. The mod still runs -- it just
        # cannot do the part that needed a function nobody has found yet, and
        # `doctor` is where that shows up.
        self.declare("spawn_partner", None, None, note="find this")
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [])
        self.assertEqual(plan.warnings, [])
        self.assertEqual(mod.status()["calls"], "no calls")

    def test_a_half_measured_call_is_refused_out_loud(self) -> None:
        # An address with no argument is the dangerous case: jumping into code we
        # found while handing it a value we invented. The call is dropped, nothing is
        # called, and the reason is stated -- on the pass after it is first noticed,
        # which is when a note surfaces.
        self.declare("spawn_partner", self.spawn, None)
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [])
        warnings = [w for w in plan.warnings if "calls.spawn_partner" in w]
        self.assertEqual(len(warnings), 1, plan.warnings)
        self.assertIn("not both", warnings[0])
        self.sim.frame(1)
        self.assertEqual(self.sim.called(self.spawn), [])

    def test_a_problem_with_the_worksheet_is_reported_once_not_every_pass(self) -> None:
        # The condition ("this call has no argument") does not change while the mod
        # runs, so a watchdog that repeated it four times a second would bury the
        # warnings that are actually new.
        self.declare("spawn_partner", self.spawn, None)
        mod = CoopMod(self.pine, self.table)
        # Said once at install, and never again while the worksheet says the same.
        said = [w for w in mod.install(cave_addr=self.sim.stage.cave).notes if "calls.spawn_partner" in w]
        for _ in range(6):
            self.sim.frame(1)
            said.extend(w for w in mod.tick().warnings if "calls.spawn_partner" in w)
        self.assertEqual(len(said), 1, said)

    def test_the_same_call_declared_once_and_repeating_is_refused(self) -> None:
        # Both halves of this worksheet ask for the same function with the same
        # argument. The cave cannot tell the host which of the two slots it ran, so
        # the one-shot half would be re-sent every refill for ever -- one partner,
        # sixty times a second.
        self.declare("spawn_partner", self.spawn, 5)
        self.declare("keep_spawning", self.spawn, 5, once=False)
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [cave.call(self.spawn, 5)])
        self.assertEqual(plan.call_labels, ["keep_spawning"])
        self.assertIn("told apart", " ".join(plan.warnings))

    def test_two_one_shot_calls_with_the_same_pair_are_both_kept(self) -> None:
        # Same shape, but both one-shot: the cave makes one call per slot, so both
        # are consumed together and the ambiguity costs nothing.
        self.declare("spawn_a", self.spawn, 5)
        self.declare("spawn_b", self.spawn, 5)
        mod = CoopMod(self.pine, self.table)
        self.assertEqual(len(mod.plan().calls), 2)

    def test_an_indirect_argument_pointing_at_nothing_is_refused(self) -> None:
        # Bit 31 set and a zero source is "pass whatever is at address 0", which is
        # not a handle in any game. Better a note than a spawner handed garbage.
        self.declare("spawn_partner", self.spawn, 0x80000000)
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [])
        self.assertIn("address 0", " ".join(plan.warnings))

    def test_a_call_that_reads_its_argument_goes_in_as_an_indirect(self) -> None:
        # The shape a spawner needs most: pass whatever the game has in slot 1's
        # activeness word *right now*, not what it held one watchdog pass ago.
        source = self._source_word()
        self.declare("bind_pad", self.setter, source | 0x80000000)
        mod = CoopMod(self.pine, self.table)
        plan = mod.plan()
        self.assertEqual(plan.calls, [(self.setter, source | 0x80000000)])
        self.assertEqual(plan.calls_once, ["bind_pad"])


class TestCallsInTheGame(CallCase):
    """Against a console that rebuilds its own state every frame."""

    def install(self, **kwargs) -> CoopMod:
        mod = CoopMod(self.pine, self.table, kwargs.pop("config", None))
        report = mod.install(cave_addr=self.sim.stage.cave, **kwargs)
        self.assertTrue(report.ok, report.errors)
        return mod

    def _source_word(self) -> int:
        """A word the game owns that the fake engine leaves alone.

        A spare word rather than a real actor field on purpose: the point of the
        indirect argument is *when* it is read, and a value the test sets after the
        install can only reach `$a0` if the cave read it at call time.
        """
        return 0x00110020

    def test_a_one_shot_call_reaches_the_game_exactly_once(self) -> None:
        self.declare("spawn_partner", self.spawn, 7)
        mod = self.install()
        # Several ticks and many frames later, the function has been entered once:
        # the cave consumes the entry, and the host does not re-arm a call it has
        # already recorded as fired.
        for _ in range(4):
            self.sim.frame(5)
            mod.tick()
        self.assertEqual(self.sim.called(self.spawn), [7])

    def test_a_repeating_call_is_made_once_per_refill(self) -> None:
        self.declare("rebuild_views", self.setter, 2, once=False)
        mod = self.install()
        for _ in range(3):
            self.sim.frame(4)
            mod.tick()
        # Three refills, three calls -- one per watchdog pass rather than one per
        # frame, which is the difference between a setter and a stampede.
        self.assertEqual(self.sim.called(self.setter), [2, 2, 2])

    def test_the_tick_says_when_the_call_has_been_made(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = self.install()
        # Written, but nothing has run the frame that would carry it yet.
        self.assertEqual([n for n in mod.tick().notes if n.startswith("called ")], [])
        self.sim.frame(1)
        self.assertIn("called spawn_partner", mod.tick().notes)
        self.sim.frame(1)
        self.assertNotIn("called spawn_partner", mod.tick().notes)
        self.assertEqual(self.sim.called(self.spawn), [1])

    def test_a_call_queued_while_the_game_is_paused_lands_when_it_is_not(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = self.install()
        # No frames: the hook has not run, so the call sits in the cave. The host
        # must not decide it failed and forget about it.
        mod.tick()
        mod.tick()
        self.assertEqual(self.sim.called(self.spawn), [])
        self.sim.frame(1)
        self.assertIn("called spawn_partner", mod.tick().notes)
        self.assertEqual(self.sim.called(self.spawn), [1])

    def test_a_call_made_while_the_game_pauses_does_not_repeat_when_it_resumes(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = self.install()
        self.sim.frame(2)
        mod.tick()
        self.sim.pcsx2.status = 1  # the player opened a menu
        for _ in range(3):
            mod.tick()
        self.sim.pcsx2.status = 0
        self.sim.frame(3)
        mod.tick()
        self.assertEqual(self.sim.called(self.spawn), [1])

    def test_installation_says_what_it_is_about_to_call(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = CoopMod(self.pine, self.table)
        report = mod.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("spawn_partner" in n for n in report.notes), report.notes)
        self.assertEqual(mod.status()["calls"], "spawn_partner")

    def test_an_indirect_argument_carries_the_value_the_game_has_now(self) -> None:
        source = self._source_word()
        self.declare("bind_pad", self.setter, source | 0x80000000)
        mod = self.install()
        self.sim.vm.write32(source, 0x1234)
        self.sim.frame(1)
        mod.tick()
        # The word was read *inside* the cave, on the frame the call was made: the
        # argument is the live value, not a copy the host took a moment ago.
        self.assertEqual(self.sim.called(self.setter), [0x1234])
        self.sim.vm.write32(source, 0x5678)
        self.sim.frame(1)
        mod.tick()
        self.assertEqual(self.sim.called(self.setter), [0x1234])  # still one call

    def test_a_second_host_does_not_spawn_the_partner_twice(self) -> None:
        # Ctrl-C and run `play` again: the cave is still installed, and the new host
        # cannot tell whether the partner was already constructed. Assuming it was is
        # the safer mistake -- a missing second hero is a reload, two of them is a bug.
        self.declare("spawn_partner", self.spawn, 1)
        first = self.install()
        self.sim.frame(3)
        first.tick()
        self.assertEqual(self.sim.called(self.spawn), [1])

        second = CoopMod(self.pine, self.table)  # a new process, in effect
        report = second.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertTrue(any("already installed" in n for n in report.notes), report.notes)
        self.assertTrue(any("left alone" in n for n in report.notes), report.notes)
        for _ in range(3):
            self.sim.frame(3)
            second.tick()
        self.assertEqual(self.sim.called(self.spawn), [1])

    def test_a_call_left_queued_by_the_last_host_still_lands(self) -> None:
        # The opposite case: the previous session armed the call while the game was
        # paused, so it is sitting in the region unrun. Adopting it as pending is what
        # stops the next refill from wiping it.
        self.declare("spawn_partner", self.spawn, 1)
        self.install()  # armed, and no frame has run since
        second = CoopMod(self.pine, self.table)
        report = second.install(cave_addr=self.sim.stage.cave)
        self.assertTrue(report.ok, report.errors)
        self.assertFalse(any("left alone" in n for n in report.notes), report.notes)
        self.sim.frame(1)
        second.tick()
        self.assertEqual(self.sim.called(self.spawn), [1])

    def test_uninstalling_leaves_no_call_behind(self) -> None:
        self.declare("spawn_partner", self.spawn, 1)
        mod = self.install()
        self.sim.frame(2)
        mod.tick()
        self.assertEqual(self.sim.called(self.spawn), [1])
        mod.eject()
        self.sim.frame(3)
        self.assertEqual(self.sim.called(self.spawn), [1])
        self.assertEqual(
            self.sim.vm.read_bytes(self.sim.stage.cave + cave.CALLS_OFFSET, 8),
            b"\x00" * 8,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
