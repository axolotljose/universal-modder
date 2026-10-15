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

import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from . import cave
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

    def validate(self) -> None:
        for who in (self.p1, self.p2):
            if who not in (KEI, YUMI):
                raise ValueError(f"unknown character {who!r}: pick {KEI!r} or {YUMI!r}")
        if self.p1 == self.p2:
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
        return cls(p1=who, p2=who)


@dataclass
class TickReport:
    ok: bool = True
    tick: int = 0
    applied: int = 0
    wrote: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: EmuInfo | None = None

    def summary(self) -> str:
        state = "ok" if self.ok else "PROBLEM"
        bits = [f"tick {self.tick}", f"{self.applied} writes", state]
        if self.wrote:
            bits.append(", ".join(self.wrote))
        text = " · ".join(bits)
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

    # ------------------------------------------------------------------ install

    def install(self, cave_addr: int | None = None, site: int | None = None, journal: Path | None = None) -> InstallReport:
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

            self._resolved = self.table.resolve_all(self.pine)
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

        if self._is_installed():
            report.ok = True
            report.cave, report.site, report.callee = cave_addr, site, self._interpreter.callee
            report.notes.append("cave was already installed; left as it is")
            return report

        # Order matters: the cave and its block exist before anything jumps to it.
        self.pine.write_bytes(cave_addr, self._interpreter.code)
        self.pine.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block())
        self.pine.write_bytes(site, self._interpreter.trampoline)

        report.ok = True
        report.cave = cave_addr
        report.site = site
        report.callee = self._interpreter.callee
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

    def entries(self) -> list[tuple[int, int]]:
        """The (address, value) pairs the game should re-apply this frame.

        Everything here is idempotent state, never one-shot input: the engine
        resets it, we set it again, and the result is stable rather than
        oscillating.
        """
        table, cfg = self.table, self.config
        out: list[tuple[int, int]] = []

        globals_ = self._resolved

        def set_global(name: str, value: int, label: str) -> None:
            addr = globals_.get(name)
            if addr is None:
                return
            out.append(cave.const(addr, value))
            labels.append(label)

        labels: list[str] = []
        # 1. two viewports
        if cfg.split is not Split.SINGLE:
            set_global("view_mode", table.layout_id(cfg.split.value), f"split={cfg.split.value}")
        # 2. two live heroes
        set_global("actor_count", 2, "actor_count=2")
        set_global("partner_enabled", 1, "partner=on")

        # 3. per-player character ids, and their pads
        actor = table.structs.get("actor")
        if actor is not None and actor.base.addr is not None:
            base = actor.base.addr
            try:
                for slot, who in ((Slot.P1, cfg.p1), (Slot.P2, cfg.p2)):
                    out.append(cave.const(actor.address_of(base, slot.value, "char_id"), table.character_id(who)))
                    labels.append(f"p{slot.value + 1}={who}")
                    out.append(cave.const(actor.address_of(base, slot.value, "pad_port"), slot.value))
                    out.append(cave.const(actor.address_of(base, slot.value, "active"), 1))
            except TableError as exc:
                labels.append(f"characters skipped ({exc})")

        # 4. the tether: mirror the partner onto player one until it can stand up
        if cfg.tether_until_independent and not self.independent:
            out.extend(self._tether_entries())

        if cfg.share_quota:
            set_global("quota_shared", 1, "quota shared")
        return out

    def _tether_entries(self) -> list[tuple[int, int]]:
        """Mirror player one onto player two, *in game*, with indirect entries.

        Doing it with constants would need a read every tick and would lag by a
        watchdog period; an indirect entry copies within the same frame the game
        runs, which keeps the two heroes visually glued instead of a frame apart.
        """
        actor = self.table.structs.get("actor")
        if actor is None or actor.base.addr is None or not actor.stride:
            return []
        base = actor.base.addr
        out = []
        try:
            for field_name in ("pos_x", "pos_y", "pos_z", "yaw"):
                src = actor.address_of(base, Slot.P1.value, field_name)
                dst = actor.address_of(base, Slot.P2.value, field_name)
                out.append(cave.copy(dst, src))
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

        entries = self.entries()
        try:
            # Only the entry list is rewritten. The header's tick counter and
            # applied count belong to the game and are how we tell whether the
            # hook is alive -- overwriting them every pass would erase the very
            # proof we are reading, and would look like a permanent stall.
            self.pine.write_bytes(
                cave.param_block_at(self._cave) + cave.ENTRIES_OFFSET,
                cave.encode_entries(entries),
            )
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
        return done

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        info = None
        try:
            info = self.pine.info()
        except PineError:
            pass
        return {
            "installed": bool(self._cave),
            "cave": self._cave,
            "site": self._install_site,
            "p1": self.config.p1,
            "p2": self.config.p2,
            "split": self.config.split.value,
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
