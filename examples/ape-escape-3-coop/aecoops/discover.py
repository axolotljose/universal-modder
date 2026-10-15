"""Discovery: finding the addresses a co-op mod needs, on a disc we have never seen.

Everything here is the same shape: read memory over PINE, look for a *property*
that only the thing we want can have, and then confirm it by watching it behave.

The property that matters most is liveness. A hook site only works if the game
calls it exactly once per frame, and you cannot tell that from a disassembly --
plenty of engine functions are called from a render loop, a pause path or a
menu. So the harness *installs the cave on a candidate and measures its tick
rate*: ~59.94 Hz on an NTSC disc, ~50 Hz on a PAL one. That measurement is the
oracle; a candidate that does not tick at frame rate is rejected, automatically.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import cave, mips
from .pine import Pine, PineError
from .scan import DEFAULT_RANGES, EE_RAM, Match, find_pattern

NTSC_HZ = 60.0
PAL_HZ = 50.0


def try_candidates(
    pine: Pine,
    site: int,
    cave_addr: int,
    candidates: list[tuple[int, int]],
    ask,
    duration: float = 3.0,
    expected_hz: float | None = None,
    stop_at_first: bool = True,
    on_try=None,
) -> TryReport:
    """Poke candidates one at a time and ask a human whether each one did it.

    The last step of every recipe on this route is the same, and it is the step that
    cannot be automated: write a candidate value, look at the screen, decide. Doing
    it by hand means a shell command per candidate, and a diff of forty candidates
    is forty commands with the same five arguments -- which is how a measureable
    question turns into an afternoon.

    So the loop lives here, and the judgement is injected: ``ask(address, value,
    index, total) -> bool`` is called *after* each poke, and the CLI supplies one
    that reads the keyboard while a test supplies a script. ``on_try``, if given,
    gets the :class:`PokeReport` for each candidate -- a caller that wants to print
    tick rates, or a test that wants to assert every poke was undone.

    Every poke goes through :func:`poke_entries`, so each candidate is tried with
    the same guarantees: the instruction is restored and the cave zeroed even if the
    game is interrupted or the write is refused, and the whole run refuses to start
    on top of an installed cave.

    The return value is the record, not just the answer: which addresses were tried
    and what was said about each, so that a *no* on every one of them is still
    information rather than a dead command.
    """
    report = TryReport()
    total = len(candidates)
    for index, (address, value) in enumerate(candidates, start=1):
        try:
            poke = poke_entries(
                pine,
                site,
                cave_addr,
                [cave.const(address, value)],
                duration=duration,
                expected_hz=expected_hz,
            )
        except (ValueError, RuntimeError, PineError) as exc:
            # A candidate that cannot be tried is one candidate, not the end of the
            # run: the site may not be a jal, or a cave may be installed.
            report.tried.append((address, value, f"not tried: {exc}"))
            if on_try:
                on_try(None, address, value)
            continue
        if on_try:
            on_try(poke, address, value)
        if not ask(address, value, index, total):
            report.tried.append((address, value, "no"))
            continue
        report.tried.append((address, value, "yes"))
        if report.winner is None:
            # First hit wins the title even in a survey: on a survey the useful
            # answer is still "try this one first", with the rest listed after it.
            report.winner = (address, value)
        report.stopped = "accepted"
        if stop_at_first:
            return report
    report.stopped = "exhausted" if report.winner is None else "accepted"
    return report


# --------------------------------------------------------------- frame counters


@dataclass
class Counter:
    addr: int
    values: tuple[int, ...]

    @property
    def delta(self) -> int:
        return self.values[-1] - self.values[0]

    def hz(self, elapsed: float) -> float:
        return self.delta / elapsed if elapsed > 0 else 0.0

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Counter {self.addr:#010x} {self.values} d={self.delta}>"


def sample_words(pine: Pine, ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Read every word in ``ranges`` as (address, value), in one pass per chunk."""
    out: list[tuple[int, int]] = []
    for start, end in ranges:
        addr = start
        while addr < end:
            take = min(262144, end - addr)
            blob = pine.read_bytes(addr, take)
            for i in range(0, len(blob) - 3, 4):
                out.append((addr + i, struct.unpack_from("<I", blob, i)[0]))
            addr += take
    return out


def find_counters(
    pine: Pine,
    ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES,
    interval: float = 1.0,
    samples: int = 3,
    min_delta: int = 20,
    max_delta: int = 200,
) -> list[Counter]:
    """Words that increase by a constant amount over time: timers and frame counters.

    A game's global frame counter ticks once per frame forever, so its delta over
    a known wall-clock interval *is* the frame rate. Three samples are needed
    because a monotonic one-off increment (a score, a stage timer that stutters)
    looks like a counter if you only take two.
    """
    if samples < 2:
        raise ValueError("need at least two samples")
    history: list[list[tuple[int, int]]] = []
    times: list[float] = []
    for i in range(samples):
        times.append(time.monotonic())
        history.append(sample_words(pine, ranges))
        if i != samples - 1:
            time.sleep(interval)
    elapsed = times[-1] - times[0]

    # Look the samples up through dicts built once, not once per address: over 32 MB
    # that difference is a few seconds versus several minutes.
    lookups = [dict(sample) for sample in history]
    candidates: list[Counter] = []
    for addr in lookups[0]:
        series = tuple(table.get(addr, 0) for table in lookups)
        deltas = [series[i + 1] - series[i] for i in range(len(series) - 1)]
        if not deltas or any(d <= 0 for d in deltas):
            continue
        mean = sum(deltas) / len(deltas)
        if not min_delta <= mean <= max_delta:
            continue
        # Sampling over a socket has jitter, so deltas never match exactly. What
        # separates a frame counter from a stage timer is whether they *nearly*
        # match: a timer that stutters (it pauses while a menu is open, it counts
        # in tenths) spreads out far more than a quarter of its own mean.
        spread = max(deltas) - min(deltas)
        if spread > max(2.0, mean * 0.25):
            continue
        candidates.append(Counter(addr, series))

    # Rank by how close the rate is to a real frame rate, then by how steady the
    # deltas were, and only then by size. The order matters: ranking on the raw
    # delta -- which is what a "biggest number wins" heuristic does -- picks a fast
    # menu timer over the frame counter every time, and ranking on a coarse
    # "within 3 Hz" bucket lets a jittery timer win the tie-break by having the
    # larger total.
    def score(c: Counter) -> tuple[float, int, int]:
        hz = c.hz(elapsed)
        distance = min(abs(hz - NTSC_HZ), abs(hz - PAL_HZ))
        deltas = [c.values[i + 1] - c.values[i] for i in range(len(c.values) - 1)]
        spread = (max(deltas) - min(deltas)) if deltas else 0
        return (round(distance, 3), spread, -abs(c.delta))

    candidates.sort(key=score)
    return candidates


def find_frame_counter(pine: Pine, ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES, **kwargs) -> Counter:
    found = find_counters(pine, ranges, **kwargs)
    if not found:
        raise LookupError(
            "no counter found. Be sure you are in a running stage (not the title screen or a "
            "pause), give it a longer --interval, or widen --range."
        )
    best = found[0]
    window = max(3.0, NTSC_HZ * 0.05)
    hz = best.hz(float(kwargs.get("interval", 1.0)))
    if min(abs(hz - NTSC_HZ), abs(hz - PAL_HZ)) > window:
        raise LookupError(
            f"best candidate {best.addr:#x} advances {hz:.1f} Hz, which is not a frame rate. "
            "The game may be paused or running frame-limited elsewhere."
        )
    return best


# ------------------------------------------------------------------ differencing


@dataclass
class Snapshot:
    ranges: tuple[tuple[int, int], ...]
    words: dict[int, int] = field(default_factory=dict)
    taken_at: float = 0.0

    @classmethod
    def take(cls, pine: Pine, ranges: Sequence[tuple[int, int]] = (EE_RAM,)) -> "Snapshot":
        return cls(ranges=tuple(ranges), words=dict(sample_words(pine, ranges)), taken_at=time.monotonic())

    def diff(self, other: "Snapshot", kind: str = "changed", limit: int = 64) -> list[tuple[int, int, int]]:
        """(address, before, after) for words that changed / were written / went quiet.

        ``changed`` -- any difference.
        ``written`` -- before was 0 or obviously stale and after is not.
        ``settled`` -- values that stopped changing between the two samples (the
        signature of frame state becoming stage state).

        ``limit=0`` means every difference: the narrowing filters above this need
        the whole list to choose from, and "stop at zero" is a trap that looks
        like "found nothing".
        """
        out: list[tuple[int, int, int]] = []
        for addr, after in other.words.items():
            before = self.words.get(addr, 0)
            if kind == "changed" and before != after:
                out.append((addr, before, after))
            elif kind == "written" and before == 0 and after != 0:
                out.append((addr, before, after))
            if limit and len(out) >= limit:
                break
        return out

    def save(self, path: str | Path) -> Path:
        """Write a snapshot to disk, so two *states* can be compared later.

        Comparing states is how the interesting values are found: a story stage
        against a two-player minigame, or a MonkeyCam at its normal size against
        one enlarged with L2. Those two readings are minutes apart and separated by
        a menu, which is exactly why they cannot be diffed from one process.
        """
        target = Path(path)
        payload = {
            "taken_at": self.taken_at,
            "ranges": [[start, end] for start, end in self.ranges],
            "words": {f"{addr:08x}": value for addr, value in sorted(self.words.items())},
        }
        target.write_text(json.dumps(payload) + "\n")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Snapshot":
        raw = json.loads(Path(path).read_text())
        return cls(
            ranges=tuple((int(a), int(b)) for a, b in raw.get("ranges", [])),
            words={int(addr, 16): int(value) for addr, value in raw.get("words", {}).items()},
            taken_at=float(raw.get("taken_at", 0.0)),
        )

    def filtered_diff(
        self,
        other: "Snapshot",
        kind: str = "changed",
        limit: int = 64,
        max_value: int | None = None,
        min_value: int | None = None,
        near: int | None = None,
        span: int = 0x400,
    ) -> list[tuple[int, int, int]]:
        """``diff``, then narrowed the way a search actually needs it.

        ``max_value`` is the important one: "a word that changed between a story
        stage and a minigame, and is a small integer" is a handful of candidates,
        where the raw diff of a 32 MB console is thousands.
        """
        rows = self.diff(other, kind, limit=0)
        low = 0 if min_value is None else min_value
        out = []
        for addr, before, after in rows:
            if near is not None and not (near <= addr < near + span):
                continue
            if max_value is not None and not (abs(before) <= max_value and abs(after) <= max_value):
                continue
            if min_value is not None and not (before >= low and after >= low):
                continue
            out.append((addr, before, after))
            if limit and len(out) >= limit:
                break
        return out

    def find(self, value: int) -> list[int]:
        return [a for a, v in self.words.items() if v == value]

    def near(self, addr: int, count: int = 16) -> list[tuple[int, int]]:
        """Words around an address, for eyeballing a structure you just found."""
        step = 4
        return [(addr + i * step, self.words.get(addr + i * step, 0)) for i in range(-count, count + 1)]


def watch(pine: Pine, ranges: Sequence[tuple[int, int]], span: float = 0.5, limit: int = 32) -> list[tuple[int, int, int]]:
    """What changes in this region over ``span`` seconds."""
    before = Snapshot.take(pine, ranges)
    time.sleep(span)
    after = Snapshot.take(pine, ranges)
    return before.diff(after, "changed", limit)


# ----------------------------------------------------------------- site probing


@dataclass
class Probe:
    site: int
    cave: int
    callee: int
    hz: float
    ticks: int
    applied: int
    resident: bool
    verdict: str


def probe_site(
    pine: Pine,
    site: int,
    cave_addr: int,
    duration: float = 1.5,
    expected_hz: float | None = None,
    max_entries: int = cave.MAX_ENTRIES,
) -> Probe:
    """Install the interpreter on ``site``, measure how often the game calls it, uninstall.

    The site must hold a ``jal``. Returns the measured call rate -- the verdict
    field says whether that rate looks like a frame.
    """
    original = pine.read_bytes(site, 4)
    (word,) = struct.unpack("<I", original)
    callee = cave.callee_of_jal(word, site)

    interp = cave.build_interpreter(cave_addr, site, callee, max_entries=max_entries)

    if probe_is_installed(pine, site, cave_addr):
        raise RuntimeError(f"a cave is already installed at {cave_addr:#x} / site {site:#x}")

    try:
        pine.write_bytes(cave_addr, interp.code)
        pine.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block(max_entries))
        pine.write_bytes(site, interp.trampoline)

        first = cave.read_block(pine, cave_addr)
        t0 = time.monotonic()
        time.sleep(duration)
        second = cave.read_block(pine, cave_addr)
        elapsed = time.monotonic() - t0
    finally:
        # Always put the game back, even if the probe blew up.
        pine.write_bytes(site, original)
        pine.write_bytes(cave_addr, b"\x00" * len(interp.code))
        pine.write_bytes(cave.param_block_at(cave_addr), b"\x00" * cave.PARAM_BLOCK_SIZE)

    ticks = (second["ticks"] - first["ticks"]) & 0xFFFFFFFF
    hz = ticks / elapsed if elapsed > 0 else 0.0
    target = expected_hz if expected_hz is not None else NTSC_HZ
    resident = bool(second["magic_ok"] and first["magic_ok"])
    if not resident:
        verdict = "the parameter block was cleared while running: something else owns this memory"
    elif ticks == 0:
        verdict = "never called: this code path is not resident while you are in this scene"
    elif abs(hz - target) > max(2.0, target * 0.1):
        verdict = f"called at {hz:.1f} Hz, not frame rate: it is a render/menu/conditional path"
    else:
        verdict = "per frame"
    return Probe(
        site=site,
        cave=cave_addr,
        callee=callee,
        hz=hz,
        ticks=ticks,
        applied=int(second["applied"]),
        resident=resident,
        verdict=verdict,
    )


@dataclass
class TryReport:
    """The outcome of walking a candidate list and asking a human about each one."""

    #: (address, value, what the human said) in the order they were tried.
    tried: list[tuple[int, int, str]] = field(default_factory=list)
    winner: tuple[int, int] | None = None
    stopped: str = ""  # "accepted", "quit", "exhausted", "interrupted"

    @property
    def accepted(self) -> list[tuple[int, int]]:
        """Every candidate that was said to work, in the order they were tried."""
        return [(addr, value) for addr, value, verdict in self.tried if verdict == "yes"]

    def describe(self) -> str:
        if self.winner is not None:
            return f"{self.winner[0]:#010x} = {self.winner[1]} (candidate {len(self.tried)})"
        if self.stopped == "quit":
            return f"stopped after {len(self.tried)} candidate(s) at your request"
        return f"{len(self.tried)} candidate(s) tried, none of them did it"


@dataclass
class PokeReport:
    site: int
    cave: int
    entries: list[tuple[int, int]]
    calls: list[tuple[int, int]] = field(default_factory=list)
    ticks: int = 0
    applied: int = 0
    verdict: str = ""


def poke_entries(
    pine: Pine,
    site: int,
    cave_addr: int,
    entries: list[tuple[int, int]],
    calls: list[tuple[int, int]] | None = None,
    duration: float = 3.0,
    expected_hz: float | None = None,
    max_entries: int = cave.MAX_ENTRIES,
) -> PokeReport:
    """Write a chosen list of values every frame for a few seconds, then undo it.

    This is the *write* half of discovery, and it is what turns a guess into a
    measurement. "Which word moves the HUD?" cannot be answered by reading: you
    set a candidate every frame -- the engine rewrites its own state mid-frame, so
    a one-shot write proves nothing -- watch the screen, and stop.

    Safety properties, in order of importance:

    * it refuses to run if a cave is already installed on this site, so it cannot
      stomp a co-op session that is already running;
    * it restores the original instruction and zeroes the cave in a ``finally``,
      so a crash, a Ctrl-C or a bad entry still leaves the game as it was found;
    * it writes only the addresses it was given. There is no "region" mode: a
      discovery tool that scribbles across memory is how a save file dies;
    * calls are queued once, not every frame, so "call the actor constructor" is one
      construction rather than one per frame for three seconds. A call the game
      refuses to survive (it segfaults, or it loops) is exactly the kind of thing
      this command exists to find out, and the timer is the difference between
      finding out and finding out repeatedly.

    The return value's tick rate is the evidence that the write actually happened
    every frame rather than once while you were looking away.
    """
    original = pine.read_bytes(site, 4)
    (word,) = struct.unpack("<I", original)
    if (word >> 26) != 0x03:
        raise ValueError(f"{site:#x} does not hold a jal, so nothing can be intercepted there")
    callee = cave.callee_of_jal(word, site)
    if probe_is_installed(pine, site, cave_addr):
        raise RuntimeError(
            f"a cave is already installed at {cave_addr:#x} (site {site:#x}). Eject it first: "
            "`aecoops eject --site ... --cave ...`"
        )

    interp = cave.build_interpreter(cave_addr, site, callee, max_entries=max_entries)
    try:
        pine.write_bytes(cave_addr, interp.code)
        pine.write_bytes(cave.param_block_at(cave_addr), cave.initial_param_block(max_entries))
        pine.write_bytes(cave.param_block_at(cave_addr) + cave.ENTRIES_OFFSET, cave.encode_entries(entries, max_entries))
        pine.write_bytes(cave.param_block_at(cave_addr) + cave.CALLS_OFFSET, cave.encode_calls(calls or []))
        pine.write_bytes(site, interp.trampoline)

        first = cave.read_block(pine, cave_addr)
        t0 = time.monotonic()
        time.sleep(duration)
        second = cave.read_block(pine, cave_addr)
        elapsed = time.monotonic() - t0
    finally:
        pine.write_bytes(site, original)
        pine.write_bytes(cave_addr, b"\x00" * len(interp.code))
        pine.write_bytes(cave.param_block_at(cave_addr), b"\x00" * cave.PARAM_BLOCK_SIZE)

    ticks = (second["ticks"] - first["ticks"]) & 0xFFFFFFFF
    hz = ticks / elapsed if elapsed > 0 else 0.0
    target = expected_hz if expected_hz is not None else NTSC_HZ
    if not (first["magic_ok"] and second["magic_ok"]):
        verdict = "the parameter block was cleared: something else owns this memory"
    elif ticks == 0:
        verdict = "never called: not on the per-frame path in this scene"
    elif abs(hz - target) > max(2.0, target * 0.1):
        verdict = f"called at {hz:.1f} Hz: the writes are not landing every frame"
    else:
        bits = [f"writing {len(entries)} entries every frame"]
        if calls:
            bits.append(f"calling {len(calls)} function(s) once per refill")
        verdict = " and ".join(bits) + f" for {duration:.1f}s"
    return PokeReport(
        site=site,
        cave=cave_addr,
        entries=entries,
        calls=list(calls or []),
        ticks=ticks,
        applied=int(second["applied"]),
        verdict=verdict,
    )


def probe_is_installed(pine: Pine, site: int, cave_addr: int) -> bool:
    """Is the trampoline already at the site, pointing at this cave?"""
    current = struct.unpack("<I", pine.read_bytes(site, 4))[0]
    return (current >> 26) == 0x03 and cave.callee_of_jal(current, site) == cave_addr


def sweep_sites(
    pine: Pine,
    candidates: Iterable[tuple[str, int]],
    cave_addr: int,
    duration: float = 1.2,
    expected_hz: float | None = None,
    progress=None,
) -> list[Probe]:
    """Probe a list of (name, address) candidates and return the per-frame ones.

    Feed it a stripped game's candidates from a Ghidra function map or a
    disassembly pass; feed it the ELF symbol table's ``*Frame*``/``*Loop*``/
    ``*Update*`` style names when you have symbols.
    """
    results: list[Probe] = []
    for name, addr in candidates:
        if progress:
            progress(name, addr)
        try:
            word = struct.unpack("<I", pine.read_bytes(addr, 4))[0]
        except Exception:  # pragma: no cover - transport hiccup
            continue
        if (word >> 26) != 0x03:
            continue  # not a jal: cannot be intercepted this way
        try:
            probe = probe_site(pine, addr, cave_addr, duration, expected_hz)
        except Exception:  # pragma: no cover - keep sweeping
            continue
        probe = Probe(**{**probe.__dict__, "verdict": f"{name}: {probe.verdict}"})
        results.append(probe)
    results.sort(key=lambda p: -p.hz)
    return results


def find_jal_targets(
    pine: Pine, target: int, ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES, limit: int = 32
) -> list[Match]:
    """Every ``jal <target>`` instruction in RAM -- i.e. who calls that function.

    A MIPS ``jal`` only encodes 26 of the target's 28 bits (the low two are always
    zero), so we scan for the two middle bytes and then decode each hit properly
    against its own address, which is what resolves the top four bits.
    """
    encoded = struct.pack("<I", (0x03 << 26) | ((target >> 2) & 0x03FFFFFF))
    out: list[Match] = []
    for hit in find_pattern(pine, f"?? {encoded[1]:02x} {encoded[2]:02x}", ranges, limit=limit * 16):
        if hit.addr % 4:
            continue
        word = struct.unpack("<I", pine.read_bytes(hit.addr, 4))[0]
        if (word >> 26) == 0x03 and cave.callee_of_jal(word, hit.addr) == target:
            out.append(Match(hit.addr, pine.read_bytes(hit.addr, 4)))
            if len(out) >= limit:
                break
    return out


def read_disassembly(pine: Pine, addr: int, instructions: int = 24) -> list[str]:
    """A readable listing of RAM, for eyeballing a function you just found.

    This is a *reader*: it decodes the words into mnemonics so a human (or an
    agent) can recognise a prologue or a struct access pattern. It is not a
    decompiler and it never writes.
    """
    out: list[str] = []
    for i in range(instructions):
        pc = addr + i * 4
        word = struct.unpack("<I", pine.read_bytes(pc, 4))[0]
        out.append(f"{pc:#010x}  {word:08x}  {mips.disassemble(word, pc)}")
    return out
