"""Tests de la copia de seguridad (scripts/backup.py)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import backup  # noqa: E402

from vigia.almacen import Almacen  # noqa: E402


def _base_con_datos(ruta: Path) -> None:
    almacen = Almacen(ruta)
    try:
        almacen.guardar("persona_entra", id_tracking=7, detalle="prueba")
        almacen.guardar_rostro("Ana", b"\x00" * 128)
        almacen.guardar_rostro("Luis", b"\x01" * 128)
    finally:
        almacen.cerrar()


def test_backup_incluye_eventos_y_rostros(tmp_path):
    origen = tmp_path / "vigia.db"
    _base_con_datos(origen)
    config = tmp_path / "config.yaml"
    config.write_text("puerto: 8000\n", encoding="utf-8")

    resumen = backup.hacer_backup(origen, tmp_path / "copias", conservar=5,
                                  config=config)

    copia = Path(resumen["copia"])
    assert copia.is_file()
    assert resumen["integridad"] == "ok"
    assert resumen["eventos"] == 1
    assert resumen["rostros"] == 2
    assert Path(resumen["config"]).read_text(encoding="utf-8") == "puerto: 8000\n"

    con = sqlite3.connect(str(copia))
    try:
        assert con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0] == 1
        nombres = {f[0] for f in con.execute("SELECT nombre FROM rostros")}
        assert nombres == {"Ana", "Luis"}
    finally:
        con.close()


def test_rotacion_conserva_las_mas_recientes(tmp_path):
    destino = tmp_path / "copias"
    destino.mkdir()
    for i in range(1, 6):
        (destino / f"vigia-2026010{i}-000000.db").write_bytes(b"x")
        (destino / f"vigia-2026010{i}-000000-config.yaml").write_text("x")
    # Una copia interrumpida no debe sobrevivir.
    (destino / ".vigia-20260106-000000.db.tmp").write_bytes(b"x")

    borradas = backup.rotar(destino, 2)

    quedan = sorted(p.name for p in destino.glob("vigia-*.db"))
    assert quedan == ["vigia-20260104-000000.db", "vigia-20260105-000000.db"]
    assert not (destino / "vigia-20260103-000000-config.yaml").exists()
    assert (destino / "vigia-20260105-000000-config.yaml").exists()
    assert not (destino / ".vigia-20260106-000000.db.tmp").exists()
    assert len(borradas) == 7  # 3 bases + 3 configs + 1 temporal


def test_backup_falla_si_no_hay_base(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.hacer_backup(tmp_path / "no-existe.db", tmp_path / "copias")


def test_no_borra_ficheros_ajenos(tmp_path):
    destino = tmp_path / "copias"
    destino.mkdir()
    ajeno = destino / "notas.txt"
    ajeno.write_text("no tocar")
    for i in range(1, 4):
        (destino / f"vigia-2026010{i}-000000.db").write_bytes(b"x")
    backup.rotar(destino, 1)
    assert ajeno.exists()
    assert len(list(destino.glob("vigia-*.db"))) == 1
