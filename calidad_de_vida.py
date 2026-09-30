# -*- coding: utf-8 -*-
"""Calidad de vida: perfil al pulsar nombre/foto, contactos, respuestas y punto rojo en el icono."""
import re

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QLabel, QPushButton, QLabel as _L,
                               QMenu, QPlainTextEdit, QLineEdit)

META = {
    "id": "calidad_de_vida", "name": "Calidad de vida", "version": "1.1",
    "description": "Responder mensajes (doble clic o clic derecho), perfil al pulsar nombre o foto y punto rojo en el icono.",
    "settings": [
        {"key": "perfiles", "type": "bool", "label": "Abrir perfil al pulsar nombre o foto", "default": True},
        {"key": "punto", "type": "bool", "label": "Punto rojo en el icono con mensajes sin leer", "default": True},
        {"key": "contactos", "type": "bool", "label": "Botón «Añadir a contactos» en el perfil", "default": True},
        {"key": "info", "type": "info", "label": "Responder: doble clic en un mensaje (o clic derecho). La respuesta viaja como cita dentro del texto (↩); quien no tenga la extensión la verá como texto normal."},
    ],
}

QUOTE = "↩ "          # los mensajes con respuesta empiezan por «↩ Nombre: fragmento» + salto de línea


def split_reply(text):
    """'↩ Ana: hola\\nmensaje' -> ('Ana', 'hola', 'mensaje'); si no es respuesta -> None."""
    if text and text.startswith(QUOTE) and "\n" in text:
        head, body = text.split("\n", 1)
        head = head[len(QUOTE):]
        if ": " in head and len(head) <= 140 and body.strip():
            who, snip = head.split(": ", 1)
            return who, snip, body
    return None


_api = None
_unread = 0
_base_icon = None


def _plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _labels(row):
    return row.findChildren(QLabel)


def _row_info(row):
    avatar, texts = None, []
    for l in _labels(row):
        pm = l.pixmap()
        if pm is not None and not pm.isNull():
            avatar = avatar or l
        else:
            t = _plain(l.text())
            if t:
                texts.append((t, l))
    if not texts:
        return avatar, None, "", None
    body = max(texts, key=lambda x: len(x[0]))
    names = [x for x in texts if x[1] is not body[1] and len(x[0]) <= 40]
    name = names[0] if names else (None, None)
    return avatar, name[1], body[0], (name[0] if names else "")


# ---------------------------------------------------------------- perfil
def _find_native_profile(name):
    """Intenta usar el diálogo de perfil del propio cliente si existe; si no, devuelve None."""
    g = _api.g
    for k, v in g.items():
        if callable(v) and "profile" in k.lower() and ("show" in k.lower() or "open" in k.lower()):
            try:
                v(name)
                return True
            except Exception:
                pass
    ctl = _api.ctl
    for n in ("show_profile", "open_profile", "view_profile", "show_user"):
        fn = getattr(ctl, n, None) if ctl else None
        if callable(fn):
            try:
                fn(name)
                return True
            except Exception:
                pass
    return None


def _profile(row):
    avatar, name_lbl, body, name = _row_info(row)
    if name and _find_native_profile(name):
        return
    d = QDialog(_api.win)
    d.setWindowTitle(name or "Perfil")
    lay = QVBoxLayout(d)
    if avatar is not None:
        pm = avatar.pixmap()
        pic = QLabel()
        pic.setAlignment(Qt.AlignCenter)
        pic.setPixmap(pm.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(pic)
    t = QLabel(name or "(usuario)")
    t.setObjectName("title")
    t.setAlignment(Qt.AlignCenter)
    lay.addWidget(t)
    if _api.get("contactos"):
        b = QPushButton("Añadir a contactos")
        b.clicked.connect(lambda: (_add_contact(name), d.accept()))
        lay.addWidget(b)
    c = QPushButton("Cerrar")
    c.setObjectName("ghost")
    c.clicked.connect(d.accept)
    lay.addWidget(c)
    d.exec()


def _add_contact(name):
    if not name:
        return
    ctl = _api.ctl
    for n in ("add_contact", "send_friend_request", "request_contact", "add_friend", "send_request"):
        fn = getattr(ctl, n, None) if ctl else None
        if callable(fn):
            try:
                fn(name)
                _api.toast("Solicitud enviada a %s" % name)
                return
            except Exception as e:
                _api.log("%s falló: %s" % (n, e))
    _api.toast("No encuentro cómo añadir contactos en este cliente.", True)


class _Click(QObject):
    def __init__(self, row):
        super().__init__(row)
        self.row = row

    def eventFilter(self, o, e):
        if e.type() == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton and _api.get("perfiles"):
            avatar, name_lbl, body, name = _row_info(self.row)
            if o is avatar or o is name_lbl:
                _profile(self.row)
                return True
        return False


def _on_row(row):
    avatar, name_lbl, body, name = _row_info(row)
    f = _Click(row)
    for w in (avatar, name_lbl):
        if w is not None:
            w.installEventFilter(f)
            w.setCursor(Qt.PointingHandCursor)


# ---------------------------------------------------------------- punto rojo
def _dotted(icon, n):
    pm = icon.pixmap(64, 64)
    if pm.isNull():
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("#ffffff"))
    p.setBrush(QColor("#ff2d3d"))
    p.drawEllipse(36, 0, 27, 27)
    p.end()
    return QIcon(pm)


def _set_badge(on):
    win = _api.win
    if not win or _base_icon is None:
        return
    icon = _dotted(_base_icon, 1) if on else _base_icon
    win.setWindowIcon(icon)
    app = QApplication.instance()
    if app:
        app.setWindowIcon(icon)


def _on_msg(win, m):
    global _unread
    if not _api.get("punto") or win.isActiveWindow():
        return
    mine = False
    try:
        me = getattr(_api.ctl, "username", None) or getattr(_api.ctl, "user", None)
        if isinstance(m, dict) and me:
            mine = me in (m.get("from"), m.get("sender"), m.get("user"), m.get("author"))
    except Exception:
        pass
    if not mine:
        _unread += 1
        _set_badge(True)


class _Active(QObject):
    def eventFilter(self, o, e):
        global _unread
        if e.type() == QEvent.WindowActivate and _unread:
            _unread = 0
            _set_badge(False)
        return False


def _on_win(win):
    global _base_icon
    _base_icon = win.windowIcon()
    if _base_icon.isNull() and QApplication.instance():
        _base_icon = QApplication.instance().windowIcon()
    win._cv_active = _Active(win)
    win.installEventFilter(win._cv_active)


def setup_reply(api):
    """Responder estilo WhatsApp: doble clic (o clic derecho) -> barra «Respondiendo a…» -> cita dentro de la burbuja."""
    g = api.g
    from PySide6.QtCore import Qt, QObject, QEvent
    from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout
    MessageRow, ChatView, ChatItem = g["MessageRow"], g["ChatView"], g["ChatItem"]
    rich = g["rich"]

    class Clicker(QObject):
        def __init__(self, parent, fn):
            super().__init__(parent)
            self.fn = fn

        def eventFilter(self, o, e):
            if e.type() == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton:
                self.fn()
                return True
            return False

    def make_clickable(w, fn, tip):
        w.setCursor(Qt.PointingHandCursor)
        w.setToolTip(tip)
        w.installEventFilter(Clicker(w, fn))

    class RowEvents(QObject):
        def __init__(self, row):
            super().__init__(row)
            self.row = row

        def eventFilter(self, o, e):
            t = e.type()
            if t == QEvent.ContextMenu:
                row_menu(self.row, e.globalPos())
                return True
            if t == QEvent.MouseButtonDblClick:
                start_reply(self.row.view, self.row)
                return True
            return False

    def snippet_of(row):
        m = row.msg
        if m["kind"] == "image":
            return "📷 Imagen"
        if m["kind"] == "file" and m.get("file"):
            return "📎 " + m["file"]["name"]
        t = (m.get("text") or "").replace("\n", " ").strip()
        return t if len(t) <= 70 else t[:69] + "…"

    def row_menu(row, pos):
        m = QMenu(row)
        m.addAction("↩  Responder", lambda: start_reply(row.view, row))
        if row.msg.get("text") and row.msg["kind"] != "file":
            m.addAction("📋  Copiar texto", lambda: QApplication.clipboard().setText(row.msg["text"]))
        m.exec(pos)

    def ensure_bar(view):
        bar = getattr(view, "_reply_bar", None)
        if bar is not None:
            return bar
        bar = QFrame()
        bar.setObjectName("replyBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 6, 8, 6)
        bar._lbl = QLabel()
        bar._lbl.setTextFormat(Qt.RichText)
        bl.addWidget(bar._lbl, 1)
        x = QToolButton()
        x.setText("✕")
        x.setToolTip("Cancelar respuesta")
        x.clicked.connect(lambda: clear_reply(view))
        bl.addWidget(x)
        lay = view.bar.parentWidget().layout()
        lay.insertWidget(lay.indexOf(view.bar), bar)
        bar.hide()
        view._reply_bar = bar
        return bar

    def start_reply(view, row):
        who = view.win.display_name(view.chat, row.msg["sender"]) if row.msg["sender"] != view.win.me else "ti"
        view._reply = (who, snippet_of(row))
        bar = ensure_bar(view)
        bar._lbl.setText("↩ Respondiendo a <b>%s</b>: <i>%s</i>" % (g["html"].escape(who), g["html"].escape(view._reply[1])))
        bar.show()
        view.input.setFocus()

    def clear_reply(view):
        view._reply = None
        bar = getattr(view, "_reply_bar", None)
        if bar is not None:
            bar.hide()

    def jump_to(view, snip):
        key = snip.rstrip("…").strip()
        for r in reversed(view.rows):
            if snippet_of(r).startswith(key):
                view.scroll.ensureWidgetVisible(r, 0, 80)
                return

    def row_init(row, _r, view, msg):
        ev = RowEvents(row)
        for w in (row, row.bubble, getattr(row, "txt", None), getattr(row, "img_label", None)):
            if w is not None:
                w.installEventFilter(ev)
        q = split_reply(msg.get("text"))
        if q and getattr(row, "txt", None) is not None:
            who, snip, body = q
            row.msg = dict(msg, text=body)
            row.txt.setText(rich(body))
            box = QFrame()
            box.setObjectName("replyQuote")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(9, 4, 9, 4)
            bl.setSpacing(0)
            a = QLabel(who)
            a.setObjectName("replyWho")
            b = QLabel(snip)
            b.setObjectName("replyTxt")
            b.setWordWrap(True)
            bl.addWidget(a)
            bl.addWidget(b)
            make_clickable(box, lambda: jump_to(view, snip), "Ir al mensaje")
            row.bubble.layout().insertWidget(row.bubble.layout().indexOf(row.txt), box)
            row.set_max_width(view.width())
    api.after(MessageRow, "__init__", row_init)

    def send_text(orig, view):
        rep = getattr(view, "_reply", None)
        t = view.input.toPlainText()
        if rep and t.strip():
            view.input.setPlainText("%s%s: %s\n%s" % (QUOTE, rep[0], rep[1], t))
            orig(view)
            if not view.input.toPlainText():
                clear_reply(view)
            else:
                view.input.setPlainText(t)
        else:
            orig(view)
    api.wrap(ChatView, "send_text", send_text)

    def view_init(view, _r, *a, **k):
        view._reply = None
    api.after(ChatView, "__init__", view_init)

    # la lista de chats no debe mostrar el prefijo de cita
    orig_preview = ChatItem.preview

    def preview(win, chat):
        last = chat.get("last")
        q = split_reply(last.get("text")) if last else None
        if q:
            chat = dict(chat, last=dict(last, text=q[2]))
        return orig_preview(win, chat)
    ChatItem.preview = staticmethod(preview)

    api.css(lambda t: """
QFrame#replyBar { background: %(glass2)s; border: none; border-top: 1px solid %(line)s; }
QFrame#replyQuote { background: rgba(0,0,0,45); border: none; border-left: 3px solid %(accent)s; border-radius: 6px; }
QLabel#replyWho { font-weight: 700; font-size: 8.5pt; color: %(accent)s; }
QLabel#replyTxt { font-size: 9pt; }
""" % t)


def setup(api):
    global _api
    _api = api
    try:
        setup_reply(api)
    except Exception:
        import traceback
        api.log("No se pudo activar «responder»:\n" + traceback.format_exc())
    api.on("main_window", _on_win)
    api.on("message_row", _on_row)
    api.on("msg", _on_msg)
    if api.win:
        _on_win(api.win)
