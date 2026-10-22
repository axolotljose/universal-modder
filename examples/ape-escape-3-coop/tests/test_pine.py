"""PINE client tests, against a server implemented from PCSX2's own C++."""

from __future__ import annotations

import struct
import unittest

from aecoops.pine import (
    MAX_REPLY,
    Pine,
    PineCommandFailed,
    PineNotRunning,
    wait_for_pine,
)

from .fakepine import FakePCSX2


class TestPineProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakePCSX2()
        self.pine = Pine(slot=self.fake.port, timeout=5.0).connect()

    def tearDown(self) -> None:
        self.pine.close()
        self.fake.close()

    def test_status_and_strings(self) -> None:
        self.assertEqual(self.pine.status(), 0)
        self.assertTrue(self.pine.version().startswith("PCSX2"))
        self.assertEqual(self.pine.title(), "Ape Escape 3")
        self.assertEqual(self.pine.serial(), "SCUS-97501")
        self.assertEqual(self.pine.uuid(), "a1b2c3d4")
        self.assertEqual(self.pine.disc_version(), "1.00")

    def test_strings_are_nul_terminated_and_length_prefixed(self) -> None:
        # The reply carries u32 size including the terminator; a client that
        # forgets the terminator ends up with a stray byte per call.
        self.assertNotIn("\x00", self.pine.title())

    def test_reads_of_every_width(self) -> None:
        self.fake.ram[0x00200000:0x00200008] = struct.pack("<Q", 0x0102030405060708)
        self.assertEqual(self.pine.read8(0x00200000), 0x08)
        self.assertEqual(self.pine.read16(0x00200000), 0x0708)
        self.assertEqual(self.pine.read32(0x00200000), 0x05060708)
        self.assertEqual(self.pine.read64(0x00200000), 0x0102030405060708)

    def test_writes_of_every_width(self) -> None:
        self.pine.write8(0x00200000, 0xAA)
        self.pine.write16(0x00200002, 0xBBCC)
        self.pine.write32(0x00200004, 0xDDEEFF11)
        self.pine.write64(0x00200008, 0x1122334455667788)
        self.assertEqual(self.fake.ram[0x00200000], 0xAA)
        self.assertEqual(self.fake.ram[0x00200002:0x00200004], b"\xcc\xbb")
        self.assertEqual(self.fake.ram[0x00200004:0x00200008], b"\x11\xff\xee\xdd")
        self.assertEqual(self.fake.ram[0x00200008:0x00200010], b"\x88\x77\x66\x55\x44\x33\x22\x11")

    def test_rejects_bad_width(self) -> None:
        with self.assertRaises(ValueError):
            self.pine.read(0x00200000, 3)

    def test_batched_read_is_one_round_trip(self) -> None:
        for i in range(64):
            struct.pack_into("<I", self.fake.ram, 0x00200000 + i * 4, i * 0x1000)
        before = self.fake.requests
        values = self.pine.read_words([0x00200000 + i * 4 for i in range(64)])
        self.assertEqual(values, [i * 0x1000 for i in range(64)])
        self.assertEqual(self.fake.requests - before, 1)

    def test_read_bytes_spans_the_batch_size(self) -> None:
        # 100 KiB at 4 bytes a read means several batches; the seam is where
        # off-by-one bugs hide.
        blob = bytes((i * 7) % 256 for i in range(100 * 1024))
        self.fake.ram[0x00300000:0x00300000 + len(blob)] = blob
        self.assertEqual(self.pine.read_bytes(0x00300000, len(blob)), blob)

    def test_unaligned_read_bytes(self) -> None:
        self.fake.ram[0x00200101:0x00200105] = b"\x01\x02\x03\x04"
        self.assertEqual(self.pine.read_bytes(0x00200101, 4), b"\x01\x02\x03\x04")
        self.assertEqual(self.pine.read_bytes(0x002000FF, 4), bytes(self.fake.ram[0x002000FF:0x00200103]))

    def test_unaligned_write_bytes_keeps_neighbours(self) -> None:
        self.fake.ram[0x00200200:0x00200208] = b"\xAA" * 8
        self.pine.write_bytes(0x00200201, b"\x01\x02\x03")
        self.assertEqual(bytes(self.fake.ram[0x00200200:0x00200208]), b"\xAA\x01\x02\x03\xAA\xAA\xAA\xAA")

    def test_write_bytes_round_trip(self) -> None:
        data = bytes((i * 13) % 256 for i in range(50_000))
        self.pine.write_bytes(0x00400001, data)
        self.assertEqual(self.pine.read_bytes(0x00400001, len(data)), data)

    def test_empty_io_is_a_no_op(self) -> None:
        self.assertEqual(self.pine.read_bytes(0x00200000, 0), b"")
        self.pine.write_bytes(0x00200000, b"")
        self.assertEqual(self.pine.read_words([]), [])

    def test_failure_drops_the_connection(self) -> None:
        # PCSX2 resets its receive state after a 0xFF, so staying connected would
        # silently desync every later reply.
        self.fake.fail_next = True
        with self.assertRaises(PineCommandFailed):
            self.pine.status()
        self.assertFalse(self.pine.connected)
        self.fake.fail_next = False
        self.pine.reconnect()
        self.assertEqual(self.pine.status(), 0)

    def test_no_vm_is_reported_as_failure(self) -> None:
        self.fake.status = 2
        with self.assertRaises(PineCommandFailed):
            self.pine.read32(0x00200000)

    def test_unreachable_port(self) -> None:
        closed = FakePCSX2()
        port = closed.port
        closed.close()
        with self.assertRaises(PineNotRunning):
            Pine(slot=port, timeout=0.5).connect()

    def test_savestate_slots(self) -> None:
        self.pine.save_state(3)
        self.pine.load_state(4)
        self.assertEqual(self.fake.saves, [3])
        self.assertEqual(self.fake.loads, [4])
        with self.assertRaises(ValueError):
            self.pine.save_state(999)

    def test_require_running(self) -> None:
        self.assertTrue(self.pine.require_running().running)
        self.fake.status = 1
        from aecoops.pine import PineUnusable

        with self.assertRaises(PineUnusable):
            self.pine.require_running()

    def test_info_tolerates_a_dead_vm(self) -> None:
        self.fake.status = 2
        info = self.pine.info()
        self.assertFalse(info.running)
        self.assertIn("shutdown", str(info).lower())

    def test_request_size_guard(self) -> None:
        with self.assertRaises(ValueError):
            self.pine._roundtrip(b"\x00" * 700_000)

    def test_wait_for_pine_returns_a_live_connection(self) -> None:
        # PCSX2 serves one client at a time, so the current one has to let go first.
        self.pine.close()
        handle = wait_for_pine(self.fake.port, timeout=5.0, poll=0.05)
        self.assertEqual(handle.status(), 0)
        handle.close()

    def test_only_one_client_is_served_at_a_time(self) -> None:
        # A property of the real server worth pinning down: the socket accepts a
        # second connection into the backlog but never reads from it, so a second
        # client silently times out instead of being told it is second in line.
        second = Pine(slot=self.fake.port, timeout=0.4).connect()
        with self.assertRaises(TimeoutError):
            second.status()
        second.close()

    def test_reply_size_limit_is_respected_by_batching(self) -> None:
        # A batch larger than PCSX2's 450000-byte reply ceiling would come back as
        # a bare 0xFF; the client must never build one.
        blob = bytes(200 * 1024)
        self.fake.ram[0x00500000:0x00500000 + len(blob)] = blob
        self.assertEqual(self.pine.read_bytes(0x00500000, len(blob)), blob)
        self.assertLess(len(blob) + 5, MAX_REPLY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
