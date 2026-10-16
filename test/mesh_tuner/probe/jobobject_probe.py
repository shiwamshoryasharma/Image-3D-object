"""Proof: run blender.exe inside a Windows Job Object (KILL_ON_JOB_CLOSE) and kill the whole tree.

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\jobobject_probe.py
Output: test\\outputs\\mesh_tuner\\detect\\jobobject_probe.json

Scenarios
  A  control: Popen + p.kill() (TerminateProcess)  -> grandchild survives (orphan)  [then cleaned up]
  B  job + TerminateJobObject                        -> blender + grandchild gone
  C  job + CloseHandle(job) (KILL_ON_JOB_CLOSE)      -> blender + grandchild gone
  D  owner process hard-killed while holding the job -> OS closes handle -> tree gone (crash safety)
  E  child-process census of plain blender --background (does blender spawn children by itself?)
"""
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BS = HERE / "blender_side"
OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect"
OUT.mkdir(parents=True, exist_ok=True)
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

# ----------------------------------------------------------------------------- Win32 bindings
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SUSPEND_RESUME = 0x0800
SYNCHRONIZE = 0x00100000
JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102
TH32CS_SNAPPROCESS = 0x2


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD),
                ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


assert ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144, ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)

k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
k32.CreateJobObjectW.restype = wt.HANDLE
k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
k32.SetInformationJobObject.restype = wt.BOOL
k32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.QueryInformationJobObject.restype = wt.BOOL
k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
k32.AssignProcessToJobObject.restype = wt.BOOL
k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
k32.TerminateJobObject.restype = wt.BOOL
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.WaitForSingleObject.restype = wt.DWORD
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
k32.TerminateProcess.restype = wt.BOOL
k32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
k32.IsProcessInJob.restype = wt.BOOL
k32.GetCurrentProcess.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32FirstW.restype = wt.BOOL
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = wt.BOOL
ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
ntdll.NtResumeProcess.restype = ctypes.c_long


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)
    return ok


# ----------------------------------------------------------------------------- reusable helpers
class KillOnCloseJob:
    """A Job Object that kills every process in it when the last handle closes (incl. owner crash)."""

    def __init__(self):
        self.handle = k32.CreateJobObjectW(None, None)
        _check(self.handle, "CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                 | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        _check(k32.SetInformationJobObject(self.handle, JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)), "SetInformationJobObject")

    def assign(self, pid):
        h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME, False, pid)
        _check(h, "OpenProcess")
        try:
            _check(k32.AssignProcessToJobObject(self.handle, h), "AssignProcessToJobObject")
        finally:
            k32.CloseHandle(h)

    def active_processes(self):
        acc = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        _check(k32.QueryInformationJobObject(self.handle, JobObjectBasicAccountingInformation,
                                             ctypes.byref(acc), ctypes.sizeof(acc), None), "QueryInformationJobObject")
        return acc.ActiveProcesses, acc.TotalProcesses

    def terminate(self, exit_code=1):
        return bool(k32.TerminateJobObject(self.handle, exit_code))

    def close(self):
        if self.handle:
            k32.CloseHandle(self.handle)
            self.handle = None


def popen_in_job(args, job, **kw):
    """Start suspended, put into the job, then resume -> no window where grandchildren escape the job."""
    flags = kw.pop("creationflags", 0) | CREATE_SUSPENDED | CREATE_NO_WINDOW
    p = subprocess.Popen(args, creationflags=flags, **kw)
    try:
        job.assign(p.pid)
    except Exception:
        p.kill()
        raise
    h = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, p.pid)
    _check(h, "OpenProcess(resume)")
    try:
        st = ntdll.NtResumeProcess(h)
        if st != 0:
            raise OSError(f"NtResumeProcess NTSTATUS=0x{st & 0xffffffff:08x}")
    finally:
        k32.CloseHandle(h)
    return p


def process_table():
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    _check(snap != wt.HANDLE(-1).value, "CreateToolhelp32Snapshot")
    out = {}
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def descendants(pid, table=None):
    table = table or process_table()
    kids, frontier = [], [pid]
    while frontier:
        cur = frontier.pop()
        for p, (pp, name) in table.items():
            if pp == cur and p != cur:
                kids.append((p, name))
                frontier.append(p)
    return kids


def alive(pid):
    h = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        k32.CloseHandle(h)


def wait_dead(pids, timeout=10.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if not any(alive(p) for p in pids):
            return round(time.perf_counter() - t0, 4)
        time.sleep(0.002)
    return None


def start_reader(p, sink):
    def run():
        for raw in iter(p.stdout.readline, b""):
            sink.append((time.perf_counter(), raw.decode("utf-8", "replace").rstrip()))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def wait_for_line(sink, prefix, timeout=30.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        for _, ln in sink:
            if ln.startswith(prefix):
                return ln
        time.sleep(0.02)
    return None


SPAWN_ARGS = [BLENDER, "--background", "--factory-startup", "--disable-autoexec",
              "--python-exit-code", "1", "--python", str(BS / "spawn_child.py")]


# ----------------------------------------------------------------------------- helper mode (scenario D)
if len(sys.argv) > 1 and sys.argv[1] == "--helper":
    job = KillOnCloseJob()
    p = popen_in_job(SPAWN_ARGS, job, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    sink = []
    start_reader(p, sink)
    ln = wait_for_line(sink, "SPAWNED")
    print(json.dumps({"blender_pid": p.pid, "line": ln}), flush=True)
    time.sleep(600)  # parent will TerminateProcess us
    sys.exit(0)


def main():
    res = {}
    in_job = wt.BOOL()
    k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(in_job))
    res["probe_process_already_in_a_job"] = bool(in_job.value)

    # E: does blender spawn children on its own?
    p = subprocess.Popen([BLENDER, "--background", "--factory-startup", "--disable-autoexec", "--python",
                          str(BS / "sleep_long.py"), "--", "6"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    sink = []
    start_reader(p, sink)
    wait_for_line(sink, "SLEEP_START")
    time.sleep(2.0)
    res["E_children_of_plain_background_blender"] = descendants(p.pid)
    p.wait(30)

    # A: control - TerminateProcess only
    p = subprocess.Popen(SPAWN_ARGS, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW)
    sink = []
    start_reader(p, sink)
    ln = wait_for_line(sink, "SPAWNED")
    tree = descendants(p.pid)
    t0 = time.perf_counter()
    p.kill()
    p.wait(10)
    dt = time.perf_counter() - t0
    time.sleep(0.5)
    orphans = [(pid, n) for pid, n in tree if alive(pid)]
    res["A_control_TerminateProcess"] = {"spawn_line": ln, "tree_before": tree, "kill_to_exit_s": round(dt, 4),
                                         "orphans_after_kill": orphans}
    for pid, _ in orphans:  # cleanup
        h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            k32.TerminateProcess(h, 1)
            k32.CloseHandle(h)

    # B: job + TerminateJobObject
    job = KillOnCloseJob()
    t_start = time.perf_counter()
    p = popen_in_job(SPAWN_ARGS, job, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    sink = []
    start_reader(p, sink)
    ln = wait_for_line(sink, "SPAWNED")
    startup_s = round(time.perf_counter() - t_start, 3)
    tree = descendants(p.pid)
    act_before = job.active_processes()
    t0 = time.perf_counter()
    ok = job.terminate(0xC000013A & 0xFFFFFFFF)  # STATUS_CONTROL_C_EXIT, distinguishable "cancelled"
    rc = p.wait(10)
    t_exit = round(time.perf_counter() - t0, 4)
    all_dead = wait_dead([p.pid] + [pid for pid, _ in tree])
    res["B_TerminateJobObject"] = {"spawn_line": ln, "startup_to_spawn_s": startup_s, "tree_before": tree,
                                   "job_active_before(active,total)": act_before, "terminate_ok": ok,
                                   "blender_exit_code": rc, "blender_exit_code_hex": hex(rc & 0xFFFFFFFF),
                                   "terminate_to_blender_exit_s": t_exit,
                                   "terminate_to_whole_tree_dead_s": round(t_exit + (all_dead or 0), 4),
                                   "job_active_after": job.active_processes(),
                                   "survivors": [(pid, n) for pid, n in tree if alive(pid)]}
    job.close()

    # C: job + CloseHandle(job)
    job = KillOnCloseJob()
    p = popen_in_job(SPAWN_ARGS, job, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    sink = []
    start_reader(p, sink)
    ln = wait_for_line(sink, "SPAWNED")
    tree = descendants(p.pid)
    t0 = time.perf_counter()
    job.close()
    rc = p.wait(10)
    t_exit = round(time.perf_counter() - t0, 4)
    all_dead = wait_dead([pid for pid, _ in tree])
    res["C_CloseHandle_KILL_ON_JOB_CLOSE"] = {"spawn_line": ln, "tree_before": tree, "blender_exit_code": rc,
                                              "close_to_blender_exit_s": t_exit,
                                              "grandchildren_dead_after_extra_s": all_dead,
                                              "survivors": [(pid, n) for pid, n in tree if alive(pid)]}

    # D: owner crash
    helper = subprocess.Popen([sys.executable, "-s", __file__, "--helper"], stdout=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    first = helper.stdout.readline().decode()
    info = json.loads(first)
    bpid = info["blender_pid"]
    tree = [(bpid, "blender.exe")] + descendants(bpid)
    t0 = time.perf_counter()
    helper.kill()  # TerminateProcess on the job owner: no Python cleanup runs
    helper.wait(10)
    dead_s = wait_dead([pid for pid, _ in tree])
    res["D_owner_hard_killed"] = {"helper_line": info, "tree_before": tree,
                                  "owner_kill_to_tree_dead_s": round(time.perf_counter() - t0, 4) if dead_s is not None else None,
                                  "survivors": [(pid, n) for pid, n in tree if alive(pid)]}

    (OUT / "jobobject_probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
