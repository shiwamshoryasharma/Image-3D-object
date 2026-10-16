"""Version parsing: the 17 samples from research/blender_detection_windows.md section 3 (+ helpers)."""

from __future__ import annotations

import pytest

from launcher import BlenderVersion, format_min_version, parse_blender_version, parse_min_version

TBB_NOISE = (
    "TBBmalloc: skip allocation functions replacement in ucrtbase.dll: unknown prologue for function _msize\r\n"
    "Blender 4.2.17 LTS\r\n\tbuild date: 2026-01-20\r\n\tbuild hash: 76b996a81c95\r\n"
    "\tbuild branch: blender-v4.2-release\r\n"
)

# (input, expected tuple, cycle, lts, build_hash, build_branch, build_date, label)
SAMPLES = [
    ("Blender 5.2.2 LTS", (5, 2, 2), "release", True, None, None, None, "5.2.2 LTS"),
    ("Blender 4.2.17 LTS", (4, 2, 17), "release", True, None, None, None, "4.2.17 LTS"),
    ("Blender 5.1.0", (5, 1, 0), "release", False, None, None, None, "5.1.0"),
    ("Blender 5.3.0 Alpha", (5, 3, 0), "alpha", False, None, None, None, "5.3.0 Alpha"),
    ("Blender 5.2.0 Beta", (5, 2, 0), "beta", False, None, None, None, "5.2.0 Beta"),
    ("Blender 5.2.0 Release Candidate", (5, 2, 0), "rc", False, None, None, None, "5.2.0 Release Candidate"),
    ("Blender 4.2.0 LTS Beta", (4, 2, 0), "beta", True, None, None, None, "4.2.0 LTS Beta"),
    ("Blender 5.3.0 Alpha (hash 1a2b3c4d5e6f built 2026-10-01 01:02:03)", (5, 3, 0), "alpha", False,
     "1a2b3c4d5e6f", None, "2026-10-01", "5.3.0 Alpha"),
    ("Blender 5.2.2 LTS (hash d13f752e3b9c built 2026-09-15 01:37:04)", (5, 2, 2), "release", True,
     "d13f752e3b9c", None, "2026-09-15", "5.2.2 LTS"),
    ("Blender 2.79 (sub 0)", (2, 79, 0), "release", False, None, None, None, "2.79.0"),
    ("Blender 2.80 (sub 75)", (2, 80, 0), "release", False, None, None, None, "2.80.0"),
    ("Blender 2.93.18", (2, 93, 18), "release", False, None, None, None, "2.93.18"),
    (TBB_NOISE, (4, 2, 17), "release", True, "76b996a81c95", "blender-v4.2-release", "2026-01-20", "4.2.17 LTS"),
    ("Blender 5.3.0 Alpha\n\tbuild hash: unknown\n\tbuild branch: main\n", (5, 3, 0), "alpha", False, "unknown",
     "main", None, "5.3.0 Alpha"),
    ("Blender 5.10.0", (5, 10, 0), "release", False, None, None, None, "5.10.0"),
]
NOT_BLENDER = ["Bforartists 4.2.0", "Error: Not freed memory blocks: 1"]


def test_sample_count_matches_research() -> None:
    assert len(SAMPLES) + len(NOT_BLENDER) == 17


@pytest.mark.parametrize(("text", "tup", "cycle", "lts", "build_hash", "branch", "date", "label"), SAMPLES,
                         ids=[s[0].splitlines()[-1][:40] if "\n" in s[0] else s[0][:40] for s in SAMPLES])
def test_parse_samples(text: str, tup: tuple[int, int, int], cycle: str, lts: bool, build_hash: str | None,
                       branch: str | None, date: str | None, label: str) -> None:
    version = parse_blender_version(text)
    assert version is not None
    assert version.tuple == tup
    assert version.cycle == cycle
    assert version.lts is lts
    assert version.is_stable is (cycle == "release")
    assert version.build_hash == build_hash
    assert version.build_branch == branch
    assert version.build_date == date
    assert version.label == label
    assert version.raw.startswith("Blender ")


@pytest.mark.parametrize("text", NOT_BLENDER)
def test_parse_rejects_non_blender(text: str) -> None:
    assert parse_blender_version(text) is None


def test_parse_real_5_2_output_with_build_lines() -> None:
    text = (
        "Blender 5.2.2 LTS\r\n\tbuild date: 2026-09-15\r\n\tbuild time: 01:37:04\r\n\tbuild commit date: 2026-09-14\r\n"
        "\tbuild hash: d13f752e3b9c\r\n\tbuild branch: blender-v5.2-release\r\n\tbuild platform: Windows\r\n"
        "\tbuild type: Release\r\n\tbuild link flags: /MACHINE:X64 /SUBSYSTEM:CONSOLE\r\n"
    )
    version = parse_blender_version(text)
    assert version == BlenderVersion(5, 2, 2, "release", True, "d13f752e3b9c", "blender-v5.2-release", "2026-09-15",
                                     "Blender 5.2.2 LTS")


def test_parse_empty_and_letter_release() -> None:
    assert parse_blender_version("") is None
    legacy = parse_blender_version("Blender 2.79b")
    assert legacy is not None and legacy.tuple == (2, 79, 2)


def test_sort_key_orders_cycles_and_versions() -> None:
    texts = ["5.2.0 Beta", "5.2.0", "5.2.0 Release Candidate", "5.2.0 Alpha", "5.10.0", "5.2.2 LTS", "4.5.3"]
    parsed = [parse_blender_version(f"Blender {t}") for t in texts]
    ordered = sorted(parsed, key=lambda v: v.sort_key(), reverse=True)  # type: ignore[union-attr]
    assert [v.label for v in ordered] == [  # type: ignore[union-attr]
        "5.10.0", "5.2.2 LTS", "5.2.0", "5.2.0 Release Candidate", "5.2.0 Beta", "5.2.0 Alpha", "4.5.3"
    ]
    assert parsed[1].at_least((5, 1, 0)) and not parsed[6].at_least((5, 1, 0))  # type: ignore[union-attr]
    assert parsed[0].label_without_lts == "5.2.0 Beta"  # type: ignore[union-attr]
    assert parse_blender_version("Blender 4.2.17 LTS").label_without_lts == "4.2.17"  # type: ignore[union-attr]


def test_parse_min_version() -> None:
    assert parse_min_version("5.1") == (5, 1, 0)
    assert parse_min_version(" 5.1.2 ") == (5, 1, 2)
    assert format_min_version((5, 1, 0)) == "5.1"
    assert format_min_version((5, 1, 2)) == "5.1.2"
    for bad in ("", "5", "five", "5.1.x"):
        with pytest.raises(ValueError):
            parse_min_version(bad)
