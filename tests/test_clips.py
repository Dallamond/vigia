"""Tests del guardador de clips MP4 (sin camara)."""
import cv2
import numpy as np

from vigia.clips import GuardadorClips


def _frame(valor: int = 60) -> np.ndarray:
    return np.full((48, 64, 3), valor, np.uint8)


def test_guardar_sin_frames_no_crea_nada(tmp_path):
    g = GuardadorClips(carpeta=str(tmp_path / "clips"))
    assert g.guardar("persona_entra") is None


def test_guardar_escribe_un_mp4_legible(tmp_path):
    g = GuardadorClips(max_frames=5, carpeta=str(tmp_path / "clips"),
                       fps_salida=10)
    for i in range(5):
        g.meter(_frame(i * 10))
    destino = g.guardar("persona_entra")
    assert destino is not None
    g.esperar_trabajos(10)

    assert destino.exists() and destino.stat().st_size > 0
    cap = cv2.VideoCapture(str(destino))
    try:
        assert cap.isOpened()
        ok, frame = cap.read()
        assert ok
        assert frame.shape[1] == 64 and frame.shape[0] == 48
    finally:
        cap.release()


def test_dos_clips_en_el_mismo_segundo_no_se_pisan(tmp_path):
    g = GuardadorClips(max_frames=3, carpeta=str(tmp_path / "clips"))
    g.meter(_frame())
    a = g.guardar("persona_entra")
    b = g.guardar("persona_entra")
    g.esperar_trabajos(10)

    assert a != b
    assert a.exists() and b.exists()


def test_meter_guarda_copias_independientes(tmp_path):
    g = GuardadorClips(max_frames=2, carpeta=str(tmp_path / "clips"))
    frame = _frame(10)
    g.meter(frame)
    frame[:] = 255  # mutar el original no debe afectar al buffer
    assert g._buf[0][0, 0, 0] == 10
