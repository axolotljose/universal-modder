"""Scanning, address discovery and patch sets, over the fake PINE server."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from aecoops import cave
from aecoops.patcher import Patch, PatchSet, load_all, pick, revert
from aecoops.pine import Pine
from aecoops.scan import Discovery, chase, find, find_pattern, read_c_string, read_int_table

from .fakepine import FakePCSX2


# Scanning all 32 MB is right for real use and far too slow for a test suite, so
# the tests scan a window. Chunking still gets exercised: the window spans several
# 256 KiB chunks.
WINDOW = [(0x00100000, 0x00300000)]


class ScanCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakePCSX2()
        self.pine = Pine(slot=self.fake.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.fake.close()

    def plant(self, addr: int, data: bytes) -> None:
        self.fake.ram[addr : addr + len(data)] = data


class TestScan(ScanCase):
    def test_exact_find(self) -> None:
        self.plant(0x00180000, b"hello world")
        hits = find(self.pine, "hello world", ranges=WINDOW)
        self.assertEqual([h.addr for h in hits], [0x00180000])

    def test_spaced_and_compact_patterns_agree(self) -> None:
        self.plant(0x00181000, bytes.fromhex("11aa33"))
        spaced = find(self.pine, "11 aa 33", ranges=WINDOW)
        compact = find(self.pine, "11aa33", ranges=WINDOW)
        self.assertEqual([h.addr for h in spaced], [0x00181000])
        self.assertEqual([h.addr for h in compact], [0x00181000])

    def test_non_hex_patterns_are_read_as_text(self) -> None:
        # `find(pine, "MONKEY")` should mean the six bytes of the word, not a
        # parse error: reading text in RAM is half of what a scanner is for.
        self.plant(0x00181500, b"MONKEY, 2005")
        self.assertEqual(find(self.pine, "MONKEY", ranges=WINDOW)[0].addr, 0x00181500)

    def test_wildcards(self) -> None:
        self.plant(0x00182000, bytes.fromhex("deadbeef"))
        hits = find_pattern(self.pine, "de ?? be ??", ranges=WINDOW)
        self.assertIn(0x00182000, [h.addr for h in hits])

    def test_pure_wildcard_pattern_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            find_pattern(self.pine, "?? ?? ??")

    def test_chunk_boundaries_are_not_missed(self) -> None:
        # The signature starts five bytes before a 256 KiB chunk boundary; a
        # scanner that forgets to carry the tail across chunks loses it.
        boundary = 0x00100000 + 0x40000
        self.plant(boundary - 5, b"\xAB\xCD\xEF\x12\x34\x56\x78")
        hits = find(self.pine, "ab cd ef 12 34 56 78", ranges=WINDOW)
        self.assertEqual([h.addr for h in hits], [boundary - 5])

    def test_limit_and_multiple_hits(self) -> None:
        for i in range(5):
            self.plant(0x00183000 + i * 0x100, b"zzzzzzzz")
        self.assertEqual(len(find(self.pine, "zzzzzzzz", ranges=WINDOW, limit=3)), 3)

    def test_ranges_are_respected(self) -> None:
        self.plant(0x00184000, b"target!!")
        self.assertEqual(find(self.pine, "target!!", ranges=[(0x00100000, 0x00180000)]), [])
        self.assertEqual(len(find(self.pine, "target!!", ranges=[(0x00180000, 0x00190000)])), 1)

    def test_read_helpers(self) -> None:
        self.plant(0x00185000, b"PCSX2\x00garbage")
        self.assertEqual(read_c_string(self.pine, 0x00185000), "PCSX2")
        for i in range(4):
            struct.pack_into("<I", self.fake.ram, 0x00186000 + i * 4, 10 * i)
        self.assertEqual(read_int_table(self.pine, 0x00186000, 4), [0, 10, 20, 30])

    def test_pointer_chase(self) -> None:
        # chase() reads the pointer at the base, then steps off by each offset in
        # turn: [0x10] lands on a field, [0x10, 0x8] walks one level deeper.
        struct.pack_into("<I", self.fake.ram, 0x00100000, 0x00187000)
        struct.pack_into("<I", self.fake.ram, 0x00187010, 0x00188000)
        self.assertEqual(chase(self.pine, 0x00100000, [0x10]), 0x00187010)
        self.assertEqual(chase(self.pine, 0x00100000, [0x10, 0x8]), 0x00188008)


class TestDiscovery(ScanCase):
    def test_signature_is_found_and_offset_applied(self) -> None:
        self.plant(0x00190000, bytes.fromhex("0011223344556677"))
        entry = Discovery(name="thing", signature="00 11 22 33", signature_offset=4)
        self.assertEqual(entry.resolve(self.pine, WINDOW), 0x00190004)

    def test_pinned_address_wins_without_scanning(self) -> None:
        entry = Discovery(name="pinned", addr=0x00195000, signature="aa bb cc dd")
        self.assertEqual(entry.resolve(self.pine, WINDOW), 0x00195000)

    def test_ambiguous_signature_is_refused(self) -> None:
        self.plant(0x00196000, bytes.fromhex("aabbccdd"))
        self.plant(0x00197000, bytes.fromhex("aabbccdd"))
        entry = Discovery(name="dup", signature="aa bb cc dd")
        with self.assertRaises(LookupError) as caught:
            entry.resolve(self.pine, WINDOW)
        self.assertIn("matched 2", str(caught.exception))

    def test_missing_signature_explains_itself(self) -> None:
        entry = Discovery(name="ghost", signature="fe ed fa ce")
        with self.assertRaises(LookupError) as caught:
            entry.resolve(self.pine, WINDOW)
        self.assertIn("not found in RAM", str(caught.exception))

    def test_unfilled_entry_tells_you_what_to_do(self) -> None:
        entry = Discovery(name="blank")
        with self.assertRaises(LookupError) as caught:
            entry.resolve(self.pine, WINDOW)
        self.assertIn("DISCOVERY.md", str(caught.exception))

    def test_pointer_chain(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x00198000, 0x001A0000)
        entry = Discovery(name="chained", addr=0x00198000, pointer_chain=[0x20])
        self.assertEqual(entry.resolve(self.pine), 0x001A0020)


class TestPatchSet(ScanCase):
    def _set(self, **kwargs) -> PatchSet:
        return PatchSet(
            name="test",
            serials=["SCUS-97501"],
            uuids=[],
            patches=[
                Patch(name="first", addr=0x00200000, data="11223344", expect="aabbccdd"),
                Patch(name="second", addr=0x00200010, data="55667788", expect="00000000"),
            ],
            **kwargs,
        )

    def test_apply_verifies_then_writes(self) -> None:
        self.plant(0x00200000, bytes.fromhex("aabbccdd"))
        self.plant(0x00200010, bytes.fromhex("00000000"))
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "j.json"
            report = self._set().apply(self.pine, journal)
            self.assertTrue(report.ok, report.errors)
            self.assertEqual(report.applied, ["first", "second"])
            self.assertEqual(bytes(self.fake.ram[0x00200000:0x00200004]).hex(), "11223344")
            payload = json.loads(journal.read_text())
            self.assertEqual(payload["entries"][0]["before"], "aabbccdd")
            self.assertEqual(payload["fingerprint"], self._set().fingerprint())

    def test_mismatch_aborts_before_writing_anything(self) -> None:
        self.plant(0x00200000, bytes.fromhex("aabbccdd"))
        self.plant(0x00200010, bytes.fromhex("ffffffff"))  # not what the set expects
        report = self._set().apply(self.pine)
        self.assertFalse(report.ok)
        self.assertTrue(any("refusing to patch" in e for e in report.errors))
        # The good patch must not have been written: a half-applied set is worse
        # than an unapplied one.
        self.assertEqual(bytes(self.fake.ram[0x00200000:0x00200004]).hex(), "aabbccdd")

    def test_second_apply_is_idempotent(self) -> None:
        self.plant(0x00200000, bytes.fromhex("aabbccdd"))
        self.plant(0x00200010, bytes.fromhex("00000000"))
        patchset = self._set()
        self.assertTrue(patchset.apply(self.pine).ok)
        again = patchset.apply(self.pine)
        self.assertTrue(again.ok)
        self.assertEqual(again.skipped, ["first", "second"])
        self.assertEqual(again.applied, [])

    def test_verify_reports_problems(self) -> None:
        self.plant(0x00200000, bytes.fromhex("01010101"))
        self.plant(0x00200010, bytes.fromhex("00000000"))
        problems = self._set().verify(self.pine)
        self.assertEqual(len(problems), 1)
        self.assertIn("expected aabbccdd", problems[0])

    def test_wildcards_in_expect(self) -> None:
        # Wildcards exist because a few bytes of a signature are always volatile:
        # pointers into the heap, a build timestamp, a frame counter.
        patch = Patch(name="wild", addr=0x00200000, data="11223344", expect="aabb????")
        self.assertTrue(patch.wildcarded)
        self.assertTrue(patch.matches(bytes.fromhex("aabb9999")))
        self.assertFalse(patch.matches(bytes.fromhex("aacc9999")))
        nibble = Patch(name="nibble", addr=0x00200000, data="11223344", expect="aab?ccdd")
        self.assertTrue(nibble.matches(bytes.fromhex("aab7ccdd")))
        self.assertFalse(nibble.matches(bytes.fromhex("aab7ccde")))
        self.assertEqual(nibble.wildcard_mask.hex(), "fff0ffff")

    def test_unverifiable_patch_is_flagged(self) -> None:
        patchset = PatchSet(name="blind", patches=[Patch(name="blind", addr=0x00200000, data="11223344")])
        problems = patchset.verify(self.pine)
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot be verified", problems[0])

    def test_overlap_is_a_set_bug(self) -> None:
        patchset = PatchSet(
            name="overlap",
            patches=[
                Patch(name="a", addr=0x00200000, data="1122334455667788"),
                Patch(name="b", addr=0x00200004, data="99aabbcc"),
            ],
        )
        self.assertEqual(len(patchset.overlapping()), 1)
        report = patchset.apply(self.pine)
        self.assertFalse(report.ok)
        self.assertIn("overlaps", report.errors[0])

    def test_journal_round_trip_restores_the_originals(self) -> None:
        self.plant(0x00200000, bytes.fromhex("aabbccdd"))
        self.plant(0x00200010, bytes.fromhex("00000000"))
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "j.json"
            self._set().apply(self.pine, journal)
            self.assertEqual(revert(self.pine, journal), 2)
            self.assertEqual(bytes(self.fake.ram[0x00200000:0x00200004]).hex(), "aabbccdd")
            self.assertEqual(bytes(self.fake.ram[0x00200010:0x00200014]).hex(), "00000000")

    def test_reapply_from_journal(self) -> None:
        self.plant(0x00200000, bytes.fromhex("aabbccdd"))
        self.plant(0x00200010, bytes.fromhex("00000000"))
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "j.json"
            patchset = self._set()
            patchset.apply(self.pine, journal)
            # A savestate load would wipe a memory patch; the journal puts it back.
            self.plant(0x00200000, bytes.fromhex("aabbccdd"))
            self.assertEqual(patchset.apply_from_journal(self.pine, journal), 2)
            self.assertEqual(bytes(self.fake.ram[0x00200000:0x00200004]).hex(), "11223344")

    def test_accepts_only_its_own_disc(self) -> None:
        patchset = self._set()
        ok, why = patchset.accepts("SCUS-97501")
        self.assertTrue(ok)
        ok, why = patchset.accepts("SLUS-12345")
        self.assertFalse(ok)
        self.assertIn("not in", why)
        permissive = PatchSet(name="any")
        self.assertTrue(permissive.accepts("ANYTHING")[0])

    def test_uuid_and_version_constraints(self) -> None:
        patchset = PatchSet(name="t", serials=[], uuids=["a1b2c3d4"], disc_versions=["1.00"])
        self.assertTrue(patchset.accepts("SCUS-97501", "a1b2c3d4", "1.00")[0])
        self.assertFalse(patchset.accepts("SCUS-97501", "deadbeef", "1.00")[0])
        self.assertFalse(patchset.accepts("SCUS-97501", "a1b2c3d4", "2.00")[0])

    def test_fingerprint_changes_with_content(self) -> None:
        one = self._set()
        two = self._set()
        two.patches[0].data = "99887766"
        self.assertNotEqual(one.fingerprint(), two.fingerprint())
        self.assertEqual(one.fingerprint(), self._set().fingerprint())

    def test_bad_patch_definitions_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Patch(name="unaligned", addr=0x00200002, data="11223344")
        with self.assertRaises(ValueError):
            Patch(name="short", addr=0x00200000, data="112233")
        with self.assertRaises(ValueError):
            Patch(name="mismatched", addr=0x00200000, data="11223344", expect="aabb")
        with self.assertRaises(ValueError):
            Patch(name="nothex", addr=0x00200000, data="zzzz")

    def test_save_load_and_pick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._set().save(directory / "one.patchset.json")
            other = PatchSet(name="other", serials=["SLES-00000"], patches=[])
            other.save(directory / "two.patchset.json")
            loaded = load_all(directory)
            self.assertEqual([s.name for s in loaded], ["test", "other"])
            self.assertEqual(pick(loaded, "SCUS-97501").name, "test")
            self.assertEqual(pick(loaded, "SLES-00000").name, "other")
            self.assertIsNone(pick(loaded, "SLUS-99999"))


class TestFreeRegionStability(ScanCase):
    def test_a_zero_region_reads_as_stable(self) -> None:
        self.assertTrue(cave.verify_stable(self.pine, 0x001A0000, 0x100, samples=2, delay=0.0))

    def test_a_used_region_is_rejected(self) -> None:
        struct.pack_into("<I", self.fake.ram, 0x001A0000, 0x1234)
        self.assertFalse(cave.verify_stable(self.pine, 0x001A0000, 0x100, samples=1))

    def test_a_write_between_samples_is_caught(self) -> None:
        # This is the failure mode that matters: RAM that is empty *now* but gets
        # written every frame is exactly where a cave must not go.
        class Ticking:
            def __init__(self, fake: FakePCSX2) -> None:
                self.fake = fake
                self.calls = 0

            def read_bytes(self, addr: int, length: int) -> bytes:
                self.calls += 1
                if self.calls == 3:
                    struct.pack_into("<I", self.fake.ram, addr + 0x40, 0xABCD)
                return bytes(self.fake.ram[addr : addr + length])

        ticking = Ticking(self.fake)
        self.assertFalse(cave.verify_stable(ticking, 0x001A0000, 0x100, samples=3, delay=0.0))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
