"""Tests de caras: identidad por embedding en SQLite (sin modelos ni webcam)."""
import numpy as np
import pytest

from vigia.almacen import Almacen
from vigia.caras import EMB_DIM, _SIM_MIN, CarasDetector, embedding_valido  # noqa: F401


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


def _detector_sin_modelos(conocidos):
    """CarasDetector sin cargar los .onnx: solo la parte de identidad."""
    d = object.__new__(CarasDetector)
    d.conocidos = {}
    d.set_conocidos(conocidos)
    return d


def _vec(*indices):
    v = np.zeros(EMB_DIM, np.float32)
    for i in indices:
        v[i] = 1.0
    return v


def test_identificar_reconoce_al_conocido_y_rechaza_al_desconocido():
    d = _detector_sin_modelos({"Lucas": _vec(0, 1), "Ana": _vec(5, 6)})
    nombre, sim = d.identificar(_vec(0, 1) + 0.01)
    assert nombre == "Lucas" and sim > 0.9
    nombre, sim = d.identificar(_vec(50, 51))
    assert nombre is None and sim < 0.4


def test_identificar_usa_la_mejor_de_varias_muestras():
    d = _detector_sin_modelos({"Lucas": [_vec(0, 1), _vec(10, 11)]})
    assert d.identificar(_vec(10, 11))[0] == "Lucas"


def test_identificar_sin_conocidos_o_con_vector_malo():
    d = _detector_sin_modelos({})
    assert d.identificar(_vec(0)) == (None, 0.0)
    d = _detector_sin_modelos({"A": _vec(0)})
    assert d.identificar(np.ones(7, np.float32)) == (None, 0.0)


def test_set_conocidos_descarta_embeddings_invalidos():
    d = _detector_sin_modelos({"ok": _vec(0), "corto": np.ones(5, np.float32),
                               "nan": np.full(EMB_DIM, np.nan, np.float32)})
    assert list(d.conocidos) == ["ok"]


def test_embedding_valido():
    assert embedding_valido([0.5] * EMB_DIM).dtype == np.float32
    for malo in ([1, 2, 3], ["a"] * EMB_DIM, [float("nan")] * EMB_DIM, None):
        with pytest.raises(ValueError):
            embedding_valido(malo)
