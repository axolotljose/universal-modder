# Ape Escape 3: split-screen co-op, as either Kei or Yumi

Story mode is one player, one hero -- you pick Kei or Yumi when you start and the
other one does not exist. This mod puts **both of them in the stage at once**, on
one screen split down the middle, with pad 1 on one hero and pad 2 on the other:

```
   +----------------+----------------+
   |                |                |
   |   P1  (Kei)    |   P2  (Yumi)   |     either player can be either hero,
   |                |                |     and they can trade places mid-stage
   +----------------+----------------+
```

- **Two viewports, two ways.** The split comes from the view mode the renderer
  reads, and -- where the rectangle a view is drawn into has been measured -- from
  the rectangles themselves. Either lever is enough; both run at once, because the
  mode says *how many* and the rectangles say *where*. Each half's camera follows
  its own player.
- **Either hero for either player.** `--p1 yumi --p2 kei` works, and always did
  in the engine's head; the mod just tells it so.
- **Swap mid-stage.** Two writes, on the next frame.
- **A HUD per player, or none.** `--hud shared` leaves the game's HUD alone;
  `--hud per-view` gives each half its own HUD, anchored inside its own view and
  owned by that player; `--hud hidden` draws none. Story mode's single HUD is
  placed for a single screen, so a split needs an answer either way.
- **Tether.** Until the second hero can stand up on its own, it mirrors player
  one so player two is never pressing buttons at a statue; afterwards
  `--tether-radius N` keeps the two within N world units, so a split cannot walk
  itself into two different loading zones.
- **Calls, for what a write cannot do.** The cave can also `jal` the game's own
  functions -- one argument in `$a0`, read from memory if you want -- which is how a
  second actor gets *constructed* rather than merely described, and how a renderer
  that recomputes its layout from something else gets told to rebuild. One-shot by
  default: a call re-applied every frame is sixty partners a second.
- **One shared quota.** Either hero's captures count toward the stage total.
- **Ships as a PCSX2 patch.** Once configured, it exports to a `.pnach`, so co-op
  starts with the game and nothing else has to run.

Works on the PS2 original, played on PCSX2 on Windows, Linux or from WSL. You
need the game and an emulator; nothing from the disc ships here.

### Status, honestly

The mod's code is finished and tested -- 293 tests, including the machine code
running in a bundled R5900 interpreter and the whole mod running end to end
against a simulated console that rewrites its own state every frame, the way the
real engine does.

What is not done is **the address discovery**, and it cannot be done from here: the
numbers that name the frame hook, the actor table, the character ids, the split
view mode and the HUD are properties of *your* pressing of the game.
`data/addresses.json` is a worksheet with every field the mod needs, a recipe for
each, and `NOT FOUND YET` where nobody has measured it -- and nobody has, yet, on
hardware. The tooling to measure them is complete and tested; `docs/DISCOVERY.md`
is the procedure, and it should take an evening.

One thing that changed while writing this: **no Ape Escape 3 minigame is actually
split-screen.** The two-player minigames are shared-screen, so the "borrow the
minigame's two viewports" plan had less to borrow than it looked. What the story
renderer demonstrably can do is draw a second view of the world into a rectangle
-- that is the MonkeyCam in the bottom-right, which L2 enlarges -- so that is
where the split comes from now. The HUD half of the borrow is intact: the
minigames still draw one HUD per player, and that is the shape this mod
imitates. Both corrections are in the field note and in section 5 of the
discovery procedure.

---

## Quick start

[`docs/GETTING-IT-WORKING.md`](docs/GETTING-IT-WORKING.md) is the same thing as one
session, in order, with what you should see at each step and what to send back if it
fights you. The short version:

```bash
cd examples/ape-escape-3-coop

# 1. is the emulator reachable, and what does the address worksheet still need?
python -m aecoops doctor

# 2. find the per-frame hook in YOUR copy of the game, and prove it
python -m aecoops discover sweep --from-elf /path/to/AE3.elf
#    (no symbols on a retail disc? export a function map from Ghidra and use --from-csv)

# 3. record what you found, then see what is still missing
python -m aecoops set characters.kei=0 characters.yumi=1
python -m aecoops status

# 4. install and play (add --hud per-view once the HUD is measured)
python -m aecoops install
python -m aecoops play --p1 kei --p2 yumi

# if the split does not appear, measure the viewport rectangles (section 8 of the
# discovery page) -- the mod drives those too, and `--no-viewports` turns it off
python -m aecoops discover diff cam_small.json cam_big.json --max-value 700

# and when a diff leaves you twenty candidates, walk them instead of typing twenty
# commands: one poke each, one question each, nothing left written (`q` stops)
python -m aecoops discover try --from-diff story.json minigame.json --max-value 64

# and if the renderer rebuilds its views from something else entirely, the route
# left is to call the game's own setter instead of writing to the word (section 10)
python -m aecoops discover poke --call 0x00123456=1 --duration 4

# keep the two heroes from wandering into different load zones
python -m aecoops play --p1 kei --p2 yumi --tether-radius 200

# stuck? one small text file with everything a helper needs (addresses only, no game data)
python -m aecoops share --extra sweep.txt --out bundle.txt

# and when someone hands you a batch of measurements, apply it in one command
python -m aecoops set --file measurements.txt
```

Ctrl-C stops the watchdog. `python -m aecoops eject --site 0x... --cave 0x...`
puts the game's own code back -- it does not need to remember anything, because
the cave still contains the call it displaced.

### Once it works

```bash
python -m aecoops export-pnach --out cheats/SCUS-97501.pnach
```

Copy that into PCSX2's `cheats/` folder. It carries the cave, the parameter block
and the trampoline, so co-op loads with the game and the Python side is only for
discovery and for changing your mind. A frozen patch has no watchdog, so a call in
one fires exactly once -- at the first frame of the first stage, which is the shape
"construct the partner when the stage starts" wants anyway.

---

## What it needs from your disc

The mod's code is complete; the addresses are not, because they are properties of
a specific pressing of the game rather than of the mod. `aecoops doctor` prints
`NOT FOUND YET` for each one, and
[`docs/DISCOVERY.md`](docs/DISCOVERY.md) has the recipe:

| Field | What it is |
|---|---|
| `frame_hook` | one `jal` a running stage executes once per frame |
| `structs.actor.*` | the two player-actor structures, their stride, and the offsets of `active`, `char_id`, `pad_port`, `pos_x/y/z`, `yaw` |
| `characters.kei`, `characters.yumi` | the two hero ids |
| `layouts.*` | the view-mode values for one viewport, a vertical split, a horizontal split |
| `structs.camera.*` | the two stage cameras and which actor each follows |
| `structs.viewport.*` | the rectangles the two views are drawn into -- the split's second lever, found by watching the MonkeyCam move |
| `globals.hud_instances`, `structs.hud.*` | how many HUDs the renderer builds, and where each one sits (optional: without them the HUD is left alone) |
| `actor_count`, `partner_enabled`, `quota_shared` | the flags the stage logic keeps rewriting |
| `calls.*` | functions of the game's own the cave may call, with the argument to call them with -- how a *write* becomes a *construction*. Optional, and refused unless both halves are measured |

The harness does the hard part for you. **Liveness is measured, not guessed:**
candidate after candidate is redirected into the cave, and the tool counts how
often the game calls it. A function that is not called once per frame is rejected
with the rate it *is* called at:

```
   59.94 Hz  0x00123450  per frame
   83.10 Hz  0x00150120  update_2d: called at 83.1 Hz, not frame rate: it is a
                         render/menu/conditional path
    0.00 Hz  0x0017abc0  never called: this code path is not resident in this scene
```

---

## How it works

### The problem

Everything the mod wants to change, the engine recomputes every frame. Write the
view mode once and the camera code overwrites it before you see it. Write the
second actor's character id once and the stage loader overwrites it. A watchdog
process that writes the values every millisecond nags the game from outside and
still loses the race: a PINE round trip is a millisecond, a frame is 16.7 ms, and
the engine changes those words in the middle of its frame, not at the end.

### The shape of the fix

One code cave, installed into RAM the game never touches, reached by redirecting a
single `jal`:

```
   site      : jal update   ->   jal cave          (one instruction, same slot)
   site+4    : delay slot         untouched
   site+8    : the game carries on exactly as before
```

The cave is a 77-instruction interpreter -- 308 of the 448 bytes reserved for it --
and that is the whole in-game half:

```
  save $ra, $a0-$a3, $sp            <- the game's own arguments must survive us
  tick a counter                    <- this is the mod's liveness oracle
  for each (address, value) in the write list:
      if value has bit 31 set:  read the word at value, store it
      else:                     store the constant
  for each (function, argument) in the call list:
      skip empty slots; zero the slot as we take it  <- a call is made once
      if argument has bit 31 set:  call it with the word at argument
      else:                        call it with the constant
  jal the function we displaced     <- the game's own code, still called
  restore $ra, $a0-$a3, $sp
  j site+8
```

The game executes it every frame, forever, with no host process involved. Python
only refills the parameter block. **Policy in Python, effect in-game** -- and
because the loop is data-driven, new behaviour is a new list, not new machine code.

The entry format has one clever bit: a value word with bit 31 set means "read the
word at this address and store that instead". That one flag is what makes the
tether free -- player two's position is copied from player one's *inside the game*,
in the same frame, with no host round trip and therefore no lag.

### Why this is safe

- **Nothing is guessed.** Every patch verifies the bytes it expects to find first.
  A mismatch aborts the set *before* the first write, so a different pressing of
  the game gets an error rather than a corrupted one.
- **Every write is journalled** to `coop-journal.json` before it happens, with the
  original bytes, so `aecoops revert` undoes an install even after a crash.
- **Free RAM is sampled, not assumed.** `find_free_region` proposes a run of zero
  bytes; `verify_stable` then samples it over a second and refuses to use it if
  anything writes there.
- **The cave preserves the game's own contract.** It clobbers only `$at`, `$t0-$t6`
  and nine words of stack; `$sp`, `$ra`, `$a0-$a3`, `$s*`, `$gp` and `$fp` come out
  exactly as they went in -- including when the mod makes calls of its own, which is
  the case that actually tempts a shortcut. There is a test for each of those.
- **Uninstall is a read.** The cave's last call is the function it displaced, so
  `eject` recovers the original instruction from the cave itself instead of
  trusting a file.
- **The split has two levers and both refuse to guess.** The view mode is one
  word; the viewport rectangles are four per view. Either can be unmeasured, and
  a half-measured rectangle is refused outright rather than written with three
  sides -- an x/y move with the old w/h draws the old view somewhere strange, which
  looks like a broken renderer rather than a missing number.
- **A call is made once, and the cave proves it.** A write is re-applied every
  frame because the engine un-does it every frame; a call is the opposite -- sixty
  calls a second is sixty partners. So the cave *consumes* a call as it takes it
  (zeroes the function word), and the host re-sends only the calls it has not yet
  read back as consumed. A call with an address and no argument is refused outright:
  jumping into code we found while handing it a value we invented is how a stage
  crashes.
- **The HUD is never guessed at.** Every HUD write is gated on a measured address,
  and a mode whose addresses are missing degrades to the next one down with a
  reason. `shared` -- write nothing -- is the default, and the only mode that
  cannot surprise you.

---

## Verification

You cannot run PS2 code in CI, so the project brings its own two oracles:

1. **An R5900 interpreter** (`aecoops/r5900vm.py`) executes the cave for real:
   register file, memory, branch delay slots. The tests assert what the mod
   promises -- every entry applied, the displaced function called exactly once,
   `$ra` holding what the original call would have left, `$sp` restored, an empty
   block being a no-op, the budget honoured, indirect entries copying, and a queued
   call made once with its argument in `$a0` and then consumed.
2. **A fake console** (`tests/gamesim.py`): a fake PCSX2 speaking real PINE over a
   real socket, joined to the interpreter, with a stand-in game that **rewrites
   its own frame state every frame** -- actor table, view mode, HUD instances, the
   lot. The full path -- install, watchdog, character assignment, tether, split
   mode, per-view HUD, camera targets, calls, eject -- runs against it.

```bash
python -m unittest discover -s tests -t .   # 293 tests, ~30 s, no emulator needed
python -m aecoops selfcheck                 # 123 checks, one command, no emulator
```

The HUD tests are the ones worth reading: they assert that the mod's writes land
in the right half *and survive the engine rewriting them*, which is a different
claim from "the mod wrote an address once".

The protocol tests are worth a note: the fake emulator is implemented from
PCSX2's own `PINEServer::ParseCommand`, not from this client, so it disagrees with
the client when the client is wrong. That is how the reply framing, the 450000-byte
reply ceiling and the "PCSX2 serves one client at a time" behaviour got pinned down.

**What none of that proves:** that the addresses you discover are the right ones,
that the story renderer accepts a split view mode at all, or that the story HUD
even *has* an instance count and movable anchors. Only the game can answer those,
and the HUD question is the one the mod degrades most gracefully from: an
unmeasured HUD is not written to, and `status` says which field is missing
instead of writing a plausible number into a word that turns out to be something
else.

If you get it running -- or get stuck at a particular step -- the field note is
where that belongs: `knowledge/games/ape-escape-3/coop-split-screen-kei-yumi.md`,
whose `Open questions` section lists exactly what is still unknown.

---

## Repository layout

```
aecoops/
  pine.py       PINE protocol client: batching, framing, reconnection
  mips.py       MIPS III / R5900 encoder and a small disassembler
  cave.py       the interpreter: assembly, parameter block, free-RAM search
  r5900vm.py    an R5900 subset interpreter, used as the test oracle
  discover.py   frame counters, snapshot differencing, the liveness oracle, poke
  scan.py       RAM signature scanning and address resolution
  addresses.py  the address worksheet (fields, offsets, recipes, verification)
  patcher.py    verified, journalled, reversible memory patches
  hud.py        where the HUD goes on a split screen, and what it refuses to guess
  views.py      the rectangles the two views are drawn into -- the split's other lever
  coop.py       the mod: install, the watchdog, characters, split, tether, HUD, viewports
  pnach.py      export to PCSX2's own patch format
  elf.py        ELF32 symbols / Ghidra function maps, for candidate lists
  cli.py        `aecoops ...`
  data/addresses.json   the worksheet: every field the mod needs, and its recipe
docs/DISCOVERY.md   how to find each address, with the failure table
MODLOG.md           the journal kept while building this
patches/            exported `.pnach` files, and what goes in them
tests/              293 tests: protocol, encodings, cave semantics, HUD, calls, docs
```

---

## Credits and rules

- PINE is PCSX2's IPC protocol; the wire format here is implemented from
  [PCSX2's `PINE.cpp`](https://github.com/PCSX2/pcsx2). All credit for it goes
  there.
- No game files, no extracted assets, no decompiled code are shipped. Addresses
  and byte patterns are facts about a disc, and they never leave your machine.
- Single-player and offline only. Ape Escape 3 has no online mode; nothing here
  touches DRM, ownership checks or anti-cheat, and nothing here would be useful as
  a multiplayer cheat.
- Built by an AI agent, from the method in this repository. Every claim about
  what was verified is in the field note --
  [`knowledge/games/ape-escape-3/coop-split-screen-kei-yumi.md`](../../knowledge/games/ape-escape-3/coop-split-screen-kei-yumi.md).
