@echo off
call "C:\VS Studio 2026\VC\Auxiliary\Build\vcvars64.bat" >nul
set "CUDA_HOME=E:\14-OCT-2026\Img3D\backend\.toolchain\cuda\nvidia\cu13"
set "PATH=%CUDA_HOME%\bin;%PATH%"
cd /d E:\14-OCT-2026\Img3D\test\env_setup\nvcc_flags
echo === dup c++20
nvcc -c k2.cu -o k2a.obj -arch=sm_120 -std=c++20 -std=c++20 2>&1
echo exit=%ERRORLEVEL%
echo === c++20 then c++17
nvcc -c k2.cu -o k2b.obj -arch=sm_120 -std=c++20 -std=c++17 2>&1
echo exit=%ERRORLEVEL%
echo === Xcompiler std c++17 with -std=c++20
nvcc -c k2.cu -o k2c.obj -arch=sm_120 -std=c++20 -Xcompiler=/std:c++17 2>&1
echo exit=%ERRORLEVEL%
