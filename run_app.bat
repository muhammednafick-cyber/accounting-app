@echo off
cd /d "%~dp0"
echo Starting Accounting App...
python app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Error: Application failed to start.
    echo Please ensure Python is installed and added to your PATH.
    echo.
    pause
)
