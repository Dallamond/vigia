"""Lineas de cruce (tripwire) para entradas/salidas fiables por una puerta.

Una linea es un segmento `p1 -> p2` sobre el video. Cuando el **centro-pie**
de una persona (punto medio de la base de su caja) cruza el segmento se emite
`cruce_linea` con la direccion:

- lado `a` = lado izquierdo del vector `p1 -> p2` (producto vectorial > 0)
- lado `b` = lado derecho (producto vectorial < 0)
- `a→b` = paso de `a` a `b`; `b→a` = paso de `b` a `a`

`sentido` permite quedarse solo con una direccion: `ambos` (por defecto),
`a_b` (solo `a→b`) o `b_a`.

Hay histeresis (`margen_px`): el lado solo cambia cuando la persona esta a mas
de ese margen del segmento, para que una persona parada encima no genere
rebotes. Tambien hay anti-rebote (`cooldown_seg`) por (persona, linea).

Formato en `config.yaml` (el mismo que `GET/PUT /lineas`):

    lineas:
      puerta:
        p1: [100, 400]
        p2: [500, 400]
        sentido: ambos
        margen_px: 6
        cooldown_seg: 2

Tambien se acepta una lista de objetos con `nombre`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

SENTIDOS = ("ambos", "a_b", "b_a")
_ARROWS = {"a→b": "a_b", "b→a": "b_a", "a->b": "a_b", "b->a": "b_a"}


def _numero(valor, defecto=None):
    if isinstance(valor, bool):
        return defecto
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


def _punto(valor, ancho: int | None, alto: int | None):
    if (not isinstance(valor, (list, tuple)) or len(valor) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in valor)):
        return None
    x, y = int(valor[0]), int(valor[1])
    if x < 0 or y < 0:
        return None
    if ancho is not None and x > int(ancho):
        return None
    if alto is not None and y > int(alto):
        return None
    return x, y


def _sentido(valor) -> str:
    texto = str(valor or "ambos").strip().lower()
    texto = _ARROWS.get(texto, texto)
    if texto not in SENTIDOS:
        raise ValueError(f"sentido invalido: {valor!r} (ambos/a_b/b_a)")
    return texto


@dataclass(frozen=True)
class Linea:
    nombre: str = ""
    p1: tuple[int, int] = (0, 0)
    p2: tuple[int, int] = (0, 0)
    sentido: str = "ambos"
    margen_px: float = 6.0
    cooldown_seg: float = 2.0

    def a_dict(self) -> dict:
        return {
            "p1": list(self.p1),
            "p2": list(self.p2),
            "sentido": self.sentido,
            "margen_px": self.margen_px,
            "cooldown_seg": self.cooldown_seg,
        }

    def lado(self, punto) -> float:
        """Producto vectorial: >0 lado a, <0 lado b."""
        x, y = punto
        x1, y1 = self.p1
        x2, y2 = self.p2
        return (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)

    def distancia(self, punto) -> float:
        """Distancia con signo en pixeles a la recta (misma convencion que lado)."""
        largo = ((self.p2[0] - self.p1[0]) ** 2
                 + (self.p2[1] - self.p1[1]) ** 2) ** 0.5
        return self.lado(punto) / largo if largo > 0 else 0.0


def normalizar_linea(nombre: str, valor, ancho: int | None = None,
                     alto: int | None = None) -> Linea:
    """Valida una linea (estricto). Lanza ValueError si no encaja."""
    nombre = str(nombre)
    if not isinstance(valor, dict):
        raise ValueError(f"linea '{nombre}' debe ser un objeto p1/p2")
    p1 = _punto(valor.get("p1"), ancho, alto)
    p2 = _punto(valor.get("p2"), ancho, alto)
    if p1 is None or p2 is None:
        limite = f" (0..{ancho}x{alto})" if ancho and alto else ""
        raise ValueError(f"linea '{nombre}': p1/p2 fuera de rango{limite}")
    if p1 == p2:
        raise ValueError(f"linea '{nombre}': p1 y p2 no pueden coincidir")
    margen = _numero(valor.get("margen_px", 6), None)
    cooldown = _numero(valor.get("cooldown_seg", 2), None)
    if margen is None or margen < 0:
        raise ValueError(f"linea '{nombre}': margen_px invalido")
    if cooldown is None or cooldown < 0:
        raise ValueError(f"linea '{nombre}': cooldown_seg invalido")
    return Linea(nombre=nombre, p1=p1, p2=p2,
                 sentido=_sentido(valor.get("sentido")),
                 margen_px=margen, cooldown_seg=cooldown)


def normalizar_lineas(valor, estricto: bool = False,
                      ancho: int | None = None, alto: int | None = None,
                      log_errores: bool = True) -> dict[str, Linea]:
    """Mapa nombre -> Linea. Acepta dict o lista con `nombre`."""
    if valor is None:
        return {}
    if isinstance(valor, (list, tuple)):
        mapa = {}
        for elemento in valor:
            if not isinstance(elemento, dict):
                if estricto:
                    raise ValueError("cada linea debe ser un objeto")
                continue
            nombre = elemento.get("nombre")
            if not nombre:
                if estricto:
                    raise ValueError("cada linea necesita 'nombre'")
                continue
            mapa[str(nombre)] = {k: v for k, v in elemento.items()
                                 if k != "nombre"}
        valor = mapa
    if not isinstance(valor, dict):
        if estricto:
            raise ValueError("lineas debe ser un mapa nombre -> linea")
        if log_errores:
            log.warning("lineas debe ser un mapa nombre -> linea (%r); se "
                        "ignora", valor)
        return {}
    salida: dict[str, Linea] = {}
    for nombre, datos in valor.items():
        try:
            salida[str(nombre)] = normalizar_linea(
                nombre, datos, ancho=ancho, alto=alto)
        except ValueError as e:
            if estricto:
                raise
            if log_errores:
                log.warning("config: %s; se ignora", e)
    return salida


def centro_pie(caja) -> tuple[float, float]:
    """Punto de apoyo de una caja de persona: centro de la base."""
    return (float(caja[0]) + float(caja[2])) / 2.0, float(caja[3])


@dataclass
class _EstadoLinea:
    lado: int = 0            # -1, 0 (sin confirmar), +1
    ultimo_evento: float = float("-inf")  # sin eventos previos: el cooldown no aplica


class GestorLineas:
    """Detecta cruces con histeresis y anti-rebote por persona y linea."""

    def __init__(self, lineas: dict[str, Linea] | None = None,
                 reloj=None):
        self.lineas: dict[str, Linea] = dict(lineas or {})
        self._estado: dict[tuple[int, str], _EstadoLinea] = {}
        self._ultimo: dict[int, tuple[float, float]] = {}
        self._reloj = reloj

    def actualizar(self, lineas: dict[str, Linea]) -> None:
        """Cambia las lineas en caliente (el estado de las que siguen vale)."""
        self.lineas = dict(lineas or {})
        for clave in [c for c in self._estado if c[1] not in self.lineas]:
            del self._estado[clave]

    def procesar(self, ids_persona: dict[int, tuple],
                 ahora: float | None = None) -> list[dict]:
        """Devuelve `[{linea, direccion, id_tracking}]` de los cruces nuevos."""
        if not self.lineas or not ids_persona:
            return []
        if ahora is None:
            import time
            ahora = self._reloj() if self._reloj is not None else time.time()
        cruces: list[dict] = []
        vistos: set[int] = set()
        for pid, caja in ids_persona.items():
            vistos.add(pid)
            punto = centro_pie(caja)
            self._ultimo[pid] = punto
            for nombre, linea in self.lineas.items():
                clave = (pid, nombre)
                estado = self._estado.get(clave)
                if estado is None:
                    estado = self._estado[clave] = _EstadoLinea()
                valor = linea.distancia(punto)
                if abs(valor) < linea.margen_px:
                    continue  # zona muerta: no cambia de lado
                lado = 1 if valor > 0 else -1
                anterior, estado.lado = estado.lado, lado
                if anterior == 0 or anterior == lado:
                    continue
                direccion = "a→b" if lado < 0 else "b→a"
                if linea.sentido == "a_b" and direccion != "a→b":
                    continue
                if linea.sentido == "b_a" and direccion != "b→a":
                    continue
                if (linea.cooldown_seg > 0
                        and ahora - estado.ultimo_evento
                        < linea.cooldown_seg):
                    continue
                estado.ultimo_evento = ahora
                cruces.append({"linea": nombre, "direccion": direccion,
                               "id_tracking": pid})
        # olvidar a quien ya no se ve (evita crecer sin limite)
        for pid in [p for p in set(k[0] for k in self._estado)
                    if p not in vistos]:
            for clave in [c for c in self._estado if c[0] == pid]:
                del self._estado[clave]
            self._ultimo.pop(pid, None)
        return cruces

    def a_dict(self) -> dict[str, dict]:
        return {n: l.a_dict() for n, l in self.lineas.items()}

    def dibujo(self) -> list[dict]:
        """Lineas serializables para que la GUI las pinte sobre el video."""
        return [{"nombre": n, **l.a_dict()} for n, l in self.lineas.items()]
