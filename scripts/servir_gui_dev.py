"""Servidor de desarrollo solo para la GUI (puerto 8020).

El backend sirve `/` pero, hasta que la terminal A monte `vigia/static` en
`/static`, los módulos ES y el CSS dan 404. Este script arranca la misma app
con ese montaje añadido *en memoria* (no toca `web.py`) y sin el bucle de
cámara, para verificar el panel en el navegador.

Uso:
    .venv\\Scripts\\python scripts\\servir_gui_dev.py
    .venv\\Scripts\\python scripts\\servir_gui_dev.py --token secreto

No es el servidor de producción: para eso, `python -m vigia.web`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=8020)
    parser.add_argument("--token", default=None,
                        help="simula VIGIA_TOKEN para probar el login")
    args = parser.parse_args()

    if args.token:
        import os
        os.environ["VIGIA_TOKEN"] = args.token

    import uvicorn
    from fastapi.staticfiles import StaticFiles

    import vigia.web as web

    static = Path(web.__file__).parent / "static"
    ya_montado = any(getattr(ruta, "path", None) == "/static"
                     for ruta in web.app.routes)
    if not ya_montado:
        web.app.mount("/static", StaticFiles(directory=str(static)),
                      name="static-dev")

    print(f"GUI de desarrollo en http://{args.host}:{args.puerto}/  "
          f"(static: {static})")
    if args.token:
        print(f"Login con el token: {args.token}")
    uvicorn.run(web.app, host=args.host, port=args.puerto, log_level="warning")


if __name__ == "__main__":
    main()
