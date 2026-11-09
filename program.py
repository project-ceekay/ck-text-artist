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

import ui_kit
from ui_kit import *
import rendering
from rendering import *

class PresetCard(tk.Frame):
    """A saved preset: name and thumbnail (click to load) plus a delete button."""

    def __init__(self, master, name, thumb, on_pick, on_delete):
        bg = "#252526" if DARK_MODE else "white"
        bd = "#3c4043" if DARK_MODE else BORDER
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        super().__init__(master, bg=bg, highlightthickness=1, highlightbackground=bd, cursor="hand2")
        body = tk.Frame(self, bg=bg)
        body.pack(side="left", fill="x", expand=True, padx=px(10), pady=px(6))
        self.parts = [self, body]
        self.lbl = tk.Label(body, text=name, bg=bg, fg=fg, font=("Segoe UI Semibold", 10), anchor="w")
        self.lbl.pack(anchor="w")
        self.parts.append(self.lbl)
        if thumb is not None:
            pic = tk.Label(body, image=thumb, bg=bg)
            pic.image = thumb
            pic.pack(anchor="w", pady=(px(4), 0))
            self.parts.append(pic)
        ttk.Button(self, text="Delete", width=7, command=lambda: on_delete(name)).pack(side="right", padx=px(8))
        for w in self.parts:
            w.bind("<Button-1>", lambda e: on_pick(name))
            w.bind("<Enter>", lambda e: self._paint("#323236" if DARK_MODE else HOVER))
            w.bind("<Leave>", self._leave)

    def retheme(self):
        bg = "#252526" if DARK_MODE else "white"
        bd = "#3c4043" if DARK_MODE else BORDER
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        self.configure(highlightbackground=bd)
        self.lbl.configure(fg=fg)
        self._paint(bg)

    def _paint(self, colour):
        for w in self.parts:
            w.configure(bg=colour)

    def _leave(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            w = w.master
        if w is None:
            self._paint("#252526" if DARK_MODE else "white")


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
        self.title(f"{APP_NAME} v{VERSION}")
        h = min(px(760), self.winfo_screenheight() - px(80))
        self.geometry(f"{px(1180)}x{h}")
        self.minsize(px(960), min(px(580), h))
        self.icon = ImageTk.PhotoImage(make_icon())
        self.iconphoto(True, self.icon)
        self.setup_style()

        self.config_file = os.path.join(data_dir(), "config.json")
        self.cache_file = os.path.join(data_dir(), "font_cache.json")
        self.prefs = self.load_prefs()
        self.dark_mode = tk.BooleanVar(value=bool(self.prefs.get("dark_mode", False)))
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
        self.apply_dark_mode()
        self.update_title()
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
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(px(18), px(5)))
        style.configure("Footer.TButton", padding=(px(12), px(5)))
        style.configure("Toolbutton", padding=(px(9), px(2)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Invalid.TEntry", foreground=ERROR)
        style.configure("TNotebook.Tab", padding=(px(10), px(4)))

    # ---------------------------------------------------------- UI
    def slider(self, parent, text, lo, hi, value):
        s = SliderRow(parent, text, lo, hi, value, self.schedule_preview)
        s.pack(fill="x", pady=px(1))
        return s

    def update_title(self):
        font = self.selected_font or "Arial"
        self.title(f"{APP_NAME} v{VERSION} - {font}")

    def build_ui(self):
        self.build_footer()  # packed before the body so it always stays visible
        ttk.Separator(self).pack(side="bottom", fill="x")

        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        self.align_var = tk.StringVar(value="Left")
        self.bg_toggle = tk.StringVar(value="Dark")

        self.build_menubar()
        self.build_toolbar()

        body = ttk.Frame(self, padding=(px(12), px(8), px(12), px(8)))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=px(380))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self.tab_frames = {}
        for name, build in (("Text", self.build_text_tab), ("Layout", self.build_layout_tab),
                            ("Fill", self.build_fill_tab), ("Outline", self.build_outline_tab),
                            ("Effects", self.build_effects_tab), ("Presets", self.build_presets_tab)):
            sf = ScrollFrame(self.tabs, padding=(px(8), px(6), px(6), px(4)))
            self.tabs.add(sf, text=name)
            self.tab_frames[name] = sf
            build(sf.body)
        self.build_main(body)

        # the mouse wheel scrolls whichever tab is under the pointer; number boxes and
        # drop-downs shouldn't change value just because the wheel passes over them
        self.bind_all("<MouseWheel>", self.on_wheel)
        for cls in ("TSpinbox", "TCombobox"):
            self.unbind_class(cls, "<MouseWheel>")

    def build_menubar(self):
        mb_bg = "#1e1e1e" if DARK_MODE else "#ffffff"
        mb_fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        bar = tk.Frame(self, bg=mb_bg, padx=px(4), pady=px(1))
        bar.pack(fill="x")
        self.menubar_frame = bar
        self._menu_btns = []
        self._menus = []

        def add_menu(label, underline, items):
            btn = tk.Label(bar, text=label, underline=underline, bg=mb_bg, fg=mb_fg,
                           font=("Segoe UI", 9), padx=px(7), pady=px(2))
            btn.pack(side="left")
            m = tk.Menu(btn, tearoff=False, font=("Segoe UI", 9))
            self._menus.append(m)
            for item in items:
                if item is None:
                    m.add_separator()
                elif item[0] == "__check__":
                    m.add_checkbutton(label=item[1], variable=item[2], command=item[3],
                                      accelerator=item[4] if len(item) > 4 else "")
                else:
                    m.add_command(label=item[0], command=item[1],
                                  accelerator=item[2] if len(item) > 2 else "",
                                  underline=item[3] if len(item) > 3 else -1)

            def on_enter(e=None):
                btn.configure(bg="#323236" if DARK_MODE else "#e5f1fb")

            def on_leave(e=None):
                btn.configure(bg="#1e1e1e" if DARK_MODE else "#ffffff")

            def open_menu(e=None):
                on_enter()
                try:
                    m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
                finally:
                    m.grab_release()
                    on_leave()
                return "break"

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.bind("<Button-1>", open_menu)
            if 0 <= underline < len(label):
                ch = label[underline].lower()
                self.bind(f"<Alt-Key-{ch}>", open_menu)
                self.bind(f"<Alt-Key-{ch.upper()}>", open_menu)
            self._menu_btns.append(btn)
            return m

        add_menu("File", 0, [
            ("New / Reset design", self.reset_design, "Ctrl+N", 0),
            None,
            ("Export image / SVG…", self.export, "Ctrl+E", 0),
            None,
            ("Import preset file…", self.import_preset_file, "", 0),
            ("Export preset file…", self.export_preset_file, "", 1),
            None,
            ("Rescan system fonts", self.rescan, "F5", 0),
            None,
            ("Exit", self.on_close, "Alt+F4", 1),
        ])
        add_menu("Edit", 0, [
            ("Undo", self.undo, "Ctrl+Z", 0),
            ("Redo", self.redo, "Ctrl+Y", 0),
            None,
            ("Copy text", self.copy_text, "", 0),
            ("Clear text", self.clear_text, "", 1),
        ])
        add_menu("View", 0, [
            ("Text tab", lambda: self.select_tab("Text"), ""),
            ("Layout tab", lambda: self.select_tab("Layout"), ""),
            ("Fill tab", lambda: self.select_tab("Fill"), ""),
            ("Outline tab", lambda: self.select_tab("Outline"), ""),
            ("Effects tab", lambda: self.select_tab("Effects"), ""),
            ("Presets tab", lambda: self.select_tab("Presets"), "Ctrl+O"),
            None,
            ("Preview: Light", lambda: (self.bg_toggle.set("Light"), self.set_preview_bg("Light")), ""),
            ("Preview: Dark", lambda: (self.bg_toggle.set("Dark"), self.set_preview_bg("Dark")), ""),
            ("Preview: Checker", lambda: (self.bg_toggle.set("Checker"), self.set_preview_bg("Checker")), ""),
            None,
            ("__check__", "Dark Mode", self.dark_mode, self.apply_dark_mode, ""),
        ])
        add_menu("Style", 0, [
            ("__check__", "Bold", self.bold_var, self.schedule_preview, "Ctrl+B"),
            ("__check__", "Italic", self.italic_var, self.schedule_preview, "Ctrl+I"),
            None,
            ("Align Left", lambda: (self.align_var.set("Left"), self.schedule_preview()), ""),
            ("Align Center", lambda: (self.align_var.set("Center"), self.schedule_preview()), ""),
            ("Align Right", lambda: (self.align_var.set("Right"), self.schedule_preview()), ""),
            None,
            ("★ Add current font to Favorites", lambda: self.add_font_fav(self.selected_font), ""),
            ("★ Save current fill color", self.add_color_fav, ""),
        ])
        add_menu("Presets", 0, [
            ("Save current as preset…", self.save_preset, "Ctrl+S", 0),
            ("Open Presets tab", self.show_presets, "Ctrl+O", 0),
            None,
            ("Import preset file…", self.import_preset_file, "", 0),
            ("Export preset file…", self.export_preset_file, "", 0),
        ])
        add_menu("Help", 0, [
            ("Check for Updates…", lambda: self.updater.check(quiet=False), ""),
            None,
            (f"About {APP_NAME}", self.updater.show_about, ""),
        ])
        self.menubar_sep = tk.Frame(self, height=1, bg=BORDER)
        self.menubar_sep.pack(fill="x")

    def build_toolbar(self):
        bg = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        bar = tk.Frame(self, bg=bg, padx=px(8), pady=px(3))
        bar.pack(fill="x")
        self.toolbar_bar = bar
        self._tb_icons = {}
        self._tb_btns = []
        self._tb_seps = []
        icon_px = px(18)
        cell_px = px(24)
        self.tb_hint = tk.Label(bar, text="", bg=bg, fg=MUTED, font=("Segoe UI", 9))
        self.tb_hint.pack(side="right", padx=(0, px(6)))

        def set_tb_hint(text):
            self.tb_hint.configure(text=text)
            self.set_status(text or "Ready")

        def make_btn(kind, tip, cmd):
            icon = make_toolbar_icon(kind, icon_px, dark=DARK_MODE)
            self._tb_icons[kind] = icon
            btn = tk.Label(bar, image=icon, width=cell_px, height=cell_px, bg=bg, bd=0,
                           highlightthickness=1, highlightbackground=bg)
            btn.pack(side="left", padx=px(1))
            self._tb_btns.append((kind, btn))

            def on_enter(e=None):
                hov = "#323236" if DARK_MODE else HOVER
                bd_hov = "#4cc2ff" if DARK_MODE else "#99d1ff"
                btn.configure(bg=hov, highlightbackground=bd_hov)

            def on_leave(e=None):
                cur_bg = "#252526" if DARK_MODE else (ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0")
                btn.configure(bg=cur_bg, highlightbackground=cur_bg)

            def on_press(e=None):
                pr_bg = "#094771" if DARK_MODE else "#cce4f7"
                btn.configure(bg=pr_bg, highlightbackground=ACCENT)

            def on_release(e=None):
                on_enter()
                if cmd:
                    cmd()

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.bind("<ButtonPress-1>", on_press)
            btn.bind("<ButtonRelease-1>", on_release)
            attach_tooltip(btn, tip, status_fn=set_tb_hint)

        def sep():
            s = tk.Frame(bar, width=1, height=px(18), bg=BORDER)
            s.pack(side="left", padx=px(5), pady=px(4))
            self._tb_seps.append(s)

        make_btn("new", "New / Reset design (Ctrl+N)", self.reset_design)
        make_btn("presets", "Presets library (Ctrl+O)", self.show_presets)
        make_btn("save_preset", "Save current as preset… (Ctrl+S)", self.save_preset)
        make_btn("export", "Export PNG / JPG / SVG… (Ctrl+E)", self.export)
        sep()
        make_btn("undo", "Undo (Ctrl+Z)", self.undo)
        make_btn("redo", "Redo (Ctrl+Y)", self.redo)
        sep()
        make_btn("bold", "Toggle Bold (Ctrl+B)", self.toggle_bold)
        make_btn("italic", "Toggle Italic (Ctrl+I)", self.toggle_italic)
        make_btn("align_left", "Align Left", lambda: (self.align_var.set("Left"), self.schedule_preview()))
        make_btn("align_center", "Align Center", lambda: (self.align_var.set("Center"), self.schedule_preview()))
        make_btn("align_right", "Align Right", lambda: (self.align_var.set("Right"), self.schedule_preview()))
        sep()
        make_btn("fav_font", "★ Add current font to Favorites", lambda: self.add_font_fav(self.selected_font))
        make_btn("rescan", "Rescan system fonts (F5)", self.rescan)
        sep()
        make_btn("theme", "Toggle Dark Mode", self.toggle_dark_mode)
        ttk.Separator(self).pack(fill="x")

    def toggle_bold(self):
        self.bold_var.set(not self.bold_var.get())
        self.schedule_preview()

    def toggle_italic(self):
        self.italic_var.set(not self.italic_var.get())
        self.schedule_preview()

    def copy_text(self):
        self.clipboard_clear()
        self.clipboard_append(self.text_box.get("1.0", "end-1c"))
        self.set_status("Copied text to clipboard")

    def clear_text(self):
        self.text_box.delete("1.0", "end")
        self.schedule_preview()

    def reset_design(self):
        self.apply_state({
            "text": DEFAULT_TEXT, "font": "Arial" if "Arial" in self.families else self.selected_font,
            "bold": False, "italic": False, "align": "Left",
            "size": 100, "char": 0, "line": 0, "pad": 0, "outline": 0,
            "stretch_x": 100, "stretch_y": 100, "rotation": 0,
            "fill": dict(PAINT_DEFAULTS), "stroke_paint": dict(PAINT_DEFAULTS, color="#ffffff"),
            "shadow": {"on": False, "color": "#000000", "opacity": 60, "dx": 8, "dy": 8, "blur": 6},
            "bg_mode": "Transparent", "bg_color": "#ffffff",
        })
        self.set_status("Reset to default design")

    def toggle_dark_mode(self):
        self.dark_mode.set(not self.dark_mode.get())
        self.apply_dark_mode()

    def apply_dark_mode(self):
        dark = bool(self.dark_mode.get())
        apply_theme(self, dark)
        mb_bg = "#1e1e1e" if dark else "#ffffff"
        mb_fg = "#e8eaed" if dark else "#1a1a1a"
        tb_bg = "#252526" if dark else (ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0")
        sep_bg = "#3c4043" if dark else BORDER
        if hasattr(self, "menubar_frame"):
            self.menubar_frame.configure(bg=mb_bg)
            self.menubar_sep.configure(bg="#2d2d30" if dark else "#e5e5e5")
            for b in self._menu_btns:
                b.configure(bg=mb_bg, fg=mb_fg)
        for m in getattr(self, "_menus", ()):
            m.configure(
                bg="#252526" if dark else "#ffffff",
                fg="#e8eaed" if dark else "#1a1a1a",
                activebackground="#094771" if dark else ACCENT,
                activeforeground="#ffffff",
                selectcolor="#4cc2ff" if dark else "#1a1a1a",
            )
        self.toolbar_bar.configure(bg=tb_bg)
        self.tb_hint.configure(bg=tb_bg, fg="#9aa0a6" if dark else MUTED)
        icon_px = px(18)
        for kind, btn in self._tb_btns:
            icon = make_toolbar_icon(kind, icon_px, dark=dark)
            self._tb_icons[kind] = icon
            btn.configure(image=icon, bg=tb_bg, highlightbackground=tb_bg)
        for s in self._tb_seps:
            s.configure(bg=sep_bg)
        if hasattr(self, "text_box"):
            self.text_box.configure(
                bg="#141414" if dark else "white",
                fg="#e8eaed" if dark else "#1a1a1a",
                insertbackground="#e8eaed" if dark else "#1a1a1a",
                highlightbackground=sep_bg,
            )
        if hasattr(self, "preview_holder"):
            self.preview_holder.configure(highlightbackground=sep_bg)

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
        self.text_box = tk.Text(s, height=3, wrap="word", font=("Segoe UI", 11), relief="flat",
                                padx=px(6), pady=px(4), highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT)
        self.text_box.insert("1.0", DEFAULT_TEXT)
        self.text_box.pack(fill="x")
        self.text_box.bind("<KeyRelease>", self.schedule_preview)
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(6), 0))
        ttk.Label(row, text="Alignment").pack(side="left", padx=(0, px(8)))
        segmented(row, ["Left", "Center", "Right"], self.align_var, self.schedule_preview).pack(side="left")

        s = section(t, "Font")
        row = ttk.Frame(s)
        row.pack(fill="x")
        ttk.Label(row, text="Search").pack(side="left", padx=(0, px(8)))
        self.search_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", self.schedule_filter)
        lb_row = ttk.Frame(s)
        lb_row.pack(fill="x", pady=px(6))
        self.listbox = styled_listbox(lb_row, 8)
        sb = ttk.Scrollbar(lb_row, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_font_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self.add_font_fav(self.selected_font))
        row = ttk.Frame(s)
        row.pack(fill="x")
        ttk.Checkbutton(row, text="Bold", variable=self.bold_var,
                        command=self.schedule_preview).pack(side="left")
        ttk.Checkbutton(row, text="Italic", variable=self.italic_var,
                        command=self.schedule_preview).pack(side="left", padx=(px(12), 0))
        self.btn_rescan = ttk.Button(row, text="Rescan Fonts", command=self.rescan)
        self.btn_rescan.pack(side="right")
        ttk.Button(row, text="★ Favorite", command=lambda: self.add_font_fav(self.selected_font)).pack(
            side="right", padx=(0, px(6)))

        s = section(t, "Favorite fonts")
        self.fav_list = styled_listbox(s, 4)
        self.fav_list.pack(fill="x")
        self.fav_list.bind("<<ListboxSelect>>", self.on_fav_select)
        self.fav_list.bind("<Delete>", lambda e: self.remove_selected_fav())
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(4), 0))
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
        main = ttk.Labelframe(body, text="Preview", style="Section.TLabelframe", padding=px(8))
        main.pack(side="left", fill="both", expand=True, padx=(px(10), 0))
        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, px(6)))
        ttk.Button(top, text="Undo", command=self.undo).pack(side="left")
        ttk.Button(top, text="Redo", command=self.redo).pack(side="left", padx=(px(6), 0))
        self.res_label = ttk.Label(top, text="0 x 0", style="Muted.TLabel")
        self.res_label.pack(side="left", padx=px(14))
        segmented(top, list(PREVIEW_BGS), self.bg_toggle,
                  lambda: self.set_preview_bg(self.bg_toggle.get())).pack(side="right")
        ttk.Label(top, text="Background", style="Muted.TLabel").pack(side="right", padx=(0, px(8)))

        holder = tk.Frame(main, highlightthickness=1, highlightbackground=BORDER)
        holder.pack(expand=True, fill="both")
        self.preview_holder = holder
        self.canvas = tk.Canvas(holder, bg=PREVIEW_BGS[self.bg_toggle.get()], highlightthickness=0)
        self.canvas.pack(expand=True, fill="both")
        self.canvas.bind("<Configure>", self.schedule_preview)
        if self.dnd_ok:
            shutil.rmtree(drag_dir(), ignore_errors=True)  # files from earlier sessions
            self.canvas.drag_source_register(1, DND_FILES)
            self.canvas.dnd_bind("<<DragInitCmd>>", self.on_drag_start)
            self.canvas.configure(cursor="hand2")

    def build_footer(self):
        footer = ttk.Frame(self, padding=(px(14), px(7)))
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
        for k, fn in (("e", self.export), ("s", self.save_preset), ("o", self.show_presets),
                      ("n", self.reset_design), ("b", self.toggle_bold), ("i", self.toggle_italic)):
            keys[k] = keys[k.upper()] = lambda e, fn=fn: fn()
        for k, handler in keys.items():
            self.bind_all(f"<Control-{k}>", handler)
        self.bind_all("<F5>", lambda e: self.rescan())
        # Tk's Text widget has its own Ctrl+O (insert newline) and Ctrl+E (end of line)
        # bindings that run before bind_all; handle those keys first and stop there.
        for k in "oOeEnNbBiI":
            self.text_box.bind(f"<Control-{k}>", lambda e, h=keys[k]: (h(e), "break")[1])

    def set_status(self, text, color="gray"):
        c = STATUS_COLORS.get(color, color)
        if DARK_MODE:
            c = {"#5f6368": "#9aa0a6", "#0f7b0f": "#4cc355", "#c42b1c": "#ff6b6b", "#9d5d00": "#f0a020"}.get(c, c)
        self.status_label.configure(text=text, foreground=c)
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
        prefs.setdefault("dark_mode", False)
        return prefs

    def save_prefs(self):
        self.prefs["last_color"] = self.fill.color.value
        self.prefs["last_font"] = self.selected_font
        self.prefs["quality"] = self.quality.get()
        self.prefs["dark_mode"] = self.dark_mode.get()
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
        self.update_title()
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
            self.update_title()
            self.schedule_preview()

    def pick_font(self, name):
        self.selected_font = name
        self.update_title()
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