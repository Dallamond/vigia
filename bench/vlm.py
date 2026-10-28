"""Sondeo de viabilidad de un VLM local (Ollama o LM Studio).

Comprueba si hay un servidor levantado, elige un modelo (preferentemente uno
con pinta de visión) y le pide describir una imagen midiendo la latencia. No
instala nada ni descarga modelos.

    python bench/vlm.py
    python bench/vlm.py --imagen frame.jpg --modelo qwen2.5vl:7b
    python bench/vlm.py --json bench/salida/vlm.json

Si no hay servidor o modelo, termina con código 0 y `disponible: false`: eso
también es un resultado de viabilidad.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
OLLAMA = "http://127.0.0.1:11434"
LMSTUDIO = "http://127.0.0.1:1234"
PISTAS_VISION = ("llava", "vision", "vl", "minicpm-v", "gemma3", "moondream",
                 "qwen2.5-vl", "qwen2-vl", "pixtral", "internvl")


def _get(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, cuerpo: dict, timeout: float):
    datos = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(url, data=datos, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _imagen(path: str | None) -> tuple[bytes, str]:
    if path:
        ruta = Path(path)
        if not ruta.is_absolute():
            ruta = RAIZ / ruta
        return ruta.read_bytes(), ruta.suffix.lower().lstrip(".") or "jpeg"
    import cv2
    import numpy as np
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:, :] = (35, 28, 20)
    cv2.rectangle(img, (180, 90), (460, 430), (90, 120, 160), -1)
    cv2.circle(img, (320, 90), 55, (130, 160, 200), -1)
    cv2.putText(img, "Vigia", (20, 450), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (220, 220, 220), 2)
    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("no se pudo generar la imagen de prueba")
    return png.tobytes(), "png"


def _vision(nombre: str) -> bool:
    bajo = nombre.lower()
    return any(p in bajo for p in PISTAS_VISION)


def probar_ollama(base: str, modelo: str | None, img_b64: str,
                  prompt: str, timeout: float, tokens: int) -> dict:
    resultado = {"servidor": base, "disponible": False}
    try:
        etiquetas = _get(base + "/api/tags", timeout=3)
    except (urllib.error.URLError, OSError, ValueError) as e:
        resultado["error"] = f"no responde: {e}"
        return resultado
    modelos = [m.get("name", "") for m in etiquetas.get("models", [])]
    resultado["modelos"] = modelos
    resultado["disponible"] = True
    elegido = modelo or next((m for m in modelos if _vision(m)), None) or \
        (modelos[0] if modelos else None)
    resultado["modelo"] = elegido
    if not elegido:
        resultado["error"] = "servidor sin modelos instalados"
        return resultado
    cuerpo = {"model": elegido, "prompt": prompt, "images": [img_b64],
              "stream": False, "options": {"num_predict": tokens}}
    t0 = time.perf_counter()
    try:
        resp = _post(base + "/api/generate", cuerpo, timeout)
    except (urllib.error.URLError, OSError, ValueError) as e:
        resultado["error"] = str(e)
        return resultado
    resultado["latencia_s"] = round(time.perf_counter() - t0, 2)
    resultado["respuesta"] = (resp.get("response") or "").strip()[:800]
    if resp.get("eval_count"):
        segundos = (resp.get("eval_duration") or 0) / 1e9
        resultado["tokens_salida"] = resp["eval_count"]
        resultado["tokens_por_s"] = round(resp["eval_count"] / segundos, 1) \
            if segundos else None
    return resultado


def probar_lmstudio(base: str, modelo: str | None, img_b64: str,
                    prompt: str, timeout: float, tokens: int,
                    mime: str = "png") -> dict:
    resultado = {"servidor": base, "disponible": False}
    try:
        lista = _get(base + "/v1/models", timeout=3)
    except (urllib.error.URLError, OSError, ValueError) as e:
        resultado["error"] = f"no responde: {e}"
        return resultado
    modelos = [m.get("id", "") for m in lista.get("data", [])]
    resultado["modelos"] = modelos
    resultado["disponible"] = True
    elegido = modelo or next((m for m in modelos if _vision(m)), None) or \
        (modelos[0] if modelos else None)
    resultado["modelo"] = elegido
    if not elegido:
        resultado["error"] = "servidor sin modelos cargados"
        return resultado
    cuerpo = {
        "model": elegido,
        "max_tokens": tokens,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/{mime};base64,{img_b64}"}},
            ],
        }],
    }
    t0 = time.perf_counter()
    try:
        resp = _post(base + "/v1/chat/completions", cuerpo, timeout)
    except (urllib.error.URLError, OSError, ValueError) as e:
        resultado["error"] = str(e)
        return resultado
    resultado["latencia_s"] = round(time.perf_counter() - t0, 2)
    try:
        resultado["respuesta"] = (
            resp["choices"][0]["message"]["content"] or "").strip()[:800]
    except (KeyError, IndexError, TypeError):
        resultado["respuesta"] = ""
    uso = resp.get("usage") or {}
    if uso:
        resultado["tokens_entrada"] = uso.get("prompt_tokens")
        resultado["tokens_salida"] = uso.get("completion_tokens")
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama", default=OLLAMA)
    parser.add_argument("--lmstudio", default=LMSTUDIO)
    parser.add_argument("--modelo", default=None,
                        help="nombre exacto; si falta se busca uno de visión")
    parser.add_argument("--imagen", default=None,
                        help="frame a describir; si falta se genera uno")
    parser.add_argument("--prompt",
                        default="Describe en una frase qué se ve y si hay personas.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--tokens", type=int, default=80)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args(argv)

    img, mime = _imagen(args.imagen)
    img_b64 = base64.b64encode(img).decode("ascii")
    salida = {
        "fecha": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "imagen_bytes": len(img),
        "prompt": args.prompt,
        "ollama": probar_ollama(args.ollama, args.modelo, img_b64,
                                args.prompt, args.timeout, args.tokens),
        "lmstudio": probar_lmstudio(args.lmstudio, args.modelo, img_b64,
                                    args.prompt, args.timeout, args.tokens,
                                    mime),
    }
    texto = json.dumps(salida, ensure_ascii=False, indent=2)
    print(texto)
    if args.json_path:
        destino = Path(args.json_path)
        if not destino.is_absolute():
            destino = RAIZ / destino
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
