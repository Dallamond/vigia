"""Diagnostico de webcams en Windows: recorre indices y backends.

Uso: .venv\\Scripts\\python.exe -m vigia.probar_camara
"""
from __future__ import annotations

import cv2

# DSHOW es el backend que usa `captura.py`; MSMF se prueba como alternativa.
BACKENDS = [
    ("DSHOW", cv2.CAP_DSHOW),
    ("MSMF", cv2.CAP_MSMF),
]
INDICES = range(4)


def _probar(indice: int, backend: int) -> tuple[bool, bool]:
    """Devuelve (abre, lee_frames). Nunca propaga errores del driver."""
    cap = None
    try:
        cap = cv2.VideoCapture(indice, backend)
        abierta = cap.isOpened()
        if not abierta:
            return False, False
        ok, _ = cap.read()
        return True, bool(ok)
    except cv2.error:
        return False, False
    finally:
        if cap is not None:
            cap.release()


def main() -> None:
    print("Recorriendo camaras disponibles...\n")
    encontradas: list[tuple[int, str]] = []
    for nombre, backend in BACKENDS:
        for indice in INDICES:
            abierta, lee = _probar(indice, backend)
            if lee:
                print(f"[OK ] indice={indice} backend={nombre} -> lee frames")
                encontradas.append((indice, nombre))
            elif abierta:
                print(f"[~  ] indice={indice} backend={nombre} -> abre pero "
                      "no lee frames")
            else:
                print(f"[-- ] indice={indice} backend={nombre} -> no disponible")

    if not encontradas:
        print("\nNinguna camara leyo frames. Revisa:")
        print("  1. Configuracion de Windows > Privacidad y seguridad > Camara >")
        print("     'Permitir que aplicaciones de escritorio accedan a la "
              "camara' = SI")
        print("  2. Que la app Camara u otra app no la este usando ahora mismo")
        print("  3. Probar otro puerto USB / hub")
        return

    print("\nCamaras funcionales:")
    for indice, backend in encontradas:
        print(f"  fuente={indice}  backend={backend}")
    print("\nVigia usa fuente=0 y backend DSHOW. Si tu camara solo funciona "
          "con otro indice/backend, pasalos a Camara(fuente=..., backend=...).")


if __name__ == "__main__":
    main()
