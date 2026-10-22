"""The rectangles the two views are drawn into: the split's second lever.

The mod's primary way to get two viewports is a value: `view_mode`, whatever the
renderer reads to decide how many views to draw and how to cut the frame. That is
one word, and if the story renderer ignores it, co-op has nothing.

This module is the other way. Every renderer that draws a viewport needs to know
*where* to draw it -- a rectangle, usually four words: `x`, `y`, `w`, `h`. Ape
Escape 3 has one of these already, and it moves: the MonkeyCam, the
picture-in-picture in the bottom-right of an ordinary stage, is a second view
drawn into a rectangle that **L2 enlarges and L2 restores**. So the story renderer
is known to take a rectangle at runtime; whether it will accept *two* of them with
the frame cut in half is the question, and it is a question a measurement can
answer rather than a guess.

So: if `structs.viewport` is measured, the mod writes the rectangles itself --
left/right halves for a vertical split, top/bottom for a horizontal one -- plus
the count, plus which actor each view follows. If it is not measured, nothing
happens and the value-based route stands alone. The rectangles tile the frame
exactly, odd sizes included, because a one-pixel seam down the middle of the
screen is the most visible possible way to be wrong.

The coordinates are the engine's, not pixels counted off a screenshot, which is
why the screen size is a flag and why the measurement recipe starts from the
MonkeyCam's own rectangle rather than from arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .hud import Screen


@dataclass
class ViewPlan:
    """The rectangles to write this frame, and what could not be worked out."""

    entries: list[tuple[int, int]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    placed: bool = False

    def describe(self) -> str:
        if not self.entries:
            return "viewports: left to the game"
        bits = [f"{len(self.entries)} writes"]
        if self.placed:
            bits.append("rectangles placed")
        return "viewports: " + ", ".join(bits)


def _viewport_struct(table):
    struct = table.structs.get("viewport")
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


def _global_addr(table, name: str, resolved: dict | None) -> int | None:
    if resolved:
        value = resolved.get(name)
        if value is not None:
            return int(value)
    entry = table.globals.get(name)
    if entry is None or entry.addr is None:
        return None
    return int(entry.addr)


def requirements() -> list[str]:
    """What has to be measured before the rectangles can be driven."""
    return [
        "structs.viewport.base (or its signature)",
        "structs.viewport.stride",
        "structs.viewport.fields.x, .y, .w and .h",
        "globals.viewport_count (optional: how many views the renderer draws)",
        "structs.viewport.fields.target_actor (optional: which actor each view follows)",
    ]


def plan_views(
    table,
    split: str = "vertical",
    screen: Screen | None = None,
    targets: tuple[int, int] = (0, 1),
    resolved: dict | None = None,
) -> ViewPlan:
    """Rectangles for the two views, using only measured addresses.

    ``targets`` says which actor slot each view should follow (view 0 ->
    ``targets[0]``), which is how a split stops both halves looking at the same
    hero. Returns an empty plan -- with a note, not a warning -- when the viewport
    structure is unmeasured, because the value-based route is still in play and a
    missing backup is not a problem to report.
    """
    screen = screen or Screen()
    plan = ViewPlan()
    struct = _viewport_struct(table)
    if struct is None:
        plan.notes.append(
            "the viewport rectangles have not been measured, so the split rests entirely on "
            "view_mode (docs/DISCOVERY.md#viewports)"
        )
        return plan

    base = struct.base.addr
    if base is None:
        # Signature-only bases are resolved when a session starts; until then there
        # is no rectangle to write, and inventing one is how a screen ends up black.
        plan.notes.append("structs.viewport.base is a signature that has not been resolved yet")
        return plan

    def emit(addr: int, value: int, label: str) -> None:
        plan.entries.append((addr, value))
        plan.labels.append(label)

    # Everything is checked before anything is written, and all of it at once. A
    # half-written rectangle is the worst outcome available here: x and y moved
    # with w and h left alone draws the old view in a new place, which looks like
    # a broken renderer rather than a missing measurement.
    if not struct.stride:
        plan.notes.append(
            "structs.viewport.stride is 0, so both views resolve to the same rectangle -- and "
            "telling the renderer to draw two views from one rectangle draws the same half of "
            "the frame twice. Measure the stride first (docs/DISCOVERY.md#viewports)"
        )
        return plan

    missing = [name for name in ("x", "y", "w", "h") if _field_offset(struct, name) is None]
    if missing:
        plan.notes.append(
            "structs.viewport.fields." + "/".join(missing) + " unmeasured: the engine keeps "
            "placing this view (docs/DISCOVERY.md#viewports)"
        )
        return plan

    # The count first: a renderer that has been told to draw one view may never
    # look at the second rectangle, in which case writing it looks like a no-op.
    addr = _global_addr(table, "viewport_count", resolved)
    if addr is not None:
        emit(addr, 2, "viewport_count=2")
    else:
        plan.notes.append("globals.viewport_count is unmeasured; the second rectangle may be ignored")

    rects = screen.views(split)
    for slot, rect in enumerate(rects):
        for name, value in zip(("x", "y", "w", "h"), (rect.x, rect.y, rect.w, rect.h)):
            emit(struct.address_of(base, slot, name), int(value), f"view{slot + 1}.{name}={int(value)}")
        plan.placed = True
        if _field_offset(struct, "target_actor") is not None:
            emit(
                struct.address_of(base, slot, "target_actor"),
                int(targets[slot]),
                f"view{slot + 1}.target=hero{int(targets[slot]) + 1}",
            )

    plan.notes.append(
        f"{split} split: " + " | ".join(f"{rect.x},{rect.y} {rect.w}x{rect.h}" for rect in rects)
    )
    return plan
