"""The address table: what the mod needs to know about Ape Escape 3's memory.

The table is a *worksheet*, not a finished map. Every field records where it came
from, how it can be re-derived on a disc we have never seen, and whether anybody
has actually confirmed it on hardware. An entry without a ``signature`` or an
``addr`` is a field the mod needs but nobody has located yet -- ``aecoops
doctor`` lists them, and ``docs/DISCOVERY.md`` gives a recipe for each one.

Keeping the unverified things visible is the point. A table that quietly guessed
would install a cave into the middle of the game's heap and crash on the title
screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .pine import Pine
from .scan import Discovery

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_TABLE = DATA_DIR / "addresses.json"


class TableError(RuntimeError):
    pass


@dataclass
class Field:
    """One offset inside a structure, resolved relative to the structure base."""

    name: str
    offset: int | None = None
    width: int = 4
    note: str = ""
    recipe: str = ""
    verified: str | None = None

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "Field":
        return cls(
            name=name,
            offset=raw.get("offset"),
            width=int(raw.get("width") or 4),
            note=raw.get("note", ""),
            recipe=raw.get("recipe", ""),
            verified=raw.get("verified"),
        )


@dataclass
class Struct:
    """A structure the mod pokes: a base address plus named field offsets."""

    name: str
    base: Discovery
    stride: int = 0
    count: int = 1
    fields: dict[str, Field] = field(default_factory=dict)

    def field_offset(self, name: str) -> int:
        fld = self.fields.get(name)
        if fld is None or fld.offset is None:
            raise TableError(f"{self.name}.{name}: offset is not known yet (see docs/DISCOVERY.md)")
        return int(fld.offset)

    def element(self, index: int) -> int:
        return index * int(self.stride)

    def address_of(self, base_addr: int, index: int, field_name: str) -> int:
        return base_addr + self.element(index) + self.field_offset(field_name)

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "Struct":
        base_raw = raw["base"]
        return cls(
            name=name,
            base=Discovery.from_dict(f"{name}.base", base_raw),
            stride=int(raw.get("stride") or 0),
            count=int(raw.get("count") or 1),
            fields={k: Field.from_dict(k, v) for k, v in raw.get("fields", {}).items()},
        )


@dataclass
class AddressTable:
    game: str = "Ape Escape 3"
    eras: dict = field(default_factory=dict)
    characters: dict[str, int] = field(default_factory=dict)
    layouts: dict[str, int] = field(default_factory=dict)
    globals: dict[str, Discovery] = field(default_factory=dict)
    structs: dict[str, Struct] = field(default_factory=dict)
    path: Path | None = None

    # ----------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: str | Path = DEFAULT_TABLE) -> "AddressTable":
        path = Path(path)
        raw = json.loads(path.read_text())
        return cls(
            game=raw.get("game", "Ape Escape 3"),
            eras=raw.get("eras", {}),
            characters={
                k: v for k, v in raw.get("characters", {}).items() if not k.startswith("_")
            },
            layouts={k: v for k, v in raw.get("layouts", {}).items() if not k.startswith("_")},
            globals={k: Discovery.from_dict(k, v) for k, v in raw.get("globals", {}).items()},
            structs={k: Struct.from_dict(k, v) for k, v in raw.get("structs", {}).items()},
            path=path,
        )

    def save(self, path: str | Path | None = None) -> None:
        """Write the table back, preserving the schema (used by `aecoops discover --write`)."""
        target = Path(path or self.path or DEFAULT_TABLE)
        raw = {
            "game": self.game,
            "eras": self.eras,
            "characters": self.characters,
            "layouts": self.layouts,
            "globals": {k: _discovery_dict(v) for k, v in self.globals.items()},
            "structs": {
                k: {
                    "base": _discovery_dict(v.base),
                    "stride": v.stride,
                    "count": v.count,
                    "fields": {
                        fk: {
                            "offset": fv.offset,
                            "width": fv.width,
                            "note": fv.note,
                            "recipe": fv.recipe,
                            "verified": fv.verified,
                        }
                        for fk, fv in v.fields.items()
                    },
                }
                for k, v in self.structs.items()
            },
        }
        target.write_text(json.dumps(raw, indent=2) + "\n")

    # ------------------------------------------------------------------ queries

    def required_names(self) -> list[str]:
        """Globals the co-op mod cannot run without."""
        return list(self.globals)

    def unresolved_globals(self) -> list[str]:
        return [n for n, d in self.globals.items() if d.addr is None and not d.signature]

    def unresolved_fields(self) -> list[str]:
        out = []
        for sname, struct in self.structs.items():
            for fname, fld in struct.fields.items():
                if fld.offset is None:
                    out.append(f"{sname}.{fname}")
        return out

    def unresolved_structs(self) -> list[str]:
        return [n for n, s in self.structs.items() if s.base.addr is None and not s.base.signature]

    def unresolved(self) -> list[str]:
        return self.unresolved_structs() + self.unresolved_globals() + self.unresolved_fields()

    def verified(self) -> list[str]:
        out = []
        for name, d in self.globals.items():
            if d.verified:
                out.append(name)
        for sname, struct in self.structs.items():
            if struct.base.verified:
                out.append(f"{sname}.base")
            for fname, fld in struct.fields.items():
                if fld.verified:
                    out.append(f"{sname}.{fname}")
        return out

    def resolve_all(self, pine: Pine, ranges=None) -> dict[str, int]:
        """Scan for every global that needs it and return name -> live address.

        ``ranges`` narrows where signatures are looked for. Scanning all 32 MB of
        main RAM plus the scratchpad takes several seconds, and some signatures only
        exist while a stage is loaded, so "look here, not everywhere" is worth being
        able to say.
        """
        out: dict[str, int] = {}
        problems: list[str] = []
        for name, d in self.globals.items():
            try:
                out[name] = d.resolve(pine, ranges)
            except LookupError as exc:
                problems.append(str(exc))
        if problems:
            raise TableError("could not resolve:\n  " + "\n  ".join(problems))
        return out

    def struct_base(self, name: str, pine: Pine) -> int:
        return self.structs[name].base.resolve(pine)

    def character_id(self, who: str) -> int:
        key = who.lower()
        if key not in self.characters:
            raise TableError(
                f"unknown character {who!r}; known: {sorted(self.characters)}. "
                "Fill in `characters` in data/addresses.json (see docs/DISCOVERY.md)."
            )
        value = self.characters[key]
        if value is None:
            raise TableError(
                f"the character id for {who!r} has not been measured yet. It is a small integer "
                "the engine stores in an actor's character field; docs/DISCOVERY.md#character-ids "
                "has the recipe. Set it with: "
                f"aecoops set characters.{key}=<id>"
            )
        return int(value)

    def layout_id(self, layout: str) -> int:
        key = layout.lower()
        if key not in self.layouts:
            raise TableError(f"unknown layout {layout!r}; known: {sorted(self.layouts)}")
        value = self.layouts[key]
        if value is None:
            raise TableError(
                f"the view-mode value for {layout!r} has not been measured yet "
                "(docs/DISCOVERY.md#view-mode). Note `single` is usually what the story stage "
                "writes today; the two split values come from a minigame that already draws two screens."
            )
        return int(value)

    def set_value(self, path: str, value) -> None:
        """``characters.kei=0``, ``globals.view_mode.addr=0x1b2c3d4``, ``structs.actor.stride=0x1f0``."""
        parts = path.split(".")
        if len(parts) == 2 and parts[0] == "characters":
            self.characters[parts[1]] = value
            return
        if len(parts) == 2 and parts[0] == "layouts":
            self.layouts[parts[1]] = value
            return
        if len(parts) == 3 and parts[0] == "globals":
            entry = self.globals.get(parts[1])
            if entry is None:
                raise TableError(f"no global {parts[1]!r}")
            if parts[2] not in ("addr", "expect", "signature", "signature_offset", "verified"):
                raise TableError(f"cannot set globals.{parts[1]}.{parts[2]}")
            setattr(entry, parts[2], value)
            return
        if len(parts) == 3 and parts[0] == "structs" and parts[2] in ("stride", "count", "verified"):
            setattr(self.structs[parts[1]], parts[2], value)
            return
        if len(parts) == 4 and parts[0] == "structs" and parts[2] == "fields":
            field = self.structs[parts[1]].fields.get(parts[3])
            if field is None:
                raise TableError(f"no field {parts[1]}.{parts[3]}")
            if parts[3] not in ("offset",) and len(parts) == 4:
                raise TableError(f"cannot set fields.{parts[3]} on a field; only .offset is settable")
            field.offset = value
            return
        if len(parts) == 3 and parts[0] == "structs" and parts[2] == "base":
            self.structs[parts[1]].base.addr = value
            return
        if len(parts) == 4 and parts[0] == "structs" and parts[2] == "base":
            entry = self.structs[parts[1]].base
            if parts[3] not in ("addr", "expect", "signature", "signature_offset", "verified"):
                raise TableError(f"cannot set structs.{parts[1]}.base.{parts[3]}")
            setattr(entry, parts[3], value)
            return
        raise TableError(f"don't know how to set {path!r}")

    def report(self) -> str:
        lines = [f"{self.game}: {len(self.globals)} globals, {len(self.structs)} structs"]
        for name, d in self.globals.items():
            where = f"{d.addr:#010x}" if d.addr else (f"sig {d.signature}" if d.signature else "NOT FOUND YET")
            mark = "verified" if d.verified else "unverified"
            lines.append(f"  [global] {name:<24} {where:<28} {mark}")
        for sname, struct in self.structs.items():
            base = struct.base
            where = f"{base.addr:#010x}" if base.addr else (f"sig {base.signature}" if base.signature else "NOT FOUND YET")
            lines.append(f"  [struct] {sname:<24} {where:<28} stride {struct.stride:#x}")
            for fname, fld in struct.fields.items():
                off = f"{fld.offset:#06x}" if fld.offset is not None else "  TBD "
                lines.append(f"      .{fname:<20} {off}  {'verified' if fld.verified else 'unverified'}")
        return "\n".join(lines)


def _discovery_dict(d: Discovery) -> dict:
    out: dict = {}
    if d.addr is not None:
        out["addr"] = d.addr
    if d.expect is not None:
        out["expect"] = d.expect
    if d.signature is not None:
        out["signature"] = d.signature
    if d.signature_offset:
        out["signature_offset"] = d.signature_offset
    if d.pointer_chain:
        out["pointer_chain"] = d.pointer_chain
    if d.note:
        out["note"] = d.note
    if d.recipe:
        out["recipe"] = d.recipe
    if d.verified:
        out["verified"] = d.verified
    return out


def iter_discoveries(table: AddressTable) -> Iterator[Discovery]:
    for d in table.globals.values():
        yield d
    for struct in table.structs.values():
        yield struct.base
