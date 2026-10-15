"""Minimal ELF32 reader: enough to get symbols out of a PS2 executable.

PS2 games ship an ``ELF`` (either on the disc as the boot file or inside a
packed archive). Its symbol table, when it has not been stripped, is the best
function map you can get: names, addresses and sizes, straight from the
developer's linker. Retail releases are usually stripped, but demo discs,
prototypes and a few retail builds are not -- and a Ghidra function-map CSV
gives the same thing for stripped ones.

Nothing here is game data; we only ever read it off the user's own disc, and we
never commit the results.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
SHT_SYMTAB = 2
SHT_DYNSYM = 11
STT_FUNC = 2
STT_OBJECT = 1

_HEADER = struct.Struct("<16sHHIIIIIHHHHHH")
_SECTION = struct.Struct("<IIIIIIIIII")
_SYMBOL = struct.Struct("<IIIBBH")

TYPE_NAMES = {0: "none", 1: "rel", 2: "exec", 3: "dyn", 4: "core"}


class ElfError(RuntimeError):
    pass


@dataclass
class Section:
    name: str
    type: int
    addr: int
    offset: int
    size: int
    entsize: int
    link: int


@dataclass
class Symbol:
    name: str
    value: int
    size: int
    info: int
    shndx: int

    @property
    def type(self) -> int:
        return self.info & 0x0F

    @property
    def is_function(self) -> bool:
        return self.type == STT_FUNC

    @property
    def end(self) -> int:
        return self.value + self.size


@dataclass
class Elf:
    path: Path
    type: int
    machine: int
    entry: int
    sections: list[Section]
    symbols: list[Symbol]

    def functions(self, min_size: int = 0x10) -> list[Symbol]:
        found = [s for s in self.symbols if s.is_function and s.value and s.size >= min_size]
        seen: set[int] = set()
        out = []
        for sym in sorted(found, key=lambda s: s.value):
            if sym.value in seen:
                continue
            seen.add(sym.value)
            out.append(sym)
        return out

    def objects(self, min_size: int = 4) -> list[Symbol]:
        return [s for s in self.symbols if s.type == STT_OBJECT and s.value and s.size >= min_size]

    def summary(self) -> str:
        return (
            f"{self.path.name}: ELF32 {TYPE_NAMES.get(self.type, '?')} machine {self.machine}, "
            f"entry {self.entry:#x}, {len(self.sections)} sections, {len(self.symbols)} symbols, "
            f"{len(self.functions())} functions"
        )


def parse(data: bytes, path: str | Path = "<memory>") -> Elf:
    if len(data) < _HEADER.size or data[:4] != ELF_MAGIC:
        raise ElfError("not an ELF file")
    if data[4] != 1:
        raise ElfError("not ELF32 (this reader only handles PS2's 32-bit ELFs)")
    if data[5] != 1:
        raise ElfError("big-endian ELF; PS2 executables are little-endian")

    (
        _ident,
        e_type,
        e_machine,
        _version,
        e_entry,
        _phoff,
        e_shoff,
        _flags,
        _ehsize,
        _phentsize,
        _phnum,
        _shentsize,
        e_shnum,
        e_shstrndx,
    ) = _HEADER.unpack_from(data, 0)
    if not e_shoff or not e_shnum:
        raise ElfError("section table is missing (fully stripped); use a Ghidra function map instead")

    raw_sections = [
        _SECTION.unpack_from(data, e_shoff + i * _SECTION.size) for i in range(e_shnum)
    ]
    strtab = b""
    if e_shstrndx < e_shnum:
        _, _, _, _, off, size, _, _, _, _ = raw_sections[e_shstrndx]
        strtab = data[off : off + size]

    sections: list[Section] = []
    for sh_name, sh_type, _, sh_addr, sh_offset, sh_size, sh_link, _, _, sh_entsize in raw_sections:
        sections.append(
            Section(
                name=_cstr(strtab, sh_name),
                type=sh_type,
                addr=sh_addr,
                offset=sh_offset,
                size=sh_size,
                entsize=sh_entsize,
                link=sh_link,
            )
        )

    symbols: list[Symbol] = []
    for sec in sections:
        if sec.type not in (SHT_SYMTAB, SHT_DYNSYM) or not sec.entsize:
            continue
        if sec.link >= len(sections):
            continue
        strings = data[sections[sec.link].offset : sections[sec.link].offset + sections[sec.link].size]
        count = sec.size // sec.entsize
        for i in range(count):
            st_name, st_value, st_size, st_info, _, st_shndx = _SYMBOL.unpack_from(data, sec.offset + i * sec.entsize)
            symbols.append(
                Symbol(
                    name=_cstr(strings, st_name),
                    value=st_value,
                    size=st_size,
                    info=st_info,
                    shndx=st_shndx,
                )
            )
    return Elf(
        path=Path(path),
        type=e_type,
        machine=e_machine,
        entry=e_entry,
        sections=sections,
        symbols=symbols,
    )


def load(path: str | Path) -> Elf:
    return parse(Path(path).read_bytes(), path)


def _cstr(blob: bytes, offset: int) -> str:
    if offset >= len(blob):
        return ""
    end = blob.find(b"\x00", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("utf-8", "replace")


def read_ghidra_function_map(path: str | Path) -> list[tuple[str, int]]:
    """Read a Ghidra function CSV: ``name,address[,size]`` per line, hex addresses."""
    out: list[tuple[str, int]] = []
    for line in Path(path).read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[1]:
            continue
        try:
            addr = int(parts[1], 16)
        except ValueError:
            continue  # header row
        out.append((parts[0], addr))
    return out
