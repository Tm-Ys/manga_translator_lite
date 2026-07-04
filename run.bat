@echo off
REM ============================================
REM   Manga Translator Lite - 一键启动
REM   同时起后端 (FastAPI:8000) 和前端 (Vite:5173)
REM ============================================
cd /d "%~dp0"

echo [1/3] 启动后端 (FastAPI @ http://127.0.0.1:8000)...
start "MIT-Lite Backend" cmd /k "venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [2/3] 启动前端 (Vite @ http://127.0.0.1:5173)...
start "MIT-Lite Frontend" cmd /k "cd front && npx vite --host 127.0.0.1 --port 5173"

echo [3/3] 等待服务就绪，打开浏览器...
timeout /t 6 /nobreak >nul
start http://127.0.0.1:5173

echo.
echo ============================================
echo  两个窗口已启动。关闭对应窗口即停止服务。
echo  也可以在网页点"停止服务"按钮停后端。
echo ============================================
echo.
echo 此窗口可关闭。
pause
