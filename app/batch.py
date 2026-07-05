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
    progress: str = ''          # MIT state string ("detection", "ocr", ...)
    result_filename: Optional[str] = None   # "<batch_id>/<safe>.png"
    result_url: Optional[str] = None        # "/result/<batch_id>/<safe>.png"
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # 原图数据（处理前保留，用于前端缩略图）
    source_b64: str = ''
    source_thumb_b64: str = ''  # 原图缩略图 base64（给前端立即展示）
    # 结果图 base64（done 时填，前端直接展示，无需二次拉取）
    result_b64: str = ''
    # 文件夹模式专用：相对输入根的路径（如 "第2话/001.jpg"），网页模式为 None
    rel_path: Optional[str] = None


@dataclass
class Batch:
    id: str
    items: list[BatchItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    cancelled: bool = False  # 取消标志：worker 每张开始前检查

    def summary(self) -> dict:
        """汇总进度：done/total"""
        total = len(self.items)
        done = sum(1 for i in self.items if i.status == 'done')
        error = sum(1 for i in self.items if i.status == 'error')
        running = any(i.status == 'running' for i in self.items)
        # 取消后，未处理的 queued 项也算"结束"（避免前端永远轮询）
        cancelled_count = sum(1 for i in self.items if i.status == 'queued' and self.cancelled)
        finished = (done + error + cancelled_count) == total
        return {'total': total, 'done': done, 'error': error,
                'running': running, 'finished': finished,
                'cancelled': self.cancelled}


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

        # 取消检查：批次被取消后，未处理的 queued 项直接标记为已取消
        if batch.cancelled:
            if item.status == 'queued':
                item.status = 'error'
                item.error = '已取消'
                item.progress = 'cancelled'
                item.finished_at = time.time()
            _queue.task_done()
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
            # 把 font_id 解析成实际字体路径
            from app.pipeline import resolve_font_path
            font_path = resolve_font_path(cfg.get('font_id', 'auto'))
            item.progress = 'translating'
            result = await translate_image(
                img,
                target_lang=cfg.get('target_lang', 'CHS'),
                direction=cfg.get('direction', 'auto'),
                font_size_offset=int(cfg.get('font_size_offset', 0)),
                font_size_minimum=int(cfg.get('font_size_minimum', -1)),
                font_path=font_path,
            )

            item.result_b64 = _pil_to_b64_full(result)

            # 落盘路径：文件夹模式 vs 网页模式
            mode = cfg.get('mode', 'upload')
            if mode == 'folder':
                # 文件夹模式：镜像目录树，保留原文件名 + 原扩展名
                out_root = cfg.get('out_root', batch_id)
                rel = item.rel_path or item.filename
                # 安全校验：rel_path 不得逃出 out_root
                out_root_abs = os.path.join(OUTPUTS_DIR, out_root)
                out_path = os.path.normpath(os.path.join(out_root_abs, rel))
                if not out_path.startswith(os.path.normpath(out_root_abs) + os.sep) \
                   and out_path != os.path.normpath(out_root_abs):
                    raise RuntimeError(f'路径越界: {rel}')
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                # 保留原扩展名（jpg 仍是 jpg），但 PIL 按扩展名选格式
                # JPEG 不支持透明通道，RGBA/PA 要先转 RGB（白底）
                out_img = result
                ext_lower = os.path.splitext(out_path)[1].lower()
                if ext_lower in ('.jpg', '.jpeg') and out_img.mode in ('RGBA', 'PA', 'P'):
                    from PIL import Image as _PILImage
                    bg = _PILImage.new('RGB', out_img.size, (255, 255, 255))
                    if out_img.mode == 'P':
                        out_img = out_img.convert('RGBA')
                    bg.paste(out_img, mask=out_img.split()[-1] if out_img.mode == 'RGBA' else None)
                    out_img = bg
                out_img.save(out_path)
                url_rel = rel.replace('\\', '/').lstrip('/')
                item.result_filename = f'{out_root}/{url_rel}'
                item.result_url = f'/result/{out_root}/{url_rel}'
            else:
                # 网页上传模式：flat 到 <batch_id>/<安全名>.png
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


# 支持的图片扩展名（小写）
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def enqueue_folder(input_root: str, cfg: dict) -> tuple[str, str, int]:
    """
    文件夹模式：扫描 input_root 下所有图片（递归），入队翻译。
    输出会镜像目录树到 outputs/<输入目录名>_<时间戳>/，文件名保留。

    返回 (batch_id, out_root, total)。
    cfg 会被原地补上 mode='folder' 和 out_root。
    """
    input_root = os.path.abspath(input_root)
    if not os.path.isdir(input_root):
        raise NotADirectoryError(f'不是目录: {input_root}')

    # 输出根目录名：<输入目录名>_yyyy_mm_dd_HH_MM_SS
    dirname = os.path.basename(input_root.rstrip('\\/')) or 'output'
    ts = time.strftime('%Y_%m_%d_%H_%M_%S')
    out_root = f'{dirname}_{ts}'

    cfg = dict(cfg)
    cfg['mode'] = 'folder'
    cfg['out_root'] = out_root
    cfg['input_root'] = input_root

    batch_id = uuid.uuid4().hex[:12]
    batch = Batch(id=batch_id)

    # 递归扫描图片
    for root, _dirs, files in os.walk(input_root):
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_root)  # 如 "第2话\\001.jpg"
            try:
                with open(full, 'rb') as fp:
                    raw = fp.read()
            except Exception as e:
                print(f'[folder] 跳过无法读取的文件 {full}: {e}')
                continue
            b64 = 'data:image/png;base64,' + base64.b64encode(raw).decode()
            item = BatchItem(
                id=uuid.uuid4().hex[:8],
                filename=fn,
                rel_path=rel,
                source_b64=b64,
            )
            # 缩略图：试生成，失败就空着（前端用占位）
            try:
                img = Image.open(io.BytesIO(raw))
                item.source_thumb_b64 = _img_to_b64(img)
            except Exception:
                pass
            batch.items.append(item)

    BATCHES[batch_id] = batch
    setattr(batch, 'cfg', cfg)
    for item in batch.items:
        _queue.put_nowait((batch_id, item.id))
    return batch_id, out_root, len(batch.items)


def cancel_batch(batch_id: str) -> bool:
    """标记批次为取消。worker 会在下一张开始前检查并跳过。"""
    b = BATCHES.get(batch_id)
    if not b:
        return False
    b.cancelled = True
    return True


def enqueue_folder_upload(
    files: list[tuple[str, str, bytes]],
    input_dirname: str,
    cfg: dict,
) -> tuple[str, str, int]:
    """
    文件夹上传模式：浏览器通过 showDirectoryPicker 选目录后，把文件们
    （含相对路径）上传过来。后端按相对路径镜像输出。

    files: [(filename, rel_path, raw_bytes), ...]
        rel_path 形如 "第2话/001.jpg"（含子目录），用 / 分隔
    input_dirname: 用户选择的目录名（用于生成 out_root）
    返回 (batch_id, out_root, total)。
    """
    ts = time.strftime('%Y_%m_%d_%H_%M_%S')
    safe_dirname = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', input_dirname or 'upload')
    out_root = f'{safe_dirname}_{ts}'

    cfg = dict(cfg)
    cfg['mode'] = 'folder'  # 复用 worker 的 folder 分支
    cfg['out_root'] = out_root

    batch_id = uuid.uuid4().hex[:12]
    batch = Batch(id=batch_id)

    for filename, rel_path, raw in files:
        # 统一 rel_path 用 os.sep（worker 里用 os.path.join 拼）
        rel = rel_path.replace('/', os.sep)
        b64 = 'data:image/png;base64,' + base64.b64encode(raw).decode()
        item = BatchItem(
            id=uuid.uuid4().hex[:8],
            filename=filename,
            rel_path=rel,
            source_b64=b64,
        )
        try:
            img = Image.open(io.BytesIO(raw))
            item.source_thumb_b64 = _img_to_b64(img)
        except Exception:
            pass
        batch.items.append(item)

    BATCHES[batch_id] = batch
    setattr(batch, 'cfg', cfg)
    for item in batch.items:
        _queue.put_nowait((batch_id, item.id))
    return batch_id, out_root, len(batch.items)
