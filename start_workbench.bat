@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" workbench_server.py --open-browser %*
) else (
    where py >nul 2>&1
    if errorlevel 1 (
        python workbench_server.py --open-browser %*
    ) else (
        py -3 workbench_server.py --open-browser %*
    )
)
if errorlevel 1 (
    echo.
    echo Startup failed. Install Python 3 or check the error above.
    pause
)
endlocal
