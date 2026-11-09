"""CK Text Artist.

Requires:  pip install pillow fonttools tkinterdnd2
           (tkinterdnd2 is optional; it enables dragging the preview out as a file)

Shortcuts: Ctrl+Z undo, Ctrl+Y / Ctrl+Shift+Z redo, Ctrl+E export,
           Ctrl+S save preset, Ctrl+O show presets
"""
import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import (Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont,
                 ImageOps, ImageTk)
from fontTools.ttLib import TTFont, TTCollection
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.misc.arrayTools import unionRect
from fontTools.misc.transform import Transform

try:  # drag-out support; the app works without it
    from tkinterdnd2 import TkinterDnD, DND_FILES, COPY
except ImportError:
    TkinterDnD = None

APP_NAME = "CK Text Artist"
VERSION = "1.0.0"
REPO = "project-ceekay/ck-text-artist"
SETTINGS_NAME = "Text Artist"  # %APPDATA%\CK Suite\<this>
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


# ---------------------------------------------------------------- settings & updates
# Each program defines APP_NAME, VERSION, REPO ("owner/name") and SETTINGS_NAME before this block.
import re as _re
import tempfile as _tempfile
import urllib.error as _urlerror
import urllib.request as _urlrequest
import webbrowser as _webbrowser


def settings_file(old_paths=(), name="config.json"):
    """data_dir()/name; the first existing old location is copied over once."""
    path = os.path.join(data_dir(), name)
    if not os.path.exists(path):
        for old in old_paths:
            if old and os.path.isfile(old):
                try:
                    shutil.copy2(old, path)
                except OSError:
                    pass
                break
    return path


def version_tuple(text):
    return tuple(int(x) for x in _re.findall(r"\d+", text)[:3]) or (0,)


def latest_release():
    """{'version', 'notes', 'url', 'page'} of the newest GitHub release; {} if none yet."""
    req = _urlrequest.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                              headers={"User-Agent": "CK-Suite-Updater", "Accept": "application/vnd.github+json"})
    try:
        with _urlrequest.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except _urlerror.HTTPError as e:
        if e.code == 404:
            return {}
        raise
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
    return {"version": data.get("tag_name", "").lstrip("v"), "notes": data.get("body") or "",
            "url": asset["browser_download_url"] if asset else None, "page": data.get("html_url")}


class Updater:
    """About box + daily update check + one-click install, living in the footer `bar`.
    status(text, kind) / progress(0..100 or None) report to the window; quit() saves and closes it."""

    def __init__(self, root, bar, cfg, make_icon, status, progress, quit):
        self.root, self.cfg, self.make_icon = root, cfg, make_icon
        self.status, self.progress, self.quit = status, progress, quit
        self.auto = tk.BooleanVar(value=cfg.get("auto_update", True))
        self.release = None
        self.about_lbl = None
        ttk.Style(root).configure("Link.TLabel", foreground="#0067c0")
        about = ttk.Label(bar, text=f"About · v{VERSION}", style="Link.TLabel", cursor="hand2")
        about.pack(side="right", padx=(0, self._px(16)))
        about.bind("<Button-1>", lambda e: self.show_about())
        self.link = ttk.Label(bar, text="", style="Link.TLabel", cursor="hand2", font=("Segoe UI Semibold", 10))
        self.link.bind("<Button-1>", lambda e: self.offer())
        if self.auto.get() and time.time() - cfg.get("last_update_check", 0) > 20 * 3600:
            root.after(2500, lambda: self.check(quiet=True))

    def state(self):
        return {"auto_update": self.auto.get(), "last_update_check": self.cfg.get("last_update_check", 0)}

    def _px(self, n):
        return round(n * self.root.winfo_fpixels("1i") / 96)

    def _dialog(self, title):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=self._px(18))
        body.pack(fill="both", expand=True)
        return win, body

    def _show(self, win):
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + self._px(110)}+{self.root.winfo_rooty() + self._px(110)}")
        win.deiconify()
        win.grab_set()

    def show_about(self):
        win, body = self._dialog(f"About {APP_NAME}")
        P = self._px
        self._icon = ImageTk.PhotoImage(self.make_icon(P(56)))
        ttk.Label(body, image=self._icon).grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, P(14)))
        ttk.Label(body, text=APP_NAME, font=("Segoe UI Semibold", 13)).grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Version {VERSION}  ·  CK Suite  ·  © CK Software  ·  MIT License",
                  foreground="#5f6368").grid(row=1, column=1, sticky="w")
        link = ttk.Label(body, text=f"github.com/{REPO}", style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        link.bind("<Button-1>", lambda e: _webbrowser.open(f"https://github.com/{REPO}"))
        ttk.Separator(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=P(14))
        ttk.Checkbutton(body, text="Check for updates automatically (once a day)", variable=self.auto).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self.about_lbl = ttk.Label(body, text="", foreground="#5f6368")
        self.about_lbl.grid(row=5, column=0, columnspan=2, sticky="w", pady=(P(8), 0))
        bar = ttk.Frame(body)
        bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(P(14), 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Check for Updates Now",
                   command=lambda: self.check(quiet=False)).pack(side="right", padx=(0, P(8)))
        self._show(win)

    def _about(self, text):
        if self.about_lbl is not None and self.about_lbl.winfo_exists():
            self.about_lbl.configure(text=text)

    def check(self, quiet):
        self._about("Checking…")
        result = {}

        def work():
            try:
                result["rel"] = latest_release()
            except Exception:
                result["rel"] = None

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def done():
            if t.is_alive():
                self.root.after(200, done)
                return
            self.cfg["last_update_check"] = time.time()
            rel = result.get("rel")
            if rel is None:
                self._about("Couldn't reach GitHub. Check your internet connection.")
            elif not rel:
                self._about(f"No releases published yet. You have version {VERSION}.")
            elif version_tuple(rel["version"]) > version_tuple(VERSION):
                self.release = rel
                self.link.configure(text=f"⬆ Version {rel['version']} is available")
                self.link.pack(side="right", padx=(0, self._px(16)))
                self._about(f"Version {rel['version']} is available (you have {VERSION}).")
                if not quiet:
                    self.offer()
            else:
                self._about(f"You have the latest version ({VERSION}).")

        done()

    def offer(self):
        rel = self.release
        if not rel:
            return
        win, body = self._dialog("Update available")
        P = self._px
        ttk.Label(body, text=f"{APP_NAME} {rel['version']} is available", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(body, text=f"You have version {VERSION}. What's new:",
                  foreground="#5f6368").pack(anchor="w", pady=(P(2), P(8)))
        notes = tk.Text(body, height=10, width=64, wrap="word", font=("Segoe UI", 10), relief="flat",
                        highlightthickness=1, highlightbackground="#d9d9d9", padx=P(8), pady=P(6))
        notes.insert("1.0", rel["notes"].strip() or "(no release notes)")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(P(12), 0))
        if getattr(sys, "frozen", False) and rel["url"]:
            ttk.Button(bar, text="Update Now", default="active",
                       command=lambda: (win.destroy(), self.install(rel))).pack(side="right")
        else:  # running from source: send them to the release page
            ttk.Button(bar, text="Open Download Page", default="active",
                       command=lambda: (win.destroy(), _webbrowser.open(rel["page"]))).pack(side="right")
        ttk.Button(bar, text="Later", command=win.destroy).pack(side="right", padx=(0, P(8)))
        self._show(win)

    def install(self, rel):
        """Download the installer, run it quietly and close; the installer reopens the program."""
        if getattr(self.root, "busy", False):
            messagebox.showinfo(APP_NAME, "Wait for the current job to finish, then update.")
            return
        self.status(f"Downloading version {rel['version']}…", "muted")
        self.progress(0)
        dest = os.path.join(_tempfile.gettempdir(), "CK Suite updates", os.path.basename(rel["url"]))
        st = {"frac": 0.0}

        def work():
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                req = _urlrequest.Request(rel["url"], headers={"User-Agent": "CK-Suite-Updater"})
                with _urlrequest.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                    total, got = int(r.headers.get("Content-Length") or 0), 0
                    while chunk := r.read(256 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        st["frac"] = got / total if total else 0
                st["ok"] = True
            except Exception as e:
                st["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def watch():
            self.progress(st["frac"] * 100)
            if t.is_alive():
                self.root.after(150, watch)
                return
            self.progress(None)
            if not st.get("ok"):
                self.status(f"Download failed: {st.get('err')}", "error")
                return
            try:
                subprocess.Popen([dest, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
            except OSError as e:
                self.status(f"Couldn't start the installer: {e}", "error")
                return
            self.quit()

        watch()


# ---------------------------------------------------------------- helpers
def app_path():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Per-user settings folder: %APPDATA%\\CK Suite\\<SETTINGS_NAME>. The first time, settings,
    presets and the font cache are copied over from the earlier Text Editor folders."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "CK Suite", SETTINGS_NAME)
    try:
        if not os.path.exists(path):
            for old in (os.path.join("CK Suite", "Text Editor"), "CKTextEditor"):  # earlier names
                if os.path.isdir(os.path.join(base, old)):
                    shutil.copytree(os.path.join(base, old), path)  # presets, settings, font cache
                    break
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return app_path()


def drag_dir():
    """Temp folder for files handed out by drag and drop."""
    return os.path.join(tempfile.gettempdir(), "CKTextEditor drag")


def safe_filename(text, fallback="text"):
    name = re.sub(r"[^\w\- ]+", "", text.split("\n")[0]).strip(" .")[:40] or fallback
    if re.fullmatch(r"(?i)con|prn|aux|nul|com\d|lpt\d", name):  # reserved on Windows
        name += "_"
    return name


def normalize_hex(value):
    v = value.strip()
    if v and not v.startswith("#"):
        v = "#" + v
    if not HEX_RE.match(v):
        return None
    if len(v) == 4:
        v = "#" + "".join(c * 2 for c in v[1:])
    return v.lower()


def open_folder(folder):
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.call(["open", folder])
        else:
            subprocess.call(["xdg-open", folder])
    except Exception:
        pass


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
ACCENT = "#0067c0"      # Windows 11 accent blue
ERROR = "#c42b1c"
SUCCESS = "#0f7b0f"
WARN = "#9d5d00"
MUTED = "#5f6368"
HEADER_BG = "#ffffff"
BORDER = "#d9d9d9"
HOVER = "#e5f1fb"
# set_status() callers use these names/colours; map them onto the palette
STATUS_COLORS = {"gray": MUTED, "#2ecc71": SUCCESS, "#e74c3c": ERROR, "#f39c12": WARN}
SCALE = 1.0  # screen scaling (1.0 at 100%); set when the window is created


def px(n):
    """n pixels at 100% scaling, converted to this screen's scaling."""
    return round(n * SCALE)


def enable_dpi_awareness():
    """Tell Windows we draw at real resolution, so text is sharp instead of stretched."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def make_icon(size=64):
    """A styled "T" on blue, drawn so the program needs no .ico file."""
    s = size / 64
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4 * s, 4 * s, 60 * s, 60 * s], radius=12 * s, fill=ACCENT)
    for (ox, oy), col in (((3, 3), "#003e74"), ((0, 0), "white")):  # shadow, then letter
        d.rectangle([(15 + ox) * s, (14 + oy) * s, (47 + ox) * s, (22 + oy) * s], fill=col)
        d.rectangle([(27 + ox) * s, (14 + oy) * s, (35 + ox) * s, (49 + oy) * s], fill=col)
    return im


def section(parent, text):
    """A titled group box, stacked down the tab."""
    box = ttk.Labelframe(parent, text=text, style="Section.TLabelframe", padding=px(10))
    box.pack(fill="x", pady=(0, px(10)))
    return box


def hint(parent, text, **pack):
    lbl = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=px(350), justify="left")
    lbl.pack(anchor="w", **(pack or {"pady": (px(2), 0)}))
    return lbl


def segmented(parent, values, var, command=None):
    """A row of toggle buttons that work like radio buttons."""
    row = ttk.Frame(parent)
    for v in values:
        ttk.Radiobutton(row, text=v, value=v, variable=var, style="Toolbutton",
                        command=command).pack(side="left", padx=(0, px(2)))
    return row


def styled_listbox(parent, height):
    return tk.Listbox(parent, height=height, exportselection=False, activestyle="none", relief="flat",
                      borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=BORDER, font=("Segoe UI", 10), bg="white", fg="#1a1a1a",
                      selectbackground=ACCENT, selectforeground="white")


class ScrollFrame(ttk.Frame):
    """A frame whose contents scroll vertically. Put widgets in .body."""

    def __init__(self, master, padding=0):
        super().__init__(master)
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=bg,
                                yscrollincrement=px(20))
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)

        def set_bar(lo, hi):  # the scrollbar only shows when the contents don't fit
            if float(lo) <= 0 and float(hi) >= 1:
                bar.pack_forget()
            elif not bar.winfo_ismapped():
                bar.pack(side="right", fill="y", before=self.canvas)
            bar.set(lo, hi)

        self.canvas.configure(yscrollcommand=set_bar)
        self.body = ttk.Frame(self.canvas, padding=padding)
        win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))

    def scroll(self, delta):
        if self.canvas.yview() != (0.0, 1.0):  # only when the contents don't fit
            steps = -delta / 120 * 3
            self.canvas.yview_scroll(int(steps) or (1 if steps > 0 else -1), "units")


class SliderRow(ttk.Frame):
    """Label, slider and a number box you can type into. Double-click the slider to reset."""

    def __init__(self, master, text, lo, hi, value, on_change):
        super().__init__(master)
        self.on_change = on_change
        self.lo, self.hi, self.default, self.value = lo, hi, value, value
        self._setting = False
        ttk.Label(self, text=text, width=17).grid(row=0, column=0, sticky="w")
        self.scale = ttk.Scale(self, from_=lo, to=hi, command=self._moved)
        self.scale.grid(row=0, column=1, sticky="ew", padx=(px(4), px(8)))
        self.spin_var = tk.StringVar(value=str(value))
        spin = ttk.Spinbox(self, from_=lo, to=hi, textvariable=self.spin_var, width=5, command=self._typed)
        spin.grid(row=0, column=2)
        spin.bind("<Return>", self._typed)
        spin.bind("<FocusOut>", self._typed)
        self.scale.bind("<Double-Button-1>", lambda e: self.after_idle(self.reset))
        self.columnconfigure(1, weight=1)
        self.set(value)

    def _moved(self, v):
        if self._setting:
            return
        n = int(round(float(v)))
        if abs(float(v) - n) > 1e-9:
            self._set_scale(n)  # snap to whole numbers
        if n != self.value:
            self.value = n
            self.spin_var.set(str(n))
            self.on_change()

    def _typed(self, *_):
        try:
            n = max(self.lo, min(self.hi, int(round(float(self.spin_var.get())))))
        except ValueError:
            self.spin_var.set(str(self.value))
            return
        if n != self.value:
            self.set(n)
            self.on_change()
        else:
            self.spin_var.set(str(n))

    def _set_scale(self, v):
        self._setting = True
        try:
            self.scale.set(v)
        finally:
            self._setting = False

    def reset(self):
        if self.value != self.default:
            self.set(self.default)
            self.on_change()

    def get(self):
        return self.value

    def set(self, v):
        self.value = int(round(v))
        self._set_scale(self.value)
        self.spin_var.set(str(self.value))


class ColorField(ttk.Frame):
    """Colour square (click to pick), hex entry with validation, and optional save button."""

    def __init__(self, master, initial, on_change, on_star=None):
        super().__init__(master)
        self.on_change = on_change
        self.value = normalize_hex(initial) or "#000000"
        self.swatch = tk.Label(self, bg=self.value, width=3, cursor="hand2",
                               highlightthickness=1, highlightbackground="#8a8a8a")
        self.swatch.pack(side="left", ipady=px(1))
        self.swatch.bind("<Button-1>", lambda e: self.pick())
        self.var = tk.StringVar(value=self.value)
        self.entry = ttk.Entry(self, textvariable=self.var, width=10)
        self.entry.pack(side="left", padx=(px(6), 0))
        ttk.Button(self, text="Pick…", width=6, command=self.pick).pack(side="left", padx=(px(6), 0))
        if on_star:
            ttk.Button(self, text="★ Save", width=7, command=on_star).pack(side="left", padx=(px(4), 0))
        self.var.trace_add("write", self._changed)

    def _changed(self, *_):
        v = normalize_hex(self.var.get())
        if v:
            self.value = v
            self.swatch.configure(bg=v)
            self.entry.configure(style="TEntry")
            self.on_change()
        else:
            self.entry.configure(style="Invalid.TEntry")

    def pick(self):
        c = colorchooser.askcolor(initialcolor=self.value, parent=self)[1]
        if c:
            self.set(c)

    def set(self, v):
        self.var.set(v)


class PresetCard(tk.Frame):
    """A saved preset: name and thumbnail (click to load) plus a delete button."""

    def __init__(self, master, name, thumb, on_pick, on_delete):
        super().__init__(master, bg="white", highlightthickness=1, highlightbackground=BORDER, cursor="hand2")
        body = tk.Frame(self, bg="white")
        body.pack(side="left", fill="x", expand=True, padx=px(10), pady=px(8))
        self.parts = [self, body]
        lbl = tk.Label(body, text=name, bg="white", fg="#1a1a1a", font=("Segoe UI Semibold", 10), anchor="w")
        lbl.pack(anchor="w")
        self.parts.append(lbl)
        if thumb is not None:
            pic = tk.Label(body, image=thumb, bg="white")
            pic.image = thumb
            pic.pack(anchor="w", pady=(px(4), 0))
            self.parts.append(pic)
        ttk.Button(self, text="Delete", width=7, command=lambda: on_delete(name)).pack(side="right", padx=px(8))
        for w in self.parts:
            w.bind("<Button-1>", lambda e: on_pick(name))
            w.bind("<Enter>", lambda e: self._paint(HOVER))
            w.bind("<Leave>", self._leave)

    def _paint(self, colour):
        for w in self.parts:
            w.configure(bg=colour)

    def _leave(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            w = w.master
        if w is None:
            self._paint("white")


class PaintPanel(ttk.Frame):
    """Colour / gradient / texture paint with texture filters, used for fill and outline."""

    def __init__(self, master, color, on_change, on_star=None):
        super().__init__(master)
        self.on_change = on_change
        self.texture = ""
        self.thumb_job = None
        self.thumb_img = None

        self.mode = tk.StringVar(value="Color")
        segmented(self, PAINT_MODES, self.mode, lambda: self._mode_changed()).pack(anchor="w", pady=(0, px(8)))

        # colour (also gradient colour 1)
        self.colbox = ttk.Frame(self)
        self.col_lbl = ttk.Label(self.colbox, text="Color")
        self.col_lbl.pack(anchor="w", pady=(0, px(2)))
        self.color = ColorField(self.colbox, color, self._changed, on_star=on_star)
        self.color.pack(anchor="w")
        self.extra = ttk.Frame(self.colbox)  # e.g. colour favourites
        self.extra.pack(anchor="w", fill="x", pady=(px(6), 0))

        # gradient
        self.grad = ttk.Frame(self)
        ttk.Label(self.grad, text="Second color").pack(anchor="w", pady=(px(6), px(2)))
        self.color2 = ColorField(self.grad, "#ffffff", self._changed)
        self.color2.pack(anchor="w")
        self.angle = SliderRow(self.grad, "Angle", 0, 360, 0, self._changed)
        self.angle.pack(fill="x", pady=(px(8), 0))

        # texture
        self.tex = ttk.Frame(self)
        row = ttk.Frame(self.tex)
        row.pack(fill="x")
        ttk.Button(row, text="Load Image…", command=self.load_texture).pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear_texture).pack(side="left", padx=(px(6), 0))
        self.tex_name = ttk.Label(self.tex, text="No image loaded", style="Muted.TLabel",
                                  wraplength=px(340), justify="left")
        self.tex_name.pack(anchor="w", pady=(px(4), 0))
        self.thumb = ttk.Label(self.tex)
        self.thumb.pack(anchor="w", pady=px(4))
        fit_row = ttk.Frame(self.tex)
        fit_row.pack(fill="x", pady=(0, px(6)))
        ttk.Label(fit_row, text="Fit", width=17).pack(side="left")
        self.fit = tk.StringVar(value="Tile")
        segmented(fit_row, FIT_MODES, self.fit, lambda: self._changed()).pack(side="left", padx=(px(4), 0))
        self.sliders = {}
        for key, text, lo, hi in (("scale", "Scale %", 5, 400), ("rotation", "Rotation", 0, 360),
                                  ("brightness", "Brightness %", 0, 200), ("contrast", "Contrast %", 0, 200),
                                  ("saturation", "Saturation %", 0, 200), ("hue", "Hue shift", -180, 180),
                                  ("blur", "Blur", 0, 30)):
            s = SliderRow(self.tex, text, lo, hi, PAINT_DEFAULTS[key], self._changed)
            s.pack(fill="x", pady=px(2))
            self.sliders[key] = s
        bottom = ttk.Frame(self.tex)
        bottom.pack(fill="x", pady=(px(6), 0))
        self.invert = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Invert colors", variable=self.invert,
                        command=self._changed).pack(side="left")
        ttk.Button(bottom, text="Reset Filters", command=self.reset_filters).pack(side="right")

        self.sep = ttk.Separator(self)
        self.sep.pack(fill="x", pady=(px(10), px(8)))
        self.opacity = SliderRow(self, "Opacity %", 0, 100, 100, self._changed)
        self.opacity.pack(fill="x")
        self._mode_changed(notify=False)

    # --- state
    def get(self):
        d = {"mode": self.mode.get(), "color": self.color.value, "color2": self.color2.value,
             "angle": self.angle.get(), "texture": self.texture, "fit": self.fit.get(),
             "invert": self.invert.get(), "opacity": self.opacity.get()}
        d.update({k: s.get() for k, s in self.sliders.items()})
        return d

    def set(self, d):
        d = dict(PAINT_DEFAULTS, **(d or {}))
        self.color.set(d["color"])
        self.color2.set(d["color2"])
        self.angle.set(d["angle"])
        self.fit.set(d["fit"] if d["fit"] in FIT_MODES else "Tile")
        self.invert.set(bool(d["invert"]))
        self.opacity.set(d["opacity"])
        for k, s in self.sliders.items():
            s.set(d[k])
        self.texture = d["texture"] if d["texture"] and os.path.exists(d["texture"]) else ""
        self.tex_name.configure(text=os.path.basename(self.texture) if self.texture else "No image loaded")
        self.mode.set(d["mode"] if d["mode"] in PAINT_MODES else "Color")
        self._mode_changed(notify=False)
        self._update_thumb()

    # --- events
    def _mode_changed(self, notify=True):
        m = self.mode.get()
        for f in (self.colbox, self.grad, self.tex):
            f.pack_forget()
        anchor = self.sep  # everything goes above the Opacity section
        if m in ("Color", "Gradient"):
            self.col_lbl.configure(text="Color" if m == "Color" else "First color")
            self.colbox.pack(fill="x", before=anchor)
        if m == "Gradient":
            self.grad.pack(fill="x", before=anchor)
        if m == "Texture":
            self.tex.pack(fill="x", before=anchor)
        if notify:
            self.on_change()

    def _changed(self, *_):
        if self.thumb_job:
            self.after_cancel(self.thumb_job)
        self.thumb_job = self.after(150, self._update_thumb)
        self.on_change()

    def load_texture(self):
        path = filedialog.askopenfilename(title="Choose a texture image", filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            with Image.open(path) as im:
                im.verify()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not open image:\n{e}")
            return
        self.texture = os.path.normpath(path)
        self.tex_name.configure(text=os.path.basename(path))
        self._changed()

    def clear_texture(self):
        self.texture = ""
        self.tex_name.configure(text="No image loaded")
        self._changed()

    def reset_filters(self):
        for k in FILTER_KEYS + ("scale", "rotation"):
            if k in self.sliders:
                self.sliders[k].set(PAINT_DEFAULTS[k])
        self.invert.set(False)
        self._changed()

    def _update_thumb(self):
        self.thumb_job = None
        img = None
        if self.texture:
            try:
                img = filtered_texture(self.get()).copy()
                img.thumbnail((px(160), px(110)))
                bg = checker(img.size, 6)
                bg.alpha_composite(img)
                img = bg
            except Exception:
                img = None
        if img is None:
            self.thumb_img = None
            self.thumb.configure(image="")
        else:
            self.thumb_img = ImageTk.PhotoImage(img)
            self.thumb.configure(image=self.thumb_img)


# ------------------------------------------------------------------ app
def smooth_resize(root, settle_ms=120):
    """Keep dragging the window edge smooth. On Windows every Tk widget is its own native window,
    and all of them are moved and repainted on every resize step, so live resizing a full window
    lags. While the size is changing, the window shows a still picture of itself instead (the
    bottom-packed footer pinned to the bottom edge) and the real widgets are hidden; settle_ms
    after the last change they come back, laid out once for the new size."""
    if not sys.platform.startswith("win"):
        return
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    user32.GetDC.restype = H
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, H]
    user32.PrintWindow.argtypes = [wintypes.HWND, H, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = H
    gdi32.CreateCompatibleDC.argtypes = [H]
    gdi32.CreateCompatibleBitmap.restype = H
    gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = H
    gdi32.SelectObject.argtypes = [H, H]
    gdi32.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [H]
    gdi32.DeleteDC.argtypes = [H]

    def capture(w, h):
        """The window's client area as Windows composed it, even where other windows cover it."""
        hwnd = int(root.wm_frame(), 16)
        hdc = user32.GetDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        try:
            if not user32.PrintWindow(hwnd, mem, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
                return None
            header = (ctypes.c_uint32 * 10)(40, w, ctypes.c_uint32(-h).value, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)  # 32-bit top-down BGRX rows
            if not gdi32.GetDIBits(mem, bmp, 0, h, buf, header, 0):
                return None
            return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(hwnd, hdc)

    bg = ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0"
    state = {"size": None, "frozen": None, "shots": [], "job": None, "zoomed": False}
    big = round(200 * root.winfo_fpixels("1i") / 96)  # a jump this large (200 px at 100%) is no drag

    def thaw():
        state["job"] = None
        frozen, state["frozen"] = state["frozen"], None
        for w, info in frozen or ():
            w.pack(**info)  # in the original order, so the layout is exactly as before
        for shot in state["shots"]:
            shot.destroy()
        state["shots"] = []

    def freeze(old_w, old_h):
        slaves = [(w, w.pack_info()) for w in root.pack_slaves() if w.winfo_ismapped()]
        if not slaves:
            return
        w, h = root.winfo_width(), root.winfo_height()
        try:
            shown = capture(w, h)
        except (OSError, ValueError, tk.TclError):
            shown = None
        if shown is None:
            return  # just resize normally
        # the sections are still laid out for the old size; a strip lost to shrinking shows as background
        image = Image.new("RGB", (old_w, old_h), tuple(c >> 8 for c in root.winfo_rgb(bg)))
        image.paste(shown.crop((0, 0, min(w, old_w), min(h, old_h))))
        # everything from the first bottom-packed section down stays pinned to the bottom edge
        split = min([w.winfo_y() for w, info in slaves if info.get("side") == "bottom"] or [old_h])
        for box, place in (((0, 0, old_w, split), dict(x=0, y=0, anchor="nw")),
                           ((0, split, old_w, old_h), dict(x=0, rely=1, anchor="sw"))):
            if box[3] > box[1]:
                photo = ImageTk.PhotoImage(image.crop(box))
                shot = tk.Label(root, image=photo, bd=0, highlightthickness=0, bg=bg)
                shot.image = photo
                shot.place(**place)
                state["shots"].append(shot)
        for w, info in slaves:
            info.pop("in", None)
            w.pack_forget()
        state["frozen"] = slaves

    def on_configure(e):
        if e.widget is not root:
            return
        size = (e.width, e.height)
        old = state["size"]
        state["size"] = size
        if old in (None, size):
            return
        zoomed, was_zoomed = root.state() == "zoomed", state["zoomed"]
        state["zoomed"] = zoomed
        if state["frozen"] is None:
            # one-off jumps (maximize, restore, snapping) just lay out once, as normal
            if zoomed or was_zoomed or abs(size[0] - old[0]) > big or abs(size[1] - old[1]) > big:
                return
            freeze(*old)  # the window still shows the layout for the previous size
        if state["job"]:
            root.after_cancel(state["job"])
        state["job"] = root.after(settle_ms, thaw)

    root.configure(bg=bg)  # what shows in newly uncovered space while frozen
    root.bind("<Configure>", on_configure, add="+")


class FontApp(tk.Tk):
    def __init__(self):
        global SCALE
        enable_dpi_awareness()
        super().__init__()
        SCALE = self.winfo_fpixels("1i") / 96
        self.dnd_ok = False
        if TkinterDnD is not None:
            try:
                TkinterDnD._require(self)  # load the tkdnd extension into this window
                self.dnd_ok = True
            except Exception:
                pass
        self.drag_key = None  # state of the last rendered drag file, to reuse it
        self.drag_path = ""
        self.drag_info = ""
        self.title(APP_NAME)
        h = min(px(860), self.winfo_screenheight() - px(80))
        self.geometry(f"{px(1300)}x{h}")
        self.minsize(px(1000), min(px(620), h))
        self.icon = ImageTk.PhotoImage(make_icon())
        self.iconphoto(True, self.icon)
        self.setup_style()

        self.config_file = os.path.join(data_dir(), "config.json")
        self.cache_file = os.path.join(data_dir(), "font_cache.json")
        self.prefs = self.load_prefs()
        self.presets_file = os.path.join(data_dir(), "presets.json")
        self.presets = self.load_presets()

        self.families = {}
        self.family_names = []
        self.matches = []
        self.selected_font = self.prefs.get("last_font", "Arial")
        self.font_cache = {}
        self.font_note = ""
        self.paint_note = ""
        self.status_is_note = False
        self.preview_job = None
        self.filter_job = None
        self.scan_result = None
        self.tk_img = None
        # undo / redo
        self.history, self.hist_i = [], -1
        self.restoring = False
        self.snap_job = None

        self.build_ui()
        smooth_resize(self)
        self.bind_shortcuts()
        self.load_fonts()
        self.after(600, self.snapshot)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        if sys.platform.startswith("win"):
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
                try:
                    tkfont.nametofont(name).configure(family="Segoe UI", size=10)
                except tk.TclError:
                    pass
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(px(20), px(6)))
        style.configure("Footer.TButton", padding=(px(14), px(6)))
        style.configure("Toolbutton", padding=(px(10), px(3)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Invalid.TEntry", foreground=ERROR)
        style.configure("TNotebook.Tab", padding=(px(10), px(4)))

    # ---------------------------------------------------------- UI
    def slider(self, parent, text, lo, hi, value):
        s = SliderRow(parent, text, lo, hi, value, self.schedule_preview)
        s.pack(fill="x", pady=px(2))
        return s

    def build_ui(self):
        # header band
        header = tk.Frame(self, bg=HEADER_BG)
        header.pack(fill="x")
        tk.Label(header, text="Text Artist", bg=HEADER_BG, fg="#1a1a1a",
                 font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=px(20), pady=(px(14), 0))
        tk.Label(header, text="Style text with any installed font, fills, outlines and shadows, "
                              "then export it as PNG, JPG or SVG.",
                 bg=HEADER_BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=px(20), pady=(0, px(14)))
        ttk.Separator(self).pack(fill="x")

        self.build_footer()  # packed before the body so it always stays visible
        ttk.Separator(self).pack(side="bottom", fill="x")

        body = ttk.Frame(self, padding=(px(16), px(12), px(16), px(12)))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=px(450))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self.tab_frames = {}
        for name, build in (("Text", self.build_text_tab), ("Layout", self.build_layout_tab),
                            ("Fill", self.build_fill_tab), ("Outline", self.build_outline_tab),
                            ("Effects", self.build_effects_tab), ("Presets", self.build_presets_tab)):
            sf = ScrollFrame(self.tabs, padding=(px(10), px(10), px(6), px(4)))
            self.tabs.add(sf, text=name)
            self.tab_frames[name] = sf
            build(sf.body)
        self.build_main(body)

        # the mouse wheel scrolls whichever tab is under the pointer; number boxes and
        # drop-downs shouldn't change value just because the wheel passes over them
        self.bind_all("<MouseWheel>", self.on_wheel)
        for cls in ("TSpinbox", "TCombobox"):
            self.unbind_class(cls, "<MouseWheel>")

    def on_wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if isinstance(w, (tk.Listbox, tk.Text)):
                return  # these scroll themselves
            if isinstance(w, ScrollFrame):
                w.scroll(e.delta)
                return
            w = w.master

    def select_tab(self, name):
        self.tabs.select(self.tab_frames[name])

    def build_text_tab(self, t):
        s = section(t, "Text")
        self.text_box = tk.Text(s, height=5, wrap="word", font=("Segoe UI", 11), relief="flat",
                                padx=px(6), pady=px(4), highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT)
        self.text_box.insert("1.0", DEFAULT_TEXT)
        self.text_box.pack(fill="x")
        self.text_box.bind("<KeyRelease>", self.schedule_preview)
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(8), 0))
        ttk.Label(row, text="Alignment").pack(side="left", padx=(0, px(8)))
        self.align_var = tk.StringVar(value="Left")
        segmented(row, ["Left", "Center", "Right"], self.align_var, self.schedule_preview).pack(side="left")

        s = section(t, "Font")
        row = ttk.Frame(s)
        row.pack(fill="x")
        ttk.Label(row, text="Search").pack(side="left", padx=(0, px(8)))
        self.search_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", self.schedule_filter)
        lb_row = ttk.Frame(s)
        lb_row.pack(fill="x", pady=px(8))
        self.listbox = styled_listbox(lb_row, 10)
        sb = ttk.Scrollbar(lb_row, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_font_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self.add_font_fav(self.selected_font))
        row = ttk.Frame(s)
        row.pack(fill="x")
        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Bold", variable=self.bold_var,
                        command=self.schedule_preview).pack(side="left")
        ttk.Checkbutton(row, text="Italic", variable=self.italic_var,
                        command=self.schedule_preview).pack(side="left", padx=(px(12), 0))
        self.btn_rescan = ttk.Button(row, text="Rescan Fonts", command=self.rescan)
        self.btn_rescan.pack(side="right")
        ttk.Button(row, text="★ Favorite", command=lambda: self.add_font_fav(self.selected_font)).pack(
            side="right", padx=(0, px(6)))

        s = section(t, "Favorite fonts")
        self.fav_list = styled_listbox(s, 5)
        self.fav_list.pack(fill="x")
        self.fav_list.bind("<<ListboxSelect>>", self.on_fav_select)
        self.fav_list.bind("<Delete>", lambda e: self.remove_selected_fav())
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(6), 0))
        hint(row, "Double-click a font above to add it here.", side="left")
        ttk.Button(row, text="Remove", command=self.remove_selected_fav).pack(side="right")
        self.refresh_fav_ui()

    def build_layout_tab(self, t):
        s = section(t, "Size & spacing")
        self.size = self.slider(s, "Size", 10, 1500, 100)
        self.char = self.slider(s, "Character spacing", -20, 100, 0)
        self.line = self.slider(s, "Line spacing", -50, 150, 0)
        self.pad = self.slider(s, "Padding", 0, 200, 0)
        s = section(t, "Squash & stretch")
        self.stretch_x = self.slider(s, "Stretch width %", 10, 300, 100)
        self.stretch_y = self.slider(s, "Stretch height %", 10, 300, 100)
        ttk.Button(s, text="Reset Stretch", command=self.reset_stretch).pack(anchor="e", pady=(px(6), 0))
        s = section(t, "Rotation")
        self.rotation = self.slider(s, "Rotate (degrees)", -180, 180, 0)
        ttk.Button(s, text="Reset Rotation",
                   command=lambda: (self.rotation.set(0), self.schedule_preview())).pack(anchor="e", pady=(px(6), 0))
        hint(t, "Tip: double-click any slider to reset it, or type an exact value in its box.")

    def build_fill_tab(self, t):
        s = section(t, "Text fill")
        self.fill = PaintPanel(s, self.prefs.get("last_color", "#000000"), self.schedule_preview,
                               on_star=self.add_color_fav)
        self.fill.pack(fill="x")
        self.color_fav_frame = self.fill.extra
        self.refresh_color_favs()

    def build_outline_tab(self, t):
        s = section(t, "Outline")
        self.outline = self.slider(s, "Outline width", 0, 30, 0)
        ttk.Separator(s).pack(fill="x", pady=px(8))
        self.stroke = PaintPanel(s, "#ffffff", self.schedule_preview)
        self.stroke.pack(fill="x")

    def build_effects_tab(self, t):
        s = section(t, "Drop shadow / glow")
        self.sh_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(s, text="Enable shadow", variable=self.sh_on,
                        command=self.schedule_preview).pack(anchor="w", pady=(0, px(6)))
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(0, px(6)))
        ttk.Label(row, text="Color", width=17).pack(side="left")
        self.sh_color = ColorField(row, "#000000", self.schedule_preview)
        self.sh_color.pack(side="left", padx=(px(4), 0))
        self.sh_op = self.slider(s, "Opacity %", 0, 100, 60)
        self.sh_dx = self.slider(s, "Offset X", -100, 100, 8)
        self.sh_dy = self.slider(s, "Offset Y", -100, 100, 8)
        self.sh_blur = self.slider(s, "Blur", 0, 50, 6)
        hint(s, "Tip: offset 0 with a bright color and some blur makes a glow.", pady=(px(6), 0))

        s = section(t, "Background")
        self.bg_mode = tk.StringVar(value="Transparent")
        segmented(s, ["Transparent", "Solid"], self.bg_mode, self.schedule_preview).pack(anchor="w")
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(8), 0))
        ttk.Label(row, text="Solid color", width=17).pack(side="left")
        self.bg_color = ColorField(row, "#ffffff", self.schedule_preview)
        self.bg_color.pack(side="left", padx=(px(4), 0))

    def build_presets_tab(self, t):
        s = section(t, "Saved presets")
        hint(s, "A preset keeps the text and all styling. Click one to load it (Ctrl+Z undoes that).",
             pady=(0, px(8)))
        ttk.Button(s, text="+ Save Current as Preset…", command=self.save_preset).pack(anchor="w", pady=(0, px(8)))
        self.preset_frame = ttk.Frame(s)
        self.preset_frame.pack(fill="x")
        self.refresh_presets()

        s = section(t, "Preset files")
        hint(s, "For backups or sharing. Imported files are added to the list.", pady=(0, px(8)))
        row = ttk.Frame(s)
        row.pack(anchor="w")
        ttk.Button(row, text="Import File…", command=self.import_preset_file).pack(side="left")
        ttk.Button(row, text="Export File…", command=self.export_preset_file).pack(side="left", padx=(px(6), 0))

    def build_main(self, body):
        main = ttk.Labelframe(body, text="Preview", style="Section.TLabelframe", padding=px(10))
        main.pack(side="left", fill="both", expand=True, padx=(px(14), 0))
        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, px(8)))
        ttk.Button(top, text="Undo", command=self.undo).pack(side="left")
        ttk.Button(top, text="Redo", command=self.redo).pack(side="left", padx=(px(6), 0))
        self.res_label = ttk.Label(top, text="0 x 0", style="Muted.TLabel")
        self.res_label.pack(side="left", padx=px(16))
        self.bg_toggle = tk.StringVar(value="Dark")
        segmented(top, list(PREVIEW_BGS), self.bg_toggle,
                  lambda: self.set_preview_bg(self.bg_toggle.get())).pack(side="right")
        ttk.Label(top, text="Background", style="Muted.TLabel").pack(side="right", padx=(0, px(8)))

        holder = tk.Frame(main, highlightthickness=1, highlightbackground=BORDER)
        holder.pack(expand=True, fill="both")
        self.canvas = tk.Canvas(holder, bg=PREVIEW_BGS[self.bg_toggle.get()], highlightthickness=0)
        self.canvas.pack(expand=True, fill="both")
        self.canvas.bind("<Configure>", self.schedule_preview)
        if self.dnd_ok:
            shutil.rmtree(drag_dir(), ignore_errors=True)  # files from earlier sessions
            self.canvas.drag_source_register(1, DND_FILES)
            self.canvas.dnd_bind("<<DragInitCmd>>", self.on_drag_start)
            self.canvas.configure(cursor="hand2")

    def build_footer(self):
        footer = ttk.Frame(self, padding=(px(20), px(10)))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Export…", style="Go.TButton", default="active",
                   command=self.export).pack(side="right")
        self.export_type = tk.StringVar(value="PNG")
        fmt = ttk.Combobox(footer, textvariable=self.export_type, values=["PNG", "JPG", "SVG"],
                           state="readonly", width=5)
        fmt.pack(side="right", padx=(0, px(12)))
        ttk.Label(footer, text="Format").pack(side="right", padx=(0, px(6)))
        # output quality: renders everything (text, outline, shadow, padding) N times larger
        q = self.prefs.get("quality", DEFAULT_QUALITY)
        self.quality = tk.StringVar(value=q if q in QUALITIES else DEFAULT_QUALITY)
        qual = ttk.Combobox(footer, textvariable=self.quality, values=QUALITIES, state="readonly", width=4)
        qual.pack(side="right", padx=(0, px(16)))
        ttk.Label(footer, text="Quality").pack(side="right", padx=(0, px(6)))
        for box in (fmt, qual):  # readonly boxes keep their text highlighted otherwise
            box.bind("<<ComboboxSelected>>", lambda e: (e.widget.selection_clear(), self.schedule_preview()))
        ttk.Button(footer, text="Save Preset…", style="Footer.TButton",
                   command=self.save_preset).pack(side="right", padx=(0, px(20)))
        self.progress = ttk.Progressbar(footer, length=px(120), maximum=1.0)  # shown while exporting
        self.status_label = ttk.Label(footer, text="Ready", style="Muted.TLabel")
        self.status_label.pack(side="left")
        kinds = {"error": "#e74c3c", "ok": "#2ecc71", "warn": "#f39c12"}
        self.updater = Updater(self, footer, self.prefs, make_icon,
                               lambda text, kind: self.set_status(text, kinds.get(kind, "gray")),
                               lambda v: self.show_progress(None if v is None else v / 100), self.on_close)

    def show_progress(self, value):
        """value 0..1 shows the bar at that point; None hides it."""
        if value is None:
            self.progress.pack_forget()
            return
        if not self.progress.winfo_ismapped():
            self.progress.pack(side="left", padx=(0, px(12)), before=self.status_label)
        self.progress.configure(value=value)

    def bind_shortcuts(self):
        def undo_or_redo(e):
            # <Control-Z> also fires with Caps Lock on; only Shift (state bit 1) means redo
            self.redo() if e.state & 0x1 else self.undo()

        keys = {"z": lambda e: self.undo(), "Z": undo_or_redo,
                "y": lambda e: self.redo(), "Y": lambda e: self.redo()}
        for k, fn in (("e", self.export), ("s", self.save_preset), ("o", self.show_presets)):
            keys[k] = keys[k.upper()] = lambda e, fn=fn: fn()
        for k, handler in keys.items():
            self.bind_all(f"<Control-{k}>", handler)
        # Tk's Text widget has its own Ctrl+O (insert newline) and Ctrl+E (end of line)
        # bindings that run before bind_all; handle those keys first and stop there.
        for k in "oOeE":
            self.text_box.bind(f"<Control-{k}>", lambda e, h=keys[k]: (h(e), "break")[1])

    def set_status(self, text, color="gray"):
        self.status_label.configure(text=text, foreground=STATUS_COLORS.get(color, color))
        self.status_is_note = False

    def set_preview_bg(self, choice):
        self.canvas.configure(bg=PREVIEW_BGS[choice])
        self.schedule_preview()

    def reset_stretch(self):
        self.stretch_x.set(100)
        self.stretch_y.set(100)
        self.schedule_preview()

    # ------------------------------------------------------ prefs
    def load_prefs(self):
        prefs = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
            except Exception:
                prefs = {}
        if not isinstance(prefs, dict):
            prefs = {}
        prefs.setdefault("fav_fonts", [])
        prefs.setdefault("fav_colors", ["#000000"])
        prefs.setdefault("last_color", "#000000")
        return prefs

    def save_prefs(self):
        self.prefs["last_color"] = self.fill.color.value
        self.prefs["last_font"] = self.selected_font
        self.prefs["quality"] = self.quality.get()
        if hasattr(self, "updater"):
            self.prefs.update(self.updater.state())
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f)
        except OSError as e:
            self.set_status(f"Could not save settings: {e}", "#e74c3c")

    def on_close(self):
        self.save_prefs()
        self.destroy()

    # ------------------------------------------------- font loading
    def load_fonts(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                self.set_families(data)
        except Exception:
            pass
        if not self.families:
            self.rescan()

    def rescan(self):
        self.set_status("Scanning fonts...")
        self.btn_rescan.configure(state="disabled")
        self.scan_result = None
        threading.Thread(target=self._scan_worker, daemon=True).start()
        self.after(200, self._poll_scan)

    def _scan_worker(self):
        self.scan_result = scan_fonts()

    def _poll_scan(self):
        if self.scan_result is None:
            self.after(200, self._poll_scan)
            return
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.scan_result, f)
        except OSError:
            pass
        self.set_families(self.scan_result)
        self.btn_rescan.configure(state="normal")
        self.set_status(f"Found {len(self.families)} font families", "#2ecc71")

    def set_families(self, data):
        self.families = data
        self.family_names = sorted(data, key=str.lower)
        if self.selected_font not in data and self.family_names:
            self.selected_font = "Arial" if "Arial" in data else self.family_names[0]
        self.filter_fonts()
        self.schedule_preview()

    def schedule_filter(self, *_):
        if self.filter_job:
            self.after_cancel(self.filter_job)
        self.filter_job = self.after(150, self.filter_fonts)

    def filter_fonts(self):
        self.filter_job = None
        q = self.search_var.get().lower()
        self.matches = [n for n in self.family_names if q in n.lower()]
        self.listbox.delete(0, "end")
        if self.matches:
            self.listbox.insert("end", *self.matches)
        if self.selected_font in self.matches:
            i = self.matches.index(self.selected_font)
            self.listbox.selection_set(i)
            self.listbox.see(i)

    def on_font_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self.selected_font = self.matches[sel[0]]
            self.schedule_preview()

    def pick_font(self, name):
        self.selected_font = name
        if name not in self.matches:
            self.search_var.set("")
        self.filter_fonts()
        self.schedule_preview()

    # -------------------------------------------------- favorites
    def refresh_fav_ui(self):
        self.fav_list.delete(0, "end")
        if self.prefs["fav_fonts"]:
            self.fav_list.insert("end", *self.prefs["fav_fonts"])

    def on_fav_select(self, _event=None):
        sel = self.fav_list.curselection()
        if sel:
            self.pick_font(self.prefs["fav_fonts"][sel[0]])

    def remove_selected_fav(self):
        sel = self.fav_list.curselection()
        if sel:
            self.remove_font_fav(self.prefs["fav_fonts"][sel[0]])

    def add_font_fav(self, name):
        if name and name not in self.prefs["fav_fonts"]:
            self.prefs["fav_fonts"].append(name)
            self.save_prefs()
            self.refresh_fav_ui()

    def remove_font_fav(self, name):
        if name in self.prefs["fav_fonts"]:
            self.prefs["fav_fonts"].remove(name)
            self.save_prefs()
            self.refresh_fav_ui()

    def add_color_fav(self):
        c = self.fill.color.value
        if c not in self.prefs["fav_colors"]:
            self.prefs["fav_colors"].append(c)
            self.save_prefs()
            self.refresh_color_favs()

    def remove_color_fav(self, c):
        if c in self.prefs["fav_colors"]:
            self.prefs["fav_colors"].remove(c)
            self.save_prefs()
            self.refresh_color_favs()

    def refresh_color_favs(self):
        for w in self.color_fav_frame.winfo_children():
            w.destroy()
        colors = [c for c in self.prefs["fav_colors"] if normalize_hex(c)][-10:]
        if not colors:
            return
        row = ttk.Frame(self.color_fav_frame)
        row.pack(anchor="w")
        ttk.Label(row, text="Saved", style="Muted.TLabel").pack(side="left", padx=(0, px(6)))
        for c in colors:
            sw = tk.Label(row, bg=c, width=2, cursor="hand2", highlightthickness=1, highlightbackground="#8a8a8a")
            sw.pack(side="left", padx=px(2), ipady=px(1))
            sw.bind("<Button-1>", lambda e, col=c: self.fill.color.set(col))
            sw.bind("<Button-3>", lambda e, col=c: self.remove_color_fav(col))
        hint(self.color_fav_frame, "Click a saved color to use it; right-click to remove it.")

    # ------------------------------------------------- state (presets + undo)
    def shadow_params(self):
        return {"on": self.sh_on.get(), "color": self.sh_color.value, "opacity": self.sh_op.get(),
                "dx": self.sh_dx.get(), "dy": self.sh_dy.get(), "blur": self.sh_blur.get()}

    def get_state(self):
        return {
            "text": self.text_box.get("1.0", "end-1c"), "font": self.selected_font,
            "bold": self.bold_var.get(), "italic": self.italic_var.get(), "align": self.align_var.get(),
            "size": self.size.get(), "char": self.char.get(), "line": self.line.get(),
            "pad": self.pad.get(), "outline": self.outline.get(),
            "stretch_x": self.stretch_x.get(), "stretch_y": self.stretch_y.get(),
            "rotation": self.rotation.get(),
            "fill": self.fill.get(), "stroke_paint": self.stroke.get(), "shadow": self.shadow_params(),
            "bg_mode": self.bg_mode.get(), "bg_color": self.bg_color.value,
        }

    def apply_state(self, d):
        text = d.get("text", DEFAULT_TEXT)
        if text != self.text_box.get("1.0", "end-1c"):
            self.text_box.delete("1.0", "end")
            self.text_box.insert("1.0", text)
        self.bold_var.set(bool(d.get("bold", False)))
        self.italic_var.set(bool(d.get("italic", False)))
        self.align_var.set(d.get("align", "Left").capitalize())
        for widget, key, default in ((self.size, "size", 100), (self.char, "char", 0), (self.line, "line", 0),
                                     (self.pad, "pad", 0), (self.outline, "outline", 0),
                                     (self.stretch_x, "stretch_x", 100), (self.stretch_y, "stretch_y", 100),
                                     (self.rotation, "rotation", 0)):
            widget.set(d.get(key, default))
        # older presets only had flat colours
        self.fill.set(d.get("fill") or {"color": d.get("color", "#000000")})
        self.stroke.set(d.get("stroke_paint") or {"color": d.get("outline_color", "#ffffff")})
        sh = d.get("shadow") or {}
        self.sh_on.set(bool(sh.get("on", False)))
        self.sh_color.set(sh.get("color", "#000000"))
        self.sh_op.set(sh.get("opacity", 60))
        self.sh_dx.set(sh.get("dx", 8))
        self.sh_dy.set(sh.get("dy", 8))
        self.sh_blur.set(sh.get("blur", 6))
        self.bg_mode.set(d.get("bg_mode", "Transparent"))
        self.bg_color.set(d.get("bg_color", "#ffffff"))
        if d.get("font"):
            self.pick_font(d["font"])
        self.schedule_preview()

    def snapshot(self):
        self.snap_job = None
        s = json.dumps(self.get_state(), sort_keys=True)
        if self.hist_i >= 0 and self.history[self.hist_i] == s:
            return
        del self.history[self.hist_i + 1:]
        self.history.append(s)
        if len(self.history) > 100:
            self.history.pop(0)
        self.hist_i = len(self.history) - 1

    def _flush_snapshot(self):
        if self.snap_job:
            self.after_cancel(self.snap_job)
            self.snapshot()

    def _restore(self, i):
        self.hist_i = i
        self.restoring = True
        try:
            self.apply_state(json.loads(self.history[i]))
        finally:
            self.restoring = False

    def undo(self):
        self._flush_snapshot()
        if self.hist_i > 0:
            self._restore(self.hist_i - 1)
            self.set_status("Undo")

    def redo(self):
        self._flush_snapshot()
        if self.hist_i < len(self.history) - 1:
            self._restore(self.hist_i + 1)
            self.set_status("Redo")

    # ------------------------------------------------- rendering
    def resolve_font(self):
        variants = self.families.get(self.selected_font)
        if not variants:
            return None, 0
        want = ("bold" if self.bold_var.get() else "") + ("italic" if self.italic_var.get() else "") or "regular"
        self.font_note = ""
        if want in variants:
            entry = variants[want]
        else:
            entry = variants.get("regular") or next(iter(variants.values()))
            if want != "regular":
                self.font_note = f"{self.selected_font} has no {want} style - showing a fallback."
        return entry[0], entry[1]

    def get_pil_font(self, path, idx, size):
        key = (path, idx, size)
        if key not in self.font_cache:
            if len(self.font_cache) > 40:
                self.font_cache.clear()
            self.font_cache[key] = ImageFont.truetype(path, size, index=idx)
        return self.font_cache[key]

    def params(self):
        return {
            "text": self.text_box.get("1.0", "end-1c") or " ",
            "align": self.align_var.get().lower(),
            "size": self.size.get(), "cs": self.char.get(), "ls": self.line.get(),
            "pad": self.pad.get(), "sw": self.outline.get(),
            "sx": self.stretch_x.get() / 100, "sy": self.stretch_y.get() / 100,
            "rotation": self.rotation.get(),
            "fill": self.fill.get(), "stroke_paint": self.stroke.get(), "shadow": self.shadow_params(),
            "color": "#ffffff", "stroke": "#ffffff",  # glyph mask colours
            "bg": self.bg_color.value if self.bg_mode.get() == "Solid" else None,
        }

    @staticmethod
    def line_width(fnt, line, cs):
        if cs == 0 or len(line) < 2:
            return fnt.getlength(line)  # whole line keeps kerning
        return sum(fnt.getlength(c) for c in line) + cs * (len(line) - 1)

    def layout(self, p, scale=1.0):
        path, idx = self.resolve_font()
        if not path:
            raise ValueError("No font selected")
        fnt = self.get_pil_font(path, idx, max(1, round(p["size"] * scale)))
        cs, ls = p["cs"] * scale, p["ls"] * scale
        sw = round(p["sw"] * scale)
        pad = round(p["pad"] * scale) + sw
        lines = p["text"].split("\n")
        widths = [self.line_width(fnt, ln, cs) for ln in lines]
        asc, desc = fnt.getmetrics()
        lh = asc + desc
        max_w = max(widths)
        W = max(1, int(math.ceil(max_w)) + 2 * pad)
        H = max(1, int(math.ceil(lh * len(lines) + ls * (len(lines) - 1))) + 2 * pad)
        return dict(fnt=fnt, cs=cs, ls=ls, sw=sw, pad=pad, lines=lines, widths=widths,
                    lh=lh, max_w=max_w, W=W, H=H)

    def render(self, lay, p):
        img = Image.new("RGBA", (lay["W"], lay["H"]), p["bg"] or (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        fnt, cs = lay["fnt"], lay["cs"]
        y = lay["pad"]
        for line, w in zip(lay["lines"], lay["widths"]):
            if p["align"] == "center":
                x = lay["pad"] + (lay["max_w"] - w) / 2
            elif p["align"] == "right":
                x = lay["pad"] + lay["max_w"] - w
            else:
                x = lay["pad"]
            kw = dict(font=fnt, fill=p["color"], stroke_width=lay["sw"], stroke_fill=p["stroke"])
            if cs == 0 or len(line) < 2:
                draw.text((x, y), line, **kw)
            else:
                for ch in line:
                    draw.text((x, y), ch, **kw)
                    x += fnt.getlength(ch) + cs
            y += lay["lh"] + lay["ls"]
        return img

    # ---- geometry
    # Pipeline: bare glyphs -> squash/stretch -> outline -> paints -> shadow -> rotate -> padding/bg.
    def stretched_size(self, p, scale=1.0):
        m = max(p["sx"], p["sy"])
        lay = self.layout(dict(p, pad=0, sw=0), scale * m)
        w = max(1, round(lay["W"] * p["sx"] / m))
        h = max(1, round(lay["H"] * p["sy"] / m))
        sw = round(p["sw"] * scale)
        pad = round(p["pad"] * scale)
        return lay, w, h, sw, pad

    @staticmethod
    def shadow_geo(p, scale):
        """(left, top, right, bottom margins, dx, dy, blur) for the shadow."""
        sh = p["shadow"]
        if not sh["on"]:
            return 0, 0, 0, 0, 0, 0, 0
        dx, dy, blur = round(sh["dx"] * scale), round(sh["dy"] * scale), sh["blur"] * scale
        e = int(math.ceil(blur * 3))
        return max(0, e - dx), max(0, e - dy), max(0, e + dx), max(0, e + dy), dx, dy, blur

    def output_scale(self, p):
        """The Quality multiplier, reduced if needed so the output stays under MAX_OUTPUT.
        Never below 1x (a design that is already huge is left at its own size)."""
        want = int(self.quality.get().rstrip("x"))
        longest = max(self.final_size(p))
        return max(1.0, min(want, MAX_OUTPUT / longest))

    def final_size(self, p):
        _, w, h, sw, pad = self.stretched_size(p, 1.0)
        l, t, r, b = self.shadow_geo(p, 1.0)[:4]
        rw, rh = rot_size(w + 2 * sw + l + r, h + 2 * sw + t + b, p["rotation"])
        return int(math.ceil(rw)) + 2 * pad, int(math.ceil(rh)) + 2 * pad

    def paint_layer(self, cfg, mask, scale):
        try:
            paint = make_paint(cfg, mask.size, scale)
        except Exception as e:
            self.paint_note = f"Texture problem ({e}) - using the color instead."
            paint = Image.new("RGBA", mask.size, cfg["color"])
        if cfg["opacity"] < 100:
            op = cfg["opacity"] / 100
            mask = mask.point(lambda v: round(v * op))
        paint.putalpha(ImageChops.multiply(paint.getchannel("A"), mask))
        return paint

    def build_image(self, p, scale=1.0):
        lay, w, h, sw, pad = self.stretched_size(p, scale)
        img = self.render(lay, dict(p, pad=0, sw=0, bg=None))
        if img.size != (w, h):
            img = img.resize((w, h), RESAMPLE)
        cw, ch = w + 2 * sw, h + 2 * sw
        fmask = Image.new("L", (cw, ch), 0)
        fmask.paste(img.getchannel("A"), (sw, sw))
        omask = outline_mask(fmask, sw) if sw > 0 else None

        content = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if omask is not None:
            content.alpha_composite(self.paint_layer(p["stroke_paint"], omask, scale))
        content.alpha_composite(self.paint_layer(p["fill"], fmask, scale))

        sh = p["shadow"]
        if sh["on"]:
            l, t, r, b, dx, dy, blur = self.shadow_geo(p, scale)
            canvas = Image.new("RGBA", (cw + l + r, ch + t + b), (0, 0, 0, 0))
            smask = ImageChops.lighter(fmask, omask) if omask is not None else fmask
            op = sh["opacity"] / 100
            m = Image.new("L", canvas.size, 0)
            m.paste(smask.point(lambda v: round(v * op)), (l + dx, t + dy))
            if blur > 0:
                m = m.filter(ImageFilter.GaussianBlur(blur))
            layer = Image.new("RGBA", canvas.size, sh["color"])
            layer.putalpha(m)
            canvas.alpha_composite(layer)
            canvas.alpha_composite(content, (l, t))
            content = canvas

        if p["rotation"]:
            content = content.rotate(p["rotation"], resample=BICUBIC, expand=True)
        # trim to the visible ink (letters, outline, shadow); padding is added after this
        bbox = content.getchannel("A").getbbox()
        if bbox:
            content = content.crop(bbox)
        out = Image.new("RGBA", (content.width + 2 * pad, content.height + 2 * pad),
                        p["bg"] or (255, 255, 255, 0))
        out.alpha_composite(content, (pad, pad))
        return out

    def schedule_preview(self, *_):
        if self.preview_job:
            self.after_cancel(self.preview_job)
        self.preview_job = self.after(120, self.update_preview)
        if not self.restoring:
            if self.snap_job:
                self.after_cancel(self.snap_job)
            self.snap_job = self.after(500, self.snapshot)

    def update_preview(self):
        self.preview_job = None
        if not self.families:
            return
        try:
            self.paint_note = ""
            p = self.params()
            full_w, full_h = self.final_size(p)
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            cw, ch = (cw if cw > 1 else 850), (ch if ch > 1 else 750)
            ratio = max(0.01, min((cw - 40) / full_w, (ch - 40) / full_h, 1))
            img = self.build_image(p, ratio)  # render small, not full-size-then-shrink
            # the image is trimmed to its ink, so estimate the exported size (at the chosen
            # quality) from the preview; exact only when the preview is already that size
            q = self.output_scale(p)
            if ratio == q:
                res = f"Res: {img.width} x {img.height}"
            else:
                ps, po = round(p["pad"] * ratio), round(p["pad"] * q)
                out_w, out_h = (round((v - 2 * ps) / ratio * q) + 2 * po for v in img.size)
                res = f"Res: ~{out_w} x {out_h}"
            if self.bg_toggle.get() == "Checker":
                base = checker(img.size)
                base.alpha_composite(img)
                img = base
            self.res_label.configure(text=res)
            self.tk_img = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(cw / 2, ch / 2, anchor="center", image=self.tk_img)
            if self.dnd_ok:
                self.canvas.create_text(cw / 2, ch - 10, anchor="s", fill="#888888", font=("Arial", 10),
                                        text="Drag the preview into a browser, Canva or a folder")
            note = self.paint_note or self.font_note
            if note:
                self.set_status(note, "#f39c12")
                self.status_is_note = True
            elif self.status_is_note or self.status_label.cget("text").startswith("Preview error"):
                self.set_status("Ready")  # the problem went away
        except Exception as e:
            self.set_status(f"Preview error: {str(e)[:80]}", "#e74c3c")

    # ---------------------------------------------------- SVG
    def svg_paint(self, cfg, kind, pid, cw, ch, defs):
        """Attribute string for fill/stroke. Gradients and textures are embedded as an image pattern."""
        op = f'{kind}-opacity="{cfg["opacity"] / 100:.3f}"'
        if cfg["mode"] == "Color":
            return f'{kind}="{cfg["color"]}" {op}'
        img = self.paint_layer(dict(cfg, opacity=100), Image.new("L", (max(1, math.ceil(cw)), max(1, math.ceil(ch))), 255), 1.0)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        defs.append(f'<pattern id="{pid}" patternUnits="userSpaceOnUse" x="0" y="0" '
                    f'width="{cw:.2f}" height="{ch:.2f}"><image href="data:image/png;base64,{b64}" '
                    f'xlink:href="data:image/png;base64,{b64}" width="{cw:.2f}" height="{ch:.2f}" '
                    'preserveAspectRatio="none" /></pattern>')
        return f'{kind}="url(#{pid})" {op}'

    def save_as_svg(self, path, p, out_scale=1.0):
        font_path, idx = self.resolve_font()
        if not font_path:
            raise ValueError("No font selected")
        font = TTFont(font_path, fontNumber=idx, lazy=True)
        try:
            glyphs = font.getGlyphSet()
            cmap = font.getBestCmap()
            scale = p["size"] / font["head"].unitsPerEm
            asc, desc = font["hhea"].ascent, font["hhea"].descent
            lh = (asc - desc) * scale
            cs, sw, sx, sy = p["cs"], p["sw"], p["sx"], p["sy"]
            # PNG keeps the font's kerning when character spacing is 0; measure the same
            # pair adjustments with PIL (large size for precision) so SVG matches.
            kfont = ImageFont.truetype(font_path, 1000, index=idx) if cs == 0 else None
            kscale = p["size"] / 1000

            lines = p["text"].split("\n")
            layouts, widths = [], []
            for line in lines:
                items = []
                for i, ch_ in enumerate(line):
                    # missing characters show the font's "missing" box, like the PNG does
                    g = cmap.get(ord(ch_)) or (".notdef" if ".notdef" in glyphs else None)
                    if not g:
                        continue
                    adv = glyphs[g].width * scale
                    if kfont and i + 1 < len(line):
                        nxt = line[i + 1]
                        adv += (kfont.getlength(ch_ + nxt) - kfont.getlength(ch_)
                                - kfont.getlength(nxt)) * kscale
                    items.append((g, adv))
                layouts.append(items)
                # the last glyph's kerning slot is 0, so summing advances is the line width
                widths.append(sum(a for _, a in items) + cs * max(len(items) - 1, 0))
            max_w = max(widths)
            text_h = lh * len(lines) + p["ls"] * (len(lines) - 1)
            cw, ch = max_w * sx + 2 * sw, text_h * sy + 2 * sw

            # stretch is baked into the coordinates so the stroke isn't distorted
            # (SVG rotate(a) turns clockwise in y-down space; PIL's positive angle is counter-clockwise)
            rot = Transform().rotate(math.radians(-p["rotation"]))
            ink = None  # (xmin, ymin, xmax, ymax) of the letter outlines, in rotated space
            paths = []
            y = 0
            for items, w in zip(layouts, widths):
                x = (max_w - w) / 2 if p["align"] == "center" else (max_w - w if p["align"] == "right" else 0)
                for g, adv in items:
                    place = (scale * sx, 0, 0, -scale * sy, sw + x * sx, sw + (y + asc * scale) * sy)
                    pen = svg_pen(glyphs)
                    glyphs[g].draw(TransformPen(pen, place))
                    d = pen.getCommands()
                    if d:
                        paths.append(f'<path d="{d}" />')
                        # exact outline bounds after rotation, for cropping to the ink
                        bp = BoundsPen(glyphs)
                        glyphs[g].draw(TransformPen(TransformPen(bp, rot), place))
                        if bp.bounds:
                            ink = unionRect(ink, bp.bounds) if ink else bp.bounds
                    x += adv + cs
                y += lh + p["ls"]
        finally:
            font.close()

        # crop to the ink: outlines + outline width (+ shadow), then add padding
        if ink is None:  # nothing visible (e.g. only spaces): keep the text box
            xs, ys = zip(*(rot.transformPoint(pt) for pt in ((0, 0), (cw, 0), (0, ch), (cw, ch))))
            ink = (min(xs), min(ys), max(xs), max(ys))
        box = (ink[0] - sw, ink[1] - sw, ink[2] + sw, ink[3] + sw)
        dx, dy, blur = self.shadow_geo(p, 1.0)[4:]
        if p["shadow"]["on"]:
            ox, oy = rot.transformPoint((dx, dy))  # the shadow offset turns with the text
            e = math.ceil(blur * 3)
            box = unionRect(box, (box[0] + ox - e, box[1] + oy - e, box[2] + ox + e, box[3] + oy + e))
        pad = p["pad"]
        W, H = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
        stroke_attrs = f'fill="none" stroke-width="{2 * sw:.2f}" stroke-linejoin="round"'
        defs, body = [], []

        sh = p["shadow"]
        if sh["on"]:
            filt = ""
            if blur > 0:
                defs.append('<filter id="shadowblur" x="-50%" y="-50%" width="200%" height="200%">'
                            f'<feGaussianBlur stdDeviation="{blur:.2f}" /></filter>')
                filt = ' filter="url(#shadowblur)"'
            body.append(f'<g transform="translate({dx},{dy})" opacity="{sh["opacity"] / 100:.3f}"{filt}>')
            if sw > 0:
                body.append(f'<g {stroke_attrs} stroke="{sh["color"]}">' + "".join(paths) + "</g>")
            body.append(f'<g fill="{sh["color"]}">' + "".join(paths) + "</g>")
            body.append("</g>")

        if sw > 0:
            body.append(f'<g {stroke_attrs} '
                        f'{self.svg_paint(p["stroke_paint"], "stroke", "strokepaint", cw, ch, defs)}>'
                        + "\n".join(paths) + "</g>")
        body.append(f'<g {self.svg_paint(p["fill"], "fill", "fillpaint", cw, ch, defs)}>'
                    + "\n".join(paths) + "</g>")

        # Quality only changes the SVG's display size; the drawing (viewBox) is the same
        out = [f'<?xml version="1.0" encoding="utf-8"?>',
               f'<svg width="{W * out_scale:.2f}" height="{H * out_scale:.2f}" viewBox="0 0 {W:.2f} {H:.2f}" '
               'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">']
        if defs:
            out.append("<defs>" + "\n".join(defs) + "</defs>")
        if p["bg"]:
            out.append(f'<rect width="{W:.2f}" height="{H:.2f}" fill="{p["bg"]}" />')
        out.append(f'<g transform="translate({pad - box[0]:.2f},{pad - box[1]:.2f}) '
                   f'rotate({-p["rotation"]})">')
        out.extend(body)
        out.append("</g>\n</svg>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        return f"{W * out_scale:.0f} x {H * out_scale:.0f}"

    # --------------------------------------------------- export
    def write_output(self, path, p):
        """Write the result at the chosen Quality to path; format comes from the extension.
        Returns a short description of what was written, e.g. "1200 x 400"."""
        scale = self.output_scale(p)
        if path.lower().endswith(".svg"):
            return self.save_as_svg(path, p, scale)
        img = self.build_image(p, scale)
        if path.lower().endswith((".jpg", ".jpeg")):
            bg = Image.new("RGB", img.size, p["bg"] or (255, 255, 255))
            bg.paste(img, (0, 0), img)
            img = bg
        img.save(path)
        return f"{img.width} x {img.height}"

    def on_drag_start(self, _event):
        """Render the full-size image to a temp file and hand that file to the drop target."""
        if not self.families:
            return None  # nothing to drag yet; cancels the drag
        ext = self.export_type.get().lower()
        key = json.dumps([self.get_state(), ext, self.quality.get()], sort_keys=True)
        if key != self.drag_key or not os.path.exists(self.drag_path):
            p = self.params()
            path = os.path.join(drag_dir(), f"{safe_filename(p['text'])}.{ext}")
            try:
                self.canvas.configure(cursor="watch")
                self.update_idletasks()
                os.makedirs(drag_dir(), exist_ok=True)
                self.drag_info = self.write_output(path, p)
            except Exception as e:
                self.set_status(f"Drag failed: {str(e)[:80]}", "#e74c3c")
                return None
            finally:
                self.canvas.configure(cursor="hand2")
            self.drag_key, self.drag_path = key, path
        self.set_status(f"Dragging {os.path.basename(self.drag_path)} ({self.drag_info})")
        return COPY, DND_FILES, (self.drag_path,)

    def export(self):
        text = self.text_box.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Nothing to export", "Please enter some text first.")
            return
        if text.strip() == DEFAULT_TEXT and not messagebox.askyesno(
                "Placeholder text", "The text is still the placeholder. Export anyway?"):
            return
        ext = self.export_type.get().lower()
        path = filedialog.asksaveasfilename(defaultextension=f".{ext}", filetypes=[(ext.upper(), f"*.{ext}")],
                                            initialfile=f"{safe_filename(text)}.{ext}")
        if not path:
            return
        try:
            self.show_progress(0.2)
            self.update_idletasks()
            info = self.write_output(path, self.params())
            self.show_progress(None)
            self.set_status(f"Export Complete! ({info})", "#2ecc71")
            if messagebox.askyesno("Success", f"Saved to:\n{path}\n\nOpen folder?"):
                open_folder(os.path.dirname(os.path.abspath(path)))
        except Exception as e:
            self.show_progress(None)
            self.set_status(f"Error: {str(e)[:80]}", "#e74c3c")
            messagebox.showerror("Error", f"Failed to save:\n{e}")

    # --------------------------------------------------- preset library
    # Stored in presets.json as a list (newest first) of
    # {"name": str, "state": get_state() dict, "thumb": base64 PNG or ""}.
    def load_presets(self):
        try:
            with open(self.presets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and isinstance(d.get("name"), str)
                and isinstance(d.get("state"), dict)]

    def write_presets(self):
        tmp = self.presets_file + ".tmp"  # write then swap, so a crash can't corrupt the library
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.presets, f)
            os.replace(tmp, self.presets_file)
        except OSError as e:
            self.set_status(f"Could not save presets: {e}", "#e74c3c")

    def find_preset(self, name):
        return next((i for i, d in enumerate(self.presets) if d["name"].lower() == name.lower()), None)

    def preset_thumb(self):
        """A small picture of the current design (rendered at 2x for sharp HiDPI display)."""
        p = self.params()
        tw, th = PRESET_THUMB[0] * 2, PRESET_THUMB[1] * 2
        w, h = self.final_size(p)
        img = self.build_image(p, min(1.0, tw / w, th / h))
        img.thumbnail((tw, th), RESAMPLE)
        bg = checker(img.size, 8)
        bg.alpha_composite(img)
        buf = io.BytesIO()
        bg.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def add_preset(self, name, state):
        """Add or replace (same name) a preset from the current design. Returns False if cancelled."""
        i = self.find_preset(name)
        if i is not None and not messagebox.askyesno(
                "Replace preset", f'A preset named "{self.presets[i]["name"]}" already exists. Replace it?'):
            return False
        try:
            thumb = self.preset_thumb()
        except Exception:
            thumb = ""
        entry = {"name": name, "state": state, "thumb": thumb}
        if i is None:
            self.presets.insert(0, entry)
        else:
            self.presets[i] = entry
        self.write_presets()
        self.refresh_presets()
        return True

    def save_preset(self):
        if not self.families:
            return
        name = simpledialog.askstring("Save Preset", "Name for this preset:", parent=self)
        if name is None:  # cancelled
            return
        first_line = self.text_box.get("1.0", "end-1c").split("\n")[0].strip()[:40]
        name = name.strip() or first_line or "Untitled"
        if self.add_preset(name, self.get_state()):
            self.select_tab("Presets")
            self.set_status(f'Saved preset "{name}"', "#2ecc71")

    def apply_preset(self, name):
        i = self.find_preset(name)
        if i is None:
            return
        state = self.presets[i]["state"]
        self.apply_state(state)  # goes through undo history, so Ctrl+Z undoes it
        font = state.get("font")
        if font and self.families and font not in self.families:
            self.set_status(f'Loaded "{name}", but the font "{font}" is not installed', "#f39c12")
        else:
            self.set_status(f'Loaded preset "{name}"', "#2ecc71")

    def delete_preset(self, name):
        i = self.find_preset(name)
        if i is not None and messagebox.askyesno("Delete preset", f'Delete the preset "{name}"?'):
            del self.presets[i]
            self.write_presets()
            self.refresh_presets()

    def refresh_presets(self):
        for w in self.preset_frame.winfo_children():
            w.destroy()
        if not self.presets:
            hint(self.preset_frame, "No presets yet.")
        for d in self.presets:
            thumb = None
            if d.get("thumb"):
                try:
                    img = Image.open(io.BytesIO(base64.b64decode(d["thumb"])))
                    img.load()
                    # thumbnails are stored at 2x; show them at this screen's scale
                    img = img.resize((max(1, px(img.width / 2)), max(1, px(img.height / 2))), RESAMPLE)
                    thumb = ImageTk.PhotoImage(img)
                except Exception:
                    thumb = None
            PresetCard(self.preset_frame, d["name"], thumb, self.apply_preset,
                       self.delete_preset).pack(fill="x", pady=(0, px(6)))

    def show_presets(self):
        self.select_tab("Presets")

    # ---- preset files (for backups or sharing)
    def export_preset_file(self):
        first_line = self.text_box.get("1.0", "end-1c")
        path = filedialog.asksaveasfilename(initialdir=app_path(), defaultextension=".json",
                                            initialfile=f"{safe_filename(first_line, 'preset')}.json",
                                            filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.get_state(), f, indent=2)
            self.set_status("Preset file saved", "#2ecc71")
        except OSError as e:
            messagebox.showerror("Error", f"Could not save preset:\n{e}")

    def import_preset_file(self):
        """Load a preset file and add it to the library (named after the file)."""
        path = filedialog.askopenfilename(initialdir=app_path(), filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("this file is not a preset")
            self.apply_state(d)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load preset:\n{e}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        base, n = name, 2
        while self.find_preset(name) is not None:  # don't overwrite; pick a free name
            name = f"{base} ({n})"
            n += 1
        self.add_preset(name, self.get_state())
        self.set_status(f'Imported preset "{name}"', "#2ecc71")


if __name__ == "__main__":
    FontApp().mainloop()