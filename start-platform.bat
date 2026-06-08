@echo off
REM ============================================================
REM HunJing User Platform Launcher
REM   backend  : FastAPI on http://localhost:8000
REM   frontend : Vite on http://localhost:5173
REM
REM Pure ASCII to avoid Windows cmd encoding issues.
REM Chinese text only inside child windows (chcp 65001 first).
REM ============================================================

title HunJing Platform Launcher

echo.
echo ============================================================
echo   HunJing User Platform - Starting...
echo.
echo   Backend  : http://localhost:8000
echo   Frontend : http://localhost:5173
echo.
echo   Two services will run in separate child windows.
echo   Close a child window to stop that service.
echo ============================================================
echo.

REM ---- sanity check ----
if not exist "%~dp0backend\app\main.py" (
    echo [ERROR] backend\app\main.py not found
    echo current script dir = %~dp0
    pause
    exit /b 1
)
if not exist "%~dp0frontend\package.json" (
    echo [ERROR] frontend\package.json not found
    pause
    exit /b 1
)

REM ---- backend(uvicorn, no --reload to avoid Windows quit bug)----
start "HunJing backend (8000)" cmd /k "chcp 65001 >nul && cd /d %~dp0backend && uvicorn app.main:app --host 0.0.0.0 --port 8000"

REM wait for backend before launching frontend
timeout /t 2 /nobreak >nul

REM ---- frontend(vite dev)----
start "HunJing frontend (5173)" cmd /k "chcp 65001 >nul && cd /d %~dp0frontend && npm run dev"

echo.
echo Two child windows launched.
echo Open browser: http://localhost:5173
echo.
echo Press any key to close this launcher (services keep running)...
pause >nul
