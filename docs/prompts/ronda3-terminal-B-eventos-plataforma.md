# Ronda 3 · Terminal B — Eventos, zonas, API y cámaras

Trabajas en **Vigia** (`D:\Lukaton1\03 - PROYECTOS TEC\vigia`): videovigilancia local con YOLO (FastAPI + OpenCV + SQLite, Python 3.12, `.venv` en la carpeta). Lee antes `docs/04-estado.md`, `docs/prompts/NOTAS.md` (contratos vigentes: zonas, auth, Telegram, armado) y `vigia/eventos.py`, `zonas.py`, `web.py`, `almacen.py`, `captura.py`.

Hay **otra terminal (A)** trabajando a la vez en percepción (pose, actividad, tracking) y **Claude** rehaciendo la GUI (`vigia/static/`) para consumir lo que tú expongas. Nadie va a estar disponible ~2 horas: **decide con criterio, anota la decisión y sigue. No te pares a preguntar.**

## Problema real
Lucas ve que los eventos (entra/sale, permanencia, zonas) no son fiables y que la GUI no tiene datos suficientes para ser un panel profesional (estadísticas, estado vivo, logs). Además falta multicámara real.

## Reparto de ficheros (obligatorio)
- **Tuyos:** `vigia/web.py`, `eventos.py`, `zonas.py`, `config.py`, `almacen.py`, `captura.py`, `clips.py`, `telegram.py`, `armado.py`, `mantener.py`, `seguridad.py`, `main.py`, ficheros nuevos (`vigia/estadisticas.py`, `vigia/camaras_ip.py`, `vigia/lineas.py`...), `config.example.yaml`, `docker*`/`deploy/` si lo necesitas, y sus tests.
- **De A (no tocar):** `pose.py`, `actividades.py`, `tracking.py`, `analisis.py`, `caras.py`, `detector.py`, `dibujo.py`, `vigia/vivo.py`, `eval/`. A añadirá **una línea** en `web.py` para montar su router `/vivo`: respétala si la ves y no la borres. Si necesitas algo de A, anótalo en `NOTAS.md` ("Petición a A") y sigue con otra cosa.
- **De Claude (no tocar):** `vigia/static/*`, `scripts/`, `docs/04-estado.md`.
- Git: commits pequeños con `git add <rutas concretas>` (nunca `-A` ni `commit -a`). Mensajes en español como el historial. Sin rebase/reset/force. **No hagas push** (lo hace Claude). Si la suite falla por ficheros de A, espera y reintenta; no los arregles tú.
- Servidor de prueba en el puerto **8030** (`VIGIA_PUERTO=8030`). **No abras la webcam** (la usa Lucas/Claude): tests con frames sintéticos/clips (`clips/*.webm`). Sin red real (mockea Telegram, RTSP, ffmpeg).
- Suite completa (`.venv\Scripts\python -m pytest -q`) verde antes de cada commit.
- **Contratos existentes que NO se rompen** (la GUI nueva se apoya en ellos): zonas (formato objeto + antiguo), auth (`401 {"detalle":...}`), `/armado`, `/telegram/*`, `/eventos`, `/timeline`, `/salud`, `/config`, `/caras/registro`, `/clips`, `/camaras`. Solo se **añaden** campos/endpoints.

## Tareas, por orden de prioridad

### P1. Datos para un panel profesional (la GUI los espera)
Todos protegidos por el middleware, con tests y documentados en `NOTAS.md` (sección **"Contrato Ronda 3 — B"**, con ejemplos JSON):
1. `GET /estadisticas?dias=7` → eventos por tipo y por hora del día, por día, tiempo acumulado por actividad (de los eventos `actividad`), personas distintas, ocupación media, picos. Agregado en SQL con índices si hace falta (`almacen.py`).
2. `GET /logs?limite=200&nivel=INFO` → últimas líneas de `logs/vigia.log` como `{"lineas":[{"t","nivel","modulo","msg"}], "tamano_bytes", "rotaciones"}` (parseo tolerante).
3. `GET /eventos/stream` (**SSE**, `text/event-stream`) que emite cada evento nuevo (`event: evento`, data JSON igual que `/eventos`) y un `event: ping` cada 15 s; autenticado por cookie/Bearer. Reutilizable por la GUI en vez del polling. Con tests (cliente que lee N líneas) y sin dejar hilos colgados al cerrar.
4. `GET /eventos` con paginación (`limite`, `antes_id`), filtros combinables (`tipo`, `camara_id`, `desde`, `hasta`, `persona`, `zona`) y total. Compatible hacia atrás.
5. `GET /resumen` para el Dashboard: estado de cámara, armado, FPS, eventos de hoy por tipo, último evento, disco, uptime, Telegram (en una sola llamada, para no hacer 6 peticiones).
6. Unifica los errores: añade `detalle` **y** `detail` en las respuestas de error que hoy solo tienen uno (mismo texto).

### P2. Entrada/salida y zonas fiables (con A en percepción)
1. **Líneas de cruce (tripwire)**: nueva figura en `zonas.py`/`lineas.py` — línea `{p1,p2,sentido}` con eventos `cruce_linea` (dirección `a→b` / `b→a`) cuando el **centro-pie** de una persona cruza el segmento, con histéresis y anti-rebote. Configurable en `config.yaml` (`lineas:`), `GET/PUT /lineas` con validación (rango de coordenadas contra la resolución). Permite "entra/sale" por puerta de verdad.
2. **Eventos de zona**: `zona_entra` / `zona_sale` por persona con tiempo dentro (`duracion_seg` en la salida), una persona puede estar en **varias zonas a la vez** (hoy se asigna solo a la primera), validación de coordenadas de zonas, y las reglas por zona funcionan **fuera de la franja global** (hoy se salta).
3. **Desduplicación y calidad**: ningún par `persona_entra` repetido sin `persona_salio` entre medias; cooldown por (tipo, persona, zona); `aforo` actual calculado y guardado. Tests deterministas con reloj inyectable.
4. Cuando A publique los cambios de `tracking.py`/`analisis.py` (mira `NOTAS.md` → "Ronda 3 — A"), adapta lo que consumas de ellos sin tocar sus ficheros.

### P3. Cámaras IP y multicámara (bloque 5 de `docs/06-plan-v2.md`)
- `camaras:` lista en config (`id`, `nombre`, `fuente` índice USB o URL `rtsp://`, `sustream` opcional para detectar, `mainstream` para clips). Un lector con reconexión exponencial y estado (`conectada|reconectando|caida`) por cámara, un motor de eventos independiente por cámara, `camara_id` en eventos, miniaturas y clips (migración automática de la BD con valor por defecto), `GET /camaras/estado`, `/video.mjpg?camara=ID` y `/snapshot.jpg?camara=ID`. **Sin cámara IP real ni RTSP real**: prueba con un servidor RTSP simulado o con archivos de vídeo como fuente (`fuente: clips/x.webm`) y mocks. Con una sola cámara USB todo debe seguir funcionando exactamente igual.
- Si el bucle de `analisis.py` (de A) hace falta tocarlo para instanciar varios motores, pide a A el cambio por `NOTAS.md` o, si es mínimo y aditivo, hazlo con un edit puntual y avísalo ahí.

### P4. Telegram y robustez
- Conversión WebM → MP4 H.264 con `ffmpeg` si está en el PATH (hilo aparte, nunca bloquea el bucle); si no hay ffmpeg, envía WebM como ahora y registra aviso. Tests con `subprocess` mockeado.
- `/telegram/prueba` ya existe: añade `/telegram/estado` con último error legible. Nada contra la API real (no hay bot).
- Revisión de errores silenciosos: cada `except Exception: pass` en tus ficheros debe loguear a `logs/vigia.log`.

### P5 (si sobra tiempo)
- Retención configurable desde la GUI (`/config` ya valida: añade `retencion_*` si falta) y endpoint `POST /mantenimiento/ejecutar` (autenticado) que ejecuta `mantener` y devuelve lo borrado.
- Backups programados (`scripts/backup.py` es de Claude: solo llámalo, no lo edites) con estado en `/salud`.

## Al terminar cada tarea
Commit pequeño. En `docs/prompts/NOTAS.md`, bajo **"Contrato Ronda 3 — B"** y **"Ronda 3 — B"** (no edites lo de otros): qué hiciste, endpoints con ejemplo JSON, claves de config nuevas y decisiones raras. Dilo claro si algo queda a medias o sin probar con hardware real.
