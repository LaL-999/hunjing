@echo off
REM ============================================================
REM HunJing Insights Admin Panel Launcher
REM   insights-backend  : FastAPI on http://localhost:8001
REM   insights-frontend : Vite on http://localhost:5174
REM
REM Insights admin only attaches huimeng.db read-only,
REM independent from user platform - can start without it.
REM ============================================================

title HunJing Insights Launcher

echo.
echo ============================================================
echo   HunJing Insights Admin - Starting...
echo.
echo   Backend  : http://localhost:8001
echo   Frontend : http://localhost:5174
echo.
echo   Two services will run in separate child windows.
echo   Close a child window to stop that service.
echo ============================================================
echo.

REM ---- sanity check ----
if not exist "%~dp0insights-backend\app\main.py" (
    echo [ERROR] insights-backend\app\main.py not found
    echo current script dir = %~dp0
    pause
    exit /b 1
)
if not exist "%~dp0insights-frontend\package.json" (
    echo [ERROR] insights-frontend\package.json not found
    pause
    exit /b 1
)

REM ---- insights-backend(port 8001)----
start "Insights backend (8001)" cmd /k "chcp 65001 >nul && cd /d %~dp0insights-backend && uvicorn app.main:app --host 0.0.0.0 --port 8001"

REM wait for backend
timeout /t 2 /nobreak >nul

REM ---- insights-frontend(vite dev, port 5174 to avoid clash with user 5173)----
start "Insights frontend (5174)" cmd /k "chcp 65001 >nul && cd /d %~dp0insights-frontend && npm run dev"

echo.
echo Two child windows launched.
echo Open browser: http://localhost:5174
echo.
echo Press any key to close this launcher (services keep running)...
pause >nul
