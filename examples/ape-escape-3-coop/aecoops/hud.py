"""Where the HUD goes once there are two viewports.

Story mode draws one HUD, once, in screen space: the monkey counter, the jackets
and coins, the gadget icons. Split the screen and that single HUD is now sitting
on top of one player's half while the other player has nothing to read. That is
not a rendering problem, it is a *placement* problem, and the game already
contains the answer: the two-player minigames draw one HUD per player, each
anchored to its own side, each reading that player's own state.

So this module is deliberately small and deliberately honest. Three modes, in
increasing order of how much has to be measured:

``shared``
    Leave the game's HUD exactly where it is. One HUD for one frame, drawn once.
    Zero writes, zero risk, and the default: with a shared monkey quota both
    players are reading the same number anyway.

``per-view``
    Two HUD instances, one per viewport, each owned by one player. This is the
    shape the minigames use, and it is what we imitate: tell the engine there are
    two instances, mark both visible, give each one its own viewport's anchor,
    and (where the engine has such a field) point each at a different pad so it
    shows that player's state.

``hidden``
    Switch the shared HUD off for the split, for players who would rather read
    nothing than read the wrong half's layout. The pause menu still shows
    everything the HUD did.

**Nothing in here guesses.** Every single write is gated on an address the user
has actually measured on their own disc, and a mode whose addresses are missing
degrades to the next-lower mode with a warning that says which field is missing
and where the recipe is. That is the difference between "the mod did nothing"
and "the mod wrote a plausible number into a word that turned out to be the
gadget wheel's rotation".

The one piece of arithmetic here is the split geometry: two rectangles that tile
the frame, and an anchor inside each. The numbers are in the game's own HUD
space, which is why the screen size is a flag rather than a constant -- measure
it once by comparing where the game puts its own HUD, and the same numbers work
for both halves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# What the story stage's renderer is assumed to be drawing into. PS2 NTSC frames
# are 640x448; PAL is 640x512. These are only ever used to *place* a HUD inside a
# viewport, never to poke the renderer, so a wrong guess puts the HUD at a
# slightly wrong pixel rather than breaking anything.
NTSC_SCREEN = (640, 448)
PAL_SCREEN = (640, 512)
DEFAULT_MARGIN = 8


class HudMode(Enum):
    """What the split does to the HUD."""

    SHARED = "shared"
    PER_VIEW = "per-view"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def __iter__(self):
        return iter((self.x, self.y, self.w, self.h))

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.x},{self.y} {self.w}x{self.h}"


@dataclass(frozen=True)
class Screen:
    """The game's HUD coordinate space, and how far in from a edge a HUD sits."""

    width: int = NTSC_SCREEN[0]
    height: int = NTSC_SCREEN[1]
    margin: int = DEFAULT_MARGIN

    @classmethod
    def parse(cls, text: str | None) -> "Screen":
        """``640x448``, ``640x448:12`` or ``pal`` -- whatever is quickest to type."""
        if not text:
            return cls()
        lowered = text.strip().lower()
        if lowered in ("pal", "50"):
            return cls(*PAL_SCREEN)
        if lowered in ("ntsc", "60", "default"):
            return cls(*NTSC_SCREEN)
        size, _, margin = lowered.partition(":")
        width, _, height = size.partition("x")
        try:
            screen = cls(int(width, 0), int(height, 0), int(margin, 0) if margin else DEFAULT_MARGIN)
        except ValueError as exc:
            raise ValueError(f"cannot read screen {text!r}: expected WIDTHxHEIGHT[:MARGIN]") from exc
        if screen.width <= 1 or screen.height <= 1:
            raise ValueError(f"screen {screen.width}x{screen.height} is too small to split")
        return screen

    def views(self, split: str) -> tuple[Rect, Rect]:
        """The two viewport rectangles for a split, in HUD coordinates.

        The halves tile the frame exactly (no seam), because the renderer's own
        idea of where the cut is may differ from ours by a pixel or two and a
        seam draws as a coloured line down the middle of the screen.
        """
        width, height = self.width, self.height
        half_w, half_h = width // 2, height // 2
        key = str(split).lower()
        if key == "vertical":  # left | right
            return Rect(0, 0, half_w, height), Rect(half_w, 0, width - half_w, height)
        if key == "horizontal":  # top / bottom
            return Rect(0, 0, width, half_h), Rect(0, half_h, width, height - half_h)
        return Rect(0, 0, width, height), Rect(0, 0, width, height)

    def anchors(self, split: str) -> tuple[tuple[int, int], tuple[int, int]]:
        """Where a per-view HUD's top-left corner goes, one per view."""
        out = []
        for view in self.views(split):
            out.append((view.x + self.margin, view.y + self.margin))
        return out[0], out[1]


def requirements(mode: HudMode) -> list[str]:
    """The worksheet entries a mode needs before it will write anything."""
    if mode is HudMode.SHARED:
        return []
    if mode is HudMode.PER_VIEW:
        return [
            "structs.hud.base (or its signature)",
            "structs.hud.stride",
            "globals.hud_instances (optional: how many instances the renderer builds)",
            "structs.hud.fields.visible (optional)",
            "structs.hud.fields.x and .y (optional: per-view anchors)",
            "structs.hud.fields.owner (optional: which pad's state this HUD shows)",
        ]
    return [
        "globals.hud_instances (preferred: set it to 0)",
        "or structs.hud.fields.visible per slot",
    ]


@dataclass
class HudPlan:
    """What the HUD policy wants written this frame, and what it could not do."""

    mode: HudMode = HudMode.SHARED
    entries: list[tuple[int, int]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    placed: bool = False  # True once a per-view anchor was actually written

    def describe(self) -> str:
        bits = [f"{self.mode.value}: {len(self.entries)} writes"]
        if self.placed:
            bits.append("per-view anchors")
        if self.warnings:
            bits.append(f"{len(self.warnings)} unresolved")
        return ", ".join(bits)


def _global_addr(table, name: str, resolved: dict | None) -> int | None:
    """A global's live address, preferring the ones already resolved this session."""
    if resolved:
        value = resolved.get(name)
        if value is not None:
            return int(value)
    entry = table.globals.get(name)
    if entry is None or entry.addr is None:
        return None
    return int(entry.addr)


def _hud_struct(table):
    struct = table.structs.get("hud")
    if struct is None or (struct.base.addr is None and not struct.base.signature):
        return None
    return struct


def _field_offset(struct, name: str) -> int | None:
    if struct is None:
        return None
    fld = struct.fields.get(name)
    if fld is None or fld.offset is None:
        return None
    return int(fld.offset)


def _slot_addr(struct, slot: int, field_name: str) -> int | None:
    """The address of one field of one slot, or None if it is not knowable yet."""
    offset = _field_offset(struct, field_name)
    if offset is None:
        return None
    if struct.base.addr is None:
        # Signature-only bases are resolved when a session starts; until then there
        # is no address to write, and inventing one is not an option.
        return None
    if slot and not struct.stride:
        return None  # both slots would land on the same word
    return int(struct.base.addr) + slot * int(struct.stride) + offset


def measured(table, mode: HudMode) -> dict[str, bool]:
    """Which of a mode's optional pieces this disc actually has measured."""
    struct = _hud_struct(table)
    entry = table.globals.get("hud_instances")
    out = {"hud_instances": entry is not None and (entry.addr is not None or entry.signature)}
    if mode is HudMode.PER_VIEW:
        out["visible"] = _field_offset(struct, "visible") is not None
        out["anchors"] = _slot_addr(struct, 1, "x") is not None and _slot_addr(struct, 1, "y") is not None
        out["owner"] = _slot_addr(struct, 1, "owner") is not None
    else:
        out["visible"] = _field_offset(struct, "visible") is not None
    return out


def describe_for(table, mode: HudMode) -> str:
    """One line for `status`/`install`: what this mode will actually do here."""
    if mode is HudMode.SHARED:
        return "shared (the game's own HUD, no writes)"
    struct = _hud_struct(table)
    if struct is None:
        return f"{mode.value}: unwritten -- the hud structure is not measured (docs/DISCOVERY.md#hud)"
    have = measured(table, mode)
    if mode is HudMode.PER_VIEW:
        bits = ["2 instances" if have.get("hud_instances") else "instance count unknown"]
        bits.append("anchors set" if have.get("anchors") else "anchors left to the game")
        if have.get("owner"):
            bits.append("owner per player")
        return "per-view: " + ", ".join(bits)
    return "hidden: " + ("instance count 0" if have.get("hud_instances") else "per-slot visible 0")


def plan_hud(
    table,
    mode: HudMode = HudMode.SHARED,
    split: str = "vertical",
    screen: Screen | None = None,
    pads: tuple[int, int] = (0, 1),
    resolved: dict | None = None,
) -> HudPlan:
    """Turn a HUD mode into cave entries, using only what has been measured.

    ``table`` is an :class:`aecoops.addresses.AddressTable`; ``resolved`` is the
    session's name -> live address map, so a signature-based entry is used at the
    address it actually settled on. The returned entries are plain
    ``(address, value)`` pairs ready for the cave, and ``labels`` line up with
    them one for one.
    """
    screen = screen or Screen()
    plan = HudPlan(mode=mode)

    def emit(addr: int, value: int, label: str) -> None:
        """One write and its label, always together.

        They are two lists, so the only way they stay aligned is if no code path
        can append to one without the other. (A previous version appended two
        anchors with a single label, which silently shifted every label after it
        -- the writes were right, the reporting was a lie.)
        """
        plan.entries.append((addr, value))
        plan.labels.append(label)

    if mode is HudMode.SHARED:
        plan.notes.append("one HUD, drawn once by the game, where the game puts it")
        return plan

    struct = _hud_struct(table)
    if struct is None:
        plan.warnings.append(
            "the HUD structure has not been measured, so the "
            f"{mode.value} HUD mode cannot be applied; the game keeps its own HUD "
            "(docs/DISCOVERY.md#hud)"
        )
        return plan

    if mode is HudMode.PER_VIEW:
        # 1. how many HUD instances the renderer builds. This is the parallel of
        #    actor_count, and it is the write that actually creates the second HUD.
        addr = _global_addr(table, "hud_instances", resolved)
        if addr is not None:
            emit(addr, 2, "hud_instances=2")
        else:
            plan.warnings.append(
                "globals.hud_instances is not measured: the second HUD instance may never be "
                "built, so player two sees nothing even if the anchors are set "
                "(docs/DISCOVERY.md#hud)"
            )

        anchors = screen.anchors(split)
        for slot in range(2):
            # 2. both instances have to be alive: a slot the renderer skipped last
            #    frame is usually remembered as invisible.
            addr = _slot_addr(struct, slot, "visible")
            if addr is not None:
                emit(addr, 1, f"hud{slot + 1}=visible")

            # 3. each instance in its own view. This is the only piece that needs
            #    arithmetic, and the only piece that is skipped without a warning:
            #    many engines place a HUD from the viewport it belongs to, in which
            #    case the two writes above are already enough.
            if (
                _field_offset(struct, "x") is not None
                and _field_offset(struct, "y") is not None
                and (slot == 0 or struct.stride)
            ):
                x = _slot_addr(struct, slot, "x")
                y = _slot_addr(struct, slot, "y")
                if x is not None and y is not None:
                    emit(x, anchors[slot][0], f"hud{slot + 1}=x {anchors[slot][0]}")
                    emit(y, anchors[slot][1], f"hud{slot + 1}=y {anchors[slot][1]}")
                    plan.placed = True

            # 4. whose state this HUD shows. In the minigames each player's HUD
            #    reads that player's score and stamina; in a story stage the
            #    useful equivalent is the gadget/jacket state of the hero whose
            #    pad drives the view.
            addr = _slot_addr(struct, slot, "owner")
            if addr is not None:
                emit(addr, int(pads[slot]), f"hud{slot + 1}=pad {pads[slot]}")

        if not plan.placed:
            plan.notes.append(
                "both HUD instances are alive and owned, but their anchors were not measured, so "
                "the game places them; if both land in the same half, measure x/y"
            )
        if not plan.entries:
            plan.warnings.append(
                "per-view HUD was asked for but none of its addresses are measured; the split "
                "will show the game's single HUD (docs/DISCOVERY.md#hud)"
            )
        return plan

    # HIDDEN: prefer "build no instances at all" over "build one and hide it",
    # because the second is a per-frame fight and the first is a state.
    addr = _global_addr(table, "hud_instances", resolved)
    if addr is not None:
        emit(addr, 0, "hud_instances=0")
        return plan

    wrote = False
    for slot in range(2):
        addr = _slot_addr(struct, slot, "visible")
        if addr is None:
            continue
        emit(addr, 0, f"hud{slot + 1}=hidden")
        wrote = True
    if not wrote:
        plan.warnings.append(
            "no way to switch the HUD off has been measured: neither globals.hud_instances nor "
            "structs.hud.fields.visible is in the worksheet (docs/DISCOVERY.md#hud)"
        )
    return plan
