"""Cliente y notificador de Telegram por long polling (solo stdlib).

- Los secretos (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`) se leen del entorno o
  de `.env`; nunca de `config.yaml`.
- El bot solo atiende al chat autorizado.
- La deteccion nunca espera a la red: los avisos se encolan y un hilo aparte
  los envia. El clip WebM se manda cuando ya esta escrito en disco.

TODO: algunos clientes moviles no reproducen WebM (VP8). Telegram lo acepta
como documento/video, pero si no se reproduce habria que convertir a MP4
H.264; eso exige ffmpeg y queda pendiente a proposito (no se instala aqui).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
PRIORIDADES = ("info", "aviso", "critico")


class TelegramError(RuntimeError):
    pass


class ClienteTelegram:
    """Llamadas a la Bot API con `urllib` (sin dependencias nuevas)."""

    def __init__(self, token: str, api_base: str = API_BASE,
                 timeout: float = 15.0) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def _url(self, metodo: str) -> str:
        return f"{self.api_base}/bot{self.token}/{metodo}"

    def _peticion(self, metodo: str, datos: dict | None = None,
                  timeout: float | None = None) -> object:
        cuerpo = urllib.parse.urlencode(datos or {}).encode()
        req = urllib.request.Request(self._url(metodo), data=cuerpo)
        try:
            with urllib.request.urlopen(
                    req, timeout=timeout or self.timeout) as resp:
                salida = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                salida = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise TelegramError(f"HTTP {e.code} en {metodo}") from e
        except Exception as e:
            raise TelegramError(f"red: {e}") from e
        if not salida.get("ok"):
            raise TelegramError(str(salida.get("description", "error")))
        return salida.get("result")

    def _multipart(self, metodo: str, campos: dict, nombre: str,
                   contenido: bytes, tipo: str,
                   timeout: float | None = None) -> object:
        frontera = f"----vigia{uuid.uuid4().hex}"
        partes: list[bytes] = []
        for clave, valor in campos.items():
            partes.append(
                f"--{frontera}\r\nContent-Disposition: form-data; "
                f'name="{clave}"\r\n\r\n{valor}\r\n'.encode("utf-8"))
        partes.append(
            f"--{frontera}\r\nContent-Disposition: form-data; "
            f'name="{nombre}"; filename="{nombre}"\r\n'
            f"Content-Type: {tipo}\r\n\r\n".encode("utf-8"))
        partes.append(contenido)
        partes.append(f"\r\n--{frontera}--\r\n".encode("utf-8"))
        cuerpo = b"".join(partes)
        req = urllib.request.Request(
            self._url(metodo), data=cuerpo,
            headers={"Content-Type":
                     f"multipart/form-data; boundary={frontera}"})
        try:
            with urllib.request.urlopen(
                    req, timeout=timeout or self.timeout * 4) as resp:
                salida = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                salida = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise TelegramError(f"HTTP {e.code} en {metodo}") from e
        except Exception as e:
            raise TelegramError(f"red: {e}") from e
        if not salida.get("ok"):
            raise TelegramError(str(salida.get("description", "error")))
        return salida.get("result")

    # -- api --------------------------------------------------------------
    def enviar_texto(self, chat_id, texto: str) -> object:
        return self._peticion("sendMessage", {
            "chat_id": str(chat_id), "text": texto,
            "disable_web_page_preview": "true"})

    def enviar_foto(self, chat_id, jpeg: bytes, caption: str = "") -> object:
        campos = {"chat_id": str(chat_id)}
        if caption:
            campos["caption"] = caption[:1024]
        return self._multipart("sendPhoto", campos, "foto.jpg", jpeg,
                               "image/jpeg")

    def enviar_video(self, chat_id, ruta: str | Path, caption: str = "") -> object:
        ruta = Path(ruta)
        contenido = ruta.read_bytes()
        campos = {"chat_id": str(chat_id)}
        if caption:
            campos["caption"] = caption[:1024]
        tipo = "video/webm" if ruta.suffix.lower() == ".webm" else "video/mp4"
        return self._multipart("sendVideo", campos, ruta.name, contenido, tipo,
                               timeout=90)

    def actualizar(self, offset: int = 0, timeout: int = 25) -> list:
        datos = {"timeout": str(timeout)}
        if offset:
            datos["offset"] = str(offset)
        salida = self._peticion("getUpdates", datos,
                                timeout=timeout + 10)
        return salida or []


class NotificadorTelegram:
    """Cola de avisos, anti-spam y comandos del bot."""

    def __init__(self, cliente, chat_id, config: dict | None = None,
                 reloj=time.time) -> None:
        cfg = config or {}
        self.cliente = cliente
        self.chat_id = str(chat_id)
        self.reloj = reloj
        self.prioridades = dict(cfg.get("telegram_prioridades") or {})
        self.max_hora = max(0, int(cfg.get("telegram_max_hora", 20) or 0))
        self.cooldown_seg = float(cfg.get("telegram_cooldown_seg", 300) or 0)
        self.espera_clip_seg = float(cfg.get("telegram_espera_clip_seg", 30))
        self._cola: queue.Queue = queue.Queue(maxsize=200)
        self._lock = threading.Lock()
        self._enviados: list[float] = []
        self._ultimo_por_clave: dict[tuple[str, str], float] = {}
        self._omitidos = 0
        self._ultimo_error: str | None = None
        self._ultimo_envio: str | None = None
        self._activo = False
        self._hilo_envios: threading.Thread | None = None
        self._hilo_bot: threading.Thread | None = None

    # -- prioridad y anti-spam -------------------------------------------
    def prioridad(self, tipo: str, armado: bool = False) -> str | None:
        base = self.prioridades.get(tipo)
        if tipo in ("cara_desconocida", "persona_desconocida"):
            return "critico" if armado else "aviso"
        if tipo == "zona_restringida" and armado:
            return "critico"
        return base

    def _permitir(self, tipo: str, clave: str, ahora: float) -> str | None:
        """Texto a anadir al aviso o None si toca silenciarlo."""
        with self._lock:
            self._enviados = [t for t in self._enviados
                              if ahora - t < 3600]
            if self.max_hora and len(self._enviados) >= self.max_hora:
                self._omitidos += 1
                return None
            llave = (tipo, clave or "")
            ultimo = self._ultimo_por_clave.get(llave, -1e18)
            if self.cooldown_seg and ahora - ultimo < self.cooldown_seg:
                self._omitidos += 1
                return None
            self._ultimo_por_clave[llave] = ahora
            self._enviados.append(ahora)
            omitidos, self._omitidos = self._omitidos, 0
        if omitidos:
            return f"(+{omitidos} eventos más)"
        return ""

    def evento(self, tipo: str, detalle: str = "", frame=None,
               clip: str | Path | None = None, clave: str = "",
               armado: bool = False) -> bool:
        """Encola un evento si su prioridad y el anti-spam lo permiten."""
        prioridad = self.prioridad(tipo, armado)
        if prioridad not in ("aviso", "critico"):
            return False
        ahora = self.reloj()
        extra = self._permitir(tipo, clave, ahora)
        if extra is None:
            return False
        texto = f"[{prioridad.upper()}] {tipo}"
        if detalle:
            texto += f"\n{detalle}"
        if extra:
            texto += f"\n{extra}"
        jpeg = _jpeg(frame)
        self._encolar({"texto": texto, "jpeg": jpeg,
                       "clip": str(clip) if clip else None,
                       "tipo": tipo})
        return True

    def aviso_sistema(self, texto: str) -> bool:
        return self._encolar({"texto": f"[SISTEMA] {texto}", "jpeg": None,
                              "clip": None, "tipo": "sistema"})

    def _encolar(self, item: dict) -> bool:
        try:
            self._cola.put_nowait(item)
            return True
        except queue.Full:
            log.warning("cola de Telegram llena: aviso descartado")
            return False

    # -- envio ------------------------------------------------------------
    def _worker(self) -> None:
        while self._activo or not self._cola.empty():
            try:
                item = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._procesar(item)
            except Exception as e:
                log.warning("fallo enviando a Telegram: %s", e)
                self._ultimo_error = str(e)
            finally:
                self._cola.task_done()

    def _procesar(self, item: dict) -> None:
        texto = item.get("texto", "")
        jpeg = item.get("jpeg")
        if jpeg:
            self.cliente.enviar_foto(self.chat_id, jpeg, caption=texto)
        else:
            self.cliente.enviar_texto(self.chat_id, texto)
        self._ultimo_envio = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clip = item.get("clip")
        if clip:
            ruta = Path(clip)
            limite = time.monotonic() + self.espera_clip_seg
            while not ruta.is_file() and time.monotonic() < limite:
                time.sleep(0.25)
            if ruta.is_file():
                self.cliente.enviar_video(
                    self.chat_id, ruta, caption=f"{item.get('tipo', 'clip')} "
                    "(clip)")
            else:
                log.warning("clip no disponible para Telegram: %s", ruta)

    def enviar_prueba(self) -> dict:
        """Envio sincrono (lo pide el panel); devuelve el resultado."""
        try:
            self.cliente.enviar_texto(
                self.chat_id, "Vigia: prueba de Telegram correcta.")
            self._ultimo_envio = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S")
            self._ultimo_error = None
            return {"ok": True}
        except Exception as e:
            self._ultimo_error = str(e)
            return {"ok": False, "error": str(e)}

    def estado(self) -> dict:
        with self._lock:
            pendientes = self._cola.qsize()
        return {
            "configurado": True,
            "ultimo_error": self._ultimo_error,
            "ultimo_envio": self._ultimo_envio,
            "pendientes": pendientes,
        }

    # -- bot --------------------------------------------------------------
    def iniciar(self, manejador=None) -> None:
        if self._activo:
            return
        self._activo = True
        self._hilo_envios = threading.Thread(
            target=self._worker, daemon=True, name="telegram-envios")
        self._hilo_envios.start()
        self._hilo_bot = threading.Thread(
            target=self._bucle_bot, args=(manejador,), daemon=True,
            name="telegram-bot")
        self._hilo_bot.start()

    def parar(self) -> None:
        self._activo = False

    def _bucle_bot(self, manejador) -> None:
        offset = 0
        while self._activo:
            try:
                updates = self.cliente.actualizar(offset, timeout=25)
            except Exception as e:
                self._ultimo_error = str(e)
                log.debug("getUpdates fallo: %s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                mensaje = (update.get("message")
                           or update.get("edited_message") or {})
                chat = str((mensaje.get("chat") or {}).get("id", ""))
                if chat != self.chat_id:
                    continue  # solo el chat autorizado
                texto = str(mensaje.get("text") or "").strip()
                if not texto:
                    continue
                try:
                    respuesta = self.manejar_comando(texto, manejador)
                except Exception as e:
                    log.exception("error en comando %r", texto)
                    respuesta = ("error", f"fallo: {e}")
                self._responder(respuesta)
            if not updates:
                time.sleep(1)

    def _responder(self, respuesta) -> None:
        jpeg = None
        if isinstance(respuesta, tuple):
            texto, jpeg = (respuesta + (None,))[:2]
        else:
            texto = respuesta
        if not texto:
            return
        try:
            if jpeg:
                self.cliente.enviar_foto(self.chat_id, jpeg, caption=str(texto))
            else:
                self.cliente.enviar_texto(self.chat_id, str(texto))
        except Exception as e:
            self._ultimo_error = str(e)

    def manejar_comando(self, texto: str, manejador=None):
        comando = texto.strip().split()[0].lower() if texto.strip() else ""
        if comando not in ("/armar", "/desarmar", "/estado", "/foto"):
            return "Comandos: /armar /desarmar /estado /foto"
        if manejador is None:
            return "Vigia: comando no disponible en este momento."
        return manejador(comando)


def _jpeg(frame) -> bytes | None:
    if frame is None:
        return None
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", frame,
                               [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def crear_notificador(config: dict, token: str | None = None,
                      chat_id: str | None = None):
    """Notificador si Telegram esta activo y hay secretos; si no, None."""
    from .seguridad import cargar_env
    cargar_env()
    token = (token or os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not config.get("telegram") or not token or not chat_id:
        return None
    return NotificadorTelegram(ClienteTelegram(token), chat_id, config)


def estado_config(config: dict | None = None) -> dict:
    """Estado sin secretos, para `GET /telegram/estado`."""
    from .seguridad import cargar_env
    cargar_env()
    tiene_token = bool((os.environ.get("TELEGRAM_TOKEN") or "").strip())
    tiene_chat = bool((os.environ.get("TELEGRAM_CHAT_ID") or "").strip())
    activo = bool((config or {}).get("telegram"))
    return {
        "configurado": activo and tiene_token and tiene_chat,
        "activo": activo,
        "token_configurado": tiene_token,
        "chat_configurado": tiene_chat,
        "ultimo_error": None if (activo and tiene_token and tiene_chat)
        else _motivo(activo, tiene_token, tiene_chat),
        "ultimo_envio": None,
        "pendientes": 0,
    }


def _motivo(activo: bool, token: bool, chat: bool) -> str:
    if not activo:
        return "telegram desactivado en config.yaml"
    if not token:
        return "falta TELEGRAM_TOKEN en .env"
    if not chat:
        return "falta TELEGRAM_CHAT_ID en .env"
    return ""


class VigilanteSistema:
    """Avisos del propio sistema: camara caida y poco disco."""

    def __init__(self, notificador, camara_seg: float = 300,
                 disco_gb: float = 5, reloj=time.time) -> None:
        self.notificador = notificador
        self.camara_seg = float(camara_seg)
        self.disco_gb = float(disco_gb)
        self.reloj = reloj
        self._caida_desde: float | None = None
        self._camara_avisada = False
        self._disco_avisado = False

    def revisar(self, camara_ok: bool, disco_libre_gb: float | None) -> list[str]:
        avisos: list[str] = []
        ahora = self.reloj()
        if camara_ok:
            self._caida_desde = None
            self._camara_avisada = False
        else:
            if self._caida_desde is None:
                self._caida_desde = ahora
            elif (not self._camara_avisada
                  and ahora - self._caida_desde >= self.camara_seg):
                self._camara_avisada = True
                avisos.append("camara caida mas de "
                              f"{int(self.camara_seg // 60)} min")
        if disco_libre_gb is not None:
            if disco_libre_gb < self.disco_gb and not self._disco_avisado:
                self._disco_avisado = True
                avisos.append(f"disco bajo: {disco_libre_gb:.1f} GB libres")
            elif disco_libre_gb >= self.disco_gb * 1.2:
                self._disco_avisado = False
        for aviso in avisos:
            self.notificador.aviso_sistema(aviso)
        return avisos
