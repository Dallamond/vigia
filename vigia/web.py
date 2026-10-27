"""Dashboard web local de Vigia — fases 1-5 integradas.

Arranque: .venv\\Scripts\\python.exe -m vigia.web   →  http://localhost:8000

- /            dashboard: video + estado + feed + historial + zonas
- /video.mjpg  stream MJPEG con detecciones, IDs de personas y zonas
- /estado      JSON: FPS, personas, objetos, estado de camara
- /eventos     JSON: feed reciente en memoria
- /historial   JSON: eventos de SQLite, filtrables (?tipo=, ?desde=, ?limite=)
- /clip/{fichero}  estilo: sirve un mp4 guardado por el sistema
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from .almacen import Almacen
from .captura import Camara
from .clips import GuardadorClips
from .config import cargar_config
from .detector import Deteccion, Detector
from .eventos import Evento, MotorEventos
from .notificaciones import notificar
from .tracking import RastreadorPersonas

log = logging.getLogger("vigia.web")

_VERDE = (0, 200, 0)
_NARANJA = (0, 160, 255)
_BLANCO = (255, 255, 255)
_ROJO = (60, 60, 230)

# tipios de evento que justifican clip + notificacion
_EVENTOS_GRANDES = {"persona_entra", "objeto_desaparece", "permanencia"}


class Estado:
    """Estado compartido entre el loop de captura y las rutas web."""

    def __init__(self) -> None:
        self.cerradura = threading.Lock()
        self.frame_jpeg: bytes | None = None
        self.fps = 0.0
        self.detecciones: list[Deteccion] = []
        self.ids_persona = set[int]()
        self.camara_ok = False
        self.eventos: deque = deque(maxlen=50)  # (hora, tipo, etiqueta)

    def actualizar(self, jpeg: bytes | None, fps: float, dets: list[Deteccion],
                   ids_persona: set[int], camara_ok: bool) -> None:
        with self.cerradura:
            if jpeg is not None:
                self.frame_jpeg = jpeg
            self.fps = fps
            self.detecciones = dets
            self.ids_persona = ids_persona
            self.camara_ok = camara_ok

    def anadir_evento(self, tipo: str, etiqueta: str | None = None) -> None:
        hora = time.strftime("%H:%M:%S")
        with self.cerradura:
            self.eventos.appendleft((hora, tipo, etiqueta or ""))

    def anadir_eventos(self, eventos: tuple) -> None:
        if not eventos:
            return
        hora = time.strftime("%H:%M:%S")
        with self.cerradura:
            for tipo, pid in eventos:
                self.eventos.appendleft((hora, tipo, f"id {pid}"))

    def snapshot(self) -> tuple[bytes | None, float, list[Deteccion],
                                set[int], bool]:
        with self.cerradura:
            return (self.frame_jpeg, self.fps, list(self.detecciones),
                    set(self.ids_persona), self.camara_ok)


def _jpeg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else b""


def dibujar(frame, detecciones, ids_por_indice: dict[int, int],
            fps: float, ids_persona: set[int], zonas: dict) -> None:
    for nombre, caja in zonas.items():
        if all(v == 0 for v in caja):
            continue
        x1, y1, x2, y2 = caja
        cv2.rectangle(frame, (x1, y1), (x2, y2), (150, 80, 200), 1)
        cv2.putText(frame, nombre, (x1 + 3, y1 + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 80, 200), 1, cv2.LINE_AA)
    for i, det in enumerate(detecciones):
        x1, y1, x2, y2 = det.caja
        if det.es_persona:
            pid = ids_por_indice.get(i)
            color = _VERDE
            etiqueta = f"person #{pid}" if pid else "person"
        else:
            color, etiqueta = _NARANJA, det.clase
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, etiqueta, (x1, max(y1 - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    texto = (f"FPS {fps:5.1f} | personas ahora {len(ids_persona)} | "
             f"objetos {sum(1 for d in detecciones if not d.es_persona)}")
    cv2.putText(frame, texto, (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _BLANCO, 1, cv2.LINE_AA)


def bucle_procesamiento(estado: Estado, almacen: Almacen | None = None,
                        config: dict | None = None,
                        clips: GuardadorClips | None = None,
                        cam=None, detector=None, motor: MotorEventos | None = None,
                        parar: threading.Event | None = None) -> None:
    """Hilo: webcam -> YOLO+track -> motor de eventos -> dibujar -> compartir.

    cam/detector/motor/clips/parar inyectables para tests.
    """
    if cam is None:
        cam = Camara(abrir=False)
    if detector is None:
        detector = Detector(conf=0.45)
    if almacen is None:
        almacen = _almacen
    if config is None:
        config = _config
    if motor is None:
        motor = MotorEventos(almacen, config=config)
    rastreador = RastreadorPersonas(
        timeout_seg=float(config.get("timeout_persona_seg", 10)))
    zonas = motor.zonas
    fps_hist: deque = deque(maxlen=30)
    frame_nulo = np.zeros((360, 640, 3), np.uint8)

    while not (parar and parar.is_set()):
        t0 = time.perf_counter()
        try:
            ok, frame = cam.leer()
            if not ok:
                if hasattr(cam, "abrir"):
                    cam.abrir()  # reintento: puede haberse conectado ahora
                frame = frame_nulo.copy()
                cv2.putText(frame, "CAMARA CAIDA - conectala o revisa permisos",
                            (30, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            _ROJO, 2, cv2.LINE_AA)
                estado.actualizar(_jpeg(frame), estado.fps, [], set(), False)
                time.sleep(2.0)
                continue

            dets, ids_idx = detector.detectar_track(frame)
        except Exception as e:
            log.exception("error en el bucle (se sigue intentando): %s", e)
            estado.actualizar(None, estado.fps, [], set(), False)
            time.sleep(0.5)
            continue

        try:
            ids_persona = {ids_idx[i] for i, d in enumerate(dets)
                           if d.es_persona and i in ids_idx}
            for tipo, pid in rastreador.actualizar(ids_persona):
                almacen.guardar(tipo, id_tracking=pid)
                estado.anadir_evento(tipo, f"id {pid}")
            mapa_ids = {ids_idx[i]: d.caja for i, d in enumerate(dets)
                        if d.es_persona and i in ids_idx}
            extras = [d for d in dets if not d.es_persona]
            for ev in motor.procesar(dets, mapa_ids, extras):
                estado.anadir_evento(ev.tipo, ev.detalle or "")
                if ev.tipo in _EVENTOS_GRANDES:
                    if config.get("guardar_clips", True) and clips:
                        clips.guardar(ev.tipo)
                    if config.get("notificar", True):
                        notificar(f"Vigia: {ev.tipo}", ev.detalle or "")

            dt = time.perf_counter() - t0
            fps_hist.append(1.0 / dt if dt > 0 else 0.0)
            fps = sum(fps_hist) / len(fps_hist) if fps_hist else 0.0
            dibujar(frame, dets, ids_idx, fps, rastreador.ids_dentro(), zonas)
            if clips:
                clips.meter(frame)
            estado.actualizar(_jpeg(frame), fps, dets,
                              rastreador.ids_dentro(), True)
        except Exception as e:
            log.exception("error en el bucle (se sigue intentando): %s", e)
            estado.actualizar(None, estado.fps, [], set(), False)
        time.sleep(0.01)


app = FastAPI(title="Vigia")
estado = Estado()
_config = cargar_config()
_almacen = Almacen(_config.get("almacen", "vigia.db"))
_clips = GuardadorClips(fps_salida=10.0) if _config.get("guardar_clips", True) else None

_PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Vigia</title>
<style>
  body{font-family:system-ui;background:#111;color:#eee;margin:0;
       display:flex;flex-direction:column;align-items:center}
  h1{margin:12px 0 4px;font-weight:600;font-size:1.4rem}
  img{max-width:96vw;border:1px solid #333;border-radius:8px;margin-top:8px}
  #estado{margin:8px;font-size:.95rem;color:#9cf}
  .panel{width:640px;max-width:96vw;margin:8px 0;font-size:.9rem}
  #feed div,#hist div{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:6px;
            padding:6px 10px;margin-top:4px}
  .entra{color:#8f8} .salio{color:#f88}
  span.badge{background:#222;border:1px solid #444;border-radius:999px;
             padding:4px 12px;margin:0 4px;display:inline-block}
  select,input{background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:4px}
</style></head>
<body>
<h1>🛡 Vigia</h1>
<img src="/video.mjpg" alt="stream">
<div id="estado">cargando…</div>

<div class="panel" id="feed"></div>

<div class="panel">
  <b>Historial (SQLite)</b> &
  <select id="ftipo"><option value="">todos</option>
    <option>persona_entra</option><option>persona_salio</option>
    <option>permanencia</option><option>objeto_aparece</option>
    <option>objeto_desaparece</option></select>
  desde: <input id="fdesde" placeholder="2026-10-27" size="12">
  <div id="hist"></div>
</div>
<script>
async function tick(){
  try{
    const [er,ev] = await Promise.all([fetch('/estado'), fetch('/eventos')]);
    const s = await er.json(); const l = await ev.json();
    estado.innerHTML = `<span class="badge">camara ${s.camara_ok ? '✅' : '❌'}</span>
      <span class="badge">FPS ${s.fps.toFixed(1)}</span>
      <span class="badge">personas ahora ${s.personas}</span>
      <span class="badge">objetos ${s.objetos}</span>`;
    feed.innerHTML = '';
    for(const e of l){
      const d = document.createElement('div');
      d.className = e.tipo.includes('entra') || e.tipo.includes('aparece') ? 'entra' : 'salio';
      d.textContent = `${e.hora} — ${e.tipo} ${e.etiqueta}`;
      feed.appendChild(d);
    }
    const ftipo = document.getElementById('ftipo').value;
    const desde = document.getElementById('fdesde').value.trim();
    const r = await fetch('/historial?limite=15' + (ftipo?`&tipo=${ftipo}`:'') + (desde?`&desde=${desde}`:''));
    const h = await r.json();
    hist.innerHTML = h.map(e=>`<div>${e.marca_sistema} — ${e.tipo}${e.detalle?' ('+e.detalle+')':''}${e.id_tracking!=null?' id '+e.id_tracking:''}</div>`).join('');
  }catch(e){}
}
setInterval(tick, 700); tick();
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
        return [{"hora": h, "tipo": t, "etiqueta": e}
                for h, t, e in estado.eventos]


@app.get("/historial")
def historial_json(tipo: str | None = None, desde: str | None = None,
                   limite: int = 100) -> list[dict]:
    if limite > 500:
        limite = 500
    return _almacen.historial(limite=limite, tipo=tipo, desde=desde)


@app.get("/clip/{fichero}")
def clip(fichero: str):
    if "/" in fichero or "\\" in fichero or ".." in fichero:
        raise HTTPException(400, "fichero inválido")
    ruta = _clips.carpeta / fichero if _clips else None
    if ruta is None or not ruta.exists():
        raise HTTPException(404, "clip no encontrado")
    def gen():
        yield ruta.read_bytes()
    return StreamingResponse(gen(), media_type="video/mp4")


def main() -> None:
    import uvicorn
    hilo = threading.Thread(
        target=bucle_procesamiento,
        args=(estado, _almacen, _config, _clips), daemon=True)
    hilo.start()
    log.info("Dashboard en http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
