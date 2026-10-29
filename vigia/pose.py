"""Detector de pose humana (YOLO11-pose): 17 keypoints + asociacion a las
personas ya rastreadas por el detector principal.

Esto alimenta el clasificador de actividades. El modelo es configurable
(`pose_modelo`: n/s/m o ruta a un .pt) y el tamano de inferencia
(`pose_imgsz`). La asociacion es uno-a-uno (IoU + distancia de centros), no
por orden: dos personas cruzadas ya no se intercambian el esqueleto. El
suavizado temporal vive en `suavizado.py`.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from .suavizado import SuavizadorKpts

log = logging.getLogger(__name__)

# indices de keypoints COCO (17)
NARIZ, HOMBRO_IZQ, HOMBRO_DER = 0, 5, 6
CODO_IZQ, CODO_DER = 7, 8
MUNECA_IZQ, MUNECA_DER = 9, 10
CADERA_IZQ, CADERA_DER = 11, 12
RODILLA_IZQ, RODILLA_DER = 13, 14
TOBILLO_IZQ, TOBILLO_DER = 15, 16
OJO_IZQ, OJO_DER, OREJA_IZQ, OREJA_DER = 1, 2, 3, 4

UMBRAL_KPT = 0.3
_RAIZ = Path(__file__).resolve().parent.parent
_TAMANOS = {"n", "s", "m", "l", "x"}


def resolver_modelo(modelo: str | Path) -> str:
    """'n' -> yolo11n-pose.pt; si existe en la raiz, ruta absoluta."""
    texto = str(modelo or "n").strip()
    if texto in _TAMANOS:
        texto = f"yolo11{texto}-pose.pt"
    elif texto.startswith("yolo11") and not texto.endswith(".pt"):
        texto = f"{texto}-pose.pt"
    ruta = Path(texto)
    if not ruta.is_absolute():
        candidata = _RAIZ / ruta
        if candidata.exists():
            return str(candidata)
    return texto


class PersonaPose:
    """Una persona con su esqueleto en un frame dado."""

    __slots__ = ("track_id", "kpts", "conf", "caja", "conf_cruda",
                 "kpts_crudos", "score")

    def __init__(self, track_id: int, kpts: np.ndarray, conf: np.ndarray,
                 caja: tuple, conf_cruda: np.ndarray | None = None,
                 kpts_crudos: np.ndarray | None = None,
                 score: float = 0.0):
        self.track_id = track_id
        self.kpts = kpts
        self.conf = conf
        self.caja = caja
        self.conf_cruda = conf if conf_cruda is None else conf_cruda
        self.kpts_crudos = kpts if kpts_crudos is None else kpts_crudos
        self.score = float(score)

    def punto(self, idx: int, conf_min: float | None = None):
        """(x, y) del keypoint si es digno de confianza, si no None."""
        try:
            umbral = UMBRAL_KPT if conf_min is None else float(conf_min)
            if float(self.conf[idx]) < umbral:
                return None
            return float(self.kpts[idx][0]), float(self.kpts[idx][1])
        except (IndexError, TypeError):
            return None

    def puntos(self, *indices):
        return [self.punto(i) for i in indices]


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
             - inter)
    return inter / union if union > 0 else 0.0


def _centro(caja):
    return (caja[0] + caja[2]) / 2.0, (caja[1] + caja[3]) / 2.0


def asociar_poses(cajas_pose, personas, iou_min: float = 0.05
                  ) -> dict[int, int]:
    """Asocia poses (indice) a personas `(id, caja)` uno-a-uno.

    Coste = 0.65*(1-IoU) + 0.35*distancia_normalizada. Se ordenan todos los
    pares y se aceptan de menor a mayor coste (Hungaro simplificado); una
    persona y una pose no se usan dos veces. Devuelve {indice_pose: id}.
    """
    pares = []
    for i, cp in enumerate(cajas_pose):
        cx, cy = _centro(cp)
        area = max((cp[2] - cp[0]) * (cp[3] - cp[1]), 1.0)
        for pid, cpp in personas:
            iou = _iou(cp, cpp)
            dx, dy = cx - _centro(cpp)[0], cy - _centro(cpp)[1]
            dist = math.hypot(dx, dy)
            escala = max(math.sqrt(area), 1.0)
            norma = min(dist / (escala * 1.5), 1.0)
            centro_dentro = (cpp[0] <= cx <= cpp[2] and cpp[1] <= cy <= cpp[3])
            if iou < iou_min and not centro_dentro and norma > 0.9:
                continue
            coste = 0.65 * (1.0 - iou) + 0.35 * norma
            pares.append((coste, i, pid))
    pares.sort(key=lambda p: p[0])
    usadas: set[int] = set()
    usados_pid: set[int] = set()
    salida: dict[int, int] = {}
    for coste, i, pid in pares:
        if i in usadas or pid in usados_pid or coste > 0.95:
            continue
        salida[i] = pid
        usadas.add(i)
        usados_pid.add(pid)
    return salida


class PoseDetector:
    """Envuelve YOLO-pose. `detectar(frame, personas)` asocia a los IDs."""

    def __init__(self, modelo: str = "n", conf: float = 0.35,
                 imgsz: int = 640, conf_kpt: float = UMBRAL_KPT,
                 suavizar: bool = True, mantener_seg: float = 1.0,
                 dispositivo: str | None = None):
        self.model = YOLO(resolver_modelo(modelo))
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.conf_kpt = float(conf_kpt)
        self.dispositivo = dispositivo
        self.suavizador = (SuavizadorKpts(conf_min=self.conf_kpt,
                                          mantener_seg=mantener_seg)
                           if suavizar else None)

    def detectar(self, frame, personas=None) -> list[PersonaPose]:
        """Poses del frame; si `personas` = [(id, caja)], asigna `track_id`."""
        args = {"conf": self.conf, "verbose": False, "imgsz": self.imgsz}
        if self.dispositivo:
            args["device"] = self.dispositivo
        resultados = self.model.predict(frame, **args)
        crudas: list[tuple[tuple, np.ndarray, np.ndarray, float]] = []
        for r in resultados:
            if r.boxes is None or r.keypoints is None:
                continue
            for i in range(len(r.boxes)):
                kp_xy = r.keypoints.xy[i].cpu().numpy().astype(np.float64)
                kp_conf = (r.keypoints.conf[i].cpu().numpy().astype(np.float64)
                           if r.keypoints.conf is not None
                           else np.zeros(17, dtype=np.float64))
                caja = tuple(int(v) for v in r.boxes.xyxy[i].cpu().numpy())
                crudas.append((caja, kp_xy, kp_conf,
                               float(r.boxes.conf[i].cpu())))
        if not crudas:
            return []
        mapa: dict[int, int] = {}
        if personas:
            if isinstance(personas, dict):
                personas = list(personas.items())
            mapa = asociar_poses([c[0] for c in crudas], list(personas))
        t = getattr(self, "_t", None)
        salida: list[PersonaPose] = []
        for i, (caja, kp_xy, kp_conf, score) in enumerate(crudas):
            pid = mapa.get(i, -1)
            if self.suavizador is not None and pid >= 0 and t is not None:
                kp_f, conf_f, conf_cruda = self.suavizador.suavizar(
                    kp_xy, kp_conf, pid, t)
                salida.append(PersonaPose(pid, kp_f, conf_f, caja,
                                          conf_cruda=conf_cruda,
                                          kpts_crudos=kp_xy, score=score))
            else:
                salida.append(PersonaPose(pid, kp_xy, kp_conf, caja,
                                          score=score))
        return salida

    def poner_tiempo(self, t: float) -> None:
        """Reloj simulado/real para el suavizado (lo llama el modulo)."""
        self._t = float(t)

    def olvidar(self, t: float) -> None:
        if self.suavizador is not None:
            self.suavizador.olvidar(t)


# -- dibujo ---------------------------------------------------------------
# (indice_a, indice_b, color BGR)
HUESOS = (
    (HOMBRO_IZQ, HOMBRO_DER, (220, 220, 220)),      # hombros
    (HOMBRO_IZQ, CADERA_IZQ, (210, 160, 90)),       # tronco izq
    (HOMBRO_DER, CADERA_DER, (210, 160, 90)),       # tronco der
    (CADERA_IZQ, CADERA_DER, (210, 160, 90)),       # cadera
    (HOMBRO_IZQ, CODO_IZQ, (80, 210, 80)),          # brazo izq
    (CODO_IZQ, MUNECA_IZQ, (80, 210, 80)),
    (HOMBRO_DER, CODO_DER, (80, 140, 255)),         # brazo der
    (CODO_DER, MUNECA_DER, (80, 140, 255)),
    (CADERA_IZQ, RODILLA_IZQ, (240, 130, 60)),      # pierna izq
    (RODILLA_IZQ, TOBILLO_IZQ, (240, 130, 60)),
    (CADERA_DER, RODILLA_DER, (60, 130, 240)),      # pierna der
    (RODILLA_DER, TOBILLO_DER, (60, 130, 240)),
)
CABEZA = ((NARIZ, OJO_IZQ), (NARIZ, OJO_DER), (OJO_IZQ, OREJA_IZQ),
          (OJO_DER, OREJA_DER))


def dibujar_pose(frame, poses, conf_min: float = UMBRAL_KPT,
                 etiquetas: dict[int, str] | None = None) -> None:
    """Pinta huesos por colores y puntos con confianza, sin caja."""
    for p in poses:
        for a, b, color in HUESOS:
            pa, pb = p.punto(a, conf_min), p.punto(b, conf_min)
            if pa and pb:
                cv2.line(frame, (int(pa[0]), int(pa[1])),
                         (int(pb[0]), int(pb[1])), color, 2, cv2.LINE_AA)
        for a, b in CABEZA:
            pa, pb = p.punto(a, conf_min), p.punto(b, conf_min)
            if pa and pb:
                cv2.line(frame, (int(pa[0]), int(pa[1])),
                         (int(pb[0]), int(pb[1])), (200, 200, 200), 1,
                         cv2.LINE_AA)
        for idx in range(17):
            if float(p.conf[idx]) < conf_min:
                continue
            x, y = int(p.kpts[idx][0]), int(p.kpts[idx][1])
            c = (0, 255, 255) if idx in (5, 6, 11, 12) else (0, 200, 200)
            cv2.circle(frame, (x, y), 3, c, -1, cv2.LINE_AA)
        if etiquetas and p.track_id in etiquetas:
            texto = etiquetas[p.track_id]
            x1, y1 = p.caja[0], p.caja[1]
            cv2.putText(frame, texto, (x1, max(y1 - 8, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 230, 230), 1,
                        cv2.LINE_AA)
