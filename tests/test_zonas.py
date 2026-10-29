"""Tests de zonas (caja/poligono) y reglas nuevas del motor (bloque 4)."""
import pytest
from fastapi.testclient import TestClient

import vigia.web as web
from vigia.almacen import Almacen
from vigia.detector import Deteccion
from vigia.eventos import MotorEventos
from vigia.zonas import (Zona, en_franja, normalizar_zona, normalizar_zonas,
                         punto_en_poligono)


def P(x1=0, y1=0, x2=40, y2=40):
    return Deteccion("person", 0.9, (x1, y1, x2, y2))


class Reloj:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def avanza(self, seg):
        self.t += seg


@pytest.fixture
def motor(tmp_path):
    reloj = Reloj()
    a = Almacen(tmp_path / "z.db")
    m = MotorEventos(a, config={
        "permanencia_seg": 30,
        "timeout_persona_seg": 10,
        "timeout_objeto_seg": 5,
        "zonas": {},
    }, reloj=reloj)
    m.reloj = reloj
    yield m
    a.cerrar()


@pytest.fixture
def cliente():
    return TestClient(web.app, client=("127.0.0.1", 50000))


# -- normalizacion ----------------------------------------------------------

def test_formato_antiguo_y_nuevo():
    z = normalizar_zona("puerta", [1, 2, 30, 40])
    assert z.caja == (1, 2, 30, 40) and z.poligono is None
    z = normalizar_zona("mesa", {
        "caja": [0, 0, 100, 100], "permanencia_seg": 12,
        "restringida": True, "excluir": False, "max_personas": 2,
        "vigilar": ["laptop"], "conocidos": False,
        "franjas": [{"desde": "22:00", "hasta": "08:00"}],
    })
    assert z.permanencia_seg == 12 and z.restringida
    assert z.vigilar == ("laptop",) and z.conocidos is False
    assert z.franjas == (("22:00", "08:00"),)
    d = z.a_dict()
    assert d["caja"] == [0, 0, 100, 100] and d["max_personas"] == 2


def test_poligono_y_punto_en_poligono():
    triangulo = [(0, 0), (100, 0), (0, 100)]
    assert punto_en_poligono((10, 10), triangulo)
    assert not punto_en_poligono((90, 90), triangulo)
    z = normalizar_zona("esquina", {"poligono": triangulo})
    assert z.poligono == tuple(triangulo)
    assert z.contiene((0, 0, 10, 10))  # centro dentro
    assert not z.contiene((80, 80, 100, 100))
    assert "poligono" in z.a_dict() and "caja" not in z.a_dict()


def test_zonas_mal_formadas_se_descartan_o_fallan():
    assert normalizar_zonas({"a": [0, 0, 10, 10], "b": [1, 2]}).keys() == {"a"}
    with pytest.raises(ValueError):
        normalizar_zonas({"b": [1, 2]}, estricto=True)
    with pytest.raises(ValueError):
        normalizar_zona("x", {"caja": [0, 0, 1, 1],
                              "poligono": [[0, 0], [5, 0], [0, 5]]})
    with pytest.raises(ValueError):
        normalizar_zona("x", {"caja": [0, 0, 1, 1], "restringida": "si"})
    with pytest.raises(ValueError):
        normalizar_zona("x", {"caja": [0, 0, 1, 1],
                              "franjas": [{"desde": "25:00", "hasta": "08:00"}]})


def test_franjas_nocturnas_y_diurnas():
    f = [("22:00", "08:00")]
    assert en_franja(f, "23:30") and en_franja(f, "02:00")
    assert not en_franja(f, "12:00")
    assert en_franja([("08:00", "14:00")], "12:00")
    assert en_franja([], "03:00")


# -- permanencia ------------------------------------------------------------

def test_permanencia_por_zona_usa_su_umbral(motor):
    motor.zonas = normalizar_zonas({
        "rapida": {"caja": [0, 0, 50, 50], "permanencia_seg": 5},
        "lenta": {"caja": [100, 100, 150, 150], "permanencia_seg": 100},
    })
    motor.procesar(P(), {1: (0, 0, 40, 40)})
    motor.reloj.avanza(5)
    evs = motor.procesar(P(), {1: (0, 0, 40, 40)})
    assert evs and evs[0].tipo == "permanencia" and evs[0].zona == "rapida"


def test_permanencia_reavisa_con_tiempo_acumulado(motor):
    motor.zonas = normalizar_zonas({"z": [0, 0, 100, 100]})
    motor.permanencia_seg = 5
    motor.reaviso_seg = 6
    motor.procesar(P(), {1: (0, 0, 40, 40)})
    motor.reloj.avanza(5)
    assert motor.procesar(P(), {1: (0, 0, 40, 40)})[0].tipo == "permanencia"
    motor.reloj.avanza(6)
    evs = motor.procesar(P(), {1: (0, 0, 40, 40)})
    assert len(evs) == 1 and "acumulado" in evs[0].detalle


def test_conocidos_no_disparan_o_usan_otro_umbral(motor):
    motor.zonas = normalizar_zonas({"z": [0, 0, 100, 100]})
    motor.permanencia_seg = 5
    ident = {1: ("Ana", 0.9)}
    motor.procesar(P(), {1: (0, 0, 40, 40)}, identidades=ident)
    motor.reloj.avanza(20)
    assert motor.procesar(P(), {1: (0, 0, 40, 40)},
                          identidades=ident) == []

    motor2 = MotorEventos(motor.almacen, config={
        "permanencia_seg": 5, "permanencia_conocidos_seg": 10,
        "zonas": {"z": [0, 0, 100, 100]}}, reloj=motor.reloj)
    motor2.procesar(P(), {2: (0, 0, 40, 40)},
                    identidades={2: ("Ana", 0.9)})
    motor2.reloj.avanza(10)
    assert motor2.procesar(P(), {2: (0, 0, 40, 40)},
                           identidades={2: ("Ana", 0.9)})[0].tipo == "permanencia"


def test_zona_puede_excluir_a_los_conocidos(motor):
    motor.zonas = normalizar_zonas({
        "z": {"caja": [0, 0, 100, 100], "conocidos": False}})
    motor.permanencia_seg = 5
    motor.procesar(P(), {1: (0, 0, 40, 40)},
                   identidades={1: ("Ana", 0.9)})
    motor.reloj.avanza(50)
    assert motor.procesar(P(), {1: (0, 0, 40, 40)},
                          identidades={1: ("Ana", 0.9)}) == []


# -- reglas nuevas ----------------------------------------------------------

def test_zona_restringida_fuera_de_horario_avisa_una_vez(motor):
    motor.zonas = normalizar_zonas({
        "caja_fuerte": {"caja": [0, 0, 100, 100], "restringida": True,
                        "franjas": [{"desde": "22:00", "hasta": "08:00"}]}})
    motor._hora_actual = lambda: "12:00"
    evs = motor.procesar(P(), {1: (0, 0, 40, 40)})
    assert [e.tipo for e in evs if e.tipo == "zona_restringida"] == [
        "zona_restringida"]
    assert motor.procesar(P(), {1: (0, 0, 40, 40)}) == []
    motor.reloj.avanza(20)
    motor.procesar(set(), {})  # sale
    assert motor.procesar(P(), {1: (0, 0, 40, 40)})[0].tipo == "zona_restringida"
    motor._hora_actual = lambda: "23:00"  # dentro de la franja permitida
    assert not [e for e in motor.procesar(P(), {2: (0, 0, 40, 40)})
                if e.tipo == "zona_restringida"]


def test_zona_restringida_sin_franjas_avisa_siempre(motor):
    motor.zonas = normalizar_zonas({
        "vip": {"caja": [0, 0, 100, 100], "restringida": True}})
    assert [e.tipo for e in motor.procesar(
        P(), {1: (0, 0, 40, 40)}) if e.tipo == "zona_restringida"] == [
            "zona_restringida"]


def test_demasiadas_personas_por_zona(motor):
    motor.zonas = normalizar_zonas({
        "sala": {"caja": [0, 0, 100, 100], "max_personas": 1}})
    dos = {1: (0, 0, 30, 30), 2: (50, 50, 80, 80)}
    evs = motor.procesar(P(), dos)
    assert [e.tipo for e in evs if e.tipo == "demasiadas_personas"] == [
        "demasiadas_personas"]
    lleno = [e for e in evs if e.tipo == "demasiadas_personas"]
    assert "personas=2" in lleno[0].detalle
    assert not [e for e in motor.procesar(P(), dos)
                if e.tipo == "demasiadas_personas"]  # no repite cada frame
    una = {1: (0, 0, 30, 30)}
    assert not [e for e in motor.procesar(P(), una)
                if e.tipo == "demasiadas_personas"]
    assert [e.tipo for e in motor.procesar(P(), dos)
            if e.tipo == "demasiadas_personas"] == ["demasiadas_personas"]


def test_max_personas_global(motor):
    motor.max_personas = 1
    dos = {1: (0, 0, 30, 30), 2: (200, 200, 230, 230)}
    evs = motor.procesar(P(), dos)
    assert evs[0].tipo == "demasiadas_personas" and "personas=2" in evs[0].detalle


def test_merodeo_por_reentradas(motor):
    motor.merodeo_entradas = 2
    motor.merodeo_ventana_seg = 100
    caja = {5: (0, 0, 40, 40)}
    motor.procesar(P(), caja)
    for _ in range(2):
        motor.reloj.avanza(11)
        motor.procesar(set(), {})
        motor.reloj.avanza(1)
        evs = motor.procesar(P(), caja)
    assert [e.tipo for e in evs] == ["merodeo"]
    assert "reentradas=2" in evs[0].detalle


def test_persona_inmovil_y_vuelve_a_moverse(motor):
    motor.inmovil_seg = 5
    motor.inmovil_px = 5
    motor.procesar(P(), {1: (0, 0, 40, 40)})
    motor.reloj.avanza(5)
    evs = motor.procesar(P(), {1: (0, 0, 40, 40)})
    assert [e.tipo for e in evs] == ["persona_inmovil"]
    assert motor.procesar(P(), {1: (0, 0, 40, 40)}) == []
    motor.reloj.avanza(1)
    motor.procesar(P(100, 100, 140, 140), {1: (100, 100, 140, 140)})
    motor.reloj.avanza(5)
    evs = motor.procesar(P(100, 100, 140, 140), {1: (100, 100, 140, 140)})
    assert [e.tipo for e in evs] == ["persona_inmovil"]


def test_poligono_y_zona_de_exclusion(motor):
    motor.zonas = normalizar_zonas({
        "tri": {"poligono": [[0, 0], [100, 0], [0, 100]],
                "permanencia_seg": 5},
        "espejo": {"caja": [200, 200, 300, 300], "excluir": True},
    })
    motor.procesar(P(0, 0, 20, 20), {1: (0, 0, 20, 20)})
    motor.reloj.avanza(5)
    evs = motor.procesar(P(0, 0, 20, 20), {1: (0, 0, 20, 20)})
    assert evs[0].zona == "tri"
    # dentro de la zona de exclusion no cuenta nada
    motor.procesar(P(220, 220, 260, 260), {2: (220, 220, 260, 260)})
    motor.reloj.avanza(50)
    evs = motor.procesar(P(220, 220, 260, 260), {2: (220, 220, 260, 260)})
    assert not [e for e in evs if e.tipo in (
        "permanencia", "zona_entra", "zona_restringida",
        "demasiadas_personas")]


def test_objetos_vigilados_por_zona(motor):
    motor.zonas = normalizar_zonas({
        "mesa": {"caja": [0, 0, 100, 100], "vigilar": ["laptop"]},
        "fuera": {"caja": [200, 200, 300, 300], "vigilar": ["laptop"]},
    })
    laptop = [Deteccion("laptop", 0.9, (10, 10, 40, 40))]
    motor.procesar(P(), {}, objetos_extra=laptop)
    motor.reloj.avanza(6)
    evs = motor.procesar(P(), {}, objetos_extra=[])
    assert [e.tipo for e in evs] == ["objeto_desaparece"]
    assert "laptop" in evs[0].detalle and "mesa" in evs[0].detalle
    # un objeto en la zona de fuera no crea un segundo aviso para 'mesa'
    motor.procesar(P(), {}, objetos_extra=[
        Deteccion("laptop", 0.9, (220, 220, 250, 250))])
    motor.reloj.avanza(6)
    evs = motor.procesar(P(), {}, objetos_extra=[])
    assert [e.detalle for e in evs] == ["laptop zona=fuera"]


def test_franjas_de_zona_gatean_sus_reglas(motor):
    motor.zonas = normalizar_zonas({
        "noche": {"caja": [0, 0, 100, 100], "permanencia_seg": 5,
                  "franjas": [{"desde": "22:00", "hasta": "08:00"}]}})
    motor._hora_actual = lambda: "12:00"
    motor.procesar(P(), {1: (0, 0, 40, 40)})
    motor.reloj.avanza(20)
    assert motor.procesar(P(), {1: (0, 0, 40, 40)}) == []
    motor._hora_actual = lambda: "23:00"
    motor._reiniciar_estado()
    motor.procesar(P(), {1: (0, 0, 40, 40)})
    motor.reloj.avanza(5)
    assert motor.procesar(P(), {1: (0, 0, 40, 40)})[0].tipo == "permanencia"


# -- API /zonas -------------------------------------------------------------

def test_get_zonas_devuelve_objetos(cliente, monkeypatch):
    motor = MotorEventos(Almacen(":memory:"), config={
        "zonas": {"puerta": [0, 0, 10, 10]}})
    monkeypatch.setattr(web, "_motor_compartido", motor)
    d = cliente.get("/zonas").json()
    assert d["zonas"]["puerta"]["caja"] == [0, 0, 10, 10]
    assert d["zonas"]["puerta"]["restringida"] is False
    assert d["zonas"]["puerta"]["permanencia_seg"] is None


def test_put_zonas_antiguo_y_nuevo(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    motor = MotorEventos(Almacen(":memory:"), config={"zonas": {}})
    monkeypatch.setattr(web, "_motor_compartido", motor)
    assert cliente.put("/zonas", json={"zonas": {
        "vieja": [0, 0, 50, 50]}}).status_code == 200
    assert motor.zonas["vieja"].caja == (0, 0, 50, 50)
    nuevo = {"caja": [10, 10, 90, 90], "permanencia_seg": 8,
             "restringida": True, "poligono": None}
    nuevo.pop("poligono")
    r = cliente.put("/zonas", json={"zonas": {"nueva": nuevo}})
    assert r.status_code == 200
    assert motor.zonas["nueva"].permanencia_seg == 8
    d = cliente.get("/zonas").json()
    assert d["zonas"]["nueva"]["caja"] == [10, 10, 90, 90]
    assert cliente.put("/zonas", json={"zonas": {"mala": [1, 2]}}
                       ).status_code == 400


def test_config_carga_zonas_nuevas(tmp_path):
    from vigia.config import cargar_config
    p = tmp_path / "c.yaml"
    p.write_text(
        "zonas:\n"
        "  puerta:\n"
        "    poligono: [[0, 0], [10, 0], [0, 10]]\n"
        "    restringida: true\n"
        "    max_personas: 2\n"
        "  mesa: [1, 2, 3, 4]\n", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["zonas"]["puerta"].restringida is True
    assert cfg["zonas"]["puerta"].max_personas == 2
    assert cfg["zonas"]["mesa"].caja == (1, 2, 3, 4)
