"""
pipeline.py — 翻译管线编排。

复用 manga_translator 包（已搬进本项目根目录），单进程 in-process 调用，
不走 executor/nonce 双进程协议。
"""
import os
import sys
from pathlib import Path

# ---- 项目根加入 sys.path（让 `from manga_translator import ...` 能找到本地包）----
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()  # 加载 .env（DEEPSEEK_API_KEY 等）

from PIL import Image
from manga_translator import MangaTranslator, Config


# ---- 单例：模型常驻，避免每次请求重载 ----
_translator: MangaTranslator | None = None


def get_translator() -> MangaTranslator:
    """返回常驻的 MangaTranslator 单例。首次调用时初始化。"""
    global _translator
    if _translator is None:
        _translator = MangaTranslator({
            'use_gpu': True,
            'ignore_errors': False,
            'verbose': False,
            'kernel_size': 3,
            'model_dir': os.path.join(PROJECT_ROOT, 'models'),
        })
    return _translator


def build_config(
    target_lang: str = 'CHS',
    source_lang: str = 'auto',
    direction: str = 'auto',
    font_size_offset: int = 0,
    font_size_minimum: int = -1,
) -> Config:
    """
    构建精简配置。固定使用：
    - translator: deepseek
    - detector: default
    - ocr: 48px (MIT 默认，质量/速度均衡)
    - inpainter: lama_large
    只暴露少量可调参数给前端。
    """
    c = Config()
    # 翻译器 + 语言
    c.translator.translator = 'deepseek'
    c.translator.target_lang = target_lang
    if source_lang and source_lang != 'auto':
        c.translator.skip_lang = None  # auto = 不跳过任何语言
    # 嵌字
    c.render.direction = direction
    c.render.font_size_offset = font_size_offset
    if font_size_minimum >= 0:
        c.render.font_size_minimum = font_size_minimum
    # 其余用默认：detector=default, ocr=48px, inpainter=lama_large
    return c


async def translate_image(
    image: Image.Image,
    target_lang: str = 'CHS',
    direction: str = 'auto',
    font_size_offset: int = 0,
    font_size_minimum: int = -1,
) -> Image.Image:
    """
    翻译一张图，返回嵌字后的 PIL Image。
    单人单机串行，由调用方（batch worker）控制并发。
    """
    t = get_translator()
    config = build_config(
        target_lang=target_lang,
        direction=direction,
        font_size_offset=font_size_offset,
        font_size_minimum=font_size_minimum,
    )
    ctx = await t.translate(image, config)
    return ctx.result
