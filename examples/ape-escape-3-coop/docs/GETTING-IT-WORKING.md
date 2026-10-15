# Getting it working, start to finish

This is the whole thing as one session, in order: what to do, what you should see,
and what to do when you don't. `README.md` explains the mod, `docs/DISCOVERY.md`
teaches each measurement in depth, and `aecoops doctor` tracks where you are. This
page is the to-do list.

Budget an evening. Most of it is steps 4 and 5; steps 0–3 are ten minutes.

> **Before anything:** this needs the game, in PCSX2, on your machine. The mod never
> writes to your ISO or your save, and `eject` / `revert` put RAM back. Nothing here
> is a copy of the game, and nothing leaves your machine unless you paste it.

---

## 0. PCSX2 and the game (2 minutes)

1. PCSX2 → **Settings → Advanced → Enable PINE IPC Server**. (Windows: TCP
   `127.0.0.1:28011`. Linux/macOS: a unix socket. `doctor` prints what it tried.)
2. Boot Ape Escape 3 and **get into a stage**, with a save loaded. Most of the memory
   this needs does not exist on the title screen or in a menu.

## 1. The tool (1 minute)

```bash
git clone -b arena/aa9f0fae-universal-modder https://github.com/axolotljose/universal-modder.git
cd universal-modder/examples/ape-escape-3-coop
python -m aecoops selfcheck        # 114 checks, no emulator needed
```

Python 3.10 or newer, standard library only. If `selfcheck` fails, stop and send me
the failure — that is the one thing that is supposed to work before anything else.

## 2. Is the emulator talking? (1 minute)

```bash
python -m aecoops doctor
```

**You should see:** a list of `NOT FOUND YET` entries (that's the to-do list, ~32 of
them) and then one of:

- `PINE: <serial>, <title>` — good, continue.
- `PINE: not reachable` — the step-0 setting, or the game is not running.

## 3. A running stage (1 minute)

```bash
python -m aecoops discover counter
```

**You should see:** a rate of roughly **59.94 Hz** (NTSC) or **50 Hz** (PAL). Anything
else means you are not in a running stage, or the game is frame-limited somewhere
unusual. This step is a sanity check, not a measurement you need.

## 4. The frame hook — the one everything depends on (20 minutes)

This is the only step that wants a disassembler. Export the function list from Ghidra
(open the game's executable, let it auto-analyze, export *name, address, size*), then:

```bash
python -m aecoops discover sweep --from-csv ghidra_funcs.csv --name update
```

**You should see** a table like:

```
   59.94 Hz  0x00123450  per frame
   83.10 Hz  0x00150120  update_2d: called at 83.1 Hz, not frame rate: it is a
                         render/menu/conditional path
    0.00 Hz  0x0017abc0  never called: this code path is not resident in this scene
```

Keep the row that says **per frame**. No row says per frame? Widen it
(`--name` filter off, `--limit 0`) and read `docs/DISCOVERY.md` §1.

```bash
python -m aecoops set globals.frame_hook.addr=0x00123450 globals.frame_hook.verified=2026-10-15
```

## 5. The four numbers that make the split visible (60–90 minutes)

Ordered by how much of the feature each one unlocks. After each, `status` tells you
what it bought you.

```bash
# a) character ids -- get these by looking at what differs between two saves, one per hero
python -m aecoops set characters.kei=<n> characters.yumi=<n>          # DISCOVERY.md §3

# b) the split value, and the two-view id in the minigame menu
#    in a stage: snapshot. then enter a 2-player minigame: snapshot again.
python -m aecoops discover snapshot --out story.json
#    ...get into Monkey Soccer (or any 2P minigame)... then:
python -m aecoops discover snapshot --out minigame.json
python -m aecoops discover try --from-diff story.json minigame.json --max-value 64 --seconds 3
#    it pokes each candidate in turn and asks you: did that do it?
#    `y` stops and prints the exact `set` commands to record the winner.

# c) the actor table (~§2): the two hero structures, their stride, and the offsets of
#    active / char_id / pad_port / pos_x / pos_y / pos_z / yaw
python -m aecoops set structs.actor.stride=0x1f0
python -m aecoops set structs.actor.fields.char_id.offset=0x38
#    ...and so on; doctor lists every field name the mod wants
```

## 6. What would it do? (1 minute)

```bash
python -m aecoops status
```

**You should see** the writes it would make and a `waiting on:` list. **That list is
your to-do.** It is safe to run at any point, at any stage of measurement — it reads,
and writes nothing.

## 7. Co-op (5 minutes)

```bash
python -m aecoops install
python -m aecoops play --p1 kei --p2 yumi
```

`install` either reports what it armed, or refuses and says why — a missing hook, a
site that isn't a `jal`, a disc it can't verify. Ctrl-C stops the watchdog; the cave
stays in and the game keeps running. Undo it any time:

```bash
python -m aecoops eject --site 0x... --cave 0x...
python -m aecoops revert            # replays coop-journal.json
```

If it runs but the screen doesn't split, that is the expected next thing to chase:
`docs/DISCOVERY.md` §8 (the viewport rectangles, driven by wherever the MonkeyCam's
box lives) and §10 (calling the game's own view-mode setter instead of writing to the
word). Both are implemented and were built for exactly this outcome.

## 8. Freeze it (1 minute)

```bash
python -m aecoops export-pnach --out cheats/<your CRC>.pnach
```

Copy that into PCSX2's `cheats/` folder. Co-op then loads with the game and nothing
else has to run. The filename has to be your disc's CRC (the dotted `uuid` in
`aecoops info`).

---

---

## If you are doing this from a phone

Two things are worth knowing, because this changes what is possible rather than just
how it feels:

**The tool runs where PCSX2 runs.** It talks to the emulator over PINE, which is a
local IPC channel (Windows: TCP `127.0.0.1:28011`; Linux/macOS: a unix socket). It is
not a network service and it is not exposed to the internet, so the phone in your
hand cannot reach the emulator on its own. Also worth saying plainly: **Android PS2
emulators do not have PINE**, so a phone-only setup (game running on the phone) cannot
be hooked by this mod at all -- the game has to be in PCSX2 on a computer.

That leaves two practical arrangements.

**One: the computer runs everything, the phone is the conversation.** This is the one
to use. Run the commands on the computer, then get the *bundle* to the phone:

```bash
python -m aecoops share --out bundle.txt          # 3 KB: what is measured, what is not
python -m aecoops share --extra sweep.txt         # ...and inline a saved command's output
```

`share` prints only the bundle, so `python -m aecoops share > bundle.txt` works too,
and `--extra` folds a saved `sweep`/`diff` output into the same text so there is one
thing to move, not five. Then send that file to the phone any way you like -- a
message to yourself, a note app, email -- and paste it here. It is addresses, names
and counts: no game data, nothing that should not circulate.

Coming back the other way is symmetric. When I answer with a batch of measurements,
put them in a file and apply them in one command instead of one command each:

```bash
# measurements.txt, with the lines exactly as sent:
#   globals.frame_hook.addr = 0x00123450
#   globals.view_mode.addr = 0x0011A2B4
python -m aecoops set --file measurements.txt
```

**Two: the phone is the terminal (advanced).** Android can run the tool itself in
[Termux](https://termux.dev) (`pkg install python git`), and Android's `adb` can
tunnel the phone's localhost to the computer's over USB, which is what makes the
local-only PINE port reachable:

```bash
# on the COMPUTER, with the phone attached and USB debugging on:
adb forward tcp:28011 tcp:28011

# on the PHONE, in Termux:
git clone -b arena/aa9f0fae-universal-modder https://github.com/axolotljose/universal-modder.git
cd universal-modder/examples/ape-escape-3-coop
python -m aecoops selfcheck        # proves the tool itself works on the phone
python -m aecoops doctor           # reaches PCSX2 through the adb tunnel
```

From there every command in this page works as written, on the phone, including
`discover try` -- which is the one command where being on the phone is actually
*better*, because the emulator screen is in front of you while it asks. It is more
setup than arrangement one, and it needs Termux, adb and USB debugging; take it only
if you would rather not move text between two machines.

---

## What to send back if it fights you

Raw output, messy is fine — these four cover almost every case:

1. the `doctor` output,
2. the whole `sweep` table (I care most about which row hit frame rate),
3. the two addresses `discover diff` reported around the minigame, if you get that far,
4. the exact text of anything that refused or crashed.

One command produces all of it:

```bash
python -m aecoops share --extra sweep.txt --out bundle.txt
```

`share` never includes the ISO, a savestate, a RAM dump, extracted files or a
decompiled listing -- it reads the worksheet and the emulator's own metadata (serial,
title, CRC, run state) and nothing else, and a journal is summarised as a count
rather than quoted. So the bundle is safe to paste anywhere, and it is the only thing
worth sending.

**Do not send** your ISO, a savestate, a RAM dump, extracted files, or a decompiled
listing. None of that is needed, and it is the one thing that must never circulate.
A filled-in `aecoops/data/addresses.json` is smaller than a screenshot and is the
entire payload this mod is waiting on.
