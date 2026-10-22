"""Patch sets: verified, reversible memory edits over PINE.

Two rules make PS2 patching safe enough to ship:

1. **Every patch declares the bytes it expects to find.** Patching a different
   revision of a game with another revision's addresses corrupts it silently, so
   a mismatch aborts the whole set before a single byte is written.
2. **Every write is journalled to disk before it happens.** ``um``-style: take a
   snapshot, patch, and you can always put the original bytes back over PINE,
   even after a crash or a savestate load.

The journal is your own bytes -- addresses and original values -- never game
data, so it is safe to keep next to the mod.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .pine import Pine, PineError

WORD = 4


@dataclass
class Patch:
    """One verified edit: ``data`` overwrites ``expect`` at ``addr``.

    ``data`` is a string of *bytes written in order*, not a word. For machine
    code that means the little-endian encoding of each instruction, which is
    what ``from_words`` is for -- writing ``"0c050000"`` as a word value by hand
    and getting ``00 00 05 0c`` in memory is the classic way to spend an hour
    debugging a patch that "applied fine".
    """

    name: str
    addr: int
    data: str  # hex
    expect: str | None = None  # hex, ``??`` = wildcard nibble
    note: str = ""

    def __post_init__(self) -> None:
        self.addr = int(self.addr)
        if self.addr % WORD:
            raise ValueError(f"{self.name}: address {self.addr:#x} is not word aligned")
        self.data = _normalise_hex(self.data, "data")
        if len(self.data) % WORD:
            raise ValueError(f"{self.name}: patch data must be a multiple of 4 bytes")
        if self.expect is not None:
            self.expect = _normalise_expect(self.expect)
            if len(self.expect) != len(self.data):
                raise ValueError(f"{self.name}: expect and data must be the same length")

    @property
    def bytes(self) -> bytes:
        return bytes.fromhex(self.data)

    @property
    def expect_bytes(self) -> bytes | None:
        return None if self.expect is None else bytes.fromhex(self.expect.replace("?", "0"))

    @property
    def wildcarded(self) -> bool:
        return self.expect is not None and "?" in self.expect

    @property
    def wildcard_mask(self) -> bytes:
        """One mask byte per expect byte: nibble-aware, so ``aab?`` is expressible.

        The mask has to be built from *pairs* of hex digits. Building it per
        character instead yields twice as many mask bytes as data bytes, which
        silently makes every comparison fail.
        """
        if self.expect is None:
            return b""
        out = bytearray()
        for i in range(0, len(self.expect), 2):
            high, low = self.expect[i], self.expect[i + 1]
            # The mask says which bits must match, so a '?' nibble contributes
            # nothing and a real nibble contributes its four bits.
            out.append((0xF0 if high != "?" else 0) | (0x0F if low != "?" else 0))
        return bytes(out)

    def matches(self, actual: bytes) -> bool:
        """Byte-wise compare that honours the wildcard nibbles in ``expect``."""
        if self.expect_bytes is None:
            return True
        if len(actual) != len(self.expect_bytes):
            return False
        for got, want, mask in zip(actual, self.expect_bytes, self.wildcard_mask):
            if (got & mask) != (want & mask):
                return False
        return True

    @property
    def end(self) -> int:
        return self.addr + len(self.bytes)

    @classmethod
    def words(cls, name: str, addr: int, words: Sequence[int], expect: Sequence[int] | None = None, note: str = "") -> "Patch":
        """A patch made of instruction words, encoded little-endian for you."""
        data = b"".join(int(w).to_bytes(4, "little") for w in words)
        expected = (
            None
            if expect is None
            else b"".join(int(w).to_bytes(4, "little") for w in expect).hex()
        )
        return cls(name=name, addr=addr, data=data.hex(), expect=expected, note=note)


def _normalise_hex(text: str, what: str) -> str:
    cleaned = "".join(text.split()).lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) % 2:
        raise ValueError(f"{what} hex string must have an even number of digits")
    try:
        bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"{what} is not valid hex: {text!r}") from exc
    return cleaned


def _normalise_expect(text: str) -> str:
    cleaned = "".join(text.split()).lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) % 2:
        raise ValueError("expect hex string must have an even number of digits")
    for c in cleaned:
        if c not in "0123456789abcdef?":
            raise ValueError(f"expect contains {c!r}; use hex digits or '?' wildcards")
    return cleaned


@dataclass
class PatchSet:
    """A named group of patches for one exact game build."""

    name: str
    serials: list[str] = field(default_factory=list)
    uuids: list[str] = field(default_factory=list)
    disc_versions: list[str] = field(default_factory=list)
    description: str = ""
    patches: list[Patch] = field(default_factory=list)

    # ------------------------------------------------------------- construction

    @classmethod
    def from_dict(cls, raw: dict) -> "PatchSet":
        return cls(
            name=raw["name"],
            serials=list(raw.get("serials", [])),
            uuids=list(raw.get("uuids", [])),
            disc_versions=list(raw.get("disc_versions", [])),
            description=raw.get("description", ""),
            patches=[Patch(**p) for p in raw.get("patches", [])],
        )

    @classmethod
    def load(cls, path: str | Path) -> "PatchSet":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "serials": self.serials,
            "uuids": self.uuids,
            "disc_versions": self.disc_versions,
            "description": self.description,
            "patches": [asdict(p) for p in self.patches],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    def fingerprint(self) -> str:
        """Stable hash of everything that affects the bytes written."""
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    # ------------------------------------------------------------------ checks

    def accepts(self, serial: str, uuid: str = "", disc_version: str = "") -> tuple[bool, str]:
        """Does this set claim the running disc? Empty lists mean ``any``."""
        if self.serials and serial.upper() not in {s.upper() for s in self.serials}:
            return False, f"disc serial {serial!r} is not in {self.serials}"
        if self.uuids and uuid.lower() not in {u.lower() for u in self.uuids}:
            return False, f"disc UUID {uuid!r} is not in {self.uuids}"
        if self.disc_versions and disc_version not in set(self.disc_versions):
            return False, f"disc version {disc_version!r} is not in {self.disc_versions}"
        return True, "ok"

    def overlapping(self) -> list[tuple[Patch, Patch]]:
        """Patches whose byte ranges collide -- always a bug in the set."""
        out: list[tuple[Patch, Patch]] = []
        ordered = sorted(self.patches, key=lambda p: p.addr)
        for a, b in zip(ordered, ordered[1:]):
            if b.addr < a.end:
                out.append((a, b))
        return out

    # ------------------------------------------------------------------ apply

    def verify(self, pine: Pine) -> list[str]:
        """Return one human-readable problem per patch that does not match."""
        problems: list[str] = []
        for patch in self.patches:
            actual = pine.read_bytes(patch.addr, len(patch.bytes))
            if actual == patch.bytes:
                continue  # already applied: fine
            if patch.expect is None:
                # A patch with no expect string can only ever be written blind,
                # so it is a problem to report rather than a thing to trust.
                problems.append(
                    f"{patch.name}: memory at {patch.addr:#x} is {actual.hex()} and the patch has no "
                    f"expect string, so it cannot be verified"
                )
                continue
            if patch.matches(actual):
                continue
            problems.append(
                f"{patch.name}: expected {patch.expect} at {patch.addr:#x}, found {actual.hex()}"
            )
        return problems

    def apply(self, pine: Pine, journal: Path | None = None, force: bool = False) -> "ApplyReport":
        """Verify every patch against live memory, then write them all.

        ``journal`` is written *before* the first byte is changed.
        """
        report = ApplyReport(patchset=self.name, fingerprint=self.fingerprint())
        clashes = self.overlapping()
        if clashes:
            report.errors.extend(f"{a.name} overlaps {b.name}" for a, b in clashes)
            return report

        journal_entries = []
        for patch in self.patches:
            actual = pine.read_bytes(patch.addr, len(patch.bytes))
            if actual == patch.bytes:
                report.skipped.append(patch.name)
                continue
            if not patch.matches(actual) and not force:
                report.errors.append(
                    f"{patch.name}: expected {patch.expect} at {patch.addr:#x}, found {actual.hex()} "
                    f"-- refusing to patch a build this set was not made for"
                )
                continue
            journal_entries.append(
                {"name": patch.name, "addr": patch.addr, "before": actual.hex(), "after": patch.data}
            )
            report.applied.append(patch.name)

        if report.errors:
            report.errors.append("nothing was written; the set aborted before the first change")
            return report

        journal_payload = {
            "patchset": self.name,
            "fingerprint": report.fingerprint,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": journal_entries,
        }
        if journal is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(json.dumps(journal_payload, indent=2) + "\n")
        report.journal = journal_payload

        for entry in journal_entries:
            pine.write_bytes(entry["addr"], bytes.fromhex(entry["after"]))
        return report

    def apply_from_journal(self, pine: Pine, journal: Path) -> int:
        """Re-apply the exact bytes a journal recorded (used after a savestate load)."""
        payload = json.loads(Path(journal).read_text())
        for entry in payload["entries"]:
            pine.write_bytes(entry["addr"], bytes.fromhex(entry["after"]))
        return len(payload["entries"])


@dataclass
class ApplyReport:
    patchset: str = ""
    fingerprint: str = ""
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    journal: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [f"patch set {self.patchset!r} ({self.fingerprint[:12]})"]
        if self.applied:
            lines.append(f"  applied: {len(self.applied)}")
            lines.extend(f"    + {n}" for n in self.applied)
        if self.skipped:
            lines.append(f"  already applied: {len(self.skipped)}")
            lines.extend(f"    = {n}" for n in self.skipped)
        if self.errors:
            lines.append("  FAILED:")
            lines.extend(f"    ! {e}" for e in self.errors)
        return "\n".join(lines)


def revert(pine: Pine, journal: Path) -> int:
    """Restore the original bytes recorded in a journal. Returns writes made."""
    payload = json.loads(Path(journal).read_text())
    count = 0
    for entry in payload["entries"]:
        pine.write_bytes(int(entry["addr"]), bytes.fromhex(entry["before"]))
        count += 1
    return count


def load_all(directory: str | Path) -> list[PatchSet]:
    """Load every ``*.patchset.json`` in a directory, sorted by name."""
    out = []
    for path in sorted(Path(directory).glob("*.patchset.json")):
        out.append(PatchSet.load(path))
    return out


def pick(sets: Iterable[PatchSet], serial: str, uuid: str = "", disc_version: str = "") -> PatchSet | None:
    """The first set that accepts this disc."""
    for candidate in sets:
        ok, _ = candidate.accepts(serial, uuid, disc_version)
        if ok:
            return candidate
    return None


def check_alignment(addrs: Sequence[int]) -> list[int]:
    """Addresses that are not word aligned (PS2 code must be 4-byte aligned)."""
    return [a for a in addrs if a % WORD]


__all__ = [
    "ApplyReport",
    "Patch",
    "PatchSet",
    "PineError",
    "check_alignment",
    "load_all",
    "pick",
    "revert",
]
