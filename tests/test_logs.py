"""Tests del logging a fichero (sin ensuciar el logger raiz del resto)."""
from __future__ import annotations

import logging

from vigia.logs import configurar_logging


def _quitarlo(ruta):
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(ruta):
            raiz.removeHandler(h)
            h.close()


def test_crea_el_fichero_y_escribe(tmp_path):
    ruta = configurar_logging(carpeta=tmp_path, fichero="v.log")
    try:
        assert ruta is not None and ruta.exists()
        logging.getLogger("vigia.test").info("hola desde el test")
        for h in logging.getLogger().handlers:
            h.flush()
        assert "hola desde el test" in ruta.read_text(encoding="utf-8")
    finally:
        _quitarlo(ruta)


def test_no_duplica_el_handler_si_se_llama_dos_veces(tmp_path):
    primera = configurar_logging(carpeta=tmp_path, fichero="v.log")
    segunda = configurar_logging(carpeta=tmp_path, fichero="v.log")
    try:
        assert primera == segunda
        manejadores = [h for h in logging.getLogger().handlers
                       if isinstance(h, logging.FileHandler)
                       and h.baseFilename == str(primera)]
        assert len(manejadores) == 1
    finally:
        _quitarlo(primera)
