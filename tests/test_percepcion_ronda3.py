"""Tests de los modulos de la ronda 3: lineas de cruce, suavizado de keypoints,
huella de apariencia, detector de caidas y estado en vivo (`/vivo`)."""
from __future__ import annotations

import numpy as np
import pytest

from vigia import vivo
from vigia.actividades import DetectorCaidas
from vigia.apariencia import huella_torso, similitud
from vigia.lineas import GestorLineas, normalizar_lineas
from vigia.suavizado import SuavizadorKpts


# ---------------------------------------------------------------- lineas
def _gestor(**extra):
    cfg = {"puerta": {"p1": [0, 100], "p2": [200, 100], "margen_px": 4,
                      "cooldown_seg": 2, **extra}}
    return GestorLineas(normalizar_lineas(cfg))


def _caja_en(y):
    # el centro-pie es el punto medio de la base de la caja
    return (90, y - 40, 110, y)


def test_cruce_de_linea_emite_una_vez_con_direccion():
    g = _gestor()
    assert g.procesar({1: _caja_en(60)}, 0.0) == []
    cruces = g.procesar({1: _caja_en(140)}, 1.0)
    assert [c["linea"] for c in cruces] == ["puerta"]
    assert cruces[0]["id_tracking"] == 1
    assert cruces[0]["direccion"] in ("a→b", "b→a")
    # quedarse al otro lado no repite
    assert g.procesar({1: _caja_en(150)}, 1.5) == []


def test_cruce_de_ida_y_vuelta_tiene_direcciones_opuestas():
    g = _gestor(cooldown_seg=0)
    g.procesar({1: _caja_en(60)}, 0.0)
    ida = g.procesar({1: _caja_en(140)}, 1.0)[0]["direccion"]
    vuelta = g.procesar({1: _caja_en(60)}, 2.0)[0]["direccion"]
    assert ida != vuelta


def test_persona_parada_sobre_la_linea_no_genera_rebotes():
    g = _gestor(cooldown_seg=0)
    g.procesar({1: _caja_en(60)}, 0.0)
    eventos = []
    for i, y in enumerate((99, 101, 100, 99, 101, 100)):  # dentro del margen
        eventos += g.procesar({1: _caja_en(y)}, 1.0 + i)
    assert eventos == []


def test_sentido_unico_ignora_la_otra_direccion():
    g = _gestor(sentido="a_b", cooldown_seg=0)
    g.procesar({1: _caja_en(60)}, 0.0)
    r1 = g.procesar({1: _caja_en(140)}, 1.0)
    r2 = g.procesar({1: _caja_en(60)}, 2.0)
    assert len(r1) + len(r2) == 1


def test_cooldown_evita_dobles_avisos():
    g = _gestor(cooldown_seg=5)
    g.procesar({1: _caja_en(60)}, 0.0)
    assert len(g.procesar({1: _caja_en(140)}, 1.0)) == 1
    assert g.procesar({1: _caja_en(60)}, 2.0) == []   # dentro del cooldown


# ------------------------------------------------------------- suavizado
def test_suavizado_reduce_el_temblor_de_un_punto_quieto():
    rng = np.random.default_rng(0)
    s = SuavizadorKpts()
    crudo, suave = [], []
    for i in range(60):
        ruido = rng.normal(0, 3, size=2)
        k = np.array([[100.0 + ruido[0], 200.0 + ruido[1]]])
        sal, _, _ = s.suavizar(k, np.array([0.9]), 1, i / 15)
        crudo.append(k[0, 0])
        suave.append(sal[0, 0])
    assert np.std(suave[10:]) < 0.8 * np.std(crudo[10:])


def test_suavizado_sigue_un_movimiento_rapido():
    s = SuavizadorKpts()
    ultimo = None
    for i in range(40):
        k = np.array([[10.0 + 12 * i, 50.0]])
        ultimo, _, _ = s.suavizar(k, np.array([0.9]), 1, i / 15)
    # no se queda atras mas de ~un par de frames
    assert abs(ultimo[0, 0] - (10.0 + 12 * 39)) < 40


def test_suavizado_mantiene_un_instante_el_punto_perdido():
    s = SuavizadorKpts(mantener_seg=1.0)
    s.suavizar(np.array([[50.0, 60.0]]), np.array([0.9]), 7, 0.0)
    sal, conf, _ = s.suavizar(np.array([[0.0, 0.0]]), np.array([0.05]), 7, 0.2)
    assert abs(sal[0, 0] - 50.0) < 1.0 and conf[0] > 0.5
    sal, _, _ = s.suavizar(np.array([[0.0, 0.0]]), np.array([0.05]), 7, 3.0)
    assert sal[0, 0] == 0.0       # pasado el tiempo ya no se inventa


# ------------------------------------------------------------ apariencia
def _frame_color(bgr):
    f = np.zeros((200, 100, 3), np.uint8)
    f[:] = bgr
    return f


def test_apariencia_distingue_colores_de_ropa():
    caja = (0, 0, 100, 200)
    roja1 = huella_torso(_frame_color((0, 0, 220)), caja)
    roja2 = huella_torso(_frame_color((10, 10, 215)), caja)
    azul = huella_torso(_frame_color((220, 30, 10)), caja)
    assert similitud(roja1, roja2) > similitud(roja1, azul)
    assert similitud(roja1, roja1) == pytest.approx(1.0, abs=1e-3)


def test_apariencia_tolera_entradas_vacias():
    assert huella_torso(None, (0, 0, 10, 10)) is None
    assert huella_torso(_frame_color((1, 2, 3)), (0, 0, 2, 2)) is None
    assert similitud(None, np.zeros(4)) == 0.0


# --------------------------------------------------------------- caidas
CAJA = (100, 100, 160, 300)


def test_caida_de_pie_a_tumbado_e_inmovil_se_anuncia_una_vez():
    d = DetectorCaidas(inmovil_seg=3.0)
    assert d.actualizar(1, "de pie", CAJA, 0.0) is False
    assert d.actualizar(1, "tumbado", CAJA, 1.0) is False
    assert d.actualizar(1, "tumbado", CAJA, 2.5) is False
    assert d.actualizar(1, "tumbado", CAJA, 4.5) is True
    assert d.actualizar(1, "tumbado", CAJA, 8.0) is False   # una sola vez


def test_tumbado_desde_el_principio_no_es_caida():
    d = DetectorCaidas(inmovil_seg=2.0)
    for t in (0.0, 1.0, 3.0, 6.0):
        assert d.actualizar(2, "tumbado", CAJA, t) is False


def test_caida_desactivada_no_emite():
    d = DetectorCaidas(activa=False)
    d.actualizar(1, "de pie", CAJA, 0.0)
    assert d.actualizar(1, "tumbado", CAJA, 10.0) is False


# ------------------------------------------------------------------ /vivo
@pytest.fixture
def estado_limpio():
    vivo.limpiar()
    yield
    vivo.limpiar()


def test_snapshot_vivo_tiene_el_contrato(estado_limpio):
    vivo.publicar_frame(960, 540, [{"clase": "laptop", "caja": [1, 2, 3, 4],
                                    "conf": 0.7}])
    vivo.publicar_persona(3, (10, 20, 110, 220),
                          kpts=[(i, i, 0.9) for i in range(17)],
                          actividad="sentado", actividad_cruda="sentado",
                          conf=0.88, desde=42.0)
    vivo.publicar_identidad(3, "Lucas")
    vivo.publicar_caras([{"caja": [20, 20, 40, 40], "nombre": "Lucas"}])
    d = vivo.snapshot_vivo()
    assert (d["ancho"], d["alto"]) == (960, 540)
    p = d["personas"][0]
    assert set(p) == {"id", "nombre", "caja", "actividad", "actividad_cruda",
                      "desde_seg", "kpts", "zona", "conf"}
    assert p["nombre"] == "Lucas" and len(p["kpts"]) == 17
    assert d["objetos"][0]["clase"] == "laptop"
    assert d["caras"][0]["nombre"] == "Lucas"


def test_olvidar_personas_quita_a_los_que_ya_no_estan(estado_limpio):
    vivo.publicar_persona(1, (0, 0, 10, 10))
    vivo.publicar_persona(2, (0, 0, 10, 10))
    vivo.olvidar_personas({2})
    vivo._cache = None
    assert [p["id"] for p in vivo.snapshot_vivo()["personas"]] == [2]


def test_endpoint_vivo_responde_json_y_no_se_cachea(estado_limpio):
    from fastapi.testclient import TestClient

    from vigia import web

    vivo.publicar_frame(640, 480)
    r = TestClient(web.app, client=("127.0.0.1", 50000)).get("/vivo")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["ancho"] == 640
