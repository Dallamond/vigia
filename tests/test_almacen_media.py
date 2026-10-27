"""Tests de las columnas nuevas (miniatura/clip), timeline y purga."""
from __future__ import annotations

import sqlite3

from vigia.almacen import Almacen


def test_guardar_devuelve_id_y_media_se_enlaza(tmp_path):
    a = Almacen(tmp_path / "a.db")
    ev_id = a.guardar("persona_entra", id_tracking=3)
    assert ev_id > 0
    a.anotar_media(ev_id, miniatura="m.jpg", clip="c.mp4")
    fila = a.historial()[0]
    assert fila["miniatura"] == "m.jpg" and fila["clip"] == "c.mp4"
    a.cerrar()


def test_anotar_media_no_borra_lo_que_ya_habia(tmp_path):
    a = Almacen(tmp_path / "a.db")
    ev_id = a.guardar("x")
    a.anotar_media(ev_id, miniatura="m.jpg")
    a.anotar_media(ev_id, clip="c.mp4")
    fila = a.historial()[0]
    assert fila["miniatura"] == "m.jpg" and fila["clip"] == "c.mp4"
    a.cerrar()


def test_no_revienta_si_el_evento_no_existe(tmp_path):
    a = Almacen(tmp_path / "a.db")
    a.anotar_media(9999, miniatura="m.jpg")
    a.anotar_media(None, miniatura="m.jpg")
    a.cerrar()


def test_migra_una_bd_antigua_sin_columnas(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(str(ruta))
    con.executescript("""
        CREATE TABLE eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marca_sistema DATETIME NOT NULL,
            marca_pantalla TEXT NOT NULL,
            tipo TEXT NOT NULL,
            id_tracking INTEGER,
            detalle TEXT
        );
        INSERT INTO eventos (marca_sistema, marca_pantalla, tipo)
        VALUES ('2026-10-01 10:00:00', '10:00:00', 'persona_entra');
    """)
    con.commit()
    con.close()

    a = Almacen(ruta)
    fila = a.historial()[0]
    assert fila["tipo"] == "persona_entra"
    assert fila["miniatura"] is None and fila["clip"] is None
    # y se puede escribir media en la BD migrada
    a.anotar_media(fila["id"], miniatura="m.jpg")
    assert a.historial()[0]["miniatura"] == "m.jpg"
    a.cerrar()


def test_eventos_del_dia_filtra_y_ordena(tmp_path):
    a = Almacen(tmp_path / "a.db")
    a.guardar("persona_salio", cuando="2026-10-27 18:00:00")
    a.guardar("persona_entra", cuando="2026-10-27 09:00:00")
    a.guardar("persona_entra", cuando="2026-10-26 09:00:00")
    evs = a.eventos_del_dia("2026-10-27")
    assert [e["tipo"] for e in evs] == ["persona_entra", "persona_salio"]
    assert a.eventos_del_dia("mal-formado") == []
    a.cerrar()


def test_contar_ultimo_y_tamano(tmp_path):
    a = Almacen(tmp_path / "a.db")
    assert a.contar() == 0 and a.ultimo_evento() is None
    a.guardar("persona_entra", cuando="2026-10-27 09:00:00")
    a.guardar("persona_salio", cuando="2026-10-27 09:10:00")
    assert a.contar() == 2
    assert a.ultimo_evento() == "2026-10-27 09:10:00"
    assert a.tamano_bytes() > 0
    a.cerrar()


def test_purgar_eventos_respeta_la_retencion(tmp_path):
    a = Almacen(tmp_path / "a.db")
    a.guardar("viejo", cuando="2020-01-01 10:00:00")
    a.guardar("nuevo", cuando="2026-10-27 10:00:00")
    assert a.purgar_eventos(30) == 1
    assert a.contar() == 1 and a.historial()[0]["tipo"] == "nuevo"
    assert a.purgar_eventos(0) == 0  # 0 = no purgar
    a.cerrar()
