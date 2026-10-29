"""Tracking de personas por ID (ByteTrack vía Ultralytics) con motor de
eventos entra/sale, sin depender de YOLO (se alimenta con IDs ya trackeados).

Ronda 3 (percepcion): la entrada exige tiempo, confianza media y tamano
minimo; si aparece lejos del borde se exige mas tiempo. La salida usa un
timeout adaptativo: corto si la ultima caja estaba pegada al borde (se fue),
largo si estaba en el centro (probable oclusion). La re-identificacion
ligera vive en `ReasociadorIds` (geometria + histograma del torso).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# Evento = (tipo, id_tracking)
EVENTO_ENTRA = "persona_entra"
EVENTO_SALIO = "persona_salio"

# margen (fraccion del lado) para considerar que una caja toca el borde
_MARGEN_BORDE = 0.10

# ultima info (caja/conf) publicada por el reasociador; permite que el bucle
# real, que llama `rastreador.actualizar(set)` por compatibilidad, aplique
# confianza/tamano/borde sin cambiar `web.py`
_INFO_RECIENTE: dict[int, tuple[float, dict]] = {}
_INFO_DIMENSIONES: list[int | None] = [None, None]


def registrar_dimensiones(ancho: int, alto: int) -> None:
    """El detector publica el tamano del frame para el timeout por borde."""
    _INFO_DIMENSIONES[0] = int(ancho)
    _INFO_DIMENSIONES[1] = int(alto)


def _defecto(clave: str):
    try:
        from .config import DEFAULTS
        return DEFAULTS.get(clave)
    except Exception:
        return None


_DEFECTO = object()


class _PersonaEstado:
    __slots__ = ("dentro", "primera_vez", "ultima_vez", "anunciada",
                 "caja", "conf_media", "n", "toca_borde", "alto_max")

    def __init__(self, ahora: float):
        self.dentro = True
        self.primera_vez = ahora
        self.ultima_vez = ahora
        self.anunciada = False
        self.caja = None          # ultima caja vista
        self.conf_media = 0.0
        self.n = 0
        self.toca_borde = False   # la ultima posicion tocaba el borde
        self.alto_max = 0


def _toca_borde(caja, ancho: int | None, alto: int | None,
                margen: float = _MARGEN_BORDE) -> bool:
    if not caja or not ancho or not alto:
        return False
    x1, y1, x2, y2 = caja
    mx, my = ancho * margen, alto * margen
    return x1 <= mx or y1 <= my or x2 >= ancho - mx or y2 >= alto - my


class RastreadorPersonas:
    """A partir de los IDs de personas de cada frame genera eventos:

    - `persona_entra`: aparece un ID nuevo o un ID que ya había salido
    - `persona_salio`: un ID deja de verse durante un timeout

    `actualizar(ids)` mantiene la compatibilidad antigua. La version rica es
    `actualizar(ids, info, ancho, alto)` con `info[id] = {"caja", "conf"}`.
    """

    def __init__(self, timeout_seg: float = 10.0, confirmar_seg: float = 0.0,
                 reloj=time.monotonic, *,
                 timeout_borde_seg=_DEFECTO,
                 timeout_centro_seg=_DEFECTO,
                 confirmar_interior_seg=_DEFECTO,
                 conf_minima: float = 0.0, alto_min_rel: float = 0.0,
                 margen_borde: float = _MARGEN_BORDE):
        self.timeout_seg = float(timeout_seg)
        # un ID debe verse este tiempo seguido antes de anunciar `persona_entra`
        self.confirmar_seg = float(confirmar_seg)
        self.reloj = reloj
        # los valores omitidos salen de config; `None` explicito los desactiva
        if timeout_borde_seg is _DEFECTO:
            timeout_borde_seg = _defecto("timeout_borde_seg")
        if timeout_centro_seg is _DEFECTO:
            timeout_centro_seg = _defecto("timeout_centro_seg")
        if confirmar_interior_seg is _DEFECTO:
            confirmar_interior_seg = _defecto("confirmar_interior_seg")
        if not conf_minima:
            conf_minima = _defecto("entrada_conf_min") or 0.0
        if not alto_min_rel:
            alto_min_rel = _defecto("entrada_alto_min") or 0.0
        # salida adaptativa: borde = se fue; centro = probable oclusion
        self.timeout_borde_seg = (None if timeout_borde_seg is None
                                  else float(timeout_borde_seg))
        self.timeout_centro_seg = (None if timeout_centro_seg is None
                                   else float(timeout_centro_seg))
        # una aparicion en medio del encuadre (sombras, reflejos) tarda mas
        self.confirmar_interior_seg = (None if confirmar_interior_seg is None
                                       else float(confirmar_interior_seg))
        self.conf_minima = float(conf_minima or 0.0)
        self.alto_min_rel = float(alto_min_rel or 0.0)
        self.margen_borde = float(margen_borde)
        self._por_id: dict[int, _PersonaEstado] = {}
        self._ultimo_ancho: int | None = None
        self._ultimo_alto: int | None = None

    def _timeout_de(self, st: _PersonaEstado) -> float:
        # sin caja (API antigua) no se puede saber si fue por el borde
        if st.caja is None:
            return self.timeout_seg
        if st.toca_borde and self.timeout_borde_seg is not None:
            return self.timeout_borde_seg
        if not st.toca_borde and self.timeout_centro_seg is not None:
            return self.timeout_centro_seg
        return self.timeout_seg

    def _confirmacion_de(self, st: _PersonaEstado) -> float:
        # el tiempo extra "en medio del encuadre" solo tiene sentido si el
        # bucle nos dio la caja; sin informacion se mantiene el tiempo base
        if (st.caja is not None and not st.toca_borde
                and self.confirmar_interior_seg is not None):
            return self.confirmar_interior_seg
        return self.confirmar_seg

    def _criterios(self, st: _PersonaEstado) -> bool:
        """Confianza media y tamano minimo (si se pidieron)."""
        if st.n == 0:
            return True
        if self.conf_minima and st.conf_media < self.conf_minima:
            return False
        if self.alto_min_rel and self._ultimo_alto:
            if st.alto_max < self.alto_min_rel * self._ultimo_alto:
                return False
        return True

    def actualizar(self, ids_presentes: set[int],
                   info: dict[int, dict] | None = None,
                   ancho: int | None = None,
                   alto: int | None = None) -> tuple[tuple[str, int], ...]:
        """Pasa los IDs vistos en este frame; devuelve los eventos generados.

        `info` es opcional: {id: {"caja": (x1,y1,x2,y2), "conf": float}}.
        Sin `info` se comporta como antes (solo tiempo de confirmacion).
        """
        ahora = self.reloj()
        self._ultimo_ancho = ancho or self._ultimo_ancho or _INFO_DIMENSIONES[0]
        self._ultimo_alto = alto or self._ultimo_alto or _INFO_DIMENSIONES[1]
        if info is None:
            info = {pid: datos for pid, (t, datos) in _INFO_RECIENTE.items()
                    if ahora - t <= max(self.timeout_seg, 1.0)}
        info = info or {}
        eventos: list[tuple[str, int]] = []

        # salidas: sin verse durante el timeout que corresponda a su ultima
        # posicion. Se borra el estado para que el diccionario no crezca sin
        # limite y para que una reaparicion vuelva a anunciarse.
        # Nunca se emite salida de alguien cuya entrada no se anuncio.
        for pid, st in list(self._por_id.items()):
            if ahora - st.ultima_vez > self._timeout_de(st):
                del self._por_id[pid]
                if st.anunciada:
                    eventos.append((EVENTO_SALIO, pid))
                    log.info("persona_salio id=%s", pid)

        # entradas: cualquier ID que no estabamos siguiendo
        for pid in ids_presentes:
            datos = info.get(pid) or {}
            st = self._por_id.get(pid)
            if st is None:
                st = self._por_id[pid] = _PersonaEstado(ahora)
            st.ultima_vez = ahora
            st.dentro = True
            caja = datos.get("caja")
            if caja:
                st.caja = caja
                st.toca_borde = _toca_borde(caja, self._ultimo_ancho,
                                            self._ultimo_alto,
                                            self.margen_borde)
                st.alto_max = max(st.alto_max, caja[3] - caja[1])
            conf = datos.get("conf")
            if conf is not None:
                st.n += 1
                st.conf_media += (float(conf) - st.conf_media) / st.n
            if not st.anunciada:
                minimo = self._confirmacion_de(st)
                if (ahora - st.primera_vez >= minimo
                        and self._criterios(st)):
                    st.anunciada = True
                    eventos.append((EVENTO_ENTRA, pid))
                    log.info("persona_entra id=%s", pid)
        return tuple(eventos)

    def ids_dentro(self) -> set[int]:
        return {pid for pid, st in self._por_id.items() if st.dentro}

    def reset(self) -> None:
        self._por_id.clear()


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
             - inter)
    return inter / union if union > 0 else 0.0


def _distancia_centros(a, b) -> float:
    return ((((a[0] + a[2]) - (b[0] + b[2])) / 2) ** 2
            + (((a[1] + a[3]) - (b[1] + b[3])) / 2) ** 2) ** 0.5


def _similitud_huella(h1, h2) -> float:
    """Correlacion de histogramas (0..1); sin datos, 0."""
    if h1 is None or h2 is None:
        return 0.0
    try:
        import numpy as np
        a = np.asarray(h1, dtype=np.float32).ravel()
        b = np.asarray(h2, dtype=np.float32).ravel()
        if a.size != b.size or not a.any() or not b.any():
            return 0.0
        a = a - a.mean()
        b = b - b.mean()
        den = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(a.dot(b) / den) if den else 0.0
    except Exception:
        return 0.0


class ReasociadorIds:
    """IDs de persona estables aunque ByteTrack cambie el suyo.

    ByteTrack (sin re-identificacion) crea un ID nuevo cada vez que pierde a
    alguien unos frames (oclusion, silla, baja confianza). Sin esto cada
    cambio es un `persona_entra` falso y reinicia la permanencia.

    Se usa geometria (IoU + distancia de centros) y, si esta disponible, la
    huella de apariencia del torso (`Deteccion.huella`: histograma HSV). La
    apariencia permite reasociar cuando la persona reaparece desplazada pero
    con la misma ropa; sin huella el comportamiento es el antiguo.
    """

    def __init__(self, ventana_seg: float = 8.0, iou_min: float = 0.15,
                 dist_rel: float = 0.6, reloj=time.monotonic, *,
                 reid: bool = False, sim_min: float = 0.55):
        self.ventana_seg = ventana_seg
        self.iou_min = iou_min
        self.dist_rel = dist_rel
        self.reloj = reloj
        self.reid = bool(reid)
        self.sim_min = float(sim_min)
        self._alias: dict[int, int] = {}                 # id crudo -> estable
        self._ultimo: dict[int, tuple[tuple, float]] = {}  # estable -> (caja, t)
        self._huella: dict[int, object] = {}             # estable -> histograma
        self._raw_visto: dict[int, float] = {}

    def mapear(self, ids_por_indice: dict[int, int], dets,
               frame=None) -> dict[int, int]:
        """{indice: id_crudo} -> {indice: id_estable} (solo personas se tocan)."""
        ahora = self.reloj()
        salida: dict[int, int] = {}
        presentes: set[int] = set()
        pendientes: list[int] = []
        for i, raw in ids_por_indice.items():
            if not dets[i].es_persona:
                salida[i] = raw
            elif raw in self._alias:
                salida[i] = self._alias[raw]
                presentes.add(salida[i])
            else:
                pendientes.append(i)
        for i in pendientes:
            raw = ids_por_indice[i]
            caja = dets[i].caja
            mejor, mejor_d = None, float("inf")
            for estable, (caja_vieja, t) in self._ultimo.items():
                if estable in presentes or ahora - t > self.ventana_seg:
                    continue
                d = _distancia_centros(caja_vieja, caja)
                lado = max(caja_vieja[2] - caja_vieja[0],
                           caja_vieja[3] - caja_vieja[1], 1)
                if (_iou(caja_vieja, caja) >= self.iou_min
                        or d <= self.dist_rel * lado) and d < mejor_d:
                    mejor, mejor_d = estable, d
            if mejor is None and self.reid:
                mejor = self._por_apariencia(dets[i], presentes, ahora)
            estable = mejor if mejor is not None else raw
            if mejor is not None:
                log.info("id %s reasociado a id %s", raw, mejor)
            self._alias[raw] = estable
            salida[i] = estable
            presentes.add(estable)
        for i, estable in salida.items():
            if dets[i].es_persona:
                self._ultimo[estable] = (dets[i].caja, ahora)
                _INFO_RECIENTE[estable] = (
                    ahora, {"caja": dets[i].caja, "conf": dets[i].conf})
                huella = getattr(dets[i], "huella", None)
                if huella is not None:
                    self._huella[estable] = huella
                self._raw_visto[ids_por_indice[i]] = ahora
        # poda: no guardar historia de personas que ya no estan
        for estable in [e for e, (ts, _) in _INFO_RECIENTE.items()
                        if ahora - ts > self.ventana_seg * 4]:
            del _INFO_RECIENTE[estable]
        for estable in [e for e, (_, t) in self._ultimo.items()
                        if ahora - t > self.ventana_seg * 4]:
            del self._ultimo[estable]
            self._huella.pop(estable, None)
        for raw in [r for r, t in self._raw_visto.items()
                    if ahora - t > self.ventana_seg * 4]:
            del self._raw_visto[raw]
            self._alias.pop(raw, None)
        return salida

    def _por_apariencia(self, det, presentes: set[int], ahora: float):
        huella = getattr(det, "huella", None)
        if huella is None:
            return None
        mejor, mejor_sim = None, self.sim_min
        for estable, (_, t) in self._ultimo.items():
            if estable in presentes or ahora - t > self.ventana_seg:
                continue
            sim = _similitud_huella(huella, self._huella.get(estable))
            if sim >= mejor_sim:
                mejor, mejor_sim = estable, sim
        return mejor

    def reset(self) -> None:
        self._alias.clear()
        self._ultimo.clear()
        self._huella.clear()
        self._raw_visto.clear()
