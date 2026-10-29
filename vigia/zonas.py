"""Zonas de vigilancia: cajas o poligonos, con sus reglas.

Formato nuevo (lo que devuelve y acepta `GET/PUT /zonas`):

    nombre:
      caja: [x1, y1, x2, y2]      # o bien `poligono: [[x, y], ...]`
      permanencia_seg: 30         # null/ausente = umbral global
      permanencia_conocidos_seg: 0  # 0 = los conocidos no disparan
      franjas: [{desde: "22:00", hasta: "08:00"}]
      restringida: false          # avisa si hay alguien fuera de sus franjas
      excluir: false              # ignora lo que caiga aqui (poster, espejo)
      max_personas: 0             # 0 = sin limite
      vigilar: [laptop]           # objetos vigilados solo en esta zona
      conocidos: true             # si los conocidos cuentan para permanencia

El formato antiguo `nombre: [x1, y1, x2, y2]` se sigue aceptando.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

log = logging.getLogger(__name__)

PRIORIDADES = ("info", "aviso", "critico")


def _numero(valor, defecto=None):
    if isinstance(valor, bool):
        return defecto
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


def _caja(valor, ancho: int | None = None,
          alto: int | None = None) -> tuple[int, int, int, int] | None:
    if not isinstance(valor, (list, tuple)) or len(valor) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in valor):
        return None
    caja = tuple(int(v) for v in valor)
    if any(v < 0 for v in caja):
        return None
    if ancho is not None and (caja[0] > ancho or caja[2] > ancho):
        return None
    if alto is not None and (caja[1] > alto or caja[3] > alto):
        return None
    return caja


def _poligono(valor, ancho: int | None = None,
              alto: int | None = None) -> tuple[tuple[int, int], ...] | None:
    if not isinstance(valor, (list, tuple)) or len(valor) < 3:
        return None
    puntos = []
    for punto in valor:
        if (not isinstance(punto, (list, tuple)) or len(punto) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in punto)):
            return None
        x, y = int(punto[0]), int(punto[1])
        if x < 0 or y < 0:
            return None
        if ancho is not None and x > ancho:
            return None
        if alto is not None and y > alto:
            return None
        puntos.append((x, y))
    return tuple(puntos)


def en_franja(franjas, hora: str) -> bool:
    """True si `hora` (HH:MM) cae en alguna franja; sin franjas, siempre."""
    if not franjas:
        return True
    try:
        h, m = (int(x) for x in hora.split(":")[:2])
    except (TypeError, ValueError):
        return True
    minutos = h * 60 + m
    for desde, hasta in franjas:
        d = int(desde.split(":")[0]) * 60 + int(desde.split(":")[1])
        hh = int(hasta.split(":")[0]) * 60 + int(hasta.split(":")[1])
        if d <= hh:
            if d <= minutos <= hh:
                return True
        elif minutos >= d or minutos <= hh:  # franja nocturna 22:00-08:00
            return True
    return False


def punto_en_poligono(punto, poligono) -> bool:
    """Ray casting: punto (x, y) dentro de un poligono [(x, y), ...]."""
    x, y = punto
    dentro = False
    n = len(poligono)
    for i in range(n):
        x1, y1 = poligono[i]
        x2, y2 = poligono[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            corte = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < corte:
                dentro = not dentro
    return dentro


def _cajas_solapan(a, b) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)


def _centro(caja) -> tuple[float, float]:
    return (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2


@dataclass(frozen=True)
class Zona:
    nombre: str = ""
    caja: tuple[int, int, int, int] | None = None
    poligono: tuple[tuple[int, int], ...] | None = None
    permanencia_seg: float | None = None
    permanencia_conocidos_seg: float | None = None
    franjas: tuple[tuple[str, str], ...] = ()
    restringida: bool = False
    excluir: bool = False
    max_personas: int = 0
    vigilar: tuple[str, ...] = ()
    conocidos: bool = True

    def contiene(self, caja) -> bool:
        """Una caja de persona/objeto cae dentro de la zona."""
        if self.poligono is not None:
            return punto_en_poligono(_centro(caja), self.poligono)
        if self.caja is not None:
            return _cajas_solapan(caja, self.caja)
        return False

    def activa(self, hora: str) -> bool:
        return en_franja(self.franjas, hora)

    def con_nombre(self, nombre: str) -> "Zona":
        return replace(self, nombre=str(nombre))

    def a_dict(self) -> dict:
        """Formato estable para la API/GUI y para config.yaml."""
        datos: dict = {
            "permanencia_seg": self.permanencia_seg,
            "permanencia_conocidos_seg": self.permanencia_conocidos_seg,
            "franjas": [{"desde": d, "hasta": h} for d, h in self.franjas],
            "restringida": bool(self.restringida),
            "excluir": bool(self.excluir),
            "max_personas": int(self.max_personas),
            "vigilar": list(self.vigilar),
            "conocidos": bool(self.conocidos),
        }
        if self.poligono is not None:
            datos["poligono"] = [list(p) for p in self.poligono]
        else:
            datos["caja"] = list(self.caja or (0, 0, 0, 0))
        return datos


def _franjas(valor):
    if valor is None:
        return ()
    if not isinstance(valor, (list, tuple)):
        raise ValueError("franjas debe ser una lista")
    salida = []
    for franja in valor:
        if not isinstance(franja, dict):
            raise ValueError("franja debe ser un mapa desde/hasta")
        desde, hasta = str(franja.get("desde", "")), str(franja.get("hasta", ""))
        for texto in (desde, hasta):
            partes = texto.split(":")
            if len(partes) != 2 or len(partes[0]) != 2 or len(partes[1]) != 2:
                raise ValueError(f"hora invalida en franja: {franja!r}")
            h, m = int(partes[0]), int(partes[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError(f"hora invalida en franja: {franja!r}")
        salida.append((desde, hasta))
    return tuple(salida)


def _bool(valor, defecto: bool, clave: str) -> bool:
    if valor is None:
        return defecto
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    raise ValueError(f"{clave} debe ser booleano")


def normalizar_zona(nombre: str, valor, ancho: int | None = None,
                    alto: int | None = None) -> Zona:
    """Convierte el formato antiguo o el nuevo en una `Zona` (estricto).

    Si se pasan `ancho`/`alto` valida que las coordenadas caigan dentro de la
    resolucion del video (lo usa `PUT /zonas`; la carga de config es laxa).
    """
    nombre = str(nombre)
    if isinstance(valor, (list, tuple)):
        caja = _caja(valor, ancho, alto)
        if caja is None:
            raise ValueError(f"zona '{nombre}' no es 4 numeros validos")
        return Zona(nombre=nombre, caja=caja)
    if not isinstance(valor, dict):
        raise ValueError(f"zona '{nombre}' debe ser una caja o un objeto")
    poligono = _poligono(valor.get("poligono"), ancho, alto)
    caja = _caja(valor.get("caja"), ancho, alto)
    if poligono is None and caja is None:
        raise ValueError(f"zona '{nombre}' necesita 'caja' o 'poligono' "
                         f"valido dentro del video")
    if poligono is not None and caja is not None:
        raise ValueError(f"zona '{nombre}': usa 'caja' o 'poligono', no ambos")
    permanencia = valor.get("permanencia_seg")
    if permanencia is not None:
        permanencia = _numero(permanencia)
        if permanencia is None or permanencia < 0:
            raise ValueError(f"zona '{nombre}': permanencia_seg invalida")
    p_conocidos = valor.get("permanencia_conocidos_seg")
    if p_conocidos is not None:
        p_conocidos = _numero(p_conocidos)
        if p_conocidos is None or p_conocidos < 0:
            raise ValueError(
                f"zona '{nombre}': permanencia_conocidos_seg invalida")
    max_personas = valor.get("max_personas", 0)
    if max_personas is not None and max_personas != "":
        max_personas = _numero(max_personas)
        if max_personas is None or max_personas < 0:
            raise ValueError(f"zona '{nombre}': max_personas invalido")
        max_personas = int(max_personas)
    else:
        max_personas = 0
    vigilar = valor.get("vigilar") or []
    if not isinstance(vigilar, (list, tuple)) or not all(
            isinstance(v, str) for v in vigilar):
        raise ValueError(f"zona '{nombre}': vigilar debe ser lista de clases")
    return Zona(
        nombre=nombre,
        caja=caja,
        poligono=poligono,
        permanencia_seg=permanencia,
        permanencia_conocidos_seg=p_conocidos,
        franjas=_franjas(valor.get("franjas")),
        restringida=_bool(valor.get("restringida"), False, "restringida"),
        excluir=_bool(valor.get("excluir"), False, "excluir"),
        max_personas=max_personas,
        vigilar=tuple(vigilar),
        conocidos=_bool(valor.get("conocidos"), True, "conocidos"),
    )


def normalizar_zonas(valor, estricto: bool = False,
                     log_errores: bool = True,
                     ancho: int | None = None,
                     alto: int | None = None) -> dict[str, Zona]:
    """Dict nombre -> Zona. En modo no estricto descarta las mal formadas."""
    if not isinstance(valor, dict):
        if estricto:
            raise ValueError("zonas debe ser un mapa nombre -> zona")
        if log_errores:
            log.warning("zonas debe ser un mapa nombre -> zona (%r); se ignora",
                        valor)
        return {}
    zonas: dict[str, Zona] = {}
    for nombre, datos in valor.items():
        try:
            zonas[str(nombre)] = normalizar_zona(
                nombre, datos, ancho=ancho, alto=alto)
        except ValueError as e:
            if estricto:
                raise
            if log_errores:
                log.warning("config: %s; se ignora", e)
    return zonas
