# -*- coding: utf-8 -*-
"""
MyTE Injector
=============
Gestor de extensiones y temas para el cliente de MyTE.

  1. Localiza myte_client.py (detecta la instalación del instalador o te deja elegirlo).
  2. Lee la carpeta de extensiones: cada .py es una extensión; en  temas/<nombre>/  va cada tema
     (su .py + la imagen, GIF o vídeo de fondo que quieras).
  3. Te las muestra con palomitas. Lo marcado se inyecta; lo desmarcado (si estaba inyectado) se quita.

Cómo inyecta (todo reversible):
  - Copia de seguridad del cliente original:  myte_client.py.orig
  - Copia las extensiones a  <app>/ext/  y añade al final de myte_client.py un pequeño cargador entre
    marcadores  # >>> MYTE-INJECTOR >>>  ...  # <<< MYTE-INJECTOR <<<
  - Si no marcas nada, el cliente vuelve a quedar exactamente como el original.
  - Si una extensión declara  META["requires"] = ["paquete"]  el injector lo instala en el Python de MyTE.

Compilar (igual que el instalador):
  pyinstaller --onefile --noconsole --noupx --name MyTE_Injector --icon myte.ico --add-data "myte.ico;." myte_injector.py
"""
import ast
import hashlib
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

APP = "MyTE Injector"
IS_WIN = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if IS_WIN else 0            # CREATE_NO_WINDOW
DEFAULT_BASE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MyTE")
CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MyTE-Injector")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

MARK_BEGIN = "# >>> MYTE-INJECTOR >>>"
MARK_END = "# <<< MYTE-INJECTOR <<<"
RUNTIME_FILE = "_myte_runtime.py"
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v")
VIDEO_PKG = "PySide6-Addons"
STEPS = ["Comprobar el cliente y las extensiones", "Copia de seguridad del cliente original",
         "Instalar lo que necesiten las extensiones", "Copiar extensiones y temas", "Activar el cargador en MyTE"]

# Lo que el runtime necesita encontrar en myte_client.py para poder engancharse.
REQUIRED_SYMBOLS = ["class MainWindow", "class AppCtl", "class AeroBackground", "class LoginWindow", "class ChatView",
                    "class MessageRow", "def build_qss", "THEMES", "def on_msg", "def refresh_list", "def apply_theme",
                    "def build_profile_menu", "def toggle_theme", "def set_theme"]

# Runtime que se copia a <app>/ext/_myte_runtime.py (vive dentro del injector: no hace falta ningún archivo más).
RUNTIME_SRC = r'''# -*- coding: utf-8 -*-
"""
MyTE Injector - runtime de extensiones
======================================
Lo escribe MyTE Injector en  <app>/ext/_myte_runtime.py  y lo carga el cliente desde el bloque
marcado con  # >>> MYTE-INJECTOR >>>.  No lo edites: se sobrescribe al aplicar cambios.

Carga  ext/*.py  (extensiones)  y  ext/temas/<tema>/*.py  (temas) y les da una API (ExtAPI).
"""
import functools
import importlib.util
import json
import os
import pprint
import random
import re
import shutil
import sys
import time
import traceback
import unicodedata
import hashlib

from PySide6.QtCore import Qt, QSettings, QTimer, QRectF, QPoint, QUrl
from PySide6.QtGui import QColor, QPainter, QPixmap, QImage, QMovie, QPen, QLinearGradient, QRadialGradient, QIcon
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
                               QCheckBox, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QSlider, QListWidget,
                               QListWidgetItem, QStackedWidget, QScrollArea, QFrame, QFileDialog, QColorDialog,
                               QMenu, QMessageBox)

RUNTIME_VERSION = 2
EXT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(EXT_DIR, "runtime.log")
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v")
ANIM_EXT = (".gif", ".webp")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp")
COLOR_KEYS = [("accent", "Acento"), ("accent2", "Acento 2"), ("bg1", "Fondo (arriba)"), ("bg2", "Fondo (medio)"),
              ("bg3", "Fondo (abajo)"), ("text", "Texto")]
BUILTIN_LABEL = {"light": "☀️  Claro (Frutiger Aero)", "dark": "🌙  Oscuro (Space era)"}


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def guard(fn, *a, **k):
    """Ejecuta fn; si falla lo apunta en runtime.log y sigue (una extensión rota no tumba MyTE)."""
    try:
        return fn(*a, **k)
    except Exception:
        log("Error en %s:\n%s" % (getattr(fn, "__name__", fn), traceback.format_exc()))
        return None


def patch(cls, name, fn):
    """Sustituye cls.name por un envoltorio  fn(original, self, *args, **kwargs)."""
    orig = getattr(cls, name)

    @functools.wraps(orig)
    def wrapper(self, *a, **k):
        return fn(orig, self, *a, **k)
    setattr(cls, name, wrapper)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fit_rects(sw, sh, dst, fit):
    """Devuelve (rectángulo destino, rectángulo origen) para cover / contain / stretch."""
    dw, dh = dst.width(), dst.height()
    if fit == "stretch":
        return QRectF(dst), QRectF(0, 0, sw, sh)
    if fit == "contain":
        k = min(dw / sw, dh / sh)
        tw, th = sw * k, sh * k
        return QRectF(dst.x() + (dw - tw) / 2, dst.y() + (dh - th) / 2, tw, th), QRectF(0, 0, sw, sh)
    k = max(dw / sw, dh / sh)                      # cover: llena la ventana y recorta lo que sobre
    cw, ch = dw / k, dh / k
    return QRectF(dst), QRectF((sw - cw) / 2, (sh - ch) / 2, cw, ch)


def coerce(v, default):
    if v is None:
        return default
    try:
        if isinstance(default, bool):
            return str(v).lower() in ("1", "true", "yes", "si", "sí")
        if isinstance(default, int):
            return int(float(v))
        if isinstance(default, float):
            return float(v)
    except (TypeError, ValueError):
        return default
    return v


# ============================================================ fondos de los temas
class Backdrop:
    """Fondo de un tema: decoración dibujada (estrellas, rejilla) + imagen / GIF / vídeo con opacidad."""

    def __init__(self, rt, tid):
        self.rt, self.tid = rt, tid
        self.kind = self.pix = self.movie = self.player = self.sink = self.audio = None
        self.frame, self.cache, self.decor, self.qmp = None, None, None, None
        self.active, self._t = False, 0.0
        self.c = {}
        self.reload()

    def reload(self):
        self.stop()
        self.kind = self.pix = self.movie = self.player = self.sink = self.audio = None
        self.frame = self.cache = self.decor = None
        self.c = self.rt.bg_cfg(self.tid)
        f = self.c.get("file")
        if f and os.path.isfile(f):
            ext = os.path.splitext(f)[1].lower()
            try:
                if ext in VIDEO_EXT:
                    self._load_video(f)
                elif ext in ANIM_EXT:
                    m = QMovie(f)
                    if m.isValid() and (ext == ".gif" or m.frameCount() > 1):
                        m.setCacheMode(QMovie.CacheAll if m.frameCount() and m.frameCount() < 60 else QMovie.CacheNone)
                        m.frameChanged.connect(lambda _=0: self.rt.repaint_bg())
                        self.movie, self.kind = m, "movie"
                if self.kind is None and not (ext in VIDEO_EXT):
                    pm = QPixmap(f)
                    if not pm.isNull():
                        self.pix, self.kind = pm, "image"
            except Exception:
                log("No se pudo cargar el fondo %s:\n%s" % (f, traceback.format_exc()))
        if self.active:
            self.start()

    def refresh_cfg(self):
        self.c = self.rt.bg_cfg(self.tid)
        self.decor = None

    def _load_video(self, f):
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QAudioOutput
        except Exception:
            self.rt.need_multimedia()
            return
        self.qmp = QMediaPlayer
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.audio.setVolume(0)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink()
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.setSource(QUrl.fromLocalFile(f))
        self.kind = "video"

    def _on_frame(self, frame):
        now = time.time()
        if now - self._t < 0.03:
            return
        self._t = now
        img = frame.toImage()
        if not img.isNull():
            self.frame = img
            self.rt.repaint_bg()

    def _status(self, st):
        if self.qmp is not None and st == self.qmp.MediaStatus.EndOfMedia and self.active:
            self.player.setPosition(0)
            self.player.play()

    def start(self):
        if self.movie is not None:
            self.movie.start()
        if self.player is not None:
            self.player.play()

    def stop(self):
        if self.movie is not None:
            self.movie.stop()
        if self.player is not None:
            self.player.pause()

    def set_active(self, on):
        if on == self.active:
            return
        self.active = on
        self.start() if on else self.stop()

    # -- pintado
    def _source(self):
        if self.kind == "movie":
            return self.movie.currentPixmap()
        if self.kind == "video":
            return self.frame
        return None

    def _decor_pixmap(self, size):
        key = (size.width(), size.height())
        if self.decor is not None and self.decor[0] == key:
            return self.decor[1]
        c = self.c
        pm = QPixmap(size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = size.width(), size.height()
        for (cx, cy), col in zip(((0.18, 0.25), (0.85, 0.78), (0.62, 0.08)), c.get("glow") or []):
            qc = QColor(col)
            rg = QRadialGradient(cx * w, cy * h, max(w, h) * 0.55)
            qc.setAlpha(80)
            rg.setColorAt(0, qc)
            qc.setAlpha(0)
            rg.setColorAt(1, qc)
            p.fillRect(pm.rect(), rg)
        style = c.get("style")
        if style == "stars":
            rnd = random.Random(sum(map(ord, self.tid)))
            base = QColor(c.get("star_color") or "#dbe6ff")
            p.setPen(Qt.NoPen)
            for i in range(260):
                x, y, r, a = rnd.random() * w, rnd.random() * h, rnd.uniform(0.5, 2.1), rnd.random()
                base.setAlpha(int(60 + 170 * a))
                p.setBrush(base)
                p.drawEllipse(QRectF(x, y, r, r))
                if i < 14:                                     # unas pocas estrellas con destello
                    pen = QPen(base, 1)
                    p.setPen(pen)
                    p.drawLine(int(x - 6 * r), int(y + r / 2), int(x + 7 * r), int(y + r / 2))
                    p.drawLine(int(x + r / 2), int(y - 6 * r), int(x + r / 2), int(y + 7 * r))
                    p.setPen(Qt.NoPen)
        elif style == "grid":
            step = max(12, int(c.get("grid_size") or 48))
            col = QColor(c.get("grid_color") or "#ff1744")
            col.setAlpha(int(c.get("grid_alpha") or 40))
            p.setPen(QPen(col, 1))
            for x in range(0, w, step):
                p.drawLine(x, 0, x, h)
            for y in range(0, h, step):
                p.drawLine(0, y, w, y)
            p.setPen(QPen(QColor(0, 0, 0, 34), 1))
            for y in range(0, h, 3):                           # líneas de barrido tipo monitor
                p.drawLine(0, y, w, y)
        p.end()
        self.decor = (key, pm)
        return pm

    def paint(self, p, rect):
        c = self.c
        if c.get("style") or c.get("glow"):
            p.drawPixmap(rect.topLeft(), self._decor_pixmap(rect.size()))
        op = max(0.0, min(1.0, float(c.get("opacity", 0.6))))
        fit = c.get("fit") or "cover"
        if self.kind == "image" and self.pix is not None:
            key = (rect.width(), rect.height(), fit)
            if self.cache is None or self.cache[0] != key:
                pm = QPixmap(rect.size())
                pm.fill(Qt.transparent)
                q = QPainter(pm)
                q.setRenderHint(QPainter.SmoothPixmapTransform)
                tr, sr = fit_rects(self.pix.width(), self.pix.height(), QRectF(0, 0, rect.width(), rect.height()), fit)
                q.drawPixmap(tr, self.pix, sr)
                q.end()
                self.cache = (key, pm)
            p.save()
            p.setOpacity(op)
            p.drawPixmap(rect.topLeft(), self.cache[1])
            p.restore()
            return
        src = self._source()
        if src is None or not src.width() or not src.height():
            return
        tr, sr = fit_rects(src.width(), src.height(), QRectF(rect), fit)
        p.save()
        p.setOpacity(op)
        if isinstance(src, QImage):
            p.drawImage(tr, src, sr)
        else:
            p.drawPixmap(tr, src, sr)
        p.restore()


# ================================================================ API de extensiones
class ExtAPI:
    """Lo que recibe  setup(api)  de cada extensión."""

    def __init__(self, rt, meta, path, mod):
        self.rt, self.meta, self.path, self.mod = rt, meta, path, mod
        self.id = meta["id"]
        self.dir = os.path.dirname(path)
        self.g = rt.g                       # globales del cliente: MainWindow, ChatView, THEMES, ...
        self.THEMES = rt.g["THEMES"]
        self._defaults = {f["key"]: f.get("default") for f in meta.get("settings", []) if "key" in f}
        self._change_cbs = []

    # -- ventanas
    @property
    def win(self):
        return self.rt.win

    @property
    def ctl(self):
        return self.rt.ctl

    # -- ajustes propios (se guardan en QSettings y los edita la ventana de ajustes)
    def get(self, key, default=None):
        d = self._defaults.get(key, default)
        return coerce(self.rt.qs.value("ext/%s/%s" % (self.id, key), None), d)

    def set(self, key, value):
        self.rt.qs.setValue("ext/%s/%s" % (self.id, key), value)
        for cb in list(self._change_cbs):
            guard(cb, key, value)

    def on_change(self, fn):
        self._change_cbs.append(fn)

    # -- eventos: main_window, chat_view, message_row, msg, refresh_list, apply_theme
    def on(self, event, fn):
        self.rt.listeners.setdefault(event, []).append(fn)

    def patch(self, cls, name, fn):
        if isinstance(cls, str):
            cls = self.g[cls]
        patch(cls, name, fn)

    def wrap(self, cls, name, fn):
        """fn(original, self, *args, **kwargs): tú decides cuándo llamar al original. Si fn falla, se llama al original."""
        if isinstance(cls, str):
            cls = self.g[cls]
        orig = getattr(cls, name)

        @functools.wraps(orig)
        def wrapper(obj, *a, **k):
            called = []

            def inner(*aa, **kk):
                called.append(1)
                return orig(*aa, **kk)
            try:
                return fn(inner, obj, *a, **k)
            except Exception:
                log("[%s] Error en wrap %s.%s:\n%s" % (self.id, cls.__name__, name, traceback.format_exc()))
                if called:
                    return None
                return orig(obj, *a, **k)
        setattr(cls, name, wrapper)

    def after(self, cls, name, fn):
        """fn(self, resultado, *args, **kwargs) se ejecuta DESPUÉS del método original."""
        if isinstance(cls, str):
            cls = self.g[cls]
        orig = getattr(cls, name)

        @functools.wraps(orig)
        def wrapper(obj, *a, **k):
            r = orig(obj, *a, **k)
            guard(fn, obj, r, *a, **k)
            return r
        setattr(cls, name, wrapper)

    def css(self, fn):
        """fn(tema) -> str con QSS; se añade al estilo de cada tema (tema = dict de colores del cliente)."""
        self.rt.css_fns.append(fn)
        if self.rt.ctl and self.rt.ctl.main:
            self.rt.ctl.apply_theme()

    # -- utilidades
    def register_theme(self, tid, spec, folder=None):
        return self.rt.register_theme(tid, spec, folder or self.dir)

    def set_theme(self, tid):
        self.rt.set_theme(tid)

    def add_qss(self, css):
        self.rt.extra_qss.append(css)
        if self.rt.ctl and self.rt.ctl.main:
            self.rt.ctl.apply_theme()

    def toast(self, text, err=False):
        if self.rt.win:
            guard(self.rt.win.toast, text, err)

    def open_settings(self):
        self.rt.open_settings(self.rt.win)

    def log(self, msg):
        log("[%s] %s" % (self.id, msg))


# ==================================================================== ajustes (ventana)
def _color_button(current, on_pick):
    b = QPushButton(current)
    b.setObjectName("ghost")

    def paint(col):
        b.setText(col)
        b.setStyleSheet("QPushButton { border-left: 18px solid %s; }" % col)

    def pick():
        c = QColorDialog.getColor(QColor(b.text()), b, "Elige un color")
        if c.isValid():
            paint(c.name())
            on_pick(c.name())
    paint(current)
    b.clicked.connect(pick)
    return b


def build_form(api, fields):
    """Genera el formulario de ajustes a partir de META['settings']."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for f in fields:
        key, typ, label = f.get("key"), f.get("type", "text"), f.get("label", f.get("key", ""))
        if typ == "info":
            l = QLabel(label)
            l.setObjectName("sub")
            l.setWordWrap(True)
            lay.addWidget(l)
            continue
        cur = api.get(key)
        if typ == "bool":
            cb = QCheckBox(label)
            cb.setChecked(bool(cur))
            cb.toggled.connect(lambda v, k=key: api.set(k, v))
            lay.addWidget(cb)
            if f.get("help"):
                h = QLabel(f["help"])
                h.setObjectName("faint")
                h.setWordWrap(True)
                lay.addWidget(h)
            continue
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setMinimumWidth(170)
        row.addWidget(lab)
        if typ in ("int", "float"):
            sp = QSpinBox() if typ == "int" else QDoubleSpinBox()
            sp.setRange(f.get("min", 0), f.get("max", 9999))
            if typ == "float":
                sp.setSingleStep(f.get("step", 0.1))
            sp.setValue(cur if cur is not None else 0)
            sp.valueChanged.connect(lambda v, k=key: api.set(k, v))
            row.addWidget(sp)
            row.addStretch(1)
        elif typ == "slider":
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(int(round(float(cur or 0) * 100)))
            pc = QLabel("%d%%" % sl.value())
            pc.setMinimumWidth(38)

            def moved(v, k=key, pc=pc):
                pc.setText("%d%%" % v)
                api.set(k, v / 100.0)
            sl.valueChanged.connect(moved)
            row.addWidget(sl, 1)
            row.addWidget(pc)
        elif typ == "color":
            row.addWidget(_color_button(str(cur or "#ffffff"), lambda c, k=key: api.set(k, c)))
            row.addStretch(1)
        elif typ == "choice":
            cbx = QComboBox()
            for ch in f.get("choices", []):
                v, t = (ch if isinstance(ch, (list, tuple)) else (ch, ch))
                cbx.addItem(str(t), v)
            i = cbx.findData(cur)
            cbx.setCurrentIndex(max(0, i))
            cbx.currentIndexChanged.connect(lambda _i, k=key, c=cbx: api.set(k, c.currentData()))
            row.addWidget(cbx, 1)
        elif typ == "file":
            e = QLineEdit(str(cur or ""))
            e.setReadOnly(True)
            e.setPlaceholderText("(ninguno)")
            b = QPushButton("Elegir…")
            b.setObjectName("ghost")
            x = QPushButton("Quitar")
            x.setObjectName("ghost")

            def choose(k=key, e=e, flt=f.get("filter", "Todos (*.*)")):
                fn, _ = QFileDialog.getOpenFileName(e, "Elige un archivo", e.text() or os.path.expanduser("~"), flt)
                if fn:
                    e.setText(fn)
                    api.set(k, fn)

            def clear(k=key, e=e):
                e.clear()
                api.set(k, "")
            b.clicked.connect(choose)
            x.clicked.connect(clear)
            row.addWidget(e, 1)
            row.addWidget(b)
            row.addWidget(x)
        else:
            e = QLineEdit(str(cur or ""))
            e.editingFinished.connect(lambda k=key, e=e: api.set(k, e.text()))
            row.addWidget(e, 1)
        lay.addLayout(row)
        if f.get("help"):
            h = QLabel(f["help"])
            h.setObjectName("faint")
            h.setWordWrap(True)
            lay.addWidget(h)
    return w


class ExtSettings(QDialog):
    """Ventana «Ajustes de extensiones»: una página por extensión y otra por tema."""

    def __init__(self, rt, parent):
        super().__init__(parent)
        self.rt = rt
        self.setWindowTitle("Ajustes de extensiones")
        self.resize(800, 540)
        root = QVBoxLayout(self)
        t = QLabel("🧩  Ajustes de extensiones")
        t.setObjectName("title")
        root.addWidget(t)
        row = QHBoxLayout()
        root.addLayout(row, 1)
        self.list = QListWidget()
        self.list.setFixedWidth(230)
        self.stack = QStackedWidget()
        row.addWidget(self.list)
        row.addWidget(self.stack, 1)
        self._section("EXTENSIONES", bool(rt.exts))
        for api in rt.exts:
            self._page(api.meta.get("name", api.id), lambda a=api: self._ext_page(a))
        themes = rt.custom_themes()
        self._section("TEMAS", bool(themes))
        for tid, t in themes:
            self._page("🎨  " + t.get("label", tid), lambda tid=tid: self._theme_page(tid))
        self.list.currentRowChanged.connect(self._go)
        first = next((i for i in range(self.list.count()) if self.list.item(i).flags() & Qt.ItemIsSelectable), -1)
        self.list.setCurrentRow(first)
        cl = QPushButton("Cerrar")
        cl.setObjectName("ghost")
        cl.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(cl)
        root.addLayout(bottom)

    def _section(self, title, show):
        if not show:
            return
        it = QListWidgetItem(title)
        it.setFlags(Qt.NoItemFlags)
        self.list.addItem(it)

    def _page(self, title, builder):
        self.list.addItem(QListWidgetItem(title))
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.stack.addWidget(holder)
        holder._builder, holder._built = builder, False
        self.list.item(self.list.count() - 1).setData(Qt.UserRole, holder)

    def _go(self, row):
        it = self.list.item(row)
        holder = it.data(Qt.UserRole) if it else None
        if holder is None:
            return
        if not holder._built:
            holder._built = True
            w = QWidget()
            w.setObjectName("extpage")
            w.setStyleSheet("#extpage { background: transparent; }")
            holder.viewport().setStyleSheet("background: transparent;")
            lay = QVBoxLayout(w)
            lay.setContentsMargins(14, 6, 14, 6)
            try:
                lay.addWidget(holder._builder())
            except Exception:
                log("Error creando la página de ajustes:\n" + traceback.format_exc())
                lay.addWidget(QLabel("No se pudo crear esta página de ajustes (mira ext/runtime.log)."))
            lay.addStretch(1)
            holder.setWidget(w)
        self.stack.setCurrentWidget(holder)

    def _ext_page(self, api):
        m = api.meta
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        h = QLabel(m.get("name", api.id))
        h.setObjectName("title")
        lay.addWidget(h)
        info = "  ·  ".join(x for x in (("v" + str(m["version"])) if m.get("version") else "", m.get("author", "")) if x)
        if info:
            s = QLabel(info)
            s.setObjectName("faint")
            lay.addWidget(s)
        if m.get("description"):
            d = QLabel(m["description"])
            d.setObjectName("sub")
            d.setWordWrap(True)
            lay.addWidget(d)
        sep = QFrame()
        sep.setObjectName("sep")
        lay.addWidget(sep)
        got = False
        panel = getattr(api.mod, "settings_panel", None)
        if callable(panel):
            pw = panel(api, w)
            if pw is not None:
                lay.addWidget(pw)
                got = True
        if m.get("settings"):
            lay.addWidget(build_form(api, m["settings"]))
            got = True
        if not got:
            n = QLabel("Esta extensión no tiene ajustes.")
            n.setObjectName("sub")
            lay.addWidget(n)
        return w

    def _theme_page(self, tid):
        rt = self.rt
        t = THEMES_REF()[tid]
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        h = QLabel("🎨  " + t.get("label", tid))
        h.setObjectName("title")
        lay.addWidget(h)
        d = QLabel("Carpeta del tema: " + (t.get("_dir") or "?"))
        d.setObjectName("faint")
        d.setWordWrap(True)
        lay.addWidget(d)
        use = QPushButton("Usar este tema ahora")
        use.setObjectName("green")
        use.clicked.connect(lambda: rt.set_theme(tid))
        lay.addWidget(use, 0, Qt.AlignLeft)
        sep = QFrame()
        sep.setObjectName("sep")
        lay.addWidget(sep)
        c = rt.bg_cfg(tid)
        # opacidad
        row = QHBoxLayout()
        row.addWidget(QLabel("Opacidad del fondo"))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        sl.setValue(int(round(float(c.get("opacity", 0.6)) * 100)))
        pc = QLabel("%d%%" % sl.value())
        pc.setMinimumWidth(38)

        def moved(v):
            pc.setText("%d%%" % v)
            rt.tset(tid, "opacity", v / 100.0)
            rt.theme_refresh(tid, light=True)
        sl.valueChanged.connect(moved)
        row.addWidget(sl, 1)
        row.addWidget(pc)
        lay.addLayout(row)
        # fondo (imagen / gif / vídeo)
        lay.addWidget(QLabel("Fondo (imagen, GIF animado o vídeo)"))
        row = QHBoxLayout()
        e = QLineEdit(os.path.basename(c.get("file") or ""))
        e.setReadOnly(True)
        e.setPlaceholderText("(sin fondo)")
        b = QPushButton("Elegir…")
        b.setObjectName("ghost")
        x = QPushButton("Quitar")
        x.setObjectName("ghost")
        r = QPushButton("Del tema")
        r.setObjectName("ghost")

        def choose():
            fn, _ = QFileDialog.getOpenFileName(w, "Elige un fondo", os.path.expanduser("~"),
                                                "Imágenes y vídeos (*.png *.jpg *.jpeg *.bmp *.webp *.gif "
                                                "*.mp4 *.webm *.mkv *.avi *.mov *.m4v);;Todos (*.*)")
            if fn:
                rt.tset(tid, "file", fn)
                e.setText(os.path.basename(fn))
                rt.theme_refresh(tid)

        def clear():
            rt.tset(tid, "file", "-")
            e.setText("")
            rt.theme_refresh(tid)

        def default():
            rt.tset(tid, "file", None)
            e.setText(os.path.basename(rt.bg_cfg(tid).get("file") or ""))
            rt.theme_refresh(tid)
        b.clicked.connect(choose)
        x.clicked.connect(clear)
        r.clicked.connect(default)
        row.addWidget(e, 1)
        for btn in (b, x, r):
            row.addWidget(btn)
        lay.addLayout(row)
        vh = QLabel("Los vídeos necesitan PySide6-Addons: MyTE Injector lo instala si hay un vídeo en la carpeta del tema "
                    "o si marcas «soporte de vídeo».")
        vh.setObjectName("faint")
        vh.setWordWrap(True)
        lay.addWidget(vh)
        lay.addWidget(QLabel("Colores"))
        for key, label in COLOR_KEYS:
            row = QHBoxLayout()
            lb = QLabel(label)
            lb.setMinimumWidth(130)
            row.addWidget(lb)
            row.addWidget(_color_button(str(t.get(key, "#ffffff")),
                                        lambda col, k=key: (rt.tset(tid, "color/" + k, col), rt.theme_refresh(tid))))
            row.addStretch(1)
            lay.addLayout(row)
        rs = QPushButton("Restablecer este tema")
        rs.setObjectName("ghost")

        def reset():
            rt.treset(tid)
            rt._pending = tid
            rt._refresh_now()
            holder = self.list.currentItem().data(Qt.UserRole)
            holder._built = False
            QTimer.singleShot(0, lambda: self._go(self.list.currentRow()))
        rs.clicked.connect(reset)
        lay.addWidget(rs, 0, Qt.AlignLeft)
        return w


# ==================================================================== creador de temas
def _mixc(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _lt(c, t):
    return _mixc(c, QColor("#ffffff"), t)


def _dk(c, t):
    return _mixc(c, QColor("#000000"), t)


def _rgba(c, a):
    return "rgba(%d,%d,%d,%d)" % (c.red(), c.green(), c.blue(), a)


def make_palette(cols, mode):
    """A partir de 6 colores saca la paleta completa que necesita el cliente (botones, burbujas, cristal...)."""
    ac, ac2, tx = QColor(cols["accent"]), QColor(cols["accent2"]), QColor(cols["text"])
    b1, b2 = QColor(cols["bg1"]), QColor(cols["bg2"])
    dark = mode != "light"
    p = dict(cols)
    p["sub"] = _mixc(tx, b2, 0.35).name()
    p["faint"] = _mixc(tx, b2, 0.6).name()
    lum = (0.299 * ac.red() + 0.587 * ac.green() + 0.114 * ac.blue()) / 255.0
    p["accent_txt"] = "#10131a" if lum > 0.6 else "#ffffff"
    p.update(btn_top=_lt(ac, 0.45).name(), btn_mid=ac.name(), btn_mid2=_dk(ac, 0.15).name(),
             btn_bot=_lt(ac, 0.15).name(), btn_edge=_dk(ac, 0.45).name(),
             green_top=_lt(ac2, 0.45).name(), green_mid=ac2.name(), green_mid2=_dk(ac2, 0.15).name(),
             green_bot=_lt(ac2, 0.15).name(), green_edge=_dk(ac2, 0.45).name())
    if dark:
        p.update(glass=_rgba(_mixc(b2, ac, 0.06), 175), glass2=_rgba(_mixc(b2, ac, 0.12), 215),
                 glass_edge=_rgba(ac, 110), line=_rgba(ac, 60),
                 mine_a=_dk(ac, 0.25).name(), mine_b=_mixc(_dk(ac2, 0.5), _dk(ac, 0.45), 0.5).name(),
                 mine_edge=_lt(ac, 0.35).name(), mine_txt="#ffffff",
                 other_a=_lt(b2, 0.08).name(), other_b=_lt(b1, 0.03).name(), other_edge=_mixc(b2, ac, 0.35).name(),
                 other_txt=tx.name(), input_bg=_rgba(_dk(b1, 0.2), 225), rail=_rgba(_dk(b1, 0.3), 160))
    else:
        w = QColor("#ffffff")
        p.update(glass=_rgba(w, 150), glass2=_rgba(w, 205), glass_edge=_rgba(w, 235), line=_rgba(ac, 70),
                 mine_a=_lt(ac2, 0.7).name(), mine_b=_lt(ac2, 0.4).name(), mine_edge=ac2.name(),
                 mine_txt=_dk(ac2, 0.75).name(), other_a="#ffffff", other_b=_lt(ac, 0.88).name(),
                 other_edge=_lt(ac, 0.55).name(), other_txt=tx.name(), input_bg=_rgba(w, 235), rail=_rgba(w, 120))
    p.update(sel=_rgba(ac, 60), hover=_rgba(ac, 40))
    return p


def colors_from_image(path):
    """Saca una paleta (acento, fondo...) de una imagen. Devuelve None si no se puede leer."""
    img = QImage(path)
    if img.isNull():
        return None
    img = img.scaled(48, 27, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    best, bs, r_, g_, b_, n = None, -1.0, 0, 0, 0, 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            r_, g_, b_, n = r_ + c.red(), g_ + c.green(), b_ + c.blue(), n + 1
            sc = c.saturationF() * c.valueF() if c.valueF() > 0.3 else 0
            if sc > bs:
                best, bs = c, sc
    avg = QColor(r_ // n, g_ // n, b_ // n)
    bh = max(avg.hueF(), 0.0)
    acc = best or QColor("#3fd9ff")
    ah = max(acc.hueF(), 0.0)
    acc = QColor.fromHsvF(ah, min(1.0, max(acc.saturationF(), 0.6)), max(acc.valueF(), 0.9))
    acc2 = QColor.fromHsvF((ah + 0.11) % 1.0, 0.65, 1.0)
    sat = min(1.0, max(avg.saturationF(), 0.4))
    return dict(accent=acc.name(), accent2=acc2.name(), bg1=QColor.fromHsvF(bh, sat, 0.07).name(),
                bg2=QColor.fromHsvF(bh, sat, 0.14).name(), bg3=QColor.fromHsvF(bh, sat, 0.04).name(), text="#f4f4f6")


def _slug(name):
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_") or "tema"


def _sha_dir(folder):
    h = hashlib.sha1()
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for n in sorted(files):
            fp = os.path.join(root, n)
            h.update(os.path.relpath(fp, folder).encode("utf-8", "replace"))
            if n.lower().endswith(".py"):
                h.update(open(fp, "rb").read())
            else:
                h.update(str(os.path.getsize(fp)).encode())
    return h.hexdigest()


def _paint_color_btn(b, col):
    b.setText(col)
    b.setStyleSheet("QPushButton { border-left: 18px solid %s; }" % col)


class ThemeMaker(QDialog):
    """Creador de temas: nombre + imagen/GIF/vídeo + colores, sin tocar código."""
    FITS = [("cover", "Rellenar (recorta lo que sobre)"), ("contain", "Ajustar (se ve entera)"), ("stretch", "Estirar")]
    STYLES = [("", "Ninguna"), ("stars", "Estrellas"), ("grid", "Rejilla")]

    def __init__(self, rt, parent):
        super().__init__(parent)
        self.rt, self.cur, self.media, self._loading = rt, None, None, False
        d = rt.g["THEMES"]["dark"]
        self.colors = {k: str(d[k]) for k, _ in COLOR_KEYS}
        self.setWindowTitle("Creador de temas")
        self.resize(720, 680)
        root = QVBoxLayout(self)
        t = QLabel("🖌️  Creador de temas")
        t.setObjectName("title")
        root.addWidget(t)
        s = QLabel("Ponle nombre, elige una imagen, GIF o vídeo de fondo y los colores. Al guardar se aplica al momento.")
        s.setObjectName("sub")
        s.setWordWrap(True)
        root.addWidget(s)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        root.addWidget(sc, 1)
        body = QWidget()
        sc.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setSpacing(9)
        # tema a editar
        row = QHBoxLayout()
        row.addWidget(QLabel("Tema"))
        self.pick = QComboBox()
        row.addWidget(self.pick, 1)
        self.btn_del = QPushButton("Borrar")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self.delete)
        row.addWidget(self.btn_del)
        lay.addLayout(row)
        # nombre y modo
        row = QHBoxLayout()
        row.addWidget(QLabel("Nombre"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Mi tema")
        row.addWidget(self.name, 1)
        self.mode = QComboBox()
        self.mode.addItems(["Oscuro", "Claro"])
        row.addWidget(self.mode)
        lay.addLayout(row)
        # fondo
        lay.addWidget(self._title("Fondo (imagen, GIF animado o vídeo)"))
        row = QHBoxLayout()
        self.prev = QLabel("Sin fondo")
        self.prev.setAlignment(Qt.AlignCenter)
        self.prev.setFixedSize(256, 144)
        self.prev.setStyleSheet("border: 1px dashed rgba(150,150,150,140); border-radius: 8px;")
        row.addWidget(self.prev)
        col = QVBoxLayout()
        self.file_lbl = QLabel("")
        self.file_lbl.setObjectName("faint")
        self.file_lbl.setWordWrap(True)
        col.addWidget(self.file_lbl)
        b = QPushButton("Elegir archivo…")
        b.clicked.connect(self.choose)
        x = QPushButton("Quitar fondo")
        x.setObjectName("ghost")
        x.clicked.connect(lambda: self.set_media(None))
        self.magic = QPushButton("🪄 Sacar colores de la imagen")
        self.magic.setObjectName("ghost")
        self.magic.clicked.connect(self.auto_colors)
        for w in (b, x, self.magic):
            col.addWidget(w)
        col.addStretch(1)
        row.addLayout(col, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Ajuste"))
        self.fit = QComboBox()
        for k, l in self.FITS:
            self.fit.addItem(l, k)
        row.addWidget(self.fit, 1)
        row.addWidget(QLabel("Decoración"))
        self.style = QComboBox()
        for k, l in self.STYLES:
            self.style.addItem(l, k)
        row.addWidget(self.style)
        lay.addLayout(row)
        self.op, self.opl = self._slider(lay, "Opacidad del fondo", 0, 100, 70, "%d%%")
        self.rad, self.radl = self._slider(lay, "Bordes (0 = cuadrados)", 0, 150, 100, "%d%%")
        # colores
        lay.addWidget(self._title("Colores"))
        self.cbtn = {}
        for key, label in COLOR_KEYS:
            row = QHBoxLayout()
            lb = QLabel(label)
            lb.setMinimumWidth(130)
            row.addWidget(lb)
            cb = QPushButton(self.colors[key])
            cb.setObjectName("ghost")
            _paint_color_btn(cb, self.colors[key])
            cb.clicked.connect(lambda _=False, k=key: self.pick_color(k))
            self.cbtn[key] = cb
            row.addWidget(cb)
            row.addStretch(1)
            lay.addLayout(row)
        h = QLabel("Con el acento y los colores de fondo se calculan solos los botones, burbujas y paneles.")
        h.setObjectName("faint")
        h.setWordWrap(True)
        lay.addWidget(h)
        lay.addStretch(1)
        # botones
        bot = QHBoxLayout()
        bot.addStretch(1)
        use = QPushButton("Guardar y usar")
        use.setObjectName("green")
        use.clicked.connect(lambda: self.save(True))
        sv = QPushButton("Guardar")
        sv.clicked.connect(lambda: self.save(False))
        cl = QPushButton("Cerrar")
        cl.setObjectName("ghost")
        cl.clicked.connect(self.accept)
        for w in (use, sv, cl):
            bot.addWidget(w)
        root.addLayout(bot)
        self.fill_pick()
        self.pick.currentIndexChanged.connect(self.on_pick)
        self.on_pick()

    # -- piezas
    @staticmethod
    def _title(text):
        l = QLabel(text)
        l.setObjectName("h2")
        return l

    def _slider(self, lay, label, lo, hi, val, fmt):
        row = QHBoxLayout()
        lb = QLabel(label)
        lb.setMinimumWidth(170)
        row.addWidget(lb)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        pc = QLabel(fmt % val)
        pc.setMinimumWidth(44)
        sl.valueChanged.connect(lambda v: pc.setText(fmt % v))
        row.addWidget(sl, 1)
        row.addWidget(pc)
        lay.addLayout(row)
        return sl, pc

    def fill_pick(self, select=None):
        self.pick.blockSignals(True)
        self.pick.clear()
        self.pick.addItem("✨  Nuevo tema", None)
        for tid, t in self.rt.custom_themes():
            self.pick.addItem("🎨  " + t.get("label", tid), tid)
        i = self.pick.findData(select)
        self.pick.setCurrentIndex(max(i, 0))
        self.pick.blockSignals(False)

    # -- cargar / elegir
    def on_pick(self, *_):
        tid = self.pick.currentData()
        self.cur = tid
        self.btn_del.setVisible(tid is not None)
        self._loading = True
        try:
            if tid is None:
                d = self.rt.g["THEMES"]["dark"]
                self.colors = {k: str(d[k]) for k, _ in COLOR_KEYS}
                self.name.setText("")
                self.mode.setCurrentIndex(0)
                self.fit.setCurrentIndex(0)
                self.style.setCurrentIndex(0)
                self.op.setValue(70)
                self.rad.setValue(100)
                self.set_media(None)
            else:
                t = self.rt.g["THEMES"][tid]
                spec = t.get("_spec") or {}
                c = self.rt.bg_cfg(tid)
                self.colors = {k: str(t.get(k, "#ffffff")) for k, _ in COLOR_KEYS}
                self.name.setText(t.get("label", tid))
                self.mode.setCurrentIndex(1 if t.get("name") == "light" else 0)
                self.fit.setCurrentIndex(max(0, self.fit.findData(c.get("fit", "cover"))))
                self.style.setCurrentIndex(max(0, self.style.findData(c.get("style") or "")))
                self.op.setValue(int(round(float(c.get("opacity", 0.6)) * 100)))
                self.rad.setValue(int(round(float(spec.get("radius_scale", 1.0)) * 100)))
                self.set_media(c.get("file"))
            for k, cb in self.cbtn.items():
                _paint_color_btn(cb, self.colors[k])
        finally:
            self._loading = False

    def set_media(self, path):
        self.media = path if path and os.path.isfile(path) else None
        self.magic.setEnabled(bool(self.media) and not self.media.lower().endswith(VIDEO_EXT))
        if not self.media:
            self.prev.setPixmap(QPixmap())
            self.prev.setText("Sin fondo")
            self.file_lbl.setText("")
            return
        self.file_lbl.setText(os.path.basename(self.media))
        if self.media.lower().endswith(VIDEO_EXT):
            self.prev.setPixmap(QPixmap())
            self.prev.setText("🎬  Vídeo")
            return
        pm = QPixmap(self.media)
        if pm.isNull():
            self.prev.setText("No se puede leer")
        else:
            self.prev.setPixmap(pm.scaled(self.prev.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def choose(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Elige un fondo", os.path.expanduser("~"),
                                            "Imágenes y vídeos (*.png *.jpg *.jpeg *.bmp *.webp *.gif "
                                            "*.mp4 *.webm *.mkv *.avi *.mov *.m4v);;Todos (*.*)")
        if fn:
            self.set_media(fn)
            if not self.name.text().strip():
                self.name.setText(os.path.splitext(os.path.basename(fn))[0][:30])

    def pick_color(self, key):
        c = QColorDialog.getColor(QColor(self.colors[key]), self, "Elige un color")
        if c.isValid():
            self.colors[key] = c.name()
            _paint_color_btn(self.cbtn[key], c.name())

    def auto_colors(self):
        res = colors_from_image(self.media) if self.media else None
        if not res:
            QMessageBox.information(self, "Creador de temas", "No he podido leer colores de este archivo.")
            return
        self.colors.update(res)
        for k, cb in self.cbtn.items():
            _paint_color_btn(cb, self.colors[k])

    # -- guardar
    def _new_id(self, name):
        base, tid, n = _slug(name), _slug(name), 2
        while tid in self.rt.g["THEMES"]:
            tid = "%s_%d" % (base, n)
            n += 1
        return tid

    def save(self, use):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Creador de temas", "Ponle un nombre al tema.")
            return
        rt = self.rt
        try:
            mode = "light" if self.mode.currentIndex() == 1 else "dark"
            tid = self.cur or self._new_id(name)
            ac, ac2 = QColor(self.colors["accent"]), QColor(self.colors["accent2"])
            bg = dict(opacity=round(self.op.value() / 100.0, 2), fit=self.fit.currentData(),
                      style=self.style.currentData(), glow=[ac.name(), ac2.name()])
            if bg["style"] == "stars":
                bg["star_color"] = _lt(ac2, 0.6).name()
            elif bg["style"] == "grid":
                bg.update(grid_color=ac.name(), grid_size=48, grid_alpha=40)
            spec = dict(id=tid, name=name, base=mode, mode=mode, radius_scale=round(self.rad.value() / 100.0, 2),
                        colors=make_palette(self.colors, mode), background=bg)
            first = None
            for root in rt.theme_roots():
                folder = os.path.join(root, tid)
                os.makedirs(folder, exist_ok=True)
                b = dict(bg)
                dest = None
                if self.media:
                    dest = os.path.join(folder, "fondo" + os.path.splitext(self.media)[1].lower())
                if not dest or os.path.abspath(dest) != os.path.abspath(self.media):
                    for f in os.listdir(folder):
                        if f.lower().startswith("fondo."):
                            os.remove(os.path.join(folder, f))
                    if dest:
                        shutil.copy2(self.media, dest)
                if dest:
                    b["file"] = os.path.basename(dest)
                sp = dict(spec, background=b)
                txt = ("# -*- coding: utf-8 -*-\n\"\"\"Tema creado con el Creador de temas de MyTE.\"\"\"\nTHEME = "
                       + pprint.pformat(sp, width=100, sort_dicts=False) + "\n")
                with open(os.path.join(folder, "tema.py"), "w", encoding="utf-8") as f:
                    f.write(txt)
                if first is None:
                    first = (folder, sp)
            folder, sp = first
            rt.treset(tid)
            tid = rt.register_theme(tid, sp, folder)
            rt.manifest_theme(tid, name, folder, bool(self.media and self.media.lower().endswith(VIDEO_EXT)))
            if self.media and self.media.lower().endswith(VIDEO_EXT) and importlib.util.find_spec("PySide6.QtMultimedia") is None:
                rt.need_multimedia()
            rt._pending = tid
            rt._refresh_now()
            if rt.win:
                guard(rt.win.apply_theme)
            if use:
                rt.set_theme(tid)
            self.cur = tid
            self.fill_pick(tid)
            self.btn_del.setVisible(True)
            if rt.win:
                guard(rt.win.toast, "Tema «%s» guardado%s" % (name, " y aplicado" if use else ""), False)
        except Exception:
            log("Error guardando el tema:\n" + traceback.format_exc())
            QMessageBox.critical(self, "Creador de temas", "No se pudo guardar el tema (mira ext/runtime.log).")

    def delete(self):
        tid = self.cur
        if not tid:
            return
        label = self.rt.g["THEMES"][tid].get("label", tid)
        if QMessageBox.question(self, "Borrar tema", "¿Borrar el tema «%s»?" % label) != QMessageBox.Yes:
            return
        rt = self.rt
        if rt.ctl and rt.ctl.theme_name == tid:
            rt.set_theme("dark")
        for root in rt.theme_roots():
            shutil.rmtree(os.path.join(root, tid), ignore_errors=True)
        rt.manifest_theme(tid, "", "", False, remove=True)
        rt.backdrops.pop(tid, None)
        rt.g["THEMES"].pop(tid, None)
        rt.treset(tid)
        if rt.win:
            guard(rt.win.apply_theme)
        self.fill_pick(None)
        self.on_pick()


_RT = None


def THEMES_REF():
    return _RT.g["THEMES"]


# ==================================================================== el runtime
class Runtime:
    def __init__(self, g):
        global _RT
        _RT = self
        self.g = g
        self.qs = QSettings("MyTE", "MyTEClient")
        self.exts, self.listeners, self.extra_qss, self.css_fns = [], {}, [], []
        self.backdrops, self.win, self.ctl, self.warned = {}, None, None, set()
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._refresh_now)
        self._pending = None

    # -- eventos
    def emit(self, event, *a):
        for fn in list(self.listeners.get(event, [])):
            guard(fn, *a)

    # -- ajustes de temas (QSettings)
    def tget(self, tid, key):
        v = self.qs.value("theme/%s/%s" % (tid, key), None)
        return None if v in (None, "") else v

    def tset(self, tid, key, value):
        k = "theme/%s/%s" % (tid, key)
        if value is None:
            self.qs.remove(k)
        else:
            self.qs.setValue(k, value)

    def treset(self, tid):
        self.qs.remove("theme/%s" % tid)

    # -- temas
    def custom_themes(self):
        T = self.g["THEMES"]
        return sorted(((k, v) for k, v in T.items() if v.get("_custom")), key=lambda kv: kv[1].get("label", kv[0]).lower())

    def build_theme(self, tid, spec, folder):
        T = self.g["THEMES"]
        base = T.get(spec.get("base", "dark"))
        if base is None or base.get("_custom"):
            base = T["dark"]
        t = dict(base)
        t.update(spec.get("colors") or {})
        for key, _ in COLOR_KEYS:
            ov = self.tget(tid, "color/" + key)
            if ov:
                t[key] = ov
        t["name"] = "light" if spec.get("mode", base["name"]) == "light" else "dark"
        t.update(_custom=True, _id=tid, _spec=spec, _dir=folder, label=spec.get("name", tid))
        return t

    def register_theme(self, tid, spec, folder):
        tid = re.sub(r"[^\w\-]", "_", str(tid))
        if tid in ("light", "dark"):
            tid = "tema_" + tid
        self.g["THEMES"][tid] = self.build_theme(tid, spec, folder)
        self.backdrops.pop(tid, None)
        return tid

    def bg_cfg(self, tid):
        t = self.g["THEMES"].get(tid) or {}
        spec, folder = t.get("_spec") or {}, t.get("_dir")
        b = dict(spec.get("background") or {})
        c = dict(style=b.get("style", ""), fit=b.get("fit", "cover"), glow=b.get("glow") or [],
                 star_color=b.get("star_color"), grid_color=b.get("grid_color"), grid_size=b.get("grid_size"),
                 grid_alpha=b.get("grid_alpha"))
        try:
            c["opacity"] = float(b.get("opacity", 0.6))
        except (TypeError, ValueError):
            c["opacity"] = 0.6
        ov = self.tget(tid, "opacity")
        if ov is not None:
            c["opacity"] = coerce(ov, 0.6)
        chosen = self.tget(tid, "file")
        f = None
        if chosen != "-":
            f = chosen or b.get("file")
            if f and not os.path.isabs(f) and folder:
                f = os.path.join(folder, f)
            if not (f and os.path.isfile(f)) and folder and not chosen:
                f = self._auto_media(folder)
        c["file"] = f
        return c

    @staticmethod
    def _auto_media(folder):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return None
        for group in (VIDEO_EXT, ANIM_EXT, IMAGE_EXT + (".webp",)):
            for n in names:
                if n.lower().endswith(group) and not n.startswith("_"):
                    return os.path.join(folder, n)
        return None

    def backdrop(self, tid):
        bd = self.backdrops.get(tid)
        if bd is None:
            bd = self.backdrops[tid] = Backdrop(self, tid)
        return bd

    def activate(self, tid):
        t = self.g["THEMES"].get(tid) or {}
        for k, bd in list(self.backdrops.items()):
            bd.set_active(k == tid and bool(t.get("_custom")))
        if t.get("_custom"):
            self.backdrop(tid).set_active(True)

    def paint_decor(self, p, rect, t):
        bd = self.backdrop(t["_id"])
        if not bd.active:
            self.activate(t["_id"])
        bd.paint(p, rect)

    def repaint_bg(self):
        for w in (getattr(self.win, "bg", None), getattr(self.ctl, "login", None)):
            if w is not None:
                try:
                    if w.isVisible():
                        w.update()
                except RuntimeError:
                    pass

    def need_multimedia(self):
        if "mm" in self.warned:
            return
        self.warned.add("mm")
        log("Falta PySide6-Addons (QtMultimedia): no se puede reproducir el vídeo de fondo.")
        if self.win:
            guard(self.win.toast, "El fondo de vídeo necesita PySide6-Addons. Abre MyTE Injector y activa el soporte de vídeo.", True)

    def set_theme(self, tid):
        ctl = self.ctl
        if not ctl or tid not in self.g["THEMES"]:
            return
        ctl.theme_name = tid
        ctl.settings.setValue("theme", tid)
        ctl.apply_theme()
        if ctl.main:
            ctl.main.retheme()

    def theme_refresh(self, tid, light=False):
        """Recalcula un tema tras cambiar un ajuste. light=True: solo repinta el fondo (deslizador)."""
        t = self.g["THEMES"].get(tid)
        if not t:
            return
        if light:
            bd = self.backdrops.get(tid)
            if bd:
                bd.refresh_cfg()
            self.repaint_bg()
            return
        self._pending = tid
        self.timer.start(120)

    def _refresh_now(self):
        tid = self._pending
        t = self.g["THEMES"].get(tid)
        if not t:
            return
        self.g["THEMES"][tid] = self.build_theme(tid, t["_spec"], t["_dir"])
        bd = self.backdrops.get(tid)
        if bd:
            bd.reload()
        if self.ctl and self.ctl.theme_name == tid:
            self.ctl.apply_theme()
            if self.ctl.main:
                self.ctl.main.retheme()
        self.repaint_bg()

    # -- menús y botones
    def theme_menu(self, ctl):
        win = ctl.main
        T = self.g["THEMES"]
        m = QMenu(win)
        order = [k for k in ("light", "dark") if k in T] + [k for k, _ in self.custom_themes()]
        for k in order:
            label = BUILTIN_LABEL.get(k) or ("🎨  " + T[k].get("label", k))
            a = m.addAction(("✔  " if k == ctl.theme_name else "      ") + label.strip())
            a.triggered.connect(lambda _=False, k=k: self.set_theme(k))
        m.addSeparator()
        m.addAction("🖌️  Creador de temas…", lambda: self.open_theme_maker(win))
        m.addAction("🧩  Ajustes de extensiones…", lambda: self.open_settings(win))
        b = win.theme_btn
        m.exec(b.mapToGlobal(QPoint(b.width() + 4, 0)))

    def open_settings(self, parent=None):
        d = ExtSettings(self, parent or self.win)
        d.exec()

    def open_theme_maker(self, parent=None):
        d = ThemeMaker(self, parent or self.win)
        d.exec()

    def theme_roots(self):
        """Dónde se guardan los temas creados: dentro de MyTE y, si se conoce, en tu carpeta de extensiones."""
        roots = [os.path.join(EXT_DIR, "temas")]
        try:
            with open(os.path.join(EXT_DIR, "manifest.json"), encoding="utf-8") as f:
                src = json.load(f).get("source")
            if src and os.path.isdir(src):
                r = os.path.join(src, "temas")
                if os.path.abspath(r) != os.path.abspath(roots[0]):
                    roots.append(r)
        except (OSError, ValueError):
            pass
        return roots

    def manifest_theme(self, tid, name, folder, video, remove=False):
        """Anota el tema en manifest.json para que el injector lo vea como ya inyectado."""
        mp = os.path.join(EXT_DIR, "manifest.json")
        try:
            with open(mp, encoding="utf-8") as f:
                mf = json.load(f)
        except (OSError, ValueError):
            return
        items = [e for e in mf.get("items", []) if not (e.get("kind") == "theme" and e.get("id") == tid)]
        if not remove:
            items.append(dict(kind="theme", id=tid, name=name, version="", sha=_sha_dir(folder),
                              requires=["PySide6-Addons"] if video else []))
        mf["items"] = items
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(mf, f, indent=1, ensure_ascii=False)

    def _side_button(self, win, text, tip, fn, ref):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        if ref is not None:
            try:
                if ref.objectName():
                    b.setObjectName(ref.objectName())
                if ref.styleSheet():
                    b.setStyleSheet(ref.styleSheet())
                b.setFont(ref.font())
                if ref.minimumWidth() > 0 and ref.minimumWidth() == ref.maximumWidth():
                    b.setFixedSize(ref.minimumSize())
            except Exception:
                pass
        b.clicked.connect(fn)
        return b

    def _decorate_window(self, win):
        """Añade 🖌️ (creador de temas) y 🧩 (ajustes) en la barra lateral, encima de la campanita."""
        ref = getattr(win, "bell", None) or getattr(win, "theme_btn", None)
        mk = self._side_button(win, "🖌️", "Creador de temas", lambda: self.open_theme_maker(win), ref)
        ex = self._side_button(win, "🧩", "Ajustes de extensiones", lambda: self.open_settings(win), ref)
        lay = None
        for cand in (getattr(win, "bell", None), getattr(win, "theme_btn", None)):
            pw = cand.parentWidget() if cand is not None else None
            lay = pw.layout() if pw is not None else None
            if lay is not None and hasattr(lay, "insertWidget") and lay.indexOf(cand) >= 0:
                at = lay.indexOf(cand)
                lay.insertWidget(at, mk, 0, Qt.AlignHCenter)
                lay.insertWidget(at + 1, ex, 0, Qt.AlignHCenter)
                log("Botones 🖌️ y 🧩 añadidos junto a %s." % cand.objectName())
                break
            lay = None
        if lay is None:                                   # plan B: botones flotantes arriba a la derecha
            log("No encuentro la barra lateral: pongo los botones flotando en la ventana.")
            for i, b in enumerate((mk, ex)):
                b.setParent(win)
                b.move(8 + 34 * i, 8)
                b.show()
                b.raise_()
        win.theme_maker_btn, win.ext_btn = mk, ex

    # -- parcheo del cliente
    def patch_core(self):
        g, rt = self.g, self

        def w_ctl_init(orig, ctl, *a, **k):
            rt.ctl = ctl
            orig(ctl, *a, **k)
        patch(g["AppCtl"], "__init__", w_ctl_init)

        def w_main_init(orig, win, ctl, *a, **k):
            rt.win, rt.ctl = win, ctl
            orig(win, ctl, *a, **k)
            guard(rt._decorate_window, win)
            guard(rt.activate, ctl.theme_name)
            rt.emit("main_window", win)
        patch(g["MainWindow"], "__init__", w_main_init)

        def w_view_init(orig, view, *a, **k):
            orig(view, *a, **k)
            rt.emit("chat_view", view)
        patch(g["ChatView"], "__init__", w_view_init)

        def w_row_init(orig, row, *a, **k):
            orig(row, *a, **k)
            rt.emit("message_row", row)
        patch(g["MessageRow"], "__init__", w_row_init)

        def w_on_msg(orig, win, m):
            orig(win, m)
            rt.emit("msg", win, m)
        patch(g["MainWindow"], "on_msg", w_on_msg)

        def w_refresh_list(orig, win, *a, **k):
            r = orig(win, *a, **k)
            rt.emit("refresh_list", win)
            return r
        patch(g["MainWindow"], "refresh_list", w_refresh_list)

        # -- temas: menú de temas, botón, fondo y estilos
        def w_toggle(orig, ctl):
            if len(g["THEMES"]) > 2 and ctl.main:
                rt.theme_menu(ctl)
            else:
                orig(ctl)
        patch(g["AppCtl"], "toggle_theme", w_toggle)

        def w_apply_theme(orig, win):
            orig(win)
            T = g["THEMES"]
            win.fab.plus = QColor("#123008" if win.theme()["name"] == "light" else "#ffffff")
            win.fab.update()
            if len(T) > 2:
                win.theme_btn.setText("🎨")
                cur = T[win.ctl.theme_name]
                win.theme_btn.setToolTip("Cambiar tema (ahora: %s)" % (cur.get("label") or win.ctl.theme_name))
            rt.emit("apply_theme", win)
        patch(g["MainWindow"], "apply_theme", w_apply_theme)

        def w_profile_menu(orig, win):
            orig(win)
            if len(g["THEMES"]) > 2:
                for a in win.avatar_btn.menu().actions():
                    if a.text().strip().endswith(("Modo claro", "Modo oscuro")) or "Modo " in a.text():
                        a.setText("🎨  Cambiar tema…")
        patch(g["MainWindow"], "build_profile_menu", w_profile_menu)

        def w_set_theme(orig, bg, name):
            orig(bg, name)
            rt.activate(name)
        patch(g["AeroBackground"], "set_theme", w_set_theme)

        def w_bg_paint(orig, bg, e):
            t = g["THEMES"].get(bg.theme)
            if not t or not t.get("_custom"):
                return orig(bg, e)
            p = QPainter(bg)
            p.setRenderHint(QPainter.Antialiasing)
            gr = QLinearGradient(0, 0, bg.width() * 0.6, bg.height())
            gr.setColorAt(0, QColor(t["bg1"]))
            gr.setColorAt(0.55, QColor(t["bg2"]))
            gr.setColorAt(1, QColor(t["bg3"]))
            p.fillRect(bg.rect(), gr)
            guard(rt.paint_decor, p, bg.rect(), t)
            p.end()
        patch(g["AeroBackground"], "paintEvent", w_bg_paint)

        def w_login_paint(orig, lw, e):
            orig(lw, e)
            t = g["THEMES"].get(lw.ctl.theme_name)
            if t and t.get("_custom"):
                p = QPainter(lw)
                guard(rt.paint_decor, p, lw.rect(), t)
                p.end()
        patch(g["LoginWindow"], "paintEvent", w_login_paint)

        orig_qss = g["build_qss"]

        def build_qss2(t):
            s = orig_qss(t)
            k = t.get("_spec", {}).get("radius_scale") if t.get("_custom") else None
            if k is not None:
                s = re.sub(r"(border(?:-(?:top|bottom)-(?:left|right))?-radius:\s*)(\d+)px",
                           lambda m: "%s%dpx" % (m.group(1), max(0, round(int(m.group(2)) * float(k)))), s)
            if t.get("_custom") and t["_spec"].get("qss"):
                s += "\n" + t["_spec"]["qss"]
            if self.extra_qss:
                s += "\n" + "\n".join(self.extra_qss)
            for fn in self.css_fns:
                try:
                    s += "\n" + fn(t)
                except Exception:
                    log("Error en api.css:\n" + traceback.format_exc())
            return s
        g["build_qss"] = build_qss2

    # -- carga
    def load_all(self):
        T = self.g["THEMES"]
        tdir = os.path.join(EXT_DIR, "temas")
        if os.path.isdir(tdir):
            for name in sorted(os.listdir(tdir)):
                folder = os.path.join(tdir, name)
                if name.startswith("_") or not os.path.isdir(folder):
                    continue
                guard(self.load_theme, name, folder)
        for name in sorted(os.listdir(EXT_DIR)):
            if name.endswith(".py") and not name.startswith("_"):
                guard(self.load_ext, os.path.join(EXT_DIR, name))
        if self.qs.value("theme", "light") not in T:              # el tema guardado ya no está instalado
            self.qs.setValue("theme", "light")

    def theme_file(self, folder):
        for cand in ("tema.py", "theme.py"):
            if os.path.isfile(os.path.join(folder, cand)):
                return os.path.join(folder, cand)
        py = sorted(n for n in os.listdir(folder) if n.endswith(".py") and not n.startswith("_"))
        return os.path.join(folder, py[0]) if py else None

    def load_theme(self, name, folder):
        path = self.theme_file(folder)
        if not path:
            return
        mod = load_module("myte_theme_" + re.sub(r"\W", "_", name), path)
        spec = getattr(mod, "THEME", None)
        if not isinstance(spec, dict):
            log("El tema %s no define THEME." % name)
            return
        self.register_theme(spec.get("id") or name, spec, folder)

    def load_ext(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        mod = load_module("myte_ext_" + stem, path)
        meta = dict(getattr(mod, "META", None) or {})
        meta.setdefault("id", stem)
        meta.setdefault("name", stem)
        api = ExtAPI(self, meta, path, mod)
        self.exts.append(api)
        setup = getattr(mod, "setup", None)
        if callable(setup):
            setup(api)


def boot(g):
    """Punto de entrada: lo llama el bloque que MyTE Injector añade al final de myte_client.py."""
    try:
        rt = Runtime(g)
        g["MYTE_EXT"] = rt
        rt.patch_core()
        rt.load_all()
    except Exception:
        log("Error arrancando el runtime:\n" + traceback.format_exc())
'''

LEEME = """\
MyTE INJECTOR - carpeta de extensiones
======================================

  extensiones\\
  |-- mi_extension.py            <- cada .py de aqui es una extension
  `-- temas\\
      `-- mi_tema\\               <- un tema = una carpeta
          |-- tema.py            <- define THEME = {...}   (o usa el boton 🖌️ dentro de MyTE: lo crea por ti)
          `-- fondo.png          <- imagen, GIF o video de fondo (opcional, el que quieras)

Las carpetas y archivos que empiezan por "_" se ignoran.
Abre MyTE Injector, marca lo que quieras y pulsa "Aplicar cambios". Reinicia MyTE para verlo.

----------------------------------------------------------------------------------------------------
EXTENSION (.py)
----------------------------------------------------------------------------------------------------
META = {
    "id": "mi_extension",                 # opcional (por defecto, el nombre del archivo)
    "name": "Mi extension",
    "description": "Que hace.",
    "version": "1.0",
    "author": "Yo",
    "requires": ["PySide6-Addons"],       # paquetes pip que el injector instalara si faltan
    "settings": [                          # ajustes que salen solos en la ventana de ajustes (icono 🧩)
        {"key": "activo", "type": "bool", "label": "Activar", "default": True},
        {"key": "opacidad", "type": "slider", "label": "Opacidad", "default": 0.5},
        {"key": "color", "type": "color", "label": "Color", "default": "#ff8800"},
        {"key": "n", "type": "int", "label": "Numero", "default": 3, "min": 0, "max": 10},
        {"key": "modo", "type": "choice", "label": "Modo", "choices": ["a", "b"], "default": "a"},
        {"key": "img", "type": "file", "label": "Imagen", "filter": "Imagenes (*.png *.jpg)"},
        {"key": "txt", "type": "text", "label": "Texto", "default": ""},
    ],
}

def setup(api):
    # api.get("clave") / api.set("clave", valor)   -> ajustes guardados
    # api.win, api.ctl                              -> ventana principal y controlador (cuando existen)
    # api.g                                         -> globales del cliente (MainWindow, ChatView, THEMES...)
    # api.on("main_window" | "chat_view" | "message_row" | "msg" | "refresh_list" | "apply_theme", fn)
    # api.patch / api.wrap("MainWindow", "metodo", lambda original, self, *a, **k: original(self, *a, **k))
    # api.after("MainWindow", "metodo", lambda self, resultado, *a, **k: ...)   -> tras el original
    # api.css(lambda t: "QLabel#x { color: %(accent)s; }" % t)                 -> QSS segun el tema
    # api.register_theme(id, spec) / api.add_qss(css) / api.toast(texto) / api.on_change(fn)
    pass

def settings_panel(api, parent):      # opcional: tu propio panel de ajustes (devuelve un QWidget)
    return None

----------------------------------------------------------------------------------------------------
TEMA (temas/<nombre>/tema.py)
----------------------------------------------------------------------------------------------------
THEME = {
    "name": "Mi tema",
    "base": "dark",                        # "dark" o "light": de donde hereda los colores que no pongas
    "colors": {"accent": "#ff8a1f", "bg1": "#0b0500", "bg2": "#1a0d02", "bg3": "#000000"},
    "radius_scale": 1.0,                   # 0 = bordes cuadrados, 1.5 = mas redondeados
    "qss": "",                             # estilos extra (Qt stylesheet)
    "background": {
        "file": "fondo.png",               # opcional: si no lo pones se usa la primera imagen/GIF/video de la carpeta
        "opacity": 0.6,                    # 0 a 1 (tambien se cambia desde los ajustes)
        "fit": "cover",                    # cover | contain | stretch (se adapta al tamano de la ventana)
        "style": "stars",                  # "", "stars" o "grid" (decoracion dibujada bajo la imagen)
        "glow": ["#ff7a1a", "#ffb300"],    # resplandores de color
    },
}
Un video necesita PySide6-Addons: el injector lo instala solo si hay un video en la carpeta del tema
(o marcando "soporte de video").
"""

BLOCK_LINES = [
    MARK_BEGIN + "  (lo gestiona MyTE Injector; para quitarlo usa el injector)",
    "try:",
    "    import os as _mi_os, importlib.util as _mi_u",
    "    _mi_dir = _mi_os.path.join(_mi_os.path.dirname(_mi_os.path.abspath(__file__)), \"ext\")",
    "    _mi_p = _mi_os.path.join(_mi_dir, \"" + RUNTIME_FILE + "\")",
    "    if _mi_os.path.isfile(_mi_p):",
    "        _mi_s = _mi_u.spec_from_file_location(\"myte_runtime\", _mi_p)",
    "        _mi_m = _mi_u.module_from_spec(_mi_s)",
    "        _mi_s.loader.exec_module(_mi_m)",
    "        _mi_m.boot(globals())",
    "except Exception:",
    "    try:",
    "        import traceback as _mi_t",
    "        open(_mi_os.path.join(_mi_dir, \"runtime.log\"), \"a\", encoding=\"utf-8\").write(_mi_t.format_exc())",
    "    except Exception:",
    "        pass",
    MARK_END,
]


# ============================================================ utilidades
def resource(name):
    """Busca un archivo junto al .exe (versión más nueva) o dentro del paquete."""
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    cands = [os.path.join(here, name)]
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, name))
    cands.append(os.path.join(os.getcwd(), name))
    return next((c for c in cands if os.path.isfile(c)), None)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
    except OSError:
        pass


def resolve_client(text):
    """Acepta la ruta de myte_client.py o de una carpeta que lo contenga (o tenga 'app')."""
    p = os.path.normpath((text or "").strip().strip('"'))
    if not p or p == ".":
        return None
    if os.path.isfile(p):
        return p
    for c in (os.path.join(p, "myte_client.py"), os.path.join(p, "app", "myte_client.py")):
        if os.path.isfile(c):
            return c
    return None


def detect_client():
    cfg = load_config()
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for c in (cfg.get("client"), os.path.join(DEFAULT_BASE, "app", "myte_client.py"),
              os.path.join(here, "myte_client.py"), os.path.join(here, "app", "myte_client.py")):
        if c and os.path.isfile(c):
            return os.path.normpath(c)
    return None


def default_ext_folder(client):
    cfg = load_config()
    if cfg.get("ext"):
        return cfg["ext"]
    if client:
        app_dir = os.path.dirname(client)
        st = read_state(client)
        if st.get("source"):
            return st["source"]
        base = os.path.dirname(app_dir) if os.path.basename(app_dir).lower() == "app" else app_dir
        return os.path.join(base, "extensiones")
    return os.path.join(DEFAULT_BASE, "extensiones")


def find_python(app_dir):
    """Python privado que creó el instalador de MyTE (<base>/py)."""
    base = os.path.dirname(app_dir)
    cands = [os.environ.get("MYTE_PY") or "",
             os.path.join(base, "py", "python.exe"), os.path.join(app_dir, "py", "python.exe"),
             os.path.join(base, "py", "bin", "python3"), os.path.join(base, "py", "python")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def find_pythonw(app_dir):
    base = os.path.dirname(app_dir)
    return next((c for c in (os.path.join(base, "py", "pythonw.exe"), os.path.join(app_dir, "py", "pythonw.exe"))
                 if os.path.isfile(c)), None)


# ============================================================ carpeta de extensiones
def ensure_folders(folder):
    os.makedirs(os.path.join(folder, "temas"), exist_ok=True)
    readme = os.path.join(folder, "LEEME.txt")
    if not os.path.exists(readme):
        try:
            write_text(readme, LEEME)
        except OSError:
            pass


def parse_py(path):
    """Lee META / THEME / setup sin ejecutar nada (ast)."""
    out = {"META": None, "THEME": None, "has_setup": False, "has_theme": False, "error": None}
    try:
        tree = ast.parse(read_text(path).lstrip("\ufeff"))
    except (SyntaxError, ValueError, OSError) as e:
        out["error"] = "Error de sintaxis: %s" % e
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("META", "THEME"):
                    if t.id == "THEME":
                        out["has_theme"] = True
                    try:
                        out[t.id] = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        pass
        elif isinstance(node, ast.FunctionDef) and node.name == "setup":
            out["has_setup"] = True
    return out


def pkg_list(v):
    if isinstance(v, str):
        v = [v]
    return [p.strip() for p in v if isinstance(p, str) and p.strip()] if isinstance(v, (list, tuple)) else []


def sha_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def sha_dir(folder):
    h = hashlib.sha1()
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(root, n)
            h.update(os.path.relpath(p, folder).encode("utf-8", "replace"))
            if n.lower().endswith(".py"):
                h.update(open(p, "rb").read())
            else:
                h.update(str(os.path.getsize(p)).encode())
    return h.hexdigest()


def scan_sources(folder):
    """Devuelve la lista de extensiones y temas que hay en la carpeta."""
    items = []
    try:
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return items
    for n in names:
        p = os.path.join(folder, n)
        if n.startswith("_") or not (os.path.isfile(p) and n.lower().endswith(".py")):
            continue
        info = parse_py(p)
        meta = info["META"] if isinstance(info["META"], dict) else {}
        it = dict(kind="ext", id=os.path.splitext(n)[0], path=p, name=meta.get("name") or os.path.splitext(n)[0],
                  desc=meta.get("description", ""), version=str(meta.get("version", "")), author=meta.get("author", ""),
                  requires=pkg_list(meta.get("requires")), valid=True, err="")
        if info["error"]:
            it.update(valid=False, err=info["error"])
        elif not (info["has_setup"] or info["META"] is not None):
            it.update(valid=False, err="No define setup(api) ni META: no parece una extensión.")
        it["sha"] = sha_file(p)
        items.append(it)
    tdir = os.path.join(folder, "temas")
    if os.path.isdir(tdir):
        for n in sorted(os.listdir(tdir), key=str.lower):
            d = os.path.join(tdir, n)
            if n.startswith("_") or not os.path.isdir(d):
                continue
            pyf = next((os.path.join(d, c) for c in ("tema.py", "theme.py") if os.path.isfile(os.path.join(d, c))), None)
            if not pyf:
                cand = sorted(x for x in os.listdir(d) if x.endswith(".py") and not x.startswith("_"))
                pyf = os.path.join(d, cand[0]) if cand else None
            it = dict(kind="theme", id=n, path=d, name=n, desc="", version="", author="", requires=[], valid=True, err="")
            if not pyf:
                it.update(valid=False, err="La carpeta no tiene ningún .py (falta tema.py).")
            else:
                info = parse_py(pyf)
                th = info["THEME"] if isinstance(info["THEME"], dict) else {}
                meta = info["META"] if isinstance(info["META"], dict) else {}
                it.update(name=th.get("name") or meta.get("name") or n, desc=th.get("description") or meta.get("description", ""),
                          version=str(meta.get("version", th.get("version", ""))), author=meta.get("author", th.get("author", "")),
                          requires=pkg_list(meta.get("requires")) + pkg_list(th.get("requires")))
                if info["error"]:
                    it.update(valid=False, err=info["error"])
                elif not info["has_theme"]:
                    it.update(valid=False, err="El .py no define THEME = {...}.")
                it["pyfile"] = pyf
            if any(f.lower().endswith(VIDEO_EXT) for f in os.listdir(d)):
                it["requires"] = it["requires"] + [VIDEO_PKG]
                it["video"] = True
            it["sha"] = sha_dir(d)
            items.append(it)
    return items


# ============================================================ estado del cliente
def ext_dir(client):
    return os.path.join(os.path.dirname(client), "ext")


def strip_block(text):
    return re.sub(re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END) + r"[ \t]*\r?\n(?:\r?\n)?", "", text, flags=re.S)


def has_block(text):
    return MARK_BEGIN in text and MARK_END in text


def add_block(text):
    nl = "\r\n" if "\r\n" in text else "\n"
    ms = list(re.finditer(r"^if __name__ == [\"']__main__[\"']:", text, flags=re.M))
    if not ms:
        raise ValueError("No encuentro el punto de entrada («if __name__ == \"__main__\"») del cliente.")
    i = ms[-1].start()
    return text[:i] + nl.join(BLOCK_LINES) + nl + nl + text[i:]


def read_state(client):
    """Qué hay inyectado ahora mismo en el cliente."""
    st = dict(loader=False, items={}, source=None, runtime=False, foreign=False)
    try:
        st["loader"] = has_block(read_text(client))
    except OSError:
        return st
    d = ext_dir(client)
    st["runtime"] = os.path.isfile(os.path.join(d, RUNTIME_FILE))
    try:
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            mf = json.load(f)
        st["source"] = mf.get("source")
        for e in mf.get("items", []):
            st["items"][(e["kind"], e["id"])] = e
    except (OSError, ValueError, KeyError):
        pass
    st["foreign"] = st["loader"] and not st["items"]
    return st


def pip_name(spec):
    return re.split(r"[=<>!~\[; ]", spec, maxsplit=1)[0]


def safe_pkg(spec):
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[\w,]+\])?([=<>!~]=?[\w.*]+(,[=<>!~]=?[\w.*]+)*)?$", spec))


# ============================================================ el motor
class Engine:
    def __init__(self, emit, client, source, keep, orphans, remove, video):
        self.emit, self.client, self.source = emit, client, source
        self.keep, self.orphans, self.remove, self.video = keep, orphans, remove, video
        self.app_dir = os.path.dirname(client)
        self.ext = ext_dir(client)

    def log(self, text, kind="info"):
        self.emit("log", text, kind)

    def run(self, cmd):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", creationflags=NO_WINDOW)
        for line in p.stdout:
            line = line.rstrip()
            if line:
                self.log(line[:200], "dim")
        return p.wait()

    def quiet(self, cmd):
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
        return r.returncode, r.stdout

    def go(self):
        step = 0
        try:
            step = 0
            self.emit("step", 0, "run")
            text = self.check()
            self.emit("step", 0, "ok")
            self.emit("prog", 10)

            step = 1
            self.emit("step", 1, "run")
            orig = strip_block(text)
            bak = self.client + ".orig"
            if os.path.exists(bak):
                self.log("Ya existe myte_client.py.orig: se conserva.", "dim")
                self.emit("step", 1, "skip")
            else:
                write_text(bak, orig)
                self.log("Guardado " + bak, "ok")
                self.emit("step", 1, "ok")
            self.emit("prog", 20)

            step = 2
            self.emit("step", 2, "run")
            state = self.deps()
            self.emit("step", 2, state)
            self.emit("prog", 70)

            step = 3
            self.emit("step", 3, "run")
            self.copy()
            self.emit("step", 3, "ok")
            self.emit("prog", 88)

            step = 4
            self.emit("step", 4, "run")
            total = len(self.keep) + len(self.orphans)
            if total:
                write_text(self.client, add_block(orig))
                self.log("Cargador activado en myte_client.py", "ok")
            else:
                write_text(self.client, orig)
                self.log("Cliente restaurado: vuelve a ser el original.", "ok")
            self.emit("step", 4, "ok")
            self.emit("prog", 100)
            n_ext = sum(1 for k in self.keep if k["kind"] == "ext") + sum(1 for k in self.orphans if k["kind"] == "ext")
            n_th = total - n_ext
            if total:
                self.emit("done", "Listo: %d extensión(es) y %d tema(s) activos. Reinicia MyTE para verlos." % (n_ext, n_th))
            else:
                self.emit("done", "Listo: MyTE vuelve a ser el original, sin extensiones.")
        except Exception as e:
            self.emit("step", step, "err")
            self.emit("fail", str(e) or e.__class__.__name__)

    # -- paso 0
    def check(self):
        if not os.path.isfile(self.client):
            raise RuntimeError("No existe " + self.client)
        text = read_text(self.client)
        missing = [s for s in REQUIRED_SYMBOLS if s not in text]
        if missing:
            raise RuntimeError("Este myte_client.py no es compatible (le falta: %s). ¿Es una versión distinta de MyTE?"
                               % ", ".join(missing[:4]))
        try:
            with open(self.client, "r+b"):
                pass
        except OSError:
            raise RuntimeError("No puedo escribir en " + self.client + " (¿está en una carpeta protegida o solo lectura?)")
        for it in self.keep:
            paths = [it["path"]] if it["kind"] == "ext" else [it.get("pyfile")]
            for p in paths:
                try:
                    compile(read_text(p).lstrip("\ufeff"), p, "exec")
                except SyntaxError as e:
                    raise RuntimeError("«%s» tiene un error de sintaxis (línea %s): %s" % (os.path.basename(p), e.lineno, e.msg))
        self.log("Cliente compatible. %d elemento(s) por activar, %d por quitar." % (len(self.keep), len(self.remove)), "ok")
        return text

    # -- paso 2
    def deps(self):
        wanted = []
        for it in self.keep + self.orphans:
            wanted += it.get("requires", [])
        if self.video:
            wanted.append(VIDEO_PKG)
        wanted = list(dict.fromkeys(w for w in wanted if w))
        if not wanted:
            self.log("Nada extra que instalar.", "dim")
            return "skip"
        py = find_python(self.app_dir)
        if not py:
            self.log("No encuentro el Python de MyTE (carpeta py): instala a mano → " + ", ".join(wanted), "warn")
            return "ok"
        code, out = self.quiet([py, "-m", "pip", "show", "PySide6-Essentials"])
        ver = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.lower().startswith("version:")), None)
        for spec in wanted:
            if not safe_pkg(spec):
                self.log("Paquete ignorado (nombre no válido): " + spec, "warn")
                continue
            name = pip_name(spec)
            if self.quiet([py, "-m", "pip", "show", name])[0] == 0:
                self.log("%s ya está instalado." % name, "dim")
                continue
            if name.lower() == VIDEO_PKG.lower() and ver and spec == name:
                spec = "%s==%s" % (name, ver)                   # mismo número de versión que PySide6-Essentials
            self.log("Instalando %s (puede tardar un poco)…" % spec, "info")
            if self.run([py, "-m", "pip", "install", "--disable-pip-version-check", spec]) != 0:
                raise RuntimeError("No se pudo instalar «%s». Comprueba la conexión a Internet." % spec)
            self.log("%s instalado." % name, "ok")
        return "ok"

    # -- paso 3
    def copy(self):
        if not self.keep and not self.orphans:
            if os.path.isdir(self.ext):
                shutil.rmtree(self.ext, ignore_errors=True)
            self.log("Carpeta ext eliminada.", "dim")
            return
        os.makedirs(os.path.join(self.ext, "temas"), exist_ok=True)
        write_text(os.path.join(self.ext, RUNTIME_FILE), RUNTIME_SRC)
        for e in self.remove:
            self.drop(e["kind"], e["id"])
            self.log("Quitado: " + e.get("name", e["id"]), "ok")
        entries = [dict(kind=o["kind"], id=o["id"], name=o.get("name", o["id"]), version=o.get("version", ""),
                        sha=o.get("sha", ""), requires=o.get("requires", [])) for o in self.orphans]
        for it in self.keep:
            dest = self.dest(it["kind"], it["id"])
            old = self.manifest_entry(it)
            if old and old.get("sha") == it["sha"] and os.path.exists(dest):
                self.log("Sin cambios: " + it["name"], "dim")
            else:
                self.drop(it["kind"], it["id"])
                if it["kind"] == "ext":
                    shutil.copy2(it["path"], dest)
                else:
                    shutil.copytree(it["path"], dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                self.log(("Actualizado: " if old else "Inyectado: ") + it["name"], "ok")
            entries.append(dict(kind=it["kind"], id=it["id"], name=it["name"], version=it["version"], sha=it["sha"],
                                requires=it["requires"]))
        mf = dict(runtime=1, source=self.source, items=entries)
        write_text(os.path.join(self.ext, "manifest.json"), json.dumps(mf, indent=1, ensure_ascii=False))

    def manifest_entry(self, it):
        try:
            with open(os.path.join(self.ext, "manifest.json"), encoding="utf-8") as f:
                for e in json.load(f).get("items", []):
                    if e["kind"] == it["kind"] and e["id"] == it["id"]:
                        return e
        except (OSError, ValueError, KeyError):
            pass
        return None

    def dest(self, kind, id_):
        return os.path.join(self.ext, id_ + ".py") if kind == "ext" else os.path.join(self.ext, "temas", id_)

    def drop(self, kind, id_):
        p = self.dest(kind, id_)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)


# ============================================================ interfaz (space era)
C = dict(bg="#04061a", card="#0b1035", card2="#10174a", edge="#2b3a8c", text="#e6eeff", sub="#9db4dd",
         cyan="#3fd9ff", cyan_d="#1790b8", purple="#8a5cf0", purple_d="#5b34b8", ok="#4ade80", err="#ff6b7a",
         warn="#ffc266", dim="#7085b5", field="#070b2a")
FONT = "Segoe UI"


def dark_titlebar(win):
    """Barra de título oscura en Windows 10/11 (si no se puede, se queda como está)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


class Check(tk.Canvas):
    """Palomita propia (los checkbuttons nativos de Windows no se pueden teñir de oscuro)."""

    def __init__(self, parent, var, bg, command=None, enabled=True, size=22):
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2" if enabled else "arrow")
        self.var, self.cmd, self.enabled, self.size = var, command, enabled, size
        self.bind("<Button-1>", self.toggle)
        self._tr = var.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", lambda e: var.trace_remove("write", self._tr) if e.widget is self else None)
        self.draw()

    def toggle(self, _e=None):
        if not self.enabled:
            return
        self.var.set(not self.var.get())
        if self.cmd:
            self.cmd()

    def draw(self):
        s = self.size
        self.delete("all")
        on = self.var.get()
        edge = C["edge"] if not self.enabled else (C["cyan"] if on else C["dim"])
        self.create_rectangle(2, 2, s - 2, s - 2, outline=edge, width=2, fill=C["purple"] if on and self.enabled else C["field"])
        if on:
            self.create_line(s * 0.27, s * 0.52, s * 0.44, s * 0.70, s * 0.74, s * 0.30, fill="white" if self.enabled else C["dim"],
                             width=3, capstyle="round", joinstyle="round")


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="M.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.wrap = []
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _resize(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)
        for l in self.wrap:
            try:
                l.configure(wraplength=max(120, e.width - 96))
            except tk.TclError:
                pass

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))

    def _unbind_wheel(self, _e):
        for s in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(s)

    def _wheel(self, e):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.wrap = []
        self.canvas.yview_moveto(0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MyTE — Injector")
        self.geometry("680x%d" % min(820, max(720, self.winfo_screenheight() - 80)))
        self.minsize(680, 720)
        self.resizable(False, True)
        self.configure(bg=C["bg"])
        ico = resource("myte.ico")
        if ico and IS_WIN:
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass
        self.q = queue.Queue()
        self.busy = False
        self.phase = "list"                   # list | run | after
        self.rows = []
        self.client = None
        self.state = dict(loader=False, items={}, source=None, runtime=False, foreign=False)
        self._build()
        self.eval("tk::PlaceWindow . center")
        self.after(80, lambda: dark_titlebar(self))
        self.after(60, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(150, self._first_scan)

    # ------------------------------------------------------------ construcción
    def _build(self):
        h = tk.Canvas(self, width=680, height=96, highlightthickness=0, bd=0, bg=C["bg"])
        h.pack(fill="x")
        top, bot = (0x1d, 0x12, 0x55), (0x04, 0x06, 0x1a)
        for y in range(96):
            t = y / 95
            h.create_line(0, y, 680, y, fill="#%02x%02x%02x" % tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
        rnd = random.Random(7)
        for _ in range(70):
            x, y, r = rnd.randint(0, 680), rnd.randint(0, 94), rnd.choice((0, 0, 1))
            col = rnd.choice(("#ffffff", "#9db4dd", "#6f84b3", "#3fd9ff"))
            h.create_oval(x - r, y - r, x + r + 1, y + r + 1, fill=col, outline="")
        for x, y, r, col in ((610, 34, 34, "#3a4aa0"), (650, 74, 18, "#5b34b8"), (565, 80, 12, "#1790b8")):
            h.create_oval(x - r, y - r, x + r, y + r, outline=col, width=2)
        h.create_oval(601, 25, 619, 43, fill="#8a5cf0", outline="")
        f = tkfont.Font(family=FONT, size=30, weight="bold")
        h.create_text(26, 40, text="MyTE", anchor="w", font=(FONT, 30, "bold"), fill=C["cyan"])
        h.create_text(26 + f.measure("MyTE") + 12, 48, text="INJECTOR", anchor="w", font=(FONT, 13, "bold"), fill=C["purple"])
        h.create_text(28, 78, text="Injector · extensiones y temas para tu cliente", anchor="w", font=(FONT, 10), fill=C["sub"])

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=22, pady=(10, 14))
        self.body = body
        tk.Label(body, bg=C["bg"], fg=C["text"], font=(FONT, 10), justify="left", wraplength=630, anchor="w",
                 text="Añade extensiones (.py) y temas a tu MyTE sin tocar el código. Marca lo que quieras y pulsa "
                      "«Aplicar cambios»: lo desmarcado se quita y el original se guarda siempre.").pack(fill="x")

        self.v_client, self.v_ext = tk.StringVar(), tk.StringVar()
        self.e_client, self.b_client, self.hint_client = self._path_row(
            body, "Cliente de MyTE (myte_client.py)", self.v_client, self.browse_client, scan=self.scan_client)
        self.e_ext, self.b_ext, self.hint_ext = self._path_row(
            body, "Carpeta de extensiones", self.v_ext, self.browse_ext, scan=self.scan_ext, folder=True)

        self.mid = tk.Frame(body, bg=C["bg"])
        self.mid.pack(fill="both", expand=True, pady=(8, 4))
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("M.Horizontal.TProgressbar", troughcolor=C["field"], background=C["cyan"], bordercolor=C["edge"],
                     lightcolor="#8aeaff", darkcolor=C["cyan_d"], thickness=16)
        st.configure("M.Vertical.TScrollbar", troughcolor=C["field"], background=C["edge"], bordercolor=C["field"],
                     arrowcolor=C["sub"], lightcolor=C["edge"], darkcolor=C["edge"])
        st.map("M.Vertical.TScrollbar", background=[("active", C["purple_d"])])

        self.list_card = tk.Frame(self.mid, bg=C["card"], highlightbackground=C["edge"], highlightthickness=1)
        self.scroll = ScrollFrame(self.list_card, C["card"])
        self.scroll.pack(fill="both", expand=True, padx=(8, 2), pady=6)

        self.step_card = tk.Frame(self.mid, bg=C["card"], highlightbackground=C["edge"], highlightthickness=1)
        self.step_lbl = []
        for s in STEPS:
            l = tk.Label(self.step_card, text="○  " + s, bg=C["card"], fg=C["sub"], font=(FONT, 10), anchor="w")
            l.pack(fill="x", padx=14, pady=5)
            self.step_lbl.append(l)
        self.list_card.pack(fill="both", expand=True)

        self.v_video = tk.BooleanVar(value=False)
        orow = tk.Frame(body, bg=C["bg"])
        orow.pack(fill="x", pady=(6, 0))
        Check(orow, self.v_video, C["bg"]).pack(side="left")
        ol = tk.Label(orow, bg=C["bg"], fg=C["text"], font=(FONT, 10), anchor="w", cursor="hand2",
                      text="Instalar soporte de vídeo para fondos animados (PySide6-Addons, unos 150 MB)")
        ol.pack(side="left", padx=8)
        ol.bind("<Button-1>", lambda e: self.v_video.set(not self.v_video.get()))

        self.bar = ttk.Progressbar(body, style="M.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(8, 2))
        self.status = tk.Label(body, text="", bg=C["bg"], fg=C["sub"], font=(FONT, 9), anchor="w")
        self.status.pack(fill="x")

        lf = tk.Frame(body, bg=C["card"], highlightbackground=C["edge"], highlightthickness=1)
        lf.pack(fill="x", pady=6)
        self.txt = tk.Text(lf, height=4, bg=C["card"], fg=C["text"], relief="flat", wrap="word", font=("Consolas", 9),
                           state="disabled", padx=8, pady=6, insertbackground=C["cyan"])
        sb = ttk.Scrollbar(lf, command=self.txt.yview, style="M.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        for tag, col in (("ok", C["ok"]), ("err", C["err"]), ("warn", C["warn"]), ("dim", C["dim"]), ("info", C["text"])):
            self.txt.tag_configure(tag, foreground=col)

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x")
        self.b_main = self._button(row, "Aplicar cambios", self.start, C["purple"], C["purple_d"], "white")
        self.b_main.pack(side="left")
        self.b_open = self._button(row, "Abrir MyTE", self.open_app, C["cyan"], C["cyan_d"], "#031321")
        self.b_close = self._button(row, "Cerrar", self._close, C["card2"], C["edge"], C["text"])
        self.b_close.pack(side="right")

    def _button(self, parent, text, cmd, bg, edge, fg, small=False):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=edge, activeforeground=fg,
                         relief="flat", font=(FONT, 10 if small else 11, "normal" if small else "bold"),
                         padx=12 if small else 22, pady=2 if small else 7, cursor="hand2", highlightbackground=edge,
                         highlightthickness=1, bd=0, disabledforeground=C["dim"])

    def _path_row(self, body, title, var, browse, scan, folder=False):
        tk.Label(body, text=title, bg=C["bg"], fg=C["text"], font=(FONT, 10, "bold"), anchor="w").pack(fill="x", pady=(10, 2))
        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x")
        e = tk.Entry(row, textvariable=var, font=(FONT, 10), relief="flat", bg=C["field"], fg=C["text"],
                     insertbackground=C["cyan"], highlightbackground=C["edge"], highlightcolor=C["cyan"], highlightthickness=1,
                     disabledbackground=C["field"], disabledforeground=C["dim"])
        e.pack(side="left", fill="x", expand=True, ipady=4)
        e.bind("<Return>", lambda ev: scan())
        e.bind("<FocusOut>", lambda ev: None)
        b = self._button(row, "Examinar…", browse, C["card2"], C["edge"], C["text"], small=True)
        b.pack(side="left", padx=(8, 0))
        if folder:
            o = self._button(row, "📂", self.open_folder, C["card2"], C["edge"], C["text"], small=True)
            o.pack(side="left", padx=(6, 0))
            b.open_btn = o
        hint = tk.Label(body, text="", bg=C["bg"], fg=C["sub"], font=(FONT, 9), justify="left", wraplength=630, anchor="w")
        hint.pack(fill="x", pady=(3, 0))
        return e, b, hint

    # ------------------------------------------------------------ rutas y escaneo
    def _first_scan(self):
        c = detect_client()
        if c:
            self.v_client.set(c)
            self.hint_client.configure(fg=C["ok"], text="✔ Instalación de MyTE detectada automáticamente.")
        else:
            self.hint_client.configure(fg=C["warn"], text="No encuentro la instalación predeterminada. Pulsa «Examinar…» y elige "
                                                          "myte_client.py (está dentro de la carpeta «app» de MyTE).")
        self.scan_client()

    def browse_client(self):
        cur = os.path.dirname(resolve_client(self.v_client.get()) or "") or DEFAULT_BASE
        f = filedialog.askopenfilename(title="Elige myte_client.py", initialdir=cur if os.path.isdir(cur) else os.path.expanduser("~"),
                                       filetypes=[("Cliente de MyTE", "myte_client.py"), ("Python", "*.py"), ("Todos", "*.*")])
        if f:
            self.v_client.set(os.path.normpath(f))
            self.hint_client.configure(fg=C["sub"], text="")
            self.scan_client()

    def browse_ext(self):
        cur = self.v_ext.get().strip()
        start = cur if os.path.isdir(cur) else (os.path.dirname(cur) if os.path.isdir(os.path.dirname(cur or "x")) else os.path.expanduser("~"))
        d = filedialog.askdirectory(title="Elige la carpeta de extensiones", initialdir=start)
        if d:
            self.v_ext.set(os.path.normpath(d))
            self.scan_ext()

    def open_folder(self):
        d = self.v_ext.get().strip()
        try:
            ensure_folders(d)
            if IS_WIN:
                os.startfile(d)
            else:
                subprocess.Popen(["xdg-open", d])
        except OSError as e:
            messagebox.showerror(APP, "No se pudo abrir la carpeta:\n%s" % e)

    def scan_client(self):
        """Comprueba el cliente y lee qué hay inyectado ya."""
        c = resolve_client(self.v_client.get())
        self.client = c
        if not c:
            self.state = dict(loader=False, items={}, source=None, runtime=False, foreign=False)
            if self.v_client.get().strip():
                self.hint_client.configure(fg=C["err"], text="No encuentro myte_client.py en esa ruta.")
            self.v_ext.set(self.v_ext.get() or default_ext_folder(None))
            self.scan_ext()
            return
        self.v_client.set(c)
        self.state = read_state(c)
        s = self.state
        n = len(s["items"])
        if s["loader"] and n:
            msg, col = "✔ Inyector activo: %d elemento(s) ya instalados en este cliente." % n, C["ok"]
        elif s["loader"]:
            msg, col = "Este cliente tiene un cargador sin registro (lo puso otra copia del injector). Aplica para regularizarlo.", C["warn"]
        elif n:
            msg, col = ("El cliente se actualizó y perdió el cargador (había %d elemento(s)). Pulsa «Aplicar cambios» para "
                        "reactivarlos." % n), C["warn"]
        else:
            msg, col = "Cliente limpio: todavía no tiene extensiones inyectadas.", C["sub"]
        if not s["loader"] or s["foreign"] or n:
            self.hint_client.configure(text=msg, fg=col)
        if not self.v_ext.get().strip():
            self.v_ext.set(default_ext_folder(c))
        self.scan_ext()

    def scan_ext(self):
        folder = os.path.normpath(self.v_ext.get().strip().strip('"')) if self.v_ext.get().strip() else ""
        self.items = []
        if folder:
            try:
                created = not os.path.isdir(folder)
                ensure_folders(folder)
                if created:
                    self.hint_ext.configure(fg=C["ok"], text="✔ Carpeta creada: dentro va un .py por extensión y «temas\\<nombre>» por tema.")
                else:
                    self.hint_ext.configure(fg=C["sub"], text="Un .py por extensión. En «temas» una carpeta por tema (su .py + imagen/GIF/vídeo).")
                self.items = scan_sources(folder)
            except OSError as e:
                self.hint_ext.configure(fg=C["err"], text="No se pudo crear/leer la carpeta: %s" % e)
            self.v_ext.set(folder)
        self.build_rows()

    # ------------------------------------------------------------ lista
    def build_rows(self):
        self.rows = []
        inst = self.state["items"]
        seen = set()
        for it in self.items:
            key = (it["kind"], it["id"])
            seen.add(key)
            e = inst.get(key)
            if not it["valid"] and e:
                self.rows.append(dict(item=None, entry=e, kind=it["kind"], id=it["id"], name=it["name"], status="broken",
                                      version=it["version"], author=it["author"], desc=it["err"], requires=[],
                                      var=tk.BooleanVar(value=True)))
                continue
            st = "invalid" if not it["valid"] else ("new" if not e else ("same" if e.get("sha") == it["sha"] else "update"))
            self.rows.append(dict(item=it, entry=e, kind=it["kind"], id=it["id"], name=it["name"], status=st,
                                  version=it["version"], author=it["author"], desc=it["err"] if st == "invalid" else it["desc"],
                                  requires=it["requires"], var=tk.BooleanVar(value=bool(e) and st != "invalid")))
        for key, e in inst.items():
            if key not in seen:
                self.rows.append(dict(item=None, entry=e, kind=key[0], id=key[1], name=e.get("name", key[1]), status="orphan",
                                      version=e.get("version", ""), author="", desc="Instalado, pero ya no está en la carpeta de extensiones.",
                                      requires=e.get("requires", []), var=tk.BooleanVar(value=True)))
        self.paint_rows()
        if any(r["item"] and r["item"].get("video") for r in self.rows):
            self.v_video.set(True)
        self.update_summary()

    TAGS = {"new": ("✦ nueva", C["cyan"]), "same": ("✔ instalada", C["ok"]), "update": ("⟳ actualizar", C["warn"]),
            "orphan": ("⚠ sin archivo", C["warn"]), "invalid": ("✖ no válida", C["err"]), "broken": ("⚠ con errores", C["err"])}

    def paint_rows(self):
        sf = self.scroll
        sf.clear()
        if not self.rows:
            tk.Label(sf.inner, bg=C["card"], fg=C["sub"], font=(FONT, 10), justify="left", anchor="w", wraplength=560,
                     text="Aquí aparecerán tus extensiones y temas.\n\nCopia los .py de las extensiones en:\n  %s\n\nY cada tema en una "
                          "carpeta dentro de:\n  %s" % (self.v_ext.get() or "(elige una carpeta)",
                                                        os.path.join(self.v_ext.get() or "…", "temas"))).pack(fill="x", padx=12, pady=14)
            return
        for kind, title, icon in (("ext", "EXTENSIONES", "🧩"), ("theme", "TEMAS", "🎨")):
            rows = [r for r in self.rows if r["kind"] == kind]
            if not rows:
                continue
            tk.Label(sf.inner, text="%s  %s  (%d)" % (icon, title, len(rows)), bg=C["card"], fg=C["cyan"],
                     font=(FONT, 9, "bold"), anchor="w").pack(fill="x", padx=6, pady=(8, 3))
            for r in rows:
                self.paint_row(sf, r)

    def paint_row(self, sf, r):
        bg = C["card2"]
        ok = r["status"] not in ("invalid",)
        card = tk.Frame(sf.inner, bg=bg, highlightbackground=C["edge"], highlightthickness=1)
        card.pack(fill="x", padx=4, pady=3)
        top = tk.Frame(card, bg=bg)
        top.pack(fill="x", padx=10, pady=(8, 0))
        Check(top, r["var"], bg, command=self.update_summary, enabled=ok).pack(side="left")
        name = tk.Label(top, text=r["name"], bg=bg, fg=C["text"] if ok else C["dim"], font=(FONT, 11, "bold"), anchor="w",
                        cursor="hand2" if ok else "arrow")
        name.pack(side="left", padx=(10, 6))
        if ok:
            name.bind("<Button-1>", lambda e, r=r: (r["var"].set(not r["var"].get()), self.update_summary()))
        meta = "  ·  ".join(x for x in (("v" + r["version"]) if r["version"] else "", r["author"]) if x)
        if meta:
            tk.Label(top, text=meta, bg=bg, fg=C["dim"], font=(FONT, 9)).pack(side="left")
        txt, col = self.TAGS[r["status"]]
        tk.Label(top, text=txt, bg=bg, fg=col, font=(FONT, 9, "bold")).pack(side="right")
        if r["desc"]:
            d = tk.Label(card, text=r["desc"], bg=bg, fg=C["err"] if r["status"] in ("invalid", "broken") else C["sub"],
                         font=(FONT, 9), justify="left", anchor="w", wraplength=500)
            d.pack(fill="x", padx=(42, 10), pady=(1, 0))
            sf.wrap.append(d)
        if r["requires"]:
            tk.Label(card, text="⬇ Instalará: " + ", ".join(r["requires"]), bg=bg, fg=C["warn"], font=(FONT, 9),
                     anchor="w").pack(fill="x", padx=(42, 10), pady=(1, 0))
        tk.Frame(card, bg=bg, height=8).pack()

    def plan(self):
        keep, orphans, remove = [], [], []
        for r in self.rows:
            on = r["var"].get()
            if r["item"] and on:
                keep.append(r["item"])
            elif r["entry"] and on:
                orphans.append(dict(r["entry"]))
            elif r["entry"] and not on:
                remove.append(r["entry"])
        return keep, orphans, remove

    def update_summary(self):
        if self.phase != "list":
            return
        add = upd = rem = 0
        for r in self.rows:
            on = r["var"].get()
            if on and not r["entry"]:
                add += 1
            elif on and r["status"] == "update":
                upd += 1
            elif not on and r["entry"]:
                rem += 1
        parts = []
        if add:
            parts.append("+%d nueva(s)" % add)
        if upd:
            parts.append("%d por actualizar" % upd)
        if rem:
            parts.append("%d por quitar" % rem)
        repair = self.client and not self.state["loader"] and self.state["items"]
        self.status.configure(fg=C["sub"], text=("Cambios pendientes: " + " · ".join(parts)) if parts else
                              ("Listo para reactivar el cargador." if repair else "Sin cambios pendientes."))

    # ------------------------------------------------------------ aplicar
    def show_phase(self, phase):
        self.phase = phase
        if phase == "list":
            self.step_card.pack_forget()
            self.list_card.pack(fill="both", expand=True)
        else:
            self.list_card.pack_forget()
            self.step_card.pack(fill="x")

    def start(self):
        if self.busy:
            return
        if self.phase == "after":
            self.b_open.pack_forget()
            self.bar["value"] = 0
            self.show_phase("list")
            self.b_main.configure(text="Aplicar cambios")
            self.scan_client()
            return
        client = resolve_client(self.v_client.get())
        if not client:
            messagebox.showerror(APP, "Indica dónde está myte_client.py (botón «Examinar…»).")
            return
        folder = self.v_ext.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror(APP, "Elige una carpeta de extensiones válida.")
            return
        keep, orphans, remove = self.plan()
        if not keep and not orphans and not self.state["loader"] and not self.state["items"]:
            messagebox.showinfo(APP, "No has marcado nada y el cliente ya está limpio.")
            return
        if not keep and not orphans:
            if not messagebox.askyesno(APP, "No hay nada marcado: se quitará todo y MyTE volverá a ser el cliente original.\n\n¿Continuar?"):
                return
        elif remove and not messagebox.askyesno(APP, "Se quitarán %d elemento(s) desmarcado(s) del cliente.\n\n¿Continuar?" % len(remove)):
            return
        self.busy = True
        self.client = client
        self.show_phase("run")
        for i in range(len(STEPS)):
            self._set_step(i, "wait")
        self.b_main.configure(state="disabled", text="Aplicando…")
        for w in (self.e_client, self.b_client, self.e_ext, self.b_ext):
            w.configure(state="disabled")
        self.status.configure(fg=C["sub"], text="Trabajando…")
        save_config(dict(client=client, ext=folder))
        eng = Engine(lambda *a: self.q.put(a), client, folder, keep, orphans, remove, self.v_video.get())
        threading.Thread(target=eng.go, daemon=True).start()

    def _set_step(self, i, state):
        icon, col = {"wait": ("○", C["sub"]), "run": ("⏳", C["cyan"]), "ok": ("✔", C["ok"]), "skip": ("✔", C["ok"]),
                     "err": ("✖", C["err"])}[state]
        extra = ("  (ya existe)" if i == 1 else "  (nada que hacer)") if state == "skip" else ""
        self.step_lbl[i].configure(text="%s  %s%s" % (icon, STEPS[i], extra), fg=col,
                                   font=(FONT, 10, "bold" if state == "run" else "normal"))

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                getattr(self, "ev_" + ev[0])(*ev[1:])
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def ev_log(self, text, kind):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", kind)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def ev_step(self, i, state):
        self._set_step(i, state)
        if state == "run":
            self.status.configure(fg=C["sub"], text=STEPS[i] + "…")

    def ev_prog(self, v):
        self.bar["value"] = v

    def _unlock(self):
        self.busy = False
        self.phase = "after"
        for w in (self.e_client, self.b_client, self.e_ext, self.b_ext):
            w.configure(state="normal")
        self.b_main.configure(state="normal", text="Volver a la lista")

    def ev_done(self, msg):
        self.ev_log(msg, "ok")
        self.status.configure(fg=C["ok"], text=msg)
        self._unlock()
        if resolve_client(self.v_client.get()):
            self.b_open.pack(side="left", padx=10)

    def ev_fail(self, msg):
        self.ev_log("ERROR: " + msg, "err")
        self.status.configure(fg=C["err"], text="No se pudo completar: " + msg)
        self._unlock()
        messagebox.showerror(APP, msg)

    def open_app(self):
        c = resolve_client(self.v_client.get())
        if not c:
            return
        app_dir = os.path.dirname(c)
        pyw = find_pythonw(app_dir)
        try:
            if pyw:
                subprocess.Popen([pyw, c], cwd=app_dir, creationflags=NO_WINDOW)
            else:
                subprocess.Popen([sys.executable if not getattr(sys, "frozen", False) else "python", c], cwd=app_dir)
        except OSError as e:
            messagebox.showerror(APP, "No se pudo abrir MyTE:\n%s" % e)

    def _close(self):
        if self.busy and not messagebox.askyesno(APP, "Se está aplicando algo. ¿Salir igualmente?"):
            return
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
