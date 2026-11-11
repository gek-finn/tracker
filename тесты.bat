@echo off
rem Task Tracker - run all automated tests.
rem ASCII-only content: cmd.exe parses batch files in the OEM code page
rem (CP866), so non-ASCII characters break parsing. Keep this file ASCII.
chcp 65001 >nul
title Task Tracker - tests
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo         Run "zapusk.bat" first - it creates .venv and installs deps.
    echo.
    pause
    exit /b 1
)

echo Running Task Tracker tests...
echo Tests use a temporary SQLite database; the working tasks.db is NOT touched.
echo.

".venv\Scripts\python.exe" -X utf8 -m unittest discover -v
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo =============================================
    echo  SOME TESTS FAILED. See details above.
    echo =============================================
) else (
    echo =============================================
    echo  ALL TESTS PASSED.
    echo =============================================
)
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXITCODE%
