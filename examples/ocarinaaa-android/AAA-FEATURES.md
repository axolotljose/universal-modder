# OcarinAAA feature layer — reverse-engineering notes

Status: **intel only, no feature code ported yet.** Compiled 2026-11-10 from
`strings` output of `OcarinAAA.exe` (25 MB, SoH 9.2.3 "Ackbar Delta" fork,
build path `mod/build/aaa-ocarina-of-time`, C++ namespace `AAAOcarinaOfTime`),
plus the repo's data files. The desktop mod's C++ source is not public — these
notes are the basis for re-implementing features against the SoH 9.0.2 engine
in `Shipwright-Android/`.

## 1. Feature inventory

All features register settings as CVars under `gMods.AAAOcarinaOfTime.*`
(346 distinct names; full list in the appendix). Engine-side, SoH loads CVars
from `shipofharkinian.json`; `InitMods()` in
`soh/soh/Enhancements/mods.cpp:1070` (called from `OTRGlobals.cpp:1247`) is
the natural registration point for an `InitAAA()` hook.

| Feature | CVars | What it does (from strings) | Data deps | Port difficulty |
|---|---|---|---|---|
| Hud | 116 | Fully configurable HUD: per-element anchors/scales/assets (A/B/C buttons, magic, hearts, rupees, minimap…) | `OcarinAAA.o2r` textures | Medium — layout math + SoH HUD draw hooks |
| OpeningDialogue | 102 | THE dialogue engine: voice-acted conversations, subtitles (font/colors/reading time/answer choices), auto-speak range, per-character settings (Saria, Shopkeeper, Navi…), F1 settings overlay with live preview | **`aaa-dialogue.pack`** | Hard — needs pack format + textbox hooks |
| QuestPanel | 54 | Quest-log style panel (bullets, offsets, width scales) | `OcarinAAA.o2r` | Medium |
| Reactions | 27 | Navi/Link contextual reactions: proximity hints for chests/rupees/torches/vines/enemies (range + chance + debug radius overlays), greetings with delays (Deku, Gohma title/victory/credits), idle timer, repeat-item chance | **`aaa-dialogue.pack`** (clip recordings) + `aaa-subtitles/` WAVs | Hard — pack + Navi actor state machine |
| NorthPointer | 16 | Compass/north arrow, aim-at-height, RGBA color | `OcarinAAA.o2r` | Medium |
| PostProcessing | 10 | Bloom (radius/strength/threshold), DepthOfField (focus), … | shaders likely in `OcarinAAA.o2r` | Medium |
| NavigationBar | 9 | Direction bar with `DotsPerQuarter`, debug mode, art version | `OcarinAAA.o2r` | Medium |
| LocationDiscovery | 6 | Area-name banner on first entry (delay/scale/offset) | `OcarinAAA.o2r` | Medium — needs area-change hook |
| SoundEffects | 2 | `PlayDuringConversations`, `MuteTalkingSpeaker` — gates for the WAV effect player | WAVs | Trivial flags, effects need pack |
| TitleLogo | 1 | `TitleLogo.Enabled` custom title logo | `OcarinAAA.o2r` | Trivial |
| Credits | 1 | `Credits.MusicGainDb` + data-driven player | **`aaa-credits/sequence.json`** | Medium — self-contained, best first target |

Pack-independent candidates (doable without `aaa-dialogue.pack`): **Credits**,
**LocationDiscovery**, **NorthPointer**, **NavigationBar**, **TitleLogo**,
parts of **Hud**.

## 2. `aaa-dialogue.pack` format (from error strings)

- Not fetched here: Git LFS pointer in repo, oid
  `sha256:c0b976298890474610aa9dcae013e979d084669525d723dce3dbaf883e539913`,
  size **173,001,557** bytes. Download on a normal machine:
  `git lfs pull` in a clone of `null-port/OcarinAAA-of-Time`, or grab the
  release zip `OcarinAAA_of_Time_v1.0.zip`.
- Binary container with magic/version string **`AAAVO001`**.
- Header fields implied by errors: *document count* ("Invalid dialogue pack
  document count"), *version* ("Invalid dialogue pack version"), per-entry
  *entry sizes* ("Invalid dialogue pack entry sizes"), then **metadata** and
  **recording** blobs per entry ("Truncated dialogue pack metadata /
  recording / Truncated dialogue pack").
- Recordings are **deflate-compressed** (zlib 1.3.1 linked into the exe).
- Documents are **JSON parsed with nlohmann::json** (its exact diagnostic
  strings are in the exe: "cannot create object from initializer list",
  "syntax error while parsing", BJData/UBJSON/MessagePack/BSON errors…).
- Companion file: **`navi-flight-cues.json`** ("Invalid packed Navi's Flight
  cue", `flight_start_ms`) — Navi fly-to-hint choreography.
- Runtime classes visible in symbols:
  `AAAOcarinaOfTime::Dialogue::Clips::Register/Preload/Report/ActorUnavailable`,
  preload log: "Preloaded baked dialogue: {{}} conversations, {{}} recordings",
  per-line log: "Subtitle clip: {{}}", clip bound to an actor
  ("Subtitle clip stopped (bound actor removed)").
- Document/cue schema hints: cues are `{text | runs}` (rich text runs),
  messages carry `message_id`, event arrays with `target/action/args`,
  ordering + IDs + times, "Native checkpoint guard" integers, answer choices
  (matches `Subtitles.AnswerColor/AnswerGap/AnswerOffset` CVars).
- Named gameplay documents (scripted modern quest flow — each is a
  conversation/cue id): `mido.allow_passage`, `mido.0177.cue.000`,
  `mido.return_to_hands_on_hips`, `saria_reminder_complete`,
  `shop_browse`, `options.shop.main_menu/purchase/continue_shopping`,
  `options.deku_tree.accept_task`, `scrub.begin_departure/finish_puzzle_departure`,
  `kokiri_rocks`, `kokiri_awning`, `kokiri.training.*`, `kokiri.grass.*`,
  `navi.complete_hint_spot`, `navi.acknowledge_hint`,
  `deku_tree_crawling_link_grunt`, plus "Missing Saria reminder cue/handoff/
  completion", "Missing Deku Tree request handoff", "Missing return-message
  checkpoint".
- Settings UI strings (desktop uses an ImGui overlay, F1): "Dialogue tab >
  Subtitles", "Show sample subtitles while F1 is open", "Navi has an
  independent line, position, scale and background", "Navi and cinematic
  dialogue use the same character colors and subtitle styling".

## 3. Reactions = Navi proximity hint system

CVar cluster shows proximity *range* + *chance* pairs with debug overlays:

- Chests: `ChestRange`, `ChestNearChance`, `ChestHalfHeight`
- Rupees: `RupeeRange`, `RupeeNearChance`, `RupeePickupChance`
- Torches: `TorchRange`, `TorchNearChance`, `ShowTorchRadii`
- Vines: `VineRange`, `ShowVineRadii`
- Enemies: `EnemyTargetChance`, `EnemyDefeatChance`, `SlingshotChance`
- Misc: `RepeatItemChance`, `IdleSeconds`, `ShowRadii`
- Greetings/timing: `DekuGreetingDelay`, `DekuGuardianSeconds`,
  `DoorSuccessDelay`, `GohmaGreetingDelay`, `GohmaTitleDelay`,
  `GohmaVictoryDelay`, `GohmaCreditsDelay`, `GohmaFocusSeconds`
- Master switch: `Reactions.Enabled`

`GohmaCreditsDelay` + `sequence.json.trigger = "gohma_blackout"` together
suggest credits are triggered from the Gohma fight blackout (Deku Tree boss).

## 4. `aaa-credits/sequence.json` (bundled, fully readable)

Top-level: `version` (1), `trigger` ("gohma_blackout"), `duration_ms`
(105000), `layers` (26), `events` (1), `ending`, `media`.

- **layers**: timed text layers — `{id, type, name, start_ms, end_ms, text,
  x, y, width, height, font_size, line_spacing, align, color "#rrggbb",
  opacity, fade_in_ms, fade_out_ms}` (e.g. "developed by nullport").
- **events**: `{id, type:"event", action, target, at_ms}` — e.g.
  `clear_current_save` on `active_slot` at t=0.
- **media**: named entries with `source` referencing ROM-derived resources:
  `credits-music` → `rom-hyrule-field-running`, a title-logo entry →
  `rom-title-logo` (i.e. play an OoT audio sequence + show a ROM texture
  through the SoH resource/audio systems).
- **ending**: subtitle-style choice UI — style `deku_tree_subtitle_choice`,
  text "Give it another go?", choices Yes → `title_screen`, No → `quit_game`
  (positions are in the 320×240 N64 coordinate space: x=213.33, y=120).

## 5. Shipped WAV effects (`aaa-subtitles/audio/`, 39 files)

Played by the reactions/conversation effect player ("WAV effects do not
create subtitles"; `SoundEffects.PlayDuringConversations` gates them).

- Link: `sfx_link_attack_1..4`, `strong_attack_1..2`, `hurt_1..5`,
  `knocked_back`, `falling_1..2`, `fall_damage`, `gasp_1..3`,
  `dangling_gasp_1..2`, `dying_gasp`, `exhausted_panting`, `wheeze`,
  `hup_1..2`, `sigh_1..2`, `refreshed`, `shiver`, `strangled`,
  `stretch_refreshingly`, `yawn_starts`, `yawn_and_stretch`
- Navi: `sfx_navi_hello`, `hey`, `listen`, `look`, `watch_out`,
  `collision_reaction`

## 6. Integration points in the 9.0.2 engine (to verify while porting)

- Mod init: `InitMods()` (`soh/soh/Enhancements/mods.cpp:1070`) — add
  `InitAAA()` here; CVars live in `shipofharkinian.json`.
- WAV playback: SDL2 audio (`SDL_LoadWAV` + `SDL_OpenAudioDevice`) is
  self-contained and works on Android — avoid depending on SoH internals for
  the effects player.
- Drawing: ImGui is bundled via libultraship; SoH menus register frame
  callbacks (find the pattern used by e.g. the Audio Editor) for subtitle/
  banner/HUD overlay rendering.
- Pack/dialogue work additionally needs SoH textbox state
  (`Message_GetState`/`z_message.c` territory) and Navi actor internals
  (`z_en_nav*`/actor update) — plan RE time accordingly.

## 7. Appendix — full CVar inventory (346)

Grouped by feature; every name is prefixed `gMods.AAAOcarinaOfTime.`.


### Hud (116)

```
Hud.
Hud.AButton.
Hud.AButton.AnchorX
Hud.AButton.AnchorY
Hud.AButton.Asset
Hud.AButton.AssetScale
Hud.AButton.Color.B
Hud.AButton.Color.G
Hud.AButton.Color.R
Hud.AButton.ColorOverride
Hud.AButton.OffsetX
Hud.AButton.OffsetY
Hud.AButton.Opacity
Hud.AButton.Override
Hud.AButton.TextLeftAlign
Hud.AButton.TextX
Hud.BButton.AnchorX
Hud.BButton.AnchorY
Hud.BButton.AssetScale
Hud.BButton.Color.B
Hud.BButton.Color.G
Hud.BButton.Color.R
Hud.BButton.ColorOverride
Hud.BButton.IconX
Hud.BButton.OffsetX
Hud.BButton.OffsetY
Hud.BButton.Opacity
Hud.BButton.Override
Hud.BButton.TextX
Hud.CDownButton.AnchorX
Hud.CDownButton.AnchorY
Hud.CDownButton.Color.B
Hud.CDownButton.Color.G
Hud.CDownButton.Color.R
Hud.CDownButton.ColorOverride
Hud.CDownButton.IconX
Hud.CDownButton.OffsetX
Hud.CDownButton.OffsetY
Hud.CDownButton.Opacity
Hud.CDownButton.Override
Hud.CDownButton.TextX
Hud.CLeftButton.AnchorX
Hud.CLeftButton.AnchorY
Hud.CLeftButton.Color.B
Hud.CLeftButton.Color.G
Hud.CLeftButton.Color.R
Hud.CLeftButton.ColorOverride
Hud.CLeftButton.IconX
Hud.CLeftButton.OffsetX
Hud.CLeftButton.OffsetY
Hud.CLeftButton.Opacity
Hud.CLeftButton.Override
Hud.CLeftButton.TextX
Hud.CRightButton.AnchorX
Hud.CRightButton.AnchorY
Hud.CRightButton.Color.B
Hud.CRightButton.Color.G
Hud.CRightButton.Color.R
Hud.CRightButton.ColorOverride
Hud.CRightButton.IconX
Hud.CRightButton.OffsetX
Hud.CRightButton.OffsetY
Hud.CRightButton.Opacity
Hud.CRightButton.Override
Hud.CRightButton.TextX
Hud.CUpButton.AnchorX
Hud.CUpButton.Override
Hud.Dialogue.
Hud.Dialogue.OffsetX
Hud.Dialogue.OffsetY
Hud.Dialogue.Override
Hud.Dialogue.Scale
Hud.EnemyHealth.
Hud.HealthBar.
Hud.HealthBar.Color.B
Hud.HealthBar.Color.G
Hud.HealthBar.Color.R
Hud.HealthBar.OffsetX
Hud.HealthBar.OffsetY
Hud.HealthBar.Opacity
Hud.HealthBar.Scale
Hud.HealthValue.
Hud.HealthValue.AnchorX
Hud.HealthValue.OffsetX
Hud.HealthValue.OffsetY
Hud.HealthValue.Scale
Hud.Minimap.
Hud.Minimap.ArtworkColor.B
Hud.Minimap.ArtworkColor.G
Hud.Minimap.ArtworkColor.R
Hud.Minimap.ContentZoom
Hud.Minimap.CutoffOffsetX
Hud.Minimap.CutoffOffsetY
Hud.Minimap.OffsetX
Hud.Minimap.OffsetY
Hud.Minimap.Override
Hud.Minimap.PlayerColor.A
Hud.Minimap.PlayerColor.B
Hud.Minimap.PlayerColor.G
Hud.Minimap.PlayerColor.R
Hud.Minimap.PlayerColorOverride
Hud.Minimap.PlayerScale
Hud.Minimap.RenderResolution
Hud.Minimap.Scale
Hud.NaviDialogue.
Hud.NaviDialogue.OffsetY
Hud.NaviDialogue.Override
Hud.Rupees.Model.
Hud.Rupees.Model.Exposure
Hud.Rupees.Model.Preview.Background
Hud.Rupees.OffsetX
Hud.Rupees.OffsetY
Hud.Rupees.Override
Hud.StartButton.FollowActionColumn
Hud.StartButton.InstantAppearance
Hud.StartButton.RowSpacing
```

### OpeningDialogue (102)

```
OpeningDialogue.
OpeningDialogue.AutoSpeakRangeOffset
OpeningDialogue.BaseHoldSeconds
OpeningDialogue.Characters.
OpeningDialogue.Characters.Saria
OpeningDialogue.Characters.Shopkeeper
OpeningDialogue.ChoiceStick
OpeningDialogue.Cutscenes.Enabled
OpeningDialogue.Enabled
OpeningDialogue.FocusEditor
OpeningDialogue.Navi.Enabled
OpeningDialogue.Navi.ShowTriggers
OpeningDialogue.RangeDebug.Enabled
OpeningDialogue.RangeDebug.ThroughWalls
OpeningDialogue.RangeDebug.Vertical
OpeningDialogue.ShowBackground
OpeningDialogue.ShowTextShadow
OpeningDialogue.Subtitles.
OpeningDialogue.Subtitles.AnswerColor.B
OpeningDialogue.Subtitles.AnswerColor.G
OpeningDialogue.Subtitles.AnswerColor.R
OpeningDialogue.Subtitles.AnswerGap
OpeningDialogue.Subtitles.AnswerOffset
OpeningDialogue.Subtitles.ArrowColor.B
OpeningDialogue.Subtitles.ArrowColor.G
OpeningDialogue.Subtitles.ArrowColor.R
OpeningDialogue.Subtitles.BackgroundOpacity
OpeningDialogue.Subtitles.Characters.BrotherCUp.B
OpeningDialogue.Subtitles.Characters.BrotherCUp.G
OpeningDialogue.Subtitles.Characters.BrotherCUp.R
OpeningDialogue.Subtitles.Characters.BrotherIcons.B
OpeningDialogue.Subtitles.Characters.BrotherIcons.G
OpeningDialogue.Subtitles.Characters.BrotherIcons.R
OpeningDialogue.Subtitles.Characters.BrotherMapItems.B
OpeningDialogue.Subtitles.Characters.BrotherMapItems.G
OpeningDialogue.Subtitles.Characters.BrotherMapItems.R
OpeningDialogue.Subtitles.Characters.Fado.B
OpeningDialogue.Subtitles.Characters.Fado.G
OpeningDialogue.Subtitles.Characters.Fado.R
OpeningDialogue.Subtitles.Characters.GreatDekuTree.B
OpeningDialogue.Subtitles.Characters.GreatDekuTree.G
OpeningDialogue.Subtitles.Characters.GreatDekuTree.R
OpeningDialogue.Subtitles.Characters.KokiriAwning.B
OpeningDialogue.Subtitles.Characters.KokiriAwning.G
OpeningDialogue.Subtitles.Characters.KokiriAwning.R
OpeningDialogue.Subtitles.Characters.KokiriExit.B
OpeningDialogue.Subtitles.Characters.KokiriExit.G
OpeningDialogue.Subtitles.Characters.KokiriExit.R
OpeningDialogue.Subtitles.Characters.KokiriGrass.B
OpeningDialogue.Subtitles.Characters.KokiriGrass.G
OpeningDialogue.Subtitles.Characters.KokiriGrass.R
OpeningDialogue.Subtitles.Characters.KokiriGuardian.B
OpeningDialogue.Subtitles.Characters.KokiriGuardian.G
OpeningDialogue.Subtitles.Characters.KokiriGuardian.R
OpeningDialogue.Subtitles.Characters.KokiriRocks.B
OpeningDialogue.Subtitles.Characters.KokiriRocks.G
OpeningDialogue.Subtitles.Characters.KokiriRocks.R
OpeningDialogue.Subtitles.Characters.KokiriTraining.B
OpeningDialogue.Subtitles.Characters.KokiriTraining.G
OpeningDialogue.Subtitles.Characters.KokiriTraining.R
OpeningDialogue.Subtitles.Characters.KokiriTrainingGuide.B
OpeningDialogue.Subtitles.Characters.KokiriTrainingGuide.G
OpeningDialogue.Subtitles.Characters.KokiriTrainingGuide.R
OpeningDialogue.Subtitles.Characters.Link.B
OpeningDialogue.Subtitles.Characters.Link.G
OpeningDialogue.Subtitles.Characters.Link.R
OpeningDialogue.Subtitles.Characters.Mido.B
OpeningDialogue.Subtitles.Characters.Mido.G
OpeningDialogue.Subtitles.Characters.Mido.R
OpeningDialogue.Subtitles.Characters.Navi.B
OpeningDialogue.Subtitles.Characters.Navi.G
OpeningDialogue.Subtitles.Characters.Navi.R
OpeningDialogue.Subtitles.Characters.Saria.B
OpeningDialogue.Subtitles.Characters.Saria.G
OpeningDialogue.Subtitles.Characters.Saria.R
OpeningDialogue.Subtitles.Characters.ScrubBasement.B
OpeningDialogue.Subtitles.Characters.ScrubBasement.G
OpeningDialogue.Subtitles.Characters.ScrubBasement.R
OpeningDialogue.Subtitles.Characters.ScrubPuzzle.B
OpeningDialogue.Subtitles.Characters.ScrubPuzzle.G
OpeningDialogue.Subtitles.Characters.ScrubPuzzle.R
OpeningDialogue.Subtitles.Characters.ScrubUpper.B
OpeningDialogue.Subtitles.Characters.ScrubUpper.G
OpeningDialogue.Subtitles.Characters.ScrubUpper.R
OpeningDialogue.Subtitles.Characters.Shopkeeper.B
OpeningDialogue.Subtitles.Characters.Shopkeeper.G
OpeningDialogue.Subtitles.Characters.Shopkeeper.R
OpeningDialogue.Subtitles.Characters.TwinHouse.B
OpeningDialogue.Subtitles.Characters.TwinHouse.G
OpeningDialogue.Subtitles.Characters.TwinHouse.R
OpeningDialogue.Subtitles.Characters.TwinShop.B
OpeningDialogue.Subtitles.Characters.TwinShop.G
OpeningDialogue.Subtitles.Characters.TwinShop.R
OpeningDialogue.Subtitles.OutlineWidth
OpeningDialogue.Subtitles.Padding
OpeningDialogue.Subtitles.Preview
OpeningDialogue.Subtitles.PreviewAnswerCount
OpeningDialogue.Subtitles.ShadowOffset
OpeningDialogue.Subtitles.Spacing
OpeningDialogue.Subtitles.UseCharacterColors
OpeningDialogue.Subtitles.Width
OpeningDialogue.VoiceGainDb
```

### QuestPanel (54)

```
QuestPanel.
QuestPanel.AnchorX
QuestPanel.AnchorY
QuestPanel.Bullets.Enter.OffsetX
QuestPanel.Bullets.Equip.OffsetX
QuestPanel.Bullets.Equip.WidthScale
QuestPanel.Bullets.Explore.OffsetX
QuestPanel.Bullets.Explore.WidthScale
QuestPanel.Bullets.Find.WidthScale
QuestPanel.Bullets.Gohma.OffsetX
QuestPanel.Bullets.Gohma.WidthScale
QuestPanel.Bullets.Mido.Height
QuestPanel.Bullets.Mido.OffsetX
QuestPanel.Bullets.Mido.WidthScale
QuestPanel.Bullets.Saria.Height
QuestPanel.Bullets.Saria.OffsetX
QuestPanel.Bullets.Saria.WidthScale
QuestPanel.Heading
QuestPanel.Headings.InsideJob.OffsetY
QuestPanel.Headings.InsideJob.Scale
QuestPanel.Headings.InsideJob.Width
QuestPanel.Headings.Woods.ShadowOffset
QuestPanel.Height
QuestPanel.Line1.FitToPanel
QuestPanel.Line1.OffsetY
QuestPanel.Line1.Scale
QuestPanel.Line1.ShadowOffset
QuestPanel.Line1.ShadowOpacity
QuestPanel.Line2.Color.A
QuestPanel.Line2.Color.B
QuestPanel.Line2.Color.G
QuestPanel.Line2.Color.R
QuestPanel.Line2.FitToPanel
QuestPanel.Line2.Height
QuestPanel.Line2.LeftAlign
QuestPanel.Line2.OffsetX
QuestPanel.Line2.OffsetY
QuestPanel.Line2.Scale
QuestPanel.Line2.ShadowOffset
QuestPanel.Line2.ShadowOpacity
QuestPanel.Line2.WidthScale
QuestPanel.Objective
QuestPanel.Objectives.Find.OffsetX
QuestPanel.Objectives.Mido.OffsetX
QuestPanel.Objectives.Saria.OffsetX
QuestPanel.OffsetX
QuestPanel.OffsetY
QuestPanel.PanelColor.A
QuestPanel.PanelColor.B
QuestPanel.PanelColor.G
QuestPanel.PanelColor.R
QuestPanel.Scale
QuestPanel.SharedObjectiveStyleVersion
QuestPanel.Width
```

### Reactions (27)

```
Reactions.ChestHalfHeight
Reactions.ChestNearChance
Reactions.ChestRange
Reactions.DekuGreetingDelay
Reactions.DekuGuardianSeconds
Reactions.DoorSuccessDelay
Reactions.Enabled
Reactions.EnemyDefeatChance
Reactions.EnemyTargetChance
Reactions.GohmaCreditsDelay
Reactions.GohmaFocusSeconds
Reactions.GohmaGreetingDelay
Reactions.GohmaReflectionDelay
Reactions.GohmaTitleDelay
Reactions.GohmaVictoryDelay
Reactions.IdleSeconds
Reactions.RepeatItemChance
Reactions.RupeeNearChance
Reactions.RupeePickupChance
Reactions.RupeeRange
Reactions.ShowRadii
Reactions.ShowTorchRadii
Reactions.ShowVineRadii
Reactions.SlingshotChance
Reactions.TorchNearChance
Reactions.TorchRange
Reactions.VineRange
```

### NorthPointer (16)

```
NorthPointer.
NorthPointer.AimAtHeight
NorthPointer.Color.A
NorthPointer.Color.B
NorthPointer.Color.G
NorthPointer.Color.R
NorthPointer.Enabled
NorthPointer.Height
NorthPointer.OffsetZ
NorthPointer.Pickup.FadeOutSeconds
NorthPointer.Rear.Tilt
NorthPointer.Shading.Enabled
NorthPointer.Shading.LightElevation
NorthPointer.Shading.LightYaw
NorthPointer.Shading.Strength
NorthPointer.Size
```

### PostProcessing (10)

```
PostProcessing.Bloom.Enabled
PostProcessing.Bloom.Radius
PostProcessing.Bloom.Strength
PostProcessing.Bloom.Threshold
PostProcessing.DepthOfField.Enabled
PostProcessing.DepthOfField.Focus
PostProcessing.DepthOfField.Radius
PostProcessing.DepthOfField.Range
PostProcessing.MotionBlur.Enabled
PostProcessing.MotionBlur.Strength
```

### NavigationBar (9)

```
NavigationBar
NavigationBar.
NavigationBar.AnchorX
NavigationBar.AnchorY
NavigationBar.Debug
NavigationBar.DotsPerQuarter
NavigationBar.Enabled
NavigationBar.Opacity
NavigationBar.Scale
```

### LocationDiscovery (6)

```
LocationDiscovery.
LocationDiscovery.BannerScale
LocationDiscovery.Delay
LocationDiscovery.Enabled
LocationDiscovery.OffsetY
LocationDiscovery.Scale
```

### SoundEffects (2)

```
SoundEffects.MuteTalkingSpeaker
SoundEffects.PlayDuringConversations
```

### BootFirstSaveOutside (1)

```
BootFirstSaveOutside
```

### Credits (1)

```
Credits.MusicGainDb
```

### NavigationBarArtVersion (1)

```
NavigationBarArtVersion
```

### TitleLogo (1)

```
TitleLogo.Enabled
```
