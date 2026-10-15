---
kind: game
title: "Split-screen co-op in Ape Escape 3, as either Kei or Yumi"
game: "Ape Escape 3"
games_also: []
game_version: "PS2 NTSC-U, SCUS-97501, on PCSX2 (PINE IPC on)"   # the addresses are per-pressing; see Setup
platform: emulator
engine: unknown
route: emulator
tools: ["PCSX2 (PINE IPC)", "aecoops (this mod's toolkit)", "Ghidra (function maps)", "ELF symtab reader"]
anti_cheat: "none -- PS2 retail disc, no online mode, no checks of any kind"
status: in-progress
agents: ["Claude Code (Opus 4.7)"]
humans: []
date: 2026-10-15
links: ["examples/ape-escape-3-coop"]
tags: ["coop", "split-screen", "code-cave", "mips", "pine", "pcsx2", "emulator", "coprocessor", "address-discovery", "liveness-oracle"]
---

# Split-screen co-op in Ape Escape 3, as either Kei or Yumi

> Story mode is one player and one hero, so this mod puts both heroes in the stage
> at once with the screen split, one pad each, and either player can be Kei or
> Yumi. It is an emulator mod: a 77-instruction interpreter installed into unused
> EE RAM, reached by redirecting one `jal` in the game's per-frame path, which
> re-applies the state the engine keeps overwriting -- and, where state is not
> enough, **calls the game's own functions** (a write can set `actor_count`, it
> cannot construct a second actor). The mod's code is finished and
> tested (276 tests, including the machine code executing in an R5900 interpreter),
> but **nobody has found the addresses on real hardware yet** -- that is the one
> step left, and `docs/DISCOVERY.md` in the example folder is the procedure.
>
> Two facts about this game are worth knowing before believing any plan that says
> "copy what the minigames do": **no AE3 minigame is split-screen** (they are
> shared-screen, one picture, two players), and the story mode's own HUD is placed
> for one screen. What the story renderer *does* have is a second view of the world
> drawn into a rectangle -- the MonkeyCam, bottom-right, which L2 enlarges. That is
> the load-bearing piece of evidence for a second viewport, and it is in the story
> path rather than the minigame path. Details in "How the game works".

## Setup

- Game: Ape Escape 3, PS2, NTSC-U serial `SCUS-97501`. Japan and PAL are `Saru Get
  You 3` with Satoru and Sayaka; the American release renamed them, and nothing
  else in this note depends on which one you have.
- Emulator: PCSX2 with **Settings → Advanced → Enable PINE IPC Server** on. Default
  slot 28011. On Windows PINE is TCP on 127.0.0.1; on Linux/macOS it is an `AF_UNIX`
  socket at `$XDG_RUNTIME_DIR/pcsx2.sock` (`/tmp/pcsx2.sock` if unset, with `.<slot>`
  appended for non-default slots).
- Tooling: `examples/ape-escape-3-coop` in this repository. Python 3.10+, no
  third-party dependencies, no `uv` needed. Optional: a Ghidra export of the
  game's function map, or an unstripped ELF, to seed the hook-candidate list.
- Optional for shipping: the exported `.pnach` goes in PCSX2's `cheats/` folder with
  **Settings → Emulation → Enable Cheats** on.

This is an emulator mod, so there is no loader to install and no game folder to
touch. Nothing is written to disk except this mod's own journal and your own
address worksheet.

## Route and why

`emulator`. The alternatives, and why not:

- **Static recompilation / decompilation.** No decomp exists for any Ape Escape
  game, and one does not exist for *any* PS2 retail title in the sense an N64 or
  Xbox 360 title does. PS2Recomp is experimental and a full game is months of work
  before the first frame draws. Too expensive for "add a second hero".
- **Data / asset modding.** Story-mode co-op is not in the data. There is no level
  format to edit and no script layer to extend.
- **Consoles with a loader.** Nothing exists. There is no Ape Escape modding
  community: no loader, no editor, no tooling, and `um kb search "ape escape"` finds
  nothing. This is the expensive kind of route -- everything is built from nothing.
- **Emulator memory scripting (chosen).** PINE gives read/write to EE RAM on a
  running game from any language. The catch is that a host-side writer loses every
  race (a PINE round trip is ~1 ms, a frame is 16.7 ms, and the engine rewrites its
  frame state mid-frame) -- which is what shaped the whole design: keep the *policy*
  outside and put the *effect* inside.

## How the game works (what we had to learn)

Everything here is about the *mechanism*; the game's own numbers are the user's to
measure, and none are recorded in this note.

**The engine rewrites its own frame state every frame.** Actor count, view mode and
the partner flag are recomputed by the stage logic; a character id is rewritten by
the save-slot loader. Writing any of them once from outside does nothing observable.
This is the central fact and the reason the mod cannot be a script.

**The engine already contains everything co-op needs -- but not in the shape the
first pass assumed.** AE3's multiplayer minigames do put two heroes in one stage
with two pads, so the actor structures, animations and pad handling exist twice
over in the shipped binary; the story stage *selects* behaviour with a view mode
rather than being a different program. **The one thing the minigames do not
provide is two viewports:** they are shared-screen (Monkey Soccer, `Ultim-ape
Fighter!`, the two-player ping pong and boxing modes: one picture, both players on
it). So "the minigame renderer already draws two views of one stage" was a
plausible theory with no evidence behind it, and the corrected evidence is in
story mode itself:

- the **MonkeyCam** -- a live picture-in-picture of a nearby monkey, drawn at the
  bottom-right of an ordinary stage, **enlarged with L2 and restored with L2**. That
  is the engine rendering a second *view of the world* into a rectangle whose
  position and size it can change at runtime. For a split screen, the rectangle is
  the whole problem, and it is also the mod's second lever: the split is applied by
  forcing the view mode *and* by writing each view's rectangle, and a renderer that
  respects either one is enough. It is also the safest thing to go and measure
  first, because moving a rectangle is something this engine is *known* to do;
- the **L2 first-person zoom**, a second camera mode in the story path;
- and on the HUD side, the minigames *do* have the reference implementation: one
  HUD instance per player, each anchored to its own side, each reading that
  player's own state. That half of the borrow survived.

**The HUD is per-instance, and the minigames prove it.** A story HUD is global
candy (jackets, coins, remaining monkeys) placed once for one screen; a two-player
minigame's HUD is one instance per player, which is why `Ultim-ape Fighter!`'s two
stamina bars are the pair to measure against. That gives the mod its HUD policy:
tell the renderer there are two instances, mark both visible, anchor each inside
its own view, and point each at its own pad where the engine has such a field --
and write none of it until the user has measured the addresses.

**A `jal` in a MIPS binary is one instruction, and the delay slot belongs to
whoever follows.** Replacing `jal F` with `jal CAVE` keeps the very next
instruction (the delay slot) semantically attached to the call, and the cave can
call `F` itself and then `j site+8`. The interrupted function sees exactly what it
always saw. This is the whole trick, and it is why a per-frame hook needs no
trampoline larger than four bytes.

**A `jal` encodes only 26 of a target's 28 bits.** The top four come from `PC+4`.
A decoder that forgets this resolves the wrong address; an encoder that tries to
encode all 28 bits cannot. Both are one line of arithmetic and one unit test.

**EE memory, as the game sees it.** Main RAM is 32 MB from `0x00100000`; the
scratchpad is 16 KiB at `0x70000000`; there is a large, stable region of
never-written RAM at the top of main RAM in most builds, which is where a code cave
fits. "Never written" needs *sampling* to establish, not one read: a watchdog pass
that owns that memory but has not run yet looks identical to free space.

**PINE's real shape** (from `pcsx2/PINE.cpp`, and worth writing down because none of
it is in the docs):

- request = `u32` total length (counting itself), then commands; reply = `u32` size
  (counting itself), `u8` result (`0x00` ok, `0xFF` fail), then payload;
- one request may batch many commands, and the reply concatenates every command's
  result in order -- 8192 reads in one round trip is normal, and is the only reason
  scanning 32 MB is practical;
- ceilings: 650000 bytes of request, 450000 of reply. Exceed either and the reply is
  a bare `0xFF` with no way to tell which limit you hit;
- **one client at a time.** A second connection is accepted into the backlog and then
  never read from, so a second client times out rather than being refused;
- after a `0xFF` the server resets its receive state, so the client must drop the
  socket and reconnect instead of trying to resync;
- strings (`title`, `id`, `uuid`, `gameversion`, `version`) come back as `u32`
  length plus a NUL-terminated body, and the length **includes** the terminator.

## Build steps

The mod lives in `examples/ape-escape-3-coop`; it is self-contained Python.

```bash
cd examples/ape-escape-3-coop
python -m aecoops selfcheck          # offline: encodings, cave semantics, protocol

python -m aecoops doctor             # PINE alive? which worksheet entries are blank?
python -m aecoops discover counter   # optional sanity: 59.94 Hz or 50 Hz frame counter
python -m aecoops discover sweep --from-elf game.elf        # or --from-csv ghidra.csv
python -m aecoops discover probe --site 0x... --cave 0x...  # confirm one candidate

# compare two game states ("which small integer differs?") -- the tool that finds
# a view mode and a HUD instance count:
python -m aecoops discover snapshot --out stage.json
python -m aecoops discover snapshot --out minigame.json
python -m aecoops discover diff stage.json minigame.json --max-value 64
# and, to test one candidate by eye, the only command here that writes:
python -m aecoops discover poke --site 0x... --set 0x00110010=1 --duration 3
# a diff gives you twenty candidates; walk them: one poke each, one question each
python -m aecoops discover try --from-diff story.json minigame.json --max-value 64
# a candidate that has to be *run* rather than written -- a constructor, a setter:
python -m aecoops discover poke --site 0x... --call 0x00123456=1 --duration 4

# record what you found (see docs/DISCOVERY.md for every field)
python -m aecoops set globals.frame_hook.addr=0x00123456 globals.frame_hook.verified=2026-10-15
python -m aecoops set characters.kei=0 characters.yumi=1
python -m aecoops set structs.actor.stride=0x1f0 structs.actor.fields.char_id.offset=0x38
python -m aecoops set globals.hud_instances.addr=0x1a2b3c4
python -m aecoops set structs.hud.stride=0x40 structs.hud.fields.x.offset=0x04
# optional, and refused unless both halves are measured: a function of the game's
# own the cave may call (bit 31 on `arg` means "read the argument at this address")
python -m aecoops set calls.spawn_partner.addr=0x00160000 calls.spawn_partner.arg=1

python -m aecoops status             # the writes it would make, and what is missing
python -m aecoops install            # cave + parameter block + trampoline, plan armed
python -m aecoops play --p1 kei --p2 yumi --hud per-view
python -m aecoops eject --site 0x... --cave 0x...

# once it works, freeze it
python -m aecoops export-pnach --out cheats/<CRC>.pnach
```

## Verification

**What was verified, and how.**

1. **The protocol.** A fake PCSX2 (`tests/fakepine.py`) implements PINE *from
   PCSX2's C++*, not from this client, and speaks it over a real socket. The client
   is tested against it: framing, every read/write width, batched transfers across
   the 450000-byte ceiling, unaligned writes that must not clobber their neighbours,
   failure-and-reconnect, and the one-client-at-a-time behaviour.
2. **The instruction encodings.** Checked against hand-computed instruction words,
   so the assembler cannot be its own oracle.
3. **The cave's behaviour.** An R5900 subset interpreter (`aecoops/r5900vm.py`)
   executes the actual generated machine code. Asserted: every entry applied, the
   displaced function called exactly once, `$ra` holding what the original call
   would have left (`site+8`), `$sp` restored, `$a0-$a3`/`$s*`/`$gp`/`$fp`
   untouched, an empty block being a genuine no-op, the entry budget honoured, a
   zero terminator stopping the loop, and indirect entries copying live words. For
   calls: the function is entered with its argument in `$a0` (a constant or the word
   at an address), the slot is consumed so it is not called a second time, an empty
   slot is skipped rather than treated as the end of the list, and the displaced
   function still runs afterwards with the game's own `$a0-$a3` intact.
4. **The mod end to end.** A simulated console (`tests/gamesim.py`) joins the fake
   emulator to the interpreter and stands in for the game -- **including rewriting
   its own frame state every frame**, which is the behaviour the whole mod exists to
   fight. Install → watchdog → two viewports → two heroes → character assignment →
   swap → tether → eject all run against it.
5. **The discovery method.** The loop that ends every recipe -- poke a candidate,
   look at the screen, decide -- is tested with the judgement *injected*, so
   everything around the human is machine-checked: the run stops at the first
   candidate that works, a survey keeps going and still nominates the first hit, a
   candidate that cannot be tried costs one candidate rather than the run, and every
   poke puts the instruction back and zeroes the cave before the next one. The
   liveness oracle is tested against a simulated console driven at a known frame
   rate: a per-frame site is accepted, a 20 Hz site
   is rejected with its measured rate, a dead site is reported as never called, and
   a probe always reinstalls what it displaced. The write half (`discover poke`) is
   tested the same way: it lands its writes every frame, restores the instruction
   and zeroes the cave afterwards -- including when the writes are refused mid-way
   -- and refuses to run over an installed cave.
6. **The viewport rectangles.** The same two layers as the HUD: an unmeasured
   viewport table produces no writes and no complaint (the value-based route is
   still in play), a half-measured one -- three sides of a rectangle -- produces no
   writes either rather than a rectangle that is moved but not resized, and a
   measured one produces two halves that tile the frame. Against the simulated
   console the rectangles hold while the stand-in renderer rebuilds its own every
   frame, and `--no-viewports` leaves the renderer's own viewports alone.
7. **The HUD policy.** Two layers. Against a worksheet: an unmeasured disc produces
   *no writes and a reason*; a measured one produces two instances, both visible,
   each anchored inside its own view and owned by its own pad; `hidden` prefers
   switching the instance count off over hiding instances; labels line up with
   writes one for one. Against the simulated console: the writes land in the right
   half **and survive the engine rewriting them every frame**, which is the claim
   that matters and the one a policy test cannot make.

`python -m aecoops selfcheck` runs the same checks on a user's machine in one
command (114 of them, including that the call region does not overlap the write
list and that every `docs/DISCOVERY.md#key` a message prints still resolves to a
section); the suite is 276 tests in about 30 seconds.

**What was NOT verified.** Everything about the actual game:

- the addresses (`frame_hook`, actor table, character ids, view mode) have not been
  measured on hardware by anyone. `data/addresses.json` says `NOT FOUND YET` and
  `docs/DISCOVERY.md` gives the recipe for each;
- whether the story renderer accepts a split view mode at all. It follows from the
  story renderer's own secondary views (the MonkeyCam's rectangle, the L2 zoom)
  that a second view is possible; it does not follow that one word selects it. The
  rectangle route was built for exactly this, and it is still unverified: whether
  the engine obeys rectangles written by someone else, and whether the MonkeyCam's
  rectangle is a slot in a table (two views) or a special case (one);
- whether the second actor's logic will drive itself once `actor_count` is 2, or
  whether a further flag is needed;
- whether the story HUD has the fields the per-view policy wants at all: an
  instance count, a stride between two instances, and anchors that are data rather
  than baked into the draw call. Every one of those writes is gated on a
  measurement, so the honest failure mode is "no second HUD" rather than a
  corrupted one;
- the tether radius: the code is now real (`--tether-radius`, host-measured
  distance, direction-preserving pull-back) but the *number* is a guess until
  somebody watches a load-zone boundary.

## Gotchas

1. **Symptom:** a host-side watchdog writes a value correctly and the game ignores
   it. **Cause:** the engine recomputes that word every frame, mid-frame, faster than
   any IPC round trip. **Fix:** stop writing from outside. Install the interpreter
   inside and let the game apply the values at frame rate; Python only refills the
   parameter block.

2. **Symptom:** a healthy, per-frame hook reports a permanent stall. **Cause:** the
   watchdog was rewriting the *whole* parameter block each pass, including the tick
   counter -- erasing the very number it reads back as proof of life. **Fix:** write
   only the entry list (from `ENTRIES_OFFSET`) every pass; the header belongs to the
   game. There is a test that pins this.

3. **Symptom:** installing works, the game calls the hook, nothing changes.
   **Cause:** the hook is per-frame but only in *some* scenes; the address that
   probes clean on a stage may be dead in a menu or on another stage. **Fix:** the
   liveness oracle measures rate, not existence. Probe in a running stage, and note
   which stage you measured in.

4. **Symptom:** a probe reports a plausible-but-wrong rate (83 Hz, 120 Hz).
   **Cause:** the function is called once per subsystem or once per rendered frame,
   not once per simulation frame. **Fix:** the oracle's threshold is 10% around
   60/50 Hz; anything else is rejected on purpose. Do not lower it to make a
   candidate pass.

5. **Symptom:** a code cave in "empty" RAM breaks the game a few seconds later.
   **Cause:** the region is empty *now* -- a buffer that has not been allocated yet,
   or a scratch area written only during transitions. **Fix:** sample it over at
   least a second (`verify_stable`, 8 samples) before trusting it, and prefer the
   region at the top of main RAM.

6. **Symptom:** PINE reads and writes work, then everything after one call fails.
   **Cause:** a `0xFF` reply. PCSX2 resets its receive state after a failure, so a
   client that keeps the socket desynchronises and misreads every later reply.
   **Fix:** on `0xFF`, close and reconnect. Never try to resync in place.

7. **Symptom:** a second PINE client hangs instead of failing. **Cause:** PCSX2
   serves one client at a time; the second connection sits accepted-but-unread in
   the backlog. **Fix:** one client per emulator. Close before reconnecting, and
   expect a timeout rather than an error.

8. **Symptom:** a large batched read fails with a bare error and no clue.
   **Cause:** the reply would exceed 450000 bytes (650000 for a request). **Fix:**
   cap batches at a few thousand words; this mod uses 8192 reads per request.

9. **Symptom:** the emulator carries on running with the wrong `gameversion` or
   title, or a patch lands in the wrong game entirely. **Cause:** PINE talks to
   *whatever* is loaded, and titles change as the disc boots. **Fix:** always check
   serial + CRC + disc version before writing, and abort if they do not match what
   the patch set was measured against.

10. **Symptom:** a patch that applied cleanly does nothing. **Cause:** patch data is
    *bytes in order*, not words -- `"deadbeef"` lands as `de ad be ef`. **Fix:** use
    `Patch.words()` for instruction patches so the little-endian encoding is done
    for you, and write an `expect` string so a wrong revision aborts before the
    first byte instead of corrupting one.

11. **Symptom:** a wildcard in an `expect` string never matches. **Cause:** the
    wildcard mask was built one byte per hex *character*, giving twice as many mask
    bytes as data bytes. **Fix:** build the mask from hex *pairs*, nibble-aware, so
    `aab?ccdd` masks the low nibble of one byte.

12. **Symptom:** a fake PCSX2's socket stays open after `close()`, and a test
    connects to a listener that should be gone. **Cause:** a thread blocked in
    `accept()` does not wake on a bare `close()`. **Fix:** `shutdown()` then
    `close()`. PCSX2's own `Deinitialize()` does exactly this, for exactly this
    reason.

13. **Symptom:** a full-RAM scan that should take seconds takes minutes. **Cause:**
    a per-address `dict(...)` inside the sampling loop -- quadratic in the number of
    words. **Fix:** build the lookup dicts once, outside the loop.

14. **Symptom:** a savestate load silently undoes the mod. **Cause:** a savestate
    restores RAM wholesale, including the cave and the trampoline. **Fix:** boot
    fresh when using a `.pnach`, or re-apply from the journal
    (`PatchSet.apply_from_journal`) after loading a state.

15. **Symptom:** `--hud per-view` (or any optional feature) runs, reports no error,
    and visibly does nothing. **Cause:** every optional write is gated on an address
    the user measured; an unmeasured feature degrades to the next mode down and only
    says so in a note. **Fix:** `aecoops status` prints the writes it *would* make and
    a `waiting on:` list -- that list is the to-do, and an empty one means every
    measured thing is in use. This is the deliberate trade: a policy that guessed
    would move a panel at best and write a plausible number over the gadget wheel's
    rotation at worst.

16. **Symptom:** a discovery tool reports writes that do not match what it wrote.
    **Cause:** the list of writes and the list of labels were appended by different
    code paths, and one path added two writes per label -- so every label after that
    point described the previous entry. **Fix:** a single `emit(addr, value, label)`
    that cannot do one without the other, plus a selfcheck asserting the two lists
    are the same length. (A tool whose job is to be trustworthy has to be tested for
    *reporting* as hard as for behaviour.)

17. **Symptom:** the split works, then one half stops updating when the other hero
    walks away. **Cause:** two views of one stage can be in two different loading
    zones; the second view has nothing to draw. **Fix:** `--tether-radius N` keeps
    player two within N world units (host-measured, so up to a watchdog period
    late), or accept it and let the second hero wait at the edge.

18. **Symptom:** the partner appears twice -- two of them, or a burst of them -- the
    moment co-op starts. **Cause:** a *call* is not a write. The engine un-does a
    write every frame, so the mod re-applies writes every frame; a call re-applied
    every frame is sixty spawns a second. **Fix:** the cave consumes a call as it
    takes it (zeroes the function word) and the host re-sends only the calls it has
    not read back as consumed. The one-shot flag is the default for exactly this
    reason: "make the game do a thing" and "keep a word at a value" need opposite
    schedules, and the same code path cannot serve both.

19. **Symptom:** a call is made once, and then a full write list appears to erase it
    -- the partner never spawns, with no error. **Cause:** the call region started on
    the entry list's *terminator* slot, so a frame that filled the 40-entry write
    budget zeroed the first call slot. **Fix:** the regions are laid out with explicit
    room for each other's terminator, and `aecoops selfcheck` asserts it -- this is
    the class of bug that is invisible until both lists are full, so it needs an
    assertion rather than a comment.

20. **Symptom:** a value poked once from outside appears to work -- the panel moves,
    the flag flips -- and then reverts a moment later. **Cause:** the engine rewrites
    its frame state mid-frame, so a one-shot write is indistinguishable from a write
    the engine ignored while you were looking. **Fix:** `discover poke` writes every
    frame for a few seconds and then restores. "It responded" and "it kept it" are
    different observations, and only the second one predicts what happens in a stage.

## Assets

None. There is no art, audio or 3D in this mod: it uses the characters the game
already has, which is the point -- the second hero is the one the engine already
knows how to draw.

## Cost and time

One session, then a second one for the two-viewport HUD (which is where the
"minigames are split-screen" assumption died). Most of the time went into things
that are reusable: the R5900 interpreter used as a test oracle for generated
machine code, the liveness oracle that turns "is this function called every frame?"
into a measurement, and the write half of discovery -- snapshot, diff and poke --
which turns "which word does this?" into a five-minute experiment instead of an
afternoon of guessing.

## What is *not* on the internet

Worth recording, because the shortcut looks obvious and is a dead end: the only
published "codes" for this game are in-game **passwords** (content unlocks, not
addresses), **encrypted** CodeBreaker/Action Replay payloads (player-visible state
even when decrypted), and GameShark listings for unlock flags, tied to one pressing
of one region. None of them is a per-frame `jal`, an actor table, a view mode or a
struct offset. Those are properties of a running machine, which is why the project
ships recipes and refuses to guess -- and why the work went into shortening the
measuring (`discover try`) instead of hunting for a number to copy.

## Open questions

1. Does the story stage's view mode *hold* when forced, or does the stage camera
   code reset it mid-frame in a place the hook cannot reach? The fallback is to hook
   the camera code instead of the stage update.
2. Is the split one value or a pair (viewport count + layout)? It does not have to
   be answered before trying the other lever: the viewport rectangles are
   implemented, and `discover diff` across an L2 press is the measurement that
   finds them. Two words change when the MonkeyCam enlarges; find them, then look
   for the same fields at a fixed offset for a second view.
3. Does the second actor need a flag beyond `actor_count` to be simulated? If the
   actor exists but its logic never runs, the tether masks it -- turn the tether off
   to find out.
4. **Which function constructs the second player actor** -- if forcing `actor_count`
   is not enough, that is the call that matters. The route is §10: find the stage
   loader's own "add player two", confirm it with `discover poke --call` while the
   stage is running, then record the address *and* the argument. Nothing is called
   until both are there, which is the design: a call to an address nobody has looked
   at is a jump into code with garbage in a register.
5. Does the story HUD have an instance table at all? The policy's per-view mode
   needs three things from it -- a count, a stride, movable anchors -- and the
   measurement recipe is `Ultim-ape Fighter!`'s two stamina bars. If the answer is
   no, the fallback is `hidden` plus the pause menu, which is a worse answer but an
   honest one, and it is already implemented.
5. Does the HUD's anchor space match the frame? Per-view placement assumes
   `640x448` (or `--screen pal`). It is a flag rather than a constant because the
   first person to measure it will know.
