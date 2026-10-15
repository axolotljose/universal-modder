"""One small text payload: what this install knows, in a form you can paste.

The numbers this mod is missing can only be measured on the machine the game is
running on. The person doing the measuring is often not sitting in front of *that*
machine -- they are reading a chat on a phone, with a terminal open somewhere else --
so the useful output is not a log file, it is a payload: small enough to paste in one
go, complete enough to act on, and containing nothing that should not circulate.

That last property is structural, not a promise. This module reads exactly two
things: the address worksheet (addresses, offsets, names, dates) and the emulator's
own metadata (serial, title, CRC, run state). It never opens a game file or an
emulator memory buffer, so there is no code path here that *could* put game data,
extracted assets or disassembled code into the text. The journal is the closest call,
and it is deliberately summarised by count and address only -- its payload is original
instruction bytes, which is game code, and it has no business in a chat window.
"""

from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass, field

#: A bundle that has outgrown this is no longer one paste, which is its whole point.
COMFORTABLE_BYTES = 8192


@dataclass
class Disc:
    """The emulator's own metadata about what is running."""

    title: str = ""
    serial: str = ""
    crc: str = ""
    state: str = ""
    note: str = ""

    def line(self) -> str:
        if not (self.title or self.serial):
            return "disc    " + (self.note or "unknown")
        bits = []
        if self.serial:
            bits.append(self.serial)
        if self.crc:
            bits.append(f"crc {self.crc.upper()}")
        if self.title:
            bits.append(f'"{self.title}"')
        if self.state:
            bits.append(f"state {self.state}")
        out = "disc    " + ", ".join(bits)
        if self.note:
            out += f"  ({self.note})"
        return out


@dataclass
class Install:
    """What the mod currently has in the game, if anything."""

    cave: int | None = None
    site: int | None = None
    journal_writes: int = 0
    journal_at: str = ""
    note: str = ""

    def line(self) -> str:
        if self.note:
            return f"install {self.note}"
        if self.cave is None:
            return "install nothing installed (no cave)"
        out = f"install cave {self.cave:#010x}"
        if self.site is not None:
            out += f", hook site {self.site:#010x}"
        if self.journal_writes:
            out += f", journal {self.journal_writes} writes at {self.journal_at}"
        return out


@dataclass
class Bundle:
    text: str
    unresolved: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.text)


def _entry_lines(table, full: bool) -> tuple[list[str], list[str]]:
    """One line per entry, and the list of paths that are still blank."""
    lines: list[str] = []
    missing: list[str] = []

    def found(addr) -> str:
        return f"{int(addr):#010x}" if addr is not None else "NOT FOUND YET"

    for name, entry in table.globals.items():
        if entry.addr is None and not entry.signature:
            missing.append(name)
        line = f"[global] {name:<22} {found(entry.addr)}"
        if entry.signature:
            line += "  signature"
        if entry.verified:
            line += f"  verified {entry.verified}"
        lines.append(line)
        if full and entry.note:
            lines.append(f"           {entry.note}")

    for name, struct in table.structs.items():
        base = struct.base.addr if struct.base is not None else None
        if base is None and not (struct.base is not None and struct.base.signature):
            missing.append(name)
        line = f"[struct] {name:<22} base {found(base)}  stride {struct.stride or 0:#x}  count {struct.count}"
        if struct.base is not None and struct.base.verified:
            line += f"  verified {struct.base.verified}"
        lines.append(line)
        for field_name, f in struct.fields.items():
            path = f"{name}.{field_name}"
            if f.offset is None:
                missing.append(path)
            line = f"           .{field_name:<21} offset {f.offset:#04x}" if f.offset is not None else f"           .{field_name:<21} offset NOT FOUND YET"
            lines.append(line)
            if full and f.note:
                lines.append(f"             {f.note}")

    for spec in table.calls:
        if not spec.ready:
            missing.append(f"calls.{spec.name}")
        line = (
            f"[call]   {spec.name:<22} addr {found(spec.addr)}  arg {found(spec.arg)}  "
            f"{'once' if spec.once else 'every refill'}"
        )
        if spec.verified:
            line += f"  verified {spec.verified}"
        lines.append(line)

    return lines, missing


def build_bundle(
    table,
    disc: Disc | None = None,
    install: Install | None = None,
    extras: list[tuple[str, str]] | None = None,
    full: bool = False,
    now: float | None = None,
) -> Bundle:
    """Render the worksheet, the disc and the install state as one pasteable text."""
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now if now is not None else time.time()))
    out: list[str] = []
    out.append("aecoops measurement bundle")
    out.append(f"written {stamp}")
    out.append(f"python  {platform.python_version()} on {platform.system().lower()}")
    out.append(f"table   {table.game}" + (f" ({table.path})" if table.path else ""))
    out.append((disc or Disc(note="not checked (PINE not asked)")).line())
    out.append((install or Install()).line())

    lines, missing = _entry_lines(table, full)
    out.append("")
    out.append(f"entries {len(lines)} lines; {len(missing)} still to find")
    out.append("")
    out.extend(lines)

    if missing:
        out.append("")
        # Wrapped, because this bundle is read on a phone as often as in a terminal
        # and a 400-character line is a horizontal scroll with no visible end.
        out.extend(_wrap("still to find: ", missing))

    for name, text in extras or []:
        out.append("")
        out.append(f"-- {name} --")
        out.append(text.rstrip("\n"))

    out.append("")
    out.append(
        "This is addresses, names and counts from my own machine -- no game data. "
        "Paste it as-is when asking for help. (An ISO, a savestate or a RAM dump is "
        "never needed and should never be sent.)"
    )
    return Bundle(text="\n".join(out) + "\n", unresolved=missing)


def _wrap(label: str, names: list[str], width: int = 88) -> list[str]:
    """Comma-separated names, labelled, wrapped at ``width`` and indented after it."""
    lines: list[str] = []
    current = label
    indent = " " * len(label)
    for index, name in enumerate(names):
        piece = name + ("," if index < len(names) - 1 else "")
        if len(current) + len(piece) + 1 > width and current.strip() != label.strip():
            lines.append(current.rstrip())
            current = indent
        current += piece + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def ascii_only(text: str) -> str:
    """The bundle is pasted into terminals, chats and notes: keep it plain ASCII."""
    return text.encode("ascii", "replace").decode("ascii")
