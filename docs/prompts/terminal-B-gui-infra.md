# Terminal B — GUI, despliegue e investigación de GPUs

Trabajas en **Vigia** (`D:\Lukaton1\03 - PROYECTOS TEC\vigia`), sistema local de videovigilancia con YOLO (FastAPI + OpenCV + SQLite, Python 3.12, `.venv` en la carpeta). Lee primero `docs/04-estado.md`, `docs/06-plan-v2.md` (bloques 3, 6 y 10) y `README.md`. Hay otra terminal (A) trabajando a la vez en el backend (seguridad, Telegram, reglas por zona): **respeta el reparto de ficheros**.

## Reparto de ficheros (obligatorio)
- **Tuyos:** `vigia/static/**` (toda la GUI), `docs/07-*`, `docs/08-*`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy/`, `scripts/`, `bench/`, y tests nuevos que sean solo de lo tuyo.
- **NO toques:** `vigia/web.py`, `config.py`, `eventos.py`, `analisis.py`, `almacen.py`, `main.py`, `notificaciones.py`, `tracking.py` (son de A). Si la GUI necesita un endpoint nuevo, pídelo en `docs/prompts/NOTAS.md` (sección "Peticiones de B a A") y haz la GUI tolerante a que aún no exista (degrada sin errores en consola).
- Git: commits pequeños con `git add <rutas concretas>` (nunca `git add -A` ni `git commit -a`). Mensajes en español, estilo del historial. Sin rebase/reset/force. Ignora los cambios de A en `git status`.
- Para probar la GUI arranca el servidor en el **puerto 8020** (`VIGIA_PUERTO=8020`); A usa 8010 y :8000 es el servidor "de verdad" (no lo mates).
- La suite (`.venv\Scripts\python -m pytest -q`) debe seguir verde. `tests/test_web.py` comprueba cosas del HTML: si lo rompes al reorganizar, adáptalo con criterio (no lo borres).

## Contrato con el backend (lo implementa A; construye contra esto)
- `GET /auth/estado` → `{"auth_requerida": bool, "autenticado": bool}`; `POST /auth/login` `{"token"}` pone cookie; `POST /auth/logout`. Cualquier endpoint puede devolver **401** `{"detalle":"no autenticado"}` → la GUI muestra pantalla de login y reintenta. Mientras A no lo entregue, trata un 404 en `/auth/estado` como "sin auth".
- Telegram: `GET /telegram/estado`, `POST /telegram/prueba`, `GET/POST /armado`. Zonas: A cambiará `GET/PUT /zonas` a objetos `{caja|poligono, permanencia_seg, franjas, restringida, excluir, max_personas}` (formato exacto en `NOTAS.md`). Hasta entonces soporta el formato antiguo `nombre: [x1,y1,x2,y2]`.

## Tareas, por orden

### B1. Reorganización de la GUI (bloque 3)
Hoy `static/index.html` son ~1.200 líneas con CSS y JS mezclados.
1. Separar en `static/panel.css` y `static/js/*.js` como módulos ES (sin paso de build; nada de CDN en runtime; si usas Alpine/Preact, vendorizado en local y solo si de verdad simplifica).
2. Navegación por vistas con router por hash (`#/eventos`): **En vivo** (vídeo + línea del tiempo), **Eventos** (historial, filtros, detalle con clip/miniatura), **Clips**, **Personas** (caras + actividad), **Zonas y reglas**, **Sistema** (salud, logs, armado/Telegram), **Ajustes**.
3. Ajustes agrupados por tema con descripción y validación; **editor visual de franjas** (sustituye al JSON a mano).
4. Multicámara preparada: selector/rejilla con una cámara principal; la línea del tiempo filtra por `camara_id` si el dato existe (aún no: déjalo preparado y oculto).
5. Pantalla de login según el contrato, y aviso si se accede por HTTP sin token fuera de localhost.
6. Móvil primero: responsive real, tema claro/oscuro (`prefers-color-scheme` + conmutador), accesibilidad (foco, ARIA, contraste) y atajos de teclado.
7. Estado vivo: de momento polling razonable (en pausa si la pestaña está oculta) con la capa de datos aislada en un módulo para cambiar luego a SSE. Si ves necesario SSE, pídelo a A en `NOTAS.md`.
8. Conserva lo que ya funciona: línea del tiempo en directo, detalle con clip, editor de zonas (amplíalo a polígonos y exclusión cuando A entregue el formato), registro de caras, selector de webcam.
9. Verifica en navegador real (herramientas de Chrome si las tienes) a ancho móvil y escritorio, sin errores en consola.

### B2. Servidor local (bloque 6)
1. `Dockerfile` (base CUDA runtime, usuario no root) y `docker-compose.yml` con volúmenes para `vigia.db`, `clips/`, `modelos/`, `config.yaml`, `.env`; variante CPU documentada; `.dockerignore`.
2. Alternativa sin Docker: `deploy/vigia.service` (systemd) y `deploy/instalar-servicio-windows.ps1` (NSSM) con reinicio automático.
3. `deploy/Caddyfile` de ejemplo (HTTPS + proxy inverso) y nota sobre acceso remoto por VPN (WireGuard/Tailscale) en lugar de abrir puertos.
4. `scripts/backup.py` (BD con la API de backup de sqlite3, incluye la tabla `rostros`, con rotación) y su test.
5. `docs/07-despliegue.md`: pasos, decisión Proxmox-vs-PC con GPU en tabla de pros/contras y checklist de seguridad. Si Docker no está instalado no puedes probarlo: dilo en el doc, no afirmes que lo verificaste.

### B3. Investigación de GPUs (bloque 10, parte medible)
1. `bench/inventario.py`: detecta GPUs (torch + `nvidia-smi`), VRAM, driver y CUDA; guarda JSON.
2. `bench/benchmark.py`: FPS y latencia p50/p95 de yolo11n/s/m y pose en PyTorch (ONNX Runtime/TensorRT solo si ya hay soporte; si no, "pendiente", sin instalar cosas pesadas). Sobre vídeo sintético o un clip de `clips/`, con resolución y lote parametrizables. Estima cuántas cámaras a 10–15 FPS caben por GPU.
3. Viabilidad de VLM local (Ollama / LM Studio si responden en sus puertos por defecto): describir una miniatura de evento en una frase; mide latencia y VRAM. Si no están accesibles, documenta el método y sigue.
4. Entregable `docs/08-gpus.md` con tablas **solo con datos medidos** y recomendación de hardware por nº de cámaras. Lo no medido va como "pendiente".

## Al terminar cada bloque
Añade una fila al final del registro de `docs/04-estado.md` (pequeña, para no chocar con A), haz commit y, al acabar B1–B3, escribe en `docs/prompts/NOTAS.md` (sección "Resumen final B") qué se hizo, qué quedó a medias y decisiones raras, para la revisión final. **No te pares a preguntar**: decide con criterio, anota la decisión y sigue.
