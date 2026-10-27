"""Dibujo de cajas/etiquetas/FPS/zonas y avisos sobre frames BGR.

Compartido por la ventana de escritorio (`main`) y el dashboard (`web`)
para no duplicar el pintado ni acoplar la web al punto de entrada CLI.
"""
from __future__ import annotations

import cv2
import numpy as np

from .detector import Deteccion

VERDE = (0, 200, 0)
AZUL = (0, 160, 255)
BLANCO = (255, 255, 255)
ROJO = (0, 0, 255)
MORADO = (150, 80, 200)


def dibujar(frame, detecciones: list[Deteccion], fps: float,
            pie: str | None = "q salir",
            ids_por_indice: dict[int, int] | None = None,
            ids_persona: set[int] | None = None,
            zonas: dict[str, tuple[int, int, int, int]] | None = None) -> None:
    """Pinta zonas, detecciones y barra inferior sobre `frame`.

    `ids_por_indice` mapea indice de deteccion -> id de tracking (Fase 2).
    `ids_persona` son las personas dentro segun el rastreador; si no se pasa,
    se cuentan las personas detectadas en este frame.
    """
    for nombre, caja in (zonas or {}).items():
        if all(v == 0 for v in caja):
            continue
        zx1, zy1, zx2, zy2 = caja
        cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), MORADO, 1)
        cv2.putText(frame, nombre, (zx1 + 3, zy1 + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, MORADO, 1, cv2.LINE_AA)

    ids_por_indice = ids_por_indice or {}
    for i, det in enumerate(detecciones):
        x1, y1, x2, y2 = det.caja
        color = VERDE if det.es_persona else AZUL
        pid = ids_por_indice.get(i) if det.es_persona else None
        if pid is not None:
            etiqueta = f"person #{pid} {det.conf:.0%}"
        else:
            etiqueta = f"{det.clase} {det.conf:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, etiqueta, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    if ids_persona is not None:
        n_personas = len(ids_persona)
    else:
        n_personas = sum(1 for d in detecciones if d.es_persona)
    n_objetos = len(detecciones) - sum(1 for d in detecciones if d.es_persona)
    texto = f"FPS {fps:5.1f} | personas {n_personas} | objetos {n_objetos}"
    if pie:
        texto += f" | {pie}"
    cv2.putText(frame, texto, (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, BLANCO, 1, cv2.LINE_AA)


def frame_aviso(texto: str, ancho: int = 1280, alto: int = 720) -> np.ndarray:
    """Frame negro con un mensaje centrado (para estados de error)."""
    frame = np.zeros((alto, ancho, 3), np.uint8)
    cv2.putText(frame, texto, (20, alto // 2), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, ROJO, 2, cv2.LINE_AA)
    return frame
