@echo off
REM ============================================
REM   Manga Translator Lite - First-run + Launcher
REM   - Creates venv if missing
REM   - Installs Python deps if missing
REM   - Copies CJK fonts if missing
REM   - Hands off to launcher.py (Chinese UI)
REM ============================================
cd /d "%~dp0"
setlocal EnableDelayedExpansion

set "PY=venv\Scripts\python.exe"

REM ---- 1. venv ----
if not exist "%PY%" (
    echo [1/4] Creating Python 3.12 virtual environment...
    where py >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] 'py' launcher not found. Install Python 3.12 first.
        pause
        exit /b 1
    )
    py -3.12 -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Make sure Python 3.12 is installed.
        pause
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip >nul
) else (
    echo [1/4] venv already exists.
)

REM ---- 2. Python deps (检测 fastapi 是否已装来判定) ----
"%PY%" -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo [2/4] Installing Python dependencies...
    echo        This may take 5-15 minutes on first run.
    echo        - torch (CUDA 12.8^) ~2.8 GB
    echo        - other deps
    "%PY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
    if errorlevel 1 (
        echo [ERROR] Failed to install torch.
        pause
        exit /b 1
    )
    "%PY%" -m pip install -r requirements.txt
    "%PY%" -m pip install omegaconf tensorboardX einops kornia ImageHash timm safetensors manga-ocr pandas onnxruntime deepl groq google-genai cryptography ctranslate2 sentencepiece tiktoken rusty-manga-image-translator --extra-index-url https://frederik-uni.github.io/manga-image-translator-rust/python/wheels/simple/
    if errorlevel 1 (
        echo [WARN] Some deps failed to install. Continuing, but things may break.
    )
) else (
    echo [2/4] Python deps already installed.
)

REM ---- 3. CJK fonts (msyh/msgothic 因版权不入库) ----
set "NEED_FONT=0"
if not exist "fonts\msyh.ttc" set "NEED_FONT=1"
if not exist "fonts\msgothic.ttc" set "NEED_FONT=1"
if "!NEED_FONT!"=="1" (
    echo [3/4] Copying CJK fonts from Windows...
    if exist "C:\Windows\Fonts\msyh.ttc" (
        copy /Y "C:\Windows\Fonts\msyh.ttc" "fonts\msyh.ttc" >nul
    ) else (
        echo        [WARN] msyh.ttc not found in C:\Windows\Fonts
    )
    if exist "C:\Windows\Fonts\msgothic.ttc" (
        copy /Y "C:\Windows\Fonts\msgothic.ttc" "fonts\msgothic.ttc" >nul
    ) else (
        echo        [WARN] msgothic.ttc not found, trying YuGothic...
        if exist "C:\Windows\Fonts\YuGothR.ttc" copy /Y "C:\Windows\Fonts\YuGothR.ttc" "fonts\msgothic.ttc" >nul >nul
    )
) else (
    echo [3/4] Fonts already in place.
)

REM ---- 4. Frontend deps ----
if not exist "front\node_modules" (
    echo [4/4] Installing frontend dependencies (npm install^)...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] npm not found. Install Node.js 20+ from https://nodejs.org
        pause
        exit /b 1
    )
    pushd front
    call npm install
    popd
) else (
    echo [4/4] Frontend deps already installed.
)

echo.
echo ============================================
echo  Setup complete. Launching Manga Translator Lite...
echo ============================================
echo.

REM ---- 启动 Python 引导（接管 .env 配置 + API 验证 + 起服务 + 开浏览器）----
"%PY%" launcher.py

REM launcher.py 退出后暂停，让用户能看到任何错误
if errorlevel 1 pause
endlocal
