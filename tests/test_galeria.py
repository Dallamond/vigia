"""Galeria de caras: agrupado de desconocidos, tomas, nombrar y borrar."""
from __future__ import annotations

import numpy as np
import pytest

from vigia.almacen import Almacen
from vigia.galeria import MAX_TOMAS, GaleriaDesconocidos


def _emb(semilla: int, ruido: float = 0.0, base=None) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    v = rng.normal(size=128).astype(np.float32) if base is None else base.copy()
    if ruido:
        v = v + np.random.default_rng(semilla + 99).normal(scale=ruido, size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def _ficha(emb) -> dict:
    return {"emb": emb, "jpeg": b"\xff\xd8fake", "t": 0.0}


@pytest.fixture
def almacen(tmp_path):
    a = Almacen(tmp_path / "t.db")
    yield a
    a.cerrar() if hasattr(a, "cerrar") else None


def test_misma_persona_un_grupo_y_otra_persona_otro(almacen):
    g = GaleriaDesconocidos(almacen, cada_seg=0)
    a = _emb(1)
    g.ver(_ficha(a), ahora=0)
    g.ver(_ficha(_emb(2, 0.07, base=a)), ahora=10)   # parecida a `a`
    g.ver(_ficha(_emb(3)), ahora=20)                 # otra distinta
    grupos = almacen.listar_desconocidos()
    assert len(grupos) == 2
    assert sorted(x["veces"] for x in grupos) == [1, 2]


def test_tomas_limitadas_y_espaciadas(almacen):
    g = GaleriaDesconocidos(almacen, cada_seg=0)
    a = _emb(1)
    for i in range(40):
        g.ver(_ficha(_emb(i + 10, 0.07, base=a)), ahora=i * 10.0)
    (grupo,) = almacen.listar_desconocidos()
    assert 1 <= len(grupo["tomas"]) <= MAX_TOMAS


def test_throttle_ignora_caras_muy_seguidas(almacen):
    g = GaleriaDesconocidos(almacen, cada_seg=5)
    g.ver(_ficha(_emb(1)), ahora=0)
    g.ver(_ficha(_emb(2)), ahora=1)
    assert len(almacen.listar_desconocidos()) == 1


def test_nombrar_promueve_las_tomas_a_rostros_con_foto(almacen):
    g = GaleriaDesconocidos(almacen, cada_seg=0)
    g.ver(_ficha(_emb(1)), ahora=0)
    (grupo,) = almacen.listar_desconocidos()
    assert almacen.promover_desconocido(grupo["id"], "Ana") == 1
    assert almacen.listar_desconocidos() == []
    (r,) = almacen.rostros_con_foto()
    assert r["nombre"] == "Ana" and r["foto"] is True
    assert almacen.foto_rostro(r["id"]) == b"\xff\xd8fake"


def test_borrar_persona_y_renombrar(almacen):
    almacen.guardar_rostro("Ana", _emb(1).tobytes(), b"x")
    almacen.guardar_rostro("Ana", _emb(2).tobytes())
    assert almacen.renombrar_persona("Ana", "Bea") == 2
    assert almacen.borrar_persona("Bea") == 2
    assert almacen.rostros() == []


def test_endpoints_galeria(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vigia import web

    a = Almacen(tmp_path / "w.db")
    monkeypatch.setattr(web, "_almacen", a)
    monkeypatch.setattr(web, "_galeria", GaleriaDesconocidos(a, cada_seg=0))
    web._galeria.ver(_ficha(_emb(5)), ahora=0)
    c = TestClient(web.app, client=("127.0.0.1", 50000))
    d = c.get("/caras/personas").json()
    assert d["conocidas"] == [] and len(d["desconocidas"]) == 1
    gid = d["desconocidas"][0]["id"]
    tid = d["desconocidas"][0]["tomas"][0]
    assert c.get(f"/caras/foto/toma/{tid}").content == b"\xff\xd8fake"
    assert c.post(f"/caras/desconocidas/{gid}/nombrar", json={"nombre": " "}).status_code == 400
    assert c.post(f"/caras/desconocidas/{gid}/nombrar", json={"nombre": "Ana"}).status_code == 200
    d = c.get("/caras/personas").json()
    assert d["desconocidas"] == [] and d["conocidas"][0]["nombre"] == "Ana"
    assert c.delete("/caras/persona", params={"nombre": "Ana"}).status_code == 200
    assert c.delete("/caras/persona", params={"nombre": "Ana"}).status_code == 404
