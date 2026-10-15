# Finding the addresses

> Every `aecoops ...` command below means `python -m aecoops ...` run from
> `examples/ape-escape-3-coop`. If you have put the folder on `PATH`, the short
> form works as written.

The mod is finished code. What it is missing is numbers: a handful of addresses
inside your copy of Ape Escape 3 that nobody can ship, because they belong to the
disc rather than to us, and because two pressings of the same game can differ.

`aecoops doctor` lists every one of them and prints `NOT FOUND YET` for the ones
still unknown. This page is the recipe for each. Do them in order: the first one
unlocks everything else.

Throughout, "the game" means PCSX2 with your own ISO, PINE enabled
(*Settings → Advanced → Enable PINE IPC Server*), and **a stage actually running**:
most of this memory does not exist on the title screen.

```bash
aecoops doctor          # what is missing, and is PINE alive
aecoops info            # serial, CRC -- write these into data/addresses.json's "eras"
```

---

## 0. The frame counter (do this first; it is also a sanity check)

This one finds itself:

```bash
aecoops discover counter --interval 1
```

It samples RAM three times and keeps words that advance by a *nearly constant*
amount. A game's global frame counter ticks once per frame forever, so its rate
lands on 59.94 Hz (NTSC) or 50 Hz (PAL). Anything else is a stage timer, and the
tool says so.

If it finds nothing, you are on a menu, a pause screen or a loading transition.
Get into a stage and try again. If it reports a rate that is not a frame rate,
the game is frame-limited somewhere unusual; try `--interval 2`.

You do not need this address for the mod. It is the fastest way to confirm that
the memory you are looking at belongs to a *running* stage, and it is a useful
anchor for everything below: lots of game state is easiest to recognise by how it
moves relative to the frame counter.

---

## 1. The frame hook

**Why.** One `jal` that a running stage executes exactly once per frame. The mod
overwrites that instruction's target, so its own interpreter runs every frame.

**How.** You cannot tell a per-frame function from a menu function by reading a
disassembly, so the harness measures it. That measurement is the whole method:

```bash
# Candidates from a symbol table, if your executable still has one
aecoops discover sweep --from-elf game.elf --name update

# No symbols? Export a function map from Ghidra (name,address) and use that
aecoops discover sweep --from-csv funcs.csv --limit 400
```

Each candidate is probed: the tool builds the interpreter in free RAM, redirects
the candidate, waits a second, reads back how many times it was called, and puts
everything back. The verdict is one of:

| Verdict | Meaning |
|---|---|
| `per frame` | The rate matches 60/50 Hz within 10%. This is the one you want. |
| `called at 83.1 Hz, not frame rate` | A render path, a menu, or a function called once per subsystem. |
| `never called` | Not resident in this scene, or not on the stage's path at all. |
| `the parameter block was cleared while running` | Something else owns that memory: pick another cave address. |

Then paste the winner into the table and confirm with the real thing:

```bash
aecoops discover probe --site 0x00123456 --cave 0x001f0000
```

Good candidates, if you are choosing by hand from a disassembly: the function that
polls the pads, the one that steps the stage's actor list, or the one at the end
of the stage's own update. Bad candidates: anything called from the drawing code
(too often, and only when a frame is actually presented), and anything the game
only calls while a specific menu is open.

**What can go wrong.** If a probe reports a plausible rate but `install` then
reports `the hook has not run`, you are installing in one scene and measuring in
another: some stages call a *different* update function. The fix is to note the
address that works for the stage you care about, or install while already in that
stage.

---

## 2. The actor table

**Why.** The mod needs to find the two player-actor structures: one to write the
second hero into, and both to write character ids into.

**How.** The actor table is a global pointer or a fixed array, and the cheapest
way to find it is by *difference*:

```bash
# Catch the values that appear when a stage starts and settle when it finishes loading
aecoops discover counter            # (optional) confirm you are in a stage
```

With the game in a stage, use the snapshot differencing helpers from a short
Python session. `aecoops` is a library as well as a CLI, and for this step it is
much faster to drive it directly:

```python
from aecoops.pine import Pine
from aecoops.discover import Snapshot

pine = Pine().connect()
before = Snapshot.take(pine, [(0x00100000, 0x00300000)])   # narrow the range to find things faster
# ... now move the hero in game ...
after = Snapshot.take(pine, [(0x00100000, 0x00300000)])
for addr, was, now in before.diff(after, limit=40):
    print(hex(addr), was, now)
```

The hero's position is stored as a **float**, so look for three words side by side
that change *continuously* (rather than in steps of one), followed by a yaw that
sweeps across a narrow range. Coordinates in Ape Escape 3 are metres-ish, in the
range of a few dozen, so the float bit patterns cluster around `0x41xxxxxx` and
`0xC1xxxxxx` rather than being arbitrary. `Snapshot.near(addr, 16)` prints the
surrounding words once you have a suspect.

Once you have the position of the hero you can find the *structure*: compare it to
the second slot. In story mode slot 1 is allocated but idle, so its fields hold
whatever the engine last left there -- usually the values from the stage's initial
spawn. A field that is identical in both slots at a fixed offset from your
position is the field you want.

Fill in `structs.actor.base.addr` (or, better, a `signature`), `stride`, and the
field offsets.

> **Signature, not address.** A pinned address only works for the exact build you
> measured, and only until the engine moves that allocation to a different place
> next time it boots -- which it will, because heap layouts depend on how you got
> into the stage. Prefer a signature over *static* code that references the table
> (an instruction sequence accessing the same offset), and use `pointer_chain` to
> follow a global pointer to the live table. `aecoops doctor` will happily accept
> either, and the entry's `note` field is the place to write down which one you
> trust and why.

---

## 3. Character ids

**Why.** `characters.kei` and `characters.yumi`: the two small integers that say
which hero an actor is.

**How.** These are almost certainly 0 and 1, and "almost certainly" is how you
break a stage's models, so measure them:

1. Start a stage as Kei. Find the byte or word that the engine stores in the actor
   structure's character field (it will be adjacent to the actor's other
   identity-ish fields and will survive the whole stage, unlike position).
2. Quit out, start a **new save** or the character-select for a two-player
   minigame as Yumi, and read the same field in the same structure.
3. Whatever the two values are, they are the ids. If they come out as anything
   other than `0` and `1`, put exactly what you measured into the table -- the
   mod does not care about the values, only that they are the right ones.

The two-player minigames are the fastest place to do this, because there both
heroes exist at once and you can watch the id swap as you change selection.

---

## 4. Pad port

**Why.** Which controller drives which actor. `0` for player one, `1` for player
two.

**How.** In a two-player minigame, find the field that differs between the two
actors while everything else about them matches, and change when you swap
controllers. It is usually next to the character id. In story mode, the field is
likely to hold `0` and never change -- that is consistent, and it is also the
field the mod needs to write `1` into.

---

## 5. View mode

**Why.** `layouts.single`, `layouts.vertical` and `layouts.horizontal`: the value
the renderer reads to decide how many viewports to draw and how to cut them.

**How.** This is the one to be careful with, because guessing it turns the screen
into noise. Find it by *entering one of the multiplayer minigames that already
draws two screens*, and comparing that RAM to a story stage:

| State | What to record |
|---|---|
| Story stage | whatever the renderer reads. Often `0`, but record what you see rather than assuming. |
| A two-player minigame, two screens showing | one value |
| A two-player minigame in a different layout, if one exists | a second value |

The value in story mode goes into `layouts.single`; the minigame values into
`/vertical` and `horizontal` as they match what you see. If the game only ever
produces one split layout, record it for `vertical` and leave the other `null` --
`aecoops install --split horizontal` will then fail with a clear message instead
of writing a nonsense value.

Use `--split vertical` (or whichever you verified) and check the picture. If the
image tears or the HUD doubles up, you have the wrong value: revert with
`aecoops revert --journal coop-journal.json` and try the neighbouring word.

---

## 6. Counts and flags

| Field | Where to look |
|---|---|
| `actor_count` | The stage logic's count of live player actors. In a two-player minigame it is `2`; in a story stage it is `1`. |
| `partner_enabled` | Something the story stage checks before the second hero can exist. If `actor_count` alone is not enough to make the partner move, this is the flag that was missing. |
| `quota_shared` | Optional. The monkey quota for the stage. Only needed if you want both heroes' captures to count toward one number. Skip it at first. |

For each: in a two-player minigame, snapshot; in a story stage, snapshot; the
words that differ and are small integers are the candidates. Change them *in the
minigame* first -- if forcing the story value there breaks the split, the word
you are holding is load-bearing and you have found the right one.

---

## Recording what you find

```bash
aecoops set characters.kei=0
aecoops set characters.yumi=1
aecoops set structs.actor.stride=0x1f0
aecoops set structs.actor.fields.char_id.offset=0x38
aecoops set globals.frame_hook.addr=0x00123456
aecoops set globals.frame_hook.verified=2026-10-15
```

Every one of these writes into `data/addresses.json`, in the same shape the file
already has, so the table can be version-controlled, shared and reviewed. Add
`verified: <date>` to anything you confirmed in a running game, and remove it
again if a game update invalidates it.

Then check yourself:

```bash
aecoops doctor          # everything resolved?
aecoops install         # install and take one watchdog pass
aecoops play            # co-op
```

---

## When it does not work

| Symptom | Likely cause | What to do |
|---|---|---|
| `the hook has not run for N watchdog passes` | Site is not on the per-frame path, or you are in a menu. | `aecoops discover sweep` in a *running stage*. |
| The picture tears, or the HUD doubles | Wrong `view_mode` value. | `aecoops revert`, try the neighbouring value. |
| Player two's hero stands still | The pad port or `partner_enabled` is wrong, or `actor_count` is not the field the spawner reads. | Probe the neighbouring words; the tether is on by default and should at least mirror player one. |
| Both heroes are Kei | `characters.*` are wrong (both reading the same value). | Re-measure against a two-player minigame. |
| The game crashes on install | The cave landed in memory something else uses. | `aecoops find-free --range 0x00100000:0x00300000`, and `verify_stable` must say `stable=True`. |
| Nothing happens at all, no errors | PINE is connected to a different game or a paused emulator. | `aecoops info`. |

Whatever you learn, write it into the field note at
`knowledge/games/ape-escape-3/coop-split-screen-kei-yumi.md`. Addresses and
recipes, not game data.
