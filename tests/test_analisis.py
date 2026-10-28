"""Tests de los pasos opcionales del bucle (caras y actividades), sin modelos."""
import threading
import time

import numpy as np

from vigia.analisis import (CargaDiferida, ModuloActividades, ModuloCaras,
                            persona_de_pose)


class _DetectorCarasFalso:
    """Devuelve las caras que se le indiquen e identifica por un mapa."""

    def __init__(self):
        self.caras = []
        self.identidad = (None, 0.0)
        self.conocidos = None
        self.llamadas = 0

    def set_conocidos(self, conocidos):
        self.conocidos = conocidos

    def detectar(self, frame):
        self.llamadas += 1
        return list(self.caras)

    def identificar(self, emb):
        return self.identidad


def _cara(caja=(10, 10, 50, 50), conf=0.9):
    return {"caja": caja, "conf": conf, "emb": np.ones(128, np.float32)}


def _modulo(detector, **kw):
    m = ModuloCaras(cargar_conocidos=lambda: {"Lucas": [np.ones(128)]},
                    recarga=threading.Event(), fabrica=lambda: detector, **kw)
    m._carga.obtener()
    m._carga._hilo.join(2)  # carga sincronica para el test
    m.CADA_N_FRAMES = 1
    return m


FRAME = np.zeros((120, 160, 3), np.uint8)
PERSONA = {7: (0, 0, 100, 100)}


def test_carga_diferida_no_bloquea_y_entrega_el_modelo():
    listo = threading.Event()

    def fabrica():
        listo.wait(2)
        return "modelo"

    c = CargaDiferida(fabrica, "x")
    assert c.obtener() is None  # lanza la carga y sigue
    listo.set()
    c._hilo.join(2)
    assert c.obtener() == "modelo"


def test_carga_diferida_no_reintenta_tras_fallar_hasta_reactivarse():
    intentos = []

    def fabrica():
        intentos.append(1)
        raise RuntimeError("sin red")

    c = CargaDiferida(fabrica, "x")
    c.obtener()
    c._hilo.join(2)
    assert c.fallo and c.obtener() is None and c.obtener() is None
    assert len(intentos) == 1
    c.permitir_reintento()  # el usuario apaga y vuelve a encender el ajuste
    c.obtener()
    c._hilo.join(2)
    assert len(intentos) == 2


def test_caras_devuelve_none_mientras_carga():
    m = ModuloCaras(cargar_conocidos=dict, recarga=threading.Event(),
                    fabrica=lambda: time.sleep(0.3) or _DetectorCarasFalso())
    assert m.procesar(FRAME, PERSONA) is None


def test_caras_anuncia_al_reconocido_una_sola_vez():
    det = _DetectorCarasFalso()
    det.caras = [_cara()]
    det.identidad = ("Lucas", 0.9)
    m = _modulo(det)
    primero = m.procesar(FRAME, PERSONA, ahora=100.0)
    assert [e[0] for e in primero] == ["persona_reconocida"]
    assert "Lucas" in primero[0][1] and primero[0][2] == 7
    assert m.procesar(FRAME, PERSONA, ahora=101.0) == []
    assert m.identidades[7][0] == "Lucas"


def test_caras_no_repite_al_mismo_nombre_con_otro_id_enseguida():
    det = _DetectorCarasFalso()
    det.caras = [_cara()]
    det.identidad = ("Lucas", 0.9)
    m = _modulo(det)
    m.procesar(FRAME, {7: (0, 0, 100, 100)}, ahora=100.0)
    # ByteTrack le da un id nuevo a los 5 s: sigue siendo la misma persona
    assert m.procesar(FRAME, {8: (0, 0, 100, 100)}, ahora=105.0) == []
    assert len(m.procesar(FRAME, {9: (0, 0, 100, 100)}, ahora=200.0)) == 1


def test_caras_desconocida_tiene_cooldown_por_persona():
    det = _DetectorCarasFalso()
    det.caras = [_cara()]
    m = _modulo(det)
    assert [e[0] for e in m.procesar(FRAME, PERSONA, ahora=1000.0)] == [
        "cara_desconocida"]
    assert m.procesar(FRAME, PERSONA, ahora=1010.0) == []
    assert len(m.procesar(FRAME, PERSONA, ahora=1300.0)) == 1


def test_caras_baja_confianza_no_dispara_desconocida():
    det = _DetectorCarasFalso()
    det.caras = [_cara(conf=0.62)]
    assert _modulo(det).procesar(FRAME, PERSONA, ahora=1.0) == []


def test_caras_ignora_caras_fuera_de_toda_persona():
    det = _DetectorCarasFalso()
    det.caras = [_cara(caja=(120, 80, 150, 110))]
    assert _modulo(det).procesar(FRAME, PERSONA, ahora=1.0) == []


def test_caras_olvida_a_las_personas_que_se_fueron():
    det = _DetectorCarasFalso()
    det.caras = [_cara()]
    det.identidad = ("Lucas", 0.9)
    m = _modulo(det)
    m.procesar(FRAME, PERSONA, ahora=100.0)
    det.caras = []
    m.procesar(FRAME, {}, ahora=100.0 + m.OLVIDO_SEG + 1)
    assert m.identidades == {} and m._reconocidos == {}
    assert m._aviso_desconocida == {}


def test_caras_solo_analiza_cada_n_frames():
    det = _DetectorCarasFalso()
    m = _modulo(det)
    m.CADA_N_FRAMES = 3
    for _ in range(9):
        m.procesar(FRAME, PERSONA, ahora=1.0)
    assert det.llamadas == 3


def test_caras_publica_la_cara_mas_grande_para_registrarla():
    vistas = []
    det = _DetectorCarasFalso()
    det.caras = [_cara(caja=(0, 0, 20, 20)), _cara(caja=(30, 30, 90, 90))]
    m = _modulo(det, al_ver_cara=vistas.append)
    m.procesar(FRAME, PERSONA, ahora=50.0)
    assert len(vistas) == 1 and vistas[0]["t"] == 50.0
    assert vistas[0]["emb"].shape == (128,) and vistas[0]["jpeg"][:2] == b"\xff\xd8"


def test_caras_recarga_los_conocidos_cuando_la_ui_lo_pide():
    det = _DetectorCarasFalso()
    m = _modulo(det)
    m.procesar(FRAME, PERSONA)
    assert list(det.conocidos) == ["Lucas"]
    det.conocidos = None
    m._recarga.set()
    m.procesar(FRAME, PERSONA)
    assert list(det.conocidos) == ["Lucas"] and not m._recarga.is_set()


def test_caras_escribe_el_nombre_en_el_frame():
    det = _DetectorCarasFalso()
    det.caras = [_cara()]
    det.identidad = ("Lucas", 0.9)
    frame = np.zeros((120, 160, 3), np.uint8)
    _modulo(det).procesar(frame, PERSONA, ahora=1.0)
    assert frame.any()  # hay texto pintado


def test_actividades_devuelve_none_mientras_carga_y_vacio_sin_poses():
    class PoseFalsa:
        def detectar(self, frame):
            return []

    m = ModuloActividades(fabrica=lambda: PoseFalsa())
    assert m.procesar(FRAME, PERSONA, []) is None
    m._carga._hilo.join(2)
    assert m.procesar(FRAME, PERSONA, []) == ({}, [])
    assert m.apagar() is False


def test_persona_de_pose_empareja_por_caja():
    personas = [(4, (0, 0, 100, 100)), (5, (200, 200, 300, 300))]
    assert persona_de_pose((10, 10, 90, 90), personas) == 4
    assert persona_de_pose((250, 250, 260, 260), personas) == 5
    assert persona_de_pose((500, 500, 510, 510), personas) is None


# -- analisis asincrono ------------------------------------------------------
from vigia.analisis import AnalisisAsincrono  # noqa: E402


class _ActividadesFalsas:
    def __init__(self):
        self.llamadas = 0
        self.apagados = 0

    def procesar(self, frame, mapa_ids, extras, ahora=None, dibujar=True):
        self.llamadas += 1
        assert dibujar is False  # el hilo nunca pinta sobre el frame vivo
        return {1: "sentado"}, [(1, "sentado")]

    def apagar(self):
        self.apagados += 1
        return True


class _CarasFalsas:
    def procesar(self, frame, mapa_ids, ahora=None, dibujar=True):
        return [("persona_reconocida", "id 1 = Lucas", 1)]

    def apagar(self):
        pass


def _esperar(cond, seg=2.0):
    fin = time.time() + seg
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_analisis_asincrono_entrega_resultados_sin_bloquear():
    publicado = {}
    a = AnalisisAsincrono(_ActividadesFalsas(), _CarasFalsas(), publicado.update)
    try:
        frame = np.zeros((10, 10, 3), np.uint8)
        a.enviar(frame, {1: (0, 0, 5, 5)}, [], True, True)
        eventos = []
        assert _esperar(lambda: eventos.extend(a.recoger()) or eventos)
        assert ("actividad", "id 1 sentado", 1) in eventos
        assert ("persona_reconocida", "id 1 = Lucas", 1) in eventos
        assert publicado == {1: "sentado"}
    finally:
        a.parar()


def test_analisis_asincrono_trabaja_sobre_una_copia_del_frame():
    visto = []

    class _Act(_ActividadesFalsas):
        def procesar(self, frame, *a, **k):
            visto.append(frame)
            return super().procesar(frame, *a, **k)

    a = AnalisisAsincrono(_Act(), _CarasFalsas(), lambda _: None)
    try:
        frame = np.zeros((10, 10, 3), np.uint8)
        a.enviar(frame, {}, [], True, False)
        assert _esperar(lambda: visto)
        assert visto[0] is not frame
    finally:
        a.parar()


def test_analisis_asincrono_apaga_los_modulos_al_desactivarlos():
    act = _ActividadesFalsas()
    publicado = []
    a = AnalisisAsincrono(act, _CarasFalsas(), publicado.append)
    try:
        frame = np.zeros((10, 10, 3), np.uint8)
        a.enviar(frame, {}, [], True, False)
        assert _esperar(lambda: act.llamadas)
        assert _esperar(lambda: a.activo)
        a.enviar(frame, {}, [], False, False)   # el usuario lo apaga
        assert _esperar(lambda: act.apagados)
        assert publicado[-1] == {}
        assert _esperar(lambda: not a.activo)
    finally:
        a.parar()


def test_analisis_asincrono_inactivo_no_arranca_hilo():
    a = AnalisisAsincrono(_ActividadesFalsas(), _CarasFalsas(), lambda _: None)
    a.enviar(np.zeros((4, 4, 3), np.uint8), {}, [], False, False)
    assert a._hilo is None
