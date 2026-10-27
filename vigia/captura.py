"""Captura de webcam con OpenCV: abrir/reconectar y entregar frames BGR."""
from __future__ import annotations

import logging
import time

import cv2

log = logging.getLogger(__name__)

_REINTENTO_SEGUNDOS = 2.0
_INTENTOS_APERTURA = 15
_ESPERA_ENTRE_INTENTOS = 0.1
_BACKEND = cv2.CAP_DSHOW  # MSMF falla con muchas webcams Windows (abre pero no lee)


def detectar_camaras(maximo: int = 6, ocupada: int | None = None,
                     backend: int = _BACKEND) -> list[dict]:
    """Webcams disponibles (indices 0..maximo-1).

    OpenCV no da nombres, solo indices. La que esta en uso (`ocupada`) no se
    puede abrir dos veces, asi que se da por disponible sin probarla.
    """
    camaras = []
    for i in range(maximo):
        if i == ocupada:
            camaras.append({"indice": i, "disponible": True})
            continue
        cap = None
        try:
            cap = cv2.VideoCapture(i, backend)
            ok = bool(cap.isOpened() and cap.read()[0])
        except cv2.error:
            ok = False
        finally:
            if cap is not None:
                cap.release()
        if ok:
            camaras.append({"indice": i, "disponible": True})
    return camaras


class CamaraError(RuntimeError):
    """La cámara no está disponible."""


class Camara:
    """Abre una webcam y entrega frames. Reintenta si se pierde la cámara.

    Nota: la webcam del PC limita a ~14 fps reales; 640x480 es la resolución
    de captura óptima (a 720p baja a ~9 fps).
    """

    def __init__(self, fuente: int | str = 0, ancho: int = 640, alto: int = 480,
                 abrir: bool = True, backend: int = _BACKEND,
                 reintento_segundos: float = _REINTENTO_SEGUNDOS):
        self.fuente = fuente
        self.ancho = ancho
        self.alto = alto
        self.backend = backend
        self.reintento_segundos = reintento_segundos
        self.cap: "cv2.VideoCapture | None" = None
        self.abierta = False
        self._siguiente_intento = 0.0
        if abrir:
            self.abrir()
            if not self.abierta:
                raise CamaraError(f"No se pudo abrir la cámara ({self.fuente!r})")

    # -- interno ---------------------------------------------------------
    def _siguiente_reintento(self) -> None:
        self._siguiente_intento = time.monotonic() + self.reintento_segundos
        log.warning("Cámara caída; reintentando en %.1fs", self.reintento_segundos)

    # -- api -------------------------------------------------------------
    def abrir(self) -> bool:
        """Intenta abrir la cámara. Devuelve True si quedó abierta."""
        if self.abierta:
            return True
        if time.monotonic() < self._siguiente_intento:
            return False
        cap = None
        try:
            cap = cv2.VideoCapture(self.fuente, self.backend)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.ancho)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.alto)
            ok_frame = False
            # algunas webcams necesitan varios intentos antes del primer frame
            if cap.isOpened():
                for _ in range(_INTENTOS_APERTURA):
                    ok_frame, _ = cap.read()
                    if ok_frame:
                        break
                    time.sleep(_ESPERA_ENTRE_INTENTOS)
            if ok_frame:
                self.cap = cap
                self.abierta = True
                self._siguiente_intento = 0.0
                log.info("Cámara abierta: %s", self.fuente)
                return True
            cap.release()
        except cv2.error:
            # los drivers de cámara pueden lanzar errores propios de OpenCV
            log.exception("Error del driver al abrir la cámara %r", self.fuente)
            if cap is not None:
                cap.release()
        self._siguiente_reintento()
        log.debug("Cámara no disponible todavía (fuente %r)", self.fuente)
        return False

    def cambiar(self, fuente: int | str) -> bool:
        """Cambia de webcam en caliente. Si la nueva no abre, vuelve a la anterior."""
        anterior = self.fuente
        self.liberar()
        self.fuente = fuente
        self._siguiente_intento = 0.0
        if self.abrir():
            log.info("Camara cambiada: %s -> %s", anterior, fuente)
            return True
        log.warning("No se pudo abrir la camara %s; se vuelve a la %s",
                    fuente, anterior)
        self.fuente = anterior
        self._siguiente_intento = 0.0
        self.abrir()
        return False

    def abrir_bloqueante(self, timeout: float | None = None) -> bool:
        """Intenta abrir hasta lograrlo o hasta agotar `timeout` segundos."""
        inicio = time.monotonic()
        while not self.abrir():
            if timeout is not None and (time.monotonic() - inicio) >= timeout:
                return False
            log.info("Esperando cámara...")
            time.sleep(self.reintento_segundos)
        return True

    def leer(self) -> tuple[bool, "cv2.typing.MatLike | None"]:
        """Devuelve (ok, frame). Si la cámara se pierde, (False, None) y reintenta."""
        if not self.abierta:
            # clave: sin esto, una cámara caída no se recuperaba nunca
            self.abrir()
            if not self.abierta:
                return False, None
        ok, frame = self.cap.read()
        if not ok:
            log.warning("Lectura fallida; se libera la cámara y se reintenta")
            self.liberar()
            self._siguiente_reintento()
            return False, None
        return True, frame

    def liberar(self) -> None:
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.abierta = False
        log.info("Cámara liberada")
