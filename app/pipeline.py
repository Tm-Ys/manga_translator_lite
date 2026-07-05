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

FONTS_DIR = os.path.join(PROJECT_ROOT, 'fonts')
USER_FONTS_DIR = os.path.join(FONTS_DIR, 'user')
os.makedirs(USER_FONTS_DIR, exist_ok=True)

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


# ---- 字体管理 ----
# 内置字体白名单（仓库自带 + Windows 系统字体），用户字体在 fonts/user/
FONT_EXTENSIONS = ('.ttf', '.ttc', '.otf')

# Windows 系统字体目录里值得暴露给用户的字体（不要全列，太多了）
_SYSTEM_FONT_PICKS = ['msyh.ttc', 'msyhbd.ttc', 'msyhl.ttc', 'msgothic.ttc',
                      'YuGothR.ttc', 'YuGothM.ttc', 'simhei.ttf', 'simsun.ttc',
                      'meiryo.ttc', 'meiryob.ttc']


def list_fonts() -> list[dict]:
    """
    列出所有可用字体，按优先级：
      - 内置（仓库自带）：anime_ace / comic shanns / NotoSansMonoCJK 等
      - 系统（Windows 目录里被 run.bat 复制到 fonts/ 的，或直接读系统目录）
      - 用户上传（fonts/user/）
    每项返回 {id, name, source, path}。
    id 是稳定的标识符，前端用它发请求；path 是后端实际加载的绝对路径。
    """
    result = []
    seen_names = set()

    # 特殊项：auto（用 FALLBACK 链自动选）
    result.append({'id': 'auto', 'name': '自动（推荐）', 'source': 'builtin', 'path': ''})

    # 1. 项目 fonts/ 下的字体（不含 user/ 子目录）
    for fn in sorted(os.listdir(FONTS_DIR)):
        full = os.path.join(FONTS_DIR, fn)
        if not os.path.isfile(full):
            continue
        if fn == 'user':
            continue
        if not fn.lower().endswith(FONT_EXTENSIONS):
            continue
        # 给个友好中文名
        nice = _pretty_font_name(fn)
        if nice not in seen_names:
            seen_names.add(nice)
            result.append({'id': 'builtin:' + fn, 'name': nice,
                           'source': 'builtin', 'path': full})

    # 2. Windows 系统字体（如果存在且没被复制到 fonts/）
    win_fonts = os.environ.get('WINDIR', r'C:\Windows') + r'\Fonts'
    if os.path.isdir(win_fonts):
        for fn in _SYSTEM_FONT_PICKS:
            full = os.path.join(win_fonts, fn)
            local_copy = os.path.join(FONTS_DIR, fn)
            if os.path.isfile(full) and not os.path.isfile(local_copy):
                nice = _pretty_font_name(fn)
                if nice not in seen_names:
                    seen_names.add(nice)
                    result.append({'id': 'system:' + fn, 'name': nice,
                                   'source': 'system', 'path': full})

    # 3. 用户上传
    for fn in sorted(os.listdir(USER_FONTS_DIR)):
        full = os.path.join(USER_FONTS_DIR, fn)
        if not os.path.isfile(full):
            continue
        if not fn.lower().endswith(FONT_EXTENSIONS):
            continue
        nice = fn + '（用户上传）'
        result.append({'id': 'user:' + fn, 'name': nice,
                       'source': 'user', 'path': full})

    return result


def _pretty_font_name(filename: str) -> str:
    """把字体文件名转成友好中文名。"""
    name_map = {
        'msyh.ttc': '微软雅黑',
        'msyhbd.ttc': '微软雅黑 粗',
        'msyhl.ttc': '微软雅黑 细',
        'msgothic.ttc': 'MS Gothic（日文）',
        'yugothr.ttc': 'Yu Gothic（日文）',
        'yugothm.ttc': 'Yu Gothic Medium（日文）',
        'meiryo.ttc': 'Meiryo（日文）',
        'simhei.ttf': '黑体',
        'simsun.ttc': '宋体',
        'notosansmonocjk-vf.ttf.ttc': 'Noto Sans CJK（开源）',
        'arial-unicode-regular.ttf': 'Arial Unicode',
        'anime_ace.ttf': 'Anime Ace（英文手写）',
        'anime_ace_3.ttf': 'Anime Ace 3（英文手写）',
        'comic shanns 2.ttf': 'Comic Shanns（英文手写）',
    }
    return name_map.get(filename.lower(), os.path.splitext(filename)[0])


def resolve_font_path(font_id: str) -> str:
    """把前端传来的 font_id 解析成实际文件路径。空或 'auto' 返回 ''。"""
    if not font_id or font_id == 'auto':
        return ''
    for f in list_fonts():
        if f['id'] == font_id:
            return f['path']
    return ''


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
    font_path: str = '',
) -> Image.Image:
    """
    翻译一张图，返回嵌字后的 PIL Image。
    单人单机串行，由调用方（batch worker）控制并发。
    font_path: 主字体路径；空字符串表示走 FALLBACK 链自动选。
    """
    t = get_translator()
    t.font_path = font_path  # MangaTranslator 实例属性，dispatch 会用它
    config = build_config(
        target_lang=target_lang,
        direction=direction,
        font_size_offset=font_size_offset,
        font_size_minimum=font_size_minimum,
    )
    ctx = await t.translate(image, config)
    return ctx.result
