# Notas entre terminales

## Cambios de contrato (A)

### Seguridad / auth (bloque 1)

- `POST /auth/login` body `{"token": "..."}`:
  - `200 {"ok": true, "auth_requerida": true}` y cookie `vigia_token`
    (HttpOnly, SameSite=Lax, 30 días, path `/`).
  - `401 {"detail": ...}` si el token no coincide.
  - `429` si esa IP supera 5 fallos en 60 s.
  - Si no hay `VIGIA_TOKEN`, devuelve `200` sin cookie (`auth_requerida:false`).
- `POST /auth/logout` → `200 {"ok": true}` y borra la cookie.
- `GET /auth/estado` → siempre accesible:
  `{"auth_requerida": bool, "autenticado": bool}`.
- Cualquier otro endpoint (API, vídeo, `/media`, `/clip`, `/static` no) sin
  credencial → `401` JSON **`{"detalle": "no autenticado"}`** (ojo: `detalle`,
  no `detail`).
- Credenciales aceptadas:
  - `Authorization: Bearer <token>`
  - cookie `vigia_token`
  - `?token=<token>` **solo** en `/video.mjpg` y `/snapshot.jpg`.
- Público sin token: `/`, `/index.html`, `/favicon.ico`, todo `/static/*` y
  todo `/auth/*`. El resto queda cerrado.
- Sin `VIGIA_TOKEN` solo se atiende a loopback (`127.0.0.1`, `::1`,
  `localhost`); desde otra IP → `403`. Si `host` no es loopback y no hay
  token, el proceso **no arranca** (mensaje claro en consola/log).
- `host` y `puerto` ya se leen de `config.yaml`; `VIGIA_HOST`/`VIGIA_PUERTO`
  siguen ganando. Por defecto `127.0.0.1:8000`.
- Cabeceras en todas las respuestas: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy` sin cámara/micro/geo.

### Zonas (bloque 4) — **lo consume la GUI**

`GET /zonas` → `{"zonas": {nombre: ZONA}, "ancho": int, "alto": int}`

`ZONA` siempre lleva estas claves:

```json
{
  "caja": [x1, y1, x2, y2],          // o "poligono": [[x,y], ...], nunca ambos
  "permanencia_seg": 20,            // null = umbral global
  "permanencia_conocidos_seg": null,// null = usa el global; 0 = no dispara
  "franjas": [{"desde": "22:00", "hasta": "08:00"}],
  "restringida": false,
  "excluir": false,
  "max_personas": 0,                // 0 = sin límite
  "vigilar": ["laptop"],
  "conocidos": true
}
```

- `PUT /zonas` acepta exactamente ese objeto (sin `nombre` dentro) **y** el
  formato antiguo `{"zonas": {"puerta": [x1,y1,x2,y2]}}`.
- Un `PUT` inválido → `400` con texto en `detail`.
- Se persiste en `config.yaml` también en el formato nuevo; `config.yaml`
  antiguo con listas sigue cargando.
- Reglas: `restringida` avisa cuando hay alguien y **no** está en sus
  `franjas`; si no tiene `franjas`, avisa siempre. `excluir` ignora esa área
  para todo. Los polígonos usan el **centro** de la caja de la persona.
- Eventos nuevos: `zona_restringida`, `demasiadas_personas`, `merodeo`,
  `persona_inmovil`.

### Telegram / armado (bloque 2)

- `GET /telegram/estado` →
  `{"configurado": bool, "activo": bool, "token_configurado": bool,
    "chat_configurado": bool, "ultimo_error": str|null,
    "ultimo_envio": "AAAA-MM-DD HH:MM:SS"|null, "pendientes": int}`.
  Nunca incluye el token ni el chat_id.
- `POST /telegram/prueba` → `{"ok": true}` o `{"ok": false, "error": "..."}`;
  `409` si Telegram no está configurado.
- `GET /armado` → `{"armado": bool, "auto": bool, "desde": str|null,
  "franjas": [{"desde","hasta"}]}`.
- `POST /armado` body `{"armado": bool}` y opcional `{"auto": bool}`.
  Un cambio manual desactiva el automático (salvo que mandes `auto` en el
  mismo POST). Devuelve el mismo objeto que `GET /armado`.
- Prioridades por defecto: `cara_desconocida` → crítico si armado / aviso si
  no; `permanencia` y `objeto_desaparece` → aviso; `persona_entra/salio` →
  info (no se envían). Configurable en `telegram_prioridades`.

## Peticiones de B a A

### B1 — GUI
1. **`GET /static/{fichero}`** ✅ **Hecho por A en `34435a5`** (montado
   `StaticFiles`); la GUI carga ya en `:8000`. Queda probarla con la cámara
   real conectada. Mientras no existía se usó `scripts/servir_gui_dev.py` en
   `:8020` para verificarla sin tocar `web.py`.
2. **`GET /logs?limite=...`** (opcional) para la vista *Sistema*: devolver las
   últimas N líneas de `logs/vigia.log` y el tamaño/rotaciones. La GUI ya
   muestra un aviso de "pendiente" si no existe.
3. **Multicámara**: cuando `eventos`/`timeline` incluyan `camara_id`, la GUI ya
   filtra por cámara y muestra el selector; no hace falta endpoint nuevo, solo
   el campo en los eventos.
4. Confirmado: el contrato de zonas, armado y Telegram de `NOTAS.md` encaja con
   la GUI (formatos objeto, 401 con `detalle`, etc.).

## Resumen final A

### Qué se hizo

- **A1 (seguridad)**: `vigia/seguridad.py` (`.env` sin dependencias, token,
  intentos por IP, loopback, cabeceras), middleware en `vigia/web.py`,
  endpoints `/auth/*`, `host`/`puerto` en `config.py`, validación estricta de
  `fichero` en `/clip` y `/media` (traversal, ocultos, `~`, separadores),
  `VIGIA_TOKEN` también con `Authorization`/cookie/query. 14 tests.
- **A2 (Telegram)**: `vigia/telegram.py` con cliente `urllib` (long polling,
  multipart para foto/vídeo), cola y dos hilos (envíos y bot), anti-spam con
  cooldown y resumen, prioridades, `vigia/armado.py` persistente en SQLite,
  comandos `/armar /desarmar /estado /foto`, armado automático por franjas,
  avisos de cámara/disco/reinicio y endpoints para la GUI. Tests con cliente
  falso (sin red).
- **A3 (zonas)**: `vigia/zonas.py` (modelo, polígonos, franjas, validación),
  motor de eventos reescrito para umbral por zona, re-aviso, conocidos,
  restringidas, aforo, merodeo, inmovilidad, exclusión y objetos por zona.
  `GET/PUT /zonas` en formato objeto retrocompatible.

### Qué quedó a medias / pendiente

- **Telegram no se ha probado contra la API real** (no hay token en `.env`);
  toda la lógica está testeada con un cliente falso. Falta una prueba manual
  con un bot real.
- **WebM en móviles**: queda el `TODO` documentado en `vigia/telegram.py`.
  No se monta ffmpeg ni conversión a MP4 H.264.
- **Polígonos en el vídeo**: se guardan y se evalúan, pero `dibujo.py` solo
  pinta cajas (no es fichero mío); la GUI puede pintarlos en el editor. Si se
  quieren ver en el stream, pedírselo a A o ampliar `dibujo.py` en otra ronda.
- Las reglas por zona se evalúan solo cuando la **franja global** está activa
  (comportamiento previo del motor). Si en el futuro se quieren zonas activas
  fuera de la franja global, hay que cambiar `procesar` para no salir antes.
- Una persona que cae en dos zonas se asigna a la **primera** del mapa (orden
  de `config.yaml`/PUT); no se generan eventos duplicados por múltiples zonas.
- `/zonas` no valida el rango de coordenadas contra la resolución (solo que
  sean 4 números o un polígono). La GUI ya limita al dibujar.

### Decisiones raras (a propósito)

- `detalle` (español) en el 401, tal y como pidió el contrato de B.
- Sin token, las peticiones remotas reciben **403** (no 401) porque no hay
  credencial que presentar; el 401 es solo cuando hay token configurado.
- `reaviso_min` por defecto **0** (comportamiento antiguo: un solo aviso).
- `inmovil_seg` por defecto **0**: activarlo de serie generaría falsos
  positivos con personas sentadas.
- Los conocidos **no** disparan permanencia por defecto
  (`permanencia_conocidos_seg: 0`); se puede dar un umbral distinto o dejar
  que la zona los ignore con `conocidos: false`.
- Un POST manual a `/armado` desactiva el modo automático para que la
  siguiente franja no revierta la decisión del usuario.
- El estado de armado se guarda en la tabla `estado_sistema` de `vigia.db`
  (no en `config.yaml`, que es de ajustes).
- `?token=` se acepta en `/snapshot.jpg` además de `/video.mjpg` porque el
  editor de zonas carga la imagen con `<img>` y no puede mandar cabeceras.
- La cookie no lleva `Secure` para no romper HTTP local; delante de HTTPS
  (Caddy) conviene añadirlo o dejar que el proxy lo haga.

## Resumen final B

### Commits

- **B1 GUI modular** — `12fe0ea`: `index.html` con 7 vistas (`data-vista`),
  router por hash, `panel.css` y 18 módulos ES en `vigia/static/js/`. Añade
  tema auto/oscuro/claro, responsive móvil, ARIA y atajos; login que no arranca
  router ni datos ni pide `/video.mjpg` antes de autenticar; editor visual de
  zonas (cajas, polígonos, exclusión y reglas) y de franjas (barra de 24 h);
  ajustes agrupados; multicámara preparada; polling pausable aislado en
  `datos.js` para un futuro SSE. Herramientas: `scripts/servir_gui_dev.py`
  (8020/8021) y `scripts/verificar_gui.mjs` (Chrome headless por CDP).
- **B2 despliegue** — `2fe9cd4`: `Dockerfile` CUDA con usuario no root y `ARG`
  para CPU, `docker-compose.yml` (GPU, solo loopback, proyecto montado),
  `.dockerignore`, `deploy/vigia.service` (systemd), NSSM para Windows,
  `deploy/Caddyfile` con TLS interno, `scripts/backup.py` (API de backup de
  SQLite, `quick_check`, `config.yaml`, rotación; la tabla `rostros` va dentro
  de la BD) y `docs/07-despliegue.md`.
- **B3 GPUs** — `3ca6d37`: `bench/inventario.py` (torch + `nvidia-smi`),
  `bench/benchmark.py` (YOLO11 n/s/m y pose, clip o frames sintéticos,
  `--imgsz`/lote/`--track`, FPS y p50/p95, estimación de cámaras a 10/15 FPS
  con 20 % de margen), `bench/vlm.py` (sonda Ollama/LM Studio), JSON en
  `bench/salida/` y conclusiones en `docs/08-gpus.md`.

### Qué se probó de verdad

- GUI en Chrome real (1440×900 y 390×844) con `verificar_gui.mjs`: sin fallos
  de consola ni desbordamiento. Login con token erróneo y correcto; guardado
  real de caja, polígono, exclusión, reglas y franjas contra `:8020`,
  restaurando `config.yaml` después.
- Backup contra la BD real (228 eventos, 1 rostro, `quick_check` ok) y 4 tests
  propios. YAML del compose y sintaxis del script de PowerShell validados.
- Benchmarks en la RTX 3060 (driver 580.97, torch 2.6.0+cu124): clip 640×480
  b1 sin tracking 46,4 FPS / p50 18,2 ms; con tracking 30,7 FPS / p50 30,8 ms
  (≈2 cámaras a 10 FPS o 1 a 15); lote 4, 137,8 FPS; 1280, yolo11n 36,0 FPS y
  yolo11m 12,0 FPS.
- Suite completa: **263 passed, 1 warning**.

### Pendiente / no probado (a propósito)

- **Docker, systemd, NSSM y Caddy no se han ejecutado**: no están instalados en
  este PC. Están escritos y validados de sintaxis, pero no hay prueba real de
  arranque; así se dice en `docs/07-despliegue.md`.
- **VLM sin medir**: ni Ollama (11434) ni LM Studio (1234) respondían. No hay
  conclusiones de VLM en `docs/08-gpus.md` más allá de "no medido".
- **Panel en `:8000` con cámara real**: la webcam no estaba disponible en el
  entorno de B; queda pendiente una pasada con el motor real.
- `/logs` sigue sin existir (petición 2 de arriba); la vista *Sistema* muestra
  el aviso de "pendiente".

### Decisiones raras (a propósito)

- Compose monta el proyecto entero en `/app` en vez de `config.yaml` suelto
  porque el guardado atómico sobre un bind-mount de fichero falla en Linux.
- El backup copia también `config.yaml` (no solo la BD), para que una
  restauración deje el sistema como estaba.
- La estimación de cámaras usa `fps * 0.8 / objetivo`, un 20 % de margen para
  dibujo, captura y picos; es una estimación, no una promesa.
- `--track` va separado del benchmark base porque el tracking es el modo real
  (y baja el FPS a la mitad); mezclarlos daría cifras engañosas.
- En la GUI, `Camaras` conserva el valor previo del select y muestra
  "Cargando cámaras…" porque `/camaras` tarda ~16 s en frío sin cámara.

## Ronda de correcciones (revisión tras pruebas en vivo, 28/10/2026)

- **Caras:** YuNet devuelve cajas `x,y,w,h`; se trataban como `x1,y1,x2,y2`. Recorte
  vacío (imagen rota en «Personas»), cara nunca asociada a persona, nadie se
  reconocía. Corregido en `caras.py` (+test).
- **Zonas (GUI, fichero de B):** `pintar()` hacía `clearRect` y borraba la captura
  de fondo (lienzo negro). Edit puntual en `static/js/zonas.js` (`_img`).
  **B: sin commitear aún; está en tu working tree.**
- **IDs estables:** `tracking.ReasociadorIds` + `confirmar_seg` en
  `RastreadorPersonas` (config `reasociar_seg`, `confirmar_entrada_seg`).
- **Actividad:** clasificador reescrito (sin rodillas ≠ de pie; móvil cerca de las
  muñecas; votación temporal en `EstabilizadorAct`).
- **FPS:** `captura.LectorAsincrono` (hilo de lectura, último frame) y
  `analisis.AnalisisAsincrono` (pose+caras fuera del bucle). Medido con el móvil
  (cámara 2): 11 → ~20 fps a 640x480; **1280x720 solo da 10 fps de cámara pura
  (driver), 960x540 da 30** → `config.yaml` queda en 960x540.
- Pendiente: el snapshot del editor de zonas lleva pintadas las detecciones.

## Contrato Ronda 3 — B

> Todo lo de esta seccion es **aditivo**: los contratos anteriores siguen
> valiendo. Los endpoints nuevos pasan por el mismo middleware (cookie/Bearer;
> sin token, solo localhost).

### `GET /estadisticas?dias=7`

Agregados en SQL (indices `tipo+marca_sistema` y `camara_id`). `dias` 1..365
(fuera de rango se recorta; no numerico -> 422).

```json
{
  "dias": 7,
  "desde": "2026-10-23 00:00:00",
  "hasta": "2026-10-29 18:20:00",
  "total": 184,
  "por_tipo": {"persona_entra": 40, "permanencia": 12, "actividad": 90},
  "por_hora": [0, 0, 2, 0, 1, 5, "…24 enteros, indice = hora local…"],
  "por_dia": [{"dia": "2026-10-28", "n": 90}, {"dia": "2026-10-29", "n": 94}],
  "actividades": {"usando movil": 1830.0, "de pie": 640.0},
  "personas_distintas": 6,
  "ocupacion_media": 0.03,
  "ocupacion_max": 3,
  "picos": {
    "hora": {"hora": 18, "n": 22},
    "dia": {"dia": "2026-10-29", "n": 94},
    "personas": {"n": 3, "t_seg": 43200}
  }
}
```

- `actividades`: segundos acumulados a partir de los eventos `actividad`
  (tramo entre eventos consecutivos de la misma persona, con tope de 10 min
  para no contar huecos nocturnos). Es una aproximacion, no un cronometro.
- `ocupacion_media` = persona-segundos dentro / segundos de la ventana;
  `ocupacion_max` = maximo simultaneo emparejando `persona_entra`/`persona_salio`.

### `GET /logs?limite=200&nivel=INFO`

Ultimas lineas del log rotativo, **mas recientes primero**. Parseo tolerante:
las lineas que no siguen el formato salen con `nivel: "?"`.

```json
{
  "lineas": [
    {"t": "2026-10-29 18:20:01,123", "nivel": "ERROR",
     "modulo": "vigia.eventos", "msg": "fallo ..."},
    {"t": "", "nivel": "?", "modulo": "", "msg": "traza sin formato"}
  ],
  "tamano_bytes": 812345,
  "rotaciones": 2
}
```

### `GET /eventos/stream` (SSE, `text/event-stream`)

- `event: evento` con `data:` = un objeto como los de `/eventos` (con campos
  extra): `{id, marca, hora, tipo, etiqueta, detalle, id_tracking, zona,
  camara_id, miniatura, clip}`.
- `event: ping` con `data: {}` cada 15 s si no hay novedades.
- Autenticacion normal por cookie/Bearer (EventSource del navegador envia la
  cookie). `?limite=N` cierra tras N eventos; sin limite es infinito.
- Es un bus en memoria del proceso: no reenvia historia. Al arrancar, pedir
  `/eventos` y luego abrir el stream (o usar `?limite`). Al desconectar el
  cliente se desuscribe (sin hilos colgados).

### `GET /eventos` paginado y filtrable

Sin parametros devuelve **la lista de siempre** (retrocompatible). En cuanto se
manda cualquier parametro devuelve objeto:

```json
{
  "eventos": [{"id": 184, "marca_sistema": "2026-10-29 18:19:00",
               "marca_pantalla": "18:19:00", "tipo": "persona_entra",
               "id_tracking": 3, "detalle": "id 3", "miniatura": "…jpg",
               "clip": null, "camara_id": "0", "zona": null}],
  "total": 184,
  "limite": 20,
  "antes_id": null,
  "siguiente_antes_id": 165
}
```

- Parametros combinables: `limite` (1..500), `antes_id`, `tipo`,
  `camara_id`, `desde`, `hasta` (`AAAA-MM-DD` o `AAAA-MM-DD HH:MM:SS`),
  `persona` (= `id_tracking`), `zona`.
- `total` cuenta todas las filas que cumplen filtros, antes de paginar.
  Para la pagina siguiente: `?antes_id=<siguiente_antes_id>`.

### `GET /resumen`

Todo el Dashboard en una llamada: estado de camara, armado, FPS, eventos de
hoy por tipo, ultimo evento, disco, uptime, Telegram, aforo.

```json
{
  "version": "0.8",
  "uptime_seg": 3600,
  "camara": {"ok": true, "fps": 18.4, "personas": 1, "ids": [3],
             "ancho": 960, "alto": 540},
  "camaras": {"actual": "0",
              "camaras": [{"id": "0", "nombre": "Camara",
                           "estado": "conectada", "fps": 18.4,
                           "personas": 1}]},
  "armado": {"armado": false, "auto": false, "desde": null, "franjas": []},
  "eventos_hoy": {"dia": "2026-10-29", "total": 94,
                  "por_tipo": {"persona_entra": 8, "permanencia": 3}},
  "ultimo_evento": {"id": 184, "tipo": "persona_entra", "…": "…"},
  "total_eventos": 1200,
  "aforo_actual": 1,
  "disco": {"libre_gb": 120.4, "total_gb": 930.2},
  "db_bytes": 812345,
  "telegram": {"configurado": false, "activo": false, "ultimo_error": null,
               "pendientes": 0}
}
```

### Errores unificados

Toda respuesta de error lleva **el mismo texto** en `detail` y `detalle`
(HTTPException, validacion 422 y respuestas del middleware de seguridad).
El 401 sigue siendo `{"detalle": "no autenticado", "detail": "no autenticado"}`
con codigo 401.

### Cambios internos relevantes

- BD: migracion automatica a `user_version=3` con columnas `camara_id
  TEXT DEFAULT '0'` y `zona TEXT` (filas antiguas quedan en camara `"0"`).
- `_anotar_evento`, `GuardadorClips.guardar*` aceptan `camara_id`; la camara
  `"0"` conserva los nombres de fichero de siempre (`_c<id>` para las demas).
- El feed SSE se publica desde `_anotar_evento` (unico punto de salida).

## Ronda 3 — B

### P1 — datos para el panel (hecho)

- `GET /estadisticas`, `GET /logs`, `GET /eventos/stream`, `/eventos`
  paginado/filtrable y `GET /resumen`.
- Tests en `tests/test_panel.py` (agregados, parseo de logs, SSE con cliente
  que lee el evento, paginacion, filtros, resumen y errores con `detail` +
  `detalle`). El 401 se ajusto para aceptar el campo extra (contrato aditivo).
