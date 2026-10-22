"""Pre-flight: does the worksheet agree with the game that is actually running?

Finding the numbers is one job. Trusting them is another, and the second one is where
a typo costs the most: a character id with a digit transposed, a struct base that is
off by a slot, a call address that is really the middle of a different function. The
mod would install, write, and the stage would do something strange -- which is a
terrible way to learn that `0x0038` was meant to be `0x0830`.

So before anything is written, the measured entries are read back and checked against
each other and against the running game. Almost every check here is a *cross* check,
because that is what a measurement cannot fake on its own:

* the hook site holds a `jal` (a wrong address is usually not even an instruction);
* the actor table, read through its measured base, stride and field offsets, says the
  hero in slot 0 is one of the two character ids that were measured independently;
* the layout words are values the renderer is actually using right now;
* two calls do not point at the same function, and a call does not point at memory
  that is entirely zero;
* the viewport rectangles lie on the screen instead of off the end of it.

Every check reads. None of them writes, and none of them needs a cave installed, so
this is safe to run at any point -- including from a phone over an adb tunnel, which
is where a "why does the screen go black" question is usually being asked from.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Addresses the EE can plausibly hold code or data in. A "measurement" outside this
#: is almost always a number with a misplaced hex digit.
EE_RAM = (0x00100000, 0x02000000)
EE_SCRATCHPAD = (0x70000000, 0x70004000)

OK = "ok"
NOTE = "note"
WARN = "warn"
SKIP = "skip"


class Reader(Protocol):
    """The little bit of PINE this needs: one aligned word."""

    def read32(self, addr: int) -> int:  # pragma: no cover - protocol
        ...


@dataclass
class Finding:
    subject: str
    level: str
    message: str
    seen: Any = None

    def line(self) -> str:
        mark = {"ok": "ok  ", "note": "note", "warn": "WARN", "skip": "skip"}[self.level]
        return f"  {mark} {self.subject:<28} {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    #: True when nothing was read (no PINE, or nothing measured yet).
    skipped: bool = False
    note: str = ""

    def add(self, subject: str, level: str, message: str, seen: Any = None) -> None:
        self.findings.append(Finding(subject, level, message, seen))

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARN]

    @property
    def checked(self) -> int:
        return len([f for f in self.findings if f.level != SKIP])

    def text(self) -> str:
        out = [f.line() for f in self.findings]
        if self.problems:
            out.append("")
            out.append(f"{len(self.problems)} problem(s) above. Fix those before installing:")
            out.append("  a wrong address here is a write into something else's memory.")
        elif self.checked:
            out.append("")
            out.append(
                f"{self.checked} entries read back and consistent. Nothing was written; "
                "`aecoops install` is the next step."
            )
        else:
            out.append("")
            out.append("nothing measured yet, so there is nothing to check. `aecoops doctor` lists the blanks.")
        return "\n".join(out)


def _read32(reader: Reader, addr: int) -> int | None:
    try:
        return reader.read32(int(addr))
    except Exception:  # noqa: BLE001 - any transport failure means "cannot check"
        return None


def _float_word(word: int) -> float | None:
    value = struct.unpack("<f", struct.pack("<I", word & 0xFFFFFFFF))[0]
    return value if value == value and abs(value) != float("inf") else None


def _plausible_address(addr: int) -> bool:
    return EE_RAM[0] <= addr < EE_RAM[1] or EE_SCRATCHPAD[0] <= addr < EE_SCRATCHPAD[1]


def check_table(reader: Reader, table) -> Report:
    """Read every measured entry back and check the worksheet against the game."""
    report = Report()
    measured = [n for n, d in table.globals.items() if d.addr is not None]
    # A call is a measurement too, and a worksheet whose only measured thing is a call
    # still has something worth checking (that it points at code, not at a zero).
    if not measured and not any(s.base.addr for s in table.structs.values()) and not any(
        c.ready for c in table.calls
    ):
        report.skipped = True
        return report

    resolved = {n: int(d.addr) for n, d in table.globals.items() if d.addr is not None}

    # --- the hook: the one measurement everything else depends on ---------------
    if "frame_hook" in resolved:
        site = resolved["frame_hook"]
        word = _read32(reader, site)
        if word is None:
            report.add("frame_hook", WARN, f"could not read {site:#010x}")
        elif (word >> 26) != 0x03:
            report.add(
                "frame_hook",
                WARN,
                f"{site:#010x} holds {word:#010x}, which is not a jal -- "
                "an install would refuse, so the address is probably off by a few bytes",
            )
        else:
            from .cave import callee_of_jal

            report.add("frame_hook", OK, f"jal at {site:#010x} -> {callee_of_jal(word, site):#010x}")
    else:
        report.add("frame_hook", SKIP, "not measured yet: nothing can be installed without it")

    # --- the two hero ids, and the actor table they should agree with -----------
    hero_ids = {name: int(value) for name, value in table.characters.items()}
    for name, value in hero_ids.items():
        if 0 <= value < 256:
            report.add(f"characters.{name}", OK, f"id {value}")
        else:
            report.add(f"characters.{name}", WARN, f"id {value} is not a believable character id")

    actor = table.structs.get("actor")
    if actor is not None and actor.base.addr is not None and actor.stride:
        base = int(actor.base.addr)
        if not _plausible_address(base):
            report.add("actor.base", WARN, f"{base:#010x} is not in EE RAM; check the address")
        try:
            slot0 = _read32(reader, actor.address_of(base, 0, "char_id"))
            pad0 = _read32(reader, actor.address_of(base, 0, "pad_port"))
        except Exception:  # noqa: BLE001 - an unusable field name is its own finding
            report.add("actor.fields", WARN, "base and stride are measured but a field offset is not")
            slot0 = pad0 = None
        if slot0 is not None:
            if slot0 in hero_ids.values():
                name = next(n for n, v in hero_ids.items() if v == slot0)
                report.add(
                    "actor.char_id",
                    OK,
                    f"slot 0 says {slot0}, which is characters.{name} -- the base, the stride and "
                    "the offset agree",
                )
            else:
                report.add(
                    "actor.char_id",
                    WARN,
                    f"slot 0 says {slot0}, which is neither measured hero id "
                    f"({hero_ids or 'none'}) -- one of the two is wrong, and the base or the "
                    "offset is the likelier one",
                )
        if pad0 is not None and pad0 not in (0, 1):
            report.add("actor.pad_port", WARN, f"slot 0 says pad {pad0}, which is not a pad port")
        if pad0 == 0 and slot0 is not None:
            report.add("actor.pad_port", OK, "slot 0 answers pad 0")
    elif actor is not None and actor.base.addr is None:
        report.add("actor.base", SKIP, "not measured yet: per-player character ids are skipped")
    if actor is not None and actor.base.addr is not None and not actor.stride:
        report.add("actor.stride", WARN, "a base with a stride of 0 makes both slots the same slot")

    # --- the layout words -------------------------------------------------------
    if "view_mode" in resolved:
        word = _read32(reader, resolved["view_mode"])
        known = {int(v): k for k, v in table.layouts.items()}
        if word is None:
            report.add("view_mode", WARN, "could not be read")
        elif word in known:
            report.add("view_mode", OK, f"reads {word} now, which is layouts.{known[word]}")
        else:
            report.add(
                "view_mode",
                NOTE,
                f"reads {word}, which is not one of the measured layouts {known or '{}'} -- "
                "normal in a menu or a cutscene; check it again inside a stage",
            )
        # Two layouts with the same value is not a crash, it is a measurement that has
        # not separated them yet: the mod would write the same word for both splits.
        by_value: dict[int, list[str]] = {}
        for name, value in table.layouts.items():
            if value is not None:
                by_value.setdefault(int(value), []).append(name)
        for value, names in sorted(by_value.items()):
            if len(names) > 1:
                report.add(
                    "layouts",
                    WARN,
                    f"{', '.join(names)} are both {value}: the two splits would be the same "
                    "value, so one of them has not been measured yet",
                )
    else:
        report.add("view_mode", SKIP, "not measured yet: the split falls back to the rectangles")

    # --- counts and flags -------------------------------------------------------
    for name, (low, high, written) in {
        "actor_count": (1, 2, 2),
        "partner_enabled": (0, 1, 1),
        "hud_instances": (0, 4, None),
        "viewport_count": (1, 4, None),
        "quota_shared": (0, 1, 1),
    }.items():
        if name not in resolved:
            report.add(name, SKIP, "not measured yet")
            continue
        word = _read32(reader, resolved[name])
        if word is None:
            report.add(name, WARN, "could not be read")
        elif low <= word <= high:
            tail = f"the mod would write {written} here" if written is not None else "a believable value"
            report.add(name, OK, f"reads {word} ({tail})")
        else:
            report.add(
                name,
                NOTE,
                f"reads {word}, outside the {low}..{high} this is supposed to hold -- "
                "right address, wrong meaning?",
            )

    # --- the rectangles, if they are measured -----------------------------------
    viewport = table.structs.get("viewport")
    if viewport is not None and viewport.base.addr is not None and viewport.stride:
        base = int(viewport.base.addr)
        screen = (640, 448)
        for index in range(min(2, viewport.count)):
            try:
                rect = [
                    _read32(reader, viewport.address_of(base, index, f))
                    for f in ("x", "y", "w", "h")
                ]
            except Exception:  # noqa: BLE001
                report.add(f"viewport[{index}]", SKIP, "a field offset is not measured yet")
                continue
            if None in rect:
                report.add(f"viewport[{index}]", WARN, "could not be read")
                continue
            x, y, w, h = rect
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > screen[0] + 8 or y + h > screen[1] + 8:
                report.add(
                    f"viewport[{index}]",
                    NOTE,
                    f"{x},{y} {w}x{h} does not fit a {screen[0]}x{screen[1]} frame -- if the "
                    "stage is drawing, the base or the field offsets are probably off",
                )
            else:
                report.add(f"viewport[{index}]", OK, f"{x},{y} {w}x{h}")
    else:
        report.add("structs.viewport", SKIP, "not measured yet: the split rests on view_mode alone")

    # --- calls ------------------------------------------------------------------
    seen_calls: dict[int, str] = {}
    for spec in table.calls:
        if not spec.ready:
            report.add(f"calls.{spec.name}", SKIP, "needs both an address and an argument")
            continue
        addr = int(spec.addr)
        if not _plausible_address(addr) or addr % 4:
            report.add(
                f"calls.{spec.name}",
                WARN,
                f"{addr:#010x} is not a word-aligned EE address, so it is not a function",
            )
            continue
        first = _read32(reader, addr)
        if first == 0:
            report.add(
                f"calls.{spec.name}",
                WARN,
                f"{addr:#010x} is all zeros; that is empty memory, not a function. "
                "A call would jump into nothing",
            )
            continue
        if addr in seen_calls:
            report.add(
                f"calls.{spec.name}",
                WARN,
                f"the same function as calls.{seen_calls[addr]}, with the same argument: calling "
                "it twice per refill is almost certainly a copy-paste, and if it constructs "
                "anything, it constructs it twice",
            )
        else:
            seen_calls[addr] = spec.name
            report.add(
                f"calls.{spec.name}",
                OK,
                f"{addr:#010x} holds {first:#010x} (arg {spec.arg:#x}, "
                f"{'once' if spec.once else 'every refill'})",
            )
    return report
