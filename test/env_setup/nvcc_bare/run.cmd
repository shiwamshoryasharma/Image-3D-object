@echo off
call "C:\VS Studio 2026\VC\Auxiliary\Build\vcvars64.bat" %VCV% >nul
set "CUDA_HOME=E:\14-OCT-2026\Img3D\backend\.toolchain\cuda\nvidia\cu13"
set "PATH=%CUDA_HOME%\bin;%PATH%"
cd /d E:\14-OCT-2026\Img3D\test\env_setup\nvcc_bare
echo === bare k.cu
nvcc -c k.cu -o k.obj -arch=sm_120 %EXTRA% 2>&1
echo exit=%ERRORLEVEL%
echo === k2.cu with STL
nvcc -c k2.cu -o k2.obj -arch=sm_120 -std=c++17 %EXTRA% 2>&1
echo exit=%ERRORLEVEL%
