"""Windows platform helpers (ctypes / winreg): native architecture, PE header, long paths, registry reads, Job Object.

Every helper degrades gracefully on non-Windows hosts (returns ``None`` / ``[]`` / a sensible default, or the Job
Object becomes a no-op) so the launcher package stays importable everywhere.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import os
import platform
import stat
import struct
import sys
from typing import Any, Final

logger = logging.getLogger(__name__)

IS_WINDOWS: Final = sys.platform == "win32"

CREATE_SUSPENDED: Final = 0x00000004
CREATE_NO_WINDOW: Final = 0x08000000
STATUS_CONTROL_C_EXIT: Final = 0xC000013A
MAX_PATH_CHARS: Final = 259  # MAX_PATH (260) minus the terminating NUL
IO_REPARSE_TAG_APPEXECLINK: Final = 0x8000001B

_PE_MACHINES: Final = {0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86", 0xA641: "arm64ec", 0x1C4: "arm"}
_PE_SUBSYSTEMS: Final = {2: "gui", 3: "console"}

if IS_WINDOWS:
    import winreg

    HKEY_LOCAL_MACHINE: Any = winreg.HKEY_LOCAL_MACHINE
    HKEY_CURRENT_USER: Any = winreg.HKEY_CURRENT_USER
    HKEY_CLASSES_ROOT: Any = winreg.HKEY_CLASSES_ROOT
    VIEW_64: int = winreg.KEY_WOW64_64KEY
    VIEW_32: int = winreg.KEY_WOW64_32KEY
else:  # pragma: no cover - non-Windows
    winreg = None  # type: ignore[assignment]
    HKEY_LOCAL_MACHINE = HKEY_CURRENT_USER = HKEY_CLASSES_ROOT = None
    VIEW_64 = VIEW_32 = 0


# ---------------------------------------------------------------------------------------------- architecture / PE
@functools.cache
def native_architecture() -> str:
    """Native machine architecture: ``"x64"``, ``"arm64"``, ``"x86"`` or ``"other"``.

    Uses ``IsWow64Process2`` (true native machine even for an emulated x64 process on ARM64), falling back to the
    ``PROCESSOR_ARCHITEW6432`` / ``PROCESSOR_ARCHITECTURE`` variables, and to ``platform.machine()`` elsewhere.
    """
    if IS_WINDOWS:
        try:
            import ctypes.wintypes as wt

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            fn = getattr(k32, "IsWow64Process2", None)
            if fn is not None:
                fn.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT), ctypes.POINTER(wt.USHORT)]
                fn.restype = wt.BOOL
                k32.GetCurrentProcess.restype = wt.HANDLE
                process_machine, native_machine = wt.USHORT(), wt.USHORT()
                if fn(k32.GetCurrentProcess(), ctypes.byref(process_machine), ctypes.byref(native_machine)):
                    return _PE_MACHINES.get(native_machine.value, "other")
        except (OSError, AttributeError) as exc:  # pragma: no cover - very old Windows
            logger.debug("IsWow64Process2 unavailable: %s", exc)
        env = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        return {"AMD64": "x64", "ARM64": "arm64", "X86": "x86"}.get(env.upper(), "other")
    machine = platform.machine().lower()  # pragma: no cover - non-Windows
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64", "i386": "x86", "i686": "x86"}.get(
        machine, "other"
    )


def pe_info(path: str | os.PathLike[str]) -> tuple[str, str]:
    """``(machine, subsystem)`` from a PE header, e.g. ``("x64", "console")``.

    ``machine`` is ``x64|arm64|x86|arm64ec|arm|other``; ``subsystem`` is ``console|gui|other``.
    Raises ``OSError`` if unreadable and ``ValueError`` if the file is not a PE image.
    """
    with open(path, "rb") as fh:
        dos = fh.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise ValueError("not an MZ executable")
        pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
        if pe_offset <= 0 or pe_offset > 64 * 1024 * 1024:
            raise ValueError("invalid PE header offset")
        fh.seek(pe_offset)
        header = fh.read(24 + 70)  # signature + COFF header (20) + optional header up to Subsystem (+68, 2 bytes)
    if len(header) < 94 or header[:4] != b"PE\0\0":
        raise ValueError("no PE signature")
    machine = _PE_MACHINES.get(struct.unpack_from("<H", header, 4)[0], "other")
    subsystem = _PE_SUBSYSTEMS.get(struct.unpack_from("<H", header, 92)[0], "other")
    return machine, subsystem


def can_run_architecture(exe_arch: str | None, native: str | None = None) -> tuple[bool, bool]:
    """``(runnable, emulated)`` for an executable architecture on this machine.

    x64 binaries run emulated on ARM64 Windows 11 (allowed as a fallback); everything else must match exactly.
    """
    native = native or native_architecture()
    if not exe_arch:
        return False, False
    if exe_arch == native:
        return True, False
    if native == "arm64" and exe_arch in ("x64", "arm64ec"):
        return True, True
    return False, False


def is_app_execution_alias(path: str | os.PathLike[str]) -> bool:
    """``True`` for a Microsoft Store execution alias (0-byte ``APPEXECLINK`` reparse point; cannot be opened)."""
    if not IS_WINDOWS:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT) and getattr(st, "st_reparse_tag", 0) == (
        IO_REPARSE_TAG_APPEXECLINK
    )


@functools.cache
def long_paths_enabled() -> bool:
    """``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled == 1`` (always True off Windows)."""
    if not IS_WINDOWS:
        return True
    value = reg_get_value(HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled")
    return value == 1


def extended_path(path: str | os.PathLike[str]) -> str:
    """Absolute path with the ``\\\\?\\`` prefix on Windows (lets *this* process exceed MAX_PATH).

    Only for files the launcher itself writes - never pass such a path to Blender (its glTF exporter breaks on it).
    """
    text = os.path.abspath(os.fspath(path))
    if not IS_WINDOWS or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def path_too_long(path: str | os.PathLike[str]) -> bool:
    """``True`` if the absolute form of ``path`` exceeds :data:`MAX_PATH_CHARS`."""
    return len(os.path.abspath(os.fspath(path))) > MAX_PATH_CHARS


# ---------------------------------------------------------------------------------------------- registry
def reg_get_value(root: Any, key_path: str, name: str = "", view: int = VIEW_64) -> Any | None:
    """Read one registry value (``None`` if missing, inaccessible, or not on Windows). Never recursive."""
    if not IS_WINDOWS or root is None:
        return None
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def reg_subkeys(root: Any, key_path: str, view: int = VIEW_64) -> list[str]:
    """Names of the direct subkeys of one key (``[]`` on any failure)."""
    if not IS_WINDOWS or root is None:
        return []
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
            count = winreg.QueryInfoKey(key)[0]
            return [winreg.EnumKey(key, index) for index in range(count)]
    except OSError:
        return []


def reg_value_names(root: Any, key_path: str, view: int = VIEW_64) -> list[str]:
    """Names of the values stored in one key (``[]`` on any failure)."""
    if not IS_WINDOWS or root is None:
        return []
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
            count = winreg.QueryInfoKey(key)[1]
            return [winreg.EnumValue(key, index)[0] for index in range(count)]
    except OSError:
        return []


# ---------------------------------------------------------------------------------------------- Job Object
class _Win32:
    """Lazily created ctypes bindings for the Job Object API (own WinDLL instances; nothing global is mutated)."""

    _instance: _Win32 | None = None

    def __init__(self) -> None:
        import ctypes.wintypes as wt

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wt.DWORD),
                ("TotalProcesses", wt.DWORD),
                ("ActiveProcesses", wt.DWORD),
                ("TotalTerminatedProcesses", wt.DWORD),
            ]

        if ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) != 144:
            raise OSError("unexpected JOBOBJECT_EXTENDED_LIMIT_INFORMATION layout")
        self.extended_limit_info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        self.accounting_info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        k32.CreateJobObjectW.restype = wt.HANDLE
        k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.SetInformationJobObject.restype = wt.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wt.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wt.BOOL
        k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        k32.AssignProcessToJobObject.restype = wt.BOOL
        k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
        k32.TerminateJobObject.restype = wt.BOOL
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.OpenProcess.restype = wt.HANDLE
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        self.k32 = k32
        self.ntdll = ntdll

    @classmethod
    def get(cls) -> _Win32:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


PROCESS_TERMINATE: Final = 0x0001
PROCESS_SET_QUOTA: Final = 0x0100
PROCESS_SUSPEND_RESUME: Final = 0x0800
_JOB_EXTENDED_LIMIT_INFORMATION_CLASS: Final = 9
_JOB_BASIC_ACCOUNTING_INFORMATION_CLASS: Final = 1
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: Final = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000


def _raise_last_error(what: str) -> None:
    raise ctypes.WinError(ctypes.get_last_error(), what)  # type: ignore[attr-defined]


class KillOnCloseJob:
    """Windows Job Object that kills every process in it when terminated or when its last handle closes.

    Also covers a backend crash: the OS closes the handle and the whole Blender tree (incl. conhost and any
    grandchild) dies. On non-Windows hosts the object is inert (``available`` is ``False``).
    """

    def __init__(self) -> None:
        self.handle: int | None = None
        if not IS_WINDOWS:
            return
        api = _Win32.get()
        handle = api.k32.CreateJobObjectW(None, None)
        if not handle:
            _raise_last_error("CreateJobObjectW")
        info = api.extended_limit_info()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        if not api.k32.SetInformationJobObject(
            handle, _JOB_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            api.k32.CloseHandle(handle)
            raise ctypes.WinError(error, "SetInformationJobObject")  # type: ignore[attr-defined]
        self.handle = handle

    @property
    def available(self) -> bool:
        return self.handle is not None

    def assign(self, pid: int) -> None:
        """Put process ``pid`` into the job (call while it is still suspended)."""
        if self.handle is None:
            return
        api = _Win32.get()
        process = api.k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME, False, pid)
        if not process:
            _raise_last_error("OpenProcess")
        try:
            if not api.k32.AssignProcessToJobObject(self.handle, process):
                _raise_last_error("AssignProcessToJobObject")
        finally:
            api.k32.CloseHandle(process)

    def active_processes(self) -> int:
        """Number of live processes in the job (0 when unavailable)."""
        if self.handle is None:
            return 0
        api = _Win32.get()
        info = api.accounting_info()
        if not api.k32.QueryInformationJobObject(
            self.handle, _JOB_BASIC_ACCOUNTING_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            _raise_last_error("QueryInformationJobObject")
        return int(info.ActiveProcesses)

    def terminate(self, exit_code: int = STATUS_CONTROL_C_EXIT) -> bool:
        """Kill every process in the job with ``exit_code``. Returns ``True`` on success."""
        if self.handle is None:
            return False
        return bool(_Win32.get().k32.TerminateJobObject(self.handle, exit_code & 0xFFFFFFFF))

    def close(self) -> None:
        """Close the job handle (KILL_ON_JOB_CLOSE kills any survivor). Idempotent."""
        handle, self.handle = self.handle, None
        if handle:
            _Win32.get().k32.CloseHandle(handle)

    def __enter__(self) -> KillOnCloseJob:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - safety net
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def resume_process(pid: int) -> None:
    """Resume every thread of a process started with ``CREATE_SUSPENDED`` (``NtResumeProcess``)."""
    if not IS_WINDOWS:
        return
    api = _Win32.get()
    process = api.k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not process:
        _raise_last_error("OpenProcess(resume)")
    try:
        status = api.ntdll.NtResumeProcess(process)
        if status != 0:
            raise OSError(f"NtResumeProcess failed (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")
    finally:
        api.k32.CloseHandle(process)


def path_is_within(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """``True`` if ``path`` resolves to ``root`` or to something inside it (symlinks/junctions resolved)."""
    try:
        resolved = os.path.normcase(os.path.realpath(os.fspath(path)))
        base = os.path.normcase(os.path.realpath(os.fspath(root)))
    except (OSError, ValueError):
        return False
    try:
        return os.path.commonpath([resolved, base]) == base
    except ValueError:  # different drives
        return False


def is_same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    try:
        return os.path.normcase(os.path.realpath(os.fspath(a))) == os.path.normcase(os.path.realpath(os.fspath(b)))
    except (OSError, ValueError):
        return False
