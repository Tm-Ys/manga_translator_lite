"""
launcher.py — Manga Translator Lite 启动引导。

职责（run.bat 已建好 venv / 装好依赖 / 复制好字体之后，这里接管）：
  1. 检查 .env；不存在或 key 无效时交互式引导用户输入
  2. 真实调用 DeepSeek API 验证 key 有效（max_tokens=1，几乎不消耗）
  3. 启动后端（uvicorn）+ 前端（vite）子进程
  4. 等待后端就绪后用默认浏览器打开界面
  5. 监控子进程，任一退出或 Ctrl+C 时清理

只用 Python 标准库，零额外依赖。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / '.env'
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
DEEPSEEK_BASE = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-v4-flash'


# ---------- .env 读写 ----------

def parse_env(path: Path) -> dict:
    """简易 .env 解析（KEY=VALUE，忽略注释和空行）。"""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env(path: Path, values: dict) -> None:
    with path.open('w', encoding='utf-8') as f:
        for k, v in values.items():
            f.write(f'{k}={v}\n')


# ---------- API 验证 ----------

def verify_key(api_key: str, model: str) -> tuple[bool, str]:
    """真实调用一次 DeepSeek API 验证 key 是否有效。max_tokens=1 几乎不消耗。"""
    payload = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': 'hi'}],
        'max_tokens': 1,
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{DEEPSEEK_BASE}/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            if 'choices' in body:
                return True, 'API 验证通过'
            return False, f'响应异常: {str(body)[:200]}'
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:200]
        if e.code == 401:
            return False, 'API Key 无效或已过期（401）'
        return False, f'HTTP {e.code}: {detail}'
    except urllib.error.URLError as e:
        return False, f'网络错误: {e.reason}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def prompt_for_key(default_model: str) -> tuple[str, str]:
    """交互式引导用户输入 API key 和模型名。"""
    print()
    print('=' * 56)
    print('  配置 DeepSeek API')
    print('=' * 56)
    print('翻译需要 DeepSeek API Key。')
    print('获取地址: https://platform.deepseek.com/api_keys')
    print()
    print('\033[36m[隐私提示]\033[0m Key 仅保存在本机 .env 文件，')
    print('不会上传到任何服务器。本程序只直接调用 DeepSeek 官方 API。')
    print()
    while True:
        key = input('请输入 DeepSeek API Key (sk-...): ').strip()
        if key:
            break
        print('  不能为空，请重新输入。')
    model = input(f'模型名（回车默认 {default_model}）: ').strip() or default_model
    print()
    return key, model


def ensure_api_configured() -> bool:
    """确保 .env 里有有效的 key。返回 True 表示已就绪。"""
    env = parse_env(ENV_FILE)
    api_key = env.get('DEEPSEEK_API_KEY', '').strip()
    model = env.get('DEEPSEEK_MODEL', '').strip() or DEFAULT_MODEL

    # 没 .env 或没 key → 完整引导
    if not api_key:
        print('\033[33m[首次启动] 未检测到 DeepSeek API 配置，开始引导。\033[0m')
        while True:
            key, mdl = prompt_for_key(DEFAULT_MODEL)
            print('正在验证 API Key...')
            ok, msg = verify_key(key, mdl)
            if ok:
                print(f'\033[32m[成功]\033[0m {msg}')
                write_env(ENV_FILE, {
                    'DEEPSEEK_API_KEY': key,
                    'DEEPSEEK_API_BASE': DEEPSEEK_BASE,
                    'DEEPSEEK_MODEL': mdl,
                })
                print(f'\033[32m[已保存]\033[0m 配置写入 {ENV_FILE.name}')
                return True
            print(f'\033[31m[失败]\033[0m {msg}')
            print('请检查 Key 是否正确，或模型名是否支持。')
            retry = input('重新输入？(Y/n): ').strip().lower()
            if retry == 'n':
                return False
        # unreachable
    return True


def quick_check_existing() -> bool:
    """已有 .env 时，启动前快速验证一次 key 是否仍然有效。"""
    env = parse_env(ENV_FILE)
    api_key = env.get('DEEPSEEK_API_KEY', '').strip()
    model = env.get('DEEPSEEK_MODEL', '').strip() or DEFAULT_MODEL
    if not api_key:
        return ensure_api_configured()

    print('正在确认 API Key 仍然有效...')
    ok, msg = verify_key(api_key, model)
    if ok:
        print(f'\033[32m[OK]\033[0m {msg}（模型: {model}）')
        return True
    print(f'\033[31m[警告]\033[0m 现有 Key 验证失败：{msg}')
    choice = input('是否重新配置？(Y/n): ').strip().lower()
    if choice != 'n':
        # 强制重新引导
        if ENV_FILE.exists():
            ENV_FILE.unlink()
        return ensure_api_configured()
    print('跳过验证，继续启动（如果 Key 真的无效，翻译时会报错）。')
    return True


# ---------- 子进程启动 ----------

def start_backend() -> subprocess.Popen:
    py = sys.executable
    print(f'\033[36m[启动后端]\033[0m FastAPI @ http://127.0.0.1:{BACKEND_PORT}')
    return subprocess.Popen(
        [py, '-m', 'uvicorn', 'app.main:app',
         '--host', '127.0.0.1', '--port', str(BACKEND_PORT)],
        cwd=str(PROJECT_ROOT),
    )


def start_frontend() -> subprocess.Popen:
    print(f'\033[36m[启动前端]\033[0m Vite @ http://127.0.0.1:{FRONTEND_PORT}')
    # Windows 下 npx 要 shell=True 才能找到
    return subprocess.Popen(
        f'npx vite --host 127.0.0.1 --port {FRONTEND_PORT}',
        cwd=str(PROJECT_ROOT / 'front'),
        shell=True,
    )


def wait_for_backend(timeout: int = 60) -> bool:
    """轮询后端直到 / 返回 200，或超时。"""
    print('等待后端就绪...', end='', flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{BACKEND_PORT}/', timeout=2):
                print(' \033[32m就绪\033[0m')
                return True
        except Exception:
            print('.', end='', flush=True)
            time.sleep(1)
    print(' \033[31m超时\033[0m')
    return False


def open_browser():
    url = f'http://127.0.0.1:{FRONTEND_PORT}'
    print(f'\033[36m[打开浏览器]\033[0m {url}')
    webbrowser.open(url)


# ---------- 主流程 ----------

def main():
    os.chdir(PROJECT_ROOT)

    # 1. 配置验证
    if not quick_check_existing():
        print('\033[31m未配置有效 API Key，无法启动翻译服务。\033[0m')
        sys.exit(1)

    print()
    backend = start_backend()
    frontend = start_frontend()

    try:
        if wait_for_backend():
            open_browser()
        else:
            print('\033[33m后端启动较慢，请稍后手动打开浏览器。\033[0m')

        print()
        print('=' * 56)
        print('  服务已启动')
        print(f'  浏览器: http://127.0.0.1:{FRONTEND_PORT}')
        print('  关闭此窗口将停止所有服务。')
        print('=' * 56)
        print()

        # 监控子进程，任一退出则全部清理
        while True:
            if backend.poll() is not None:
                print('\033[31m[后端已退出]\033[0m')
                break
            if frontend.poll() is not None:
                print('\033[31m[前端已退出]\033[0m')
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n\033[33m收到 Ctrl+C，正在停止所有服务...\033[0m')
    finally:
        for p in (backend, frontend):
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print('\033[32m已停止。\033[0m')


if __name__ == '__main__':
    main()
