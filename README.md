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
| `docs/05-mejoras.md` | Mejoras por sprints |
| `docs/06-plan-v2.md` | Plan v2 priorizado: Telegram, cámaras IP, servidor, GUI, GPUs |

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

Por defecto escucha en `127.0.0.1:8000` (configurable en `config.yaml`; las
variables `VIGIA_HOST` / `VIGIA_PUERTO` ganan). Para verlo desde otro
dispositivo de la red local **define un token**:

```bash
set VIGIA_HOST=0.0.0.0
set VIGIA_TOKEN=algo-largo-y-secreto
```

Copia `.env.example` a `.env` para no repetir las variables. Sin `VIGIA_TOKEN`
Vigia solo atiende a `127.0.0.1` y se niega a arrancar si `host` no es local.
Con token, la API y el vídeo exigen `Authorization: Bearer`, la cookie
`vigia_token` (la pone `/auth/login`) o `?token=` solo en `/video.mjpg` y
`/snapshot.jpg`. El HTML y los estáticos quedan públicos para poder mostrar el
login.

El panel (`vigia/static/index.html`) trae:

- **vídeo en vivo** con las cajas, los IDs de persona y las zonas;
- **tamaño del vídeo** S / M / L / XL / ancho + pantalla completa (se recuerda
  en el navegador);
- **línea del tiempo** horizontal a todo el ancho, en directo (1 h / 6 h / 24 h o un día), con presencia, permanencias, actividades, objetos y caras, más una tira de miniaturas;
- **actividad en directo**, historial filtrable y clips;
- **selector de webcam** en la cabecera (cambio en caliente) y registro de **caras** (☺);
- **detalle de evento**: miniatura y reproductor del clip;
- **editor de zonas** sobre una captura congelada (se aplica en caliente);
- **ajustes** sin editar YAML (reglas, retención, resolución, extras).

Rutas:

| Ruta | Qué devuelve |
|---|---|
| `/` | panel completo (HTML estático) |
| `/video.mjpg` | stream MJPEG con cajas, IDs de personas y zonas |
| `/snapshot.jpg` | último frame como imagen (fondo del editor de zonas) |
| `/estado` | JSON: FPS, personas, objetos, resolución y estado de cámara |
| `/eventos` | JSON del feed reciente (en memoria) |
| `/historial` | JSON de SQLite; `?tipo=`, `?desde=AAAA-MM-DD`, `?limite=1..500` |
| `/timeline` | JSON de un día (`?dia=AAAA-MM-DD`) o de una ventana (`?desde=&hasta=`, `AAAA-MM-DD HH:MM:SS`, máx. 8 días) |
| `/clips` | JSON con los clips en disco |
| `/media/<f>` | sirve un clip (`.webm`/`.mp4`) o una miniatura `.jpg` |
| `/clip/<f>` | alias histórico, solo vídeo |
| `/camaras` | webcams disponibles y la activa; se cambia con `POST /config {camara: N}` |
| `/caras/registro` | `GET` conocidos · `POST` alta (`{nombre, capturar:true}`), renombrar (`{nombre, id}`) · `DELETE /{id}` |
| `/caras/ultima` `.jpg` | ¿hay una cara a la vista?, y su recorte |
| `/zonas` | `GET` zonas + resolución; `PUT` para guardarlas |
| `/config` | `GET`/`POST` ajustes en caliente |
| `/actividades` | JSON `{id: actividad}` (si `actividades: true`) |
| `/salud` | uptime, tamaño de BD, clips y espacio libre en disco |
| `/auth/estado` | `{auth_requerida, autenticado}` (siempre accesible) |
| `/auth/login` | `POST {"token"}`: pone la cookie; 401 si es incorrecto |
| `/auth/logout` | `POST`: borra la cookie |
| `/armado` | `GET` estado; `POST {"armado": bool, "auto": bool}` |
| `/telegram/estado` | configurado, último error y último envío (sin secretos) |
| `/telegram/prueba` | `POST`: manda un mensaje de prueba |

### Zonas y reglas

`GET /zonas` devuelve objetos (el formato antiguo de `config.yaml` se sigue
leyendo). Ejemplo de una zona y de lo que acepta `PUT /zonas`:

```json
{"zonas": {"puerta": {
  "caja": [0, 0, 200, 480],
  "permanencia_seg": 20,
  "franjas": [{"desde": "22:00", "hasta": "08:00"}],
  "restringida": false,
  "excluir": false,
  "max_personas": 0,
  "vigilar": ["laptop"],
  "conocidos": true
}}}
```

También se admite `"poligono": [[x, y], ...]` en vez de `"caja"` (nunca los
dos). Las reglas nuevas generan eventos `zona_restringida`,
`demasiadas_personas`, `merodeo` y `persona_inmovil`, además de la permanencia
por zona con re-aviso.

### Telegram

Rellena `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`, pon `telegram: true`
en `config.yaml` y reinicia. El bot funciona por *long polling* (no abre
puertos), solo atiende al chat autorizado y acepta `/armar`, `/desarmar`,
`/estado` y `/foto`. Los avisos incluyen miniatura y, cuando el clip WebM ya
está escrito, el vídeo. El envío va en un hilo aparte: nunca frena la
detección. Los clientes móviles que no reproduzcan WebM necesitarían conversión
a MP4 H.264 con ffmpeg (pendiente, documentado en `vigia/telegram.py`).


## Configuración

Copia `config.example.yaml` a `config.yaml` y ajusta lo que necesites. Si el
fichero no existe se usan los valores por defecto; si una clave está mal
escrita, Vigia avisa en el log y usa el valor por defecto en vez de romperse.
Casi todo se puede cambiar desde el panel, sin reiniciar.

| Clave | Por defecto | Qué hace |
|---|---|---|
| `almacen` | `vigia.db` | fichero SQLite de eventos |
| `host` / `puerto` | `127.0.0.1` / `8000` | dónde escucha el panel (el entorno gana) |
| `camara` | `0` | índice de la webcam (se cambia en caliente) |
| `ancho` / `alto` | `640` / `480` | resolución de captura (requiere reiniciar) |
| `permanencia_seg` | `30` | segundos para disparar `permanencia` |
| `timeout_persona_seg` | `10` | segundos sin ver a alguien para `persona_salio` |
| `timeout_objeto_seg` | `10` | segundos sin ver un objeto para `objeto_desaparece` |
| `vigilar_desaparicion` | `[]` | clases COCO cuyo aparecer/desaparecer registrar |
| `zonas` | `{}` | `nombre: [x1, y1, x2, y2]` en píxeles |
| `franjas` | `[]` | `{desde: "HH:MM", hasta: "HH:MM"}`; fuera de ellas no hay eventos |
| `guardar_clips` | `true` | guardar un clip WebM al dispararse `persona_entra`, `permanencia` u `objeto_desaparece` |
| `miniaturas` | `true` | guardar un JPEG del instante en cada evento |
| `retencion_dias` | `30` | días de eventos a conservar (`0` = no purgar) |
| `retencion_clips_dias` | `14` | días de clips y miniaturas a conservar |
| `retencion_clips_mb` | `2048` | tope de la carpeta de media en MB (`0` = sin tope) |
| `mantener_cada_min` | `60` | cada cuánto se ejecuta la purga con el panel abierto |
| `notificar` | `true` | toast de Windows en `permanencia` y objetos desaparecidos |
| `actividades` | `false` | segunda pasada de pose (más FPS de coste) |
| `caras` | `false` | reconocimiento de caras (YuNet + SFace, local) |
| `permanencia_conocidos_seg` | `0` | permanencia para conocidos (`0` = nunca) |
| `reaviso_min` | `0` | re-aviso mientras la persona sigue (`0` = una vez) |
| `merodeo_entradas` / `merodeo_ventana_seg` | `3` / `120` | re-entradas para `merodeo` |
| `inmovil_seg` / `inmovil_px` | `0` / `20` | inmovilidad (posible caída); `0` desactiva |
| `max_personas` | `0` | máximo global de personas (`0` = sin límite) |
| `telegram` | `false` | activa el bot y los avisos (secretos en `.env`) |
| `telegram_prioridades` | ver ejemplo | `info` / `aviso` / `critico` por tipo |
| `telegram_max_hora` / `telegram_cooldown_seg` | `20` / `300` | anti-spam |
| `armado_auto` / `armado_franjas` | `false` / `[]` | armado automático por franjas |
| `avisos_camara_seg` / `avisos_disco_gb` | `300` / `5` | avisos del sistema |

### Mantenimiento y logs

```bash
python -m vigia.mantener               # purga eventos y media según config.yaml
python -m vigia.mantener --eventos 90  # sobrescribe la retención de eventos
python -m vigia.mantener --convertir   # recodifica clips .mp4 antiguos a WebM
```

Los clips son WebM (VP8) porque es lo que reproduce cualquier navegador.
Los eventos y las miniaturas/clips se guardan en SQLite y en `clips/`
(ignorada por git). La purga corre sola cada hora mientras el panel está
abierto, y también se puede lanzar a mano. El log va a `logs/vigia.log`
(rotativo: 5 × 2 MB).


## Diagnóstico y tests

```bash
python -m vigia.probar_camara   # qué cámara/backend funciona
pytest                          # suite completa (sin cámara real)
```

El peso `yolo11n.pt` se descarga automáticamente en el primer arranque
si no está en la carpeta (necesita internet esa primera vez).

## Privacidad

Todo local. La BD guarda solo eventos (metadatos), no video continuo.
