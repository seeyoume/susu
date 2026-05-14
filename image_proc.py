"""绮绮采集器 - 图片处理
- 随机噪点扰动 MD5
- 添加文字水印
- 微裁边
- 随机文件名输出
"""
import os
import random
import uuid
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _find_font(size=24):
    """ 在 Windows/macOS/Linux 常见字体目录找一个中文字体 """
    candidates = [
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",      # 黑体
        r"C:\Windows\Fonts\simsun.ttc",
        # Linux
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _add_noise(img, level=0.003):
    """ 给图片随机像素加微小噪点 - 改变 MD5 但视觉无感 """
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    pixels = img.load()
    w, h = img.size
    n = int(w * h * level)
    for _ in range(n):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)
        cur = pixels[x, y]
        if isinstance(cur, tuple):
            new = tuple(min(255, max(0, c + random.randint(-3, 3))) for c in cur[:3])
            if len(cur) == 4:
                new = new + (cur[3],)
            pixels[x, y] = new
    return img


def _add_watermark(img, text, pos="右下", opacity=180, size=None):
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    if size is None:
        size = max(18, min(w, h) // 40)
    font = _find_font(size)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # 测量文字尺寸
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = len(text) * size // 2, size
    margin = max(12, size // 2)
    pos_map = {
        "右下": (w - tw - margin, h - th - margin),
        "右上": (w - tw - margin, margin),
        "左下": (margin, h - th - margin),
        "左上": (margin, margin),
        "居中": ((w - tw) // 2, (h - th) // 2),
    }
    x, y = pos_map.get(pos, pos_map["右下"])
    # 黑色阴影 + 白色文字
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, opacity))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, opacity))
    out = Image.alpha_composite(img, overlay)
    return out


def _micro_crop(img):
    """ 微裁 1~3 像素 """
    w, h = img.size
    a = random.randint(0, 3)
    b = random.randint(0, 3)
    c = random.randint(0, 3)
    d = random.randint(0, 3)
    return img.crop((a, b, w - c, h - d))


def process(in_path, out_dir, opts):
    """ opts: dict
        in_path: str
        out_dir: Path
        返回输出路径
    """
    if not HAS_PIL:
        raise RuntimeError("未安装 Pillow，请执行: pip install pillow")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(in_path)
    # ICC 等元数据不带出（去关联线索）
    if opts.get("micro_crop"):
        img = _micro_crop(img)
    if opts.get("noise"):
        img = _add_noise(img)
    if opts.get("watermark") and opts.get("watermark_text"):
        img = _add_watermark(img,
                             opts["watermark_text"],
                             pos=opts.get("watermark_pos", "右下"),
                             opacity=int(opts.get("watermark_opacity", 180)))
    # 输出
    src = Path(in_path)
    ext = src.suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    if opts.get("random_name"):
        name = uuid.uuid4().hex[:12] + ext
    else:
        name = src.stem + "_p" + ext
    out_path = out_dir / name
    # JPG 不支持 RGBA
    if ext in (".jpg", ".jpeg") and img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    save_kwargs = {}
    if ext in (".jpg", ".jpeg"):
        save_kwargs["quality"] = random.randint(88, 96)
        save_kwargs["optimize"] = True
    img.save(out_path, **save_kwargs)
    return out_path
