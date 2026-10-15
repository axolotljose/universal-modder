"""Discovery tests: the properties that make an address findable.

The important one is liveness. "This function is called every frame" cannot be
read off a disassembly, so the harness measures it: install the cave on a
candidate, count ticks against wall time, and compare against a frame rate. That
measurement is tested here against a simulated console whose frame rate we control.
"""

from __future__ import annotations

import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from aecoops import cave, discover
from aecoops.pine import Pine
from aecoops.scan import EE_RAM

from .fakepine import FakePCSX2
from .gamesim import FakeAEC3, Stage

WINDOW = [(0x00100000, 0x00140000)]


class TestCounters(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakePCSX2()
        self.pine = Pine(slot=self.fake.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.fake.close()

    def test_finds_a_frame_counter_among_noise(self) -> None:
        # A tight window on purpose. The filter compares deltas between samples,
        # and a sample of 32 MB takes long enough over a socket that the *read
        # time itself* jitters by more than the tolerance -- which is a property of
        # the transport, not of the counter. The frame counter is found by scanning
        # a small window once candidates are known, so that is what this tests.
        window = [(0x00110000, 0x00110080)]
        counter = 0x00110000
        struct.pack_into("<I", self.fake.ram, 0x00110040, 0x42424242)  # static noise
        stop = threading.Event()

        def run() -> None:
            step = 0
            value = 0
            while not stop.is_set():
                step += 1
                # The steady one: +1 every iteration, like a frame counter.
                value += 1
                struct.pack_into("<I", self.fake.ram, counter, value)
                # A jittery one: mostly +1, with a jump every few iterations, the
                # way a stage timer stutters. It must not be mistaken for a frame
                # counter just because it also increases.
                struct.pack_into("<I", self.fake.ram, 0x00110010, value + (7 if step % 3 == 0 else 0))
                time.sleep(0.005)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            found = discover.find_counters(self.pine, window, interval=0.3, samples=3)
        finally:
            stop.set()
            thread.join(timeout=2.0)
        self.assertTrue(found)
        # The steady one ranks first. The stutter may legitimately *also* be a
        # candidate -- a 12 % spread is within what a socket-sampled frame counter
        # shows, and rejecting it would mean rejecting the real one on a busy
        # machine -- but it must never outrank the steady one just because its
        # sampled delta happened to come out larger.
        self.assertEqual(found[0].addr, counter)
        addresses = [c.addr for c in found]
        if 0x00110010 in addresses:
            self.assertGreater(addresses.index(0x00110010), 0)
        deltas = [found[0].values[i + 1] - found[0].values[i] for i in range(len(found[0].values) - 1)]
        self.assertLessEqual(max(deltas) - min(deltas), 3, deltas)

    def test_a_static_word_is_not_a_counter(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x00110000, 7)
        self.assertEqual(discover.find_counters(self.pine, WINDOW, interval=0.1, samples=2), [])

    def test_a_jittery_timer_is_not_a_frame_counter(self) -> None:
        # A stage timer that stutters -- it stops while a menu is open, it counts
        # in odd steps -- must not be mistaken for the frame counter.
        addr = 0x00110000
        values = [10, 60, 63]
        index = {"i": 0}

        class Stepper:
            """Feeds find_counters a different value on each sampling pass."""

            def __init__(self, fake: FakePCSX2) -> None:
                self.fake = fake

            def read_bytes(self, a: int, length: int) -> bytes:
                # Write first, then read: returning the pre-write bytes would make
                # every sampling pass see the *previous* value, which is the kind
                # of off-by-one that makes a test quietly check nothing.
                if a <= addr < a + length:
                    struct.pack_into("<I", self.fake.ram, addr, values[min(index["i"], 2)])
                    index["i"] += 1
                return bytes(self.fake.ram[a : a + length])

        found = discover.find_counters(Stepper(self.fake), WINDOW, interval=0.0, samples=3, min_delta=1)
        self.assertEqual([c.addr for c in found if c.addr == addr], [])

    def test_a_steady_counter_survives_sampling_jitter(self) -> None:  # noqa: D401
        # The acceptance rule has to tolerate a few frames of jitter, because the
        # samples are taken over a socket from a network of two processes.
        addr = 0x00110000
        values = [100, 160, 219]  # deltas 60 and 59
        index = {"i": 0}

        class Stepper:
            def __init__(self, fake: FakePCSX2) -> None:
                self.fake = fake

            def read_bytes(self, a: int, length: int) -> bytes:
                # Write first, then read: returning the pre-write bytes would make
                # every sampling pass see the *previous* value, which is the kind
                # of off-by-one that makes a test quietly check nothing.
                if a <= addr < a + length:
                    struct.pack_into("<I", self.fake.ram, addr, values[min(index["i"], 2)])
                    index["i"] += 1
                return bytes(self.fake.ram[a : a + length])

        found = discover.find_counters(Stepper(self.fake), WINDOW, interval=0.0, samples=3)
        self.assertIn(addr, [c.addr for c in found])

    def test_find_frame_counter_raises_when_nothing_moves(self) -> None:
        with self.assertRaises(LookupError):
            discover.find_frame_counter(self.pine, WINDOW, interval=0.05, samples=2)

    def test_find_frame_counter_rejects_a_non_frame_rate(self) -> None:
        # A counter that advances 3 times a second is a stage timer, not a frame.
        addr = 0x00110000
        stop = threading.Event()

        def run() -> None:
            value = 0
            while not stop.is_set():
                value += 1
                struct.pack_into("<I", self.fake.ram, addr, value)
                time.sleep(0.4)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            # 3 Hz, not a frame rate, and too slow for the min_delta window.
            with self.assertRaises(LookupError):
                discover.find_frame_counter(self.pine, WINDOW, interval=0.1, samples=3, min_delta=1, max_delta=20)
        finally:
            stop.set()
            thread.join(timeout=2.0)


class TestDifferencing(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakePCSX2()
        self.pine = Pine(slot=self.fake.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.fake.close()

    def test_snapshot_diff_finds_changes(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x00110000, 1)
        before = discover.Snapshot.take(self.pine, WINDOW)
        struct.pack_into("<I", self.fake.ram, 0x00110000, 2)
        struct.pack_into("<I", self.fake.ram, 0x00110004, 9)
        after = discover.Snapshot.take(self.pine, WINDOW)
        changes = before.diff(after)
        self.assertEqual(changes, [(0x00110000, 1, 2), (0x00110004, 0, 9)])

    def test_snapshot_find_and_near(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x00110000, 0x1234)
        snapshot = discover.Snapshot.take(self.pine, WINDOW)
        self.assertIn(0x00110000, snapshot.find(0x1234))
        around = dict(snapshot.near(0x00110000, 2))
        self.assertEqual(around[0x00110000], 0x1234)

    def test_a_snapshot_survives_a_round_trip_through_a_file(self) -> None:
        # The reason snapshots go to disk at all: the two states worth comparing --
        # a story stage and a two-player minigame -- are minutes and a menu apart.
        struct.pack_into("<I", self.fake.ram, 0x00110000, 0x2A)
        struct.pack_into("<I", self.fake.ram, 0x00110004, 0x42424242)
        taken = discover.Snapshot.take(self.pine, [(0x00110000, 0x00110010)])
        with tempfile.TemporaryDirectory() as tmp:
            path = taken.save(Path(tmp) / "stage.json")
            loaded = discover.Snapshot.load(path)
        self.assertEqual(loaded.words, taken.words)
        self.assertEqual(loaded.ranges, taken.ranges)
        self.assertEqual(loaded.words[0x00110000], 0x2A)

    def test_a_diff_can_be_narrowed_to_small_values(self) -> None:
        # "Which word differs between the two states, and is a small integer?" is
        # the question that finds a view mode; the raw diff of a console is
        # thousands of words and answers nothing.
        before = discover.Snapshot(ranges=(WINDOW,), words={0x1000: 0, 0x1004: 3, 0x1008: 0x40000000})
        after = discover.Snapshot(ranges=(WINDOW,), words={0x1000: 1, 0x1004: 3, 0x1008: 0x40000004})
        everything = before.filtered_diff(after, limit=10)
        self.assertEqual(len(everything), 2)
        small = before.filtered_diff(after, limit=10, max_value=64)
        self.assertEqual(small, [(0x1000, 0, 1)])
        near = before.filtered_diff(after, limit=10, near=0x1008, span=4)
        self.assertEqual(near, [(0x1008, 0x40000000, 0x40000004)])

    def test_watch_returns_changes(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x00110000, 5)

        def mutate() -> None:
            time.sleep(0.05)
            struct.pack_into("<I", self.fake.ram, 0x00110000, 6)

        thread = threading.Thread(target=mutate, daemon=True)
        thread.start()
        changes = discover.watch(self.pine, [(0x00110000, 0x00110010)], span=0.2, limit=4)
        thread.join(timeout=2.0)
        self.assertIn((0x00110000, 5, 6), changes)


class TestSiteProbing(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def _drive_frames(self, hz: float, duration: float) -> threading.Thread:
        stop = time.monotonic() + duration

        def run() -> None:
            period = 1.0 / hz
            while time.monotonic() < stop:
                self.sim.frame(1)
                time.sleep(period)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_a_per_frame_site_is_recognised(self) -> None:
        driver = self._drive_frames(hz=60.0, duration=1.6)
        probe = discover.probe_site(self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=1.0)
        driver.join(timeout=3.0)
        self.assertEqual(probe.verdict, "per frame", probe)
        self.assertGreater(probe.hz, 50.0)
        self.assertFalse(self.sim.cave_installed, "the probe must always uninstall itself")

    def test_a_dead_site_is_reported_as_never_called(self) -> None:
        probe = discover.probe_site(
            self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=0.2
        )
        self.assertEqual(probe.ticks, 0)
        self.assertIn("never called", probe.verdict)

    def test_a_wrong_rate_is_not_per_frame(self) -> None:
        driver = self._drive_frames(hz=20.0, duration=1.3)
        probe = discover.probe_site(
            self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=1.0, expected_hz=60.0
        )
        driver.join(timeout=3.0)
        self.assertNotEqual(probe.verdict, "per frame")
        self.assertIn("not frame rate", probe.verdict)

    def test_probing_something_that_is_not_a_call_is_refused(self) -> None:
        self.sim.vm.write_bytes(self.sim.stage.hook_site, b"\x00\x00\x00\x00")
        with self.assertRaises(ValueError):
            discover.probe_site(self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=0.1)

    def test_probe_leaves_no_trace(self) -> None:
        before_site = self.sim.vm.read_bytes(self.sim.stage.hook_site, 4)
        before_cave = self.sim.vm.read_bytes(self.sim.stage.cave, 32)
        discover.probe_site(self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=0.1)
        self.assertEqual(self.sim.vm.read_bytes(self.sim.stage.hook_site, 4), before_site)
        self.assertEqual(self.sim.vm.read_bytes(self.sim.stage.cave, 32), before_cave)

    def test_refuses_to_probe_over_an_installed_cave(self) -> None:
        from aecoops.coop import CoopConfig, CoopMod

        from .gamesim import make_table

        CoopMod(self.pine, make_table(self.sim.stage), CoopConfig()).install(cave_addr=self.sim.stage.cave)
        with self.assertRaises(RuntimeError):
            discover.probe_site(self.pine, self.sim.stage.hook_site, self.sim.stage.cave, duration=0.1)

    def test_sweep_keeps_the_per_frame_candidate(self) -> None:
        # Two candidates: the real per-frame call, and a code address that is not
        # a jal at all (so it must be skipped without incident).
        driver = self._drive_frames(hz=60.0, duration=2.2)
        results = discover.sweep_sites(
            self.pine,
            [("update", self.sim.stage.hook_site), ("notacall", self.sim.stage.update_fn + 4)],
            self.sim.stage.cave,
            duration=1.2,
        )
        driver.join(timeout=4.0)
        names = [r.verdict for r in results]
        self.assertTrue(any("per frame" in n for n in names), names)

    def test_find_jal_targets_finds_the_caller(self) -> None:
        hits = discover.find_jal_targets(self.pine, self.sim.stage.update_fn, WINDOW)
        self.assertIn(self.sim.stage.hook_site, [h.addr for h in hits])

    def test_disassembly_of_planted_code(self) -> None:
        listing = discover.read_disassembly(self.pine, self.sim.stage.update_fn, 4)
        self.assertEqual(len(listing), 4)
        self.assertIn("addiu $sp, $sp, -32", listing[0])
        self.assertIn("sw $ra, 28($sp)", listing[1])

    def test_probe_is_installed_detection(self) -> None:
        self.assertFalse(discover.probe_is_installed(self.pine, self.sim.stage.hook_site, self.sim.stage.cave))
        self.sim.vm.write_bytes(
            self.sim.stage.hook_site,
            cave.build_interpreter(self.sim.stage.cave, self.sim.stage.hook_site, self.sim.stage.update_fn).trampoline,
        )
        self.assertTrue(discover.probe_is_installed(self.pine, self.sim.stage.hook_site, self.sim.stage.cave))


class TestPoke(unittest.TestCase):
    """The write half of discovery: candidates go in, the screen is the oracle."""

    def setUp(self) -> None:
        self.sim = FakeAEC3()
        self.pine = Pine(slot=self.sim.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.sim.close()

    def _drive_frames(self, hz: float, duration: float) -> threading.Thread:
        stop = time.monotonic() + duration

        def run() -> None:
            while time.monotonic() < stop:
                self.sim.frame(1)
                time.sleep(1.0 / hz)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_a_poked_value_lands_every_frame_and_is_undone_afterwards(self) -> None:
        target = 0x00110020  # a word the fake engine does not rewrite itself
        driver = self._drive_frames(hz=60.0, duration=0.9)
        report = discover.poke_entries(
            self.pine,
            self.sim.stage.hook_site,
            self.sim.stage.cave,
            [cave.const(target, 0x99)],
            duration=0.5,
            expected_hz=60.0,
        )
        driver.join(timeout=3.0)
        self.assertGreater(report.ticks, 5)
        self.assertIn("writing 1 entries", report.verdict)
        self.assertEqual(self.sim.read(target), 0x99)
        # ...and the game is exactly as it was found.
        self.assertFalse(self.sim.cave_installed)
        self.assertEqual(
            self.sim.vm.read_bytes(self.sim.stage.cave, cave.CAVE_CODE_SIZE),
            b"\x00" * cave.CAVE_CODE_SIZE,
        )

    def test_poke_restores_the_site_even_when_the_writes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            discover.poke_entries(
                self.pine,
                self.sim.stage.hook_site,
                self.sim.stage.cave,
                [cave.const(0, 1)],  # address 0 terminates the entry list
                duration=0.1,
            )
        self.assertFalse(self.sim.cave_installed)

    def test_poke_will_not_stomp_a_running_session(self) -> None:
        from aecoops.coop import CoopConfig, CoopMod

        from .gamesim import make_table

        CoopMod(self.pine, make_table(self.sim.stage), CoopConfig()).install(cave_addr=self.sim.stage.cave)
        with self.assertRaises(RuntimeError):
            discover.poke_entries(
                self.pine, self.sim.stage.hook_site, self.sim.stage.cave, [cave.const(0x110020, 1)], duration=0.1
            )

    def test_a_dead_site_is_reported_rather_than_silently_writing_nothing(self) -> None:
        report = discover.poke_entries(
            self.pine, self.sim.stage.hook_site, self.sim.stage.cave, [cave.const(0x110020, 7)], duration=0.2
        )
        self.assertEqual(report.ticks, 0)
        self.assertIn("never called", report.verdict)


class TestStageDefaults(unittest.TestCase):
    def test_default_ranges_cover_ee_ram(self) -> None:
        self.assertEqual(discover.EE_RAM, (0x00100000, 0x02000000))
        self.assertEqual(Stage().hook_site % 4, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
