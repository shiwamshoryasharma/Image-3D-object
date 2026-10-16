## Launcher report: Blender runtime package (`backend/launcher/`)

The package is built to DESIGN.md §0–§2 using only the standard library, with no backend or FastAPI imports. All 124 tests pass, including the opt-in network test.

### Files created
- **Package:** `E:/14-OCT-2026/Img3D/backend/launcher/`
  - `__init__.py`, `errors.py`, `config.py`, `blender_version.py`, `platform_win.py`
  - `blender_detector.py`, `blender_downloader.py`, `process_runner.py`, `blender_runtime.py`
- **Tests:** `E:/14-OCT-2026/Img3D/test/launcher/`
  - `conftest.py` registers the `blender` and `network` markers. Network tests are skipped unless you pass `-m network` or set `IMG3D_RUN_NETWORK_TESTS=1`.
  - `lt_helpers.py` holds the fakes, the local HTTP server and the ZIP builders.
  - `blender_side/amt_probe.py` is the test-only script that runs inside Blender.
  - Eight test files: `test_launcher_{version,runtime_policy,detector,downloader,process_runner,real_detection,platform,network}.py`.
- **Test artifacts:** `E:/14-OCT-2026/Img3D/test/outputs/mesh_tuner/launcher/<module>/<test>/`

### Public API
`__init__` re-exports every name in DESIGN §2. Extras beyond the contract:
- **Settings:** `LauncherSettings.require_https=True` and `min_version_label`. `min_version` also accepts a string such as `"5.1"`.
- **Detector:** constructor takes `runner`, `pe_reader`, `native_arch`, `sources` (subset of `SOURCE_NAMES`) and `use_cache`. Adds `candidate_groups()`, plus `subsystem` and `emulated` fields on each installation.
- **Downloader:** constructor takes `opener`, `validator(exe, release)`, `runner`, `arch` and `retry_backoff_s`.
  - `make_opener(settings, proxies=)` builds the HTTP opener and checks the host allowlist on every redirect hop.
  - `open_url()` is the HTTP layer; `inspect_archive()` does the safety checks.
- **Runner:** `parse_protocol_line()` is exported. `BlenderRunResult.pid` is added. `run_capture` is the short probe used for `--version`.
- **Manager:** `select_runtime()` is the policy as a pure function. `needs_download_message()`, `config()`, and the `detector` / `downloader` properties are also exposed.

### Testing
Command: `E:/14-OCT-2026/Img3D/backend/python/python.exe -s -m pytest -c E:/14-OCT-2026/Img3D/test/pytest.ini E:/14-OCT-2026/Img3D/test/launcher -q -p no:cacheprovider`

- **Default run:** 123 passed, 1 skipped (network), about 28 s. I ran it three times with no failures.
- **With `IMG3D_RUN_NETWORK_TESTS=1`:** 124 passed in 31.5 s.
- **By marker:** 106 pure-Python, 17 real-Blender, 1 network.

| Area | Tests | What is covered |
|---|---|---|
| Version parsing | 22 | All 17 research samples, sort order, `parse_min_version` |
| Selection policy | 27 | Every policy case in the task (exact 4.2.17 and no-install messages), config persistence and corrupt config, `select()` validation, manager download/cancel/failure flows, progress notifications held to 4 per second or fewer |
| Detector | 9 | Each env var pointed at temp dirs; launcher mapped to `blender.exe`; dedupe including a scoop `current` junction; missing files dropped; folders without an exe ignored; winget depth limit; probe cache reused, re-probed on change or `force`, stale entries pruned |
| Downloader | 41 | Local HTTP server: full install, resume after a dropped connection (Range used, no byte sent twice), cancel keeps `.part` then resumes, If-Range mismatch and 416 restart from 0, checksum mismatch, missing checksum, both redirect cases, HTTP refused, User-Agent sent, low disk space, 14 kinds of unsafe archive, reuse of a valid install, containment guard, resolve from a local index, manager end to end |
| Process runner (real 5.2) | 12 | Protocol events found even when Blender's own output is glued onto them; exit 0, 3, 86 and error-event statuses; timeout and cancel |
| Real detection | 5 | This PC's installs (results below) |
| Platform helpers | 7 | PE header, architecture check, registry readers, Job Object terminate and close |
| Network | 1 | Live resolve, HEAD and Range request |

**Kill-tree and environment checks (process runner):**
- Timeout and cancel leave no orphans. psutil confirms blender.exe, conhost.exe and the grandchild python.exe are all gone; exit code is 0xC000013A. The timeout test kills at 8.0 s.
- Non-ASCII text survives the round trip.
- The child's `TEMP` and `TMP` equal the run's temp dir. `BLENDER_USER_RESOURCES` is set, and no other `PYTHON*` or `BLENDER_*` variable reaches Blender.
- The log file is written. A path over 259 characters raises `PathTooLongError` before launch.

**Measurements:**
- **Real detection:** 5.2.2 LTS is found as compatible, system, x64. 4.2.17 is valid but flagged "Blender 4.2.17 is older than 5.1". The 4.4/4.5 folders are ignored and auto-selection picks 5.2.2.
- **Detection speed:** cold detect takes 0.24 s (0.95 s on the very first process start); warm (cached) takes 75 ms.
- **Per-source cost:** registry 25 ms, PATH 43 ms, everything else under 1.2 ms. Each version probe takes about 140 ms, run in parallel.
- **Blender run:** about 1.4 s.
- **Network:** resolves to 5.2.2 x64, size 404453484, sha256 `3849d17a…0679b535`, LTS true. HEAD returns 200 with ETag `"6aa90cd8-181b786c"`, and `bytes=0-1023` returns 206 with a ZIP header. Pinning `version="5.1"` resolves to 5.1.2.

### Known limitations
- The non-Windows fallbacks (`killpg`, `platform.machine`) are untested.
- The registry, Steam and Store sources were only exercised against this PC's real registry. Every other source was tested with fake directories.
- The full 400 MB download and extraction of the official ZIP were not run, as instructed. The path-length check covers it (the longest entry is 168 characters).
- Mirror redirects are refused unless the mirror host is in the allowlist.
- During `extracting`, the `percent` field reports extraction progress instead of download progress.

### Deviations from DESIGN.md (please record in its Deviations section)
1. **Third prompt message.** When only alpha/beta/RC compatible builds exist, the message is "Blender 5.3.0 Beta is installed but is not a stable release. Auto Mesh Tuner requires a stable Blender 5.1 or newer. Download a supported portable Blender?" Neither design message fits that case.
2. **Downloads disabled.** With `allow_download=False`, the message ending becomes "Install Blender 5.1 or newer and detect again."
3. **`select()` with a path.**
   - `select("auto", executable)` switches the mode to that install's kind (system or embedded) and pins it.
   - An explicit mode that doesn't match the install's kind raises `BlenderIncompatibleError`.
4. **Unrunnable executables.** GUI-subsystem, wrong-architecture or non-PE files are not executed. They are reported as `valid=False` with `incompatible_reason="Not a working Blender executable"` and the detail in `error`.
5. **Path too long during extraction.** If an extracted path would exceed 259 characters, the error is `PathTooLongError` (`blender_path_too_long`) rather than `UnsafeArchiveError`, because the cause is a long install folder.
6. **Additions and conventions.**
   - Extra fields: `BlenderInstallation.to_dict()` also has `build_branch`, `subsystem`, `emulated`, `probe_ms`; `ReleaseInfo.to_dict()` adds `host`.
   - `BlenderRunResult.result` is the whole last result event (it keeps the `"event"` key).
   - A run with no `cwd` uses `temp_dir` as its working directory.
   - The log file is line-buffered so it can be tailed live.
   - Two extra error codes are used on `LauncherError`: `launcher_unsafe_path` and `blender_invalid_mode`.
7. **Cross-process install lock.** `downloads/.install.lock` raises `DownloadInProgressError` if another process is already installing. Stale `.staging-*` folders are cleaned while the lock is held.
8. **Probe cache and LTS list.** The cache stores only successful probes. The LTS series list is scraped for both channels and merged with the fallback `{4.2, 4.5, 5.2}`.

### Requests for other areas
- **backend:**
  - Map launcher codes to `AppError`, including the two new ones. Suggested: `launcher_unsafe_path` → 500, `blender_invalid_mode` → 422, `blender_path_too_long` → 422.
  - Build `LauncherSettings(runtime_root=runtime_dir/"blender", temp_root=runtime_dir/"temp"/"blender", min_version=settings.blender_min_version, …)`.
  - Call `manager.close()` at shutdown and forward `on_change` to the `blender.updated` WebSocket event.
  - The response schemas need to allow the extra keys listed above.
- **frontend:** the prompt `message` can now take the third variant from deviation 1, and `release.host` is available to show the source host.
- **framework / CLI:** use `BlenderProcessRunner().run(BlenderRunRequest(...))`. `parse_protocol_line` is exported. A tuning run's exit codes 2, 3 or 4 map to `failed`.