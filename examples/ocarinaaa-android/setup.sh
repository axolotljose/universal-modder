#!/usr/bin/env bash
# Fetch the Shipwright-Android engine source (with submodules) next to this
# project and rebrand its JNI glue from com.dishii.soh to com.nullport.ocarinaaa.
#
# Run once before building:
#   ./setup.sh
#
# The clone lands in examples/ocarinaaa-android/Shipwright-Android/ (gitignored)
# and is referenced by android/app/build.gradle:
#   externalNativeBuild.cmake.path = '../../Shipwright-Android/CMakeLists.txt'
set -euo pipefail

cd "$(dirname "$0")"

BASE_DIR="Shipwright-Android"
ENGINE_REPO="https://github.com/axolotljose/Shipwright-Android"
EXPECTED_VERSION="9.0.2"

if [ ! -d "$BASE_DIR/.git" ]; then
    echo "[setup] Cloning $ENGINE_REPO (shallow)..."
    git clone --depth 1 "$ENGINE_REPO" "$BASE_DIR"
    echo "[setup] Fetching submodules (libultraship, OTRExporter, ZAPDTR)..."
    git -C "$BASE_DIR" submodule update --init --recursive --depth 1
else
    echo "[setup] Engine checkout already present at $BASE_DIR/, skipping clone."
fi

# --- sanity: engine version must pair with the bundled soh.otr (9.0.2) -------
if ! grep -q "project(Ship VERSION ${EXPECTED_VERSION}" "$BASE_DIR/CMakeLists.txt"; then
    echo "[setup] ERROR: unexpected engine version in $BASE_DIR/CMakeLists.txt." >&2
    echo "        Expected project(Ship VERSION ${EXPECTED_VERSION} ...)." >&2
    echo "        The bundled soh.otr is built for ${EXPECTED_VERSION}; a mismatch" >&2
    echo "        makes the game exit at startup (CheckSoHOTRVersion)." >&2
    exit 1
fi

# --- rebrand JNI: Java package com.nullport.ocarinaaa ------------------------
# Four native call sites reference the old com.dishii.soh package:
#   soh/src/code/main.c                FindClass("com/dishii/soh/MainActivity")
#   soh/soh/Extractor/Extract.cpp      Java_..._nativeHandleSelectedFile
#   soh/src/code/z_camera.c            Java_..._setCameraState
#   libultraship/src/port/mobile/MobileImpl.cpp
#                                      Java_..._attachController/setButton/setAxis/detachController
# (MobileImpl.cpp must match the pinned submodule commit a850449, which
#  ships com_dishii_soh symbols; the branch head ships com_dishii_mm.)
PATCH_TARGETS=(
    "$BASE_DIR/soh/src/code/main.c"
    "$BASE_DIR/soh/soh/Extractor/Extract.cpp"
    "$BASE_DIR/soh/src/code/z_camera.c"
    "$BASE_DIR/libultraship/src/port/mobile/MobileImpl.cpp"
)

for f in "${PATCH_TARGETS[@]}"; do
    if [ ! -f "$f" ]; then
        echo "[setup] ERROR: expected file missing: $f" >&2
        echo "        Did submodule update succeed? Check: git -C $BASE_DIR submodule status" >&2
        exit 1
    fi
    if grep -q "dishii" "$f"; then
        sed -e 's|com/dishii/soh/MainActivity|com/nullport/ocarinaaa/MainActivity|g' \
            -e 's|Java_com_dishii_soh_MainActivity|Java_com_nullport_ocarinaaa_MainActivity|g' \
            "$f" > "$f.tmp" && mv "$f.tmp" "$f"
        echo "[setup] Rebranded JNI package in $f"
    else
        echo "[setup] Already rebranded (or upstream changed): $f"
    fi
done

# --- verify nothing old is left ---------------------------------------------
LEFTOVER=$(grep -ra "dishii" \
    "$BASE_DIR/soh/src/code/main.c" \
    "$BASE_DIR/soh/soh/Extractor/Extract.cpp" \
    "$BASE_DIR/soh/src/code/z_camera.c" \
    "$BASE_DIR/libultraship/src/port/mobile/MobileImpl.cpp" || true)
if [ -n "$LEFTOVER" ]; then
    echo "[setup] ERROR: stale com.dishii references remain:" >&2
    echo "$LEFTOVER" >&2
    exit 1
fi

echo "[setup] Engine ready: $BASE_DIR (Ship ${EXPECTED_VERSION}, JNI rebranded)."
echo "[setup] Next: cd android && ./gradlew assembleRelease"
