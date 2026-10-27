"""Tests del dashboard web: no requieren camara ni modelo real."""
import threading
import time

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import vigia.web as web
from vigia.almacen import Almacen
from vigia.detector import Deteccion
from vigia.web import Estado


@pytest.fixture(scope="module")
def cliente():
    return TestClient(web.app)


class _CamaraFalsa:
    def __init__(self, disponible=True):
        self.disponible = disponible
        self.alto, self.ancho = 240, 320
        self.lecturas = 0

    def leer(self):
        self.lecturas += 1
        if not self.disponible:
            return False, None
        return True, np.zeros((self.alto, self.ancho, 3), np.uint8)


class _DetectorFalso:
    def __init__(self, error=False):
        self.error = error
        self.llamadas = 0

    def detectar_track(self, frame):
        self.llamadas += 1
        if self.error:
            raise RuntimeError("fallo simulado de inferencia")
        return [], {}


def _lanzar_bucle(estado, cam, detector, parar):
    hilo = threading.Thread(
        target=web.bucle_procesamiento,
        args=(estado,),
        kwargs={"cam": cam, "detector": detector, "parar": parar},
        daemon=True)
    hilo.start()
    return hilo


def test_index_carga(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Vigia" in r.text
    # debe servirse el panel nuevo (static/index.html), no el minimo
    assert 'id="segSize"' in r.text
    assert 'id="zCanvas"' in r.text


def test_estado_tiene_el_formato_esperado(cliente):
    r = cliente.get("/estado")
    assert r.status_code == 200
    datos = r.json()
    assert {"fps", "camara_ok", "personas", "objetos",
            "ancho", "alto"} <= set(datos)
    assert datos["fps"] >= 0
    assert isinstance(datos["personas"], int)
    assert datos["ancho"] > 0 and datos["alto"] > 0


def test_eventos_tiene_el_formato_esperado(cliente):
    r = cliente.get("/eventos")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_estado_cuenta_personas_y_objetos():
    estado = Estado()
    estado.actualizar(
        b"jpeg", 12.5,
        [Deteccion("person", 0.9, (0, 0, 10, 10)),
         Deteccion("laptop", 0.8, (0, 0, 10, 10))],
        {7}, True)
    _, fps, dets, ids, ok = estado.snapshot()
    assert fps == 12.5 and ok and len(dets) == 2 and ids == {7}


def test_snapshot_devuelve_copias():
    estado = Estado()
    estado.actualizar(b"", 0.0, [Deteccion("person", 0.9, (0, 0, 1, 1))],
                      {3}, True)
    _, _, dets, ids, _ = estado.snapshot()
    dets.clear()
    ids.clear()
    _, _, dets2, ids2, _ = estado.snapshot()
    assert len(dets2) == 1 and ids2 == {3}


def test_anadir_eventos_conserva_orden_reciente_primero():
    estado = Estado()
    estado.anadir_eventos((("persona_entra", 1),))
    estado.anadir_eventos((("persona_salio", 1),))
    assert [tipo for _, tipo, _ in estado.eventos] == ["persona_salio",
                                                      "persona_entra"]


def test_jpeg_es_una_imagen_valida():
    frame = np.zeros((48, 64, 3), np.uint8)
    jpeg = web._jpeg(frame)
    assert jpeg[:2] == b"\xff\xd8"


def test_bucle_sobrevive_a_un_error_de_inferencia():
    estado = Estado()
    detector = _DetectorFalso(error=True)
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(), detector, parar)
    try:
        limite = time.monotonic() + 5
        while detector.llamadas < 2 and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    assert not hilo.is_alive(), "el hilo murio con la excepcion"
    assert detector.llamadas >= 2, "dejo de procesar frames tras el error"
    assert estado.snapshot()[4] is False


def test_bucle_marca_camara_ok_con_frame_valido():
    estado = Estado()
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(), _DetectorFalso(), parar)
    try:
        limite = time.monotonic() + 5
        while not estado.snapshot()[4] and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    jpeg, _, _, _, camara_ok = estado.snapshot()
    assert camara_ok is True
    assert jpeg


def test_bucle_avisa_si_la_camara_esta_caida():
    estado = Estado()
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(disponible=False),
                         _DetectorFalso(), parar)
    try:
        limite = time.monotonic() + 5
        while estado.snapshot()[0] is None and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    jpeg, _, _, _, camara_ok = estado.snapshot()
    assert camara_ok is False
    assert jpeg


class _ClipsFalso:
    def __init__(self, carpeta):
        self.carpeta = carpeta


def test_bucle_avisa_si_el_modelo_no_carga(monkeypatch):
    class DetectorRoto:
        def __init__(self, *a, **k):
            raise RuntimeError("sin pesos yolo11n.pt")

    monkeypatch.setattr(web, "Detector", DetectorRoto)
    estado = Estado()
    web.bucle_procesamiento(estado, cam=_CamaraFalsa(), detector=None,
                            config={"timeout_persona_seg": 10},
                            motor=object())
    jpeg, fps, dets, ids, camara_ok = estado.snapshot()
    assert jpeg and camara_ok is False and dets == [] and fps == 0.0


def test_historial_limita_el_limite(monkeypatch, cliente):
    visto = {}

    class AlmacenFalso:
        def historial(self, limite=100, tipo=None, desde=None):
            visto["limite"] = limite
            return []

    monkeypatch.setattr(web, "_almacen", AlmacenFalso())
    assert cliente.get("/historial?limite=-1").status_code == 200
    assert 1 <= visto["limite"] <= 500
    assert cliente.get("/historial?limite=99999").status_code == 200
    assert visto["limite"] == 500


def test_clip_rechaza_lo_que_no_es_mp4(monkeypatch, tmp_path):
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (carpeta / "notas.txt").write_bytes(b"hola")
    monkeypatch.setattr(web, "_clips", _ClipsFalso(carpeta))
    with pytest.raises(HTTPException) as exc:
        web.clip("notas.txt")
    assert exc.value.status_code in (400, 404)


def test_clip_rechaza_salir_de_la_carpeta(monkeypatch, tmp_path):
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (tmp_path / "secreto.mp4").write_bytes(b"x")
    monkeypatch.setattr(web, "_clips", _ClipsFalso(carpeta))
    for malo in ["../secreto.mp4", "..\\secreto.mp4", "noexiste.mp4"]:
        with pytest.raises(HTTPException) as exc:
            web.clip(malo)
        assert exc.value.status_code in (400, 404)


def test_clip_sirve_un_mp4_de_la_carpeta(monkeypatch, cliente, tmp_path):
    carpeta = tmp_path / "clips"
    carpeta.mkdir()
    (carpeta / "a.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42datos")
    monkeypatch.setattr(web, "_clips", _ClipsFalso(carpeta))
    r = cliente.get("/clip/a.mp4")
    assert r.status_code == 200
    assert r.content.startswith(b"\x00\x00\x00\x18ftyp")


def test_clip_404_si_clips_desactivados(monkeypatch):
    monkeypatch.setattr(web, "_clips", None)
    with pytest.raises(HTTPException) as exc:
        web.clip("a.mp4")
    assert exc.value.status_code == 404


def test_salud_incluye_disco_bd_y_clips(monkeypatch, cliente, tmp_path):
    a = Almacen(tmp_path / "s.db")
    a.guardar("persona_entra")
    monkeypatch.setattr(web, "_almacen", a)
    datos = cliente.get("/salud").json()
    assert {"version", "uptime_seg", "eventos", "db_bytes", "clips",
            "disco_libre_gb", "disco_total_gb"} <= set(datos)
    assert datos["eventos"] == 1
    assert datos["db_bytes"] > 0
    assert datos["disco_libre_gb"] >= 0
    a.cerrar()


def test_timeline_devuelve_solo_ese_dia(monkeypatch, cliente, tmp_path):
    a = Almacen(tmp_path / "t.db")
    a.guardar("persona_entra", cuando="2026-10-27 09:00:00")
    a.guardar("persona_salio", cuando="2026-10-27 09:30:00")
    a.guardar("persona_entra", cuando="2026-10-26 09:00:00")
    monkeypatch.setattr(web, "_almacen", a)
    datos = cliente.get("/timeline?dia=2026-10-27").json()
    assert datos["dia"] == "2026-10-27"
    assert datos["total"] == 2
    assert [e["tipo"] for e in datos["eventos"]] == ["persona_entra",
                                                     "persona_salio"]
    a.cerrar()


def test_timeline_sin_dia_usa_hoy(cliente):
    datos = cliente.get("/timeline").json()
    assert "dia" in datos and isinstance(datos["eventos"], list)


def test_zonas_get_devuelve_dimensiones(cliente):
    datos = cliente.get("/zonas").json()
    assert isinstance(datos["zonas"], dict)
    assert datos["ancho"] > 0 and datos["alto"] > 0


def test_zonas_put_aplica_y_persiste(monkeypatch, cliente, tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    zonas_antes = dict(web._motor_compartido.zonas)
    cfg_antes = dict(web._config)
    try:
        r = cliente.put("/zonas", json={"zonas": {"puerta": [0, 0, 120, 90]}})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert web._motor_compartido.zonas == {"puerta": (0, 0, 120, 90)}
        assert (tmp_path / "config.yaml").exists()

        r = cliente.put("/zonas", json={"zonas": {"mala": [1, 2]}})
        assert r.status_code == 200 and r.json()["ok"] is False
    finally:
        web._motor_compartido.zonas = zonas_antes
        web._config.clear()
        web._config.update(cfg_antes)


def test_config_rechaza_un_booleano_inventado(monkeypatch, cliente, tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    r = cliente.post("/config", json={"notificar": "quizas"})
    assert r.status_code == 400


def test_config_acepta_texto_booleano_y_lo_interpreta(monkeypatch, cliente,
                                                      tmp_path):
    monkeypatch.setattr(web, "_RUTA_CONFIG", tmp_path / "config.yaml")
    cfg_antes = dict(web._config)
    try:
        r = cliente.post("/config", json={"notificar": "false"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert web._config["notificar"] is False
    finally:
        web._config.clear()
        web._config.update(cfg_antes)


def test_media_sirve_jpg_y_rechaza_otros(monkeypatch, cliente, tmp_path):
    carpeta = tmp_path / "media"
    carpeta.mkdir()
    (carpeta / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    (carpeta / "notas.txt").write_bytes(b"hola")
    monkeypatch.setattr(web, "_clips", _ClipsFalso(carpeta))

    r = cliente.get("/media/a.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    with pytest.raises(HTTPException):
        web.media("notas.txt")
    with pytest.raises(HTTPException):
        web.media("../secreto.jpg")


class _ClipsConMedia:
    def __init__(self, carpeta):
        self.carpeta = carpeta

    def guardar_miniatura(self, frame, tipo):
        return self.carpeta / f"{tipo}_min.jpg"

    def guardar(self, tipo):
        return self.carpeta / f"{tipo}.mp4"


def test_anotar_evento_enlaza_miniatura_y_clip(tmp_path):
    a = Almacen(tmp_path / "a.db")
    clips = _ClipsConMedia(tmp_path)
    estado = Estado()
    ev_id = web._anotar_evento(
        estado, a, clips,
        {"miniaturas": True, "guardar_clips": True, "notificar": False},
        np.zeros((8, 8, 3), np.uint8), "persona_entra", "id 4",
        id_tracking=4)
    assert ev_id > 0
    fila = a.historial()[0]
    assert fila["miniatura"].endswith("_min.jpg")
    assert fila["clip"].endswith(".mp4")
    assert estado.eventos[0][1] == "persona_entra"
    a.cerrar()


def test_anotar_evento_reutiliza_el_id_del_motor(tmp_path):
    a = Almacen(tmp_path / "a.db")
    clips = _ClipsConMedia(tmp_path)
    previo = a.guardar("permanencia", detalle="x")
    estado = Estado()
    web._anotar_evento(estado, a, clips,
                       {"miniaturas": True, "guardar_clips": False},
                       np.zeros((4, 4, 3), np.uint8), "permanencia", "x",
                       evento_id=previo)
    assert a.contar() == 1  # no se duplica el evento
    assert a.historial()[0]["miniatura"].endswith("_min.jpg")
    a.cerrar()


# -- regresiones: media, salud y persistencia de config ------------------

def test_salud_cuenta_miniaturas_y_clips(monkeypatch, cliente, tmp_path):
    a = Almacen(tmp_path / "s.db")
    carpeta = tmp_path / "clips"
    g = web.GuardadorClips(carpeta=str(carpeta))
    g.guardar_miniatura(np.zeros((8, 8, 3), np.uint8), "persona_entra")
    (carpeta / "a.mp4").write_bytes(b"\x00" * 100)
    monkeypatch.setattr(web, "_almacen", a)
    monkeypatch.setattr(web, "_clips", g)

    datos = cliente.get("/salud").json()
    total = sum(p.stat().st_size for p in carpeta.iterdir() if p.is_file())
    assert datos["clips"]["n"] == 1
    assert datos["clips"]["miniaturas"] == 1
    assert datos["clips"]["bytes"] == total
    a.cerrar()


def test_salud_sin_clips_devuelve_ceros(monkeypatch, cliente):
    monkeypatch.setattr(web, "_clips", None)
    datos = cliente.get("/salud").json()
    assert datos["clips"] == {"n": 0, "bytes": 0, "miniaturas": 0}


def test_persistir_config_conserva_comentarios_y_orden(monkeypatch, tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text(
        "# Vigia - comentario de cabecera\n"
        "almacen: vigia.db  # no tocar\n"
        "permanencia_seg: 30\n"
        "vigilar_desaparicion:\n"
        "- laptop\n"
        "- backpack\n"
        "# franja de prueba\n"
        "franjas: []\n",
        encoding="utf-8")
    monkeypatch.setattr(web, "_RUTA_CONFIG", ruta)

    web._persistir_config({"guardar_clips": False})

    texto = ruta.read_text(encoding="utf-8")
    assert "# Vigia - comentario de cabecera" in texto
    assert "# franja de prueba" in texto
    assert "permanencia_seg: 30" in texto
    assert "- laptop" in texto and "- backpack" in texto
    import yaml
    datos = yaml.safe_load(texto)
    assert datos["almacen"] == "vigia.db"
    assert datos["guardar_clips"] is False
    assert datos["vigilar_desaparicion"] == ["laptop", "backpack"]


def test_persistir_config_reemplaza_bloque_de_lista(monkeypatch, tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("vigilar_desaparicion:\n- laptop\n- backpack\n"
                    "notificar: true\n", encoding="utf-8")
    monkeypatch.setattr(web, "_RUTA_CONFIG", ruta)

    web._persistir_config({"vigilar_desaparicion": ["laptop"]})

    import yaml
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert datos["vigilar_desaparicion"] == ["laptop"]
    assert datos["notificar"] is True


def test_persistir_config_zonas_y_franjas_siguen_siendo_yaml(monkeypatch,
                                                              tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("permanencia_seg: 30\nzonas: {}\nfranjas: []\n",
                    encoding="utf-8")
    monkeypatch.setattr(web, "_RUTA_CONFIG", ruta)

    web._persistir_config({
        "zonas": {"puerta": [0, 0, 100, 100]},
        "franjas": [{"desde": "22:00", "hasta": "08:00"}],
    })

    from vigia.config import cargar_config
    cfg = cargar_config(ruta)
    assert cfg["permanencia_seg"] == 30
    assert cfg["zonas"] == {"puerta": (0, 0, 100, 100)}
    assert cfg["franjas"] == [{"desde": "22:00", "hasta": "08:00"}]


def test_config_guardar_persiste_solo_lo_cambiado(monkeypatch, cliente,
                                                  tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("# comentario\npermanencia_seg: 30\nnotificar: true\n",
                    encoding="utf-8")
    monkeypatch.setattr(web, "_RUTA_CONFIG", ruta)
    cfg_antes = dict(web._config)
    perm_antes = web._motor_compartido.permanencia_seg
    try:
        r = cliente.post("/config", json={"permanencia_seg": 45})
        assert r.status_code == 200 and r.json()["ok"] is True
        texto = ruta.read_text(encoding="utf-8")
        assert "# comentario" in texto
        assert "notificar: true" in texto
        assert "permanencia_seg: 45" in texto
        assert web._motor_compartido.permanencia_seg == 45
    finally:
        web._motor_compartido.permanencia_seg = perm_antes
        web._config.clear()
        web._config.update(cfg_antes)


def test_iou_y_persona_de_pose_emparejan_por_caja():
    assert web._iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert web._iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    personas = [(4, (0, 0, 100, 100)), (5, (200, 200, 300, 300))]
    assert web._persona_de_pose((10, 10, 90, 90), personas) == 4
    assert web._persona_de_pose((250, 250, 260, 260), personas) == 5
    assert web._persona_de_pose((500, 500, 510, 510), personas) is None


def test_bucle_no_llena_el_buffer_si_los_clips_estan_apagados(tmp_path):
    a = Almacen(tmp_path / "b.db")
    estado = Estado()
    clips = web.GuardadorClips(max_frames=10,
                               carpeta=str(tmp_path / "clips"))
    config = {"guardar_clips": False, "notificar": False,
              "miniaturas": False, "actividades": False, "caras": False,
              "timeout_persona_seg": 10}
    parar = threading.Event()
    hilo = threading.Thread(
        target=web.bucle_procesamiento, args=(estado,),
        kwargs={"cam": _CamaraFalsa(), "detector": _DetectorFalso(),
                "clips": clips, "config": config, "almacen": a,
                "parar": parar}, daemon=True)
    hilo.start()
    try:
        limite = time.monotonic() + 5
        while estado.snapshot()[0] is None and time.monotonic() < limite:
            time.sleep(0.02)
        time.sleep(0.1)
    finally:
        parar.set()
        hilo.join(2)
        a.cerrar()
    assert list(clips._buf) == []


def test_bucle_llena_el_buffer_al_activar_clips_en_caliente(tmp_path):
    a = Almacen(tmp_path / "b2.db")
    estado = Estado()
    clips = web.GuardadorClips(max_frames=20,
                               carpeta=str(tmp_path / "clips"))
    config = {"guardar_clips": False, "notificar": False,
              "miniaturas": False, "actividades": False, "caras": False,
              "timeout_persona_seg": 10}
    parar = threading.Event()
    hilo = threading.Thread(
        target=web.bucle_procesamiento, args=(estado,),
        kwargs={"cam": _CamaraFalsa(), "detector": _DetectorFalso(),
                "clips": clips, "config": config, "almacen": a,
                "parar": parar}, daemon=True)
    hilo.start()
    try:
        limite = time.monotonic() + 5
        while estado.snapshot()[0] is None and time.monotonic() < limite:
            time.sleep(0.02)
        assert list(clips._buf) == []
        config["guardar_clips"] = True  # interruptor en caliente
        limite = time.monotonic() + 5
        while not clips._buf and time.monotonic() < limite:
            time.sleep(0.02)
        assert len(clips._buf) > 0
    finally:
        parar.set()
        hilo.join(2)
        a.cerrar()

