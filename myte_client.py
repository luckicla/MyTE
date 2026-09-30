#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyTE Client  -  cliente de mensajería para MyTE  (PySide6)
==========================================================
Requisitos:   pip install PySide6
Uso:          python myte_client.py
Protocolo:    tramas JSON (4 bytes de longitud + JSON UTF-8) sobre TCP, igual que myte_server.py
"""
import base64, html, json, os, queue, random, re, socket, struct, sys, threading, time
from datetime import datetime

from PySide6.QtCore import (Qt, QObject, Signal, QTimer, QSize, QPoint, QRect, QRectF, QByteArray,
                            QBuffer, QIODevice, QSettings, QUrl)
from PySide6.QtGui import (QColor, QPainter, QPixmap, QImage, QFont, QLinearGradient, QRadialGradient,
                           QBrush, QPen, QPainterPath, QIcon, QGuiApplication, QDesktopServices,
                           QAction, QKeySequence)
from PySide6.QtWidgets import (QApplication, QWidget, QMainWindow, QDialog, QLabel, QPushButton,
                               QToolButton, QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea,
                               QFrame, QMenu, QFileDialog, QMessageBox, QCheckBox, QButtonGroup,
                               QSizePolicy, QSpacerItem, QInputDialog, QTabBar, QProgressBar)

APP_NAME = "MyTE"
MAX_AVATAR_B64 = 400_000          # límite del servidor
AVATAR_PX = 160
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
EMOJIS = ["😀", "😁", "😂", "🤣", "😊", "😍", "😘", "😎", "🤔", "😅", "😭", "😡", "👍", "👎", "👏", "🙏",
          "🔥", "✨", "💡", "🚀", "🎉", "❤️", "💙", "⭐", "✅", "❌", "⚠️", "📌", "📝", "📎", "💻", "🛰️"]


# =============================================================== temas
THEMES = {
    "light": dict(          # Frutiger Aero
        name="light", text="#12385a", sub="#4d7291", faint="#7ea0bb",
        glass="rgba(255,255,255,150)", glass2="rgba(255,255,255,205)", glass_edge="rgba(255,255,255,235)",
        line="rgba(70,140,190,70)", accent="#2aa9ec", accent2="#7bdc4a", accent_txt="#ffffff",
        btn_top="#9be6ff", btn_mid="#39b8f1", btn_mid2="#159bde", btn_bot="#55cbf7", btn_edge="#1a86bf",
        green_top="#d3ff9e", green_mid="#8fe04c", green_mid2="#5fc42a", green_bot="#97e660", green_edge="#4a9f22",
        mine_a="#d9ffc4", mine_b="#a9ec7f", mine_edge="#7fc24f", mine_txt="#173c10",
        other_a="#ffffff", other_b="#e4f4ff", other_edge="#b7dcf2", other_txt="#12385a",
        input_bg="rgba(255,255,255,235)", sel="rgba(42,169,236,70)", hover="rgba(255,255,255,140)",
        danger="#e2483d", ok="#3fae2a", warn="#f0a020", online="#39d353", offline="#a3b4c2",
        bg1="#f3fbff", bg2="#bfe7fb", bg3="#c9f3d9", rail="rgba(255,255,255,120)"),
    "dark": dict(           # Space era
        name="dark", text="#e6f0ff", sub="#9db4dd", faint="#6a7fa8",
        glass="rgba(24,28,72,175)", glass2="rgba(34,40,96,205)", glass_edge="rgba(120,170,255,110)",
        line="rgba(110,150,255,60)", accent="#3fd9ff", accent2="#b477ff", accent_txt="#031321",
        btn_top="#7cf0ff", btn_mid="#22b7e6", btn_mid2="#1288c4", btn_bot="#3ccbf2", btn_edge="#0d6e9e",
        green_top="#d8b4ff", green_mid="#a066f0", green_mid2="#7a3fd6", green_bot="#a878f5", green_edge="#5a2aa8",
        mine_a="#2a63b8", mine_b="#5b3fc9", mine_edge="#7f8bff", mine_txt="#f2f6ff",
        other_a="#1d2360", other_b="#151a4a", other_edge="#3a4aa0", other_txt="#e6f0ff",
        input_bg="rgba(12,16,52,220)", sel="rgba(63,217,255,55)", hover="rgba(120,160,255,40)",
        danger="#ff5b6e", ok="#4ee08a", warn="#ffc24d", online="#4ee08a", offline="#5d6b8f",
        bg1="#04061a", bg2="#16123f", bg3="#2a1250", rail="rgba(8,10,40,150)"),
}


def build_qss(t):
    g = lambda top, m1, m2, bot: (f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {top},stop:0.49 {m1},"
                                  f"stop:0.5 {m2},stop:1 {bot})")
    blue = g(t["btn_top"], t["btn_mid"], t["btn_mid2"], t["btn_bot"])
    green = g(t["green_top"], t["green_mid"], t["green_mid2"], t["green_bot"])
    return f"""
* {{ font-family: "Segoe UI","Trebuchet MS","Helvetica Neue","DejaVu Sans",sans-serif; font-size: 10.5pt; color: {t['text']}; }}
QToolTip {{ background: {t['glass2']}; color: {t['text']}; border: 1px solid {t['accent']}; padding: 4px 8px; border-radius: 6px; }}
QDialog, QMessageBox, QInputDialog {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['bg1']},stop:1 {t['bg2']}); }}
#detached {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {t['bg1']},stop:0.6 {t['bg2']},stop:1 {t['bg3']}); }}
QLabel {{ background: transparent; }}
#title {{ font-size: 15pt; font-weight: 600; }}
#h2 {{ font-size: 12pt; font-weight: 600; }}
#sub {{ color: {t['sub']}; font-size: 9pt; }}
#faint {{ color: {t['faint']}; font-size: 8.5pt; }}
#logo {{ font-size: 34pt; font-weight: 700; color: {t['accent']}; }}

QFrame#glass, QFrame#card {{ background: {t['glass']}; border: 1px solid {t['glass_edge']}; border-radius: 16px; }}
QFrame#card {{ background: {t['glass2']}; border-radius: 12px; }}
QFrame#rail {{ background: {t['rail']}; border: none; border-right: 1px solid {t['line']}; }}
QFrame#header {{ background: {t['glass2']}; border: none; border-bottom: 1px solid {t['line']}; border-top-left-radius: 16px; border-top-right-radius: 16px; }}
QFrame#inputbar {{ background: {t['glass']}; border: none; border-top: 1px solid {t['line']}; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; }}
QFrame#sep {{ background: {t['line']}; max-height: 1px; border: none; }}
QFrame#column {{ background: {t['glass']}; border: 1px solid {t['glass_edge']}; border-radius: 14px; }}

QScrollArea, QScrollArea > QWidget > QWidget#viewport, #msgHost, #colHost {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 4px; min-width: 30px; }}

QLineEdit, QPlainTextEdit#composer {{ background: {t['input_bg']}; border: 1px solid {t['glass_edge']}; border-radius: 12px;
    padding: 7px 11px; selection-background-color: {t['accent']}; selection-color: {t['accent_txt']}; }}
QLineEdit:focus, QPlainTextEdit#composer:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit:read-only {{ color: {t['sub']}; }}

QPushButton {{ background: {blue}; color: {t['accent_txt']}; border: 1px solid {t['btn_edge']}; border-radius: 13px;
    padding: 7px 18px; font-weight: 600; }}
QPushButton:hover {{ border: 1px solid {t['text']}; }}
QPushButton:pressed {{ padding-top: 9px; padding-bottom: 5px; }}
QPushButton:disabled {{ background: {t['glass']}; color: {t['faint']}; border: 1px solid {t['line']}; }}
QPushButton#green {{ background: {green}; border: 1px solid {t['green_edge']}; color: {'#123008' if t['name']=='light' else '#ffffff'}; }}
QPushButton#danger {{ background: {g('#ffb3ad','#f0574a','#dd3a2d','#f37a6e')}; border: 1px solid #a3261c; color: white; }}
QPushButton#ghost {{ background: {t['glass']}; color: {t['text']}; border: 1px solid {t['line']}; }}
QPushButton#ghost:hover {{ background: {t['glass2']}; border: 1px solid {t['accent']}; }}
QPushButton#link {{ background: transparent; border: none; color: {t['accent']}; padding: 2px 4px; font-weight: 400; text-decoration: underline; }}
QPushButton#seg {{ background: {t['glass']}; color: {t['sub']}; border: 1px solid {t['glass_edge']}; border-radius: 12px; padding: 6px 16px; font-weight: 500; }}
QPushButton#seg:checked {{ background: {blue}; color: {t['accent_txt']}; border: 1px solid {t['btn_edge']}; font-weight: 600; }}
QPushButton#seg:hover:!checked {{ border: 1px solid {t['accent']}; }}
QPushButton#fab {{ background: {green}; border: 2px solid {t['green_edge']}; border-radius: 27px; padding: 0; }}
QPushButton#fab:hover {{ border: 2px solid {t['text']}; }}
QProgressBar {{ background: {t['input_bg']}; border: 1px solid {t['line']}; border-radius: 8px; text-align: center; font-size: 8pt; color: {t['text']}; }}
QProgressBar::chunk {{ background: {green}; border-radius: 7px; }}
QPushButton#mini {{ padding: 4px 11px; font-size: 9pt; border-radius: 10px; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 12px; padding: 5px; font-size: 13pt; }}
QToolButton:hover {{ background: {t['hover']}; border: 1px solid {t['glass_edge']}; }}
QToolButton:checked {{ background: {t['sel']}; border: 1px solid {t['accent']}; }}
QToolButton::menu-indicator {{ image: none; }}
QToolButton#avatarBtn {{ border-radius: 26px; padding: 2px; border: 2px solid {t['glass_edge']}; }}
QToolButton#avatarBtn:hover {{ border: 2px solid {t['accent']}; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 12px; margin: 1px 6px; }}
QListWidget::item:hover {{ background: {t['hover']}; }}
QListWidget::item:selected {{ background: {t['sel']}; border: 1px solid {t['accent']}; }}

QFrame#bubbleMine {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['mine_a']},stop:1 {t['mine_b']});
    border: 1px solid {t['mine_edge']}; border-radius: 15px; }}
QFrame#bubbleOther {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['other_a']},stop:1 {t['other_b']});
    border: 1px solid {t['other_edge']}; border-radius: 15px; }}
QFrame#bubbleMine QLabel {{ color: {t['mine_txt']}; }}
QFrame#bubbleOther QLabel {{ color: {t['other_txt']}; }}
QLabel#sysmsg {{ background: {t['glass']}; border: 1px solid {t['line']}; border-radius: 10px; padding: 3px 12px; color: {t['sub']}; font-size: 9pt; }}
QLabel#daymsg {{ color: {t['faint']}; font-size: 8.5pt; }}
QLabel#badge {{ background: {g('#ff9a90','#f0483c','#d92c20','#f06a5f')}; color: white; border-radius: 9px; font-size: 8pt; font-weight: 700; padding: 0 5px; }}
QLabel#roleTag {{ background: {t['sel']}; border: 1px solid {t['accent']}; border-radius: 8px; padding: 0 7px; font-size: 8pt; }}
QLabel#toast {{ background: {t['glass2']}; border: 1px solid {t['accent']}; border-radius: 14px; padding: 9px 20px; font-weight: 600; }}
QLabel#toastErr {{ background: {t['glass2']}; border: 1px solid {t['danger']}; border-radius: 14px; padding: 9px 20px; font-weight: 600; color: {t['danger']}; }}
QLabel#banner {{ background: {t['warn']}; color: #2b1c00; border-radius: 0; padding: 6px; font-weight: 600; }}

QMenu {{ background: {t['glass2']}; border: 1px solid {t['glass_edge']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 7px; }}
QMenu::item:selected {{ background: {t['sel']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 5px 8px; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid {t['accent']}; background: {t['input_bg']}; }}
QCheckBox::indicator:checked {{ background: {blue}; }}
QTabBar::tab {{ background: {t['glass']}; border: 1px solid {t['glass_edge']}; padding: 7px 18px; border-radius: 11px; margin: 2px; }}
QTabBar::tab:selected {{ background: {blue}; color: {t['accent_txt']}; font-weight: 600; }}
"""


# ============================================================ utilidades
def fmt_time(ts):
    d = datetime.fromtimestamp(ts)
    n = datetime.now()
    if d.date() == n.date():
        return d.strftime("%H:%M")
    if (n.date() - d.date()).days < 7:
        return d.strftime("%a %H:%M")
    return d.strftime("%d/%m %H:%M")


def day_label(ts):
    d = datetime.fromtimestamp(ts).date()
    n = datetime.now().date()
    if d == n:
        return "Hoy"
    if (n - d).days == 1:
        return "Ayer"
    return d.strftime("%d/%m/%Y")


def human_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def rich(text):
    s = html.escape(text or "")
    s = URL_RE.sub(lambda m: f'<a href="{m.group(1)}" style="color:#1b7fd0">{m.group(1)}</a>', s)
    return s.replace("\n", "<br>")


_AV_COLORS = [("#4fc3f7", "#0288d1"), ("#81e07a", "#2e9e3a"), ("#ffb74d", "#ef6c00"), ("#ba68c8", "#7b1fa2"),
              ("#f06292", "#c2185b"), ("#4dd0e1", "#00838f"), ("#9575cd", "#512da8"), ("#e57373", "#c62828")]
_av_cache = {}


def b64_to_image(b64):
    try:
        img = QImage()
        if img.loadFromData(QByteArray(base64.b64decode(b64))):
            return img
    except Exception:
        pass
    return None


def avatar_pixmap(b64, name, size, online=None, dark=False):
    """Avatar redondeado (foto o inicial sobre color) con brillo y punto de estado opcional."""
    key = (hash(b64) if b64 else None, name, size, online, dark)
    if key in _av_cache:
        return _av_cache[key]
    dpr = 2
    px = QPixmap(size * dpr, size * dpr)
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    rect = QRectF(1, 1, size - 2, size - 2)
    path = QPainterPath()
    path.addEllipse(rect)
    p.setClipPath(path)
    img = b64_to_image(b64) if b64 else None
    if img:
        img = img.scaled(size * dpr, size * dpr, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawImage(QRectF(0, 0, size, size), img,
                    QRectF((img.width() - size * dpr) / 2, (img.height() - size * dpr) / 2, size * dpr, size * dpr))
    else:
        c1, c2 = _AV_COLORS[sum(ord(c) for c in (name or "?")) % len(_AV_COLORS)]
        gr = QLinearGradient(0, 0, 0, size)
        gr.setColorAt(0, QColor(c1))
        gr.setColorAt(1, QColor(c2))
        p.fillRect(QRectF(0, 0, size, size), gr)
        f = QFont()
        f.setPixelSize(int(size * 0.46))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (name or "?").strip()[:1].upper() or "?")
    # brillo aero
    gl = QLinearGradient(0, 0, 0, size * 0.55)
    gl.setColorAt(0, QColor(255, 255, 255, 110))
    gl.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(0, 0, size, size * 0.55), gl)
    p.setClipping(False)
    p.setPen(QPen(QColor(255, 255, 255, 200) if not dark else QColor(140, 180, 255, 150), 1.6))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(rect)
    if online is not None:
        d = max(9, size * 0.26)
        r = QRectF(size - d - 1, size - d - 1, d, d)
        p.setPen(QPen(QColor("white") if not dark else QColor("#0b0f33"), 2))
        p.setBrush(QColor("#39d353") if online else QColor("#a3b4c2"))
        p.drawEllipse(r)
    p.end()
    if len(_av_cache) > 600:
        _av_cache.clear()
    _av_cache[key] = px
    return px


def image_to_avatar_b64(img):
    """Recorta al centro, reduce y codifica en PNG/JPEG por debajo del límite del servidor."""
    if img.isNull():
        return None
    side = min(img.width(), img.height())
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    for px_size, fmt, q in ((AVATAR_PX, "PNG", -1), (AVATAR_PX, "JPG", 88), (110, "JPG", 80), (72, "JPG", 70)):
        im = img.scaled(px_size, px_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        im.save(buf, fmt, q)
        b64 = base64.b64encode(bytes(ba)).decode()
        if len(b64) < MAX_AVATAR_B64 - 1000:
            return b64
    return None


def pick_avatar(parent):
    fn, _ = QFileDialog.getOpenFileName(parent, "Elige una imagen", "",
                                        "Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
    if not fn:
        return None
    b = image_to_avatar_b64(QImage(fn))
    if not b:
        QMessageBox.warning(parent, APP_NAME, "No se pudo usar esa imagen.")
    return b


def qimage_to_png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def make_app_icon():
    px = QPixmap(128, 128)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHints(QPainter.Antialiasing)
    gr = QRadialGradient(50, 40, 90)
    gr.setColorAt(0, QColor("#a8ecff"))
    gr.setColorAt(0.5, QColor("#25aef0"))
    gr.setColorAt(1, QColor("#0b63b5"))
    p.setBrush(gr)
    p.setPen(QPen(QColor("#ffffff"), 4))
    p.drawEllipse(6, 6, 116, 116)
    gl = QLinearGradient(0, 8, 0, 70)
    gl.setColorAt(0, QColor(255, 255, 255, 190))
    gl.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(gl)
    p.drawEllipse(22, 10, 84, 60)
    f = QFont("Segoe UI")
    f.setPixelSize(70)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 8, 128, 120), Qt.AlignCenter, "M")
    p.end()
    return QIcon(px)


# ================================================================ red
class Net(QObject):
    """Conexión TCP con tramas [4 bytes longitud][JSON]. Lectura y escritura en hilos propios."""
    message = Signal(dict)
    connected = Signal()
    failed = Signal(str)
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.sock = None
        self.gen = 0
        self.outq = None

    def is_open(self):
        return self.sock is not None

    def connect_to(self, host, port):
        self.disconnect_now()
        self.gen += 1
        gen = self.gen
        threading.Thread(target=self._connect_thread, args=(host, port, gen), daemon=True).start()

    def _connect_thread(self, host, port, gen):
        try:
            s = socket.create_connection((host, port), timeout=6)
            s.settimeout(None)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except Exception as e:
            if gen == self.gen:
                self.failed.emit(f"No se pudo conectar con {host}:{port}\n({e})")
            return
        if gen != self.gen:
            s.close()
            return
        self.sock = s
        self.outq = queue.Queue()
        threading.Thread(target=self._reader, args=(s, gen), daemon=True).start()
        threading.Thread(target=self._writer, args=(s, self.outq, gen), daemon=True).start()
        self.connected.emit()

    def _recv_exact(self, s, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionError("cerrado")
            buf.extend(chunk)
        return bytes(buf)

    def _reader(self, s, gen):
        try:
            while True:
                n = struct.unpack(">I", self._recv_exact(s, 4))[0]
                msg = json.loads(self._recv_exact(s, n).decode("utf-8"))
                if gen != self.gen:
                    return
                self.message.emit(msg)
        except Exception:
            pass
        if gen == self.gen:
            self.sock = None
            self.closed.emit()
        try:
            s.close()
        except OSError:
            pass

    def _writer(self, s, q, gen):
        while True:
            d = q.get()
            if d is None:
                return
            try:
                s.sendall(d)
            except OSError:
                try:
                    s.close()
                except OSError:
                    pass
                return

    def send(self, obj):
        if self.outq is None or self.sock is None:
            return False
        b = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.outq.put(struct.pack(">I", len(b)) + b)
        return True

    def disconnect_now(self):
        self.gen += 1
        s, q = self.sock, self.outq
        self.sock, self.outq = None, None
        if q:
            q.put(None)
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


# ========================================================== fondo pintado
class AeroBackground(QWidget):
    """Fondo Frutiger Aero (cielo, burbujas) o Space era (nebulosa, estrellas)."""
    def __init__(self):
        super().__init__()
        self.theme = "light"
        rnd = random.Random(7)
        self.bubbles = [(rnd.random(), rnd.random(), rnd.uniform(0.03, 0.14), rnd.random()) for _ in range(16)]
        self.stars = [(rnd.random(), rnd.random(), rnd.uniform(0.5, 2.0), rnd.random()) for _ in range(150)]

    def set_theme(self, name):
        self.theme = name
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = THEMES[self.theme]
        g = QLinearGradient(0, 0, w * 0.6, h)
        g.setColorAt(0, QColor(t["bg1"]))
        g.setColorAt(0.55, QColor(t["bg2"]))
        g.setColorAt(1, QColor(t["bg3"]))
        p.fillRect(self.rect(), g)
        if self.theme == "light":
            rg = QRadialGradient(w * 0.85, h * 0.05, max(w, h) * 0.6)
            rg.setColorAt(0, QColor(255, 255, 255, 200))
            rg.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillRect(self.rect(), rg)
            for x, y, r, a in self.bubbles:
                rad = r * min(w, h)
                c = QRadialGradient(x * w - rad * 0.3, y * h - rad * 0.3, rad * 1.1)
                c.setColorAt(0, QColor(255, 255, 255, 150))
                c.setColorAt(0.7, QColor(150, 220, 255, 45))
                c.setColorAt(1, QColor(80, 190, 240, 70))
                p.setBrush(c)
                p.setPen(QPen(QColor(255, 255, 255, 130), 1.2))
                p.drawEllipse(QPoint(int(x * w), int(y * h)), int(rad), int(rad))
        else:
            for cx, cy, col in ((0.2, 0.25, QColor(120, 60, 255, 70)), (0.85, 0.75, QColor(0, 170, 255, 55))):
                rg = QRadialGradient(cx * w, cy * h, max(w, h) * 0.5)
                rg.setColorAt(0, col)
                rg.setColorAt(1, QColor(0, 0, 0, 0))
                p.fillRect(self.rect(), rg)
            p.setPen(Qt.NoPen)
            for x, y, r, a in self.stars:
                p.setBrush(QColor(200, 225, 255, int(70 + 150 * a)))
                p.drawEllipse(QRectF(x * w, y * h, r, r))
        p.end()


# =================================================================== mensajes
class ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class MessageRow(QWidget):
    """Una fila del chat: avatar + burbuja (texto, imagen o archivo)."""
    def __init__(self, view, msg):
        super().__init__()
        self.view, self.msg = view, msg
        self.setAttribute(Qt.WA_StyledBackground, False)
        st = view.win
        mine = msg["sender"] == st.me
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 3, 10, 3)
        lay.setSpacing(8)
        self.bubble = QFrame()
        self.bubble.setObjectName("bubbleMine" if mine else "bubbleOther")
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(12, 7, 12, 6)
        bl.setSpacing(3)
        show_name = (not mine) and view.chat["type"] == "group"
        if show_name:
            nm = QLabel(st.display_name(view.chat, msg["sender"]))
            nm.setStyleSheet(f"font-weight:700; font-size:9pt; color:{st.theme()['accent'] if st.theme()['name']=='dark' else '#1d6fa5'};")
            bl.addWidget(nm)
        self.img_label = None
        kind = msg["kind"]
        if kind == "image" and msg.get("file"):
            self.img_label = ClickLabel("Cargando imagen…")
            self.img_label.setCursor(Qt.PointingHandCursor)
            self.img_label.setMinimumSize(140, 70)
            self.img_label.setAlignment(Qt.AlignCenter)
            self.img_label.clicked.connect(self.open_image)
            bl.addWidget(self.img_label)
            st.request_file(msg["file"]["id"], self.on_image)
        elif kind == "file" and msg.get("file"):
            f = msg["file"]
            row = QHBoxLayout()
            ic = QLabel("📎")
            ic.setStyleSheet("font-size:20pt;")
            row.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(0)
            n = QLabel(f["name"])
            n.setStyleSheet("font-weight:600;")
            n.setWordWrap(True)
            col.addWidget(n)
            col.addWidget(QLabel(human_size(f["size"])))
            row.addLayout(col, 1)
            bl.addLayout(row)
            b = QPushButton("Descargar")
            b.setObjectName("mini")
            b.clicked.connect(lambda: st.download_file(f["id"], f["name"]))
            bl.addWidget(b, 0, Qt.AlignLeft)
        if msg.get("text") and kind != "file":
            self.txt = QLabel(rich(msg["text"]))
            self.txt.setTextFormat(Qt.RichText)
            self.txt.setWordWrap(True)
            self.txt.setOpenExternalLinks(True)
            self.txt.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            bl.addWidget(self.txt)
        tm = QLabel(fmt_time(msg["ts"]))
        tm.setStyleSheet("font-size:8pt; color:%s;" % ("#5b7f4a" if mine and st.theme()["name"] == "light" else "#7d93b5"))
        tm.setAlignment(Qt.AlignRight)
        bl.addWidget(tm)
        av = None
        if not mine:
            av = QLabel()
            u = st.users.get(msg["sender"])
            av.setPixmap(avatar_pixmap(u and u.get("avatar"), st.uname(msg["sender"]), 30, dark=st.theme()["name"] == "dark"))
            av.setFixedSize(30, 30)
            av.setAlignment(Qt.AlignTop)
        if mine:
            lay.addStretch(1)
            lay.addWidget(self.bubble)
        else:
            lay.addWidget(av, 0, Qt.AlignTop)
            lay.addWidget(self.bubble)
            lay.addStretch(1)
        self.set_max_width(view.width())

    def set_max_width(self, w):
        mw = max(220, int(w * 0.68))
        self.bubble.setMaximumWidth(mw)
        t = getattr(self, "txt", None)
        if t is not None:
            fm = t.fontMetrics()
            plain = self.msg["text"] or ""
            want = max((fm.horizontalAdvance(l) for l in plain.split("\n")), default=0) + 16
            t.setMinimumWidth(min(want, mw - 26))

    def on_image(self, name, data):
        if not self.img_label:
            return
        if data is None:
            self.img_label.setText("(imagen no disponible)")
            return
        img = QImage()
        img.loadFromData(QByteArray(data))
        self.full = img
        pm = QPixmap.fromImage(img.scaled(340, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img_label.setPixmap(pm)
        self.img_label.setMinimumSize(pm.size())
        self.view.stick_bottom_soon()

    def open_image(self):
        if getattr(self, "full", None) is not None:
            ImageViewer(self.view.win, self.full, self.msg["file"]["name"], self.msg["file"]["id"]).exec()


class ImageViewer(QDialog):
    def __init__(self, win, img, name, fid):
        super().__init__(win)
        self.setWindowTitle(name)
        self.img = img
        lay = QVBoxLayout(self)
        sc = QScrollArea()
        lb = QLabel()
        scr = self.screen().availableGeometry()
        pm = QPixmap.fromImage(img)
        if pm.width() > scr.width() * 0.8 or pm.height() > scr.height() * 0.75:
            pm = pm.scaled(int(scr.width() * 0.8), int(scr.height() * 0.75), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lb.setPixmap(pm)
        sc.setWidget(lb)
        lay.addWidget(sc)
        row = QHBoxLayout()
        row.addStretch(1)
        sv = QPushButton("Guardar como…")
        sv.clicked.connect(lambda: win.download_file(fid, name))
        cl = QPushButton("Cerrar")
        cl.setObjectName("ghost")
        cl.clicked.connect(self.accept)
        row.addWidget(sv)
        row.addWidget(cl)
        lay.addLayout(row)
        self.resize(min(pm.width() + 60, int(scr.width() * 0.85)), min(pm.height() + 110, int(scr.height() * 0.85)))


class SendPreview(QDialog):
    """Vista previa de una imagen (captura, pegada o elegida) con pie de foto opcional."""
    def __init__(self, parent, img, title="Enviar imagen"):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lb = QLabel()
        lb.setAlignment(Qt.AlignCenter)
        lb.setPixmap(QPixmap.fromImage(img.scaled(640, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        lay.addWidget(lb)
        self.cap = QLineEdit()
        self.cap.setPlaceholderText("Añade un comentario (opcional)…")
        lay.addWidget(self.cap)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancelar")
        c.setObjectName("ghost")
        c.clicked.connect(self.reject)
        s = QPushButton("Enviar")
        s.setObjectName("green")
        s.setDefault(True)
        s.clicked.connect(self.accept)
        row.addWidget(c)
        row.addWidget(s)
        lay.addLayout(row)
        self.cap.setFocus()


class Composer(QPlainTextEdit):
    send = Signal()
    imagePasted = Signal(QImage)

    def __init__(self):
        super().__init__()
        self.setObjectName("composer")
        self.setPlaceholderText("Escribe un mensaje…  (Ctrl+V pega capturas)")
        self.setFixedHeight(42)
        self.textChanged.connect(self._grow)

    def _grow(self):
        n = min(5, max(1, self.document().blockCount()))
        self.setFixedHeight(22 + 20 * n)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)

    def canInsertFromMimeData(self, src):
        return src.hasImage() or super().canInsertFromMimeData(src)

    def insertFromMimeData(self, src):
        if src.hasImage():
            img = src.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.imagePasted.emit(img)
                return
        super().insertFromMimeData(src)


class EmojiPopup(QFrame):
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("glass")
        g = QGridLayout(self)
        g.setSpacing(2)
        for i, em in enumerate(EMOJIS):
            b = QToolButton()
            b.setText(em)
            b.clicked.connect(lambda _=False, e=em: (self.picked.emit(e), self.close()))
            g.addWidget(b, i // 8, i % 8)


# ===================================================================== tareas
# Paleta Aero: (arriba, medio, medio2, abajo, borde)
TASK_COLORS = [
    ("#a6e8ff", "#3db9f2", "#1a9de0", "#58cbf7", "#1a86bf"),   # azul cielo
    ("#d3ff9e", "#8fe04c", "#5fc42a", "#97e660", "#4a9f22"),   # verde lima
    ("#ffe0a3", "#ffb03a", "#f28c0f", "#ffc766", "#c26f08"),   # naranja
    ("#ffc2dc", "#f2679f", "#d93f80", "#f58bb8", "#a82d61"),   # rosa
    ("#dcc8ff", "#a57ae8", "#8450d0", "#b592f0", "#5f34a3"),   # violeta
    ("#a8f5ee", "#2fcfc3", "#12aca1", "#55ddd2", "#0d8079"),   # turquesa
]


def aero_grad(c):
    return (f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {c[0]},stop:0.49 {c[1]},"
            f"stop:0.5 {c[2]},stop:1 {c[3]})")


class TaskCard(QFrame):
    """Tarjeta con cabecera de color aero (distinto por tarea) y cuerpo de cristal."""
    def __init__(self, view, t):
        super().__init__()
        self.setObjectName("taskCard")
        st = view.win
        th = st.theme()
        c = TASK_COLORS[t["id"] % len(TASK_COLORS)]
        self.setStyleSheet(f"""
            QFrame#taskCard {{ background: {th['glass2']}; border: 1px solid {c[4]}; border-radius: 14px; }}
            QFrame#taskHead {{ background: {aero_grad(c)}; border: none; border-bottom: 1px solid {c[4]};
                border-top-left-radius: 13px; border-top-right-radius: 13px; }}
            QLabel#taskTitle {{ color: white; font-weight: 700; background: transparent; }}
            QToolButton#taskDel {{ color: white; font-size: 10pt; padding: 1px; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QFrame()
        head.setObjectName("taskHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 7, 6, 7)
        ti = QLabel(("✔ " if t["status"] == "done" else "") + t["title"])
        ti.setObjectName("taskTitle")
        ti.setWordWrap(True)
        hl.addWidget(ti, 1)
        me = st.me
        admin = view.my_role() in ("owner", "admin")
        mine = t["assignee"] == me
        if t["creator"] == me or admin:
            x = QToolButton()
            x.setObjectName("taskDel")
            x.setText("🗑")
            x.setToolTip("Eliminar tarea")
            x.clicked.connect(lambda: st.net.send({"t": "task_update", "id": t["id"], "action": "delete"}))
            hl.addWidget(x, 0, Qt.AlignTop)
        outer.addWidget(head)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 8, 12, 10)
        bl.setSpacing(5)
        if t.get("description"):
            d = QLabel(t["description"])
            d.setWordWrap(True)
            bl.addWidget(d)
        meta = QLabel(f"Creada por {st.display_name(view.chat, t['creator'])}")
        meta.setObjectName("faint")
        bl.addWidget(meta)
        if t["assignee"]:
            row = QHBoxLayout()
            row.setSpacing(6)
            u = st.users.get(t["assignee"], {})
            av = QLabel()
            av.setPixmap(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 22, dark=th["name"] == "dark"))
            row.addWidget(av)
            a = QLabel(st.display_name(view.chat, t["assignee"]))
            a.setStyleSheet("font-weight:600; font-size:9pt;")
            row.addWidget(a)
            row.addStretch(1)
            bl.addLayout(row)
        btns = QHBoxLayout()
        btns.setSpacing(6)

        def btn(text, action, obj):
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setStyleSheet("padding:4px 10px; font-size:9pt; border-radius:10px;")
            b.clicked.connect(lambda: st.net.send({"t": "task_update", "id": t["id"], "action": action}))
            btns.addWidget(b)
        if t["status"] == "pending":
            btn("✋ Aceptar", "accept", "green")
        elif t["status"] == "in_progress":
            if mine or admin:
                btn("✔ Terminar", "done", "green")
                btn("↩ Soltar", "release", "ghost")
        elif mine or admin:
            btn("🔄 Reabrir", "reopen", "ghost")
        btns.addStretch(1)
        if btns.count() > 1:
            bl.addLayout(btns)
        outer.addWidget(body)


class TaskBoard(QWidget):
    # clave, título, color de la cabecera (índice de TASK_COLORS), texto vacío
    COLS = (("pending", "📋 Pendientes", 0, "Nada pendiente. ¡Crea una tarea!"),
            ("in_progress", "🚧 En proceso", 2, "Nadie está trabajando en nada ahora mismo."),
            ("done", "✅ Terminadas", 1, "Aún no hay tareas terminadas."))

    def __init__(self, view):
        super().__init__()
        self.view = view
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(0)
        lb = QLabel("Organizador de tareas")
        lb.setObjectName("h2")
        self.summary = QLabel("")
        self.summary.setObjectName("sub")
        col.addWidget(lb)
        col.addWidget(self.summary)
        top.addLayout(col)
        top.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.setFixedHeight(16)
        self.progress.setRange(0, 100)
        top.addWidget(self.progress)
        nb = QPushButton("＋ Nueva tarea")
        nb.setObjectName("green")
        nb.clicked.connect(self.new_task)
        top.addWidget(nb)
        lay.addLayout(top)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.viewport().setObjectName("viewport")
        host = QWidget()
        host.setObjectName("colHost")
        hb = QHBoxLayout(host)
        hb.setContentsMargins(0, 0, 6, 8)
        hb.setSpacing(14)
        self.bodies, self.heads, self.empties, self.cards = {}, {}, {}, {k: [] for k, *_ in self.COLS}
        for key, title, ci, empty in self.COLS:
            fr = QFrame()
            fr.setObjectName("column")
            fr.setMinimumHeight(170)
            fl = QVBoxLayout(fr)
            fl.setContentsMargins(10, 10, 10, 12)
            fl.setSpacing(10)
            h = QLabel(title)
            h.setAlignment(Qt.AlignCenter)
            c = TASK_COLORS[ci]
            h.setStyleSheet(f"background:{aero_grad(c)}; color:white; font-weight:700; border:1px solid {c[4]};"
                            f"border-radius:13px; padding:6px 10px;")
            fl.addWidget(h)
            body = QVBoxLayout()
            body.setSpacing(10)
            fl.addLayout(body)
            em = QLabel(empty)
            em.setObjectName("faint")
            em.setWordWrap(True)
            em.setAlignment(Qt.AlignCenter)
            fl.addWidget(em)
            fl.addStretch(1)
            hb.addWidget(fr, 1, Qt.AlignTop)     # cada columna solo ocupa lo que necesita
            self.bodies[key], self.heads[key], self.empties[key] = body, (h, title), em
        sc.setWidget(host)
        lay.addWidget(sc, 1)

    def new_task(self):
        d = QDialog(self)
        d.setWindowTitle("Nueva tarea")
        d.setMinimumWidth(380)
        l = QVBoxLayout(d)
        t = QLineEdit()
        t.setPlaceholderText("Título de la tarea")
        t.setMaxLength(120)
        de = QPlainTextEdit()
        de.setObjectName("composer")
        de.setPlaceholderText("Descripción (opcional)")
        de.setFixedHeight(90)
        l.addWidget(t)
        l.addWidget(de)
        r = QHBoxLayout()
        r.addStretch(1)
        c = QPushButton("Cancelar")
        c.setObjectName("ghost")
        c.clicked.connect(d.reject)
        ok = QPushButton("Crear")
        ok.setObjectName("green")
        ok.clicked.connect(d.accept)
        r.addWidget(c)
        r.addWidget(ok)
        l.addLayout(r)
        if d.exec() and t.text().strip():
            self.view.win.net.send({"t": "task_add", "chat": self.view.chat["id"], "title": t.text().strip(),
                                    "desc": de.toPlainText().strip()})

    def set_tasks(self, tasks):
        for key in self.cards:
            for w in self.cards[key]:
                w.setParent(None)
                w.deleteLater()
            self.cards[key] = []
        cnt = {k: 0 for k in self.cards}
        for t in sorted(tasks, key=lambda x: (-x["updated"] if x["status"] == "done" else x["id"])):
            if t["status"] in self.bodies:
                card = TaskCard(self.view, t)
                self.bodies[t["status"]].addWidget(card)
                self.cards[t["status"]].append(card)
                cnt[t["status"]] += 1
        for k, (h, title) in self.heads.items():
            h.setText(f"{title}   ·   {cnt[k]}")
            self.empties[k].setVisible(cnt[k] == 0)
        total = len(tasks)
        self.progress.setValue(int(100 * cnt["done"] / total) if total else 0)
        self.progress.setFormat("%p%")
        self.summary.setText(f"{cnt['done']} de {total} completadas" if total else "Todavía no hay tareas")


# ================================================================ diálogos
def clear_layout(l):
    while l.count():
        it = l.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            clear_layout(it.layout())


def form_dialog(parent, title, fields, ok="Aceptar", hint=None):
    """fields: [(etiqueta, es_password)] -> lista de textos o None."""
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setMinimumWidth(360)
    l = QVBoxLayout(d)
    l.setSpacing(8)
    if hint:
        h = QLabel(hint)
        h.setObjectName("sub")
        h.setWordWrap(True)
        l.addWidget(h)
    edits = []
    for label, pw in fields:
        l.addWidget(QLabel(label))
        e = QLineEdit()
        if pw:
            e.setEchoMode(QLineEdit.Password)
        l.addWidget(e)
        edits.append(e)
    r = QHBoxLayout()
    r.addStretch(1)
    c = QPushButton("Cancelar")
    c.setObjectName("ghost")
    c.clicked.connect(d.reject)
    o = QPushButton(ok)
    o.setObjectName("green")
    o.setDefault(True)
    o.clicked.connect(d.accept)
    r.addWidget(c)
    r.addWidget(o)
    l.addLayout(r)
    edits[0].setFocus()
    return [e.text() for e in edits] if d.exec() else None


class AvatarPicker(QWidget):
    """Círculo con foto + botones Elegir/Quitar."""
    def __init__(self, name="?", current=None, size=72, editable=True):
        super().__init__()
        self.b64, self.name, self.size_px, self.changed = current, name, size, False
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        self.lb = QLabel()
        self.lb.setFixedSize(size, size)
        l.addWidget(self.lb)
        if editable:
            col = QVBoxLayout()
            b = QPushButton("Elegir foto…")
            b.setObjectName("ghost")
            b.clicked.connect(self.choose)
            x = QPushButton("Quitar")
            x.setObjectName("link")
            x.clicked.connect(self.clear)
            col.addWidget(b)
            col.addWidget(x, 0, Qt.AlignLeft)
            l.addLayout(col)
        l.addStretch(1)
        self.redraw()

    def redraw(self):
        self.lb.setPixmap(avatar_pixmap(self.b64, self.name, self.size_px))

    def choose(self):
        b = pick_avatar(self)
        if b:
            self.b64, self.changed = b, True
            self.redraw()

    def clear(self):
        self.b64, self.changed = None, True
        self.redraw()


class AddContactDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Añadir contacto")
        self.setMinimumWidth(400)
        l = QVBoxLayout(self)
        t = QLabel("Añadir contacto")
        t.setObjectName("title")
        l.addWidget(t)
        h = QLabel("Escribe su nombre de usuario o la IP desde la que está conectado. "
                   "Cuando acepte tu petición podréis chatear.")
        h.setObjectName("sub")
        h.setWordWrap(True)
        l.addWidget(h)
        self.e = QLineEdit()
        self.e.setPlaceholderText("usuario  o  192.168.11.x")
        l.addWidget(self.e)
        r = QHBoxLayout()
        r.addStretch(1)
        c = QPushButton("Cerrar")
        c.setObjectName("ghost")
        c.clicked.connect(self.reject)
        s = QPushButton("Enviar petición")
        s.setObjectName("green")
        s.setDefault(True)
        s.clicked.connect(self.go)
        r.addWidget(c)
        r.addWidget(s)
        l.addLayout(r)

    def go(self):
        v = self.e.text().strip()
        if v:
            self.win.net.send({"t": "friend_request", "target": v})
            self.e.clear()
            self.accept()


class RequestsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Peticiones de chat")
        self.setMinimumSize(420, 380)
        self.body = QVBoxLayout(self)
        self.refresh()

    def refresh(self):
        w = self.win
        clear_layout(self.body)
        t = QLabel("Peticiones de chat")
        t.setObjectName("title")
        self.body.addWidget(t)
        self.body.addWidget(QLabel("Recibidas"))
        if not w.req_in:
            e = QLabel("No tienes peticiones pendientes.")
            e.setObjectName("sub")
            self.body.addWidget(e)
        for r in w.req_in:
            self.body.addWidget(self.row(r["user"], [("Aceptar", "green", lambda _=False, i=r["id"]: self.answer(i, True)),
                                                     ("Rechazar", "ghost", lambda _=False, i=r["id"]: self.answer(i, False))]))
        self.body.addWidget(QLabel("Enviadas"))
        if not w.req_out:
            e = QLabel("No hay peticiones enviadas.")
            e.setObjectName("sub")
            self.body.addWidget(e)
        for r in w.req_out:
            self.body.addWidget(self.row(r["user"], [("Cancelar", "ghost", lambda _=False, i=r["id"]: w.net.send({"t": "friend_cancel", "id": i}))]))
        self.body.addStretch(1)
        b = QPushButton("Cerrar")
        b.setObjectName("ghost")
        b.clicked.connect(self.accept)
        self.body.addWidget(b, 0, Qt.AlignRight)

    def answer(self, rid, ok):
        self.win.net.send({"t": "friend_respond", "id": rid, "accept": ok})

    def row(self, uid, buttons):
        f = QFrame()
        f.setObjectName("card")
        l = QHBoxLayout(f)
        a = QLabel()
        u = self.win.users.get(uid, {})
        a.setPixmap(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 36))
        l.addWidget(a)
        l.addWidget(QLabel(u.get("username", "?")), 1)
        for text, obj, fn in buttons:
            b = QPushButton(text)
            b.setObjectName(obj)
            b.setStyleSheet("padding:4px 12px;")
            b.clicked.connect(fn)
            l.addWidget(b)
        return f


class BlockedDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Usuarios bloqueados")
        self.setMinimumSize(380, 320)
        self.body = QVBoxLayout(self)
        self.refresh()

    def refresh(self):
        clear_layout(self.body)
        t = QLabel("Usuarios bloqueados")
        t.setObjectName("title")
        self.body.addWidget(t)
        if not self.win.blocked:
            e = QLabel("No has bloqueado a nadie.")
            e.setObjectName("sub")
            self.body.addWidget(e)
        for uid in self.win.blocked:
            f = QFrame()
            f.setObjectName("card")
            l = QHBoxLayout(f)
            a = QLabel()
            u = self.win.users.get(uid, {})
            a.setPixmap(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 34))
            l.addWidget(a)
            l.addWidget(QLabel(u.get("username", "?")), 1)
            b = QPushButton("Desbloquear")
            b.setObjectName("ghost")
            b.clicked.connect(lambda _=False, i=uid: self.win.net.send({"t": "unblock", "user": i}))
            l.addWidget(b)
            self.body.addWidget(f)
        self.body.addStretch(1)
        c = QPushButton("Cerrar")
        c.setObjectName("ghost")
        c.clicked.connect(self.accept)
        self.body.addWidget(c, 0, Qt.AlignRight)


def friend_checklist(win, exclude=()):
    """Devuelve (widget, lista de (uid, checkbox)) con tus contactos."""
    host = QWidget()
    host.setObjectName("colHost")
    l = QVBoxLayout(host)
    l.setContentsMargins(0, 0, 0, 0)
    boxes = []
    for f in win.friends:
        if f["id"] in exclude:
            continue
        cb = QCheckBox(win.uname(f["id"]))
        l.addWidget(cb)
        boxes.append((f["id"], cb))
    if not boxes:
        e = QLabel("No tienes contactos disponibles.")
        e.setObjectName("sub")
        l.addWidget(e)
    l.addStretch(1)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(host)
    sc.viewport().setObjectName("viewport")
    sc.setFixedHeight(130)
    return sc, boxes


class CreateGroupDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Crear grupo")
        self.setMinimumWidth(420)
        l = QVBoxLayout(self)
        t = QLabel("Nuevo grupo")
        t.setObjectName("title")
        l.addWidget(t)
        self.av = AvatarPicker("G", None, 64)
        l.addWidget(self.av)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nombre del grupo")
        self.name.setMaxLength(40)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("Descripción (opcional)")
        self.desc.setMaxLength(300)
        l.addWidget(self.name)
        l.addWidget(self.desc)
        l.addWidget(QLabel("Invitar contactos:"))
        sc, self.boxes = friend_checklist(win)
        l.addWidget(sc)
        r = QHBoxLayout()
        r.addStretch(1)
        c = QPushButton("Cancelar")
        c.setObjectName("ghost")
        c.clicked.connect(self.reject)
        o = QPushButton("Crear grupo")
        o.setObjectName("green")
        o.setDefault(True)
        o.clicked.connect(self.go)
        r.addWidget(c)
        r.addWidget(o)
        l.addLayout(r)

    def go(self):
        if not self.name.text().strip():
            self.name.setFocus()
            return
        m = {"t": "create_group", "name": self.name.text().strip(), "desc": self.desc.text().strip(),
             "members": [u for u, cb in self.boxes if cb.isChecked()]}
        if self.av.b64:
            m["avatar"] = self.av.b64
        self.win.net.send(m)
        self.accept()


class ChatSettingsDialog(QDialog):
    def __init__(self, win, cid):
        super().__init__(win)
        self.win, self.cid = win, cid
        self.setMinimumWidth(460)
        self.body = QVBoxLayout(self)
        self.body.setSpacing(8)
        self.refresh()

    # -- utilidades
    def chat(self):
        return self.win.chats.get(self.cid)

    def refresh(self):
        c = self.chat()
        if not c:
            self.reject()
            return
        clear_layout(self.body)
        if c["type"] == "dm":
            self.build_dm(c)
        else:
            self.build_group(c)

    def my_role(self):
        for m in self.chat()["members"]:
            if m["id"] == self.win.me:
                return m["role"]
        return None

    # -- DM
    def build_dm(self, c):
        w = self.win
        uid = w.other_user(c)
        u = w.users.get(uid, {})
        self.setWindowTitle("Ajustes del chat")
        big = QLabel()
        big.setPixmap(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 200, u.get("online", False), w.theme()["name"] == "dark"))
        big.setAlignment(Qt.AlignCenter)
        self.body.addWidget(big)
        n = QLabel(u.get("username", "?"))
        n.setObjectName("title")
        n.setAlignment(Qt.AlignCenter)
        self.body.addWidget(n)
        st = QLabel("En línea" if u.get("online") else "Desconectado")
        st.setObjectName("sub")
        st.setAlignment(Qt.AlignCenter)
        self.body.addWidget(st)
        row = QHBoxLayout()
        e = QLineEdit(u.get("username", ""))
        e.setReadOnly(True)
        e.setAlignment(Qt.AlignCenter)
        cp = QPushButton("Copiar usuario")
        cp.setObjectName("ghost")
        cp.clicked.connect(lambda: (QGuiApplication.clipboard().setText(u.get("username", "")), w.toast("Usuario copiado")))
        row.addWidget(e, 1)
        row.addWidget(cp)
        self.body.addLayout(row)
        blocked = uid in w.blocked
        b = QPushButton("Desbloquear usuario" if blocked else "Bloquear usuario")
        b.setObjectName("ghost" if blocked else "danger")
        b.clicked.connect(lambda: w.net.send({"t": "unblock" if blocked else "block", "user": uid}))
        self.body.addWidget(b)
        r = QPushButton("Eliminar de contactos")
        r.setObjectName("danger")
        r.clicked.connect(lambda: self.confirm_remove(uid, u.get("username", "?")))
        self.body.addWidget(r)
        cl = QPushButton("Cerrar")
        cl.setObjectName("ghost")
        cl.clicked.connect(self.accept)
        self.body.addWidget(cl)

    def confirm_remove(self, uid, name):
        if QMessageBox.question(self, APP_NAME, f"¿Eliminar a {name} de tus contactos?\n"
                                "Se borrará también esta conversación para los dos.") == QMessageBox.Yes:
            self.win.net.send({"t": "remove_friend", "user": uid})
            self.accept()

    # -- grupo
    def build_group(self, c):
        w = self.win
        role = self.my_role()
        admin = role in ("owner", "admin")
        self.setWindowTitle("Ajustes del grupo")
        t = QLabel("Ajustes del grupo")
        t.setObjectName("title")
        self.body.addWidget(t)
        self.av = AvatarPicker(c["name"], c.get("avatar"), 72, editable=admin)
        self.body.addWidget(self.av)
        self.name = QLineEdit(c["name"] or "")
        self.name.setMaxLength(40)
        self.name.setEnabled(admin)
        self.desc = QLineEdit(c.get("description") or "")
        self.desc.setMaxLength(300)
        self.desc.setPlaceholderText("Sin descripción")
        self.desc.setEnabled(admin)
        self.body.addWidget(self.name)
        self.body.addWidget(self.desc)
        if admin:
            s = QPushButton("Guardar cambios")
            s.setObjectName("green")
            s.clicked.connect(self.save)
            self.body.addWidget(s, 0, Qt.AlignRight)
        hr = QHBoxLayout()
        hl = QLabel(f"Miembros ({len(c['members'])})")
        hl.setObjectName("h2")
        hr.addWidget(hl)
        hr.addStretch(1)
        if admin:
            inv = QPushButton("＋ Invitar")
            inv.setObjectName("ghost")
            inv.clicked.connect(self.invite)
            hr.addWidget(inv)
        self.body.addLayout(hr)
        host = QWidget()
        host.setObjectName("colHost")
        ml = QVBoxLayout(host)
        ml.setContentsMargins(0, 0, 4, 0)
        ml.setSpacing(5)
        order = {"owner": 0, "admin": 1, "member": 2}
        for m in sorted(c["members"], key=lambda x: (order.get(x["role"], 3), w.uname(x["id"]).lower())):
            ml.addWidget(self.member_row(c, m, role))
        ml.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(host)
        sc.viewport().setObjectName("viewport")
        sc.setMinimumHeight(210)
        self.body.addWidget(sc, 1)
        fr = QHBoxLayout()
        if role != "owner":
            lv = QPushButton("Salir del grupo")
            lv.setObjectName("danger")
            lv.clicked.connect(self.leave)
            fr.addWidget(lv)
        if admin:
            dl = QPushButton("Eliminar grupo")
            dl.setObjectName("danger")
            dl.clicked.connect(self.delete)
            fr.addWidget(dl)
        fr.addStretch(1)
        cl = QPushButton("Cerrar")
        cl.setObjectName("ghost")
        cl.clicked.connect(self.accept)
        fr.addWidget(cl)
        self.body.addLayout(fr)

    def member_row(self, c, m, my_role):
        w = self.win
        uid = m["id"]
        u = w.users.get(uid, {})
        f = QFrame()
        f.setObjectName("card")
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 6, 10, 6)
        a = QLabel()
        a.setPixmap(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 36, u.get("online", False), w.theme()["name"] == "dark"))
        l.addWidget(a)
        col = QVBoxLayout()
        col.setSpacing(0)
        nm = QLabel(m["nick"] or u.get("username", "?"))
        nm.setStyleSheet("font-weight:600;")
        col.addWidget(nm)
        if m["nick"]:
            s = QLabel(u.get("username", "?") + (" (tú)" if uid == w.me else ""))
            s.setObjectName("faint")
            col.addWidget(s)
        elif uid == w.me:
            s = QLabel("tú")
            s.setObjectName("faint")
            col.addWidget(s)
        l.addLayout(col, 1)
        if m["role"] != "member":
            tag = QLabel("👑 Principal" if m["role"] == "owner" else "⭐ Admin")
            tag.setObjectName("roleTag")
            l.addWidget(tag)
        menu = QMenu(self)
        n = 0
        if uid == w.me:
            menu.addAction("Cambiar mi mote…", lambda: self.nick(uid, m["nick"]))
            n += 1
        elif my_role in ("owner", "admin"):
            menu.addAction("Poner mote…", lambda: self.nick(uid, m["nick"]))
            n += 1
            if my_role == "owner" and m["role"] != "owner":
                if m["role"] == "admin":
                    menu.addAction("Quitar administrador", lambda: w.net.send({"t": "set_role", "chat": self.cid, "user": uid, "role": "member"}))
                else:
                    menu.addAction("Hacer administrador", lambda: w.net.send({"t": "set_role", "chat": self.cid, "user": uid, "role": "admin"}))
                n += 1
            if m["role"] != "owner" and (m["role"] == "member" or my_role == "owner"):
                menu.addSeparator()
                menu.addAction("Echar del grupo", lambda: self.kick(uid))
                n += 1
        if n:
            b = QToolButton()
            b.setText("⋯")
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
            l.addWidget(b)
        return f

    # -- acciones
    def save(self):
        m = {"t": "edit_group", "chat": self.cid, "name": self.name.text().strip(), "desc": self.desc.text().strip()}
        if self.av.changed:
            m["avatar"] = self.av.b64
        if not m["name"]:
            self.win.toast("El nombre no puede estar vacío.", True)
            return
        self.win.net.send(m)
        self.win.toast("Cambios guardados")

    def nick(self, uid, cur):
        text, ok = QInputDialog.getText(self, "Mote", "Mote (vacío para quitarlo):", QLineEdit.Normal, cur or "")
        if ok:
            self.win.net.send({"t": "set_nick", "chat": self.cid, "user": uid, "nick": text})

    def kick(self, uid):
        if QMessageBox.question(self, APP_NAME, f"¿Echar a {self.win.uname(uid)} del grupo?") == QMessageBox.Yes:
            self.win.net.send({"t": "kick", "chat": self.cid, "user": uid})

    def invite(self):
        c = self.chat()
        d = QDialog(self)
        d.setWindowTitle("Invitar al grupo")
        d.setMinimumWidth(340)
        l = QVBoxLayout(d)
        l.addWidget(QLabel("Solo puedes invitar a tus contactos:"))
        sc, boxes = friend_checklist(self.win, exclude={m["id"] for m in c["members"]})
        l.addWidget(sc)
        r = QHBoxLayout()
        r.addStretch(1)
        cc = QPushButton("Cancelar")
        cc.setObjectName("ghost")
        cc.clicked.connect(d.reject)
        ok = QPushButton("Invitar")
        ok.setObjectName("green")
        ok.clicked.connect(d.accept)
        r.addWidget(cc)
        r.addWidget(ok)
        l.addLayout(r)
        if d.exec():
            for uid, cb in boxes:
                if cb.isChecked():
                    self.win.net.send({"t": "invite", "chat": self.cid, "user": uid})

    def leave(self):
        if QMessageBox.question(self, APP_NAME, "¿Salir de este grupo?") == QMessageBox.Yes:
            self.win.net.send({"t": "leave_group", "chat": self.cid})
            self.accept()

    def delete(self):
        if QMessageBox.question(self, APP_NAME, "¿Eliminar el grupo para todos?\nSe borrarán sus mensajes y archivos.") == QMessageBox.Yes:
            self.win.net.send({"t": "delete_group", "chat": self.cid})
            self.accept()


# ================================================================ vista de chat
class ChatView(QFrame):
    def __init__(self, win, chat):
        super().__init__()
        self.setObjectName("glass")
        self.win, self.chat = win, chat
        self.loaded, self.ids, self.last_day, self.rows = False, set(), None, []
        self.tasks = []
        self.detached = None
        self.requested = False
        self.msgs = []
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # cabecera
        self.header = QFrame()
        self.header.setObjectName("header")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(14, 8, 10, 8)
        self.av = QLabel()
        hl.addWidget(self.av)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("h2")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("sub")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        hl.addLayout(col, 1)
        self.b_chat = QPushButton("💬 Chat")
        self.b_tasks = QPushButton("📋 Tareas")
        grp = QButtonGroup(self)
        for i, b in enumerate((self.b_chat, self.b_tasks)):
            b.setObjectName("seg")
            b.setCheckable(True)
            grp.addButton(b, i)
            hl.addWidget(b)
        self.b_chat.setChecked(True)
        grp.idClicked.connect(lambda i: self.pages.setCurrentIndex(i))
        self.b_detach = QToolButton()
        self.b_detach.setText("⧉")
        self.b_detach.clicked.connect(lambda: win.toggle_detach(self.chat["id"]))
        self.b_set = QToolButton()
        self.b_set.setText("⚙")
        self.b_set.setToolTip("Ajustes del chat")
        self.b_set.clicked.connect(lambda: win.open_chat_settings(self.chat["id"]))
        hl.addWidget(self.b_detach)
        hl.addWidget(self.b_set)
        lay.addWidget(self.header)
        # páginas
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.viewport().setObjectName("viewport")
        self.host = QWidget()
        self.host.setObjectName("msgHost")
        self.ml = QVBoxLayout(self.host)
        self.ml.setContentsMargins(0, 8, 0, 8)
        self.ml.setSpacing(2)
        self.ml.addStretch(1)
        self.scroll.setWidget(self.host)
        pl.addWidget(self.scroll, 1)
        self.blocked_lbl = QLabel("Has bloqueado a este usuario. Desbloquéalo desde ⚙ para escribirle.")
        self.blocked_lbl.setObjectName("sysmsg")
        self.blocked_lbl.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.blocked_lbl)
        bar = QFrame()
        bar.setObjectName("inputbar")
        self.bar = bar
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(4)
        for txt, tip, fn in (("📎", "Adjuntar archivo", self.pick_file), ("📷", "Capturar la pantalla", self.capture),
                             ("😊", "Emojis", self.emoji)):
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bl.addWidget(b, 0, Qt.AlignBottom)
            if txt == "😊":
                self.b_emoji = b
        self.input = Composer()
        self.input.send.connect(self.send_text)
        self.input.imagePasted.connect(self.image_flow)
        bl.addWidget(self.input, 1)
        sb = QPushButton("Enviar")
        sb.setObjectName("green")
        sb.clicked.connect(self.send_text)
        bl.addWidget(sb, 0, Qt.AlignBottom)
        pl.addWidget(bar)
        self.pages.addWidget(page)
        self.board = TaskBoard(self)
        self.pages.addWidget(self.board)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self._stick = True
        self.scroll.verticalScrollBar().valueChanged.connect(self._val_changed)
        self.update_header()

    # -- estado
    def my_role(self):
        for m in self.chat["members"]:
            if m["id"] == self.win.me:
                return m["role"]

    def update_chat(self, chat):
        self.chat = chat
        self.update_header()
        self.board.set_tasks(self.tasks)
        self.update_tab_badge()

    def update_header(self):
        w, c = self.win, self.chat
        name, av = w.chat_title(c), w.chat_avatar(c)
        self.title.setText(name)
        online = None
        if c["type"] == "dm":
            u = w.users.get(w.other_user(c), {})
            online = bool(u.get("online"))
            self.subtitle.setText("● En línea" if online else "Desconectado")
            self.blocked_lbl.setVisible(w.other_user(c) in w.blocked)
            self.bar.setVisible(w.other_user(c) not in w.blocked)
        else:
            on = sum(1 for m in c["members"] if w.users.get(m["id"], {}).get("online"))
            d = c.get("description")
            nm = len(c["members"])
            self.subtitle.setText(f"{nm} miembro{'s' if nm != 1 else ''} · {on} en línea" + (f" — {d}" if d else ""))
            self.blocked_lbl.setVisible(False)
            self.bar.setVisible(True)
        self.av.setPixmap(avatar_pixmap(av, name, 42, online, w.theme()["name"] == "dark"))
        self.b_detach.setText("⤓" if self.detached else "⧉")
        self.b_detach.setToolTip("Volver a unir a MyTE" if self.detached else "Sacar el chat a otra ventana")
        if self.detached:
            self.detached.setWindowTitle(f"{name} — MyTE")

    def update_tab_badge(self):
        n = sum(1 for t in self.tasks if t["status"] != "done")
        self.b_tasks.setText("📋 Tareas" + (f" ({n})" if n else ""))
        self.b_tasks.setMinimumWidth(self.b_tasks.fontMetrics().horizontalAdvance(self.b_tasks.text()) + 52)

    # -- mensajes
    def set_messages(self, msgs, tasks):
        self.loaded = True
        self.msgs = list(msgs)
        clear_layout(self.ml)
        self.ml.addStretch(1)
        self.rows, self.ids, self.last_day = [], set(), None
        for m in msgs:
            self._add(m)
        self.set_tasks(tasks)
        self._stick = True
        self.stick_bottom_soon()

    def set_tasks(self, tasks):
        self.tasks = tasks
        self.board.set_tasks(tasks)
        self.update_tab_badge()

    def add_message(self, m):
        if not self.loaded or m["id"] in self.ids:
            return
        self.msgs.append(m)
        self._add(m)
        self.stick_bottom_soon()

    def _add(self, m):
        if m["id"] in self.ids:
            return
        self.ids.add(m["id"])
        day = datetime.fromtimestamp(m["ts"]).date()
        if day != self.last_day:
            self.last_day = day
            d = QLabel(day_label(m["ts"]))
            d.setObjectName("daymsg")
            d.setAlignment(Qt.AlignCenter)
            self.ml.insertWidget(self.ml.count() - 1, d)
        if m["kind"] == "system":
            l = QLabel(m["text"])
            l.setObjectName("sysmsg")
            l.setWordWrap(True)
            l.setAlignment(Qt.AlignCenter)
            wrap = QHBoxLayout()
            wrap.addStretch(1)
            wrap.addWidget(l)
            wrap.addStretch(1)
            hw = QWidget()
            hw.setLayout(wrap)
            self.ml.insertWidget(self.ml.count() - 1, hw)
            return
        row = MessageRow(self, m)
        self.rows.append(row)
        self.ml.insertWidget(self.ml.count() - 1, row)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for r in self.rows:
            r.set_max_width(self.width())

    # -- scroll
    def _val_changed(self, v):
        sb = self.scroll.verticalScrollBar()
        self._stick = v >= sb.maximum() - 30

    def _range_changed(self, a, b):
        if self._stick:
            self.scroll.verticalScrollBar().setValue(b)

    def stick_bottom_soon(self):
        if self._stick:
            QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    # -- envío
    def send_text(self):
        t = self.input.toPlainText()
        if not t.strip():
            return
        if self.win.net.send({"t": "send_msg", "chat": self.chat["id"], "kind": "text", "text": t}):
            self.input.clear()
            self._stick = True

    def send_bytes(self, kind, name, raw, caption=""):
        lim = self.win.limits.get("max_file_mb", 25)
        if len(raw) > lim * 1024 * 1024:
            self.win.toast(f"El archivo supera el límite del servidor ({lim} MB).", True)
            return
        self.win.toast("Enviando…")
        self._stick = True
        self.win.net.send({"t": "send_msg", "chat": self.chat["id"], "kind": kind, "name": name,
                           "data": base64.b64encode(raw).decode(), "text": caption})

    def image_flow(self, img, name=None):
        d = SendPreview(self.window(), img)
        if d.exec():
            self.send_bytes("image", name or f"captura_{datetime.now():%Y%m%d_%H%M%S}.png",
                            qimage_to_png_bytes(img), d.cap.text().strip())

    def send_path(self, fn):
        try:
            with open(fn, "rb") as f:
                raw = f.read()
        except OSError as e:
            self.win.toast(f"No se pudo leer el archivo: {e}", True)
            return
        if fn.lower().endswith(IMG_EXT):
            img = QImage(fn)
            if not img.isNull():
                d = SendPreview(self.window(), img)
                if d.exec():
                    self.send_bytes("image", os.path.basename(fn), raw, d.cap.text().strip())
                return
        self.send_bytes("file", os.path.basename(fn), raw)

    def pick_file(self):
        fns, _ = QFileDialog.getOpenFileNames(self.window(), "Adjuntar archivos")
        for fn in fns:
            self.send_path(fn)

    def capture(self):
        self.win.capture_screen(self)

    def emoji(self):
        p = EmojiPopup(self)
        p.picked.connect(lambda e: self.input.insertPlainText(e))
        p.move(self.b_emoji.mapToGlobal(QPoint(0, -p.sizeHint().height() - 6)))
        p.show()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.send_path(u.toLocalFile())


class DetachedWindow(QWidget):
    def __init__(self, win, view):
        super().__init__()
        self.setObjectName("detached")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.win, self.view = win, view
        self.setWindowIcon(win.windowIcon())
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8, 8, 8, 8)
        self.lay.addWidget(view)
        view.show()          # al cambiar de padre Qt lo oculta
        self.resize(560, 700)
        self.reattaching = False

    def closeEvent(self, e):
        if not self.reattaching:
            e.ignore()
            self.win.toggle_detach(self.view.chat["id"])
        else:
            e.accept()


# ============================================================ lista de chats
class ChatItem(QWidget):
    def __init__(self, win, chat, unread):
        super().__init__()
        l = QHBoxLayout(self)
        l.setContentsMargins(10, 7, 10, 7)
        l.setSpacing(10)
        name, av = win.chat_title(chat), win.chat_avatar(chat)
        online = None
        if chat["type"] == "dm":
            online = bool(win.users.get(win.other_user(chat), {}).get("online"))
        a = QLabel()
        a.setPixmap(avatar_pixmap(av, name, 46, online, win.theme()["name"] == "dark"))
        l.addWidget(a)
        col = QVBoxLayout()
        col.setSpacing(1)
        top = QHBoxLayout()
        n = QLabel(("👥 " if chat["type"] == "group" else "") + name)
        n.setStyleSheet("font-weight:700;")
        top.addWidget(n, 1)
        last = chat.get("last")
        if last:
            tm = QLabel(fmt_time(last["ts"]))
            tm.setObjectName("faint")
            top.addWidget(tm)
        col.addLayout(top)
        bot = QHBoxLayout()
        pv = QLabel(self.preview(win, chat))
        pv.setObjectName("sub")
        bot.addWidget(pv, 1)
        if unread:
            b = QLabel(str(unread if unread < 100 else "99+"))
            b.setObjectName("badge")
            b.setAlignment(Qt.AlignCenter)
            b.setMinimumWidth(18)
            bot.addWidget(b)
        col.addLayout(bot)
        l.addLayout(col, 1)
        self.setMinimumHeight(60)

    @staticmethod
    def preview(win, chat):
        last = chat.get("last")
        if not last:
            return "Sin mensajes todavía"
        k = last["kind"]
        if k == "image":
            t = "📷 Imagen"
        elif k == "file":
            t = "📎 " + (last.get("text") or "Archivo")
        else:
            t = (last.get("text") or "").replace("\n", " ")
        if k != "system" and last["sender"] == win.me:
            t = "Tú: " + t
        elif k != "system" and chat["type"] == "group":
            t = win.display_name(chat, last["sender"]) + ": " + t
        return t if len(t) < 34 else t[:33] + "…"


# =================================================================== login
class LoginWindow(QWidget):
    logged = Signal()

    def __init__(self, app_ctl):
        super().__init__()
        self.ctl = app_ctl
        self.net = app_ctl.net
        self.setWindowTitle("MyTE — Iniciar sesión")
        self.setObjectName("detached")
        self.setFixedWidth(470)
        self.bg_theme = "light"
        s = app_ctl.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        logo = QLabel("MyTE")
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignCenter)
        outer.addWidget(logo)
        sub = QLabel("Chatea, comparte capturas y organiza tareas con tu equipo")
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        outer.addWidget(sub)
        card = QFrame()
        card.setObjectName("glass")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 18, 22, 18)
        cl.setSpacing(7)
        tabs = QHBoxLayout()
        self.t_login, self.t_reg = QPushButton("Iniciar sesión"), QPushButton("Crear cuenta")
        g = QButtonGroup(self)
        for i, b in enumerate((self.t_login, self.t_reg)):
            b.setObjectName("seg")
            b.setCheckable(True)
            g.addButton(b, i)
            tabs.addWidget(b)
        self.t_login.setChecked(True)
        g.idClicked.connect(self.set_mode)
        cl.addLayout(tabs)
        row = QHBoxLayout()
        self.host = QLineEdit(s.value("host", "127.0.0.1"))
        self.host.setPlaceholderText("IP del servidor")
        self.port = QLineEdit(str(s.value("port", "5050")))
        self.port.setFixedWidth(80)
        row.addWidget(self.host, 1)
        row.addWidget(self.port)
        cl.addWidget(QLabel("Servidor  (IP y puerto)"))
        cl.addLayout(row)
        self.user = QLineEdit(s.value("user", ""))
        self.user.setPlaceholderText("Usuario")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Contraseña")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.pw2.setPlaceholderText("Repite la contraseña")
        cl.addWidget(QLabel("Cuenta"))
        cl.addWidget(self.user)
        cl.addWidget(self.pw)
        cl.addWidget(self.pw2)
        self.av_lbl = QLabel("Foto de perfil (opcional)")
        self.avp = AvatarPicker("?", None, 56)
        cl.addWidget(self.av_lbl)
        cl.addWidget(self.avp)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#e2483d; font-weight:600;")
        cl.addWidget(self.status)
        self.go = QPushButton("Conectar")
        self.go.setObjectName("green")
        self.go.setDefault(True)
        self.go.clicked.connect(self.submit)
        cl.addWidget(self.go)
        outer.addWidget(card)
        for e in (self.host, self.port, self.user, self.pw, self.pw2):
            e.returnPressed.connect(self.submit)
        self.set_mode(0)
        self.mode = 0

    def set_mode(self, i):
        self.mode = i
        for w in (self.pw2, self.av_lbl, self.avp):
            w.setVisible(i == 1)
        self.go.setText("Crear cuenta y entrar" if i else "Conectar")
        self.status.setText("")
        self.adjustSize()

    def set_busy(self, b):
        self.go.setEnabled(not b)

    def closeEvent(self, e):
        self.ctl.quit()
        e.accept()

    def error(self, text):
        self.status.setText(text)
        self.set_busy(False)

    def submit(self):
        host, user, pw = self.host.text().strip(), self.user.text().strip(), self.pw.text()
        try:
            port = int(self.port.text())
        except ValueError:
            return self.error("El puerto debe ser un número.")
        if not host or not user or not pw:
            return self.error("Rellena servidor, usuario y contraseña.")
        if self.mode == 1 and pw != self.pw2.text():
            return self.error("Las contraseñas no coinciden.")
        self.status.setText("")
        self.set_busy(True)
        av = self.avp.b64 if self.mode == 1 else None
        self.ctl.start_login(host, port, user, pw, register=self.mode == 1, avatar=av)

    def paintEvent(self, e):
        p = QPainter(self)
        t = THEMES[self.ctl.theme_name]
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(t["bg1"]))
        g.setColorAt(0.6, QColor(t["bg2"]))
        g.setColorAt(1, QColor(t["bg3"]))
        p.fillRect(self.rect(), g)
        p.end()


# ============================================================ ventana principal
class FabButton(QPushButton):
    """Botón redondo flotante con el «+» dibujado y centrado a mano."""
    def __init__(self):
        super().__init__("")
        self.setObjectName("fab")
        self.plus = QColor("#123008")

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy, r = self.width() / 2, self.height() / 2, 10.5
        for col, w, off in ((QColor(255, 255, 255, 110), 6.5, 0.8), (self.plus, 4.2, 0)):
            pen = QPen(col, w)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QRectF(cx - r, cy + off, 0, 0).topLeft(), QRectF(cx + r, cy + off, 0, 0).topLeft())
            p.drawLine(QRectF(cx, cy - r + off, 0, 0).topLeft(), QRectF(cx, cy + r + off, 0, 0).topLeft())
        p.end()


class Sidebar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("glass")
        self.fab = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fab:
            self.fab.move(16, self.height() - self.fab.height() - 16)
            self.fab.raise_()


class MainWindow(QMainWindow):
    def __init__(self, ctl):
        super().__init__()
        self.ctl, self.net = ctl, ctl.net
        self.setWindowTitle("MyTE")
        self.resize(1180, 760)
        self.setMinimumSize(820, 520)
        self.me, self.users, self.chats = None, {}, {}
        self.friends, self.req_in, self.req_out, self.blocked, self.limits = [], [], [], [], {}
        self.views, self.unread, self.current = {}, {}, None
        self.file_cache, self.file_waiters = {}, {}
        self.first_sync = True
        self.dlg_settings = self.dlg_req = self.dlg_blocked = None
        self.bg = AeroBackground()
        self.setCentralWidget(self.bg)
        root = QVBoxLayout(self.bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.hide()
        root.addWidget(self.banner)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 12, 12)
        body.setSpacing(12)
        root.addLayout(body, 1)
        # --- raíl (izquierda del todo)
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(74)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 14, 0, 14)
        rl.setSpacing(10)
        self.avatar_btn = QToolButton()
        self.avatar_btn.setObjectName("avatarBtn")
        self.avatar_btn.setIconSize(QSize(50, 50))
        self.avatar_btn.setPopupMode(QToolButton.InstantPopup)
        self.avatar_btn.setToolTip("Tu perfil y ajustes")
        self.avatar_btn.setMenu(QMenu(self))
        self.avatar_btn.menu().aboutToShow.connect(self.build_profile_menu)
        rl.addWidget(self.avatar_btn, 0, Qt.AlignHCenter)
        self.theme_btn = QToolButton()
        self.theme_btn.clicked.connect(ctl.toggle_theme)
        rl.addWidget(self.theme_btn, 0, Qt.AlignHCenter)
        rl.addStretch(1)
        self.bell = QToolButton()
        self.bell.setText("🔔")
        self.bell.setToolTip("Peticiones de chat")
        self.bell.clicked.connect(self.open_requests)
        rl.addWidget(self.bell, 0, Qt.AlignHCenter)
        body.addWidget(rail)
        body.setContentsMargins(0, 12, 12, 12)
        # --- lista de chats
        self.side = Sidebar()
        self.side.setFixedWidth(310)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(6, 12, 6, 6)
        ttl = QLabel("  Chats")
        ttl.setObjectName("title")
        sl.addWidget(ttl)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Buscar chat…")
        self.search.textChanged.connect(lambda _: self.refresh_list())
        sl.addWidget(self.search)
        self.req_btn = QPushButton("")
        self.req_btn.setObjectName("green")
        self.req_btn.clicked.connect(self.open_requests)
        self.req_btn.hide()
        sl.addWidget(self.req_btn)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.currentItemChanged.connect(self.on_item)
        sl.addWidget(self.list, 1)
        self.empty_lbl = QLabel("Aún no tienes chats.\nPulsa ＋ para añadir un contacto\no crear un grupo.")
        self.empty_lbl.setObjectName("sub")
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        sl.addWidget(self.empty_lbl)
        self.fab = FabButton()
        self.fab.setParent(self.side)
        self.fab.setFixedSize(54, 54)
        self.fab.setToolTip("Añadir contacto o crear grupo")
        fm = QMenu(self)
        fm.addAction("👤  Añadir contacto", self.add_contact)
        fm.addAction("👥  Crear grupo", self.create_group)
        fm.addSeparator()
        fm.addAction("📨  Peticiones de chat", self.open_requests)
        self.fab_menu = fm
        self.fab.clicked.connect(self.show_fab_menu)
        self.side.fab = self.fab
        body.addWidget(self.side)
        # --- zona derecha
        self.right = QStackedWidget()
        ph = QFrame()
        ph.setObjectName("glass")
        pl = QVBoxLayout(ph)
        pl.addStretch(1)
        big = QLabel("MyTE")
        big.setObjectName("logo")
        big.setAlignment(Qt.AlignCenter)
        pl.addWidget(big)
        t = QLabel("Selecciona un chat de la izquierda para empezar.")
        t.setObjectName("sub")
        t.setAlignment(Qt.AlignCenter)
        pl.addWidget(t)
        pl.addStretch(1)
        self.right.addWidget(ph)
        ph2 = QFrame()
        ph2.setObjectName("glass")
        p2 = QVBoxLayout(ph2)
        p2.addStretch(1)
        self.det_lbl = QLabel("Este chat está en una ventana aparte.")
        self.det_lbl.setObjectName("h2")
        self.det_lbl.setAlignment(Qt.AlignCenter)
        p2.addWidget(self.det_lbl)
        bb = QPushButton("Volver a unirlo a MyTE")
        bb.clicked.connect(lambda: self.toggle_detach(self.current))
        p2.addWidget(bb, 0, Qt.AlignCenter)
        p2.addStretch(1)
        self.right.addWidget(ph2)
        body.addWidget(self.right, 1)
        self.toast_lbl = QLabel("", self.bg)
        self.toast_lbl.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast_lbl.hide)
        self.apply_theme()

    def show_fab_menu(self):
        pos = self.fab.mapToGlobal(QPoint(0, 0))
        self.fab_menu.exec(QPoint(pos.x(), pos.y() - self.fab_menu.sizeHint().height() - 8))

    # -- ayudas de datos
    def theme(self):
        return THEMES[self.ctl.theme_name]

    def uname(self, uid):
        return self.users.get(uid, {}).get("username", "Usuario")

    def other_user(self, chat):
        for m in chat["members"]:
            if m["id"] != self.me:
                return m["id"]
        return self.me

    def display_name(self, chat, uid):
        for m in chat["members"]:
            if m["id"] == uid and m.get("nick"):
                return m["nick"]
        return self.uname(uid)

    def chat_title(self, c):
        return (c["name"] or "Grupo") if c["type"] == "group" else self.uname(self.other_user(c))

    def chat_avatar(self, c):
        return c.get("avatar") if c["type"] == "group" else self.users.get(self.other_user(c), {}).get("avatar")

    # -- tema / avisos
    def apply_theme(self):
        self.bg.set_theme(self.ctl.theme_name)
        self.fab.plus = QColor("#123008" if self.ctl.theme_name == "light" else "#ffffff")
        self.fab.update()
        self.theme_btn.setText("🌙" if self.ctl.theme_name == "light" else "☀️")
        self.theme_btn.setToolTip("Cambiar a modo " + ("oscuro" if self.ctl.theme_name == "light" else "claro"))

    def retheme(self):
        self.apply_theme()
        for v in self.views.values():
            if v.loaded:
                v.set_messages(v.msgs, v.tasks)
            v.update_header()
        self.refresh_list()
        self.refresh_me()

    def toast(self, text, err=False):
        self.toast_lbl.setObjectName("toastErr" if err else "toast")
        self.toast_lbl.style().unpolish(self.toast_lbl)
        self.toast_lbl.style().polish(self.toast_lbl)
        self.toast_lbl.setText(text)
        self.toast_lbl.adjustSize()
        self.place_toast()
        self.toast_lbl.show()
        self.toast_lbl.raise_()
        self.toast_timer.start(4200 if err else 3000)

    def place_toast(self):
        self.toast_lbl.move((self.bg.width() - self.toast_lbl.width()) // 2, 18)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_toast()

    def set_banner(self, text):
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    # -- mensajes del servidor
    def handle(self, m):
        t = m.get("t")
        fn = getattr(self, "on_" + str(t), None)
        if fn:
            fn(m)

    def on_sync(self, m):
        self.me = m["me"]
        self.users = {int(k): v for k, v in m["users"].items()}
        self.friends, self.req_in, self.req_out = m["friends"], m["req_in"], m["req_out"]
        self.blocked, self.limits = m["blocked"], m.get("limits", {})
        new = {c["id"]: c for c in m["chats"]}
        for cid in list(self.views):
            if cid not in new:
                v = self.views.pop(cid)
                if v.detached:
                    v.detached.reattaching = True
                    v.detached.close()
                self.right.removeWidget(v)
                v.deleteLater()
                self.chats.pop(cid, None)
                if self.current == cid:
                    self.current = None
                    self.right.setCurrentIndex(0)
        select_new = None
        for cid, c in new.items():
            if cid not in self.views:
                v = ChatView(self, c)
                self.views[cid] = v
                self.right.addWidget(v)
                if not self.first_sync and c["type"] == "group" and c.get("creator") == self.me:
                    select_new = cid
            self.chats[cid] = c
            self.views[cid].update_chat(c)
        self.first_sync = False
        self.refresh_me()
        self.refresh_list()
        for d in (self.dlg_settings, self.dlg_req, self.dlg_blocked):
            if d is not None and d.isVisible():
                d.refresh()
        if select_new:
            self.select_chat(select_new)

    def on_presence(self, m):
        u = self.users.get(m["id"])
        if u:
            u["online"] = m["online"]
            for v in self.views.values():
                v.update_header()
            self.refresh_list()
            if self.dlg_settings is not None and self.dlg_settings.isVisible():
                self.dlg_settings.refresh()

    def on_msg(self, m):
        cid, msg = m["chat"], m["msg"]
        c = self.chats.get(cid)
        v = self.views.get(cid)
        if not c or not v:
            return
        c["last"] = {"sender": msg["sender"], "kind": msg["kind"], "ts": msg["ts"],
                     "text": (msg["file"]["name"] if msg["kind"] == "file" and msg.get("file") else msg["text"])}
        v.add_message(msg)
        if msg["kind"] != "system" and msg["sender"] != self.me and not self.is_seen(cid):
            self.unread[cid] = self.unread.get(cid, 0) + 1
            if self.ctl.settings.value("sound", "1") == "1":
                QApplication.beep()
        self.refresh_list()

    def on_history(self, m):
        v = self.views.get(m["chat"])
        if v:
            v.set_messages(m["msgs"], m["tasks"])

    def on_tasks(self, m):
        v = self.views.get(m["chat"])
        if v:
            v.set_tasks(m["tasks"])

    def on_reload(self, m):
        v = self.views.get(m["chat"])
        if v and v.loaded:
            self.net.send({"t": "history", "chat": m["chat"]})

    def on_info(self, m):
        self.toast(m["text"])

    def on_error(self, m):
        self.toast(m["text"], True)

    def on_file(self, m):
        fid = m["id"]
        if m.get("missing"):
            name, data = None, None
        else:
            name, data = m["name"], base64.b64decode(m["data"])
            self.file_cache[fid] = (name, data)
            if len(self.file_cache) > 80:
                self.file_cache.pop(next(iter(self.file_cache)))
        for cb in self.file_waiters.pop(fid, []):
            cb(name, data)

    def request_file(self, fid, cb):
        if fid in self.file_cache:
            n, d = self.file_cache[fid]
            QTimer.singleShot(0, lambda: cb(n, d))
            return
        first = fid not in self.file_waiters
        self.file_waiters.setdefault(fid, []).append(cb)
        if first:
            self.net.send({"t": "get_file", "id": fid})

    def download_file(self, fid, name):
        def done(n, data):
            if data is None:
                self.toast("El archivo ya no está disponible.", True)
                return
            fn, _ = QFileDialog.getSaveFileName(self, "Guardar archivo", name)
            if fn:
                try:
                    with open(fn, "wb") as f:
                        f.write(data)
                    self.toast("Archivo guardado")
                except OSError as e:
                    self.toast(f"No se pudo guardar: {e}", True)
        self.request_file(fid, done)

    # -- lista y selección
    def refresh_me(self):
        u = self.users.get(self.me, {})
        self.avatar_btn.setIcon(QIcon(avatar_pixmap(u.get("avatar"), u.get("username", "?"), 50, dark=self.theme()["name"] == "dark")))
        n = len(self.req_in)
        self.bell.setText("🔔" + (f" {n}" if n else ""))
        self.req_btn.setVisible(n > 0)
        self.req_btn.setText(f"📨 {n} petición{'es' if n != 1 else ''} de chat pendiente{'s' if n != 1 else ''}")

    def refresh_list(self):
        keep = self.current
        q = self.search.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        cs = sorted(self.chats.values(), key=lambda c: (c["last"]["ts"] if c.get("last") else c.get("created", 0)), reverse=True)
        sel_item = None
        for c in cs:
            if q and q not in self.chat_title(c).lower():
                continue
            it = QListWidgetItem()
            it.setData(Qt.UserRole, c["id"])
            w = ChatItem(self, c, self.unread.get(c["id"], 0))
            it.setSizeHint(QSize(0, 64))
            self.list.addItem(it)
            self.list.setItemWidget(it, w)
            if c["id"] == keep:
                sel_item = it
        if sel_item:
            self.list.setCurrentItem(sel_item)
        self.list.blockSignals(False)
        self.empty_lbl.setVisible(not self.chats)
        self.list.setVisible(bool(self.chats))

    def on_item(self, it, _prev):
        if it is not None:
            self.select_chat(it.data(Qt.UserRole))

    def select_chat(self, cid):
        v = self.views.get(cid)
        if not v:
            return
        self.current = cid
        self.unread.pop(cid, None)
        if not v.requested:
            v.requested = True
            self.net.send({"t": "history", "chat": cid})
        if v.detached:
            self.det_lbl.setText(f"«{self.chat_title(v.chat)}» está en una ventana aparte.")
            self.right.setCurrentIndex(1)
            v.detached.raise_()
            v.detached.activateWindow()
        else:
            self.right.setCurrentWidget(v)
            v.input.setFocus()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == cid:
                self.list.blockSignals(True)
                self.list.setCurrentItem(it)
                self.list.blockSignals(False)
        self.refresh_list()

    def is_seen(self, cid):
        v = self.views.get(cid)
        if v and v.detached:
            return v.detached.isActiveWindow()
        return cid == self.current and self.isActiveWindow()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and self.isActiveWindow() and self.current in self.unread:
            self.unread.pop(self.current, None)
            self.refresh_list()

    # -- separar / unir ventana
    def toggle_detach(self, cid):
        v = self.views.get(cid)
        if not v:
            return
        if v.detached:
            d = v.detached
            d.reattaching = True
            d.lay.removeWidget(v)
            v.detached = None
            self.right.addWidget(v)
            d.close()
            d.deleteLater()
            v.update_header()
            if self.current == cid:
                self.right.setCurrentWidget(v)
        else:
            self.right.removeWidget(v)
            d = DetachedWindow(self, v)
            v.detached = d
            d.show()
            v.update_header()
            if self.current == cid:
                self.det_lbl.setText(f"«{self.chat_title(v.chat)}» está en una ventana aparte.")
                self.right.setCurrentIndex(1)

    def capture_screen(self, view):
        wins = [w for w in [self] + [v.detached for v in self.views.values() if v.detached] if w.isVisible()]
        scr = view.window().screen() or QGuiApplication.primaryScreen()
        for w in wins:
            w.hide()

        def grab():
            pm = scr.grabWindow(0)
            for w in wins:
                w.show()
            if pm.isNull():
                self.toast("No se pudo capturar la pantalla en este sistema.", True)
            else:
                view.image_flow(pm.toImage())
        QTimer.singleShot(450, grab)

    # -- diálogos y acciones
    def open_chat_settings(self, cid):
        self.dlg_settings = ChatSettingsDialog(self, cid)
        self.dlg_settings.show()

    def add_contact(self):
        AddContactDialog(self).exec()

    def create_group(self):
        CreateGroupDialog(self).exec()

    def open_requests(self):
        self.dlg_req = RequestsDialog(self)
        self.dlg_req.show()

    def build_profile_menu(self):
        m = self.avatar_btn.menu()
        m.clear()
        a = m.addAction(f"👤  {self.uname(self.me)}")
        a.setEnabled(False)
        m.addSeparator()
        m.addAction("🖼  Cambiar foto de perfil…", self.change_avatar)
        m.addAction("🚫  Quitar foto de perfil", lambda: self.net.send({"t": "update_profile", "avatar": None}))
        m.addAction("✏️  Cambiar nombre de usuario…", self.change_username)
        m.addAction("🔑  Cambiar contraseña…", self.change_password)
        m.addSeparator()
        m.addAction("☀️  Modo claro" if self.ctl.theme_name == "dark" else "🌙  Modo oscuro", self.ctl.toggle_theme)
        snd = m.addAction("🔔  Sonido de mensajes")
        snd.setCheckable(True)
        snd.setChecked(self.ctl.settings.value("sound", "1") == "1")
        snd.toggled.connect(lambda v: self.ctl.settings.setValue("sound", "1" if v else "0"))
        m.addAction("⛔  Usuarios bloqueados", self.open_blocked)
        m.addSeparator()
        m.addAction("🚪  Cerrar sesión", self.ctl.logout)

    def open_blocked(self):
        self.dlg_blocked = BlockedDialog(self)
        self.dlg_blocked.show()

    def change_avatar(self):
        b = pick_avatar(self)
        if b:
            self.net.send({"t": "update_profile", "avatar": b})

    def change_username(self):
        r = form_dialog(self, "Cambiar nombre de usuario", [("Nuevo nombre de usuario", False), ("Tu contraseña actual", True)],
                        "Guardar", "3-20 caracteres: letras, números, punto, guion y guion bajo.")
        if r and r[0].strip():
            self.net.send({"t": "change_username", "user": r[0].strip(), "pw": r[1]})
            self.ctl.creds["user"] = r[0].strip()

    def change_password(self):
        r = form_dialog(self, "Cambiar contraseña", [("Contraseña actual", True), ("Nueva contraseña", True), ("Repite la nueva", True)], "Guardar")
        if r:
            if r[1] != r[2]:
                self.toast("Las contraseñas nuevas no coinciden.", True)
            else:
                self.net.send({"t": "change_password", "old": r[0], "new": r[1]})
                self.ctl.creds["pw"] = r[1]

    def closeEvent(self, e):
        self.ctl.quit()
        e.accept()

    def close_all(self):
        for v in list(self.views.values()):
            if v.detached:
                v.detached.reattaching = True
                v.detached.close()


# ============================================================== controlador
class AppCtl(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = QSettings("MyTE", "MyTEClient")
        self.theme_name = self.settings.value("theme", "light")
        self.net = Net()
        self.net.connected.connect(self.on_connected)
        self.net.failed.connect(self.on_failed)
        self.net.closed.connect(self.on_closed)
        self.net.message.connect(self.on_message)
        self.phase = "login"       # login | main | reconnect
        self.creds = {}
        self.main = None
        self.login = LoginWindow(self)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.try_reconnect)
        self.apply_theme()

    def apply_theme(self):
        self.app.setStyleSheet(build_qss(THEMES[self.theme_name]))
        self.login.update()

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.settings.setValue("theme", self.theme_name)
        self.apply_theme()
        if self.main:
            self.main.retheme()

    # -- conexión
    def start_login(self, host, port, user, pw, register=False, avatar=None):
        self.creds = dict(host=host, port=port, user=user, pw=pw, register=register, avatar=avatar)
        self.phase = "login"
        self.net.connect_to(host, port)

    def on_connected(self):
        c = self.creds
        if self.phase == "reconnect" or not c.get("register"):
            self.net.send({"t": "login", "user": c["user"], "pw": c["pw"]})
        else:
            m = {"t": "register", "user": c["user"], "pw": c["pw"]}
            if c.get("avatar"):
                m["avatar"] = c["avatar"]
            self.net.send(m)

    def on_failed(self, text):
        if self.phase == "reconnect":
            self.retry.start(3000)
        else:
            self.login.error(text)

    def on_closed(self):
        if self.phase == "main":
            self.phase = "reconnect"
            self.main.set_banner("⚠ Conexión perdida con el servidor. Reconectando…")
            self.retry.start(2500)
        elif self.phase == "login":
            self.login.error("El servidor cerró la conexión.")

    def try_reconnect(self):
        if self.phase == "reconnect":
            self.net.connect_to(self.creds["host"], self.creds["port"])

    def on_message(self, m):
        t = m.get("t")
        if t == "logged":
            c = self.creds
            self.settings.setValue("host", c["host"])
            self.settings.setValue("port", c["port"])
            self.settings.setValue("user", c["user"])
            if self.main is None:
                self.main = MainWindow(self)
                self.main.show()
                self.login.hide()
            else:
                self.main.set_banner("")
                self.main.first_sync = False
                for v in self.main.views.values():
                    if v.loaded:
                        self.net.send({"t": "history", "chat": v.chat["id"]})
            self.phase = "main"
            self.login.set_busy(False)
            return
        if self.main is None or self.phase == "login":
            if t == "error":
                self.login.error(m["text"])
            return
        if self.phase == "reconnect":
            if t == "error" and m.get("ref") in ("login", "register"):
                self.logout(m["text"])
            return
        self.main.handle(m)

    def logout(self, msg=""):
        self.retry.stop()
        self.net.disconnect_now()
        if self.main:
            self.main.close_all()
            m, self.main = self.main, None
            m.hide()
            m.deleteLater()
        self.phase = "login"
        self.creds = {}
        self.login.pw.clear()
        self.login.pw2.clear()
        self.login.set_busy(False)
        self.login.status.setText(msg)
        self.login.show()

    def quit(self):
        self.retry.stop()
        self.net.disconnect_now()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)   # ocultamos ventanas al hacer capturas
    app.setWindowIcon(make_app_icon())
    ctl = AppCtl(app)
    ctl.login.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
