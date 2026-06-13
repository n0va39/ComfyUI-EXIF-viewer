@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --collect-data tkinterdnd2 --name ComfyUI-EXIF-viewer comfy_exif_viewer.py
