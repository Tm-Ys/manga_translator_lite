# Manga Translator Lite

基于大模型（DeepSeek）的漫画自动翻译。日漫竖排日文 → 中文，自动擦字 + 嵌字回填。

> **许可证**：GPL-3.0（见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)）。
> 本项目核心神经网络模块（`manga_translator/`）来源于上游
> [manga-image-translator](https://github.com/zyddnys/manga-image-translator)，
> 其余代码为本项目编写，整体以 GPL-3.0 发布。

## 架构

```
manga-translator-lite/
├── app/                # 后端（单进程 FastAPI）
│   ├── main.py         # 端点：批量翻译 / 状态轮询 / 列出结果 / 打开目录 / 停止
│   ├── pipeline.py     # 翻译管线（单例 MangaTranslator，单进程）
│   └── batch.py        # 任务队列 + 进度状态（内存 dict）
├── front/              # 前端（React 19 + Vite + TS）
│   └── src/App.tsx     # 多文件上传 + 队列 + 轮询 + 结果对比
├── manga_translator/   # 核心模块（来自上游 GPL-3.0）
├── fonts/              # 嵌字字体（用户自行放入）
├── models/             # 模型权重（运行时自动下载，不入库）
├── dict/               # 翻译词典
├── .env                # DeepSeek API key（不入库）
├── requirements.txt
└── run.bat             # 一键启动
```

**独立运行**：`manga_translator/` 已搬入项目根目录，不依赖外部 fork。

## 管线

每张图依次跑：
1. **检测** (`detector=default`) — 找文字区域
2. **OCR** (`ocr=48px`) — 识别日文文字
3. **翻译** (`translator=deepseek`) — DeepSeek-V4-Flash 翻译成目标语言
4. **擦字** (`inpainter=lama_large`) — 神经网络擦除原文（~200MB 模型）
5. **嵌字** (`rendering`) — 自适应字号 + 气泡形状感知，把译文嵌回去

## 首次安装

需要 Python 3.12、Node.js 20+、NVIDIA GPU（驱动支持 CUDA 12.8+）。

```bat
REM 1. 建 venv + 装依赖
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
venv\Scripts\python.exe -m pip install -r requirements.txt
REM 还需这些间接依赖（manga_translator 包内部引用）
venv\Scripts\python.exe -m pip install omegaconf tensorboardX einops kornia ImageHash timm safetensors manga-ocr pandas onnxruntime deepl groq google-genai cryptography ctranslate2 sentencepiece tiktoken rusty-manga-image-translator --extra-index-url https://frederik-uni.github.io/manga-image-translator-rust/python/wheels/simple/

REM 2. 前端依赖
cd front && npm install && cd ..

REM 3. 字体（可选）
REM    仓库已含开源 NotoSansMonoCJK（CJK 全覆盖）作为兜底字体，开箱即用。
REM    run.bat 会自动从 C:\Windows\Fonts 复制 msyh.ttc / msgothic.ttc（如存在）
REM    获得更好的中文/日文显示效果（这些字体因版权不入库）。

REM 4. 配置 .env（填入你的 DeepSeek key）
copy .env.example .env
notepad .env
```

首次翻译时会自动下载模型权重（约 685MB）到 `models/`。

## 启动

双击 `run.bat`，或：

```bat
REM 终端 1：后端
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

REM 终端 2：前端
cd front && npx vite
```

浏览器打开 http://127.0.0.1:5173

## 配置

`.env` 文件：
```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

## API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/translate/batch` | POST | multipart 上传多图 + config JSON，返回 `batch_id` |
| `/api/batch/{id}/status` | GET | 轮询批次状态（含每张图结果 base64） |
| `/api/shutdown` | POST | 停止后端 |

## 已知约束

- 首次翻译下载模型 ~2GB（detect / ocr / lama_large / 字体），之后常驻。
- 串行处理（单 worker），避免 GPU 抢占。16GB 显存够用。
- 状态存内存，进程重启即清空（单人单机够用）。
- 依赖隔壁 fork 目录存在（作为"模型库"）。
