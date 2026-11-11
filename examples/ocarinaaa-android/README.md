# OcarinAAA of Time — Android port

Android port of [OcarinAAA-of-Time](https://github.com/axolotljose/OcarinAAA-of-Time)
(modernized, voice-acted *Ocarina of Time* built on Ship of Harkinian) using
[Shipwright-Ports](https://github.com/axolotljose/Shipwright-Ports) as the
base engine (SoH **9.2.3**, matching `OcarinAAA.o2r` `portVersion`).

The APK ships the Ship of Harkinian engine (9.2.3, Android glue from
Shipwright-Ports), the `OcarinAAA.o2r` mod archive, and the OcarinAAA data
files (subtitle voice lines + credits sequence). On first launch the app
installs everything into the `SOH/` folder (folder picker on Android 11+),
extracts your own legally dumped OoT ROM, and loads the mod as a patch
archive on top of the base game.

## Layout

```
examples/ocarinaaa-android/
├── README.md              this file
├── setup.sh               fetches the engine source + rebrands its JNI glue
├── .gitignore             ignores the engine checkout and build outputs
├── icon-source.png        launcher icon artwork (512×512 master)
└── android/               Gradle project — drop-in app module for the engine
    ├── build.gradle       app id com.nullport.ocarinaaa, v1.0.0
    ├── settings.gradle
    ├── gradlew            Gradle 8.11.1 wrapper (AGP 8.10.1)
    └── app/src/main/
        ├── AndroidManifest.xml
        ├── java/com/nullport/ocarinaaa/
        │   ├── MainActivity.java        setup flow, touch overlay, ROM picker
        │   ├── AssetCopyUtil.java       recursive APK-assets installer
        │   └── ControllerButtons.java   virtual gamepad constants
        ├── assets/
        │   ├── soh.otr                  engine resource archive (pairs with 9.0.2)
        │   ├── gamecontrollerdb.txt     SDL controller database
        │   ├── assets/extractor/        ROM-extraction configs (first launch)
        │   ├── mods/OcarinAAA.o2r       ← the OcarinAAA mod (1034 entries)
        │   ├── aaa-subtitles/audio/     ← 39 voice/subtitle WAVs
        │   └── aaa-credits/sequence.json ← credits sequence data
        └── res/                         OcarinAAA launcher icons + label
```

## Requirements

- Linux/macOS/Windows with `git`
- **JDK 17** (`java -version`) — required by Android Gradle Plugin 8.10.1
- Android SDK with:
  - `platforms;android-31`
  - `ndk;26.0.10792818`
    (`sdkmanager "ndk;26.0.10792818"` — AGP can also auto-install it if
    licenses are accepted via `sdkmanager --licenses`)
  - SDK location known to Gradle: Android Studio does this automatically,
    otherwise export `ANDROID_HOME` or create `android/local.properties`
    containing `sdk.dir=/path/to/SDK`
- **CMake 3.31.5** (pinned by `app/build.gradle`). Either:
  - the SDK package, if your repository has it: `sdkmanager --list | grep cmake`
    then `sdkmanager "cmake;3.31.5"`, **or**
  - Kitware's CMake 3.31.5 on your `PATH` — this is what upstream's own
    Docker builder uses (`.sh`/`.dmg`/`.msi` from
    `github.com/Kitware/CMake/releases/tag/v3.31.5`)
- Gradle itself is **not** installed manually — `./gradlew` downloads the
  pinned wrapper (8.11.1) on first run
- ~10 GB free disk for the engine clone + native build
- A legally dumped *Ocarina of Time* ROM (`.z64`/`.n64`/`.v64`, N64 or GameCube)
  — required at first launch, never redistributed

## Build

```bash
cd examples/ocarinaaa-android
./setup.sh                 # clone Shipwright-Ports + submodules, rebrand JNI, bundle AAA payload
# generate soh.o2r (host):
cmake -S Shipwright-Ports -B Shipwright-Ports/build-linux -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build Shipwright-Ports/build-linux --target GenerateSohOtr --parallel
cp Shipwright-Ports/soh/soh.o2r Shipwright-Ports/Android/app/src/main/assets/soh.o2r
cd Shipwright-Ports/Android
./gradlew assembleRelease
```

The APK lands at `Shipwright-Ports/Android/app/build/outputs/apk/release/ocarinaaa_release_v1.0.0-ports.9.2.3.apk`.

The first `assembleRelease` compiles the entire Ship of Harkinian engine for
`arm64-v8a` — expect 10–30+ minutes depending on the machine. Subsequent
incremental builds are much faster.

`setup.sh` is idempotent and must run at least once: it places the engine at
`../Shipwright-Android/` (where `app/build.gradle` points CMake), verifies the
engine is 9.0.2 (must match the bundled `soh.otr` or the game exits at
startup), and rewrites the four native JNI call sites from
`com.dishii.soh` → `com.nullport.ocarinaaa` (engine `main.c`, `Extract.cpp`,
`z_camera.c`, and `libultraship` `MobileImpl.cpp`).

### Troubleshooting

| Symptom | Fix |
|---|---|
| `CMake 3.31.5 not found` | Install CMake 3.31.5 (Kitware release on `PATH`, or `sdkmanager "cmake;3.31.5"` if listed). Other CMake versions do not satisfy the pinned `version "3.31.5"`. |
| `SDK location not found` | Export `ANDROID_HOME=/path/to/SDK`, or add `android/local.properties` with `sdk.dir=/path/to/SDK`. |
| `NDK 26.0.10792818 not found` | `sdkmanager "ndk;26.0.10792818"` (or accept all licenses first: `sdkmanager --licenses`). |
| `Unable to load CMake... ../../Shipwright-Android/CMakeLists.txt` | `setup.sh` has not been run — run it from `examples/ocarinaaa-android/` first. |
| `Unsupported class file major version` / AGP/Gradle mismatch errors | Use the wrapper (`./gradlew`), not a system `gradle`; JDK must be 17. |
| `Permission denied` on `gradlew`/`setup.sh` | `chmod +x gradlew setup.sh`. |
| Native build killed / out of memory | Close other apps; on ≤8 GB machines retry with fewer parallel jobs, e.g. `./gradlew assembleRelease --no-daemon`. |
| `lintVitalRelease` fails on release build | Lint is advisory here (`abortOnError false` is set); if it still trips, build `assembleDebug` or read the lint report under `app/build/reports/`. |

## Install & first run

1. `adb install ocarinaaa_release_v1.0.0.apk` (or sideload the APK).
2. Grant storage access when prompted
   (All-files access on Android 11+, read/write below that).
3. The app creates `SDCARD/SOH/` and installs from APK assets:
   `soh.otr`, `assets/extractor/`, `mods/OcarinAAA.o2r`,
   `aaa-subtitles/`, `aaa-credits/`, `gamecontrollerdb.txt`.
4. On first launch with no game archive present, the engine asks to extract
   your ROM → pick your `.z64` via the file picker → `oot.otr`/`oot-mq.otr`
   are generated into `SOH/`.
5. The OcarinAAA mod loads automatically from `SOH/mods/` (every `.o2r`/
   `.otr`/`.mpq`/`.zip` in `mods/` is applied as a patch archive after the
   base game).

Data layout after setup:

```
SDCARD/SOH/
├── soh.otr  oot.otr (or oot-mq.otr)   base game
├── shipofharkinian.json               settings / save config
├── mods/OcarinAAA.o2r                 the mod
├── aaa-subtitles/audio/*.wav          voice lines (looked up by the mod)
├── aaa-credits/sequence.json          credits data
└── assets/extractor/                  ROM extraction tool data
```

## How the port works

- **Base**: Shipwright-Android (Ship of Harkinian 9.0.2 + Android glue).
  Engine version, `soh.otr` `portVersion`, and Android assets are a matched
  set — `CheckSoHOTRVersion()` hard-exits on mismatch.
- **Mod loading**: `OTRGlobals` scans `SOH/mods/` and loads
  `OcarinAAA.o2r` as a patch archive over the base game. Mod archives are
  *not* version-checked, so the 9.2.3-authored mod loads on the 9.0.2 engine.
- **First-run install**: `MainActivity.setupFilesInBackground()` copies the
  payload out of APK assets (APK assets are read-only; the engine needs real
  files on disk). App updates re-run this via `deleteOutdatedAssets()` +
  `versionCode` bump so the payload always matches the APK.
- **Rebrand**: `applicationId`/namespace `com.nullport.ocarinaaa`, label
  "OcarinAAA of Time", custom launcher icons, `ocarinaaa_*.apk` output name.
  The internal Java package matches, and `setup.sh` patches the engine's four
  JNI symbols to match (`FindClass`, `nativeHandleSelectedFile`,
  `setCameraState`, and the virtual-gamepad bridge in `libultraship`).

## Status / known gaps

Working:

- Buildable Android project (Gradle + NDK/CMake wiring from Shipwright-Android).
- Full first-run flow: permissions → asset install → ROM extraction → game.
- OcarinAAA assets bundled and installed: mod archive, 39 subtitle voice
  lines, credits sequence.
- Touch overlay, controller support, landscape, keep-screen-on (inherited).

Pending (the OcarinAAA gameplay code is closed-source C++ in the Windows
`OcarinAAA.exe`; it has to be re-implemented against this engine):

- AAA feature modules from the desktop build (`AAAOcarinaOfTime` namespace:
  dialogue subtitles, reactions, HUD/quest panel, navigation bar, opening
  dialogue, location discovery, title logo, post-processing, sound effects,
  credits sequence player) are **not yet ported**. Until they are, the APK
  runs a standard SoH experience with the OcarinAAA resource patch applied —
  custom textures/UI from `OcarinAAA.o2r` show, but voice playback, subtitle
  display and the scripted credits do not trigger.
- `aaa-dialogue.pack` (173 MB, Git LFS in the OcarinAAA repo) is not bundled;
  it is needed by the dialogue/subtitle feature when that code is ported.
- Engine is 9.0.2 (the newest pairing that ships a matching `soh.otr`);
  OcarinAAA's Windows build targets SoH 9.2.3. Resource names are stable
  across these versions, but a few mod entries may not resolve on 9.0.2.

## Provenance

| Component | Source |
|---|---|
| Engine + Android glue | `axolotljose/Shipwright-Android` @ `c9d8f4a` (fork of Waterdish/Shipwright-Android) |
| `libultraship` / `OTRExporter` / `ZAPDTR` | Waterdish forks, pinned submodule commits |
| `OcarinAAA.o2r` | `axolotljose/OcarinAAA-of-Time` release v1.0 |
| `aaa-subtitles/audio/*.wav`, `aaa-credits/sequence.json` | `axolotljose/OcarinAAA-of-Time` repo |
| `soh.otr`, `gamecontrollerdb.txt`, `assets/extractor/` | Shipwright-Android `Android/app/src/main/assets/` |

Ocarina of Time is © Nintendo. Ship of Harkinian requires a ROM you legally
own; this port does not distribute any Nintendo assets.
