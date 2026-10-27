"""Diagnostico de webcams en Windows: recorre indices y backends.

Uso: .venv\\Scripts\\python.exe -m vigia.probar_camara
"""
from __future__ import annotations

import cv2

BACKENDS = [
    ("MSMF", cv2.CAP_MSMF),      # nativo Windows (uso por defecto)
    ("DSHOW", cv2.CAP_DSHOW),    # DirectShow (a menudo mas fiable con webcams)
    ("ANY", cv2.CAP_ANY),        # automatico
]


def main() -> None:
    print("Recorriendo camaras disponibles...\n")
    encontradas = []
    for backend_nombre, backend in BACKENDS:
        for indice in range(4):
            cap = cv2.VideoCapture(indice, backend)
            open_msmf = bool(getattr(cv2, "CAP_PROP_BACKEND", None))
            ok_lectura, _ = cap.read()
            abierta = cap.isOpened()
            cap.release()
            if ok_lectura:
                print(f"[OK ]   indice={indice}  backend={backend_nombre}  -> LEE FRAMES")
                encontradas.append((indice, backend))
            elif abierta:
                print(f"[~  ]   indice={indice}  backend={backend_nombre}  -> abre pero no lee frames")
            else:
                print(f"[-- ]   indice={indice}  backend={backend_nombre}  -> no disponible")
    if not encontradas:
        print("\n⚠ Ninguna camara leyo frames. Revisa:")
        print("  1. Configuracion de Windows > Privacidad y seguridad > Camara >")
        print("     'Permitir que aplicaciones de escritorio accedan a la camara' = SI")
        print("  2. Que la app Camara u otra app no la esté usando ahora mismo")
        print("  3. Probar otro puerto USB / hub")
    else:
        print(f"\n✅ Camaras funcionales: {[(i, b) for i, b in encontradas]}")


if __name__ == "__main__":
    main()
