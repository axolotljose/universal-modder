# Garry's Mod — two meme SWEPs: `100 ZOMBIE SPAWNER` and `7000 F*CKING MISSILES`

A complete GMod addon (`epic_sweps/`) with two weapons in the **EPIC SWEPs** spawnmenu tab, plus the
offline harness we used to prove both run without errors.

| Weapon | Class name | What it does |
|---|---|---|
| **100 Zombie Spawner** | `epic_zombiespawner` | You throw a box. It cracks open with a green portal ring, a sky beam, dust and dynamic light — then **100 zombies** walk out of it in four waves. |
| **7000 F*cking Missiles** | `epic_missilebarrage` | You paint a target. Big text slams onto every screen: **"7 THOUSAND F*CKING MISSILES"**, then **"BLOW UP THIS MAN"**, a lock-on reticle, then **7000 missiles fall out of the sky** and flatten everything in a 1400-unit area. |

Both also have voice lines (see [Voice](#voice-lines)).

## Download

Prebuilt addons (each zip has the addon folder at its root, so unzipping into `garrysmod/addons/` *is* the install):

- **both weapons** → [`epic_sweps.zip`](https://github.com/axolotljose/universal-modder/releases/latest/download/epic_sweps.zip)
- **just the zombie box** → [`epic_zombie_spawner.zip`](https://github.com/axolotljose/universal-modder/releases/latest/download/epic_zombie_spawner.zip)
- **just the missiles** → [`epic_7000_missiles.zip`](https://github.com/axolotljose/universal-modder/releases/latest/download/epic_7000_missiles.zip)

These are the only binaries in the release; the real source is the `epic_sweps/` folder in this repo
(`git clone`, or `python3 tools/build_materials.py --package` to rebuild the zips yourself). If a link
404s, no release has been published to this repo yet.

## Install

Drop the folder in and restart GMod:

```
garrysmod/addons/epic_sweps/
```

Only want one of them? Build the split versions (each is a standalone addon):

```bash
python3 tools/build_materials.py --package     # -> dist/epic_zombie_spawner.zip, dist/epic_7000_missiles.zip
```

Then: spawnmenu → **Weapons → EPIC SWEPs**, or `give weapon_epic_zombiespawner` /
`give weapon_epic_missilebarrage` in the console. Works on sandbox, on a local listen server
(`-insecure`) and on a Semi-Sideways-style server; if there are no zombie NPCs installed the spawner
falls back to zombie-brained citizens or physics ragdolls, so the box always delivers 100 of *something*.

## Tuning

Everything is in `epic_sweps/lua/epic_sweps/shared/epic_sweps_config.lua`. Console commands, saved
between sessions:

| CVar | Default | Meaning |
|---|---|---|
| `epic_zombies_count` | 100 | zombies per box |
| `epic_zombies_instant` | 0 | `1` skips the waves and dumps all of them at once (fun, painful) |
| `epic_missiles_count` | 7000 | missiles per barrage |
| `epic_show_text` | 1 | the big on-screen text |
| `epic_play_voice` | 1 | the voice lines |
| `epic_screen_shake` | 1 | screen shake |

There is also a slider panel at **Spawnmenu → Utilities → Epic SWEPs**.

## How 7000 missiles does not kill the server

This is the whole trick, and the reason naive implementations of this idea crash:

- **One entity, whole server.** `epic_missile_barrage` runs the timetable, does the damage and
  broadcasts ~30 bytes of state every 0.2 s (centre, start time, count, radius, seed).
- **Zero entities for the streaks.** Every client runs the same deterministic impact function with the
  same seed, so everybody sees the same sky without the server networking 7000 positions. The streaks
  are additive 2D sprites drawn in `HUDPaint`, sampled down to `MaxMissilesDrawn` per frame.
- **Only 7 % of them bite.** `DamageMissilePercent` impacts do real `TakeDamageInfo`, and every 24th of
  those also fires `util.BlastDamage` so props and ragdolls actually fly. The other 6500 are light,
  noise and fire — which is what 7000 missiles should look like anyway.
- **The explosion visuals are our own** (`epic_explosion`, render-API flash + ring + one dynamic light),
  not the engine's `explosion` effect, which spawns client-side debris entities you cannot afford by the thousand.

The zombie box uses the same shape of trick: the horde is real (100 NPCs), but the *portal* is state +
effects, and it counts itself down so a 100-zombie spam does not melt the server
(`MaxZombiesAlive`, `RemoveAfter`).

## Voice lines

`cfg.Voice` lists lines as `{ file, text, start }`. The **text is always drawn** — if a wav is missing,
muted, or `epic_play_voice 0`, the words still appear on screen at exactly the right moment. The four
shipped lines are TTS takes; replace them by dropping your own 44.1 kHz / 16-bit / **mono** `.wav`
files over these names (GMod rejects 48 kHz and 32-bit files):

```
sound/epic_sweps/missiles_1.wav   "7 thousand fucking missiles"
sound/epic_sweps/missiles_2.wav   "blow up this man"
sound/epic_sweps/zombie_1.wav     "100 zombies"
sound/epic_sweps/zombie_2.wav     "release the horde"
```

One honest substitution: the text the game **shows** is exactly `cfg.Voice` (`"7 THOUSAND F*CKING
MISSILES"`, `"BLOW UP THIS MAN"`), but the shipped `missiles_2.wav` audio says *"Blow it all up!"* — the
text-to-speech service refused the literal line. Nothing enforces that: record, download or synthesise
any take you like and drop it on that filename, and the audio will match the text. If you keep the file
as it is, nobody hears the difference unless they are looking away.

The other sounds (`explosion`, `launch`, `siren`, `whine`, `portal_hum`) are **generated procedurally**
by `tools/make_sfx.py` — pure Python, no samples, nothing to license. Every sound Valve ships is only
*referenced by path*, never copied into the addon.

## Files

```
epic_sweps/
  addon.txt
  scripts/epic_sweps_sound.txt            sound scripts (random variants, pitch jitter)
  materials/epic_sweps/{flash,missile_trail}.png|.vmt    additive sprites
  materials/vgui/entities/*.png                          spawnmenu icons
  sound/epic_sweps/*.wav                                 generated SFX + the 4 voice takes
  lua/autorun/epic_sweps_init.lua                        loader: includes modules in a fixed order
  lua/epic_sweps/shared/                                 config, util, net, timeline, particles, zombies, cvars
  lua/epic_sweps/client/                                 epic_sweps_hud.lua (the big text), epic_sweps_render.lua (the show)
  lua/weapons/epic_zombiespawner/shared.lua
  lua/weapons/epic_missilebarrage/shared.lua
  lua/entities/epic_zombie_breach.lua                    server horde logic + broadcast state
  lua/entities/epic_missile_barrage.lua                  server barrage logic + broadcast state
  lua/effects/epic_sweps_fx.lua                          epic_explosion / epic_zombie_{burst,wave,pop}
```

Note the layout: all shared modules live under `lua/epic_sweps/` and are pulled in by one `autorun`
loader with `include`/`AddCSLuaFile`. `lua/autorun` has no guaranteed load order, so relying on file
names to load your config before your modules is how addons end up with `attempt to index a nil value
(global 'EpicSWEPs')`.

## Testing without the game

`tools/stub_gmod.lua` is a fake GMod: Vector/Angle math, `timer`, `hook`, `net` (with real read/write
type checking), `ents`, convars, particles, `EmitSound`/`render`/`surface` with **argument-order
validation copied from the wiki's signatures**. `tools/smoke_test.lua` loads the addon into it twice —
once as server, once as client — replays the server's net traffic into the client and steps a fake
clock through both weapons' full timelines.

```bash
npm install                        # dev only, once (fengari + luaparse; the addon itself needs nothing)
npm test                           # 32 checks: 100 zombies, 7000 fired, damage dealt, streaks drawn
npm run lint                       # parse + symbol resolution

node tools/lint_lua.js epic_sweps/lua     # every file parses as Lua 5.1
node tools/lint_symbols.js epic_sweps     # every EpicSWEPs.<module>.<fn> a file calls is defined
```

`lint_symbols.js` is the one that pays for itself: GMod resolves module fields at call time, so a typo
(`Missiles.SpawnBarage`) is normally a runtime error for whoever pulls the trigger. It reads the files'
`local U = EpicSWEPs.Util` aliases, so it checks the calls as they are actually written.

It caught four real bugs while building this: a `net` framing mismatch between the two realms,
`Entity:EmitSound` argument order (it is `name, level, pitch, volume, channel` — *not* the order most
tutorials write), `ParticleEffect` having no quantity argument, and an O(n²) missile firing loop.
`tools/run_smoke_test.js` also prints any engine method it had to invent — currently none, which is how
we know there are no typo'd API names. Both scripts are plain node; `npm test` and `npm run lint` are the
same commands.

## What is and is not verified

Verified in the stub harness: both realms load, both weapons fire, exactly 100 zombies spawn, all
7000 missiles get launched, damage and blast damage land, the required text lines are broadcast, the
renderer draws streaks/text/lights with no NaN or negative-size draw calls, and every engine call
matches a documented signature.

**Not** verified in the real game — nobody has booted GMod here. The look of the effects, sound
levels/attenuation and NPC behaviour on your gamemode are the things to eyeball first. Two honest
caveats: the icons ship as PNG (GMod auto-imports them; convert to 32-bit TGA with VTFEdit if yours
shows up blank), and the zombie NPC brains are left to the gamemode, so on a non-Sandbox gamemode the
horde may stand around until your gamemode gives them targets.

## Ideas from here

`assets/src/` (not in git) holds the big icon sources; regenerate shipped 128×128 icons with
`python3 tools/build_materials.py --icons`. Custom models: set `SWEP.WorldModel` on either weapon and
`cfg.ZombieSpawner.Models` to your zombie list — the box works with any model that exists.
