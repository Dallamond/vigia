"""Clips de video al disparar eventos: buffer circular de frames recientes que
se vuelca a disco como WebM (VP8), que es lo que reproduce cualquier navegador.

El codec `mp4v` de OpenCV (MPEG-4 Part 2) no lo reproducen ni Chrome ni Edge y
H.264 exige una DLL (`openh264`) que OpenCV no trae; por eso se prueba una
lista de formatos y se usa el primero que el equipo sabe escribir.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

_TIPO_SEGURO = re.compile(r"[^A-Za-z0-9_-]+")

# (fourcc, extension) por orden de preferencia: VP8 es rapido y universal
_FORMATOS = (("VP80", ".webm"), ("VP90", ".webm"), ("mp4v", ".mp4"))
EXT_CLIP = (".webm", ".mp4")
EXT_MINIATURA = (".jpg", ".jpeg", ".png")
_CALIDAD_BUFFER = 85
_PREFIJO_TMP = "~"  # los clips a medio escribir no se listan ni se sirven


def _calidad(valor: int) -> list[int]:
    return [int(cv2.IMWRITE_JPEG_QUALITY), valor]


class GuardadorClips:
    """Buffer circular de frames recientes y guardado en disco bajo demanda.

    - `meter(frame)`: cada frame nuevo del bucle (se guarda comprimido: 70
      frames sin comprimir a 1080p serian ~430 MB de RAM)
    - `guardar(tipo)`: escribe un clip con los ultimos frames en segundo plano
    """

    # topes de seguridad por numero de ficheros; la retencion "de verdad" por
    # dias/MB la aplica `mantener`
    tope_clips = 500
    tope_miniaturas = 3000

    def __init__(self, max_frames: int = 70, carpeta: str | Path = "clips",
                 fps_salida: float = 10.0):
        self._buf: deque[bytes] = deque(maxlen=max_frames)
        self.carpeta = Path(carpeta)
        self.fps_salida = fps_salida
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._trabajos: list[threading.Thread] = []
        self._secuencia = 0
        self._formato: tuple[str, str] | None = None

    # -- entrada ----------------------------------------------------------
    def meter(self, frame: np.ndarray | None) -> None:
        if frame is None:
            return
        ok, buf = cv2.imencode(".jpg", frame, _calidad(_CALIDAD_BUFFER))
        if not ok:
            return
        with self._lock:
            self._buf.append(buf.tobytes())

    # -- formato ----------------------------------------------------------
    def formato(self) -> tuple[str, str]:
        """(fourcc, extension) que este equipo sabe escribir (se prueba una vez)."""
        if self._formato is None:
            self._formato = self._probar_formato()
        return self._formato

    def _probar_formato(self) -> tuple[str, str]:
        for fourcc, ext in _FORMATOS:
            prueba = self.carpeta / f"{_PREFIJO_TMP}prueba{ext}"
            try:
                out = cv2.VideoWriter(str(prueba),
                                      cv2.VideoWriter_fourcc(*fourcc),
                                      10, (64, 48))
                ok = out.isOpened()
                if ok:
                    out.write(np.zeros((48, 64, 3), np.uint8))
                out.release()
            except Exception:
                ok = False
            finally:
                try:
                    prueba.unlink()
                except OSError:
                    pass
            if ok:
                log.info("clips: formato %s (%s)", fourcc, ext)
                return fourcc, ext
        log.error("clips: ningun codec disponible; se usara mp4v")
        return "mp4v", ".mp4"

    # -- salida -----------------------------------------------------------
    def guardar(self, tipo: str) -> Path | None:
        with self._lock:
            if not self._buf:
                return None
            copia = list(self._buf)
        nombre = f"{self._sello(tipo)}{self.formato()[1]}"
        destino = self.carpeta / nombre
        hilo = threading.Thread(target=self._escribir, args=(copia, destino),
                                daemon=True)
        with self._lock:
            self._trabajos = [t for t in self._trabajos if t.is_alive()]
            self._trabajos.append(hilo)
        hilo.start()
        return destino

    def _sello(self, tipo: str) -> str:
        """Nombre unico (segundo + contador) compartido por clips y miniaturas."""
        with self._lock:
            self._secuencia += 1
            secuencia = self._secuencia
        base = f"{time.strftime('%Y%m%d_%H%M%S')}_{secuencia:04d}"
        return f"{base}_{_TIPO_SEGURO.sub('_', tipo)}"

    def guardar_miniatura(self, frame: "np.ndarray | None",
                          tipo: str) -> Path | None:
        """Guarda un JPEG del instante del evento (barato, ~20 KB)."""
        if frame is None:
            return None
        nombre = f"{self._sello(f'{tipo}_min')}.jpg"
        destino = self.carpeta / nombre
        try:
            ok = cv2.imwrite(str(destino), frame, _calidad(75))
        except Exception:
            log.exception("no se pudo guardar la miniatura %s", nombre)
            return None
        if not ok:
            log.warning("no se pudo guardar la miniatura %s", nombre)
            return None
        return destino

    def _escribir(self, jpegs: list[bytes], destino: Path) -> None:
        fourcc = self.formato()[0]
        # se escribe con prefijo y se renombra al final: el panel nunca ve un
        # fichero a medias (daria un reproductor roto)
        tmp = destino.with_name(_PREFIJO_TMP + destino.name)
        frames = (cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
                  for j in jpegs)
        out = None
        escritos = 0
        try:
            for frame in frames:
                if frame is None:
                    continue
                if out is None:
                    h, w = frame.shape[:2]
                    out = cv2.VideoWriter(
                        str(tmp), cv2.VideoWriter_fourcc(*fourcc),
                        max(self.fps_salida, 1.0), (w, h))
                    if not out.isOpened():
                        log.error("no se pudo crear el clip %s (codec %s)",
                                  destino.name, fourcc)
                        out.release()
                        return
                if frame.shape[:2] != (h, w):
                    frame = cv2.resize(frame, (w, h))
                out.write(frame)
                escritos += 1
        except Exception:
            log.exception("error escribiendo el clip %s", destino.name)
        finally:
            if out is not None:
                out.release()
        if not escritos:
            self._borrar(tmp)
            return
        try:
            os.replace(tmp, destino)
        except OSError:
            log.exception("no se pudo cerrar el clip %s", destino.name)
            self._borrar(tmp)
            return
        log.info("clip guardado: %s (%d frames)", destino.name, escritos)
        self.podar()

    @staticmethod
    def _borrar(ruta: Path) -> None:
        try:
            ruta.unlink()
        except OSError:
            pass

    # -- consulta ---------------------------------------------------------
    def _ficheros(self, extensiones: tuple[str, ...]) -> list[Path]:
        """Ficheros de la carpeta con esas extensiones, sin los a medio escribir."""
        if not self.carpeta.exists():
            return []
        try:
            return [p for p in self.carpeta.iterdir()
                    if p.is_file() and p.suffix.lower() in extensiones
                    and not p.name.startswith(_PREFIJO_TMP)]
        except OSError:
            log.exception("no se pudo leer la carpeta de media")
            return []

    def _podar(self, extensiones: tuple[str, ...], tope: int, que: str) -> int:
        ficheros = []
        for p in self._ficheros(extensiones):
            try:
                ficheros.append((p.stat().st_mtime, p))
            except OSError:
                continue
        ficheros.sort(key=lambda x: x[0])
        borrados = 0
        for _mtime, f in ficheros[: max(0, len(ficheros) - tope)]:
            try:
                f.unlink()
                borrados += 1
            except OSError:
                log.exception("no se pudo borrar %s %s", que, f.name)
        return borrados

    def podar(self, max_ficheros: int | None = None) -> int:
        """Borra los clips mas antiguos si hay mas del tope. Devuelve cuantos."""
        return self._podar(EXT_CLIP, self.tope_clips if max_ficheros is None
                           else max_ficheros, "el clip")

    def podar_miniaturas(self, max_ficheros: int | None = None) -> int:
        """Igual que `podar` pero para las miniaturas."""
        return self._podar((".jpg",), self.tope_miniaturas
                           if max_ficheros is None else max_ficheros,
                           "la miniatura")

    def listar(self) -> list[dict]:
        """Clips en disco, el mas reciente primero (para la UI)."""
        info = []
        for p in self._ficheros(EXT_CLIP):
            try:
                st = p.stat()
            except OSError:
                continue
            info.append({"fichero": p.name, "tamano": st.st_size,
                         "_t": st.st_mtime,
                         "cuando": time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.localtime(st.st_mtime))})
        info.sort(key=lambda i: i["_t"], reverse=True)
        for i in info:
            del i["_t"]
        return info

    def resumen_media(self) -> dict:
        """Cuenta y tamano de toda la media (clips + miniaturas)."""
        clips = miniaturas = total = 0
        for p in self._ficheros(EXT_CLIP + EXT_MINIATURA):
            try:
                total += p.stat().st_size
            except OSError:
                continue
            if p.suffix.lower() in EXT_CLIP:
                clips += 1
            else:
                miniaturas += 1
        return {"n": clips, "miniaturas": miniaturas, "bytes": total}

    def esperar_trabajos(self, limite: float = 5.0) -> None:
        t0 = time.monotonic()
        with self._lock:
            trabajos = list(self._trabajos)
        for t in trabajos:
            t.join(max(0.1, limite - (time.monotonic() - t0)))

    # -- migracion --------------------------------------------------------
    def convertir_antiguos(self) -> dict[str, str]:
        """Recodifica los .mp4 antiguos (mp4v, no reproducibles) a WebM.

        Devuelve {nombre_viejo: nombre_nuevo} de los convertidos; el original
        se borra solo si el nuevo quedo bien escrito.
        """
        fourcc, ext = self.formato()
        if ext == ".mp4":
            return {}  # este equipo no sabe escribir nada mejor
        hechos: dict[str, str] = {}
        for viejo in self._ficheros((".mp4",)):
            nuevo = viejo.with_suffix(ext)
            tmp = nuevo.with_name(_PREFIJO_TMP + nuevo.name)
            cap = cv2.VideoCapture(str(viejo))
            out = None
            n = 0
            try:
                fps = cap.get(cv2.CAP_PROP_FPS) or self.fps_salida
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if out is None:
                        h, w = frame.shape[:2]
                        out = cv2.VideoWriter(
                            str(tmp), cv2.VideoWriter_fourcc(*fourcc),
                            max(fps, 1.0), (w, h))
                        if not out.isOpened():
                            break
                    out.write(frame)
                    n += 1
            except Exception:
                log.exception("no se pudo convertir %s", viejo.name)
                n = 0
            finally:
                cap.release()
                if out is not None:
                    out.release()
            if not n:
                self._borrar(tmp)
                continue
            try:
                os.replace(tmp, nuevo)
                viejo.unlink()
            except OSError:
                log.exception("no se pudo cerrar la conversion de %s",
                              viejo.name)
                self._borrar(tmp)
                continue
            hechos[viejo.name] = nuevo.name
            log.info("clip convertido: %s -> %s", viejo.name, nuevo.name)
        return hechos
