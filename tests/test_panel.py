"""Contrato del panel profesional: estadisticas, logs, SSE, eventos y resumen."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import vigia.web as web
from vigia.almacen import Almacen


@pytest.fixture(scope="module")
def cliente():
    return TestClient(web.app, client=("127.0.0.1", 50000))


@pytest.fixture
def almacen(monkeypatch, tmp_path):
    a = Almacen(tmp_path / "panel.db")
    monkeypatch.setattr(web, "_almacen", a)
    yield a
    a.cerrar()


def _sembrar(a: Almacen) -> None:
    ahora = datetime.now()
    hoy = ahora.strftime("%Y-%m-%d")
    ayer = (ahora - timedelta(days=1)).strftime("%Y-%m-%d")
    a.guardar("persona_entra", 1, cuando=f"{hoy} 09:00:00")
    a.guardar("persona_salio", 1, cuando=f"{hoy} 09:10:00")
    a.guardar("persona_entra", 2, cuando=f"{hoy} 10:00:00")
    a.guardar("persona_salio", 2, cuando=f"{hoy} 10:05:00")
    a.guardar("permanencia", 1, "permanencia zona=puerta",
              cuando=f"{hoy} 09:05:00", zona="puerta")
    a.guardar("actividad", 1, "id 1 usando movil",
              cuando=f"{hoy} 09:00:00")
    a.guardar("actividad", 1, "id 1 usando movil",
              cuando=f"{hoy} 09:02:00")
    a.guardar("cruce_linea", 2, "linea=puerta a→b",
              cuando=f"{ayer} 23:00:00", camara_id="cam1")


# -- estadisticas -----------------------------------------------------------

def test_estadisticas_agrega_por_tipo_hora_dia_y_actividad(cliente, almacen):
    _sembrar(almacen)
    datos = cliente.get("/estadisticas?dias=7").json()
    assert datos["total"] == 8
    assert datos["por_tipo"]["persona_entra"] == 2
    assert datos["por_tipo"]["permanencia"] == 1
    assert datos["por_hora"][9] == 5  # 5 eventos a las 09:xx
    assert datos["por_hora"][10] == 2
    assert len(datos["por_dia"]) == 2
    assert datos["personas_distintas"] == 2
    assert datos["actividades"]["usando movil"] == pytest.approx(120.0,
                                                                 rel=0.01)
    assert datos["picos"]["hora"]["hora"] == 9
    assert datos["ocupacion_max"] >= 1


def test_estadisticas_valida_dias(cliente):
    assert cliente.get("/estadisticas?dias=0").json()["dias"] == 1
    assert cliente.get("/estadisticas?dias=abc").status_code == 422


# -- logs -------------------------------------------------------------------

def test_logs_parsea_y_cuenta_rotaciones(cliente, monkeypatch, tmp_path):
    (tmp_path / "vigia.log").write_text(
        "2026-10-29 10:00:00,001 INFO vigia.web: hola\n"
        "linea rota sin formato\n"
        "2026-10-29 10:00:01,002 ERROR vigia.eventos: fallo\n",
        encoding="utf-8")
    (tmp_path / "vigia.log.1").write_text("viejo\n", encoding="utf-8")
    monkeypatch.setattr(web, "_RUTA_LOGS", tmp_path)
    datos = cliente.get("/logs?limite=10").json()
    assert datos["rotaciones"] == 1
    assert datos["tamano_bytes"] > 0
    assert [l["nivel"] for l in datos["lineas"]] == ["ERROR", "?", "INFO"]
    assert datos["lineas"][0]["modulo"] == "vigia.eventos"
    assert datos["lineas"][1]["msg"] == "linea rota sin formato"
    solo_error = cliente.get("/logs?nivel=error").json()
    assert [l["msg"] for l in solo_error["lineas"]] == ["fallo"]


def test_logs_sin_fichero_no_falla(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_RUTA_LOGS", tmp_path / "noexiste")
    datos = cliente.get("/logs").json()
    assert datos == {"lineas": [], "tamano_bytes": 0, "rotaciones": 0}


# -- SSE --------------------------------------------------------------------

def test_sse_emite_evento_y_limpia_el_oyente(cliente):
    publicado = {}

    def publicar_cuando_haya_oyente():
        limite = time.monotonic() + 3
        while web._bus_eventos.oyentes() == 0 and time.monotonic() < limite:
            time.sleep(0.01)
        publicado["ok"] = True
        web._bus_eventos.publicar({
            "id": 7, "hora": "12:00:00", "tipo": "persona_entra",
            "etiqueta": "id 3", "camara_id": "0", "zona": None})

    hilo = threading.Thread(target=publicar_cuando_haya_oyente, daemon=True)
    hilo.start()
    with cliente.stream("GET", "/eventos/stream?limite=1") as respuesta:
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith(
            "text/event-stream")
        texto = "".join(respuesta.iter_text())
    hilo.join(2)
    assert publicado.get("ok") is True
    assert "event: evento" in texto
    datos = json.loads(texto.split("data: ", 1)[1].split("\n", 1)[0])
    assert datos["tipo"] == "persona_entra" and datos["id"] == 7
    tiempo = time.monotonic() + 2
    while web._bus_eventos.oyentes() and time.monotonic() < tiempo:
        time.sleep(0.01)
    assert web._bus_eventos.oyentes() == 0


def test_anotar_evento_publica_en_el_bus(tmp_path):
    from vigia.web import Estado
    a = Almacen(tmp_path / "sse.db")
    estado = Estado()
    cola = web._bus_eventos.suscribir()
    try:
        web._anotar_evento(
            estado, a, None, {"miniaturas": False, "notificar": False},
            None, "persona_entra", "id 3", id_tracking=3, camara_id="cam2")
        evento = cola.get(timeout=1)
        assert evento["tipo"] == "persona_entra"
        assert evento["camara_id"] == "cam2"
        assert evento["id"] == 1
    finally:
        web._bus_eventos.desuscribir(cola)
        a.cerrar()


# -- /eventos paginado ------------------------------------------------------

def test_eventos_sin_parametros_sigue_siendo_lista(cliente):
    assert isinstance(cliente.get("/eventos").json(), list)


def test_eventos_paginacion_filtros_y_total(cliente, almacen):
    _sembrar(almacen)
    datos = cliente.get("/eventos?limite=2").json()
    assert datos["total"] == 8 and len(datos["eventos"]) == 2
    assert datos["limite"] == 2
    siguiente = datos["siguiente_antes_id"]
    assert siguiente == datos["eventos"][-1]["id"]

    pagina2 = cliente.get(f"/eventos?limite=2&antes_id={siguiente}").json()
    ids1 = {e["id"] for e in datos["eventos"]}
    ids2 = {e["id"] for e in pagina2["eventos"]}
    assert ids1.isdisjoint(ids2)

    por_tipo = cliente.get("/eventos?tipo=cruce_linea").json()
    assert por_tipo["total"] == 1
    assert por_tipo["eventos"][0]["camara_id"] == "cam1"

    por_camara = cliente.get("/eventos?camara_id=cam1").json()
    assert por_camara["total"] == 1

    por_zona = cliente.get("/eventos?zona=puerta").json()
    assert por_zona["total"] == 1
    assert por_zona["eventos"][0]["tipo"] == "permanencia"

    por_persona = cliente.get("/eventos?persona=2").json()
    assert por_persona["total"] == 3  # entra, salio y cruce

    hoy = datetime.now().strftime("%Y-%m-%d")
    por_fecha = cliente.get(f"/eventos?desde={hoy}&hasta={hoy}").json()
    assert por_fecha["total"] == 7


def test_eventos_valida_fechas(cliente):
    r = cliente.get("/eventos?desde=ayer")
    assert r.status_code == 400
    assert r.json()["detail"] == r.json()["detalle"]


# -- resumen ----------------------------------------------------------------

def test_resumen_trae_todo_en_una_llamada(cliente, almacen):
    _sembrar(almacen)
    datos = cliente.get("/resumen").json()
    assert {"version", "uptime_seg", "camara", "camaras", "armado",
            "eventos_hoy", "ultimo_evento", "disco", "telegram",
            "aforo_actual", "total_eventos"} <= set(datos)
    assert datos["total_eventos"] == 8
    assert datos["eventos_hoy"]["total"] >= 1
    assert datos["ultimo_evento"]["tipo"] == "cruce_linea"
    assert "libre_gb" in datos["disco"] and "total_gb" in datos["disco"]
    assert "configurado" in datos["telegram"]


# -- errores unificados -----------------------------------------------------

def test_errores_llevan_detail_y_detalle(cliente):
    r = cliente.get("/no-existe")
    assert r.status_code == 404
    assert r.json()["detail"] == r.json()["detalle"]

    r = cliente.get("/eventos?desde=mal")
    assert r.status_code == 400
    assert r.json()["detail"] == r.json()["detalle"]

    r = cliente.get("/eventos?persona=abc")
    assert r.status_code == 422
    assert r.json()["detail"] == r.json()["detalle"]
