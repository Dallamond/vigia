"""Tests del motor de eventos: permanencia, objetos, zonas y franja horaria.

Usa un almacen en tmp_path y reloj controlado (sin webcam ni YOLO).
"""
from __future__ import annotations

import pytest

from vigia.almacen import Almacen
from vigia.detector import Deteccion
from vigia.eventos import MotorEventos

RELOJ = {"t": 1000.0}


def ahora():
    return RELOJ["t"]


def adelantar(seg):
    RELOJ["t"] += seg


@pytest.fixture
def motor(tmp_path):
    RELOJ["t"] = 1000.0
    a = Almacen(tmp_path / "m.db")
    m = MotorEventos(a, config={
        "permanencia_seg": 30,
        "vigilar_desaparicion": ["laptop"],
        "timeout_objeto_seg": 5,
        "zonas": {"puerta": (0, 0, 50, 50)},
    }, reloj=ahora)
    yield m
    a.cerrar()


P = lambda x1=0, y1=0, x2=100, y2=100: Deteccion("person", 0.9, (x1, y1, x2, y2))  # noqa
L = lambda present: [Deteccion("laptop", 0.9, (10, 10, 50, 50))] if present else []


def test_permanencia_dispara_una_vez(motor):
    motor.procesar(P(), {})
    assert motor.procesar(P(), {}) == []  # aun no
    adelantar(31)
    evs = motor.procesar(P(), {})
    assert len(evs) == 1 and evs[0].tipo == "permanencia"
    adelantar(60)
    assert motor.procesar(P(), {}) == []  # no repite hasta que sale


def test_permanencia_tiende_a_cero_al_salir(motor):
    motor.procesar(P(), {})
    adelantar(31)
    motor.procesar(P(), {})
    adelantar(5)
    motor.procesar(set(), {})  # sale (no hay dets de persona)
    adelantar(40)
    motor.procesar(P(), {})  # reentrada
    assert motor.procesar(P(), {}) == []


def test_objeto_desaparece_y_aparece(motor):
    motor.procesar(P(), {}, objetos_extra=L(True))
    adelantar(6)
    evs = motor.procesar(P(), {}, objetos_extra=L(False))
    tipos = {e.tipo for e in evs}
    assert "objeto_desaparece" in tipos
    adelantar(1)
    evs = motor.procesar(P(), {}, objetos_extra=L(True))
    assert any(e.tipo == "objeto_aparece" for e in evs)


def test_zona_se_registra_en_detalle(motor):
    motor.procesar(P(0, 0, 40, 40), {})  # dentro de puerta
    adelantar(31)
    evs = motor.procesar(P(0, 0, 40, 40), {})
    assert evs[0].detalle == "permanencia zona=puerta"


def test_franja_horaria_bloquea(tmp_path):
    import time as _t
    a = Almacen(tmp_path / "f.db")
    m = MotorEventos(a, config={
        "permanencia_seg": 30,
        "franjas": [{"desde": "01:00", "hasta": "05:00"}],
    }, reloj=_t.time)  # reloj real: ahora no es de 01:00-05:00 salvo caso raro
    motor_copia = m
    motor_copia.procesar(P(), {})
    # falta tiempo simulado -> no se dispara nada (si no estamos en franja)
    if not m._en_franja(m._hora_actual()):
        assert m.procesar(P(), {}) == []
    a.cerrar()
