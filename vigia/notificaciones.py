"""Notificaciones de Windows (toast) sin dependencias externas.

Implementa un toast con BurnedToast via powershell; si falla, solo es log
muted (nunca rompe el bucle principal).
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

_PS_PLANTILLA = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template=`"ToastText02`"><text id=`"1`">{1}</text><text id=`"2`">{2}</text></binding></visual></toast>")
$t = New-Object Windows.UI.Notifications.ToastNotification($x)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Vigia").Show($t)
"""


def notificar(titulo: str, texto: str) -> bool:
    """Lanza un toast de Windows. Devuelve True si salió sin excepción."""
    try:
        msg = _PS_PLANTILLA.replace("{1}", titulo.replace("'", "''"))
        msg = msg.replace("{2}", texto.replace("'", "''"))
        subprocess.Popen(["powershell", "-NoProfile", "-Command", msg],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        log.warning("notificacion falló: %s", e)
        return False
