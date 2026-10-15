"""The co-op mod itself: two players, one stage, split screen.

What Ape Escape 3 does today
----------------------------
Story mode is one player, one of Kei or Yumi, chosen per save. Two-player exists
only in the minigame menu. So co-op needs three things the game will not give us:

1. **A second hero in the stage.** The stage's actor table has room for the
   partner: the engine allocates the same structure for the two heroes in the
   minigames, and the model, animations and net code all exist. We mark slot 1
   active, point it at player two's pad, and the engine does the rest -- it was
   already written to drive two of them.
2. **A second viewport.** The minigame renderer already knows how to draw two
   views of one stage; that is its whole job. We flip the view mode the story
   stage's renderer reads, so the same stage gets drawn twice, once per hero.
3. **Per-player character choice.** Each actor carries a character id. Writing
   Kei's id into slot 0 and Yumi's into slot 1 gives the two heroes as a pair, so
   either player can be either character -- and swapping is two writes.

None of that survives contact with the game on its own, because the engine
rewrites its own frame state every frame: the view mode is recomputed by the
stage camera code, the actor count by the spawner, the character id by the
save-slot loader. So the mod does not fight for it. It installs the interpreter
cave (see ``cave.py``) and refills its parameter block from Python a few times a
second; the game re-applies the values sixty times a second, forever. Policy in
Python, effect in-game.

What we do **not** do
---------------------
Nothing here goes near the online side of anything, and Ape Escape 3 has no
online side. No DRM or ownership checks are touched; the mod reads and writes
memory inside an emulator the user runs on an ISO they own. Nothing from the game
is copied into this repository -- addresses and byte patterns are facts about
someone's own disc, and they never ship.
"""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from . import cave, hud, views
from .hud import HudMode, Screen
from .addresses import AddressTable, TableError
from .patcher import PatchSet
from .pine import EmuInfo, Pine, PineError, PineUnusable
from .scan import EE_RAM, EE_SCRATCHPAD

KEI = "kei"
YUMI = "yumi"


class Slot(Enum):
    P1 = 0
    P2 = 1


class Split(Enum):
    """View layouts, named after what the players see."""

    SINGLE = "single"
    VERTICAL = "vertical"  # left | right
    HORIZONTAL = "horizontal"  # top / bottom


@dataclass
class CoopConfig:
    """Everything a player can choose before the run starts."""

    p1: str = KEI
    p2: str = YUMI
    split: Split = Split.VERTICAL
    # What the split does to the HUD. `shared` writes nothing at all, which is why
    # it is the default: two players reading one monkey counter is a real answer,
    # and the two other modes need measurements the worksheet probably does not
    # have yet. See hud.py.
    hud_mode: HudMode = HudMode.SHARED
    # The game's own HUD coordinate space, used only to place a per-view HUD. Both
    # defaults are PS2 NTSC; `--screen pal` is 640x512.
    screen: Screen = field(default_factory=Screen)
    # Give each viewport's camera its own hero. Only ever written when the camera
    # structure has been measured.
    cameras_follow_views: bool = True
    # Drive the viewport rectangles directly when they have been measured: the
    # fallback for a renderer that ignores view_mode. See views.py.
    place_viewports: bool = True
    # The stage quota is shared by default: two heroes, one monkey count, so the
    # stage still ends where it always did. Set False for "either player can
    # clear the stage alone".
    share_quota: bool = True
    # Until the second hero is independent, mirror the first one's position so
    # player two's inputs still move something instead of doing nothing.
    tether_until_independent: bool = True
    # Keep the two heroes near each other (0 = off). Two viewports of one stage
    # would otherwise let them wander into different load zones.
    tether_radius: float = 0.0
    # Which hero the camera prefers when the split is asymmetric.
    camera_follows: str = KEI
    # Two of the same hero is a debug shape, not co-op: the stage keys the second
    # actor's model and morphs off the character id. It is allowed only when the
    # caller says so out loud (CoopConfig.mirror()).
    allow_twins: bool = False

    def validate(self) -> None:
        for who in (self.p1, self.p2):
            if who not in (KEI, YUMI):
                raise ValueError(f"unknown character {who!r}: pick {KEI!r} or {YUMI!r}")
        if self.p1 == self.p2 and not self.allow_twins:
            raise ValueError(
                "both players picked the same hero. Ape Escape 3's stage code keys the second "
                "actor's model and morphs off the character id, so two of the same is unsupported "
                "by this mod -- use p1/p2 of kei and yumi, or 'mirror' if you really want twins."
            )
        if self.tether_radius < 0:
            raise ValueError("tether_radius cannot be negative")

    @classmethod
    def mirror(cls, who: str = KEI) -> "CoopConfig":
        """Both screens, one hero: a debug shape, not a co-op mode."""
        return cls(p1=who, p2=who, allow_twins=True)


@dataclass
class TickReport:
    ok: bool = True
    tick: int = 0
    applied: int = 0
    wrote: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    info: EmuInfo | None = None

    def summary(self) -> str:
        state = "ok" if self.ok else "PROBLEM"
        # `applied` counts writes *and* calls: a tick that only made calls must not
        # read as a tick that did nothing.
        bits = [f"tick {self.tick}", f"{self.applied} writes", state]
        if self.wrote:
            bits.append(", ".join(self.wrote))
        text = " · ".join(bits)
        for note in self.notes:
            text += f"\n  + {note}"
        if self.warnings:
            text += "\n  ! " + "\n  ! ".join(self.warnings)
        return text


@dataclass
class InstallReport:
    ok: bool = False
    cave: int | None = None
    site: int | None = None
    callee: int | None = None
    patchset: str = ""
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = []
        if self.ok:
            lines.append(f"cave installed at {self.cave:#010x}, intercepting the call at {self.site:#010x}")
            lines.append(f"  the game's function being wrapped: {self.callee:#010x}")
            if self.patchset:
                lines.append(f"  patch set: {self.patchset}")
        else:
            lines.append("install failed:")
        lines += [f"  ! {e}" for e in self.errors]
        lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


class CoopError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    """One write the cave should make every frame.

    ``essential`` is the mod's own priority: the writes that *are* co-op (the
    split, the second actor, who is who) versus the ones that make it nicer (the
    tether, the HUD, the per-view cameras). If a fully measured configuration ever
    exceeds the cave's entry budget, the optional ones are dropped first and the
    user is told which, instead of the whole install failing.
    """

    addr: int
    value: int
    label: str
    essential: bool = True


@dataclass
class Plan:
    """The refill's intended writes and calls, in the order they are applied."""

    entries: list[Entry] = field(default_factory=list)
    calls: list[tuple[int, int]] = field(default_factory=list)
    call_labels: list[str] = field(default_factory=list)
    #: Labels of the calls that are one-shot. The writer arms these *after* the
    #: block carrying them is safely in the game: a call marked spent before it was
    #: ever handed over is a partner who never spawns.
    calls_once: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def values(self) -> list[tuple[int, int]]:
        return [(e.addr, e.value) for e in self.entries]

    def describe_calls(self) -> str:
        if not self.calls:
            return "no calls"
        return ", ".join(self.call_labels)

    def labels(self) -> list[str]:
        return [e.label for e in self.entries]

    def trimmed_to_budget(
        self, budget: int = cave.MAX_ENTRIES, call_budget: int = cave.MAX_CALLS
    ) -> "Plan":
        """Keep every essential entry, then optional ones in order, up to ``budget``."""
        if len(self.calls) > call_budget:
            kept = self.calls[:call_budget]
            dropped = self.call_labels[call_budget:]
            self.calls, self.call_labels = kept, self.call_labels[:call_budget]
            self.calls_once = [n for n in self.calls_once if n in self.call_labels]
            self.warnings.append(
                f"{len(dropped)} calls did not fit the cave's {call_budget}-call budget: "
                + ", ".join(dropped)
            )
        if len(self.entries) <= budget:
            return self
        kept = [e for e in self.entries if e.essential]
        dropped = [e for e in self.entries if not e.essential]
        room = max(0, budget - len(kept))
        if len(kept) > budget:  # cannot happen with the current table, but be honest
            dropped, kept = kept[budget:] + dropped, kept[:budget]
        else:
            dropped = dropped[room:]
        out = Plan(
            entries=kept + [e for e in self.entries if not e.essential][:room],
            calls=list(self.calls),
            call_labels=list(self.call_labels),
            calls_once=list(self.calls_once),
            warnings=list(self.warnings),
        )
        out.warnings.append(
            f"{len(dropped)} optional writes did not fit the cave's {budget}-entry budget and "
            "were left out this run: " + ", ".join(e.label for e in dropped[:6])
        )
        return out


class CoopMod:
    """Installs and runs co-op on a live PCSX2 session."""

    def __init__(
        self,
        pine: Pine,
        table: AddressTable | None = None,
        config: CoopConfig | None = None,
        patchset: PatchSet | None = None,
    ) -> None:
        self.pine = pine
        self.table = table or AddressTable.load()
        self.config = config or CoopConfig()
        self.config.validate()
        self.patchset = patchset
        self.install_info = InstallReport()

        self._resolved: dict[str, int] = {}
        self._cave = 0
        self._interpreter: cave.Interpreter | None = None
        self._original_site = b""
        self._last_tick = 0
        self._tick_stall = 0
        self._independent_at: float | None = None
        self._hud_plan = hud.HudPlan(mode=self.config.hud_mode)
        self._view_plan = views.ViewPlan()
        # One-shot calls. `_calls_pending` maps a name to the pair the cave was
        # given and has not been seen to consume; `_calls_done` is what the cave has
        # been *observed* to consume, which is the only thing that stops the host
        # re-sending it. The evidence is the block itself: see `_settle_calls`.
        self._calls_pending: dict[str, tuple[int, int]] = {}
        self._calls_done: set[str] = set()
        self._notes_seen: dict[str, str] = {}
        self._notes_reported: set[str] = set()
        # Where signatures are looked for. None means "everywhere the worksheet's
        # defaults cover"; a stage-only signature is much faster to find inside one.
        self._scan_ranges: list[tuple[int, int]] | None = None

    # ------------------------------------------------------------------ install

    def resolve(self, ranges: list[tuple[int, int]] | None = None) -> dict[str, int]:
        """Resolve every global against the running game. Reads only, writes nothing.

        Split out of :meth:`install` so that ``export-pnach`` can freeze exactly
        what a live run would write without a cave ever touching the game.

        Structure bases are resolved here too, for the same reason the table allows
        a signature instead of an address: a pattern survives a different pressing
        of the disc, and a base that is only known by signature would otherwise be
        invisible to everything that pokes a field through it.
        """
        if ranges is not None:
            self._scan_ranges = list(ranges)
        self._resolved = self.table.resolve_all(self.pine, self._scan_ranges)
        # A fresh resolve is a fresh look at the worksheet, so notes it produces are
        # new information again (that is what a reinstall after editing the JSON is).
        self._notes_reported.clear()
        for name, struct in self.table.structs.items():
            if struct.base.addr is not None or not struct.base.signature:
                continue
            try:
                struct.base.addr = struct.base.resolve(self.pine, self._scan_ranges or None)
            except LookupError as exc:
                self._note_once(f"struct:{name}", f"{name}.base could not be found: {exc}")
        return self._resolved

    def install(
        self,
        cave_addr: int | None = None,
        site: int | None = None,
        journal: Path | None = None,
        ranges: list[tuple[int, int]] | None = None,
    ) -> InstallReport:
        """Resolve addresses, pick free RAM, and drop the interpreter in.

        Idempotent: running it twice does not install a second cave.
        """
        report = self.install_info = InstallReport()
        try:
            info = self.pine.require_running()
            if self.patchset is not None:
                ok, why = self.patchset.accepts(info.serial, info.uuid, info.disc_version)
                if not ok:
                    report.errors.append(f"wrong disc for this patch set: {why}")
                    return report
                applied = self.patchset.apply(self.pine, journal)
                if not applied.ok:
                    report.errors.extend(applied.errors)
                    return report
                report.patchset = f"{applied.patchset} ({len(applied.applied)} new)"

            self.resolve(ranges)
        except (TableError, PineError, PineUnusable) as exc:
            report.errors.append(str(exc))
            return report


        site = site if site is not None else self._resolved.get("frame_hook")
        if site is None:
            report.errors.append(
                "no frame hook. Either set `frame_hook` in data/addresses.json, or pass --site. "
                "`aecoops discover sweep` finds candidates and proves which ones run per frame."
            )
            return report

        word = struct.unpack("<I", self.pine.read_bytes(site, 4))[0]
        if (word >> 26) != 0x03:
            report.errors.append(
                f"{site:#010x} does not hold a jal (it is {word:#010x}), so it cannot be "
                "intercepted. Pick the jal that calls the per-frame update function."
            )
            return report

        cave_addr = cave_addr if cave_addr is not None else self._find_cave()
        self._cave = cave_addr
        self._original_site = self.pine.read_bytes(site, 4)
        self._interpreter = cave.build_interpreter(cave_addr, site, cave.callee_of_jal(word, site))

        if not self._is_installed():
            # A fresh cave is a fresh session: whatever the previous one was told to
            # call, this one has not called.
            self._calls_pending.clear()
            self._calls_done.clear()

        # Build the plan before anything is written: it decides both the entries to
        # arm and the warnings to report, and it reads memory rather than writing.
        plan = self.plan()
        if plan.warnings:
            report.notes += [f"! {warning}" for warning in plan.warnings]

        if self._is_installed():
            report.ok = True
            report.cave, report.site, report.callee = cave_addr, site, self._interpreter.callee
            report.notes.append("cave was already installed; left as it is")
            spent = self._adopt_installed_calls()
            if spent:
                report.notes.append(
                    f"a cave was already here, so {len(spent)} one-shot call(s) are left alone "
                    "rather than re-armed: " + ", ".join(sorted(spent)) + ". If the stage was "
                    "reloaded and they need to happen again, eject and install again."
                )
            report.notes.append(f"hud: {self._hud_plan.describe()}")
            report.notes.append(self._view_plan.describe())
            report.notes.extend(f"  {n}" for n in self._view_plan.notes)
            report.notes.extend(f"! {w}" for w in self._view_plan.warnings)
            return report

        # Order matters: the cave and its block exist before anything jumps to it.
        # The block is armed with this frame's entries rather than left empty, so
        # co-op is already on during the first frame after the install instead of
        # during the first frame after the first watchdog pass.
        self.pine.write_bytes(cave_addr, self._interpreter.code)
        self.pine.write_bytes(
            cave.param_block_at(cave_addr),
            cave.encode_block(plan.values(), calls=plan.calls),
        )
        self.pine.write_bytes(site, self._interpreter.trampoline)
        self._arm_calls(plan)

        report.ok = True
        report.cave = cave_addr
        report.site = site
        report.callee = self._interpreter.callee
        report.notes.append(f"{len(plan.entries)} writes armed")
        if plan.calls:
            report.notes.append(
                f"{len(plan.calls)} call(s) queued: {plan.describe_calls()} "
                "(they fire on the next frame the hook runs)"
            )
        report.notes.append(f"hud: {self._hud_plan.describe()}")
        report.notes.append(self._view_plan.describe())
        report.notes.extend(f"  {n}" for n in self._view_plan.notes)
        for warning in self._hud_plan.warnings:
            report.notes.append(f"! {warning}")
        for warning in self._view_plan.warnings:
            report.notes.append(f"! {warning}")
        return report

    def _find_cave(self) -> int:
        """Free RAM for the cave, preferring space the game has already proven it ignores."""
        ranges = [EE_RAM, EE_SCRATCHPAD]
        size = cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE
        addr = cave.find_free_region(self.pine, size, ranges)
        if not cave.verify_stable(self.pine, addr, size):
            raise CoopError(
                f"{addr:#010x} looked free but is being written to. Try `aecoops find-free --range ...` "
                "to pick a spot by hand."
            )
        return addr

    def attach(self, cave_addr: int | None) -> None:
        """Adopt an already-installed cave (same shape as `install`, no writes)."""
        if not cave_addr:
            return
        try:
            word = struct.unpack("<I", self.pine.read_bytes(self._hook_site_from_table(), 4))[0]
        except (PineError, KeyError):
            return
        site = self._resolved.get("frame_hook")
        if site and (word >> 26) == 0x03:
            self._cave = int(cave_addr)
            self._interpreter = cave.build_interpreter(
                int(cave_addr), site, cave.callee_of_jal(word, site)
            )

    def _hook_site_from_table(self) -> int:
        entry = self.table.globals.get("frame_hook")
        if entry is None or entry.addr is None:
            raise KeyError("no frame_hook")
        return int(entry.addr)

    def _is_installed(self) -> bool:
        from .discover import probe_is_installed

        return bool(self._cave and probe_is_installed(self.pine, self._install_site, self._cave))

    @property
    def _install_site(self) -> int:
        return self._interpreter.site if self._interpreter else 0

    # -------------------------------------------------------------- per-frame

    def plan(self) -> Plan:
        """Everything the game should re-apply this frame, with its priorities.

        The order is deliberate: the shape of the game first (split, two actors,
        who is who), then the conveniences (tether, HUD, cameras), so that a
        truncated plan still plays a recognisable co-op session.
        """
        table, cfg = self.table, self.config
        out = Plan()

        # Anything `resolve()` noticed out of band (a struct base that could not be
        # found) belongs to this report.
        out.warnings.extend(self._drain_notes())

        globals_ = self._resolved

        def set_global(name: str, value: int, label: str, essential: bool = True) -> None:
            addr = globals_.get(name)
            if addr is None:
                return
            out.entries.append(Entry(cave.const(addr, value)[0], value, label, essential))

        # 1. two viewports
        if cfg.split is not Split.SINGLE:
            try:
                set_global("view_mode", table.layout_id(cfg.split.value), f"split={cfg.split.value}")
            except TableError as exc:
                # An unmeasured worksheet is the normal state on a first run, and
                # `status` has to be readable in exactly that state.
                out.warnings.append(f"the split cannot be applied yet: {exc}")
        # 2. two live heroes
        set_global("actor_count", 2, "actor_count=2")
        set_global("partner_enabled", 1, "partner=on")

        # 3. per-player character ids, and their pads
        actor = table.structs.get("actor")
        if actor is not None and actor.base.addr is not None:
            base = actor.base.addr
            try:
                for slot, who in ((Slot.P1, cfg.p1), (Slot.P2, cfg.p2)):
                    out.entries.append(
                        Entry(
                            actor.address_of(base, slot.value, "char_id"),
                            table.character_id(who),
                            f"p{slot.value + 1}={who}",
                        )
                    )
                    out.entries.append(
                        Entry(
                            actor.address_of(base, slot.value, "pad_port"),
                            slot.value,
                            f"p{slot.value + 1}=pad {slot.value}",
                        )
                    )
                    out.entries.append(
                        Entry(actor.address_of(base, slot.value, "active"), 1, f"hero{slot.value + 1}=on")
                    )
            except TableError as exc:
                out.warnings.append(f"per-player character ids skipped: {exc}")

        # 4. hero two: mirrored onto player one until its own logic drives it, and
        #    then (optionally) kept within a radius so two viewports cannot wander
        #    into different load zones.
        if cfg.tether_until_independent and not self.independent:
            out.entries.extend(self._tether_entries())
        else:
            out.entries.extend(self._radius_entries())

        if cfg.share_quota:
            set_global("quota_shared", 1, "quota shared")

        # 5. the rectangles themselves, if they have been measured: the split can
        #    come from a value or from four numbers per view, and the second one
        #    does not depend on the renderer agreeing to a mode.
        if cfg.place_viewports and cfg.split is not Split.SINGLE:
            view_plan = views.plan_views(
                table,
                cfg.split.value,
                cfg.screen,
                targets=self._view_targets(),
                resolved=self._resolved,
            )
            self._view_plan = view_plan
            out.entries.extend(
                Entry(addr, value, label, essential=False)
                for (addr, value), label in zip(view_plan.entries, view_plan.labels)
            )
            out.warnings.extend(view_plan.warnings)

        # 6. who each half of the screen is looking at
        if cfg.cameras_follow_views and cfg.split is not Split.SINGLE:
            out.entries.extend(self._camera_entries())

        # 7. the HUD, for a screen that is now two screens
        hud_plan = hud.plan_hud(table, cfg.hud_mode, cfg.split.value, cfg.screen, resolved=self._resolved)
        out.entries.extend(
            Entry(addr, value, label, essential=False)
            for (addr, value), label in zip(hud_plan.entries, hud_plan.labels)
        )
        out.warnings.extend(hud_plan.warnings)
        self._hud_plan = hud_plan

        # 8. the game's own functions, for what a write cannot do
        out.calls, out.call_labels, out.calls_once = self._declared_calls()

        # ...and anything this pass noticed, in this pass's report: a note is a fact
        # about the worksheet, and the worksheet does not change while the mod runs,
        # so saying it once is the whole point -- see `_note_once`.
        out.warnings.extend(self._drain_notes())

        return out.trimmed_to_budget()

    def _drain_notes(self) -> list[str]:
        """The notes that have not been said yet, oldest first."""
        fresh = [text for key, text in self._notes_seen.items() if key not in self._notes_reported]
        self._notes_reported.update(self._notes_seen)
        self._notes_seen.clear()
        return fresh

    def entries(self) -> list[tuple[int, int]]:
        """The (address, value) pairs the game should re-apply this frame.

        Everything here is idempotent state, never one-shot input: the engine
        resets it, we set it again, and the result is stable rather than
        oscillating.
        """
        return self.plan().values()

    def _declared_calls(self) -> tuple[list[tuple[int, int]], list[str], list[str]]:
        """Calls from the worksheet: the ones the user measured, and nothing else.

        An unmeasured call is not a warning: the mod runs without it, and `doctor`
        lists it alongside every other blank. A call that *is* measured but whose
        argument is missing is refused loudly, because guessing an argument to a
        function that constructs things is how a stage crashes.
        """
        wanted = [(spec, self._call_pair(spec)) for spec in self.table.calls]
        # A call declared both ways with the same function and argument cannot be
        # told apart in the cave's own consumption, so the one-shot half would be
        # re-sent every refill for ever -- the same function, twice a frame. Refuse
        # it instead: one of the two declarations is a mistake.
        repeated = {pair: spec.name for spec, pair in wanted if pair and not spec.once}
        calls: list[tuple[int, int]] = []
        labels: list[str] = []
        once: list[str] = []
        for spec, pair in wanted:
            if pair is None:
                continue
            if not spec.once:
                calls.append(pair)
                labels.append(spec.name)
                continue
            if spec.name in self._calls_done:
                continue
            if pair in repeated:
                self._note_once(
                    f"call:{spec.name}",
                    f"calls.{spec.name} is the same function and argument as the repeating call "
                    f"{repeated[pair]!r}, so whether the cave has run it cannot be told apart and it "
                    "would be sent every refill; drop one of the two, or set once=false "
                    "(docs/DISCOVERY.md#calls)",
                )
                continue
            calls.append(pair)
            labels.append(spec.name)
            once.append(spec.name)
        return calls, labels, once

    def _call_pair(self, spec) -> tuple[int, int] | None:
        """One worksheet call as the (function, argument) pair the cave takes.

        ``None`` means there is nothing to call -- either the call is a blank in the
        worksheet, which is normal, or it is half measured, which is not and is said
        so here, once.
        """
        if spec.addr is None and spec.arg is None:
            return None
        if not spec.ready:
            self._note_once(
                f"call:{spec.name}",
                f"calls.{spec.name} has "
                + ("an address" if spec.addr is not None else "an argument")
                + " but not both, so it is not called (docs/DISCOVERY.md#calls)",
            )
            return None
        argument = int(spec.arg)
        if argument & 0x80000000:
            # An indirect argument, in the same spelling an entry value uses: bit 31
            # means "read the word at this address and pass what is there". A spawner
            # wants the handle the stage has just allocated, not the number it held
            # one watchdog pass ago.
            source = argument & 0x7FFFFFFF
            if not source:
                self._note_once(
                    f"call:{spec.name}",
                    f"calls.{spec.name}.arg is an indirect argument reading address 0, "
                    "which is not a handle; clear bit 31 or point it at the word "
                    "(docs/DISCOVERY.md#calls)",
                )
                return None
            return cave.call_read(int(spec.addr), source)
        return cave.call(int(spec.addr), argument)

    def _arm_calls(self, plan: Plan) -> None:
        """Remember what the refill just handed over: it is pending until seen run.

        Called by the writer, after the write returned -- not by `plan()`, which
        builds a plan for `status` and for the dry runs too, and a call marked spent
        by a plan that was never written is a call that never happens.
        """
        pairs = dict(zip(plan.call_labels, plan.calls))
        for name in plan.calls_once:
            self._calls_pending.setdefault(name, pairs[name])

    def _settle_calls(self, report: TickReport) -> None:
        """Note the one-shot calls the cave has made, from the block itself.

        A one-shot call cannot be written once and forgotten: the host refills the
        whole call region every pass, so a call the game has not reached yet would be
        wiped by the refill that follows -- and a call written twice is sixty
        partners instead of one. The way out is that the cave *consumes* a call when
        it makes it, so the region read back before the refill says exactly which
        calls are still waiting. Until a call stops appearing there it is re-sent; the
        moment it disappears, it has been made, and it is said so once.

        (The window where this is not airtight: a frame that lands between this read
        and the refill write could consume a call that the same refill then re-sends.
        That is a sub-millisecond window per call, only while a call is still
        pending, and the alternative -- trusting a write the game has not run yet --
        loses calls outright.)
        """
        if not self._calls_pending:
            return
        try:
            waiting = cave.read_calls(self.pine, self._cave)
        except PineError:
            return  # cannot see the region: keep re-sending rather than claim it ran
        for name, pair in sorted(self._calls_pending.items()):
            if pair not in waiting:
                report.notes.append(f"called {name}")
                self._calls_done.add(name)
        for name in self._calls_done:
            self._calls_pending.pop(name, None)

    def _adopt_installed_calls(self) -> set[str]:
        """Take an installed cave's word for what it has already called. Returns the names.

        This is the attach case: another host process installed the cave, or this one
        did and exited, and a one-shot call that is *not* in the call region now may
        have been made already -- there is no way to tell. Spawning a partner twice is
        worse than not spawning one, so a call this process never armed is treated as
        spent. A call the previous session left queued in the region is adopted as
        pending, because the refill would otherwise wipe it before the game reaches it.
        """
        spent: set[str] = set()
        try:
            waiting = cave.read_calls(self.pine, self._cave)
        except PineError:
            return spent
        for spec in self.table.calls:
            if not spec.once:
                continue
            pair = self._call_pair(spec)
            if pair is None:
                continue
            if pair in waiting:
                self._calls_pending.setdefault(spec.name, pair)
            else:
                self._calls_done.add(spec.name)
                spent.add(spec.name)
        return spent

    def _view_targets(self) -> tuple[int, int]:
        """Which actor slot each view follows: view 1 shows `camera_follows`' hero."""
        primary = 0 if self.config.camera_follows == self.config.p1 else 1
        return (primary, 1 - primary)

    def _camera_entries(self) -> list[Entry]:
        """Point each viewport's camera at its own hero.

        Only the *target* is written: distance, height and behaviour belong to the
        stage's own camera code, and overriding them is how a split ends up with
        two cameras inside a wall. Without a measured camera structure this
        returns nothing and the engine's own path is left alone.
        """
        camera = self.table.structs.get("camera")
        if camera is None or camera.base.addr is None:
            return []
        if not camera.stride and camera.count > 1:
            self._note_once(
                "camera_targets",
                "structs.camera.stride is 0, so both viewports would resolve to the same camera; "
                "measure the stride between the two camera slots (docs/DISCOVERY.md#camera)",
            )
            return []
        try:
            out = []
            for view, hero in enumerate(self._view_targets()):
                addr = camera.address_of(camera.base.addr, view, "target_actor")
                out.append(Entry(addr, hero, f"view{view + 1}->hero{hero + 1}", essential=False))
            return out
        except TableError:
            return []

    def _radius_entries(self) -> list[Entry]:
        """Pull player two back inside ``tether_radius`` of player one.

        This is the leash for two viewports of one stage: nothing stops the heroes
        walking into different load zones, and the cheap fix is a distance clamp.

        It is deliberately coarse. The distance is measured by the *host* (a PINE
        read, once per watchdog pass) and the correction is written as constant
        words, so it can be up to one watchdog period late -- which is why the
        default radius is off and why this is optional rather than essential. The
        alternative -- asking the cave to do float arithmetic and square roots on
        every frame -- would mean a different interpreter for one feature that only
        matters when the two players deliberately walk apart.

        Direction is preserved: the partner is moved back along the line to player
        one rather than teleported on top of them, so a player who is running away
        is stopped instead of disoriented.
        """
        radius = float(self.config.tether_radius or 0.0)
        if radius <= 0:
            return []
        actor = self.table.structs.get("actor")
        if actor is None or actor.base.addr is None or not actor.stride:
            return []
        try:
            offsets = [actor.field_offset(name) for name in ("pos_x", "pos_y", "pos_z")]
        except TableError:
            self._note_once(
                "tether_radius:fields",
                "tether_radius is set but the actor's position fields are not all measured, so "
                "the leash cannot be applied (docs/DISCOVERY.md#position)",
            )
            return []
        base = actor.base.addr
        first = [self._read_float(base + Slot.P1.value * actor.stride + off) for off in offsets]
        second = [self._read_float(base + Slot.P2.value * actor.stride + off) for off in offsets]
        if any(value is None for value in first + second):
            return []
        distance = math.sqrt(sum((b - a) ** 2 for a, b in zip(first, second)))
        if not math.isfinite(distance) or distance <= radius:
            return []
        scale = radius / distance
        out: list[Entry] = []
        for name, off, a, b in zip(("pos_x", "pos_y", "pos_z"), offsets, first, second):
            clamped = a + (b - a) * scale
            addr = base + Slot.P2.value * actor.stride + off
            out.append(
                Entry(addr, self._float_word(clamped), f"hero2.{name}=leash", essential=False)
            )
        return out

    @staticmethod
    def _float_word(value: float) -> int:
        return struct.unpack("<I", struct.pack("<f", value))[0]

    def _read_float(self, addr: int) -> float | None:
        """Read a word and interpret it as the IEEE single the engine stores there."""
        try:
            word = self.pine.read32(addr)
        except PineError:
            return None
        value = struct.unpack("<f", struct.pack("<I", word))[0]
        return value if math.isfinite(value) else None

    def _note_once(self, key: str, message: str) -> None:
        """Remember a missing measurement; it is reported once, on the next pass.

        Repeating the same line in every watchdog report would bury the warnings
        that are actually new, and this one cannot change while the mod runs -- so it
        is said once and remembered as said, even though the condition behind it is
        still true on the next pass.
        """
        if key not in self._notes_seen and key not in self._notes_reported:
            self._notes_seen[key] = message

    def _tether_entries(self) -> list[Entry]:
        """Mirror player one onto player two, *in game*, with indirect entries.

        Doing it with constants would need a read every tick and would lag by a
        watchdog period; an indirect entry copies within the same frame the game
        runs, which keeps the two heroes visually glued instead of a frame apart.
        """
        actor = self.table.structs.get("actor")
        if actor is None or actor.base.addr is None or not actor.stride:
            return []
        base = actor.base.addr
        out: list[Entry] = []
        try:
            for field_name in ("pos_x", "pos_y", "pos_z", "yaw"):
                src = actor.address_of(base, Slot.P1.value, field_name)
                dst = actor.address_of(base, Slot.P2.value, field_name)
                addr, value = cave.copy(dst, src)
                out.append(Entry(addr, value, f"hero2.{field_name}=follow hero1", essential=False))
        except TableError:
            return []
        return out

    @property
    def independent(self) -> bool:
        """Has the second hero stopped needing the tether?

        The honest test is whether the game itself has moved it away from player
        one: if the partner stays exactly on top of the hero for a full second
        after we stop tethering, its logic is not driving it (or the stage has no
        partner path) and we go back to mirroring instead of leaving a dead body.
        """
        actor = self.table.structs.get("actor")
        if actor is None or actor.base.addr is None or not actor.stride:
            return True
        base = actor.base.addr
        try:
            p1x = self.pine.read32(actor.address_of(base, Slot.P1.value, "pos_x"))
            p2x = self.pine.read32(actor.address_of(base, Slot.P2.value, "pos_x"))
        except (TableError, PineError):
            return True
        if p1x == p2x:
            self._independent_at = None
            return False
        if self._independent_at is None:
            self._independent_at = time.monotonic()
            return False
        return (time.monotonic() - self._independent_at) > 1.0

    def tick(self) -> TickReport:
        """One watchdog pass: refill the parameter block, read back what happened."""
        report = TickReport()
        if not self._interpreter or not self._cave:
            report.ok = False
            report.warnings.append("not installed: call install() first")
            return report

        try:
            info = self.pine.require_running()
            report.info = info
        except PineUnusable as exc:
            report.ok = False
            report.warnings.append(str(exc))
            return report

        # Before anything is rewritten: what does the cave still have queued?
        self._settle_calls(report)

        plan = self.plan()
        entries = plan.values()
        report.wrote = plan.labels()[:6]
        if len(plan.labels()) > 6:
            report.wrote.append(f"+{len(plan.labels()) - 6} more")
        report.warnings.extend(plan.warnings)
        try:
            # Only the entry list is rewritten. The header's tick counter and
            # applied count belong to the game and are how we tell whether the
            # hook is alive -- overwriting them every pass would erase the very
            # proof we are reading, and would look like a permanent stall.
            self.pine.write_bytes(
                cave.param_block_at(self._cave) + cave.ENTRIES_OFFSET,
                cave.encode_entries(entries),
            )
            self.pine.write_bytes(
                cave.param_block_at(self._cave) + cave.CALLS_OFFSET,
                cave.encode_calls(plan.calls),
            )
            self._arm_calls(plan)
        except PineError as exc:
            report.ok = False
            report.warnings.append(f"could not refill the cave: {exc}")
            return report

        block = cave.read_block(self.pine, self._cave)
        report.tick = block["ticks"]
        report.applied = block["applied"]

        if not block["magic_ok"]:
            report.ok = False
            report.warnings.append(
                f"the parameter block at {cave.param_block_at(self._cave):#x} lost its magic; "
                "something else is using that memory. Reinstall with --cave <other address>."
            )
            return report

        if report.tick == self._last_tick:
            self._tick_stall += 1
            report.ok = False
            report.warnings.append(
                f"the hook has not run for {self._tick_stall} watchdog passes: the game is probably "
                "paused in a menu, between stages, or the site is not on the per-frame path"
            )
        else:
            self._tick_stall = 0
        self._last_tick = report.tick
        return report

    def run(self, hz: float = 4.0, duration: float | None = None, on_tick=None) -> int:
        """Watchdog loop: refill the cave until interrupted. Returns passes made."""
        period = 1.0 / hz
        started = time.monotonic()
        passes = 0
        try:
            while True:
                report = self.tick()
                passes += 1
                if on_tick:
                    on_tick(report)
                if duration is not None and time.monotonic() - started >= duration:
                    return passes
                time.sleep(period)
        except KeyboardInterrupt:
            return passes

    # ------------------------------------------------------------------ policy

    def assign(self, p1: str, p2: str) -> None:
        """Switch who is who mid-stage. Two writes on the next tick."""
        candidate = CoopConfig(**{**self.config.__dict__, "p1": p1, "p2": p2})
        candidate.validate()
        self.config = candidate

    def swap(self) -> None:
        """Kei and Yumi trade places -- the pad each hero answers to goes with them."""
        self.assign(self.config.p2, self.config.p1)

    def set_split(self, split: Split) -> None:
        self.config.split = split

    def eject(self) -> list[str]:
        """Put the game back the way we found it. Safe to call on a half-install."""
        done: list[str] = []
        if self._interpreter and self._original_site:
            try:
                self.pine.write_bytes(self._interpreter.site, self._original_site)
                done.append(f"restored the call at {self._interpreter.site:#x}")
            except PineError as exc:
                done.append(f"could not restore the call: {exc}")
        if self._cave:
            try:
                size = cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE
                self.pine.write_bytes(self._cave, b"\x00" * size)
                done.append(f"zeroed the cave at {self._cave:#x}")
            except PineError as exc:
                done.append(f"could not zero the cave: {exc}")
        self._cave = 0
        self._interpreter = None
        # A later install is a new cave and a new session: the calls it was told to
        # make have not been made by *it*.
        self._calls_pending.clear()
        self._calls_done.clear()
        return done

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        info = None
        try:
            info = self.pine.info()
        except PineError:
            pass
        plan = self.plan()
        return {
            "installed": bool(self._cave),
            "cave": self._cave,
            "site": self._install_site,
            "p1": self.config.p1,
            "p2": self.config.p2,
            "split": self.config.split.value,
            "hud": hud.describe_for(self.table, self.config.hud_mode),
            "calls": plan.describe_calls(),
            "views": views.plan_views(
                self.table,
                self.config.split.value,
                self.config.screen,
                targets=self._view_targets(),
                resolved=self._resolved,
            ).describe(),
            "writes": len(plan.entries),
            "independent": self.independent,
            "tick": self._last_tick,
            "disc": str(info) if info else "unreachable",
        }


def character_from(text: str | None, default: str = KEI) -> str:
    """``--p1 yumi`` / ``--p1 1`` / ``--p1 auto`` all land here."""
    if not text:
        return default
    lowered = text.strip().lower()
    if lowered in ("auto", "either", "any"):
        return default
    if lowered in ("0", "kei", "satoru"):
        return KEI
    if lowered in ("1", "yumi", "sayaka"):
        return YUMI
    raise ValueError(f"unknown character {text!r}: use kei or yumi")


def partner_of(who: str) -> str:
    return YUMI if who == KEI else KEI
