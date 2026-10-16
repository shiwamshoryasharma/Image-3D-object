@echo off
rem ===========================================================================
rem  Img3D - build and install the native CUDA extensions required by TRELLIS.2
rem  into the embedded Python (backend\python\python.exe).
rem
rem    cumesh      backend\third_party\extensions\CuMesh
rem    flex_gemm   backend\third_party\extensions\FlexGEMM   (needs triton-windows at runtime)
rem    nvdiffrast  backend\third_party\extensions\nvdiffrast (v0.4.0)
rem    o_voxel     backend\third_party\TRELLIS.2\o-voxel     (imports the three above)
rem
rem  Usage:
rem    build_extensions.cmd                       build + install all four
rem    build_extensions.cmd cumesh flex_gemm      build + install only the named ones
rem    build_extensions.cmd all --if-missing      skip extensions that already import
rem  Extensions are always built once each, in the dependency order
rem  cumesh, flex_gemm, nvdiffrast, o_voxel - whatever the argument order.
rem
rem  Toolchain (no system CUDA install required):
rem    * MSVC x64 from the newest Visual Studio found by vswhere (default toolset).
rem      Proven combination: MSVC 14.51 (VS 2026) + nvcc 13.4.
rem    * CUDA_HOME = backend\.toolchain\cuda\nvidia\cu13 (CUDA 13.4 nvcc assembled
rem      from NVIDIA pip wheels, see .claude\skills\img3d-environment\SKILL.md).
rem    * torch 2.14 cpp_extension compiles with -std=c++20 / /std:c++20.
rem
rem  Optional environment overrides:
rem    IMG3D_VCVARS64        full path to vcvars64.bat (skips vswhere discovery)
rem    IMG3D_VCVARS_VER      MSVC toolset version, passed as -vcvars_ver=...
rem    IMG3D_CUDA_HOME       alternative CUDA_HOME
rem    TORCH_CUDA_ARCH_LIST  default 12.0 (RTX 50xx / sm_120)
rem    MAX_JOBS              parallel compile jobs, default NUMBER_OF_PROCESSORS/2
rem
rem  Source patches required by this toolchain live in backend\third_party\patches
rem  and are documented in backend\third_party\PATCHES.md. They are applied
rem  idempotently (via git apply) before each build.
rem
rem  Builds are in-tree (setuptools "build\" dir inside each source tree), so a
rem  re-run only recompiles what changed (ninja). An extension whose installed
rem  binaries are loaded by a running process (e.g. the backend) is not touched:
rem  stop that process first. The exit code is non-zero if any requested
rem  extension fails to build, install or import.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..") do set "BACKEND=%%~fI"
set "PY=%BACKEND%\python\python.exe"
set "THIRD_PARTY=%BACKEND%\third_party"
set "PATCH_DIR=%THIRD_PARTY%\patches"
set "ALL_EXTS=cumesh flex_gemm nvdiffrast o_voxel"
set "VSINSTALLER_DIR=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
set "NOW_CODE=import time; print(int(time.time()))"
rem Lists the installed .pyd files of distribution argv[1] that cannot be opened for exclusive
rem write access (i.e. are loaded by a running process) and exits 3 if there are any.
rem Reinstalling over loaded binaries fails (or leaves "~xxx" stash dirs in site-packages).
rem NOTE: delayed expansion is on - the code must not contain an exclamation mark.
set "INUSE_CODE=import sys, ctypes, ctypes.wintypes as w, importlib.metadata as m; k = ctypes.WinDLL('kernel32', use_last_error=True); k.CreateFileW.restype = w.HANDLE; k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]; k.CloseHandle.argtypes = [w.HANDLE]; bad = w.HANDLE(-1).value; files = [str(d.locate_file(f)) for d in m.distributions(name=sys.argv[1]) for f in (d.files or []) if str(f).lower().endswith('.pyd')]; hs = [(p, k.CreateFileW(p, 0x40000000, 0, None, 3, 0, None), ctypes.get_last_error()) for p in files]; busy = [p for p, h, e in hs if h in (None, bad) and e not in (2, 3)]; [k.CloseHandle(h) for p, h, e in hs if h not in (None, bad)]; [print('[build]   in use:', p) for p in busy]; sys.exit(3 if busy else 0)"

rem ---------------------------------------------------------------- arguments
rem Requested extensions are flagged (WANT_<name>) and then built in the dependency
rem order of ALL_EXTS (o_voxel imports the other three), each at most once,
rem regardless of the order or repetition of the arguments.
set "TARGETS="
set "IF_MISSING=0"
for %%E in (%ALL_EXTS%) do set "WANT_%%E="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
if /i "%~1"=="--if-missing" (
    set "IF_MISSING=1"
    goto next_arg
)
if /i "%~1"=="all" (
    for %%E in (%ALL_EXTS%) do set "WANT_%%E=1"
    goto next_arg
)
set "KNOWN=0"
for %%E in (%ALL_EXTS%) do if /i "%%E"=="%~1" (
    set "KNOWN=1"
    set "WANT_%%E=1"
)
if "%KNOWN%"=="0" (
    echo [build] ERROR: unknown extension "%~1". Valid: %ALL_EXTS% or all
    exit /b 2
)
:next_arg
shift
goto parse_args
:args_done
for %%E in (%ALL_EXTS%) do if defined WANT_%%E set "TARGETS=!TARGETS! %%E"
if not defined TARGETS set "TARGETS= %ALL_EXTS%"

rem ------------------------------------------------------------ sanity checks
if not exist "%PY%" (
    echo [build] ERROR: embedded Python not found: "%PY%"
    exit /b 1
)
set "CUDA_HOME=%BACKEND%\.toolchain\cuda\nvidia\cu13"
if defined IMG3D_CUDA_HOME set "CUDA_HOME=%IMG3D_CUDA_HOME%"
if not exist "%CUDA_HOME%\bin\nvcc.exe" (
    echo [build] ERROR: nvcc not found under CUDA_HOME="%CUDA_HOME%"
    exit /b 1
)

rem ------------------------------------------------------------ MSVC (vcvars)
rem VS 2026's vcvars calls vswhere.exe from PATH; make it resolvable.
if exist "%VSINSTALLER_DIR%\vswhere.exe" set "PATH=%VSINSTALLER_DIR%;%PATH%"

if /i not "%VSCMD_ARG_TGT_ARCH%"=="x64" goto find_vcvars
if not defined VCToolsInstallDir goto find_vcvars
echo [build] reusing the active MSVC x64 environment
goto msvc_ready

:find_vcvars
set "VCVARS=%IMG3D_VCVARS64%"
if defined VCVARS goto have_vcvars
set "VSINSTALL="
where vswhere.exe >nul 2>&1
if errorlevel 1 goto no_vs
for /f "usebackq delims=" %%P in (`vswhere.exe -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%P"
if not defined VSINSTALL goto no_vs
set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
:have_vcvars
if not exist "%VCVARS%" (
    echo [build] ERROR: vcvars64.bat not found: "%VCVARS%"
    exit /b 1
)
if defined IMG3D_VCVARS_VER (
    call "%VCVARS%" -vcvars_ver=%IMG3D_VCVARS_VER% >nul
) else (
    call "%VCVARS%" >nul
)
where cl.exe >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: cl.exe is not on PATH after calling "%VCVARS%"
    exit /b 1
)
goto msvc_ready
:no_vs
echo [build] ERROR: no Visual Studio with C++ x64 tools found; set IMG3D_VCVARS64.
exit /b 1
:msvc_ready

rem ------------------------------------------------------- build environment
set "CUDA_PATH=%CUDA_HOME%"
set "PATH=%CUDA_HOME%\bin;%CUDA_HOME%\nvvm\bin;%BACKEND%\python\Scripts;%PATH%"
if not defined TORCH_CUDA_ARCH_LIST set "TORCH_CUDA_ARCH_LIST=12.0"
set "DISTUTILS_USE_SDK=1"
if not defined MAX_JOBS set /a "MAX_JOBS=NUMBER_OF_PROCESSORS / 2"
if %MAX_JOBS% LSS 1 set "MAX_JOBS=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_INPUT=1"

rem Paths are echoed with delayed expansion so "&", "(" or ")" in them cannot break the line.
echo [build] backend   : !BACKEND!
echo [build] python    : !PY!
echo [build] CUDA_HOME : !CUDA_HOME!
echo [build] MSVC      : %VCToolsVersion%
echo [build] arch list : %TORCH_CUDA_ARCH_LIST%
echo [build] MAX_JOBS  : %MAX_JOBS%
echo [build] targets   :%TARGETS%
for /f "tokens=*" %%L in ('nvcc --version ^| findstr /c:"release"') do echo [build] nvcc      : %%L
rem "-s" keeps the per-user site-packages (which can shadow the embedded
rem site-packages in this interpreter) out of the build and checks.
"%PY%" -s -c "import sys, torch; print('[build] torch     :', torch.__version__, '| cuda', torch.version.cuda, '| gpu available', torch.cuda.is_available()); sys.exit(0 if torch.version.cuda else 1)"
if errorlevel 1 (
    echo [build] ERROR: a CUDA-enabled torch is not importable in the embedded Python.
    exit /b 1
)

rem --------------------------------------------------------------- build loop
set "FAILED="
call :now TOTAL_START
for %%E in (%TARGETS%) do (
    call :build_one %%E
    if errorlevel 1 set "FAILED=!FAILED! %%E"
)
call :now TOTAL_END
set /a "TOTAL_SECS=TOTAL_END - TOTAL_START"
echo.
echo [build] total time: %TOTAL_SECS% s
if defined FAILED (
    echo [build] FAILED:%FAILED%
    exit /b 1
)
echo [build] OK:%TARGETS%
exit /b 0

rem ===========================================================================
rem  :build_one <name>   build + install + import-check one extension
rem ===========================================================================
:build_one
set "EXT=%~1"
set "REPO="
set "SRC="
set "CHECK_MOD="
if /i "%EXT%"=="cumesh" (
    set "REPO=!THIRD_PARTY!\extensions\CuMesh"
    set "SRC=!REPO!"
    set "CHECK_MOD=cumesh"
)
if /i "%EXT%"=="flex_gemm" (
    set "REPO=!THIRD_PARTY!\extensions\FlexGEMM"
    set "SRC=!REPO!"
    set "CHECK_MOD=flex_gemm"
)
if /i "%EXT%"=="nvdiffrast" (
    set "REPO=!THIRD_PARTY!\extensions\nvdiffrast"
    set "SRC=!REPO!"
    set "CHECK_MOD=nvdiffrast.torch"
)
if /i "%EXT%"=="o_voxel" (
    set "REPO=!THIRD_PARTY!\TRELLIS.2"
    set "SRC=!REPO!\o-voxel"
    set "CHECK_MOD=o_voxel"
)
set "PATCH=%PATCH_DIR%\%EXT%.patch"
set "CHECK_CODE=import torch, importlib; m = importlib.import_module('%CHECK_MOD%'); print('[build] %EXT%: import OK ->', m.__file__)"

echo.
echo ===========================================================================
echo [build] %EXT%  from  !SRC!
echo ===========================================================================
if not exist "%SRC%\setup.py" (
    echo [build] ERROR: %EXT%: setup.py not found in "%SRC%"
    exit /b 1
)

if not "%IF_MISSING%"=="1" goto do_build
"%PY%" -s -c "!CHECK_CODE!" >nul 2>&1
if errorlevel 1 goto do_build
echo [build] %EXT%: already installed and importable - skipped (--if-missing)
exit /b 0

:do_build
"%PY%" -s -c "!INUSE_CODE!" %EXT%
if errorlevel 1 (
    echo [build] ERROR: %EXT%: installed binaries are loaded by a running Python process ^(e.g. the backend^).
    echo [build]        Stop that process and re-run. Nothing was changed.
    exit /b 1
)

call :ensure_patch "%REPO%" "%PATCH%"
if errorlevel 1 exit /b 1

call :now EXT_START
"%PY%" -s -m pip install --no-build-isolation --no-deps --verbose "%SRC%"
set "PIP_RC=%ERRORLEVEL%"
call :now EXT_END
set /a "EXT_SECS=EXT_END - EXT_START"
if not "%PIP_RC%"=="0" (
    echo [build] ERROR: %EXT%: pip install failed with exit code %PIP_RC% after %EXT_SECS% s
    echo [build]        "Access is denied" on a .pyd means a Python process loaded the extension while it
    echo [build]        was building: stop it and re-run. pip rolls the old install back.
    exit /b 1
)
echo [build] %EXT%: build + install took %EXT_SECS% s

"%PY%" -s -c "!CHECK_CODE!"
if errorlevel 1 (
    echo [build] ERROR: %EXT%: installed but "import %CHECK_MOD%" fails
    if /i "%EXT%"=="o_voxel" echo [build]        o_voxel imports cumesh, flex_gemm and nvdiffrast - build those first.
    exit /b 1
)
exit /b 0

rem ===========================================================================
rem  :ensure_patch <repo_dir> <patch_file>
rem  Applies the patch unless it is already applied; no-op without a patch
rem  file. Needs git; without git it only warns.
rem ===========================================================================
:ensure_patch
if not exist "%~2" exit /b 0
where git.exe >nul 2>&1
if errorlevel 1 (
    echo [build] WARNING: git not found - cannot verify %~nx2, assuming it is applied.
    exit /b 0
)
git -C "%~1" apply --reverse --check "%~2" >nul 2>&1
if not errorlevel 1 (
    echo [build] patch already applied: %~nx2
    exit /b 0
)
git -C "%~1" apply --check "%~2"
if errorlevel 1 (
    echo [build] ERROR: patch %~nx2 is neither applied nor applicable in "%~1"
    exit /b 1
)
git -C "%~1" apply "%~2"
if errorlevel 1 (
    echo [build] ERROR: failed to apply %~nx2
    exit /b 1
)
echo [build] patch applied: %~nx2
exit /b 0

rem ===========================================================================
rem  :now <var>   store the current Unix time (seconds) in <var>
rem ===========================================================================
rem  The outer quotes are caret-escaped (^") so that "%PY%" stays quoted while the
rem  FOR line is parsed: a ")" in the install path (e.g. "... (x86)\...") would
rem  otherwise close the FOR set. cmd /c then strips the outer quote pair.
:now
set "%~1=0"
for /f %%T in ('^""%PY%" -s -c "!NOW_CODE!"^"') do set "%~1=%%T"
exit /b 0

:usage
echo Usage: %~nx0 [all ^| cumesh ^| flex_gemm ^| nvdiffrast ^| o_voxel ...] [--if-missing]
exit /b 0
