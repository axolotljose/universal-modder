"""aecoops -- split-screen co-op for Ape Escape 3, as either Kei or Yumi.

The mod has two halves:

* **In-game**: a ~40 instruction interpreter installed into unused EE RAM, reached
  by redirecting one ``jal`` in the game's per-frame path (``cave.py``). It
  re-applies the state the engine keeps resetting: two viewports, two heroes, one
  character each.
* **Host**: this package. It finds addresses on the user's own disc
  (``discover.py``), proves the hook really runs per frame, and refills the
  interpreter's parameter block when a player swaps characters (``coop.py``).

It also exports the same cave as a standalone PCSX2 patch (``pnach.py``), so a
working configuration can be frozen into the format PS2 mods usually ship in.
"""

from .addresses import AddressTable
from .coop import CoopConfig, CoopMod, Split
from .patcher import Patch, PatchSet
from .pine import EmuInfo, Pine, PineError, PineNotRunning, PineUnusable

__all__ = [
    "AddressTable",
    "CoopConfig",
    "CoopMod",
    "EmuInfo",
    "Patch",
    "PatchSet",
    "Pine",
    "PineError",
    "PineNotRunning",
    "PineUnusable",
    "Split",
]

__version__ = "0.1.0"
