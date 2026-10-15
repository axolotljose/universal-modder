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

- **Two viewports.** The stage is drawn twice, once per hero, and each half's
  camera follows its own player.
- **Either hero for either player.** `--p1 yumi --p2 kei` works, and always did
  in the engine's head; the mod just tells it so.
- **Swap mid-stage.** Two writes, on the next frame.
- **A HUD per player, or none.** `--hud shared` leaves the game's HUD alone;
  `--hud per-view` gives each half its own HUD, anchored inside its own view and
  owned by that player; `--hud hidden` draws none. Story mode's single HUD is
  placed for a single screen, so a split needs an answer either way.
- **Tether.** Until the second hero can stand up on its own, it mirrors player
  one so player two is never pressing buttons at a statue.
- **One shared quota.** Either hero's captures count toward the stage total.
- **Ships as a PCSX2 patch.** Once configured, it exports to a `.pnach`, so co-op
  starts with the game and nothing else has to run.

Works on the PS2 original, played on PCSX2 on Windows, Linux or from WSL. You
need the game and an emulator; nothing from the disc ships here.

### Status, honestly

The mod's code is finished and tested -- 209 tests, including the machine code
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
discovery and for changing your mind.

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
| `globals.hud_instances`, `structs.hud.*` | how many HUDs the renderer builds, and where each one sits (optional: without them the HUD is left alone) |
| `actor_count`, `partner_enabled`, `quota_shared` | the flags the stage logic keeps rewriting |

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

The cave is a ~40 instruction interpreter, and that is the whole in-game half:

```
  save $ra
  tick a counter                    <- this is the mod's liveness oracle
  for each (address, value) in the block:
      if value has bit 31 set:  read the word at value, store it
      else:                     store the constant
  jal the function we displaced     <- the game's own code, still called
  restore $ra
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
- **The cave preserves the game's own contract.** It clobbers only `$at`, `$t0-$t5`
  and two words of stack; `$sp`, `$ra`, `$a0-$a3`, `$s*`, `$gp` and `$fp` come out
  exactly as they went in. There is a test for each of those.
- **Uninstall is a read.** The cave's last call is the function it displaced, so
  `eject` recovers the original instruction from the cave itself instead of
  trusting a file.
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
   block being a no-op, the budget honoured, indirect entries copying.
2. **A fake console** (`tests/gamesim.py`): a fake PCSX2 speaking real PINE over a
   real socket, joined to the interpreter, with a stand-in game that **rewrites
   its own frame state every frame** -- actor table, view mode, HUD instances, the
   lot. The full path -- install, watchdog, character assignment, tether, split
   mode, per-view HUD, camera targets, eject -- runs against it.

```bash
python -m unittest discover -s tests -t .   # 209 tests, ~23 s, no emulator needed
python -m aecoops selfcheck                 # 79 checks, one command, no emulator
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
  coop.py       the mod: install, the watchdog, characters, split, tether, HUD
  pnach.py      export to PCSX2's own patch format
  elf.py        ELF32 symbols / Ghidra function maps, for candidate lists
  cli.py        `aecoops ...`
  data/addresses.json   the worksheet: every field the mod needs, and its recipe
docs/DISCOVERY.md   how to find each address, with the failure table
MODLOG.md           the journal kept while building this
patches/            exported `.pnach` files, and what goes in them
tests/              209 tests: protocol, encodings, cave semantics, HUD, end to end
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
