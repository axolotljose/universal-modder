#!/usr/bin/env bash
# Fetch Shipwright-Ports (SoH 9.2.3 Android/iOS) and rebrand it as OcarinAAA.
#
# Run once before building:
#   ./setup.sh
#
# The clone lands in examples/ocarinaaa-android/Shipwright-Ports/ (gitignored).
# CI then generates soh.o2r and runs Gradle from Shipwright-Ports/Android/.
set -euo pipefail

cd "$(dirname "$0")"

BASE_DIR="Shipwright-Ports"
ENGINE_REPO="https://github.com/axolotljose/Shipwright-Ports"
EXPECTED_VERSION="9.2.3"
OLD_PKG="com.dishii.soh"
NEW_PKG="com.nullport.ocarinaaa"
OLD_JNI="com/dishii/soh"
NEW_JNI="com/nullport/ocarinaaa"
OLD_JAVA="Java_com_dishii_soh"
NEW_JAVA="Java_com_nullport_ocarinaaa"

if [ ! -d "$BASE_DIR/.git" ]; then
    echo "[setup] Cloning $ENGINE_REPO (shallow)..."
    git clone --depth 1 "$ENGINE_REPO" "$BASE_DIR"
    echo "[setup] Fetching submodules (libultraship, OTRExporter, ZAPDTR)..."
    git -C "$BASE_DIR" submodule update --init --recursive --depth 1
else
    echo "[setup] Engine checkout already present at $BASE_DIR/, skipping clone."
fi

if ! grep -q "project(Ship VERSION ${EXPECTED_VERSION}" "$BASE_DIR/CMakeLists.txt"; then
    echo "[setup] ERROR: unexpected engine version in $BASE_DIR/CMakeLists.txt." >&2
    echo "        Expected project(Ship VERSION ${EXPECTED_VERSION} ...)." >&2
    echo "        OcarinAAA.o2r ships portVersion ${EXPECTED_VERSION}." >&2
    exit 1
fi

# --- rebrand JNI + Java package strings ------------------------------------
echo "[setup] Rebranding ${OLD_PKG} -> ${NEW_PKG} ..."
while IFS= read -r -d '' f; do
    if grep -q "dishii" "$f" 2>/dev/null; then
        sed -i \
            -e "s|${OLD_JNI}/MainActivity|${NEW_JNI}/MainActivity|g" \
            -e "s|${OLD_JAVA}|${NEW_JAVA}|g" \
            -e "s|${OLD_PKG}|${NEW_PKG}|g" \
            -e "s|${OLD_JNI}|${NEW_JNI}|g" \
            "$f"
        echo "[setup]   patched $f"
    fi
done < <(find "$BASE_DIR" \( -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.hpp' -o -name '*.java' -o -name '*.xml' -o -name '*.gradle' \) -print0)

# Move Java sources into the new package directory
JAVA_ROOT="$BASE_DIR/Android/app/src/main/java"
OLD_DIR="$JAVA_ROOT/com/dishii/soh"
NEW_DIR="$JAVA_ROOT/com/nullport/ocarinaaa"
if [ -d "$OLD_DIR" ]; then
    mkdir -p "$NEW_DIR"
    mv "$OLD_DIR"/* "$NEW_DIR/"
    rmdir -p "$OLD_DIR" 2>/dev/null || true
    echo "[setup] Moved Java sources to $NEW_DIR"
fi

# Namespace / applicationId / signing / APK name
APP_GRADLE="$BASE_DIR/Android/app/build.gradle"
python3 - <<'PY'
from pathlib import Path
p = Path("Shipwright-Ports/Android/app/build.gradle")
t = p.read_text()
t = t.replace("namespace 'com.dishii.soh'", "namespace 'com.nullport.ocarinaaa'")
t = t.replace("namespace \"com.dishii.soh\"", "namespace 'com.nullport.ocarinaaa'")
t = t.replace(
    'applicationId project.findProperty(\'sohApplicationId\') ?: "com.linkzenic.soh"',
    'applicationId "com.nullport.ocarinaaa"',
)
t = t.replace('def androidAppVersionName = "9.2.3-android.14"',
              'def androidAppVersionName = "1.0.0-ports.9.2.3"')
t = t.replace("signingConfig signingConfigs.release",
              "signingConfig signingConfigs.debug")
t = t.replace('def newApkName = "soh_${buildType}_${gitTag}.apk"',
              'def newApkName = "ocarinaaa_${buildType}_v${versionName}.apk"')
p.write_text(t)
print("[setup] Patched Android/app/build.gradle")
PY

# App name
STRINGS="$BASE_DIR/Android/app/src/main/res/values/strings.xml"
if [ -f "$STRINGS" ]; then
    sed -i 's|>Ship of Harkinian<|>OcarinAAA of Time<|' "$STRINGS"
fi

# Launcher icons: Ports already ships adaptive webp mipmaps. Do not drop
# extra PNG ic_launcher files next to them — mergeReleaseResources fails.

# AAA payload into APK assets
ASSETS="$BASE_DIR/Android/app/src/main/assets"
mkdir -p "$ASSETS/mods" "$ASSETS/aaa-subtitles" "$ASSETS/aaa-credits"
if [ -f android/app/src/main/assets/mods/OcarinAAA.o2r ]; then
    cp -f android/app/src/main/assets/mods/OcarinAAA.o2r "$ASSETS/mods/"
    echo "[setup] Bundled mods/OcarinAAA.o2r"
else
    echo "[setup] ERROR: missing android/app/src/main/assets/mods/OcarinAAA.o2r" >&2
    exit 1
fi
if [ -d android/app/src/main/assets/aaa-subtitles ]; then
    cp -a android/app/src/main/assets/aaa-subtitles/. "$ASSETS/aaa-subtitles/"
    echo "[setup] Bundled aaa-subtitles"
fi
if [ -d android/app/src/main/assets/aaa-credits ]; then
    cp -a android/app/src/main/assets/aaa-credits/. "$ASSETS/aaa-credits/"
    echo "[setup] Bundled aaa-credits"
fi

# Inject payload installer into MainActivity (always copy, even if SOH/ already exists)
MAIN="$NEW_DIR/MainActivity.java"
if [ -f "$MAIN" ] && ! grep -q "ensureAaaPayload" "$MAIN"; then
    python3 - <<'PY'
from pathlib import Path
p = Path("Shipwright-Ports/Android/app/src/main/java/com/nullport/ocarinaaa/MainActivity.java")
t = p.read_text()
t = t.replace('preferences = getSharedPreferences("com.dishii.soh.prefs"',
              'preferences = getSharedPreferences("com.nullport.ocarinaaa.prefs"')
t = t.replace(
    'private static final String SUPPORT_FILES_VERSION = "soh-android-support-1";',
    'private static final String SUPPORT_FILES_VERSION = "ocarinaaa-android-support-1";',
)
needle = "        // Always ensure mods folder exists"
inject = '''        ensureAaaPayload(targetRootFolder);

        // Always ensure mods folder exists'''
if needle not in t:
    raise SystemExit("setup: could not find mods-folder marker in MainActivity")
t = t.replace(needle, inject, 1)

# Also run on the "already set up" path, after the soh.o2r ensure block's countdown
# Insert a call at the start of checkAndSetupFiles
marker = "        File targetRootFolder = getTargetRootFolder();"
if marker not in t:
    raise SystemExit("setup: could not find checkAndSetupFiles start")
# Always install AAA payload, even when the SOH folder already exists from vanilla SoH.
t = t.replace(
    marker,
    marker + "\n        ensureAaaPayload(targetRootFolder);",
    1,
)
# add method before last closing brace of class
method = r'''
    /** Copy OcarinAAA.o2r + voice/credits data into the active SOH folder. Always overwrites the mod archive. */
    private void ensureAaaPayload(File targetRootFolder) {
        File modsDir = new File(targetRootFolder, "mods");
        if (!modsDir.exists() && !modsDir.mkdirs()) {
            Log.e("setupFiles", "Failed to create mods folder");
            return;
        }
        copyAssetFile("mods/OcarinAAA.o2r", new File(modsDir, "OcarinAAA.o2r"), true);
        try {
            AssetCopyUtil.copyAssetsToExternal(this, "aaa-subtitles",
                    new File(targetRootFolder, "aaa-subtitles").getAbsolutePath());
            AssetCopyUtil.copyAssetsToExternal(this, "aaa-credits",
                    new File(targetRootFolder, "aaa-credits").getAbsolutePath());
        } catch (IOException e) {
            Log.e("setupFiles", "Failed to copy AAA data folders", e);
        }
    }

    private void copyAssetFile(String assetPath, File dest, boolean overwrite) {
        if (dest.exists() && !overwrite) {
            return;
        }
        try (InputStream in = getAssets().open(assetPath);
             OutputStream out = new FileOutputStream(dest)) {
            byte[] buffer = new byte[COPY_BUFFER_SIZE];
            int read;
            while ((read = in.read(buffer)) != -1) {
                out.write(buffer, 0, read);
            }
            Log.i("setupFiles", "Copied " + assetPath + " -> " + dest.getAbsolutePath());
        } catch (IOException e) {
            Log.e("setupFiles", "Failed to copy " + assetPath, e);
        }
    }

'''
# insert method before the final "}\n" of the file
idx = t.rfind("}")
t = t[:idx] + method + t[idx:]
p.write_text(t)
print("[setup] Injected ensureAaaPayload into MainActivity")
PY
else
    echo "[setup] MainActivity already has ensureAaaPayload (or missing)"
fi

# Verify no leftover dishii JNI in the four known sites + java package dir
if grep -ra "com.dishii\|com/dishii\|Java_com_dishii" \
        "$BASE_DIR/soh/src/code" \
        "$BASE_DIR/soh/soh/Extractor" \
        "$NEW_DIR" \
        "$BASE_DIR/Android/app/build.gradle" 2>/dev/null | grep -v "Binary" | head; then
    echo "[setup] WARNING: leftover dishii references printed above" >&2
fi

echo "[setup] Engine ready: $BASE_DIR (Ship ${EXPECTED_VERSION}, JNI rebranded, AAA payload bundled)."
echo "[setup] Next (CI): generate soh.o2r, then cd $BASE_DIR/Android && ./gradlew assembleRelease"
