"""Dashboard web local de Vigia.

Arranque: .venv\\Scripts\\python.exe -m vigia.web   →  http://localhost:8000

- /             panel: video en vivo + linea del tiempo + historial + clips
- /video.mjpg   stream MJPEG con detecciones, IDs de personas y zonas
- /estado       JSON: FPS, personas, objetos, estado de camara
- /eventos      JSON: feed reciente en memoria
- /historial    JSON: eventos de SQLite, filtrables
- /timeline     JSON: eventos de un dia o de una ventana de tiempo
- /media/{f}    sirve un clip (.webm/.mp4) o una miniatura (.jpg)
- /caras/...    registro de personas conocidas
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .almacen import Almacen
from .armado import GestorArmado
from .captura import Camara, LectorAsincrono, detectar_camaras
from .clips import GuardadorClips
from .analisis import AnalisisAsincrono, ModuloActividades, ModuloCaras
from .analisis import _iou, persona_de_pose as _persona_de_pose  # noqa: F401
from .caras import embedding_valido
from .galeria import GaleriaDesconocidos
from .clips import EXT_CLIP
from .config import RAIZ, cargar_config, ruta_proyecto
from .detector import Deteccion, Detector
from .dibujo import dibujar, frame_aviso
from .eventos import MotorEventos
from .logs import configurar_logging, leer_log
from .lineas import normalizar_lineas
from .mantener import mantener
from .notificaciones import notificar
from .seguridad import (CABECERAS_SEGURIDAD, RUTAS_PUBLICAS,
                        RUTAS_TOKEN_QUERY, Seguridad, cargar_env,
                        comprobar_arranque, es_loopback)
from .telegram import (VigilanteSistema, crear_notificador, estado_config)
from .tracking import RastreadorPersonas, ReasociadorIds
from .zonas import normalizar_zonas

log = logging.getLogger("vigia.web")

VERSION = "0.8"

_ESPERA_OK = 0.01
_ESPERA_CAIDA = 2.0
_ESPERA_ERROR = 0.5

# tipos de evento que justifican un clip de video
_EVENTOS_GRANDES = {"persona_entra", "objeto_desaparece", "permanencia"}
# tipos que ademas lanzan un aviso de Windows (evita spam al pasar gente)
_AVISOS = {"permanencia", "objeto_desaparece"}

_EXT_MEDIA = {".webm": "video/webm", ".mp4": "video/mp4",
              ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".png": "image/png"}

# fichero donde se persisten los ajustes del dashboard (los tests lo cambian)
_RUTA_CONFIG = RAIZ / "config.yaml"
# carpeta del log rotativo (los tests pueden apuntarla a tmp_path)
_RUTA_LOGS = RAIZ / "logs"
_persistir_lock = threading.Lock()

_INICIO = time.monotonic()


class _BusEventos:
    """Reparto de eventos nuevos a los clientes SSE (`GET /eventos/stream`).

    Cada suscriptor tiene su propia cola: un cliente lento no bloquea al bucle
    de video. Si se llena, se descarta el evento mas viejo de ese cliente (la
    GUI nueva siempre pide `/eventos` al arrancar, asi que no pierde historia).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._oyentes: set[queue.Queue] = set()

    def suscribir(self, maxsize: int = 200) -> queue.Queue:
        cola: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._oyentes.add(cola)
        return cola

    def desuscribir(self, cola: queue.Queue) -> None:
        with self._lock:
            self._oyentes.discard(cola)

    def publicar(self, evento: dict) -> None:
        with self._lock:
            oyentes = list(self._oyentes)
        for cola in oyentes:
            try:
                cola.put_nowait(evento)
            except queue.Full:
                try:
                    cola.get_nowait()
                    cola.put_nowait(evento)
                except (queue.Empty, queue.Full):
                    log.debug("cliente SSE lento: evento descartado")

    def oyentes(self) -> int:
        with self._lock:
            return len(self._oyentes)


_bus_eventos = _BusEventos()

# lo que el bucle publica para las rutas web
_actividades_personas: dict[int, str] = {}   # {id_tracking: actividad}
_act_lock = threading.Lock()
_ultima_cara: dict = {}                      # {emb, t, jpeg} de la cara mas grande
_cara_lock = threading.Lock()
_CARA_VIGENCIA_SEG = 5.0                     # una cara "vista" caduca pronto
# el bucle recarga el registro de conocidos cuando la UI lo cambia
bucle_caras_recarga = threading.Event()


class Estado:
    """Estado compartido entre el loop de captura y las rutas web."""

    def __init__(self) -> None:
        self.cerradura = threading.Lock()
        # condicion para que los clientes de MJPEG esperen frames nuevos
        # (un solo productor, N consumidores sin polling agresivo)
        self.condicion = threading.Condition(self.cerradura)
        self.version = 0
        self.frame_jpeg: bytes | None = None
        # ultimo frame sin dibujos: fondo del editor de zonas
        self.frame_crudo = None
        self.fps = 0.0
        self.detecciones: list[Deteccion] = []
        self.ids_persona: set[int] = set()
        self.camara_ok = False
        self.eventos: deque = deque(maxlen=50)  # (hora, tipo, etiqueta)
        # resolucion real de captura (la rellena el bucle)
        self.ancho = 640
        self.alto = 480

    def actualizar(self, jpeg: bytes | None, fps: float, dets: list[Deteccion],
                   ids_persona: set[int], camara_ok: bool,
                   crudo=None) -> None:
        with self.condicion:
            if jpeg is not None:
                self.frame_jpeg = jpeg
            if crudo is not None:
                self.frame_crudo = crudo
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


def _anotar_evento(estado: Estado, almacen: Almacen, clips, config: dict,
                   frame, tipo: str, detalle: str | None = "",
                   id_tracking: int | None = None,
                   evento_id: int | None = None,
                   zona: str | None = None,
                   camara_id: str | int | None = None,
                   duracion_seg: float | None = None) -> int | None:
    """Unico punto de salida de un evento: persiste, guarda media y publica.

    - si el evento ya existe (motor), se reutiliza su `evento_id`
    - guarda una miniatura JPEG del frame actual
    - en los eventos grandes guarda tambien el clip de los ultimos segundos
    - lanza el aviso de Windows solo para los tipos de `_AVISOS`
    - publica en el bus SSE y en el feed en memoria
    """
    camara = "0" if camara_id is None else str(camara_id)
    if evento_id is None:
        evento_id = almacen.guardar(tipo, id_tracking=id_tracking,
                                    detalle=detalle, zona=zona,
                                    camara_id=camara,
                                    duracion_seg=duracion_seg)
    ahora = datetime.now()
    miniatura = clip = None
    clip_path = None
    if clips is not None:
        if config.get("miniaturas", True):
            try:
                p = clips.guardar_miniatura(frame, tipo, camara_id=camara)
            except TypeError:  # dobles de test con la firma antigua
                p = clips.guardar_miniatura(frame, tipo)
            miniatura = p.name if p else None
        if tipo in _EVENTOS_GRANDES and config.get("guardar_clips", True):
            try:
                p = clips.guardar(tipo, camara_id=camara)
            except TypeError:  # dobles de test con la firma antigua
                p = clips.guardar(tipo)
            clip = p.name if p else None
            clip_path = p
        try:
            almacen.anotar_media(evento_id, miniatura=miniatura, clip=clip)
        except Exception:
            log.exception("no se pudo enlazar la media del evento %s", evento_id)
    if tipo in _AVISOS and config.get("notificar", True):
        notificar(f"Vigia: {tipo}", detalle or "")
    if _telegram is not None:
        try:
            _telegram.evento(
                tipo, detalle or "", frame=frame, clip=clip_path,
                clave=f"{detalle or ''}|{id_tracking or ''}",
                armado=_gestor_armado.armado)
        except Exception:
            log.exception("no se pudo encolar el aviso de Telegram")
    estado.anadir_evento(tipo, detalle or "")
    _bus_eventos.publicar({
        "id": evento_id,
        "marca": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "hora": ahora.strftime("%H:%M:%S"),
        "tipo": tipo,
        "etiqueta": detalle or "",
        "detalle": detalle,
        "id_tracking": id_tracking,
        "zona": zona,
        "camara_id": camara,
        "duracion_seg": duracion_seg,
        "miniatura": miniatura,
        "clip": clip,
    })
    return evento_id


def _publicar_actividades(actuales: dict[int, str]) -> None:
    with _act_lock:
        _actividades_personas.clear()
        _actividades_personas.update(actuales)


def _publicar_cara(ficha: dict) -> None:
    with _cara_lock:
        _ultima_cara.clear()
        _ultima_cara.update(ficha)


def _conocidos(almacen: Almacen) -> dict[str, list[np.ndarray]]:
    """Registro de caras: nombre -> todos sus embeddings (varias muestras)."""
    conocidos: dict[str, list[np.ndarray]] = {}
    for r in almacen.rostros():
        try:
            emb = embedding_valido(np.frombuffer(r["embedding"],
                                                 dtype=np.float32))
        except ValueError:
            log.warning("rostro %r ignorado: embedding invalido", r["nombre"])
            continue
        conocidos.setdefault(r["nombre"], []).append(emb)
    return conocidos


def _aplicar_camara(cam, config: dict) -> None:
    """Si el panel eligio otra webcam, cambia en caliente (y revierte si falla)."""
    destino = config.get("camara", 0)
    actual = getattr(cam, "fuente", destino)
    if not hasattr(cam, "cambiar") or destino == actual:
        return
    if not cam.cambiar(destino):
        config["camara"] = actual  # la nueva no abrio: el panel vuelve a la buena
        if config is _config:      # y config.yaml no se queda con un indice roto
            try:
                _persistir_config({"camara": actual})
            except OSError:
                log.exception("no se pudo restaurar la camara en config.yaml")


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
    # con camara propia: lectura en hilo aparte y pose/caras asincronas. Con
    # una camara inyectada (tests) todo va en serie y es determinista
    asincrono = cam is None
    if cam is None:
        cam = LectorAsincrono(Camara(
            fuente=int(config.get("camara", 0)), abrir=False,
            ancho=int(config.get("ancho", 640)),
            alto=int(config.get("alto", 480))))
    ancho = getattr(cam, "ancho", 640)
    alto = getattr(cam, "alto", 480)
    estado.ancho, estado.alto = ancho, alto
    if detector is None:
        try:
            detector = Detector(conf=0.45)
        except Exception:
            # sin modelo no hay nada que hacer: mejor avisar en el dashboard
            # que dejar el hilo muerto con la pantalla en negro
            log.exception("No se pudo cargar el modelo YOLO (falta "
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

    # pasos opcionales: cargan su modelo en segundo plano
    mod_actividades = ModuloActividades()
    mod_caras = ModuloCaras(
        cargar_conocidos=lambda: _conocidos(almacen),
        recarga=bucle_caras_recarga, al_ver_cara=_publicar_cara,
        cada_n_frames=2 if asincrono else None,
        al_ver_desconocida=(_galeria if almacen is _almacen
                            else GaleriaDesconocidos(almacen)).ver)
    analisis = (AnalisisAsincrono(mod_actividades, mod_caras,
                                  _publicar_actividades)
                if asincrono else None)
    reasociador = ReasociadorIds(
        ventana_seg=float(config.get("reasociar_seg", 8) or 8))

    fps_hist: deque[float] = deque(maxlen=30)
    fps_ultimo = 0.0
    ultimo_t = 0.0
    # identidad de la camara para media y eventos multicamara (P3)
    camara_id = str(config.get("camara_id", "0") or "0")
    # evita duplicar entra/salio si el rastreador repite el mismo aviso
    anunciados_dentro: set[int] = set()
    # la primera pasada de mantenimiento, tras el arranque
    proximo_mantenimiento = time.monotonic() + 60 * int(
        config.get("mantener_cada_min", 60) or 60)
    proximo_aviso_sistema = time.monotonic() + 5.0

    def anotar(frame, tipo, detalle="", id_tracking=None):
        _anotar_evento(estado, almacen, clips, config, frame, tipo, detalle,
                       id_tracking=id_tracking, camara_id=camara_id)

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
            _aplicar_camara(cam, config)
            ok, frame = cam.leer()
            if not ok:
                estado.actualizar(
                    _jpeg(frame_aviso(
                        "CAMARA CAIDA - conectala o revisa permisos",
                        ancho, alto)),
                    fps_ultimo, [], set(), False)
                espera = _ESPERA_CAIDA
            else:
                crudo = frame.copy()  # sin dibujos, para el editor de zonas
                dets, ids_idx = detector.detectar_track(frame)
                # ByteTrack cambia de ID al perder a alguien un momento: se
                # unifican para no inventar entradas ni reiniciar permanencias
                ids_idx = reasociador.mapear(ids_idx, dets)
                # el buffer guarda el frame ANTES de anotar el evento: asi el
                # clip incluye el instante que lo dispara
                if clips is not None and config.get("guardar_clips", True):
                    clips.fps_salida = min(max(fps_ultimo or 10.0, 1.0), 30.0)
                    clips.meter(frame)
                ids_persona = {ids_idx[i] for i, d in enumerate(dets)
                               if d.es_persona and i in ids_idx}
                fuera = motor.fuera_de_franja()
                for tipo, pid in rastreador.actualizar(ids_persona):
                    # coherencia: entra/sale tambien respeta las franjas
                    if fuera:
                        continue
                    # dedupe defensivo: el rastreador no debe anunciar dos
                    # veces la misma entrada ni una salida sin entrada
                    if tipo == "persona_entra":
                        if pid in anunciados_dentro:
                            continue
                        anunciados_dentro.add(pid)
                    elif tipo == "persona_salio":
                        if pid not in anunciados_dentro:
                            continue
                        anunciados_dentro.discard(pid)
                    anotar(frame, tipo, f"id {pid}", pid)
                mapa_ids = {ids_idx[i]: d.caja for i, d in enumerate(dets)
                            if d.es_persona and i in ids_idx}
                extras = [d for d in dets if not d.es_persona]
                identidades = (mod_caras.identidades
                               if config.get("caras", False) else None)
                for ev in motor.procesar(dets, mapa_ids, extras,
                                         identidades=identidades):
                    _anotar_evento(estado, almacen, clips, config, frame,
                                   ev.tipo, ev.detalle, evento_id=ev.id,
                                   id_tracking=ev.id_tracking, zona=ev.zona,
                                   camara_id=ev.camara_id or camara_id,
                                   duracion_seg=ev.duracion_seg)

                # los interruptores del panel se leen en cada frame: funcionan
                # sin reiniciar
                usar_act = bool(config.get("actividades", False))
                usar_caras = bool(config.get("caras", False))
                if analisis is not None:
                    # pose y caras corren en su hilo: aqui solo se entrega el
                    # frame, se recogen resultados y se pinta lo ultimo conocido
                    analisis.enviar(frame, mapa_ids, extras, usar_act,
                                    usar_caras)
                    for tipo, detalle, pid in analisis.recoger():
                        if not fuera:
                            anotar(frame, tipo, detalle, pid)
                    if usar_act:
                        mod_actividades.dibujar(frame)
                    if usar_caras:
                        mod_caras.etiquetar(frame, mapa_ids)
                else:
                    if usar_act:
                        try:
                            res = mod_actividades.procesar(frame, mapa_ids,
                                                           extras)
                            if res is not None:
                                actuales, anuncios = res
                                for pid, texto in anuncios:
                                    if not fuera:
                                        anotar(frame, "actividad",
                                               f"id {pid} {texto}", pid)
                                _publicar_actividades(actuales)
                        except Exception:
                            log.exception("error en pose/actividades (se sigue)")
                    elif mod_actividades.apagar():
                        _publicar_actividades({})

                    if usar_caras:
                        try:
                            for tipo, detalle, pid in (
                                    mod_caras.procesar(frame, mapa_ids) or []):
                                if not fuera:
                                    anotar(frame, tipo, detalle, pid)
                        except Exception:
                            log.exception("error en caras (se sigue)")
                    else:
                        mod_caras.apagar()

                ids_dentro = rastreador.ids_dentro()
                # leer las zonas del motor en cada frame: el editor las cambia
                # en caliente y no deben quedarse congeladas hasta reiniciar
                dibujar(frame, dets, fps_ultimo, pie=None,
                        ids_por_indice=ids_idx, ids_persona=ids_dentro,
                        zonas=motor.zonas_dibujo())
                estado.actualizar(_jpeg(frame), fps_ultimo, dets,
                                  ids_dentro, True, crudo=crudo)

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

        try:
            _gestor_armado.actualizar()
            if (_vigilante_sistema is not None
                    and time.monotonic() >= proximo_aviso_sistema):
                proximo_aviso_sistema = time.monotonic() + 60
                try:
                    libre_gb = shutil.disk_usage(
                        str(clips.carpeta if clips is not None else RAIZ)).free
                    libre_gb /= 1024 ** 3
                except OSError:
                    libre_gb = None
                _vigilante_sistema.revisar(estado.camara_ok, libre_gb)
        except Exception:
            log.exception("fallo en los avisos del sistema")

        if parar is not None:
            parar.wait(espera)
        else:
            time.sleep(espera)

    if analisis is not None:
        analisis.parar()
    if asincrono:
        cam.liberar()


app = FastAPI(title="Vigia")
app.include_router(__import__("vigia.vivo", fromlist=["router"]).router)


@app.exception_handler(StarletteHTTPException)
async def _errores_http(request: Request, exc: StarletteHTTPException):
    """Mismo texto en `detail` y `detalle` (contratos viejo y nuevo)."""
    return JSONResponse(
        {"detail": exc.detail, "detalle": exc.detail},
        status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def _errores_validacion(request: Request,
                               exc: RequestValidationError):
    from fastapi.encoders import jsonable_encoder
    detalle = jsonable_encoder(exc.errors())
    return JSONResponse({"detail": detalle, "detalle": detalle},
                        status_code=422)


# el panel nuevo carga css y modulos js desde /static; el middleware ya deja
# pasar ese prefijo sin token, igual que /auth/
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"),
          name="static")
estado = Estado()
cargar_env()
_config = cargar_config()
_almacen = Almacen(ruta_proyecto(_config.get("almacen", "vigia.db")))
_galeria = GaleriaDesconocidos(_almacen)
# se crea siempre: el buffer solo se llena con guardar_clips=true, pero los
# endpoints /clips y /media siguen sirviendo lo que ya hay en disco y el
# interruptor del panel funciona en caliente
_clips = GuardadorClips(carpeta=RAIZ / "clips", fps_salida=10.0)
# compartidos para poder aplicar los ajustes del dashboard en caliente
_motor_compartido = MotorEventos(_almacen, config=_config)
_rastreador_compartido = RastreadorPersonas(
    timeout_seg=float(_config.get("timeout_persona_seg", 10)),
    confirmar_seg=float(_config.get("confirmar_entrada_seg", 1.0) or 0))
_seguridad = Seguridad(token=os.environ.get("VIGIA_TOKEN"))
_gestor_armado = GestorArmado(_almacen, _config)
_telegram = crear_notificador(_config)
_vigilante_sistema = (VigilanteSistema(
    _telegram,
    camara_seg=float(_config.get("avisos_camara_seg", 300)),
    disco_gb=float(_config.get("avisos_disco_gb", 5)))
    if _telegram is not None else None)


def _aplicar_armado_en_caliente(config: dict) -> None:
    """Reconfigura el armado automatico sin perder el estado manual."""
    _gestor_armado.auto = bool(config.get("armado_auto", False))
    franjas = config.get("armado_franjas") or config.get("franjas") or []
    _gestor_armado.franjas = [
        (str(f.get("desde", "00:00")), str(f.get("hasta", "23:59")))
        for f in franjas]


def _reconfigurar_telegram() -> None:
    """Tras cambiar `telegram` en ajustes: crear/parar el notificador."""
    global _telegram, _vigilante_sistema
    if _telegram is not None:
        _telegram.parar()
    _telegram = crear_notificador(_config)
    _vigilante_sistema = (VigilanteSistema(
        _telegram,
        camara_seg=float(_config.get("avisos_camara_seg", 300)),
        disco_gb=float(_config.get("avisos_disco_gb", 5)))
        if _telegram is not None else None)


# -- seguridad: token, cookie y cabeceras ------------------------------------
def _es_publica(ruta: str) -> bool:
    return ruta in RUTAS_PUBLICAS or any(
        ruta.startswith(prefijo) for prefijo in
        ("/static/", "/auth/"))


def _respuesta_no_autenticado() -> JSONResponse:
    return JSONResponse({"detalle": "no autenticado",
                         "detail": "no autenticado"}, status_code=401,
                        headers=dict(CABECERAS_SEGURIDAD))


def _origen_propio(request: Request) -> bool:
    """True si la peticion no declara origen o este coincide con el Host."""
    origen = request.headers.get("origin")
    if not origen:
        return True  # curl, scripts y navegadores en misma-origen sin cabecera
    host = request.headers.get("host", "")
    return urlsplit(origen).netloc.lower() == host.lower()


@app.middleware("http")
async def _middleware_seguridad(request: Request, call_next):
    ruta = request.url.path
    cliente_ip = request.client.host if request.client else ""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origen_propio(
            request):
        # CSRF: una web cualquiera abierta en tu navegador no debe poder
        # cambiar la config de un panel escuchando en localhost
        return JSONResponse({"detalle": "origen no permitido",
                             "detail": "origen no permitido"},
                            status_code=403,
                            headers=dict(CABECERAS_SEGURIDAD))
    if not _es_publica(ruta):
        if not _seguridad.requerida:
            # sin token solo se atiende a la propia maquina. Si hay cabeceras
            # de proxy, la peticion viene de fuera aunque llegue por loopback
            # (un Caddy/nginx local lo haria pasar por 127.0.0.1)
            if (not es_loopback(cliente_ip)
                    or "x-forwarded-for" in request.headers
                    or "forwarded" in request.headers):
                return JSONResponse(
                    {"detalle": "sin VIGIA_TOKEN solo se permite localhost",
                     "detail": "sin VIGIA_TOKEN solo se permite localhost"},
                    status_code=403, headers=dict(CABECERAS_SEGURIDAD))
        else:
            token_query = (request.query_params.get("token")
                           if ruta in RUTAS_TOKEN_QUERY else None)
            if not _seguridad.credencial_valida(
                    request.headers.get("authorization"),
                    request.cookies.get("vigia_token"), token_query):
                return _respuesta_no_autenticado()
    respuesta = await call_next(request)
    for clave, valor in CABECERAS_SEGURIDAD.items():
        respuesta.headers.setdefault(clave, valor)
    return respuesta


@app.post("/auth/login")
async def auth_login(datos: dict, request: Request):
    """Body {"token": "..."} -> cookie HttpOnly; 401 si no coincide."""
    ip = request.client.host if request.client else ""
    if not _seguridad.requerida:
        return {"ok": True, "auth_requerida": False}
    if not _seguridad.permitir_login(ip):
        raise HTTPException(429, "demasiados intentos; espera un minuto")
    token = str((datos or {}).get("token", ""))
    if not _seguridad.valido(token):
        _seguridad.fallo_login(ip)
        raise HTTPException(401, "token incorrecto")
    _seguridad.exito_login(ip)
    respuesta = JSONResponse({"ok": True, "auth_requerida": True})
    respuesta.set_cookie("vigia_token", token, httponly=True, samesite="lax",
                         max_age=30 * 24 * 3600, path="/")
    return respuesta


@app.post("/auth/logout")
async def auth_logout():
    respuesta = JSONResponse({"ok": True})
    respuesta.delete_cookie("vigia_token", path="/")
    return respuesta


@app.get("/auth/estado")
def auth_estado(request: Request) -> dict:
    """Siempre accesible: la GUI decide si mostrar el login."""
    autenticado = (not _seguridad.requerida
                   or _seguridad.credencial_valida(
                       request.headers.get("authorization"),
                       request.cookies.get("vigia_token")))
    return {"auth_requerida": _seguridad.requerida,
            "autenticado": bool(autenticado)}


# -- armado / desarmado ------------------------------------------------------
def _comando_bot(comando: str):
    """Comandos del bot; `/foto` devuelve tambien el ultimo frame."""
    if comando == "/armar":
        _gestor_armado.auto = False
        _gestor_armado.armar()
        return "Vigia ARMADO."
    if comando == "/desarmar":
        _gestor_armado.auto = False
        _gestor_armado.desarmar()
        return "Vigia desarmado."
    if comando == "/estado":
        e = _gestor_armado.estado()
        return (f"Vigia {'ARMADO' if e['armado'] else 'desarmado'}"
                + (" (automatico)" if e["auto"] else ""))
    if comando == "/foto":
        jpeg, *_ = estado.snapshot()
        if not jpeg:
            return "Todavia no hay imagen de la camara."
        return ("Foto actual de Vigia.", jpeg)
    return "Comandos: /armar /desarmar /estado /foto"


@app.get("/armado")
def armado_json(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    _gestor_armado.actualizar()
    return _gestor_armado.estado()


@app.post("/armado")
async def armado_post(datos: dict) -> dict:
    """Body {"armado": bool} y opcional {"auto": bool}."""
    datos = datos or {}
    if "auto" in datos:
        _gestor_armado.configurar_auto(_bool_json(datos["auto"], "auto"))
    if "armado" in datos:
        # un cambio manual desactiva el automatico para que no lo revierta
        _gestor_armado.auto = False if "auto" not in datos else \
            _gestor_armado.auto
        if _bool_json(datos["armado"], "armado"):
            _gestor_armado.armar()
        else:
            _gestor_armado.desarmar()
    return _gestor_armado.estado()


def _bool_json(valor, clave: str) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = str(valor).strip().lower()
    if texto in ("1", "true", "si", "sí", "yes", "on"):
        return True
    if texto in ("0", "false", "no", "off", ""):
        return False
    raise HTTPException(400, f"{clave} debe ser booleano")


# -- Telegram ----------------------------------------------------------------
@app.get("/telegram/estado")
def telegram_estado(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    salida = estado_config(_config)
    if _telegram is not None:
        salida.update(_telegram.estado())
    return salida


@app.post("/telegram/prueba")
def telegram_prueba() -> dict:
    if _telegram is None:
        raise HTTPException(
            409, "Telegram no esta configurado (TELEGRAM_TOKEN/CHAT_ID y "
                 "telegram: true en config.yaml)")
    return _telegram.enviar_prueba()


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



@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # se relee en cada visita: editar el panel no exige reiniciar el servidor
    return _leer_panel()


@app.get("/video.mjpg")
def video(limpio: bool = False) -> StreamingResponse:
    """MJPEG en vivo. `?limpio=1`: sin cajas ni texto (la GUI las dibuja encima)."""
    def gen():
        vistos = -1
        while True:
            with estado.condicion:
                estado.condicion.wait_for(
                    lambda: estado.version != vistos, timeout=1.0)
                jpeg = estado.frame_jpeg
                crudo = (estado.frame_crudo
                         if limpio and estado.camara_ok else None)
                vistos = estado.version
            if crudo is not None:
                jpeg = _jpeg(crudo) or jpeg
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
            "ids": sorted(ids_persona),
            "objetos": sum(1 for d in dets if not d.es_persona),
            "ancho": estado.ancho, "alto": estado.alto}


_camaras_cache: dict = {"t": 0.0, "datos": []}
_CAMARAS_VIGENCIA_SEG = 30.0
_camaras_lock = threading.Lock()


@app.get("/camaras")
def camaras_json(response: Response, refrescar: bool = False) -> dict:
    """Webcams disponibles y la que esta en uso (el sondeo tarda ~1 s: se cachea)."""
    response.headers["Cache-Control"] = "no-store"
    actual = int(_config.get("camara", 0))
    with _camaras_lock:
        vieja = time.monotonic() - _camaras_cache["t"] > _CAMARAS_VIGENCIA_SEG
        if refrescar or vieja or not _camaras_cache["datos"]:
            try:
                _camaras_cache["datos"] = detectar_camaras(ocupada=actual)
                _camaras_cache["t"] = time.monotonic()
            except Exception:  # drivers rotos: mejor lista vacia que un 500
                log.exception("no se pudieron sondear las camaras")
                _camaras_cache["datos"] = _camaras_cache["datos"] or []
        datos = list(_camaras_cache["datos"])
    if actual not in {c["indice"] for c in datos}:
        datos.append({"indice": actual, "disponible": False})
    return {"actual": actual, "camaras": sorted(datos,
                                                key=lambda c: c["indice"])}


@app.get("/snapshot.jpg")
def snapshot(limpio: bool = False) -> Response:
    """Ultimo frame como JPEG. `?limpio=1`: sin cajas ni texto (editor de zonas)."""
    jpeg, *_ = estado.snapshot()
    if limpio and estado.frame_crudo is not None:
        jpeg = _jpeg(estado.frame_crudo)
    if not jpeg:
        raise HTTPException(404, "todavia no hay imagen")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/eventos")
def eventos_json(response: Response, tipo: str | None = None,
                 camara_id: str | None = None,
                 desde: str | None = None, hasta: str | None = None,
                 persona: int | None = None, zona: str | None = None,
                 limite: int | None = None,
                 antes_id: int | None = None):
    """Feed en memoria (compatibilidad) o consulta paginada en SQLite.

    - Sin parametros: lista de los ultimos eventos en memoria, como siempre
      (`[{"hora","tipo","etiqueta"}]`).
    - Con cualquier parametro (`limite`, `antes_id`, `tipo`, `camara_id`,
      `desde`, `hasta`, `persona`, `zona`): objeto
      `{"eventos": [...], "total": n, "limite": n, "antes_id": ...}`.
    """
    response.headers["Cache-Control"] = "no-store"
    nuevo = any(v is not None for v in (
        tipo, camara_id, desde, hasta, persona, zona, limite, antes_id))
    if not nuevo:
        with estado.cerradura:
            return [{"hora": h, "tipo": t, "etiqueta": e}
                    for h, t, e in estado.eventos]
    for nombre, valor in (("desde", desde), ("hasta", hasta)):
        if valor is not None:
            _instante(valor, nombre)
    limite = max(1, min(int(limite or 100), 500))
    datos = _almacen.listar_eventos(
        limite=limite, antes_id=antes_id, tipo=tipo, camara_id=camara_id,
        desde=desde, hasta=hasta, persona=persona, zona=zona)
    datos.update({"limite": limite, "antes_id": antes_id,
                  "siguiente_antes_id": (datos["eventos"][-1]["id"]
                                         if datos["eventos"] else None)})
    return datos


def _instante(valor: str, nombre: str) -> str:
    """Valida `AAAA-MM-DD` o `AAAA-MM-DD HH:MM:SS`; 400 si no encaja."""
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            datetime.strptime(texto, formato)
            return texto
        except ValueError:
            continue
    raise HTTPException(400, f"{nombre}: AAAA-MM-DD[ HH:MM:SS]")


@app.get("/eventos/stream")
def eventos_stream(request: Request, limite: int = 0):
    """SSE: cada evento nuevo y un `ping` cada 15 s.

    `?limite=N` cierra el stream tras N eventos (util en tests y para clientes
    que solo quieren una foto del feed). El bus se limpia siempre al cerrar.
    """
    def gen():
        cola = _bus_eventos.suscribir()
        enviados = 0
        try:
            yield b": conectado\n\n"
            while True:
                try:
                    evento = cola.get(timeout=15.0)
                except queue.Empty:
                    yield b"event: ping\ndata: {}\n\n"
                    continue
                datos = json.dumps(evento, ensure_ascii=False)
                yield f"event: evento\ndata: {datos}\n\n".encode("utf-8")
                enviados += 1
                if limite and enviados >= limite:
                    break
        finally:
            _bus_eventos.desuscribir(cola)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})


@app.get("/logs")
def logs_json(response: Response, limite: int = 200,
              nivel: str | None = None) -> dict:
    """Ultimas lineas de `logs/vigia.log` para la vista Sistema."""
    response.headers["Cache-Control"] = "no-store"
    return leer_log(_RUTA_LOGS, limite=limite, nivel=nivel)


@app.get("/estadisticas")
def estadisticas_json(response: Response, dias: int = 7) -> dict:
    """Agregados de eventos para el Dashboard (SQL, sin traerse todo)."""
    response.headers["Cache-Control"] = "no-store"
    try:
        dias = max(1, min(int(dias), 365))
    except (TypeError, ValueError):
        raise HTTPException(400, "dias debe ser un entero")
    return _almacen.estadisticas(dias)


def _aforo_guardado() -> int:
    try:
        valor = _almacen.leer_estado("aforo_actual")
        return int(valor or 0)
    except Exception:
        log.exception("no se pudo leer el aforo actual")
        return 0


def _resumen_camaras() -> dict:
    _, fps, _, ids, ok = estado.snapshot()
    camaras = [{"id": "0", "nombre": "Camara", "estado":
                "conectada" if ok else "caida",
                "fps": round(fps, 1), "personas": len(ids)}]
    return {"actual": "0", "camaras": camaras}


@app.get("/resumen")
def resumen_json(response: Response) -> dict:
    """Todo lo del Dashboard en una sola llamada (menos polling)."""
    response.headers["Cache-Control"] = "no-store"
    _, fps, _, ids, camara_ok = estado.snapshot()
    hoy = datetime.now().date().isoformat()
    try:
        est = _almacen.estadisticas(1)
        ultimo = _almacen.historial(limite=1)
        db_bytes = _almacen.tamano_bytes()
        total_eventos = _almacen.contar()
    except Exception:
        log.exception("no se pudo leer la BD para /resumen")
        est, ultimo, db_bytes, total_eventos = {}, [], 0, 0
    try:
        raiz = _clips.carpeta if _clips is not None else RAIZ
        uso = shutil.disk_usage(str(raiz))
        disco = {"libre_gb": round(uso.free / 1024 ** 3, 1),
                 "total_gb": round(uso.total / 1024 ** 3, 1)}
    except OSError:
        disco = {"libre_gb": 0.0, "total_gb": 0.0}
    telegram = estado_config(_config)
    if _telegram is not None:
        telegram.update(_telegram.estado())
    return {
        "version": VERSION,
        "uptime_seg": int(time.monotonic() - _INICIO),
        "camara": {"ok": camara_ok, "fps": round(fps, 1),
                   "personas": len(ids), "ids": sorted(ids),
                   "ancho": estado.ancho, "alto": estado.alto},
        "camaras": _resumen_camaras(),
        "armado": _gestor_armado.estado(),
        "eventos_hoy": {"dia": hoy,
                        "total": est.get("total", 0),
                        "por_tipo": est.get("por_tipo", {})},
        "ultimo_evento": ultimo[0] if ultimo else None,
        "total_eventos": total_eventos,
        "aforo_actual": _aforo_guardado(),
        "disco": disco,
        "db_bytes": db_bytes,
        "telegram": telegram,
    }


@app.get("/historial")
def historial_json(response: Response, tipo: str | None = None,
                   desde: str | None = None, limite: int = 100) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    limite = max(1, min(limite, 500))
    return _almacen.historial(limite=limite, tipo=tipo, desde=desde)


def _fichero_seguro(fichero: str, extensiones) -> Path:
    """Resuelve un fichero de media y rechaza traversal y nombres raros."""
    nombre = str(fichero or "")
    if (not nombre or nombre != Path(nombre).name or "/" in nombre
            or "\\" in nombre or ".." in Path(nombre).parts
            or nombre.startswith("~") or nombre.startswith(".")):
        raise HTTPException(404, "fichero no encontrado")
    carpeta = _clips.carpeta.resolve()
    ruta = (carpeta / nombre).resolve()
    if ruta.parent != carpeta or ruta.suffix.lower() not in extensiones:
        raise HTTPException(404, "fichero no encontrado")
    if not ruta.is_file():
        raise HTTPException(404, "fichero no encontrado")
    return ruta


@app.get("/clip/{fichero}")
def clip(fichero: str):
    if _clips is None:
        raise HTTPException(404, "clips desactivados")
    ruta = _fichero_seguro(fichero, EXT_CLIP)
    return FileResponse(ruta, media_type=_EXT_MEDIA.get(ruta.suffix.lower(),
                                                        "video/mp4"))


@app.get("/clips")
def clips_json(response: Response) -> list[dict]:
    response.headers["Cache-Control"] = "no-store"
    if _clips is None:
        return []
    return _clips.listar()


@app.get("/media/{fichero}")
def media(fichero: str):
    """Sirve un clip (.webm/.mp4) o una miniatura (.jpg) de la carpeta de media."""
    if _clips is None:
        raise HTTPException(404, "media desactivada")
    ruta = _fichero_seguro(fichero, tuple(_EXT_MEDIA))
    return FileResponse(ruta, media_type=_EXT_MEDIA[ruta.suffix.lower()])


@app.get("/timeline")
def timeline_json(response: Response, dia: str | None = None,
                  desde: str | None = None, hasta: str | None = None,
                  limite: int = 5000) -> dict:
    """Eventos de un dia (`?dia=`) o de una ventana (`?desde=&hasta=`,
    `YYYY-MM-DD HH:MM:SS`). Sin parametros: hoy."""
    response.headers["Cache-Control"] = "no-store"
    if desde or hasta:
        try:
            d = datetime.strptime(desde or "", "%Y-%m-%d %H:%M:%S")
            h = datetime.strptime(hasta or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(400, "desde/hasta: AAAA-MM-DD HH:MM:SS")
        if h <= d or h - d > timedelta(days=8):
            raise HTTPException(400, "ventana invalida (maximo 8 dias)")
        eventos = _almacen.eventos_entre(desde, hasta, limite=limite)
        return {"desde": desde, "hasta": hasta, "total": len(eventos),
                "eventos": eventos}
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
    return {"zonas": _motor_compartido.zonas_dict(),
            "ancho": estado.ancho, "alto": estado.alto}


@app.put("/zonas")
async def zonas_guardar(datos: dict) -> dict:
    """Zonas desde el editor visual: se aplican en caliente y se persisten.

    Acepta el formato nuevo (objetos) y el antiguo `nombre: [x1,y1,x2,y2]`.
    """
    try:
        zonas = normalizar_zonas((datos or {}).get("zonas", {}), estricto=True,
                                 log_errores=False, ancho=estado.ancho,
                                 alto=estado.alto)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await config_guardar(
        {"zonas": {n: z.a_dict() for n, z in zonas.items()}})


@app.get("/lineas")
def lineas_json(response: Response) -> dict:
    """Lineas de cruce (tripwires) actuales y resolucion vigente."""
    response.headers["Cache-Control"] = "no-store"
    return {"lineas": _motor_compartido.lineas_dict(),
            "ancho": estado.ancho, "alto": estado.alto}


@app.put("/lineas")
async def lineas_guardar(datos: dict) -> dict:
    """Lineas desde el editor visual: validacion estricta y caliente."""
    try:
        lineas = normalizar_lineas((datos or {}).get("lineas", {}),
                                   estricto=True, ancho=estado.ancho,
                                   alto=estado.alto, log_errores=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await config_guardar(
        {"lineas": {n: l.a_dict() for n, l in lineas.items()}})


@app.get("/actividades")
def actividades_json(response: Response) -> dict:
    """Mapa id_tracking -> actividad actual (usando PC, durmiendo...)."""
    response.headers["Cache-Control"] = "no-store"
    with _act_lock:
        return dict(_actividades_personas)


# -- registro de caras ---------------------------------------------------------
def _cara_vigente() -> dict | None:
    with _cara_lock:
        ficha = dict(_ultima_cara)
    if not ficha or time.time() - ficha.get("t", 0.0) > _CARA_VIGENCIA_SEG:
        return None
    return ficha


@app.get("/caras/registro")
def caras_registro(response: Response) -> list[dict]:
    """Personas conocidas: id y nombre (los embeddings no se exponen)."""
    response.headers["Cache-Control"] = "no-store"
    return [{"id": r["id"], "nombre": r["nombre"]}
            for r in _almacen.rostros()]


@app.get("/caras/ultima")
def caras_ultima(response: Response) -> dict:
    """¿Hay una cara a la vista ahora mismo que se pueda registrar?"""
    response.headers["Cache-Control"] = "no-store"
    return {"disponible": _cara_vigente() is not None}


@app.get("/caras/ultima.jpg")
def caras_ultima_jpg() -> Response:
    """Recorte de la cara que se registraria (para confirmarla a ojo)."""
    ficha = _cara_vigente()
    if not ficha or not ficha.get("jpeg"):
        raise HTTPException(404, "no hay ninguna cara a la vista")
    return Response(content=ficha["jpeg"], media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


def _nombre_valido(datos: dict) -> str:
    nombre = str(datos.get("nombre", "")).strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre")
    if len(nombre) > 60:
        raise HTTPException(400, "nombre demasiado largo (maximo 60)")
    return nombre


@app.post("/caras/registro")
async def caras_registrar(datos: dict) -> dict:
    """Registra o renombra a una persona conocida.

    {nombre, embedding: [128 floats]} -> registra una muestra nueva
    {nombre, id}                      -> renombra la entrada id
    {nombre, capturar: true}          -> registra la cara que se ve ahora
    """
    datos = datos or {}
    nombre = _nombre_valido(datos)
    if datos.get("id") is not None:
        try:
            id_entrada = int(datos["id"])
        except (TypeError, ValueError):
            raise HTTPException(400, "id invalido")
        if not _almacen.renombrar_rostro(id_entrada, nombre):
            raise HTTPException(404, "entrada no encontrada")
    elif datos.get("capturar"):
        ficha = _cara_vigente()
        if ficha is None:
            raise HTTPException(
                409, "no se ve ninguna cara: ponte frente a la camara")
        _almacen.guardar_rostro(nombre, ficha["emb"].tobytes(),
                                ficha.get("jpeg") or None)
    elif datos.get("embedding") is not None:
        try:
            emb = embedding_valido(datos["embedding"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        _almacen.guardar_rostro(nombre, emb.tobytes())
    else:
        raise HTTPException(400, "falta 'embedding', 'capturar' o 'id'")
    bucle_caras_recarga.set()
    return {"ok": True}


@app.delete("/caras/registro/{id_entrada}")
def caras_borrar(id_entrada: int) -> dict:
    if not _almacen.borrar_rostro(id_entrada):
        raise HTTPException(404, "entrada no encontrada")
    bucle_caras_recarga.set()
    return {"ok": True}


@app.get("/caras/personas")
def caras_personas(response: Response) -> dict:
    """Galeria: personas conocidas (con sus tomas) y caras sin identificar."""
    response.headers["Cache-Control"] = "no-store"
    conocidas: dict[str, list[dict]] = {}
    for r in _almacen.rostros_con_foto():
        conocidas.setdefault(r["nombre"], []).append(
            {"id": r["id"], "foto": r["foto"], "creado": r["creado"]})
    return {
        "conocidas": [{"nombre": n, "tomas": t} for n, t in conocidas.items()],
        "desconocidas": _almacen.listar_desconocidos(),
    }


def _jpeg_o_404(datos: bytes | None) -> Response:
    if not datos:
        raise HTTPException(404, "sin foto")
    return Response(content=datos, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/caras/foto/rostro/{id_rostro}")
def caras_foto_rostro(id_rostro: int) -> Response:
    return _jpeg_o_404(_almacen.foto_rostro(id_rostro))


@app.get("/caras/foto/toma/{id_toma}")
def caras_foto_toma(id_toma: int) -> Response:
    return _jpeg_o_404(_almacen.foto_toma(id_toma))


@app.post("/caras/desconocidas/{id_grupo}/nombrar")
async def caras_nombrar_desconocida(id_grupo: int, datos: dict) -> dict:
    """Le pone nombre a una cara sin identificar: pasa a ser persona conocida."""
    nombre = _nombre_valido(datos or {})
    n = _almacen.promover_desconocido(id_grupo, nombre)
    if not n:
        raise HTTPException(404, "grupo no encontrado")
    _galeria.olvidar_cache()
    bucle_caras_recarga.set()
    return {"ok": True, "muestras": n}


@app.delete("/caras/desconocidas/{id_grupo}")
def caras_borrar_desconocida(id_grupo: int) -> dict:
    if not _almacen.borrar_desconocido(id_grupo):
        raise HTTPException(404, "grupo no encontrado")
    _galeria.olvidar_cache()
    return {"ok": True}


@app.post("/caras/persona/renombrar")
async def caras_renombrar_persona(datos: dict) -> dict:
    datos = datos or {}
    de = str(datos.get("de", "")).strip()
    a = _nombre_valido({"nombre": datos.get("a", "")})
    if not de or not _almacen.renombrar_persona(de, a):
        raise HTTPException(404, "persona no encontrada")
    bucle_caras_recarga.set()
    return {"ok": True}


@app.post("/caras/persona/tomar")
async def caras_tomar_para(datos: dict) -> dict:
    """Anade otra muestra (la cara que se ve ahora) a una persona conocida."""
    nombre = _nombre_valido(datos or {})
    ficha = _cara_vigente()
    if ficha is None:
        raise HTTPException(409, "no se ve ninguna cara: ponte frente a la camara")
    _almacen.guardar_rostro(nombre, ficha["emb"].tobytes(),
                            ficha.get("jpeg") or None)
    bucle_caras_recarga.set()
    return {"ok": True}


@app.delete("/caras/persona")
def caras_borrar_persona(nombre: str) -> dict:
    if not _almacen.borrar_persona(nombre.strip()):
        raise HTTPException(404, "persona no encontrada")
    bucle_caras_recarga.set()
    return {"ok": True}


# -- ajustes ------------------------------------------------------------------
_AJUSTES_NUM = ("camara", "permanencia_seg", "timeout_objeto_seg",
                "timeout_persona_seg", "ancho", "alto", "retencion_dias",
                "retencion_clips_dias", "retencion_clips_mb",
                "mantener_cada_min", "permanencia_conocidos_seg", "reaviso_min",
                "merodeo_entradas", "merodeo_ventana_seg", "inmovil_seg",
                "inmovil_px", "max_personas", "telegram_max_hora",
                "telegram_cooldown_seg", "avisos_camara_seg",
                "avisos_disco_gb")
_RANGOS = {
    "permanencia_seg": (1, 3600),
    "timeout_objeto_seg": (1, 3600),
    "timeout_persona_seg": (1, 3600),
    "camara": (0, 9),
    "ancho": (160, 3840),
    "alto": (120, 2160),
    "retencion_dias": (0, 3650),
    "retencion_clips_dias": (0, 3650),
    "retencion_clips_mb": (0, 1024 * 1024),
    "mantener_cada_min": (1, 1440),
    "permanencia_conocidos_seg": (0, 3600),
    "reaviso_min": (0, 1440),
    "merodeo_entradas": (1, 100),
    "merodeo_ventana_seg": (1, 86400),
    "inmovil_seg": (0, 86400),
    "inmovil_px": (1, 4000),
    "max_personas": (0, 1000),
    "telegram_max_hora": (0, 10000),
    "telegram_cooldown_seg": (0, 86400),
    "avisos_camara_seg": (0, 86400),
    "avisos_disco_gb": (0, 100000),
}
_AJUSTES_BOOL = ("guardar_clips", "notificar", "actividades", "miniaturas",
                 "caras", "telegram", "armado_auto")
_AJUSTES_LISTA = ("vigilar_desaparicion", "zonas", "lineas", "franjas",
                  "armado_franjas")
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
    c["zonas"] = _motor_compartido.zonas_dict()
    c["lineas"] = _motor_compartido.lineas_dict()
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
    with _persistir_lock:
        _persistir_config_sin_lock(cambios)


def _persistir_config_sin_lock(cambios: dict) -> None:
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
    # escritura atomica: un corte a medias no deja el YAML truncado
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text("\n".join(lineas).rstrip("\n") + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


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
            try:
                datos["zonas"] = normalizar_zonas(
                    zonas, estricto=True, log_errores=False,
                    ancho=estado.ancho, alto=estado.alto)
            except ValueError as e:
                raise ValueError(str(e))
        lineas = datos.get("lineas")
        if lineas is not None:
            try:
                datos["lineas"] = normalizar_lineas(
                    lineas, estricto=True, ancho=estado.ancho,
                    alto=estado.alto, log_errores=False)
            except ValueError as e:
                raise ValueError(str(e))
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
        armado_franjas = datos.get("armado_franjas")
        if armado_franjas is not None:
            from datetime import datetime as _dt
            if not isinstance(armado_franjas, list):
                raise ValueError("armado_franjas debe ser una lista")
            for f in armado_franjas:
                if not isinstance(f, dict):
                    raise ValueError("armado_franjas: franja desde/hasta")
                _dt.strptime(str(f.get("desde", "")), "%H:%M")
                _dt.strptime(str(f.get("hasta", "")), "%H:%M")
        prioridades = datos.get("telegram_prioridades")
        if prioridades is not None:
            if not isinstance(prioridades, dict):
                raise ValueError("telegram_prioridades debe ser un mapa")
            from .zonas import PRIORIDADES
            for tipo, valor in list(prioridades.items()):
                texto = str(valor).strip().lower()
                if texto not in PRIORIDADES:
                    raise ValueError(
                        f"prioridad invalida para {tipo}: {valor!r}")
                prioridades[tipo] = texto
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
        _motor_compartido.zonas = dict(datos["zonas"])
    if "lineas" in datos:
        _motor_compartido.gestor_lineas.actualizar(dict(datos["lineas"]))
        _motor_compartido.lineas = dict(datos["lineas"])
    if "franjas" in datos:
        _motor_compartido.franjas = [
            (f["desde"], f["hasta"]) for f in datos["franjas"]]
    if "permanencia_conocidos_seg" in datos:
        _motor_compartido.permanencia_conocidos_seg = float(
            datos["permanencia_conocidos_seg"])
    if "reaviso_min" in datos:
        _motor_compartido.reaviso_seg = float(datos["reaviso_min"]) * 60.0
    if "merodeo_entradas" in datos:
        _motor_compartido.merodeo_entradas = int(datos["merodeo_entradas"])
    if "merodeo_ventana_seg" in datos:
        _motor_compartido.merodeo_ventana_seg = float(
            datos["merodeo_ventana_seg"])
    if "inmovil_seg" in datos:
        _motor_compartido.inmovil_seg = float(datos["inmovil_seg"])
    if "inmovil_px" in datos:
        _motor_compartido.inmovil_px = float(datos["inmovil_px"])
    if "max_personas" in datos:
        _motor_compartido.max_personas = int(datos["max_personas"])
    if "timeout_persona_seg" in datos:
        _rastreador_compartido.timeout_seg = float(datos["timeout_persona_seg"])
        # el motor tambien usa el margen para no resetear la permanencia
        _motor_compartido.timeout_persona_seg = float(
            datos["timeout_persona_seg"])

    # persistir en config.yaml (el dict _config lo lee el bucle en vivo)
    guardables = dict(datos)
    if "zonas" in guardables:
        guardables["zonas"] = {n: z.a_dict()
                               for n, z in guardables["zonas"].items()}
    if "lineas" in guardables:
        guardables["lineas"] = {n: l.a_dict()
                                for n, l in guardables["lineas"].items()}
    cambios = {k: v for k, v in guardables.items()
               if k in _AJUSTES_NUM + _AJUSTES_BOOL + _AJUSTES_LISTA
               or k == "telegram_prioridades"}
    _config.update(cambios)
    if "armado_auto" in cambios or "armado_franjas" in cambios:
        _aplicar_armado_en_caliente(_config)
    if "telegram" in cambios:
        _reconfigurar_telegram()
    try:
        _persistir_config(cambios)
    except OSError:
        log.exception("no se pudo guardar config.yaml (ajustes en memoria igual)")
        return {"ok": False, "guardado": False}
    return {"ok": True, "guardado": True,
            "reinicio": any(k in datos for k in _REQUIEREN_REINICIO)}


def _host_y_puerto(config: dict, entorno=None) -> tuple[str, int]:
    """Las variables de entorno VIGIA_HOST/VIGIA_PUERTO ganan a config.yaml."""
    entorno = os.environ if entorno is None else entorno
    host = entorno.get("VIGIA_HOST") or str(config.get("host", "127.0.0.1"))
    try:
        puerto = int(entorno.get("VIGIA_PUERTO") or config.get("puerto", 8000))
    except (TypeError, ValueError):
        puerto = 8000
    return host, puerto


def main() -> None:
    import uvicorn

    ruta_log = configurar_logging()
    log.info("log en %s", ruta_log or "consola")
    host, puerto = _host_y_puerto(_config)
    try:
        comprobar_arranque(host, _seguridad.token)
    except RuntimeError as e:
        log.error("%s", e)
        raise SystemExit(str(e))
    _gestor_armado.actualizar()
    if _telegram is not None:
        _telegram.iniciar(_comando_bot)
        _telegram.aviso_sistema("servicio reiniciado")
    parar = threading.Event()
    hilo = threading.Thread(
        target=bucle_procesamiento,
        args=(estado, _almacen, _config, _clips),
        kwargs={"parar": parar, "motor": _motor_compartido,
                "rastreador": _rastreador_compartido}, daemon=True)
    hilo.start()
    log.info("Dashboard en http://%s:%d", host, puerto)
    try:
        # los streams MJPEG nunca terminan solos: sin limite, Ctrl+C se cuelga
        uvicorn.run(app, host=host, port=puerto, log_level="warning",
                    timeout_graceful_shutdown=3)
    finally:
        # parar el bucle antes de cerrar la BD: si no, el hilo sigue
        # escribiendo sobre una conexion cerrada hasta que muere el proceso
        parar.set()
        hilo.join(5)
        if _telegram is not None:
            _telegram.parar()
        if _clips is not None:
            _clips.esperar_trabajos(5)
        _almacen.cerrar()


if __name__ == "__main__":
    main()
