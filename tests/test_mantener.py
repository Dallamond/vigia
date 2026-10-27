"""Tests de la purga de media y del mantenimiento periodico."""
from __future__ import annotations

import os
import time
from pathlib import Path

from vigia.almacen import Almacen
from vigia.mantener import mantener, purgar_media


def _crear(p: Path, bytes_: int = 100, edad_dias: float = 0) -> Path:
    p.write_bytes(b"x" * bytes_)
    if edad_dias:
        viejo = time.time() - edad_dias * 86400
        os.utime(p, (viejo, viejo))
    return p


def test_purgar_por_antiguedad(tmp_path):
    _crear(tmp_path / "viejo.mp4", edad_dias=40)
    _crear(tmp_path / "nuevo.mp4", edad_dias=1)
    _crear(tmp_path / "vieja.jpg", edad_dias=40)
    _crear(tmp_path / "notas.txt", edad_dias=40)  # no es media: no se toca

    res = purgar_media(tmp_path, dias=30)
    assert res["borrados"] == 2
    assert not (tmp_path / "viejo.mp4").exists()
    assert not (tmp_path / "vieja.jpg").exists()
    assert (tmp_path / "nuevo.mp4").exists()
    assert (tmp_path / "notas.txt").exists()


def test_purgar_por_tamano_borra_los_mas_viejos(tmp_path):
    for i in range(4):
        _crear(tmp_path / f"c{i}.mp4", bytes_=1000, edad_dias=10 - i)
    # 4 x 1000 = 4000; dejamos 2500 -> se borran los 2 mas viejos
    res = purgar_media(tmp_path, max_bytes=2500)
    assert res["borrados"] == 2
    assert (tmp_path / "c2.mp4").exists() and (tmp_path / "c3.mp4").exists()
    assert not (tmp_path / "c0.mp4").exists()


def test_carpeta_inexistente_no_rompe(tmp_path):
    assert purgar_media(tmp_path / "no-existe")["borrados"] == 0


def test_mantener_purga_eventos_y_media(tmp_path):
    a = Almacen(tmp_path / "m.db")
    a.guardar("viejo", cuando="2020-01-01 10:00:00")
    a.guardar("nuevo", cuando="2026-10-27 10:00:00")

    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    _crear(carpeta / "viejo.mp4", edad_dias=40)

    class ClipsFalso:
        def __init__(self, carpeta):
            self.carpeta = carpeta
            self.podados = 0

        def podar(self):
            self.podados += 1
            return 0

        def podar_miniaturas(self):
            self.podados += 1
            return 0

    clips = ClipsFalso(carpeta)
    res = mantener(a, clips, {"retencion_dias": 30,
                              "retencion_clips_dias": 30,
                              "retencion_clips_mb": 0})
    assert res["eventos"] == 1
    assert res["borrados"] == 1
    assert clips.podados == 2
    assert a.contar() == 1
    a.cerrar()
