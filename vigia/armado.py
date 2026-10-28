"""Estado de armado/desarmado, persistente y con soporte de franjas.

El estado manual se guarda en SQLite (`estado_sistema`) para que un reinicio
no desarme la casa sin querer. El modo automatico usa `armado_franjas` (o las
franjas globales si no hay) y no pisa un cambio manual hasta que el usuario
vuelve a activar el automatico.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .zonas import en_franja

log = logging.getLogger(__name__)

_CLAVE_ARMADO = "armado"
_CLAVE_DESDE = "armado_desde"


class GestorArmado:
    def __init__(self, almacen=None, config: dict | None = None,
                 reloj=time.time) -> None:
        self.almacen = almacen
        self.reloj = reloj
        cfg = config or {}
        self.auto = bool(cfg.get("armado_auto", False))
        franjas = cfg.get("armado_franjas") or cfg.get("franjas") or []
        self.franjas = [(str(f["desde"]), str(f["hasta"])) for f in franjas]
        self.armado = self._leer_bool(_CLAVE_ARMADO, False)
        self.desde = self._leer(_CLAVE_DESDE)

    # -- persistencia -----------------------------------------------------
    def _leer(self, clave: str) -> str | None:
        if self.almacen is None:
            return None
        try:
            return self.almacen.leer_estado(clave)
        except Exception:
            log.exception("no se pudo leer el estado %s", clave)
            return None

    def _leer_bool(self, clave: str, defecto: bool) -> bool:
        valor = self._leer(clave)
        if valor is None:
            return defecto
        return str(valor).strip().lower() in ("1", "true", "si", "sí", "on")

    def _guardar(self, clave: str, valor: str | None) -> None:
        if self.almacen is None:
            return
        try:
            self.almacen.guardar_estado(clave, valor)
        except Exception:
            log.exception("no se pudo guardar el estado %s", clave)

    # -- api --------------------------------------------------------------
    def _hora(self, ahora: float | None = None) -> str:
        return datetime.fromtimestamp(
            self.reloj() if ahora is None else ahora).strftime("%H:%M")

    def _cambiar(self, armado: bool) -> None:
        if armado == self.armado:
            return
        self.armado = armado
        self.desde = datetime.fromtimestamp(self.reloj()).strftime(
            "%Y-%m-%d %H:%M:%S")
        self._guardar(_CLAVE_ARMADO, "true" if armado else "false")
        self._guardar(_CLAVE_DESDE, self.desde)
        log.info("sistema %s", "ARMADO" if armado else "desarmado")

    def armar(self) -> None:
        self._cambiar(True)

    def desarmar(self) -> None:
        self._cambiar(False)

    def alternar(self) -> bool:
        self._cambiar(not self.armado)
        return self.armado

    def configurar_auto(self, activo: bool) -> None:
        self.auto = bool(activo)
        if self.auto:
            self.actualizar()

    def actualizar(self, ahora: float | None = None) -> bool:
        """Aplica el armado automatico. True si cambio el estado."""
        if not self.auto or not self.franjas:
            return False
        antes = self.armado
        self._cambiar(en_franja(self.franjas, self._hora(ahora)))
        return self.armado != antes

    def estado(self) -> dict:
        return {
            "armado": bool(self.armado),
            "auto": bool(self.auto),
            "desde": self.desde,
            "franjas": [{"desde": d, "hasta": h} for d, h in self.franjas],
        }
