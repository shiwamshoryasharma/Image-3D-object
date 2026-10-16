"""Probe Blender CLI behaviour (exit codes, argv, stdout streaming/encoding) from the embedded Python.

Run:  backend\\python\\python.exe -s test\\mesh_tuner\\probe\\detect_cli_probe.py
Output: test\\outputs\\mesh_tuner\\detect\\cli_probe.json (+ printed summary)
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BS = HERE / "blender_side"
OUT = HERE.parents[1] / "outputs" / "mesh_tuner" / "detect"
OUT.mkdir(parents=True, exist_ok=True)

EXES = {
    "5.2": r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    "4.2": r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
}
CREATE_NO_WINDOW = 0x08000000
BASE = ["--background", "--factory-startup", "--disable-autoexec"]


def run(exe, args, timeout=120):
    t0 = time.perf_counter()
    p = subprocess.run([exe, *args], capture_output=True, timeout=timeout,
                       stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    dt = time.perf_counter() - t0
    return {
        "args": args, "exit": p.returncode, "secs": round(dt, 3),
        "stdout": p.stdout.decode("utf-8", "replace"),
        "stderr": p.stderr.decode("utf-8", "replace"),
    }


def tail(s, n=6):
    return [ln for ln in s.splitlines() if ln.strip()][-n:]


results = {}
for ver, exe in EXES.items():
    r = {}
    # --version: which stream carries what
    v = run(exe, ["--factory-startup", "--version"])
    r["version"] = {"exit": v["exit"], "secs": v["secs"],
                    "stdout_head": v["stdout"].splitlines()[:3], "stderr": v["stderr"].splitlines()}
    cases = {
        "expr_SystemExit3_pec1": BASE + ["--python-exit-code", "1", "--python-expr", "raise SystemExit(3)"],
        "expr_SystemExit3_nopec": BASE + ["--python-expr", "raise SystemExit(3)"],
        "expr_sysexit0_pec1": BASE + ["--python-exit-code", "1", "--python-expr", "import sys; sys.exit(0)"],
        "file_exception_pec1": BASE + ["--python-exit-code", "1", "--python", str(BS / "raise_exc.py")],
        "file_exception_pec7": BASE + ["--python-exit-code", "7", "--python", str(BS / "raise_exc.py")],
        "file_exception_nopec": BASE + ["--python", str(BS / "raise_exc.py")],
        "file_sysexit5_pec1": BASE + ["--python-exit-code", "1", "--python", str(BS / "sys_exit.py"), "--", "5"],
        "file_sysexit0_pec1": BASE + ["--python-exit-code", "1", "--python", str(BS / "sys_exit.py"), "--", "0"],
        "file_missing_pec1": BASE + ["--python-exit-code", "1", "--python", str(BS / "does_not_exist.py")],
        "expr_print_after_raise_continue": BASE + ["--python-expr", "raise ValueError('x')", "--python-expr", "print('SECOND_EXPR_RAN')"],
        "expr_print_after_raise_pec1": BASE + ["--python-exit-code", "1", "--python-expr", "raise ValueError('x')", "--python-expr", "print('SECOND_EXPR_RAN')"],
        "unknown_arg_before_dd": BASE + ["--this-is-not-an-arg", "--python-expr", "print('RAN')"],
    }
    if ver == "5.2":
        cases["quiet_flag"] = BASE + ["--quiet", "--python-expr", "print('RAN')"]
    for name, args in cases.items():
        res = run(exe, args)
        r[name] = {"exit": res["exit"], "secs": res["secs"], "stdout_tail": tail(res["stdout"]),
                   "stderr_tail": tail(res["stderr"])}
    # argv after --
    weird = ["--", "a b", "C:\\dir with space\\in.glb", "--flag=1", "-x", "é✓", "", "--"]
    res = run(exe, BASE + ["--python", str(BS / "argv_dump.py"), *weird])
    line = next((ln for ln in res["stdout"].splitlines() if ln.startswith("ARGV_JSON ")), None)
    r["argv"] = {"exit": res["exit"], "passed": weird[1:], "dump": json.loads(line[9:]) if line else res["stdout"][-2000:]}
    results[ver] = r

# streaming test (5.2 & 4.2): read pipe line by line, timestamp arrival
def stream(exe, flush):
    args = [exe, *BASE, "--python", str(BS / "stream_lines.py"), "--"] + (["flush"] if flush else [])
    t0 = time.perf_counter()
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         bufsize=0, creationflags=CREATE_NO_WINDOW)
    arrivals = []
    for raw in iter(p.stdout.readline, b""):
        arrivals.append((round(time.perf_counter() - t0, 3), raw.decode("utf-8", "replace").rstrip("\r\n"), raw[-2:].hex()))
    err = p.stderr.read()
    p.wait()
    return {"flush": flush, "exit": p.returncode, "arrivals": arrivals,
            "stderr_raw_hex_of_probe_line": next((ln.hex() for ln in err.splitlines() if ln.startswith(b"STDERR")), None),
            "stderr_lines": err.decode("utf-8", "replace").splitlines()[-3:]}

results["stream"] = {}
for ver, exe in EXES.items():
    results["stream"][ver] = [stream(exe, False), stream(exe, True)]
# encoding: raw bytes of the unicode line
for ver, exe in EXES.items():
    p = subprocess.run([exe, *BASE, "--python", str(BS / "stream_lines.py"), "--", "flush"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    uline = next((ln for ln in p.stdout.splitlines() if ln.startswith(b"UNICODE")), b"")
    results.setdefault("encoding", {})[ver] = {"raw_hex": uline.hex(), "utf8_decodes": uline.decode("utf-8", "replace")}

(OUT / "cli_probe.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps(results, indent=1, ensure_ascii=True)[:60000])
