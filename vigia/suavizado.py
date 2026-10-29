"""Suavizado temporal de keypoints por persona (One-Euro, adaptativo).

El esqueleto crudo de YOLO-pose tiembla entre frames (sobre todo manos y
pies), lo que hace parpadear al clasificador de actividad. Aqui cada
coordenada se filtra con un One-Euro: suaviza mucho cuando el punto esta
quieto y sigue rapido cuando se mueve (beta controla esa adaptacion).

- descarte por confianza: por debajo de `conf_min` no se actualiza el filtro
- mantenimiento breve: si un keypoint se pierde, se reutiliza su ultimo valor
  valido durante `mantener_seg` (p. ej. una rodilla tapada por la mesa)
"""
from __future__ import annotations

import math

import numpy as np

_CONF_MIN = 0.3


def _alpha(cutoff: float, dt: float) -> float:
    """Constante de suavizado de un filtro de primer orden para `cutoff` Hz."""
    if cutoff <= 0 or dt <= 0:
        return 1.0
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class _EstadoKpt:
    __slots__ = ("x", "dx", "t", "ultimo", "ultimo_t")

    def __init__(self):
        self.x = None       # posicion filtrada
        self.dx = None      # velocidad filtrada
        self.t = None
        self.ultimo = None  # ultimo (x, y, conf) valido
        self.ultimo_t = None


class SuavizadorKpts:
    """One-Euro por persona y keypoint. Determinista si se le pasa `t`."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.04,
                 d_cutoff: float = 1.0, conf_min: float = _CONF_MIN,
                 mantener_seg: float = 1.0, dt_max: float = 0.5):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.conf_min = float(conf_min)
        self.mantener_seg = float(mantener_seg)
        self.dt_max = float(dt_max)
        self._por_id: dict[int, list[_EstadoKpt]] = {}

    def _estados(self, track_id: int, n: int) -> list[_EstadoKpt]:
        estados = self._por_id.get(track_id)
        if estados is None or len(estados) != n:
            estados = [_EstadoKpt() for _ in range(n)]
            self._por_id[track_id] = estados
        return estados

    def suavizar(self, kpts, conf, track_id: int, t: float):
        """Devuelve (kpts_filtrados, conf_salida, conf_cruda)."""
        kpts = np.asarray(kpts, dtype=np.float64)
        conf = np.asarray(conf, dtype=np.float64)
        salida = kpts.copy()
        conf_out = conf.copy()
        estados = self._estados(int(track_id), len(kpts))
        for i, est in enumerate(estados):
            x, y = float(kpts[i][0]), float(kpts[i][1])
            c = float(conf[i]) if i < len(conf) else 0.0
            if c < self.conf_min:
                # keypoint no fiable: mantener el ultimo valor valido un rato
                if est.ultimo is not None and est.ultimo_t is not None \
                        and t - est.ultimo_t <= self.mantener_seg:
                    salida[i] = (est.ultimo[0], est.ultimo[1])
                    conf_out[i] = est.ultimo[2]
                continue
            if est.x is None or est.t is None:
                est.x = np.array([x, y], dtype=np.float64)
                est.dx = np.zeros(2, dtype=np.float64)
                est.t = t
            else:
                dt = t - est.t
                if dt <= 0:
                    dt = 1e-3
                dt = min(dt, self.dt_max)
                dx = (np.array([x, y]) - est.x) / dt
                a_d = _alpha(self.d_cutoff, dt)
                est.dx = a_d * dx + (1.0 - a_d) * est.dx
                velocidad = float(np.linalg.norm(est.dx))
                cutoff = self.min_cutoff + self.beta * velocidad
                a = _alpha(cutoff, dt)
                est.x = a * np.array([x, y]) + (1.0 - a) * est.x
                est.t = t
            est.ultimo = (float(est.x[0]), float(est.x[1]), c)
            est.ultimo_t = t
            salida[i] = (est.x[0], est.x[1])
            conf_out[i] = c
        return salida, conf_out, conf

    def olvidar(self, t: float) -> None:
        """Suelta las personas que llevan mucho sin aparecer."""
        for pid in list(self._por_id):
            estados = self._por_id[pid]
            if not estados or all(
                    e.ultimo_t is None or t - e.ultimo_t > self.mantener_seg * 5
                    for e in estados):
                del self._por_id[pid]

    def reset(self) -> None:
        self._por_id.clear()
