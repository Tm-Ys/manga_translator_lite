"""
main.py — Manga Translator Lite 后端入口。

单进程 FastAPI，暴露 3 个端点 + 停止接口：
  POST /api/translate/batch        批量上传，返回 batch_id
  GET  /api/batch/{id}/status      查询批次状态（前端轮询）
  POST /api/shutdown               停止服务
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import nest_asyncio
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 确保当前项目根在 sys.path（这样 `from app...` 能 import）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import batch as batch_mod

nest_asyncio.apply()

app = FastAPI(title="Manga Translator Lite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 静态文件：outputs 目录（翻译结果落盘的地方）----
# 前端通过 /result/<batch_id>/<file.png> 直接拉取结果图
app.mount('/result', StaticFiles(directory=batch_mod.OUTPUTS_DIR), name='result')


@app.on_event("startup")
async def _startup():
    batch_mod.init_queue()
    print(f"[startup] queue ready, outputs dir: {batch_mod.OUTPUTS_DIR}")


@app.post("/api/open-outputs")
async def open_outputs():
    """在系统文件管理器里打开 outputs 目录。"""
    target = batch_mod.OUTPUTS_DIR
    try:
        if sys.platform == 'win32':
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target])
        else:
            subprocess.Popen(['xdg-open', target])
        return {'ok': True, 'path': target}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'path': target}


@app.get("/api/outputs/list")
async def list_outputs():
    """列出 outputs 目录下的所有批次和文件，供前端浏览/重新下载。"""
    root = batch_mod.OUTPUTS_DIR
    batches = []
    if os.path.isdir(root):
        for batch_id in sorted(os.listdir(root), reverse=True):
            bdir = os.path.join(root, batch_id)
            if not os.path.isdir(bdir):
                continue
            files = []
            for fn in sorted(os.listdir(bdir)):
                fp = os.path.join(bdir, fn)
                if os.path.isfile(fp):
                    files.append({
                        'name': fn,
                        'url': f'/result/{batch_id}/{fn}',
                        'size': os.path.getsize(fp),
                        'mtime': int(os.path.getmtime(fp)),
                    })
            if files:
                batches.append({'batch_id': batch_id, 'files': files})
    return {'outputs_dir': root, 'batches': batches}


# ---------- 字体管理 ----------

@app.get("/api/fonts")
async def list_fonts_endpoint():
    """列出所有可用字体（内置 + 系统 + 用户上传）。"""
    from app.pipeline import list_fonts
    return {'fonts': list_fonts()}


@app.post("/api/fonts/upload")
async def upload_font(file: UploadFile = File(...)):
    """上传用户字体到 fonts/user/。返回新字体项。"""
    from app.pipeline import FONT_EXTENSIONS, USER_FONTS_DIR
    fn = (file.filename or '').strip().lower()
    if not fn.endswith(FONT_EXTENSIONS):
        return {'ok': False, 'error': f'只支持 {FONT_EXTENSIONS} 字体文件'}
    # 安全校验：仅文件名，去掉路径
    safe = os.path.basename(file.filename)
    dest = os.path.join(USER_FONTS_DIR, safe)
    raw = await file.read()
    with open(dest, 'wb') as fp:
        fp.write(raw)
    return {
        'ok': True,
        'font': {
            'id': 'user:' + safe,
            'name': safe + '（用户上传）',
            'source': 'user',
            'path': dest,
        },
    }


# ---------- 请求/响应模型 ----------

class BatchStatusResponse(BaseModel):
    id: str
    summary: dict
    items: list[dict]
    out_root: Optional[str] = None


# ---------- 端点 ----------

@app.post("/api/translate/batch")
async def translate_batch(
    images: list[UploadFile] = File(...),
    config: str = Form('{}'),
):
    """
    批量上传图片。
    multipart: images[] 多文件 + config (JSON 字符串，可选)
    返回 batch_id + items 概览。
    """
    # config 暂时不深度解析（管线用固定配置），但保留接口给前端
    try:
        cfg = json.loads(config) if config else {}
    except json.JSONDecodeError:
        cfg = {}
    # 存到 batch 上，worker 用（目前只用 target_lang/direction）
    images_data = []
    for f in images:
        raw = await f.read()
        images_data.append((f.filename or 'image.png', raw))

    batch_id = batch_mod.enqueue_batch(images_data)
    # 把 cfg 挂到 batch 上（worker 读）
    b = batch_mod.BATCHES[batch_id]
    setattr(b, 'cfg', cfg)

    return {
        'batch_id': batch_id,
        'total': len(images_data),
        'items': [{'id': i.id, 'filename': i.filename} for i in b.items],
    }


@app.post("/api/translate/folder")
async def translate_folder(payload: dict):
    """
    文件夹模式：扫描本地目录，递归翻译所有图片，输出镜像目录树。
    payload: { input_root: str, config?: dict }
    """
    input_root = payload.get('input_root', '').strip()
    if not input_root:
        return {'error': '请提供 input_root 路径'}
    if not os.path.isdir(input_root):
        return {'error': f'路径不存在或不是目录: {input_root}'}

    cfg = payload.get('config', {}) or {}
    try:
        batch_id, out_root, total = batch_mod.enqueue_folder(input_root, cfg)
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}

    return {
        'batch_id': batch_id,
        'out_root': out_root,
        'total': total,
    }


@app.post("/api/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批次：未处理的项目会被跳过，正在跑的会跑完。"""
    ok = batch_mod.cancel_batch(batch_id)
    return {'ok': ok}


@app.get("/api/batch/{batch_id}/status", response_model=BatchStatusResponse)
async def batch_status(batch_id: str):
    b = batch_mod.BATCHES.get(batch_id)
    if not b:
        return BatchStatusResponse(id=batch_id, summary={'total': 0, 'done': 0, 'error': 0, 'running': False, 'finished': True, 'cancelled': False}, items=[])

    cfg = getattr(b, 'cfg', {}) or {}
    out_root = cfg.get('out_root') if cfg.get('mode') == 'folder' else None

    items_out = []
    for it in b.items:
        item_dict = {
            'id': it.id,
            'filename': it.rel_path or it.filename,  # 文件夹模式显示相对路径
            'status': it.status,
            'progress': it.progress,
            'error': it.error,
            'source_thumb': getattr(it, 'source_thumb_b64', ''),
        }
        if it.status == 'done' and it.result_b64:
            item_dict['result'] = it.result_b64
        if it.result_url:
            item_dict['result_url'] = it.result_url
        items_out.append(item_dict)

    return BatchStatusResponse(
        id=batch_id,
        summary=b.summary(),
        items=items_out,
        out_root=out_root,
    )


@app.post("/api/shutdown")
async def shutdown():
    """停止整个进程（前端"停止服务"按钮）。"""
    print("[shutdown] received, exiting in 1s...")
    loop = asyncio.get_event_loop()
    loop.call_later(1.0, lambda: os.kill(os.getpid(), signal.SIGINT))
    return {'ok': True, 'message': 'shutting down'}


@app.get("/")
async def root():
    return {'name': 'Manga Translator Lite', 'status': 'running'}


if __name__ == '__main__':
    # 不用 uvicorn.run（跟 nest_asyncio 的 loop_factory 冲突），
    # 直接命令行 `python -m uvicorn app.main:app` 启动更稳。
    import uvicorn
    uvicorn.run("app.main:app", host='127.0.0.1', port=8000, log_level='info')
