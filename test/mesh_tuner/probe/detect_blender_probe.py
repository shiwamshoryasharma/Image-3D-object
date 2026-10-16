"""Reference implementation + probe: discover Blender installs on Windows, validate, parse versions, pick runtime.

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_blender_probe.py
Output: test\\outputs\\mesh_tuner\\detect\\detect_blender.json
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import winreg
from dataclasses import asdict, dataclass, field
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "outputs" / "mesh_tuner" / "detect"
OUT.mkdir(parents=True, exist_ok=True)
CREATE_NO_WINDOW = 0x08000000
TIMINGS: dict[str, float] = {}

# ============================================================================ version parsing
# Source of truth (blender/blenkernel/intern/blender.cc, blender_version_init):
#   "%d.%01d.%d%s%s" % (V/100, V%100, PATCH, " LTS" if SUFFIX==LTS else "", cycle_suffix)
#   cycle_suffix: alpha->" Alpha", beta->" Beta", rc->" Release Candidate", release->""
# creator_args.cc: --version -> "Blender %s\n" + "\tbuild <key>: <val>\n" lines (only #ifdef BUILD_DATE);
#   -b banner (print_version_short) -> "Blender %s (hash %s built %s %s)\n".
# 2.7x/2.8x: "Blender 2.79 (sub 0)" ("Blender %d.%02d (sub %d)").
VERSION_RE = re.compile(
    r"^Blender[ \t]+(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?P<char>[a-z])?"
    r"(?P<tags>(?:[ \t]+(?:LTS|Alpha|Beta|Release[ \t]+Candidate|RC))*)"
    r"(?:[ \t]+\(sub[ \t]+(?P<sub>\d+)\))?"
    r"(?:[ \t]+\(hash[ \t]+(?P<hash>[0-9a-fA-F]+|unknown)[ \t]+built[ \t]+(?P<built>[^)]*)\))?"
    r"[ \t]*\r?$",
    re.MULTILINE,
)
BUILD_LINE_RE = re.compile(r"^[ \t]+build (?P<key>[a-z +]+?):[ \t]?(?P<val>.*?)[ \t]*\r?$", re.MULTILINE)
CYCLE_MAP = {"Alpha": "alpha", "Beta": "beta", "Release Candidate": "rc", "RC": "rc"}


def parse_blender_version(text: str) -> dict | None:
    """Parse `blender --version` (or the -b banner). Tolerates noise lines before it (e.g. TBBmalloc on 4.2)."""
    m = VERSION_RE.search(text)
    if not m:
        return None
    tags = re.findall(r"LTS|Alpha|Beta|Release[ \t]+Candidate|RC", m.group("tags") or "")
    cycle = "release"
    for t in tags:
        cycle = CYCLE_MAP.get(re.sub(r"[ \t]+", " ", t), cycle)
    build = {b.group("key").replace(" ", "_"): b.group("val") for b in BUILD_LINE_RE.finditer(text)}
    major, minor = int(m.group("major")), int(m.group("minor"))
    patch = int(m.group("patch")) if m.group("patch") is not None else 0
    return {
        "version": [major, minor, patch],
        "version_text": (m.group(0).split("(hash")[0]).strip()[len("Blender "):],
        "cycle": cycle,  # alpha | beta | rc | release  (== bpy.app.version_cycle)
        "lts": "LTS" in tags,
        "stable": cycle == "release",
        "legacy_sub": int(m.group("sub")) if m.group("sub") else None,
        "build_hash": build.get("hash") or m.group("hash"),
        "build_branch": build.get("branch"),
        "build_date": build.get("date") or (m.group("built") or "").split(" ")[0] or None,
        "build_commit_date": build.get("commit_date"),
        "build_type": build.get("type"),
        "build_platform": build.get("platform"),
    }


# ============================================================================ PE / file version helpers
def pe_machine_subsystem(path: str) -> tuple[str, str]:
    with open(path, "rb") as f:
        dos = f.read(64)
        if dos[:2] != b"MZ":
            raise ValueError("not MZ")
        f.seek(struct.unpack_from("<I", dos, 0x3C)[0])
        hdr = f.read(94)
    if hdr[:4] != b"PE\0\0":
        raise ValueError("no PE signature")
    machine = {0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86", 0xA641: "arm64ec"}.get(
        struct.unpack_from("<H", hdr, 4)[0], "other")
    subsystem = {2: "gui", 3: "console"}.get(struct.unpack_from("<H", hdr, 92)[0], "other")
    return machine, subsystem


def file_version(path: str) -> tuple[int, int, int, int] | None:
    """VS_FIXEDFILEINFO.dwFileVersion (cheap, no exec). Pre-filter only - Blender sets just major.minor."""
    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None
    p, n = ctypes.c_void_p(), wt.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)):
        return None
    ms, ls = struct.unpack_from("<II", ctypes.string_at(p.value + 8, 8))
    return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF


def native_machine() -> str:
    k32 = ctypes.WinDLL("kernel32")
    fn = getattr(k32, "IsWow64Process2", None)
    if fn:
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT), ctypes.POINTER(wt.USHORT)]
        k32.GetCurrentProcess.restype = wt.HANDLE
        pm, nm = wt.USHORT(), wt.USHORT()
        if fn(k32.GetCurrentProcess(), ctypes.byref(pm), ctypes.byref(nm)):
            return {0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86"}.get(nm.value, "other")
    return {"AMD64": "x64", "ARM64": "arm64"}.get(os.environ.get("PROCESSOR_ARCHITEW6432")
                                                  or os.environ.get("PROCESSOR_ARCHITECTURE", ""), "other")


# ============================================================================ registry helpers
def _reg_get(root, path, name="", view=winreg.KEY_WOW64_64KEY):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _reg_subkeys(root, path, view=winreg.KEY_WOW64_64KEY):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
            n = winreg.QueryInfoKey(k)[0]
            return [winreg.EnumKey(k, i) for i in range(n)]
    except OSError:
        return []


def _reg_value_names(root, path, view=winreg.KEY_WOW64_64KEY):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
            return [winreg.EnumValue(k, i)[0] for i in range(winreg.QueryInfoKey(k)[1])]
    except OSError:
        return []


_CMD_EXE_RE = re.compile(r'^\s*"([^"]+\.exe)"|^\s*(\S+\.exe)', re.IGNORECASE)


def _exe_from_command(cmd: str | None) -> str | None:
    if not cmd:
        return None
    m = _CMD_EXE_RE.match(os.path.expandvars(cmd))
    return (m.group(1) or m.group(2)) if m else None


# ============================================================================ candidate sources
@dataclass
class Candidate:
    path: str
    sources: list[str] = field(default_factory=list)


def timed(name):
    def deco(fn):
        def wrap(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                TIMINGS[name] = round((time.perf_counter() - t0) * 1000, 2)
        return wrap
    return deco


UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


@timed("registry_uninstall")
def src_uninstall():
    out = []
    for root, rname in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        for view, vname in ((winreg.KEY_WOW64_64KEY, "64"), (winreg.KEY_WOW64_32KEY, "32")):
            for sub in _reg_subkeys(root, UNINSTALL, view):
                kp = UNINSTALL + "\\" + sub
                dn = _reg_get(root, kp, "DisplayName", view)
                if not dn or "blender" not in dn.lower():
                    continue
                loc = _reg_get(root, kp, "InstallLocation", view)
                if loc:
                    out.append((os.path.join(loc, "blender.exe"),
                                f"uninstall:{rname}{vname}:{sub}:{_reg_get(root, kp, 'DisplayVersion', view)}"))
                # Steam: "Steam App 365670" has InstallLocation too (DisplayName 'Blender')
    return out


@timed("registry_app_paths")
def src_app_paths():
    out = []
    for root, rname in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        for exe in ("blender.exe", "blender-launcher.exe"):
            v = _reg_get(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
            if v:
                out.append((v.strip('"'), f"app_paths:{rname}"))
    return out


@timed("registry_blend_association")
def src_blend_association():
    out = []
    progids = set()
    for root, base in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"), (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes")):
        d = _reg_get(root, base + r"\.blend")
        if d:
            progids.add(d)
        progids.update(_reg_value_names(root, base + r"\.blend\OpenWithProgids"))
    uc = _reg_get(winreg.HKEY_CURRENT_USER,
                  r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.blend\UserChoice", "ProgId")
    if uc:
        progids.add(uc)
    for pid in sorted(progids):
        cmd = _reg_get(winreg.HKEY_CLASSES_ROOT, pid + r"\shell\open\command")
        exe = _exe_from_command(cmd)
        if exe:
            out.append((exe, f"progid:{pid}"))
    for app in ("blender.exe", "blender-launcher.exe"):
        exe = _exe_from_command(_reg_get(winreg.HKEY_CLASSES_ROOT, rf"Applications\{app}\shell\open\command"))
        if exe:
            out.append((exe, f"applications:{app}"))
    return out


@timed("path_env")
def src_path():
    out = []
    for name in ("blender.exe", "blender"):
        w = shutil.which(name)
        if w:
            out.append((w, "PATH(shutil.which)"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.isfile(os.path.join(d, "blender.exe")):
            out.append((os.path.join(d, "blender.exe"), "PATH"))
    return out


@timed("program_files_glob")
def src_program_files():
    out = []
    roots = {os.environ.get(v) for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")} - {None}
    roots.add(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"))
    for r in roots:
        base = Path(r) / "Blender Foundation"
        if base.is_dir():
            for d in base.iterdir():
                out.append((str(d / "blender.exe"), f"dir:{base}"))
    return out


@timed("steam")
def src_steam():
    out = []
    roots = [_reg_get(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
             _reg_get(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath", winreg.KEY_WOW64_32KEY)]
    libs = []
    for r in filter(None, roots):
        r = os.path.normpath(r)
        libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            txt = Path(vdf).read_text(encoding="utf-8", errors="replace")
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', txt)]
        except OSError:
            pass
    for lib in dict.fromkeys(libs):
        out.append((os.path.join(lib, "steamapps", "common", "Blender", "blender.exe"), f"steam:{lib}"))
    return out, {"steam_roots_from_registry": roots, "steam_root_exists": [os.path.isdir(r) for r in filter(None, roots)]}


@timed("package_managers")
def src_package_managers():
    out = []
    home, lad = os.environ.get("USERPROFILE", ""), os.environ.get("LOCALAPPDATA", "")
    scoop = os.environ.get("SCOOP") or os.path.join(home, "scoop")
    for sroot in (scoop, os.environ.get("SCOOP_GLOBAL") or r"C:\ProgramData\scoop"):
        app = Path(sroot) / "apps" / "blender"
        if app.is_dir():
            for d in app.iterdir():  # <version>\ and 'current' junction
                out.append((str(d / "blender.exe"), f"scoop:{d.name}"))
    wg = Path(lad) / "Microsoft" / "WinGet" / "Packages"
    if wg.is_dir():
        for d in wg.glob("BlenderFoundation.Blender*"):
            for exe in d.rglob("blender.exe"):
                out.append((str(exe), "winget-portable"))
    choco = Path(os.environ.get("ChocolateyInstall", r"C:\ProgramData\chocolatey"))
    for exe in (choco / "lib" / "blender").rglob("blender.exe") if (choco / "lib" / "blender").is_dir() else []:
        out.append((str(exe), "chocolatey-lib"))
    alias = Path(lad) / "Microsoft" / "WindowsApps" / "blender.exe"
    if os.path.lexists(alias):
        out.append((str(alias), "windowsapps-alias"))
    return out


@timed("blender_launcher_v2")
def src_blender_launcher_v2():
    """Third-party Blender Launcher V2: %LOCALAPPDATA%\\Blender Launcher\\Blender Launcher.ini -> library_folder;
    builds in <lib>\\{stable,daily,experimental,custom,...}\\<build>\\blender.exe (+ .blinfo JSON)."""
    out = []
    lad = os.environ.get("LOCALAPPDATA", "")
    ini = Path(lad) / "Blender Launcher" / "Blender Launcher.ini"
    libs = [Path(lad) / "Blender Launcher"]
    if ini.is_file():
        m = re.search(r"(?m)^library_folder\s*=\s*(.+)$", ini.read_text(encoding="utf-8", errors="replace"))
        if m:
            libs.insert(0, Path(m.group(1).strip().strip('"')))
    for lib in libs:
        for sub in ("stable", "daily", "experimental", "custom"):
            d = lib / sub
            if d.is_dir():
                for b in d.iterdir():
                    out.append((str(b / "blender.exe"), f"blender-launcher-v2:{sub}"))
    return out


# ============================================================================ validation
def normalize(path: str) -> str:
    p = os.path.normpath(os.path.expandvars(path.strip().strip('"')))
    if os.path.basename(p).lower() == "blender-launcher.exe":  # GUI-subsystem stub: no piped stdout
        p = os.path.join(os.path.dirname(p), "blender.exe")
    return p


def probe_exe(path: str, timeout: float = 20.0) -> dict:
    t0 = time.perf_counter()
    r: dict = {"path": path}
    try:
        r["machine"], r["subsystem"] = pe_machine_subsystem(path)
    except (OSError, ValueError) as e:
        r["error"] = f"pe:{e}"
        return r
    r["file_version"] = file_version(path)
    try:
        p = subprocess.run([path, "--factory-startup", "--version"], capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
                           cwd=os.path.dirname(path))
        txt = p.stdout.decode("utf-8", "replace")
        r["exit"] = p.returncode
        r["parsed"] = parse_blender_version(txt)
        if not r["parsed"]:
            r["error"] = "unparseable --version output: " + (txt + p.stderr.decode("utf-8", "replace"))[:200]
    except subprocess.TimeoutExpired:
        r["error"] = "timeout"
    except OSError as e:
        r["error"] = f"exec:{e}"
    r["probe_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return r


def detect(min_version=(5, 1, 0)):
    cands: dict[str, Candidate] = {}
    extra = {}
    t_all = time.perf_counter()
    raw = []
    raw += src_path()
    raw += src_program_files()
    raw += src_uninstall()
    raw += src_app_paths()
    raw += src_blend_association()
    s, extra["steam"] = src_steam()
    raw += s
    raw += src_package_managers()
    raw += src_blender_launcher_v2()
    TIMINGS["all_sources"] = round((time.perf_counter() - t_all) * 1000, 2)
    rejected = []
    for path, src in raw:
        n = normalize(path)
        key = os.path.normcase(os.path.realpath(n)) if os.path.exists(n) else os.path.normcase(n)
        if not os.path.isfile(n):
            rejected.append({"path": n, "source": src, "why": "missing"})
            continue
        cands.setdefault(key, Candidate(n)).sources.append(src)
    nat = native_machine()
    results = []
    t_probe = time.perf_counter()
    for c in cands.values():
        info = probe_exe(c.path)
        info["sources"] = c.sources
        pv = info.get("parsed")
        info["compatible"] = bool(pv and info.get("machine") == nat and info.get("subsystem") == "console"
                                  and tuple(pv["version"]) >= tuple(min_version))
        results.append(info)
    TIMINGS["exec_probes"] = round((time.perf_counter() - t_probe) * 1000, 2)
    ok = [r for r in results if r["compatible"]]
    rank = {"release": 3, "rc": 2, "beta": 1, "alpha": 0}
    ok.sort(key=lambda r: (r["parsed"]["stable"], tuple(r["parsed"]["version"]), rank[r["parsed"]["cycle"]]),
            reverse=True)
    selected = ok[0]["path"] if ok and ok[0]["parsed"]["stable"] else None
    return {"native_machine": nat, "selected": selected, "installs": results, "rejected_candidates": rejected,
            "extra": extra, "timings_ms": TIMINGS}


SAMPLES = [
    "Blender 5.2.2 LTS",
    "Blender 4.2.17 LTS",
    "Blender 5.1.0",
    "Blender 5.3.0 Alpha",
    "Blender 5.2.0 Beta",
    "Blender 5.2.0 Release Candidate",
    "Blender 4.2.0 LTS Beta",  # theoretical: suffix precedes cycle in the format string
    "Blender 5.3.0 Alpha (hash 1a2b3c4d5e6f built 2026-10-01 01:02:03)",
    "Blender 5.2.2 LTS (hash d13f752e3b9c built 2026-09-15 01:37:04)",
    "Blender 2.79 (sub 0)",
    "Blender 2.80 (sub 75)",
    "Blender 2.93.18",
    "TBBmalloc: skip allocation functions replacement in ucrtbase.dll: unknown prologue for function _msize\r\n"
    "Blender 4.2.17 LTS\r\n\tbuild date: 2026-01-20\r\n\tbuild hash: 76b996a81c95\r\n\tbuild branch: blender-v4.2-release\r\n",
    "Blender 5.3.0 Alpha\n\tbuild hash: unknown\n\tbuild branch: main\n",
    "Blender 5.10.0",
    "Bforartists 4.2.0",
    "Error: Not freed memory blocks: 1",
]

if __name__ == "__main__":
    parsed = {s.splitlines()[-1] if len(s) > 120 else s: parse_blender_version(s) for s in SAMPLES}
    res = detect()
    res["sample_parse"] = parsed
    (OUT / "detect_blender.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
