"""Dashboard web local de Vigia — fases 1-5 integradas.

Arranque: .venv\\Scripts\\python.exe -m vigia.web   →  http://localhost:8000

- /            dashboard: video + estado + feed + historial + zonas
- /video.mjpg  stream MJPEG con detecciones, IDs de personas y zonas
- /estado      JSON: FPS, personas, objetos, estado de camara
- /eventos     JSON: feed reciente en memoria
- /historial   JSON: eventos de SQLite, filtrables (?tipo=, ?desde=, ?limite=)
- /clip/{fichero}  sirve un mp4 guardado por el sistema
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .almacen import Almacen
from .captura import Camara
from .clips import GuardadorClips
from .config import cargar_config
from .detector import Deteccion, Detector
from .dibujo import dibujar, frame_aviso
from .eventos import MotorEventos
from .notificaciones import notificar
from .tracking import RastreadorPersonas

log = logging.getLogger("vigia.web")

_ESPERA_OK = 0.01
_ESPERA_CAIDA = 2.0
_ESPERA_ERROR = 0.5

# tipos de evento que justifican clip + notificacion
_EVENTOS_GRANDES = {"persona_entra", "objeto_desaparece", "permanencia"}


class Estado:
    """Estado compartido entre el loop de captura y las rutas web."""

    def __init__(self) -> None:
        self.cerradura = threading.Lock()
        # condicion para que los clientes de MJPEG esperen frames nuevos
        # (un solo productor, N consumidores sin polling agresivo)
        self.condicion = threading.Condition(self.cerradura)
        self.version = 0
        self.frame_jpeg: bytes | None = None
        self.fps = 0.0
        self.detecciones: list[Deteccion] = []
        self.ids_persona: set[int] = set()
        self.camara_ok = False
        self.eventos: deque = deque(maxlen=50)  # (hora, tipo, etiqueta)

    def actualizar(self, jpeg: bytes | None, fps: float, dets: list[Deteccion],
                   ids_persona: set[int], camara_ok: bool) -> None:
        with self.condicion:
            if jpeg is not None:
                self.frame_jpeg = jpeg
            self.fps = fps
            self.detecciones = dets
            self.ids_persona = ids_persona
            self.camara_ok = camara_ok
            self.version += 1
            self.condicion.notify_all()

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


def bucle_procesamiento(estado: Estado, almacen: Almacen | None = None,
                        config: dict | None = None,
                        clips: GuardadorClips | None = None,
                        cam=None, detector=None, motor: MotorEventos | None = None,
                        rastreador: RastreadorPersonas | None = None,
                        parar: threading.Event | None = None) -> None:
    """Hilo: webcam -> YOLO+track -> motor de eventos -> dibujar -> compartir.

    cam/detector/motor/clips/parar inyectables para tests.
    """
    if cam is None:
        cam = Camara(abrir=False)
    ancho = getattr(cam, "ancho", 640)
    alto = getattr(cam, "alto", 480)
    if detector is None:
        try:
            detector = Detector(conf=0.45)
        except Exception:
            # sin modelo no hay nada que hacer: mejor avisar en el dashboard
            # que dejar el hilo muerto con la pantalla en negro
            log.exception("No se pudo cargar el modelo YOLO (¿falta "
                          "yolo11n.pt o internet en el primer arranque?)")
            estado.actualizar(
                _jpeg(frame_aviso("MODELO YOLO NO DISPONIBLE - revisa el log",
                                  ancho, alto)),
                0.0, [], set(), False)
            return
    if almacen is None:
        almacen = _almacen
    if config is None:
        config = _config
    if motor is None:
        motor = _motor_compartido
    if rastreador is None:
        rastreador = RastreadorPersonas(
            timeout_seg=float(config.get("timeout_persona_seg", 10)))
    zonas = motor.zonas
    fps_hist: deque[float] = deque(maxlen=30)
    fps_ultimo = 0.0

    while not (parar and parar.is_set()):
        t0 = time.perf_counter()
        try:
            ok, frame = cam.leer()
            if not ok:
                estado.actualizar(
                    _jpeg(frame_aviso(
                        "CAMARA CAIDA - conectala o revisa permisos",
                        ancho, alto)),
                    fps_ultimo, [], set(), False)
                espera = _ESPERA_CAIDA
            else:
                dets, ids_idx = detector.detectar_track(frame)
                ids_persona = {ids_idx[i] for i, d in enumerate(dets)
                               if d.es_persona and i in ids_idx}
                ids_dentro = rastreador.ids_dentro()
                for tipo, pid in rastreador.actualizar(ids_persona):
                    # coherencia: entra/sale tambien respeta las franjas
                    if motor.fuera_de_franja():
                        continue
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
                fps_ultimo = sum(fps_hist) / len(fps_hist)
                ids_dentro = rastreador.ids_dentro()
                dibujar(frame, dets, fps_ultimo, pie=None,
                        ids_por_indice=ids_idx, ids_persona=ids_dentro,
                        zonas=zonas)
                if clips:
                    clips.meter(frame)
                estado.actualizar(_jpeg(frame), fps_ultimo, dets,
                                  ids_dentro, True)
                espera = _ESPERA_OK
        except Exception:
            # el hilo no debe morir: se avisa en el dashboard y se reintenta
            log.exception("Error en el bucle de captura; se reintenta")
            estado.actualizar(
                _jpeg(frame_aviso("ERROR EN EL MOTOR - revisa el log",
                                  ancho, alto)),
                fps_ultimo, [], set(), False)
            espera = _ESPERA_ERROR

        if parar is not None:
            parar.wait(espera)
        else:
            time.sleep(espera)


app = FastAPI(title="Vigia")
estado = Estado()
_config = cargar_config()
_almacen = Almacen(_config.get("almacen", "vigia.db"))
_clips = (GuardadorClips(fps_salida=10.0)
          if _config.get("guardar_clips", True) else None)
# compartidos para poder aplicar los ajustes del dashboard en caliente
_motor_compartido = MotorEventos(_almacen, config=_config)
_rastreador_compartido = RastreadorPersonas(
    timeout_seg=float(_config.get("timeout_persona_seg", 10)))

_PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Vigia</title>
<style>
  body{font-family:system-ui;background:#111;color:#eee;margin:0}
  header{display:flex;align-items:center;gap:10px;padding:10px 16px}
  header h1{margin:0;font-weight:600;font-size:1.35rem}
  header .btn{margin-left:auto;background:#2a2a2a;border:1px solid #444;
    color:#eee;border-radius:8px;padding:6px 14px;cursor:pointer}
  header .btn:hover{background:#3a3a3a}
  .layout{display:grid;grid-template-columns:minmax(0,62%) minmax(280px,38%);
          gap:12px;padding:0 16px}
  .video-wrap img{width:100%;border:1px solid #333;border-radius:8px;display:block}
  #estado{margin:8px 0;font-size:.95rem;color:#9cf}
  .col .sec{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;
            padding:8px;margin-bottom:10px;font-size:.9rem;max-height:340px;
            overflow-y:auto}
  #feed div,#hist div{background:#141414;border:1px solid #2c2c2c;border-radius:6px;
            padding:6px 10px;margin-top:4px}
  .entra{color:#8f8} .salio{color:#f88}
  span.badge{background:#222;border:1px solid #444;border-radius:999px;
             padding:4px 12px;margin:0 4px;display:inline-block}
  select,input,textarea{background:#222;color:#eee;border:1px solid #444;
             border-radius:6px;padding:4px;font-size:.9rem}
  .sec a{color:#9cf}
  #abajo{margin:12px 16px 20px;background:#151515;border:1px dashed #333;
         border-radius:8px;padding:12px;min-height:110px}
  #abajo h3{margin:0 0 6px;font-size:1rem}
  #modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;
         align-items:center;justify-content:center;z-index:10}
  #modal .caja{background:#1d1d1d;border:1px solid #444;border-radius:10px;
         padding:16px;width:520px;max-width:94vw;max-height:88vh;overflow-y:auto}
  #modal label{display:block;margin:10px 0 4px;font-size:.85rem;color:#bbb}
  #modal .fila{display:flex;gap:8px;align-items:center;margin-bottom:4px}
  .ok{color:#8f8} .err{color:#f88}
</style></head>
<body>
<header>
  <h1>🛡 Vigia</h1>
  <button class="btn" onclick="abrirAjustes()">⚙ Ajustes</button>
</header>

<div class="layout">
  <div class="video-wrap">
    <img src="/video.mjpg" alt="stream">
    <div id="estado">cargando…</div>
  </div>
  <div class="col">
    <div class="sec" id="feed"></div>
    <div class="sec">
      <b>Historial (SQLite)</b><br>
      <select id="ftipo"><option value="">todos</option>
        <option>persona_entra</option><option>persona_salio</option>
        <option>permanencia</option><option>actividad</option>
        <option>objeto_aparece</option><option>objeto_desaparece</option></select>
      desde: <input id="fdesde" placeholder="2026-10-27" size="10">
      <div id="hist"></div>
    </div>
    <div class="sec" id="clipssec"><b>Clips</b><div id="clips">—</div></div>
  </div>
</div>

<div id="abajo">
  <h3>📊 Actividades y métricas (panel en construcción)</h3>
  <div id="actividades">cargando…</div>
  <div class="badge">Espacio reservado: pose, métricas y futura base de escalada</div>
</div>

<div id="modal" onclick="if(event.target===this)cerrarAjustes()">
  <div class="caja">
    <h3 style="margin-top:0">⚙ Ajustes</h3>
    <label>Permanencia (seg antes de avisar)</label>
    <input id="a_permanencia" type="number" min="1" max="3600" size="6">
    <label>Persona sin verse = salida (seg)</label>
    <input id="a_timeout_persona" type="number" min="1" max="3600" size="6">
    <label>Objeto ausente = desaparece (seg)</label>
    <input id="a_timeout_objeto" type="number" min="1" max="3600" size="6">
    <label>Clases vigiladas (desaparición, separadas por comas)</label>
    <input id="a_vigiladas" size="40">
    <label>Zonas (JSON: {"puerta":[0,0,120,300]})</label>
    <textarea id="a_zonas" rows="4" style="width:100%"></textarea>
    <label>Franjas activas (JSON: [{"desde":"22:00","hasta":"08:00"}])</label>
    <textarea id="a_franjas" rows="3" style="width:100%"></textarea>
    <label><input id="a_clips" type="checkbox"> guardar clips</label>
    <label><input id="a_notificar" type="checkbox"> notificar (toast Windows)</label>
    <label><input id="a_actividades" type="checkbox"> detectar actividades</label><br>
    <button class="btn" onclick="guardarAjustes()">Guardar</button>
    <button class="btn" onclick="cerrarAjustes()">Cancelar</button>
    <span id="ajustes_msg"></span>
  </div>
</div>

<script>
function esc(t){ return encodeURIComponent(String(t ?? ''));
}
function escH(t){
  return String(t ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function abrirAjustes(){ document.getElementById('modal').style.display='flex'; cargarAjustes(); }
function cerrarAjustes(){ document.getElementById('modal').style.display='none'; }
async function cargarAjustes(){
  try{
    const c = await (await fetch('/config')).json();
    a_permanencia.value = c.permanencia_seg ?? 30;
    a_timeout_persona.value = c.timeout_persona_seg ?? 10;
    a_timeout_objeto.value = c.timeout_objeto_seg ?? 10;
    a_vigiladas.value = (c.vigilar_desaparicion||[]).join(', ');
    a_zonas.value = JSON.stringify(c.zonas || {});
    a_franjas.value = JSON.stringify(c.franjas || []);
    a_clips.checked = !!c.guardar_clips;
    a_notificar.checked = !!c.notificar;
    a_actividades.checked = !!c.actividades;
  }catch(e){}
}
async function guardarAjustes(){
  const msg = document.getElementById('ajustes_msg');
  try{
    const cuerpo = {
      permanencia_seg: +a_permanencia.value,
      timeout_persona_seg: +a_timeout_persona.value,
      timeout_objeto_seg: +a_timeout_objeto.value,
      vigilar_desaparicion: a_vigiladas.value.split(',').map(s=>s.trim()).filter(Boolean),
      zonas: JSON.parse(a_zonas.value || '{}'),
      franjas: JSON.parse(a_franjas.value || '[]'),
      guardar_clips: a_clips.checked,
      notificar: a_notificar.checked,
      actividades: a_actividades.checked
    };
    const r = await fetch('/config', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cuerpo)});
    const res = await r.json();
    msg.className = res.ok ? 'ok' : 'err';
    msg.textContent = res.ok ? '✅ aplicado y guardado' : '❌ no se guardó en yaml';
    if(res.ok) setTimeout(cerrarAjustes, 700);
  }catch(e){ msg.className='err'; msg.textContent='❌ ' + e.message; }
}
async function tick(){
  try{
    const [er,ev,ec] = await Promise.all([
      fetch('/estado'), fetch('/eventos'), fetch('/clips')]);
    const s = await er.json(); const l = await ev.json(); const cs = await ec.json();
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
    hist.innerHTML = h.map(e=>`<div>${escH(e.marca_sistema)} — ${escH(e.tipo)}${e.detalle?' ('+escH(e.detalle)+')':''}${e.id_tracking!=null?' id '+escH(e.id_tracking):''}</div>`).join('');
    clips.innerHTML = cs.length
      ? cs.slice(0,10).map(c=>`<div><a href="/clip/${escH(c.fichero)}" target="_blank">${escH(c.fichero)}</a> <small>(${Math.round(c.tamano/1024)} KB, ${escH(c.cuando)})</small></div>`).join('')
      : '<small>sin clips aún</small>';
    const ra = await fetch('/actividades'); const acts = await ra.json();
    actividades.innerHTML = Object.keys(acts).length
      ? Object.entries(acts).map(([k,v])=>`<span class="badge">persona #${k}: ${escH(v)}</span>`).join('')
      : '<small>sin personas ni actividades ahora</small>';
  }catch(e){}
}
setInterval(tick, 700); tick();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGINA


@app.get("/video.mjpg")
def video() -> StreamingResponse:
    def gen():
        vistos = -1
        while True:
            with estado.condicion:
                estado.condicion.wait_for(
                    lambda: estado.version != vistos, timeout=1.0)
                jpeg = estado.frame_jpeg
                vistos = estado.version
            if jpeg:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n"
                       + jpeg + b"\r\n")
    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/estado")
def estado_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    _, fps, dets, ids_persona, ok = estado.snapshot()
    return {"fps": fps, "camara_ok": ok, "personas": len(ids_persona),
            "objetos": sum(1 for d in dets if not d.es_persona)}


@app.get("/eventos")
def eventos_json(response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    with estado.cerradura:
        return [{"hora": h, "tipo": t, "etiqueta": e}
                for h, t, e in estado.eventos]


@app.get("/historial")
def historial_json(response: Response, tipo: str | None = None,
                   desde: str | None = None, limite: int = 100) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    limite = max(1, min(limite, 500))
    return _almacen.historial(limite=limite, tipo=tipo, desde=desde)


@app.get("/clip/{fichero}")
def clip(fichero: str):
    if _clips is None:
        raise HTTPException(404, "clips desactivados")
    carpeta = _clips.carpeta.resolve()
    ruta = (carpeta / fichero).resolve()
    if (ruta.parent != carpeta or ruta.suffix.lower() != ".mp4"
            or not ruta.is_file()):
        raise HTTPException(404, "clip no encontrado")
    return FileResponse(ruta, media_type="video/mp4")


@app.get("/clips")
def clips_json(response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    if _clips is None:
        return []
    return _clips.listar()


@app.get("/actividades")
def actividades_json(response: Response) -> dict:
    """Mapa id_tracking -> actividad actual (usando PC, durmiendo...)."""
    response.headers["Cache-Control"] = "no-store"
    return dict(_actividades_personas)


# estado de actividades por id de tracking: {pid: {"actividad":..., "desde":..., "anunciada":...}}
_actividades_personas: dict[int, dict] = {}


# -- ajustes ------------------------------------------------------------------
_AJUSTES_NUM = ("permanencia_seg", "timeout_objeto_seg", "timeout_persona_seg")
_AJUSTES_BOOL = ("guardar_clips", "notificar", "actividades")


@app.get("/config")
def config_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    c = dict(_config)
    c["zonas"] = {k: list(v) for k, v in (_motor_compartido.zonas or {}).items()}
    c["franjas"] = [{"desde": d, "hasta": h} for d, h in _motor_compartido.franjas]
    return c


@app.post("/config")
async def config_guardar(datos: dict) -> dict:
    """Aplica ajustes al sistema en caliente y los guarda en config.yaml."""
    try:
        for k in _AJUSTES_NUM:
            if k in datos:
                v = int(datos[k])
                if v < 1 or v > 3600:
                    raise ValueError(f"{k} fuera de rango")
        for k in _AJUSTES_BOOL:
            if k in datos:
                datos[k] = bool(datos[k])
        if "vigilar_desaparicion" in datos:
            if not isinstance(datos["vigilar_desaparicion"], list) or not all(
                    isinstance(x, str) for x in datos["vigilar_desaparicion"]):
                raise ValueError("vigilar_desaparicion debe ser lista de clases")
        zonas = datos.get("zonas")
        if zonas is not None:
            if not isinstance(zonas, dict):
                raise ValueError("zonas debe ser dict nombre -> caja")
            for nombre, caja in list(zonas.items()):
                caja = list(caja)
                if (len(caja) == 4 and all(
                        isinstance(x, int) and 0 <= x <= 4000 for x in caja)):
                    zonas[nombre] = caja
                    continue
                raise ValueError(f"zona '{nombre}' no es 4 enteros")
        franjas = datos.get("franjas")
        if franjas is not None:
            from datetime import datetime as _dt
            if not isinstance(franjas, list):
                raise ValueError("franjas debe ser una lista")
            for f in franjas:
                if not isinstance(f, dict):
                    raise ValueError("franja debe ser dict desde/hasta")
                _dt.strptime(str(f.get("desde", "")), "%H:%M")
                _dt.strptime(str(f.get("hasta", "")), "%H:%M")
    except ValueError as e:
        raise HTTPException(400, str(e))

    # aplicar en caliente
    if "permanencia_seg" in datos:
        _motor_compartido.permanencia_seg = float(datos["permanencia_seg"])
    if "timeout_objeto_seg" in datos:
        _motor_compartido.timeout_objeto_seg = float(datos["timeout_objeto_seg"])
    if "vigilar_desaparicion" in datos:
        _motor_compartido.vigilar = list(datos["vigilar_desaparicion"])
    if "zonas" in datos:
        _motor_compartido.zonas = {
            k: tuple(v) for k, v in datos["zonas"].items()}
    if "franjas" in datos:
        _motor_compartido.franjas = [
            (f["desde"], f["hasta"]) for f in datos["franjas"]]
    if "timeout_persona_seg" in datos:
        _rastreador_compartido.timeout_seg = float(datos["timeout_persona_seg"])

    # persistir en config.yaml
    import yaml
    _config.update({k: v for k, v in datos.items()
                    if k in _AJUSTES_NUM + _AJUSTES_BOOL
                    or k in ("vigilar_desaparicion", "zonas", "franjas")})
    try:
        yaml.safe_dump(_config, open("config.yaml", "w", encoding="utf-8"),
                       allow_unicode=True, sort_keys=False)
    except OSError:
        log.exception("no se pudo guardar config.yaml (ajustes en memoria igual)")
        return {"ok": False, "guardado": False}
    return {"ok": True, "guardado": True}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parar = threading.Event()
    hilo = threading.Thread(
        target=bucle_procesamiento,
        args=(estado, _almacen, _config, _clips),
        kwargs={"parar": parar, "motor": _motor_compartido,
                "rastreador": _rastreador_compartido}, daemon=True)
    hilo.start()
    log.info("Dashboard en http://localhost:8000")
    try:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    finally:
        # parar el bucle antes de cerrar la BD: si no, el hilo sigue
        # escribiendo sobre una conexion cerrada hasta que muere el proceso
        parar.set()
        hilo.join(5)
        if _clips is not None:
            _clips.esperar_trabajos(5)
        _almacen.cerrar()


if __name__ == "__main__":
    main()
