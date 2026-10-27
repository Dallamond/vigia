"""Diagnostico FPS: mide camara sola y deteccion sola.

Uso: python -m vigia.diagnostico_fps
"""
from __future__ import annotations

import time

import cv2
import numpy as np


def _medir_camara() -> None:
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        n = 0
        t0 = time.perf_counter()
        while n < 60:
            ok, _ = cap.read()
            if ok:
                n += 1
        dt = time.perf_counter() - t0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if n:
            print(f"solo camara 720p: {n / dt:.1f} fps (res real {w}x{h})")
        else:
            print("no se pudo leer ningun frame de la camara")
    finally:
        cap.release()


def _medir_deteccion() -> None:
    import torch
    from ultralytics import YOLO

    print("cuda disponible:", torch.cuda.is_available())
    m = YOLO("yolo11n.pt")
    f = np.zeros((480, 640, 3), np.uint8)
    for _ in range(10):
        m.predict(f, conf=0.4, verbose=False)
    t0 = time.perf_counter()
    for _ in range(30):
        m.predict(f, conf=0.4, verbose=False)
    print(f"solo deteccion 640: {30 / (time.perf_counter() - t0):.1f} fps "
          "(frame negro)")


def main() -> None:
    _medir_camara()
    _medir_deteccion()


if __name__ == "__main__":
    main()
