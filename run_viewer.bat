@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
".venv\Scripts\python.exe" -c "from importlib.metadata import version; assert tuple(map(int, version('Pillow').split('.')[:3])) >= (12, 3, 0); assert tuple(map(int, version('tkinterdnd2').split('.')[:3])) >= (0, 5, 0)" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements.lock
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" "%~dp0comfy_exif_viewer.py" %*
exit /b %errorlevel%
