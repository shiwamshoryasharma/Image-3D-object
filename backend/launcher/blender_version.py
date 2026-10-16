"""Blender version model and ``blender --version`` parser.

Format (``blenkernel/intern/blender.cc`` ``blender_version_init``): ``"%d.%01d.%d%s%s"`` = major, minor, patch,
``" LTS"`` (version suffix, independent of the cycle), cycle text (``" Alpha"``, ``" Beta"``,
``" Release Candidate"``, nothing for release). ``--version`` prints ``Blender <version>`` followed by tab-indented
``build <key>: <value>`` lines; the ``-b`` banner is ``Blender <version> (hash <h> built <date> <time>)``; legacy
2.7x/2.80 builds print ``Blender 2.79 (sub 0)``. Blender 4.2 writes a ``TBBmalloc: ...`` noise line on stderr first,
so the version line is searched for anywhere in the output (MULTILINE), never taken from line 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

VersionCycle = Literal["release", "rc", "beta", "alpha"]

MIN_SUPPORTED_VERSION: Final[tuple[int, int, int]] = (5, 1, 0)

_CYCLE_RANK: Final[dict[str, int]] = {"release": 3, "rc": 2, "beta": 1, "alpha": 0}
_CYCLE_TEXT: Final[dict[str, str]] = {"release": "", "rc": " Release Candidate", "beta": " Beta", "alpha": " Alpha"}
_TAG_TO_CYCLE: Final[dict[str, VersionCycle]] = {
    "Alpha": "alpha",
    "Beta": "beta",
    "Release Candidate": "rc",
    "RC": "rc",
}

VERSION_RE: Final = re.compile(
    r"^Blender[ \t]+(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?P<char>[a-z])?"
    r"(?P<tags>(?:[ \t]+(?:LTS|Alpha|Beta|Release[ \t]+Candidate|RC))*)"
    r"(?:[ \t]+\(sub[ \t]+(?P<sub>\d+)\))?"
    r"(?:[ \t]+\(hash[ \t]+(?P<hash>[0-9a-fA-F]+|unknown)[ \t]+built[ \t]+(?P<built>[^)]*)\))?"
    r"[ \t]*\r?$",
    re.MULTILINE,
)
_BUILD_LINE_RE: Final = re.compile(r"^[ \t]+build (?P<key>[a-z +]+?):[ \t]?(?P<val>.*?)[ \t]*\r?$", re.MULTILINE)
_TAG_RE: Final = re.compile(r"LTS|Alpha|Beta|Release[ \t]+Candidate|RC")
_MIN_VERSION_RE: Final = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?\s*$")


@dataclass(frozen=True, order=False, slots=True)
class BlenderVersion:
    """A parsed Blender version (``bpy.app.version`` + ``version_cycle`` + LTS flag + build info)."""

    major: int
    minor: int
    patch: int
    cycle: VersionCycle = "release"
    lts: bool = False
    build_hash: str | None = None
    build_branch: str | None = None
    build_date: str | None = None
    raw: str = ""

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def is_stable(self) -> bool:
        return self.cycle == "release"

    @property
    def number(self) -> str:
        """``"5.2.2"`` (no LTS / cycle text)."""
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def label(self) -> str:
        """Blender's own version string: ``"5.2.2 LTS"``, ``"5.3.0 Beta"``, ``"5.2.0 Release Candidate"``."""
        return self.number + (" LTS" if self.lts else "") + _CYCLE_TEXT[self.cycle]

    @property
    def label_without_lts(self) -> str:
        """``"4.2.17"``, ``"5.3.0 Beta"`` - the form used in user prompts."""
        return self.number + _CYCLE_TEXT[self.cycle]

    def at_least(self, minimum: tuple[int, int, int]) -> bool:
        return self.tuple >= tuple(minimum)

    def sort_key(self) -> tuple[int, int, int, int]:
        """``(major, minor, patch, cycle_rank)`` with release=3 > rc=2 > beta=1 > alpha=0."""
        return (self.major, self.minor, self.patch, _CYCLE_RANK[self.cycle])

    def __str__(self) -> str:
        return self.label


def parse_blender_version(output: str) -> BlenderVersion | None:
    """Parse ``blender --version`` output (or the ``-b`` banner). Returns ``None`` if no version line is found.

    Tolerates noise lines before/after the version line (e.g. the 4.2 ``TBBmalloc`` stderr line) and CRLF endings.
    """
    if not output:
        return None
    match = VERSION_RE.search(output)
    if match is None:
        return None
    tags = [re.sub(r"[ \t]+", " ", tag) for tag in _TAG_RE.findall(match.group("tags") or "")]
    cycle: VersionCycle = "release"
    for tag in tags:
        cycle = _TAG_TO_CYCLE.get(tag, cycle)
    major, minor = int(match.group("major")), int(match.group("minor"))
    if match.group("patch") is not None:
        patch = int(match.group("patch"))
    elif match.group("char"):
        # Legacy letter releases: 2.79a -> 2.79.1, 2.79b -> 2.79.2 (keeps ordering correct).
        patch = ord(match.group("char")) - ord("a") + 1
    else:
        patch = 0
    build = {m.group("key").strip().replace(" ", "_"): m.group("val") for m in _BUILD_LINE_RE.finditer(output)}
    built = (match.group("built") or "").strip()
    return BlenderVersion(
        major=major,
        minor=minor,
        patch=patch,
        cycle=cycle,
        lts="LTS" in tags,
        build_hash=build.get("hash") or match.group("hash") or None,
        build_branch=build.get("branch") or None,
        build_date=build.get("date") or (built.split(" ")[0] if built else None),
        raw=match.group(0).strip(),
    )


def parse_min_version(text: str) -> tuple[int, int, int]:
    """``"5.1"`` -> ``(5, 1, 0)``, ``"5.1.2"`` -> ``(5, 1, 2)``. Raises ``ValueError`` for anything else."""
    match = _MIN_VERSION_RE.match(text or "")
    if match is None:
        raise ValueError(f"Invalid Blender version {text!r} (expected MAJOR.MINOR or MAJOR.MINOR.PATCH)")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def format_min_version(version: tuple[int, int, int]) -> str:
    """``(5, 1, 0)`` -> ``"5.1"``, ``(5, 1, 2)`` -> ``"5.1.2"`` (used in user-facing messages)."""
    major, minor, patch = (tuple(version) + (0, 0, 0))[:3]
    return f"{major}.{minor}" if not patch else f"{major}.{minor}.{patch}"
