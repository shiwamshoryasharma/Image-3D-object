# Auto Mesh Tuner — design contract (authoritative)

Status: v1 design, 2026-10-15. Every implementation agent codes against THIS file. If you must deviate, record
the deviation in the "Deviations" section at the bottom (what + why) instead of silently changing a contract.

Goal: one-click "Auto Tune" that turns a raw TRELLIS.2 GLB into a cleaner character mesh (fix the hollow
double-walled shell, holes, floating bits, normals, noise, eye/face defects, optional symmetry repair, finer
density where faceted, rig weight cleanup when a rig exists) using **Blender >= 5.1 in background mode**, with
automatic Blender runtime detection/selection and an optional official portable Blender download.

```
Launch → scan Blender installs (execute each, read real version) → compatible >= 5.1?
   yes → pick per priority (embedded pin > system pin > highest stable system > highest embedded)
   no  → ask permission → download official portable ZIP → verify sha256 → safe-extract to runtime/blender
 → selected blender.exe --background --factory-startup ... --python blender_scripts/auto_mesh_tuner.py -- --config cfg.json
 → Auto Mesh Tuning steps → tuned.glb + report.json (+ previews, optional .blend)
```

## 0. Ground truth (measured on this PC, see test\outputs\mesh_tuner\)

- Installed: `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (5.2.2 LTS, Py 3.13.13, numpy 2.3.4) and
  `...\Blender 4.2\blender.exe` (4.2.17 LTS). Folders `Blender 4.4`, `Blender 4.5` exist but have **no exe** (leftovers).
  Registry MSI Uninstall entries exist for 5.2.2 and 4.2.17 (`DisplayName="Blender"`, `InstallLocation`).
  `.blend` ProgIDs list stale 4.4/4.5/5.0/5.1 entries → existence + execution checks are mandatory.
- `blender.exe --factory-startup --version` ≈ 0.13 s. First matching line `^Blender X.Y.Z[ LTS][ Alpha|Beta|Release Candidate]`.
  4.2 prints `TBBmalloc: ...` noise on **stderr** → search all lines with a MULTILINE regex, never take line 1.
- `blender-launcher.exe` is a GUI-subsystem wrapper: piped output is EMPTY → always map it to sibling `blender.exe`.
- `--python-exit-code N` is mandatory (without it a script exception exits 0). `sys.exit(k)` → rc k.
  Everything after `--` reaches `sys.argv` verbatim. Blender's Python is isolated + ignores env vars; script dir is NOT on sys.path.
- Blender's Python stdout is block-buffered when piped → scripts must `sys.stdout.reconfigure(line_buffering=True)`
  and flush. Blender's own C prints interleave out of order → protocol lines are found by **substring** sentinel.
- Kill-tree: `TerminateProcess(blender)` orphans conhost + grandchildren. Use a **Job Object** with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; start suspended → assign → resume (probe: `test\mesh_tuner\probe\jobobject_probe.py`).
  Exit code after `TerminateJobObject(job, 0xC000013A)` = 3221225786.
- Blender leaks `%TEMP%\blender_aNNNNN` when killed → give every run its own `TEMP/TMP`.
- Blender exes are NOT longPathAware; the 5.2 glTF exporter fails on `\\?\` paths → keep every path passed to
  Blender ≤ 259 chars; validate and fail with a clear message otherwise.
- `gpu.init()` exists in 5.2 and works in `--background` (OpenGL, RTX 5090): offscreen render + exact RGBA8 readback
  verified (`test\mesh_tuner\probe\design\gpu_offscreen_probe.py`). Must be feature-detected (5.1 / no-GPU fallback).
- Official downloads: `https://download.blender.org/release/Blender5.N/blender-X.Y.Z-windows-{x64|arm64}.zip`
  + `blender-X.Y.Z.sha256` (GNU sha256sum format, both Windows zips listed). Latest stable ≥5.1 today: **5.2.2 (LTS)**,
  x64 zip 404,453,484 bytes, sha256 `3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535`.
  Cloudflare returns **403 to the default `Python-urllib` User-Agent** → always send `User-Agent: Img3D-BlenderRuntime/1.0`.
  Range/If-Range supported (206); a 200 reply to a Range request means "restart from 0"; 416 → restart.
  ZIP: single top folder `blender-X.Y.Z-windows-ARCH/`, `blender.exe` inside it, ~7500 entries, ~944 MB unpacked,
  longest entry 168 chars, deflate/stored only.
- Real TRELLIS.2 GLBs (`backend\runtime\jobs\job_*\model.glb`, 41–88 MB): trimesh-written, 1 node, 1 primitive,
  triangles only, ~1.6–1.8M raw verts / 1.9–2.0M faces, 39–50% verts are UV-seam duplicates (weld → ~0.9M),
  WebP textures (EXT_texture_webp required) 4096², +Y-up glTF → Blender **+Z up, character faces −Y**, character's
  left = +X, fitted to the [−0.5,0.5]³ cube (feet at z≈−0.5).
  **Root defect: hollow double-walled shell** — an inner wall ~2 voxels (~1.3–1.5e-3 units) behind the outer surface
  on 74–84% of faces; only ~51% of faces are visible from outside. Outer and inner walls connect at hole rims.
  Other defects: 3–6-face non-manifold junctions (2k–33k edges), opposite-wound coincident faces, 2–191 real holes
  (≥5 edges; largest 112 edges / 8.5% diag), 155–430 tiny floating parts (but legit separate parts exist —
  e.g. legs = separate 448k-face part → NEVER "keep largest only"), 0.2–1.4% inconsistent winding, degenerate
  slivers, triangle-scale surface noise, flat-facet stair-stepping on smooth areas, eye recesses with grooves/pits/
  blobs + raised rims, sharp vertical midline crease on the face/chin, near-perfect X-mirror symmetry on characters
  (mean 0.06–0.12% diag) but NOT on animals/props (fox 3%, castle 2.9%).
  Timings (5.2, 80 MB GLB): import 3–4 s, bmesh weld 2.4 s, Workbench 1024² render 0.15 s, glTF export 2.5 s,
  peak RAM 4–7 GB.

## 1. Layout

```
backend/
  launcher/                      # Blender RUNTIME management only (no mesh code, no FastAPI imports, stdlib only)
    __init__.py                  # re-exports the public API below
    errors.py
    config.py                    # LauncherSettings + persisted RuntimeConfig (blender_runtime.json)
    blender_version.py           # BlenderVersion + parse_blender_version()
    platform_win.py              # winreg reads, PE header, native arch, long-path check, Job Object (ctypes)
    blender_detector.py          # BlenderDetector → list[BlenderInstallation]
    blender_downloader.py        # BlenderDownloader (resolve, resumable download, sha256, safe extract, validate)
    process_runner.py            # BlenderProcessRunner (+ run_capture for --version probes)
    blender_runtime.py           # BlenderRuntimeManager (policy, state, events, download thread)
  blender_scripts/               # runs INSIDE Blender's Python (3.11+ syntax!, numpy>=1.24). Never imported by backend.
    auto_mesh_tuner.py           # entry: inserts own dir into sys.path, calls img3d_amt.cli.main()
    img3d_amt/ ...               # see §5
  scripts/auto_mesh_tuner.py     # standalone CLI (detect/select/download/tune) — the "Launch Auto Mesh Tuner" flow
  models/tuning.py  storage/tuning_store.py  schemas/mesh_tuner.py  services/blender_runtime_service.py
  services/mesh_tuner_service.py  workers/tuning_worker.py  api/v1/blender.py  api/v1/tunings.py
  runtime/blender/               # == settings.runtime_dir / "blender" unless IMG3D_BLENDER_INSTALL_DIR
    blender_runtime.json         # persisted selection (project configuration)
    detect_cache.json            # version-probe cache keyed by (normcase path, size, mtime_ns)
    blender-5.2.2-windows-x64/   # extracted official portable build (+ .img3d_install.json manifest)
    downloads/                   # *.zip.part + *.part.json (resume state; NOT purged at startup)
    .staging-<hex>/              # extraction staging (deleted on success/failure)
    user/                        # BLENDER_USER_RESOURCES for every run (isolates from %APPDATA% Blender config)
  runtime/temp/blender/<run>/    # per-run TEMP/TMP (purged at startup by RuntimePaths.purge_temp)
  runtime/jobs/<job_id>/tuning/<tn_id>/  run.json tuned.glb report.json blender.log config.json previews/*.png [tuned.blend]
frontend/src/  api/blender.ts api/tunings.ts store/meshTunerStore.ts components/meshTuner/* (+ viewer toggle)
test/launcher/ (pure-python unit tests)   test/mesh_tuner/ (real-Blender tests, marker `blender`)
```

Hard rules: launcher never deletes/modifies anything outside its own `runtime/blender` root (assert path containment
before every rmtree/unlink/replace). Never write to `C:\Program Files`, `%APPDATA%\Blender Foundation`, or the
registry. Never upgrade/replace a system Blender. Never download without explicit user confirmation.

## 2. Launcher public API (`backend/launcher`, stdlib only)

```python
# errors.py — all carry .code (str, snake_case) and .message; services map them to AppError subclasses
class LauncherError(Exception):            code = "launcher_error"
class BlenderNotFoundError(LauncherError): code = "blender_not_found"        # path missing / not blender
class BlenderIncompatibleError(LauncherError): code = "blender_incompatible" # < min version, wrong arch, not console exe
class BlenderUnavailableError(LauncherError):  code = "blender_unavailable"  # no usable runtime selected (msg = user prompt)
class DownloadsDisabledError(LauncherError):   code = "blender_download_disabled"
class DownloadInProgressError(LauncherError):  code = "blender_download_in_progress"
class DownloadError(LauncherError):            code = "blender_download_failed"   # network/HTTP/disk
class ChecksumMismatchError(DownloadError):    code = "blender_checksum_mismatch"
class UnsafeArchiveError(DownloadError):       code = "blender_unsafe_archive"
class DownloadCancelledError(LauncherError):   code = "blender_download_cancelled"
class ReleaseResolveError(DownloadError):      code = "blender_release_unresolved"
class PathTooLongError(LauncherError):         code = "blender_path_too_long"
```

```python
# blender_version.py
@dataclass(frozen=True, order=False)
class BlenderVersion:
    major: int; minor: int; patch: int
    cycle: Literal["release", "rc", "beta", "alpha"] = "release"
    lts: bool = False
    build_hash: str | None = None; build_branch: str | None = None; build_date: str | None = None
    raw: str = ""                                   # the matched "Blender ..." line
    @property tuple -> tuple[int,int,int]; @property is_stable -> bool (cycle == "release")
    @property label -> str  # "5.2.2 LTS", "5.3.0 Beta"
    def at_least(self, minimum: tuple[int,int,int]) -> bool
    def sort_key(self) -> tuple  # (major, minor, patch, cycle_rank) cycle_rank release=3 rc=2 beta=1 alpha=0
def parse_blender_version(output: str) -> BlenderVersion | None   # MULTILINE search, tolerant of noise lines,
    # handles "Blender 2.79 (sub 0)", "Blender 5.2.2 LTS", "... Alpha", "Release Candidate", build lines
def parse_min_version(text: str) -> tuple[int,int,int]   # "5.1" -> (5,1,0)
MIN_SUPPORTED_VERSION = (5, 1, 0)
```

```python
# config.py
RuntimeMode = Literal["auto", "system", "embedded"]
@dataclass
class LauncherSettings:
    runtime_root: Path                 # runtime/blender
    temp_root: Path                    # runtime/temp/blender
    min_version: tuple[int,int,int] = (5, 1, 0)
    override_executable: Path | None = None      # IMG3D_BLENDER_EXECUTABLE (admin override, highest priority)
    extra_search_dirs: list[Path] = field(default_factory=list)
    allow_download: bool = True
    download_base_url: str = "https://download.blender.org/release/"
    download_channel: Literal["latest", "lts"] = "latest"   # latest = newest stable >= min; lts = newest LTS series
    allowed_hosts: tuple[str, ...] = ("download.blender.org", "www.blender.org")
    user_agent: str = "Img3D-BlenderRuntime/1.0 (+local app)"
    detect_timeout_s: float = 20.0
    http_timeout_s: float = 30.0
    max_download_retries: int = 5
    keep_zip_after_install: bool = False
    @property config_path -> runtime_root / "blender_runtime.json"; cache_path; downloads_dir; user_resources_dir
@dataclass
class RuntimeConfig:                     # persisted, schema_version=1, atomic write (tmp + os.replace)
    mode: RuntimeMode = "auto"
    pinned_executable: str | None = None # user's explicit choice ("Change Runtime"), None = highest of mode
    selected_executable: str | None = None   # last resolved selection (informational, written on every resolve)
    selected_version: str | None = None
    selected_kind: Literal["system","embedded"] | None = None
    updated_at: str | None = None
    @classmethod load(path) -> RuntimeConfig   # corrupt/missing → defaults + warning, never raises
    def save(self, path) -> None
```

```python
# blender_detector.py
InstallKind = Literal["system", "embedded"]      # embedded == inside settings.runtime_root
@dataclass
class BlenderInstallation:
    id: str                      # sha1(normcase(realpath(exe)))[:12]
    executable: Path             # always ...\blender.exe (launcher mapped to sibling)
    kind: InstallKind
    origins: list[str]           # e.g. ["program_files","registry_uninstall","progid","path","override","user", "embedded_dir", "steam","scoop","winget","choco","blender_launcher","store"]
    version: BlenderVersion | None
    architecture: str | None     # "x64" | "arm64" | "x86" | other (from PE header)
    valid: bool                  # executed OK and parsed
    compatible: bool             # valid and version >= min and arch runnable and console subsystem
    incompatible_reason: str | None   # "Blender 4.2.17 is older than 5.1", "not stable (Beta)" is NOT incompatible — see policy
    error: str | None            # probe failure detail
    probe_ms: float | None
    def to_dict(self) -> dict    # JSON-ready, version fields flattened: version, version_label, version_tuple, cycle, is_lts, is_stable, build_hash, build_date
class BlenderDetector:
    def __init__(self, settings: LauncherSettings, *, runner: "ProcessCapture" | None = None): ...
    def candidates(self) -> list[tuple[Path, str]]       # (exe path, origin) — cheap, no execution
    def detect(self, *, force: bool = False, extra: Iterable[Path] = ()) -> list[BlenderInstallation]
        # dedupe by normcase(realpath); drop non-existent; PE check (machine/subsystem); execute
        # `--factory-startup --version` (timeout, CREATE_NO_WINDOW, stdin DEVNULL, cwd=exe dir) unless the
        # cache entry for (path,size,mtime_ns) exists and force=False; parallel probes (max 4 threads);
        # sorted by version desc (invalid last). Never raises for a single bad candidate.
    def probe(self, exe: Path, origin: str = "user") -> BlenderInstallation   # always executes
```
Candidate sources (in this order; each wrapped in try/except, each logged with timing at DEBUG):
override_executable · runtime_root/*/blender.exe (embedded) · extra_search_dirs/*/blender.exe and dir/blender.exe ·
HKLM(64+32 view)/HKCU `...\Uninstall\*` with DisplayName containing "blender" or Publisher "Blender Foundation"
→ InstallLocation · `%ProgramW6432%`, `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%\Programs` `\Blender Foundation\*\blender.exe` ·
PATH (`shutil.which` + every PATH dir) · `.blend` ProgIDs (HKCU/HKLM Classes `.blend` default + OpenWithProgids + UserChoice → `<progid>\shell\open\command`) ·
`HKCR\Applications\blender*.exe` · App Paths · scoop (`%SCOOP%` or `~\scoop\apps\blender\*\`, global `C:\ProgramData\scoop\apps\blender\*\`) ·
winget portable (`%LOCALAPPDATA%\Microsoft\WinGet\Packages\BlenderFoundation.Blender*\**\blender.exe`, depth ≤ 3) ·
chocolatey (`%ChocolateyInstall%\lib\blender\**`, depth ≤ 4) · Steam (HKCU `SteamPath`, HKLM `InstallPath` → `steamapps\libraryfolders.vdf` paths → `steamapps\common\Blender\blender.exe`) ·
Blender Launcher V2 library (`%LOCALAPPDATA%\Blender Launcher\{stable,daily,experimental,custom}\*\blender.exe`) ·
Microsoft Store alias `%LOCALAPPDATA%\Microsoft\WindowsApps\blender.exe` (0-byte reparse point: skip PE check, just execute).
NEVER enumerate HKCR or use recursive registry walks (a recursive query hung >120 s on this PC).

```python
# process_runner.py
RunStatus = Literal["succeeded", "failed", "script_error", "cancelled", "timed_out", "crashed", "launch_failed"]
PROTOCOL_SENTINEL = "@@AMT@@"
SCRIPT_EXCEPTION_EXIT_CODE = 86        # passed as --python-exit-code
@dataclass
class CaptureResult: exit_code: int | None; stdout: str; stderr: str; duration_s: float; timed_out: bool
def run_capture(cmd: list[str], *, timeout_s: float, cwd: Path | None = None) -> CaptureResult   # short probes
@dataclass
class BlenderRunRequest:
    executable: Path
    script: Path
    script_args: list[str]                       # placed after "--"
    timeout_s: float
    temp_dir: Path                               # created; TEMP=TMP=temp_dir for the child
    user_resources_dir: Path | None = None       # BLENDER_USER_RESOURCES
    log_path: Path | None = None                 # full raw stdout+stderr log (utf-8)
    cwd: Path | None = None
    cancel_event: threading.Event | None = None
    on_event: Callable[[dict], None] | None = None      # each parsed protocol JSON object (any thread)
    on_output: Callable[[str, str], None] | None = None # (stream "stdout"|"stderr", line)
    extra_env: dict[str, str] | None = None
    factory_startup: bool = True
@dataclass
class BlenderRunResult:
    status: RunStatus; exit_code: int | None; duration_s: float; command: list[str]
    events: list[dict]; result: dict | None      # last {"event":"result"} payload
    errors: list[str]                            # from {"event":"error"} + traceback tail + "Error: script failed"
    stdout_tail: str; stderr_tail: str           # last ~200 lines each
    blender_version: str | None                  # from {"event":"hello"}
    log_path: Path | None; timed_out: bool; cancelled: bool
class BlenderProcessRunner:
    def run(self, req: BlenderRunRequest) -> BlenderRunResult   # blocking; never raises for process failures
```
Command: `[exe, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode", "--python-exit-code", "86", "--python", script, "--", *script_args]`.
Env: copy of os.environ minus `PYTHON*` vars and any `BLENDER_*` vars; set `TEMP`, `TMP`, `BLENDER_USER_RESOURCES`.
Windows: Job Object kill-on-close; `CREATE_SUSPENDED|CREATE_NO_WINDOW` → assign → `NtResumeProcess`; reader threads
for stdout/stderr (binary → utf-8 `errors="replace"`, strip CR); cancel/timeout → `TerminateJobObject`; always close
the job handle. Non-Windows fallback: `start_new_session=True` + `os.killpg`. Status mapping: rc 0 → succeeded unless
a protocol `error` event was emitted (→ failed); rc 86 → script_error; rc in {2,3,4} → failed (script-defined);
cancelled/timed_out by flags; rc ≥ 0xC0000000 or negative → crashed; OSError on spawn → launch_failed.
Also verify every path argument ≤ 259 chars before launch (`PathTooLongError`).

```python
# blender_downloader.py
@dataclass
class ReleaseInfo: version: str; series: str; file_name: str; url: str; size_bytes: int | None
                   sha256: str | None; checksum_url: str | None; architecture: str; is_lts: bool | None; release_date: str | None
DownloadState = Literal["idle","resolving","downloading","verifying","extracting","validating","completed","failed","cancelled"]
@dataclass
class DownloadProgress: state: DownloadState; version: str | None; file_name: str | None; url: str | None
                        bytes_done: int; bytes_total: int | None; percent: float | None; speed_bps: float | None
                        eta_s: float | None; resumed: bool; verified: bool | None; error: str | None
                        error_code: str | None; install_dir: str | None; started_at: str | None; updated_at: str
class BlenderDownloader:
    def __init__(self, settings: LauncherSettings, *, opener=None): ...   # opener injectable for tests
    def native_architecture(self) -> str               # IsWow64Process2 nativeMachine → "x64"/"arm64"
    def resolve_latest(self, *, version: str | None = None) -> ReleaseInfo
        # GET base_url → Blender{maj}.{min}/ dirs >= min → newest first → listing → exact ZIP regex for arch
        # → checksum file blender-X.Y.Z.sha256 → parse line for the zip. channel "lts" uses
        # https://www.blender.org/download/lts/ (current section) with fallback {"4.2","4.5","5.2"} for is_lts.
    def download_and_install(self, release: ReleaseInfo, *, cancel: threading.Event,
                             on_progress: Callable[[DownloadProgress], None]) -> Path   # returns blender.exe
```
Download rules: HTTPS only; exact host allowlist on the initial URL **and every redirect hop** (custom redirect
handler); explicit User-Agent; HEAD for size/ETag; stream to `downloads/<file>.part` (+ `.part.json` with url, etag,
size, sha256) in 1 MiB chunks; resume with `Range: bytes=N-` + `If-Range: <etag>` — accept only 206 whose
Content-Range start == N and total == size, 200 → truncate & restart, 416 → restart; retry transient errors
(exponential backoff, max_download_retries) resuming each time; sha256 computed over the full file (hash the
existing .part first on resume); mismatch → delete .part, ChecksumMismatchError; no official checksum → proceed
with `verified=False` + warning. Free-space check before download (zip + 1.2 GB) and before extract.
Safe extract: reject absolute paths, drive letters, `..`, NUL, backslash-escapes, symlinks (external_attr S_IFLNK),
entries outside the single top folder `^blender-\d+\.\d+\.\d+-windows-(x64|arm64)/`, > 20,000 entries, > 4 GB total
uncompressed, per-entry compression ratio > 200 with size > 10 MB; enforce `len(final install path + member) ≤ 259`;
extract into `.staging-<hex>` with cancel checks, then `os.replace` to `runtime_root/<top folder>`; if that target
already exists and validates, reuse it; write `.img3d_install.json` {version, url, sha256, verified, installed_at,
size_bytes}. Validate by executing the new exe: parsed version must equal the requested one. Delete the zip after a
successful install unless keep_zip_after_install. All cleanup paths assert containment in runtime_root.

```python
# blender_runtime.py
RuntimeState = Literal["detecting", "ready", "needs_download", "downloading", "error"]
@dataclass
class RuntimeStatus:
    state: RuntimeState; mode: RuntimeMode; min_version: str          # "5.1"
    selected: BlenderInstallation | None; selection_reason: str | None
    installations: list[BlenderInstallation]   # all, version desc
    message: str | None                        # user prompt, see policy
    warnings: list[str]; runtime_root: str; can_download: bool
    download: DownloadProgress | None; release: ReleaseInfo | None
    last_scan_at: str | None; scan_duration_ms: float | None; error: str | None
    def to_dict(self) -> dict                  # JSON-ready (snake_case)
class BlenderRuntimeManager:
    def __init__(self, settings: LauncherSettings, *, detector=None, downloader=None, runner=None,
                 on_change: Callable[[RuntimeStatus], None] | None = None): ...
    runner: BlenderProcessRunner                       # property
    def status(self) -> RuntimeStatus                  # cheap snapshot, thread-safe
    def scan(self, *, force: bool = False) -> RuntimeStatus          # blocking detect + policy + persist config
    def select(self, mode: RuntimeMode, executable: str | None = None) -> RuntimeStatus
        # validates (probe if unknown path) → BlenderNotFoundError / BlenderIncompatibleError; persists config
    def resolve_release(self, *, force: bool = False) -> ReleaseInfo  # cached 1 h
    def start_download(self, *, version: str | None = None) -> RuntimeStatus   # background thread; raises
        # DownloadsDisabledError / DownloadInProgressError; on success: rescan, and if mode=="embedded" or no
        # compatible system Blender → select the new embedded build automatically (never override a system pin)
    def cancel_download(self) -> RuntimeStatus
    def require_runtime(self) -> BlenderInstallation   # selected & compatible or BlenderUnavailableError(message)
    def close(self, timeout: float = 5.0) -> None      # cancel download, join thread
```
`on_change` is invoked (from any thread, outside locks) after every state change; download progress is throttled
to ≤ 4 calls/s. All public methods are thread-safe (one RLock; never hold it while executing Blender or doing I/O
over the network).

### Selection policy (the priority list from the requirements)
Compatible = valid ∧ version ≥ min (5.1) ∧ native-runnable arch (x64 on arm64 allowed as fallback) ∧ console exe.
Auto-selection only considers **stable** (cycle == release) compatible installs; a non-stable compatible build is
usable only when explicitly pinned (then a warning is added).
0. `override_executable` (env IMG3D_BLENDER_EXECUTABLE) if compatible → reason "Configured override".
1. mode == "embedded": pinned embedded install if compatible, else highest stable compatible embedded install.
2. mode == "system" with a pin: the pinned install if compatible.
3. Highest stable compatible **system** install (mode auto, or mode system without a usable pin).
4. Highest stable compatible **embedded** install.
5. None → state `needs_download`, `selected=None`, message:
   - if any valid system install exists but none compatible (take the highest such version V):
     `"Blender {V} is installed but Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?"`
     (exact wording; V = version label without "LTS", e.g. "4.2.17")
   - else `"No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?"`
If the user's explicit choice (1 or 2) is unusable, fall back down the list and add a warning, e.g.
"Embedded Blender is selected but not installed — using system Blender 5.2.2." (mode stays as chosen).
Example: 3.6 + 4.5 + 5.1 + 5.2 → 5.2. On this PC: auto → 5.2.2 LTS (system); 4.2.17 listed as incompatible.

## 3. Blender script CLI + protocol (contract between backend and `blender_scripts`)

Invocation: `blender.exe [runner flags] --python backend/blender_scripts/auto_mesh_tuner.py -- --config <cfg.json>`
(also accepted: `--input <file> --output-dir <dir> [--options <json-string>]` for manual use).

`cfg.json` (written by the backend/CLI; all paths absolute, ≤ 259 chars):
```json
{ "schema": 1, "input": "C:\\...\\input.glb", "output_dir": "C:\\...\\out",
  "options": { ...TuningOptions (§4), unknown keys ignored... },
  "outputs": { "model": "tuned.glb", "report": "report.json", "blend": null, "previews_dir": "previews" },
  "context": { "job_id": "job_...", "tuning_id": "tn_...", "source": "trellis2" } }
```
Protocol: one line per event on stdout: `@@AMT@@` + `json.dumps(obj, ensure_ascii=True, separators=(",",":"))`,
flushed. Parse with `line.find("@@AMT@@")`. Events (`event` key):
- `hello`  {protocol:1, script_version, blender_version:"5.2.2 LTS", python:"3.13.13", gpu: bool|null}
- `plan`   {steps:[{id,label}]}  (fixed ids, see below)
- `step`   {id, status:"running"|"done"|"warning"|"skipped"|"failed", message, duration_s, progress (overall 0..1), details:{}}
- `progress` {id, fraction (within step 0..1), progress (overall 0..1), message}
- `log`    {level:"debug"|"info"|"warning"|"error", message}
- `result` {status:"completed"|"completed_with_warnings", model, report, blend|null, previews:[{name,view,variant}],
            metrics:[Metric], warnings:[str], validation:{passed, checks:[...]}}
- `error`  {code, message, step|null, traceback|null}
Exit codes: 0 ok · 2 bad arguments/config · 3 input unreadable / no mesh · 4 processing/export failure (after an
`error` event) · 86 uncaught exception (from --python-exit-code).
Step ids (order fixed) and labels: `load` "Load mesh" · `analyze` "Mesh analysis" · `geometry_repair` "Geometry repair" ·
`face_refinement` "Face refinement" · `topology_tuning` "Topology tuning" · `rig_optimization` "Rig optimization" ·
`validation` "Validation" · `export` "Save results". Overall progress weights: load .05, analyze .07,
geometry_repair .30, face_refinement .15, topology_tuning .20, rig_optimization .05, validation .13, export .05.

Metric: `{key, label, unit|null, before|null, after|null, better:"lower"|"higher"|"neutral"}`. Keys (any subset):
vertices, faces, triangles, quads, ngons, non_manifold_edges, boundary_edges, holes, largest_hole_edges, components,
small_components, degenerate_faces, duplicate_vertices, inconsistent_normal_edges, hidden_faces, inner_shell_faces,
symmetry_error_pct, surface_area, mean_edge_length, deviation_mean_pct, deviation_p95_pct, deviation_max_pct,
uv_layers, materials, textures, armatures, max_bone_influences, unnormalized_weight_verts, pose_max_stretch.

report.json: `{schema:1, tool:"Img3D Auto Mesh Tuner", script_version, protocol:1, blender_version, created_at,
duration_s, input:{path,format,size_bytes}, options, steps:[{id,label,status,message,duration_s,details}],
metrics:[Metric], warnings:[...], validation:{passed, checks:[{id,label,status:"pass"|"warn"|"fail",value,threshold,message}]},
outputs:{model, blend, previews:[{name,view,variant}]}, environment:{gpu, gpu_renderer, python, numpy}}`.

## 4. TuningOptions (API body, cfg.options; all optional)
```
preset: "conservative" | "balanced" | "strong" = "balanced"     # sets defaults below; explicit fields win
remove_inner_shell: bool = true         # TRELLIS double-wall removal (visibility + paired-wall test)
fill_holes: bool = true; max_hole_edges: int = 300 (8..5000)
remove_islands: bool = true; island_min_ratio: float = 0.001 (0..0.05)   # fraction of total faces; hidden/enclosed tiny parts
smoothing: float = 0.5 (0..1)            # feature-preserving denoise strength (0 = off)
refine_face: bool = true; eye_correction: bool = true
symmetry: "auto" | "off" | "repair" | "symmetrize" = "auto"   # auto = local repair only if model is mirror-symmetric
refine_density: bool = true              # adaptive split of oversized/faceted triangles ("finer mesh")
quads: bool = false                      # tris→quads (only meaningful for .blend; GLB re-triangulates)
subdivision: "auto" | "off" | "modifier" = "auto"   # auto: add Subdivision Surface (viewport 1, render 2) only if faces ≤ 300k, never applied to GLB
optimize_rig: bool = true                # no-op (skipped) when there is no armature
render_previews: bool = true
save_blend: bool = false
```
Presets: conservative = smoothing .25, refine_density false, symmetry "off"→"auto" stays, max_hole_edges 120;
strong = smoothing .8, max_hole_edges 1000, island_min_ratio .003.

## 5. Blender-side package `backend/blender_scripts/img3d_amt/` (Python 3.11 syntax, numpy ≥ 1.24)
Framework (agent "framework"): `__init__.py` (__version__, PROTOCOL_VERSION=1) · `protocol.py` · `config.py`
(options/presets parsing) · `context.py` (TunerContext) · `io_mesh.py` (import glb/gltf/obj/fbx/ply/stl/blend,
export GLB keeping textures (export_image_format AUTO), materials alphaMode/doubleSided preserved, coordinate frame
unchanged, `.blend` save) · `meshdata.py` (numpy foreach_get/foreach_set helpers, CSR vertex adjacency, face
adjacency, normals/areas, edge lists, `laplacian_fair`, attribute helpers) · `analysis.py` · `pipeline.py` · `cli.py`
· `report.py` · `previews.py` (Workbench matcap/texture renders before/after: front, three_quarter, side, face).
Step modules each expose `run(ctx: TunerContext) -> StepOutcome` (`StepOutcome(status="done"|"warning"|"skipped",
message: str, details: dict)`; raise `StepError(code, message)` to fail the step; report fine progress with
`ctx.progress(fraction, message)`): `repair.py` (+ `visibility.py`, `holes.py`) · `face.py` (+ `symmetry.py`,
`smoothing.py`) · `topology.py` · `rig.py` · `validation.py`.
A failing optional step (face/topology/rig) must NOT abort the run: pipeline restores the pre-step mesh snapshot
(when cheap) or continues, marks the step `failed`, adds a warning; load/geometry_repair/export failures abort.
Cross-step data: mesh attributes prefixed `amt_` (e.g. `amt_filled` POINT FLOAT = 1 for hole-fill vertices,
`amt_face_region` POINT FLOAT) and `ctx.store`; all `amt_*` attributes are removed before export.
Orientation: +Z up, front = −Y (TRELLIS/glTF import); for other sources assume the same unless options say otherwise.

## 6. Backend service/API (agent "backend")
Settings (IMG3D_ prefix): blender_enabled=True, blender_executable: Path|None, blender_search_dirs: list[Path]=[],
blender_min_version="5.1", blender_install_dir: Path|None (None → runtime_dir/"blender"), blender_allow_download=True,
blender_download_base_url="https://download.blender.org/release/", blender_download_channel="latest",
blender_detect_on_startup=True, blender_detect_timeout_s=20, blender_tune_timeout_s=1800, blender_max_queue_size=4.
`test\conftest.py` defaults `IMG3D_BLENDER_DETECT_ON_STARTUP=false`.

REST (all under /api/v1, JSON routes AuthGuard, file routes FileAuthGuard):
| Method & path | Body → Response |
|---|---|
| GET  /blender/runtime | → BlenderRuntimeOut |
| POST /blender/runtime/detect | → BlenderRuntimeOut (force rescan, runs in a thread) |
| PUT  /blender/runtime/selection | {mode, executable?} → BlenderRuntimeOut (404 blender_not_found, 422 blender_incompatible) |
| POST /blender/runtime/release | → BlenderReleaseOut (resolve official latest; 502 blender_release_unresolved) |
| POST /blender/runtime/download | {confirm: true, version?} → 202 BlenderRuntimeOut (409 in progress, 403 disabled, 422 if confirm≠true) |
| DELETE /blender/runtime/download | → 202 BlenderRuntimeOut (cancel) |
| POST /jobs/{job_id}/tunings | TuningOptions → 202 TuningRunOut (409 job not completed, 503 blender_unavailable w/ message, 429 queue full) |
| GET  /jobs/{job_id}/tunings | → {items: TuningRunOut[]} newest first |
| GET  /tunings/{tuning_id} | → TuningRunOut |
| POST /tunings/{tuning_id}/cancel | → 202 TuningRunOut |
| DELETE /tunings/{tuning_id} | → 204 (409 if active) |
| GET  /tunings/{tuning_id}/report | → report.json |
| GET  /tunings/{tuning_id}/model.glb | file inline (model/gltf-binary) |
| GET  /tunings/{tuning_id}/export/glb | file attachment `img3d_<job>_tuned_<tn>.glb` |
| GET  /tunings/{tuning_id}/previews/{name} | image/png (name validated `^[a-z0-9_]+\.png$`) |
| GET  /tunings/{tuning_id}/log | text/plain tail (≤ 256 KB) |

BlenderRuntimeOut = RuntimeStatus.to_dict() shape: {state, mode, min_version, selected: BlenderInstallOut|null,
selection_reason, installations: BlenderInstallOut[], message, warnings[], runtime_root, can_download,
download: DownloadOut|null, release: BlenderReleaseOut|null, last_scan_at, scan_duration_ms, error}.
BlenderInstallOut = {id, executable, kind:"system"|"embedded", origins[], version, version_label, version_tuple[3],
cycle, is_lts, is_stable, architecture, valid, compatible, incompatible_reason, error, selected: bool}.
DownloadOut = DownloadProgress fields. BlenderReleaseOut = ReleaseInfo fields.
TuningRunOut = {id, job_id, status: queued|running|completed|failed|cancelled, created_at, started_at, finished_at,
options (effective), progress:{percent, step_id, step_index, step_count, message, updated_at}, steps:[{id,label,status:
pending|running|done|warning|skipped|failed, message, duration_s}], metrics: Metric[], warnings[], validation|null,
blender:{executable, version, kind}|null, result:{model_url, download_url, report_url, log_url, previews:[{name, view,
variant, url}], stats: MeshStats|null}|null, error:{code,message,details}|null}.
WebSocket: `blender.updated` (data BlenderRuntimeOut; download-progress frames droppable) and `tuning.updated`
(data TuningRunOut, Event.job_id = run.job_id; progress frames droppable, status changes not).
Tuning worker: own asyncio consumer + `ThreadPoolExecutor(1, "blender-worker")` (never the GPU worker); copies the
job's model.glb into `runtime/temp/tuning/<tn>/input.glb`; runs Blender via the launcher runner; on success moves
outputs into `runtime/jobs/<job>/tuning/<tn>/` (re-checks the job still exists); cancel kills the tree; startup marks
interrupted runs failed (`interrupted`); shutdown cancels + kills. Deleting a job cancels its active tunings first.

## 7. Frontend (agent "frontend")
Header pill `BlenderRuntimeStatus` (EngineStatus-style popover) → state colours: ready / needs_download (warning) /
downloading (progress) / error. `BlenderSetupDialog` (native `<dialog>`): detected versions table (version, LTS,
kind System/Embedded, path, compatibility badge + reason, "selected" marker), selected runtime card (kind, path,
version, compatibility status, selection reason, warnings), `SegmentedControl` **Use System Blender | Use Embedded
Blender | Auto**, **Change Runtime** (pick a specific install → PUT selection with executable), **Re-detect**,
download prompt with the backend's exact `message` + release info (version, size, source host, sha256) and explicit
confirm, live download progress (bytes, %, speed, ETA, state: verifying/extracting) + Cancel, and an **Auto Tune**
button (enabled when runtime ready and a completed model is viewed). Auto-open the dialog once per browser profile
when state == needs_download (remember dismissal per message in localStorage). `TuningCard` (right column / mobile
viewer tab): preset + options, Auto Tune, live step checklist (8 steps, the screenshot's "Mesh Analysis / Geometry
Repair / Face Refinement / Topology Tuning / Rig Optimization / Validation" flow), Cancel, report (metrics
before/after table, validation checks, warnings, preview thumbnails), Download tuned GLB. Viewer: Original | Tuned
toggle (both models mounted in one canvas, tuned aligned with the original's layout offset), tuned model stats.
WS events `blender.updated`, `tuning.updated`; REST is the source of truth on (re)connect.

## Deviations
(none yet)
