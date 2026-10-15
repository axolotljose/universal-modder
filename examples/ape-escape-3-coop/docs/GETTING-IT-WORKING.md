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

## What to send back if it fights you

Raw output, messy is fine — these four cover almost every case:

1. the `doctor` output,
2. the whole `sweep` table (I care most about which row hit frame rate),
3. the two addresses `discover diff` reported around the minigame, if you get that far,
4. the exact text of anything that refused or crashed.

**Do not send** your ISO, a savestate, a RAM dump, extracted files, or a decompiled
listing. None of that is needed, and it is the one thing that must never circulate.
A filled-in `aecoops/data/addresses.json` is smaller than a screenshot and is the
entire payload this mod is waiting on.
