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


def test_guardar_miniatura_escribe_un_jpg_legible(tmp_path):
    g = GuardadorClips(carpeta=str(tmp_path / "clips"))
    destino = g.guardar_miniatura(_frame(120), "persona_entra")
    assert destino is not None and destino.suffix == ".jpg"
    img = cv2.imread(str(destino))
    assert img is not None and img.shape[1] == 64 and img.shape[0] == 48


def test_guardar_miniatura_sin_frame_no_rompe(tmp_path):
    g = GuardadorClips(carpeta=str(tmp_path / "clips"))
    assert g.guardar_miniatura(None, "x") is None


def test_miniaturas_y_clips_no_comparten_nombre(tmp_path):
    g = GuardadorClips(max_frames=2, carpeta=str(tmp_path / "clips"))
    g.meter(_frame())
    clip = g.guardar("persona_entra")
    mini = g.guardar_miniatura(_frame(), "persona_entra")
    g.esperar_trabajos(10)
    assert clip.stem != mini.stem


def test_podar_miniaturas(tmp_path):
    g = GuardadorClips(carpeta=str(tmp_path / "clips"))
    for _ in range(5):
        g.guardar_miniatura(_frame(), "x")
    assert g.podar_miniaturas(max_ficheros=2) == 3
    assert len(list((tmp_path / "clips").glob("*.jpg"))) == 2


def test_resumen_media_cuenta_clips_y_miniaturas(tmp_path):
    g = GuardadorClips(max_frames=2, carpeta=str(tmp_path / "clips"))
    g.meter(_frame())
    clip = g.guardar("persona_entra")
    mini = g.guardar_miniatura(_frame(), "persona_entra")
    g.esperar_trabajos(10)
    (g.carpeta / "notas.txt").write_bytes(b"no es media")

    res = g.resumen_media()
    assert res["n"] == 1
    assert res["miniaturas"] == 1
    assert res["bytes"] == clip.stat().st_size + mini.stat().st_size


def test_resumen_media_carpeta_inexistente(tmp_path):
    g = GuardadorClips(carpeta=str(tmp_path / "no-existe"))
    import shutil
    shutil.rmtree(g.carpeta)
    assert g.resumen_media() == {"n": 0, "miniaturas": 0, "bytes": 0}


def test_fps_salida_se_puede_ajustar(tmp_path):
    g = GuardadorClips(max_frames=2, carpeta=str(tmp_path / "clips"))
    g.fps_salida = 12.5
    g.meter(_frame())
    destino = g.guardar("x")
    g.esperar_trabajos(10)
    assert destino.exists()
