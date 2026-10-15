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

## Second pass: the HUD, and a correction about the minigames

The HUD was next because a split screen has two players looking at one picture,
and the game's single HUD is placed for one of them. Digging into it produced a
correction worth recording before the fix.

**No AE3 minigame is split-screen.** The two-player minigames are shared-screen:
Monkey Soccer, the two-player ping pong and boxing modes, and `Ultim-ape Fighter!`
each draw one picture for both players. The design had been leaning on "the
minigame renderer already knows how to draw two views of one stage" -- and that
may be a thing that does not exist in this game. What the *story* renderer
demonstrably does is draw a second view of the world into a rectangle: the
MonkeyCam, the picture-in-picture in the bottom-right corner of a normal stage,
which L2 enlarges and L2 restores, and the L2 first-person zoom. So the evidence
for "this engine can draw two views" is in story mode itself, and the procedure
now says to find the split value by comparing a stage against an enlarged
MonkeyCam rather than against a minigame.

The same dig produced three tools, because the recipes kept asking for things the
toolkit could not do:

- `discover snapshot` / `discover diff` -- the two states worth comparing are
  minutes and a menu apart, so they are files now, and `diff` can be narrowed to
  "words that differ and are small integers", which is the question that actually
  finds a view mode.
- `discover watch` (existed as a function, had no command).
- `discover poke` -- writes candidate values every frame for a few seconds, then
  puts everything back. "Which word moves the HUD?" cannot be answered by reading,
  and a one-shot write proves nothing because the engine rewrites its frame state
  mid-frame. It refuses to run over an installed cave and restores in a `finally`,
  so it is a safe loop rather than a way to lose a save.

**The HUD policy itself** is in `aecoops/hud.py`, and its whole design is
refusal. Three modes: `shared` (write nothing, the default), `per-view` (two
instances, each visible, each anchored inside its own view, each owned by a pad)
and `hidden` (switch the instances off). Every write is gated on an address the
user measured, and a mode whose addresses are missing falls back to the one below
it *with a reason*, which `install` and `status` both print. The geometry is the
only arithmetic: two rectangles that tile the frame exactly -- odd widths
included, so the halves of a 641-pixel screen still cover 641 columns -- and an
anchor inside each, from a configurable screen space because HUD coordinates are
the engine's, not pixels.

Per-view cameras came along for the ride: `structs.camera.target_actor` is written
per view when it is measured, which finally gives the old `camera_follows` option
a meaning (which hero the left half shows). It refuses to write when the camera
stride is 0, because then both "slots" resolve to the same word and the second
view would overwrite the first.

Consequences carried into the code: the parameter block grew from 0x100 to 0x180
bytes (24 -> 40 entries), because story co-op's mandatory writes are about ten and
the optional sets -- tether, HUD, cameras -- can add a dozen; and entries now carry
an `essential` flag so that a fully measured configuration which somehow does not
fit loses its conveniences rather than failing to install.

Bugs the tests caught on this pass:

10. **The counter ranking preferred the wrong counter.** `find_counters` ranked by
    "closest to 60/50 Hz" as a *boolean* and then by the largest raw delta, so a
    jittery timer that happened to advance more in one sample outranked the real
    frame counter. It now ranks by distance in Hz, then by how steady the deltas
    were, then by size. Found by a test that had been flaky for a different
    reason -- the *read window* was large enough that socket timing dominated the
    deltas -- so the test was made to measure a small window, and the ranking bug
    it had been hiding fell out immediately.
11. **Labels drifted out of lockstep with writes.** The HUD policy appended two
    anchors with a single label, so every label after it described the previous
    write. The writes were right and the reporting was a lie, which is the worse
    of the two failures in a tool whose job is to be trustworthy. Appends now go
    through one `emit()` that cannot do one without the other, and a selfcheck
    asserts the two lists are the same length.
12. **A fresh install armed zero writes.** The parameter block was written with an
    empty entry list and only filled in on the first watchdog pass, so the first
    frame after `install` was still single-player. `install` now builds the plan
    first and writes the block with it armed, and reports how many writes it
    armed.
13. **`diff(limit=0)` returned one row instead of all of them.** `0 >= 0` ended the
    loop early, which in a filtering helper reads as "found nothing". `limit=0`
    now means every difference.

## Third pass: the split's second lever, and a leash

The open risk from the first pass was that the split rests on one number -- if the
story renderer ignores `view_mode`, co-op has nothing. It is a real risk, and there
was already an answer sitting in the recon notes: the renderer draws the MonkeyCam
into a rectangle it can move at runtime, so it takes rectangles at runtime, and a
split is two of them. `aecoops/views.py` now drives those directly -- `x`, `y`,
`w`, `h` per view plus the count and which actor each view follows -- under the same
rule as the HUD: every write gated on a measurement, nothing half-written, and a
note rather than a warning when the whole route is unmeasured (because the
value-based route is still in play and a missing backup is not a fault).

Two details worth keeping:

- **The rectangles tile the frame exactly**, odd sizes included. A one-pixel seam
  down the middle of the screen is the most visible possible way to be wrong, and
  it happens for free if you compute the second half as `width / 2` in integers.
- **All four fields are checked before any is written.** The first version checked
  "is the field named in the table", which is true for a field whose offset is
  still `null` -- so a partially measured rectangle got its `x` and `y` written
  before the write to `w` blew up. A test caught it. The same shape as the HUD's
  labels bug: the safe version is to decide everything, then write.

Also finished: **the tether radius**, which had been a config option with no
implementation since the first pass. It is the leash for two viewports of one
stage -- nothing stops the heroes walking into different loading zones -- and it is
deliberately coarse: the host measures the distance once per watchdog pass and
writes constant words, so the correction can be a fraction of a second late. The
alternative was teaching the in-game interpreter float arithmetic and a square
root for a feature that only matters when two players deliberately walk apart,
which is the wrong trade. Direction is preserved (player two is pulled back along
the line to player one, not teleported on top), and it is off by default.

## Fourth pass: calls, because a write cannot construct anything

Everything the mod could do until now was *state*: set `actor_count` to 2, force a
view mode, write a rectangle, place a HUD, copy a position. That runs out exactly at
the two places this mod most needs to be: if the story stage never built a second
actor structure, no amount of writing to it will make one appear, and if the renderer
recomputes its viewports from something else, writing `view_mode` again is not the
answer. Both of those are *doings*, and the only code that can do them is the game's
own. So the cave learned to call.

The interpreter grew a second list: eight bytes per call -- function address, then
argument -- scanned after the write list, from `CALLS_OFFSET` to `CALLS_END`. An
argument with bit 31 set is read at call time, the same spelling the write list
uses, which is what a spawner wants: the handle the stage has just allocated, not
the one it had a quarter of a second ago. `$a0-$a3` are saved in the prologue and
restored before the displaced function runs, because those registers hold *the
game's* arguments to the function we interrupted, and a call of our own is exactly
the thing that would otherwise eat them.

**The once rule is the whole design.** A write must be re-applied every frame,
because the engine un-does it every frame. A call is the opposite: "spawn the
partner" run sixty times a second is sixty partners. So the cave *consumes* a call
as it takes it -- it zeroes the function word -- and the host, in `_settle_calls`,
reads the call region back before each refill and re-sends only the calls it has not
yet seen disappear. That read is the evidence for the line `called spawn_partner`,
and it replaced an earlier, weaker design that used "did the frame counter move" as
proof: the frame counter proves a refill ran, not that a call did, and it cannot
tell "queued, waiting for the game to reach it" from "already made".

Three of the bugs this pass were found by tests written before the code was trusted,
and all three were the same shape -- something that looked like bookkeeping and was
actually the difference between one partner and sixty:

- **A refill wiped a call the game had not reached yet.** The host rewrites the whole
  call region every pass; a one-shot call armed while the game was paused was erased
  by the next refill before a frame could carry it. Fix: the read-back decides, and a
  call that is still in the region is still re-sent.
- **The install itself counted as a firing.** Arming on write meant "armed at install,
  then re-armed on the first tick", so the first call was made twice. Same root cause,
  same fix.
- **The call region started on the entry list's terminator.** `CALLS_OFFSET` was
  `ENTRIES_OFFSET + 8 * MAX_ENTRIES`, which is where a *full* write list (40 entries,
  41 slots with its terminator) puts its terminator -- so a frame that filled the
  write budget silently zeroed the first call slot. Off by one slot, invisible until
  both budgets are full, and now a selfcheck assertion rather than a comment.

And one bug of the opposite kind, in a note: `_note_once` reported a problem once per
watchdog pass instead of once ever, because the condition behind it stays true and the
drain forgot what it had already said. Four identical warnings a second is how the
warnings that are new get buried -- which is the exact thing the function's docstring
promised not to do.

One case cannot be decided at all, only chosen: a host that starts against a cave
another host installed. A one-shot call that is not in the region *now* may have been
made a second ago or never, and there is no reading that tells them apart -- so the
mod assumes it was made and leaves it alone, says so in the install report, and tells
the user to eject and install again if they reloaded a stage. Spawning the partner
twice is a bug a player sees; not spawning it is a reload.

The worksheet gained two calls that are deliberately `NOT FOUND YET`
(`spawn_partner`, and `set_view_mode` as the last-resort route to the split), the
recipe for finding one is `docs/DISCOVERY.md` §10, and `discover poke --call
FUNCTION=ARG` is how you try one out for four seconds and get everything put back.
A call with an address and no argument is refused outright, loudly and once: jumping
into code we found while handing it a value we invented is how a stage crashes.

## What is not done

**The addresses.** Every field the mod needs is in
`aecoops/data/addresses.json`, marked `NOT FOUND YET`, with a recipe in
`docs/DISCOVERY.md`. They are properties of a specific pressing of the game, not of
the mod, and no agent can measure them without the disc in a running emulator.
The tooling to measure them is complete and tested; the measuring is the user's
step, and the mod says so rather than guessing values that would corrupt a stage.

The same goes for the calls: **nothing is called**, because the worksheet has no call
with both an address and an argument. `spawn_partner` is the one that would matter
most -- if forcing `actor_count` is not enough to get a second actor simulated, that
function is what actually constructs it -- and finding it is a Ghidra session on the
stage loader plus one `discover poke --call`, not a guess.

Also unverified: whether the story renderer accepts a split view mode at all, and
whether the story HUD has the fields the per-view policy wants (an instance
count, a stride, movable anchors). Both are answered by the same evening of
measurement, and neither is a reason the mod cannot run in the meantime: `shared`
is the default HUD mode precisely so that an unmeasured disc still gets two
viewports, two heroes and two pads.

## Next steps

1. Run `docs/DISCOVERY.md` §0-§2 on a real disc and fill in the worksheet.
2. If `view_mode` refuses to hold, try the second theory: that the split is not one
   value but a pair (viewport count and layout), and that the story stage's camera
   code needs the same actor list the minigames give it.
3. Measure the tether radius properly. Two viewports of one stage let the heroes
   walk into different load zones; a distance clamp is the cheap fix and it is
   already implemented, just not tuned.
4. HUD: done as policy (§"Second pass"), pending the measurements it is gated on.
   The half that is still open is the *engine's* side: whether the story HUD
   instance table exists, and whether a second instance is a count the renderer
   reads or something the stage code builds. `Ultim-ape Fighter!`'s two stamina
   bars are the pair to measure against, now that the split-screen assumption is
   off the table.
5. The split itself: the view-mode value (§5 of the discovery page), and the
   viewport rectangles as the fallback (§8) -- implemented and tested, waiting on
   the same evening of measurement. The rectangles are the safer bet of the two,
   because they are how the MonkeyCam works and the MonkeyCam demonstrably works.
6. The tether radius wants a number rather than a guess: `--tether-radius 200` is
   a placeholder for a playground-sized stage, and the right value is whatever a
   load-zone boundary turns out to be.
7. The calls (§10): find the stage's own "add the second player" routine, or the
   view-mode setter behind the renderer, and record both address and argument with
   `aecoops set calls.<name>.addr=... calls.<name>.arg=...`. Until then the whole
   feature is inert by construction -- which is the point of it being refused
   rather than guessed.
