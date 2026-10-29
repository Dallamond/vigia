"""Galeria de caras sin identificar: agrupa por parecido y guarda varias tomas.

Cada cara que el reconocedor no identifica llega a `ver()`. Si se parece a un
grupo ya visto (similitud coseno de SFace) se cuenta como otra vista de esa
persona y, si aporta un angulo distinto, se guarda como toma nueva (hasta
`MAX_TOMAS`). Si no se parece a nadie, nace un grupo nuevo. Desde el panel el
usuario le pone nombre (el grupo pasa a ser una persona conocida) o lo descarta.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from .caras import EMB_DIM, _SIM_MIN

log = logging.getLogger("vigia.galeria")

MAX_TOMAS = 8          # fotos por persona sin identificar
MAX_GRUPOS = 40        # se olvidan los grupos vistos hace mas tiempo
CADA_SEG = 1.5         # como mucho una cara analizada cada tanto
TOMA_NUEVA_MAX_SIM = 0.92   # una toma casi identica a otra no aporta nada
TOMA_ENTRE_SEG = 4.0   # y entre tomas de un mismo grupo, un respiro


def _unit(emb) -> np.ndarray | None:
    v = np.asarray(emb, dtype=np.float32).reshape(-1)
    if v.size != EMB_DIM or not np.all(np.isfinite(v)):
        return None
    return v / max(float(np.linalg.norm(v)), 1e-6)


class GaleriaDesconocidos:
    def __init__(self, almacen, cada_seg: float = CADA_SEG):
        self._almacen = almacen
        self._cada = cada_seg
        self._lock = threading.Lock()
        self._ultimo = float("-inf")
        self._ultima_toma: dict[int, float] = {}
        # grupo -> matriz (n, EMB_DIM) de embeddings normalizados
        self._grupos: dict[int, list[np.ndarray]] | None = None

    def _cargar(self) -> dict[int, list[np.ndarray]]:
        if self._grupos is None:
            grupos: dict[int, list[np.ndarray]] = {}
            for _, g, blob in self._almacen.desconocidos_tomas():
                v = _unit(np.frombuffer(blob, dtype=np.float32))
                if v is not None:
                    grupos.setdefault(g, []).append(v)
            self._grupos = grupos
        return self._grupos

    def olvidar_cache(self) -> None:
        with self._lock:
            self._grupos = None

    def ver(self, ficha: dict, ahora: float | None = None) -> None:
        """`ficha`: {emb, jpeg, t} de una cara que no se ha identificado."""
        ahora = time.time() if ahora is None else ahora
        v = _unit(ficha.get("emb"))
        if v is None or not ficha.get("jpeg"):
            return
        with self._lock:
            if ahora - self._ultimo < self._cada:
                return
            self._ultimo = ahora
            try:
                self._registrar(v, ficha["jpeg"], ahora)
            except Exception:
                log.exception("no se pudo guardar la cara sin identificar")

    def _registrar(self, v: np.ndarray, jpeg: bytes, ahora: float) -> None:
        grupos = self._cargar()
        mejor, mejor_sim = None, -1.0
        for g, muestras in grupos.items():
            sim = float(np.max(np.stack(muestras) @ v))
            if sim > mejor_sim:
                mejor, mejor_sim = g, sim
        if mejor is not None and mejor_sim >= _SIM_MIN:
            self._almacen.tocar_desconocido(mejor)
            muestras = grupos[mejor]
            if (len(muestras) < MAX_TOMAS
                    and mejor_sim < TOMA_NUEVA_MAX_SIM
                    and ahora - self._ultima_toma.get(mejor, 0.0) >= TOMA_ENTRE_SEG):
                self._almacen.anadir_toma_desconocido(mejor, v.tobytes(), jpeg)
                muestras.append(v)
                self._ultima_toma[mejor] = ahora
            return
        g = self._almacen.nuevo_desconocido()
        self._almacen.anadir_toma_desconocido(g, v.tobytes(), jpeg)
        grupos[g] = [v]
        self._ultima_toma[g] = ahora
        if len(grupos) > MAX_GRUPOS:
            self._almacen.podar_desconocidos(MAX_GRUPOS)
            self._grupos = None
