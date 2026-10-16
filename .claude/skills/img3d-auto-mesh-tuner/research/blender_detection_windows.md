# Blender detection on Windows: probe results and recommendations (Img3D Auto Mesh Tuner, step 1)

I didn't modify any existing project source file or anything under `C:\Program Files`. All the new probe scripts are under `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\` and are run with `backend\python\python.exe -s <file>`. Their outputs are under `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\detect\`. Other agents' files share the `probe\` folder. Mine are the `detect_*` files, `jobobject_probe.py` and `blender_side\`.

| File | What it proves |
|---|---|
| `probe\detect_cli_probe.py` (+ `blender_side\{argv_dump,stream_lines,raise_exc,sys_exit}.py`) | Exit codes, `sys.argv`, stdout streaming and encoding, on 4.2 and 5.2 → `detect\cli_probe.json` |
| `probe\jobobject_probe.py` (+ `blender_side\{spawn_child,sleep_long}.py`) | Killing the whole process tree with a Job Object; child-process census → `detect\jobobject_probe.json` |
| `probe\detect_platform_probe.py` | PE header, native architecture, LongPathsEnabled, manifests → `detect\platform_probe.json` |
| `probe\detect_longpath_probe.py` (+ `blender_side\longpath_check.py`) | Paths longer than 260 characters, with and without `\\?\` → `detect\longpath_probe.log` |
| `probe\detect_launcher_probe.py` | `blender-launcher.exe` compared with `blender.exe` → `detect\launcher_probe.json` |
| `probe\detect_tempdir_probe.py`, `detect_misc_probe.py`, `detect_detached_probe.py` | Temp-dir leak, line-buffering fix, `BLENDER_USER_RESOURCES`, `DETACHED_PROCESS` |
| **`probe\detect_blender_probe.py`** | **Reference detector**: all location sources, validation, version regex, runtime selection → `detect\detect_blender.json` |
| `detect\help_5.2.txt`, `detect\help_4.2.txt` | Full `--help` output (exit 0; 524 and 414 lines) |

---

## 1. Blender command-line behaviour (measured on this PC)

### `--factory-startup --version`
- **Timing** (exit 0 in every run):
  - 5.2: 108–132 ms.
  - 4.2: 119–127 ms.
- **5.2.2 output** (all on stdout, stderr empty), in this order:
  1. `Blender 5.2.2 LTS`
  2. `\tbuild date: 2026-09-15`
  3. `\tbuild time: 01:37:04`
  4. `\tbuild commit date: 2026-09-14`
  5. `\tbuild commit time: 15:14`
  6. `\tbuild hash: d13f752e3b9c`
  7. `\tbuild branch: blender-v5.2-release`
  8. `\tbuild platform: Windows`
  9. `\tbuild type: Release`
  10. `\tbuild c flags: …`
  11. `\tbuild c++ flags: …`
  12. `\tbuild link flags: /MACHINE:X64 /SUBSYSTEM:CONSOLE …`
  13. `\tbuild system: CMake`
- **4.2.17 output:** same layout, with hash `76b996a81c95` and branch `blender-v4.2-release`.
  - It also prints `TBBmalloc: skip allocation functions replacement in ucrtbase.dll: unknown prologue for function _msize` on **stderr** at every start, including `--help`. Do not treat a non-empty stderr as a failure, and do not assume the version is on line 1 of merged output.
- **Fast version read without executing anything:** `VS_FIXEDFILEINFO` (`file_version()` in `detect_blender_probe.py:90`) returns `(5,2,2,0)` and `(4,2,17,0)` in about 1 ms.
  - The string resources `FileVersion`/`ProductVersion` contain only `"5.2"` / `"4.2"`.
  - It does not give the release cycle, so use it only as a pre-filter.
- **Background start-up cost** (`-b … --python-expr`):
  - 5.2: about 1.40–1.63 s.
  - 4.2: about 0.40–0.49 s (1.8 s on the first, cold run).

### Help flags (5.2: `help_5.2.txt` line numbers)
- **Background and scripts:**
  - `-b/--background` (5): disables audio.
  - `-P/--python <file>` (145), `--python-text` (148).
  - `--python-expr <expr>` (151): accepts a multi-line script.
  - `--python-exit-code <0..255>` (160): only applies to scripts run from the command line; 0 disables it.
  - `--python-use-system-env` (164): needed for `PYTHONPATH` etc.
  - `--python-use-user-env` (168): **new in 5.x**.
  - `--addons a,b` (172): comma-separated, no spaces.
- **Start-up and autoexec:**
  - `-X/--factory-startup` (399). 4.2 only has the long form `--factory-startup`.
  - `-y/--enable-autoexec` (138), `-Y/--disable-autoexec` (141). Disabled is the default.
  - `--online-mode`/`--offline-mode` (177/180): present in 4.2 too.
- **Logging and quiet:**
  - `--log <match>` (185), `--log-level` (194), `--log-file` (213). 5.x uses named levels `fatal|error|warning|info|debug|trace`; 4.2 uses a number (`-1` = all).
  - `-d/--debug` (222), `--debug-python` (248).
  - `-q/--quiet` (352): **5.x only, not in 4.2.** It suppresses the `-b` banner (verified).
- **Other:**
  - `-c/--command` (427): implies `-b`.
  - `-v/--version` (460).
  - `--` (463): ends option processing; everything after it is passed unchanged into `sys.argv`.
- **Environment variables** (506–524):
  - `BLENDER_USER_RESOURCES` (other `BLENDER_USER_*` variables override it), `BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS`, `BLENDER_USER_EXTENSIONS`, `BLENDER_USER_DATAFILES`.
  - `BLENDER_SYSTEM_RESOURCES`, `BLENDER_SYSTEM_SCRIPTS` ("Directories to add extra scripts"), `BLENDER_SYSTEM_EXTENSIONS`, `BLENDER_SYSTEM_DATAFILES`, `BLENDER_SYSTEM_PYTHON`.
  - `BLENDER_CUSTOM_SPLASH[_BANNER]`.
  - `BLENDER_OCIO` (falls back to `OCIO`). 4.2 only knows `OCIO`.
  - `TEMP`: must be an existing directory.
- **Verified:** `BLENDER_USER_RESOURCES=<dir>` redirects `resource_path('USER')` and `user_resource('CONFIG'/'SCRIPTS')` to `<dir>\config` and `<dir>\scripts`. A query alone does not create the directory.

### Exit-code matrix (`-b --factory-startup --disable-autoexec`; identical on 4.2 and 5.2)

| Case | Exit code |
|---|---|
| `--python-exit-code 1 --python-expr "raise SystemExit(3)"` | **3** |
| Same, without `--python-exit-code` | **3** (SystemExit always wins; no "Blender quit" line) |
| `sys.exit(0)` | **0** |
| File script calling `sys.exit(5)` | **5** |
| Uncaught exception with `--python-exit-code 1` | **1**; stderr shows the traceback plus `Error: script failed, file: '<path>', exiting.`; remaining arguments are **not** processed |
| Uncaught exception with `--python-exit-code 7` | **7** |
| Uncaught exception **without** `--python-exit-code` | **0**: traceback on stderr, and Blender carries on with the next arguments (a second `--python-expr` still ran) |
| Missing script file with `--python-exit-code 1` | **1** (`OSError: Python file "…" could not be opened`) |
| Unknown option before `--` | **1**: `unknown argument, loading as file: --x`, then `Cannot read file` |

So `--python-exit-code` is mandatory, and every user argument must go after `--`.

### `sys.argv` after `--`
- Passed: `["a b", "C:\dir with space\in.glb", "--flag=1", "-x", "é✓", "", "--"]`.
- Received byte-for-byte identical, including the empty string, Unicode, and a second `--`.
- `sys.argv[0]` is the path to `blender.exe`; all Blender options are visible in `sys.argv`.
- Parse with `sys.argv[sys.argv.index("--")+1:]`.

### Blender's embedded Python
- **5.2:** Python 3.13.13; `sys.executable` = `…\Blender 5.2\5.2\python\bin\python.exe`.
- **4.2:** Python 3.11.7.
- **Flags:** `isolated=1`, `ignore_environment=1` (so `PYTHONUNBUFFERED` and similar variables are ignored), `utf8_mode=1`.
- **stdout:** UTF-8 with `errors=surrogateescape`, `line_buffering=False`, `isatty=False`.
- **`bpy.app` values (5.2):** `version=(5,2,2)`, `version_cycle='release'`, `version_file=(5,2,45)`, `build_branch=b'blender-v5.2-release'`, `online_access=False`.
- **Resource paths (5.2):**
  - USER = `%APPDATA%\Blender Foundation\Blender\5.2`
  - LOCAL = `<exe dir>\5.2`
  - SYSTEM = `C:\ProgramData\Blender Foundation\Blender\5.2`

### Streaming over a pipe
- **Without `flush`:** all 6 lines (printed 0.5 s apart) arrived at once at exit (t = 3.911 s on 5.2, 2.807 s on 4.2).
- **With `flush=True`:** lines arrived exactly 0.50 s apart.
- **Fix to put at the top of every Blender-side script:** `sys.stdout.reconfigure(line_buffering=True)`. Verified: lines arrived at 1.345, 1.845, 2.345 and 2.846 s.
- **Blender's own C-level output is block-buffered by the C runtime and arrives only at exit.** This covers the `-b` banner `Blender 5.2.2 LTS (hash d13f752e3b9c built 2026-09-15 01:37:04)`, `Blender quit`, CLOG lines and operator timings.
  - It interleaves out of order with Python output.
  - A report printed without a trailing newline got glued onto the front of my JSON line.
  - → Use a sentinel-prefixed JSON-line protocol and find it with `"SENTINEL" in line`, not `startswith`.
  - → Never use the banner to detect the version.
- **Line endings:** Python `print` produces CRLF (`0d0a`); Blender's C prints produce LF.
- **Log format:** 5.2 CLOG lines such as `00:01.313  reports          | ERROR …` go to **stdout**. Python tracebacks and `Error: script failed` go to **stderr**.

### Encoding
- The raw bytes of `é ✓ 日本 Ω` are UTF-8 (`c3a9 e29c93 e697a5e69cac cea9`) on both stdout and stderr, in both versions.
- On the host, use `encoding="utf-8", errors="replace"`.
- Both executables' manifests set `activeCodePage=UTF-8`.

### Child processes
- A plain `-b` run of `blender.exe` started with `CREATE_NO_WINDOW` has exactly **one child, `conhost.exe`** (the hidden console host).
- If Blender's Python spawns a subprocess, it becomes a grandchild of `blender.exe` (python.exe in the probe).
- `TerminateProcess` on `blender.exe` alone left **both the `python.exe` grandchild and `conhost.exe` orphaned** (`A_control_TerminateProcess.orphans_after_kill`).

### `blender-launcher.exe` compared with `blender.exe`
- `blender-launcher.exe` has PE subsystem **GUI (2)**; `blender.exe` is **console (3)**. Both are x64 PE32+.
- The launcher spawns `"…\blender.exe" <same args>` as a child, waits for it, and **passes through the exit code** (4 → 4).
- But when piped, **stdout and stderr are empty** (for both `--version` and `-b` runs).
- The `.blend` ProgID, the Start-menu `.lnk` files and the scoop shortcut all point at the launcher. Always map it to the sibling `blender.exe`.

### Temp directories
- Blender creates `%TEMP%\blender_a<5 digits>\` (= `bpy.app.tempdir`). The suffix is **not** the PID (PID 46592 got `blender_a50640`).
- It is removed on normal exit and **leaked when the process is killed** (verified).
- `TEMP`/`TMP` overrides are honoured.
- This machine already has 4 leaked `%TEMP%\blender_a*` directories from 11:52, left by other runs.
- → Give each run its own `TEMP=TMP=backend\runtime\temp\blender\<run_id>`. `runtime\temp` is already purged at start-up by `backend\storage\paths.py:78` (`purge_temp`).

---

## 2. Where Blender can be installed on Windows

Read costs come from `detect_blender_probe.py` `TIMINGS`, using winreg from the embedded Python.

| # | Source | Generic pattern | What exists on this PC | Cost |
|---|---|---|---|---|
| 1 | MSI Uninstall keys | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{ProductCode}` read in both `KEY_WOW64_64KEY` and `_32KEY` views, plus the same under HKCU. Match `DisplayName` containing "blender" (it is just `"Blender"`; also check `Publisher="Blender Foundation"`), then `InstallLocation`\blender.exe | `{4B0BC91B-29A6-4B8C-A60B-6154FD3AE3AD}` 5.2.2 → `C:\Program Files\Blender Foundation\Blender 5.2\` (trailing `\`). `{F7E744FF-AB6F-4AD1-B3C2-18DAB89D228D}` 4.2.17 → `…\Blender 4.2\`. `WindowsInstaller=1`, `VersionMajor/Minor` set, `DisplayIcon` empty. Nothing under WOW6432Node or HKCU. The 5.2.2 ProductCode matches the winget manifest (`InstallerType: wix`, `Scope: machine`, `UpgradeCode {60132683-FE29-5605-8A63-E5B523FC4D5C}`; the arm64 MSI is `{B36F1168-5D61-4476-85B3-B5A2439CECE1}`) | 14.7–15.4 ms for all 4 views |
| 2 | Install directories | `%ProgramW6432%`, `%ProgramFiles%`, `%ProgramFiles(x86)%` and `%LOCALAPPDATA%\Programs`, each `\Blender Foundation\*\blender.exe` | `Blender 4.2` ✓, `Blender 5.2` ✓. `Blender 4.4` and `Blender 4.5` have **no exe**, only `4.x\python\lib` and `4.x\scripts\{addons_core,modules,startup}` remnants. The (x86) and LOCALAPPDATA locations are absent | 0.3 ms |
| 3 | PATH | `shutil.which("blender")` plus a scan of PATH directories | none (`where.exe` finds nothing) | 34–36 ms (PATHEXT probing) |
| 4 | `.blend` association | `HKCU\Software\Classes\.blend` and `HKLM\Software\Classes\.blend` (default value plus `OpenWithProgids` value names) plus `HKCU\…\Explorer\FileExts\.blend\UserChoice\ProgId`. Each `HKCR\<progid>\shell\open\command` gives `"…\blender-launcher.exe" "%1"` | Default `blender.5.2`; ftype `blender.5.2="C:\Program Files\Blender Foundation\Blender 5.2\blender-launcher.exe" "%1"`. OpenWithProgids: `blender.4.2, 4.4, 4.5, 5.0, 5.1, 5.2` (4.4/4.5/5.0/5.1 **stale**). **UserChoice = `blender.4.2`**, so the real double-click handler differs from the HKCR default. `HKCR\blendfile` is **absent** (that is the old NSIS ProgID) | 0.7 ms |
| 4b | `Applications` keys | `HKCR\Applications\blender.exe` and `\blender-launcher.exe`, `\shell\open\command` | `blender.exe` → `…\Blender 4.5\blender.exe` (**stale**); `blender-launcher.exe` → `…\Blender 4.2\blender-launcher.exe` | included in 4 |
| 5 | App Paths | `HK{LM,CU}\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe` | absent (the MSI does not write it) | 0.04 ms |
| 6 | Start menu | `%APPDATA%` and `%ProgramData%` `\Microsoft\Windows\Start Menu\Programs\Blender\*.lnk` (resolving them needs COM) | `Blender {4.2,5.0,5.1,5.2}.lnk` → `…\blender-launcher.exe`; 5.0 and 5.1 are stale. They are per-user even though the MSI is per-machine. Not needed (covered by source 1) | n/a |
| 7 | Steam (appid 365670) | `HKCU\Software\Valve\Steam\SteamPath`, `HKLM\SOFTWARE\Valve\Steam\InstallPath` (32-bit view = WOW6432Node), then `<root>\steamapps\libraryfolders.vdf` `"path"` entries, then `<lib>\steamapps\common\Blender\blender.exe` | `SteamPath` absent. `InstallPath=E:\<user>\Steam` is **stale**: the directory doesn't exist and `E:\<user>` is empty. No `Steam App 365670` Uninstall key | 0.2 ms |
| 8 | Microsoft Store | `Get-AppxPackage *Blender*` (slow, PowerShell), or winreg enumeration of `HKCU\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages` (`PackageRootFolder`). Execution alias: `%LOCALAPPDATA%\Microsoft\WindowsApps\blender.exe` | No Blender package (402 packages enumerated in 2.0 ms); no alias. Alias behaviour was checked on `WindowsApps\winget.exe`: a 0-byte reparse point with tag `0x8000001B` (APPEXECLINK); `os.path.isfile` is True, `open()` fails with **EINVAL** so the PE header can't be checked, but **`subprocess` can execute it** (rc 0, 84 ms) | 2 ms |
| 9 | winget | The Blender manifest uses the MSI, so it shows up in sources 1 and 2. Portable packages only: `%LOCALAPPDATA%\Microsoft\WinGet\Packages\BlenderFoundation.Blender*\**\blender.exe`; `WinGet\Links` | No `BlenderFoundation.*`; `Links` contains only ffmpeg, ffplay, ffprobe | <1 ms |
| 10 | scoop (Extras `blender.json`) | Zip install, `extract_dir blender-5.2.2-windows-x64`, `bin: blender.exe, blender-launcher.exe` (shims in `~\scoop\shims`), `persist: portable`. Paths: `%SCOOP%` or `%USERPROFILE%\scoop\apps\blender\{<ver>,current}\blender.exe`; global install under `C:\ProgramData\scoop\apps\blender\…` | absent | <1 ms |
| 11 | chocolatey | `%ChocolateyInstall%` or `C:\ProgramData\chocolatey\lib\blender\**\blender.exe`. The package wraps the MSI, so normally sources 1 and 2 catch it (not verified, since choco isn't installed) | `C:\ProgramData\chocolatey` absent | <1 ms |
| 12 | Blender Launcher V2 (third-party) | From its source (`platform_utils.py`, `settings.py`, `build_info.py`). Settings ini: `<launcher exe dir>\Blender Launcher.ini` (portable, takes priority), otherwise `%LOCALAPPDATA%\Blender Launcher\Blender Launcher.ini`. Default library: `%LOCALAPPDATA%\Blender Launcher`. Library subfolders: `stable, daily, experimental, bforartists, upbge-stable, upbge-weekly, custom` (+ `template`). Each build directory has `blender.exe` and a `.blinfo` JSON file `{file_version, blinfo:[{branch, subversion, build_hash, commit_time, custom_name, is_favorite, custom_executable, is_frozen}]}`. The launcher itself reads the version by running `blender.exe -v` | absent | 0.3 ms |
| — | User config only (proves nothing) | `%APPDATA%\Blender Foundation\Blender\<X.Y>` | `4.2, 4.4, 4.5, 5.0, 5.1, 5.2` | — |

**Registry hang:** avoid `-Recurse` and any enumeration of HKCR. Read only the specific keys above; every read was under 16 ms.

Result of the reference detector:
- **Selected:** `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`. It was found by three sources (dir, uninstall, progid).
- 4.2.17 was found and parsed but marked incompatible (below 5.1).
- **Rejected as missing:** 4.4 and 4.5 (dir and progid), 5.0 and 5.1 (progid), Applications → 4.5, and the stale Steam path.
- **Total time:** 52 ms to collect candidates plus 255 ms to execute both binaries.

---

## 3. Version string format and parsing regex

**Source** (fetched from `projects.blender.org`, branch `main`):
- `BKE_blender_version.h`: `BLENDER_VERSION 504`, `BLENDER_VERSION_PATCH 0`, `BLENDER_VERSION_CYCLE alpha` ("alpha/beta/rc/release"), `BLENDER_VERSION_SUFFIX` (LTS when set), `BLENDER_FILE_SUBVERSION 3`. So main is currently 5.4.0 Alpha.
- `blenkernel/intern/blender.cc` `blender_version_init()`:
  - Full string: `"%d.%01d.%d%s%s"` = `V/100`, `V%100`, `PATCH`, then `" LTS"` (from `BKE_blender_version_is_lts()`, i.e. the SUFFIX, **not** the cycle), then the cycle text.
  - Cycle text: alpha → `" Alpha"`, beta → `" Beta"`, rc → `" Release Candidate"`, release → `""`.
  - Compact form (`" a"/" b"/" RC"`, never includes LTS) is used only in the UI.
- `source/creator/creator_args.cc`:
  - `print_version_full` prints `"Blender %s\n"`, then the `"\tbuild <key>: %s\n"` lines **only `#ifdef BUILD_DATE`**. Custom builds without it print just the first line.
  - `print_version_short` (the `-b` banner, skipped when `--quiet`): `"Blender %s (hash %s built %s %s)\n"`.
  - `--` is handled by `arg_handle_arguments_end`.
  - The hash is `unknown` for non-git builds.
- **Legacy 2.7x/2.80:** `"Blender %d.%02d (sub %d)"`, e.g. `Blender 2.79 (sub 0)`. From memory of the old creator source; not re-fetched.

**Cycle mapping:**

| Version text suffix | `bpy.app.version_cycle` |
|---|---|
| (none) | `release` |
| ` Alpha` | `alpha` |
| ` Beta` | `beta` |
| ` Release Candidate` | `rc` |

`LTS` is independent of the cycle. Daily builds:
- Alpha builds come from branch `main`.
- Beta, RC and point-release dailies come from `blender-vX.Y-release`.
- A release-branch daily prints `cycle=release`, the same as the official build, so `--version` cannot tell them apart.
- The official installs here have a Valid Authenticode signature, `CN=Blender Foundation, O=Blender Foundation, L=Amsterdam, S=Noord-Holland, C=NL`. Whether daily builds are signed was not verified.

**Regex** (`detect_blender_probe.py:34–43`, `re.MULTILINE`, use `search()` so noise lines before it don't matter):
```python
VERSION_RE = re.compile(
    r"^Blender[ \t]+(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?(?P<char>[a-z])?"
    r"(?P<tags>(?:[ \t]+(?:LTS|Alpha|Beta|Release[ \t]+Candidate|RC))*)"
    r"(?:[ \t]+\(sub[ \t]+(?P<sub>\d+)\))?"
    r"(?:[ \t]+\(hash[ \t]+(?P<hash>[0-9a-fA-F]+|unknown)[ \t]+built[ \t]+(?P<built>[^)]*)\))?"
    r"[ \t]*\r?$", re.MULTILINE)
BUILD_LINE_RE = re.compile(r"^[ \t]+build (?P<key>[a-z +]+?):[ \t]?(?P<val>.*?)[ \t]*\r?$", re.MULTILINE)
# cycle = last of {Alpha:alpha, Beta:beta, Release Candidate:rc, RC:rc} found in tags, else "release"
```

All 17 sample strings were verified (`sample_parse` in `detect_blender.json`):

| Input | Parsed |
|---|---|
| `5.2.2 LTS` | release, LTS |
| `4.2.17 LTS` | release, LTS |
| `5.1.0` | release |
| `5.3.0 Alpha` | alpha |
| `5.2.0 Beta` | beta |
| `5.2.0 Release Candidate` | rc |
| `4.2.0 LTS Beta` | beta, LTS |
| `-b` banner with hash and build date | hash and date extracted |
| `2.79 (sub 0)` | 2.79.0, sub 0 |
| `2.80 (sub 75)` | 2.80.0, sub 75 |
| `2.93.18` | release |
| TBBmalloc noise line first, then the version | parsed, plus hash and branch from the build lines |
| `build hash: unknown` / `build branch: main` | hash `unknown`, branch `main` |
| `5.10.0` | 5.10.0 |
| `Bforartists 4.2.0` | None |
| An error line | None |

---

## 4. Windows process handling

**(a) Hiding the console.** Use `creationflags=CREATE_NO_WINDOW (0x08000000)`.
- The output is captured, and one hidden `conhost.exe` is created as a child of `blender.exe`. It belongs to the job (job ActiveProcesses = 3: blender, conhost, grandchild).
- Do **not** use `DETACHED_PROCESS`. Verified (`detect_detached_probe.py`): it avoids conhost for `blender.exe`, but a console grandchild then gets its **own new `conhost.exe`**, which means a visible console window.
- Pass `stdin=subprocess.DEVNULL`.

**(b) Killing the whole process tree.** Verified in `jobobject_probe.py`:
- Helpers: `KillOnCloseJob` at :121, `popen_in_job` at :156.
- Limit flags: `KILL_ON_JOB_CLOSE | DIE_ON_UNHANDLED_EXCEPTION`. Assert `sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144`.
- Start order: `Popen(..., creationflags=CREATE_SUSPENDED|CREATE_NO_WINDOW)`, then `OpenProcess(PROCESS_SET_QUOTA|PROCESS_TERMINATE|PROCESS_SUSPEND_RESUME)`, then `AssignProcessToJobObject`, then `ntdll.NtResumeProcess`. This leaves no window in which a grandchild could escape the job.

```python
p = subprocess.Popen(args, creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW, stdout=PIPE, stderr=PIPE, stdin=DEVNULL, env=env)
job.assign(p.pid)                                    # AssignProcessToJobObject
h = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, p.pid); ntdll.NtResumeProcess(h); k32.CloseHandle(h)
# cancel/timeout: k32.TerminateJobObject(job.handle, 0xC000013A)
```

Results:

| Scenario | Outcome |
|---|---|
| `TerminateJobObject` | blender + python grandchild + conhost dead in **16.1 ms**; `p.returncode = 3221225786` (`0xC000013A`); ActiveProcesses 3 → 0 |
| `CloseHandle(job)` | tree dead in **13.4 ms**, but **exit code 0**. Record the cancel yourself; don't infer it from the exit code |
| Job owner hard-killed (`TerminateProcess` on the helper) | Windows closes the job handle and the whole tree dies in **15.3 ms**. This covers a backend crash |
| Control: `p.kill()` only | grandchild and conhost **orphaned** |

- The probe process was itself already inside a job (`IsProcessInJob=True`). Nested jobs (Windows 8+) worked without needing breakaway.
- Read stdout and stderr in daemon threads (`start_reader` at :223). The pipes close when the job kills the tree.

**(c) Long paths.**
- `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1` (REG_DWORD; 0.043 ms to read).
- The embedded `python.exe` manifest is `longPathAware`, so `RtlAreLongPathsEnabled()` is True there.
- **Neither Blender `blender.exe` manifest has `longPathAware`** (only `activeCodePage UTF-8`).
- Test with a 372-character path:
  - Plain path: `open()`, `wm.obj_import` and the glTF export all fail on both 4.2 and 5.2.
  - With `\\?\` on 4.2: `open()`, OBJ import and glTF export all work.
  - With `\\?\` on 5.2: `open()` and OBJ import work, but **the glTF export fails** with `OSError: [WinError 123]` in `io_scene_gltf2\blender\exp\export.py:59` `os.makedirs` (the directory has a `/` appended).
- → Do not rely on `\\?\`. Validate that every path given to Blender is ≤ 259 characters and keep run directories short. `backend\runtime\jobs\<id>\model.glb` is about 85 characters.

**(d) Native architecture.**
- `platform.machine()='AMD64'`, `PROCESSOR_ARCHITECTURE=AMD64`, `PROCESSOR_ARCHITEW6432=None`.
- `IsWow64Process2`: process = native (0), native = **x64**.
- Use `IsWow64Process2`'s `nativeMachine` (`detect_blender_probe.py:106`). An x64 process emulated on ARM64 reports AMD64 through `platform.machine()` and the environment variables.
- Official zip URL pattern (from the scoop and winget manifests): `https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-{x64|arm64}.{zip|msi}`.

**(e) PE header check.** From `detect_blender_probe.py:75` (also `detect_platform_probe.py:23`), about 0.5–1 ms per file:

```python
def pe_machine_subsystem(path):
    with open(path, "rb") as f:
        dos = f.read(64);  assert dos[:2] == b"MZ"
        f.seek(struct.unpack_from("<I", dos, 0x3C)[0]); hdr = f.read(94)
    assert hdr[:4] == b"PE\0\0"
    machine = {0x8664:"x64",0xAA64:"arm64",0x14C:"x86",0xA641:"arm64ec"}.get(struct.unpack_from("<H", hdr, 4)[0], "other")
    subsystem = {2:"gui",3:"console"}.get(struct.unpack_from("<H", hdr, 92)[0], "other")  # optional hdr +68
    return machine, subsystem
```

Results: both `blender.exe` files are `x64` (0x8664), PE32+, console. Both `blender-launcher.exe` files are `x64`, gui. `backend\python\python.exe` is x64, console.

---

## 5. Recommended detection order and selection rule

1. **Collect candidates**, cheapest and most authoritative first:
   1. A user-pinned path, then the project's embedded runtime directory.
   2. MSI Uninstall keys (HKLM 64-bit view, HKLM 32-bit view, HKCU).
   3. `Program Files` / `Program Files (x86)` / `%LOCALAPPDATA%\Programs` `\Blender Foundation\*`.
   4. PATH.
   5. `.blend` ProgIDs (HKCU/HKLM Classes default + OpenWithProgids + UserChoice), `HKCR\Applications\*`, App Paths.
   6. scoop / winget-portable / choco.
   7. Steam (registry → `libraryfolders.vdf`).
   8. Blender Launcher V2 library.
   9. Microsoft Store: AppModel repository key, or the alias. With the alias, skip the PE check and just execute it.
2. **Normalise:**
   - `expandvars`, strip quotes, take the executable from command strings (`_exe_from_command`).
   - Map `blender-launcher.exe` to the sibling `blender.exe`.
   - Deduplicate on `normcase(realpath)`.
   - Drop missing files. Registry and shortcut entries go stale (5 stale entries on this PC), and an `X.Y` folder does not prove an install (4.4 and 4.5 here).
3. **Validate:**
   - PE machine must equal the native machine (x64 on arm64 is allowed only as a fallback), and the subsystem must be console.
   - Optional `VS_FIXEDFILEINFO` pre-filter: skip running anything below 5.1.
   - Then run `"<exe>" --factory-startup --version` with `CREATE_NO_WINDOW`, `stdin=DEVNULL`, `cwd=<exe dir>`, a 20 s timeout and UTF-8 decoding, and parse it with the regex.
   - Cache the result keyed on (path, size, mtime).
4. **Select:**
   - Compatible means `version >= (5,1,0)`, native architecture, console subsystem.
   - Pick the highest **stable** (`cycle == "release"`) version. Order by (stable, version, release > rc > beta > alpha).
   - Use alpha, beta or RC builds only when the user explicitly selects them.
   - On this PC: 5.2.2 LTS.
5. **Runner command line:** `blender.exe --background --factory-startup --disable-autoexec [--offline-mode] [-q (5.x only)] --python-exit-code <N> --python <script> -- <args>`
   - Inside the job object.
   - Environment: `TEMP=TMP=<run temp dir>`, and optionally `BLENDER_USER_RESOURCES=<isolated dir>`.
   - In the Blender-side script: `sys.stdout.reconfigure(line_buffering=True)` and sentinel JSON lines.
   - Treat these exit codes as follows:
     - `N`: uncaught exception.
     - Script-chosen code: from `sys.exit`.
     - `0xC000013A`: cancelled.
     - Anything else: crash.