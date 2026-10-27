"""Clips de video al disparar eventos: mantiene un buffer circular de frames
y guarda ±5s alrededor como MP4 (reconstruido desde JPEG sin/recursor FFMPEG)."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)


class GuardadorClips:
    """Buffer circular de frames recentes y guardado en disco on demande.

    - `meter(frame)`: cada vez que un frame nuevo llegue del bucle
    - `guardar(tipo)`: escribe un mp4 con los ultimos ~70 frames
    """

    def __init__(self, max_frames: int = 70, carpeta: str = "clips",
                 fps_salida: float = 10.0):
        self._buf: deque[np.ndarray] = deque(maxlen=max_frames)
        self.carpeta = Path(carpeta)
        self.fps_salida = fps_salida
        self.carpeta.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._trabajos: list[threading.Thread] = []

    def meter(self, frame: np.ndarray) -> None:
        mini = cv2.resize(frame, (640, 480)) if frame.shape[:2] != (480, 640) else frame
        with self._lock:
            self._buf.append(mini.copy())

    def guardar(self, tipo: str) -> Path | None:
        with self._lock:
            copia: deque[np.ndarray] = self._buf.copy()
        if not copia:
            return None
        destino = self.carpeta / (
            f"{time.strftime('%Y%m%d_%H%M%S')}_{tipo}.mp4")
        hilo = threading.Thread(target=self._escribir, args=(copia, destino),
                                daemon=True)
        hilo.start()
        self._trabajos.append(hilo)
        return destino

    def _escribir(self, frames: deque[np.ndarray], destino: Path) -> None:
        h = frames[0].shape[0]
        w = frames[0].shape[1]
        out = cv2.VideoWriter(str(destino),
                              cv2.VideoWriter_fourcc(*"mp4v"),
                              self.fps_salida, (w, h))
        for f in frames:
            out.write(f)
        out.release()
        log.info("clip guardado: %s (%d frames)", destino.name, len(frames))

    def esperar_trabajos(self, limite: float = 5.0) -> None:
        t0 = time.monotonic()
        for t in self._trabajos:
            t.join(max(0.1, limite - (time.monotonic() - t0)))
