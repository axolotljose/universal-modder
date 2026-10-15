# MODLOG -- Ape Escape 3 split-screen co-op, as either Kei or Yumi

The journal kept while building this, in order, including the dead ends. It
became the field note in `knowledge/games/ape-escape-3/`.

## The idea

"A mod for Ape Escape 3 that adds co-op with split screen, but we can play as
either Kei or Yumi." Story mode is single player and you are locked to one hero
per save; the multiplayer is a minigame menu. So the mod has to do three things
the game will not: a second hero in the stage, a second viewport, and per-player
character choice.

## Recon

- **The game.** PS2, 2005/2006, SCE Japan Studio. NTSC-U serial `SCUS-97501`,
  `Saru Get You 3` in Japan and PAL, where the heroes are Satoru and Sayaka --
  the American release is the one that renamed them Kei and Yumi. No online mode,
  no anti-cheat, nothing to stay clear of.
- **The engine.** Proprietary, no decompilation exists, and no recompilation
  either. So it is an emulator mod: the game stays the game, and the mod lives on
  both sides of the PCSX2 boundary.
- **Prior art: none.** `um kb search "ape escape"` came back empty, and there is
  no AE3 modding community to lean on -- no loader, no level editor, no tooling.
  The Ape Escape games are famous for being loved and unmodded. That makes this
  the expensive kind of route: everything is built from scratch.
- **Route: `emulator`.** PCSX2's PINE IPC gives read/write to EE memory and a
  running game. Chosen over a static recompilation (PS2Recomp is experimental and
  would be months) and over a savestate hack (no code, so no second actor and no
  second viewport).

## Reading the thing we have to talk to

PINE first, before anything else, because every later decision depends on what it
can actually do. The wire format comes out of `pcsx2/PINE.cpp`:

- request: `u32` total length, then commands; reply: `u32` size, `u8` result, then
  the payload;
- a request may batch thousands of commands, and the reply concatenates the
  results in order -- that is what makes scanning 32 MB feasible;
- ceilings: 650000 bytes of request, 450000 of reply. Exceed either and you get a
  bare `0xFF` with no explanation;
- one client at a time. PCSX2 accepts a second connection into the backlog and then
  never reads from it, so a second client *times out* rather than being told;
- `0xFF` resets the server's receive state: reconnect, do not try to resync.

That last pair of facts cost real time later, and both are now pinned by tests.

## The design decision that made the mod possible

The mod's real problem is not reading memory, it is *timing*. Everything it wants
to change, the engine recomputes every frame. A Python watchdog writing a value
every millisecond still loses: a PINE round trip is ~1 ms, a frame is 16.7 ms, and
the engine overwrites those words in the middle of its own frame.

So: **stop writing from outside.** Install a small interpreter in unused RAM,
redirect one `jal` in the game's per-frame path into it, and let the *game* apply
the mod's state at frame rate. Python becomes policy; the game becomes the effect.

The replacement is one instruction:

```
site    : jal update   ->   jal cave
site+4  : delay slot        untouched -- it belongs to the new jal too
site+8  : the game carries on
```

and the cave ends by calling the function it displaced and returning. Semantics
are identical to the original call, which is the entire point.

Two things came out of that decision that were not planned and are now the
mod's best features:

1. **The parameter block is (address, value) pairs**, so the cave is a
   data-driven coprocessor: new behaviour is a new list, not new machine code.
2. **Bit 31 of a value word means "read from this address"** -- an indirect
   entry. That one flag makes the tether run *inside* the game, in the same frame
   as player one's movement, instead of lagging a watchdog period behind. It also
   means a frozen `.pnach` can carry the tether, since it no longer needs a host.

## Building it

Order of work, and why:

1. `pine.py` -- the transport, with batching. Tested against a fake server written
   from the C++, not from the client, so the two disagree when the client is wrong.
2. `mips.py` -- an encoder, plus a small disassembler for looking at found code.
   Every encoding is checked against a hand-computed word in the tests: an
   assembler cannot be its own oracle.
3. `cave.py` -- the interpreter itself.
4. `r5900vm.py` -- an R5900 subset interpreter, **so the cave could be executed**
   before it ever met a PlayStation 2. This is the single most valuable thing in
   the project. It turned "the assembly looks right" into "the assembly provably
   writes every entry, calls the displaced function exactly once, leaves `$ra`
   holding `site+8` and puts `$sp` back".
5. `discover.py` -- the liveness oracle: install the cave on a candidate, count
   ticks against wall time, compare to a frame rate. This is what makes address
   discovery automatic instead of a guessing game, and it is the reason someone
   else can run this mod on a disc we have never seen.
6. `patcher.py`, `scan.py`, `addresses.py` -- verified, journalled, reversible
   changes, and a worksheet that refuses to guess.
7. `coop.py` -- the actual mod: two viewports, two actors, character ids, pad
   ports, the tether, the watchdog.
8. `pnach.py` -- freeze a working configuration into the format PS2 mods ship in.
9. `tests/gamesim.py` -- a fake console: fake PCSX2 + R5900 interpreter + a
   stand-in game that rewrites its own frame state every frame, exactly like a
   real engine. The whole mod runs end to end against it.

## Bugs found by the tests (each one would have been a crash or a silent nothing)

1. **`verify()` trusted a patch with no `expect`.** A no-expect patch matched
   anything, so it was silently written blind. Now it is reported as a problem.
2. **The wildcard mask was built per hex character instead of per byte**, giving
   twice as many mask bytes as data bytes and making every comparison fail. Now
   it is nibble-aware, so `aab?ccdd` works.
3. **The watchdog reset the cave's tick counter every pass** by rewriting the whole
   parameter block. The tick is the proof of liveness -- erasing it every pass made
   a perfectly healthy hook report a permanent stall. Only the entry list is
   rewritten now, and there is a test that asserts it.
4. **`pointer_chain` was ignored for pinned addresses**, so a chain only ever
   resolved from a signature.
5. **The trampoline was a `j`, not a `jal`.** With `j`, `$ra` at `site+8` is not what
   the displaced call would have left. Caught by a test that compares `$ra` against
   `site+8` in the simulator.
6. **`find_counters` rebuilt a dict inside a per-address loop** -- O(n²) over 32 MB,
   minutes instead of seconds. Found when a test hung.
7. **`--slot` was parsed as a string**, so a port number was treated as a Unix
   socket path and the client reported "No such file or directory" instead of
   connecting.
8. **Data-less patches wrote bytes, not words.** `"deadbeef"` lands as `de ad be ef`.
   Correct, but the trap it sets is worth documenting: `Patch.words()` now exists
   so a code patch cannot be written backwards by hand.
9. **A fake server's `close()` did not wake a thread blocked in `accept()`**, so the
   listener stayed open and a test connected to a socket that should have been
   gone. `shutdown()` before `close()` -- which is what PCSX2's own
   `Deinitialize()` does, for the same reason.

## What is not done

**The addresses.** Every field the mod needs is in
`aecoops/data/addresses.json`, marked `NOT FOUND YET`, with a recipe in
`docs/DISCOVERY.md`. They are properties of a specific pressing of the game, not of
the mod, and no agent can measure them without the disc in a running emulator.
The tooling to measure them is complete and tested; the measuring is the user's
step, and the mod says so rather than guessing values that would corrupt a stage.

Also unverified: whether the game's own two-player renderer is happy being pointed
at a story stage. The design assumes the minigames and the story stages share the
renderer -- which is why forcing the view mode is worth trying at all -- but that
assumption has not been tested on hardware either.

## Next steps

1. Run `docs/DISCOVERY.md` §0-§2 on a real disc and fill in the worksheet.
2. If `view_mode` refuses to hold, try the second theory: that the split is not one
   value but a pair (viewport count and layout), and that the story stage's camera
   code needs the same actor list the minigames give it.
3. Measure the tether radius properly. Two viewports of one stage let the heroes
   walk into different load zones; a distance clamp is the cheap fix and it is
   already implemented, just not tuned.
4. HUD: the story HUD is drawn once, centred. With two viewports it belongs to
   neither player. The minigames must draw their own per-view HUD somewhere.
