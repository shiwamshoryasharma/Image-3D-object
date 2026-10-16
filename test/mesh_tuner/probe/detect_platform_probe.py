"""Platform facts for Blender runtime detection: PE header (machine/subsystem), native arch, long paths.

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_platform_probe.py
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import platform
import struct
import sys
import time
import winreg
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "outputs" / "mesh_tuner" / "detect"
OUT.mkdir(parents=True, exist_ok=True)

IMAGE_FILE_MACHINE = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0xA641: "arm64ec", 0x01C4: "arm"}
IMAGE_SUBSYSTEM = {2: "windows_gui", 3: "windows_cui"}


def pe_info(path):
    """Return {'machine','subsystem','pe32plus'} from the PE headers, or raise ValueError.

    Layout: 'MZ' at 0; e_lfanew (u32) at 0x3C -> 'PE\\0\\0'; COFF header: Machine u16 @+4;
    optional header starts @+24: Magic u16 (0x10B PE32 / 0x20B PE32+), Subsystem u16 @ opt+68.
    """
    with open(path, "rb") as f:
        dos = f.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise ValueError("not an MZ executable")
        (e_lfanew,) = struct.unpack_from("<I", dos, 0x3C)
        f.seek(e_lfanew)
        hdr = f.read(24 + 70)
        if len(hdr) < 94 or hdr[:4] != b"PE\0\0":
            raise ValueError("missing PE signature")
        (machine,) = struct.unpack_from("<H", hdr, 4)
        (magic,) = struct.unpack_from("<H", hdr, 24)
        (subsystem,) = struct.unpack_from("<H", hdr, 24 + 68)
    return {"machine": IMAGE_FILE_MACHINE.get(machine, hex(machine)), "machine_raw": hex(machine),
            "pe32plus": magic == 0x20B, "subsystem": IMAGE_SUBSYSTEM.get(subsystem, subsystem)}


def native_arch():
    """Native OS architecture, correct even from an x64-emulated process on ARM64 Windows."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    res = {"platform.machine()": platform.machine(),
           "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE"),
           "PROCESSOR_ARCHITEW6432": os.environ.get("PROCESSOR_ARCHITEW6432"),
           "sys.maxsize>2**32": sys.maxsize > 2 ** 32}
    fn = getattr(k32, "IsWow64Process2", None)  # Win10 1709+
    if fn:
        fn.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT), ctypes.POINTER(wt.USHORT)]
        fn.restype = wt.BOOL
        k32.GetCurrentProcess.restype = wt.HANDLE
        pm, nm = wt.USHORT(), wt.USHORT()
        if fn(k32.GetCurrentProcess(), ctypes.byref(pm), ctypes.byref(nm)):
            res["IsWow64Process2.process"] = "native(not-wow64)" if pm.value == 0 else IMAGE_FILE_MACHINE.get(pm.value, hex(pm.value))
            res["IsWow64Process2.native"] = IMAGE_FILE_MACHINE.get(nm.value, hex(nm.value))
    # An x64 process emulated on ARM64 is not WOW64: process=UNKNOWN(0) but native=arm64, while
    # platform.machine()/PROCESSOR_ARCHITECTURE report AMD64 there. IsWow64Process2 is the reliable source.
    return res


def long_paths():
    t0 = time.perf_counter()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            val, typ = winreg.QueryValueEx(k, "LongPathsEnabled")
    except FileNotFoundError:
        val, typ = None, None
    reg_ms = (time.perf_counter() - t0) * 1000
    ntdll = ctypes.WinDLL("ntdll")
    rtl = getattr(ntdll, "RtlAreLongPathsEnabled", None)
    proc_enabled = None
    if rtl:
        rtl.restype = ctypes.c_ubyte
        proc_enabled = bool(rtl())
    return {"HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled": val, "type": typ,
            "read_ms": round(reg_ms, 3), "RtlAreLongPathsEnabled(this python.exe)": proc_enabled}


def manifest_longpath_aware(path):
    """Look for <longPathAware>true</longPathAware> in the embedded RT_MANIFEST (cheap byte scan)."""
    data = Path(path).read_bytes()
    i = data.find(b"longPathAware")
    return None if i < 0 else data[max(0, i - 40): i + 60].decode("latin-1", "replace")


res = {"pe": {}, "manifest_longPathAware": {}}
for exe in [r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 5.2\blender-launcher.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender-launcher.exe",
            sys.executable]:
    if os.path.exists(exe):
        t0 = time.perf_counter()
        info = pe_info(exe)
        info["ms"] = round((time.perf_counter() - t0) * 1000, 3)
        res["pe"][exe] = info
        res["manifest_longPathAware"][exe] = manifest_longpath_aware(exe)
res["native_arch"] = native_arch()
res["long_paths"] = long_paths()
(OUT / "platform_probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
