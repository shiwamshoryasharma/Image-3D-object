@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON_DIR=%ROOT%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "SITE_PACKAGES=%PYTHON_DIR%\Lib\site-packages"

if not exist "%PYTHON_EXE%" goto PYTHON_NOT_FOUND

set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="

if /I "%~1"=="-m" goto CHECK_PIP

"%PYTHON_EXE%" %*
exit /b %ERRORLEVEL%

:CHECK_PIP
if /I "%~2"=="pip" goto CHECK_INSTALL

"%PYTHON_EXE%" %*
exit /b %ERRORLEVEL%

:CHECK_INSTALL
if /I "%~3"=="install" goto PIP_INSTALL

"%PYTHON_EXE%" %*
exit /b %ERRORLEVEL%

:PIP_INSTALL
if not exist "%SITE_PACKAGES%" mkdir "%SITE_PACKAGES%"
"%PYTHON_EXE%" %* --target "%SITE_PACKAGES%" --no-warn-script-location
exit /b %ERRORLEVEL%

:PYTHON_NOT_FOUND
echo ERROR: "%PYTHON_EXE%" not found.
exit /b 1