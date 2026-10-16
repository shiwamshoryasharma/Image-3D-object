@echo off
setlocal
if "%VCVARS_VER%"=="" (
  call "C:\VS Studio 2026\VC\Auxiliary\Build\vcvars64.bat" >nul
) else (
  call "C:\VS Studio 2026\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver=%VCVARS_VER% >nul
)
set "BACKEND=E:\14-OCT-2026\Img3D\backend"
set "CUDA_HOME=%BACKEND%\.toolchain\cuda\nvidia\cu13"
set "CUDA_PATH=%CUDA_HOME%"
set "PATH=%CUDA_HOME%\bin;%CUDA_HOME%\nvvm\bin;%BACKEND%\python\Scripts;%PATH%"
set "TORCH_CUDA_ARCH_LIST=12.0"
set "DISTUTILS_USE_SDK=1"
set "PYTHONNOUSERSITE=1"
set "SMOKE_BUILD_DIR=E:\14-OCT-2026\Img3D\test\env_setup\smoke_build"
if not exist "%SMOKE_BUILD_DIR%" mkdir "%SMOKE_BUILD_DIR%"
where cl
"%BACKEND%\python\python.exe" "E:\14-OCT-2026\Img3D\test\env_setup\smoke_ext.py"
exit /b %ERRORLEVEL%
