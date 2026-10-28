# Terminal A — Backend: seguridad, Telegram y reglas por zona

Trabajas en **Vigia** (`D:\Lukaton1\03 - PROYECTOS TEC\vigia`), sistema local de videovigilancia con YOLO (FastAPI + OpenCV + SQLite, Python 3.12, `.venv` en la carpeta). Lee primero `docs/04-estado.md`, `docs/06-plan-v2.md` (bloques 1, 2 y 4) y `README.md`. Hay otra terminal (B) trabajando a la vez en la GUI y en despliegue/investigación: **respeta el reparto de ficheros**.

## Reparto de ficheros (obligatorio)
- **Tuyos:** `vigia/web.py`, `vigia/config.py`, `vigia/eventos.py`, `vigia/analisis.py`, `vigia/almacen.py`, `vigia/main.py`, `vigia/notificaciones.py`, `vigia/tracking.py`, ficheros nuevos que crees (`vigia/seguridad.py`, `vigia/telegram.py`, `vigia/reglas.py`...), `config.example.yaml` y sus tests en `tests/`.
- **NO toques:** `vigia/static/*` (GUI), `docs/07-*`, `docs/08-*`, `Dockerfile*`, `docker-compose.yml`, `deploy/`, `scripts/`, `bench/` (son de B).
- Git: commits pequeños con `git add <rutas concretas>` (nunca `git add -A` ni `git commit -a`). Mensajes en español, estilo del historial. Sin rebase/reset/force. Si `git status` muestra cambios de B, ignóralos.
- Para probar el servidor arráncalo en el **puerto 8010** (`VIGIA_PUERTO=8010`); B usa 8020 y :8000 es el servidor "de verdad" (no lo mates).
- Suite completa (`.venv\Scripts\python -m pytest -q`) verde antes de cada commit. Cada función nueva con tests. Los tests no usan la `vigia.db` real ni la red real (mockea Telegram).

## Tareas, por orden

### A1. Seguridad (bloque 1)
1. `host` y `puerto` en `config.yaml` (las variables de entorno siguen ganando).
2. Token `VIGIA_TOKEN` (entorno / `.env`, cargado sin dependencia nueva si es posible). Middleware que exige token a TODA la API y al vídeo salvo `/auth/*` y el HTML/estáticos necesarios para mostrar el login. Aceptar cabecera `Authorization: Bearer`, cookie `vigia_token` (HttpOnly, SameSite=Lax) y `?token=` solo en `/video.mjpg` y `/snapshot.jpg`. Comparar con `secrets.compare_digest`.
3. **Contrato con la GUI (B depende de esto; si lo cambias, anótalo en `docs/prompts/NOTAS.md`):**
   - `POST /auth/login` body `{"token": "..."}` → 200 y pone la cookie; 401 si es incorrecto (con limitación de intentos por IP).
   - `POST /auth/logout` → borra la cookie.
   - `GET /auth/estado` → `{"auth_requerida": bool, "autenticado": bool}` (siempre accesible).
   - Cualquier otro endpoint sin credencial → **401** JSON `{"detalle": "no autenticado"}`.
4. Sin `VIGIA_TOKEN`: solo se permite `127.0.0.1`; si `host` no es loopback y no hay token, **negarse a arrancar** con mensaje claro.
5. `POST /config`, `/caras/*`, `PUT /zonas` y borrados: solo autenticados (lo cubre el middleware; añade un test por endpoint).
6. Cabeceras de seguridad básicas y validación de `fichero` en `/clip` y `/media` (test explícito de path traversal).

### A2. Alertas por Telegram (bloque 2)
1. `vigia/telegram.py`: cliente por **long polling** con `urllib`/`httpx` (si añades dependencia, a `requirements.txt`). `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`, nunca en `config.yaml`. Solo atiende al `chat_id` autorizado.
2. Alerta: texto + miniatura; cuando el clip WebM esté listo, se envía después (cola/hilo aparte, jamás bloquear el bucle de detección). Si el cliente móvil no reproduce WebM, deja un TODO documentado; no montes ffmpeg.
3. Prioridades: `critico` (desconocido en franja armada), `aviso` (permanencia, objeto desaparece), `info` (entra/sale: no notificar). Mapa configurable en `config.yaml`.
4. Anti-spam: máximo por hora, cooldown por (tipo, persona/zona) y resumen "+N eventos más" al superarlo.
5. Armado/desarmado: estado persistente, `GET/POST /armado`, comandos del bot `/armar /desarmar /estado /foto` y armado automático por franjas.
6. Avisos del sistema: cámara caída > 5 min, disco < 5 GB, servicio reiniciado.
7. Para la GUI: `GET /telegram/estado` (configurado / último error, sin exponer secretos) y `POST /telegram/prueba`.

### A3. Permanencia y reglas por zona (bloque 4)
1. Cada zona pasa a ser un objeto `{caja|poligono, permanencia_seg, franjas, restringida, excluir, max_personas}`. **Retrocompatible**: el formato antiguo `nombre: [x1,y1,x2,y2]` sigue cargando y `GET/PUT /zonas` usa un formato que documentes en `NOTAS.md` (B lo consume).
2. Permanencia por zona con histéresis: tolerar pérdidas cortas de ID de ByteTrack sin reiniciar el contador; re-aviso periódico con tiempo acumulado (`reaviso_min`).
3. Los conocidos no disparan permanencia (o umbral distinto); los desconocidos sí.
4. Reglas nuevas con eventos propios: zona restringida fuera de horario, más de N personas, merodeo (≥ K re-entradas en una ventana), inmóvil > X (`persona_inmovil`, posible caída).
5. Polígonos (punto en polígono) y zonas de exclusión.
6. Objetos vigilados por zona, no solo por clase global.
7. Todo con tests deterministas (reloj inyectable, sin cámara).

## Al terminar cada bloque
Añade una fila al registro de `docs/04-estado.md`, deja los cambios de contrato en `docs/prompts/NOTAS.md` y haz commit. Al acabar A1–A3 escribe en `NOTAS.md` (sección "Resumen final A") qué se hizo, qué quedó a medias y decisiones raras, para la revisión final. **No te pares a preguntar**: decide con criterio, anota la decisión y sigue.
