"""Tests del modulo de dibujo (sin GUI, solo arrays)."""
import numpy as np

from vigia.detector import Deteccion
from vigia.dibujo import dibujar, frame_aviso


def test_frame_aviso_respeta_el_tamano():
    frame = frame_aviso("aviso", 320, 240)
    assert frame.shape == (240, 320, 3)


def test_dibujar_pinta_detecciones_sin_ids():
    frame = np.zeros((240, 320, 3), np.uint8)
    dibujar(frame, [Deteccion("person", 0.9, (5, 5, 50, 90))], 10.0)
    assert frame.sum() > 0


def test_dibujar_pinta_detecciones_con_id_de_tracking():
    frame = np.zeros((240, 320, 3), np.uint8)
    dets = [Deteccion("person", 0.9, (5, 5, 50, 90)),
            Deteccion("laptop", 0.8, (60, 40, 120, 100))]
    dibujar(frame, dets, 12.0, pie=None, ids_por_indice={0: 7}, ids_persona={7})
    assert frame.sum() > 0
