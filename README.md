# Manga Translator Lite

基于大模型（DeepSeek）的漫画自动翻译。日漫竖排日文 → 中文，自动擦字 + 嵌字回填。

复用 [manga-image-translator](../manga-image-translator) 的核心神经网络模块（检测 / OCR / inpainting / rendering），用全新的单进程 FastAPI 后端 + 轻量 React 前端重写编排层。

## 架构

```
manga-translator-lite/
├── app/                # 后端（单进程 FastAPI）
│   ├── main.py         # 3 个端点：批量翻译 / 状态轮询 / 停止
│   ├── pipeline.py     # 翻译管线（委托给 fork 的 MangaTranslator）
│   └── batch.py        # 任务队列 + 进度状态（内存 dict）
├── front/              # 前端（React 19 + Vite + TS）
│   └── src/App.tsx     # 多文件上传 + 队列 + 轮询 + 结果对比
├── .env                # DeepSeek API key
├── requirements.txt
└── run.bat             # 一键启动
```

**关键设计**：通过 `sys.path` 引用隔壁 `../manga-image-translator/manga_translator` 的模块，复用其检测/OCR/inpainting/rendering 实现，不复制代码。fork 当"模型库"。

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
REM 还需这些 fork 间接依赖
venv\Scripts\python.exe -m pip install omegaconf tensorboardX einops kornia ImageHash timm safetensors manga-ocr pandas onnxruntime deepl groq google-genai cryptography ctranslate2 sentencepiece tiktoken rusty-manga-image-translator --extra-index-url https://frederik-uni.github.io/manga-image-translator-rust/python/wheels/simple/

REM 2. 前端依赖
cd front && npm install && cd ..
```

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
