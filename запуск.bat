@echo off
rem Task Tracker - local start on Windows (double-click this file).
rem ASCII-only content: cmd.exe parses batch files in the OEM code page
rem (CP866), so non-ASCII characters break parsing. Keep this file ASCII.
title Task Tracker
cd /d "%~dp0"

rem 0. pip temp on the project drive (the system drive may be full)
if not exist ".pip_tmp" mkdir .pip_tmp
set "TMP=%CD%\.pip_tmp"
set "TEMP=%CD%\.pip_tmp"

rem 1. Pick a Python launcher command
set "PYTHON=py -3"
where py >nul 2>nul
if errorlevel 1 set "PYTHON=python"

rem 2. Virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to create the virtual environment.
        echo         Install Python 3.10+ from https://www.python.org/downloads/
        echo         and run this file again.
        echo.
        pause
        exit /b 1
    )
)

rem 3. Dependencies (installed once; repeated runs skip installed ones)
echo [2/3] Checking dependencies ^(Flask, openpyxl, gunicorn^)...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Primary PyPI is unreachable, trying mirror...
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)
if errorlevel 1 (
    echo [WARN] pip could not check dependencies.
    ".venv\Scripts\python.exe" -c "import flask, openpyxl" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependencies are missing and could not be installed.
        echo         Check your internet connection and free disk space.
        echo.
        pause
        exit /b 1
    )
    echo [WARN] Existing dependencies found - continuing without update.
)

rem 4. Start the app and open the browser
echo [3/3] Starting Task Tracker...
echo.
echo App address: http://127.0.0.1:5000
echo To stop: close this window or press Ctrl+C.
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:5000"
".venv\Scripts\python.exe" app.py
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo [ERROR] The application exited with code %EXITCODE%.
    echo         Check the messages above.
) else (
    echo Application stopped.
)
echo.
pause
