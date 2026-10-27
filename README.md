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

Rutas:

| Ruta | Qué devuelve |
|---|---|
| `/` | dashboard: video en vivo + estado + feed + historial con filtros |
| `/video.mjpg` | stream MJPEG con cajas, IDs de personas y zonas |
| `/estado` | JSON con FPS, personas, objetos y estado de la cámara |
| `/eventos` | JSON con el feed reciente (en memoria) |
| `/historial` | JSON de SQLite; `?tipo=`, `?desde=AAAA-MM-DD`, `?limite=1..500` |
| `/clip/<fichero>.mp4` | clip guardado al dispararse un evento |

## Configuración

Copia `config.example.yaml` a `config.yaml` y ajusta lo que necesites. Si el
fichero no existe se usan los valores por defecto; si una clave está mal
escrita, Vigia avisa en el log y usa el valor por defecto en vez de romperse.

| Clave | Por defecto | Qué hace |
|---|---|---|
| `almacen` | `vigia.db` | fichero SQLite de eventos |
| `permanencia_seg` | `30` | segundos para disparar `permanencia` |
| `timeout_persona_seg` | `10` | segundos sin ver a alguien para `persona_salio` |
| `timeout_objeto_seg` | `10` | segundos sin ver un objeto para `objeto_desaparece` |
| `vigilar_desaparicion` | `[]` | clases COCO cuyo aparecer/desaparecer registrar |
| `zonas` | `{}` | `nombre: [x1, y1, x2, y2]` en píxeles |
| `franjas` | `[]` | `{desde: "HH:MM", hasta: "HH:MM"}`; fuera de ellas no hay eventos |
| `guardar_clips` | `true` | guardar un MP4 al dispararse `persona_entra`, `permanencia` u `objeto_desaparece` |
| `notificar` | `true` | lanzar un toast de Windows con esos eventos |

Los clips se escriben en `clips/` (ignorada por git) y los eventos en SQLite;
ninguno de los dos se limpia solo todavía.

## Diagnóstico y tests

```bash
python -m vigia.probar_camara   # qué cámara/backend funciona
pytest                          # suite completa (sin cámara real)
```

El peso `yolo11n.pt` se descarga automáticamente en el primer arranque
si no está en la carpeta (necesita internet esa primera vez).

## Privacidad

Todo local. La BD guarda solo eventos (metadatos), no video continuo.
