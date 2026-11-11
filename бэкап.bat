@echo off
rem Task Tracker - create a database backup (double-click this file).
rem The copy is created via the SQLite Backup API and is safe even while
rem the application is running. Restoring is a separate manual step,
rem see docs/BACKUP.md (restore requires the app to be stopped).
chcp 65001 >nul
title Task Tracker - backup
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
    echo         Run "zapusk.bat" ^(the app start script^) first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -X utf8 backup.py backup
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo =============================================
    echo  BACKUP FAILED. See the message above.
    echo =============================================
) else (
    echo =============================================
    echo  BACKUP CREATED in the backups\ folder.
    echo =============================================
)
echo.
pause
