"""Assembler tests: every encoding is checked against a hand-computed word.

The point of putting the expected values in by hand is that the assembler cannot
be its own oracle. If both the encoder and the test derived a word the same way,
a wrong encoding would pass.
"""

from __future__ import annotations

import unittest

from aecoops import mips


class TestEncodings(unittest.TestCase):
    def test_r_type(self) -> None:
        self.assertEqual(mips.jr("ra"), 0x03E00008)
        self.assertEqual(mips.addu("t0", "t1", "t2"), 0x012A4021)
        self.assertEqual(mips.subu("v0", "a0", "a1"), 0x00851023)
        self.assertEqual(mips.sll("t2", "t2", 3), 0x000A50C0)
        self.assertEqual(mips.srl("t4", "t4", 1), 0x000C6042)
        self.assertEqual(mips.sltu("v0", "a0", "a1"), 0x0085102B)
        self.assertEqual(mips.jalr("ra", "t9"), 0x0320F809)
        self.assertEqual(mips.nor("t0", "t1", "t2"), 0x012A4027)

    def test_i_type(self) -> None:
        self.assertEqual(mips.addiu("sp", "sp", -0x18), 0x27BDFFE8)
        self.assertEqual(mips.addiu("v0", "zero", 2), 0x24020002)
        self.assertEqual(mips.lui("t0", 0x0014), 0x3C080014)
        self.assertEqual(mips.ori("t0", "t0", 0xFFFF), 0x3508FFFF)
        self.assertEqual(mips.andi("t0", "t1", 0x00FF), 0x312800FF)
        self.assertEqual(mips.sw("ra", 0x14, "sp"), 0xAFBF0014)
        self.assertEqual(mips.lw("t3", 0, "t2"), 0x8D4B0000)
        self.assertEqual(mips.lbu("t0", 0, "a0"), 0x90880000)
        self.assertEqual(mips.slti("v0", "a0", 5), 0x28820005)

    def test_jumps_and_branches(self) -> None:
        self.assertEqual(mips.j(0x00140000), 0x08050000)
        self.assertEqual(mips.jal(0x00140000), 0x0C050000)
        self.assertEqual(mips.beq("t3", "zero", 2), 0x11600002)
        self.assertEqual(mips.bne("t1", "t2", -1), 0x152AFFFF)
        self.assertEqual(mips.blez("t5", 3), 0x19A00003)
        self.assertEqual(mips.bgtz("t5", 3), 0x1DA00003)
        self.assertEqual(mips.bltz("t4", 4), 0x05800004)
        self.assertEqual(mips.bgez("t4", 4), 0x05810004)

    def test_jump_top_bits_come_from_the_delay_slot_pc(self) -> None:
        # A j/jal immediate is only 26 bits; the top four come from (PC+4). The
        # encoder drops them and the decoder puts them back, so a jump inside the
        # EE's own 0x001xxxxx region round trips exactly.
        site = 0x0F123450
        word = mips.jal(0x00140000)
        self.assertEqual(mips.disassemble(word, site).split()[1], "0x00140000")

    def test_unsigned_and_signed_rules(self) -> None:
        self.assertEqual(mips.lui("t0", 0xFFFF), 0x3C08FFFF)  # lui is unsigned
        with self.assertRaises(ValueError):
            mips.addiu("t0", "zero", 0x8000)  # does not fit in 16 signed bits
        self.assertEqual(mips.addiu("t0", "zero", -0x8000), 0x24088000)
        with self.assertRaises(ValueError):
            mips.andi("t0", "zero", 0x10000)

    def test_load_immediate_shapes(self) -> None:
        self.assertEqual(mips.load_immediate("t0", 0), [0x34080000])
        self.assertEqual(mips.load_immediate("t0", -1), [0x2408FFFF])
        self.assertEqual(len(mips.load_immediate("t0", 0x7FFF)), 1)
        self.assertEqual(
            mips.load_immediate("t0", 0x8000),
            [mips.lui("t0", 0), mips.ori("t0", "t0", 0x8000)],
        )
        self.assertEqual(
            mips.load_immediate("t0", 0xDEADBEEF & 0xFFFFFFFF),
            [mips.lui("t0", 0xDEAD), mips.ori("t0", "t0", 0xBEEF)],
        )

    def test_la_is_always_unsigned(self) -> None:
        self.assertEqual(mips.la("t1", 0x001F0000), [mips.lui("t1", 0x001F), mips.ori("t1", "t1", 0)])

    def test_register_aliases(self) -> None:
        self.assertEqual(mips.reg("$t0"), 8)
        self.assertEqual(mips.reg("t0"), 8)
        self.assertEqual(mips.reg("fp"), 30)
        self.assertEqual(mips.reg("s8"), 30)
        self.assertEqual(mips.reg(31), 31)
        with self.assertRaises(ValueError):
            mips.reg("nope")
        with self.assertRaises(ValueError):
            mips.reg(32)

    def test_alignment_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            mips.j(0x00140002)
        with self.assertRaises(ValueError):
            mips.jal(0x00140001)


class TestProgram(unittest.TestCase):
    def test_labels_and_branches(self) -> None:
        p = mips.Program(base=0x00140000)
        p.label("start")
        p.emit(mips.addiu("t0", "zero", 1))
        p.beq("t0", "zero", "done")
        p.emit(mips.NOP)
        p.emit(mips.addiu("t0", "t0", 1))
        p.label("done")
        p.emit(mips.jr("ra"))
        code = p.finish()
        words = _words(code)
        # The branch sits at index 1 and its target at index 4. MIPS measures the
        # displacement from the delay slot, so the immediate is 4 - 1 - 1 = 2.
        self.assertEqual(words[1] & 0xFFFF, 2)
        self.assertEqual(words[1] >> 26, 0x04)  # beq
        self.assertEqual(words[1] >> 21 & 0x1F, mips.reg("t0"))

    def test_forward_and_backward_labels(self) -> None:
        p = mips.Program(base=0x00140000)
        p.label("top")
        p.emit(mips.addiu("t0", "t0", 1))
        p.b("top")
        p.emit(mips.NOP)
        words = _words(p.finish())
        self.assertEqual(words[1] & 0xFFFF, 0xFFFE)  # -2: back to the top

    def test_jump_label_is_resolved(self) -> None:
        p = mips.Program(base=0x00140000)
        p.j("far")
        p.emit(mips.NOP)
        p.emit(mips.NOP)
        p.label("far")
        p.emit(mips.NOP)
        words = _words(p.finish())
        self.assertEqual(mips.disassemble(words[0], 0x00140000).split()[1], "0x0014000c")

    def test_undefined_label_is_an_error(self) -> None:
        p = mips.Program(base=0)
        p.b("nowhere")
        p.emit(mips.NOP)
        with self.assertRaises(ValueError):
            p.finish()

    def test_duplicate_label_is_an_error(self) -> None:
        p = mips.Program(base=0)
        p.label("x")
        with self.assertRaises(ValueError):
            p.label("x")

    def test_emit_bytes_requires_word_sizes(self) -> None:
        p = mips.Program(base=0)
        with self.assertRaises(ValueError):
            p.emit_bytes(b"\x01\x02\x03")

    def test_end_tracks_length(self) -> None:
        p = mips.Program(base=0x00140000)
        p.emit(mips.NOP, mips.NOP)
        self.assertEqual(len(p), 2)
        self.assertEqual(p.end, 0x00140008)

    def test_emit_accepts_lists(self) -> None:
        p = mips.Program(base=0)
        p.emit(mips.load_immediate("t0", 0x12345))
        self.assertEqual(len(p), 2)


class TestDisassembler(unittest.TestCase):
    def test_round_trips(self) -> None:
        cases = [
            (mips.NOP, "nop"),
            (mips.jr("ra"), "jr $ra"),
            (mips.addiu("sp", "sp", -0x18), "addiu $sp, $sp, -24"),
            (mips.lw("t3", 8, "t2"), "lw $t3, 8($t2)"),
            (mips.lui("t0", 0x001F), "lui $t0, 0x001f"),
            (mips.ori("t0", "t0", 0x1234), "ori $t0, $t0, 0x1234"),
            (mips.sll("t4", "t4", 1), "sll $t4, $t4, 1"),
        ]
        for word, expected in cases:
            with self.subTest(word=f"{word:08x}"):
                self.assertEqual(mips.disassemble(word, 0), expected)

    def test_unknown_words_are_not_invented(self) -> None:
        # 0xFC000000 is a COP3 instruction, outside this module's subset. The
        # listing must say so rather than guess a mnemonic.
        line = mips.disassemble(0xFC000000, 0)
        self.assertTrue(line.startswith(".word"), line)
        self.assertIn("op=", line)


def _words(code: bytes) -> list[int]:
    return [int.from_bytes(code[i : i + 4], "little") for i in range(0, len(code), 4)]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
