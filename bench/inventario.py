"""Inventario del equipo para la investigación de GPUs.

No requiere que Vigia esté arrancado. Ejemplo:

    python bench/inventario.py
    python bench/inventario.py --json bench/salida/inventario.json

Recoge lo que condiciona los benchmarks: GPU/VRAM, driver, CUDA, PyTorch,
versiones de ultralytics/OpenCV y proveedores de ONNX Runtime si está.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _nvidia_smi() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        salida = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.used,memory.free,"
             "driver_version,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for linea in salida.strip().splitlines():
        partes = [p.strip() for p in linea.split(",")]
        if len(partes) < 8:
            continue
        gpus.append({
            "indice": int(partes[0]),
            "nombre": partes[1],
            "vram_total_mb": _num(partes[2]),
            "vram_usada_mb": _num(partes[3]),
            "vram_libre_mb": _num(partes[4]),
            "driver": partes[5],
            "temperatura_c": _num(partes[6]),
            "potencia_w": _num(partes[7]),
        })
    return gpus


def _num(texto: str):
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def _modulo(nombre: str) -> str | None:
    try:
        modulo = __import__(nombre)
        return getattr(modulo, "__version__", "?")
    except Exception:
        return None


def resumen() -> dict:
    datos: dict = {
        "fecha": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "equipo": platform.node(),
        "sistema": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": platform.processor() or platform.machine(),
        "gpus_nvidia_smi": _nvidia_smi(),
        "torch": None,
        "cuda": None,
        "gpu_torch": [],
        "versiones": {
            "ultralytics": _modulo("ultralytics"),
            "opencv": _modulo("cv2"),
            "numpy": _modulo("numpy"),
            "onnxruntime": _modulo("onnxruntime"),
        },
    }
    try:
        import torch
        datos["torch"] = torch.__version__
        datos["cuda"] = {
            "compilada": torch.version.cuda,
            "disponible": bool(torch.cuda.is_available()),
            "cudnn": torch.backends.cudnn.version() if torch.cuda.is_available() else None,
        }
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                datos["gpu_torch"].append({
                    "indice": i,
                    "nombre": props.name,
                    "vram_total_mb": round(props.total_memory / (1024 * 1024)),
                    "compute_capability": f"{props.major}.{props.minor}",
                    "multiprocesadores": props.multi_processor_count,
                })
    except Exception as e:
        datos["torch_error"] = str(e)

    try:
        import onnxruntime as ort
        datos["onnx_providers"] = ort.get_available_providers()
    except Exception:
        datos["onnx_providers"] = []

    return datos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="guardar el inventario en este fichero")
    args = parser.parse_args(argv)
    datos = resumen()
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    print(texto)
    if args.json:
        destino = Path(args.json)
        if not destino.is_absolute():
            destino = RAIZ / destino
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
