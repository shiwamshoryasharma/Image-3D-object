"""BlenderDetector: candidate sources (env vars pointed at temp dirs with fake files), launcher mapping, dedupe,
missing files, probe classification and the (path, size, mtime_ns) probe cache. No real Blender is executed."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import _winapi
import pytest
from lt_helpers import FAKE_EXE, FakeRunner, fake_pe_reader

from launcher import BlenderDetector, LauncherSettings, normalize_executable
from launcher.blender_detector import installation_id

ENV_SOURCES = ("override", "embedded_dir", "user", "program_files", "path", "scoop", "winget", "choco",
               "blender_launcher", "store")


def touch(path: Path, data: bytes = FAKE_EXE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def fake_tree(out_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Fake install locations; every relevant environment variable points into ``out_dir``."""
    pf64 = out_dir / "pf64"
    lad = out_dir / "lad"
    exes = {
        "pf_52": touch(pf64 / "Blender Foundation" / "Blender 5.2" / "blender.exe"),
        "pf_launcher_52": touch(pf64 / "Blender Foundation" / "Blender 5.2" / "blender-launcher.exe"),
        "lad_51": touch(lad / "Programs" / "Blender Foundation" / "Blender 5.1" / "blender.exe"),
        "path_exe": touch(out_dir / "pathdir" / "blender.exe"),
        "scoop_522": touch(out_dir / "scoop" / "apps" / "blender" / "5.2.2" / "blender.exe"),
        "winget": touch(lad / "Microsoft" / "WinGet" / "Packages" / "BlenderFoundation.Blender_x" / "blender-5.2.2"
                        / "blender.exe"),
        "winget_too_deep": touch(lad / "Microsoft" / "WinGet" / "Packages" / "BlenderFoundation.Blender_x" / "a" / "b"
                                 / "c" / "d" / "blender.exe"),
        "choco": touch(out_dir / "choco" / "lib" / "blender" / "tools" / "blender-5.2.2" / "blender.exe"),
        "bl_launcher": touch(lad / "Blender Launcher" / "stable" / "blender-5.2.2-stable" / "blender.exe"),
        "store": touch(lad / "Microsoft" / "WindowsApps" / "blender.exe"),
        "embedded": touch(out_dir / "rt" / "blender-5.2.2-windows-x64" / "blender.exe"),
        "extra_nested": touch(out_dir / "extra" / "portable-5.1" / "blender.exe"),
        "override_dir_exe": touch(out_dir / "override" / "blender.exe"),
    }
    touch(out_dir / "override" / "blender-launcher.exe")
    # folders without an executable (like the stale "Blender 4.4"/"4.5" leftovers) must be ignored
    (pf64 / "Blender Foundation" / "Blender 4.4" / "4.4" / "scripts").mkdir(parents=True)
    (pf64 / "Blender Foundation" / "Blender 4.5").mkdir(parents=True)
    (out_dir / "rt" / "downloads").mkdir(parents=True)
    # scoop 'current' is a junction to the version folder -> must dedupe via realpath
    _winapi.CreateJunction(str(out_dir / "scoop" / "apps" / "blender" / "5.2.2"),
                           str(out_dir / "scoop" / "apps" / "blender" / "current"))
    empty = out_dir / "empty"
    empty.mkdir()
    for name, value in {
        "ProgramW6432": pf64,
        "ProgramFiles": pf64,  # same folder twice -> dedupe
        "ProgramFiles(x86)": empty,
        "LOCALAPPDATA": lad,
        "PATH": out_dir / "pathdir",
        "SCOOP": out_dir / "scoop",
        "SCOOP_GLOBAL": empty,
        "ChocolateyInstall": out_dir / "choco",
    }.items():
        monkeypatch.setenv(name, str(value))
    return exes


def make_settings(out_dir: Path, **kw: object) -> LauncherSettings:
    return LauncherSettings(runtime_root=out_dir / "rt", temp_root=out_dir / "tmp", **kw)  # type: ignore[arg-type]


def test_candidates_from_env_sources(out_dir: Path, fake_tree: dict[str, Path]) -> None:
    settings = make_settings(
        out_dir,
        override_executable=out_dir / "override" / "blender-launcher.exe",  # launcher -> sibling blender.exe
        extra_search_dirs=[out_dir / "extra", out_dir / "does-not-exist"],
    )
    detector = BlenderDetector(settings, sources=ENV_SOURCES)
    groups = {str(path).lower(): origins for path, origins in detector.candidate_groups()}
    expected = {
        "override_dir_exe": ["override"],
        "embedded": ["embedded_dir"],
        "extra_nested": ["user"],
        "pf_52": ["program_files"],
        "lad_51": ["program_files"],
        "path_exe": ["path"],
        "scoop_522": ["scoop"],
        "winget": ["winget"],
        "choco": ["choco"],
        "bl_launcher": ["blender_launcher"],
        "store": ["store"],
    }
    assert set(groups) == {str(fake_tree[k]).lower() for k in expected}
    for key, origins in expected.items():
        assert groups[str(fake_tree[key]).lower()] == origins, key
    # winget search depth is limited to 3 levels; launcher exe never appears as a candidate
    assert str(fake_tree["winget_too_deep"]).lower() not in groups
    assert not any(p.endswith("blender-launcher.exe") for p in groups)
    # candidates(): one tuple per unique executable, first origin
    flat = detector.candidates()
    assert len(flat) == len(groups) and len({str(p).lower() for p, _ in flat}) == len(flat)
    assert set(detector.last_timings_ms) == set(ENV_SOURCES)  # every source timed


def test_dedupe_merges_origins_and_drops_missing(out_dir: Path, fake_tree: dict[str, Path]) -> None:
    pf52 = fake_tree["pf_52"]
    settings = make_settings(
        out_dir,
        override_executable=pf52.parent / "blender-launcher.exe",  # same exe via the launcher name
        extra_search_dirs=[pf52.parent.parent, out_dir / "missing-dir"],  # "Blender Foundation" folder -> */blender.exe
    )
    detector = BlenderDetector(settings, sources=("override", "user", "program_files", "scoop"))
    groups = {str(p).lower(): o for p, o in detector.candidate_groups()}
    assert groups[str(pf52).lower()] == ["override", "user", "program_files"]
    # scoop: '5.2.2' and the 'current' junction resolve to the same file -> one candidate
    scoop = [p for p in groups if "scoop" in p]
    assert len(scoop) == 1
    assert not any("blender 4.4" in p or "blender 4.5" in p for p in groups)


def test_normalize_executable(out_dir: Path) -> None:
    folder = out_dir / "x"
    touch(folder / "blender.exe")
    assert normalize_executable(f'  "{folder / "blender-launcher.exe"}"  ') == folder / "blender.exe"
    assert normalize_executable(folder) == folder / "blender.exe"
    os.environ["AMT_TEST_DIR"] = str(folder)
    try:
        assert normalize_executable("%AMT_TEST_DIR%\\blender.exe") == folder / "blender.exe"
    finally:
        del os.environ["AMT_TEST_DIR"]


def test_detect_probe_classification(out_dir: Path) -> None:
    root = out_dir / "inst"
    exes = {name: touch(root / name / "blender.exe") for name in
            ("v522", "v4217", "beta", "garbage", "timeout", "gui", "arm", "notpe", "v51")}
    runner = FakeRunner({
        str(exes["v522"]): "Blender 5.2.2 LTS\n\tbuild hash: d13f752e3b9c\n",
        str(exes["v4217"]): "TBBmalloc: noise\nBlender 4.2.17 LTS\n",
        str(exes["beta"]): "Blender 5.3.0 Beta\n",
        str(exes["garbage"]): "hello world\n",
        str(exes["v51"]): "Blender 5.1.0\n",
        str(exes["arm"]): "Blender 5.2.2 LTS\n",
    })
    runner.timeout_paths.add(str(exes["timeout"]).lower())
    pe_table = {str(exes["gui"]): ("x64", "gui"), str(exes["arm"]): ("arm64", "console")}

    def pe_reader(path: Path) -> tuple[str, str]:
        if path == exes["notpe"]:
            raise ValueError("not an MZ executable")
        return fake_pe_reader(pe_table)(path)

    settings = make_settings(out_dir, extra_search_dirs=[root])
    detector = BlenderDetector(settings, runner=runner, pe_reader=pe_reader, native_arch="x64", sources=("user",),
                               use_cache=False)
    started = time.perf_counter()
    found = detector.detect()
    assert time.perf_counter() - started < 3.0
    by = {p.executable.parent.name: p for p in found}
    assert by["v522"].valid and by["v522"].compatible and by["v522"].version.label == "5.2.2 LTS"  # type: ignore[union-attr]
    assert by["v522"].version.build_hash == "d13f752e3b9c"  # type: ignore[union-attr]
    assert by["v522"].kind == "system" and by["v522"].origins == ["user"]
    assert by["v522"].id == installation_id(exes["v522"])
    assert by["v4217"].valid and not by["v4217"].compatible
    assert by["v4217"].incompatible_reason == "Blender 4.2.17 is older than 5.1"
    assert by["beta"].valid and by["beta"].compatible and not by["beta"].is_stable
    for bad in ("garbage", "timeout", "gui", "arm", "notpe"):
        assert not by[bad].valid and not by[bad].compatible and by[bad].error, bad
    assert "Unrecognised" in by["garbage"].error  # type: ignore[operator]
    assert "timed out" in by["timeout"].error  # type: ignore[operator]
    assert "GUI" in by["gui"].error  # type: ignore[operator]
    assert "arm64" in by["arm"].error  # type: ignore[operator]
    # sorted: version desc (release > beta within ordering), invalid last
    labels = [p.version.label if p.version else None for p in found]
    assert labels[:4] == ["5.3.0 Beta", "5.2.2 LTS", "5.1.0", "4.2.17 LTS"]
    assert labels[4:] == [None] * 5
    data = by["v522"].to_dict()
    json.dumps(data)
    assert data["version"] == "5.2.2" and data["version_tuple"] == [5, 2, 2] and data["is_lts"] is True
    assert data["cycle"] == "release" and data["is_stable"] is True and data["architecture"] == "x64"


def test_x64_on_arm64_is_compatible_but_emulated(out_dir: Path) -> None:
    exe = touch(out_dir / "b" / "blender.exe")
    detector = BlenderDetector(make_settings(out_dir), runner=FakeRunner(default="Blender 5.2.2 LTS\n"),
                               pe_reader=fake_pe_reader(), native_arch="arm64", sources=(), use_cache=False)
    install = detector.probe(exe)
    assert install.valid and install.compatible and install.emulated and install.architecture == "x64"


def test_embedded_kind_and_probe_launcher_mapping(out_dir: Path) -> None:
    exe = touch(out_dir / "rt" / "blender-5.2.2-windows-x64" / "blender.exe")
    touch(exe.parent / "blender-launcher.exe")
    detector = BlenderDetector(make_settings(out_dir), runner=FakeRunner(default="Blender 5.2.2 LTS\n"),
                               pe_reader=fake_pe_reader(), sources=("embedded_dir",), use_cache=False)
    install = detector.probe(exe.parent / "blender-launcher.exe")
    assert install.executable == exe and install.kind == "embedded"
    assert [i.kind for i in detector.detect()] == ["embedded"]
    missing = detector.probe(out_dir / "nope" / "blender.exe")
    assert not missing.valid and missing.error == "File not found"


def test_probe_cache_reused_unless_forced_or_changed(out_dir: Path) -> None:
    root = out_dir / "inst"
    a = touch(root / "a" / "blender.exe")
    b = touch(root / "b" / "blender.exe")
    runner = FakeRunner({str(a): "Blender 5.2.2 LTS\n", str(b): "Blender 5.1.0\n"})
    settings = make_settings(out_dir, extra_search_dirs=[root])
    detector = BlenderDetector(settings, runner=runner, pe_reader=fake_pe_reader(), sources=("user",))
    first = detector.detect()
    assert len(runner.calls) == 2 and all(i.valid for i in first)
    cache = json.loads(settings.cache_path.read_text(encoding="utf-8"))
    assert cache["schema"] == 1 and len(cache["entries"]) == 2
    key = next(iter(cache["entries"]))
    assert key.count("|") == 2  # normcase path | size | mtime_ns

    second = detector.detect()
    assert len(runner.calls) == 2  # served from cache
    assert [i.to_dict() | {"probe_ms": None} for i in second] == [i.to_dict() | {"probe_ms": None} for i in first]

    fresh = BlenderDetector(settings, runner=runner, pe_reader=fake_pe_reader(), sources=("user",))
    fresh.detect()
    assert len(runner.calls) == 2  # persisted cache file reused by a new detector

    detector.detect(force=True)
    assert len(runner.calls) == 4

    time.sleep(0.01)
    b.write_bytes(FAKE_EXE + b"changed")  # size + mtime change -> re-probe only b
    runner.outputs[str(b).lower()] = "Blender 5.1.1\n"
    third = detector.detect()
    assert len(runner.calls) == 5 and runner.calls[-1][0] == str(b)
    assert {i.version.number for i in third} == {"5.2.2", "5.1.1"}  # type: ignore[union-attr]

    b.unlink()
    detector.detect()
    cache = json.loads(settings.cache_path.read_text(encoding="utf-8"))
    assert len(cache["entries"]) == 1  # stale entry pruned


def test_broken_source_does_not_break_detection(out_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = touch(out_dir / "e" / "blender.exe")
    detector = BlenderDetector(make_settings(out_dir, extra_search_dirs=[exe.parent]),
                               runner=FakeRunner(default="Blender 5.2.2 LTS\n"), pe_reader=fake_pe_reader(),
                               sources=("user", "program_files"), use_cache=False)

    def boom() -> list[Path]:
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(detector, "_src_program_files", boom)
    assert [i.executable for i in detector.detect()] == [exe]


def test_unknown_source_name_rejected(out_dir: Path) -> None:
    with pytest.raises(ValueError):
        BlenderDetector(make_settings(out_dir), sources=("nope",))
