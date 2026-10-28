"""Copia de seguridad de la base de datos de Vigia.

Usa la API de backup de SQLite (no copia el fichero a pelo) para que la copia
sea consistente aunque el servicio esté escribiendo, incluido el WAL. La base
contiene los eventos y la tabla `rostros` (caras conocidas), así que ambas
quedan dentro del respaldo.

Uso:
    python scripts/backup.py
    python scripts/backup.py --destino D:\\copias\\vigia --conservar 30
    python scripts/backup.py --config config.yaml --sin-config

Rotación: conserva las N copias más recientes (`--conservar`, 14 por defecto)
y borra las antiguas junto con su copia de config.yaml.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TABLAS_ESPERADAS = ("eventos", "rostros")


def _sello() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _nombre_libre(destino: Path, sello: str) -> str:
    """Evita pisar una copia si se lanzan dos en el mismo segundo."""
    candidato = sello
    n = 2
    while (destino / f"vigia-{candidato}.db").exists():
        candidato = f"{sello}-{n}"
        n += 1
    return candidato


def hacer_backup(origen: str | Path, destino: str | Path,
                 conservar: int = 14, config: str | Path | None = None,
                 copiar_config: bool = True) -> dict:
    """Crea una copia consistente y rota las antiguas. Devuelve un resumen."""
    origen = Path(origen).resolve()
    destino = Path(destino).resolve()
    if not origen.is_file():
        raise FileNotFoundError(f"no existe la base de datos: {origen}")
    destino.mkdir(parents=True, exist_ok=True)

    sello = _nombre_libre(destino, _sello())
    final = destino / f"vigia-{sello}.db"
    tmp = destino / f".vigia-{sello}.db.tmp"

    fuente = sqlite3.connect(str(origen))
    copia = sqlite3.connect(str(tmp))
    try:
        fuente.backup(copia)
        copia.commit()
        integridad = copia.execute("PRAGMA quick_check").fetchone()
        if not integridad or integridad[0] != "ok":
            raise RuntimeError(f"la copia no pasó quick_check: {integridad!r}")
        tablas = {fila[0] for fila in copia.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        faltan = [t for t in TABLAS_ESPERADAS if t not in tablas]
        if faltan:
            raise RuntimeError(f"faltan tablas en la copia: {', '.join(faltan)}")
        rostros = copia.execute("SELECT COUNT(*) FROM rostros").fetchone()[0]
        eventos = copia.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
    finally:
        copia.close()
        fuente.close()

    tmp.replace(final)
    resumen = {
        "copia": str(final),
        "bytes": final.stat().st_size,
        "eventos": eventos,
        "rostros": rostros,
        "integridad": "ok",
        "config": None,
    }

    if copiar_config:
        ruta_config = Path(config).resolve() if config else (origen.parent / "config.yaml")
        if ruta_config.is_file():
            copia_config = destino / f"vigia-{sello}-config.yaml"
            shutil.copy2(ruta_config, copia_config)
            resumen["config"] = str(copia_config)

    resumen["borradas"] = [str(p) for p in rotar(destino, conservar)]
    return resumen


def rotar(destino: str | Path, conservar: int) -> list[Path]:
    """Borra las copias más antiguas. Solo toca `vigia-*.db` y su config."""
    if conservar < 1:
        raise ValueError("conservar debe ser >= 1")
    destino = Path(destino)
    copias = sorted(destino.glob("vigia-*.db"), key=lambda p: p.name, reverse=True)
    borradas: list[Path] = []
    for vieja in copias[conservar:]:
        config = vieja.with_name(vieja.stem + "-config.yaml")
        for ruta in (vieja, config):
            try:
                ruta.unlink()
                borradas.append(ruta)
            except FileNotFoundError:
                pass
    # Restos de una copia interrumpida (nunca se consideran válidos).
    for tmp in destino.glob(".vigia-*.db.tmp"):
        try:
            tmp.unlink()
            borradas.append(tmp)
        except FileNotFoundError:
            pass
    return borradas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origen", default=str(RAIZ / "vigia.db"),
                        help="base de datos a copiar (por defecto vigia.db)")
    parser.add_argument("--destino", default=str(RAIZ / "backups"),
                        help="carpeta de copias (por defecto backups/)")
    parser.add_argument("--conservar", type=int, default=14,
                        help="número de copias que se conservan (14 por defecto)")
    parser.add_argument("--config", default=str(RAIZ / "config.yaml"),
                        help="config.yaml que se copia junto a la base")
    parser.add_argument("--sin-config", action="store_true",
                        help="no copiar config.yaml")
    args = parser.parse_args(argv)

    try:
        resumen = hacer_backup(args.origen, args.destino,
                               conservar=args.conservar,
                               config=args.config,
                               copiar_config=not args.sin_config)
    except (OSError, sqlite3.Error, RuntimeError) as e:
        print(f"backup: error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
