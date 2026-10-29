"""Servidor de desarrollo de la GUI con vídeo sintético y datos de `/vivo`.

Arranca la app real (`vigia.web.app`, con la BD de `vigia.db` en solo lectura
para eventos, clips y línea del tiempo) pero sin bucle de cámara: un hilo
alimenta `estado` con una escena sintética (una persona sentada ante un
escritorio que mueve un brazo) y `/vivo` devuelve los keypoints de esa misma
figura, para probar el overlay de esqueleto sin webcam.

Uso:
    .venv\\Scripts\\python scripts\\servir_gui_mock.py            # :8020
    .venv\\Scripts\\python scripts\\servir_gui_mock.py --puerto 8021
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ANCHO, ALTO = 960, 540


def figura(t: float) -> dict:
    """Keypoints COCO-17 (x, y, conf) de una persona sentada que teclea."""
    cx = 480 + 18 * math.sin(t * 0.4)
    respira = 3 * math.sin(t * 1.3)
    mano = 14 * math.sin(t * 3.0)
    k = {
        0: (cx, 205 + respira), 1: (cx - 9, 197 + respira), 2: (cx + 9, 197 + respira),
        3: (cx - 22, 205 + respira), 4: (cx + 22, 205 + respira),
        5: (cx - 52, 262 + respira), 6: (cx + 52, 262 + respira),
        7: (cx - 74, 335), 8: (cx + 74, 335),
        9: (cx - 40 + mano, 400), 10: (cx + 40 - mano, 400),
        11: (cx - 34, 430), 12: (cx + 34, 430),
        13: (cx - 44, 500), 14: (cx + 44, 500),
    }
    return {i: (x, y, 0.93) for i, (x, y) in k.items()} | {
        15: (cx - 44, 535, 0.35), 16: (cx + 44, 535, 0.35)}


HUESOS = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
          (11, 13), (12, 14), (0, 1), (0, 2), (1, 3), (2, 4)]


def dibujar_escena(t: float):
    import cv2
    import numpy as np

    img = np.zeros((ALTO, ANCHO, 3), np.uint8)
    img[:] = (38, 34, 30)
    cv2.rectangle(img, (0, 395), (ANCHO, ALTO), (52, 58, 66), -1)           # mesa
    cv2.rectangle(img, (640, 150), (900, 330), (70, 58, 40), -1)            # ventana
    cv2.rectangle(img, (650, 160), (890, 320), (120, 105, 80), -1)
    k = figura(t)
    cuerpo = (96, 106, 126)
    for a, b in [(5, 6), (5, 11), (6, 12), (11, 12), (5, 7), (7, 9), (6, 8), (8, 10)]:
        cv2.line(img, tuple(map(int, k[a][:2])), tuple(map(int, k[b][:2])), cuerpo, 34)
    cv2.circle(img, tuple(map(int, k[0][:2])), 36, (140, 150, 170), -1)
    cv2.rectangle(img, (400, 410), (560, 470), (24, 24, 28), -1)            # portátil
    cv2.putText(img, "MOCK", (16, 32), cv2.FONT_HERSHEY_SIMPLEX, .8, (160, 160, 170), 2)
    return img


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=8020)
    args = parser.parse_args()

    import cv2
    import uvicorn
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute
    from fastapi.staticfiles import StaticFiles

    import vigia.web as web

    static = Path(web.__file__).parent / "static"
    if not any(getattr(r, "path", None) == "/static" for r in web.app.routes):
        web.app.mount("/static", StaticFiles(directory=str(static)), name="static-dev")

    web.estado.ancho, web.estado.alto = ANCHO, ALTO
    web._actividades_personas.update({3: "usando PC"})

    def productor() -> None:
        t0 = time.time()
        while True:
            t = time.time() - t0
            img = dibujar_escena(t)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            web.estado.actualizar(buf.tobytes() if ok else None, 21.4, [], {3}, True,
                                  crudo=img)
            time.sleep(1 / 15)

    threading.Thread(target=productor, daemon=True).start()

    async def vivo() -> JSONResponse:
        t = time.time() % 100000
        k = figura(t)
        xs = [p[0] for p in k.values()]
        ys = [p[1] for p in k.values()]
        return JSONResponse({
            "t": time.time(), "ancho": ANCHO, "alto": ALTO, "fps": 21.4,
            "personas": [{
                "id": 3, "nombre": "Lucas", "caja": [min(xs) - 30, min(ys) - 45, max(xs) + 30, ALTO - 6],
                "actividad": "usando PC", "actividad_cruda": "usando PC", "desde_seg": 42.1,
                "kpts": [list(k[i]) for i in range(17)], "zona": "escritorio", "conf": 0.91}],
            "objetos": [{"clase": "laptop", "caja": [400, 410, 560, 470], "conf": 0.78}],
            "caras": [{"caja": [k[0][0] - 30, k[0][1] - 38, k[0][0] + 30, k[0][1] + 34], "nombre": "Lucas"}],
        })

    # delante de las rutas reales: /vivo todavía no existe en el backend
    web.app.router.routes.insert(0, APIRoute("/vivo", vivo, methods=["GET"]))

    print(f"GUI mock en http://{args.host}:{args.puerto}/  (vídeo sintético + /vivo)")
    uvicorn.run(web.app, host=args.host, port=args.puerto, log_level="warning")


if __name__ == "__main__":
    main()
