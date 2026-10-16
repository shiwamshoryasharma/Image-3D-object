# Official Blender portable ZIP distribution for Windows: research report (as of 2026-10-15)

Everything below was measured live with HEAD requests and small Range requests. No full ZIP was downloaded.

Probe scripts:
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\zip_cd_probe.py` reads a ZIP's end-of-central-directory and central directory using Range requests only.
- `E:\14-OCT-2026\Img3D\test\mesh_tuner\probe\httpx_probe.py` checks httpx 0.28.1 under the embedded Python, run with `-s`.

Raw captures are in `E:\14-OCT-2026\Img3D\test\outputs\mesh_tuner\dl_probe\`: the index HTML files, response headers, `blender-5.2.2.sha256` and `.md5`, the builder JSON files, `x64_5.2.2\report.json` and `x64_5.2.2\zip_names.txt` (7500 entries). I deleted `mirrorlist.html` because the page includes the client's IP address and location.

No existing project source was modified.

## 1. Release index format (`https://download.blender.org/release/`)
- It is a plain nginx autoindex served behind Cloudflare (`Server: cloudflare`, `Content-Type: text/html`, chunked).
  - Headers: `Cache-Control: max-age=14400, must-revalidate`. `last-modified` is the time of the request, so the page is generated on each fetch.
- Line format:
  ```
  <a href="Blender5.2/">Blender5.2/</a>                                        15-Sep-2026 09:21                   -
  <a href="blender-5.2.2-windows-x64.zip">blender-5.2.2-windows-x64.zip</a>                      15-Sep-2026 09:16           404453484
  ```
  - Dates are `DD-Mon-YYYY HH:MM` in UTC. Sizes are exact byte counts. Directories show `-` as the size.
- The 5.x directories are `Blender5.0/`, `Blender5.1/` and `Blender5.2/`. `Blender5.3/` returns 404 (5.3 exists only as a beta on builder).
- A URL without the trailing slash gets `301` to the same host with the slash added (`/release/Blender5.2` → `/release/Blender5.2/`).
- Some entries break a naive pattern, so the regex must be strict:
  - `Blender2.28a/`, `Blender2.27-newpy/`, `BlenderBenchmark2.0/`, `plugin/`, `trace`
  - `Blender2.83/archive/`
  - `blender-2.80rc1-*`: release candidates have appeared under `/release/` before, though none exist for 5.x.
  - `Blender4.2/add-ons-legacy-bundle.zip`

Regexes:
```
DIR_RE  = r'<a href="Blender(?P<maj>\d+)\.(?P<min>\d+)/">'
ROW_RE  = r'<a href="(?P<name>[^"/]+)">[^<]*</a>\s+(?P<date>\d{2}-[A-Z][a-z]{2}-\d{4} \d{2}:\d{2})\s+(?P<size>\d+)'
ZIP_RE  = r'^blender-(?P<ver>(?P<maj>\d+)\.(?P<min>\d+)\.(?P<pat>\d+))-windows-(?P<arch>x64|arm64)\.zip$'
SUM_RE  = r'^blender-(?P<ver>\d+\.\d+\.\d+)\.(?P<algo>sha256|md5)$'
```
The `$` anchors in `ZIP_RE` and `SUM_RE` are what reject rc, alpha and beta builds and stray files.

## 2. Files in each 5.x directory
Every patch release has the same 10 files:
- `-linux-x64.tar.xz`
- `-macos-arm64.dmg` (there is no `macos-x64` build in 5.x)
- `-windows-{x64,arm64}.{msi,msix,zip}`
- `blender-X.Y.Z.md5` (521 B)
- `blender-X.Y.Z.sha256` (777 B)

None of the 5.x directories contain alpha, beta or rc files.

| ZIP | x64 bytes | arm64 bytes | date (UTC) |
|---|---|---|---|
| 5.0.0 | 413902772 | 263762472 | 18-Nov-2025 |
| 5.0.1 | 401385182 | 263726173 | 16-Dec-2025 |
| 5.1.0 | 414263332 | 267048552 | 17-Mar-2026 |
| 5.1.1 | 414370237 | 267140499 | 14-Apr-2026 |
| 5.1.2 | 414378949 | 267204476 | 19-May-2026 |
| 5.2.0 | 404954661 | 274036838 | 14-Jul-2026 |
| 5.2.1 | 404851964 | 274046853 | 25-Aug-2026 |
| 5.2.2 | 404453484 | 274036892 | 15-Sep-2026 |

**Checksum file format.** `https://download.blender.org/release/Blender5.2/blender-5.2.2.sha256` is served as `text/plain`, 777 bytes. It uses the GNU `sha256sum` layout:
- 8 lines, each `<64 lowercase hex><2 spaces><filename>`.
- LF line endings, with a final LF.
- No `*` binary marker and no path in the filename.
- Line order: macos-arm64.dmg, linux-x64.tar.xz, windows-x64 .msi/.msix/.zip, windows-arm64 .msi/.msix/.zip.
- Both Windows ZIPs are listed in every 5.x checksum file I checked (5.0.1, 5.1.2, 5.2.0, 5.2.2).
- The `.md5` file has the same layout with 32 hex characters per hash.

Line regex: `^(?P<sha>[0-9a-f]{64})  (?P<name>[A-Za-z0-9._+-]+)$`

Hashes for the newest ZIPs:
- `blender-5.2.2-windows-x64.zip`: `3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535`
- `blender-5.2.2-windows-arm64.zip`: `2e90b53c9443cc56d2be75a61550a38e4aba942c8cded046ceff98ed58ebc67b`
- `blender-5.1.2-windows-x64.zip`: `345bedea7b0acf7cc9666423d8553f9129622aea34ded65c23e8cb70f83f14ff`
- `blender-5.1.2-windows-arm64.zip`: `91d39ca72ed4862724eb4398ccfc11913e0650654175efed72023efd7748960e`

The mirror redirector gives the same hash for 5.2.2 x64 as plain text: `https://mirror.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip?sha256` → `3849d17a…b535  blender-5.2.2-windows-x64.zip`. `?sha1` also works. `?md5` returns 404 "Hash type not supported".

## 3. Latest stable release and LTS status
- **The latest stable release that is 5.1 or newer is 5.2.2, released 2026-09-15, and it is LTS.** The latest 5.1 release is 5.1.2, which is not LTS.
- Where LTS is stated officially:
  - **`https://www.blender.org/download/lts/` (HTML only):**
    - Current LTS series:
      - "Blender 5.2 LTS | Last updated to 5.2.2 on September 15, 2026"
      - "Blender 4.5 LTS | Last updated to 4.5.14 …"
    - Listed under "Previous LTS Releases": 4.2 (last 4.2.23, July 2026), 3.6, 3.3, 2.93, 2.83.
    - Scrape regex: `Blender (\d+)\.(\d+) LTS`, applied only to text before "Previous LTS Releases".
  - **`https://www.blender.org/download/`:**
    - Heading `<h1>Blender 5.2.2 LTS</h1>`.
    - `<li>v5.2.2 LTS</li>` for each platform.
    - Checksum links point to `download.blender.org/.../blender-5.2.2.{md5,sha256}`.
  - **The local binary:**
    - `blender.exe --version` prints `Blender 5.2.2 LTS` (CRLF line endings), followed by tab-indented lines such as `build hash: d13f752e3b9c` and `build branch: blender-v5.2-release`.
    - `bpy.app.version_string` returns `'5.2.2 LTS'`. `bpy.app.version_cycle` returns `'release'`. The embedded Python is 3.13.13.
    - **Parsing hazard:** 4.2.17 writes `TBBmalloc: skip allocation functions replacement…` before the version line. The user's command dropped it with `2>$null`, which suggests it goes to stderr, but merged output puts it first. Never take the first line blindly. Search all lines with `^Blender (\d+)\.(\d+)\.(\d+)(?: (Alpha|Beta|Release Candidate))?( LTS)?\s*$`.
    - The only suffixes seen here are " LTS" and none. "Alpha", "Beta" and "Release Candidate" come from my knowledge of the Blender source and were not observed.
  - **No machine-readable LTS flag exists.** Use the binary's suffix once a build is installed. Before downloading, scrape the LTS page and fall back to a hardcoded set `{"5.2"}`.
- **How blender.org links the ZIP:**
  - The download page links to `https://www.blender.org/download/release/Blender5.2/blender-5.2.2-windows-x64.zip/`. That is an HTML "thank you" page returning 200, not a redirect.
  - It carries the header `Refresh: 1;url=https://mirror.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip`, a "Direct link" anchor to the same URL, and a `?mirrorlist` link.
  - `mirror.blender.org` runs Mirrorbits v0.6.1. A HEAD returns `302` with `Location: https://mirror.freedif.org/blender/...` for this geography, plus `link: <https://mirror.nju.edu.cn/blender/...>; rel=duplicate; pri=1; geo=cn`.
  - The mirror list has 14 hosts, including freedif.org, nju.edu.cn, twds.com.tw, tsinghua, download.blender.org, dotsrc, rwth-aachen, nluug, clarkson, xenyth, iu13, ocf.berkeley, fcix and purdue. **The target depends on where the client is and can change at any time.**
  - The mirror I followed (freedif, nginx) returned the same Content-Length and ETag `"6aa90cd8-181b786c"` and supported Range with 206.

## 4. HTTP behaviour of `download.blender.org` for the ZIP
- **HEAD:**
  - `200` with no redirect. Served by the Cloudflare cache (`cf-cache-status: HIT`, `Age: 2581326`).
  - `Content-Type: application/zip`, `Content-Length: 404453484`, `Accept-Ranges: bytes`.
  - `etag: "6aa90cd8-181b786c"`. This is nginx's format: hex mtime (2026-09-15 09:16:08 UTC) and hex size (0x181b786c = 404453484).
  - `last-modified: Tue, 15 Sep 2026 09:16:08 GMT`, `Cache-Control: public, max-age=2592000`.
  - HSTS is set with `max-age=63072000; includeSubDomains; preload`.
- **Range and conditional requests:**

| Request | Result |
|---|---|
| `Range: bytes=0-1023` | `206`, `content-range: bytes 0-1023/404453484` |
| Suffix range `bytes=-65536` | `206` |
| Multiple ranges | `206 multipart/byteranges` |
| Range starting at or past the end | `416`, `content-range: bytes */404453484` |
| `If-Range` with the matching ETag | `206` |
| `If-Range` with the Last-Modified date | `206` |
| `If-Range` with a wrong ETag | `200` and the full body. My client aborted after the headers; a downloader must handle this as "restart from 0". |
| `If-None-Match` with the ETag | `304` |

- `http://` returns `301` to `https://` on the same host.
- **TLS:** I checked with Python ssl. `download.blender.org`, `www.blender.org`, `mirror.blender.org` and `builder.blender.org` all negotiate TLS 1.3 with ALPN `h2` and Let's Encrypt certificates. curl (Schannel build) and httpx (default settings) used HTTP/1.1.
- **User-Agent filtering (important):** Cloudflare returns **403 to Python's default `Python-urllib/3.x` User-Agent** on `download.blender.org` (index and files) and `mirror.blender.org`.
  - These return 200: `python-httpx/0.28.1`, `python-requests/…`, `curl/…`, `Img3D/0.1 (+local app)`, and an empty UA sent with curl.
  - `builder.blender.org` accepts every UA tested.
  - Always send an explicit UA such as `Img3D-BlenderRuntime/<ver>`.
  - The embedded Python already has `httpx` (0.28.1), `requests`, `urllib3` and `certifi`.
  - httpx does **not** follow redirects by default, so pass `follow_redirects=True` only where redirects are expected.
- I could not test how a Cloudflare cache MISS treats Range requests. Treat any `200` reply to a Range request as "restart from 0".

## 5. Machine-readable JSON
- `https://builder.blender.org/download/daily/?format=json&v=1` returns `application/json`: a list of 147 entries, all hosted on `cdn.builder.blender.org`.
  - Fields: `url`, `app`, `version`, `risk_id`, `branch`, `patch`, `hash`, `platform`, `architecture`, `bitness`, `file_mtime`, `file_name`, `file_size`, `file_extension`, `release_cycle`.
  - `hash` is the **git commit**, not a checksum. Checksums are separate entries with `file_extension: "sha256"`; each `.sha256` file is a bare 64 hex characters with no filename and no newline.
  - `platform` is `windows`; `architecture` is `amd64` or `arm64`.
  - Names look like `blender-5.1.2-stable+v51.ec6e62d40fa9-windows.amd64-release.zip`.
  - `release_cycle` is one of `alpha`, `beta`, `candidate`, `stable`.
  - Current content: 5.1.2 stable, 5.2.3 candidate, 5.3.0 beta, 5.4.0 alpha. **5.2.2 is missing.**
- `…/download/daily/archive/?format=json&v=1` has 1654 entries and includes `5.2.2-stable+v52.d13f752e3b9c`.
- **These are rebuilds, not the official release files.**
  - The builder 5.2.2 x64 ZIP is 404983867 bytes with mtime 2026-10-14. The release ZIP is 404453484 bytes.
  - Builder 5.1.2 is 414872393 bytes with mtime 2026-06-03. The release ZIP is 414378949 bytes, and the sha256 differs.
  - Same commit, different files.
- **There is no official JSON for stable releases.** Use the `/release/` autoindex plus the `.sha256` files. Do not use builder JSON for the portable runtime.

## 6. Windows arm64
- Naming is `blender-X.Y.Z-windows-arm64.zip` (`.msi` and `.msix` also exist). The x64 build is `windows-x64`.
- Every 5.0.0 to 5.2.2 release has an arm64 build of about 267 to 274 MB, and each one is listed in its `.sha256` file.
- Before 5.x: `Blender4.2/` has no arm64 files; arm64 builds start with `blender-4.5.0-windows-arm64.zip`.
- To detect the architecture, map `platform.machine()` as `AMD64` → `x64` and `ARM64` → `arm64`. An x64 Python running under emulation on an ARM device reports `AMD64`; `IsWow64Process2` gives the true native architecture.
- The 5.x x64 builds are compiled with `-march=x86-64-v2` (seen in the 5.2.2 `--version` flags), so the CPU needs SSE4.2-class support. `blender_cpu_check.dll` ships in the ZIP root.

## 7. Inside the ZIP (5.2.2 x64)
I read the last 64 KiB and the 1.19 MB central directory using Range requests, about 0.3% of the file.
- No ZIP64 records. 7500 entries, of which 975 are directories. Central directory offset 403208041, size 1245421, no archive comment.
- Compression methods are 0 (stored, 1336 entries) and 8 (deflate, 6164 entries), so the stdlib `zipfile` module can extract it.
- Uncompressed size is about 943.6 MB. Peak disk use with the ZIP still present is about 1.35 GB.
- **A single top-level folder named `blender-<ver>-windows-<arch>/`** (also confirmed for the 5.2.2 arm64, 5.1.2 x64 and 5.0.1 x64 ZIPs).
- The executable is `blender-5.2.2-windows-x64/blender.exe`, next to `blender-launcher.exe`, `python313.dll`, `blender_cpu_check.dll` and the `*.cmd` launch scripts.
  - Root subfolders: `5.2/` (contains `datafiles`, `extensions`, `python`, `scripts`), `blender.crt/`, `blender.shared/`, `license/`.
  - Bundled Python is at `5.2/python/bin/python.exe`.
- **The longest entry path is 168 characters:**
  `.../5.2/python/lib/site-packages/pkg_resources/tests/data/my-test-package_unpacked-egg/.../dependency_links.txt`
  To stay under Windows MAX_PATH (260), keep the extraction directory to 85 characters or fewer, or check LongPathsEnabled first.
  - Example: `E:\14-OCT-2026\Img3D\runtime\blender` is 36 characters, giving 205 total.
- Entry names have no `..`, no absolute paths, no backslashes and no non-ASCII characters. Still apply a zip-slip guard.
- From the 4.2 release notes (not verified here): portable user configuration lives in a `portable\` folder next to `blender.exe`. For background runs, `--factory-startup` together with the `BLENDER_USER_*` environment variables is the safer way to isolate settings.

## Recommended host allowlist
HTTPS only, with an exact host match on every request and on every redirect hop.
- **`download.blender.org`**: index, ZIP and checksums. This is the primary and default host. File requests are served without redirects; the only redirect seen is the slash-adding 301 on a directory URL, which stays on the same host.
- **`www.blender.org`**: optional, only to scrape `/download/lts/`.
- **`mirror.blender.org`**: optional, only for the `?sha256` cross-check. Do not follow its 302 by default, because the targets are third-party, depend on location and change over time. If mirrors are ever enabled, keep the 14 mirror hosts above as an explicit list and still verify the sha256 obtained from `download.blender.org`.
- **Excluded:** `builder.blender.org` and `cdn.builder.blender.org`. They host daily and rebuilt builds whose checksums do not match the official release files.

## Recommended resolution algorithm
1. **Local scan.** Find candidate `blender.exe` files and run `--version` (timeout 15 s, both output streams captured). Parse with the version regex above.
   - Accept only builds with no Alpha, Beta or Release Candidate suffix and with (major, minor) ≥ (5, 1).
   - Record whether the suffix includes LTS.
   - On this PC: 5.2.2 LTS is eligible, 4.2.17 LTS is rejected, and `Blender 4.4` and `Blender 4.5` have no exe.
2. **Remote listing** (only if no local build qualifies or the user asks to change runtime).
   - GET `/release/` and apply `DIR_RE`, keeping (maj, min) ≥ (5, 1).
   - For each directory, newest first, GET the listing and apply `ROW_RE` + `ZIP_RE` with `arch = native_arch()`.
   - Take the highest `(maj, min, pat)` per series. Require that `blender-<ver>.sha256` exists and contains a line for the chosen ZIP name.
3. **LTS rule.**
   - The LTS set is the scrape of `/download/lts/` (current section only) merged with the fallback `{"5.2"}`.
   - Default channel "lts": choose the highest series in that set that is ≥ 5.1. If none qualifies, choose the highest stable series.
   - Channel "latest": choose the highest stable series.
   - Today both channels resolve to `Blender5.2/blender-5.2.2-windows-{x64|arm64}.zip`.
4. **Download.**
   - Send `User-Agent: Img3D-BlenderRuntime/x`.
   - HEAD the ZIP and record Content-Length and ETag. Stream to `<dest>.part`.
   - To resume, send `Range: bytes=N-` with `If-Range: <etag>`.
     - Accept the response only if it is `206` and its `Content-Range` start equals N and its total equals Content-Length.
     - On `200`, truncate and restart. On `416`, delete the `.part` file and restart.
   - Use a 1 MiB chunk size and report progress as bytes / Content-Length.
5. **Verify.** The streamed sha256 must equal the hash from `blender-<ver>.sha256`. Optionally cross-check against mirrorbits `?sha256`. On a mismatch, delete the file and fail.
6. **Extract.**
   - Check free space (at least 1.0 GB on top of the ZIP) and the path-length limit.
   - Reject unsafe entries (zip-slip).
   - Extract into `runtime\blender\_staging`, require exactly one top folder matching `^blender-\d+\.\d+\.\d+-windows-(x64|arm64)/$`, then rename atomically.
7. **Validate.** Run `<dir>\blender.exe --version`. The parsed version must match the requested one, and the LTS suffix must agree with step 3. Then run a `--factory-startup --background --python-expr` smoke test.