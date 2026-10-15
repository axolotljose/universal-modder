# MODLOG — Garry's Mod: 100 ZOMBIE SPAWNER + 7000 F*CKING MISSILES

Working journal for this example. Becomes `knowledge/games/garrys-mod/epic-sweps-garrys-mod.md`.

## 2026-10-15 — intake

Ask: two GMod SWEP addons. (1) "100 zombie spawner": throwing a box summons 100 zombies with epic
visual effects. (2) "7 thousand f*cking missiles": the pigeon-meme shape — on use, on-screen text says
"7 thousand f*cking missiles" then "blow up this man", then that many missiles fall out of the sky and
blow everything up. AI voice if possible, otherwise the text carries it.

No game installed in this sandbox, so: build the addon as files, verify with an offline harness, and be
explicit in the README about what was not run in the real game.

## 2026-10-15 — route

Garry's Mod = Source 1 with a first-class Lua modding layer; loose addon folders under
`garrysmod/addons/` are the sanctioned route (see the `source` engine playbook). No binary patching, no
anti-cheat questions, nothing to bypass: single-player/local listen server.

Decisions up front:
- **One addon, two weapons.** Shared config/net/HUD/renderer, so nothing is duplicated; plus a packager
  that emits each weapon as its own standalone addon for people who only want one.
- **Never 7000 entities.** The barrage is one server entity + a deterministic client-side impact
  function seeded by a shared integer; only 7 % of impacts do damage. This is the design decision that
  separates a working meme from a crash.
- **Text first, audio as garnish.** Voice lines are an extra `EmitSound`; the HUD text is driven by the
  same timetable, so muting or deleting the wavs cannot break the joke.
- **Only our own assets.** Generated sprites + procedural SFX; Valve's sounds referenced by path only.

## 2026-10-15 — assets

- Voice: TTS through the agent's speech tool, 4 mono 44.1 kHz wav takes. The moderation pass blocked the
  full line with the second half, so each line is generated separately — the on-screen text keeps the
  wording the user asked for, the audio says the parts the TTS model will say.
- Icons: generated at 1254×1254, downscaled to 128×128 with Pillow to `materials/vgui/entities/`
  (GMod's own icon convention, so no code needed). Big sources live in `assets/src/`, gitignored.
- Sprites: `flash` (radial burst) and `missile_trail` (vertical streak), luminance packed into alpha
  for additive materials. `tools/build_materials.py` can regenerate both procedurally with no AI and no
  Pillow, so the addon's textures are reproducible from a stock Python.
- Sounds: `tools/make_sfx.py`, pure stdlib — filtered noise + pitch-dropping sine for the launch,
  transient/boom/rumble/debris for the explosion, tremolo whistle, two-tone siren, detuned portal hum.
  Loopables fade both ends so the loop seam is quiet.
- No models: default HL2 props (`cardboard_box004a`, citizen/zombie models) via `util.IsValidModel`
  probing, with a ragdoll fallback so the box works on any install.

## 2026-10-15 — verification

Built the fake-GMod harness (`tools/stub_gmod.lua` + `tools/smoke_test.lua`, run by node+fengari) because
"it parses" is not verification. It runs both realms, replays the server's net traffic into the client,
steps a fake clock through both timelines, and asserts: 100 zombies spawned, 7000 missiles fired,
damage + blast damage dealt, the exact required text lines broadcast, streaks/text/lights drawn with no
NaN or negative sizes, and every engine method called is one the stub models explicitly (invented-method
list is empty). 32 checks, 0 failures.

## Gotchas found (all four were real bugs, caught by the harness or the wiki)

1. `Entity:EmitSound( name, LEVEL, PITCH, VOLUME, CHANNEL, ... )` — the level comes *second*, not the
   channel. Wrong order = sounds at `SNDCHAN_AUTO`(0) interpreted as level 0, i.e. inaudible across the
   map. The global `EmitSound` has a *different* order again: `(name, pos, entity, channel, volume,
   level, flags, pitch)`. Now enforced by the stub.
2. `net` framing: server wrote a 4-bit kind then a string, client read 1 bit. Any mismatch here is a
   silent desync, so the stub type-checks reads against writes and errors on "wrote table, reader wants
   string".
3. `ParticleEffect(name, pos, ang)` has no quantity argument — arg 4 is an optional parent **Entity**.
   Passing a count errors, and inside `pcall` it fails silently, so the particles just never appear.
   Also: named .pcf systems need `game.AddParticles` + `PrecacheParticleSystem` server-side. Switched
   the garnish layer to GMod's *default engine effects* (`VortDispel`, `ThumperDust`, `ManhackSparks`,
   `StunstickImpact`), which need no precaching at all.
4. The missile firing loop recomputed every already-fired impact each tick — O(n²) with n=490 damaging
   impacts, plus duplicate damage. Track `self.Sent` and iterate only the new slice; also flush the
   remainder when the firing window closes, or "7000" quietly becomes 6973.

## 2026-10-15 — packaging + linters

`tools/build_materials.py --package` splits the folder into two standalone addons and one combined zip.
They land in `downloads/` (tracked) rather than `dist/` (ignored repo-wide by the root `.gitignore`, and
`dist/` inside a directory is not negatable from a nested `.gitignore`) — the zips are the deliverable for
anyone who will not run the script, and `gh release` asset upload is not reachable from a sandbox that only
allows github.com/api.github.com, so in-repo was the only honest way to hand out a download link.

each zip puts `epic_zombie_spawner/` (or `epic_7000_missiles/`) at its root so unzipping into
`garrysmod/addons/` installs it, and both halves carry the shared modules so no `include` 404s. The
`addon.txt` in an addon folder is the **AddonInfo** keyvalues schema (`name`, `author`, `Version`,
`tags`); the `"Addon" { "title" ... }` shape is `gmad`'s script file — easy to mix up.

Two static linters joined the harness (`node tools/lint_lua.js epic_sweps/lua`,
`node tools/lint_symbols.js epic_sweps`). The symbol one earned its keep immediately, and taught me the
lesson about heuristics: my first version inferred aliases from any `EpicSWEPs.X = ...` text and happily
"found" `pos = EpicSWEPs.Zombies` inside `local pos = EpicSWEPs.Zombies.GroundPos(o)` — every regex has
to be anchored to a whole statement, or the tool cries wolf and gets ignored.

## Not verified

Never booted in the real game. Open: how loud the attenuated explosions actually are, whether the icons
show (PNG auto-import is documented but TGA is the safe path), how the horde behaves on non-Sandbox
gamemodes, and frame time with 7000 streaks on a weak GPU (`MaxMissilesDrawn` is the dial).

## 2026-10-15 (later) — "double check the lua, I think there are errors"

There were. Not in the parse sense (`ALL 15 LUA FILES PARSE` was already true) — in the "runs until
somebody looks at the gun" sense. Two new static checks plus a cold-path phase in the smoke test:

- `tools/lint_globals.js` dumps the *stub's own environment* and diffs every global the addon reads
  against it. It reported `CHAN_AUTO`/`CHAN_VOICE` as unmodelled, which sent me to the wiki, which said
  the `SNDLVL_*` enum is **not in GMod's Lua at all** ("use the raw number values"). The addon was passing
  `SNDLVL_GUNFIRE` (→ nil) into `EmitSound` as the soundlevel, and `SNDCHAN_AUTO` (a name invented in this
  repo, never in GMod) as the channel. The stub had *defined both*, which is why 32/32 was green: my
  harness was more generous than the game. Constants now live in `Util.CHAN`/`Util.LVL` with the wiki's
  numbers, the invented ones are deleted from the stub, and `EmitSound` rejects a nil between two values.
- Cold-path phase → `util.comma(count)` in `SWEP:DrawHUD`. Real name is `string.Comma` (found by reading
  `garrysmod/lua/includes/extensions/string.lua` off GitHub, since Lua-side helpers have no wiki page).
  It would have thrown once per frame, on the client, at the one moment the player is definitely looking.
- Same phase → `EpicSWEPs.Util.Impacts(centre, nil, ...)` from a partial state message: nil arithmetic
  inside `HUDPaint`. Now the renderer waits for pos+radius+start before computing, and `Impacts` refuses
  to do maths on missing inputs.
- `tools/lint_assets.js` → the soundscript had `"level"` instead of `"soundlevel"`, four sibling `wave`
  lines with no `rndwave` wrapper (KeyValues keeps the last), and `"channel" "1"`, which is CHAN_WEAPON,
  not CHAN_STATIC. Fixed all three; the comma pitch form `"96,106"` turned out to be legal after all, which
  is why every claim here cites the file or page it came from.
- Also dropped `FCVAR_REPLICATED` from the three presentation toggles: replicated + archived means the
  server rewrites your saved `epic_show_text 0` on every connect.

49/49 green, four linters clean, `npm run lint` now runs all of them. The reusable lesson is in the field
note under "a harness that is more forgiving than the game is worse than no harness".
