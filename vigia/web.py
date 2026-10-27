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
import re
import shutil
import threading
import time
from collections import deque
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .almacen import Almacen
from .captura import Camara
from .clips import GuardadorClips
from .config import cargar_config
from .actividades import EstabilizadorAct, clasificar as clasificar_actividad
from .caras import CarasDetector
from .detector import Deteccion, Detector
from .dibujo import dibujar, frame_aviso
from .eventos import MotorEventos
from .logs import configurar_logging
from .mantener import mantener
from .notificaciones import notificar
from .pose import PoseDetector, dibujar_pose
from .tracking import RastreadorPersonas

log = logging.getLogger("vigia.web")

VERSION = "0.7"

_ESPERA_OK = 0.01
_ESPERA_CAIDA = 2.0
_ESPERA_ERROR = 0.5

# tipos de evento que justifican un clip de video
_EVENTOS_GRANDES = {"persona_entra", "objeto_desaparece", "permanencia"}
# tipos que ademas lanzan un aviso de Windows (evita spam al pasar gente)
_AVISOS = {"permanencia", "objeto_desaparece"}

_EXT_MEDIA = {".mp4": "video/mp4", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".png": "image/png"}

# fichero donde se persisten los ajustes del dashboard (los tests lo cambian)
_RUTA_CONFIG = Path("config.yaml")

_INICIO = time.monotonic()

# cooldown para banner "cara nueva" (no spamear el feed)
_CARA_NUEVA_CADA_SEG = 120.0
_ULTIMA_CARA_NUEVA = {"t": 0.0}


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
        # resolucion real de captura (la rellena el bucle)
        self.ancho = 640
        self.alto = 480

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


def _iou(caja_a, caja_b) -> float:
    ax1, ay1, ax2, ay2 = caja_a
    bx1, by1, bx2, by2 = caja_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _persona_de_pose(caja, personas) -> int | None:
    """Empareja la caja de una pose con la persona trackeada (mismo ID visual).

    El modelo de pose usa su propio ByteTrack: sin este emparejamiento el #N
    de /actividades no seria el mismo #N que se dibuja en el video.
    """
    mejor, mejor_iou = None, 0.0
    for pid, caja_p in personas:
        iou = _iou(caja, caja_p)
        if iou > mejor_iou:
            mejor, mejor_iou = pid, iou
    if mejor is not None and mejor_iou >= 0.2:
        return mejor
    # respaldo: centro de la pose dentro de la caja de la persona
    cx = (caja[0] + caja[2]) / 2
    cy = (caja[1] + caja[3]) / 2
    for pid, caja_p in personas:
        if (caja_p[0] - 10 <= cx <= caja_p[2] + 10
                and caja_p[1] - 10 <= cy <= caja_p[3] + 10):
            return pid
    return None


def _anotar_evento(estado: Estado, almacen: Almacen, clips, config: dict,
                   frame, tipo: str, detalle: str | None = "",
                   id_tracking: int | None = None,
                   evento_id: int | None = None) -> int | None:
    """Unico punto de salida de un evento: persiste, guarda media y publica.

    - si el evento ya existe (motor), se reutiliza su `evento_id`
    - guarda una miniatura JPEG del frame actual
    - en los eventos grandes guarda tambien el clip de los ultimos segundos
    - lanza el aviso de Windows solo para los tipos de `_AVISOS`
    """
    if evento_id is None:
        evento_id = almacen.guardar(tipo, id_tracking=id_tracking,
                                    detalle=detalle)
    if clips is not None:
        miniatura = None
        if config.get("miniaturas", True):
            p = clips.guardar_miniatura(frame, tipo)
            miniatura = p.name if p else None
        clip = None
        if tipo in _EVENTOS_GRANDES and config.get("guardar_clips", True):
            p = clips.guardar(tipo)
            clip = p.name if p else None
        try:
            almacen.anotar_media(evento_id, miniatura=miniatura, clip=clip)
        except Exception:
            log.exception("no se pudo enlazar la media del evento %s", evento_id)
    if tipo in _AVISOS and config.get("notificar", True):
        notificar(f"Vigia: {tipo}", detalle or "")
    estado.anadir_evento(tipo, detalle or "")
    return evento_id


def bucle_procesamiento(estado: Estado, almacen: Almacen | None = None,
                        config: dict | None = None,
                        clips: GuardadorClips | None = None,
                        cam=None, detector=None, motor: MotorEventos | None = None,
                        rastreador: RastreadorPersonas | None = None,
                        parar: threading.Event | None = None) -> None:
    """Hilo: webcam -> YOLO+track -> motor de eventos -> dibujar -> compartir.

    cam/detector/motor/clips/parar inyectables para tests.
    """
    if config is None:
        config = _config
    if cam is None:
        cam = Camara(abrir=False, ancho=int(config.get("ancho", 640)),
                     alto=int(config.get("alto", 480)))
    ancho = getattr(cam, "ancho", 640)
    alto = getattr(cam, "alto", 480)
    estado.ancho, estado.alto = ancho, alto
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
    if motor is None:
        motor = _motor_compartido
    if rastreador is None:
        rastreador = RastreadorPersonas(
            timeout_seg=float(config.get("timeout_persona_seg", 10)))
    # caras (identidad): carga diferida; desactivacion permanente si falla
    caras_det: CarasDetector | None = None
    caras_fallo = False
    identidades: dict[int, tuple[str, float]] = {}
    pendiente: dict = {"emb": None, "cuando": 0.0, "imagen": None}
    fps_hist: deque[float] = deque(maxlen=30)
    fps_ultimo = 0.0
    ultimo_t = 0.0
    # la primera pasada de mantenimiento, tras el arranque
    proximo_mantenimiento = time.monotonic() + 60 * int(
        config.get("mantener_cada_min", 60) or 60)
    # carga diferida del modelo de pose (segunda descarga en el primer arranque:
    # si falla, seguimos sin pose y avisamos en el log una sola vez)
    pose_det: PoseDetector | None = None
    pose_fallo = False
    actividades: dict[int, EstabilizadorAct] = {}
    ultima_actividad: dict[int, float] = {}

    while not (parar and parar.is_set()):
        t0 = time.perf_counter()
        # FPS real del bucle completo (incluye dibujo, JPEG y espera)
        if ultimo_t:
            delta = t0 - ultimo_t
            if delta > 0:
                fps_hist.append(1.0 / delta)
                fps_ultimo = sum(fps_hist) / len(fps_hist)
        ultimo_t = t0
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
                # el buffer guarda el frame ANTES de anotar el evento: asi el
                # clip incluye el instante que lo dispara
                if clips is not None and config.get("guardar_clips", True):
                    clips.fps_salida = min(max(fps_ultimo or 10.0, 1.0), 30.0)
                    clips.meter(frame)
                ids_persona = {ids_idx[i] for i, d in enumerate(dets)
                               if d.es_persona and i in ids_idx}
                for tipo, pid in rastreador.actualizar(ids_persona):
                    # coherencia: entra/sale tambien respeta las franjas
                    if motor.fuera_de_franja():
                        continue
                    _anotar_evento(estado, almacen, clips, config, frame,
                                   tipo, f"id {pid}", id_tracking=pid)
                mapa_ids = {ids_idx[i]: d.caja for i, d in enumerate(dets)
                            if d.es_persona and i in ids_idx}
                extras = [d for d in dets if not d.es_persona]
                for ev in motor.procesar(dets, mapa_ids, extras):
                    _anotar_evento(estado, almacen, clips, config, frame,
                                   ev.tipo, ev.detalle, evento_id=ev.id)

                # actividades: segunda pasada de pose + clasificacion por persona
                # (se lee la config en cada frame: el interruptor del panel
                # funciona sin reiniciar)
                act_on = bool(config.get("actividades", False))
                if not act_on:
                    pose_fallo = False  # permitir reintento si se reactiva
                    if actividades:
                        actividades.clear()
                        ultima_actividad.clear()
                        with _act_lock:
                            _actividades_personas.clear()
                if act_on and pose_det is None and not pose_fallo:
                    try:
                        pose_det = PoseDetector()
                    except Exception:
                        pose_fallo = True  # no insistir (p.ej. sin red)
                        log.warning("modelo de pose no disponible; "
                                    "actividades desactivadas")
                if act_on and pose_det is not None:
                    try:
                        poses = pose_det.detectar(frame)
                        personas = [(pid, caja) for pid, caja in mapa_ids.items()]
                        ahora_act = time.time()
                        vistos = set()
                        for p in poses:
                            pid = _persona_de_pose(p.caja, personas)
                            if pid is None:
                                continue
                            act = clasificar_actividad(p, extras)
                            est = actividades.get(pid)
                            if est is None:
                                est = EstabilizadorAct()
                                actividades[pid] = est
                            ultima_actividad[pid] = ahora_act
                            anuncio = est.tick(act, ahora_act)
                            if anuncio and not motor.fuera_de_franja():
                                _anotar_evento(
                                    estado, almacen, clips, config, frame,
                                    "actividad", f"id {pid} {anuncio}",
                                    id_tracking=pid)
                            vistos.add(pid)
                        # gracia de 5 s: un frame sin pose no borra la actividad
                        for pid in list(actividades):
                            if pid not in vistos and (
                                    ahora_act - ultima_actividad.get(pid, 0)
                                    > 5.0):
                                del actividades[pid]
                                ultima_actividad.pop(pid, None)
                        dibujar_pose(frame, poses)
                        # publicar para /actividades (con cerrojo: lo lee la web)
                        with _act_lock:
                            _actividades_personas.clear()
                            for pid, est in actividades.items():
                                _actividades_personas[pid] = est.actual
                    except Exception:
                        log.exception("error en pose/actividades (se sigue)")

                # caras: identidad por persona (YuNet + SFace, local)
                caras_on = bool(config.get("caras", False))
                if not caras_on:
                    caras_fallo = False
                if caras_on and caras_det is None and not caras_fallo:
                    try:
                        caras_det = CarasDetector()
                        caras_det.set_conocidos({
                            r["nombre"]:
                                np.frombuffer(r["embedding"], dtype=np.float32)
                            for r in almacen.rostros()})
                        caras_det.recargar = False
                    except Exception:
                        caras_fallo = True
                        log.warning("caras desactivadas: modelos no disponibles")
                if caras_on and caras_det is not None:
                    try:
                        # la UI pudo cambiar el registro: recargar conocidos
                        if bucle_caras_recarga.is_set():
                            bucle_caras_recarga.clear()
                            caras_det.set_conocidos({
                                r["nombre"]:
                                    np.frombuffer(r["embedding"],
                                                  dtype=np.float32)
                                for r in almacen.rostros()})
                        for cara in caras_det.detectar(frame):
                            x1, y1, x2, y2 = cara["caja"]
                            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                            # cara -> persona: centro de la cara dentro de su caja
                            pid_asoc = None
                            for pid, caja in mapa_ids.items():
                                if (caja[0] - 10 <= cx <= caja[2] + 10
                                        and caja[1] - 10 <= cy <= caja[3] + 10):
                                    pid_asoc = pid
                                    break
                            if pid_asoc is None:
                                continue
                            nombre, sim = caras_det.identificar(cara["emb"])
                            identidades[pid_asoc] = (nombre, sim)
                            if nombre is not None:
                                cv2.putText(frame, f"{nombre} {sim:.0%}",
                                            (x1, y1 - 6),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                            (80, 220, 120), 2, cv2.LINE_AA)
                            elif cara["conf"] >= 0.7 and \
                                    time.time() - _ULTIMA_CARA_NUEVA["t"] \
                                    >= _CARA_NUEVA_CADA_SEG:
                                _ULTIMA_CARA_NUEVA["t"] = time.time()
                                _anotar_evento(
                                    estado, almacen, clips, config, frame,
                                    "cara_desconocida",
                                    f"id {pid_asoc} sin identificar",
                                    id_tracking=pid_asoc)
                    except Exception:
                        log.exception("error en caras (se sigue)")

                ids_dentro = rastreador.ids_dentro()
                # leer las zonas del motor en cada frame: el editor las cambia
                # en caliente y no deben quedarse congeladas hasta reiniciar
                dibujar(frame, dets, fps_ultimo, pie=None,
                        ids_por_indice=ids_idx, ids_persona=ids_dentro,
                        zonas=motor.zonas)
                estado.actualizar(_jpeg(frame), fps_ultimo, dets,
                                  ids_dentro, True)

                if time.monotonic() >= proximo_mantenimiento:
                    proximo_mantenimiento = time.monotonic() + 60 * int(
                        config.get("mantener_cada_min", 60) or 60)
                    try:
                        mantener(almacen, clips, config)
                    except Exception:
                        log.exception("fallo en el mantenimiento periodico")
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
# se crea siempre: el buffer solo se llena con guardar_clips=true, pero los
# endpoints /clips y /media siguen sirviendo lo que ya hay en disco y el
# interruptor del panel funciona en caliente
_clips = GuardadorClips(fps_salida=10.0)
# compartidos para poder aplicar los ajustes del dashboard en caliente
_motor_compartido = MotorEventos(_almacen, config=_config)
_rastreador_compartido = RastreadorPersonas(
    timeout_seg=float(_config.get("timeout_persona_seg", 10)))

_RUTA_PANEL = Path(__file__).parent / "static" / "index.html"
_PAGINA_MINIMA = ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
                  "<title>Vigia</title></head><body><h1>Vigia</h1>"
                  "<p>Falta <code>vigia/static/index.html</code>.</p>"
                  "</body></html>")


def _leer_panel() -> str:
    """Panel nuevo desde disco; si falta, una pagina minima que no rompe."""
    try:
        return _RUTA_PANEL.read_text(encoding="utf-8")
    except OSError:
        log.warning("no se encontro %s; se sirve el panel minimo", _RUTA_PANEL)
        return _PAGINA_MINIMA


_PAGINA = _leer_panel()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # se relee en cada visita: editar el panel no exige reiniciar el servidor
    return _leer_panel()


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
            "objetos": sum(1 for d in dets if not d.es_persona),
            "ancho": estado.ancho, "alto": estado.alto}


@app.get("/snapshot.jpg")
def snapshot() -> Response:
    """Ultimo frame como JPEG (fondo del editor de zonas)."""
    jpeg, *_ = estado.snapshot()
    if not jpeg:
        raise HTTPException(404, "todavia no hay imagen")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


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


@app.get("/media/{fichero}")
def media(fichero: str):
    """Sirve un clip (.mp4) o una miniatura (.jpg) de la carpeta de media."""
    if _clips is None:
        raise HTTPException(404, "media desactivada")
    carpeta = _clips.carpeta.resolve()
    ruta = (carpeta / fichero).resolve()
    tipo = _EXT_MEDIA.get(ruta.suffix.lower())
    if ruta.parent != carpeta or tipo is None or not ruta.is_file():
        raise HTTPException(404, "fichero no encontrado")
    return FileResponse(ruta, media_type=tipo)


@app.get("/timeline")
def timeline_json(response: Response, dia: str | None = None,
                  limite: int = 2000) -> dict:
    response.headers["Cache-Control"] = "no-store"
    dia = dia or date.today().isoformat()
    eventos = _almacen.eventos_del_dia(dia, limite=limite)
    return {"dia": dia, "total": len(eventos), "eventos": eventos}


@app.get("/salud")
def salud_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    resumen = (_clips.resumen_media() if _clips is not None
               else {"n": 0, "bytes": 0, "miniaturas": 0})
    raiz = _clips.carpeta if _clips is not None else Path(".")
    try:
        uso = shutil.disk_usage(str(raiz))
        libre_gb = uso.free / 1024 ** 3
        total_gb = uso.total / 1024 ** 3
    except OSError:
        libre_gb = total_gb = 0.0
    try:
        eventos = _almacen.contar()
        ultimo = _almacen.ultimo_evento()
        db_bytes = _almacen.tamano_bytes()
    except Exception:
        log.exception("no se pudo leer el estado de la BD")
        eventos, ultimo, db_bytes = 0, None, 0
    return {
        "version": VERSION,
        "uptime_seg": int(time.monotonic() - _INICIO),
        "camara_ok": estado.camara_ok,
        "fps": round(estado.fps, 1),
        "eventos": eventos,
        "ultimo_evento": ultimo,
        "db_bytes": db_bytes,
        "clips": {"n": resumen.get("n", 0),
                  "bytes": resumen.get("bytes", 0),
                  "miniaturas": resumen.get("miniaturas", 0)},
        "disco_libre_gb": round(libre_gb, 1),
        "disco_total_gb": round(total_gb, 1),
    }


@app.get("/zonas")
def zonas_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"zonas": {k: list(v) for k, v in _motor_compartido.zonas.items()},
            "ancho": estado.ancho, "alto": estado.alto}


@app.put("/zonas")
async def zonas_guardar(datos: dict) -> dict:
    """Zonas desde el editor visual: se aplican en caliente y se persisten."""
    try:
        return await config_guardar({"zonas": (datos or {}).get("zonas", {})})
    except HTTPException as e:
        return {"ok": False, "error": e.detail}


@app.get("/actividades")
def actividades_json(response: Response) -> dict:
    """Mapa id_tracking -> actividad actual (usando PC, durmiendo...)."""
    response.headers["Cache-Control"] = "no-store"
    with _act_lock:
        return dict(_actividades_personas)


# publicacion del hilo -> endpoint: {pid: actividad string}
_actividades_personas: dict[int, str] = {}
_act_lock = threading.Lock()


# -- registro de caras (fase 7) ------------------------------------------------
@app.get("/caras/registro")
def caras_registro(response: Response) -> list[dict]:
    """Personas conocidas: id y nombre (los embeddings no se exponen)."""
    response.headers["Cache-Control"] = "no-store"
    return [{"id": r["id"], "nombre": r["nombre"]}
            for r in _almacen.rostros()]


@app.post("/caras/registro")
async def caras_registrar(datos: dict) -> dict:
    """Registra o renombra a una persona conocida.

    {nombre, embedding: [float...]} -> registra nueva identidad
    {nombre, id}                    -> renombra la entrada id
    """
    datos = datos or {}
    nombre = str(datos.get("nombre", "")).strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre")
    if datos.get("id") is not None:
        id_entrada = int(datos["id"])
        existentes = {r["id"]: r for r in _almacen.rostros()}
        if id_entrada not in existentes:
            raise HTTPException(404, "entrada no encontrada")
        _almacen.guardar_rostro(
            nombre, bytes(existentes[id_entrada]["embedding"]))
        _almacen.borrar_rostro(id_entrada)
    elif datos.get("embedding") is not None:
        emb = np.asarray(datos["embedding"], dtype=np.float32)
        _almacen.guardar_rostro(nombre, emb.tobytes())
    else:
        raise HTTPException(400, "falta 'embedding' o 'id'")
    _marcar_recarga_caras()
    return {"ok": True}


@app.delete("/caras/registro/{id_entrada}")
def caras_borrar(id_entrada: int) -> dict:
    if not _almacen.borrar_rostro(id_entrada):
        raise HTTPException(404, "entrada no encontrada")
    _marcar_recarga_caras()
    return {"ok": True}


def _marcar_recarga_caras() -> None:
    """El bucle recarga el registro de conocidos en el siguiente frame."""
    bucle_caras_recarga.set()


# evento del bucle: recargar conocidos (se consulta con getattr si el hilo
# corrio antes de que existiera esta variable)
bucle_caras_recarga = threading.Event()


# -- ajustes ------------------------------------------------------------------
_AJUSTES_NUM = ("permanencia_seg", "timeout_objeto_seg", "timeout_persona_seg",
                "ancho", "alto", "retencion_dias", "retencion_clips_dias",
                "retencion_clips_mb", "mantener_cada_min")
_RANGOS = {
    "permanencia_seg": (1, 3600),
    "timeout_objeto_seg": (1, 3600),
    "timeout_persona_seg": (1, 3600),
    "ancho": (160, 3840),
    "alto": (120, 2160),
    "retencion_dias": (0, 3650),
    "retencion_clips_dias": (0, 3650),
    "retencion_clips_mb": (0, 1024 * 1024),
    "mantener_cada_min": (1, 1440),
}
_AJUSTES_BOOL = ("guardar_clips", "notificar", "actividades", "miniaturas",
                 "caras")
_AJUSTES_LISTA = ("vigilar_desaparicion", "zonas", "franjas")
# ancho/alto solo se leen al crear la camara: hace falta reiniciar
_REQUIEREN_REINICIO = ("ancho", "alto")


def _a_bool(valor, clave: str) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    if isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in ("1", "true", "si", "sí", "yes", "on"):
            return True
        if texto in ("0", "false", "no", "off", ""):
            return False
    raise ValueError(f"{clave} debe ser booleano")


@app.get("/config")
def config_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    c = dict(_config)
    c["zonas"] = {k: list(v) for k, v in (_motor_compartido.zonas or {}).items()}
    c["franjas"] = [{"desde": d, "hasta": h} for d, h in _motor_compartido.franjas]
    return c


def _linea_yaml(clave: str, valor) -> str:
    """Linea(s) YAML para una clave (sin romper comentarios del resto).

    Estilo bloque: un escalar ocupa una linea y una lista/mapa varias, para
    que el fichero siga siendo YAML valido al mezclarse con el resto.
    """
    import yaml
    texto = yaml.safe_dump({clave: valor}, allow_unicode=True,
                           default_flow_style=False, sort_keys=False,
                           indent=2).strip()
    return texto


def _persistir_config(cambios: dict) -> None:
    """Actualiza solo las claves cambiadas, conservando comentarios y orden."""
    ruta = Path(_RUTA_CONFIG)
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        texto = ("# Vigia - configuracion activa (editada desde el panel)\n"
                 "# Referencia completa y comentada: config.example.yaml\n")
    lineas = texto.splitlines()
    for clave, valor in cambios.items():
        nuevas = _linea_yaml(clave, valor).splitlines()
        patron = re.compile(rf"^{re.escape(clave)}\s*:")
        indice = next((i for i, linea in enumerate(lineas)
                       if patron.match(linea)), None)
        if indice is None:
            lineas.extend(nuevas)
            continue
        # el bloque de la clave llega hasta la siguiente linea no indentada
        fin = indice + 1
        while fin < len(lineas):
            linea = lineas[fin]
            if not linea.strip():
                break
            if linea[0] in " \t-":
                fin += 1
                continue
            break
        lineas[indice:fin] = nuevas
    ruta.write_text("\n".join(lineas).rstrip("\n") + "\n", encoding="utf-8")


@app.post("/config")
async def config_guardar(datos: dict) -> dict:
    """Aplica ajustes al sistema en caliente y los guarda en config.yaml."""
    datos = dict(datos or {})
    try:
        for k in _AJUSTES_NUM:
            if k not in datos:
                continue
            v = int(float(datos[k]))
            minimo, maximo = _RANGOS[k]
            if not minimo <= v <= maximo:
                raise ValueError(f"{k} fuera de rango ({minimo}..{maximo})")
            datos[k] = v
        for k in _AJUSTES_BOOL:
            if k in datos:
                datos[k] = _a_bool(datos[k], k)
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
    except (ValueError, TypeError) as e:
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
        # el motor tambien usa el margen para no resetear la permanencia
        _motor_compartido.timeout_persona_seg = float(
            datos["timeout_persona_seg"])

    # persistir en config.yaml (el dict _config lo lee el bucle en vivo)
    cambios = {k: v for k, v in datos.items()
               if k in _AJUSTES_NUM + _AJUSTES_BOOL + _AJUSTES_LISTA}
    _config.update(cambios)
    try:
        _persistir_config(cambios)
    except OSError:
        log.exception("no se pudo guardar config.yaml (ajustes en memoria igual)")
        return {"ok": False, "guardado": False}
    return {"ok": True, "guardado": True,
            "reinicio": any(k in datos for k in _REQUIEREN_REINICIO)}


def main() -> None:
    import os

    import uvicorn

    ruta_log = configurar_logging()
    log.info("log en %s", ruta_log or "consola")
    host = os.environ.get("VIGIA_HOST", "127.0.0.1")
    try:
        puerto = int(os.environ.get("VIGIA_PUERTO", "8000"))
    except ValueError:
        puerto = 8000
    parar = threading.Event()
    hilo = threading.Thread(
        target=bucle_procesamiento,
        args=(estado, _almacen, _config, _clips),
        kwargs={"parar": parar, "motor": _motor_compartido,
                "rastreador": _rastreador_compartido}, daemon=True)
    hilo.start()
    log.info("Dashboard en http://%s:%d", host, puerto)
    try:
        uvicorn.run(app, host=host, port=puerto, log_level="warning")
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
