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

## The six discovery verbs

Everything below is built from these, so they are worth knowing before the first
recipe uses them:

| Command | What it answers |
|---|---|
| `discover counter` | Is the game running at frame rate? (Also: where is a clock I can trust.) |
| `discover sweep --from-elf` | Which of these functions runs exactly once per frame? Measured, not guessed. |
| `discover snapshot` / `discover diff` | What differs between two **states** -- a stage and a minigame, an ordinary view and an enlarged MonkeyCam? |
| `discover watch` | What differs between two **moments**, in a region you already suspect? |
| `discover poke` | Which word *does* the thing? Writes candidates every frame for a few seconds, then puts everything back. `--call` makes the game *run* a candidate instead (`--call-read` when the function wants the word at an address). |
| `discover try` | The same question, forty times in a row: walks a diff's candidate list, one poke each, and asks you after every one. This is the last step of most of the recipes below. |

`poke` and `try` are the only ones that write. Both refuse to run on top of an
installed cave, restore the instruction they displaced and zero the cave they built,
even if you interrupt them -- so "try a value and look" is a safe loop rather than a
way to lose a save.

## The try loop

Every recipe here ends the same way, and it is the one step that cannot be
automated: write a candidate value, look at the screen, decide. A diff routinely
hands you twenty candidates, and doing that by hand is twenty shell commands with
the same five arguments -- which is how a measurable question turns into an
afternoon.

```bash
# in a stage, with a story-mode save loaded
aecoops discover snapshot --out story.json
# ... now get to the *other* state: e.g. enter a two-player minigame ...
aecoops discover snapshot --out minigame.json

# walk what changed, writing the minigame's value into the story state, one at a time
aecoops discover try --from-diff story.json minigame.json --max-value 64 --seconds 3
```

It prints one candidate at a time, writes it for a few seconds, and then asks:

```
  trying 0x00110010 = 3
    178 ticks -- writing 1 entries every frame for 3.0s
  [4/21] 0x00110010 = 3 -- did that do it? [y]es / [n]o / [q]uit:
```

Keep your eyes on the screen during those seconds -- the write stops when the
question appears, so the *during* is the observation. `n` moves on, `y` stops and
hands you the two commands that record it, `q` stops immediately and leaves nothing
written. Everything that was tried is printed as it goes, so `> log` gives you a
list of what was ruled out as well as what worked.

Three things it will not do. It will not decide for you: "did the screen split" is a
judgement, and a tool that guessed it would be writing your answer into the recipe.
It will not write the worksheet by itself, because *which field* a value belongs to
(one view mode? a layout id? a count that now means something else) is the part that
needs your eyes on the game. And it will not tell you the diff was relevant -- if
nothing works, the two snapshots were probably not the two states you meant, and
`--kind settled` is worth trying before you widen the search.

## Which section a message means

The tool's output and `data/addresses.json` point back here as
`docs/DISCOVERY.md#<key>`, with the short keys below rather than the full heading
-- a warning line has to stay readable on a terminal. `aecoops selfcheck` checks
that every key used anywhere in the tool still resolves to a section.

| key | section |
|---|---|
| `frame-counter` | 0 |
| `frame-hook` | 1 |
| `actor-table` | 2 |
| `character-ids` | 3 |
| `pad-port` | 4 |
| `view-mode` | 5 |
| `counts` | 6 |
| `quota` | 6 |
| `actor-count` | 6 |
| `hud` | 7 |
| `viewports` | 8 |
| `camera` | 8 |
| `position` | 9 |
| `tether-radius` | 9 |
| `calls` | 10 |
| `try` | The try loop |
| `recording` | Recording what you find |

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

**A correction first, because the earlier version of this page got it wrong.** No
Ape Escape 3 minigame is documented as *split-screen*: the two-player minigames
(`Monkey Soccer`, `Ultim-ape Fighter!`, the two-player ping pong and boxing
modes) are shared-screen, one picture, two players. So "copy the minigame's two
viewports" may have nothing to copy. What the story renderer itself definitely
can do, and therefore what the split has to be built on, is draw a **second view
of the world into a rectangle**: the MonkeyCam picture-in-picture in the
bottom-right corner, which **L2 enlarges and L2 restores**, and the L2
first-person zoom. Use those as your two-viewport evidence.

**How.** The search is the same shape either way: compare two *states*, and keep
the words that differ and are small integers. There is now a pair of commands for
exactly this, because the two states are minutes and a menu apart and no single
process can hold both:

```bash
aecoops discover snapshot --out stage.json      # in a story stage, HUD visible
# ... walk into a MonkeyCam, or enter a two-player minigame ...
aecoops discover snapshot --out second.json
aecoops discover diff stage.json second.json --max-value 64
```

Then narrow the candidates by poking them and watching:

```bash
aecoops discover poke --site 0x00123456 --set 0x00110010=1 --duration 3
```

`poke` writes a value every frame for a few seconds and then puts everything
back. It is the only command here that writes memory for its own sake, which is
why it refuses to run over an installed cave and always restores what it found.
Guess-then-look is legitimate for `view_mode` because a wrong value is *visible*
immediately, and `poke` undoes itself when the timer runs out -- unlike a
one-shot write, it cannot leave the game in a state you have to reset.

| State | What to record |
|---|---|
| Story stage | whatever the renderer reads. Often `0`, but record what you see rather than assuming. |
| MonkeyCam enlarged with L2, or a minigame's second view | one value |
| A second such state in a different layout, if you find one | a second value |

A signature is often only present while a stage is loaded, and looking for one
across all 32 MB of main RAM plus the scratchpad takes several seconds. When you
know where the thing lives, say so and the scan gets quick:

```bash
aecoops install --scan-range 0x00100000:0x00300000
```

The value in story mode goes into `layouts.single`; the other values into
`/vertical` and `horizontal` as they match what you see. If the game only ever
produces one split layout, record it for `vertical` and leave the other `null` --
`aecoops install --split horizontal` will then fail with a clear message instead
of writing a nonsense value.

Use `--split vertical` (or whichever you verified) and check the picture. If the
image tears, that is the wrong value: revert with
`aecoops revert --journal coop-journal.json` and try the neighbouring word.

**If neither value holds**, the renderer is not choosing its cut from one word.
Then go to section 8 and measure the *rectangles* -- the mod drives those directly
as well, and it is the same structure the MonkeyCam moves when you press L2.

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

The same snapshot/diff pair from section 5 works here:

```bash
aecoops discover diff stage.json minigame.json --max-value 8
```

---

## 7. The HUD

**Why.** One screen draws one HUD. Split it and the game's single monkey counter
sits on top of one player's half while the other has nothing to read. Three modes
exist, and you only need to measure as much as the mode you want:

| Mode | `--hud` | What it needs measured |
|---|---|---|
| One HUD, where the game puts it | `shared` (default) | nothing at all |
| One HUD per player, each in its own view | `per-view` | `globals.hud_instances`, `structs.hud.base`, `.stride`, and the `visible` field at minimum |
| Nothing, for a clean split | `hidden` | `globals.hud_instances`, or `structs.hud.fields.visible` |

**How.** The reference implementation is the game's own two-player modes: they
draw one HUD per player, each anchored to its own side, each reading that player's
state. Three questions, in the order they get answered:

1. **How many HUD instances does the renderer build?** `globals.hud_instances` is
   the parallel of `actor_count`: `1` in a story stage, `2` in a two-player
   minigame. Snapshot the two states and diff them; it is a small integer that
   changes exactly once, when the second player's HUD appears.
2. **Where is the HUD structure, and how far apart are two instances?** Find
   something per-player in the HUD -- a score, a stamina bar, an owner index --
   then find the same field twice, at the same offset, `stride` bytes apart. In
   *Ultim-ape Fighter!* the two stamina bars are the easiest pair in the game.
3. **What are the anchor fields?** In a two-player minigame both instances are on
   screen at once, at different positions. Their `x`/`y` are two words that do not
   change on their own: `aecoops discover diff` two snapshots taken while
   *nothing about the HUD changes* will not show them (they are constants), so
   find them by poking:

   ```bash
   aecoops discover poke --site 0x00123456 --set 0x00181004=320 --duration 3
   ```

   Watch the HUD while the poke runs. A write to the anchor moves the panel; a
   write to any other word does not. `--set` takes several pairs, so a small sweep
   is one command rather than five.

**Coordinates.** Anchors are in the game's own HUD space, not pixels you can count
off a screenshot, so the `x`/`y` you measure are whatever the engine writes. The
mod places two instances at `--screen`'s two halves (default `640x448`, the PS2
NTSC frame; `--screen pal` is `640x512`) plus a margin, which is only correct if
that space matches the engine's. Check it the first time: if a per-view HUD lands
in the right half but the wrong place, adjust the numbers you recorded rather than
the flag.

**Owner.** If the story HUD has an owner/player field, the mod writes each
instance's own pad number into it. If it does not, that field is skipped -- the
contents of a story HUD are global (jackets, coins, remaining monkeys), so a
missing owner costs nothing.

---

## 8. The viewport rectangles (the second lever)

**Why.** Only when section 5 failed: the mod can also place each view by writing
the rectangle the renderer draws it into -- `x`, `y`, `w`, `h` per view. That does
not depend on the renderer agreeing to a *mode*, which is what makes it worth
measuring even though it is more work.

**How.** The MonkeyCam is the anchor. It is a second view of the world drawn into
a rectangle the game moves at runtime, so its position and size are stored in
memory rather than baked into a draw call -- which means the structure exists, and
you can find it:

1. Get a MonkeyCam on screen in a normal stage (Monkey Radar + L2), then stop
   touching anything: a still camera with a stationary monkey gives you a stable
   picture.
2. `aecoops discover snapshot --out cam_small.json`
3. Press **L2** to enlarge the MonkeyCam. Do not move anything else.
4. `aecoops discover snapshot --out cam_big.json`
5. `aecoops discover diff cam_small.json cam_big.json --max-value 700`

You are looking for two integers that changed and are in screen range: one for
the rectangle's left/top corner or its size, one for the other axis. Four fields
move together, usually adjacent. Record:

```bash
aecoops set structs.viewport.fields.x.offset=0x00
aecoops set structs.viewport.fields.y.offset=0x04
aecoops set structs.viewport.fields.w.offset=0x08
aecoops set structs.viewport.fields.h.offset=0x0C
```

**The stride.** The MonkeyCam's rectangle is one instance; a split needs a second.
Two possibilities, and the diff tells you which:

- the second rectangle is a *second instance* of the same structure: find the
  field that differs between two views while both are drawn, and measure the gap;
- the engine has exactly one rectangle and scales the view into it: then there is
  no stride to find, and this route cannot split the screen. `--no-viewports`
  turns it off; `view_mode` is then the only lever.

**The count.** `globals.viewport_count` is how many views the renderer draws. If
you find it, the mod writes 2; if you do not, the mod writes the rectangles anyway
and says so, because plenty of renderers derive the count from something else.

**Check it.** This is the one write in the toolkit that can turn the picture into
garbage, so check it where you can see it:

```bash
aecoops discover poke --site 0x00123456 --set 0x00183008=320 --set 0x00183028=320 --duration 4
```

Two widths of 320 in a row and the two views should appear side by side (or not --
that is the measurement). Escape restores everything; so does the timer.

**If it works**, `aecoops install` picks it up automatically and `status` shows
`viewports: N writes, rectangles placed`. (If you get to the point of testing
several rectangle words at once, `discover try --candidate 0x...=320` walks a list
of them the same way `--from-diff` does.) Both routes run at once, which is
deliberate: the rectangles say *where*, the mode says *how many*, and a renderer
that respects either one is enough.

---

## 9. The tether radius (optional)

**Why.** With two viewports of one stage, the heroes can walk into different
loading zones. `--tether-radius N` keeps player two within N world units of
player one.

**How.** Nothing to measure: it uses the position fields from section 2 and works
in world units. Tune it by playing -- `--tether-radius 200` is a starting guess for
a stage whose areas are tens of metres across. It is applied by the watchdog (four
times a second), so the correction can be a fraction of a second late; that is why
it is off by default and why the distance is measured by the host rather than
inside the cave. `--no-tether` turns the mirroring off as well as the leash.

---

## 10. Calls (functions of the game's own)

**Why.** Everything else in the mod *writes*: a value into a word, or a copy of one
word into another. That is enough for state, and it is not enough for anything the
game has to *do*. Two examples from this mod's own gap list:

* the second actor may not exist as a structure at all -- a stage that simulates one
  hero may need its own "add the second player" routine, called once, to build one;
* the renderer may recompute its viewports every frame from something other than
  `view_mode`, in which case what is needed is the *setter* that tells it to rebuild,
  not another write to the same word.

Both are calls. There is no way to make them from outside the emulator: only code
already running inside the game can call the game.

**What the cave does with a call.** A call entry is two words -- a function address
and an argument -- and the cave's interpreter calls it with the argument in `$a0`,
with `$ra` and `$sp` saved, and then goes on to the next entry. The function runs
inside the game's own frame, on its own stack, with the game's state live. If the
argument word has **bit 31 set**, the low bits are an address and the *value at that
address* is passed instead, read at call time -- which is how a spawner gets the
handle the stage has just allocated rather than a number that was true a quarter of a
second ago.

**Once, or every refill.** A call marked `once` is made once and then never again:
the cave zeroes the function word as it calls, and the host re-sends only the calls it
has not yet *seen* the cave consume (it reads the call region back and looks for them).
A frozen `.pnach` has no host at all, so there every call fires once, on the first
frame of the first stage -- which is the right shape for "construct the partner when
the stage starts" and the wrong shape for everything else, which is why the export
warns about repeating calls. Leave `once` at its default unless the function is
genuinely idempotent: the difference is one partner or sixty a second.

**Finding one.** You are looking for a function with two properties: it is called
from the per-frame path (or from a place you can reach), and calling it *does* the
thing. The worked example is the view-mode setter, because you already have a value
to try:

```bash
# 1. Find the word the setter writes. Walk into a two-player minigame and diff:
aecoops discover snapshot before.json
# ... enter the minigame ...
aecoops discover snapshot after.json
aecoops discover diff before.json after.json --kind changed --limit 40

# 2. Find what writes it: Ghidra, or a breakpoint on the word, or -- if you have a
#    function map -- the candidates whose code stores to it. Any function that both
#    appears in that list and runs once per frame is a candidate.

# 3. Call it, once, for four seconds, and watch the screen:
aecoops discover poke --call 0x00123456=1 --duration 4

# 4. Put the answer in the worksheet. `arg` is the value the setter wants; if the
#    setter should read a handle instead, set bit 31 on the address:
aecoops set calls.set_view_mode.addr=0x00123456
aecoops set calls.set_view_mode.arg=1
aecoops set calls.set_view_mode.once=false
```

`discover poke --call FUNCTION=ARG` (or `--call-read FUNCTION=SOURCE` to pass the
word at an address, which is what a routine that wants a handle expects) is
deliberately awkward about the argument: a
call to an address with a made-up argument is a jump into code with garbage in a
register, and the game is entitled to crash. So pass `0` if the function takes none
(this is the common case for a setter), and only pass a real value when you have seen
the function's parameter in a disassembly or in a register dump. **The mod itself is
stricter still**: a call with an address and no argument is refused outright, and the
refusal is printed once (see `aecoops doctor` for the standing list). An unmeasured
call -- neither address nor argument -- is simply not made, which is why the shipped
worksheet has `calls.spawn_partner` and `calls.set_view_mode` sitting at `NOT FOUND
YET` doing nothing.

**What it cannot do.** A call is not a sandbox. The function runs with whatever the
game has in memory at that moment, so a call to a routine that expects a loaded stage
will misbehave in a menu. And a call to something that *constructs* has to be made
once per stage, not once per session -- the mod's `once` bookkeeping is per install,
so `eject` and reinstall (or reload the stage, if you are re-running `play`) when you
change stages. Restarting the host against a cave that is still installed is the one
case the mod resolves by assumption: a one-shot call it cannot see waiting in the
region is treated as already made and left alone, because a second hero who never
spawns is a reload while two of them is a bug. `install` says so when it happens.
That is the honest bound on this feature, and the reason it is the last of the ten
recipes rather than the first.

---

## Recording what you find

```bash
aecoops set characters.kei=0
aecoops set characters.yumi=1
aecoops set structs.actor.stride=0x1f0
aecoops set globals.hud_instances.addr=0x1a2b3c4
aecoops set structs.hud.stride=0x40
aecoops set structs.hud.fields.x.offset=0x04
aecoops set structs.hud.fields.y.offset=0x08
aecoops set structs.camera.stride=0x20
aecoops set structs.camera.fields.target_actor.offset=0x00
aecoops set structs.viewport.stride=0x20
aecoops set globals.viewport_count.addr=0x1a2b3c8
aecoops set structs.actor.fields.char_id.offset=0x38
aecoops set globals.frame_hook.addr=0x00123456
aecoops set globals.frame_hook.verified=2026-10-15
# calls (section 10): both halves or nothing -- a function plus the argument to
# call it with. Bit 31 on `arg` means "read the argument at this address".
aecoops set calls.spawn_partner.addr=0x00160000 calls.spawn_partner.arg=1
aecoops set calls.set_view_mode.addr=0x00123480 calls.set_view_mode.arg=1 calls.set_view_mode.once=false
```

Every one of these writes into `data/addresses.json`, in the same shape the file
already has, so the table can be version-controlled, shared and reviewed. Add
`verified: <date>` to anything you confirmed in a running game, and remove it
again if a game update invalidates it.

Then check yourself:

```bash
aecoops doctor                 # everything resolved?
aecoops status --hud shared    # what would be written, and what is still missing
aecoops install --hud shared   # install; the plan is armed in the same pass
aecoops play --hud shared      # co-op
```

`status` is the honest one: it prints the writes the mod would apply and a
`waiting on:` list of every measurement those writes depend on. On a fresh disc
that list is the whole to-do, in the order the rest of this page explains it.

Once a configuration works, freeze it (the `.pnach` carries every write the live
run would make, the HUD and the cameras included, and any calls -- which in a frozen
patch fire once, at the first stage frame):

```bash
aecoops export-pnach --hud per-view --out patches/SCUS-97501.pnach
```

---

## When it does not work

| Symptom | Likely cause | What to do |
|---|---|---|
| `the hook has not run for N watchdog passes` | Site is not on the per-frame path, or you are in a menu. | `aecoops discover sweep` in a *running stage*. |
| The picture tears in the middle | Wrong `view_mode` value. | `aecoops revert`, try the neighbouring value. |
| `status` prints `the split cannot be applied yet` | `layouts.*` are still `null`. | Section 5. Nothing is written to the game until this is measured. |
| `--hud per-view` did nothing | `globals.hud_instances` and `structs.hud` are still `null`; install lists them as notes. | Section 7. `shared` is a legitimate answer in the meantime. |
| Both HUDs are stacked in the same corner | `view_mode` worked (there are two instances) but the anchors are unmeasured, so the game placed both. | Measure `structs.hud.fields.x`/`.y` by poking, section 7. |
| `viewports: N writes, rectangles placed` but the screen is still one view | The renderer ignores rectangles written by someone else, or `viewport_count` is unmeasured and it never looks at the second one. | Section 8. `--no-viewports` stops the writes if they are not helping. |
| The picture goes black or garbage when the rectangles are driven | The rectangle units are not pixels, or `w`/`h` landed on a different field than you measured. | `aecoops revert --journal coop-journal.json`, or restart; then re-measure with the MonkeyCam diff. |
| The HUD is in the right half but half off the screen | The anchor space is not `640x448`. | `--screen pal`, or record the engine's own coordinates instead. |
| Player two's hero stands still | The pad port or `partner_enabled` is wrong, or `actor_count` is not the field the spawner reads. | Probe the neighbouring words; the tether is on by default and should at least mirror player one. |
| Both heroes are Kei | `characters.*` are wrong (both reading the same value). | Re-measure against a two-player minigame. |
| The game crashes on install | The cave landed in memory something else uses. | `aecoops find-free --range 0x00100000:0x00300000`, and `verify_stable` must say `stable=True`. |
| Nothing happens at all, no errors | PINE is connected to a different game or a paused emulator. | `aecoops info`. |

Whatever you learn, write it into the field note at
`knowledge/games/ape-escape-3/coop-split-screen-kei-yumi.md`. Addresses and
recipes, not game data.
