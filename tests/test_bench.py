"""Tests de las funciones puras de bench/benchmark.py."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "bench"))

import benchmark  # noqa: E402


def test_percentil_interpola():
    valores = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert benchmark.percentil(valores, 0) == 10.0
    assert benchmark.percentil(valores, 100) == 50.0
    assert benchmark.percentil(valores, 50) == 30.0
    assert benchmark.percentil([1.0, 2.0], 50) == 1.5
    assert benchmark.percentil([], 95) == 0.0


def test_estimar_camaras_con_margen():
    r = benchmark.estimar_camaras(100.0, margen=0.8)
    assert r["fps_disponibles"] == 80.0
    assert r["a_10_fps"] == 8
    assert r["a_15_fps"] == 5
    assert benchmark.estimar_camaras(12.0)["a_15_fps"] == 0


def test_fuente_sintetica_devuelve_lote_del_tamano_pedido():
    fuente = benchmark.Fuente("sintetico", ancho=320, alto=240)
    try:
        lote = fuente.lote(3)
    finally:
        fuente.cerrar()
    assert len(lote) == 3
    assert lote[0].shape == (240, 320, 3)
