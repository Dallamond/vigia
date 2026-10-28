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


def test_no_acumula_ids_de_personas_que_ya_salieron():
    r = RastreadorPersonas(timeout_seg=0.01)
    r.actualizar({ID_ENTRA, ID_SALE})
    time.sleep(0.02)
    r.actualizar(set())  # ambos salen
    assert r._por_id == {}


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


# -- IDs estables y confirmacion de entrada -------------------------------
from vigia.detector import Deteccion  # noqa: E402
from vigia.tracking import ReasociadorIds  # noqa: E402


class _Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _persona(caja):
    return Deteccion("person", 0.9, caja)


def test_reasocia_id_nuevo_en_el_mismo_sitio():
    reloj = _Reloj()
    r = ReasociadorIds(reloj=reloj)
    dets = [_persona((100, 100, 200, 300))]
    assert r.mapear({0: 5}, dets) == {0: 5}
    reloj.t = 3.0  # ByteTrack pierde el 5 y crea el 9 donde estaba
    dets = [_persona((105, 102, 205, 302))]
    assert r.mapear({0: 9}, dets) == {0: 5}
    reloj.t = 3.1
    assert r.mapear({0: 9}, dets) == {0: 5}  # el alias se mantiene


def test_no_reasocia_personas_distintas_ni_ids_viejos():
    reloj = _Reloj()
    r = ReasociadorIds(ventana_seg=5.0, reloj=reloj)
    r.mapear({0: 1}, [_persona((0, 0, 50, 100))])
    reloj.t = 1.0  # otra persona lejos
    assert r.mapear({0: 2}, [_persona((500, 300, 560, 420))]) == {0: 2}
    reloj.t = 60.0  # el sitio esta libre desde hace mucho
    assert r.mapear({0: 3}, [_persona((0, 0, 50, 100))]) == {0: 3}


def test_dos_ids_nuevos_no_se_funden_en_uno():
    reloj = _Reloj()
    r = ReasociadorIds(reloj=reloj)
    r.mapear({0: 1}, [_persona((100, 100, 200, 300))])
    reloj.t = 2.0
    dets = [_persona((100, 100, 200, 300)), _persona((110, 100, 210, 300))]
    res = r.mapear({0: 7, 1: 8}, dets)
    assert len(set(res.values())) == 2


def test_objetos_no_se_remapean():
    r = ReasociadorIds(reloj=_Reloj())
    dets = [Deteccion("laptop", 0.9, (0, 0, 10, 10))]
    assert r.mapear({0: 4}, dets) == {0: 4}


def test_entrada_se_confirma_tras_el_tiempo_minimo():
    r = RastreadorPersonas(timeout_seg=5.0, confirmar_seg=0.1)
    assert list(r.actualizar({1})) == []          # aun no
    time.sleep(0.12)
    assert list(r.actualizar({1})) == [("persona_entra", 1)]
    assert list(r.actualizar({1})) == []


def test_deteccion_fugaz_no_genera_entra_ni_sale():
    r = RastreadorPersonas(timeout_seg=0.05, confirmar_seg=1.0)
    assert list(r.actualizar({1})) == []
    time.sleep(0.08)
    assert list(r.actualizar(set())) == []        # ni entra ni salio
    assert r._por_id == {}
