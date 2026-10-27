"""Clips de video al disparar eventos: mantiene un buffer circular de frames
y guarda ~5s previos como MP4 (reconstruido con el codec mp4v de OpenCV)."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

_TIPO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")


class GuardadorClips:
    """Buffer circular de frames recientes y guardado en disco bajo demanda.

    - `meter(frame)`: cada frame nuevo del bucle
    - `guardar(tipo)`: escribe un mp4 con los ultimos frames en segundo plano
    """

    def __init__(self, max_frames: int = 70, carpeta: str = "clips",
                 fps_salida: float = 10.0):
        self._buf: deque[np.ndarray] = deque(maxlen=max_frames)
        self.carpeta = Path(carpeta)
        self.fps_salida = fps_salida
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._trabajos: list[threading.Thread] = []
        self._secuencia = 0

    def meter(self, frame: np.ndarray | None) -> None:
        if frame is None:
            return
        with self._lock:
            self._buf.append(frame.copy())

    def guardar(self, tipo: str) -> Path | None:
        with self._lock:
            if not self._buf:
                return None
            copia = deque(self._buf, maxlen=self._buf.maxlen)
            nombre = f"{self._sello(tipo)}.mp4"

        destino = self.carpeta / nombre
        hilo = threading.Thread(target=self._escribir, args=(copia, destino),
                                daemon=True)
        hilo.start()
        self._trabajos = [t for t in self._trabajos if t.is_alive()]
        self._trabajos.append(hilo)
        return destino

    def _sello(self, tipo: str) -> str:
        """Nombre unico (segundo + contador) compartido por clips y miniaturas."""
        self._secuencia += 1
        base = f"{time.strftime('%Y%m%d_%H%M%S')}_{self._secuencia:04d}"
        return f"{base}_{_TIPO_SEGURO.sub('_', tipo)}"

    def guardar_miniatura(self, frame: "np.ndarray | None",
                          tipo: str) -> Path | None:
        """Guarda un JPEG del instante del evento (barato, ~20 KB)."""
        if frame is None:
            return None
        with self._lock:
            nombre = f"{self._sello(f'{tipo}_min')}.jpg"
        destino = self.carpeta / nombre
        try:
            ok = cv2.imwrite(str(destino), frame,
                             [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        except Exception:
            log.exception("no se pudo guardar la miniatura %s", nombre)
            return None
        if not ok:
            log.warning("no se pudo guardar la miniatura %s", nombre)
            return None
        return destino

    def _escribir(self, frames: deque[np.ndarray], destino: Path) -> None:
        h, w = frames[0].shape[:2]
        out = cv2.VideoWriter(str(destino), cv2.VideoWriter_fourcc(*"mp4v"),
                              self.fps_salida, (w, h))
        if not out.isOpened():
            log.error("no se pudo crear el clip %s (codec mp4v no disponible)",
                      destino.name)
            return
        try:
            for frame in frames:
                if frame.shape[:2] != (h, w):
                    frame = cv2.resize(frame, (w, h))
                out.write(frame)
        except Exception:
            log.exception("error escribiendo el clip %s", destino.name)
        finally:
            out.release()
        log.info("clip guardado: %s (%d frames)", destino.name, len(frames))
        self.podar()

    def podar(self, max_ficheros: int = 60) -> int:
        """Borra los clips mas antiguos si hay mas de `max_ficheros`.

        Devuelve cuantos se borraron. Evita que clips/ crezca sin limite.
        """
        if not self.carpeta.exists():
            return 0
        try:
            ficheros = sorted(self.carpeta.glob("*.mp4"),
                              key=lambda p: p.stat().st_mtime)
        except OSError:
            log.exception("no se pudo podar la carpeta de clips")
            return 0
        sobrantes = ficheros[: max(0, len(ficheros) - max_ficheros)]
        borrados = 0
        for f in sobrantes:
            try:
                f.unlink()
                borrados += 1
            except OSError:
                log.exception("no se pudo borrar el clip %s", f.name)
        return borrados

    def podar_miniaturas(self, max_ficheros: int = 300) -> int:
        """Igual que `podar` pero para las miniaturas .jpg."""
        if not self.carpeta.exists():
            return 0
        try:
            ficheros = sorted(self.carpeta.glob("*.jpg"),
                              key=lambda p: p.stat().st_mtime)
        except OSError:
            log.exception("no se pudieron podar las miniaturas")
            return 0
        sobrantes = ficheros[: max(0, len(ficheros) - max_ficheros)]
        borrados = 0
        for f in sobrantes:
            try:
                f.unlink()
                borrados += 1
            except OSError:
                log.exception("no se pudo borrar la miniatura %s", f.name)
        return borrados

    def listar(self) -> list[dict]:
        """Contenido de clips/ mas reciente primero (para la UI)."""
        def _info(p: Path) -> dict:
            try:
                st = p.stat()
            except OSError:
                return {}
            return {"fichero": p.name,
                    "tamano": st.st_size,
                    "cuando": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}
        if not self.carpeta.exists():
            return []
        return [i for i in map(_info, sorted(
            self.carpeta.glob("*.mp4"),
            key=lambda p: p.stat().st_mtime, reverse=True)) if i]

    def esperar_trabajos(self, limite: float = 5.0) -> None:
        t0 = time.monotonic()
        for t in self._trabajos:
            t.join(max(0.1, limite - (time.monotonic() - t0)))
