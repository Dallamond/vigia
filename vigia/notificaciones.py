"""Notificaciones de Windows (toast) sin dependencias externas.

Lanza un toast con PowerShell; si falla, solo se registra en el log
(nunca rompe el bucle principal). El texto se escapa para XML y para la
cadena de PowerShell, porque viene de nombres de zona/config.
"""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)

_PS_PLANTILLA = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("__XML__")
$t = New-Object Windows.UI.Notifications.ToastNotification($x)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Vigia").Show($t)
"""


def _escapar_xml(texto: str) -> str:
    return (texto.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _escapar_ps(texto: str) -> str:
    # dentro de una cadena PowerShell entre comillas dobles
    return (texto.replace("`", "``")
            .replace('"', '`"')
            .replace("$", "`$"))


def _xml_toast(titulo: str, texto: str) -> str:
    t = _escapar_ps(_escapar_xml(str(titulo)))
    x = _escapar_ps(_escapar_xml(str(texto)))
    return ('<toast><visual><binding template="ToastText02">'
            f'<text id="1">{t}</text><text id="2">{x}</text>'
            '</binding></visual></toast>')


def notificar(titulo: str, texto: str) -> bool:
    """Lanza un toast de Windows. Devuelve True si se pudo lanzar."""
    try:
        msg = _PS_PLANTILLA.replace("__XML__", _xml_toast(titulo, texto))
        extra = {}
        if os.name == "nt":
            extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["powershell", "-NoProfile", "-Command", msg],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **extra)
        return True
    except Exception as e:
        log.warning("notificacion falló: %s", e)
        return False
