"""A simulated console: fake PCSX2 + fake game, enough to run the mod end to end.

Two pieces are joined here:

* ``FakePCSX2`` -- a server that speaks PINE over a real socket, so the client's
  framing is exercised for real;
* ``R5900VM`` -- an interpreter, so the code the mod installs actually *runs*.

On top of them sits a stand-in for the game: a stage memory map with a per-frame
function, an actor table, a view-mode word, and -- importantly -- an engine that
behaves like Ape Escape 3 does: **it rewrites its own frame state every frame.**
That is the whole reason the mod exists, so the simulation reproduces it. A test
that only wrote the values once and read them back would pass while the real mod
did nothing at all.

What this cannot tell you: whether the addresses are right (they come from the
user's own disc) or whether the game's own two-player renderer behaves. Those
need the game. Everything about *our* machine code, protocol and policy is
testable here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from aecoops import cave, mips
from aecoops.r5900vm import R5900VM

from .fakepine import FakePCSX2


@dataclass
class Stage:
    """Where a fake Ape Escape 3 keeps the things the mod pokes."""

    hook_site: int = 0x00120000
    update_fn: int = 0x00150000
    cave: int = 0x001F0000
    frame_counter: int = 0x00110000
    view_mode: int = 0x00110010
    actor_count: int = 0x00110014
    partner_enabled: int = 0x00110018
    actor_base: int = 0x00180000
    actor_stride: int = 0x100
    # field offsets inside an actor
    off_active: int = 0x00
    off_char_id: int = 0x04
    off_pad_port: int = 0x08
    off_pos_x: int = 0x10
    off_pos_y: int = 0x14
    off_pos_z: int = 0x18
    off_yaw: int = 0x1C
    # the HUD: one instance in story mode, two in a two-player minigame
    hud_instances: int = 0x00110024
    hud_base: int = 0x00181000
    hud_stride: int = 0x40
    off_hud_visible: int = 0x00
    off_hud_x: int = 0x04
    off_hud_y: int = 0x08
    off_hud_owner: int = 0x0C
    # the two stage cameras
    camera_base: int = 0x00182000
    camera_stride: int = 0x20
    off_camera_target: int = 0x00
    # the viewport rectangles the renderer draws into
    viewport_count: int = 0x00110028
    viewport_base: int = 0x00183000
    viewport_stride: int = 0x20
    off_view_x: int = 0x00
    off_view_y: int = 0x04
    off_view_w: int = 0x08
    off_view_h: int = 0x0C
    off_view_target: int = 0x10

    # Values the engine itself believes, i.e. what it writes back every frame.
    engine_view_mode: int = 0  # single viewport, story mode
    engine_actor_count: int = 1
    engine_char_id: int = 0
    engine_partner: int = 0
    engine_hud_instances: int = 1
    # where the engine puts the story HUD, and where it puts a second one when it
    # believes there is one: bottom-right, which is why a split needs it moved.
    engine_hud: tuple = (560, 8)
    # One full-screen view, and the story renderer's own idea of where a second
    # view goes when it draws one (the MonkeyCam's corner, made up).
    engine_viewports: int = 1
    # Functions the fake console treats as "the game's own code": a call into one
    # returns immediately, like a real function would, and the console records it.
    external: tuple = ()
    engine_view: tuple = (0, 0, 640, 448)
    engine_view2: tuple = (448, 336, 192, 112)

    def field(self, slot: int, offset: int) -> int:
        return self.actor_base + slot * self.actor_stride + offset

    def hud(self, slot: int, offset: int) -> int:
        return self.hud_base + slot * self.hud_stride + offset

    def camera(self, slot: int, offset: int) -> int:
        return self.camera_base + slot * self.camera_stride + offset

    def viewport(self, slot: int, offset: int) -> int:
        return self.viewport_base + slot * self.viewport_stride + offset


class FakeAEC3:
    """One fake console: a game, an emulator and the mod talking over a socket."""

    def __init__(self, stage: Stage | None = None, kei: int = 0, yumi: int = 1) -> None:
        self.stage = stage or Stage()
        self.vm = R5900VM()
        self.pcsx2 = FakePCSX2(ram=self.vm.ram)
        self.frames = 0
        self.hook_calls = 0
        # (function, $a0) for every call the cave made into the game's own code.
        self.calls: list[tuple[int, int]] = []
        self.kei, self.yumi = kei, yumi
        self._plant()

    # ------------------------------------------------------------------ context

    @property
    def port(self) -> int:
        return self.pcsx2.port

    def close(self) -> None:
        self.pcsx2.close()

    def __enter__(self) -> "FakeAEC3":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------------- setup

    def _plant(self) -> None:
        s = self.stage
        # Some plausible game code, so disassembly and pattern scans have something
        # real to look at. The hook site is a `jal` into the per-frame update.
        self.vm.write_bytes(s.hook_site, mips.to_bytes([mips.jal(s.update_fn)]))
        self.vm.write_bytes(
            s.update_fn,
            mips.to_bytes(
                [
                    mips.addiu("sp", "sp", -0x20),
                    mips.sw("ra", 0x1C, "sp"),
                    mips.lw("t0", 0, "a0"),
                    mips.NOP,
                ]
            ),
        )
        self.vm.write32(s.frame_counter, 0)
        self.vm.write32(s.view_mode, s.engine_view_mode)
        self.vm.write32(s.actor_count, s.engine_actor_count)
        self.vm.write32(s.partner_enabled, s.engine_partner)
        for slot in range(2):
            self.vm.write32(s.field(slot, s.off_active), 1 if slot == 0 else 0)
            self.vm.write32(s.field(slot, s.off_char_id), s.engine_char_id)
            self.vm.write32(s.field(slot, s.off_pad_port), 0)
            self.vm.write32(s.field(slot, s.off_pos_x), 0x40000000 + slot)
            self.vm.write32(s.field(slot, s.off_pos_y), 0x41000000)
            self.vm.write32(s.field(slot, s.off_pos_z), 0x42000000)
            self.vm.write32(s.field(slot, s.off_yaw), 0)
        self.vm.write32(s.hud_instances, s.engine_hud_instances)
        for slot in range(2):
            self.vm.write32(s.hud(slot, s.off_hud_visible), 1 if slot == 0 else 0)
            self.vm.write32(s.hud(slot, s.off_hud_x), s.engine_hud[0])
            self.vm.write32(s.hud(slot, s.off_hud_y), s.engine_hud[1])
            self.vm.write32(s.hud(slot, s.off_hud_owner), 0)
        for slot in range(2):
            self.vm.write32(s.camera(slot, s.off_camera_target), 0)
        self.vm.write32(s.viewport_count, s.engine_viewports)
        for slot, rect in enumerate((s.engine_view, s.engine_view2)):
            for offset, value in zip((s.off_view_x, s.off_view_y, s.off_view_w, s.off_view_h), rect):
                self.vm.write32(s.viewport(slot, offset), value)
            self.vm.write32(s.viewport(slot, s.off_view_target), 0)

    # -------------------------------------------------------------------- frames

    def frame(self, count: int = 1) -> None:
        """Run the game forward: the engine resets its state, then calls the hook."""
        s = self.stage
        for _ in range(count):
            self.frames += 1
            value = self.vm.read32(s.frame_counter)
            self.vm.write32(s.frame_counter, value + 1)
            # The engine rewriting itself is the reason the mod needs a cave.
            self.vm.write32(s.view_mode, s.engine_view_mode)
            self.vm.write32(s.actor_count, s.engine_actor_count)
            self.vm.write32(s.partner_enabled, s.engine_partner)
            self.vm.write32(s.field(1, s.off_active), 0)
            # The HUD too: a story stage rebuilds one instance at the bottom-right
            # every frame, which is exactly why a per-view HUD cannot be a one-off
            # write any more than the split itself can.
            self.vm.write32(s.hud_instances, s.engine_hud_instances)
            for slot in range(2):
                self.vm.write32(s.hud(slot, s.off_hud_visible), 1 if slot == 0 else 0)
                self.vm.write32(s.hud(slot, s.off_hud_x), s.engine_hud[0])
                self.vm.write32(s.hud(slot, s.off_hud_y), s.engine_hud[1])
                self.vm.write32(s.hud(slot, s.off_hud_owner), 0)
                self.vm.write32(s.camera(slot, s.off_camera_target), 0)
            # The renderer rebuilds its rectangles from the view mode every frame,
            # which is the whole reason the rectangle route is also a per-frame wrestle.
            self.vm.write32(s.viewport_count, s.engine_viewports)
            for slot, rect in enumerate((s.engine_view, s.engine_view2)):
                for offset, value in zip((s.off_view_x, s.off_view_y, s.off_view_w, s.off_view_h), rect):
                    self.vm.write32(s.viewport(slot, offset), value)
                self.vm.write32(s.viewport(slot, s.off_view_target), 0)
            self._call_hook()

    def _call_hook(self):
        s = self.stage
        word = self.vm.read32(s.hook_site)
        if (word >> 26) != 0x03:
            return None
        target = cave.callee_of_jal(word, s.hook_site)
        if target != s.cave:
            # Not our cave: the game's own call happens and nothing else does.
            return None
        self.hook_calls += 1
        self.vm.set_reg("sp", 0x001FFF00)
        # The arguments the game passed to the function we displaced: if the cave
        # fails to preserve them, the real engine would see garbage here.
        self.vm.set_reg("a0", 0xAAAAAAAA)
        self.vm.set_reg("a1", 0xBBBBBBBB)
        self.vm.set_reg("a2", 0xCCCCCCCC)
        self.vm.set_reg("a3", 0xDDDDDDDD)
        result = self.vm.run(
            start=s.hook_site,
            stop_at=s.hook_site + 8,
            external_calls=(s.update_fn, *s.external),
            max_steps=10000,
        )
        self.calls.extend((call.callee, call.a0) for call in result.calls)
        return result

    # -------------------------------------------------------------------- reads

    def read(self, addr: int) -> int:
        return self.vm.read32(addr)

    def read_float(self, addr: int) -> float:
        return struct.unpack("<f", struct.pack("<I", self.read(addr)))[0]

    def write_float(self, addr: int, value: float) -> None:
        self.vm.write32(addr, struct.unpack("<I", struct.pack("<f", value))[0])

    def actor(self, slot: int) -> dict:
        s = self.stage
        return {
            "active": self.read(s.field(slot, s.off_active)),
            "char_id": self.read(s.field(slot, s.off_char_id)),
            "pad_port": self.read(s.field(slot, s.off_pad_port)),
            "pos_x": self.read(s.field(slot, s.off_pos_x)),
            "pos_y": self.read(s.field(slot, s.off_pos_y)),
            "pos_z": self.read(s.field(slot, s.off_pos_z)),
            "yaw": self.read(s.field(slot, s.off_yaw)),
        }

    def hud_instance(self, slot: int) -> dict:
        s = self.stage
        return {
            "visible": self.read(s.hud(slot, s.off_hud_visible)),
            "x": self.read(s.hud(slot, s.off_hud_x)),
            "y": self.read(s.hud(slot, s.off_hud_y)),
            "owner": self.read(s.hud(slot, s.off_hud_owner)),
        }

    def view_rect(self, slot: int) -> tuple:
        s = self.stage
        return tuple(
            self.read(s.viewport(slot, offset))
            for offset in (s.off_view_x, s.off_view_y, s.off_view_w, s.off_view_h)
        )

    def called(self, fn: int) -> list[int]:
        """Every argument the cave passed to ``fn``, in order."""
        return [a0 for callee, a0 in self.calls if callee == fn]

    def set_engine_char(self, char_id: int) -> None:
        """Pretend the save slot was loaded with a different hero."""
        self.stage.engine_char_id = char_id
        for slot in range(2):
            self.vm.write32(self.stage.field(slot, self.stage.off_char_id), char_id)

    def move_p1(self, word: int) -> None:
        """Put player one at ``x``, as a raw word.

        Raw, not a float, because most tests care that a value *moved between two
        actors* rather than what it means as a number -- and a raw word makes the
        expectation exact. Use ``move_p1_float`` where the arithmetic matters.
        """
        self.vm.write32(self.stage.field(0, self.stage.off_pos_x), word)

    def move_p1_float(self, x: float) -> None:
        self.write_float(self.stage.field(0, self.stage.off_pos_x), x)

    def move_p2_float(self, x: float) -> None:
        self.write_float(self.stage.field(1, self.stage.off_pos_x), x)

    @property
    def cave_installed(self) -> bool:
        word = self.vm.read32(self.stage.hook_site)
        return (word >> 26) == 0x03 and cave.callee_of_jal(word, self.stage.hook_site) == self.stage.cave

    def block(self) -> dict:
        return cave.read_block(_VMReader(self.vm), self.stage.cave)


class _VMReader:
    def __init__(self, vm: R5900VM) -> None:
        self.vm = vm

    def read_bytes(self, addr: int, length: int) -> bytes:
        return self.vm.read_bytes(addr, length)


@dataclass
class FakeRig:
    """A prepared fake console plus the address table that points at it.

    Builds a real ``AddressTable`` with the fake console's addresses and field
    offsets filled in, which is the same shape a user's own discovered table has.
    """

    sim: FakeAEC3
    table: object = field(default=None)  # AddressTable
    table_path: object = None

    def close(self) -> None:
        self.sim.close()


def make_table(
    stage: Stage,
    kei: int = 0,
    yumi: int = 1,
    tmp=None,
    with_hud: bool = False,
    with_cameras: bool = False,
    with_viewports: bool = False,
):
    """An AddressTable aimed at a simulated stage (used by the co-op tests).

    ``tmp`` is an optional pytest tmp_path: when given, the table is written to
    disk in the ordinary JSON shape so loading and saving are exercised too.
    """
    from aecoops.addresses import AddressTable, Field, Struct
    from aecoops.scan import Discovery

    def disc(addr: int) -> Discovery:
        return Discovery(name="", addr=addr, verified="simulated")

    actor = Struct(
        name="actor",
        base=disc(stage.actor_base),
        stride=stage.actor_stride,
        count=2,
        fields={
            "active": Field("active", stage.off_active, verified="simulated"),
            "char_id": Field("char_id", stage.off_char_id, verified="simulated"),
            "pad_port": Field("pad_port", stage.off_pad_port, verified="simulated"),
            "pos_x": Field("pos_x", stage.off_pos_x, verified="simulated"),
            "pos_y": Field("pos_y", stage.off_pos_y, verified="simulated"),
            "pos_z": Field("pos_z", stage.off_pos_z, verified="simulated"),
            "yaw": Field("yaw", stage.off_yaw, verified="simulated"),
        },
    )
    structs = {"actor": actor}
    globals_ = {
        "frame_hook": disc(stage.hook_site),
        "view_mode": disc(stage.view_mode),
        "actor_count": disc(stage.actor_count),
        "partner_enabled": disc(stage.partner_enabled),
    }
    if with_hud:
        # The HUD the minigames use: an instance table, each entry with a
        # visibility flag, an anchor and an owner.
        structs["hud"] = Struct(
            name="hud",
            base=disc(stage.hud_base),
            stride=stage.hud_stride,
            count=2,
            fields={
                "visible": Field("visible", stage.off_hud_visible, verified="simulated"),
                "x": Field("x", stage.off_hud_x, verified="simulated"),
                "y": Field("y", stage.off_hud_y, verified="simulated"),
                "owner": Field("owner", stage.off_hud_owner, verified="simulated"),
            },
        )
        globals_["hud_instances"] = disc(stage.hud_instances)
    if with_viewports:
        structs["viewport"] = Struct(
            name="viewport",
            base=disc(stage.viewport_base),
            stride=stage.viewport_stride,
            count=2,
            fields={
                "x": Field("x", stage.off_view_x, verified="simulated"),
                "y": Field("y", stage.off_view_y, verified="simulated"),
                "w": Field("w", stage.off_view_w, verified="simulated"),
                "h": Field("h", stage.off_view_h, verified="simulated"),
                "target_actor": Field("target_actor", stage.off_view_target, verified="simulated"),
            },
        )
        globals_["viewport_count"] = disc(stage.viewport_count)
    if with_cameras:
        structs["camera"] = Struct(
            name="camera",
            base=disc(stage.camera_base),
            stride=stage.camera_stride,
            count=2,
            fields={"target_actor": Field("target_actor", stage.off_camera_target, verified="simulated")},
        )
    table = AddressTable(
        characters={"kei": kei, "yumi": yumi},
        layouts={"single": 0, "vertical": 1, "horizontal": 2},
        globals=globals_,
        structs=structs,
    )
    if tmp is not None:
        path = tmp / "addresses.json"
        table.save(path)
        table.path = path
    return table


def read32(vm: R5900VM, addr: int) -> int:
    return struct.unpack("<I", vm.read_bytes(addr, 4))[0]
