"""Dashboard web local de Vigia (Fase 4 minima + tracking Fase 2).

Arranque: .venv\\Scripts\\python.exe -m vigia.web   →  http://localhost:8000

- /            pagina del dashboard
- /video.mjpg  stream MJPEG con detecciones dibujadas
- /estado      JSON con FPS, personas, objetos, estado de la camara
- /eventos     JSON con el feed reciente (persona_entra/persona_salio)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from .captura import Camara
from .detector import Deteccion, Detector
from .tracking import RastreadorPersonas

log = logging.getLogger("vigia.web")

_VERDE = (0, 200, 0)
_NARANJA = (0, 160, 255)
_BLANCO = (255, 255, 255)


class Estado:
    """Estado compartido entre el loop de captura y las rutas web."""

    def __init__(self) -> None:
        self.cerradura = threading.Lock()
        self.frame_jpeg: bytes | None = None
        self.fps = 0.0
        self.detecciones: list[Deteccion] = []
        self.ids_persona = set[int]()
        self.camara_ok = False
        self.eventos: deque = deque(maxlen=50)  # (hora, tipo, id)

    def actualizar(self, jpeg: bytes | None, fps: float, dets: list[Deteccion],
                   ids_persona: set[int], camara_ok: bool) -> None:
        with self.cerradura:
            if jpeg is not None:
                self.frame_jpeg = jpeg
            self.fps = fps
            self.detecciones = dets
            self.ids_persona = ids_persona
            self.camara_ok = camara_ok

    def anadir_eventos(self, eventos: tuple[tuple[str, int], ...]) -> None:
        if not eventos:
            return
        hora = time.strftime("%H:%M:%S")
        with self.cerradura:
            for tipo, pid in eventos:
                self.eventos.appendleft((hora, tipo, pid))

    def snapshot(self) -> tuple[bytes | None, float, list[Deteccion],
                                set[int], bool]:
        with self.cerradura:
            return (self.frame_jpeg, self.fps, list(self.detecciones),
                    set(self.ids_persona), self.camara_ok)


def _jpeg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else b""


def dibujar(frame, detecciones, ids_por_indice: dict[int, int],
            fps: float, ids_persona: set[int]) -> None:
    for i, det in enumerate(detecciones):
        x1, y1, x2, y2 = det.caja
        if det.es_persona:
            pid = ids_por_indice.get(i)
            color, etiqueta = _VERDE, f"person #{pid}" if pid else "person"
        else:
            color, etiqueta = _NARANJA, det.clase
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, etiqueta, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    texto = (f"FPS {fps:5.1f} | personas ahora {len(ids_persona)} | "
             f"objetos {sum(1 for d in detecciones if not d.es_persona)}")
    cv2.putText(frame, texto, (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _BLANCO, 1, cv2.LINE_AA)


def bucle_procesamiento(estado: Estado, timeout_salida: float = 10.0) -> None:
    """Hilo: webcam -> YOLO+track -> dibujar -> jpeg/evento compartido."""
    cam = Camara(abrir=False)
    detector = Detector(conf=0.45)
    rastreador = RastreadorPersonas(timeout_seg=timeout_salida)
    fps_hist: deque = deque(maxlen=30)
    frame_nulo = np.zeros((360, 640, 3), np.uint8)

    while True:
        t0 = time.perf_counter()
        ok, frame = cam.leer()
        if not ok:
            cam.abrir()  # reintento: puede haberse conectado ahora
            frame = frame_nulo.copy()
            cv2.putText(frame, "CAMARA CAIDA - conectala o revisa permisos",
                        (30, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 0, 255), 2, cv2.LINE_AA)
            estado.actualizar(_jpeg(frame), estado.fps, [], set(), False)
            time.sleep(2.0)
            continue

        dets, ids_idx = detector.detectar_track(frame)
        ids_persona = {ids_idx[i] for i, d in enumerate(dets) if d.es_persona}
        eventos = rastreador.actualizar(ids_persona)
        estado.anadir_eventos(eventos)

        dt = time.perf_counter() - t0
        fps_hist.append(1.0 / dt if dt > 0 else 0.0)
        fps = sum(fps_hist) / len(fps_hist) if fps_hist else 0.0
        dibujar(frame, dets, ids_idx, fps, rastreador.ids_dentro())
        estado.actualizar(_jpeg(frame), fps, dets, rastreador.ids_dentro(), True)
        time.sleep(0.01)


app = FastAPI(title="Vigia")
estado = Estado()

_PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Vigia</title>
<style>
  body{font-family:system-ui;background:#111;color:#eee;margin:0;
       display:flex;flex-direction:column;align-items:center}
  h1{margin:12px 0 4px;font-weight:600}
  img{max-width:96vw;border:1px solid #333;border-radius:8px;margin-top:8px}
  #estado{margin:8px;font-size:.95rem;color:#9cf}
  #feed{width:640px;max-width:96vw;margin:8px 0 20px;font-size:.9rem}
  #feed div{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:6px;
            padding:6px 10px;margin-top:4px}
  .entra{color:#8f8} .salio{color:#f88}
  span.badge{background:#222;border:1px solid #444;border-radius:999px;
             padding:4px 12px;margin:0 4px;display:inline-block}
</style></head>
<body>
<h1>🛡 Vigia</h1>
<img src="/video.mjpg" alt="stream">
<div id="estado">cargando…</div>
<div id="feed"></div>
<script>
const feed = document.getElementById('feed');
const vistos = new Set();
async function tick(){
  try{
    const [er, ev] = await Promise.all([
      fetch('/estado'), fetch('/eventos')]);
    const s = await er.json(); const l = await ev.json();
    document.getElementById('estado').innerHTML =
      `<span class="badge">camara ${s.camara_ok ? '✅' : '❌'}</span>
       <span class="badge">FPS ${s.fps.toFixed(1)}</span>
       <span class="badge">personas ahora ${s.personas}</span>
       <span class="badge">objetos ${s.objetos}</span>`;
    for(const e of l.slice().reverse()){
      const k = e.hora + e.tipo + e.id;
      if(vistos.has(k)) continue;
      vistos.add(k);
      const d = document.createElement('div');
      d.className = e.tipo.includes('entra') ? 'entra' : 'salio';
      d.textContent = `${e.hora} — ${e.tipo} (id ${e.id})`;
      feed.prepend(d);
    }
  }catch(e){}
}
setInterval(tick, 500); tick();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGINA


@app.get("/video.mjpg")
def video():
    def gen():
        while True:
            jpeg, *_ = estado.snapshot()
            if jpeg:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpeg + b"\r\n")
            time.sleep(0.03)
    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/estado")
def estado_json() -> dict:
    _, fps, dets, ids_persona, ok = estado.snapshot()
    return {"fps": fps, "camara_ok": ok, "personas": len(ids_persona),
            "objetos": sum(1 for d in dets if not d.es_persona)}


@app.get("/eventos")
def eventos_json() -> list[dict]:
    with estado.cerradura:
        return [{"hora": h, "tipo": t, "id": i}
                for h, t, i in estado.eventos]


def main() -> None:
    import uvicorn
    hilo = threading.Thread(target=bucle_procesamiento, args=(estado,),
                            daemon=True)
    hilo.start()
    log.info("Dashboard en http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
