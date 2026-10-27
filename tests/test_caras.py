"""Tests de caras: identidad por embedding en SQLite (sin modelos ni webcam)."""
import numpy as np
import pytest

from vigia.almacen import Almacen
from vigia.caras import _SIM_MIN  # noqa: F401


def test_rostro_persiste(tmp_path):
    a = Almacen(tmp_path / "r.db")
    emb = np.random.rand(128).astype(np.float32).tobytes()
    pid = a.guardar_rostro("Lucas", emb)
    rs = a.rostros()
    assert len(rs) == 1 and rs[0]["nombre"] == "Lucas" and rs[0]["id"] == pid
    assert rs[0]["embedding"] == emb
    a.cerrar()


def test_borrar_rostro(tmp_path):
    a = Almacen(tmp_path / "r.db")
    pid = a.guardar_rostro("A", b"123")
    assert a.borrar_rostro(pid) is True
    assert a.borrar_rostro(pid) is False
    assert a.rostros() == []
    a.cerrar()


def test_identificar_selecciona_evmejor_sim(tmp_path):
    """Con embeddings sinteticos: similitud alta vs baja."""
    # vector base casi identico
    base = np.array([1.0, 0.0, 0.5, 0.0, 2.0], dtype=np.float32)
    conocidos = {"L": base / np.linalg.norm(base)}
    emb_nuevo = (base + 0.01 * np.random.rand(5)).astype(np.float32)
    emb_nuevo /= np.linalg.norm(emb_nuevo)
    best = max(float(np.dot(emb_nuevo, v)) for v in conocidos.values())
    assert best >= _SIM_MIN
    otro = np.array([0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    assert max(float(np.dot(otro, v)) for v in conocidos.values()) < _SIM_MIN
