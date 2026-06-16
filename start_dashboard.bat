@echo off
REM Start the WC Engine dashboard (FastAPI) and open it in the browser.
REM Serves webapp\index.html at http://127.0.0.1:<port>.
REM Picks a free port automatically (starting at WC_WEB_PORT, default 8000) so it
REM won't collide with another server already running on that port.

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Activate the project virtualenv if present, else fall back to system python.
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

if "%WC_WEB_PORT%"=="" set "WC_WEB_PORT=8000"
set "PORT=%WC_WEB_PORT%"

:findport
netstat -ano -p tcp | findstr "LISTENING" | findstr /c:":%PORT% " >nul
if not errorlevel 1 (
  echo Port %PORT% is busy, trying %PORT% +1 ...
  set /a PORT+=1
  goto findport
)
set "WC_WEB_PORT=%PORT%"

echo Starting WC Engine dashboard on http://127.0.0.1:%PORT% ...
REM Open the browser after a short delay, once the server has had time to bind.
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:%PORT%'"

python app.py

endlocal
