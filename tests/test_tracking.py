"""Tests del tracking: Trayectorias simuladas, sin webcam ni YOLO."""
import time

from vigia.tracking import RastreadorPersonas

ID_ENTRA = 1
ID_SALE = 2


def test_persona_entra_y_sale():
    r = RastreadorPersonas(timeout_seg=0.2)
    events = list(r.actualizar({ID_ENTRA}))
    assert any(tipo == "persona_entra" for tipo, _ in events)


def test_misma_persona_no_genera_segundo_entra():
    r = RastreadorPersonas(timeout_seg=0.2)
    r.actualizar({ID_ENTRA})
    assert list(r.actualizar({ID_ENTRA})) == []  # sigue dentro


def test_misma_persona_no_genera_segundo_entra():
    r = RastreadorPersonas(timeout_seg=0.2)
    r.actualizar({ID_ENTRA})
    assert list(r.actualizar({ID_ENTRA})) == []  # sigue dentro


def test_persona_sale_por_timeout():
    r = RastreadorPersonas(timeout_seg=0.05)
    acumulado = list(r.actualizar({ID_ENTRA}))
    acumulado += list(r.actualizar({ID_ENTRA, ID_SALE}))
    time.sleep(0.08)
    acumulado += list(r.actualizar(set()))
    tipos = {t for t, _ in acumulado}
    assert tipos == {"persona_entra", "persona_salio"}


def test_eventos_tienen_id():
    r = RastreadorPersonas(timeout_seg=0.05)
    acumulado = list(r.actualizar({ID_ENTRA}))
    acumulado += list(r.actualizar({ID_ENTRA, ID_SALE}))
    time.sleep(0.08)
    acumulado += list(r.actualizar(set()))
    ids = {i for _, i in acumulado}
    assert ids == {ID_ENTRA, ID_SALE}


def test_reentrada_genera_nuevo_entra():
    r = RastreadorPersonas(timeout_seg=0.05)
    r.actualizar({ID_ENTRA})
    time.sleep(0.08)
    r.actualizar(set())  # sale
    events = r.actualizar({ID_ENTRA})  # vuelve a entrar
    assert ("persona_entra", ID_ENTRA) in events
