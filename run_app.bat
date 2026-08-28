@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   Accounting App - starting up
echo ============================================================
echo.

REM ------------------------------------------------------------------
REM 1. Python
REM ------------------------------------------------------------------
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [X] Python was not found on your PATH.
    echo     Install Python, or add it to PATH, then run this again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

REM ------------------------------------------------------------------
REM 2. Python dependencies
REM ------------------------------------------------------------------
python -c "import flask" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [..] Installing Python dependencies ^(first run, this takes a minute^)...
    python -m pip install -q -r requirements.txt
)

python -c "import flask" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [X] Could not install dependencies. See the errors above.
    pause
    exit /b 1
)

echo [OK] Python packages ready

echo.
echo ============================================================
echo   Starting the app at http://localhost:5000
echo ============================================================
echo.

python app.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [X] The application failed to start. See the errors above.
    echo.
    pause
    exit /b 1
)

endlocal
exit /b 0
