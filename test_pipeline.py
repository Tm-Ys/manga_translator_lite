"""
测试脚本：读一张图 -> 翻译 -> 存盘。
首次运行会下载模型（约 2GB），耗时较长。
"""
import asyncio
import sys
import time
from pathlib import Path

from PIL import Image

from app.pipeline import translate_image


async def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('test_input.png')
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('test_output.png')

    if not src.exists():
        print(f'[ERR] 输入图不存在: {src}')
        sys.exit(1)

    print(f'[INFO] 输入: {src}')
    print(f'[INFO] 输出: {dst}')
    print('[INFO] 开始翻译（首次会下载模型，请耐心等待）...')

    img = Image.open(src).convert('RGB')
    t0 = time.time()
    result = await translate_image(img, target_lang='CHS', direction='auto')
    dt = time.time() - t0

    result.save(dst)
    print(f'[OK] 完成，耗时 {dt:.1f}s，结果已存: {dst}')


if __name__ == '__main__':
    asyncio.run(main())
