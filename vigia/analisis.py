"""Pasos opcionales del bucle de video: actividades (pose) y caras.

Cada modulo guarda su propio estado y expone `procesar(...)`; el bucle de
`web.py` solo los llama. Los modelos se cargan en un hilo aparte: en el primer
arranque se descargan decenas de MB y no deben congelar el video.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2
import numpy as np

from .actividades import EstabilizadorAct, clasificar as clasificar_actividad
from .caras import CarasDetector
from .pose import PoseDetector, dibujar_pose

log = logging.getLogger(__name__)

# un frame sin pose no borra la actividad hasta pasados estos segundos
_GRACIA_ACTIVIDAD_SEG = 5.0
_MARGEN_CARA_PX = 10


class CargaDiferida:
    """Construye un objeto pesado en un hilo y lo entrega cuando esta listo."""

    def __init__(self, fabrica: Callable[[], object], nombre: str):
        self._fabrica = fabrica
        self.nombre = nombre
        self._valor = None
        self._fallo = False
        self._hilo: threading.Thread | None = None

    def obtener(self):
        """El objeto si ya esta cargado; si no, lanza la carga y devuelve None."""
        if self._valor is not None:
            return self._valor
        if self._hilo is None and not self._fallo:
            self._hilo = threading.Thread(target=self._cargar, daemon=True,
                                          name=f"carga-{self.nombre}")
            self._hilo.start()
        return None

    def _cargar(self) -> None:
        try:
            self._valor = self._fabrica()
            log.info("modelo %s cargado", self.nombre)
        except Exception:
            self._fallo = True
            log.warning("modelo %s no disponible; se desactiva hasta que se "
                        "vuelva a activar", self.nombre, exc_info=True)

    @property
    def fallo(self) -> bool:
        return self._fallo

    def permitir_reintento(self) -> None:
        """Tras un fallo, deja que la proxima activacion vuelva a intentarlo."""
        if self._fallo and (self._hilo is None or not self._hilo.is_alive()):
            self._fallo = False
            self._hilo = None


def _centro(caja) -> tuple[float, float]:
    return (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2


def _iou(caja_a, caja_b) -> float:
    ax1, ay1, ax2, ay2 = caja_a
    bx1, by1, bx2, by2 = caja_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _dentro(punto, caja, margen: int = _MARGEN_CARA_PX) -> bool:
    return (caja[0] - margen <= punto[0] <= caja[2] + margen
            and caja[1] - margen <= punto[1] <= caja[3] + margen)


def persona_de_pose(caja, personas) -> int | None:
    """Empareja la caja de una pose con la persona trackeada (mismo ID visual).

    El modelo de pose usa su propio ByteTrack: sin este emparejamiento el #N
    de /actividades no seria el mismo #N que se dibuja en el video.
    """
    mejor, mejor_iou = None, 0.0
    for pid, caja_p in personas:
        iou = _iou(caja, caja_p)
        if iou > mejor_iou:
            mejor, mejor_iou = pid, iou
    if mejor is not None and mejor_iou >= 0.2:
        return mejor
    # respaldo: centro de la pose dentro de la caja de la persona
    centro = _centro(caja)
    for pid, caja_p in personas:
        if _dentro(centro, caja_p):
            return pid
    return None


class ModuloActividades:
    """Segunda pasada con pose: clasifica que hace cada persona."""

    def __init__(self, fabrica: Callable[[], object] = PoseDetector):
        self._carga = CargaDiferida(fabrica, "pose")
        self._estabilizadores: dict[int, EstabilizadorAct] = {}
        self._ultima: dict[int, float] = {}

    def apagar(self) -> bool:
        """Se llama con el interruptor apagado. True si habia estado que borrar."""
        self._carga.permitir_reintento()
        hay = bool(self._estabilizadores)
        self._estabilizadores.clear()
        self._ultima.clear()
        return hay

    def procesar(self, frame, mapa_ids: dict, extras: list,
                 ahora: float | None = None
                 ) -> tuple[dict[int, str], list[tuple[int, str]]] | None:
        """(actividad actual por persona, anuncios nuevos), o None si el modelo
        todavia no esta listo."""
        pose = self._carga.obtener()
        if pose is None:
            return None
        ahora = time.time() if ahora is None else ahora
        poses = pose.detectar(frame)
        personas = list(mapa_ids.items())
        anuncios: list[tuple[int, str]] = []
        vistos: set[int] = set()
        for p in poses:
            pid = persona_de_pose(p.caja, personas)
            if pid is None:
                continue
            est = self._estabilizadores.setdefault(pid, EstabilizadorAct())
            self._ultima[pid] = ahora
            anuncio = est.tick(clasificar_actividad(p, extras), ahora)
            if anuncio:
                anuncios.append((pid, anuncio))
            vistos.add(pid)
        for pid in list(self._estabilizadores):
            if (pid not in vistos
                    and ahora - self._ultima.get(pid, 0) > _GRACIA_ACTIVIDAD_SEG):
                del self._estabilizadores[pid]
                self._ultima.pop(pid, None)
        dibujar_pose(frame, poses)
        return ({pid: est.actual for pid, est in self._estabilizadores.items()},
                anuncios)


class ModuloCaras:
    """Detecta caras, las identifica y las asocia a la persona trackeada."""

    CADA_N_FRAMES = 3           # YuNet + SFace en cada frame costaria demasiado
    RECONOCIDA_MIN_SEG = 30.0   # no repetir "reconocido X" para el mismo nombre
    DESCONOCIDA_SEG = 120.0     # cooldown de "cara desconocida" por persona
    OLVIDO_SEG = 60.0           # una persona que no se ve se borra de memoria
    CONF_MINIMA = 0.7

    def __init__(self, cargar_conocidos: Callable[[], dict],
                 recarga: threading.Event,
                 al_ver_cara: Callable[[dict], None] | None = None,
                 fabrica: Callable[[], object] = CarasDetector):
        self._cargar_conocidos = cargar_conocidos
        self._recarga = recarga
        self._al_ver_cara = al_ver_cara
        self._carga = CargaDiferida(fabrica, "caras")
        self._listo = False  # conocidos ya volcados al detector
        self._frame_n = 0
        self.identidades: dict[int, tuple[str | None, float]] = {}
        self._visto: dict[int, float] = {}
        self._aviso_desconocida: dict[int, float] = {}
        self._reconocidos: dict[int, str] = {}
        self._ultimo_nombre: dict[str, float] = {}

    def apagar(self) -> None:
        self._carga.permitir_reintento()

    def procesar(self, frame, mapa_ids: dict, ahora: float | None = None
                 ) -> list[tuple[str, str, int]] | None:
        """Eventos nuevos (tipo, detalle, id_persona), o None si el modelo
        todavia no esta listo."""
        detector = self._carga.obtener()
        if detector is None:
            return None
        ahora = time.time() if ahora is None else ahora
        if not self._listo or self._recarga.is_set():
            self._recarga.clear()
            detector.set_conocidos(self._cargar_conocidos())
            self._listo = True

        eventos: list[tuple[str, str, int]] = []
        self._frame_n += 1
        if (self._frame_n - 1) % self.CADA_N_FRAMES == 0:
            eventos = self._analizar(detector, frame, mapa_ids, ahora)
        self._olvidar(mapa_ids, ahora)
        self._etiquetar(frame, mapa_ids)
        return eventos

    # -- internos ---------------------------------------------------------
    def _analizar(self, detector, frame, mapa_ids: dict,
                  ahora: float) -> list[tuple[str, str, int]]:
        eventos: list[tuple[str, str, int]] = []
        mejor = None
        for cara in detector.detectar(frame):
            if mejor is None or _area(cara["caja"]) > _area(mejor["caja"]):
                if cara["conf"] >= self.CONF_MINIMA:
                    mejor = cara
            centro = _centro(cara["caja"])
            pid = next((p for p, caja in mapa_ids.items()
                        if _dentro(centro, caja)), None)
            if pid is None:
                continue
            nombre, sim = detector.identificar(cara["emb"])
            self.identidades[pid] = (nombre, sim)
            self._visto[pid] = ahora
            if nombre is not None:
                if (self._reconocidos.get(pid) != nombre
                        and ahora - self._ultimo_nombre.get(nombre, 0.0)
                        >= self.RECONOCIDA_MIN_SEG):
                    self._reconocidos[pid] = nombre
                    self._ultimo_nombre[nombre] = ahora
                    eventos.append((
                        "persona_reconocida",
                        f"id {pid} = {nombre} ({sim:.0%})", pid))
            elif (cara["conf"] >= self.CONF_MINIMA
                  and ahora - self._aviso_desconocida.get(pid, 0.0)
                  >= self.DESCONOCIDA_SEG):
                self._aviso_desconocida[pid] = ahora
                eventos.append(("cara_desconocida",
                                f"id {pid} sin identificar", pid))
        if mejor is not None and self._al_ver_cara is not None:
            self._al_ver_cara(self._ficha(frame, mejor, ahora))
        return eventos

    @staticmethod
    def _ficha(frame, cara: dict, ahora: float) -> dict:
        """Cara mas grande del frame, con recorte JPEG para confirmarla en la UI."""
        x1, y1, x2, y2 = cara["caja"]
        mx, my = int((x2 - x1) * 0.25), int((y2 - y1) * 0.25)
        alto, ancho = frame.shape[:2]
        recorte = frame[max(0, y1 - my):min(alto, y2 + my),
                        max(0, x1 - mx):min(ancho, x2 + mx)]
        jpeg = b""
        if recorte.size:
            ok, buf = cv2.imencode(".jpg", recorte)
            jpeg = buf.tobytes() if ok else b""
        return {"emb": np.asarray(cara["emb"], dtype=np.float32),
                "t": ahora, "jpeg": jpeg}

    def _olvidar(self, mapa_ids: dict, ahora: float) -> None:
        """Libera la memoria de las personas que llevan un rato sin verse."""
        for pid in list(self.identidades):
            if pid in mapa_ids:
                self._visto.setdefault(pid, ahora)
                continue
            if ahora - self._visto.get(pid, 0.0) > self.OLVIDO_SEG:
                self.identidades.pop(pid, None)
                self._visto.pop(pid, None)
                self._reconocidos.pop(pid, None)
                self._aviso_desconocida.pop(pid, None)
        # el cooldown por nombre se poda aparte: es pequeno pero no debe crecer
        for nombre in [n for n, t in self._ultimo_nombre.items()
                       if ahora - t > 3600]:
            del self._ultimo_nombre[nombre]

    def _etiquetar(self, frame, mapa_ids: dict) -> None:
        """Escribe el nombre bajo la caja de cada persona identificada."""
        for pid, (nombre, sim) in self.identidades.items():
            caja = mapa_ids.get(pid)
            if nombre is None or caja is None:
                continue
            cv2.putText(frame, f"{nombre} {sim:.0%}",
                        (caja[0], min(caja[3] + 18, frame.shape[0] - 24)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 220, 120), 2,
                        cv2.LINE_AA)


def _area(caja) -> int:
    return max(0, caja[2] - caja[0]) * max(0, caja[3] - caja[1])
