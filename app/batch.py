"""
batch.py — 批量任务队列 + 进度状态。

单 worker 串行处理（单人单机，避免 GPU 抢占）。
状态全在内存 dict 里，进程重启即清空（单人用够）。
"""
import asyncio
import base64
import io
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from PIL import Image


# ---- 输出目录：翻译结果自动落盘到这里 ----
OUTPUTS_DIR = str(Path(__file__).resolve().parent.parent / 'outputs')
os.makedirs(OUTPUTS_DIR, exist_ok=True)


@dataclass
class BatchItem:
    id: str
    filename: str
    status: Literal['queued', 'running', 'done', 'error'] = 'queued'
    progress: str = ''          # 来自 MIT 的 state 字符串（如 "detection", "ocr" 等）
    result_filename: Optional[str] = None  # 存到 outputs/ 的文件名（含批次子目录），前端用 /result/{path} 拉
    result_url: Optional[str] = None       # 静态文件 URL，如 /result/abc123/原文件名.png
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # 原图数据（处理前保留，用于前端缩略图）
    source_b64: str = ''
    source_thumb_b64: str = ''  # 原图缩略图 base64（给前端立即展示）
    # 结果图 base64（done 时填，前端直接展示，无需二次拉取）
    result_b64: str = ''


@dataclass
class Batch:
    id: str
    items: list[BatchItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def summary(self) -> dict:
        """汇总进度：done/total"""
        total = len(self.items)
        done = sum(1 for i in self.items if i.status == 'done')
        error = sum(1 for i in self.items if i.status == 'error')
        running = any(i.status == 'running' for i in self.items)
        return {'total': total, 'done': done, 'error': error,
                'running': running, 'finished': (done + error) == total}


# ---- 全局状态 ----
BATCHES: dict[str, Batch] = {}
RESULTS_DIR: str = ''  # main.py 启动时设置

# 任务队列 + 单 worker
_queue: asyncio.Queue = None  # main.py 启动时创建
_worker_started = False


def init_queue(loop=None):
    """在 asyncio loop 里初始化队列并启动 worker。"""
    global _queue, _worker_started
    if _queue is None:
        _queue = asyncio.Queue()
    if not _worker_started:
        _worker_started = True
        asyncio.create_task(_worker())


def _img_to_b64(img: Image.Image, fmt='PNG', max_size=400) -> str:
    """PIL Image -> base64 data URL（缩略图，给前端展示用）。"""
    thumb = img.copy()
    thumb.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    thumb.save(buf, format=fmt)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _pil_to_b64_full(img: Image.Image) -> str:
    """完整图 base64（结果图）。"""
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _safe_filename(name: str) -> str:
    """把任意文件名清成安全的文件系统名（保留中文，去路径/特殊符号）。"""
    base = os.path.basename(name or 'image.png')
    # 去掉路径分隔符和控制字符，保留常见字符（含中日韩）
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', base)
    # 换扩展名为 .png（嵌字结果统一 PNG）
    stem = os.path.splitext(base)[0] or 'image'
    return stem + '.png'


async def _worker():
    """单 worker：从队列取任务，串行调用 pipeline。"""
    from app.pipeline import translate_image  # 延迟导入，避免循环依赖 + 确保 fork path 已就绪
    print('[worker] started, waiting for tasks...')
    while True:
        batch_id, item_id = await _queue.get()
        batch = BATCHES.get(batch_id)
        if not batch:
            continue
        item = next((i for i in batch.items if i.id == item_id), None)
        if not item:
            continue

        item.status = 'running'
        item.progress = 'loading'
        item.started_at = time.time()

        try:
            # 从 base64 还原原图
            header, data = item.source_b64.split(',', 1)
            img = Image.open(io.BytesIO(base64.b64decode(data))).convert('RGB')

            # 从 batch 上读前端传来的配置（main.py 挂的 cfg 属性）
            cfg = getattr(batch, 'cfg', {}) or {}
            item.progress = 'translating'
            result = await translate_image(
                img,
                target_lang=cfg.get('target_lang', 'CHS'),
                direction=cfg.get('direction', 'auto'),
                font_size_offset=int(cfg.get('font_size_offset', 0)),
                font_size_minimum=int(cfg.get('font_size_minimum', -1)),
            )

            item.result_b64 = _pil_to_b64_full(result)
            # 落盘：outputs/<batch_id>/<安全文件名>.png
            batch_dir = os.path.join(OUTPUTS_DIR, batch_id)
            os.makedirs(batch_dir, exist_ok=True)
            safe_name = _safe_filename(item.filename)
            out_path = os.path.join(batch_dir, safe_name)
            result.save(out_path, format='PNG')
            item.result_filename = f'{batch_id}/{safe_name}'
            item.result_url = f'/result/{batch_id}/{safe_name}'
            item.status = 'done'
            item.progress = 'finished'
            item.finished_at = time.time()
            dt = item.finished_at - item.started_at
            print(f'[worker] done: {item.filename} in {dt:.1f}s -> {out_path}')
        except Exception as e:
            item.status = 'error'
            item.error = f'{type(e).__name__}: {e}'
            item.progress = 'failed'
            item.finished_at = time.time()
            print(f'[worker] error: {item.filename}: {e}')
        finally:
            _queue.task_done()


def enqueue_batch(images: list[tuple[str, bytes]]) -> str:
    """
    入队一批图。images = [(filename, raw_bytes), ...]
    返回 batch_id。
    """
    batch_id = uuid.uuid4().hex[:12]
    batch = Batch(id=batch_id)
    for filename, raw in images:
        item_id = uuid.uuid4().hex[:8]
        # 转 base64 data URL 存起来（worker 处理时还原）
        b64 = 'data:image/png;base64,' + base64.b64encode(raw).decode()
        item = BatchItem(
            id=item_id,
            filename=filename,
            source_b64=b64,
        )
        # 同时生成原图缩略图供前端立刻展示
        try:
            img = Image.open(io.BytesIO(raw))
            item.source_thumb_b64 = _img_to_b64(img)  # type: ignore[attr-defined]
        except Exception:
            pass
        batch.items.append(item)

    BATCHES[batch_id] = batch
    # 入队（注意：worker 在另一个 task 里 await，这里只 put）
    for item in batch.items:
        _queue.put_nowait((batch_id, item.id))
    return batch_id
