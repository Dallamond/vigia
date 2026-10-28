"""Seguridad del dashboard: token, `.env`, intentos de login y cabeceras.

Sin dependencia extra: el `.env` se parsea a mano y se vuelca en `os.environ`
sin pisar lo que ya venga del sistema (las variables de entorno siempre ganan).
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from .config import RAIZ

log = logging.getLogger(__name__)

# rutas que se sirven sin token para poder mostrar la pantalla de login
RUTAS_PUBLICAS = {"/", "/index.html", "/favicon.ico"}
PREFIJOS_PUBLICOS = ("/static/", "/auth/")
# el token por query solo se acepta en el video (un <img>/<video> no manda
# cabeceras personalizadas de forma sencilla)
RUTAS_TOKEN_QUERY = {"/video.mjpg", "/snapshot.jpg"}

CABECERAS_SEGURIDAD = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def cargar_env(ruta: str | Path | None = None) -> dict[str, str]:
    """Carga un `.env` sencillo (`CLAVE=valor`, `#` comentarios).

    No pisa variables ya definidas en el entorno. Devuelve lo leido.
    """
    p = Path(ruta) if ruta is not None else RAIZ / ".env"
    leidas: dict[str, str] = {}
    try:
        texto = p.read_text(encoding="utf-8")
    except OSError:
        return leidas
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if not clave:
            continue
        leidas[clave] = valor
        os.environ.setdefault(clave, valor)
    return leidas


def es_loopback(host: str | None) -> bool:
    """True para 127.0.0.1, ::1 y localhost (no para 0.0.0.0)."""
    if not host:
        return False
    host = str(host).strip().strip("[]").lower()
    return (host == "localhost" or host == "::1" or host.startswith("127.")
            or host.startswith("::ffff:127."))


def comprobar_arranque(host: str, token: str | None) -> None:
    """Niega el arranque si se expone la red sin token."""
    if token:
        return
    if not es_loopback(host):
        raise RuntimeError(
            f"Vigia no puede escuchar en {host!r} sin VIGIA_TOKEN: la camara "
            "quedaria abierta a toda la red. Define VIGIA_TOKEN (entorno o "
            ".env) o usa host 127.0.0.1.")


class Seguridad:
    """Token de acceso y limitacion de intentos de login por IP."""

    def __init__(self, token: str | None = None, max_intentos: int = 5,
                 ventana_seg: float = 60.0,
                 reloj=time.monotonic) -> None:
        self.token = (token or "").strip() or None
        self.max_intentos = max(1, int(max_intentos))
        self.ventana_seg = float(ventana_seg)
        self.reloj = reloj
        self._fallos: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def requerida(self) -> bool:
        return self.token is not None

    def valido(self, candidato: str | None) -> bool:
        if not self.token or not candidato:
            return False
        return secrets.compare_digest(str(candidato), self.token)

    def credencial_valida(self, authorization: str | None = None,
                          cookie: str | None = None,
                          token_query: str | None = None) -> bool:
        """Acepta `Authorization: Bearer`, cookie o (si se permite) ?token=."""
        if not self.requerida:
            return True
        if authorization:
            partes = authorization.split(None, 1)
            if len(partes) == 2 and partes[0].lower() == "bearer":
                if self.valido(partes[1].strip()):
                    return True
        if cookie and self.valido(cookie):
            return True
        if token_query and self.valido(token_query):
            return True
        return False

    def permitir_login(self, ip: str) -> bool:
        """False si esa IP agoto los intentos dentro de la ventana."""
        ahora = self.reloj()
        with self._lock:
            intentos = self._fallos.get(ip)
            if not intentos:
                return True
            self._podar(intentos, ahora)
            return len(intentos) < self.max_intentos

    def fallo_login(self, ip: str) -> None:
        ahora = self.reloj()
        with self._lock:
            intentos = self._fallos.setdefault(ip, deque())
            self._podar(intentos, ahora)
            intentos.append(ahora)

    def exito_login(self, ip: str) -> None:
        with self._lock:
            self._fallos.pop(ip, None)

    def _podar(self, intentos: deque, ahora: float) -> None:
        while intentos and ahora - intentos[0] > self.ventana_seg:
            intentos.popleft()
