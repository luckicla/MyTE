# -*- coding: utf-8 -*-
"""
Calidad de vida 2 - segundo pack de comodidades para MyTE (extensión de MyTE Injector).

Funciones: borradores, respuestas rápidas, buscar en el chat (Ctrl+F), mensajes fijados, marcar como no leído,
copiar imagen/enlace, reacciones locales, silenciar chats + palabras clave, resumen al volver, chats fijados,
favoritos y etiquetas de color, Ctrl+K / Ctrl+1…9, recordatorios, modo compacto, zoom de imágenes y aviso de texto largo.

NO incluido (a propósito):
  * «Editar el último mensaje con ↑»: ni el cliente ni el protocolo que usa (myte_client.py) tienen mensaje de edición
    (solo hay send_msg/history/get_file/...). Sin eso no se puede hacer de verdad, así que queda fuera.
  * «Última conexión en el perfil»: los usuarios del cliente solo traen username/avatar/online; no hay ningún dato de
    «última vez». Si algún día el servidor lo manda, hay que añadirlo en la ventana de perfil de calidad_de_vida.py.

Cómo está hecha (lecciones aprendidas):
  * Independiente de la versión del runtime: after/wrap propios, build_qss envuelto aquí, sondeo de ajustes si no hay
    api.on_change.
  * En el arranque (setup) NO existe todavía QApplication (el cargador corre antes de main()). Por eso todo lo que necesita
    la aplicación (filtros globales, temporizadores, atajos) se crea al construirse la ventana principal.
  * Cada función vive en su propio bloque try/except; los menús, enganches y avisos se registran en listas, así que si un
    bloque falla los demás siguen funcionando.
  * Las señales de Qt (p. ej. clicked(bool)) pueden pasar un argumento de más a los métodos envueltos: los envoltorios
    de este archivo lo toleran.
"""
import base64
import functools
import html as _html
import json
import re
import time
import traceback
import unicodedata
from datetime import datetime, timedelta

VERSION = "1.0"

COLORS = [("Rojo", "#ef5350"), ("Naranja", "#ff9800"), ("Amarillo", "#fdd835"), ("Verde", "#66bb6a"),
          ("Azul", "#42a5f5"), ("Violeta", "#ab47bc"), ("Rosa", "#ec407a")]
REACTS = ["👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "👀"]
LINK_RE = re.compile(r"(https?://[^\s<>\"']+)")

META = {
    "id": "calidad_de_vida_2", "name": "Calidad de vida 2", "version": VERSION,
    "description": "Borradores, respuestas rápidas, buscar en el chat, fijados, silenciar, favoritos, Ctrl+K, recordatorios, "
                   "modo compacto, zoom de imágenes y más.",
    "settings": [
        {"key": "i1", "type": "info", "label": "── MENSAJES ──"},
        {"key": "borradores", "type": "bool", "label": "Guardar el texto a medias de cada chat (borradores)", "default": True,
         "help": "Se conserva también al cerrar MyTE. En la lista de chats aparece ✏ en los chats con borrador."},
        {"key": "respuestas_rapidas", "type": "text", "label": "Respuestas rápidas (atajo = texto)", "default": "",
         "help": "Un atajo por línea: «gracias = ¡Muchas gracias!». Para varias líneas usa el botón «Editar respuestas "
                 "rápidas…» de arriba (aquí solo cabe una línea: separa los atajos con ;;). Se expanden al pulsar espacio o Intro."},
        {"key": "prefijo_rapidas", "type": "choice", "label": "Prefijo de las respuestas rápidas", "default": "/",
         "choices": [("/", "/  (barra)"), (";", ";  (punto y coma)"), ("!", "!  (exclamación)")]},
        {"key": "buscar_ctrl_f", "type": "bool", "label": "Ctrl+F busca dentro del chat", "default": True,
         "help": "Intro: siguiente · Mayús+Intro: anterior · Esc: cerrar."},
        {"key": "max_fijados", "type": "int", "label": "Máximo de mensajes fijados por chat", "default": 5, "min": 1, "max": 20,
         "help": "Clic derecho en un mensaje → «Fijar». Se guardan solo en este equipo."},
        {"key": "i2", "type": "info", "label": "── AVISOS ──"},
        {"key": "silenciar_menu", "type": "bool", "label": "Poder silenciar chats (clic derecho en la lista)", "default": True,
         "help": "Un chat silenciado no parpadea, no pita y no suma al contador del título. Si lo desactivas, dejan de aplicarse los silencios."},
        {"key": "palabras_aviso", "type": "text", "label": "Palabras que avisan aunque el chat esté silenciado", "default": "",
         "help": "Separadas por comas. Tu nombre de usuario avisa siempre."},
        {"key": "resumen_ausencia", "type": "bool", "label": "Resumen al volver: «Mientras no estabas…»", "default": False},
        {"key": "ausencia_minutos", "type": "int", "label": "Cuenta como ausencia a partir de (minutos)", "default": 5, "min": 1, "max": 240},
        {"key": "i3", "type": "info", "label": "── ORGANIZACIÓN ──"},
        {"key": "ctrl_k", "type": "bool", "label": "Ctrl+K salta a un chat · Ctrl+1…9 abre los primeros", "default": True,
         "help": "Fijar chats, favoritos y etiquetas de color: clic derecho en un chat de la lista."},
        {"key": "i4", "type": "info", "label": "── COMODIDAD ──"},
        {"key": "modo_compacto", "type": "choice", "label": "Modo compacto", "default": "no",
         "choices": [("no", "No"), ("suave", "Suave"), ("maximo", "Máximo")],
         "help": "Burbujas más juntas y sin repetir la foto en mensajes seguidos."},
        {"key": "zoom_imagenes", "type": "bool", "label": "Zoom al abrir imágenes (rueda, arrastrar, copiar)", "default": True},
        {"key": "aviso_texto_largo", "type": "bool", "label": "Avisar antes de enviar un texto muy largo", "default": True,
         "help": "Te deja enviarlo igualmente o como archivo .txt."},
        {"key": "texto_largo_max", "type": "int", "label": "Se considera largo a partir de (caracteres)", "default": 2000, "min": 200, "max": 100000},
        {"key": "i5", "type": "info", "label": "Reacciones y recordatorios: clic derecho sobre un mensaje. Las reacciones son locales (no se envían)."},
    ],
}


# ====================================================================== utilidades puras (sin Qt)
def _gt(v):
    """Texto de un ajuste: QSettings a veces devuelve una lista si el valor lleva comas."""
    if isinstance(v, (list, tuple)):
        v = ",".join(str(x) for x in v)
    return "" if v is None else str(v)


def fold(s):
    """Minúsculas y sin acentos, conservando la longitud (un carácter -> un carácter)."""
    return "".join((unicodedata.normalize("NFD", c)[:1] or c).lower()[:1] for c in (s or ""))


def u16len(s):
    return len(s.encode("utf-16-le")) // 2


def cp_from_u16(s, n):
    """Índice (en caracteres de Python) que corresponde a n unidades UTF-16 (las posiciones de Qt)."""
    tot = 0
    for i, ch in enumerate(s):
        if tot >= n:
            return i
        tot += 2 if ord(ch) > 0xFFFF else 1
    return len(s)


def parse_quick(raw):
    """'gracias = ¡Gracias!\\nbrb = ahora vuelvo' -> {'gracias': '¡Gracias!', 'brb': 'ahora vuelvo'}"""
    out = {}
    for line in re.split(r"[\r\n\u2028\u2029]+|;;", raw or ""):
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lstrip("/;!").strip().lower()
        v = v.strip()
        if k and v and not re.search(r"\s", k):
            out[k] = v.replace("\\n", "\n")
    return out


def expand_final(text, prefix, quick):
    """Si el texto termina en «/atajo» lo sustituye; si no, devuelve None."""
    m = re.search(r"(^|\s)%s(\S+)\s*$" % re.escape(prefix), text or "")
    if m and m.group(2).lower() in quick:
        return text[:m.start(2) - len(prefix)] + quick[m.group(2).lower()]
    return None


def find_spans(text, q):
    fq = fold((q or "").strip())
    if not fq:
        return []
    return [(m.start(), m.end()) for m in re.finditer(re.escape(fq), fold(text))]


def marked_html(text, spans, cur, rich):
    """HTML del mensaje con las coincidencias resaltadas (cur = índice de la coincidencia actual o -1)."""
    out, pos = [], 0
    for i, (a, b) in enumerate(spans):
        if a > pos:
            out.append(rich(text[pos:a]))
        col = "#ff9f1c" if i == cur else "#ffe066"
        out.append('<span style="background-color:%s; color:#1a1a1a;">%s</span>'
                   % (col, _html.escape(text[a:b]).replace("\n", "<br>")))
        pos = b
    if pos < len(text):
        out.append(rich(text[pos:]))
    return "".join(out)


def keyword_hit(text, words):
    ft = fold(text or "")
    for w in words:
        fw = fold((w or "").strip())
        if fw and re.search(r"(?<!\w)" + re.escape(fw) + r"(?!\w)", ft):
            return True
    return False


def split_words(raw):
    return [w.strip() for w in re.split(r"[,;\n]", _gt(raw)) if w.strip()]


def is_consecutive(prev, cur, gap=300):
    """¿Este mensaje sigue al anterior del mismo remitente (mismo día, menos de 5 min)?"""
    try:
        if not prev or prev.get("sender") != cur.get("sender"):
            return False
        a, b = float(prev["ts"]), float(cur["ts"])
        return abs(b - a) <= gap and datetime.fromtimestamp(a).date() == datetime.fromtimestamp(b).date()
    except Exception:
        return False


def snippet(msg, n=70):
    k = msg.get("kind")
    if k == "image":
        return "📷 Imagen"
    if k == "file" and msg.get("file"):
        return "📎 " + str(msg["file"].get("name", "archivo"))
    t = (msg.get("text") or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def plural(n, uno, varios):
    return "%d %s" % (n, uno if n == 1 else varios)


def next_morning(now=None):
    now = now or datetime.now()
    return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()


# ====================================================================== panel de ajustes (respuestas rápidas)
def settings_panel(api, parent):
    """Botón para editar las respuestas rápidas en un cuadro grande (el formulario automático solo tiene una línea)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
    box = QWidget(parent)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lbl = QLabel()
    lbl.setObjectName("sub")

    def count():
        lbl.setText("✏️  Respuestas rápidas guardadas: %d" % len(parse_quick(_gt(api.get("respuestas_rapidas")))))
    count()
    btn = QPushButton("Editar respuestas rápidas…")
    btn.setObjectName("ghost")

    def edit(_=False):
        d = QDialog(box.window())
        d.setWindowTitle("Respuestas rápidas")
        d.resize(520, 360)
        dl = QVBoxLayout(d)
        t = QLabel("Respuestas rápidas")
        t.setObjectName("title")
        dl.addWidget(t)
        h = QLabel("Un atajo por línea, con el formato  atajo = texto.  Escribe el prefijo + el atajo y un espacio "
                   "(o Intro) y se sustituye por el texto. Usa \\n para un salto de línea.")
        h.setObjectName("sub")
        h.setWordWrap(True)
        dl.addWidget(h)
        ed = QPlainTextEdit()
        ed.setPlaceholderText("gracias = ¡Muchas gracias!\nbrb = Ahora vuelvo, dame un minuto\nfirma = Un saludo,\\nTu nombre")
        ed.setPlainText("\n".join(l.strip() for l in re.split(r"[\r\n\u2028\u2029]+", _gt(api.get("respuestas_rapidas"))) if l.strip()))
        dl.addWidget(ed, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.setObjectName("ghost")
        cancel.setAutoDefault(False)
        ok = QPushButton("Guardar")
        ok.setObjectName("green")
        ok.setAutoDefault(False)
        row.addWidget(cancel)
        row.addWidget(ok)
        dl.addLayout(row)
        cancel.clicked.connect(d.reject)

        def save(_=False):
            api.set("respuestas_rapidas", ed.toPlainText().strip())
            count()
            d.accept()
        ok.clicked.connect(save)
        d.exec()
    btn.clicked.connect(edit)
    lay.addWidget(lbl)
    lay.addWidget(btn, 0, Qt.AlignLeft)
    return box


# ====================================================================== setup
def setup(api):
    g = api.g
    from PySide6.QtCore import Qt, QObject, QEvent, QSize, QTimer, QUrl
    from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut, QTextCursor
    from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                   QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
                                   QToolButton, QVBoxLayout, QWidget)
    # -- solo lo verificado; el resto, con g.get(...) y degradando
    MessageRow, ChatView, ChatItem, rich = g["MessageRow"], g["ChatView"], g["ChatItem"], g["rich"]
    MainWindow = g.get("MainWindow")
    keep = []                                   # referencias para que Python no borre filtros/objetos

    def fail(nombre):
        api.log("Se omite «%s» por un error:\n%s" % (nombre, traceback.format_exc()))

    # ------------------------------------------------------------------ helpers propios (no dependen del runtime)
    def after(cls, name, fn):
        orig = getattr(cls, name, None)
        if orig is None:
            api.log("%s.%s no existe en este cliente: se omite." % (getattr(cls, "__name__", cls), name))
            return

        @functools.wraps(orig)
        def w(obj, *a, **k):
            r = orig(obj, *a, **k)
            try:
                fn(obj, r, *a, **k)
            except Exception:
                api.log("Fallo en %s.%s:\n%s" % (cls.__name__, name, traceback.format_exc()))
            return r
        setattr(cls, name, w)

    def wrap(cls, name, fn):
        orig = getattr(cls, name, None)
        if orig is None:
            api.log("%s.%s no existe en este cliente: se omite." % (getattr(cls, "__name__", cls), name))
            return

        @functools.wraps(orig)
        def w(obj, *a, **k):
            called = []

            def inner(*aa, **kk):
                called.append(1)
                return orig(*aa, **kk)
            try:
                return fn(inner, obj, *a, **k)
            except Exception:
                api.log("Fallo en wrap %s.%s:\n%s" % (cls.__name__, name, traceback.format_exc()))
                return None if called else orig(obj, *a, **k)
        setattr(cls, name, w)

    def gtext(key):
        return _gt(api.get(key))

    # ------------------------------------------------------------------ almacén local (JSON en base64, por usuario)
    store = {}

    def me_id():
        try:
            return getattr(api.win, "me", None)
        except RuntimeError:
            return None

    def skey(name):
        return "d_%s_%s" % (name, me_id() or 0)

    def sload(name, kind):
        k = skey(name)
        v = store.get(k)
        if v is None:
            raw = _gt(api.get(k))
            try:
                v = json.loads(base64.b64decode(raw).decode("utf-8")) if raw else None
            except Exception:
                v = None
            if not isinstance(v, kind):
                v = kind()
            store[k] = v
        return v

    def ssave(name):
        k = skey(name)
        if k in store:
            api.set(k, base64.b64encode(json.dumps(store[k], ensure_ascii=False).encode("utf-8")).decode("ascii"))

    # ------------------------------------------------------------------ registros (cada función se apunta aquí)
    view_hooks, row_hooks, win_hooks, pre_select, list_ranks, list_decos = [], [], [], [], [], []
    msg_silencers, msg_listeners, msgs_changed, row_menu_items, chat_menu_items = [], [], [], [], []
    change_handlers = {}

    def run(lst, label, *a):
        for name, fn in list(lst):
            try:
                fn(*a)
            except Exception:
                api.log("Fallo en «%s» (%s):\n%s" % (name, label, traceback.format_exc()))

    # ------------------------------------------------------------------ estilos
    css_fns = []
    try:
        if "build_qss" in g:
            prev_qss = g["build_qss"]

            def build_qss_cv2(t):
                s = prev_qss(t)
                for fn in css_fns:
                    try:
                        s += "\n" + fn(t)
                    except Exception:
                        api.log("Fallo en estilos:\n" + traceback.format_exc())
                return s
            g["build_qss"] = build_qss_cv2
        else:
            api.log("No encuentro build_qss: los estilos de Calidad de vida 2 no se aplican.")

        def own_css(t):
            d = {k: t.get(k, v) for k, v in (("glass2", "rgba(255,255,255,205)"), ("line", "rgba(120,120,160,80)"),
                                              ("sel", "rgba(63,217,255,55)"), ("hover", "rgba(120,160,255,40)"),
                                              ("accent", "#3fd9ff"))}
            return """
QFrame#cv2Search, QFrame#cv2PinBar { background: %(glass2)s; border: none; border-bottom: 1px solid %(line)s; }
QToolButton#cv2PinChip { background: %(sel)s; border: 1px solid %(accent)s; border-radius: 10px; padding: 1px 9px; font-size: 9pt; }
QToolButton#cv2PinChip:hover { background: %(hover)s; }
QWidget#cv2Transparent { background: transparent; }
QLabel#cv2React { background: rgba(0,0,0,38); border-radius: 10px; padding: 0 6px; font-size: 11pt; }
QLabel#cv2Deco { background: transparent; font-size: 9pt; }
""" % d
        css_fns.append(own_css)
    except Exception:
        fail("estilos")

    # ------------------------------------------------------------------ enganches comunes
    def reapply_messages():
        ctl, win = api.ctl, api.win
        if win is not None and hasattr(win, "retheme"):
            win.retheme()
        elif ctl is not None and getattr(ctl, "main", None) is not None:
            ctl.main.retheme()

    try:
        if MainWindow is None:
            raise RuntimeError("el cliente no tiene MainWindow")
        after(ChatView, "__init__", lambda view, _r, *a, **k: run(view_hooks, "vista de chat", view))
        after(MessageRow, "__init__", lambda row, _r, *a, **k: run(row_hooks, "fila de mensaje", row, a[0], a[1]))
        after(MainWindow, "__init__", lambda win, _r, *a, **k: run(win_hooks, "ventana principal", win))
        after(MainWindow, "refresh_list", lambda win, _r, *a, **k: list_post(win))
        after(ChatView, "set_messages", lambda view, _r, *a, **k: run(msgs_changed, "mensajes", view, "set"))
        after(ChatView, "add_message", lambda view, _r, *a, **k: run(msgs_changed, "mensajes", view, "add"))

        def pre_select_wrap(inner, win, cid=None, *_ignored):
            run(pre_select, "cambiar de chat", win, cid)
            return inner(win, cid)
        wrap(MainWindow, "select_chat", pre_select_wrap)

        def is_seen_wrap(inner, win, cid, *_ignored):
            if getattr(win, "_cv2_silent", None) == cid:
                return True                     # mensaje silenciado: el cliente no suma no leído ni pita
            return inner(win, cid)
        wrap(MainWindow, "is_seen", is_seen_wrap)

        def mute_sounds():
            """Calla QApplication.beep/alert mientras se procesa un mensaje silenciado (también los de otras extensiones)."""
            saved = []
            for nm in ("beep", "alert"):
                try:
                    raw = QApplication.__dict__.get(nm)
                    if raw is None:
                        api.log("QApplication.%s no se puede callar en este Qt: los avisos de otras extensiones sonarán." % nm)
                        continue
                    setattr(QApplication, nm, staticmethod(lambda *a, **k: None))
                    saved.append((nm, raw))
                except Exception:
                    api.log("No puedo callar QApplication.%s: %s" % (nm, traceback.format_exc(limit=1)))

            def restore():
                for nm, raw in saved:
                    try:
                        setattr(QApplication, nm, raw)
                    except Exception:
                        api.log("No puedo restaurar QApplication.%s" % nm)
            return restore

        def on_msg_wrap(inner, win, m, *_ignored):
            silent, cid = False, None
            try:
                cid, msg = m["chat"], m["msg"]
                if msg.get("kind") != "system" and msg.get("sender") != win.me:
                    for name, fn in list(msg_silencers):
                        try:
                            if fn(win, cid, msg):
                                silent = True
                        except Exception:
                            api.log("Fallo en «%s» (silenciar):\n%s" % (name, traceback.format_exc()))
                    for name, fn in list(msg_listeners):
                        try:
                            fn(win, cid, msg, silent)
                        except Exception:
                            api.log("Fallo en «%s» (mensaje nuevo):\n%s" % (name, traceback.format_exc()))
            except Exception:
                api.log("Fallo analizando un mensaje nuevo:\n" + traceback.format_exc())
            restore = None
            if silent:
                win._cv2_silent = cid
                restore = mute_sounds()
            try:
                return inner(win, m)
            finally:
                if silent:
                    win._cv2_silent = None
                    if restore:
                        restore()
        wrap(MainWindow, "on_msg", on_msg_wrap)
    except Exception:
        fail("enganches comunes")

    # -- ajustes: cambios en caliente (runtime con on_change) o sondeo con QTimer (runtime antiguo)
    def on_changed(key, value):
        if str(key).startswith("d_"):
            return
        for fn in change_handlers.get(key, []):
            try:
                fn(value)
            except Exception:
                api.log("Fallo al cambiar el ajuste %s:\n%s" % (key, traceback.format_exc()))
    poll_state = {"timer": None}
    try:
        if hasattr(api, "on_change"):
            api.on_change(on_changed)
    except Exception:
        fail("ajustes en caliente")

    def start_poll(win):
        if hasattr(api, "on_change") or poll_state["timer"] is not None:
            return
        keys = [f["key"] for f in META["settings"] if f.get("type") != "info"]
        last = {k: api.get(k) for k in keys}
        t = QTimer(win)

        def tick():
            for k in keys:
                v = api.get(k)
                if v != last[k]:
                    last[k] = v
                    on_changed(k, v)
        t.timeout.connect(tick)
        t.start(1000)
        poll_state["timer"] = t
    win_hooks.append(("sondeo de ajustes", start_poll))

    # ------------------------------------------------------------------ menús contextuales (mensaje y lista de chats)
    def build_menu(items, label, *a):
        m = QMenu(a[0])
        for name, fn in list(items):
            try:
                fn(m, *a)
            except Exception:
                api.log("Fallo en «%s» (menú de %s):\n%s" % (name, label, traceback.format_exc()))
        return m

    def owner_row(w):
        while w is not None:
            if isinstance(w, MessageRow):
                return w
            w = w.parentWidget()
        return None

    try:
        class CtxFilter(QObject):
            def eventFilter(self, o, e):
                if e.type() != QEvent.ContextMenu or not isinstance(o, QWidget):
                    return False
                try:
                    row = owner_row(o)
                    if row is not None:
                        m = build_menu(row_menu_items, "mensaje", row)
                        if m.isEmpty():
                            return False
                        m.exec(e.globalPos())
                        return True
                    win = api.win
                    lst = getattr(win, "list", None)
                    if lst is not None and (o is lst or lst.isAncestorOf(o)):
                        it = lst.itemAt(lst.viewport().mapFromGlobal(e.globalPos()))
                        if it is not None:
                            cid = it.data(Qt.UserRole)
                            m = build_menu(chat_menu_items, "chat", win, cid)
                            if m.isEmpty():
                                return False
                            m.exec(e.globalPos())
                            return True
                except Exception:
                    api.log("Fallo en el menú contextual:\n" + traceback.format_exc())
                return False

        ctx_state = {"on": False}

        def install_ctx(win):
            app = QApplication.instance()           # en setup todavía no existe: se instala al abrir la ventana
            if app is None or ctx_state["on"]:
                return
            f = CtxFilter(app)
            app.installEventFilter(f)
            keep.append(f)
            ctx_state["on"] = True
        win_hooks.append(("filtro del menú contextual", install_ctx))

        def base_items(m, row):
            btn = getattr(row, "_reply_btn", None)          # botón ↩ de calidad_de_vida.py (si está cargada)
            if btn is not None:
                m.addAction("↩  Responder", lambda _=False: btn.click())
            msg = row.msg
            if msg.get("text") and msg.get("kind") != "file":
                m.addAction("📋  Copiar texto", lambda _=False: QApplication.clipboard().setText(msg["text"]))
        row_menu_items.append(("responder y copiar", base_items))
    except Exception:
        fail("menú contextual")

    # ------------------------------------------------------------------ lista de chats: orden y adornos
    def ordered_chats(win, q=""):
        cs = sorted(win.chats.values(), key=lambda c: (c["last"]["ts"] if c.get("last") else c.get("created", 0)), reverse=True)
        if list_ranks:
            cs.sort(key=lambda c: tuple(rk(c["id"]) for _n, rk in list_ranks))
        q = (q or "").strip().lower()
        return [c for c in cs if not q or q in win.chat_title(c).lower()]

    def find_top(w):
        try:
            col = w.layout().itemAt(1).layout()
            top = col.itemAt(0).layout()
            return top if top is not None and hasattr(top, "insertWidget") else None
        except Exception:
            return None

    def list_post(win):
        lst = getattr(win, "list", None)
        if lst is None:
            return
        try:
            if list_ranks:
                cs = ordered_chats(win, win.search.text())
                want = [c["id"] for c in cs]
                have = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]
                if want != have:                                    # misma construcción que refresh_list del cliente
                    cur, sel = win.current, None
                    lst.blockSignals(True)
                    lst.clear()
                    for c in cs:
                        it = QListWidgetItem()
                        it.setData(Qt.UserRole, c["id"])
                        w = ChatItem(win, c, win.unread.get(c["id"], 0))
                        it.setSizeHint(QSize(0, 64))
                        lst.addItem(it)
                        lst.setItemWidget(it, w)
                        if c["id"] == cur:
                            sel = it
                    if sel is not None:
                        lst.setCurrentItem(sel)
                    lst.blockSignals(False)
        except Exception:
            api.log("Fallo ordenando la lista de chats:\n" + traceback.format_exc())
        try:
            if not list_decos:
                return
            for i in range(lst.count()):
                it = lst.item(i)
                cid = it.data(Qt.UserRole)
                w = lst.itemWidget(it)
                if w is None:
                    continue
                parts = []
                for _n, fn in list_decos:
                    try:
                        s = fn(win, cid)
                    except Exception:
                        s = ""
                    if s:
                        parts.append(s)
                top = find_top(w) if parts else None
                if top is not None:
                    lb = QLabel("  ".join(parts))
                    lb.setObjectName("cv2Deco")
                    lb.setTextFormat(Qt.RichText)
                    top.insertWidget(1, lb)
        except Exception:
            api.log("Fallo adornando la lista de chats:\n" + traceback.format_exc())

    def refresh(win=None):
        win = win or api.win
        try:
            win.refresh_list()
        except Exception:
            pass

    # ======================================================================== MENSAJES
    try:
        # ---------------------------------------------------------------- 1. borradores por chat
        # (En la misma sesión cada chat ya conserva su cuadro de texto; lo que faltaba es recordarlo al cerrar MyTE.)
        def draft_put(cid, text):
            d = sload("drafts", dict)
            k = str(cid)
            old = bool(d.get(k))
            if text.strip():
                if d.get(k) == text:
                    return old, True
                d[k] = text
            else:
                d.pop(k, None)
            ssave("drafts")
            return old, bool(text.strip())

        def draft_view(view):
            cid = view.chat["id"]
            if api.get("borradores"):
                t = sload("drafts", dict).get(str(cid), "")
                if t and not view.input.toPlainText():
                    view.input.setPlainText(t)
                    c = view.input.textCursor()
                    c.movePosition(QTextCursor.End)
                    view.input.setTextCursor(c)
            tm = QTimer(view)
            tm.setSingleShot(True)
            tm.setInterval(600)

            def flush():
                if not api.get("borradores"):
                    return
                old, new = draft_put(view.chat["id"], view.input.toPlainText())
                if old != new:
                    refresh(view.win)
            tm.timeout.connect(flush)
            view.input.textChanged.connect(lambda: tm.start())
            view._cv2_draft_flush = flush
        view_hooks.append(("borradores", draft_view))

        def draft_before_select(win, cid):
            v = win.views.get(win.current)
            if v is not None and getattr(v, "_cv2_draft_flush", None):
                v._cv2_draft_flush()
        pre_select.append(("borradores", draft_before_select))

        orig_preview = getattr(ChatItem, "preview", None)
        if orig_preview is not None:
            def preview_cv2(win, chat):
                try:
                    if api.get("borradores"):
                        t = sload("drafts", dict).get(str(chat["id"]))
                        if t and t.strip():
                            line = "✏ " + t.replace("\n", " ").strip()
                            return line if len(line) < 34 else line[:33] + "…"
                except Exception:
                    pass
                return orig_preview(win, chat)
            ChatItem.preview = staticmethod(preview_cv2)
    except Exception:
        fail("borradores")

    try:
        # ---------------------------------------------------------------- 19. texto largo (se envuelve antes que las respuestas rápidas)
        def long_send(inner, view, *_ignored):
            t = view.input.toPlainText()
            lim = max(200, int(api.get("texto_largo_max") or 2000))
            if api.get("aviso_texto_largo") and t.strip() and len(t) > lim:
                box = QMessageBox(view.window())
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("Mensaje muy largo")
                box.setText("Tu mensaje tiene %d caracteres." % len(t))
                box.setInformativeText("Un texto tan largo es incómodo de leer en el chat. ¿Cómo quieres enviarlo?")
                b_send = box.addButton("Enviar igualmente", QMessageBox.AcceptRole)
                b_file = box.addButton("Enviar como archivo .txt", QMessageBox.ActionRole)
                box.addButton("Cancelar", QMessageBox.RejectRole)
                box.exec()
                c = box.clickedButton()
                if c is b_file:
                    view.send_bytes("file", "mensaje_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M%S"), t.encode("utf-8"))
                    view.input.clear()
                    view._reply = None
                    bar = getattr(view, "_reply_bar", None)
                    if bar is not None:
                        bar.hide()
                    return None
                if c is not b_send:
                    return None
            return inner(view)
        wrap(ChatView, "send_text", long_send)
    except Exception:
        fail("texto largo")

    try:
        # ---------------------------------------------------------------- 2. respuestas rápidas
        def quick_map():
            return parse_quick(gtext("respuestas_rapidas"))

        def quick_prefix():
            p = gtext("prefijo_rapidas")
            return p if p in ("/", ";", "!") else "/"

        def quick_typed(view):
            if getattr(view, "_cv2_busy", False):
                return
            qr = quick_map()
            if not qr:
                return
            box = view.input
            txt = box.toPlainText()
            pos = cp_from_u16(txt, box.textCursor().position())
            if pos < 2 or pos > len(txt) or txt[pos - 1] not in " \n":
                return
            pre = quick_prefix()
            head = txt[:pos - 1]
            m = re.search(r"(^|\s)%s(\S+)$" % re.escape(pre), head)
            if not m or m.group(2).lower() not in qr:
                return
            start = m.start(2) - len(pre)
            view._cv2_busy = True
            try:
                c = QTextCursor(box.document())
                c.setPosition(u16len(head[:start]))
                c.setPosition(u16len(head), QTextCursor.KeepAnchor)
                c.insertText(qr[m.group(2).lower()])
            finally:
                view._cv2_busy = False
        view_hooks.append(("respuestas rápidas", lambda view: view.input.textChanged.connect(lambda: quick_typed(view))))

        def quick_send(inner, view, *_ignored):
            qr = quick_map()
            if qr:
                t = view.input.toPlainText()
                new = expand_final(t, quick_prefix(), qr)
                if new is not None:
                    view._cv2_busy = True
                    try:
                        view.input.setPlainText(new)
                    finally:
                        view._cv2_busy = False
            return inner(view)
        wrap(ChatView, "send_text", quick_send)
    except Exception:
        fail("respuestas rápidas")

    try:
        # ---------------------------------------------------------------- 3. buscar en el chat (Ctrl+F)
        def s_bar(view):
            bar = getattr(view, "_cv2_sbar", None)
            if bar is not None:
                return bar
            bar = QFrame()
            bar.setObjectName("cv2Search")
            hl = QHBoxLayout(bar)
            hl.setContentsMargins(10, 5, 8, 5)
            hl.setSpacing(6)
            ed = QLineEdit()
            ed.setPlaceholderText("🔍  Buscar en este chat…")
            cnt = QLabel("")
            cnt.setObjectName("faint")
            cnt.setMinimumWidth(80)
            btns = []
            for txt, tip, fn in (("▲", "Anterior (Mayús+Intro)", lambda: s_step(view, -1)),
                                 ("▼", "Siguiente (Intro)", lambda: s_step(view, 1)),
                                 ("✕", "Cerrar (Esc)", lambda: s_close(view))):
                b = QToolButton()
                b.setText(txt)
                b.setToolTip(tip)
                b.clicked.connect(lambda _=False, f=fn: f())
                btns.append(b)
            hl.addWidget(ed, 1)
            hl.addWidget(cnt)
            for b in btns:
                hl.addWidget(b)
            lay = view.scroll.parentWidget().layout()
            lay.insertWidget(max(0, lay.indexOf(view.scroll)), bar)
            bar.hide()
            bar._ed, bar._cnt = ed, cnt

            class Keys(QObject):
                def eventFilter(self, o, e):
                    if e.type() == QEvent.KeyPress:
                        k = e.key()
                        if k == Qt.Key_Escape:
                            s_close(view)
                            return True
                        if k in (Qt.Key_Return, Qt.Key_Enter):
                            s_step(view, -1 if (e.modifiers() & Qt.ShiftModifier) else 1)
                            return True
                    return False
            kf = Keys(ed)
            ed.installEventFilter(kf)
            bar._keys = kf
            ed.textChanged.connect(lambda _t="": s_run(view))
            view._cv2_sbar, view._cv2_hits, view._cv2_i, view._cv2_marked = bar, [], -1, []
            return bar

        def s_clear(view):
            for r in getattr(view, "_cv2_marked", []):
                try:
                    if getattr(r, "txt", None) is not None:
                        r.txt.setText(rich(r.msg.get("text") or ""))
                except RuntimeError:
                    pass                      # la fila ya no existe (se recargó el historial)
            view._cv2_marked = []

        def s_paint(view, scroll=True):
            bar = s_bar(view)
            s_clear(view)
            hits, i = view._cv2_hits, view._cv2_i
            for n, (r, sp) in enumerate(hits):
                r.txt.setText(marked_html(r.msg.get("text") or "", sp, 0 if n == i else -1, rich))
                view._cv2_marked.append(r)
            q = bar._ed.text().strip()
            bar._cnt.setText(("%d/%d" % (i + 1, len(hits))) if hits else ("Sin resultados" if q else ""))
            if scroll and 0 <= i < len(hits):
                row = hits[i][0]
                QTimer.singleShot(30, lambda: view.scroll.ensureWidgetVisible(row, 0, 80))

        def s_run(view):
            bar = s_bar(view)
            q = bar._ed.text()
            hits = []
            if q.strip():
                for r in list(view.rows):
                    try:
                        if r.msg.get("kind") == "file" or getattr(r, "txt", None) is None:
                            continue
                        sp = find_spans(r.msg.get("text") or "", q)
                        if sp:
                            hits.append((r, sp))
                    except RuntimeError:
                        pass
            view._cv2_hits, view._cv2_i = hits, len(hits) - 1      # empieza por la coincidencia más reciente
            s_paint(view)

        def s_step(view, d):
            hits = getattr(view, "_cv2_hits", [])
            if not hits:
                return
            view._cv2_i = (view._cv2_i + d) % len(hits)
            try:
                hits[view._cv2_i][0].objectName()
            except RuntimeError:
                s_run(view)
                return
            s_paint(view)

        def s_open(view):
            bar = s_bar(view)
            bar.show()
            bar._ed.setFocus()
            bar._ed.selectAll()

        def s_close(view):
            bar = getattr(view, "_cv2_sbar", None)
            if bar is None:
                return
            bar.hide()
            s_clear(view)
            view._cv2_hits, view._cv2_i = [], -1
            bar._ed.blockSignals(True)
            bar._ed.clear()
            bar._ed.blockSignals(False)
            bar._cnt.setText("")
            view.input.setFocus()

        def s_view(view):
            s_bar(view)
            sc = QShortcut(QKeySequence("Ctrl+F"), view)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda: s_open(view) if api.get("buscar_ctrl_f") else None)
            keep.append(sc)
            try:                                              # botón 🔍 en la cabecera del chat
                hl = view.header.layout()
                b = QToolButton()
                b.setText("🔍")
                b.setToolTip("Buscar en este chat (Ctrl+F)")
                b.clicked.connect(lambda _=False: s_open(view) if api.get("buscar_ctrl_f") else None)
                hl.insertWidget(max(0, hl.indexOf(view.b_detach)), b)
            except Exception:
                api.log("No pude poner el botón 🔍 en la cabecera: %s" % traceback.format_exc(limit=1))
        view_hooks.append(("buscar en el chat", s_view))

        def s_msgs(view, kind):
            bar = getattr(view, "_cv2_sbar", None)
            if bar is None or not bar.isVisible() or not bar._ed.text().strip():
                return
            if kind == "set":
                s_run(view)
            elif view.rows:                                   # mensaje nuevo: se resalta sin mover la vista
                r = view.rows[-1]
                sp = find_spans(r.msg.get("text") or "", bar._ed.text()) if getattr(r, "txt", None) is not None and r.msg.get("kind") != "file" else []
                if sp:
                    view._cv2_hits.append((r, sp))
                    s_paint(view, scroll=False)
        msgs_changed.append(("buscar en el chat", s_msgs))
    except Exception:
        fail("buscar en el chat")

    try:
        # ---------------------------------------------------------------- 4. fijar mensajes
        def pins_of(cid):
            return sload("pins", dict).setdefault(str(cid), [])

        def pb_bar(view):
            bar = getattr(view, "_cv2_pbar", None)
            if bar is not None:
                return bar
            bar = QFrame()
            bar.setObjectName("cv2PinBar")
            hl = QHBoxLayout(bar)
            hl.setContentsMargins(10, 3, 8, 3)
            hl.setSpacing(6)
            hl.addWidget(QLabel("📌"))
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setFixedHeight(36)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            inner = QWidget()
            inner.setObjectName("cv2Transparent")
            area.viewport().setObjectName("cv2Transparent")
            il = QHBoxLayout(inner)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(6)
            area.setWidget(inner)
            hl.addWidget(area, 1)
            view.scroll.parentWidget().layout().insertWidget(0, bar)
            bar.hide()
            bar._lay = il
            view._cv2_pbar = bar
            return bar

        def jump_to_msg(view, mid, quiet=False):
            for r in reversed(view.rows):
                try:
                    if r.msg.get("id") == mid:
                        view.scroll.ensureWidgetVisible(r, 0, 80)
                        b = r.bubble
                        b.setStyleSheet("QFrame#%s { border: 2px solid #ffb300; }" % b.objectName())
                        QTimer.singleShot(1600, lambda: _unflash(b))
                        return True
                except RuntimeError:
                    continue
            if not quiet:
                api.toast("No encuentro ese mensaje (quizá es anterior al historial cargado)", err=True)
            return False

        def _unflash(b):
            try:
                b.setStyleSheet("")
            except RuntimeError:
                pass

        def pb_refresh(view):
            bar = pb_bar(view)
            lay = bar._lay
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            pins = pins_of(view.chat["id"])
            for p in pins:
                b = QToolButton()
                b.setObjectName("cv2PinChip")
                txt = "%s: %s" % (p.get("who", ""), p.get("snip", ""))
                b.setText(txt if len(txt) <= 32 else txt[:31] + "…")
                b.setToolTip(txt + "\nClic: ir al mensaje · Clic derecho: quitar")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, mid=p["id"]: jump_to_msg(view, mid))
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda _pos, b=b, mid=p["id"]: pb_menu(view, b, mid))
                lay.addWidget(b)
            lay.addStretch(1)
            bar.setVisible(bool(pins))

        def pb_menu(view, b, mid):
            m = QMenu(b)
            m.addAction("↗  Ir al mensaje", lambda _=False: jump_to_msg(view, mid))
            m.addAction("📌  Quitar de fijados", lambda _=False: unpin(view, mid))
            m.exec(b.mapToGlobal(b.rect().bottomLeft()))

        def pin(view, row):
            pins = pins_of(view.chat["id"])
            mx = max(1, int(api.get("max_fijados") or 5))
            if len(pins) >= mx:
                api.toast("Ya hay %d mensajes fijados en este chat (máximo %d). Quita alguno o sube el máximo en los ajustes." % (len(pins), mx), err=True)
                return
            msg = row.msg
            who = "Tú" if msg.get("sender") == view.win.me else view.win.display_name(view.chat, msg.get("sender"))
            pins.append({"id": msg["id"], "who": who, "snip": snippet(msg, 60), "ts": msg.get("ts", 0)})
            ssave("pins")
            pb_refresh(view)

        def unpin(view, mid):
            pins = pins_of(view.chat["id"])
            pins[:] = [p for p in pins if p["id"] != mid]
            ssave("pins")
            pb_refresh(view)

        def pin_item(m, row):
            mid = row.msg.get("id")
            if mid is None:
                return
            view = row.view
            if any(p["id"] == mid for p in pins_of(view.chat["id"])):
                m.addAction("📌  Quitar de fijados", lambda _=False: unpin(view, mid))
            else:
                m.addAction("📌  Fijar mensaje", lambda _=False: pin(view, row))
        view_hooks.append(("mensajes fijados", pb_refresh))
        row_menu_items.append(("mensajes fijados", pin_item))
    except Exception:
        fail("mensajes fijados")

    try:
        # ---------------------------------------------------------------- 7. copiar imagen / enlace, abrir enlaces
        # (Los enlaces ya se abren con un clic: el cliente usa setOpenExternalLinks. Aquí se añaden las acciones del menú.)
        url_re = g.get("URL_RE") or LINK_RE

        def link_track(row, view, msg):
            t = getattr(row, "txt", None)
            if t is not None:
                t.linkHovered.connect(lambda url: setattr(row, "_cv2_link", url))
        row_hooks.append(("enlaces", link_track))

        def media_items(m, row):
            msg = row.msg
            if msg.get("kind") == "image" and msg.get("file"):
                def copy_img(_=False):
                    full = getattr(row, "full", None)
                    if full is not None:
                        QApplication.clipboard().setImage(full)
                        api.toast("Imagen copiada")
                a = m.addAction("🖼  Copiar imagen", copy_img)
                a.setEnabled(getattr(row, "full", None) is not None)
            urls = []
            hov = getattr(row, "_cv2_link", "") or ""
            if hov:
                urls.append(hov)
            for u in url_re.findall(msg.get("text") or ""):
                if u not in urls:
                    urls.append(u)
            for u in urls[:3]:
                short = u if len(u) <= 38 else u[:37] + "…"
                m.addAction("🔗  Copiar enlace: " + short, lambda _=False, u=u: (QApplication.clipboard().setText(u), api.toast("Enlace copiado")))
                m.addAction("🌐  Abrir: " + short, lambda _=False, u=u: QDesktopServices.openUrl(QUrl(u)))
        row_menu_items.append(("copiar imagen y enlaces", media_items))
    except Exception:
        fail("copiar imagen y enlaces")

    try:
        # ---------------------------------------------------------------- 8. reacciones locales
        def react_get(cid, mid):
            return sload("reacts", dict).get(str(cid), {}).get(str(mid), "")

        def react_set(row, emoji):
            cid, mid = row.view.chat["id"], row.msg.get("id")
            d = sload("reacts", dict)
            ch = d.setdefault(str(cid), {})
            if emoji:
                ch[str(mid)] = emoji
            else:
                ch.pop(str(mid), None)
                if not ch:
                    d.pop(str(cid), None)
            ssave("reacts")
            react_apply(row)

        def react_apply(row):
            mid = row.msg.get("id")
            em = react_get(row.view.chat["id"], mid) if mid is not None else ""
            chip = getattr(row, "_cv2_react", None)
            if not em:
                if chip is not None:
                    chip.hide()
                return
            if chip is None:
                chip = QLabel()
                chip.setObjectName("cv2React")
                chip.setToolTip("Reacción local: solo la ves tú")
                bl = row.bubble.layout()
                bl.insertWidget(max(0, bl.count() - 1), chip, 0, Qt.AlignLeft)
                row._cv2_react = chip
            chip.setText(em)
            chip.show()
        row_hooks.append(("reacciones", lambda row, view, msg: react_apply(row)))

        def react_item(m, row):
            if row.msg.get("id") is None:
                return
            cur = react_get(row.view.chat["id"], row.msg["id"])
            sub = m.addMenu("😀  Reaccionar (solo para ti)")
            for em in REACTS:
                sub.addAction(("✔ " if em == cur else "") + em, lambda _=False, em=em: react_set(row, em))
            if cur:
                sub.addSeparator()
                sub.addAction("Quitar mi reacción", lambda _=False: react_set(row, ""))
        row_menu_items.append(("reacciones", react_item))
    except Exception:
        fail("reacciones locales")

    try:
        # ---------------------------------------------------------------- 15. recordatorios
        def rem_list():
            return sload("remind", list)

        def rem_add(row, due):
            view = row.view
            msg = row.msg
            who = "Tú" if msg.get("sender") == view.win.me else view.win.display_name(view.chat, msg.get("sender"))
            rem_list().append({"id": int(time.time() * 1000), "due": due, "cid": view.chat["id"], "mid": msg.get("id"),
                               "who": who, "snip": snippet(msg, 60)})
            ssave("remind")
            api.toast("⏰ Te lo recordaré a las %s" % datetime.fromtimestamp(due).strftime("%H:%M (%d/%m)"))

        def rem_item(m, row):
            sub = m.addMenu("⏰  Recuérdame")
            for label, secs in (("en 15 minutos", 900), ("en 1 hora", 3600)):
                sub.addAction(label, lambda _=False, s=secs: rem_add(row, time.time() + s))
            sub.addAction("mañana (9:00)", lambda _=False: rem_add(row, next_morning()))
        row_menu_items.append(("recordatorios", rem_item))

        def rem_check(win):
            if me_id() is None:
                return
            now = time.time()
            lst = rem_list()
            due = [r for r in lst if r.get("due", 0) <= now]
            if not due:
                return
            lst[:] = [r for r in lst if r.get("due", 0) > now]
            ssave("remind")
            flags = getattr(win, "_cv2_rem", None)
            if flags is None:
                flags = win._cv2_rem = {}
            for r in due:
                flags[r["cid"]] = r.get("mid")
            if len(due) == 1:
                c = win.chats.get(due[0]["cid"])
                win.toast("⏰ Recordatorio · %s: %s" % (win.chat_title(c) if c else "chat", due[0].get("snip", "")))
            else:
                win.toast("⏰ Tienes %d recordatorios: mira los chats marcados con ⏰" % len(due))
            QApplication.alert(win, 0)
            refresh(win)

        def rem_window(win):
            t = QTimer(win)
            t.setInterval(20000)
            t.timeout.connect(lambda: rem_check(win))
            t.start()
            QTimer.singleShot(8000, lambda: rem_check(win))        # recordatorios vencidos mientras MyTE estaba cerrado
        win_hooks.append(("recordatorios", rem_window))

        def rem_try_jump(win, cid, mid, tries):
            try:
                v = win.views.get(cid)
                if v is None or mid is None:
                    return
                if not jump_to_msg(v, mid, quiet=True) and tries > 0:
                    QTimer.singleShot(600, lambda: rem_try_jump(win, cid, mid, tries - 1))
            except Exception:
                api.log("Fallo al saltar al mensaje del recordatorio:\n" + traceback.format_exc())

        def rem_select(win, cid):
            flags = getattr(win, "_cv2_rem", None) or {}
            if cid in flags:
                mid = flags.pop(cid)
                QTimer.singleShot(500, lambda: rem_try_jump(win, cid, mid, 6))
        pre_select.append(("recordatorios", rem_select))
        list_decos.append(("recordatorios", lambda win, cid: "⏰" if cid in (getattr(win, "_cv2_rem", None) or {}) else ""))
    except Exception:
        fail("recordatorios")

    # ======================================================================== AVISOS
    try:
        # ---------------------------------------------------------------- 9. silenciar chats + 10. palabras clave
        def muted_list():
            return sload("muted", list)

        def is_muted(cid):
            return bool(api.get("silenciar_menu")) and cid in muted_list()

        def mute_silencer(win, cid, msg):
            if not is_muted(cid):
                return False
            words = split_words(api.get("palabras_aviso"))
            try:
                words.append(win.uname(win.me))
            except Exception:
                pass
            if keyword_hit(msg.get("text"), words):                # avisa igualmente
                c = win.chats.get(cid)
                win.toast("🔔 Te mencionan en «%s»: %s" % (win.chat_title(c) if c else "chat", snippet(msg, 50)))
                return False
            return True
        msg_silencers.append(("silenciar chats", mute_silencer))

        def mute_count(win, cid, msg, silent):
            if silent:
                d = getattr(win, "_cv2_mnew", None)
                if d is None:
                    d = win._cv2_mnew = {}
                d[cid] = d.get(cid, 0) + 1
        msg_listeners.append(("contador de silenciados", mute_count))

        def mute_select(win, cid):
            (getattr(win, "_cv2_mnew", None) or {}).pop(cid, None)
        pre_select.append(("silenciar chats", mute_select))

        def mute_deco(win, cid):
            if not is_muted(cid):
                return ""
            n = (getattr(win, "_cv2_mnew", None) or {}).get(cid, 0)
            return "🔕" + (' <span style="color:#8a94a8;">%d</span>' % n if n else "")
        list_decos.append(("silenciar chats", mute_deco))

        def mute_item(m, win, cid):
            if not api.get("silenciar_menu"):
                return
            if cid in muted_list():
                m.addAction("🔔  Quitar el silencio", lambda _=False: mute_toggle(win, cid))
            else:
                m.addAction("🔕  Silenciar chat", lambda _=False: mute_toggle(win, cid))

        def mute_toggle(win, cid):
            lst = muted_list()
            if cid in lst:
                lst.remove(cid)
            else:
                lst.append(cid)
            ssave("muted")
            refresh(win)
        chat_menu_items.append(("silenciar chats", mute_item))
    except Exception:
        fail("silenciar chats")

    try:
        # ---------------------------------------------------------------- 5. marcar chat como no leído
        def unread_item(m, win, cid):
            if win.unread.get(cid, 0) == 0:
                m.addAction("🔵  Marcar como no leído", lambda _=False: (win.unread.__setitem__(cid, 1), refresh(win)))
        chat_menu_items.append(("marcar no leído", unread_item))
    except Exception:
        fail("marcar como no leído")

    try:
        # ---------------------------------------------------------------- 11. resumen al volver de estar ausente
        away = {"t": None, "n": 0, "chats": set(), "conn": False}

        def away_state(st):
            if st == Qt.ApplicationActive:
                t0, n, chats = away["t"], away["n"], set(away["chats"])
                away["t"], away["n"] = None, 0
                away["chats"].clear()
                mins = max(1, int(api.get("ausencia_minutos") or 5))
                if api.get("resumen_ausencia") and t0 is not None and n and time.time() - t0 >= mins * 60:
                    api.toast("Mientras no estabas: %s en %s" % (plural(n, "mensaje", "mensajes"), plural(len(chats), "chat", "chats")))
            elif away["t"] is None:
                away["t"], away["n"] = time.time(), 0
                away["chats"].clear()

        def away_window(win):
            app = QApplication.instance()
            if app is not None and not away["conn"]:
                app.applicationStateChanged.connect(away_state)
                away["conn"] = True
        win_hooks.append(("resumen de ausencia", away_window))

        def away_count(win, cid, msg, silent):
            if away["t"] is not None and not silent:
                away["n"] += 1
                away["chats"].add(cid)
        msg_listeners.append(("resumen de ausencia", away_count))
    except Exception:
        fail("resumen al volver")

    # ======================================================================== ORGANIZACIÓN
    try:
        # ---------------------------------------------------------------- 12. fijar chats arriba / 13. favoritos y etiquetas
        def toggle_in(name, win, cid):
            lst = sload(name, list)
            if cid in lst:
                lst.remove(cid)
            else:
                lst.append(cid)
            ssave(name)
            refresh(win)

        list_ranks.append(("chats fijados", lambda cid: 0 if cid in sload("pchats", list) else 1))
        list_ranks.append(("favoritos", lambda cid: 0 if cid in sload("favs", list) else 1))
        list_decos.append(("chat fijado", lambda win, cid: "📌" if cid in sload("pchats", list) else ""))
        list_decos.append(("favorito", lambda win, cid: "⭐" if cid in sload("favs", list) else ""))

        def label_deco(win, cid):
            col = sload("labels", dict).get(str(cid))
            return '<span style="color:%s;">●</span>' % col if col else ""
        list_decos.append(("etiqueta de color", label_deco))

        def set_label(win, cid, col):
            d = sload("labels", dict)
            if col:
                d[str(cid)] = col
            else:
                d.pop(str(cid), None)
            ssave("labels")
            refresh(win)

        def color_icon(hexcol):
            pm = QPixmap(14, 14)
            pm.fill(QColor(hexcol))
            return QIcon(pm)

        def org_items(m, win, cid):
            m.addAction("📌  Quitar de los chats fijados" if cid in sload("pchats", list) else "📌  Fijar chat arriba",
                        lambda _=False: toggle_in("pchats", win, cid))
            m.addAction("⭐  Quitar de favoritos" if cid in sload("favs", list) else "⭐  Añadir a favoritos",
                        lambda _=False: toggle_in("favs", win, cid))
            sub = m.addMenu("🎨  Etiqueta de color")
            for name, col in COLORS:
                a = sub.addAction(name, lambda _=False, col=col: set_label(win, cid, col))
                a.setIcon(color_icon(col))
            if str(cid) in sload("labels", dict):
                sub.addSeparator()
                sub.addAction("Quitar la etiqueta", lambda _=False: set_label(win, cid, None))
        chat_menu_items.append(("fijar, favoritos y etiquetas", org_items))
    except Exception:
        fail("chats fijados, favoritos y etiquetas")

    try:
        # ---------------------------------------------------------------- 14. Ctrl+K y Ctrl+1…9
        class Palette(QDialog):
            def __init__(self, win):
                super().__init__(win)
                self.win = win
                self.setWindowTitle("Ir a un chat")
                self.setMinimumWidth(420)
                lay = QVBoxLayout(self)
                lay.setContentsMargins(16, 14, 16, 14)
                lay.setSpacing(8)
                t = QLabel("⌨  Ir a un chat")
                t.setObjectName("h2")
                lay.addWidget(t)
                self.ed = QLineEdit()
                self.ed.setPlaceholderText("Escribe el nombre del chat…")
                lay.addWidget(self.ed)
                self.lst = QListWidget()
                self.lst.setMinimumHeight(260)
                lay.addWidget(self.lst, 1)
                h = QLabel("↑ ↓ para moverte · Intro para abrir · Esc para cerrar")
                h.setObjectName("faint")
                lay.addWidget(h)
                row = QHBoxLayout()
                row.addStretch(1)
                cancel = QPushButton("Cancelar")
                cancel.setObjectName("ghost")
                cancel.setAutoDefault(False)
                go = QPushButton("Ir")
                go.setObjectName("green")
                go.setAutoDefault(False)
                row.addWidget(cancel)
                row.addWidget(go)
                lay.addLayout(row)
                cancel.clicked.connect(self.reject)
                go.clicked.connect(lambda _=False: self.go_current())
                self.ed.textChanged.connect(lambda _t="": self.fill())
                self.lst.itemDoubleClicked.connect(lambda it: self.go(it))
                self.ed.installEventFilter(self)
                self.fill()
                self.ed.setFocus()

            def fill(self):
                q = fold(self.ed.text().strip())
                self.lst.clear()
                for c in ordered_chats(self.win):
                    title = self.win.chat_title(c)
                    if q and q not in fold(title):
                        continue
                    n = self.win.unread.get(c["id"], 0)
                    it = QListWidgetItem(("👥 " if c["type"] == "group" else "") + title + ("   (%d)" % n if n else ""))
                    it.setData(Qt.UserRole, c["id"])
                    self.lst.addItem(it)
                if self.lst.count():
                    self.lst.setCurrentRow(0)

            def go(self, it):
                if it is None:
                    return
                cid = it.data(Qt.UserRole)
                self.accept()
                self.win.select_chat(cid)

            def go_current(self):
                self.go(self.lst.currentItem())

            def eventFilter(self, o, e):
                if o is self.ed and e.type() == QEvent.KeyPress:
                    k = e.key()
                    if k in (Qt.Key_Up, Qt.Key_Down):
                        r = self.lst.currentRow() + (1 if k == Qt.Key_Down else -1)
                        self.lst.setCurrentRow(max(0, min(self.lst.count() - 1, r)))
                        return True
                    if k in (Qt.Key_Return, Qt.Key_Enter):
                        self.go_current()
                        return True
                return False

        def open_palette(win):
            if api.get("ctrl_k") and getattr(win, "chats", None):
                Palette(win).exec()

        def goto_nth(win, n):
            if not api.get("ctrl_k"):
                return
            ids = [win.list.item(i).data(Qt.UserRole) for i in range(win.list.count())]
            if 0 < n <= len(ids):
                win.select_chat(ids[n - 1])

        def key_window(win):
            sk = QShortcut(QKeySequence("Ctrl+K"), win)
            sk.activated.connect(lambda: open_palette(win))
            for n in range(1, 10):
                sc = QShortcut(QKeySequence("Ctrl+%d" % n), win)
                sc.activated.connect(lambda n=n: goto_nth(win, n))
        win_hooks.append(("Ctrl+K y Ctrl+1…9", key_window))
    except Exception:
        fail("Ctrl+K y Ctrl+1…9")

    # ======================================================================== COMODIDAD
    try:
        # ---------------------------------------------------------------- 16. modo compacto
        def level():
            return {"suave": 1, "maximo": 2}.get(gtext("modo_compacto"), 0)

        def compact_row(row, view, msg):
            lv = level()
            if not lv:
                return
            lay = row.layout()
            mg = lay.contentsMargins()
            v = 1 if lv == 1 else 0
            lay.setContentsMargins(mg.left(), v, mg.right(), v)
            bl = row.bubble.layout()
            bl.setContentsMargins(11, 5 if lv == 1 else 3, 11, 4 if lv == 1 else 2)
            bl.setSpacing(2 if lv == 1 else 1)
            prev = view.rows[-1].msg if view.rows else None       # la fila nueva aún no está en view.rows
            if not is_consecutive(prev, msg):
                return
            if msg.get("sender") != view.win.me:                   # sin foto repetida (se conserva el hueco: queda alineado)
                av = lay.itemAt(0).widget() if lay.count() else None
                if isinstance(av, QLabel) and av.pixmap() is not None and not av.pixmap().isNull():
                    sp = av.sizePolicy()
                    sp.setRetainSizeWhenHidden(True)
                    av.setSizePolicy(sp)
                    av.hide()
                if lv == 2 and view.chat.get("type") == "group" and bl.count():
                    nm = bl.itemAt(0).widget()
                    if isinstance(nm, QLabel) and "font-weight:700" in nm.styleSheet():
                        nm.hide()
        row_hooks.append(("modo compacto", compact_row))

        def compact_view(view):
            view.ml.setSpacing({0: 2, 1: 1, 2: 0}[level()])
        view_hooks.append(("modo compacto", compact_view))

        def compact_changed(_v=None):
            win = api.win
            if win is not None:
                for v in win.views.values():
                    compact_view(v)
            reapply_messages()
        change_handlers.setdefault("modo_compacto", []).append(compact_changed)
    except Exception:
        fail("modo compacto")

    try:
        # ---------------------------------------------------------------- 17. zoom de imágenes
        class ZoomViewer(QDialog):
            def __init__(self, win, img, name, fid):
                super().__init__(win)
                self.win, self.img, self.pm = win, img, QPixmap.fromImage(img)
                self.k, self.fit, self._drag = 1.0, True, None
                self.setWindowTitle(name)
                lay = QVBoxLayout(self)
                self.area = QScrollArea()
                self.area.setWidgetResizable(False)
                self.area.setFrameShape(QFrame.NoFrame)
                self.lbl = QLabel()
                self.lbl.setAlignment(Qt.AlignCenter)
                self.area.setWidget(self.lbl)
                # diálogo con scroll en tema claro: área, viewport y widget interior transparentes
                self.area.setStyleSheet("background: transparent; border: none;")
                self.area.viewport().setStyleSheet("background: transparent;")
                self.lbl.setStyleSheet("background: transparent;")
                lay.addWidget(self.area, 1)
                row = QHBoxLayout()
                self.info = QLabel("")
                self.info.setObjectName("faint")
                row.addWidget(self.info, 1)
                for txt, fn in (("−", lambda: self.zoom(1 / 1.25)), ("+", lambda: self.zoom(1.25)),
                                ("Ajustar", self.set_fit), ("100 %", self.set_full)):
                    b = QPushButton(txt)
                    b.setObjectName("ghost")
                    b.setAutoDefault(False)
                    b.clicked.connect(lambda _=False, f=fn: f())
                    row.addWidget(b)
                cp = QPushButton("Copiar")
                cp.setObjectName("ghost")
                cp.setAutoDefault(False)
                cp.clicked.connect(lambda _=False: (QApplication.clipboard().setImage(self.img), api.toast("Imagen copiada")))
                sv = QPushButton("Guardar como…")
                sv.setAutoDefault(False)
                sv.clicked.connect(lambda _=False: win.download_file(fid, name))
                cl = QPushButton("Cerrar")
                cl.setObjectName("ghost")
                cl.setAutoDefault(False)
                cl.clicked.connect(self.accept)
                for b in (cp, sv, cl):
                    row.addWidget(b)
                lay.addLayout(row)
                scr = self.screen().availableGeometry()
                self.resize(int(scr.width() * 0.72), int(scr.height() * 0.78))
                self.area.viewport().installEventFilter(self)
                QTimer.singleShot(0, self.render)

            def render(self):
                vp = self.area.viewport().size()
                if self.fit:
                    k = min(max(1, vp.width() - 4) / max(1, self.pm.width()), max(1, vp.height() - 4) / max(1, self.pm.height()), 1.0)
                    self.k = k
                size = QSize(max(1, int(self.pm.width() * self.k)), max(1, int(self.pm.height() * self.k)))
                self.lbl.setPixmap(self.pm.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.lbl.resize(max(size.width(), vp.width()), max(size.height(), vp.height()))
                self.info.setText("%d %%  ·  %d × %d px  ·  rueda: zoom · arrastra para mover" % (round(self.k * 100), self.pm.width(), self.pm.height()))

            def zoom(self, f):
                hb, vb = self.area.horizontalScrollBar(), self.area.verticalScrollBar()
                vp = self.area.viewport().size()
                cx = (hb.value() + vp.width() / 2) / max(1, self.lbl.width())
                cy = (vb.value() + vp.height() / 2) / max(1, self.lbl.height())
                self.fit = False
                self.k = max(0.05, min(16.0, self.k * f))
                self.render()
                hb.setValue(int(cx * self.lbl.width() - vp.width() / 2))
                vb.setValue(int(cy * self.lbl.height() - vp.height() / 2))

            def set_fit(self):
                self.fit = True
                self.render()

            def set_full(self):
                self.fit, self.k = False, 1.0
                self.render()

            def resizeEvent(self, e):
                super().resizeEvent(e)
                if self.fit:
                    QTimer.singleShot(0, self.render)

            def eventFilter(self, o, e):
                t = e.type()
                if o is self.area.viewport():
                    if t == QEvent.Wheel:
                        self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
                        return True
                    if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
                        self._drag = e.globalPosition().toPoint()
                        return True
                    if t == QEvent.MouseMove and self._drag is not None:
                        p = e.globalPosition().toPoint()
                        d = p - self._drag
                        self._drag = p
                        self.area.horizontalScrollBar().setValue(self.area.horizontalScrollBar().value() - d.x())
                        self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().value() - d.y())
                        return True
                    if t == QEvent.MouseButtonRelease:
                        self._drag = None
                        return True
                return False

        def open_image_zoom(inner, row, *_ignored):
            full = getattr(row, "full", None)
            if api.get("zoom_imagenes") and full is not None:
                ZoomViewer(row.view.win, full, row.msg["file"]["name"], row.msg["file"]["id"]).exec()
                return None
            return inner(row)
        wrap(MessageRow, "open_image", open_image_zoom)
    except Exception:
        fail("zoom de imágenes")

    api.log("Calidad de vida 2 v%s cargada (runtime con on_change: %s)." % (VERSION, hasattr(api, "on_change")))
