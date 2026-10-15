"""``aecoops``: install and run Ape Escape 3 split-screen co-op on a running PCSX2.

Typical first run, in order:

    aecoops doctor                     # is PINE reachable, and what does the table still need?
    aecoops discover counter           # sanity: the game is in a stage and running at frame rate
    aecoops discover sweep --from-elf game.elf    # find and prove the per-frame hook
    aecoops install                    # drop the interpreter in
    aecoops play --p1 kei --p2 yumi    # run co-op; Ctrl-C to stop, then `aecoops eject`

Once a configuration works, freeze it:

    aecoops export-pnach --out patches/SCUS-97501.pnach

Every command takes ``--slot`` (PINE port, default 28011) and ``--table`` (the
address worksheet, default ``aecoops/data/addresses.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import cave, discover, hud, mips, pnach, views
from .addresses import AddressTable, DEFAULT_TABLE, TableError
from .coop import CoopConfig, CoopMod, Split, character_from
from .hud import HudMode, Screen
from .patcher import PatchSet, revert
from .pine import Pine, PineError, PineNotRunning, PineUnusable
from .scan import EE_RAM, EE_SCRATCHPAD

JOURNAL = Path("coop-journal.json")


def _pine(args) -> Pine:
    return Pine(slot=args.slot)


def _table(args) -> AddressTable:
    return AddressTable.load(args.table)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


# ------------------------------------------------------------------- commands


def cmd_doctor(args) -> int:
    table = _table(args)
    print(f"address table: {table.path}")
    print(table.report())
    unresolved = table.unresolved()
    print()
    if unresolved:
        print(f"{len(unresolved)} entries still to find:")
        for name in unresolved[:24]:
            print(f"  - {name}")
        if len(unresolved) > 24:
            print(f"  ... and {len(unresolved) - 24} more")
        print("recipes: docs/DISCOVERY.md")
    else:
        print("every entry in the table is resolved.")

    print()
    try:
        with _pine(args) as pine:
            info = pine.info()
            print(f"PCSX2: {info}")
            if "ape escape 3" not in info.title.lower() and "saru" not in info.title.lower():
                print(
                    "  ! the running disc does not look like Ape Escape 3. PINE talks to whatever "
                    "game is loaded, so all of this only means something with AE3 in the drive."
                )
            status = pine.status()
        print(f"  PINE ok (status {status} -- 0 running, 1 paused, 2 shut down)")
    except PineNotRunning as exc:
        print(f"PINE: not reachable\n  {exc}")
        print("  enable it in PCSX2: Settings -> Advanced -> Enable PINE IPC Server")
    if JOURNAL.exists():
        payload = json.loads(JOURNAL.read_text())
        print(f"journal: {JOURNAL} from {payload.get('written_at')} ({len(payload.get('entries', []))} writes)")
        print("  `aecoops revert` will put those bytes back")
    else:
        print(f"journal: none at {JOURNAL} (nothing installed yet)")
    return 0


def cmd_info(args) -> int:
    with _pine(args) as pine:
        info = pine.info()
        print(info)
        print(f"disc CRC (UUID): {info.uuid}")
        print(f"suggested pnach name: {pnach.pnach_name(info.serial, info.uuid)}")
    return 0


def cmd_discover_counter(args) -> int:
    with _pine(args) as pine:
        counter = discover.find_frame_counter(pine, interval=args.interval, samples=args.samples)
        print(f"frame counter at {counter.addr:#010x}: {counter.values}, +{counter.delta}")
        print(f"  implies {counter.hz(args.interval):.2f} Hz")
    return 0


def cmd_discover_probe(args) -> int:
    with _pine(args) as pine:
        probe = discover.probe_site(pine, args.site, args.cave, duration=args.duration, expected_hz=args.hz)
        print(f"site {probe.site:#010x} -> callee {probe.callee:#010x}")
        print(f"  {probe.ticks} calls in {args.duration:.1f}s = {probe.hz:.2f} Hz")
        print(f"  verdict: {probe.verdict}")
    return 0 if probe.verdict == "per frame" else 1


def cmd_discover_sweep(args) -> int:
    candidates: list[tuple[str, int]] = []
    if args.from_elf:
        from . import elf

        image = elf.load(args.from_elf)
        print(image.summary())
        for sym in image.functions(min_size=args.min_size):
            if args.name_filter and args.name_filter.lower() not in sym.name.lower():
                continue
            candidates.append((sym.name, sym.value))
    if args.from_csv:
        from . import elf as elfmod

        candidates += elfmod.read_ghidra_function_map(args.from_csv)
    if not candidates:
        return _fail("no candidates: pass --from-elf or --from-csv")
    print(f"{len(candidates)} candidates")
    limit = args.limit if args.limit else len(candidates)

    cave_addr = args.cave
    if cave_addr is None:
        with _pine(args) as pine:
            size = cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE
            cave_addr = cave.find_free_region(pine, size, [EE_RAM, EE_SCRATCHPAD])
            print(f"using free RAM at {cave_addr:#010x} for probing (verified stable)")

    results = discover.sweep_sites(
        _pine(args),
        candidates[:limit],
        cave_addr,
        duration=args.duration,
        expected_hz=args.hz,
        progress=lambda name, addr: print(f"  probing {name} @ {addr:#010x} ...", flush=True),
    )
    good = [r for r in results if r.verdict.endswith("per frame")]
    print()
    for probe in results[:12]:
        print(f"  {probe.hz:7.2f} Hz  {probe.site:#010x}  {probe.verdict}")
    if not good:
        print(
            "\nno per-frame candidates. The scene has to be a *running stage* while this runs, "
            "and the hook is only resident then. Try a candidate list from the function that "
            "reads the pad, or filter with --name (e.g. --name update)."
        )
        return 1
    print("\nper-frame candidates (paste the best into data/addresses.json as globals.frame_hook.addr):")
    for probe in good[:5]:
        print(f"  {probe.site:#010x}   {probe.verdict}")
    return 0


def cmd_discover_disasm(args) -> int:
    with _pine(args) as pine:
        for line in discover.read_disassembly(pine, args.addr, args.count):
            print(line)
    return 0


def _ranges(args) -> list[tuple[int, int]]:
    if getattr(args, "range", None):
        return [(int(a, 16), int(b, 16)) for a, b in (r.split(":") for r in args.range)]
    return [EE_RAM]


def cmd_discover_snapshot(args) -> int:
    """Read a region now and save it, so a *state* can be compared later.

    The comparison that matters is between two game states minutes apart -- a
    story stage and a two-player minigame, a MonkeyCam at normal size and one
    enlarged with L2 -- which no single process can hold in memory at once.
    """
    ranges = _ranges(args)
    with _pine(args) as pine:
        shot = discover.Snapshot.take(pine, ranges)
    path = shot.save(args.out)
    print(f"wrote {path}: {len(shot.words)} words from {len(ranges)} range(s)")
    print("  change the game (enter a minigame, press L2), take the second one, then `aecoops discover diff`")
    return 0


def cmd_discover_diff(args) -> int:
    before = discover.Snapshot.load(args.before)
    after = discover.Snapshot.load(args.after)
    rows = before.filtered_diff(
        after,
        kind=args.kind,
        limit=args.limit,
        max_value=args.max_value,
        min_value=args.min_value,
        near=_int_or_none(args.near),
    )
    print(f"{len(rows)} word(s) {args.kind} between the two snapshots")
    for addr, old, new in rows:
        print(f"  {addr:#010x}  {old:#010x} -> {new:#010x}   ({old} -> {new})")
    if not rows and args.max_value is not None:
        print("  nothing small changed: either the state did not actually differ, or the value is not a small int")
    return 0


def cmd_discover_watch(args) -> int:
    """What changes in this region over the next few seconds."""
    ranges = _ranges(args)
    with _pine(args) as pine:
        rows = discover.watch(pine, ranges, span=args.span, limit=args.limit)
    print(f"{len(rows)} word(s) changed in {args.span:.1f}s")
    for addr, old, new in rows[: args.limit]:
        print(f"  {addr:#010x}  {old:#010x} -> {new:#010x}")
    return 0


def cmd_discover_poke(args) -> int:
    """Write candidate values every frame, for a few seconds, then undo them.

    This is the command that answers "which word moves the HUD?": set a candidate
    and watch the screen. It always restores what it found, and it refuses to run
    on top of an installed cave.
    """
    writes: list[tuple[int, int]] = []
    for pair in args.set or []:
        if "=" not in pair:
            return _fail(f"{pair!r} is not ADDR=VALUE")
        addr, value = pair.split("=", 1)
        try:
            writes.append(cave.const(int(addr, 0), int(value, 0)))
        except ValueError as exc:
            return _fail(str(exc))
    for pair in args.copy or []:
        if "=" not in pair:
            return _fail(f"{pair!r} is not DEST=SOURCE")
        dest, source = pair.split("=", 1)
        writes.append(cave.copy(int(dest, 0), int(source, 0)))
    calls: list[tuple[int, int]] = []
    for pair in getattr(args, "call", None) or []:
        if "=" not in pair:
            return _fail(f"{pair!r} is not FUNCTION=ARGUMENT (use FUNCTION=0 for no argument)")
        fn, arg = pair.split("=", 1)
        try:
            calls.append(cave.call(int(fn, 0), int(arg, 0)))
        except ValueError as exc:
            return _fail(str(exc))
    for pair in getattr(args, "call_read", None) or []:
        # The shape a spawner wants: hand it the handle the stage allocated, not a
        # number that was true when this command was typed.
        if "=" not in pair:
            return _fail(f"{pair!r} is not FUNCTION=SOURCE_ADDRESS")
        fn, source = pair.split("=", 1)
        try:
            calls.append(cave.call_read(int(fn, 0), int(source, 0)))
        except ValueError as exc:
            return _fail(str(exc))
    if not writes and not calls:
        return _fail(
            "nothing to do: pass --set ADDR=VALUE, --copy DEST=SOURCE, --call FUNCTION=ARG "
            "or --call-read FUNCTION=SOURCE"
        )
    cave_addr = _int_or_none(args.cave)
    with _pine(args) as pine:
        if cave_addr is None:
            cave_addr = cave.find_free_region(
                pine, cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE, [EE_RAM, EE_SCRATCHPAD]
            )
            print(f"using free RAM at {cave_addr:#010x}")
        what = f"{len(writes)} address(es) every frame"
        if calls:
            what += f" and {len(calls)} call(s) once per refill"
        print(f"doing that for {args.duration:.1f}s -- watch the screen: {what}")
        report = discover.poke_entries(
            pine,
            _int_or_none(args.site),
            cave_addr,
            writes,
            calls=calls,
            duration=args.duration,
            expected_hz=args.hz,
        )
    print(f"  site {report.site:#010x}, cave {report.cave:#010x}")
    print(f"  {report.ticks} ticks, {report.applied} writes per tick")
    print(f"  {report.verdict}")
    print("  everything is back the way it was")
    return 0 if report.ticks else 1


def cmd_find_free(args) -> int:
    if args.range:
        ranges = [(int(a, 16), int(b, 16)) for a, b in (r.split(":") for r in args.range)]
    else:
        ranges = [EE_RAM, EE_SCRATCHPAD]
    with _pine(args) as pine:
        addr = cave.find_free_region(pine, args.size, ranges)
        stable = cave.verify_stable(pine, addr, args.size)
        print(f"{addr:#010x} ({args.size} bytes) stable={stable}")
        if not stable:
            print("  ! something writes there; do not host a cave in it")
    return 0 if stable else 1


def _scan_ranges(args) -> list[tuple[int, int]] | None:
    """``--scan-range START:END`` (hex, repeatable) or None for "everywhere"."""
    raw = getattr(args, "scan_range", None)
    if not raw:
        return None
    try:
        return [(int(a, 16), int(b, 16)) for a, b in (item.split(":") for item in raw)]
    except ValueError as exc:
        raise TableError(f"--scan-range wants START:END in hex: {exc}") from exc


def _config(args) -> CoopConfig:
    p1 = character_from(getattr(args, "p1", None), "kei")
    p2_raw = getattr(args, "p2", None)
    p2 = character_from(p2_raw, "yumi") if p2_raw else ("yumi" if p1 == "kei" else "kei")
    if p1 == p2:
        p2 = "yumi" if p1 == "kei" else "kei"
    return CoopConfig(
        p1=p1,
        p2=p2,
        split=Split(getattr(args, "split", "vertical")),
        hud_mode=HudMode(getattr(args, "hud", "shared")),
        screen=Screen.parse(getattr(args, "screen", None)),
        cameras_follow_views=not getattr(args, "no_cameras", False),
        place_viewports=not getattr(args, "no_viewports", False),
        tether_until_independent=not getattr(args, "no_tether", False),
        tether_radius=float(getattr(args, "tether_radius", 0.0)),
    )


def cmd_install(args) -> int:
    table = _table(args)
    patchset = PatchSet.load(args.patchset) if args.patchset else None
    with _pine(args) as pine:
        mod = CoopMod(pine, table, _config(args), patchset)
        report = mod.install(
            cave_addr=_int_or_none(args.cave),
            site=_int_or_none(args.site),
            journal=Path(args.journal),
            ranges=_scan_ranges(args),
        )
        print(report.summary())
        if not report.ok:
            return 1
        tick = mod.tick()
        print(f"first watchdog pass: {tick.summary()}")
        if tick.warnings:
            print(
                "\nThe cave is installed but the hook has not run yet. Load into a stage and run "
                "`aecoops play` again; if it stays at tick 0 in a stage, the site is not on the "
                "per-frame path -- re-run `aecoops discover sweep`."
            )
    return 0 if report.ok else 1


def cmd_play(args) -> int:
    table = _table(args)
    with _pine(args) as pine:
        mod = CoopMod(pine, table, _config(args))
        if args.site or args.cave:
            report = mod.install(site=_int_or_none(args.site), cave_addr=_int_or_none(args.cave))
            if not report.ok:
                print(report.summary())
                return 1
        elif args.install:
            report = mod.install(ranges=_scan_ranges(args))
            if not report.ok:
                print(report.summary())
                return 1
        print(
            f"co-op: {mod.config.p1} (pad 1) + {mod.config.p2} (pad 2), {mod.config.split.value} split, "
            f"hud {mod.config.hud_mode.value}"
        )
        print("Ctrl-C to stop. `aecoops eject` puts the game back afterwards.")
        state = {"beats": 0}

        def report_tick(tick):
            state["beats"] += 1
            if state["beats"] % 20 == 1 or not tick.ok:
                print(f"  {time.strftime('%H:%M:%S')}  {tick.summary()}")

        passes = mod.run(hz=args.hz, duration=args.duration, on_tick=report_tick)
        print(f"\nstopped after {passes} passes")
    return 0


def cmd_status(args) -> int:
    """Report what the mod would do, including the parts it cannot do yet.

    On a fresh worksheet this is the most useful command in the toolkit: it reads
    the plan the mod would apply and the warnings it would raise, without a single
    write anywhere. The warnings are the to-do list.
    """
    table = _table(args)
    with _pine(args) as pine:
        mod = CoopMod(pine, table, _config(args))
        mod.attach(_int_or_none(args.cave))
        for key, value in mod.status().items():
            print(f"  {key:<12} {value}")
        plan = mod.plan()
        print(f"  {'writes':<12} {len(plan.entries)}: " + ", ".join(e.label for e in plan.entries[:8]) + (" ..." if len(plan.entries) > 8 else ""))
        if plan.warnings:
            print("  waiting on:")
            for warning in plan.warnings:
                print(f"    ! {warning}")
    return 0


def cmd_eject(args) -> int:
    """Undo an install knowing only where things are.

    The original instruction does not have to be remembered: the trampoline is a
    ``jal`` into our cave, and the cave's own ``jal`` still holds the function it
    displaced. Reading that back reconstructs the original call exactly, so eject
    works even after the journal is long gone.
    """
    site = _int_or_none(args.site)
    cave_addr = _int_or_none(args.cave)
    if site is None or cave_addr is None:
        return _fail("eject needs both --site and --cave (the values install printed)")
    with _pine(args) as pine:
        word = _read_word(pine, site)
        if (word >> 26) != 0x03:
            return _fail(f"{site:#x} holds {word:#010x}, which is not a call: nothing to restore")
        target = cave.callee_of_jal(word, site)
        if target != cave_addr:
            return _fail(
                f"{site:#x} calls {target:#x}, not the cave at {cave_addr:#x}; refusing to guess"
            )
        displaced = _displaced_callee(pine, cave_addr)
        if displaced is None:
            return _fail(
                f"the code at {cave_addr:#x} does not look like the interpreter, so the function it "
                "displaced cannot be recovered. Restart the game (or reload a savestate)."
            )
        pine.write_bytes(site, mips.to_bytes([mips.jal(displaced)]))
        pine.write_bytes(cave_addr, b"\x00" * (cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE))
        print(
            f"restored the call to {displaced:#010x} at {site:#x}, "
            f"zeroed the cave at {cave_addr:#x}"
        )
    return 0


def _displaced_callee(pine: Pine, cave_addr: int) -> int | None:
    """The ``jal`` the interpreter ends with: the function it wrapped.

    The cave is built so that its last call is the displaced one, which makes
    uninstalling a read rather than a memory.
    """
    code = pine.read_bytes(cave_addr, cave.CAVE_CODE_SIZE)
    for i in range(len(code) - 4, -1, -4):
        word = int.from_bytes(code[i : i + 4], "little")
        if (word >> 26) == 0x03:
            return cave.callee_of_jal(word, cave_addr + i)
    return None


def cmd_revert(args) -> int:
    path = Path(args.journal)
    if not path.exists():
        return _fail(f"no journal at {path}")
    with _pine(args) as pine:
        count = revert(pine, path)
        print(f"restored {count} address ranges from {path}")
    return 0


def cmd_export_pnach(args) -> int:
    """Freeze the *live* plan into PCSX2's own patch format.

    The point of building this from the same ``plan()`` the watchdog uses is that
    the frozen file cannot quietly differ from what works: whatever the mod would
    write this frame -- split, characters, pads, the tether, the per-view HUD, the
    camera targets -- is what lands in the file.
    """
    site = _int_or_none(args.site)
    cave_addr = _int_or_none(args.cave)
    table = _table(args)
    config = _config(args)
    with _pine(args) as pine:
        info = pine.info()
        if site is None:
            entry = table.globals.get("frame_hook")
            if entry is None:
                return _fail("no frame_hook entry in the table: nothing to hook")
            try:
                site = entry.resolve(pine)
            except LookupError as exc:
                return _fail(f"cannot find the hook site: {exc}")
        if cave_addr is None:
            cave_addr = cave.find_free_region(
                pine, cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE, [EE_RAM, EE_SCRATCHPAD]
            )
        word = _read_word(pine, site)
        mod = CoopMod(pine, table, config)
        mod.resolve(_scan_ranges(args))
        plan = mod.plan()
        if config.split is not Split.SINGLE and not any(e.label.startswith("split=") for e in plan.entries):
            return _fail(
                f"the split value for {config.split.value!r} has not been measured, so there is "
                "nothing honest to freeze yet (docs/DISCOVERY.md#view-mode)"
            )
        entries = plan.values()
        for warning in plan.warnings:
            print(f"  ! {warning}", file=sys.stderr)
        repeating = [spec.name for spec in table.calls if spec.ready and not spec.once]
        if repeating:
            print(
                "  ! these calls are marked as repeating, but a frozen .pnach has no watchdog to "
                "refill them, so each will fire once at the first stage frame: "
                + ", ".join(repeating),
                file=sys.stderr,
            )
        text = pnach.build_from_words(
            site=site,
            site_word=word,
            cave_addr=cave_addr,
            gametitle=info.title or "Ape Escape 3",
            serial=info.serial,
            crc=info.uuid,
            comment=(
                f"co-op split screen: player one {config.p1}, player two {config.p2}, "
                f"{config.split.value} split. Built on {time.strftime('%Y-%m-%d')}."
            ),
            entries=entries,
            calls=plan.calls,
        )
        out = Path(args.out) if args.out else Path(pnach.pnach_name(info.serial, info.uuid))
        pnach.write(text, out)
        print(f"wrote {out} ({len(text.splitlines())} lines; {pnach.explain(text)})")
        print(f"  copy it into PCSX2's cheats/ folder -- it applies itself at boot, no host process needed")
        calls_note = f", {len(plan.calls)} call(s) at the first stage frame" if plan.calls else ""
        print(f"  cave {cave_addr:#010x}, hook {site:#010x}, {len(entries)} baked-in entries{calls_note}")
    return 0


def cmd_set(args) -> int:
    """Write a measurement into the address worksheet.

    Values are parsed as ints (decimal or ``0x`` hex), strings stay strings, so
    ``aecoops set globals.frame_hook.verified=2026-10-15`` records a date and
    ``aecoops set characters.kei=0`` records a number.
    """
    table = _table(args)
    for assignment in args.assignments:
        if "=" not in assignment:
            return _fail(f"{assignment!r} is not PATH=VALUE")
        path, raw = assignment.split("=", 1)
        try:
            value: object = int(raw, 0)
        except ValueError:
            value = raw
        try:
            table.set_value(path.strip(), value)
        except TableError as exc:
            return _fail(str(exc))
        print(f"{path.strip()} = {value}")
    table.save(args.table)
    print(f"wrote {args.table}")
    return 0


def cmd_selfcheck(args) -> int:
    """Offline sanity: nothing here needs PCSX2 or a game."""
    from .selftest import run_all

    return run_all(verbose=args.verbose)


# --------------------------------------------------------------------- parsing


def _slot(value: str) -> int | str:
    """``--slot`` is a TCP port number, or a socket path for PINE's Unix flavour.

    Passing the number through as a string makes the client treat it as a path,
    which fails with a misleading "No such file or directory".
    """
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return text


def _number(text: str) -> int:
    """Accepts both ``4096`` and ``0x1000``: addresses and sizes are written in hex."""
    return int(str(text), 0)


def _int_or_none(text: str | None) -> int | None:
    if text is None:
        return None
    return int(str(text), 0)


def _read_word(pine: Pine, addr: int) -> int:
    import struct

    return struct.unpack("<I", pine.read_bytes(addr, 4))[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aecoops",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--slot", default=28011, type=_slot, help="PINE slot/port (default 28011) or a socket path"
    )
    parser.add_argument("--table", default=str(DEFAULT_TABLE), help="address worksheet to use")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check PINE and report what the address table still needs")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("info", help="print the running emulator and disc")
    p.set_defaults(func=cmd_info)

    disc = sub.add_parser("discover", help="find addresses on your own disc")
    disc_sub = disc.add_subparsers(dest="what", required=True)

    p = disc_sub.add_parser("counter", help="find the global frame counter (proves the game is running)")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--samples", type=int, default=3)
    p.set_defaults(func=cmd_discover_counter)

    p = disc_sub.add_parser("probe", help="install the cave on one candidate and measure its call rate")
    p.add_argument("--site", required=True, help="address holding a jal")
    p.add_argument("--cave", required=True, help="free RAM to build the probe in")
    p.add_argument("--duration", type=float, default=1.5)
    p.add_argument("--hz", type=float, default=None, help="expected frame rate (default 60)")
    p.set_defaults(func=cmd_discover_probe)

    p = disc_sub.add_parser("sweep", help="probe many candidates and keep the per-frame ones")
    p.add_argument("--from-elf", help="PS2 ELF with a symbol table (your own disc's executable)")
    p.add_argument("--from-csv", help="Ghidra function map: name,address[,size]")
    p.add_argument("--name", dest="name_filter", help="only symbols containing this text")
    p.add_argument("--min-size", type=_number, default=0x10)
    p.add_argument("--limit", type=_number, default=200, help="how many candidates to probe (0 = all)")
    p.add_argument("--cave", help="cave address to probe in (default: auto-detect free RAM)")
    p.add_argument("--duration", type=float, default=1.2)
    p.add_argument("--hz", type=float, default=None)
    p.set_defaults(func=cmd_discover_sweep)

    p = disc_sub.add_parser("disasm", help="read a listing of RAM around an address")
    p.add_argument("--addr", required=True)
    p.add_argument("--count", type=_number, default=24)
    p.set_defaults(func=cmd_discover_disasm)

    # Reading finds what a value *is*; writing finds what it *does*. These four
    # are the pair of halves: snapshot/diff compare two game states, watch/diff
    # compare two moments, and poke is the only command in the toolkit that
    # writes RAM for its own sake -- which is why it puts everything back.
    for name, help_text, fn in (
        ("snapshot", "save a region's words so a later state can be compared", cmd_discover_snapshot),
        ("diff", "compare two saved snapshots, narrowed to small values if asked", cmd_discover_diff),
        ("watch", "print what changes in a region over a few seconds", cmd_discover_watch),
        ("poke", "write candidate values every frame for a few seconds, then undo", cmd_discover_poke),
    ):
        p = disc_sub.add_parser(name, help=help_text)
        p.add_argument("--range", action="append", help="START:END in hex; repeatable (default: all EE RAM)")
        if name == "snapshot":
            p.add_argument("--out", required=True, help="file to write the snapshot to")
        if name == "diff":
            p.add_argument("before", help="snapshot taken first (a .json from `discover snapshot`)")
            p.add_argument("after", help="snapshot taken second")
            p.add_argument("--kind", default="changed", choices=["changed", "written", "settled"])
            p.add_argument("--max-value", type=_number, default=None, help="only words whose both values are this small")
            p.add_argument("--min-value", type=_number, default=None)
            p.add_argument("--near", default=None, help="only addresses from here")
            p.add_argument("--limit", type=_number, default=64)
        if name == "watch":
            p.add_argument("--span", type=float, default=0.5)
            p.add_argument("--limit", type=_number, default=32)
        if name == "poke":
            p.add_argument("--site", required=True, help="the per-frame jal to intercept")
            p.add_argument("--cave", default=None, help="cave address (default: auto-detect free RAM)")
            p.add_argument("--set", action="append", metavar="ADDR=VALUE", help="a constant to write; repeatable")
            p.add_argument("--copy", action="append", metavar="DEST=SOURCE", help="mirror one word onto another")
            p.add_argument(
                "--call",
                action="append",
                metavar="FUNCTION=ARG",
                help="call a function of the game's own, once per refill, with ARG in $a0",
            )
            p.add_argument(
                "--call-read",
                action="append",
                metavar="FUNCTION=SOURCE",
                help="...passing the word at SOURCE instead, read at call time",
            )
            p.add_argument("--duration", type=float, default=3.0)
            p.add_argument("--hz", type=float, default=None, help="expected frame rate (default 60)")
        p.set_defaults(func=fn)

    p = sub.add_parser("find-free", help="locate RAM the game never writes to")
    p.add_argument("--size", type=_number, default=cave.CAVE_CODE_SIZE + cave.PARAM_BLOCK_SIZE)
    p.add_argument("--range", action="append", help="START:END in hex; repeatable")
    p.set_defaults(func=cmd_find_free)

    for name, help_text, fn in (
        ("install", "install the interpreter cave into a running game", cmd_install),
        ("play", "run the co-op watchdog loop", cmd_play),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--p1", default="kei", help="kei | yumi | auto")
        p.add_argument("--p2", default=None, help="kei | yumi | auto (default: the other one)")
        p.add_argument("--split", default="vertical", choices=[s.value for s in Split])
        p.add_argument("--site", default=None, help="override the frame hook address")
        p.add_argument("--cave", default=None, help="override the cave address")
        p.add_argument("--patchset", default=None, help="a patch set JSON to apply first")
        p.add_argument("--journal", default=str(JOURNAL), help="where to record what was changed")
        p.add_argument("--no-tether", action="store_true", help="do not mirror player one onto player two")
        p.add_argument("--tether-radius", type=float, default=0.0)
        p.add_argument(
            "--hud",
            default="shared",
            choices=[m.value for m in HudMode],
            help="shared: leave the game's HUD alone (default) | per-view: one per player | hidden",
        )
        p.add_argument("--screen", default=None, help="the game's HUD space, e.g. 640x448 (pal, 640x512)")
        p.add_argument("--no-cameras", action="store_true", help="do not point each viewport's camera at its own hero")
        p.add_argument(
            "--no-viewports",
            action="store_true",
            help="do not drive the viewport rectangles even if they are measured",
        )
        p.add_argument(
            "--scan-range",
            action="append",
            help="START:END in hex to look for signatures in (faster, and some signatures only exist in a stage)",
        )
        if name == "play":
            p.add_argument("--hz", type=float, default=4.0, help="watchdog passes per second")
            p.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
            p.add_argument("--install", action="store_true", help="install first if nothing is there yet")
        p.set_defaults(func=fn)

    p = sub.add_parser("status", help="print what the mod would do right now")
    p.add_argument("--p1", default="kei")
    p.add_argument("--p2", default=None)
    p.add_argument("--split", default="vertical", choices=[s.value for s in Split])
    p.add_argument("--hud", default="shared", choices=[m.value for m in HudMode])
    p.add_argument("--screen", default=None)
    p.add_argument("--no-cameras", action="store_true")
    p.add_argument("--cave", default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("eject", help="restore the game call and zero the cave")
    p.add_argument("--site", required=True)
    p.add_argument("--cave", required=True)
    p.set_defaults(func=cmd_eject)

    p = sub.add_parser("revert", help="restore every remembered write in a journal")
    p.add_argument("--journal", default=str(JOURNAL))
    p.set_defaults(func=cmd_revert)

    p = sub.add_parser("export-pnach", help="write a standalone PCSX2 patch carrying the cave")
    p.add_argument("--out", default=None)
    p.add_argument("--site", default=None)
    p.add_argument("--cave", default=None)
    p.add_argument("--p1", default="kei")
    p.add_argument("--p2", default=None)
    p.add_argument("--split", default="vertical", choices=[s.value for s in Split])
    p.add_argument("--hud", default="shared", choices=[m.value for m in HudMode])
    p.add_argument("--screen", default=None)
    p.add_argument("--no-cameras", action="store_true")
    p.add_argument("--no-viewports", action="store_true")
    p.add_argument("--scan-range", action="append", help="START:END in hex to look for signatures in; repeatable")
    p.set_defaults(func=cmd_export_pnach)

    p = sub.add_parser("set", help="record a measurement in the address worksheet")
    p.add_argument("assignments", nargs="+", metavar="PATH=VALUE")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("selfcheck", help="offline checks: encodings, cave semantics, protocol")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_selfcheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except PineNotRunning as exc:
        return _fail(str(exc))
    except PineUnusable as exc:
        return _fail(f"{exc} -- load into a running stage and try again")
    except TableError as exc:
        return _fail(str(exc))
    except PineError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
