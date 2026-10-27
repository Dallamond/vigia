# Vigia

Sistema local de vigilancia inteligente: webcam → detección de personas
y objetos (YOLO) → eventos → dashboard web + historial SQLite.
Todo corre en tu PC; el video no sale de él.

## Estado

Ver `docs/04-estado.md` — fuente de verdad del estado del proyecto.

## Documentación

| Archivo | Qué contiene |
|---|---|
| `docs/01-diseno.md` | Arquitectura, stack, decisiones |
| `docs/02-roadmap.md` | Fases 1–5 y criterios de cierre |
| `docs/03-plan-fase-1.md` | Plan detallado de la fase en curso |
| `docs/04-estado.md` | Estado vivo del proyecto + registro de cambios |

## Arranque

Requiere Python 3.11+ en Windows.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Después instala PyTorch con la CUDA de tu PC (si no, irá en CPU):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

### Ventana de escritorio (detección en vivo)

```bash
python -m vigia.main        # tecla q para salir
```

### Dashboard web

```bash
python -m vigia.web         # http://localhost:8000
```

### Diagnóstico y tests

```bash
python -m vigia.probar_camara   # qué cámara/backend funciona
pytest                          # suite completa (sin cámara real)
```

El peso `yolo11n.pt` se descarga automáticamente en el primer arranque
si no está en la carpeta (necesita internet esa primera vez).

## Privacidad

Todo local. La BD guarda solo eventos (metadatos), no video continuo.
