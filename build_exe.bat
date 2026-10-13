@echo off
REM Builds a standalone TrueSizePDF.exe (no Python needed on the target PC).
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py
%PY% -m pip install --upgrade pymupdf pyinstaller
%PY% -m PyInstaller --noconfirm --onefile --windowed --name TrueSizePDF TrueSizePDF.py
echo.
echo Done. Your app is in the "dist" folder: dist\TrueSizePDF.exe
pause
