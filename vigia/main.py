"""Punto de entrada: webcam -> YOLO en vivo, con cajas, etiquetas y FPS.

Uso: python -m vigia.main      (tecla q para salir)
"""
from __future__ import annotations

import logging
import time
from collections import deque

import cv2
import numpy as np

from .captura import Camara, CamaraError
from .detector import Detector

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vigia")

_VERDE = (0, 200, 0)
_BLANCO = (255, 255, 255)


def dibujar(frame, detecciones, fps: float) -> None:
    for det in detecciones:
        x1, y1, x2, y2 = det.caja
        color = _VERDE if det.es_persona else (0, 160, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        etiqueta = f"{det.clase} {det.conf:.0%}"
        cv2.putText(frame, etiqueta, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    n_personas = sum(1 for d in detecciones if d.es_persona)
    texto = f"FPS {fps:5.1f} | personas {n_personas} | objetos {len(detecciones) - n_personas} | q salir"
    cv2.putText(frame, texto, (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _BLANCO, 1, cv2.LINE_AA)


def main() -> None:
    cam = Camara(abrir=False)
    cam.abrir_bloqueante()
    detector = Detector(conf=0.45)
    fps_hist: deque = deque(maxlen=30)
    log.info("Vigia en marcha. Pulsa 'q' para salir.")

    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = cam.leer()
            if not ok:
                # cámara caída -> aviso en pantalla mientras reintenta
                aviso = cv2.putText(np.zeros((240, 420, 3), np.uint8),
                                    "CAMARA CAIDA - reintentando...",
                                    (20, 130), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7, (0, 0, 255), 2)
                cv2.imshow("Vigia", aviso)
                if (cv2.waitKey(500) & 0xFF) == ord("q"):
                    break
                continue

            detecciones = detector.detectar(frame)
            dt = time.perf_counter() - t0
            fps_hist.append(1.0 / dt if dt > 0 else 0.0)
            dibujar(frame, detecciones, sum(fps_hist) / len(fps_hist))

            cv2.imshow("Vigia", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
    except CamaraError as e:
        log.error("Error de cámara: %s", e)
    except KeyboardInterrupt:
        log.info("Interrumpido por teclado")
    finally:
        cam.liberar()
        cv2.destroyAllWindows()
        log.info("Vigia cerrado")


if __name__ == "__main__":
    main()
