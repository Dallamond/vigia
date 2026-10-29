"""Motor de eventos: convierte detecciones/tracking en eventos con reglas.

Reglas soportadas (config dict):
- lineas de cruce (tripwire) por el centro-pie de cada persona
- permanencia por persona y **por zona** (varias zonas a la vez), con umbral
  propio y re-aviso
- entrada/salida de zona (`zona_entra` / `zona_sale` con `duracion_seg`)
- conocidos: no disparan permanencia (o con otro umbral)
- zonas en caja o poligono, con franjas, `restringida`, `excluir` y objetos
  vigilados propios; sus reglas funcionan aunque estes fuera de la franja
  global
- mas de N personas (global o por zona) y aforo actual persistido
- merodeo: K re-entradas de la misma persona en una ventana
- persona inmovil durante X segundos (posible caida)
- objetos vigilados: aparecen/desaparecen
- franjas horarias globales (solo afectan a las reglas globales)

Todo evento se persiste en el Almacen (SQLite) y se devuelve para que la web
le enlace miniatura/clip.
"""
from __future__ import annotations

import logging
import time as _time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable

from .almacen import Almacen
from .detector import Deteccion
from .lineas import GestorLineas, normalizar_lineas
from .zonas import Zona, normalizar_zonas

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evento:
    tipo: str
    detalle: str | None = None
    id: int | None = None  # id en SQLite, para enlazarle miniatura/clip
    id_tracking: int | None = None  # persona implicada (permanencia)
    zona: str | None = None  # zona implicada (si la hay)
    camara_id: str | None = None  # multicamara ("0" = unica clasica)
    duracion_seg: float | None = None  # solo en `zona_sale`


def _cajas_solapan(caja_a, caja_b) -> bool:
    ax1, ay1, ax2, ay2 = caja_a
    bx1, by1, bx2, by2 = caja_b
    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)


def _centro(caja) -> tuple[float, float]:
    return (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2


class _PermanenciaEstado:
    __slots__ = ("desde", "zona", "anunciada", "ultima_vez", "ultimo_aviso",
                 "entrada_anunciada")

    def __init__(self, t, zona: str | None = None):
        self.desde = t
        self.zona = zona
        self.anunciada = False
        self.ultima_vez = t
        self.ultimo_aviso = t
        self.entrada_anunciada = False


class _Movimiento:
    __slots__ = ("x", "y", "desde", "anunciado", "ultima_vez")

    def __init__(self, x, y, t):
        self.x = x
        self.y = y
        self.desde = t
        self.anunciado = False
        self.ultima_vez = t


class MotorEventos:
    def __init__(self, almacen: Almacen, config: dict | None = None,
                 reloj: Callable[[], float] = _time.time,
                 camara_id: str | int | None = None):
        cfg = config or {}
        self.almacen = almacen
        self.reloj = reloj
        self.camara_id = str(
            camara_id if camara_id is not None else cfg.get("camara_id", "0"))
        self.permanencia_seg = float(cfg.get("permanencia_seg", 30))
        self.permanencia_conocidos_seg = float(
            cfg.get("permanencia_conocidos_seg", 0) or 0)
        self.reaviso_seg = float(cfg.get("reaviso_min", 0) or 0) * 60.0
        self.vigilar: list[str] = list(cfg.get("vigilar_desaparicion", []))
        self.timeout_objeto_seg = float(cfg.get("timeout_objeto_seg", 10))
        # gracia para no resetear la permanencia si ByteTrack pierde el ID
        # durante un frame suelto (mismo margen que persona_salio)
        self.timeout_persona_seg = float(cfg.get("timeout_persona_seg", 10))
        # gracia al salir de una zona sin perder de vista a la persona
        # (evita rebotes si un frame cae justo fuera del borde)
        self.timeout_zona_seg = float(cfg.get("timeout_zona_seg", 1.5) or 0)
        # anti-rebote de los eventos de zona
        self.cooldown_zona_seg = float(cfg.get("cooldown_zona_seg", 2) or 0)
        self.merodeo_entradas = max(1, int(cfg.get("merodeo_entradas", 3) or 3))
        self.merodeo_ventana_seg = float(
            cfg.get("merodeo_ventana_seg", 120) or 120)
        self.inmovil_seg = float(cfg.get("inmovil_seg", 0) or 0)
        self.inmovil_px = float(cfg.get("inmovil_px", 20) or 20)
        self.max_personas = int(cfg.get("max_personas", 0) or 0)
        self.zonas: dict[str, Zona] = normalizar_zonas(
            cfg.get("zonas") or {}, log_errores=False)
        self.lineas = normalizar_lineas(
            cfg.get("lineas") or {}, log_errores=False)
        self.gestor_lineas = GestorLineas(self.lineas)
        self.franjas: list[tuple[str, str]] = [
            (f.get("desde", "00:00"), f.get("hasta", "23:59"))
            for f in cfg.get("franjas", [])] if cfg.get("franjas") else []
        # estado por (id_tracking, zona); zona "" = reglas globales
        self._ids: dict[tuple[int, str], _PermanenciaEstado] = {}
        self._vistos: set[int] = set()
        self._reentradas: dict[int, deque[float]] = {}
        self._merodeo_anuncio: dict[int, float] = {}
        self._restringida: set[tuple[int, str]] = set()
        self._lleno: dict[str, bool] = {}
        self._movimiento: dict[int, _Movimiento] = {}
        self._objetos_vistos: dict[str, float] = {}
        self._objeto_ausente: dict[str, bool] = {}
        self._cooldowns: dict[tuple[str, int, str], float] = {}
        # aforo actual calculado (se guarda para /resumen y /estado)
        self.aforo = 0
        self.aforo_zonas: dict[str, int] = {}
        self._aforo_guardado: tuple[int, str] | None = None

    # -- franja horaria ---------------------------------------------------
    def _hora_actual(self) -> str:
        return datetime.now().strftime("%H:%M")

    def _en_franja(self, hora: str) -> bool:
        if not self.franjas:
            return True
        from .zonas import en_franja
        return en_franja(self.franjas, hora)

    def fuera_de_franja(self) -> bool:
        """True si AHORA (hora del sistema) esta fuera de las franjas activas."""
        return not self._en_franja(self._hora_actual())

    # -- zonas ------------------------------------------------------------
    def _zonas_de(self, caja) -> tuple[str, ...]:
        """Todas las zonas (no de exclusion) en las que cae la caja."""
        return tuple(nombre for nombre, zona in self.zonas.items()
                     if not zona.excluir and zona.contiene(caja))

    def _zona_de(self, caja) -> str | None:
        """Primera zona (compatibilidad con el comportamiento anterior)."""
        zonas = self._zonas_de(caja)
        return zonas[0] if zonas else None

    def _excluida(self, caja) -> bool:
        return any(z.excluir and z.contiene(caja) for z in self.zonas.values())

    def zonas_dibujo(self) -> dict[str, tuple[int, int, int, int]]:
        """Solo las cajas (para pintar sobre el video); los poligonos no."""
        return {n: z.caja for n, z in self.zonas.items() if z.caja is not None}

    def zonas_dict(self) -> dict[str, dict]:
        """Formato de la API `/zonas` (objetos)."""
        return {n: z.a_dict() for n, z in self.zonas.items()}

    def lineas_dict(self) -> dict[str, dict]:
        return self.gestor_lineas.a_dict()

    def _reiniciar_estado(self) -> None:
        """Olvida presencias y objetos: se llama al estar fuera de franja."""
        self._ids.clear()
        self._objetos_vistos.clear()
        self._objeto_ausente.clear()
        self._restringida.clear()
        self._lleno.clear()
        self._movimiento.clear()
        self._cooldowns.clear()

    def _permitir_zona(self, tipo: str, pid: int, zona: str,
                       ahora: float) -> bool:
        """Cooldown por (tipo, persona, zona) para no duplicar rebotes."""
        if self.cooldown_zona_seg <= 0:
            return True
        clave = (tipo, pid, zona)
        ultimo = self._cooldowns.get(clave, -1e18)
        if ahora - ultimo < self.cooldown_zona_seg:
            return False
        self._cooldowns[clave] = ahora
        return True

    # -- api ---------------------------------------------------------------
    def procesar(self, dets: Iterable[Deteccion],
                 ids_persona: dict[int, tuple] | None = None,
                 objetos_extra: Iterable[Deteccion] | None = None,
                 identidades: dict | None = None
                 ) -> list[Evento]:
        """Inspeccion de un instante: genera, persiste y devuelve eventos.

        - dets: detecciones de persona con caja
        - ids_persona: {id_tracking: caja}
        - objetos_extra: detecciones de objetos (no persona)
        - identidades: {id_tracking: (nombre|None, similitud)} del modulo de caras
        """
        hora = self._hora_actual()
        en_global = self._en_franja(hora)
        if not en_global:
            # sin franja global siguen valiendo las reglas de zona; solo se
            # olvida el estado global para que no salten fantasmas al reentrar
            self._olvidar_global()
        eventos: list[Evento] = []
        ahora = self.reloj()
        dets_list = list(dets) if not isinstance(dets, Deteccion) else [dets]

        # permanencia por id de tracking
        # sin IDs de tracking se derivan IDs sinteticos de las detecciones
        if not ids_persona:
            personas = [d for d in dets_list
                        if getattr(d, "es_persona", False)]
            ids_persona = {-(i + 1): d.caja for i, d in enumerate(personas)}
        ids_persona = ids_persona or {}

        # lineas de cruce (centro-pie, con histeresis y anti-rebote)
        for cruce in self.gestor_lineas.procesar(ids_persona, ahora):
            eventos.append(Evento(
                "cruce_linea",
                f"linea={cruce['linea']} {cruce['direccion']}",
                id_tracking=cruce["id_tracking"],
                camara_id=self.camara_id))

        visibles: dict[int, tuple[str, ...]] = {}
        for pid, caja in ids_persona.items():
            if self._excluida(caja):
                continue  # zona de exclusion: como si no estuviera
            visibles[pid] = self._zonas_de(caja)
        self._permanencia(visibles, identidades, ahora, eventos, en_global)
        self._restringidas(visibles, hora, ahora, eventos)
        self._aforo(visibles, ahora, eventos, en_global)
        if en_global:
            self._inmovilidad(ids_persona, visibles, ahora, eventos)

        self._limpiar_ausentes(visibles, ahora, eventos, en_global)
        self._objetos(objetos_extra, ahora, eventos, en_global)
        self._actualizar_aforo(visibles)

        # persistir (devuelve el id para poder enlazar miniatura/video)
        guardados: list[Evento] = []
        for ev in eventos:
            ev_id = self.almacen.guardar(
                ev.tipo, id_tracking=ev.id_tracking, detalle=ev.detalle,
                zona=ev.zona, camara_id=ev.camara_id or self.camara_id,
                duracion_seg=ev.duracion_seg)
            guardados.append(replace(ev, id=ev_id,
                                     camara_id=ev.camara_id or self.camara_id))
            log.info("evento %s (%s) id=%s camara=%s", ev.tipo, ev.detalle,
                     ev_id, self.camara_id)
        return guardados

    def _olvidar_global(self) -> None:
        """Borra solo el estado de las reglas globales (no el de las zonas)."""
        for clave in [c for c in self._ids if c[1] == ""]:
            del self._ids[clave]
        self._movimiento.clear()
        self._restringida.clear()
        self._lleno.pop("__global__", None)
        # objetos globales (sin zona): fuera de franja no cuenta su ausencia
        for clave in [c for c in self._objetos_vistos if "|" not in c]:
            del self._objetos_vistos[clave]
        for clave in [c for c in self._objeto_ausente if "|" not in c]:
            del self._objeto_ausente[clave]

    def _actualizar_aforo(self, visibles: dict[int, tuple[str, ...]]) -> None:
        self.aforo = len(visibles)
        cuentas: dict[str, int] = {}
        for zonas in visibles.values():
            for zona in zonas:
                cuentas[zona] = cuentas.get(zona, 0) + 1
        self.aforo_zonas = cuentas
        firma = (self.aforo, repr(sorted(cuentas.items())))
        if firma == self._aforo_guardado:
            return
        self._aforo_guardado = firma
        try:
            self.almacen.guardar_estado("aforo_actual", str(self.aforo))
            import json
            self.almacen.guardar_estado(
                "aforo_zonas", json.dumps(cuentas, ensure_ascii=False))
        except Exception:
            log.exception("no se pudo guardar el aforo actual")

    # -- reglas ------------------------------------------------------------
    def _permanencia(self, visibles: dict[int, tuple[str, ...]],
                     identidades: dict | None, ahora: float,
                     eventos: list[Evento], en_global: bool) -> None:
        for pid, zonas in visibles.items():
            claves = [(pid, z) for z in zonas
                      if (zona := self.zonas.get(z)) is not None
                      and zona.activa(self._hora_actual())]
            if not zonas and en_global:
                claves = [(pid, "")]
            for clave in claves:
                zona_nombre = clave[1] or None
                zona = self.zonas.get(zona_nombre) if zona_nombre else None
                st = self._ids.get(clave)
                if st is None:
                    st = _PermanenciaEstado(ahora, zona_nombre)
                    self._ids[clave] = st
                    if zona_nombre and self._permitir_zona(
                            "zona_entra", pid, zona_nombre, ahora):
                        st.entrada_anunciada = True
                        eventos.append(Evento(
                            "zona_entra", f"zona={zona_nombre} id {pid}",
                            id_tracking=pid, zona=zona_nombre,
                            camara_id=self.camara_id))
                    if not zona_nombre:
                        if pid in self._vistos:
                            self._anotar_reentrada(pid, ahora, eventos)
                        self._vistos.add(pid)
                    continue
                st.ultima_vez = ahora
                if zona_nombre is not None and zona is not None \
                        and not zona.activa(self._hora_actual()):
                    continue
                if zona_nombre != st.zona:
                    st.zona = zona_nombre
                    st.desde = ahora
                    st.anunciada = False
                    st.ultimo_aviso = ahora
                    continue

                conocido = self._nombre_conocido(pid, identidades)
                if conocido is not None:
                    if zona is not None and not zona.conocidos:
                        st.anunciada = True
                        continue
                    umbral = (zona.permanencia_conocidos_seg
                              if zona and zona.permanencia_conocidos_seg is not None
                              else self.permanencia_conocidos_seg)
                    if umbral <= 0:
                        st.anunciada = True
                        continue
                else:
                    umbral = (zona.permanencia_seg
                              if zona and zona.permanencia_seg is not None
                              else self.permanencia_seg)

                telon = ahora - st.desde
                if telon < umbral:
                    continue
                if not st.anunciada:
                    detalle = (f"permanencia zona={zona_nombre}" if zona_nombre
                               else "permanencia")
                    eventos.append(Evento("permanencia", detalle,
                                          id_tracking=pid, zona=zona_nombre,
                                          camara_id=self.camara_id))
                    st.anunciada = True
                    st.ultimo_aviso = ahora
                elif (self.reaviso_seg > 0
                      and ahora - st.ultimo_aviso >= self.reaviso_seg):
                    detalle = (f"permanencia zona={zona_nombre} acumulado="
                               f"{int(telon)}s" if zona_nombre else
                               f"permanencia acumulado={int(telon)}s")
                    eventos.append(Evento("permanencia", detalle,
                                          id_tracking=pid, zona=zona_nombre,
                                          camara_id=self.camara_id))
                    st.ultimo_aviso = ahora

    def _limpiar_ausentes(self, visibles: dict[int, tuple[str, ...]],
                          ahora: float, eventos: list[Evento],
                          en_global: bool) -> None:
        for clave, st in list(self._ids.items()):
            pid, zona_nombre = clave
            if pid not in visibles:
                margen = self.timeout_persona_seg
            elif zona_nombre and zona_nombre not in visibles[pid]:
                margen = self.timeout_zona_seg
            elif not zona_nombre and visibles[pid]:
                margen = self.timeout_zona_seg
            else:
                continue
            if ahora - st.ultima_vez <= margen:
                continue
            del self._ids[clave]
            if zona_nombre and self._permitir_zona(
                    "zona_sale", pid, zona_nombre, ahora):
                duracion = max(0.0, st.ultima_vez - st.desde)
                eventos.append(Evento(
                    "zona_sale",
                    f"zona={zona_nombre} id {pid} "
                    f"duracion_seg={int(duracion)}",
                    id_tracking=pid, zona=zona_nombre,
                    camara_id=self.camara_id,
                    duracion_seg=round(duracion, 1)))

    @staticmethod
    def _nombre_conocido(pid: int, identidades: dict | None) -> str | None:
        if not identidades or pid not in identidades:
            return None
        valor = identidades[pid]
        if isinstance(valor, str):
            return valor or None
        try:
            nombre = valor[0]
        except (TypeError, IndexError):
            return None
        return nombre or None

    def _anotar_reentrada(self, pid: int, ahora: float,
                          eventos: list[Evento]) -> None:
        """Cuenta una re-entrada y dispara `merodeo` si llega al umbral."""
        marca = self._reentradas.setdefault(pid, deque())
        marca.append(ahora)
        while marca and ahora - marca[0] > self.merodeo_ventana_seg:
            marca.popleft()
        if len(marca) < self.merodeo_entradas:
            return
        ultimo = self._merodeo_anuncio.get(pid, 0.0)
        if ahora - ultimo < self.merodeo_ventana_seg:
            return
        self._merodeo_anuncio[pid] = ahora
        eventos.append(Evento(
            "merodeo", f"id {pid} reentradas={len(marca)}",
            id_tracking=pid, camara_id=self.camara_id))

    def _restringidas(self, visibles: dict[int, tuple[str, ...]], hora: str,
                      ahora: float, eventos: list[Evento]) -> None:
        violando: set[tuple[int, str]] = set()
        for pid, zonas in visibles.items():
            for zona_nombre in zonas:
                zona = self.zonas.get(zona_nombre)
                if zona is None or not zona.restringida:
                    continue
                # con franjas: se permite dentro de ellas; sin franjas: siempre
                if zona.franjas and zona.activa(hora):
                    continue
                violando.add((pid, zona_nombre))
                if (pid, zona_nombre) in self._restringida:
                    continue
                eventos.append(Evento(
                    "zona_restringida", f"zona={zona_nombre} id {pid}",
                    id_tracking=pid, zona=zona_nombre,
                    camara_id=self.camara_id))
        # solo recordamos los avisados que siguen violando: al salir y volver
        # a entrar se vuelve a avisar
        self._restringida = violando

    def _aforo(self, visibles: dict[int, tuple[str, ...]], ahora: float,
               eventos: list[Evento], en_global: bool) -> None:
        cuentas: dict[str, int] = {}
        for zonas in visibles.values():
            for zona_nombre in zonas:
                cuentas[zona_nombre] = cuentas.get(zona_nombre, 0) + 1
        for nombre, cuenta in cuentas.items():
            zona = self.zonas.get(nombre)
            if zona is None or zona.max_personas <= 0:
                continue
            lleno = cuenta > zona.max_personas
            if lleno and not self._lleno.get(nombre):
                eventos.append(Evento(
                    "demasiadas_personas",
                    f"zona={nombre} personas={cuenta} max={zona.max_personas}",
                    zona=nombre, camara_id=self.camara_id))
            self._lleno[nombre] = lleno
        if en_global and self.max_personas > 0:
            cuenta = len(visibles)
            lleno = cuenta > self.max_personas
            if lleno and not self._lleno.get("__global__"):
                eventos.append(Evento(
                    "demasiadas_personas",
                    f"personas={cuenta} max={self.max_personas}",
                    camara_id=self.camara_id))
            self._lleno["__global__"] = lleno

    def _inmovilidad(self, ids_persona: dict[int, tuple],
                     visibles: dict[int, tuple[str, ...]], ahora: float,
                     eventos: list[Evento]) -> None:
        if self.inmovil_seg <= 0:
            return
        for pid, caja in ids_persona.items():
            if pid not in visibles:
                continue
            x, y = _centro(caja)
            mov = self._movimiento.get(pid)
            if mov is None:
                self._movimiento[pid] = _Movimiento(x, y, ahora)
                continue
            mov.ultima_vez = ahora
            if abs(x - mov.x) > self.inmovil_px or abs(y - mov.y) > self.inmovil_px:
                mov.x, mov.y = x, y
                mov.desde = ahora
                mov.anunciado = False
                continue
            if (not mov.anunciado
                    and ahora - mov.desde >= self.inmovil_seg):
                mov.anunciado = True
                zona = visibles.get(pid) or (None,)
                eventos.append(Evento(
                    "persona_inmovil",
                    f"id {pid} inmovil {int(ahora - mov.desde)}s",
                    id_tracking=pid, zona=zona[0],
                    camara_id=self.camara_id))

    def _objetos(self, objetos_extra: Iterable[Deteccion] | None,
                 ahora: float, eventos: list[Evento],
                 en_global: bool = True) -> None:
        extras = [d for d in (objetos_extra or [])
                  if isinstance(d, Deteccion)]
        vistos: dict[str, tuple[float, str]] = {}
        for d in extras:
            if en_global and d.clase in self.vigilar:
                vistos[d.clase] = (ahora, d.clase)
            for zona in self.zonas.values():
                if (not zona.excluir and d.clase in zona.vigilar
                        and zona.contiene(d.caja)):
                    clave = f"{zona.nombre}|{d.clase}"
                    vistos[clave] = (ahora, f"{d.clase} zona={zona.nombre}")
        for clave, (_t, detalle) in vistos.items():
            self._objetos_vistos[clave] = ahora
            if self._objeto_ausente.get(clave):
                self._objeto_ausente[clave] = False
                eventos.append(Evento("objeto_aparece", detalle,
                                      camara_id=self.camara_id))
        for clave, visto in list(self._objetos_vistos.items()):
            if clave in vistos:
                continue
            if ahora - visto < self.timeout_objeto_seg:
                continue
            if self._objeto_ausente.get(clave):
                continue
            self._objeto_ausente[clave] = True
            detalle = (clave.split("|", 1)[1] + f" zona={clave.split('|', 1)[0]}"
                       if "|" in clave else clave)
            eventos.append(Evento("objeto_desaparece", detalle,
                                  camara_id=self.camara_id))
