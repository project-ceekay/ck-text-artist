"""CK Text Artist — Shared UI Kit and System Utilities."""
import ctypes
from ctypes import wintypes
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
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageDraw, ImageTk

_R = getattr(Image, "Resampling", Image)
RESAMPLE, BICUBIC = _R.LANCZOS, _R.BICUBIC
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


APP_NAME = "CK Text Artist"
VERSION = "1.0.1"
REPO = "project-ceekay/ck-text-artist"
SETTINGS_NAME = "Text Artist"  # %APPDATA%\CK Suite\<this>

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
        if DARK_MODE:
            win.configure(bg="#1e1e1e")
        body = ttk.Frame(win, padding=self._px(18))
        body.pack(fill="both", expand=True)
        return win, body

    def _show(self, win):
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        set_window_dark_titlebar(win, DARK_MODE)
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
                  style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        link = ttk.Label(body, text=f"github.com/{REPO}", style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        link.bind("<Button-1>", lambda e: _webbrowser.open(f"https://github.com/{REPO}"))
        ttk.Separator(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=P(14))
        ttk.Checkbutton(body, text="Check for updates automatically (once a day)", variable=self.auto).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self.about_lbl = ttk.Label(body, text="", style="Muted.TLabel")
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



ACCENT = "#0067c0"      # Windows 11 accent blue
ERROR = "#c42b1c"
SUCCESS = "#0f7b0f"
WARN = "#9d5d00"
MUTED = "#5f6368"
HEADER_BG = "#ffffff"
BORDER = "#d9d9d9"
HOVER = "#e5f1fb"
DARK_MODE = False
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


def make_toolbar_icon(kind, size=18, dark=False):
    """Crisp 2x-supersampled original toolbar icons for CK Text Artist."""
    W = size * 2
    s = W / 24.0
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ink = "#e8eaed" if dark else "#3c4043"
    acc = "#4cc2ff" if dark else ACCENT
    card = "#2d2d30" if dark else "#ffffff"

    if kind == "new":
        d.rectangle([4 * s, 3 * s, 17 * s, 20 * s], fill=card, outline="#9aa0a6" if dark else "#5f6368",
                    width=max(1, round(1.3 * s)))
        for ly in (0.34, 0.56, 0.76):
            y = (3 + 17 * ly) * s
            d.line([(6.2 * s, y), (14.8 * s, y)], fill="#9aa0a6", width=max(1, round(1.2 * s)))
        cx, cy, r = 17.5, 17.0, 5.2
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill="#25a244", outline="#ffffff",
                  width=max(1, round(1.1 * s)))
        w = max(1, round(1.8 * s))
        d.line([(cx * s, (cy - 2.8) * s), (cx * s, (cy + 2.8) * s)], fill="#ffffff", width=w)
        d.line([((cx - 2.8) * s, cy * s), ((cx + 2.8) * s, cy * s)], fill="#ffffff", width=w)
    elif kind == "presets":
        d.polygon([(2 * s, 6 * s), (9 * s, 6 * s), (11 * s, 8 * s), (21 * s, 8 * s),
                   (21 * s, 19 * s), (2 * s, 19 * s)],
                  fill="#f3b63f", outline="#b87d14", width=max(1, round(1.3 * s)))
        d.rectangle([3 * s, 10 * s, 20 * s, 18 * s], fill="#ffd269")
    elif kind == "save_preset":
        d.rounded_rectangle([3 * s, 3 * s, 21 * s, 21 * s], radius=2 * s, fill=ACCENT, outline="#004a8f",
                            width=max(1, round(1.2 * s)))
        d.rectangle([7 * s, 3 * s, 16 * s, 9 * s], fill="#e8eaed")
        d.rectangle([6 * s, 13 * s, 18 * s, 20 * s], fill="#ffffff")
    elif kind == "export":
        d.rounded_rectangle([3 * s, 13 * s, 21 * s, 20 * s], radius=2 * s, fill=card,
                            outline=ink, width=max(1, round(1.3 * s)))
        w = max(2, round(2.2 * s))
        c = "#4cc355" if dark else SUCCESS
        d.line([(12 * s, 15 * s), (12 * s, 5 * s)], fill=c, width=w)
        d.polygon([(12 * s, 2.5 * s), (7.5 * s, 8 * s), (16.5 * s, 8 * s)], fill=c)
    elif kind == "undo":
        w = max(2, round(2.2 * s))
        d.arc([5 * s, 7 * s, 20 * s, 20 * s], start=210, end=45, fill=acc, width=w)
        d.polygon([(3 * s, 11 * s), (10 * s, 6 * s), (9 * s, 13 * s)], fill=acc)
    elif kind == "redo":
        w = max(2, round(2.2 * s))
        d.arc([4 * s, 7 * s, 19 * s, 20 * s], start=135, end=330, fill=acc, width=w)
        d.polygon([(21 * s, 11 * s), (14 * s, 6 * s), (15 * s, 13 * s)], fill=acc)
    elif kind == "bold":
        d.rounded_rectangle([3 * s, 3 * s, 21 * s, 21 * s], radius=2.5 * s, fill=card,
                            outline=ink, width=max(1, round(1.2 * s)))
        w = max(2, round(2.2 * s))
        d.line([(8 * s, 6.5 * s), (8 * s, 17.5 * s)], fill=ink, width=w)
        d.arc([6 * s, 6.5 * s, 15.5 * s, 12.2 * s], start=270, end=90, fill=ink, width=w)
        d.arc([6 * s, 11.5 * s, 16.5 * s, 17.5 * s], start=270, end=90, fill=ink, width=w)
        d.line([(8 * s, 6.5 * s), (12 * s, 6.5 * s)], fill=ink, width=w)
        d.line([(8 * s, 12 * s), (12.5 * s, 12 * s)], fill=ink, width=w)
        d.line([(8 * s, 17.5 * s), (13 * s, 17.5 * s)], fill=ink, width=w)
    elif kind == "italic":
        d.rounded_rectangle([3 * s, 3 * s, 21 * s, 21 * s], radius=2.5 * s, fill=card,
                            outline=ink, width=max(1, round(1.2 * s)))
        w = max(2, round(2.0 * s))
        d.line([(10 * s, 6.5 * s), (16 * s, 6.5 * s)], fill=ink, width=w)
        d.line([(14 * s, 6.5 * s), (10 * s, 17.5 * s)], fill=ink, width=w)
        d.line([(8 * s, 17.5 * s), (14 * s, 17.5 * s)], fill=ink, width=w)
    elif kind in ("align_left", "align_center", "align_right"):
        w = max(1, round(1.8 * s))
        spans = [(4, 20), (4, 15), (4, 18), (4, 13)]
        for idx, (a, b) in enumerate(spans):
            length = b - a
            if kind == "align_left":
                x1, x2 = 4, 4 + length
            elif kind == "align_center":
                x1, x2 = 12 - length / 2, 12 + length / 2
            else:
                x1, x2 = 20 - length, 20
            y = (5.5 + idx * 4.2) * s
            d.line([(x1 * s, y), (x2 * s, y)], fill=acc if idx % 2 == 0 else ink, width=w)
    elif kind == "fav_font":
        pts = []
        for i in range(10):
            ang = math.radians(-90 + i * 36)
            r = 9.2 * s if i % 2 == 0 else 4.0 * s
            pts.append((12 * s + r * math.cos(ang), 12.5 * s + r * math.sin(ang)))
        d.polygon(pts, fill="#fbbc04", outline="#b87d14", width=max(1, round(1.2 * s)))
    elif kind == "rescan":
        w = max(2, round(2.1 * s))
        d.arc([4 * s, 4 * s, 20 * s, 20 * s], start=30, end=310, fill=acc, width=w)
        d.polygon([(20 * s, 4 * s), (21 * s, 11 * s), (14 * s, 9 * s)], fill=acc)
    elif kind == "theme":
        d.ellipse([4.5 * s, 4.5 * s, 19.5 * s, 19.5 * s], fill="#fbbc04" if dark else "#5f6368")
        d.ellipse([9.0 * s, 2.5 * s, 22.0 * s, 15.5 * s], fill=(0, 0, 0, 0))

    return ImageTk.PhotoImage(im.resize((size, size), RESAMPLE))


def attach_tooltip(widget, text, status_fn=None):
    """Native-style hover tooltip below a toolbar button."""
    tip = {"win": None, "job": None}

    def hide():
        if tip["job"]:
            widget.after_cancel(tip["job"])
            tip["job"] = None
        if tip["win"] is not None:
            try:
                tip["win"].destroy()
            except tk.TclError:
                pass
            tip["win"] = None

    def show():
        tip["job"] = None
        if not widget.winfo_exists():
            return
        w = tk.Toplevel(widget)
        w.withdraw()
        w.wm_overrideredirect(True)
        bg = "#2d2d30" if DARK_MODE else "#ffffe1"
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        lbl = tk.Label(w, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       relief="solid", bd=1, padx=px(6), pady=px(2))
        lbl.pack()
        w.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + px(3)
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.attributes("-topmost", True)
        w.lift()
        tip["win"] = w

    def enter(e=None):
        hide()
        if status_fn:
            status_fn(text)
        tip["job"] = widget.after(150, show)

    def leave(e=None):
        hide()
        if status_fn:
            status_fn("")

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", lambda e: hide(), add="+")


def set_window_dark_titlebar(win, dark):
    """Enable or disable Windows 10/11 native dark title bar on a Tk or Toplevel window."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            uxtheme = ctypes.windll.uxtheme
            uxtheme[135](2 if dark else 3)
            uxtheme[136]()
        except Exception:
            pass
        val = ctypes.c_int(1 if dark else 0)
        hwnds = set()
        try:
            frame_id = win.wm_frame()
            if frame_id and frame_id != "0x0":
                hwnds.add(int(frame_id, 16))
        except Exception:
            pass
        hwnd = win.winfo_id()
        hwnds.add(hwnd)
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnds.add(parent)
        for h in hwnds:
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(h, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def apply_theme(root, dark=False):
    """Switch the entire window between Light Mode (Windows vista) and Dark Mode (clam dark)."""
    global DARK_MODE
    DARK_MODE = bool(dark)
    style = ttk.Style(root)
    if dark:
        BG = "#1e1e1e"
        SURFACE = "#252526"
        FIELD = "#141414"
        HEAD = "#2d2d30"
        FG = "#e8eaed"
        MUTED_FG = "#9aa0a6"
        BD = "#3c4043"
        ACC = "#4cc2ff"
        SEL = "#094771"
        if "clam" in style.theme_names():
            style.theme_use("clam")
        root.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        troughcolor=SURFACE, bordercolor=BD, darkcolor=BG, lightcolor=SURFACE,
                        selectbackground=SEL, selectforeground="#ffffff", insertcolor=FG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED_FG)
        style.configure("Link.TLabel", background=BG, foreground=ACC)
        style.configure("TSeparator", background=BD)
        style.configure("TLabelframe", background=BG, bordercolor=BD)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("Section.TLabelframe", background=BG, bordercolor=BD)
        style.configure("Section.TLabelframe.Label", background=BG, foreground=ACC, font=("Segoe UI Semibold", 10))
        style.configure("TButton", background=HEAD, foreground=FG, bordercolor=BD, focuscolor=ACC, padding=(px(8), px(3)))
        style.map("TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666"), ("pressed", "#ffffff")])
        style.configure("Go.TButton", background=ACCENT, foreground="#ffffff", bordercolor="#005a9e",
                        font=("Segoe UI Semibold", 10), padding=(px(18), px(5)))
        style.map("Go.TButton",
                  background=[("disabled", "#2a2a2a"), ("pressed", "#004c87"), ("active", "#1077d0")],
                  foreground=[("disabled", "#666666"), ("!disabled", "#ffffff")])
        style.configure("Footer.TButton", background=HEAD, foreground=FG, bordercolor=BD, padding=(px(12), px(5)))
        style.map("Footer.TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666")])
        style.configure("Toolbutton", background=SURFACE, foreground=FG, bordercolor=BD, padding=(px(9), px(2)))
        style.map("Toolbutton",
                  background=[("selected", ACCENT), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("selected", "#ffffff"), ("!selected", FG)])
        style.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("TRadiobutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TRadiobutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, bordercolor=BD,
                        lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.configure("Invalid.TEntry", fieldbackground=FIELD, foreground="#ff6b6b")
        style.configure("TCombobox", fieldbackground=FIELD, background=HEAD, foreground=FG,
                        arrowcolor=FG, bordercolor=BD, lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD), ("disabled", "#1b1b1b")],
                  foreground=[("readonly", FG), ("disabled", "#666666")],
                  background=[("active", "#38383c"), ("disabled", "#222222")])
        style.configure("TSpinbox", fieldbackground=FIELD, background=HEAD, foreground=FG,
                        arrowcolor=FG, bordercolor=BD, lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.configure("TNotebook", background=BG, bordercolor=BD, tabmargins=(0, px(2), 0, 0))
        style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED_FG,
                        bordercolor=BD, lightcolor=SURFACE, padding=(px(10), px(4)))
        style.map("TNotebook.Tab",
                  background=[("selected", HEAD), ("active", "#323236")],
                  foreground=[("selected", FG), ("active", FG)])
        style.configure("TScrollbar", background=HEAD, troughcolor=FIELD, bordercolor=FIELD,
                        arrowcolor=MUTED_FG, darkcolor=HEAD, lightcolor=HEAD)
        style.map("TScrollbar", background=[("active", "#454549"), ("pressed", "#55555a")])
        style.configure("TScale", background=BG, troughcolor=SURFACE, bordercolor=BD, darkcolor=HEAD, lightcolor=HEAD)
        style.configure("TProgressbar", background=ACCENT, troughcolor=SURFACE, bordercolor=BD)
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", SEL)
        root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    else:
        if "vista" in style.theme_names():
            style.theme_use("vista")
        bg = style.lookup("TFrame", "background") or "#f0f0f0"
        root.configure(bg=bg)
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(px(18), px(5)))
        style.configure("Footer.TButton", padding=(px(12), px(5)))
        style.configure("Toolbutton", padding=(px(9), px(2)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Link.TLabel", foreground=ACCENT)
        style.configure("Invalid.TEntry", foreground=ERROR)
        style.configure("TNotebook.Tab", padding=(px(10), px(4)))
        root.option_add("*TCombobox*Listbox.background", "#ffffff")
        root.option_add("*TCombobox*Listbox.foreground", "#1a1a1a")
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    set_window_dark_titlebar(root, dark)
    frame_bg = style.lookup("TFrame", "background") or ("#1e1e1e" if dark else "#f0f0f0")

    def walk(w):
        for child in w.winfo_children():
            if isinstance(child, ScrollFrame):
                child.canvas.configure(bg=frame_bg)
            elif isinstance(child, tk.Listbox):
                child.configure(
                    bg="#141414" if dark else "white",
                    fg="#e8eaed" if dark else "#1a1a1a",
                    highlightbackground="#3c4043" if dark else BORDER,
                    highlightcolor="#3c4043" if dark else BORDER,
                    selectbackground="#094771" if dark else ACCENT,
                    selectforeground="white",
                )
            elif hasattr(child, "retheme") and callable(child.retheme):
                child.retheme()
            walk(child)

    walk(root)


def section(parent, text):
    """A titled group box, stacked down the tab."""
    box = ttk.Labelframe(parent, text=text, style="Section.TLabelframe", padding=px(8))
    box.pack(fill="x", pady=(0, px(7)))
    return box


def hint(parent, text, **pack):
    lbl = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=px(320), justify="left")
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
    bg = "#141414" if DARK_MODE else "white"
    fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
    bd = "#3c4043" if DARK_MODE else BORDER
    sel = "#094771" if DARK_MODE else ACCENT
    return tk.Listbox(parent, height=height, exportselection=False, activestyle="none", relief="flat",
                      borderwidth=0, highlightthickness=1, highlightbackground=bd,
                      highlightcolor=bd, font=("Segoe UI", 10), bg=bg, fg=fg,
                      selectbackground=sel, selectforeground="white")


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
        cur_bg = ttk.Style(root).lookup("TFrame", "background") or bg
        image = Image.new("RGB", (old_w, old_h), tuple(c >> 8 for c in root.winfo_rgb(cur_bg)))
        image.paste(shown.crop((0, 0, min(w, old_w), min(h, old_h))))
        # everything from the first bottom-packed section down stays pinned to the bottom edge
        split = min([w.winfo_y() for w, info in slaves if info.get("side") == "bottom"] or [old_h])
        for box, place in (((0, 0, old_w, split), dict(x=0, y=0, anchor="nw")),
                           ((0, split, old_w, old_h), dict(x=0, rely=1, anchor="sw"))):
            if box[3] > box[1]:
                photo = ImageTk.PhotoImage(image.crop(box))
                shot = tk.Label(root, image=photo, bd=0, highlightthickness=0, bg=cur_bg)
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


