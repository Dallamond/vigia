"""Punto de entrada: webcam -> YOLO en vivo, con cajas, etiquetas y FPS.

Uso: python -m vigia.main      (tecla q para salir)
"""
from __future__ import annotations

import logging
import time
from collections import deque

import cv2

from .captura import Camara
from .detector import Detector
from .dibujo import dibujar, frame_aviso

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vigia")

_CONF = 0.45


def main() -> None:
    cam = Camara(abrir=False)
    try:
        detector = Detector(conf=_CONF)
    except Exception:
        log.exception("No se pudo cargar el modelo YOLO (¿sin internet en el "
                      "primer arranque?). Revisa 'yolo11n.pt'.")
        return

    fps_hist: deque[float] = deque(maxlen=30)
    log.info("Vigia en marcha. Pulsa 'q' para salir.")

    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = cam.leer()
            if not ok:
                # cámara caída -> aviso en pantalla mientras reintenta
                cv2.imshow("Vigia", frame_aviso("CAMARA CAIDA - reintentando...",
                                                cam.ancho, cam.alto))
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
    except KeyboardInterrupt:
        log.info("Interrumpido por teclado")
    except Exception:
        log.exception("Error inesperado en el bucle principal")
    finally:
        cam.liberar()
        cv2.destroyAllWindows()
        log.info("Vigia cerrado")


if __name__ == "__main__":
    main()
