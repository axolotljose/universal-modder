"""Exporting to PCSX2's own patch format (``.pnach``).

The Python side is the installer and the tuning wheel: it finds addresses, proves
the hook runs per frame, and refills the parameter block when a player swaps
characters. But it does not have to be *running* for co-op to work. A ``.pnach``
carries the same cave as plain text that PCSX2 itself applies at boot, so once you
have a working configuration you can freeze it: co-op loads with the game, no
second process, on any machine with the same disc.

The format is one line per write:

    patch=1,EE,00140000,word,0x0C001234
    patch=1,EE,00140100,extended,0x00000001 0x00000002 ...

``1`` means "always on" (``0`` makes it a toggle in PCSX2's patch list), ``EE`` is
the CPU, the address is the physical EE address in hex, and ``extended`` writes a
run of consecutive words -- which is exactly what a code cave and its parameter
block are.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from . import cave

WORDS_PER_LINE = 8


@dataclass
class PnachEntry:
    addr: int
    data: bytes
    enabled: bool = True
    note: str = ""

    def lines(self, ee: str = "EE") -> list[str]:
        if len(self.data) % 4:
            raise ValueError("patch data must be a whole number of words")
        words = [struct.unpack_from("<I", self.data, i)[0] for i in range(0, len(self.data), 4)]
        flag = 1 if self.enabled else 0
        out: list[str] = []
        if len(words) == 1:
            out.append(f"patch={flag},{ee},{self.addr:08X},word,0x{words[0]:08X}")
            return out
        for i in range(0, len(words), WORDS_PER_LINE):
            chunk = words[i : i + WORDS_PER_LINE]
            start = self.addr + i * 4
            payload = " ".join(f"0x{w:08X}" for w in chunk)
            out.append(f"patch={flag},{ee},{start:08X},extended,{payload}")
        return out


def build_from_words(
    site: int,
    site_word: int,
    cave_addr: int,
    gametitle: str = "Ape Escape 3",
    serial: str = "",
    crc: str = "",
    comment: str = "",
    enabled: bool = True,
    entries: list[tuple[int, int]] | None = None,
    max_entries: int = cave.MAX_ENTRIES,
) -> str:
    """Build a ``.pnach`` from values read out of the running game.

    ``site_word`` is the instruction currently at ``site`` -- the ``jal`` we are
    displacing. Everything else is derived, so the file cannot disagree with the
    game it was made for.
    """
    callee = cave.callee_of_jal(site_word, site)
    interpreter = cave.build_interpreter(cave_addr, site, callee, max_entries=max_entries)

    head = [
        f"gametitle={gametitle}" + (f" [{serial}]" if serial else ""),
        f"// co-op split screen with Kei and Yumi -- built by aecoops",
    ]
    if crc:
        head.append(f"// disc CRC {crc.upper()} (this is the name PCSX2 looks for in cheats/)")
    if comment:
        head.extend(f"// {line}" for line in comment.splitlines())
    head.append(
        "// the interpreter is written into unused RAM, then one jal in the game's "
        "per-frame path is redirected into it"
    )
    head.append("")

    out = list(head)
    out += PnachEntry(cave_addr, interpreter.code, enabled, "cave").lines()
    block = cave.encode_block(entries or [], max_entries)
    out += PnachEntry(cave.param_block_at(cave_addr), block, enabled, "parameter block").lines()
    out += PnachEntry(site, interpreter.trampoline, enabled, "trampoline").lines()
    return "\n".join(out) + "\n"


def write(text: str, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def pnach_name(serial: str, crc: str = "") -> str:
    """PCSX2 looks for ``<CRC>.pnach`` (or ``<serial>.pnach``) in ``cheats/``."""
    if crc:
        return f"{crc.upper()}.pnach"
    return f"{serial.upper().replace('-', '').replace('_', '')}.pnach"


def make_entries_from_config(
    split_value: int,
    actor_count_addr: int | None,
    partner_addr: int | None,
    char_ids: list[tuple[int, int]] | None = None,
    tether: list[tuple[int, int]] | None = None,
    view_mode_addr: int | None = None,
) -> list[tuple[int, int]]:
    """The frozen configuration a standalone ``.pnach`` bakes in.

    This is where "either Kei or Yumi" stops being a menu and becomes four
    constants: the two character ids and the two pad ports.
    """
    out: list[tuple[int, int]] = []
    if view_mode_addr is not None:
        out.append(cave.const(view_mode_addr, split_value))
    if actor_count_addr is not None:
        out.append(cave.const(actor_count_addr, 2))
    if partner_addr is not None:
        out.append(cave.const(partner_addr, 1))
    for addr, value in char_ids or []:
        out.append(cave.const(addr, value))
    for addr, src in tether or []:
        out.append(cave.copy(addr, src))
    return out


def explain(text: str) -> str:
    """Summarise a ``.pnach`` back, for the ``aecoops pnach --explain`` path."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith("patch="):
            continue
        parts = line.split(",")
        if len(parts) < 5:
            continue
        counts[parts[3]] = counts.get(parts[3], 0) + 1
    if not counts:
        return "no patch lines"
    return ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
