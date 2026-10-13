@echo off
REM Runs TrueSize PDF. Installs PyMuPDF the first time if needed.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py
%PY% -c "import fitz" >nul 2>nul || (
    echo Installing PyMuPDF (first run only)...
    %PY% -m pip install --upgrade pymupdf
)
start "" %PY% "%~dp0TrueSizePDF.py" %*
