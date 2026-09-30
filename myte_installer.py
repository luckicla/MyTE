#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyTE — Instalador
=================
Prepara todo lo necesario para usar MyTE en Windows, sin permisos de administrador:

  1. Descarga el instalador oficial de Python desde python.org
  2. Comprueba que está firmado por la «Python Software Foundation»
  3. Instala un Python PRIVADO para MyTE en  %LOCALAPPDATA%\\MyTE\\py
     (no toca tu Python, ni el PATH, ni necesita reiniciar)
  4. Instala PySide6 (interfaz del cliente)
  5. Copia myte_client.py / myte_server.py y crea los accesos directos

Puedes elegir la carpeta de instalación (por ejemplo otra unidad o una memoria USB si tu equipo
se restaura al reiniciar). En esa carpeta se crea "Abrir MyTE.bat", que funciona aunque cambie la
letra de la unidad y no depende de los accesos directos del menú Inicio.

Se compila con build_installer.bat  (PyInstaller).
Si junto al .exe hay un myte_client.py / myte_server.py, se usan esos en lugar de los incluidos,
así puedes actualizar MyTE sin recompilar el instalador.
"""
import base64
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

IS_WIN = os.name == "nt"
PY_VERSIONS = ["3.13.3", "3.12.10"]            # se prueban en orden
PACKAGES = ["PySide6-Essentials"]              # el cliente solo necesita QtCore/QtGui/QtWidgets
SIGNER = "Python Software Foundation"

DEFAULT_BASE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MyTE")
MIN_FREE_MB = 700                               # Python + PySide6 ocupan unos 500 MB
MAX_PATH_LEN = 70                               # margen para el límite de 260 caracteres de Windows


class Paths:
    """Todas las rutas cuelgan de la carpeta que elige la persona (ruta corta a propósito)."""

    def __init__(self, base):
        self.base = os.path.normpath(base)
        self.py_dir = os.path.join(self.base, "py")
        self.app_dir = os.path.join(self.base, "app")
        self.py_exe = os.path.join(self.py_dir, "python.exe")
        self.pyw_exe = os.path.join(self.py_dir, "pythonw.exe")
        self.log_path = os.path.join(self.base, "install.log")


NO_WINDOW = 0x08000000 if IS_WIN else 0        # CREATE_NO_WINDOW
APP_FILES = [("myte_client.py", True), ("myte_server.py", False), ("myte.ico", False)]   # (nombre, obligatorio)

STEPS = ["Descargar Python",
         "Verificar la firma de Python",
         "Instalar Python (privado para MyTE)",
         "Instalar PySide6 y dependencias",
         "Copiar MyTE y crear accesos directos"]


def check_base(base):
    """Devuelve un texto de error si la carpeta no sirve, o None si está bien."""
    if not base or not os.path.isabs(base):
        return "Escribe la ruta completa de la carpeta, por ejemplo  D:\\MyTE"
    if base.startswith("\\\\"):
        return "Elige una unidad de este equipo (no una ruta de red)."
    drive = os.path.splitdrive(base)[0]
    if not drive or not os.path.isdir(drive + "\\"):
        return f"La unidad {drive or '?'} no existe o no está disponible."
    if len(base) > MAX_PATH_LEN:
        return (f"La ruta es demasiado larga ({len(base)} caracteres; máximo {MAX_PATH_LEN}).\n"
                "Elige una más corta, por ejemplo  D:\\MyTE")
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".myte_prueba_escritura")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
    except OSError as e:
        return f"No puedo escribir en esa carpeta:\n{base}\n({e})"
    free = shutil.disk_usage(base).free
    if free < MIN_FREE_MB * 1048576:
        return f"No hay espacio suficiente en la unidad {drive}. Hacen falta al menos {MIN_FREE_MB} MB libres y hay {free // 1048576} MB."
    return None


def is_foreign(base):
    """True si la carpeta ya contiene archivos que no son de MyTE."""
    ours = {"py", "app", "install.log", "abrir myte.bat", "abrir myte servidor.bat"}
    try:
        return bool({n.lower() for n in os.listdir(base)} - ours)
    except OSError:
        return False


class InstallError(Exception):
    pass


def resource(name):
    """Busca un archivo junto al .exe (versión más nueva) o dentro del paquete."""
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    cands = [os.path.join(here, name)]
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, name))
    cands.append(os.path.join(os.getcwd(), name))
    return next((c for c in cands if os.path.isfile(c)), None)


def ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


# =============================================================== lógica
class Installer:
    """Toda la instalación. Habla con la interfaz mediante emit(evento, ...)."""

    def __init__(self, emit, desktop=True, server=False, base=None):
        self.emit, self.desktop, self.server = emit, desktop, server
        self.p = Paths(base or DEFAULT_BASE)
        os.makedirs(self.p.base, exist_ok=True)

    # -- utilidades
    def log(self, text, kind="info"):
        try:
            with open(self.p.log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        except OSError:
            pass
        self.emit("log", text, kind)

    def run(self, cmd, show=True, env=None):
        """Ejecuta un comando y va mostrando su salida. Devuelve el código de salida."""
        self.log("$ " + (cmd if isinstance(cmd, str) else " ".join(cmd)), "dim")
        e = dict(os.environ, PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        e.update(env or {})
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env=e)
        for line in p.stdout:
            line = line.rstrip()
            if line and show:
                self.log("   " + line[:160], "dim")
        return p.wait()

    def python_ok(self):
        if not os.path.isfile(self.p.py_exe):
            return False
        try:
            r = subprocess.run([self.p.py_exe, "-c", "import tkinter, ssl, sys; print(sys.version_info[:2])"],
                               capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            return r.returncode == 0
        except Exception:
            return False

    # -- pasos
    def download_one(self, dest_dir, v):
        """Descarga el instalador de Python `v`. Devuelve la ruta, o None si python.org no lo tiene."""
        url = f"https://www.python.org/ftp/python/{v}/python-{v}-amd64.exe"
        dest = os.path.join(dest_dir, f"python-{v}-amd64.exe")
        self.log(f"Descargando Python {v} desde python.org …")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MyTE-Installer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                total, got, t0 = int(r.headers.get("Content-Length") or 0), 0, time.time()
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        speed = got / max(time.time() - t0, 0.1) / 1024 / 1024
                        self.emit("progress", got * 100 / total,
                                  f"Descargando Python {v}: {got / 1048576:.1f} / {total / 1048576:.1f} MB  ({speed:.1f} MB/s)")
            if total and got != total:
                raise InstallError("La descarga quedó incompleta.")
            return dest
        except urllib.error.HTTPError as e:
            self.log(f"HTTP {e.code} al descargar Python {v}; pruebo la siguiente versión.", "warn")
            return None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise InstallError(f"No se pudo descargar Python. Comprueba tu conexión a Internet.\n({e})")

    @staticmethod
    def registered_python(v):
        """Carpeta de un Python de esa versión ya registrado para este usuario (o None)."""
        try:
            import winreg
            key = "Software\\Python\\PythonCore\\" + ".".join(v.split(".")[:2]) + "\\InstallPath"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return winreg.QueryValue(k, None) or None
        except (ImportError, OSError):
            return None

    def verify_signature(self, path):
        ps = (f"$s = Get-AuthenticodeSignature -LiteralPath {ps_quote(path)}; "
              "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                               capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW)
            out = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
        except Exception as e:
            raise InstallError(f"No se pudo verificar la firma del instalador de Python.\n({e})")
        status, _, subject = out.partition("|")
        self.log(f"Firma digital: {status}  ·  {subject}", "dim")
        if status != "Valid" or SIGNER not in subject:
            raise InstallError("El instalador descargado NO tiene la firma de la Python Software Foundation.\n"
                               "Por seguridad, no lo instalo.")
        self.log("✔ Firma válida: Python Software Foundation", "ok")

    def install_python(self, installer, v):
        """Instala Python `v` en la carpeta elegida. Devuelve True si quedó funcionando."""
        self.log(f"Instalando Python {v} en silencio (puede tardar un minuto)…")
        self.emit("progress", None, "Instalando Python…")
        pylog = os.path.join(os.path.dirname(installer), "python_setup.log")
        cmd = (f'"{installer}" /quiet /log "{pylog}" InstallAllUsers=0 TargetDir="{self.p.py_dir}" PrependPath=0 '
               "Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Shortcuts=0 "
               "AssociateFiles=0 Include_pip=1 Include_tcltk=1")
        code = self.run(cmd, show=False)
        if code in (0, 3010) and self.python_ok():
            self.log("✔ Python instalado", "ok")
            return True
        self.log(f"El instalador de Python {v} terminó con código {code} pero no dejó Python en {self.p.py_dir}", "warn")
        reg = self.registered_python(v)
        if reg:
            self.log(f"Windows ya tiene registrado un Python {'.'.join(v.split('.')[:2])} en: {reg}\n"
                     "   (el instalador de Python no permite dos copias de la misma versión para el mismo usuario)", "warn")
        try:                                        # cola del registro del propio instalador de Python
            with open(pylog, encoding="utf-8", errors="replace") as f:
                for line in f.read().splitlines()[-12:]:
                    self.log("   " + line[:160], "dim")
        except OSError:
            pass
        return False

    def setup_python(self, tmp):
        """Descarga, verifica e instala Python. Si una versión no se puede instalar, prueba la siguiente."""
        tried = []
        for v in PY_VERSIONS:
            self.emit("step", 0, "run")
            path = self.download_one(tmp, v)
            if not path:
                continue
            self.emit("step", 0, "ok")
            self.emit("step", 1, "run")
            self.verify_signature(path)
            self.emit("step", 1, "ok")
            self.emit("step", 2, "run")
            if self.install_python(path, v):
                self.emit("step", 2, "ok")
                return
            tried.append(v)
            for i in (0, 1, 2):
                self.emit("step", i, "wait")
            try:
                os.remove(path)
            except OSError:
                pass
            if v != PY_VERSIONS[-1]:
                self.log("Pruebo con la siguiente versión de Python…", "warn")
        regs = [f"Python {'.'.join(v.split('.')[:2])}: {self.registered_python(v)}" for v in tried
                if self.registered_python(v)]
        if regs:
            raise InstallError(
                f"No se pudo instalar Python en {self.p.py_dir}.\n\n"
                "Ya existe una instalación anterior de Python (seguramente la de MyTE en otra carpeta):\n  "
                + "\n  ".join(regs) +
                "\n\nSolución: abre Configuración → Aplicaciones → Aplicaciones instaladas, desinstala ese "
                "«Python … (64-bit)» y pulsa Reintentar.\n\nDetalles: " + self.p.log_path)
        raise InstallError(f"No se pudo instalar Python en {self.p.py_dir}.\nDetalles: {self.p.log_path}")

    def install_deps(self):
        self.emit("progress", None, "Instalando PySide6 (unos 80 MB)…")
        base = [self.p.py_exe, "-m", "pip", "install", "--no-warn-script-location", "--progress-bar", "off"]
        if self.run(base + ["--upgrade", "pip"]) != 0:
            self.log("No se pudo actualizar pip; sigo con el que trae Python.", "warn")
        for attempt in (1, 2):
            self.log("Instalando " + ", ".join(PACKAGES) + " …")
            if self.run(base + PACKAGES) == 0:
                break
            if attempt == 2:
                raise InstallError(f"No se pudieron instalar las dependencias.\nMira el registro: {self.p.log_path}")
            self.log("Fallo de red o de pip; reintento…", "warn")
        r = subprocess.run([self.p.py_exe, "-c", "import PySide6.QtWidgets"], capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            raise InstallError("PySide6 se instaló pero no se puede importar:\n" + (r.stderr or "")[-300:])
        self.log("✔ PySide6 listo", "ok")

    def copy_app(self):
        os.makedirs(self.p.app_dir, exist_ok=True)
        for name, required in APP_FILES:
            src = resource(name)
            if not src:
                if required:
                    raise InstallError(f"Falta {name} dentro del instalador.")
                self.log(f"(no incluido: {name})", "warn")
                continue
            shutil.copy2(src, os.path.join(self.p.app_dir, name))
            self.log(f"Copiado {name}", "dim")

    def launchers(self):
        """Archivos .bat dentro de la carpeta de instalación. Usan rutas relativas (%~dp0),
        así funcionan aunque la unidad cambie de letra y sin accesos directos del menú Inicio."""
        items = [("Abrir MyTE.bat", "myte_client.py")]
        if self.server:
            items.append(("Abrir MyTE Servidor.bat", "myte_server.py"))
        try:
            for name, script in items:
                text = ("@echo off\r\n"
                        'cd /d "%~dp0app"\r\n'
                        f'start "" "%~dp0py\\pythonw.exe" "%~dp0app\\{script}"\r\n')
                with open(os.path.join(self.p.base, name), "w", encoding="ascii", newline="") as f:
                    f.write(text)
            self.log("✔ Creado «" + items[0][0] + "» en la carpeta de instalación", "ok")
        except OSError as e:
            self.log(f"No se pudo crear el lanzador: {e}", "warn")

    def shortcuts(self):
        script = r"""
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject WScript.Shell
$pyw = $env:MYTE_PYW; $app = $env:MYTE_APP; $ico = Join-Path $app 'myte.ico'
$items = @(@{n='MyTE'; f='myte_client.py'; d='Chat, capturas y tareas en equipo'})
if ($env:MYTE_SERVER -eq '1') { $items += @{n='MyTE Servidor'; f='myte_server.py'; d='Servidor de MyTE'} }
$dirs = @((Join-Path ([Environment]::GetFolderPath('Programs')) 'MyTE'))
if ($env:MYTE_DESKTOP -eq '1') { $dirs += [Environment]::GetFolderPath('Desktop') }
foreach ($d in $dirs) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  foreach ($it in $items) {
    $s = $w.CreateShortcut((Join-Path $d ($it.n + '.lnk')))
    $s.TargetPath = $pyw
    $s.Arguments = '"' + (Join-Path $app $it.f) + '"'
    $s.WorkingDirectory = $app
    if (Test-Path $ico) { $s.IconLocation = $ico }
    $s.Description = $it.d
    $s.Save()
  }
}
"""
        enc = base64.b64encode(script.encode("utf-16-le")).decode()
        env = dict(os.environ, MYTE_PYW=self.p.pyw_exe, MYTE_APP=self.p.app_dir,
                   MYTE_SERVER="1" if self.server else "0", MYTE_DESKTOP="1" if self.desktop else "0")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True, text=True, env=env, timeout=90, creationflags=NO_WINDOW)
        if r.returncode != 0:
            self.log("No se pudieron crear los accesos directos: " + (r.stderr or "")[-200:], "warn")
        else:
            self.log("✔ Accesos directos creados (menú Inicio" + (" y escritorio)" if self.desktop else ")"), "ok")

    # -- flujo completo
    def go(self):
        try:
            if not IS_WIN:
                raise InstallError("Este instalador es solo para Windows.")
            self.log(f"Carpeta de instalación: {self.p.base}", "dim")
            if self.python_ok():
                self.log("✔ Ya hay un Python de MyTE instalado; me salto su descarga.", "ok")
                for i in (0, 1, 2):
                    self.emit("step", i, "skip")
            else:
                tmp = tempfile.mkdtemp(prefix="myte_")
                try:
                    self.setup_python(tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            self.emit("step", 3, "run")
            self.install_deps()
            self.emit("step", 3, "ok")
            self.emit("step", 4, "run")
            self.copy_app()
            self.launchers()
            self.shortcuts()
            self.emit("step", 4, "ok")
            self.emit("done")
        except InstallError as e:
            self.log("✖ " + str(e), "err")
            self.emit("fail", str(e))
        except Exception:
            tb = traceback.format_exc()
            self.log(tb, "err")
            self.emit("fail", "Error inesperado. Detalles en el registro:\n" + self.p.log_path)


# ============================================================ interfaz
C = dict(bg="#eaf6ff", card="#f8fdff", edge="#b7dcf2", text="#12385a", sub="#4d7291", blue="#2aa9ec", blue_d="#1a86bf",
         green="#6ccb33", green_d="#4a9f22", ok="#2f9e1f", err="#d93a2d", warn="#c77800", dim="#6a8aa5")
FONT = "Segoe UI"


class App(tk.Tk):
    def __init__(self, installer_cls=Installer):
        super().__init__()
        self.installer_cls = installer_cls
        self.title("MyTE — Instalador")
        self.geometry(f"640x{min(760, max(680, self.winfo_screenheight() - 80))}")
        self.resizable(False, False)
        self.configure(bg=C["bg"])
        ico = resource("myte.ico")
        if ico and IS_WIN:
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass
        self.paths = Paths(DEFAULT_BASE)
        self.q = queue.Queue()
        self.busy = self.finished = False
        self._build()
        self.eval("tk::PlaceWindow . center")
        self.after(60, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        h = tk.Canvas(self, width=640, height=96, highlightthickness=0, bd=0)
        h.pack(fill="x")
        top, bot = (0xbf, 0xe7, 0xfb), (0xea, 0xf6, 0xff)
        for y in range(96):
            t = y / 95
            h.create_line(0, y, 640, y, fill="#%02x%02x%02x" % tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
        for x, y, r in ((560, 30, 34), (600, 70, 18), (520, 78, 12)):
            h.create_oval(x - r, y - r, x + r, y + r, outline="#ffffff", width=2)
        h.create_text(26, 40, text="MyTE", anchor="w", font=(FONT, 30, "bold"), fill=C["blue"])
        h.create_text(28, 76, text="Instalador · chat, capturas y tareas en equipo", anchor="w", font=(FONT, 10), fill=C["sub"])

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=22, pady=(10, 16))
        tk.Label(body, bg=C["bg"], fg=C["text"], font=(FONT, 10), justify="left", wraplength=590, anchor="w",
                 text="Prepara todo lo necesario para usar MyTE: un Python propio (no toca el tuyo), las librerías y los "
                      "accesos directos. No necesita permisos de administrador.").pack(fill="x")

        tk.Label(body, text="Carpeta de instalación", bg=C["bg"], fg=C["text"], font=(FONT, 10, "bold"),
                 anchor="w").pack(fill="x", pady=(10, 2))
        drow = tk.Frame(body, bg=C["bg"])
        drow.pack(fill="x")
        self.v_dir = tk.StringVar(value=DEFAULT_BASE)
        self.e_dir = tk.Entry(drow, textvariable=self.v_dir, font=(FONT, 10), relief="flat", bg="white", fg=C["text"],
                              highlightbackground=C["edge"], highlightcolor=C["blue"], highlightthickness=1)
        self.e_dir.pack(side="left", fill="x", expand=True, ipady=4)
        self.b_dir = tk.Button(drow, text="Examinar…", command=self.browse, bg="#dcecf7", fg=C["text"], relief="flat",
                               font=(FONT, 10), padx=12, pady=2, cursor="hand2", highlightbackground=C["edge"],
                               highlightthickness=1, bd=0)
        self.b_dir.pack(side="left", padx=(8, 0))
        tk.Label(body, bg=C["bg"], fg=C["sub"], font=(FONT, 9), justify="left", wraplength=590, anchor="w",
                 text="¿Tu equipo se restaura al reiniciar (Deep Freeze, Reboot Restore…)? Elige una unidad que no se "
                      "borre, como otro disco o una memoria USB.").pack(fill="x", pady=(3, 0))

        opts = tk.Frame(body, bg=C["bg"])
        opts.pack(fill="x", pady=(10, 4))
        self.v_desktop, self.v_server = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        for var, text in ((self.v_desktop, "Crear acceso directo en el escritorio"),
                          (self.v_server, "Crear también el acceso al servidor (solo si vas a alojar el chat)")):
            tk.Checkbutton(opts, text=text, variable=var, bg=C["bg"], fg=C["text"], activebackground=C["bg"], selectcolor="white",
                           font=(FONT, 10), anchor="w").pack(fill="x")

        card = tk.Frame(body, bg=C["card"], highlightbackground=C["edge"], highlightthickness=1)
        card.pack(fill="x", pady=8)
        self.step_lbl = []
        for s in STEPS:
            l = tk.Label(card, text="○  " + s, bg=C["card"], fg=C["sub"], font=(FONT, 10), anchor="w")
            l.pack(fill="x", padx=14, pady=3)
            self.step_lbl.append(l)

        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("M.Horizontal.TProgressbar", troughcolor="#d6ecfa", background=C["blue"], bordercolor=C["edge"],
                     lightcolor="#8fdcff", darkcolor=C["blue_d"], thickness=16)
        self.bar = ttk.Progressbar(body, style="M.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(6, 2))
        self.status = tk.Label(body, text="Listo para instalar.", bg=C["bg"], fg=C["sub"], font=(FONT, 9), anchor="w")
        self.status.pack(fill="x")

        lf = tk.Frame(body, bg=C["card"], highlightbackground=C["edge"], highlightthickness=1)
        lf.pack(fill="both", expand=True, pady=8)
        self.txt = tk.Text(lf, height=4, bg=C["card"], fg=C["text"], relief="flat", wrap="word", font=("Consolas", 9),
                           state="disabled", padx=8, pady=6)
        sb = tk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        for tag, col in (("ok", C["ok"]), ("err", C["err"]), ("warn", C["warn"]), ("dim", C["dim"]), ("info", C["text"])):
            self.txt.tag_configure(tag, foreground=col)

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x")
        self.b_main = self._button(row, "Instalar MyTE", self.start, C["green"], C["green_d"], "#123008")
        self.b_main.pack(side="left")
        self.b_open = self._button(row, "Abrir MyTE", self.open_app, C["blue"], C["blue_d"], "white")
        self.b_close = self._button(row, "Cerrar", self._close, "#dcecf7", C["edge"], C["text"])
        self.b_close.pack(side="right")

    def _button(self, parent, text, cmd, bg, edge, fg):
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=edge, activeforeground=fg, relief="flat",
                      font=(FONT, 11, "bold"), padx=22, pady=7, cursor="hand2", highlightbackground=edge, highlightthickness=1, bd=0)
        return b

    # -- eventos
    def browse(self):
        cur = os.path.dirname(os.path.normpath(self.v_dir.get().strip() or DEFAULT_BASE))
        d = filedialog.askdirectory(title="Elige dónde instalar MyTE", mustexist=True,
                                    initialdir=cur if os.path.isdir(cur) else os.path.expanduser("~"))
        if d:
            d = os.path.normpath(d)
            if os.path.basename(d).lower() != "myte":      # no mezclar MyTE con otros archivos
                d = os.path.join(d, "MyTE")
            self.v_dir.set(d)

    def _lock_dir(self, locked):
        state = "disabled" if locked else "normal"
        self.e_dir.configure(state=state)
        self.b_dir.configure(state=state)

    def start(self):
        if self.busy:
            return
        base = os.path.normpath(self.v_dir.get().strip().strip('"')) if self.v_dir.get().strip() else ""
        err = check_base(base)
        if err:
            messagebox.showerror("MyTE — Instalador", err)
            return
        if is_foreign(base) and not messagebox.askyesno(
                "MyTE — Instalador", f"La carpeta\n{base}\nya contiene otros archivos.\n\n¿Instalar MyTE aquí de todos modos?"):
            return
        self.v_dir.set(base)
        self.paths = Paths(base)
        self.busy = True
        self._lock_dir(True)
        self.b_main.configure(state="disabled", text="Instalando…")
        for i, s in enumerate(STEPS):
            self._set_step(i, "wait")
        inst = self.installer_cls(lambda *a: self.q.put(a), desktop=self.v_desktop.get(), server=self.v_server.get(), base=base)
        threading.Thread(target=inst.go, daemon=True).start()

    def _set_step(self, i, state):
        icon, col = {"wait": ("○", C["sub"]), "run": ("⏳", C["blue_d"]), "ok": ("✔", C["ok"]),
                     "skip": ("✔", C["ok"]), "err": ("✖", C["err"])}[state]
        extra = "  (ya instalado)" if state == "skip" else ""
        self.step_lbl[i].configure(text=f"{icon}  {STEPS[i]}{extra}", fg=col,
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
            self.bar.stop()
            self.bar.configure(mode="indeterminate")
            self.bar.start(14)

    def ev_progress(self, value, text):
        self.status.configure(text=text)
        if value is None:
            if str(self.bar["mode"]) != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(14)
        else:
            self.bar.stop()
            self.bar.configure(mode="determinate", value=value)

    def ev_done(self):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100)
        self.busy, self.finished = False, True
        self.status.configure(text="¡Todo listo! Ya puedes abrir MyTE.", fg=C["ok"])
        self.b_main.pack_forget()
        self.b_open.pack(side="left")
        self.ev_log("Instalación completada en " + self.paths.base, "ok")
        self.ev_log("Para abrir MyTE: menú Inicio / escritorio, o «Abrir MyTE.bat» en esa carpeta.", "ok")
        self.ev_log("Si tu equipo se restaura al reiniciar, los accesos directos se perderán, "
                    "pero «Abrir MyTE.bat» seguirá funcionando.", "info")

    def ev_fail(self, msg):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)
        self.busy = False
        self._lock_dir(False)
        for i, l in enumerate(self.step_lbl):
            if "⏳" in l.cget("text"):
                self._set_step(i, "err")
        self.status.configure(text="La instalación falló. Puedes volver a intentarlo.", fg=C["err"])
        self.b_main.configure(state="normal", text="Reintentar")
        messagebox.showerror("MyTE — Instalador", msg)

    def open_app(self):
        try:
            subprocess.Popen([self.paths.pyw_exe, os.path.join(self.paths.app_dir, "myte_client.py")],
                             cwd=self.paths.app_dir, creationflags=NO_WINDOW)
            self._close(force=True)
        except OSError as e:
            messagebox.showerror("MyTE", f"No se pudo abrir MyTE:\n{e}")

    def _close(self, force=False):
        if self.busy and not force and not messagebox.askyesno("MyTE", "La instalación está en curso. ¿Salir de todos modos?"):
            return
        self.destroy()


def main():
    if not IS_WIN:
        print("Este instalador es solo para Windows.")
    App().mainloop()


if __name__ == "__main__":
    main()
