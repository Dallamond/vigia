"""Tests del clasificador de actividades: poses sinteticas (sin webcam/modelo)."""
import numpy as np

from vigia.actividades import EstabilizadorAct, clasificar
from vigia.detector import Deteccion
from vigia.pose import PersonaPose

_CONF = np.full(17, 0.9, dtype=np.float32)


def pose_de(caja, hombro_y, cadera_y, rodilla_y=None, munecas=None):
    kpts = np.zeros((17, 2))
    conf = _CONF.copy()
    conf[[9, 10]] = 0.0  # sin munecas salvo que se pidan
    if munecas is not None:
        kpts[9], kpts[10] = munecas
        conf[[9, 10]] = 0.9
    if rodilla_y is None:
        conf[[13, 14]] = 0.0  # rodillas fuera de encuadre
    kpts[5] = (caja[0] + 10, hombro_y)
    kpts[6] = (caja[2] - 10, hombro_y)
    kpts[11] = (caja[0] + 20, cadera_y)
    kpts[12] = (caja[2] - 20, cadera_y)
    if rodilla_y is not None:
        kpts[13] = (caja[0] + 25, rodilla_y)
        kpts[14] = (caja[2] - 25, rodilla_y)
    return PersonaPose(1, kpts, conf, caja)


def test_de_pie():
    p = pose_de((200, 80, 260, 400), hombro_y=120, cadera_y=240, rodilla_y=320)
    assert clasificar(p, []) == "de pie"


def test_sentado():
    # rodilla elevada a la cadera
    p = pose_de((200, 150, 280, 330), hombro_y=180, cadera_y=260, rodilla_y=250)
    assert clasificar(p, []) == "sentado"


def test_durmiendo_tumbado():
    # caja mas ancha que alta, torso horizontal
    p = pose_de((80, 300, 560, 400), hombro_y=330, cadera_y=350, rodilla_y=355)
    assert clasificar(p, []) == "durmiendo"


def test_usando_pc_por_solape():
    p = pose_de((200, 150, 280, 330), hombro_y=180, cadera_y=260, rodilla_y=250)
    laptop = Deteccion("laptop", 0.9, (210, 200, 320, 260))
    assert clasificar(p, [laptop]) == "usando PC"


def test_usando_movil_por_solape():
    p = pose_de((200, 150, 280, 330), hombro_y=180, cadera_y=260, rodilla_y=250)
    movil = Deteccion("cell phone", 0.9, (250, 250, 280, 285))
    assert clasificar(p, [movil]) == "usando movil"


def test_estabilizador_no_anuncia_antes_de_tiempo():
    e = EstabilizadorAct(min_seg=3.0)
    assert e.tick("usando PC", 0.0) is None
    assert e.tick("usando PC", 2.0) is None   # aun no
    assert e.tick("usando PC", 3.2) == "usando PC"  # estable
    assert e.tick("usando PC", 30.0) is None  # ya anunciado


def test_estabilizador_reinicia_con_cambio():
    e = EstabilizadorAct(min_seg=3.0)
    e.tick("usando PC", 0.0)
    e.tick("sentado", 1.0)
    assert e.tick("sentado", 2.0) is None  # reset al cambiar


def test_estabilizador_arranca_sin_petar_con_desconocida():
    # regresion: `anunciada` no existia hasta el primer cambio de actividad
    e = EstabilizadorAct(min_seg=1.0)
    assert e.tick("desconocida", 0.0) is None
    assert e.tick("desconocida", 5.0) is None  # nunca se anuncia
    assert e.tick("de pie", 6.0) is None
    assert e.tick("de pie", 6.5) is None   # la votacion cambia aqui
    assert e.tick("de pie", 8.0) == "de pie"


def test_sentado_ante_la_mesa_sin_rodillas_no_es_de_pie():
    # regresion: sin rodillas visibles (mesa) se devolvia "de pie" siempre
    p = pose_de((200, 100, 400, 330), hombro_y=150, cadera_y=290)
    assert clasificar(p, []) == "sentado"


def test_cuerpo_entero_sin_rodillas_es_de_pie():
    p = pose_de((200, 20, 260, 470), hombro_y=100, cadera_y=250)
    assert clasificar(p, []) == "de pie"


def test_movil_lejos_de_las_manos_no_cuenta():
    p = pose_de((200, 150, 280, 330), hombro_y=180, cadera_y=260, rodilla_y=250,
                munecas=[(205, 320), (210, 322)])
    movil = Deteccion("cell phone", 0.9, (255, 170, 275, 190))
    assert clasificar(p, [movil]) == "sentado"


def test_movil_en_la_mano_cuenta():
    p = pose_de((200, 150, 280, 330), hombro_y=180, cadera_y=260, rodilla_y=250,
                munecas=[(265, 200), (250, 205)])
    movil = Deteccion("cell phone", 0.9, (255, 190, 275, 215))
    assert clasificar(p, [movil]) == "usando movil"


def test_de_pie_no_usa_pc_por_una_pantalla_detras():
    p = pose_de((200, 80, 260, 400), hombro_y=120, cadera_y=240, rodilla_y=320)
    tele = Deteccion("laptop", 0.9, (205, 150, 250, 200))
    assert clasificar(p, [tele]) == "de pie"


def test_estabilizador_ignora_una_lectura_suelta():
    e = EstabilizadorAct(min_seg=3.0)
    for t in (0.0, 0.5, 1.0):
        e.tick("sentado", t)
    e.tick("de pie", 1.2)   # frame ruidoso
    e.tick("sentado", 1.5)
    assert e.actual == "sentado"
    assert e.tick("sentado", 3.2) == "sentado"
