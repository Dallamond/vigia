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

## Arranque (cuando exista código)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m vigia.main
```

## Privacidad

Todo local. La BD guarda solo eventos (metadatos), no video continuo.
