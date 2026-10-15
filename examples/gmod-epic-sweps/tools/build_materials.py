#!/usr/bin/env python3
"""Build the Epic SWEPs textures, and package the two weapons as their own addons.

	make_materials.py            regenerate the two procedural sprites (no AI needed)
	make_materials.py --icons    also rebuild the 128x128 icons from assets/src/*.png
	package_addons.py            write dist/epic_zombie_spawner.zip + dist/epic_7000_missiles.zip

Everything is plain Python + Pillow (Pillow only for --icons).
"""

import argparse
import math
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.dirname(HERE)
ADDON = os.path.join(EXAMPLE, "epic_sweps")
MAT = os.path.join(ADDON, "materials")
SRC = os.path.join(EXAMPLE, "assets", "src")


# --------------------------------------------------------------- sprites ----
def make_flash(path, size=256):
    """Hot white core -> orange falloff, alpha = luminance. Additive-friendly."""
    px = bytearray(size * size * 4)
    c = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            d = min(1.0, math.sqrt(dx * dx + dy * dy))
            glow = (1.0 - d) ** 2.6
            # a few light rays so it is not a boring circle
            ang = math.atan2(dy, dx)
            rays = 0.75 + 0.25 * abs(math.sin(ang * 4.0)) ** 1.5
            a = int(255 * min(1.0, glow * rays))
            r = 255
            g = int(180 + 75 * glow)
            b = int(90 + 165 * glow ** 2)
            i = (y * size + x) * 4
            px[i:i + 4] = bytes((r, g, b, a))
    _write_rgba(path, size, size, bytes(px))


def make_trail(path, w=128, h=256):
    """A vertical fire streak: bright narrow head, wide smoke tail."""
    px = bytearray(w * h * 4)
    for y in range(h):
        t = y / (h - 1)             # 0 = head (bottom of the missile), 1 = tail
        core = (1.0 - t) ** 1.4     # the hot tip is at t=0
        width = 0.06 + 0.30 * t    # it fans out into smoke
        for x in range(w):
            u = abs((x / (w - 1)) - 0.5) / 0.5
            inside = max(0.0, 1.0 - (u / width) ** 1.6)
            a = int(255 * min(1.0, inside * (0.35 + 0.65 * core)))
            heat = max(0.0, core - u * 0.5)
            r = 255
            g = int(120 + 135 * heat)
            b = int(60 + 195 * heat ** 3)
            i = (y * w + x) * 4
            px[i:i + 4] = bytes((r, g, b, a))
    _write_rgba(path, w, h, bytes(px))


def _write_rgba(path, w, h, raw):
    """PNG writer, stdlib only, so the fallback needs no Pillow at all."""
    import struct
    import zlib

    os.makedirs(os.path.dirname(path), exist_ok=True)
    stride = w * 4
    rows = b"".join(b"\x00" + raw[i * stride:(i + 1) * stride] for i in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(rows, 9)))
        f.write(chunk(b"IEND", b""))


def icons():
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed for --icons (pip install pillow). Skipping.")
        return

    for src, name in [("zombie_icon.png", "epic_zombiespawner"), ("missile_icon.png", "epic_missilebarrage")]:
        path = os.path.join(SRC, src)
        if not os.path.exists(path):
            print("no source icon at", path, "- the shipped one stays as it is")
            continue
        im = Image.open(path).convert("RGB").resize((128, 128), Image.LANCZOS)
        # materials/vgui/entities/<class>.png is the whole contract: GMod reads it for the spawnmenu
        # tile and the weapon-select HUD with no Lua, no Material() call, no icon table to maintain.
        d = os.path.join(MAT, "vgui/entities")
        os.makedirs(d, exist_ok=True)
        im.save(os.path.join(d, name + ".png"), optimize=True)
        print("icon  ", os.path.join(d, name + ".png"))


def materials(force=False):
    """Regenerate the procedural sprites. Existing files are kept unless --force,
    so the nicer AI-made ones shipped in the repo survive a casual run."""
    for out, fn in [("epic_sweps/flash.png", make_flash), ("epic_sweps/missile_trail.png", make_trail)]:
        path = os.path.join(MAT, out)
        if os.path.exists(path) and not force:
            print("keep  ", path, "(--force to regenerate procedurally)")
            continue
        fn(path)
        print("sprite", path, os.path.getsize(path), "bytes")

    vmt = os.path.join(MAT, "epic_sweps")
    os.makedirs(vmt, exist_ok=True)
    for name, shader, extra in [
        ("flash", "LightmappedGeneric", '\t"$additive"\t\t"1"\n\t"$unlitmaterial"\t"1"\n'),
        ("missile_trail", "UnlitGeneric", '\t"$additive"\t\t"1"\n\t"$unlitmaterial"\t"1"\n\t"$vertexcolor"\t\t"1"\n'),
    ]:
        with open(os.path.join(vmt, name + ".vmt"), "w") as f:
            f.write('"%s"\n{\n\t"$basetexture"\t\t"epic_sweps/%s"\n%s\t"$nocull"\t\t"1"\n}\n' % (shader, name, extra))
        print("vmt   ", name + ".vmt")


# -------------------------------------------------------------- packages ---
# The two weapons in separate addons, for people who only want one of them.
SPLIT = {
    "epic_zombie_spawner": {
        "lua": [
            "lua/weapons/epic_zombiespawner/",
            "lua/entities/epic_zombie_breach.lua",
        ],
        "keep_render": "zombie",
        "name": "100 Zombie Spawner",
    },
    "epic_7000_missiles": {
        "lua": [
            "lua/weapons/epic_missilebarrage/",
            "lua/entities/epic_missile_barrage.lua",
        ],
        "keep_render": "missile",
        "name": "7000 F*cking Missiles",
    },
}
# files both halves need
COMMON = [
    "lua/autorun/epic_sweps_init.lua",
    "lua/epic_sweps/shared/epic_sweps_zombies.lua",
    "lua/epic_sweps/shared/epic_sweps_config.lua",
    "lua/epic_sweps/shared/epic_sweps_util.lua",
    "lua/epic_sweps/shared/epic_sweps_particles.lua",
    "lua/epic_sweps/shared/epic_sweps_net.lua",
    "lua/epic_sweps/shared/epic_sweps_timeline.lua",
    "lua/epic_sweps/shared/epic_sweps_cvars.lua",
    "lua/epic_sweps/client/epic_sweps_hud.lua",
    "lua/epic_sweps/client/epic_sweps_render.lua",
    "lua/effects/epic_sweps_fx.lua",
    "addon.txt",
]


def package(out_dir):
    dist = os.path.join(out_dir, "dist")
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    os.makedirs(dist, exist_ok=True)

    for slug, spec in SPLIT.items():
        stage = os.path.join(dist, slug)
        for rel in COMMON + spec["lua"]:
            rel = rel.strip("/")
            src = os.path.join(ADDON, rel)
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif os.path.exists(src):
                shutil.copy2(src, dst)

        # shared assets both halves may reference
        for folder in ("materials/epic_sweps", "materials/vgui", "sound/epic_sweps", "scripts"):
            src = os.path.join(ADDON, folder)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(stage, folder), dirs_exist_ok=True)

        with open(os.path.join(stage, "addon.txt"), "w") as f:
            # this is the schema GMod's addon manager reads; "title"/"type" are gma's, not this file's
            f.write('"AddonInfo"\n{\n\t"name"\t\t"%s"\n\t"author"\t"universal-modder"\n'
                    '\t"contact"\t""\n\t"Version"\t"1.0"\n\t"UpstreamPage" ""\n'
                    '\t"tags"\t\t"fun+other"\n}\n' % spec["name"])

        zpath = os.path.join(dist, slug + ".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(stage):
                for fn in files:
                    full = os.path.join(root, fn)
                    z.write(full, os.path.join(slug, os.path.relpath(full, stage)))
        print("packed", zpath, "%.0f KB" % (os.path.getsize(zpath) / 1024.0))

    # the combined one, as it is developed
    zpath = os.path.join(dist, "epic_sweps.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(ADDON):
            if "assets" in root:
                continue
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, os.path.dirname(ADDON)))
    print("packed", zpath, "%.0f KB" % (os.path.getsize(zpath) / 1024.0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--icons", action="store_true", help="rebuild the 128x128 icons from assets/src")
    ap.add_argument("--package", action="store_true", help="write the two standalone addons into dist/")
    ap.add_argument("--force", action="store_true", help="overwrite the shipped sprites with procedural ones")
    a = ap.parse_args()

    materials(a.force)
    if a.icons:
        icons()
    if a.package:
        package(EXAMPLE)
