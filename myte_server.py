#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyTE Server  -  servidor de mensajería para MyTE
================================================
* Sockets TCP con tramas JSON (4 bytes de longitud + JSON UTF-8).
* Datos en SQLite (myte_data/myte.db), archivos en myte_data/files.
* Panel de administración en tkinter.  Sin pantalla:  python myte_server.py --headless
"""
import argparse, base64, hashlib, hmac, json, os, queue, re, secrets, socket
import sqlite3, struct, sys, threading, time, traceback
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
except Exception:          # servidor sin entorno gráfico
    tk = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("MYTE_DATA", os.path.join(APP_DIR, "myte_data"))
FILES_DIR = os.path.join(DATA_DIR, "files")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DB_PATH = os.path.join(DATA_DIR, "myte.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
for _d in (DATA_DIR, FILES_DIR, SNAP_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)

DEFAULTS = {"port": 5050, "max_file_mb": 25, "max_storage_mb": 2048, "autosave_min": 5}
MAX_AVATAR_B64 = 400_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,20}$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


# ----------------------------------------------------------------- utilidades
def now():
    return time.time()


def ts_str(t):
    return datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M")


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return salt, h


def pack(obj):
    b = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(b)) + b


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("cerrado")
        buf.extend(chunk)
    return bytes(buf)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


class ClientError(Exception):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
  salt TEXT NOT NULL, pw_hash TEXT NOT NULL, avatar TEXT, created REAL);
CREATE TABLE IF NOT EXISTS friends(a INTEGER, b INTEGER, chat_id INTEGER, PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER, to_id INTEGER, ts REAL, UNIQUE(from_id,to_id));
CREATE TABLE IF NOT EXISTS blocks(blocker INTEGER, blocked INTEGER, PRIMARY KEY(blocker,blocked));
CREATE TABLE IF NOT EXISTS chats(
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, name TEXT, description TEXT,
  avatar TEXT, creator INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS members(
  chat_id INTEGER, user_id INTEGER, role TEXT DEFAULT 'member', nick TEXT, joined REAL,
  PRIMARY KEY(chat_id,user_id));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender INTEGER, kind TEXT,
  text TEXT, file_id INTEGER, ts REAL);
CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, size INTEGER, path TEXT, uploader INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, title TEXT, description TEXT,
  creator INTEGER, assignee INTEGER, status TEXT DEFAULT 'pending', created REAL, updated REAL);
"""

MSG_SQL = ("SELECT m.id,m.chat_id,m.sender,m.kind,m.text,m.file_id,m.ts,f.name AS fname,f.size AS fsize "
           "FROM messages m LEFT JOIN files f ON f.id=m.file_id ")


def mdict(r):
    return {"id": r["id"], "chat": r["chat_id"], "sender": r["sender"], "kind": r["kind"],
            "text": r["text"], "ts": r["ts"],
            "file": ({"id": r["file_id"], "name": r["fname"] or "(archivo eliminado)",
                      "size": r["fsize"] or 0} if r["file_id"] else None)}


def tdict(r):
    return {"id": r["id"], "chat": r["chat_id"], "title": r["title"], "description": r["description"],
            "creator": r["creator"], "assignee": r["assignee"], "status": r["status"],
            "created": r["created"], "updated": r["updated"]}


# ------------------------------------------------------------------- sesión
class Session:
    def __init__(self, srv, conn, addr):
        self.srv, self.conn, self.ip = srv, conn, addr[0]
        self.uid = None
        self.outq = queue.Queue()
        self.alive = True

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def send(self, obj):
        if self.alive:
            self.outq.put(pack(obj))

    def _writer(self):
        while True:
            d = self.outq.get()
            if d is None:
                break
            try:
                self.conn.sendall(d)
            except OSError:
                break
        self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass
        self.outq.put(None)

    def run(self):
        self.srv.all_sessions.add(self)
        threading.Thread(target=self._writer, daemon=True).start()
        self.send({"t": "hello", "app": "MyTE", "v": 1})
        try:
            self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        try:
            while True:
                n = struct.unpack(">I", recv_exact(self.conn, 4))[0]
                if n > self.srv.frame_limit():
                    raise ConnectionError("trama demasiado grande")
                msg = json.loads(recv_exact(self.conn, n).decode("utf-8"))
                self.srv.dispatch(self, msg)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            self.srv.session_closed(self)
            self.close()


# ------------------------------------------------------------------ servidor
class MyTEServer:
    def __init__(self, log=print):
        self._log = log
        self.lock = threading.RLock()
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.settings = load_settings()
        self.sessions = {}
        self.all_sessions = set()
        self.sock = None
        self.running = False
        self.stop_evt = threading.Event()
        self.last_save = None

    # --- básicos
    def log(self, msg):
        self._log(f"[{datetime.now():%H:%M:%S}] {msg}")

    def q(self, sql, a=()):
        with self.lock:
            return self.db.execute(sql, a).fetchall()

    def one(self, sql, a=()):
        with self.lock:
            return self.db.execute(sql, a).fetchone()

    def ex(self, sql, a=()):
        with self.lock:
            cur = self.db.execute(sql, a)
            self.db.commit()
            return cur.lastrowid

    def frame_limit(self):
        return int(self.settings["max_file_mb"] * 1024 * 1024 * 1.4) + 2 * 1024 * 1024

    def is_online(self, uid):
        return bool(self.sessions.get(uid))

    def send_to(self, uid, obj):
        for s in list(self.sessions.get(uid, [])):
            s.send(obj)

    def uname(self, uid):
        r = self.one("SELECT username FROM users WHERE id=?", (uid,))
        return r["username"] if r else "Usuario eliminado"

    def related(self, uid):
        return {r[0] for r in self.q(
            "SELECT DISTINCT m2.user_id FROM members m1 JOIN members m2 ON m1.chat_id=m2.chat_id "
            "WHERE m1.user_id=?", (uid,)) if r[0] != uid}

    def role_of(self, chat, uid):
        r = self.one("SELECT role FROM members WHERE chat_id=? AND user_id=?", (chat, uid))
        return r["role"] if r else None

    def need_member(self, chat, uid):
        r = self.role_of(chat, uid)
        if not r:
            raise ClientError("No perteneces a este chat.")
        return r

    def need_admin(self, chat, uid):
        r = self.need_member(chat, uid)
        if r not in ("owner", "admin"):
            raise ClientError("Necesitas ser administrador del grupo.")
        return r

    def are_friends(self, a, b):
        x, y = min(a, b), max(a, b)
        return self.one("SELECT 1 FROM friends WHERE a=? AND b=?", (x, y)) is not None

    def is_blocked_pair(self, a, b):
        return self.one("SELECT 1 FROM blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",
                        (a, b, b, a)) is not None

    def chat_member_ids(self, chat):
        return [r[0] for r in self.q("SELECT user_id FROM members WHERE chat_id=?", (chat,))]

    def _avatar(self, v):
        if not v:
            return None
        if not isinstance(v, str) or len(v) > MAX_AVATAR_B64:
            raise ClientError("La imagen de perfil es demasiado grande.")
        return v

    # --- sincronización
    def build_sync(self, uid):
        users = {}

        def add(r):
            if r is not None:
                users[r["id"]] = {"id": r["id"], "username": r["username"], "avatar": r["avatar"],
                                  "online": self.is_online(r["id"])}
        with self.lock:
            add(self.one("SELECT * FROM users WHERE id=?", (uid,)))
            chats = []
            for c in self.q("SELECT c.* FROM chats c JOIN members m ON m.chat_id=c.id WHERE m.user_id=?", (uid,)):
                mem = []
                for r in self.q("SELECT u.*, m.role, m.nick FROM members m JOIN users u ON u.id=m.user_id "
                                "WHERE m.chat_id=?", (c["id"],)):
                    add(r)
                    mem.append({"id": r["id"], "role": r["role"], "nick": r["nick"]})
                lm = self.one("SELECT m.sender,m.kind,m.text,m.ts,f.name AS fname FROM messages m "
                              "LEFT JOIN files f ON f.id=m.file_id WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 1",
                              (c["id"],))
                last = None
                if lm:
                    last = {"sender": lm["sender"], "kind": lm["kind"], "ts": lm["ts"],
                            "text": lm["fname"] if lm["kind"] == "file" else lm["text"]}
                chats.append({"id": c["id"], "type": c["type"], "name": c["name"],
                              "description": c["description"], "avatar": c["avatar"],
                              "creator": c["creator"], "created": c["created"], "members": mem, "last": last})
            friends = []
            for f in self.q("SELECT * FROM friends WHERE a=? OR b=?", (uid, uid)):
                other = f["b"] if f["a"] == uid else f["a"]
                add(self.one("SELECT * FROM users WHERE id=?", (other,)))
                friends.append({"id": other, "chat": f["chat_id"]})
            req_in, req_out = [], []
            for r in self.q("SELECT * FROM requests WHERE to_id=?", (uid,)):
                add(self.one("SELECT * FROM users WHERE id=?", (r["from_id"],)))
                req_in.append({"id": r["id"], "user": r["from_id"]})
            for r in self.q("SELECT * FROM requests WHERE from_id=?", (uid,)):
                add(self.one("SELECT * FROM users WHERE id=?", (r["to_id"],)))
                req_out.append({"id": r["id"], "user": r["to_id"]})
            blocked = []
            for r in self.q("SELECT blocked FROM blocks WHERE blocker=?", (uid,)):
                add(self.one("SELECT * FROM users WHERE id=?", (r[0],)))
                blocked.append(r[0])
        return {"t": "sync", "me": uid, "users": users, "chats": chats, "friends": friends,
                "req_in": req_in, "req_out": req_out, "blocked": blocked,
                "limits": {"max_file_mb": self.settings["max_file_mb"]}}

    def push_sync(self, uids):
        for u in set(uids):
            if self.is_online(u):
                self.send_to(u, self.build_sync(u))

    def presence(self, uid, online):
        for r in self.related(uid):
            self.send_to(r, {"t": "presence", "id": uid, "online": online})

    def broadcast_msg(self, chat, mid):
        md = mdict(self.one(MSG_SQL + "WHERE m.id=?", (mid,)))
        for u in self.chat_member_ids(chat):
            self.send_to(u, {"t": "msg", "chat": chat, "msg": md})

    def add_system(self, chat, text):
        mid = self.ex("INSERT INTO messages(chat_id,sender,kind,text,ts) VALUES(?,?,?,?,?)",
                      (chat, 0, "system", text, now()))
        self.broadcast_msg(chat, mid)

    def push_tasks(self, chat):
        ts = [tdict(r) for r in self.q("SELECT * FROM tasks WHERE chat_id=? ORDER BY id", (chat,))]
        for u in self.chat_member_ids(chat):
            self.send_to(u, {"t": "tasks", "chat": chat, "tasks": ts})

    # --- despacho
    def dispatch(self, s, m):
        t = m.get("t")
        try:
            if not isinstance(t, str) or not t.isidentifier():
                raise ClientError("Petición no válida.")
            if s.uid is None:
                if t not in ("register", "login"):
                    raise ClientError("Debes iniciar sesión.")
            elif t in ("register", "login"):
                raise ClientError("Ya has iniciado sesión.")
            fn = getattr(self, "h_" + t, None)
            if fn is None:
                raise ClientError("Petición desconocida.")
            fn(s, m)
        except ClientError as e:
            s.send({"t": "error", "text": str(e), "ref": t})
        except Exception as e:
            self.log(f"ERROR en '{t}': {e}\n{traceback.format_exc()}")
            s.send({"t": "error", "text": "Error interno del servidor.", "ref": t})

    def attach(self, s, uid):
        s.uid = uid
        lst = self.sessions.setdefault(uid, [])
        lst.append(s)
        s.send({"t": "logged", "id": uid, "username": self.uname(uid)})
        s.send(self.build_sync(uid))
        if len(lst) == 1:
            self.presence(uid, True)
        self.log(f"{self.uname(uid)} conectado desde {s.ip}")

    def session_closed(self, s):
        self.all_sessions.discard(s)
        if s.uid is not None:
            lst = self.sessions.get(s.uid, [])
            if s in lst:
                lst.remove(s)
            if not lst:
                self.sessions.pop(s.uid, None)
                self.presence(s.uid, False)
            self.log(f"{self.uname(s.uid)} desconectado ({s.ip})")

    # --- cuentas
    def h_register(self, s, m):
        u, pw = str(m.get("user", "")).strip(), str(m.get("pw", ""))
        if not USERNAME_RE.match(u):
            raise ClientError("El usuario debe tener 3-20 caracteres (letras, números, . _ -).")
        if len(pw) < 4:
            raise ClientError("La contraseña debe tener al menos 4 caracteres.")
        av = self._avatar(m.get("avatar"))
        salt, h = hash_pw(pw)
        try:
            uid = self.ex("INSERT INTO users(username,salt,pw_hash,avatar,created) VALUES(?,?,?,?,?)",
                          (u, salt, h, av, now()))
        except sqlite3.IntegrityError:
            raise ClientError("Ese nombre de usuario ya existe.")
        self.log(f"Nuevo usuario registrado: {u}")
        self.attach(s, uid)

    def h_login(self, s, m):
        r = self.one("SELECT * FROM users WHERE username=?", (str(m.get("user", "")).strip(),))
        pw = str(m.get("pw", ""))
        if not r or not hmac.compare_digest(hash_pw(pw, r["salt"])[1], r["pw_hash"]):
            time.sleep(0.4)
            raise ClientError("Usuario o contraseña incorrectos.")
        self.attach(s, r["id"])

    def h_update_profile(self, s, m):
        av = self._avatar(m.get("avatar")) if "avatar" in m else None
        self.ex("UPDATE users SET avatar=? WHERE id=?", (av, s.uid))
        self.push_sync({s.uid} | self.related(s.uid))

    def h_change_username(self, s, m):
        r = self.one("SELECT * FROM users WHERE id=?", (s.uid,))
        if not hmac.compare_digest(hash_pw(str(m.get("pw", "")), r["salt"])[1], r["pw_hash"]):
            raise ClientError("Contraseña incorrecta.")
        u = str(m.get("user", "")).strip()
        if not USERNAME_RE.match(u):
            raise ClientError("El usuario debe tener 3-20 caracteres (letras, números, . _ -).")
        try:
            self.ex("UPDATE users SET username=? WHERE id=?", (u, s.uid))
        except sqlite3.IntegrityError:
            raise ClientError("Ese nombre de usuario ya existe.")
        s.send({"t": "info", "text": "Nombre de usuario cambiado."})
        self.push_sync({s.uid} | self.related(s.uid))

    def h_change_password(self, s, m):
        r = self.one("SELECT * FROM users WHERE id=?", (s.uid,))
        if not hmac.compare_digest(hash_pw(str(m.get("old", "")), r["salt"])[1], r["pw_hash"]):
            raise ClientError("La contraseña actual no es correcta.")
        new = str(m.get("new", ""))
        if len(new) < 4:
            raise ClientError("La contraseña debe tener al menos 4 caracteres.")
        salt, h = hash_pw(new)
        self.ex("UPDATE users SET salt=?, pw_hash=? WHERE id=?", (salt, h, s.uid))
        s.send({"t": "info", "text": "Contraseña cambiada."})

    # --- amistades / DMs
    def make_dm(self, a, b):
        with self.lock:
            cid = self.ex("INSERT INTO chats(type,creator,created) VALUES('dm',NULL,?)", (now(),))
            for u in (a, b):
                self.ex("INSERT INTO members(chat_id,user_id,role,joined) VALUES(?,?,?,?)", (cid, u, "member", now()))
            self.ex("INSERT INTO friends(a,b,chat_id) VALUES(?,?,?)", (min(a, b), max(a, b), cid))
        return cid

    def h_friend_request(self, s, m):
        target = str(m.get("target", "")).strip()
        tid = None
        if IP_RE.match(target):
            for uid, lst in self.sessions.items():
                if uid != s.uid and any(x.ip == target for x in lst):
                    tid = uid
                    break
            if tid is None:
                raise ClientError("No hay ningún usuario conectado con esa IP.")
        else:
            r = self.one("SELECT id FROM users WHERE username=?", (target,))
            if not r:
                raise ClientError("No se encontró a ese usuario.")
            tid = r["id"]
        if tid == s.uid:
            raise ClientError("No puedes enviarte una petición a ti mismo.")
        if self.are_friends(s.uid, tid):
            raise ClientError("Ya sois contactos.")
        if self.one("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (s.uid, tid)):
            raise ClientError("Has bloqueado a este usuario. Desbloquéalo primero.")
        if self.one("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (tid, s.uid)):
            raise ClientError("No se pudo enviar la petición.")
        if self.one("SELECT 1 FROM requests WHERE from_id=? AND to_id=?", (s.uid, tid)):
            raise ClientError("Ya le enviaste una petición.")
        rev = self.one("SELECT id FROM requests WHERE from_id=? AND to_id=?", (tid, s.uid))
        if rev:                                  # petición cruzada: se acepta sola
            self.ex("DELETE FROM requests WHERE id=?", (rev["id"],))
            self.make_dm(s.uid, tid)
        else:
            self.ex("INSERT INTO requests(from_id,to_id,ts) VALUES(?,?,?)", (s.uid, tid, now()))
            self.send_to(tid, {"t": "info", "text": f"{self.uname(s.uid)} te ha enviado una petición de chat."})
        s.send({"t": "info", "text": f"Petición enviada a {self.uname(tid)}."})
        self.push_sync([s.uid, tid])

    def h_friend_respond(self, s, m):
        r = self.one("SELECT * FROM requests WHERE id=? AND to_id=?", (int(m.get("id", 0)), s.uid))
        if not r:
            raise ClientError("La petición ya no existe.")
        self.ex("DELETE FROM requests WHERE id=?", (r["id"],))
        if m.get("accept"):
            if not self.are_friends(s.uid, r["from_id"]) and not self.is_blocked_pair(s.uid, r["from_id"]):
                self.make_dm(s.uid, r["from_id"])
            self.send_to(r["from_id"], {"t": "info", "text": f"{self.uname(s.uid)} aceptó tu petición."})
        else:
            self.send_to(r["from_id"], {"t": "info", "text": f"{self.uname(s.uid)} rechazó tu petición."})
        self.push_sync([s.uid, r["from_id"]])

    def h_friend_cancel(self, s, m):
        r = self.one("SELECT * FROM requests WHERE id=? AND from_id=?", (int(m.get("id", 0)), s.uid))
        if r:
            self.ex("DELETE FROM requests WHERE id=?", (r["id"],))
            self.push_sync([s.uid, r["to_id"]])

    def h_block(self, s, m):
        t = int(m.get("user", 0))
        if t == s.uid or not self.one("SELECT 1 FROM users WHERE id=?", (t,)):
            raise ClientError("Usuario no válido.")
        self.ex("INSERT OR IGNORE INTO blocks(blocker,blocked) VALUES(?,?)", (s.uid, t))
        self.ex("DELETE FROM requests WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)", (s.uid, t, t, s.uid))
        self.push_sync([s.uid, t])

    def h_unblock(self, s, m):
        self.ex("DELETE FROM blocks WHERE blocker=? AND blocked=?", (s.uid, int(m.get("user", 0))))
        self.push_sync([s.uid])

    def h_remove_friend(self, s, m):
        t = int(m.get("user", 0))
        f = self.one("SELECT chat_id FROM friends WHERE a=? AND b=?", (min(s.uid, t), max(s.uid, t)))
        if not f:
            raise ClientError("Esa persona no está en tus contactos.")
        ids = self._delete_chat(f["chat_id"])
        self.ex("DELETE FROM friends WHERE a=? AND b=?", (min(s.uid, t), max(s.uid, t)))
        self.push_sync(ids)

    # --- mensajes y archivos
    def store_file(self, uid, name, raw):
        size = len(raw)
        if size > self.settings["max_file_mb"] * 1024 * 1024:
            raise ClientError(f"El archivo supera el límite de {self.settings['max_file_mb']} MB.")
        used = self.one("SELECT COALESCE(SUM(size),0) FROM files")[0]
        if used + size > self.settings["max_storage_mb"] * 1024 * 1024:
            raise ClientError("El servidor ha alcanzado su límite de almacenamiento.")
        path = secrets.token_hex(12)
        with open(os.path.join(FILES_DIR, path), "wb") as f:
            f.write(raw)
        safe = os.path.basename(str(name).replace("\\", "/"))[:120] or "archivo"
        return self.ex("INSERT INTO files(name,size,path,uploader,ts) VALUES(?,?,?,?,?)", (safe, size, path, uid, now()))

    def h_send_msg(self, s, m):
        chat = int(m.get("chat", 0))
        self.need_member(chat, s.uid)
        c = self.one("SELECT type FROM chats WHERE id=?", (chat,))
        if c["type"] == "dm":
            other = self.one("SELECT user_id FROM members WHERE chat_id=? AND user_id!=?", (chat, s.uid))
            if other and self.is_blocked_pair(s.uid, other[0]):
                raise ClientError("No puedes enviar mensajes a este usuario.")
        kind, text, fid = m.get("kind", "text"), str(m.get("text", ""))[:4000], None
        if kind in ("image", "file"):
            try:
                raw = base64.b64decode(m.get("data", ""), validate=True)
            except Exception:
                raise ClientError("Archivo no válido.")
            fid = self.store_file(s.uid, m.get("name", "archivo"), raw)
        elif kind == "text":
            if not text.strip():
                return
        else:
            raise ClientError("Tipo de mensaje no válido.")
        mid = self.ex("INSERT INTO messages(chat_id,sender,kind,text,file_id,ts) VALUES(?,?,?,?,?,?)",
                      (chat, s.uid, kind, text, fid, now()))
        self.broadcast_msg(chat, mid)

    def h_history(self, s, m):
        chat = int(m.get("chat", 0))
        self.need_member(chat, s.uid)
        rows = self.q(MSG_SQL + "WHERE m.chat_id=? ORDER BY m.id DESC LIMIT 500", (chat,))
        tasks = [tdict(r) for r in self.q("SELECT * FROM tasks WHERE chat_id=? ORDER BY id", (chat,))]
        s.send({"t": "history", "chat": chat, "msgs": [mdict(r) for r in reversed(rows)], "tasks": tasks})

    def h_get_file(self, s, m):
        fid = int(m.get("id", 0))
        ok = self.one("SELECT 1 FROM messages m JOIN members mm ON mm.chat_id=m.chat_id "
                      "WHERE m.file_id=? AND mm.user_id=?", (fid, s.uid))
        f = self.one("SELECT * FROM files WHERE id=?", (fid,))
        if not ok or not f or not os.path.exists(os.path.join(FILES_DIR, f["path"])):
            s.send({"t": "file", "id": fid, "missing": True})
            return
        with open(os.path.join(FILES_DIR, f["path"]), "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        s.send({"t": "file", "id": fid, "name": f["name"], "data": data})

    # --- grupos
    def h_create_group(self, s, m):
        name = str(m.get("name", "")).strip()[:40]
        if not name:
            raise ClientError("El grupo necesita un nombre.")
        desc, av = str(m.get("desc", ""))[:300], self._avatar(m.get("avatar"))
        added = [s.uid]
        with self.lock:
            cid = self.ex("INSERT INTO chats(type,name,description,avatar,creator,created) VALUES('group',?,?,?,?,?)",
                          (name, desc, av, s.uid, now()))
            self.ex("INSERT INTO members(chat_id,user_id,role,joined) VALUES(?,?,?,?)", (cid, s.uid, "owner", now()))
            for u in m.get("members", []):
                u = int(u)
                if u != s.uid and self.are_friends(s.uid, u):
                    self.ex("INSERT INTO members(chat_id,user_id,role,joined) VALUES(?,?,?,?)", (cid, u, "member", now()))
                    added.append(u)
        self.push_sync(added)
        self.add_system(cid, f"{self.uname(s.uid)} ha creado el grupo «{name}».")

    def h_edit_group(self, s, m):
        chat = int(m.get("chat", 0))
        self.need_admin(chat, s.uid)
        if self.one("SELECT type FROM chats WHERE id=?", (chat,))["type"] != "group":
            raise ClientError("Solo los grupos se pueden editar.")
        if "name" in m:
            name = str(m["name"]).strip()[:40]
            if not name:
                raise ClientError("El nombre no puede estar vacío.")
            self.ex("UPDATE chats SET name=? WHERE id=?", (name, chat))
        if "desc" in m:
            self.ex("UPDATE chats SET description=? WHERE id=?", (str(m["desc"])[:300], chat))
        if "avatar" in m:
            self.ex("UPDATE chats SET avatar=? WHERE id=?", (self._avatar(m["avatar"]), chat))
        self.push_sync(self.chat_member_ids(chat))

    def _group_admin_target(self, s, m):
        chat, target = int(m.get("chat", 0)), int(m.get("user", 0))
        my = self.need_admin(chat, s.uid)
        tr = self.role_of(chat, target)
        if not tr:
            raise ClientError("Esa persona no está en el grupo.")
        return chat, target, my, tr

    def h_invite(self, s, m):
        chat, target = int(m.get("chat", 0)), int(m.get("user", 0))
        self.need_admin(chat, s.uid)
        if self.one("SELECT type FROM chats WHERE id=?", (chat,))["type"] != "group":
            raise ClientError("Solo se puede invitar a grupos.")
        if not self.are_friends(s.uid, target):
            raise ClientError("Solo puedes invitar a personas que tengas como contactos.")
        if self.role_of(chat, target):
            raise ClientError("Ya está en el grupo.")
        self.ex("INSERT INTO members(chat_id,user_id,role,joined) VALUES(?,?,?,?)", (chat, target, "member", now()))
        self.push_sync(self.chat_member_ids(chat))
        self.add_system(chat, f"{self.uname(s.uid)} ha añadido a {self.uname(target)}.")

    def h_kick(self, s, m):
        chat, target, my, tr = self._group_admin_target(s, m)
        if target == s.uid:
            raise ClientError("Usa «Salir del grupo».")
        if tr == "owner":
            raise ClientError("No se puede echar al administrador principal.")
        if tr == "admin" and my != "owner":
            raise ClientError("Solo el administrador principal puede echar a otros administradores.")
        before = self.chat_member_ids(chat)
        self.ex("DELETE FROM members WHERE chat_id=? AND user_id=?", (chat, target))
        self.push_sync(before)
        self.add_system(chat, f"{self.uname(s.uid)} ha echado a {self.uname(target)}.")

    def h_set_role(self, s, m):
        chat, target, my, tr = self._group_admin_target(s, m)
        if my != "owner":
            raise ClientError("Solo el administrador principal puede cambiar los roles.")
        if tr == "owner":
            raise ClientError("No se puede cambiar el rol del administrador principal.")
        role = "admin" if m.get("role") == "admin" else "member"
        self.ex("UPDATE members SET role=? WHERE chat_id=? AND user_id=?", (role, chat, target))
        self.push_sync(self.chat_member_ids(chat))
        self.add_system(chat, f"{self.uname(target)} ahora es {'administrador' if role == 'admin' else 'miembro'}.")

    def h_set_nick(self, s, m):
        chat, target = int(m.get("chat", 0)), int(m.get("user", 0))
        self.need_member(chat, s.uid)
        if target != s.uid:
            self.need_admin(chat, s.uid)
        if not self.role_of(chat, target):
            raise ClientError("Esa persona no está en el grupo.")
        nick = str(m.get("nick", "")).strip()[:24] or None
        self.ex("UPDATE members SET nick=? WHERE chat_id=? AND user_id=?", (nick, chat, target))
        self.push_sync(self.chat_member_ids(chat))

    def h_leave_group(self, s, m):
        chat = int(m.get("chat", 0))
        role = self.need_member(chat, s.uid)
        if self.one("SELECT type FROM chats WHERE id=?", (chat,))["type"] != "group":
            raise ClientError("Esto no es un grupo.")
        if role == "owner":
            raise ClientError("El administrador principal no puede salir. Elimina el grupo si quieres cerrarlo.")
        before = self.chat_member_ids(chat)
        self.ex("DELETE FROM members WHERE chat_id=? AND user_id=?", (chat, s.uid))
        self.push_sync(before)
        self.add_system(chat, f"{self.uname(s.uid)} ha salido del grupo.")

    def h_delete_group(self, s, m):
        chat = int(m.get("chat", 0))
        self.need_admin(chat, s.uid)
        if self.one("SELECT type FROM chats WHERE id=?", (chat,))["type"] != "group":
            raise ClientError("Esto no es un grupo.")
        ids = self._delete_chat(chat)
        self.log(f"Grupo {chat} eliminado por {self.uname(s.uid)}")
        self.push_sync(ids)

    # --- tareas
    def h_task_add(self, s, m):
        chat = int(m.get("chat", 0))
        self.need_member(chat, s.uid)
        title = str(m.get("title", "")).strip()[:120]
        if not title:
            raise ClientError("La tarea necesita un título.")
        self.ex("INSERT INTO tasks(chat_id,title,description,creator,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (chat, title, str(m.get("desc", ""))[:600], s.uid, "pending", now(), now()))
        self.push_tasks(chat)
        self.add_system(chat, f"📝 {self.uname(s.uid)} creó la tarea «{title}».")

    def h_task_update(self, s, m):
        t = self.one("SELECT * FROM tasks WHERE id=?", (int(m.get("id", 0)),))
        if not t:
            raise ClientError("La tarea ya no existe.")
        role = self.need_member(t["chat_id"], s.uid)
        admin = role in ("owner", "admin")
        mine = t["assignee"] == s.uid
        act, st, title = m.get("action"), t["status"], t["title"]
        me = self.uname(s.uid)
        if act == "accept" and st == "pending":
            self.ex("UPDATE tasks SET status='in_progress', assignee=?, updated=? WHERE id=?", (s.uid, now(), t["id"]))
            msg = f"🚧 {me} aceptó la tarea «{title}»."
        elif act == "release" and st == "in_progress" and (mine or admin):
            self.ex("UPDATE tasks SET status='pending', assignee=NULL, updated=? WHERE id=?", (now(), t["id"]))
            msg = f"↩️ {me} soltó la tarea «{title}»."
        elif act == "done" and st == "in_progress" and (mine or admin):
            self.ex("UPDATE tasks SET status='done', updated=? WHERE id=?", (now(), t["id"]))
            msg = f"✅ {me} terminó la tarea «{title}»."
        elif act == "reopen" and st == "done" and (mine or admin):
            self.ex("UPDATE tasks SET status='in_progress', updated=? WHERE id=?", (now(), t["id"]))
            msg = f"🔄 {me} reabrió la tarea «{title}»."
        elif act == "delete" and (t["creator"] == s.uid or admin):
            self.ex("DELETE FROM tasks WHERE id=?", (t["id"],))
            msg = None
        else:
            raise ClientError("No puedes hacer eso con esta tarea.")
        self.push_tasks(t["chat_id"])
        if msg:
            self.add_system(t["chat_id"], msg)

    # --- borrado
    def _delete_file(self, fid):
        with self.lock:
            f = self.one("SELECT path FROM files WHERE id=?", (fid,))
            if f:
                try:
                    os.remove(os.path.join(FILES_DIR, f["path"]))
                except OSError:
                    pass
                self.ex("DELETE FROM files WHERE id=?", (fid,))

    def _delete_chat(self, cid):
        with self.lock:
            ids = self.chat_member_ids(cid)
            for r in self.q("SELECT DISTINCT file_id FROM messages WHERE chat_id=? AND file_id IS NOT NULL", (cid,)):
                self._delete_file(r[0])
            for tb in ("messages", "tasks", "members", "friends"):
                self.ex(f"DELETE FROM {tb} WHERE chat_id=?", (cid,))
            self.ex("DELETE FROM chats WHERE id=?", (cid,))
        return ids

    # ================================================= API para el panel admin
    def admin_users(self):
        out = []
        for r in self.q("SELECT id,username,created FROM users ORDER BY id"):
            ss = self.sessions.get(r["id"], [])
            out.append((r["id"], r["username"], "conectado" if ss else "desconectado",
                        ", ".join(sorted({x.ip for x in ss})), ts_str(r["created"])))
        return out

    def admin_kick(self, uid):
        for s in list(self.sessions.get(uid, [])):
            s.close()

    def admin_set_password(self, uid, pw):
        salt, h = hash_pw(pw)
        self.ex("UPDATE users SET salt=?, pw_hash=? WHERE id=?", (salt, h, uid))

    def admin_delete_user(self, uid):
        with self.lock:
            affected = self.related(uid)
            for c in self.q("SELECT c.id,c.type FROM chats c JOIN members m ON m.chat_id=c.id WHERE m.user_id=?", (uid,)):
                if c["type"] == "dm":
                    self._delete_chat(c["id"])
                    continue
                rest = self.q("SELECT user_id,role FROM members WHERE chat_id=? AND user_id!=? "
                              "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined", (c["id"], uid))
                if not rest:
                    self._delete_chat(c["id"])
                    continue
                if self.role_of(c["id"], uid) == "owner":
                    self.ex("UPDATE members SET role='owner' WHERE chat_id=? AND user_id=?", (c["id"], rest[0]["user_id"]))
                self.ex("DELETE FROM members WHERE chat_id=? AND user_id=?", (c["id"], uid))
            self.ex("DELETE FROM requests WHERE from_id=? OR to_id=?", (uid, uid))
            self.ex("DELETE FROM blocks WHERE blocker=? OR blocked=?", (uid, uid))
            self.ex("DELETE FROM friends WHERE a=? OR b=?", (uid, uid))
            self.ex("DELETE FROM users WHERE id=?", (uid,))
        self.admin_kick(uid)
        self.push_sync(affected)
        self.log(f"Usuario {uid} eliminado")

    def _chat_label(self, c):
        if c["type"] == "dm":
            names = [self.uname(u) for u in self.chat_member_ids(c["id"])]
            return " ↔ ".join(names)
        return c["name"]

    def admin_chats(self):
        out = []
        for c in self.q("SELECT * FROM chats ORDER BY id"):
            mc = self.one("SELECT COUNT(*) FROM members WHERE chat_id=?", (c["id"],))[0]
            n = self.one("SELECT COUNT(*) FROM messages WHERE chat_id=?", (c["id"],))[0]
            out.append((c["id"], "DM" if c["type"] == "dm" else "Grupo", self._chat_label(c), mc, n))
        return out

    def admin_chat_dump(self, cid):
        c = self.one("SELECT * FROM chats WHERE id=?", (cid,))
        if not c:
            return "(chat inexistente)"
        L = [f"=== {self._chat_label(c)}  (id {cid}, {'DM' if c['type'] == 'dm' else 'grupo'}) ==="]
        if c["description"]:
            L.append(f"Descripción: {c['description']}")
        for r in self.q("SELECT user_id,role,nick FROM members WHERE chat_id=?", (cid,)):
            L.append(f"  · {self.uname(r['user_id'])}  [{r['role']}]" + (f"  mote: {r['nick']}" if r["nick"] else ""))
        ts = self.q("SELECT * FROM tasks WHERE chat_id=? ORDER BY id", (cid,))
        if ts:
            L.append("\n--- Tareas ---")
            for t in ts:
                who = self.uname(t["assignee"]) if t["assignee"] else "-"
                L.append(f"  [{t['status']}] {t['title']}  (asignada a: {who})")
        L.append("\n--- Mensajes ---")
        for r in self.q(MSG_SQL + "WHERE m.chat_id=? ORDER BY m.id", (cid,)):
            who = "sistema" if r["sender"] == 0 else self.uname(r["sender"])
            body = r["text"] or ""
            if r["kind"] in ("image", "file"):
                body = f"[{r['kind']}: {r['fname'] or 'eliminado'}] {body}"
            L.append(f"[{ts_str(r['ts'])}] {who}: {body}")
        return "\n".join(L)

    def admin_delete_chat(self, cid):
        ids = self._delete_chat(cid)
        self.push_sync(ids)
        self.log(f"Chat {cid} eliminado desde el panel")

    def storage_info(self):
        r = self.one("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files")
        return r[0], r[1]

    def admin_files(self):
        return [(r["id"], r["name"], f"{r['size'] / 1024:.1f} KB", self.uname(r["uploader"]), ts_str(r["ts"]))
                for r in self.q("SELECT * FROM files ORDER BY size DESC LIMIT 500")]

    def admin_delete_file(self, fid):
        self._delete_file(fid)

    def purge_orphans(self):
        n = 0
        with self.lock:
            for f in self.q("SELECT id FROM files WHERE id NOT IN (SELECT file_id FROM messages WHERE file_id IS NOT NULL)"):
                self._delete_file(f[0])
                n += 1
            known = {r[0] for r in self.q("SELECT path FROM files")}
            for name in os.listdir(FILES_DIR):
                if name not in known:
                    os.remove(os.path.join(FILES_DIR, name))
                    n += 1
        return n

    # --- snapshots
    def make_snapshot(self, cid, label=""):
        with self.lock:
            c = self.one("SELECT * FROM chats WHERE id=?", (cid,))
            if not c:
                raise ClientError("El chat no existe.")
            data = {"version": 1, "created": now(), "label": label, "chat": dict(c),
                    "chat_label": self._chat_label(c),
                    "members": [dict(r) for r in self.q("SELECT * FROM members WHERE chat_id=?", (cid,))],
                    "messages": [dict(r) for r in self.q("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (cid,))],
                    "tasks": [dict(r) for r in self.q("SELECT * FROM tasks WHERE chat_id=? ORDER BY id", (cid,))]}
        path = os.path.join(SNAP_DIR, f"snap_{cid}_{int(now())}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.log(f"Snapshot creado: {os.path.basename(path)}")
        return path

    def admin_snapshots(self):
        out = []
        for name in sorted(os.listdir(SNAP_DIR), reverse=True):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(SNAP_DIR, name), encoding="utf-8") as f:
                    d = json.load(f)
                out.append((name, d.get("chat_label", "?"), d.get("label", ""), ts_str(d["created"]),
                            len(d["messages"]), len(d["tasks"])))
            except Exception:
                continue
        return out

    def restore_snapshot(self, name):
        with open(os.path.join(SNAP_DIR, os.path.basename(name)), encoding="utf-8") as f:
            d = json.load(f)
        cid = d["chat"]["id"]
        with self.lock:
            if not self.one("SELECT 1 FROM chats WHERE id=?", (cid,)):
                raise ClientError("El chat original ya no existe; no se puede restaurar.")
            self.db.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
            self.db.execute("DELETE FROM tasks WHERE chat_id=?", (cid,))
            for m in d["messages"]:
                self.db.execute("INSERT INTO messages(id,chat_id,sender,kind,text,file_id,ts) VALUES(?,?,?,?,?,?,?)",
                                (m["id"], cid, m["sender"], m["kind"], m["text"], m["file_id"], m["ts"]))
            for t in d["tasks"]:
                self.db.execute("INSERT INTO tasks(id,chat_id,title,description,creator,assignee,status,created,updated) "
                                "VALUES(?,?,?,?,?,?,?,?,?)",
                                (t["id"], cid, t["title"], t["description"], t["creator"], t["assignee"],
                                 t["status"], t["created"], t["updated"]))
            if d["chat"]["type"] == "group":
                self.db.execute("UPDATE chats SET name=?, description=?, avatar=? WHERE id=?",
                                (d["chat"]["name"], d["chat"]["description"], d["chat"]["avatar"], cid))
            self.db.commit()
        ids = self.chat_member_ids(cid)
        self.push_sync(ids)
        for u in ids:
            self.send_to(u, {"t": "reload", "chat": cid})
        self.log(f"Snapshot restaurado en el chat {cid}: {name}")

    # --- guardado / ciclo de vida
    def save_all(self, reason="autoguardado"):
        try:
            with self.lock:
                self.db.commit()
                dst = sqlite3.connect(os.path.join(BACKUP_DIR, f"myte_{datetime.now():%Y%m%d_%H%M%S}.db"))
                self.db.backup(dst)
                dst.close()
            for f in sorted(x for x in os.listdir(BACKUP_DIR) if x.endswith(".db"))[:-5]:
                os.remove(os.path.join(BACKUP_DIR, f))
            save_settings(self.settings)
            self.last_save = time.time()
            self.log(f"Datos guardados ({reason}).")
        except Exception as e:
            self.log(f"Error al guardar: {e}")

    def _autosave_loop(self):
        while not self.stop_evt.wait(max(1, int(self.settings["autosave_min"])) * 60):
            self.save_all("autoguardado")

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(None)
            Session(self, conn, addr).start()

    def start(self, port):
        if self.running:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", int(port)))
        s.listen(64)
        s.settimeout(1.0)
        self.sock, self.running = s, True
        self.settings["port"] = int(port)
        save_settings(self.settings)
        self.stop_evt.clear()
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._autosave_loop, daemon=True).start()
        self.log(f"Servidor escuchando en 0.0.0.0:{port}  (IPs locales: {', '.join(local_ips())})")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.stop_evt.set()
        try:
            self.sock.close()
        except OSError:
            pass
        for s in list(self.all_sessions):
            s.close()
        self.save_all("apagado del servidor")
        self.log("Servidor detenido.")


# ------------------------------------------------------------------ panel tk
class ServerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MyTE Server")
        self.root.geometry("1000x660")
        self.logq = queue.Queue()
        self.srv = MyTEServer(self.logq.put)
        ttk.Style().theme_use("clam")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="MyTE Server", font=("TkDefaultFont", 16, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="● detenido", foreground="#b00020")
        self.status.pack(side="right")
        self.btn = ttk.Button(top, text="Iniciar servidor", command=self.toggle)
        self.btn.pack(side="right", padx=8)
        self.port = tk.StringVar(value=str(self.srv.settings["port"]))
        ttk.Entry(top, textvariable=self.port, width=7).pack(side="right")
        ttk.Label(top, text="Puerto:").pack(side="right", padx=(12, 2))
        ttk.Label(top, text="IP(s) para los clientes: " + "  |  ".join(local_ips())).pack(side="right", padx=12)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.build_users()
        self.build_chats()
        self.build_storage()
        self.build_snaps()
        self.build_log()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.pump)
        self.root.after(1000, self.tick)
        self.tick_n = 0

    def tree(self, parent, cols, widths):
        f = ttk.Frame(parent)
        t = ttk.Treeview(f, columns=[c for c, _ in zip(cols, widths)], show="headings", selectmode="browse")
        for c, w in zip(cols, widths):
            t.heading(c, text=c)
            t.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return f, t

    def fill(self, tree, rows):
        sel = tree.selection()
        sel_id = tree.item(sel[0])["values"][0] if sel else None
        tree.delete(*tree.get_children())
        for r in rows:
            iid = tree.insert("", "end", values=r)
            if sel_id is not None and str(r[0]) == str(sel_id):
                tree.selection_set(iid)

    def selected(self, tree):
        s = tree.selection()
        return tree.item(s[0])["values"] if s else None

    # -- pestañas
    def build_users(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Usuarios")
        tf, self.t_users = self.tree(f, ("ID", "Usuario", "Estado", "IP", "Creado"), (50, 200, 110, 160, 150))
        tf.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        for txt, cmd in (("Desconectar", self.u_kick), ("Cambiar contraseña", self.u_pw), ("Eliminar usuario", self.u_del)):
            ttk.Button(bar, text=txt, command=cmd).pack(side="left", padx=4)

    def u_kick(self):
        v = self.selected(self.t_users)
        if v:
            self.srv.admin_kick(int(v[0]))

    def u_pw(self):
        v = self.selected(self.t_users)
        if v:
            pw = simpledialog.askstring("Nueva contraseña", f"Nueva contraseña para {v[1]}:", show="*")
            if pw and len(pw) >= 4:
                self.srv.admin_set_password(int(v[0]), pw)
                messagebox.showinfo("MyTE", "Contraseña cambiada.")

    def u_del(self):
        v = self.selected(self.t_users)
        if v and messagebox.askyesno("Eliminar", f"¿Eliminar a {v[1]} y sus chats directos?"):
            self.srv.admin_delete_user(int(v[0]))
            self.refresh()

    def build_chats(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Chats")
        left = ttk.Frame(f)
        left.pack(side="left", fill="y")
        tf, self.t_chats = self.tree(left, ("ID", "Tipo", "Nombre", "Miembros", "Mensajes"), (40, 60, 180, 70, 70))
        tf.pack(fill="both", expand=True)
        self.t_chats.bind("<<TreeviewSelect>>", lambda e: self.show_chat())
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Snapshot", command=self.c_snap).pack(side="left", padx=2)
        ttk.Button(bar, text="Eliminar chat", command=self.c_del).pack(side="left", padx=2)
        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.txt = tk.Text(right, wrap="word", font="TkFixedFont", state="disabled")
        sb = ttk.Scrollbar(right, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def show_chat(self):
        v = self.selected(self.t_chats)
        if v:
            self.txt.configure(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.insert("end", self.srv.admin_chat_dump(int(v[0])))
            self.txt.configure(state="disabled")
            self.txt.see("end")

    def c_snap(self):
        v = self.selected(self.t_chats)
        if v:
            label = simpledialog.askstring("Snapshot", "Etiqueta (opcional):") or ""
            self.srv.make_snapshot(int(v[0]), label)
            self.refresh()

    def c_del(self):
        v = self.selected(self.t_chats)
        if v and messagebox.askyesno("Eliminar", f"¿Eliminar el chat «{v[2]}» con todo su contenido?"):
            self.srv.admin_delete_chat(int(v[0]))
            self.refresh()

    def build_storage(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Almacenamiento")
        box = ttk.LabelFrame(f, text="Límites y guardado", padding=8)
        box.pack(fill="x")
        self.v_file = tk.StringVar(value=str(self.srv.settings["max_file_mb"]))
        self.v_total = tk.StringVar(value=str(self.srv.settings["max_storage_mb"]))
        self.v_auto = tk.StringVar(value=str(self.srv.settings["autosave_min"]))
        for i, (txt, var) in enumerate((("Peso máximo por archivo (MB):", self.v_file),
                                        ("Almacenamiento total máximo (MB):", self.v_total),
                                        ("Autoguardado cada (minutos):", self.v_auto))):
            ttk.Label(box, text=txt).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(box, textvariable=var, width=10).grid(row=i, column=1, padx=8)
        ttk.Button(box, text="Aplicar", command=self.apply_limits).grid(row=0, column=2, padx=12)
        ttk.Button(box, text="Guardar ahora", command=lambda: self.srv.save_all("manual")).grid(row=1, column=2, padx=12)
        ttk.Button(box, text="Limpiar archivos huérfanos", command=self.purge).grid(row=2, column=2, padx=12)
        self.usage = ttk.Label(f, text="")
        self.usage.pack(anchor="w", pady=(8, 2))
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x")
        tf, self.t_files = self.tree(f, ("ID", "Archivo", "Tamaño", "Subido por", "Fecha"), (50, 300, 90, 140, 150))
        tf.pack(fill="both", expand=True, pady=8)
        ttk.Button(f, text="Eliminar archivo seleccionado", command=self.f_del).pack(anchor="w")

    def apply_limits(self):
        try:
            self.srv.settings["max_file_mb"] = max(1, int(self.v_file.get()))
            self.srv.settings["max_storage_mb"] = max(1, int(self.v_total.get()))
            self.srv.settings["autosave_min"] = max(1, int(self.v_auto.get()))
            save_settings(self.srv.settings)
            self.srv.log("Ajustes actualizados.")
        except ValueError:
            messagebox.showerror("MyTE", "Introduce números enteros.")

    def purge(self):
        n = self.srv.purge_orphans()
        messagebox.showinfo("MyTE", f"{n} archivo(s) huérfano(s) eliminados.")
        self.refresh()

    def f_del(self):
        v = self.selected(self.t_files)
        if v and messagebox.askyesno("Eliminar", f"¿Eliminar «{v[1]}»? Los mensajes mostrarán «archivo eliminado»."):
            self.srv.admin_delete_file(int(v[0]))
            self.refresh()

    def build_snaps(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Snapshots")
        tf, self.t_snaps = self.tree(f, ("Archivo", "Chat", "Etiqueta", "Creado", "Msgs", "Tareas"),
                                     (200, 200, 160, 140, 60, 60))
        tf.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Restaurar", command=self.s_restore).pack(side="left", padx=4)
        ttk.Button(bar, text="Eliminar", command=self.s_del).pack(side="left", padx=4)
        ttk.Button(bar, text="Snapshot de TODOS los chats", command=self.s_all).pack(side="left", padx=4)
        ttk.Label(f, text="Un snapshot guarda mensajes, tareas y miembros de un chat. "
                          "Restaurar reemplaza los mensajes y tareas actuales. Los adjuntos no se copian.",
                  wraplength=900).pack(anchor="w")

    def s_restore(self):
        v = self.selected(self.t_snaps)
        if v and messagebox.askyesno("Restaurar", "Esto reemplazará los mensajes y tareas actuales de ese chat. ¿Continuar?"):
            try:
                self.srv.restore_snapshot(v[0])
                messagebox.showinfo("MyTE", "Snapshot restaurado.")
            except ClientError as e:
                messagebox.showerror("MyTE", str(e))
            self.refresh()

    def s_del(self):
        v = self.selected(self.t_snaps)
        if v and messagebox.askyesno("Eliminar", "¿Eliminar este snapshot?"):
            os.remove(os.path.join(SNAP_DIR, v[0]))
            self.refresh()

    def s_all(self):
        label = simpledialog.askstring("Snapshot", "Etiqueta (opcional):") or ""
        for c in self.srv.admin_chats():
            self.srv.make_snapshot(c[0], label)
        self.refresh()

    def build_log(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="Registro")
        self.log = tk.Text(f, state="disabled", font="TkFixedFont")
        self.log.pack(fill="both", expand=True)

    # -- ciclo
    def toggle(self):
        if self.srv.running:
            self.srv.stop()
        else:
            try:
                self.srv.start(int(self.port.get()))
            except (OSError, ValueError) as e:
                messagebox.showerror("MyTE", f"No se pudo iniciar: {e}")
        self.update_status()

    def update_status(self):
        if self.srv.running:
            self.btn.configure(text="Detener servidor")
            self.status.configure(text="● en marcha", foreground="#1a8a1a")
        else:
            self.btn.configure(text="Iniciar servidor")
            self.status.configure(text="● detenido", foreground="#b00020")

    def refresh(self):
        self.fill(self.t_users, self.srv.admin_users())
        self.fill(self.t_chats, self.srv.admin_chats())
        self.fill(self.t_files, self.srv.admin_files())
        self.fill(self.t_snaps, [r for r in self.srv.admin_snapshots()])
        n, size = self.srv.storage_info()
        mx = self.srv.settings["max_storage_mb"] * 1024 * 1024
        self.usage.configure(text=f"{n} archivos · {size / 1048576:.1f} MB de {mx / 1048576:.0f} MB usados")
        self.bar["value"] = min(100, size * 100 / mx)

    def pump(self):
        try:
            while True:
                line = self.logq.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    def tick(self):
        self.tick_n += 1
        try:
            self.refresh()
        except Exception:
            pass
        self.root.after(3000, self.tick)

    def on_close(self):
        self.srv.stop()
        self.srv.save_all("cierre de la aplicación")
        self.root.destroy()

    def run(self):
        self.refresh()
        self.root.mainloop()


def run_headless(port):
    srv = MyTEServer(print)
    srv.start(port or srv.settings["port"])
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MyTE Server")
    ap.add_argument("--headless", action="store_true", help="sin interfaz gráfica (para VPS/servidores)")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    if a.headless or tk is None:
        run_headless(a.port)
    else:
        ServerGUI().run()
