"""Captura de webcam con OpenCV: abrir/reconectar y entregar frames BGR."""
from __future__ import annotations

import logging
import time

import cv2

log = logging.getLogger(__name__)

_REINTENTO_SEGUNDOS = 2.0
_BACKEND = cv2.CAP_DSHOW  # MSMF falla con muchas webcams Windows (abre pero no lee)


class CamaraError(RuntimeError):
    """La cámara no está disponible."""


class Camara:
    """Abre una webcam y entrega frames. Reintenta si se pierde la cámara.

    Nota: la webcam del PC limita a ~14 fps reales; 640x480 es la resolución
    de captura óptima (a 720p baja a ~9 fps).
    """

    def __init__(self, fuente: int | str = 0, ancho: int = 640, alto: int = 480,
                 abrir: bool = True):
        self.fuente = fuente
        self.ancho = ancho
        self.alto = alto
        self.cap: "cv2.VideoCapture | None" = None
        self.abierta = False
        self._siguiente_intento = 0.0
        if abrir:
            self.abrir()
            if not self.abierta:
                raise CamaraError(f"No se pudo abrir la cámara ({self.fuente!r})")

    # -- interno ---------------------------------------------------------
    def _siguiente_reintento(self) -> None:
        self._siguiente_intento = time.monotonic() + _REINTENTO_SEGUNDOS
        log.warning("Cámara caída; reintentando en %.0fs", _REINTENTO_SEGUNDOS)

    # -- api -------------------------------------------------------------
    def abrir(self) -> bool:
        """Intenta abrir la cámara. Devuelve True si quedó abierta."""
        if self.abierta:
            return True
        if time.monotonic() < self._siguiente_intento:
            return False
        cap = cv2.VideoCapture(self.fuente, _BACKEND)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.ancho)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.alto)
        ok_frame = False
        # algunas webcams necesitan varios intentos antes del primer frame
        for _ in range(15):
            ok_frame, _ = cap.read()
            if ok_frame:
                break
            time.sleep(0.1)
        if cap.isOpened() and ok_frame:
            self.cap = cap
            self.abierta = True
            log.info("Cámara abierta: %s", self.fuente)
            return True
        cap.release()
        self._siguiente_reintento()
        return False

    def abrir_bloqueante(self) -> None:
        """Intenta abrir hasta lograrlo (para el arranque del programa)."""
        while not self.abrir():
            log.info("Esperando cámara...")
            time.sleep(_REINTENTO_SEGUNDOS)

    def leer(self) -> tuple[bool, "cv2.typing.MatLike | None"]:
        """Devuelve (ok, frame). Si la cámara se pierde, (False, None) y reintenta."""
        if not self.abierta:
            return False, None
        ok, frame = self.cap.read()
        if not ok:
            self.liberar()
            self._siguiente_reintento()
            self.abrir()
            return False, None
        return True, frame

    def liberar(self) -> None:
        if self.cap is not None:
            self.cap.release()
        self.cap = None
        self.abierta = False
        log.info("Cámara liberada")
