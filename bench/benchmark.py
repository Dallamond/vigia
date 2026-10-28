"""Benchmark de inferencia de Vigia (YOLO11n/s/m y pose).

Mide FPS y latencias p50/p95 con frames sintéticos o un clip de vídeo, para
decidir cuántas cámaras caben por GPU a 10-15 FPS. No arranca Vigia ni toca la
base de datos.

Ejemplos:
    python bench/benchmark.py
    python bench/benchmark.py --modelos yolo11n.pt,yolo11s.pt,yolo11m.pt,yolo11n-pose.pt
    python bench/benchmark.py --fuente clips/20261027_212633_permanencia.webm --lote 4
    python bench/benchmark.py --resolucion 1280 --iteraciones 40 --json bench/salida/1280.json

Los FPS son el techo de inferencia: no incluyen lectura de cámara, dibujo,
tracking de personas ni la segunda pasada de pose/caras del servicio real.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
MODELOS_POR_DEFECTO = ("yolo11n.pt", "yolo11s.pt", "yolo11m.pt",
                       "yolo11n-pose.pt")


def percentil(valores: list[float], p: float) -> float:
    """Percentil con interpolación lineal (p. ej. 50, 95)."""
    if not valores:
        return 0.0
    orden = sorted(valores)
    if len(orden) == 1:
        return float(orden[0])
    pos = (len(orden) - 1) * (p / 100.0)
    bajo = math.floor(pos)
    alto = math.ceil(pos)
    if bajo == alto:
        return float(orden[bajo])
    return float(orden[bajo] + (orden[alto] - orden[bajo]) * (pos - bajo))


def estimar_camaras(fps: float, margen: float = 0.8) -> dict:
    """Cámaras que caben dejando `margen` para el resto del pipeline."""
    disponible = max(0.0, fps * margen)
    return {
        "margen": margen,
        "fps_disponibles": round(disponible, 2),
        "a_10_fps": int(disponible // 10),
        "a_15_fps": int(disponible // 15),
    }


class Fuente:
    """Frames sintéticos o un clip; al acabar el clip vuelve a empezar."""

    def __init__(self, fuente: str, ancho: int = 640, alto: int = 480):
        self.fuente = fuente
        self.ancho, self.alto = ancho, alto
        self.cap = None
        self.sinteticos: list[np.ndarray] = []
        self._i = 0
        if fuente == "sintetico":
            self.sinteticos = self._generar()
        else:
            ruta = Path(fuente)
            if not ruta.is_absolute():
                ruta = RAIZ / ruta
            if not ruta.is_file():
                raise FileNotFoundError(f"no existe el vídeo: {ruta}")
            self.cap = cv2.VideoCapture(str(ruta))
            if not self.cap.isOpened():
                raise RuntimeError(f"OpenCV no puede abrir {ruta}")
            self.ancho = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or ancho
            self.alto = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or alto

    def _generar(self) -> list[np.ndarray]:
        rng = np.random.default_rng(7)
        frames = []
        for k in range(8):
            frame = np.zeros((self.alto, self.ancho, 3), dtype=np.uint8)
            frame[:, :] = (18 + k * 3, 22, 28)
            # Degradado suave y formas tipo persona/objeto para que haya cajas
            # candidatas y el NMS no sea trivial.
            for x in range(0, self.ancho, 16):
                frame[:, x:x + 16] = (18 + k * 3 + (x % 40), 22, 28)
            for j in range(3):
                x = 60 + j * (self.ancho // 4)
                y = 80 + (j % 2) * 60
                cv2.rectangle(frame, (x, y), (x + 80, y + 220),
                              (90 + j * 30, 70, 55), -1)
                cv2.circle(frame, (x + 40, y - 18), 18, (120, 100, 80), -1)
            cv2.rectangle(frame, (self.ancho - 220, self.alto - 130),
                          (self.ancho - 60, self.alto - 40),
                          (70, 90, 120), -1)
            frame = cv2.GaussianBlur(frame, (5, 5), 0)
            frames.append(frame)
        return frames

    def lote(self, n: int) -> list[np.ndarray]:
        salida = []
        for _ in range(n):
            if self.sinteticos:
                salida.append(self.sinteticos[self._i % len(self.sinteticos)].copy())
                self._i += 1
                continue
            ok, frame = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
            if not ok:
                raise RuntimeError("no se pudo leer ningún frame del vídeo")
            salida.append(frame)
        return salida

    def cerrar(self) -> None:
        if self.cap is not None:
            self.cap.release()


def _sincronizar(torch) -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _dispositivo(pedido: str | None) -> str:
    if pedido:
        return pedido
    try:
        import torch
        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def bench_modelo(nombre: str, fuente: Fuente, args, torch) -> dict:
    from ultralytics import YOLO

    t0 = time.perf_counter()
    modelo = YOLO(nombre)
    carga_ms = (time.perf_counter() - t0) * 1000.0

    def inferir(frames: list[np.ndarray]):
        if args.track:
            return [modelo.track(f, imgsz=args.resolucion, conf=args.conf,
                                 verbose=False, persist=True,
                                 device=args.dispositivo_pedido or None)
                    for f in frames]
        return modelo.predict(frames, imgsz=args.resolucion, conf=args.conf,
                              verbose=False, device=args.dispositivo,
                              batch=len(frames))

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    for _ in range(args.calentamiento):
        inferir(fuente.lote(args.lote))
    _sincronizar(torch)

    latencias: list[float] = []
    frames_procesados = 0
    for _ in range(args.iteraciones):
        frames = fuente.lote(args.lote)
        _sincronizar(torch)
        inicio = time.perf_counter()
        inferir(frames)
        _sincronizar(torch)
        latencias.append((time.perf_counter() - inicio) * 1000.0)
        frames_procesados += len(frames)

    total_s = sum(latencias) / 1000.0
    fps = frames_procesados / total_s if total_s > 0 else 0.0
    por_frame = [l / args.lote for l in latencias]
    memoria = {}
    if torch.cuda.is_available():
        memoria = {
            "vram_max_asignada_mb": round(
                torch.cuda.max_memory_allocated() / (1024 * 1024), 1),
            "vram_max_reservada_mb": round(
                torch.cuda.max_memory_reserved() / (1024 * 1024), 1),
        }
    return {
        "modelo": nombre,
        "carga_modelo_ms": round(carga_ms, 1),
        "lote": args.lote,
        "resolucion": args.resolucion,
        "track": bool(args.track),
        "latencias_lote_ms": {
            "p50": round(percentil(latencias, 50), 2),
            "p95": round(percentil(latencias, 95), 2),
            "media": round(statistics.fmean(latencias), 2),
            "min": round(min(latencias), 2),
            "max": round(max(latencias), 2),
        },
        "latencias_frame_ms": {
            "p50": round(percentil(por_frame, 50), 2),
            "p95": round(percentil(por_frame, 95), 2),
        },
        "fps": round(fps, 1),
        "camaras": estimar_camaras(fps, args.margen),
        "vram": memoria,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modelos", default=",".join(MODELOS_POR_DEFECTO),
                        help="lista separada por comas")
    parser.add_argument("--fuente", default="sintetico",
                        help="'sintetico' o la ruta de un vídeo")
    parser.add_argument("--resolucion", type=int, default=640,
                        help="imgsz de YOLO (múltiplo de 32; 640 por defecto)")
    parser.add_argument("--lote", type=int, default=1)
    parser.add_argument("--iteraciones", type=int, default=60)
    parser.add_argument("--calentamiento", type=int, default=10)
    parser.add_argument("--conf", type=float, default=0.45)
    parser.add_argument("--dispositivo", default=None,
                        help="0/cuda:0/cpu; por defecto GPU si existe")
    parser.add_argument("--track", action="store_true",
                        help="usar model.track (ByteTrack) como el servicio")
    parser.add_argument("--margen", type=float, default=0.8,
                        help="fracción de FPS reservada al resto del pipeline")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)

    if args.lote < 1 or args.iteraciones < 1 or args.calentamiento < 0:
        parser.error("lote/iteraciones deben ser >= 1 y calentamiento >= 0")
    args.dispositivo = _dispositivo(args.dispositivo)
    # `device=None` no es válido en track; se pasa solo si el usuario lo pidió.
    args.dispositivo_pedido = None if args.dispositivo == "cpu" else args.dispositivo

    import torch

    fuente = Fuente(args.fuente)
    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    resultados = []
    errores = []
    try:
        for nombre in modelos:
            print(f"[bench] {nombre}  fuente={args.fuente}  imgsz={args.resolucion} "
                  f"lote={args.lote}  device={args.dispositivo}", file=sys.stderr)
            try:
                resultados.append(bench_modelo(nombre, fuente, args, torch))
            except Exception as e:  # un modelo que falla no oculta los demás
                errores.append({"modelo": nombre, "error": str(e)})
                print(f"[bench] error con {nombre}: {e}", file=sys.stderr)
    finally:
        fuente.cerrar()

    salida = {
        "fecha": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "fuente": args.fuente,
        "ancho_frame": fuente.ancho,
        "alto_frame": fuente.alto,
        "dispositivo": args.dispositivo,
        "torch": torch.__version__,
        "cuda_disponible": bool(torch.cuda.is_available()),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "iteraciones": args.iteraciones,
        "calentamiento": args.calentamiento,
        "resultados": resultados,
        "errores": errores,
    }
    texto = json.dumps(salida, ensure_ascii=False, indent=2)
    print(texto)
    if args.json_path:
        destino = Path(args.json_path)
        if not destino.is_absolute():
            destino = RAIZ / destino
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto + "\n", encoding="utf-8")

    for r in resultados:
        print(f"{r['modelo']:>16}  fps={r['fps']:7.1f}  "
              f"p50={r['latencias_frame_ms']['p50']:6.2f} ms  "
              f"p95={r['latencias_frame_ms']['p95']:6.2f} ms  "
              f"cámaras 10/15 fps = {r['camaras']['a_10_fps']}/"
              f"{r['camaras']['a_15_fps']}", file=sys.stderr)
    return 1 if errores and not resultados else 0


if __name__ == "__main__":
    raise SystemExit(main())
