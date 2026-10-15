# Retro and console games: decomps, recomps, emulators

The user must own the game and dump it themselves. Never download ROMs, ISOs or leaked source. Projects
built on leaked material get shunned ("leak poisoning"); clean decomps (libsm64's) get adopted.

## Is there already a decompilation or port?
Many classics have matching decompilations (C source that compiles back to the identical ROM) and native PC
ports built on them:
- Super Mario 64: `sm64` decomp, `sm64coopdx` (Lua mods, online co-op), libsm64 (SM64 as a library other
  engines embed; G64 embeds it into Garry's Mod);
- Ocarina of Time / Majora's Mask: decomps, Ship of Harkinian, 2 Ship 2 Harkinian;
- Zelda64Recomp;
- Twilight Princess (Dusklight);
- Super Mario Sunshine, Mario Kart 64, Pokémon (pret: pokered, pokeemerald...), Sonic, Metroid...

Search "<game> decomp" / "<game> recomp" / "<game> pc port" first. Modding a decomp means editing C (or the
port's Lua API) and building with your own ROM as the asset source. Examples: Minecraft inside Mario 64 was
built on sm64coopdx, and a new Majora's Mask area was built by Opus on the MM recomp.

## Static recompilation (no source, still native)
Recompilers translate machine code into C that links against a runtime reimplementing the console:
- N64: N64Recomp + N64ModernRuntime (+ RT64 renderer);
- Xbox 360: XenonRecomp / ReXGlue;
- PS2: PS2Recomp;
- GameCube/Wii: DolRecomp;
- PS1: psxrecomp / RecompOne.

A recomp is also the best **oracle** for a clean reimplementation: run or read it to get ground truth, then
write an idiomatic engine (the Skate 3 Rust engine was built this way).

## Starting a decompilation with agents
Tools:
- splat splits the ROM into asm/data;
- m2c gives a first-pass C decompile;
- asm-differ / objdiff compare your compiled function to the original;
- decomp-permuter searches for matching code shapes;
- decomp.me shares scratches.

The agent loop that worked in public write-ups (one N64 game: 2,145 functions in 84 days):
- one function at a time, with a `build-and-verify` script that says exactly how close it is;
- each attempt in its own file, with a hard cap (about 10 attempts), then move on and log it;
- commit every match, and keep a shared `LEARNINGS.md`;
- rank candidates cheapest first (instructions, branches, jumps);
- run several worktrees in parallel, and try another model on the hard ones.

"Pure mechanical decompilation first; modernization/portability afterwards." Budget for it: a large
decomp can take billions of tokens.

## Emulator-based mods (no source needed)
- **Memory scripting:** BizHawk Lua, mGBA Lua/scripting, Dolphin Memory Engine, PCSX2 PINE, RetroArch
  cheats. Find addresses with RAM search. mgba-mcp / PCSX2-MCP expose this to agents.
  - **PCSX2/PINE specifics** (learned building the Ape Escape 3 co-op mod, field note in
    `knowledge/games/ape-escape-3/`): a request is `u32 length` + commands and a reply is `u32 size`,
    `u8 result`, payload; thousands of commands batch into one round trip, capped at 650000 bytes of
    request and 450000 of reply; one client at a time (a second one is accepted and never read, so it
    times out); after a `0xFF` the server resets, so reconnect rather than resync. A host-side writer
    loses every race against the engine's own per-frame state -- put the effect *inside* the game
    (a code cave reached by redirecting one `jal` in a per-frame path) and keep the policy outside.
  - **The write half of discovery.** Reading memory finds what a value *is*; only writing
    finds what it *does*. Two tools carry most of the weight: (1) snapshot a region to a file,
    change the game state, snapshot again, and diff narrowed to "words that differ and are
    small integers" -- the two states worth comparing (a level versus a menu, one view versus
    two) are usually minutes and a transition apart, so they cannot be diffed from one
    process; (2) a "poke" that writes candidate values *every frame* for a few seconds and
    then restores everything. A one-shot write cannot distinguish "the engine responded" from
    "the engine overwrote it while you were looking", and it is the difference between a
    value that works in a menu and one that works in gameplay. Make the poke refuse to run
    on top of an installed cave and restore in a `finally`.
  - **A screen split usually has two levers, and the cheaper one is not the flag.**
    Engines that "can only draw one view" often already draw a second one somewhere:
    a picture-in-picture camera, a minimap, a scope. That proves the renderer takes
    a *rectangle* at runtime, which is the same structure a split needs -- so the
    fallback when a view-mode flag is ignored is to write the rectangles yourself
    (`x`, `y`, `w`, `h` per view, plus the count). Find them by poking the
    picture-in-picture's size while it is on screen and watching what moves, and
    write nothing unless all four fields are measured: an `x`/`y` move with a stale
    `w`/`h` draws the old view in a new place, which reads as a broken renderer
    rather than a missing number.
  - **A write sets state; only the game's own code constructs anything.** There is a
    ceiling to memory-poking that shows up at the same place in every project: you can
    force `actor_count` to two, but if the engine never built a second actor structure,
    there is no second actor -- and re-writing a "view mode" word does nothing if the
    renderer recomputes its layout from something else. Both are *doings*, and the fix is
    to call the function the game calls: queue the address plus one argument for a small
    in-game stub to `jal`, with the argument already in `$a0`. Three rules make that safe.
    Take a call **once**, not every frame -- a call is the opposite of a write, and
    "spawn the partner" run sixty times a second is sixty partners: have the stub consume
    the slot (zero the function word) and have the host re-send only the calls it has read
    back as *not yet* consumed, which is also the only honest evidence that a call
    happened. Refuse a call whose argument has not been measured -- jumping into code you
    found while handing it a value you invented is how a stage crashes -- and make the
    function word the only part of the slot the stub clears, so the host can tell "queued"
    from "done" at a glance.
  - **The last step of every address hunt is a human judgement, so build the loop that
    asks.** Snapshot, diff and poke get you a candidate list; deciding which candidate is
    *the* one is eyes-on-the-screen work that no tool can do. Left alone it becomes twenty
    commands with the same five arguments, which is how a measurable question turns into an
    afternoon. Put the loop in the tool and *inject* the judgement
    (`try_candidates(..., ask=...)` where `ask` is a callback): the CLI supplies one that
    prompts, tests supply a script, and then everything around the human -- where the run
    stops, what happens to a candidate that cannot be tried, whether each poke is really
    undone before the next -- becomes assertable. Print every candidate as it is tried, not
    just the winner, so a redirected log is the record of what was ruled out.
  - **The person measuring is often not at the machine measuring.** Anything that needs
    a running emulator to measure also needs the measurement to travel: to a chat, to a
    phone, to whoever is helping. Build the hand-off and make it *structural* -- a
    command whose whole stdout is one small ASCII payload, built only from your
    worksheet and the emulator's metadata (serial, title, CRC, run state), so there is
    no code path that could put a game file, a savestate or a decompiled listing into
    it. Summarise anything sensitive as a count (a patch journal becomes "3 writes at
    <addr>", never its bytes), wrap lines to phone width, keep it under a few
    kilobytes, and provide the reverse direction too (`set --file` to apply a batch of
    measurements in one command). One more hard limit worth stating up front: IPC APIs
    like PCSX2's PINE bind to localhost only and have no Android equivalent, so a
    phone-only setup cannot be hooked -- the game must run on a desktop emulator.
  - **Gate every optional write on a measured address.** A mod that pokes state the engine
    owns (HUD placement, camera targets, viewport count) should degrade mode by mode -- and
    say which field it is waiting on -- rather than guessing a plausible number. Guessing
    turns "the mod did nothing" into "the mod moved something nobody was looking at and the
    save no longer loads". The same rule applies to *evidence*: before copying what a
    minigame does, check that it does it (one project spent a design on "the minigame renderer
    already draws two views" before finding that the game's two-player minigames are all
    shared-screen, and that the second view it needed was the story mode's own
    picture-in-picture camera).
- **ROM hacks:**
  - patch code or data in the ROM and distribute an IPS/BPS/xdelta patch, never the ROM;
  - existing editors: Lunar Magic (SMW), Wiimm's SZS tools (Mario Kart Wii tracks), Pokémon hacking tools.
- **Dolphin:** texture packs, Gecko/AR codes, Riivolution patches for GameCube/Wii.

## Pitfalls
- Some recomp/decomp communities ban AI-generated contributions. Respect each project's rules and disclose
  AI use.
- Publishers do DMCA recomps and ports even without assets. Keep projects non-commercial; ship patches and
  tools only.
