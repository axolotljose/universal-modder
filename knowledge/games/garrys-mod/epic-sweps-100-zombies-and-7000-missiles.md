---
kind: game
title: "Garry's Mod SWEPs: a 100-zombie box and 7000 missiles without 7000 entities"
game: "Garry's Mod"                                          # the game's usual name
games_also: []
game_version: "Garry's Mod (Steam), sandbox gamemode, Windows"
platform: windows
engine: source
route: loader-api
tools: ["Garry's Mod Lua addons", "fengari (Lua in node, for the offline harness)", "Pillow (icons)", "python wave + zlib (procedural SFX and PNGs)"]
anti_cheat: "VAC on GMod's multiplayer; local listen server / single player, no injection, no bypass"
status: in-progress
agents: ["Arena.ai (Agent Mode)"]
humans: []
date: 2026-10-15
links: ["examples/gmod-epic-sweps"]
tags: [garrys-mod, source, swep, lua, entity-budget, net, effects, sound-scripts, procedural-audio, offline-harness]
---

# Garry's Mod SWEPs: a 100-zombie box and 7000 missiles without 7000 entities

Two joke weapons built as one addon folder in GMod's official Lua modding layer: `100 ZOMBIE SPAWNER`
(throw a box, a hundred zombies come out with a portal ring, beam, dust and dynamic light) and
`7000 F*CKING MISSILES` (paint a target, on-screen text says the two required lines, then a barrage
falls on a 1400-unit area). The interesting part is not the Lua API, it is how to say "seven thousand"
without the server dying: one entity, one seeded integer, and thousands of cheap screen-space sprites
drawn from a deterministic function every client runs identically.

**Not verified in the real game** — no GMod install in the sandbox. It is verified against a fake-GMod
harness that runs both realms and asserts the behaviour, which caught four real bugs (below).

## Setup

- Garry's Mod on Steam (Windows in the player's case; addons are the same on Linux/Proton). No
  third-party loader: GMod *is* the modding layer.
- Content: an addon folder, `garrysmod/addons/epic_sweps/`, plain loose files — no VPK, no compiled
  assets, no Hammer, no CS:GO-era tooling.
- Test it on a local listen server or single player. Don't put a joke weapon on a public VAC server.

## Route and why

`loader-api`: the game's own Lua VM. Alternatives and why they were rejected:

- **Tool gun / SENT only**: tools are fine for utility, but a weapon in the loadout is what "SWEP addon"
  means to the people who will install this, and `SWEP`/`SENT`/`EFFECT` give the same access.
- **SourcePawn or a gamemode edit**: heavier, needs a server you control, and pointless for spawnables.
- **Custom .mdl/.pcf content**: real .pcf particle systems need `game.AddParticles` plus a server-side
  `PrecacheParticleSystem`, and shipping a model makes the addon big for no gameplay gain. GMod's
  default engine effects (`VortDispel`, `ThumperDust`, `ManhackSparks`, `StunstickImpact`) need neither.

## How the game works (what we had to learn)

- **Load order is not alphabetical by luck.** `lua/autorun/*.lua` runs on both realms; only `*_srv.lua`
  / `*_cl.lua` suffixes are realm-filtered. There is no guarantee that your config file loads before
  your modules, so the addon ships one `autorun` loader that `include`s its own module list in a fixed
  order and `AddCSLuaFile`s each one first. All shared code lives under `lua/epic_sweps/`, not in
  `lua/autorun/`, because `include`/`AddCSLuaFile` resolution from inside `lua/autorun` is a trap worth
  avoiding.
- **Entity budget is the game.** Spawning 7000 entities or 7000 `env_explosion`s is a crash. The design
  that fits: one server entity per barrage, ~30 bytes of state per 0.2 s (`net.SendPVS`), and impact
  positions derived from `math.randomseed(seed)` on each realm, so no per-missile networking exists.
- **EffectData is lossy.** `CEffectData:SetMagnitude` is stored as a 12-bit float limited to 0–1023 for
  networking; `SetScale` is not. Never carry identity/state through magnitude that must survive to the
  client exactly — we keep the seed in the net table and only pass `seed % 1023` for decoration.
- **Two EmitSounds, two orders.** `Entity:EmitSound( name, soundLevel, pitchPercent, volume, channel,
  flags, dsp, filter )` and the global `EmitSound( name, position, entityIndex, channel, volume,
  soundLevel, flags, pitch, dsp, filter )`. Neither matches the order most snippets on the internet use.
- **Sound scripts shadow your arguments.** When you `EmitSound` a *script* name, the script's own
  volume/level/pitch/channel win. Fine here (we set them in the script and randomise variants' pitch),
  but it explains "my volume/pitch changes did nothing".
- **`ParticleEffect` has no quantity argument** — its 4th argument is an optional parent *Entity*, so
  passing a count errors, and inside a `pcall` it fails invisibly.
- **Lua effects are capped at 2048 per frame** (`util.Effect`); `PostDrawTranslucentRenderables` is the
  right place for `render.DrawBeam` / `render.DrawQuadEasy` / `DynamicLight`, and 2D stays in `HUDPaint`.
- **Icons need no code.** GMod reads `materials/vgui/entities/<class>.<tga|png|jpg>` on its own.
- **Audio format is picky**: 16-bit, 11025/22050/44100 Hz. A 48 kHz or 32-bit wav simply does not play.
- `CreateSound`-driven loops are client-side here, which is what we wanted for the siren/whistle (per
  listener, no networking), and `SetPlayTime`/`Stop` on the channel is the clean way to kill them when
  the barrage state ages out.

## Build steps

1. Write the folder as `examples/gmod-epic-sweps/epic_sweps/` (README lists every file).
2. Assets: `python3 tools/make_sfx.py` for the SFX wavs (stdlib only), `python3
   tools/build_materials.py` for the two additive sprites and their `.vmt`s, `--icons` to rebuild the
   128×128 spawnmenu icons from `assets/src/` with Pillow.
3. Voice: generate the takes as 44.1 kHz mono wav and drop them in `sound/epic_sweps/`; the on-screen
   text is driven by the same `cfg.Voice` table, so a missing or muted wav cannot break the joke.
4. After every change: `node tools/lint_lua.js epic_sweps/lua` (parses), `node tools/lint_symbols.js
   epic_sweps` (every `EpicSWEPs.<module>.<field>` read is also written), `node tools/run_smoke_test.js`
   (behaviour; needs `npm i fengari luaparse`, dev only).
5. `python3 tools/build_materials.py --package` → `dist/epic_sweps.zip` plus each weapon as its own
   standalone addon (`dist/epic_zombie_spawner.zip`, `dist/epic_7000_missiles.zip`). Each zip has the
   addon folder at its root, so unzipping into `garrysmod/addons/` is the whole install; both halves ship
   the shared modules so the loader's `include` list still resolves.
   `addon.txt` is GMod's **`"AddonInfo"`** keyvalues (`name`/`author`/`Version`/`tags`) — the
   `"Addon" { "title" ... }` shape you see in some tutorials is `gmad`'s script file, not this one.

## Verification

The harness (`tools/stub_gmod.lua` + `tools/smoke_test.lua`) is a fake GMod in plain Lua: Vector/Angle
with real metatables, `timer`, `hook`, `ents`, convars, `net` with **write/read type checking**, and
`render`/`surface`/`draw` stubs that fail on NaN or negative sizes and validate argument order against
the wiki signatures. It loads the addon as the server, fires both weapons *through the SWEPs*, steps
470 frames of fake clock, then reloads the addon as a client, replays the server's captured net traffic
through the real `net.Receive` handler, and steps the draw hooks.

32 checks, all green: exactly 100 zombies spawned and counted; `Sent == 7000` missiles launched; real
`TakeDamageInfo` hits and `util.BlastDamage` explosions; the exact lines "7 THOUSAND F*CKING MISSILES"
and "BLOW UP THIS MAN" broadcast, plus "100 ZOMBIE SPAWNER"; 30k streak sprites, text, dynamic lights
and validated garnish effects drawn; zero unknown engine methods.

**Not verified:** anything a running game would tell you — look, actual loudness and attenuation, frame
time with 7000 streaks on a weak GPU, NPC behaviour under a non-Sandbox gamemode, whether PNG icons
import cleanly on every install.

## Gotchas

1. **Sounds play at zero volume / nowhere.** **Cause:** `Entity:EmitSound` is `(name, level, pitch,
   volume, channel)`, and we passed `SNDCHAN_AUTO` (0) where level belongs. **Fix:** corrected order;
   global `EmitSound` got its own wrapper (`Util.PlayAt`) because its order differs again.
2. **`net type mismatch: wrote table, reader wants string`.** **Cause:** the server's announce message
   wrote a 4-bit kind, the client read 1 bit, and the state stream forgot its kind byte entirely.
   **Fix:** one framing rule (0 = announce + table, 1 = state + string + table), and the stub now errors
   on any write/read mismatch, which is how the class of bug stays dead.
3. **Particles silently never appear.** **Cause:** `ParticleEffect(name, pos, ang, 6)` — the 4th arg is
   a parent entity, and the named .pcf emitters are not precached; wrapped in `pcall`, so no error.
   **Fix:** dropped the count, and switched the garnish to GMod's default engine effects, which need no
   precaching. A real .pcf stays available via one config string.
4. **"7000" became 6973 and the server hitched.** **Cause:** the firing loop recomputed every already
   fired impact per tick (quadratic on the damaging subset), and the last partial batch was dropped when
   the window closed. **Fix:** track `self.Sent`, iterate only the new slice, and flush the remainder
   once the impacts window ends.
5. **`attempt to index a nil value (global 'EpicSWEPs')`.** **Cause:** modules under `lua/autorun`
   loaded in an unspecified order. **Fix:** one loader `include`s everything in an explicit order; each
   module also self-registers (`EpicSWEPs.Util = M`) so the order cannot strand a caller.
6. **100 zombies spawn inside the floor / in the sky.** **Cause:** spawning at the trace point without a
   hull trace. **Fix:** `util.TraceHull` to the ground with a player-sized hull, then a small drop so
   they land with visible weight (and `MOVETYPE_NONE` on the breach entity so nobody poses with the box).

## Assets

Icons were generated at high resolution and downscaled with Pillow to 128×128
(`materials/vgui/entities/<weapon_class>.png`); the two sprites (radial flash, vertical streak) ship as
alpha-luminance PNGs with `.vmt`s that use `$additive` + `$unlitmaterial`, and `build_materials.py` can
regenerate them procedurally with no Pillow and no AI (it writes the PNGs with `zlib` + `struct`
directly). All SFX are procedurally synthesised in pure Python — filtered noise for whoosh and debris,
pitch-dropping sines for boom and launch, tremolo whistle, two-tone siren, detuned portal hum, loopables
faded at both ends. Voice lines are TTS takes; each line is a separate short file so one blocked phrase
cannot take the others down. Nothing from Valve's content is copied in — only referenced by path.

## Cost and time

One session, all inside the sandbox: no game install, no paid assets. Pillow for icons, `fengari` (npm)
for the harness, everything else stdlib.

## Open questions

- Does the streak budget (`MaxMissilesDrawn`) need an FPS-based governor, or is "lower it" good enough?
- Should `100 ZOMBIE SPAWNER` also support a "spawn at every NPC in view" mode for Semi-Sideways servers?
- Worth shipping a `.pcf`-free "missile" model (a stretched `sprites/yellow_line` quad already reads
  fine) versus a real `.mdl` for the launcher, if someone wants a prop they can pose with?
- The engine `explosion` effect is avoided because it spawns client-side debris; confirm that claim in a
  real game before copying the pattern to another addon.
