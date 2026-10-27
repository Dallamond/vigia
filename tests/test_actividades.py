"""Tests del clasificador de actividades: poses sinteticas (sin webcam/modelo)."""
import numpy as np

from vigia.actividades import EstabilizadorAct, clasificar
from vigia.detector import Deteccion
from vigia.pose import PersonaPose

_CONF = np.full(17, 0.9, dtype=np.float32)


def pose_de(caja, hombro_y, cadera_y, rodilla_y=None):
    kpts = np.zeros((17, 2))
    kpts[5] = (caja[0] + 10, hombro_y)
    kpts[6] = (caja[2] - 10, hombro_y)
    kpts[11] = (caja[0] + 20, cadera_y)
    kpts[12] = (caja[2] - 20, cadera_y)
    if rodilla_y is not None:
        kpts[13] = (caja[0] + 25, rodilla_y)
        kpts[14] = (caja[2] - 25, rodilla_y)
    return PersonaPose(1, kpts, _CONF, caja)


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
