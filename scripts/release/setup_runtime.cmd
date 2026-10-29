@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
if not exist "data\output" mkdir "data\output"
if exist ".runtime\verified.json" (
  move /Y ".runtime\verified.json" ".runtime\verified.previous.json" >nul
  if errorlevel 1 exit /b 4
)
call :install_runtime >> "data\output\installation.log" 2>&1
set "setup_result=%errorlevel%"
if not "%setup_result%"=="0" (
  echo ShuJian Cube runtime setup failed. See data\output\installation.log.
  exit /b %setup_result%
)
echo ShuJian Cube runtime is ready.
exit /b 0

:install_runtime
echo.
if not exist "VERSION" exit /b 3
set "SHUJIAN_RELEASE_VERSION="
set /p "SHUJIAN_RELEASE_VERSION="<"VERSION"
if not defined SHUJIAN_RELEASE_VERSION exit /b 3
echo [%date% %time%] Preparing ShuJian Cube %SHUJIAN_RELEASE_VERSION%
set "UV_PYTHON_INSTALL_DIR=%cd%\.runtime\python"
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%cd%\.runtime\cache"
set "UV_PYTHON_PREFERENCE="
set "UV_NO_MANAGED_PYTHON="
set "UV_PYTHON_DOWNLOADS=never"
set "UV_LINK_MODE=copy"
set "UV_NO_PROGRESS=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH="
set "PYTHONHOME="
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
if not exist ".runtime\uv.exe" exit /b 2
if not exist "scripts\release\requirements-windows-x64.lock" exit /b 3
set "SHUJIAN_BUNDLED_PYTHON=%cd%\.runtime\python\cpython-3.11.9-windows-x86_64-none\python.exe"
if not exist "%SHUJIAN_BUNDLED_PYTHON%" exit /b 10
"%SHUJIAN_BUNDLED_PYTHON%" -I -X utf8 -c "import sys,ssl,venv,json,ctypes; assert sys.version_info[:3] == (3,11,9); print('Bundled Python 3.11.9 verified')"
if errorlevel 1 exit /b 10
if not exist ".venv\Scripts\python.exe" (
  ".runtime\uv.exe" --no-config venv --python "%SHUJIAN_BUNDLED_PYTHON%" --no-python-downloads ".venv"
  if errorlevel 1 exit /b 11
)
".runtime\uv.exe" --no-config pip sync --python ".venv\Scripts\python.exe" --require-hashes --build-constraints "scripts\release\build-constraints.txt" "scripts\release\requirements-windows-x64.lock"
if errorlevel 1 exit /b 12
".runtime\uv.exe" --no-config pip check --python ".venv\Scripts\python.exe"
if errorlevel 1 exit /b 13
".venv\Scripts\python.exe" -X utf8 "scripts\release\verify_runtime.py"
if errorlevel 1 exit /b 14
echo [%date% %time%] Runtime verification succeeded.
exit /b 0
