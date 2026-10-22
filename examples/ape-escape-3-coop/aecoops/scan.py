"""RAM scanning over PINE: how the mod finds its own addresses.

Addresses in a PS2 game move between regional releases and between the original
pressings and the Greatest Hits/Platinum reprints. Hard-coding one revision's
numbers means the mod works for exactly one ISO, so instead every address in
``data/addresses.json`` carries a *signature*: a short byte pattern from the code
or data around it, plus how to get from the match to the value we want. The
scanner re-derives the address at install time on the user's own disc.

Scanning 32 MB of EE RAM over PINE takes seconds, because a whole 256 KiB chunk
is read in a single batched round trip (8192 reads per request).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .pine import Pine

# EE main RAM as the game sees it, plus the 16 KiB scratchpad.
EE_RAM = (0x00100000, 0x02000000)
EE_SCRATCHPAD = (0x70000000, 0x70004000)
DEFAULT_RANGES: tuple[tuple[int, int], ...] = (EE_RAM, EE_SCRATCHPAD)

CHUNK = 256 * 1024


class ScanError(RuntimeError):
    pass


@dataclass
class Match:
    addr: int
    data: bytes

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"<Match {self.addr:#010x} {self.data.hex()}>"


def _normalise_pattern(pattern: str | bytes) -> tuple[bytes, bytes]:
    """Turn ``"11 22 ?? 44"`` into (literal bytes, mask bytes).

    A string whose tokens are all hex (or ``??``) is read as hex, so ``"de ad"``
    means two bytes. Anything else is read as literal text, so ``"MONKEY"`` and
    ``"A1B2"``-style mixed strings do the obvious thing -- hex is the narrow case,
    text is the fallback, and a string that is ambiguous is treated as hex because
    that is what a signature parser is for.
    """
    if isinstance(pattern, bytes):
        return pattern, b"\xff" * len(pattern)
    text = pattern.replace("0x", " ")
    if not re.fullmatch(r"[0-9a-fA-F?\s]*", text) or not text.strip():
        return pattern.encode("utf-8"), b"\xff" * len(pattern.encode("utf-8"))
    if re.fullmatch(r"[0-9a-fA-F?]{2,}", text.strip()) and " " not in text.strip():
        text = " ".join(text.strip()[i : i + 2] for i in range(0, len(text.strip()), 2))
    tokens = text.split()
    literal = bytearray()
    mask = bytearray()
    for token in tokens:
        if token in ("?", "??"):
            literal.append(0)
            mask.append(0)
            continue
        if "?" in token:
            raise ValueError(f"partial-byte wildcards are not supported: {token!r}")
        if len(token) != 2:
            raise ValueError(f"bad pattern token {token!r}")
        literal.append(int(token, 16))
        mask.append(0xFF)
    if not literal:
        raise ValueError("empty pattern")
    if not any(mask):
        raise ValueError("a pattern of pure wildcards matches everything; be more specific")
    return bytes(literal), bytes(mask)


def find(pine: Pine, needle: str | bytes, ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES, limit: int = 16) -> list[Match]:
    """Find an exact byte string (chunk borders included)."""
    literal, mask = _normalise_pattern(needle)
    return _scan(pine, literal, mask, ranges, limit)


def find_pattern(
    pine: Pine, pattern: str, ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES, limit: int = 16
) -> list[Match]:
    """Find a pattern with ``??`` wildcard bytes."""
    literal, mask = _normalise_pattern(pattern)
    return _scan(pine, literal, mask, ranges, limit)


def _scan(
    pine: Pine,
    literal: bytes,
    mask: bytes,
    ranges: Sequence[tuple[int, int]],
    limit: int,
    chunk: int = CHUNK,
) -> list[Match]:
    size = len(literal)
    out: list[Match] = []
    seen: set[int] = set()
    for start, end in ranges:
        addr = start
        carry = b""
        while addr < end:
            take = min(chunk, end - addr)
            blob = carry + pine.read_bytes(addr, take)
            base = addr - len(carry)
            pos = 0
            while True:
                idx = _search(blob, literal, mask, pos)
                if idx < 0:
                    break
                found = base + idx
                if found + size <= end and found not in seen:
                    seen.add(found)
                    out.append(Match(found, blob[idx : idx + size]))
                    if len(out) >= limit:
                        return out
                pos = idx + 1
            carry = blob[-(size - 1) :] if size > 1 else b""
            addr += take
    return out


def _search(hay: bytes, literal: bytes, mask: bytes, start: int) -> int:
    if all(b == 0xFF for b in mask):
        return hay.find(literal, start)
    # Wildcards are rare and short, so a plain scan is plenty.
    size = len(literal)
    for i in range(start, len(hay) - size + 1):
        if all((hay[i + j] & mask[j]) == literal[j] for j in range(size)):
            return i
    return -1


def find_all_pointer_targets(
    pine: Pine, target: int, ranges: Sequence[tuple[int, int]] = DEFAULT_RANGES, limit: int = 32
) -> list[int]:
    """Addresses whose stored word equals ``target`` -- step one of a pointer chain."""
    needle = target.to_bytes(4, "little")
    return [m.addr for m in find(pine, needle, ranges, limit)]


def deref(pine: Pine, addr: int) -> int:
    """Read a 32-bit pointer."""
    return pine.read32(addr)


def chase(pine: Pine, base: int, offsets: Iterable[int]) -> int:
    """Follow a pointer chain: read ``base``, then add each offset and re-read."""
    current = base
    for offset in offsets:
        current = pine.read32(current) + offset
    return current


def read_c_string(pine: Pine, addr: int, max_len: int = 64) -> str:
    raw = pine.read_bytes(addr, max_len)
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


def read_int_table(pine: Pine, addr: int, count: int, width: int = 4) -> list[int]:
    return pine.read_words([addr + i * width for i in range(count)], width)


@dataclass
class Discovery:
    """A named address that is either pinned or derived from a signature."""

    name: str
    addr: int | None = None
    expect: str | None = None
    signature: str | None = None
    signature_offset: int = 0
    pointer_chain: list[int] = field(default_factory=list)
    note: str = ""
    recipe: str = ""
    verified: str | None = None

    def resolve(self, pine: Pine, ranges: Sequence[tuple[int, int]] | None = None) -> int:
        """Return the live address, scanning if the table has not pinned one.

        A pinned address is still walked through ``pointer_chain``: pinning the
        global that *holds* the stage context pointer is the durable way to name
        something, because the context itself moves every time a stage loads.

        ``ranges`` narrows the search; useful because scanning all 32 MB takes a
        few seconds and because some code only exists while a stage is loaded.
        """
        if self.addr is not None:
            return self._follow(pine, int(self.addr))
        if not self.signature:
            raise LookupError(
                f"{self.name}: no address and no signature. Run `aecoops discover` on your own "
                "ISO and fill in data/addresses.json (see docs/DISCOVERY.md)."
            )
        hits = find_pattern(pine, self.signature, ranges or DEFAULT_RANGES, limit=8)
        if not hits:
            raise LookupError(
                f"{self.name}: signature {self.signature!r} was not found in RAM. Either the "
                "signature is stale for this build, or you are not in the scene the signature "
                "lives in (some code is only resident while a stage is loaded)."
            )
        if len(hits) > 1:
            raise LookupError(
                f"{self.name}: signature {self.signature!r} matched {len(hits)} places "
                f"({[hex(h.addr) for h in hits]}); lengthen it until it is unique."
            )
        return self._follow(pine, hits[0].addr + int(self.signature_offset))

    def _follow(self, pine: Pine, addr: int) -> int:
        for offset in self.pointer_chain:
            addr = pine.read32(addr) + offset
        return addr

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "Discovery":
        return cls(
            name=name,
            addr=raw.get("addr"),
            expect=raw.get("expect"),
            signature=raw.get("signature"),
            signature_offset=int(raw.get("signature_offset") or 0),
            pointer_chain=list(raw.get("pointer_chain", [])),
            note=raw.get("note", ""),
            recipe=raw.get("recipe", ""),
            verified=raw.get("verified"),
        )
