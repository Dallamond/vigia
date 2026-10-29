"""Tests de seguridad: token, cookie, middleware y rutas publicas."""
import os

import pytest
from fastapi.testclient import TestClient

import vigia.web as web
from vigia.seguridad import (Seguridad, cargar_env, comprobar_arranque,
                            es_loopback)

LOCAL = ("127.0.0.1", 50000)


def _cliente(ip=LOCAL):
    return TestClient(web.app, client=ip)


@pytest.fixture
def con_token(monkeypatch):
    s = Seguridad(token="secreto", max_intentos=3, ventana_seg=60)
    monkeypatch.setattr(web, "_seguridad", s)
    return s


@pytest.fixture
def sin_token(monkeypatch):
    monkeypatch.setattr(web, "_seguridad", Seguridad(token=None))
    return _cliente()


# -- unidades ---------------------------------------------------------------

def test_cargar_env_no_pisa_el_entorno(tmp_path, monkeypatch):
    monkeypatch.delenv("VIGIA_TOKEN", raising=False)
    monkeypatch.delenv("OTRA", raising=False)
    ruta = tmp_path / ".env"
    ruta.write_text("# comentario\nVIGIA_TOKEN=\"abc\"\nOTRA='x'\nMALA\n",
                    encoding="utf-8")
    leidas = cargar_env(ruta)
    assert leidas == {"VIGIA_TOKEN": "abc", "OTRA": "x"}
    assert os.environ["VIGIA_TOKEN"] == "abc"
    monkeypatch.setenv("OTRA", "del-sistema")
    cargar_env(ruta)
    assert os.environ["OTRA"] == "del-sistema"  # el entorno gana


def test_loopback_y_arranque_sin_token():
    assert es_loopback("127.0.0.1") and es_loopback("localhost")
    assert es_loopback("::1") and es_loopback("::ffff:127.0.0.1")
    assert not es_loopback("0.0.0.0") and not es_loopback("192.168.1.5")
    comprobar_arranque("127.0.0.1", None)
    comprobar_arranque("0.0.0.0", "token")
    with pytest.raises(RuntimeError, match="VIGIA_TOKEN"):
        comprobar_arranque("0.0.0.0", None)


def test_seguridad_valida_cabecera_cookie_y_query():
    s = Seguridad(token="abc")
    assert s.credencial_valida("Bearer abc")
    assert not s.credencial_valida("Bearer abd")
    assert s.credencial_valida(cookie="abc")
    assert s.credencial_valida(token_query="abc")
    assert not s.credencial_valida("Basic abc")
    assert not Seguridad(token=None).requerida


def test_limitacion_de_intentos_por_ip():
    t = {"v": 100.0}
    s = Seguridad(token="x", max_intentos=2, ventana_seg=10,
                  reloj=lambda: t["v"])
    assert s.permitir_login("1.2.3.4")
    s.fallo_login("1.2.3.4")
    s.fallo_login("1.2.3.4")
    assert not s.permitir_login("1.2.3.4")
    assert s.permitir_login("5.6.7.8")  # otra IP no se ve afectada
    t["v"] += 11
    assert s.permitir_login("1.2.3.4")  # la ventana caduca
    s.fallo_login("1.2.3.4")
    s.exito_login("1.2.3.4")
    assert s.permitir_login("1.2.3.4")


# -- middleware y contrato de auth -----------------------------------------

def test_sin_token_solo_localhost(sin_token):
    assert sin_token.get("/estado").status_code == 200
    remoto = _cliente(("192.168.1.20", 50000))
    r = remoto.get("/estado")
    assert r.status_code == 403 and "localhost" in r.json()["detalle"]
    # el HTML sigue disponible para poder mostrar el panel
    assert remoto.get("/").status_code == 200


def test_api_sin_credencial_devuelve_401(con_token):
    r = _cliente().get("/estado")
    assert r.status_code == 401
    assert r.json()["detalle"] == "no autenticado"
    assert r.json()["detail"] == "no autenticado"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_auth_estado_es_siempre_accesible(con_token):
    datos = _cliente().get("/auth/estado").json()
    assert datos == {"auth_requerida": True, "autenticado": False}


def test_login_correcto_pone_cookie_y_logout_la_borra(con_token):
    c = _cliente()
    r = c.post("/auth/login", json={"token": "malo"})
    assert r.status_code == 401
    r = c.post("/auth/login", json={"token": "secreto"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "vigia_token=" in cookie and "httponly" in cookie
    assert "samesite=lax" in cookie
    assert c.get("/auth/estado").json()["autenticado"] is True
    assert c.get("/estado").status_code == 200
    assert c.post("/auth/logout").status_code == 200
    assert c.get("/estado").status_code == 401


def test_bearer_y_query_solo_en_el_video(con_token):
    c = _cliente()
    assert c.get("/estado",
                 headers={"Authorization": "Bearer secreto"}).status_code == 200
    # query en un endpoint normal: no vale
    assert c.get("/estado?token=secreto").status_code == 401
    # en snapshot si vale (aqui no hay frame: 404, pero pasa el middleware)
    assert c.get("/snapshot.jpg?token=secreto").status_code == 404
    assert c.get("/snapshot.jpg?token=malo").status_code == 401


def test_login_se_bloquea_tras_varios_fallos(con_token):
    c = _cliente()
    for _ in range(3):
        assert c.post("/auth/login", json={"token": "x"}).status_code == 401
    assert c.post("/auth/login", json={"token": "x"}).status_code == 429
    # aun con el token bueno, la IP esta bloqueada hasta que pase la ventana
    assert c.post("/auth/login", json={"token": "secreto"}).status_code == 429


def test_html_y_estaticos_son_publicos(con_token):
    c = _cliente()
    assert c.get("/").status_code == 200
    # el panel nuevo referencia ficheros static: no deben pedir token
    for ruta in ("/static/no-existe.js", "/static/panel.css"):
        assert c.get(ruta).status_code in (200, 404)
    assert c.get("/no-existe").status_code == 401


def test_endpoints_sensibles_exigen_token(con_token, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    c = _cliente()
    casos = [
        ("post", "/config", {"permanencia_seg": 31}),
        ("put", "/zonas", {"zonas": {}}),
        ("post", "/caras/registro", {"nombre": "x"}),
        ("delete", "/caras/registro/1", None),
        ("post", "/telegram/prueba", None),
        ("post", "/armado", {"armado": True}),
    ]
    for metodo, ruta, cuerpo in casos:
        r = getattr(c, metodo)(ruta, json=cuerpo) if cuerpo is not None else \
            getattr(c, metodo)(ruta)
        assert r.status_code == 401, (metodo, ruta, r.status_code)


def test_endpoints_sensibles_con_token_no_dan_401(con_token, monkeypatch,
                                                  tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    c = _cliente()
    cab = {"Authorization": "Bearer secreto"}
    assert c.put("/zonas", json={"zonas": {}}, headers=cab).status_code == 200
    assert c.post("/config", json={"permanencia_seg": 31},
                  headers=cab).status_code == 200
    # sin token de telegram: 409, pero autenticado
    assert c.post("/telegram/prueba", headers=cab).status_code == 409
    assert c.post("/armado", json={"armado": True},
                  headers=cab).status_code == 200


def test_host_y_puerto_entorno_gana_a_config():
    cfg = {"host": "127.0.0.1", "puerto": 8000}
    assert web._host_y_puerto(cfg, {}) == ("127.0.0.1", 8000)
    assert web._host_y_puerto(cfg, {"VIGIA_HOST": "0.0.0.0",
                                    "VIGIA_PUERTO": "8010"}) == \
        ("0.0.0.0", 8010)
    assert web._host_y_puerto(cfg, {"VIGIA_PUERTO": "malo"})[1] == 8000


def test_path_traversal_en_clip_y_media_es_404(monkeypatch, tmp_path):
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (tmp_path / "secreto.jpg").write_bytes(b"x")

    class Clips:
        def __init__(self, c):
            self.carpeta = c

    monkeypatch.setattr(web, "_clips", Clips(carpeta))
    for malo in ("../secreto.jpg", "..\\secreto.jpg", "..%2Fsecreto.jpg",
                 ".oculto.jpg", "sub/dir.jpg"):
        with pytest.raises(Exception):
            web.media(malo)
        with pytest.raises(Exception):
            web.clip(malo)
    with pytest.raises(Exception):
        web.media("notas.txt")
