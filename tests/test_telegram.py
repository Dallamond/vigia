"""Tests de Telegram sin red real: cliente falso y reloj inyectable."""
import json
import os
from pathlib import Path

import pytest

from vigia import telegram
from vigia.telegram import (ClienteTelegram, NotificadorTelegram,
                            VigilanteSistema, crear_notificador,
                            estado_config)


class ClienteFalso:
    def __init__(self, falla=False):
        self.textos = []
        self.fotos = []
        self.videos = []
        self.updates = []
        self.falla = falla

    def enviar_texto(self, chat_id, texto):
        if self.falla:
            raise RuntimeError("sin red")
        self.textos.append((str(chat_id), texto))

    def enviar_foto(self, chat_id, jpeg, caption=""):
        self.fotos.append((str(chat_id), jpeg, caption))

    def enviar_video(self, chat_id, ruta, caption=""):
        self.videos.append((str(chat_id), Path(ruta).name, caption))

    def actualizar(self, offset=0, timeout=25):
        return list(self.updates)


def _notificador(cliente=None, **cfg):
    base = {"telegram_prioridades": {
        "cara_desconocida": "aviso", "permanencia": "aviso",
        "objeto_desaparece": "aviso", "persona_entra": "info"},
        "telegram_max_hora": 20, "telegram_cooldown_seg": 60}
    base.update(cfg)
    reloj = cfg.pop("reloj", None)
    n = NotificadorTelegram(cliente or ClienteFalso(), "42", base,
                            reloj=reloj or (lambda: 1000.0))
    return n


def test_prioridades_y_armado():
    n = _notificador()
    assert n.prioridad("persona_entra") == "info"
    assert n.prioridad("permanencia") == "aviso"
    assert n.prioridad("cara_desconocida", armado=False) == "aviso"
    assert n.prioridad("cara_desconocida", armado=True) == "critico"


def test_evento_info_no_se_encola_y_aviso_si():
    n = _notificador()
    assert n.evento("persona_entra", "id 1") is False
    assert n.evento("permanencia", "zona=puerta") is True
    assert n._cola.qsize() == 1


def test_anti_spam_por_cooldown_y_resumen_de_omitidos():
    n = _notificador(telegram_cooldown_seg=60)
    assert n.evento("permanencia", "id 1", clave="1")
    assert n.evento("permanencia", "id 1", clave="1") is False
    assert n.evento("permanencia", "id 2", clave="2") is True
    n._cola.get_nowait()  # el primer aviso no lleva resumen
    item = n._cola.get_nowait()
    assert "(+1 eventos más)" in item["texto"]


def test_anti_spam_maximo_por_hora():
    t = {"v": 1000.0}
    n = _notificador(reloj=lambda: t["v"], telegram_max_hora=1,
                     telegram_cooldown_seg=0)
    assert n.evento("permanencia", "id 1", clave="1")
    assert n.evento("permanencia", "id 2", clave="2") is False
    t["v"] += 3601
    assert n.evento("permanencia", "id 3", clave="3")
    n._cola.get_nowait()
    item = n._cola.get_nowait()
    assert item["texto"].endswith("(+1 eventos más)")


def test_envia_texto_foto_y_clip_cuando_esta_listo(tmp_path):
    cliente = ClienteFalso()
    n = _notificador(cliente)
    clip = tmp_path / "a.webm"
    clip.write_bytes(b"webm")
    n._procesar({"texto": "[AVISO] permanencia", "jpeg": b"\xff\xd8jpg",
                 "clip": str(clip), "tipo": "permanencia"})
    assert cliente.fotos and cliente.fotos[0][1] == b"\xff\xd8jpg"
    assert "permanencia" in cliente.fotos[0][2]
    assert cliente.videos and cliente.videos[0][1] == "a.webm"
    assert n.estado()["ultimo_envio"]


def test_error_al_enviar_se_guarda_sin_romper():
    cliente = ClienteFalso(falla=True)
    n = _notificador(cliente)
    with pytest.raises(RuntimeError):
        n._procesar({"texto": "x", "jpeg": None, "clip": None,
                     "tipo": "x"})
    assert n.enviar_prueba()["ok"] is False
    assert "sin red" in n.estado()["ultimo_error"]


def test_prueba_correcta_y_aviso_de_sistema():
    cliente = ClienteFalso()
    n = _notificador(cliente)
    assert n.enviar_prueba() == {"ok": True}
    assert cliente.textos and "prueba" in cliente.textos[0][1]
    assert n.aviso_sistema("disco bajo")
    item = n._cola.get_nowait()
    assert item["texto"].startswith("[SISTEMA]")


def test_comandos_del_bot_y_chat_autorizado():
    cliente = ClienteFalso()
    n = _notificador(cliente)
    vistos = []

    def manejador(comando):
        vistos.append(comando)
        if comando == "/foto":
            return ("foto", b"\xff\xd8x")
        return "ok"

    assert n.manejar_comando("/armar", manejador) == "ok"
    assert vistos == ["/armar"]
    assert "Comandos" in n.manejar_comando("/desconocido", manejador)
    n._responder(manejador("/foto"))
    assert cliente.fotos[-1][1] == b"\xff\xd8x"


def test_cliente_telegram_envia_y_pide_updates(monkeypatch):
    vistos = {}

    class Respuesta:
        def __init__(self, datos):
            self.datos = json.dumps(datos).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.datos

    def fake_urlopen(req, timeout=None):
        vistos["url"] = req.full_url
        vistos["datos"] = req.data
        if "getUpdates" in req.full_url:
            return Respuesta({"ok": True, "result": [{"update_id": 7}]})
        return Respuesta({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    c = ClienteTelegram("T0KEN", api_base="https://ejemplo.test")
    assert c.enviar_texto(42, "hola") == {"message_id": 1}
    assert "botT0KEN/sendMessage" in vistos["url"]
    assert b"chat_id=42" in vistos["datos"]
    assert c.actualizar(offset=6, timeout=1) == [{"update_id": 7}]
    assert b"offset=6" in vistos["datos"]


def test_cliente_telegram_multipart_video(monkeypatch, tmp_path):
    visto = {}

    class Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "result": {}}'

    def fake_urlopen(req, timeout=None):
        visto["tipo"] = req.headers.get("Content-type")
        visto["datos"] = req.data
        return Respuesta()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    ruta = tmp_path / "clip.webm"
    ruta.write_bytes(b"contenido")
    ClienteTelegram("t").enviar_video(1, ruta, caption="clip")
    assert visto["tipo"].startswith("multipart/form-data; boundary=")
    assert b'filename="clip.webm"' in visto["datos"]
    assert b"contenido" in visto["datos"]


def test_crear_notificador_y_estado_sin_secretos(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-secreto")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert crear_notificador({"telegram": False}) is None
    n = crear_notificador({"telegram": True})
    assert n is not None and n.chat_id == "123"
    e = estado_config({"telegram": True})
    assert e["configurado"] is True
    assert "token-secreto" not in json.dumps(e)
    monkeypatch.delenv("TELEGRAM_TOKEN")
    e = estado_config({"telegram": True})
    assert e["configurado"] is False and "TELEGRAM_TOKEN" in e["ultimo_error"]
    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    assert "TELEGRAM_CHAT_ID" in estado_config({"telegram": True})["ultimo_error"]


class _Avisos:
    def __init__(self):
        self.avisos = []

    def aviso_sistema(self, texto):
        self.avisos.append(texto)


def test_vigilante_camara_y_disco_con_historial():
    t = {"v": 0.0}
    avisos = _Avisos()
    v = VigilanteSistema(avisos, camara_seg=300, disco_gb=5,
                         reloj=lambda: t["v"])
    assert v.revisar(False, 100.0) == []
    t["v"] = 299
    assert v.revisar(False, 100.0) == []
    t["v"] = 300
    assert v.revisar(False, 100.0) == ["camara caida mas de 5 min"]
    t["v"] = 301
    assert v.revisar(False, 100.0) == []  # no repite
    assert v.revisar(True, 100.0) == []  # se recupera
    t["v"] = 302
    assert v.revisar(False, 100.0) == []
    assert v.revisar(True, 4.0) == ["disco bajo: 4.0 GB libres"]
    assert v.revisar(True, 3.0) == []
    assert v.revisar(True, 7.0) == []  # histéresis
    t["v"] = 900
    assert v.revisar(True, 4.0) == ["disco bajo: 4.0 GB libres"]
    assert avisos.avisos


# -- endpoints del panel ----------------------------------------------------

def test_endpoints_telegram_y_armado(monkeypatch):
    from fastapi.testclient import TestClient
    import vigia.web as web

    class FakeTelegram:
        def estado(self):
            return {"configurado": True, "ultimo_error": None,
                    "ultimo_envio": "2026-10-28 10:00:00", "pendientes": 0}

        def enviar_prueba(self):
            return {"ok": True}

    class FakeArmado:
        def __init__(self):
            self.armado = False
            self.auto = False

        def actualizar(self):
            return False

        def estado(self):
            return {"armado": self.armado, "auto": self.auto, "desde": None}

        def armar(self):
            self.armado = True

        def desarmar(self):
            self.armado = False

        def configurar_auto(self, activo):
            self.auto = activo

    fake = FakeTelegram()
    armado = FakeArmado()
    monkeypatch.setattr(web, "_telegram", fake)
    monkeypatch.setattr(web, "_gestor_armado", armado)
    c = TestClient(web.app, client=("127.0.0.1", 50000))
    d = c.get("/telegram/estado").json()
    assert d["configurado"] is True
    assert "token_configurado" in d and "secreto" not in str(d).lower()
    assert c.post("/telegram/prueba").json() == {"ok": True}
    assert c.get("/armado").json()["armado"] is False
    assert c.post("/armado", json={"armado": True}).json()["armado"] is True
    assert c.post("/armado", json={"armado": "no"}).json()["armado"] is False
    assert c.post("/armado", json={"armado": "quizas"}).status_code == 400


def test_probar_telegram_sin_configurar_da_409(monkeypatch):
    from fastapi.testclient import TestClient
    import vigia.web as web

    monkeypatch.setattr(web, "_telegram", None)
    c = TestClient(web.app, client=("127.0.0.1", 50000))
    assert c.post("/telegram/prueba").status_code == 409
    d = c.get("/telegram/estado").json()
    assert d["configurado"] is False and d["token_configurado"] is False
    assert "secret" not in str(d).lower()
