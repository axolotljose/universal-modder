"""The recipe pointers, and the calls recipe itself.

Every message this tool prints about a missing measurement ends with a pointer into
`docs/DISCOVERY.md`. That is the difference between "not measured" and "not measured,
here is how", and it is a promise the message makes to whoever is holding the pad. A
pointer into a section that was renamed, or a key nobody put in the table, reads as
help while being none -- so it is checked the same way as everything else.

The second half is the recipe the pointers lead to. A recipe that does not mention the
flag it is telling you to use is how a discovery document goes stale.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from aecoops import selftest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "DISCOVERY.md"


class TestRecipePointers(unittest.TestCase):
    def test_every_pointer_resolves_to_a_section(self) -> None:
        problems = selftest.doc_pointer_problems(ROOT)
        self.assertEqual(problems, [], problems)

    def test_the_checker_notices_a_broken_key(self) -> None:
        # The check has to be able to fail, or it is decoration. A missing key and a
        # key pointing at a section that does not exist are the two failure shapes.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "DISCOVERY.md").write_text(
                "# Recipe\n\n## Which section a message means\n\n"
                "| key | section |\n|---|---|\n| `hud` | 7 |\n| `gone` | 99 |\n\n---\n\n"
                "## 1. The frame hook\n\n## 7. The HUD\n",
                encoding="utf-8",
            )
            (root / "aecoops").mkdir()
            (root / "aecoops" / "hud.py").write_text(
                'note = "docs/DISCOVERY.md#hud"  # ok\nother = "docs/DISCOVERY.md#typo"\n',
                encoding="utf-8",
            )
            problems = selftest.doc_pointer_problems(root)
        self.assertTrue(any("typo" in p for p in problems), problems)
        self.assertTrue(any("gone" in p for p in problems), problems)
        self.assertFalse(any("'hud'" in p for p in problems), problems)

    def test_the_doc_still_has_a_section_per_pointer_key(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        keys = re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([^|]+?)\s*\|\s*$", text.split("## Which section a message means")[1].split("\n---", 1)[0], re.M)
        self.assertGreaterEqual(len(keys), 10, keys)
        self.assertIn(("calls", "10"), keys)


class TestTheCallsRecipe(unittest.TestCase):
    """What section 10 has to keep saying, because the tool's messages assume it."""

    def setUp(self) -> None:
        self.text = DOC.read_text(encoding="utf-8")
        self.section = self.text.split("## 10. Calls", 1)[1].split("## Recording what you find", 1)[0]

    def test_it_explains_the_flag_it_recommends(self) -> None:
        self.assertIn("--call", self.section)
        self.assertIn("FUNCTION=ARG", self.section)

    def test_it_covers_the_bit_31_argument(self) -> None:
        # The indirect argument is the one thing about calls that cannot be guessed
        # from the CLI: it is a spelling, and it has a discovery-shaped reason.
        self.assertIn("bit 31", self.section)
        self.assertIn("read at call time", self.section)

    def test_it_covers_the_once_rule_and_the_frozen_patch_case(self) -> None:
        self.assertIn("once", self.section)
        self.assertIn(".pnach", self.section)

    def test_it_says_what_a_call_cannot_do(self) -> None:
        self.assertIn("not a sandbox", self.section)

    def test_the_worksheet_has_calls_to_measure(self) -> None:
        import json

        raw = json.loads((ROOT / "aecoops" / "data" / "addresses.json").read_text(encoding="utf-8"))
        calls = {c["name"]: c for c in raw.get("calls", [])}
        self.assertIn("spawn_partner", calls)
        # Both are unsigned: pointing at a function nobody measured is what the
        # "NOT FOUND YET" state is for.
        for spec in calls.values():
            self.assertIsNone(spec["addr"])
            self.assertIsNone(spec["arg"])
            self.assertEqual(spec["recipe"], "docs/DISCOVERY.md#calls")

    def test_the_worksheet_documents_the_call_fields(self) -> None:
        import json

        raw = json.loads((ROOT / "aecoops" / "data" / "addresses.json").read_text(encoding="utf-8"))
        readme = " ".join(raw["_readme"])
        self.assertIn("calls", readme)
        self.assertIn("DISCOVERY.md#calls", readme)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
