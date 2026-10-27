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


# -- regresiones: media que desaparece y referencias huerfanas -----------

def test_purgar_tolera_fichero_que_desaparece(tmp_path, monkeypatch):
    for i in range(3):
        _crear(tmp_path / f"c{i}.mp4", edad_dias=40)

    original = Path.unlink

    def unlink_fragil(self, *a, **k):
        if self.name == "c0.mp4":
            raise FileNotFoundError(self)  # otro proceso lo borro antes
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink_fragil)
    res = purgar_media(tmp_path, dias=30)  # no debe lanzar
    assert res["borrados"] == 2
    assert (tmp_path / "c0.mp4").exists()


def test_limpiar_media_huerfana_quita_referencias_rotas(tmp_path):
    a = Almacen(tmp_path / "h.db")
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (carpeta / "viva.jpg").write_bytes(b"jpg")
    (carpeta / "vivo.mp4").write_bytes(b"mp4")

    id_vivo = a.guardar("persona_entra")
    a.anotar_media(id_vivo, miniatura="viva.jpg", clip="vivo.mp4")
    id_roto = a.guardar("persona_salio")
    a.anotar_media(id_roto, miniatura="borrada.jpg", clip="borrado.mp4")

    assert a.limpiar_media_huerfana(carpeta) == 1
    filas = {f["id"]: f for f in a.historial()}
    assert filas[id_vivo]["miniatura"] == "viva.jpg"
    assert filas[id_vivo]["clip"] == "vivo.mp4"
    assert filas[id_roto]["miniatura"] is None
    assert filas[id_roto]["clip"] is None
    # idempotente: ya no queda nada que limpiar
    assert a.limpiar_media_huerfana(carpeta) == 0
    a.cerrar()


def test_mantener_limpia_referencias_tras_purgar(tmp_path):
    a = Almacen(tmp_path / "m2.db")
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    _crear(carpeta / "vieja.jpg", edad_dias=40)
    evento = a.guardar("persona_entra")
    a.anotar_media(evento, miniatura="vieja.jpg")

    class ClipsFalso:
        def __init__(self, carpeta):
            self.carpeta = carpeta

        def podar(self):
            return 0

        def podar_miniaturas(self):
            return 0

    res = mantener(a, ClipsFalso(carpeta), {"retencion_clips_dias": 30,
                                            "retencion_clips_mb": 0})
    assert res["borrados"] == 1
    assert res["media_huerfana"] == 1
    assert a.historial()[0]["miniatura"] is None
    a.cerrar()
