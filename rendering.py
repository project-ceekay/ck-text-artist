"""CK Text Artist — Rendering Engine, Filters, and Font Scanning."""
import math
import os
import re
import sys
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from fontTools.ttLib import TTFont, TTCollection
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.misc.arrayTools import unionRect
from fontTools.misc.transform import Transform

DEFAULT_TEXT = "Insert text here..."
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"), ("All files", "*.*")]
PREVIEW_BGS = {"Light": "#f0f0f0", "Dark": "#2b2b2b", "Checker": "#e6e6e6"}
PAINT_MODES = ["Color", "Gradient", "Texture"]
FIT_MODES = ["Tile", "Cover", "Stretch"]
_R = getattr(Image, "Resampling", Image)
RESAMPLE, BICUBIC = _R.LANCZOS, _R.BICUBIC
MAX_TEXTURE = 2048  # textures are downsized to this on load to keep filters fast
QUALITIES = ["1x", "2x", "3x", "4x"]  # output multiplier for exports and drag and drop
DEFAULT_QUALITY = "4x"
MAX_OUTPUT = 8000  # quality is reduced so the longest side stays under this
PRESET_THUMB = (300, 50)  # max thumbnail size in the Presets tab

PAINT_DEFAULTS = {
    "mode": "Color", "color": "#000000", "color2": "#ffffff", "angle": 0,
    "texture": "", "fit": "Tile", "scale": 100, "rotation": 0,
    "brightness": 100, "contrast": 100, "saturation": 100, "hue": 0, "blur": 0,
    "invert": False, "opacity": 100,
}
FILTER_KEYS = ("brightness", "contrast", "saturation", "hue", "blur", "invert")

def rot_size(w, h, angle):
    r = math.radians(angle)
    c, s = abs(math.cos(r)), abs(math.sin(r))
    return w * c + h * s, w * s + h * c


def checker(size, cell=10):
    w, h = size
    tile = Image.new("RGBA", (cell * 2, cell * 2), "#ffffff")
    d = ImageDraw.Draw(tile)
    d.rectangle((0, 0, cell - 1, cell - 1), fill="#cfcfcf")
    d.rectangle((cell, cell, 2 * cell - 1, 2 * cell - 1), fill="#cfcfcf")
    out = Image.new("RGBA", (w, h))
    for y in range(0, h, cell * 2):
        for x in range(0, w, cell * 2):
            out.paste(tile, (x, y))
    return out


# ------------------------------------------------------------- masks
def dilate(mask, r):
    """Grow an alpha mask outward by r pixels with a near-round (octagon) shape,
    keeping the soft anti-aliased edge. The mask needs at least r pixels of margin."""
    for i in range(r):
        if i % 2 == 0:
            mask = mask.filter(ImageFilter.MaxFilter(3))
        else:
            base = mask
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                mask = ImageChops.lighter(mask, ImageChops.offset(base, dx, dy))
    return mask


def outline_mask(alpha, r):
    """Outline that stays solid around very thin (sub-pixel) parts of the letters:
    the inner part grows from a boosted mask, only the outermost pixel stays soft."""
    soft = dilate(alpha, r)
    solid = dilate(alpha.point(lambda v: min(255, v * 4)), r - 1)
    return ImageChops.lighter(soft, solid)


# ------------------------------------------------------------- paints
_RAW_TEX, _FILTERED_TEX = {}, {}


def filtered_texture(cfg):
    """The texture with its filters applied (cached). Returns None if there is none."""
    path = cfg.get("texture")
    if not path:
        return None
    key = (path,) + tuple(cfg[k] for k in FILTER_KEYS)
    if key in _FILTERED_TEX:
        return _FILTERED_TEX[key]
    src = _RAW_TEX.get(path)
    if src is None:
        with Image.open(path) as im:
            src = im.convert("RGBA")
        src.thumbnail((MAX_TEXTURE, MAX_TEXTURE), RESAMPLE)
        _RAW_TEX[path] = src
    rgb, alpha = src.convert("RGB"), src.getchannel("A")
    if cfg["brightness"] != 100:
        rgb = ImageEnhance.Brightness(rgb).enhance(cfg["brightness"] / 100)
    if cfg["contrast"] != 100:
        rgb = ImageEnhance.Contrast(rgb).enhance(cfg["contrast"] / 100)
    if cfg["saturation"] != 100:
        rgb = ImageEnhance.Color(rgb).enhance(cfg["saturation"] / 100)
    if cfg["hue"]:
        shift = round(cfg["hue"] / 360 * 256)
        h, s, v = rgb.convert("HSV").split()
        rgb = Image.merge("HSV", (h.point(lambda x: (x + shift) % 256), s, v)).convert("RGB")
    if cfg["invert"]:
        rgb = ImageOps.invert(rgb)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    if cfg["blur"]:
        out = out.filter(ImageFilter.GaussianBlur(cfg["blur"]))
    if len(_FILTERED_TEX) > 8:
        _FILTERED_TEX.pop(next(iter(_FILTERED_TEX)))
    _FILTERED_TEX[key] = out
    return out


def gradient_fill(c1, c2, angle, w, h):
    d = max(1, int(math.ceil(math.hypot(w, h))))
    ramp = Image.linear_gradient("L").resize((d, d), BICUBIC).rotate(angle, BICUBIC)
    left, top = (d - w) // 2, (d - h) // 2
    mask = ramp.crop((left, top, left + w, top + h))
    return Image.composite(Image.new("RGBA", (w, h), c2), Image.new("RGBA", (w, h), c1), mask)


def texture_fill(tex, cfg, w, h, scale):
    if cfg["fit"] == "Stretch":
        return tex.resize((w, h), RESAMPLE)
    f = cfg["scale"] / 100
    f *= max(w / tex.width, h / tex.height) if cfg["fit"] == "Cover" else scale
    tw, th = max(2, round(tex.width * f)), max(2, round(tex.height * f))
    t = tex.resize((tw, th), RESAMPLE)
    ang = cfg["rotation"] % 360
    if ang:
        cw = ch = int(math.ceil(math.hypot(w, h))) + 2
    else:
        cw, ch = w, h
    # tiles are centred on the text box; build one row, then stack rows (fast)
    strip = Image.new("RGBA", (cw, th))
    x = int(round((cw / 2 - tw / 2) % tw - tw))
    while x < cw:
        strip.paste(t, (x, 0))
        x += tw
    canvas = Image.new("RGBA", (cw, ch))
    y = int(round((ch / 2 - th / 2) % th - th))
    while y < ch:
        canvas.paste(strip, (0, y))
        y += th
    if ang:
        canvas = canvas.rotate(ang, BICUBIC)
        left, top = (cw - w) // 2, (ch - h) // 2
        canvas = canvas.crop((left, top, left + w, top + h))
    return canvas


def make_paint(cfg, size, scale=1.0):
    """A full-size RGBA image of the paint (colour, gradient or texture)."""
    w, h = max(1, size[0]), max(1, size[1])
    if cfg["mode"] == "Gradient":
        return gradient_fill(cfg["color"], cfg["color2"], cfg["angle"], w, h)
    if cfg["mode"] == "Texture":
        tex = filtered_texture(cfg)  # may raise if the file is missing
        if tex is not None:
            return texture_fill(tex, cfg, w, h, scale)
    return Image.new("RGBA", (w, h), cfg["color"])


def svg_pen(glyphs):
    try:
        return SVGPathPen(glyphs, ntos=lambda v: ("%.2f" % v).rstrip("0").rstrip("."))
    except TypeError:  # older fontTools without ntos
        return SVGPathPen(glyphs)


# ---------------------------------------------------------- font scanning
def font_dirs():
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        return [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    if sys.platform == "darwin":
        return ["/System/Library/Fonts", "/Library/Fonts", os.path.join(home, "Library/Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts",
            os.path.join(home, ".fonts"), os.path.join(home, ".local/share/fonts")]


def describe_font(font):
    name = font["name"]
    family = name.getDebugName(16) or name.getDebugName(1)
    sub = (name.getDebugName(17) or name.getDebugName(2) or "").lower()
    mac = font["head"].macStyle if "head" in font else 0
    sel = font["OS/2"].fsSelection if "OS/2" in font else 0
    bold = bool(mac & 1 or sel & 32)
    italic = bool(mac & 2 or sel & 1)
    key = ("bold" if bold else "") + ("italic" if italic else "") or "regular"
    plain = sub in ("regular", "normal", "book", "roman", "bold", "italic",
                    "bold italic", "oblique", "bold oblique")
    return family, key, plain


def scan_fonts():
    """Returns {family: {style_key: [path, index, is_plain]}}"""
    fams = {}

    def add(font, path, idx):
        if "name" not in font:
            return
        family, key, plain = describe_font(font)
        if not family:
            return
        slot = fams.setdefault(family, {})
        if key not in slot or (plain and not slot[key][2]):
            slot[key] = [path, idx, plain]

    for d in font_dirs():
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith(FONT_EXTS):
                    continue
                path = os.path.join(root, fn)
                try:
                    if fn.lower().endswith((".ttc", ".otc")):
                        for i, f in enumerate(TTCollection(path, lazy=True).fonts):
                            add(f, path, i)
                    else:
                        f = TTFont(path, lazy=True, fontNumber=0)
                        add(f, path, 0)
                        f.close()
                except Exception:
                    continue
    return fams


# ------------------------------------------------------------- look & feel
