"""Tests del estado de armado persistente y automatico por franjas."""
from datetime import datetime

from vigia.almacen import Almacen
from vigia.armado import GestorArmado


def _ts(hora):
    return datetime(2026, 10, 28, hora, 0).timestamp()


def test_armado_manual_persiste_entre_instancias(tmp_path):
    a = Almacen(tmp_path / "a.db")
    g = GestorArmado(a, {"franjas": []}, reloj=lambda: _ts(12))
    assert g.estado()["armado"] is False
    g.armar()
    assert g.estado()["armado"] is True and g.estado()["desde"]
    g2 = GestorArmado(a, {"franjas": []})
    assert g2.estado()["armado"] is True
    g2.desarmar()
    assert GestorArmado(a).estado()["armado"] is False
    a.cerrar()


def test_armado_automatico_por_franjas():
    t = {"v": _ts(23)}
    g = GestorArmado(None, {
        "armado_auto": True,
        "armado_franjas": [{"desde": "22:00", "hasta": "08:00"}],
    }, reloj=lambda: t["v"])
    assert g.actualizar() is True and g.armado is True
    assert g.estado()["auto"] is True
    t["v"] = _ts(12)
    assert g.actualizar() is True and g.armado is False
    assert g.actualizar() is False


def test_armado_sin_franjas_no_cambia_solo():
    g = GestorArmado(None, {"armado_auto": True}, reloj=lambda: _ts(23))
    assert g.actualizar() is False and g.armado is False


def test_configurar_auto_aplica_el_estado_actual():
    g = GestorArmado(None, {
        "armado_franjas": [{"desde": "22:00", "hasta": "08:00"}]},
        reloj=lambda: _ts(23))
    assert g.auto is False
    g.configurar_auto(True)
    assert g.auto is True and g.armado is True
    g.configurar_auto(False)
    assert g.auto is False


def test_alternar_y_cambio_solo_cuando_toca():
    g = GestorArmado(None)
    assert g.alternar() is True
    assert g.alternar() is False
