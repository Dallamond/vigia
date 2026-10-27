"""Tests de notificaciones: no lanzan PowerShell de verdad."""
import vigia.notificaciones as notificaciones


def test_escapa_xml_y_powershell(monkeypatch):
    capturado = {}

    class PopenFalso:
        def __init__(self, args, **kwargs):
            capturado["args"] = args

    monkeypatch.setattr(notificaciones.subprocess, "Popen", PopenFalso)
    assert notificaciones.notificar("Vigia: entra & <b>", "zona A&B <x> $HOME")

    comando = capturado["args"][-1]
    assert "&amp;" in comando
    assert "&lt;b&gt;" in comando
    assert "A&amp;B" in comando
    assert "`$HOME" in comando
    assert "<b>" not in comando


def test_si_powershell_no_existe_no_rompe(monkeypatch):
    def falla(*_a, **_k):
        raise FileNotFoundError("powershell no esta")

    monkeypatch.setattr(notificaciones.subprocess, "Popen", falla)
    assert notificaciones.notificar("x", "y") is False
